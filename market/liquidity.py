"""
File: market/liquidity.py
1. Single Responsibility: Cluster confirmed swing points into liquidity
   levels and detect liquidity sweeps on the latest candle.
2. Consumes: Candle, SwingPoint (contracts/market.py); config.thresholds;
   monitoring.logger.
3. Produces: identify_liquidity_levels(), find_strongest_level(),
   is_liquidity_sweep() consumed by engine/smc.py (sweep detection) and
   engine/confidence.py (LIQUIDITY_WEIGHT component).
4. Downstream: engine/smc.py (LiquiditySweep construction),
   engine/confidence.py.
5. New Dependencies: No (pure-Python; clustering is O(n log n) without numpy).
6. Touches Section 6 bugs? Yes -- is_liquidity_sweep must respect Bug 1:
   a high-sweep (wick above a swing high, close back below) is a BEARISH
   reversal; a low-sweep (wick below a swing low, close back above) is a
   BULLISH reversal. The ``direction`` derived from a sweep is the REVERSAL
   direction, not the wick direction.
7. Tests: indirectly exercised by Section 10 engine/smc.py acceptance
   criteria (high sweep -> bearish, low sweep -> bullish); also any future
   tests/unit/test_liquidity.py.
8. Logging: liquidity_levels_identified {timestamp, symbol, high_count,
   low_count, strongest_level} (extension to the Section 9 catalog --
   documents the clustering outcome for traceability).
9. Dependency Order: contracts -> monitoring -> market/liquidity.py
   (no upstream violations; does not import engine.*).
"""

from __future__ import annotations

from typing import Optional

from config.thresholds import (
    LIQUIDITY_CLUSTER_LOOKBACK,
    LIQUIDITY_CLUSTER_TOLERANCE_PCT,
)
from contracts.market import Candle, SwingPoint
from monitoring.logger import get_logger

logger = get_logger(__name__)


# Strength saturates at this many touches (matches the spec: a level with 5
# touches has strength 1.0). Kept as a private constant -- it is a scoring
# coefficient, not a trading threshold.
_STRENGTH_SATURATION_TOUCHES = 5


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------
def _within_tolerance(price_a: float, price_b: float, tolerance_pct: float) -> bool:
    """True iff two prices are within ``tolerance_pct`` percent of each other.

    Uses the smaller price as the denominator so that the test is symmetric
    and conservative (a 0.1% move from 100 -> 100.1 is 0.1%; from 100.1 -> 100
    is also 0.1%). Handles zero/negative prices by returning False.
    """
    if price_a <= 0 or price_b <= 0:
        return False
    diff = abs(price_a - price_b)
    denom = min(price_a, price_b)
    return (diff / denom) * 100.0 <= tolerance_pct


def _cluster_by_price(
    prices: list[float], tolerance_pct: float
) -> list[dict]:
    """Greedy single-link clustering of sorted prices.

    Walks the ascending-sorted price list and grows the current cluster while
    each new price is within ``tolerance_pct`` of the cluster's running mean.
    When a price falls outside the tolerance, the cluster is closed and a new
    one is opened.

    Returns a list of cluster dicts: ``{"price": mean, "touches": count,
    "strength": float}`` sorted by descending touch count.
    """
    if not prices:
        return []

    sorted_prices = sorted(prices)
    clusters: list[dict] = []
    current: list[float] = [sorted_prices[0]]

    for price in sorted_prices[1:]:
        running_mean = sum(current) / len(current)
        if _within_tolerance(price, running_mean, tolerance_pct):
            current.append(price)
        else:
            clusters.append(_build_cluster(current))
            current = [price]
    clusters.append(_build_cluster(current))

    # Strongest (most touches) first; tiebreak by recency in the original
    # input (preserved via stable sort on touches descending).
    clusters.sort(key=lambda c: c["touches"], reverse=True)
    return clusters


def _build_cluster(prices: list[float]) -> dict:
    """Build a cluster dict from a non-empty list of prices."""
    touches = len(prices)
    mean_price = sum(prices) / touches
    strength = min(touches / _STRENGTH_SATURATION_TOUCHES, 1.0)
    return {
        "price": float(mean_price),
        "touches": int(touches),
        "strength": float(strength),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def identify_liquidity_levels(
    swing_points: list[SwingPoint],
    lookback: int = LIQUIDITY_CLUSTER_LOOKBACK,
) -> dict:
    """Cluster swing points into liquidity levels.

    Algorithm (Section 16):
      1. Keep only the most recent ``lookback`` swing points (default 20).
      2. Partition into swing highs and swing lows.
      3. Within each side, group prices within ``LIQUIDITY_CLUSTER_TOLERANCE_PCT``
         (0.1%) of each other into a single liquidity level.
      4. ``touches`` = number of swing points that collapsed into the level.
      5. ``strength`` = min(touches / 5, 1.0).
      6. Return ``{"high_levels": [...], "low_levels": [...]}`` with each
         level shaped as
         ``{"price": float, "touches": int, "strength": float, "type": "high"|"low"}``.

    Edge cases (Section 22):
      * Empty input -> ``{"high_levels": [], "low_levels": []}``.
      * ``lookback <= 0`` -> use all swing points.
      * Zero/negative prices -> skipped (cannot be clustered).

    Logs a ``liquidity_levels_identified`` event with the cluster counts and
    the strongest level (if any) for downstream traceability.
    """
    if not swing_points:
        logger.info(
            "liquidity_levels_identified",
            high_count=0,
            low_count=0,
            strongest_level=None,
        )
        return {"high_levels": [], "low_levels": []}

    # Apply lookback (keep most recent N swing points).
    if lookback > 0:
        recent = swing_points[-lookback:]
    else:
        recent = list(swing_points)

    high_prices = [sp.price for sp in recent if sp.type == "high" and sp.price > 0]
    low_prices = [sp.price for sp in recent if sp.type == "low" and sp.price > 0]

    high_clusters = _cluster_by_price(high_prices, LIQUIDITY_CLUSTER_TOLERANCE_PCT)
    low_clusters = _cluster_by_price(low_prices, LIQUIDITY_CLUSTER_TOLERANCE_PCT)

    # Tag each level with its side so is_liquidity_sweep() can self-disambiguate.
    for lvl in high_clusters:
        lvl["type"] = "high"
    for lvl in low_clusters:
        lvl["type"] = "low"

    strongest = find_strongest_level(high_clusters + low_clusters)

    # Pull a representative symbol from the swing points for logging.
    symbol = recent[-1].symbol if recent else ""

    logger.info(
        "liquidity_levels_identified",
        symbol=symbol,
        high_count=len(high_clusters),
        low_count=len(low_clusters),
        strongest_level=(
            {
                "price": strongest["price"],
                "touches": strongest["touches"],
                "strength": strongest["strength"],
                "type": strongest["type"],
            }
            if strongest is not None
            else None
        ),
    )

    return {
        "high_levels": high_clusters,
        "low_levels": low_clusters,
    }


def find_strongest_level(levels: list[dict]) -> Optional[dict]:
    """Return the level with the highest strength.

    Tiebreakers: more touches first, then higher price (arbitrary but
    deterministic). Returns ``None`` if ``levels`` is empty.
    """
    if not levels:
        return None
    return max(
        levels,
        key=lambda lvl: (lvl.get("strength", 0.0), lvl.get("touches", 0), lvl.get("price", 0.0)),
    )


def is_liquidity_sweep(
    candle: Candle,
    level: dict,
    tolerance_pct: float = 0.05,
) -> bool:
    """Detect a liquidity sweep on ``candle`` against ``level``.

    A sweep is a wick that pokes BEYOND a liquidity level by at least
    ``tolerance_pct`` percent, followed by a close back INSIDE the level:

      * High-level sweep (bearish reversal, Section 6 Bug 1):
          (candle.high - level.price) / level.price * 100 >= tolerance_pct
          AND candle.close < level.price
      * Low-level sweep (bullish reversal, Section 6 Bug 1):
          (level.price - candle.low) / level.price * 100 >= tolerance_pct
          AND candle.close > level.price

    The ``level`` dict must include a ``"type"`` field set to ``"high"`` or
    ``"low"`` (as produced by :func:`identify_liquidity_levels`). If the type
    is missing the function falls back to ``"high"``.

    Edge cases (Section 22):
      * ``level.price <= 0`` -> False (no meaningful level).
      * ``tolerance_pct <= 0`` -> any poke beyond the level qualifies.
    """
    price = float(level.get("price", 0.0))
    if price <= 0:
        return False

    level_type = level.get("type", "high")
    tol = tolerance_pct if tolerance_pct > 0 else 0.0

    if level_type == "low":
        # Bullish sweep: wick below level, close back above.
        if candle.low >= price:
            return False
        poke_pct = (price - candle.low) / price * 100.0
        return poke_pct >= tol and candle.close > price

    # Default: high-level bearish sweep.
    if candle.high <= price:
        return False
    poke_pct = (candle.high - price) / price * 100.0
    return poke_pct >= tol and candle.close < price


def sweep_direction(candle: Candle, level: dict) -> Optional[str]:
    """Return the REVERSAL direction of a sweep on ``candle`` (Bug 1).

      * High-level sweep -> ``"bearish"`` (price rejected down from above).
      * Low-level sweep  -> ``"bullish"`` (price rejected up from below).
      * No sweep         -> ``None``.

    Convenience wrapper around :func:`is_liquidity_sweep` for callers that
    need the reversal direction directly (used by engine/smc.py when
    constructing a ``LiquiditySweep``).
    """
    if not is_liquidity_sweep(candle, level):
        return None
    return "bearish" if level.get("type", "high") == "high" else "bullish"


__all__ = [
    "identify_liquidity_levels",
    "find_strongest_level",
    "is_liquidity_sweep",
    "sweep_direction",
]
