"""
CTM Bot - Supabase Database Client
Handles all CRUD operations for coins, signals, trades, and logs.
"""

import psycopg
import json
from datetime import datetime
from config import SUPABASE_DB_URL

def get_conn():
    """Get a database connection."""
    return psycopg.connect(SUPABASE_DB_URL, sslmode="require")

def init_db():
    """Create all required tables if they don't exist."""
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tracked_coins (
            id SERIAL PRIMARY KEY,
            symbol VARCHAR(20) NOT NULL UNIQUE,
            timeframes TEXT[] NOT NULL DEFAULT '{"1h"}',
            capital_value REAL NOT NULL DEFAULT 100.0,
            risk_percent REAL NOT NULL DEFAULT 2.0,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    # Migration: rename capital_percent to capital_value if old schema exists
    try:
        cur.execute("ALTER TABLE tracked_coins RENAME COLUMN capital_percent TO capital_value")
        conn.commit()
    except:
        conn.rollback()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id SERIAL PRIMARY KEY,
            symbol VARCHAR(20) NOT NULL,
            timeframe VARCHAR(10) NOT NULL,
            strategy VARCHAR(50) NOT NULL,
            entry_price REAL NOT NULL,
            stop_loss REAL NOT NULL,
            take_profit1 REAL NOT NULL,
            take_profit2 REAL,
            position_size REAL NOT NULL,
            risk_percent REAL NOT NULL,
            capital_value REAL NOT NULL,
            signal_status VARCHAR(20) DEFAULT 'PENDING',
            market_regime VARCHAR(30),
            regime_details JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            closed_at TIMESTAMPTZ
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trade_results (
            id SERIAL PRIMARY KEY,
            signal_id INTEGER REFERENCES signals(id),
            symbol VARCHAR(20) NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            result VARCHAR(10) NOT NULL,
            profit_pct REAL NOT NULL,
            profit_usd REAL NOT NULL,
            closed_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            level VARCHAR(10) NOT NULL,
            component VARCHAR(30) NOT NULL,
            message TEXT NOT NULL,
            details JSONB DEFAULT '{}'
        );
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp DESC);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(signal_status);
    """)

    conn.commit()
    cur.close()
    conn.close()

# === Coin CRUD ===

def get_active_coins():
    """Get all active tracked coins."""
    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute("SELECT * FROM tracked_coins WHERE is_active = TRUE")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def add_coin(symbol: str, timeframes: list, capital_value: float, risk_percent: float):
    """Add or update a tracked coin."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO tracked_coins (symbol, timeframes, capital_value, risk_percent)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (symbol) DO UPDATE SET
            timeframes = EXCLUDED.timeframes,
            capital_value = EXCLUDED.capital_value,
            risk_percent = EXCLUDED.risk_percent,
            is_active = TRUE,
            updated_at = NOW()
    """, (symbol.upper(), timeframes, capital_value, risk_percent))
    conn.commit()
    cur.close()
    conn.close()

def remove_coin(symbol: str):
    """Deactivate a tracked coin."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE tracked_coins SET is_active = FALSE, updated_at = NOW() WHERE symbol = %s", (symbol.upper(),))
    conn.commit()
    cur.close()
    conn.close()

# === Signal CRUD ===

def save_signal(signal_data: dict):
    """Save a new signal."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO signals (symbol, timeframe, strategy, entry_price, stop_loss,
            take_profit1, take_profit2, position_size, risk_percent, capital_value,
            signal_status, market_regime, regime_details)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        signal_data['symbol'], signal_data['timeframe'], signal_data['strategy'],
        signal_data['entry_price'], signal_data['stop_loss'],
        signal_data['take_profit1'], signal_data.get('take_profit2'),
        signal_data['position_size'], signal_data['risk_percent'],
        signal_data['capital_value'], 'ACTIVE',
        signal_data.get('market_regime', 'UNKNOWN'),
        json.dumps(signal_data.get('regime_details', {}))
    ))
    signal_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return signal_id

def get_active_signals():
    """Get all active signals being monitored."""
    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute("SELECT * FROM signals WHERE signal_status = 'ACTIVE' ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def close_signal(signal_id: int, status: str, exit_price: float):
    """Close a signal (TP/SL hit or cancelled)."""
    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    # Get signal data
    cur.execute("SELECT * FROM signals WHERE id = %s", (signal_id,))
    signal = cur.fetchone()

    if not signal:
        cur.close()
        conn.close()
        return None

    entry = signal['entry_price']
    profit_pct = ((exit_price - entry) / entry) * 100
    profit_usd = (exit_price - entry) * signal['position_size']

    # Update signal status
    cur.execute("""
        UPDATE signals SET signal_status = %s, closed_at = NOW()
        WHERE id = %s
    """, (status, signal_id))

    # Save result
    cur.execute("""
        INSERT INTO trade_results (signal_id, symbol, entry_price, exit_price, result, profit_pct, profit_usd)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (signal_id, signal['symbol'], entry, exit_price, status, profit_pct, profit_usd))

    conn.commit()
    cur.close()
    conn.close()

    return {
        'symbol': signal['symbol'],
        'entry': entry,
        'exit': exit_price,
        'result': status,
        'profit_pct': profit_pct,
        'profit_usd': profit_usd
    }

def get_recent_signals(limit: int = 5):
    """Get recently generated signals."""
    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute("SELECT * FROM signals ORDER BY created_at DESC LIMIT %s", (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def get_recent_results(limit: int = 10):
    """Get recent trade results."""
    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute("SELECT * FROM trade_results ORDER BY closed_at DESC LIMIT %s", (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def get_recent_logs(limit: int = 30):
    """Get recent system logs."""
    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute("SELECT * FROM logs ORDER BY timestamp DESC LIMIT %s", (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows
