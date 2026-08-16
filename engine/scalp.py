"""Independent Scalp Balanced monitor.

This module deliberately starts in paper/observability mode. It shares the
Supabase candle source with the swing engine but owns its timeframes, gates,
summary counters, and diagnostics.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
from typing import Any

from config.profiles import (
    SCALP_MAX_ATR_PERCENT,
    SCALP_MAX_HOLD_MINUTES,
    SCALP_MIN_CONFIDENCE,
    SCALP_MIN_NET_EDGE_PCT,
    SCALP_REVERSAL_MIN_STRENGTH,
    SCALP_ROUND_TRIP_COST_PCT,
    SCALP_MIN_SCORE,
    SCALP_PAPER_ONLY,
    SCALP_STOP_PCT,
    SCALP_TARGET_PCT,
    SCALP_TIMEFRAMES,
)
from config.thresholds import TIMEFRAME_TO_SECONDS
from engine.momentum import calculate_momentum
from engine.trend import analyze_trend
from engine.volume import analyze_volume
from monitoring.logger import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class ScalpDecision:
    symbol: str
    profile: str = "scalp_balanced"
    status: str = "rejected"
    direction: str = "neutral"
    score: float = 0.0
    confidence: float = 0.0
    trigger_timeframe: str = "5m"
    reason: str = ""
    volume_state: str = "missing"
    atr_percent: float = 0.0
    evaluated_at: str = ""
    paper_only: bool = SCALP_PAPER_ONLY
    mode: str = "balanced"
    expected_net_edge_pct: float = 0.0

    @property
    def approved(self) -> bool:
        return self.status == "approved"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["approved"] = self.approved
        return payload


@dataclass(slots=True)
class ScalpExitDecision:
    status: str
    reason: str
    gross_pnl_pct: float
    net_pnl_pct: float
    held_minutes: float


class ScalpMonitor:
    """Evaluate a fixed low-timeframe profile without opening live trades."""

    def __init__(self, supabase: Any) -> None:
        self._supabase = supabase

    async def evaluate(self, symbol: str) -> ScalpDecision:
        now = datetime.now(timezone.utc)
        decision = ScalpDecision(symbol=symbol, evaluated_at=now.isoformat())
        candles: dict[str, list[Any]] = {}
        for timeframe in SCALP_TIMEFRAMES:
            rows = await self._supabase.fetch_closed_candles(
                symbol, timeframe, limit=100
            )
            candles[timeframe] = [c for c in rows if c.is_closed]
            if not candles[timeframe]:
                decision.reason = f"missing_candles:{timeframe}"
                return decision
            last = candles[timeframe][-1]
            age = (now - last.close_time.replace(tzinfo=timezone.utc)).total_seconds()
            limit = TIMEFRAME_TO_SECONDS[timeframe] * 2.0
            if age > limit:
                decision.reason = f"stale_candles:{timeframe}:{age:.0f}s>{limit:.0f}s"
                return decision

        trends = {tf: analyze_trend(candles[tf]) for tf in SCALP_TIMEFRAMES}
        trigger = trends["5m"]
        setup = trends["15m"]
        bias = trends["30m"]
        context = trends["1h"]
        volume_payload = analyze_volume(candles["5m"])
        decision.volume_state = self._classify_volume(candles["5m"], volume_payload)
        momentum = calculate_momentum(candles["5m"])
        decision.atr_percent = self._atr_percent(candles["5m"])

        trend_scores = [
            float(context.get("strength", 0.0) or 0.0),
            float(bias.get("strength", 0.0) or 0.0),
            float(setup.get("strength", 0.0) or 0.0),
            float(trigger.get("strength", 0.0) or 0.0),
        ]
        decision.score = max(0.0, min(1.0, sum(trend_scores) / len(trend_scores)))
        momentum_score = float(momentum.get("momentum_score", 0.5) or 0.5)
        volume_bonus = 0.10 if decision.volume_state == "bullish" else 0.0
        decision.score = max(0.0, min(1.0, decision.score * 0.8 + momentum_score * 0.1 + volume_bonus))
        decision.confidence = decision.score * (1.0 if decision.volume_state == "bullish" else 0.85)
        decision.direction = "long" if trigger.get("direction") == "bullish" else "neutral"
        decision.expected_net_edge_pct = max(0.0, SCALP_TARGET_PCT - SCALP_ROUND_TRIP_COST_PCT)

        lower_reversal = self._is_balanced_reversal(trigger, setup)
        if context.get("direction") == "bearish":
            decision.reason = "context_1h_bearish"
            return decision
        if bias.get("direction") != "bullish" and not lower_reversal:
            decision.reason = "bias_30m_not_bullish"
            return decision
        if setup.get("direction") != "bullish":
            decision.reason = "setup_15m_not_bullish"
            return decision
        if trigger.get("direction") != "bullish":
            decision.reason = "trigger_5m_not_bullish"
            return decision
        if lower_reversal and (context.get("direction") == "neutral" or bias.get("direction") == "neutral"):
            decision.mode = "balanced_reversal"
        if decision.volume_state == "bearish":
            decision.reason = "volume_bearish"
            return decision
        if decision.volume_state == "missing":
            decision.reason = "volume_missing"
            return decision
        if decision.atr_percent > SCALP_MAX_ATR_PERCENT:
            decision.reason = f"atr_too_high:{decision.atr_percent:.2f}%"
            return decision

        if decision.score < SCALP_MIN_SCORE:
            decision.reason = f"score_below_threshold:{decision.score:.3f}<{SCALP_MIN_SCORE:.3f}"
            return decision
        if decision.confidence < SCALP_MIN_CONFIDENCE:
            decision.reason = f"confidence_below_threshold:{decision.confidence:.3f}<{SCALP_MIN_CONFIDENCE:.3f}"
            return decision
        if decision.expected_net_edge_pct < SCALP_MIN_NET_EDGE_PCT:
            decision.reason = "net_edge_below_cost_floor"
            return decision

        decision.status = "approved"
        decision.reason = "scalp_candidate_approved_paper_only"
        logger.info("scalp_decision", **decision.to_dict())
        return decision

    @staticmethod
    def _is_balanced_reversal(trigger: dict[str, Any], setup: dict[str, Any]) -> bool:
        """Allow neutral 30m/1h context only when 5m and 15m reverse strongly."""
        return (
            trigger.get("direction") == "bullish"
            and setup.get("direction") == "bullish"
            and float(trigger.get("strength", 0.0) or 0.0) >= SCALP_REVERSAL_MIN_STRENGTH
            and float(setup.get("strength", 0.0) or 0.0) >= SCALP_REVERSAL_MIN_STRENGTH
        )

    @staticmethod
    def evaluate_exit(
        entry_price: float,
        current_price: float,
        opened_at: datetime,
        now: datetime | None = None,
        direction: str = "long",
    ) -> ScalpExitDecision:
        """Compute Scalp-only exit reasons; never used by Swing paper trades."""
        now = now or datetime.now(timezone.utc)
        opened_at = opened_at.replace(tzinfo=timezone.utc) if opened_at.tzinfo is None else opened_at
        held_minutes = max(0.0, (now - opened_at).total_seconds() / 60.0)
        if entry_price <= 0 or current_price <= 0:
            return ScalpExitDecision("hold", "invalid_price", 0.0, 0.0, held_minutes)
        sign = -1.0 if direction == "short" else 1.0
        gross = sign * (current_price - entry_price) / entry_price
        net = gross - SCALP_ROUND_TRIP_COST_PCT
        if net >= SCALP_TARGET_PCT - SCALP_ROUND_TRIP_COST_PCT:
            return ScalpExitDecision("take_profit", "net_target_reached", gross, net, held_minutes)
        if gross <= -SCALP_STOP_PCT:
            return ScalpExitDecision("stop_loss", "scalp_stop_reached", gross, net, held_minutes)
        if held_minutes >= SCALP_MAX_HOLD_MINUTES:
            return ScalpExitDecision("time_exit", "max_hold_minutes", gross, net, held_minutes)
        return ScalpExitDecision("hold", "within_scalp_limits", gross, net, held_minutes)

    @staticmethod
    def _classify_volume(candles: list[Any], payload: dict[str, Any]) -> str:
        if not candles:
            return "missing"
        try:
            last_volume = float(candles[-1].volume)
            cvd_slope = float(payload.get("cvd_slope", 0.0) or 0.0)
            delta = float(payload.get("delta", 0.0) or 0.0)
        except (TypeError, ValueError):
            return "missing"
        if not all(math.isfinite(v) for v in (last_volume, cvd_slope, delta)) or last_volume <= 0:
            return "missing"
        cvd_ratio = cvd_slope / last_volume
        delta_ratio = delta / last_volume
        if cvd_ratio >= 0.02 and delta_ratio >= 0.02:
            return "bullish"
        if cvd_ratio <= -0.02 and delta_ratio <= -0.02:
            return "bearish"
        return "neutral"

    @staticmethod
    def _atr_percent(candles: list[Any]) -> float:
        if not candles or candles[-1].close <= 0:
            return 0.0
        lookback = candles[-14:]
        average_range = sum(c.high - c.low for c in lookback) / max(1, len(lookback))
        return average_range / candles[-1].close * 100.0
