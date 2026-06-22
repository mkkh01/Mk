"""
Portfolio Engine — simulates portfolio behavior without real trades.
Tracks virtual capital, positions, and performance.
No real exchange connection.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from core.base import BaseEngine
from core.events import (
    ExecutionEvent, MarketTickEvent, PortfolioEvent, EventBus,
    HealthEvent, HealthStatus, AlertEvent, AlertLevel
)
from core.types import PortfolioSnapshot
from database.repositories import (
    TradeRepository, PositionRepository, PortfolioRepository, get_session
)
from database.models import PortfolioSnapshot as DBPortfolioSnapshot

logger = logging.getLogger("portfolio_engine")


class PortfolioEngine(BaseEngine):
    """Tracks virtual portfolio. No real money. No exchange connection."""

    def __init__(self, event_bus: EventBus, initial_balance: float = 1000.0):
        super().__init__("portfolio_engine")
        self.event_bus = event_bus
        self.initial_balance: float = initial_balance
        self.balance: float = initial_balance
        self.equity: float = initial_balance
        self.peak_equity: float = initial_balance
        self.total_pnl: float = 0.0
        self.realized_pnl: float = 0.0
        self.unrealized_pnl: float = 0.0
        self.open_positions_count: int = 0
        self.total_trades: int = 0
        self.wins: int = 0
        self.losses: int = 0
        self.user_id: str = ""

    async def initialize(self) -> None:
        await self.event_bus.subscribe("ExecutionEvent", self._on_execution)
        await self.event_bus.subscribe("MarketTickEvent", self._on_price_update)
        self.logger.info(f"Portfolio Engine initialized. Balance: {self.balance}")

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._snapshot_loop())
        asyncio.create_task(self._heartbeat_loop())
        self.logger.info("Portfolio Engine started.")

    async def stop(self) -> None:
        self._running = False

    async def _on_execution(self, event: ExecutionEvent):
        """Track virtual positions when trades are executed."""
        if event.status == "FILLED":
            self.open_positions_count += 1
            self.total_trades += 1
            # Deduct fees from balance
            self.balance -= event.fees
            self.logger.info(f"Position opened: {event.symbol} @ {event.entry_price}")

    async def _on_price_update(self, event: MarketTickEvent):
        """Update unrealized PnL based on live prices."""
        # Recalculate equity
        try:
            async for session in get_session():
                open_trades = await TradeRepository.get_open_trades_for_user(session, self.user_id)
                unrealized = 0.0
                for trade in open_trades:
                    price = event.price if trade.symbol == event.symbol else trade.entry_price
                    pnl_pct = (price - trade.entry_price) / trade.entry_price
                    unrealized += trade.quantity * pnl_pct

                self.unrealized_pnl = unrealized
                self.equity = self.balance + self.unrealized_pnl
                self.open_positions_count = len(open_trades)

                if self.equity > self.peak_equity:
                    self.peak_equity = self.equity
        except Exception as e:
            pass  # Non-critical, skip if DB not available

    def record_closed_trade(self, won: bool, pnl: float):
        """Update portfolio after a trade closes."""
        if won:
            self.wins += 1
        else:
            self.losses += 1
        self.realized_pnl += pnl
        self.balance += pnl
        self.total_pnl = self.realized_pnl
        self.total_trades += 1

    def get_snapshot(self) -> PortfolioSnapshot:
        """Get current portfolio state."""
        drawdown = 0.0
        if self.peak_equity > 0:
            drawdown = (self.peak_equity - self.equity) / self.peak_equity * 100

        win_rate = (self.wins / max(self.total_trades, 1)) * 100

        return PortfolioSnapshot(
            balance=round(self.balance, 2),
            equity=round(self.equity, 2),
            open_positions=self.open_positions_count,
            total_pnl=round(self.total_pnl, 2),
            win_rate=round(win_rate, 1),
            drawdown=round(drawdown, 2),
            status="ACTIVE" if self._running else "STOPPED",
        )

    def get_detailed_stats(self) -> dict:
        """Get detailed portfolio statistics."""
        snapshot = self.get_snapshot()
        return {
            "initial_balance": self.initial_balance,
            "current_balance": snapshot.balance,
            "equity": snapshot.equity,
            "peak_equity": self.peak_equity,
            "total_pnl": snapshot.total_pnl,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "win_rate": snapshot.win_rate,
            "drawdown_pct": snapshot.drawdown,
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "open_positions": snapshot.open_positions,
        }

    async def _snapshot_loop(self):
        """Periodically save portfolio snapshots to DB."""
        while self._running:
            try:
                snapshot = self.get_snapshot()
                async for session in get_session():
                    db_snapshot = DBPortfolioSnapshot(
                        user_id=self.user_id,
                        total_balance=snapshot.balance,
                        available_balance=snapshot.balance,
                        unrealized_pnl=self.unrealized_pnl,
                        realized_pnl=self.realized_pnl,
                        exposure=self.open_positions_count,
                    )
                    session.add(db_snapshot)
                    await session.commit()

                # Publish portfolio event
                await self.event_bus.publish(PortfolioEvent(
                    balance=snapshot.balance,
                    equity=snapshot.equity,
                    open_positions=snapshot.open_positions,
                    total_pnl=snapshot.total_pnl,
                    win_rate=snapshot.win_rate,
                    drawdown=snapshot.drawdown,
                    status=snapshot.status,
                ))
            except Exception as e:
                self.logger.debug(f"Snapshot error (non-critical): {e}")

            await asyncio.sleep(30)  # Every 30 seconds

    async def _heartbeat_loop(self):
        while self._running:
            await self.event_bus.publish(HealthEvent(
                engine=self.name, status=HealthStatus.HEALTHY,
                latency_ms=0, error_rate=0,
            ))
            await asyncio.sleep(5)
