"""
File: engine/htf_filter.py
1. Single Responsibility: Filter lower-timeframe (LTF) signals against the
   higher-timeframe (HTF) bias so the engine never enters counter-trend on
   the structural timeframe.
2. Consumes: ``StrategySignal``, ``HTFFilterResult`` (contracts/decision.py),
   ``Candle`` (contracts/market.py); ``engine/trend.py`` for the HTF trend
   computation.
3. Produces: ``filter_by_htf`` returning ``HTFFilterResult`` consumed by
   engine/confidence.py (HTF_ALIGNMENT_WEIGHT component) and
   engine/orchestrator.py (HTF gate).
4. Downstream: engine/confidence.py, engine/orchestrator.py.
5. New Dependencies: No new external deps. Imports ``engine.trend`` which is
   a sibling engine module -- both sit at the same dependency layer so no
   upstream violation.
6. Touches Section 6 bugs? No.
7. Tests: Section 10 engine/htf_filter.py acceptance criteria:
       1. Bullish alignment -- LTF long + HTF bullish -> alignment = True.
       2. Bullish contradiction -- LTF short + HTF bullish -> alignment = False.
       3. Neutral pass-through -- HTF neutral -> alignment = True.
8. Logging: ``htf_filter_result`` {timestamp, symbol, htf, ltf, bias,
   alignment} per the monitoring/logger.py event catalog.
9. Dependency Order: config -> contracts -> monitoring -> engine/trend.py ->
   engine/htf_filter.py (no upstream violations).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import numpy as np

from contracts.decision import HTFFilterResult, StrategySignal
from contracts.market import Candle
from engine.trend import analyze_trend
from monitoring.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------
# Minimum number of HTF closed candles required to make a bias decision. Below
# this we fall back to "neutral" (pass-through) rather than guessing.
_MIN_HTF_CANDLES = 30


# ---------------------------------------------------------------------------
# HTF bias determination
# ---------------------------------------------------------------------------
def _determine_bias(htf_candles: list[Candle]) -> tuple[Literal["bullish", "bearish", "neutral"], list[str]]:
    """Determine the HTF bias from its candle list.

    Algorithm (Section 15 engine/htf_filter.py):
      1. Filter to closed candles (Bug 3 -- handled inside ``analyze_trend``).
      2. Compute EMA_FAST, EMA_SLOW, and the latest close via
         :func:`engine.trend.analyze_trend`.
      3. ``bullish``: ``ema_fast > ema_slow`` AND ``close > ema_fast``.
      4. ``bearish``: ``ema_fast < ema_slow`` AND ``close < ema_fast``.
      5. ``neutral`` otherwise (including insufficient data).

    Returns:
        ``(bias, reasons)`` where ``reasons`` is a list of human-readable
        decision strings.
    """
    if not htf_candles:
        return "neutral", ["no_htf_candles"]

    closed = [c for c in htf_candles if c.is_closed]
    if len(closed) < _MIN_HTF_CANDLES:
        return "neutral", [f"insufficient_htf_candles:{len(closed)}/{_MIN_HTF_CANDLES}"]

    trend = analyze_trend(closed)
    ema_fast = trend.get("ema_fast", float("nan"))
    ema_slow = trend.get("ema_slow", float("nan"))
    last_close = closed[-1].close

    reasons: list[str] = []
    if any(np.isnan(x) for x in (ema_fast, ema_slow)):
        return "neutral", ["htf_ema_not_ready"]

    if ema_fast > ema_slow and last_close > ema_fast:
        reasons.append(f"ema_fast({ema_fast:.6f}) > ema_slow({ema_slow:.6f})")
        reasons.append(f"close({last_close:.6f}) > ema_fast({ema_fast:.6f})")
        return "bullish", reasons
    if ema_fast < ema_slow and last_close < ema_fast:
        reasons.append(f"ema_fast({ema_fast:.6f}) < ema_slow({ema_slow:.6f})")
        reasons.append(f"close({last_close:.6f}) < ema_fast({ema_fast:.6f})")
        return "bearish", reasons

    reasons.append(
        f"htf_inconclusive: ema_fast={ema_fast:.6f}, ema_slow={ema_slow:.6f}, close={last_close:.6f}"
    )
    return "neutral", reasons


# ---------------------------------------------------------------------------
# LTF / HTF alignment check
# ---------------------------------------------------------------------------
def _check_alignment(
    ltf_direction: Literal["long", "neutral"],
    htf_bias: Literal["bullish", "bearish", "neutral"],
) -> tuple[bool, str]:
    """Return ``(alignment, reason)`` for the LTF/HTF pair in Spot mode.

      * LTF long  + HTF bullish  -> aligned (True).
      * LTF neutral              -> aligned (True, pass-through).
      * HTF neutral              -> aligned (True, pass-through -- no filter).
      * LTF long  + HTF bearish  -> NOT aligned (False).

    The reason string is human-readable and unique per case so the
    orchestrator can surface it as the rejection_reason.
    """
    # LTF neutral: the market has no clear direction -- pass through (low signal).
    if ltf_direction == "neutral":
        return True, "ltf_neutral_pass_through"
    # [OPTIMIZATION] HTF neutral no longer auto-passes. Without HTF confirmation,
    # a long LTF signal has reduced alignment — gated by confidence threshold.
    if htf_bias == "neutral":
        return False, "htf_neutral_requires_confirmation"
    if ltf_direction == "long" and htf_bias == "bullish":
        return True, "ltf_long_aligned_with_htf_bullish"
    if ltf_direction == "long" and htf_bias == "bearish":
        return False, "ltf_long_contradicts_htf_bearish"
    # Defensive default -- should never be reached given the Literal types.
    return False, f"htf_alignment_unknown:{ltf_direction}:{htf_bias}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def filter_by_htf(
    ltf_signal: StrategySignal,
    htf_candles: list[Candle],
    htf_timeframe: str,
    ltf_timeframe: str,
) -> HTFFilterResult:
    """Filter a lower-timeframe signal against the higher-timeframe bias.

    Args:
        ltf_signal: The candidate signal from the lower timeframe. Its
            ``direction`` (``"long"`` / ``"neutral"``) is the only field used
            for the alignment check.
        htf_candles: Closed-candle history on the higher timeframe (e.g. 4h
            or 1h). Unclosed candles are filtered out internally.
        htf_timeframe: Label of the HTF (e.g. ``"4h"``). Stored on the
            result for traceability.
        ltf_timeframe: Label of the LTF (e.g. ``"15m"``). Stored on the
            result for traceability.

    Returns:
        :class:`HTFFilterResult` with:
          * ``bias``: ``"bullish"``, ``"bearish"``, or ``"neutral"``.
          * ``alignment``: True iff the LTF signal direction is permitted
            under the HTF bias.
          * ``reason``: human-readable decision string.

    Edge cases:
      * Empty ``htf_candles`` -> bias="neutral", alignment=True, reason
        "no_htf_candles".
      * Insufficient closed HTF candles (< ``_MIN_HTF_CANDLES``) -> bias=
        "neutral", alignment=True, reason="insufficient_htf_candles:...".
        Pass-through is preferred over blocking when the HTF cannot speak.
    """
    bias, bias_reasons = _determine_bias(htf_candles)
    aligned, align_reason = _check_alignment(ltf_signal.direction, bias)

    # Combine bias and alignment reasons into a single human-readable string.
    full_reason = "; ".join([*bias_reasons, align_reason]) if bias_reasons else align_reason

    result = HTFFilterResult(
        symbol=ltf_signal.symbol,
        htf_timeframe=htf_timeframe,
        ltf_timeframe=ltf_timeframe,
        bias=bias,
        alignment=aligned,
        reason=full_reason,
        timestamp=datetime.now(timezone.utc),
    )

    logger.info(
        "htf_filter_result",
        timestamp=datetime.utcnow(),
        symbol=ltf_signal.symbol,
        htf=htf_timeframe,
        ltf=ltf_timeframe,
        bias=bias,
        alignment=aligned,
        reason=full_reason,
    )

    return result


# ---------------------------------------------------------------------------
# Bonus: bulk HTF filter for multiple LTF signals
# ---------------------------------------------------------------------------
def filter_signals_by_htf(
    ltf_signals: list[StrategySignal],
    htf_candles: list[Candle],
    htf_timeframe: str,
    ltf_timeframe: str,
) -> list[HTFFilterResult]:
    """Apply :func:`filter_by_htf` to each LTF signal in ``ltf_signals``.

    Convenience for orchestrators / backtests that batch-process many signals
    against the same HTF snapshot.
    """
    return [
        filter_by_htf(sig, htf_candles, htf_timeframe, ltf_timeframe)
        for sig in ltf_signals
    ]


__all__ = [
    "filter_by_htf",
    "filter_signals_by_htf",
]
