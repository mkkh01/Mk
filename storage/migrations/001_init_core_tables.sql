-- File: storage/migrations/001_init_core_tables.sql
-- Responsibility: Create the four core tables (candles, decisions,
--   simulated_trades, ws_checkpoints, performance_snapshots) with their
--   constraints and indexes.
-- Downstream: storage/supabase.py reads/writes these tables.
-- Idempotency rules (Section 4) are enforced here at the DB level:
--   * candles:        PRIMARY KEY (symbol, timeframe, open_time)  -> upsert ON CONFLICT
--   * decisions:      UNIQUE (symbol, source_candle_open_time)    -> idempotent decision writes
--   * simulated_trades: UNIQUE (decision_id)                     -> one trade per decision

CREATE TABLE IF NOT EXISTS candles (
    symbol              TEXT NOT NULL,
    timeframe           TEXT NOT NULL,
    open_time           TIMESTAMPTZ NOT NULL,
    close_time          TIMESTAMPTZ NOT NULL,
    open                NUMERIC NOT NULL,
    high                NUMERIC NOT NULL,
    low                 NUMERIC NOT NULL,
    close               NUMERIC NOT NULL,
    volume              NUMERIC NOT NULL,
    taker_buy_volume    NUMERIC NOT NULL,
    taker_sell_volume   NUMERIC NOT NULL,
    is_closed           BOOLEAN NOT NULL,
    PRIMARY KEY (symbol, timeframe, open_time)
);

CREATE INDEX IF NOT EXISTS idx_candles_symbol_timeframe_closed
ON candles(symbol, timeframe, open_time) WHERE is_closed = TRUE;

CREATE TABLE IF NOT EXISTS decisions (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol                      TEXT NOT NULL,
    source_candle_open_time     TIMESTAMPTZ NOT NULL,
    score                       NUMERIC NOT NULL,
    confidence                  NUMERIC NOT NULL,
    regime_check_passed         BOOLEAN NOT NULL,
    structure_alignment_passed  BOOLEAN NOT NULL,
    htf_bias_aligned            BOOLEAN NOT NULL DEFAULT FALSE,
    risk_allowed                BOOLEAN NOT NULL,
    risk_reason                 TEXT,
    entry_payload               JSONB,
    risk_payload                JSONB,
    final_verdict               BOOLEAN NOT NULL,
    rejection_reason            TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, source_candle_open_time)
);

CREATE INDEX IF NOT EXISTS idx_decisions_symbol_time ON decisions(symbol, source_candle_open_time);
CREATE INDEX IF NOT EXISTS idx_decisions_verdict ON decisions(final_verdict, created_at);

CREATE TABLE IF NOT EXISTS simulated_trades (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_id     UUID NOT NULL REFERENCES decisions(id) UNIQUE,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('long', 'short')),
    entry_price     NUMERIC NOT NULL,
    size            NUMERIC NOT NULL,
    fee             NUMERIC NOT NULL,
    slippage        NUMERIC NOT NULL,
    opened_at       TIMESTAMPTZ NOT NULL,
    closed_at       TIMESTAMPTZ,
    pnl             NUMERIC,
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    close_reason    TEXT CHECK (close_reason IN ('tp', 'sl', 'time', 'manual')),
    is_simulated    BOOLEAN NOT NULL DEFAULT TRUE,
    stop_loss       NUMERIC,
    take_profit     NUMERIC
);

CREATE INDEX IF NOT EXISTS idx_simulated_trades_symbol ON simulated_trades(symbol, status);
CREATE INDEX IF NOT EXISTS idx_simulated_trades_opened ON simulated_trades(opened_at DESC);

CREATE TABLE IF NOT EXISTS ws_checkpoints (
    symbol                 TEXT NOT NULL,
    timeframe              TEXT NOT NULL,
    last_closed_open_time  TIMESTAMPTZ NOT NULL,
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, timeframe)
);

CREATE TABLE IF NOT EXISTS performance_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    period_start    TIMESTAMPTZ,
    period_end      TIMESTAMPTZ,
    total_trades    INTEGER NOT NULL DEFAULT 0,
    winning_trades  INTEGER NOT NULL DEFAULT 0,
    losing_trades   INTEGER NOT NULL DEFAULT 0,
    win_rate        NUMERIC,
    total_pnl       NUMERIC,
    max_drawdown    NUMERIC,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_perf_snapshots_created ON performance_snapshots(created_at DESC);

CREATE TABLE IF NOT EXISTS decision_component_signals (
    decision_id UUID NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY (decision_id, idx)
);
