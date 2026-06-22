"""
استراتيجية الارتداد نحو المتوسط — Mean Reversion
تتداول عودة السعر إلى المتوسط في الأسواق المتذبذبة.
الأفضل في الأسواق ذات النطاق المحدد. تتجنب الاتجاهات القوية.
الأطر المدعومة: 5 دقائق، 15 دقيقة
"""
from strategies import BaseStrategy, StrategySignal
from core.types import MarketAnalysis


class MeanReversionStrategy(BaseStrategy):
    name = "ارتداد_المتوسط"
    supported_timeframes = ["5m", "15m"]

    async def evaluate(self, analysis: MarketAnalysis) -> StrategySignal:
        signal = StrategySignal(
            symbol=analysis.symbol,
            strategy_name=self.name,
            action="HOLD",
            confidence=0.0,
            score_breakdown={},
            reasoning="",
        )

        # الأفضل في الأسواق المتذبذبة
        if analysis.regime not in ("RANGING",):
            signal.reasoning = (
                f"السوق في حالة {analysis.regime} — "
                f"استراتيجية الارتداد لا تعمل بشكل موثوق في الأسواق ذات الاتجاه"
            )
            signal.confidence = 10.0
            return signal

        # تجنب التقلب الشديد
        if analysis.volatility > 80:
            signal.reasoning = f"تقلب مرتفع جداً ({analysis.volatility:.0f}%) — المخاطرة عالية"
            signal.confidence = 5.0
            return signal

        score = 0.0

        # نظام النطاق المحدد (35%)
        ranging_component = 35
        score += 35

        # زخم منخفض — مرشح للارتداد (25%)
        momentum_component = 0
        if analysis.momentum < 40:
            momentum_component = 25
            score += 25
        elif analysis.momentum < 55:
            momentum_component = 15
            score += 15

        # مستوى الضوضاء — نطاق نظيف مفضل (20%)
        noise_component = 0
        if analysis.noise_level < 40:
            noise_component = 20
            score += 20
        elif analysis.noise_level < 60:
            noise_component = 10
            score += 10

        # السيولة (20%)
        liquidity_component = 0
        if analysis.liquidity_score > 50:
            liquidity_component = 20
            score += 20

        signal.confidence = min(100, score)
        signal.score_breakdown = {
            "نظام_النطاق": ranging_component,
            "زخم_منخفض": momentum_component,
            "ضوضاء_منخفضة": noise_component,
            "السيولة": liquidity_component,
        }

        if signal.confidence >= 65:
            signal.action = "BUY"
            signal.reasoning = (
                f"إعداد ارتداد نحو المتوسط — السوق في نطاق، "
                f"زخم منخفض ({analysis.momentum:.0f}%)، ضوضاء {analysis.noise_level:.0f}%"
            )
        else:
            signal.action = "HOLD"
            signal.reasoning = f"ثقة الارتداد غير كافية ({signal.confidence:.0f}%) — انتظار ظروف أفضل"

        return signal
