"""Liquidity / Order Flow Strategy — accepts pre-computed indicators."""
from config import MarketRegime
from analysis.order_flow import analyze_order_flow


def check_order_flow_signal(order_book: dict, regime_data: dict,
                            klines: list = None, indicators: dict = None) -> dict | None:
    """
    Check for order flow based entry signal using pre-computed indicators.
    
    Args:
        order_book: Order book data
        regime_data: Market regime classification
        klines: Raw kline data (optional fallback)
        indicators: Pre-computed {'atr': float, ...}
    """
    if not order_book:
        return None

    of = analyze_order_flow(order_book)
    regime = regime_data.get('regime', '')
    price = regime_data.get('metrics', {}).get('price', 0)

    if not of['order_flow_signal'] or of['signal'] == 'NEUTRAL':
        return None

    # Use pre-computed ATR or calculate fallback
    atr = 0
    if indicators and indicators.get('atr', 0) > 0:
        atr = indicators['atr']
    elif klines:
        from analysis.indicators import calculate_atr
        from data.binance_api import extract_ohlcv
        ohlcv = extract_ohlcv(klines)
        atr = calculate_atr(ohlcv['high'], ohlcv['low'], ohlcv['close'])

    if atr == 0:
        atr = price * 0.01  # fallback: 1% of price

    # Strong buy signal
    if of['signal'] in ['STRONG_BUY', 'BUY'] and regime != MarketRegime.TREND_DOWN:
        entry = price
        stop_loss = entry - (1.5 * atr)
        take_profit1 = entry + (1.5 * atr * 2.0)
        take_profit2 = entry + (3 * atr * 2.0)

        return {
            'strategy': 'Liquidity (Order Flow)',
            'direction': 'LONG',
            'entry_price': entry, 'stop_loss': stop_loss,
            'take_profit1': take_profit1, 'take_profit2': take_profit2,
            'atr': atr, 'order_flow': of,
            'reason': f"Buying pressure: {of['analysis']}. Volume ratio: {of['volume_ratio']:.3f}"
        }

    # Strong sell signal
    if of['signal'] in ['STRONG_SELL', 'SELL'] and regime == MarketRegime.TREND_DOWN:
        entry = price
        stop_loss = entry + (1.5 * atr)
        take_profit1 = entry - (1.5 * atr * 2.0)
        take_profit2 = entry - (3 * atr * 2.0)

        return {
            'strategy': 'Liquidity (Order Flow)',
            'direction': 'SHORT',
            'entry_price': entry, 'stop_loss': stop_loss,
            'take_profit1': take_profit1, 'take_profit2': take_profit2,
            'atr': atr, 'order_flow': of,
            'reason': f"Selling pressure: {of['analysis']}. Volume ratio: {of['volume_ratio']:.3f}"
        }

    return None
