"""
File: contracts/simulation.py
1. Single Responsibility: Define the SimulatedTrade model.
2. Consumes: nothing.
3. Produces: SimulatedTrade.
4. Downstream: simulation/paper_trade.py, storage/supabase.py, portfolio/performance.py.
5. New Dependencies: pydantic.
6. Touches Section 6 bugs? No.
7. Tests: tests/unit/test_simulation.py.
8. Logging: No.
9. Dependency Order: contracts/decision.py -> contracts/simulation.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class SimulatedTrade(BaseModel):
    """A single simulated trade.

    Per Section 0 hard-constraint 7, every instance MUST be flagged as
    ``is_simulated=True`` and never relabelled as "live" in user-facing text.
    """

    id: UUID = Field(default_factory=uuid4)
    decision_id: UUID
    symbol: str
    direction: Literal["long"]
    entry_price: float
    size: float
    fee: float
    slippage: float
    opened_at: datetime
    closed_at: Optional[datetime] = None
    pnl: Optional[float] = None
    status: Literal["open", "closed"] = "open"
    close_reason: Optional[Literal["tp", "sl", "time", "manual"]] = None
    close_price: Optional[float] = None
    is_simulated: bool = True
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    # Trailing Stop tracking fields (Section 0 hard-constraint 7 compliant).
    highest_price: Optional[float] = None
    """Highest price reached since trade opened (for LONG positions)."""
    lowest_price: Optional[float] = None
    """Lowest price reached since trade opened (for SHORT positions)."""
    atr_at_entry: Optional[float] = None
    """ATR value at the time of entry, used to calculate trailing stop distance."""
    initial_stop_loss: Optional[float] = None
    """Immutable initial stop loss, used for trailing activation threshold."""
    timeframe: str = "15m"
    """The timeframe this trade was opened on, used for ATR lookups."""
