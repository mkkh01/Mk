# -*- coding: utf-8 -*-
"""الطبقة اللحظية:
  1) بث أسعار WebSocket من OKX (تيك لحظي لكل الرموز الثلاثين)
  2) مراقب سريع كل MONITOR_SECONDS يغلق الصفقات فور ضرب الوقف/الهدف + إشعار فوري
  3) أقفال منع الإغلاق المزدوج + تحديث آمن للربح التراكمي
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from . import format as fmt
from . import paper_engine as pe

log = logging.getLogger("realtime")


def _okx_inst(symbol: str) -> str:
    s = symbol.upper()
    base = s[:-4] if s.endswith("USDT") else s
    return f"{base}-USDT-SWAP"


# =====================================================
#  1) بث الأسعار اللحظي
# =====================================================

class WSPriceFeed:
    def __init__(self, symbols: list, cache, flush_seconds: int = 2):
        self.symbols = list(symbols)
        self.cache = cache
        self.flush_seconds = flush_seconds
        self.prices: dict = {}          # sym -> {price, change_pct}
        self.connected = False
        self.last_msg = 0.0
        self.reconnects = 0
        self.feed_name = ""
        self._task: asyncio.Task | None = None
        self._stop = False

    async def start(self):
        self._stop = False
        self._task = asyncio.create_task(self._run(), name="ws-feed")

    async def stop(self):
        self._stop = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.connected = False

    def tick_age(self) -> float | None:
        return (time.time() - self.last_msg) if self.last_msg else None

    async def _run(self):
        backoff = 2
        while not self._stop:
            try:
                try:
                    await self._stream_binance()
                except Exception as e:
                    log.warning("بث Binance انقطع (%s) - التحويل لـ OKX", str(e)[:100])
                    self.connected = False
                    if not self._stop:
                        await self._stream_okx()
                backoff = 2
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.connected = False
                self.reconnects += 1
                log.warning("انقطع بث الأسعار (%s) - إعادة المحاولة بعد %s ث", str(e)[:120], backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _stream_okx(self):
        import websockets
        url = "wss://ws.okx.com:8443/ws/v5/public"
        async with websockets.connect(url, ping_interval=20, ping_timeout=20,
                                      close_timeout=5, max_size=2 * 1024 * 1024) as ws:
            args = [{"channel": "tickers", "instId": _okx_inst(s)} for s in self.symbols]
            await ws.send(json.dumps({"op": "subscribe", "args": args}))
            self.connected = True
            self.feed_name = "okx-ws"
            log.info("متصل ببث OKX اللحظي (%s رمز)", len(args))
            last_flush, last_ping = 0.0, time.time()
            async for raw in ws:
                if self._stop:
                    return
                if raw == "pong":
                    continue
                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                data = msg.get("data")
                if data:
                    for t in data:
                        inst = t.get("instId", "")
                        if not inst.endswith("-USDT-SWAP"):
                            continue
                        try:
                            last = float(t["last"])
                            op = float(t.get("open24h") or 0)
                            ch = ((last - op) / op * 100) if op else 0.0
                            sym = inst[:-len("-USDT-SWAP")] + "USDT"
                            self.prices[sym] = {"price": last, "change_pct": round(ch, 2)}
                            self.last_msg = time.time()
                        except (TypeError, ValueError):
                            continue
                now = time.time()
                if now - last_ping > 20:
                    last_ping = now
                    try:
                        await ws.send("ping")
                    except Exception:
                        pass
                if now - last_flush > self.flush_seconds and self.prices:
                    last_flush = now
                    await self._flush()


    async def _flush(self):
        try:
            await self.cache.set("prices:live", {
                "ts": datetime.now(timezone.utc).isoformat(),
                "source": self.feed_name or "ws",
                "prices": dict(self.prices),
            }, ttl=120)
        except Exception:
            pass

    async def _stream_binance(self):
        import websockets
        streams = "/".join(f"{s.lower()}@miniTicker" for s in self.symbols)
        url = f"wss://data-stream.binance.vision/stream?streams={streams}"
        async with websockets.connect(url, ping_interval=30, ping_timeout=30,
                                      close_timeout=5, max_size=2 * 1024 * 1024) as ws:
            self.connected = True
            self.feed_name = "binance-ws"
            log.info("متصل ببث Binance اللحظي (%s رمز)", len(self.symbols))
            last_flush = 0.0
            async for raw in ws:
                if self._stop:
                    return
                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                d = msg.get("data") or {}
                if not d.get("s"):
                    continue
                try:
                    last = float(d["c"])
                    op = float(d.get("o") or 0)
                    ch = ((last - op) / op * 100) if op else 0.0
                    self.prices[d["s"].upper()] = {"price": last, "change_pct": round(ch, 2)}
                    self.last_msg = time.time()
                except (TypeError, ValueError, KeyError):
                    continue
                now = time.time()
                if now - last_flush > self.flush_seconds and self.prices:
                    last_flush = now
                    await self._flush()


# =====================================================
#  2) أدوات الأمان المشتركة (الدورة + المراقب)
# =====================================================

async def try_claim_close(cache, trade_id: str) -> bool:
    """حجز إغلاق الصفقة لمنع إغلاقها مرتين (من الدورة والمراقب معاً)."""
    return await cache.acquire_lock(f"trade:close:{trade_id}", ttl=120)


async def add_realized(ctx, amount: float) -> float:
    """يضيف للربح التراكمي بأمان مع إعادة المحاولة عند التزاحم."""
    for _ in range(6):
        locked = await ctx.cache.acquire_lock("state:realized", ttl=10)
        if locked:
            try:
                st = await ctx.db.get_state("realized_pnl", {"total": 0}) or {"total": 0}
                try:
                    total = float(st.get("total", 0)) + float(amount or 0)
                except (TypeError, ValueError):
                    total = float(amount or 0)
                await ctx.db.set_state("realized_pnl", {"total": round(total, 4)})
                return total
            finally:
                await ctx.cache.release_lock("state:realized")
        await asyncio.sleep(0.3)
    log.error("تعذر تحديث الربح التراكمي بعد عدة محاولات!")
    st = await ctx.db.get_state("realized_pnl", {"total": 0}) or {"total": 0}
    try:
        return float(st.get("total", 0))
    except (TypeError, ValueError):
        return 0.0


async def current_equity(ctx, live: dict | None = None) -> tuple[float, float]:
    """يرجع (المحفظة الكلية، الربح التراكمي)."""
    st = await ctx.db.get_state("realized_pnl", {"total": 0}) or {"total": 0}
    try:
        realized = float(st.get("total", 0))
    except (TypeError, ValueError):
        realized = 0.0
    opens = await ctx.db.get_open_trades()
    live = live or {}
    upnl = 0.0
    for t in opens:
        px = (live.get(t["symbol"]) or {}).get("price") or t.get("current_price") or t["entry_price"]
        try:
            upnl += pe.unrealized(t, px, ctx.cfg.FEE_PCT)
        except Exception:
            continue
    return ctx.cfg.START_BALANCE + realized + upnl, realized


# =====================================================
#  3) المراقب السريع (وقف/هدف فوري)
# =====================================================

async def _live_map(ctx) -> dict:
    """أحدث الأسعار: البث اللحظي أولاً ثم كاش الدورة."""
    live: dict = {}
    feed: WSPriceFeed | None = getattr(ctx, "ws", None)
    if feed and feed.prices:
        live.update(feed.prices)
    try:
        cached = await ctx.cache.get("prices:live") or {}
        for s, q in (cached.get("prices") or {}).items():
            live.setdefault(s, q)
    except Exception:
        pass
    return live


async def monitor_once(ctx) -> dict:
    """فحص سريع واحد: وقف/هدف/مدة قصوى → إغلاق فوري + إشعار."""
    cfg = ctx.cfg
    now = datetime.now(timezone.utc)
    stats = {"checked": 0, "closed": 0, "no_price": 0, "last_run": now.isoformat()}
    opens = await ctx.db.get_open_trades()
    stats["checked"] = len(opens)
    if not opens:
        await ctx.cache.set("monitor:stats", stats, ttl=300)
        return stats
    live = await _live_map(ctx)
    closed_ids: set = set()
    for t in opens:
        px = (live.get(t["symbol"]) or {}).get("price")
        if not px:
            stats["no_price"] += 1
            continue
        hit, reason, exit_px = False, "", 0.0
        sl, tp = t["sl"], t["tp"]
        if t["side"] == "LONG":
            if px <= sl:
                hit, reason, exit_px = True, "ضرب وقف الخسارة 🛑", sl
            elif px >= tp:
                hit, reason, exit_px = True, "تحقيق الهدف 🎯", tp
        else:
            if px >= sl:
                hit, reason, exit_px = True, "ضرب وقف الخسارة 🛑", sl
            elif px <= tp:
                hit, reason, exit_px = True, "تحقيق الهدف 🎯", tp
        if not hit:
            try:
                entry_dt = datetime.fromisoformat(t["entry_time"])
                held_h = (now - entry_dt).total_seconds() / 3600
            except Exception:
                held_h = 0
            if held_h >= cfg.MAX_HOLD_HOURS:
                hit, reason, exit_px = True, f"انتهت المدة القصوى ({cfg.MAX_HOLD_HOURS:.0f} ساعة) ⏱️", px
        if not hit:
            continue
        if not await try_claim_close(ctx.cache, t["id"]):
            continue  # الدورة الرئيسية تتولاها
        still = [x for x in await ctx.db.get_open_trades() if x["id"] == t["id"]]
        if not still:
            continue
        closed = pe.close_trade(still[0], exit_px, reason, cfg.FEE_PCT)
        await ctx.db.delete_open_trade(t["id"])
        await ctx.db.insert_closed_trade(closed)
        await add_realized(ctx, closed.get("pnl", 0))
        closed_ids.add(t["id"])
        stats["closed"] += 1
        try:
            equity, _ = await current_equity(ctx, live)
            await ctx.notifier.send("⚡ <b>إغلاق فوري (مراقبة لحظية)</b>\n" + fmt.format_trade_closed(closed, equity))
        except Exception as e:
            log.warning("فشل إشعار الإغلاق الفوري: %s", e)
        log.info("إغلاق فوري: %s %s %s", closed["symbol"], closed["result"], closed["pnl"])

    # تحديث العائم في القاعدة (مخفّض: كل ~15 ثانية)
    try:
        last_u = await ctx.cache.get("monitor:unreal_ts") or 0
        if time.time() - float(last_u or 0) > 15:
            for t in opens:
                if t["id"] in closed_ids:
                    continue
                px = (live.get(t["symbol"]) or {}).get("price")
                if not px:
                    continue
                try:
                    await ctx.db.update_open_trade(t["id"], {
                        "current_price": px,
                        "unrealized_pnl": round(pe.unrealized(t, px, cfg.FEE_PCT), 4)})
                except Exception:
                    pass
            await ctx.cache.set("monitor:unreal_ts", time.time(), ttl=120)
    except Exception:
        pass
    stats["ws_age_sec"] = round(time.time() - ctx.ws.last_msg, 1) if getattr(ctx, "ws", None) and ctx.ws.last_msg else None
    stats["ws_connected"] = bool(getattr(ctx, "ws", None) and ctx.ws.connected)
    await ctx.cache.set("monitor:stats", stats, ttl=300)
    return stats


async def monitor_loop(ctx):
    """حلقة المراقبة الدائمة."""
    await asyncio.sleep(10)  # مهلة للإقلاع والجلب التاريخي
    log.info("بدأ المراقب اللحظي (كل %s ثوانٍ)", ctx.cfg.MONITOR_SECONDS)
    while True:
        try:
            await monitor_once(ctx)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.exception("خطأ المراقب اللحظي: %s", e)
        await asyncio.sleep(ctx.cfg.MONITOR_SECONDS)
