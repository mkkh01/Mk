"""Liquidity / Order Flow Strategy — accepts pre-computed indicators."""
from config import MarketRegime
from analysis.order_flow import analyze_order_flow


def check_order_flow_signal(order_book: dict, regime_data: dict,
                            klines: list = None, indicators: dict = None) -> dict | None:
    """
    Check for order flow based entry signal using pre-computed indicators.
    Distinguishes between: data error (NO_DATA) vs genuine neutral market.
    """
    if not order_book:
        return None

    of = analyze_order_flow(order_book)
    regime = regime_data.get('regime', '')
    price = regime_data.get('metrics', {}).get('price', 0)

    # Data integrity: NO_DATA means API failure, not neutral market
    if of.get('data_error') or of.get('signal') == 'NO_DATA':
        err_msg = of.get('analysis', 'Order book unavailable')
        return {
            'strategy': 'Liquidity (Order Flow)',
            'direction': 'NONE',
            'entry_price': 0, 'stop_loss': 0,
            'take_profit1': 0,
            'atr': 0, 'order_flow': of,
            'reason': 'DATA_ERROR: ' + err_msg,
            'data_error': True,
        }

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
            'reason': "Buying pressure: {}. Volume ratio: {:.3f}".format(
                of['analysis'], of['volume_ratio']),
            'data_error': False,
        }

    # Strong sell signal (NOW ACTUALLY WORKS thanks to order_flow_signal=True fix)
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
            'reason': "Selling pressure: {}. Volume ratio: {:.3f}".format(
                of['analysis'], of['volume_ratio']),
            'data_error': False,
        }

    return None
