"""
System constants — immutable, no secrets here.
"""
from core.types import RiskLevel

# ── Trade Parameters ───────────────────────────────────────
DEFAULT_CAPITAL: float = 1000.0
TRADE_FEE: float = 0.001
MAX_RISK_PER_TRADE: float = 0.02

# ── Evidence Engine Thresholds ──────────────────────────────
EVIDENCE_THRESHOLD: float = 75.0        # Minimum score for BUY
MIN_CONFLICTING_SIGNALS: int = 2         # How many conflicts → reduce confidence
HIGH_CONFIDENCE: float = 85.0

# ── Risk Limits ─────────────────────────────────────────────
MAX_POSITION_PER_SYMBOL_PCT: float = 0.10
MAX_TOTAL_EXPOSURE_PCT: float = 0.30
MAX_CORRELATED_EXPOSURE_PCT: float = 0.20
MAX_DAILY_LOSS_PCT: float = 0.03
MAX_WEEKLY_LOSS_PCT: float = 0.07
MAX_MONTHLY_LOSS_PCT: float = 0.15
MAX_CONSECUTIVE_LOSSES: int = 5
MAX_EXPOSURE_PCT: float = 0.30
MAX_DRAWDOWN_PCT: float = 0.20
DRAWDOWN_REDUCE_1: float = 0.05   # 5% → reduce position
DRAWDOWN_REDUCE_2: float = 0.10   # 10% → halve frequency
DRAWDOWN_STOP: float = 0.15       # 15% → disable risky

# ── Time Intervals ──────────────────────────────────────────
HEARTBEAT_INTERVAL_SEC: int = 5
ANALYSIS_INTERVAL_SEC: int = 120   # 2 min between analyses
RECONNECT_DELAY_SEC: int = 5
HTF_CACHE_DURATION_SEC: int = 1800  # 30 min

# ── Volatility Adjustment ───────────────────────────────────
VOLATILITY_RISK_MAP: dict[str, float] = {
    "LOW": 1.0,
    "MEDIUM": 0.8,
    "HIGH": 0.5,
    "EXTREME": 0.0,
}

# ── External URLs ───────────────────────────────────────────
BINANCE_WS_URL: str = "wss://stream.binance.com:9443"
FNG_API_URL: str = "https://api.alternative.me/fng/?limit=1"

# ── Session Weights (for Evidence Engine) ───────────────────
SESSION_WEIGHTS: dict[str, float] = {
    "London": 0.08,
    "New York": 0.07,
    "Asia": 0.04,
    "Overlap": 0.09,
    "Weekend": 0.02,
}

# ── Risk Level Labels ───────────────────────────────────────
RISK_LEVEL_ORDER = [
    RiskLevel.LOW,
    RiskLevel.MEDIUM,
    RiskLevel.HIGH,
    RiskLevel.EXTREME,
]

# ── Admin ───────────────────────────────────────────────────
ADMIN_ID: int = 1503808643
