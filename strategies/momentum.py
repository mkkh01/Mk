"""
استراتيجية الزخم — Momentum
تتداول الزخم الاتجاهي القوي مع تأكيد الحجم.
الأفضل في الأسواق ذات الاتجاه أو المتقلبة مع زخم مرتفع.
الأطر المدعومة: 1 دقيقة، 5 دقائق، 15 دقيقة، 1 ساعة
"""
from strategies import BaseStrategy, StrategySignal
from core.types import MarketAnalysis
from core.strategy_contract import StrategyMeta


class MomentumStrategy(BaseStrategy):
    meta = StrategyMeta(
        name="الزخم",
        version="1.0.0",
        description="استراتيجية الزخم — تتداول الزخم الاتجاهي القوي مع تأكيد الحجم.",
        min_confidence=65.0,
        supported_timeframes=["1m", "5m", "15m", "1h"],
        suitable_regimes=["TRENDING", "CHOPPY"],
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

        # تحقق: هل نظام السوق مناسب؟
        if not self.is_suitable_for_regime(analysis.regime):
            signal.reasoning = f"نظام السوق {analysis.regime} غير مناسب لـ {self.meta.name}"
            signal.confidence = 5.0
            return signal

        # الزخم أقل من الحد الأدنى — لا نتداول
        if analysis.momentum < 60:
            signal.reasoning = f"زخم منخفض ({analysis.momentum:.0f}%) — لا توجد قوة دافعة كافية"
            signal.confidence = 10.0
            return signal

        # تجنب الأسواق العشوائية
        if analysis.regime == "CHOPPY":
            signal.reasoning = "السوق عشوائي — الزخم غير موثوق في هذه الحالة"
            signal.confidence = 5.0
            return signal

        score = 0.0

        # قوة الزخم (40%)
        momentum_component = min(40, analysis.momentum * 0.4)
        score += momentum_component

        # توافق الاتجاه (25%)
        trend_component = 0
        if analysis.trend_direction in ("UP", "DOWN") and analysis.trend_strength > 50:
            trend_component = 25
            score += 25
        elif analysis.trend_direction in ("UP", "DOWN"):
            trend_component = 15
            score += 15

        # الحجم / السيولة (20%)
        volume_component = 0
        if analysis.liquidity_score > 50:
            volume_component = 20
            score += 20
        elif analysis.liquidity_score > 30:
            volume_component = 10
            score += 10

        # ضوضاء منخفضة (15%)
        noise_component = 0
        if analysis.noise_level < 50:
            noise_component = 15
            score += 15
        elif analysis.noise_level < 70:
            noise_component = 8
            score += 8

        signal.confidence = min(100, score)
        signal.score_breakdown = {
            "قوة_الزخم": round(momentum_component, 1),
            "توافق_الاتجاه": trend_component,
            "الحجم": volume_component,
            "ضوضاء_منخفضة": noise_component,
        }

        if signal.confidence >= 75:
            if analysis.trend_direction == "UP":
                signal.action = "BUY"
                signal.reasoning = (
                    f"زخم صاعد قوي ({analysis.momentum:.0f}%) — "
                    f"تأكيد من الاتجاه، سيولة {analysis.liquidity_score:.0f}%"
                )
            elif analysis.trend_direction == "DOWN":
                signal.action = "SELL"
                signal.reasoning = (
                    f"زخم هابط قوي ({analysis.momentum:.0f}%) — "
                    f"تأكيد من الاتجاه، سيولة {analysis.liquidity_score:.0f}%"
                )
            else:
                signal.action = "HOLD"
                signal.reasoning = f"زخم قوي ({analysis.momentum:.0f}%) لكن بدون اتجاه واضح"
        elif signal.confidence >= 65 and analysis.trend_direction == "UP":
            signal.action = "BUY"
            signal.reasoning = (
                f"زخم صاعد متوسط ({analysis.momentum:.0f}%) — "
                f"اتجاه صاعد يدعم الصفقة"
            )
        elif signal.confidence >= 65 and analysis.trend_direction == "DOWN":
            signal.action = "SELL"
            signal.reasoning = (
                f"زخم هابط متوسط ({analysis.momentum:.0f}%) — "
                f"اتجاه هابط يدعم الصفقة"
            )
        else:
            signal.action = "HOLD"
            signal.reasoning = f"ثقة الزخم غير كافية ({signal.confidence:.0f}%) — انتظار زخم أقوى"

        return signal
