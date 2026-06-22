"""
Logging Engine — centralized logging for all events.
Every event is logged with timestamp, module, severity, context.
"""
import logging
import traceback
from datetime import datetime
from core.base import BaseEngine
from core.events import LogEvent, LogLevel
from database.repositories import LogRepository, get_session
from database.models import SystemLog


class LoggingEngine(BaseEngine):
    """Centralized logging. Every event that passes through is persisted."""

    def __init__(self):
        super().__init__("logging_engine")
        self._queue: list = []

    async def initialize(self) -> None:
        self.logger.info("Logging Engine initialized.")

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        # Flush remaining logs
        if self._queue:
            await self._flush()

    async def log(self, level: LogLevel, module: str, message: str,
                  context: dict = None, exception: Exception = None):
        """Main logging entry point."""
        entry = {
            "level": level.value,
            "module": module,
            "message": message,
            "context": context or {},
            "stack_trace": traceback.format_exc() if exception else None,
        }
        self._queue.append(entry)

        # Also write to Python logger for console
        log_func = getattr(self.logger, level.value.lower(), self.logger.info)
        log_func(f"[{module}] {message}")

    async def log_event(self, event: LogEvent):
        """Log a structured LogEvent."""
        await self.log(
            event.level, event.module, event.message,
            event.context,
        )

    async def _flush(self):
        """Persist queued logs to database."""
        try:
            async for session in get_session():
                for entry in self._queue:
                    log_entry = SystemLog(
                        level=entry["level"],
                        module=entry["module"],
                        message=entry["message"],
                        context=entry["context"],
                    )
                    session.add(log_entry)
                await session.commit()
            self._queue.clear()
        except Exception as e:
            self.logger.error(f"Failed to flush logs: {e}")

    async def query_recent(self, limit: int = 50) -> list:
        """Retrieve recent logs."""
        async for session in get_session():
            return await LogRepository.get_recent(session, limit)
