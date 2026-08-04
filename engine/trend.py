"""
File: engine/trend.py
1. Single Responsibility: Determine trend direction and strength using EMA
   (fast/slow) and ADX/+DI/-DI.
2. Consumes: ``Candle`` (contracts/market.py), thresholds from
   config/thresholds.py.
3. Produces: ``calculate_ema``, ``calculate_adx``, ``analyze_trend`` returning a
   dict consumed by engine/confidence.py, engine/htf_filter.py, and
   engine/orchestrator.py.
4. Downstream: engine/confidence.py, engine/htf_filter.py,
   engine/orchestrator.py, market/regime.py.
5. New Dependencies: numpy (already in requirements.txt).
6. Touches Section 6 bugs? Bug 3 (repainting) -- ``analyze_trend`` filters out
   unclosed candles before computing indicators.
7. Tests: Indirectly exercised via market/regime.py Section 10 tests
   (trending/ranging/volatile detection) and engine/htf_filter.py tests.
8. Logging: ``trend_analyzed`` per the monitoring/logger.py event catalog.
9. Dependency Order: config -> contracts/market.py -> monitoring/logger.py ->
   engine/trend.py (no upstream violations).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import numpy as np

from config.thresholds import (
    ADX_PERIOD,
    TREND_ADX_MODERATE_LOWER,
    TREND_ADX_THRESHOLD,
    TREND_EMA_FAST,
    TREND_EMA_SLOW,
)
from contracts.market import Candle
from monitoring.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _filter_closed(candles: list[Candle]) -> list[Candle]:
    """Drop unclosed candles (Section 6 Bug 3 -- no repainting on live data)."""
    return [c for c in candles if c.is_closed]


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value


# ---------------------------------------------------------------------------
# EMA
# ---------------------------------------------------------------------------
def calculate_ema(values: list[float], period: int) -> list[float]:
    """Compute an Exponential Moving Average using numpy.

    Uses the standard smoothing factor ``alpha = 2 / (period + 1)`` and seeds
    the first valid EMA value with the simple moving average of the first
    ``period`` samples.

    Args:
        values: Input series.
        period: EMA window.  Must be ``>= 1``.

    Returns:
        A list of the same length as ``values``.  The first ``period - 1``
        entries are ``float('nan')`` (insufficient data); entry ``period - 1``
        is the seed SMA; subsequent entries follow the EMA recurrence.  If the
        input is shorter than ``period``, the entire output is NaN.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    n = len(values)
    if n == 0:
        return []
    if n < period:
        return [float("nan")] * n

    arr = np.asarray(values, dtype=float)
    alpha = 2.0 / (period + 1.0)
    ema = np.full(n, np.nan, dtype=float)

    # Seed with SMA of the first `period` values.
    seed = float(np.mean(arr[:period]))
    ema[period - 1] = seed

    for i in range(period, n):
        ema[i] = alpha * arr[i] + (1.0 - alpha) * ema[i - 1]

    return ema.tolist()


# ---------------------------------------------------------------------------
# ADX / +DI / -DI  (Wilder's smoothing)
# ---------------------------------------------------------------------------
def _wilder_smooth(values: np.ndarray, period: int) -> np.ndarray:
    """Wilder's smoothing in *mean* form.

    Seed = mean of the first ``period`` values; subsequent values follow
    ``next = (prev * (period - 1) + current) / period``.

    Using the mean form (rather than Wilder's original sum form) keeps every
    smoothed series on the same scale as its input -- ATR is in price units,
    ADX is on the 0-100 scale, etc. -- which is what charting platforms and
    the rest of the CT codebase expect.  All ratio calculations
    (``+DI = 100 * sm_plus / atr``) are unaffected because numerator and
    denominator use the same scaling.
    """
    n = len(values)
    out = np.full(n, np.nan, dtype=float)
    if n < period:
        return out

    out[period - 1] = float(np.mean(values[:period]))
    for i in range(period, n):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out


def calculate_adx(
    candles: list[Candle],
    period: int = ADX_PERIOD,
) -> tuple[float, float, float]:
    """Calculate ADX, +DI, and -DI using Wilder's method.

    Args:
        candles: Input candle list.  Unclosed candles are filtered out.
        period: Lookback for all Wilder smoothings (TR, +DM, -DM, DX).

    Returns:
        ``(adx, plus_di, minus_di)`` for the most recent candle.  When there is
        insufficient data (fewer than ``period * 2`` closed candles), returns
        ``(0.0, 0.0, 0.0)``.
    """
    closed = _filter_closed(candles)
    n = len(closed)
    if n < period * 2 + 1:
        return (0.0, 0.0, 0.0)

    highs = np.array([c.high for c in closed], dtype=float)
    lows = np.array([c.low for c in closed], dtype=float)
    closes = np.array([c.close for c in closed], dtype=float)

    # True Range.
    tr = np.zeros(n, dtype=float)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i] = max(hl, hc, lc)

    # Directional movement.
    plus_dm = np.zeros(n, dtype=float)
    minus_dm = np.zeros(n, dtype=float)
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        if up > down and up > 0:
            plus_dm[i] = up
        if down > up and down > 0:
            minus_dm[i] = down

    atr = _wilder_smooth(tr, period)
    sm_plus = _wilder_smooth(plus_dm, period)
    sm_minus = _wilder_smooth(minus_dm, period)

    plus_di = np.full(n, np.nan, dtype=float)
    minus_di = np.full(n, np.nan, dtype=float)
    dx = np.full(n, np.nan, dtype=float)

    for i in range(period - 1, n):
        if atr[i] > 0:
            plus_di[i] = 100.0 * sm_plus[i] / atr[i]
            minus_di[i] = 100.0 * sm_minus[i] / atr[i]
            di_sum = plus_di[i] + minus_di[i]
            if di_sum > 0:
                dx[i] = 100.0 * abs(plus_di[i] - minus_di[i]) / di_sum

    # ADX is Wilder's smoothing of DX.  We can only compute it once we have
    # ``period`` valid DX values (i.e. starting at index ``period * 2 - 1``).
    dx_series = dx[period - 1:]
    if len(dx_series) < period:
        return (0.0, 0.0, 0.0)

    adx_smoothed = _wilder_smooth(dx_series, period)
    if np.isnan(adx_smoothed[-1]):
        return (0.0, 0.0, 0.0)

    adx_value = float(adx_smoothed[-1])
    plus_di_value = float(plus_di[-1]) if not np.isnan(plus_di[-1]) else 0.0
    minus_di_value = float(minus_di[-1]) if not np.isnan(minus_di[-1]) else 0.0

    return (adx_value, plus_di_value, minus_di_value)


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------
def _strength_from_adx(adx: float) -> float:
    """Map ADX to a 0-1 trend strength using the configured bands.

    * ``adx >= TREND_ADX_THRESHOLD`` (25): strong, mapped to ``[0.7, 1.0]``
      linearly up to ADX 50.
    * ``TREND_ADX_MODERATE_LOWER`` (20) <= adx < 25: moderate, ``[0.4, 0.7]``.
    * ``adx < 20``: weak, ``[0.0, 0.4]``.
    """
    if adx >= TREND_ADX_THRESHOLD:
        return _clip(0.7 + (adx - TREND_ADX_THRESHOLD) / 25.0 * 0.3)
    if adx >= TREND_ADX_MODERATE_LOWER:
        return 0.4 + (adx - TREND_ADX_MODERATE_LOWER) / (
            TREND_ADX_THRESHOLD - TREND_ADX_MODERATE_LOWER
        ) * 0.3
    if TREND_ADX_MODERATE_LOWER > 0:
        return _clip(adx / TREND_ADX_MODERATE_LOWER * 0.4)
    return 0.0


def analyze_trend(candles: list[Candle]) -> dict:
    """Analyse trend direction and strength from a closed-candle series.

    Pipeline:
      1. Filter to closed candles (Section 6 Bug 3).
      2. Compute EMA_FAST and EMA_SLOW on closes.
      3. Compute ADX, +DI, -DI.
      4. Determine direction:
           * ``bullish``: ``ema_fast > ema_slow`` AND ``close > ema_fast``
           * ``bearish``: ``ema_fast < ema_slow`` AND ``close < ema_fast``
           * ``neutral`` otherwise.
      5. Map ADX to strength in ``[0, 1]``.

    Args:
        candles: Input candle list.

    Returns:
        Dict with keys: ``direction``, ``strength``, ``adx``, ``plus_di``,
        ``minus_di``, ``ema_fast``, ``ema_slow``, ``reasons``.  When there is
        insufficient data, returns a neutral-safe dict and a human-readable
        reason list.
    """
    closed = _filter_closed(candles)

    if not closed:
        return _empty_trend(reasons=["no_closed_candles"])

    symbol = closed[0].symbol
    timeframe = closed[0].timeframe
    last_close = closed[-1].close

    min_required = max(TREND_EMA_SLOW + 1, ADX_PERIOD * 2 + 1)
    if len(closed) < min_required:
        logger.warning(
            "trend_analyzed",
            timestamp=datetime.utcnow(),
            symbol=symbol,
            timeframe=timeframe,
            direction="neutral",
            strength=0.0,
            adx=0.0,
            reason=f"insufficient_candles:{len(closed)}/{min_required}",
        )
        return _empty_trend(
            reasons=[f"insufficient_candles:{len(closed)}/{min_required}"],
            symbol=symbol,
            timeframe=timeframe,
        )

    closes = [c.close for c in closed]
    ema_fast_series = calculate_ema(closes, TREND_EMA_FAST)
    ema_slow_series = calculate_ema(closes, TREND_EMA_SLOW)

    ema_fast = ema_fast_series[-1]
    ema_slow = ema_slow_series[-1]

    if any(np.isnan(x) for x in (ema_fast, ema_slow)):
        logger.warning(
            "trend_analyzed",
            timestamp=datetime.utcnow(),
            symbol=symbol,
            timeframe=timeframe,
            direction="neutral",
            strength=0.0,
            adx=0.0,
            reason="ema_not_ready",
        )
        return _empty_trend(
            reasons=["ema_not_ready"],
            symbol=symbol,
            timeframe=timeframe,
        )

    adx, plus_di, minus_di = calculate_adx(closed, period=ADX_PERIOD)
    strength = _strength_from_adx(adx)

    direction = "neutral"
    reasons: list[str] = []

    if ema_fast > ema_slow and last_close > ema_fast:
        direction = "bullish"
        reasons.append(f"ema_fast({ema_fast:.4f}) > ema_slow({ema_slow:.4f})")
        reasons.append(f"close({last_close:.4f}) > ema_fast({ema_fast:.4f})")
    elif ema_fast < ema_slow and last_close < ema_fast:
        direction = "bearish"
        reasons.append(f"ema_fast({ema_fast:.4f}) < ema_slow({ema_slow:.4f})")
        reasons.append(f"close({last_close:.4f}) < ema_fast({ema_fast:.4f})")
    else:
        reasons.append(
            f"ema_alignment inconclusive: ema_fast={ema_fast:.4f}, "
            f"ema_slow={ema_slow:.4f}, close={last_close:.4f}"
        )

    if adx >= TREND_ADX_THRESHOLD:
        reasons.append(f"adx={adx:.2f} strong_trend")
    elif adx >= TREND_ADX_MODERATE_LOWER:
        reasons.append(f"adx={adx:.2f} moderate_trend")
    else:
        reasons.append(f"adx={adx:.2f} weak_or_ranging")

    if plus_di > minus_di:
        reasons.append(f"+DI({plus_di:.2f}) > -DI({minus_di:.2f})")
    elif minus_di > plus_di:
        reasons.append(f"-DI({minus_di:.2f}) > +DI({plus_di:.2f})")

    logger.info(
        "trend_analyzed",
        timestamp=datetime.utcnow(),
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        strength=strength,
        adx=adx,
        plus_di=plus_di,
        minus_di=minus_di,
    )

    return {
        "direction": direction,
        "strength": strength,
        "adx": adx,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "ema_fast_aligned": last_close > ema_fast if direction == "bullish" else last_close < ema_fast if direction == "bearish" else False,
        "ema_slow_aligned": ema_fast > ema_slow if direction == "bullish" else ema_fast < ema_slow if direction == "bearish" else False,
        "reasons": reasons,
    }


def _empty_trend(
    reasons: list[str],
    symbol: str = "",
    timeframe: str = "",
) -> dict:
    """Return a neutral-safe trend dict for edge cases."""
    return {
        "direction": "neutral",
        "strength": 0.0,
        "adx": 0.0,
        "plus_di": 0.0,
        "minus_di": 0.0,
        "ema_fast": float("nan"),
        "ema_slow": float("nan"),
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# Convenience: full EMA series for callers that need them (charts, etc.)
# ---------------------------------------------------------------------------
def ema_series_for_candles(
    candles: list[Candle],
    fast: int = TREND_EMA_FAST,
    slow: int = TREND_EMA_SLOW,
) -> tuple[list[float], list[float]]:
    """Return the full EMA(fast) and EMA(slow) series for a candle list."""
    closed = _filter_closed(candles)
    closes = [c.close for c in closed]
    return calculate_ema(closes, fast), calculate_ema(closes, slow)


def calculate_atr(candles: list[Candle], period: int = ADX_PERIOD) -> list[float]:
    """Compute the Average True Range series (Wilder's smoothing).

    Exposed as a convenience for downstream modules (risk, volatility) that
    need ATR for stop-loss / take-profit sizing.  The first ``period - 1``
    entries are ``float('nan')``.

    Args:
        candles: Input candle list.  Unclosed candles are filtered out.
        period: Wilder smoothing window.

    Returns:
        ATR series aligned with the *closed* candle list.
    """
    closed = _filter_closed(candles)
    n = len(closed)
    if n < period + 1:
        return [float("nan")] * n

    highs = np.array([c.high for c in closed], dtype=float)
    lows = np.array([c.low for c in closed], dtype=float)
    closes = np.array([c.close for c in closed], dtype=float)

    tr = np.zeros(n, dtype=float)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i] = max(hl, hc, lc)

    return _wilder_smooth(tr, period).tolist()


def detect_ema_crossover(
    candles: list[Candle],
    fast: int = TREND_EMA_FAST,
    slow: int = TREND_EMA_SLOW,
) -> Optional[str]:
    """Detect the most recent EMA(fast) / EMA(slow) crossover direction.

    Args:
        candles: Input candle list (unclosed candles are filtered out).
        fast: Fast EMA period.
        slow: Slow EMA period.

    Returns:
        * ``"bullish"`` if the most recent crossover was fast-crosses-above-slow.
        * ``"bearish"`` if the most recent crossover was fast-crosses-below-slow.
        * ``None`` if there is insufficient data or no crossover has occurred.
    """
    ema_fast, ema_slow = ema_series_for_candles(candles, fast=fast, slow=slow)
    n = len(ema_fast)
    if n < 2:
        return None

    # Walk backwards from the end to find the most recent crossover.
    for i in range(n - 1, 0, -1):
        cur_f, cur_s = ema_fast[i], ema_slow[i]
        prev_f, prev_s = ema_fast[i - 1], ema_slow[i - 1]
        if any(np.isnan(x) for x in (cur_f, cur_s, prev_f, prev_s)):
            continue
        if prev_f <= prev_s and cur_f > cur_s:
            return "bullish"
        if prev_f >= prev_s and cur_f < cur_s:
            return "bearish"
    return None


def trend_series_for_candles(candles: list[Candle]) -> dict:
    """Return the full EMA / ATR / ADX-series for a candle list.

    Useful for charts or backtests that need every value, not just the latest.
    """
    closed = _filter_closed(candles)
    closes = [c.close for c in closed]
    ema_fast = calculate_ema(closes, TREND_EMA_FAST)
    ema_slow = calculate_ema(closes, TREND_EMA_SLOW)
    atr = calculate_atr(closed, ADX_PERIOD)
    return {
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "atr": atr,
    }


def is_trending(candles: list[Candle], min_adx: float = TREND_ADX_THRESHOLD) -> bool:
    """Convenience predicate: is the market trending (ADX >= ``min_adx``)?

    Used by market/regime.py and engine/htf_filter.py to short-circuit
    regime classification when ADX is unambiguous.
    """
    closed = _filter_closed(candles)
    if len(closed) < ADX_PERIOD * 2 + 1:
        return False
    adx, _, _ = calculate_adx(closed, ADX_PERIOD)
    return adx >= min_adx
