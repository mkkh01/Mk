"""
محرك بيانات السوق — المصدر الوحيد للحقيقة لبيانات السوق.
يجمع، يطبع، يتحقق، ويوزع بيانات السوق في الوقت الفعلي.
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Optional, Dict, List
import websockets

from core.base import BaseEngine
from core.events import (
    MarketTickEvent, CandleUpdateEvent, EventBus, HealthEvent, HealthStatus
)
from config.constants import BINANCE_WS_URL, RECONNECT_DELAY_SEC

logger = logging.getLogger("market_data_engine")

MAX_CANDLES_PER_BUCKET = 500

class MarketDataEngine(BaseEngine):
    """محرك بيانات السوق — يجمع بيانات كل عملة لكل إطار زمني بشكل منفصل ومعزول تماماً."""

    def __init__(self, event_bus: EventBus):
        super().__init__("market_data_engine")
        self.event_bus = event_bus
        self.candles: Dict[str, Dict[str, list]] = {}
        self.live_prices: Dict[str, dict] = {}
        self._symbols: set[str] = set()
        self._timeframes_map: Dict[str, List[str]] = {}
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._error_count: int = 0
        self._needs_reconnect: bool = False
        self._kline_count: int = 0
        self._ws_task: Optional[asyncio.Task] = None

    async def initialize(self) -> None:
        self.logger.info("[بيانات السوق] ✅ تم تهيئة محرك بيانات السوق.")

    async def start(self) -> None:
        self._running = True
        self._ws_task = asyncio.create_task(self._run_websocket_loop())
        asyncio.create_task(self._heartbeat_loop())
        self.logger.info("[بيانات السوق] ✅ بدأ محرك بيانات السوق.")

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._ws_task:
            self._ws_task.cancel()
        self.logger.info("[بيانات السوق] ⏹️ توقف محرك بيانات السوق.")

    def update_symbols(self, symbols: List[str], timeframes_map: Dict[str, List[str]]) -> None:
        new_symbols = set(symbols)
        new_tf_map = {s: sorted(set(tfs)) for s, tfs in timeframes_map.items() if s in new_symbols}

        if new_symbols != self._symbols or new_tf_map != self._timeframes_map:
            self._symbols = new_symbols
            self._timeframes_map = new_tf_map
            self._needs_reconnect = True
            self.logger.info(f"[بيانات السوق] 🔄 تحديث الاشتراكات: {len(self._symbols)} عملة")

    async def _run_websocket_loop(self):
        while self._running:
            try:
                await self._connect_and_stream()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._error_count += 1
                self._ws = None  # 🛡️ ضمان تصفير المرجع عند الخطأ
                self.logger.error(f"[بيانات السوق] ❌ خطأ WebSocket: {e}")
                await asyncio.sleep(RECONNECT_DELAY_SEC)

    async def _connect_and_stream(self):
        self._needs_reconnect = False
        if not self._symbols:
            await asyncio.sleep(2)
            return

        streams = []
        for symbol in self._symbols:
            s_lower = symbol.lower()
            streams.append(f"{s_lower}@miniTicker")
            for tf in self._timeframes_map.get(symbol, []):
                streams.append(f"{s_lower}@kline_{tf}")

        uri = f"{BINANCE_WS_URL}/stream?streams={'/'.join(streams)}"
        self.logger.info(f"[بيانات السوق] 🔌 جاري الاتصال بـ {len(streams)} streams...")

        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=60) as ws:
                self._ws = ws
                self._error_count = 0
                self.logger.info("[بيانات السوق] ✅ متصل بـ Binance.")

                while self._running and not self._needs_reconnect:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=10)
                        await self._process_message(msg)
                    except asyncio.TimeoutError:
                        continue
                    except websockets.ConnectionClosed:
                        self.logger.warning("[بيانات السوق] ⚠️ انقطع اتصال WebSocket.")
                        break
        finally:
            self._ws = None  # 🛡️ تصفير المرجع دائماً عند الخروج من السياق

    async def _process_message(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
            data = payload.get("data", {})
            stream = payload.get("stream", "")
            if "miniTicker" in stream:
                await self._handle_miniticker(data)
            elif "kline" in stream:
                await self._handle_kline(data, stream)
        except Exception as e:
            self.logger.debug(f"[بيانات السوق] ⚠️ خطأ في معالجة رسالة: {e}")

    async def _handle_miniticker(self, data: dict) -> None:
        symbol = data.get("s")
        if not symbol: return
        price = float(data.get("c", 0))
        self.live_prices[symbol] = {"price": price, "time": datetime.utcnow().strftime("%H:%M:%S")}
        await self.event_bus.publish(MarketTickEvent(symbol=symbol, price=price, volume=float(data.get("v", 0)), exchange="binance"))

    async def _handle_kline(self, data: dict, stream: str) -> None:
        k = data.get("k", {})
        symbol = data.get("s")
        if not symbol or not k: return
        self._kline_count += 1
        interval = k.get("i", "")
        is_closed = k.get("x", False)
        if is_closed:
            if symbol not in self.candles: self.candles[symbol] = {}
            if interval not in self.candles[symbol]: self.candles[symbol][interval] = []
            candle = {"t": k.get("t"), "o": float(k.get("o")), "h": float(k.get("h")), "l": float(k.get("l")), "c": float(k.get("c")), "v": float(k.get("v"))}
            bucket = self.candles[symbol][interval]
            if not bucket or bucket[-1]["t"] != candle["t"]:
                bucket.append(candle)
                if len(bucket) > MAX_CANDLES_PER_BUCKET: bucket.pop(0)

    async def _heartbeat_loop(self):
        while self._running:
            status = HealthStatus.HEALTHY if self._ws else HealthStatus.DEGRADED
            await self.event_bus.publish(HealthEvent(engine=self.name, status=status, latency_ms=0, error_rate=self._error_count))
            await asyncio.sleep(30)
