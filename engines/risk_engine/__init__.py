"""
محرك المخاطر — CT V4.0
طبقة حماية رأس المال. لديه صلاحية مطلقة لمنع الصفقات، تقليل الحجم،
وإيقاف التداول. لا يتنبأ بالسوق — يحمي المحفظة فقط.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

from core.base import BaseEngine
from core.events import (
    EvidenceEvent, RiskEvent, EventBus, HealthEvent, HealthStatus,
    AlertEvent, AlertLevel, PortfolioEvent
)
from core.types import EvidenceResult, RiskDecision, RiskLevel as RiskLevelEnum
from core.errors import RiskError
from config.constants import (
    MAX_RISK_PER_TRADE, MAX_POSITION_PER_SYMBOL_PCT,
    MAX_TOTAL_EXPOSURE_PCT, MAX_DAILY_LOSS_PCT,
    MAX_WEEKLY_LOSS_PCT, MAX_CONSECUTIVE_LOSSES,
    MAX_DRAWDOWN_PCT, VOLATILITY_RISK_MAP,
)

logger = logging.getLogger("risk_engine")


class RiskEngine(BaseEngine):
    """حماية رأس المال. يمنع الصفقات غير الآمنة بشكل مطلق."""

    def __init__(self, event_bus: EventBus):
        super().__init__("risk_engine")
        self.event_bus = event_bus
        self._daily_loss: float = 0.0
        self._weekly_loss: float = 0.0
        self._total_exposure: float = 0.0
        self._consecutive_losses: int = 0
        self._open_positions: dict[str, dict] = {}
        self._last_reset_day: int = datetime.utcnow().day
        self._portfolio_balance: float = 1000.0
        self._peak_balance: float = 1000.0
        self._trading_blocked: bool = False
        self._block_reason: str = ""
        self._interest_areas: dict[str, int] = {}
        self._interest_percentages: dict[str, int] = {}
        self._daily_drawdown: float = 0.0

    async def initialize(self) -> None:
        await self.event_bus.subscribe("EvidenceEvent", self._on_evidence)
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
        """تتبع حالة المحفظة لحسابات المخاطر."""
        old_balance = self._portfolio_balance
        self._portfolio_balance = event.equity
        if event.equity > self._peak_balance:
            self._peak_balance = event.equity

    async def _on_evidence(self, event: EvidenceEvent):
        """تقييم المخاطر عند وجود أدلة."""
        if not self._running:
            return

        # لا يمكننا حساب المركز بدون رأس مال — نحتاجه من الطبقة العليا
        # هنا ننشر حدث مخاطر تحذيري فقط
        self.logger.warning(
            f"[المخاطر] تم استلام حدث أدلة لـ {event.symbol} — "
            f"ينتظر تقييم المخاطر مع رأس المال المخصص"
        )

    # ── Core Evaluation ────────────────────────────────────────

    async def evaluate(
        self,
        evidence: EvidenceResult,
        entry_price: float,
        atr: float = 0.0,
        capital: Optional[float] = None,
        risk_percentage: Optional[float] = None,
    ) -> RiskDecision:
        """
        تقييم المخاطر الأساسي. لا توجد قيم افتراضية لرأس المال!
        يُرجع RiskDecision: موافقة، تقليص، أو منع.

        Args:
            evidence: نتيجة محرك الأدلة
            entry_price: سعر الدخول
            atr: متوسط المدى الحقيقي (اختياري)
            capital: رأس المال المخصص من العملة (إجباري — لا قيمة افتراضية)
            risk_percentage: نسبة المخاطرة (إجباري — لا قيمة افتراضية)

        Returns:
            RiskDecision بالقرار النهائي
        """
        symbol = evidence.symbol

        # ── لا قيم افتراضية لرأس المال ──
        if capital is None:
            self.logger.error(
                f"[المخاطر] {symbol} — رأس المال غير محدد! لا يمكن حساب حجم الصفقة"
            )
            return RiskDecision(
                trade_allowed=False,
                risk_level="EXTREME",
                position_size=0.0,
                max_loss=0.0,
                reasoning="رأس المال غير محدد — لا يمكن تقييم المخاطر",
                blocking_reason="رأس المال غير محدد",
            )

        if risk_percentage is None:
            self.logger.error(
                f"[المخاطر] {symbol} — نسبة المخاطرة غير محددة!"
            )
            return RiskDecision(
                trade_allowed=False,
                risk_level="EXTREME",
                position_size=0.0,
                max_loss=0.0,
                reasoning="نسبة المخاطرة غير محددة — لا يمكن تقييم المخاطر",
                blocking_reason="نسبة المخاطرة غير محددة",
            )

        # ── فحص شروط المنع أولاً ──
        blocked, reason = self._check_blocking_conditions(symbol)
        if blocked and evidence.decision != "SELL":
            self.logger.warning(f"[حماية] {symbol} — ممنوع: {reason}")
            return RiskDecision(
                trade_allowed=False,
                risk_level="EXTREME",
                position_size=0.0,
                max_loss=0.0,
                reasoning=f"ممنوع: {reason}",
                blocking_reason=reason,
            )

        # ── فحص حد الخسارة اليومية ──
        daily_loss_pct = (self._daily_loss / max(self._portfolio_balance, 1)) * 100
        if daily_loss_pct >= MAX_DAILY_LOSS_PCT * 100:
            self.logger.critical(
                f"[حماية] حد الخسارة اليومية تم تجاوزه: {daily_loss_pct:.1f}%"
            )
            return RiskDecision(
                trade_allowed=False,
                risk_level="EXTREME",
                position_size=0.0,
                max_loss=0.0,
                reasoning=f"تم تجاوز حد الخسارة اليومية ({daily_loss_pct:.1f}%)",
                blocking_reason="حد الخسارة اليومية",
            )

        # ── حساب حجم الصفقة بناءً على رأس المال المخصص ──
        volatility = evidence.evidence.get("momentum", 50)
        vol_mult = self._get_volatility_multiplier(volatility)

        # مبلغ المخاطرة = رأس المال × نسبة المخاطرة × مضاعف التقلب
        risk_amount = capital * (risk_percentage / 100) * vol_mult

        # مسافة وقف الخسارة
        if atr > 0:
            sl_distance = atr * 2
        elif entry_price > 0:
            sl_distance = entry_price * 0.015  # 1.5% من السعر
        else:
            sl_distance = 0.0

        # حجم المركز = مبلغ المخاطرة ÷ (مسافة الوقف / السعر)
        if sl_distance > 0 and entry_price > 0:
            position_size = risk_amount / (sl_distance / entry_price)
        else:
            position_size = risk_amount / max(entry_price, 0.0001) * 0.1  # تقدير

        if position_size <= 0:
            self.logger.warning(f"[المخاطر] {symbol} — حجم الصفقة غير صالح (position_size={position_size})")
            return RiskDecision(
                trade_allowed=False,
                risk_level="HIGH",
                position_size=0.0,
                max_loss=risk_amount,
                reasoning="حجم الصفقة غير صالح — تحقق من سعر الدخول ورأس المال",
                blocking_reason="حجم الصفقة غير صالح",
            )

        # ── سقف التعرض للرمز الواحد ──
        max_symbol_exposure = self._portfolio_balance * MAX_POSITION_PER_SYMBOL_PCT
        if position_size * entry_price > max_symbol_exposure:
            old_size = position_size
            position_size = max_symbol_exposure / entry_price
            self.logger.info(
                f"[حماية] {symbol} — تقليص حجم الصفقة من {old_size:.6f} إلى {position_size:.6f} "
                f"(حد التعرض للرمز: {max_symbol_exposure:.2f})"
            )

        # ── سقف التعرض الكلي ──
        max_total = self._portfolio_balance * MAX_TOTAL_EXPOSURE_PCT
        current_exposure = sum(p.get("exposure", 0) for p in self._open_positions.values())
        if current_exposure + position_size * entry_price > max_total:
            if current_exposure >= max_total:
                self.logger.warning(
                    f"[حماية] {symbol} — تم الوصول للحد الأقصى للتعرض الكلي"
                )
                return RiskDecision(
                    trade_allowed=False,
                    risk_level="HIGH",
                    position_size=0.0,
                    max_loss=risk_amount,
                    reasoning="تم الوصول للحد الأقصى للتعرض الكلي",
                    blocking_reason="الحد الأقصى للتعرض",
                )
            position_size = (max_total - current_exposure) / entry_price
            self.logger.info(
                f"[حماية] {symbol} — تقليص حجم الصفقة ليبقى ضمن التعرض الكلي: {position_size:.6f}"
            )

        # ── تحديد مستوى المخاطرة ──
        risk_level = self._determine_risk_level(volatility, position_size, capital)

        # ── نسبة جني الأرباح ──
        tp_ratio = 2.0 if risk_level in ("LOW", "MEDIUM") else 1.5

        reasoning = (
            f"رأس المال المخصص: {capital:.2f} | "
            f"مبلغ المخاطرة: {risk_amount:.2f} | "
            f"حجم المركز: {position_size:.6f} | "
            f"مستوى المخاطرة: {risk_level} | "
            f"وقف الخسارة: {sl_distance:.6f} | "
            f"جني الأرباح: ×{tp_ratio}"
        )

        self.logger.info(
            f"[المخاطر] {symbol} — موافقة | {reasoning}"
        )

        return RiskDecision(
            trade_allowed=True,
            risk_level=risk_level,
            position_size=round(position_size, 6),
            max_loss=round(risk_amount, 2),
            stop_loss_distance=round(sl_distance, 6),
            take_profit_ratio=tp_ratio,
            reasoning=reasoning,
        )

    # ── Blocking Conditions ───────────────────────────────────

    def _check_blocking_conditions(self, symbol: str) -> tuple[bool, str]:
        """فحص كل شروط المنع."""
        if self._trading_blocked:
            return True, self._block_reason

        if self._consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            self._trading_blocked = True
            self._block_reason = f"خسائر متتالية ({self._consecutive_losses}) — تم إيقاف التداول"
            self.logger.critical(f"[حماية] {self._block_reason}")
            return True, self._block_reason

        daily_pct = (self._daily_loss / max(self._portfolio_balance, 1)) * 100
        if daily_pct >= MAX_DAILY_LOSS_PCT * 100:
            self._trading_blocked = True
            self._block_reason = f"حد الخسارة اليومية ({daily_pct:.1f}%)"
            self.logger.critical(f"[حماية] {self._block_reason}")
            return True, self._block_reason

        # فحص الانخفاض من الذروة
        if self._peak_balance > 0:
            dd_pct = (self._peak_balance - self._portfolio_balance) / self._peak_balance * 100
            if dd_pct >= MAX_DRAWDOWN_PCT * 100:
                self._trading_blocked = True
                self._block_reason = f"أقصى انخفاض ({dd_pct:.1f}%)"
                self.logger.critical(f"[حماية] {self._block_reason}")
                return True, self._block_reason

        return False, ""

    # ── Helpers ──────────────────────────────────────────────

    def _get_volatility_multiplier(self, volatility: float) -> float:
        """مضاعف التقلب — يقلل حجم الصفقة في الأسواق المتقلبة."""
        if volatility < 30:
            mult = VOLATILITY_RISK_MAP.get("LOW", 1.0)
        elif volatility < 60:
            mult = VOLATILITY_RISK_MAP.get("MEDIUM", 0.8)
        elif volatility < 80:
            mult = VOLATILITY_RISK_MAP.get("HIGH", 0.5)
        else:
            mult = VOLATILITY_RISK_MAP.get("EXTREME", 0.0)

        if mult < 1.0:
            self.logger.debug(
                f"[المخاطر] مضاعف التقلب: {mult} (تقلب={volatility:.0f})"
            )
        return mult

    def _determine_risk_level(
        self, volatility: float, position_size: float, capital: float
    ) -> str:
        """تحديد مستوى المخاطرة بناءً على عدة عوامل."""
        exposure_pct = (position_size * 0) / max(capital, 1)  # مبسط

        if volatility > 80 or self._consecutive_losses >= 3:
            return "HIGH"
        if volatility > 60 or self._consecutive_losses >= 1:
            return "MEDIUM"
        return "LOW"

    # ── State Management ─────────────────────────────────────

    def record_trade_result(self, won: bool, pnl: float):
        """تحديث حالة المخاطرة بعد إغلاق الصفقة."""
        if won:
            if self._consecutive_losses > 0:
                self.logger.info(
                    f"[المخاطر] صفقة رابحة — إعادة تعيين عداد الخسائر (كان {self._consecutive_losses})"
                )
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1
            self._daily_loss += abs(pnl)
            self._weekly_loss += abs(pnl)
            self.logger.warning(
                f"[المخاطر] صفقة خاسرة ({pnl:.2f}) — "
                f"الخسائر المتتالية: {self._consecutive_losses}/{MAX_CONSECUTIVE_LOSSES}"
            )

    def emergency_stop(self, reason: str = "يدوي"):
        """إيقاف فوري لكل التداولات."""
        self._trading_blocked = True
        self._block_reason = reason
        self.logger.critical(f"[حماية] ⛔ إيقاف طارئ: {reason}")

    def resume_trading(self):
        """استئناف التداول بعد الإيقاف الطارئ."""
        self._trading_blocked = False
        self._block_reason = ""
        self._consecutive_losses = 0
        self.logger.info("[حماية] ✓ تم استئناف التداول")

    def update_capital(self, balance: float):
        """تحديث رصيد المحفظة."""
        self._portfolio_balance = balance

    def get_status(self) -> dict:
        """حالة المحرك الحالية."""
        return {
            "trading_blocked": self._trading_blocked,
            "block_reason": self._block_reason,
            "consecutive_losses": self._consecutive_losses,
            "daily_loss": round(self._daily_loss, 2),
            "weekly_loss": round(self._weekly_loss, 2),
            "total_exposure": round(self._total_exposure, 2),
            "open_positions": len(self._open_positions),
            "portfolio_balance": round(self._portfolio_balance, 2),
            "peak_balance": round(self._peak_balance, 2),
        }

    async def _daily_reset_loop(self):
        """إعادة تعيين العدادات اليومية عند منتصف الليل."""
        while self._running:
            now = datetime.utcnow()
            if now.day != self._last_reset_day:
                self._daily_loss = 0.0
                self._daily_drawdown = 0.0
                self._last_reset_day = now.day
                if now.weekday() == 0:  # الاثنين
                    self._weekly_loss = 0.0
                self.logger.info("[المخاطر] تم إعادة تعيين عدادات المخاطرة اليومية")
            await asyncio.sleep(60)

    async def _heartbeat_loop(self):
        while self._running:
            await self.event_bus.publish(HealthEvent(
                engine=self.name,
                status=HealthStatus.HEALTHY,
                latency_ms=0,
                error_rate=0,
            ))
            await asyncio.sleep(5)
