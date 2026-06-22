"""
Risk Service — coordinates risk management operations.
"""
import logging

from engines.risk_engine import RiskEngine

logger = logging.getLogger("risk_service")


class RiskService:
    """Risk management coordination."""

    def __init__(self, risk_engine: RiskEngine):
        self.risk_engine = risk_engine

    def emergency_stop(self, reason: str = "Manual from Telegram"):
        self.risk_engine.emergency_stop(reason)

    def resume_trading(self):
        self.risk_engine.resume_trading()

    def get_risk_status(self) -> dict:
        return self.risk_engine.get_status()

    def is_trading_allowed(self) -> bool:
        return not self.risk_engine._trading_blocked
