# -*- coding: utf-8 -*-
"""استراتيجية الملف: EMA200 (اتجاه) + فيبوناتشي (منطقة) + ستوكاستيك (توقيت).

هيكل متعدد الفريمات:
  - فريم الترند (1h): السعر فوق/تحت EMA200 يحدد المسموح (شراء/بيع فقط)
  - فريم الدخول (15m): EMA200 + منطقة فيبو 50%-61.8% + تشبع ستوكاستيك + شمعة انعكاسية

القاعدة العليا: لا دخول إلا بالتقاء الترند + المنطقة + التوقيت معاً.
"""
import math
from dataclasses import dataclass, field

import pandas as pd

from .indicators import (
    atr, detect_swing, ema, fib_extension_levels_down,
    fib_retracement_levels, is_bearish_engulfing, is_bullish_engulfing,
    is_hammer, is_shooting_star, stochastic,
)


@dataclass
class Signal:
    symbol: str
    side: str            # "LONG" أو "SHORT"
    entry: float
    sl: float
    tp: float
    risk_dist: float
    rr: float
    confluence: int      # عدد العوامل الملتقية (من 5)
    strength: str        # قوية جداً / قوية / متوسطة
    reasons: list = field(default_factory=list)
    snapshot: dict = field(default_factory=dict)


def _f(x, nd: int = 4):
    try:
        if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
            return None
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None


def evaluate(symbol: str, entry_df: pd.DataFrame, trend_df: pd.DataFrame,
             live_price: float, s) -> dict:
    """يفحص رمزاً واحداً. يرجع {"signal": Signal|None, "rejects": [...], "debug": {...}}."""
    out: dict = {"symbol": symbol, "signal": None, "rejects": [], "debug": {}}

    if live_price is None or live_price <= 0:
        out["rejects"].append("لا يوجد سعر حي")
        return out
    need = s.EMA_LEN + 5
    if entry_df is None or trend_df is None or len(entry_df) < need or len(trend_df) < need:
        out["rejects"].append(f"بيانات تاريخية غير كافية لحساب EMA{s.EMA_LEN}")
        return out

    e = entry_df.reset_index(drop=True)
    t = trend_df.reset_index(drop=True)

    ema_e = _f(ema(e["close"], s.EMA_LEN).iloc[-1])
    ema_e_prev = _f(ema(e["close"], s.EMA_LEN).iloc[-6])
    ema_t = _f(ema(t["close"], s.EMA_LEN).iloc[-1])
    if ema_e is None or ema_t is None:
        out["rejects"].append("تعذر حساب EMA200")
        return out

    k, d = stochastic(e, s.STOCH_K, s.STOCH_SMOOTH, s.STOCH_D)
    k_now, d_now = _f(k.iloc[-1], 2), _f(d.iloc[-1], 2)
    k_prev, d_prev = _f(k.iloc[-2], 2), _f(d.iloc[-2], 2)
    if k_now is None or d_now is None:
        out["rejects"].append("تعذر حساب الستوكاستيك (سوق مسطح)")
        return out

    swing = detect_swing(e, s.FIB_LOOKBACK)
    sw_low, sw_high = swing["swing_low"], swing["swing_high"]
    rng = sw_high - sw_low
    rng_pct = rng / live_price * 100 if live_price else 0
    atr_now = _f(atr(e).iloc[-1]) or 0.0

    fib_up = fib_retracement_levels(sw_low, sw_high)
    fib_dn = fib_extension_levels_down(sw_high, sw_low)
    tol = live_price * s.FIB_TOL_PCT / 100

    last_close_e = float(e["close"].iloc[-1])
    prev, last = e.iloc[-2], e.iloc[-1]
    bull_candle = is_bullish_engulfing(prev, last) or is_hammer(last)
    bear_candle = is_bearish_engulfing(prev, last) or is_shooting_star(last)
    cross_up = (k_prev is not None and d_prev is not None
                and k_prev <= d_prev and k_now > d_now)
    cross_dn = (k_prev is not None and d_prev is not None
                and k_prev >= d_prev and k_now < d_now)

    out["debug"] = {
        "live": _f(live_price, 6), "ema_entry": ema_e, "ema_trend": ema_t,
        "stoch_k": k_now, "stoch_d": d_now,
        "swing_low": _f(sw_low, 6), "swing_high": _f(sw_high, 6),
        "swing_pct": round(rng_pct, 2), "low_first": swing["low_first"],
        "fib50": _f(fib_up["50%"], 6), "fib618": _f(fib_up["61.8%"], 6),
        "fib50_dn": _f(fib_dn["50%"], 6), "fib618_dn": _f(fib_dn["61.8%"], 6),
        "atr": _f(atr_now, 6),
    }

    # ============ سيناريو الشراء LONG ============
    long_checks = []
    c_trend_l = live_price > ema_t and last_close_e > ema_e
    long_checks.append(("الترند صاعد (فوق EMA200)", c_trend_l))
    c_wave = swing["low_first"] and rng_pct >= s.MIN_SWING_PCT
    long_checks.append((f"موجة صاعدة بحجم {rng_pct:.1f}%", c_wave))
    in_buy_zone = (swing["low_first"] and rng > 0
                   and (fib_up["61.8%"] - tol) <= live_price <= (fib_up["50%"] + tol))
    long_checks.append(("السعر في منطقة الخصم 50%-61.8%", in_buy_zone))
    c_stoch_l = k_now < s.STOCH_OS and d_now < s.STOCH_OS
    long_checks.append((f"تشبع بيعي (ستوكاستيك {k_now:.0f})", c_stoch_l))

    # ============ سيناريو البيع SHORT ============
    short_checks = []
    c_trend_s = live_price < ema_t and last_close_e < ema_e
    short_checks.append(("الترند هابط (تحت EMA200)", c_trend_s))
    c_wave_s = (not swing["low_first"]) and rng_pct >= s.MIN_SWING_PCT
    short_checks.append((f"موجة هابطة بحجم {rng_pct:.1f}%", c_wave_s))
    in_sell_zone = ((not swing["low_first"]) and rng > 0
                    and (fib_dn["50%"] - tol) <= live_price <= (fib_dn["61.8%"] + tol))
    short_checks.append(("السعر في منطقة الارتداد 50%-61.8%", in_sell_zone))
    c_stoch_s = k_now > s.STOCH_OB and d_now > s.STOCH_OB
    short_checks.append((f"تشبع شرائي (ستوكاستيك {k_now:.0f})", c_stoch_s))

    long_ok = all(c for _, c in long_checks)
    short_ok = all(c for _, c in short_checks)

    if not long_ok and not short_ok:
        # سجل أسباب الرفض للتشخيص (الطرف الأقرب للاكتمال)
        def fails(checks):
            return [name for name, ok in checks if not ok]
        fl, fs = fails(long_checks), fails(short_checks)
        chosen = fl if len(fl) <= len(fs) else fs
        side_name = "شراء" if len(fl) <= len(fs) else "بيع"
        out["rejects"].append(f"لا تكتمل شروط الـ{side_name}: " + "، ".join(chosen))
        return out

    # ============ بناء الإشارة ============
    if long_ok and short_ok:
        # مستحيل نظرياً (فوق وتحت معاً) - نختار الأقوى تقاطعاً
        side = "LONG" if (cross_up and not cross_dn) else ("SHORT" if cross_dn else "LONG")
    else:
        side = "LONG" if long_ok else "SHORT"

    reasons: list = []
    conf = 0
    if side == "LONG":
        reasons.append(f"الترند صاعد: السعر فوق EMA200 على {s.TREND_TF} و{s.ENTRY_TF}")
        conf += 1
        reasons.append(f"تصحيح لمنطقة الخصم فيبو 50%-61.8% (موجة {rng_pct:.1f}%)")
        conf += 1
        reasons.append(f"تشبع بيعي: ستوكاستيك K={k_now:.0f} وD={d_now:.0f} تحت {s.STOCH_OS:.0f}")
        conf += 1
        if cross_up:
            reasons.append("تقاطع صاعد للستوكاستيك تحت 20 (أقوى توقيت)")
            conf += 1
        if bull_candle:
            reasons.append("شمعة انعكاسية صاعدة (ابتلاع/مطرقة)")
            conf += 1
        if ema_e_prev and ema_e > ema_e_prev:
            reasons.append("ميل EMA200 صاعد يدعم الصفقة")
        sl = sw_low - 0.15 * atr_now
        risk = live_price - sl
        tp_swing = sw_high
    else:
        reasons.append(f"الترند هابط: السعر تحت EMA200 على {s.TREND_TF} و{s.ENTRY_TF}")
        conf += 1
        reasons.append(f"ارتداد لمنطقة فيبو 50%-61.8% (موجة {rng_pct:.1f}%)")
        conf += 1
        reasons.append(f"تشبع شرائي: ستوكاستيك K={k_now:.0f} وD={d_now:.0f} فوق {s.STOCH_OB:.0f}")
        conf += 1
        if cross_dn:
            reasons.append("تقاطع هابط للستوكاستيك فوق 80 (أقوى توقيت)")
            conf += 1
        if bear_candle:
            reasons.append("شمعة انعكاسية هابطة (ابتلاع/شهاب)")
            conf += 1
        if ema_e_prev and ema_e < ema_e_prev:
            reasons.append("ميل EMA200 هابط يدعم الصفقة")
        sl = sw_high + 0.15 * atr_now
        risk = sl - live_price
        tp_swing = sw_low

    if risk <= 0:
        out["rejects"].append("وقف الخسارة غير صالح (خارج المنطقة)")
        return out
    risk_pct = risk / live_price * 100
    if risk_pct > 10:
        out["rejects"].append(f"وقف الخسارة واسع جداً ({risk_pct:.1f}%)")
        return out
    if risk_pct < 0.05:
        out["rejects"].append("وقف الخسارة ضيق بشكل مشكوك فيه")
        return out

    tp_min = live_price + s.RR_MIN * risk if side == "LONG" else live_price - s.RR_MIN * risk
    tp = max(tp_swing, tp_min) if side == "LONG" else min(tp_swing, tp_min)
    rr = abs(tp - live_price) / risk
    strength = "قوية جداً 💪" if conf >= 5 else ("قوية ✅" if conf == 4 else "متوسطة ⚖️")

    snap = dict(out["debug"])
    snap.update({"side": side, "sl": _f(sl, 6), "tp": _f(tp, 6),
                 "risk_pct": round(risk_pct, 3), "tfs": f"{s.TREND_TF}/{s.ENTRY_TF}"})

    out["signal"] = Signal(
        symbol=symbol, side=side, entry=live_price, sl=sl, tp=tp,
        risk_dist=risk, rr=round(rr, 2), confluence=conf,
        strength=strength, reasons=reasons, snapshot=snap,
    )
    return out
