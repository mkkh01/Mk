"""Signal Generator - Creates trade signals with position sizing calculation."""
from config import DEFAULT_RR_RATIO
from strategies.trend_following import check_donchian_signal
from strategies.liquidity import check_order_flow_signal
from strategies.selector import select_strategy
from analysis.market_regime import classify_regime

def generate_signal(symbol: str, timeframe: str, klines: list, order_book: dict,
                    coin_config: dict) -> dict | None:
    """
    Generate a complete trade signal for a coin.
    
    Args:
        symbol: Trading pair (e.g. 'BTCUSDT')
        timeframe: Kline interval
        klines: Raw Binance kline data
        order_book: Raw Binance order book data
        coin_config: {'symbol', 'capital_value', 'risk_percent'}
    
    Returns:
        Full signal dict or None
    """
    # Step 1: Classify market regime
    regime_data = classify_regime(klines, order_book)
    
    # Step 2: Check individual strategies
    donchian_signal = check_donchian_signal(klines, regime_data)
    order_flow_signal = check_order_flow_signal(order_book, regime_data, klines)
    
    # Step 3: Strategy selection
    decision = select_strategy(regime_data, donchian_signal, order_flow_signal)
    
    if not decision['signal']:
        return {
            'symbol': symbol,
            'timeframe': timeframe,
            'has_signal': False,
            'regime': regime_data,
            'reason': decision['reason']
        }
    
    signal = decision['signal']
    risk_percent = coin_config.get('risk_percent', 2.0)
    capital_value = coin_config.get('capital_value', 100.0)
    
    # Step 4: Position sizing (ATR-based)
    entry = signal['entry_price']
    stop_loss = signal['stop_loss']
    risk_per_trade = abs(entry - stop_loss)
    
    # Position size = (capital_value * risk_percent%) / risk_per_trade
    risk_amount_usd = capital_value * (risk_percent / 100)
    position_size = risk_amount_usd / risk_per_trade if risk_per_trade > 0 else 0
    
    return {
        'symbol': symbol,
        'timeframe': timeframe,
        'has_signal': True,
        'strategy': signal['strategy'],
        'direction': signal['direction'],
        'entry_price': round(entry, 8),
        'stop_loss': round(stop_loss, 8),
        'take_profit1': round(signal['take_profit1'], 8),
        'take_profit2': round(signal['take_profit2'], 8) if signal.get('take_profit2') else None,
        'position_size': round(position_size, 8),
        'risk_percent': risk_percent,
        'capital_value': capital_value,
        'risk_amount': round(risk_amount_usd, 2),
        'atr': round(signal.get('atr', 0), 8),
        'reason': signal.get('reason', ''),
        'regime': regime_data,
        'regime_details': {
            'regime': regime_data['regime'],
            'confidence': regime_data['confidence'],
            'metrics': regime_data['metrics']
        }
    }
