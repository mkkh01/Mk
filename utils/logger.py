"""
CTM Bot - Logging System
Logs everything: startup, analysis, strategy decisions, signals, monitoring, results.
Logs stored in Supabase + printed to console.
"""

import json
import time
from datetime import datetime
import psycopg
import psycopg.extras

# Will be imported after config
DB_URL = None

def init_logger(db_url: str):
    """Initialize logger with DB connection URL."""
    global DB_URL
    DB_URL = db_url

def _log(level: str, component: str, message: str, details: dict = None):
    """Core log function — writes to console and Supabase."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {level} {component} — {message}"
    print(log_entry)

    if DB_URL and details is None:
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
        except Exception as e:
            print(f"[LOGGER ERROR] Failed to write to DB: {e}")

    return log_entry

def system_start():
    """Log system startup."""
    _log("⚡", "SYSTEM", "CTM Bot v1.0 — Starting up")
    _log("✅", "SYSTEM", "Configuration loaded")

def binance_connected():
    _log("📡", "DATA", "Binance Public API — connected")

def supabase_connected():
    _log("🗄️", "DB", "Supabase — connected")

def coins_loaded(count: int, coins: list):
    _log("✅", "SYSTEM", f"Loaded {count} tracked coins: {', '.join(coins)}")

def analysis_start(symbol: str, tf: str):
    _log("🔍", "ANALYSIS", f"Analyzing {symbol} | Timeframe: {tf}")

def market_regime(symbol: str, regime: str, details: dict):
    _log("📊", "ANALYSIS", f"{symbol}: Market Regime = {regime}", details)

def strategy_selected(symbol: str, strategy: str, reason: str):
    _log("🧠", "STRATEGY", f"{symbol}: Selected {strategy} — {reason}")

def no_signal(symbol: str, reason: str):
    _log("⏳", "SIGNAL", f"{symbol}: No entry signal — {reason}")

def signal_generated(signal_data: dict):
    _log("🎯", "SIGNAL",
         f"{signal_data['symbol']}: SIGNAL — Entry={signal_data['entry']:.4f} "
         f"SL={signal_data['stop_loss']:.4f} TP={signal_data['take_profit1']:.4f} "
         f"Size={signal_data['position_size']:.4f}")

def signal_sent(symbol: str):
    _log("📨", "BOT", f"Signal sent to Telegram for {symbol}")

def monitoring(symbol: str, current_price: float, entry: float, sl: float, tp: float):
    dist_sl = abs(current_price - sl) / entry * 100
    dist_tp = abs(tp - current_price) / entry * 100
    _log("👁️", "MONITOR", f"{symbol}: Price={current_price:.4f} | SL distance: {dist_sl:.1f}% | TP distance: {dist_tp:.1f}%")

def tp_hit(symbol: str, price: float, profit_pct: float, profit_usd: float):
    _log("🎯", "RESULT", f"{symbol}: TP HIT @ {price:.4f} | Profit: {profit_pct:.2f}% (${profit_usd:.2f})")

def sl_hit(symbol: str, price: float, loss_pct: float, loss_usd: float):
    _log("🛑", "RESULT", f"{symbol}: SL HIT @ {price:.4f} | Loss: {loss_pct:.2f}% (${loss_usd:.2f})")

def error(component: str, message: str):
    _log("❌", "ERROR", f"{component}: {message}")

def cron_tick():
    _log("⏰", "SYSTEM", "Cron tick — starting analysis cycle")

def cron_complete(duration_seconds: float):
    _log("✅", "SYSTEM", f"Analysis cycle complete — {duration_seconds:.1f}s")
