"""
Kill Switch — Emergency Stop Mechanism
إيقاف فوري لكل التداولات عند تجاوز أي حد.
لا استثناءات. لا bypass.
"""
import logging
from datetime import datetime

logger = logging.getLogger("kill_switch")


class KillSwitch:
    """
    مفتاح إيقاف طارئ.
    يُفعّل عند تجاوز أي من الحدود الحرجة.
    """

    # حدود صارمة
    MAX_CONSECUTIVE_LOSSES = 5
    MAX_DAILY_LOSS_PCT = 5.0        # %5 من رأس المال
    MAX_DRAWDOWN_PCT = 15.0         # %15 من الذروة
    MAX_VOLATILITY_SPIKE = 80       # تقلب > 80%
    MAX_DATA_GAP_SEC = 120          # انقطاع بيانات > 2 دقيقة

    def __init__(self):
        self.activated: bool = False
        self.reason: str = ""
        self.activated_at: str = ""
        self._daily_pnl: float = 0.0
        self._peak_balance: float = 0.0
        self._current_balance: float = 0.0
        self._consecutive_losses: int = 0
        self._last_data_ts: float = 0.0

    def evaluate(self,
                 consecutive_losses: int,
                 daily_pnl: float,
                 current_balance: float,
                 peak_balance: float,
                 volatility: float = 0.0,
                 data_age_sec: float = 0.0) -> tuple[bool, str]:
        """
        تقييم كل شروط القتل.
        يُرجع (should_kill, reason).
        """

        if self.activated:
            return True, self.reason  # مفتوح بالفعل

        # 1. خسائر متتالية
        if consecutive_losses >= self.MAX_CONSECUTIVE_LOSSES:
            return self._activate(f"خسائر متتالية: {consecutive_losses} ≥ {self.MAX_CONSECUTIVE_LOSSES}")

        # 2. خسارة يومية
        if current_balance > 0 and peak_balance > 0:
            daily_loss_pct = abs(daily_pnl) / peak_balance * 100
            if daily_loss_pct >= self.MAX_DAILY_LOSS_PCT:
                return self._activate(f"خسارة يومية: {daily_loss_pct:.1f}% ≥ {self.MAX_DAILY_LOSS_PCT}%")

        # 3. انخفاض من الذروة
        if peak_balance > 0 and current_balance > 0:
            drawdown = (peak_balance - current_balance) / peak_balance * 100
            if drawdown >= self.MAX_DRAWDOWN_PCT:
                return self._activate(f"انخفاض: {drawdown:.1f}% ≥ {self.MAX_DRAWDOWN_PCT}%")

        # 4. ارتفاع حاد في التقلب
        if volatility >= self.MAX_VOLATILITY_SPIKE:
            return self._activate(f"تقلب حاد: {volatility:.0f}% ≥ {self.MAX_VOLATILITY_SPIKE}%")

        # 5. انقطاع بيانات
        if data_age_sec >= self.MAX_DATA_GAP_SEC and data_age_sec > 0:
            return self._activate(f"انقطاع بيانات: {data_age_sec:.0f}ث ≥ {self.MAX_DATA_GAP_SEC}ث")

        return False, ""

    def _activate(self, reason: str) -> tuple[bool, str]:
        """تفعيل القفل — لا رجعة فيه حتى إعادة التعيين اليدوي."""
        self.activated = True
        self.reason = reason
        self.activated_at = datetime.utcnow().isoformat()
        logger.critical(f"🔴 [KILL SWITCH] مُفعّل: {reason}")
        return True, reason

    def reset(self):
        """إعادة تعيين القفل (يدوياً فقط)."""
        self.activated = False
        self.reason = ""
        self.activated_at = ""
        self._consecutive_losses = 0
        logger.info("🟢 [KILL SWITCH] تم إعادة التعيين")

    @property
    def is_active(self) -> bool:
        return self.activated
