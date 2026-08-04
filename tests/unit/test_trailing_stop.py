import pytest
from uuid import uuid4
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock

from contracts.simulation import SimulatedTrade
from contracts.market import Candle
from simulation.paper_trade import PaperTrader

@pytest.mark.asyncio
async def test_compute_initial_risk_regression():
    """
    Test that _compute_initial_risk uses the current stop_loss,
    which is the bug we need to fix.
    """
    # 1. Setup a trade
    trade = SimulatedTrade(
        decision_id=uuid4(),
        symbol="BTCUSDT",
        direction="long",
        entry_price=100.0,
        size=1.0,
        fee=0.1,
        slippage=0.05,
        opened_at=datetime.now(timezone.utc),
        stop_loss=97.0,  # Initial risk = 3.0
        atr_at_entry=2.0
    )
    
    # 2. Check initial risk
    risk = PaperTrader._compute_initial_risk(trade)
    assert risk == 3.0
    
    # 3. Move stop loss (simulating a trailing move)
    trade.stop_loss = 99.0
    
    # 4. Check risk again - should be 3.0 now (FIXED)
    # Even if stop_loss moves, initial_stop_loss stays at 97.0
    trade.initial_stop_loss = 97.0
    risk_after_move = PaperTrader._compute_initial_risk(trade)
    
    assert risk_after_move == 3.0 

@pytest.mark.asyncio
async def test_trailing_stop_activation_threshold_fixed():
    """
    Test how the bug affects activation.
    """
    supabase = MagicMock()
    trader = PaperTrader(supabase)
    
    # Trade with entry=100, SL=97 -> risk=3.
    # Activation threshold = 3 * 1.5 = 4.5.
    # Price needs to reach 104.5.
    trade = SimulatedTrade(
        decision_id=uuid4(),
        symbol="BTCUSDT",
        direction="long",
        entry_price=100.0,
        size=1.0,
        fee=0.1,
        slippage=0.05,
        opened_at=datetime.now(timezone.utc),
        stop_loss=97.0,
        atr_at_entry=2.0,
        highest_price=100.0
    )
    
    # Candle at 105.0 -> should activate and move SL.
    now = datetime.now(timezone.utc)
    candle = Candle(
        symbol="BTCUSDT", 
        timeframe="15m", 
        open=100.0, 
        high=105.0, 
        low=99.0, 
        close=104.0, 
        volume=10.0, 
        open_time=now,
        close_time=now,
        taker_buy_volume=5.0,
        taker_sell_volume=5.0,
        is_closed=True
    )
    
    # Mock supabase update
    trader._supabase.update_simulated_trade_trailing = AsyncMock()
    
    updated = await trader.update_trailing_stop(trade, candle)
    assert updated is not None
    assert updated.stop_loss > 97.0
    
    # Now, if we move SL to 101.0, risk should still be 3.0
    trade.initial_stop_loss = 97.0
    trade.stop_loss = 101.0
    risk = PaperTrader._compute_initial_risk(trade)
    assert risk == 3.0
    
    # Next update should move SL again!
    candle2 = Candle(
        symbol="BTCUSDT", 
        timeframe="15m", 
        open=104.0, 
        high=110.0, 
        low=103.0, 
        close=109.0, 
        volume=10.0, 
        open_time=now,
        close_time=now,
        taker_buy_volume=5.0,
        taker_sell_volume=5.0,
        is_closed=True
    )
    updated2 = await trader.update_trailing_stop(trade, candle2)
    assert updated2 is not None
    assert updated2.stop_loss > 101.0

@pytest.mark.asyncio
async def test_compute_atr_async_uses_correct_timeframe():
    """
    Test that _compute_atr_async uses the provided timeframe.
    """
    supabase = MagicMock()
    trader = PaperTrader(supabase)
    
    trader._supabase.fetch_closed_candles = AsyncMock(return_value=[])
    
    await trader._compute_atr_async("BTCUSDT", "1h")
    
    trader._supabase.fetch_closed_candles.assert_called_once()
    args, kwargs = trader._supabase.fetch_closed_candles.call_args
    assert args[1] == "1h"
