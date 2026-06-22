"""
Evidence Engine — final intelligence layer before any trading decision.
Aggregates ALL signals into a single structured decision.
Answers: "Is there enough evidence to justify a trade?"
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from core.base import BaseEngine
from core.events import (
    EvidenceEvent, SignalEvent, AnalysisEvent, RiskEvent,
    WhaleEvent, EventBus, HealthEvent, HealthStatus, AlertEvent
)
from core.types import EvidenceResult, TradeAction, MarketAnalysis
from core.errors import EvidenceError
from config.constants import EVIDENCE_THRESHOLD, HIGH_CONFIDENCE, SESSION_WEIGHTS

logger = logging.getLogger("evidence_engine")


class EvidenceEngine(BaseEngine):
    """Final judge of all signals. Gatekeeper of capital."""

    # Evidence weights
    WEIGHTS = {
        "trend": 0.25,
        "momentum": 0.15,
        "strategy_alignment": 0.20,
        "risk_safety": 0.20,
        "whale_flow": 0.05,
        "news_stability": 0.05,
        "session_strength": 0.05,
        "historical_success": 0.05,
    }

    def __init__(self, event_bus: EventBus):
        super().__init__("evidence_engine")
        self.event_bus = event_bus
        self._latest_evidence: dict[str, EvidenceResult] = {}
        self._latest_signals: dict[str, list] = {}
        self._latest_whale: dict[str, list] = {}
        self._risk_approvals: dict[str, RiskEvent] = {}
        self._historical_scores: dict[str, float] = {}  # strategy → historical success rate
        self.decision_count: int = 0

    async def initialize(self) -> None:
        await self.event_bus.subscribe("SignalEvent", self._on_signal)
        await self.event_bus.subscribe("WhaleEvent", self._on_whale)
        await self.event_bus.subscribe("RiskEvent", self._on_risk)
        self.logger.info("Evidence Engine initialized.")

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._heartbeat_loop())
        self.logger.info("Evidence Engine started.")

    async def stop(self) -> None:
        self._running = False

    async def _on_signal(self, event: SignalEvent):
        """Collect strategy signals."""
        self._latest_signals.setdefault(event.symbol, []).append(event)
        # Keep only last 10
        if len(self._latest_signals[event.symbol]) > 10:
            self._latest_signals[event.symbol] = self._latest_signals[event.symbol][-10:]

    async def _on_whale(self, event: WhaleEvent):
        """Collect whale events."""
        self._latest_whale.setdefault(event.symbol, []).append(event)
        if len(self._latest_whale[event.symbol]) > 10:
            self._latest_whale[event.symbol] = self._latest_whale[event.symbol][-10:]

    async def _on_risk(self, event: RiskEvent):
        """Track risk approvals."""
        self._risk_approvals[event.stop_loss_distance or "global"] = event

    async def evaluate(self, analysis: MarketAnalysis, signals: list,
                       whale_events: list = None) -> EvidenceResult:
        """
        Core evaluation: aggregate all evidence into a decision.
        Returns EvidenceResult with BUY/SELL/HOLD/IGNORE.
        """
        symbol = analysis.symbol
        evidence = {}
        conflicts = []

        # 1. Trend Evidence (0–100)
        trend_score = self._score_trend(analysis)
        evidence["market_trend"] = trend_score

        # 2. Momentum Evidence (0–100)
        momentum_score = self._score_momentum(analysis)
        evidence["momentum"] = momentum_score

        # 3. Strategy Alignment (0–100)
        strategy_score = self._score_strategy_alignment(signals, analysis)
        evidence["strategy_alignment"] = strategy_score

        # 4. Risk Safety (0–100)
        risk_score = self._score_risk_safety()
        evidence["risk_score"] = risk_score

        # 5. Whale Flow (0–100)
        whale_score = self._score_whale_flow(whale_events or [], analysis)
        evidence["whale_flow"] = whale_score

        # 6. News Stability (0–100) — currently neutral
        evidence["news_impact"] = 60.0

        # 7. Session Strength (0–100)
        session_score = self._score_session()
        evidence["session_strength"] = session_score

        # 8. Historical Success (0–100)
        hist_score = self._get_historical_score(signals)
        evidence["historical_success_rate"] = hist_score

        # Detect conflicts
        conflicts = self._detect_conflicts(analysis, signals, evidence)

        # Calculate final score
        final_score = (
            trend_score * self.WEIGHTS["trend"] +
            momentum_score * self.WEIGHTS["momentum"] +
            strategy_score * self.WEIGHTS["strategy_alignment"] +
            risk_score * self.WEIGHTS["risk_safety"] +
            whale_score * self.WEIGHTS["whale_flow"] +
            evidence["news_impact"] * self.WEIGHTS["news_stability"] +
            session_score * self.WEIGHTS["session_strength"] +
            hist_score * self.WEIGHTS["historical_success"]
        ) * 100 / 100  # Normalize

        final_score = round(final_score, 1)

        # Apply conflict penalty
        if len(conflicts) >= 2:
            final_score *= 0.8
        if len(conflicts) >= 4:
            final_score *= 0.6

        # Decision logic
        risk_approved = risk_score >= 60
        decision = self._make_decision(final_score, conflicts, analysis, risk_approved)

        reasoning = self._build_reasoning(analysis, signals, evidence, conflicts, final_score, decision)

        result = EvidenceResult(
            symbol=symbol,
            decision=decision,
            confidence=final_score,
            final_score=final_score,
            evidence=evidence,
            conflicts=conflicts,
            reasoning=reasoning,
            risk_approved=risk_approved,
        )

        self._latest_evidence[symbol] = result
        self.decision_count += 1

        # Publish evidence event
        await self.event_bus.publish(EvidenceEvent(
            symbol=symbol, decision=decision,
            confidence=final_score, final_score=final_score,
            evidence=evidence, conflicts=conflicts,
            reasoning=reasoning, risk_approved=risk_approved,
        ))

        return result

    # ── Scoring Methods ─────────────────────────────────────

    def _score_trend(self, a: MarketAnalysis) -> float:
        if a.regime == "TRENDING" and a.trend_direction in ("UP", "DOWN"):
            return a.trend_strength
        if a.regime == "TRENDING":
            return min(a.trend_strength, 60.0)
        return max(0, a.trend_strength * 0.5)

    def _score_momentum(self, a: MarketAnalysis) -> float:
        if a.momentum > 70:
            return min(100, a.momentum * 0.9)
        return a.momentum

    def _score_strategy_alignment(self, signals: list, a: MarketAnalysis) -> float:
        if not signals:
            return 30.0  # No signal — neutral-low
        buy_confidence = max((s.confidence for s in signals if s.action == "BUY"), default=0)
        if buy_confidence > 0:
            return buy_confidence
        return 20.0

    def _score_risk_safety(self) -> float:
        # Currently neutral — Risk Engine provides hard constraints
        return 70.0

    def _score_whale_flow(self, whale_events: list, a: MarketAnalysis) -> float:
        if not whale_events:
            return 50.0

        buy_score = sum(1 for w in whale_events
                       if w.direction == "IN" and w.is_market_trade)
        total = len(whale_events)
        if total == 0:
            return 50.0

        ratio = buy_score / total
        if a.trend_direction == "UP":
            return 50 + ratio * 50
        elif a.trend_direction == "DOWN":
            return 100 - ratio * 50
        return 50 + (ratio - 0.5) * 30

    def _score_session(self) -> float:
        """Score based on current trading session."""
        hour = datetime.utcnow().hour
        if 7 <= hour < 9:   # London open
            return 75.0
        if 9 <= hour < 16:  # London + NY overlap
            return 85.0
        if 16 <= hour < 20:  # NY
            return 65.0
        if 0 <= hour < 7:   # Asia
            return 45.0
        return 35.0  # Weekend / low activity

    def _get_historical_score(self, signals: list) -> float:
        """Average historical success rate for active strategies."""
        if not signals:
            return 50.0
        strategies = set(s.strategy_name for s in signals)
        scores = [self._historical_scores.get(s, 50.0) for s in strategies]
        return sum(scores) / len(scores)

    def update_historical_score(self, strategy_name: str, score: float):
        """Update learned success rate for a strategy."""
        old = self._historical_scores.get(strategy_name, 50.0)
        self._historical_scores[strategy_name] = old * 0.8 + score * 0.2

    # ── Conflict Detection ──────────────────────────────────

    def _detect_conflicts(self, a: MarketAnalysis, signals: list,
                          evidence: dict) -> list:
        conflicts = []

        # Trend UP but momentum DOWN
        if a.trend_direction == "UP" and a.momentum < 30:
            conflicts.append("Trend UP but momentum LOW — potential divergence")

        # Strategy says BUY but regime is RANGING
        buy_signals = [s for s in signals if s.action == "BUY"]
        if buy_signals and a.regime == "RANGING":
            conflicts.append("BUY signal in RANGING market — low reliability")

        # High volatility with BUY signal
        if a.volatility > 75 and buy_signals:
            conflicts.append("High volatility with BUY signal — elevated risk")

        # Trend DOWN but strategy says BUY
        if a.trend_direction == "DOWN" and buy_signals:
            conflicts.append("BUY signal against DOWN trend — counter-trend risk")

        # Low liquidity
        if a.liquidity_score < 30:
            conflicts.append(f"Low liquidity ({a.liquidity_score}) — execution risk")

        # Break of structure
        if a.structure.get("break_of_structure"):
            conflicts.append("Market structure broken — trend invalidation risk")

        return conflicts

    def _make_decision(self, score: float, conflicts: list,
                       analysis: MarketAnalysis, risk_approved: bool) -> str:
        """Final decision logic."""
        if not risk_approved:
            return "HOLD"
        if len(conflicts) >= 4:
            return "IGNORE"
        if score >= EVIDENCE_THRESHOLD:
            if analysis.trend_direction == "DOWN" and analysis.momentum > 60:
                return "SELL"
            return "BUY"
        if score >= 65 and analysis.trend_direction == "DOWN":
            return "SELL"
        if score >= 50:
            return "HOLD"
        return "IGNORE"

    def _build_reasoning(self, a: MarketAnalysis, signals: list,
                         evidence: dict, conflicts: list,
                         score: float, decision: str) -> str:
        parts = [
            f"Regime: {a.regime} | Trend: {a.trend_direction} ({a.trend_strength:.0f})",
            f"Momentum: {a.momentum:.0f} | Volatility: {a.volatility:.0f}",
            f"Liquidity: {a.liquidity_score:.0f} | Breakout: {a.breakout_state}",
            f"Score: {score:.1f}/100 | Decision: {decision}",
        ]
        if conflicts:
            parts.append(f"Conflicts ({len(conflicts)}): {', '.join(conflicts[:3])}")
        if signals:
            strategies_used = [s.strategy_name for s in signals]
            parts.append(f"Strategies: {', '.join(strategies_used)}")
        return " | ".join(parts)

    def get_latest_evidence(self, symbol: str) -> Optional[EvidenceResult]:
        return self._latest_evidence.get(symbol)

    async def _heartbeat_loop(self):
        while self._running:
            await self.event_bus.publish(HealthEvent(
                engine=self.name, status=HealthStatus.HEALTHY,
                latency_ms=0, error_rate=0,
            ))
            await asyncio.sleep(5)
