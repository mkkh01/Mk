"""
محرك بيانات السوق — المصدر الوحيد للحقيقة لبيانات السوق.
يجمع، يطبع، يتحقق، ويوزع بيانات السوق في الوقت الفعلي.
ممنوع منطق التداول أو الاستراتيجيات أو المخاطر هنا.

الهيكل:
  candles["BTCUSDT"]["1m"] = [شمعة1, شمعة2, ...]
  candles["BTCUSDT"]["5m"] = [شمعة1, شمعة2, ...]
  candles["BTCUSDT"]["15m"] = [شمعة1, شمعة2, ...]

عزل تام بين الأطر الزمنية — كل إطار له WebSocket stream منفصل وكاش منفصل.
"""
import asyncio
import json
import os
import logging
from datetime import datetime
from typing import Optional, Dict, List
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

MAX_CANDLES_PER_BUCKET = 500
CANDLE_CACHE_DIR = "/tmp"


class MarketDataEngine(BaseEngine):
    """محرك بيانات السوق — يجمع بيانات كل عملة لكل إطار زمني بشكل منفصل ومعزول تماماً."""

    def __init__(self, event_bus: EventBus):
        super().__init__("market_data_engine")
        self.event_bus = event_bus

        # candles[coin_symbol][timeframe] — عزل تام بين الأطر الزمنية
        self.candles: Dict[str, Dict[str, list]] = {}

        # الأسعار الحية من miniTicker
        self.live_prices: Dict[str, dict] = {}

        # تتبع الاشتراكات النشطة
        self._symbols: set[str] = set()
        self._timeframes_map: Dict[str, List[str]] = {}

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._error_count: int = 0
        self._needs_reconnect: bool = False

    # ═══════════════════════════════════════════════════════════
    # دورة الحياة
    # ═══════════════════════════════════════════════════════════

    async def initialize(self) -> None:
        self.logger.info("[بيانات السوق] ✅ تم تهيئة محرك بيانات السوق.")

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._run_websocket_loop())
        asyncio.create_task(self._heartbeat_loop())
        self.logger.info("[بيانات السوق] ✅ بدأ محرك بيانات السوق.")

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        self.logger.info("[بيانات السوق] ⏹️ توقف محرك بيانات السوق.")

    # ═══════════════════════════════════════════════════════════
    # تحديث الاشتراكات
    # ═══════════════════════════════════════════════════════════

    def update_symbols(self, symbols: List[str], timeframes_map: Dict[str, List[str]]) -> None:
        """
        تحديث العملات والأطر الزمنية المتتبعة.
        triggers إعادة اتصال WebSocket عند تغيير الاشتراكات.

        symbols: قائمة رموز العملات (مثلاً ["BTCUSDT", "ETHUSDT"])
        timeframes_map: خريطة الأطر الزمنية لكل عملة
                        {"BTCUSDT": ["1m", "5m", "15m"], "ETHUSDT": ["5m", "15m"]}
        """
        new_symbols = set(symbols)
        new_tf_map = {s: sorted(set(tfs)) for s, tfs in timeframes_map.items() if s in new_symbols}

        if new_symbols != self._symbols or new_tf_map != self._timeframes_map:
            self._symbols = new_symbols
            self._timeframes_map = new_tf_map
            self._needs_reconnect = True
            self.logger.info(
                f"[بيانات السوق] 🔄 تحديث الاشتراكات: {len(self._symbols)} عملة | "
                f"{sum(len(v) for v in self._timeframes_map.values())} إطار زمني"
            )

    # ═══════════════════════════════════════════════════════════
    # WebSocket — دورة الاتصال
    # ═══════════════════════════════════════════════════════════

    async def _run_websocket_loop(self):
        """دورة WebSocket الرئيسية مع إعادة اتصال تلقائية."""
        while self._running:
            try:
                await self._connect_and_stream()
            except Exception as e:
                self._error_count += 1
                self.logger.error(f"[بيانات السوق] ❌ خطأ WebSocket: {e}")
                await asyncio.sleep(RECONNECT_DELAY_SEC)

    async def _connect_and_stream(self):
        """الاتصال بـ Binance WebSocket ومعالجة جميع الـ streams لكل عملة ولكل إطار زمني."""
        self._needs_reconnect = False

        if not self._symbols:
            self.logger.debug("[بيانات السوق] لا توجد عملات للمتابعة، انتظار...")
            await asyncio.sleep(5)
            return

        streams = []
        for symbol in self._symbols:
            s_lower = symbol.lower()
            # miniTicker — للسعر الحي
            streams.append(f"{s_lower}@miniTicker")
            # kline لكل إطار زمني للعملة
            for tf in self._timeframes_map.get(symbol, []):
                streams.append(f"{s_lower}@kline_{tf}")

        uri = f"{BINANCE_WS_URL}/stream?streams={'/'.join(streams)}"
        self.logger.info(
            f"[بيانات السوق] 🔌 جاري الاتصال بـ {len(streams)} streams "
            f"({len(self._symbols)} عملة)..."
        )

        async with websockets.connect(uri, ping_interval=20, ping_timeout=60) as ws:
            self._ws = ws
            self._error_count = 0
            self.logger.info(
                f"[بيانات السوق] ✅ متصل. جاري مراقبة {len(self._symbols)} عملة "
                f"عبر {sum(len(v) for v in self._timeframes_map.values())} إطار زمني."
            )

            while self._running and not self._needs_reconnect:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=10)
                    await self._process_message(msg)
                except asyncio.TimeoutError:
                    continue
                except websockets.ConnectionClosed:
                    self.logger.warning("[بيانات السوق] ⚠️ انقطع اتصال WebSocket.")
                    break

    # ═══════════════════════════════════════════════════════════
    # معالجة الرسائل الواردة
    # ═══════════════════════════════════════════════════════════

    async def _process_message(self, raw: str) -> None:
        """تحليل وتوزيع الرسالة الواردة من WebSocket."""
        try:
            payload = json.loads(raw)
            data = payload.get("data", {})
            stream = payload.get("stream", "")

            if "miniTicker" in stream:
                await self._handle_miniticker(data)
            elif "kline" in stream:
                await self._handle_kline(data, stream)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            self._error_count += 1
            self.logger.debug(f"[بيانات السوق] ⚠️ خطأ في تحليل رسالة: {e}")

    async def _handle_miniticker(self, data: dict) -> None:
        """معالجة بيانات miniTicker — تحديث السعر الحي."""
        symbol = data.get("s")
        if not symbol:
            return

        price = float(data.get("c", 0))
        volume = float(data.get("v", 0))

        self.live_prices[symbol] = {
            "price": price,
            "volume": volume,
            "time": datetime.utcnow().strftime("%H:%M:%S"),
        }

        await self.event_bus.publish(MarketTickEvent(
            symbol=symbol, price=price, volume=volume, exchange="binance"
        ))

    async def _handle_kline(self, data: dict, stream: str) -> None:
        """معالجة بيانات الشمعة — تخزينها في الإطار الزمني الصحيح."""
        k = data.get("k", {})
        symbol = data.get("s")
        if not symbol or not k:
            return

        interval = k.get("i", "")
        if not interval:
            return

        # قراءة بيانات الشمعة
        open_p = float(k.get("o", 0))
        high_p = float(k.get("h", 0))
        low_p = float(k.get("l", 0))
        close_p = float(k.get("c", 0))
        volume_v = float(k.get("v", 0))
        is_closed = k.get("x", False)
        kline_start = k.get("t", 0)

        # نشر الحدث عبر Event Bus مع timestamp الشمعة الحقيقي
        from datetime import timezone as tz
        candle_ts = datetime.fromtimestamp(kline_start / 1000, tz=tz.utc)
        await self.event_bus.publish(CandleUpdateEvent(
            symbol=symbol, timeframe=interval,
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=volume_v, is_closed=is_closed,
            timestamp=candle_ts,
        ))

        # تسجيل تشخيصي — مرة كل 10 شموع
        if not hasattr(self, '_kline_count'):
            self._kline_count = 0
        self._kline_count += 1
        if self._kline_count % 10 == 0:
            self.logger.info(
                f"[بيانات السوق] 📊 شمعة #{self._kline_count}: "
                f"{symbol} {interval} س={close_p:.6f} | مغلقة={'نعم' if is_closed else 'لا'}"
            )

        # تخزين الشمعة المكتملة فقط في الهيكل
        if not is_closed:
            return

        # تأكد من وجود الحاويات
        if symbol not in self.candles:
            self.candles[symbol] = {}
        if interval not in self.candles[symbol]:
            self.candles[symbol][interval] = []

        candle = {
            "t": kline_start,
            "o": open_p,
            "h": high_p,
            "l": low_p,
            "c": close_p,
            "v": volume_v,
        }

        bucket = self.candles[symbol][interval]

        # تجنب التكرار (نفس وقت بدء الشمعة)
        if bucket and bucket[-1]["t"] == kline_start:
            bucket[-1] = candle
            self.logger.debug(
                f"[شمعة] 🔄 تحديث {symbol} {interval}: "
                f"س={open_p} ع={high_p} و={low_p} إ={close_p} | مغلقة"
            )
            return

        bucket.append(candle)
        if len(bucket) > MAX_CANDLES_PER_BUCKET:
            bucket.pop(0)

        self.logger.debug(
            f"[شمعة] 🕯️ شمعة جديدة {symbol} {interval}: "
            f"س={open_p} ع={high_p} و={low_p} إ={close_p} ح={volume_v:.2f}"
        )

    # ═══════════════════════════════════════════════════════════
    # واجهة القراءة العامة
    # ═══════════════════════════════════════════════════════════

    def get_candles(self, symbol: str, timeframe: str) -> list:
        """
        إرجاع الشموع لإطار زمني محدد لعملة محددة فقط.
        لا تلويث من أطر زمنية أخرى.
        """
        return self.candles.get(symbol, {}).get(timeframe, [])

    def get_price(self, symbol: str) -> Optional[float]:
        """
        إرجاع آخر سعر حي للعملة (من miniTicker).
        إذا لم يتوفر miniTicker بعد، يحاول من آخر شمعة مغلقة من أي إطار.
        """
        lp = self.live_prices.get(symbol)
        if lp:
            return lp["price"]

        # محاولة من آخر شمعة في أي إطار زمني
        symbol_candles = self.candles.get(symbol, {})
        for tf in sorted(symbol_candles.keys()):
            bucket = symbol_candles[tf]
            if bucket:
                return bucket[-1]["c"]

        return None

    def get_prices_cache(self) -> dict:
        """إرجاع نسخة من كل الأسعار الحية."""
        return dict(self.live_prices)

    def get_klines_cache(self) -> dict:
        """
        إرجاع نسخة مسطحة من الشموع (للتوافق مع الإصدارات السابقة).
        المفتاح: "{symbol}_{timeframe}" → القيمة: آخر شمعة فقط.
        """
        flat = {}
        for symbol, tfs in self.candles.items():
            for tf, bucket in tfs.items():
                if bucket:
                    last = bucket[-1]
                    flat[f"{symbol}_{tf}"] = {
                        "o": last["o"], "h": last["h"],
                        "l": last["l"], "c": last["c"],
                        "v": last["v"], "x": True,
                    }
        return flat

    # ═══════════════════════════════════════════════════════════
    # الكاش — ملف منفصل لكل عملة لكل إطار زمني
    # ═══════════════════════════════════════════════════════════

    def _save_cache(self) -> None:
        """حفظ الكاش لكل زوج (عملة، إطار زمني) في ملف منفصل."""
        try:
            for symbol, tfs in self.candles.items():
                for tf, bucket in tfs.items():
                    cache_path = os.path.join(
                        CANDLE_CACHE_DIR, f"candles_{symbol}_{tf}.json"
                    )
                    with open(cache_path, "w") as f:
                        json.dump(bucket, f)
                    self.logger.debug(f"[بيانات السوق] 💾 كاش محفوظ: {cache_path} ({len(bucket)} شمعة)")

            # السعر الحي في ملف منفصل
            prices_path = os.path.join(CANDLE_CACHE_DIR, "live_prices.json")
            with open(prices_path, "w") as f:
                json.dump(self.live_prices, f)
        except Exception as e:
            self.logger.warning(f"[بيانات السوق] ⚠️ فشل حفظ الكاش: {e}")

    # ═══════════════════════════════════════════════════════════
    # نبضات القلب
    # ═══════════════════════════════════════════════════════════

    async def _heartbeat_loop(self):
        """نبضات دورية لمراقب الصحة."""
        while self._running:
            ws_alive = self._ws is not None
            await self.event_bus.publish(HealthEvent(
                engine=self.name,
                status=HealthStatus.HEALTHY if ws_alive else HealthStatus.DEGRADED,
                latency_ms=0,
                error_rate=self._error_count / max(1, self._error_count + 100),
            ))
            self._save_cache()
            await asyncio.sleep(5)
