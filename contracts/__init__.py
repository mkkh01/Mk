"""
File: contracts/__init__.py
1. Single Responsibility: Re-export every public contract for convenient imports.
2. Consumes: every contract module.
3. Produces: a single import surface.
4. Downstream: every consumer module.
5. New Dependencies: pydantic (transitively).
6. Touches Section 6 bugs? No.
7. Tests: No.
8. Logging: No.
9. Dependency Order: config -> contracts/__init__.py -> ...
"""

from contracts.config import CoinConfig, SystemConfig
from contracts.decision import (
    DecisionResult,
    EntrySignal,
    HTFFilterResult,
    RiskAssessment,
    StrategySignal,
)
from contracts.market import (
    Candle,
    FairValueGap,
    LiquiditySweep,
    MarketStructure,
    OrderBlock,
    RegimeState,
    SwingPoint,
)
from contracts.portfolio import PerformanceMetrics, TradeSummary
from contracts.simulation import SimulatedTrade

__all__ = [
    "Candle",
    "CoinConfig",
    "DecisionResult",
    "EntrySignal",
    "FairValueGap",
    "HTFFilterResult",
    "LiquiditySweep",
    "MarketStructure",
    "OrderBlock",
    "PerformanceMetrics",
    "RegimeState",
    "RiskAssessment",
    "SimulatedTrade",
    "StrategySignal",
    "SwingPoint",
    "SystemConfig",
    "TradeSummary",
]
