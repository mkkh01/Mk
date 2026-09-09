# -*- coding: utf-8 -*-
"""الباك-تست (§47-49): نفس evaluate() الحية على بيانات تاريخية.

  python -m app.backtest BTCUSDT,ETHUSDT --days 60

- شموع مغلقة فقط، pivots مؤكدة، محاذاة زمنية صارمة (لا Look-ahead)
- الدخول بسعر إغلاق شمعة الإشارة + انزلاق، الإدارة على شموع MTF
- الوقف يُفحص قبل الهدف داخل نفس الشمعة (تحفظ)
- صفقة واحدة لكل رمز في المرة (مطابق للايف)؛ رأس مال ثابت بلا تراكم

ملاحظة: الخروج بالإشارة المعاكسة غير مشمول (يتطلب تقييم كل شمعة لكل رمز).
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from bisect import bisect_right
from datetime import datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config as app_config
from app import paper_engine as pe
from app.indicators import TF_SECONDS
from app.market_data import MarketDataClient
from app.strategy import evaluate

log = logging.getLogger("backtest")
LIMIT = 1000


VISION = "https://data-api.binance.vision/api/v3"


async def _fetch_many(client: MarketDataClient, symbol: str, tf: str,
                      since_ms: int, until_ms: int) -> pd.DataFrame:
    """ترقيم خلفي عبر Vision (endTime) لجلب نطاق تاريخي كامل."""
    import httpx
    frames, end = [], until_ms
    try:
        async with httpx.AsyncClient(timeout=30) as cli:
            for _ in range(60):
                r = await cli.get(f"{VISION}/klines", params={
                    "symbol": symbol, "interval": tf,
                    "limit": LIMIT, "endTime": end})
                rows = r.json()
                if not isinstance(rows, list) or not rows:
                    break
                df = pd.DataFrame([{
                    "ts": int(x[0]), "open": float(x[1]), "high": float(x[2]),
                    "low": float(x[3]), "close": float(x[4]),
                    "volume": float(x[5])} for x in rows])
                frames.append(df)
                first = int(df["ts"].iloc[0])
                if first <= since_ms or len(df) < LIMIT:
                    break
                end = first - 1
    except Exception as e:
        log.warning("جلب %s %s فشل: %s", symbol, tf, str(e)[:120])
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out = out[out["ts"] >= since_ms].drop_duplicates("ts").sort_values("ts")
    return out.reset_index(drop=True)


def _closed_before(df: pd.DataFrame, close_ms: int, tf_dur_ms: int, warmup: int) -> pd.DataFrame:
    """شموع مغلقة قبل لحظة close_ms (آخر شمعة مغلقة بالكامل)."""
    ts = df["ts"].to_numpy()
    n = bisect_right(ts, close_ms - tf_dur_ms)
    if n < warmup:
        return df.iloc[0:0]
    return df.iloc[:n]


async def backtest_symbol(client: MarketDataClient, symbol: str, s,
                          days: int, end_days_ago: int = 0) -> dict:
    now = datetime.now(timezone.utc)
    end_dt = now - timedelta(days=end_days_ago)
    since_ms = int((end_dt - timedelta(days=days + 30)).timestamp() * 1000)
    until_ms = int(end_dt.timestamp() * 1000)
    htf = await _fetch_many(client, symbol, s.HTF, since_ms, until_ms)
    mtf = await _fetch_many(client, symbol, s.MTF, since_ms, until_ms)
    ltf = await _fetch_many(client, symbol, s.LTF, since_ms, until_ms)
    if mtf.empty or ltf.empty or htf.empty:
        return {"symbol": symbol, "error": "تعذر جلب البيانات التاريخية"}
    mtf_d = TF_SECONDS[s.MTF] * 1000
    htf_d = TF_SECONDS[s.HTF] * 1000
    ltf_d = TF_SECONDS[s.LTF] * 1000
    eval_from = int((end_dt - timedelta(days=days)).timestamp() * 1000)
    warmup = s.EMA_LEN + s.PIVOT_K + 5

    closed, open_pos = [], None
    stats = {"bars": 0, "approved": 0, "watch": 0}
    mts = mtf["ts"].to_numpy()

    for i in range(len(mtf)):
        t_open = int(mts[i])
        if t_open < eval_from - mtf_d:
            continue
        t_close = t_open + mtf_d
        bar = mtf.iloc[i]
        # --- إدارة الصفقة المفتوحة على هذه الشمعة ---
        if open_pos is not None:
            tr, entry_i = open_pos["trade"], open_pos["bar_idx"]
            if i > entry_i:
                lo, hi, cl = float(bar["low"]), float(bar["high"]), float(bar["close"])
                exit_px, reason = 0.0, ""
                if tr["side"] == "LONG":
                    if lo <= tr["sl"]:
                        exit_px, reason = tr["sl"], "ضرب وقف الخسارة 🛑"
                    elif hi >= tr["tp"]:
                        exit_px, reason = tr["tp"], "تحقيق الهدف 🎯"
                else:
                    if hi >= tr["sl"]:
                        exit_px, reason = tr["sl"], "ضرب وقف الخسارة 🛑"
                    elif lo <= tr["tp"]:
                        exit_px, reason = tr["tp"], "تحقيق الهدف 🎯"
                if not reason:
                    held_h = (i - entry_i) * TF_SECONDS[s.MTF] / 3600
                    if held_h >= s.MAX_HOLD_HOURS:
                        exit_px, reason = cl, f"انتهت المدة القصوى ({s.MAX_HOLD_HOURS:.0f} ساعة) ⏱️"
                if reason:
                    c = pe.close_trade(tr, exit_px, reason, s.FEE_PCT, s.SLIPPAGE_BPS)
                    c["duration_min"] = round((i - entry_i) * TF_SECONDS[s.MTF] / 60, 1)
                    closed.append(c)
                    open_pos = None
                    continue
        # --- تقييم إشارة جديدة عند إغلاق الشمعة ---
        if open_pos is not None or t_open < eval_from:
            continue
        h = _closed_before(htf, t_close, htf_d, warmup)
        m = mtf.iloc[:i + 1]
        lt = _closed_before(ltf, t_close, ltf_d, warmup)
        if len(h) < warmup or len(m) < warmup or len(lt) < warmup:
            continue
        stats["bars"] += 1
        try:
            res = evaluate(symbol, h, m, lt, float(bar["close"]), s)
        except Exception:
            continue
        sig = res.signal
        if sig is None:
            continue
        if sig.status == "WATCH":
            stats["watch"] += 1
            continue
        if not res.approved:
            continue
        stats["approved"] += 1
        sizing = pe.position_size(s.START_BALANCE, s.RISK_PCT, sig.entry,
                                  sig.stop_loss, s.LEVERAGE, s.MIN_NOTIONAL,
                                  s.MAX_NOTIONAL_PCT, s.FIXED_NOTIONAL_USDT)
        if not sizing:
            continue
        tr = pe.build_open_trade(sig, sizing, s.LEVERAGE, s.SLIPPAGE_BPS)
        if s.FIXED_TP_NET_USDT > 0:
            tr["tp"] = pe.fixed_tp_price(tr["side"], tr["entry_price"], tr["qty"],
                                         s.FIXED_TP_NET_USDT, s.FEE_PCT, s.SLIPPAGE_BPS)
            tr["snapshot"]["fixed_tp_net"] = s.FIXED_TP_NET_USDT
        tr["entry_time"] = datetime.fromtimestamp(t_close / 1000, timezone.utc).isoformat()
        open_pos = {"trade": tr, "bar_idx": i}

    # إغلاق أي صفقة متبقية بسعر آخر إغلاق
    if open_pos is not None:
        tr = open_pos["trade"]
        c = pe.close_trade(tr, float(mtf["close"].iloc[-1]), "نهاية فترة الاختبار",
                           s.FEE_PCT, s.SLIPPAGE_BPS)
        closed.append(c)
    realized = sum(float(t.get("pnl", 0) or 0) for t in closed)
    perf = pe.compute_performance([], closed, realized, s.START_BALANCE, 0.0)
    slip_total = sum(float((t.get("snapshot") or {}).get("slippage_cost", 0) or 0)
                      for t in closed)
    perf["slippage_sensitivity"] = {
        "slippage_paid": round(slip_total, 2),
        "pnl_2x_slippage": round(realized - slip_total, 2),
    }
    return {"symbol": symbol, "stats": stats, "performance": perf, "trades": closed}


def _print_report(all_res: list, days: int, s):
    print(f"\n{'=' * 55}\n📊 نتائج الباك-تست ({days} يوم | {s.HTF}/{s.MTF}/{s.LTF} | حد {s.MIN_SCORE})\n{'=' * 55}")
    tot_tr, tot_pnl, tot_w = 0, 0.0, 0
    for r in all_res:
        if "error" in r:
            print(f"\n❌ {r['symbol']}: {r['error']}")
            continue
        p, st = r["performance"], r["stats"]
        tot_tr += p["total_trades"]
        tot_pnl += p["realized_pnl"]
        tot_w += p["wins"]
        print(f"\n<b>{r['symbol']}</b>: شموع {st['bars']} | معتمدة {st['approved']} | مراقبة {st['watch']}")
        print(f"  صفقات: {p['total_trades']} | فوز: {p['winrate']}% | PF: {p['profit_factor']} | "
              f"توقع: {p['expectancy_r']}R | تراجع: {p['max_drawdown_pct']}% | PnL: {p['realized_pnl']:+.2f}$")
        sens = p.get("slippage_sensitivity", {})
        print(f"  حساسية الانزلاق: مدفوع {sens.get('slippage_paid', 0)}$ | "
              f"PnL بانزلاق ×2: {sens.get('pnl_2x_slippage', 0):+.2f}$")
    n_sym = sum(1 for r in all_res if "error" not in r)
    print(f"\n{'=' * 55}\nالإجمالي ({n_sym} رموز): {tot_tr} صفقة | "
          f"فوز {(tot_w / tot_tr * 100 if tot_tr else 0):.1f}% | PnL: {tot_pnl:+.2f}$")
    print("⚠️ نتائج افتراضية: الخروج المعاكس غير مشمول، ورأس المال ثابت بلا تراكم.")


async def main_async(symbols: list, days: int, walk: int = 1):
    s = app_config.settings
    client = MarketDataClient(s.DATA_SOURCES)
    out = []
    for w in range(walk):
        span = max(1, days // max(walk, 1))
        end_ago = (walk - 1 - w) * span if walk > 1 else 0
        label = f" [نافذة {w + 1}/{walk}]" if walk > 1 else ""
        for sym in symbols:
            print(f"⏳ اختبار {sym}{label} ...")
            try:
                r = await backtest_symbol(client, sym, s, span, end_ago)
                r["window"] = w + 1 if walk > 1 else 0
                out.append(r)
            except Exception as e:
                out.append({"symbol": sym, "window": w + 1, "error": str(e)[:200]})
    await client.close()
    _print_report(out, days, s)
    os.makedirs("backtest_results", exist_ok=True)
    fp = os.path.join("backtest_results",
                      f"bt_{datetime.now(timezone.utc):%Y%m%d_%H%M}.json")
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, default=str)
    print(f"💾 حُفظت النتائج في {fp}")


def main():
    ap = argparse.ArgumentParser(description="باك-تست نظام المؤشرات الثلاثة")
    ap.add_argument("symbols", nargs="?", default="BTCUSDT",
                    help="رموز بفاصلة (افتراضي BTCUSDT)")
    ap.add_argument("--days", type=int, default=60, help="أيام فترة الاختبار (افتراضي 60)")
    ap.add_argument("--walk", type=int, default=1, help="عدد نوافذ Walk-Forward (افتراضي 1)")
    ap.add_argument("--min-score", type=int, default=None, help="تجاوز حد الاعتماد (افتراضي من الإعدادات)")
    ap.add_argument("--rr-min", type=float, default=None, help="تجاوز حد العائد (افتراضي من الإعدادات)")
    a = ap.parse_args()
    if a.min_score is not None:
        app_config.settings.MIN_SCORE = a.min_score
    if a.rr_min is not None:
        app_config.settings.RR_MIN = a.rr_min
    syms = [x.strip().upper() for x in a.symbols.split(",") if x.strip()]
    asyncio.run(main_async(syms, max(7, a.days), max(1, a.walk)))


if __name__ == "__main__":
    main()
