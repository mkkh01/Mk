"""
Execution Engine — the ONLY component allowed to place, modify, or close orders.
Does NOT analyze the market or decide trades — only executes pre-approved decisions.
In simulation mode: simulates execution without real exchange interaction.

All DB writes go through Repository layer (handles UUID resolution).
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
from database.repositories import (
    TradeRepository, PositionRepository, UserRepository, get_session
)
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
        self._admin_telegram_id: int = 1503808643  # Telegram ID (set by main)

    async def initialize(self) -> None:
        await self.event_bus.subscribe("RiskEvent", self._on_risk_approval)
        self.logger.info(f"[EXEC] Initialized (sim={self.simulation_mode}).")

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._heartbeat_loop())
        self.logger.info("[EXEC] Started.")

    async def stop(self) -> None:
        self._running = False

    async def _on_risk_approval(self, event: RiskEvent):
        """Execute trade when Risk Engine approves."""
        if not self._running or not event.trade_allowed:
            return
        await self.execute_simulated(event)

    async def execute_simulated(self, risk: RiskEvent, symbol: str = "",
                                 entry_price: float = 0.0, strategy: str = "unknown",
                                 telegram_id: int = 0, entry_reason: str = "") -> ExecutionResult:
        """
        Simulate trade execution (no real exchange).
        Records trade via Repository layer (handles user UUID resolution).

        Args:
            telegram_id: The user's Telegram ID (int). Repository resolves to UUID.
        """
        order_id = str(uuid.uuid4())[:8]
        slippage = entry_price * 0.001 if entry_price > 0 else 0.0
        executed_price = entry_price + slippage
        fees = executed_price * risk.position_size * TRADE_FEE

        tid = telegram_id or self._admin_telegram_id

        result = ExecutionResult(
            order_id=order_id,
            symbol=symbol,
            status="FILLED",
            entry_price=round(executed_price, 8),
            executed_quantity=risk.position_size,
            slippage=round(slippage, 8),
            fees=round(fees, 4),
        )

        # Persist via Repository (handles UUID resolution)
        try:
            async for session in get_session():
                user_uuid = await UserRepository.resolve_user_uuid(session, tid)
                self.logger.info(
                    f"[EXEC] Resolved: telegram_id={tid} → uuid={user_uuid[:8]}..."
                )

                # Create trade via repository
                trade = await TradeRepository.add(
                    session, tid,
                    symbol=symbol,
                    side="BUY",
                    entry_price=executed_price,
                    quantity=risk.position_size,
                    strategy_used=strategy,
                    risk_score=80 if risk.risk_level == "LOW" else 50,
                    confidence_score=75 if risk.position_size > 0 else 50,
                    entry_reason=entry_reason,
                    market_conditions={"risk_level": risk.risk_level},
                    fees=fees,
                )

                # Create position via repository
                sl_dist = risk.stop_loss_distance or executed_price * 0.02
                tp_dist = sl_dist * (risk.take_profit_ratio or 2.0)
                position = await PositionRepository.create(
                    session, tid,
                    symbol=symbol,
                    entry_price=executed_price,
                    quantity=risk.position_size,
                    stop_loss=executed_price - sl_dist,
                    take_profit=executed_price + tp_dist,
                    risk_exposure=risk.position_size * executed_price,
                )

                self.logger.info(
                    f"[EXEC] ✅ {symbol}: trade_id={trade.id[:8]}... "
                    f"position_id={position.id[:8]}..."
                )

        except Exception as e:
            self.logger.error(f"[EXEC] Database error in execution: {e}", exc_info=True)

        self._execution_count += 1

        # Publish execution event
        await self.event_bus.publish(ExecutionEvent(
            order_id=order_id, symbol=symbol,
            status="FILLED", entry_price=executed_price,
            executed_quantity=risk.position_size,
            slippage=slippage, fees=fees,
        ))

        self.logger.info(
            f"[EXEC] {symbol} | Qty={risk.position_size:.6f} | "
            f"Price={executed_price:.6f} | Slippage={slippage:.6f}"
        )

        return result

    async def close_position(self, symbol: str, exit_price: float,
                              telegram_id: int, won: bool, exit_reason: str = ""):
        """Close a simulated position. Uses repository layer."""
        try:
            async for session in get_session():
                position = await PositionRepository.get_by_symbol(session, telegram_id, symbol)
                trades = await TradeRepository.get_open_trades(session, symbol)

                if position:
                    await PositionRepository.close_position(session, position)
                    self.logger.info(f"[EXEC] Position closed: {symbol}")

                for trade in trades:
                    status = "WON" if won else "LOST"
                    await TradeRepository.close_trade(session, trade, exit_price, status, exit_reason)
                    self.logger.info(f"[EXEC] Trade closed: {trade.symbol} {status}")

        except Exception as e:
            self.logger.error(f"[EXEC] Error closing position for {symbol}: {e}", exc_info=True)

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
