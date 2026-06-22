"""
خدمة المخاطر — تنسيق عمليات إدارة المخاطر.
V4.0: إيقاف طارئ، استئناف، تقرير الحالة.
"""
import logging

from engines.risk_engine import RiskEngine

logger = logging.getLogger("مخاطر_الخدمة")


class RiskService:
    """تنسيق إدارة المخاطر — واجهة للتحكم اليدوي."""

    def __init__(self, risk_engine: RiskEngine):
        self.risk_engine = risk_engine

    def emergency_stop(self, reason: str = "إيقاف يدوي من تيليجرام"):
        """
        إيقاف طارئ لجميع عمليات التداول.

        المعاملات:
            reason: سبب الإيقاف (للتدقيق).
        """
        self.risk_engine.emergency_stop(reason)
        logger.critical(f"[مخاطر] 🛑 إيقاف طارئ: {reason}")

    def resume_trading(self):
        """استئناف التداول بعد الإيقاف الطارئ."""
        self.risk_engine.resume_trading()
        logger.info("[مخاطر] ✅ تم استئناف التداول")

    def get_risk_status(self) -> dict:
        """تقرير حالة المخاطر الحالية."""
        status = self.risk_engine.get_status()
        logger.debug(
            f"[مخاطر] الحالة: محظور={status.get('trading_blocked')} | "
            f"خسائر متتالية={status.get('consecutive_losses')} | "
            f"خسارة يومية={status.get('daily_loss', 0):.2f}"
        )
        return status

    def is_trading_allowed(self) -> bool:
        """هل التداول مسموح حالياً؟"""
        allowed = not self.risk_engine._trading_blocked
        if not allowed:
            logger.warning(
                f"[مخاطر] ⛔ التداول محظور: {self.risk_engine._block_reason}"
            )
        return allowed

    def record_trade_result(self, won: bool, pnl: float):
        """
        تسجيل نتيجة صفقة — لتحديث عداد الخسائر المتتالية.

        المعاملات:
            won: هل الصفقة رابحة؟
            pnl: قيمة الربح/الخسارة.
        """
        self.risk_engine.record_trade_result(won, pnl)
        if won:
            logger.info(f"[مخاطر] ✅ صفقة رابحة: +{pnl:.2f} — إعادة تعيين عداد الخسائر")
        else:
            logger.warning(f"[مخاطر] ❌ صفقة خاسرة: {pnl:.2f} — زيادة عداد الخسائر")
