"""
استراتيجية تتبع الاتجاه — Trend Following
تتداول في اتجاه الاتجاه القائم. الأفضل في الأسواق ذات الاتجاه الواضح.
تتجنب الأسواق المتذبذبة والعشوائية.
الأطر المدعومة: 15 دقيقة، 1 ساعة، 4 ساعات، يومي
"""
from strategies import BaseStrategy, StrategySignal
from core.types import MarketAnalysis
from core.strategy_contract import StrategyMeta


class TrendFollowingStrategy(BaseStrategy):
    meta = StrategyMeta(
        name="تتبع_الاتجاه",
        version="1.0.0",
        description="استراتيجية تتبع الاتجاه — تتداول في اتجاه الاتجاه القائم. تتجنب الأسواق المتذبذبة.",
        min_confidence=65.0,
        supported_timeframes=["15m", "1h", "4h", "1d"],
        suitable_regimes=["TRENDING"],
        required_inputs=["trend_direction", "trend_strength", "momentum", "volatility", "liquidity_score"],
    )

    async def evaluate(self, analysis: MarketAnalysis) -> StrategySignal:
        signal = StrategySignal(
            symbol=analysis.symbol,
            strategy_name=self.meta.name,
            action="HOLD",
            confidence=0.0,
            score_breakdown={},
            reasoning="",
        )

        # تحقق: هل نظام السوق مناسب لهذه الاستراتيجية؟
        if not self.is_suitable_for_regime(analysis.regime):
            signal.reasoning = f"نظام السوق {analysis.regime} غير مناسب لـ {self.meta.name} — الاستراتيجية مناسبة لـ {self.meta.suitable_regimes}"
            signal.confidence = 5.0
            return signal

        # لا نتداول إلا في الأسواق ذات الاتجاه
        if analysis.regime not in ("TRENDING",):
            signal.reasoning = f"السوق في حالة {analysis.regime} — استراتيجية تتبع الاتجاه لا تعمل في هذه الحالة"
            signal.confidence = 10.0
            return signal

        # ثقة منخفضة — لا نتداول
        if analysis.confidence < self.meta.min_confidence:
            signal.reasoning = f"ثقة التحليل منخفضة ({analysis.confidence:.0f}%) — تخطي"
            signal.confidence = 15.0
            return signal

        score = 0.0

        # قوة الاتجاه (40%)
        trend_component = analysis.trend_strength * 0.40
        score += trend_component

        # تأكيد الزخم (25%)
        momentum_component = 0
        if analysis.trend_direction == "UP" and analysis.momentum > 50:
            momentum_component = 25
            score += 25
        elif analysis.trend_direction == "DOWN" and analysis.momentum > 50:
            momentum_component = 25
            score += 25

        # تأكيد الهيكل (20%)
        structure = analysis.structure
        structure_component = 0
        if analysis.trend_direction == "UP" and structure.get("higher_highs") and structure.get("higher_lows"):
            structure_component = 20
            score += 20
        elif analysis.trend_direction == "DOWN" and not structure.get("higher_highs") and not structure.get("higher_lows"):
            structure_component = 20
            score += 20

        # السيولة (15%)
        liquidity_component = 0
        if analysis.liquidity_score > 60:
            liquidity_component = 15
            score += 15

        signal.confidence = min(100, score)
        signal.score_breakdown = {
            "قوة_الاتجاه": round(trend_component, 1),
            "تأكيد_الزخم": momentum_component,
            "تأكيد_الهيكل": structure_component,
            "السيولة": liquidity_component,
        }

        # القرار
        if analysis.trend_direction == "UP" and signal.confidence >= 70:
            signal.action = "BUY"
            signal.reasoning = (
                f"اتجاه صاعد قوي — قوة الاتجاه {analysis.trend_strength:.0f}%، "
                f"زخم {analysis.momentum:.0f}%، سيولة {analysis.liquidity_score:.0f}%"
            )
        elif analysis.trend_direction == "DOWN" and signal.confidence >= 70:
            signal.action = "SELL"
            signal.reasoning = (
                f"اتجاه هابط قوي — قوة الاتجاه {analysis.trend_strength:.0f}%، "
                f"زخم {analysis.momentum:.0f}%، سيولة {analysis.liquidity_score:.0f}%"
            )
        else:
            signal.action = "HOLD"
            signal.reasoning = f"ثقة غير كافية ({signal.confidence:.0f}%) — انتظار تأكيد أقوى"

        return signal
