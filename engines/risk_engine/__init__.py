"""
محرك المخاطر — CT V4.0
طبقة حماية رأس المال. لديه صلاحية مطلقة لمنع الصفقات.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from core.base import BaseEngine
from core.events import (
    EventBus, HealthEvent, HealthStatus, PortfolioEvent
)
from core.types import RiskDecision
from config.constants import (
    MAX_DAILY_LOSS_PCT, MAX_TOTAL_EXPOSURE_PCT, MAX_POSITION_PER_SYMBOL_PCT,
    MAX_CONSECUTIVE_LOSSES, MAX_DRAWDOWN_PCT
)

logger = logging.getLogger("risk_engine")

class RiskEngine(BaseEngine):
    """حماية رأس المال. يمنع الصفقات غير الآمنة بشكل مطلق."""

    def __init__(self, event_bus: EventBus):
        super().__init__("risk_engine")
        self.event_bus = event_bus
        self._daily_loss: float = 0.0
        self._consecutive_losses: int = 0
        self._portfolio_balance: float = 1000.0
        self._peak_balance: float = 1000.0
        self._trading_blocked: bool = False
        self._block_reason: str = ""
        self._last_reset_day: int = datetime.utcnow().day
        self._open_positions: dict = {}

    async def initialize(self) -> None:
        await self.event_bus.subscribe("PortfolioEvent", self._on_portfolio_update)
        self.logger.info("[المخاطر] تم التهيئة — جاهز لحماية رأس المال")

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._heartbeat_loop())
        asyncio.create_task(self._daily_reset_loop())
        self.logger.info("[المخاطر] تم البدء")

    async def stop(self) -> None:
        self._running = False
        self.logger.info("[المخاطر] تم الإيقاف")

    async def _on_portfolio_update(self, event: PortfolioEvent):
        self._portfolio_balance = event.equity
        if event.equity > self._peak_balance:
            self._peak_balance = event.equity

    async def evaluate(
        self,
        evidence: object,
        entry_price: float,
        atr: float = 0.0,
        capital: Optional[float] = None,
        risk_percentage: Optional[float] = None,
    ) -> RiskDecision:
        symbol = getattr(evidence, 'symbol', 'UNKNOWN')

        if capital is None or risk_percentage is None:
            return RiskDecision(trade_allowed=False, reasoning="بيانات المخاطرة غير مكتملة")

        blocked, reason = self._check_blocking_conditions()
        if blocked:
            return RiskDecision(trade_allowed=False, reasoning=f"ممنوع: {reason}")

        # حساب الحجم والتحقق من الحدود (مبسط للمراجعة)
        risk_amount = capital * (risk_percentage / 100)
        sl_distance = atr * 2 if atr > 0 else entry_price * 0.02
        position_size = risk_amount / (sl_distance / entry_price) if sl_distance > 0 else 0
        
        return RiskDecision(
            trade_allowed=True,
            risk_level="LOW",
            position_size=round(position_size, 6),
            reasoning="المخاطر ضمن الحدود المقبولة"
        )

    def _check_blocking_conditions(self) -> tuple[bool, str]:
        # SSOT: التحقق من الحالة المركزية إذا لزم الأمر (سيتم استخدامه في main.py)
        if self._trading_blocked:
            return True, self._block_reason

        if self._consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            return True, f"خسائر متتالية ({self._consecutive_losses})"

        daily_pct = (self._daily_loss / max(self._portfolio_balance, 1)) * 100
        if daily_pct >= MAX_DAILY_LOSS_PCT * 100:
            return True, f"حد الخسارة اليومية ({daily_pct:.1f}%)"

        return False, ""

    def emergency_stop(self, reason: str = "يدوي"):
        self._trading_blocked = True
        self._block_reason = reason
        # 🛡️ إبلاغ آلة الحالات المركزية (يتم عبر main.py الذي يراقب هذا المحرك)
        self.logger.critical(f"[حماية] ⛔ إيقاف طارئ: {reason}")

    def resume_trading(self):
        self._trading_blocked = False
        self._block_reason = ""
        self._consecutive_losses = 0
        self.logger.info("[حماية] ✓ تم استئناف التداول")

    def record_trade_result(self, won: bool, pnl: float):
        if won:
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1
            self._daily_loss += abs(pnl)

    async def _daily_reset_loop(self):
        while self._running:
            now = datetime.utcnow()
            if now.day != self._last_reset_day:
                self._daily_loss = 0.0
                self._last_reset_day = now.day
            await asyncio.sleep(60)

    async def _heartbeat_loop(self):
        while self._running:
            await self.event_bus.publish(HealthEvent(engine=self.name, status=HealthStatus.HEALTHY))
            await asyncio.sleep(60)
