# -*- coding: utf-8 -*-
"""الشموع السابقة: جلب تاريخي كامل عند الإقلاع (Warm-up) ثم تحديث خفيف كل دورة.

الفكرة: أول تشغيل يجلب 260 شمعة لكل رمز×فريم ويحفظها في Redis،
وبعدها كل دورة تجلب آخر ~8 شموع فقط وتدمجها — فيبدأ العمل دائماً
من آخر شمعة مغلقة + السعر الحي، بسرعة وبحمل أخف على المنصات.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

import pandas as pd

log = logging.getLogger("history")

KEEP_ROWS = 320          # عدد الشموع المحفوظة لكل رمز×فريم
CACHE_TTL = 6 * 3600     # صلاحية الكاش
STALE_AFTER = 20 * 60    # بعدها يُعاد الجلب الكامل إذا فشل التحديث
REFRESH_ROWS = 8         # شموع التحديث الخفيف كل دورة


def _key(symbol: str, tf: str) -> str:
    return f"klines:{symbol}:{tf}"


def _to_frame(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df.drop_duplicates(subset="ts").sort_values("ts").reset_index(drop=True)


def _merge(old_rows: list, fresh_df: pd.DataFrame) -> list:
    by_ts: dict = {}
    for r in old_rows or []:
        try:
            by_ts[int(r[0])] = [int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
        except (IndexError, TypeError, ValueError):
            continue
    for _, k in fresh_df.iterrows():
        try:
            t = int(k["ts"])
            by_ts[t] = [t, float(k["open"]), float(k["high"]), float(k["low"]),
                        float(k["close"]), float(k["volume"])]
        except (TypeError, ValueError):
            continue
    merged = [by_ts[t] for t in sorted(by_ts)]
    return merged[-KEEP_ROWS:]


async def _store(ctx, symbol: str, tf: str, rows: list):
    await ctx.cache.set(_key(symbol, tf), {
        "rows": rows,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }, ttl=CACHE_TTL)


async def warmup_history(ctx) -> dict:
    """الجلب التاريخي الكامل عند الإقلاع. يرجع إحصائية."""
    cfg = ctx.cfg
    t0 = time.time()
    sem = asyncio.Semaphore(5)
    ok, fail, failed = 0, 0, []

    async def one(sym: str, tf: str):
        nonlocal ok, fail
        async with sem:
            try:
                df, src, err = await ctx.market.fetch_klines(sym, tf, cfg.KLINES_LIMIT)
                if df is None:
                    fail += 1
                    failed.append(f"{sym} {tf}")
                    return
                rows = df[["ts", "open", "high", "low", "close", "volume"]].values.tolist()
                rows = [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
                        for r in rows]
                await _store(ctx, sym, tf, rows[-KEEP_ROWS:])
                ok += 1
            except Exception as e:
                fail += 1
                failed.append(f"{sym} {tf}")
                log.warning("warmup %s %s: %s", sym, tf, e)

    tfs = (cfg.HTF, cfg.MTF, cfg.LTF)
    await asyncio.gather(*[one(s, tf) for s in cfg.SYMBOLS for tf in tfs])
    stat = {"ok": ok, "fail": fail, "failed": failed[:10], "seconds": round(time.time() - t0, 1)}
    log.info("اكتمل الجلب التاريخي: %s ناجح / %s فاشل في %s ث", ok, fail, stat["seconds"])
    await ctx.cache.set("history:warmup", stat, ttl=CACHE_TTL)
    return stat


async def get_klines(ctx, symbol: str, tf: str):
    """يرجع (DataFrame أو None, المصدر, تحذير, هل_قديمة).

    يستخدم الكاش + تحديث خفيف، ويعود للجلب الكامل عند الحاجة.
    """
    cfg = ctx.cfg
    meta = await ctx.cache.get(_key(symbol, tf)) or {}
    rows = meta.get("rows") or []
    try:
        updated = datetime.fromisoformat(meta.get("updated_at", ""))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - updated).total_seconds()
    except Exception:
        age = 1e9

    need_full = len(rows) < cfg.EMA_LEN + 5 or age > STALE_AFTER
    if need_full:
        df, src, err = await ctx.market.fetch_klines(symbol, tf, cfg.KLINES_LIMIT)
        if df is not None:
            vals = df[["ts", "open", "high", "low", "close", "volume"]].values.tolist()
            vals = [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
                    for r in vals]
            await _store(ctx, symbol, tf, vals[-KEEP_ROWS:])
            return df, src, "", False
        if rows:
            return _to_frame(rows), "cache", f"جلب كامل فاشل، كاش قديم ({age / 60:.0f} د): {err[:80]}", True
        return None, "", err, False

    # تحديث خفيف: آخر الشموع فقط ثم دمج
    fresh, src, err = await ctx.market.fetch_klines(symbol, tf, limit=REFRESH_ROWS, min_rows=1)
    if fresh is not None:
        merged = _merge(rows, fresh)
        await _store(ctx, symbol, tf, merged)
        return _to_frame(merged), src, "", False
    return _to_frame(rows), "cache", f"تحديث خفيف فاشل: {err[:80]}", True
