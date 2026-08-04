"""
File: storage/redis_cache.py
1. Single Responsibility: Redis access (cache + pub/sub) for hot data and
   inter-module communication.
2. Consumes: redis (async client), contracts.market.Candle.
3. Produces: typed helpers for candle cache, checkpoints, engine flag,
   live prices, last-message timestamps, and pub/sub channels.
4. Downstream: ingest/binance_ws.py, engine/orchestrator.py, bot/telegram_bot.py.
5. New Dependencies: redis (in requirements.txt).
6. Touches Section 6 bugs? No.
7. Tests: tests/unit/test_storage.py (smoke tests using a fakeredis-like stub).
8. Logging: redis_op (debug only).
9. Dependency Order: config -> contracts -> storage/redis_cache.py.

REDIS DATA STRUCTURES (Section 21):
  candle:{symbol}:{timeframe}      Hash    TTL 24h
  ws_checkpoint:{symbol}:{timeframe}  Hash  no TTL
  engine_running                   String  no TTL
  last_ws_message:{symbol}:{timeframe}  String  TTL 1h
  new_candle:{symbol}:{timeframe}  Pub/Sub channel
  live_price:{symbol}              String  TTL 5m
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as redis

from contracts.market import Candle
from monitoring.logger import get_logger

logger = get_logger(__name__)


CANDLE_TTL_SECONDS = 24 * 3600
LAST_MSG_TTL_SECONDS = 3600
LIVE_PRICE_TTL_SECONDS = 300


class RedisCache:
    """Thin async wrapper around redis.asyncio.Redis.

    All public methods are async. Call ``connect()`` once at startup.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: Optional[redis.Redis] = None

    async def connect(self) -> None:
        if self._client is not None:
            return
        try:
            self._client = redis.from_url(
                self._url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=5.0,
                socket_connect_timeout=5.0,
                health_check_interval=30.0,
            )
            await self._client.ping()
        except Exception as exc:  # noqa: BLE001
            # Never log the URL -- it may contain credentials.
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="storage.redis_cache",
                error_type=type(exc).__name__,
                error_message=f"redis connection failed: {exc}",
            )
            self._client = None
            raise
        logger.info("redis_connected")

    async def close(self) -> None:
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None
        logger.info("redis_closed")

    def _require(self) -> redis.Redis:
        if self._client is None:
            raise RuntimeError("RedisCache.connect() must be called first")
        return self._client

    # ---------------- candle cache ----------------
    async def set_candle(self, candle: Candle) -> None:
        """Cache the latest candle for a (symbol, timeframe)."""
        client = self._require()
        key = f"candle:{candle.symbol}:{candle.timeframe}"
        payload = {
            "open_time": candle.open_time.isoformat(),
            "close_time": candle.close_time.isoformat(),
            "open": str(candle.open),
            "high": str(candle.high),
            "low": str(candle.low),
            "close": str(candle.close),
            "volume": str(candle.volume),
            "taker_buy_volume": str(candle.taker_buy_volume),
            "taker_sell_volume": str(candle.taker_sell_volume),
            "is_closed": str(candle.is_closed),
        }
        await client.hset(key, mapping=payload)
        await client.expire(key, CANDLE_TTL_SECONDS)

    async def get_candle(self, symbol: str, timeframe: str) -> Optional[Candle]:
        client = self._require()
        key = f"candle:{symbol}:{timeframe}"
        data = await client.hgetall(key)
        if not data:
            return None
        return Candle(
            symbol=symbol,
            timeframe=timeframe,
            open_time=datetime.fromisoformat(data["open_time"]),
            close_time=datetime.fromisoformat(data["close_time"]),
            open=float(data["open"]),
            high=float(data["high"]),
            low=float(data["low"]),
            close=float(data["close"]),
            volume=float(data["volume"]),
            taker_buy_volume=float(data["taker_buy_volume"]),
            taker_sell_volume=float(data["taker_sell_volume"]),
            is_closed=data["is_closed"] == "True",
        )

    # ---------------- live prices ----------------
    async def set_live_price(self, symbol: str, price: float) -> None:
        client = self._require()
        key = f"live_price:{symbol}"
        payload = json.dumps({"price": price, "timestamp": datetime.now(timezone.utc).isoformat()})
        await client.set(key, payload, ex=LIVE_PRICE_TTL_SECONDS)

    async def get_live_price(self, symbol: str) -> Optional[tuple[float, datetime]]:
        client = self._require()
        key = f"live_price:{symbol}"
        raw = await client.get(key)
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return float(data["price"]), datetime.fromisoformat(data["timestamp"])
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="storage.redis_cache",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return None

    # ---------------- WebSocket checkpoints ----------------
    async def set_checkpoint(
        self, symbol: str, timeframe: str, last_closed_open_time: datetime
    ) -> None:
        client = self._require()
        key = f"ws_checkpoint:{symbol}:{timeframe}"
        await client.hset(
            key,
            mapping={
                "last_closed_open_time": last_closed_open_time.isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        # No expiration: persistent across restarts (Section 21).

    async def get_checkpoint(
        self, symbol: str, timeframe: str
    ) -> Optional[datetime]:
        client = self._require()
        key = f"ws_checkpoint:{symbol}:{timeframe}"
        data = await client.hgetall(key)
        if not data:
            return None
        try:
            return datetime.fromisoformat(data["last_closed_open_time"])
        except (KeyError, ValueError):
            return None

    async def delete_checkpoint(self, symbol: str, timeframe: Optional[str] = None) -> None:
        client = self._require()
        if timeframe is None:
            # Delete all checkpoints for this symbol -- scan keys.
            pattern = f"ws_checkpoint:{symbol}:*"
            async for key in client.scan_iter(match=pattern):
                await client.delete(key)
        else:
            await client.delete(f"ws_checkpoint:{symbol}:{timeframe}")

    # ---------------- last WS message timestamps (health check) ----------------
    async def touch_last_message(self, symbol: str, timeframe: str) -> None:
        client = self._require()
        key = f"last_ws_message:{symbol}:{timeframe}"
        await client.set(key, datetime.now(timezone.utc).isoformat(), ex=LAST_MSG_TTL_SECONDS)

    async def get_last_message(self, symbol: str, timeframe: str) -> Optional[datetime]:
        client = self._require()
        key = f"last_ws_message:{symbol}:{timeframe}"
        raw = await client.get(key)
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    # ---------------- engine flag ----------------
    async def set_engine_running(self, running: bool) -> None:
        client = self._require()
        await client.set("engine_running", "true" if running else "false")

    async def get_engine_running(self) -> bool:
        client = self._require()
        raw = await client.get("engine_running")
        return raw == "true"

    # ---------------- pub/sub ----------------
    async def publish_new_candle(self, candle: Candle) -> None:
        client = self._require()
        channel = f"new_candle:{candle.symbol}:{candle.timeframe}"
        payload = candle.model_dump(mode="json")
        await client.publish(channel, json.dumps(payload))

    async def subscribe_new_candles(
        self, symbol: str, timeframe: str
    ) -> redis.client.PubSub:
        """Create a pubsub connection and subscribe to a single channel.

        Returns the :class:`redis.client.PubSub` object so the caller can
        iterate ``get_message`` / ``close`` it.  The subscription call itself
        is awaited so the channel is guaranteed to be active before the
        returned object is used.
        """
        client = self._require()
        channel = f"new_candle:{symbol}:{timeframe}"
        pubsub = client.pubsub()
        await pubsub.subscribe(channel)
        return pubsub

    async def get_pubsub(self) -> redis.client.PubSub:
        client = self._require()
        return client.pubsub()

    # ---------------- health ----------------
    async def ping(self) -> bool:
        client = self._require()
        try:
            return bool(await client.ping())
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="storage.redis_cache",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return False


__all__ = ["RedisCache"]
