"""
File: tests/unit/test_storage.py
1. Single Responsibility: Verify storage/supabase.py and storage/redis_cache.py basic behavior.
2. Consumes: storage.supabase, storage.redis_cache, contracts.*.
3. Produces: Tests for row->contract mapping and idempotency expectations.
4. Downstream: pytest run.
5. New Dependencies: pytest.
6. Touches Section 6 bugs? No.
7. Tests: smoke tests for storage modules (no real DB required).
8. Logging: No.
9. Dependency Order: contracts -> storage -> tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from contracts.config import CoinConfig
from contracts.decision import DecisionResult, RiskAssessment
from contracts.market import Candle
from contracts.simulation import SimulatedTrade
from storage.supabase import (
    SupabaseClient,
    _candle_from_row,
    _coin_from_row,
    _decision_from_row,
    _trade_from_row,
)
from storage.redis_cache import RedisCache
from tests.conftest import make_candle, make_dt


# ---------------------------------------------------------------------------
# Row -> contract mappers (no DB needed)
# ---------------------------------------------------------------------------
class TestCandleFromRow:
    def test_maps_row_to_candle(self):
        row = {
            "symbol": "BTCUSDT", "timeframe": "15m",
            "open_time": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "close_time": datetime(2024, 1, 1, 0, 15, tzinfo=timezone.utc),
            "open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0,
            "volume": 100.0, "taker_buy_volume": 60.0, "taker_sell_volume": 40.0,
            "is_closed": True,
        }
        candle = _candle_from_row(row)
        assert isinstance(candle, Candle)
        assert candle.symbol == "BTCUSDT"
        assert candle.open == 100.0
        assert candle.is_closed is True


class TestDecisionFromRow:
    def test_maps_row_to_decision(self):
        row = {
            "id": uuid4(),
            "symbol": "BTCUSDT",
            "source_candle_open_time": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "score": 0.85,
            "confidence": 0.80,
            "regime_check_passed": True,
            "structure_alignment_passed": True,
            "htf_bias_aligned": True,
            "risk_allowed": True,
            "risk_reason": None,
            "final_verdict": True,
            "rejection_reason": None,
            "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        }
        decision = _decision_from_row(row)
        assert isinstance(decision, DecisionResult)
        assert decision.symbol == "BTCUSDT"
        assert decision.score == 0.85
        assert decision.final_verdict is True
        assert decision.risk.allowed is True


class TestTradeFromRow:
    def test_maps_row_to_simulated_trade(self):
        decision_id = uuid4()
        row = {
            "id": uuid4(),
            "decision_id": decision_id,
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry_price": 100.0,
            "size": 1.5,
            "fee": 0.15,
            "slippage": 0.075,
            "opened_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "closed_at": None,
            "pnl": None,
            "status": "open",
            "close_reason": None,
            "is_simulated": True,
            "stop_loss": 95.0,
            "take_profit": 110.0,
        }
        trade = _trade_from_row(row)
        assert isinstance(trade, SimulatedTrade)
        assert trade.decision_id == decision_id
        assert trade.direction == "long"
        assert trade.is_simulated is True


class TestCoinFromRow:
    def test_maps_row_to_coin_config(self):
        row = {
            "symbol": "BTCUSDT",
            "timeframes": ["15m", "1h", "4h"],
            "capital": 10000.0,
            "risk_percent": 2.0,
            "is_active": True,
        }
        coin = _coin_from_row(row)
        assert isinstance(coin, CoinConfig)
        assert coin.symbol == "BTCUSDT"
        assert coin.timeframes == ["15m", "1h", "4h"]


# ---------------------------------------------------------------------------
# Client lifecycle (no real DB required)
# ---------------------------------------------------------------------------
class TestSupabaseClientLifecycle:
    def test_constructs_without_connecting(self):
        client = SupabaseClient(dsn="postgresql://nobody@nowhere/db")
        assert client._pool is None

    def test_require_pool_raises_before_connect(self):
        client = SupabaseClient(dsn="postgresql://nobody@nowhere/db")
        with pytest.raises(RuntimeError):
            client._require_pool()


class TestRedisCacheLifecycle:
    def test_constructs_without_connecting(self):
        cache = RedisCache(url="redis://localhost:6379/0")
        assert cache._client is None

    def test_require_raises_before_connect(self):
        cache = RedisCache(url="redis://localhost:6379/0")
        with pytest.raises(RuntimeError):
            cache._require()


# ---------------------------------------------------------------------------
# Idempotency expectations (verifying the SQL uses ON CONFLICT)
# ---------------------------------------------------------------------------
class TestIdempotencySQL:
    """Section 4 idempotency rules are enforced at the DB level via ON CONFLICT.
    These tests inspect the SupabaseClient source to verify the SQL contains
    the right ON CONFLICT clauses."""

    def test_upsert_candle_uses_on_conflict(self):
        import inspect
        source = inspect.getsource(SupabaseClient.upsert_candle)
        assert "ON CONFLICT" in source.upper()
        assert "(symbol, timeframe, open_time)" in source

    def test_upsert_decision_uses_on_conflict_do_update(self):
        import inspect
        source = inspect.getsource(SupabaseClient.upsert_decision)
        assert "ON CONFLICT" in source.upper()
        assert "DO UPDATE" in source.upper()

    def test_insert_simulated_trade_uses_on_conflict(self):
        import inspect
        source = inspect.getsource(SupabaseClient.insert_simulated_trade)
        assert "ON CONFLICT" in source.upper()
        assert "decision_id" in source
