"""
File: tests/unit/test_market_volatility.py
1. Single Responsibility: Verify market/volatility.py basic functionality.
2. Consumes: market.volatility.
3. Produces: Tests for ATR, Bollinger Bands, high-volatility detection.
4. Downstream: pytest run.
5. New Dependencies: pytest.
6. Touches Section 6 bugs? No.
7. Tests: smoke tests for volatility module.
8. Logging: No.
9. Dependency Order: contracts -> market -> tests.
"""

from __future__ import annotations

import pytest

from market.volatility import (
    calculate_atr,
    calculate_bollinger_bands,
    calculate_volatility,
    is_high_volatility,
    is_ranging,
)
from tests.conftest import bullish_seq, make_candle, make_dt


class TestATR:
    def test_atr_returns_positive_float(self):
        candles = bullish_seq(n=30)
        atr = calculate_atr(candles, period=14)
        assert isinstance(atr, float)
        assert atr >= 0.0

    def test_atr_zero_for_flat_sequence(self):
        base = make_dt(0)
        candles = []
        for i in range(30):
            candles.append(make_candle(
                open_time=base, open=100.0, high=100.0,
                low=100.0, close=100.0, timeframe_minutes=15,
            ))
            base = candles[-1].close_time
        atr = calculate_atr(candles, period=14)
        assert atr == pytest.approx(0.0, abs=1e-6)

    def test_atr_handles_insufficient_candles(self):
        candles = bullish_seq(n=5)
        atr = calculate_atr(candles, period=14)
        assert isinstance(atr, float)


class TestBollingerBands:
    def test_bollinger_bands_returns_three_floats(self):
        candles = bullish_seq(n=30)
        result = calculate_bollinger_bands(candles, period=20, std_dev=2.0)
        assert isinstance(result, tuple)
        assert len(result) == 3
        upper, middle, lower = result
        assert upper >= middle >= lower

    def test_bollinger_bands_flat_for_constant_price(self):
        base = make_dt(0)
        candles = []
        for i in range(30):
            candles.append(make_candle(
                open_time=base, open=100.0, high=100.0,
                low=100.0, close=100.0, timeframe_minutes=15,
            ))
            base = candles[-1].close_time
        upper, middle, lower = calculate_bollinger_bands(candles, period=20, std_dev=2.0)
        assert upper == pytest.approx(100.0, abs=1e-3)
        assert middle == pytest.approx(100.0, abs=1e-3)
        assert lower == pytest.approx(100.0, abs=1e-3)


class TestCalculateVolatility:
    def test_calculate_volatility_returns_dict_with_expected_keys(self):
        candles = bullish_seq(n=30)
        result = calculate_volatility(candles)
        assert isinstance(result, dict)
        # Required keys per spec §16.
        for key in ("atr", "atr_percent", "bb_upper", "bb_lower", "bb_width"):
            assert key in result, f"Missing key: {key}"

    def test_calculate_volatility_handles_insufficient_candles(self):
        candles = bullish_seq(n=5)
        result = calculate_volatility(candles)
        assert isinstance(result, dict)


class TestVolatilityFlags:
    def test_is_high_volatility_returns_bool(self):
        candles = bullish_seq(n=30)
        assert isinstance(is_high_volatility(candles), bool)

    def test_is_ranging_returns_bool(self):
        candles = bullish_seq(n=30)
        assert isinstance(is_ranging(candles), bool)

    def test_is_ranging_true_for_flat_sequence(self):
        base = make_dt(0)
        candles = []
        for i in range(30):
            candles.append(make_candle(
                open_time=base, open=100.0, high=100.02,
                low=99.98, close=100.0, timeframe_minutes=15,
            ))
            base = candles[-1].close_time
        assert is_ranging(candles) is True
