import logging
from analysis_layer.indicators import ema, rsi, atr, adx

logger = logging.getLogger("market_regime")

REGIMES = [
    "TREND_UP", "TREND_DOWN", "RANGE", 
    "HIGH_VOLATILITY", "LOW_VOLATILITY",
    "DISTRIBUTION", "CAPITULATION", "BREAKOUT", "PULLBACK"
]

def evaluate(candles, atr_values=None):
    """
    Classify the current market regime.
    Returns dict with: regime, trend_strength, volatility_level, 
                        rsi_val, adx_val, ema_trend
    """
    if not candles or len(candles) < 50:
        return {"regime": "RANGE", "trend_strength": "WEAK", 
                "volatility_level": "NORMAL", "rsi_val": 50, "adx_val": 20, "ema_trend": "FLAT"}
    
    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]
    n = len(closes)
    
    # ── EMA Trend ──
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    ema_trend = "FLAT"
    if ema50 and ema200:
        if ema50[-1] > ema200[-1] and ema50[-5] > ema200[-5]:
            ema_trend = "BULLISH"
        elif ema50[-1] < ema200[-1] and ema50[-5] < ema200[-5]:
            ema_trend = "BEARISH"
    
    # ── RSI ──
    rsi_vals = rsi(closes, 14)
    rsi_val = rsi_vals[-1] if rsi_vals else 50
    
    # ── ADX ──
    adx_data = adx(candles, 14)
    adx_val = adx_data["adx"][-1] if adx_data and adx_data["adx"] else 20
    
    # ── ATR / Volatility ──
    atr_vals = atr(candles, 14)
    current_atr = atr_vals[-1] if atr_vals else 0
    avg_price = closes[-1]
    atr_pct = (current_atr / avg_price * 100) if avg_price > 0 else 0
    
    if atr_pct > 5:
        vol_level = "EXTREME"
    elif atr_pct > 2.5:
        vol_level = "HIGH"
    elif atr_pct > 1:
        vol_level = "NORMAL"
    else:
        vol_level = "LOW"
    
    # ── Trend Strength ──
    if adx_val > 40:
        trend_strength = "STRONG"
    elif adx_val > 25:
        trend_strength = "MODERATE"
    else:
        trend_strength = "WEAK"
    
    # ── Classify Regime ──
    regime = "RANGE"
    
    if vol_level in ("EXTREME", "HIGH") and trend_strength == "WEAK":
        regime = "HIGH_VOLATILITY"
    elif vol_level == "LOW" and trend_strength == "WEAK":
        regime = "LOW_VOLATILITY"
    elif ema_trend == "BULLISH" and trend_strength in ("STRONG", "MODERATE"):
        regime = "TREND_UP"
        if rsi_val > 70:
            regime = "DISTRIBUTION"
        elif rsi_val < 40:
            regime = "PULLBACK"
    elif ema_trend == "BEARISH" and trend_strength in ("STRONG", "MODERATE"):
        regime = "TREND_DOWN"
        if rsi_val < 20 and vol_level in ("EXTREME", "HIGH"):
            regime = "CAPITULATION"
    elif ema_trend == "BULLISH" and rsi_val < 35 and vol_level == "LOW":
        regime = "PULLBACK"
    
    # Recent breakout detection (price broke recent high/low range)
    recent_high = max(c["high"] for c in candles[-20:])
    recent_low = min(c["low"] for c in candles[-20:])
    if closes[-1] > recent_high * 1.005 and trend_strength == "STRONG":
        regime = "BREAKOUT"
    elif closes[-1] < recent_low * 0.995 and trend_strength == "STRONG":
        regime = "CAPITULATION"
    
    return {
        "regime": regime,
        "trend_strength": trend_strength,
        "volatility_level": vol_level,
        "rsi_val": round(rsi_val, 2),
        "adx_val": round(adx_val, 2),
        "ema_trend": ema_trend,
        "atr_val": round(current_atr, 6),
        "atr_pct": round(atr_pct, 4)
    }