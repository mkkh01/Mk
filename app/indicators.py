# -*- coding: utf-8 -*-
"""المؤشرات الفنية: EMA + Stochastic + أدوات فيبوناتشي والشموع.

تعمل على شموع *مغلقة* فقط (تُستبعد الشمعة الحية قبل الحساب).
"""
import numpy as np
import pandas as pd


def ema(close: pd.Series, length: int) -> pd.Series:
    return close.ewm(span=length, adjust=False, min_periods=length).mean()


def stochastic(df: pd.DataFrame, k_len: int = 14, k_smooth: int = 3,
               d_len: int = 3) -> tuple[pd.Series, pd.Series]:
    low_min = df["low"].rolling(k_len).min()
    high_max = df["high"].rolling(k_len).max()
    denom = (high_max - low_min).replace(0, np.nan)
    raw_k = 100 * (df["close"] - low_min) / denom
    k = raw_k.rolling(k_smooth).mean()
    d = k.rolling(d_len).mean()
    return k, d


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    h, low, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - low, (h - prev_c).abs(), (low - prev_c).abs()], axis=1).max(axis=1)
    return tr.rolling(length).mean()


def detect_swing(df: pd.DataFrame, lookback: int = 120) -> dict:
    """أدنى قاع وأعلى قمة في آخر N شمعة مغلقة."""
    window = df.tail(lookback)
    lo_pos = window["low"].values.argmin()
    hi_pos = window["high"].values.argmax()
    lo = window.iloc[lo_pos]
    hi = window.iloc[hi_pos]
    return {
        "swing_low": float(lo["low"]),
        "swing_high": float(hi["high"]),
        "low_ts": int(lo["ts"]),
        "high_ts": int(hi["ts"]),
        "low_first": bool(lo["ts"] < hi["ts"]),
    }


def fib_retracement_levels(swing_low: float, swing_high: float) -> dict:
    """مستويات التصحيح لموجة صاعدة (قاع → قمة)."""
    rng = swing_high - swing_low
    return {
        "0%": swing_low,
        "23.6%": swing_high - rng * 0.236,
        "38.2%": swing_high - rng * 0.382,
        "50%": swing_high - rng * 0.50,
        "61.8%": swing_high - rng * 0.618,
        "78.6%": swing_high - rng * 0.786,
        "100%": swing_high,
        "range": rng,
    }


def fib_extension_levels_down(swing_high: float, swing_low: float) -> dict:
    """مستويات الارتداد لموجة هابطة (قمة → قاع)."""
    rng = swing_high - swing_low
    return {
        "50%": swing_low + rng * 0.50,
        "61.8%": swing_low + rng * 0.618,
        "range": rng,
    }


# ---------- أنماط الشموع الانعكاسية ----------

def _body(o: float, c: float) -> float:
    return abs(c - o)


def is_bullish_engulfing(prev: pd.Series, last: pd.Series) -> bool:
    try:
        return (prev["close"] < prev["open"] and last["close"] > last["open"]
                and last["close"] >= prev["open"] and last["open"] <= prev["close"])
    except Exception:
        return False


def is_bearish_engulfing(prev: pd.Series, last: pd.Series) -> bool:
    try:
        return (prev["close"] > prev["open"] and last["close"] < last["open"]
                and last["close"] <= prev["open"] and last["open"] >= prev["close"])
    except Exception:
        return False


def is_hammer(row: pd.Series) -> bool:
    try:
        o, c, h, low = row["open"], row["close"], row["high"], row["low"]
        b = _body(o, c)
        if b == 0:
            return False
        lower = min(o, c) - low
        upper = h - max(o, c)
        return lower >= 2 * b and upper <= b
    except Exception:
        return False


def is_shooting_star(row: pd.Series) -> bool:
    try:
        o, c, h, low = row["open"], row["close"], row["high"], row["low"]
        b = _body(o, c)
        if b == 0:
            return False
        upper = h - max(o, c)
        lower = min(o, c) - low
        return upper >= 2 * b and lower <= b
    except Exception:
        return False
