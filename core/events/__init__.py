"""
Event system — the ONLY communication channel between engines.
All engines communicate exclusively via events. No direct imports.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Any
import asyncio
import logging

logger = logging.getLogger("events")

# ── Event Bus ──────────────────────────────────────────────
class EventBus:
    """Central async event bus. Engines subscribe, services publish."""
    def __init__(self):
        self._subscribers: dict[str, list] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, event_type: str, callback):
        async with self._lock:
            self._subscribers.setdefault(event_type, []).append(callback)

    async def publish(self, event):
        event_type = event.__class__.__name__
        callbacks = self._subscribers.get(event_type, [])
        results = []
        for cb in callbacks:
            try:
                results.append(await cb(event))
            except Exception as e:
                logger.error(f"Event handler error for {event_type}: {e}", exc_info=True)
        return results

# ── Base Event ─────────────────────────────────────────────
@dataclass
class BaseEvent:
    timestamp: datetime = field(default_factory=datetime.utcnow)

# ── Market Data Events ─────────────────────────────────────
@dataclass
class MarketTickEvent(BaseEvent):
    symbol: str = ""
    price: float = 0.0
    volume: float = 0.0
    exchange: str = "binance"

@dataclass
class CandleUpdateEvent(BaseEvent):
    symbol: str = ""
    timeframe: str = "15m"
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    is_closed: bool = False

@dataclass
class OrderBookEvent(BaseEvent):
    symbol: str = ""
    bid: float = 0.0
    ask: float = 0.0
    bid_volume: float = 0.0
    ask_volume: float = 0.0

@dataclass
class TradeEvent(BaseEvent):
    symbol: str = ""
    price: float = 0.0
    quantity: float = 0.0
    value_usd: float = 0.0
    is_buyer_maker: bool = False

# ── Analysis Events ────────────────────────────────────────
@dataclass
class AnalysisEvent(BaseEvent):
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

# ── Signal Events ──────────────────────────────────────────
@dataclass
class SignalEvent(BaseEvent):
    symbol: str = ""
    strategy_name: str = ""
    action: str = "HOLD"  # BUY, SELL, HOLD, IGNORE
    confidence: float = 0.0
    score_breakdown: dict = field(default_factory=dict)
    market_conditions: dict = field(default_factory=dict)
    reasoning: str = ""

# ── Evidence Events ────────────────────────────────────────
@dataclass
class EvidenceEvent(BaseEvent):
    symbol: str = ""
    decision: str = "HOLD"
    confidence: float = 0.0
    final_score: float = 0.0
    evidence: dict = field(default_factory=dict)
    conflicts: list = field(default_factory=list)
    reasoning: str = ""
    risk_approved: bool = False

# ── Risk Events ────────────────────────────────────────────
@dataclass
class RiskEvent(BaseEvent):
    trade_allowed: bool = False
    risk_level: str = "LOW"
    position_size: float = 0.0
    max_loss: float = 0.0
    stop_loss_distance: float = 0.0
    take_profit_ratio: float = 0.0
    reasoning: str = ""
    blocking_reason: str = ""

# ── Execution Events ───────────────────────────────────────
@dataclass
class ExecutionEvent(BaseEvent):
    order_id: str = ""
    symbol: str = ""
    status: str = "PENDING"
    entry_price: float = 0.0
    executed_quantity: float = 0.0
    slippage: float = 0.0
    fees: float = 0.0
    side: str = "BUY"
    strategy_used: str = ""

# ── Portfolio Events ───────────────────────────────────────
@dataclass
class PortfolioEvent(BaseEvent):
    balance: float = 0.0
    equity: float = 0.0
    open_positions: int = 0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    drawdown: float = 0.0
    status: str = "ACTIVE"

# ── Alert Events ───────────────────────────────────────────
class AlertLevel(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

@dataclass
class AlertEvent(BaseEvent):
    level: AlertLevel = AlertLevel.INFO
    module: str = ""
    message: str = ""
    context: dict = field(default_factory=dict)

# ── Whale Events ───────────────────────────────────────────
@dataclass
class WhaleEvent(BaseEvent):
    symbol: str = ""
    volume: float = 0.0
    value_usd: float = 0.0
    direction: str = "IN"
    is_market_trade: bool = False
    action_label: str = ""

# ── Health Events ──────────────────────────────────────────
class HealthStatus(Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"

@dataclass
class HealthEvent(BaseEvent):
    engine: str = ""
    status: HealthStatus = HealthStatus.HEALTHY
    latency_ms: float = 0.0
    error_rate: float = 0.0
    memory_usage: float = 0.0
    last_update: Optional[datetime] = None

# ── Log Events ─────────────────────────────────────────────
class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

@dataclass
class LogEvent(BaseEvent):
    level: LogLevel = LogLevel.INFO
    module: str = ""
    message: str = ""
    context: dict = field(default_factory=dict)
    stack_trace: Optional[str] = None
    recovery_result: Optional[str] = None
