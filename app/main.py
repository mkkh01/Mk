# -*- coding: utf-8 -*-
"""نقطة الدخول: FastAPI + مجدول الدورات + بوت التلجرام."""
import asyncio
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

import config as app_config
from . import paper_engine as pe
from .cache import Cache
from .cycle import run_cycle
from .database import create_database
from .history import warmup_history
from .market_data import MarketDataClient
from .realtime import WSPriceFeed, monitor_loop
from .telegram_bot import TelegramNotifier, create_bot, stop_bot

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")
log = logging.getLogger("main")

settings = app_config.settings
APP_VERSION = "27f69f4"


class AppCtx:
    def __init__(self):
        self.cfg = settings
        self.cache = Cache(settings.REDIS_URL)
        self.db = create_database(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        self.market = MarketDataClient(settings.DATA_SOURCES)
        self.notifier = TelegramNotifier(None, settings.TELEGRAM_CHAT_IDS)
        self.tg_app = None
        self.webhook_mode = False
        self.scheduler: AsyncIOScheduler | None = None
        self.ws: WSPriceFeed | None = None
        self._monitor_task: asyncio.Task | None = None


ctx = AppCtx()


async def _cycle_job():
    try:
        await run_cycle(ctx)
    except Exception as e:
        log.exception("خطأ غير متوقع في مهمة الدورة: %s", e)


async def _first_cycle_delayed():
    await asyncio.sleep(8)
    log.info("تشغيل أول دورة بعد الإقلاع...")
    await _cycle_job()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("🚀 إقلاع نظام التداول الورقي (%s زوج)", len(settings.SYMBOLS))
    await ctx.cache.connect()
    await ctx.db.connect()
    # 1) الجلب التاريخي: شموع سابقة لكل الرموز ليبدأ العمل من آخر شمعة
    try:
        stat = await warmup_history(ctx)
        log.info("المخزون التاريخي: %s/%s في %s ث", stat["ok"], stat["ok"] + stat["fail"], stat["seconds"])
    except Exception as e:
        log.warning("الجلب التاريخي فشل (ستجلبه الدورات تلقائياً): %s", e)
    # 2) بث الأسعار اللحظي + المراقب السريع
    if settings.WS_ENABLE:
        ctx.ws = WSPriceFeed(settings.SYMBOLS, ctx.cache)
        await ctx.ws.start()
        ctx._monitor_task = asyncio.create_task(monitor_loop(ctx), name="monitor")
    try:
        ctx.tg_app, ctx.webhook_mode = await create_bot(ctx)
    except Exception as e:
        log.error("فشل تشغيل بوت التلجرام - النظام يستمر بدونه: %s", str(e)[:200])
        ctx.tg_app, ctx.webhook_mode = None, False
    ctx.notifier = TelegramNotifier(ctx.tg_app, settings.TELEGRAM_CHAT_IDS)
    try:
        await ctx.db.insert_event("system_startup", {
            "db": ctx.db.mode, "cache": ctx.cache.mode,
            "timeframes": f"{settings.HTF}/{settings.MTF}/{settings.LTF}",
            "symbols": len(settings.SYMBOLS)})
    except Exception:
        pass
    ctx.scheduler = AsyncIOScheduler()
    ctx.scheduler.add_job(_cycle_job, "interval", seconds=settings.CYCLE_SECONDS,
                          coalesce=True, max_instances=1, misfire_grace_time=30,
                          id="cycle")
    ctx.scheduler.start()
    asyncio.create_task(_first_cycle_delayed())
    try:
        await ctx.notifier.send(
            "🟢 <b>النظام يعمل الآن</b>\n"
            f"📊 {len(settings.SYMBOLS)} زوج | دورة كل {settings.CYCLE_SECONDS} ثانية\n"
            f"💾 تخزين: {ctx.db.mode} | ⚡ كاش: {ctx.cache.mode}\n"
            "أرسل /start لعرض الأزرار.")
    except Exception:
        pass
    yield
    log.info("إيقاف النظام...")
    try:
        if ctx.scheduler:
            ctx.scheduler.shutdown(wait=False)
        await stop_bot(ctx.tg_app, ctx.webhook_mode)
        await ctx.market.close()
        await ctx.cache.close()
    except Exception as e:
        log.warning("خطأ أثناء الإيقاف: %s", e)


app = FastAPI(title="نظام التداول الورقي", lifespan=lifespan)


# ---------------- صفحات ونبض ----------------

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    last = await ctx.cache.get("cycle:last") or await ctx.db.get_last_cycle() or {}
    opens = await ctx.db.get_open_trades()
    st = await ctx.db.get_state("realized_pnl", {"total": 0}) or {"total": 0}
    try:
        realized = float(st.get("total", 0))
    except (TypeError, ValueError):
        realized = 0.0
    open_pnl = sum((t.get("unrealized_pnl") or 0) for t in opens)
    equity = settings.START_BALANCE + realized + open_pnl
    status = (last.get("status", "—") if last else "لم تعمل دورة بعد")
    rows = "".join(
        f"<tr><td>{t['symbol']}</td><td>{t['side']}</td><td>{t['entry_price']}</td>"
        f"<td>{t.get('current_price', '')}</td><td>{t.get('unrealized_pnl', 0)}</td></tr>"
        for t in opens) or '<tr><td colspan="5">لا توجد صفقات مفتوحة</td></tr>'
    return f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>نظام التداول الورقي</title>
<style>body{{font-family:Tahoma,Arial;background:#0f172a;color:#e2e8f0;margin:0;padding:24px}}
.card{{background:#1e293b;border-radius:12px;padding:16px;margin-bottom:16px}}
h1{{font-size:22px}}table{{width:100%;border-collapse:collapse}}td,th{{border:1px solid #334155;padding:8px;text-align:center}}
a{{color:#38bdf8}}</style></head><body>
<h1>🤖 نظام التداول الورقي - يعمل ✅</h1>
<div class="card">💼 المحفظة: <b>{equity:,.2f}$</b> | المحقق: {realized:,.2f}$ |
العائم: {open_pnl:,.2f}$ | المفتوحة: {len(opens)} | آخر دورة: {status}</div>
<div class="card"><h3>الصفقات المفتوحة</h3>
<table><tr><th>الرمز</th><th>الاتجاه</th><th>الدخول</th><th>الحالي</th><th>العائم</th></tr>{rows}</table></div>
<div class="card">🔗
<a href="/health">health</a> | <a href="/prices/live">الأسعار</a> |
<a href="/trades/open">المفتوحة</a> | <a href="/trades/closed">المغلقة</a> |
<a href="/performance">الأداء</a> | <a href="/signals">الإشارات</a> | <a href="/cycle/last">آخر دورة</a></div>
</body></html>"""


@app.get("/health")
async def health():
    return {"status": "ok", "version": APP_VERSION, "db": ctx.db.mode, "cache": ctx.cache.mode,
            "telegram": bool(ctx.tg_app), "symbols": len(settings.SYMBOLS)}


# ---------------- واجهات البيانات ----------------

@app.get("/prices/live")
async def live_prices():
    cached = await ctx.cache.get("prices:live")
    if cached:
        return cached
    prices, src, err = await ctx.market.fetch_all_prices(ctx.cfg.SYMBOLS)
    return {"source": src, "error": err, "prices": prices}


@app.get("/trades/open")
async def open_trades():
    return {"trades": await ctx.db.get_open_trades()}


@app.get("/trades/closed")
async def closed_trades(limit: int = 50):
    return {"trades": await ctx.db.list_closed_trades(limit=min(limit, 200))}


@app.get("/signals")
async def signals(limit: int = 20):
    return {"signals": await ctx.db.list_signals(limit=min(limit, 100))}


@app.get("/performance")
async def performance():
    opens = await ctx.db.get_open_trades()
    closed = await ctx.db.list_closed_trades(limit=2000)
    st = await ctx.db.get_state("realized_pnl", {"total": 0}) or {"total": 0}
    try:
        realized = float(st.get("total", 0))
    except (TypeError, ValueError):
        realized = 0.0
    open_pnl = sum((t.get("unrealized_pnl") or 0) for t in opens)
    return pe.compute_performance(opens, closed, realized, settings.START_BALANCE, open_pnl)


@app.get("/cycle/last")
async def last_cycle():
    s = await ctx.cache.get("cycle:last") or await ctx.db.get_last_cycle()
    return s or {"status": "none", "message": "لم تعمل أي دورة بعد"}


@app.post("/cycle/run")
async def manual_cycle(secret: str = ""):
    if settings.WEBHOOK_SECRET and secret != settings.WEBHOOK_SECRET:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await run_cycle(ctx)


# ---------------- ويبهوك التلجرام ----------------

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    if settings.WEBHOOK_SECRET:
        got = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if got != settings.WEBHOOK_SECRET:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not ctx.tg_app:
        return {"ok": False, "error": "bot disabled"}
    try:
        from telegram import Update
        data = await request.json()
        update = Update.de_json(data, ctx.tg_app.bot)
        await ctx.tg_app.process_update(update)
        return {"ok": True}
    except Exception as e:
        log.exception("خطأ الويبهوك: %s", e)
        return {"ok": False}
