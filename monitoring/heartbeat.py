"""
File: monitoring/heartbeat.py
Responsibility: Periodic heartbeat generation for system observability.
"""

from __future__ import annotations

import asyncio
import os
import psutil
from datetime import datetime, timezone
from typing import Any, Dict

from monitoring.logger import get_logger
from monitoring.health_manager import health_manager, HealthStatus

logger = get_logger(__name__)


async def run_heartbeat_loop(interval_seconds: float = 60.0):
    """Background task to emit periodic heartbeat logs."""
    logger.info("heartbeat_started", interval=interval_seconds)
    
    while True:
        try:
            health_summary = await health_manager.get_overall_health()
            
            # Add system metrics
            try:
                process = psutil.Process(os.getpid())
                health_summary["system"] = {
                    "cpu_percent": psutil.cpu_percent(),
                    "memory_mb": process.memory_info().rss / (1024 * 1024),
                    "threads": process.num_threads()
                }
            except Exception:
                health_summary["system"] = {}

            # Emit heartbeat log
            logger.info(
                "engine_heartbeat",
                timestamp=datetime.now(timezone.utc),
                status=health_summary["status"],
                uptime=health_summary["uptime"],
                stats=health_summary["stats"],
                system=health_summary["system"],
                message_text=f"[HEARTBEAT] System Status: {health_summary['status']} | Uptime: {health_summary['uptime']:.0f}s"
            )

            # Check for critical silence
            last_activity = health_summary["stats"]["last_activity"]
            silence_duration = (datetime.now(timezone.utc) - last_activity).total_seconds()
            if silence_duration > 300: # 5 minutes of silence
                logger.warning(
                    "abnormal_silence_detected",
                    duration=silence_duration,
                    message_text=f"WARNING: Abnormal silence detected. No activity for {silence_duration:.0f}s"
                )

            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("heartbeat_stopped")
            break
        except Exception as exc:
            logger.error("heartbeat_error", error=str(exc))
            await asyncio.sleep(interval_seconds)
