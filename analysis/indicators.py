import math
from config import DONCHIAN_PERIOD, ATR_PERIOD, ADX_THRESHOLD, MOMENTUM_PERIOD

# === Pure Python Math Helpers (no numpy/pandas needed) ===

def _mean(values: list) -> float:
    """Arithmetic mean."""
    return sum(values) / len(values) if values else 0.0

def _std(values: list) -> float:
    """Sample standard deviation."""
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    variance = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)

def _linreg(x: list, y: list) -> tuple:
    """Simple linear regression: returns (slope, intercept)."""
    n = len(x)
    if n < 2:
        return 0.0, 0.0
    sx, sy = sum(x), sum(y)
    sxy = sum(xi * yi for xi, yi in zip(x, y))
    sxx = sum(xi * xi for xi in x)
    denom = n * sxx - sx * sx
    if denom == 0:
        return 0.0, sy / n
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept

def calculate_donchian(highs: list, lows: list, period: int = DONCHIAN_PERIOD) -> dict:
    """Calculate Donchian Channels. Returns {upper, lower, mid, width_pct}"""
    if len(highs) < period:
        return None
    upper = max(highs[-period:])
    lower = min(lows[-period:])
    mid = (upper + lower) / 2
    width_pct = ((upper - lower) / mid) * 100
    return {'upper': upper, 'lower': lower, 'mid': mid, 'width_pct': width_pct}

def calculate_atr(highs: list, lows: list, closes: list, period: int = ATR_PERIOD) -> float:
    """Calculate Average True Range."""
    if len(closes) < period + 1:
        return 0
    tr_values = []
    for i in range(1, len(closes)):
        high_low = highs[i] - lows[i]
        high_close = abs(highs[i] - closes[i-1])
        low_close = abs(lows[i] - closes[i-1])
        tr_values.append(max(high_low, high_close, low_close))

    # Safety floor: never return literal 0 (breaks position sizing)
    atr = _mean(tr_values[-period:])
    if atr <= 0 and closes:
        atr = closes[-1] * 0.00001  # 0.001% of price as absolute floor
    return atr

def calculate_adx(highs: list, lows: list, closes: list, period: int = 14) -> dict:
    """Calculate ADX and DI+/DI-. Returns {adx, plus_di, minus_di, trending}"""
    if len(closes) < period * 2:
        return {'adx': 0, 'plus_di': 0, 'minus_di': 0, 'trending': False}
    
    tr_values = []
    plus_dm = []
    minus_dm = []
    
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        tr_values.append(tr)
        
        up_move = highs[i] - highs[i-1]
        down_move = lows[i-1] - lows[i]
        
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0)
    
    # Wilder's smoothing
    atr = sum(tr_values[:period]) / period
    smoothed_plus_dm = sum(plus_dm[:period]) / period
    smoothed_minus_dm = sum(minus_dm[:period]) / period
    
    dx_values = []
    for i in range(period, len(tr_values)):
        atr = (atr * (period - 1) + tr_values[i]) / period
        smoothed_plus_dm = (smoothed_plus_dm * (period - 1) + plus_dm[i]) / period
        smoothed_minus_dm = (smoothed_minus_dm * (period - 1) + minus_dm[i]) / period
        
        plus_di = (smoothed_plus_dm / atr) * 100 if atr > 0 else 0
        minus_di = (smoothed_minus_dm / atr) * 100 if atr > 0 else 0
        
        dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
        dx_values.append(dx)
    
    adx = _mean(dx_values[-period:]) if dx_values else 0
    
    # Calculate current DI values
    plus_di = 0
    minus_di = 0
    if atr > 0:
        plus_di = (smoothed_plus_dm / atr) * 100
        minus_di = (smoothed_minus_dm / atr) * 100
    
    return {
        'adx': round(adx, 2),
        'plus_di': round(plus_di, 2),
        'minus_di': round(minus_di, 2),
        'trending': adx > ADX_THRESHOLD
    }

def calculate_ema(data: list, period: int) -> list:
    """Calculate Exponential Moving Average."""
    if len(data) < period:
        return [_mean(data)] * len(data) if data else []
    multiplier = 2 / (period + 1)
    ema = [_mean(data[:period])]
    for price in data[period:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])
    return ema

def calculate_slope(data: list, period: int = 5) -> float:
    """Calculate linear slope of recent data as percentage."""
    if len(data) < period:
        return 0
    recent = data[-period:]
    x = list(range(period))
    slope, _ = _linreg(x, recent)
    return (slope / _mean(recent)) * 100 if _mean(recent) != 0 else 0.0

def calculate_volatility(closes: list, period: int = 20) -> float:
    """Calculate historical volatility (standard deviation of returns)."""
    if len(closes) < period:
        return 0
    returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
    return _std(returns[-period:]) * math.sqrt(365) * 100  # Annualized

def calculate_momentum(closes: list, period: int = MOMENTUM_PERIOD) -> float:
    """Calculate momentum as price change over period."""
    if len(closes) < period:
        return 0
    return ((closes[-1] - closes[-period]) / closes[-period]) * 100

def calculate_rsi(closes: list, period: int = 14) -> float:
    """Calculate RSI."""
    if len(closes) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = _mean(gains[-period:])
    avg_loss = _mean(losses[-period:])
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))
