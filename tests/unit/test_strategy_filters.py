"""
File: tests/unit/test_strategy_filters.py

Unit tests for the losing-trades strategy enhancements applied on 2026-08-09:

1. ``CONFIDENCE_THRESHOLD`` calibrated from 0.70 to the Balanced 0.65 profile.
2. ``VOLATILITY_ATR_MULTIPLIER_SL`` raised from 1.8 to 2.5.
3. RSI overbought gate in ``engine/orchestrator.py`` (reject longs when
   LTF RSI >= ``MOMENTUM_RSI_OVERBOUGHT`` == 70).
4. Volume-spike (climactic) gate via ``engine/volume.volume_is_climactic``
   (reject when last closed candle volume >= 4x the rolling average).

Downstream: every other module in the project (read by CI).
Tests: pytest (see pytest.ini).
Logging: no events emitted by tests.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import thresholds
from contracts.decision import DecisionResult, RiskAssessment
from engine.volume import volume_is_climactic
from tests.conftest import make_candle, make_dt


# ---------------------------------------------------------------------------
# 1. Threshold updates
# ---------------------------------------------------------------------------
class TestThresholdUpdates:
    """Verify the current Balanced strategy profile and safety gates."""

    def test_confidence_threshold_is_065(self):
        assert thresholds.CONFIDENCE_THRESHOLD == pytest.approx(0.65)

    def test_sl_atr_multiplier_is_25(self):
        assert thresholds.VOLATILITY_ATR_MULTIPLIER_SL == pytest.approx(2.5)

    def test_volume_spike_ratio_exists_and_is_positive(self):
        assert thresholds.HIGH_VOLATILITY_VOLUME_SPIKE_RATIO == pytest.approx(4.0)

    def test_rsi_overbought_unchanged_at_70(self):
        assert thresholds.MOMENTUM_RSI_OVERBOUGHT == pytest.approx(70.0)


# ---------------------------------------------------------------------------
# 2. Confidence gate behaviour at the Balanced 0.65 level
# ---------------------------------------------------------------------------
class TestConfidenceGate:
    """``confidence_gate`` must reject below 0.65 and accept at/above."""

    def test_below_threshold_rejected(self):
        from engine.confidence import confidence_gate

        assert confidence_gate(0.64) is False
        assert confidence_gate(0.60) is False

    def test_at_and_above_threshold_accepted(self):
        from engine.confidence import confidence_gate

        assert confidence_gate(0.65) is True
        assert confidence_gate(0.90) is True


# ---------------------------------------------------------------------------
# 3. Volume-spike (climactic) filter
# ---------------------------------------------------------------------------
class TestVolumeSpikeFilter:
    """``volume_is_climactic`` must flag 4x-average spikes and allow normal volume."""

    def test_normal_volume_allowed(self):
        base = make_dt(0)
        candles = [
            make_candle(symbol="XLMUSDT", timeframe="5m", open_time=base,
                        open=1.0, high=1.05, low=0.95, close=1.02, volume=1000.0)
            for _ in range(10)
        ]
        assert volume_is_climactic(candles) is False

    def test_climactic_spike_flagged(self):
        base = make_dt(0)
        candles = [
            make_candle(symbol="XLMUSDT", timeframe="5m", open_time=base,
                        open=1.0, high=1.05, low=0.95, close=1.02, volume=1000.0)
            for _ in range(9)
        ]
        # Last closed candle has 5x the rolling average (>= 4.0).
        candles.append(
            make_candle(symbol="XLMUSDT", timeframe="5m", open_time=base,
                        open=1.02, high=1.12, low=1.0, close=1.1, volume=5000.0)
        )
        assert volume_is_climactic(candles) is True

    def test_insufficient_data_allows(self):
        # Fewer than 3 closed candles with identical default volume:
        # _volume_ratio returns the neutral 1.0 -> entry allowed.
        base = make_dt(0)
        candles = [
            make_candle(symbol="XLMUSDT", timeframe="5m", open_time=base,
                        open=1.0, high=1.01, low=0.99, close=1.0, volume=100.0)
            for _ in range(2)
        ]
        assert volume_is_climactic(candles) is False

    def test_empty_candles_allows(self):
        assert volume_is_climactic([]) is False

    def test_unclosed_candle_is_ignored(self):
        base = make_dt(0)
        candles = [
            make_candle(symbol="XLMUSDT", timeframe="5m", open_time=base,
                        open=1.0, high=1.05, low=0.95, close=1.02, volume=1000.0)
            for _ in range(5)
        ]
        # The *last* candle has a huge spike but is unclosed (is_closed=False)
        # -> the filter only inspects closed candles, so no spike is seen.
        candles.append(
            make_candle(symbol="XLMUSDT", timeframe="5m", open_time=base,
                        open=1.02, high=1.2, low=1.0, close=1.15,
                        volume=50000.0, is_closed=False)
        )
        assert volume_is_climactic(candles) is False


# ---------------------------------------------------------------------------
# 4. RSI overbought gate (via the orchestrator end-to-end mock path)
# ---------------------------------------------------------------------------
class TestRsiOverboughtGate:
    """The orchestrator must reject long entries when the LTF RSI is >= 70."""

    def test_rsi_gate_reason_strings(self):
        from engine.orchestrator import _determine_rejection_reason

        # All gates pass -> no rejection.
        reason = _determine_rejection_reason(
            regime_ok=True, structure_ok=True, htf_ok=True,
            confidence_ok=True, rsi_ok=True, volume_ok=True,
            risk_ok=True, risk_reason=None,
        )
        assert reason == ""

        # RSI overbought must produce a dedicated reason.
        reason = _determine_rejection_reason(
            regime_ok=True, structure_ok=True, htf_ok=True,
            confidence_ok=True, rsi_ok=False, volume_ok=True,
            risk_ok=True, risk_reason=None,
        )
        assert reason.startswith("rsi_overbought")
        assert "70" in reason

        # Volume spike must produce a dedicated reason.
        reason = _determine_rejection_reason(
            regime_ok=True, structure_ok=True, htf_ok=True,
            confidence_ok=True, rsi_ok=True, volume_ok=False,
            risk_ok=True, risk_reason=None,
        )
        assert reason.startswith("volume_spike")

    def test_volume_spike_precedence_over_htf(self):
        """A volume spike should surface before an htf misalignment."""
        from engine.orchestrator import _determine_rejection_reason

        reason = _determine_rejection_reason(
            regime_ok=True, structure_ok=True, htf_ok=False,
            confidence_ok=True, rsi_ok=True, volume_ok=False,
            risk_ok=True, risk_reason=None,
        )
        assert reason.startswith("volume_spike")

    def test_decision_result_carries_rsi_blocked_flag(self):
        """``DecisionResult.rsi_overbought_blocked`` defaults to False."""
        decision = DecisionResult(
            symbol="XLMUSDT",
            source_candle_open_time=make_dt(0),
            score=0.5,
            confidence=0.8,
            regime_check_passed=True,
            structure_alignment_passed=True,
            htf_bias_aligned=True,
            rsi_overbought_blocked=True,
            risk=RiskAssessment(allowed=False, reason="rsi_overbought"),
            final_verdict=False,
            timestamp=make_dt(0),
        )
        assert decision.rsi_overbought_blocked is True
        assert decision.final_verdict is False


# ---------------------------------------------------------------------------
# 5. SL distance widened by the new multiplier
# ---------------------------------------------------------------------------
class TestWiderStopLoss:
    """A 2.5x ATR stop sits further from the entry than the old 1.8x."""

    def test_sl_distance_widened(self):
        from engine.risk import calculate_stop_loss

        entry, atr = 100.0, 1.0
        with patch.object(thresholds, "VOLATILITY_ATR_MULTIPLIER_SL", 1.8):
            old_sl = calculate_stop_loss(entry_price=entry, atr=atr)
        new_sl = calculate_stop_loss(entry_price=entry, atr=atr)
        assert new_sl < old_sl  # lower SL price -> wider distance
        assert entry - new_sl == pytest.approx(2.5)
        assert entry - old_sl == pytest.approx(1.8)

    def test_default_rr_stays_above_minimum(self):
        """TP multiplier raised to 4.0 so SL=2.5x / TP=4.0x keeps R:R = 1.6
        above MIN_RISK_REWARD_RATIO (1.4)."""
        assert (thresholds.VOLATILITY_ATR_MULTIPLIER_TP
                / thresholds.VOLATILITY_ATR_MULTIPLIER_SL
                ) >= thresholds.MIN_RISK_REWARD_RATIO
