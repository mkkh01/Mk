"""Donchian Channel Breakout Strategy - Trend Following"""
from config import DONCHIAN_PERIOD, ATR_PERIOD, MarketRegime
from analysis.indicators import calculate_donchian, calculate_atr
from data.binance_api import extract_ohlcv

def check_donchian_signal(klines: list, regime_data: dict) -> dict | None:
    """
    Check for Donchian Channel breakout entry signal.
    
    Returns signal dict or None if no signal.
    """
    ohlcv = extract_ohlcv(klines)
    closes = ohlcv['close']
    highs = ohlcv['high']
    lows = ohlcv['low']
    
    donchian = calculate_donchian(highs, lows, DONCHIAN_PERIOD)
    if not donchian:
        return None
    
    atr = calculate_atr(highs, lows, closes, ATR_PERIOD)
    if atr == 0:
        return None
    
    current_price = closes[-1]
    regime = regime_data.get('regime', '')
    volume_spike = regime_data.get('metrics', {}).get('volume_trend', 0)
    
    # Only enter in trending markets
    if regime not in [MarketRegime.TREND_UP, MarketRegime.BREAKOUT, MarketRegime.TREND_DOWN]:
        return None
    
    # LONG: Price breaks above upper Donchian
    if regime == MarketRegime.TREND_UP and current_price >= donchian['upper'] * 0.998:
        entry = current_price
        stop_loss = entry - (2 * atr)
        take_profit1 = entry + (2 * atr * 2.0)  # R:R = 2
        take_profit2 = entry + (4 * atr * 2.0)
        
        return {
            'strategy': 'Trend Following (Donchian)',
            'direction': 'LONG',
            'entry_price': entry,
            'stop_loss': stop_loss,
            'take_profit1': take_profit1,
            'take_profit2': take_profit2,
            'atr': atr,
            'donchian_upper': donchian['upper'],
            'donchian_lower': donchian['lower'],
            'reason': f"Price ({current_price:.2f}) broke above Donchian upper ({donchian['upper']:.2f}). "
                      f"Regime: {regime}, ATR: {atr:.4f}"
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
            'entry_price': entry,
            'stop_loss': stop_loss,
            'take_profit1': take_profit1,
            'take_profit2': take_profit2,
            'atr': atr,
            'donchian_upper': donchian['upper'],
            'donchian_lower': donchian['lower'],
            'reason': f"Price ({current_price:.2f}) broke below Donchian lower ({donchian['lower']:.2f}). "
                      f"Regime: {regime}, ATR: {atr:.4f}"
        }
    
    return None
