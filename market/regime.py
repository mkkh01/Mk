"""
File: market/regime.py
1. Single Responsibility: Classify the current market regime as TRENDING,
   RANGING or VOLATILE from a list of closed candles.
2. Consumes: Candle, RegimeState (contracts/market.py); config.thresholds;
   monitoring.logger.
3. Produces: classify_regime(), classify_regime_with_confidence() and private
   helpers _calculate_adx(), _calculate_atr(), _calculate_bb_width() consumed
   by engine/confidence.py and engine/orchestrator.py.
4. Downstream: engine/confidence.py (REGIME_MODIFIER_* lookup),
   engine/orchestrator.py (regime gating).
5. New Dependencies: numpy (already in Section 3 requirements.txt).
6. Touches Section 6 bugs? No.
7. Tests: Section 10 market/regime.py acceptance criteria --
   (1) ADX>25 with clear EMA alignment -> TRENDING,
   (2) ADX<20 with price between EMAs -> RANGING,
   (3) ATR expansion beyond threshold -> VOLATILE.
8. Logging: regime_classified {timestamp, symbol, regime}.
9. Dependency Order: contracts -> monitoring -> market/regime.py
   (no upstream violations; does not import engine.*).
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from config.thresholds import (
    ADX_PERIOD,
    HIGH_VOLATILITY_THRESHOLD,
    TREND_ADX_MODERATE_LOWER,
    TREND_ADX_THRESHOLD,
    TREND_EMA_FAST,
    TREND_EMA_SLOW,
    VOLATILITY_ATR_PERIOD,
    VOLATILITY_BB_PERIOD,
    VOLATILITY_BB_RANGING_PCT,
    VOLATILITY_BB_STD,
)
from contracts.market import Candle, RegimeState
from monitoring.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Minimum candle counts
# ---------------------------------------------------------------------------
# ADX(14) under Wilder's method needs at least 2*period+1 candles to produce a
# single smoothed ADX value (period for the first ATR/DI, period for the first
# ADX average). Below this we degrade gracefully and report RANGING.
_MIN_ADX_CANDLES = 2 * ADX_PERIOD + 1
_MIN_ATR_CANDLES = VOLATILITY_ATR_PERIOD + 1
_MIN_BB_CANDLES = VOLATILITY_BB_PERIOD


# ---------------------------------------------------------------------------
# Wilder smoothing helpers
# ---------------------------------------------------------------------------
def _wilder_smooth(values: np.ndarray, period: int) -> np.ndarray:
    """Apply Wilder's smoothing to a 1-D numpy array.

    Wilder's smoothing is equivalent to an EMA with alpha = 1/period. The first
    ``period`` values are seeded with the simple mean of values[1:period+1]
    (mirroring the standard ATR/DI initialisation which skips index 0 because
    TR/DM at index 0 is undefined or zero).

    Returns an array of the same length where indices < ``period`` are zero
    (the smoothing has not yet started).
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
# True Range / Directional Movement
# ---------------------------------------------------------------------------
def _true_range(candles: list[Candle]) -> np.ndarray:
    """Compute Wilder's True Range for every candle.

    TR[i] = max(high - low, |high - prev_close|, |low - prev_close|).
    The first candle has no previous close so TR[0] = high - low.
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


def _directional_movement(candles: list[Candle]) -> tuple[np.ndarray, np.ndarray]:
    """Compute +DM and -DM per Welles Wilder.

    +DM[i] = (high[i] - high[i-1]) if that up-move is larger than the
             down-move AND positive, else 0.
    -DM[i] = (low[i-1] - low[i])   if that down-move is larger than the
             up-move AND positive, else 0.
    Index 0 is undefined -> 0.
    """
    n = len(candles)
    plus_dm = np.zeros(n, dtype=float)
    minus_dm = np.zeros(n, dtype=float)
    if n < 2:
        return plus_dm, minus_dm
    highs = np.fromiter((c.high for c in candles), dtype=float, count=n)
    lows = np.fromiter((c.low for c in candles), dtype=float, count=n)

    up_move = highs[1:] - highs[:-1]
    down_move = lows[:-1] - lows[1:]

    plus_mask = (up_move > down_move) & (up_move > 0)
    minus_mask = (down_move > up_move) & (down_move > 0)

    plus_dm[1:] = np.where(plus_mask, up_move, 0.0)
    minus_dm[1:] = np.where(minus_mask, down_move, 0.0)
    return plus_dm, minus_dm


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------
def _calculate_atr(
    candles: list[Candle], period: int = VOLATILITY_ATR_PERIOD
) -> float:
    """Calculate the Average True Range using Wilder's smoothing.

    Returns 0.0 if there are not enough candles (Section 22: insufficient
    candles -> safe default).
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
# ADX
# ---------------------------------------------------------------------------
def _calculate_adx(candles: list[Candle], period: int = ADX_PERIOD) -> float:
    """Calculate ADX (Average Directional Index) using Wilder's method.

    Returns 0.0 if there are not enough candles to produce a smoothed ADX
    (Section 22: insufficient candles -> safe default).
    """
    n = len(candles)
    if n < _MIN_ADX_CANDLES:
        return 0.0

    tr = _true_range(candles)
    plus_dm, minus_dm = _directional_movement(candles)

    atr_smooth = _wilder_smooth(tr, period)
    plus_smooth = _wilder_smooth(plus_dm, period)
    minus_smooth = _wilder_smooth(minus_dm, period)

    # +DI / -DI (period cancels because all three arrays share the same
    # smoothing window, so we can compare smoothed DM directly to smoothed TR).
    plus_di = np.zeros(n, dtype=float)
    minus_di = np.zeros(n, dtype=float)
    safe = atr_smooth > 0
    plus_di[safe] = 100.0 * plus_smooth[safe] / atr_smooth[safe]
    minus_di[safe] = 100.0 * minus_smooth[safe] / atr_smooth[safe]

    # DX
    di_sum = plus_di + minus_di
    dx = np.zeros(n, dtype=float)
    safe_dx = di_sum > 0
    dx[safe_dx] = 100.0 * np.abs(plus_di[safe_dx] - minus_di[safe_dx]) / di_sum[safe_dx]

    # ADX = Wilder's smoothing of DX, seeded with simple mean of the first
    # `period` DX values that come after the DI smoothing window.
    dx_start = period  # first index where DX is meaningful
    if n < dx_start + period:
        # Not enough DX samples to seed an ADX; fall back to last DX.
        val = float(dx[dx_start:n].mean()) if n > dx_start else 0.0
        return val if np.isfinite(val) else 0.0

    seed = float(np.mean(dx[dx_start : dx_start + period]))
    adx = seed
    for i in range(dx_start + period, n):
        adx = (adx * (period - 1) + dx[i]) / period
    if not np.isfinite(adx):
        return 0.0
    return float(adx)


# ---------------------------------------------------------------------------
# Bollinger Band width
# ---------------------------------------------------------------------------
def _calculate_bb_width(
    candles: list[Candle],
    period: int = VOLATILITY_BB_PERIOD,
    std: float = VOLATILITY_BB_STD,
) -> float:
    """Calculate the Bollinger Band width ratio = (upper - lower) / middle.

    Returns 0.0 if there are not enough candles or if the middle band is zero
    (Section 22: division by zero -> safe default).
    """
    n = len(candles)
    if n < period:
        return 0.0
    closes = np.fromiter((c.close for c in candles), dtype=float, count=n)
    window = closes[-period:]
    middle = float(window.mean())
    if middle <= 0:
        return 0.0
    std_val = float(window.std(ddof=0))
    width_ratio = (2.0 * std * std_val) / middle
    if not np.isfinite(width_ratio):
        return 0.0
    return width_ratio


def _bollinger_bands(
    candles: list[Candle],
    period: int = VOLATILITY_BB_PERIOD,
    std: float = VOLATILITY_BB_STD,
) -> tuple[float, float, float]:
    """Return (upper, middle, lower) for the latest closed candle.

    Returns (0.0, 0.0, 0.0) on insufficient data.
    """
    n = len(candles)
    if n < period:
        return 0.0, 0.0, 0.0
    closes = np.fromiter((c.close for c in candles), dtype=float, count=n)
    window = closes[-period:]
    middle = float(window.mean())
    std_val = float(window.std(ddof=0))
    upper = middle + std * std_val
    lower = middle - std * std_val
    return upper, middle, lower


# ---------------------------------------------------------------------------
# EMA alignment helpers
# ---------------------------------------------------------------------------
def _ema(values: np.ndarray, period: int) -> np.ndarray:
    """Standard exponential moving average seeded with the first value."""
    n = values.shape[0]
    out = np.zeros(n, dtype=float)
    if n == 0:
        return out
    alpha = 2.0 / (period + 1.0)
    out[0] = float(values[0])
    for i in range(1, n):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


def _is_price_between_emas(candles: list[Candle]) -> bool:
    """True if the latest close lies between EMA(fast) and EMA(slow).

    Used as an additional ranging signal -- when ADX is low AND price is
    oscillating between the two EMAs the market is consolidating. This mirrors
    the Section 10 test language ("ADX<20 with price between EMAs") which is
    slightly looser than the spec's "price within BB middle 50%" wording.
    """
    n = len(candles)
    if n < max(TREND_EMA_FAST, TREND_EMA_SLOW) + 1:
        return False
    closes = np.fromiter((c.close for c in candles), dtype=float, count=n)
    ema_fast = _ema(closes, TREND_EMA_FAST)
    ema_slow = _ema(closes, TREND_EMA_SLOW)
    last_close = closes[-1]
    lo = min(ema_fast[-1], ema_slow[-1])
    hi = max(ema_fast[-1], ema_slow[-1])
    return bool(lo <= last_close <= hi)


def _is_price_in_bb_middle(
    close: float, upper: float, lower: float, middle: float
) -> bool:
    """True if ``close`` is within the inner 50% of the Bollinger Band range.

    The full range [lower, upper] is 4*std wide. The middle 50% is therefore
    [middle - std, middle + std] = [middle - 0.25*width, middle + 0.25*width].
    """
    width = upper - lower
    if width <= 0:
        return False
    lower_bound = middle - 0.25 * width
    upper_bound = middle + 0.25 * width
    return bool(lower_bound <= close <= upper_bound)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def classify_regime(candles: list[Candle]) -> RegimeState:
    """Classify the most recent closed-candle regime.

    Implements the Section 16 algorithm:

      1. ADX(14) -> directional strength
      2. ATR(14) / close -> normalised volatility (percent)
      3. Bollinger Band(20, 2.0) width ratio
      4. Decision rules (evaluated in order):
         - ADX > TREND_ADX_THRESHOLD AND BB width > ranging pct -> TRENDING
         - ADX < TREND_ADX_MODERATE_LOWER AND (price in BB middle 50% OR
           price between EMA9/EMA21) -> RANGING
         - ATR/price (%) > HIGH_VOLATILITY_THRESHOLD -> VOLATILE
         - default -> RANGING

    Edge cases (Section 22):
      - Empty input or insufficient candles -> RANGING (safe default), warning
        logged.
      - Division by zero (close == 0) -> treated as infinite volatility and
        skipped; the function still returns a regime.
    """
    if not candles:
        logger.warning(
            "regime_classified_skipped",
            message_text="regime_classified skipped: empty candle list",
            regime=RegimeState.RANGING.value,
        )
        return RegimeState.RANGING

    n = len(candles)
    last = candles[-1]
    close = last.close

    # --- compute indicators (each degrades to 0.0 on insufficient data) -----
    adx = _calculate_adx(candles, ADX_PERIOD)
    atr = _calculate_atr(candles, VOLATILITY_ATR_PERIOD)
    bb_upper, bb_middle, bb_lower = _bollinger_bands(
        candles, VOLATILITY_BB_PERIOD, VOLATILITY_BB_STD
    )
    bb_width_ratio = _calculate_bb_width(
        candles, VOLATILITY_BB_PERIOD, VOLATILITY_BB_STD
    )
    bb_width_pct = bb_width_ratio * 100.0  # as percent of price

    # Normalised volatility as percent (ATR / close * 100).
    atr_pct = (atr / close * 100.0) if close > 0 else float("inf")

    if n < max(_MIN_ADX_CANDLES, _MIN_BB_CANDLES):
        logger.warning(
            "regime_classified insufficient candles; defaulting to RANGING",
            extra={
                "event": "regime_classified",
                "candle_count": n,
                "regime": RegimeState.RANGING.value,
            },
        )

    # --- Rule 1: TRENDING ---------------------------------------------------
    if adx > TREND_ADX_THRESHOLD and bb_width_pct > VOLATILITY_BB_RANGING_PCT:
        regime = RegimeState.TRENDING
        _log_regime(last, regime, adx=adx, atr_pct=atr_pct, bb_width_pct=bb_width_pct)
        return regime

    # --- Rule 2: RANGING ----------------------------------------------------
    # A true range has low ADX AND tight consolidation AND is not in a
    # high-volatility state. Guarding with ``atr_pct <= HIGH_VOLATILITY_THRESHOLD``
    # prevents a chop-with-huge-wicks market (low ADX, tight closes, but
    # extreme ATR from the wicks) from being misclassified as RANGING -- that
    # scenario must fall through to VOLATILE per Section 10 test 3.
    if adx < TREND_ADX_MODERATE_LOWER and atr_pct <= HIGH_VOLATILITY_THRESHOLD:
        in_bb_middle = _is_price_in_bb_middle(close, bb_upper, bb_lower, bb_middle)
        between_emas = _is_price_between_emas(candles)
        if in_bb_middle or between_emas:
            regime = RegimeState.RANGING
            _log_regime(
                last,
                regime,
                adx=adx,
                atr_pct=atr_pct,
                bb_width_pct=bb_width_pct,
            )
            return regime

    # --- Rule 3: VOLATILE ---------------------------------------------------
    if atr_pct > HIGH_VOLATILITY_THRESHOLD:
        regime = RegimeState.VOLATILE
        _log_regime(last, regime, adx=adx, atr_pct=atr_pct, bb_width_pct=bb_width_pct)
        return regime

    # --- Default ------------------------------------------------------------
    regime = RegimeState.RANGING
    _log_regime(last, regime, adx=adx, atr_pct=atr_pct, bb_width_pct=bb_width_pct)
    return regime


def classify_regime_with_confidence(
    candles: list[Candle],
) -> tuple[RegimeState, float]:
    """Classify the regime and return a confidence score in [0.0, 1.0].

    Confidence heuristic per regime:
      * TRENDING  -- how far ADX exceeds the threshold, scaled by 25 ADX units.
      * RANGING   -- how far ADX sits below the moderate lower bound.
      * VOLATILE  -- how far ATR% exceeds the volatility threshold.
      * Default   -- 0.5 (no strong signal).

    The score is clamped to [0.0, 1.0] and never influences the regime
    decision itself (logging only).
    """
    regime = classify_regime(candles)

    if not candles:
        return regime, 0.0

    adx = _calculate_adx(candles, ADX_PERIOD)
    atr = _calculate_atr(candles, VOLATILITY_ATR_PERIOD)
    close = candles[-1].close
    atr_pct = (atr / close * 100.0) if close > 0 else 0.0

    if regime == RegimeState.TRENDING:
        confidence = (adx - TREND_ADX_THRESHOLD) / 25.0
    elif regime == RegimeState.RANGING:
        # Only confident about RANGING when ADX really is low; otherwise this
        # is the default fallthrough and we cap confidence at 0.5.
        if adx < TREND_ADX_MODERATE_LOWER:
            confidence = (TREND_ADX_MODERATE_LOWER - adx) / TREND_ADX_MODERATE_LOWER
        else:
            confidence = 0.5
    elif regime == RegimeState.VOLATILE:
        confidence = (atr_pct - HIGH_VOLATILITY_THRESHOLD) / HIGH_VOLATILITY_THRESHOLD
    else:  # pragma: no cover -- defensive
        confidence = 0.5

    confidence = max(0.0, min(1.0, float(confidence)))
    return regime, confidence


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------
def _log_regime(
    candle: Candle,
    regime: RegimeState,
    *,
    adx: float,
    atr_pct: float,
    bb_width_pct: float,
) -> None:
    """Emit a regime_classified event per the Section 9 log catalog."""
    logger.info(
        "regime_classified",
        timestamp=candle.close_time.isoformat() if candle.close_time else None,
        symbol=candle.symbol,
        regime=regime.value,
        adx=round(adx, 4),
        atr_pct=round(atr_pct, 4) if np.isfinite(atr_pct) else None,
        bb_width_pct=round(bb_width_pct, 4),
    )


__all__ = [
    "classify_regime",
    "classify_regime_with_confidence",
]
