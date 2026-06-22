"""
FinalDecisionArbiter — Last Firewall
آخر طبقة حماية قبل التنفيذ. ترفض أي قرار إذا أي طبقة غير متطابقة.
حتى لو 5 طبقات وافقت — إذا Arbiter رفض → NO TRADE.
"""
import logging
from typing import Optional
from core.trade_decision import TradeDecision

logger = logging.getLogger("arbiter")


class FinalDecisionArbiter:
    """حكم نهائي — يفحص كل طبقات النظام قبل السماح بالتنفيذ."""

    # ══ حدود صارمة ══
    MIN_CONFIDENCE = 60.0
    MIN_RISK_SCORE = 50.0
    MAX_POSITION_PCT = 25.0       # أقصى نسبة من رأس المال لمركز واحد
    MAX_CONSECUTIVE_LOSSES = 5
    MIN_EVIDENCE_SCORE = 40.0

    def __init__(self):
        self._rejected_count: int = 0
        self._approved_count: int = 0
        self._last_rejection_reason: str = ""

    def arbitrate(self, decision: TradeDecision,
                  state: Optional[object] = None) -> TradeDecision:
        """
        فحص نهائي لكل طبقات القرار.
        يُرجع نفس الكائن مع final_approval=True/False.
        """

        # ══ 1. التحقق من وجود القرار ══
        if not decision:
            decision = TradeDecision()
            decision.reject("لا يوجد قرار", "ARBITER")
            self._rejected_count += 1
            return decision

        # ══ 2. التحقق من حالة النظام ══
        if state is not None:
            if not getattr(state, 'trading_allowed', False):
                decision.reject(
                    f"التداول غير مسموح — المرحلة={getattr(state, 'phase', '?')}",
                    "ARBITER"
                )
                self._rejected_count += 1
                self._last_rejection_reason = decision.blocked_reason
                return decision

            if not getattr(state, 'ws_connected', False):
                decision.reject("WebSocket منفصل", "ARBITER")
                self._rejected_count += 1
                self._last_rejection_reason = decision.blocked_reason
                return decision

        # ══ 3. التحقق من الأعلام ══
        required_flags = [
            "strategy_valid",
            "confidence_met",
            "risk_accepted",
            "state_allows_trading",
            "ws_healthy",
        ]
        for flag in required_flags:
            if not decision.flags.get(flag, False):
                decision.reject(f"علم مطلوب غير محقق: {flag}", "ARBITER")
                self._rejected_count += 1
                self._last_rejection_reason = decision.blocked_reason
                return decision

        # ══ 4. التحقق من الاتجاه ══
        if decision.direction not in ("BUY", "SELL"):
            decision.reject(
                f"اتجاه غير صالح: {decision.direction}", "ARBITER"
            )
            self._rejected_count += 1
            self._last_rejection_reason = decision.blocked_reason
            return decision

        # ══ 5. التحقق من الثقة ══
        if decision.confidence < self.MIN_CONFIDENCE:
            decision.reject(
                f"ثقة منخفضة: {decision.confidence:.0f}% < {self.MIN_CONFIDENCE}%",
                "ARBITER"
            )
            self._rejected_count += 1
            self._last_rejection_reason = decision.blocked_reason
            return decision

        # ══ 6. التحقق من الأدلة ══
        if decision.evidence_score < self.MIN_EVIDENCE_SCORE:
            decision.reject(
                f"درجة أدلة منخفضة: {decision.evidence_score:.0f} < {self.MIN_EVIDENCE_SCORE}",
                "ARBITER"
            )
            self._rejected_count += 1
            self._last_rejection_reason = decision.blocked_reason
            return decision

        # ══ 7. التحقق من المخاطر ══
        if not decision.risk_allowed:
            decision.reject(
                f"المخاطر غير مقبولة — risk_score={decision.risk_score:.0f}",
                "ARBITER"
            )
            self._rejected_count += 1
            self._last_rejection_reason = decision.blocked_reason
            return decision

        if decision.risk_score < self.MIN_RISK_SCORE:
            decision.reject(
                f"درجة مخاطر منخفضة: {decision.risk_score:.0f} < {self.MIN_RISK_SCORE}",
                "ARBITER"
            )
            self._rejected_count += 1
            self._last_rejection_reason = decision.blocked_reason
            return decision

        # ══ 8. التحقق من حجم المركز ══
        if decision.position_size_pct > self.MAX_POSITION_PCT:
            decision.reject(
                f"حجم المركز كبير جداً: {decision.position_size_pct:.1f}% > {self.MAX_POSITION_PCT}%",
                "ARBITER"
            )
            self._rejected_count += 1
            self._last_rejection_reason = decision.blocked_reason
            return decision

        # ══ 9. التحقق من نظام السوق ══
        if decision.regime == "CHOPPY":
            decision.reject("نظام سوق عشوائي (CHOPPY)", "ARBITER")
            self._rejected_count += 1
            self._last_rejection_reason = decision.blocked_reason
            return decision

        # ══ 10. تحقق من التناقض ══
        if decision.direction == "BUY" and decision.trend_direction == "DOWN":
            decision.reject("BUY ضد اتجاه هابط", "ARBITER")
            self._rejected_count += 1
            self._last_rejection_reason = decision.blocked_reason
            return decision

        if decision.direction == "SELL" and decision.trend_direction == "UP":
            decision.reject("SELL ضد اتجاه صاعد", "ARBITER")
            self._rejected_count += 1
            self._last_rejection_reason = decision.blocked_reason
            return decision

        # ══ موافقة نهائية ══
        decision.approve()
        decision.arbiter_decision = "APPROVED"
        self._approved_count += 1
        logger.info(
            f"[محكم] ✅ قرار #{self._approved_count}: {decision.symbol} | "
            f"{decision.direction} | ثقة={decision.confidence:.0f}% | "
            f"مخاطر={decision.risk_score:.0f}"
        )
        return decision

    def get_stats(self) -> dict:
        return {
            "approved": self._approved_count,
            "rejected": self._rejected_count,
            "last_rejection": self._last_rejection_reason,
        }
