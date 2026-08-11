"""Regression tests for signal-quality and safe-fallback gates."""

from __future__ import annotations

import pytest

from config.thresholds import MIN_ENTRY_MOMENTUM_SCORE, MIN_ENTRY_SIGNAL_SCORE
from engine.momentum import _empty_momentum
from engine.orchestrator import _determine_rejection_reason


def test_quality_gate_thresholds_are_explicit_and_conservative() -> None:
    assert MIN_ENTRY_SIGNAL_SCORE == pytest.approx(0.70)
    assert MIN_ENTRY_MOMENTUM_SCORE == pytest.approx(2.0 / 3.0, abs=1e-4)


def test_structure_failure_is_not_mislabelled_as_risk_rejected() -> None:
    reason = _determine_rejection_reason(
        regime_ok=True,
        structure_ok=False,
        htf_ok=True,
        confidence_ok=True,
        signal_quality_ok=True,
        rsi_ok=True,
        volume_ok=True,
        risk_ok=False,
        risk_reason="skipped: earlier gate failed",
    )
    assert reason.startswith("structure_alignment_failed")


def test_signal_quality_failure_precedes_skipped_risk_reason() -> None:
    reason = _determine_rejection_reason(
        regime_ok=True,
        structure_ok=True,
        htf_ok=True,
        confidence_ok=True,
        signal_quality_ok=False,
        signal_quality_reason=(
            "signal_quality_gate_failed: momentum_score=0.5000<0.6667"
        ),
        rsi_ok=True,
        volume_ok=True,
        risk_ok=False,
        risk_reason="skipped: signal-quality gate failed",
    )
    assert reason.startswith("signal_quality_gate_failed")
    assert "risk_rejected" not in reason


def test_insufficient_momentum_data_is_neutral_not_long() -> None:
    result = _empty_momentum(["insufficient_data"])
    assert result["direction"] == "neutral"
