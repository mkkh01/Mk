"""
File: monitoring/logger.py
1. Single Responsibility: Provide a configured structlog logger and helpers.
   Logging must NEVER influence trading outcome (Section 9).
2. Consumes: structlog.
3. Produces: get_logger(), configure_logging().
4. Downstream: every module that logs.
5. New Dependencies: structlog (already in requirements.txt).
6. Touches Section 6 bugs? No.
7. Tests: No (logging is not tested by Section 10).
8. Logging: No (this IS the logger).
9. Dependency Order: contracts -> monitoring/logger.py -> everything else.

LOG EVENT CATALOG (Section 9):
  - structure_detected       {timestamp, symbol, timeframe, event, result}
  - bos_detected             {timestamp, symbol, timeframe, direction}
  - choch_detected           {timestamp, symbol, timeframe, direction}
  - swing_detected           {timestamp, symbol, timeframe, type, price, index}
  - ob_detected              {timestamp, symbol, timeframe, type, mitigation_level}
  - fvg_detected             {timestamp, symbol, timeframe, type, top, bottom}
  - sweep_detected           {timestamp, symbol, timeframe, direction, swept_level, strength}
  - trend_analyzed           {timestamp, symbol, timeframe, direction, strength, adx}
  - momentum_calculated      {timestamp, symbol, timeframe, rsi, macd, score}
  - volume_analyzed          {timestamp, symbol, timeframe, cvd, cvd_slope, poc}
  - session_classified       {timestamp, symbol, session, quality_score}
  - regime_classified        {timestamp, symbol, regime}
  - htf_filter_result        {timestamp, symbol, htf, ltf, bias, alignment}
  - confidence_calculated    {timestamp, symbol, confidence, regime_modifier}
  - risk_assessed            {timestamp, symbol, allowed, reason, position_size}
  - entry_refined            {timestamp, symbol, entry_type, entry_price}
  - decision_made            {timestamp, symbol, score, confidence, final_verdict}
  - decision_rejected        {timestamp, symbol, score, final_verdict, rejection_reason}
  - simulated_trade_opened   {timestamp, trade_id, decision_id, symbol, direction, entry_price, size, fee, slippage, is_simulated}
  - simulated_trade_closed   {timestamp, trade_id, symbol, close_reason, pnl, is_simulated}
  - ws_connect               {timestamp, url}
  - ws_disconnect            {timestamp, reason}
  - ws_reconnect             {timestamp, attempt, backoff_seconds}
  - ws_stale                 {timestamp, symbol, timeframe, seconds_since_last}
  - ws_checkpoint_advanced   {timestamp, symbol, timeframe, last_closed_open_time}
  - candle_written           {timestamp, symbol, timeframe, open_time, is_closed}
  - engine_started           {timestamp, active_coins}
  - engine_stopped           {timestamp, open_trades_count}
  - bot_command              {timestamp, user_id, command}
  - bot_reply                {timestamp, user_id, reply_kind}
  - error                    {timestamp, module, error_type, error_message}
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from typing import Any
import uuid

import structlog


_CONFIGURED = False


def configure_logging(level: int = logging.INFO) -> None:
    """Configure structlog exactly once.

    Renders structured JSON to stdout -- Render's log stream ingests JSON
    cleanly and structured fields are filterable in the dashboard.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    # Custom processor for the required format: [TIME] [LEVEL] [MODULE] [SYMBOL] [TIMEFRAME] message
    def custom_formatter(logger, name, event_dict):
        timestamp = event_dict.get("timestamp", datetime.now(timezone.utc).isoformat())
        level = event_dict.get("level", "info").upper()
        module = event_dict.get("module", name)
        symbol = event_dict.get("symbol", "-")
        timeframe = event_dict.get("timeframe", "-")
        trace_id = event_dict.get("trace_id", "-")
        cycle_id = event_dict.get("cycle_id", "-")
        
        # Use message_text if provided, otherwise fall back to event (which is the first positional arg)
        # Note: structlog automatically puts the first positional argument into 'event'
        message = event_dict.get("message_text") or event_dict.get("event", "")
        
        # Format with trace/cycle IDs for better correlation
        formatted_msg = f"[{timestamp}] [{level}] [{module}] [{symbol}] [{timeframe}] [T:{trace_id}] [C:{cycle_id}] {message}"
        event_dict["formatted_message"] = formatted_msg
        return event_dict

    def plain_text_renderer(logger, method_name, event_dict):
        """Render health_summary events as plain text on stdout (for Render logs).
        All other events fall through to JSONRenderer below.
        """
        event = event_dict.get("event", "")
        if event == "health_summary":
            message_text = event_dict.get("message_text", "")
            if message_text:
                # Print the formatted block directly — no JSON wrapping
                print(message_text, flush=True)
                return None  # Stop processing this event
        return event_dict  # Pass through to JSONRenderer

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            custom_formatter,
            plain_text_renderer,
            # We keep JSONRenderer for Render's structured logging,
            # but we can also use ConsoleRenderer for local debugging if needed.
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    _CONFIGURED = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger.

    Always calls ``configure_logging`` to be safe in entry-points that bypass
    ``app/main.py`` (tests, scripts).
    """
    configure_logging()
    return structlog.get_logger(name)


def bind_context(**kwargs: Any) -> None:
    """Bind fields to the current logging context (thread-local / asyncio-safe)."""
    configure_logging()
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context() -> None:
    """Clear all context-local bindings."""
    structlog.contextvars.clear_contextvars()
