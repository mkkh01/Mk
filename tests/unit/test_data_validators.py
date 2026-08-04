"""
File: tests/unit/test_data_validators.py
1. Single Responsibility: Verify data/validators.py.
2. Consumes: data.validators, contracts.market.
3. Produces: Tests for candle validation rules.
4. Downstream: pytest run.
5. New Dependencies: pytest.
6. Touches Section 6 bugs? No.
7. Tests: smoke tests for validators.
8. Logging: No.
9. Dependency Order: contracts -> data -> tests.
"""

from __future__ import annotations

import pytest

from data.validators import validate_binance_kline, validate_candle, validate_candle_batch
from tests.conftest import make_candle, make_dt


class TestValidateCandle:
    def test_valid_candle_passes(self):
        candle = make_candle(
            open_time=make_dt(0), open=100.0, high=105.0,
            low=95.0, close=102.0, volume=100.0,
        )
        assert validate_candle(candle) is True

    def test_negative_price_rejected(self):
        candle = make_candle(
            open_time=make_dt(0), open=-1.0, high=105.0,
            low=95.0, close=102.0,
        )
        with pytest.raises(Exception):
            validate_candle(candle)

    def test_high_low_invariant_rejected(self):
        """high must be >= max(open, close, low); low must be <= min(open, close, high)."""
        candle = make_candle(
            open_time=make_dt(0), open=100.0, high=90.0,  # high < open
            low=95.0, close=102.0,
        )
        with pytest.raises(Exception):
            validate_candle(candle)

    def test_volume_mismatch_rejected(self):
        """taker_buy + taker_sell should approximately equal volume."""
        candle = make_candle(
            open_time=make_dt(0), open=100.0, high=105.0,
            low=95.0, close=102.0,
            volume=100.0,
            taker_buy_volume=80.0, taker_sell_volume=80.0,  # sum=160 != 100
        )
        with pytest.raises(Exception):
            validate_candle(candle)


class TestValidateCandleBatch:
    def test_returns_only_valid_candles(self):
        good = make_candle(open_time=make_dt(0), open=100.0, high=105.0,
                          low=95.0, close=102.0)
        bad = make_candle(open_time=make_dt(15), open=-1.0, high=105.0,
                         low=95.0, close=102.0)
        result = validate_candle_batch([good, bad])
        assert good in result
        assert bad not in result

    def test_empty_input_returns_empty(self):
        assert validate_candle_batch([]) == []


class TestValidateBinanceKline:
    def test_valid_kline_returns_parsed_dict(self):
        raw = {
            "e": "kline",
            "E": 1234567890,
            "s": "BTCUSDT",
            "k": {
                "t": 1700000000000,
                "T": 1700000099999,
                "s": "BTCUSDT",
                "i": "15m",
                "o": "100.0",
                "c": "102.0",
                "h": "105.0",
                "l": "95.0",
                "v": "100.0",
                "x": True,
                "V": "60.0",
            },
        }
        result = validate_binance_kline(raw)
        assert isinstance(result, dict)
        assert "symbol" in result or "s" in result

    def test_missing_kline_field_rejected(self):
        raw = {"e": "kline", "s": "BTCUSDT"}  # missing 'k'
        with pytest.raises(Exception):
            validate_binance_kline(raw)
