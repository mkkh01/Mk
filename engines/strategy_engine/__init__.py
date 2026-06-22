"""
محرك الاستراتيجيات — CT V4.0
يدير وينفذ كل استراتيجيات التداول. كل استراتيجية تُنفذ لكل إطار زمني بشكل مستقل.
لا تلويث بين الأطر الزمنية. الاستراتيجيات لا تتواصل مع بعضها مباشرة.
"""
import asyncio
import logging
from typing import Dict, List, Optional
from datetime import datetime
import importlib
import importlib.util
import os

from core.base import BaseEngine
from core.events import SignalEvent, AnalysisEvent, EventBus, HealthEvent, HealthStatus
from core.types import MarketAnalysis
from core.errors import StrategyError
from strategies import StrategySignal, BaseStrategy

logger = logging.getLogger("strategy_engine")


class StrategyEngine(BaseEngine):
    """يدير وينفذ استراتيجيات التداول في عزلة تامة لكل إطار زمني."""

    def __init__(self, event_bus: EventBus):
        super().__init__("strategy_engine")
        self.event_bus = event_bus
        self.strategies: Dict[str, BaseStrategy] = {}
        self._active_strategies: set[str] = set()
        self._last_signals: Dict[str, Dict[str, List[StrategySignal]]] = {}  # symbol → timeframe → signals
        self._strategy_dir = os.path.join(os.path.dirname(__file__), "..", "..", "strategies")

    async def initialize(self) -> None:
        """تحميل الاستراتيجيات والاشتراك في الأحداث."""
        await self.event_bus.subscribe("AnalysisEvent", self._on_analysis)
        self._load_strategies()
        self.logger.info(f"[الاستراتيجيات] تم التهيئة. المحملة: {list(self.strategies.keys())}")

    async def start(self) -> None:
        """بدء المحرك."""
        self._running = True
        asyncio.create_task(self._heartbeat_loop())
        self.logger.info("[الاستراتيجيات] تم البدء")

    async def stop(self) -> None:
        """إيقاف المحرك."""
        self._running = False
        self.logger.info("[الاستراتيجيات] تم الإيقاف")

    def _load_strategies(self):
        """تحميل ديناميكي لكل الاستراتيجيات من مجلد الاستراتيجيات."""
        try:
            strategies_dir = os.path.abspath(self._strategy_dir)
            if not os.path.exists(strategies_dir):
                self.logger.warning(f"[الاستراتيجيات] مجلد الاستراتيجيات غير موجود: {strategies_dir}")
                return

            for filename in sorted(os.listdir(strategies_dir)):
                if filename.endswith(".py") and not filename.startswith("_"):
                    module_name = filename[:-3]
                    try:
                        spec = importlib.util.spec_from_file_location(
                            module_name, os.path.join(strategies_dir, filename)
                        )
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)

                        for attr_name in dir(module):
                            attr = getattr(module, attr_name)
                            if (isinstance(attr, type)
                                    and issubclass(attr, BaseStrategy)
                                    and attr is not BaseStrategy
                                    and attr_name.endswith("Strategy")):
                                instance = attr()
                                # تحقق من العقد قبل التشغيل
                                valid, msg = instance.validate_contract()
                                if not valid:
                                    self.logger.error(
                                        f"[الاستراتيجيات] ❌ {module_name} مرفوضة: {msg}"
                                    )
                                    continue
                                self.strategies[module_name] = instance
                                self._active_strategies.add(module_name)
                                self.logger.info(
                                    f"[الاستراتيجيات] ✓ تم تحميل: {instance.meta.name} v{instance.meta.version} "
                                    f"| الأطر: {instance.meta.supported_timeframes} "
                                    f"| min_confidence: {instance.meta.min_confidence}% "
                                    f"| Regimes: {instance.meta.suitable_regimes}"
                                )
                    except Exception as e:
                        self.logger.error(f"[الاستراتيجيات] فشل تحميل {module_name}: {e}")
        except Exception as e:
            self.logger.error(f"[الاستراتيجيات] خطأ في التحميل: {e}")

    # ── Event Handler (backward-compatible) ─────────────────────

    async def _on_analysis(self, event: AnalysisEvent):
        """استقبال تحليل السوق — تنفيذ الاستراتيجيات للإطار الزمني الحالي."""
        if not self._running:
            return

        analysis = MarketAnalysis(
            symbol=event.symbol,
            regime=event.regime,
            trend_direction=event.trend_direction,
            trend_strength=event.trend_strength,
            momentum=event.momentum,
            volatility=event.volatility,
            liquidity_score=event.liquidity_score,
            structure=event.structure,
            breakout_state=event.breakout_state,
            noise_level=event.noise_level,
            confidence=event.confidence,
        )
        # التشغيل بدون إطار زمني محدد — كل الاستراتيجيات النشطة
        await self.run_strategies(event.symbol, None, analysis)

    # ── Core API ──────────────────────────────────────────────

    async def run_strategies(
        self, symbol: str, timeframe: Optional[str], analysis: MarketAnalysis
    ) -> List[StrategySignal]:
        """
        تشغيل كل الاستراتيجيات النشطة لإطار زمني محدد.
        إذا كان timeframe=None، تشغّل كل الاستراتيجيات بغض النظر عن الإطار.
        """
        signals: List[StrategySignal] = []

        for name in sorted(self._active_strategies):
            strategy = self.strategies.get(name)
            if not strategy:
                continue

            # تصفية حسب الإطار الزمني إذا كان محدداً
            if timeframe is not None and timeframe not in strategy.meta.supported_timeframes:
                continue

            try:
                signal = await strategy.evaluate(analysis)
                if signal is None:
                    continue

                signal.symbol = symbol
                signal.timeframe = timeframe or "unknown"

                if signal.action != "HOLD":
                    signals.append(signal)
                    self.logger.info(
                        f"[إشارة] {symbol} | {signal.strategy_name} | "
                        f"{signal.timeframe} | {signal.action} | "
                        f"ثقة {signal.confidence:.0f}% | {signal.reasoning}"
                    )

                    # تخزين آخر إشارة
                    self._last_signals.setdefault(symbol, {}).setdefault(
                        signal.timeframe, []
                    ).append(signal)

                    # نشر حدث الإشارة
                    await self.event_bus.publish(SignalEvent(
                        symbol=symbol,
                        strategy_name=signal.strategy_name,
                        action=signal.action,
                        confidence=signal.confidence,
                        score_breakdown=signal.score_breakdown,
                        reasoning=signal.reasoning,
                    ))
                else:
                    self.logger.debug(
                        f"[إشارة] {symbol} | {signal.strategy_name} | "
                        f"{signal.timeframe} | انتظار | ثقة {signal.confidence:.0f}%"
                    )

            except Exception as e:
                self.logger.error(
                    f"[الاستراتيجيات] خطأ في {name} للرمز {symbol}: {e}",
                    exc_info=True
                )

        return signals

    async def run_all_timeframes(
        self, symbol: str, analyses: Dict[str, MarketAnalysis]
    ) -> Dict[str, List[StrategySignal]]:
        """
        تشغيل كل الاستراتيجيات لكل الأطر الزمنية دفعة واحدة.
        لا تلويث بين الأطر — كل إطار زمني مستقل تماماً.

        Args:
            symbol: رمز العملة
            analyses: dict يربط كل إطار زمني بتحليله الخاص
                مثال: {"15m": MarketAnalysis(...), "1h": MarketAnalysis(...), "4h": MarketAnalysis(...)}

        Returns:
            dict يربط كل إطار زمني بقائمة إشاراته
                مثال: {"15m": [Signal, ...], "1h": [Signal, ...]}
        """
        all_signals: Dict[str, List[StrategySignal]] = {}

        self.logger.info(f"[الاستراتيجيات] تشغيل كل الأطر للرمز {symbol} — {len(analyses)} أطر")

        for timeframe, analysis in analyses.items():
            if not analysis:
                self.logger.warning(f"[الاستراتيجيات] {symbol} | {timeframe} — تحليل فارغ، تخطي")
                continue

            signals = await self.run_strategies(symbol, timeframe, analysis)
            if signals:
                all_signals[timeframe] = signals

        total = sum(len(s) for s in all_signals.values())
        self.logger.info(
            f"[الاستراتيجيات] {symbol} — {total} إشارة من {len(all_signals)} أطر زمنية"
        )

        return all_signals

    # ── Public Helpers ────────────────────────────────────────

    def get_last_signals(self, symbol: str, timeframe: Optional[str] = None) -> List[StrategySignal]:
        """استرجاع آخر الإشارات — اختيارياً حسب الإطار الزمني."""
        symbol_signals = self._last_signals.get(symbol, {})
        if timeframe:
            return symbol_signals.get(timeframe, [])
        # كل الإشارات من كل الأطر
        all_sigs = []
        for tf_sigs in symbol_signals.values():
            all_sigs.extend(tf_sigs)
        return all_sigs

    def get_strategies_by_timeframe(self, timeframe: str) -> List[BaseStrategy]:
        """قائمة الاستراتيجيات التي تدعم إطاراً زمنياً محدداً."""
        return [
            s for s in self.strategies.values()
            if timeframe in s.meta.supported_timeframes and s.meta.name in self._active_strategies
        ]

    def enable_strategy(self, name: str):
        """تفعيل استراتيجية."""
        if name in self.strategies:
            self._active_strategies.add(name)
            self.logger.info(f"[الاستراتيجيات] تم تفعيل: {name}")

    def disable_strategy(self, name: str):
        """تعطيل استراتيجية."""
        self._active_strategies.discard(name)
        self.logger.info(f"[الاستراتيجيات] تم تعطيل: {name}")

    # ── Health ────────────────────────────────────────────────

    async def _heartbeat_loop(self):
        """نبض المحرك الدوري."""
        while self._running:
            await self.event_bus.publish(HealthEvent(
                engine=self.name,
                status=HealthStatus.HEALTHY,
                latency_ms=0,
                error_rate=0,
            ))
            await asyncio.sleep(60)
