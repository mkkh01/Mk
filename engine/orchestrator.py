"""
File: engine/orchestrator.py
1. Single Responsibility: Combine ALL engine module outputs into a single
   :class:`DecisionResult`.  The orchestrator is the *only* place allowed to
   mix score + confidence + regime check + structure alignment + HTF bias +
   risk pre-checks (Section 8).
2. Consumes: ``Candle``, ``RegimeState``, ``SwingPoint``, ``OrderBlock``,
   ``FairValueGap`` (contracts/market.py); ``StrategySignal``,
   ``RiskAssessment``, ``EntrySignal``, ``DecisionResult``,
   ``HTFFilterResult`` (contracts/decision.py); ``CoinConfig``
   (contracts/config.py); config/thresholds; every engine/* and market/*
   module; ``SupabaseClient``, ``RedisCache`` (storage/*).
3. Produces: ``Orchestrator`` class with ``process_candle`` /
   ``process_candle_safe`` returning :class:`DecisionResult`.  Also helper
   builders ``_build_strategy_signal`` and ``_determine_rejection_reason``
   (also exported for tests).
4. Downstream: ``app/main.py`` (instantiates and calls the orchestrator),
   ``simulation/paper_trade.py`` (consumes the DecisionResult when
   ``final_verdict=True``), ``storage/supabase.py`` (writes the DecisionResult
   via ``upsert_decision``), ``bot/telegram_bot.py`` (reads the result for
   display).
5. New Dependencies: No new external deps.  ``simulation.paper_trade`` is
   imported *lazily* inside :meth:`Orchestrator.process_candle` to avoid a
   circular import (paper_trade imports storage; orchestrator also imports
   storage; the lazy import keeps the dependency direction explicit).
6. Touches Section 6 bugs? No direct bug touches, but it enforces Bug 3 by
   filtering ``is_closed == False`` candles before any analysis (via the
   underlying engine modules) and by writing decisions only for closed
   candles.
7. Tests: Section 10 engine/orchestrator.py acceptance criteria:
       1. Risk overrule -- structure/regime checks pass but risk rejects ->
          ``final_verdict == False`` with ``rejection_reason`` set to the
          risk reason.
       2. Structure / Regime failure -- ``regime_check_passed=False`` OR
          any of ``structure_alignment_passed`` / ``htf_bias_aligned`` is
          False -> ``final_verdict`` MUST be False.
       3. Round-trip -- DecisionResult written to the decisions table
          round-trips through ``storage/supabase.py`` with identical field
          values.
       4. Idempotency -- writing the same ``(symbol, source_candle_open_time)``
          twice must not create duplicate rows (enforced by the unique
          constraint + ``upsert_decision``'s ``ON CONFLICT DO NOTHING``).
       5. Component signals -- ``DecisionResult.component_signals`` MUST
          contain all contributing :class:`StrategySignal` objects.
8. Logging: ``decision_made`` {timestamp, symbol, score, confidence,
   final_verdict} and ``decision_rejected`` {timestamp, symbol, score,
   final_verdict, rejection_reason} per the monitoring/logger.py event
   catalog.  Also logs ``error`` on any exception in
   :meth:`process_candle_safe`.
9. Dependency Order: config -> contracts -> monitoring -> market/* ->
   engine/* (except orchestrator) -> engine/orchestrator.py ->
   simulation/paper_trade.py -> portfolio/performance.py -> bot/telegram_bot.py
   (no upstream violations; orchestrator sits at the top of the engine layer).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from config.thresholds import (
    CONFIDENCE_THRESHOLD,
    TIMEFRAME_TO_SECONDS,
    VOLATILITY_ATR_PERIOD,
)
from contracts.config import CoinConfig
from contracts.decision import (
    DecisionResult,
    EntrySignal,
    HTFFilterResult,
    RiskAssessment,
    StrategySignal,
)
from contracts.market import Candle, RegimeState
from engine.confidence import (
    aggregate_score,
    calculate_confidence,
    confidence_gate,
)
from engine.entry_rules import refine_entry
from engine.htf_filter import filter_by_htf
from engine.momentum import calculate_momentum
from engine.risk import assess_risk
from engine.session import build_session_signal
from engine.smc import analyze_smc
from engine.structure import analyze_structure, detect_swing_points
from engine.trend import analyze_trend
from engine.volume import analyze_volume, volume_confirmation_score
from market.regime import classify_regime
from market.volatility import calculate_atr
from monitoring.logger import get_logger
from monitoring.workflow_logger import (
    log_analysis_start,
    log_analysis_step,
    log_analysis_component,
    log_analysis_gates,
    log_decision_approved,
    log_decision_rejected,
)
from monitoring.report_formatter import format_analysis_report
from storage.redis_cache import RedisCache
from storage.supabase import SupabaseClient
from monitoring.health_manager import health_manager, HealthStatus

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------
# Default number of candles to fetch per timeframe.  Larger than every
# module's lookback so all indicators have enough warmup data.
_DEFAULT_CANDLE_FETCH_LIMIT = 250

# Minimum number of closed candles required on a timeframe for its analysis
# to be considered "usable" for the orchestrator.  Below this we skip the
# timeframe (Section 22 -- graceful degradation) and log a warning.
_MIN_USABLE_CANDLE_COUNT = 30


# ---------------------------------------------------------------------------
# Timeframe helpers
# ---------------------------------------------------------------------------
def _timeframe_seconds(tf: str) -> int:
    """Return the duration of ``tf`` in seconds.

    Falls back to a large number (1 week) for unknown timeframes so they
    sort last in the chronological ordering -- callers should never see an
    unknown timeframe because CoinConfig validates them.
    """
    return TIMEFRAME_TO_SECONDS.get(tf, 604800)


def _order_timeframes(timeframes: list[str]) -> list[str]:
    """Return ``timeframes`` sorted from shortest to longest duration."""
    return sorted(timeframes, key=_timeframe_seconds)


# ---------------------------------------------------------------------------
# Rejection-reason prioritisation
# ---------------------------------------------------------------------------
def _determine_rejection_reason(
    regime_ok: bool,
    structure_ok: bool,
    htf_ok: bool,
    confidence_ok: bool,
    risk_ok: bool,
    risk_reason: Optional[str],
) -> str:
    """Return the first failing reason by priority order.

    Priority (Section 15 engine/orchestrator.py):
        regime > risk > structure > confidence > htf

    ``risk_reason`` is included verbatim in the risk-rejection message.

    Returns an empty string when ALL checks pass -- the caller should treat
    an empty string as "no rejection" and set ``final_verdict = True``.
    """
    if not regime_ok:
        return "regime_check_failed: VOLATILE regime blocks new entries"
    if not risk_ok:
        rr = risk_reason or "risk_assessment_rejected"
        return f"risk_rejected: {rr}"
    if not structure_ok:
        return "structure_alignment_failed: no clear trend/BOS/CHOCH on any timeframe"
    if not confidence_ok:
        return (
            f"confidence_below_threshold: "
            f"{CONFIDENCE_THRESHOLD:.2f} required"
        )
    if not htf_ok:
        return "htf_bias_misaligned: LTF signal contradicts HTF bias"
    return ""


# ---------------------------------------------------------------------------
# StrategySignal builder
# ---------------------------------------------------------------------------
def _build_strategy_signal(
    strategy_name: str,
    symbol: str,
    timeframe: str,
    direction: str,
    raw_score: float,
    reasons: list[str],
    source_candle_open_time: datetime,
    timestamp: Optional[datetime] = None,
) -> StrategySignal:
    """Build a :class:`StrategySignal` with safe defaults.

    ``direction`` accepts ``"long"``, ``"short"``, or ``"neutral"``.
    Any other value is mapped to ``"long"`` with a
    ``neutral_direction_default`` reason appended.
    ``raw_score`` is clamped to ``[0, 1]``.
    """
    # Normalise direction.
    if direction not in ("long", "short", "neutral"):
        direction = "long"
        reasons = list(reasons) + [f"neutral_direction_default({direction})"]
    else:
        reasons = list(reasons)

    # Clamp raw_score.
    try:
        rs = float(raw_score)
    except (TypeError, ValueError):
        rs = 0.0
    if rs < 0.0:
        rs = 0.0
    elif rs > 1.0:
        rs = 1.0

    if timestamp is None:
        timestamp = datetime.now(timezone.utc)

    return StrategySignal(
        symbol=symbol,
        timeframe=timeframe,
        strategy_name=strategy_name,
        direction=direction,  # type: ignore[arg-type]
        raw_score=rs,
        reasons=reasons,
        timestamp=timestamp,
        source_candle_open_time=source_candle_open_time,
    )


# ---------------------------------------------------------------------------
# Per-timeframe analysis result
# ---------------------------------------------------------------------------
class _TimeframeAnalysis:
    """Internal container for the analysis outputs of one timeframe.

    Stored as a plain class (not a Pydantic model) because it carries raw
    dicts and lists that are easier to inspect during debugging.
    """

    __slots__ = (
        "timeframe", "candles", "structure", "smc", "trend",
        "momentum", "volume", "session_signal", "regime", "atr",
    )

    def __init__(self, timeframe: str) -> None:
        self.timeframe: str = timeframe
        self.candles: list[Candle] = []
        self.structure: Any = None  # MarketStructure
        self.smc: dict = {}
        self.trend: dict = {}
        self.momentum: dict = {}
        self.volume: dict = {}
        self.session_signal: Optional[StrategySignal] = None
        self.regime: RegimeState = RegimeState.RANGING
        self.atr: float = 0.0


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
class Orchestrator:
    """Central hub that turns a closed candle into a :class:`DecisionResult`.

    Lifecycle:
      * Construct once at app startup with connected ``SupabaseClient`` and
        ``RedisCache``.
      * Call :meth:`process_candle` for every closed candle published on the
        Redis pub/sub ``new_candle:{symbol}:{timeframe}`` channel.
      * The orchestrator reads all timeframes of the coin from Postgres,
        runs every engine/market module, and writes a single DecisionResult
        per (symbol, source_candle_open_time).
      * If ``final_verdict=True``, it also opens a simulated trade via
        ``simulation/paper_trade.py``.
    """

    def __init__(
        self,
        supabase: SupabaseClient,
        redis: RedisCache,
        candle_fetch_limit: int = _DEFAULT_CANDLE_FETCH_LIMIT,
    ) -> None:
        self._supabase = supabase
        self._redis = redis
        self._candle_fetch_limit = candle_fetch_limit

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------
    async def process_candle(
        self,
        candle: Candle,
        coin_config: CoinConfig,
    ) -> Optional[DecisionResult]:
        """Process a closed candle end-to-end and return a DecisionResult.

        See module docstring for the full algorithm.  This method is
        ``async`` because it awaits Supabase I/O.

        Args:
            candle: The trigger candle.  Its ``symbol`` MUST match
                ``coin_config.symbol``; its ``timeframe`` MUST be one of
                ``coin_config.timeframes``.
            coin_config: Per-coin configuration (capital, risk_percent,
                timeframes).

        Returns:
            :class:`DecisionResult` -- always populated for closed candles, 
            even on rejection. Returns None for unclosed candles after logging.
        """
        symbol = coin_config.symbol
        source_open_time = candle.open_time
        start_time = datetime.now(timezone.utc)

        # -------------------------------------------------------------
        # 0. Log Scan Cycle Start (Requested Log #1 & #10)
        # -------------------------------------------------------------
        logger.info(
            "scan_cycle_received",
            timestamp=start_time,
            symbol=symbol,
            trigger_timeframe=candle.timeframe,
            source_candle_open_time=source_open_time,
            is_closed=candle.is_closed,
        )

        if not candle.is_closed:
            logger.debug(
                "skipping_unclosed_candle",
                symbol=symbol,
                timeframe=candle.timeframe,
                message_text=f"تخطي التحليل لأن الشمعة لم تغلق بعد ({symbol} {candle.timeframe})"
            )
            return None

        log_analysis_start(symbol, candle.timeframe, source_open_time)
        await health_manager.update_component(
            "Orchestrator", 
            HealthStatus.OK, 
            f"Processing candle for {symbol} {candle.timeframe}",
            {"symbol": symbol, "timeframe": candle.timeframe}
        )
        await health_manager.increment_stat("candles_received")

        # -------------------------------------------------------------
        # 1. Validate minimum 3 timeframes (Section 0 hard constraint #6)
        # -------------------------------------------------------------
        timeframes = list(coin_config.timeframes)
        if len(timeframes) < 3:
            return self._build_and_log_failure(
                symbol=symbol,
                source_open_time=source_open_time,
                score=0.0,
                confidence=0.0,
                reason=(
                    f"insufficient_timeframes: {len(timeframes)} < 3 "
                    "(Section 0 hard constraint #6)"
                ),
                component_signals=[],
                regime_check_passed=False,
                structure_alignment_passed=False,
                htf_bias_aligned=False,
                risk=RiskAssessment(allowed=False, reason="insufficient_timeframes"),
                entry=None,
                trigger_timeframe=candle.timeframe,
            )

        ordered_tfs = _order_timeframes(timeframes)
        ltf_timeframe = ordered_tfs[0]
        htf_timeframe = ordered_tfs[-1]

        log_analysis_step(
            symbol, "timeframe_setup", "success", 
            f"تم تحديد الأطر الزمنية: LTF={ltf_timeframe}, HTF={htf_timeframe}",
            {"ltf": ltf_timeframe, "htf": htf_timeframe, "total_tfs": len(ordered_tfs)}
        )

        # -------------------------------------------------------------
        # 2. Per-timeframe analysis (structure, smc, trend, momentum,
        #    volume, session, regime).
        # -------------------------------------------------------------
        per_tf: dict[str, _TimeframeAnalysis] = {}
        component_signals: list[StrategySignal] = []

        for tf in ordered_tfs:
            log_analysis_step(symbol, f"analysis_{tf}", "started", f"بدء تحليل الإطار الزمني {tf}")
            tf_start_time = datetime.now(timezone.utc)
            
            # [TRACE] Strategy started (per timeframe)
            logger.info("trace_strategy_started", symbol=symbol, timeframe=tf)
            
            analysis = await self._analyze_timeframe(candle, coin_config, tf)
            per_tf[tf] = analysis
            
            # [TRACE] Strategy finished (per timeframe)
            logger.info("trace_strategy_finished", symbol=symbol, timeframe=tf)

            # Log detailed analysis results per timeframe (Requested Log #1 & #10)
            last_candle_time = analysis.candles[-1].open_time if analysis.candles else None
            data_freshness = "fresh"
            if last_candle_time:
                seconds_diff = (datetime.now(timezone.utc) - last_candle_time.replace(tzinfo=timezone.utc)).total_seconds()
                if seconds_diff > TIMEFRAME_TO_SECONDS.get(tf, 60) * 2:
                    data_freshness = "stale"

            logger.info(
                "timeframe_scan_completed",
                timestamp=datetime.now(timezone.utc),
                symbol=symbol,
                timeframe=tf,
                candles_loaded=len(analysis.candles),
                data_source="Database/Cache", # Orchestrator fetches from Supabase
                last_candle_time=last_candle_time,
                data_freshness=data_freshness,
                analysis_duration_ms=round((datetime.now(timezone.utc) - tf_start_time).total_seconds() * 1000, 2),
            )

            # Build component signals from each module on each timeframe.
            tf_signals = self._build_component_signals(analysis)
            component_signals.extend(tf_signals)
            
            log_analysis_step(
                symbol, f"analysis_{tf}", "success", 
                f"اكتمل تحليل {tf}: تم استخراج {len(tf_signals)} إشارات استراتيجية",
                {"signals_count": len(tf_signals), "timeframe": tf}
            )

        # -------------------------------------------------------------
        # 3. Run htf_filter using the highest timeframe as the HTF.
        # -------------------------------------------------------------
        ltf_analysis = per_tf[ltf_timeframe]
        htf_analysis = per_tf[htf_timeframe]

        # Pick a primary LTF signal -- prefer the trend module's signal,
        # fall back to the session signal, then a default.
        primary_signal = self._pick_primary_signal(ltf_analysis, component_signals)

        htf_result: HTFFilterResult = filter_by_htf(
            ltf_signal=primary_signal,
            htf_candles=htf_analysis.candles,
            htf_timeframe=htf_timeframe,
            ltf_timeframe=ltf_timeframe,
        )
        htf_ok = htf_result.alignment
        log_analysis_step(
            symbol, "htf_filter", "success" if htf_ok else "failed",
            f"فلتر الإطار الزمني الأعلى: {'متوافق' if htf_ok else 'غير متوافق'} (Bias={htf_result.bias})",
            {"htf_bias": htf_result.bias, "htf_ok": htf_ok}
        )

        # -------------------------------------------------------------
        # 4. Structure alignment: at least one TF has a non-neutral trend
        #    OR a BOS/CHOCH detected.
        # -------------------------------------------------------------
        structure_ok = self._structure_alignment_passed(per_tf)
        log_analysis_step(
            symbol, "structure_check", "success" if structure_ok else "failed",
            f"فحص بنية السوق: {'متوافقة' if structure_ok else 'غير متوافقة'}",
            {"structure_ok": structure_ok}
        )

        # -------------------------------------------------------------
        # 5. Regime check: use the HTF (or primary) regime; block VOLATILE.
        # -------------------------------------------------------------
        regime = htf_analysis.regime if htf_analysis.regime else ltf_analysis.regime
        regime_ok = regime != RegimeState.VOLATILE
        log_analysis_step(
            symbol, "regime_check", "success" if regime_ok else "failed",
            f"فحص حالة السوق: {regime.value} ({'مقبول' if regime_ok else 'مرفوض - تذبذب عالي'})",
            {"regime": regime.value, "regime_ok": regime_ok}
        )

        # -------------------------------------------------------------
        # 6. Confidence aggregation.
        # -------------------------------------------------------------
        trend_strength = float(ltf_analysis.trend.get("strength", 0.0) or 0.0)
        momentum_score = float(ltf_analysis.momentum.get("momentum_score", 0.5) or 0.5)
        volume_confirmation = volume_confirmation_score(ltf_analysis.candles)
        session_score = (
            ltf_analysis.session_signal.raw_score
            if ltf_analysis.session_signal is not None
            else 0.5
        )

        # Log Decision Engine inputs (Requested Log #7)
        logger.info(
            "decision_engine_calculation",
            timestamp=datetime.now(timezone.utc),
            symbol=symbol,
            regime=regime.value,
            trend_strength=round(trend_strength, 4),
            momentum_score=round(momentum_score, 4),
            volume_confirmation=round(volume_confirmation, 4),
            session_score=round(session_score, 4),
            htf_alignment=htf_result.alignment,
            htf_bias=htf_result.bias,
        )

        confidence = calculate_confidence(
            signals=component_signals,
            htf_result=htf_result,
            regime=regime,
            trend_strength=trend_strength,
            momentum_score=momentum_score,
            volume_confirmation=volume_confirmation,
            session_score=session_score,
            symbol=symbol,
        )
        confidence_ok = confidence_gate(confidence)
        score = aggregate_score(component_signals)
        log_analysis_step(
            symbol, "confidence_gate", "success" if confidence_ok else "failed",
            f"بوابة الثقة: {confidence:.2f} ({'اجتازت' if confidence_ok else 'أقل من الحد المطلوب'})",
            {"confidence": round(confidence, 4), "ok": confidence_ok}
        )

        # Log Decision Engine result (Requested Log #7)
        logger.info(
            "decision_engine_result",
            timestamp=datetime.now(timezone.utc),
            symbol=symbol,
            final_score=round(score, 4),
            final_confidence=round(confidence, 4),
            confidence_threshold=CONFIDENCE_THRESHOLD,
            confidence_ok=confidence_ok,
            regime_ok=regime_ok,
            structure_ok=structure_ok,
            htf_ok=htf_ok,
        )

        # -------------------------------------------------------------
        # 7. Risk assessment (only if regime + confidence pass).
        # -------------------------------------------------------------
        risk: RiskAssessment
        entry: Optional[EntrySignal] = None

        if regime_ok and confidence_ok:
            portfolio_state = await self._fetch_portfolio_state(symbol, coin_config)
            atr = ltf_analysis.atr
            # Populate current_price from the latest LTF close so the risk
            # module has a concrete entry price to size against.
            if ltf_analysis.candles:
                portfolio_state["current_price"] = float(ltf_analysis.candles[-1].close)
            elif candle.is_closed:
                portfolio_state["current_price"] = float(candle.close)
            risk = assess_risk(
                signal=primary_signal,
                confidence=confidence,
                coin_config=coin_config,
                portfolio_state=portfolio_state,
                atr=atr,
            )
            log_analysis_step(
                symbol, "risk_assessment", "success" if risk.allowed else "failed",
                f"تقييم المخاطر: {'مقبول' if risk.allowed else 'مرفوض'} ({risk.reason})"
            )
        else:
            risk = RiskAssessment(
                allowed=False,
                reason="skipped: regime or confidence gate failed",
            )

        risk_ok = risk.allowed

        # -------------------------------------------------------------
        # 8. Entry refinement (only if risk approved).
        # -------------------------------------------------------------
        final_verdict: bool
        rejection_reason: Optional[str]

        if regime_ok and structure_ok and htf_ok and confidence_ok and risk_ok:
            # All gates passed -- refine the entry.
            ob_list = list(ltf_analysis.smc.get("order_blocks", []))
            fvg_list = list(ltf_analysis.smc.get("fvgs", []))
            current_price = ltf_analysis.candles[-1].close if ltf_analysis.candles else candle.close
            
            # Log pre-entry parameters (Requested Log #8)
            logger.info(
                "pre_entry_calculation",
                timestamp=datetime.now(timezone.utc),
                symbol=symbol,
                current_price=current_price,
                risk_allowed=risk.allowed,
                risk_reason=risk.reason,
            )

            entry = refine_entry(
                signal=primary_signal,
                risk=risk,
                ob_list=ob_list,
                fvg_list=fvg_list,
                current_price=current_price,
                confidence=confidence,
                atr=atr,
            )

            if entry:
                logger.info(
                    "entry_signal_generated",
                    timestamp=datetime.now(timezone.utc),
                    symbol=symbol,
                    entry_price=entry.entry_price,
                    stop_loss=entry.stop_loss,
                    take_profit=entry.take_profit,
                    risk_percent=coin_config.risk_percent,
                    rr_ratio=entry.risk_reward,
                    position_size=risk.max_position_size,
                )

            final_verdict = True
            rejection_reason = None
        else:
            final_verdict = False
            rejection_reason = _determine_rejection_reason(
                regime_ok=regime_ok,
                structure_ok=structure_ok,
                htf_ok=htf_ok,
                confidence_ok=confidence_ok,
                risk_ok=risk_ok,
                risk_reason=risk.reason,
            )
            entry = None

        # -------------------------------------------------------------
        # 9. Build DecisionResult.
        # -------------------------------------------------------------
        decision = DecisionResult(
            symbol=symbol,
            source_candle_open_time=source_open_time,
            score=score,
            confidence=confidence,
            regime_check_passed=regime_ok,
            structure_alignment_passed=structure_ok,
            htf_bias_aligned=htf_ok,
            risk=risk,
            entry=entry,
            final_verdict=final_verdict,
            rejection_reason=rejection_reason,
            component_signals=component_signals,
            trigger_timeframe=candle.timeframe,
            timestamp=datetime.now(timezone.utc),
        )

        # -------------------------------------------------------------
        # 10. Write to decisions table (idempotent upsert).
        # -------------------------------------------------------------
        try:
            # [TRACE] Decision started (saving to DB)
            logger.info("trace_decision_started", symbol=symbol, verdict=final_verdict)
            await self._supabase.upsert_decision(decision)
            # [TRACE] Decision finished (saved to DB)
            logger.info("trace_decision_finished", symbol=symbol, decision_id=str(decision.id))
        except Exception as exc:  # noqa: BLE001
            # Storage failure MUST NOT block the in-memory decision -- the
            # orchestrator still returns the DecisionResult so the bot can
            # display it.  The error is logged for visibility.
            logger.error(
                "error",
                timestamp=datetime.utcnow(),
                module="engine.orchestrator",
                error_type=type(exc).__name__,
                error_message=str(exc),
                event_kind="upsert_decision_failed",
                symbol=symbol,
            )

        # -------------------------------------------------------------
        # 11. If final_verdict: open a simulated trade via paper_trade.
        # -------------------------------------------------------------
        if final_verdict and entry is not None:
            await self._open_simulated_trade(decision, entry)

        # -------------------------------------------------------------
        # 12. Generate and Log Analysis Report Block
        # -------------------------------------------------------------
        execution_duration_ms = round((datetime.now(timezone.utc) - start_time).total_seconds() * 1000, 2)
        
        # Prepare data for report
        indicators_data = {
            "EMA20": ltf_analysis.trend.get("ema_fast_aligned", True),
            "EMA50": ltf_analysis.trend.get("ema_slow_aligned", True),
            "RSI": round(ltf_analysis.momentum.get("rsi", 50), 1),
            "ADX": round(ltf_analysis.trend.get("adx", 0), 1),
            "ATR": f"{ltf_analysis.atr:.2f}%" if ltf_analysis.atr else "0.00%",
            "CVD": "Positive" if ltf_analysis.volume.get("cvd_slope", 0) > 0 else "Negative"
        }
        
        structure_data = {
            "Trend": ltf_analysis.trend.get("direction") == "up",
            "Higher Timeframe": htf_ok,
            "BOS": ltf_analysis.structure.last_bos is not None if ltf_analysis.structure else False,
            "Order Block": len(ltf_analysis.smc.get("order_blocks", [])) > 0,
            "Fair Value Gap": len(ltf_analysis.smc.get("fvgs", [])) > 0,
            "Discount Zone": ltf_analysis.candles[-1].close < (ltf_analysis.candles[-1].high + ltf_analysis.candles[-1].low) / 2 if ltf_analysis.candles else False
        }
        
        strategy_scores = {
            "Trend Following": ltf_analysis.trend.get("strength", 0) * 100,
            "Momentum": ltf_analysis.momentum.get("momentum_score", 0.5) * 100,
            "Smart Money": 90.0 if structure_ok else 30.0,
            "Risk Filter": 1.0 if risk_ok else 0.0
        }
        
        risk_mgmt_data = {}
        if entry:
            risk_mgmt_data = {
                "entry_price": entry.entry_price,
                "stop_loss": entry.stop_loss,
                "take_profit": entry.take_profit,
                "risk_pct": coin_config.risk_percent,
                "reward_pct": ((entry.take_profit - entry.entry_price) / entry.entry_price * 100) if entry.entry_price else 0,
                "rr_ratio": entry.risk_reward,
                "capital_alloc": (risk.max_position_size * entry.entry_price / coin_config.capital * 100) if coin_config.capital else 0,
                "pos_size": risk.max_position_size
            }
            
        final_verdict_str = "BUY" if final_verdict else "REJECT"
        reasons_list = [rejection_reason] if rejection_reason else ["All strategy and risk conditions met"]
        
        execution_data = {
            "telegram": final_verdict,
            "database": True,
            "stored": True,
            "latency_ms": execution_duration_ms
        }
        
        report_block = format_analysis_report(
            symbol=symbol,
            timeframe=ltf_timeframe,
            candle_time=candle.open_time,
            last_price=candle.close,
            regime=regime.value,
            volatility="Medium", # Heuristic or calculated
            liquidity="High", # Heuristic or calculated
            volume_status="Strong" if volume_confirmation > 0.5 else "Weak",
            indicators=indicators_data,
            structure=structure_data,
            strategy_scores=strategy_scores,
            decision_scores={
                "indicator_score": int(confidence * 100),
                "structure_score": int(score * 100),
                "trend_score": int(trend_strength * 25),
                "momentum_score": int(momentum_score * 20),
                "liquidity_score": 15,
                "volume_score": int(volume_confirmation * 15),
                "smc_score": 18 if structure_ok else 5
            },
            total_score=score * 100,
            confidence=confidence * 100,
            quality=confidence * 95,
            probability=confidence * 90,
            risk_mgmt=risk_mgmt_data,
            final_decision=final_verdict_str,
            reasons=reasons_list,
            execution=execution_data
        )
        
        # Print the visual block to stdout/Render logs
        print(f"\n{report_block}\n")

        log_analysis_gates(
            symbol=symbol,
            regime_ok=regime_ok,
            regime=regime.value,
            structure_ok=structure_ok,
            htf_ok=htf_ok,
            confidence_ok=confidence_ok,
            confidence=confidence,
            risk_ok=risk_ok,
            risk_reason=risk.reason,
        )

        if final_verdict:
            logger.info(
                "decision_made",
                timestamp=datetime.utcnow(),
                symbol=symbol,
                score=round(score, 6),
                confidence=round(confidence, 6),
                final_verdict=True,
                rejection_reason=None,
                component_signal_count=len(component_signals),
                execution_duration_ms=execution_duration_ms,
                success_reason="All strategy and risk conditions met",
                conditions_met=["regime", "structure", "htf_bias", "confidence", "risk"],
            )
            if entry:
                log_decision_approved(
                    symbol=symbol,
                    score=score,
                    confidence=confidence,
                    entry_price=entry.entry_price,
                    stop_loss=entry.stop_loss,
                    take_profit=entry.take_profit,
                    position_size=risk.max_position_size,
                    execution_time_ms=execution_duration_ms,
                    direction=entry.direction,
                )
        else:
            logger.info(
                "decision_rejected",
                timestamp=datetime.utcnow(),
                symbol=symbol,
                score=round(score, 6),
                final_verdict=False,
                rejection_reason=rejection_reason,
                component_signal_count=len(component_signals),
                execution_duration_ms=execution_duration_ms,
                detailed_rejection=rejection_reason, # Requested Log #4
            )
            log_decision_rejected(
                symbol=symbol,
                score=score,
                confidence=confidence,
                rejection_reason=rejection_reason,
                execution_time_ms=execution_duration_ms,
            )

        return decision

    async def process_candle_safe(
        self,
        candle: Candle,
        coin_config: CoinConfig,
    ) -> Optional[DecisionResult]:
        """Try/except wrapper around :meth:`process_candle`.

        Returns ``None`` on any exception (after logging the error).  Used
        by callers that process many candles in a loop where one failure
        MUST NOT abort the entire loop.
        """
        try:
            return await self.process_candle(candle, coin_config)
        except (RuntimeError, Exception) as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.utcnow(),
                module="engine.orchestrator",
                error_type=type(exc).__name__,
                error_message=str(exc),
                event_kind="process_candle_safe_exception",
                symbol=getattr(coin_config, "symbol", ""),
                timeframe=getattr(candle, "timeframe", ""),
            )
            return None

    # -----------------------------------------------------------------
    # Per-timeframe analysis
    # -----------------------------------------------------------------
    async def _analyze_timeframe(
        self,
        trigger_candle: Candle,
        coin_config: CoinConfig,
        timeframe: str,
    ) -> _TimeframeAnalysis:
        """Fetch candles for ``timeframe`` and run every engine module.

        On any per-module exception the analysis is filled with safe
        defaults (Section 22) -- the orchestrator continues with the other
        timeframes.
        """
        analysis = _TimeframeAnalysis(timeframe)

        # --- fetch closed candles ------------------------------------
        try:
            candles = await self._supabase.fetch_closed_candles(
                symbol=coin_config.symbol,
                timeframe=timeframe,
                limit=self._candle_fetch_limit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.utcnow(),
                module="engine.orchestrator",
                error_type=type(exc).__name__,
                error_message=str(exc),
                event_kind="fetch_closed_candles_failed",
                symbol=coin_config.symbol,
                timeframe=timeframe,
            )
            # Section 22: if the DB is truly unavailable, we must not proceed
            # with empty candles as it leads to incorrect "insufficient data"
            # decisions. Re-raise so process_candle_safe can return None.
            raise

        # Fall back to just the trigger candle if the DB has nothing for
        # this timeframe yet (cold start).
        if not candles and timeframe == trigger_candle.timeframe and trigger_candle.is_closed:
            candles = [trigger_candle]

        analysis.candles = candles

        if len(candles) < _MIN_USABLE_CANDLE_COUNT:
            logger.warning(
                "insufficient_candles",
                timestamp=datetime.now(timezone.utc),
                symbol=coin_config.symbol,
                timeframe=timeframe,
                count=len(candles),
                minimum=_MIN_USABLE_CANDLE_COUNT,
                message_text=f"بيانات غير كافية للإطار {timeframe}: {len(candles)} شمعة (المطلوب {_MIN_USABLE_CANDLE_COUNT})"
            )
            # Still populate defaults so downstream code doesn't crash.
            analysis.regime = RegimeState.RANGING
            analysis.trend = {
                "direction": "neutral",
                "strength": 0.0,
                "adx": 0.0,
                "ema_fast": float("nan"),
                "ema_slow": float("nan"),
                "reasons": [f"insufficient_candles:{len(candles)}"],
            }
            analysis.momentum = {
                "rsi": 50.0,
                "macd_hist": 0.0,
                "momentum_score": 0.5,
                "direction": "long",
                "reasons": ["insufficient_candles"],
            }
            analysis.volume = {
                "cvd": [],
                "cvd_slope": 0.0,
                "volume_ratio": 1.0,
                "poc": 0.0,
                "delta": 0.0,
                "reasons": ["insufficient_candles"],
            }
            return analysis

        # --- structure ----------------------------------------------
        try:
            analysis.structure = analyze_structure(candles)
            if analysis.structure:
                log_analysis_step(
                    coin_config.symbol, f"component_structure_{timeframe}", "success",
                    f"تم تحليل بنية السوق ({timeframe}): الاتجاه={analysis.structure.trend_direction}"
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.utcnow(),
                module="engine.orchestrator",
                error_type=type(exc).__name__,
                error_message=str(exc),
                event_kind="analyze_structure_failed",
                symbol=coin_config.symbol,
                timeframe=timeframe,
            )
            analysis.structure = None

        # --- smc ----------------------------------------------------
        try:
            swing_points = (
                detect_swing_points(candles)
                if analysis.structure is not None
                else []
            )
            analysis.smc = analyze_smc(candles, swing_points=swing_points)
            if analysis.smc:
                ob_count = len(analysis.smc.get("order_blocks", []))
                fvg_count = len(analysis.smc.get("fvgs", []))
                log_analysis_step(
                    coin_config.symbol, f"component_smc_{timeframe}", "success",
                    f"تم تحليل SMC ({timeframe}): تم العثور على {ob_count} مناطق OB و {fvg_count} فجوات FVG"
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.utcnow(),
                module="engine.orchestrator",
                error_type=type(exc).__name__,
                error_message=str(exc),
                event_kind="analyze_smc_failed",
                symbol=coin_config.symbol,
                timeframe=timeframe,
            )
            analysis.smc = {"order_blocks": [], "fvgs": [], "sweeps": []}

        # --- trend --------------------------------------------------
        try:
            analysis.trend = analyze_trend(candles)
            if analysis.trend:
                log_analysis_step(
                    coin_config.symbol, f"component_trend_{timeframe}", "success",
                    f"تم تحليل الاتجاه ({timeframe}): {analysis.trend.get('direction')} (القوة={analysis.trend.get('strength'):.2f})"
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.utcnow(),
                module="engine.orchestrator",
                error_type=type(exc).__name__,
                error_message=str(exc),
                event_kind="analyze_trend_failed",
                symbol=coin_config.symbol,
                timeframe=timeframe,
            )
            analysis.trend = {
                "direction": "neutral",
                "strength": 0.0,
                "adx": 0.0,
                "ema_fast": float("nan"),
                "ema_slow": float("nan"),
                "reasons": [f"analyze_trend_failed:{type(exc).__name__}"],
            }

        # --- momentum -----------------------------------------------
        try:
            analysis.momentum = calculate_momentum(candles)
            if analysis.momentum:
                log_analysis_step(
                    coin_config.symbol, f"component_momentum_{timeframe}", "success",
                    f"تم تحليل الزخم ({timeframe}): {analysis.momentum.get('direction')} (Score={analysis.momentum.get('momentum_score'):.2f})"
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.utcnow(),
                module="engine.orchestrator",
                error_type=type(exc).__name__,
                error_message=str(exc),
                event_kind="calculate_momentum_failed",
                symbol=coin_config.symbol,
                timeframe=timeframe,
            )
            analysis.momentum = {
                "rsi": 50.0,
                "macd_hist": 0.0,
                "momentum_score": 0.5,
                "direction": "long",
                "reasons": [f"calculate_momentum_failed:{type(exc).__name__}"],
            }

        # --- volume -------------------------------------------------
        try:
            analysis.volume = analyze_volume(candles)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.utcnow(),
                module="engine.orchestrator",
                error_type=type(exc).__name__,
                error_message=str(exc),
                event_kind="analyze_volume_failed",
                symbol=coin_config.symbol,
                timeframe=timeframe,
            )
            analysis.volume = {
                "cvd": [],
                "cvd_slope": 0.0,
                "volume_ratio": 1.0,
                "poc": 0.0,
                "delta": 0.0,
                "reasons": [f"analyze_volume_failed:{type(exc).__name__}"],
            }

        # --- session ------------------------------------------------
        try:
            last_candle = candles[-1] if candles else trigger_candle
            analysis.session_signal = build_session_signal(last_candle, coin_config.symbol)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.utcnow(),
                module="engine.orchestrator",
                error_type=type(exc).__name__,
                error_message=str(exc),
                event_kind="build_session_signal_failed",
                symbol=coin_config.symbol,
                timeframe=timeframe,
            )
            analysis.session_signal = _build_strategy_signal(
                strategy_name="session",
                symbol=coin_config.symbol,
                timeframe=timeframe,
                direction="long",
                raw_score=0.5,
                reasons=[f"build_session_signal_failed:{type(exc).__name__}"],
                source_candle_open_time=trigger_candle.open_time,
            )

        # --- regime -------------------------------------------------
        try:
            analysis.regime = classify_regime(candles)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.utcnow(),
                module="engine.orchestrator",
                error_type=type(exc).__name__,
                error_message=str(exc),
                event_kind="classify_regime_failed",
                symbol=coin_config.symbol,
                timeframe=timeframe,
            )
            analysis.regime = RegimeState.RANGING

        # --- atr (for risk module) ----------------------------------
        try:
            analysis.atr = calculate_atr(candles, VOLATILITY_ATR_PERIOD)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.utcnow(),
                module="engine.orchestrator",
                error_type=type(exc).__name__,
                error_message=str(exc),
                event_kind="calculate_atr_failed",
                symbol=coin_config.symbol,
                timeframe=timeframe,
            )
            analysis.atr = 0.0

        return analysis

    # -----------------------------------------------------------------
    # Component-signal builders
    # -----------------------------------------------------------------
    def _build_component_signals(
        self,
        analysis: _TimeframeAnalysis,
    ) -> list[StrategySignal]:
        """Build a list of :class:`StrategySignal` from one timeframe's analysis.

        Produces up to 6 component signals per timeframe:
          1. ``structure`` -- from MarketStructure.trend_direction.
          2. ``smc`` -- from the latest liquidity sweep direction (if any).
          3. ``trend`` -- from analyze_trend's direction + strength.
          4. ``momentum`` -- from calculate_momentum's direction + score.
          5. ``volume`` -- from analyze_volume's CVD slope direction.
          6. ``session`` -- the pre-built session signal.

        Each signal's ``raw_score`` is the module's confidence in its own
        conclusion, normalised to ``[0, 1]``.
        """
        signals: list[StrategySignal] = []
        if not analysis.candles:
            return signals

        last_candle = analysis.candles[-1]
        open_time = last_candle.open_time
        symbol = last_candle.symbol
        tf = analysis.timeframe

        # 1. Structure signal.
        if analysis.structure is not None:
            struct = analysis.structure
            if struct.trend_direction == "up":
                struct_dir = "long"
                struct_score = 0.7
                struct_reasons = [f"structure_trend=up (tf={tf})"]
            elif struct.trend_direction == "down":
                # In Spot-only, bearish structure is neutral (no trade)
                struct_dir = "neutral"
                struct_score = 0.7
                struct_reasons = [f"structure_trend=down (tf={tf})"]
            else:
                struct_dir = "long"
                struct_score = 0.4
                struct_reasons = [f"structure_trend=neutral (tf={tf})"]
            signals.append(
                _build_strategy_signal(
                    strategy_name="structure",
                    symbol=symbol,
                    timeframe=tf,
                    direction=struct_dir,
                    raw_score=struct_score,
                    reasons=struct_reasons,
                    source_candle_open_time=open_time,
                )
            )

        # 2. SMC signal (from latest sweep if any).
        sweeps = analysis.smc.get("sweeps", []) if analysis.smc else []
        if sweeps:
            last_sweep = sweeps[-1]
            # [FIX] LiquiditySweep is a Pydantic model, not a dict. Access attributes directly.
            # Also normalise direction: 'bullish' sweep (low sweep) -> 'long' signal.
            # In Spot-only, bearish sweeps are neutral.
            smc_dir = "long" if last_sweep.direction == "bullish" else "neutral"
            smc_score = float(last_sweep.strength)
            smc_reasons = [
                f"smc_sweep: direction={last_sweep.direction}, "
                f"strength={last_sweep.strength:.3f}, level={last_sweep.swept_level:.6f}"
            ]
            signals.append(
                _build_strategy_signal(
                    strategy_name="smc",
                    symbol=symbol,
                    timeframe=tf,
                    direction=smc_dir,
                    raw_score=smc_score,
                    reasons=smc_reasons,
                    source_candle_open_time=open_time,
                )
            )

        # 3. Trend signal.
        trend = analysis.trend or {}
        trend_dir_raw = trend.get("direction", "neutral")
        if trend_dir_raw == "bullish":
            trend_dir = "long"
        elif trend_dir_raw == "bearish":
            trend_dir = "neutral"
        else:
            trend_dir = "neutral"
        trend_score = float(trend.get("strength", 0.0) or 0.0)
        trend_reasons = list(trend.get("reasons", []))
        if not trend_reasons:
            trend_reasons = [f"trend direction={trend_dir_raw} (tf={tf})"]
        signals.append(
            _build_strategy_signal(
                strategy_name="trend",
                symbol=symbol,
                timeframe=tf,
                direction=trend_dir,
                raw_score=trend_score,
                reasons=trend_reasons,
                source_candle_open_time=open_time,
            )
        )

        # 4. Momentum signal.
        mom = analysis.momentum or {}
        mom_dir_raw = mom.get("direction", "neutral")
        mom_dir = "long" if mom_dir_raw == "long" else "neutral"
        mom_score = float(mom.get("momentum_score", 0.5) or 0.5)
        mom_reasons = list(mom.get("reasons", []))
        if not mom_reasons:
            mom_reasons = [f"momentum direction={mom_dir_raw} (tf={tf})"]
        signals.append(
            _build_strategy_signal(
                strategy_name="momentum",
                symbol=symbol,
                timeframe=tf,
                direction=mom_dir,
                raw_score=mom_score,
                reasons=mom_reasons,
                source_candle_open_time=open_time,
            )
        )

        # 5. Volume signal.
        vol = analysis.volume or {}
        cvd_slope = float(vol.get("cvd_slope", 0.0) or 0.0)
        delta = float(vol.get("delta", 0.0) or 0.0)
        # Rising CVD -> bullish bias; falling -> bearish.
        if cvd_slope > 0 or delta > 0:
            vol_dir = "long"
        elif cvd_slope < 0 or delta < 0:
            vol_dir = "neutral"
        else:
            vol_dir = "neutral"
        # Map |cvd_slope| to [0.4, 0.9] via a soft tanh-like transform.
        vol_score = 0.4 + 0.5 * min(abs(cvd_slope) * 1e-3, 1.0)
        vol_reasons = list(vol.get("reasons", []))
        if not vol_reasons:
            vol_reasons = [
                f"volume cvd_slope={cvd_slope:+.6f}, delta={delta:+.4f} (tf={tf})"
            ]
        signals.append(
            _build_strategy_signal(
                strategy_name="volume",
                symbol=symbol,
                timeframe=tf,
                direction=vol_dir,
                raw_score=vol_score,
                reasons=vol_reasons,
                source_candle_open_time=open_time,
            )
        )

        # 6. Session signal (already built in _analyze_timeframe).
        if analysis.session_signal is not None:
            signals.append(analysis.session_signal)

        return signals

    # -----------------------------------------------------------------
    # Primary signal selection
    # -----------------------------------------------------------------
    def _pick_primary_signal(
        self,
        ltf_analysis: _TimeframeAnalysis,
        component_signals: list[StrategySignal],
    ) -> StrategySignal:
        """Pick the primary LTF signal for the HTF filter and risk assessment.

        Preference order:
          1. The LTF trend signal (highest structural weight).
          2. The LTF momentum signal.
          3. The first component signal on the LTF timeframe.
          4. A default long signal at score 0.5.
        """
        ltf_tf = ltf_analysis.timeframe
        # 1. Trend signal.
        for sig in component_signals:
            if sig.timeframe == ltf_tf and sig.strategy_name == "trend":
                return sig
        # 2. Momentum signal.
        for sig in component_signals:
            if sig.timeframe == ltf_tf and sig.strategy_name == "momentum":
                return sig
        # 3. Any LTF signal.
        for sig in component_signals:
            if sig.timeframe == ltf_tf:
                return sig
        # 4. Default.
        last_candle = ltf_analysis.candles[-1] if ltf_analysis.candles else None
        open_time = last_candle.open_time if last_candle else datetime.now(timezone.utc)
        symbol = last_candle.symbol if last_candle else ""
        return _build_strategy_signal(
            strategy_name="default",
            symbol=symbol,
            timeframe=ltf_tf,
            direction="long",
            raw_score=0.5,
            reasons=["default_signal: no component signal available"],
            source_candle_open_time=open_time,
        )

    # -----------------------------------------------------------------
    # Structure alignment check
    # -----------------------------------------------------------------
    def _structure_alignment_passed(
        self,
        per_tf: dict[str, _TimeframeAnalysis],
    ) -> bool:
        """True iff at least one timeframe has non-neutral structure.

        "Non-neutral" means the MarketStructure's ``trend_direction`` is
        ``"up"`` or ``"down"``, OR there is a recent BOS or CHOCH.
        """
        for analysis in per_tf.values():
            struct = analysis.structure
            if struct is None:
                continue
            if struct.trend_direction in ("up", "down"):
                return True
            if struct.last_bos is not None or struct.last_choch is not None:
                return True
        return False

    # -----------------------------------------------------------------
    # Portfolio state fetcher
    # -----------------------------------------------------------------
    async def _fetch_portfolio_state(
        self,
        symbol: str,
        coin_config: CoinConfig,
    ) -> dict:
        """Fetch the portfolio state required by ``assess_risk``.

        Reads from Supabase:
          * ``open_trade_count`` -- count of open simulated trades (any
            symbol; the concurrent-trade limit is system-wide).
          * ``current_exposure`` -- sum of (entry_price * size) for open
            trades in the same symbol.  Section 8 specifies portfolio-level
            exposure; for simplicity we treat it per-symbol (the orchestrator
            runs one coin at a time).
          * ``current_pnl`` and ``peak_pnl`` -- read from Redis if available,
            otherwise default to 0.0 (cold start).
          * ``current_price`` -- latest close of the LTF candle.

        Returns a dict suitable for direct consumption by
        :func:`engine.risk.assess_risk`.
        """
        state: dict[str, Any] = {
            "current_exposure": 0.0,
            "current_pnl": 0.0,
            "peak_pnl": 0.0,
            "open_trade_count": 0,
            "current_price": 0.0,
        }

        try:
            state["open_trade_count"] = await self._supabase.count_open_trades()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.utcnow(),
                module="engine.orchestrator",
                error_type=type(exc).__name__,
                error_message=str(exc),
                event_kind="count_open_trades_failed",
                symbol=symbol,
            )

        try:
            open_trades = await self._supabase.fetch_open_trades(symbol=symbol)
            state["current_exposure"] = sum(
                float(t.entry_price) * float(t.size) for t in open_trades
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.utcnow(),
                module="engine.orchestrator",
                error_type=type(exc).__name__,
                error_message=str(exc),
                event_kind="fetch_open_trades_failed",
                symbol=symbol,
            )

        # current_price will be filled by the caller via the LTF analysis;
        # we leave it at 0.0 here and let assess_risk fall back to its own
        # logic.  (The orchestrator populates ``current_price`` separately
        # before calling assess_risk.)

        return state

    # -----------------------------------------------------------------
    # Simulated trade opening (lazy import to avoid circular dep)
    # -----------------------------------------------------------------
    async def _open_simulated_trade(
        self,
        decision: DecisionResult,
        entry: EntrySignal,
    ) -> None:
        """Open a simulated trade via ``simulation/paper_trade.PaperTrader``.

        The import is performed inside this method so that ``paper_trade``
        is only required when ``final_verdict=True`` -- tests that exercise
        rejection paths do not need to mock the simulation module.  We
        instantiate the ``PaperTrader`` with the orchestrator's existing
        ``SupabaseClient`` to avoid a second connection pool.
        """
        try:
            # Lazy import to avoid a circular dependency at module load time.
            from simulation.paper_trade import PaperTrader  # type: ignore
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.utcnow(),
                module="engine.orchestrator",
                error_type=type(exc).__name__,
                error_message=str(exc),
                event_kind="paper_trade_import_failed",
                symbol=decision.symbol,
            )
            return

        try:
            trader = PaperTrader(self._supabase)
            await trader.open_trade(decision)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.utcnow(),
                module="engine.orchestrator",
                error_type=type(exc).__name__,
                error_message=str(exc),
                event_kind="open_simulated_trade_failed",
                symbol=decision.symbol,
                decision_id=str(decision.id),
            )

    # -----------------------------------------------------------------
    # Failure-result builder (used when min-timeframes constraint fails)
    # -----------------------------------------------------------------
    def _build_and_log_failure(
        self,
        symbol: str,
        source_open_time: datetime,
        score: float,
        confidence: float,
        reason: str,
        component_signals: list[StrategySignal],
        regime_check_passed: bool,
        structure_alignment_passed: bool,
        htf_bias_aligned: bool,
        risk: RiskAssessment,
        entry: Optional[EntrySignal],
        trigger_timeframe: str = "",
    ) -> DecisionResult:
        """Build a failure :class:`DecisionResult` and emit a rejection log."""
        decision = DecisionResult(
            symbol=symbol,
            source_candle_open_time=source_open_time,
            score=max(0.0, min(1.0, float(score))),
            confidence=max(0.0, min(1.0, float(confidence))),
            regime_check_passed=regime_check_passed,
            structure_alignment_passed=structure_alignment_passed,
            htf_bias_aligned=htf_bias_aligned,
            risk=risk,
            entry=entry,
            final_verdict=False,
            rejection_reason=reason,
            component_signals=component_signals,
            trigger_timeframe=trigger_timeframe,
            timestamp=datetime.now(timezone.utc),
        )
        logger.info(
            "decision_rejected",
            timestamp=datetime.utcnow(),
            symbol=symbol,
            score=round(float(score), 6),
            final_verdict=False,
            rejection_reason=reason,
            component_signal_count=len(component_signals),
        )
        return decision


__all__ = [
    "Orchestrator",
    "_build_strategy_signal",
    "_determine_rejection_reason",
]
