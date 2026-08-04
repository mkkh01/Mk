"""
File: engine/volume.py
1. Single Responsibility: Analyze volume patterns -- CVD (Cumulative Volume
   Delta), volume profile, and volume delta -- from closed candle sequences.
2. Consumes: ``Candle`` (contracts/market.py), thresholds from
   config/thresholds.py.
3. Produces: ``calculate_cvd``, ``calculate_cvd_slope``,
   ``calculate_volume_profile``, ``analyze_volume`` returning a dict consumed
   by engine/confidence.py and engine/orchestrator.py.
4. Downstream: engine/confidence.py (LIQUIDITY_WEIGHT component),
   engine/orchestrator.py.
5. New Dependencies: numpy (already in requirements.txt).
6. Touches Section 6 bugs? YES -- Bug 2. CVD MUST use
   ``Candle.taker_buy_volume`` / ``Candle.taker_sell_volume`` (the actual taker
   volumes), NOT candle colour. The old implementation assigned positive
   volume to green candles and negative volume to red candles, which is wrong
   because a green candle can have net aggressive selling (close > open with
   dominant taker sell volume) -- e.g. when a passive buyer absorbs market
   sells. Verification is required (Section 10 engine/structure.py acceptance
   criterion 4): CVD output must match a hand-computed value from taker
   volumes AND must differ from a candle-colour-based calc on at least one
   deliberately contradictory fixture. Also Bug 3 (repainting): unclosed
   candles are filtered out before any calculation.
7. Tests: Section 10 engine/structure.py acceptance criterion 4 -- CVD
   accuracy against hand-computed taker-volume fixtures and divergence from
   candle-colour calc on a contradictory fixture.
8. Logging: ``volume_analyzed`` {timestamp, symbol, timeframe, cvd,
   cvd_slope, poc}.
9. Dependency Order: config -> contracts/market.py -> monitoring/logger.py ->
   engine/volume.py (no upstream violations; does not import engine.*).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import numpy as np

from contracts.market import Candle
from monitoring.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------
# Number of candles used for the rolling average volume baseline. Not a trading
# threshold -- it is a windowing parameter for the volume_ratio heuristic. Kept
# private to avoid polluting config/thresholds.py with non-threshold knobs.
_VOLUME_AVG_LOOKBACK = 20

# Default number of price bins used by ``calculate_volume_profile`` when the
# caller does not supply one. Public default in the function signature; this
# constant only documents the choice.
_DEFAULT_PROFILE_BINS = 20

# Value-area coverage target -- standard market-profile convention is 70% of
# total volume centred on the POC. Not a trading threshold.
_VALUE_AREA_TARGET_PCT = 0.70

# Minimum number of CVD samples required to compute a meaningful slope. Below
# this we return 0.0 (flat) and emit a warning rather than raising.
_MIN_SLOPE_SAMPLES = 2


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
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


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert ``value`` to ``float``, returning ``default`` on NaN / failure.

    Section 22 mandates safe defaults on division-by-zero / NaN propagation.
    """
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(out):
        return default
    return out


def _candle_color_delta(candle: Candle) -> float:
    """Reference implementation of the BUGGY candle-colour-based delta.

    Used ONLY by the contradictory-fixture self-test in this module's
    docstring and by ``_assert_cvd_diverges_from_color`` -- never called from
    production code paths. A green candle contributes ``+volume``; a red
    candle contributes ``-volume``; a doji contributes ``0``.
    """
    if candle.close > candle.open:
        return candle.volume
    if candle.close < candle.open:
        return -candle.volume
    return 0.0


def _assert_cvd_diverges_from_color(candles: list[Candle]) -> None:
    """Module-load self-check: prove the CVD impl is NOT candle-colour-based.

    Builds a deliberately contradictory fixture where every candle is GREEN
    (close > open) but taker sell volume dominates taker buy volume. Under the
    buggy implementation CVD would rise monotonically; under the correct
    implementation CVD must fall. This runs once at import time and logs a
    warning (not a raise) if the divergence is not observed -- raising would
    break production imports, but a warning makes the failure visible.
    """
    if not candles:
        return
    correct_cvd = calculate_cvd(candles)
    buggy_cvd = list(
        np.cumsum([_candle_color_delta(c) for c in candles]).astype(float)
    )
    if not correct_cvd or not buggy_cvd:
        return
    # On a contradictory fixture the two series must disagree on the final
    # value's sign (one rising, one falling). If they agree on every candle
    # the impl is suspect.
    correct_final = correct_cvd[-1]
    buggy_final = buggy_cvd[-1]
    if np.signbit(correct_final) == np.signbit(buggy_final) and correct_final != 0:
        logger.warning(
            "volume_analyzed",
            timestamp=datetime.utcnow(),
            event_kind="cvd_self_check_sign_match",
            correct_final=correct_final,
            buggy_final=buggy_final,
        )


# ---------------------------------------------------------------------------
# CVD (Cumulative Volume Delta) -- Section 6 Bug 2 fix
# ---------------------------------------------------------------------------
def calculate_cvd(candles: list[Candle]) -> list[float]:
    """Compute the Cumulative Volume Delta series.

    CRITICAL (Section 6, Bug 2): CVD is the cumulative sum of
    ``taker_buy_volume - taker_sell_volume`` per candle. It MUST NOT use
    candle colour as a proxy for buy/sell pressure -- a green candle can
    carry net aggressive selling (close > open with taker_sell_volume >
    taker_buy_volume) when a passive buyer absorbs taker market sells, and
    that information is only visible in the taker volume fields.

    Args:
        candles: Input candle list.  Unclosed candles are filtered out before
            any computation (Section 6 Bug 3 -- no repainting).

    Returns:
        A list of cumulative-delta floats aligned with the *closed* candle
        list. Each entry ``cvd[i]`` is the cumulative sum of per-candle deltas
        from closed candle 0 through closed candle ``i``. Empty list when
        there are no closed candles.
    """
    closed = _filter_closed(candles)
    if not closed:
        return []

    # Per-candle delta = taker_buy_volume - taker_sell_volume.
    deltas = np.array(
        [c.taker_buy_volume - c.taker_sell_volume for c in closed],
        dtype=float,
    )
    cvd = np.cumsum(deltas).astype(float)
    return cvd.tolist()


def calculate_cvd_slope(cvd: list[float], lookback: int = 5) -> float:
    """Compute the slope of the CVD over its last ``lookback`` values.

    Uses ordinary least-squares linear regression (``np.polyfit`` degree 1)
    on the last ``min(lookback, len(cvd))`` CVD samples. A positive slope
    means buyers are becoming more aggressive; negative means sellers.

    Args:
        cvd: CVD series as produced by :func:`calculate_cvd`.
        lookback: Number of trailing samples to fit. Must be ``>= 2`` to
            produce a meaningful slope; values below 2 fall back to ``0.0``.

    Returns:
        The slope (per-sample) of the CVD over the lookback window. Returns
        ``0.0`` when ``len(cvd) < _MIN_SLOPE_SAMPLES`` or when the regression
        fails (Section 22 -- division by zero -> safe default).
    """
    if not cvd or len(cvd) < _MIN_SLOPE_SAMPLES:
        return 0.0

    window_size = max(_MIN_SLOPE_SAMPLES, min(lookback, len(cvd)))
    window = np.asarray(cvd[-window_size:], dtype=float)
    if window.size < _MIN_SLOPE_SAMPLES:
        return 0.0
    if not np.all(np.isfinite(window)):
        # Drop NaNs / infinities defensively.
        window = window[np.isfinite(window)]
        if window.size < _MIN_SLOPE_SAMPLES:
            return 0.0

    x = np.arange(window.size, dtype=float)
    try:
        # slope, intercept
        slope, _ = np.polyfit(x, window, 1)
    except (np.linalg.LinAlgError, ValueError):
        return 0.0
    return _safe_float(slope, default=0.0)


# ---------------------------------------------------------------------------
# Volume Profile
# ---------------------------------------------------------------------------
def calculate_volume_profile(
    candles: list[Candle],
    bins: int = _DEFAULT_PROFILE_BINS,
) -> dict:
    """Build a volume profile from closed candles.

    Algorithm (Section 15 engine/volume.py):
      1. Divide the high-low price range of the input into ``bins`` equal
         width bins.
      2. For each candle, distribute its total ``volume`` across the bins its
         [low, high] range overlaps. (A candle spanning N bins contributes
         ``volume / N`` to each.)
      3. Identify the Point of Control (POC) -- the bin with the largest
         volume.
      4. Compute the Value Area (VA) -- the contiguous band of bins around
         the POC that together contain at least ``_VALUE_AREA_TARGET_PCT``
         (70%) of the total volume. Return the high and low prices of that
         band as ``value_area_high`` and ``value_area_low``.

    Args:
        candles: Input candle list. Unclosed candles are filtered out.
        bins: Number of price bins. Defaults to ``_DEFAULT_PROFILE_BINS``
            (20). Must be ``>= 2``.

    Returns:
        Dict with keys:
          * ``bins``        -- ``list[dict]`` of ``{price_low, price_high,
                                price_mid, volume}`` per bin (ascending by
                                price).
          * ``poc``         -- mid-price of the bin with the most volume
                                (``0.0`` if the input is empty).
          * ``poc_volume``  -- volume in the POC bin.
          * ``poc_index``   -- index of the POC bin in ``bins`` (-1 if empty).
          * ``value_area_high`` -- upper price of the 70% value area.
          * ``value_area_low``  -- lower price of the 70% value area.
          * ``total_volume``    -- sum of all bin volumes.
          * ``bin_width``       -- width of each bin in price units.
    """
    closed = _filter_closed(candles)
    profile: dict = {
        "bins": [],
        "poc": 0.0,
        "poc_volume": 0.0,
        "poc_index": -1,
        "value_area_high": 0.0,
        "value_area_low": 0.0,
        "total_volume": 0.0,
        "bin_width": 0.0,
    }
    if not closed or bins < 2:
        return profile

    lows = np.array([c.low for c in closed], dtype=float)
    highs = np.array([c.high for c in closed], dtype=float)
    vols = np.array([c.volume for c in closed], dtype=float)

    price_low = float(np.min(lows))
    price_high = float(np.max(highs))
    if price_high <= price_low:
        # Degenerate range -- single bin, all volume in it.
        price_high = price_low + max(abs(price_low) * 1e-6, 1e-9)

    bin_width = (price_high - price_low) / bins
    bin_volumes = np.zeros(bins, dtype=float)
    bin_edges = price_low + bin_width * np.arange(bins + 1)

    for i in range(len(closed)):
        lo = lows[i]
        hi = highs[i]
        v = vols[i]
        if v <= 0 or hi <= lo:
            continue
        # Indices of bins overlapping [lo, hi] (inclusive on both edges).
        start_idx = int(np.floor((lo - price_low) / bin_width))
        end_idx = int(np.floor((hi - price_low) / bin_width))
        start_idx = max(0, min(bins - 1, start_idx))
        end_idx = max(0, min(bins - 1, end_idx))
        span = end_idx - start_idx + 1
        bin_volumes[start_idx : end_idx + 1] += v / span

    total_volume = float(bin_volumes.sum())
    if total_volume <= 0:
        # All candles had zero/negative volume -- still return the bin
        # structure but with zeroed volumes.
        bin_dicts = _build_bin_dicts(bin_edges, bin_volumes, bin_width)
        profile["bins"] = bin_dicts
        profile["bin_width"] = bin_width
        return profile

    poc_index = int(np.argmax(bin_volumes))
    poc_volume = float(bin_volumes[poc_index])
    poc_price = float((bin_edges[poc_index] + bin_edges[poc_index + 1]) / 2.0)

    va_low_idx, va_high_idx = _value_area_bounds(bin_volumes, poc_index, total_volume)
    value_area_low = float(bin_edges[va_low_idx])
    value_area_high = float(bin_edges[va_high_idx + 1])

    bin_dicts = _build_bin_dicts(bin_edges, bin_volumes, bin_width)
    profile.update(
        {
            "bins": bin_dicts,
            "poc": poc_price,
            "poc_volume": poc_volume,
            "poc_index": poc_index,
            "value_area_high": value_area_high,
            "value_area_low": value_area_low,
            "total_volume": total_volume,
            "bin_width": bin_width,
        }
    )
    return profile


def _build_bin_dicts(
    edges: np.ndarray, volumes: np.ndarray, bin_width: float
) -> list[dict]:
    """Convert raw numpy bin edges + volumes into a list of dict records."""
    out: list[dict] = []
    for i in range(len(volumes)):
        lo = float(edges[i])
        hi = float(edges[i + 1])
        out.append(
            {
                "price_low": lo,
                "price_high": hi,
                "price_mid": (lo + hi) / 2.0,
                "volume": float(volumes[i]),
                "volume_pct": 0.0,  # populated below if total > 0
            }
        )
    total = float(volumes.sum())
    if total > 0:
        for entry in out:
            entry["volume_pct"] = entry["volume"] / total
    return out


def _value_area_bounds(
    bin_volumes: np.ndarray, poc_index: int, total_volume: float
) -> tuple[int, int]:
    """Return the ``(low_idx, high_idx)`` of the 70% value area around the POC.

    Algorithm: expand outward from the POC bin, at each step adding whichever
    neighbour (above or below) has the larger volume, until the cumulative
    volume >= ``_VALUE_AREA_TARGET_PCT * total_volume``. Returns the inclusive
    bin-index range.
    """
    n = len(bin_volumes)
    if n == 0:
        return (0, 0)

    low = high = poc_index
    cumulative = float(bin_volumes[poc_index])
    target = _VALUE_AREA_TARGET_PCT * total_volume

    while cumulative < target and (low > 0 or high < n - 1):
        above_vol = bin_volumes[high + 1] if high + 1 < n else -1.0
        below_vol = bin_volumes[low - 1] if low - 1 >= 0 else -1.0
        if above_vol < 0 and below_vol < 0:
            break
        if above_vol >= below_vol:
            high += 1
            cumulative += above_vol
        else:
            low -= 1
            cumulative += below_vol

    return (low, high)


# ---------------------------------------------------------------------------
# Aggregate volume analysis
# ---------------------------------------------------------------------------
def _volume_ratio(closed: list[Candle]) -> float:
    """Current candle volume / rolling mean of last ``_VOLUME_AVG_LOOKBACK``.

    Returns ``1.0`` (neutral) when there is insufficient data or zero average
    volume (Section 22 -- division by zero -> safe default of 1.0 so the
    confidence formula is unaffected).
    """
    if not closed:
        return 1.0
    current_vol = closed[-1].volume
    lookback = min(_VOLUME_AVG_LOOKBACK, max(len(closed) - 1, 1))
    if lookback <= 0:
        return 1.0
    prior = closed[-lookback - 1 : -1]
    if not prior:
        return 1.0
    avg = float(np.mean([c.volume for c in prior]))
    if avg <= 0:
        return 1.0
    return _safe_float(current_vol / avg, default=1.0)


def _delta_for_last(closed: list[Candle]) -> float:
    """Taker buy minus taker sell volume on the most recent closed candle."""
    if not closed:
        return 0.0
    last = closed[-1]
    return float(last.taker_buy_volume - last.taker_sell_volume)


def _build_reasons(
    cvd: list[float],
    cvd_slope: float,
    volume_ratio: float,
    delta: float,
    poc: float,
) -> list[str]:
    """Assemble a human-readable list of volume-observation reasons."""
    reasons: list[str] = []
    if cvd:
        reasons.append(f"cvd_final={cvd[-1]:.4f}")
    else:
        reasons.append("cvd_empty")
    reasons.append(f"cvd_slope={cvd_slope:+.6f}")
    if cvd_slope > 0:
        reasons.append("cvd_rising: buyers aggressive")
    elif cvd_slope < 0:
        reasons.append("cvd_falling: sellers aggressive")
    else:
        reasons.append("cvd_flat: balanced flow")
    reasons.append(f"volume_ratio={volume_ratio:.3f} vs {_VOLUME_AVG_LOOKBACK}-period avg")
    if volume_ratio >= 1.5:
        reasons.append("volume_elevated: confirmation")
    elif volume_ratio <= 0.5:
        reasons.append("volume_low: potential_fakeout")
    reasons.append(f"last_delta={delta:+.4f} (taker_buy - taker_sell)")
    if poc > 0:
        reasons.append(f"poc={poc:.4f}")
    return reasons


def analyze_volume(candles: list[Candle]) -> dict:
    """Run the full volume-analysis pipeline and return an aggregated dict.

    Pipeline:
      1. Filter to closed candles (Section 6 Bug 3).
      2. Compute CVD via :func:`calculate_cvd` (Section 6 Bug 2 -- uses
         taker volumes, NOT candle colour).
      3. Compute CVD slope via :func:`calculate_cvd_slope`.
      4. Compute volume profile (POC + value area) via
         :func:`calculate_volume_profile`.
      5. Compute ``volume_ratio`` -- current candle volume divided by the
         rolling mean of the previous ``_VOLUME_AVG_LOOKBACK`` candles.
      6. Compute ``delta`` -- the per-candle taker buy minus taker sell
         volume of the most recent closed candle.
      7. Assemble human-readable ``reasons`` and emit a ``volume_analyzed``
         log event per the Section 9 catalog.

    Args:
        candles: Input candle list (closed and unclosed -- unclosed are
            silently dropped).

    Returns:
        Dict with keys: ``cvd`` (list[float]), ``cvd_slope`` (float),
        ``volume_ratio`` (float), ``poc`` (float), ``value_area_high``
        (float), ``value_area_low`` (float), ``delta`` (float),
        ``reasons`` (list[str]), ``profile`` (dict from
        :func:`calculate_volume_profile`). When the input is empty, all
        numeric fields default to ``0.0`` and ``cvd`` is an empty list.
    """
    closed = _filter_closed(candles)

    if not closed:
        logger.warning(
            "volume_analyzed",
            timestamp=datetime.utcnow(),
            symbol="",
            timeframe="",
            event_kind="no_closed_candles",
            cvd=0.0,
            cvd_slope=0.0,
            poc=0.0,
        )
        return {
            "cvd": [],
            "cvd_slope": 0.0,
            "volume_ratio": 1.0,
            "poc": 0.0,
            "value_area_high": 0.0,
            "value_area_low": 0.0,
            "delta": 0.0,
            "reasons": ["no_closed_candles"],
            "profile": calculate_volume_profile([]),
        }

    symbol = closed[0].symbol
    timeframe = closed[0].timeframe

    cvd = calculate_cvd(closed)
    cvd_slope = calculate_cvd_slope(cvd)
    profile = calculate_volume_profile(closed, bins=_DEFAULT_PROFILE_BINS)
    volume_ratio = _volume_ratio(closed)
    delta = _delta_for_last(closed)
    reasons = _build_reasons(cvd, cvd_slope, volume_ratio, delta, profile["poc"])

    logger.info(
        "volume_analyzed",
        timestamp=datetime.utcnow(),
        symbol=symbol,
        timeframe=timeframe,
        cvd=cvd[-1] if cvd else 0.0,
        cvd_slope=cvd_slope,
        poc=profile["poc"],
        volume_ratio=volume_ratio,
        delta=delta,
    )

    return {
        "cvd": cvd,
        "cvd_slope": cvd_slope,
        "volume_ratio": volume_ratio,
        "poc": profile["poc"],
        "value_area_high": profile["value_area_high"],
        "value_area_low": profile["value_area_low"],
        "delta": delta,
        "reasons": reasons,
        "profile": profile,
    }


# ---------------------------------------------------------------------------
# Bonus: helpers used by tests and downstream callers
# ---------------------------------------------------------------------------
def cvd_diverges_from_color(candles: list[Candle]) -> Optional[bool]:
    """Return ``True`` if the taker-volume CVD disagrees with the buggy
    candle-colour CVD on the final sample's sign.

    Used by the Section 10 acceptance test:
      * ``True``  -- the impl correctly diverges from the buggy calc (good).
      * ``False`` -- the impl agrees with the buggy calc on this fixture
                     (BAD -- the impl is likely candle-colour-based).
      * ``None``  -- the fixture is non-contradictory (e.g. all candles have
                     positive delta and green colour) so the test is
                     inconclusive.
    """
    closed = _filter_closed(candles)
    if not closed:
        return None
    correct = calculate_cvd(closed)
    buggy = list(
        np.cumsum([_candle_color_delta(c) for c in closed]).astype(float)
    )
    if not correct or not buggy:
        return None
    c_final = correct[-1]
    b_final = buggy[-1]
    if c_final == 0 or b_final == 0:
        return None
    return bool(np.signbit(c_final) != np.signbit(b_final))


def volume_confirmation_score(candles: list[Candle]) -> float:
    """Map volume evidence onto a ``[0, 1]`` confidence score.

    Combines:
      * CVD slope direction relative to the last candle's direction (rising
        CVD during a green candle = bullish confirmation; falling CVD during
        a red candle = bearish confirmation; mismatches are penalised).
      * Volume ratio -- elevated volume (>1.5x average) boosts the score;
        depleted volume (<0.5x) reduces it.

    The output is consumed by :func:`engine.confidence.calculate_confidence`
    as the ``volume_confirmation`` argument (weighted by
    ``LIQUIDITY_WEIGHT``).
    """
    closed = _filter_closed(candles)
    if not closed:
        return 0.5

    cvd = calculate_cvd(closed)
    cvd_slope = calculate_cvd_slope(cvd)
    volume_ratio = _volume_ratio(closed)
    last = closed[-1]

    # Directional alignment between CVD slope and the last candle.
    if last.is_bullish():
        directional = _clip(0.5 + cvd_slope * 1e-3)
    elif last.is_bearish():
        directional = _clip(0.5 - cvd_slope * 1e-3)
    else:
        directional = 0.5

    # Volume amplification factor -- ratio >= 1.5 boosts, <= 0.5 dampens.
    amp = _clip(0.5 + (volume_ratio - 1.0) * 0.25)
    score = _clip(0.5 * directional + 0.5 * amp)
    return score


# ---------------------------------------------------------------------------
# Module-load self-check (Section 10 criterion 4 -- divergence from buggy
# candle-colour calc). Built-in contradictory fixture: a green candle whose
# taker_sell_volume exceeds its taker_buy_volume.
# ---------------------------------------------------------------------------
def _self_test_contradictory_fixture() -> list[Candle]:
    """Build a deliberately contradictory candle fixture for the self-check.

    Each candle is GREEN (close > open) but its taker_sell_volume exceeds
    its taker_buy_volume -- so the correct CVD falls while the buggy
    candle-colour CVD would rise.
    """
    from datetime import timedelta

    base_time = datetime(2026, 1, 1, 0, 0, 0)
    candles: list[Candle] = []
    for i in range(5):
        # Green candle (close > open) with dominant taker sell volume.
        candles.append(
            Candle(
                symbol="BTCUSDT",
                timeframe="15m",
                open_time=base_time + timedelta(minutes=i * 15),
                close_time=base_time + timedelta(minutes=(i + 1) * 15),
                open=100.0 + i,
                high=102.0 + i,
                low=99.0 + i,
                close=101.0 + i,  # green: close > open
                volume=100.0,
                taker_buy_volume=30.0,  # < taker_sell_volume
                taker_sell_volume=70.0,
                is_closed=True,
            )
        )
    return candles


_assert_cvd_diverges_from_color(_self_test_contradictory_fixture())


__all__ = [
    "calculate_cvd",
    "calculate_cvd_slope",
    "calculate_volume_profile",
    "analyze_volume",
    "cvd_diverges_from_color",
    "volume_confirmation_score",
]
