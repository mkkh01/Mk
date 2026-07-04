"""Trade Monitor - Checks active trades for TP/SL hits."""
from config import SignalStatus

def check_trade(signal_row: dict, current_price: float) -> dict | None:
    """
    Check if a trade signal has hit TP or SL.
    
    Args:
        signal_row: Signal record from DB with entry_price, stop_loss, take_profit1, take_profit2, position_size
        current_price: Current market price
    
    Returns:
        Result dict or None if still active
    """
    entry = signal_row['entry_price']
    sl = signal_row['stop_loss']
    tp1 = signal_row['take_profit1']
    tp2 = signal_row.get('take_profit2')
    
    direction = 'LONG' if tp1 > entry else 'SHORT'
    
    if direction == 'LONG':
        # Check SL first (worst case)
        if current_price <= sl:
            loss_pct = ((sl - entry) / entry) * 100
            loss_usd = (sl - entry) * signal_row['position_size']
            return {
                'hit': True,
                'result': 'SL_HIT',
                'exit_price': current_price,
                'profit_pct': round(loss_pct, 2),
                'profit_usd': round(loss_usd, 2),
                'detail': f'Stop Loss triggered at {current_price:.4f} (SL={sl:.4f})'
            }
        # Check TP2 first (better profit)
        if tp2 and current_price >= tp2:
            profit_pct = ((tp2 - entry) / entry) * 100
            profit_usd = (tp2 - entry) * signal_row['position_size']
            return {
                'hit': True,
                'result': 'TP_HIT',
                'exit_price': tp2,
                'profit_pct': round(profit_pct, 2),
                'profit_usd': round(profit_usd, 2),
                'detail': f'Take Profit 2 hit at {tp2:.4f}'
            }
        # Check TP1
        if current_price >= tp1:
            profit_pct = ((tp1 - entry) / entry) * 100
            profit_usd = (tp1 - entry) * signal_row['position_size']
            return {
                'hit': True,
                'result': 'TP_HIT',
                'exit_price': tp1,
                'profit_pct': round(profit_pct, 2),
                'profit_usd': round(profit_usd, 2),
                'detail': f'Take Profit 1 hit at {tp1:.4f}'
            }
    else:  # SHORT
        # Check SL first
        if current_price >= sl:
            loss_pct = ((entry - sl) / entry) * 100  # positive number for loss
            loss_usd = (entry - sl) * signal_row['position_size']
            return {
                'hit': True,
                'result': 'SL_HIT',
                'exit_price': current_price,
                'profit_pct': round(loss_pct, 2),
                'profit_usd': round(loss_usd, 2),
                'detail': f'Stop Loss triggered at {current_price:.4f} (SL={sl:.4f})'
            }
        # Check TP2
        if tp2 and current_price <= tp2:
            profit_pct = ((entry - tp2) / entry) * 100
            profit_usd = (entry - tp2) * signal_row['position_size']
            return {
                'hit': True,
                'result': 'TP_HIT',
                'exit_price': tp2,
                'profit_pct': round(profit_pct, 2),
                'profit_usd': round(profit_usd, 2),
                'detail': f'Take Profit 2 hit at {tp2:.4f}'
            }
        # Check TP1
        if current_price <= tp1:
            profit_pct = ((entry - tp1) / entry) * 100
            profit_usd = (entry - tp1) * signal_row['position_size']
            return {
                'hit': True,
                'result': 'TP_HIT',
                'exit_price': tp1,
                'profit_pct': round(profit_pct, 2),
                'profit_usd': round(profit_usd, 2),
                'detail': f'Take Profit 1 hit at {tp1:.4f}'
            }
    
    # Still active
    return {
        'hit': False,
        'current_price': current_price,
        'distance_to_sl': round(abs(current_price - sl) / entry * 100, 2),
        'distance_to_tp': round(abs(tp1 - current_price) / entry * 100, 2)
    }
