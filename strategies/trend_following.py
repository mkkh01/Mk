"""
Trend Following Strategy — trades in the direction of the established trend.
Best in TRENDING regimes. Avoids RANGING/CHOPPY markets.
"""
from strategies import BaseStrategy, StrategySignal
from core.types import MarketAnalysis


class TrendFollowingStrategy(BaseStrategy):
    name = "trend_following"

    async def evaluate(self, analysis: MarketAnalysis) -> StrategySignal:
        signal = StrategySignal(
            symbol=analysis.symbol,
            strategy_name=self.name,
            action="HOLD",
            confidence=0.0,
            score_breakdown={},
            reasoning="",
        )

        # Only trade in trending markets
        if analysis.regime not in ("TRENDING",):
            signal.reasoning = f"Regime {analysis.regime} — skipping"
            signal.confidence = 10.0
            return signal

        # Skip low confidence
        if analysis.confidence < 60:
            signal.reasoning = f"Low confidence ({analysis.confidence})"
            signal.confidence = 15.0
            return signal

        # Score calculation
        score = 0.0

        # Trend strength (40%)
        score += analysis.trend_strength * 0.40

        # Momentum confirmation (25%)
        if analysis.trend_direction == "UP" and analysis.momentum > 50:
            score += 25
        elif analysis.trend_direction == "DOWN" and analysis.momentum > 50:
            score += 25

        # Structure confirmation (20%)
        structure = analysis.structure
        if analysis.trend_direction == "UP" and structure.get("higher_highs") and structure.get("higher_lows"):
            score += 20
        elif analysis.trend_direction == "DOWN" and not structure.get("higher_highs") and not structure.get("higher_lows"):
            score += 20

        # Liquidity (15%)
        if analysis.liquidity_score > 60:
            score += 15

        signal.confidence = min(100, score)
        signal.score_breakdown = {
            "trend_strength": round(analysis.trend_strength * 0.40, 1),
            "momentum": 25 if analysis.momentum > 50 else 0,
            "structure": 20 if structure.get("higher_highs") else 0,
            "liquidity": 15 if analysis.liquidity_score > 60 else 0,
        }

        # Decision
        if analysis.trend_direction == "UP" and signal.confidence >= 70:
            signal.action = "BUY"
            signal.reasoning = f"Strong uptrend — {analysis.trend_strength:.0f} strength, {analysis.momentum:.0f} momentum"
        elif analysis.trend_direction == "DOWN" and signal.confidence >= 70:
            signal.action = "SELL"
            signal.reasoning = f"Strong downtrend — {analysis.trend_strength:.0f} strength, {analysis.momentum:.0f} momentum"
        else:
            signal.action = "HOLD"
            signal.reasoning = f"Insufficient confidence ({signal.confidence:.0f})"

        return signal
