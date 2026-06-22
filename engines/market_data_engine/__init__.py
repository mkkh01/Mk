"""
Market Data Engine — the ONLY source of truth for all market data.
Collects, normalizes, validates, and distributes real-time market info.
NO trading, strategy, or risk logic allowed.
"""
import asyncio
import json
import os
import logging
from datetime import datetime
from typing import Optional
import websockets

from core.base import BaseEngine
from core.events import (
    MarketTickEvent, CandleUpdateEvent, OrderBookEvent, TradeEvent,
    EventBus, HealthEvent, HealthStatus
)
from core.types import UnifiedMarketData
from core.errors import ConnectionError as EngineConnectionError
from config.constants import BINANCE_WS_URL, RECONNECT_DELAY_SEC

logger = logging.getLogger("market_data_engine")


class MarketDataEngine(BaseEngine):
    """Real-time market data ingestion and distribution."""

    PRICES_CACHE = "/tmp/live_prices.json"
    KLINES_CACHE = "/tmp/live_klines.json"

    def __init__(self, event_bus: EventBus):
        super().__init__("market_data_engine")
        self.event_bus = event_bus
        self.live_prices: dict[str, dict] = {}
        self.live_klines: dict[str, dict] = {}
        self._symbols: set[str] = set()
        self._timeframes: dict[str, str] = {}  # symbol → timeframe
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._error_count: int = 0

    async def initialize(self) -> None:
        self.logger.info("Market Data Engine initialized.")

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._run_websocket_loop())
        asyncio.create_task(self._heartbeat_loop())
        self.logger.info("Market Data Engine started.")

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()

    def update_symbols(self, symbols: list[str], timeframes: dict[str, str]) -> None:
        """Update tracked symbols. Triggers WebSocket reconnect."""
        new_set = set(symbols)
        self._timeframes = timeframes
        if new_set != self._symbols:
            self._symbols = new_set
            self.logger.info(f"Symbols updated: {self._symbols}")

    async def _run_websocket_loop(self):
        """Main WebSocket connection loop with auto-reconnect."""
        while self._running:
            try:
                await self._connect_and_stream()
            except Exception as e:
                self._error_count += 1
                self.logger.error(f"WebSocket error: {e}")
                await asyncio.sleep(RECONNECT_DELAY_SEC)

    async def _connect_and_stream(self):
        """Connect to Binance WebSocket and process streams."""
        if not self._symbols:
            await asyncio.sleep(5)
            return

        streams = []
        for symbol in self._symbols:
            s_lower = symbol.lower()
            streams.append(f"{s_lower}@miniTicker")
            tf = self._timeframes.get(symbol, "15m")
            streams.append(f"{s_lower}@kline_{tf}")

        uri = f"{BINANCE_WS_URL}/stream?streams={'/'.join(streams)}"
        self.logger.info(f"Connecting to {len(streams)} streams...")

        async with websockets.connect(uri, ping_interval=20, ping_timeout=60) as ws:
            self._ws = ws
            self._error_count = 0
            self.logger.info(f"Connected. Monitoring {len(self._symbols)} symbols.")

            while self._running:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=10)
                    await self._process_message(msg)
                except asyncio.TimeoutError:
                    continue
                except websockets.ConnectionClosed:
                    self.logger.warning("WebSocket connection closed.")
                    break

    async def _process_message(self, raw: str):
        """Parse and dispatch incoming message."""
        try:
            payload = json.loads(raw)
            data = payload.get("data", {})
            stream = payload.get("stream", "")

            if "miniTicker" in stream:
                symbol = data.get("s", "")
                price = float(data.get("c", 0))
                volume = float(data.get("v", 0))
                self.live_prices[symbol] = {"price": price, "time": datetime.utcnow().strftime("%H:%M:%S")}
                await self.event_bus.publish(MarketTickEvent(
                    symbol=symbol, price=price, volume=volume, exchange="binance"
                ))
            elif "kline" in stream:
                k = data.get("k", {})
                symbol = data.get("s", "")
                interval = k.get("i", "")
                cache_key = f"{symbol}_{interval}"
                self.live_klines[cache_key] = {
                    "o": float(k.get("o", 0)),
                    "h": float(k.get("h", 0)),
                    "l": float(k.get("l", 0)),
                    "c": float(k.get("c", 0)),
                    "v": float(k.get("v", 0)),
                    "x": k.get("x", False),
                }
                await self.event_bus.publish(CandleUpdateEvent(
                    symbol=symbol, timeframe=interval,
                    open=float(k.get("o", 0)), high=float(k.get("h", 0)),
                    low=float(k.get("l", 0)), close=float(k.get("c", 0)),
                    volume=float(k.get("v", 0)), is_closed=k.get("x", False)
                ))
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            self._error_count += 1

    def get_price(self, symbol: str) -> Optional[float]:
        """Get latest price for a symbol."""
        data = self.live_prices.get(symbol)
        return data["price"] if data else None

    def get_klines_cache(self) -> dict:
        """Get full klines cache."""
        return dict(self.live_klines)

    def get_prices_cache(self) -> dict:
        """Get full prices cache."""
        return dict(self.live_prices)

    def _save_cache(self):
        """Persist cache to disk for inter-process access."""
        try:
            with open(self.PRICES_CACHE, "w") as f:
                json.dump(self.live_prices, f)
            with open(self.KLINES_CACHE, "w") as f:
                json.dump(self.live_klines, f)
        except Exception:
            pass

    async def _heartbeat_loop(self):
        """Send heartbeat to Health Monitor."""
        while self._running:
            await self.event_bus.publish(HealthEvent(
                engine=self.name,
                status=HealthStatus.HEALTHY if self._ws else HealthStatus.DEGRADED,
                latency_ms=0,
                error_rate=self._error_count / max(1, self._error_count + 100),
            ))
            self._save_cache()
            await asyncio.sleep(5)
