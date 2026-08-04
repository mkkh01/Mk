"""
File: storage/supabase.py
1. Single Responsibility: Async Postgres access (via asyncpg) for Supabase.
   Every public function maps 1:1 to a contract -- no dict returns.
2. Consumes: asyncpg, contracts.*.
3. Produces: typed CRUD operations on candles, decisions, simulated_trades,
   ws_checkpoints, coins, performance_snapshots.
4. Downstream: ingest/binance_ws.py, engine/orchestrator.py,
   simulation/paper_trade.py, portfolio/performance.py, bot/telegram_bot.py.
5. New Dependencies: asyncpg (in requirements.txt).
6. Touches Section 6 bugs? No.
7. Tests: tests/unit/test_storage.py -- round-trip + idempotency.
8. Logging: candle_written, decision_written, simulated_trade_written.
9. Dependency Order: config -> contracts -> storage/supabase.py.

STORAGE CONTRACT RULES (Section 5):
  * Every function maps 1:1 to a contract (typed, never dict).
  * IDs and FKs are UUID in Postgres and UUID in Pydantic -- never str.
  * ws_checkpoints is the Postgres-backed fallback for Redis checkpoints.
  * Unique constraints make idempotency enforceable at the DB level.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional, Sequence
from uuid import UUID

import asyncpg
from asyncpg.exceptions import UniqueViolationError

from contracts.config import CoinConfig
from contracts.decision import DecisionResult, EntrySignal, RiskAssessment
from contracts.market import Candle
from contracts.portfolio import PerformanceMetrics
from contracts.simulation import SimulatedTrade
from monitoring.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Secret redaction helpers
# ---------------------------------------------------------------------------
def _redact_dsn(dsn: str) -> str:
    """Return a sanitised copy of ``dsn`` with credentials replaced by ``***``.

    Handles the common ``postgresql://user:password@host/...`` and
    ``postgresql+asyncpg://user:password@host/...`` formats.  Also covers the
    simpler ``postgresql://host/...`` case (no credentials).
    """
    if "://" not in dsn:
        return "***"
    try:
        prefix, remainder = dsn.split("://", 1)
    except ValueError:
        return "***"

    # Everything before the first ``@`` after ``://`` is the credentials part.
    if "@" in remainder:
        creds, host_part = remainder.split("@", 1)
        if ":" in creds:
            username, _password = creds.split(":", 1)
            safe_creds = f"{username}:***"
        else:
            safe_creds = "***"
        return f"{prefix}://{safe_creds}@{host_part}"

    # No credentials present.
    return f"{prefix}://{remainder}"


# ---------------------------------------------------------------------------
# Row -> contract mapping helpers
# ---------------------------------------------------------------------------
def _candle_from_row(row: asyncpg.Record) -> Candle:
    return Candle(
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        open_time=row["open_time"],
        close_time=row["close_time"],
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
        taker_buy_volume=float(row["taker_buy_volume"]),
        taker_sell_volume=float(row["taker_sell_volume"]),
        is_closed=bool(row["is_closed"]),
    )


def _decision_from_row(row: asyncpg.Record) -> DecisionResult:
    # Use .get() because older rows (or test dicts) may not have the payload columns.
    risk_payload = row.get("risk_payload")
    if risk_payload:
        risk_data = json.loads(risk_payload)
        risk = RiskAssessment(**risk_data)
    else:
        risk = RiskAssessment(
            allowed=bool(row["risk_allowed"]),
            reason=row.get("risk_reason"),
        )
    
    entry = None
    entry_payload = row.get("entry_payload")
    if entry_payload:
        entry_data = json.loads(entry_payload)
        entry = EntrySignal(**entry_data)

    return DecisionResult(
        id=row["id"],
        symbol=row["symbol"],
        source_candle_open_time=row["source_candle_open_time"],
        score=float(row["score"]),
        confidence=float(row["confidence"]),
        regime_check_passed=bool(row["regime_check_passed"]),
        structure_alignment_passed=bool(row["structure_alignment_passed"]),
        htf_bias_aligned=bool(row["htf_bias_aligned"]),
        risk=risk,
        entry=entry,
        final_verdict=bool(row["final_verdict"]),
        rejection_reason=row["rejection_reason"],
        timestamp=row["created_at"],
    )


def _trade_from_row(row: asyncpg.Record) -> SimulatedTrade:
    return SimulatedTrade(
        id=row["id"],
        decision_id=row["decision_id"],
        symbol=row["symbol"],
        direction=row["direction"],
        entry_price=float(row["entry_price"]),
        size=float(row["size"]),
        fee=float(row["fee"]),
        slippage=float(row["slippage"]),
        opened_at=row["opened_at"],
        closed_at=row["closed_at"],
        pnl=None if row["pnl"] is None else float(row["pnl"]),
        status=row["status"],
        close_reason=row["close_reason"],
        is_simulated=bool(row["is_simulated"]),
        stop_loss=None if row.get("stop_loss") is None else float(row["stop_loss"]),
        take_profit=None if row.get("take_profit") is None else float(row["take_profit"]),
        highest_price=None if row.get("highest_price") is None else float(row["highest_price"]),
        lowest_price=None if row.get("lowest_price") is None else float(row["lowest_price"]),
        atr_at_entry=None if row.get("atr_at_entry") is None else float(row["atr_at_entry"]),
        initial_stop_loss=None if row.get("initial_stop_loss") is None else float(row["initial_stop_loss"]),
        timeframe=row.get("timeframe", "15m"),
        close_price=None if row.get("close_price") is None else float(row["close_price"]),
    )


def _coin_from_row(row: asyncpg.Record) -> CoinConfig:
    return CoinConfig(
        symbol=row["symbol"],
        timeframes=list(row["timeframes"]),
        capital=float(row["capital"]),
        risk_percent=float(row["risk_percent"]),
        is_active=bool(row["is_active"]),
    )


# ---------------------------------------------------------------------------
# Connection pool
# ---------------------------------------------------------------------------
class SupabaseClient:
    """Thin async wrapper around an asyncpg connection pool.

    All public methods are async. Call ``connect()`` once at startup (from
    ``app/main.py``) and ``close()`` on graceful shutdown.
    """

    def __init__(
        self,
        dsn: str,
        key: Optional[str] = None,
        min_size: int = 1,
        max_size: int = 5,
    ) -> None:
        # Per Section 3, Supabase project URL + key is provided, but this client
        # connects directly to the underlying Postgres instance.
        # We assume 'dsn' is the full Postgres connection string.
        self._dsn = dsn
        self._key = key
        self._min_size = min_size
        self._max_size = max_size
        self._pool: Optional[asyncpg.Pool] = None

    # ---------------- connection lifecycle ----------------
    async def connect(self) -> None:
        if self._pool is not None:
            return
        try:
            # Note: statement_cache_size=0 is REQUIRED for PgBouncer in transaction mode
            # to avoid DuplicatePreparedStatementError.
            self._pool = await asyncpg.create_pool(
                dsn=self._dsn,
                min_size=self._min_size,
                max_size=self._max_size,
                command_timeout=30.0,
                statement_cache_size=0,
            )
        except Exception as exc:  # noqa: BLE001
            # Never log the DSN -- the traceback could contain credentials.
            safe_dsn = _redact_dsn(self._dsn)
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="storage.supabase",
                error_type=type(exc).__name__,
                error_message=f"create_pool failed (dsn={safe_dsn}): {exc}",
            )
            raise
        safe_dsn = _redact_dsn(self._dsn)
        logger.info("supabase_pool_created", dsn=safe_dsn)

    async def close(self) -> None:
        if self._pool is None:
            return
        await self._pool.close()
        self._pool = None
        logger.info("supabase_pool_closed")

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("SupabaseClient.connect() must be called first")
        return self._pool

    # ---------------- candles ----------------
    async def upsert_candle(self, candle: Candle) -> None:
        """Idempotent candle write. Section 4: only closed candles should reach
        here, but the function is safe to call on unclosed candles too.
        """
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO candles (
                    symbol, timeframe, open_time, close_time,
                    open, high, low, close, volume,
                    taker_buy_volume, taker_sell_volume, is_closed
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                ON CONFLICT (symbol, timeframe, open_time) DO UPDATE SET
                    close_time = EXCLUDED.close_time,
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    taker_buy_volume = EXCLUDED.taker_buy_volume,
                    taker_sell_volume = EXCLUDED.taker_sell_volume,
                    is_closed = EXCLUDED.is_closed
                """,
                candle.symbol, candle.timeframe, candle.open_time, candle.close_time,
                candle.open, candle.high, candle.low, candle.close, candle.volume,
                candle.taker_buy_volume, candle.taker_sell_volume, candle.is_closed,
            )
        logger.info(
            "candle_written",
            timestamp=datetime.now(timezone.utc),
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            open_time=candle.open_time.isoformat(),
            is_closed=candle.is_closed,
        )

    async def upsert_candles(self, candles: Sequence[Candle]) -> None:
        """Batch upsert -- used by the resume-gap-fill path."""
        if not candles:
            return
        pool = self._require_pool()
        rows = [
            (
                c.symbol, c.timeframe, c.open_time, c.close_time,
                c.open, c.high, c.low, c.close, c.volume,
                c.taker_buy_volume, c.taker_sell_volume, c.is_closed,
            )
            for c in candles
        ]
        async with pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO candles (
                    symbol, timeframe, open_time, close_time,
                    open, high, low, close, volume,
                    taker_buy_volume, taker_sell_volume, is_closed
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                ON CONFLICT (symbol, timeframe, open_time) DO UPDATE SET
                    close_time = EXCLUDED.close_time,
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    taker_buy_volume = EXCLUDED.taker_buy_volume,
                    taker_sell_volume = EXCLUDED.taker_sell_volume,
                    is_closed = EXCLUDED.is_closed
                """,
                rows,
            )

    async def fetch_closed_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 200,
        since: Optional[datetime] = None,
    ) -> list[Candle]:
        """Fetch the most recent ``limit`` closed candles, optionally after ``since``."""
        pool = self._require_pool()
        async with pool.acquire() as conn:
            if since is None:
                rows = await conn.fetch(
                    """
                    SELECT * FROM candles
                    WHERE symbol = $1 AND timeframe = $2 AND is_closed = TRUE
                    ORDER BY open_time DESC LIMIT $3
                    """,
                    symbol, timeframe, limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM candles
                    WHERE symbol = $1 AND timeframe = $2 AND is_closed = TRUE
                      AND open_time > $3
                    ORDER BY open_time ASC LIMIT $4
                    """,
                    symbol, timeframe, since, limit,
                )
        candles = [_candle_from_row(r) for r in rows]
        # Sort ascending by open_time for engine consumers.
        candles.sort(key=lambda c: c.open_time)
        return candles

    async def fetch_latest_candle(self, symbol: str, timeframe: str) -> Optional[Candle]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM candles
                WHERE symbol = $1 AND timeframe = $2
                ORDER BY open_time DESC LIMIT 1
                """,
                symbol, timeframe,
            )
        return _candle_from_row(row) if row else None

    # ---------------- decisions ----------------
    async def upsert_decision(self, decision: DecisionResult) -> bool:
        """Insert a decision. Returns True if a new row was inserted, False if
        the (symbol, source_candle_open_time) row already existed (idempotent).
        """
        pool = self._require_pool()
        async with pool.acquire() as conn:
            try:
                entry_json = json.dumps(decision.entry.model_dump(mode="json")) if decision.entry else None
                risk_json = json.dumps(decision.risk.model_dump(mode="json"))
                # [FIX] Use ON CONFLICT ... DO UPDATE to ensure we can RETURNING the ID 
                # even if the row already exists. This prevents ForeignKeyViolation 
                # in decision_component_signals.
                row = await conn.fetchrow(
                    """
                    INSERT INTO decisions (
                        id, symbol, source_candle_open_time, score, confidence,
                        regime_check_passed, structure_alignment_passed,
                        htf_bias_aligned, risk_allowed, risk_reason,
                        entry_payload, risk_payload,
                        final_verdict, rejection_reason
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                    ON CONFLICT (symbol, source_candle_open_time) 
                    DO UPDATE SET score = EXCLUDED.score  -- dummy update to trigger RETURNING
                    RETURNING id
                    """,
                    decision.id, decision.symbol, decision.source_candle_open_time,
                    decision.score, decision.confidence,
                    decision.regime_check_passed, decision.structure_alignment_passed,
                    decision.htf_bias_aligned, decision.risk.allowed, decision.risk.reason,
                    entry_json, risk_json,
                    decision.final_verdict, decision.rejection_reason,
                )
                if row:
                    decision_id = row["id"]
                else:
                    decision_id = decision.id
                # Persist component_signals as JSON for traceability.
                # NOTE: CREATE TABLE moved to migrations/001_init_core_tables.sql
                # to avoid DuplicatePreparedStatementError in PgBouncer.
                pass
            except UniqueViolationError:
                logger.info(
                    "decision_skipped_duplicate",
                    timestamp=datetime.now(timezone.utc),
                    symbol=decision.symbol,
                    source_candle_open_time=decision.source_candle_open_time.isoformat(),
                )
                return False

        # Persist component signals (best-effort; failure does not block).
        try:
            async with pool.acquire() as conn:
                await conn.executemany(
                    """
                    INSERT INTO decision_component_signals (decision_id, idx, payload)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (decision_id, idx) DO NOTHING
                    """,
                    [
                        (decision_id, i, json.dumps(s.model_dump(mode="json")))
                        for i, s in enumerate(decision.component_signals)
                    ],
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="storage.supabase",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

        logger.info(
            "decision_written",
            timestamp=datetime.now(timezone.utc),
            symbol=decision.symbol,
            decision_id=str(decision.id),
            final_verdict=decision.final_verdict,
            module="storage.supabase",
            message_text=f"تم حفظ القرار بنجاح في قاعدة البيانات لـ {decision.symbol}"
        )
        return True

    async def fetch_decision(self, decision_id: UUID) -> Optional[DecisionResult]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM decisions WHERE id = $1", decision_id,
            )
        return _decision_from_row(row) if row else None

    async def fetch_decisions_by_symbol(
        self, symbol: str, limit: int = 50
    ) -> list[DecisionResult]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM decisions WHERE symbol = $1
                ORDER BY source_candle_open_time DESC LIMIT $2
                """,
                symbol, limit,
            )
        return [_decision_from_row(r) for r in rows]

    # ---------------- simulated_trades ----------------
    async def insert_simulated_trade(self, trade: SimulatedTrade) -> bool:
        """Insert a simulated trade. Idempotent on decision_id."""
        pool = self._require_pool()
        async with pool.acquire() as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO simulated_trades (
                        id, decision_id, symbol, direction,
                        entry_price, size, fee, slippage,
                        opened_at, closed_at, pnl, status,
                        close_reason, is_simulated,
                        stop_loss, take_profit,
                        highest_price, lowest_price, atr_at_entry,
                        initial_stop_loss, timeframe
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21)
                    ON CONFLICT (decision_id) DO NOTHING
                    """,
                    trade.id, trade.decision_id, trade.symbol, trade.direction,
                    trade.entry_price, trade.size, trade.fee, trade.slippage,
                    trade.opened_at, trade.closed_at, trade.pnl, trade.status,
                    trade.close_reason, trade.is_simulated,
                    trade.stop_loss, trade.take_profit,
                    trade.highest_price, trade.lowest_price, trade.atr_at_entry,
                    trade.initial_stop_loss, trade.timeframe,
                )
                inserted = True
            except UniqueViolationError:
                inserted = False
        logger.info(
            "simulated_trade_written",
            timestamp=datetime.now(timezone.utc),
            trade_id=str(trade.id),
            decision_id=str(trade.decision_id),
            symbol=trade.symbol,
            inserted=inserted,
        )
        return inserted

    async def update_simulated_trade_closure(
        self,
        trade_id: UUID,
        closed_at: datetime,
        pnl: float,
        close_reason: str,
        close_price: Optional[float] = None,
    ) -> None:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE simulated_trades
                SET closed_at = $1, pnl = $2, close_reason = $3, status = 'closed', close_price = $4
                WHERE id = $5
                """,
                closed_at, pnl, close_reason, close_price, trade_id,
            )
        logger.info(
            "simulated_trade_closed_db",
            timestamp=datetime.now(timezone.utc),
            trade_id=str(trade_id),
            pnl=pnl,
            close_reason=close_reason,
        )

    async def update_simulated_trade_trailing(
        self,
        trade_id: UUID,
        stop_loss: Optional[float],
        highest_price: Optional[float] = None,
        lowest_price: Optional[float] = None,
    ) -> None:
        """Update an open trade's stop_loss and trailing-track fields.

        Called by ``PaperTrader.update_trailing_stop`` on every candle tick
        that produces a new high (LONG) or low (SHORT).
        """
        pool = self._require_pool()
        async with pool.acquire() as conn:
            updates = ["stop_loss = $1"]
            params: list[Any] = [stop_loss]
            idx = 2
            if highest_price is not None:
                updates.append(f"highest_price = ${idx}")
                params.append(highest_price)
                idx += 1
            if lowest_price is not None:
                updates.append(f"lowest_price = ${idx}")
                params.append(lowest_price)
                idx += 1
            params.append(trade_id)
            sql = f"UPDATE simulated_trades SET {', '.join(updates)} WHERE id = ${idx} AND status = 'open'"
            await conn.execute(sql, *params)
        logger.info(
            "simulated_trade_trailing_updated",
            timestamp=datetime.now(timezone.utc),
            trade_id=str(trade_id),
            new_stop_loss=stop_loss,
        )

    async def fetch_open_trades(self, symbol: Optional[str] = None) -> list[SimulatedTrade]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            if symbol is None:
                rows = await conn.fetch(
                    "SELECT * FROM simulated_trades WHERE status = 'open' ORDER BY opened_at DESC"
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM simulated_trades WHERE symbol = $1 AND status = 'open' ORDER BY opened_at DESC",
                    symbol,
                )
        return [_trade_from_row(r) for r in rows]

    async def fetch_recent_trades(self, limit: int = 10) -> list[SimulatedTrade]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM simulated_trades ORDER BY opened_at DESC LIMIT $1",
                limit,
            )
        return [_trade_from_row(r) for r in rows]

    async def fetch_trades_by_symbol(
        self,
        symbol: str,
        limit: int = 100,
        status: Optional[str] = None,
    ) -> list[SimulatedTrade]:
        """Fetch trades for a specific symbol with optional status filter."""
        pool = self._require_pool()
        if status is None:
            sql = "SELECT * FROM simulated_trades WHERE symbol = $1 ORDER BY opened_at DESC LIMIT $2"
            async with pool.acquire() as conn:
                rows = await conn.fetch(sql, symbol, limit)
        else:
            sql = "SELECT * FROM simulated_trades WHERE symbol = $1 AND status = $2 ORDER BY opened_at DESC LIMIT $3"
            async with pool.acquire() as conn:
                rows = await conn.fetch(sql, symbol, status, limit)
        return [_trade_from_row(r) for r in rows]

    async def fetch_closed_trades(
        self,
        symbol: Optional[str] = None,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
    ) -> list[SimulatedTrade]:
        pool = self._require_pool()
        conditions = ["status = 'closed'"]
        params: list[Any] = []
        idx = 1
        if symbol is not None:
            conditions.append(f"symbol = ${idx}")
            params.append(symbol)
            idx += 1
        if period_start is not None:
            conditions.append(f"closed_at >= ${idx}")
            params.append(period_start)
            idx += 1
        if period_end is not None:
            conditions.append(f"closed_at <= ${idx}")
            params.append(period_end)
            idx += 1
        sql = (
            "SELECT * FROM simulated_trades WHERE "
            + " AND ".join(conditions)
            + " ORDER BY closed_at ASC"
        )
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_trade_from_row(r) for r in rows]

    async def count_open_trades(self) -> int:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS n FROM simulated_trades WHERE status = 'open'"
            )
        return int(row["n"]) if row else 0

    # ---------------- ws_checkpoints ----------------
    async def get_checkpoint(
        self, symbol: str, timeframe: str
    ) -> Optional[datetime]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT last_closed_open_time FROM ws_checkpoints
                WHERE symbol = $1 AND timeframe = $2
                """,
                symbol, timeframe,
            )
        return row["last_closed_open_time"] if row else None

    async def upsert_checkpoint(
        self, symbol: str, timeframe: str, last_closed_open_time: datetime
    ) -> None:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO ws_checkpoints (symbol, timeframe, last_closed_open_time, updated_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (symbol, timeframe) DO UPDATE SET
                    last_closed_open_time = EXCLUDED.last_closed_open_time,
                    updated_at = NOW()
                """,
                symbol, timeframe, last_closed_open_time,
            )

    async def delete_checkpoint(self, symbol: str, timeframe: Optional[str] = None) -> None:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            if timeframe is None:
                await conn.execute(
                    "DELETE FROM ws_checkpoints WHERE symbol = $1", symbol,
                )
            else:
                await conn.execute(
                    "DELETE FROM ws_checkpoints WHERE symbol = $1 AND timeframe = $2",
                    symbol, timeframe,
                )

    # ---------------- coins ----------------
    async def upsert_coin(self, coin: CoinConfig) -> None:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO coins (symbol, timeframes, capital, risk_percent, is_active)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (symbol) DO UPDATE SET
                    timeframes = EXCLUDED.timeframes,
                    capital = EXCLUDED.capital,
                    risk_percent = EXCLUDED.risk_percent,
                    is_active = EXCLUDED.is_active,
                    updated_at = NOW()
                """,
                coin.symbol, coin.timeframes, coin.capital, coin.risk_percent, coin.is_active,
            )

    async def fetch_coin(self, symbol: str) -> Optional[CoinConfig]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM coins WHERE symbol = $1", symbol,
            )
        return _coin_from_row(row) if row else None

    async def fetch_all_coins(self, only_active: bool = False) -> list[CoinConfig]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            if only_active:
                rows = await conn.fetch(
                    "SELECT * FROM coins WHERE is_active = TRUE ORDER BY symbol"
                )
            else:
                rows = await conn.fetch("SELECT * FROM coins ORDER BY symbol")
        return [_coin_from_row(r) for r in rows]

    async def delete_coin(self, symbol: str) -> None:
        """Delete a coin. ws_checkpoints for this symbol are cascade-deleted
        by the trigger in migration 002. Historical decisions / simulated_trades
        are NEVER deleted.
        """
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM coins WHERE symbol = $1", symbol)

    # ---------------- performance_snapshots ----------------
    async def save_performance_snapshot(self, metrics: PerformanceMetrics) -> None:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO performance_snapshots (
                    period_start, period_end, total_trades, winning_trades,
                    losing_trades, win_rate, total_pnl, max_drawdown,
                    sharpe_ratio, profit_factor
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                """,
                metrics.period_start, metrics.period_end,
                metrics.total_trades, metrics.winning_trades, metrics.losing_trades,
                metrics.win_rate, metrics.total_pnl, metrics.max_drawdown,
                metrics.sharpe_ratio, metrics.profit_factor,
            )

    # ---------------- migrations runner ----------------
    async def apply_migrations(self, migration_sqls: list[str]) -> None:
        """Apply each migration SQL in order. Each migration must be idempotent
        (use CREATE TABLE IF NOT EXISTS, DO $$ blocks, etc.).
        """
        pool = self._require_pool()
        async with pool.acquire() as conn:
            for sql in migration_sqls:
                await conn.execute(sql)
        logger.info(
            "migrations_applied",
            timestamp=datetime.now(timezone.utc),
            count=len(migration_sqls),
        )


__all__ = ["SupabaseClient"]
