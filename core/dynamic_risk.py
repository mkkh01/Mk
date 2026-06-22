"""
Dynamic Risk Engine — No Hardcoded Values
يحسب المخاطر ديناميكياً من:
- Volatility clustering
- Drawdown recency
- Trade streak analysis
- Regime instability
- Strategy disagreement
"""
import numpy as np
from typing import Optional


class DynamicRiskCalculator:
    """
    حاسبة مخاطر ديناميكية.
    لا توجد قيم ثابتة. كل score يُحسب من البيانات الحية.
    """

    def __init__(self):
        self._recent_volatility: list[float] = []
        self._recent_drawdowns: list[float] = []
        self._trade_outcomes: list[bool] = []
        self._last_regime: str = "UNKNOWN"
        self._regime_changes: int = 0

    def compute_risk_score(
        self,
        volatility: float,
        drawdown_pct: float,
        consecutive_losses: int,
        win_rate: float,
        regime: str,
        strategy_variance: float = 0.0,
    ) -> float:
        """
        حساب درجة المخاطر (0–100).
        0 = خطر أقصى. 100 = آمن تماماً.

        المكونات:
        - Volatility penalty (0–30 points)
        - Drawdown penalty (0–25 points)
        - Streak penalty (0–20 points)
        - Regime instability penalty (0–15 points)
        - Strategy disagreement penalty (0–10 points)
        """

        # ══ 1. Volatility Clustering ══
        self._recent_volatility.append(volatility)
        if len(self._recent_volatility) > 20:
            self._recent_volatility.pop(0)

        if len(self._recent_volatility) >= 3:
            vol_trend = np.mean(self._recent_volatility[-3:]) - np.mean(self._recent_volatility[:max(1, len(self._recent_volatility)-3)])
            vol_penalty = min(30, (volatility / 100) * 20 + max(0, vol_trend) * 0.5)
        else:
            vol_penalty = (volatility / 100) * 20

        # ══ 2. Drawdown Recency ══
        self._recent_drawdowns.append(drawdown_pct)
        if len(self._recent_drawdowns) > 10:
            self._recent_drawdowns.pop(0)

        if len(self._recent_drawdowns) >= 2:
            dd_accelerating = self._recent_drawdowns[-1] > np.mean(self._recent_drawdowns[:-1])
            dd_penalty = min(25, drawdown_pct * 0.5 + (10 if dd_accelerating else 0))
        else:
            dd_penalty = drawdown_pct * 0.5

        # ══ 3. Trade Streak Analysis ══
        if consecutive_losses >= 5:
            streak_penalty = 20
        elif consecutive_losses >= 3:
            streak_penalty = 12
        elif consecutive_losses >= 2:
            streak_penalty = 6
        else:
            streak_penalty = 0

        # ══ 4. Regime Instability ══
        if regime != self._last_regime and self._last_regime != "UNKNOWN":
            self._regime_changes += 1
        self._last_regime = regime

        regime_penalty = min(15, self._regime_changes * 5)

        # ══ 5. Strategy Disagreement ══
        strategy_penalty = min(10, strategy_variance * 20)

        # ══ 6. Win Rate Factor ══
        if win_rate >= 60:
            win_bonus = 5
        elif win_rate >= 40:
            win_bonus = 0
        else:
            win_bonus = -10

        # المجموع
        total_penalty = vol_penalty + dd_penalty + streak_penalty + regime_penalty + strategy_penalty
        score = max(0.0, min(100.0, 80.0 - total_penalty + win_bonus))

        return round(score, 1)

    def record_trade(self, won: bool):
        """تسجيل نتيجة صفقة لتحليل streaks."""
        self._trade_outcomes.append(won)
        if len(self._trade_outcomes) > 50:
            self._trade_outcomes.pop(0)

    @property
    def consecutive_losses(self) -> int:
        """حساب الخسائر المتتالية من السجل."""
        count = 0
        for outcome in reversed(self._trade_outcomes):
            if not outcome:
                count += 1
            else:
                break
        return count

    def reset(self):
        """إعادة تعيين الحالة."""
        self._recent_volatility.clear()
        self._recent_drawdowns.clear()
        self._trade_outcomes.clear()
        self._regime_changes = 0
        self._last_regime = "UNKNOWN"
