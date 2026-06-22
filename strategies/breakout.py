"""
Breakout Strategy — trades validated breakouts with volume confirmation.
Requires VALID breakout state and high liquidity.
"""
from strategies import BaseStrategy, StrategySignal
from core.types import MarketAnalysis


class BreakoutStrategy(BaseStrategy):
    name = "breakout"

    async def evaluate(self, analysis: MarketAnalysis) -> StrategySignal:
        signal = StrategySignal(
            symbol=analysis.symbol,
            strategy_name=self.name,
            action="HOLD",
            confidence=0.0,
            score_breakdown={},
            reasoning="",
        )

        # Require valid breakout
        if analysis.breakout_state != "VALID":
            signal.reasoning = f"No valid breakout ({analysis.breakout_state})"
            signal.confidence = 5.0
            return signal

        # Require trending or volatile regime
        if analysis.regime not in ("TRENDING", "VOLATILE"):
            signal.reasoning = f"Regime {analysis.regime} — breakout unreliable"
            signal.confidence = 20.0
            return signal

        score = 0.0

        # Breakout quality (35%)
        score += 35  # Already validated

        # Volume confirmation (25%)
        if analysis.liquidity_score > 50:
            score += 25

        # Momentum (20%)
        if analysis.momentum > 60:
            score += 20
        elif analysis.momentum > 40:
            score += 10

        # Trend alignment (20%)
        if analysis.trend_direction == "UP":
            score += 20
        elif analysis.trend_direction == "DOWN":
            score += 10  # Counter-trend breakout, lower score

        signal.confidence = min(100, score)
        signal.score_breakdown = {
            "breakout_valid": 35,
            "volume": 25 if analysis.liquidity_score > 50 else 0,
            "momentum": 20 if analysis.momentum > 60 else 10,
            "trend_align": 20 if analysis.trend_direction == "UP" else 10,
        }

        if signal.confidence >= 75:
            signal.action = "BUY"
            signal.reasoning = f"Valid breakout confirmed — liquidity {analysis.liquidity_score:.0f}, momentum {analysis.momentum:.0f}"
        elif signal.confidence >= 60 and analysis.trend_direction == "DOWN":
            signal.action = "SELL"
            signal.reasoning = f"Breakout breakdown — bearish momentum"
        else:
            signal.action = "HOLD"
            signal.reasoning = f"Breakout confidence insufficient ({signal.confidence:.0f})"

        return signal
