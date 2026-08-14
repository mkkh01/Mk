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
VOLATILITY_ATR_MULTIPLIER_SL = 2.5
"""ATR multiplier used to compute the stop-loss distance.
Raised from 1.8 after a losing-trades review: several trades were stopped
out by normal intra-candle noise *after* the analysed candle had closed
beyond the level. A wider 2.5x ATR stop keeps trades alive through the
next-candle evaluation cycle (Section 8)."""

VOLATILITY_ATR_MULTIPLIER_TP = 4.0
"""ATR multiplier used to compute the take-profit distance.
Raised from 3.2 alongside the SL multiplier (2.5) so that the default
reward:risk ratio stays above MIN_RISK_REWARD_RATIO (1.4):
4.0 / 2.5 = 1.6 > 1.4."""

# Replay-only sensitivity profile. These constants are never used by the
# production path unless a replay explicitly selects the ``1to1`` profile.
REPLAY_1TO1_ATR_MULTIPLIER_TP = 2.5
"""Replay-only TP distance; equal to the production SL distance."""

REPLAY_1TO1_MIN_RR = 1.0
"""Replay-only minimum reward:risk threshold for the 1:1 sensitivity run."""

VOLATILITY_BB_PERIOD = 20
VOLATILITY_BB_STD = 2.0
HIGH_VOLATILITY_THRESHOLD = 1.8
"""ATR/price ratio (%) above which the market is considered highly volatile."""

HIGH_VOLATILITY_VOLUME_SPIKE_RATIO = 4.0
"""Current-candle volume / rolling-average ratio above which a volume candle
is considered climactic. Entries immediately after such a spike are
rejected in the orchestrator's entry-gate (losing-trades review
2026-08-08)."""

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
CONFIDENCE_THRESHOLD = 0.65
"""Balanced profile: minimum confidence required for a signal to be acted on.
The previous 0.70 threshold admitted very few candidates in replay. Signals
below this threshold remain rejected; the reduced threshold is paired with
smaller risk for controlled volatile entries and must be validated in paper
trading before any live-capital increase."""

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

# Balanced volatility profile. VOLATILE is not treated as a blanket ban when
# normalized ATR is within this controlled band; the risk module halves the
# per-trade risk for those entries. Extreme volatility remains blocked.
ALLOW_CONTROLLED_VOLATILE_ENTRIES = True
VOLATILE_MAX_ENTRY_ATR_PERCENT = 3.0
VOLATILE_RISK_MULTIPLIER = 0.50
VOLATILE_MIN_RISK_REWARD_RATIO = 1.80

# ---------------------------------------------------------------------------
# Entry Rules
# ---------------------------------------------------------------------------
# Quality gates are intentionally separate from CONFIDENCE_THRESHOLD. The
# confidence score is a weighted aggregate and can pass on strong HTF/volume
# evidence while short-term momentum is weak. These gates require a minimum
# directional signal before an entry is refined.
MIN_ENTRY_SIGNAL_SCORE = 0.70
"""Minimum aggregate component score required for a new entry."""

MIN_ENTRY_MOMENTUM_SCORE = 0.6667
"""Minimum LTF momentum score for Spot-long entries.

The momentum model emits values in increments of roughly 1/6. A value of
0.6667 requires net-positive agreement from the RSI/MACD/Stochastic ensemble
instead of allowing neutral or mixed momentum to pass on confidence alone.
"""

# Conservative long-entry timing gates. These are intentionally separate from
# confidence/risk limits: they block late entries without weakening existing
# portfolio protection.
LONG_RSI_NEAR_OVERBOUGHT = 65.0
LONG_NEAR_OVERBOUGHT_STOCH = 75.0
LONG_RSI_RECOVERY_MAX = 40.0
LONG_RSI_MIN_UPTICK = 1.0

MAX_LONG_EXTENSION_ATR = 1.25
MIN_DISTANCE_TO_SWING_HIGH_ATR = 0.75
MIN_DISTANCE_TO_SWING_HIGH_PCT = 0.50

PULLBACK_LOOKBACK_CANDLES = 5
PULLBACK_ZONE_TOLERANCE_ATR = 0.25
PULLBACK_CONFIRMATION_CLOSE_LOCATION = 0.60
PULLBACK_CONFIRMATION_BODY_RATIO = 0.35
MAX_CONFIRMATION_DISTANCE_ATR = 1.00

# Signal-quality calibration. These controls are intentionally separate from
# risk limits and the global confidence gate so they can be evaluated in
# simulation without weakening portfolio protection.
SIGNAL_QUALITY_VERSION = "v2-confluence-2026-08"
MIN_ENTRY_VOLUME_SCORE = 0.60
MIN_SUPPORTING_COMPONENTS = 2
MAX_CONFIDENCE_WITHOUT_MOMENTUM = 0.60
CONTRADICTION_PENALTY = 0.08
MAX_CONTRADICTION_PENALTY = 0.25
VOLUME_MIN_DELTA_RATIO = 0.02
VOLUME_MIN_CVD_RATIO = 0.02
VOLUME_STRENGTH_SCALE = 0.10

ENTRY_LIMIT_OFFSET_PCT = 0.03
"""Limit order offset (% of price) in the favourable direction."""

ENTRY_TIMEOUT_MINUTES = 20
"""Number of minutes a limit entry is valid for before being cancelled."""

MAX_ENTRY_RETRIES = 2
"""Number of times a limit entry may be retried before it is cancelled.

Long entries never fall back to market after the limit retry budget is
exhausted; this prevents chasing a move that never pulled back.
"""

ALLOW_LONG_MARKET_FALLBACK = False
"""Safety switch: long entries must not convert an expired limit to market."""

MAX_LIMIT_SLIPPAGE_PCT = 0.05
"""Maximum favourable-price drift accepted for a simulated long limit fill."""

# ---------------------------------------------------------------------------
# Trailing Stop
# ---------------------------------------------------------------------------
TRAILING_ENABLED = True
"""Global switch to enable / disable trailing-stop logic."""

TRAILING_ACTIVATION_MULTIPLIER = 1.5
"""Start moving the stop once unrealised profit >= this x initial risk.
For example, 1.5 means: after profit reaches 1.5x the initial risk, the
stop begins trailing behind the price."""

TRAILING_ATR_DISTANCE = 2.2
"""Distance (in ATR multiples) between the current high/low and the
tailing stop.  A larger value gives the trade more breathing room."""

TRAILING_MIN_DISTANCE_PCT = 0.5
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
LIVE_PRICE_MAX_AGE_SECONDS = 60
"""Maximum acceptable age (seconds) of a live market price when resolving the
actual fill price at trade-open time. The live-price cache has a Redis TTL of
30s (``storage.redis_cache.LIVE_PRICE_TTL_SECONDS``); 60s is a generous
ceiling that still guarantees the fill price reflects the *current* market
rather than a stale cache (stale signal prices are what caused multiple
time-distant trades to open at the same entry price)."""

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
    "REPLAY_1TO1_ATR_MULTIPLIER_TP", "REPLAY_1TO1_MIN_RR",
    "VOLATILITY_BB_PERIOD", "VOLATILITY_BB_STD", "HIGH_VOLATILITY_THRESHOLD",
"HIGH_VOLATILITY_VOLUME_SPIKE_RATIO", "VOLATILITY_BB_RANGING_PCT",
    "ASIAN_START_UTC", "ASIAN_END_UTC", "LONDON_START_UTC", "LONDON_END_UTC", "NY_START_UTC", "NY_END_UTC",
    "MAX_PORTFOLIO_EXPOSURE_PCT", "MAX_POSITION_SIZE_PCT", "MAX_DAILY_LOSS_PCT", "MAX_CONCURRENT_TRADES",
    "MIN_RISK_REWARD_RATIO", "RISK_REWARD_TARGET",
    "CONFIDENCE_THRESHOLD", "HTF_ALIGNMENT_WEIGHT", "STRUCTURE_WEIGHT", "MOMENTUM_WEIGHT", "LIQUIDITY_WEIGHT",
    "SESSION_WEIGHT", "REGIME_MODIFIER_TRENDING", "REGIME_MODIFIER_RANGING", "REGIME_MODIFIER_VOLATILE",
    "ENTRY_LIMIT_OFFSET_PCT", "ENTRY_TIMEOUT_MINUTES", "MAX_ENTRY_RETRIES",
    "ALLOW_LONG_MARKET_FALLBACK", "MAX_LIMIT_SLIPPAGE_PCT",
    "LONG_RSI_NEAR_OVERBOUGHT", "LONG_NEAR_OVERBOUGHT_STOCH", "LONG_RSI_RECOVERY_MAX",
    "LONG_RSI_MIN_UPTICK", "MAX_LONG_EXTENSION_ATR", "MIN_DISTANCE_TO_SWING_HIGH_ATR",
    "MIN_DISTANCE_TO_SWING_HIGH_PCT", "PULLBACK_LOOKBACK_CANDLES", "PULLBACK_ZONE_TOLERANCE_ATR",
    "PULLBACK_CONFIRMATION_CLOSE_LOCATION", "PULLBACK_CONFIRMATION_BODY_RATIO",
    "MAX_CONFIRMATION_DISTANCE_ATR",
    "MAKER_FEE_PCT", "TAKER_FEE_PCT", "SLIPPAGE_PCT", "LIVE_PRICE_MAX_AGE_SECONDS",
    "WS_INITIAL_BACKOFF_SECONDS", "WS_MAX_BACKOFF_SECONDS", "WS_STABLE_RESET_SECONDS", "WS_STALE_MULTIPLIER",
    "WS_REST_RETRY_COUNT", "WS_RESUME_PAD_CANDLES",
    "TIMEFRAME_TO_SECONDS", "VALID_TIMEFRAMES", "timeframe_to_seconds", "resume_window_candles",
    "TRAILING_ENABLED", "TRAILING_ACTIVATION_MULTIPLIER", "TRAILING_ATR_DISTANCE",
    "TRAILING_MIN_DISTANCE_PCT", "TRAILING_MAX_DISTANCE_PCT",
]
