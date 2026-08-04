"""
File: tests/conftest.py
1. Single Responsibility: Shared pytest fixtures and synthetic candle builders.
2. Consumes: contracts.market, contracts.config.
3. Produces: fixtures used by every test file.
4. Downstream: tests/unit/* and tests/integration/*.
5. New Dependencies: pytest, pytest-asyncio.
6. Touches Section 6 bugs? No (only fixtures).
7. Tests: N/A (this IS the test infrastructure).
8. Logging: No.
9. Dependency Order: contracts -> tests/conftest.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from contracts.config import CoinConfig, SystemConfig
from contracts.market import Candle


# ---------------------------------------------------------------------------
# Timezone helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def make_dt(minutes: int, base: Optional[datetime] = None) -> datetime:
    """Build a UTC datetime `minutes` after `base` (default: 2024-01-01 00:00 UTC)."""
    base = base or datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(minutes=minutes)


# ---------------------------------------------------------------------------
# Candle builders
# ---------------------------------------------------------------------------
def make_candle(
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    open_time: datetime,
    open: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
    taker_buy_volume: Optional[float] = None,
    taker_sell_volume: Optional[float] = None,
    is_closed: bool = True,
    timeframe_minutes: int = 15,
) -> Candle:
    """Build a single Candle with derived taker volumes when not specified."""
    if taker_buy_volume is None:
        taker_buy_volume = volume * 0.5
    if taker_sell_volume is None:
        taker_sell_volume = volume - taker_buy_volume
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=timeframe_minutes),
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
        taker_buy_volume=taker_buy_volume,
        taker_sell_volume=taker_sell_volume,
        is_closed=is_closed,
    )


def bullish_seq(
    n: int = 30,
    start_price: float = 100.0,
    step: float = 1.0,
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    base_time: Optional[datetime] = None,
) -> list[Candle]:
    """Generate a clean bullish sequence (each candle closes higher)."""
    base = base_time or datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    tf_minutes = {"15m": 15, "1h": 60, "4h": 240}.get(timeframe, 15)
    candles: list[Candle] = []
    price = start_price
    for i in range(n):
        o = price
        c = price + step
        h = c + step * 0.5
        low = o - step * 0.2
        candles.append(
            make_candle(
                symbol=symbol,
                timeframe=timeframe,
                open_time=base + timedelta(minutes=i * tf_minutes),
                open=o, high=h, low=low, close=c,
                timeframe_minutes=tf_minutes,
            )
        )
        price = c
    return candles


def bearish_seq(
    n: int = 30,
    start_price: float = 200.0,
    step: float = 1.0,
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    base_time: Optional[datetime] = None,
) -> list[Candle]:
    """Generate a clean bearish sequence (each candle closes lower)."""
    base = base_time or datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    tf_minutes = {"15m": 15, "1h": 60, "4h": 240}.get(timeframe, 15)
    candles: list[Candle] = []
    price = start_price
    for i in range(n):
        o = price
        c = price - step
        h = o + step * 0.2
        low = c - step * 0.5
        candles.append(
            make_candle(
                symbol=symbol,
                timeframe=timeframe,
                open_time=base + timedelta(minutes=i * tf_minutes),
                open=o, high=h, low=low, close=c,
                timeframe_minutes=tf_minutes,
            )
        )
        price = c
    return candles


def high_sweep_seq(
    swing_high_price: float = 110.0,
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    base_time: Optional[datetime] = None,
) -> list[Candle]:
    """A sequence that ends in a high-sweep-then-reject-down (bearish reversal).

    Per Section 6 Bug 1, the resulting LiquiditySweep must have
    direction == "bearish".
    """
    base = base_time or datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    # Build a 25-candle bullish run that establishes a swing high, then a
    # single high-sweep candle whose wick pokes above the swing high but
    # which closes back below it.
    pre = bullish_seq(n=25, start_price=100.0, step=0.4, symbol=symbol,
                     timeframe=timeframe, base_time=base)
    # Force a candle near the end (but with padding) to be the swing high.
    # pre has 25 candles. We'll make index 19 the swing high.
    # index 19 has 5 candles after it (20, 21, 22, 23, 24).
    swing_idx = 19
    swing_candle = pre[swing_idx].model_copy(update={"high": swing_high_price})
    pre[swing_idx] = swing_candle
    
    # Ensure neighbors are lower.
    for j in range(swing_idx - 5, swing_idx + 6):
        if j != swing_idx and j < len(pre):
            pre[j] = pre[j].model_copy(update={"high": swing_high_price - 1.0})

    tf_minutes = {"15m": 15, "1h": 60, "4h": 240}.get(timeframe, 15)
    # Sweep candle: wick_high > swing_high_price but close < swing_high_price.
    # We place the sweep at the very end.
    last_candle = pre[-1]
    # To meet 0.6 strength threshold:
    # wick_size_factor = upper_wick / range
    # range = high - low
    # upper_wick = high - max(open, close)
    sweep_high = swing_high_price + 2.0
    sweep_open = swing_high_price - 0.1
    sweep_close = swing_high_price - 1.0
    sweep_low = sweep_close - 0.1
    # range = 3.1, upper_wick = 2.1, factor = 2.1/3.1 = 0.67
    sweep = make_candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=last_candle.open_time + timedelta(minutes=tf_minutes),
        open=sweep_open,
        high=sweep_high,
        low=sweep_low,
        close=sweep_close,
        volume=500.0,  # Ensure volume factor is 1.0
        timeframe_minutes=tf_minutes,
    )
    return pre + [sweep]


def low_sweep_seq(
    swing_low_price: float = 90.0,
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    base_time: Optional[datetime] = None,
) -> list[Candle]:
    """A sequence that ends in a low-sweep-then-reject-up (bullish reversal).

    Per Section 6 Bug 1, the resulting LiquiditySweep must have
    direction == "bullish".
    """
    base = base_time or datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    pre = bearish_seq(n=25, start_price=100.0, step=0.4, symbol=symbol,
                     timeframe=timeframe, base_time=base)
    # Establish swing low at index 19.
    swing_idx = 19
    swing_candle = pre[swing_idx].model_copy(update={"low": swing_low_price})
    pre[swing_idx] = swing_candle
    
    # Ensure neighbors are higher.
    for j in range(swing_idx - 5, swing_idx + 6):
        if j != swing_idx and j < len(pre):
            pre[j] = pre[j].model_copy(update={"low": swing_low_price + 1.0})

    tf_minutes = {"15m": 15, "1h": 60, "4h": 240}.get(timeframe, 15)
    last_candle = pre[-1]
    # To meet 0.6 strength threshold:
    # wick_size_factor = lower_wick / range
    # range = high - low
    # lower_wick = min(open, close) - low
    sweep_low = swing_low_price - 2.0
    sweep_open = swing_low_price + 0.1
    sweep_close = swing_low_price + 1.0
    sweep_high = sweep_close + 0.1
    # range = 3.1, lower_wick = 2.1, factor = 2.1/3.1 = 0.67
    sweep = make_candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=last_candle.open_time + timedelta(minutes=tf_minutes),
        open=sweep_open,
        high=sweep_high,
        low=sweep_low,
        close=sweep_close,
        volume=500.0,
        timeframe_minutes=tf_minutes,
    )
    return pre + [sweep]


def bos_bullish_seq(
    swing_high_price: float = 110.0,
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
) -> list[Candle]:
    """Sequence that produces a clear bullish BOS: an established swing high
    followed by a candle that closes above it and the next confirmation
    candle also closes above it.
    """
    pre = bullish_seq(n=15, start_price=100.0, step=0.3, symbol=symbol, timeframe=timeframe)
    # Force a clear swing high near the middle.
    pre[7] = pre[7].model_copy(update={"high": swing_high_price})
    tf_minutes = {"15m": 15, "1h": 60, "4h": 240}.get(timeframe, 15)
    base_time = pre[-1].open_time + timedelta(minutes=tf_minutes)
    # Break-out candle: closes above swing high.
    brk = make_candle(
        symbol=symbol, timeframe=timeframe, open_time=base_time,
        open=pre[-1].close, high=swing_high_price + 2.0, low=pre[-1].close,
        close=swing_high_price + 1.0, timeframe_minutes=tf_minutes,
    )
    # Confirmation candle: also closes above swing high.
    conf = make_candle(
        symbol=symbol, timeframe=timeframe,
        open_time=base_time + timedelta(minutes=tf_minutes),
        open=brk.close, high=brk.close + 0.5, low=brk.close - 0.3,
        close=brk.close + 0.2, timeframe_minutes=tf_minutes,
    )
    return pre + [brk, conf]


def choch_bearish_seq(
    swing_low_price: float = 90.0,
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
) -> list[Candle]:
    """Sequence that produces a bearish CHOCH after an uptrend: a clear higher
    low is established, then a candle closes below it, and the next candle
    confirms."""
    pre = bullish_seq(n=20, start_price=80.0, step=0.5, symbol=symbol, timeframe=timeframe)
    # Establish a higher low near the middle of the run.
    pre[10] = pre[10].model_copy(update={"low": swing_low_price})
    tf_minutes = {"15m": 15, "1h": 60, "4h": 240}.get(timeframe, 15)
    base_time = pre[-1].open_time + timedelta(minutes=tf_minutes)
    brk = make_candle(
        symbol=symbol, timeframe=timeframe, open_time=base_time,
        open=pre[-1].close, high=pre[-1].close, low=swing_low_price - 1.0,
        close=swing_low_price - 0.5, timeframe_minutes=tf_minutes,
    )
    conf = make_candle(
        symbol=symbol, timeframe=timeframe,
        open_time=base_time + timedelta(minutes=tf_minutes),
        open=brk.close, high=brk.close, low=brk.close - 0.5,
        close=brk.close - 0.3, timeframe_minutes=tf_minutes,
    )
    return pre + [brk, conf]


# ---------------------------------------------------------------------------
# Config fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def default_coin_config() -> CoinConfig:
    return CoinConfig(
        symbol="BTCUSDT",
        timeframes=["15m", "1h", "4h"],
        capital=10000.0,
        risk_percent=2.0,
        is_active=True,
    )


@pytest.fixture
def default_system_config() -> SystemConfig:
    return SystemConfig(
        telegram_bot_token="0000000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        supabase_url="https://example.supabase.co",
        supabase_key="example-key",
        redis_url="redis://localhost:6379/0",
        default_timeframes=["15m", "1h", "4h"],
        max_active_coins=15,
        simulation_mode=True,
    )


# ---------------------------------------------------------------------------
# Mock storage fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_supabase():
    """A MagicMock with async methods pre-configured. Individual tests should
    configure return values as needed."""
    supabase = MagicMock()
    supabase.upsert_candle = AsyncMock()
    supabase.upsert_candles = AsyncMock()
    supabase.upsert_decision = AsyncMock(return_value=True)
    supabase.insert_simulated_trade = AsyncMock(return_value=True)
    supabase.update_simulated_trade_closure = AsyncMock()
    supabase.fetch_closed_candles = AsyncMock(return_value=[])
    supabase.fetch_latest_candle = AsyncMock(return_value=None)
    supabase.fetch_open_trades = AsyncMock(return_value=[])
    supabase.fetch_recent_trades = AsyncMock(return_value=[])
    supabase.fetch_closed_trades = AsyncMock(return_value=[])
    supabase.count_open_trades = AsyncMock(return_value=0)
    supabase.get_checkpoint = AsyncMock(return_value=None)
    supabase.upsert_checkpoint = AsyncMock()
    supabase.upsert_coin = AsyncMock()
    supabase.fetch_coin = AsyncMock(return_value=None)
    supabase.fetch_all_coins = AsyncMock(return_value=[])
    supabase.delete_coin = AsyncMock()
    supabase.save_performance_snapshot = AsyncMock()
    supabase.apply_migrations = AsyncMock()
    return supabase


@pytest.fixture
def mock_redis():
    redis = MagicMock()
    redis.set_candle = AsyncMock()
    redis.get_candle = AsyncMock(return_value=None)
    redis.set_live_price = AsyncMock()
    redis.get_live_price = AsyncMock(return_value=None)
    redis.set_checkpoint = AsyncMock()
    redis.get_checkpoint = AsyncMock(return_value=None)
    redis.delete_checkpoint = AsyncMock()
    redis.touch_last_message = AsyncMock()
    redis.get_last_message = AsyncMock(return_value=None)
    redis.set_engine_running = AsyncMock()
    redis.get_engine_running = AsyncMock(return_value=False)
    redis.publish_new_candle = AsyncMock()
    redis.ping = AsyncMock(return_value=True)
    return redis
