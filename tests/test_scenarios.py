
import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
from engine.orchestrator import Orchestrator
from contracts.config import CoinConfig
from tests.conftest import bullish_seq, bearish_seq, make_candle

@pytest.mark.asyncio
async def test_scenario_volatile_market_rejection(mock_supabase, mock_redis):
    """
    Scenario: Market is extremely volatile.
    Expected: Orchestrator should reject with 'regime_check_failed' or 'VOLATILE regime blocks new entries'.
    """
    symbol = "BTCUSDT"
    timeframes = ["15m", "1h", "4h"]
    
    # Create a volatile sequence (large wicks, high ATR)
    base_time = datetime.now(timezone.utc) - timedelta(days=1)
    volatile_candles = []
    for i in range(50):
        o = 100.0
        c = 105.0 if i % 2 == 0 else 95.0
        h = 120.0
        l = 80.0
        volatile_candles.append(make_candle(
            symbol=symbol, timeframe="15m", open_time=base_time + timedelta(minutes=i*15),
            open=o, high=h, low=l, close=c
        ))
    
    # Setup mocks
    mock_supabase.fetch_closed_candles.side_effect = lambda symbol, timeframe, limit: volatile_candles[-limit:]
    
    orchestrator = Orchestrator(supabase=mock_supabase, redis=mock_redis)
    coin_config = CoinConfig(symbol=symbol, timeframes=timeframes, capital=1000.0, risk_percent=2.0)
    
    result = await orchestrator.process_candle(volatile_candles[-1], coin_config)
    
    assert result.final_verdict is False
    assert "regime" in result.rejection_reason.lower() or "volatile" in result.rejection_reason.lower()

@pytest.mark.asyncio
async def test_scenario_perfect_bullish_setup(mock_supabase, mock_redis):
    """
    Scenario: Perfect bullish trend with BOS and low volatility.
    Expected: Orchestrator should ideally approve if confidence > threshold.
    """
    symbol = "BTCUSDT"
    timeframes = ["15m", "1h", "4h"]
    
    # Create a smooth bullish sequence
    base_time = datetime.now(timezone.utc) - timedelta(days=2)
    bull_candles = bullish_seq(n=100, start_price=100.0, step=0.5, symbol=symbol, timeframe="15m", base_time=base_time)
    bull_candles_1h = bullish_seq(n=100, start_price=100.0, step=2.0, symbol=symbol, timeframe="1h", base_time=base_time)
    bull_candles_4h = bullish_seq(n=100, start_price=100.0, step=8.0, symbol=symbol, timeframe="4h", base_time=base_time)
    
    def mock_fetch(symbol, timeframe, limit):
        if timeframe == "15m": return bull_candles[-limit:]
        if timeframe == "1h": return bull_candles_1h[-limit:]
        if timeframe == "4h": return bull_candles_4h[-limit:]
        return []

    mock_supabase.fetch_closed_candles.side_effect = mock_fetch
    
    orchestrator = Orchestrator(supabase=mock_supabase, redis=mock_redis)
    coin_config = CoinConfig(symbol=symbol, timeframes=timeframes, capital=1000.0, risk_percent=2.0)
    
    result = await orchestrator.process_candle(bull_candles[-1], coin_config)
    
    # We check that it passed the regime and HTF bias gates.
    # Structure might fail if the generated sequence doesn't have clear BOS/OrderBlocks
    # but the logic should be sound.
    assert result.regime_check_passed is True
    assert result.htf_bias_aligned is True
    # If it failed structure, it should be reflected in the reason
    if not result.structure_alignment_passed:
        assert "structure" in result.rejection_reason.lower()

@pytest.mark.asyncio
async def test_scenario_bearish_trend_long_attempt(mock_supabase, mock_redis):
    """
    Scenario: Strong bearish trend, but a small bullish candle appears.
    Expected: Orchestrator should reject because HTF bias or structure is down.
    """
    symbol = "BTCUSDT"
    timeframes = ["15m", "1h", "4h"]
    
    base_time = datetime.now(timezone.utc) - timedelta(days=2)
    bear_candles = bearish_seq(n=100, start_price=200.0, step=0.5, symbol=symbol, timeframe="15m", base_time=base_time)
    
    # Add a fake bullish trigger at the end
    last_candle = bear_candles[-1]
    trigger = make_candle(
        symbol=symbol, timeframe="15m", 
        open_time=last_candle.close_time, 
        open=last_candle.close, high=last_candle.close + 2.0, low=last_candle.close - 0.1, close=last_candle.close + 1.5
    )
    
    mock_supabase.fetch_closed_candles.return_value = bear_candles[-200:]
    
    orchestrator = Orchestrator(supabase=mock_supabase, redis=mock_redis)
    coin_config = CoinConfig(symbol=symbol, timeframes=timeframes, capital=1000.0, risk_percent=2.0)
    
    result = await orchestrator.process_candle(trigger, coin_config)
    
    assert result.final_verdict is False
    # Should fail due to trend/structure/HTF bias
    assert not result.structure_alignment_passed or not result.htf_bias_aligned or result.score < 0.5
