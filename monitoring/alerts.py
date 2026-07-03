import logging
from datetime import datetime

logger = logging.getLogger("alerts")

_bot_instance = None

def set_bot(bot):
    """Set the bot instance for sending messages."""
    global _bot_instance
    _bot_instance = bot

async def send_signal_notification(chat_id, signal, symbol, timeframe, regime):
    """Send a new signal notification."""
    if not _bot_instance:
        return
    
    direction_emoji = "🟢" if signal["signal"] == "BUY" else "🔴"
    direction_ar = "شراء" if signal["signal"] == "BUY" else "بيع"
    
    text = (
        f"📋 *إشارة جديدة — {signal['strategy']}*\n\n"
        f"{direction_emoji} *{direction_ar}* `{symbol}`\n"
        f"📊 الإطار: `{timeframe}`\n"
        f"📈 سعر الدخول: `{signal['entry_price']}`\n"
        f"🛑 وقف الخسارة: `{signal['stop_loss']}`\n"
        f"🎯 هدف الربح: `{signal['take_profit']}`\n"
        f"📏 نسبة R:R: `{abs(signal['take_profit'] - signal['entry_price']) / abs(signal['entry_price'] - signal['stop_loss']):.1f}`\n"
        f"💪 الثقة: `{signal['confidence']}%`\n"
        f"🌐 حالة السوق: `{regime['regime']}`\n"
        f"📝 السبب: {signal['reason']}\n"
    )
    
    try:
        await _bot_instance.send_message(
            chat_id=chat_id, text=text, parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to send signal notification: {e}")

async def send_trade_closed(chat_id, trade):
    """Send trade closure notification."""
    if not _bot_instance:
        return
    
    if trade["reason"] == "TP":
        emoji = "🎯"
        status = "هدف الربح"
        color = "✅"
    else:
        emoji = "🛑"
        status = "وقف الخسارة"
        color = "❌"
    
    pnl = trade["pnl_pct"]
    pnl_emoji = "📈" if pnl > 0 else "📉"
    
    text = (
        f"{emoji} *صفقة مغلقة — {status}*\n\n"
        f"{'🟢' if trade['direction'] == 'BUY' else '🔴'} `{trade['symbol']}` | "
        f"{'شراء' if trade['direction'] == 'BUY' else 'بيع'}\n"
        f"📊 الاستراتيجية: `{trade['strategy']}`\n\n"
        f"🏠 الدخول: `{trade['entry']}`\n"
        f"🔚 الخروج: `{trade['exit']}`\n\n"
        f"{pnl_emoji} *النتيجة: {pnl:+.2f}%*\n"
        f"{color} {'ربح' if pnl > 0 else 'خسارة'}"
    )
    
    try:
        await _bot_instance.send_message(
            chat_id=chat_id, text=text, parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to send trade close notification: {e}")

async def send_alert(chat_id, title, message):
    """Send a general alert."""
    if not _bot_instance:
        return
    text = f"⚠️ *{title}*\n\n{message}"
    try:
        await _bot_instance.send_message(
            chat_id=chat_id, text=text, parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to send alert: {e}")