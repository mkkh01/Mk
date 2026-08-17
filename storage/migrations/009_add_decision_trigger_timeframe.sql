-- Preserve the trigger timeframe on every decision and keep idempotency
-- independent for Day Trading and Scalp-triggered analysis.
ALTER TABLE public.decisions
    ADD COLUMN IF NOT EXISTS trigger_timeframe TEXT;

UPDATE public.decisions
SET trigger_timeframe = '15m'
WHERE trigger_timeframe IS NULL OR trigger_timeframe = '';

ALTER TABLE public.decisions
    ALTER COLUMN trigger_timeframe SET DEFAULT '15m';

ALTER TABLE public.decisions
    ALTER COLUMN trigger_timeframe SET NOT NULL;

ALTER TABLE public.decisions
    DROP CONSTRAINT IF EXISTS decisions_symbol_source_candle_open_time_key;

ALTER TABLE public.decisions
    DROP CONSTRAINT IF EXISTS decisions_symbol_trigger_timeframe_source_candle_open_time_key;

ALTER TABLE public.decisions
    ADD CONSTRAINT decisions_symbol_trigger_timeframe_source_candle_open_time_key
    UNIQUE (symbol, trigger_timeframe, source_candle_open_time);
