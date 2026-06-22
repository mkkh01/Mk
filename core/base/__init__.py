"""
Base engine class — every engine inherits from this.
Enforces lifecycle: initialize → start → run → stop → shutdown.
"""
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional


class BaseEngine(ABC):
    """Abstract base for all engines. Enforces clean lifecycle."""

    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(f"engine.{name}")
        self._running = False
        self._tasks: list[asyncio.Task] = []

    @abstractmethod
    async def initialize(self) -> None:
        """Setup connections, load config, prepare state."""

    @abstractmethod
    async def start(self) -> None:
        """Begin operations."""

    @abstractmethod
    async def stop(self) -> None:
        """Graceful shutdown."""

    async def shutdown(self) -> None:
        """Full cleanup."""
        await self.stop()
        for task in self._tasks:
            if not task.done():
                task.cancel()
        self._running = False
        self.logger.info(f"[{self.name}] Shutdown complete.")

    @property
    def is_running(self) -> bool:
        return self._running

    async def heartbeat(self) -> dict:
        """Return health status dict for Health Monitor."""
        return {
            "engine": self.name,
            "status": "HEALTHY" if self._running else "STOPPED",
            "latency_ms": 0,
            "error_rate": 0,
            "last_update": None,
        }
