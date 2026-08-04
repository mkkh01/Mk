import asyncio
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime, timezone
from uuid import uuid4

from contracts.simulation import SimulatedTrade
from contracts.decision import DecisionResult, EntrySignal, RiskAssessment, StrategySignal
from analysis.result_formatter import ResultFormatter
from analysis.performance_analyzer import PerformanceAnalyzer

async def test_analysis():
    print("Starting Analysis Package Test...")
    
    # 1. Create mock data
    trade_id = uuid4()
    decision_id = uuid4()
    
    trade = SimulatedTrade(
        id=trade_id,
        decision_id=decision_id,
        symbol="BTCUSDT",
        direction="long",
        entry_price=50000.0,
        size=0.1,
        fee=5.0,
        slippage=1.0,
        opened_at=datetime.now(timezone.utc),
        closed_at=datetime.now(timezone.utc),
        pnl=100.0,
        status="closed",
        close_reason="tp",
        is_simulated=True
    )
    
    decision = DecisionResult(
        id=decision_id,
        symbol="BTCUSDT",
        source_candle_open_time=datetime.now(timezone.utc),
        score=0.85,
        confidence=0.9,
        regime_check_passed=True,
        structure_alignment_passed=True,
        htf_bias_aligned=True,
        risk=RiskAssessment(allowed=True, reason="None", max_position_size=0.1),
        entry=EntrySignal(
            symbol="BTCUSDT", 
            direction="long", 
            entry_price=50000.0,
            entry_type="market",
            timeframe="1h", 
            confidence=0.9,
            reasons=["Trend alignment"],
            stop_loss=49000.0,
            take_profit=52000.0,
            risk_reward=2.0,
            valid_until=datetime.now(timezone.utc)
        ),
        component_signals=[
            StrategySignal(
                symbol="BTCUSDT",
                timeframe="1h",
                strategy_name="TrendFollower",
                direction="long",
                raw_score=0.85,
                timestamp=datetime.now(timezone.utc),
                source_candle_open_time=datetime.now(timezone.utc)
            )
        ],
        final_verdict=True,
        rejection_reason="",
        timestamp=datetime.now(timezone.utc)
    )
    
    # 2. Test Formatter
    print("Testing Formatter...")
    log_output = ResultFormatter.format_trade_log(trade, decision)
    print(f"Log Output:\n{log_output}\n")
    assert "BTCUSDT" in log_output
    assert "TrendFollower" in log_output
    
    tg_output = ResultFormatter.format_trade_telegram(trade, decision)
    print(f"Telegram Output:\n{tg_output}\n")
    assert "BTCUSDT" in tg_output
    
    # 3. Test Analyzer
    print("Testing Analyzer...")
    trades = [trade]
    decisions = [decision]
    
    strat_stats = PerformanceAnalyzer.analyze_strategies(trades, decisions)
    print(f"Strategy Stats: {strat_stats}")
    assert strat_stats["TrendFollower"]["wins"] == 1
    
    rej_stats = PerformanceAnalyzer.analyze_rejections([decision])
    print(f"Rejection Stats: {rej_stats}")
    
    print("Analysis Package Test Completed Successfully!")

if __name__ == "__main__":
    asyncio.run(test_analysis())
