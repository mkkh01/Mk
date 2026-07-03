import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# ── Supabase REST ──
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://lvvcbqqtjygqlxyhiabm.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# ── Supabase Direct (Postgres) ──
SUPABASE_HOST = os.getenv("SUPABASE_HOST", "db.lvvcbqqtjygqlxyhiabm.supabase.co")
SUPABASE_PORT = int(os.getenv("SUPABASE_PORT", 5432))
SUPABASE_DB = os.getenv("SUPABASE_DB", "postgres")
SUPABASE_USER = os.getenv("SUPABASE_USER", "postgres")
SUPABASE_PASSWORD = os.getenv("SUPABASE_PASSWORD", "")
DATABASE_URL = f"postgresql://{SUPABASE_USER}:{SUPABASE_PASSWORD}@{SUPABASE_HOST}:{SUPABASE_PORT}/{SUPABASE_DB}"

# ── Redis ──
REDIS_HOST = os.getenv("REDIS_HOST", "moon-close-reaction-79072.db.redis.io")
REDIS_PORT = int(os.getenv("REDIS_PORT", 10184))
REDIS_USER = os.getenv("REDIS_USER", "default")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_URL = f"redis://{REDIS_USER}:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/0"

# ── Binance ──
BINANCE_REST = "https://api.binance.com/api/v3"
BINANCE_WS = "wss://stream.binance.com:9443/ws"
BINANCE_FUTURES_REST = "https://fapi.binance.com/fapi/v1"

# ── Risk Defaults ──
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", 3.0))
MAX_DRAWDOWN_PCT = float(os.getenv("MAX_DRAWDOWN_PCT", 10.0))
DEFAULT_RISK_PER_TRADE = float(os.getenv("DEFAULT_RISK_PER_TRADE", 2.0))
DEFAULT_ATR_MULTIPLIER = float(os.getenv("DEFAULT_ATR_MULTIPLIER", 3.0))
DEFAULT_TP_RATIO = float(os.getenv("DEFAULT_TP_RATIO", 2.0))
MAX_CONSECUTIVE_LOSSES = int(os.getenv("MAX_CONSECUTIVE_LOSSES", 20))

# ── Strategy Defaults ──
DONCHIAN_PERIOD = int(os.getenv("DONCHIAN_PERIOD", 20))
ATR_PERIOD = int(os.getenv("ATR_PERIOD", 14))
EMA_FAST = int(os.getenv("EMA_FAST", 50))
EMA_SLOW = int(os.getenv("EMA_SLOW", 200))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", 14))
ADX_PERIOD = int(os.getenv("ADX_PERIOD", 14))

# ── System ──
BOT_ENABLED = os.getenv("BOT_ENABLED", "True").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_SECONDS", 300))
ALLOWED_TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w"]

# ── Admin Chat IDs (for sensitive commands) ──
ADMIN_CHAT_IDS = [int(x) for x in os.getenv("ADMIN_CHAT_IDS", "").split(",") if x.strip()]