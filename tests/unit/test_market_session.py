"""
File: tests/unit/test_market_session.py
1. Single Responsibility: Verify market/session.py against Section 10 acceptance criteria.
2. Consumes: market.session.
3. Produces: Tests for Asian/London/NY/overlap session classification.
4. Downstream: pytest run.
5. New Dependencies: pytest.
6. Touches Section 6 bugs? No.
7. Tests: Section 10 market/session.py tests 1-4.
8. Logging: No.
9. Dependency Order: contracts -> market -> tests.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from market.session import get_current_session, is_overlap, session_quality_score, get_session_bounds


class TestSessionClassification:
    """Section 10 market/session.py tests 1-4."""

    def test_asian_session_at_03_utc(self):
        ts = datetime(2024, 1, 1, 3, 0, 0, tzinfo=timezone.utc)
        assert get_current_session(ts) == "asian"

    def test_london_session_at_10_utc(self):
        ts = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        assert get_current_session(ts) == "london"

    def test_ny_session_at_18_utc(self):
        ts = datetime(2024, 1, 1, 18, 0, 0, tzinfo=timezone.utc)
        assert get_current_session(ts) == "ny"

    def test_overlap_at_14_utc(self):
        ts = datetime(2024, 1, 1, 14, 0, 0, tzinfo=timezone.utc)
        assert get_current_session(ts) == "overlap"

    def test_overlap_takes_priority_over_london_and_ny(self):
        """Hours 13-16 UTC are London+NY overlap, must return 'overlap'."""
        for hour in (13, 14, 15):
            ts = datetime(2024, 1, 1, hour, 0, 0, tzinfo=timezone.utc)
            assert get_current_session(ts) == "overlap", f"hour={hour} should be overlap"

    def test_asian_session_at_00_utc(self):
        ts = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert get_current_session(ts) == "asian"

    def test_ny_session_at_20_utc(self):
        ts = datetime(2024, 1, 1, 20, 0, 0, tzinfo=timezone.utc)
        assert get_current_session(ts) == "ny"

    def test_london_session_at_08_utc(self):
        ts = datetime(2024, 1, 1, 8, 0, 0, tzinfo=timezone.utc)
        assert get_current_session(ts) == "london"


class TestIsOverlap:
    def test_is_overlap_true_during_overlap(self):
        ts = datetime(2024, 1, 1, 14, 0, 0, tzinfo=timezone.utc)
        assert is_overlap(ts) is True

    def test_is_overlap_false_outside_overlap(self):
        ts = datetime(2024, 1, 1, 18, 0, 0, tzinfo=timezone.utc)
        assert is_overlap(ts) is False


class TestSessionQualityScore:
    def test_quality_score_returns_float_in_range(self):
        for session in ("asian", "london", "ny", "overlap"):
            score = session_quality_score(session, "BTCUSDT")
            assert 0.0 <= score <= 1.0, f"session={session} score={score}"

    def test_overlap_score_for_btc_is_high(self):
        score = session_quality_score("overlap", "BTCUSDT")
        assert score >= 0.5

    def test_asian_score_for_btc_is_lower_than_overlap(self):
        asian = session_quality_score("asian", "BTCUSDT")
        overlap = session_quality_score("overlap", "BTCUSDT")
        assert asian <= overlap


class TestGetSessionBounds:
    def test_asian_bounds(self):
        start, end = get_session_bounds("asian")
        assert start == 0
        assert end == 8

    def test_london_bounds(self):
        start, end = get_session_bounds("london")
        assert start == 8
        assert end == 16

    def test_ny_bounds(self):
        start, end = get_session_bounds("ny")
        assert start == 13
        assert end == 21
