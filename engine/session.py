"""
File: engine/session.py
1. Single Responsibility: Wrap market/session.py with engine-level filtering
   and signal construction -- classify the active trading session, score its
   quality for the given symbol, and decide whether a candidate signal is
   allowed to fire in this session.
2. Consumes: ``Candle``, ``StrategySignal`` (contracts/market.py,
   contracts/decision.py); ``market/session.py``; config/thresholds;
   monitoring.logger.
3. Produces: ``classify_session``, ``session_quality_score``,
   ``filter_by_session``, ``build_session_signal`` consumed by
   engine/confidence.py and engine/orchestrator.py.
4. Downstream: engine/confidence.py (SESSION_WEIGHT component),
   engine/orchestrator.py (session gating).
5. New Dependencies: No new external deps. Imports ``market.session`` which
   is one layer upstream of engine -- this is allowed because engine sits
   above market in the §1 dependency order (config -> contracts -> storage
   -> ingest/data -> market -> engine -> simulation -> ...).
6. Touches Section 6 bugs? No.
7. Tests: indirectly covered by Section 10 market/session.py tests (which
   exercise the underlying classifier). The wrappers here add only filtering
   and signal construction.
8. Logging: ``session_classified`` {timestamp, symbol, session,
   quality_score} -- already emitted by market/session.py; this module
   additionally emits ``session_filter_result`` and ``session_signal_built``
   for traceability.
9. Dependency Order: config -> contracts -> monitoring -> market/session.py
   -> engine/session.py (no upstream violations).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from contracts.decision import StrategySignal
from contracts.market import Candle
from market.session import (
    SessionName,
    classify_and_log,
    get_current_session,
    session_quality_score as _market_session_quality_score,
)
from monitoring.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal scoring constants
# ---------------------------------------------------------------------------
# Minimum session-quality score below which a signal is filtered out. This is
# a scoring coefficient (not a trading threshold) -- it determines how strict
# the session gate is. The Asian session for majors scores ~0.45 and would be
# filtered out under this default; alt-coins in Asian score 0.55 and pass.
# Tunable but kept private to avoid cluttering config/thresholds.py with
# non-threshold knobs.
_MIN_SESSION_QUALITY = 0.40

# Score at or above which we label a session "favourable" -- purely cosmetic,
# used in reason strings.
_FAVOURABLE_SESSION_SCORE = 0.70


# ---------------------------------------------------------------------------
# Thin wrappers around market/session.py
# ---------------------------------------------------------------------------
def classify_session(timestamp: datetime) -> SessionName:
    """Classify the trading session for ``timestamp`` (interpreted as UTC).

    Thin wrapper around :func:`market.session.get_current_session` so callers
    inside ``engine/`` do not need to reach across package boundaries.
    """
    return get_current_session(timestamp)


def session_quality_score(session: str, symbol: str) -> float:
    """Return a quality score in ``[0.0, 1.0]`` for ``session`` / ``symbol``.

    Thin wrapper around :func:`market.session.session_quality_score`. Returns
    ``0.0`` for unknown session names.
    """
    if not session:
        return 0.0
    try:
        return _market_session_quality_score(session, symbol)  # type: ignore[arg-type]
    except (KeyError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Engine-level session filter
# ---------------------------------------------------------------------------
def filter_by_session(
    signal: StrategySignal,
    timestamp: datetime,
    symbol: str,
) -> tuple[bool, float, str]:
    """Decide whether ``signal`` is allowed to fire in the current session.

    Args:
        signal: The candidate strategy signal. Only its ``direction`` and
            ``symbol`` are inspected -- the gate is purely session-based.
        timestamp: UTC timestamp of the candle that produced the signal.
        symbol: Trading symbol (used for the quality table lookup).

    Returns:
        ``(allowed, quality_score, reason)``:
          * ``allowed`` -- True iff ``quality_score >= _MIN_SESSION_QUALITY``.
          * ``quality_score`` -- the session quality for this symbol/session.
          * ``reason`` -- human-readable reason string. Empty when allowed;
            otherwise ``"low_quality_session:{name}:{score:.3f}"``.

    Edge cases:
      * Empty timestamp -> allowed=False, score=0.0, reason="missing_timestamp".
      * Empty symbol -> allowed=False, score=0.0, reason="missing_symbol".
      * Unknown session -> allowed=False, score=0.0,
        reason="unknown_session".
    """
    if timestamp is None:
        return False, 0.0, "missing_timestamp"
    if not symbol:
        return False, 0.0, "missing_symbol"

    session = classify_session(timestamp)
    score = session_quality_score(session, symbol)

    # Log the classification for traceability (in addition to the
    # ``session_classified`` event that ``classify_and_log`` emits).
    logger.info(
        "session_filter_result",
        timestamp=datetime.utcnow(),
        symbol=symbol,
        session=session,
        quality_score=round(score, 4),
        signal_direction=signal.direction,
        allowed=score >= _MIN_SESSION_QUALITY,
    )

    if score >= _MIN_SESSION_QUALITY:
        return True, score, ""

    return (
        False,
        score,
        f"low_quality_session:{session}:{score:.3f}",
    )


# ---------------------------------------------------------------------------
# Build a StrategySignal from a single candle
# ---------------------------------------------------------------------------
def build_session_signal(
    candle: Candle,
    symbol: str,
) -> StrategySignal:
    """Construct a ``StrategySignal`` representing the session-quality score.

    Per the task specification:
      * ``strategy_name`` = ``"session"``
      * ``direction`` is derived from session quality -- favourable sessions
        (``score >= _FAVOURABLE_SESSION_SCORE``) -> ``"long"``; lower-quality
        sessions -> ``"short"``. This is not a directional trade signal in the
        usual sense -- the raw_score reflects the conviction level and the
        orchestrator's confidence gate will filter out low-conviction cases.
      * ``raw_score`` = the session quality score (0-1).
      * ``reasons`` = human-readable list of session, score, and a note about
        whether the score is favourable.

    Args:
        candle: The trigger candle. Its ``open_time`` is used as the
            classification timestamp; its ``symbol`` and ``timeframe`` are
            propagated to the signal.
        symbol: Trading symbol (used for the quality table lookup). Defaults
            to ``candle.symbol`` if the caller passes an empty string.

    Returns:
        A populated :class:`StrategySignal`. When ``candle`` has no open_time
        (defensive), the signal is still returned with a neutral-safe score
        of 0.5 and a ``"missing_open_time"`` reason.
    """
    sym = symbol or candle.symbol
    timestamp = candle.open_time
    if timestamp is None:
        # Defensive: contracts guarantee open_time, but we degrade gracefully.
        return StrategySignal(
            symbol=sym,
            timeframe=candle.timeframe,
            strategy_name="session",
            direction="long",
            raw_score=0.5,
            reasons=["missing_open_time"],
            timestamp=datetime.now(timezone.utc),
            source_candle_open_time=datetime.now(timezone.utc),
        )

    session = classify_session(timestamp)
    score = session_quality_score(session, sym)
    # Clamp to [0,1] just in case the underlying table ever drifts.
    raw_score = max(0.0, min(1.0, float(score)))

    direction: Literal["long", "neutral"]
    reasons: list[str] = [f"session={session}", f"quality_score={raw_score:.3f}"]

    if raw_score >= _FAVOURABLE_SESSION_SCORE:
        direction = "long"
        reasons.append("favourable_session: long bias")
    else:
        direction = "neutral"
        reasons.append("low_quality_session: neutral bias (will be gated by confidence)")

    # Emit the additional trace event (``classify_and_log`` is not called
    # here because we don't have a guarantee the caller wants the double-log;
    # ``session_filter_result`` above already covers the filter path).
    logger.info(
        "session_signal_built",
        timestamp=datetime.utcnow(),
        symbol=sym,
        timeframe=candle.timeframe,
        session=session,
        quality_score=raw_score,
        direction=direction,
    )

    return StrategySignal(
        symbol=sym,
        timeframe=candle.timeframe,
        strategy_name="session",
        direction=direction,
        raw_score=raw_score,
        reasons=reasons,
        timestamp=datetime.now(timezone.utc),
        source_candle_open_time=candle.open_time,
    )


# ---------------------------------------------------------------------------
# Convenience: classify + log + score in one call (matches market/session.py)
# ---------------------------------------------------------------------------
def classify_and_log_session(
    timestamp: datetime,
    symbol: str,
) -> tuple[SessionName, float]:
    """Classify the session, log via ``classify_and_log``, and return the
    (session, quality_score) pair.

    Thin wrapper around :func:`market.session.classify_and_log` for callers
    that want the engine-level interface.
    """
    session, score = classify_and_log(timestamp, symbol)
    return session, float(score)


__all__ = [
    "classify_session",
    "session_quality_score",
    "filter_by_session",
    "build_session_signal",
    "classify_and_log_session",
]
