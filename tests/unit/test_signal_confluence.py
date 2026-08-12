"""Regression tests for conservative signal-quality calibration."""

from engine.confidence import (
    aggregate_directional_score,
    apply_confidence_safety_cap,
    calculate_confluence_metrics,
)


def test_directional_score_ignores_neutral_components() -> None:
    from tests.unit.test_confidence import make_signal

    signals = [
        make_signal(0.90, "long"),
        make_signal(0.90, "neutral"),
        make_signal(0.10, "neutral"),
    ]

    assert aggregate_directional_score(signals) == 0.90


def test_weak_momentum_caps_confidence_even_with_other_support() -> None:
    metrics = calculate_confluence_metrics(
        htf_ok=True,
        structure_ok=True,
        primary_direction="long",
        momentum_score=0.50,
        volume_score=0.80,
    )

    adjusted = apply_confidence_safety_cap(0.95, metrics)

    assert adjusted <= 0.60
    assert metrics["momentum_ok"] is False


def test_conflicting_directional_inputs_create_penalty() -> None:
    metrics = calculate_confluence_metrics(
        htf_ok=True,
        structure_ok=False,
        primary_direction="neutral",
        momentum_score=0.50,
        volume_score=0.20,
    )

    adjusted = apply_confidence_safety_cap(0.80, metrics)

    assert float(metrics["contradiction_penalty"]) > 0.0
    assert adjusted < 0.80
    assert metrics["direction_ok"] is False
    assert metrics["minimum_support_ok"] is False
