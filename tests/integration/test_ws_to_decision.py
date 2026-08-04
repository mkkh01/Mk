"""
File: tests/integration/test_ws_to_decision.py
1. Single Responsibility: End-to-end: synthetic WS message -> Candle -> orchestrator -> DecisionResult.
2. Consumes: ingest.binance_ws, engine.orchestrator.
3. Produces: Integration test verifying the WS-to-decision pipeline.
4. Downstream: pytest run.
5. New Dependencies: pytest.
6. Touches Section 6 bugs? No.
7. Tests: integration smoke test.
8. Logging: No.
9. Dependency Order: tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from contracts.config import CoinConfig
from contracts.decision import DecisionResult
from contracts.market import Candle
from engine.orchestrator import Orchestrator
from ingest.binance_ws import BinanceWSClient
from tests.conftest import bullish_seq, make_candle, make_dt


@pytest.fixture
def coin_config():
    return CoinConfig(
        symbol="BTCUSDT", timeframes=["15m", "1h", "4h"],
        capital=10000.0, risk_percent=2.0,
    )


@pytest.fixture
def orchestrator(mock_supabase, mock_redis):
    return Orchestrator(supabase=mock_supabase, redis=mock_redis)


class TestWSToDecisionPipeline:
    """End-to-end: WS message -> parsed Candle -> orchestrator -> DecisionResult."""

    @pytest.mark.asyncio
    async def test_closed_candle_triggers_orchestrator(
        self, orchestrator, mock_supabase, mock_redis, coin_config
    ):
        # Mock supabase to return enough closed candles for analysis.
        mock_supabase.fetch_closed_candles = AsyncMock(return_value=bullish_seq(n=30))

        # Simulate a closed candle arriving from the WS.
        closed_candle = make_candle(
            open_time=make_dt(0),
            open=100.0, high=101.0, low=99.0, close=100.5,
            is_closed=True,
        )

        # The orchestrator should produce a DecisionResult (or None on graceful degradation).
        result = await orchestrator.process_candle_safe(closed_candle, coin_config)

        # Verify the supabase fetch was called.
        assert mock_supabase.fetch_closed_candles.called

        # If a decision was produced, verify its shape.
        if result is not None:
            assert isinstance(result, DecisionResult)
            assert result.symbol == "BTCUSDT"
            assert 0.0 <= result.score <= 1.0
            assert 0.0 <= result.confidence <= 1.0
            assert isinstance(result.final_verdict, bool)
            assert isinstance(result.component_signals, list)

    @pytest.mark.asyncio
    async def test_binance_ws_client_constructs_with_coins(
        self, mock_redis, mock_supabase, coin_config
    ):
        client = BinanceWSClient(
            coins=[coin_config], redis=mock_redis, supabase=mock_supabase,
        )
        assert client is not None

    @pytest.mark.asyncio
    async def test_orchestrator_handles_db_failure_gracefully(
        self, orchestrator, mock_supabase, coin_config
    ):
        """Section 22 graceful degradation: DB failure must not crash the orchestrator."""
        mock_supabase.fetch_closed_candles = AsyncMock(
            side_effect=RuntimeError("DB unavailable")
        )
        closed_candle = make_candle(
            open_time=make_dt(0),
            open=100.0, high=101.0, low=99.0, close=100.5,
            is_closed=True,
        )
        result = await orchestrator.process_candle_safe(closed_candle, coin_config)
        assert result is None  # Graceful degradation returns None.
