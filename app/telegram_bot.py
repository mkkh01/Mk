# -*- coding: utf-8 -*-
"""بوت التلجرام: الأزرار الخمسة + الإشعارات التلقائية.

يعمل بوضع Webhook على Render (WEBHOOK_BASE_URL)
أو Polling محلياً إذا لم يُضبط الرابط.
"""
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from . import format as fmt
from . import paper_engine as pe

log = logging.getLogger("telegram")


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 الصفقات المفتوحة", callback_data="open"),
         InlineKeyboardButton("📁 الصفقات المغلقة", callback_data="closed:0")],
        [InlineKeyboardButton("📈 أداء النظام", callback_data="perf"),
         InlineKeyboardButton("📡 الأسعار الحية", callback_data="prices")],
        [InlineKeyboardButton("🔄 ملخص الدورة", callback_data="cycle")],
    ])


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="menu")]])


def open_trades_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث الأسعار", callback_data="open")],
        [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="menu")],
    ])


def closed_nav(page: int, has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    row = []
    if has_prev:
        row.append(InlineKeyboardButton("◀️ السابق", callback_data=f"closed:{page - 1}"))
    if has_next:
        row.append(InlineKeyboardButton("التالي ▶️", callback_data=f"closed:{page + 1}"))
    rows = [row] if row else []
    rows.append([InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def _ctx(context: ContextTypes.DEFAULT_TYPE):
    return context.bot_data["ctx"]


def _allowed(ctx, chat_id) -> bool:
    ids = ctx.cfg.TELEGRAM_CHAT_IDS
    return (not ids) or (str(chat_id) in [str(x) for x in ids])


async def _perf_snapshot(ctx) -> dict:
    cfg, db = ctx.cfg, ctx.db
    opens = await db.get_open_trades()
    st = await db.get_state("realized_pnl", {"total": 0}) or {"total": 0}
    try:
        realized = float(st.get("total", 0))
    except (TypeError, ValueError):
        realized = 0.0
    open_pnl = sum((t.get("unrealized_pnl") or 0) for t in opens)
    closed = await db.list_closed_trades(limit=2000)
    return pe.compute_performance(opens, closed, realized, cfg.START_BALANCE, open_pnl)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ctx = _ctx(context)
    chat_id = update.effective_chat.id
    if not _allowed(ctx, chat_id):
        await update.message.reply_text("⛔ غير مصرح لك باستخدام هذا البوت.")
        return
    await update.message.reply_text(
        "🤖 <b>مرحباً بك في نظام التداول الورقي</b>\n"
        f"📊 {len(ctx.cfg.SYMBOLS)} زوجاً | فريم {ctx.cfg.TREND_TF}/{ctx.cfg.ENTRY_TF} | دورة كل دقيقة\n"
        "اختر من الأزرار:",
        parse_mode=ParseMode.HTML, reply_markup=main_keyboard())


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    ctx = _ctx(context)
    await query.answer()
    if not _allowed(ctx, query.message.chat_id):
        await query.edit_message_text("⛔ غير مصرح.")
        return
    data = query.data or ""
    try:
        if data == "menu":
            await query.edit_message_text("اختر من الأزرار:", parse_mode=ParseMode.HTML,
                                          reply_markup=main_keyboard())
        elif data == "open":
            opens = await ctx.db.get_open_trades()
            try:
                live: dict = {}
                feed = getattr(ctx, "ws", None)
                if feed and getattr(feed, "prices", None):
                    live.update(feed.prices)
                cached = await ctx.cache.get("prices:live") or {}
                for sym, q in (cached.get("prices") or {}).items():
                    live.setdefault(sym, q)
                for t in opens:
                    px = (live.get(t["symbol"]) or {}).get("price")
                    if px:
                        t["current_price"] = px
                        t["unrealized_pnl"] = round(
                            pe.unrealized(t, float(px), ctx.cfg.FEE_PCT), 4)
            except Exception:
                pass
            perf = await _perf_snapshot(ctx)
            try:
                live_pnl = round(sum(float(t.get("unrealized_pnl") or 0) for t in opens), 2)
                perf["open_pnl"] = live_pnl
                perf["equity"] = round(float(perf.get("balance", 0)) + live_pnl, 2)
                sb = ctx.cfg.START_BALANCE
                perf["return_pct"] = round((perf["equity"] - sb) / sb * 100, 2)
            except Exception:
                pass
            await query.edit_message_text(fmt.format_open_trades(opens, perf)[:4000],
                                          parse_mode=ParseMode.HTML,
                                          reply_markup=open_trades_keyboard())
        elif data.startswith("closed:"):
            try:
                page = int(data.split(":")[1])
            except (IndexError, ValueError):
                page = 0
            closed = await ctx.db.list_closed_trades(limit=100)
            text, has_prev, has_next = fmt.format_closed_trades(closed, page)
            await query.edit_message_text(text[:4000], parse_mode=ParseMode.HTML,
                                          reply_markup=closed_nav(page, has_prev, has_next))
        elif data == "perf":
            perf = await _perf_snapshot(ctx)
            await query.edit_message_text(fmt.format_performance(perf, ctx.cfg.START_BALANCE)[:4000],
                                          parse_mode=ParseMode.HTML, reply_markup=back_keyboard())
        elif data == "prices":
            cached = await ctx.cache.get("prices:live") or {}
            prices = cached.get("prices") or {}
            if not prices:
                prices, src, _ = await ctx.market.fetch_all_prices(ctx.cfg.SYMBOLS)
                cached = {"ts": "", "source": src, "prices": prices}
            text = fmt.format_prices(prices, ctx.cfg.SYMBOLS,
                                     cached.get("source", "?"), cached.get("ts", ""))
            await query.edit_message_text(text[:4000], parse_mode=ParseMode.HTML,
                                          reply_markup=back_keyboard())
        elif data == "cycle":
            s = await ctx.cache.get("cycle:last")
            if not s:
                s = await ctx.db.get_last_cycle()
            await query.edit_message_text(fmt.format_cycle_summary(s)[:4000],
                                          parse_mode=ParseMode.HTML, reply_markup=back_keyboard())
    except Exception as e:
        log.exception("خطأ زر التلجرام: %s", e)
        try:
            await query.edit_message_text("⚠️ حدث خطأ، حاول مرة أخرى.", reply_markup=back_keyboard())
        except Exception:
            pass


class TelegramNotifier:
    """إرسال الإشعارات التلقائية لقائمة الشاتات."""

    def __init__(self, app: Application | None, chat_ids: list):
        self._app = app
        self._chat_ids = [str(x) for x in chat_ids]

    async def send(self, text: str):
        if not self._app or not self._chat_ids:
            log.info("إشعار (بدون تلجرام): %s", text[:120].replace("\n", " "))
            return
        for cid in self._chat_ids:
            try:
                await self._app.bot.send_message(chat_id=cid, text=text[:4000],
                                                 parse_mode=ParseMode.HTML,
                                                 disable_web_page_preview=True)
            except Exception as e:
                log.warning("فشل الإرسال لـ %s: %s", cid, e)


async def create_bot(ctx) -> tuple[Application | None, bool]:
    """ينشئ البوت ويشغله. يرجع (التطبيق, هل وضع ويبهوك)."""
    token = ctx.cfg.TELEGRAM_BOT_TOKEN
    if not token:
        log.warning("TELEGRAM_BOT_TOKEN غير مضبوط - البوت معطل")
        return None, False
    app = Application.builder().token(token).build()
    app.bot_data["ctx"] = ctx
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_button))
    await app.initialize()
    await app.start()
    base = ctx.cfg.WEBHOOK_BASE_URL
    if base:
        url = f"{base}/telegram/webhook"
        try:
            await app.bot.set_webhook(url, secret_token=ctx.cfg.WEBHOOK_SECRET or None,
                                      allowed_updates=["message", "callback_query"])
            log.info("وضع Webhook: %s", url)
        except Exception as e:
            log.error("فشل ضبط الويبهوك: %s", e)
        return app, True
    else:
        await app.updater.start_polling(allowed_updates=["message", "callback_query"])
        log.info("وضع Polling (تجربة محلية)")
        return app, False


async def stop_bot(app: Application | None, webhook_mode: bool):
    if not app:
        return
    try:
        if not webhook_mode and app.updater and app.updater.running:
            await app.updater.stop()
        await app.stop()
        await app.shutdown()
    except Exception as e:
        log.warning("خطأ إيقاف البوت: %s", e)
