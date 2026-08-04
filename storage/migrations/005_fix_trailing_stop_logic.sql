-- Migration: 005_fix_trailing_stop_logic
-- Description: Adds initial_stop_loss and timeframe to simulated_trades table to fix trailing stop activation logic.

DO $$ 
BEGIN
    -- Add initial_stop_loss column if it doesn't exist
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='simulated_trades' AND column_name='initial_stop_loss') THEN
        ALTER TABLE simulated_trades ADD COLUMN initial_stop_loss DECIMAL;
    END IF;

    -- Add timeframe column if it doesn't exist
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='simulated_trades' AND column_name='timeframe') THEN
        ALTER TABLE simulated_trades ADD COLUMN timeframe TEXT DEFAULT '15m';
    END IF;
END $$;

-- Backfill initial_stop_loss for existing trades if possible
UPDATE simulated_trades SET initial_stop_loss = stop_loss WHERE initial_stop_loss IS NULL;
