"""
File: market/session.py
1. Single Responsibility: Map a UTC timestamp to a trading-session label
   (asian / london / ny / overlap) and score the session's quality for a
   given symbol.
2. Consumes: config.thresholds (ASIAN_*, LONDON_*, NY_*); monitoring.logger.
3. Produces: get_current_session(), is_overlap(), session_quality_score(),
   get_session_bounds() consumed by engine/confidence.py and engine/orchestrator.py.
4. Downstream: engine/confidence.py (SESSION_WEIGHT component),
   engine/orchestrator.py (session gating).
5. New Dependencies: No (pure-Python, stdlib only).
6. Touches Section 6 bugs? No.
7. Tests: Section 10 market/session.py acceptance criteria --
   (1) 03:00 UTC -> asian, (2) 10:00 UTC -> london,
   (3) 15:00 UTC -> ny, (4) 14:00 UTC -> overlap.
8. Logging: session_classified {timestamp, symbol, session, quality_score}.
9. Dependency Order: config -> monitoring -> market/session.py
   (no upstream violations; does not import engine.*).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from config.thresholds import (
    ASIAN_END_UTC,
    ASIAN_START_UTC,
    LONDON_END_UTC,
    LONDON_START_UTC,
    NY_END_UTC,
    NY_START_UTC,
)
from monitoring.logger import get_logger

logger = get_logger(__name__)


SessionName = Literal["asian", "london", "ny", "overlap"]

# Quality scores per (session, symbol-class). The numeric values are
# heuristic -- they feed engine/confidence.py via SESSION_WEIGHT and are
# deliberately defined here (not in thresholds.py) because they are scoring
# coefficients rather than trading thresholds. Major pairs (BTC/ETH) get the
# full benefit of London/NY/overlap liquidity; alts trade thinnest in Asian
# hours so the Asian score is lower.
_MAJOR_SYMBOLS = ("BTC", "ETH")


_QUALITY_TABLE: dict[str, dict[str, float]] = {
    "asian":   {"major": 0.45, "alt": 0.55},
    "london":  {"major": 0.90, "alt": 0.75},
    "ny":      {"major": 0.90, "alt": 0.75},
    "overlap": {"major": 1.00, "alt": 0.85},
}


# ---------------------------------------------------------------------------
# Core session detection
# ---------------------------------------------------------------------------
def get_current_session(timestamp: datetime) -> SessionName:
    """Return the active trading session for ``timestamp`` (interpreted as UTC).

    Sessions (Section 16 algorithm):
      * Asian  : hour in [ASIAN_START_UTC, ASIAN_END_UTC)   = [0, 8)
      * London : hour in [LONDON_START_UTC, LONDON_END_UTC) = [8, 16)
      * NY     : hour in [NY_START_UTC, NY_END_UTC)         = [13, 21)
      * Overlap: hour in [13, 16) -- both London and NY active -- takes priority.

    Any hour outside the explicit ranges (e.g. 21..23 UTC, between NY close
    and Asian open) is reported as ``"asian"`` because the order flow in that
    gap most closely resembles the thin Asian session.

    The function is timezone-agnostic on purpose: callers must pass a UTC
    ``datetime``. If a tz-aware datetime is supplied its UTC hour is used.
    """
    # Normalise to UTC if tz-aware; otherwise assume the caller already did.
    if timestamp.tzinfo is not None:
        timestamp = timestamp.astimezone(tz=timezone.utc)
    hour = timestamp.hour

    # Overlap takes priority -- both London and NY are active.
    if NY_START_UTC <= hour < LONDON_END_UTC:
        return "overlap"
    if ASIAN_START_UTC <= hour < ASIAN_END_UTC:
        return "asian"
    if LONDON_START_UTC <= hour < LONDON_END_UTC:
        return "london"
    if NY_START_UTC <= hour < NY_END_UTC:
        return "ny"
    # Gap hours (21..23) -- treat as Asian-style thin market.
    return "asian"


def is_overlap(timestamp: datetime) -> bool:
    """True iff ``timestamp`` falls inside the London/NY overlap window."""
    if timestamp.tzinfo is not None:
        timestamp = timestamp.astimezone(tz=timezone.utc)
    hour = timestamp.hour
    return NY_START_UTC <= hour < LONDON_END_UTC


def get_session_bounds(session: SessionName) -> tuple[int, int]:
    """Return the half-open UTC hour range ``[start, end)`` for ``session``.

    For ``"overlap"`` this is [NY_START_UTC, LONDON_END_UTC) = [13, 16).
    Raises ``ValueError`` for unknown session names.
    """
    if session == "asian":
        return ASIAN_START_UTC, ASIAN_END_UTC
    if session == "london":
        return LONDON_START_UTC, LONDON_END_UTC
    if session == "ny":
        return NY_START_UTC, NY_END_UTC
    if session == "overlap":
        return NY_START_UTC, LONDON_END_UTC
    raise ValueError(f"unknown session: {session!r}")


# ---------------------------------------------------------------------------
# Quality scoring
# ---------------------------------------------------------------------------
def _symbol_class(symbol: str) -> str:
    """Classify a symbol as 'major' (BTC/ETH) or 'alt'."""
    if not symbol:
        return "alt"
    upper = symbol.upper()
    for major in _MAJOR_SYMBOLS:
        if upper.startswith(major):
            return "major"
    return "alt"


def session_quality_score(session: SessionName, symbol: str) -> float:
    """Return a quality score in [0.0, 1.0] for ``session`` and ``symbol``.

    Rationale:
      * BTC/ETH (major) have deep liquidity through London/NY/overlap, so the
        score is highest there. Asian is thinner for majors.
      * Alt coins often have their best moves during Asian hours (Korea/China
        flow) so the Asian score is *higher* for alts than for majors.
      * Overlap is always the strongest liquidity window for any symbol.

    Returns 0.0 for unknown session names.
    """
    table = _QUALITY_TABLE.get(session)
    if table is None:
        return 0.0
    cls = _symbol_class(symbol)
    return float(table[cls])


# ---------------------------------------------------------------------------
# Convenience: classify + log in one call
# ---------------------------------------------------------------------------
def classify_and_log(
    timestamp: datetime,
    symbol: str,
) -> tuple[SessionName, float]:
    """Classify the session, log a ``session_classified`` event, and return
    the (session, quality_score) pair.

    The logging call is best-effort: if the logger is unavailable the function
    still returns the classification.
    """
    session = get_current_session(timestamp)
    score = session_quality_score(session, symbol)
    logger.info(
        "session_classified",
        timestamp=timestamp.isoformat(),
        symbol=symbol,
        session=session,
        quality_score=round(score, 4),
    )
    return session, score


__all__ = [
    "SessionName",
    "get_current_session",
    "is_overlap",
    "get_session_bounds",
    "session_quality_score",
    "classify_and_log",
]
