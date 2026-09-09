# -*- coding: utf-8 -*-
"""محرك الدورة V1 (§15-17 + §25 + §32-33 + §54):

  تحقق بيانات → 3 فريمات → قرار (فلاتر+نقاط) → بوابات (محفظة/تبريد/تكرار/سلامة)
  → فتح → مراقبة → ملخص تشخيصي
"""
import asyncio
import logging
import time
from collections import Counter
from datetime import datetime, timezone

from . import format as fmt
from . import history as hist
from . import paper_engine as pe
from .indicators import TF_SECONDS, validate_ohlc
from .realtime import add_realized, try_claim_close
from .strategy import evaluate

log = logging.getLogger("cycle")


async def run_cycle(ctx) -> dict:
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
            prev = await ctx.cache.get("cycle:last") or {}
            same = (prev.get("status") == "failed" and (prev.get("errors") or [""])[0]
                    == (summary["errors"] or [""])[0])
        except Exception:
            same = False
        try:
            await ctx.db.insert_cycle(summary)
            await ctx.cache.set("cycle:last", summary, ttl=3600)
            await ctx.db.insert_event("cycle_failed", {"error": str(e)[:300]})
            if not same:
                await ctx.notifier.send(
                    f"❌ <b>فشلت دورة النظام</b>\n{str(e)[:300]}"
                    "\n(لن تتكرر الرسالة حتى يتغير الخطأ — /stop يوقف المحاولات)")
        except Exception:
            pass
    finally:
        await ctx.cache.release_lock("cycle:lock")
    return summary


def _closed(df):
    return df.iloc[:-1].reset_index(drop=True) if len(df) > 1 else df


async def _run(ctx, started, t0) -> dict:
    cfg, db, cache, market = ctx.cfg, ctx.db, ctx.cache, ctx.market
    cycle_id = await cache.incr("cycle:counter")
    if cycle_id <= 1:
        try:
            last = await db.get_last_cycle()
            if last and isinstance(last.get("cycle_id"), int) and last["cycle_id"] >= 1:
                cycle_id = last["cycle_id"] + 1
                await cache.set("cycle:counter", cycle_id)
        except Exception:
            pass
    errors: list = []
    n = len(cfg.SYMBOLS)
    tfs = (cfg.HTF, cfg.MTF, cfg.LTF)

    # ---------- 1) الأسعار الحية ----------
    prices, price_src, price_err = await market.fetch_all_prices(cfg.SYMBOLS)
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
    await cache.set("prices:live", {"ts": datetime.now(timezone.utc).isoformat(),
                                    "source": price_src, "prices": prices}, ttl=120)

    # ---------- 2) الشموع (3 فريمات) + تحقق (§6.2) ----------
    sem = asyncio.Semaphore(5)

    async def fetch_one(sym: str):
        async with sem:
            out = {}
            for tf in tfs:
                df, src, warn, stale = await hist.get_klines(ctx, sym, tf)
                out[tf] = (df, src, warn, stale)
            return sym, out

    gathered = await asyncio.gather(*[fetch_one(s) for s in cfg.SYMBOLS])
    klines_ok, failed_symbols, stale_symbols, invalid_symbols = 0, [], [], []
    data: dict = {}
    src_counter: Counter = Counter()
    for sym, tfdata in gathered:
        (h_df, h_src, h_warn, h_stale), (m_df, m_src, m_warn, m_stale), \
            (l_df, l_src, l_warn, l_stale) = (tfdata[cfg.HTF], tfdata[cfg.MTF], tfdata[cfg.LTF])
        if h_df is None or m_df is None or l_df is None:
            failed_symbols.append(sym)
            continue
        bad = None
        for tf, df in ((cfg.HTF, h_df), (cfg.MTF, m_df), (cfg.LTF, l_df)):
            ok, why = validate_ohlc(df, TF_SECONDS.get(tf, 900))
            if not ok:
                bad = f"{tf}: {why}"
                break
        if bad:
            invalid_symbols.append(sym)
            errors.append(f"{sym}: بيانات مرفوضة ({bad})")
            continue
        if m_warn or m_stale:
            stale_symbols.append(sym)
        live = (prices.get(sym) or {}).get("price") or float(m_df["close"].iloc[-1])
        data[sym] = {"htf": _closed(h_df), "mtf": _closed(m_df), "ltf": _closed(l_df),
                     "live": live, "src": m_src or h_src, "stale_mtf": bool(m_stale)}
        src_counter[m_src or h_src or "?"] += 1
        klines_ok += 1

    # ---------- 3) القرار (§15-19) ----------
    approved, watch, reject_tally = [], [], Counter()
    per_symbol: dict = {}
    computed = 0
    for sym in cfg.SYMBOLS:
        if sym not in data:
            per_symbol[sym] = {"status": "data_fail"}
            continue
        d = data[sym]
        if d["stale_mtf"]:
            # §54: بيانات قديمة → لا صفقات جديدة (المراقبة تستمر)
            per_symbol[sym] = {"status": "stale", "src": d["src"]}
            reject_tally["بيانات قديمة (STALE)"] += 1
            continue
        try:
            res = evaluate(sym, d["htf"], d["mtf"], d["ltf"], d["live"], cfg)
        except Exception as e:
            errors.append(f"{sym}: خطأ استراتيجية ({str(e)[:100]})")
            per_symbol[sym] = {"status": "strategy_error"}
            continue
        dbg = res.debug or {}
        if dbg:
            computed += 1
        # حفظ القرار لأمر /why
        try:
            await cache.set(f"decision:{sym}", {
                "ts": datetime.now(timezone.utc).isoformat(),
                "approved": res.approved, "direction": res.direction,
                "score": res.score, "rejects": res.rejects[:4],
                "signal_id": res.signal.signal_id if res.signal else None,
                "snapshot": (res.signal.snapshot if res.signal else dbg),
            }, ttl=3600)
        except Exception:
            pass
        sig = res.signal
        if res.approved and sig:
            approved.append(sig)
            per_symbol[sym] = {"status": "signal", "side": sig.direction,
                               "score": sig.score, "src": d["src"],
                               "live": dbg.get("live"), "rr": sig.rr}
        elif sig and sig.status == "WATCH":
            watch.append(sig)
            per_symbol[sym] = {"status": "watch", "side": sig.direction,
                               "score": sig.score, "src": d["src"]}
            reject_tally[f"تحت المراقبة ({sig.score})"] += 1
        else:
            rej = (res.rejects or ["مرفوضة"])[0]
            reject_tally[rej[:110]] += 1
            per_symbol[sym] = {"status": "reject", "reason": rej[:160], "src": d["src"],
                               "live": dbg.get("live"), "score": res.score or None}

    # حفظ الإشارات المعتمدة وتحت المراقبة (§43)
    for sig in approved + watch:
        try:
            await db.insert_signal(sig.to_dict())
            if sig.status == "APPROVED":
                await db.insert_swing({
                    "symbol": sig.symbol, "timeframe": sig.mtf,
                    "type": ("up" if sig.direction == "LONG" else "down"),
                    "low": sig.swing_low, "high": sig.swing_high,
                    "quality": (sig.snapshot or {}).get("swing_q", 0)})
        except Exception as e:
            errors.append(f"حفظ إشارة {sig.symbol} فشل ({str(e)[:80]})")

    # ---------- 4) متابعة المفتوحة ----------
    now = datetime.now(timezone.utc)
    open_trades = await db.get_open_trades()
    accepted_map = {(s.symbol, s.direction): s for s in approved}
    closed_now: list = []
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
                continue
            closed = pe.close_trade(t, px, reason, cfg.FEE_PCT, cfg.SLIPPAGE_BPS)
            await db.delete_open_trade(t["id"])
            await db.insert_closed_trade(closed)
            await add_realized(ctx, closed.get("pnl", 0))
            closed_now.append(closed)
        else:
            await db.update_open_trade(t["id"], {"current_price": live, "unrealized_pnl": round(upnl, 4)})

    # ---------- 5) المحفظة ----------
    st = await db.get_state("realized_pnl", {"total": 0}) or {"total": 0}
    try:
        realized = float(st.get("total", 0))
    except (TypeError, ValueError):
        realized = 0.0
    closed_ids = {c["id"] for c in closed_now}
    remaining_open = [t for t in open_trades if t["id"] not in closed_ids]
    open_pnl_total = 0.0
    for t in remaining_open:
        live = (prices.get(t["symbol"]) or {}).get("price") or t.get("current_price") or t["entry_price"]
        t["current_price"] = live
        t["unrealized_pnl"] = round(pe.unrealized(t, live, cfg.FEE_PCT), 4)
        open_pnl_total += t["unrealized_pnl"]
    equity = cfg.START_BALANCE + realized + open_pnl_total
    existing_risk = sum(float(t.get("risk_amount", 0) or 0) for t in remaining_open)

    # ---------- 6) بوابات الدخول (§25/32/33/54) + فتح ----------
    opened_now: list = []
    blocked = Counter()
    degraded = bool(getattr(db, "degraded", False))
    try:
        paused = bool(await cache.get("engine:paused"))
    except Exception:
        paused = False
    recent_closed = await db.list_closed_trades(limit=200)
    open_setup_ids = {t.get("setup_id") for t in remaining_open if t.get("setup_id")}
    closed_setup_ids = {t.get("snapshot", {}).get("setup_id")
                        for t in recent_closed if isinstance(t.get("snapshot"), dict)}
    closed_by_sym: dict = {}
    for t in recent_closed:
        closed_by_sym.setdefault(t.get("symbol"), t.get("exit_time"))

    open_syms = {t["symbol"] for t in remaining_open}
    slots = cfg.MAX_OPEN_TRADES - len(remaining_open)
    for sig in sorted(approved, key=lambda s: s.score, reverse=True):
        try:
            if paused:
                blocked["المحرك متوقف مؤقتاً (/resume)"] += 1
                continue
            if degraded:
                blocked["قاعدة البيانات متعثرة (SAFE)"] += 1
                continue
            if sig.symbol in open_syms:
                continue
            if slots <= 0:
                blocked["الحد الأقصى للصفقات"] += 1
                continue
            # تبريد §32
            last_exit = closed_by_sym.get(sig.symbol)
            try:
                if last_exit and (now - datetime.fromisoformat(last_exit)).total_seconds() / 60 < cfg.COOLDOWN_MINUTES:
                    blocked[f"تبريد {cfg.COOLDOWN_MINUTES} دقيقة"] += 1
                    continue
            except Exception:
                pass
            # إشارة مكررة §33
            if sig.setup_id and (sig.setup_id in open_setup_ids or sig.setup_id in closed_setup_ids):
                blocked["إشارة مكررة"] += 1
                continue
            # مخاطرة المحفظة §25
            new_risk = equity * cfg.RISK_PCT / 100
            if existing_risk + new_risk > equity * cfg.MAX_PORTFOLIO_RISK / 100:
                blocked["تجاوز حد مخاطر المحفظة 3%"] += 1
                continue
            sizing = pe.position_size(equity, cfg.RISK_PCT, sig.entry, sig.stop_loss,
                                      cfg.LEVERAGE, cfg.MIN_NOTIONAL, cfg.MAX_NOTIONAL_PCT,
                                      cfg.FIXED_NOTIONAL_USDT)
            if not sizing:
                blocked["حجم صفقة غير صالح"] += 1
                continue
            trade = pe.build_open_trade(sig, sizing, cfg.LEVERAGE, cfg.SLIPPAGE_BPS)
            if cfg.FIXED_TP_NET_USDT > 0:
                trade["tp"] = pe.fixed_tp_price(trade["side"], trade["entry_price"], trade["qty"],
                                                cfg.FIXED_TP_NET_USDT, cfg.FEE_PCT, cfg.SLIPPAGE_BPS)
                trade["snapshot"]["fixed_tp_net"] = cfg.FIXED_TP_NET_USDT
            trade["current_price"] = trade["entry_price"]
            trade["unrealized_pnl"] = round(
                -(trade["snapshot"].get("entry_slip", 0)
                  + 2 * trade["notional"] * cfg.FEE_PCT / 100), 4)
            await db.insert_open_trade(trade)
            opened_now.append(trade)
            open_syms.add(sig.symbol)
            if sig.setup_id:
                open_setup_ids.add(sig.setup_id)
            existing_risk += sizing["risk_amount"]
            slots -= 1
        except Exception as e:
            log.exception("فشل فتح %s: %s", sig.symbol, e)
            errors.append(f"فتح {sig.symbol} فشل ({str(e)[:120]})")
            continue

    open_pnl_total += sum(t["unrealized_pnl"] for t in opened_now)
    equity = cfg.START_BALANCE + realized + open_pnl_total

    # ---------- 7) لقطة المحفظة ----------
    perf_open = remaining_open + opened_now
    await db.insert_equity({"ts": now.isoformat(), "equity": round(equity, 2),
                            "balance": round(cfg.START_BALANCE + realized, 2),
                            "realized_pnl": round(realized, 2),
                            "open_pnl": round(open_pnl_total, 2),
                            "open_count": len(perf_open)})

    # ---------- 8) الملخص ----------
    finished = datetime.now(timezone.utc)
    status = "success"
    if failed_symbols or invalid_symbols or errors:
        status = "partial" if klines_ok >= n / 2 else "failed"
    mon_stats = await cache.get("monitor:stats") or {}
    if feed is not None:
        ws_info = {"connected": bool(feed.connected), "symbols": len(feed.prices),
                   "feed": getattr(feed, "feed_name", ""),
                   "tick_age_sec": (round(time.time() - feed.last_msg, 1) if feed.last_msg else None)}
    else:
        ws_info = {"connected": False, "symbols": 0, "tick_age_sec": None}
    summary = {
        "cycle_id": cycle_id,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_sec": round(time.time() - t0, 1),
        "status": status,
        "timeframes": {"htf": cfg.HTF, "mtf": cfg.MTF, "ltf": cfg.LTF},
        "data_source": {"prices": price_src or "—", "klines": dict(src_counter)},
        "prices": {"ok": prices_ok, "total": n},
        "klines": {"ok": klines_ok, "total": n, "failed_symbols": failed_symbols,
                   "invalid_data": invalid_symbols[:10], "stale_cache": stale_symbols[:10]},
        "indicators": {"computed": computed, "total": n},
        "signals": {
            "checked": len(data),
            "approved": [{"symbol": s.symbol, "side": s.direction, "score": s.score,
                          "rr": s.rr} for s in approved],
            "watch": [{"symbol": s.symbol, "side": s.direction, "score": s.score} for s in watch[:10]],
            "watch_count": len(watch),
            "rejected": sum(1 for v in per_symbol.values() if v.get("status") == "reject"),
            "top_reject": dict(reject_tally.most_common(5)),
        },
        "gates": dict(blocked),
        "trades": {
            "opened": [{"symbol": t["symbol"], "side": t["side"], "entry": t["entry_price"],
                        "score": (t.get("snapshot") or {}).get("score")} for t in opened_now],
            "closed": [{"symbol": t["symbol"], "side": t["side"], "pnl": t["pnl"],
                        "result": t["result"], "exit_reason": t["exit_reason"]} for t in closed_now],
            "open_count": len(perf_open),
            "portfolio_risk_pct": round(existing_risk / equity * 100, 2) if equity else 0,
        },
        "equity": {"equity": round(equity, 2),
                   "return_pct": round((equity - cfg.START_BALANCE) / cfg.START_BALANCE * 100, 2)},
        "realtime": {"ws": ws_info, "ws_prices_used": ws_n, "monitor": mon_stats},
        "errors": errors[:20],
        "per_symbol": per_symbol,
    }
    # توافق مع القارئات القديمة
    summary["signals"]["accepted"] = summary["signals"]["approved"]
    await db.insert_cycle(summary)
    await cache.set("cycle:last", summary, ttl=3600)

    # ---------- 9) الإشعارات ----------
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

    log.info("دورة #%s: %s | شموع %s/%s | معتمدة %s مراقبة %s | فتح %s إغلاق %s | %.1fs",
             cycle_id, status, klines_ok, n, len(approved), len(watch),
             len(opened_now), len(closed_now), summary["duration_sec"])
    return summary
