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
ADD_SYMBOL, ADD_CAPITAL, ADD_RISK, ADD_TIMEFRAMES = range(4)


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
        logger.info(f"[معالجات] تم التهيئة (admin={admin_id})")

    async def _is_admin(self, update: Update) -> bool:
        uid = update.effective_user.id if update.effective_user else 0
        is_admin = uid == self.admin_id
        if not is_admin:
            logger.warning(f"[صلاحية] محاولة وصول غير مصرح من المستخدم {uid}")
        return is_admin

    def _log_conversation(self, step: str, update: Update, context: ContextTypes.DEFAULT_TYPE,
                          extra: str = ""):
        """Log conversation state with user/chat context."""
        uid = update.effective_user.id if update.effective_user else "?"
        state = context.user_data.get("__state__", "NONE")
        ud = dict(context.user_data)
        ud.pop("__state__", None)
        logger.info(
            f"[محادثة] [{step}] user={uid} state={state} "
            f"data={json.dumps(ud, default=str, ensure_ascii=False)} {extra}"
        )

    # ── Start / Cancel ──────────────────────────────────────

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[بدء] /start من المستخدم={update.effective_user.id}")
        if not await self._is_admin(update):
            return
        user_id = str(update.effective_user.id)
        try:
            async for session in get_session():
                user = await UserRepository.get_or_create(session, int(user_id))
                logger.info(f"[بدء] تم التأكد من سجل المستخدم: {user.telegram_id}")
        except Exception as e:
            logger.error(f"[بدء] خطأ في قاعدة البيانات: {e}", exc_info=True)
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
        logger.info("[بدء] تم إرسال القائمة الرئيسية.")

    async def cancel_conversation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel the current conversation."""
        logger.info(f"[محادثة] /cancel من المستخدم={update.effective_user.id}")
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
        logger.info(f"[رسالة] user={uid} text={repr(text)} action={action}")

        if action:
            await self._process_action(update, context)
            return

        # NOTE: "➕ إضافة عملة" is NOT in this dict.
        # It is handled EXCLUSIVELY by the ConversationHandler entry point.
        routes = {
            "📈 الأسعار المباشرة": self.cmd_live_prices,
            "➖ حذف عملة": self.cmd_delete_coin,
            "⚙️ تعديل العملة": self.cmd_edit_coin,
            "📊 الإحصائيات": self.cmd_stats,
            "📋 سجل الصفقات": self.cmd_trade_history,
            "🛑 إيقاف الطوارئ": self.cmd_emergency_stop,
            "▶️ تشغيل التداول": self.cmd_start_trading,
            "⏸ إيقاف التداول": self.cmd_stop_trading,
            "🧠 توصيات النظام": self.cmd_recommendations,
            "📡 حالة النظام": self.cmd_status,
        }

        handler = routes.get(text)
        if handler:
            logger.info(f"[رسالة] تم التوجيه إلى: {handler.__name__}")
            try:
                await handler(update, context)
            except Exception as e:
                logger.error(f"[رسالة] خطأ في المعالج ({handler.__name__}): {e}", exc_info=True)
                await update.message.reply_text("⚠️ حدث خطأ. حاول مرة أخرى.")
        else:
            logger.debug(f"[رسالة] لا مسار لـ: {repr(text)}")

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Global CallbackQueryHandler — for NON-conversation callbacks only."""
        query = update.callback_query
        data = query.data
        uid = query.from_user.id
        logger.info(f"[استدعاء] user={uid} data={repr(data)}")

        await query.answer()

        # Do NOT handle tf_* callbacks here — ConversationHandler handles those
        if data.startswith("tf_"):
            logger.debug(f"[استدعاء] تخطي tf_* في المعالج العام (يتبع المحادثة)")
            return

        try:
            if data == "main_menu":
                from bots.telegram.keyboards import get_main_menu
                await query.edit_message_text("🏠 القائمة الرئيسية", reply_markup=get_main_menu())
                logger.info("[استدعاء] تم عرض القائمة الرئيسية.")
            elif data == "edit_base_capital":
                await query.edit_message_text("💵 أرسل رأس المال الأساسي الجديد:")
                context.user_data["action"] = "edit_base_capital"
                logger.info("[استدعاء] بدء تعديل رأس المال الأساسي.")
            elif data.startswith("set_risk_"):
                risk_val = float(data.replace("set_risk_", ""))
                async for session in get_session():
                    user = await UserRepository.get_by_telegram_id(session, self.admin_id)
                    if user:
                        user.risk_per_trade = risk_val
                        await session.commit()
                        logger.info(f"[استدعاء] تم تعيين المخاطرة إلى {risk_val}%")
                await query.edit_message_text(f"✅ تم تعيين نسبة المخاطرة إلى {risk_val}%")
            else:
                logger.debug(f"[استدعاء] بيانات غير معالجة: {repr(data)}")
        except Exception as e:
            logger.error(f"[استدعاء] خطأ: {e}", exc_info=True)
            await query.edit_message_text("⚠️ حدث خطأ.")

    # ── Conversation Handlers (Add Coin Flow) ───────────────

    async def start_add_coin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Entry point: user clicked '➕ إضافة عملة'. Enters ADD_SYMBOL state."""
        uid = update.effective_user.id if update.effective_user else "?"
        logger.info(f"[محادثة] دخول: start_add_coin user={uid}")

        if not await self._is_admin(update):
            logger.warning(f"[محادثة] دخول ممنوع: مستخدم غير مصرح={uid}")
            return ConversationHandler.END

        context.user_data["__state__"] = "ADD_SYMBOL"
        await update.message.reply_text("✍️ أرسل رمز العملة (مثال: BTCUSDT):")
        logger.info(f"[محادثة] → الحالة: ADD_SYMBOL user={uid}")
        return ADD_SYMBOL

    async def process_add_symbol(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """State ADD_SYMBOL → ADD_CAPITAL. Stores symbol."""
        uid = update.effective_user.id
        symbol = update.message.text.strip().upper()
        context.user_data["new_coin_symbol"] = symbol
        self._log_conversation("process_add_symbol", update, context, f"symbol={symbol}")

        await update.message.reply_text("💰 أدخل رأس المال المخصص (USDT):")
        context.user_data["__state__"] = "ADD_CAPITAL"
        logger.info(f"[محادثة] → الحالة: ADD_CAPITAL user={uid}")
        return ADD_CAPITAL

    async def process_add_capital(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """State ADD_CAPITAL → ADD_RISK. Stores capital."""
        uid = update.effective_user.id
        try:
            capital = float(update.message.text)
            if capital <= 0:
                raise ValueError("رأس المال يجب أن يكون أكبر من صفر")
            context.user_data["new_coin_capital"] = capital
            self._log_conversation("process_add_capital", update, context, f"capital={capital}")

            await update.message.reply_text("⚠️ أدخل نسبة المخاطرة (مثال: 1.5):")
            context.user_data["__state__"] = "ADD_RISK"
            logger.info(f"[محادثة] → الحالة: ADD_RISK user={uid}")
            return ADD_RISK
        except ValueError:
            logger.warning(f"[محادثة] قيمة رأس مال غير صالحة: {repr(update.message.text)}")
            await update.message.reply_text(
                "❌ خطأ: يرجى إدخال قيمة عددية صحيحة أكبر من صفر (مثال: 100)."
            )
            return ADD_CAPITAL

    async def process_add_risk(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """State ADD_RISK → ADD_TIMEFRAMES. Stores risk %, sends multi-select timeframe keyboard."""
        uid = update.effective_user.id
        try:
            risk = float(update.message.text)
            if risk <= 0 or risk > 100:
                raise ValueError("نسبة المخاطرة يجب أن تكون بين 0 و 100")
            context.user_data["new_coin_risk"] = risk
            self._log_conversation("process_add_risk", update, context, f"risk={risk}%")

            # Initialize selected timeframes set
            context.user_data["selected_timeframes"] = set()

            from bots.telegram.keyboards import get_timeframe_menu
            await update.message.reply_text(
                "⏱ اختر الأطر الزمنية (يمكنك اختيار أكثر من واحد):",
                reply_markup=get_timeframe_menu(selected_timeframes=set()),
            )
            context.user_data["__state__"] = "ADD_TIMEFRAMES"
            logger.info(f"[محادثة] → الحالة: ADD_TIMEFRAMES user={uid} (قائمة الأطر الزمنية المتعددة)")
            return ADD_TIMEFRAMES
        except ValueError:
            logger.warning(f"[محادثة] قيمة مخاطرة غير صالحة: {repr(update.message.text)}")
            await update.message.reply_text(
                "❌ خطأ: يرجى إدخال قيمة عددية بين 0 و 100 (مثال: 1.5)."
            )
            return ADD_RISK
        except Exception as e:
            logger.error(f"[محادثة] خطأ غير متوقع في process_add_risk: {e}", exc_info=True)
            await update.message.reply_text("⚠️ حدث خطأ. أعد المحاولة.")
            return ADD_RISK

    async def process_add_tf_toggle(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Toggle a timeframe selection and rebuild the keyboard."""
        query = update.callback_query
        await query.answer()
        data = query.data
        uid = query.from_user.id

        # Parse which timeframe was toggled
        tf = data.replace("tf_toggle_", "")
        selected = context.user_data.get("selected_timeframes", set())

        if tf in selected:
            selected.discard(tf)
            logger.info(f"[محادثة] إلغاء تحديد الإطار الزمني: {tf} user={uid}")
        else:
            selected.add(tf)
            logger.info(f"[محادثة] تحديد الإطار الزمني: {tf} user={uid}")

        context.user_data["selected_timeframes"] = selected

        from bots.telegram.keyboards import get_timeframe_menu
        await query.edit_message_text(
            f"⏱ اختر الأطر الزمنية (يمكنك اختيار أكثر من واحد):\n\n"
            f"المختار حالياً: {', '.join(sorted(selected)) if selected else 'لا شيء'}",
            reply_markup=get_timeframe_menu(selected_timeframes=selected),
        )
        return ADD_TIMEFRAMES

    async def process_add_tf_done(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Save coin with all selected timeframes."""
        query = update.callback_query
        await query.answer()
        uid = query.from_user.id

        selected = context.user_data.get("selected_timeframes", set())
        symbol = context.user_data.get("new_coin_symbol", "UNKNOWN")
        capital = context.user_data.get("new_coin_capital", 0)
        risk = context.user_data.get("new_coin_risk", 0)

        if not selected:
            logger.warning(f"[محادثة] محاولة حفظ بدون أطر زمنية user={uid} symbol={symbol}")
            await query.answer("⚠️ اختر إطاراً زمنياً واحداً على الأقل!", show_alert=True)
            return ADD_TIMEFRAMES

        # Join timeframes as comma-separated string
        tfs_str = ",".join(sorted(selected))

        logger.info(
            f"[محادثة] process_add_tf_done user={uid} "
            f"symbol={symbol} capital={capital} risk={risk} tfs={tfs_str}"
        )

        try:
            async for session in get_session():
                user_uuid = await UserRepository.resolve_user_uuid(session, uid)
                logger.info(
                    f"[محادثة] تم تحديد UUID المستخدم: telegram_id={uid} → {user_uuid[:8]}..."
                )

                coin = await CoinRepository.add(
                    session, uid,
                    symbol=symbol,
                    capital_allocated=capital,
                    risk_per_trade=risk,
                    timeframes=sorted(selected),  # قائمة الأطر المختارة
                )
                logger.info(
                    f"[محادثة] ✅ تم حفظ العملة: {symbol} tfs={tfs_str} "
                    f"capital={capital} coin_id={coin.id[:8]}..."
                )

        except Exception as e:
            logger.critical(
                f"[محادثة] ❌ فشل حفظ {symbol} في قاعدة البيانات: {e}",
                exc_info=True,
            )
            await query.edit_message_text(
                f"❌ فشل حفظ {symbol} في قاعدة البيانات.\n"
                f"الخطأ: {e}\nحاول مرة أخرى."
            )
            context.user_data.clear()
            return ConversationHandler.END

        tfs_display = ", ".join(sorted(selected))
        await query.edit_message_text(
            f"✅ تمت إضافة {symbol} بنجاح!\n"
            f"💰 رأس المال: {capital} USDT\n"
            f"⚠️ المخاطرة: {risk}%\n"
            f"⏱ الأطر الزمنية: {tfs_display}",
        )
        logger.info(f"[محادثة] ✅ انتهى: تمت إضافة {symbol} بنجاح.")
        context.user_data.clear()
        return ConversationHandler.END

    # ── Command Handlers ────────────────────────────────────

    async def cmd_live_prices(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[أمر] الأسعار_المباشرة user={update.effective_user.id}")
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
        logger.info(f"[أمر] الأسعار_المباشرة: تم عرض {len(prices)} رمز.")

    async def cmd_delete_coin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[أمر] حذف_عملة user={update.effective_user.id}")
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
                logger.info(f"[أمر] حذف_عملة: عرض {len(coins)} عملة.")
        except Exception as e:
            logger.error(f"[أمر] خطأ في حذف_عملة: {e}", exc_info=True)
            await update.message.reply_text("⚠️ خطأ في جلب قائمة العملات.")

    async def cmd_edit_coin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[أمر] تعديل_عملة user={update.effective_user.id}")
        user_id = str(update.effective_user.id)
        try:
            async for session in get_session():
                coins = await CoinRepository.get_all(session, user_id)
                if not coins:
                    await update.message.reply_text("❌ لا توجد عملات.")
                    return
                msg = "⚙️ أرسل رمز العملة للتعديل:\n"
                for c in coins:
                    msg += (
                        f"- `{c.symbol}` "
                        f"(رأس مال: {c.capital_allocated}, "
                        f"إطار: {c.timeframe}, "
                        f"مخاطرة: {c.risk_per_trade}%)\n"
                    )
                await update.message.reply_text(msg, parse_mode="Markdown")
                context.user_data["action"] = "edit_coin_start"
                logger.info(f"[أمر] تعديل_عملة: عرض {len(coins)} عملة.")
        except Exception as e:
            logger.error(f"[أمر] خطأ في تعديل_عملة: {e}", exc_info=True)
            await update.message.reply_text("⚠️ خطأ في جلب قائمة العملات.")

    async def cmd_capital_mgmt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[أمر] إدارة_رأس_المال user={update.effective_user.id}")
        try:
            async for session in get_session():
                user = await UserRepository.get_by_telegram_id(
                    session, int(update.effective_user.id)
                )
                if user:
                    msg = (
                        f"💰 *إدارة رأس المال*\n\n"
                        f"رأس المال الكلي: `{user.total_capital}` USDT\n"
                        f"نسبة المخاطرة: {user.risk_per_trade}%\n"
                        f"أقصى سحب: {user.max_drawdown_limit}%"
                    )
                    from bots.telegram.keyboards import get_capital_management_menu
                    await update.message.reply_text(
                        msg,
                        reply_markup=get_capital_management_menu(),
                        parse_mode="Markdown",
                    )
                    return
            await update.message.reply_text("❌ لم يتم العثور على بيانات المستخدم.")
        except Exception as e:
            logger.error(f"[أمر] خطأ في إدارة_رأس_المال: {e}", exc_info=True)
            await update.message.reply_text("⚠️ خطأ في جلب بيانات رأس المال.")

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[أمر] إحصائيات user={update.effective_user.id}")
        user_id = str(update.effective_user.id)
        if self.portfolio_service:
            try:
                report = await self.portfolio_service.get_performance_report(user_id)
                await update.message.reply_text(report, parse_mode="Markdown")
                return
            except Exception as e:
                logger.error(f"[أمر] إحصائيات عبر portfolio_service: {e}")
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
            logger.error(f"[أمر] خطأ في الإحصائيات: {e}", exc_info=True)
            await update.message.reply_text("⚠️ خطأ في جلب الإحصائيات.")

    async def cmd_trade_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[أمر] سجل_الصفقات user={update.effective_user.id}")
        user_id = str(update.effective_user.id)
        if self.portfolio_service:
            try:
                report = await self.portfolio_service.get_trade_report(user_id)
                await update.message.reply_text(report, parse_mode="Markdown")
                return
            except Exception as e:
                logger.error(f"[أمر] سجل_الصفقات عبر الخدمة: {e}")
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
            logger.error(f"[أمر] خطأ في سجل_الصفقات: {e}", exc_info=True)
            await update.message.reply_text("⚠️ خطأ في جلب سجل الصفقات.")

    async def cmd_performance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[أمر] أداء user={update.effective_user.id}")
        user_id = str(update.effective_user.id)
        if self.portfolio_service:
            try:
                report = await self.portfolio_service.get_performance_report(user_id)
                await update.message.reply_text(report, parse_mode="Markdown")
                return
            except Exception as e:
                logger.error(f"[أمر] خطأ في الأداء: {e}")
        await update.message.reply_text("📊 استخدم 📊 الإحصائيات للحصول على التفاصيل.")

    async def cmd_recommendations(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """توصيات النظام — أداء الاستراتيجيات + توصيات التعلم."""
        logger.info(f"[أمر] توصيات user={update.effective_user.id}")
        user_id = str(update.effective_user.id)
        msg_parts = ["🧠 *توصيات النظام*\n━━━━━━━━━━━━━━"]
        has_data = False

        # 1. أداء الاستراتيجيات من محرك التعلم
        if self.portfolio_service:
            try:
                status = await self.portfolio_service.get_full_status(user_id)
                strat = status.get("strategy_performance", {})
                if strat:
                    has_data = True
                    msg_parts.append("\n📊 *أداء الاستراتيجيات:*")
                    for name, perf in list(strat.items())[:5]:
                        msg_parts.append(
                            f"• {name}: {perf.get('win_rate', 0):.0f}% فوز "
                            f"| {perf.get('total_trades', 0)} صفقة"
                        )

                recs = status.get("recommendations", [])
                if recs:
                    has_data = True
                    msg_parts.append("\n💡 *توصيات:*")
                    for r in recs[:5]:
                        msg_parts.append(f"• {r}")

                # 2. ملخص الأداء (Performance)
                try:
                    perf = await self.portfolio_service.get_performance_report(user_id)
                    if perf:
                        has_data = True
                        msg_parts.append(f"\n🎯 *ملخص الأداء:*\n{perf[:500]}")
                except Exception:
                    pass

            except Exception as e:
                logger.error(f"[أمر] خطأ في التوصيات: {e}")

        if not has_data:
            msg_parts.append("\n⏳ لا توجد بيانات كافية بعد. انتظر تنفيذ بعض الصفقات.")

        await update.message.reply_text("\n".join(msg_parts), parse_mode="Markdown")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """تقرير حالة النظام — تقرير ديناميكي شامل."""
        logger.info(f"[أمر] حالة_النظام user={update.effective_user.id}")

        # Default values when no service available
        portfolio_value = 0.0
        equity = 0.0
        open_positions = 0
        today_trades = 0
        last_market_sync = "غير متوفر"
        last_cycle_duration = "غير متوفر"
        signals_processed = 0
        signals_rejected = 0
        rejection_reasons = []
        health = {}

        if self.portfolio_service:
            try:
                status = await self.portfolio_service.get_full_status(
                    str(update.effective_user.id)
                )
                portfolio = status.get("portfolio", {})
                health = status.get("health", {})
                market = status.get("market", {})
                signals = status.get("signals", {})

                portfolio_value = portfolio.get("balance", portfolio.get("total_value", 0))
                equity = portfolio.get("equity", portfolio.get("current_equity", 0))
                open_positions = portfolio.get("open_positions", portfolio.get("open_trades", 0))
                today_trades = portfolio.get("today_trades", portfolio.get("daily_trades", 0))
                last_market_sync = market.get("last_sync", market.get("last_update", "غير متوفر"))
                last_cycle_duration = market.get("cycle_duration", market.get("last_cycle_ms", "غير متوفر"))
                signals_processed = signals.get("processed", 0)
                signals_rejected = signals.get("rejected", 0)
                rejection_reasons = signals.get("rejection_reasons", [])

            except Exception as e:
                logger.error(f"[أمر] حالة_النظام خطأ في جلب البيانات: {e}", exc_info=True)

        # ── Build health status ──
        def health_icon(ok: bool) -> str:
            return "🟢" if ok else "🔴"

        db_ok = health.get("database", health.get("db", True))
        api_ok = health.get("api", health.get("exchange_api", True))
        bot_ok = health.get("bot", health.get("telegram_bot", True))
        scheduler_ok = health.get("scheduler", health.get("scheduler_running", True))
        strategies_ok = health.get("strategies", health.get("strategy_engine", True))
        market_data_ok = health.get("market_data", health.get("data_feed", True))

        # ── Format rejection reasons ──
        reasons_text = ""
        if rejection_reasons:
            reasons_text = "\n📋 *أسباب الرفض:*\n"
            # Group by reason
            from collections import Counter
            counts = Counter(rejection_reasons)
            for reason, count in counts.most_common(5):
                reasons_text += f"  • {reason}: {count}\n"

        msg = (
            f"📡 *حالة النظام*\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"💼 *المحفظة*\n"
            f"  💰 قيمة المحفظة: `{portfolio_value:,.2f} USDT`\n"
            f"  📊 Equity: `{equity:,.2f} USDT`\n"
            f"  🔓 صفقات مفتوحة: {open_positions}\n"
            f"  📅 صفقات اليوم: {today_trades}\n\n"
            f"🔄 *السوق*\n"
            f"  🕐 آخر مزامنة: {last_market_sync}\n"
            f"  ⏱ مدة آخر دورة: {last_cycle_duration}\n\n"
            f"📶 *الإشارات*\n"
            f"  ✅ معالجة: {signals_processed}\n"
            f"  ❌ مرفوضة: {signals_rejected}"
            f"{reasons_text}\n"
            f"🏥 *صحة الخدمات*\n"
            f"  {health_icon(db_ok)} قاعدة البيانات\n"
            f"  {health_icon(api_ok)} API\n"
            f"  {health_icon(bot_ok)} البوت\n"
            f"  {health_icon(scheduler_ok)} المجدول\n"
            f"  {health_icon(strategies_ok)} الاستراتيجيات\n"
            f"  {health_icon(market_data_ok)} بيانات السوق"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
        logger.info(f"[أمر] حالة_النظام: تم عرض التقرير الكامل.")

    async def cmd_emergency_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.warning(f"[أمر] إيقاف_طوارئ user={update.effective_user.id}")
        if self.risk_service:
            self.risk_service.emergency_stop("Manual from Telegram")
        try:
            user_id = str(update.effective_user.id)
            async for session in get_session():
                user = await UserRepository.get_by_telegram_id(session, int(user_id))
                if user:
                    await UserRepository.update_status(session, user, False, True)
        except Exception as e:
            logger.error(f"[أمر] خطأ في إيقاف_الطوارئ DB: {e}")
        await update.message.reply_text("🛑 *تم تفعيل إيقاف الطوارئ!*", parse_mode="Markdown")

    async def cmd_start_trading(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[أمر] تشغيل_التداول user={update.effective_user.id}")
        if self.risk_service:
            self.risk_service.resume_trading()
        try:
            user_id = str(update.effective_user.id)
            async for session in get_session():
                user = await UserRepository.get_by_telegram_id(session, int(user_id))
                if user:
                    await UserRepository.update_status(session, user, True)
        except Exception as e:
            logger.error(f"[أمر] خطأ في تشغيل_التداول DB: {e}")
        await update.message.reply_text("▶️ نظام التداول يعمل الآن.")

    async def cmd_stop_trading(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"[أمر] إيقاف_التداول user={update.effective_user.id}")
        if self.risk_service:
            self.risk_service.emergency_stop("Manual stop")
        try:
            user_id = str(update.effective_user.id)
            async for session in get_session():
                user = await UserRepository.get_by_telegram_id(session, int(user_id))
                if user:
                    await UserRepository.update_status(session, user, False)
        except Exception as e:
            logger.error(f"[أمر] خطأ في إيقاف_التداول DB: {e}")
        await update.message.reply_text("⏸ نظام التداول متوقف.")

    # ── Action Processing (edit/delete flows) ───────────────

    async def _process_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        action = context.user_data.get("action")
        text = update.message.text.strip().upper()
        uid = update.effective_user.id
        logger.info(f"[إجراء] user={uid} action={action} text={repr(text)}")

        try:
            if action == "delete_coin":
                user_id = str(uid)
                async for session in get_session():
                    await CoinRepository.delete_by_symbol(session, user_id, text)
                await update.message.reply_text(f"✅ تم حذف {text}.")
                context.user_data.pop("action", None)
                logger.info(f"[إجراء] تم حذف العملة: {text}")

            elif action == "edit_coin_start":
                context.user_data["edit_target"] = text
                await update.message.reply_text(
                    f"📝 *تعديل {text}*\n\n"
                    f"💰 أدخل رأس المال الجديد (USDT):\n"
                    f"أو أرسل: `skip` للاحتفاظ بالقيمة الحالية",
                    parse_mode="Markdown",
                )
                context.user_data["action"] = "edit_coin_capital"
                logger.info(f"[إجراء] بدء تعديل العملة: {text}")

            elif action == "edit_coin_capital":
                symbol = context.user_data["edit_target"]
                user_id = str(uid)
                if text.upper() == "SKIP":
                    cap = None
                else:
                    cap = float(text)

                async for session in get_session():
                    coin = await CoinRepository.get_by_symbol(session, user_id, symbol)
                    if coin:
                        if cap is not None:
                            await CoinRepository.update(
                                session, coin, capital_allocated=cap
                            )
                        update_msg = f"✅ تم تحديث {symbol}."
                        if cap is not None:
                            update_msg += f"\n💰 رأس المال: {cap} USDT"
                        else:
                            update_msg += "\n💰 تم الاحتفاظ برأس المال الحالي."
                await update.message.reply_text(update_msg)
                context.user_data.clear()
                logger.info(f"[إجراء] تم تحديث رأس مال العملة: {symbol}")

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
                logger.info(f"[إجراء] تم تحديث رأس المال الأساسي إلى {cap}")

            else:
                logger.warning(f"[إجراء] إجراء غير معروف: {action}")
                await update.message.reply_text("⚠️ أمر غير معروف.")
                context.user_data.clear()
        except ValueError:
            await update.message.reply_text("❌ أدخل قيمة عددية صحيحة.")
        except Exception as e:
            logger.error(f"[إجراء] خطأ في {action}: {e}", exc_info=True)
            await update.message.reply_text("⚠️ حدث خطأ. حاول مرة أخرى.")
