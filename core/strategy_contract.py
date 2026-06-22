"""
Strategy Contract — Enterprise Grade
كل استراتيجية تداول يجب أن تحقق هذا العقد قبل التشغيل.
Validation إجباري. Versioning إجباري. Performance tracking إجباري.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime
from abc import ABC, abstractmethod


@dataclass
class StrategyMeta:
    """بيانات تعريفية للاستراتيجية — إجبارية."""
    name: str                              # اسم فريد
    version: str = "1.0.0"                 # Semantic versioning
    description: str = ""                  # وصف بالعربية
    author: str = ""
    min_confidence: float = 65.0           # حد أدنى للثقة — مرفوضة إذا < هذا
    supported_timeframes: List[str] = field(default_factory=list)
    suitable_regimes: List[str] = field(default_factory=lambda: ["TRENDING"])
    required_inputs: List[str] = field(default_factory=lambda: [
        "trend_direction", "trend_strength", "momentum",
        "volatility", "liquidity_score"
    ])

    def validate(self) -> tuple[bool, str]:
        """تحقق إجباري قبل تشغيل الاستراتيجية."""
        if not self.name or self.name == "base":
            return False, "الاسم غير محدد"
        if not self.supported_timeframes:
            return False, "supported_timeframes فارغة — يجب تحديد إطار زمني واحد على الأقل"
        if self.min_confidence < 30 or self.min_confidence > 100:
            return False, f"min_confidence غير صالح: {self.min_confidence} (يجب 30–100)"
        if not self.suitable_regimes:
            return False, "suitable_regimes فارغة — يجب تحديد regime واحد على الأقل"
        return True, "OK"


@dataclass
class StrategyPerformance:
    """تتبع أداء الاستراتيجية عبر الزمن."""
    total_signals: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    current_drawdown: float = 0.0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    sharpe_estimate: float = 0.0
    consecutive_wins: int = 0
    consecutive_losses: int = 0
    last_trade_at: str = ""
    regime_performance: Dict[str, dict] = field(default_factory=dict)

    def record_trade(self, won: bool, pnl: float, regime: str = "UNKNOWN"):
        """تحديث الإحصائيات بعد كل صفقة."""
        self.total_trades += 1
        self.total_pnl += pnl

        if won:
            self.winning_trades += 1
            self.consecutive_wins += 1
            self.consecutive_losses = 0
        else:
            self.losing_trades += 1
            self.consecutive_losses += 1
            self.consecutive_wins = 0

        # معدلات
        if self.total_trades > 0:
            self.win_rate = (self.winning_trades / self.total_trades) * 100

        if self.winning_trades > 0:
            self.avg_win = sum([pnl for _ in [1] if won]) / max(self.winning_trades, 1)
        if self.losing_trades > 0:
            self.avg_loss = abs(pnl) if not won else self.avg_loss

        # Profit Factor
        gross_profit = self.avg_win * self.winning_trades
        gross_loss = self.avg_loss * self.losing_trades
        self.profit_factor = gross_profit / max(gross_loss, 0.01)

        # Expectancy
        self.expectancy = (self.win_rate / 100 * self.avg_win) - ((1 - self.win_rate / 100) * self.avg_loss)

        # Drawdown tracking
        if pnl < 0:
            self.current_drawdown += abs(pnl)
            self.max_drawdown = max(self.max_drawdown, self.current_drawdown)
        else:
            self.current_drawdown = max(0, self.current_drawdown - pnl * 0.5)

        # Regime-specific
        if regime not in self.regime_performance:
            self.regime_performance[regime] = {"trades": 0, "won": 0, "pnl": 0.0}
        self.regime_performance[regime]["trades"] += 1
        if won:
            self.regime_performance[regime]["won"] += 1
        self.regime_performance[regime]["pnl"] += pnl

        self.last_trade_at = datetime.utcnow().isoformat()

    @property
    def is_degraded(self) -> bool:
        """الاستراتيجية في حالة انحطاط إذا خسائر متتالية ≥ 5 أو توقع سلبي."""
        return (
            self.consecutive_losses >= 5
            or (self.total_trades >= 10 and self.expectancy < 0)
        )

    @property
    def is_reliable(self) -> bool:
        """الاستراتيجية موثوقة إذا لديها سجل كافٍ وتوقع إيجابي."""
        return (
            self.total_trades >= 5
            and self.expectancy > 0
            and self.profit_factor > 1.0
            and self.win_rate >= 30
        )


class BaseStrategy(ABC):
    """الفئة الأساسية لكل استراتيجيات التداول — Enterprise Grade."""

    meta: StrategyMeta = StrategyMeta()
    performance: StrategyPerformance = StrategyPerformance()

    @abstractmethod
    async def evaluate(self, analysis) -> 'StrategySignal':
        """تقييم تحليل السوق وإنتاج إشارة."""
        ...

    def validate_contract(self) -> tuple[bool, str]:
        """تحقق إجباري من العقد قبل التشغيل."""
        ok, msg = self.meta.validate()
        if not ok:
            return False, msg
        if not self.meta.suitable_regimes:
            return False, "suitable_regimes فارغة"
        if self.meta.min_confidence < 30:
            return False, f"min_confidence منخفض جداً: {self.meta.min_confidence}"
        return True, "VALID"

    def is_suitable_for_regime(self, regime: str) -> bool:
        """هل هذه الاستراتيجية مناسبة لنظام السوق الحالي؟"""
        return regime in self.meta.suitable_regimes

    def record_signal(self, action: str):
        """تسجيل إشارة (بدون تنفيذ)."""
        self.performance.total_signals += 1

    def record_result(self, won: bool, pnl: float, regime: str = "UNKNOWN"):
        """تسجيل نتيجة صفقة."""
        self.performance.record_trade(won, pnl, regime)
