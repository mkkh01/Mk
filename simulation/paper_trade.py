"""
File: simulation/paper_trade.py
1. Single Responsibility: Open, monitor, and close *simulated* trades based on
   approved ``DecisionResult`` objects and live ``Candle`` updates.  All writes
   go to the ``simulated_trades`` table via ``storage/supabase.py``.
2. Consumes: ``DecisionResult``, ``EntrySignal``, ``RiskAssessment``
   (contracts/decision.py); ``SimulatedTrade`` (contracts/simulation.py);
   ``Candle`` (contracts/market.py); ``SupabaseClient`` (storage/supabase.py);
   ``simulation/fees.py`` and ``simulation/slippage.py``.
3. Produces: ``SimulatedTrade`` objects (open or closed) via the public
   ``PaperTrader`` methods; persisted rows in the ``simulated_trades`` table.
4. Downstream: engine/orchestrator.py (calls ``open_trade`` after a positive
   verdict and ``check_trade_closure`` on every closed candle); bot/
   telegram_bot.py (calls ``close_all_open`` on engine stop, displays open
   trades in the Trade History button); portfolio/performance.py (reads the
   closed rows this module writes).
5. New Dependencies: numpy (already in requirements.txt; used only for fsafe
   NaN checks).  No new packages.
6. Touches Section 6 bugs? No.  Section 0 hard-constraint 7 is enforced
   everywhere: ``is_simulated`` is hard-coded to ``True`` on every
   ``SimulatedTrade`` this module constructs, every log event carries the
   ``is_simulated=True`` field, and no user-facing string ever uses the words
   "live" or "executed".  The ``_SIMULATED_LABEL`` constant below is the
   single source of truth for the simulation banner.
7. Tests: tests/unit/test_simulation.py exercises:
       * open_trade creates a SimulatedTrade with is_simulated=True, status=open,
         fee/slippage computed via fees.py / slippage.py
       * open_trade with decision.entry=None raises ValueError (no silent skip)
       * check_trade_closure long TP hit (candle.high >= take_profit)
       * check_trade_closure long SL hit (candle.low <= stop_loss)
       * check_trade_closure short TP hit (candle.low <= take_profit)
       * check_trade_closure short SL hit (candle.high >= stop_loss)
       * check_trade_closure SL-TP-same-candle resolves to SL (conservative)
       * check_trade_closure no-hit returns None and does not call the DB
       * check_trade_closure with stop_loss=None / take_profit=None skips that
         leg gracefully (no crash, no DB write)
       * close_trade_manual persists with reason="manual" / "time"
       * close_all_open iterates every open trade and closes each
       * check_all_open_trades iterates every open trade and closes those
         that hit TP/SL
       * Idempotency: re-opening the same decision_id does not duplicate.
8. Logging: ``simulated_trade_opened`` and ``simulated_trade_closed`` per the
   monitoring/logger.py event catalog.  Both events carry ``is_simulated=True``.
   Debug-level ``simulated_trade_skip_no_entry`` / ``simulated_trade_skip_no_hit``
   events provide operator visibility without flooding the info stream.
9. Dependency Order: config -> contracts/* -> monitoring/logger.py ->
   storage/supabase.py -> simulation/fees.py -> simulation/slippage.py ->
   simulation/paper_trade.py (no upstream violations).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import UUID

import numpy as np

from config.thresholds import TAKER_FEE_PCT, VOLATILITY_ATR_PERIOD  # noqa: F401 -- re-exported for tests
from config.thresholds import (
    TRAILING_ENABLED,
    TRAILING_ACTIVATION_MULTIPLIER,
    TRAILING_ATR_DISTANCE,
    TRAILING_MIN_DISTANCE_PCT,
    TRAILING_MAX_DISTANCE_PCT,
)
from market.volatility import calculate_atr
from contracts.decision import DecisionResult
from contracts.market import Candle
from contracts.simulation import SimulatedTrade
from monitoring.logger import get_logger
from simulation import fees as fees_mod
from simulation import slippage as slippage_mod
from simulation.fees import calculate_fee
from simulation.slippage import estimate_slippage
from storage.supabase import SupabaseClient
from monitoring.health_manager import health_manager, HealthStatus

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants -- Section 0 hard-constraint 7 enforcement
# ---------------------------------------------------------------------------
_SIMULATED_LABEL = "SIMULATION ONLY"
"""Banner appended to every user-facing string produced by this module.

Any future code that builds a Telegram message from a SimulatedTrade must
include this label verbatim.  This makes a future grep trivial: ``grep -r
"live" simulation/`` returns nothing; ``grep -r "executed" simulation/``
returns nothing.
"""

_DEFAULT_CLOSE_REASON = "manual"
"""Default close reason when ``close_trade_manual`` is invoked without one."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _utcnow() -> datetime:
    """Return a timezone-aware UTC ``datetime`` (Render-safe)."""
    return datetime.now(timezone.utc)


def _safe_float(value: float) -> float:
    """Coerce to finite float; NaN/inf -> 0.0 (Section 22 graceful degradation)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not np.isfinite(f):
        return 0.0
    return f


def _compute_pnl(
    direction: Literal["long", "short"],
    entry_price: float,
    close_price: float,
    size: float,
    fee: float,
    slippage: float,
) -> float:
    """Compute the realised PnL of a closed simulated trade.

    Per Section 18, the formula is::

        long:  pnl = (close_price - entry_price) * size - fee - slippage
        short: pnl = (entry_price - close_price) * size - fee - slippage

    where ``fee`` and ``slippage`` are the *entry-side* costs stored on the
    trade (``trade.fee`` and ``trade.slippage``).  The simulator models the
    full round-trip cost on the entry side as a simplification -- this is the
    formula explicitly written in the spec and it is the one Section 10
    acceptance tests verify against.

    The function is defensive: non-finite inputs are coerced to 0.0 so a
    malformed trade cannot poison the portfolio calculation downstream
    (Section 22 Engine Level -- division by zero / NaN must not propagate).
    """
    ep = _safe_float(entry_price)
    cp = _safe_float(close_price)
    sz = _safe_float(size)
    f = _safe_float(fee)
    s = _safe_float(slippage)
    if direction == "long":
        gross = (cp - ep) * sz
    else:
        # Spot-only: only long trades are supported.
        logger.warning(
            "pnl_invalid_direction_for_spot",
            direction=direction,
            entry_price=ep,
            close_price=cp,
            size=sz,
        )
        gross = 0.0
    pnl = gross - f - s
    if not np.isfinite(pnl):
        logger.warning(
            "pnl_non_finite",
            direction=direction,
            entry_price=ep,
            close_price=cp,
            size=sz,
            fee=f,
            slippage=s,
        )
        return 0.0
    return pnl


def _resolve_close_price(
    trade: SimulatedTrade, current_candle: Candle
) -> Optional[tuple[float, Literal["tp", "sl"]]]:
    """Inspect ``current_candle`` against ``trade`` SL/TP and return the close.

    Returns:
        ``(close_price, close_reason)`` if the trade should be closed by this
        candle, or ``None`` if neither SL nor TP was hit.

    Closure rules (Section 18):

    * **Long:**
        * ``current_candle.low <= trade.stop_loss`` -> close at SL price.
        * ``current_candle.high >= trade.take_profit`` -> close at TP price.
    * **Short:**
        * ``current_candle.high >= trade.stop_loss`` -> close at SL price.
        * ``current_candle.low <= trade.take_profit`` -> close at TP price.

    If both SL and TP would be hit in the same candle (a candle that wicks
    both above TP and below SL), the **stop loss is assumed to have hit
    first** (worst-case / conservative).  This matches how a real broker
    would behave if both orders rested on the book -- the protective stop is
    typically closer to the entry and triggers first on a violent wick.

    A ``None`` stop_loss or take_profit (e.g. a manually-opened trade without
    SL/TP) silently disables that leg of the check.
    """
    direction = trade.direction
    sl = trade.stop_loss
    tp = trade.take_profit
    low = _safe_float(current_candle.low)
    high = _safe_float(current_candle.high)

    if direction == "long":
        # Check SL first (conservative for ambiguous wicks).
        if sl is not None and low <= float(sl):
            return (float(sl), "sl")
        if tp is not None and high >= float(tp):
            return (float(tp), "tp")
        return None
    else:
        # Spot-only: only long trades are supported.
        logger.warning(
            "resolve_close_invalid_direction_for_spot",
            trade_id=str(trade.id),
            direction=direction,
        )
        return None


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------
class PaperTrader:
    """Open, monitor, and close simulated trades.

    The trader is a *thin recorder* over ``SupabaseClient``.  It contains no
    risk logic (Section 0 hard-constraint 3), no trading/scoring logic, and
    no Telegram UI code.  It only:

    1. Takes an approved ``DecisionResult`` and persists a ``SimulatedTrade``.
    2. Inspects live ``Candle`` updates to decide if an open trade should be
       closed at its SL or TP.
    3. Provides manual / batch close helpers for engine shutdown and
       operational overrides.

    Every ``SimulatedTrade`` produced by this class has
    ``is_simulated=True`` hard-coded; no method accepts a flag to disable it.
    """

    def __init__(self, supabase: SupabaseClient) -> None:
        """Construct a PaperTrader.

        Args:
            supabase: An *already-connected* ``SupabaseClient``.  The trader
                does not call ``connect()`` itself; that is the responsibility
                of ``app/main.py`` per the Section 1 startup contract.
        """
        self._supabase = supabase

    # ----------------------- open -------------------------------------------
    async def open_trade(self, decision: DecisionResult) -> SimulatedTrade:
        """Open a simulated trade from an approved ``DecisionResult``.

        Steps (Section 18 ``open_trade`` algorithm):

        1. Extract entry info from ``decision.entry``:
           symbol, direction, entry_price, size, stop_loss, take_profit.
           The size comes from ``decision.risk.max_position_size`` (the
           ``EntrySignal`` itself carries no size field per contracts/
           decision.py).  The symbol comes from ``decision.symbol`` for
           consistency with the decision record.
        2. Calculate entry fee via ``fees.calculate_fee`` (default taker --
           Section 18 conservative default).
        3. Calculate entry slippage via ``slippage.estimate_slippage``.
        4. Build a ``SimulatedTrade`` with ``id=uuid4()``,
           ``decision_id=decision.id``, ``status="open"``,
           ``is_simulated=True`` (HARD-CODED), and the SL/TP from the entry.
        5. Persist via ``supabase.insert_simulated_trade`` (idempotent on
           ``decision_id``).
        6. Log ``simulated_trade_opened`` with all fields.
        7. Return the ``SimulatedTrade``.

        Args:
            decision: An approved ``DecisionResult`` with ``final_verdict``
                ``True`` and a non-None ``entry``.  If ``decision.entry`` is
                ``None``, ``ValueError`` is raised -- the simulator must not
                silently open a trade with no entry signal.

        Returns:
            The persisted ``SimulatedTrade`` (``status="open"``).

        Raises:
            ValueError: if ``decision.entry`` is ``None`` or
                ``decision.risk.allowed`` is ``False`` (defence in depth --
                the orchestrator should have already rejected these, but we
                refuse to record a trade that contradicts the risk decision).
        """
        if decision.entry is None:
            logger.warning(
                "simulated_trade_skip_no_entry",
                timestamp=_utcnow().isoformat(),
                decision_id=str(decision.id),
                symbol=decision.symbol,
                final_verdict=decision.final_verdict,
            )
            raise ValueError(
                f"DecisionResult {decision.id} has no EntrySignal; cannot open a "
                f"simulated trade."
            )
        if not decision.risk.allowed:
            logger.warning(
                "simulated_trade_skip_risk_rejected",
                timestamp=_utcnow().isoformat(),
                decision_id=str(decision.id),
                symbol=decision.symbol,
                risk_reason=decision.risk.reason,
            )
            raise ValueError(
                f"DecisionResult {decision.id} was not approved by risk "
                f"(reason={decision.risk.reason!r}); cannot open a simulated trade."
            )

        entry = decision.entry
        symbol = decision.symbol
        direction = entry.direction
        entry_price = _safe_float(entry.entry_price)
        # Size comes from the risk assessment -- EntrySignal has no size field.
        size = _safe_float(decision.risk.max_position_size)
        stop_loss = (
            float(entry.stop_loss) if entry.stop_loss is not None else None
        )
        take_profit = (
            float(entry.take_profit) if entry.take_profit is not None else None
        )

        # Conservative: default taker for the entry leg (Section 18).
        fee = calculate_fee(entry_price, size, is_maker=False)
        slippage = estimate_slippage(entry_price, size, symbol)

        # Extract timeframe from entry signal.
        timeframe = entry.timeframe if hasattr(entry, "timeframe") else "15m"

        # Calculate initial ATR for trailing-stop distance reference.
        atr_value = await self._compute_atr_async(symbol, timeframe)

        # Initialise trailing-track fields (Spot-only: only highest is tracked).
        initial_highest = entry_price

        trade = SimulatedTrade(
            decision_id=decision.id,
            symbol=symbol,
            direction="long", # Force long for Spot
            entry_price=entry_price,
            size=size,
            fee=fee,
            slippage=slippage,
            opened_at=_utcnow(),
            status="open",
            close_reason=None,
            is_simulated=True,  # HARD-CODED -- Section 0 hard-constraint 7.
            stop_loss=stop_loss,
            take_profit=take_profit,
            highest_price=initial_highest,
            lowest_price=None,
            atr_at_entry=atr_value if atr_value > 0 else None,
            initial_stop_loss=stop_loss,
            timeframe=timeframe,
        )

        # Persist (idempotent on decision_id at the DB level).
        try:
            await self._supabase.insert_simulated_trade(trade)
            await health_manager.increment_stat("trades_simulated")
            await health_manager.update_component(
                "PaperTrader", 
                HealthStatus.OK, 
                f"Opened simulated trade for {symbol}",
                {"trade_id": str(trade.id), "symbol": symbol}
            )
        except Exception as exc:  # noqa: BLE001
            # Section 22 (Storage Level): foreign key violation -> log error,
            # skip trade write.  We re-raise as RuntimeError so the caller
            # (orchestrator) can decide whether to retry / skip the cycle.
            await health_manager.update_component(
                "PaperTrader", 
                HealthStatus.ERROR, 
                f"Failed to open simulated trade for {symbol}: {exc}",
                {"symbol": symbol}
            )
            logger.error(
                "error",
                timestamp=_utcnow(),
                module="simulation.paper_trade",
                error_type=type(exc).__name__,
                error_message=str(exc),
                trade_id=str(trade.id),
                decision_id=str(decision.id),
                symbol=symbol,
            )
            raise RuntimeError(
                f"Failed to persist simulated trade for decision {decision.id}"
            ) from exc

        logger.info(
            "simulated_trade_opened",
            timestamp=_utcnow(),
            trade_id=str(trade.id),
            decision_id=str(decision.id),
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            size=size,
            fee=fee,
            slippage=slippage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            timeframe=timeframe,
            is_simulated=True,  # Section 0 hard-constraint 7.
            label=_SIMULATED_LABEL,
        )
        return trade

    # ----------------------- closure check ---------------------------------
    async def check_trade_closure(
        self, trade: SimulatedTrade, current_candle: Candle
    ) -> Optional[SimulatedTrade]:
        """Check whether ``trade`` should be closed by ``current_candle``.

        Closure rules are documented on :func:`_resolve_close_price`.  If the
        candle hits SL or TP, the trade is closed at the corresponding level
        (SL price or TP price), the PnL is computed, and the row is updated
        via ``supabase.update_simulated_trade_closure``.

        Args:
            trade: An *open* ``SimulatedTrade``.  If ``trade.status`` is
                already ``"closed"``, the function returns ``None`` without
                touching the DB (idempotent -- prevents double-closure if the
                same candle is processed twice on resume).
            current_candle: The most recent *closed* candle for the trade's
                symbol.  The caller is responsible for filtering unclosed
                candles (Section 6 Bug 3).

        Returns:
            The updated ``SimulatedTrade`` (``status="closed"``) if the candle
            triggered a closure, or ``None`` if the trade remains open.
        """
        if trade.status == "closed":
            # Idempotent: do not re-close an already-closed trade.
            logger.debug(
                "simulated_trade_already_closed",
                trade_id=str(trade.id),
                symbol=trade.symbol,
            )
            return None

        resolution = _resolve_close_price(trade, current_candle)
        if resolution is None:
            logger.debug(
                "simulated_trade_skip_no_hit",
                trade_id=str(trade.id),
                symbol=trade.symbol,
                direction=trade.direction,
                candle_open_time=current_candle.open_time.isoformat(),
            )
            return None

        close_price, close_reason = resolution
        pnl = _compute_pnl(
            direction=trade.direction,
            entry_price=trade.entry_price,
            close_price=close_price,
            size=trade.size,
            fee=trade.fee,
            slippage=trade.slippage,
        )
        closed_at = current_candle.close_time

        # Persist the closure (DB-level update of closed_at / pnl /
        # close_reason / status).
        try:
            await self._supabase.update_simulated_trade_closure(
                trade_id=trade.id,
                closed_at=closed_at,
                pnl=pnl,
                close_reason=close_reason,
                close_price=close_price,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=_utcnow(),
                module="simulation.paper_trade",
                error_type=type(exc).__name__,
                error_message=str(exc),
                trade_id=str(trade.id),
                symbol=trade.symbol,
            )
            raise RuntimeError(
                f"Failed to persist closure for simulated trade {trade.id}"
            ) from exc

        # Update the in-memory object so the caller sees the final state.
        updated = trade.model_copy(
            update={
                "closed_at": closed_at,
                "pnl": pnl,
                "status": "closed",
                "close_reason": close_reason,
                "close_price": close_price,
            }
        )

        logger.info(
            "simulated_trade_closed",
            timestamp=_utcnow(),
            trade_id=str(updated.id),
            decision_id=str(updated.decision_id),
            symbol=updated.symbol,
            direction=updated.direction,
            entry_price=updated.entry_price,
            close_price=close_price,
            size=updated.size,
            fee=updated.fee,
            slippage=updated.slippage,
            pnl=pnl,
            close_reason=close_reason,
            closed_at=closed_at.isoformat(),
            opened_at=updated.opened_at.isoformat(),
            timeframe=updated.timeframe,
            is_simulated=True,  # Section 0 hard-constraint 7.
            label=_SIMULATED_LABEL,
        )
        return updated
    # ----------------------- manual close ----------------------------------
    async def close_trade_manual(
        self,
        trade_id: UUID,
        close_price: float,
        reason: Literal["time", "manual"] = _DEFAULT_CLOSE_REASON,
    ) -> SimulatedTrade:
        """Manually close an open simulated trade at ``close_price``.

        Used by:

        * ``close_all_open`` (engine shutdown) with ``reason="time"``.
        * The bot's "close trade" admin command (if ever added) with
          ``reason="manual"``.
        * The orchestrator's entry-timeout path with ``reason="time"``.

        Args:
            trade_id: UUID of the open trade to close.
            close_price: The price at which to close.  Caller is responsible
                for providing a sensible value (latest candle close, live
                ticker, etc.).
            reason: ``"manual"`` (default) or ``"time"``.  These are the only
                two non-SL/TP close reasons the contract allows.

        Returns:
            The updated ``SimulatedTrade`` (``status="closed"``).

        Raises:
            ValueError: if no open trade with ``trade_id`` exists, or if
                ``reason`` is not ``"manual"`` / ``"time"``.
        """
        if reason not in ("manual", "time"):
            raise ValueError(
                f"reason must be 'manual' or 'time', got {reason!r}"
            )

        # Fetch the open trade.  We use fetch_open_trades and filter rather
        # than adding a new SupabaseClient method (out of scope for this
        # task).  If the trade is not open, we raise -- a closed trade cannot
        # be re-closed.
        open_trades = await self._supabase.fetch_open_trades()
        trade: Optional[SimulatedTrade] = None
        for t in open_trades:
            if t.id == trade_id:
                trade = t
                break
        if trade is None:
            raise ValueError(
                f"No open simulated trade with id={trade_id} found."
            )

        cp = _safe_float(close_price)
        pnl = _compute_pnl(
            direction=trade.direction,
            entry_price=trade.entry_price,
            close_price=cp,
            size=trade.size,
            fee=trade.fee,
            slippage=trade.slippage,
        )
        closed_at = _utcnow()

        try:
            await self._supabase.update_simulated_trade_closure(
                trade_id=trade.id,
                closed_at=closed_at,
                pnl=pnl,
                close_reason=reason,
                close_price=cp,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=_utcnow(),
                module="simulation.paper_trade",
                error_type=type(exc).__name__,
                error_message=str(exc),
                trade_id=str(trade.id),
                symbol=trade.symbol,
            )
            raise RuntimeError(
                f"Failed to persist manual closure for simulated trade {trade.id}"
            ) from exc

        updated = trade.model_copy(
            update={
                "closed_at": closed_at,
                "pnl": pnl,
                "status": "closed",
                "close_reason": reason,
                "close_price": cp,
            }
        )

        logger.info(
            "simulated_trade_closed",
            timestamp=_utcnow(),
            trade_id=str(updated.id),
            decision_id=str(updated.decision_id),
            symbol=updated.symbol,
            direction=updated.direction,
            entry_price=updated.entry_price,
            close_price=cp,
            size=updated.size,
            fee=updated.fee,
            slippage=updated.slippage,
            pnl=pnl,
            close_reason=reason,
            closed_at=closed_at.isoformat(),
            opened_at=updated.opened_at.isoformat(),
            timeframe=updated.timeframe,
            is_simulated=True,  # Section 0 hard-constraint 7.
            label=_SIMULATED_LABEL,
        )
        return updated

    # ----------------------- batch close -----------------------------------
    async def close_all_open(
        self, current_prices: dict[str, float]
    ) -> list[SimulatedTrade]:
        """Close every open simulated trade at its symbol's current price.

        Called on engine shutdown (Section 7 Stop Engine flow) so the
        portfolio has a clean cutoff and no trade is left dangling across a
        restart.  The close reason is ``"time"`` (engine time-out).

        Args:
            current_prices: Mapping of ``symbol -> current_price``.  Trades
                whose symbol is missing from the map are *skipped* (not
                closed) and a warning is logged -- the caller is expected to
                provide a price for every symbol that has open trades.

        Returns:
            The list of ``SimulatedTrade`` objects that were closed (in
            arbitrary order).  Trades that were already closed or whose
            symbol had no price are not in the list.
        """
        open_trades = await self._supabase.fetch_open_trades()
        closed: list[SimulatedTrade] = []
        for trade in open_trades:
            price = current_prices.get(trade.symbol)
            if price is None:
                logger.warning(
                    "close_all_open_missing_price_fallback_to_entry",
                    timestamp=_utcnow(),
                    trade_id=str(trade.id),
                    symbol=trade.symbol,
                )
                price = trade.entry_price # Fallback to entry price to ensure closure

            try:
                updated = await self.close_trade_manual(
                    trade_id=trade.id,
                    close_price=float(price),
                    reason="time",
                )
                closed.append(updated)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "error",
                    timestamp=_utcnow(),
                    module="simulation.paper_trade",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    trade_id=str(trade.id),
                    symbol=trade.symbol,
                )
                # Continue with the next trade -- one failure must not block
                # the rest (Section 22 graceful degradation: "If one coin
                # fails: Continue processing other coins, log error for
                # failed coin").
                continue
        logger.info(
            "simulated_trade_batch_closed",
            timestamp=_utcnow(),
            closed_count=len(closed),
            attempted_count=len(open_trades),
            is_simulated=True,
        )
        return closed

    # ----------------------- batch closure check ---------------------------
    async def scan_and_close_open_trades(self) -> list[SimulatedTrade]:
        """Background task entry point: fetch latest candles and check all open trades.
        
        This simplifies the integration with app/main.py by handling the candle 
        lookup internally.
        """
        open_trades = await self._supabase.fetch_open_trades()
        if not open_trades:
            # Heartbeat for health manager even when no trades are open
            await health_manager.update_component(
                "PaperTrader", HealthStatus.OK, "No open trades to scan"
            )
            return []

        # Build a map of symbol -> latest candle
        # We use a composite key (symbol, timeframe) because different trades 
        # might use different timeframes for the same symbol.
        current_candles: dict[tuple[str, str], Candle] = {}
        for trade in open_trades:
            key = (trade.symbol, trade.timeframe)
            if key not in current_candles:
                candle = await self._supabase.fetch_latest_candle(trade.symbol, trade.timeframe)
                if candle:
                    current_candles[key] = candle

        # Update trailing stops before checking for closures.
        if TRAILING_ENABLED:
            await self.update_all_trailing_stops(current_candles)

        closed = await self.check_all_open_trades(current_candles)
        
        await health_manager.update_component(
            "PaperTrader", 
            HealthStatus.OK, 
            f"Scanned {len(open_trades)} trades, closed {len(closed)}"
        )
        return closed

    async def check_all_open_trades(
        self, current_candles: dict[tuple[str, str], Candle]
    ) -> list[SimulatedTrade]:
        """Run :meth:`check_trade_closure` for every open simulated trade.

        Called by the orchestrator on every closed-candle tick.  For each
        open trade, the latest closed candle for that trade's symbol is
        looked up in ``current_candles`` and passed to
        :meth:`check_trade_closure`.

        Args:
            current_candles: Mapping of ``(symbol, timeframe) -> Candle``.  Only *closed*
                candles should be passed (Section 6 Bug 3 -- the caller is
                responsible for filtering).  Trades whose symbol/timeframe is missing
                are skipped (warning logged).

        Returns:
            The list of trades that were closed by this tick (trades that
            remained open are not in the list).
        """
        open_trades = await self._supabase.fetch_open_trades()
        closed: list[SimulatedTrade] = []
        for trade in open_trades:
            candle = current_candles.get((trade.symbol, trade.timeframe))
            if candle is None:
                logger.warning(
                    "check_all_open_missing_candle",
                    timestamp=_utcnow(),
                    trade_id=str(trade.id),
                    symbol=trade.symbol,
                )
                continue
            try:
                updated = await self.check_trade_closure(trade, candle)
                if updated is not None:
                    closed.append(updated)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "error",
                    timestamp=_utcnow(),
                    module="simulation.paper_trade",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    trade_id=str(trade.id),
                    symbol=trade.symbol,
                )
                # Continue with the next trade -- one failure must not block
                # the rest.
                continue
        if closed:
            logger.info(
                "simulated_trade_batch_closed",
                timestamp=_utcnow(),
                closed_count=len(closed),
                attempted_count=len(open_trades),
                trigger="candle_check",
                is_simulated=True,
            )
        return closed

    # ----------------------- trailing stop ---------------------------------
    async def _compute_atr_async(self, symbol: str, timeframe: str = "15m") -> float:
        """Async wrapper: fetch closed candles and compute ATR."""
        try:
            candles = await self._supabase.fetch_closed_candles(
                symbol, timeframe, limit=VOLATILITY_ATR_PERIOD + 5
            )
            return calculate_atr(candles)
        except Exception as exc:
            logger.warning(
                "atr_fetch_failed",
                symbol=symbol,
                error=str(exc),
            )
            return 0.0

    async def update_trailing_stop(
        self,
        trade: SimulatedTrade,
        current_candle: Candle,
    ) -> Optional[SimulatedTrade]:
        """Update the trailing stop for an open trade based on current candle.

        Logic:

        1. Update the tracked extreme (highest_price for LONG, lowest_price
           for SHORT) if the current candle exceeds it.
        2. Check whether the unrealised profit has reached the activation
           threshold (initial risk x TRAILING_ACTIVATION_MULTIPLIER).
        3. If activated, compute a candidate trailing stop:
           - LONG:  ``highest_price - max(ATR * multiplier, min_pct * price)``
           - SHORT: ``lowest_price + max(ATR * multiplier, min_pct * price)``
        4. The candidate must also respect the maximum distance cap and must
           never move backwards (LONG stop can only rise; SHORT stop can
           only fall).
        5. If the candidate is better than the current stop_loss, persist
           the update.

        Args:
            trade: An open ``SimulatedTrade``.
            current_candle: The latest closed candle for the trade's symbol.

        Returns:
            The updated ``SimulatedTrade`` (in-memory copy) if the stop was
            moved, or ``None`` if no change was needed.
        """
        if not TRAILING_ENABLED:
            return None
        if trade.status == "closed":
            return None

        # --- Step 1: update extreme ---
        high = _safe_float(current_candle.high)
        low = _safe_float(current_candle.low)
        new_highest = trade.highest_price
        new_lowest = trade.lowest_price

        if trade.direction == "long":
            if new_highest is not None and high > new_highest:
                new_highest = high
            elif new_highest is None:
                new_highest = high
        else:
            # Spot-only: only long trades are supported.
            return None

        # --- Step 2: check activation threshold ---
        initial_risk = self._compute_initial_risk(trade)
        if initial_risk is None or initial_risk <= 0:
            return None

        if trade.direction == "long":
            current_price = high  # Use candle high for conservative check.
            unrealised = current_price - trade.entry_price
        else:
            return None

        activation_threshold = initial_risk * TRAILING_ACTIVATION_MULTIPLIER
        if unrealised < activation_threshold:
            return None

        # --- Step 3: compute candidate trailing stop ---
        atr = trade.atr_at_entry
        if atr is None or atr <= 0:
            # Fallback: use a fixed percentage of the current price.
            atr = trade.entry_price * TRAILING_MIN_DISTANCE_PCT / 100.0

        atr_distance = atr * TRAILING_ATR_DISTANCE
        pct_distance = (trade.entry_price * TRAILING_MIN_DISTANCE_PCT) / 100.0
        max_distance = (trade.entry_price * TRAILING_MAX_DISTANCE_PCT) / 100.0
        candidate_distance = max(atr_distance, pct_distance)
        # Cap at maximum distance.
        candidate_distance = min(candidate_distance, max_distance)

        if trade.direction == "long":
            if new_highest is None:
                return None
            candidate_sl = new_highest - candidate_distance
            # Must not move backwards (stop can only rise for LONG).
            current_sl = float(trade.stop_loss) if trade.stop_loss is not None else 0.0
            if candidate_sl <= current_sl:
                return None
            # Must not exceed the current high (stop must be below price).
            if candidate_sl >= new_highest:
                return None
        else:
            return None

        # --- Step 4: persist ---
        try:
            await self._supabase.update_simulated_trade_trailing(
                trade_id=trade.id,
                stop_loss=candidate_sl,
                highest_price=new_highest,
                lowest_price=None,
            )
        except Exception as exc:
            logger.error(
                "trailing_stop_update_failed",
                trade_id=str(trade.id),
                symbol=trade.symbol,
                error=str(exc),
            )
            return None

        # Update in-memory object.
        updated = trade.model_copy(
            update={
                "stop_loss": candidate_sl,
                "highest_price": new_highest,
                "lowest_price": new_lowest,
            }
        )

        logger.info(
            "simulated_trade_trailing_stop_updated",
            timestamp=_utcnow(),
            trade_id=str(trade.id),
            symbol=trade.symbol,
            direction=trade.direction,
            old_stop_loss=trade.stop_loss,
            new_stop_loss=candidate_sl,
            highest_price=new_highest,
            lowest_price=new_lowest,
            is_simulated=True,
            label=_SIMULATED_LABEL,
        )
        return updated

    async def update_all_trailing_stops(
        self,
        current_candles: dict[tuple[str, str], Candle],
    ) -> list[SimulatedTrade]:
        """Run :meth:`update_trailing_stop` for every open trade.

        This is called from :meth:`scan_and_close_open_trades` before the
        closure check so that the stop has been moved (if eligible) before
        we test whether the current candle hits it.
        
        Returns:
            List of trades that had their trailing stops updated.
        """
        open_trades = await self._supabase.fetch_open_trades()
        updated_trades: list[SimulatedTrade] = []
        for trade in open_trades:
            candle = current_candles.get((trade.symbol, trade.timeframe))
            if candle is None:
                continue
            try:
                result = await self.update_trailing_stop(trade, candle)
                if result is not None:
                    updated_trades.append(result)
            except Exception as exc:
                logger.error(
                    "trailing_stop_update_error",
                    timestamp=_utcnow(),
                    module="simulation.paper_trade",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    trade_id=str(trade.id),
                    symbol=trade.symbol,
                )
                continue
        if len(updated_trades) > 0:
            logger.info(
                "trailing_stop_batch_update",
                timestamp=_utcnow(),
                updated_count=len(updated_trades),
                total_open=len(open_trades),
                is_simulated=True,
            )
        return updated_trades

    @staticmethod
    def _compute_initial_risk(trade: SimulatedTrade) -> Optional[float]:
        """Compute the initial risk amount per unit (price-based).

        For LONG:  ``entry_price - initial_stop_loss``
        For SHORT: ``initial_stop_loss - entry_price``

        Returns ``None`` if the initial_stop_loss is not set.
        """
        sl = trade.initial_stop_loss
        if sl is None:
            # Fallback to current stop_loss if initial_stop_loss is missing
            # (for trades opened before the schema update).
            sl = trade.stop_loss

        if sl is None:
            return None

        ep = trade.entry_price
        # Spot-only: only long trades are supported.
        if trade.direction == "long":
            risk = ep - sl
        else:
            risk = 0.0
        return risk if risk > 0 else None

    # ----------------------- introspection ---------------------------------
    async def list_open_trades(
        self, symbol: Optional[str] = None
    ) -> list[SimulatedTrade]:
        """Return the currently-open simulated trades (optionally filtered).

        Thin pass-through to ``SupabaseClient.fetch_open_trades`` -- provided
        here so callers can stay within the ``PaperTrader`` API for all
        trade-related operations.
        """
        return await self._supabase.fetch_open_trades(symbol=symbol)

    async def list_recent_trades(self, limit: int = 10) -> list[SimulatedTrade]:
        """Return the most recent ``limit`` simulated trades (any status).

        Thin pass-through to ``SupabaseClient.fetch_recent_trades``.
        """
        return await self._supabase.fetch_recent_trades(limit=limit)


__all__ = [
    "PaperTrader",
]


# Re-export the fee/slippage helpers for callers that want a single import
# path (e.g. the orchestrator which already imports PaperTrader).
__all__ += [
    "fees_mod",
    "slippage_mod",
    "calculate_fee",
    "estimate_slippage",
]
