# CTM Bot - Configuration
# v2.0 — secrets loaded from environment variables
import os
from dotenv import load_dotenv

load_dotenv()  # Load .env file if present

# === Telegram ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# === Supabase ===
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL", "")

# === Binance (Public API - no auth needed) ===
BINANCE_BASE_URL = os.getenv("BINANCE_BASE_URL", "https://api.binance.com")

# === Strategy Parameters ===
DONCHIAN_PERIOD = 20          # Donchian channel lookback
ATR_PERIOD = 14               # ATR period for volatility & position sizing
ADX_THRESHOLD = 25            # ADX > 25 = trending market
VOLATILITY_THRESHOLD = 2.0    # ATR/price ratio threshold for high volatility
ORDER_FLOW_RATIO = 2.0        # Buy/Sell ratio threshold for order flow signals
MOMENTUM_PERIOD = 10          # Momentum calculation period

# === Risk Defaults ===
DEFAULT_RISK_PERCENT = 2.0    # Default risk per trade %
DEFAULT_CAPITAL_PERCENT = 30.0 # Default capital allocation %
MAX_DAILY_LOSS = 3.0          # Circuit breaker: max daily loss %
MAX_CONSECUTIVE_LOSSES = 5    # Circuit breaker: consecutive losses
DEFAULT_RR_RATIO = 2.0        # Default Risk:Reward ratio

# === Monitoring ===
MONITOR_INTERVAL_SECONDS = 60  # Price check interval
LOOKBACK_DAYS = 7             # Historical data lookback

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
