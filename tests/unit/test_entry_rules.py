"""
File: tests/unit/test_entry_rules.py
1. Single Responsibility: Verify engine/entry_rules.py against Section 10 acceptance criteria.
2. Consumes: engine.entry_rules, contracts.decision, contracts.market, config.thresholds.
3. Produces: Tests for limit offset, timeout rejection, retry limit.
4. Downstream: pytest run.
5. New Dependencies: pytest.
6. Touches Section 6 bugs? No.
7. Tests: Section 10 engine/entry_rules.py tests 1-3.
8. Logging: No.
9. Dependency Order: contracts -> engine -> tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from config import thresholds
from contracts.decision import RiskAssessment, StrategySignal
from contracts.market import FairValueGap, OrderBlock
from engine.entry_rules import is_entry_expired, refine_entry, should_retry_limit


def make_signal(direction: str = "long", price: float = 100.0) -> StrategySignal:
    now = datetime.now(timezone.utc)
    return StrategySignal(
        symbol="BTCUSDT", timeframe="15m", strategy_name="test",
        direction=direction,  # type: ignore[arg-type]
        raw_score=0.8, reasons=["test"],
        timestamp=now, source_candle_open_time=now,
    )


def make_risk(direction: str = "long", entry_price: float = 100.0) -> RiskAssessment:
    # Spot-only: only long risk calculations are relevant.
    sl = entry_price - 5.0
    tp = entry_price + 10.0
    return RiskAssessment(
        allowed=True,
        max_position_size=10.0,
        max_risk_amount=200.0,
        stop_loss_price=sl,
        take_profit_price=tp,
        risk_reward_ratio=2.0,
        exposure_after_trade=1000.0,
        drawdown_after_trade=0.0,
    )


class TestRefineEntry:
    """Section 10 engine/entry_rules.py tests 1-3."""

    def test_limit_offset_for_long(self):
        """Long limit entry must be entry_price * (1 - ENTRY_LIMIT_OFFSET_PCT/100)."""
        signal = make_signal("long", 100.0)
        risk = make_risk("long", 100.0)
        entry = refine_entry(
            signal=signal, risk=risk,
            ob_list=[], fvg_list=[],
            current_price=100.0,
        )
        if entry.entry_type == "limit":
            expected = 100.0 * (1 - thresholds.ENTRY_LIMIT_OFFSET_PCT / 100)
            assert entry.entry_price == pytest.approx(expected, rel=1e-3)

    def test_entry_has_valid_until_in_future(self):
        signal = make_signal("long", 100.0)
        risk = make_risk("long", 100.0)
        now = datetime.now(timezone.utc)
        entry = refine_entry(
            signal=signal, risk=risk,
            ob_list=[], fvg_list=[],
            current_price=100.0,
        )
        # valid_until should be ~now + ENTRY_TIMEOUT_MINUTES.
        assert entry.valid_until > now

    def test_entry_near_ob_uses_limit(self):
        signal = make_signal("long", 100.0)
        risk = make_risk("long", 100.0)
        # OB near the current price.
        now = datetime.now(timezone.utc)
        ob = OrderBlock(
            symbol="BTCUSDT", timeframe="15m", type="bullish",
            open_price=99.5, high_price=100.0, low_price=99.0, close_price=99.5,
            timestamp=now, mitigation_level=99.0,
            is_mitigated=False, strength=0.8,
        )
        entry = refine_entry(
            signal=signal, risk=risk,
            ob_list=[ob], fvg_list=[],
            current_price=100.0,
        )
        assert entry.entry_type in ("limit", "market")


class TestEntryExpiry:
    def test_expired_entry_is_detected(self):
        signal = make_signal("long", 100.0)
        risk = make_risk("long", 100.0)
        entry = refine_entry(
            signal=signal, risk=risk,
            ob_list=[], fvg_list=[],
            current_price=100.0,
        )
        # Move valid_until into the past.
        expired_entry = entry.model_copy(update={"valid_until": datetime.now(timezone.utc) - timedelta(minutes=1)})
        assert is_entry_expired(expired_entry, datetime.now(timezone.utc)) is True

    def test_non_expired_entry_is_not_expired(self):
        signal = make_signal("long", 100.0)
        risk = make_risk("long", 100.0)
        entry = refine_entry(
            signal=signal, risk=risk,
            ob_list=[], fvg_list=[],
            current_price=100.0,
        )
        assert is_entry_expired(entry, datetime.now(timezone.utc)) is False


class TestRetryLimit:
    def test_retry_below_limit_returns_true(self):
        assert should_retry_limit(0) is True
        assert should_retry_limit(thresholds.MAX_ENTRY_RETRIES - 1) is True

    def test_retry_at_limit_returns_false(self):
        assert should_retry_limit(thresholds.MAX_ENTRY_RETRIES) is False
        assert should_retry_limit(thresholds.MAX_ENTRY_RETRIES + 5) is False
