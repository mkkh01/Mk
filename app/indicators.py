# -*- coding: utf-8 -*-
"""طبقة المؤشرات (§8 + §13 + أدوات §6/§14/§35).

نقية تماماً: قياس فقط، لا قرارات ولا قاعدة بيانات (§3.3).
تعمل على شموع *مغلقة* فقط (§3.1).
"""
import numpy as np
import pandas as pd

# =====================================================
#  التحقق من البيانات (§6.2)
# =====================================================

def validate_ohlc(df: pd.DataFrame, tf_seconds: int) -> tuple[bool, str]:
    """يفحص سلسلة الشموع. يرجع (سليمة؟, سبب الرفض)."""
    if df is None or len(df) < 10:
        return False, "شموع غير كافية"
    try:
        ts = df["ts"].to_numpy()
        if len(np.unique(ts)) != len(ts):
            return False, "شموع مكررة"
        if not bool(np.all(np.diff(ts) > 0)):
            return False, "ترتيب زمني خاطئ"
        gaps = np.diff(ts)
        if bool(np.any(gaps > tf_seconds * 3)):
            return False, "فجوة زمنية في الشموع"
        o, h, low, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
        if bool(np.any(~np.isfinite(o)) or np.any(~np.isfinite(h))
                or np.any(~np.isfinite(low)) or np.any(~np.isfinite(c))):
            return False, "أسعار غير صالحة (NaN)"
        if bool(np.any(o <= 0) or np.any(h <= 0) or np.any(low <= 0) or np.any(c <= 0)):
            return False, "أسعار صفرية/سالبة"
        if bool(np.any(h < np.maximum(o, c))):
            return False, "High أقل من Open/Close"
        if bool(np.any(low > np.minimum(o, c))):
            return False, "Low أعلى من Open/Close"
        if bool(np.any(h < low)):
            return False, "High أقل من Low"
        return True, ""
    except Exception as e:
        return False, f"فحص البيانات فشل: {e}"


TF_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
              "1h": 3600, "1H": 3600, "2h": 7200, "4h": 14400, "1d": 86400}

# =====================================================
#  EMA + الميل (§8)
# =====================================================

def ema(close: pd.Series, length: int) -> pd.Series:
    return close.ewm(span=length, adjust=False, min_periods=length).mean()


def ema_slope_value(ema_s: pd.Series, lookback: int) -> float | None:
    """(EMA_now - EMA_N_ago) / EMA_N_ago — §8.3."""
    try:
        if len(ema_s) < lookback + 1:
            return None
        now = float(ema_s.iloc[-1])
        ago = float(ema_s.iloc[-1 - lookback])
        if ago == 0 or np.isnan(now) or np.isnan(ago):
            return None
        return (now - ago) / abs(ago)
    except (IndexError, TypeError, ValueError):
        return None


def slope_state(slope: float | None, strong: float, weak: float) -> str:
    """Strong Up / Weak Up / Flat / Weak Down / Strong Down."""
    if slope is None:
        return "Unknown"
    if slope >= strong:
        return "Strong Up"
    if slope >= weak:
        return "Weak Up"
    if slope <= -strong:
        return "Strong Down"
    if slope <= -weak:
        return "Weak Down"
    return "Flat"

# =====================================================
#  Stochastic (§13)
# =====================================================

def stochastic(df: pd.DataFrame, k_len: int = 14, k_smooth: int = 3,
               d_len: int = 3) -> tuple[pd.Series, pd.Series]:
    low_min = df["low"].rolling(k_len).min()
    high_max = df["high"].rolling(k_len).max()
    denom = (high_max - low_min).replace(0, np.nan)
    raw_k = 100 * (df["close"] - low_min) / denom
    k = raw_k.rolling(k_smooth).mean()
    d = k.rolling(d_len).mean()
    return k, d


def cross_events(k: pd.Series, d: pd.Series) -> tuple[pd.Series, pd.Series]:
    """تقاطع صاعد/هابط لكل شمعة (True/False)."""
    ku = (k.shift(1) <= d.shift(1)) & (k > d)
    kd = (k.shift(1) >= d.shift(1)) & (k < d)
    return ku.fillna(False), kd.fillna(False)

# =====================================================
#  ATR + التقلب + الحجم (§35-36)
# =====================================================

def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    h, low, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - low, (h - prev_c).abs(), (low - prev_c).abs()], axis=1).max(axis=1)
    return tr.rolling(length).mean()


def volume_sma(df: pd.DataFrame, length: int = 20) -> pd.Series:
    return df["volume"].rolling(length).mean()

# =====================================================
#  Pivots مؤكدة (§9.1) — لا Look-ahead
# =====================================================

def pivot_highs(df: pd.DataFrame, k: int) -> pd.Series:
    """True عند الشمعة i إذا كانت قمة مؤكدة (تحتاج k شموع بعدها)."""
    h = df["high"]
    left = h.shift(1).rolling(k).max()
    right = h.shift(-1)[::-1].rolling(k).max()[::-1]
    return ((h > left) & (h >= right)).fillna(False)


def pivot_lows(df: pd.DataFrame, k: int) -> pd.Series:
    low = df["low"]
    left = low.shift(1).rolling(k).min()
    right = low.shift(-1)[::-1].rolling(k).min()[::-1]
    return ((low < left) & (low <= right)).fillna(False)

# =====================================================
#  أنماط التأكيد السعري (§14)
# =====================================================

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


def lower_wick(row: pd.Series) -> float:
    try:
        return float(min(row["open"], row["close"]) - row["low"])
    except Exception:
        return 0.0


def upper_wick(row: pd.Series) -> float:
    try:
        return float(row["high"] - max(row["open"], row["close"]))
    except Exception:
        return 0.0


def is_hammer(row: pd.Series) -> bool:
    try:
        o, c = row["open"], row["close"]
        b = _body(o, c)
        return b > 0 and lower_wick(row) >= 2 * b and upper_wick(row) <= b
    except Exception:
        return False


def is_shooting_star(row: pd.Series) -> bool:
    try:
        o, c = row["open"], row["close"]
        b = _body(o, c)
        return b > 0 and upper_wick(row) >= 2 * b and lower_wick(row) <= b
    except Exception:
        return False


def is_bullish_rejection(row: pd.Series) -> bool:
    """رفض سفلي قوي: ذيل ≥ 2× الجسم والإغلاق في النصف العلوي."""
    try:
        o, c, h, low = row["open"], row["close"], row["high"], row["low"]
        b = _body(o, c)
        rng = h - low
        return b > 0 and rng > 0 and lower_wick(row) >= 2 * b and (c - low) >= rng * 0.5
    except Exception:
        return False


def is_bearish_rejection(row: pd.Series) -> bool:
    try:
        o, c, h, low = row["open"], row["close"], row["high"], row["low"]
        b = _body(o, c)
        rng = h - low
        return b > 0 and rng > 0 and upper_wick(row) >= 2 * b and (h - c) >= rng * 0.5
    except Exception:
        return False


def is_trigger_break_up(prev: pd.Series, last: pd.Series) -> bool:
    """إغلاق شمعة الإشارة فوق قمة الشمعة السابقة (LONG)."""
    try:
        return float(last["close"]) > float(prev["high"])
    except Exception:
        return False


def is_trigger_break_dn(prev: pd.Series, last: pd.Series) -> bool:
    try:
        return float(last["close"]) < float(prev["low"])
    except Exception:
        return False
