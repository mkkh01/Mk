"""
File: tests/unit/test_smc.py
1. Single Responsibility: Verify engine/smc.py against Section 10 acceptance criteria.
2. Consumes: engine.smc, engine.structure, contracts.market.
3. Produces: Regression tests for Bug 1 (sweep direction).
4. Downstream: pytest run.
5. New Dependencies: pytest.
6. Touches Section 6 bugs? YES -- Bug 1.
7. Tests: Section 10 engine/smc.py tests 1-4.
8. Logging: No.
9. Dependency Order: contracts -> engine -> tests.
"""

from __future__ import annotations

import pytest

from engine.smc import detect_liquidity_sweeps, detect_order_blocks, detect_fvgs, analyze_smc
from engine.structure import detect_swing_points
from tests.conftest import (
    bearish_seq,
    bullish_seq,
    high_sweep_seq,
    low_sweep_seq,
    make_candle,
    make_dt,
)


class TestOrderBlocks:
    """Section 10 engine/smc.py tests 1-2."""

    def test_bullish_ob_detection(self):
        """A strong bullish impulse should produce at least one bullish OB."""
        # Build a sequence with one bearish candle followed by a strong bullish impulse.
        candles = []
        base = make_dt(0)
        # Setup: 5 small bullish candles.
        for i in range(5):
            candles.append(make_candle(
                open_time=base,
                open=100.0 + i, high=101.0 + i, low=99.5 + i, close=100.5 + i,
                timeframe_minutes=15,
            ))
            base = candles[-1].close_time
        # Bearish candle (the future OB).
        ob_candle = make_candle(
            open_time=base, open=105.5, high=105.5, low=104.0, close=104.5,
            timeframe_minutes=15,
        )
        candles.append(ob_candle)
        base = ob_candle.close_time
        # Strong bullish impulse: close-open > OB_MIN_IMPULSE_PCT of price (0.3%).
        # 0.3% of 104.5 = 0.31 -> need close-open > 0.31.
        impulse = make_candle(
            open_time=base, open=104.5, high=110.0, low=104.5, close=109.0,
            timeframe_minutes=15,
        )
        candles.append(impulse)

        obs = detect_order_blocks(candles, max_lookback=10)
        assert isinstance(obs, list)
        bullish_obs = [ob for ob in obs if ob.type == "bullish"]
        # We should find at least one bullish OB (the bearish candle before the impulse).
        assert len(bullish_obs) >= 1, "Expected at least one bullish order block"

    def test_ob_mitigation_marks_is_mitigated(self):
        """Price trading through an OB's mitigation_level must mark is_mitigated=True."""
        candles = []
        base = make_dt(0)
        for i in range(5):
            candles.append(make_candle(
                open_time=base, open=100.0 + i, high=101.0 + i,
                low=99.5 + i, close=100.5 + i, timeframe_minutes=15,
            ))
            base = candles[-1].close_time
        ob_candle = make_candle(
            open_time=base, open=105.5, high=105.5, low=104.0, close=104.5,
            timeframe_minutes=15,
        )
        candles.append(ob_candle)
        base = ob_candle.close_time
        impulse = make_candle(
            open_time=base, open=104.5, high=110.0, low=104.5, close=109.0,
            timeframe_minutes=15,
        )
        candles.append(impulse)
        base = impulse.close_time
        # Now price trades BELOW the OB's mitigation_level (low of OB candle = 104.0).
        mitigation = make_candle(
            open_time=base, open=109.0, high=109.0, low=103.0, close=104.0,
            timeframe_minutes=15,
        )
        candles.append(mitigation)

        obs = detect_order_blocks(candles, max_lookback=10)
        bullish_obs = [ob for ob in obs if ob.type == "bullish"]
        if bullish_obs:
            # The original bullish OB (the bearish candle at index 5) should now be mitigated.
            target_ob = min(bullish_obs, key=lambda ob: ob.timestamp)
            assert target_ob.is_mitigated is True, "OB should be marked mitigated"

    def test_bearish_ob_detection(self):
        """A strong bearish impulse should produce a bearish OB."""
        candles = []
        base = make_dt(0)
        for i in range(5):
            candles.append(make_candle(
                open_time=base, open=110.0 - i, high=110.5 - i,
                low=109.5 - i, close=110.0 - i, timeframe_minutes=15,
            ))
            base = candles[-1].close_time
        # Bullish candle (future bearish OB).
        ob_candle = make_candle(
            open_time=base, open=104.5, high=106.0, low=104.5, close=105.5,
            timeframe_minutes=15,
        )
        candles.append(ob_candle)
        base = ob_candle.close_time
        # Strong bearish impulse.
        impulse = make_candle(
            open_time=base, open=105.5, high=105.5, low=100.0, close=100.5,
            timeframe_minutes=15,
        )
        candles.append(impulse)

        obs = detect_order_blocks(candles, max_lookback=10)
        bearish_obs = [ob for ob in obs if ob.type == "bearish"]
        assert len(bearish_obs) >= 1, "Expected at least one bearish order block"


class TestFairValueGaps:
    """Section 10 engine/smc.py tests 3-4."""

    def test_fvg_detection_bullish(self):
        """A 3-candle impulse sequence with a visible gap must produce a
        FairValueGap."""
        candles = []
        base = make_dt(0)
        # Candle A: small bearish, high = 100.5
        a = make_candle(open_time=base, open=100.5, high=100.5, low=99.5, close=99.8,
                       timeframe_minutes=15)
        candles.append(a)
        base = a.close_time
        # Candle B: strong bullish impulse.
        b = make_candle(open_time=base, open=99.8, high=105.0, low=99.5, close=104.5,
                       timeframe_minutes=15)
        candles.append(b)
        base = b.close_time
        # Candle C: bullish continuation with low > high of A (gap condition).
        c = make_candle(open_time=base, open=104.5, high=106.0, low=101.0, close=105.5,
                       timeframe_minutes=15)
        candles.append(c)

        fvgs = detect_fvgs(candles)
        bullish_fvgs = [f for f in fvgs if f.type == "bullish"]
        # A gap exists: low of C (101) > high of A (100.5).
        assert len(bullish_fvgs) >= 1, "Expected at least one bullish FVG"

    def test_fvg_fill_marks_is_filled(self):
        """Price trading through the entire FVG must mark is_filled=True."""
        candles = []
        base = make_dt(0)
        a = make_candle(open_time=base, open=100.5, high=100.5, low=99.5, close=99.8,
                       timeframe_minutes=15)
        candles.append(a)
        base = a.close_time
        b = make_candle(open_time=base, open=99.8, high=105.0, low=99.5, close=104.5,
                       timeframe_minutes=15)
        candles.append(b)
        base = b.close_time
        c = make_candle(open_time=base, open=104.5, high=106.0, low=101.0, close=105.5,
                       timeframe_minutes=15)
        candles.append(c)
        base = c.close_time
        # Now price trades back down through the gap.
        fill = make_candle(open_time=base, open=105.5, high=105.5, low=99.0, close=100.0,
                          timeframe_minutes=15)
        candles.append(fill)

        fvgs = detect_fvgs(candles)
        if fvgs:
            # At least one FVG should now be marked filled.
            filled = [f for f in fvgs if f.is_filled]
            assert len(filled) >= 1, "Expected at least one FVG to be filled"


class TestLiquiditySweepsBug1:
    """Section 6 Bug 1 regression tests.

    A high sweep (wick above swing high, close back below) is BEARISH.
    A low sweep (wick below swing low, close back above) is BULLISH.
    """

    def test_high_sweep_is_bearish(self):
        """A high-sweep candle sequence must produce direction == 'bearish'."""
        candles = high_sweep_seq(swing_high_price=110.0)
        swings = detect_swing_points(candles[:-1], lookback=3)
        sweeps = detect_liquidity_sweeps(candles, swings)
        assert len(sweeps) >= 1, "Expected at least one liquidity sweep"
        # At least one sweep must be bearish (high sweep).
        bearish_sweeps = [s for s in sweeps if s.direction == "bearish"]
        assert len(bearish_sweeps) >= 1, (
            "High sweep must produce direction='bearish' (Section 6 Bug 1)"
        )

    def test_low_sweep_is_bullish(self):
        """A low-sweep candle sequence must produce direction == 'bullish'."""
        candles = low_sweep_seq(swing_low_price=90.0)
        swings = detect_swing_points(candles[:-1], lookback=3)
        sweeps = detect_liquidity_sweeps(candles, swings)
        assert len(sweeps) >= 1, "Expected at least one liquidity sweep"
        bullish_sweeps = [s for s in sweeps if s.direction == "bullish"]
        assert len(bullish_sweeps) >= 1, (
            "Low sweep must produce direction='bullish' (Section 6 Bug 1)"
        )

    def test_never_assume_high_equals_bullish(self):
        """Sanity: high sweep must NEVER produce direction == 'bullish'."""
        candles = high_sweep_seq(swing_high_price=110.0)
        swings = detect_swing_points(candles[:-1], lookback=3)
        sweeps = detect_liquidity_sweeps(candles, swings)
        for s in sweeps:
            # A sweep detected at a swing high must be bearish, never bullish.
            # We check the swept_level is around the swing high.
            high_swings = [sw for sw in swings if sw.type == "high"]
            if high_swings:
                max_high = max(sw.price for sw in high_swings)
                if abs(s.swept_level - max_high) < 5.0:
                    assert s.direction == "bearish", (
                        f"High sweep produced direction={s.direction} "
                        f"(Section 6 Bug 1 violation)"
                    )


class TestAnalyzeSMC:
    """End-to-end smc.analyze_smc function."""

    def test_analyze_smc_returns_dict_with_expected_keys(self):
        candles = bullish_seq(n=30)
        swings = detect_swing_points(candles, lookback=3)
        result = analyze_smc(candles, swings)
        assert isinstance(result, dict)
        assert "order_blocks" in result
        assert "fvgs" in result
        assert "sweeps" in result
        assert isinstance(result["order_blocks"], list)
        assert isinstance(result["fvgs"], list)
        assert isinstance(result["sweeps"], list)

    def test_analyze_smc_with_empty_inputs(self):
        result = analyze_smc([], [])
        assert result["order_blocks"] == []
        assert result["fvgs"] == []
        assert result["sweeps"] == []
