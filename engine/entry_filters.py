"""Conservative long-entry timing gates.

This module is deliberately independent from confidence and portfolio risk.  It
answers one narrow question: is the current closed-candle setup a pullback and
confirmed bounce, rather than an overextended long entry near a local top?
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Optional

from config.thresholds import (
    LONG_NEAR_OVERBOUGHT_STOCH,
    LONG_RSI_NEAR_OVERBOUGHT,
    LONG_RSI_RECOVERY_MAX,
    LONG_RSI_MIN_UPTICK,
    MAX_CONFIRMATION_DISTANCE_ATR,
    MAX_LONG_EXTENSION_ATR,
    MIN_DISTANCE_TO_SWING_HIGH_ATR,
    MIN_DISTANCE_TO_SWING_HIGH_PCT,
    PULLBACK_CONFIRMATION_BODY_RATIO,
    PULLBACK_CONFIRMATION_CLOSE_LOCATION,
    PULLBACK_LOOKBACK_CANDLES,
    PULLBACK_ZONE_TOLERANCE_ATR,
    MOMENTUM_RSI_OVERBOUGHT,
)
from contracts.market import Candle, FairValueGap, MarketStructure, OrderBlock


@dataclass(frozen=True)
class EntryQualityResult:
    """All timing-gate outputs used by the orchestrator and audit logs."""

    allowed: bool
    rsi_ok: bool
    extension_ok: bool
    swing_high_distance_ok: bool
    pullback_ok: bool
    bounce_confirmation_ok: bool
    recovery_ok: bool
    extension_atr: Optional[float]
    distance_to_swing_high_pct: Optional[float]
    distance_to_swing_high_atr: Optional[float]
    pullback_reference: Optional[float]
    reason: str


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if isfinite(result) else default


def _closed(candles: list[Candle]) -> list[Candle]:
    return [candle for candle in candles if candle.is_closed]


def _rsi_gate(momentum: dict[str, Any]) -> tuple[bool, str]:
    rsi = _finite(momentum.get("rsi"), 50.0)
    rsi_prev = _finite(momentum.get("rsi_prev"), rsi)
    stoch_k = _finite(momentum.get("stoch_k"), 50.0)

    if rsi >= MOMENTUM_RSI_OVERBOUGHT:
        return False, "rsi_hard_overbought"

    if (
        rsi >= LONG_RSI_NEAR_OVERBOUGHT
        and (stoch_k >= LONG_NEAR_OVERBOUGHT_STOCH or rsi <= rsi_prev)
    ):
        return False, "rsi_near_overbought_with_exhaustion"

    return True, "rsi_not_exhausted"


def _recovery_gate(momentum: dict[str, Any]) -> tuple[bool, str]:
    rsi = _finite(momentum.get("rsi"), 50.0)
    rsi_prev = _finite(momentum.get("rsi_prev"), rsi)
    rsi_slope = _finite(momentum.get("rsi_slope"), rsi - rsi_prev)

    if rsi <= LONG_RSI_RECOVERY_MAX and rsi_slope < LONG_RSI_MIN_UPTICK:
        return False, "rsi_low_without_recovery"

    if bool(momentum.get("recovery_confirmation")):
        return True, "momentum_recovery_confirmed"

    stoch_k = _finite(momentum.get("stoch_k"), 50.0)
    stoch_d = _finite(momentum.get("stoch_d"), 50.0)
    macd_improving = bool(momentum.get("macd_improving"))
    if (stoch_k > stoch_d and stoch_k <= LONG_NEAR_OVERBOUGHT_STOCH) or macd_improving:
        return True, "momentum_recovery_confirmed"

    return False, "momentum_recovery_missing"


def _find_support_reference(
    candles: list[Candle],
    structure: Optional[MarketStructure],
    ob_list: list[OrderBlock],
    fvg_list: list[FairValueGap],
    current_price: float,
) -> tuple[Optional[float], Optional[float], str]:
    """Select the nearest unmitigated bullish support below current price."""
    candidates: list[tuple[float, float, str]] = []

    for ob in ob_list:
        if ob.type != "bullish" or ob.is_mitigated:
            continue
        level = _finite(ob.mitigation_level)
        low = _finite(ob.low_price)
        high = _finite(ob.high_price)
        if level <= 0 or low <= 0 or high <= 0 or low > high:
            continue
        if level <= current_price:
            candidates.append((current_price - level, level, "bullish_ob"))

    for fvg in fvg_list:
        if fvg.type != "bullish" or fvg.is_filled:
            continue
        bottom = _finite(fvg.bottom)
        top = _finite(fvg.top)
        if bottom <= 0 or top <= 0 or bottom > top:
            continue
        level = top
        if level <= current_price:
            candidates.append((current_price - level, level, "bullish_fvg"))

    if structure is not None and structure.last_swing_low is not None:
        swing_low = _finite(structure.last_swing_low.price)
        if 0 < swing_low <= current_price:
            candidates.append((current_price - swing_low, swing_low, "swing_low"))

    if not candidates:
        return None, None, "support_reference_missing"

    _, level, kind = min(candidates, key=lambda item: item[0])
    return level, level, kind


def _pullback_and_bounce(
    candles: list[Candle],
    support: Optional[float],
    atr: float,
) -> tuple[bool, bool, str]:
    """Require a recent support touch followed by a strong closed bounce."""
    closed = _closed(candles)
    if support is None or len(closed) < 2:
        return False, False, "pullback_support_or_history_missing"

    latest = closed[-1]
    previous = closed[-2]
    lookback = closed[-(PULLBACK_LOOKBACK_CANDLES + 1) : -1]
    if not lookback:
        return False, False, "pullback_history_missing"

    tolerance = max(0.0, atr * PULLBACK_ZONE_TOLERANCE_ATR)
    touched = any(
        candle.low <= support + tolerance and candle.high >= support - tolerance
        for candle in lookback
    )
    if not touched:
        return False, False, "pullback_not_touched"

    candle_range = max(latest.high - latest.low, 0.0)
    if candle_range <= 0:
        return True, False, "bounce_candle_has_no_range"

    body_ratio = abs(latest.close - latest.open) / candle_range
    close_location = (latest.close - latest.low) / candle_range
    bullish = latest.close > latest.open
    closes_above_previous_high = latest.close > previous.high
    close_above_support = latest.close > support
    distance_from_support_atr = (
        (latest.close - support) / atr if atr > 0 else 0.0
    )

    confirmed = bool(
        bullish
        and body_ratio >= PULLBACK_CONFIRMATION_BODY_RATIO
        and close_location >= PULLBACK_CONFIRMATION_CLOSE_LOCATION
        and close_above_support
        and (closes_above_previous_high or latest.close >= support + max(tolerance, 0.0))
        and (
            atr <= 0
            or distance_from_support_atr <= MAX_CONFIRMATION_DISTANCE_ATR
        )
    )
    return True, confirmed, (
        "pullback_and_bounce_confirmed" if confirmed else "bounce_confirmation_missing"
    )


def evaluate_long_entry_quality(
    candles: list[Candle],
    momentum: dict[str, Any],
    trend: dict[str, Any],
    structure: Optional[MarketStructure],
    ob_list: list[OrderBlock],
    fvg_list: list[FairValueGap],
    atr: float,
) -> EntryQualityResult:
    """Evaluate all conservative long timing gates on closed candles."""
    closed = _closed(candles)
    if not closed:
        return EntryQualityResult(
            allowed=False,
            rsi_ok=False,
            extension_ok=False,
            swing_high_distance_ok=False,
            pullback_ok=False,
            bounce_confirmation_ok=False,
            recovery_ok=False,
            extension_atr=None,
            distance_to_swing_high_pct=None,
            distance_to_swing_high_atr=None,
            pullback_reference=None,
            reason="entry_timing_no_closed_candles",
        )

    close = _finite(closed[-1].close)
    atr_value = _finite(atr)
    ema_fast = _finite(trend.get("ema_fast"), 0.0)
    extension_atr = (
        (close - ema_fast) / atr_value
        if close > 0 and ema_fast > 0 and atr_value > 0
        else None
    )
    extension_ok = extension_atr is None or extension_atr <= MAX_LONG_EXTENSION_ATR

    distance_pct: Optional[float] = None
    distance_atr: Optional[float] = None
    if structure is not None and structure.last_swing_high is not None and close > 0:
        swing_high = _finite(structure.last_swing_high.price)
        if swing_high > 0:
            distance_pct = (swing_high - close) / close * 100.0
            distance_atr = (
                (swing_high - close) / atr_value if atr_value > 0 else None
            )
    swing_ok = True
    if distance_pct is not None:
        swing_ok = (
            distance_pct >= MIN_DISTANCE_TO_SWING_HIGH_PCT
            and (
                distance_atr is None
                or distance_atr >= MIN_DISTANCE_TO_SWING_HIGH_ATR
            )
        )

    support, _, support_kind = _find_support_reference(
        closed, structure, ob_list, fvg_list, close
    )
    pullback_ok, bounce_ok, pullback_reason = _pullback_and_bounce(
        closed, support, atr_value
    )
    rsi_ok, rsi_reason = _rsi_gate(momentum)
    recovery_ok, recovery_reason = _recovery_gate(momentum)

    if not rsi_ok:
        reason = rsi_reason
    elif not extension_ok:
        reason = "long_price_extended_from_ema"
    elif not swing_ok:
        reason = "long_too_close_to_recent_swing_high"
    elif not pullback_ok:
        reason = pullback_reason
    elif not bounce_ok:
        reason = pullback_reason
    elif not recovery_ok:
        reason = recovery_reason
    else:
        reason = f"entry_timing_passed:{support_kind}"

    return EntryQualityResult(
        allowed=bool(
            rsi_ok
            and extension_ok
            and swing_ok
            and pullback_ok
            and bounce_ok
            and recovery_ok
        ),
        rsi_ok=rsi_ok,
        extension_ok=extension_ok,
        swing_high_distance_ok=swing_ok,
        pullback_ok=pullback_ok,
        bounce_confirmation_ok=bounce_ok,
        recovery_ok=recovery_ok,
        extension_atr=extension_atr,
        distance_to_swing_high_pct=distance_pct,
        distance_to_swing_high_atr=distance_atr,
        pullback_reference=support,
        reason=reason,
    )


__all__ = ["EntryQualityResult", "evaluate_long_entry_quality"]
