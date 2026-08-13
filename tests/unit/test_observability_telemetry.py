from __future__ import annotations

import asyncio
from monitoring.health_manager import HealthManager, HealthStatus
from monitoring.report_formatter import format_cycle_summary


def test_confluence_telemetry_separates_early_and_timing_rejections() -> None:
    async def scenario() -> dict:
        manager = HealthManager()
        await manager.record_confluence_result(passed=False)
        await manager.record_confluence_result(
            passed=True,
            timing_checked=True,
            timing_passed=False,
            timing_reason="pullback_not_confirmed",
        )
        await manager.record_confluence_result(
            passed=True,
            timing_checked=True,
            timing_passed=True,
        )
        return await manager.get_stats()

    stats = asyncio.run(scenario())

    assert stats["confluence_candidates"] == 3
    assert stats["confluence_passed"] == 2
    assert stats["entry_timing_checked"] == 2
    assert stats["entry_timing_passed"] == 1
    assert stats["timing_rejection_reasons"] == {"pullback_not_confirmed": 1}


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
            "confluence_passed": 2,
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
    assert "Entry Timing Checked     : 2" in summary
    assert "Timing Rejection - pullback_not_confirmed" in summary
    assert "WebSocket=CRITICAL: Component stale" in summary
