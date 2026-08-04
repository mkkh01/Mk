-- File: storage/migrations/003_add_performance.sql
-- Responsibility: Add close_reason column to simulated_trades if missing
--   (the column was added late in the design). Idempotent via DO $$ block.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'simulated_trades' AND column_name = 'close_reason'
    ) THEN
        ALTER TABLE simulated_trades ADD COLUMN close_reason TEXT
        CHECK (close_reason IN ('tp', 'sl', 'time', 'manual'));
    END IF;
END $$;

-- Performance snapshots enrichment (add sharpe_ratio + profit_factor for
-- later use by portfolio/performance.py). Idempotent.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'performance_snapshots' AND column_name = 'sharpe_ratio'
    ) THEN
        ALTER TABLE performance_snapshots ADD COLUMN sharpe_ratio NUMERIC;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'performance_snapshots' AND column_name = 'profit_factor'
    ) THEN
        ALTER TABLE performance_snapshots ADD COLUMN profit_factor NUMERIC;
    END IF;
END $$;
