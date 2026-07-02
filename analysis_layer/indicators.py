import numpy as np
import pandas as pd
import logging

logger = logging.getLogger("indicators")

def ema(series, period):
    """Exponential Moving Average."""
    if len(series) < period:
        return None
    return pd.Series(series).ewm(span=period, adjust=False).mean().tolist()

def sma(series, period):
    """Simple Moving Average."""
    if len(series) < period:
        return None
    return pd.Series(series).rolling(window=period).mean().tolist()

def rsi(closes, period=14):
    """Relative Strength Index."""
    if len(closes) < period + 1:
        return None
    series = pd.Series(closes)
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    rsi_values = (100 - (100 / (1 + rs))).tolist()
    return rsi_values

def atr(candles, period=14):
    """Average True Range."""
    if len(candles) < period + 1:
        return None
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    
    tr = []
    for i in range(1, len(candles)):
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i-1])
        tr3 = abs(lows[i] - closes[i-1])
        tr.append(max(tr1, tr2, tr3))
    
    atr_series = pd.Series(tr).ewm(alpha=1/period, min_periods=period).mean()
    return atr_series.tolist()

def adx(candles, period=14):
    """Average Directional Index."""
    if len(candles) < period * 2 + 1:
        return None
    highs = np.array([c["high"] for c in candles])
    lows = np.array([c["low"] for c in candles])
    closes = np.array([c["close"] for c in candles])
    
    plus_dm = np.where((highs[1:] - highs[:-1]) > (lows[:-1] - lows[1:]),
                       np.maximum(highs[1:] - highs[:-1], 0), 0)
    minus_dm = np.where((lows[:-1] - lows[1:]) > (highs[1:] - highs[:-1]),
                        np.maximum(lows[:-1] - lows[1:], 0), 0)
    
    tr = np.maximum(
        np.maximum(highs[1:] - lows[1:], np.abs(highs[1:] - closes[:-1])),
        np.abs(lows[1:] - closes[:-1])
    )
    
    atr_val = pd.Series(tr).ewm(alpha=1/period, min_periods=period).mean()
    plus_di = 100 * pd.Series(plus_dm).ewm(alpha=1/period, min_periods=period).mean() / atr_val.replace(0, 1e-10)
    minus_di = 100 * pd.Series(minus_dm).ewm(alpha=1/period, min_periods=period).mean() / atr_val.replace(0, 1e-10)
    
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, 1e-10)
    adx_val = pd.Series(dx).ewm(alpha=1/period, min_periods=period).mean()
    
    return {
        "adx": adx_val.tolist(),
        "plus_di": plus_di.tolist(),
        "minus_di": minus_di.tolist()
    }

def bollinger_bands(closes, period=20, std_dev=2):
    """Bollinger Bands."""
    if len(closes) < period:
        return None
    series = pd.Series(closes)
    mid = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return {
        "upper": upper.tolist(),
        "mid": mid.tolist(),
        "lower": lower.tolist()
    }

def macd(closes, fast=12, slow=26, signal=9):
    """MACD indicator."""
    if len(closes) < slow + signal:
        return None
    series = pd.Series(closes)
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return {
        "macd": macd_line.tolist(),
        "signal": signal_line.tolist(),
        "histogram": histogram.tolist()
    }

def volume_sma(volumes, period=20):
    """Volume Simple Moving Average."""
    if len(volumes) < period:
        return None
    return pd.Series(volumes).rolling(window=period).mean().tolist()