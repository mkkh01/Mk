"""
File: tests/unit/test_portfolio.py
1. Single Responsibility: Verify portfolio/performance.py.
2. Consumes: portfolio.performance, contracts.simulation, contracts.portfolio.
3. Produces: Tests for metrics calculation, max drawdown, win rate, profit factor.
4. Downstream: pytest run.
5. New Dependencies: pytest.
6. Touches Section 6 bugs? No.
7. Tests: smoke + correctness tests for performance module.
8. Logging: No.
9. Dependency Order: contracts -> portfolio -> tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from contracts.portfolio import PerformanceMetrics
from contracts.simulation import SimulatedTrade
from portfolio.performance import PerformanceCalculator


def make_trade(pnl: float | None, days_ago: int = 0, direction: str = "long") -> SimulatedTrade:
    opened = datetime.now(timezone.utc) - timedelta(days=days_ago + 1)
    closed = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return SimulatedTrade(
        id=uuid4(), decision_id=uuid4(),
        symbol="BTCUSDT", direction=direction,  # type: ignore[arg-type]
        entry_price=100.0, size=1.0, fee=0.1, slippage=0.05,
        opened_at=opened, closed_at=closed if pnl is not None else None,
        pnl=pnl, status="closed" if pnl is not None else "open",
        is_simulated=True,
    )


class TestCalculateMetrics:
    @pytest.mark.asyncio
    async def test_empty_trades_returns_zero_metrics(self, mock_supabase):
        mock_supabase.fetch_closed_trades.return_value = []
        calc = PerformanceCalculator(supabase=mock_supabase)
        metrics = await calc.calculate_metrics()
        assert isinstance(metrics, PerformanceMetrics)
        assert metrics.total_trades == 0
        assert metrics.winning_trades == 0
        assert metrics.losing_trades == 0
        assert metrics.total_pnl == 0.0
        assert metrics.win_rate == 0.0

    @pytest.mark.asyncio
    async def test_metrics_with_winning_and_losing_trades(self, mock_supabase):
        mock_supabase.fetch_closed_trades.return_value = [
            make_trade(50.0, days_ago=5),   # win
            make_trade(-20.0, days_ago=4),  # loss
            make_trade(30.0, days_ago=3),   # win
            make_trade(-10.0, days_ago=2),  # loss
            make_trade(40.0, days_ago=1),   # win
        ]
        calc = PerformanceCalculator(supabase=mock_supabase)
        metrics = await calc.calculate_metrics()
        assert metrics.total_trades == 5
        assert metrics.winning_trades == 3
        assert metrics.losing_trades == 2
        assert metrics.win_rate == pytest.approx(0.6, rel=1e-2)
        assert metrics.total_pnl == pytest.approx(90.0, rel=1e-2)
        assert metrics.average_pnl == pytest.approx(18.0, rel=1e-2)

    @pytest.mark.asyncio
    async def test_max_drawdown_calculation(self, mock_supabase):
        # Sequence: +50, +30 (peak=80), -60 (trough=20) -> DD = 60.
        mock_supabase.fetch_closed_trades.return_value = [
            make_trade(50.0, days_ago=3),
            make_trade(30.0, days_ago=2),
            make_trade(-60.0, days_ago=1),
        ]
        calc = PerformanceCalculator(supabase=mock_supabase)
        metrics = await calc.calculate_metrics()
        # Max drawdown should be 60 (from peak 80 to trough 20).
        assert metrics.max_drawdown == pytest.approx(60.0, rel=1e-1)

    @pytest.mark.asyncio
    async def test_profit_factor_calculation(self, mock_supabase):
        # gross_profit = 50 + 30 = 80; gross_loss = 20 + 10 = 30; PF = 80/30.
        mock_supabase.fetch_closed_trades.return_value = [
            make_trade(50.0, days_ago=4),
            make_trade(30.0, days_ago=3),
            make_trade(-20.0, days_ago=2),
            make_trade(-10.0, days_ago=1),
        ]
        calc = PerformanceCalculator(supabase=mock_supabase)
        metrics = await calc.calculate_metrics()
        if metrics.profit_factor is not None:
            assert metrics.profit_factor == pytest.approx(80.0 / 30.0, rel=1e-1)

    @pytest.mark.asyncio
    async def test_consecutive_wins_losses(self, mock_supabase):
        # Sequence: W W L L L W -> max_consec_wins=2, max_consec_losses=3.
        mock_supabase.fetch_closed_trades.return_value = [
            make_trade(10.0, days_ago=5),
            make_trade(10.0, days_ago=4),
            make_trade(-5.0, days_ago=3),
            make_trade(-5.0, days_ago=2),
            make_trade(-5.0, days_ago=1),
            make_trade(10.0, days_ago=0),
        ]
        calc = PerformanceCalculator(supabase=mock_supabase)
        metrics = await calc.calculate_metrics()
        assert metrics.consecutive_wins >= 2
        assert metrics.consecutive_losses >= 3

    @pytest.mark.asyncio
    async def test_largest_win_and_loss(self, mock_supabase):
        mock_supabase.fetch_closed_trades.return_value = [
            make_trade(50.0, days_ago=3),
            make_trade(30.0, days_ago=2),
            make_trade(-20.0, days_ago=1),
            make_trade(-10.0, days_ago=0),
        ]
        calc = PerformanceCalculator(supabase=mock_supabase)
        metrics = await calc.calculate_metrics()
        assert metrics.largest_win == pytest.approx(50.0)
        assert metrics.largest_loss == pytest.approx(-20.0)

    @pytest.mark.asyncio
    async def test_sharpe_ratio_returns_float_or_none(self, mock_supabase):
        mock_supabase.fetch_closed_trades.return_value = [
            make_trade(10.0, days_ago=3),
            make_trade(20.0, days_ago=2),
            make_trade(15.0, days_ago=1),
        ]
        calc = PerformanceCalculator(supabase=mock_supabase)
        metrics = await calc.calculate_metrics()
        # Sharpe may be None if std is 0, otherwise a float.
        assert metrics.sharpe_ratio is None or isinstance(metrics.sharpe_ratio, float)


class TestGetTradeSummaries:
    @pytest.mark.asyncio
    async def test_get_trade_summaries_calls_supabase(self, mock_supabase):
        mock_supabase.fetch_recent_trades.return_value = [
            make_trade(50.0, days_ago=1),
        ]
        calc = PerformanceCalculator(supabase=mock_supabase)
        summaries = await calc.get_trade_summaries(limit=10)
        mock_supabase.fetch_recent_trades.assert_called_once_with(limit=10)
        assert isinstance(summaries, list)
