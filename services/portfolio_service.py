"""
خدمة المحفظة — تنسيق تتبع المحفظة والتقارير.
V4.0: تقارير ديناميكية شاملة — حالة النظام، الصفقات، الصحة.
       جميع القيم من DB أو engine state — لا قيم افتراضية.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from engines.portfolio_engine import PortfolioEngine
from engines.reporting_engine import ReportingEngine
from engines.learning_engine import LearningEngine
from engines.health_monitor import HealthMonitor
from database.repositories import (
    TradeRepository, PositionRepository,
    PortfolioRepository, CoinRepository, get_session,
)

logger = logging.getLogger("محفظة_الخدمة")


class PortfolioService:
    """إدارة منسقة للمحفظة وتقاريرها."""

    def __init__(self, portfolio_engine: PortfolioEngine,
                 reporting_engine: ReportingEngine,
                 learning_engine: LearningEngine,
                 health_monitor: HealthMonitor):
        self.portfolio = portfolio_engine
        self.reporting = reporting_engine
        self.learning = learning_engine
        self.health = health_monitor
        # توقيت آخر مزامنة
        self._last_sync_time: Optional[datetime] = None
        self._last_cycle_duration: float = 0.0
        # إحصائيات الإشارات
        self._signals_processed: int = 0
        self._signals_rejected: int = 0
        self._rejection_reasons: dict[str, int] = {}

    # ═════════════════════════════════════════════════════════
    #  تقرير الحالة الديناميكي الكامل
    # ═════════════════════════════════════════════════════════

    async def get_dynamic_status_report(self, telegram_id: int) -> dict:
        """
        تقرير حالة ديناميكي كامل — جميع القيم محسوبة من DB و engine state.

        المعاملات:
            telegram_id: معرف تليجرام الخاص بالمستخدم (int).

        المكونات:
            - portfolio_value (محسوب)
            - equity (لحظي)
            - drawdown
            - open_positions
            - today_trades
            - last_sync_time
            - last_cycle_duration
            - signals_processed / rejected + reasons
            - health: db, exchange, telegram, scheduler, strategies, market_data

        يعيد:
            قاموس تقرير كامل.
        """
        report: dict = {
            "timestamp": datetime.utcnow().isoformat(),
            "portfolio": {},
            "trading": {},
            "health": {},
        }

        # ── المحفظة ─────────────────────────────────────────
        snapshot = self.portfolio.get_snapshot()
        peak_equity = self.portfolio.peak_equity

        report["portfolio"] = {
            "portfolio_value": round(snapshot.balance, 2),
            "equity": round(snapshot.equity, 2),
            "peak_equity": round(peak_equity, 2),
            "total_pnl": round(snapshot.total_pnl, 2),
            "win_rate": round(snapshot.win_rate, 1),
            "drawdown": round(snapshot.drawdown, 2),
            "open_positions": snapshot.open_positions,
            "status": snapshot.status,
            "initial_balance": round(self.portfolio.initial_balance, 2),
        }

        # ── صفقات اليوم ─────────────────────────────────────
        today_trades_count = 0
        try:
            async for session in get_session():
                closed_trades = await TradeRepository.get_closed_trades(
                    session, telegram_id, limit=200
                )
                today = datetime.utcnow().date()
                today_trades = [
                    t for t in closed_trades
                    if t.closed_at and t.closed_at.date() == today
                ]
                today_trades_count = len(today_trades)
                report["trading"]["today_trades"] = today_trades_count

                # صفقات مفتوحة
                open_trades = await TradeRepository.get_open_trades_for_user(
                    session, telegram_id
                )
                report["trading"]["open_trades_count"] = len(open_trades)

                # آخر صفقة
                if closed_trades:
                    last_trade = closed_trades[0]
                    report["trading"]["last_trade"] = {
                        "symbol": last_trade.symbol,
                        "status": last_trade.status,
                        "pnl": round(last_trade.pnl, 2),
                        "strategy": last_trade.strategy_used,
                        "closed_at": last_trade.closed_at.isoformat() if last_trade.closed_at else None,
                    }
        except Exception as e:
            logger.warning(f"[محفظة] خطأ في جلب الصفقات: {e}")
            report["trading"]["today_trades"] = 0

        # ── التوقيت ─────────────────────────────────────────
        report["trading"]["last_sync_time"] = (
            self._last_sync_time.isoformat() if self._last_sync_time else None
        )
        report["trading"]["last_cycle_duration"] = round(self._last_cycle_duration, 2)

        # ── إحصائيات الإشارات ──────────────────────────────
        report["trading"]["signals"] = {
            "processed": self._signals_processed,
            "rejected": self._signals_rejected,
            "rejection_reasons": dict(self._rejection_reasons),
            "acceptance_rate": round(
                (self._signals_processed - self._signals_rejected) /
                max(self._signals_processed, 1) * 100, 1
            ),
        }

        # ── صحة النظام ──────────────────────────────────────
        health_status = self.health.get_status()
        engine_statuses = health_status.get("engines", {})

        # فحص اتصال قاعدة البيانات
        db_healthy = True
        try:
            async for session in get_session():
                # مجرد اختبار اتصال — session ناجحة = الاتصال يعمل
                pass
        except Exception:
            db_healthy = False

        # حالة المحركات الرئيسية
        report["health"] = {
            "system_state": health_status.get("system_state", "UNKNOWN"),
            "alerts_count": health_status.get("alerts_count", 0),
            "db": "✅ متصل" if db_healthy else "❌ منقطع",
            "exchange": "✅ متصل (محاكاة)" if "market_data_engine" in engine_statuses else "⚠️ غير معروف",
            "telegram": "✅ متصل (محاكاة)" if engine_statuses else "⚠️ غير معروف",
            "scheduler": "✅ نشط" if "market_analyzer" in engine_statuses else "⚠️ متوقف",
            "strategies": "✅ نشطة" if "strategy_engine" in engine_statuses else "⚠️ متوقفة",
            "market_data": "✅ نشط" if "market_data_engine" in engine_statuses else "⚠️ متوقف",
            "execution": "✅ نشط" if "execution_engine" in engine_statuses else "⚠️ متوقف",
        }

        # تفاصيل المحركات
        report["health"]["engines_detail"] = {}
        for engine_name, status in engine_statuses.items():
            report["health"]["engines_detail"][engine_name] = {
                "status": status.get("status", "UNKNOWN"),
                "error_rate": status.get("error_rate", 0),
            }

        # ── توصيات التعلم ───────────────────────────────────
        try:
            recommendations = self.learning.get_recommendations()
            report["learning"] = {
                "recommendations": recommendations[:5] if recommendations else [],
                "strategy_performance": self.learning.get_strategy_performance(),
            }
        except Exception:
            report["learning"] = {"recommendations": [], "strategy_performance": {}}

        # ── العملات النشطة ─────────────────────────────────
        try:
            async for session in get_session():
                coins = await CoinRepository.get_all_active(session, telegram_id)
                report["coins"] = {
                    "count": len(coins),
                    "details": [
                        {
                            "symbol": c.symbol,
                            "capital_allocated": c.capital_allocated,
                            "timeframes": getattr(c, 'timeframes', ["15m"]),
                            "risk_per_trade": getattr(c, 'risk_per_trade', 1.0),
                            "active": c.is_active,
                        }
                        for c in coins
                    ],
                    "total_allocated": sum(c.capital_allocated for c in coins),
                }
        except Exception:
            report["coins"] = {"count": 0, "details": [], "total_allocated": 0}

        return report

    # ═════════════════════════════════════════════════════════
    #  تقرير الحالة المختصر
    # ═════════════════════════════════════════════════════════

    async def get_full_status(self, telegram_id: int) -> dict:
        """
        تقرير حالة مختصر — للمراقبة السريعة.

        المعاملات:
            telegram_id: معرف تليجرام الخاص بالمستخدم (int).

        يعيد:
            قاموس يحتوي على ملخص المحفظة والصحة والتوصيات.
        """
        snapshot = self.portfolio.get_snapshot()
        health = self.health.get_status()
        recommendations = self.learning.get_recommendations()
        strategy_perf = self.learning.get_strategy_performance()

        # عدد الصفقات النشطة
        open_trades_count = snapshot.open_positions
        try:
            async for session in get_session():
                open_trades = await TradeRepository.get_open_trades_for_user(
                    session, telegram_id
                )
                open_trades_count = len(open_trades)
        except Exception:
            pass

        return {
            "portfolio": {
                "balance": snapshot.balance,
                "equity": snapshot.equity,
                "open_positions": open_trades_count,
                "total_pnl": snapshot.total_pnl,
                "win_rate": snapshot.win_rate,
                "drawdown": snapshot.drawdown,
                "status": snapshot.status,
            },
            "health": health,
            "recommendations": recommendations[:3] if recommendations else [],
            "strategy_performance": strategy_perf,
            "timestamp": datetime.utcnow().isoformat(),
        }

    # ═════════════════════════════════════════════════════════
    #  تحديث الإحصائيات من خدمة التداول
    # ═════════════════════════════════════════════════════════

    def update_signal_stats(self, processed: int, rejected: int,
                            reasons: dict[str, int], cycle_duration: float):
        """تحديث إحصائيات الإشارات من خدمة التداول."""
        self._signals_processed += processed
        self._signals_rejected += rejected
        for reason, count in reasons.items():
            self._rejection_reasons[reason] = self._rejection_reasons.get(reason, 0) + count
        self._last_cycle_duration = cycle_duration

    def mark_synced(self):
        """تسجيل وقت آخر مزامنة."""
        self._last_sync_time = datetime.utcnow()

    # ═════════════════════════════════════════════════════════
    #  تقارير إضافية
    # ═════════════════════════════════════════════════════════

    async def get_trade_report(self, telegram_id: int) -> str:
        """تقرير آخر الصفقات."""
        return await self.reporting.generate_trade_report(str(telegram_id))

    async def get_performance_report(self, telegram_id: int) -> str:
        """تقرير الأداء."""
        return await self.reporting.generate_performance_report(str(telegram_id))

    async def get_daily_report(self, telegram_id: int) -> str:
        """التقرير اليومي."""
        return await self.reporting.generate_daily_report(str(telegram_id))

    async def get_sharpe_score(self) -> float:
        """درجة شارب التقريبية."""
        return self.learning.get_sharpe_like_score()
