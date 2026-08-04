"""
File: simulation/slippage.py
1. Single Responsibility: Estimate price slippage for simulated trades.
   No fee, no risk, no DB writes -- only the slippage arithmetic.  Two
   models are provided: a flat-percentage model (the spec default) and an
   optional market-impact model that scales with order size vs. average
   traded volume.
2. Consumes: ``SLIPPAGE_PCT`` from config/thresholds.py.  Optionally an
   ``avg_volume`` argument (in base currency) for the impact-aware variant.
3. Produces: pure functions returning slippage amounts (quote currency),
   effective fill prices, and a structured breakdown dict.
4. Downstream: simulation/paper_trade.py (entry slippage on open and
   effective fill price), portfolio/performance.py (gross cost attribution),
   bot/telegram_bot.py (cost preview in the trade alert).
5. New Dependencies: numpy (already in requirements.txt; used for fsafe
   numeric coercion / NaN handling).
6. Touches Section 6 bugs? No -- pure arithmetic.  Section 0
   hard-constraint 7 is honoured by always labelling outputs as slippage
   for *simulated* trades (the breakdown dict always carries
   ``is_simulated=True``).
7. Tests: tests/unit/test_simulation.py exercises:
       * flat slippage formula (price * size * SLIPPAGE_PCT/100)
       * threshold-sensitivity (changing SLIPPAGE_PCT changes the result)
       * market-impact extension (size > avg_volume inflates the slippage)
       * apply_slippage_to_price long -> fill price rises (worse for buyer)
       * apply_slippage_to_price short -> fill price falls (worse for seller)
       * zero-size / zero-price safety (returns 0.0, no ZeroDivisionError)
       * zero avg_volume safety (impact term disabled, falls back to flat)
       * slippage_estimate_breakdown schema.
8. Logging: ``slippage_calculated`` (debug only) with
   {entry_price, size, symbol, slippage_pct, slippage_amount, model,
   is_simulated}.  No info-level events -- this module is hot-path.
9. Dependency Order: config -> monitoring/logger.py -> simulation/slippage.py
   (no upstream violations; slippage.py is a leaf of the simulation package,
   consumed by paper_trade.py).
"""

from __future__ import annotations

from typing import Any, Literal, Optional

import numpy as np

from config.thresholds import SLIPPAGE_PCT
from monitoring.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal constants for the optional market-impact model
# ---------------------------------------------------------------------------
# These are NOT trading thresholds; they are parameters of the slippage model
# itself.  They live here (not in config/thresholds.py) because they are not
# user-tunable risk/strategy knobs -- they describe how the simulator approxi-
# mates market micro-structure.  If we ever want to tune them per coin, move
# them to config/thresholds.py at that point.

IMPACT_COEFFICIENT = 0.10
"""Linear impact coefficient applied to (size / avg_volume).

A trade equal to 1x the average per-candle base volume incurs an extra
~10% of the flat slippage as impact cost.  A trade equal to 10x the
average volume incurs ~100% extra (i.e. the slippage doubles).  The
relationship is intentionally linear (square-root impact is overkill for
a simulator whose purpose is to prove the decision engine, not to
micmic a real execution desk).
"""

MAX_IMPACT_MULTIPLIER = 5.0
"""Cap on the impact multiplier so an absurd size/avg_volume ratio cannot
produce a slippage larger than (flat * (1 + MAX_IMPACT_MULTIPLIER)).  This
keeps the simulator well-behaved on illiquid symbols with sparse volume
history."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _safe_float(value: float) -> float:
    """Coerce ``value`` to a finite float; NaN/inf -> 0.0 (Section 22)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        logger.warning("slippage_input_coercion_failed", raw_value=repr(value))
        return 0.0
    if not np.isfinite(f):
        logger.warning("slippage_input_non_finite", raw_value=f)
        return 0.0
    return f


def _impact_multiplier(size: float, avg_volume: Optional[float]) -> float:
    """Return the multiplier applied on top of the flat slippage for impact.

    * If ``avg_volume`` is None or non-positive, returns 0.0 (no impact term).
    * Otherwise returns ``min(IMPACT_COEFFICIENT * size / avg_volume,
      MAX_IMPACT_MULTIPLIER)``.

    The multiplier is the *additional* fraction of flat slippage -- i.e.
    effective slippage = flat * (1 + multiplier).
    """
    if avg_volume is None:
        return 0.0
    av = _safe_float(avg_volume)
    if av <= 0.0:
        return 0.0
    sz = _safe_float(size)
    if sz < 0.0:
        return 0.0
    mult = IMPACT_COEFFICIENT * sz / av
    return float(min(mult, MAX_IMPACT_MULTIPLIER))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def estimate_slippage(entry_price: float, size: float, symbol: str) -> float:
    """Estimate the slippage cost (in quote currency) of a simulated entry.

    Flat-percentage model (Section 18 default)::

        slippage = entry_price * size * (SLIPPAGE_PCT / 100)

    Args:
        entry_price: Intended entry (signal) price.  Must be >= 0.
        size: Order size in base currency.  Must be >= 0.
        symbol: Ticker symbol (e.g. ``"BTCUSDT"``).  Used only for logging /
            traceability; the flat model does not vary per symbol.

    Returns:
        Slippage cost in quote currency.  Always a finite non-negative float.
        Returns 0.0 on any non-finite or negative input.
    """
    price = _safe_float(entry_price)
    qty = _safe_float(size)
    if price < 0.0 or qty < 0.0:
        logger.warning(
            "slippage_input_negative",
            entry_price=price,
            size=qty,
            symbol=symbol,
        )
        return 0.0

    slippage_amount = price * qty * (SLIPPAGE_PCT / 100.0)

    logger.debug(
        "slippage_calculated",
        entry_price=price,
        size=qty,
        symbol=symbol,
        slippage_pct=SLIPPAGE_PCT,
        slippage_amount=slippage_amount,
        model="flat",
        is_simulated=True,
    )
    return slippage_amount


def estimate_slippage_with_impact(
    entry_price: float,
    size: float,
    symbol: str,
    avg_volume: Optional[float] = None,
) -> float:
    """Estimate slippage with an optional market-impact extension.

    When ``avg_volume`` is provided (in base currency, e.g. average BTC volume
    per candle for BTCUSDT), the slippage is inflated to reflect the price
    impact of a large order eating through available liquidity::

        flat            = entry_price * size * (SLIPPAGE_PCT / 100)
        impact_mult     = min(IMPACT_COEFFICIENT * size / avg_volume,
                                MAX_IMPACT_MULTIPLIER)
        effective       = flat * (1.0 + impact_mult)

    When ``avg_volume`` is None, non-positive, or NaN, the function falls
    back to the flat model (Section 22 graceful degradation -- a missing
    volume estimate must not block the simulator).

    Args:
        entry_price: Intended entry price.
        size: Order size in base currency.
        symbol: Ticker symbol.
        avg_volume: Optional average traded volume (in base currency) used to
            gauge market impact.  Pass ``None`` to disable the impact term.

    Returns:
        Slippage cost in quote currency.  Always a finite non-negative float.
    """
    flat = estimate_slippage(entry_price, size, symbol)
    mult = _impact_multiplier(size, avg_volume)
    effective = flat * (1.0 + mult)
    if not np.isfinite(effective):
        logger.warning(
            "slippage_effective_non_finite",
            flat=flat,
            impact_multiplier=mult,
            symbol=symbol,
        )
        return 0.0

    logger.debug(
        "slippage_calculated",
        entry_price=_safe_float(entry_price),
        size=_safe_float(size),
        symbol=symbol,
        slippage_pct=SLIPPAGE_PCT,
        slippage_amount=effective,
        flat_amount=flat,
        impact_multiplier=mult,
        avg_volume=avg_volume,
        model="impact" if avg_volume is not None else "flat",
        is_simulated=True,
    )
    return effective


def apply_slippage_to_price(
    entry_price: float,
    size: float,
    symbol: str,
    direction: Literal["long"] = "long",
    avg_volume: Optional[float] = None,
) -> float:
    """Return the effective fill price after slippage for a Spot market order.

    Slippage always moves the fill price *against* the trader:

    * **Long:** buyer pays more than the signal price -> fill = price * (1 +
      slippage_pct/100).

    Args:
        entry_price: Signal / intended entry price.
        size: Order size in base currency.
        symbol: Ticker symbol.
        direction: Only ``"long"`` is supported for Spot.
        avg_volume: Optional average traded volume for the impact-aware model.

    Returns:
        The effective fill price.  Always a finite non-negative float.
    """
    if direction != "long":
        # Spot-only: fallback to long fill or return price as-is.
        logger.warning("slippage_invalid_direction_for_spot", direction=direction)

    price = _safe_float(entry_price)
    qty = _safe_float(size)
    if price <= 0.0 or qty < 0.0:
        logger.warning(
            "slippage_price_invalid",
            entry_price=price,
            size=qty,
            symbol=symbol,
            direction=direction,
        )
        return price  # nothing meaningful to slip

    # Compute the effective slippage fraction (0..1) of price, accounting for
    # the optional impact multiplier.  We compute it off the notional so the
    # price-side and cost-side stay consistent.
    flat = price * qty * (SLIPPAGE_PCT / 100.0)
    mult = _impact_multiplier(qty, avg_volume)
    effective_cost = flat * (1.0 + mult)
    # effective_cost = price * qty * eff_pct/100  ->  eff_pct = eff_cost*100/(price*qty)
    if price * qty <= 0.0:
        return price
    eff_pct = (effective_cost * 100.0) / (price * qty)
    eff_frac = eff_pct / 100.0

    if direction == "long":
        fill_price = price * (1.0 + eff_frac)
    else:
        fill_price = price

    if not np.isfinite(fill_price):
        logger.warning(
            "slippage_fill_non_finite",
            entry_price=price,
            size=qty,
            symbol=symbol,
            direction=direction,
            eff_pct=eff_pct,
        )
        return price

    logger.debug(
        "slippage_fill_calculated",
        entry_price=price,
        size=qty,
        symbol=symbol,
        direction=direction,
        slippage_pct=SLIPPAGE_PCT,
        effective_pct=eff_pct,
        impact_multiplier=mult,
        fill_price=fill_price,
        model="impact" if avg_volume is not None else "flat",
        is_simulated=True,
    )
    return fill_price


def slippage_estimate_breakdown(
    entry_price: float, size: float, symbol: str
) -> dict[str, Any]:
    """Return a structured breakdown of the flat slippage estimate.

    Used by the bot when the user asks for a cost preview on a candidate
    trade, and by the simulator's ``simulated_trade_opened`` log event for
    full traceability.

    Args:
        entry_price: Signal / intended entry price.
        size: Order size in base currency.
        symbol: Ticker symbol (kept for traceability; the flat model does not
            vary per symbol).

    Returns:
        Dict with the keys:

        * ``slippage_pct``      -- the flat percentage applied (0-100 scale).
        * ``slippage_amount``   -- the slippage cost in quote currency.
        * ``notional``          -- ``entry_price * size`` (the base the slippage
          was computed on).
        * ``model``             -- ``"flat"`` (always, for this function; the
          impact-aware breakdown is produced by
          :func:`slippage_impact_breakdown`).
        * ``is_simulated``      -- always ``True`` (Section 0 hard-constraint 7).
        * ``symbol``            -- the symbol passed in, for traceability.
    """
    price = _safe_float(entry_price)
    qty = _safe_float(size)
    notional = price * qty
    slippage_amount = notional * (SLIPPAGE_PCT / 100.0)
    return {
        "slippage_pct": SLIPPAGE_PCT,
        "slippage_amount": slippage_amount,
        "notional": notional,
        "model": "flat",
        "is_simulated": True,
        "symbol": symbol,
    }


def slippage_impact_breakdown(
    entry_price: float,
    size: float,
    symbol: str,
    avg_volume: Optional[float] = None,
) -> dict[str, Any]:
    """Return a structured breakdown of the impact-aware slippage estimate.

    Extends :func:`slippage_estimate_breakdown` with the impact term.

    Returns:
        Dict with the keys:

        * ``slippage_pct``        -- the flat percentage (0-100 scale).
        * ``slippage_amount``     -- the effective slippage cost (impact
          included) in quote currency.
        * ``flat_amount``         -- the flat-only slippage cost.
        * ``impact_multiplier``   -- the multiplier applied on top of flat.
        * ``effective_pct``       -- the effective slippage percentage after
          impact (0-100 scale, applied to notional).
        * ``notional``            -- ``entry_price * size``.
        * ``avg_volume``          -- the volume input (or ``None``).
        * ``model``               -- ``"impact"`` if ``avg_volume`` was given,
          ``"flat"`` otherwise.
        * ``is_simulated``        -- always ``True``.
        * ``symbol``              -- the symbol passed in.
    """
    price = _safe_float(entry_price)
    qty = _safe_float(size)
    notional = price * qty
    flat = notional * (SLIPPAGE_PCT / 100.0)
    mult = _impact_multiplier(qty, avg_volume)
    effective = flat * (1.0 + mult)
    effective_pct = (
        (effective * 100.0) / notional if notional > 0.0 else 0.0
    )
    return {
        "slippage_pct": SLIPPAGE_PCT,
        "slippage_amount": effective,
        "flat_amount": flat,
        "impact_multiplier": mult,
        "effective_pct": effective_pct,
        "notional": notional,
        "avg_volume": avg_volume,
        "model": "impact" if avg_volume is not None else "flat",
        "is_simulated": True,
        "symbol": symbol,
    }


__all__ = [
    "estimate_slippage",
    "estimate_slippage_with_impact",
    "apply_slippage_to_price",
    "slippage_estimate_breakdown",
    "slippage_impact_breakdown",
    "IMPACT_COEFFICIENT",
    "MAX_IMPACT_MULTIPLIER",
]
