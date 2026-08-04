-- Migration: 007_spot_only_constraint
-- Description: Update simulated_trades direction check to only allow 'long' (Spot-only).

-- 1. Remove the old constraint if it exists.
DO $$
BEGIN
    ALTER TABLE simulated_trades DROP CONSTRAINT IF EXISTS simulated_trades_direction_check;
EXCEPTION
    WHEN undefined_object THEN
        NULL;
END $$;

-- 2. Update any existing 'short' trades to 'long' BEFORE adding the constraint.
-- This prevents CheckViolationError during migration.
UPDATE simulated_trades SET direction = 'long' WHERE direction = 'short';

-- 3. Add the new Spot-only constraint.
ALTER TABLE simulated_trades 
ADD CONSTRAINT simulated_trades_direction_check 
CHECK (direction = 'long');
