# -*- coding: utf-8 -*-
"""اختبار ذاتي سريع (§51-lite): `python -m app.selftest`.

يبني شموعاً اصطناعية تحقق سيناريو LONG ثم يعكسها لسيناريو SHORT،
ويتحقق من: الفلاتر الصلبة + النقاط + RR + عدم Look-ahead + الانزلاق.
"""
import os
import sys
import types

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings as _S  # noqa: E402

from app.decision import compute_score, grade  # noqa: E402
from app.indicators import (  # noqa: E402
    ema, ema_slope_value, pivot_highs, pivot_lows, slope_state, stochastic,
    validate_ohlc)
from app.paper_engine import close_trade, fill_price, position_size  # noqa: E402
from app.strategy import evaluate  # noqa: E402
from app.structure import build_swing  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f" — {extra}" if extra and not cond else ""))


def _cfg(**kw):
    d = {k: getattr(_S, k) for k in dir(_S) if k.isupper()}
    d.update(kw)
    return types.SimpleNamespace(**d)


def _bars(closes: list, tf_sec: int, vol: float = 100.0, last_vol: float = 0) -> pd.DataFrame:
    rows = []
    ts = 1_700_000_000
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        h = max(o, c) + 0.05
        low = min(o, c) - 0.05
        v = last_vol if (last_vol and i == len(closes) - 1) else vol
        rows.append({"ts": ts + i * tf_sec, "open": o, "high": h, "low": low,
                     "close": c, "volume": v})
        prev = c
    return pd.DataFrame(rows)


def _lin(a: float, b: float, n: int) -> list:
    return [a + (b - a) * i / max(n - 1, 1) for i in range(n)]


def build_long_setup():
    # MTF 250: صعود 90→140 ثم هبوط لـ125 ثم صعود لـ150 ثم تراجع لـ135 (منطقة 61.8)
    mtf_c = _lin(90, 140, 181) + _lin(140, 125, 10)[1:] + _lin(125, 150, 41)[1:] \
        + _lin(150, 135, 20)[1:]
    mtf = _bars(mtf_c, 900, last_vol=150.0)
    # HTF 210: صعود مستمر
    htf = _bars(_lin(70, 145, 210), 3600)
    # LTF 210: صعود ثم سقوط ثم ارتداد ابتلاعي بتقاطع صاعد
    ltf_c = _lin(90, 145, 196) + _lin(145, 128, 13)[1:]
    ltf = _bars(ltf_c, 300)
    prev = {"ts": ltf["ts"].iloc[-1] + 300, "open": 133.0, "high": 133.2,
            "low": 127.5, "close": 128.5, "volume": 100.0}
    last = {"ts": prev["ts"] + 300, "open": 128.0, "high": 134.0,
            "low": 127.8, "close": 133.5, "volume": 100.0}
    ltf = pd.concat([ltf, pd.DataFrame([prev, last])], ignore_index=True)
    return htf, mtf, ltf


def _mirror(df: pd.DataFrame) -> pd.DataFrame:
    c = float(df["high"].max()) + float(df["low"].min()) + 50
    out = df.copy()
    for k in ("open", "high", "low", "close"):
        out[k] = c - out[k]
    out["high"], out["low"] = out["high"].where(
        out["high"] >= out["low"], out["low"]), out["low"].where(
        out["low"] <= out["high"], out["high"])
    # إصلاح القمم/القيعان بعد القلب
    hi = out[["open", "close"]].max(axis=1) + 0.05
    lo = out[["open", "close"]].min(axis=1) - 0.05
    out["high"] = out["high"].where(out["high"] >= hi, hi)
    out["low"] = out["low"].where(out["low"] <= lo, lo)
    return out


def main() -> int:
    cfg = _cfg()
    live = 135.0

    # ---- 1) LONG معتمدة ----
    htf, mtf, ltf = build_long_setup()
    dec = evaluate("TESTUSDT", htf, mtf, ltf, live, cfg)
    if not (dec.approved and dec.direction == "LONG" and dec.score >= 70):
        print("  DEBUG:", dec.rejects, dec.debug, getattr(dec.signal, "score_parts", None))
    check("LONG تُعتمد بدرجة ≥70", dec.approved and dec.direction == "LONG" and dec.score >= 70,
          f"{dec.rejects} {dec.debug}")
    if dec.signal:
        check("RR طبيعي ≥1.3", dec.signal.rr >= 1.3, str(dec.signal.rr))
        check("وقف تحت القاع بهامش ATR", dec.signal.stop_loss < dec.signal.swing_low)
        check("setup_id فريد ومحدد", bool(dec.signal.setup_id) and "TESTUSDT:LONG" in dec.signal.setup_id)
        check("الأسباب عربية (≥5)", len(dec.signal.reasons) >= 5)

    # ---- 2) SHORT بالعكس ----
    h2, m2, l2 = _mirror(htf), _mirror(mtf), _mirror(ltf)
    live_s = float(m2["close"].iloc[-1])
    dec_s = evaluate("TESTUSDT", h2, m2, l2, live_s, cfg)
    if not (dec_s.approved and dec_s.direction == "SHORT"):
        print("  DEBUG-S:", dec_s.rejects, dec_s.debug)
    check("SHORT تُعتمد بالبيانات المعكوسة", dec_s.approved and dec_s.direction == "SHORT",
          f"{dec_s.rejects}")

    # ---- 3) تعطيل الشورت يرفض البيع ----
    dec_off = evaluate("TESTUSDT", h2, m2, l2, live_s, _cfg(ALLOW_SHORTS=False))
    check("ALLOW_SHORTS=false يمنع SHORT", not dec_off.approved and dec_off.direction != "SHORT",
          f"{dec_off.rejects}")

    # ---- 4) رفض: دخول عند 50% يفشل RR ----
    mtf50 = mtf.copy()
    mtf50.loc[mtf50.index[-1], "close"] = 137.5
    dec50 = evaluate("TESTUSDT", htf, mtf50, ltf, 137.5, cfg)
    check("دخول 50% يُرفض (RR<1.3)", not dec50.approved, f"{dec50.rejects}")

    # ---- 5) رفض: ستوكاستيك غير مشبع ----
    ltf_hi = ltf.copy()
    ltf_hi.loc[ltf_hi.index[-1], ["open", "high", "low", "close"]] = [143.0, 144.0, 142.0, 143.5]
    dec_st = evaluate("TESTUSDT", htf, mtf, ltf_hi, live, cfg)
    check("ستوكاستيك غير مشبع يُرفض", not dec_st.approved, f"{dec_st.rejects}")

    # ---- 6) Swing: لا Look-ahead ----
    sw, err = build_swing(mtf, 2, live, 0.5)
    check("Swing صاعد مؤكد بجودة ≤6", sw is not None and sw.direction == "up" and 0 < sw.quality <= 6,
          err or str(getattr(sw, "quality", None)))
    ph, pl = pivot_highs(mtf, 2), pivot_lows(mtf, 2)
    check("لا pivots في آخر k شمعتين (تأكيد لاحق)",
          not bool(ph.iloc[-2:].any()) and not bool(pl.iloc[-2:].any()))

    # ---- 7) تحقق البيانات ----
    ok, _ = validate_ohlc(mtf, 900)
    check("بيانات سليمة تُقبل", ok)
    bad = mtf.copy()
    bad.loc[bad.index[-1], "high"] = 1.0
    check("High خاطئ يُرفض", not validate_ohlc(bad, 900)[0])
    dup = pd.concat([mtf, mtf.tail(1)], ignore_index=True)
    check("شمعة مكررة تُرفض", not validate_ohlc(dup, 900)[0])

    # ---- 8) النقاط والحدود ----
    p = {"side": "LONG", "ema_dist_pct": 1.0, "slope_state": "Strong Up", "htf_agree": True,
         "prox_618": 1.0, "prox_50": 1.0, "swing_quality": 6.0, "stoch_k": 15,
         "cross_age_bars": 0, "momentum_recovery": True, "confirmation": "ENGULFING",
         "trigger_break": True, "volume_ok": True,
         "zone_conf": {"horizontal": True, "ema_overlap": True, "prev_reactions": 2},
         "regime": "TRENDING_UP"}
    tot, parts = compute_score(p)
    check("مجموع النقاط = 100 للحالة المثالية", tot == 100, str(parts))
    check("الحدود 60/70/80/90", grade(59) != grade(60) and grade(69) != grade(70)
          and grade(79) != grade(80) and grade(89) != grade(90))
    p2 = dict(p, regime="RANGING")
    check("خصم التذبذب -10", compute_score(p2)[0] == 90, str(compute_score(p2)[0]))

    # ---- 9) الانزلاق والتنفيذ ----
    check("انزلاق الدخول أسوأ للشراء", fill_price("LONG", 100, 5, True) > 100)
    check("انزلاق الخروج أسوأ للشراء", fill_price("LONG", 100, 5, False) < 100)
    check("انزلاق الدخول أسوأ للبيع", fill_price("SHORT", 100, 5, True) < 100)

    # ---- 10) الميل والحالات ----
    e = ema(mtf["close"], 200)
    sl = ema_slope_value(e, 5)
    check("حالة الميل من 5 حالات", slope_state(sl, 0.001, 0.0003) in
          ("Strong Up", "Weak Up", "Flat", "Weak Down", "Strong Down"))
    k, d = stochastic(ltf)
    check("ستوكاستيك الإشارة مشبع بيعياً", float(k.iloc[-1]) < 20, f"K={float(k.iloc[-1]):.1f}")

    # ---- 11) EMA مقابل مرجع يدوي (§52) ----
    closes = mtf["close"]
    alpha = 2 / (200 + 1)
    ref = float(closes.iloc[0])
    for c in closes.iloc[1:]:
        ref = float(c) * alpha + ref * (1 - alpha)
    check("EMA يطابق الحساب اليدوي", abs(float(e.iloc[-1]) - ref) < 1e-6,
          f"{float(e.iloc[-1])} vs {ref}")

    # ---- 12) السوق المسطح (§52: ستوكاستيك مسطح) ----
    flat = pd.DataFrame([{"ts": 1_700_000_000 + i * 300, "open": 100.0, "high": 100.0,
                          "low": 100.0, "close": 100.0, "volume": 10.0} for i in range(210)])
    kf, df_ = stochastic(flat)
    import math as _m
    check("السوق المسطح تماماً يعطي NaN آمناً", _m.isnan(float(kf.iloc[-1])),
          f"K={float(kf.iloc[-1])}")
    dec_flat = evaluate("TESTUSDT", htf, mtf, flat, live, cfg)
    check("السوق المسطح يُرفض بسبب الستوكاستيك", not dec_flat.approved,
          f"{dec_flat.rejects}")

    # ---- 13) حواف المخاطرة (§52) ----
    check("مسافة صفر → لا حجم", position_size(10000, 1.0, 100.0, 100.0) is None)
    check("رصيد غير كافٍ → لا حجم", position_size(10, 1.0, 50000.0, 49000.0,
                                                  min_notional=5.0) is None)
    huge = position_size(10000, 1.0, 100.0, 50.0)
    check("وقف ضخم → حجم صغير صالح", huge is not None and huge["qty"] > 0)
    tiny = position_size(10000, 1.0, 100.0, 99.99)
    check("وقف صغير → حجم محدود بالقيمة القصوى",
          tiny is not None and tiny["notional"] <= 10000 * 0.30 + 1)

    # ---- 14) رياضيات الإغلاق (§52: تنفيذ) ----
    tr = {"id": "t", "symbol": "T", "side": "LONG", "entry_price": 100.0, "qty": 1.0,
          "margin": 100.0, "notional": 100.0, "sl": 95.0, "tp": 110.0,
          "risk_amount": 5.0, "entry_time": "2026-01-01T00:00:00+00:00",
          "entry_reasons": [], "snapshot": {"entry_slip": 0.05}}
    c_tp = close_trade(tr, 110.0, "تحقيق الهدف 🎯", 0.1, 5)
    check("إغلاق الهدف: رابح + R موجب + كود TP",
          c_tp["result"] == "WIN" and c_tp["r_multiple"] > 0 and c_tp["exit_code"] == "TP",
          str(c_tp["r_multiple"]))
    c_sl = close_trade(tr, 95.0, "ضرب وقف الخسارة 🛑", 0.1, 5)
    check("إغلاق الوقف: خاسر + R≈-1 + كود SL",
          c_sl["result"] == "LOSS" and -1.2 < c_sl["r_multiple"] < -0.8
          and c_sl["exit_code"] == "SL", str(c_sl["r_multiple"]))
    check("تكلفة الانزلاق مسجلة (دخول+خروج)",
          float(c_tp["snapshot"].get("slippage_cost", 0)) > 0.05)

    print(f"\n{'=' * 40}\nالنتيجة: {len(PASS)} ناجح | {len(FAIL)} فاشل")
    if FAIL:
        print("الفاشلة:", FAIL)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
