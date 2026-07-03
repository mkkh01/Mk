import psycopg
import psycopg.rows
import logging
from config import DATABASE_URL

logger = logging.getLogger("database")

_conn = None

def get_conn():
    """Get or create a database connection."""
    global _conn
    try:
        if _conn is None or _conn.closed:
            _conn = psycopg.connect(DATABASE_URL, autocommit=True,
                                    row_factory=psycopg.rows.DictRow)
        return _conn
    except Exception as e:
        logger.error(f"DB connection error: {e}")
        raise

def init_tables():
    """Create all required tables if they don't exist."""
    # Use a plain connection WITHOUT row_factory for DDL —
    # psycopg v3 raises "didn't produce records" on CREATE TABLE
    # when a row_factory is active.
    try:
        ddl_conn = psycopg.connect(DATABASE_URL, autocommit=True)
    except Exception as e:
        logger.error(f"Cannot connect for DDL: {e}")
        raise
    ddl_cur = ddl_conn.cursor()
    
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
            ddl_cur.execute(sql)
            logger.info("Table created/verified")
        except Exception as e:
            logger.error(f"Table creation error: {e}")
    
    # Initialize system state row if not exists
    try:
        ddl_cur.execute("INSERT INTO system_state (id) VALUES (1) ON CONFLICT DO NOTHING;")
    except Exception as e:
        logger.error(f"System state init error: {e}")
    
    ddl_cur.close()
    ddl_conn.close()
    logger.info("All database tables initialized")

def query(sql, params=None, fetch=True):
    """Execute a query and return results."""
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(sql, params or ())
        if fetch:
            result = cur.fetchall()
            # Convert DictRow to list of dicts for compatibility
            result = [dict(row) for row in result]
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