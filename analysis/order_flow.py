"""
Order Flow Analysis
Analyzes order book structure for buy/sell pressure and liquidity signals.
"""
from config import ORDER_FLOW_RATIO

def analyze_order_flow(order_book: dict) -> dict:
    """
    Analyze order book to determine buy/sell pressure.
    
    Args:
        order_book: {'bids': [[price, qty],...], 'asks': [[price, qty],...]}
    
    Returns:
        dict with buy_pressure, sell_pressure, ratio, signal, analysis
    """
    if not order_book or 'bids' not in order_book or 'asks' not in order_book:
        return {
            'buy_pressure': 0, 'sell_pressure': 0,
            'ratio': 1.0, 'signal': 'NO_DATA',
            'order_flow_signal': False,
            'data_error': True,
            'analysis': 'Order book data unavailable — API may be blocked'
        }
    
    bids = order_book['bids']
    asks = order_book['asks']

    # Empty order book = data error, not neutral
    if not bids or not asks:
        return {
            'buy_pressure': 0, 'sell_pressure': 0,
            'ratio': 1.0, 'signal': 'NO_DATA',
            'order_flow_signal': False,
            'data_error': True,
            'analysis': 'Empty order book — API returned no bids/asks'
        }
    
    # Calculate total bid/ask volume (first 20 levels)
    bid_volume = sum(float(b[1]) for b in bids[:20])
    ask_volume = sum(float(a[1]) for a in asks[:20])
    
    # Calculate weighted bid/ask (volume * price)
    bid_value = sum(float(b[0]) * float(b[1]) for b in bids[:20])
    ask_value = sum(float(a[0]) * float(a[1]) for a in asks[:20])
    
    # Ratios
    volume_ratio = bid_volume / ask_volume if ask_volume > 0 else 1.0
    value_ratio = bid_value / ask_value if ask_value > 0 else 1.0
    
    # Wall detection: find largest orders
    bid_walls = sorted(bids[:20], key=lambda x: float(x[1]), reverse=True)[:3]
    ask_walls = sorted(asks[:20], key=lambda x: float(x[1]), reverse=True)[:3]
    
    max_bid_wall = float(bid_walls[0][1]) if bid_walls else 0
    max_ask_wall = float(ask_walls[0][1]) if ask_walls else 0
    wall_ratio = max_bid_wall / max_ask_wall if max_ask_wall > 0 else 2.0
    
    # Spread analysis
    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    spread = best_ask - best_bid
    spread_pct = (spread / best_ask) * 100
    
    # Determine signal
    if volume_ratio > ORDER_FLOW_RATIO and wall_ratio > 1.5:
        signal = 'STRONG_BUY'
        order_flow_signal = True
        analysis = f"Strong buying pressure: bid/ask vol={volume_ratio:.2f}, wall ratio={wall_ratio:.2f}"
    elif volume_ratio > 1.3:
        signal = 'BUY'
        order_flow_signal = True
        analysis = f"Buying pressure: bid/ask vol={volume_ratio:.2f}"
    elif volume_ratio < (1.0 / ORDER_FLOW_RATIO) and wall_ratio < 0.67:
        signal = 'STRONG_SELL'
        order_flow_signal = True  # FIXED: was False, preventing all sell signals
        analysis = f"Strong selling pressure: bid/ask vol={volume_ratio:.2f}, wall ratio={wall_ratio:.2f}"
    elif volume_ratio < 0.77:
        signal = 'SELL'
        order_flow_signal = True  # FIXED: was False, preventing sell signals
        analysis = f"Selling pressure: bid/ask vol={volume_ratio:.2f}"
    else:
        signal = 'NEUTRAL'
        order_flow_signal = False
        analysis = f"Neutral order flow: bid/ask vol={volume_ratio:.2f}"
    
    return {
        'bid_volume': round(bid_volume, 2),
        'ask_volume': round(ask_volume, 2),
        'volume_ratio': round(volume_ratio, 3),
        'value_ratio': round(value_ratio, 3),
        'wall_ratio': round(wall_ratio, 3),
        'spread_pct': round(spread_pct, 4),
        'bid_walls': [[float(b[0]), float(b[1])] for b in bid_walls],
        'ask_walls': [[float(a[0]), float(a[1])] for a in ask_walls],
        'signal': signal,
        'order_flow_signal': order_flow_signal,
        'data_error': False,
        'analysis': analysis
    }
