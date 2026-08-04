"""
File: contracts/config.py
1. Single Responsibility: Define CoinConfig (per-coin configuration) and SystemConfig
   (whole-system configuration).
2. Consumes: nothing.
3. Produces: CoinConfig, SystemConfig.
4. Downstream: config/settings.py, app/main.py, bot/telegram_bot.py, engine/orchestrator.py.
5. New Dependencies: pydantic.
6. Touches Section 6 bugs? No.
7. Tests: tests/unit/test_bot.py validates the min-3-timeframes rule and the
   distinct-timeframes rule.
8. Logging: No.
9. Dependency Order: contracts -> config/settings.py -> app/main.py.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field, field_validator

from config.thresholds import VALID_TIMEFRAMES


class CoinConfig(BaseModel):
    """Per-coin configuration.

    Hard constraint (Section 0 #6 + #8): every coin MUST have at least 3
    distinct timeframes -- one for entry, one for confirmation, one for HTF
    bias. This is enforced here in Pydantic, again at DB level (Section 5
    migration 002), and again in engine/orchestrator.py.
    """

    symbol: str = Field(..., min_length=1)
    timeframes: list[str]
    capital: float = Field(gt=0.0)
    risk_percent: float = Field(gt=0.0, le=100.0)
    is_active: bool = True

    @field_validator("timeframes")
    @classmethod
    def min_three_timeframes(cls, v: list[str]) -> list[str]:
        if len(v) < 3:
            raise ValueError("at least 3 timeframes are required per coin")
        if len(set(v)) != len(v):
            raise ValueError("timeframes must be distinct (no duplicates allowed)")
        invalid = [tf for tf in v if tf not in VALID_TIMEFRAMES]
        if invalid:
            raise ValueError(f"invalid timeframes: {invalid}")
        return v

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, v: str) -> str:
        return v.upper()

    def total_timeframe_count(self) -> int:
        return len(self.timeframes)


class SystemConfig(BaseModel):
    """Whole-system configuration loaded from config/settings.py.

    Per Section 3, plain Python values -- NO .env, NO os.environ.
    """

    telegram_bot_token: str
    supabase_url: str
    supabase_key: str
    redis_url: str
    default_timeframes: list[str] = Field(default_factory=lambda: ["15m", "1h", "4h"])
    max_active_coins: int = 10
    simulation_mode: bool = True
    telegram_chat_id: Optional[str] = None
