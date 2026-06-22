"""
Market Analyzer Engine — transforms raw market data into structured market intelligence.
Does NOT trade, generate orders, or execute. Only answers:
"What is the market doing right now?"
"""
import asyncio
import json
import os
from datetime import datetime, timedelta
import numpy as np

from core.base import BaseEngine
from core.events import (
    AnalysisEvent, CandleUpdateEvent, EventBus, HealthEvent, HealthStatus
)
from core.types import MarketAnalysis, MarketRegime, TrendDirection
from config.constants import ANALYSIS_INTERVAL_SEC


class MarketAnalyzer(BaseEngine):
    """Analyzes market conditions. Produces structured analysis objects."""

    def __init__(self, event_bus: EventBus):
        super().__init__("market_analyzer")
        self.event_bus = event_bus
        self._analyses: dict[str, MarketAnalysis] = {}
        self._symbols: list[str] = []
        self._last_analysis: dict[str, datetime] = {}
        self._historical_cache: dict[str, list] = {}  # symbol → ohlcv
        self._htf_cache: dict[str, list] = {}

    async def initialize(self) -> None:
        await self.event_bus.subscribe("CandleUpdateEvent", self._on_candle_update)
        self.logger.info("Market Analyzer initialized.")

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._analysis_loop())
        asyncio.create_task(self._heartbeat_loop())
        self.logger.info("Market Analyzer started.")

    async def stop(self) -> None:
        self._running = False

    def update_symbols(self, symbols: list[str]):
        self._symbols = symbols

    async def _on_candle_update(self, event: CandleUpdateEvent):
        """Update cached kline data on each candle event."""
        key = f"{event.symbol}_{event.timeframe}"
        cache_key = event.symbol
        if cache_key not in self._historical_cache:
            self._historical_cache[cache_key] = []

        candle = {
            "timestamp": event.timestamp.timestamp() * 1000,
            "open": event.open, "high": event.high,
            "low": event.low, "close": event.close,
            "volume": event.volume,
        }

        hist = self._historical_cache[cache_key]
        if event.is_closed:
            hist.append(candle)
            if len(hist) > 300:
                hist.pop(0)

    async def _analysis_loop(self):
        """Periodic analysis of all tracked symbols."""
        while self._running:
            try:
                for symbol in self._symbols:
                    await self.analyze(symbol)
                    await asyncio.sleep(1)
                await asyncio.sleep(ANALYSIS_INTERVAL_SEC)
            except Exception as e:
                self.logger.error(f"Analysis loop error: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def analyze(self, symbol: str) -> Optional[MarketAnalysis]:
        """Run full analysis pipeline for a symbol."""
        hist = self._historical_cache.get(symbol, [])
        if len(hist) < 50:
            return None

        closes = [c["close"] for c in hist]
        highs = [c["high"] for c in hist]
        lows = [c["low"] for c in hist]
        volumes = [c["volume"] for c in hist]

        # 1. Trend Analysis
        trend_direction, trend_strength = self._analyze_trend(closes)

        # 2. Momentum Analysis
        momentum = self._analyze_momentum(closes, volumes)

        # 3. Volatility Analysis
        volatility = self._analyze_volatility(highs, lows, closes)

        # 4. Liquidity Score
        liquidity = self._analyze_liquidity(volumes)

        # 5. Market Structure
        structure = self._analyze_structure(highs, lows, closes)

        # 6. Regime Detection
        regime = self._detect_regime(trend_direction, trend_strength, momentum, volatility, liquidity)

        # 7. Breakout Validation
        breakout = self._validate_breakout(highs, lows, closes, volumes)

        # 8. Noise Level
        noise = self._calculate_noise(highs, lows, closes)

        # 9. Confidence
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
        )

        self._analyses[symbol] = analysis
        self._last_analysis[symbol] = datetime.utcnow()

        # Publish event
        await self.event_bus.publish(AnalysisEvent(
            symbol=symbol, regime=regime, trend_direction=trend_direction,
            trend_strength=trend_strength, momentum=momentum,
            volatility=volatility, liquidity_score=liquidity,
            structure=structure, breakout_state=breakout,
            noise_level=noise, confidence=confidence,
        ))

        return analysis

    # ── Analysis Sub-modules ────────────────────────────────

    def _analyze_trend(self, closes: list) -> tuple[str, float]:
        """EMA-based trend detection."""
        if len(closes) < 50:
            return "NONE", 0.0

        arr = np.array(closes)
        ema20 = self._ema(arr, 20)
        ema50 = self._ema(arr, 50)
        current = arr[-1]

        if current > ema20 > ema50:
            return "UP", min(100, (current - ema50) / current * 100 * 10)
        elif current < ema20 < ema50:
            return "DOWN", min(100, (ema50 - current) / current * 100 * 10)
        elif current > ema50:
            return "UP", min(60, (current - ema50) / current * 100 * 5)
        elif current < ema50:
            return "DOWN", min(60, (ema50 - current) / current * 100 * 5)
        return "NONE", 0.0

    def _analyze_momentum(self, closes: list, volumes: list) -> float:
        """Price velocity with volume confirmation."""
        if len(closes) < 10:
            return 50.0
        arr = np.array(closes)
        momentum = (arr[-1] - arr[-10]) / arr[-10] * 100
        vol_ratio = np.mean(volumes[-5:]) / max(1e-10, np.mean(volumes[-20:]))
        score = min(100, abs(momentum) * 10 * min(vol_ratio, 2))
        return round(score, 1)

    def _analyze_volatility(self, highs: list, lows: list, closes: list) -> float:
        """ATR-based volatility measurement."""
        if len(closes) < 14:
            return 50.0
        h, l, c = np.array(highs), np.array(lows), np.array(closes)
        tr = np.maximum(h[1:] - l[1:],
                        np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
        atr = float(np.mean(tr[-14:]))
        atr_pct = (atr / max(c[-1], 1e-10)) * 100

        if atr_pct < 0.5: return 10.0
        if atr_pct < 1.0: return 30.0
        if atr_pct < 2.0: return 60.0
        if atr_pct < 3.0: return 80.0
        return 95.0

    def _analyze_liquidity(self, volumes: list) -> float:
        """Volume consistency score."""
        if len(volumes) < 20:
            return 50.0
        avg_vol = np.mean(volumes[-20:])
        curr_vol = volumes[-1]
        consistency = 1.0 - min(1.0, np.std(volumes[-20:]) / max(avg_vol, 1e-10))
        ratio = min(curr_vol / max(avg_vol, 1e-10), 2.0)
        return round(max(0, min(100, consistency * 60 + ratio * 20)), 1)

    def _analyze_structure(self, highs: list, lows: list, closes: list) -> dict:
        """Higher High / Higher Low structure detection."""
        if len(highs) < 20:
            return {"higher_highs": False, "higher_lows": False, "break_of_structure": False}

        h_arr = np.array(highs[-20:])
        l_arr = np.array(lows[-20:])
        c_arr = np.array(closes[-5:])

        # Simple structure: compare first half to second half
        mid = 10
        hh = h_arr[mid:].max() > h_arr[:mid].max()
        hl = l_arr[mid:].min() > l_arr[:mid].min()
        bos = c_arr[-1] < l_arr[:mid].min() or c_arr[-1] > h_arr[:mid].max()

        return {
            "higher_highs": bool(hh),
            "higher_lows": bool(hl),
            "break_of_structure": bool(bos),
        }

    def _detect_regime(self, trend_dir: str, trend_str: float, momentum: float,
                       volatility: float, liquidity: float) -> str:
        """Classify market regime."""
        if volatility > 85:
            return "VOLATILE"
        if liquidity < 30:
            return "LOW_LIQUIDITY"
        if trend_str > 70 and trend_dir in ("UP", "DOWN"):
            return "TRENDING"
        if momentum > 70:
            return "TRENDING"
        if 30 < volatility < 70:
            return "RANGING"
        return "CHOPPY"

    def _validate_breakout(self, highs: list, lows: list, closes: list,
                           volumes: list) -> str:
        """Validate breakout signals."""
        if len(closes) < 20:
            return "NONE"
        h_arr = np.array(highs[-20:])
        c_arr = np.array(closes)
        v_arr = np.array(volumes)
        resistance = h_arr[:-1].max()
        if c_arr[-1] > resistance and v_arr[-1] > np.mean(v_arr[-10:]) * 1.5:
            return "VALID"
        if c_arr[-1] > resistance:
            return "FAKE"
        return "NONE"

    def _calculate_noise(self, highs: list, lows: list, closes: list) -> float:
        """Measure market noise level (chop index)."""
        if len(closes) < 14:
            return 50.0
        c_arr = np.array(closes[-14:])
        total_range = c_arr.max() - c_arr.min()
        if total_range == 0:
            return 0.0
        smoothness = 1.0 - np.std(np.diff(c_arr)) / max(total_range, 1e-10)
        return round((1.0 - smoothness) * 100, 1)

    def _calculate_confidence(self, trend_str: float, momentum: float,
                              volatility: float, liquidity: float, noise: float) -> float:
        """Calculate analysis confidence."""
        base = (trend_str * 0.3 + momentum * 0.2 + liquidity * 0.2 + (100 - volatility) * 0.2 + (100 - noise) * 0.1)
        return round(max(0, min(100, base)), 1)

    def get_analysis(self, symbol: str) -> Optional[MarketAnalysis]:
        """Get latest analysis for a symbol."""
        return self._analyses.get(symbol)

    # ── Helpers ─────────────────────────────────────────────

    @staticmethod
    def _ema(data: np.ndarray, window: int) -> float:
        """Exponential moving average."""
        if len(data) < window:
            return float(data[-1])
        alpha = 2 / (window + 1)
        ema = data[0]
        for x in data[1:]:
            ema = alpha * x + (1 - alpha) * ema
        return float(ema)

    async def _heartbeat_loop(self):
        while self._running:
            await self.event_bus.publish(HealthEvent(
                engine=self.name, status=HealthStatus.HEALTHY,
                latency_ms=0, error_rate=0,
            ))
            await asyncio.sleep(5)
