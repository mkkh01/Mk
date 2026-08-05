"""
File: config/thresholds_dynamic.py
Dynamic threshold resolution logic based on market volatility (ATR) and regime.
"""
from typing import Literal
from config import thresholds

def resolve_swing_size(atr_pct: float) -> float:
    """max(0.1%, 0.5 * ATR%)"""
    return max(thresholds.MIN_SWING_SIZE_PCT, 0.5 * atr_pct)

def resolve_ob_impulse(atr_pct: float) -> float:
    """max(0.2%, 1.0 * ATR%)"""
    return max(thresholds.OB_MIN_IMPULSE_PCT, 1.0 * atr_pct)

def resolve_fvg_gap(atr_pct: float) -> float:
    """max(0.05%, 0.3 * ATR%)"""
    return max(thresholds.FVG_MIN_GAP_PCT, 0.3 * atr_pct)

def resolve_sl_multiplier(regime_name: str) -> float:
    """
    Trending: 1.6
    Ranging: 2.2
    Volatile: 2.4
    Default: 1.8
    """
    rn = regime_name.lower()
    if rn == "trending":
        return 1.6
    elif rn == "ranging":
        return 2.2
    elif rn == "volatile":
        return 2.4
    return thresholds.VOLATILITY_ATR_MULTIPLIER_SL

def resolve_tp_multiplier(regime_name: str) -> float:
    """
    Adjust TP to maintain same R:R (~1.78)
    Trending: 1.6 * 1.78 = 2.85
    Ranging: 2.2 * 1.78 = 3.9
    Volatile: 2.4 * 1.78 = 4.3
    Default: 3.2
    """
    rr = thresholds.VOLATILITY_ATR_MULTIPLIER_TP / thresholds.VOLATILITY_ATR_MULTIPLIER_SL
    sl = resolve_sl_multiplier(regime_name)
    return round(sl * rr, 2)

def resolve_entry_offset(spread_pct: float, atr_pct: float) -> float:
    """max(0.03%, 0.3 * spread%, 0.1 * ATR%)"""
    return max(thresholds.ENTRY_LIMIT_OFFSET_PCT, 0.3 * spread_pct, 0.1 * atr_pct)
