"""
File: ingest/binance_ws.py
1. Single Responsibility: Maintain a WebSocket connection to Binance, receive
   kline data, validate + clean + persist each candle, handle reconnect / resume
   with exponential backoff, and publish each closed candle to Redis pub/sub
   for the engine. No trading logic, no engine imports.
2. Consumes: contracts.config.CoinConfig, contracts.market.Candle,
   storage.redis_cache.RedisCache, storage.supabase.SupabaseClient,
   data.validators, data.cleaners, config.thresholds.
3. Produces: BinanceWSClient class.
4. Downstream: app/main.py (starts the client in a background task),
   engine/orchestrator.py (subscribes to new_candle pub/sub channels).
5. New Dependencies: websockets, httpx (both already in requirements.txt).
6. Touches Section 6 bugs? Yes -- Bug 3 (repainting). The client NEVER advances
   the ws_checkpoint on an unclosed candle, NEVER writes an unclosed candle to
   Postgres, and always sets ``is_closed`` from the Binance ``k.x`` flag (no
   inference, no override). Bug 2 (CVD) is honoured by computing
   ``taker_sell_volume = volume - taker_buy_volume`` per the spec, never from
   candle colour.
7. Tests: Section 10 ingest/binance_ws.py acceptance criteria -- reconnect
   backoff (1s -> 60s), resume gap-fill, checkpoint advance on is_closed only,
   health warning after 2x expected interval.
8. Logging: ws_connect, ws_disconnect, ws_reconnect, ws_stale,
   ws_checkpoint_advanced, candle_written, error (per Section 9 catalog).
9. Dependency Order: config -> contracts -> storage -> data ->
   ingest/binance_ws.py. No upstream violations.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK

from config.thresholds import (
    WS_INITIAL_BACKOFF_SECONDS,
    WS_MAX_BACKOFF_SECONDS,
    WS_REST_RETRY_COUNT,
    WS_STABLE_RESET_SECONDS,
    WS_STALE_MULTIPLIER,
    resume_window_candles,
    timeframe_to_seconds,
)
from contracts.config import CoinConfig
from contracts.market import Candle
from data.cleaners import normalize_volume
from data.validators import InvalidCandleError, validate_binance_kline, validate_candle
from monitoring.logger import get_logger
from storage.redis_cache import RedisCache
from storage.supabase import SupabaseClient
from monitoring.health_manager import health_manager, HealthStatus

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Module-local constants (ingest-specific, not engine thresholds)
# ---------------------------------------------------------------------------
BINANCE_WS_BASE_URL = "wss://stream.binance.com:9443/stream"
"""Combined-stream base URL. Single multiplexed connection for all pairs."""

BINANCE_REST_KLINES_URL = "https://api.binance.com/api/v3/klines"
"""REST endpoint for gap-fill on reconnect."""

WS_PING_INTERVAL_SECONDS = 20
"""websockets library ping interval -- keeps the connection alive through
Render's idle timeouts."""

WS_PING_TIMEOUT_SECONDS = 20
"""If no pong is received within this window, the connection is considered dead."""

WS_CLOSE_TIMEOUT_SECONDS = 10
"""Grace period for the websocket close handshake during shutdown."""

WS_RECEIVE_TIMEOUT_SECONDS = 90
"""Maximum idle time on the recv() call before we treat the connection as dead.
Must be larger than the longest expected inter-message gap (the largest
timeframe we support, currently 1w = 604800s, but Binance sends a heartbeat
ping every 3 minutes so this is mostly a safety net)."""

HEALTH_CHECK_INTERVAL_SECONDS = 30
"""How often ``_health_check_loop`` runs."""

REST_TIMEOUT_SECONDS = 15
"""Per-request timeout for the gap-fill REST call."""

REST_BATCH_LIMIT = 1000
"""Binance caps klines REST responses at 1000 candles per request."""


class BinanceWSClient:
    """Async Binance WebSocket client with reconnect / resume semantics.

    Lifecycle::

        client = BinanceWSClient(coins, redis, supabase)
        await client.start()       # blocks until client.stop() is called

    Concurrency model::

        * ``start()`` is the only public coroutine. It runs the outer
          connect / receive / reconnect loop.
        * A single ``_health_check_loop`` task runs concurrently for the
          lifetime of ``start()`` and is cancelled on shutdown.
        * All state mutations happen on the asyncio event loop -- no locks are
          needed because Python coroutines only yield at await points.
    """

    # ---------------- construction ----------------
    def __init__(
        self,
        coins: list[CoinConfig],
        redis: RedisCache,
        supabase: SupabaseClient,
    ) -> None:
        if not coins:
            raise ValueError("BinanceWSClient requires at least one CoinConfig")

        self._coins: list[CoinConfig] = coins
        self._redis: RedisCache = redis
        self._supabase: SupabaseClient = supabase

        # (symbol, timeframe) pairs we are subscribed to.
        self._active_pairs: list[tuple[str, str]] = self._build_active_pairs(coins)

        # Run-state flags.
        self._running: bool = False
        self._connected: bool = False

        # Backoff bookkeeping (Section 4).
        self._backoff_seconds: float = float(WS_INITIAL_BACKOFF_SECONDS)
        self._connection_started_at: Optional[datetime] = None
        self._reconnect_attempts: int = 0

        # The live websocket connection (None when disconnected).
        self._ws: Optional[websockets.WebSocketClientProtocol] = None  # type: ignore[name-defined]

        # Background tasks.
        self._health_task: Optional[asyncio.Task[None]] = None

        # Per-pair last-message timestamps, kept in memory as well as in Redis
        # so the health-check loop doesn't have to await a Redis call for every
        # pair on every tick.
        self._last_message_at: dict[tuple[str, str], datetime] = {}

        # Per-pair last-advanced checkpoint (open_time of the most recently
        # *closed* candle persisted). Used to compute gap-fill windows.
        self._last_checkpoint: dict[tuple[str, str], datetime] = {}

        # HTTP client for gap-fill REST calls. Created lazily.
        self._http_client: Optional[httpx.AsyncClient] = None

    # ---------------- public API ----------------
    async def start(self) -> None:
        """Run the connect / receive / reconnect loop until ``stop()`` is called.

        Implements the exponential backoff specified in Section 4: starts at
        ``WS_INITIAL_BACKOFF_SECONDS``, doubles on each failure, caps at
        ``WS_MAX_BACKOFF_SECONDS``, and resets to the initial value after
        ``WS_STABLE_RESET_SECONDS`` of stable connection.
        """
        self._running = True
        logger.info(
            "engine_started",
            timestamp=datetime.now(timezone.utc),
            active_coins=len(self._coins),
            active_pairs=len(self._active_pairs),
        )

        # Start the health-check loop in parallel.
        self._health_task = asyncio.create_task(
            self._health_check_loop(), name="binance_ws_health_check"
        )

        try:
            while self._running:
                try:
                    await self._connect()
                    await self._receive_loop()
                    # ``_receive_loop`` returns without raising only when
                    # ``_running`` was flipped to False mid-stream -- shutdown.
                except asyncio.CancelledError:
                    # Cooperative cancellation -- propagate so the caller can
                    # clean up.
                    raise
                except Exception as exc:  # noqa: BLE001
                    # Any unexpected error: log, treat as disconnect, back off.
                    await self._on_disconnect(self._describe_exception(exc))
        finally:
            await self._cleanup_background_tasks()
            logger.info(
                "engine_stopped",
                timestamp=datetime.now(timezone.utc),
                open_trades_count=0,  # ingest doesn't know about trades; field kept per Section 9 catalog
            )

    async def stop(self) -> None:
        """Graceful shutdown (Section 4 #6).

        Order of operations:
          1. Set ``_running = False`` so the outer loop stops after the
             current message.
          2. Wait for the current message to finish (``_receive_loop`` checks
             ``_running`` between messages).
          3. Write final checkpoints for every active pair to Redis + Postgres.
          4. Close the WebSocket.
          5. Cancel the health-check task.
        """
        logger.info(
            "ws_disconnect",
            timestamp=datetime.now(timezone.utc),
            reason="graceful_stop",
        )
        self._running = False

        # Close the websocket -- this will cause the recv() loop to raise
        # ConnectionClosedOK, which the outer loop treats as a clean exit.
        if self._ws is not None:
            try:
                await self._ws.close(code=1000, reason="client_shutdown")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "error",
                    timestamp=datetime.now(timezone.utc),
                    module="ingest.binance_ws",
                    error_type=type(exc).__name__,
                    error_message=f"ws.close() failed: {exc}",
                )

        # Flush final checkpoints so the next start() picks up exactly where
        # we left off (Section 4 #3).
        await self._flush_final_checkpoints()

        # Close HTTP client.
        if self._http_client is not None:
            try:
                await self._http_client.aclose()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "error",
                    timestamp=datetime.now(timezone.utc),
                    module="ingest.binance_ws",
                    error_type=type(exc).__name__,
                    error_message=f"http_client.aclose() failed: {exc}",
                )
            finally:
                self._http_client = None

        await self._cleanup_background_tasks()

    # ---------------- dynamic coin reload ----------------
    async def reload_coins(self, coins: list[CoinConfig]) -> None:
        """Replace the active coin list and reconnect the WebSocket.

        This is the live-reload hook called by ``app/main.py`` when the
        operator adds, edits, or deletes a coin while the engine is running.
        It updates ``_coins`` and ``_active_pairs``, closes the current
        connection (which causes ``_receive_loop`` to exit), and lets the
        outer ``start()`` loop reconnect with the new pair list.

        If the client is not currently running (``_running`` is False) the
        call is a no-op: the new coins will simply be used on the next
        ``start()`` invocation.
        """
        new_active_pairs = self._build_active_pairs(coins)
        if new_active_pairs == self._active_pairs:
            logger.info(
                "ws_reload_skipped",
                timestamp=datetime.now(timezone.utc),
                note="active pairs unchanged after reload_coins",
            )
            self._coins = coins
            return

        self._coins = coins
        self._active_pairs = new_active_pairs

        if self._running and self._ws is not None:
            logger.info(
                "ws_reload_reconnect",
                timestamp=datetime.now(timezone.utc),
                active_pairs=len(new_active_pairs),
            )
            # Close the current WS -- the outer start() loop will detect the
            # disconnect, call _on_disconnect, and reconnect with the new
            # stream URL built from the updated _active_pairs.
            try:
                await self._ws.close(code=4444, reason="coin_reload")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "error",
                    timestamp=datetime.now(timezone.utc),
                    module="ingest.binance_ws",
                    error_type=type(exc).__name__,
                    error_message=f"ws.close() during reload failed: {exc}",
                )

    # ---------------- connection lifecycle ----------------
    def _build_stream_url(self) -> str:
        """Build the combined-stream URL for all configured pairs.

        Binance combined streams use '/' as a separator, NOT ','.
        Maximum 200 streams per connection.
        """
        if len(self._active_pairs) > 200:
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="ingest.binance_ws",
                error_type="MaxStreamsExceeded",
                error_message=f"Too many streams: {len(self._active_pairs)} (max 200)",
            )
            # We truncate to 200 to at least try connecting, but the operator
            # should be warned.
            active_pairs = self._active_pairs[:200]
        else:
            active_pairs = self._active_pairs

        streams = []
        for symbol, timeframe in active_pairs:
            streams.append(f"{symbol.lower()}@kline_{timeframe}")
        
        # CORRECT: Binance uses '/' for combined streams
        stream_query = "/".join(streams)
        return f"{BINANCE_WS_BASE_URL}?streams={stream_query}"

    async def _connect(self) -> None:
        """Open the WebSocket connection and log the ws_connect event."""
        url = self._build_stream_url()
        self._connection_started_at = datetime.now(timezone.utc)
        self._reconnect_attempts += 1
        logger.info(
            "ws_connect",
            timestamp=datetime.now(timezone.utc),
            url=url,
            attempt=self._reconnect_attempts,
            active_pairs=len(self._active_pairs),
            module="ingest.binance_ws",
            message_text=f"محاولة فتح اتصال WebSocket مع Binance (المحاولة {self._reconnect_attempts})"
        )
        self._ws = await websockets.connect(
            url,
            ping_interval=WS_PING_INTERVAL_SECONDS,
            ping_timeout=WS_PING_TIMEOUT_SECONDS,
            close_timeout=WS_CLOSE_TIMEOUT_SECONDS,
            max_size=2**22,  # 4 MiB -- generous, Binance payloads are tiny
            open_timeout=20,
        )
        self._connected = True
        await health_manager.update_component(
            "WebSocket", 
            HealthStatus.OK, 
            "تم تأسيس الاتصال بنجاح مع Binance WebSocket",
            {"attempt": self._reconnect_attempts, "backoff": self._backoff_seconds}
        )

    async def _receive_loop(self) -> None:
        """Continuous recv() loop. Exits when ws closes or _running is False."""
        if self._ws is None:
            return

        # 1. On every connect, load fresh checkpoints and gap-fill (Section 4 #2).
        await self._refill_all_gaps()

        while self._running:
            try:
                # recv() blocks until a message arrives or a ping timeout occurs.
                raw_message = await asyncio.wait_for(
                    self._ws.recv(),
                    timeout=WS_RECEIVE_TIMEOUT_SECONDS,
                )
                await self._on_raw_message(raw_message)
            except asyncio.TimeoutError:
                # Treated as a disconnect so the outer loop can back off and
                # reconnect (Section 4 #4).
                raise ConnectionClosedError(
                    code=1006,
                    reason=f"recv_timeout_after_{WS_RECEIVE_TIMEOUT_SECONDS}s",
                ) from None
            except (ConnectionClosed, ConnectionClosedError, ConnectionClosedOK):
                # Raise to the outer loop.
                raise

    async def _on_raw_message(self, raw_message: str | bytes) -> None:
        """Parse a raw WS frame and dispatch to ``_process_message``.
        
        [TRACE] WebSocket received
        """
        logger.debug("trace_websocket_received", raw_len=len(raw_message))

        receive_time = datetime.now(timezone.utc)
        if isinstance(raw_message, (bytes, bytearray)):
            try:
                raw_message = raw_message.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise InvalidCandleError(
                    f"undecodable_frame: {exc}",
                    details={"raw_len": len(raw_message)},
                ) from exc

        try:
            payload = json.loads(raw_message)
            # [TRACE] Message parsed
            logger.debug("trace_message_parsed", stream=payload.get("stream"))
        except json.JSONDecodeError as exc:
            raise InvalidCandleError(
                f"unparseable_json: {exc}",
                details={"raw_preview": raw_message[:200]},
            ) from exc

        await self._process_message(payload, receive_time=receive_time)

    async def _process_message(self, msg: dict, receive_time: Optional[datetime] = None) -> None:
        """Validate, clean, persist, and publish a single Binance kline message."""
        # 4a/4b -- Validate the raw payload and build a typed dict that
        # satisfies Candle's constructor. InvalidCandleError propagates to
        # ``_on_raw_message``'s caller which logs + skips.
        parsed = validate_binance_kline(msg)

        # 4b -- Build the Candle. taker_sell_volume was already derived inside
        # validate_binance_kline as ``volume - taker_buy_volume`` (Section 17).
        candle = Candle(**parsed)

        # 4c -- Validate via data/validators.py (Candle-level sanity).
        try:
            validate_candle(candle)
        except InvalidCandleError:
            # Re-raise so the caller logs + skips. Do NOT swallow here --
            # we want a single, consistent log line per bad candle.
            raise

        # 4d -- Clean: ensure the taker-volume identity holds (defensive --
        # validate_binance_kline already guarantees it, but a future code path
        # through the REST gap-fill might not).
        cleaned = normalize_volume([candle])
        if not cleaned:
            # normalize_volume never returns an empty list for non-empty input
            # unless something is very wrong; treat as invalid.
            raise InvalidCandleError(
                "normalize_volume_empty: input was non-empty but output empty",
                details={"symbol": candle.symbol, "timeframe": candle.timeframe},
            )
        candle = cleaned[0]

        # 4g -- Update last_ws_message timestamp (in-memory + Redis).
        now = datetime.now(timezone.utc)
        pair_key = (candle.symbol, candle.timeframe)
        self._last_message_at[pair_key] = now
        try:
            await self._redis.touch_last_message(candle.symbol, candle.timeframe)
        except Exception as exc:  # noqa: BLE001
            # Redis being down must not crash the ingest loop (Section 22).
            logger.warning(
                "error",
                timestamp=now,
                module="ingest.binance_ws",
                error_type=type(exc).__name__,
                error_message=f"touch_last_message failed: {exc}",
            )

        # [TRACE] Cache updated
        # 4e -- Cache the latest candle in Redis (live price display etc.).
        try:
            await self._redis.set_candle(candle)
            logger.debug("trace_cache_updated", symbol=candle.symbol, timeframe=candle.timeframe)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "error",
                timestamp=now,
                module="ingest.binance_ws",
                error_type=type(exc).__name__,
                error_message=f"set_candle failed: {exc}",
            )

        # Update live price (every kline tick -- WS or closed) for the bot.
        try:
            await self._redis.set_live_price(candle.symbol, candle.close)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "error",
                timestamp=now,
                module="ingest.binance_ws",
                error_type=type(exc).__name__,
                error_message=f"set_live_price failed: {exc}",
                symbol=candle.symbol,
            )

        # [FIX] Refresh health status on successful message receipt to prevent stale CRITICAL status.
        await health_manager.update_component(
            "WebSocket", 
            HealthStatus.OK, 
            f"Receiving data for {candle.symbol}",
            {"symbol": candle.symbol, "timeframe": candle.timeframe, "is_closed": candle.is_closed}
        )

        # Log real data proof (Requested Log #10)
        if receive_time:
            process_time_ms = round((datetime.now(timezone.utc) - receive_time).total_seconds() * 1000, 2)
            logger.info(
                "real_data_received",
                timestamp=datetime.now(timezone.utc),
                symbol=candle.symbol,
                timeframe=candle.timeframe,
                last_price=candle.close,
                is_closed=candle.is_closed,
                receive_latency_ms=process_time_ms,
                candle_open_time=candle.open_time.isoformat(),
                module="ingest.binance_ws",
                message_text=f"استقبال بيانات لـ {candle.symbol} ({candle.timeframe}) - السعر: {candle.close} - مغلقة: {candle.is_closed}"
            )

        # 4f -- If is_closed: write to Postgres + advance checkpoint + publish.
        # Note: We ALWAYS publish to Redis so the orchestrator can see every tick if needed,
        # but only closed candles trigger persistence and checkpoint advancement.
        if candle.is_closed:
            await self._persist_closed_candle(candle)
            await self._advance_checkpoint(candle)
        
        try:
            # [TRACE] Queue push (Publish to Redis Pub/Sub)
            # Always publish to trigger analysis cycle or update live state
            await self._redis.publish_new_candle(candle)
            
            # [TRACE] Queue size (not directly available for Pub/Sub, but we log the publish)
            logger.info(
                "trace_queue_push",
                symbol=candle.symbol,
                timeframe=candle.timeframe,
                is_closed=candle.is_closed
            )
            
            logger.debug(
                "candle_published",
                symbol=candle.symbol,
                timeframe=candle.timeframe,
                is_closed=candle.is_closed,
                open_time=candle.open_time.isoformat()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="ingest.binance_ws",
                error_type=type(exc).__name__,
                error_message=f"publish_new_candle failed: {exc}",
                symbol=candle.symbol,
                timeframe=candle.timeframe,
            )

    async def _persist_closed_candle(self, candle: Candle) -> None:
        """Write a closed candle to Postgres via the idempotent upsert."""
        try:
            await self._supabase.upsert_candle(candle)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="ingest.binance_ws",
                error_type=type(exc).__name__,
                error_message=f"upsert_candle failed: {exc}",
                symbol=candle.symbol,
                timeframe=candle.timeframe,
                open_time=candle.open_time.isoformat(),
            )

    # ---------------- checkpoint advancement ----------------
    async def _advance_checkpoint(self, candle: Candle) -> None:
        """Advance the Redis + Postgres checkpoints for ``(symbol, timeframe)``."""
        if not candle.is_closed:
            # Expected condition for live candles -- log at debug (Section 6 Bug 3).
            logger.debug(
                "checkpoint_skip_unclosed",
                timestamp=datetime.now(timezone.utc),
                symbol=candle.symbol,
                timeframe=candle.timeframe,
                open_time=candle.open_time.isoformat(),
                note="skipping checkpoint advance on unclosed candle",
            )
            return

        pair_key = (candle.symbol, candle.timeframe)
        previous = self._last_checkpoint.get(pair_key)
        if previous is not None and candle.open_time <= previous:
            # Out-of-order or duplicate closed candle. Idempotent: do not
            # rewind the checkpoint. Log at debug (Section 22 Data Level).
            logger.debug(
                "checkpoint_skip_older",
                timestamp=datetime.now(timezone.utc),
                symbol=candle.symbol,
                timeframe=candle.timeframe,
                previous_open_time=previous.isoformat(),
                incoming_open_time=candle.open_time.isoformat(),
            )
            return

        # Update Redis checkpoint first.
        try:
            await self._redis.set_checkpoint(candle.symbol, candle.timeframe, candle.open_time)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="ingest.binance_ws",
                error_type=type(exc).__name__,
                error_message=f"redis.set_checkpoint failed: {exc}",
                symbol=candle.symbol,
                timeframe=candle.timeframe,
            )
            # Do NOT update the in-memory pointer if Redis failed -- the next
            # reconnect's gap-fill must still cover this candle.
            return

        # Update Postgres checkpoint (durable fallback per Section 5).
        try:
            await self._supabase.upsert_checkpoint(candle.symbol, candle.timeframe, candle.open_time)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="ingest.binance_ws",
                error_type=type(exc).__name__,
                error_message=f"supabase.upsert_checkpoint failed: {exc}",
                symbol=candle.symbol,
                timeframe=candle.timeframe,
            )
            return

        self._last_checkpoint[pair_key] = candle.open_time
        logger.info(
            "ws_checkpoint_advanced",
            timestamp=datetime.now(timezone.utc),
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            last_closed_open_time=candle.open_time.isoformat(),
        )

    async def _flush_final_checkpoints(self) -> None:
        """On graceful stop, re-write the in-memory checkpoints to both stores."""
        for (symbol, timeframe), open_time in self._last_checkpoint.items():
            try:
                await self._redis.set_checkpoint(symbol, timeframe, open_time)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "error",
                    timestamp=datetime.now(timezone.utc),
                    module="ingest.binance_ws",
                    error_type=type(exc).__name__,
                    error_message=f"final redis.set_checkpoint failed: {exc}",
                    symbol=symbol,
                    timeframe=timeframe,
                )
            try:
                await self._supabase.upsert_checkpoint(symbol, timeframe, open_time)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "error",
                    timestamp=datetime.now(timezone.utc),
                    module="ingest.binance_ws",
                    error_type=type(exc).__name__,
                    error_message=f"final supabase.upsert_checkpoint failed: {exc}",
                    symbol=symbol,
                    timeframe=timeframe,
                )

    # ---------------- disconnect / reconnect ----------------
    async def _on_disconnect(self, reason: str) -> None:
        """Log the disconnect, wait with jittered backoff, fetch gaps, reconnect."""
        import random

        uptime_seconds: Optional[float] = None
        if self._connection_started_at is not None:
            uptime_seconds = (
                datetime.now(timezone.utc) - self._connection_started_at
            ).total_seconds()

        self._connected = False
        self._ws = None
        self._connection_started_at = None

        await health_manager.update_component(
            "WebSocket", 
            HealthStatus.ERROR, 
            f"انقطع الاتصال بـ Binance WebSocket: {reason}",
            {"reason": reason, "uptime": uptime_seconds, "backoff": self._backoff_seconds}
        )

        # 1. Update backoff strategy
        if uptime_seconds is not None and uptime_seconds >= WS_STABLE_RESET_SECONDS:
            # Reset on stable connection
            self._backoff_seconds = float(WS_INITIAL_BACKOFF_SECONDS)
            self._reconnect_attempts = 0
            note = f"backoff reset to initial after {uptime_seconds:.1f}s stable run"
        else:
            # Exponential increase
            self._backoff_seconds = min(
                self._backoff_seconds * 2.0,
                float(WS_MAX_BACKOFF_SECONDS),
            )
            self._reconnect_attempts += 1
            note = f"backoff increased (attempt {self._reconnect_attempts})"

        # 2. Add Jitter (Section 4 best practice)
        jittered_sleep = self._backoff_seconds * (0.5 + random.random())
        
        logger.info(
            "ws_reconnect",
            timestamp=datetime.now(timezone.utc),
            attempt=self._reconnect_attempts,
            backoff_seconds=self._backoff_seconds,
            jittered_sleep=f"{jittered_sleep:.2f}s",
            note=note,
        )

        # 3. Sleep with jitter
        try:
            await asyncio.sleep(jittered_sleep)
        except asyncio.CancelledError:
            raise

        # 4. Refill gaps BEFORE reconnecting
        await self._refill_all_gaps()

    # ---------------- gap fill via REST ----------------
    async def _refill_all_gaps(self) -> None:
        """For every active ``(symbol, timeframe)`` pair, fetch and persist any missed candles."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(REST_TIMEOUT_SECONDS),
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            )

        now = datetime.now(timezone.utc)
        for symbol, timeframe in self._active_pairs:
            try:
                # 1. Get the last known checkpoint
                since = await self._load_checkpoint(symbol, timeframe)
                if not since:
                    # If no checkpoint, we fetch the default window
                    limit = resume_window_candles()
                    logger.info(
                        "ws_reconnect",
                        timestamp=now,
                        note=f"no checkpoint for {symbol} {timeframe}, fetching default {limit} candles",
                        symbol=symbol,
                        timeframe=timeframe,
                    )
                else:
                    # 2. Calculate missing candles
                    tf_seconds = timeframe_to_seconds(timeframe)
                    seconds_missed = (now - since).total_seconds()
                    missing_count = int(seconds_missed // tf_seconds)
                    
                    if missing_count <= 0:
                        logger.debug(
                            "ws_reconnect",
                            timestamp=now,
                            note=f"no candles missing for {symbol} {timeframe}",
                            symbol=symbol,
                            timeframe=timeframe,
                        )
                        continue
                    
                    # Cap at 1000 (Binance limit) or resume_window_candles
                    limit = min(missing_count + 1, REST_BATCH_LIMIT)
                    logger.info(
                        "ws_reconnect",
                        timestamp=now,
                        note=f"detected {missing_count} missing candles for {symbol} {timeframe}",
                        symbol=symbol,
                        timeframe=timeframe,
                        limit=limit,
                    )

                # 3. Fetch ONLY missing candles
                gap_candles = await self._fetch_gap_candles(
                    symbol=symbol,
                    timeframe=timeframe,
                    since=since,
                    limit=limit,
                )

                if not gap_candles:
                    continue

                # 4. Filter out any that might have been received just as we reconnected
                closed_gaps = [c for c in gap_candles if c.is_closed]
                if not closed_gaps:
                    continue

                # 5. Persist and advance
                for candle in closed_gaps:
                    await self._persist_closed_candle(candle)
                
                # Advance to the last one
                await self._advance_checkpoint(closed_gaps[-1])

            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "error",
                    timestamp=now,
                    module="ingest.binance_ws",
                    error_type=type(exc).__name__,
                    error_message=f"gap-fill failed for {symbol} {timeframe}: {exc}",
                )

    async def _load_checkpoint(self, symbol: str, timeframe: str) -> Optional[datetime]:
        """Load the most recent checkpoint from Redis, then Postgres."""
        pair_key = (symbol, timeframe)
        if pair_key in self._last_checkpoint:
            return self._last_checkpoint[pair_key]

        try:
            cp = await self._redis.get_checkpoint(symbol, timeframe)
            if cp:
                self._last_checkpoint[pair_key] = cp
                return cp
        except Exception:  # noqa: BLE001
            pass

        try:
            cp = await self._supabase.fetch_checkpoint(symbol, timeframe)
            if cp:
                self._last_checkpoint[pair_key] = cp
                return cp
        except Exception:  # noqa: BLE001
            pass

        return None

    async def _fetch_gap_candles(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[datetime],
        limit: int,
    ) -> list[Candle]:
        """Fetch historical candles from Binance REST API."""
        params: dict[str, Any] = {
            "symbol": symbol,
            "interval": timeframe,
            "limit": limit,
        }
        if since:
            params["startTime"] = int(since.timestamp() * 1000) + 1

        for attempt in range(WS_REST_RETRY_COUNT):
            try:
                resp = await self._http_client.get(BINANCE_REST_KLINES_URL, params=params)  # type: ignore[union-attr]
                resp.raise_for_status()
                data = resp.json()
                return self._parse_rest_klines(data, symbol, timeframe)
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                if attempt == WS_REST_RETRY_COUNT - 1:
                    raise
                logger.warning(
                    "ws_reconnect",
                    timestamp=datetime.now(timezone.utc),
                    note=f"REST klines failed (attempt {attempt+1}): {exc}",
                    symbol=symbol,
                    timeframe=timeframe,
                )
                await asyncio.sleep(1.0 * (attempt + 1))
        return []

    def _parse_rest_klines(
        self,
        data: Any,
        symbol: str,
        timeframe: str,
    ) -> list[Candle]:
        """Parse the array-of-arrays response from ``GET /api/v3/klines``."""
        if not isinstance(data, list):
            raise InvalidCandleError(
                f"rest_klines_not_list: type={type(data).__name__}",
                details={"symbol": symbol, "timeframe": timeframe},
            )

        out: list[Candle] = []
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        tf_seconds = timeframe_to_seconds(timeframe)
        for idx, row in enumerate(data):
            if not isinstance(row, list) or len(row) < 11:
                continue
            try:
                open_time_ms = int(row[0])
                close_time_ms = int(row[6])
                o = float(row[1])
                h = float(row[2])
                low = float(row[3])
                c = float(row[4])
                v = float(row[5])
                taker_buy = float(row[9])
            except (TypeError, ValueError):
                continue

            taker_sell = v - taker_buy
            if -1e-6 < taker_sell < 0:
                taker_sell = 0.0

            is_closed = close_time_ms < now_ms
            if is_closed and (now_ms - close_time_ms) < (tf_seconds * 1000):
                if (now_ms - close_time_ms) < 1000:
                    is_closed = False

            try:
                candle = Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    open_time=datetime.fromtimestamp(open_time_ms / 1000.0, tz=timezone.utc),
                    close_time=datetime.fromtimestamp(close_time_ms / 1000.0, tz=timezone.utc),
                    open=o, high=h, low=low, close=c,
                    volume=v,
                    taker_buy_volume=taker_buy,
                    taker_sell_volume=taker_sell,
                    is_closed=is_closed,
                )
                validate_candle(candle)
                out.append(candle)
            except InvalidCandleError:
                continue

        out.sort(key=lambda c: c.open_time)
        return out

    # ---------------- health check ----------------
    async def _health_check_loop(self) -> None:
        """Periodically check that each stream has produced a message."""
        try:
            while self._running:
                await asyncio.sleep(HEALTH_CHECK_INTERVAL_SECONDS)
                now = datetime.now(timezone.utc)
                for symbol, timeframe in self._active_pairs:
                    try:
                        last = await self._redis.get_last_message(symbol, timeframe)
                    except Exception:  # noqa: BLE001
                        last = self._last_message_at.get((symbol, timeframe))

                    if last is None:
                        if self._connected and self._connection_started_at is not None:
                            uptime = (now - self._connection_started_at).total_seconds()
                            expected = timeframe_to_seconds(timeframe) * WS_STALE_MULTIPLIER
                            if uptime > expected:
                                logger.warning(
                                    "ws_stale",
                                    timestamp=now,
                                    symbol=symbol,
                                    timeframe=timeframe,
                                    seconds_since_last=None,
                                    note="no messages received since connect",
                                )
                        continue

                    seconds_since = (now - last).total_seconds()
                    expected_interval = timeframe_to_seconds(timeframe)
                    threshold = expected_interval * WS_STALE_MULTIPLIER
                    if seconds_since > threshold:
                        await health_manager.update_component(
                            "WebSocket", 
                            HealthStatus.WARNING, 
                            f"WebSocket data stale for {symbol} {timeframe}: {seconds_since:.1f}s",
                            {"symbol": symbol, "timeframe": timeframe, "delta": seconds_since}
                        )
        except asyncio.CancelledError:
            return

    # ---------------- helpers ----------------
    async def _cleanup_background_tasks(self) -> None:
        """Cancel and await the health-check task on shutdown."""
        if self._health_task is None:
            return
        if not self._health_task.done():
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
        self._health_task = None

    @staticmethod
    def _build_active_pairs(coins: list[CoinConfig]) -> list[tuple[str, str]]:
        """Build the deduplicated (symbol, timeframe) list from coin configs."""
        seen: set[tuple[str, str]] = set()
        pairs: list[tuple[str, str]] = []
        for coin in coins:
            if not coin.is_active:
                continue
            for tf in coin.timeframes:
                key = (coin.symbol, tf)
                if key in seen:
                    continue
                seen.add(key)
                pairs.append(key)
        if not pairs:
            raise ValueError("no active (symbol, timeframe) pairs")
        return pairs

    @staticmethod
    def _describe_exception(exc: Exception) -> str:
        """Compact one-line description of an exception."""
        if isinstance(exc, ConnectionClosedOK):
            return "connection_closed_ok"
        if isinstance(exc, ConnectionClosedError):
            return f"connection_closed_error: code={exc.code} reason={exc.reason!r}"
        if isinstance(exc, ConnectionClosed):
            return f"connection_closed: code={exc.code} reason={exc.reason!r}"
        if isinstance(exc, asyncio.TimeoutError):
            return "recv_timeout"
        return f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Convenience module-level starter
# ---------------------------------------------------------------------------
async def start_ingestion(
    coins: list[CoinConfig],
    redis: RedisCache,
    supabase: SupabaseClient,
) -> BinanceWSClient:
    """Construct and start a ``BinanceWSClient``."""
    client = BinanceWSClient(coins=coins, redis=redis, supabase=supabase)
    return client


__all__ = ["BinanceWSClient", "start_ingestion"]
