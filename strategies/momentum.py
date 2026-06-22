"""
Momentum Strategy — trades strong directional momentum with volume.
Best in TRENDING or VOLATILE regimes with high momentum.
"""
from strategies import BaseStrategy, StrategySignal
from core.types import MarketAnalysis


class MomentumStrategy(BaseStrategy):
    name = "momentum"

    async def evaluate(self, analysis: MarketAnalysis) -> StrategySignal:
        signal = StrategySignal(
            symbol=analysis.symbol,
            strategy_name=self.name,
            action="HOLD",
            confidence=0.0,
            score_breakdown={},
            reasoning="",
        )

        # Need momentum above threshold
        if analysis.momentum < 60:
            signal.reasoning = f"Momentum too low ({analysis.momentum})"
            signal.confidence = 10.0
            return signal

        # Avoid choppy markets
        if analysis.regime == "CHOPPY":
            signal.reasoning = "Choppy market — momentum unreliable"
            signal.confidence = 5.0
            return signal

        score = 0.0

        # Momentum strength (40%)
        score += min(40, analysis.momentum * 0.4)

        # Trend alignment (25%)
        if analysis.trend_direction in ("UP", "DOWN") and analysis.trend_strength > 50:
            score += 25
        elif analysis.trend_direction in ("UP", "DOWN"):
            score += 15

        # Volume/liquidity (20%)
        if analysis.liquidity_score > 50:
            score += 20
        elif analysis.liquidity_score > 30:
            score += 10

        # Low noise (15%)
        if analysis.noise_level < 50:
            score += 15
        elif analysis.noise_level < 70:
            score += 8

        signal.confidence = min(100, score)
        signal.score_breakdown = {
            "momentum": min(40, analysis.momentum * 0.4),
            "trend_align": 25 if analysis.trend_strength > 50 else 15,
            "volume": 20 if analysis.liquidity_score > 50 else 10,
            "low_noise": 15 if analysis.noise_level < 50 else 8,
        }

        if signal.confidence >= 75:
            if analysis.trend_direction == "UP":
                signal.action = "BUY"
                signal.reasoning = f"Strong bullish momentum ({analysis.momentum:.0f}) with trend confirmation"
            elif analysis.trend_direction == "DOWN":
                signal.action = "SELL"
                signal.reasoning = f"Strong bearish momentum ({analysis.momentum:.0f}) with trend confirmation"
        elif signal.confidence >= 65 and analysis.trend_direction == "UP":
            signal.action = "BUY"
            signal.reasoning = f"Moderate bullish momentum ({analysis.momentum:.0f})"
        else:
            signal.action = "HOLD"
            signal.reasoning = f"Momentum confidence insufficient ({signal.confidence:.0f})"

        return signal
