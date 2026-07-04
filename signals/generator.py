"""
Signal Generator — uses pre-computed indicators + live price for decisions.
"""
from strategies.trend_following import check_donchian_signal
from strategies.liquidity import check_order_flow_signal
from strategies.selector import select_strategy
from analysis.market_regime import classify_regime
from utils.logger import market_regime as _log_regime, strategy_check, strategy_selected


def generate_signal(symbol: str, timeframe: str, klines: list, order_book: dict,
                    coin_config: dict, indicators: dict = None,
                    live_price: float = 0) -> dict:
    """
    Generate trade signal using pre-computed indicators + live price.
    
    Args:
        live_price: Real-time ticker price (used for entry decisions, not kline close)
    """
    if not klines:
        return {
            'symbol': symbol, 'timeframe': timeframe, 'has_signal': False,
            'regime': {'regime': 'NO_DATA'}, 'reason': 'No kline data'
        }

    # Step 1: Classify market regime (pass pre-computed + live price)
    regime_data = classify_regime(klines, order_book, indicators, live_price)
    _log_regime(symbol, regime_data['regime'], regime_data)

    # Step 2: Check strategies with pre-computed indicators
    strategy_check(symbol, 'Trend Following (Donchian)')
    donchian_signal = check_donchian_signal(klines, regime_data, indicators)

    strategy_check(symbol, 'Liquidity (Order Flow)')
    order_flow_signal = check_order_flow_signal(order_book, regime_data, klines, indicators)

    # Step 3: Strategy selection (priority-chain pattern)
    decision = select_strategy(regime_data, donchian_signal, order_flow_signal)
    if decision.get('selected_strategy'):
        strategy_selected(symbol, decision['selected_strategy'], decision['reason'])

    if not decision['signal']:
        return {
            'symbol': symbol, 'timeframe': timeframe, 'has_signal': False,
            'regime': regime_data, 'reason': decision['reason'],
            '_debug': {
                'donchian_signal': donchian_signal,
                'order_flow_signal': order_flow_signal,
                'decision': decision
            }
        }

    signal = decision['signal']
    risk_percent = coin_config.get('risk_percent', 2.0)
    capital_value = coin_config.get('capital_value', 100.0)

    entry = signal['entry_price']
    stop_loss = signal['stop_loss']
    risk_per_trade = abs(entry - stop_loss)
    risk_amount_usd = capital_value * (risk_percent / 100)
    position_size = risk_amount_usd / risk_per_trade if risk_per_trade > 0 else 0

    return {
        'symbol': symbol, 'timeframe': timeframe, 'has_signal': True,
        'strategy': signal['strategy'], 'direction': signal['direction'],
        'entry_price': round(entry, 8), 'stop_loss': round(stop_loss, 8),
        'take_profit1': round(signal['take_profit1'], 8),
        'take_profit2': round(signal['take_profit2'], 8) if signal.get('take_profit2') else None,
        'position_size': round(position_size, 8),
        'risk_percent': risk_percent, 'capital_value': capital_value,
        'risk_amount': round(risk_amount_usd, 2),
        'atr': round(signal.get('atr', 0), 8),
        'reason': signal.get('reason', ''),
        'regime': regime_data,
        'regime_details': {
            'regime': regime_data['regime'], 'confidence': regime_data['confidence'],
            'metrics': regime_data['metrics']
        },
        '_debug': {
            'donchian_signal': donchian_signal,
            'order_flow_signal': order_flow_signal,
            'decision': decision
        }
    }


def precompute_indicators(klines: list) -> dict:
    """Calculate all indicators once, pass to classify_regime and strategies."""
    from analysis.indicators import (
        calculate_donchian, calculate_atr, calculate_adx,
        calculate_ema, calculate_rsi, calculate_volatility, calculate_momentum,
        calculate_slope,
    )
    from data.binance_api import extract_ohlcv

    ohlcv = extract_ohlcv(klines)
    closes = ohlcv['close']
    highs = ohlcv['high']
    lows = ohlcv['low']
    volumes = ohlcv['volume']

    if not closes:
        return {}

    return {
        'donchian': calculate_donchian(highs, lows),
        'atr': calculate_atr(highs, lows, closes),
        'adx': calculate_adx(highs, lows, closes),
        'rsi': calculate_rsi(closes),
        'volatility': calculate_volatility(closes),
        'momentum': calculate_momentum(closes),
        'slope': calculate_slope(closes, 5),
        'ema20': (calculate_ema(closes, 20) or [0])[-1] if closes else 0,
        'ema50': (calculate_ema(closes, 50) or [0])[-1] if len(closes) >= 50 else 0,
        'ema200': (calculate_ema(closes, 200) or [0])[-1] if len(closes) >= 200 else 0,
        'closes': closes,
        'highs': highs,
        'lows': lows,
        'volumes': volumes,
    }
