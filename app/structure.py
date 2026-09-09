# -*- coding: utf-8 -*-
"""محرك البنية: Pivots مؤكدة → Swings → مناطق Fibonacci → التقاء (§9-12).

لا Look-ahead: الـ pivot لا يُستخدم إلا بعد اكتمال شموع تأكيده.
"""
from dataclasses import dataclass, field

import pandas as pd

from .indicators import pivot_highs, pivot_lows


@dataclass
class Swing:
    direction: str       # "up" (low→high) أو "down" (high→low)
    low: float
    high: float
    low_idx: int
    high_idx: int
    low_ts: int
    high_ts: int
    magnitude_pct: float
    duration_bars: int
    quality: float = 0.0          # 0-6 (§9.2)
    quality_parts: dict = field(default_factory=dict)


@dataclass
class FibZone:
    direction: str       # اتجاه الموجة: "up" أو "down"
    lvl50: float
    lvl618: float
    lvl786: float
    band_lo: float       # Primary 50→61.8 (الأدنى)
    band_hi: float       # Primary (الأعلى)


def confirmed_pivots(df: pd.DataFrame, k: int):
    """يرجع ([(idx,ts,price)] قيعان, [(idx,ts,price)] قمم) مؤكدة فقط."""
    ph = pivot_highs(df, k)
    pl = pivot_lows(df, k)
    n = len(df)
    highs, lows = [], []
    for i in range(n):
        if bool(ph.iloc[i]):
            highs.append((i, int(df["ts"].iloc[i]), float(df["high"].iloc[i])))
        if bool(pl.iloc[i]):
            lows.append((i, int(df["ts"].iloc[i]), float(df["low"].iloc[i])))
    return lows, highs


def _vol_ratio(df: pd.DataFrame, a: int, b: int, baseline: int = 60) -> float:
    try:
        seg = df["volume"].iloc[a:b + 1].mean()
        base = df["volume"].tail(baseline).mean()
        if base and base > 0:
            return float(seg / base)
    except Exception:
        pass
    return 1.0


def build_swing(df: pd.DataFrame, k: int, ref_price: float,
                min_swing_pct: float) -> tuple[Swing | None, str]:
    """يبني آخر Swing مكتمل من pivots مؤكدة. يرجع (Swing|None, سبب)."""
    if ref_price is None or ref_price <= 0:
        return None, "سعر مرجعي غير صالح"
    lows, highs = confirmed_pivots(df, k)
    if not lows or not highs:
        return None, "لا توجد pivots مؤكدة كافية"
    last_lo, last_hi = lows[-1], highs[-1]
    if last_hi[0] > last_lo[0]:
        direction = "up"
        lo, hi = last_lo, last_hi
    else:
        direction = "down"
        lo, hi = last_lo, last_hi
    rng = hi[2] - lo[2]
    if rng <= 0:
        return None, "Swing غير صالح (قمة ≤ قاع)"
    mag = rng / ref_price * 100
    if mag < min_swing_pct:
        return None, f"Swing ضعيف ({mag:.2f}% < {min_swing_pct}%)"
    dur = abs(hi[0] - lo[0])
    if dur < 3:
        return None, "Swing قصير جداً"

    # جودة الـ Swing (§9.2) من 6
    mag_pts = 2.0 if mag >= 4 else (1.5 if mag >= 2 else 1.0)
    dur_pts = 1.5 if dur >= 20 else (1.0 if dur >= 10 else 0.5)
    same = lows if direction == "up" else highs
    sep_bars = (same[-1][0] - same[-2][0]) if len(same) >= 2 else 999
    sep_pts = 1.0 if sep_bars >= 15 else 0.5
    vr = _vol_ratio(df, min(lo[0], hi[0]), max(lo[0], hi[0]))
    vol_pts = 1.5 if vr >= 1.3 else (1.0 if vr >= 1.1 else 0.5)
    quality = round(mag_pts + dur_pts + sep_pts + vol_pts, 2)

    sw = Swing(direction=direction, low=lo[2], high=hi[2],
               low_idx=lo[0], high_idx=hi[0], low_ts=lo[1], high_ts=hi[1],
               magnitude_pct=round(mag, 2), duration_bars=dur, quality=quality,
               quality_parts={"magnitude": mag_pts, "duration": dur_pts,
                              "separation": sep_pts, "volume": vol_pts,
                              "vol_ratio": round(vr, 2)})
    return sw, ""


def fib_zone(sw: Swing) -> FibZone:
    rng = sw.high - sw.low
    if sw.direction == "up":
        l50 = sw.high - rng * 0.50
        l618 = sw.high - rng * 0.618
        l786 = sw.high - rng * 0.786
        return FibZone("up", l50, l618, l786, min(l50, l618), max(l50, l618))
    l50 = sw.low + rng * 0.50
    l618 = sw.low + rng * 0.618
    l786 = sw.low + rng * 0.786
    return FibZone("down", l50, l618, l786, min(l50, l618), max(l50, l618))


def zone_position(price: float, zone: FibZone, tol: float) -> str:
    """primary (50-61.8) / deep (61.8-78.6) / outside."""
    lo, hi = zone.band_lo - tol, zone.band_hi + tol
    if lo <= price <= hi:
        return "primary"
    if zone.direction == "up":
        d_lo, d_hi = zone.lvl786 - tol, zone.lvl618 + tol
    else:
        d_lo, d_hi = zone.lvl618 - tol, zone.lvl786 + tol
    if min(d_lo, d_hi) <= price <= max(d_lo, d_hi):
        return "deep"
    return "outside"


def zone_confluence(df: pd.DataFrame, zone: FibZone, side: str,
                    ema_mtf: float | None, level_tol_pct: float,
                    ref_price: float) -> dict:
    """التقاء المنطقة (§12): مستوى أفقي + EMA + تفاعلات سابقة."""
    tol = ref_price * level_tol_pct / 100
    mid = (zone.band_lo + zone.band_hi) / 2
    win = df.tail(120)

    horizontal = False
    h_price = None
    try:
        if side == "LONG":
            piv = win.nsmallest(6, "low")
            for _, r in piv.iterrows():
                if abs(float(r["low"]) - mid) <= tol * 2 or \
                   (zone.band_lo - tol) <= float(r["low"]) <= (zone.band_hi + tol):
                    horizontal, h_price = True, float(r["low"])
                    break
        else:
            piv = win.nlargest(6, "high")
            for _, r in piv.iterrows():
                if abs(float(r["high"]) - mid) <= tol * 2 or \
                   (zone.band_lo - tol) <= float(r["high"]) <= (zone.band_hi + tol):
                    horizontal, h_price = True, float(r["high"])
                    break
    except Exception:
        pass

    ema_overlap = bool(ema_mtf and (zone.band_lo - tol) <= ema_mtf <= (zone.band_hi + tol))

    reactions = 0
    try:
        closes = win["close"].to_numpy()
        lows = win["low"].to_numpy()
        highs = win["high"].to_numpy()
        for i in range(len(win) - 6):
            if side == "LONG":
                touch = (zone.band_lo - tol) <= lows[i] <= (zone.band_hi + tol)
                recovered = closes[i + 5] > zone.band_hi
            else:
                touch = (zone.band_lo - tol) <= highs[i] <= (zone.band_hi + tol)
                recovered = closes[i + 5] < zone.band_lo
            if touch and recovered:
                reactions += 1
        reactions = min(reactions, 5)
    except Exception:
        reactions = 0

    return {"horizontal": horizontal, "horizontal_price": h_price,
            "ema_overlap": ema_overlap, "prev_reactions": reactions}


def regime_tag(slope_state: str, above_ema: bool) -> str:
    """تصنيف مبسط (§34): TRENDING_UP / TRENDING_DOWN / RANGING."""
    if slope_state == "Flat":
        return "RANGING"
    if above_ema and slope_state in ("Strong Up", "Weak Up"):
        return "TRENDING_UP"
    if (not above_ema) and slope_state in ("Strong Down", "Weak Down"):
        return "TRENDING_DOWN"
    if above_ema:
        return "TRENDING_UP"
    return "TRENDING_DOWN"
