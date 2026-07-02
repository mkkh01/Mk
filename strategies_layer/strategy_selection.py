import logging
from analysis_layer.market_regime import evaluate as evaluate_regime
from strategies_layer.strategy_trend import compute as trend_compute
from strategies_layer.strategy_liquidity import compute as liquidity_compute

logger = logging.getLogger("strategy_selection")

# Weight tables by regime
REGIME_WEIGHTS = {
    "TREND_UP":          {"trend": 0.75, "liquidity": 0.25},
    "TREND_DOWN":        {"trend": 0.70, "liquidity": 0.30},
    "RANGE":             {"trend": 0.30, "liquidity": 0.70},
    "HIGH_VOLATILITY":   {"trend": 0.10, "liquidity": 0.10},
    "LOW_VOLATILITY":    {"trend": 0.40, "liquidity": 0.60},
    "DISTRIBUTION":      {"trend": 0.20, "liquidity": 0.20},
    "CAPITULATION":      {"trend": 0.10, "liquidity": 0.10},
    "BREAKOUT":          {"trend": 0.80, "liquidity": 0.20},
    "PULLBACK":          {"trend": 0.50, "liquidity": 0.50},
}

def choose(candles, order_book, asset_params):
    """
    Main strategy selection engine.
    Evaluates all strategies, applies regime-based weighting,
    and returns the final signal decision.
    
    Returns: signal dict or None
    """
    regime = evaluate_regime(candles)
    regime_name = regime["regime"]
    
    # Skip trading in dangerous regimes
    if regime_name in ("HIGH_VOLATILITY", "CAPITULATION"):
        logger.info(f"[{candles[-1]['close_time'] if candles else '?'}] Skipping - regime: {regime_name}")
        return None, regime
    
    weights = REGIME_WEIGHTS.get(regime_name, {"trend": 0.5, "liquidity": 0.5})
    
    # ── Run Strategies ──
    trend_signal = trend_compute(candles, asset_params)
    liq_signal = liquidity_compute(order_book, candles, asset_params)
    
    # ── Combine Signals ──
    final_signal = None
    combined_confidence = 0
    
    if trend_signal and liq_signal:
        # Both agree
        if trend_signal["signal"] == liq_signal["signal"]:
            final_signal = trend_signal.copy()
            combined_confidence = (
                trend_signal["confidence"] * weights["trend"] +
                liq_signal["confidence"] * weights["liquidity"]
            )
            final_signal["confidence"] = min(round(combined_confidence), 95)
            final_signal["reason"] = f"{trend_signal['reason']} | +تأكيد سيولة"
            final_signal["strategy"] = f"TREND+LIQUIDITY"
        else:
            # Conflict - go with higher weight strategy
            if weights["trend"] >= weights["liquidity"]:
                final_signal = trend_signal
                final_signal["strategy"] = "TREND"
            else:
                final_signal = liq_signal
                final_signal["strategy"] = "LIQUIDITY"
    
    elif trend_signal:
        final_signal = trend_signal.copy()
        final_signal["confidence"] = round(final_signal["confidence"] * weights["trend"])
        final_signal["strategy"] = "TREND"
    
    elif liq_signal:
        final_signal = liq_signal.copy()
        final_signal["confidence"] = round(final_signal["confidence"] * weights["liquidity"])
        final_signal["strategy"] = "LIQUIDITY"
    
    # Minimum confidence threshold
    if final_signal and final_signal["confidence"] < 40:
        logger.info(f"Signal below confidence threshold ({final_signal['confidence']}%)")
        return None, regime
    
    if final_signal:
        final_signal["regime"] = regime_name
        final_signal["regime_data"] = regime
    
    return final_signal, regime