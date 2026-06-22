"""
محرك الأدلة — CT V4.0
آخر طبقة ذكاء قبل أي قرار تداول. يجمع كل الإشارات من كل الأطر الزمنية
ويصدر قراراً واحداً. يجيب على سؤال: "هل هناك أدلة كافية لتبرير الصفقة؟"
"""
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional

from core.base import BaseEngine
from core.events import (
    EvidenceEvent, SignalEvent, RiskEvent,
    WhaleEvent, EventBus, HealthEvent, HealthStatus, AlertEvent, AlertLevel
)
from core.types import EvidenceResult, MarketAnalysis
from core.errors import EvidenceError
from config.constants import EVIDENCE_THRESHOLD, HIGH_CONFIDENCE, SESSION_WEIGHTS
from strategies import StrategySignal

logger = logging.getLogger("evidence_engine")


class EvidenceEngine(BaseEngine):
    """الحكم النهائي لكل الإشارات. حارس رأس المال."""

    # أوزان الأدلة — CT V4.0
    WEIGHTS = {
        "trend": 0.20,                       # قوة الاتجاه
        "momentum": 0.12,                    # الزخم
        "strategy_alignment": 0.18,          # توافق الاستراتيجيات
        "multi_timeframe_confirmation": 0.18, # تأكيد الأطر الزمنية المتعددة ★
        "risk_safety": 0.15,                 # سلامة المخاطر
        "whale_flow": 0.05,                  # تدفق الحيتان
        "session_strength": 0.05,            # قوة الجلسة
        "historical_success": 0.07,          # نجاح تاريخي
    }

    # عتبات الأطر الزمنية لتأكيد متعدد
    TIMEFRAME_WEIGHTS: Dict[str, float] = {
        "1d": 1.0,
        "4h": 0.9,
        "1h": 0.7,
        "15m": 0.5,
        "5m": 0.3,
        "1m": 0.2,
    }

    def __init__(self, event_bus: EventBus):
        super().__init__("evidence_engine")
        self.event_bus = event_bus
        self._latest_evidence: Dict[str, EvidenceResult] = {}
        self._latest_signals: Dict[str, Dict[str, List[StrategySignal]]] = {}  # symbol → timeframe → signals
        self._latest_whale: Dict[str, list] = {}
        self._risk_approvals: Dict[str, RiskEvent] = {}
        self._historical_scores: Dict[str, float] = {}  # strategy → historical success rate
        self.decision_count: int = 0

    async def initialize(self) -> None:
        await self.event_bus.subscribe("SignalEvent", self._on_signal)
        await self.event_bus.subscribe("WhaleEvent", self._on_whale)
        await self.event_bus.subscribe("RiskEvent", self._on_risk)
        self.logger.info("[الأدلة] تم التهيئة — جاهز لتقييم الإشارات")

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._heartbeat_loop())
        self.logger.info("[الأدلة] تم البدء")

    async def stop(self) -> None:
        self._running = False
        self.logger.info("[الأدلة] تم الإيقاف")

    async def _on_signal(self, event: SignalEvent):
        """تجميع إشارات الاستراتيجيات."""
        symbol_signals = self._latest_signals.setdefault(event.symbol, {})
        timeframe = "unknown"
        signals_list = symbol_signals.setdefault(timeframe, [])
        signals_list.append(event)
        if len(signals_list) > 20:
            symbol_signals[timeframe] = signals_list[-20:]

    async def _on_whale(self, event: WhaleEvent):
        """تجميع أحداث الحيتان."""
        self._latest_whale.setdefault(event.symbol, []).append(event)
        if len(self._latest_whale[event.symbol]) > 10:
            self._latest_whale[event.symbol] = self._latest_whale[event.symbol][-10:]

    async def _on_risk(self, event: RiskEvent):
        """تتبع موافقات المخاطر."""
        self._risk_approvals[event.stop_loss_distance or "global"] = event

    # ── Core Evaluation ────────────────────────────────────────

    async def evaluate(
        self,
        symbol: str,
        analyses: Dict[str, MarketAnalysis],
        signals_by_timeframe: Dict[str, List[StrategySignal]],
        whale_events: list = None,
    ) -> EvidenceResult:
        """
        التقييم الأساسي: تجميع كل الأدلة من كل الأطر الزمنية في قرار واحد.

        Args:
            symbol: رمز العملة
            analyses: تحليلات كل إطار زمني {"15m": Analysis, "1h": Analysis, ...}
            signals_by_timeframe: إشارات كل إطار {"15m": [Signal, ...], "1h": [...]}
            whale_events: أحداث الحيتان الاختيارية

        Returns:
            EvidenceResult بالقرار النهائي: BUY / SELL / HOLD / IGNORE
        """
        evidence = {}
        conflicts: List[str] = []

        # اختيار التحليل الأساسي (الإطار الأعلى أولوية)
        primary_analysis = self._select_primary_analysis(analyses)

        # 1. دليل الاتجاه (0–100)
        trend_score = self._score_trend(analyses)
        evidence["market_trend"] = trend_score

        # 2. دليل الزخم (0–100)
        momentum_score = self._score_momentum(analyses)
        evidence["momentum"] = momentum_score

        # 3. توافق الاستراتيجيات (0–100)
        strategy_score = self._score_strategy_alignment(signals_by_timeframe, analyses)
        evidence["strategy_alignment"] = strategy_score

        # 4. تأكيد الأطر الزمنية المتعددة ★ (0–100)
        mtf_score = self._score_multi_timeframe(signals_by_timeframe, analyses)
        evidence["multi_timeframe_confirmation"] = mtf_score

        # 5. سلامة المخاطر (0–100)
        risk_score = self._score_risk_safety()
        evidence["risk_score"] = risk_score

        # 6. تدفق الحيتان (0–100)
        whale_score = self._score_whale_flow(whale_events or [], primary_analysis)
        evidence["whale_flow"] = whale_score

        # 7. قوة الجلسة (0–100)
        session_score = self._score_session()
        evidence["session_strength"] = session_score

        # 8. نجاح تاريخي (0–100)
        hist_score = self._get_historical_score(signals_by_timeframe)
        evidence["historical_success_rate"] = hist_score

        # كشف التعارضات
        conflicts = self._detect_conflicts(analyses, signals_by_timeframe, evidence)

        # حساب النتيجة النهائية
        final_score = (
            trend_score * self.WEIGHTS["trend"]
            + momentum_score * self.WEIGHTS["momentum"]
            + strategy_score * self.WEIGHTS["strategy_alignment"]
            + mtf_score * self.WEIGHTS["multi_timeframe_confirmation"]
            + risk_score * self.WEIGHTS["risk_safety"]
            + whale_score * self.WEIGHTS["whale_flow"]
            + session_score * self.WEIGHTS["session_strength"]
            + hist_score * self.WEIGHTS["historical_success"]
        )

        final_score = round(final_score, 1)

        # عقوبة التعارضات
        if len(conflicts) >= 2:
            final_score *= 0.80
            self.logger.info(f"[الأدلة] عقوبة تعارض (−20%): {len(conflicts)} تعارض")
        if len(conflicts) >= 4:
            final_score *= 0.75  # تراكمي: 0.80 × 0.75 = 0.60
            self.logger.warning(f"[الأدلة] عقوبة تعارض شديد (−40%): {len(conflicts)} تعارض")

        final_score = round(final_score, 1)

        # القرار
        risk_approved = risk_score >= 60
        decision = self._make_decision(final_score, conflicts, primary_analysis, risk_approved)

        # بناء المنطق بالعربية
        reasoning = self._build_reasoning(
            symbol, analyses, signals_by_timeframe, evidence, conflicts, final_score, decision
        )

        # إصدار نتيجة
        result = EvidenceResult(
            symbol=symbol,
            decision=decision,
            confidence=final_score,
            final_score=final_score,
            evidence=evidence,
            conflicts=conflicts,
            reasoning=reasoning,
            risk_approved=risk_approved,
        )

        self._latest_evidence[symbol] = result
        self.decision_count += 1

        self.logger.info(
            f"[تقييم] {symbol} | النتيجة: {final_score:.1f} | القرار: {decision} | "
            f"اتجاه: {primary_analysis.trend_direction} | زخم: {primary_analysis.momentum:.0f} | "
            f"تأكيد_متعدد: {mtf_score:.0f} | تعارضات: {len(conflicts)}"
        )

        if decision in ("HOLD", "IGNORE") and conflicts:
            main_conflict = conflicts[0]
            self.logger.info(f"[الأدلة] تم رفض الصفقة: {main_conflict}")

        # نشر حدث الأدلة
        await self.event_bus.publish(EvidenceEvent(
            symbol=symbol,
            decision=decision,
            confidence=final_score,
            final_score=final_score,
            evidence=evidence,
            conflicts=conflicts,
            reasoning=reasoning,
            risk_approved=risk_approved,
        ))

        return result

    # ── Analysis Selection ────────────────────────────────────

    def _select_primary_analysis(self, analyses: Dict[str, MarketAnalysis]) -> MarketAnalysis:
        """اختيار التحليل الأساسي — الإطار الأعلى المتاح."""
        priority = ["1d", "4h", "1h", "15m", "5m", "1m"]
        for tf in priority:
            if tf in analyses and analyses[tf]:
                return analyses[tf]
        # أول تحليل متاح
        if analyses:
            return list(analyses.values())[0]
        return MarketAnalysis()

    # ── Scoring Methods ───────────────────────────────────────

    def _score_trend(self, analyses: Dict[str, MarketAnalysis]) -> float:
        """تقييم الاتجاه عبر كل الأطر الزمنية."""
        if not analyses:
            return 30.0

        scores = []
        for tf, a in analyses.items():
            if not a:
                continue
            weight = self.TIMEFRAME_WEIGHTS.get(tf, 0.5)
            if a.regime == "TRENDING" and a.trend_direction in ("UP", "DOWN"):
                scores.append(a.trend_strength * weight)
            elif a.regime == "TRENDING":
                scores.append(min(a.trend_strength, 60) * weight)
            else:
                scores.append(max(0, a.trend_strength * 0.5) * weight)

        if not scores:
            return 30.0

        return round(sum(scores) / sum(self.TIMEFRAME_WEIGHTS.get(tf, 0.5) for tf in analyses if analyses[tf]), 1)

    def _score_momentum(self, analyses: Dict[str, MarketAnalysis]) -> float:
        """تقييم الزخم عبر كل الأطر الزمنية."""
        if not analyses:
            return 40.0

        momentum_values = []
        for tf, a in analyses.items():
            if not a:
                continue
            weight = self.TIMEFRAME_WEIGHTS.get(tf, 0.5)
            if a.momentum > 70:
                momentum_values.append(min(100, a.momentum * 0.9) * weight)
            else:
                momentum_values.append(a.momentum * weight)

        if not momentum_values:
            return 40.0

        weights_sum = sum(self.TIMEFRAME_WEIGHTS.get(tf, 0.5) for tf in analyses if analyses[tf])
        return round(sum(momentum_values) / max(weights_sum, 0.001), 1)

    def _score_strategy_alignment(
        self, signals_by_timeframe: Dict[str, List[StrategySignal]],
        analyses: Dict[str, MarketAnalysis]
    ) -> float:
        """تقييم توافق الاستراتيجيات عبر الأطر الزمنية."""
        all_signals = []
        for tf_signals in signals_by_timeframe.values():
            all_signals.extend(tf_signals)

        if not all_signals:
            return 25.0  # لا إشارات — متعادل-منخفض

        # إشارات الشراء
        buy_signals = [s for s in all_signals if s.action == "BUY"]
        sell_signals = [s for s in all_signals if s.action == "SELL"]

        if buy_signals:
            avg_confidence = sum(s.confidence for s in buy_signals) / len(buy_signals)
            # مكافأة الاستراتيجيات المتعددة
            unique_strategies = len(set(s.strategy_name for s in buy_signals))
            bonus = min(20, unique_strategies * 7)
            return min(100, avg_confidence + bonus)
        elif sell_signals:
            avg_confidence = sum(s.confidence for s in sell_signals) / len(sell_signals)
            unique_strategies = len(set(s.strategy_name for s in sell_signals))
            bonus = min(20, unique_strategies * 7)
            return min(100, avg_confidence + bonus)

        return 20.0

    def _score_multi_timeframe(
        self, signals_by_timeframe: Dict[str, List[StrategySignal]],
        analyses: Dict[str, MarketAnalysis]
    ) -> float:
        """
        تقييم تأكيد الأطر الزمنية المتعددة ★.
        إشارة من إطارين زمنيين مختلفين تزيد الثقة بشكل كبير.
        """
        if not signals_by_timeframe or len(signals_by_timeframe) < 2:
            return 30.0

        # جمع كل إشارات الشراء/البيع حسب الإطار
        buy_timeframes = set()
        sell_timeframes = set()

        for tf, signals in signals_by_timeframe.items():
            for s in signals:
                if s.action == "BUY":
                    buy_timeframes.add(tf)
                elif s.action == "SELL":
                    sell_timeframes.add(tf)

        max_aligned = max(len(buy_timeframes), len(sell_timeframes))

        if max_aligned >= 3:
            return 95.0  # 3+ أطر متوافقة — تأكيد قوي جداً
        elif max_aligned == 2:
            # إطارين — جيد جداً
            aligned_tfs = buy_timeframes if len(buy_timeframes) >= len(sell_timeframes) else sell_timeframes
            tf_weights = [self.TIMEFRAME_WEIGHTS.get(t, 0.5) for t in aligned_tfs]
            return 70.0 + sum(tf_weights) * 10  # 70–90
        elif max_aligned == 1:
            tf_weight = self.TIMEFRAME_WEIGHTS.get(
                list(buy_timeframes or sell_timeframes)[0], 0.5
            )
            return 40.0 + tf_weight * 20  # 40–60

        return 30.0

    def _score_risk_safety(self) -> float:
        """تقييم سلامة المخاطر — محسوب فعلياً من حالة النظام."""
        score = 70.0  # أساس

        # عقوبات من حالة المخاطر الداخلية
        if hasattr(self, '_consecutive_losses'):
            cl = getattr(self, '_consecutive_losses', 0)
            score -= cl * 15

        if hasattr(self, '_trading_blocked'):
            if getattr(self, '_trading_blocked', False):
                score = 0.0

        return max(0.0, min(100.0, score))

    def _score_whale_flow(self, whale_events: list, analysis: MarketAnalysis) -> float:
        """تقييم تدفق الحيتان."""
        if not whale_events:
            return 50.0

        buy_count = sum(1 for w in whale_events if w.direction == "IN" and w.is_market_trade)
        total = len(whale_events)
        if total == 0:
            return 50.0

        ratio = buy_count / total
        if analysis.trend_direction == "UP":
            return 50 + ratio * 50
        elif analysis.trend_direction == "DOWN":
            return 100 - ratio * 50
        return 50 + (ratio - 0.5) * 30

    def _score_session(self) -> float:
        """تقييم قوة جلسة التداول الحالية."""
        hour = datetime.utcnow().hour
        if 7 <= hour < 9:    # افتتاح لندن
            return 75.0
        if 9 <= hour < 16:   # لندن + نيويورك (تداخل)
            return 85.0
        if 16 <= hour < 20:  # نيويورك
            return 65.0
        if 0 <= hour < 7:    # آسيا
            return 45.0
        return 35.0  # نهاية أسبوع / نشاط منخفض

    def _get_historical_score(self, signals_by_timeframe: Dict[str, List[StrategySignal]]) -> float:
        """متوسط النجاح التاريخي للاستراتيجيات النشطة."""
        all_signals = []
        for tf_signals in signals_by_timeframe.values():
            all_signals.extend(tf_signals)

        if not all_signals:
            return 50.0

        strategies = set(s.strategy_name for s in all_signals if s.strategy_name)
        if not strategies:
            return 50.0

        scores = [self._historical_scores.get(s, 50.0) for s in strategies]
        return sum(scores) / len(scores)

    def update_historical_score(self, strategy_name: str, score: float):
        """تحديث معدل النجاح المكتسب لاستراتيجية."""
        old = self._historical_scores.get(strategy_name, 50.0)
        self._historical_scores[strategy_name] = old * 0.8 + score * 0.2
        self.logger.info(f"[الأدلة] تحديث المعدل التاريخي لـ {strategy_name}: {old:.0f} → {self._historical_scores[strategy_name]:.0f}")

    # ── Conflict Detection ────────────────────────────────────

    def _detect_conflicts(
        self,
        analyses: Dict[str, MarketAnalysis],
        signals_by_timeframe: Dict[str, List[StrategySignal]],
        evidence: dict,
    ) -> list:
        """كشف التعارضات بين الإشارات والتحليلات."""
        conflicts = []
        primary = self._select_primary_analysis(analyses)

        # جمع إشارات الشراء والبيع
        all_signals = []
        for tf_signals in signals_by_timeframe.values():
            all_signals.extend(tf_signals)

        buy_signals = [s for s in all_signals if s.action == "BUY"]
        sell_signals = [s for s in all_signals if s.action == "SELL"]

        # اتجاه صاعد لكن زخم منخفض
        if primary.trend_direction == "UP" and primary.momentum < 30:
            conflicts.append("اتجاه صاعد لكن الزخم منخفض — تباعد محتمل")

        # إشارة شراء في سوق متذبذب
        if buy_signals and primary.regime == "RANGING":
            conflicts.append("إشارة شراء في سوق متذبذب — موثوقية منخفضة")

        # تقلب مرتفع مع إشارة شراء
        if primary.volatility > 75 and buy_signals:
            conflicts.append("تقلب مرتفع مع إشارة شراء — مخاطرة مرتفعة")

        # إشارة شراء ضد اتجاه هابط
        if primary.trend_direction == "DOWN" and buy_signals:
            conflicts.append("إشارة شراء ضد اتجاه هابط — مخاطرة عكس الاتجاه")

        # سيولة منخفضة
        if primary.liquidity_score < 30:
            conflicts.append(f"ضعف السيولة ({primary.liquidity_score:.0f}%) — مخاطرة تنفيذ")

        # كسر هيكل السوق
        if primary.structure.get("break_of_structure"):
            conflicts.append("كسر هيكل السوق — خطر إلغاء الاتجاه")

        # تعارض بين الأطر الزمنية — إشارات متضاربة
        buy_tfs = set()
        sell_tfs = set()
        for tf, signals in signals_by_timeframe.items():
            for s in signals:
                if s.action == "BUY":
                    buy_tfs.add(tf)
                elif s.action == "SELL":
                    sell_tfs.add(tf)
        if buy_tfs and sell_tfs:
            conflicts.append(
                f"تعارض بين الأطر: شراء في {sorted(buy_tfs)} وبـيع في {sorted(sell_tfs)}"
            )

        return conflicts

    # ── Decision Logic ────────────────────────────────────────

    def _make_decision(
        self, score: float, conflicts: list,
        analysis: MarketAnalysis, risk_approved: bool
    ) -> str:
        """منطق القرار النهائي — لا افتراضات."""
        if not risk_approved:
            return "HOLD"
        if len(conflicts) >= 4:
            return "IGNORE"

        direction = getattr(analysis, 'trend_direction', 'NONE')
        momentum = getattr(analysis, 'momentum', 0)

        if score >= EVIDENCE_THRESHOLD:
            if direction == "UP" and momentum > 40:
                return "BUY"
            if direction == "DOWN" and momentum > 40:
                return "SELL"
            return "HOLD"  # لا اتجاه واضح → لا قرار

        if score >= 65:
            if direction == "DOWN":
                return "SELL"
            if direction == "UP":
                return "BUY"

        if score >= 50:
            return "HOLD"
        return "IGNORE"

    def _build_reasoning(
        self,
        symbol: str,
        analyses: Dict[str, MarketAnalysis],
        signals_by_timeframe: Dict[str, List[StrategySignal]],
        evidence: dict,
        conflicts: list,
        score: float,
        decision: str,
    ) -> str:
        """بناء نص المنطق بالعربية."""
        primary = self._select_primary_analysis(analyses)

        # ترجمة القرار
        decision_ar = {
            "BUY": "شراء ✓",
            "SELL": "بيع ✓",
            "HOLD": "انتظار",
            "IGNORE": "تجاهل",
        }.get(decision, decision)

        # جمع الاستراتيجيات المستخدمة
        all_signals = []
        for tf_signals in signals_by_timeframe.values():
            all_signals.extend(tf_signals)
        strategy_names = sorted(set(s.strategy_name for s in all_signals if s.strategy_name))

        parts = [
            f"السوق: {primary.regime} | الاتجاه: {primary.trend_direction}",
            f"الزخم: {primary.momentum:.0f}% | التقلب: {primary.volatility:.0f}% | السيولة: {primary.liquidity_score:.0f}%",
        ]

        # الأطر الزمنية النشطة
        active_tfs = sorted(analyses.keys())
        if active_tfs:
            parts.append(f"الأطر: {', '.join(active_tfs)}")

        # تأكيد متعدد الأطر
        mtf = evidence.get("multi_timeframe_confirmation", 0)
        if mtf >= 70:
            parts.append(f"تأكيد متعدد الأطر: {mtf:.0f}% ✓✓")
        elif mtf >= 50:
            parts.append(f"تأكيد متعدد الأطر: {mtf:.0f}% ✓")

        parts.append(f"النتيجة: {score:.1f}/100 | القرار: {decision_ar}")

        if strategy_names:
            parts.append(f"الاستراتيجيات: {', '.join(strategy_names)}")

        if conflicts:
            main_conflicts = conflicts[:3]
            conflict_text = " | ".join(main_conflicts)
            parts.append(f"⚠️ تعارضات ({len(conflicts)}): {conflict_text}")

        return " | ".join(parts)

    # ── Public Helpers ────────────────────────────────────────

    def get_latest_evidence(self, symbol: str) -> Optional[EvidenceResult]:
        """آخر نتيجة أدلة لرمز محدد."""
        return self._latest_evidence.get(symbol)

    # ── Health ────────────────────────────────────────────────

    async def _heartbeat_loop(self):
        while self._running:
            await self.event_bus.publish(HealthEvent(
                engine=self.name,
                status=HealthStatus.HEALTHY,
                latency_ms=0,
                error_rate=0,
            ))
            await asyncio.sleep(5)
