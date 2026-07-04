"""
CTM Bot - Telegram Interface
10-button menu with conversations for adding coins.
Uses centralized state from utils/state.
"""
import asyncio
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters
)
from config import TELEGRAM_BOT_TOKEN, TIMEFRAMES
from utils.logger import get_buffer_logs
from utils.state import pause_system, resume_system, is_system_active, get_state as _sys_state
from db.supabase_client import (
    get_active_coins, add_coin, remove_coin,
    get_recent_signals, get_active_signals as db_get_active_signals,
    get_recent_results
)

(SYMBOL, TIMEFRAMES_STATE, CAPITAL, RISK) = range(4)

MAIN_KEYBOARD = [
    ["💰 أسعار حية", "➕ إضافة عملة"],
    ["📋 عملاتي", "🗑️ حذف عملة"],
    ["📊 الإشارات", "📈 الصفقات"],
    ["📉 النتائج", "📜 السجلات"],
    ["⏸️ إيقاف", "▶️ تشغيل"],
]


def get_main_keyboard():
    return ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils.price_cache import get_all_cached_prices as _cp
    from data.binance_api import get_api_status
    s = _sys_state()
    prices = _cp()
    api = get_api_status()
    active_status = "🟢 نشط" if is_system_active() else "⏸️ متوقف"
    api_status = "🟢 متصل" if api.get('online') else "🔴 محجوب"
    await update.message.reply_text(
        f"🫡 **CTM Bot v2.2**\n\n"
        f"⚡ الحالة: {active_status}\n"
        f"📡 Binance API: {api_status}\n"
        f"🛡️ قاطع: {'🔴 نشط' if s.get('circuit_breaker') else '🟢 معطل'}\n"
        f"📊 الدورات: {s['cycles']}\n"
        f"⏱️ آخر دورة: منذ {s['last_cycle_ago']}s\n"
        f"🪙 عملات: {s['coins']}\n"
        f"💵 أسعار مخزنة: {len(prices)}\n"
        f"⚠️ أخطاء: {s['errors']}\n"
        f"🔄 فشل متتالي API: {api.get('consecutive_failures', 0)}\n"
        f"🔗 API Base: {api.get('working_base', 'N/A')[-25:]}\n\n"
        f"اختر من القائمة:",
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )


async def test_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import psycopg
    from config import SUPABASE_DB_URL
    results = []
    try:
        conn = psycopg.connect(SUPABASE_DB_URL)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM logs")
        log_count = cur.fetchone()[0]
        results.append(f"📜 سجلات: {log_count}")
        cur.execute("SELECT COUNT(*) FROM tracked_coins")
        coin_count = cur.fetchone()[0]
        results.append(f"🪙 عملات: {coin_count}")
        cur.execute("SELECT COUNT(*) FROM signals")
        sig_count = cur.fetchone()[0]
        results.append(f"📊 إشارات: {sig_count}")
        cur.close()
        conn.close()
        results.append("✅ اتصال DB ناجح")
    except Exception as e:
        results.append(f"❌ DB: {e}")

    # Add risk summary
    try:
        from utils.risk_manager import get_portfolio_summary
        ps = get_portfolio_summary()
        results.append(f"\n📊 **ملخص المخاطر:**")
        results.append(f"🪙 عملات: {ps['coins_count']}")
        results.append(f"💵 رأس المال: ${ps['total_capital']:.0f}")
        results.append(f"📈 صفقات نشطة: {ps['active_trades']}")
        results.append(f"⚠️ تعرض: {ps['exposure_pct']}%")
        daily = ps.get('daily_pnl')
        if daily is not None:
            results.append(f"📅 ربح يومي: ${daily:+.2f}")
        results.append(f"🔻 خسائر متتالية: {ps['consecutive_losses']}")
        results.append(f"🎯 Win Rate: {ps['win_rate']}%")
    except Exception as e:
        results.append(f"⚠️ ملخص المخاطر: {e}")

    await update.message.reply_text("\n".join(results))


async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """System health check."""
    from data.binance_api import get_api_status
    st = _sys_state()
    api = get_api_status()
    active = "🟢 نشط" if is_system_active() else "⏸️ متوقف"
    api_ok = "🟢 متصل" if api.get('online') else "🔴 محجوب"
    cb = "🔴 مفعل" if st.get('circuit_breaker') else "🟢 معطل"
    reason = st.get('circuit_breaker_reason', '')
    await update.message.reply_text(
        f"🏥 **حالة النظام**\n\n"
        f"⚡ التشغيل: {active}\n"
        f"📡 Binance API: {api_ok}\n"
        f"🔄 فشل API متتالي: {api.get('consecutive_failures', 0)}\n"
        f"🔗 API Base: {api.get('working_base', 'N/A')[-30:]}\n"
        f"📊 طلبات: {api.get('total_requests', 0)} | فشل: {api.get('total_failures', 0)}\n"
        f"🛡️ قاطع الدائرة: {cb}\n"
        f"{'  ↳ ' + reason if reason else ''}\n"
        f"📊 الدورات: {st['cycles']}\n"
        f"🪙 عملات: {st['coins']}\n"
        f"⚠️ أخطاء: {st['errors']}\n"
        f"⏱️ آخر دورة: منذ {st['last_cycle_ago']}s\n"
        f"⏱️ مدة الدورة: {st['duration']}s\n"
        f"\nالإصدار: v2.2",
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
        pause_system()
        await update.message.reply_text("⏸️ تم إيقاف توليد الإشارات وتحليل السوق.",
                                        reply_markup=get_main_keyboard())
    elif text == "▶️ تشغيل":
        resume_system()
        await update.message.reply_text("▶️ تم تشغيل النظام — جاري استئناف التحليل.",
                                        reply_markup=get_main_keyboard())
    elif text == "رجوع":
        await update.message.reply_text("القائمة الرئيسية:", reply_markup=get_main_keyboard())


async def show_live_prices(update: Update):
    from utils.price_cache import get_price
    coins = get_active_coins()
    if not coins:
        await update.message.reply_text("❌ لا توجد عملات مضافة.", reply_markup=get_main_keyboard())
        return
    msg = "💰 **الأسعار الحية**\n\n"
    for c in coins:
        sym = c['symbol']
        cached = get_price(sym)
        if cached:
            price = cached['price']
            ago = int((__import__('datetime').datetime.now() - cached['updated']).total_seconds())
            msg += f"🟢 **{sym}**: ${price:.4f} _(منذ {ago}s)_\n"
        else:
            msg += f"⏳ **{sym}**: انتظار أول تحليل...\n"
    await update.message.reply_text(msg, reply_markup=get_main_keyboard(), parse_mode='Markdown')


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
    logs = get_buffer_logs(50)
    if not logs:
        await update.message.reply_text("📜 لا توجد سجلات بعد. جاري التحميل...", reply_markup=get_main_keyboard())
        return
    msg = "📜 سجلات النظام (آخر 50)\n\n"
    for l in logs:
        ts = l.get('timestamp', '')
        if hasattr(ts, 'strftime'):
            ts = ts.strftime('%H:%M:%S')
        elif isinstance(ts, str):
            ts = ts[-8:] if len(ts) >= 8 else ts
        m = str(l.get('message', '')).replace('_', r'\_').replace('*', r'\*').replace('`', r'\`')
        first = m.split('\n')[0][:90]
        msg += f"`{ts}` {l.get('level','')} {l.get('component','')} — {first}\n"
    if len(msg) > 3800:
        msg = msg[:3800] + "\n..."
    await update.message.reply_text(msg, reply_markup=get_main_keyboard())


async def show_delete_menu(update: Update):
    coins = get_active_coins()
    if not coins:
        await update.message.reply_text("لا توجد عملات للحذف.", reply_markup=get_main_keyboard())
        return
    keyboard = [[KeyboardButton(f"حذف {c['symbol']}")] for c in coins]
    keyboard.append([KeyboardButton("رجوع")])
    await update.message.reply_text("اختر العملة المراد حذفها:",
                                    reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))


async def handle_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = update.message.text.replace("حذف ", "").strip()
    remove_coin(symbol)
    await update.message.reply_text(f"🗑️ تم حذف **{symbol}**", reply_markup=get_main_keyboard(),
                                    parse_mode='Markdown')


# ── Add Coin Conversation ──

async def add_coin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("➕ **إضافة عملة جديدة**\n\nأرسل رمز العملة (مثال: BTCUSDT):",
                                    parse_mode='Markdown')
    return SYMBOL


async def add_coin_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = update.message.text.strip().upper()
    context.user_data['new_coin'] = {'symbol': symbol}
    await update.message.reply_text(
        f"✅ الرمز: **{symbol}**\n\nاختر الأطر الزمنية (مفصولة بفواصل):\nمثال: `15m, 1h, 4h`",
        parse_mode='Markdown')
    return TIMEFRAMES_STATE


async def add_coin_timeframes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tfs = [t.strip() for t in update.message.text.split(',')]
    valid_tfs = [t for t in tfs if t in TIMEFRAMES]
    if not valid_tfs:
        await update.message.reply_text("❌ أطر غير صالحة. حاول مجددًا:")
        return TIMEFRAMES_STATE
    context.user_data['new_coin']['timeframes'] = valid_tfs
    await update.message.reply_text(
        f"✅ الأطر: {', '.join(valid_tfs)}\n\nأدخل قيمة رأس المال بالـ USDT (مثال: 100):")
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
    await update.message.reply_text(
        f"✅ رأس المال: {capital} USDT\n\nأدخل نسبة المخاطرة (مثال: 2):")
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
    app.add_handler(CommandHandler('test', test_db))
    app.add_handler(CommandHandler('health', health))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.Regex(r"^حذف .+$"), handle_delete))
    app.add_handler(MessageHandler(filters.Regex("^رجوع$"), lambda u, c: start(u, c)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app


def run_webhook(app: Application, url: str, port: int):
    """Run bot with built-in HTTP server — webhook + root page + health."""
    import asyncio, json, threading
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from telegram import Update

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, f, *a): pass
        def _send(self, body, ct, code=200):
            b = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", len(b))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                from utils.state import get_state as _gs
                from data.binance_api import get_api_status as _as
                st, api = _gs(), _as()
                ok = api.get('online', False)
                self._send(
                    '<!DOCTYPE html><html dir=rtl><head><meta charset=utf-8>'
                    '<meta name=viewport content="width=device-width,initial-scale=1">'
                    '<title>CTM Bot v2.3</title>'
                    '<style>body{font-family:system-ui;max-width:600px;margin:40px auto;padding:20px;background:#0d1117;color:#c9d1d9}'
                    'h1{color:#58a6ff}.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin:12px 0}'
                    '.g{color:#3fb950}.r{color:#f85149}.dim{color:#8b949e}td{padding:4px 8px}'
                    '</style></head><body><h1>🫡 CTM Bot v2.3</h1>'
                    '<div class=card><table>'
                    f'<tr><td class=dim>الحالة</td><td class=g>🟢 يعمل</td></tr>'
                    f'<tr><td class=dim>Binance API</td><td class={"g" if ok else "r"}>{"🟢 متصل" if ok else "🔴 محجوب"}</td></tr>'
                    f'<tr><td class=dim>مصدر البيانات</td><td>{api.get("working_base","?")[-30:]}</td></tr>'
                    f'<tr><td class=dim>فشل متتالي</td><td>{api.get("consecutive_failures",0)}</td></tr>'
                    f'<tr><td class=dim>الدورات</td><td>{st["cycles"]}</td></tr>'
                    f'<tr><td class=dim>العملات</td><td>{st["coins"]}</td></tr>'
                    f'<tr><td class=dim>أخطاء</td><td>{st["errors"]}</td></tr>'
                    f'<tr><td class=dim>آخر دورة</td><td>منذ {st["last_cycle_ago"]}s | {st["duration"]}s</td></tr>'
                    '</table></div>'
                    '<p class=dim style=text-align:center>Telegram: @CTM_CopyTrading_bot | Health: /health</p>'
                    '</body></html>',
                    "text/html; charset=utf-8")
            elif self.path == "/health":
                from utils.state import get_state as _gs
                st = _gs()
                self._send({"status":"ok","v":"2.3","cycles":st["cycles"],"coins":st["coins"]}, "application/json")
            else:
                self.send_response(404); self.end_headers()

        def do_POST(self):
            if self.path == "/webhook":
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(length)
                    update = Update.de_json(json.loads(body), app.bot)
                    app.update_queue.put_nowait(update)
                except Exception:
                    pass
                self.send_response(200); self.end_headers()
            else:
                self.send_response(404); self.end_headers()

    server = HTTPServer(("0.0.0.0", port), Handler)

    async def start():
        await app.initialize()
        await app.bot.set_webhook(url=url, drop_pending_updates=True)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start())

    async def process():
        while True:
            try:
                update = await app.update_queue.get()
                await app.process_update(update)
            except Exception:
                pass
    loop.create_task(process())

    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"🌐 http://0.0.0.0:{port}/ | /health | /webhook")
    loop.run_forever()
