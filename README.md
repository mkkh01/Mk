# CT — Telegram Crypto Spot Bot (Simulation-Only)

> **Single source of truth:** `CT_AI_System_Master_Guide_v6.md` (hand this file to any AI before it touches the codebase).

CT is a **simulation-only** Telegram bot that watches Binance spot markets over WebSocket, detects market structure using Smart Money Concepts (SMC), scores candidate setups, applies risk rules, and simulates trades by writing rows to Supabase Postgres.

**It does not place real exchange orders.**

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Project Structure](#project-structure)
3. [Quickstart](#quickstart)
4. [Configuration](#configuration)
5. [Database Setup](#database-setup)
6. [Running the Bot](#running-the-bot)
7. [Telegram Commands](#telegram-commands)
8. [Engine Pipeline](#engine-pipeline)
9. [Testing](#testing)
10. [Deployment](#deployment)
11. [Known Bugs (Must Not Regress)](#known-bugs-must-not-regress)
12. [Roadmap to Real Execution](#roadmap-to-real-execution)

---

## Architecture Overview

CT is a **modular monolith** — one process, one developer, one Telegram bot. The architecture philosophy is: keep decision logic in `engine/orchestrator.py`, keep the bot thin, keep storage typed, and never confuse simulation with execution.

### Layered Dependency Order (Section 1)

```
config → contracts → storage → ingest/data → market → engine → simulation → portfolio → monitoring → bot
```

**Rule:** If a file needs to import from a layer upstream of its own, redesign the file. No exceptions.

### Hard Constraints (Section 0)

1. No trading/scoring logic in `ui/` or `api/` — the bot is thin formatting only.
2. No DB access inside `strategies/` or `engine/*` modules — they take data in, return results out.
3. No risk logic inside execution/simulation modules.
4. No hardcoded thresholds — pull from `config/thresholds.py`.
5. No circular imports — dependency order above is law.
6. **Minimum 3 timeframes per coin** — enforced in Pydantic AND at DB level AND in orchestrator logic.
7. **Never label a simulated trade as "live"/"executed"** in any user-facing text.
8. Every added coin must be evaluated across at least 3 timeframes simultaneously.

---

## Project Structure

```
ct/
├── app/
│   └── main.py                    # Startup, wiring, Telegram bot entrypoint
├── config/
│   ├── settings.py                # PLAIN VALUES — never commit (in .gitignore)
│   ├── settings.example.py        # Template with placeholders (committed)
│   └── thresholds.py              # ALL magic numbers live here
├── contracts/                     # Pydantic models — the shared vocabulary
│   ├── market.py                  # Candle, RegimeState, LiquiditySweep, OrderBlock, FVG
│   ├── decision.py                # StrategySignal, RiskAssessment, DecisionResult, EntrySignal
│   ├── simulation.py              # SimulatedTrade
│   ├── config.py                  # CoinConfig, SystemConfig
│   └── portfolio.py               # PerformanceMetrics, TradeSummary
├── ingest/
│   └── binance_ws.py              # WebSocket client, reconnect/backoff, resume logic
├── data/
│   ├── cleaners.py                # Data cleaning: outlier removal, gap filling
│   └── validators.py              # Input validation: candle sanity checks
├── market/
│   ├── regime.py                  # Market regime detection (trending/ranging/volatile)
│   ├── session.py                 # Market session classification (Asian/London/NY)
│   ├── volatility.py              # Volatility metrics (ATR, BB width)
│   └── liquidity.py               # Liquidity level detection and sweep identification
├── engine/                        # Structure detection and decision making
│   ├── structure.py               # BOS/CHOCH detection, swing points
│   ├── session.py                 # Session-aware filtering
│   ├── volume.py                  # CVD, volume profile, delta analysis
│   ├── trend.py                   # Trend direction and strength
│   ├── momentum.py                # RSI, MACD, stochastic
│   ├── smc.py                     # Smart Money Concepts: OB, FVG, liquidity
│   ├── htf_filter.py              # Higher-timeframe bias filtering
│   ├── confidence.py              # Confidence scoring and signal aggregation
│   ├── risk.py                    # Risk assessment: position sizing, exposure, drawdown
│   ├── entry_rules.py             # Entry timing and price level rules
│   └── orchestrator.py            # Combines ALL above into DecisionResult
├── simulation/
│   ├── paper_trade.py             # Writes simulated trades to storage
│   ├── fees.py                    # Fee calculation (maker/taker)
│   └── slippage.py                # Slippage estimation
├── portfolio/
│   └── performance.py             # Win rate, PnL, drawdown, Sharpe-like metrics
├── storage/
│   ├── supabase.py                # Postgres access via asyncpg
│   ├── migrations/                # One SQL file per schema change
│   │   ├── 001_init_core_tables.sql
│   │   ├── 002_add_coins.sql
│   │   └── 003_add_performance.sql
│   └── redis_cache.py             # Redis operations: get, set, pub/sub
├── monitoring/
│   └── logger.py                  # Structured logging — never alters trading outcome
├── bot/
│   └── telegram_bot.py            # Thin wrapper — calls orchestrator, formats messages
├── tests/
│   ├── unit/                      # 18 unit test files
│   └── integration/               # 4 integration test files
├── requirements.txt
├── pytest.ini
├── .gitignore
└── README.md
```

---

## Quickstart

### Prerequisites

- Python 3.11+
- A Supabase project (free tier is fine)
- A Redis instance (Upstash / Render Redis / local)
- A Telegram bot token (via [@BotFather](https://t.me/BotFather))

### Install

```bash
# Clone the project
git clone <your-repo-url> ct
cd ct

# Create virtual env
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Configure

```bash
# Copy the template and fill in real values
cp config/settings.example.py config/settings.py
$EDITOR config/settings.py
```

`config/settings.py` is gitignored — never commit it. If you accidentally commit it, **rotate all credentials immediately**.

### Set up the database

Apply the migrations to your Supabase project (via the Supabase SQL editor or psql):

```sql
-- Run each file in order:
\i storage/migrations/001_init_core_tables.sql
\i storage/migrations/002_add_coins.sql
\i storage/migrations/003_add_performance.sql
```

The `app/main.py` startup also applies migrations automatically (idempotent) on boot.

### Run

```bash
python -m app.main
# or
python app/main.py
```

Open Telegram, find your bot, send `/start`.

---

## Configuration

### `config/settings.py` (Plain Values, No .env)

Per the spec's explicit policy, configuration is **plain Python values**, not environment variables. This trades a small security risk (must keep `settings.py` out of git) for simplicity on free-tier infrastructure.

```python
from contracts.config import SystemConfig

settings = SystemConfig(
    telegram_bot_token="123456:ABC-...",
    supabase_url="https://YOURPROJECT.supabase.co",
    supabase_key="YOUR-SUPABASE-SERVICE-KEY",
    redis_url="redis://localhost:6379/0",
    default_timeframes=["15m", "1h", "4h"],
    max_active_coins=10,
    simulation_mode=True,
)
```

### `config/thresholds.py`

Every magic number in the project lives here. No threshold may be hardcoded elsewhere. Tuning any of these values immediately propagates to every consumer:

| Domain | Example constants |
|--------|-------------------|
| Market Structure | `SWING_LOOKBACK`, `BOS_CONFIRMATION_CANDLES`, `CHOCH_CONFIRMATION_CANDLES` |
| SMC | `OB_MIN_IMPULSE_PCT`, `OB_MAX_CANDLES_BACK`, `FVG_MIN_GAP_PCT`, `LIQUIDITY_SWEEP_STRENGTH_THRESHOLD` |
| Trend | `TREND_EMA_FAST`, `TREND_EMA_SLOW`, `TREND_ADX_THRESHOLD` |
| Momentum | `MOMENTUM_RSI_PERIOD`, `MOMENTUM_RSI_OVERBOUGHT`, `MOMENTUM_MACD_*` |
| Volatility | `VOLATILITY_ATR_PERIOD`, `VOLATILITY_ATR_MULTIPLIER_SL`, `VOLATILITY_BB_*` |
| Session | `ASIAN_START_UTC`, `LONDON_START_UTC`, `NY_START_UTC` |
| Risk | `MAX_PORTFOLIO_EXPOSURE_PCT`, `MAX_POSITION_SIZE_PCT`, `MAX_DAILY_LOSS_PCT`, `MAX_CONCURRENT_TRADES`, `MIN_RISK_REWARD_RATIO` |
| Confidence | `CONFIDENCE_THRESHOLD`, `HTF_ALIGNMENT_WEIGHT`, `STRUCTURE_WEIGHT`, `MOMENTUM_WEIGHT`, `LIQUIDITY_WEIGHT`, `SESSION_WEIGHT` |
| Entry | `ENTRY_LIMIT_OFFSET_PCT`, `ENTRY_TIMEOUT_MINUTES`, `MAX_ENTRY_RETRIES` |
| Simulation | `MAKER_FEE_PCT`, `TAKER_FEE_PCT`, `SLIPPAGE_PCT` |
| WebSocket | `WS_INITIAL_BACKOFF_SECONDS`, `WS_MAX_BACKOFF_SECONDS`, `WS_STALE_MULTIPLIER` |

The confidence weights sum to 1.0 — this is validated at module-load time in `engine/confidence.py` and tested in `tests/unit/test_confidence.py`.

---

## Database Setup

### Tables (migration 001 + 002 + 003)

| Table | Purpose | Idempotency key |
|-------|---------|-----------------|
| `candles` | OHLCV candles | `(symbol, timeframe, open_time)` PK |
| `decisions` | DecisionResult rows | `UNIQUE (symbol, source_candle_open_time)` |
| `simulated_trades` | SimulatedTrade rows | `UNIQUE (decision_id)` |
| `ws_checkpoints` | WebSocket resume state | `(symbol, timeframe)` PK |
| `coins` | Per-coin configuration | `symbol` PK |
| `performance_snapshots` | Periodic metrics snapshots | `id` PK |

### Triggers (migration 002)

- `coin_delete_cleanup` — when a coin is deleted, cascade-delete its `ws_checkpoints` rows but **never** historical `decisions` / `simulated_trades`.
- `coins_distinct_timeframes` — DB-level enforcement of distinct timeframes (mirrors the Pydantic validator).

---

## Running the Bot

1. **Start the process:** `python app/main.py`
2. **Open Telegram** and message your bot with `/start`.
3. **Add a coin** via the "Add Coin" button. Provide:
   - Symbol (e.g. `BTCUSDT`)
   - At least 3 timeframes (e.g. `15m,1h,4h`)
   - Allocated capital in USDT
   - Risk percentage per trade (e.g. `2.0`)
4. **Start the engine** via the "Start Engine" button.
5. **Watch trade alerts** arrive as the engine detects setups and simulates trades.

---

## Telegram Commands

| Button | Action |
|--------|--------|
| **Add Coin** | Conversation: symbol → timeframes (min 3) → capital → risk% → confirm. Writes to `coins` table. |
| **Edit Coin** | Lists coins; selecting one allows editing timeframes / capital / risk, or deleting the coin (cascade-deletes `ws_checkpoints`, preserves history). |
| **Start Engine** | Starts `ingest/binance_ws.py` + `engine/orchestrator.py` for all active coins. No-op with clear message if already running. |
| **Stop Engine** | Graceful shutdown — finishes in-flight candle, writes `ws_checkpoints`, disconnects. |
| **Live Prices** | Reads latest cached price per active coin from Redis. |
| **Trade History** | Shows last 10 `simulated_trades` (symbol, direction, entry, stop, target, status + PnL if closed). |
| **System Performance** | Reads `portfolio/performance.py` output: win rate, total PnL, # trades, max drawdown. |

All trade-facing messages include the warning:

> ⚠️ WARNING: All trades are simulation only.

---

## Engine Pipeline

```
Closed Candle (from Redis pub/sub)
  → engine/structure.py    → BOS/CHOCH, swing points
  → engine/smc.py          → OBs, FVGs, liquidity sweeps
  → engine/trend.py        → EMA/ADX trend
  → engine/momentum.py     → RSI, MACD, stochastic
  → engine/volume.py       → CVD, volume profile
  → engine/session.py      → session filter
  → market/regime.py       → regime classification
  → engine/htf_filter.py   → HTF bias alignment
  → engine/confidence.py   → aggregate confidence
  → engine/risk.py         → sizing, exposure, drawdown
  → engine/entry_rules.py  → entry price + timing
  → engine/orchestrator.py → DecisionResult
  → simulation/paper_trade.py (if final_verdict=True)
  → storage/supabase.py    → writes decisions + simulated_trades
```

**Minimum 3 timeframes** per coin is enforced in three places: Pydantic validator, DB constraint, orchestrator logic.

### Position Sizing & Scaling

The system uses a **Dynamic Scaling** approach. If a trade's required notional value exceeds the allocated capital, it scales the size down to fit rather than rejecting the trade.

```python
risk_amount   = capital * (risk_percent / 100)
price_risk    = abs(entry_price - stop_loss_price)
raw_size      = risk_amount / price_risk
max_size      = capital * (MAX_POSITION_SIZE_PCT / 100) / entry_price

# Scale to fit capital limit
final_size    = min(raw_size, max_size)
if (final_size * entry_price) > capital:
    final_size = capital / entry_price
```

### Stop Loss / Take Profit

- SL = entry − (ATR × `VOLATILITY_ATR_MULTIPLIER_SL`) for longs (reverse for shorts)
- TP = entry + (ATR × `VOLATILITY_ATR_MULTIPLIER_TP`) for longs (reverse for shorts)
- Minimum R:R = `MIN_RISK_REWARD_RATIO` (default 1.5)

---

## WebSocket Reconnect / Resume (Render-Specific)

Render's free/hobby tier can idle/restart the process at any time. The ingest client handles this with:

1. **Exponential backoff:** 1s → 2s → 4s → ... → capped at 60s. Reset to 1s after 30s of stable connection.
2. **Resume, don't replay from scratch:** On reconnect, read `ws_checkpoints` from Redis AND Postgres, fetch the last N closed candles via Binance REST API (N = `max(SWING_LOOKBACK, OB_MAX_CANDLES_BACK, TREND_EMA_SLOW, VOLATILITY_ATR_PERIOD) + 5`), upsert into Postgres.
3. **Persist checkpoints:** After each closed candle is processed, advance `ws_checkpoint:{symbol}:{timeframe}` in Redis AND `ws_checkpoints` in Postgres. **Only advance on `is_closed == True`** (Bug 3 regression).
4. **Health check:** A periodic task logs `ws_stale` if no message arrives within `2 × expected_interval`.

### Idempotency

- **Candle writes:** `INSERT ... ON CONFLICT (symbol, timeframe, open_time) DO UPDATE`.
- **Decision writes:** `UNIQUE (symbol, source_candle_open_time)` + `ON CONFLICT DO NOTHING`.
- **Simulated trade writes:** `UNIQUE (decision_id)`.
- **Checkpoint advance:** Strictly AFTER the candle/decision write, in the same transaction.

---

## Testing

### Run all tests

```bash
pytest tests/ -v
```

### Test categories

- **Unit tests** (`tests/unit/`): 18 files covering every engine, market, simulation, portfolio, storage, and bot module. Each test maps to a Section 10 acceptance criterion.
- **Integration tests** (`tests/integration/`): 4 end-to-end flows — WS-to-decision, decision-to-trade, resume flow, Telegram flows.

### Critical regression tests

- `test_high_sweep_is_bearish` / `test_low_sweep_is_bullish` — Bug 1 (liquidity sweep direction)
- `test_unclosed_candle_does_not_alter_state` — Bug 3 (repainting)
- `test_cvd_uses_taker_volume` — Bug 2 (CVD source)
- `test_weight_sum_equals_one` — Confidence weights invariant
- `test_threshold_sensitivity` — No hardcoded thresholds
- `test_is_simulated_always_true` — Never label simulated as live

---

## Deployment

### Render.com Deployment Checklist (Section 23)

- [ ] `config/settings.py` exists and is in `.gitignore`
- [ ] `config/settings.example.py` is committed with placeholder values
- [ ] All migrations in `storage/migrations/` are applied to Supabase
- [ ] Redis instance is accessible from Render
- [ ] Supabase project is active and tables exist
- [ ] Telegram bot token is valid and bot is started with BotFather
- [ ] `requirements.txt` is pinned and tested locally
- [ ] All Section 10 tests pass
- [ ] No hardcoded thresholds outside `config/thresholds.py`
- [ ] No simulated trade labeled as "live" in any user-facing text
- [ ] Engine starts and processes at least one closed candle end-to-end
- [ ] Telegram bot responds to all 7 buttons correctly
- [ ] Resume/reconnect scenario tested (stop engine, wait, start engine)

### Render-specific notes

- The process can idle/restart at any time. The WebSocket client (Section 4) handles this gracefully.
- `app/main.py` auto-applies migrations on boot and auto-resumes the engine if `engine_running=true` was set in Redis before the restart.
- Free-tier Postgres on Supabase may pause after inactivity — use a keep-alive cron or upgrade.

---

## Known Bugs (Must Not Regress)

These are previously-shipped bugs that have explicit regression tests in `tests/unit/`.

### Bug 1: Liquidity Sweep Direction Inverted

A **high sweep** (price pokes above a swing high, then rejects down) is **bearish** (reversal direction). A **low sweep** is **bullish**. Older code inverted this. `LiquiditySweep.direction` is the REVERSAL direction, not the sweep direction.

### Bug 2: CVD Computed from Candle Color

Cumulative Volume Delta (CVD) must use `Candle.taker_buy_volume − Candle.taker_sell_volume`, not green/red candle color. A green candle with dominant taker-sell volume must reduce CVD, not increase it.

### Bug 3: Live Candles Overwrite Closed Historical Candles (Repainting)

Live candles (`is_closed=False`) must NEVER advance the WebSocket checkpoint, NEVER trigger structure detection, and NEVER be written through to long-term storage. They are for price display only.

---

## Roadmap to Real Execution

CT is simulation-only by design. When real order placement is genuinely being built (not before):

1. Add `execution/` as its own package with `broker_adapter.py` behind an `IBrokerAdapter` interface.
2. Add `ccxt` (or `python-binance`) to `requirements.txt` only at that point. Treat API key handling as a dedicated security review.
3. Split `SimulatedTrade` into `SimulatedTrade` + `LiveTrade`, or add a `Literal["simulated", "live"]` discriminator.
4. Add a `live_trades` table via a new migration — never repurpose the existing `simulated_trades` table.
5. Update all bot-facing text to clearly distinguish simulated vs live.
6. Add a "LIVE MODE" indicator in Telegram bot status messages.
7. Require explicit user confirmation before any live trade.

---

## License

Private project. See the master guide (`CT_AI_System_Master_Guide_v6.md`) for the authoritative specification.
