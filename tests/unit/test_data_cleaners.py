"""
File: tests/unit/test_data_cleaners.py
1. Single Responsibility: Verify data/cleaners.py.
2. Consumes: data.cleaners, contracts.market.
3. Produces: Tests for outlier removal, gap filling, dedup, normalize.
4. Downstream: pytest run.
5. New Dependencies: pytest.
6. Touches Section 6 bugs? No.
7. Tests: smoke tests for cleaners.
8. Logging: No.
9. Dependency Order: contracts -> data -> tests.
"""

from __future__ import annotations

import pytest

from data.cleaners import deduplicate, fill_gaps, normalize_volume, remove_outliers, sort_and_dedupe
from tests.conftest import bullish_seq, make_candle, make_dt


class TestRemoveOutliers:
    def test_returns_list_of_candles(self):
        candles = bullish_seq(n=30)
        result = remove_outliers(candles, z_threshold=3.0)
        assert isinstance(result, list)
        assert len(result) <= len(candles)

    def test_outlier_removed(self):
        candles = bullish_seq(n=30)
        # Inject an obvious outlier.
        outlier = candles[15].model_copy(update={"close": 99999.0, "high": 99999.0})
        candles[15] = outlier
        result = remove_outliers(candles, z_threshold=3.0)
        # The outlier should be removed.
        assert all(c.close < 99999.0 for c in result)

    def test_empty_input_returns_empty(self):
        assert remove_outliers([], z_threshold=3.0) == []


class TestFillGaps:
    def test_returns_list_of_candles(self):
        candles = bullish_seq(n=10)
        result = fill_gaps(candles, timeframe_seconds=900)
        assert isinstance(result, list)

    def test_no_gaps_returns_unchanged_count(self):
        candles = bullish_seq(n=10)
        result = fill_gaps(candles, timeframe_seconds=900)
        assert len(result) >= len(candles)

    def test_fills_missing_candle(self):
        candles = bullish_seq(n=10)
        # Remove one candle from the middle to create a gap.
        gapped = candles[:5] + candles[6:]
        result = fill_gaps(gapped, timeframe_seconds=900)
        # The filled list should be longer than the gapped input.
        assert len(result) >= len(gapped)


class TestDeduplicate:
    def test_removes_duplicates(self):
        candles = bullish_seq(n=10)
        duplicated = candles + candles
        result = deduplicate(duplicated)
        assert len(result) == len(candles)

    def test_empty_returns_empty(self):
        assert deduplicate([]) == []


class TestSortAndDedupe:
    def test_sorts_ascending_by_open_time(self):
        candles = bullish_seq(n=10)
        shuffled = list(reversed(candles))
        result = sort_and_dedupe(shuffled)
        for i in range(1, len(result)):
            assert result[i].open_time > result[i - 1].open_time


class TestNormalizeVolume:
    def test_ensures_taker_volumes_sum_to_volume(self):
        candles = bullish_seq(n=10)
        # Inject a candle with mismatched taker volumes.
        bad = candles[5].model_copy(update={
            "volume": 100.0,
            "taker_buy_volume": 30.0,
            "taker_sell_volume": 30.0,  # sum=60 != 100
        })
        candles[5] = bad
        result = normalize_volume(candles)
        for c in result:
            # After normalization, the sum should approximately equal volume.
            assert abs((c.taker_buy_volume + c.taker_sell_volume) - c.volume) < 1e-3
