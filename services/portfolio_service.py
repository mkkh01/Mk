"""
Portfolio Service — coordinates portfolio tracking and reporting.
"""
import logging

from engines.portfolio_engine import PortfolioEngine
from engines.reporting_engine import ReportingEngine
from engines.learning_engine import LearningEngine
from engines.health_monitor import HealthMonitor

logger = logging.getLogger("portfolio_service")


class PortfolioService:
    """Coordinated portfolio management."""

    def __init__(self, portfolio_engine: PortfolioEngine,
                 reporting_engine: ReportingEngine,
                 learning_engine: LearningEngine,
                 health_monitor: HealthMonitor):
        self.portfolio = portfolio_engine
        self.reporting = reporting_engine
        self.learning = learning_engine
        self.health = health_monitor

    async def get_full_status(self, user_id: str) -> dict:
        """Get comprehensive portfolio and system status."""
        snapshot = self.portfolio.get_snapshot()
        health = self.health.get_status()
        recommendations = self.learning.get_recommendations()
        strategy_perf = self.learning.get_strategy_performance()

        return {
            "portfolio": {
                "balance": snapshot.balance,
                "equity": snapshot.equity,
                "open_positions": snapshot.open_positions,
                "total_pnl": snapshot.total_pnl,
                "win_rate": snapshot.win_rate,
                "drawdown": snapshot.drawdown,
            },
            "health": health,
            "recommendations": recommendations,
            "strategy_performance": strategy_perf,
        }

    async def get_trade_report(self, user_id: str) -> str:
        return await self.reporting.generate_trade_report(user_id)

    async def get_performance_report(self, user_id: str) -> str:
        return await self.reporting.generate_performance_report(user_id)

    async def get_daily_report(self, user_id: str) -> str:
        return await self.reporting.generate_daily_report(user_id)

    async def get_sharpe_score(self) -> float:
        return self.learning.get_sharpe_like_score()
