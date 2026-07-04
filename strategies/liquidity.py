"""Liquidity / Order Flow Strategy"""
from config import ORDER_FLOW_RATIO, MarketRegime
from analysis.order_flow import analyze_order_flow

def check_order_flow_signal(order_book: dict, regime_data: dict, klines: list = None) -> dict | None:
    """
    Check for order flow based entry signal.
    
    Returns signal dict or None.
    """
    if not order_book:
        return None
    
    of = analyze_order_flow(order_book)
    regime = regime_data.get('regime', '')
    price = regime_data.get('metrics', {}).get('price', 0)
    
    if not of['order_flow_signal'] or of['signal'] == 'NEUTRAL':
        return None
    
    # Strong buy signal with trend confirmation
    if of['signal'] in ['STRONG_BUY', 'BUY'] and regime != MarketRegime.TREND_DOWN:
        # Calculate SL/TP based on volatility
        atr = 0
        if klines:
            from analysis.indicators import calculate_atr
            from data.binance_api import extract_ohlcv
            ohlcv = extract_ohlcv(klines)
            atr = calculate_atr(ohlcv['high'], ohlcv['low'], ohlcv['close'])
        
        if atr == 0:
            atr = price * 0.01  # fallback: 1% of price
        
        entry = price
        stop_loss = entry - (1.5 * atr)
        take_profit1 = entry + (1.5 * atr * 2.0)
        take_profit2 = entry + (3 * atr * 2.0)
        
        return {
            'strategy': 'Liquidity (Order Flow)',
            'direction': 'LONG',
            'entry_price': entry,
            'stop_loss': stop_loss,
            'take_profit1': take_profit1,
            'take_profit2': take_profit2,
            'atr': atr,
            'order_flow': of,
            'reason': f"Buying pressure detected: {of['analysis']}. Volume ratio: {of['volume_ratio']:.3f}"
        }
    
    # Strong sell signal
    if of['signal'] in ['STRONG_SELL', 'SELL'] and regime == MarketRegime.TREND_DOWN:
        atr = 0
        if klines:
            from analysis.indicators import calculate_atr
            from data.binance_api import extract_ohlcv
            ohlcv = extract_ohlcv(klines)
            atr = calculate_atr(ohlcv['high'], ohlcv['low'], ohlcv['close'])
        
        if atr == 0:
            atr = price * 0.01
        
        entry = price
        stop_loss = entry + (1.5 * atr)
        take_profit1 = entry - (1.5 * atr * 2.0)
        take_profit2 = entry - (3 * atr * 2.0)
        
        return {
            'strategy': 'Liquidity (Order Flow)',
            'direction': 'SHORT',
            'entry_price': entry,
            'stop_loss': stop_loss,
            'take_profit1': take_profit1,
            'take_profit2': take_profit2,
            'atr': atr,
            'order_flow': of,
            'reason': f"Selling pressure detected: {of['analysis']}. Volume ratio: {of['volume_ratio']:.3f}"
        }
    
    return None
