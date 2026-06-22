"""
Telegram Handlers — all command and message handlers.
NO business logic. Only routes to services and formats output.

Every handler logs: entry, exit, state transition, errors.
"""
import logging
import traceback
import json
import os
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from database.repositories import (
    UserRepository, CoinRepository, get_session
)
from database.models import Coin
from config.constants import ADMIN_ID

logger = logging.getLogger("telegram.handlers")

# ── Conversation States ─────────────────────────────────────
ADD_SYMBOL, ADD_CAPITAL, ADD_RISK, ADD_TF = range(4)


class Handlers:
    """All Telegram command/message handlers. Pure UI layer."""

    def __init__(self, admin_id: int = ADMIN_ID,
                 analysis_service=None, trading_service=None,
                 portfolio_service=None, risk_service=None):
        self.admin_id = admin_id
        self.analysis_service = analysis_service
        self.trading_service = trading_service
        self.portfolio_service = portfolio_service
        self.risk_service = risk_service
        logger.info(f"[HANDLERS] Initialized (admin={admin_id})")

    async def _is_admin(self, update: Update) -> bool:
        uid = update.effective_user.id if update.effective_user else 0
        is_admin = uid == self.admin_id
        if not is_admin:
            logger.warning(f"[AUTH] Unauthorized access attempt from user {uid}")
        return is_admin

    def _log_conversation(self, step: str, update: Update, context: ContextTypes.DEFAULT_TYPE,
                          extra: str = ""):
        """Log conversation state with user/chat context."""
        uid = update.effective_user.id if update.effective_user else "?"
        state = context.user_data.get("__state__", "NONE")
        ud = dict(context.user_data)
        ud.pop("__state__", None)
        logger.info(
            f"[CONV] [{step}] user={uid} state={state} "
            f"data={json.dumps(ud, default=str, ensure_ascii=False)} {extra}"
        )

    # ── Start / Cancel ──────────────────────────────────────

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[START] /start from user={update.effective_user.id}")
        if not await self._is_admin(update):
            return
        user_id = str(update.effective_user.id)
        try:
            async for session in get_session():
                user = await UserRepository.get_or_create(session, int(user_id))
                logger.info(f"[START] User record ensured: {user.telegram_id}")
        except Exception as e:
            logger.error(f"[START] DB error: {e}", exc_info=True)
            await update.message.reply_text("⚠️ خطأ في الاتصال بقاعدة البيانات. حاول لاحقاً.")
            return

        from bots.telegram.keyboards import get_main_menu
        await update.message.reply_text(
            "👋 أهلاً بك في نظام التداول المؤسسي CT V4.0\n"
            "تم تصميم هذا النظام لحماية رأس مالك وتحقيق نمو مستقر.\n\n"
            "⚙️ *المعمارية:* 14 محرك مستقل | Clean Architecture | Event-Driven",
            reply_markup=get_main_menu(),
            parse_mode="Markdown",
        )
        logger.info("[START] Main menu sent.")

    async def cancel_conversation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel the current conversation."""
        logger.info(f"[CONV] /cancel from user={update.effective_user.id}")
        context.user_data.clear()
        from bots.telegram.keyboards import get_main_menu
        await update.message.reply_text(
            "❌ تم إلغاء العملية.",
            reply_markup=get_main_menu(),
        )
        return ConversationHandler.END

    # ── Main Menu Router ────────────────────────────────────

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Global message handler — for messages outside any conversation."""
        if not await self._is_admin(update):
            return

        text = update.message.text
        uid = update.effective_user.id
        action = context.user_data.get("action")
        logger.info(f"[MSG] user={uid} text={repr(text)} action={action}")

        if action:
            await self._process_action(update, context)
            return

        # NOTE: "➕ إضافة عملة" is NOT in this dict.
        # It is handled EXCLUSIVELY by the ConversationHandler entry point.
        routes = {
            "📈 الأسعار المباشرة": self.cmd_live_prices,
            "➖ حذف عملة": self.cmd_delete_coin,
            "⚙️ تعديل العملة": self.cmd_edit_coin,
            "💰 إدارة رأس المال": self.cmd_capital_mgmt,
            "📊 الإحصائيات": self.cmd_stats,
            "📋 سجل الصفقات": self.cmd_trade_history,
            "🛑 إيقاف الطوارئ": self.cmd_emergency_stop,
            "▶️ تشغيل التداول": self.cmd_start_trading,
            "⏸ إيقاف التداول": self.cmd_stop_trading,
            "🧠 تقرير الذكاء الاصطناعي": self.cmd_ai_report,
            "🎯 تقرير الأداء": self.cmd_performance,
            "📡 حالة النظام": self.cmd_status,
        }

        handler = routes.get(text)
        if handler:
            logger.info(f"[MSG] Routed to: {handler.__name__}")
            try:
                await handler(update, context)
            except Exception as e:
                logger.error(f"[MSG] Handler error ({handler.__name__}): {e}", exc_info=True)
                await update.message.reply_text("⚠️ حدث خطأ. حاول مرة أخرى.")
        else:
            logger.debug(f"[MSG] No route for: {repr(text)}")

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Global CallbackQueryHandler — for NON-conversation callbacks only."""
        query = update.callback_query
        data = query.data
        uid = query.from_user.id
        logger.info(f"[CALLBACK] user={uid} data={repr(data)}")

        await query.answer()

        # Do NOT handle tf_* callbacks here — ConversationHandler handles those
        if data.startswith("tf_"):
            logger.debug(f"[CALLBACK] Skipping tf_* in global handler (belongs to conversation)")
            return

        try:
            if data == "main_menu":
                from bots.telegram.keyboards import get_main_menu
                await query.edit_message_text("🏠 القائمة الرئيسية", reply_markup=get_main_menu())
                logger.info("[CALLBACK] Main menu shown.")
            elif data == "edit_base_capital":
                await query.edit_message_text("💵 أرسل رأس المال الأساسي الجديد:")
                context.user_data["action"] = "edit_base_capital"
                logger.info("[CALLBACK] Edit base capital initiated.")
            elif data.startswith("set_risk_"):
                risk_val = float(data.replace("set_risk_", ""))
                async for session in get_session():
                    user = await UserRepository.get_by_telegram_id(session, self.admin_id)
                    if user:
                        user.risk_per_trade = risk_val
                        await session.commit()
                        logger.info(f"[CALLBACK] Risk set to {risk_val}%")
                await query.edit_message_text(f"✅ تم تعيين نسبة المخاطرة إلى {risk_val}%")
            else:
                logger.debug(f"[CALLBACK] Unhandled data: {repr(data)}")
        except Exception as e:
            logger.error(f"[CALLBACK] Error: {e}", exc_info=True)
            await query.edit_message_text("⚠️ حدث خطأ.")

    # ── Conversation Handlers (Add Coin Flow) ───────────────

    async def start_add_coin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Entry point: user clicked '➕ إضافة عملة'. Enters ADD_SYMBOL state."""
        uid = update.effective_user.id if update.effective_user else "?"
        logger.info(f"[CONV] ENTRY: start_add_coin user={uid}")

        if not await self._is_admin(update):
            logger.warning(f"[CONV] ENTRY BLOCKED: non-admin user={uid}")
            return ConversationHandler.END

        context.user_data["__state__"] = "ADD_SYMBOL"
        await update.message.reply_text("✍️ أرسل رمز العملة (مثال: BTCUSDT):")
        logger.info(f"[CONV] → STATE: ADD_SYMBOL user={uid}")
        return ADD_SYMBOL

    async def process_add_symbol(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """State ADD_SYMBOL → ADD_CAPITAL. Stores symbol."""
        uid = update.effective_user.id
        symbol = update.message.text.strip().upper()
        context.user_data["new_coin_symbol"] = symbol
        self._log_conversation("process_add_symbol", update, context, f"symbol={symbol}")

        await update.message.reply_text("💰 أدخل رأس المال المخصص:")
        context.user_data["__state__"] = "ADD_CAPITAL"
        logger.info(f"[CONV] → STATE: ADD_CAPITAL user={uid}")
        return ADD_CAPITAL

    async def process_add_capital(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """State ADD_CAPITAL → ADD_RISK. Stores capital."""
        uid = update.effective_user.id
        try:
            capital = float(update.message.text)
            context.user_data["new_coin_capital"] = capital
            self._log_conversation("process_add_capital", update, context, f"capital={capital}")

            await update.message.reply_text("⚠️ أدخل نسبة المخاطرة (مثال: 1):")
            context.user_data["__state__"] = "ADD_RISK"
            logger.info(f"[CONV] → STATE: ADD_RISK user={uid}")
            return ADD_RISK
        except ValueError:
            logger.warning(f"[CONV] Invalid capital value: {repr(update.message.text)}")
            await update.message.reply_text("❌ خطأ: يرجى إدخال قيمة عددية صحيحة (مثال: 100).")
            return ADD_CAPITAL

    async def process_add_risk(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """State ADD_RISK → ADD_TF. Stores risk %, sends timeframe keyboard."""
        uid = update.effective_user.id
        try:
            risk = float(update.message.text)
            context.user_data["new_coin_risk"] = risk
            self._log_conversation("process_add_risk", update, context, f"risk={risk}%")

            from bots.telegram.keyboards import get_timeframe_menu
            await update.message.reply_text(
                "⏱ اختر الإطار الزمني:",
                reply_markup=get_timeframe_menu(),
            )
            context.user_data["__state__"] = "ADD_TF"
            logger.info(f"[CONV] → STATE: ADD_TF user={uid} (timeframe keyboard sent)")
            return ADD_TF
        except ValueError:
            logger.warning(f"[CONV] Invalid risk value: {repr(update.message.text)}")
            await update.message.reply_text("❌ خطأ: يرجى إدخال قيمة عددية صحيحة (مثال: 1).")
            return ADD_RISK
        except Exception as e:
            logger.error(f"[CONV] Unexpected error in process_add_risk: {e}", exc_info=True)
            await update.message.reply_text("⚠️ حدث خطأ. أعد المحاولة.")
            return ADD_RISK

    async def process_add_tf(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """State ADD_TF → END. Saves coin via CoinRepository (handles UUID resolution)."""
        query = update.callback_query
        await query.answer()
        data = query.data
        uid = query.from_user.id  # Telegram ID (int)

        tf = data.replace("tf_", "")
        symbol = context.user_data.get("new_coin_symbol", "UNKNOWN")
        capital = context.user_data.get("new_coin_capital", 0)
        risk = context.user_data.get("new_coin_risk", 0)

        logger.info(
            f"[CONV] process_add_tf user={uid} data={repr(data)} "
            f"symbol={symbol} capital={capital} risk={risk} tf={tf}"
        )

        try:
            async for session in get_session():
                # resolve_user_uuid creates the user automatically if needed
                user_uuid = await UserRepository.resolve_user_uuid(session, uid)
                logger.info(f"[CONV] User UUID resolved: telegram_id={uid} → {user_uuid[:8]}...")

                # Use CoinRepository.add() — handles UUID resolution and duplicates
                coin = await CoinRepository.add(
                    session, uid,  # uid is telegram_id (int) — resolved internally
                    symbol=symbol,
                    capital_allocated=capital,
                    risk_per_trade=risk,
                    timeframe=tf,
                )
                logger.info(f"[CONV] ✅ Coin saved to DB: {symbol} tf={tf} capital={capital} coin_id={coin.id[:8]}...")

        except Exception as e:
            logger.critical(
                f"[CONV] ❌ DB save failed for {symbol}: {e}",
                exc_info=True,
            )
            await query.edit_message_text(
                f"❌ فشل حفظ {symbol} في قاعدة البيانات.\n"
                f"الخطأ: {e}\nحاول مرة أخرى."
            )
            context.user_data.clear()
            return ConversationHandler.END

        await query.edit_message_text(
            f"✅ تمت إضافة {symbol} بنجاح!\n"
            f"💰 رأس المال: {capital}\n"
            f"⚠️ المخاطرة: {risk}%\n"
            f"⏱ الإطار الزمني: {tf}",
        )
        logger.info(f"[CONV] ✅ END: {symbol} added successfully.")
        context.user_data.clear()
        return ConversationHandler.END

    # ── Command Handlers ────────────────────────────────────

    async def cmd_live_prices(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[CMD] live_prices user={update.effective_user.id}")
        cache = "/tmp/live_prices.json"
        if not os.path.exists(cache):
            await update.message.reply_text("⏳ جاري الاتصال بالرادار... حاول بعد لحظات.")
            return
        try:
            with open(cache, "r") as f:
                prices = json.load(f)
        except Exception:
            await update.message.reply_text("❌ خطأ في قراءة بيانات الأسعار.")
            return
        if not prices:
            await update.message.reply_text("❌ لا توجد عملات مضافة.")
            return
        msg = "📈 *الأسعار المباشرة*\n━━━━━━━━━━━━━━\n"
        for s, d in list(prices.items())[:15]:
            msg += f"🪙 {s}: `{d['price']}`\n"
        await update.message.reply_text(msg, parse_mode="Markdown")
        logger.info(f"[CMD] live_prices: {len(prices)} symbols shown.")

    async def cmd_delete_coin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[CMD] delete_coin user={update.effective_user.id}")
        user_id = str(update.effective_user.id)
        try:
            async for session in get_session():
                coins = await CoinRepository.get_all(session, user_id)
                if not coins:
                    await update.message.reply_text("❌ لا توجد عملات.")
                    return
                msg = "➖ أرسل رمز العملة لحذفها:\n"
                for c in coins:
                    msg += f"- `{c.symbol}`\n"
                await update.message.reply_text(msg, parse_mode="Markdown")
                context.user_data["action"] = "delete_coin"
                logger.info(f"[CMD] delete_coin: {len(coins)} coins listed.")
        except Exception as e:
            logger.error(f"[CMD] delete_coin error: {e}", exc_info=True)
            await update.message.reply_text("⚠️ خطأ في جلب قائمة العملات.")

    async def cmd_edit_coin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[CMD] edit_coin user={update.effective_user.id}")
        user_id = str(update.effective_user.id)
        try:
            async for session in get_session():
                coins = await CoinRepository.get_all(session, user_id)
                if not coins:
                    await update.message.reply_text("❌ لا توجد عملات.")
                    return
                msg = "⚙️ أرسل رمز العملة للتعديل:\n"
                for c in coins:
                    msg += f"- `{c.symbol}` (رأس مال: {c.capital_allocated}, إطار: {c.timeframe})\n"
                await update.message.reply_text(msg, parse_mode="Markdown")
                context.user_data["action"] = "edit_coin_start"
                logger.info(f"[CMD] edit_coin: {len(coins)} coins listed.")
        except Exception as e:
            logger.error(f"[CMD] edit_coin error: {e}", exc_info=True)
            await update.message.reply_text("⚠️ خطأ في جلب قائمة العملات.")

    async def cmd_capital_mgmt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[CMD] capital_mgmt user={update.effective_user.id}")
        try:
            async for session in get_session():
                user = await UserRepository.get_by_telegram_id(session, int(update.effective_user.id))
                if user:
                    msg = (
                        f"💰 *إدارة رأس المال*\n\n"
                        f"رأس المال الكلي: `{user.total_capital}` USDT\n"
                        f"نسبة المخاطرة: {user.risk_per_trade}%\n"
                        f"أقصى سحب: {user.max_drawdown_limit}%"
                    )
                    from bots.telegram.keyboards import get_capital_management_menu
                    await update.message.reply_text(
                        msg, reply_markup=get_capital_management_menu(), parse_mode="Markdown"
                    )
                    return
            await update.message.reply_text("❌ لم يتم العثور على بيانات المستخدم.")
        except Exception as e:
            logger.error(f"[CMD] capital_mgmt error: {e}", exc_info=True)
            await update.message.reply_text("⚠️ خطأ في جلب بيانات رأس المال.")

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[CMD] stats user={update.effective_user.id}")
        user_id = str(update.effective_user.id)
        if self.portfolio_service:
            try:
                report = await self.portfolio_service.get_performance_report(user_id)
                await update.message.reply_text(report, parse_mode="Markdown")
                return
            except Exception as e:
                logger.error(f"[CMD] stats via portfolio_service: {e}")
        # Fallback
        try:
            from database.repositories import TradeRepository
            async for session in get_session():
                trades = await TradeRepository.get_all_closed(session, user_id)
                if not trades:
                    await update.message.reply_text("❌ لا توجد صفقات مغلقة.")
                    return
                total = len(trades)
                wins = [t for t in trades if t.status == "WON"]
                total_pnl = sum(t.pnl for t in trades)
                msg = (
                    f"📊 *إحصائيات الأداء*\n━━━━━━━━━━━━━━\n"
                    f"📈 الإجمالي: {total}\n"
                    f"✅ نسبة النجاح: {(len(wins)/total)*100:.1f}%\n"
                    f"💰 صافي الربح: `{total_pnl:.2f} USDT`"
                )
                await update.message.reply_text(msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"[CMD] stats error: {e}", exc_info=True)
            await update.message.reply_text("⚠️ خطأ في جلب الإحصائيات.")

    async def cmd_trade_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[CMD] trade_history user={update.effective_user.id}")
        user_id = str(update.effective_user.id)
        if self.portfolio_service:
            try:
                report = await self.portfolio_service.get_trade_report(user_id)
                await update.message.reply_text(report, parse_mode="Markdown")
                return
            except Exception as e:
                logger.error(f"[CMD] trade_history via service: {e}")
        try:
            from database.repositories import TradeRepository
            async for session in get_session():
                trades = await TradeRepository.get_closed_trades(session, user_id, 10)
                if not trades:
                    await update.message.reply_text("❌ السجل فارغ.")
                    return
                msg = "📋 *آخر الصفقات*\n━━━━━━━━━━━━━━\n"
                for t in trades:
                    icon = "✅" if t.status == "WON" else "❌"
                    msg += f"{icon} {t.symbol} | `{t.pnl:.2f}`\n"
                await update.message.reply_text(msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"[CMD] trade_history error: {e}", exc_info=True)
            await update.message.reply_text("⚠️ خطأ في جلب سجل الصفقات.")

    async def cmd_performance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[CMD] performance user={update.effective_user.id}")
        user_id = str(update.effective_user.id)
        if self.portfolio_service:
            try:
                report = await self.portfolio_service.get_performance_report(user_id)
                await update.message.reply_text(report, parse_mode="Markdown")
                return
            except Exception as e:
                logger.error(f"[CMD] performance error: {e}")
        await update.message.reply_text("📊 استخدم 📊 الإحصائيات للحصول على التفاصيل.")

    async def cmd_ai_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[CMD] ai_report user={update.effective_user.id}")
        if self.portfolio_service:
            try:
                status = await self.portfolio_service.get_full_status(str(update.effective_user.id))
                recs = status.get("recommendations", [])
                strat = status.get("strategy_performance", {})
                msg = "🧠 *تقرير النظام*\n━━━━━━━━━━━━━━\n"
                if recs:
                    msg += "\n💡 *توصيات:*\n"
                    for r in recs[:5]:
                        msg += f"• {r}\n"
                if strat:
                    msg += "\n📊 *أداء الاستراتيجيات:*\n"
                    for name, perf in list(strat.items())[:5]:
                        msg += f"• {name}: {perf.get('win_rate', 0)}% WR | {perf.get('total_trades', 0)} trades\n"
                if not recs and not strat:
                    msg += "\nلا توجد بيانات كافية بعد."
                await update.message.reply_text(msg, parse_mode="Markdown")
                return
            except Exception as e:
                logger.error(f"[CMD] ai_report error: {e}", exc_info=True)
        await update.message.reply_text("🧠 لا توجد بيانات تعلم كافية بعد.")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[CMD] status user={update.effective_user.id}")
        if self.portfolio_service:
            try:
                status = await self.portfolio_service.get_full_status(str(update.effective_user.id))
                portfolio = status.get("portfolio", {})
                health = status.get("health", {})
                msg = (
                    f"📡 *حالة النظام*\n━━━━━━━━━━━━━━\n"
                    f"💼 المحفظة: `{portfolio.get('balance', 0):.2f} USDT`\n"
                    f"📊 Equity: `{portfolio.get('equity', 0):.2f}`\n"
                    f"🔓 مفتوحة: {portfolio.get('open_positions', 0)}\n"
                    f"✅ نسبة الربح: {portfolio.get('win_rate', 0):.1f}%\n"
                    f"📉 السحب: {portfolio.get('drawdown', 0):.2f}%\n"
                    f"⚙️ الحالة: {health.get('system_state', 'UNKNOWN')}"
                )
                await update.message.reply_text(msg, parse_mode="Markdown")
                return
            except Exception as e:
                logger.error(f"[CMD] status error: {e}", exc_info=True)
        await update.message.reply_text("📡 النظام يعمل. استخدم /start للمزيد.")

    async def cmd_emergency_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.warning(f"[CMD] EMERGENCY STOP user={update.effective_user.id}")
        if self.risk_service:
            self.risk_service.emergency_stop("Manual from Telegram")
        try:
            user_id = str(update.effective_user.id)
            async for session in get_session():
                user = await UserRepository.get_by_telegram_id(session, int(user_id))
                if user:
                    await UserRepository.update_status(session, user, False, True)
        except Exception as e:
            logger.error(f"[CMD] emergency_stop DB error: {e}")
        await update.message.reply_text("🛑 *EMERGENCY STOP ACTIVATED!*", parse_mode="Markdown")

    async def cmd_start_trading(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[CMD] START TRADING user={update.effective_user.id}")
        if self.risk_service:
            self.risk_service.resume_trading()
        try:
            user_id = str(update.effective_user.id)
            async for session in get_session():
                user = await UserRepository.get_by_telegram_id(session, int(user_id))
                if user:
                    await UserRepository.update_status(session, user, True)
        except Exception as e:
            logger.error(f"[CMD] start_trading DB error: {e}")
        await update.message.reply_text("▶️ نظام التداول يعمل الآن.")

    async def cmd_stop_trading(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[CMD] STOP TRADING user={update.effective_user.id}")
        if self.risk_service:
            self.risk_service.emergency_stop("Manual stop")
        try:
            user_id = str(update.effective_user.id)
            async for session in get_session():
                user = await UserRepository.get_by_telegram_id(session, int(user_id))
                if user:
                    await UserRepository.update_status(session, user, False)
        except Exception as e:
            logger.error(f"[CMD] stop_trading DB error: {e}")
        await update.message.reply_text("⏸ نظام التداول متوقف.")

    # ── Action Processing (edit/delete flows) ───────────────

    async def _process_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        action = context.user_data.get("action")
        text = update.message.text.strip().upper()
        uid = update.effective_user.id
        logger.info(f"[ACTION] user={uid} action={action} text={repr(text)}")

        try:
            if action == "delete_coin":
                user_id = str(uid)
                async for session in get_session():
                    await CoinRepository.delete_by_symbol(session, user_id, text)
                await update.message.reply_text(f"✅ تم حذف {text}.")
                context.user_data.pop("action")
                logger.info(f"[ACTION] Coin deleted: {text}")

            elif action == "edit_coin_start":
                context.user_data["edit_target"] = text
                await update.message.reply_text(f"💰 أدخل رأس المال الجديد لـ {text}:")
                context.user_data["action"] = "edit_coin_capital"
                logger.info(f"[ACTION] Edit coin started: {text}")

            elif action == "edit_coin_capital":
                cap = float(text)
                symbol = context.user_data["edit_target"]
                user_id = str(uid)
                async for session in get_session():
                    coin = await CoinRepository.get_by_symbol(session, user_id, symbol)
                    if coin:
                        await CoinRepository.update(session, coin, capital_allocated=cap)
                await update.message.reply_text(f"✅ تم تحديث رأس مال {symbol} إلى {cap}.")
                context.user_data.clear()
                logger.info(f"[ACTION] Coin capital updated: {symbol} → {cap}")

            elif action == "edit_base_capital":
                cap = float(text)
                user_id = str(uid)
                async for session in get_session():
                    user = await UserRepository.get_by_telegram_id(session, int(user_id))
                    if user:
                        user.total_capital = cap
                        await session.commit()
                await update.message.reply_text(f"✅ تم تحديث رأس المال الأساسي إلى {cap}.")
                context.user_data.clear()
                logger.info(f"[ACTION] Base capital updated to {cap}")

            else:
                logger.warning(f"[ACTION] Unknown action: {action}")
                await update.message.reply_text("⚠️ أمر غير معروف.")
                context.user_data.clear()
        except ValueError:
            await update.message.reply_text("❌ أدخل قيمة عددية صحيحة.")
        except Exception as e:
            logger.error(f"[ACTION] Error in {action}: {e}", exc_info=True)
            await update.message.reply_text("⚠️ حدث خطأ. حاول مرة أخرى.")
