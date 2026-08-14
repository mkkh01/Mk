"""Tests for volume evidence classification in the orchestrator."""

from engine.orchestrator import _classify_volume_state
from tests.conftest import make_candle, make_dt


def _candle(*, volume=100.0, closed=True):
    return make_candle(
        symbol="DOTUSDT",
        timeframe="15m",
        open_time=make_dt(0),
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=volume,
        taker_buy_volume=60.0,
        taker_sell_volume=40.0,
        is_closed=closed,
    )


def test_volume_state_missing_without_closed_candles():
    assert _classify_volume_state([_candle(closed=False)], {}) == "missing"


def test_volume_state_missing_for_zero_volume():
    assert _classify_volume_state([_candle(volume=0.0)], {"cvd_slope": 1.0, "delta": 1.0}) == "missing"


def test_volume_state_bullish_when_cvd_and_delta_confirm():
    candles = [_candle(volume=100.0)]
    assert _classify_volume_state(candles, {"cvd_slope": 3.0, "delta": 3.0}) == "bullish"


def test_volume_state_bearish_when_cvd_and_delta_confirm():
    candles = [_candle(volume=100.0)]
    assert _classify_volume_state(candles, {"cvd_slope": -3.0, "delta": -3.0}) == "bearish"


def test_volume_state_neutral_when_flow_disagrees():
    candles = [_candle(volume=100.0)]
    assert _classify_volume_state(candles, {"cvd_slope": 3.0, "delta": -3.0}) == "neutral"
