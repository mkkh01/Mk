from __future__ import annotations

import csv
import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import config.thresholds as thresholds
from contracts.config import CoinConfig
from contracts.decision import DecisionResult
from config.thresholds import ENTRY_TIMEOUT_MINUTES
from contracts.market import Candle
from engine.orchestrator import Orchestrator
from monitoring.health_manager import health_manager


@dataclass
class ReplayTradeOutcome:
    symbol: str
    decision_time: str
    entry_price: float
    stop_loss: float
    take_profit: float
    filled: bool
    outcome: str
    exit_price: float | None = None
    exit_time: str | None = None
    fill_time: str | None = None
    r_multiple: float | None = None
    ambiguous_bar: bool = False


@dataclass
class ReplayReport:
    source: str
    start: str
    end: str
    trigger_timeframe: str
    symbols: list[str]
    profile: str = "default"
    decisions: int = 0
    signals_found: int = 0
    approved: int = 0
    rejected: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    signal_quality_passed: int = 0
    signal_quality_failure_reasons: dict[str, int] = field(default_factory=dict)
    pre_timing_eligible: int = 0
    pre_timing_block_reasons: dict[str, int] = field(default_factory=dict)
    entry_timing_checked: int = 0
    entry_timing_passed: int = 0
    timing_rejection_reasons: dict[str, int] = field(default_factory=dict)
    db_writes: int = 0
    db_write_failures: int = 0
    outcomes: list[ReplayTradeOutcome] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)


class ReplayStorage:
    """In-memory storage facade with a strict candle as-of cutoff.

    The facade implements only the methods the orchestrator and its risk path
    need. It never writes to Supabase and never exposes a candle whose close
    time is after the current replay cutoff.
    """

    def __init__(self, candles: dict[tuple[str, str], list[Candle]]) -> None:
        self.candles = {
            key: sorted(values, key=lambda candle: candle.open_time)
            for key, values in candles.items()
        }
        self.current_cutoff: datetime | None = None
        self.decisions: list[DecisionResult] = []

    async def fetch_closed_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 200,
        since: datetime | None = None,
    ) -> list[Candle]:
        values = self.candles.get((symbol, timeframe), [])
        cutoff = self.current_cutoff
        visible = [
            candle
            for candle in values
            if candle.is_closed
            and (cutoff is None or candle.close_time <= cutoff)
            and (since is None or candle.open_time > since)
        ]
        return visible[-limit:]

    async def fetch_latest_candle(self, symbol: str, timeframe: str) -> Candle | None:
        candles = await self.fetch_closed_candles(symbol, timeframe, limit=1)
        return candles[-1] if candles else None

    async def upsert_decision(self, decision: DecisionResult) -> bool:
        self.decisions.append(decision)
        return True

    async def count_open_trades(self) -> int:
        return 0

    async def fetch_open_trades(self, symbol: str | None = None) -> list[Any]:
        return []

    async def insert_simulated_trade(self, trade: Any) -> bool:
        return True

    async def update_simulated_trade_closure(self, **_: Any) -> None:
        return None


class ReplayRedis:
    """Minimal Redis facade for deterministic replay."""

    def __init__(self, storage: ReplayStorage) -> None:
        self.storage = storage

    async def get_live_price(self, symbol: str) -> float | None:
        candidates = [
            candle
            for (candle_symbol, _), candles in self.storage.candles.items()
            if candle_symbol == symbol
            for candle in candles
            if self.storage.current_cutoff is None
            or candle.close_time <= self.storage.current_cutoff
        ]
        return candidates[-1].close if candidates else None


class ReplayRunner:
    def __init__(
        self,
        candles: dict[tuple[str, str], list[Candle]],
        *,
        symbols: list[str],
        trigger_timeframe: str = "15m",
        start: datetime | None = None,
        end: datetime | None = None,
        capital: float = 10_000.0,
        risk_percent: float = 2.0,
        profile: str = "default",
    ) -> None:
        self.storage = ReplayStorage(candles)
        self.redis = ReplayRedis(self.storage)
        self.orchestrator = Orchestrator(self.storage, self.redis)
        self.symbols = symbols
        self.trigger_timeframe = trigger_timeframe
        self.start = start
        self.end = end
        self.capital = capital
        self.risk_percent = risk_percent
        if profile not in {"default", "1to1"}:
            raise ValueError(f"unsupported replay profile: {profile!r}")
        self.profile = profile

    @contextmanager
    def _profile_context(self) -> Iterator[None]:
        """Apply replay-only risk overrides and always restore production values."""
        if self.profile == "default":
            yield
            return

        original_tp = thresholds.VOLATILITY_ATR_MULTIPLIER_TP
        original_min_rr = thresholds.MIN_RISK_REWARD_RATIO
        try:
            thresholds.VOLATILITY_ATR_MULTIPLIER_TP = (
                thresholds.REPLAY_1TO1_ATR_MULTIPLIER_TP
            )
            thresholds.MIN_RISK_REWARD_RATIO = thresholds.REPLAY_1TO1_MIN_RR
            yield
        finally:
            thresholds.VOLATILITY_ATR_MULTIPLIER_TP = original_tp
            thresholds.MIN_RISK_REWARD_RATIO = original_min_rr

    async def run(self) -> ReplayReport:
        before = await health_manager.get_stats()
        triggers = self._trigger_candles()
        report = ReplayReport(
            source="Binance Data Vision spot monthly klines",
            start=(self.start or triggers[0].close_time).isoformat() if triggers else "",
            end=(self.end or triggers[-1].close_time).isoformat() if triggers else "",
            trigger_timeframe=self.trigger_timeframe,
            symbols=self.symbols,
            profile=self.profile,
        )

        with self._profile_context():
            for trigger in triggers:
                self.storage.current_cutoff = trigger.close_time
                coin = CoinConfig(
                    symbol=trigger.symbol,
                    timeframes=[self.trigger_timeframe, "1h", "4h"],
                    capital=self.capital,
                    risk_percent=self.risk_percent,
                )
                result = await self.orchestrator.process_candle_safe(trigger, coin)
                if result is None:
                    continue
                report.decisions += 1
                report.signals_found += len(result.component_signals)
                if result.final_verdict:
                    report.approved += 1
                else:
                    report.rejected += 1
                    reason = result.rejection_reason or "unknown"
                    report.rejection_reasons[reason] = report.rejection_reasons.get(reason, 0) + 1

        after = await health_manager.get_stats()
        report.signal_quality_passed = after.get("signal_quality_passed", 0) - before.get("signal_quality_passed", 0)
        report.pre_timing_eligible = after.get("pre_timing_eligible", 0) - before.get("pre_timing_eligible", 0)
        report.entry_timing_checked = after.get("entry_timing_checked", 0) - before.get("entry_timing_checked", 0)
        report.entry_timing_passed = after.get("entry_timing_passed", 0) - before.get("entry_timing_passed", 0)
        report.db_writes = after.get("db_writes", 0) - before.get("db_writes", 0)
        report.db_write_failures = after.get("db_write_failures", 0) - before.get("db_write_failures", 0)
        report.signal_quality_failure_reasons = _counter_delta(
            before.get("signal_quality_failure_reasons", {}),
            after.get("signal_quality_failure_reasons", {}),
        )
        report.pre_timing_block_reasons = _counter_delta(
            before.get("pre_timing_block_reasons", {}),
            after.get("pre_timing_block_reasons", {}),
        )
        report.timing_rejection_reasons = _counter_delta(
            before.get("timing_rejection_reasons", {}),
            after.get("timing_rejection_reasons", {}),
        )
        report.outcomes = self._evaluate_approved_decisions()
        return report

    def _trigger_candles(self) -> list[Candle]:
        values: list[Candle] = []
        for symbol in self.symbols:
            values.extend(
                candle
                for candle in self.storage.candles.get((symbol, self.trigger_timeframe), [])
                if candle.is_closed
                and (self.start is None or candle.close_time >= self.start)
                and (self.end is None or candle.close_time <= self.end)
            )
        return sorted(values, key=lambda candle: (candle.close_time, candle.symbol))

    def _evaluate_approved_decisions(self) -> list[ReplayTradeOutcome]:
        outcomes: list[ReplayTradeOutcome] = []
        for decision in self.storage.decisions:
            if not decision.final_verdict or decision.entry is None:
                continue
            outcomes.append(self._evaluate_decision(decision))
        return outcomes

    def _evaluate_decision(self, decision: DecisionResult) -> ReplayTradeOutcome:
        entry = decision.entry
        future = [
            candle
            for candle in self.storage.candles.get((decision.symbol, self.trigger_timeframe), [])
            if candle.is_closed and candle.open_time > decision.source_candle_open_time
        ]
        # ``EntrySignal.valid_until`` is created with wall-clock time by the
        # live entry path. In replay that would make a July decision valid until
        # the current runtime date, allowing fills days later. Re-anchor the
        # timeout to the historical decision candle instead.
        source_candle = next(
            (
                candle
                for candle in self.storage.candles.get(
                    (decision.symbol, self.trigger_timeframe), []
                )
                if candle.open_time == decision.source_candle_open_time
            ),
            None,
        )
        decision_close_time = (
            source_candle.close_time
            if source_candle is not None
            else decision.source_candle_open_time
        )
        if decision_close_time.tzinfo is None:
            decision_close_time = decision_close_time.replace(tzinfo=timezone.utc)
        valid_until = decision_close_time + timedelta(minutes=ENTRY_TIMEOUT_MINUTES)
        fill_candle = next(
            (
                candle
                for candle in future
                if candle.open_time < valid_until and candle.low <= entry.entry_price
            ),
            None,
        )
        if fill_candle is None:
            return ReplayTradeOutcome(
                symbol=decision.symbol,
                decision_time=decision.source_candle_open_time.isoformat(),
                entry_price=entry.entry_price,
                stop_loss=entry.stop_loss,
                take_profit=entry.take_profit,
                filled=False,
                outcome="no_fill",
                fill_time=None,
            )

        risk_per_unit = max(entry.entry_price - entry.stop_loss, 0.0)
        for candle in future:
            if candle.close_time <= fill_candle.close_time:
                continue
            hit_stop = candle.low <= entry.stop_loss
            hit_target = candle.high >= entry.take_profit
            if hit_stop or hit_target:
                ambiguous = hit_stop and hit_target
                hit_target_first = hit_target and not hit_stop
                outcome = "tp" if hit_target_first else "sl"
                exit_price = entry.take_profit if hit_target_first else entry.stop_loss
                r_multiple = (
                    (exit_price - entry.entry_price) / risk_per_unit
                    if risk_per_unit > 0
                    else None
                )
                return ReplayTradeOutcome(
                    symbol=decision.symbol,
                    decision_time=decision.source_candle_open_time.isoformat(),
                    entry_price=entry.entry_price,
                    stop_loss=entry.stop_loss,
                    take_profit=entry.take_profit,
                    filled=True,
                    outcome=outcome,
                    exit_price=exit_price,
                    exit_time=candle.close_time.isoformat(),
                    fill_time=fill_candle.close_time.isoformat(),
                    r_multiple=r_multiple,
                    ambiguous_bar=ambiguous,
                )
        return ReplayTradeOutcome(
            symbol=decision.symbol,
            decision_time=decision.source_candle_open_time.isoformat(),
            entry_price=entry.entry_price,
            stop_loss=entry.stop_loss,
            take_profit=entry.take_profit,
            filled=True,
            outcome="open_at_end",
            fill_time=fill_candle.close_time.isoformat(),
        )


def _counter_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    keys = set(before) | set(after)
    return {key: after.get(key, 0) - before.get(key, 0) for key in sorted(keys) if after.get(key, 0) - before.get(key, 0)}


def _parse_binance_timestamp(value: str) -> datetime:
    timestamp = int(value)
    # Binance Data Vision may use milliseconds or microseconds depending on
    # the dataset generation period. Detect the unit from its magnitude.
    divisor = 1_000_000 if timestamp >= 10**15 else 1_000
    return datetime.fromtimestamp(timestamp / divisor, tz=timezone.utc)


def load_candles(directory: Path, symbols: Iterable[str], timeframes: Iterable[str]) -> dict[tuple[str, str], list[Candle]]:
    loaded: dict[tuple[str, str], list[Candle]] = {}
    for symbol in symbols:
        for timeframe in timeframes:
            path = directory / f"{symbol}_{timeframe}.csv"
            if not path.exists():
                raise FileNotFoundError(path)
            rows: list[Candle] = []
            with path.open(newline="", encoding="utf-8") as stream:
                for row in csv.DictReader(stream):
                    rows.append(
                        Candle(
                            symbol=row["symbol"],
                            timeframe=row["timeframe"],
                            open_time=_parse_binance_timestamp(row["open_time_ms"]),
                            close_time=_parse_binance_timestamp(row["close_time_ms"]),
                            open=float(row["open"]),
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=float(row["close"]),
                            volume=float(row["volume"]),
                            taker_buy_volume=float(row["taker_buy_volume"]),
                            taker_sell_volume=float(row["taker_sell_volume"]),
                            is_closed=row["is_closed"].lower() == "true",
                        )
                    )
            loaded[(symbol, timeframe)] = rows
    return loaded
