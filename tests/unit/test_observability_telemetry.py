from __future__ import annotations

import asyncio

from monitoring.health_manager import HealthManager, HealthStatus
from monitoring.report_formatter import format_cycle_summary


def test_confluence_telemetry_separates_quality_and_pre_timing() -> None:
    async def scenario() -> dict:
        manager = HealthManager()
        await manager.record_confluence_result(
            signal_quality_passed=False,
            pre_timing_eligible=False,
            pre_timing_block_reasons=["confidence", "signal_quality"],
            quality_failure_reasons=["aggregate_score", "volume_score"],
        )
        await manager.record_confluence_result(
            signal_quality_passed=True,
            pre_timing_eligible=True,
            timing_passed=False,
            timing_reason="pullback_not_confirmed",
        )
        await manager.record_confluence_result(
            signal_quality_passed=True,
            pre_timing_eligible=True,
            timing_passed=True,
        )
        return await manager.get_stats()

    stats = asyncio.run(scenario())

    assert stats["confluence_candidates"] == 3
    assert stats["signal_quality_passed"] == 2
    assert stats["pre_timing_eligible"] == 2
    assert stats["pre_timing_block_reasons"] == {
        "confidence": 1,
        "signal_quality": 1,
    }
    assert stats["entry_timing_checked"] == 2
    assert stats["entry_timing_passed"] == 1
    assert stats["timing_rejection_reasons"] == {"pullback_not_confirmed": 1}
    assert stats["signal_quality_failure_reasons"] == {
        "aggregate_score": 1,
        "volume_score": 1,
    }


def test_health_stats_snapshot_is_deep_copy() -> None:
    async def scenario() -> tuple[dict, dict]:
        manager = HealthManager()
        await manager.record_confluence_result(
            signal_quality_passed=False,
            pre_timing_eligible=False,
            pre_timing_block_reasons=["confidence"],
            quality_failure_reasons=["volume_score"],
        )
        first = await manager.get_stats()
        first["pre_timing_block_reasons"]["confidence"] = 99
        first["signal_quality_failure_reasons"]["volume_score"] = 99
        second = await manager.get_stats()
        return first, second

    first, second = asyncio.run(scenario())
    assert first["pre_timing_block_reasons"]["confidence"] == 99
    assert first["signal_quality_failure_reasons"]["volume_score"] == 99
    assert second["pre_timing_block_reasons"]["confidence"] == 1
    assert second["signal_quality_failure_reasons"]["volume_score"] == 1


def test_quality_observations_are_bounded_and_visible_in_summary() -> None:
    async def scenario() -> dict:
        manager = HealthManager()
        for index in range(35):
            await manager.record_quality_observation(
                {
                    "symbol": f"PAIR{index}",
                    "confidence": 0.35,
                    "confidence_threshold": 0.70,
                    "score": 0.68,
                    "momentum_score": 0.60,
                    "volume_score": 0.55,
                    "rsi": 58.0,
                    "cvd_slope": -1.2,
                    "delta": -20.0,
                    "primary_direction": "neutral",
                    "quality_failure_reasons": ["volume_score"],
                }
            )
        return await manager.get_stats()

    stats = asyncio.run(scenario())
    observations = stats["signal_quality_observations"]
    assert len(observations) == 30
    assert observations[0]["symbol"] == "PAIR5"
    assert observations[-1]["symbol"] == "PAIR34"

    summary = format_cycle_summary(
        pairs_analyzed=1,
        bullish_count=1,
        bearish_count=0,
        sideways_count=0,
        signals_found=1,
        approved_count=0,
        rejected_count=1,
        rejection_reasons={"confidence_below_threshold": 1},
        avg_strategy_score=68.0,
        avg_confidence=35.0,
        avg_analysis_time=100.0,
        telegram_count=0,
        database_writes=1,
        warnings_count=0,
        errors_count=0,
        system_health="EXCELLENT",
        diagnostics={"quality_observations": observations},
        health_components={},
    )
    assert "Latest Raw Values      : PAIR34" in summary
    assert "Latest Quality Blocks  : volume_score" in summary


def test_cycle_summary_formatter_labels_long_only_and_diagnostics() -> None:
    summary = format_cycle_summary(
        pairs_analyzed=6,
        bullish_count=12,
        bearish_count=0,
        sideways_count=2,
        signals_found=100,
        approved_count=0,
        rejected_count=14,
        rejection_reasons={"confidence_below_threshold": 14},
        avg_strategy_score=72.0,
        avg_confidence=42.0,
        avg_analysis_time=800.0,
        telegram_count=0,
        database_writes=14,
        warnings_count=0,
        errors_count=0,
        system_health="CRITICAL",
        diagnostics={
            "confluence_candidates": 14,
            "signal_quality_passed": 2,
            "pre_timing_eligible": 2,
            "pre_timing_block_reasons": {"confidence": 12},
            "entry_timing_checked": 2,
            "entry_timing_passed": 1,
            "timing_rejection_reasons": {"pullback_not_confirmed": 1},
        },
        health_components={
            "WebSocket": {
                "status": HealthStatus.CRITICAL,
                "message": "Component stale",
            }
        },
    )

    assert "Long Bias Observations   : 12" in summary
    assert "Non-Long Observations    : 2" in summary
    assert "Confluence Candidates    : 14" in summary
    assert "Signal Quality Passed    : 2" in summary
    assert "Pre-Timing Eligible      : 2" in summary
    assert "Pre-Timing Block - confidence" in summary
    assert "Entry Timing Checked     : 2" in summary
    assert "Timing Rejection - pullback_not_confirmed" in summary
    assert "WebSocket=CRITICAL: Component stale" in summary
