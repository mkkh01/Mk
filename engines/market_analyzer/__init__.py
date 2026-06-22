"""
محلل السوق — يحول بيانات السوق الخام إلى معلومات سوقية منظمة.
لا يتداول، لا يولد أوامر، لا ينفذ. مهمته الوحيدة الإجابة على سؤال:
"ماذا يفعل السوق الآن؟"

الهيكل:
  _analyses["BTCUSDT"]["1m"] = MarketAnalysis(...)
  _analyses["BTCUSDT"]["5m"] = MarketAnalysis(...)
  _analyses["BTCUSDT"]["15m"] = MarketAnalysis(...)

عزل تام بين الأطر الزمنية — كل إطار له تحليله المستقل.
المؤشرات تُحسب من شموع الإطار الزمني نفسه فقط، بدون تلويث.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, List
import numpy as np

from core.base import BaseEngine
from core.events import (
    AnalysisEvent, CandleUpdateEvent, EventBus, HealthEvent, HealthStatus
)
from core.types import MarketAnalysis, MarketRegime, TrendDirection
from config.constants import ANALYSIS_INTERVAL_SEC

logger = logging.getLogger("market_analyzer")

MAX_CANDLES_HISTORY = 300
MIN_CANDLES_FOR_ANALYSIS = 50


class MarketAnalyzer(BaseEngine):
    """يحلل أوضاع السوق لكل عملة لكل إطار زمني بشكل مستقل تماماً."""

    def __init__(self, event_bus: EventBus):
        super().__init__("market_analyzer")
        self.event_bus = event_bus

        # _analyses[coin_symbol][timeframe] = MarketAnalysis — تحليل مستقل لكل إطار
        self._analyses: Dict[str, Dict[str, MarketAnalysis]] = {}

        # _candles[coin_symbol][timeframe] = [candle, ...] — شموع خاصة بالمحلل
        self._candles: Dict[str, Dict[str, list]] = {}

        self._symbols: List[str] = []
        self._last_analysis: Dict[str, Dict[str, datetime]] = {}

    # ═══════════════════════════════════════════════════════════
    # دورة الحياة
    # ═══════════════════════════════════════════════════════════

    async def initialize(self) -> None:
        await self.event_bus.subscribe("CandleUpdateEvent", self._on_candle_update)
        self.logger.info("[محلل السوق] ✅ تم تهيئة محلل السوق.")

    async def warmup_candles(self, symbols: list[str], timeframes: set[str]):
        """تحميل شموع تاريخية من Binance REST API لتهيئة المحلل."""
        import httpx
        self.logger.info(f"[محلل السوق] 🔥 تسخين الشموع لـ {len(symbols)} عملة × {len(timeframes)} إطار...")

        async with httpx.AsyncClient(timeout=15) as client:
            for symbol in symbols:
                for tf in sorted(timeframes):
                    try:
                        url = (
                            f"https://api.binance.com/api/v3/klines"
                            f"?symbol={symbol}&interval={tf}&limit=100"
                        )
                        resp = await client.get(url)
                        if resp.status_code != 200:
                            self.logger.warning(f"[تسخين] {symbol} {tf}: HTTP {resp.status_code}")
                            continue

                        klines = resp.json()
                        if not isinstance(klines, list) or len(klines) == 0:
                            self.logger.warning(f"[تسخين] {symbol} {tf}: لا توجد بيانات")
                            continue

                        # تحويل بيانات kline إلى candles
                        candles = []
                        for k in klines:
                            candles.append({
                                "t": k[0],  # timestamp
                                "o": float(k[1]),
                                "h": float(k[2]),
                                "l": float(k[3]),
                                "c": float(k[4]),
                                "v": float(k[5]),
                            })

                        # تخزين في الهيكل
                        if symbol not in self._candles:
                            self._candles[symbol] = {}
                        self._candles[symbol][tf] = candles

                        self.logger.info(
                            f"[تسخين] ✅ {symbol} {tf}: {len(candles)} شمعة "
                            f"(من {candles[0]['o']} إلى {candles[-1]['c']})"
                        )
                        await asyncio.sleep(0.1)  # تجنب rate limiting

                    except Exception as e:
                        self.logger.error(f"[تسخين] {symbol} {tf}: {e}")

        total = sum(len(tf_dict) for tf_dict in self._candles.values())
        self.logger.info(f"[محلل السوق] ✅ تم تسخين {total} إطار زمني")

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._analysis_loop())
        asyncio.create_task(self._heartbeat_loop())
        self.logger.info("[محلل السوق] ✅ بدأ محلل السوق.")

    async def stop(self) -> None:
        self._running = False
        self.logger.info("[محلل السوق] ⏹️ توقف محلل السوق.")

    # ═══════════════════════════════════════════════════════════
    # تحديث الاشتراكات
    # ═══════════════════════════════════════════════════════════

    def update_symbols(self, symbols: List[str]) -> None:
        """تحديث قائمة العملات المتتبعة."""
        self._symbols = list(symbols)
        self.logger.info(f"[محلل السوق] 🔄 تحديث العملات: {len(self._symbols)} عملة")

    # ═══════════════════════════════════════════════════════════
    # استقبال الشموع — تخزين مستقل لكل إطار زمني
    # ═══════════════════════════════════════════════════════════

    async def _on_candle_update(self, event: CandleUpdateEvent):
        """استقبال حدث الشمعة وتخزينها في الإطار الزمني الصحيح — بدون تلويث."""
        symbol = event.symbol
        timeframe = event.timeframe

        # إنشاء الحاويات إذا لزم الأمر
        if symbol not in self._candles:
            self._candles[symbol] = {}
        if timeframe not in self._candles[symbol]:
            self._candles[symbol][timeframe] = []

        candle = {
            "t": event.timestamp.timestamp() * 1000,
            "o": event.open,
            "h": event.high,
            "l": event.low,
            "c": event.close,
            "v": event.volume,
        }

        bucket = self._candles[symbol][timeframe]

        if event.is_closed:
            # تجنب التكرار
            if bucket and bucket[-1]["t"] == candle["t"]:
                bucket[-1] = candle
                return
            bucket.append(candle)
            if len(bucket) > MAX_CANDLES_HISTORY:
                bucket.pop(0)

            # تسجيل تشخيصي — كل 50 شمعة لكل إطار
            count = len(bucket)
            if count % 50 == 0 or count == 1:
                self.logger.info(
                    f"[محلل السوق] 📈 {symbol} {timeframe}: "
                    f"{count} شمعة | آخر سعر={event.close:.6f}"
                )

    # ═══════════════════════════════════════════════════════════
    # حلقة التحليل الدورية
    # ═══════════════════════════════════════════════════════════

    async def _analysis_loop(self):
        """تحليل دوري لجميع العملات بكل أطرها الزمنية."""
        while self._running:
            try:
                for symbol in self._symbols:
                    await self.analyze_all_timeframes(symbol)
                    await asyncio.sleep(1)
                await asyncio.sleep(ANALYSIS_INTERVAL_SEC)
            except Exception as e:
                self.logger.error(f"[محلل السوق] ❌ خطأ في حلقة التحليل: {e}", exc_info=True)
                await asyncio.sleep(5)

    # ═══════════════════════════════════════════════════════════
    # التحليل — لكل إطار زمني بشكل مستقل
    # ═══════════════════════════════════════════════════════════

    async def analyze_all_timeframes(self, symbol: str) -> Dict[str, MarketAnalysis]:
        """
        تشغيل التحليل لجميع الأطر الزمنية المتاحة للعملة.
        يرجع قاموساً: {timeframe: MarketAnalysis, ...}
        """
        results = {}
        symbol_candles = self._candles.get(symbol, {})

        if not symbol_candles:
            self.logger.debug(f"[محلل السوق] لا توجد شموع لـ {symbol} بعد.")
            return results

        for timeframe in list(symbol_candles.keys()):
            analysis = await self.analyze(symbol, timeframe)
            if analysis:
                results[timeframe] = analysis

        return results

    async def analyze(self, symbol: str, timeframe: str) -> Optional[MarketAnalysis]:
        """
        تحليل سوقي كامل لعملة + إطار زمني محدد.
        كل المؤشرات تُحسب من شموع هذا الإطار فقط — بدون تلويث من أطر أخرى.
        """
        candles = self._candles.get(symbol, {}).get(timeframe, [])

        if len(candles) < MIN_CANDLES_FOR_ANALYSIS:
            self.logger.info(
                f"[تحليل] ⏳ {symbol} {timeframe}: "
                f"{len(candles)}/{MIN_CANDLES_FOR_ANALYSIS} شمعة — غير كافٍ للتحليل"
            )
            return None

        # استخراج البيانات كمصفوفات numpy — من هذا الإطار فقط
        closes = np.array([c["c"] for c in candles], dtype=np.float64)
        highs = np.array([c["h"] for c in candles], dtype=np.float64)
        lows = np.array([c["l"] for c in candles], dtype=np.float64)
        volumes = np.array([c["v"] for c in candles], dtype=np.float64)

        # 1. تحليل الاتجاه
        trend_direction, trend_strength = self._analyze_trend(closes)

        # 2. تحليل الزخم
        momentum = self._analyze_momentum(closes, volumes)

        # 3. تحليل التقلب
        volatility = self._analyze_volatility(highs, lows, closes)

        # 4. تحليل السيولة
        liquidity = self._analyze_liquidity(volumes)

        # 5. تحليل هيكل السوق
        structure = self._analyze_structure(highs, lows, closes)

        # 6. تصنيف النظام السوقي
        regime = self._detect_regime(trend_direction, trend_strength, momentum, volatility, liquidity)

        # 7. تحقق الاختراق
        breakout = self._validate_breakout(highs, lows, closes, volumes)

        # 8. مستوى الضوضاء
        noise = self._calculate_noise(highs, lows, closes)

        # 9. درجة الثقة
        confidence = self._calculate_confidence(trend_strength, momentum, volatility, liquidity, noise)

        analysis = MarketAnalysis(
            symbol=symbol,
            regime=regime,
            trend_direction=trend_direction,
            trend_strength=trend_strength,
            momentum=momentum,
            volatility=volatility,
            liquidity_score=liquidity,
            structure=structure,
            breakout_state=breakout,
            noise_level=noise,
            confidence=confidence,
            current_price=float(closes[-1]) if len(closes) > 0 else 0.0,
            current_volume=float(volumes[-1]) if len(volumes) > 0 else 0.0,
        )

        # تخزين التحليل في الهيكل المستقل
        if symbol not in self._analyses:
            self._analyses[symbol] = {}
        self._analyses[symbol][timeframe] = analysis

        if symbol not in self._last_analysis:
            self._last_analysis[symbol] = {}
        self._last_analysis[symbol][timeframe] = datetime.utcnow()

        self.logger.info(
            f"[تحليل] 📊 {symbol} {timeframe}: "
            f"نظام={regime} | اتجاه={trend_direction}({trend_strength:.0f}) | "
            f"زخم={momentum:.1f} | تقلب={volatility:.1f} | "
            f"سيولة={liquidity:.1f} | ضوضاء={noise:.1f} | ثقة={confidence:.1f}"
        )

        # نشر الحدث
        await self.event_bus.publish(AnalysisEvent(
            symbol=symbol, regime=regime, trend_direction=trend_direction,
            trend_strength=trend_strength, momentum=momentum,
            volatility=volatility, liquidity_score=liquidity,
            structure=structure, breakout_state=breakout,
            noise_level=noise, confidence=confidence,
        ))

        return analysis

    # ═══════════════════════════════════════════════════════════
    # وحدات التحليل الفرعية — كلها تستخدم numpy
    # ═══════════════════════════════════════════════════════════

    def _analyze_trend(self, closes: np.ndarray) -> tuple:
        """تحليل الاتجاه باستخدام EMA متعدد الفترات."""
        if len(closes) < 50:
            return "NONE", 0.0

        ema_fast = self._ema(closes, 20)
        ema_slow = self._ema(closes, 50)
        current = float(closes[-1])

        if current > ema_fast > ema_slow:
            strength = min(100.0, (current - ema_slow) / current * 1000.0)
            return "UP", round(strength, 1)
        elif current < ema_fast < ema_slow:
            strength = min(100.0, (ema_slow - current) / current * 1000.0)
            return "DOWN", round(strength, 1)
        elif current > ema_slow:
            strength = min(60.0, (current - ema_slow) / current * 500.0)
            return "UP", round(strength, 1)
        elif current < ema_slow:
            strength = min(60.0, (ema_slow - current) / current * 500.0)
            return "DOWN", round(strength, 1)
        return "NONE", 0.0

    def _analyze_momentum(self, closes: np.ndarray, volumes: np.ndarray) -> float:
        """سرعة السعر مع تأكيد الحجم."""
        if len(closes) < 10:
            return 50.0

        # RSI سريع 14 فترة
        delta = np.diff(closes)
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
        avg_gain = float(np.mean(gain[-14:]))
        avg_loss = float(np.mean(loss[-14:]))

        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))

        # تأكيد الحجم
        vol_ratio = float(np.mean(volumes[-5:]) / max(1e-10, np.mean(volumes[-20:])))
        vol_factor = min(vol_ratio, 2.0)

        # نتيجة الزخم
        momentum_raw = abs(rsi - 50.0) * 2.0 * vol_factor
        return round(min(100.0, max(0.0, momentum_raw)), 1)

    def _analyze_volatility(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> float:
        """قياس التقلب باستخدام ATR."""
        if len(closes) < 14:
            return 50.0

        prev_closes = np.roll(closes, 1)
        prev_closes[0] = closes[0]

        tr = np.maximum(
            highs - lows,
            np.maximum(
                np.abs(highs - prev_closes),
                np.abs(lows - prev_closes)
            )
        )

        atr = float(np.mean(tr[-14:]))
        current_price = float(closes[-1])
        if current_price <= 0:
            return 50.0

        atr_pct = (atr / current_price) * 100.0

        if atr_pct < 0.3:
            return 10.0
        if atr_pct < 0.7:
            return 25.0
        if atr_pct < 1.2:
            return 45.0
        if atr_pct < 2.0:
            return 65.0
        if atr_pct < 3.0:
            return 82.0
        return 95.0

    def _analyze_liquidity(self, volumes: np.ndarray) -> float:
        """درجة اتساق السيولة."""
        if len(volumes) < 20:
            return 50.0

        avg_vol = float(np.mean(volumes[-20:]))
        if avg_vol <= 0:
            return 20.0

        curr_vol = float(volumes[-1])
        std_vol = float(np.std(volumes[-20:]))

        # درجة الاتساق
        consistency = 1.0 - min(1.0, std_vol / avg_vol)

        # نسبة الحجم الحالي للمتوسط
        ratio = min(curr_vol / avg_vol, 2.0)

        score = consistency * 60.0 + ratio * 20.0
        return round(max(0.0, min(100.0, score)), 1)

    def _analyze_structure(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> dict:
        """تحليل هيكل السوق — قمم وقيعان أعلى/أدنى."""
        if len(highs) < 20:
            return {"higher_highs": False, "higher_lows": False, "break_of_structure": False}

        mid = len(highs) // 2

        # نصف أول مقابل نصف ثاني
        first_half_h = highs[:mid]
        second_half_h = highs[mid:]
        first_half_l = lows[:mid]
        second_half_l = lows[mid:]

        hh = bool(np.max(second_half_h) > np.max(first_half_h))
        hl = bool(np.min(second_half_l) > np.min(first_half_l))

        # كسر الهيكل
        bos = bool(
            closes[-1] < np.min(first_half_l) or
            closes[-1] > np.max(first_half_h)
        )

        return {
            "higher_highs": hh,
            "higher_lows": hl,
            "break_of_structure": bos,
        }

    def _detect_regime(self, trend_dir: str, trend_str: float, momentum: float,
                       volatility: float, liquidity: float) -> str:
        """تصنيف النظام السوقي."""
        if volatility > 85.0:
            return "VOLATILE"
        if liquidity < 30.0:
            return "LOW_LIQUIDITY"
        if trend_str > 70.0 and trend_dir in ("UP", "DOWN"):
            return "TRENDING"
        if momentum > 70.0:
            return "TRENDING"
        if 25.0 < volatility < 70.0:
            return "RANGING"
        return "CHOPPY"

    def _validate_breakout(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                           volumes: np.ndarray) -> str:
        """التحقق من صحة الاختراق."""
        if len(closes) < 20:
            return "NONE"

        resistance = float(np.max(highs[:-1]))
        current_close = float(closes[-1])
        avg_vol_10 = float(np.mean(volumes[-10:]))
        current_vol = float(volumes[-1])

        if current_close > resistance and avg_vol_10 > 0 and current_vol > avg_vol_10 * 1.5:
            return "VALID"
        if current_close > resistance:
            return "FAKE"
        return "NONE"

    def _calculate_noise(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> float:
        """قياس مستوى ضوضاء السوق (مؤشر التشوب)."""
        if len(closes) < 14:
            return 50.0

        segment = closes[-14:]
        total_range = float(np.max(segment) - np.min(segment))
        if total_range <= 0:
            return 0.0

        diffs = np.diff(segment)
        smoothness = 1.0 - float(np.std(diffs) / total_range)
        noise = (1.0 - smoothness) * 100.0
        return round(max(0.0, min(100.0, noise)), 1)

    def _calculate_confidence(self, trend_str: float, momentum: float,
                              volatility: float, liquidity: float, noise: float) -> float:
        """حساب درجة الثقة في التحليل."""
        base = (
            trend_str * 0.30 +
            momentum * 0.20 +
            liquidity * 0.20 +
            (100.0 - volatility) * 0.20 +
            (100.0 - noise) * 0.10
        )
        return round(max(0.0, min(100.0, base)), 1)

    # ═══════════════════════════════════════════════════════════
    # واجهة القراءة العامة
    # ═══════════════════════════════════════════════════════════

    def get_analysis(self, symbol: str, timeframe: str = None) -> Optional[MarketAnalysis]:
        """
        إرجاع آخر تحليل لعملة وإطار زمني محدد.
        إذا لم يُحدد الإطار الزمني، يرجع تحليل أول إطار متاح (للتوافق مع الإصدارات السابقة).
        """
        sym = self._analyses.get(symbol, {})
        if timeframe:
            return sym.get(timeframe)
        # للتوافق: إرجاع أول إطار زمني متاح
        if sym:
            return list(sym.values())[0]
        return None

    def get_all_analyses(self, symbol: str) -> Dict[str, MarketAnalysis]:
        """إرجاع جميع تحليلات الأطر الزمنية لعملة محددة."""
        return dict(self._analyses.get(symbol, {}))

    def get_candle_count(self, symbol: str, timeframe: str) -> int:
        """عدد الشموع المخزنة لإطار زمني محدد."""
        bucket = self._candles.get(symbol, {}).get(timeframe, [])
        return len(bucket)

    # ═══════════════════════════════════════════════════════════
    # أدوات مساعدة
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _ema(data: np.ndarray, window: int) -> float:
        """المتوسط المتحرك الأسي."""
        if len(data) < window:
            return float(data[-1])
        alpha = 2.0 / (window + 1.0)
        ema = float(data[0])
        for x in data[1:]:
            ema = alpha * float(x) + (1.0 - alpha) * ema
        return ema

    # ═══════════════════════════════════════════════════════════
    # نبضات القلب
    # ═══════════════════════════════════════════════════════════

    async def _heartbeat_loop(self):
        """نبضات دورية لمراقب الصحة."""
        while self._running:
            await self.event_bus.publish(HealthEvent(
                engine=self.name,
                status=HealthStatus.HEALTHY,
                latency_ms=0,
                error_rate=0,
            ))
            await asyncio.sleep(5)
