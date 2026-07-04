"""
CTM Bot - Telegram Interface
10-button menu with conversations for adding coins.
"""
import sys
import asyncio

# Python 3.14 fix: PTB v20.x Updater.__slots__ is missing __polling_cleanup_cb.
# Python 3.14 enforces __slots__ strictly — annotated attrs not in slots fail.
# Patch before importing telegram.ext to prevent AttributeError at runtime.
if sys.version_info >= (3, 14):
    from telegram.ext._updater import Updater as _Updater
    _slots = tuple(_Updater.__slots__) if hasattr(_Updater, '__slots__') else ()
    if '__dict__' not in _slots:
        _Updater.__slots__ = _slots + ('__dict__',)

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

# Conversation states for adding a coin
(SYMBOL, TIMEFRAMES, CAPITAL, RISK, CONFIRM) = range(5)

# === Keyboard ===
MAIN_KEYBOARD = [
    ["💰 أسعار حية", "➕ إضافة عملة"],
    ["📋 عملاتي", "🗑️ حذف عملة"],
    ["📊 الإشارات", "📈 الصفقات"],
    ["📉 النتائج", "📜 السجلات"],
    ["⏸️ إيقاف", "▶️ تشغيل"]
]

def get_main_keyboard():
    return ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)

# === System State ===
system_active = True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message with main menu."""
    await update.message.reply_text(
        "🫡 **CTM Bot v1.0 — Crypto Trading Monitor**\n\n"
        "محلل فني ذكي لتوليد إشارات التداول ومراقبة الصفقات.\n"
        "اختر من القائمة:",
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button presses from the main menu."""
    text = update.message.text
    chat_id = update.effective_chat.id
    
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
        await update.message.reply_text("▶️ تم تشغيل النظام — جاري تحليل الأسواق.", reply_markup=get_main_keyboard())
    elif text == "رجوع":
        await update.message.reply_text("القائمة الرئيسية:", reply_markup=get_main_keyboard())

async def show_live_prices(update: Update):
    """Display live prices for all tracked coins."""
    coins = get_active_coins()
    if not coins:
        await update.message.reply_text("❌ لا توجد عملات مضافة. اضغط ➕ إضافة عملة", reply_markup=get_main_keyboard())
        return
    
    symbols = [c['symbol'] for c in coins]
    
    try:
        tickers = []
        for sym in symbols:
            try:
                t = get_24hr_ticker(sym)
                tickers.append(t)
            except:
                tickers.append({'symbol': sym, 'price': 0, 'change_pct': 0})
        
        msg = "💰 **الأسعار الحية**\n\n"
        for t in tickers:
            emoji = "🟢" if t['change_pct'] >= 0 else "🔴"
            msg += f"{emoji} **{t['symbol']}**: ${t['price']:.4f} ({t['change_pct']:+.2f}%)\n"
        
        await update.message.reply_text(msg, reply_markup=get_main_keyboard(), parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ في جلب الأسعار: {e}", reply_markup=get_main_keyboard())

async def show_my_coins(update: Update):
    """Display all tracked coins with their settings."""
    coins = get_active_coins()
    if not coins:
        await update.message.reply_text("❌ لا توجد عملات مضافة.", reply_markup=get_main_keyboard())
        return
    
    msg = "📋 **عملاتي**\n\n"
    for c in coins:
        tfs = ', '.join(c['timeframes']) if c['timeframes'] else '1h'
        msg += (f"**{c['symbol']}**\n"
                f"  ⏱ أطر: {tfs}\n"
                f"  💰 رأس المال: {c['capital_percent']}%\n"
                f"  ⚠️ المخاطرة: {c['risk_percent']}%\n\n")
    
    await update.message.reply_text(msg, reply_markup=get_main_keyboard(), parse_mode='Markdown')

async def show_signals(update: Update):
    """Display recent signals."""
    signals = get_recent_signals(10)
    if not signals:
        await update.message.reply_text("📊 لا توجد إشارات حديثة.", reply_markup=get_main_keyboard())
        return
    
    msg = "📊 **آخر الإشارات**\n\n"
    for s in signals[:8]:
        status_emoji = {'PENDING': '⏳', 'ACTIVE': '🟢', 'TP_HIT': '🎯', 'SL_HIT': '🛑'}.get(s['signal_status'], '❓')
        msg += (f"{status_emoji} **{s['symbol']}** ({s['timeframe']})\n"
                f"  دخول: {s['entry_price']:.4f} | وقف: {s['stop_loss']:.4f}\n"
                f"  هدف: {s['take_profit1']:.4f} | {s['strategy']}\n\n")
    
    await update.message.reply_text(msg, reply_markup=get_main_keyboard(), parse_mode='Markdown')

async def show_active_trades(update: Update):
    """Display currently active/monitored trades."""
    trades = db_get_active_signals()
    if not trades:
        await update.message.reply_text("📈 لا توجد صفقات نشطة حاليًا.", reply_markup=get_main_keyboard())
        return
    
    msg = "📈 **الصفقات النشطة**\n\n"
    for t in trades:
        msg += (f"🔵 **{t['symbol']}** ({t['timeframe']})\n"
                f"  دخول: {t['entry_price']:.4f}\n"
                f"  🛑 SL: {t['stop_loss']:.4f}\n"
                f"  🎯 TP: {t['take_profit1']:.4f}\n"
                f"  ⚖️ الحجم: {t['position_size']:.4f}\n\n")
    
    await update.message.reply_text(msg, reply_markup=get_main_keyboard(), parse_mode='Markdown')

async def show_results(update: Update):
    """Display trade results history."""
    results = get_recent_results(10)
    if not results:
        await update.message.reply_text("📉 لا توجد نتائج بعد.", reply_markup=get_main_keyboard())
        return
    
    msg = "📉 **نتائج الصفقات**\n\n"
    total_pnl = 0
    wins = 0
    for r in results:
        emoji = "🟢" if r['profit_pct'] >= 0 else "🔴"
        msg += (f"{emoji} **{r['symbol']}** | {r['result']}\n"
                f"  ربح: {r['profit_pct']:+.2f}% (${r['profit_usd']:+.2f})\n\n")
        total_pnl += r['profit_usd']
        if r['profit_pct'] > 0:
            wins += 1
    
    win_rate = (wins / len(results) * 100) if results else 0
    msg += f"📊 Win Rate: {win_rate:.0f}% | Total PnL: ${total_pnl:+.2f}"
    
    await update.message.reply_text(msg, reply_markup=get_main_keyboard(), parse_mode='Markdown')

async def show_logs(update: Update):
    """Display recent system logs."""
    logs = get_recent_logs(15)
    if not logs:
        await update.message.reply_text("📜 لا توجد سجلات بعد.", reply_markup=get_main_keyboard())
        return
    
    msg = "📜 **آخر السجلات**\n\n"
    for l in logs:
        ts = l['timestamp'].strftime('%H:%M:%S') if hasattr(l['timestamp'], 'strftime') else str(l['timestamp'])[11:19]
        msg += f"`[{ts}]` {l['level']} {l['component']} — {l['message'][:100]}\n"
    
    if len(msg) > 3500:
        msg = msg[:3500] + "\n... (المزيد في قاعدة البيانات)"
    
    await update.message.reply_text(msg, reply_markup=get_main_keyboard(), parse_mode='Markdown')

# === Delete Coin Flow ===
async def show_delete_menu(update: Update):
    """Show coins available for deletion."""
    coins = get_active_coins()
    if not coins:
        await update.message.reply_text("لا توجد عملات للحذف.", reply_markup=get_main_keyboard())
        return
    
    keyboard = [[KeyboardButton(f"حذف {c['symbol']}")] for c in coins]
    keyboard.append([KeyboardButton("رجوع")])
    await update.message.reply_text("اختر العملة المراد حذفها:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

# === Add Coin Conversation ===
async def add_coin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start add coin flow - ask for symbol."""
    await update.message.reply_text(
        "➕ **إضافة عملة جديدة**\n\n"
        "أرسل رمز العملة (مثال: BTCUSDT):",
        parse_mode='Markdown'
    )
    return SYMBOL

async def add_coin_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive symbol, ask for timeframes."""
    symbol = update.message.text.strip().upper()
    context.user_data['new_coin'] = {'symbol': symbol}
    
    tf_list = ', '.join(TIMEFRAMES[:6]) + ', ...'
    await update.message.reply_text(
        f"✅ الرمز: **{symbol}**\n\n"
        f"اختر الأطر الزمنية (مفصولة بفواصل):\n"
        f"المتاحة: {tf_list}\n\n"
        f"مثال: `15m, 1h, 4h`",
        parse_mode='Markdown'
    )
    return TIMEFRAMES

async def add_coin_timeframes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive timeframes, ask for capital percent."""
    tfs = [t.strip() for t in update.message.text.split(',')]
    # Validate
    valid_tfs = [t for t in tfs if t in TIMEFRAMES]
    if not valid_tfs:
        await update.message.reply_text("❌ أطر غير صالحة. حاول مجددًا:")
        return TIMEFRAMES
    
    context.user_data['new_coin']['timeframes'] = valid_tfs
    
    await update.message.reply_text(
        f"✅ الأطر: {', '.join(valid_tfs)}\n\n"
        f"أدخل نسبة رأس المال (مثال: 30):"
    )
    return CAPITAL

async def add_coin_capital(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive capital, ask for risk percent."""
    try:
        capital = float(update.message.text)
        if capital <= 0 or capital > 100:
            raise ValueError
    except:
        await update.message.reply_text("❌ أدخل رقمًا بين 1 و 100:")
        return CAPITAL
    
    context.user_data['new_coin']['capital_percent'] = capital
    
    await update.message.reply_text(
        f"✅ رأس المال: {capital}%\n\n"
        f"أدخل نسبة المخاطرة (مثال: 2):"
    )
    return RISK

async def add_coin_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive risk, confirm and save."""
    try:
        risk = float(update.message.text)
        if risk <= 0 or risk > 20:
            raise ValueError
    except:
        await update.message.reply_text("❌ أدخل رقمًا بين 0.1 و 20:")
        return RISK
    
    coin = context.user_data['new_coin']
    coin['risk_percent'] = risk
    
    # Save to DB
    add_coin(coin['symbol'], coin['timeframes'], coin['capital_percent'], coin['risk_percent'])
    
    await update.message.reply_text(
        f"✅ **تمت إضافة العملة بنجاح!**\n\n"
        f"🔹 العملة: **{coin['symbol']}**\n"
        f"🔹 الأطر: {', '.join(coin['timeframes'])}\n"
        f"🔹 رأس المال: {coin['capital_percent']}%\n"
        f"🔹 المخاطرة: {coin['risk_percent']}%\n\n"
        f"سيبدأ النظام بتحليل {coin['symbol']} على الأطر المختارة.",
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تم الإلغاء.", reply_markup=get_main_keyboard())
    return ConversationHandler.END

def build_application() -> Application:
    """Build and return the Telegram Application (does not start it)."""
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add coin conversation
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ إضافة عملة$"), add_coin_start)],
        states={
            SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_coin_symbol)],
            TIMEFRAMES: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_coin_timeframes)],
            CAPITAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_coin_capital)],
            RISK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_coin_risk)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    app.add_handler(CommandHandler('start', start))
    app.add_handler(conv_handler)
    
    # Handle delete coin
    app.add_handler(MessageHandler(filters.Regex(r"^حذف .+$"), handle_delete))
    app.add_handler(MessageHandler(filters.Regex("^رجوع$"), lambda u, c: start(u, c)))
    
    # Handle all other menu button presses
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    return app


def run_bot():
    """Legacy entry point — prefer build_application() + async lifecycle."""
    app = build_application()
    app.run_polling()

async def handle_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle delete coin action."""
    symbol = update.message.text.replace("حذف ", "").strip()
    remove_coin(symbol)
    await update.message.reply_text(f"🗑️ تم حذف **{symbol}**", reply_markup=get_main_keyboard(), parse_mode='Markdown')
