from __future__ import annotations

import inspect

from app.main import CTApplication
from ingest.binance_ws import BinanceWSClient
from monitoring.logger import clear_runtime_events, get_logger, get_runtime_event_counts, get_runtime_events


def test_runtime_supervisor_restarts_long_lived_workers() -> None:
    source = inspect.getsource(CTApplication._run_runtime_supervisor)

    assert "runtime_task_stopped" in source
    assert "runtime_task_restarted" in source
    assert "_ingest_task" in source
    assert "_orchestrator_subscriber_task" in source
    assert "_paper_trader_task" in source


def test_stale_websocket_forces_reconnect() -> None:
    source = inspect.getsource(BinanceWSClient._health_check_loop)

    assert "stale_pairs" in source
    assert "ws_stale_reconnect" in source
    assert 'reason="stale_stream"' in source


def test_runtime_logger_keeps_bounded_newest_first_events() -> None:
    clear_runtime_events()
    logger = get_logger("runtime_test")
    try:
        logger.info("runtime_test_event", symbol="SOLUSDT", timeframe="5m", step="received")
        logger.warning("runtime_test_warning", symbol="SOLUSDT", reason="test")
        events = get_runtime_events(limit=10)
        counts = get_runtime_event_counts()
    finally:
        clear_runtime_events()

    assert events[0]["event"] == "runtime_test_warning"
    assert events[1]["event"] == "runtime_test_event"
    assert events[1]["symbol"] == "SOLUSDT"
    assert counts["runtime_test_event"] == 1
    assert counts["runtime_test_warning"] == 1
