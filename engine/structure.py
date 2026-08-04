"""
File: engine/structure.py
1. Single Responsibility: Detect market structure (swing points, BOS, CHOCH) from
   closed candle sequences and aggregate the result into a ``MarketStructure``.
2. Consumes: ``Candle`` (contracts/market.py), ``SwingPoint``, ``MarketStructure``
   (contracts/market.py), thresholds from ``config/thresholds.py``.
3. Produces: ``list[SwingPoint]``, ``MarketStructure``, BOS/CHOCH detection
   tuples -- all consumed by ``engine/smc.py``, ``engine/trend.py``,
   ``engine/confidence.py`` and ``engine/orchestrator.py``.
4. Downstream: engine/smc.py, engine/confidence.py, engine/orchestrator.py,
   engine/htf_filter.py.
5. New Dependencies: numpy (already in requirements.txt).
6. Touches Section 6 bugs? YES -- Bug 3 (repainting). Every public entry point
   filters out ``is_closed == False`` candles before computing structure. Live
   candles are never allowed to mutate previously stored closed-candle state.
7. Tests: Section 10 engine/structure.py acceptance criteria -- specifically:
   (3) unclosed candle safety, (5) BOS detection, (6) CHOCH detection.
   (Tests 1/2 are sweep-direction regressions -- they live in engine/smc.py per
   Section 15; tests 4 is CVD accuracy -- lives in engine/volume.py.)
8. Logging: ``swing_detected``, ``bos_detected``, ``choch_detected``,
   ``structure_detected`` -- all defined in monitoring/logger.py event catalog.
9. Dependency Order: config -> contracts/market.py -> monitoring/logger.py ->
   engine/structure.py (no upstream violations).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import numpy as np

from config.thresholds import (
    BOS_CONFIRMATION_CANDLES,
    CHOCH_CONFIRMATION_CANDLES,
    MIN_SWING_SIZE_PCT,
    SWING_LOOKBACK,
)
from contracts.market import Candle, MarketStructure, SwingPoint
from monitoring.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
_MIN_STRUCTURE_CANDLES = SWING_LOOKBACK * 2 + 5
"""Minimum number of closed candles required for a meaningful structure pass."""


def _pct_to_ratio(pct: float) -> float:
    """Convert a ``..._PCT`` constant (e.g. ``0.15``) to a fractional ratio.

    All ``*_PCT`` thresholds in ``config/thresholds.py`` are expressed in
    percent units (``0.15`` means ``0.15 %``).  Internal math uses ratios.
    """
    return pct / 100.0


def _filter_closed(candles: list[Candle]) -> list[Candle]:
    """Return only the closed candles, preserving order.

    Section 6 Bug 3: live (``is_closed == False``) candles must NEVER be used
    for structure detection -- they would cause repainting once the candle
    closes with different OHLCV values.
    """
    return [c for c in candles if c.is_closed]


def _swing_prominence(candles: list[Candle], i: int, lookback: int, kind: str) -> float:
    """How far a swing at ``i`` sticks out from its window neighbours.

    For a swing *high*: ``candles[i].high - max(neighbours.low)``.
    For a swing *low*:  ``min(neighbours.high) - candles[i].low``.

    Returns 0.0 if the window is degenerate.
    """
    start = max(0, i - lookback)
    end = min(len(candles), i + lookback + 1)
    if kind == "high":
        neighbour_lows = [candles[j].low for j in range(start, end) if j != i]
        if not neighbour_lows:
            return 0.0
        return candles[i].high - max(neighbour_lows)
    else:
        neighbour_highs = [candles[j].high for j in range(start, end) if j != i]
        if not neighbour_highs:
            return 0.0
        return min(neighbour_highs) - candles[i].low


# ---------------------------------------------------------------------------
# Swing point detection
# ---------------------------------------------------------------------------
def detect_swing_points(
    candles: list[Candle],
    lookback: int = SWING_LOOKBACK,
) -> list[SwingPoint]:
    """Detect swing highs and swing lows in a closed-candle sequence.

    Uses a centred sliding window of size ``lookback * 2 + 1``.  Candle ``i``
    is a swing high when ``candles[i].high`` is strictly greater than every
    neighbour's ``high`` inside the window.  Symmetric rule for swing lows.

    Swings whose *prominence* (how far the swing sticks out from the opposite
    extreme of its neighbours) is below ``MIN_SWING_SIZE_PCT`` percent of price
    are discarded as noise.

    Args:
        candles: Input candle list.  Unclosed candles are silently filtered out
            (Section 6 Bug 3 -- repainting prevention).
        lookback: Window radius on each side.  Defaults to ``SWING_LOOKBACK``.

    Returns:
        Chronologically ordered list of :class:`SwingPoint` objects.  The
        ``index`` field is the position of the swing inside the *filtered*
        closed-candle list, so callers should also pass closed candles to
        downstream functions to keep indexing consistent.
    """
    closed = _filter_closed(candles)

    if len(closed) < lookback * 2 + 1:
        logger.warning(
            "structure_detected",
            timestamp=datetime.utcnow(),
            event_kind="swing_scan_skipped",
            result=f"insufficient_candles:{len(closed)}/{lookback * 2 + 1}",
        )
        return []

    min_prominence_ratio = _pct_to_ratio(MIN_SWING_SIZE_PCT)
    swings: list[SwingPoint] = []

    symbol = closed[0].symbol
    timeframe = closed[0].timeframe

    for i in range(lookback, len(closed) - lookback):
        window_high = closed[i - lookback : i + lookback + 1]
        window_low = window_high  # alias for readability

        center_high = closed[i].high
        center_low = closed[i].low

        neighbour_highs = [
            window_high[j].high for j in range(len(window_high)) if j != lookback
        ]
        neighbour_lows = [
            window_low[j].low for j in range(len(window_low)) if j != lookback
        ]

        is_swing_high = center_high > max(neighbour_highs)
        is_swing_low = center_low < min(neighbour_lows)

        # A candle can theoretically be both a swing high and swing low inside
        # the same window (rare in practice).  We emit both when that happens.
        if is_swing_high:
            prominence = _swing_prominence(closed, i, lookback, "high")
            ratio = prominence / center_high if center_high > 0 else 0.0
            if ratio >= min_prominence_ratio:
                sp = SwingPoint(
                    symbol=symbol,
                    timeframe=timeframe,
                    price=center_high,
                    timestamp=closed[i].open_time,
                    type="high",
                    index=i,
                )
                swings.append(sp)
                logger.info(
                    "swing_detected",
                    timestamp=datetime.utcnow(),
                    symbol=symbol,
                    timeframe=timeframe,
                    type="high",
                    price=center_high,
                    index=i,
                )

        if is_swing_low:
            prominence = _swing_prominence(closed, i, lookback, "low")
            ratio = prominence / center_low if center_low > 0 else 0.0
            if ratio >= min_prominence_ratio:
                sp = SwingPoint(
                    symbol=symbol,
                    timeframe=timeframe,
                    price=center_low,
                    timestamp=closed[i].open_time,
                    type="low",
                    index=i,
                )
                swings.append(sp)
                logger.info(
                    "swing_detected",
                    timestamp=datetime.utcnow(),
                    symbol=symbol,
                    timeframe=timeframe,
                    type="low",
                    price=center_low,
                    index=i,
                )

    return swings


# ---------------------------------------------------------------------------
# Break of Structure
# ---------------------------------------------------------------------------
def detect_bos(
    candles: list[Candle],
    last_swing: SwingPoint,
    confirmation_candles: int = BOS_CONFIRMATION_CANDLES,
) -> Optional[tuple[bool, str]]:
    """Detect a Break of Structure (BOS) against ``last_swing``.

    A BOS is a *continuation* signal: price closes through a prior swing in
    the direction of the prevailing trend, then maintains that break for
    ``confirmation_candles`` additional closes.

    Args:
        candles: Candle list to scan (only ``is_closed`` candles are
            considered).  Typically the full closed-candle history for the
            symbol/timeframe.
        last_swing: The swing point being tested for break.
        confirmation_candles: Number of *additional* closes beyond the break
            that must hold.  ``BOS_CONFIRMATION_CANDLES`` by default.

    Returns:
        * ``(True, "bullish_bos")`` if a swing high is broken to the upside.
        * ``(True, "bearish_bos")`` if a swing low is broken to the downside.
        * ``None`` if no confirmed break is found.
    """
    closed = _filter_closed(candles)
    if not closed or confirmation_candles < 0:
        return None

    # Determine where to start scanning.  Prefer ``last_swing.index + 1`` when
    # it is a valid index into ``closed`` (i.e. the swing was detected on the
    # same candle list).  Otherwise, scan from index 0 -- this makes the
    # function robust to callers passing a pre-sliced "subsequent candles"
    # list.
    start = last_swing.index + 1
    if start < 0 or start >= len(closed):
        start = 0

    swing_price = last_swing.price

    if last_swing.type == "high":
        # Look for the first candle that closes strictly above the swing high.
        for i in range(start, len(closed) - confirmation_candles):
            if closed[i].close <= swing_price:
                continue
            # Confirm: subsequent ``confirmation_candles`` must all close above.
            confirmed = True
            for k in range(1, confirmation_candles + 1):
                if closed[i + k].close <= swing_price:
                    confirmed = False
                    break
            if confirmed:
                logger.info(
                    "bos_detected",
                    timestamp=datetime.utcnow(),
                    symbol=last_swing.symbol,
                    timeframe=last_swing.timeframe,
                    direction="bullish_bos",
                    swing_price=swing_price,
                    break_close=closed[i].close,
                    break_index=i,
                )
                return (True, "bullish_bos")
        return None

    elif last_swing.type == "low":
        for i in range(start, len(closed) - confirmation_candles):
            if closed[i].close >= swing_price:
                continue
            confirmed = True
            for k in range(1, confirmation_candles + 1):
                if closed[i + k].close >= swing_price:
                    confirmed = False
                    break
            if confirmed:
                logger.info(
                    "bos_detected",
                    timestamp=datetime.utcnow(),
                    symbol=last_swing.symbol,
                    timeframe=last_swing.timeframe,
                    direction="bearish_bos",
                    swing_price=swing_price,
                    break_close=closed[i].close,
                    break_index=i,
                )
                return (True, "bearish_bos")
        return None

    return None


# ---------------------------------------------------------------------------
# Change of Character
# ---------------------------------------------------------------------------
def detect_choch(
    candles: list[Candle],
    trend: str,
    last_swing: SwingPoint,
    confirmation_candles: int = CHOCH_CONFIRMATION_CANDLES,
) -> Optional[tuple[bool, str]]:
    """Detect a Change of Character (CHOCH) -- a trend-reversal signal.

    * If ``trend == "up"`` (prior trend was up): CHOCH occurs when price closes
      below the last higher low (``last_swing.type == "low"``) and the next
      ``confirmation_candles`` closes also hold below it.  The reversal is
      bearish.
    * If ``trend == "down"`` (prior trend was down): CHOCH occurs when price
      closes above the last lower high (``last_swing.type == "high"``) and the
      next ``confirmation_candles`` closes also hold above it.  The reversal is
      bullish.
    * For ``trend == "neutral"`` there is no character to change; the function
      returns ``None``.

    Args:
        candles: Candle list to scan (only closed candles are considered).
        trend: Prior trend direction (``"up"``, ``"down"``, ``"neutral"``).
        last_swing: The swing point being tested for reversal break.
        confirmation_candles: Additional closes that must confirm the break.

    Returns:
        * ``(True, "bearish_choch")`` if an uptrend reverses down.
        * ``(True, "bullish_choch")`` if a downtrend reverses up.
        * ``None`` otherwise (including neutral prior trend or wrong swing
          type for the requested reversal direction).
    """
    closed = _filter_closed(candles)
    if not closed or confirmation_candles < 0:
        return None

    start = last_swing.index + 1
    if start < 0 or start >= len(closed):
        start = 0

    swing_price = last_swing.price

    if trend == "up":
        # Need a swing LOW to break -- the last higher low of the uptrend.
        if last_swing.type != "low":
            return None
        for i in range(start, len(closed) - confirmation_candles):
            if closed[i].close >= swing_price:
                continue
            confirmed = True
            for k in range(1, confirmation_candles + 1):
                if closed[i + k].close >= swing_price:
                    confirmed = False
                    break
            if confirmed:
                logger.info(
                    "choch_detected",
                    timestamp=datetime.utcnow(),
                    symbol=last_swing.symbol,
                    timeframe=last_swing.timeframe,
                    direction="bearish_choch",
                    swing_price=swing_price,
                    break_close=closed[i].close,
                    break_index=i,
                )
                return (True, "bearish_choch")
        return None

    if trend == "down":
        # Need a swing HIGH to break -- the last lower high of the downtrend.
        if last_swing.type != "high":
            return None
        for i in range(start, len(closed) - confirmation_candles):
            if closed[i].close <= swing_price:
                continue
            confirmed = True
            for k in range(1, confirmation_candles + 1):
                if closed[i + k].close <= swing_price:
                    confirmed = False
                    break
            if confirmed:
                logger.info(
                    "choch_detected",
                    timestamp=datetime.utcnow(),
                    symbol=last_swing.symbol,
                    timeframe=last_swing.timeframe,
                    direction="bullish_choch",
                    swing_price=swing_price,
                    break_close=closed[i].close,
                    break_index=i,
                )
                return (True, "bullish_choch")
        return None

    # Neutral prior trend -- no character to change.
    return None


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------
def _classify_break(
    bos_or_choch_direction: str,
    prior_trend: str,
) -> tuple[str, str, str]:
    """Map a raw break direction + prior trend to (event_kind, new_trend, label).

    * BOS direction ``bullish_bos`` is continuation when prior_trend was
      ``up``/``neutral``; it is a CHOCH (reversal) when prior_trend was ``down``.
    * BOS direction ``bearish_bos`` is continuation when prior_trend was
      ``down``/``neutral``; it is a CHOCH when prior_trend was ``up``.
    """
    if bos_or_choch_direction == "bullish_bos":
        if prior_trend == "down":
            return "choch", "up", "bullish_choch"
        return "bos", "up", "bullish_bos"
    else:  # bearish_bos
        if prior_trend == "up":
            return "choch", "down", "bearish_choch"
        return "bos", "down", "bearish_bos"


def analyze_structure(candles: list[Candle]) -> MarketStructure:
    """Run the full structure-detection pipeline and return a MarketStructure.

    Pipeline:
      1. Filter to closed candles only (Section 6 Bug 3).
      2. Detect swing points.
      3. Walk through every swing in chronological order and test for a
         confirmed break against the candles that come *after* it.  Each break
         is classified as BOS (continuation) or CHOCH (reversal) depending on
         the prevailing trend at that moment.
      4. The trend is updated to the direction implied by the most recent
         confirmed break.
      5. Return a :class:`MarketStructure` capturing the latest swing high,
         latest swing low, latest BOS/CHOCH timestamps, current trend
         direction, and the full list of break timestamps.

    Args:
        candles: The full candle list (closed and unclosed).  Unclosed candles
            are silently dropped before any analysis is performed.

    Returns:
        A :class:`MarketStructure`.  When there is insufficient closed data,
        the returned object has ``trend_direction == "neutral"`` and no
        swings/breaks.
    """
    closed = _filter_closed(candles)

    # Default empty-state structure.
    if not closed:
        return MarketStructure(
            symbol="",
            timeframe="",
            trend_direction="neutral",
        )

    symbol = closed[0].symbol
    timeframe = closed[0].timeframe

    if len(closed) < _MIN_STRUCTURE_CANDLES:
        logger.warning(
            "structure_detected",
            timestamp=datetime.utcnow(),
            symbol=symbol,
            timeframe=timeframe,
            event_kind="insufficient_candles",
            result=f"{len(closed)}/{_MIN_STRUCTURE_CANDLES}",
        )
        return MarketStructure(
            symbol=symbol,
            timeframe=timeframe,
            trend_direction="neutral",
        )

    swings = detect_swing_points(closed, lookback=SWING_LOOKBACK)

    trend: str = "neutral"
    last_swing_high: Optional[SwingPoint] = None
    last_swing_low: Optional[SwingPoint] = None
    last_bos: Optional[datetime] = None
    last_choch: Optional[datetime] = None
    structure_breaks: list[datetime] = []

    for swing in swings:
        if swing.type == "high":
            last_swing_high = swing
        else:
            last_swing_low = swing

        # Slice the closed candle list to start strictly after this swing.
        subsequent = closed[swing.index + 1 :]
        bos_result = detect_bos(
            subsequent,
            swing,
            confirmation_candles=BOS_CONFIRMATION_CANDLES,
        )
        if bos_result is None:
            continue

        _, raw_direction = bos_result
        event_kind, new_trend, label = _classify_break(raw_direction, trend)

        # The break candle's open_time becomes the canonical break timestamp.
        # We re-find it inside ``subsequent`` to recover the index.
        break_index_in_sub = _find_break_index(
            subsequent, swing, confirmation_candles=BOS_CONFIRMATION_CANDLES
        )
        if break_index_in_sub is None:
            continue
        break_dt = subsequent[break_index_in_sub].open_time
        structure_breaks.append(break_dt)

        if event_kind == "bos":
            last_bos = break_dt
            logger.info(
                "bos_detected",
                timestamp=datetime.utcnow(),
                symbol=symbol,
                timeframe=timeframe,
                direction=label,
                swing_price=swing.price,
                break_index=swing.index + 1 + break_index_in_sub,
            )
        else:  # choch
            last_choch = break_dt
            logger.info(
                "choch_detected",
                timestamp=datetime.utcnow(),
                symbol=symbol,
                timeframe=timeframe,
                direction=label,
                swing_price=swing.price,
                break_index=swing.index + 1 + break_index_in_sub,
            )

        trend = new_trend

    structure = MarketStructure(
        symbol=symbol,
        timeframe=timeframe,
        last_swing_high=last_swing_high,
        last_swing_low=last_swing_low,
        last_bos=last_bos,
        last_choch=last_choch,
        trend_direction=trend,  # type: ignore[arg-type]
        structure_breaks=structure_breaks,
    )

    logger.info(
        "structure_detected",
        timestamp=datetime.utcnow(),
        symbol=symbol,
        timeframe=timeframe,
        event_kind="analyze_complete",
        result=trend,
        swings_count=len(swings),
        breaks_count=len(structure_breaks),
    )
    return structure


def _find_break_index(
    candles: list[Candle],
    last_swing: SwingPoint,
    confirmation_candles: int = BOS_CONFIRMATION_CANDLES,
) -> Optional[int]:
    """Return the index inside ``candles`` of the first confirmed break candle.

    This mirrors the logic of :func:`detect_bos` but returns the index rather
    than a tuple, so the aggregator can recover the break timestamp.  Returns
    ``None`` if no break is found.
    """
    closed = _filter_closed(candles)
    if not closed:
        return None

    start = last_swing.index + 1
    if start < 0 or start >= len(closed):
        start = 0

    swing_price = last_swing.price

    if last_swing.type == "high":
        for i in range(start, len(closed) - confirmation_candles):
            if closed[i].close <= swing_price:
                continue
            confirmed = all(
                closed[i + k].close > swing_price
                for k in range(1, confirmation_candles + 1)
            )
            if confirmed:
                return i
        return None

    if last_swing.type == "low":
        for i in range(start, len(closed) - confirmation_candles):
            if closed[i].close >= swing_price:
                continue
            confirmed = all(
                closed[i + k].close < swing_price
                for k in range(1, confirmation_candles + 1)
            )
            if confirmed:
                return i
        return None

    return None


# ---------------------------------------------------------------------------
# Convenience: numpy-backed swing detection (vectorised, faster on big inputs)
# ---------------------------------------------------------------------------
def detect_swing_points_fast(
    candles: list[Candle],
    lookback: int = SWING_LOOKBACK,
) -> list[SwingPoint]:
    """Vectorised swing-point scanner -- same semantics as :func:`detect_swing_points`.

    Uses numpy boolean masks to find candidate swing indices in one pass per
    direction, then applies the prominence filter.  Provided as a convenience
    for callers that have very large candle lists (e.g. multi-thousand-candle
    backtests).  The non-vectorised :func:`detect_swing_points` remains the
    reference implementation used by :func:`analyze_structure`.
    """
    closed = _filter_closed(candles)
    n = len(closed)
    if n < lookback * 2 + 1:
        return []

    highs = np.array([c.high for c in closed], dtype=float)
    lows = np.array([c.low for c in closed], dtype=float)

    swing_high_mask = np.zeros(n, dtype=bool)
    swing_low_mask = np.zeros(n, dtype=bool)

    for offset in range(1, lookback + 1):
        # Centre strictly greater than left/right neighbour at distance ``offset``.
        left_hi = highs[lookback - offset : n - lookback - offset]
        right_hi = highs[lookback + offset : n - lookback + offset]
        centre_hi = highs[lookback : n - lookback]

        left_lo = lows[lookback - offset : n - lookback - offset]
        right_lo = lows[lookback + offset : n - lookback + offset]
        centre_lo = lows[lookback : n - lookback]

        if offset == 1:
            swing_high_mask[lookback : n - lookback] = (centre_hi > left_hi) & (
                centre_hi > right_hi
            )
            swing_low_mask[lookback : n - lookback] = (centre_lo < left_lo) & (
                centre_lo < right_lo
            )
        else:
            swing_high_mask[lookback : n - lookback] &= (centre_hi > left_hi) & (
                centre_hi > right_hi
            )
            swing_low_mask[lookback : n - lookback] &= (centre_lo < left_lo) & (
                centre_lo < right_lo
            )

    min_prominence_ratio = _pct_to_ratio(MIN_SWING_SIZE_PCT)
    swings: list[SwingPoint] = []
    symbol = closed[0].symbol
    timeframe = closed[0].timeframe

    for i in np.flatnonzero(swing_high_mask):
        i = int(i)
        prominence = _swing_prominence(closed, i, lookback, "high")
        ratio = prominence / closed[i].high if closed[i].high > 0 else 0.0
        if ratio >= min_prominence_ratio:
            swings.append(
                SwingPoint(
                    symbol=symbol,
                    timeframe=timeframe,
                    price=closed[i].high,
                    timestamp=closed[i].open_time,
                    type="high",
                    index=i,
                )
            )

    for i in np.flatnonzero(swing_low_mask):
        i = int(i)
        prominence = _swing_prominence(closed, i, lookback, "low")
        ratio = prominence / closed[i].low if closed[i].low > 0 else 0.0
        if ratio >= min_prominence_ratio:
            swings.append(
                SwingPoint(
                    symbol=symbol,
                    timeframe=timeframe,
                    price=closed[i].low,
                    timestamp=closed[i].open_time,
                    type="low",
                    index=i,
                )
            )

    swings.sort(key=lambda s: s.index)
    return swings
