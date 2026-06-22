"""
محرك المحفظة — يتتبع المحفظة الافتراضية بدون أموال حقيقية أو اتصال بمنصة.
جميع reads/writes عبر Repository layer.

الحسابات الأساسية:
  قيمة المحفظة = مجموع (رأس المال المخصص لكل عملة + الربح/الخسارة)
  الحقوق = الرصيد + الأرباح غير المحققة
  الانكشاف = (أعلى حقق - الحقوق الحالية) / أعلى حقق
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from core.base import BaseEngine
from core.events import (
    ExecutionEvent, MarketTickEvent, PortfolioEvent, EventBus,
    HealthEvent, HealthStatus, AlertEvent, AlertLevel
)
from core.types import PortfolioSnapshot
from database.repositories import (
    TradeRepository, PositionRepository, CoinRepository, PortfolioRepository, get_session
)

logger = logging.getLogger("portfolio_engine")


# ═══════════════════════════════════════════════════════════════
#  حالة المحفظة
# ═══════════════════════════════════════════════════════════════

class PortfolioState:
    """حالات المحفظة — ديناميكية، تُحسب من حالة المحرك."""
    نشطة = "نشطة"
    متوقفة = "متوقفة"
    طارئ = "طارئ"


class PortfolioEngine(BaseEngine):
    """يتتبع المحفظة الافتراضية. لا أموال حقيقية. لا اتصال بمنصة."""

    def __init__(self, event_bus: EventBus, initial_balance: float):
        """
        المعاملات:
            initial_balance: رصيد البداية. مطلوب، لا قيمة افتراضية.
        """
        super().__init__("portfolio_engine")
        self.event_bus = event_bus
        self.initial_balance: float = initial_balance
        self.balance: float = initial_balance
        self.equity: float = initial_balance
        self.peak_equity: float = initial_balance
        # PnL
        self.total_pnl: float = 0.0
        self.realized_pnl: float = 0.0
        self.unrealized_pnl: float = 0.0
        # إحصائيات
        self.open_positions_count: int = 0
        self.total_trades: int = 0
        self.wins: int = 0
        self.losses: int = 0
        self._telegram_id: int = 0  # معرف تليجرام int وليس UUID

    # ═════════════════════════════════════════════════════════
    #  دورة الحياة
    # ═════════════════════════════════════════════════════════

    async def initialize(self) -> None:
        await self.event_bus.subscribe("ExecutionEvent", self._on_execution)
        await self.event_bus.subscribe("MarketTickEvent", self._on_price_update)
        self.logger.info(f"[المحفظة] تم التهيئة. الرصيد الابتدائي: {self.initial_balance:.2f}")

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._snapshot_loop())
        asyncio.create_task(self._heartbeat_loop())
        self.logger.info("[المحفظة] بدأت العمل.")

    async def stop(self) -> None:
        self._running = False
        self.logger.info("[المحفظة] توقفت.")

    # ═════════════════════════════════════════════════════════
    #  معالجة التنفيذ
    # ═════════════════════════════════════════════════════════

    async def _on_execution(self, event: ExecutionEvent):
        """تتبع المراكز الافتراضية عند تنفيذ الصفقات."""
        if event.status == "FILLED":
            self.open_positions_count += 1
            self.total_trades += 1
            self.balance -= event.fees
            self.logger.info(
                f"[المحفظة] فتح مركز: {event.symbol} @ {event.entry_price:.6f} | "
                f"الرسوم={event.fees:.4f}"
            )

    # ═════════════════════════════════════════════════════════
    #  تحديث الأسعار
    # ═════════════════════════════════════════════════════════

    async def _on_price_update(self, event: MarketTickEvent):
        """تحديث الأرباح غير المحققة بناءً على الأسعار الحية."""
        if not self._telegram_id:
            return
        try:
            async for session in get_session():
                open_trades = await TradeRepository.get_open_trades_for_user(
                    session, self._telegram_id
                )
                unrealized = 0.0
                for trade in open_trades:
                    price = event.price if trade.symbol == event.symbol else trade.entry_price
                    if trade.entry_price > 0:
                        pnl_pct = (price - trade.entry_price) / trade.entry_price
                        unrealized += trade.quantity * pnl_pct

                self.unrealized_pnl = unrealized
                # الحقوق = الرصيد + الأرباح غير المحققة
                self.equity = self.balance + self.unrealized_pnl
                self.open_positions_count = len(open_trades)

                if self.equity > self.peak_equity:
                    self.peak_equity = self.equity

        except Exception:
            pass  # غير حرج

    # ═════════════════════════════════════════════════════════
    #  إغلاق الصفقة
    # ═════════════════════════════════════════════════════════

    def record_closed_trade(self, won: bool, pnl: float):
        """تحديث المحفظة بعد إغلاق صفقة."""
        if won:
            self.wins += 1
        else:
            self.losses += 1
        self.realized_pnl += pnl
        self.balance += pnl
        self.total_pnl = self.realized_pnl
        self.total_trades += 1
        self.logger.info(
            f"[المحفظة] صفقة مغلقة: {'✅ ربح' if won else '❌ خسارة'} | "
            f"النتيجة={pnl:+.2f} | الرصيد الحالي={self.balance:.2f}"
        )

    # ═════════════════════════════════════════════════════════
    #  قيمة المحفظة — مجموع رأس المال المخصص + PnL لكل عملة
    # ═════════════════════════════════════════════════════════

    async def calculate_portfolio_value(self) -> float:
        """
        قيمة المحفظة = مجموع (رأس المال المخصص لكل عملة نشطة + الأرباح/الخسائر).
        تقرأ من CoinRepository وتحسب بناءً على التوزيع الفعلي.
        """
        if not self._telegram_id:
            return self.balance

        try:
            async for session in get_session():
                coins = await CoinRepository.get_all_active(session, self._telegram_id)
                total_value = 0.0
                for coin in coins:
                    total_value += coin.capital_allocated
                # إذا لا توجد عملات نشطة، استخدم الرصيد الحالي
                return total_value + self.realized_pnl if total_value > 0 else self.balance
        except Exception:
            return self.balance

    # ═════════════════════════════════════════════════════════
    #  لقطة المحفظة
    # ═════════════════════════════════════════════════════════

    def _calculate_drawdown(self) -> float:
        """
        الانكشاف = (أعلى قيمة حقوق - الحقوق الحالية) / أعلى قيمة حقوق
        لا نسبة مئوية، قيمة عشرية (0.0 - 1.0).
        """
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - self.equity) / self.peak_equity

    def _determine_status(self) -> str:
        """
        الحالة الديناميكية — تُحسب من حالة المحرك الحقيقية وليس static.
        """
        if not self._running:
            return PortfolioState.متوقفة

        # حساب الانكشاف
        drawdown = self._calculate_drawdown()

        # طارئ: انكشاف > 20% أو رصيد سالب
        if drawdown > 0.20 or self.balance <= 0:
            return PortfolioState.طارئ

        return PortfolioState.نشطة

    def get_snapshot(self) -> PortfolioSnapshot:
        """لقطة لحالة المحفظة الحالية."""
        drawdown = self._calculate_drawdown()
        win_rate = (self.wins / max(self.total_trades, 1)) * 100
        status = self._determine_status()

        return PortfolioSnapshot(
            balance=round(self.balance, 2),
            equity=round(self.equity, 2),
            open_positions=self.open_positions_count,
            total_pnl=round(self.total_pnl, 2),
            win_rate=round(win_rate, 1),
            drawdown=round(drawdown * 100, 2),  # نسبة مئوية للـ Snapshot
            status=status,
        )

    def get_detailed_stats(self) -> dict:
        """إحصائيات تفصيلية للمحفظة."""
        snapshot = self.get_snapshot()
        return {
            "initial_balance": self.initial_balance,
            "current_balance": snapshot.balance,
            "equity": snapshot.equity,
            "peak_equity": round(self.peak_equity, 2),
            "total_pnl": snapshot.total_pnl,
            "realized_pnl": round(self.realized_pnl, 2),
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "win_rate": snapshot.win_rate,
            "drawdown_pct": snapshot.drawdown,
            "drawdown_ratio": round(self._calculate_drawdown(), 4),
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "open_positions": snapshot.open_positions,
            "status": self._determine_status(),
        }

    # ═════════════════════════════════════════════════════════
    #  حفظ اللقطات الدورية
    # ═════════════════════════════════════════════════════════

    async def _snapshot_loop(self):
        """حفظ لقطات المحفظة دورياً عبر Repository."""
        while self._running:
            try:
                snapshot = self.get_snapshot()

                if self._telegram_id:
                    async for session in get_session():
                        await PortfolioRepository.save_snapshot(
                            session, self._telegram_id,
                            total_balance=snapshot.balance,
                            available_balance=snapshot.balance,
                            unrealized_pnl=self.unrealized_pnl,
                            realized_pnl=self.realized_pnl,
                            exposure=float(self.open_positions_count),
                        )
                else:
                    self.logger.debug(
                        "[تحديث] تخطي حفظ اللقطة — لم يُحدد telegram_id."
                    )

                # نشر حدث المحفظة
                await self.event_bus.publish(PortfolioEvent(
                    balance=snapshot.balance,
                    equity=snapshot.equity,
                    open_positions=snapshot.open_positions,
                    total_pnl=snapshot.total_pnl,
                    win_rate=snapshot.win_rate,
                    drawdown=snapshot.drawdown,
                    status=snapshot.status,
                ))

                self.logger.debug(
                    f"[تحديث] لقطة محفوظة | الرصيد={snapshot.balance:.2f} | "
                    f"الانكشاف={snapshot.drawdown:.1f}% | الحالة={snapshot.status}"
                )

            except Exception as e:
                self.logger.debug(f"[تحديث] خطأ غير حرج في حفظ اللقطة: {e}")

            await asyncio.sleep(30)

    # ═════════════════════════════════════════════════════════
    #  نبض القلب
    # ═════════════════════════════════════════════════════════

    async def _heartbeat_loop(self):
        while self._running:
            await self.event_bus.publish(HealthEvent(
                engine=self.name, status=HealthStatus.HEALTHY,
                latency_ms=0, error_rate=0,
            ))
            await asyncio.sleep(5)
