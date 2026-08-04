"""
File: contracts/decision.py
1. Single Responsibility: Define all decision-pipeline Pydantic models.
2. Consumes: nothing.
3. Produces: StrategySignal, RiskAssessment, EntrySignal, DecisionResult, HTFFilterResult.
4. Downstream: engine/*, simulation/*, storage/supabase.py, bot/telegram_bot.py.
5. New Dependencies: pydantic.
6. Touches Section 6 bugs? No.
7. Tests: Section 10 acceptance tests for orchestrator / risk.
8. Logging: No.
9. Dependency Order: contracts/market.py -> contracts/decision.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class StrategySignal(BaseModel):
    """A single component signal produced by one engine module on one timeframe."""

    symbol: str
    timeframe: str
    strategy_name: str
    direction: Literal["long", "neutral"]
    raw_score: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    timestamp: datetime
    source_candle_open_time: datetime


class RiskAssessment(BaseModel):
    """Output of engine/risk.py.

    ``allowed == False`` MUST always be paired with a non-empty ``reason``.
    """

    allowed: bool
    max_position_size: float = 0.0
    max_risk_amount: float = 0.0
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    risk_reward_ratio: Optional[float] = None
    reason: Optional[str] = None
    exposure_after_trade: float = 0.0
    drawdown_after_trade: float = 0.0


class EntrySignal(BaseModel):
    """Refined entry produced by engine/entry_rules.py after risk approval."""

    symbol: str
    direction: Literal["long"]
    entry_price: float
    entry_type: Literal["limit", "market"]
    timeframe: str
    confidence: float
    reasons: list[str] = Field(default_factory=list)
    stop_loss: float
    take_profit: float
    risk_reward: float
    valid_until: datetime


class DecisionResult(BaseModel):
    """Final output of engine/orchestrator.py.

    ``unique (symbol, source_candle_open_time)`` enforces idempotency at the DB
    level (Section 4 + Section 5).
    """

    id: UUID = Field(default_factory=uuid4)
    symbol: str
    source_candle_open_time: datetime
    score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    regime_check_passed: bool
    structure_alignment_passed: bool
    htf_bias_aligned: bool
    risk: RiskAssessment
    entry: Optional[EntrySignal] = None
    final_verdict: bool
    rejection_reason: Optional[str] = None
    component_signals: list[StrategySignal] = Field(default_factory=list)
    trigger_timeframe: str = ""
    """Timeframe of the trigger candle that initiated the analysis."""
    timestamp: datetime


class HTFFilterResult(BaseModel):
    """Output of engine/htf_filter.py."""

    symbol: str
    htf_timeframe: str
    ltf_timeframe: str
    bias: Literal["bullish", "bearish", "neutral"]
    alignment: bool
    reason: str
    timestamp: datetime
