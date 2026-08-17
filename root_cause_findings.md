# Root-cause review — 2026-08-17

## Repository evidence

- HEAD before this review: `8803cb9` on `main`, working tree clean.
- `app/main.py::start_engine()` normalized coin configs to Day Trading only in a local list.
- `app/main.py::_run_orchestrator_subscriber_guarded()` reloaded raw coins and subscribed to `runtime_fetch_timeframes(coin.timeframes)`, which adds Scalp timeframes.
- `app/main.py::_dispatch_candle_message()` reloaded raw `CoinConfig` and passed it to `process_candle_safe()` without Day Trading normalization. It also ran the primary orchestrator for every closed subscribed candle, including 5m, before running Scalp.
- This violated strict separation and allowed 5m analysis to enter the primary Day Trading path.
- `bot/telegram_bot.py` still exposed `Edit Timeframes` and `_edit_coin_apply_timeframes()` persisted arbitrary valid timeframe lists. The Add Coin flow was fixed, but the old edit path remained a database drift source.
- `decisions` schema used `UNIQUE(symbol, source_candle_open_time)` while `DecisionResult` already carried `trigger_timeframe`. Because the subscriber could process both 5m and Day Trading candles, decisions at the same symbol/time could collide and be overwritten or persisted under the wrong semantic identity.
- `app/main.py` used broad graceful-degradation handling: `process_candle_safe()` returns `None` on exceptions, and dispatch did not count/log a distinct `analysis_returned_none`. Approved decisions with missing entries and trade-open failures were logged but not represented in dedicated counters.
- `engine/orchestrator.py::_open_simulated_trade()` is intentionally a no-op; actual Day Trading trade opening is owned by `app/main.py`.

## Supabase evidence

- Active coins had inconsistent persisted timeframes:
  - `ADAUSDT`, `DOTUSDT`, `LINKUSDT`, `NEARUSDT`, `XLMUSDT`, `XRPUSDT`: `[15m, 1h, 4h]` (missing `30m`).
  - `ALGOUSDT`, `APTUSDT`, `ARBUSDT`, `ATOMUSDT`, `SOLUSDT`, `UNIUSDT`: `[5m, 15m, 30m, 1h, 4h]` (includes primary `5m`).
- Candle coverage existed for all monitored intervals, so the immediate blocker was not missing database candles. Recent latest closes were approximately 20:29 for 15m/30m, 19:59 for 1h/4h, and 20:39 for 5m.
- Last 6 hours of `decisions`: `815` rejected by `confidence_below_threshold: 0.65 required`, `29` by `long_too_close_to_recent_swing_high`, `5` by `bounce_confirmation_missing`, plus smaller quality/RSI reasons. This is an explicit gate rejection, not a storage outage.
- A representative recent sample had score around `0.70–0.78` but confidence often `0.00–0.60`; the dominant mismatch is score vs weighted confidence/safety-cap, not score itself.
- Recent decision aggregates showed `864` rows with `risk_reason = skipped: regime, confidence, signal-quality, RSI, volume, or entry-timing gate failed`; risk was not the hidden independent blocker because risk assessment is intentionally skipped when earlier gates fail.
- Recent approved decisions existed with no matching `simulated_trades`, including ARB at 10:15/10:25 UTC, SOL at 09:15/09:25 UTC, and several ATOM decisions. The code path can explain this through operational limit-not-filled or broad trade-open failure handling.
- Latest Scalp snapshot at about 20:46 UTC: `status=HEALTHY`, `state=RUNNING`, `cycles=180`, `errors=0`, `candidates=180`, `approved=0`, `rejected=180`, `entries=0`. Rejection reasons: `bias_30m_not_bullish=75`, `context_1h_bearish=55`, `setup_15m_not_bullish=17`, `trigger_5m_not_bullish=17`, plus small volume/momentum/strength reasons. Latest decision: SOL, `score=0.5127`, `confidence=0.5127`, `volume_state=bullish`, rejected at `bias_30m_not_bullish`.
- Scalp is not failing technically in the latest snapshot; it is healthy but over-selective under the current sequential early-return gates.

## Applied local changes so far (not yet committed)

- `app/main.py`: added `_normalise_day_trading_coin(s)`; normalized at start, subscriber, and dispatch; subscriber channels are fixed Day Trading intervals plus 5m; primary orchestrator is skipped for non-Day-Trading candles; Scalp remains on 5m.
- `bot/telegram_bot.py`: Add legacy handler now keeps fixed Day Trading timeframes; removed Edit Timeframes button; stale edit callbacks/apply path force Day Trading intervals.
- `storage/migrations/009_add_decision_trigger_timeframe.sql`: adds non-null trigger timeframe, migrates old rows to 15m, replaces old unique key with `(symbol, trigger_timeframe, source_candle_open_time)`.
- `storage/migrations/010_normalize_day_trading_timeframes.sql`: normalizes persisted coin rows to `[15m,30m,1h,4h]`.
- `storage/supabase.py`: persists and reads `trigger_timeframe`; upsert conflict key updated.
- `contracts/decision.py`: documentation updated for the composite identity.
- `monitoring/health_manager.py`: added `analysis_failures`, `trade_open_attempts`, `trade_open_failures`, `approved_without_trade`; added recording methods and counted limit-not-filled as approved-without-trade.
- `app/main.py`: records trade-open attempts/failures and `analysis_returned_none`; dashboard diagnostics updated with the new counters.

## Validation so far

- Before root-cause changes: `288 passed, 1 skipped`, Ruff clean.
- After the first clean routing/storage/Telegram patch, `py_compile` and Ruff passed, and the existing test suite still passed. Further tests are pending after the diagnostics/migration changes.
