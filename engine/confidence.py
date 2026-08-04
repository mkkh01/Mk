"""
File: engine/confidence.py
1. Single Responsibility: Aggregate all component signals and indicator
   scores into a single confidence value in ``[0, 1]`` and gate it against
   the configured threshold.
2. Consumes: ``StrategySignal``, ``HTFFilterResult`` (contracts/decision.py),
   ``RegimeState`` (contracts/market.py); thresholds from
   config/thresholds.py (HTF_ALIGNMENT_WEIGHT, STRUCTURE_WEIGHT,
   MOMENTUM_WEIGHT, LIQUIDITY_WEIGHT, SESSION_WEIGHT, CONFIDENCE_THRESHOLD,
   REGIME_MODIFIER_*).
3. Produces: ``calculate_confidence``, ``confidence_gate``,
   ``aggregate_score`` consumed by engine/orchestrator.py.
4. Downstream: engine/orchestrator.py (the only caller that combines the
   confidence value with risk assessment to produce a final verdict).
5. New Dependencies: No new external deps.
6. Touches Section 6 bugs? No.
7. Tests: Section 10 engine/confidence.py acceptance criteria:
       1. Weight validation -- the sum of all component weights must equal
          1.0 (+/-0.001). Validated at module load via ``_WEIGHT_SUM``.
       2. Threshold gate -- a final confidence below
          ``CONFIDENCE_THRESHOLD`` results in ``final_verdict = False``.
       3. High confidence pass -- a final confidence above
          ``CONFIDENCE_THRESHOLD`` with all checks passed results in
          ``final_verdict = True``.
8. Logging: ``confidence_calculated`` {timestamp, symbol, confidence,
   regime_modifier} per the monitoring/logger.py event catalog.
9. Dependency Order: config -> contracts -> monitoring -> engine/confidence.py
   (no upstream violations).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from config.thresholds import (
    CONFIDENCE_THRESHOLD,
    HTF_ALIGNMENT_WEIGHT,
    LIQUIDITY_WEIGHT,
    MOMENTUM_WEIGHT,
    REGIME_MODIFIER_RANGING,
    REGIME_MODIFIER_TRENDING,
    REGIME_MODIFIER_VOLATILE,
    SESSION_WEIGHT,
    STRUCTURE_WEIGHT,
)
from contracts.decision import HTFFilterResult, StrategySignal
from contracts.market import RegimeState
from monitoring.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Weight-sum validation (Section 10 criterion 1 -- sum MUST equal 1.0 ±0.001)
# ---------------------------------------------------------------------------
_WEIGHT_SUM = (
    HTF_ALIGNMENT_WEIGHT
    + STRUCTURE_WEIGHT
    + MOMENTUM_WEIGHT
    + LIQUIDITY_WEIGHT
    + SESSION_WEIGHT
)
_WEIGHT_TOLERANCE = 0.001

if abs(_WEIGHT_SUM - 1.0) > _WEIGHT_TOLERANCE:
    # Section 10 acceptance criterion 1 is non-negotiable. We raise at import
    # time so any misconfiguration is caught immediately by the test suite
    # rather than producing silently-miscalibrated confidence scores in
    # production.
    raise AssertionError(
        "confidence weight sum must equal 1.0 +/-0.001; "
        f"got {_WEIGHT_SUM:.6f} "
        f"(HTF={HTF_ALIGNMENT_WEIGHT}, STRUCTURE={STRUCTURE_WEIGHT}, "
        f"MOMENTUM={MOMENTUM_WEIGHT}, LIQUIDITY={LIQUIDITY_WEIGHT}, "
        f"SESSION={SESSION_WEIGHT})"
    )


# ---------------------------------------------------------------------------
# Regime modifier lookup
# ---------------------------------------------------------------------------
_REGIME_MODIFIERS: dict[RegimeState, float] = {
    RegimeState.TRENDING: REGIME_MODIFIER_TRENDING,
    RegimeState.RANGING: REGIME_MODIFIER_RANGING,
    RegimeState.VOLATILE: REGIME_MODIFIER_VOLATILE,
}


def _regime_modifier(regime: RegimeState) -> float:
    """Return the confidence multiplier for ``regime``.

    Defaults to ``REGIME_MODIFIER_RANGING`` for any unknown regime value
    (defensive -- the enum is closed but the lookup should never raise).
    """
    return _REGIME_MODIFIERS.get(regime, REGIME_MODIFIER_RANGING)


# ---------------------------------------------------------------------------
# Score aggregation
# ---------------------------------------------------------------------------
def aggregate_score(signals: list[StrategySignal]) -> float:
    """Return the mean ``raw_score`` of ``signals`` in ``[0, 1]``.

    Used by the orchestrator to produce the ``score`` field on
    :class:`DecisionResult` -- a single number summarising the conviction of
    all contributing component signals.

    Args:
        signals: Component signals from the engine modules. May be empty.

    Returns:
        Mean of ``raw_score`` clamped to ``[0, 1]``. Returns ``0.0`` when
        ``signals`` is empty (conservative -- no signals means no conviction).
    """
    if not signals:
        return 0.0
    scores = [max(0.0, min(1.0, float(s.raw_score))) for s in signals]
    return sum(scores) / len(scores)


# ---------------------------------------------------------------------------
# Confidence calculation
# ---------------------------------------------------------------------------
def calculate_confidence(
    signals: list[StrategySignal],
    htf_result: HTFFilterResult,
    regime: RegimeState,
    trend_strength: float,
    momentum_score: float,
    volume_confirmation: float,
    session_score: float,
    symbol: Optional[str] = None,
) -> float:
    """Aggregate all component scores into a final confidence value.

    Algorithm (Section 15 engine/confidence.py):
      1. Normalise each component to ``[0, 1]``.
      2. Weighted average:
         ``confidence = htf_alignment * HTF_ALIGNMENT_WEIGHT
                        + trend_strength * STRUCTURE_WEIGHT
                        + momentum_score * MOMENTUM_WEIGHT
                        + volume_confirmation * LIQUIDITY_WEIGHT
                        + session_score * SESSION_WEIGHT``
      3. Apply regime modifier (TRENDING: 1.0, RANGING: 0.80, VOLATILE: 0.70).
      4. Clamp to ``[0, 1]``.
      5. Emit ``confidence_calculated`` log event with all inputs.

    Args:
        signals: Component signals (used only for the ``symbol`` extraction
            when ``symbol`` is None and for the ``aggregate_score`` log field;
            the actual component scores are passed in as separate arguments so
            the orchestrator can pre-aggregate per-timeframe values if
            desired).
        htf_result: Output of :func:`engine.htf_filter.filter_by_htf`. Its
            ``alignment`` boolean (True=1.0, False=0.0) is the HTF component.
        regime: Detected market regime.
        trend_strength: ``[0, 1]`` trend strength from
            :func:`engine.trend.analyze_trend` (``strength`` field).
        momentum_score: ``[0, 1]`` momentum score from
            :func:`engine.momentum.calculate_momentum` (``momentum_score``
            field).
        volume_confirmation: ``[0, 1]`` volume confirmation score from
            :func:`engine.volume.volume_confirmation_score` or
            :func:`engine.volume.analyze_volume` (use a derived score).
        session_score: ``[0, 1]`` session quality score from
            :func:`engine.session.session_quality_score`.
        symbol: Optional symbol override for logging. Defaults to the symbol
            of the first signal in ``signals`` (or ``""`` if ``signals`` is
            empty).

    Returns:
        Final confidence in ``[0, 1]``.
    """
    # Normalise all components to [0, 1].
    def _norm(x: float) -> float:
        if x is None:
            return 0.0
        try:
            v = float(x)
        except (TypeError, ValueError):
            return 0.0
        if v < 0.0:
            return 0.0
        if v > 1.0:
            return 1.0
        return v

    htf_component = 1.0 if htf_result.alignment else 0.0
    trend_component = _norm(trend_strength)
    momentum_component = _norm(momentum_score)
    volume_component = _norm(volume_confirmation)
    session_component = _norm(session_score)

    raw_confidence = (
        htf_component * HTF_ALIGNMENT_WEIGHT
        + trend_component * STRUCTURE_WEIGHT
        + momentum_component * MOMENTUM_WEIGHT
        + volume_component * LIQUIDITY_WEIGHT
        + session_component * SESSION_WEIGHT
    )

    modifier = _regime_modifier(regime)
    confidence = raw_confidence * modifier

    # Final clamp to [0, 1].
    confidence = max(0.0, min(1.0, float(confidence)))

    log_symbol = symbol
    if log_symbol is None:
        log_symbol = signals[0].symbol if signals else ""

    logger.info(
        "confidence_calculated",
        timestamp=datetime.utcnow(),
        symbol=log_symbol,
        confidence=round(confidence, 6),
        raw_confidence=round(raw_confidence, 6),
        regime_modifier=modifier,
        regime=regime.value if isinstance(regime, RegimeState) else str(regime),
        htf_alignment=htf_result.alignment,
        htf_weight=HTF_ALIGNMENT_WEIGHT,
        trend_strength=trend_component,
        structure_weight=STRUCTURE_WEIGHT,
        momentum_score=momentum_component,
        momentum_weight=MOMENTUM_WEIGHT,
        volume_confirmation=volume_component,
        liquidity_weight=LIQUIDITY_WEIGHT,
        session_score=session_component,
        session_weight=SESSION_WEIGHT,
        aggregate_signal_score=round(aggregate_score(signals), 6),
    )

    return confidence


# ---------------------------------------------------------------------------
# Confidence gate
# ---------------------------------------------------------------------------
def confidence_gate(confidence: float) -> bool:
    """Return True iff ``confidence`` is at or above ``CONFIDENCE_THRESHOLD``.

    This is the gate the orchestrator uses to decide whether to proceed to
    risk assessment. Per Section 10 criterion 2, a confidence below the
    threshold MUST result in ``final_verdict = False``.
    """
    try:
        c = float(confidence)
    except (TypeError, ValueError):
        return False
    return c >= CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# Bonus: explain a confidence value for debugging / bot-facing messages
# ---------------------------------------------------------------------------
def explain_confidence(
    htf_result: HTFFilterResult,
    regime: RegimeState,
    trend_strength: float,
    momentum_score: float,
    volume_confirmation: float,
    session_score: float,
    confidence: float,
) -> list[str]:
    """Return a list of human-readable reasons explaining ``confidence``.

    Used by the orchestrator to populate ``DecisionResult.component_signals``
    or to surface in bot messages. Each item is a single line of explanation
    covering one component's contribution.
    """
    htf_component = 1.0 if htf_result.alignment else 0.0
    modifier = _regime_modifier(regime)
    lines = [
        f"final_confidence={confidence:.4f}",
        f"htf_alignment={htf_component:.2f} * weight={HTF_ALIGNMENT_WEIGHT:.2f} "
        f"= {htf_component * HTF_ALIGNMENT_WEIGHT:.4f}",
        f"trend_strength={trend_strength:.2f} * weight={STRUCTURE_WEIGHT:.2f} "
        f"= {trend_strength * STRUCTURE_WEIGHT:.4f}",
        f"momentum_score={momentum_score:.2f} * weight={MOMENTUM_WEIGHT:.2f} "
        f"= {momentum_score * MOMENTUM_WEIGHT:.4f}",
        f"volume_confirmation={volume_confirmation:.2f} * weight={LIQUIDITY_WEIGHT:.2f} "
        f"= {volume_confirmation * LIQUIDITY_WEIGHT:.4f}",
        f"session_score={session_score:.2f} * weight={SESSION_WEIGHT:.2f} "
        f"= {session_score * SESSION_WEIGHT:.4f}",
        f"regime={regime.value if isinstance(regime, RegimeState) else str(regime)} "
        f"modifier={modifier:.2f}",
        f"threshold={CONFIDENCE_THRESHOLD:.2f} -> "
        f"{'PASS' if confidence_gate(confidence) else 'FAIL'}",
    ]
    return lines


__all__ = [
    "calculate_confidence",
    "confidence_gate",
    "aggregate_score",
    "explain_confidence",
]
