-- File: storage/migrations/006_add_close_price_to_trades.sql
-- Responsibility: Add close_price column to simulated_trades table.

ALTER TABLE simulated_trades ADD COLUMN IF NOT EXISTS close_price NUMERIC;
