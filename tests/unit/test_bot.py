"""
File: tests/unit/test_bot.py
1. Single Responsibility: Verify contracts/config.py CoinConfig validators and bot helpers.
2. Consumes: contracts.config.
3. Produces: Tests for min-3-timeframes rule, distinct-timeframes rule, symbol uppercase.
4. Downstream: pytest run.
5. New Dependencies: pytest.
6. Touches Section 6 bugs? No.
7. Tests: Section 10 CoinConfig validator tests.
8. Logging: No.
9. Dependency Order: contracts -> tests.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from contracts.config import CoinConfig, SystemConfig


class TestCoinConfigValidation:
    """Section 0 hard-constraint 6 & 8: minimum 3 distinct timeframes."""

    def test_valid_coin_config(self):
        coin = CoinConfig(
            symbol="btcusdt",
            timeframes=["15m", "1h", "4h"],
            capital=10000.0,
            risk_percent=2.0,
        )
        assert coin.symbol == "BTCUSDT"  # uppercase validator
        assert coin.total_timeframe_count() == 3

    def test_less_than_three_timeframes_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            CoinConfig(
                symbol="BTCUSDT",
                timeframes=["15m", "1h"],  # only 2
                capital=10000.0,
                risk_percent=2.0,
            )
        assert "at least 3 timeframes" in str(exc_info.value).lower()

    def test_duplicate_timeframes_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            CoinConfig(
                symbol="BTCUSDT",
                timeframes=["15m", "15m", "1h"],  # duplicate
                capital=10000.0,
                risk_percent=2.0,
            )
        assert "distinct" in str(exc_info.value).lower()

    def test_invalid_timeframe_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            CoinConfig(
                symbol="BTCUSDT",
                timeframes=["15m", "1h", "17m"],  # 17m is not a valid Binance interval
                capital=10000.0,
                risk_percent=2.0,
            )
        assert "invalid" in str(exc_info.value).lower()

    def test_symbol_uppercased(self):
        coin = CoinConfig(
            symbol="btcusdt",
            timeframes=["15m", "1h", "4h"],
            capital=10000.0,
            risk_percent=2.0,
        )
        assert coin.symbol == "BTCUSDT"

    def test_empty_symbol_rejected(self):
        with pytest.raises(ValidationError):
            CoinConfig(
                symbol="",
                timeframes=["15m", "1h", "4h"],
                capital=10000.0,
                risk_percent=2.0,
            )

    def test_zero_capital_rejected(self):
        with pytest.raises(ValidationError):
            CoinConfig(
                symbol="BTCUSDT",
                timeframes=["15m", "1h", "4h"],
                capital=0.0,
                risk_percent=2.0,
            )

    def test_negative_capital_rejected(self):
        with pytest.raises(ValidationError):
            CoinConfig(
                symbol="BTCUSDT",
                timeframes=["15m", "1h", "4h"],
                capital=-1000.0,
                risk_percent=2.0,
            )

    def test_zero_risk_rejected(self):
        with pytest.raises(ValidationError):
            CoinConfig(
                symbol="BTCUSDT",
                timeframes=["15m", "1h", "4h"],
                capital=10000.0,
                risk_percent=0.0,
            )

    def test_risk_above_100_rejected(self):
        with pytest.raises(ValidationError):
            CoinConfig(
                symbol="BTCUSDT",
                timeframes=["15m", "1h", "4h"],
                capital=10000.0,
                risk_percent=150.0,
            )

    def test_risk_at_100_accepted(self):
        coin = CoinConfig(
            symbol="BTCUSDT",
            timeframes=["15m", "1h", "4h"],
            capital=10000.0,
            risk_percent=100.0,
        )
        assert coin.risk_percent == 100.0


class TestSystemConfig:
    def test_system_config_with_all_fields(self):
        cfg = SystemConfig(
            telegram_bot_token="token",
            supabase_url="https://x.supabase.co",
            supabase_key="key",
            redis_url="redis://localhost:6379/0",
        )
        assert cfg.telegram_bot_token == "token"
        assert cfg.max_active_coins == 10  # default
        assert cfg.simulation_mode is True  # default
        assert cfg.default_timeframes == ["15m", "1h", "4h"]  # default

    def test_system_config_simulation_mode_can_be_disabled(self):
        cfg = SystemConfig(
            telegram_bot_token="t", supabase_url="u", supabase_key="k",
            redis_url="r", simulation_mode=False,
        )
        assert cfg.simulation_mode is False
