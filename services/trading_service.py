"""
خدمة التداول — تنسيق مسار الصفقة الكامل.
الأدلة ← المخاطر ← التنفيذ.
V4.0: لا قيم افتراضية لرأس المال — يُقرأ من coin.capital_allocated.
       دعم الأطر الزمنية المتعددة — تجميع الإشارات من جميع الأطر.
"""
import logging
from datetime import datetime
from typing import Optional

from engines.evidence_engine import EvidenceEngine
from engines.risk_engine import RiskEngine
from engines.execution_engine import ExecutionEngine
from engines.market_analyzer import MarketAnalyzer
from engines.strategy_engine import StrategyEngine
from engines.market_data_engine import MarketDataEngine
from services.analysis_service import AnalysisService
from core.types import EvidenceResult, RiskDecision, ExecutionResult, MarketAnalysis
from database.repositories import CoinRepository, get_session

logger = logging.getLogger("تداول_الخدمة")


class TradingService:
    """تنسيق مسار التداول الكامل — من الإشارة إلى التنفيذ."""

    def __init__(self, evidence_engine: EvidenceEngine,
                 risk_engine: RiskEngine,
                 execution_engine: ExecutionEngine,
                 market_analyzer: MarketAnalyzer,
                 strategy_engine: StrategyEngine,
                 market_data_engine: MarketDataEngine,
                 analysis_service: AnalysisService):
        self.evidence = evidence_engine
        self.risk_engine = risk_engine
        self.execution = execution_engine
        self.analyzer = market_analyzer
        self.strategies = strategy_engine
        self.market_data = market_data_engine
        self.analysis_service = analysis_service
        self._signals_processed: int = 0
        self._signals_rejected: int = 0
        self._rejection_reasons: dict[str, int] = {}
        self._last_cycle_duration: float = 0.0

    async def process_symbol(self, symbol: str,
                              telegram_id: int) -> Optional[tuple]:
        """
        مسار التداول الكامل لرمز واحد عبر جميع الأطر الزمنية.
        a. لكل إطار زمني: تحليل ← استراتيجيات ← إشارات
        b. تجميع الإشارات من كل الأطر الزمنية
        c. evidence.evaluate()
        d. risk.evaluate()
        e. execution.execute()
        """
        cycle_start = datetime.utcnow()

        # a. تجميع الإشارات من جميع الأطر الزمنية
        all_signals: list = []
        all_analyses: dict[str, MarketAnalysis] = self.analysis_service.get_all_analyses(symbol)

        if not all_analyses:
            logger.debug(f"[{symbol}] ⏭️ لا توجد تحليلات متاحة — تخطي المعالجة")
            return None

        for timeframe, analysis in all_analyses.items():
            if analysis is None:
                continue
            signals = self.analysis_service._signals.get(symbol, {}).get(timeframe, [])
            if not signals:
                try:
                    signals = await self.strategies.run_strategies(symbol, analysis)
                    if signals:
                        self.analysis_service._signals.setdefault(symbol, {}).setdefault(timeframe, [])
                        self.analysis_service._signals[symbol][timeframe] = signals
                except Exception as e:
                    logger.error(f"[{symbol}] [{timeframe}] ❌ خطأ في الاستراتيجيات: {e}")
                    continue
            if signals:
                all_signals.extend(signals)
                logger.debug(
                    f"[{symbol}] [{timeframe}] 📡 {len(signals)} إشارة "
                    f"({', '.join(set(s.strategy_name for s in signals))})"
                )

        if not all_signals:
            logger.debug(f"[{symbol}] ⏸️ لا توجد إشارات من أي إطار زمني")
            return None

        # b+c. تجميع الإشارات وتقييم الأدلة
        primary_analysis: Optional[MarketAnalysis] = None
        for preferred_tf in ["1h", "4h", "15m", "1d"]:
            if preferred_tf in all_analyses and all_analyses[preferred_tf] is not None:
                primary_analysis = all_analyses[preferred_tf]
                break
        if primary_analysis is None and all_analyses:
            primary_analysis = next(iter(all_analyses.values()))

        if primary_analysis is None:
            logger.warning(f"[{symbol}] ⚠️ لا يوجد تحليل أساسي متاح — تخطي")
            return None

        whale_events = []

        try:
            evidence = await self.evidence.evaluate(primary_analysis, all_signals, whale_events)
        except Exception as e:
            logger.error(f"[{symbol}] ❌ خطأ في تقييم الأدلة: {e}")
            return None

        self._signals_processed += 1

        if evidence.decision in ("HOLD", "IGNORE"):
            reason = evidence.reasoning[:80] if evidence.reasoning else "قرار الأدلة"
            self._signals_rejected += 1
            self._rejection_reasons[reason] = self._rejection_reasons.get(reason, 0) + 1
            conflict_info = ""
            if evidence.conflicts:
                conflict_info = f" | تعارضات: {len(evidence.conflicts)}"
            logger.info(
                f"[{symbol}] ⛔ تم رفض الصفقة: {evidence.decision} "
                f"({evidence.final_score:.0f}/100){conflict_info}"
            )
            return (evidence, None, None)

        # d. تقييم المخاطر — قراءة رأس المال من DB
        capital_allocated: Optional[float] = None
        risk_per_trade: float = 1.0

        try:
            async for session in get_session():
                coin = await CoinRepository.get_by_symbol(session, telegram_id, symbol)
                if coin:
                    capital_allocated = coin.capital_allocated
                    risk_per_trade = getattr(coin, 'risk_per_trade', 1.0) or 1.0
        except Exception as e:
            logger.error(f"[{symbol}] ❌ خطأ في قراءة إعدادات العملة: {e}")

        if capital_allocated is None:
            logger.error(
                f"[{symbol}] ❌ لم يتم إيجاد رأس مال مخصص — "
                f"يجب على المستخدم تعيين capital_allocated للعملة"
            )
            return (evidence, None, None)

        entry_price = self.market_data.get_price(symbol)
        if entry_price is None or entry_price <= 0:
            logger.warning(f"[{symbol}] ⚠️ لا يوجد سعر حي — تخطي الصفقة")
            return (evidence, None, None)

        logger.info(
            f"[{symbol}] 🟢 إشارة {evidence.decision} | "
            f"الثقة: {evidence.final_score:.0f}% | "
            f"رأس المال: {capital_allocated:.2f} | "
            f"نسبة المخاطرة: {risk_per_trade}%"
        )

        risk_decision: Optional[RiskDecision] = None
        try:
            risk_decision = await self.risk_engine.evaluate(
                evidence, entry_price=entry_price,
                capital=capital_allocated, risk_percentage=risk_per_trade,
            )
        except Exception as e:
            logger.error(f"[{symbol}] ❌ خطأ في تقييم المخاطر: {e}")
            return (evidence, None, None)

        if not risk_decision.trade_allowed:
            reason = risk_decision.blocking_reason or "تقييم المخاطر"
            self._signals_rejected += 1
            self._rejection_reasons[reason] = self._rejection_reasons.get(reason, 0) + 1
            logger.info(
                f"[{symbol}] ⛔ تم رفض الصفقة: {reason} "
                f"(مستوى الخطر: {risk_decision.risk_level})"
            )
            return (evidence, risk_decision, None)

        # e. التنفيذ
        strategy_name = all_signals[0].strategy_name if all_signals else "غير معروف"
        position_size = risk_decision.position_size

        if position_size <= 0:
            logger.warning(f"[{symbol}] ⚠️ حجم المركز = 0 — تخطي التنفيذ")
            return (evidence, risk_decision, None)

        logger.info(
            f"[{symbol}] 💰 حساب حجم الصفقة: "
            f"الكمية = {position_size:.6f} | "
            f"الخسارة القصوى = {risk_decision.max_loss:.2f} | "
            f"وقف الخسارة = {risk_decision.stop_loss_distance:.4f}"
        )

        execution: Optional[ExecutionResult] = None
        try:
            execution = await self.execution.execute(
                risk_decision, symbol=symbol, entry_price=entry_price,
                strategy=strategy_name, telegram_id=telegram_id,
                entry_reason=evidence.reasoning,
            )
        except Exception as e:
            logger.error(f"[{symbol}] ❌ خطأ في التنفيذ: {e}", exc_info=True)
            return (evidence, risk_decision, None)

        if execution and execution.status == "FILLED":
            logger.info(
                f"[{symbol}] ✅ تنفيذ أمر {evidence.decision} | "
                f"السعر: {execution.entry_price:.2f} | "
                f"الكمية: {execution.executed_quantity:.6f} | "
                f"الاستراتيجية: {strategy_name}"
            )
        else:
            logger.warning(
                f"[{symbol}] ⚠️ فشل التنفيذ: {execution.status if execution else 'لا نتيجة'}"
            )

        self._last_cycle_duration = (datetime.utcnow() - cycle_start).total_seconds()
        return (evidence, risk_decision, execution)

    def get_status(self) -> dict:
        return {
            "evidence_decisions": self.evidence.decision_count,
            "risk_blocked": self.risk_engine._trading_blocked,
            "execution_metrics": self.execution.get_metrics(),
            "signals_processed": self._signals_processed,
            "signals_rejected": self._signals_rejected,
            "rejection_reasons": dict(self._rejection_reasons),
            "last_cycle_duration": round(self._last_cycle_duration, 2),
        }
