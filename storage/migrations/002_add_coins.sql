-- File: storage/migrations/002_add_coins.sql
-- Responsibility: Create the coins table with its triggers enforcing:
--   1. At least 3 timeframes per coin (matches CoinConfig validator)
--   2. Distinct timeframes (matches CoinConfig validator)
--   3. Cascade-delete ws_checkpoints when a coin is deleted (but NEVER
--      historical decisions / simulated_trades -- those are preserved).

CREATE TABLE IF NOT EXISTS coins (
    symbol          TEXT PRIMARY KEY,
    timeframes      TEXT[] NOT NULL CHECK (array_length(timeframes, 1) >= 3),
    capital         NUMERIC NOT NULL CHECK (capital > 0),
    risk_percent    NUMERIC NOT NULL CHECK (risk_percent > 0 AND risk_percent <= 100),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Cascade-delete ws_checkpoints when a coin is deleted.
CREATE OR REPLACE FUNCTION delete_coin_cleanup()
RETURNS TRIGGER AS $$
BEGIN
    DELETE FROM ws_checkpoints WHERE symbol = OLD.symbol;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS coin_delete_cleanup ON coins;
CREATE TRIGGER coin_delete_cleanup
    BEFORE DELETE ON coins
    FOR EACH ROW
    EXECUTE FUNCTION delete_coin_cleanup();

-- Enforce distinct timeframes at DB level (matches Pydantic validator).
CREATE OR REPLACE FUNCTION validate_distinct_timeframes()
RETURNS TRIGGER AS $$
BEGIN
    IF array_length(NEW.timeframes, 1) IS DISTINCT FROM (
        SELECT COUNT(DISTINCT tf) FROM unnest(NEW.timeframes) AS tf
    ) THEN
        RAISE EXCEPTION 'timeframes must be distinct (no duplicates allowed)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS coins_distinct_timeframes ON coins;
CREATE TRIGGER coins_distinct_timeframes
    BEFORE INSERT OR UPDATE ON coins
    FOR EACH ROW
    EXECUTE FUNCTION validate_distinct_timeframes();
