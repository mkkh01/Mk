"""
File: engine/smc.py
1. Single Responsibility: Detect Smart Money Concepts -- Order Blocks, Fair
   Value Gaps, and Liquidity Sweeps -- from closed candle sequences.
2. Consumes: ``Candle``, ``SwingPoint`` (from contracts/market.py and produced
   by engine/structure.py), thresholds from config/thresholds.py.
3. Produces: ``OrderBlock``, ``FairValueGap``, ``LiquiditySweep`` (all from
   contracts/market.py) and an aggregated dict from :func:`analyze_smc`.
4. Downstream: engine/confidence.py, engine/entry_rules.py,
   engine/orchestrator.py.
5. New Dependencies: numpy (already in requirements.txt).
6. Touches Section 6 bugs? YES -- Bug 1.  Liquidity sweep ``direction`` is the
   REVERSAL direction, NOT the sweep direction:
       * high sweep (wick above swing high, close below)  -> ``direction="bearish"``
       * low  sweep (wick below swing low,  close above)  -> ``direction="bullish"``
   This is asserted by Section 10 acceptance criteria 1 & 2 for SMC.
   Also Bug 3 (repainting): unclosed candles are filtered out.
7. Tests: Section 10 engine/smc.py acceptance criteria:
       1. OB detection -- a valid bullish OB must be detected.
       2. OB mitigation -- price trading through the mitigation level marks
          ``is_mitigated=True``.
       3. FVG detection -- a 3-candle impulse gap produces a FairValueGap.
       4. FVG fill -- price trading through the gap marks ``is_filled=True``.
   Also Section 10 engine/structure.py criteria 1 & 2 (sweep direction
   regressions) are exercised through this module.
8. Logging: ``ob_detected``, ``fvg_detected``, ``sweep_detected`` per the
   monitoring/logger.py event catalog.
9. Dependency Order: config -> contracts/market.py -> monitoring/logger.py ->
   engine/structure.py -> engine/smc.py (no upstream violations).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import numpy as np

from config.thresholds import (
    FVG_MIN_GAP_PCT,
    LIQUIDITY_SWEEP_STRENGTH_THRESHOLD,
    OB_MAX_CANDLES_BACK,
    OB_MIN_IMPULSE_PCT,
)
from contracts.market import (
    Candle,
    FairValueGap,
    LiquiditySweep,
    OrderBlock,
    SwingPoint,
)
from monitoring.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _pct_to_ratio(pct: float) -> float:
    """Convert a ``..._PCT`` threshold (in percent units) to a fractional ratio."""
    return pct / 100.0


def _filter_closed(candles: list[Candle]) -> list[Candle]:
    """Drop unclosed candles (Section 6 Bug 3 -- no repainting on live data)."""
    return [c for c in candles if c.is_closed]


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Clamp ``value`` to ``[low, high]``."""
    if value < low:
        return low
    if value > high:
        return high
    return value


def _average_volume(candles: list[Candle], lookback: int = 20) -> float:
    """Mean volume over the last ``lookback`` closed candles (or all if fewer)."""
    if not candles:
        return 0.0
    window = candles[-lookback:]
    vols = [c.volume for c in window if c.volume > 0]
    if not vols:
        return 0.0
    return float(np.mean(vols))


# ---------------------------------------------------------------------------
# Order Blocks
# ---------------------------------------------------------------------------
def detect_order_blocks(
    candles: list[Candle],
    max_lookback: int = OB_MAX_CANDLES_BACK,
) -> list[OrderBlock]:
    """Detect bullish and bearish order blocks in a closed-candle sequence.

    A *bullish* order block is the last bearish candle immediately preceding a
    strong bullish impulse.  Its mitigation level is the *low* of that bearish
    candle.  When price subsequently trades below that low, the OB is
    considered mitigated (its resting liquidity has been tapped).

    A *bearish* order block is the last bullish candle immediately preceding a
    strong bearish impulse.  Its mitigation level is the *high* of that bullish
    candle.  When price subsequently trades above that high, the OB is
    mitigated.

    Args:
        candles: Input candle list.  Unclosed candles are filtered out.
        max_lookback: Maximum number of candles to look back from each impulse
            to find the opposing-colour OB candle.

    Returns:
        List of :class:`OrderBlock`, chronologically ordered by the OB candle's
        open_time.  ``strength`` is in ``[0, 1]`` and reflects the impulse size
        relative to the typical candle body over the input window.
    """
    closed = _filter_closed(candles)
    if len(closed) < 2:
        return []

    symbol = closed[0].symbol
    timeframe = closed[0].timeframe

    # Reference body size used to normalise impulse strength -- a rolling mean
    # gives a smoother baseline than a single global mean.
    bodies = np.array([c.body() for c in closed], dtype=float)
    typical_body = float(np.mean(bodies[bodies > 0])) if np.any(bodies > 0) else 0.0
    min_impulse_ratio = _pct_to_ratio(OB_MIN_IMPULSE_PCT)

    order_blocks: list[OrderBlock] = []

    for i in range(1, len(closed)):
        candle = closed[i]
        price_ref = candle.close if candle.close > 0 else candle.open
        if price_ref <= 0:
            continue

        body = candle.body()
        body_ratio = body / price_ref

        if candle.is_bullish() and body_ratio >= min_impulse_ratio:
            ob_candle = _find_last_opposite(closed, i, bullish_target=False, max_lookback=max_lookback)
            if ob_candle is None:
                continue
            mitigation_level = ob_candle.low
            is_mitigated = _check_ob_mitigation(
                closed, i + 1, mitigation_level, bullish_ob=True
            )
            strength = _ob_strength(body, typical_body)
            ob = OrderBlock(
                symbol=symbol,
                timeframe=timeframe,
                type="bullish",
                open_price=ob_candle.open,
                high_price=ob_candle.high,
                low_price=ob_candle.low,
                close_price=ob_candle.close,
                timestamp=ob_candle.open_time,
                mitigation_level=mitigation_level,
                is_mitigated=is_mitigated,
                strength=strength,
            )
            order_blocks.append(ob)
            logger.info(
                "ob_detected",
                timestamp=datetime.utcnow(),
                symbol=symbol,
                timeframe=timeframe,
                type="bullish",
                mitigation_level=mitigation_level,
                impulse_index=i,
                strength=strength,
            )

        elif candle.is_bearish() and body_ratio >= min_impulse_ratio:
            ob_candle = _find_last_opposite(closed, i, bullish_target=True, max_lookback=max_lookback)
            if ob_candle is None:
                continue
            mitigation_level = ob_candle.high
            is_mitigated = _check_ob_mitigation(
                closed, i + 1, mitigation_level, bullish_ob=False
            )
            strength = _ob_strength(body, typical_body)
            ob = OrderBlock(
                symbol=symbol,
                timeframe=timeframe,
                type="bearish",
                open_price=ob_candle.open,
                high_price=ob_candle.high,
                low_price=ob_candle.low,
                close_price=ob_candle.close,
                timestamp=ob_candle.open_time,
                mitigation_level=mitigation_level,
                is_mitigated=is_mitigated,
                strength=strength,
            )
            order_blocks.append(ob)
            logger.info(
                "ob_detected",
                timestamp=datetime.utcnow(),
                symbol=symbol,
                timeframe=timeframe,
                type="bearish",
                mitigation_level=mitigation_level,
                impulse_index=i,
                strength=strength,
            )

    return order_blocks


def _find_last_opposite(
    candles: list[Candle],
    impulse_index: int,
    bullish_target: bool,
    max_lookback: int,
) -> Optional[Candle]:
    """Return the most recent opposing-colour candle before ``impulse_index``.

    If ``bullish_target`` is True we look for the last *bullish* candle (used
    when the impulse is bearish and we want a bearish OB).  Otherwise we look
    for the last *bearish* candle (bullish OB).  Search is bounded by
    ``max_lookback``.
    """
    start = max(0, impulse_index - max_lookback)
    for j in range(impulse_index - 1, start - 1, -1):
        c = candles[j]
        if bullish_target and c.is_bullish():
            return c
        if not bullish_target and c.is_bearish():
            return c
    return None


def _check_ob_mitigation(
    candles: list[Candle],
    start_index: int,
    mitigation_level: float,
    bullish_ob: bool,
) -> bool:
    """Has price traded through an OB's mitigation level after the impulse?

    For a bullish OB (mitigation = OB low), mitigation occurs when any later
    candle trades at or below the mitigation level (``low <= mitigation_level``).

    For a bearish OB (mitigation = OB high), mitigation occurs when any later
    candle trades at or above the mitigation level (``high >= mitigation_level``).
    """
    for k in range(start_index, len(candles)):
        c = candles[k]
        if bullish_ob and c.low <= mitigation_level:
            return True
        if not bullish_ob and c.high >= mitigation_level:
            return True
    return False


def _ob_strength(impulse_body: float, typical_body: float) -> float:
    """Normalise an impulse body against the typical body into ``[0, 1]``.

    ``typical_body * 3`` is treated as "fully strong" (strength = 1.0).
    """
    if typical_body <= 0:
        return 0.0
    return _clip(impulse_body / (typical_body * 3.0))


# ---------------------------------------------------------------------------
# Fair Value Gaps
# ---------------------------------------------------------------------------
def detect_fvgs(candles: list[Candle]) -> list[FairValueGap]:
    """Detect bullish and bearish Fair Value Gaps in a 3-candle pattern.

    *Bullish FVG* (3 candles A, B, C):
        ``low[C] > high[A]`` -- a price gap opens between A's high and C's low.
        The gap interval is ``[high[A], low[C]]``.

    *Bearish FVG*:
        ``high[C] < low[A]`` -- a price gap opens between C's high and A's low.
        The gap interval is ``[high[C], low[A]]``.

    Only gaps whose width is at least ``FVG_MIN_GAP_PCT`` percent of price are
    retained.

    Fill tracking: subsequent candles that trade into the gap update
    ``fill_percentage``.  A candle that trades *through* the gap sets
    ``is_filled = True`` and ``fill_percentage = 1.0``.

    Args:
        candles: Input candle list.  Unclosed candles are filtered out.

    Returns:
        Chronologically ordered list of :class:`FairValueGap`.
    """
    closed = _filter_closed(candles)
    if len(closed) < 3:
        return []

    symbol = closed[0].symbol
    timeframe = closed[0].timeframe
    min_gap_ratio = _pct_to_ratio(FVG_MIN_GAP_PCT)

    fvgs: list[FairValueGap] = []

    for i in range(len(closed) - 2):
        a = closed[i]
        c = closed[i + 2]
        price_ref = c.close if c.close > 0 else a.close
        if price_ref <= 0:
            continue

        # Bullish FVG: gap up between A.high and C.low.
        if c.low > a.high:
            gap_width = c.low - a.high
            if gap_width / price_ref >= min_gap_ratio:
                top = c.low
                bottom = a.high
                is_filled, fill_pct = _track_fvg_fill(
                    closed, i + 3, top, bottom, bullish=True
                )
                fvg = FairValueGap(
                    symbol=symbol,
                    timeframe=timeframe,
                    type="bullish",
                    top=top,
                    bottom=bottom,
                    timestamp=c.open_time,
                    is_filled=is_filled,
                    fill_percentage=fill_pct,
                )
                fvgs.append(fvg)
                logger.info(
                    "fvg_detected",
                    timestamp=datetime.utcnow(),
                    symbol=symbol,
                    timeframe=timeframe,
                    type="bullish",
                    top=top,
                    bottom=bottom,
                    fill_percentage=fill_pct,
                    is_filled=is_filled,
                )

        # Bearish FVG: gap down between C.high and A.low.
        if c.high < a.low:
            gap_width = a.low - c.high
            if gap_width / price_ref >= min_gap_ratio:
                top = a.low
                bottom = c.high
                is_filled, fill_pct = _track_fvg_fill(
                    closed, i + 3, top, bottom, bullish=False
                )
                fvg = FairValueGap(
                    symbol=symbol,
                    timeframe=timeframe,
                    type="bearish",
                    top=top,
                    bottom=bottom,
                    timestamp=c.open_time,
                    is_filled=is_filled,
                    fill_percentage=fill_pct,
                )
                fvgs.append(fvg)
                logger.info(
                    "fvg_detected",
                    timestamp=datetime.utcnow(),
                    symbol=symbol,
                    timeframe=timeframe,
                    type="bearish",
                    top=top,
                    bottom=bottom,
                    fill_percentage=fill_pct,
                    is_filled=is_filled,
                )

    return fvgs


def _track_fvg_fill(
    candles: list[Candle],
    start_index: int,
    top: float,
    bottom: float,
    bullish: bool,
) -> tuple[bool, float]:
    """Track how far price has penetrated into an FVG gap.

    For a *bullish* FVG (gap = [bottom, top]):
        * Price coming DOWN into the gap means a candle's ``low`` falls below
          ``top``.  Fill percentage = (top - lowest_low) / (top - bottom).
        * Full fill when a candle trades at or below ``bottom``.

    For a *bearish* FVG (gap = [bottom, top]):
        * Price coming UP into the gap means a candle's ``high`` rises above
          ``bottom``.  Fill percentage = (highest_high - bottom) / (top - bottom).
        * Full fill when a candle trades at or above ``top``.

    Returns ``(is_filled, fill_percentage)`` with ``fill_percentage`` in
    ``[0, 1]``.
    """
    gap_width = top - bottom
    if gap_width <= 0:
        # Degenerate gap -- nothing to fill.
        return False, 0.0

    max_penetration = 0.0  # in price units, into the gap from the entry side
    filled = False

    for k in range(start_index, len(candles)):
        c = candles[k]
        if bullish:
            # Price coming back down: how far below ``top`` did the candle reach?
            if c.low < top:
                penetration = top - c.low
                if penetration > max_penetration:
                    max_penetration = penetration
            if c.low <= bottom:
                filled = True
                max_penetration = gap_width
                break
        else:
            if c.high > bottom:
                penetration = c.high - bottom
                if penetration > max_penetration:
                    max_penetration = penetration
            if c.high >= top:
                filled = True
                max_penetration = gap_width
                break

    fill_pct = _clip(max_penetration / gap_width)
    return filled, fill_pct


# ---------------------------------------------------------------------------
# Liquidity Sweeps
# ---------------------------------------------------------------------------
def detect_liquidity_sweeps(
    candles: list[Candle],
    swing_points: list[SwingPoint],
) -> list[LiquiditySweep]:
    """Detect liquidity sweeps against prior swing highs and swing lows.

    CRITICAL (Section 6 Bug 1): ``direction`` is the REVERSAL direction, NOT
    the sweep direction.

        * A *high sweep* (candle wick pokes above a prior swing high, then
          closes back below it) is a **bearish** reversal signal.
        * A *low sweep* (candle wick pokes below a prior swing low, then
          closes back above it) is a **bullish** reversal signal.

    Each swing is swept at most once (the first qualifying candle).  Sweeps
    whose ``strength`` is below ``LIQUIDITY_SWEEP_STRENGTH_THRESHOLD`` are
    discarded.

    ``strength = volume_factor * wick_size_factor`` where
        * ``volume_factor = clip(volume / avg_volume_of_last_20_candles, 0, 1)``
        * ``wick_size_factor = clip(rejection_wick / candle_range, 0, 1)``
          (upper wick for high sweeps, lower wick for low sweeps).

    Args:
        candles: Input candle list.  Unclosed candles are filtered out.
        swing_points: Swing points produced by :func:`engine.structure.detect_swing_points`.

    Returns:
        Chronologically ordered list of :class:`LiquiditySweep`.
    """
    closed = _filter_closed(candles)
    if not closed or not swing_points:
        return []

    symbol = closed[0].symbol
    timeframe = closed[0].timeframe

    # Index swings by type for quick lookup.
    swing_highs = sorted([s for s in swing_points if s.type == "high"], key=lambda s: s.index)
    swing_lows = sorted([s for s in swing_points if s.type == "low"], key=lambda s: s.index)

    swept_high_indices: set[int] = set()
    swept_low_indices: set[int] = set()

    sweeps: list[LiquiditySweep] = []

    for j, candle in enumerate(closed):
        # We need a rolling volume average ending at (but not including) this candle.
        avg_vol = _average_volume(closed[:j], lookback=20) if j > 0 else candle.volume
        if avg_vol <= 0:
            avg_vol = candle.volume if candle.volume > 0 else 1.0

        volume_factor = _clip(candle.volume / avg_vol) if avg_vol > 0 else 0.0
        candle_range = candle.range()

        # Check for high sweep (bearish reversal).
        for swing in swing_highs:
            if swing.index >= j:
                break  # swings are sorted; later swings can't have been swept yet
            if swing.index in swept_high_indices:
                continue
            if candle.high > swing.price and candle.close < swing.price:
                upper_wick = candle.high - max(candle.open, candle.close)
                wick_size_factor = (
                    _clip(upper_wick / candle_range) if candle_range > 0 else 0.0
                )
                strength = _clip(volume_factor * wick_size_factor)
                if strength >= LIQUIDITY_SWEEP_STRENGTH_THRESHOLD:
                    sweeps.append(
                        LiquiditySweep(
                            symbol=symbol,
                            timeframe=timeframe,
                            swept_level=swing.price,
                            direction="bearish",  # REVERSAL direction (Bug 1)
                            strength=strength,
                            timestamp=candle.open_time,
                            confirming_candle_close=candle.close,
                        )
                    )
                    swept_high_indices.add(swing.index)
                    logger.info(
                        "sweep_detected",
                        timestamp=datetime.utcnow(),
                        symbol=symbol,
                        timeframe=timeframe,
                        direction="bearish",
                        swept_level=swing.price,
                        strength=strength,
                        candle_index=j,
                    )
                # Once this swing has been swept by this candle, mark it even if
                # strength was below threshold -- we don't want to re-emit it on
                # later candles either.
                swept_high_indices.add(swing.index)
                break  # one sweep per candle per direction

        # Check for low sweep (bullish reversal).
        for swing in swing_lows:
            if swing.index >= j:
                break
            if swing.index in swept_low_indices:
                continue
            if candle.low < swing.price and candle.close > swing.price:
                lower_wick = min(candle.open, candle.close) - candle.low
                wick_size_factor = (
                    _clip(lower_wick / candle_range) if candle_range > 0 else 0.0
                )
                strength = _clip(volume_factor * wick_size_factor)
                if strength >= LIQUIDITY_SWEEP_STRENGTH_THRESHOLD:
                    sweeps.append(
                        LiquiditySweep(
                            symbol=symbol,
                            timeframe=timeframe,
                            swept_level=swing.price,
                            direction="bullish",  # REVERSAL direction (Bug 1)
                            strength=strength,
                            timestamp=candle.open_time,
                            confirming_candle_close=candle.close,
                        )
                    )
                    swept_low_indices.add(swing.index)
                    logger.info(
                        "sweep_detected",
                        timestamp=datetime.utcnow(),
                        symbol=symbol,
                        timeframe=timeframe,
                        direction="bullish",
                        swept_level=swing.price,
                        strength=strength,
                        candle_index=j,
                    )
                swept_low_indices.add(swing.index)
                break

    sweeps.sort(key=lambda s: s.timestamp)
    return sweeps


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------
def analyze_smc(
    candles: list[Candle],
    swing_points: Optional[list[SwingPoint]] = None,
) -> dict:
    """Run all SMC detectors and return a single aggregated dict.

    Args:
        candles: Input candle list (closed and unclosed -- unclosed are
            filtered out internally).
        swing_points: Optional pre-computed swing points.  If ``None`` (the
            common case), swing points are not recomputed here -- sweeps will
            simply be empty.  Callers that want sweeps must pass swing points
            produced by :func:`engine.structure.detect_swing_points`.

    Returns:
        ``{"order_blocks": [...], "fvgs": [...], "sweeps": [...]}``.
    """
    closed = _filter_closed(candles)
    if len(closed) < 3:
        logger.warning(
            "ob_detected",
            timestamp=datetime.utcnow(),
            symbol=closed[0].symbol if closed else "",
            timeframe=closed[0].timeframe if closed else "",
            event_kind="insufficient_candles",
            count=len(closed),
        )

    order_blocks = detect_order_blocks(closed)
    fvgs = detect_fvgs(closed)
    sweeps = detect_liquidity_sweeps(closed, swing_points or [])

    # Log SMC Discovery (Requested Log #6)
    logger.info(
        "smc_discovery_summary",
        timestamp=datetime.utcnow(),
        symbol=candles[0].symbol if candles else "unknown",
        timeframe=candles[0].timeframe if candles else "unknown",
        order_blocks_found=len(order_blocks),
        fvgs_found=len(fvgs),
        sweeps_found=len(sweeps),
        bos_found="N/A", # Structure module handles BOS/CHOCH
        choch_found="N/A",
    )

    return {
        "order_blocks": order_blocks,
        "fvgs": fvgs,
        "sweeps": sweeps,
    }
