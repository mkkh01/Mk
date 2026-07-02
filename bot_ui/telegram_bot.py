import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from database import db
from data_layer import fetch_data
from data_layer.cache import delete
from execution_engine import trade_tracker
from monitoring import performance, alerts
from risk_management import risk_manager

logger = logging.getLogger("telegram_bot")

# ─── Admin Chat IDs (will be set from config) ───
ADMIN_IDS = []

def is_admin(chat_id):
    """Check if user is admin."""
    from config import ADMIN_CHAT_IDS
    if not ADMIN_CHAT_IDS and not ADMIN_IDS:
        return True  # Allow all during setup
    return chat_id in ADMIN_IDS or chat_id in ADMIN_IDS

# ─── Keyboard Builders ───
def main_menu_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 الأسعار الحية", callback_data="menu_prices"),
            InlineKeyboardButton("📋 الصفقات المفتوحة", callback_data="menu_open_trades"),
        ],
        [
            InlineKeyboardButton("📈 الأداء", callback_data="menu_performance"),
            InlineKeyboardButton("📜 سجل الصفقات", callback_data="menu_history"),
        ],
        [
            InlineKeyboardButton("➕ إضافة عملة", callback_data="menu_add_asset"),
            InlineKeyboardButton("✏️ تعديل عملة", callback_data="menu_edit_asset"),
        ],
        [
            InlineKeyboardButton("🗑️ حذف عملة", callback_data="menu_delete_asset"),
            InlineKeyboardButton("💼 العملات المسجلة", callback_data="menu_list_assets"),
        ],
        [
            InlineKeyboardButton("🟢 تشغيل التداول", callback_data="action_start"),
            InlineKeyboardButton("🔴 إيقاف التداول", callback_data="action_stop"),
        ],
        [
            InlineKeyboardButton("🛑 Kill Switch", callback_data="action_kill"),
            InlineKeyboardButton("🔧 إعادة تعيين القواطع", callback_data="action_reset_cb"),
        ],
    ])

def back_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="menu_main")]
    ])

def confirm_kb(action, data=""):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تأكيد", callback_data=f"confirm_{action}_{data}"),
            InlineKeyboardButton("❌ إلغاء", callback_data="menu_main"),
        ]
    ])

def timeframe_kb():
    """Build timeframe selection keyboard."""
    tfs = ["1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","3d","1w"]
    buttons = []
    row = []
    for tf in tfs:
        row.append(InlineKeyboardButton(tf, callback_data=f"tf_{tf}"))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="menu_add_asset")])
    return InlineKeyboardMarkup(buttons)

# ─── Pending Actions ───
pending_actions = {}  # chat_id -> {"action": str, "data": dict}

# ─── Handlers ───
async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    chat_id = update.effective_chat.id
    ADMIN_IDS.append(chat_id)
    
    await update.message.reply_text(
        "🤖 *بوت التداول الذكي — Mk*\n\n"
        "بوت تحليل فني وتوليد إشارات تداول آلية مع تتبع ورقي لكل صفقة.\n\n"
        "اختر من القائمة:",
        parse_mode="Markdown",
        reply_markup=main_menu_kb()
    )

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle all inline button presses."""
    query = update.callback_query
    chat_id = query.message.chat_id
    await query.answer()
    
    data = query.data
    
    # ── Main Menu ──
    if data == "menu_main":
        await query.edit_message_text(
            "🏠 *القائمة الرئيسية*\nاختر:",
            parse_mode="Markdown",
            reply_markup=main_menu_kb()
        )
    
    # ── Live Prices ──
    elif data == "menu_prices":
        assets = db.query("SELECT symbol, timeframes FROM assets WHERE is_active = TRUE")
        if not assets:
            await query.edit_message_text(
                "❌ لا توجد عملات مسجلة.\nاضغط ➕ إضافة عملة",
                reply_markup=back_kb()
            )
            return
        
        lines = ["📊 *الأسعار الحية*\n"]
        for a in assets:
            price = fetch_data.fetch_current_price(a["symbol"])
            ticker = fetch_data.fetch_24h_ticker(a["symbol"])
            if price:
                change = f"{ticker['change_pct']:+.2f}%" if ticker else "?"
                arrow = "📈" if ticker and ticker["change_pct"] > 0 else "📉" if ticker and ticker["change_pct"] < 0 else "➡️"
                lines.append(f"{arrow} `{a['symbol']}` — `{price}` ({change})")
            else:
                lines.append(f"⚠️ `{a['symbol']}` — فشل جلب السعر")
        
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=back_kb()
        )
    
    # ── Open Trades ──
    elif data == "menu_open_trades":
        trades = trade_tracker.get_open_trades()
        if not trades:
            await query.edit_message_text(
                "📭 لا توجد صفقات مفتوحة حالياً.",
                reply_markup=back_kb()
            )
            return
        
        lines = ["📋 *الصفقات المفتوحة*\n"]
        for t in trades[:10]:
            direction = "🟢 شراء" if t["direction"] == "BUY" else "🔴 بيع"
            current = t["current_price"] or t["entry_price"]
            if t["direction"] == "BUY":
                pnl = ((current - t["entry_price"]) / t["entry_price"]) * 100
            else:
                pnl = ((t["entry_price"] - current) / t["entry_price"]) * 100
            pnl_emoji = "📈" if pnl > 0 else "📉"
            
            lines.append(
                f"#{t['id']} {direction} `{t['symbol']}` ({t['timeframe']})\n"
                f"   دخول: `{t['entry_price']}` | الآن: `{current}`\n"
                f"   {pnl_emoji} PnL: `{pnl:+.2f}%` | SL: `{t['stop_loss']}` | TP: `{t['take_profit']}`\n"
            )
        
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=back_kb()
        )
    
    # ── Performance ──
    elif data == "menu_performance":
        stats = performance.get_dashboard_stats()
        state = stats["state"]
        today = stats["today"]
        all_time = stats["all_time"]
        
        bot_status = "🟢 يعمل" if state and state["bot_running"] else "🔴 متوقف"
        kb_status = "🛑 مفعل" if state and state["circuit_breaker_active"] else "✅ معطل"
        
        text = (
            f"📈 *لوحة الأداء*\n\n"
            f"🤖 حالة البوت: {bot_status}\n"
            f"⚡ قاطع الدائرة: {kb_status}\n"
            f"━━━━━━━━━━━━━━━━━\n"
        )
        
        if today:
            text += (
                f"📅 *اليوم*\n"
                f"   صفقات: `{today['total_trades'] or 0}` | "
                f"فوز: `{today['wins'] or 0}` | خسارة: `{today['losses'] or 0}`\n"
                f"   نسبة الفوز: `{today['win_rate'] or 0:.1f}%`\n"
                f"   PnL: `{today['pnl_pct'] or 0:+.2f}%`\n"
                f"━━━━━━━━━━━━━━━━━\n"
            )
        
        if all_time and all_time["total_trades"]:
            wr = (all_time["wins"] / all_time["total_trades"]) * 100 if all_time["total_trades"] > 0 else 0
            text += (
                f"📊 *إجمالي*\n"
                f"   صفقات: `{all_time['total_trades']}`\n"
                f"   فوز/خسارة: `{all_time['wins']}/{all_time['losses']}`\n"
                f"   نسبة الفوز: `{wr:.1f}%`\n"
                f"   إجمالي PnL: `{all_time['total_pnl']:+.2f}%`\n"
                f"   أفضل صفقة: `{all_time['best_trade']:+.2f}%`\n"
                f"   أسوأ صفقة: `{all_time['worst_trade']:+.2f}%`\n"
            )
            
            if state:
                text += f"\n📉 أقصى تراجع: `{state['max_drawdown_pct']:.2f}%`\n"
        
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=back_kb())
    
    # ── Trade History ──
    elif data == "menu_history":
        trades = trade_tracker.get_recent_trades(10)
        if not trades:
            await query.edit_message_text("📭 لا يوجد سجل صفقات بعد.", reply_markup=back_kb())
            return
        
        lines = ["📜 *آخر الصفقات*\n"]
        for t in trades:
            emoji = "✅" if t["pnl_pct"] > 0 else "❌"
            reason = "🎯" if t["exit_reason"] == "TP" else "🛑"
            direction = "🟢" if t["direction"] == "BUY" else "🔴"
            lines.append(
                f"{emoji} #{t['id']} {direction} `{t['symbol']}` | "
                f"{reason} | PnL: `{t['pnl_pct']:+.2f}%`"
            )
        
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=back_kb())
    
    # ── List Assets ──
    elif data == "menu_list_assets":
        assets = db.query("SELECT * FROM assets ORDER BY symbol")
        if not assets:
            await query.edit_message_text("📭 لا توجد عملات مسجلة.", reply_markup=back_kb())
            return
        
        lines = ["💼 *العملات المسجلة*\n"]
        for a in assets:
            status = "🟢" if a["is_active"] else "🔴"
            tfs = ", ".join(a["timeframes"])
            lines.append(
                f"{status} `{a['symbol']}` | أطر: `{tfs}` | "
                f"رأس مال: `{a['capital_pct']}%`"
            )
        
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=back_kb())
    
    # ── Add Asset (step 1: ask for symbol) ──
    elif data == "menu_add_asset":
        pending_actions[chat_id] = {"action": "add_symbol"}
        await query.edit_message_text(
            "➕ *إضافة عملة جديدة*\n\n"
            "أرسل رمز العملة (مثلاً: BTCUSDT)",
            parse_mode="Markdown",
            reply_markup=back_kb()
        )
    
    # ── Edit Asset (step 1: ask for symbol) ──
    elif data == "menu_edit_asset":
        pending_actions[chat_id] = {"action": "edit_symbol"}
        await query.edit_message_text(
            "✏️ *تعديل عملة*\n\n"
            "أرسل رمز العملة التي تريد تعديلها:",
            parse_mode="Markdown",
            reply_markup=back_kb()
        )
    
    # ── Delete Asset (step 1: ask for symbol) ──
    elif data == "menu_delete_asset":
        pending_actions[chat_id] = {"action": "delete_symbol"}
        await query.edit_message_text(
            "🗑️ *حذف عملة*\n\n"
            "أرسل رمز العملة التي تريد حذفها:",
            parse_mode="Markdown",
            reply_markup=back_kb()
        )
    
    # ── Start Trading ──
    elif data == "action_start":
        db.query(
            "UPDATE system_state SET bot_running = TRUE, kill_switch_active = FALSE WHERE id = 1",
            fetch=False
        )
        await query.edit_message_text(
            "🟢 *تم تشغيل البوت!*\n\nسيتولد الإشارات تلقائياً ويتتبع كل صفقة.",
            parse_mode="Markdown",
            reply_markup=back_kb()
        )
    
    # ── Stop Trading ──
    elif data == "action_stop":
        db.query("UPDATE system_state SET bot_running = FALSE WHERE id = 1", fetch=False)
        await query.edit_message_text(
            "🔴 *تم إيقاف البوت.*\n\nالصفقات المفتوحة ستبقى متتبعة.",
            parse_mode="Markdown",
            reply_markup=back_kb()
        )
    
    # ── Kill Switch ──
    elif data == "action_kill":
        await query.edit_message_text(
            "🛑 *Kill Switch*\n\nهل أنت متأكد من إيقاف كل التداولات؟",
            parse_mode="Markdown",
            reply_markup=confirm_kb("kill")
        )
    
    elif data == "confirm_kill":
        db.query(
            "UPDATE system_state SET bot_running = FALSE, kill_switch_active = TRUE WHERE id = 1",
            fetch=False
        )
        await query.edit_message_text(
            "🛑 *تم تفعيل Kill Switch!*\n\nكل التداولات متوقفة.",
            parse_mode="Markdown",
            reply_markup=back_kb()
        )
    
    # ── Reset Circuit Breaker ──
    elif data == "action_reset_cb":
        risk_manager.reset_circuit_breaker()
        await query.edit_message_text(
            "✅ *تم إعادة تعيين قاطع الدائرة.*",
            parse_mode="Markdown",
            reply_markup=back_kb()
        )
    
    # ── Confirm Delete ──
    elif data.startswith("confirm_delete_"):
        symbol = data.replace("confirm_delete_", "")
        db.query("DELETE FROM assets WHERE symbol = %s", (symbol,), fetch=False)
        delete(f"klines:{symbol}")
        await query.edit_message_text(
            f"✅ تم حذف `{symbol}`.",
            parse_mode="Markdown",
            reply_markup=back_kb()
        )
    
    # ── Timeframe selection (during add) ──
    elif data.startswith("tf_"):
        tf = data.replace("tf_", "")
        pending = pending_actions.get(chat_id, {})
        if pending.get("action") == "add_timeframes":
            selected = pending.get("timeframes", [])
            if tf in selected:
                selected.remove(tf)
            else:
                selected.append(tf)
            pending["timeframes"] = selected
            pending_actions[chat_id] = pending
            
            # Rebuild keyboard with checkmarks
            tfs = ["1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","3d","1w"]
            buttons = []
            row = []
            for t in tfs:
                mark = "✅ " if t in selected else ""
                row.append(InlineKeyboardButton(f"{mark}{t}", callback_data=f"tf_{t}"))
                if len(row) == 4:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            buttons.append([InlineKeyboardButton("✅ تم", callback_data="tf_done")])
            buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="menu_main")])
            
            await query.edit_message_text(
                f"⏱️ اختر الأطر الزمنية لـ `{pending['symbol']}`:\n\n"
                f"المختارة: {', '.join(selected) if selected else 'لا يوجد'}\n\n"
                f"اضغط على الإطار لتحديده/إلغائه، ثم اضغط ✅ تم",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
    
    elif data == "tf_done":
        pending = pending_actions.get(chat_id, {})
        if pending.get("action") == "add_timeframes":
            tfs = pending.get("timeframes", ["5m", "15m", "1h", "4h"])
            if not tfs:
                tfs = ["5m", "15m", "1h", "4h"]
            
            symbol = pending["symbol"]
            db.query(
                """INSERT INTO assets (symbol, timeframes, capital_pct, is_active) 
                   VALUES (%s, %s, 10.0, TRUE)
                   ON CONFLICT (symbol) DO UPDATE SET timeframes = %s, is_active = TRUE""",
                (symbol, tfs, tfs),
                fetch=False
            )
            
            pending_actions.pop(chat_id, None)
            await query.edit_message_text(
                f"✅ تم إضافة `{symbol}`\n\n"
                f"الأطر الزمنية: {', '.join(tfs)}\n"
                f"رأس المال: 10%\n\n"
                f"يمكنك تعديلها من ✏️ تعديل عملة",
                parse_mode="Markdown",
                reply_markup=back_kb()
            )

async def message_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle text messages (for add/edit/delete flows)."""
    chat_id = update.effective_chat.id
    text = update.message.text.strip().upper()
    pending = pending_actions.get(chat_id)
    
    if not pending:
        await update.message.reply_text("استخدم الأزرار للتنقل.", reply_markup=main_menu_kb())
        return
    
    action = pending["action"]
    
    # ── Add Asset: Step 1 - Symbol ──
    if action == "add_symbol":
        symbol = text
        if not fetch_data.validate_symbol(symbol):
            await update.message.reply_text(
                f"❌ الرمز `{symbol}` غير موجود على بينانس.",
                parse_mode="Markdown"
            )
            return
        
        pending["action"] = "add_timeframes"
        pending["symbol"] = symbol
        pending["timeframes"] = ["5m", "15m", "1h", "4h"]
        
        await update.message.reply_text(
            f"✅ `{symbol}` موجود!\n\nاختر الأطر الزمنية:",
            parse_mode="Markdown",
            reply_markup=timeframe_kb()
        )
    
    # ── Edit Asset: Show current and ask what to edit ──
    elif action == "edit_symbol":
        symbol = text
        asset = db.query_one("SELECT * FROM assets WHERE symbol = %s", (symbol,))
        if not asset:
            await update.message.reply_text(
                f"❌ `{symbol}` غير مسجل.\nأضفه أولاً.",
                parse_mode="Markdown", reply_markup=back_kb()
            )
            pending_actions.pop(chat_id, None)
            return
        
        tfs = ", ".join(asset["timeframes"])
        await update.message.reply_text(
            f"✏️ *تعديل `{symbol}`*\n\n"
            f"الأطر الحالية: `{tfs}`\n"
            f"رأس المال: `{asset['capital_pct']}%`\n\n"
            f"أرسل القيمة الجديدة:\n"
            f"• `رأس مال: 20` — لتغيير نسبة رأس المال\n"
            f"• `أطر: 5m,15m,1h` — لتغيير الأطر\n"
            f"• `تفعيل` أو `تعطيل`",
            parse_mode="Markdown",
            reply_markup=back_kb()
        )
        pending["action"] = "edit_field"
        pending["symbol"] = symbol
    
    # ── Edit Asset: Apply changes ──
    elif action == "edit_field":
        symbol = pending["symbol"]
        msg = text.lower()
        
        if msg.startswith("رأس مال"):
            try:
                pct = float(msg.split(":")[1].strip().replace("%", ""))
                pct = max(1, min(100, pct))
                db.query(
                    "UPDATE assets SET capital_pct = %s WHERE symbol = %s",
                    (pct, symbol), fetch=False
                )
                await update.message.reply_text(
                    f"✅ تم تحديث رأس مال `{symbol}` إلى `{pct}%`",
                    parse_mode="Markdown", reply_markup=back_kb()
                )
            except:
                await update.message.reply_text("❌ صيغة خاطئة. مثال: `رأس مال: 20`", parse_mode="Markdown")
        
        elif msg.startswith("أطر"):
            try:
                new_tfs = [t.strip() for t in msg.split(":")[1].strip().split(",")]
                new_tfs = [t for t in new_tfs if t in ["1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","3d","1w"]]
                if new_tfs:
                    db.query(
                        "UPDATE assets SET timeframes = %s WHERE symbol = %s",
                        (new_tfs, symbol), fetch=False
                    )
                    await update.message.reply_text(
                        f"✅ تم تحديث أطر `{symbol}`\n{', '.join(new_tfs)}",
                        parse_mode="Markdown", reply_markup=back_kb()
                    )
            except:
                await update.message.reply_text("❌ صيغة خاطئة. مثال: `أطر: 5m,15m,1h`", parse_mode="Markdown")
        
        elif "تفعيل" in msg:
            db.query("UPDATE assets SET is_active = TRUE WHERE symbol = %s", (symbol,), fetch=False)
            await update.message.reply_text(f"✅ تم تفعيل `{symbol}`", parse_mode="Markdown", reply_markup=back_kb())
        
        elif "تعطيل" in msg:
            db.query("UPDATE assets SET is_active = FALSE WHERE symbol = %s", (symbol,), fetch=False)
            await update.message.reply_text(f"🔴 تم تعطيل `{symbol}`", parse_mode="Markdown", reply_markup=back_kb())
        
        else:
            await update.message.reply_text("❌ أمر غير معروف.", reply_markup=back_kb())
        
        pending_actions.pop(chat_id, None)
    
    # ── Delete Asset ──
    elif action == "delete_symbol":
        symbol = text
        asset = db.query_one("SELECT * FROM assets WHERE symbol = %s", (symbol,))
        if not asset:
            await update.message.reply_text(
                f"❌ `{symbol}` غير مسجل.",
                parse_mode="Markdown", reply_markup=back_kb()
            )
            pending_actions.pop(chat_id, None)
            return
        
        await update.message.reply_text(
            f"🗑️ تأكيد حذف `{symbol}`؟",
            parse_mode="Markdown",
            reply_markup=confirm_kb("delete", symbol)
        )
        pending["action"] = "delete_confirm"

# ─── Build Application ───
def build_bot():
    """Create and configure the Telegram bot application."""
    import config
    
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
        return None
    
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    
    # Register handlers
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    return app