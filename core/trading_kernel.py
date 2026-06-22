"""
TradingKernel — Central SSOT (Single Source of Truth)
النواة المركزية للنظام. أي صفقة تمر من هنا أو لا تمر.
يجمع: State Machine → Strategies → Evidence → Risk → Arbiter → Execution.
"""
import logging
import uuid
from datetime import datetime
from typing import Optional

from core.trade_decision import TradeDecision
from core.arbiter import FinalDecisionArbiter
from core.types import MarketAnalysis, RiskDecision, EvidenceResult

logger = logging.getLogger("trading_kernel")


class TradingKernel:
    """
    نواة التداول المركزية — المصدر الوحيد لتنفيذ الصفقات.
    لا يُسمح بأي تنفيذ خارج هذه النواة.
    """

    def __init__(self):
        self.arbiter = FinalDecisionArbiter()
        self._total_decisions: int = 0
        self._decisions_today: int = 0

    # ═══════════════════════════════════════════════════════
    #  API الرئيسي — المدخل الوحيد للتداول
    # ═══════════════════════════════════════════════════════

    async def process_signal(
        self,
        symbol: str,
        analysis: MarketAnalysis,
        evidence: EvidenceResult,
        risk: Optional[RiskDecision],
        strategy_name: str = "",
        strategy_version: str = "1.0.0",
        entry_price: float = 0.0,
        capital_allocated: float = 0.0,
        state: Optional[object] = None,
    ) -> TradeDecision:
        """
        المسار الكامل لقرار التداول.
        كل الطبقات تُفحص في مسار واحد غير قابل للتجاوز.
        """

        # ══ 1. بناء كائن القرار ══
        decision = TradeDecision(
            decision_id=str(uuid.uuid4())[:12],
            symbol=symbol,
            created_at=datetime.utcnow().isoformat(),
            direction=evidence.decision,
            confidence=evidence.final_score,
            entry_price=entry_price,
            strategy_name=strategy_name,
            strategy_version=strategy_version,
            strategy_confidence=evidence.final_score,
            entry_reason=evidence.reasoning,
            evidence_score=evidence.final_score,
            evidence_decision=evidence.decision,
            evidence_conflicts=evidence.conflicts,
            regime=getattr(analysis, 'regime', 'UNKNOWN'),
            trend_direction=getattr(analysis, 'trend_direction', 'NONE'),
        )

        # ══ 2. التحقق من الاستراتيجية ══
        if strategy_name and strategy_name != "غير معروف":
            decision.flags["strategy_valid"] = True

        # ══ 3. التحقق من البيانات ══
        if entry_price > 0 and getattr(analysis, 'current_price', 0) > 0:
            decision.flags["data_sufficient"] = True

        # ══ 4. التحقق من نظام السوق ══
        regime = getattr(analysis, 'regime', 'UNKNOWN')
        if regime in ("TRENDING",):
            decision.flags["regime_valid"] = True

        # ══ 5. التحقق من الثقة ══
        if evidence.final_score >= 50:
            decision.flags["confidence_met"] = True

        # ══ 6. التحقق من المخاطر ══
        if risk is not None:
            decision.risk_allowed = risk.trade_allowed
            decision.risk_level = risk.risk_level if hasattr(risk, 'risk_level') else "UNKNOWN"
            decision.risk_score = 80.0 if risk.trade_allowed else risk.trade_allowed * 100  # simplified
            decision.max_loss = risk.max_loss if hasattr(risk, 'max_loss') else 0.0
            decision.stop_loss = risk.stop_loss if hasattr(risk, 'stop_loss') else 0.0
            decision.take_profit = risk.take_profit if hasattr(risk, 'take_profit') else 0.0
            decision.position_size_pct = (
                (risk.position_size * entry_price / max(capital_allocated, 1)) * 100
                if hasattr(risk, 'position_size') and capital_allocated > 0
                else 0.0
            )

            if risk.trade_allowed:
                decision.flags["risk_accepted"] = True
        else:
            decision.reject("لم يتم تقييم المخاطر", "KERNEL")
            return decision

        # ══ 7. التحقق من حالة النظام ══
        if state is not None:
            if getattr(state, 'trading_allowed', False):
                decision.flags["state_allows_trading"] = True
            if getattr(state, 'ws_connected', False):
                decision.flags["ws_healthy"] = True

        # ══ 8. التحكيم النهائي ══
        decision = self.arbiter.arbitrate(decision, state)

        self._total_decisions += 1
        self._decisions_today += 1

        if decision.is_approved:
            logger.info(
                f"[نواة] ✅ قرار معتمد: {decision.symbol} | "
                f"{decision.direction} | ثقة={decision.confidence:.0f}% | "
                f"ID={decision.decision_id}"
            )
        else:
            logger.info(
                f"[نواة] ❌ قرار مرفوض: {decision.symbol} | "
                f"السبب: {decision.blocked_reason}"
            )

        return decision

    # ═══════════════════════════════════════════════════════
    #  المقاييس
    # ═══════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        return {
            "total_decisions": self._total_decisions,
            "decisions_today": self._decisions_today,
            "arbiter": self.arbiter.get_stats(),
        }
