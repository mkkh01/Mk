# ── Config Layer ────────────────────────────────────────────
from .settings import Settings
from .constants import (
    DEFAULT_CAPITAL, TRADE_FEE, ADMIN_ID,
    BINANCE_WS_URL, MAX_RISK_PER_TRADE,
    EVIDENCE_THRESHOLD, MAX_DAILY_LOSS_PCT,
    MAX_WEEKLY_LOSS_PCT, MAX_EXPOSURE_PCT,
    HEARTBEAT_INTERVAL_SEC, ANALYSIS_INTERVAL_SEC,
)
from .env_loader import load_env_vars
