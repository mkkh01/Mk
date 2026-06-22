"""
واجهة الاستراتيجيات الموحّدة — CT V4.0
كل استراتيجية تنفذ لكل إطار زمني بشكل مستقل.
"""
from dataclasses import dataclass, field
from typing import List
from core.types import MarketAnalysis


@dataclass
class StrategySignal:
    """إشارة تداول من استراتيجية واحدة — لكل إطار زمني مستقل."""
    symbol: str = ""
    strategy_name: str = ""
    timeframe: str = ""          # الإطار الزمني الذي أُنتجت منه الإشارة
    action: str = "HOLD"         # BUY, SELL, HOLD
    confidence: float = 0.0
    score_breakdown: dict = field(default_factory=dict)
    reasoning: str = ""          # السبب بالعربية


class BaseStrategy:
    """الفئة الأساسية لكل استراتيجيات التداول."""
    name: str = "base"
    supported_timeframes: List[str] = []  # كل استراتيجية تعلن أطرها

    async def evaluate(self, analysis: MarketAnalysis) -> StrategySignal:
        """تقييم تحليل السوق وإنتاج إشارة — لا تستخدم بيانات من أطر زمنية أخرى."""
        raise NotImplementedError(f"{self.name} must implement evaluate()")
