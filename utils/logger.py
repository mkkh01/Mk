"""
CTM Bot - Logging System
Logs everything to console + DB. Falls back to memory buffer if DB fails.
"""
import json
import time
from datetime import datetime
import psycopg

DB_URL = None
_LOGS_BUFFER = []  # fallback if DB fails

def init_logger(db_url: str):
    global DB_URL
    DB_URL = db_url

def _log(level: str, component: str, message: str, details: dict = None):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if '\n' in message:
        # Multi-line report — print header + body separately
        print(f"[{timestamp}] {level} {component}")
        print(message)
    else:
        log_entry = f"[{timestamp}] {level} {component} — {message}"
        print(log_entry)

    # Always keep in memory buffer
    _LOGS_BUFFER.append({
        'timestamp': datetime.now(), 'level': level,
        'component': component, 'message': message
    })
    if len(_LOGS_BUFFER) > 200:
        _LOGS_BUFFER.pop(0)

    if details is None:
        details = {}

    if DB_URL:
        try:
            conn = psycopg.connect(DB_URL)
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO logs (timestamp, level, component, message, details)
                   VALUES (%s, %s, %s, %s, %s)""",
                (datetime.now(), level, component, message, json.dumps(details))
            )
            conn.commit()
            cur.close()
            conn.close()
            return log_entry
        except Exception as e:
            print(f"[LOGGER DB] {e}")
    return log_entry

def get_buffer_logs(limit: int = 30):
    """Get recent logs from memory buffer (for Telegram fallback)."""
    return list(reversed(_LOGS_BUFFER[-limit:]))

def system_start():
    _log("⚡", "SYSTEM", "CTM Bot v1.0 — بدء التشغيل")
    _log("✅", "SYSTEM", "تم تحميل الإعدادات")

def binance_connected():
    _log("📡", "DATA", "Binance API — متصل")

def supabase_connected():
    _log("🗄️", "DB", "Supabase — متصل")

def coins_loaded(count: int, coins: list):
    _log("✅", "SYSTEM", f"تم تحميل {count} عملات: {', '.join(coins)}")

def analysis_start(symbol: str, tf: str):
    _log("🔍", "ANALYSIS", f"جاري تحليل {symbol} | الإطار: {tf}")

def fetch_data_start(symbol: str):
    _log("📥", "DATA", f"جاري جلب بيانات {symbol} من Binance...")

def fetch_data_done(symbol: str, klines_count: int):
    _log("📥", "DATA", f"تم جلب {klines_count} شمعة لـ {symbol}")

def market_regime(symbol: str, regime: str, details: dict):
    metrics = details.get('metrics', {})
    _log("📊", "ANALYSIS",
         f"{symbol}: نظام السوق = {regime} | ADX={metrics.get('adx',0):.1f} "
         f"تقلب={metrics.get('volatility',0):.1f}% زخم={metrics.get('momentum',0):.1f}%",
         details)

def strategy_check(symbol: str, strategy: str):
    _log("🧠", "STRATEGY", f"{symbol}: فحص استراتيجية {strategy}...")

def strategy_selected(symbol: str, strategy: str, reason: str):
    _log("🧠", "STRATEGY", f"{symbol}: تم اختيار {strategy} — {reason}")

def no_signal(symbol: str, reason: str):
    _log("⏳", "SIGNAL", f"{symbol}: لا توجد إشارة دخول — {reason}")

def signal_generated(signal_data: dict):
    _log("🎯", "SIGNAL",
         f"{signal_data['symbol']}: إشـــــارة! دخول={signal_data['entry_price']:.4f} "
         f"وقف={signal_data['stop_loss']:.4f} هدف={signal_data['take_profit1']:.4f} "
         f"حجم={signal_data['position_size']:.4f}")

def signal_sent(symbol: str):
    _log("📨", "BOT", f"تم إرسال الإشارة إلى Telegram لـ {symbol}")

def monitoring(symbol: str, current_price: float, entry: float, sl: float, tp: float):
    dist_sl = abs(current_price - sl) / entry * 100
    dist_tp = abs(tp - current_price) / entry * 100
    _log("👁️", "MONITOR", f"{symbol}: السعر={current_price:.4f} | بعد عن الوقف: {dist_sl:.1f}% | بعد عن الهدف: {dist_tp:.1f}%")

def tp_hit(symbol: str, price: float, profit_pct: float, profit_usd: float):
    _log("🎯", "RESULT", f"{symbol}: هدف محقق @ {price:.4f} | ربح: {profit_pct:.2f}% (${profit_usd:.2f})")

def sl_hit(symbol: str, price: float, loss_pct: float, loss_usd: float):
    _log("🛑", "RESULT", f"{symbol}: وقف خسارة @ {price:.4f} | خسارة: {loss_pct:.2f}% (${loss_usd:.2f})")

def error(component: str, message: str):
    _log("❌", "ERROR", f"{component}: {message}")

def cron_tick():
    _log("⏰", "SYSTEM", "بدء دورة تحليل جديدة")

def cron_complete(duration_seconds: float):
    _log("✅", "SYSTEM", f"انتهت دورة التحليل — {duration_seconds:.1f} ثانية")
