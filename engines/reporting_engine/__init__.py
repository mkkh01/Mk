"""
Reporting Engine — generates structured reports for Telegram and logs.
Daily/Weekly/Monthly reports, trade reports, performance reports.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from core.base import BaseEngine
from core.events import EventBus, HealthEvent, HealthStatus
from core.types import PortfolioSnapshot
from database.repositories import TradeRepository, get_session

logger = logging.getLogger("reporting_engine")


class ReportingEngine(BaseEngine):
    """Generates reports. No trading logic."""

    def __init__(self, event_bus: EventBus):
        super().__init__("reporting_engine")
        self.event_bus = event_bus

    async def initialize(self) -> None:
        self.logger.info("Reporting Engine initialized.")

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._heartbeat_loop())
        self.logger.info("Reporting Engine started.")

    async def stop(self) -> None:
        self._running = False

    async def generate_trade_report(self, user_id: str, limit: int = 20) -> str:
        """Generate a report of recent trades."""
        try:
            async for session in get_session():
                trades = await TradeRepository.get_closed_trades(session, user_id, limit)
                if not trades:
                    return "📋 لا توجد صفقات مغلقة بعد."

                lines = ["📋 *تقرير آخر الصفقات*", "━" * 25]
                for t in trades[:limit]:
                    icon = "✅" if t.status == "WON" else "❌"
                    pnl_str = f"+{t.pnl:.2f}" if t.pnl > 0 else f"{t.pnl:.2f}"
                    lines.append(
                        f"{icon} {t.symbol} | {t.strategy_used or 'N/A'} | "
                        f"PnL: `{pnl_str}` | {t.side}"
                    )
                return "\n".join(lines)
        except Exception as e:
            self.logger.error(f"Trade report error: {e}")
            return "❌ خطأ في جلب تقرير الصفقات."

    async def generate_performance_report(self, user_id: str) -> str:
        """Generate a performance statistics report."""
        try:
            async for session in get_session():
                trades = await TradeRepository.get_all_closed(session, user_id)
                if not trades:
                    return "📊 لا توجد بيانات أداء كافية بعد."

                total = len(trades)
                wins = [t for t in trades if t.status == "WON"]
                losses = [t for t in trades if t.status == "LOST"]
                win_rate = (len(wins) / total * 100) if total > 0 else 0

                total_pnl = sum(t.pnl for t in trades)
                avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0
                avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0

                # Profit factor
                gross_profit = sum(t.pnl for t in wins)
                gross_loss = abs(sum(t.pnl for t in losses))
                profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

                # Best/worst trade
                best = max(trades, key=lambda t: t.pnl)
                worst = min(trades, key=lambda t: t.pnl)

                return (
                    f"📊 *تقرير الأداء*\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"📈 إجمالي الصفقات: {total}\n"
                    f"✅ نسبة النجاح: {win_rate:.1f}%\n"
                    f"💰 صافي الربح: `{total_pnl:.2f} USDT`\n"
                    f"📊 عامل الربح: {profit_factor:.2f}\n"
                    f"🏆 متوسط الربح: `{avg_win:.2f}` | متوسط الخسارة: `{avg_loss:.2f}`\n"
                    f"📈 أفضل صفقة: {best.symbol} `{best.pnl:.2f}`\n"
                    f"📉 أسوأ صفقة: {worst.symbol} `{worst.pnl:.2f}`"
                )
        except Exception as e:
            self.logger.error(f"Performance report error: {e}")
            return "❌ خطأ في جلب تقرير الأداء."

    async def generate_daily_report(self, user_id: str) -> str:
        """Generate a daily summary report."""
        try:
            async for session in get_session():
                trades = await TradeRepository.get_closed_trades(session, user_id, limit=100)

                # Filter today's trades
                today = datetime.utcnow().date()
                today_trades = [t for t in trades if t.closed_at and t.closed_at.date() == today]

                if not today_trades:
                    return "📅 لا توجد صفقات اليوم."

                wins = [t for t in today_trades if t.status == "WON"]
                daily_pnl = sum(t.pnl for t in today_trades)
                win_rate = (len(wins) / len(today_trades) * 100) if today_trades else 0

                return (
                    f"📅 *التقرير اليومي*\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"📊 صفقات اليوم: {len(today_trades)}\n"
                    f"✅ نسبة النجاح: {win_rate:.1f}%\n"
                    f"💰 ربح/خسارة اليوم: `{daily_pnl:.2f} USDT`"
                )
        except Exception as e:
            self.logger.error(f"Daily report error: {e}")
            return "❌ خطأ في جلب التقرير اليومي."

    async def generate_status_report(self, portfolio_snapshot: PortfolioSnapshot,
                                     health_status: dict,
                                     learning_recommendations: list) -> str:
        """Generate a comprehensive status report."""
        lines = [
            "📡 *حالة النظام*",
            "━" * 25,
            f"💼 المحفظة: `{portfolio_snapshot.balance:.2f} USDT`",
            f"📈 equity: `{portfolio_snapshot.equity:.2f}`",
            f"🔓 صفقات مفتوحة: {portfolio_snapshot.open_positions}",
            f"📊 نسبة الربح: {portfolio_snapshot.win_rate:.1f}%",
            f"📉 السحب: {portfolio_snapshot.drawdown:.2f}%",
            f"⚙️ حالة النظام: {health_status.get('system_state', 'UNKNOWN')}",
        ]
        if learning_recommendations:
            lines.append("\n💡 *توصيات:*")
            for rec in learning_recommendations[:3]:
                lines.append(f"  • {rec}")
        return "\n".join(lines)

    @staticmethod
    def format_trade_alert(symbol: str, pnl: float, result: str,
                           reason: str = "", strategy: str = "",
                           confidence: float = 0) -> str:
        """Format a trade alert message."""
        icon = "✅" if result == "WON" else "❌"
        pnl_str = f"+{pnl:.2f}" if pnl > 0 else f"{pnl:.2f}"
        lines = [
            f"{icon} *صفقة مغلقة*",
            f"━━━━━━━━━━━━━━",
            f"🪙 العملة: {symbol}",
            f"💰 النتيجة: `{pnl_str} USDT`",
        ]
        if reason:
            lines.append(f"📝 السبب: {reason}")
        if strategy:
            lines.append(f"🧠 الاستراتيجية: {strategy}")
        if confidence:
            lines.append(f"🎯 الثقة: {confidence:.0f}%")
        return "\n".join(lines)

    async def _heartbeat_loop(self):
        while self._running:
            await self.event_bus.publish(HealthEvent(
                engine=self.name, status=HealthStatus.HEALTHY,
                latency_ms=0, error_rate=0,
            ))
            await asyncio.sleep(5)
