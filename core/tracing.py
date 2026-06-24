"""
Tracing Engine — تجميع DecisionTrace من تدفق البيانات.
لا يغير منطق التداول — مجرد مراقب (Observer).
"""
import time
from typing import Any, Dict, List, Optional
from datetime import datetime

from core.decision_trace import (
    DecisionTrace, MarketSnapshotTrace, StrategyTrace,
    ScoreBreakdown, CycleCounters, get_counters, update_counters,
    format_5min_report, format_hourly_report,
)
from core.reason_codes import ReasonCode, Rejection, passed, reject


class TraceBuilder:
    """
    بناء DecisionTrace أثناء مرور البيانات عبر خط الأنابيب.
    يستخدم كـ context manager حول معالجة كل عملة.
    """

    def __init__(self, cycle: int, symbol: str, state, market_analyzer,
                 strategy_engine, evidence_engine, risk_engine, execution_engine):
        self.trace = DecisionTrace(
            cycle=cycle,
            symbol=symbol,
            timestamp=datetime.utcnow().isoformat(),
        )
        self._state = state
        self._analyzer = market_analyzer
        self._strategy_engine = strategy_engine
        self._evidence_engine = evidence_engine
        self._risk_engine = risk_engine
        self._execution_engine = execution_engine
        self._start = time.time()
        self._prev_ts = self._start

    # ── System State ──

    def capture_system_state(self) -> "TraceBuilder":
        s = self._state
        self.trace.trading_allowed = getattr(s, 'trading_allowed', False)
        self.trace.system_state = getattr(s, 'phase', 'UNKNOWN')
        self.trace.warmup_status = "OK" if getattr(s, 'warmup_complete', False) else "INCOMPLETE"
        self.trace.active_strategies = list(
            self._strategy_engine._strategies.keys()
            if hasattr(self._strategy_engine, '_strategies')
            else []
        )
        # WebSocket status
        from engines.market_data_engine import MarketDataEngine
        ws = getattr(self._state, '_market_data_ref', None)
        if ws and hasattr(ws, '_ws') and ws._ws is not None:
            self.trace.websocket_status = "متصل"
        else:
            self.trace.websocket_status = "منفصل"
        return self

    # ── Market Snapshot ──

    def capture_market(self, analysis) -> "TraceBuilder":
        if analysis is None:
            return self
        m = self.trace.market
        m.symbol = self.trace.symbol
        m.regime = str(getattr(analysis, 'regime', ''))
        m.trend_direction = str(getattr(analysis, 'trend_direction', ''))
        m.trend_strength = float(getattr(analysis, 'trend_strength', 0) or 0)
        m.momentum = float(getattr(analysis, 'momentum', 0) or 0)
        m.volatility = float(getattr(analysis, 'volatility', 0) or 0)
        m.liquidity = float(getattr(analysis, 'liquidity_score', 0) or 0)
        m.spread = float(getattr(analysis, 'spread', 0) or 0)
        m.volume = float(getattr(analysis, 'volume', 0) or 0)
        m.noise = float(getattr(analysis, 'noise', 0) or 0)
        m.confidence = float(getattr(analysis, 'confidence', 0) or 0)
        return self

    # ── Strategies ──

    def capture_strategies(self, signals: list) -> "TraceBuilder":
        if not signals:
            self.trace.strategy_results.append(
                StrategyTrace(name="All", passed=False, rejection=reject(
                    "Strategies", "Signal Available", ReasonCode.RC010_NO_STRATEGY_MATCH,
                    details="لا إشارات من أي استراتيجية"
                ))
            )
            return self

        for sig in signals:
            name = getattr(sig, 'strategy_name', 'Unknown')
            direction = getattr(sig, 'direction', 'NONE')
            confidence = float(getattr(sig, 'confidence', 0) or 0)
            is_valid = bool(getattr(sig, 'is_valid', False))

            if not is_valid:
                reason = getattr(sig, 'rejection_reason', 'استراتيجية غير صالحة')
                st = StrategyTrace(
                    name=name, passed=False, confidence=confidence,
                    decision=str(direction),
                    rejection=reject(
                        "Strategy", name, ReasonCode.RC009_STRATEGY_FAILED,
                        details=reason,
                    )
                )
            else:
                st = StrategyTrace(
                    name=name, passed=True, confidence=confidence,
                    decision=str(direction),
                )
            self.trace.strategy_results.append(st)
        return self

    # ── Evidence ──

    def capture_evidence(self, evidence) -> "TraceBuilder":
        if evidence is None:
            self.trace.evidence_rejection = reject(
                "Evidence", "Evaluate", ReasonCode.RC011_EVIDENCE_FAILED,
                details="No evidence object returned"
            )
            return self

        self.trace.evidence_score = float(getattr(evidence, 'score', 0) or 0)
        self.trace.confidence_score = float(getattr(evidence, 'final_score', 0) or getattr(evidence, 'confidence', 0) or 0)
        self.trace.direction = str(getattr(evidence, 'decision', '') or getattr(evidence, 'direction', ''))
        self.trace.consensus = str(getattr(evidence, 'consensus', ''))

        # Count votes
        votes = getattr(evidence, 'votes', {}) or {}
        self.trace.strategy_votes_buy = votes.get('BUY', 0)
        self.trace.strategy_votes_sell = votes.get('SELL', 0)
        self.trace.strategy_votes_hold = votes.get('HOLD', 0)

        # Rejection check
        if not self.trace.direction or self.trace.direction in ('NONE', 'HOLD', ''):
            self.trace.evidence_rejection = reject(
                "Evidence", "Direction", ReasonCode.RC019_NO_DIRECTION,
                current=self.trace.direction, required="BUY or SELL",
            )
        elif self.trace.confidence_score < 30:
            self.trace.evidence_rejection = reject(
                "Evidence", "Minimum Confidence", ReasonCode.RC003_LOW_CONFIDENCE,
                current=self.trace.confidence_score, required=30.0,
            )

        # Build score breakdown
        sb = ScoreBreakdown()
        sb.add("Trend", float(getattr(evidence, 'trend_score', 0) or 0))
        sb.add("Momentum", float(getattr(evidence, 'momentum_score', 0) or 0))
        sb.add("Liquidity", float(getattr(evidence, 'liquidity_score', 0) or 0))
        sb.add("Noise", float(getattr(evidence, 'noise_score', 0) or 0))
        sb.add("ADX", float(getattr(evidence, 'adx_score', 0) or 0))
        sb.add("Volume", float(getattr(evidence, 'volume_score', 0) or 0))
        sb.add("ATR", float(getattr(evidence, 'atr_score', 0) or 0))
        sb.add("Market Structure", float(getattr(evidence, 'structure_score', 0) or 0))
        penalty_amount = float(getattr(evidence, 'risk_penalty', 0) or 0)
        if penalty_amount > 0:
            sb.apply_penalty(penalty_amount, "Risk")
        self.trace.score_breakdown = sb

        return self

    # ── Risk ──

    def capture_risk(self, risk_decision) -> "TraceBuilder":
        if risk_decision is None:
            self.trace.risk_rejection = reject(
                "Risk", "Evaluate", ReasonCode.RC012_RISK_ENGINE_BLOCKED,
                details="No risk decision"
            )
            return self

        self.trace.risk_allowed = bool(getattr(risk_decision, 'trade_allowed', False))
        self.trace.position_size = float(getattr(risk_decision, 'position_size', 0) or 0)
        self.trace.exposure = float(getattr(risk_decision, 'exposure', 0) or 0)
        self.trace.drawdown = float(getattr(risk_decision, 'current_drawdown', 0) or 0)
        self.trace.max_consecutive_losses = int(getattr(risk_decision, 'consecutive_losses', 0) or 0)
        self.trace.cooldown = bool(getattr(risk_decision, 'cooldown_active', False))

        risk_level = str(getattr(risk_decision, 'risk_level', ''))
        self.trace.capital_check = risk_level not in ('BLOCKED', 'CRITICAL')
        self.trace.daily_loss_check = not bool(getattr(risk_decision, 'daily_loss_hit', False))

        if not self.trace.risk_allowed:
            blocking = getattr(risk_decision, 'blocking_reason', '') or 'تقييم المخاطر'
            # Determine which code best matches
            if 'cooldown' in blocking.lower():
                code = ReasonCode.RC004_COOLDOWN
            elif 'exposure' in blocking.lower():
                code = ReasonCode.RC022_EXPOSURE_LIMIT
            elif 'drawdown' in blocking.lower():
                code = ReasonCode.RC015_MAX_DRAWDOWN
            elif 'capital' in blocking.lower():
                code = ReasonCode.RC024_CAPITAL_INSUFFICIENT
            elif 'daily' in blocking.lower():
                code = ReasonCode.RC023_DAILY_LOSS_LIMIT
            elif 'position' in blocking.lower():
                code = ReasonCode.RC025_POSITION_EXISTS
            else:
                code = ReasonCode.RC005_RISK_LIMIT

            self.trace.risk_rejection = reject(
                "Risk", "Risk Evaluation", code,
                current=risk_level, details=blocking,
            )

        return self

    # ── Execution ──

    def capture_execution(self, execution) -> "TraceBuilder":
        if execution is None:
            self.trace.trade_candidate = False
            return self

        self.trace.trade_candidate = True
        self.trace.final_direction = str(getattr(execution, 'side', '') or '')
        self.trace.quantity = float(getattr(execution, 'executed_quantity', 0) or 0)
        self.trace.entry = float(getattr(execution, 'executed_price', 0) or 0)
        self.trace.stop_loss = float(getattr(execution, 'stop_loss', 0) or 0)
        self.trace.take_profit = float(getattr(execution, 'take_profit', 0) or 0)

        if self.trace.stop_loss and self.trace.entry:
            risk = abs(self.trace.entry - self.trace.stop_loss)
            reward = 0.0
            if self.trace.take_profit:
                reward = abs(self.trace.take_profit - self.trace.entry)
            self.trace.risk_reward = reward / risk if risk > 0 else 0.0

        return self

    # ── Timing ──

    def mark_time(self, name: str) -> None:
        now = time.time()
        elapsed_ms = (now - self._prev_ts) * 1000
        self.trace.add_timing(name, elapsed_ms)
        self._prev_ts = now

    # ── Finalize ──

    def finalize(self, executed: bool = False) -> DecisionTrace:
        self.trace.cycle_end = time.time()
        self.trace.trade_executed = executed

        # Determine final rejection if not executed
        if not executed:
            for r in [self.trace.evidence_rejection, self.trace.risk_rejection, self.trace.execution_rejection]:
                if r and r.code != ReasonCode.RC000_PASS:
                    self.trace.final_rejection = r
                    break
            if self.trace.final_rejection is None:
                if not self.trace.trading_allowed:
                    self.trace.final_rejection = reject(
                        "System", "State", ReasonCode.RC014_SYSTEM_NOT_READY,
                        current=self.trace.system_state, required="TRADING_ACTIVE",
                    )
                elif not self.trace.direction or self.trace.direction in ('NONE', 'HOLD', ''):
                    self.trace.final_rejection = reject(
                        "System", "No Signal", ReasonCode.RC019_NO_DIRECTION,
                        details="No signal generated this cycle",
                    )

        return self.trace


# ═══════════════════════════════════════════════════════════════
# Context Manager
# ═══════════════════════════════════════════════════════════════

class timer:
    """مؤقت بسيط لكل محرك."""
    def __init__(self):
        self.start: float = 0.0
        self.elapsed_ms: float = 0.0

    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, *args):
        self.elapsed_ms = (time.time() - self.start) * 1000
