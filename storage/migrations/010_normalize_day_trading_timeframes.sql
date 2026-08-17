-- The primary strategy is Day Trading. Normalize persisted coin rows so
-- legacy Swing/custom timeframe selections cannot re-enter the runtime.
UPDATE public.coins
SET timeframes = ARRAY['15m', '30m', '1h', '4h']::TEXT[]
WHERE timeframes IS DISTINCT FROM ARRAY['15m', '30m', '1h', '4h']::TEXT[];
