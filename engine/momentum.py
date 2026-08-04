"""
File: engine/momentum.py
1. Single Responsibility: Calculate momentum indicators (RSI, MACD,
   Stochastic) and aggregate them into a single momentum score + direction.
2. Consumes: ``Candle`` (contracts/market.py), thresholds from
   config/thresholds.py.
3. Produces: ``calculate_rsi``, ``calculate_macd``, ``calculate_stochastic``,
   ``calculate_momentum`` returning a dict consumed by engine/confidence.py
   and engine/orchestrator.py.
4. Downstream: engine/confidence.py, engine/orchestrator.py.
5. New Dependencies: numpy (already in requirements.txt).
6. Touches Section 6 bugs? Bug 3 (repainting) -- ``calculate_momentum`` filters
   out unclosed candles before computing indicators.
7. Tests: Indirectly exercised via the orchestrator Section 10 acceptance
   criteria and the confidence module tests.
8. Logging: ``momentum_calculated`` per the monitoring/logger.py event catalog.
9. Dependency Order: config -> contracts/market.py -> monitoring/logger.py ->
   engine/momentum.py (no upstream violations).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import numpy as np

from config.thresholds import (
    MOMENTUM_MACD_FAST,
    MOMENTUM_MACD_SIGNAL,
    MOMENTUM_MACD_SLOW,
    MOMENTUM_RSI_OVERBOUGHT,
    MOMENTUM_RSI_OVERSOLD,
    MOMENTUM_RSI_PERIOD,
    MOMENTUM_STOCH_OVERBOUGHT,
    MOMENTUM_STOCH_OVERSOLD,
    MOMENTUM_STOCH_PERIOD,
    MOMENTUM_STOCH_SMOOTH_D,
    MOMENTUM_STOCH_SMOOTH_K,
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


def _sma(values: np.ndarray, period: int) -> np.ndarray:
    """Simple moving average that correctly skips windows containing NaN.

    Returns an array of the same length as ``values``; positions whose
    ``period``-length window contains any NaN (including the first ``period -
    1`` positions, which have insufficient history) are left as NaN.
    """
    n = len(values)
    out = np.full(n, np.nan, dtype=float)
    if n < period or period < 1:
        return out
    for i in range(period - 1, n):
        window = values[i - period + 1 : i + 1]
        if np.any(np.isnan(window)):
            continue
        out[i] = float(np.mean(window))
    return out


def _wilder_smooth(values: np.ndarray, period: int) -> np.ndarray:
    """Wilder's smoothing for RSI average gain/loss."""
    n = len(values)
    out = np.full(n, np.nan, dtype=float)
    if n < period:
        return out
    out[period - 1] = float(np.mean(values[:period]))
    for i in range(period, n):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------
def calculate_rsi(
    closes: list[float],
    period: int = MOMENTUM_RSI_PERIOD,
) -> list[float]:
    """Calculate RSI using Wilder's smoothing.

    Args:
        closes: Close-price series.
        period: RSI lookback.

    Returns:
        List of the same length as ``closes``.  The first ``period`` entries
        are ``float('nan')``.  When a window has zero losses, RSI is defined as
        100.0; when zero gains, 0.0.
    """
    n = len(closes)
    if n < period + 1:
        return [float("nan")] * n

    arr = np.asarray(closes, dtype=float)
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = _wilder_smooth(gains, period)
    avg_loss = _wilder_smooth(losses, period)

    rsi = np.full(n, np.nan, dtype=float)
    # ``avg_gain`` / ``avg_loss`` are aligned to ``closes`` by index+1 because
    # they come from np.diff (length n-1).  We map them to closes[1:].
    for i in range(period, n):
        ag = avg_gain[i - 1]
        al = avg_loss[i - 1]
        if np.isnan(ag) or np.isnan(al):
            continue
        if al == 0:
            rsi[i] = 100.0
        elif ag == 0:
            rsi[i] = 0.0
        else:
            rs = ag / al
            rsi[i] = 100.0 - (100.0 / (1.0 + rs))

    return rsi.tolist()


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------
def calculate_macd(
    closes: list[float],
    fast: int = MOMENTUM_MACD_FAST,
    slow: int = MOMENTUM_MACD_SLOW,
    signal: int = MOMENTUM_MACD_SIGNAL,
) -> tuple[list[float], list[float], list[float]]:
    """Calculate MACD line, signal line, and histogram.

    Args:
        closes: Close-price series.
        fast: Fast EMA period.
        slow: Slow EMA period.
        signal: Signal-line EMA period (applied to the MACD line).

    Returns:
        ``(macd_line, signal_line, histogram)`` -- three lists of the same
        length as ``closes``.  Entries before enough data is available are
        ``float('nan')``.
    """
    # Import locally to avoid a circular import at module load time.  engine
    # .trend imports contracts + config + monitoring only, same as this module,
    # so the import is safe -- but doing it locally keeps the dependency
    # direction explicit and avoids any chance of a load-order surprise.
    from engine.trend import calculate_ema

    n = len(closes)
    if n < slow + signal:
        nan_list = [float("nan")] * n
        return nan_list, list(nan_list), list(nan_list)

    ema_fast = np.asarray(calculate_ema(closes, fast), dtype=float)
    ema_slow = np.asarray(calculate_ema(closes, slow), dtype=float)

    macd_line = ema_fast - ema_slow

    # The signal line is an EMA of the MACD line.  We need to feed only the
    # non-NaN portion to ``calculate_ema`` to keep the seed SMA meaningful.
    first_valid = np.argmax(np.isfinite(macd_line))
    if not np.isfinite(macd_line[first_valid]):
        nan_list = [float("nan")] * n
        return nan_list, list(nan_list), list(nan_list)

    macd_valid = macd_line[first_valid:].tolist()
    signal_valid = np.asarray(calculate_ema(macd_valid, signal), dtype=float)

    signal_line = np.full(n, np.nan, dtype=float)
    signal_line[first_valid:] = signal_valid

    histogram = macd_line - signal_line

    return macd_line.tolist(), signal_line.tolist(), histogram.tolist()


# ---------------------------------------------------------------------------
# Stochastic
# ---------------------------------------------------------------------------
def calculate_stochastic(
    candles: list[Candle],
    k_period: int = MOMENTUM_STOCH_PERIOD,
    k_smooth: int = MOMENTUM_STOCH_SMOOTH_K,
    d_smooth: int = MOMENTUM_STOCH_SMOOTH_D,
) -> tuple[list[float], list[float]]:
    """Calculate stochastic oscillator %K and %D.

    Args:
        candles: Input candle list.  Unclosed candles are filtered out.
        k_period: Lookback for the highest-high / lowest-low calculation.
        k_smooth: SMA smoothing of the raw %K.
        d_smooth: SMA smoothing of %K to produce %D.

    Returns:
        ``(k_series, d_series)`` -- two lists of the same length as the
        *closed* candle list.  Entries before enough data is available are
        ``float('nan')``.
    """
    closed = _filter_closed(candles)
    n = len(closed)
    if n < k_period:
        return [float("nan")] * n, [float("nan")] * n

    highs = np.array([c.high for c in closed], dtype=float)
    lows = np.array([c.low for c in closed], dtype=float)
    closes = np.array([c.close for c in closed], dtype=float)

    raw_k = np.full(n, np.nan, dtype=float)
    for i in range(k_period - 1, n):
        hh = float(np.max(highs[i - k_period + 1 : i + 1]))
        ll = float(np.min(lows[i - k_period + 1 : i + 1]))
        if hh - ll == 0:
            raw_k[i] = 50.0
        else:
            raw_k[i] = (closes[i] - ll) / (hh - ll) * 100.0

    k_series = _sma(raw_k, k_smooth)
    d_series = _sma(k_series, d_smooth)

    return k_series.tolist(), d_series.tolist()


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------
def calculate_momentum(candles: list[Candle]) -> dict:
    """Aggregate RSI, MACD, and Stochastic into a single momentum signal.

    Pipeline:
      1. Filter to closed candles (Section 6 Bug 3).
      2. Compute RSI(MOMENTUM_RSI_PERIOD), MACD, Stochastic.
      3. Score each indicator on a ``[-1, +1]`` scale:
           * RSI:
               - ``< MOMENTUM_RSI_OVERSOLD``  -> +1 (bullish reversal)
               - ``> MOMENTUM_RSI_OVERBOUGHT`` -> -1 (bearish reversal)
               - otherwise 0
           * MACD:
               - histogram > 0 -> +1
               - histogram < 0 -> -1
               - otherwise 0
           * Stochastic:
               - ``%K > %D`` and ``%K < MOMENTUM_STOCH_OVERBOUGHT`` -> +1
               - ``%K < %D`` and ``%K > MOMENTUM_STOCH_OVERSOLD``     -> -1
               - otherwise 0
      4. ``raw = (rsi_score + macd_score + stoch_score) / 3`` (in [-1, 1])
      5. ``momentum_score = (raw + 1) / 2`` (in [0, 1])
      6. ``direction = "long" if raw > 0 else "short" if raw < 0 else "long"``
         (neutral ties default to "long" with a ``neutral_momentum`` reason).

    Args:
        candles: Input candle list.

    Returns:
        Dict with keys: ``rsi``, ``macd_line``, ``macd_signal``, ``macd_hist``,
        ``stoch_k``, ``stoch_d``, ``momentum_score``, ``direction``,
        ``reasons``.  When there is insufficient data, returns a safe default
        dict with ``momentum_score = 0.5`` and a ``insufficient_data`` reason.
    """
    closed = _filter_closed(candles)

    if not closed:
        return _empty_momentum(["no_closed_candles"])

    symbol = closed[0].symbol
    timeframe = closed[0].timeframe

    min_required = max(
        MOMENTUM_MACD_SLOW + MOMENTUM_MACD_SIGNAL,
        MOMENTUM_RSI_PERIOD + 1,
        MOMENTUM_STOCH_PERIOD + MOMENTUM_STOCH_SMOOTH_K + MOMENTUM_STOCH_SMOOTH_D,
    )
    if len(closed) < min_required:
        logger.warning(
            "momentum_calculated",
            timestamp=datetime.utcnow(),
            symbol=symbol,
            timeframe=timeframe,
            rsi=0.0,
            macd=0.0,
            score=0.5,
            reason=f"insufficient_candles:{len(closed)}/{min_required}",
        )
        return _empty_momentum(
            [f"insufficient_candles:{len(closed)}/{min_required}"],
            symbol=symbol,
            timeframe=timeframe,
        )

    closes = [c.close for c in closed]

    rsi_series = calculate_rsi(closes, period=MOMENTUM_RSI_PERIOD)
    macd_line_s, signal_line_s, hist_s = calculate_macd(closes)
    k_series, d_series = calculate_stochastic(closed)

    rsi = rsi_series[-1]
    macd_line = macd_line_s[-1]
    macd_signal = signal_line_s[-1]
    macd_hist = hist_s[-1]
    stoch_k = k_series[-1]
    stoch_d = d_series[-1]

    if any(np.isnan(x) for x in (rsi, macd_line, macd_signal, macd_hist, stoch_k, stoch_d)):
        logger.warning(
            "momentum_calculated",
            timestamp=datetime.utcnow(),
            symbol=symbol,
            timeframe=timeframe,
            rsi=float(rsi) if not np.isnan(rsi) else 0.0,
            macd=float(macd_hist) if not np.isnan(macd_hist) else 0.0,
            score=0.5,
            reason="indicator_not_ready",
        )
        return _empty_momentum(
            ["indicator_not_ready"],
            symbol=symbol,
            timeframe=timeframe,
        )

    reasons: list[str] = []

    # RSI score
    rsi_score = 0.0
    rsi_required = f"<{MOMENTUM_RSI_OVERSOLD} or >{MOMENTUM_RSI_OVERBOUGHT}"
    if rsi < MOMENTUM_RSI_OVERSOLD:
        rsi_score = 1.0
        reasons.append(f"RSI({rsi:.2f}) oversold (<{MOMENTUM_RSI_OVERSOLD})")
        rsi_result = "PASS"
    elif rsi > MOMENTUM_RSI_OVERBOUGHT:
        rsi_score = -1.0
        reasons.append(f"RSI({rsi:.2f}) overbought (>{MOMENTUM_RSI_OVERBOUGHT})")
        rsi_result = "PASS"
    else:
        reasons.append(f"RSI({rsi:.2f}) neutral")
        rsi_result = "FAIL"

    logger.info(
        "strategy_condition_check",
        timestamp=datetime.utcnow(),
        symbol=symbol,
        timeframe=timeframe,
        strategy="Momentum",
        condition="RSI Check",
        current=round(float(rsi), 2),
        required=rsi_required,
        result=rsi_result
    )

    # MACD score
    macd_score = 0.0
    if macd_hist > 0:
        macd_score = 1.0
        reasons.append(f"MACD histogram({macd_hist:.4f}) positive")
        macd_result = "PASS"
    elif macd_hist < 0:
        macd_score = -1.0
        reasons.append(f"MACD histogram({macd_hist:.4f}) negative")
        macd_result = "PASS"
    else:
        reasons.append(f"MACD histogram({macd_hist:.4f}) flat")
        macd_result = "FAIL"

    logger.info(
        "strategy_condition_check",
        timestamp=datetime.utcnow(),
        symbol=symbol,
        timeframe=timeframe,
        strategy="Momentum",
        condition="MACD Histogram",
        current=round(float(macd_hist), 4),
        required="Non-zero histogram",
        result=macd_result
    )

    # Stochastic score
    stoch_score = 0.0
    if stoch_k > stoch_d and stoch_k < MOMENTUM_STOCH_OVERBOUGHT:
        stoch_score = 1.0
        reasons.append(
            f"Stoch %K({stoch_k:.2f}) > %D({stoch_d:.2f}) bullish crossover below overbought"
        )
        stoch_result = "PASS"
    elif stoch_k < stoch_d and stoch_k > MOMENTUM_STOCH_OVERSOLD:
        stoch_score = -1.0
        reasons.append(
            f"Stoch %K({stoch_k:.2f}) < %D({stoch_d:.2f}) bearish crossover above oversold"
        )
        stoch_result = "PASS"
    else:
        reasons.append(
            f"Stoch %K({stoch_k:.2f}) / %D({stoch_d:.2f}) no actionable crossover"
        )
        stoch_result = "FAIL"

    logger.info(
        "strategy_condition_check",
        timestamp=datetime.utcnow(),
        symbol=symbol,
        timeframe=timeframe,
        strategy="Momentum",
        condition="Stochastic Cross",
        current=f"k={round(float(stoch_k), 1)}, d={round(float(stoch_d), 1)}",
        required="k > d or k < d",
        result=stoch_result
    )

    raw = (rsi_score + macd_score + stoch_score) / 3.0
    momentum_score = _clip((raw + 1.0) / 2.0)

    if raw > 0:
        direction = "long"
        reasons.append(f"aggregated momentum bullish (raw={raw:+.2f})")
    elif raw < 0:
        direction = "neutral"
        reasons.append(f"aggregated momentum bearish (raw={raw:+.2f})")
    else:
        direction = "neutral"
        reasons.append("aggregated momentum neutral")

    logger.info(
        "momentum_calculated",
        timestamp=datetime.utcnow(),
        symbol=symbol,
        timeframe=timeframe,
        rsi=float(rsi),
        macd=float(macd_hist),
        score=float(momentum_score),
        direction=direction,
    )

    return {
        "rsi": float(rsi),
        "macd_line": float(macd_line),
        "macd_signal": float(macd_signal),
        "macd_hist": float(macd_hist),
        "stoch_k": float(stoch_k),
        "stoch_d": float(stoch_d),
        "momentum_score": float(momentum_score),
        "direction": direction,
        "reasons": reasons,
    }


def _empty_momentum(
    reasons: list[str],
    symbol: str = "",
    timeframe: str = "",
) -> dict:
    """Return a neutral-safe momentum dict for edge cases."""
    return {
        "rsi": 50.0,
        "macd_line": 0.0,
        "macd_signal": 0.0,
        "macd_hist": 0.0,
        "stoch_k": 50.0,
        "stoch_d": 50.0,
        "momentum_score": 0.5,
        "direction": "long",
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# Convenience: full indicator series for callers that need them (charts, etc.)
# ---------------------------------------------------------------------------
def momentum_series_for_candles(candles: list[Candle]) -> dict:
    """Return the full RSI / MACD / Stochastic series for a candle list.

    Useful for charts or backtests that need every value, not just the latest.
    """
    closed = _filter_closed(candles)
    closes = [c.close for c in closed]
    rsi = calculate_rsi(closes)
    macd_line, macd_signal, macd_hist = calculate_macd(closes)
    stoch_k, stoch_d = calculate_stochastic(closed)
    return {
        "rsi": rsi,
        "macd_line": macd_line,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "stoch_k": stoch_k,
        "stoch_d": stoch_d,
    }


def detect_rsi_divergence(
    candles: list[Candle],
    swing_points: list,
    period: int = MOMENTUM_RSI_PERIOD,
) -> Optional[str]:
    """Detect the most recent RSI divergence against swing points.

    *Bullish divergence*: price makes a lower low while RSI makes a higher low.
    *Bearish divergence*: price makes a higher high while RSI makes a lower high.

    Args:
        candles: Input candle list (unclosed candles are filtered out).
        swing_points: Swing points produced by
            :func:`engine.structure.detect_swing_points`.  Only the last two of
            each type are inspected.
        period: RSI lookback.

    Returns:
        ``"bullish"``, ``"bearish"``, or ``None`` if no divergence is found.
    """
    closed = _filter_closed(candles)
    if len(closed) < period + 1:
        return None
    rsi_series = calculate_rsi([c.close for c in closed], period=period)

    swing_highs = [s for s in swing_points if s.type == "high"]
    swing_lows = [s for s in swing_points if s.type == "low"]

    # Bearish divergence: two most recent swing highs.
    if len(swing_highs) >= 2:
        prev, last = swing_highs[-2], swing_highs[-1]
        if (
            last.price > prev.price
            and 0 <= last.index < len(rsi_series)
            and 0 <= prev.index < len(rsi_series)
            and not np.isnan(rsi_series[last.index])
            and not np.isnan(rsi_series[prev.index])
            and rsi_series[last.index] < rsi_series[prev.index]
        ):
            return "bearish"

    # Bullish divergence: two most recent swing lows.
    if len(swing_lows) >= 2:
        prev, last = swing_lows[-2], swing_lows[-1]
        if (
            last.price < prev.price
            and 0 <= last.index < len(rsi_series)
            and 0 <= prev.index < len(rsi_series)
            and not np.isnan(rsi_series[last.index])
            and not np.isnan(rsi_series[prev.index])
            and rsi_series[last.index] > rsi_series[prev.index]
        ):
            return "bullish"

    return None


def macd_crossover(closes: list[float]) -> Optional[str]:
    """Detect the most recent MACD line / signal line crossover.

    Args:
        closes: Close-price series.

    Returns:
        ``"bullish"`` if MACD crossed above signal, ``"bearish"`` if it crossed
        below, ``None`` if there is insufficient data or no crossover.
    """
    macd_line, signal_line, _ = calculate_macd(closes)
    n = len(macd_line)
    if n < 2:
        return None
    for i in range(n - 1, 0, -1):
        cur_m, cur_s = macd_line[i], signal_line[i]
        prev_m, prev_s = macd_line[i - 1], signal_line[i - 1]
        if any(np.isnan(x) for x in (cur_m, cur_s, prev_m, prev_s)):
            continue
        if prev_m <= prev_s and cur_m > cur_s:
            return "bullish"
        if prev_m >= prev_s and cur_m < cur_s:
            return "bearish"
    return None
