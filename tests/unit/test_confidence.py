"""
File: tests/unit/test_confidence.py
1. Single Responsibility: Verify engine/confidence.py against Section 10 acceptance criteria.
2. Consumes: engine.confidence, contracts.decision, contracts.market, config.thresholds.
3. Produces: Tests for weight validation, threshold gate, regime modifier.
4. Downstream: pytest run.
5. New Dependencies: pytest.
6. Touches Section 6 bugs? No.
7. Tests: Section 10 engine/confidence.py tests 1-3.
8. Logging: No.
9. Dependency Order: contracts -> engine -> tests.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from config import thresholds
from contracts.decision import HTFFilterResult, StrategySignal
from contracts.market import RegimeState
from engine.confidence import (
    aggregate_score,
    calculate_confidence,
    confidence_gate,
)


def make_signal(raw_score: float = 0.8, direction: str = "long") -> StrategySignal:
    now = datetime.now(timezone.utc)
    return StrategySignal(
        symbol="BTCUSDT", timeframe="15m", strategy_name="test",
        direction=direction,  # type: ignore[arg-type]
        raw_score=raw_score, reasons=["test"],
        timestamp=now, source_candle_open_time=now,
    )


def make_htf_result(alignment: bool = True) -> HTFFilterResult:
    return HTFFilterResult(
        symbol="BTCUSDT", htf_timeframe="4h", ltf_timeframe="15m",
        bias="bullish", alignment=alignment, reason="test",
        timestamp=datetime.now(timezone.utc),
    )


class TestWeightValidation:
    """Section 10 confidence test 1: sum of weights must equal 1.0 (+/-0.001)."""

    def test_weight_sum_equals_one(self):
        weight_sum = (
            thresholds.HTF_ALIGNMENT_WEIGHT
            + thresholds.STRUCTURE_WEIGHT
            + thresholds.MOMENTUM_WEIGHT
            + thresholds.LIQUIDITY_WEIGHT
            + thresholds.SESSION_WEIGHT
        )
        assert abs(weight_sum - 1.0) < 0.001, (
            f"Confidence weights sum to {weight_sum}, must be 1.0 +/-0.001"
        )


class TestConfidenceGate:
    """Section 10 confidence test 2 & 3."""

    def test_low_confidence_fails_gate(self):
        low = thresholds.CONFIDENCE_THRESHOLD - 0.05
        assert confidence_gate(low) is False

    def test_high_confidence_passes_gate(self):
        high = thresholds.CONFIDENCE_THRESHOLD + 0.05
        assert confidence_gate(high) is True

    def test_threshold_boundary_passes(self):
        assert confidence_gate(thresholds.CONFIDENCE_THRESHOLD) is True


class TestCalculateConfidence:
    """Section 10 confidence test 3 + regime modifier behavior."""

    def test_high_confidence_with_aligned_signals(self):
        signals = [make_signal(0.9, "long"), make_signal(0.85, "long")]
        htf = make_htf_result(alignment=True)
        confidence = calculate_confidence(
            signals=signals, htf_result=htf,
            regime=RegimeState.TRENDING,
            trend_strength=0.9,
            momentum_score=0.85,
            volume_confirmation=0.8,
            session_score=0.7,
        )
        assert 0.0 <= confidence <= 1.0
        assert confidence_gate(confidence) is True

    def test_low_confidence_with_misaligned_signals(self):
        signals = [make_signal(0.2, "long"), make_signal(0.3, "long")]
        htf = make_htf_result(alignment=False)  # Misaligned HTF.
        confidence = calculate_confidence(
            signals=signals, htf_result=htf,
            regime=RegimeState.VOLATILE,
            trend_strength=0.2,
            momentum_score=0.3,
            volume_confirmation=0.2,
            session_score=0.3,
        )
        assert 0.0 <= confidence <= 1.0
        # With all components low + volatile regime modifier, gate should fail.
        assert confidence_gate(confidence) is False

    def test_regime_modifier_ranging_reduces_confidence(self):
        signals = [make_signal(0.8, "long")]
        htf = make_htf_result(alignment=True)
        trending_conf = calculate_confidence(
            signals=signals, htf_result=htf, regime=RegimeState.TRENDING,
            trend_strength=0.8, momentum_score=0.8,
            volume_confirmation=0.8, session_score=0.8,
        )
        ranging_conf = calculate_confidence(
            signals=signals, htf_result=htf, regime=RegimeState.RANGING,
            trend_strength=0.8, momentum_score=0.8,
            volume_confirmation=0.8, session_score=0.8,
        )
        # RANGING modifier is 0.90 -- ranging_conf should be <= trending_conf * 0.91 + 0.001
        # (allowing some slack for floating-point).
        assert ranging_conf <= trending_conf * 0.91 + 0.001

    def test_regime_modifier_volatile_reduces_confidence_more(self):
        signals = [make_signal(0.8, "long")]
        htf = make_htf_result(alignment=True)
        trending_conf = calculate_confidence(
            signals=signals, htf_result=htf, regime=RegimeState.TRENDING,
            trend_strength=0.8, momentum_score=0.8,
            volume_confirmation=0.8, session_score=0.8,
        )
        volatile_conf = calculate_confidence(
            signals=signals, htf_result=htf, regime=RegimeState.VOLATILE,
            trend_strength=0.8, momentum_score=0.8,
            volume_confirmation=0.8, session_score=0.8,
        )
        # VOLATILE modifier is 0.85 -- volatile_conf should be <= trending_conf * 0.86 + 0.001
        assert volatile_conf <= trending_conf * 0.86 + 0.001
        assert volatile_conf < trending_conf


class TestAggregateScore:
    def test_aggregate_score_returns_mean(self):
        signals = [make_signal(0.6), make_signal(0.8), make_signal(1.0)]
        score = aggregate_score(signals)
        assert score == pytest.approx(0.8, rel=1e-3)

    def test_aggregate_score_empty_returns_zero(self):
        assert aggregate_score([]) == 0.0
