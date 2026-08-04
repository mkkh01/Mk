"""
File: simulation/fees.py
1. Single Responsibility: Calculate trading fees (maker / taker) for simulated
   trades.  No slippage, no risk, no DB writes -- only the fee arithmetic.
2. Consumes: ``MAKER_FEE_PCT`` / ``TAKER_FEE_PCT`` from config/thresholds.py.
3. Produces: pure functions returning fee amounts (float) and breakdown dicts.
4. Downstream: simulation/paper_trade.py (entry fee on open, exit fee on close),
   portfolio/performance.py (gross cost attribution), bot/telegram_bot.py (when
   displaying the cost breakdown to the user).
5. New Dependencies: numpy (already in requirements.txt; used only for fsafe
   numeric coercion / NaN handling).
6. Touches Section 6 bugs? No -- pure arithmetic.  Section 0 hard-constraint 7
   is honoured by always labelling outputs as fees for *simulated* trades (the
   ``fee_breakdown`` dict always carries ``fee_type`` with the suffix used by
   the simulator; callers must never re-label this as a live-execution cost).
7. Tests: tests/unit/test_simulation.py exercises:
       * taker-default conservatism (is_maker=False returns TAKER_FEE_PCT)
       * maker fee formula
       * exit fee formula (price * size * fee_pct/100)
       * total_trade_fees combining entry + exit
       * zero-size and zero-price safety (returns 0.0, no ZeroDivisionError)
       * fee_breakdown schema and threshold-sensitivity (changing
         TAKER_FEE_PCT changes the returned fee amount).
8. Logging: ``fee_calculated`` (debug only) with
   {price, size, is_maker, fee_pct, fee_amount, fee_type}.  No info-level
   events -- this module is hot-path and called per trade.
9. Dependency Order: config -> monitoring/logger.py -> simulation/fees.py
   (no upstream violations; fees.py is the leaf of the simulation package and
   is consumed by paper_trade.py).
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np

from config.thresholds import MAKER_FEE_PCT, TAKER_FEE_PCT
from monitoring.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _safe_float(value: float) -> float:
    """Coerce ``value`` to a finite float; NaN/inf -> 0.0.

    Section 22 (Engine Level): division by zero / NaN must not propagate.
    Fees feed directly into PnL so any NaN here would poison the entire
    portfolio calculation downstream.  We defensively coerce non-finite
    inputs to 0.0 and log a warning so the operator can see it.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        logger.warning(
            "fee_input_coercion_failed",
            raw_value=repr(value),
        )
        return 0.0
    if not np.isfinite(f):
        logger.warning(
            "fee_input_non_finite",
            raw_value=f,
        )
        return 0.0
    return f


def _fee_pct_for(is_maker: bool) -> float:
    """Return the fee percentage (in 0-100 scale) for the given order type."""
    return MAKER_FEE_PCT if is_maker else TAKER_FEE_PCT


def _fee_type_label(is_maker: bool) -> Literal["maker", "taker"]:
    """Human-readable label for the fee type."""
    return "maker" if is_maker else "taker"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def calculate_fee(entry_price: float, size: float, is_maker: bool = False) -> float:
    """Calculate the entry fee for a simulated trade.

    Formula::

        fee = entry_price * size * (fee_pct / 100)

    where ``fee_pct`` is ``MAKER_FEE_PCT`` if ``is_maker`` else
    ``TAKER_FEE_PCT``.

    Per Section 18, simulation defaults to **taker** fees (conservative -- the
    simulator does not benefit from maker rebates a real bot might earn).  The
    default ``is_maker=False`` enforces this.

    Args:
        entry_price: Entry (fill) price of the order.  Must be >= 0.
        size: Order size in base currency (e.g. BTC for BTCUSDT).  Must be >= 0.
        is_maker: ``True`` to use the maker fee, ``False`` (default) to use the
            taker fee.

    Returns:
        The fee amount in quote currency (e.g. USDT).  Always a finite
        non-negative float.  Returns ``0.0`` if any input is non-finite or
        non-numeric.

    Raises:
        Nothing.  All numeric coercion is defensive (Section 22).
    """
    price = _safe_float(entry_price)
    qty = _safe_float(size)
    if price < 0.0 or qty < 0.0:
        logger.warning(
            "fee_input_negative",
            entry_price=price,
            size=qty,
            is_maker=is_maker,
        )
        return 0.0

    fee_pct = _fee_pct_for(is_maker)
    fee_amount = price * qty * (fee_pct / 100.0)

    logger.debug(
        "fee_calculated",
        price=price,
        size=qty,
        is_maker=is_maker,
        fee_pct=fee_pct,
        fee_amount=fee_amount,
        fee_type=_fee_type_label(is_maker),
        side="entry",
    )
    return fee_amount


def calculate_exit_fee(exit_price: float, size: float, is_maker: bool = False) -> float:
    """Calculate the exit fee for a simulated trade.

    The exit leg is symmetric to the entry leg: the same percentage applies to
    the notional value at exit time.  In simulation we conservatively assume
    taker fees on exit by default (a market close on TP/SL hit behaves as a
    taker order).

    Args:
        exit_price: Exit (fill) price of the order.  Must be >= 0.
        size: Order size in base currency (must match the size used at entry
            for a meaningful total).  Must be >= 0.
        is_maker: ``True`` to use the maker fee, ``False`` (default) to use the
            taker fee.

    Returns:
        The fee amount in quote currency.  Always a finite non-negative float.
    """
    price = _safe_float(exit_price)
    qty = _safe_float(size)
    if price < 0.0 or qty < 0.0:
        logger.warning(
            "fee_input_negative",
            exit_price=price,
            size=qty,
            is_maker=is_maker,
        )
        return 0.0

    fee_pct = _fee_pct_for(is_maker)
    fee_amount = price * qty * (fee_pct / 100.0)

    logger.debug(
        "fee_calculated",
        price=price,
        size=qty,
        is_maker=is_maker,
        fee_pct=fee_pct,
        fee_amount=fee_amount,
        fee_type=_fee_type_label(is_maker),
        side="exit",
    )
    return fee_amount


def total_trade_fees(
    entry_price: float,
    exit_price: float,
    size: float,
    is_maker_entry: bool = False,
    is_maker_exit: bool = False,
) -> float:
    """Sum of entry + exit fees for one round-trip simulated trade.

    Args:
        entry_price: Fill price on the entry leg.
        exit_price: Fill price on the exit leg.
        size: Order size in base currency.
        is_maker_entry: Use maker fee for the entry leg (default taker).
        is_maker_exit: Use maker fee for the exit leg (default taker).

    Returns:
        ``calculate_fee(entry, size, is_maker_entry)
            + calculate_exit_fee(exit, size, is_maker_exit)``.
        Always a finite non-negative float.
    """
    entry_fee = calculate_fee(entry_price, size, is_maker=is_maker_entry)
    exit_fee = calculate_exit_fee(exit_price, size, is_maker=is_maker_exit)
    total = entry_fee + exit_fee
    # Defensive: if either leg produced a NaN (shouldn't happen because
    # calculate_fee already guards, but double-check), fall back to 0.0.
    if not np.isfinite(total):
        logger.warning(
            "fee_total_non_finite",
            entry_fee=entry_fee,
            exit_fee=exit_fee,
        )
        return 0.0
    return total


def fee_breakdown(
    entry_price: float, size: float, is_maker: bool = False
) -> dict[str, Any]:
    """Return a structured breakdown of a single-leg fee calculation.

    Used by the bot when the user asks for a cost preview on a candidate trade,
    and by the simulator's own ``simulated_trade_opened`` log event for full
    traceability.

    Args:
        entry_price: Fill price.
        size: Order size in base currency.
        is_maker: ``True`` for maker, ``False`` (default) for taker.

    Returns:
        Dict with the keys:

        * ``fee_pct``    -- the percentage applied (in 0-100 scale).
        * ``fee_amount`` -- the fee in quote currency.
        * ``fee_type``   -- ``"maker"`` or ``"taker"``.
        * ``notional``   -- ``entry_price * size`` (the notional value the fee
          was computed on; useful for sanity-checking the bot output).
        * ``is_simulated`` -- always ``True``.  Section 0 hard-constraint 7:
          the breakdown must always be labelled as simulation output so a
          downstream formatter cannot accidentally relabel it as a live cost.
    """
    price = _safe_float(entry_price)
    qty = _safe_float(size)
    fee_pct = _fee_pct_for(is_maker)
    notional = price * qty
    fee_amount = notional * (fee_pct / 100.0)

    return {
        "fee_pct": fee_pct,
        "fee_amount": fee_amount,
        "fee_type": _fee_type_label(is_maker),
        "notional": notional,
        "is_simulated": True,
    }


# ---------------------------------------------------------------------------
# Convenience: total-fee breakdown for a round-trip trade.
# ---------------------------------------------------------------------------
def total_fee_breakdown(
    entry_price: float,
    exit_price: float,
    size: float,
    is_maker_entry: bool = False,
    is_maker_exit: bool = False,
) -> dict[str, Any]:
    """Structured breakdown of a round-trip fee (entry + exit).

    This is a thin convenience wrapper combining two :func:`fee_breakdown`
    calls plus the :func:`total_trade_fees` total.  It is used by the
    portfolio module's attribution report and by the bot's "trade closed"
    notification.

    Returns:
        Dict with the keys:

        * ``entry``           -- fee_breakdown for the entry leg.
        * ``exit``            -- fee_breakdown for the exit leg.
        * ``total_fee_amount`` -- sum of entry + exit fee amounts.
        * ``is_simulated``    -- always ``True`` (Section 0 hard-constraint 7).
    """
    entry = fee_breakdown(entry_price, size, is_maker=is_maker_entry)
    exit_ = fee_breakdown(exit_price, size, is_maker=is_maker_exit)
    total = entry["fee_amount"] + exit_["fee_amount"]
    return {
        "entry": entry,
        "exit": exit_,
        "total_fee_amount": total,
        "is_simulated": True,
    }


__all__ = [
    "calculate_fee",
    "calculate_exit_fee",
    "total_trade_fees",
    "fee_breakdown",
    "total_fee_breakdown",
]
