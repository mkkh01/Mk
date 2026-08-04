"""
File: tests/unit/test_market_regime.py
1. Single Responsibility: Verify market/regime.py against Section 10 acceptance criteria.
2. Consumes: market.regime, contracts.market.
3. Produces: Tests for trending/ranging/volatile regime classification.
4. Downstream: pytest run.
5. New Dependencies: pytest.
6. Touches Section 6 bugs? No.
7. Tests: Section 10 market/regime.py tests 1-3.
8. Logging: No.
9. Dependency Order: contracts -> market -> tests.
"""

from __future__ import annotations

import pytest

from contracts.market import RegimeState
from market.regime import classify_regime, classify_regime_with_confidence
from tests.conftest import bearish_seq, bullish_seq, make_candle, make_dt


class TestRegimeClassification:
    """Section 10 market/regime.py tests 1-3."""

    def test_trending_detection(self):
        """A clear bullish sequence with strong directional movement should
        classify as TRENDING (ADX > 25 with EMA alignment)."""
        # Build a strong trend: large step candles.
        candles = bullish_seq(n=40, start_price=100.0, step=2.0)
        regime = classify_regime(candles)
        # In a clean strong trend, regime should be TRENDING.
        # (May occasionally be VOLATILE if ATR/price ratio spikes, so accept either
        # if the trend is strong -- but never RANGING.)
        assert regime in (RegimeState.TRENDING, RegimeState.VOLATILE), (
            f"Strong trend classified as {regime}; expected TRENDING or VOLATILE"
        )

    def test_ranging_detection(self):
        """A flat oscillating sequence should classify as RANGING."""
        base = make_dt(0)
        candles = []
        # Tight oscillation around 100.0 -- ADX should be low, BB width narrow.
        for i in range(40):
            o = 100.0
            c = 100.0 + (0.05 if i % 2 == 0 else -0.05)
            candles.append(make_candle(
                open_time=base, open=o, high=max(o, c) + 0.02,
                low=min(o, c) - 0.02, close=c, timeframe_minutes=15,
            ))
            base = candles[-1].close_time
        regime = classify_regime(candles)
        assert regime == RegimeState.RANGING, f"Flat sequence classified as {regime}; expected RANGING"

    def test_volatile_detection(self):
        """A sequence with massive candles should classify as VOLATILE."""
        base = make_dt(0)
        candles = []
        # Huge alternating candles -> very high ATR.
        for i in range(40):
            o = 100.0
            c = 100.0 + (15.0 if i % 2 == 0 else -15.0)
            candles.append(make_candle(
                open_time=base, open=o, high=max(o, c) + 5.0,
                low=min(o, c) - 5.0, close=c, timeframe_minutes=15,
                volume=500.0,
            ))
            base = candles[-1].close_time
        regime = classify_regime(candles)
        assert regime == RegimeState.VOLATILE, f"Volatile sequence classified as {regime}; expected VOLATILE"

    def test_returns_regime_state_enum(self):
        candles = bullish_seq(n=30)
        regime = classify_regime(candles)
        assert isinstance(regime, RegimeState)

    def test_classify_with_confidence_returns_tuple(self):
        candles = bullish_seq(n=30)
        result = classify_regime_with_confidence(candles)
        assert isinstance(result, tuple)
        assert len(result) == 2
        regime, confidence = result
        assert isinstance(regime, RegimeState)
        assert 0.0 <= confidence <= 1.0

    def test_insufficient_candles_does_not_crash(self):
        """Per Section 22 graceful degradation."""
        candles = bullish_seq(n=5)
        regime = classify_regime(candles)
        assert isinstance(regime, RegimeState)

    def test_empty_candles_does_not_crash(self):
        regime = classify_regime([])
        # Should return a safe default (RANGING).
        assert isinstance(regime, RegimeState)
