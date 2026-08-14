from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import config.thresholds as thresholds
from contracts.decision import DecisionResult, EntrySignal, RiskAssessment
from contracts.market import Candle
from replay.runner import ReplayRunner, ReplayStorage, _parse_binance_timestamp


UTC = timezone.utc


def make_candle(open_time: datetime, close: float, *, low: float | None = None, high: float | None = None) -> Candle:
    return Candle(
        symbol="ADAUSDT",
        timeframe="15m",
        open_time=open_time,
        close_time=open_time + timedelta(minutes=15),
        open=close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=100.0,
        taker_buy_volume=60.0,
        taker_sell_volume=40.0,
        is_closed=True,
    )


def test_parse_binance_millisecond_and_microsecond_timestamps() -> None:
    millisecond = _parse_binance_timestamp("1780272000000")
    microsecond = _parse_binance_timestamp("1780272000000000")
    assert millisecond == microsecond == datetime(2026, 6, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_replay_storage_hides_future_candles() -> None:
    start = datetime(2026, 7, 31, tzinfo=UTC)
    candles = [make_candle(start + timedelta(minutes=15 * index), 100 + index) for index in range(3)]
    storage = ReplayStorage({("ADAUSDT", "15m"): candles})
    storage.current_cutoff = candles[0].close_time
    visible = await storage.fetch_closed_candles("ADAUSDT", "15m", limit=200)
    assert [candle.open_time for candle in visible] == [candles[0].open_time]


def test_replay_profile_1to1_isolated_and_restored() -> None:
    runner = ReplayRunner({}, symbols=["ADAUSDT"], profile="1to1")
    production_tp = thresholds.VOLATILITY_ATR_MULTIPLIER_TP
    production_min_rr = thresholds.MIN_RISK_REWARD_RATIO

    with runner._profile_context():
        assert thresholds.VOLATILITY_ATR_MULTIPLIER_TP == thresholds.REPLAY_1TO1_ATR_MULTIPLIER_TP
        assert thresholds.MIN_RISK_REWARD_RATIO == thresholds.REPLAY_1TO1_MIN_RR

    assert thresholds.VOLATILITY_ATR_MULTIPLIER_TP == production_tp
    assert thresholds.MIN_RISK_REWARD_RATIO == production_min_rr


def test_replay_rejects_unknown_profile() -> None:
    with pytest.raises(ValueError, match="unsupported replay profile"):
        ReplayRunner({}, symbols=["ADAUSDT"], profile="unsafe")


def test_replay_outcome_does_not_use_trigger_candle_for_fill() -> None:
    start = datetime(2026, 7, 31, tzinfo=UTC)
    trigger = make_candle(start, 100.0, low=95.0, high=105.0)
    next_candle = make_candle(start + timedelta(minutes=15), 100.0, low=100.0, high=101.0)
    decision = DecisionResult(
        symbol="ADAUSDT",
        source_candle_open_time=trigger.open_time,
        score=0.8,
        confidence=0.8,
        regime_check_passed=True,
        structure_alignment_passed=True,
        htf_bias_aligned=True,
        risk=RiskAssessment(allowed=True),
        entry=EntrySignal(
            symbol="ADAUSDT",
            direction="long",
            entry_price=96.0,
            entry_type="limit",
            timeframe="15m",
            confidence=0.8,
            stop_loss=94.0,
            take_profit=102.0,
            risk_reward=3.0,
            valid_until=next_candle.close_time,
        ),
        final_verdict=True,
        timestamp=trigger.close_time,
    )
    runner = ReplayRunner({("ADAUSDT", "15m"): [trigger, next_candle]}, symbols=["ADAUSDT"])
    outcome = runner._evaluate_decision(decision)
    assert outcome.filled is False
    assert outcome.outcome == "no_fill"
