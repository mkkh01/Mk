import asyncio
import json
import os
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, filters
from sqlalchemy import select, delete, func
from database import AsyncSessionLocal, UserConfig, TrackedCoin, LiveTrade, ShadowTrade
from bot.keyboards import get_main_menu, get_capital_management_menu, get_timeframe_menu, get_risk_management_menu
from datetime import datetime
from config import ADMIN_ID

# States for ConversationHandler
ADD_SYMBOL, ADD_CAPITAL, ADD_RISK, ADD_TF = range(4)
EDIT_BASE_CAPITAL = 5

async def check_admin(update: Update) -> bool:
    return update.effective_user.id == ADMIN_ID

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_admin(update): return
    user_id = update.effective_user.id
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(UserConfig).where(UserConfig.telegram_id == user_id))
        if not res.scalars().first():
            new_user = UserConfig(telegram_id=user_id)
            session.add(new_user)
            await session.commit()
    
    await update.message.reply_text(
        "👋 أهلاً بك في نظام التداول المؤسسي CT V4.0\nتم تصميم هذا النظام لحماية رأس مالك وتحقيق نمو مستقر.",
        reply_markup=get_main_menu()
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_admin(update): return
    text = update.message.text
    action = context.user_data.get('action')
    
    if action:
        await process_text_input(update, context)
        return

    if text == "📈 الأسعار المباشرة":
        await show_live_prices(update, context)
    elif text == "➕ إضافة عملة":
        await update.message.reply_text("✍️ أرسل رمز العملة (مثال: BTCUSDT):")
        return ADD_SYMBOL
    elif text == "➖ حذف عملة":
        await show_remove_coin_list(update, context)
    elif text == "⚙️ تعديل العملة":
        await show_edit_coin_list(update, context)
    elif text == "💰 إدارة رأس المال":
        await show_capital_mgmt(update, context)
    elif text in ["📊 الإحصائيات", "🎯 تقرير الأداء"]:
        await show_statistics(update, context)
    elif text == "📋 سجل الصفقات":
        await show_trade_history(update, context)
    elif text == "🛑 إيقاف الطوارئ":
        await emergency_stop(update, context)
    elif text == "▶️ تشغيل التداول":
        await toggle_trading(update, context, True)
    elif text == "⏸ إيقاف التداول":
        await toggle_trading(update, context, False)
    elif text == "🧠 تقرير الذكاء الاصطناعي":
        await show_ai_report(update, context)

async def show_edit_coin_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with AsyncSessionLocal() as session:
        coins = (await session.execute(select(TrackedCoin))).scalars().all()
        if not coins:
            await update.message.reply_text("❌ لا توجد عملات لتعديلها.")
            return
        msg = "⚙️ *اختر العملة لتعديل إعداداتها:*\n━━━━━━━━━━━━━━\n"
        for c in coins:
            msg += f"🪙 {c.symbol} | الرأس مال: {c.capital} | الإطار: {c.timeframe}\n"
        await update.message.reply_text(msg + "\nأرسل رمز العملة للبدء بالتعديل:", parse_mode='Markdown')
        context.user_data['action'] = 'edit_coin_start'

async def process_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = context.user_data.get('action')
    text = update.message.text.strip().upper()

    if action == 'delete_coin':
        async with AsyncSessionLocal() as session:
            await session.execute(delete(TrackedCoin).where(TrackedCoin.symbol == text))
            await session.commit()
        await update.message.reply_text(f"✅ تم حذف {text} بنجاح.")
        context.user_data.pop('action')
    elif action == 'edit_coin_start':
        context.user_data['edit_target'] = text
        await update.message.reply_text(f"💰 أدخل رأس المال الجديد لـ {text}:")
        context.user_data['action'] = 'edit_coin_capital'
    elif action == 'edit_coin_capital':
        cap = float(text)
        symbol = context.user_data['edit_target']
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(TrackedCoin).where(TrackedCoin.symbol == symbol))
            coin = res.scalars().first()
            if coin:
                coin.capital = cap
                await session.commit()
        await update.message.reply_text(f"✅ تم تحديث رأس مال {symbol} إلى {cap}.")
        context.user_data.clear()

async def show_live_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    PRICES_CACHE = "/tmp/live_prices.json"
    if not os.path.exists(PRICES_CACHE):
        await update.message.reply_text("⏳ جاري الاتصال بالرادار... حاول مرة أخرى.")
        return
    with open(PRICES_CACHE, 'r') as f:
        prices = json.load(f)
    if not prices:
        await update.message.reply_text("❌ لا توجد عملات مضافة.")
        return
    msg = "📈 *الأسعار المباشرة*\n━━━━━━━━━━━━━━\n"
    for s, d in prices.items():
        msg += f"🪙 {s}: `{d['price']}`\n"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def show_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with AsyncSessionLocal() as session:
        trades = (await session.execute(select(LiveTrade).where(LiveTrade.status != "OPEN"))).scalars().all()
        if not trades:
            await update.message.reply_text("❌ لا توجد صفقات مغلقة.")
            return
        total = len(trades)
        wins = len([t for t in trades if t.status == "WON"])
        total_pnl = sum([t.pnl for t in trades])
        msg = (f"📊 *إحصائيات الأداء المؤسسي*\n"
               f"━━━━━━━━━━━━━━\n"
               f"📈 إجمالي الصفقات: {total}\n"
               f"✅ نسبة النجاح: {(wins/total)*100:.2f}%\n"
               f"💰 صافي الربح: `{total_pnl:.2f} USDT`\n"
               f"🏆 أفضل عملة: {max(trades, key=lambda t: t.pnl).symbol}")
        await update.message.reply_text(msg, parse_mode='Markdown')

async def emergency_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with AsyncSessionLocal() as session:
        cfg = (await session.execute(select(UserConfig).where(UserConfig.telegram_id == ADMIN_ID))).scalars().first()
        cfg.emergency_stop = True
        cfg.is_active = False
        await session.commit()
    await update.message.reply_text("🛑 *EMERGENCY STOP ACTIVATED!*", parse_mode='Markdown')

async def toggle_trading(update: Update, context: ContextTypes.DEFAULT_TYPE, status: bool):
    async with AsyncSessionLocal() as session:
        cfg = (await session.execute(select(UserConfig).where(UserConfig.telegram_id == ADMIN_ID))).scalars().first()
        cfg.is_active = status
        cfg.emergency_stop = False
        await session.commit()
    await update.message.reply_text("▶️ نظام التداول يعمل" if status else "⏸ نظام التداول متوقف")

async def show_ai_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with AsyncSessionLocal() as session:
        shadows = (await session.execute(select(ShadowTrade).order_by(ShadowTrade.timestamp.desc()).limit(5))).scalars().all()
        if not shadows:
            await update.message.reply_text("🧠 لا توجد بيانات تعلم كافية.")
            return
        msg = "🧠 *تقرير الذكاء الاصطناعي والتعلم*\n━━━━━━━━━━━━━━\n"
        for s in shadows:
            msg += f"🪙 {s.symbol} | Score: {s.score}/100 | State: {s.market_state}\n"
        await update.message.reply_text(msg, parse_mode='Markdown')

async def show_trade_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with AsyncSessionLocal() as session:
        trades = (await session.execute(select(LiveTrade).order_by(LiveTrade.timestamp.desc()).limit(10))).scalars().all()
        if not trades:
            await update.message.reply_text("❌ السجل فارغ.")
            return
        msg = "📋 *سجل آخر 10 صفقات*\n━━━━━━━━━━━━━━\n"
        for t in trades:
            icon = "✅" if t.status == "WON" else "❌"
            msg += f"{icon} {t.symbol} | PnL: `{t.pnl:.2f}`\n"
        await update.message.reply_text(msg, parse_mode='Markdown')

async def show_capital_mgmt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with AsyncSessionLocal() as session:
        cfg = (await session.execute(select(UserConfig).where(UserConfig.telegram_id == ADMIN_ID))).scalars().first()
        await update.message.reply_text(f"💰 *إدارة رأس المال*\n\nرأس المال الكلي: `{cfg.total_capital}` USDT", 
                                       reply_markup=get_capital_management_menu(), parse_mode='Markdown')

async def show_remove_coin_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with AsyncSessionLocal() as session:
        coins = (await session.execute(select(TrackedCoin))).scalars().all()
        if not coins:
            await update.message.reply_text("❌ لا توجد عملات لحذفها.")
            return
        msg = "➖ أرسل رمز العملة لحذفها:\n"
        for c in coins: msg += f"- `{c.symbol}`\n"
        await update.message.reply_text(msg, parse_mode='Markdown')
        context.user_data['action'] = 'delete_coin'

async def process_add_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_coin_symbol'] = update.message.text.strip().upper()
    await update.message.reply_text("💰 أدخل رأس المال المخصص لهذه العملة:")
    return ADD_CAPITAL

async def process_add_capital(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['new_coin_capital'] = float(update.message.text)
        await update.message.reply_text("⚠️ أدخل نسبة المخاطرة (مثال: 1):")
        return ADD_RISK
    except ValueError:
        await update.message.reply_text("❌ خطأ: يرجى إدخال قيمة عددية صحيحة لرأس المال (مثال: 100).")
        return ADD_CAPITAL

async def process_add_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['new_coin_risk'] = float(update.message.text)
        await update.message.reply_text("⏱ اختر الإطار الزمني:", reply_markup=get_timeframe_menu())
        return ADD_TF
    except ValueError:
        await update.message.reply_text("❌ خطأ: يرجى إدخال قيمة عددية صحيحة لنسبة المخاطرة (مثال: 1).")
        return ADD_RISK

async def process_add_tf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tf = query.data.replace("tf_", "")
    async with AsyncSessionLocal() as session:
        new_coin = TrackedCoin(
            symbol=context.user_data['new_coin_symbol'],
            capital=context.user_data['new_coin_capital'],
            risk_percentage=context.user_data['new_coin_risk'],
            timeframe=tf
        )
        session.add(new_coin)
        await session.commit()
    await query.edit_message_text(f"✅ تمت إضافة {context.user_data['new_coin_symbol']} بنجاح!")
    return ConversationHandler.END
