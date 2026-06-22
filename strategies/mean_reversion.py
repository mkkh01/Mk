"""
Mean Reversion Strategy — trades price returning to mean.
Best in RANGING markets. Avoids strong TRENDING.
"""
from strategies import BaseStrategy, StrategySignal
from core.types import MarketAnalysis


class MeanReversionStrategy(BaseStrategy):
    name = "mean_reversion"

    async def evaluate(self, analysis: MarketAnalysis) -> StrategySignal:
        signal = StrategySignal(
            symbol=analysis.symbol,
            strategy_name=self.name,
            action="HOLD",
            confidence=0.0,
            score_breakdown={},
            reasoning="",
        )

        # Best in ranging markets
        if analysis.regime not in ("RANGING",):
            signal.reasoning = f"Regime {analysis.regime} — mean reversion unreliable in trending"
            signal.confidence = 10.0
            return signal

        # Avoid extreme volatility
        if analysis.volatility > 80:
            signal.reasoning = f"Volatility too high ({analysis.volatility})"
            signal.confidence = 5.0
            return signal

        score = 0.0

        # Ranging regime (35%)
        score += 35

        # Low momentum — reversion candidate (25%)
        if analysis.momentum < 40:
            score += 25
        elif analysis.momentum < 55:
            score += 15

        # Noise level — clean range preferred (20%)
        if analysis.noise_level < 40:
            score += 20
        elif analysis.noise_level < 60:
            score += 10

        # Liquidity (20%)
        if analysis.liquidity_score > 50:
            score += 20

        signal.confidence = min(100, score)
        signal.score_breakdown = {
            "ranging_regime": 35,
            "low_momentum": 25 if analysis.momentum < 40 else 15,
            "low_noise": 20 if analysis.noise_level < 40 else 10,
            "liquidity": 20 if analysis.liquidity_score > 50 else 0,
        }

        if signal.confidence >= 65:
            signal.action = "BUY"
            signal.reasoning = f"Mean reversion setup — range-bound, momentum low ({analysis.momentum:.0f})"
        else:
            signal.action = "HOLD"
            signal.reasoning = f"Reversion confidence insufficient ({signal.confidence:.0f})"

        return signal
