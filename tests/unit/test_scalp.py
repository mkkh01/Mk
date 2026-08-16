from __future__ import annotations

from datetime import datetime, timedelta, timezone

from config.profiles import (
    ALL_MONITORED_TIMEFRAMES,
    SCALP_TIMEFRAMES,
    SWING_TIMEFRAMES,
    fixed_timeframes,
)
from engine.scalp import ScalpMonitor
from tests.conftest import make_candle


def test_fixed_timeframes_cover_both_profiles():
    assert tuple(fixed_timeframes()) == ALL_MONITORED_TIMEFRAMES
    assert set(SWING_TIMEFRAMES).issubset(ALL_MONITORED_TIMEFRAMES)
    assert set(SCALP_TIMEFRAMES).issubset(ALL_MONITORED_TIMEFRAMES)


def test_scalp_volume_state_distinguishes_bullish_bearish_neutral_and_missing():
    now = datetime.now(timezone.utc)
    bullish = make_candle(symbol="BTCUSDT", timeframe="5m", open_time=now - timedelta(minutes=5), open=100.0, high=102.0, low=99.0, close=101.0, volume=100.0)
    bearish = make_candle(symbol="BTCUSDT", timeframe="5m", open_time=now - timedelta(minutes=5), open=100.0, high=101.0, low=98.0, close=99.0, volume=100.0)
    neutral = make_candle(symbol="BTCUSDT", timeframe="5m", open_time=now - timedelta(minutes=5), open=100.0, high=101.0, low=99.0, close=100.0, volume=100.0)

    assert ScalpMonitor._classify_volume([bullish], {"cvd_slope": 3.0, "delta": 3.0}) == "bullish"
    assert ScalpMonitor._classify_volume([bearish], {"cvd_slope": -3.0, "delta": -3.0}) == "bearish"
    assert ScalpMonitor._classify_volume([neutral], {"cvd_slope": 0.1, "delta": -0.1}) == "neutral"
    assert ScalpMonitor._classify_volume([], {}) == "missing"
