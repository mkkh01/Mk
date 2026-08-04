"""
File: market/volatility.py
1. Single Responsibility: Calculate volatility metrics (ATR, ATR%, Bollinger
   Bands) for a list of closed candles and provide boolean convenience flags.
2. Consumes: Candle (contracts/market.py); config.thresholds; monitoring.logger.
3. Produces: calculate_volatility(), calculate_atr(), calculate_bollinger_bands(),
   is_high_volatility(), is_ranging() consumed by engine/risk.py (SL/TP sizing)
   and engine/confidence.py.
4. Downstream: engine/risk.py (VOLATILITY_ATR_MULTIPLIER_SL / _TP),
   engine/confidence.py, market/regime.py (could reuse helpers).
5. New Dependencies: numpy (already in Section 3 requirements.txt).
6. Touches Section 6 bugs? No.
7. Tests: exercised indirectly by Section 10 market/regime.py tests
   (ATR expansion -> VOLATILE) since regime.py delegates to the same
   formula; also covered by any future tests/unit/test_volatility.py.
8. Logging: volatility_calculated {timestamp, symbol, atr, atr_percent,
   bb_width}.
9. Dependency Order: contracts -> monitoring -> market/volatility.py
   (no upstream violations; does not import engine.*).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from config.thresholds import (
    HIGH_VOLATILITY_THRESHOLD,
    VOLATILITY_ATR_PERIOD,
    VOLATILITY_BB_PERIOD,
    VOLATILITY_BB_RANGING_PCT,
    VOLATILITY_BB_STD,
)
from contracts.market import Candle
from monitoring.logger import get_logger

logger = get_logger(__name__)


_MIN_ATR_CANDLES = VOLATILITY_ATR_PERIOD + 1
_MIN_BB_CANDLES = VOLATILITY_BB_PERIOD


# ---------------------------------------------------------------------------
# True Range
# ---------------------------------------------------------------------------
def _true_range(candles: list[Candle]) -> np.ndarray:
    """Compute Wilder's True Range for every candle.

    TR[i] = max(high - low, |high - prev_close|, |low - prev_close|).
    TR[0] = high[0] - low[0] (no previous close available).
    """
    n = len(candles)
    if n == 0:
        return np.zeros(0, dtype=float)
    highs = np.fromiter((c.high for c in candles), dtype=float, count=n)
    lows = np.fromiter((c.low for c in candles), dtype=float, count=n)
    closes = np.fromiter((c.close for c in candles), dtype=float, count=n)

    tr = highs - lows
    if n > 1:
        hl = highs[1:] - lows[1:]
        hc = np.abs(highs[1:] - closes[:-1])
        lc = np.abs(lows[1:] - closes[:-1])
        tr[1:] = np.maximum(np.maximum(hl, hc), lc)
    return tr


def _wilder_smooth(values: np.ndarray, period: int) -> np.ndarray:
    """Wilder's smoothing (= EMA with alpha = 1/period).

    The first ``period`` samples seed the average at index ``period`` using
    the simple mean of values[1:period+1]; subsequent samples follow the
    recurrence ``out[i] = (out[i-1] * (period-1) + values[i]) / period``.
    """
    n = values.shape[0]
    out = np.zeros(n, dtype=float)
    if n < period + 1:
        return out
    out[period] = float(np.mean(values[1 : period + 1]))
    for i in range(period + 1, n):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------
def calculate_atr(
    candles: list[Candle], period: int = VOLATILITY_ATR_PERIOD
) -> float:
    """Calculate the Average True Range using Wilder's smoothing.

    Returns 0.0 if there are fewer than ``period + 1`` candles (Section 22:
    insufficient candles -> safe default).
    """
    if len(candles) < _MIN_ATR_CANDLES:
        return 0.0
    tr = _true_range(candles)
    atr_arr = _wilder_smooth(tr, period)
    val = float(atr_arr[-1])
    if not np.isfinite(val):
        return 0.0
    return val


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------
def calculate_bollinger_bands(
    candles: list[Candle],
    period: int = VOLATILITY_BB_PERIOD,
    std_dev: float = VOLATILITY_BB_STD,
) -> tuple[float, float, float]:
    """Return ``(upper, middle, lower)`` for the latest closed candle.

    ``middle`` is the simple moving average of the last ``period`` closes.
    ``upper`` / ``lower`` are ``middle +/- std_dev * population_std``.

    Returns ``(0.0, 0.0, 0.0)`` on insufficient data.
    """
    n = len(candles)
    if n < period:
        return 0.0, 0.0, 0.0
    closes = np.fromiter((c.close for c in candles), dtype=float, count=n)
    window = closes[-period:]
    middle = float(window.mean())
    std_val = float(window.std(ddof=0))
    upper = middle + std_dev * std_val
    lower = middle - std_dev * std_val
    return upper, middle, lower


# ---------------------------------------------------------------------------
# Aggregate volatility snapshot
# ---------------------------------------------------------------------------
def calculate_volatility(candles: list[Candle]) -> dict[str, Any]:
    """Calculate the full volatility snapshot for the latest closed candle.

    Returns a dict with the following keys (all floats):

      * ``atr``         -- absolute ATR(14) (Wilder).
      * ``atr_percent`` -- ATR / close * 100 (normalised volatility).
      * ``bb_upper``    -- upper Bollinger Band.
      * ``bb_middle``   -- middle Bollinger Band (SMA20).
      * ``bb_lower``    -- lower Bollinger Band.
      * ``bb_width``    -- (upper - lower) / middle (ratio, 0..1+).

    All values degrade to 0.0 on insufficient data (Section 22). Emits a
    ``volatility_calculated`` log event.
    """
    n = len(candles)
    last_close = candles[-1].close if candles else 0.0
    last_symbol = candles[-1].symbol if candles else ""
    last_close_time = candles[-1].close_time if candles else None

    atr = calculate_atr(candles, VOLATILITY_ATR_PERIOD)
    atr_percent = (atr / last_close * 100.0) if last_close > 0 else 0.0
    bb_upper, bb_middle, bb_lower = calculate_bollinger_bands(
        candles, VOLATILITY_BB_PERIOD, VOLATILITY_BB_STD
    )
    if bb_middle > 0:
        bb_width = (bb_upper - bb_lower) / bb_middle
    else:
        bb_width = 0.0
    if not np.isfinite(bb_width):
        bb_width = 0.0

    snapshot: dict[str, Any] = {
        "atr": float(atr),
        "atr_percent": float(atr_percent),
        "bb_upper": float(bb_upper),
        "bb_middle": float(bb_middle),
        "bb_lower": float(bb_lower),
        "bb_width": float(bb_width),
    }

    logger.info(
        "volatility_calculated",
        timestamp=last_close_time.isoformat() if last_close_time else None,
        symbol=last_symbol,
        atr=round(snapshot["atr"], 6),
        atr_percent=round(snapshot["atr_percent"], 4),
        bb_width=round(snapshot["bb_width"], 6),
        candle_count=n,
    )
    return snapshot


# ---------------------------------------------------------------------------
# Boolean convenience flags
# ---------------------------------------------------------------------------
def is_high_volatility(candles: list[Candle]) -> bool:
    """True iff ATR/close (%) exceeds ``HIGH_VOLATILITY_THRESHOLD``.

    Handles the edge cases (insufficient candles, zero close) by returning
    ``False`` rather than raising.
    """
    if not candles or candles[-1].close <= 0:
        return False
    atr = calculate_atr(candles, VOLATILITY_ATR_PERIOD)
    if atr <= 0:
        return False
    atr_percent = atr / candles[-1].close * 100.0
    return atr_percent > HIGH_VOLATILITY_THRESHOLD


def is_ranging(candles: list[Candle]) -> bool:
    """True iff Bollinger Band width (%) is below ``VOLATILITY_BB_RANGING_PCT``.

    Handles insufficient candles or zero middle by returning ``False``.
    """
    if len(candles) < _MIN_BB_CANDLES:
        return False
    _upper, middle, _lower = calculate_bollinger_bands(
        candles, VOLATILITY_BB_PERIOD, VOLATILITY_BB_STD
    )
    if middle <= 0:
        return False
    bb_width_pct = (_upper - _lower) / middle * 100.0
    return bb_width_pct < VOLATILITY_BB_RANGING_PCT


__all__ = [
    "calculate_volatility",
    "calculate_atr",
    "calculate_bollinger_bands",
    "is_high_volatility",
    "is_ranging",
]
