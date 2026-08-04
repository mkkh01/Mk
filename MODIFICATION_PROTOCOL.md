# CT — Modification Protocol

This file is a **skill** that every AI agent MUST read before modifying any file in this repository. Its purpose: ensure modifications are focused, minimal, and confined to the exact area related to the problem.

---

## 1. Core Rule — Focused Modification

When asked to fix or modify something, edit **only** the files that satisfy **at least one** of these conditions:

1. The file **directly contains** the code causing the error
2. The file **defines** the function / variable / class related to the error
3. The file is a **test** that directly tests the affected unit
4. The file **imports the changed function/class** and needs its signature updated

If none of these apply — **do not touch the file**.

---

## 2. Do NOT Do This

- Do NOT rewrite entire files when only one function needs fixing
- Do NOT make drive-by changes to unrelated files
- Do NOT add new features that were not explicitly requested
- Do NOT change project structure without explicit permission
- Do NOT touch `engine/orchestrator.py` unless the bug is specifically in cross-module wiring, gate ordering, confidence aggregation, or end-to-end decision flow
- Do NOT touch `app/main.py` unless the bug is in process lifecycle, task startup/stop, signal handling, or pub/sub dispatch
- Do NOT touch `contracts/*.py` unless the bug is in the data model definition itself
- Do NOT touch `config/thresholds.py` unless the request is specifically about changing a constant

---

## 3. Dependency Map (Verified from Actual Imports)

| Module | Own Files | Consumes (Reads) | Avoid Touching |
|--------|-----------|------------------|----------------|
| **contracts/** | All `.py` in `contracts/` | `config/thresholds` only | engine/, bot/, app/, simulation/ |
| **config/** | `thresholds.py`, `settings.py` | `contracts/config` | engine/, market/, bot/, app/ |
| **data/** | `validators.py`, `cleaners.py` | `config/thresholds`, `contracts/market`, `monitoring/logger` | engine/, simulation/, bot/ |
| **market/** | `volatility.py`, `regime.py`, `session.py`, `liquidity.py` | `config/thresholds`, `contracts/market`, `monitoring/logger` | engine/, storage/, simulation/, bot/ |
| **engine/leaf** | `trend.py`, `structure.py`, `momentum.py`, `volume.py`, `smc.py`, `session.py`, `htf_filter.py`, `confidence.py`, `entry_rules.py`, `risk.py` | `config/thresholds`, `contracts/*`, `market/*`, `monitoring/logger` | `engine/orchestrator.py`, `storage/`, `simulation/`, `bot/`, `app/` |
| **engine/orchestrator.py** | orchestrator only | EVERYTHING | Do NOT edit unless bug is in wiring/gates/aggregation |
| **ingest/** | `binance_ws.py` | `config/thresholds`, `contracts/*`, `data/*`, `storage/*`, `monitoring/*` | engine/, simulation/, bot/ |
| **storage/** | `supabase.py`, `redis_cache.py` | `contracts/*`, `monitoring/logger` | engine/, market/, bot/, simulation/ |
| **simulation/** | `fees.py`, `slippage.py`, `paper_trade.py` | `config/thresholds`, `contracts/*`, `market/volatility`, `storage/supabase`, `monitoring/*` | engine/, market/, bot/ |
| **portfolio/** | `performance.py` | `contracts/*`, `storage/supabase`, `monitoring/logger` | engine/, market/, ingest/ |
| **analysis/** | `result_aggregator.py`, `result_formatter.py`, `performance_analyzer.py` | `storage/supabase`, `contracts/*`, `portfolio/performance` | engine/, market/, bot/, app/ |
| **monitoring/** | All `.py` in `monitoring/` | `structlog`, `psutil` (externals only) | Everything else |
| **bot/** | `telegram_bot.py` | `contracts/*`, `config/thresholds`, `storage/*`, `portfolio/performance`, `monitoring/logger`, `analysis/*` | engine/, market/, ingest/, data/ |
| **app/** | `main.py`, `dashboard_endpoints.py`, `workflow_endpoints.py` | Everything (lazy imports) | engine/*, market/*, data/*, contracts/* |

---

## 4. Quick Map — Where to Look for a Bug

| Problem Type | Look Here First | Then Check | Do NOT Touch |
|---|---|---|---|
| Market analysis wrong (trend / SMC / momentum / volume) | `engine/{module}.py` — the specific analyzer | `config/thresholds` (if threshold-related) | `engine/orchestrator.py`, `storage/`, `simulation/`, `bot/` |
| Market classification wrong (regime / volatility / session / liquidity) | `market/{module}.py` | `config/thresholds` | `engine/`, `storage/`, `simulation/` |
| Decision pipeline logic wrong (gates, aggregation, scoring) | `engine/orchestrator.py` lines 289-817 | `engine/confidence.py` | `storage/`, `simulation/`, `bot/` |
| Risk check wrong (position size, SL/TP, R:R) | `engine/risk.py` | `config/thresholds` | `engine/orchestrator.py`, `simulation/`, `bot/` |
| Entry rules wrong (limit vs market, offset, timeout) | `engine/entry_rules.py` | `config/thresholds` | `engine/orchestrator.py`, `simulation/` |
| Data ingestion wrong (Binance WebSocket, validation, cleaning) | `ingest/binance_ws.py` | `data/validators.py`, `data/cleaners.py` | `engine/`, `simulation/`, `bot/` |
| Database write/read wrong (Supabase) | `storage/supabase.py` | `storage/migrations/*.sql` | `engine/`, `market/`, `simulation/`, `bot/` |
| Redis cache wrong (live prices, checkpoints, pub/sub) | `storage/redis_cache.py` | `app/main.py` (pub/sub subscription) | `engine/`, `market/`, `data/` |
| Simulated trade wrong (open/close/fees/slippage) | `simulation/paper_trade.py` | `simulation/fees.py`, `simulation/slippage.py` | `engine/`, `market/`, `bot/` |
| Performance metrics wrong | `portfolio/performance.py` | `contracts/portfolio.py` | `engine/`, `bot/`, `ingest/` |
| Telegram bot wrong (commands, formatting, buttons) | `bot/telegram_bot.py` | `contracts/portfolio.py` | `engine/`, `market/`, `data/`, `ingest/` |
| Logging / health / cycle summary wrong | `monitoring/{module}.py` | `monitoring/logger.py` (event catalog) | `engine/`, `market/`, `bot/` |
| Dashboard / API wrong | `app/dashboard_endpoints.py`, `app/workflow_endpoints.py`, `app/static/index.html` | `contracts/config` (if model-related) | `engine/`, `market/`, `data/`, `ingest/` |
| Process lifecycle wrong (start/stop/signal/shutdown) | `app/main.py` | `monitoring/health_manager.py` | `engine/*`, `market/*`, `data/*` |
| Data model wrong (missing field, wrong type) | `contracts/{module}.py` | `storage/supabase.py` (SQL mapping), `storage/migrations/` | `engine/`, `market/`, `bot/` |
| Constant / threshold wrong | `config/thresholds.py` | `config/settings.py` (if env-related) | `engine/*`, `market/*`, `storage/*` |

---

## 5. Verification Checklist — Before Editing

Before modifying any file, answer these questions:

1. **Does this file directly contain the error?** → Yes → Edit it.
2. **Does this file define what needs to change?** → Yes → Edit it.
3. **Does another file import what I'm changing and need a signature update?** → Yes → Edit only that import/signature line.
4. **Does another file need to be edited for a different reason?** → No → Stop. Do not touch it.
5. **Am I rewriting the whole file when I only need to change 3 lines?** → Yes → Rewrite only the function/method, not the whole file.
6. **Am I adding a feature that wasn't requested?** → Yes → Stop. Ask first.
7. **Am I unsure about something?** → Stop and ask the user.

---

## 6. Special Rules for Key Files

### `engine/orchestrator.py`
This is the **only** file allowed to combine signals from all engine modules. Do NOT edit it unless the bug is specifically about:
- Cross-module wiring (wrong module called in wrong order)
- Gate ordering (HTF check before structure, etc.)
- Confidence aggregation logic
- End-to-end decision flow
- Report/log emission after decision

### `app/main.py`
This is the **wiring layer**. Do NOT edit it unless the bug is about:
- Process startup/shutdown
- Engine start/stop lifecycle
- Signal handling (SIGTERM/SIGINT)
- Redis pub/sub subscription setup
- Task creation/cancellation
- Dashboard endpoint registration

### `config/thresholds.py`
This file is consumed by **every other module**. Changing a constant here ripples everywhere. Do NOT edit it unless the request is explicitly about changing a constant value, and be aware that the change will affect all downstream modules.

### `contracts/*.py`
These are **pure data models**. Do NOT edit them unless:
- A field is missing from a model
- A field type is wrong
- A validation rule needs updating
- A new model is needed

### `storage/supabase.py`
This is the **database boundary**. Do NOT edit it unless:
- A SQL query is wrong
- A table mapping is incorrect
- A CRUD operation is missing or broken
- Migration is needed (then also edit `storage/migrations/`)

---

## 7. Test File Mapping

Each test file corresponds to one or more source files. When fixing a bug, update the related test only if the fix changes the tested behavior:

| Test File | Source Files |
|-----------|-------------|
| `tests/unit/test_bot.py` | `bot/telegram_bot.py` |
| `tests/unit/test_confidence.py` | `engine/confidence.py` |
| `tests/unit/test_data_cleaners.py` | `data/cleaners.py` |
| `tests/unit/test_data_validators.py` | `data/validators.py` |
| `tests/unit/test_entry_rules.py` | `engine/entry_rules.py` |
| `tests/unit/test_htf_filter.py` | `engine/htf_filter.py` |
| `tests/unit/test_ingest.py` | `ingest/binance_ws.py` |
| `tests/unit/test_market_liquidity.py` | `market/liquidity.py` |
| `tests/unit/test_market_regime.py` | `market/regime.py` |
| `tests/unit/test_market_session.py` | `market/session.py` |
| `tests/unit/test_market_volatility.py` | `market/volatility.py` |
| `tests/unit/test_orchestrator.py` | `engine/orchestrator.py` |
| `tests/unit/test_portfolio.py` | `portfolio/performance.py` |
| `tests/unit/test_risk.py` | `engine/risk.py` |
| `tests/unit/test_simulation.py` | `simulation/paper_trade.py` |
| `tests/unit/test_smc.py` | `engine/smc.py` |
| `tests/unit/test_storage.py` | `storage/supabase.py` |
| `tests/unit/test_structure.py` | `engine/structure.py` |
| `tests/unit/test_volume.py` | `engine/volume.py` |
| `tests/integration/test_decision_to_trade.py` | `engine/orchestrator.py` + `simulation/paper_trade.py` |
| `tests/integration/test_resume_flow.py` | `app/main.py` + `storage/redis_cache.py` |
| `tests/integration/test_telegram_flows.py` | `bot/telegram_bot.py` + `app/main.py` |
| `tests/integration/test_ws_to_decision.py` | `ingest/binance_ws.py` + `engine/orchestrator.py` |

---

## 8. Architecture Summary

```
contracts/          ← Pure data models (Pydantic). Zero deps.
       ↑
config/thresholds   ← All constants. Zero deps. Consumed by everything.
       ↑
data/               ← Validation + cleaning of raw market data.
       ↑
market/             ← Market analysis primitives (regime, volatility, session, liquidity).
       ↑
engine/leaf         ← Individual strategy analyzers (trend, structure, momentum, volume, smc, etc.)
       ↑
engine/orchestrator ← The ONLY cross-module integrator. Combines all signals.
       ↑
ingest/             ← Data ingestion boundary (Binance WebSocket → validated candles).
       ↑
storage/            ← Persistence boundary (Redis + Supabase).
       ↑
simulation/         ← Paper trading (fees, slippage, open/close).
       ↑
portfolio/          ← Performance metrics from closed trades.
       ↑
analysis/           ← Result aggregation + formatting.
       ↑
monitoring/         ← Logging + health + reporting (observation only).
       ↑
bot/                ← Telegram UI (formatting + delegation only).
       ↑
app/                ← Wiring layer (lifecycle + task orchestration + API).
```
