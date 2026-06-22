"""
Learning Engine — collects historical trades, evaluates performance,
produces improvement recommendations. NEVER changes strategies automatically.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

from core.base import BaseEngine
from core.events import (
    ExecutionEvent, PortfolioEvent, EventBus, HealthEvent, HealthStatus
)
from database.repositories import (
    TradeRepository, SignalRepository, StrategyStatRepository, get_session
)

logger = logging.getLogger("learning_engine")


class LearningEngine(BaseEngine):
    """Evaluates historical performance. Produces recommendations only."""

    def __init__(self, event_bus: EventBus):
        super().__init__("learning_engine")
        self.event_bus = event_bus
        self._strategy_performance: dict[str, dict] = {}
        self._symbol_performance: dict[str, dict] = {}
        self._recommendations: list[str] = []
        self._last_evaluation: datetime = datetime.utcnow()

    async def initialize(self) -> None:
        await self.event_bus.subscribe("ExecutionEvent", self._on_execution)
        self.logger.info("Learning Engine initialized.")

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._evaluation_loop())
        asyncio.create_task(self._heartbeat_loop())
        self.logger.info("Learning Engine started.")

    async def stop(self) -> None:
        self._running = False

    async def _on_execution(self, event: ExecutionEvent):
        """Learn from execution events."""
        pass  # Learning from closed trades happens in evaluation loop

    async def _evaluation_loop(self):
        """Periodically evaluate all closed trades and update stats."""
        while self._running:
            try:
                await self.evaluate_all()
            except Exception as e:
                self.logger.error(f"Evaluation error: {e}")
            await asyncio.sleep(300)  # Every 5 minutes

    async def evaluate_all(self):
        """Evaluate all closed trades and compute performance metrics."""
        try:
            async for session in get_session():
                # Get all closed trades
                trades = await TradeRepository.get_all_closed(session, self.user_id if hasattr(self, 'user_id') else "")

                if not trades:
                    return

                # Group by strategy
                strats: dict[str, list] = {}
                for t in trades:
                    strat = t.strategy_used or "unknown"
                    strats.setdefault(strat, []).append(t)

                for strat_name, strat_trades in strats.items():
                    await self._evaluate_strategy(session, strat_name, strat_trades)

                # Group by symbol
                symbols: dict[str, list] = {}
                for t in trades:
                    symbols.setdefault(t.symbol, []).append(t)

                for sym, sym_trades in symbols.items():
                    await self._evaluate_symbol(session, sym, sym_trades)

                self._last_evaluation = datetime.utcnow()
                self._generate_recommendations(strats, symbols)

        except Exception as e:
            self.logger.error(f"Evaluation failed: {e}")

    async def _evaluate_strategy(self, session, strategy_name: str, trades: list):
        """Compute metrics for a single strategy."""
        total = len(trades)
        wins = [t for t in trades if t.status == "WON"]
        losses = [t for t in trades if t.status == "LOST"]
        win_rate = (len(wins) / total * 100) if total > 0 else 0

        avg_profit = np.mean([t.pnl for t in wins]) if wins else 0
        avg_loss = abs(np.mean([t.pnl for t in losses])) if losses else 0

        # Calculate drawdown for this strategy
        cumulative = np.cumsum([t.pnl for t in trades])
        peak = np.maximum.accumulate(cumulative)
        drawdown = float(np.max(peak - cumulative)) if len(cumulative) > 0 else 0

        timeframe = "15m"  # Default

        await StrategyStatRepository.upsert(
            session, strategy_name, "ALL",
            round(win_rate, 1), round(float(avg_profit), 2),
            round(float(avg_loss), 2), round(drawdown, 2),
            total, timeframe,
        )

        self._strategy_performance[strategy_name] = {
            "win_rate": round(win_rate, 1),
            "avg_profit": round(float(avg_profit), 2),
            "avg_loss": round(float(avg_loss), 2),
            "total_trades": total,
            "drawdown": round(drawdown, 2),
        }

    async def _evaluate_symbol(self, session, symbol: str, trades: list):
        """Compute metrics per symbol."""
        total = len(trades)
        wins = [t for t in trades if t.status == "WON"]
        win_rate = (len(wins) / total * 100) if total > 0 else 0
        avg_pnl = np.mean([t.pnl for t in trades]) if trades else 0

        self._symbol_performance[symbol] = {
            "win_rate": round(win_rate, 1),
            "avg_pnl": round(float(avg_pnl), 2),
            "total_trades": total,
        }

    def _generate_recommendations(self, strats: dict, symbols: dict):
        """Generate improvement recommendations based on data."""
        recommendations = []

        for name, perf in self._strategy_performance.items():
            if perf["total_trades"] >= 3 and perf["win_rate"] < 45:
                recommendations.append(
                    f"Strategy '{name}' underperforming ({perf['win_rate']}% WR) — consider reducing allocation"
                )
            if perf["total_trades"] >= 5 and perf["win_rate"] > 60:
                recommendations.append(
                    f"Strategy '{name}' performing well ({perf['win_rate']}% WR) — consider increasing allocation"
                )

        for sym, perf in self._symbol_performance.items():
            if perf["total_trades"] >= 3 and perf["win_rate"] < 40:
                recommendations.append(
                    f"Symbol {sym} underperforming ({perf['win_rate']}% WR) — consider removing"
                )

        self._recommendations = recommendations

        if recommendations:
            logger.info(f"Learning recommendations: {recommendations}")

    def get_recommendations(self) -> list:
        return list(self._recommendations)

    def get_strategy_performance(self) -> dict:
        return dict(self._strategy_performance)

    def get_symbol_performance(self) -> dict:
        return dict(self._symbol_performance)

    def get_sharpe_like_score(self) -> float:
        """Simplified Sharpe-like metric."""
        all_returns = []
        for perf in self._strategy_performance.values():
            if perf["total_trades"] > 0 and perf["avg_profit"] > 0:
                ret = perf["avg_profit"] / max(perf["avg_loss"], 1e-10)
                all_returns.append(ret)
        return round(float(np.mean(all_returns)), 2) if all_returns else 0.0

    async def _heartbeat_loop(self):
        while self._running:
            await self.event_bus.publish(HealthEvent(
                engine=self.name, status=HealthStatus.HEALTHY,
                latency_ms=0, error_rate=0,
            ))
            await asyncio.sleep(5)
