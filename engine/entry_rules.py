"""
File: engine/entry_rules.py
1. Single Responsibility: Refine entry timing and price level after risk
   approval -- decide between limit / market entry, apply the configured
   offset in the favourable direction, set the entry-validity timeout, and
   expose retry / expiry predicates.
2. Consumes: ``StrategySignal``, ``RiskAssessment``, ``EntrySignal``
   (contracts/decision.py); ``OrderBlock``, ``FairValueGap``
   (contracts/market.py); thresholds from config/thresholds.py
   (ENTRY_LIMIT_OFFSET_PCT, ENTRY_TIMEOUT_MINUTES, MAX_ENTRY_RETRIES).
3. Produces: ``refine_entry``, ``is_entry_expired``,
   ``should_retry_limit``, ``fallback_to_market`` consumed by
   engine/orchestrator.py and (indirectly) simulation/paper_trade.py.
4. Downstream: engine/orchestrator.py (calls ``refine_entry`` after risk
   approves), simulation/paper_trade.py (consumes the resulting
   ``EntrySignal`` to open a simulated trade).
5. New Dependencies: No new external deps.
6. Touches Section 6 bugs? No.
7. Tests: Section 10 engine/entry_rules.py acceptance criteria:
       1. Limit offset -- entry price must be offset by
          ``ENTRY_LIMIT_OFFSET_PCT`` from signal price in the favourable
          direction (long: ``entry * (1 - offset)``; short:
          ``entry * (1 + offset)``).
       2. Timeout -- an entry signal past ``ENTRY_TIMEOUT_MINUTES`` must be
          rejected (``is_entry_expired`` returns True).
       3. Retry limit -- after ``MAX_ENTRY_RETRIES`` failed limit entries,
          fall back to market entry (``should_retry_limit`` returns False;
          ``fallback_to_market`` produces a market EntrySignal).
8. Logging: ``entry_refined`` {timestamp, symbol, entry_type, entry_price}
   per the monitoring/logger.py event catalog.
9. Dependency Order: config -> contracts -> monitoring -> engine/entry_rules.py
   (no upstream violations; imports only contracts + thresholds + logger).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from config.thresholds import (
    ENTRY_LIMIT_OFFSET_PCT,
    ENTRY_TIMEOUT_MINUTES,
    MAX_ENTRY_RETRIES,
)
from contracts.decision import EntrySignal, RiskAssessment, StrategySignal
from contracts.market import FairValueGap, OrderBlock
from monitoring.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------
# Maximum distance (as a fraction of price) between the current price and the
# nearest unmitigated OB / unfilled FVG for the entry to qualify as a *limit*
# entry. 5% is generous -- it covers most retracements in normal ATR regimes.
# Kept private because this is an algorithmic tuning knob rather than a
# trading threshold; if it ever needs to be configurable per-coin it should
# graduate to config/thresholds.py.
_PROXIMITY_TOLERANCE_PCT = 5.0

# How far the entry price is allowed to be from the OB/FVG level before we
# treat the OB/FVG as "near" the price. The same tolerance applies in both
# directions to keep the logic simple and predictable.
_NEAR_OB_FVG_TOLERANCE_PCT = 1.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _safe_float(value: float, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:  # NaN
        return default
    return out


def _within_pct(price_a: float, price_b: float, tolerance_pct: float) -> bool:
    """True iff ``price_a`` and ``price_b`` are within ``tolerance_pct`` of each other."""
    if price_a <= 0 or price_b <= 0:
        return False
    diff = abs(price_a - price_b)
    denom = min(price_a, price_b)
    return (diff / denom) * 100.0 <= tolerance_pct


def _apply_limit_offset(entry_price: float, direction: Literal["long", "neutral"] = "long") -> float:
    """Apply ``ENTRY_LIMIT_OFFSET_PCT`` in the favourable direction for Spot.

    * Long  : ``entry * (1 - offset)`` -- *better* (lower) entry for a buyer.
    * Neutral: no offset.
    """
    offset = ENTRY_LIMIT_OFFSET_PCT / 100.0
    if direction == "long":
        return entry_price * (1.0 - offset)
    return entry_price


def _nearest_unmitigated_ob(
    obs: list[OrderBlock],
    direction: Literal["long"],
    current_price: float,
    tolerance_pct: float = _NEAR_OB_FVG_TOLERANCE_PCT,
) -> Optional[OrderBlock]:
    """Return the nearest unmitigated bullish OB for Spot entry."""
    if not obs:
        return None
    wanted_type = "bullish"
    candidates = [
        ob
        for ob in obs
        if ob.type == wanted_type and not ob.is_mitigated
    ]
    if not candidates:
        return None

    # Nearest by absolute distance of mitigation_level to current_price, but
    # only keep those within the tolerance band.
    within = [
        ob for ob in candidates
        if _within_pct(ob.mitigation_level, current_price, tolerance_pct)
    ]
    pool = within or candidates  # fall back to all candidates if none nearby
    return min(
        pool,
        key=lambda ob: abs(ob.mitigation_level - current_price),
    )


def _nearest_unfilled_fvg(
    fvgs: list[FairValueGap],
    direction: Literal["long"],
    current_price: float,
    tolerance_pct: float = _NEAR_OB_FVG_TOLERANCE_PCT,
) -> Optional[FairValueGap]:
    """Return the nearest unfilled bullish FVG for Spot entry."""
    if not fvgs:
        return None
    wanted_type = "bullish"
    candidates = [
        fvg
        for fvg in fvgs
        if fvg.type == wanted_type and not fvg.is_filled
    ]
    if not candidates:
        return None

    within = [
        fvg for fvg in candidates
        if _within_pct(fvg.top, current_price, tolerance_pct)
        or _within_pct(fvg.bottom, current_price, tolerance_pct)
    ]
    pool = within or candidates
    # Distance from current_price to the nearer edge of the FVG.
    def _dist(fvg: FairValueGap) -> float:
        return min(abs(fvg.top - current_price), abs(fvg.bottom - current_price))

    return min(pool, key=_dist)


# ---------------------------------------------------------------------------
# Entry type decision
# ---------------------------------------------------------------------------
def _decide_entry_type(
    direction: Literal["long", "neutral"],
    ob_list: list[OrderBlock],
    fvg_list: list[FairValueGap],
    current_price: float,
) -> tuple[Literal["limit", "market"], list[str], Optional[float]]:
    """Decide whether to use a limit or market entry for Spot."""
    reasons: list[str] = []
    if direction != "long":
        reasons.append("market_entry: direction is not long")
        return "market", reasons, None

    ob = _nearest_unmitigated_ob(ob_list, direction, current_price)
    if ob is not None:
        if _within_pct(ob.mitigation_level, current_price, _PROXIMITY_TOLERANCE_PCT):
            reasons.append(
                f"limit_at_ob: type={ob.type}, mitigation_level={ob.mitigation_level:.6f}"
            )
            return "limit", reasons, ob.mitigation_level

    fvg = _nearest_unfilled_fvg(fvg_list, direction, current_price)
    if fvg is not None:
        edge = fvg.top
        if _within_pct(edge, current_price, _PROXIMITY_TOLERANCE_PCT):
            reasons.append(
                f"limit_at_fvg: type={fvg.type}, edge={edge:.6f}, "
                f"top={fvg.top:.6f}, bottom={fvg.bottom:.6f}"
            )
            return "limit", reasons, edge

    reasons.append("market_entry: no nearby unmitigated OB or unfilled FVG")
    return "market", reasons, None


# ---------------------------------------------------------------------------
# Public API: refine_entry
# ---------------------------------------------------------------------------
def refine_entry(
    signal: StrategySignal,
    risk: RiskAssessment,
    ob_list: list[OrderBlock],
    fvg_list: list[FairValueGap],
    current_price: float,
    confidence: float = 1.0,
    atr: float = 0.0,
) -> EntrySignal:
    """Refine the entry after risk approval.

    Algorithm (Section 15 engine/entry_rules.py):
      1. Decide entry type:
         * Limit if ``current_price`` is near an unmitigated OB (within
           ``_PROXIMITY_TOLERANCE_PCT`` of its mitigation level) OR near an
           unfilled FVG edge.
         * Market otherwise.
      2. Compute the limit price:
         * Limit entry: start from the OB/FVG level (or current_price if no
           level) and apply ``ENTRY_LIMIT_OFFSET_PCT`` in the favourable
           direction.
         * Market entry: use ``current_price`` (no offset).
      3. Set ``valid_until = now + ENTRY_TIMEOUT_MINUTES`` (UTC).
      4. Build an :class:`EntrySignal` with all fields populated from
         ``signal`` + ``risk`` + ``confidence``.

    Args:
        signal: The candidate strategy signal (provides symbol, direction,
            timeframe, reasons, source_candle_open_time).
        risk: The approved :class:`RiskAssessment` (provides stop_loss,
            take_profit, risk_reward_ratio).  ``risk.allowed`` MUST be True
            -- if False, this function still constructs an EntrySignal for
            traceability but logs a warning (the orchestrator should not
            call it on a rejected risk).
        ob_list: Order blocks detected on the entry timeframe (used for the
            limit-entry decision).
        fvg_list: Fair value gaps detected on the entry timeframe.
        current_price: Current market price of the symbol.
        confidence: Pre-computed confidence in [0, 1] from the orchestrator
            pipeline.  Defaults to ``1.0`` for callers that do not pass it
            (e.g. back-testers).  MUST NOT be derived from risk / money
            management fields -- it reflects the quality of the signal.

    Returns:
        :class:`EntrySignal` with all fields populated.
    """
    if not risk.allowed:
        logger.warning(
            "entry_refined",
            timestamp=datetime.utcnow(),
            symbol=signal.symbol,
            entry_type="market",
            entry_price=_safe_float(current_price),
            event_kind="risk_not_allowed",
        )

    current_price = _safe_float(current_price)
    direction = signal.direction

    entry_type, decide_reasons, level_price = _decide_entry_type(
        direction, ob_list, fvg_list, current_price
    )

    # The base price for limit entries is the OB/FVG level when available,
    # otherwise the current price.  Market entries always use current price.
    if entry_type == "limit":
        base_price = level_price if level_price is not None and level_price > 0 else current_price
        entry_price = _apply_limit_offset(base_price, direction)
    else:
        entry_price = current_price

    valid_until = datetime.now(timezone.utc) + timedelta(minutes=ENTRY_TIMEOUT_MINUTES)

    # [FIX] Recalculate SL and TP based on the final entry_price instead of 
    # copying them from risk. This prevents "SL > Entry" bugs when a limit offset 
    # is applied.
    from engine.risk import calculate_stop_loss, calculate_take_profit, calculate_risk_reward
    
    # Use the ATR provided or fall back to the distance implied by the risk assessment
    if atr > 0:
        stop_loss = calculate_stop_loss(entry_price, atr, direction)
        take_profit = calculate_take_profit(entry_price, atr, direction)
    else:
        # Fallback: if no ATR, preserve the absolute distance from the risk basis
        # to avoid breaking logic, but this should be rare now.
        stop_loss = risk.stop_loss_price if risk.stop_loss_price is not None else entry_price
        take_profit = risk.take_profit_price if risk.take_profit_price is not None else entry_price

    risk_reward = calculate_risk_reward(entry_price, stop_loss, take_profit)

    reasons = list(signal.reasons) + decide_reasons
    reasons.append(
        f"entry_type={entry_type}, entry_price={entry_price:.6f}, "
        f"valid_until={valid_until.isoformat()}"
    )

    entry = EntrySignal(
        symbol=signal.symbol,
        direction=direction,
        entry_price=entry_price,
        entry_type=entry_type,
        timeframe=signal.timeframe,
        confidence=_safe_float(confidence),  # signal-derived confidence, not money
        reasons=reasons,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_reward=risk_reward,
        valid_until=valid_until,
    )

    logger.info(
        "entry_refined",
        timestamp=datetime.utcnow(),
        symbol=signal.symbol,
        entry_type=entry_type,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_reward=risk_reward,
        valid_until=valid_until.isoformat(),
    )
    return entry


# ---------------------------------------------------------------------------
# Expiry / retry predicates
# ---------------------------------------------------------------------------
def is_entry_expired(entry: EntrySignal, now: Optional[datetime] = None) -> bool:
    """True iff ``entry.valid_until`` is in the past relative to ``now``.

    Args:
        entry: The entry signal to check.
        now: Reference timestamp. Defaults to ``datetime.now(UTC)``.

    Returns:
        ``True`` if expired, ``False`` otherwise. Always returns ``False``
        if ``entry.valid_until`` is None (defensive -- should never happen
        because ``refine_entry`` always sets it).
    """
    if entry.valid_until is None:
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    # Make both timestamps tz-aware (or both naive) so the comparison works.
    if entry.valid_until.tzinfo is None and now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    elif entry.valid_until.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now > entry.valid_until


def should_retry_limit(retry_count: int) -> bool:
    """True iff the limit-entry retry budget has not been exhausted.

    Returns ``True`` when ``retry_count < MAX_ENTRY_RETRIES``.  Once the
    retry count reaches the limit, callers should fall back to a market
    entry via :func:`fallback_to_market`.
    """
    try:
        count = int(retry_count)
    except (TypeError, ValueError):
        return False
    return count < MAX_ENTRY_RETRIES


# ---------------------------------------------------------------------------
# Fallback: convert a limit entry to a market entry after retries are exhausted
# ---------------------------------------------------------------------------
def fallback_to_market(
    entry: EntrySignal,
    current_price: float,
) -> EntrySignal:
    """Convert a (failed) limit entry into a market entry at ``current_price``.

    Used after :func:`should_retry_limit` returns False.  The new EntrySignal
    inherits all fields from ``entry`` except:
      * ``entry_type`` -> ``"market"``
      * ``entry_price`` -> ``current_price``
      * ``valid_until`` -> ``now + ENTRY_TIMEOUT_MINUTES`` (fresh window)
      * ``reasons`` -> the original reasons + ``"fallback_to_market"``

    Args:
        entry: The exhausted limit entry.
        current_price: Current market price for the market order.

    Returns:
        A new :class:`EntrySignal` with ``entry_type="market"``.
    """
    current_price = _safe_float(current_price)
    new_reasons = list(entry.reasons) + [
        f"fallback_to_market: retries_exhausted={MAX_ENTRY_RETRIES}",
    ]
    new_entry = EntrySignal(
        symbol=entry.symbol,
        direction=entry.direction,
        entry_price=current_price,
        entry_type="market",
        timeframe=entry.timeframe,
        confidence=entry.confidence,
        reasons=new_reasons,
        stop_loss=entry.stop_loss,
        take_profit=entry.take_profit,
        risk_reward=entry.risk_reward,
        valid_until=datetime.now(timezone.utc) + timedelta(minutes=ENTRY_TIMEOUT_MINUTES),
    )
    logger.info(
        "entry_refined",
        timestamp=datetime.utcnow(),
        symbol=entry.symbol,
        entry_type="market",
        entry_price=current_price,
        event_kind="fallback_to_market",
    )
    return new_entry


# ---------------------------------------------------------------------------
# Bonus: helper to refresh the valid_until window on a retried limit
# ---------------------------------------------------------------------------
def refresh_valid_until(entry: EntrySignal) -> EntrySignal:
    """Return a copy of ``entry`` with a fresh ``valid_until`` window.

    Used by the orchestrator when a limit order has not filled but the
    retry budget has not been exhausted -- the same limit price is kept but
    the timeout window is reset for the next retry.
    """
    new_reasons = list(entry.reasons) + ["valid_until_refreshed"]
    return entry.model_copy(
        update={
            "valid_until": datetime.now(timezone.utc)
            + timedelta(minutes=ENTRY_TIMEOUT_MINUTES),
            "reasons": new_reasons,
        }
    )


__all__ = [
    "refine_entry",
    "is_entry_expired",
    "should_retry_limit",
    "fallback_to_market",
    "refresh_valid_until",
]
