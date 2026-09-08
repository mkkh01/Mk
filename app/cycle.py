# -*- coding: utf-8 -*-
"""محرك الدورة: يفحص السوق كل دقيقة → إشارات → فتح/إغلاق → ملخص تشخيصي.

خط الأنابيب:
  1) الأسعار الحية (طلب واحد)
  2) شموع 30 رمز × فريمين (بالتوازي)
  3) حساب المؤشرات + تقييم الاستراتيجية
  4) متابعة الصفقات المفتوحة (TP/SL/معاكسة/مدة)
  5) فتح صفقات جديدة (باحترام الحدود)
  6) حفظ Summary Cycle + إشعارات التلجرام
"""
import asyncio
import logging
import time
from collections import Counter
from datetime import datetime, timezone

from . import format as fmt
from . import history as hist
from . import paper_engine as pe
from .realtime import add_realized, try_claim_close
from .strategy import evaluate

log = logging.getLogger("cycle")


async def run_cycle(ctx) -> dict:
    """يشغل دورة واحدة مع قفل منع التداخل. ctx: كائن فيه cfg/cache/db/market/notifier."""
    cfg = ctx.cfg
    started = datetime.now(timezone.utc)
    t0 = time.time()
    ttl = max(20, cfg.CYCLE_SECONDS - 5)
    if not await ctx.cache.acquire_lock("cycle:lock", ttl=ttl):
        return {"status": "skipped", "reason": "دورة سابقة ما زالت تعمل - تم التخطي لمنع التداخل",
                "started_at": started.isoformat()}
    try:
        summary = await _run(ctx, started, t0)
    except Exception as e:
        log.exception("فشلت الدورة: %s", e)
        summary = {
            "cycle_id": -1, "status": "failed",
            "started_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_sec": round(time.time() - t0, 1),
            "errors": [f"استثناء حرج: {str(e)[:200]}"],
            "prices": {}, "klines": {}, "indicators": {}, "signals": {},
            "trades": {}, "equity": {}, "per_symbol": {},
        }
        try:
            await ctx.db.insert_cycle(summary)
            await ctx.cache.set("cycle:last", summary, ttl=3600)
            await ctx.notifier.send(f"❌ <b>فشلت دورة النظام</b>\n{str(e)[:300]}")
        except Exception:
            pass
    finally:
        await ctx.cache.release_lock("cycle:lock")
    return summary


async def _run(ctx, started, t0) -> dict:
    cfg, db, cache, market = ctx.cfg, ctx.db, ctx.cache, ctx.market
    cycle_id = await cache.incr("cycle:counter")
    if cycle_id <= 1:
        # بعد إعادة التشغيل بدون Redis: أكمل الترقيم من قاعدة البيانات
        try:
            last = await db.get_last_cycle()
            if last and isinstance(last.get("cycle_id"), int) and last["cycle_id"] >= 1:
                cycle_id = last["cycle_id"] + 1
                await cache.set("cycle:counter", cycle_id)
        except Exception:
            pass
    errors: list = []
    n = len(cfg.SYMBOLS)

    # ---------- 1) الأسعار الحية (REST + تغطية من البث اللحظي) ----------
    prices, price_src, price_err = await market.fetch_all_prices()
    if price_err:
        errors.append(f"الأسعار: {price_err[:150]}")
    ws_n = 0
    feed = getattr(ctx, "ws", None)
    if feed and feed.prices:
        for s, q in feed.prices.items():
            prices[s] = q
            ws_n += 1
        price_src = f"ws+{price_src}" if price_src else "ws"
    prices_ok = sum(1 for s in cfg.SYMBOLS if s in prices)
    ts_now = datetime.now(timezone.utc).isoformat()
    await cache.set("prices:live", {"ts": ts_now, "source": price_src, "prices": prices}, ttl=120)

    # ---------- 2) الشموع: الكاش التاريخي + تحديث خفيف ----------
    sem = asyncio.Semaphore(5)

    async def fetch_one(sym: str):
        async with sem:
            e_df, e_src, e_warn, e_stale = await hist.get_klines(ctx, sym, cfg.ENTRY_TF)
            t_df, t_src, t_warn, t_stale = await hist.get_klines(ctx, sym, cfg.TREND_TF)
            return sym, e_df, e_src, e_warn, e_stale, t_df, t_src, t_warn, t_stale

    gathered = await asyncio.gather(*[fetch_one(s) for s in cfg.SYMBOLS])
    klines_ok, failed_symbols, stale_symbols = 0, [], []
    data: dict = {}
    src_counter: Counter = Counter()
    for sym, e_df, e_src, e_warn, e_stale, t_df, t_src, t_warn, t_stale in gathered:
        if e_df is None or t_df is None:
            failed_symbols.append(sym)
            errors.append(f"{sym}: شموع ناقصة ({(e_warn or t_warn)[:100]})")
            continue
        if e_warn or t_warn:
            stale_symbols.append(sym)
        e_closed = e_df.iloc[:-1].reset_index(drop=True) if len(e_df) > 1 else e_df
        t_closed = t_df.iloc[:-1].reset_index(drop=True) if len(t_df) > 1 else t_df
        live = (prices.get(sym) or {}).get("price") or float(e_df["close"].iloc[-1])
        data[sym] = {"entry": e_closed, "trend": t_closed, "live": live,
                     "src": e_src or t_src}
        src_counter[e_src or t_src or "?"] += 1
        klines_ok += 1

    # ---------- 3) تقييم الاستراتيجية ----------
    accepted, reject_tally = [], Counter()
    per_symbol: dict = {}
    computed = 0
    for sym in cfg.SYMBOLS:
        if sym not in data:
            per_symbol[sym] = {"status": "data_fail"}
            continue
        d = data[sym]
        try:
            res = evaluate(sym, d["entry"], d["trend"], d["live"], cfg)
        except Exception as e:
            errors.append(f"{sym}: خطأ استراتيجية ({str(e)[:100]})")
            per_symbol[sym] = {"status": "strategy_error"}
            continue
        dbg = res.get("debug", {})
        if dbg:
            computed += 1
        sig = res.get("signal")
        if sig:
            accepted.append(sig)
            per_symbol[sym] = {"status": "signal", "side": sig.side,
                               "strength": sig.strength, "src": d["src"],
                               "live": dbg.get("live"), "stoch_k": dbg.get("stoch_k")}
        else:
            rej = res.get("rejects", ["مرفوضة"])[0]
            reject_tally[rej[:110]] += 1
            per_symbol[sym] = {"status": "reject", "reason": rej[:160], "src": d["src"],
                               "live": dbg.get("live"), "stoch_k": dbg.get("stoch_k")}

    # ---------- 4) متابعة الصفقات المفتوحة ----------
    now = datetime.now(timezone.utc)
    open_trades = await db.get_open_trades()
    accepted_map = {(s.symbol, s.side): s for s in accepted}
    closed_now: list = []
    open_pnl_total = 0.0
    for t in open_trades:
        sym = t["symbol"]
        live = (prices.get(sym) or {}).get("price") or t.get("current_price") or t["entry_price"]
        try:
            entry_dt = datetime.fromisoformat(t["entry_time"])
        except Exception:
            entry_dt = now
        opp = ((sym, "SHORT") in accepted_map) if t["side"] == "LONG" else ((sym, "LONG") in accepted_map)
        should, reason, px = pe.check_exit(t, live, entry_dt, now, cfg.MAX_HOLD_HOURS, opp)
        upnl = pe.unrealized(t, live, cfg.FEE_PCT)
        if should:
            if not await try_claim_close(cache, t["id"]):
                continue  # المراقب اللحظي يتولاها
            closed = pe.close_trade(t, px, reason, cfg.FEE_PCT)
            await db.delete_open_trade(t["id"])
            await db.insert_closed_trade(closed)
            await add_realized(ctx, closed.get("pnl", 0))
            closed_now.append(closed)
        else:
            open_pnl_total += upnl
            await db.update_open_trade(t["id"], {"current_price": live, "unrealized_pnl": round(upnl, 4)})

    # ---------- 5) الربح التراكمي والمحفظة ----------
    st = await db.get_state("realized_pnl", {"total": 0}) or {"total": 0}
    try:
        realized = float(st.get("total", 0))
    except (TypeError, ValueError):
        realized = 0.0
    for c in closed_now:
        realized += c.get("pnl", 0)
    await db.set_state("realized_pnl", {"total": round(realized, 4)})
    remaining_open = [t for t in open_trades
                      if t["id"] not in {c["id"] for c in closed_now}]
    # أعد حساب العائم بدقة بعد الإغلاقات
    open_pnl_total = 0.0
    for t in remaining_open:
        live = (prices.get(t["symbol"]) or {}).get("price") or t.get("current_price") or t["entry_price"]
        t["current_price"] = live
        t["unrealized_pnl"] = round(pe.unrealized(t, live, cfg.FEE_PCT), 4)
        open_pnl_total += t["unrealized_pnl"]
    equity = cfg.START_BALANCE + realized + open_pnl_total

    # ---------- 6) فتح صفقات جديدة ----------
    opened_now: list = []
    blocked = 0
    open_syms = {t["symbol"] for t in remaining_open}
    slots = cfg.MAX_OPEN_TRADES - len(remaining_open)
    for sig in sorted(accepted, key=lambda s: s.confluence, reverse=True):
        if sig.symbol in open_syms:
            continue
        if slots <= 0:
            blocked += 1
            continue
        sizing = pe.position_size(equity, cfg.RISK_PCT, sig.entry, sig.sl, cfg.LEVERAGE)
        if not sizing:
            reject_tally["حجم صفقة غير صالح (هامش/رصيد)"] += 1
            continue
        trade = pe.build_open_trade(sig, sizing, cfg.LEVERAGE)
        trade["snapshot"] = {**trade.get("snapshot", {}), "strength": sig.strength,
                             "confluence": sig.confluence, "rr": sig.rr}
        trade["current_price"] = trade["entry_price"]
        trade["unrealized_pnl"] = round(-(2 * trade["notional"] * cfg.FEE_PCT / 100), 4)
        await db.insert_open_trade(trade)
        opened_now.append(trade)
        open_syms.add(sig.symbol)
        slots -= 1
    open_pnl_total += sum(t["unrealized_pnl"] for t in opened_now)
    equity = cfg.START_BALANCE + realized + open_pnl_total

    # ---------- 7) لقطة المحفظة ----------
    perf_open = remaining_open + opened_now
    perf = pe.compute_performance(perf_open, [], realized, cfg.START_BALANCE, open_pnl_total)
    await db.insert_equity({"ts": now.isoformat(), "equity": round(equity, 2),
                            "balance": round(cfg.START_BALANCE + realized, 2),
                            "realized_pnl": round(realized, 2),
                            "open_pnl": round(open_pnl_total, 2),
                            "open_count": len(perf_open)})

    # ---------- 8) الملخص ----------
    finished = datetime.now(timezone.utc)
    status = "success"
    if failed_symbols or errors:
        status = "partial" if klines_ok >= n / 2 else "failed"
    mon_stats = await cache.get("monitor:stats") or {}
    if feed is not None:
        ws_info = {"connected": bool(feed.connected), "symbols": len(feed.prices),
                   "tick_age_sec": (round(time.time() - feed.last_msg, 1) if feed.last_msg else None)}
    else:
        ws_info = {"connected": False, "symbols": 0, "tick_age_sec": None}
    summary = {
        "cycle_id": cycle_id,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_sec": round(time.time() - t0, 1),
        "status": status,
        "data_source": {"prices": price_src or "—", "klines": dict(src_counter)},
        "prices": {"ok": prices_ok, "total": n},
        "klines": {"ok": klines_ok, "total": n, "failed_symbols": failed_symbols,
                   "stale_cache": stale_symbols[:10]},
        "realtime": {"ws": ws_info, "ws_prices_used": ws_n, "monitor": mon_stats},
        "indicators": {"computed": computed, "total": n},
        "signals": {
            "checked": len(data),
            "accepted": [{"symbol": s.symbol, "side": s.side, "strength": s.strength,
                          "rr": s.rr} for s in accepted],
            "rejected": sum(1 for v in per_symbol.values() if v.get("status") == "reject"),
            "top_reject": dict(reject_tally.most_common(5)),
        },
        "trades": {
            "opened": [{"symbol": t["symbol"], "side": t["side"], "entry": t["entry_price"]} for t in opened_now],
            "closed": [{"symbol": t["symbol"], "side": t["side"], "pnl": t["pnl"],
                        "result": t["result"], "exit_reason": t["exit_reason"]} for t in closed_now],
            "open_count": len(perf_open),
            "blocked_by_limit": blocked,
        },
        "equity": {"equity": round(equity, 2),
                   "return_pct": round((equity - cfg.START_BALANCE) / cfg.START_BALANCE * 100, 2)},
        "errors": errors[:20],
        "per_symbol": per_symbol,
    }
    await db.insert_cycle(summary)
    await cache.set("cycle:last", summary, ttl=3600)

    # ---------- 9) إشعارات التلجرام ----------
    try:
        for t in opened_now:
            await ctx.notifier.send(fmt.format_trade_opened(t, equity))
        for t in closed_now:
            await ctx.notifier.send(fmt.format_trade_closed(t, equity))
        if status == "failed":
            await ctx.notifier.send(
                f"⚠️ <b>دورة #{cycle_id} فشلت جزئياً</b>\n"
                f"الشموع: {klines_ok}/{n} | الأخطاء: {len(errors)}\n"
                f"اضغط زر ملخص الدورة للتفاصيل.")
    except Exception as e:
        log.warning("فشل إرسال إشعارات التلجرام: %s", e)

    log.info("دورة #%s: %s | شموع %s/%s | إشارات %s | فتح %s إغلاق %s | %.1fs",
             cycle_id, status, klines_ok, n, len(accepted),
             len(opened_now), len(closed_now), summary["duration_sec"])
    return summary
