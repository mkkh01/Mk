"""
File: monitoring/workflow_logger.py
Responsibility: Provide structured workflow logging for trade analysis, decisions, and results.
This module enhances the standard logger with specialized functions for rendering
workflow events in Render logs with clear formatting and hierarchy.

Usage:
    from monitoring.workflow_logger import log_workflow_event, WorkflowEventType
    
    log_workflow_event(
        event_type=WorkflowEventType.ANALYSIS_START,
        symbol="BTCUSDT",
        details={...}
    )
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from monitoring.logger import get_logger

logger = get_logger(__name__)


class WorkflowEventType(str, Enum):
    """Enumeration of workflow event types for structured logging."""
    
    # Analysis phase
    ANALYSIS_START = "analysis_start"
    ANALYSIS_STEP = "analysis_step"
    ANALYSIS_COMPONENT = "analysis_component"
    ANALYSIS_GATES = "analysis_gates"
    ANALYSIS_COMPLETE = "analysis_complete"
    
    # Decision phase
    DECISION_APPROVED = "decision_approved"
    DECISION_REJECTED = "decision_rejected"
    
    # Trade phase
    TRADE_OPENED = "trade_opened"
    TRADE_CLOSED = "trade_closed"
    
    # Summary phase
    WORKFLOW_SUMMARY = "workflow_summary"


def log_workflow_event(
    event_type: WorkflowEventType,
    symbol: str,
    details: dict[str, Any],
    execution_time_ms: Optional[float] = None,
) -> None:
    """Log a structured workflow event with consistent formatting.
    
    Args:
        event_type: The type of workflow event.
        symbol: Trading symbol (e.g., "BTCUSDT").
        details: Dictionary containing event-specific details.
        execution_time_ms: Optional execution time in milliseconds.
    """
    timestamp = datetime.now(timezone.utc)
    
    # Build the log entry with workflow context
    log_entry = {
        "timestamp": timestamp,
        "workflow_event": event_type.value,
        "symbol": symbol,
        **details,
    }
    
    if execution_time_ms is not None:
        log_entry["execution_time_ms"] = round(execution_time_ms, 2)
    
    logger.info(event_type.value, **log_entry)


def log_analysis_start(
    symbol: str,
    trigger_timeframe: str,
    source_candle_open_time: datetime,
) -> None:
    """Log the start of a new analysis cycle."""
    log_workflow_event(
        event_type=WorkflowEventType.ANALYSIS_START,
        symbol=symbol,
        details={
            "trigger_timeframe": trigger_timeframe,
            "source_candle_open_time": source_candle_open_time.isoformat(),
            "status": "started",
            "step_description": f"بدء دورة البحث عن صفقات لعملة {symbol} بناءً على شمعة {trigger_timeframe}"
        },
    )

def log_analysis_step(
    symbol: str,
    step_name: str,
    status: str,
    message: str,
    details: Optional[dict[str, Any]] = None,
) -> None:
    """Log a specific step in the analysis workflow."""
    log_workflow_event(
        event_type=WorkflowEventType.ANALYSIS_STEP,
        symbol=symbol,
        details={
            "step": step_name,
            "status": status,
            "message": message,
            "step_details": details or {},
        },
    )


def log_analysis_component(
    symbol: str,
    timeframe: str,
    component_name: str,
    result: dict[str, Any],
) -> None:
    """Log analysis results for a specific component (SMC, Trend, Volume, etc.)."""
    log_workflow_event(
        event_type=WorkflowEventType.ANALYSIS_COMPONENT,
        symbol=symbol,
        details={
            "timeframe": timeframe,
            "component": component_name,
            "result": result,
        },
    )


def log_analysis_gates(
    symbol: str,
    regime_ok: bool,
    regime: str,
    structure_ok: bool,
    htf_ok: bool,
    confidence_ok: bool,
    confidence: float,
    risk_ok: bool,
    risk_reason: Optional[str] = None,
) -> None:
    """Log the status of all decision gates."""
    log_workflow_event(
        event_type=WorkflowEventType.ANALYSIS_GATES,
        symbol=symbol,
        details={
            "regime": {"passed": regime_ok, "value": regime},
            "structure": {"passed": structure_ok},
            "htf_bias": {"passed": htf_ok},
            "confidence": {"passed": confidence_ok, "value": round(confidence, 4)},
            "risk": {"passed": risk_ok, "reason": risk_reason},
        },
    )


def log_decision_approved(
    symbol: str,
    score: float,
    confidence: float,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    position_size: float,
    execution_time_ms: float,
    direction: str = "long",
) -> None:
    """Log an approved trading decision with entry parameters."""
    # Correct RR calculation based on direction
    if entry_price != stop_loss:
        if direction == "long":
            rr = (take_profit - entry_price) / (entry_price - stop_loss)
        else:
            rr = (entry_price - take_profit) / (stop_loss - entry_price)
    else:
        rr = 0

    log_workflow_event(
        event_type=WorkflowEventType.DECISION_APPROVED,
        symbol=symbol,
        details={
            "score": round(score, 6),
            "confidence": round(confidence, 6),
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "position_size": position_size,
            "risk_reward_ratio": round(rr, 2),
        },
        execution_time_ms=execution_time_ms,
    )


def log_decision_rejected(
    symbol: str,
    score: float,
    confidence: float,
    rejection_reason: str,
    execution_time_ms: float,
) -> None:
    """Log a rejected trading decision with the reason."""
    log_workflow_event(
        event_type=WorkflowEventType.DECISION_REJECTED,
        symbol=symbol,
        details={
            "score": round(score, 6),
            "confidence": round(confidence, 6),
            "rejection_reason": rejection_reason,
        },
        execution_time_ms=execution_time_ms,
    )


def log_trade_opened(
    symbol: str,
    trade_id: str,
    direction: str,
    entry_price: float,
    size: float,
    stop_loss: float,
    take_profit: float,
) -> None:
    """Log when a simulated trade is opened."""
    log_workflow_event(
        event_type=WorkflowEventType.TRADE_OPENED,
        symbol=symbol,
        details={
            "trade_id": trade_id,
            "direction": direction,
            "entry_price": entry_price,
            "size": size,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        },
    )


def log_trade_closed(
    symbol: str,
    trade_id: str,
    direction: str,
    entry_price: float,
    close_price: float,
    pnl: float,
    close_reason: str,
) -> None:
    """Log when a simulated trade is closed with results."""
    pnl_percent = ((close_price - entry_price) / entry_price * 100) if entry_price != 0 else 0
    
    log_workflow_event(
        event_type=WorkflowEventType.TRADE_CLOSED,
        symbol=symbol,
        details={
            "trade_id": trade_id,
            "direction": direction,
            "entry_price": entry_price,
            "close_price": close_price,
            "pnl": round(pnl, 2),
            "pnl_percent": round(pnl_percent, 2),
            "close_reason": close_reason,
        },
    )


def log_workflow_summary(
    symbol: str,
    total_decisions: int,
    approved_decisions: int,
    rejected_decisions: int,
    top_rejection_reasons: dict[str, int],
    total_trades: int,
    winning_trades: int,
    losing_trades: int,
) -> None:
    """Log a summary of the workflow for a symbol."""
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
    
    log_workflow_event(
        event_type=WorkflowEventType.WORKFLOW_SUMMARY,
        symbol=symbol,
        details={
            "decisions": {
                "total": total_decisions,
                "approved": approved_decisions,
                "rejected": rejected_decisions,
                "approval_rate": round(approved_decisions / total_decisions * 100, 2) if total_decisions > 0 else 0,
            },
            "top_rejection_reasons": top_rejection_reasons,
            "trades": {
                "total": total_trades,
                "winning": winning_trades,
                "losing": losing_trades,
                "win_rate": round(win_rate, 2),
            },
        },
    )
