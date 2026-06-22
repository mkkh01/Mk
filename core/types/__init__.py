"""
Shared types and dataclasses used across all engines.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum


class MarketRegime(Enum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"
    CHOPPY = "CHOPPY"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    UNKNOWN = "UNKNOWN"


class TrendDirection(Enum):
    UP = "UP"
    DOWN = "DOWN"
    NONE = "NONE"


class TradeAction(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    IGNORE = "IGNORE"


class TradeStatus(Enum):
    OPEN = "OPEN"
    WON = "WON"
    LOST = "LOST"
    CANCELLED = "CANCELLED"


class RiskLevel(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


class SystemState(Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    RISKY = "RISKY"
    SAFE_MODE = "SAFE_MODE"
    STOPPED = "STOPPED"


@dataclass
class MarketAnalysis:
    """Output of Market Analyzer Engine."""
    symbol: str = ""
    regime: str = "UNKNOWN"
    trend_direction: str = "NONE"
    trend_strength: float = 0.0
    momentum: float = 0.0
    volatility: float = 0.0
    liquidity_score: float = 0.0
    structure: dict = field(default_factory=dict)
    breakout_state: str = "NONE"
    noise_level: float = 0.0
    confidence: float = 0.0
    current_price: float = 0.0
    current_volume: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class EvidenceResult:
    """Output of Evidence Engine."""
    symbol: str = ""
    decision: str = "HOLD"
    confidence: float = 0.0
    final_score: float = 0.0
    evidence: dict = field(default_factory=dict)
    conflicts: list = field(default_factory=list)
    reasoning: str = ""
    risk_approved: bool = False
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class RiskDecision:
    """Output of Risk Engine."""
    trade_allowed: bool = False
    risk_level: str = "LOW"
    position_size: float = 0.0
    max_loss: float = 0.0
    stop_loss_distance: float = 0.0
    take_profit_ratio: float = 0.0
    reasoning: str = ""
    blocking_reason: str = ""


@dataclass
class ExecutionResult:
    """Output of Execution Engine."""
    order_id: str = ""
    symbol: str = ""
    status: str = "PENDING"
    entry_price: float = 0.0
    executed_quantity: float = 0.0
    slippage: float = 0.0
    fees: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class PortfolioSnapshot:
    """Output of Portfolio Engine."""
    balance: float = 0.0
    equity: float = 0.0
    open_positions: int = 0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    drawdown: float = 0.0
    status: str = "ACTIVE"


@dataclass
class UnifiedMarketData:
    """Normalized market data from any exchange."""
    symbol: str = ""
    price: float = 0.0
    volume: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)
    exchange: str = "binance"
