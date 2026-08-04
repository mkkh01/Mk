"""
File: contracts/portfolio.py
1. Single Responsibility: Define TradeSummary and PerformanceMetrics.
2. Consumes: nothing.
3. Produces: TradeSummary, PerformanceMetrics.
4. Downstream: portfolio/performance.py, bot/telegram_bot.py.
5. New Dependencies: pydantic.
6. Touches Section 6 bugs? No.
7. Tests: tests/unit/test_portfolio.py.
8. Logging: No.
9. Dependency Order: contracts/simulation.py -> contracts/portfolio.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TradeSummary(BaseModel):
    """Compact trade representation for the bot's Trade History button."""

    symbol: str
    direction: str
    entry_price: float
    stop_loss: Optional[float] = None
    initial_stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    pnl: Optional[float] = None
    status: str
    opened_at: datetime
    closed_at: Optional[datetime] = None
    close_price: Optional[float] = None


class PerformanceMetrics(BaseModel):
    """Aggregate performance metrics over a (possibly filtered) trade set."""

    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float = Field(ge=0.0, le=1.0)
    total_pnl: float
    average_pnl: float
    max_drawdown: float
    max_drawdown_percent: float
    sharpe_ratio: Optional[float] = None
    profit_factor: Optional[float] = None
    average_win: float
    average_loss: float
    largest_win: float
    largest_loss: float
    consecutive_wins: int
    consecutive_losses: int
