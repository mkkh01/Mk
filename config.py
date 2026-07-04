# CTM Bot - Configuration
# v2.1 — multi-source loading: env vars → .env file → fallback
import os

# ── Load .env file first (local dev only) ──
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed (shouldn't happen with requirements.txt)

# ── Fallback values (used only when no env vars or .env file exist) ──
_FALLBACKS = {
    "TELEGRAM_BOT_TOKEN": "8881069774:AAGGbcemdV6_6fobmw04Pd6gA40PKG7rD3A",
    "SUPABASE_URL": "https://lvvcbqqtjygqlxyhiabm.supabase.co",
    "SUPABASE_DB_URL": "postgresql://postgres.lvvcbqqtjygqlxyhiabm:1392e9djdhwjdjjdnw@aws-1-eu-central-1.pooler.supabase.com:5432/postgres",
    "WEBHOOK_BASE_URL": "https://ct-copytrading-bot.onrender.com",
}

# Load local .env overrides (not committed, for dev convenience)
_env_file = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(_env_file):
    try:
        with open(_env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, _, val = line.partition('=')
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if val:
                        os.environ[key] = val
    except Exception:
        pass


def _get(key: str) -> str:
    """Get config value: environment variable > .env file > fallback."""
    val = os.getenv(key)
    if val:
        return val
    return _FALLBACKS.get(key, "")


# === Telegram ===
TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN")

# === Supabase ===
SUPABASE_URL = _get("SUPABASE_URL")
SUPABASE_DB_URL = _get("SUPABASE_DB_URL")

# Supabase + Telegram values loaded (fallbacks ensure they always exist)

# === Binance (Public API) ===
BINANCE_BASE_URL = os.getenv("BINANCE_BASE_URL", "https://api.binance.com")

# === Webhook (Render deployment URL) ===
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", os.getenv("RENDER_EXTERNAL_URL", ""))

# === Strategy Parameters ===
DONCHIAN_PERIOD = 20
ATR_PERIOD = 14
ADX_THRESHOLD = 25
VOLATILITY_THRESHOLD = 2.0
ORDER_FLOW_RATIO = 2.0
MOMENTUM_PERIOD = 10

# === Risk Defaults ===
DEFAULT_RISK_PERCENT = 2.0
DEFAULT_CAPITAL_PERCENT = 30.0
MAX_DAILY_LOSS = 3.0
MAX_CONSECUTIVE_LOSSES = 5
DEFAULT_RR_RATIO = 2.0

# === Monitoring ===
MONITOR_INTERVAL_SECONDS = 60
LOOKBACK_DAYS = 7

# === Supported Timeframes ===
TIMEFRAMES = ["1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","3d","1w"]

# === Market Regime States ===
class MarketRegime:
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    RANGE = "RANGE"
    CAPITULATION = "CAPITULATION"
    DISTRIBUTION = "DISTRIBUTION"
    BREAKOUT = "BREAKOUT"

# === Signal Status ===
class SignalStatus:
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    TP_HIT = "TP_HIT"
    SL_HIT = "SL_HIT"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
