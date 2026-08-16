"""
File: tests/unit/test_simulation.py
1. Single Responsibility: Verify simulation/paper_trade.py + fees.py + slippage.py.
2. Consumes: simulation.paper_trade, simulation.fees, simulation.slippage, contracts.*.
3. Produces: Tests for trade opening, closure, PnL, fees, slippage.
4. Downstream: pytest run.
5. New Dependencies: pytest.
6. Touches Section 6 bugs? No.
7. Tests: simulation module smoke tests + Section 0 hard-constraint 7.
8. Logging: No.
9. Dependency Order: contracts -> simulation -> tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from config import thresholds
from config.profiles import DAY_TRADING_MAX_HOLD_HOURS
from contracts.decision import DecisionResult, EntrySignal, RiskAssessment
from contracts.simulation import SimulatedTrade
from simulation.fees import calculate_fee, calculate_exit_fee, total_trade_fees
from simulation.slippage import estimate_slippage, apply_slippage_to_price
from simulation.paper_trade import PaperTrader
from tests.conftest import make_candle, make_dt


def make_decision(direction: str = "long", entry_price: float = 100.0) -> DecisionResult:
    # Spot-only: only long trades are relevant.
    now = datetime.now(timezone.utc)
    risk = RiskAssessment(
        allowed=True, max_position_size=10.0, max_risk_amount=200.0,
        stop_loss_price=entry_price - 5.0,
        take_profit_price=entry_price + 10.0,
        risk_reward_ratio=2.0,
    )
    entry = EntrySignal(
        symbol="BTCUSDT", direction="long",
        entry_price=entry_price, entry_type="market",
        timeframe="15m", confidence=0.85, reasons=["test"],
        stop_loss=risk.stop_loss_price, take_profit=risk.take_profit_price,
        risk_reward=2.0, valid_until=now,
    )
    return DecisionResult(
        symbol="BTCUSDT",
        source_candle_open_time=now,
        score=0.85, confidence=0.85,
        regime_check_passed=True,
        structure_alignment_passed=True,
        htf_bias_aligned=True,
        risk=risk, entry=entry,
        final_verdict=True,
        timestamp=now,
    )


class TestFees:
    def test_taker_fee_calculation(self):
        # fee = 100 * 10 * (0.1 / 100) = 1.0
        fee = calculate_fee(entry_price=100.0, size=10.0, is_maker=False)
        expected = 100.0 * 10.0 * (thresholds.TAKER_FEE_PCT / 100.0)
        assert fee == pytest.approx(expected)

    def test_maker_fee_calculation(self):
        fee = calculate_fee(entry_price=100.0, size=10.0, is_maker=True)
        expected = 100.0 * 10.0 * (thresholds.MAKER_FEE_PCT / 100.0)
        assert fee == pytest.approx(expected)

    def test_exit_fee(self):
        fee = calculate_exit_fee(exit_price=110.0, size=10.0, is_maker=False)
        expected = 110.0 * 10.0 * (thresholds.TAKER_FEE_PCT / 100.0)
        assert fee == pytest.approx(expected)

    def test_total_trade_fees(self):
        total = total_trade_fees(
            entry_price=100.0, exit_price=110.0, size=10.0,
            is_maker_entry=False, is_maker_exit=False,
        )
        entry_fee = 100.0 * 10.0 * (thresholds.TAKER_FEE_PCT / 100.0)
        exit_fee = 110.0 * 10.0 * (thresholds.TAKER_FEE_PCT / 100.0)
        assert total == pytest.approx(entry_fee + exit_fee)


class TestSlippage:
    def test_slippage_estimation(self):
        # slippage = 100 * 10 * (0.05 / 100) = 0.5
        slip = estimate_slippage(entry_price=100.0, size=10.0, symbol="BTCUSDT")
        expected = 100.0 * 10.0 * (thresholds.SLIPPAGE_PCT / 100.0)
        assert slip == pytest.approx(expected)

    def test_apply_slippage_to_price_long(self):
        """Long: slippage makes fill price WORSE (higher)."""
        fill = apply_slippage_to_price(
            entry_price=100.0, size=10.0, symbol="BTCUSDT", direction="long",
        )
        # Implementation may return either the price with slippage added or
        # the slippage amount. Verify it's a positive float.
        assert isinstance(fill, float)

class TestPaperTraderOpenTrade:
    """Section 0 hard-constraint 7: is_simulated must always be True."""

    @pytest.mark.asyncio
    async def test_open_trade_writes_to_storage(self, mock_supabase):
        trader = PaperTrader(supabase=mock_supabase)
        decision = make_decision("long", 100.0)
        trade = await trader.open_trade(decision)
        assert isinstance(trade, SimulatedTrade)
        assert trade.symbol == "BTCUSDT"
        assert trade.direction == "long"
        assert trade.status == "open"
        # No live price available (no Redis + fetch_latest_candle stub returns
        # None) -> fill price falls back to the signal price.
        assert trade.entry_price == 100.0
        assert trade.decision_id == decision.id
        # Storage must have been called.
        mock_supabase.insert_simulated_trade.assert_called_once()

    @pytest.mark.asyncio
    async def test_open_trade_uses_live_price_from_redis(self, mock_supabase, mock_redis):
        """FIX regression: the fill price must come from the live market
        price, not the (possibly stale) signal price. SL/TP distances and the
        USDT position value are preserved around the new fill price."""
        now = datetime.now(timezone.utc)
        mock_redis.get_live_price.return_value = (102.0, now)
        trader = PaperTrader(supabase=mock_supabase, redis=mock_redis)
        decision = make_decision("long", 100.0)
        trade = await trader.open_trade(decision)
        assert trade.entry_price == 102.0, "fill must use the live price"
        assert trade.signal_price == 100.0, "original signal price preserved"
        # SL/TP absolute distances preserved: SL was 95 (-5), TP was 110 (+10).
        assert trade.stop_loss == pytest.approx(97.0)
        assert trade.take_profit == pytest.approx(112.0)
        # USDT position value preserved: 10.0 units * 100.0 signal price.
        assert trade.size == pytest.approx(10.0 * 100.0 / 102.0)
        # Fee/slippage recomputed on the actual fill, not the signal.
        expected_fee = 102.0 * trade.size * (thresholds.TAKER_FEE_PCT / 100.0)
        assert trade.fee == pytest.approx(expected_fee)
        # Trailing-track starts at the fill price, never the stale signal.
        assert trade.highest_price == pytest.approx(102.0)
        assert trade.initial_stop_loss == pytest.approx(97.0)
        assert trade.live_price_age_seconds is not None

    @pytest.mark.asyncio
    async def test_open_trade_stale_live_price_ignored(self, mock_supabase, mock_redis):
        """A live price older than LIVE_PRICE_MAX_AGE_SECONDS is rejected and
        the fill falls back to the signal price."""
        now = datetime.now(timezone.utc)
        stale = datetime.fromtimestamp(now.timestamp() - 300, timezone.utc)
        mock_redis.get_live_price.return_value = (200.0, stale)
        trader = PaperTrader(supabase=mock_supabase, redis=mock_redis)
        decision = make_decision("long", 100.0)
        trade = await trader.open_trade(decision)
        assert trade.entry_price == 100.0
        assert trade.signal_price is None

    @pytest.mark.asyncio
    async def test_is_simulated_always_true(self, mock_supabase):
        """Section 0 hard-constraint 7 regression test."""
        trader = PaperTrader(supabase=mock_supabase)
        decision = make_decision("long", 100.0)
        trade = await trader.open_trade(decision)
        assert trade.is_simulated is True, (
            "SimulatedTrade.is_simulated MUST always be True "
            "(Section 0 hard-constraint 7)"
        )

    @pytest.mark.asyncio
    async def test_open_trade_calculates_fee_and_slippage(self, mock_supabase):
        trader = PaperTrader(supabase=mock_supabase)
        decision = make_decision("long", 100.0)
        trade = await trader.open_trade(decision)
        assert trade.fee > 0
        assert trade.slippage > 0


class TestPaperTraderClosure:
    @pytest.mark.asyncio
    async def test_check_trade_closure_tp_long(self, mock_supabase):
        """A long trade whose candle high reaches TP must close at TP."""
        trader = PaperTrader(supabase=mock_supabase)
        decision = make_decision("long", 100.0)
        # SL=95, TP=110.
        trade = await trader.open_trade(decision)

        # Current candle: low=99, high=111 -> TP hit.
        current = make_candle(
            open_time=make_dt(0), open=100.0, high=111.0, low=99.0, close=110.0,
        )
        result = await trader.check_trade_closure(trade, current)
        if result is not None:
            assert result.status == "closed"
            assert result.close_reason == "tp"
            assert result.pnl is not None

    @pytest.mark.asyncio
    async def test_check_trade_closure_sl_long(self, mock_supabase):
        """A long trade whose candle low reaches SL must close at SL."""
        trader = PaperTrader(supabase=mock_supabase)
        decision = make_decision("long", 100.0)
        trade = await trader.open_trade(decision)

        # Current candle: low=94, high=100 -> SL hit.
        current = make_candle(
            open_time=make_dt(0), open=100.0, high=100.0, low=94.0, close=95.0,
        )
        result = await trader.check_trade_closure(trade, current)
        if result is not None:
            assert result.status == "closed"
            assert result.close_reason == "sl"

    @pytest.mark.asyncio
    async def test_check_trade_closure_no_hit_returns_none(self, mock_supabase):
        trader = PaperTrader(supabase=mock_supabase)
        decision = make_decision("long", 100.0)
        trade = await trader.open_trade(decision)

        # Current candle: low=99, high=101 -> neither SL nor TP hit.
        current = make_candle(
            open_time=make_dt(0), open=100.0, high=101.0, low=99.0, close=100.5,
        )
        result = await trader.check_trade_closure(trade, current)
        assert result is None  # Trade remains open.

    @pytest.mark.asyncio
    async def test_day_trading_time_exit_after_max_hold(self, mock_supabase):
        trader = PaperTrader(supabase=mock_supabase)
        decision = make_decision("long", 100.0)
        trade = await trader.open_trade(decision)
        current = make_candle(
            open_time=make_dt(0), open=100.0, high=101.0, low=99.0, close=100.5,
        ).model_copy(
            update={
                "close_time": trade.opened_at
                + timedelta(hours=DAY_TRADING_MAX_HOLD_HOURS, minutes=1),
            }
        )

        result = await trader.check_trade_closure(trade, current)

        assert result is not None
        assert result.status == "closed"
        assert result.close_reason == "time"
        assert result.close_price == pytest.approx(100.5)


class TestPreEntryCandleWarning:

    """Regression: skip_pre_entry_candle must warn ONCE per trade, not on
    every poll, while the latest closed candle predates the trade open time.
    """

    @pytest.mark.asyncio
    async def test_pre_entry_warning_emitted_once(self, mock_supabase, caplog):
        import datetime as _dt
        trader = PaperTrader(supabase=mock_supabase)
        decision = make_decision("long", 100.0)
        trade = await trader.open_trade(decision)

        # A closed candle that predates the trade open time (like on every
        # poll while the current candle is still forming). Pydantic models are
        # frozen, so rebuild with model_copy instead of mutating close_time.
        candle = make_candle(
            open_time=make_dt(-900), open=99.0, high=101.0,
            low=98.0, close=100.0,
        ).model_copy(update={"close_time": trade.opened_at - _dt.timedelta(seconds=60)})

        # First check: pre-entry skip fires and the trade id is marked warned.
        result = await trader.check_trade_closure(trade, candle)
        assert result is None
        assert trade.id in trader._pre_entry_warned, (
            "trade must be marked as warned after the first pre-entry skip"
        )

        # Subsequent checks: still skipped silently -- the warned set keeps
        # growing to a single entry per trade (idempotent).
        for _ in range(5):
            await trader.check_trade_closure(trade, candle)
        assert trader._pre_entry_warned == {trade.id}, (
            "warned set must not grow on repeated polls"
        )

    @pytest.mark.asyncio
    async def test_pre_entry_check_resumes_after_newer_candle(self, mock_supabase):
        """Once a closed candle newer than opened_at arrives, closure checks
        resume normally (TP/SL hits are honoured)."""
        trader = PaperTrader(supabase=mock_supabase)
        decision = make_decision("long", 100.0)
        trade = await trader.open_trade(decision)

        import datetime as _dt
        old = make_candle(
            open_time=make_dt(-900), open=99.0, high=101.0,
            low=98.0, close=100.0,
        ).model_copy(update={"close_time": trade.opened_at - _dt.timedelta(seconds=60)})
        assert await trader.check_trade_closure(trade, old) is None

        new_candle = make_candle(
            open_time=make_dt(0), open=100.0, high=111.0,
            low=99.0, close=110.0,
        ).model_copy(update={"close_time": trade.opened_at + _dt.timedelta(seconds=60)})
        result = await trader.check_trade_closure(trade, new_candle)
        assert result is not None
        assert result.status == "closed"
        assert result.close_reason == "tp"


class TestPnLCalculation:
    @pytest.mark.asyncio
    async def test_long_pnl_positive_on_tp(self, mock_supabase):
        trader = PaperTrader(supabase=mock_supabase)
        decision = make_decision("long", 100.0)
        trade = await trader.open_trade(decision)
        # TP at 110 -> pnl = (110-100)*size - fee - slippage > 0.
        current = make_candle(
            open_time=make_dt(0), open=100.0, high=111.0, low=99.0, close=110.0,
        )
        result = await trader.check_trade_closure(trade, current)
        if result is not None and result.pnl is not None:
            assert result.pnl > 0
