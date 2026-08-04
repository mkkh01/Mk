"""
File: tests/unit/test_volume.py
1. Single Responsibility: Verify engine/volume.py against Section 6 Bug 2 (CVD must use
   taker_buy_volume/taker_sell_volume, NOT candle color).
2. Consumes: engine.volume, contracts.market.
3. Produces: Regression tests for Bug 2.
4. Downstream: pytest run.
5. New Dependencies: pytest.
6. Touches Section 6 bugs? YES -- Bug 2 (CVD source).
7. Tests: Section 10 engine/structure.py test 4 (CVD accuracy).
8. Logging: No.
9. Dependency Order: contracts -> engine -> tests.
"""

from __future__ import annotations

import pytest

from engine.volume import analyze_volume, calculate_cvd
from tests.conftest import make_candle, make_dt


class TestCVDAccuracyBug2:
    """Section 6 Bug 2 regression tests.

    CVD must use taker_buy_volume - taker_sell_volume. It must NOT use
    candle color (green/red) as a proxy.
    """

    def test_cvd_matches_hand_computed_value(self):
        """CVD output must match a hand-computed value from taker volumes."""
        base = make_dt(0)
        c1 = make_candle(open_time=base, open=100.0, high=101.0, low=99.0, close=100.5,
                        volume=100.0, taker_buy_volume=60.0, taker_sell_volume=40.0)
        c2 = make_candle(open_time=base.replace(minute=base.minute + 15) if base.minute < 45 else base,
                        open=100.5, high=101.0, low=100.0, close=100.8,
                        volume=80.0, taker_buy_volume=30.0, taker_sell_volume=50.0)
        c3 = make_candle(open_time=base.replace(minute=base.minute + 30) if base.minute < 30 else base,
                        open=100.8, high=101.5, low=100.5, close=101.2,
                        volume=120.0, taker_buy_volume=70.0, taker_sell_volume=50.0)
        candles = [c1, c2, c3]
        cvd = calculate_cvd(candles)
        # Expected: [60-40, (60-40)+(30-50), (60-40)+(30-50)+(70-50)]
        #         = [20, 0, 20]
        assert cvd[0] == pytest.approx(20.0)
        assert cvd[1] == pytest.approx(0.0)
        assert cvd[2] == pytest.approx(20.0)

    def test_cvd_differs_from_candle_color_calculation(self):
        """A deliberately contradictory fixture: a GREEN candle with dominant
        taker SELL volume. CVD must DECREASE (not increase) on this candle."""
        base = make_dt(0)
        # Green candle (close > open) but taker_sell_volume > taker_buy_volume.
        contradictory = make_candle(
            open_time=base, open=100.0, high=101.0, low=99.0, close=100.5,
            volume=100.0, taker_buy_volume=30.0, taker_sell_volume=70.0,
        )
        cvd = calculate_cvd([contradictory])
        # CVD delta = 30 - 70 = -40 (must DECREASE).
        assert cvd[0] == pytest.approx(-40.0), (
            "CVD must use taker volumes, not candle color (Section 6 Bug 2). "
            "A green candle with dominant sell volume must DECREASE CVD."
        )

    def test_cvd_with_bearish_candle_dominant_buy(self):
        """Symmetric: a RED candle with dominant taker BUY volume must INCREASE CVD."""
        base = make_dt(0)
        contradictory = make_candle(
            open_time=base, open=101.0, high=101.5, low=99.5, close=100.5,
            volume=100.0, taker_buy_volume=70.0, taker_sell_volume=30.0,
        )
        cvd = calculate_cvd([contradictory])
        assert cvd[0] == pytest.approx(40.0), (
            "CVD must use taker volumes, not candle color (Section 6 Bug 2). "
            "A red candle with dominant buy volume must INCREASE CVD."
        )


class TestAnalyzeVolume:
    def test_returns_dict_with_expected_keys(self):
        base = make_dt(0)
        candles = []
        for i in range(30):
            candles.append(make_candle(
                open_time=base, open=100.0 + i * 0.1, high=101.0 + i * 0.1,
                low=99.0 + i * 0.1, close=100.5 + i * 0.1,
                volume=100.0, taker_buy_volume=55.0, taker_sell_volume=45.0,
                timeframe_minutes=15,
            ))
            base = candles[-1].close_time
        result = analyze_volume(candles)
        assert isinstance(result, dict)
        # Expected keys per spec §15.
        for key in ("cvd", "cvd_slope", "volume_ratio", "poc"):
            assert key in result, f"Missing key: {key}"

    def test_empty_candles_does_not_crash(self):
        result = analyze_volume([])
        assert isinstance(result, dict)
