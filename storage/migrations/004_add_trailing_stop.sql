-- File: storage/migrations/004_add_trailing_stop.sql
-- Responsibility: Add trailing-stop tracking columns to simulated_trades.
--   highest_price  -> tracks the highest price reached since trade open (LONG).
--   lowest_price   -> tracks the lowest  price reached since trade open (SHORT).
--   atr_at_entry   -> stores the ATR value at entry time for trailing distance.
-- All columns are idempotent (DO $$ blocks).

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'simulated_trades' AND column_name = 'highest_price'
    ) THEN
        ALTER TABLE simulated_trades ADD COLUMN highest_price NUMERIC;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'simulated_trades' AND column_name = 'lowest_price'
    ) THEN
        ALTER TABLE simulated_trades ADD COLUMN lowest_price NUMERIC;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'simulated_trades' AND column_name = 'atr_at_entry'
    ) THEN
        ALTER TABLE simulated_trades ADD COLUMN atr_at_entry NUMERIC;
    END IF;
END $$;
