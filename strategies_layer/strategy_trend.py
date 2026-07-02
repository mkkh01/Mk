import logging
from analysis_layer.indicators import atr, rsi, ema, volume_sma

logger = logging.getLogger("strategy_trend")

def compute(candles, params=None):
    """
    Donchian Channel Trend-Following Strategy.
    
    Entry BUY: Price breaks above N-period high
    Entry SELL: Price breaks below N-period low
    Exit: Price crosses back, or SL/TP hit
    
    Returns: {
        "signal": "BUY" | "SELL" | None,
        "entry_price": float,
        "stop_loss": float,
        "take_profit": float,
        "confidence": 0-100,
        "atr": float,
        "reason": str
    }
    """
    if not candles or len(candles) < 50:
        return None
    
    p = params or {}
    n = p.get("donchian_period", 20)
    atr_mult = p.get("atr_sl_multiplier", 3.0)
    tp_ratio = p.get("tp_ratio", 2.0)
    
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    volumes = [c["volume"] for c in candles]
    current_price = closes[-1]
    
    # ── Donchian Channel ──
    highest = max(highs[-(n+1):-1])  # Exclude current candle
    lowest = min(lows[-(n+1):-1])
    
    # ── ATR for SL/TP ──
    atr_vals = atr(candles, p.get("atr_period", 14))
    current_atr = atr_vals[-1] if atr_vals else None
    if not current_atr or current_atr <= 0:
        return None
    
    # ── RSI Filter ──
    rsi_vals = rsi(closes, 14)
    rsi_val = rsi_vals[-1] if rsi_vals else 50
    
    # ── EMA Trend Filter ──
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    ema_bullish = ema50 and ema200 and ema50[-1] > ema200[-1]
    ema_bearish = ema50 and ema200 and ema50[-1] < ema200[-1]
    
    # ── Volume Filter ──
    vol_sma = volume_sma(volumes, 20)
    vol_confirm = vol_sma and volumes[-1] > vol_sma[-1]
    
    # ── Calculate SL and TP ──
    sl_distance = current_atr * atr_mult
    tp_distance = sl_distance * tp_ratio
    
    signal = None
    confidence = 0
    reason = ""
    
    # ── BUY Signal ──
    if current_price > highest:
        if rsi_val > 45 and ema_bullish:
            signal = "BUY"
            confidence = 60
            reason = f"كسر قمة {n} فترة ({highest:.2f})"
            
            if rsi_val > 50:
                confidence += 5
            if vol_confirm:
                confidence += 10
                reason += " + تأكيد حجم"
            if rsi_val > 60:
                confidence += 5
            reason += f" | RSI={rsi_val:.1f}"
    
    # ── SELL Signal ──
    elif current_price < lowest:
        if rsi_val < 55 and ema_bearish:
            signal = "SELL"
            confidence = 60
            reason = f"كسر قاع {n} فترة ({lowest:.2f})"
            
            if rsi_val < 50:
                confidence += 5
            if vol_confirm:
                confidence += 10
                reason += " + تأكيد حجم"
            if rsi_val < 40:
                confidence += 5
            reason += f" | RSI={rsi_val:.1f}"
    
    if signal:
        if signal == "BUY":
            sl = current_price - sl_distance
            tp = current_price + tp_distance
        else:
            sl = current_price + sl_distance
            tp = current_price - tp_distance
        
        confidence = min(confidence, 95)
        
        return {
            "signal": signal,
            "entry_price": round(current_price, 8),
            "stop_loss": round(sl, 8),
            "take_profit": round(tp, 8),
            "confidence": confidence,
            "atr": round(current_atr, 8),
            "reason": reason
        }
    
    return None