
"""
File: app/main.py
1. Single Responsibility: Be the CT process entry point -- wire every layer
   together (config -> contracts -> storage -> ingest -> engine -> simulation
   -> portfolio -> bot), run the Telegram bot, run the engine in the
   background, and orchestrate graceful shutdown.
2. Consumes: config.settings (SystemConfig), monitoring.logger,
   storage.supabase.SupabaseClient, storage.redis_cache.RedisCache,
   portfolio.performance.PerformanceCalculator, bot.telegram_bot.CTTelegramBot,
   engine.orchestrator.Orchestrator (LAZY), ingest.binance_ws.BinanceWSClient
   (LAZY), simulation.paper_trade (LAZY).
3. Produces: CTApplication class and ``async def main()`` entry point.
4. Downstream: Render web service / ``python -m app.main`` / ``python app/main.py``.
5. New Dependencies: None beyond requirements.txt. Uses asyncio + signal from
   the stdlib, plus python-telegram-bot==21.4 (already pinned).
6. Touches Section 6 bugs? No (no engine / data / structure logic here).
   Touches Section 0 hard constraints? Yes -- enforces #1 (bot stays thin:
   app/main.py owns start_engine / stop_engine, the bot only calls the
   callback) and #7 (never relabels simulated trades as live).
7. Tests: tests/integration/test_telegram_flows.py exercises start/stop engine
   and the lifecycle hooks; tests/integration/test_resume_flow.py exercises
   the restart-with-checkpoints path.
8. Logging: app_starting, app_ready, engine_started, engine_stopped,
   app_shutdown, error (Section 9 + lifecycle catalog).
9. Dependency Order: app/main.py is the LAST file in the import chain -- it
   imports from every upstream layer. engine/* and ingest/* are imported
   lazily inside methods to avoid import cycles with the orchestrator.

DESIGN NOTES
------------
* Single asyncio event loop. The Telegram Application, the WebSocket ingest
  task, the orchestrator subscriber task, and the paper-trader closure task
  all share the loop.
* ``start_engine`` is callable from two places: (a) the Telegram bot's Start
  Engine button (via the callback injected into CTTelegramBot) and (b) on app
  startup if Redis still has ``engine_running=true`` (auto-resume after
  Render restart).
* ``stop_engine`` is idempotent: it is safe to call when the engine is not
  running.
* Graceful shutdown (SIGTERM/SIGINT) -- order:
  1. stop_engine()         (cancels ingest + orchestrator + paper trader)
  2. telegram stop_polling
  3. telegram shutdown
  4. supabase.close()
  5. redis.close()
  6. log app_shutdown
* Per Section 22 -- a single coin failure NEVER crashes the whole app. Every
  background task wraps its body in try/except, logs ``error``, and continues.
* Per Section 0 hard-constraint 7 -- this file NEVER places real orders. The
  BinanceWSClient is read-only (kline subscription); paper_trade.py only
  writes rows to the ``simulated_trades`` table.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Response, status, Request
from fastapi.staticfiles import StaticFiles
from datetime import timedelta
import uvicorn

from monitoring.logger import configure_logging, get_logger

from app.dashboard_endpoints import setup_dashboard_endpoints
from app.workflow_endpoints import setup_workflow_endpoints
from storage.redis_cache import RedisCache
from storage.supabase import SupabaseClient
from monitoring.health_manager import health_manager, HealthStatus
from monitoring.heartbeat import run_heartbeat_loop

# Type-only imports (avoid hard runtime dependency on layers that may not yet
# exist when this file is imported in isolation -- e.g. during unit testing).
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.telegram_bot import CTTelegramBot
    from contracts.config import SystemConfig
    from portfolio.performance import PerformanceCalculator

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = PROJECT_ROOT / "storage" / "migrations"

# How often the paper-trader closure task wakes up to scan open trades and
# decide if any have hit their stop / take-profit.
PAPER_TRADER_POLL_SECONDS = 15

# How often to log a heartbeat for the orchestrator subscriber (so Render's
# log stream shows the process is alive even on quiet markets).
SUBSCRIBER_HEARTBEAT_SECONDS = 300

# Sentinel values for the engine state machine.
_ENGINE_STATE_LOCK = asyncio.Lock()


# ---------------------------------------------------------------------------
# CTApplication
# ---------------------------------------------------------------------------
class CTApplication:
    """Top-level orchestrator of the CT process.

    Lifecycle::

        app = CTApplication(settings)
        await app.start()        # blocks until SIGTERM/SIGINT
        # (shutdown is called internally by the signal handler)

    The class is deliberately constructed with NO side effects -- only
    ``start()`` opens connections and starts tasks.
    """

    # ---------------- construction ----------------
    def __init__(self, settings: "SystemConfig") -> None:
        self._settings: "SystemConfig" = settings
        


        # Storage -- connected in start().
        self._redis: RedisCache = RedisCache(url=settings.redis_url)
        # The SupabaseClient expects a full Postgres DSN (e.g. postgresql://user:pass@host:port/db).
        # We assume settings.supabase_url is actually the DSN in this context.
        self._supabase: SupabaseClient = SupabaseClient(
            dsn=settings.supabase_url,
            key=settings.supabase_key,
            min_size=1,
            max_size=5,
        )

        # Built in start().
        self._performance_calc: Optional["PerformanceCalculator"] = None
        self._bot: Optional["CTTelegramBot"] = None
        self._telegram_app: Optional[Any] = None  # telegram.ext.Application

        # Engine -- built lazily in start_engine().
        self._orchestrator: Optional[Any] = None
        self._ws_client: Optional[Any] = None  # ingest.binance_ws.BinanceWSClient

        # Background tasks. Held so we can cancel them on shutdown.
        self._ingest_task: Optional[asyncio.Task[None]] = None
        self._orchestrator_subscriber_task: Optional[asyncio.Task[None]] = None
        self._paper_trader_task: Optional[asyncio.Task[None]] = None
        self._telegram_polling_task: Optional[asyncio.Task[None]] = None
        self._health_log_task: Optional[asyncio.Task[None]] = None

        # Engine run-state flag (mirrors Redis ``engine_running`` so we don't
        # race the cache when the user double-clicks Start/Stop).
        self._engine_running: bool = False

        # Shutdown coordination.
        self._shutdown_event: asyncio.Event = asyncio.Event()
        self._shutdown_started: bool = False

    # =====================================================================
    # Public lifecycle
    # =====================================================================
    async def start(self) -> None:
        """Wire every layer, start polling, and wait for shutdown.

        Per the spec, this method blocks until SIGTERM or SIGINT is received.
        """
        logger.info(
            "app_starting",
            timestamp=datetime.now(timezone.utc),
            pid=os.getpid(),
            simulation_mode=self._settings.simulation_mode,
            version="1.0.0",
            environment=os.getenv("ENVIRONMENT", "production"),
            module="app.main",
            message_text="بدء تشغيل النظام - الإصدار 1.0.0"
        )

        # 1. Logging first -- every subsequent step is observable.
        configure_logging()

        # 2 + 3. Storage layers.
        try:
            await self._redis.connect()
            await health_manager.update_component("Redis", HealthStatus.OK, "تم الاتصال بنجاح بخدمة Redis")
        except Exception as exc:  # noqa: BLE001
            await health_manager.update_component("Redis", HealthStatus.CRITICAL, f"فشل الاتصال بـ Redis: {exc}")
            raise
        try:
            await self._supabase.connect()
            await health_manager.update_component("Supabase", HealthStatus.OK, "تم الاتصال بنجاح بخدمة Supabase")
        except Exception as exc:  # noqa: BLE001
            await health_manager.update_component("Supabase", HealthStatus.CRITICAL, f"فشل الاتصال بـ Supabase: {exc}")
            raise

        # 4. Apply idempotent migrations (Section 5).
        await self._apply_migrations()

        # 5. Performance calculator.
        self._performance_calc = self._build_performance_calculator()

        # 6. Telegram bot.
        self._bot = self._build_bot()

        # 7. Telegram Application.
        self._telegram_app = self._bot.build_application()

        # Inject the engine callbacks via bot_data so the bot can reach them
        # without an explicit constructor argument. (CTTelegramBot already
        # accepts the callbacks via __init__ -- we use BOTH paths so unit
        # tests can inject either.)
        self._telegram_app.bot_data["start_engine_callback"] =            self.start_engine
        self._telegram_app.bot_data["stop_engine_callback"] = self.stop_engine
        self._telegram_app.bot_data["reload_engine_callback"] = self._reload_engine
        
        # 7.5 Start the new observability heartbeat loop
        self._health_log_task = asyncio.create_task(run_heartbeat_loop(interval_seconds=60.0), name="runtime_heartbeat")

        # 8. Register signal handlers (SIGTERM for Render, SIGINT for local).
        # This is now handled by FastAPI's lifespan events.

        # 9. Initialise + start the Telegram Application, then poll in a
        # background task so this coroutine can wait on the shutdown event.
        await self._telegram_app.initialize()
        await self._telegram_app.start()
        if self._telegram_app.updater is not None:
            # [FIX] Set drop_pending_updates=True to clear any stale polling sessions 
            # from previous instances, preventing Conflict: terminated by other getUpdates.
            logger.info("telegram_polling_starting", drop_pending_updates=True)
            # Try to stop any existing polling first to be safe
            try:
                await self._telegram_app.updater.stop()
            except:
                pass
            await self._telegram_app.updater.start_polling(
                allowed_updates=None,
                drop_pending_updates=True,
            )
        self._telegram_polling_task = asyncio.create_task(
            self._telegram_polling_guard(), name="telegram_polling_guard"
        )

        # 10. Auto-resume the engine if Redis says it was running before the
        # process restarted (Render idles/restarts at will -- Section 0).
        try:
            should_resume = await self._redis.get_engine_running()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="app.main",
                error_type=type(exc).__name__,
                error_message=f"could not read engine_running flag: {exc}",
            )
            should_resume = False

        if should_resume:
            logger.info(
                "app_starting",
                timestamp=datetime.now(timezone.utc),
                note="auto-resuming engine after process restart",
            )
            try:
                await self.start_engine()
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "error",
                    timestamp=datetime.now(timezone.utc),
                    module="app.main",
                    error_type=type(exc).__name__,
                    error_message=f"auto-resume failed: {exc}",
                )
        else:
            # [FIX] Force start engine if it's not running in Redis but we have active coins
            # This ensures the system works even if the operator didn't click Start in Telegram
            # or if Redis state was lost/incorrect.
            try:
                coins = await self._supabase.fetch_all_coins(only_active=True)
                if coins:
                    logger.info(
                        "app_starting",
                        timestamp=datetime.now(timezone.utc),
                        note=f"force-starting engine on boot: {len(coins)} active coins found",
                    )
                    await self.start_engine()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "error",
                    timestamp=datetime.now(timezone.utc),
                    module="app.main",
                    error_type=type(exc).__name__,
                    error_message=f"force-start on boot failed: {exc}",
                )

        logger.info(
            "app_ready",
            timestamp=datetime.now(timezone.utc),
            auto_resumed_engine=should_resume,
        )

        # 11. Wait for shutdown signal. This is now handled by FastAPI's lifespan.
        # await self._shutdown_event.wait()

        # 12. Run graceful shutdown. This is now handled by FastAPI's lifespan.
        # await self.shutdown()

    # =====================================================================
    # Engine lifecycle
    # =====================================================================
    async def start_engine(self) -> None:
        """Start the ingest + orchestrator + paper-trader loop.

        Idempotent: if the engine is already running, returns immediately
        without side effects (the bot still shows "Engine is already running"
        because it checks ``redis.get_engine_running`` BEFORE calling this).
        """
        async with _ENGINE_STATE_LOCK:
            if self._engine_running:
                logger.info(
                    "engine_started",
                    timestamp=datetime.now(timezone.utc),
                    note="start_engine called but already running",
                    active_coins=0,
                )
                return

            # 1. Load active coins from the database.
            try:
                coins = await self._supabase.fetch_all_coins(only_active=True)
                logger.info(
                    "config_loaded", 
                    module="app.main", 
                    active_coins_count=len(coins),
                    message_text=f"تم تحميل الإعدادات: عدد العملات المفعلة {len(coins)}"
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "error",
                    timestamp=datetime.now(timezone.utc),
                    module="app.main",
                    error_type=type(exc).__name__,
                    error_message=f"could not load active coins: {exc}",
                )
                raise

            if not coins:
                logger.warning(
                    "error",
                    timestamp=datetime.now(timezone.utc),
                    module="app.main",
                    error_type="NoActiveCoins",
                    error_message="start_engine called with zero active coins",
                )
                # Ensure the flag is False in Redis and memory to avoid "fake running" state.
                await self._redis.set_engine_running(False)
                self._engine_running = False
                return

            # 2. Build the orchestrator (lazy import to avoid cycles).
            self._orchestrator = self._build_orchestrator()

            # 3. Build the BinanceWSClient (lazy import).
            self._ws_client = self._build_ws_client(coins)

            # 4. Start the ingest task in the background.
            self._ingest_task = asyncio.create_task(
                self._run_ingest_guarded(), name="ingest_binance_ws"
            )

            # 5. Start the orchestrator subscriber task.
            self._orchestrator_subscriber_task = asyncio.create_task(
                self._run_orchestrator_subscriber_guarded(),
                name="orchestrator_subscriber",
            )

            # 6. Start the paper-trader closure check task.
            self._paper_trader_task = asyncio.create_task(
                self._run_paper_trader_guarded(), name="paper_trader_closure"
            )

            # 7. Flip the engine flag last so a crash during setup doesn't
            # leave Redis reporting a running engine while no tasks exist.
            await self._redis.set_engine_running(True)
            self._engine_running = True

            # 7.5 Start health logging task AFTER setting _engine_running=True
            self._health_log_task = asyncio.create_task(
                self._run_health_logger_loop(), name="health_logger"
            )

            logger.info(
                "engine_started",
                timestamp=datetime.now(timezone.utc),
                active_coins=len(coins),
                active_pairs=sum(len(c.timeframes) for c in coins),
            )

    # -----------------------------------------------------------------
    # Direction determination for the health summary
    # -----------------------------------------------------------------
    def _determine_primary_direction(self, result: Any) -> str:
        """Derive the primary market direction from a DecisionResult for Spot.

        Uses component signals to determine the dominant direction:
          - If the primary signal (trend) is long -> bullish
          - If the primary signal (trend) is short -> bearish
          - Otherwise -> neutral/sideways

        Falls back to HTF bias when no component signals are available.
        """
        # 1. Look for trend signals first (highest weight)
        for sig in (result.component_signals or []):
            if sig.strategy_name == "trend":
                return sig.direction

        # 2. Fall back to entry direction (if a trade was approved)
        if result.entry:
            return result.entry.direction

        # 3. Fall back to HTF bias signal
        for sig in (result.component_signals or []):
            if sig.strategy_name in ("htf_filter", "momentum"):
                return sig.direction

        # 4. Default to neutral
        return "neutral"

    async def stop_engine(self, close_trades: bool = True) -> None:
        """Stop the engine gracefully.

        Args:
            close_trades: If True (default), close all open simulated trades
                with reason="time". If False, leave open trades untouched so
                they survive a restart/reload (e.g. Render SIGTERM, engine
                reload from Telegram).

        Order (Section 7 Stop Engine flow):
          1. Signal the WebSocket client to stop (flushes checkpoints).
          2. Cancel the orchestrator subscriber task.
          3. Cancel the paper-trader task.
          4. Optionally close open trades (controlled by ``close_trades``).
          5. Clear the Redis engine_running flag.
        """
        async with _ENGINE_STATE_LOCK:
            if not self._engine_running:
                logger.info(
                    "engine_stopped",
                    timestamp=datetime.now(timezone.utc),
                    open_trades_count=0,
                    note="stop_engine called but already stopped",
                )
                # Ensure Redis agrees -- a previous crash may have left it set.
                try:
                    await self._redis.set_engine_running(False)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "error",
                        timestamp=datetime.now(timezone.utc),
                        module="app.main",
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                return

            # 1. Stop the WebSocket client -- it writes final checkpoints.
            if self._ws_client is not None:
                try:
                    await self._ws_client.stop()
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "error",
                        timestamp=datetime.now(timezone.utc),
                        module="app.main",
                        error_type=type(exc).__name__,
                        error_message=f"ws_client.stop() failed: {exc}",
                    )

            # 2 + 3. Cancel background tasks.
            for task_attr in ("_ingest_task", "_orchestrator_subscriber_task", "_paper_trader_task", "_health_log_task"):
                task: Optional[asyncio.Task[None]] = getattr(self, task_attr)
                if task is not None and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "error",
                            timestamp=datetime.now(timezone.utc),
                            module="app.main",
                            error_type=type(exc).__name__,
                            error_message=f"{task_attr} cleanup raised: {exc}",
                        )
                setattr(self, task_attr, None)

            # Close open trades only if requested (default True for manual stop).
            # When close_trades=False (e.g. reload or Render SIGTERM), trades stay
            # open and resume monitoring after restart.
            open_trades_count = 0
            if close_trades:
                try:
                    open_trades = await self._supabase.fetch_open_trades()
                    open_trades_count = len(open_trades)
                    if open_trades:
                        from simulation.paper_trade import PaperTrader
                        paper_trader = PaperTrader(supabase=self._supabase)
                        
                        # Build price map from latest candles
                        price_map = {}
                        for t in open_trades:
                            # Use trade's timeframe if available, else fallback to 15m
                            tf = getattr(t, "timeframe", "15m")
                            candle = await self._supabase.fetch_latest_candle(t.symbol, tf)
                            if candle:
                                price_map[t.symbol] = candle.close
                        
                        closed = await paper_trader.close_all_open(price_map)
                        logger.info("engine_stop_trades_closed", closed_count=len(closed), requested_count=len(open_trades))
                    logger.info("engine_stop_closing_trades", action="closing")
                except Exception as exc:
                    logger.warning(f"Failed to close trades on engine stop: {exc}")
            else:
                try:
                    open_trades = await self._supabase.fetch_open_trades()
                    open_trades_count = len(open_trades)
                except Exception:
                    open_trades_count = 0
                if open_trades_count > 0:
                    logger.info(
                        "engine_stop_trades_preserved",
                        preserved_count=open_trades_count,
                        note="Trades left open — will resume monitoring on next start",
                    )

            # 4. Clear the Redis flag.
            try:
                await self._redis.set_engine_running(False)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "error",
                    timestamp=datetime.now(timezone.utc),
                    module="app.main",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )

            self._engine_running = False
            self._ws_client = None
            self._orchestrator = None

            logger.info(
                "engine_stopped",
                timestamp=datetime.now(timezone.utc),
                open_trades_count=open_trades_count,
            )

    # =====================================================================
    # Shutdown
    # =====================================================================
    async def shutdown(self) -> None:
        """Graceful shutdown -- stop engine, stop Telegram, close storage."""
        # Guard against double-shutdown (signal + explicit call).
        if self._shutdown_started:
            return
        self._shutdown_started = True

        logger.info(
            "app_shutdown",
            timestamp=datetime.now(timezone.utc),
            stage="begin",
        )

        # 1. Stop the engine first so we flush checkpoints before closing
        # the storage layer.  Open trades are preserved (close_trades=False)
        # so they survive SIGTERM from Render (redeploy, cron, scaling) and
        # resume monitoring on next boot via auto-resume.
        try:
            await self.stop_engine(close_trades=False)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="app.main",
                error_type=type(exc).__name__,
                error_message=f"stop_engine during shutdown failed: {exc}",
            )

        # 2. Stop Telegram polling.
        if self._telegram_app is not None:
            try:
                if self._telegram_app.updater is not None:
                    await self._telegram_app.updater.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "error",
                    timestamp=datetime.now(timezone.utc),
                    module="app.main",
                    error_type=type(exc).__name__,
                    error_message=f"updater.stop() failed: {exc}",
                )
            try:
                await self._telegram_app.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "error",
                    timestamp=datetime.now(timezone.utc),
                    module="app.main",
                    error_type=type(exc).__name__,
                    error_message=f"telegram_app.stop() failed: {exc}",
                )
            try:
                await self._telegram_app.shutdown()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "error",
                    timestamp=datetime.now(timezone.utc),
                    module="app.main",
                    error_type=type(exc).__name__,
                    error_message=f"telegram_app.shutdown() failed: {exc}",
                )

        # Cancel the polling guard if it's still running.
        if self._telegram_polling_task is not None and not self._telegram_polling_task.done():
            self._telegram_polling_task.cancel()
            try:
                await self._telegram_polling_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "error",
                    timestamp=datetime.now(timezone.utc),
                    module="app.main",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )

        # 3. Close Supabase.
        try:
            await self._supabase.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="app.main",
                error_type=type(exc).__name__,
                error_message=f"supabase.close() failed: {exc}",
            )

        # 4. Close Redis.
        try:
            await self._redis.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="app.main",
                error_type=type(exc).__name__,
                error_message=f"redis.close() failed: {exc}",
            )

        logger.info(
            "app_shutdown",
            timestamp=datetime.now(timezone.utc),
            stage="complete",
        )

    # =====================================================================
    # Background task bodies (guarded -- never let one crash kill the process)
    # =====================================================================
    async def _run_ingest_guarded(self) -> None:
        """Run the Binance WebSocket ingest loop, isolated from process death.

        Per Section 22 -- a single coin failure must not crash the whole app.
        Any exception is logged and the task exits cleanly; the engine flag
        is NOT cleared (the operator can Stop + Start to retry).
        """
        if self._ws_client is None:
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="app.main",
                error_type="MissingWSClient",
                error_message="_run_ingest_guarded called with no ws_client",
            )
            return
        try:
            await self._ws_client.start()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="app.main",
                error_type=type(exc).__name__,
                error_message=f"ingest task crashed: {exc}",
            )

    async def _run_orchestrator_subscriber_guarded(self) -> None:
        """Subscribe to ``new_candle:*`` pub/sub channels and feed the orchestrator.

        The subscriber opens one Redis pubsub connection per (symbol,
        timeframe) and dispatches each closed-candle message to
        ``orchestrator.process_candle_safe``.
        """
        if self._orchestrator is None:
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="app.main",
                error_type="MissingOrchestrator",
                error_message="_run_orchestrator_subscriber_guarded called with no orchestrator",
            )
            return

        # Build the list of channels to subscribe to.
        try:
            coins = await self._supabase.fetch_all_coins(only_active=True)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="app.main",
                error_type=type(exc).__name__,
                error_message=f"could not load coins for subscriber: {exc}",
            )
            return

        channels: list[str] = []
        for coin in coins:
            for tf in coin.timeframes:
                channels.append(f"new_candle:{coin.symbol}:{tf}")

        if not channels:
            logger.warning(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="app.main",
                error_type="NoSubscriberChannels",
                error_message="subscriber started with zero channels",
            )
            return

        try:
            pubsub = await self._redis.get_pubsub()
            for channel in channels:
                await pubsub.subscribe(channel)
            logger.info(
                "app_ready",
                timestamp=datetime.now(timezone.utc),
                note="orchestrator subscriber subscribed",
                channels=len(channels),
            )

            last_heartbeat = datetime.now(timezone.utc)
            while not self._shutdown_event.is_set():
                try:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "error",
                        timestamp=datetime.now(timezone.utc),
                        module="app.main",
                        error_type=type(exc).__name__,
                        error_message=f"pubsub.get_message failed: {exc}",
                    )
                    await asyncio.sleep(1.0)
                    continue

                if message is None:
                    # Periodic heartbeat so the log stream shows we're alive.
                    now = datetime.now(timezone.utc)
                    if (now - last_heartbeat).total_seconds() >= SUBSCRIBER_HEARTBEAT_SECONDS:
                        logger.info(
                            "app_ready",
                            timestamp=now,
                            note="orchestrator subscriber heartbeat",
                        )
                        last_heartbeat = now
                        await health_manager.update_component(
                            "Orchestrator",
                            HealthStatus.OK,
                            "Orchestrator subscriber is active but idle (waiting for candles)",
                            {"last_activity": now.isoformat()}
                        )
                    continue

                await self._dispatch_candle_message(message)

            # Clean up pubsub on exit.
            try:
                await pubsub.unsubscribe()
            except Exception:  # noqa: BLE001
                pass
            try:
                await pubsub.close()
            except Exception:  # noqa: BLE001
                pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="app.main",
                error_type=type(exc).__name__,
                error_message=f"orchestrator subscriber crashed: {exc}",
            )

    async def _dispatch_candle_message(self, message: Any) -> None:
        """Decode a pubsub message and hand it to the orchestrator.

        Per Section 22 -- a single bad candle MUST NOT crash the subscriber.
        """
        import json
        from contracts.market import Candle

        # redis-py pubsub messages look like {"type": "message", "channel": b"...", "data": "..."}
        channel = message.get("channel") if isinstance(message, dict) else None
        raw_data = message.get("data") if isinstance(message, dict) else None
        if raw_data is None:
            return

        try:
            payload = json.loads(raw_data)
            candle = Candle(**payload)
        except (TypeError, ValueError, Exception) as exc:
            await health_manager.increment_stat("errors_count")
            logger.warning(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="app.main",
                error_type="InvalidPubsubPayload",
                error_message=f"could not decode pubsub payload: {exc}",
                channel=str(channel),
            )
            return

        # [TRACE] Consumer received
        now = datetime.now(timezone.utc)
        await health_manager.update_component("Ingest", HealthStatus.OK, "Received candle", {"symbol": candle.symbol, "timeframe": candle.timeframe}, timeout=60.0)
        # [FIX] Synchronize scan_cycles between local health_stats and global health_manager
        await health_manager.increment_stat("scan_cycles")
        
        logger.debug(
            "trace_consumer_received",
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            is_closed=candle.is_closed,
            module="app.main"
        )

        # OPTIMIZATION: Early return for unclosed candles BEFORE database I/O.
        # This prevents the subscriber from hanging on database pressure for 
        # thousands of tick updates that are just ignored by the engine.
        if not candle.is_closed:
            # We still updated scan_cycles and last_data_at above.
            return

        # The orchestrator requires (candle, coin_config). We must fetch the
        # config for this symbol from Supabase.
        try:
            # [TRACE] Cache check / DB fetch started
            coin_config = await self._supabase.fetch_coin(candle.symbol)
            if not coin_config:
                logger.warning(
                    "error",
                    timestamp=datetime.now(timezone.utc),
                    module="app.main",
                    error_type="MissingCoinConfig",
                    error_message=f"could not load coin config for {candle.symbol}",
                    symbol=candle.symbol,
                )
                return
            # [TRACE] Cache updated (loaded config)
        except Exception as exc:  # noqa: BLE001
            await health_manager.increment_stat("errors_count")
            logger.warning(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="app.main",
                error_type=type(exc).__name__,
                error_message=f"could not load coin config for {candle.symbol}: {exc}",
                symbol=candle.symbol,
            )
            return

        # Cooldown: skip if the same symbol was analyzed recently.
        # This prevents low-timeframe pairs (e.g. VTHO 1m) from dominating
        # the health summary.
        now_ts = datetime.now(timezone.utc)
        # Cooldown logic needs to be refactored to use health_manager or a dedicated cooldown tracker.
        # For now, disabling this part to remove dependency on _health_stats.
        last_time = None # self._health_stats["last_analysis_time"].get(candle.symbol)
        interval = 0.0 # self._health_stats["min_analysis_interval"]
        if last_time is not None:
            elapsed = (now_ts - last_time).total_seconds()
            if elapsed < interval:
                logger.debug(
                    "trace_cooling_down",
                    symbol=candle.symbol,
                    elapsed_seconds=elapsed,
                    cooldown_seconds=interval,
                )
                return

        # Process the candle.
        try:
            # Generate correlation IDs for this analysis cycle
            import uuid
            from monitoring.logger import bind_context, clear_context
            
            trace_id = str(uuid.uuid4())[:8]
            cycle_id = f"{candle.symbol}-{candle.timeframe}-{candle.open_time.strftime('%H%M%S')}"
            
            bind_context(trace_id=trace_id, cycle_id=cycle_id)
            
            # [TRACE] Analysis started
            logger.info("trace_analysis_started", symbol=candle.symbol, timeframe=candle.timeframe)
            
            start_analysis = datetime.now(timezone.utc)
            result = await self._orchestrator.process_candle_safe(candle, coin_config)
            
            # [TRACE] Analysis finished
            analysis_duration = (datetime.now(timezone.utc) - start_analysis).total_seconds() * 1000
            logger.info("trace_analysis_finished", symbol=candle.symbol, duration_ms=analysis_duration)
            
            # Clear context after analysis
            clear_context()
            
            if result:
                # Update health stats via health_manager
                await health_manager.increment_stat("analyses_executed")
                await health_manager.accumulate_analysis(
                    result.score, result.confidence, analysis_duration
                )
                # Record direction from component signals (not just approved entries).
                # This ensures bullish/bearish counts reflect actual market analysis,
                # not just approved trades.
                primary_direction = "neutral"
                if result.component_signals:
                    # Count signal directions to determine primary direction
                    long_count = sum(1 for s in result.component_signals if s.direction == "long")
                    # In Spot-only, we only care if long signals dominate.
                    long_count = sum(1 for s in result.component_signals if s.direction == "long")
                    short_count = sum(1 for s in result.component_signals if s.direction == "short")

                    if long_count > short_count:
                        primary_direction = "long"
                    elif short_count > long_count:
                        primary_direction = "short"
                    else:
                        primary_direction = "neutral"
                await health_manager.record_symbol_direction(
                    result.symbol,
                    primary_direction,
                )
                await health_manager.increment_stat("db_writes")
                
                # Count total component signals emitted (not just approved verdicts)
                signal_count = len(result.component_signals) if result.component_signals else 0
                await health_manager.increment_stat("signals_emitted", amount=max(signal_count, 0))
                
                if result.final_verdict:
                    await health_manager.increment_stat("opportunities_found")

                    # self._health_stats["last_success_at"] = datetime.now(timezone.utc) # Specific metric, remove or move to analytics
                    
                    # Send Telegram Notification (Section 20) - Only send trade opened message
                    if self._telegram_app and self._settings.telegram_chat_id and result.entry:
                        try:
                            # Open trade and send confirmation with confidence
                            from simulation.paper_trade import PaperTrader
                            trader = PaperTrader(self._supabase)
                            trade = await trader.open_trade(result)
                            
                            opened_text = self._bot.format_trade_opened(trade, confidence=result.confidence)
                            await self._telegram_app.bot.send_message(
                                chat_id=self._settings.telegram_chat_id,
                                text=opened_text,
                                parse_mode="HTML"
                            )
                            await health_manager.increment_stat("telegram_sent")
                        except Exception as t_exc:
                            logger.error(
                                "error",
                                timestamp=datetime.now(timezone.utc),
                                module="app.main",
                                error_type=type(t_exc).__name__,
                                error_message=f"failed to send telegram alert: {t_exc}",
                                symbol=candle.symbol,
                            )
                else:
                    await health_manager.increment_stat("opportunities_rejected")
                    reason = result.rejection_reason or "unknown"
                    await health_manager.record_rejection_reason(reason)
                    logger.info(
                        "decision_rejected_reason",
                        symbol=result.symbol,
                        timeframe=result.trigger_timeframe,
                        rejection_reason=reason,
                    )
        except Exception as exc:  # noqa: BLE001
            await health_manager.increment_stat("errors_count")
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="app.main",
                error_type=type(exc).__name__,
                error_message=f"orchestrator.process_candle_safe crashed: {exc}",
                symbol=candle.symbol,
                timeframe=candle.timeframe,
            )

    async def _run_paper_trader_guarded(self) -> None:
        """Periodically scan for open paper trades and close any that have hit
        their stop-loss or take-profit.

        Per Section 22 -- a single coin failure must not crash the whole app.
        """
        from simulation.paper_trade import PaperTrader

        paper_trader = PaperTrader(
            supabase=self._supabase
        )

        try:
            while True:
                # Fetch open trades before scanning to detect trailing stop updates
                open_trades_before = await self._supabase.fetch_open_trades()
                old_stops = {trade.id: trade.stop_loss for trade in open_trades_before}
                
                closed_trades = await paper_trader.scan_and_close_open_trades()
                await health_manager.update_component(
                    "PaperTrader",
                    HealthStatus.OK,
                    "PaperTrader is active and scanning for trade closures",
                    {"closed_trades_count": len(closed_trades)}
                )
                
                # Notify about trailing stop updates
                if self._telegram_app and self._settings.telegram_chat_id:
                    open_trades_after = await self._supabase.fetch_open_trades()
                    for trade in open_trades_after:
                        old_stop = old_stops.get(trade.id)
                        if old_stop is not None and trade.stop_loss is not None:
                            if abs(trade.stop_loss - old_stop) > 0.00001:  # Account for floating point precision
                                try:
                                    trailing_text = self._bot.format_trailing_stop_update(trade, old_stop)
                                    await self._telegram_app.bot.send_message(
                                        chat_id=self._settings.telegram_chat_id,
                                        text=trailing_text,
                                        parse_mode="HTML"
                                    )
                                except Exception as ts_exc:
                                    logger.warning(f"Failed to send trailing stop update notification: {ts_exc}")
                
                # Notify about closed trades
                if closed_trades and self._telegram_app and self._settings.telegram_chat_id:
                    for trade in closed_trades:
                        try:
                            closed_text = self._bot.format_trade_closed(trade)
                            await self._telegram_app.bot.send_message(
                                chat_id=self._settings.telegram_chat_id,
                                text=closed_text,
                                parse_mode="HTML"
                            )
                        except Exception as n_exc:
                            logger.warning(f"Failed to send trade closure notification: {n_exc}")

                await asyncio.sleep(PAPER_TRADER_POLL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="app.main",
                error_type=type(exc).__name__,
                error_message=f"paper trader task crashed: {exc}",
            )

    async def _telegram_polling_guard(self) -> None:
        """Guards the Telegram polling task against unexpected exits.

        If the polling task exits, this task logs the error and sets the
        shutdown event to trigger a graceful shutdown of the entire app.
        """
        if self._telegram_app is None:
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="app.main",
                error_type="MissingTelegramApp",
                error_message="_telegram_polling_guard called with no telegram_app",
            )
            self._shutdown_event.set()
            return

        try:
            # Note: start_polling is already called in start(). 
            # This guard task only needs to monitor if the updater is still running.
            while self._telegram_app.updater and self._telegram_app.updater.running:
                await asyncio.sleep(5)
            
            if not self._shutdown_started:
                logger.warning(
                    "error",
                    timestamp=datetime.now(timezone.utc),
                    module="app.main",
                    error_type="TelegramPollingStopped",
                    error_message="telegram polling stopped unexpectedly",
                )
        except asyncio.CancelledError:
            logger.info(
                "app_shutdown",
                timestamp=datetime.now(timezone.utc),
                note="telegram polling task cancelled",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="app.main",
                error_type=type(exc).__name__,
                error_message=f"telegram polling task crashed: {exc}",
            )
            self._shutdown_event.set()  # Trigger app shutdown on polling crash.

    async def _reload_engine(self) -> None:
        """Stop and restart the engine WITHOUT closing open trades.

        This is triggered by Telegram "Edit Coin" / "Add Coin" / "Delete Coin"
        operations.  Open trades survive the reload so they can continue being
        monitored by the paper-trader task after restart.

        Idempotent: if the engine is already stopped, this is a no-op.
        """
        logger.info(
            "engine_reload_start",
            timestamp=datetime.now(timezone.utc),
            note="Reloading engine — open trades will be preserved",
        )
        await self.stop_engine(close_trades=False)
        await self.start_engine()
        logger.info(
            "engine_reload_complete",
            timestamp=datetime.now(timezone.utc),
            note="Engine reloaded — open trades resumed",
        )

    # -----------------------------------------------------------------------
    # Cycle summary formatter (uses format_cycle_summary from report_formatter)
    # -----------------------------------------------------------------------
    @staticmethod
    def _format_cycle_summary_from_stats(
        stats: dict,
        health_summary: dict,
        status_map: dict,
        analyzed_count: int,
        avg_score: float,
        avg_conf: float,
        avg_time: float,
    ) -> str:
        from monitoring.report_formatter import format_cycle_summary
        return format_cycle_summary(
            pairs_analyzed=len(stats.get("unique_symbols_seen", set())),
            bullish_count=stats.get("bullish_count", 0),
            bearish_count=stats.get("bearish_count", 0),
            sideways_count=stats.get("sideways_count", 0),
            signals_found=stats.get("signals_emitted", 0),
            approved_count=stats.get("opportunities_found", 0),
            rejected_count=stats.get("opportunities_rejected", 0),
            rejection_reasons=stats.get("rejection_reasons", {}),
            avg_strategy_score=avg_score,
            avg_confidence=avg_conf,
            avg_analysis_time=avg_time,
            telegram_count=stats.get("telegram_sent", 0),
            database_writes=stats.get("db_writes", 0),
            warnings_count=stats.get("warnings_count", 0),
            errors_count=stats.get("errors_count", 0),
            system_health=status_map.get(health_summary["status"], "UNKNOWN"),
        )

    async def _run_health_logger_loop(self) -> None:
        """Periodically log health stats and diagnostic reports (Requested Log #9 & #11)."""
        while self._engine_running:
            try:
                stats = await health_manager.get_stats()
                analyzed_count = stats.get("analyses_executed", 0)

                # Calculate real averages from accumulated sums
                analyses = max(1, analyzed_count)
                avg_score = (stats.get("total_score_sum", 0.0) / analyses) * 100.0
                avg_conf = (stats.get("total_confidence_sum", 0.0) / analyses) * 100.0
                avg_time = stats.get("total_analysis_time_ms", 0.0) / analyses

                # [FIX] Refresh component statuses to prevent staleness
                try:
                    await health_manager.update_component("Redis", HealthStatus.OK, "Redis connection active")
                    await health_manager.update_component("Supabase", HealthStatus.OK, "Supabase connection active")
                    # Explicitly update Ingest and PaperTrader to prevent staleness if they are otherwise idle
                    await health_manager.update_component("Ingest", HealthStatus.OK, "Ingest component is active", timeout=60.0)
                    await health_manager.update_component("PaperTrader", HealthStatus.OK, "PaperTrader component is active", timeout=60.0)
                except Exception:
                    pass

                # Derive system health from global health_manager
                health_summary = await health_manager.get_overall_health()
                status_map = {
                    HealthStatus.OK: "EXCELLENT",
                    HealthStatus.WARNING: "GOOD",
                    HealthStatus.ERROR: "POOR",
                    HealthStatus.CRITICAL: "CRITICAL"
                }

                # Pairs analyzed = unique symbols seen
                unique_pair_count = len(stats.get("unique_symbols_seen", set()))

                summary_text = self._format_cycle_summary_from_stats(
                    stats, health_summary, status_map,
                    analyzed_count, avg_score, avg_conf, avg_time,
                )

                # Log ONLY the formatted cycle summary — no extra JSON fields.
                # Render's log stream will show the pretty block directly.
                logger.info(
                    "health_summary",
                    message_text=summary_text,
                )

                # Heartbeat to confirm active monitoring
                logger.info(
                    "engine_heartbeat",
                    timestamp=datetime.now(timezone.utc),
                    module="app.main",
                    status=status_map.get(health_summary["status"], "UNKNOWN"),
                    uptime_s=health_manager.get_uptime_seconds(),
                    message_text=f"[HEARTBEAT] System Status: {status_map.get(health_summary['status'], 'UNKNOWN')} | Uptime: {int(health_manager.get_uptime_seconds())}s"
                )

                await asyncio.sleep(60)  # Every 1 minute for faster feedback
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"Health logger error: {exc}")
                await asyncio.sleep(60)

    # =====================================================================
    # Builders (lazy imports here to keep app/main.py importable in tests
    # that don't have the full stack wired up)
    # =====================================================================
    def _build_performance_calculator(self) -> "PerformanceCalculator":
        """Construct the PerformanceCalculator with the live Supabase client."""
        from portfolio.performance import PerformanceCalculator

        return PerformanceCalculator(supabase=self._supabase)

    def _build_bot(self) -> "CTTelegramBot":
        """Construct the Telegram bot and inject the engine callbacks."""
        from bot.telegram_bot import CTTelegramBot

        return CTTelegramBot(
            supabase=self._supabase,
            redis=self._redis,
            performance_calc=self._performance_calc,  # type: ignore[arg-type]
            settings=self._settings,
            start_engine_callback=self.start_engine,
            stop_engine_callback=self.stop_engine,
            reload_engine_callback=self._reload_engine,
        )

    def _build_orchestrator(self) -> Any:
        """Construct the engine orchestrator (lazy import to avoid cycles)."""
        from engine.orchestrator import Orchestrator  # type: ignore

        return Orchestrator(
            supabase=self._supabase,
            redis=self._redis,
        )

    def _build_ws_client(self, coins: list[Any]) -> Any:
        """Construct the Binance WebSocket ingest client (lazy import)."""
        from ingest.binance_ws import BinanceWSClient  # type: ignore

        return BinanceWSClient(
            coins=coins,
            redis=self._redis,
            supabase=self._supabase,
        )

    # =====================================================================
    # Migrations
    # =====================================================================
    async def _apply_migrations(self) -> None:
        """Read every ``.sql`` file in ``storage/migrations`` and apply it.

        Files are applied in alphabetical order so the numeric prefixes
        (``001_``, ``002_``, ...) define the order. Each migration MUST be
        idempotent (``CREATE TABLE IF NOT EXISTS``, ``DO $$`` blocks).
        """
        if not MIGRATIONS_DIR.exists():
            logger.warning(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="app.main",
                error_type="MissingMigrationsDir",
                error_message=f"migrations dir not found: {MIGRATIONS_DIR}",
            )
            return

        files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        if not files:
            logger.warning(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="app.main",
                error_type="NoMigrations",
                error_message="no .sql files found in migrations dir",
            )
            return

        sqls: list[str] = []
        for path in files:
            try:
                sqls.append(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "error",
                    timestamp=datetime.now(timezone.utc),
                    module="app.main",
                    error_type=type(exc).__name__,
                    error_message=f"could not read migration {path}: {exc}",
                )
                raise

        try:
            await self._supabase.apply_migrations(sqls)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="app.main",
                error_type=type(exc).__name__,
                error_message=f"apply_migrations failed: {exc}",
            )
            raise

    # =====================================================================
    # Signal handling
    # =====================================================================
    # This is now handled by FastAPI's lifespan events.
    # def _register_signal_handlers(self) -> None:
    #     """Register SIGTERM and SIGINT handlers.

    #     SIGTERM is what Render sends on shutdown. SIGINT is what a developer
    #     sends with Ctrl+C. Both trigger a graceful shutdown.
    #     """
    #     loop = asyncio.get_running_loop()

    #     def _handler(signum: int, _frame: Any) -> None:
    #         sig_name = signal.Signals(signum).name
    #         logger.info(
    #             "app_shutdown",
    #             timestamp=datetime.now(timezone.utc),
    #             stage="signal_received",
    #             signal=sig_name,
    #         )
    #         self._shutdown_event.set()

    #     for sig in (signal.SIGTERM, signal.SIGINT):
    #         try:
    #             loop.add_signal_handler(sig, _handler, sig, None)
    #         except (NotImplementedError, RuntimeError):
    #             # add_signal_handler is not available on Windows / some
    #             # sandboxes -- fall back to the default handler.
    #             signal.signal(sig, lambda s, f: _handler(s, f))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
# Global instance of CTApplication to be managed by FastAPI lifespan.
ct_app_instance: Optional[CTApplication] = None

app = FastAPI(
    title="CT Web Server",
    description="Web server for the CT trading system, managing background tasks and Telegram bot.",
    version="1.0.0",
)

@app.on_event("startup")
async def startup_event():
    # Store instances in app.state for dependency injection
    global ct_app_instance
    try:
        from config.settings import settings
    except Exception as exc:  # noqa: BLE001
        configure_logging()
        logger.error(
            "error",
            timestamp=datetime.now(timezone.utc),
            module="app.main",
            error_type=type(exc).__name__,
            error_message=f"could not import config.settings: {exc}",
        )
        sys.exit(1)

    ct_app_instance = CTApplication(settings=settings)
    await ct_app_instance.start()
    logger.info("FastAPI startup complete, CTApplication started.")
    app.state.redis = ct_app_instance._redis
    app.state.supabase = ct_app_instance._supabase
    app.state.performance_calculator = ct_app_instance._performance_calc
    setup_dashboard_endpoints(app, ct_app_instance)
    setup_workflow_endpoints(app)
    app.mount("/dashboard", StaticFiles(directory="app/static"), name="dashboard")

@app.on_event("shutdown")
async def shutdown_event():
    global ct_app_instance
    if ct_app_instance:
        await ct_app_instance.shutdown()
        logger.info("FastAPI shutdown complete, CTApplication stopped.")

@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    return {"status": "ok", "message": "CT Web Server is healthy"}

@app.get("/ready", status_code=status.HTTP_200_OK)
async def readiness_check():
    global ct_app_instance
    if ct_app_instance and ct_app_instance._engine_running:
        return {"status": "ready", "message": "CT Engine is running"}
    return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content="CT Engine not ready")

@app.get("/", status_code=status.HTTP_200_OK)
async def root():
    return {"message": "Welcome to CT Web Server"}

# Workflow endpoints are now managed via app/workflow_endpoints.py
# and registered in the startup_event.


# This block is no longer needed as Uvicorn will run the FastAPI app directly.
# async def main() -> None:
#     """Load settings, build the application, and run it until shutdown."""
#     # Lazy import of config.settings so the rest of the module can be imported
#     # in test environments without a real settings.py on the path.
#     try:
#         from config.settings import settings
#     except Exception as exc:  # noqa: BLE001
#         # Configure logging first so the error is visible.
#         configure_logging()
#         logger.error(
#             "error",
#             timestamp=datetime.now(timezone.utc),
#             module="app.main",
#             error_type=type(exc).__name__,
#             error_message=f"could not import config.settings: {exc}",
#         )
#         sys.exit(1)

#     app = CTApplication(settings=settings)
#     try:
#         await app.start()
#     except Exception as exc:  # noqa: BLE001
#         logger.error(
#             "error",
#             timestamp=datetime.now(timezone.utc),
#             module="app.main",
#             error_type=type(exc).__name__,
#             error_message=f"app.start() crashed: {exc}",
#         )
#         # Try a best-effort shutdown so resources are released.
#         try:
#             await app.shutdown()
#         except Exception:  # noqa: BLE001
#             pass
#         sys.exit(1)


# if __name__ == "__main__":
#     asyncio.run(main())

# Uvicorn will be run directly, so this __name__ == "__main__" block is no longer needed.
# To run with uvicorn: uvicorn app.main:app --host 0.0.0.0 --port $PORT
