# -*- coding: utf-8 -*-
"""استراتيجية المؤشرات الثلاثة (§15-16): خط أنابيب LONG/SHORT الكامل.

  HTF سياق → MTF إعداد → LTF إشعال → فلاتر صلبة → نقاط → عقد قرار (§65)

نقية: تفسير فقط، لا أوامر تخزين (§3.3). شموع مغلقة + pivots مؤكدة (لا Look-ahead).
"""
import math

import pandas as pd

from .decision import Decision, compute_score, grade, new_signal_id
from .indicators import (
    atr, cross_events, ema, ema_slope_value, is_bearish_engulfing,
    is_bearish_rejection, is_bullish_engulfing, is_bullish_rejection,
    is_hammer, is_shooting_star, is_trigger_break_dn, is_trigger_break_up,
    slope_state, stochastic, volume_sma,
)
from .structure import build_swing, fib_zone, regime_tag, zone_confluence, zone_position


def _f(x, nd: int = 4):
    try:
        if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
            return None
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None


def _need(df, n: int) -> bool:
    return df is not None and len(df) >= n


def evaluate(symbol: str, htf_df: pd.DataFrame, mtf_df: pd.DataFrame,
             ltf_df: pd.DataFrame, live_price: float, s) -> Decision:
    """يفحص رمزاً على الفريمات الثلاثة. يرجع Decision كاملاً (§65)."""
    dec = Decision(approved=False)
    need = s.EMA_LEN + 5

    # ---- حراس البيانات ----
    if live_price is None or live_price <= 0:
        dec.rejects.append("لا يوجد سعر حي")
        return dec
    for name, df in (("HTF", htf_df), ("MTF", mtf_df), ("LTF", ltf_df)):
        if not _need(df, need):
            dec.rejects.append(f"بيانات {name} غير كافية لـ EMA{s.EMA_LEN}")
            return dec
    h = htf_df.reset_index(drop=True)
    m = mtf_df.reset_index(drop=True)
    lt = ltf_df.reset_index(drop=True)

    # ---- EMA200 + الميل (HTF وMTF) ----
    try:
        ema_h = ema(h["close"], s.EMA_LEN)
        ema_m = ema(m["close"], s.EMA_LEN)
        ema_h_now, ema_m_now = _f(ema_h.iloc[-1]), _f(ema_m.iloc[-1])
    except Exception:
        dec.rejects.append("تعذر حساب EMA200")
        return dec
    if ema_h_now is None or ema_m_now is None:
        dec.rejects.append("تعذر حساب EMA200")
        return dec
    slope_h = ema_slope_value(ema_h, s.SLOPE_LOOKBACK)
    slope_m = ema_slope_value(ema_m, s.SLOPE_LOOKBACK)
    st_h = slope_state(slope_h, s.SLOPE_STRONG, s.SLOPE_WEAK)
    st_m = slope_state(slope_m, s.SLOPE_STRONG, s.SLOPE_WEAK)

    h_close = float(h["close"].iloc[-1])
    m_close = float(m["close"].iloc[-1])
    above_h = h_close > ema_h_now
    above_m = m_close > ema_m_now
    regime = regime_tag(st_h, above_h)

    # ---- Swing + Fibonacci على MTF ----
    sw, sw_err = build_swing(m, s.PIVOT_K, m_close, s.MIN_SWING_PCT)
    if sw is None:
        dec.rejects.append(f"Swing غير صالح: {sw_err}")
        return dec
    zone = fib_zone(sw)
    tol = m_close * s.FIB_TOL_PCT / 100
    pos = zone_position(m_close, zone, tol)

    # ---- Stochastic على LTF ----
    k, d = stochastic(lt, s.STOCH_K, s.STOCH_SMOOTH, s.STOCH_D)
    try:
        k_now, d_now = _f(k.iloc[-1], 2), _f(d.iloc[-1], 2)
        k_prev = _f(k.iloc[-2], 2)
    except (IndexError, TypeError):
        dec.rejects.append("تعذر حساب الستوكاستيك")
        return dec
    if k_now is None or d_now is None:
        dec.rejects.append("تعذر حساب الستوكاستيك (سوق مسطح)")
        return dec
    ku, kd = cross_events(k, d)
    last_n_up = [bool(ku.iloc[-1 - i]) for i in range(s.CROSS_LOOKBACK)]
    last_n_dn = [bool(kd.iloc[-1 - i]) for i in range(s.CROSS_LOOKBACK)]
    cross_up_age = next((i for i, v in enumerate(last_n_up) if v), 99)
    cross_dn_age = next((i for i, v in enumerate(last_n_dn) if v), 99)

    # ---- تأكيد سعري على شمعة إشارة LTF ----
    sig_bar, prev_bar = lt.iloc[-1], lt.iloc[-2]
    bull_eng = is_bullish_engulfing(prev_bar, sig_bar)
    bear_eng = is_bearish_engulfing(prev_bar, sig_bar)
    bull_rej = is_bullish_rejection(sig_bar) or is_hammer(sig_bar)
    bear_rej = is_bearish_rejection(sig_bar) or is_shooting_star(sig_bar)
    trig_up = is_trigger_break_up(prev_bar, sig_bar)
    trig_dn = is_trigger_break_dn(prev_bar, sig_bar)
    try:
        close_back_up = float(sig_bar["close"]) > float(sig_bar["open"]) and \
            float(sig_bar["low"]) < float(prev_bar["low"])
        close_back_dn = float(sig_bar["close"]) < float(sig_bar["open"]) and \
            float(sig_bar["high"]) > float(prev_bar["high"])
    except Exception:
        close_back_up = close_back_dn = False

    # ---- تقلب وحجم (MTF) ----
    atr_m = _f(atr(m).iloc[-1]) or 0.0
    vol_ratio = (atr_m / m_close) if m_close else 0
    try:
        v_sma = float(volume_sma(m).iloc[-1])
        v_last = float(m["volume"].iloc[-1])
        volume_ok = v_sma > 0 and v_last >= v_sma * 1.2
    except Exception:
        volume_ok = False

    dbg = {
        "live": _f(live_price, 6), "ema_htf": ema_h_now, "ema_mtf": ema_m_now,
        "slope_htf": _f(slope_h, 5), "slope_state_htf": st_h, "slope_state_mtf": st_m,
        "regime": regime, "stoch_k": k_now, "stoch_d": d_now,
        "cross_up_age": cross_up_age if cross_up_age < 90 else None,
        "cross_dn_age": cross_dn_age if cross_dn_age < 90 else None,
        "swing": sw.direction, "swing_mag": sw.magnitude_pct, "swing_q": sw.quality,
        "fib50": _f(zone.lvl50, 6), "fib618": _f(zone.lvl618, 6),
        "zone_pos": pos, "vol_ratio": round(vol_ratio, 5),
    }
    dec.debug = dbg

    # فلتر التقلب (§35) — ينطبق على الاتجاهين
    if vol_ratio < s.VOL_MIN or vol_ratio > s.VOL_MAX:
        dec.rejects.append(f"تقلب خارج النطاق (ATR/Close={vol_ratio:.4f})")
        return dec

    # ================= LONG (§15) =================
    long_hard: list = []   # (اسم, ناجح؟)
    long_hard.append(("ترند HTF صاعد", above_h))
    long_hard.append(("السعر فوق EMA200", m_close > ema_m_now and live_price > ema_m_now))
    long_hard.append(("ميل EMA غير هابط بقوة", st_h != "Strong Down"))
    long_hard.append(("Swing صاعد صالح", sw.direction == "up"))
    long_hard.append(("السعر في منطقة 50-61.8 (أو العميقة)", pos in ("primary", "deep")))
    conf_l = zone_confluence(m, zone, "LONG", ema_m_now, s.LEVEL_TOL_PCT, m_close)
    long_hard.append(("التقاء المنطقة (مستوى/EMA/تفاعل)",
                      bool(conf_l["horizontal"] or conf_l["ema_overlap"] or conf_l["prev_reactions"] >= 1)))
    long_hard.append((f"تشبع بيعي K={k_now:.0f}", k_now < s.STOCH_OS))
    long_hard.append(("تقاطع صاعد حديث", cross_up_age < s.CROSS_LOOKBACK))
    conf_lbl_l = ("ENGULFING" if bull_eng else
                  ("REJECTION" if bull_rej else
                   ("TRIGGER_BREAK" if trig_up else ("CLOSE_BACK" if close_back_up else ""))))
    long_hard.append(("تأكيد سعري صاعد", bool(conf_lbl_l)))
    long_ok = all(v for _, v in long_hard)

    # ================= SHORT (§16) =================
    short_hard: list = []
    short_hard.append(("ترند HTF هابط", not above_h))
    short_hard.append(("السعر تحت EMA200", m_close < ema_m_now and live_price < ema_m_now))
    short_hard.append(("ميل EMA غير صاعد بقوة", st_h != "Strong Up"))
    short_hard.append(("Swing هابط صالح", sw.direction == "down"))
    short_hard.append(("السعر في منطقة 50-61.8 (أو العميقة)", pos in ("primary", "deep")))
    conf_s = zone_confluence(m, zone, "SHORT", ema_m_now, s.LEVEL_TOL_PCT, m_close)
    short_hard.append(("التقاء المنطقة (مستوى/EMA/تفاعل)",
                       bool(conf_s["horizontal"] or conf_s["ema_overlap"] or conf_s["prev_reactions"] >= 1)))
    short_hard.append((f"تشبع شرائي K={k_now:.0f}", k_now > s.STOCH_OB))
    short_hard.append(("تقاطع هابط حديث", cross_dn_age < s.CROSS_LOOKBACK))
    conf_lbl_s = ("ENGULFING" if bear_eng else
                  ("REJECTION" if bear_rej else
                   ("TRIGGER_BREAK" if trig_dn else ("CLOSE_BACK" if close_back_dn else ""))))
    short_hard.append(("تأكيد سعري هابط", bool(conf_lbl_s)))
    short_ok = all(v for _, v in short_hard) and s.ALLOW_SHORTS

    if not long_ok and not short_ok:
        fl = [n for n, v in long_hard if not v]
        fs = [n for n, v in short_hard if not v]
        if len(fl) <= len(fs):
            dec.rejects.append("شروط الشراء ناقصة: " + "، ".join(fl[:3]))
        else:
            what = "البيع معطل (ALLOW_SHORTS)" if not s.ALLOW_SHORTS else "شروط البيع ناقصة: " + "، ".join(fs[:3])
            dec.rejects.append(what)
        dec.debug["hard_long_fail"] = fl
        dec.debug["hard_short_fail"] = fs
        return dec

    side = "LONG" if (long_ok and not short_ok) else ("SHORT" if (short_ok and not long_ok)
            else ("LONG" if cross_up_age <= cross_dn_age else "SHORT"))

    # ---- SL/TP (§21-22) ----
    atr_buf = atr_m * s.ATR_SL_MULT
    if side == "LONG":
        sl = sw.low - atr_buf
        tp = sw.high
        risk = live_price - sl
    else:
        sl = sw.high + atr_buf
        tp = sw.low
        risk = sl - live_price
    if risk <= 0:
        dec.rejects.append("وقف الخسارة غير صالح")
        return dec
    risk_pct = risk / live_price * 100
    if risk_pct > 10 or risk_pct < 0.05:
        dec.rejects.append(f"وقف خارج الحدود ({risk_pct:.2f}%)")
        return dec
    rr = abs(tp - live_price) / risk
    if rr < s.RR_MIN:
        dec.rejects.append(f"العائد 1:{rr:.2f} أقل من الحد 1:{s.RR_MIN:g}")
        return dec

    # ---- النقاط (§18) ----
    band_w = abs(zone.lvl50 - zone.lvl618) or 1e-9
    prox_618 = 1 - min(1.0, abs(m_close - zone.lvl618) / band_w)
    prox_50 = 1 - min(1.0, abs(m_close - zone.lvl50) / band_w)
    htf_agree = (st_m in ("Strong Up", "Weak Up")) if side == "LONG" else (st_m in ("Strong Down", "Weak Down"))
    ema_ref = ema_m_now
    score_p = {
        "side": side,
        "ema_dist_pct": abs(m_close - ema_ref) / ema_ref * 100 if ema_ref else 0,
        "slope_state": st_h, "htf_agree": htf_agree,
        "prox_618": prox_618, "prox_50": prox_50, "swing_quality": sw.quality,
        "stoch_k": k_now,
        "cross_age_bars": cross_up_age if side == "LONG" else cross_dn_age,
        "momentum_recovery": (k_prev is not None and (k_now > k_prev if side == "LONG" else k_now < k_prev)),
        "confirmation": conf_lbl_l if side == "LONG" else conf_lbl_s,
        "trigger_break": trig_up if side == "LONG" else trig_dn,
        "volume_ok": volume_ok,
        "zone_conf": conf_l if side == "LONG" else conf_s,
        "regime": regime,
    }
    score, parts = compute_score(score_p)

    if score < s.WATCH_SCORE:
        dec.rejects.append(f"النقاط {score}/100 أقل من حد المراقبة ({s.WATCH_SCORE})")
        dec.debug["score_parts"] = parts
        dec.score = score
        return dec

    status = "APPROVED" if score >= s.MIN_SCORE else "WATCH"
    nearest = "61.8" if prox_618 >= prox_50 else "50"
    if pos == "deep":
        nearest = "78.6"

    reasons = _reasons_ar(side, s, dbg, sw, pos, conf_l if side == "LONG" else conf_s,
                          conf_lbl_l if side == "LONG" else conf_lbl_s,
                          cross_up_age if side == "LONG" else cross_dn_age,
                          k_now, d_now, rr, score)

    sig_ts = int(lt["ts"].iloc[-1])
    swing_id = f"{sw.low_ts}-{sw.high_ts}"
    sig = __import__("app.decision", fromlist=["Signal"]).Signal(
        signal_id=new_signal_id(), symbol=symbol, direction=side,
        htf=s.HTF, mtf=s.MTF, ltf=s.LTF,
        ema_state=("BULLISH" if side == "LONG" else "BEARISH"),
        ema_slope=_f(slope_h, 5), swing_low=sw.low, swing_high=sw.high,
        swing_id=swing_id,
        fib_zone=nearest, fib_price_low=_f(zone.band_lo, 6), fib_price_high=_f(zone.band_hi, 6),
        stoch_k=k_now, stoch_d=d_now,
        stoch_cross=("BULLISH" if side == "LONG" else "BEARISH"),
        price_confirmation=(conf_lbl_l if side == "LONG" else conf_lbl_s) or "NONE",
        score=score, score_parts=parts, entry=live_price, stop_loss=sl,
        take_profit=tp, rr=round(rr, 2),
        setup_id=f"{symbol}:{side}:{swing_id}:{sig_ts}",
        status=status, reasons=reasons,
        snapshot={**dbg, "sl": _f(sl, 6), "tp": _f(tp, 6),
                  "risk_pct": round(risk_pct, 3), "grade": grade(score),
                  "tfs": f"{s.HTF}/{s.MTF}/{s.LTF}"},
    )
    dec.approved = (status == "APPROVED")
    dec.direction = side
    dec.trend_valid = dec.swing_valid = dec.fib_valid = True
    dec.stochastic_valid = dec.confirmation_valid = dec.rr_valid = True
    dec.score = score
    dec.signal = sig
    if status == "WATCH":
        dec.rejects.append(f"تحت المراقبة: النقاط {score} (الحد {s.MIN_SCORE})")
    return dec


def _reasons_ar(side, s, dbg, sw, pos, zconf, conf_lbl, cross_age, k_now, d_now, rr, score):
    up = side == "LONG"
    t = "صاعد" if up else "هابط"
    cross_t = "صاعد" if up else "هابط"
    sat_t = "بيعي" if up else "شرائي"
    lim = s.STOCH_OS if up else s.STOCH_OB
    z = "الأساسية 50-61.8" if pos == "primary" else "العميقة 61.8-78.6"
    out = [
        f"الترند {t}: السعر فوق EMA200 على {s.HTF} و{s.MTF}" if up
        else f"الترند {t}: السعر تحت EMA200 على {s.HTF} و{s.MTF}",
        f"ميل EMA200 ({dbg['slope_state_htf']}) يدعم الاتجاه",
        f"Swing {('صاعد' if up else 'هابط')} مؤكد بحجم {sw.magnitude_pct}% وجودة {sw.quality}/6",
        f"السعر في منطقة فيبو {z}",
    ]
    if zconf["horizontal"]:
        out.append(f"التقاء بمستوى أفقي {_f(zconf['horizontal_price'], 4)}")
    if zconf["ema_overlap"]:
        out.append("المنطقة تتداخل مع EMA200")
    if zconf["prev_reactions"] >= 1:
        out.append(f"تفاعلات سابقة من المنطقة ({zconf['prev_reactions']})")
    out += [
        f"تشبع {sat_t}: K={k_now:.0f} وD={d_now:.0f} ({'تحت' if up else 'فوق'} {lim:g})",
        f"تقاطع {cross_t} حديث على {s.LTF}",
        f"تأكيد سعري: {conf_lbl}",
        f"العائد الطبيعي 1:{rr:.2f} ≥ 1:{s.RR_MIN:g}",
        f"النقاط: {score}/100 ({grade(score)})",
    ]
    return out
