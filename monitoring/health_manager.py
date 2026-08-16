"""
File: monitoring/health_manager.py
Responsibility: Centralized health management for the CT system.
Tracks the state of individual components and provides a unified health view.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum
from copy import deepcopy
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from monitoring.logger import get_logger

logger = get_logger(__name__)


class HealthStatus(str, Enum):
    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


class ComponentHealth(BaseModel):
    name: str
    status: HealthStatus = HealthStatus.UNKNOWN
    last_update: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    message: str = ""
    details: Dict[str, Any] = {}
    timeout_seconds: float = 60.0

    def is_stale(self) -> bool:
        delta = (datetime.now(timezone.utc) - self.last_update).total_seconds()
        return delta > self.timeout_seconds


class HealthManager:
    """Centralized manager for system health tracking."""

    def __init__(self):
        self._components: Dict[str, ComponentHealth] = {}
        self._start_time = datetime.now(timezone.utc)
        self._lock = asyncio.Lock()
        
        # Stats for heartbeat
        self._stats = {
            "scan_cycles": 0,
            "candles_received": 0,
            "analyses_executed": 0,
            "signals_emitted": 0,
            "trades_simulated": 0,
            "errors_count": 0,
            "warnings_count": 0,
            "opportunities_found": 0,
            "opportunities_rejected": 0,
            # v2-confluence / conservative-entry observability counters.
            "confluence_candidates": 0,
            # This is signal-quality pass, not full pre-timing eligibility.
            "signal_quality_passed": 0,
            "signal_quality_observations": [],
            "pre_timing_eligible": 0,
            "pre_timing_block_reasons": {},
            "entry_timing_checked": 0,
            "entry_timing_passed": 0,
            "timing_rejection_reasons": {},
            "telegram_sent": 0,
            "db_writes": 0,
            "db_write_failures": 0,
            # Cycle-summary aggregation fields
            "bullish_count": 0,
            "bearish_count": 0,
            "sideways_count": 0,
            "total_score_sum": 0.0,
            "total_confidence_sum": 0.0,
            "total_analysis_time_ms": 0.0,
            "unique_symbols_seen": set(),
            "rejection_reasons": {},
            "last_activity": datetime.now(timezone.utc),
            "scalp": {
                "profile": "scalp_balanced",
                "timeframes": ["5m", "15m", "30m", "1h"],
                "candidates": 0,
                "approved": 0,
                "rejected": 0,
                "paper_only": True,
                "rejection_reasons": {},
                "near_misses": 0,
                "score_sum": 0.0,
                "confidence_sum": 0.0,
                "exit_counts": {},
                "last_exit": None,
                "last_decision": None,
                "last_cycle_at": None,
                "errors": 0,
            },
        }

    async def update_component(
        self, 
        name: str, 
        status: HealthStatus, 
        message: str = "", 
        details: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None
    ):
        """Update the health status of a specific component."""
        async with self._lock:
            now = datetime.now(timezone.utc)
            if name not in self._components:
                self._components[name] = ComponentHealth(
                    name=name, 
                    status=status, 
                    message=message, 
                    details=details or {},
                    timeout_seconds=timeout or 60.0
                )
            else:
                comp = self._components[name]
                comp.status = status
                comp.message = message
                comp.details = details or {}
                comp.last_update = now
                if timeout:
                    comp.timeout_seconds = timeout

            # Log if status is not OK
            if status in (HealthStatus.WARNING, HealthStatus.ERROR, HealthStatus.CRITICAL):
                log_func = getattr(logger, status.lower() if status != HealthStatus.CRITICAL else "critical")
                log_func(
                    "health_event",
                    component=name,
                    status=status.value,
                    message_text=message,
                    details=details or {}
                )

    async def increment_stat(self, key: str, amount: int = 1):
        """Increment a specific health statistic."""
        async with self._lock:
            if key in self._stats:
                self._stats[key] += amount
                self._stats["last_activity"] = datetime.now(timezone.utc)

    async def record_rejection_reason(self, reason: str):
        """Increment the count for a specific rejection reason."""
        async with self._lock:
            reasons = self._stats["rejection_reasons"]
            reasons[reason] = reasons.get(reason, 0) + 1
            self._stats["last_activity"] = datetime.now(timezone.utc)

    async def record_scalp_decision(self, decision: dict[str, Any]) -> None:
        """Record one independent Scalp decision for dashboard and logs."""
        async with self._lock:
            scalp = self._stats["scalp"]
            scalp["candidates"] += 1
            scalp["score_sum"] += float(decision.get("score", 0.0) or 0.0)
            scalp["confidence_sum"] += float(decision.get("confidence", 0.0) or 0.0)
            if decision.get("approved"):
                scalp["approved"] += 1
            else:
                scalp["rejected"] += 1
                reason = str(decision.get("reason") or "unknown")
                reasons = scalp["rejection_reasons"]
                reasons[reason] = reasons.get(reason, 0) + 1
                if float(decision.get("score", 0.0) or 0.0) >= 0.50 or float(decision.get("confidence", 0.0) or 0.0) >= 0.45:
                    scalp["near_misses"] += 1
            scalp["last_decision"] = deepcopy(decision)
            scalp["last_cycle_at"] = datetime.now(timezone.utc)
            self._stats["last_activity"] = datetime.now(timezone.utc)

    async def record_scalp_exit(self, exit_decision: dict[str, Any]) -> None:
        """Record a Scalp-only exit decision without touching Swing trade stats."""
        async with self._lock:
            scalp = self._stats["scalp"]
            status = str(exit_decision.get("status") or "hold")
            counts = scalp["exit_counts"]
            counts[status] = counts.get(status, 0) + 1
            scalp["last_exit"] = deepcopy(exit_decision)
            scalp["last_cycle_at"] = datetime.now(timezone.utc)
            self._stats["last_activity"] = datetime.now(timezone.utc)

    async def record_quality_observation(self, observation: dict[str, Any]) -> None:
        """Store a bounded, read-only sample of raw gate inputs.

        This is observability only. The latest 30 samples are retained so the
        dashboard can explain why quality failed without changing any gate.
        """
        async with self._lock:
            observations = self._stats["signal_quality_observations"]
            observations.append(deepcopy(observation))
            del observations[:-30]
            self._stats["last_activity"] = datetime.now(timezone.utc)

    async def record_confluence_result(
        self,
        *,
        signal_quality_passed: bool,
        pre_timing_eligible: bool,
        pre_timing_block_reasons: list[str] | None = None,
        timing_passed: bool = False,
        timing_reason: str | None = None,
        quality_failure_reasons: list[str] | None = None,
    ) -> None:
        """Record v2-confluence and conservative-entry gate observability.

        These counters are diagnostic only. They do not alter any decision or
        threshold and make it possible to distinguish an early confidence /
        regime rejection from a quality failure or a later RSI, extension, or
        pullback rejection.
        """
        async with self._lock:
            self._stats["confluence_candidates"] += 1
            if signal_quality_passed:
                self._stats["signal_quality_passed"] += 1
            else:
                for reason in quality_failure_reasons or []:
                    reasons = self._stats.setdefault("signal_quality_failure_reasons", {})
                    reasons[reason] = reasons.get(reason, 0) + 1
            if pre_timing_eligible:
                self._stats["pre_timing_eligible"] += 1
                self._stats["entry_timing_checked"] += 1
                if timing_passed:
                    self._stats["entry_timing_passed"] += 1
                elif timing_reason:
                    reasons = self._stats["timing_rejection_reasons"]
                    reasons[timing_reason] = reasons.get(timing_reason, 0) + 1
            else:
                for reason in pre_timing_block_reasons or []:
                    reasons = self._stats["pre_timing_block_reasons"]
                    reasons[reason] = reasons.get(reason, 0) + 1
            self._stats["last_activity"] = datetime.now(timezone.utc)

    async def accumulate_analysis(self, score: float, confidence: float, analysis_time_ms: float):
        """Accumulate score, confidence and analysis time for cycle-summary averages."""
        async with self._lock:
            self._stats["total_score_sum"] += score
            self._stats["total_confidence_sum"] += confidence
            self._stats["total_analysis_time_ms"] += analysis_time_ms
            self._stats["last_activity"] = datetime.now(timezone.utc)

    async def record_symbol_direction(self, symbol: str, direction: str):
        """Track unique symbols and directional counts (Spot Bullish vs Sideways)."""
        async with self._lock:
            self._stats["unique_symbols_seen"].add(symbol)
            if direction == "long":
                self._stats["bullish_count"] += 1
            elif direction == "short":
                self._stats["bearish_count"] += 1
            else:
                self._stats["sideways_count"] += 1
            self._stats["last_activity"] = datetime.now(timezone.utc)

    def get_uptime_seconds(self) -> float:
        """Return the system uptime in seconds."""
        return (datetime.now(timezone.utc) - self._start_time).total_seconds()

    async def get_stats(self) -> Dict[str, Any]:
        """Return a snapshot of current stats (alias for heartbeat callers)."""
        async with self._lock:
            return deepcopy(self._stats)

    async def get_overall_health(self) -> Dict[str, Any]:
        """Return a summary of the overall system health."""
        async with self._lock:
            summary = {
                "uptime": self.get_uptime_seconds(),
                "status": HealthStatus.OK,
                "components": {},
                "stats": self._stats.copy()
            }

            worst_status = HealthStatus.OK
            for name, comp in self._components.items():
                status = comp.status
                if comp.is_stale():
                    status = HealthStatus.CRITICAL
                    comp.message = f"Component stale: last update {comp.last_update.isoformat()}"
                
                summary["components"][name] = {
                    "status": status,
                    "message": comp.message,
                    "last_update": comp.last_update.isoformat()
                }

                # Status priority: CRITICAL > ERROR > WARNING > OK
                priority = {
                    HealthStatus.OK: 0,
                    HealthStatus.WARNING: 1,
                    HealthStatus.ERROR: 2,
                    HealthStatus.CRITICAL: 3,
                    HealthStatus.UNKNOWN: 0
                }
                
                if priority[status] > priority[worst_status]:
                    worst_status = status

            summary["status"] = worst_status
            return summary

# Global instance
health_manager = HealthManager()
