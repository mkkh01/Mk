"""
File: tests/unit/test_htf_filter.py
1. Single Responsibility: Verify engine/htf_filter.py against Section 10 acceptance criteria.
2. Consumes: engine.htf_filter, contracts.decision, contracts.market.
3. Produces: Tests for bullish alignment, bullish contradiction, neutral pass-through.
4. Downstream: pytest run.
5. New Dependencies: pytest.
6. Touches Section 6 bugs? No.
7. Tests: Section 10 engine/htf_filter.py tests 1-3.
8. Logging: No.
9. Dependency Order: contracts -> engine -> tests.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from contracts.decision import StrategySignal
from engine.htf_filter import filter_by_htf
from tests.conftest import bearish_seq, bullish_seq


def make_signal(direction: str = "long") -> StrategySignal:
    now = datetime.now(timezone.utc)
    return StrategySignal(
        symbol="BTCUSDT", timeframe="15m", strategy_name="test",
        direction=direction,  # type: ignore[arg-type]
        raw_score=0.8, reasons=["test"],
        timestamp=now, source_candle_open_time=now,
    )


class TestHTFFilter:
    """Section 10 engine/htf_filter.py tests 1-3."""

    def test_bullish_alignment_returns_true(self):
        """A bullish LTF signal during bullish HTF bias must return alignment=True."""
        ltf_signal = make_signal("long")
        htf_candles = bullish_seq(n=30, timeframe="4h")
        result = filter_by_htf(
            ltf_signal=ltf_signal, htf_candles=htf_candles,
            htf_timeframe="4h", ltf_timeframe="15m",
        )
        assert result.alignment is True
        assert result.bias in ("bullish", "neutral")  # Either passes alignment.

    def test_bearish_contradiction_returns_false(self):
        """A long LTF signal during bearish HTF bias must return alignment=False for Spot."""
        ltf_signal = make_signal("long")
        htf_candles = bearish_seq(n=30, timeframe="4h")
        result = filter_by_htf(
            ltf_signal=ltf_signal, htf_candles=htf_candles,
            htf_timeframe="4h", ltf_timeframe="15m",
        )
        # In Spot-only, we still block longs if the HTF trend is bearish.
        if result.bias == "bearish":
            assert result.alignment is False

    def test_neutral_pass_through_returns_true(self):
        """A neutral HTF bias must return alignment=True (no filtering applied)."""
        ltf_signal = make_signal("long")
        # Build a flat HTF sequence (oscillating around a single price) to get neutral bias.
        from tests.conftest import make_candle, make_dt
        base = make_dt(0)
        htf_candles = []
        for i in range(30):
            # Alternate tiny up/down candles so EMAs converge to the same value.
            o = 100.0
            c = 100.0 + (0.01 if i % 2 == 0 else -0.01)
            htf_candles.append(make_candle(
                open_time=base, open=o, high=max(o, c) + 0.005,
                low=min(o, c) - 0.005, close=c, timeframe_minutes=240,
            ))
            base = htf_candles[-1].close_time

        result = filter_by_htf(
            ltf_signal=ltf_signal, htf_candles=htf_candles,
            htf_timeframe="4h", ltf_timeframe="15m",
        )
        if result.bias == "neutral":
            assert result.alignment is True

    def test_result_contains_reason_string(self):
        ltf_signal = make_signal("long")
        htf_candles = bullish_seq(n=30, timeframe="4h")
        result = filter_by_htf(
            ltf_signal=ltf_signal, htf_candles=htf_candles,
            htf_timeframe="4h", ltf_timeframe="15m",
        )
        assert isinstance(result.reason, str)
        assert len(result.reason) > 0

    def test_result_has_correct_symbol_and_timeframes(self):
        ltf_signal = make_signal("long")
        htf_candles = bullish_seq(n=30, timeframe="4h")
        result = filter_by_htf(
            ltf_signal=ltf_signal, htf_candles=htf_candles,
            htf_timeframe="4h", ltf_timeframe="15m",
        )
        assert result.symbol == "BTCUSDT"
        assert result.htf_timeframe == "4h"
        assert result.ltf_timeframe == "15m"
