"""
Health Monitor — continuously monitors all engines and system health.
Detects failures, triggers recovery, sends alerts. Does NOT trade.
"""
import asyncio
import logging
import psutil
from datetime import datetime, timedelta
from core.base import BaseEngine
from core.events import AlertEvent, AlertLevel, HealthEvent, HealthStatus, EventBus
from config.constants import HEARTBEAT_INTERVAL_SEC


class HealthMonitor(BaseEngine):
    """Monitors system health. Triggers recovery when needed."""

    def __init__(self, event_bus: EventBus):
        super().__init__("health_monitor")
        self.event_bus = event_bus
        self._engine_statuses: dict[str, dict] = {}
        self._last_heartbeats: dict[str, datetime] = {}
        self._alerts: list[AlertEvent] = []
        self.system_state: str = "HEALTHY"
        self._check_interval = HEARTBEAT_INTERVAL_SEC
        self._heartbeat_timeout = 15  # seconds before marking engine FAILED
        self._max_alerts = 100

    async def initialize(self) -> None:
        await self.event_bus.subscribe("HealthEvent", self._on_health_event)
        self.logger.info("Health Monitor initialized.")

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._monitor_loop())
        self.logger.info("Health Monitor started.")

    async def stop(self) -> None:
        self._running = False

    async def _on_health_event(self, event: HealthEvent):
        """Receive heartbeat from an engine."""
        self._engine_statuses[event.engine] = {
            "status": event.status.value,
            "latency_ms": event.latency_ms,
            "error_rate": event.error_rate,
            "memory_usage": event.memory_usage,
        }
        self._last_heartbeats[event.engine] = event.timestamp or datetime.utcnow()

    async def _monitor_loop(self):
        """Main monitoring loop."""
        while self._running:
            try:
                await self._check_all_engines()
                await self._check_system_resources()
                await self._evaluate_system_state()
                await asyncio.sleep(self._check_interval)
            except Exception as e:
                self.logger.error(f"Health check error: {e}", exc_info=True)

    async def _check_all_engines(self):
        """Check heartbeat freshness for all registered engines."""
        now = datetime.utcnow()
        for engine_name, last_hb in list(self._last_heartbeats.items()):
            age = (now - last_hb).total_seconds()
            if age > self._heartbeat_timeout:
                old_status = self._engine_statuses.get(engine_name, {}).get("status")
                self._engine_statuses[engine_name] = {
                    "status": "FAILED",
                    "latency_ms": 0,
                    "error_rate": 1.0,
                    "memory_usage": 0,
                }
                if old_status != "FAILED":
                    await self._send_alert(AlertLevel.CRITICAL, engine_name,
                                           f"Engine {engine_name} FAILED — heartbeat lost ({age:.0f}s)")

    async def _check_system_resources(self):
        """Check CPU, memory, disk."""
        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.5)
        if mem.percent > 90:
            await self._send_alert(AlertLevel.WARNING, "system", f"Memory usage critical: {mem.percent}%")
        if cpu > 90:
            await self._send_alert(AlertLevel.WARNING, "system", f"CPU usage critical: {cpu}%")

    async def _evaluate_system_state(self):
        """Determine overall system state."""
        failed = sum(1 for s in self._engine_statuses.values() if s["status"] == "FAILED")
        degraded = sum(1 for s in self._engine_statuses.values() if s["status"] == "DEGRADED")

        if failed >= 2:
            new_state = "SAFE_MODE"
        elif failed >= 1:
            new_state = "RISKY"
        elif degraded >= 2:
            new_state = "DEGRADED"
        elif degraded >= 1:
            new_state = "DEGRADED"
        else:
            new_state = "HEALTHY"

        if new_state != self.system_state:
            self.logger.info(f"System state transition: {self.system_state} → {new_state}")
            self.system_state = new_state
            if new_state in ("SAFE_MODE", "RISKY"):
                await self._send_alert(
                    AlertLevel.CRITICAL if new_state == "SAFE_MODE" else AlertLevel.WARNING,
                    "system",
                    f"System entering {new_state}"
                )

    async def _send_alert(self, level: AlertLevel, module: str, message: str):
        alert = AlertEvent(level=level, module=module, message=message)
        self._alerts.append(alert)
        if len(self._alerts) > self._max_alerts:
            self._alerts = self._alerts[-self._max_alerts:]
        await self.event_bus.publish(alert)
        log_level = getattr(logging, level.value, logging.INFO)
        self.logger.log(log_level, f"[ALERT] [{module}] {message}")

    def get_status(self) -> dict:
        return {
            "system_state": self.system_state,
            "engines": dict(self._engine_statuses),
            "alerts_count": len(self._alerts),
        }

    def get_recent_alerts(self, limit: int = 10) -> list:
        return [{"level": a.level.value, "module": a.module, "message": a.message,
                 "timestamp": a.timestamp.isoformat()}
                for a in self._alerts[-limit:]]

    def is_trading_safe(self) -> bool:
        """Check if it's safe to trade."""
        return self.system_state in ("HEALTHY", "DEGRADED")

    async def heartbeat(self) -> dict:
        return {
            "engine": self.name,
            "status": "HEALTHY" if self._running else "STOPPED",
            "system_state": self.system_state,
            "latency_ms": 0,
            "error_rate": 0,
            "last_update": datetime.utcnow(),
        }
