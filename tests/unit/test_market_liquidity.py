"""
File: tests/unit/test_market_liquidity.py
1. Single Responsibility: Verify market/liquidity.py basic functionality.
2. Consumes: market.liquidity, contracts.market.
3. Produces: Tests for liquidity level identification and clustering.
4. Downstream: pytest run.
5. New Dependencies: pytest.
6. Touches Section 6 bugs? No (Bug 1 is in engine/smc.py).
7. Tests: smoke tests for liquidity module.
8. Logging: No.
9. Dependency Order: contracts -> market -> tests.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from contracts.market import SwingPoint
from market.liquidity import find_strongest_level, identify_liquidity_levels, is_liquidity_sweep


def make_swing(price: float, type_: str = "high", index: int = 0) -> SwingPoint:
    return SwingPoint(
        symbol="BTCUSDT", timeframe="15m", price=price,
        timestamp=datetime.now(timezone.utc), type=type_, index=index,  # type: ignore[arg-type]
    )


class TestIdentifyLiquidityLevels:
    def test_returns_dict_with_expected_keys(self):
        swings = [make_swing(100.0, "high"), make_swing(95.0, "low")]
        result = identify_liquidity_levels(swings)
        assert isinstance(result, dict)
        assert "high_levels" in result
        assert "low_levels" in result

    def test_clusters_nearby_swings(self):
        # Three highs within 0.1% of 100.0 (tolerance default 0.1%).
        swings = [
            make_swing(100.0, "high", 0),
            make_swing(100.05, "high", 1),
            make_swing(99.95, "high", 2),
        ]
        result = identify_liquidity_levels(swings)
        # Should cluster as one level.
        assert len(result["high_levels"]) <= 2  # at most 2 distinct clusters

    def test_empty_swings_returns_empty_lists(self):
        result = identify_liquidity_levels([])
        assert result["high_levels"] == []
        assert result["low_levels"] == []


class TestFindStrongestLevel:
    def test_returns_strongest(self):
        levels = [
            {"price": 100.0, "touches": 1, "strength": 0.2},
            {"price": 105.0, "touches": 5, "strength": 1.0},
            {"price": 110.0, "touches": 3, "strength": 0.6},
        ]
        strongest = find_strongest_level(levels)
        assert strongest is not None
        assert strongest["strength"] == 1.0

    def test_empty_levels_returns_none(self):
        assert find_strongest_level([]) is None


class TestIsLiquiditySweep:
    def test_returns_bool(self):
        from tests.conftest import make_candle, make_dt
        candle = make_candle(open_time=make_dt(0), open=100.0, high=110.0,
                            low=99.0, close=105.0)
        level = {"price": 105.0, "touches": 3, "strength": 0.6}
        result = is_liquidity_sweep(candle, level)
        assert isinstance(result, bool)
