from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from contracts.decision import DecisionResult, EntrySignal, RiskAssessment
from contracts.market import MarketStructure, SwingPoint
from engine.entry_filters import evaluate_long_entry_quality
from simulation.paper_trade import LimitNotFilledError, PaperTrader
from tests.conftest import make_candle


def _timing_candles() -> list:
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles = []
    for index in range(7):
        candles.append(
            make_candle(
                open_time=base + timedelta(minutes=index * 15),
                open=100.0,
                high=101.0,
                low=99.8,
                close=100.2,
            )
        )
    candles.append(
        make_candle(
            open_time=base + timedelta(minutes=7 * 15),
            open=100.0,
            high=100.2,
            low=99.0,
            close=99.5,
        )
    )
    candles.append(
        make_candle(
            open_time=base + timedelta(minutes=8 * 15),
            open=99.5,
            high=101.0,
            low=99.2,
            close=100.8,
        )
    )
    return candles


def _structure(swing_high: float = 105.0) -> MarketStructure:
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return MarketStructure(
        symbol="BTCUSDT",
        timeframe="15m",
        last_swing_high=SwingPoint(
            symbol="BTCUSDT", timeframe="15m", price=swing_high,
            timestamp=now, type="high", index=2,
        ),
        last_swing_low=SwingPoint(
            symbol="BTCUSDT", timeframe="15m", price=99.5,
            timestamp=now, type="low", index=7,
        ),
        trend_direction="up",
    )


def _momentum(**updates: object) -> dict:
    value = {
        "rsi": 42.0,
        "rsi_prev": 39.0,
        "rsi_slope": 3.0,
        "stoch_k": 55.0,
        "stoch_d": 45.0,
        "macd_improving": True,
        "recovery_confirmation": True,
    }
    value.update(updates)
    return value


def _evaluate(**updates: object):
    return evaluate_long_entry_quality(
        candles=_timing_candles(),
        momentum=_momentum(**updates),
        trend={"ema_fast": 100.0},
        structure=_structure(),
        ob_list=[],
        fvg_list=[],
        atr=2.0,
    )


def test_pullback_and_bounce_are_required_for_long() -> None:
    result = _evaluate()
    assert result.allowed is True
    assert result.pullback_ok is True
    assert result.bounce_confirmation_ok is True
    assert result.recovery_ok is True


def test_rsi_near_overbought_with_stoch_is_rejected() -> None:
    result = _evaluate(rsi=68.0, rsi_prev=67.0, rsi_slope=1.0, stoch_k=78.0)
    assert result.allowed is False
    assert result.rsi_ok is False
    assert result.reason == "rsi_near_overbought_with_exhaustion"


def test_low_rsi_without_recovery_is_rejected() -> None:
    result = _evaluate(
        rsi=30.0,
        rsi_prev=30.0,
        rsi_slope=0.0,
        stoch_k=50.0,
        stoch_d=50.0,
        macd_improving=False,
        recovery_confirmation=False,
    )
    assert result.allowed is False
    assert result.recovery_ok is False
    assert result.reason == "rsi_low_without_recovery"


def test_extension_from_ema_is_rejected() -> None:
    result = evaluate_long_entry_quality(
        candles=_timing_candles(),
        momentum=_momentum(),
        trend={"ema_fast": 98.0},
        structure=_structure(),
        ob_list=[],
        fvg_list=[],
        atr=2.0,
    )
    assert result.allowed is False
    assert result.extension_ok is False
    assert result.reason == "long_price_extended_from_ema"


def test_price_near_recent_swing_high_is_rejected() -> None:
    result = evaluate_long_entry_quality(
        candles=_timing_candles(),
        momentum=_momentum(),
        trend={"ema_fast": 100.0},
        structure=_structure(swing_high=101.1),
        ob_list=[],
        fvg_list=[],
        atr=2.0,
    )
    assert result.allowed is False
    assert result.swing_high_distance_ok is False
    assert result.reason == "long_too_close_to_recent_swing_high"


def test_support_without_recent_touch_is_rejected() -> None:
    candles = _timing_candles()
    for index in range(len(candles) - 2):
        candles[index] = candles[index].model_copy(update={"low": 100.1})
    candles[-2] = candles[-2].model_copy(update={"low": 100.1})
    result = evaluate_long_entry_quality(
        candles=candles,
        momentum=_momentum(),
        trend={"ema_fast": 100.0},
        structure=_structure(),
        ob_list=[],
        fvg_list=[],
        atr=2.0,
    )
    assert result.allowed is False
    assert result.pullback_ok is False
    assert result.reason == "pullback_not_touched"


def _limit_decision() -> DecisionResult:
    now = datetime.now(timezone.utc)
    risk = RiskAssessment(
        allowed=True,
        max_position_size=10.0,
        max_risk_amount=200.0,
        stop_loss_price=95.0,
        take_profit_price=110.0,
        risk_reward_ratio=2.0,
    )
    entry = EntrySignal(
        symbol="BTCUSDT",
        direction="long",
        entry_price=100.0,
        entry_type="limit",
        timeframe="15m",
        confidence=0.85,
        reasons=["test"],
        stop_loss=95.0,
        take_profit=110.0,
        risk_reward=2.0,
        valid_until=now,
        pullback_confirmed=True,
    )
    return DecisionResult(
        symbol="BTCUSDT",
        source_candle_open_time=now,
        score=0.85,
        confidence=0.85,
        regime_check_passed=True,
        structure_alignment_passed=True,
        htf_bias_aligned=True,
        risk=risk,
        entry=entry,
        final_verdict=True,
        timestamp=now,
    )


@pytest.mark.asyncio
async def test_limit_fill_above_allowed_price_is_refused(mock_supabase, mock_redis) -> None:
    mock_redis.get_live_price = AsyncMock(
        return_value=(101.0, datetime.now(timezone.utc))
    )
    trader = PaperTrader(supabase=mock_supabase, redis=mock_redis)
    with pytest.raises(LimitNotFilledError):
        await trader.open_trade(_limit_decision())
    mock_supabase.insert_simulated_trade.assert_not_called()
