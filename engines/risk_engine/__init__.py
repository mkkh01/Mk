"""
Risk Engine — capital protection layer.
Has ABSOLUTE authority to BLOCK trades, REDUCE position size,
FORCE stop trading. Does NOT predict the market — only protects the portfolio.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

from core.base import BaseEngine
from core.events import (
    EvidenceEvent, RiskEvent, EventBus, HealthEvent, HealthStatus,
    AlertEvent, AlertLevel, PortfolioEvent
)
from core.types import EvidenceResult, RiskDecision, RiskLevel as RiskLevelEnum
from core.errors import RiskError
from config.constants import (
    MAX_RISK_PER_TRADE, MAX_POSITION_PER_SYMBOL_PCT,
    MAX_TOTAL_EXPOSURE_PCT, MAX_DAILY_LOSS_PCT,
    MAX_WEEKLY_LOSS_PCT, MAX_CONSECUTIVE_LOSSES,
    MAX_DRAWDOWN_PCT, VOLATILITY_RISK_MAP,
)

logger = logging.getLogger("risk_engine")


class RiskEngine(BaseEngine):
    """Capital protection. Blocks unsafe trades absolutely."""

    def __init__(self, event_bus: EventBus):
        super().__init__("risk_engine")
        self.event_bus = event_bus
        self._daily_loss: float = 0.0
        self._weekly_loss: float = 0.0
        self._total_exposure: float = 0.0
        self._consecutive_losses: int = 0
        self._open_positions: dict[str, dict] = {}
        self._last_reset_day: int = datetime.utcnow().day
        self._portfolio_balance: float = 1000.0
        self._peak_balance: float = 1000.0
        self._trading_blocked: bool = False
        self._block_reason: str = ""
        self._interest_areas: dict[str, int] = {}
        self._interest_percentages: dict[str, int] = {}
        self._daily_drawdown: float = 0.0

    async def initialize(self) -> None:
        await self.event_bus.subscribe("EvidenceEvent", self._on_evidence)
        await self.event_bus.subscribe("PortfolioEvent", self._on_portfolio_update)
        self.logger.info("Risk Engine initialized.")

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._heartbeat_loop())
        asyncio.create_task(self._daily_reset_loop())
        self.logger.info("Risk Engine started.")

    async def stop(self) -> None:
        self._running = False

    async def _on_portfolio_update(self, event: PortfolioEvent):
        """Track portfolio state for risk calculations."""
        self._portfolio_balance = event.equity
        if event.equity > self._peak_balance:
            self._peak_balance = event.equity

    async def _on_evidence(self, event: EvidenceEvent):
        """Evaluate risk when evidence is presented."""
        if not self._running:
            return

        decision = await self.evaluate(
            EvidenceResult(
                symbol=event.symbol, decision=event.decision,
                confidence=event.confidence, final_score=event.final_score,
                evidence=event.evidence, conflicts=event.conflicts,
                reasoning=event.reasoning, risk_approved=event.risk_approved,
            ),
            entry_price=0.0,  # Will be set by execution engine
            atr=0.0,
        )

        await self.event_bus.publish(RiskEvent(
            trade_allowed=decision.trade_allowed,
            risk_level=decision.risk_level,
            position_size=decision.position_size,
            max_loss=decision.max_loss,
            stop_loss_distance=decision.stop_loss_distance,
            take_profit_ratio=decision.take_profit_ratio,
            reasoning=decision.reasoning,
            blocking_reason=decision.blocking_reason,
        ))

    async def evaluate(self, evidence: EvidenceResult, entry_price: float,
                       atr: float = 0.0, capital: float = 100.0,
                       risk_percentage: float = 1.0) -> RiskDecision:
        """
        Core risk evaluation.
        Returns RiskDecision — APPROVE, REDUCE, or BLOCK.
        """

        # Check blocking conditions FIRST
        blocked, reason = self._check_blocking_conditions(evidence.symbol)
        if blocked and evidence.decision != "SELL":
            return RiskDecision(
                trade_allowed=False, risk_level="EXTREME",
                position_size=0.0, max_loss=0.0,
                reasoning=f"BLOCKED: {reason}", blocking_reason=reason,
            )

        # Check daily loss limit
        daily_loss_pct = (self._daily_loss / max(self._portfolio_balance, 1)) * 100
        if daily_loss_pct >= MAX_DAILY_LOSS_PCT * 100:
            return RiskDecision(
                trade_allowed=False, risk_level="EXTREME",
                position_size=0.0, max_loss=0.0,
                reasoning="BLOCKED: Daily loss limit reached",
                blocking_reason="Daily loss limit",
            )

        # Calculate position size
        volatility = evidence.evidence.get("momentum", 50)  # Use momentum as proxy
        vol_mult = self._get_volatility_multiplier(volatility)

        risk_amount = capital * (risk_percentage / 100) * vol_mult
        sl_distance = atr * 2 if atr > 0 else entry_price * 0.015
        position_size = risk_amount / (sl_distance / entry_price) if sl_distance > 0 and entry_price > 0 else 0

        if position_size <= 0:
            return RiskDecision(
                trade_allowed=False, risk_level="HIGH",
                position_size=0.0, max_loss=risk_amount,
                reasoning="Invalid position size",
                blocking_reason="Position size zero",
            )

        # Cap position exposure per symbol
        max_symbol_exposure = self._portfolio_balance * MAX_POSITION_PER_SYMBOL_PCT
        if position_size * entry_price > max_symbol_exposure:
            position_size = max_symbol_exposure / entry_price

        # Cap total exposure
        max_total = self._portfolio_balance * MAX_TOTAL_EXPOSURE_PCT
        current_exposure = sum(p.get("exposure", 0) for p in self._open_positions.values())
        if current_exposure + position_size * entry_price > max_total:
            if current_exposure >= max_total:
                return RiskDecision(
                    trade_allowed=False, risk_level="HIGH",
                    position_size=0.0, max_loss=risk_amount,
                    reasoning="BLOCKED: Maximum total exposure reached",
                    blocking_reason="Max exposure",
                )
            position_size = (max_total - current_exposure) / entry_price

        # Determine risk level
        risk_level = self._determine_risk_level(volatility, position_size, capital)

        tp_ratio = 2.0 if risk_level in ("LOW", "MEDIUM") else 1.5

        return RiskDecision(
            trade_allowed=True,
            risk_level=risk_level,
            position_size=round(position_size, 6),
            max_loss=round(risk_amount, 2),
            stop_loss_distance=round(sl_distance, 6),
            take_profit_ratio=tp_ratio,
            reasoning=f"Position: {position_size:.6f} | Risk: {risk_amount:.2f} | Level: {risk_level}",
        )

    def _check_blocking_conditions(self, symbol: str) -> tuple[bool, str]:
        """Check all blocking conditions."""
        if self._trading_blocked:
            return True, self._block_reason

        if self._consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            self._trading_blocked = True
            self._block_reason = f"Consecutive losses ({self._consecutive_losses})"
            return True, self._block_reason

        daily_pct = (self._daily_loss / max(self._portfolio_balance, 1)) * 100
        if daily_pct >= MAX_DAILY_LOSS_PCT * 100:
            self._trading_blocked = True
            self._block_reason = f"Daily loss limit ({daily_pct:.1f}%)"
            return True, self._block_reason

        # Check drawdown from peak
        if self._peak_balance > 0:
            dd_pct = (self._peak_balance - self._portfolio_balance) / self._peak_balance * 100
            if dd_pct >= MAX_DRAWDOWN_PCT * 100:
                self._trading_blocked = True
                self._block_reason = f"Max drawdown reached ({dd_pct:.1f}%)"
                return True, self._block_reason

        return False, ""

    def _get_volatility_multiplier(self, volatility: float) -> float:
        if volatility < 30:
            return VOLATILITY_RISK_MAP.get("LOW", 1.0)
        if volatility < 60:
            return VOLATILITY_RISK_MAP.get("MEDIUM", 0.8)
        if volatility < 80:
            return VOLATILITY_RISK_MAP.get("HIGH", 0.5)
        return VOLATILITY_RISK_MAP.get("EXTREME", 0.0)

    def _determine_risk_level(self, volatility: float, position_size: float,
                              capital: float) -> str:
        exposure_pct = (position_size * 0) / max(capital, 1)  # Simplified
        if volatility > 80 or self._consecutive_losses >= 3:
            return "HIGH"
        if volatility > 60 or self._consecutive_losses >= 1:
            return "MEDIUM"
        return "LOW"

    def record_trade_result(self, won: bool, pnl: float):
        """Update risk state after trade closes."""
        if won:
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1
            self._daily_loss += abs(pnl)
            self._weekly_loss += abs(pnl)

    def emergency_stop(self, reason: str = "Manual"):
        """Immediately block all trading."""
        self._trading_blocked = True
        self._block_reason = reason
        self.logger.critical(f"EMERGENCY STOP: {reason}")

    def resume_trading(self):
        """Resume trading after emergency stop."""
        self._trading_blocked = False
        self._block_reason = ""
        self._consecutive_losses = 0
        self.logger.info("Trading resumed.")

    def get_status(self) -> dict:
        return {
            "trading_blocked": self._trading_blocked,
            "block_reason": self._block_reason,
            "consecutive_losses": self._consecutive_losses,
            "daily_loss": self._daily_loss,
            "weekly_loss": self._weekly_loss,
            "total_exposure": self._total_exposure,
            "open_positions": len(self._open_positions),
            "portfolio_balance": self._portfolio_balance,
        }

    async def _daily_reset_loop(self):
        """Reset daily counters at midnight."""
        while self._running:
            now = datetime.utcnow()
            if now.day != self._last_reset_day:
                self._daily_loss = 0.0
                self._daily_drawdown = 0.0
                self._last_reset_day = now.day
                if now.weekday() == 0:  # Monday
                    self._weekly_loss = 0.0
                self.logger.info("Daily risk counters reset.")
            await asyncio.sleep(60)

    async def _heartbeat_loop(self):
        while self._running:
            await self.event_bus.publish(HealthEvent(
                engine=self.name, status=HealthStatus.HEALTHY,
                latency_ms=0, error_rate=0,
            ))
            await asyncio.sleep(5)
