"""
استراتيجية الاختراق — Breakout
تتداول الاختراقات المؤكدة بحجم تداول مرتفع.
تحتاج حالة اختراق صالحة وسيولة عالية.
الأطر المدعومة: 5 دقائق، 15 دقيقة، 1 ساعة
"""
from strategies import BaseStrategy, StrategySignal
from core.types import MarketAnalysis


class BreakoutStrategy(BaseStrategy):
    name = "الاختراق"
    supported_timeframes = ["5m", "15m", "1h"]

    async def evaluate(self, analysis: MarketAnalysis) -> StrategySignal:
        signal = StrategySignal(
            symbol=analysis.symbol,
            strategy_name=self.name,
            action="HOLD",
            confidence=0.0,
            score_breakdown={},
            reasoning="",
        )

        # اختراق غير صالح — لا نتداول
        if analysis.breakout_state != "VALID":
            signal.reasoning = f"لا يوجد اختراق مؤكد — الحالة: {analysis.breakout_state}"
            signal.confidence = 5.0
            return signal

        # السوق غير مناسب للاختراق
        if analysis.regime not in ("TRENDING", "VOLATILE"):
            signal.reasoning = f"السوق في حالة {analysis.regime} — الاختراقات غير موثوقة في هذا النمط"
            signal.confidence = 20.0
            return signal

        score = 0.0

        # جودة الاختراق (35%)
        breakout_component = 35
        score += 35

        # تأكيد الحجم (25%)
        volume_component = 0
        if analysis.liquidity_score > 50:
            volume_component = 25
            score += 25

        # الزخم (20%)
        momentum_component = 0
        if analysis.momentum > 60:
            momentum_component = 20
            score += 20
        elif analysis.momentum > 40:
            momentum_component = 10
            score += 10

        # توافق الاتجاه (20%)
        trend_component = 0
        if analysis.trend_direction == "UP":
            trend_component = 20
            score += 20
        elif analysis.trend_direction == "DOWN":
            trend_component = 10  # اختراق عكس الاتجاه — درجة أقل
            score += 10

        signal.confidence = min(100, score)
        signal.score_breakdown = {
            "جودة_الاختراق": breakout_component,
            "تأكيد_الحجم": volume_component,
            "الزخم": momentum_component,
            "توافق_الاتجاه": trend_component,
        }

        if signal.confidence >= 75:
            signal.action = "BUY"
            signal.reasoning = (
                f"اختراق مؤكد — سيولة {analysis.liquidity_score:.0f}%، "
                f"زخم {analysis.momentum:.0f}%، توافق مع الاتجاه"
            )
        elif signal.confidence >= 60 and analysis.trend_direction == "DOWN":
            signal.action = "SELL"
            signal.reasoning = (
                f"اختراق هابط — زخم هبوطي {analysis.momentum:.0f}%، "
                f"حجم تداول مرتفع"
            )
        else:
            signal.action = "HOLD"
            signal.reasoning = f"ثقة الاختراق غير كافية ({signal.confidence:.0f}%) — انتظار تأكيد أقوى"

        return signal
