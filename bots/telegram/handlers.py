"""
Telegram Handlers — all command and message handlers.
NO business logic. Only routes to services and formats output.
"""
import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from database.repositories import (
    UserRepository, CoinRepository, get_session
)
from database.models import Coin
from config.constants import ADMIN_ID

logger = logging.getLogger("telegram.handlers")

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

    async def _is_admin(self, update: Update) -> bool:
        return update.effective_user.id == self.admin_id

    # ── Start ───────────────────────────────────────────────
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update):
            return
        user_id = str(update.effective_user.id)
        async for session in get_session():
            await UserRepository.get_or_create(session, int(user_id))

        from bots.telegram.keyboards import get_main_menu
        await update.message.reply_text(
            "👋 أهلاً بك في نظام التداول المؤسسي CT V4.0\n"
            "تم تصميم هذا النظام لحماية رأس مالك وتحقيق نمو مستقر.\n\n"
            "⚙️ *معمارية جديدة:* 14 محرك مستقل | Clean Architecture | Event-Driven",
            reply_markup=get_main_menu(),
            parse_mode="Markdown",
        )

    # ── Main Menu Router ────────────────────────────────────
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update):
            return
        text = update.message.text
        action = context.user_data.get("action")

        if action:
            await self._process_action(update, context)
            return

        routes = {
            "📈 الأسعار المباشرة": self.cmd_live_prices,
            "➕ إضافة عملة": self.start_add_coin,
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
            await handler(update, context)

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data

        if data == "main_menu":
            from bots.telegram.keyboards import get_main_menu
            await query.edit_message_text("🏠 القائمة الرئيسية", reply_markup=get_main_menu())
        elif data == "edit_base_capital":
            await query.edit_message_text("💵 أرسل رأس المال الأساسي الجديد:")
            context.user_data["action"] = "edit_base_capital"
        elif data.startswith("set_risk_"):
            risk_val = data.replace("set_risk_", "")
            async for session in get_session():
                user = await UserRepository.get_by_telegram_id(session, self.admin_id)
                if user:
                    user.risk_per_trade = float(risk_val)
                    await session.commit()
            await query.edit_message_text(f"✅ تم تعيين نسبة المخاطرة إلى {risk_val}%")

    # ── Commands ────────────────────────────────────────────
    async def start_add_coin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("✍️ أرسل رمز العملة (مثال: BTCUSDT):")
        return ADD_SYMBOL

    async def process_add_symbol(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["new_coin_symbol"] = update.message.text.strip().upper()
        await update.message.reply_text("💰 أدخل رأس المال المخصص:")
        return ADD_CAPITAL

    async def process_add_capital(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            context.user_data["new_coin_capital"] = float(update.message.text)
            await update.message.reply_text("⚠️ أدخل نسبة المخاطرة (مثال: 1):")
            return ADD_RISK
        except ValueError:
            await update.message.reply_text("❌ خطأ: أدخل قيمة عددية.")
            return ADD_CAPITAL

    async def process_add_risk(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            context.user_data["new_coin_risk"] = float(update.message.text)
            from bots.telegram.keyboards import get_timeframe_menu
            await update.message.reply_text("⏱ اختر الإطار الزمني:", reply_markup=get_timeframe_menu())
            return ADD_TF
        except ValueError:
            await update.message.reply_text("❌ خطأ: أدخل قيمة عددية.")
            return ADD_RISK

    async def process_add_tf(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        tf = query.data.replace("tf_", "")
        user_id = str(query.from_user.id)

        async for session in get_session():
            coin = Coin(
                user_id=user_id,
                symbol=context.user_data["new_coin_symbol"],
                capital_allocated=context.user_data["new_coin_capital"],
                risk_per_trade=context.user_data["new_coin_risk"],
                timeframe=tf,
            )
            session.add(coin)
            await session.commit()

        await query.edit_message_text(f"✅ تمت إضافة {context.user_data['new_coin_symbol']} بنجاح!")
        return -1  # End conversation

    async def cmd_live_prices(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        import json, os
        cache = "/tmp/live_prices.json"
        if not os.path.exists(cache):
            await update.message.reply_text("⏳ جاري الاتصال بالرادار... حاول بعد لحظات.")
            return
        with open(cache, "r") as f:
            prices = json.load(f)
        if not prices:
            await update.message.reply_text("❌ لا توجد عملات مضافة.")
            return
        msg = "📈 *الأسعار المباشرة*\n━━━━━━━━━━━━━━\n"
        for s, d in list(prices.items())[:15]:
            msg += f"🪙 {s}: `{d['price']}`\n"
        await update.message.reply_text(msg, parse_mode="Markdown")

    async def cmd_delete_coin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
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

    async def cmd_edit_coin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
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

    async def cmd_capital_mgmt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        async for session in get_session():
            user = await UserRepository.get_by_telegram_id(session, int(user_id))
            if user:
                msg = (
                    f"💰 *إدارة رأس المال*\n\n"
                    f"رأس المال الكلي: `{user.total_capital}` USDT\n"
                    f"نسبة المخاطرة: {user.risk_per_trade}%\n"
                    f"أقصى سحب: {user.max_drawdown_limit}%"
                )
                from bots.telegram.keyboards import get_capital_management_menu
                await update.message.reply_text(msg, reply_markup=get_capital_management_menu(), parse_mode="Markdown")

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        if self.portfolio_service:
            report = await self.portfolio_service.get_performance_report(user_id)
            await update.message.reply_text(report, parse_mode="Markdown")
        else:
            # Fallback: direct DB query
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

    async def cmd_trade_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        if self.portfolio_service:
            report = await self.portfolio_service.get_trade_report(user_id)
            await update.message.reply_text(report, parse_mode="Markdown")
        else:
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

    async def cmd_performance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        if self.portfolio_service:
            report = await self.portfolio_service.get_performance_report(user_id)
            await update.message.reply_text(report, parse_mode="Markdown")
        else:
            await update.message.reply_text("📊 استخدم /stats للحصول على الإحصائيات.")

    async def cmd_ai_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self.portfolio_service:
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
            await update.message.reply_text(msg, parse_mode="Markdown")
        else:
            await update.message.reply_text("🧠 لا توجد بيانات تعلم كافية بعد.")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self.portfolio_service:
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
        else:
            await update.message.reply_text("📡 النظام يعمل. استخدم /start للمزيد.")

    async def cmd_emergency_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self.risk_service:
            self.risk_service.emergency_stop("Manual from Telegram")
        # Also update DB
        user_id = str(update.effective_user.id)
        async for session in get_session():
            user = await UserRepository.get_by_telegram_id(session, int(user_id))
            if user:
                await UserRepository.update_status(session, user, False, True)
        await update.message.reply_text("🛑 *EMERGENCY STOP ACTIVATED!*", parse_mode="Markdown")

    async def cmd_start_trading(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self.risk_service:
            self.risk_service.resume_trading()
        user_id = str(update.effective_user.id)
        async for session in get_session():
            user = await UserRepository.get_by_telegram_id(session, int(user_id))
            if user:
                await UserRepository.update_status(session, user, True)
        await update.message.reply_text("▶️ نظام التداول يعمل الآن.")

    async def cmd_stop_trading(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self.risk_service:
            self.risk_service.emergency_stop("Manual stop")
        user_id = str(update.effective_user.id)
        async for session in get_session():
            user = await UserRepository.get_by_telegram_id(session, int(user_id))
            if user:
                await UserRepository.update_status(session, user, False)
        await update.message.reply_text("⏸ نظام التداول متوقف.")

    # ── Action Processing ───────────────────────────────────
    async def _process_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        action = context.user_data.get("action")
        text = update.message.text.strip().upper()

        if action == "delete_coin":
            user_id = str(update.effective_user.id)
            async for session in get_session():
                await CoinRepository.delete_by_symbol(session, user_id, text)
            await update.message.reply_text(f"✅ تم حذف {text}.")
            context.user_data.pop("action")

        elif action == "edit_coin_start":
            context.user_data["edit_target"] = text
            await update.message.reply_text(f"💰 أدخل رأس المال الجديد لـ {text}:")
            context.user_data["action"] = "edit_coin_capital"

        elif action == "edit_coin_capital":
            cap = float(text)
            symbol = context.user_data["edit_target"]
            user_id = str(update.effective_user.id)
            async for session in get_session():
                coin = await CoinRepository.get_by_symbol(session, user_id, symbol)
                if coin:
                    await CoinRepository.update(session, coin, capital_allocated=cap)
            await update.message.reply_text(f"✅ تم تحديث رأس مال {symbol} إلى {cap}.")
            context.user_data.clear()

        elif action == "edit_base_capital":
            try:
                cap = float(text)
                user_id = str(update.effective_user.id)
                async for session in get_session():
                    user = await UserRepository.get_by_telegram_id(session, int(user_id))
                    if user:
                        user.total_capital = cap
                        await session.commit()
                await update.message.reply_text(f"✅ تم تحديث رأس المال الأساسي إلى {cap}.")
                context.user_data.clear()
            except ValueError:
                await update.message.reply_text("❌ أدخل قيمة عددية.")
