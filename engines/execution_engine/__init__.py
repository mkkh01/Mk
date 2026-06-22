"""
Execution Engine — the ONLY component allowed to place, modify, or close orders.
Does NOT analyze the market or decide trades — only executes pre-approved decisions.
In simulation mode: simulates execution without real exchange interaction.
"""
import asyncio
import logging
from datetime import datetime
import uuid

from core.base import BaseEngine
from core.events import (
    RiskEvent, ExecutionEvent, EvidenceEvent, EventBus,
    HealthEvent, HealthStatus, AlertEvent, AlertLevel
)
from core.types import RiskDecision, ExecutionResult
from core.errors import ExecutionError
from database.repositories import TradeRepository, PositionRepository, get_session
from database.models import Trade, Position
from config.constants import TRADE_FEE

logger = logging.getLogger("execution_engine")


class ExecutionEngine(BaseEngine):
    """Executes approved trades. Simulation mode by default."""

    def __init__(self, event_bus: EventBus, simulation_mode: bool = True):
        super().__init__("execution_engine")
        self.event_bus = event_bus
        self.simulation_mode = simulation_mode
        self._pending_orders: dict[str, dict] = {}
        self._execution_count: int = 0
        self._admin_id: int = 1503808643  # Will be set by main

    async def initialize(self) -> None:
        await self.event_bus.subscribe("RiskEvent", self._on_risk_approval)
        self.logger.info(f"Execution Engine initialized (sim={self.simulation_mode}).")

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._heartbeat_loop())
        self.logger.info("Execution Engine started.")

    async def stop(self) -> None:
        self._running = False

    async def _on_risk_approval(self, event: RiskEvent):
        """Execute trade when Risk Engine approves."""
        if not self._running or not event.trade_allowed:
            return

        # In simulation mode, we still log the trade — Portfolio Engine handles the rest
        await self.execute_simulated(event)

    async def execute_simulated(self, risk: RiskEvent, symbol: str = "",
                                 entry_price: float = 0.0, strategy: str = "unknown",
                                 user_id: str = "", entry_reason: str = "") -> ExecutionResult:
        """
        Simulate trade execution (no real exchange).
        Records the trade in database for portfolio tracking.
        """
        order_id = str(uuid.uuid4())[:8]
        slippage = entry_price * 0.001 if entry_price > 0 else 0.0  # 0.1% simulated slippage
        executed_price = entry_price + slippage
        fees = executed_price * risk.position_size * TRADE_FEE

        result = ExecutionResult(
            order_id=order_id,
            symbol=symbol,
            status="FILLED",
            entry_price=round(executed_price, 8),
            executed_quantity=risk.position_size,
            slippage=round(slippage, 8),
            fees=round(fees, 4),
        )

        # Persist to database
        try:
            async for session in get_session():
                trade = Trade(
                    user_id=user_id,
                    symbol=symbol,
                    side="BUY",
                    entry_price=executed_price,
                    quantity=risk.position_size,
                    strategy_used=strategy,
                    risk_score=risk.risk_level == "LOW" and 80 or 50,
                    confidence_score=risk.position_size > 0 and 75 or 50,
                    entry_reason=entry_reason,
                    market_conditions={"risk_level": risk.risk_level},
                    fees=fees,
                )
                session.add(trade)
                await session.commit()

                # Create position record
                position = Position(
                    user_id=user_id,
                    symbol=symbol,
                    entry_price=executed_price,
                    quantity=risk.position_size,
                    stop_loss=executed_price - (risk.stop_loss_distance if risk.stop_loss_distance else executed_price * 0.02),
                    take_profit=executed_price + (risk.stop_loss_distance * risk.take_profit_ratio if risk.stop_loss_distance else executed_price * 0.03),
                    risk_exposure=risk.position_size * executed_price,
                )
                session.add(position)
                await session.commit()

        except Exception as e:
            self.logger.error(f"Database error in execution: {e}")

        self._execution_count += 1

        # Publish execution event
        await self.event_bus.publish(ExecutionEvent(
            order_id=order_id, symbol=symbol,
            status="FILLED", entry_price=executed_price,
            executed_quantity=risk.position_size,
            slippage=slippage, fees=fees,
        ))

        self.logger.info(
            f"Simulated EXECUTION: {symbol} | Qty: {risk.position_size:.6f} | "
            f"Price: {executed_price:.6f} | Slippage: {slippage:.6f}"
        )

        return result

    async def close_position(self, symbol: str, exit_price: float,
                              user_id: str, won: bool, exit_reason: str = ""):
        """Close a simulated position."""
        try:
            async for session in get_session():
                # Find open position
                position = await PositionRepository.get_by_symbol(session, user_id, symbol)
                trades = await TradeRepository.get_open_trades(session, symbol)

                # Close position
                if position:
                    await PositionRepository.close_position(session, position)

                # Close trades
                for trade in trades:
                    status = "WON" if won else "LOST"
                    await TradeRepository.close_trade(session, trade, exit_price, status, exit_reason)

        except Exception as e:
            self.logger.error(f"Error closing position for {symbol}: {e}")

    def get_metrics(self) -> dict:
        return {
            "execution_count": self._execution_count,
            "pending_orders": len(self._pending_orders),
            "simulation_mode": self.simulation_mode,
        }

    async def _heartbeat_loop(self):
        while self._running:
            await self.event_bus.publish(HealthEvent(
                engine=self.name, status=HealthStatus.HEALTHY,
                latency_ms=0, error_rate=0,
            ))
            await asyncio.sleep(5)
