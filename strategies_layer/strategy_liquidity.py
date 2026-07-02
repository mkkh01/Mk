import logging
from analysis_layer.indicators import rsi, bollinger_bands

logger = logging.getLogger("strategy_liquidity")

def compute(order_book, candles=None, params=None):
    """
    Liquidity / Order-Flow Strategy.
    
    Measures buy vs sell pressure from order book.
    Entry when one side dominates significantly.
    
    Returns signal dict or None.
    """
    if not order_book:
        return None
    
    p = params or {}
    pressure_threshold = p.get("pressure_threshold", 2.0)
    
    bids = order_book.get("bids", [])
    asks = order_book.get("asks", [])
    
    if not bids or not asks:
        return None
    
    # Top 5 levels
    buy_pressure = sum(b["qty"] for b in bids[:5])
    sell_pressure = sum(a["qty"] for a in asks[:5])
    
    total = buy_pressure + sell_pressure
    if total == 0:
        return None
    
    buy_ratio = buy_pressure / total
    sell_ratio = sell_pressure / total
    
    # ── Bollinger confirmation if candles available ──
    bb_confirm = None
    rsi_val = 50
    if candles and len(candles) >= 21:
        closes = [c["close"] for c in candles]
        bb = bollinger_bands(closes)
        rsi_vals = rsi(closes, 14)
        
        if bb and bb["lower"] and bb["upper"]:
            current = closes[-1]
            if current <= bb["lower"][-1]:
                bb_confirm = "OVERSOLD"
            elif current >= bb["upper"][-1]:
                bb_confirm = "OVERBOUGHT"
        
        if rsi_vals:
            rsi_val = rsi_vals[-1]
    
    signal = None
    confidence = 0
    reason = ""
    
    ratio = buy_pressure / sell_pressure if sell_pressure > 0 else 999
    
    if ratio > pressure_threshold:
        signal = "BUY"
        confidence = 55
        reason = f"ضغط شراء {ratio:.1f}x | بنكية {buy_ratio*100:.0f}%"
        
        if bb_confirm == "OVERSOLD":
            confidence += 15
            reason += " + بولنجر منخفض"
        if rsi_val < 45:
            confidence += 5
    
    elif (1/ratio) > pressure_threshold:
        signal = "SELL"
        confidence = 55
        reason = f"ضغط بيع {(1/ratio):.1f}x | بنكية {sell_ratio*100:.0f}%"
        
        if bb_confirm == "OVERBOUGHT":
            confidence += 15
            reason += " + بولنجر مرتفع"
        if rsi_val > 55:
            confidence += 5
    
    if signal:
        # Calculate SL/TP from spread
        best_bid = bids[0]["price"]
        best_ask = asks[0]["price"]
        mid_price = (best_bid + best_ask) / 2
        spread = best_ask - best_bid
        
        sl_dist = spread * 15
        tp_dist = sl_dist * 2
        
        if signal == "BUY":
            entry = best_ask
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            entry = best_bid
            sl = entry + sl_dist
            tp = entry - tp_dist
        
        return {
            "signal": signal,
            "entry_price": round(entry, 8),
            "stop_loss": round(sl, 8),
            "take_profit": round(tp, 8),
            "confidence": min(confidence, 90),
            "atr": round(spread, 8),
            "reason": reason
        }
    
    return None