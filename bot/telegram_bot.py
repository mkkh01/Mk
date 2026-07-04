"""
CTM Bot - Telegram Interface
10-button menu with conversations for adding coins.
"""
import asyncio
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters
)
from config import TELEGRAM_BOT_TOKEN, TIMEFRAMES
from db.supabase_client import (
    get_active_coins, add_coin, remove_coin,
    get_recent_signals, get_active_signals as db_get_active_signals,
    get_recent_results, get_recent_logs
)
from data.binance_api import get_all_prices as fetch_all_prices, get_24hr_ticker

(SYMBOL, TIMEFRAMES_STATE, CAPITAL, RISK) = range(4)

MAIN_KEYBOARD = [
    ["💰 أسعار حية", "➕ إضافة عملة"],
    ["📋 عملاتي", "🗑️ حذف عملة"],
    ["📊 الإشارات", "📈 الصفقات"],
    ["📉 النتائج", "📜 السجلات"],
    ["⏸️ إيقاف", "▶️ تشغيل"]
]

def get_main_keyboard():
    return ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)

system_active = True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🫡 **CTM Bot v1.0 — Crypto Trading Monitor**\n\n"
        "محلل فني ذكي لتوليد إشارات التداول ومراقبة الصفقات.\n"
        "اختر من القائمة:",
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "💰 أسعار حية":
        await show_live_prices(update)
    elif text == "📋 عملاتي":
        await show_my_coins(update)
    elif text == "📊 الإشارات":
        await show_signals(update)
    elif text == "📈 الصفقات":
        await show_active_trades(update)
    elif text == "📉 النتائج":
        await show_results(update)
    elif text == "📜 السجلات":
        await show_logs(update)
    elif text == "🗑️ حذف عملة":
        await show_delete_menu(update)
    elif text == "⏸️ إيقاف":
        global system_active
        system_active = False
        await update.message.reply_text("⏸️ تم إيقاف توليد الإشارات.", reply_markup=get_main_keyboard())
    elif text == "▶️ تشغيل":
        system_active = True
        await update.message.reply_text("▶️ تم تشغيل النظام.", reply_markup=get_main_keyboard())
    elif text == "رجوع":
        await update.message.reply_text("القائمة الرئيسية:", reply_markup=get_main_keyboard())

async def show_live_prices(update: Update):
    coins = get_active_coins()
    if not coins:
        await update.message.reply_text("❌ لا توجد عملات مضافة.", reply_markup=get_main_keyboard())
        return
    symbols = [c['symbol'] for c in coins]
    try:
        msg = "💰 **الأسعار الحية**\n\n"
        for sym in symbols:
            try:
                t = get_24hr_ticker(sym)
                if t.get('_ok'):
                    emoji = "🟢" if t['change_pct'] >= 0 else "🔴"
                    msg += f"{emoji} **{t['symbol']}**: ${t['price']:.4f} ({t['change_pct']:+.2f}%)\n"
                else:
                    err = t.get('_errors', 'Unknown')
                    msg += f"❓ **{sym}**: {err[:80]}\n"
            except Exception as e:
                msg += f"❓ **{sym}**: {str(e)[:80]}\n"
        await update.message.reply_text(msg, reply_markup=get_main_keyboard(), parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}", reply_markup=get_main_keyboard())

async def show_my_coins(update: Update):
    coins = get_active_coins()
    if not coins:
        await update.message.reply_text("❌ لا توجد عملات مضافة.", reply_markup=get_main_keyboard())
        return
    msg = "📋 **عملاتي**\n\n"
    for c in coins:
        tfs = ', '.join(c['timeframes']) if c['timeframes'] else '1h'
        msg += f"**{c['symbol']}**\n  ⏱ {tfs}\n  💰 رأس المال: {c['capital_value']} USDT\n  ⚠️ المخاطرة: {c['risk_percent']}%\n\n"
    await update.message.reply_text(msg, reply_markup=get_main_keyboard(), parse_mode='Markdown')

async def show_signals(update: Update):
    signals = get_recent_signals(10)
    if not signals:
        await update.message.reply_text("📊 لا توجد إشارات حديثة.", reply_markup=get_main_keyboard())
        return
    msg = "📊 **آخر الإشارات**\n\n"
    for s in signals[:8]:
        status_emoji = {'PENDING': '⏳', 'ACTIVE': '🟢', 'TP_HIT': '🎯', 'SL_HIT': '🛑'}.get(s['signal_status'], '❓')
        msg += f"{status_emoji} **{s['symbol']}** ({s['timeframe']})\n  دخول: {s['entry_price']:.4f} | وقف: {s['stop_loss']:.4f}\n  هدف: {s['take_profit1']:.4f}\n\n"
    await update.message.reply_text(msg, reply_markup=get_main_keyboard(), parse_mode='Markdown')

async def show_active_trades(update: Update):
    trades = db_get_active_signals()
    if not trades:
        await update.message.reply_text("📈 لا توجد صفقات نشطة.", reply_markup=get_main_keyboard())
        return
    msg = "📈 **الصفقات النشطة**\n\n"
    for t in trades:
        msg += f"🔵 **{t['symbol']}** ({t['timeframe']})\n  دخول: {t['entry_price']:.4f}\n  🛑 SL: {t['stop_loss']:.4f}\n  🎯 TP: {t['take_profit1']:.4f}\n\n"
    await update.message.reply_text(msg, reply_markup=get_main_keyboard(), parse_mode='Markdown')

async def show_results(update: Update):
    results = get_recent_results(10)
    if not results:
        await update.message.reply_text("📉 لا توجد نتائج بعد.", reply_markup=get_main_keyboard())
        return
    msg = "📉 **نتائج الصفقات**\n\n"
    total_pnl = 0
    wins = 0
    for r in results:
        emoji = "🟢" if r['profit_pct'] >= 0 else "🔴"
        msg += f"{emoji} **{r['symbol']}** | {r['result']}\n  ربح: {r['profit_pct']:+.2f}% (${r['profit_usd']:+.2f})\n\n"
        total_pnl += r['profit_usd']
        if r['profit_pct'] > 0:
            wins += 1
    win_rate = (wins / len(results) * 100) if results else 0
    msg += f"📊 Win Rate: {win_rate:.0f}% | Total PnL: ${total_pnl:+.2f}"
    await update.message.reply_text(msg, reply_markup=get_main_keyboard(), parse_mode='Markdown')

async def show_logs(update: Update):
    logs = get_recent_logs(15)
    if not logs:
        await update.message.reply_text("📜 لا توجد سجلات بعد.", reply_markup=get_main_keyboard())
        return
    msg = "📜 **آخر السجلات**\n\n"
    for l in logs:
        ts = l['timestamp'].strftime('%H:%M:%S') if hasattr(l['timestamp'], 'strftime') else str(l['timestamp'])[11:19]
        msg += f"`[{ts}]` {l['level']} {l['component']} — {l['message'][:100]}\n"
    if len(msg) > 3500:
        msg = msg[:3500] + "\n..."
    await update.message.reply_text(msg, reply_markup=get_main_keyboard(), parse_mode='Markdown')

async def show_delete_menu(update: Update):
    coins = get_active_coins()
    if not coins:
        await update.message.reply_text("لا توجد عملات للحذف.", reply_markup=get_main_keyboard())
        return
    keyboard = [[KeyboardButton(f"حذف {c['symbol']}")] for c in coins]
    keyboard.append([KeyboardButton("رجوع")])
    await update.message.reply_text("اختر العملة المراد حذفها:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def handle_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = update.message.text.replace("حذف ", "").strip()
    remove_coin(symbol)
    await update.message.reply_text(f"🗑️ تم حذف **{symbol}**", reply_markup=get_main_keyboard(), parse_mode='Markdown')

async def add_coin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("➕ **إضافة عملة جديدة**\n\nأرسل رمز العملة (مثال: BTCUSDT):", parse_mode='Markdown')
    return SYMBOL

async def add_coin_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = update.message.text.strip().upper()
    context.user_data['new_coin'] = {'symbol': symbol}
    await update.message.reply_text(f"✅ الرمز: **{symbol}**\n\nاختر الأطر الزمنية (مفصولة بفواصل):\nمثال: `15m, 1h, 4h`", parse_mode='Markdown')
    return TIMEFRAMES_STATE

async def add_coin_timeframes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tfs = [t.strip() for t in update.message.text.split(',')]
    valid_tfs = [t for t in tfs if t in TIMEFRAMES]
    if not valid_tfs:
        await update.message.reply_text("❌ أطر غير صالحة. حاول مجددًا:")
        return TIMEFRAMES_STATE
    context.user_data['new_coin']['timeframes'] = valid_tfs
    await update.message.reply_text(f"✅ الأطر: {', '.join(valid_tfs)}\n\nأدخل قيمة رأس المال بالـ USDT (مثال: 100):")
    return CAPITAL

async def add_coin_capital(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        capital = float(update.message.text)
        if capital <= 0 or capital > 100000:
            raise ValueError
    except:
        await update.message.reply_text("❌ أدخل رقمًا بين 1 و 100:")
        return CAPITAL
    context.user_data['new_coin']['capital_value'] = capital
    await update.message.reply_text(f"✅ رأس المال: {capital} USDT\n\nأدخل نسبة المخاطرة (مثال: 2):")
    return RISK

async def add_coin_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        risk = float(update.message.text)
        if risk <= 0 or risk > 20:
            raise ValueError
    except:
        await update.message.reply_text("❌ أدخل رقمًا بين 0.1 و 20:")
        return RISK
    coin = context.user_data['new_coin']
    coin['risk_percent'] = risk
    add_coin(coin['symbol'], coin['timeframes'], coin['capital_value'], coin['risk_percent'])
    await update.message.reply_text(
        f"✅ **تمت إضافة العملة!**\n\n"
        f"🔹 **{coin['symbol']}**\n"
        f"🔹 الأطر: {', '.join(coin['timeframes'])}\n"
        f"🔹 رأس المال: {coin['capital_value']} USDT\n"
        f"🔹 المخاطرة: {coin['risk_percent']}%",
        reply_markup=get_main_keyboard(), parse_mode='Markdown')
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تم الإلغاء.", reply_markup=get_main_keyboard())
    return ConversationHandler.END

def build_application() -> Application:
    """Build and return the Telegram Application."""
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ إضافة عملة$"), add_coin_start)],
        states={
            SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_coin_symbol)],
            TIMEFRAMES_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_coin_timeframes)],
            CAPITAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_coin_capital)],
            RISK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_coin_risk)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(CommandHandler('start', start))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.Regex(r"^حذف .+$"), handle_delete))
    app.add_handler(MessageHandler(filters.Regex("^رجوع$"), lambda u, c: start(u, c)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app

def run_bot():
    app = build_application()
    app.run_polling(drop_pending_updates=True)
