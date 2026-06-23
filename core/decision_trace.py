"""
Decision Trace — تسلسل تتبع كامل لكل دورة تداول.
يوثق كل قرار وكل رفض وكل درجة ← شفافية كاملة.
"""
import time
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

from core.reason_codes import ReasonCode, Rejection, passed, describe

logger = logging.getLogger("decision_trace")


# ═══════════════════════════════════════════════════════════════
# Score Component — تحليل مكونات الثقة
# ═══════════════════════════════════════════════════════════════

@dataclass
class ScoreComponent:
    """مكوّن درجة واحد — مع اسم وقيمة."""
    name: str
    value: float
    weight: float = 1.0

    @property
    def contribution(self) -> float:
        return self.value * self.weight


@dataclass
class ScoreBreakdown:
    """تحليل كامل لدرجة الثقة — كل مكون مع قيمته."""
    components: List[ScoreComponent] = field(default_factory=list)
    penalty: float = 0.0
    penalty_reason: str = ""

    @property
    def total(self) -> float:
        raw = sum(c.contribution for c in self.components)
        return max(0, min(100, raw - self.penalty))

    def add(self, name: str, value: float, weight: float = 1.0) -> None:
        self.components.append(ScoreComponent(name=name, value=value, weight=weight))

    def apply_penalty(self, amount: float, reason: str) -> None:
        self.penalty += amount
        self.penalty_reason = reason

    def format(self) -> str:
        lines = []
        for c in self.components:
            sign = "+" if c.value >= 0 else ""
            lines.append(f"  {c.name:.<20s} {sign}{c.value:+.1f}")
        if self.penalty:
            lines.append(f"  {'Penalty':.<20s} {self.penalty_reason:<15s} -{self.penalty:.1f}")
        lines.append(f"  {'TOTAL':.<20s} {'=':>3s} {self.total:.1f}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Engine Timing — زمن كل محرك
# ═══════════════════════════════════════════════════════════════

@dataclass
class EngineTiming:
    """زمن محرك واحد."""
    name: str
    elapsed_ms: float = 0.0

    def __str__(self) -> str:
        return f"{self.name}: {self.elapsed_ms:.1f}ms"


# ═══════════════════════════════════════════════════════════════
# Strategy Result — نتيجة تقييم استراتيجية واحدة
# ═══════════════════════════════════════════════════════════════

@dataclass
class StrategyTrace:
    """تتبع استراتيجية واحدة."""
    name: str
    passed: bool = False
    confidence: float = 0.0
    decision: str = "NONE"       # BUY / SELL / HOLD / NONE
    rejection: Optional[Rejection] = None
    execution_time_ms: float = 0.0
    details: str = ""


# ═══════════════════════════════════════════════════════════════
# Market Snapshot — لقطة سوق
# ═══════════════════════════════════════════════════════════════

@dataclass
class MarketSnapshotTrace:
    """لقطة سوق لعملة واحدة."""
    symbol: str = ""
    regime: str = ""
    trend_direction: str = ""
    trend_strength: float = 0.0
    momentum: float = 0.0
    volatility: float = 0.0
    liquidity: float = 0.0
    spread: float = 0.0
    volume: float = 0.0
    noise: float = 0.0
    confidence: float = 0.0


# ═══════════════════════════════════════════════════════════════
# Cycle Counters — عدادت دورية
# ═══════════════════════════════════════════════════════════════

@dataclass
class CycleCounters:
    """عدادات تراكمية للدورات."""
    total_cycles: int = 0
    total_candidates: int = 0       # صفقات مرشحة
    total_rejected: int = 0         # صفقات مرفوضة
    total_executed: int = 0         # صفقات منفذة
    reject_by_code: Dict[str, int] = field(default_factory=dict)
    strategy_passes: Dict[str, int] = field(default_factory=dict)
    strategy_fails: Dict[str, int] = field(default_factory=dict)
    total_confidence: float = 0.0
    total_cycle_time: float = 0.0
    total_analyzer_time: float = 0.0
    total_strategies_time: float = 0.0
    total_evidence_time: float = 0.0
    total_risk_time: float = 0.0
    total_execution_time: float = 0.0
    total_db_time: float = 0.0
    total_telegram_time: float = 0.0
    last_report_cycle: int = 0
    last_hourly_report_ts: float = 0.0

    @property
    def avg_confidence(self) -> float:
        return self.total_confidence / max(1, self.total_cycles)

    @property
    def avg_cycle_time(self) -> float:
        return self.total_cycle_time / max(1, self.total_cycles)

    def top_rejects(self, n: int = 10) -> List[tuple[str, int]]:
        return sorted(self.reject_by_code.items(), key=lambda x: x[1], reverse=True)[:n]


# ═══════════════════════════════════════════════════════════════
# Decision Trace — تتبع كامل لدورة واحدة
# ═══════════════════════════════════════════════════════════════

@dataclass
class DecisionTrace:
    """تتبع كامل لقرار تداول دورة واحدة — لكل عملة."""
    cycle: int = 0
    symbol: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    # ── System State ──
    trading_allowed: bool = False
    trading_suspended: bool = False
    system_state: str = ""
    websocket_status: str = ""
    warmup_status: str = ""
    active_symbols: List[str] = field(default_factory=list)
    active_strategies: List[str] = field(default_factory=list)

    # ── Market ──
    market: MarketSnapshotTrace = field(default_factory=MarketSnapshotTrace)

    # ── Strategies ──
    strategy_results: List[StrategyTrace] = field(default_factory=list)

    # ── Evidence ──
    strategy_votes_buy: int = 0
    strategy_votes_sell: int = 0
    strategy_votes_hold: int = 0
    consensus: str = ""
    evidence_score: float = 0.0
    confidence_score: float = 0.0
    direction: str = ""
    evidence_rejection: Optional[Rejection] = None
    score_breakdown: Optional[ScoreBreakdown] = None

    # ── Risk ──
    risk_allowed: bool = False
    position_size: float = 0.0
    daily_loss_check: bool = False
    max_consecutive_losses: int = 0
    exposure: float = 0.0
    cooldown: bool = False
    drawdown: float = 0.0
    capital_check: bool = False
    risk_rejection: Optional[Rejection] = None

    # ── Execution ──
    trade_candidate: bool = False
    final_direction: str = ""
    quantity: float = 0.0
    entry: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    risk_reward: float = 0.0
    execution_rejection: Optional[Rejection] = None

    # ── Timing ──
    timings: List[EngineTiming] = field(default_factory=list)
    cycle_start: float = 0.0
    cycle_end: float = 0.0

    # ── Final ──
    trade_executed: bool = False
    final_rejection: Optional[Rejection] = None

    @property
    def total_cycle_ms(self) -> float:
        if self.cycle_start and self.cycle_end:
            return (self.cycle_end - self.cycle_start) * 1000
        return 0.0

    def add_timing(self, name: str, elapsed_ms: float) -> None:
        self.timings.append(EngineTiming(name=name, elapsed_ms=elapsed_ms))

    def format_full(self) -> str:
        """تنسيق كامل — للتتبع التفصيلي."""
        sep = "═" * 50
        lines = [sep, f"CYCLE #{self.cycle} | {self.symbol} | {self.timestamp}"]

        # ── System State ──
        lines.append("")
        lines.append("SYSTEM STATE")
        lines.append(f"  trading_allowed:  {self.trading_allowed}")
        lines.append(f"  trading_suspended:{self.trading_suspended}")
        lines.append(f"  system_state:     {self.system_state}")
        lines.append(f"  websocket_status: {self.websocket_status}")
        lines.append(f"  warmup_status:    {self.warmup_status}")
        lines.append(f"  active_symbols:   {', '.join(self.active_symbols[:5])}{'...' if len(self.active_symbols)>5 else ''}")
        lines.append(f"  active_strategies:{', '.join(self.active_strategies)}")

        # ── Market Snapshot ──
        lines.append("")
        lines.append(f"SYMBOL: {self.symbol} — Market Snapshot")
        lines.append(f"  regime:           {self.market.regime}")
        lines.append(f"  trend_direction:  {self.market.trend_direction}")
        lines.append(f"  trend_strength:   {self.market.trend_strength:.1f}")
        lines.append(f"  momentum:         {self.market.momentum:.1f}")
        lines.append(f"  volatility:       {self.market.volatility:.1f}")
        lines.append(f"  liquidity:        {self.market.liquidity:.1f}")
        lines.append(f"  spread:           {self.market.spread:.4f}")
        lines.append(f"  volume:           {self.market.volume:.0f}")
        lines.append(f"  noise:            {self.market.noise:.1f}")
        lines.append(f"  confidence:       {self.market.confidence:.1f}")

        # ── Strategy Evaluation ──
        for s in self.strategy_results:
            status = "✅ PASS" if s.passed else "❌ FAIL"
            lines.append("")
            lines.append(f"Strategy: {s.name}")
            lines.append(f"  Status:           {status}")
            if s.rejection:
                lines.append(f"  Reason:           [{s.rejection.code.value}] {s.rejection.reason}")
            lines.append(f"  Confidence:       {s.confidence:.1f}")
            lines.append(f"  Decision:         {s.decision}")
            lines.append(f"  Execution Time:   {s.execution_time_ms:.1f}ms")

        # ── Evidence Engine ──
        lines.append("")
        lines.append("Evidence Engine")
        lines.append(f"  Buy Votes:        {self.strategy_votes_buy}")
        lines.append(f"  Sell Votes:       {self.strategy_votes_sell}")
        lines.append(f"  Hold Votes:       {self.strategy_votes_hold}")
        lines.append(f"  Consensus:        {self.consensus}")
        lines.append(f"  Evidence Score:   {self.evidence_score:.1f}")
        lines.append(f"  Confidence Score: {self.confidence_score:.1f}")
        lines.append(f"  Direction:        {self.direction}")
        if self.evidence_rejection and self.evidence_rejection.code != ReasonCode.RC000_PASS:
            lines.append(f"  ❌ REJECTED:       {self.evidence_rejection}")
        if self.score_breakdown:
            lines.append("")
            lines.append("Score Breakdown:")
            lines.append(self.score_breakdown.format())

        # ── Risk Engine ──
        lines.append("")
        lines.append("Risk Engine")
        lines.append(f"  Risk Allowed:     {'✅' if self.risk_allowed else '❌'}")
        lines.append(f"  Position Size:    {self.position_size:.4f}")
        lines.append(f"  Daily Loss:       {'✅' if self.daily_loss_check else '❌'}")
        lines.append(f"  Cons. Losses:     {self.max_consecutive_losses}")
        lines.append(f"  Exposure:         {self.exposure:.1f}%")
        lines.append(f"  Cooldown:         {'🟡 Active' if self.cooldown else '🟢 Clear'}")
        lines.append(f"  Drawdown:         {self.drawdown:.1f}%")
        lines.append(f"  Capital Check:    {'✅' if self.capital_check else '❌'}")
        if self.risk_rejection and self.risk_rejection.code != ReasonCode.RC000_PASS:
            lines.append(f"  ❌ REJECTED:       {self.risk_rejection}")

        # ── Execution Engine ──
        lines.append("")
        lines.append("Execution Engine")
        lines.append(f"  Trade Candidate:  {'✅ YES' if self.trade_candidate else '❌ NO'}")
        lines.append(f"  Final Direction:  {self.final_direction}")
        lines.append(f"  Quantity:         {self.quantity:.4f}")
        lines.append(f"  Entry:            {self.entry:.4f}")
        lines.append(f"  Stop Loss:        {self.stop_loss:.4f}")
        lines.append(f"  Take Profit:      {self.take_profit:.4f}")
        lines.append(f"  Risk/Reward:      {self.risk_reward:.2f}")
        if self.execution_rejection and self.execution_rejection.code != ReasonCode.RC000_PASS:
            lines.append(f"  ❌ REJECTED:       {self.execution_rejection}")

        # ── Timing ──
        lines.append("")
        lines.append("Pipeline Timing:")
        for t in self.timings:
            lines.append(f"  {t}")
        lines.append(f"  {'TOTAL':.<25s} {self.total_cycle_ms:.1f}ms")

        # ── Final Result ──
        lines.append("")
        lines.append("═" * 50)
        if self.trade_executed:
            lines.append("TRADE EXECUTED ✅")
        else:
            reason_text = ""
            if self.final_rejection and self.final_rejection.code != ReasonCode.RC000_PASS:
                reason_text = str(self.final_rejection)
            else:
                # Find the first rejection
                for r in [self.evidence_rejection, self.risk_rejection, self.execution_rejection]:
                    if r and r.code != ReasonCode.RC000_PASS:
                        reason_text = str(r)
                        break
            if reason_text:
                lines.append(f"TRADE REJECTED ❌")
                lines.append(f"Reason: {reason_text}")
            else:
                lines.append("NO TRADE — no signal generated")
        lines.append("═" * 50)

        return "\n".join(lines)

    def format_one_line(self) -> str:
        """تنسيق مختصر — سطر واحد."""
        if self.trade_executed:
            return (f"[CYCLE #{self.cycle}] {self.symbol} ✅ EXECUTED "
                    f"{self.final_direction} x{self.quantity:.4f} @ {self.entry:.4f} "
                    f"SL={self.stop_loss:.4f} TP={self.take_profit:.4f} "
                    f"R:R={self.risk_reward:.2f}")
        else:
            rejection = self.final_rejection
            if not rejection or rejection.code == ReasonCode.RC000_PASS:
                # Find first rejection
                for r in [self.evidence_rejection, self.risk_rejection, self.execution_rejection]:
                    if r and r.code != ReasonCode.RC000_PASS:
                        rejection = r
                        break
            if rejection and rejection.code != ReasonCode.RC000_PASS:
                return (f"[CYCLE #{self.cycle}] {self.symbol} ❌ REJECTED "
                        f"[{rejection.code.value}] {rejection.reason} "
                        f"({rejection.engine}/{rejection.rule})")
            return f"[CYCLE #{self.cycle}] {self.symbol} — NO SIGNAL"


# ═══════════════════════════════════════════════════════════════
# Global Counters
# ═══════════════════════════════════════════════════════════════

_counters = CycleCounters()


def get_counters() -> CycleCounters:
    return _counters


def reset_counters() -> None:
    global _counters
    _counters = CycleCounters()


def update_counters(trace: DecisionTrace) -> None:
    """تحديث العدادت من تتبع دورة."""
    c = _counters
    c.total_cycles += 1
    c.total_cycle_time += trace.total_cycle_ms
    c.total_confidence += trace.confidence_score

    for t in trace.timings:
        if t.name == "Analyzer":
            c.total_analyzer_time += t.elapsed_ms
        elif t.name == "Strategies":
            c.total_strategies_time += t.elapsed_ms
        elif t.name == "Evidence":
            c.total_evidence_time += t.elapsed_ms
        elif t.name == "Risk":
            c.total_risk_time += t.elapsed_ms
        elif t.name == "Execution":
            c.total_execution_time += t.elapsed_ms
        elif t.name == "Database":
            c.total_db_time += t.elapsed_ms
        elif t.name == "Telegram":
            c.total_telegram_time += t.elapsed_ms

    for s in trace.strategy_results:
        if s.passed:
            c.strategy_passes[s.name] = c.strategy_passes.get(s.name, 0) + 1
        else:
            c.strategy_fails[s.name] = c.strategy_fails.get(s.name, 0) + 1

    if trace.trade_candidate:
        c.total_candidates += 1

    if trace.trade_executed:
        c.total_executed += 1

    # Count rejections
    for r in [trace.evidence_rejection, trace.risk_rejection, trace.execution_rejection, trace.final_rejection]:
        if r and r.code != ReasonCode.RC000_PASS:
            code_str = r.code.value
            c.reject_by_code[code_str] = c.reject_by_code.get(code_str, 0) + 1
            break  # نعد سبب واحد رئيسي فقط


def format_5min_report() -> str:
    """تقرير كل 5 دقائق."""
    c = _counters
    lines = [
        "",
        "═" * 50,
        "📊 5-MINUTE COUNTERS REPORT",
        f"  Total Cycles:          {c.total_cycles}",
        f"  Trade Candidates:      {c.total_candidates}",
        f"  Trades Rejected:       {c.total_rejected}",
        f"  Trades Executed:       {c.total_executed}",
        f"  Avg Confidence:        {c.avg_confidence:.1f}",
        f"  Avg Cycle Time:        {c.avg_cycle_time:.1f}ms",
        f"  Avg Analyzer:          {c.total_analyzer_time/max(1,c.total_cycles):.1f}ms",
        f"  Avg Strategies:        {c.total_strategies_time/max(1,c.total_cycles):.1f}ms",
        f"  Avg Evidence:          {c.total_evidence_time/max(1,c.total_cycles):.1f}ms",
        f"  Avg Risk:              {c.total_risk_time/max(1,c.total_cycles):.1f}ms",
        f"  Avg Execution:         {c.total_execution_time/max(1,c.total_cycles):.1f}ms",
        f"  Avg DB:                {c.total_db_time/max(1,c.total_cycles):.1f}ms",
    ]

    # Strategy stats
    lines.append("")
    lines.append("  Strategy Results:")
    for name in set(list(c.strategy_passes.keys()) + list(c.strategy_fails.keys())):
        p = c.strategy_passes.get(name, 0)
        f = c.strategy_fails.get(name, 0)
        total = p + f
        rate = (p / total * 100) if total > 0 else 0
        lines.append(f"    {name}: ✅ {p} / ❌ {f} ({rate:.0f}% pass)")

    # Reject reasons
    lines.append("")
    lines.append("  Reject Reasons:")
    top = c.top_rejects(5)
    total_rejects = sum(c.reject_by_code.values())
    if total_rejects > 0:
        for code, count in top:
            pct = count / total_rejects * 100
            lines.append(f"    {code}: {count}x ({pct:.0f}%)")

    # Most frequent reject reason
    if top:
        lines.append(f"\n  🔴 Most Frequent: {top[0][0]} — {top[0][1]}x")

    lines.append("═" * 50)
    return "\n".join(lines)


def format_hourly_report() -> str:
    """تقرير كل ساعة."""
    c = _counters
    total_rejects = sum(c.reject_by_code.values())

    lines = [
        "",
        "█" * 60,
        "█  🏆 HOURLY REJECT REPORT",
        "█" * 60,
    ]

    if total_rejects > 0:
        top = c.top_rejects(10)
        for i, (code, count) in enumerate(top, 1):
            pct = count / total_rejects * 100
            bar = "█" * int(pct / 5)
            lines.append(f"  {i:>2}. {code:<30s} {count:>4}x ({pct:>5.1f}%) {bar}")

    lines.append("")
    lines.append(f"  Total Rejections: {total_rejects}")
    lines.append(f"  Total Executed:   {c.total_executed}")
    lines.append(f"  Total Cycles:     {c.total_cycles}")
    if c.total_cycles > 0:
        lines.append(f"  Hit Rate:         {c.total_executed / c.total_cycles * 100:.1f}%")
    lines.append("█" * 60)
    return "\n".join(lines)
