import psycopg2
import psycopg2.extras
import logging
from config import DATABASE_URL

logger = logging.getLogger("database")

_conn = None

def get_conn():
    """Get or create a database connection."""
    global _conn
    try:
        if _conn is None or _conn.closed:
            _conn = psycopg2.connect(DATABASE_URL)
            _conn.autocommit = True
        return _conn
    except Exception as e:
        logger.error(f"DB connection error: {e}")
        raise

def init_tables():
    """Create all required tables if they don't exist."""
    conn = get_conn()
    cur = conn.cursor()
    
    tables_sql = [
        """
        CREATE TABLE IF NOT EXISTS assets (
            id SERIAL PRIMARY KEY,
            symbol TEXT NOT NULL UNIQUE,
            timeframes TEXT[] NOT NULL DEFAULT '{"5m","15m","1h","4h"}',
            capital_pct REAL DEFAULT 10.0,
            is_active BOOLEAN DEFAULT TRUE,
            donchian_period INT DEFAULT 20,
            atr_period INT DEFAULT 14,
            atr_sl_multiplier REAL DEFAULT 3.0,
            tp_ratio REAL DEFAULT 2.0,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS tracked_trades (
            id SERIAL PRIMARY KEY,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            strategy TEXT NOT NULL,
            direction TEXT NOT NULL CHECK (direction IN ('BUY', 'SELL')),
            entry_price REAL NOT NULL,
            current_price REAL,
            stop_loss REAL NOT NULL,
            take_profit REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED_SL', 'CLOSED_TP', 'CLOSED_SIGNAL', 'EXPIRED')),
            pnl_pct REAL DEFAULT 0,
            pnl_amount REAL DEFAULT 0,
            entry_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            exit_time TIMESTAMPTZ,
            exit_reason TEXT,
            regime_at_entry TEXT,
            confidence REAL DEFAULT 0,
            atr_at_entry REAL DEFAULT 0
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS signals_log (
            id SERIAL PRIMARY KEY,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            strategy TEXT NOT NULL,
            signal TEXT NOT NULL,
            price REAL NOT NULL,
            stop_loss REAL,
            take_profit REAL,
            regime TEXT,
            confidence REAL DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS system_state (
            id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            bot_running BOOLEAN DEFAULT FALSE,
            total_trades INT DEFAULT 0,
            winning_trades INT DEFAULT 0,
            losing_trades INT DEFAULT 0,
            total_pnl_pct REAL DEFAULT 0,
            max_drawdown_pct REAL DEFAULT 0,
            daily_pnl_pct REAL DEFAULT 0,
            consecutive_losses INT DEFAULT 0,
            last_check_time TIMESTAMPTZ,
            circuit_breaker_active BOOLEAN DEFAULT FALSE,
            kill_switch_active BOOLEAN DEFAULT FALSE,
            start_date TIMESTAMPTZ DEFAULT NOW()
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS performance_snapshots (
            id SERIAL PRIMARY KEY,
            snapshot_date DATE NOT NULL DEFAULT CURRENT_DATE,
            total_trades INT DEFAULT 0,
            wins INT DEFAULT 0,
            losses INT DEFAULT 0,
            pnl_pct REAL DEFAULT 0,
            max_drawdown_pct REAL DEFAULT 0,
            win_rate REAL DEFAULT 0,
            profit_factor REAL DEFAULT 0,
            avg_win_pct REAL DEFAULT 0,
            avg_loss_pct REAL DEFAULT 0,
            UNIQUE(snapshot_date)
        );
        """
    ]
    
    for sql in tables_sql:
        try:
            cur.execute(sql)
            logger.info("Table created/verified")
        except Exception as e:
            logger.error(f"Table creation error: {e}")
    
    # Initialize system state if not exists
    cur.execute("INSERT INTO system_state (id) VALUES (1) ON CONFLICT DO NOTHING;")
    cur.close()
    logger.info("All database tables initialized")

def query(sql, params=None, fetch=True):
    """Execute a query and return results."""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(sql, params or ())
        if fetch:
            result = cur.fetchall()
        else:
            result = None
        conn.commit()
        return result
    except Exception as e:
        conn.rollback()
        logger.error(f"Query error: {e}\nSQL: {sql}")
        raise
    finally:
        cur.close()

def query_one(sql, params=None):
    """Execute a query and return a single row."""
    results = query(sql, params, fetch=True)
    return results[0] if results else None