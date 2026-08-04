"""
File: portfolio/performance.py
1. Single Responsibility: Compute aggregate performance metrics (win rate,
   PnL, drawdown, Sharpe-like ratio, profit factor, consecutive runs) from
   closed ``SimulatedTrade`` rows, and persist snapshots for trend analysis.
2. Consumes: ``SimulatedTrade`` (contracts/simulation.py),
   ``PerformanceMetrics``, ``TradeSummary`` (contracts/portfolio.py),
   ``SupabaseClient`` (storage/supabase.py).  Uses numpy for stats.
3. Produces: ``PerformanceMetrics`` and ``TradeSummary`` instances, plus
   persisted rows in the ``performance_snapshots`` table.
4. Downstream: bot/telegram_bot.py (System Performance + Trade History
   buttons), app/main.py (snapshot writer on a periodic tick), portfolio
   future modules (attribution, equity curve, reporting).
5. New Dependencies: numpy (already in requirements.txt).
6. Touches Section 6 bugs? No.  Section 0 hard-constraint 7 is enforced:
   every metrics object is built from ``SimulatedTrade`` rows (which carry
   ``is_simulated=True``) and every log event carries ``is_simulated=True``.
   No metric is ever labelled "live" or "executed".
7. Tests: tests/unit/test_portfolio.py exercises:
       * empty trade list -> PerformanceMetrics with all zeros (total_trades=0,
         win_rate=0.0, sharpe_ratio=None, profit_factor=None)
       * all-winning trades -> profit_factor=None (no losses), max_drawdown=0
       * all-losing trades -> win_rate=0.0, profit_factor=0.0
       * single trade -> sharpe_ratio=None (std needs >= 2 samples)
       * win_rate fraction in [0,1] (contract enforces it)
       * max_drawdown: monotonically increasing equity curve -> 0 drawdown
       * max_drawdown: peak then trough -> correct peak-to-trough distance
       * max_drawdown_percent: zero-peak case (all losses from 0) -> 0.0
       * consecutive_wins / consecutive_losses longest-run counting
       * threshold-sensitivity: changing SLIPPAGE_PCT / TAKER_FEE_PCT changes
         the underlying trade PnLs and therefore the aggregate metrics
       * calculate_for_all_symbols returns one entry per distinct symbol
       * save_snapshot round-trips through supabase.save_performance_snapshot
       * get_trade_summaries maps SimulatedTrade -> TradeSummary correctly.
8. Logging: ``performance_calculated`` (info) with
   {symbol?, period_start?, period_end?, total_trades, win_rate, total_pnl,
   max_drawdown, is_simulated}, ``performance_snapshot_saved`` (info) with
   {period_start, period_end, total_trades, is_simulated},
   ``performance_calculation_failed`` (error) on unexpected exceptions.
9. Dependency Order: config -> contracts/* -> monitoring/logger.py ->
   storage/supabase.py -> portfolio/performance.py (no upstream violations;
   performance.py is a leaf of the portfolio package).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import numpy as np

from contracts.portfolio import PerformanceMetrics, TradeSummary
from contracts.simulation import SimulatedTrade
from monitoring.logger import get_logger
from storage.supabase import SupabaseClient

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SHARPE_MIN_SAMPLES = 2
"""Minimum number of trades required for a meaningful sample standard
deviation.  Below this, ``sharpe_ratio`` is set to ``None`` (per Section 22
graceful degradation -- do not divide by zero / NaN)."""

_DDRAW_PCT_ZERO_PEAK_SENTINEL = 0.0
"""Value returned for ``max_drawdown_percent`` when the running balance never
rose above 0 (e.g. all trades are losses from the start).  The percentage is
mathematically undefined (division by zero peak); we return 0.0 rather than
NaN/inf to keep the contract's float field finite (Section 22 Engine Level)."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _utcnow() -> datetime:
    """Timezone-aware UTC ``datetime`` (Render-safe)."""
    return datetime.now(timezone.utc)


def _safe_float(value) -> float:
    """Coerce to finite float; NaN/inf/non-numeric -> 0.0 (Section 22)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not np.isfinite(f):
        return 0.0
    return f


def _empty_metrics(
    period_start: Optional[datetime] = None,
    period_end: Optional[datetime] = None,
) -> PerformanceMetrics:
    """Return a zero-valued ``PerformanceMetrics`` for the empty-trade case.

    Per the task spec: "Handle empty trade list: return PerformanceMetrics
    with all zeros (and total_trades=0, win_rate=0)".
    """
    return PerformanceMetrics(
        period_start=period_start,
        period_end=period_end,
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        win_rate=0.0,
        total_pnl=0.0,
        average_pnl=0.0,
        max_drawdown=0.0,
        max_drawdown_percent=0.0,
        sharpe_ratio=None,
        profit_factor=None,
        average_win=0.0,
        average_loss=0.0,
        largest_win=0.0,
        largest_loss=0.0,
        consecutive_wins=0,
        consecutive_losses=0,
    )


def _calculate_max_drawdown(pnls: list[float]) -> tuple[float, float]:
    """Compute the maximum peak-to-trough drawdown of the cumulative PnL.

    The cumulative PnL series is treated as an equity curve starting at 0.
    The function walks the series, tracking the running peak and computing
    the largest drop from any historical peak to a subsequent trough.

    Args:
        pnls: Per-trade PnL values **in chronological order** (sorted by
            ``closed_at``).  The caller is responsible for sorting.

    Returns:
        ``(max_dd_abs, max_dd_pct)`` where:

        * ``max_dd_abs`` is the absolute drawdown in quote currency
          (always >= 0).  ``0.0`` if the list is empty or the equity curve
          never declines.
        * ``max_dd_pct`` is the drawdown as a percentage of the peak
          (``0..100``).  ``0.0`` if the peak never rose above 0 (e.g. all
          losses from the start) -- see :data:`_DDRAW_PCT_ZERO_PEAK_SENTINEL`.

    Notes:
        * An empty list returns ``(0.0, 0.0)``.
        * A monotonically increasing equity curve returns ``(0.0, 0.0)``.
        * A curve that only ever declines returns
          ``(abs(final_cumulative), 0.0)`` -- the absolute drawdown is the
          full decline, but the percentage is undefined (peak = 0).
    """
    if not pnls:
        return 0.0, 0.0

    # Defensive: filter out any non-finite values to avoid poisoning the
    # cumulative sum (Section 22 NaN must not propagate).
    clean = [_safe_float(p) for p in pnls]
    if not clean:
        return 0.0, 0.0

    # Prepend the initial balance (0) so a curve that only declines still
    # measures the full drop from the starting point.  Without this, a series
    # like [-5, -10, -3] would have running_peak = [-5, -5, -5] and report a
    # drawdown of 13 instead of the correct 18 (from 0 down to -18).
    cum = np.concatenate(
        ([0.0], np.cumsum(np.asarray(clean, dtype=np.float64)))
    )
    # Running peak up to and including index i (starts at the initial 0).
    running_peak = np.maximum.accumulate(cum)
    # Drawdown from peak at each index (always <= 0 when below peak).
    drawdowns = cum - running_peak  # negative or zero
    max_dd_abs = float(-drawdowns.min()) if drawdowns.size > 0 else 0.0
    if max_dd_abs < 0.0:
        max_dd_abs = 0.0

    # Percentage: relative to the peak that produced the max drawdown.
    # Find the index of the worst drawdown and use the corresponding peak.
    if max_dd_abs <= 0.0:
        return 0.0, 0.0

    worst_idx = int(np.argmin(drawdowns))
    peak_at_worst = float(running_peak[worst_idx])
    if peak_at_worst > 0.0:
        max_dd_pct = (max_dd_abs / peak_at_worst) * 100.0
    else:
        # Peak never rose above 0 (equity curve only declined from the
        # starting balance).  The percentage is mathematically undefined;
        # return the sentinel so the contract's float field stays finite.
        max_dd_pct = _DDRAW_PCT_ZERO_PEAK_SENTINEL

    if not np.isfinite(max_dd_pct):
        max_dd_pct = 0.0
    return max_dd_abs, max_dd_pct


def _calculate_consecutive(pnls: list[float]) -> tuple[int, int]:
    """Return ``(max_consecutive_wins, max_consecutive_losses)``.

    A "win" is ``pnl > 0``; a "loss" is ``pnl <= 0`` (per Section 19, which
    defines ``losing_trades = count WHERE pnl <= 0``).  Zero-pnl trades count
    as losses for both the count and the consecutive calculation, keeping
    the two consistent.

    Args:
        pnls: Per-trade PnL values **in chronological order**.

    Returns:
        ``(max_wins, max_losses)`` -- the longest run of consecutive wins
        and the longest run of consecutive losses.  Either may be 0 if no
        such run exists.
    """
    if not pnls:
        return 0, 0

    max_wins = 0
    max_losses = 0
    cur_wins = 0
    cur_losses = 0
    for p in pnls:
        f = _safe_float(p)
        if f > 0.0:
            cur_wins += 1
            cur_losses = 0
            if cur_wins > max_wins:
                max_wins = cur_wins
        else:
            cur_losses += 1
            cur_wins = 0
            if cur_losses > max_losses:
                max_losses = cur_losses
    return max_wins, max_losses


def _summarise(
    trades: list[SimulatedTrade],
    period_start: Optional[datetime] = None,
    period_end: Optional[datetime] = None,
) -> PerformanceMetrics:
    """Compute a ``PerformanceMetrics`` from an in-memory list of closed trades.

    Shared by :meth:`PerformanceCalculator.calculate_metrics` and
    :meth:`PerformanceCalculator.calculate_for_all_symbols` so they produce
    byte-identical results for the same input.

    Args:
        trades: Closed ``SimulatedTrade`` rows.  Order does not matter -- the
            function sorts by ``closed_at`` for the drawdown / consecutive
            calculations.
        period_start: Optional period-start to record on the metrics.  If
            ``None``, the min ``closed_at`` of the trades is used (or ``None``
            if the list is empty).
        period_end: Optional period-end to record on the metrics.  If
            ``None``, the max ``closed_at`` of the trades is used (or ``None``
            if the list is empty).

    Returns:
        A populated ``PerformanceMetrics``.  Empty input returns a zero-
        valued metrics per :func:`_empty_metrics`.
    """
    if not trades:
        return _empty_metrics(period_start=period_start, period_end=period_end)

    # Sort by closed_at ascending for drawdown + consecutive calculations.
    # Trades with closed_at=None (shouldn't happen for closed trades, but
    # defend against it) sort to the front via the key fallback.
    def _sort_key(t: SimulatedTrade) -> datetime:
        # Treat None as the epoch so they sort first (defensive).
        return t.closed_at or datetime.min.replace(tzinfo=timezone.utc)

    sorted_trades = sorted(trades, key=_sort_key)
    pnls = [_safe_float(t.pnl) for t in sorted_trades]
    pnl_arr = np.asarray(pnls, dtype=np.float64)

    total_trades = len(sorted_trades)
    winning = [p for p in pnls if p > 0.0]
    losing = [p for p in pnls if p <= 0.0]
    winning_trades = len(winning)
    losing_trades = len(losing)

    win_rate = (
        winning_trades / total_trades if total_trades > 0 else 0.0
    )
    # Defensive: contract enforces [0, 1].  Float rounding could in principle
    # push this microscopically above 1.0 on huge inputs; clamp.
    if win_rate > 1.0:
        win_rate = 1.0
    if win_rate < 0.0:
        win_rate = 0.0

    total_pnl = float(pnl_arr.sum()) if pnl_arr.size > 0 else 0.0
    if not np.isfinite(total_pnl):
        total_pnl = 0.0
    average_pnl = (
        float(total_pnl / total_trades) if total_trades > 0 else 0.0
    )
    if not np.isfinite(average_pnl):
        average_pnl = 0.0

    average_win = (
        float(np.asarray(winning, dtype=np.float64).mean()) if winning else 0.0
    )
    if not np.isfinite(average_win):
        average_win = 0.0
    # Loss average is reported as a negative number (preserving the sign of
    # the underlying pnls).  This matches the bot template's "Losing Trades"
    # semantics and the average_loss contract field.
    average_loss = (
        float(np.asarray(losing, dtype=np.float64).mean()) if losing else 0.0
    )
    if not np.isfinite(average_loss):
        average_loss = 0.0

    largest_win = float(max(pnls)) if pnls else 0.0
    largest_loss = float(min(pnls)) if pnls else 0.0

    # Drawdown: walk the cumulative PnL series in chronological order.
    max_dd_abs, max_dd_pct = _calculate_max_drawdown(pnls)

    # Sharpe-like ratio: avg_pnl / std(pnl).  Use sample std (ddof=1) which
    # requires >= 2 samples; otherwise None (Section 22 graceful degradation).
    sharpe_ratio: Optional[float] = None
    if pnl_arr.size >= _SHARPE_MIN_SAMPLES:
        std_dev = float(pnl_arr.std(ddof=1))
        if std_dev > 0.0 and np.isfinite(std_dev):
            sharpe_ratio = float(average_pnl / std_dev)
            if not np.isfinite(sharpe_ratio):
                sharpe_ratio = None

    # Profit factor: gross_profit / abs(gross_loss).  If gross_loss == 0
    # (no losing trades), the ratio is undefined -- return None rather than
    # inf so the contract's Optional[float] stays clean.
    gross_profit = float(sum(p for p in pnls if p > 0.0))
    gross_loss_raw = float(sum(p for p in pnls if p < 0.0))  # negative
    gross_loss_abs = abs(gross_loss_raw)
    profit_factor: Optional[float] = None
    if gross_loss_abs > 0.0:
        profit_factor = float(gross_profit / gross_loss_abs)
        if not np.isfinite(profit_factor):
            profit_factor = None
    elif gross_profit > 0.0:
        # All winning trades, no losses: convention is to leave as None
        # (the ratio is unbounded).  Some shops report ``float('inf')``;
        # we use ``None`` for JSON-safety per the contract.
        profit_factor = None

    consecutive_wins, consecutive_losses = _calculate_consecutive(pnls)

    # Period attribution: prefer caller-provided bounds; fall back to actual
    # trade range for the missing side.
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    closed_ats = [t.closed_at for t in sorted_trades if t.closed_at is not None]
    if closed_ats:
        actual_start = min(closed_ats)
        actual_end = max(closed_ats)
    resolved_start = period_start if period_start is not None else actual_start
    resolved_end = period_end if period_end is not None else actual_end

    return PerformanceMetrics(
        period_start=resolved_start,
        period_end=resolved_end,
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate=win_rate,
        total_pnl=total_pnl,
        average_pnl=average_pnl,
        max_drawdown=max_dd_abs,
        max_drawdown_percent=max_dd_pct,
        sharpe_ratio=sharpe_ratio,
        profit_factor=profit_factor,
        average_win=average_win,
        average_loss=average_loss,
        largest_win=largest_win,
        largest_loss=largest_loss,
        consecutive_wins=consecutive_wins,
        consecutive_losses=consecutive_losses,
    )


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------
class PerformanceCalculator:
    """Compute aggregate performance metrics from simulated trades.

    The calculator is a *read-only* consumer of the ``simulated_trades``
    table; it does not modify trade rows.  Its only write path is
    :meth:`save_snapshot`, which inserts a row into the
    ``performance_snapshots`` table for trend analysis.

    Every method that produces a ``PerformanceMetrics`` also emits a
    ``performance_calculated`` log event carrying ``is_simulated=True`` so
    downstream consumers (and operators reading the log stream) can never
    confuse simulated metrics with live results.
    """

    def __init__(self, supabase: SupabaseClient) -> None:
        """Construct a PerformanceCalculator.

        Args:
            supabase: An *already-connected* ``SupabaseClient``.  The
                calculator does not call ``connect()`` itself.
        """
        self._supabase = supabase

    # ----------------------- core calculation ------------------------------
    async def calculate_metrics(
        self,
        symbol: Optional[str] = None,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
    ) -> PerformanceMetrics:
        """Compute :class:`PerformanceMetrics` over a (possibly filtered) set
        of closed simulated trades.

        Algorithm (Section 19):

        1. Query ``simulated_trades`` filtered by ``status='closed'`` and
           optionally by symbol / closed_at range.
        2. Count total / winning (pnl>0) / losing (pnl<=0) trades.
        3. Sum / average PnL; compute average win, average loss, largest win,
           largest loss.
        4. Compute max drawdown by walking the cumulative-PnL equity curve
           (sorted by ``closed_at``) and finding the largest peak-to-trough
           decline.
        5. Compute Sharpe-like ratio = avg_pnl / sample_std(pnl) when std>0
           and there are >= 2 trades; otherwise ``None``.
        6. Compute profit factor = gross_profit / abs(gross_loss) when
           gross_loss > 0; otherwise ``None``.
        7. Compute longest consecutive winning and losing runs.
        8. Return the populated :class:`PerformanceMetrics`.

        Args:
            symbol: Optional symbol filter.
            period_start: Optional inclusive lower bound on ``closed_at``.
            period_end: Optional inclusive upper bound on ``closed_at``.

        Returns:
            A :class:`PerformanceMetrics`.  Empty result set returns a zero-
            valued metrics with ``total_trades=0``, ``win_rate=0.0``,
            ``sharpe_ratio=None``, ``profit_factor=None``.
        """
        try:
            trades = await self._supabase.fetch_closed_trades(
                symbol=symbol,
                period_start=period_start,
                period_end=period_end,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=_utcnow(),
                module="portfolio.performance",
                error_type=type(exc).__name__,
                error_message=str(exc),
                symbol=symbol,
                period_start=period_start.isoformat() if period_start else None,
                period_end=period_end.isoformat() if period_end else None,
            )
            logger.error(
                "performance_calculation_failed",
                timestamp=_utcnow(),
                symbol=symbol,
                reason="fetch_closed_trades_error",
                is_simulated=True,
            )
            # Graceful degradation (Section 22): return zero metrics rather
            # than crashing the bot's System Performance button.
            return _empty_metrics(period_start=period_start, period_end=period_end)

        metrics = _summarise(trades, period_start=period_start, period_end=period_end)

        logger.info(
            "performance_calculated",
            timestamp=_utcnow(),
            symbol=symbol,
            period_start=(
                metrics.period_start.isoformat() if metrics.period_start else None
            ),
            period_end=(
                metrics.period_end.isoformat() if metrics.period_end else None
            ),
            total_trades=metrics.total_trades,
            winning_trades=metrics.winning_trades,
            losing_trades=metrics.losing_trades,
            win_rate=metrics.win_rate,
            total_pnl=metrics.total_pnl,
            max_drawdown=metrics.max_drawdown,
            max_drawdown_percent=metrics.max_drawdown_percent,
            sharpe_ratio=metrics.sharpe_ratio,
            profit_factor=metrics.profit_factor,
            consecutive_wins=metrics.consecutive_wins,
            consecutive_losses=metrics.consecutive_losses,
            is_simulated=True,  # Section 0 hard-constraint 7.
        )
        return metrics

    # ----------------------- trade summaries -------------------------------
    async def get_trade_summaries(self, limit: int = 10) -> list[TradeSummary]:
        """Return the most recent ``limit`` trades as :class:`TradeSummary`.

        Powers the bot's "Trade History" button (Section 7 / Section 20
        template "Trade History (Last 10)").  The summaries include both open
        and closed trades so the user sees the current open positions at the
        top of the list.

        Args:
            limit: Maximum number of trades to return.  Default 10 (matches
                the Section 20 template "Last 10").

        Returns:
            A list of :class:`TradeSummary` ordered by ``opened_at`` DESC
            (most recent first).  Empty list if no trades exist.
        """
        if limit <= 0:
            return []
        try:
            trades = await self._supabase.fetch_recent_trades(limit=limit)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=_utcnow(),
                module="portfolio.performance",
                error_type=type(exc).__name__,
                error_message=str(exc),
                limit=limit,
            )
            return []

        summaries: list[TradeSummary] = []
        for t in trades:
            summaries.append(
                TradeSummary(
                    symbol=t.symbol,
                    direction=t.direction,
                    entry_price=t.entry_price,
                    stop_loss=t.stop_loss,
                    initial_stop_loss=t.initial_stop_loss,
                    take_profit=t.take_profit,
                    pnl=t.pnl,
                    status=t.status,
                    opened_at=t.opened_at,
                    closed_at=t.closed_at,
                    close_price=t.close_price,
                )
            )
        logger.info(
            "trade_summaries_returned",
            timestamp=_utcnow(),
            count=len(summaries),
            requested_limit=limit,
            is_simulated=True,
        )
        return summaries

    # ----------------------- snapshot persistence --------------------------
    async def save_snapshot(self, metrics: PerformanceMetrics) -> None:
        """Persist a :class:`PerformanceMetrics` snapshot to Postgres.

        The ``performance_snapshots`` table is a time-series of metrics
        snapshots used for trend charts (e.g. win rate over time, drawdown
        over time).  Call this on a periodic tick from ``app/main.py`` (e.g.
        every hour) or after each batch of closures.

        Args:
            metrics: The metrics to persist.
        """
        try:
            await self._supabase.save_performance_snapshot(metrics)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=_utcnow(),
                module="portfolio.performance",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            logger.error(
                "performance_snapshot_save_failed",
                timestamp=_utcnow(),
                total_trades=metrics.total_trades,
                is_simulated=True,
            )
            raise
        logger.info(
            "performance_snapshot_saved",
            timestamp=_utcnow(),
            period_start=(
                metrics.period_start.isoformat() if metrics.period_start else None
            ),
            period_end=(
                metrics.period_end.isoformat() if metrics.period_end else None
            ),
            total_trades=metrics.total_trades,
            total_pnl=metrics.total_pnl,
            win_rate=metrics.win_rate,
            is_simulated=True,
        )

    # ----------------------- per-symbol breakdown --------------------------
    async def calculate_for_all_symbols(
        self,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
    ) -> dict[str, PerformanceMetrics]:
        """Compute :class:`PerformanceMetrics` for every symbol that has
        closed trades in the period.

        This is more efficient than calling :meth:`calculate_metrics` once
        per symbol: it fetches all closed trades for the period in a single
        query and groups them in-memory.

        Args:
            period_start: Optional inclusive lower bound on ``closed_at``.
            period_end: Optional inclusive upper bound on ``closed_at``.

        Returns:
            Dict mapping each distinct symbol to its
            :class:`PerformanceMetrics`.  Symbols with no closed trades in
            the period are absent from the dict (no zero-padded entries --
            the caller can detect "missing symbol" by key absence).
        """
        try:
            trades = await self._supabase.fetch_closed_trades(
                symbol=None,
                period_start=period_start,
                period_end=period_end,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=_utcnow(),
                module="portfolio.performance",
                error_type=type(exc).__name__,
                error_message=str(exc),
                period_start=period_start.isoformat() if period_start else None,
                period_end=period_end.isoformat() if period_end else None,
            )
            return {}

        # Group by symbol, preserving order of first appearance.
        groups: dict[str, list[SimulatedTrade]] = {}
        for t in trades:
            groups.setdefault(t.symbol, []).append(t)

        results: dict[str, PerformanceMetrics] = {}
        for symbol, group_trades in groups.items():
            metrics = _summarise(
                group_trades,
                period_start=period_start,
                period_end=period_end,
            )
            results[symbol] = metrics
            logger.info(
                "performance_calculated",
                timestamp=_utcnow(),
                symbol=symbol,
                period_start=(
                    metrics.period_start.isoformat()
                    if metrics.period_start
                    else None
                ),
                period_end=(
                    metrics.period_end.isoformat()
                    if metrics.period_end
                    else None
                ),
                total_trades=metrics.total_trades,
                winning_trades=metrics.winning_trades,
                losing_trades=metrics.losing_trades,
                win_rate=metrics.win_rate,
                total_pnl=metrics.total_pnl,
                max_drawdown=metrics.max_drawdown,
                max_drawdown_percent=metrics.max_drawdown_percent,
                sharpe_ratio=metrics.sharpe_ratio,
                profit_factor=metrics.profit_factor,
                is_simulated=True,
            )
        logger.info(
            "performance_all_symbols_calculated",
            timestamp=_utcnow(),
            symbol_count=len(results),
            total_trades=sum(m.total_trades for m in results.values()),
            is_simulated=True,
        )
        return results

    async def get_daily_performance(self) -> PerformanceMetrics:
        """Compute metrics for the last 24 hours."""
        now = _utcnow()
        yesterday = now - timedelta(days=1)
        return await self.calculate_metrics(period_start=yesterday, period_end=now)

    async def get_per_coin_performance(self, period_start: Optional[datetime] = None, period_end: Optional[datetime] = None) -> dict[str, PerformanceMetrics]:
        """Compute metrics for each coin separately."""
        return await self.calculate_for_all_symbols(period_start=period_start, period_end=period_end)

    # ----------------------- convenience -----------------------------------
    async def calculate_and_save(
        self,
        symbol: Optional[str] = None,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
    ) -> PerformanceMetrics:
        """Compute metrics and immediately persist a snapshot.

        Convenience wrapper for the periodic-tick path in ``app/main.py``:
        a single call that fetches, computes, logs, and persists.

        Args:
            symbol: Optional symbol filter.
            period_start: Optional inclusive lower bound on ``closed_at``.
            period_end: Optional inclusive upper bound on ``closed_at``.

        Returns:
            The computed (and now persisted) :class:`PerformanceMetrics`.
        """
        metrics = await self.calculate_metrics(
            symbol=symbol,
            period_start=period_start,
            period_end=period_end,
        )
        # Save best-effort: a snapshot failure must not block the caller.
        try:
            await self.save_snapshot(metrics)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "performance_calculate_and_save_snapshot_failed",
                timestamp=_utcnow(),
                error_type=type(exc).__name__,
                error_message=str(exc),
                is_simulated=True,
            )
        return metrics


__all__ = [
    "PerformanceCalculator",
    "_calculate_max_drawdown",
    "_calculate_consecutive",
]
