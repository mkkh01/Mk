
import pytest
from datetime import datetime
from engine.risk import assess_risk, check_drawdown
from contracts.decision import StrategySignal
from contracts.config import CoinConfig

def test_check_drawdown_cold_start():
    # peak_pnl = 0, current_pnl = 0 -> Should allow first trade
    assert check_drawdown(current_pnl=0.0, peak_pnl=0.0, new_trade_risk=10.0) is True

def test_check_drawdown_losing_cold_start():
    # peak_pnl = 0, current_pnl = -5.0 -> Should reject further risk
    assert check_drawdown(current_pnl=-5.0, peak_pnl=0.0, new_trade_risk=10.0) is False

def test_assess_risk_cold_start_integration():
    signal = StrategySignal(
        symbol="BTCUSDT",
        direction="long",
        timeframe="1h",
        raw_score=0.8,
        strategy_name="test",
        timestamp=datetime.utcnow(),
        source_candle_open_time=datetime.utcnow()
    )
    coin_config = CoinConfig(
        symbol="BTCUSDT",
        capital=1000.0,
        risk_percent=2.0,
        timeframes=["1h", "4h", "15m"]
    )
    portfolio_state = {
        "current_exposure": 0.0,
        "current_pnl": 0.0,
        "peak_pnl": 0.0,
        "open_trade_count": 0,
        "current_price": 50000.0
    }
    atr = 500.0
    
    assessment = assess_risk(
        signal=signal,
        confidence=0.8,
        coin_config=coin_config,
        portfolio_state=portfolio_state,
        atr=atr
    )
    
    assert assessment.allowed is True
    assert assessment.reason is None
    assert assessment.risk_reward_ratio >= 1.5
