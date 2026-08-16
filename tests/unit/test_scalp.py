from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from config.profiles import (
    ALL_MONITORED_TIMEFRAMES,
    SCALP_TIMEFRAMES,
    SWING_TIMEFRAMES,
    fixed_timeframes,
    runtime_fetch_timeframes,
)
from engine.scalp import ScalpMonitor
from monitoring.health_manager import HealthManager, HealthStatus
from tests.conftest import make_candle


def test_fixed_timeframes_cover_both_profiles():
    assert tuple(fixed_timeframes()) == ALL_MONITORED_TIMEFRAMES
    assert set(SWING_TIMEFRAMES).issubset(ALL_MONITORED_TIMEFRAMES)
    assert set(SCALP_TIMEFRAMES).issubset(ALL_MONITORED_TIMEFRAMES)


def test_runtime_fetch_timeframes_is_additive_and_does_not_mutate_swing():
    swing = list(SWING_TIMEFRAMES)
    fetched = runtime_fetch_timeframes(swing)

    assert tuple(swing) == SWING_TIMEFRAMES
    assert fetched[:len(SWING_TIMEFRAMES)] == SWING_TIMEFRAMES
    assert set(SCALP_TIMEFRAMES).issubset(fetched)


def test_balanced_reversal_requires_strong_5m_and_15m_bullish_structure():
    assert ScalpMonitor._is_balanced_reversal(
        {"direction": "bullish", "strength": 0.70},
        {"direction": "bullish", "strength": 0.60},
    )
    assert not ScalpMonitor._is_balanced_reversal(
        {"direction": "bullish", "strength": 0.70},
        {"direction": "bullish", "strength": 0.40},
    )


def test_scalp_intrabar_stop_is_capped_at_configured_stop_price():
    monitor = ScalpMonitor(None)
    opened_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    exit_decision = monitor.evaluate_exit(
        entry_price=100.0,
        current_price=99.0,
        opened_at=opened_at,
        low_price=98.0,
        high_price=101.0,
    )

    assert exit_decision.status == "stop_loss"
    assert exit_decision.exit_price == pytest.approx(99.75)
    assert exit_decision.gross_pnl_pct == pytest.approx(-0.0025)
    assert exit_decision.net_pnl_pct == pytest.approx(-0.0035)


def test_scalp_exit_uses_net_target_and_time_limit():
    opened = datetime(2026, 1, 1, tzinfo=timezone.utc)
    target = ScalpMonitor.evaluate_exit(
        entry_price=100.0,
        current_price=100.6,
        opened_at=opened,
        now=opened + timedelta(minutes=5),
    )
    timed = ScalpMonitor.evaluate_exit(
        entry_price=100.0,
        current_price=100.05,
        opened_at=opened,
        now=opened + timedelta(minutes=45),
    )

    assert target.status == "take_profit"
    assert target.net_pnl_pct > 0
    assert timed.status == "time_exit"


@pytest.mark.asyncio
async def test_limit_not_filled_is_operational_not_system_error():
    manager = HealthManager()
    await manager.record_limit_not_filled("ATOMUSDT", "limit moved away")

    stats = await manager.get_stats()

    assert stats["limit_not_filled_count"] == 1
    assert stats["operational_rejection_reasons"] == {"limit_not_filled": 1}
    assert stats["errors_count"] == 0


@pytest.mark.asyncio
async def test_health_manager_exposes_last_error_and_downgrades_health():
    manager = HealthManager()
    await manager.record_error("app.main", "TestError", "synthetic test diagnostic")

    stats = await manager.get_stats()
    health = await manager.get_overall_health()

    assert stats["errors_count"] == 1
    assert stats["last_error"]["error_type"] == "TestError"
    assert health["status"] == HealthStatus.WARNING


@pytest.mark.asyncio
async def test_scalp_approved_signal_and_entry_are_separate_counters():
    manager = HealthManager()
    await manager.record_scalp_decision(
        {
            "symbol": "BTCUSDT",
            "approved": True,
            "score": 0.80,
            "confidence": 0.75,
        }
    )
    await manager.record_scalp_entry_block("position_already_open")
    stats = (await manager.get_stats())["scalp"]

    assert stats["approved"] == 1
    assert stats["entries"] == 0
    assert stats["entry_block_reasons"] == {"position_already_open": 1}


@pytest.mark.asyncio
async def test_scalp_trade_ledger_tracks_open_and_successful_closed_trade():
    manager = HealthManager()
    await manager.record_scalp_entry(
        {
            "id": "scalp-BTC-1",
            "symbol": "BTCUSDT",
            "entry_price": 100.0,
            "current_price": 100.0,
            "status": "open",
            "paper_only": True,
        }
    )
    await manager.record_scalp_position(
        {
            "id": "scalp-BTC-1",
            "symbol": "BTCUSDT",
            "entry_price": 100.0,
            "current_price": 100.3,
            "net_pnl_pct": 0.002,
            "status": "open",
            "paper_only": True,
        }
    )
    await manager.record_scalp_close(
        {
            "id": "scalp-BTC-1",
            "symbol": "BTCUSDT",
            "entry_price": 100.0,
            "exit_price": 100.4,
            "exit_status": "take_profit",
            "net_pnl_pct": 0.003,
            "paper_only": True,
        }
    )
    stats = (await manager.get_stats())["scalp"]

    assert stats["entries"] == 1
    assert stats["open_trades"] == []
    assert stats["wins"] == 1
    assert stats["losses"] == 0
    assert stats["closed_trades"][0]["exit_status"] == "take_profit"
    assert stats["net_pnl_pct"] == pytest.approx(0.003)


def test_scalp_volume_state_distinguishes_bullish_bearish_neutral_and_missing():
    now = datetime.now(timezone.utc)
    bullish = make_candle(symbol="BTCUSDT", timeframe="5m", open_time=now - timedelta(minutes=5), open=100.0, high=102.0, low=99.0, close=101.0, volume=100.0)
    bearish = make_candle(symbol="BTCUSDT", timeframe="5m", open_time=now - timedelta(minutes=5), open=100.0, high=101.0, low=98.0, close=99.0, volume=100.0)
    neutral = make_candle(symbol="BTCUSDT", timeframe="5m", open_time=now - timedelta(minutes=5), open=100.0, high=101.0, low=99.0, close=100.0, volume=100.0)

    assert ScalpMonitor._classify_volume([bullish], {"cvd_slope": 3.0, "delta": 3.0}) == "bullish"
    assert ScalpMonitor._classify_volume([bearish], {"cvd_slope": -3.0, "delta": -3.0}) == "bearish"
    assert ScalpMonitor._classify_volume([neutral], {"cvd_slope": 0.1, "delta": -0.1}) == "neutral"
    assert ScalpMonitor._classify_volume([], {}) == "missing"
