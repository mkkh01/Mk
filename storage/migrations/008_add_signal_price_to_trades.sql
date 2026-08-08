-- File: storage/migrations/008_add_signal_price_to_trades.sql
-- Responsibility: Add ``signal_price`` and ``live_price_age_seconds`` to
--   ``simulated_trades`` so the recorded ``entry_price`` can represent the
--   actual fill price while the original signal price is preserved for
--   slippage / stale-price analysis (fix: entry price computed from the last
--   closed candle at signal time instead of the live price at fill time).

ALTER TABLE simulated_trades
    ADD COLUMN IF NOT EXISTS signal_price NUMERIC;

ALTER TABLE simulated_trades
    ADD COLUMN IF NOT EXISTS live_price_age_seconds NUMERIC;

-- Existing rows keep NULL for both columns: the signal price was whatever the
-- entry_price was recorded as, and no fill-age information exists.
