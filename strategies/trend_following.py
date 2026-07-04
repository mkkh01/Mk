"""Donchian Channel Breakout Strategy — accepts pre-computed indicators."""
from config import MarketRegime


def check_donchian_signal(klines: list, regime_data: dict, indicators: dict = None) -> dict | None:
    """
    Check for Donchian Channel breakout using pre-computed indicators.
    
    Args:
        klines: Raw kline data
        regime_data: Market regime classification
        indicators: Pre-computed {'donchian': {...}, 'atr': float, ...}
    """
    regime = regime_data.get('regime', '')
    current_price = regime_data.get('metrics', {}).get('price', 0)

    if not indicators:
        from analysis.indicators import calculate_donchian, calculate_atr
        from data.binance_api import extract_ohlcv
        ohlcv = extract_ohlcv(klines)
        donchian = calculate_donchian(ohlcv['high'], ohlcv['low'])
        atr = calculate_atr(ohlcv['high'], ohlcv['low'], ohlcv['close'])
    else:
        donchian = indicators.get('donchian')
        atr = indicators.get('atr', 0)
        if current_price == 0 and klines:
            from data.binance_api import extract_ohlcv
            ohlcv = extract_ohlcv(klines)
            current_price = ohlcv['close'][-1] if ohlcv['close'] else 0

    if not donchian or atr == 0:
        return None

    # Only enter in trending markets
    if regime not in [MarketRegime.TREND_UP, MarketRegime.BREAKOUT, MarketRegime.TREND_DOWN]:
        return None

    # LONG: Price breaks above upper Donchian
    if regime in [MarketRegime.TREND_UP, MarketRegime.BREAKOUT] and current_price >= donchian['upper'] * 0.998:
        entry = current_price
        stop_loss = entry - (2 * atr)
        take_profit1 = entry + (2 * atr * 2.0)
        take_profit2 = entry + (4 * atr * 2.0)

        return {
            'strategy': 'Trend Following (Donchian)',
            'direction': 'LONG',
            'entry_price': entry, 'stop_loss': stop_loss,
            'take_profit1': take_profit1, 'take_profit2': take_profit2,
            'atr': atr,
            'donchian_upper': donchian['upper'], 'donchian_lower': donchian['lower'],
            'reason': f"Price ({current_price:.2f}) broke above Donchian upper ({donchian['upper']:.2f}). Regime: {regime}, ATR: {atr:.4f}"
        }

    # SHORT: Price breaks below lower Donchian
    if regime == MarketRegime.TREND_DOWN and current_price <= donchian['lower'] * 1.002:
        entry = current_price
        stop_loss = entry + (2 * atr)
        take_profit1 = entry - (2 * atr * 2.0)
        take_profit2 = entry - (4 * atr * 2.0)

        return {
            'strategy': 'Trend Following (Donchian)',
            'direction': 'SHORT',
            'entry_price': entry, 'stop_loss': stop_loss,
            'take_profit1': take_profit1, 'take_profit2': take_profit2,
            'atr': atr,
            'donchian_upper': donchian['upper'], 'donchian_lower': donchian['lower'],
            'reason': f"Price ({current_price:.2f}) broke below Donchian lower ({donchian['lower']:.2f}). Regime: {regime}, ATR: {atr:.4f}"
        }

    return None
