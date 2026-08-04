"""
File: monitoring/health_manager.py
Responsibility: Centralized health management for the CT system.
Tracks the state of individual components and provides a unified health view.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, List
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
            "telegram_sent": 0,
            "db_writes": 0,
            # Cycle-summary aggregation fields
            "bullish_count": 0,
            "bearish_count": 0,
            "sideways_count": 0,
            "total_score_sum": 0.0,
            "total_confidence_sum": 0.0,
            "total_analysis_time_ms": 0.0,
            "unique_symbols_seen": set(),
            "rejection_reasons": {},
            "last_activity": datetime.now(timezone.utc)
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
            return self._stats.copy()

    async def get_overall_health(self) -> Dict[str, Any]:
        """Return a summary of the overall system health."""
        async with self._lock:
            now = datetime.now(timezone.utc)
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
