"""Strategy Selection Engine - Decides optimal strategy based on market conditions."""
from config import MarketRegime

def select_strategy(regime_data: dict, donchian_signal: dict | None, order_flow_signal: dict | None) -> dict | None:
    """
    Select the best strategy for current market conditions.
    
    Decision tree:
    - TREND_UP/BREAKOUT + Donchian breakout → Trend Following (highest priority)
    - TREND_UP with no Donchian + strong Order Flow → Liquidity
    - CAPITULATION → No trade (wait for stabilization)
    - RANGE/HIGH_VOLATILITY → Order Flow only
    - TREND_DOWN + Donchian → Trend Following (SHORT)
    - LOW_VOLATILITY → Wait for clearer signals
    """
    regime = regime_data.get('regime', '')
    confidence = regime_data.get('confidence', 0)
    
    decision = {
        'selected_strategy': None,
        'signal': None,
        'reason': ''
    }
    
    # Blocker: extreme conditions
    if regime == MarketRegime.CAPITULATION:
        decision['reason'] = f"No trade: CAPITULATION detected. Waiting for stabilization."
        return decision
    
    if regime == MarketRegime.DISTRIBUTION:
        decision['reason'] = f"No trade: DISTRIBUTION phase. Bearish divergence."
        return decision
    
    # Priority 1: Trend Following in trending markets
    if regime in [MarketRegime.TREND_UP, MarketRegime.BREAKOUT] and donchian_signal:
        decision['selected_strategy'] = donchian_signal['strategy']
        decision['signal'] = donchian_signal
        decision['reason'] = f"Priority 1 — Trend Following selected: {regime} with confirmed Donchian breakout."
        return decision
    
    if regime == MarketRegime.TREND_DOWN and donchian_signal:
        decision['selected_strategy'] = donchian_signal['strategy']
        decision['signal'] = donchian_signal
        decision['reason'] = f"Trend Following (SHORT) selected: {regime} with Donchian breakdown."
        return decision
    
    # Priority 2: Order Flow for range/high vol
    if regime in [MarketRegime.HIGH_VOLATILITY, MarketRegime.RANGE] and order_flow_signal:
        decision['selected_strategy'] = order_flow_signal['strategy']
        decision['signal'] = order_flow_signal
        decision['reason'] = f"Priority 2 — Order Flow selected: {regime} with confirmed order flow signal."
        return decision
    
    # Priority 3: Order Flow as secondary in trending
    if regime == MarketRegime.TREND_UP and order_flow_signal and not donchian_signal:
        if order_flow_signal.get('order_flow', {}).get('volume_ratio', 1) > 2.5:
            decision['selected_strategy'] = order_flow_signal['strategy']
            decision['signal'] = order_flow_signal
            decision['reason'] = f"Strong order flow override in TREND_UP (no Donchian breakout)."
            return decision
    
    # Default: No trade
    if regime == MarketRegime.LOW_VOLATILITY:
        decision['reason'] = f"No trade: LOW_VOLATILITY. Insufficient signal strength. Confidence: {confidence:.2f}"
    else:
        decision['reason'] = f"No trade: No strategy matched for regime {regime}. Donchian: {'Yes' if donchian_signal else 'No'}, OrderFlow: {'Yes' if order_flow_signal else 'No'}"
    
    return decision
