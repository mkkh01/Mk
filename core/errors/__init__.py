"""
Error types for the entire system.
Every error is typed and traceable.
"""

class EngineError(Exception):
    """Base for all engine errors."""
    def __init__(self, engine: str, message: str, context: dict = None):
        self.engine = engine
        self.message = message
        self.context = context or {}
        super().__init__(f"[{engine}] {message}")


class ConfigError(EngineError):
    """Configuration errors."""


class MarketDataError(EngineError):
    """Market data failures."""


class AnalysisError(EngineError):
    """Analysis failures."""


class EvidenceError(EngineError):
    """Evidence engine failures."""


class RiskError(EngineError):
    """Risk engine failures."""


class ExecutionError(EngineError):
    """Execution failures."""


class DatabaseError(EngineError):
    """Database errors."""


class StrategyError(EngineError):
    """Strategy failures."""


class TelegramError(EngineError):
    """Telegram bot errors."""


class HealthError(EngineError):
    """Health monitor errors."""


class ConnectionError(EngineError):
    """WebSocket / network failures."""


class RateLimitError(EngineError):
    """API rate limit exceeded."""
