"""
TradeDecision — Unified Contract Object (SSOT)
المصدر الوحيد للحقيقة لكل قرار تداول.
لا يُسمح بأي تنفيذ خارج هذا الكائن.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class TradeDecision:
    """قرار تداول موحد — يمر عبر كامل المسار دون تحويل."""

    # ══ الهوية ══
    decision_id: str = ""              # UUID فريد
    symbol: str = ""
    created_at: str = ""               # ISO timestamp

    # ══ القرار ══
    direction: str = "HOLD"            # BUY | SELL | HOLD
    confidence: float = 0.0            # 0.0 – 100.0
    entry_price: float = 0.0
    quantity: float = 0.0

    # ══ الاستراتيجية ══
    strategy_name: str = ""
    strategy_version: str = ""
    strategy_confidence: float = 0.0
    entry_reason: str = ""

    # ══ الأدلة ══
    evidence_score: float = 0.0
    evidence_decision: str = "HOLD"
    evidence_conflicts: List[str] = field(default_factory=list)
    regime: str = "UNKNOWN"
    trend_direction: str = "NONE"

    # ══ المخاطر ══
    risk_score: float = 0.0            # محسوب فعلياً
    risk_level: str = "EXTREME"
    risk_allowed: bool = False
    max_loss: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    position_size_pct: float = 0.0     # نسبة من رأس المال

    # ══ أعلام الموافقة ══
    flags: dict = field(default_factory=lambda: {
        "strategy_valid": False,
        "data_sufficient": False,
        "regime_valid": False,
        "confidence_met": False,
        "risk_accepted": False,
        "state_allows_trading": False,
        "ws_healthy": False,
        "no_duplicate": False,
    })

    # ══ الموافقة النهائية ══
    final_approval: bool = False
    blocked_reason: str = ""
    arbiter_decision: str = "PENDING"  # PENDING | APPROVED | REJECTED

    # ══ التنفيذ ══
    execution_status: str = "PENDING"  # PENDING | FILLED | REJECTED
    execution_id: str = ""
    db_trade_id: str = ""

    @property
    def is_approved(self) -> bool:
        """كل الأعلام يجب أن تكون True + arbiter_decision == APPROVED."""
        return self.final_approval and self.arbiter_decision == "APPROVED"

    @property
    def is_buy(self) -> bool:
        return self.direction == "BUY"

    @property
    def is_sell(self) -> bool:
        return self.direction == "SELL"

    def reject(self, reason: str, stage: str = "") -> None:
        """رفض القرار مع توثيق السبب والمرحلة."""
        self.final_approval = False
        self.blocked_reason = f"[{stage}] {reason}" if stage else reason
        self.direction = "HOLD"

    def approve(self) -> None:
        """موافقة نهائية بعد اجتياز كل الطبقات."""
        if all(self.flags.values()):
            self.final_approval = True
