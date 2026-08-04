"""
File: config/thresholds.py
1. Single Responsibility: Hold ALL magic numbers used by the engine, market, simulation,
   risk, ingest and bot modules. No thresholds may be hardcoded anywhere else.
2. Consumes: nothing.
3. Produces: Module-level numeric constants grouped by domain.
4. Downstream: every other module in the project.
5. New Dependencies: No.
6. Touches Section 6 bugs? No (constants only).
7. Tests: threshold-sensitivity test in tests/unit/test_risk.py must observe changed values.
8. Logging: No (no events emitted here).
9. Dependency Order: first import in the chain (config -> contracts -> storage -> ...).
"""

# ---------------------------------------------------------------------------
# Market Structure
# ---------------------------------------------------------------------------
SWING_LOOKBACK = 4
"""Number of candles on each side used to confirm a swing high/low (center-window radius)."""

MIN_SWING_SIZE_PCT = 0.10
"""Minimum swing size as a percentage of price to qualify as a swing point."""

BOS_CONFIRMATION_CANDLES = 1
"""Number of consecutive candles that must close beyond a swing to confirm BOS."""

CHOCH_CONFIRMATION_CANDLES = 1
"""Number of consecutive candles that must close beyond the opposite swing to confirm CHOCH."""

# ---------------------------------------------------------------------------
# Smart Money Concepts
# ---------------------------------------------------------------------------
OB_MIN_IMPULSE_PCT = 0.20
"""Minimum candle body (open-close) size as % of price to be considered a strong impulse."""

OB_MAX_CANDLES_BACK = 15
"""Maximum number of candles to look back when finding the order-block candle."""

FVG_MIN_GAP_PCT = 0.05
"""Minimum gap size (as % of price) for a 3-candle sequence to be considered an FVG."""

LIQUIDITY_SWEEP_STRENGTH_THRESHOLD = 0.50
"""Minimum strength (0..1) for a sweep to be considered meaningful."""

LIQUIDITY_CLUSTER_TOLERANCE_PCT = 0.15
"""Two swing levels within this % distance are clustered as one liquidity level."""

LIQUIDITY_CLUSTER_LOOKBACK = 30
"""Default lookback in candles when clustering swing points into liquidity levels."""

# ---------------------------------------------------------------------------
# Trend
# ---------------------------------------------------------------------------
TREND_EMA_FAST = 9
TREND_EMA_SLOW = 21
TREND_ADX_THRESHOLD = 20.0
"""ADX value above which a trend is considered strong."""

TREND_ADX_MODERATE_LOWER = 20.0
"""Lower bound of the moderate-trend ADX band."""

TREND_STRENGTH_THRESHOLD = 0.40
"""Threshold above which trend strength is considered sufficient."""

ADX_PERIOD = 14
"""Standard ADX lookback period."""

# ---------------------------------------------------------------------------
# Momentum
# ---------------------------------------------------------------------------
MOMENTUM_RSI_PERIOD = 14
MOMENTUM_RSI_OVERBOUGHT = 70
MOMENTUM_RSI_OVERSOLD = 30
MOMENTUM_MACD_FAST = 12
MOMENTUM_MACD_SLOW = 26
MOMENTUM_MACD_SIGNAL = 9
MOMENTUM_STOCH_PERIOD = 14
MOMENTUM_STOCH_SMOOTH_K = 3
MOMENTUM_STOCH_SMOOTH_D = 3
MOMENTUM_STOCH_OVERBOUGHT = 80
MOMENTUM_STOCH_OVERSOLD = 20

# ---------------------------------------------------------------------------
# Volatility
# ---------------------------------------------------------------------------
VOLATILITY_ATR_PERIOD = 14
VOLATILITY_ATR_MULTIPLIER_SL = 1.8
"""ATR multiplier used to compute the stop-loss distance."""

VOLATILITY_ATR_MULTIPLIER_TP = 3.2
"""ATR multiplier used to compute the take-profit distance."""

VOLATILITY_BB_PERIOD = 20
VOLATILITY_BB_STD = 2.0
HIGH_VOLATILITY_THRESHOLD = 1.8
"""ATR/price ratio (%) above which the market is considered highly volatile."""

VOLATILITY_BB_RANGING_PCT = 0.5
"""BB width (as % of price) below which the market is considered ranging."""

# ---------------------------------------------------------------------------
# Sessions (UTC hours)
# ---------------------------------------------------------------------------
ASIAN_START_UTC = 0
ASIAN_END_UTC = 8
LONDON_START_UTC = 8
LONDON_END_UTC = 16
NY_START_UTC = 13
NY_END_UTC = 21

# ---------------------------------------------------------------------------
# Risk Management
# ---------------------------------------------------------------------------
MAX_PORTFOLIO_EXPOSURE_PCT = 100.0
"""Maximum total portfolio exposure as % of total capital."""

MAX_POSITION_SIZE_PCT = 100.0
"""Maximum size of a single position as % of coin capital."""

MAX_DAILY_LOSS_PCT = 9.0
"""Maximum daily loss as % of peak PnL before new entries are blocked."""

MAX_CONCURRENT_TRADES = 10
"""Maximum number of simultaneously open simulated trades."""

MIN_RISK_REWARD_RATIO = 1.4
"""Minimum acceptable reward:risk ratio for any signal."""

RISK_REWARD_TARGET = 1.8
"""Target reward:risk ratio used when no other information is available."""

# ---------------------------------------------------------------------------
# Confidence Scoring
# ---------------------------------------------------------------------------
CONFIDENCE_THRESHOLD = 0.60
"""Minimum confidence (0..1) required for a signal to be acted on."""

HTF_ALIGNMENT_WEIGHT = 0.20
STRUCTURE_WEIGHT = 0.30
MOMENTUM_WEIGHT = 0.20
LIQUIDITY_WEIGHT = 0.25
SESSION_WEIGHT = 0.05
# Sum of the five weights above MUST equal 1.0 (validated in tests).

REGIME_MODIFIER_TRENDING = 1.0
REGIME_MODIFIER_RANGING = 0.90
REGIME_MODIFIER_VOLATILE = 0.85
"""Confidence multipliers applied based on detected market regime."""

# ---------------------------------------------------------------------------
# Entry Rules
# ---------------------------------------------------------------------------
ENTRY_LIMIT_OFFSET_PCT = 0.03
"""Limit order offset (% of price) in the favourable direction."""

ENTRY_TIMEOUT_MINUTES = 20
"""Number of minutes a limit entry is valid for before being cancelled."""

MAX_ENTRY_RETRIES = 2
"""Number of times a limit entry may be retried before falling back to market."""

# ---------------------------------------------------------------------------
# Trailing Stop
# ---------------------------------------------------------------------------
TRAILING_ENABLED = True
"""Global switch to enable / disable trailing-stop logic."""

TRAILING_ACTIVATION_MULTIPLIER = 1.5
"""Start moving the stop once unrealised profit >= this x initial risk.
For example, 1.5 means: after profit reaches 1.5x the initial risk, the
stop begins trailing behind the price."""

TRAILING_ATR_DISTANCE = 1.5
"""Distance (in ATR multiples) between the current high/low and the
tailing stop.  A larger value gives the trade more breathing room."""

TRAILING_MIN_DISTANCE_PCT = 0.3
"""Minimum distance (as % of price) between the trailing stop and the
current extreme price.  Prevents the stop from hugging the price too
closely in low-volatility conditions."""

TRAILING_MAX_DISTANCE_PCT = 5.0
"""Maximum distance (as % of price) between the trailing stop and the
current extreme price.  Prevents excessively wide stops on very
volatile coins."""

# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
MAKER_FEE_PCT = 0.01
TAKER_FEE_PCT = 0.01
SLIPPAGE_PCT = 0.05

# ---------------------------------------------------------------------------
# WebSocket / Ingest
# ---------------------------------------------------------------------------
WS_INITIAL_BACKOFF_SECONDS = 1
WS_MAX_BACKOFF_SECONDS = 60
WS_STABLE_RESET_SECONDS = 30
"""Time of stable connection after which the backoff is reset to its initial value."""

WS_STALE_MULTIPLIER = 2.0
"""A stream is considered stale when no message arrives for (interval * this) seconds."""

WS_REST_RETRY_COUNT = 3
"""Number of REST retries on gap-fill failure."""

WS_RESUME_PAD_CANDLES = 5
"""Extra candles fetched on top of the longest lookback when resuming."""

# ---------------------------------------------------------------------------
# Timeframe metadata
# ---------------------------------------------------------------------------
TIMEFRAME_TO_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
    "3d": 259200,
    "1w": 604800,
}

VALID_TIMEFRAMES = set(TIMEFRAME_TO_SECONDS.keys())


def timeframe_to_seconds(timeframe: str) -> int:
    """Return the duration of a timeframe in seconds.

    Raises:
        ValueError: if the timeframe is not recognised.
    """
    if timeframe not in TIMEFRAME_TO_SECONDS:
        raise ValueError(f"unknown timeframe: {timeframe!r}")
    return TIMEFRAME_TO_SECONDS[timeframe]


def resume_window_candles() -> int:
    """Return the number of candles that must be fetched on WebSocket resume.

    Equals ``max(SWING_LOOKBACK, OB_MAX_CANDLES_BACK, TREND_EMA_SLOW,
    VOLATILITY_ATR_PERIOD) + WS_RESUME_PAD_CANDLES`` per Section 4.
    """
    longest = max(
        SWING_LOOKBACK,
        OB_MAX_CANDLES_BACK,
        TREND_EMA_SLOW,
        VOLATILITY_ATR_PERIOD,
    )
    return longest + WS_RESUME_PAD_CANDLES

__all__ = [
    "SWING_LOOKBACK", "MIN_SWING_SIZE_PCT", "BOS_CONFIRMATION_CANDLES", "CHOCH_CONFIRMATION_CANDLES",
    "OB_MIN_IMPULSE_PCT", "OB_MAX_CANDLES_BACK", "FVG_MIN_GAP_PCT", "LIQUIDITY_SWEEP_STRENGTH_THRESHOLD",
    "LIQUIDITY_CLUSTER_TOLERANCE_PCT", "LIQUIDITY_CLUSTER_LOOKBACK",
    "TREND_EMA_FAST", "TREND_EMA_SLOW", "TREND_ADX_THRESHOLD", "TREND_STRENGTH_THRESHOLD", "ADX_PERIOD",
    "MOMENTUM_RSI_PERIOD", "MOMENTUM_RSI_OVERBOUGHT", "MOMENTUM_RSI_OVERSOLD", "MOMENTUM_MACD_FAST",
    "MOMENTUM_MACD_SLOW", "MOMENTUM_MACD_SIGNAL", "MOMENTUM_STOCH_PERIOD", "MOMENTUM_STOCH_SMOOTH_K",
    "MOMENTUM_STOCH_SMOOTH_D", "MOMENTUM_STOCH_OVERBOUGHT", "MOMENTUM_STOCH_OVERSOLD",
    "VOLATILITY_ATR_PERIOD", "VOLATILITY_ATR_MULTIPLIER_SL", "VOLATILITY_ATR_MULTIPLIER_TP",
    "VOLATILITY_BB_PERIOD", "VOLATILITY_BB_STD", "HIGH_VOLATILITY_THRESHOLD", "VOLATILITY_BB_RANGING_PCT",
    "ASIAN_START_UTC", "ASIAN_END_UTC", "LONDON_START_UTC", "LONDON_END_UTC", "NY_START_UTC", "NY_END_UTC",
    "MAX_PORTFOLIO_EXPOSURE_PCT", "MAX_POSITION_SIZE_PCT", "MAX_DAILY_LOSS_PCT", "MAX_CONCURRENT_TRADES",
    "MIN_RISK_REWARD_RATIO", "RISK_REWARD_TARGET",
    "CONFIDENCE_THRESHOLD", "HTF_ALIGNMENT_WEIGHT", "STRUCTURE_WEIGHT", "MOMENTUM_WEIGHT", "LIQUIDITY_WEIGHT",
    "SESSION_WEIGHT", "REGIME_MODIFIER_TRENDING", "REGIME_MODIFIER_RANGING", "REGIME_MODIFIER_VOLATILE",
    "ENTRY_LIMIT_OFFSET_PCT", "ENTRY_TIMEOUT_MINUTES", "MAX_ENTRY_RETRIES",
    "MAKER_FEE_PCT", "TAKER_FEE_PCT", "SLIPPAGE_PCT",
    "WS_INITIAL_BACKOFF_SECONDS", "WS_MAX_BACKOFF_SECONDS", "WS_STABLE_RESET_SECONDS", "WS_STALE_MULTIPLIER",
    "WS_REST_RETRY_COUNT", "WS_RESUME_PAD_CANDLES",
    "TIMEFRAME_TO_SECONDS", "VALID_TIMEFRAMES", "timeframe_to_seconds", "resume_window_candles",
    "TRAILING_ENABLED", "TRAILING_ACTIVATION_MULTIPLIER", "TRAILING_ATR_DISTANCE",
    "TRAILING_MIN_DISTANCE_PCT", "TRAILING_MAX_DISTANCE_PCT",
]
