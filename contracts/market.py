"""
File: contracts/market.py
1. Single Responsibility: Define every Pydantic model describing market data
   (candles, regime state, structure, SMC primitives).
2. Consumes: nothing (pure pydantic models).
3. Produces: Candle, RegimeState, LiquiditySweep, SwingPoint, OrderBlock,
   FairValueGap, MarketStructure.
4. Downstream: storage, ingest, data, market, engine, simulation, portfolio, bot.
5. New Dependencies: pydantic.
6. Touches Section 6 bugs? No.
7. Tests: No (validated indirectly by every consumer test).
8. Logging: No.
9. Dependency Order: config -> contracts/market.py -> ...
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Candle
# ---------------------------------------------------------------------------
class Candle(BaseModel):
    """A single OHLCV candle, as produced by the Binance kline stream.

    ``is_closed`` must be respected everywhere downstream: only closed candles
    are written to the candles table or used for structure detection
    (Section 6, Bug 3 -- prevents repainting).
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float
    taker_sell_volume: float
    is_closed: bool

    def body(self) -> float:
        """Absolute size of the candle body."""
        return abs(self.close - self.open)

    def range(self) -> float:
        """High-low range."""
        return self.high - self.low

    def is_bullish(self) -> bool:
        return self.close > self.open

    def is_bearish(self) -> bool:
        return self.close < self.open

    def taker_delta(self) -> float:
        """Buy minus sell taker volume (Section 6, Bug 2 -- the only CVD input)."""
        return self.taker_buy_volume - self.taker_sell_volume


# ---------------------------------------------------------------------------
# Regime
# ---------------------------------------------------------------------------
class RegimeState(str, Enum):
    TRENDING = "trending"
    RANGING = "ranging"
    VOLATILE = "volatile"


# ---------------------------------------------------------------------------
# Swing points
# ---------------------------------------------------------------------------
class SwingPoint(BaseModel):
    """A confirmed swing high or swing low."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    price: float
    timestamp: datetime
    type: Literal["high", "low"]
    index: int


# ---------------------------------------------------------------------------
# Liquidity sweep (Section 6 Bug 1 lives here -- direction is REVERSAL direction)
# ---------------------------------------------------------------------------
class LiquiditySweep(BaseModel):
    """A liquidity sweep.

    IMPORTANT (Section 6, Bug 1):
      * A *high* sweep (price pokes above a swing high then rejects down) is
        ``direction == "bearish"`` (bearish reversal).
      * A *low* sweep (price pokes below a swing low then rejects up) is
        ``direction == "bullish"`` (bullish reversal).
    The ``direction`` field is the REVERSAL direction, NOT the sweep direction.
    """

    symbol: str
    timeframe: str
    swept_level: float
    direction: Literal["bullish", "bearish"]
    strength: float = Field(ge=0.0, le=1.0)
    timestamp: datetime
    confirming_candle_close: float


# ---------------------------------------------------------------------------
# Order Block
# ---------------------------------------------------------------------------
class OrderBlock(BaseModel):
    """A bullish or bearish order block (the last opposite-colour candle before
    a strong impulse)."""

    symbol: str
    timeframe: str
    type: Literal["bullish", "bearish"]
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    timestamp: datetime
    mitigation_level: float
    is_mitigated: bool = False
    strength: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Fair Value Gap
# ---------------------------------------------------------------------------
class FairValueGap(BaseModel):
    """A 3-candle imbalance (bullish or bearish)."""

    symbol: str
    timeframe: str
    type: Literal["bullish", "bearish"]
    top: float
    bottom: float
    timestamp: datetime
    is_filled: bool = False
    fill_percentage: float = 0.0


# ---------------------------------------------------------------------------
# Market structure (output of engine/structure.py)
# ---------------------------------------------------------------------------
class MarketStructure(BaseModel):
    """Aggregate structure state for one symbol/timeframe."""

    symbol: str
    timeframe: str
    last_swing_high: Optional[SwingPoint] = None
    last_swing_low: Optional[SwingPoint] = None
    last_bos: Optional[datetime] = None
    last_choch: Optional[datetime] = None
    trend_direction: Literal["up", "down", "neutral"] = "neutral"
    structure_breaks: list[datetime] = Field(default_factory=list)
