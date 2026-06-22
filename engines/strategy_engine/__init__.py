"""
Strategy Engine — manages and executes all trading strategies.
Each strategy is isolated. Strategies never communicate directly.
"""
import asyncio
import logging
from typing import List, Optional
from dataclasses import dataclass
from datetime import datetime
import importlib
import os

from core.base import BaseEngine
from core.events import SignalEvent, AnalysisEvent, EventBus, HealthEvent, HealthStatus
from core.types import MarketAnalysis
from core.errors import StrategyError

logger = logging.getLogger("strategy_engine")


@dataclass
class StrategySignal:
    """Output signal from a single strategy."""
    symbol: str
    strategy_name: str
    action: str  # BUY, SELL, HOLD, IGNORE
    confidence: float
    score_breakdown: dict
    reasoning: str


class StrategyEngine(BaseEngine):
    """Manages and runs trading strategies in isolation."""

    def __init__(self, event_bus: EventBus):
        super().__init__("strategy_engine")
        self.event_bus = event_bus
        self.strategies: dict[str, object] = {}
        self._active_strategies: set[str] = set()
        self._last_signals: dict[str, StrategySignal] = {}
        self._strategy_dir = os.path.join(os.path.dirname(__file__), "..", "..", "strategies")

    async def initialize(self) -> None:
        await self.event_bus.subscribe("AnalysisEvent", self._on_analysis)
        self._load_strategies()
        self.logger.info(f"Strategy Engine initialized. Loaded: {list(self.strategies.keys())}")

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._heartbeat_loop())
        self.logger.info("Strategy Engine started.")

    async def stop(self) -> None:
        self._running = False

    def _load_strategies(self):
        """Dynamically load all strategies from the strategies directory."""
        try:
            strategies_dir = os.path.abspath(self._strategy_dir)
            if not os.path.exists(strategies_dir):
                self.logger.warning(f"Strategies directory not found: {strategies_dir}")
                return

            for filename in os.listdir(strategies_dir):
                if filename.endswith(".py") and not filename.startswith("_"):
                    module_name = filename[:-3]
                    try:
                        spec = importlib.util.spec_from_file_location(
                            module_name, os.path.join(strategies_dir, filename)
                        )
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)

                        # Find strategy class in module
                        for attr_name in dir(module):
                            attr = getattr(module, attr_name)
                            if (isinstance(attr, type) and
                                hasattr(attr, "evaluate") and
                                attr_name.endswith("Strategy")):
                                self.strategies[module_name] = attr()
                                self._active_strategies.add(module_name)
                                self.logger.info(f"  Loaded strategy: {module_name}")
                    except Exception as e:
                        self.logger.error(f"Failed to load strategy {module_name}: {e}")
        except Exception as e:
            self.logger.error(f"Strategy loading error: {e}")

    async def _on_analysis(self, event: AnalysisEvent):
        """When new analysis arrives, run all strategies."""
        if not self._running:
            return

        analysis = MarketAnalysis(
            symbol=event.symbol, regime=event.regime,
            trend_direction=event.trend_direction,
            trend_strength=event.trend_strength,
            momentum=event.momentum, volatility=event.volatility,
            liquidity_score=event.liquidity_score,
            structure=event.structure, breakout_state=event.breakout_state,
            noise_level=event.noise_level, confidence=event.confidence,
        )
        await self.run_strategies(event.symbol, analysis)

    async def run_strategies(self, symbol: str, analysis: MarketAnalysis) -> List[StrategySignal]:
        """Run all active strategies against market analysis."""
        signals = []
        for name in list(self._active_strategies):
            strategy = self.strategies.get(name)
            if not strategy:
                continue
            try:
                signal = await strategy.evaluate(analysis)
                if signal and signal.action != "HOLD":
                    signals.append(signal)
                    self._last_signals[symbol] = signal
                    # Publish signal event
                    await self.event_bus.publish(SignalEvent(
                        symbol=symbol,
                        strategy_name=signal.strategy_name,
                        action=signal.action,
                        confidence=signal.confidence,
                        score_breakdown=signal.score_breakdown,
                        reasoning=signal.reasoning,
                    ))
            except Exception as e:
                self.logger.error(f"Strategy {name} error for {symbol}: {e}")

        return signals

    def get_last_signal(self, symbol: str) -> Optional[StrategySignal]:
        return self._last_signals.get(symbol)

    def enable_strategy(self, name: str):
        if name in self.strategies:
            self._active_strategies.add(name)

    def disable_strategy(self, name: str):
        self._active_strategies.discard(name)

    async def _heartbeat_loop(self):
        while self._running:
            await self.event_bus.publish(HealthEvent(
                engine=self.name, status=HealthStatus.HEALTHY,
                latency_ms=0, error_rate=0,
            ))
            await asyncio.sleep(5)
