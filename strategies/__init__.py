"""
Standard strategy interface.
Every strategy must implement: evaluate(analysis) → StrategySignal
"""
from dataclasses import dataclass, field
from core.types import MarketAnalysis


@dataclass
class StrategySignal:
    symbol: str = ""
    strategy_name: str = ""
    action: str = "HOLD"  # BUY, SELL, HOLD
    confidence: float = 0.0
    score_breakdown: dict = field(default_factory=dict)
    reasoning: str = ""


class BaseStrategy:
    """Abstract base for all trading strategies."""
    name: str = "base"

    async def evaluate(self, analysis: MarketAnalysis) -> StrategySignal:
        raise NotImplementedError
