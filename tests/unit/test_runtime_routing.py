from __future__ import annotations

import inspect

from app.main import CTApplication, _normalise_day_trading_coin
from config.profiles import DAY_TRADING_TIMEFRAMES
from contracts.config import CoinConfig


def test_runtime_coin_normalization_is_always_day_trading() -> None:
    coin = CoinConfig(
        symbol="SOLUSDT",
        timeframes=["5m", "15m", "30m", "1h", "4h"],
        capital=100.0,
        risk_percent=1.0,
        is_active=True,
    )

    normalized = _normalise_day_trading_coin(coin)

    assert normalized.timeframes == list(DAY_TRADING_TIMEFRAMES)
    assert coin.timeframes != normalized.timeframes


def test_dispatch_keeps_primary_and_scalp_timeframes_separate() -> None:
    source = inspect.getsource(CTApplication._dispatch_candle_message)

    assert "if candle.timeframe in DAY_TRADING_TIMEFRAMES" in source
    assert "if candle.is_closed and candle.timeframe == \"5m\"" in source
    assert "_normalise_day_trading_coin(coin_config)" in source


def test_subscriber_uses_fixed_day_trading_channels_plus_scalp_trigger() -> None:
    source = inspect.getsource(CTApplication._run_orchestrator_subscriber_guarded)

    assert "for tf in (*DAY_TRADING_TIMEFRAMES, \"5m\")" in source
    assert "runtime_fetch_timeframes" not in source
