"""Fixed strategy profiles and monitoring metadata.

The user-facing bot no longer accepts arbitrary timeframe lists. Both profiles
share one fetched timeframe set, while each profile consumes its own subset.
"""

from __future__ import annotations

SWING_PROFILE = "swing_conservative"
SCALP_PROFILE = "scalp_balanced"

SWING_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h")
SCALP_TIMEFRAMES: tuple[str, ...] = ("5m", "15m", "30m", "1h")
ALL_MONITORED_TIMEFRAMES: tuple[str, ...] = (
    "5m", "15m", "30m", "1h", "4h"
)

# Scalp is initially an observability/paper profile. It cannot open a live
# trade until it has been validated independently from the swing profile.
SCALP_PAPER_ONLY = True
SCALP_MIN_SCORE = 0.60
SCALP_MIN_CONFIDENCE = 0.55
SCALP_MAX_ATR_PERCENT = 3.0
SCALP_TARGET_PCT = 0.005  # gross target; approximately +0.4% after round-trip cost
SCALP_STOP_PCT = 0.0025
SCALP_MAX_HOLD_MINUTES = 45
SCALP_REENTRY_COOLDOWN_MINUTES = 15
SCALP_MIN_NET_EDGE_PCT = 0.001
SCALP_ROUND_TRIP_COST_PCT = 0.0010  # fee + slippage reserve, Scalp only
SCALP_REVERSAL_MIN_STRENGTH = 0.55

PROFILE_LABELS = {
    SWING_PROFILE: "Swing Conservative",
    SCALP_PROFILE: "Scalp Balanced",
}


def fixed_timeframes() -> list[str]:
    """Return the immutable runtime timeframe set as a fresh list."""
    return list(ALL_MONITORED_TIMEFRAMES)


def runtime_fetch_timeframes(existing: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Return existing Swing timeframes plus Scalp-only feed channels.

    This is additive at the data-feed boundary; it does not mutate CoinConfig
    or change the Swing engine's timeframe decisions.
    """
    ordered = list(dict.fromkeys([*existing, *SCALP_TIMEFRAMES]))
    return tuple(ordered)


def profile_timeframes(profile: str) -> tuple[str, ...]:
    if profile == SWING_PROFILE:
        return SWING_TIMEFRAMES
    if profile == SCALP_PROFILE:
        return SCALP_TIMEFRAMES
    raise ValueError(f"unknown strategy profile: {profile}")
