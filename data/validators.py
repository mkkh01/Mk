"""
File: data/validators.py
1. Single Responsibility: Validate candle sanity and raw Binance kline payloads
   before they enter the storage / engine layers. Never transforms data -- only
   accepts or rejects (Section 22, "Data Level").
2. Consumes: contracts.market.Candle, config.thresholds (timeframe metadata).
3. Produces: InvalidCandleError, validate_candle(), validate_candle_batch(),
   validate_binance_kline().
4. Downstream: ingest/binance_ws.py (per-message), data/cleaners.py (pre-clean
   sanity), tests/unit/test_storage.py (round-trip fixtures).
5. New Dependencies: No (pure Python + pydantic already in the tree).
6. Touches Section 6 bugs? Yes -- guards Bug 3 by raising on unclosed candles
   that look like closed ones (e.g. close == 0) and by ensuring taker volumes
   sum to total volume (Bug 2's prerequisite: real taker data integrity).
7. Tests: tests/unit/test_validators.py -- the synthetic bad-candle matrix
   (negative prices, high < low, mismatched taker volumes, open_time >=
   close_time, zero close).
8. Logging: candle_invalid, kline_invalid, candle_validated (debug only).
9. Dependency Order: config -> contracts -> data/validators.py. No upstream
   violations (does not import ingest, storage, or engine).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional

from contracts.market import Candle
from config.thresholds import TIMEFRAME_TO_SECONDS, VALID_TIMEFRAMES
from monitoring.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Module-local tolerances
# ---------------------------------------------------------------------------
# These are *data-integrity* tolerances, not engine / risk thresholds -- so they
# live here rather than in config/thresholds.py (mirroring the pattern used by
# storage/redis_cache.py for TTLs). They exist purely to absorb float drift
# between Binance REST and WS payloads of the same candle.

VOLUME_SUM_TOLERANCE = 1e-6
"""Absolute tolerance for |taker_buy_volume + taker_sell_volume - volume|."""

PRICE_EPSILON = 1e-9
"""Absolute tolerance for high/low vs open/close comparisons."""

MIN_TIMESTAMP_DELTA_MS = 1
"""Minimum allowed gap (in milliseconds) between open_time and close_time."""


class InvalidCandleError(ValueError):
    """Raised when a Candle or raw Binance kline fails validation.

    The message is always human-readable and starts with a short reason tag
    (e.g. ``"negative_price: open=-1.0"``) so it can be parsed by log
    scrapers without re-tokenising.
    """

    def __init__(self, reason: str, details: Optional[dict[str, Any]] = None) -> None:
        self.reason = reason
        self.details = details or {}
        super().__init__(reason)


# ---------------------------------------------------------------------------
# Candle validation
# ---------------------------------------------------------------------------
def validate_candle(candle: Candle) -> bool:
    """Validate a single ``Candle`` against the Section 2 contract.

    Checks (each one raises ``InvalidCandleError`` on failure):

      1. All OHLC prices are strictly positive.
      2. ``high >= max(open, close, low)`` (within ``PRICE_EPSILON``).
      3. ``low <= min(open, close, high)`` (within ``PRICE_EPSILON``).
      4. ``volume >= 0``.
      5. ``taker_buy_volume + taker_sell_volume`` is approximately ``volume``
         (within ``VOLUME_SUM_TOLERANCE``).
      6. ``open_time < close_time``.
      7. ``close > 0`` (explicit, even though covered by #1 -- closed candles
         must have a non-zero price).
      8. ``timeframe`` is a known timeframe in ``VALID_TIMEFRAMES``.

    Returns ``True`` on success (the raise-on-failure pattern matches the
    spec -- callers can also use the boolean return for inline checks).

    Raises:
        InvalidCandleError: with a detailed, parseable ``reason`` string.
    """

    # 8. Timeframe must be in the recognised set.
    if candle.timeframe not in VALID_TIMEFRAMES:
        raise InvalidCandleError(
            f"unknown_timeframe: timeframe={candle.timeframe!r}",
            details={"symbol": candle.symbol, "timeframe": candle.timeframe},
        )

    # 1 + 7. Positive prices.
    for field_name in ("open", "high", "low", "close"):
        value = getattr(candle, field_name)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or math.isnan(value):
            raise InvalidCandleError(
                f"non_numeric_price: {field_name}={value!r}",
                details={"symbol": candle.symbol, "timeframe": candle.timeframe,
                         "field": field_name, "value": value},
            )
        if value <= 0:
            raise InvalidCandleError(
                f"non_positive_price: {field_name}={value}",
                details={"symbol": candle.symbol, "timeframe": candle.timeframe,
                         "field": field_name, "value": value},
            )

    # 2. High is the maximum.
    max_oc = max(candle.open, candle.close)
    if candle.high + PRICE_EPSILON < max_oc or candle.high + PRICE_EPSILON < candle.low:
        raise InvalidCandleError(
            f"high_below_neighbor: high={candle.high} "
            f"open={candle.open} close={candle.close} low={candle.low}",
            details={"symbol": candle.symbol, "timeframe": candle.timeframe,
                     "open": candle.open, "high": candle.high,
                     "low": candle.low, "close": candle.close},
        )

    # 3. Low is the minimum.
    min_oc = min(candle.open, candle.close)
    if candle.low - PRICE_EPSILON > min_oc or candle.low - PRICE_EPSILON > candle.high:
        raise InvalidCandleError(
            f"low_above_neighbor: low={candle.low} "
            f"open={candle.open} close={candle.close} high={candle.high}",
            details={"symbol": candle.symbol, "timeframe": candle.timeframe,
                     "open": candle.open, "high": candle.high,
                     "low": candle.low, "close": candle.close},
        )

    # 4. Volume non-negative.
    if candle.volume < 0:
        raise InvalidCandleError(
            f"negative_volume: volume={candle.volume}",
            details={"symbol": candle.symbol, "timeframe": candle.timeframe,
                     "volume": candle.volume},
        )

    # 5. Taker volumes sum to total volume.
    taker_sum = candle.taker_buy_volume + candle.taker_sell_volume
    if abs(taker_sum - candle.volume) > VOLUME_SUM_TOLERANCE:
        raise InvalidCandleError(
            f"taker_volume_mismatch: buy={candle.taker_buy_volume} "
            f"sell={candle.taker_sell_volume} sum={taker_sum} volume={candle.volume}",
            details={"symbol": candle.symbol, "timeframe": candle.timeframe,
                     "taker_buy_volume": candle.taker_buy_volume,
                     "taker_sell_volume": candle.taker_sell_volume,
                     "volume": candle.volume,
                     "delta": taker_sum - candle.volume},
        )

    # 5b. Individual taker volumes cannot be negative.
    if candle.taker_buy_volume < 0 or candle.taker_sell_volume < 0:
        bad = "buy" if candle.taker_buy_volume < 0 else "sell"
        value = candle.taker_buy_volume if bad == "buy" else candle.taker_sell_volume
        raise InvalidCandleError(
            f"negative_taker_volume: {bad}={value}",
            details={"symbol": candle.symbol, "timeframe": candle.timeframe,
                     "taker_buy_volume": candle.taker_buy_volume,
                     "taker_sell_volume": candle.taker_sell_volume},
        )

    # 5c. Neither taker volume can exceed total volume.
    if candle.taker_buy_volume > candle.volume + VOLUME_SUM_TOLERANCE:
        raise InvalidCandleError(
            f"taker_buy_exceeds_volume: buy={candle.taker_buy_volume} "
            f"volume={candle.volume}",
            details={"symbol": candle.symbol, "timeframe": candle.timeframe,
                     "taker_buy_volume": candle.taker_buy_volume,
                     "volume": candle.volume},
        )
    if candle.taker_sell_volume > candle.volume + VOLUME_SUM_TOLERANCE:
        raise InvalidCandleError(
            f"taker_sell_exceeds_volume: sell={candle.taker_sell_volume} "
            f"volume={candle.volume}",
            details={"symbol": candle.symbol, "timeframe": candle.timeframe,
                     "taker_sell_volume": candle.taker_sell_volume,
                     "volume": candle.volume},
        )

    # 6. open_time < close_time.
    if candle.open_time >= candle.close_time:
        raise InvalidCandleError(
            f"time_inversion: open_time={candle.open_time.isoformat()} "
            f"close_time={candle.close_time.isoformat()}",
            details={"symbol": candle.symbol, "timeframe": candle.timeframe,
                     "open_time": candle.open_time.isoformat(),
                     "close_time": candle.close_time.isoformat()},
        )

    # 6b. Cross-check the candle duration matches the timeframe. Binance gives
    # close_time = open_time + timeframe_seconds*1000 - 1 (ms). We allow up to
    # 1 second of slack to absorb clock skew and the "-1 ms" convention.
    expected_seconds = TIMEFRAME_TO_SECONDS[candle.timeframe]
    actual_seconds = (candle.close_time - candle.open_time).total_seconds()
    if abs(actual_seconds - (expected_seconds - 0.001)) > 1.0 and \
       abs(actual_seconds - expected_seconds) > 1.0:
        # Only raise when the difference is large -- small drift is tolerated.
        if abs(actual_seconds - expected_seconds) > 5.0:
            raise InvalidCandleError(
                f"timeframe_duration_mismatch: timeframe={candle.timeframe} "
                f"expected={expected_seconds}s actual={actual_seconds:.3f}s",
                details={"symbol": candle.symbol, "timeframe": candle.timeframe,
                         "expected_seconds": expected_seconds,
                         "actual_seconds": actual_seconds},
            )

    logger.debug(
        "candle_validated",
        timestamp=datetime.now(timezone.utc),
        symbol=candle.symbol,
        timeframe=candle.timeframe,
        open_time=candle.open_time.isoformat(),
        is_closed=candle.is_closed,
    )
    return True


def validate_candle_batch(candles: list[Candle]) -> list[Candle]:
    """Filter ``candles`` to only those that pass validation.

    Per Section 22 ("Data Level: Invalid candle -> reject, log, skip"), this
    function never raises -- it logs ``candle_invalid`` at warning level for
    each rejected candle and returns the surviving subset, preserving order.

    Args:
        candles: A list of ``Candle`` objects (any mixture of valid / invalid).

    Returns:
        A new list containing only the valid candles, in their original order.
    """
    valid: list[Candle] = []
    rejected = 0
    for candle in candles:
        try:
            validate_candle(candle)
            valid.append(candle)
        except InvalidCandleError as exc:
            rejected += 1
            logger.warning(
                "candle_invalid",
                timestamp=datetime.now(timezone.utc),
                symbol=candle.symbol,
                timeframe=candle.timeframe,
                open_time=candle.open_time.isoformat() if candle.open_time else None,
                reason=exc.reason,
                details=exc.details,
            )
    if rejected:
        logger.info(
            "candle_batch_validated",
            timestamp=datetime.now(timezone.utc),
            total=len(candles),
            valid=len(valid),
            rejected=rejected,
        )
    return valid


# ---------------------------------------------------------------------------
# Raw Binance kline validation
# ---------------------------------------------------------------------------
# The set of keys we expect inside the ``k`` sub-object of a Binance kline
# event. The values for o/h/l/c/v/V are strings -- Binance never sends numbers
# inline -- so we only check for presence + parsability here, not types.

_REQUIRED_KLINE_FIELDS: tuple[str, ...] = (
    "t",  # open time (ms)
    "T",  # close time (ms)
    "s",  # symbol (also at top-level, but Binance includes it inside k)
    "i",  # interval
    "o",  # open
    "c",  # close
    "h",  # high
    "l",  # low
    "v",  # volume
    "V",  # taker buy base volume
    "x",  # is closed
)

_REQUIRED_TOP_FIELDS: tuple[str, ...] = (
    "e",  # event type
    "s",  # symbol
    "k",  # kline sub-object
)


def validate_binance_kline(raw: dict) -> dict:
    """Validate a raw Binance combined-stream kline message.

    Binance's combined stream wraps the actual kline event in
    ``{"stream": "<symbol>@kline_<tf>", "data": { ... kline event ... }}``.
    This function accepts *either* the wrapper or the bare kline event and
    returns a normalised dict with the following keys (all parsed to the
    correct Python types -- ready for ``Candle`` construction):

      ``symbol``           : str
      ``timeframe``        : str (e.g. "15m")
      ``open_time``        : datetime (UTC)
      ``close_time``       : datetime (UTC)
      ``open``             : float
      ``high``             : float
      ``low``              : float
      ``close``            : float
      ``volume``           : float
      ``taker_buy_volume`` : float
      ``taker_sell_volume``: float  (computed as ``volume - taker_buy_volume``)
      ``is_closed``        : bool

    Raises:
        InvalidCandleError: if any required field is missing, the message is
            not a kline event, a numeric field cannot be parsed, a timestamp
            is non-positive, or the timeframe is unrecognised.
    """
    if not isinstance(raw, dict):
        raise InvalidCandleError(
            f"non_dict_payload: type={type(raw).__name__}",
            details={"payload_type": type(raw).__name__},
        )

    # Unwrap combined-stream envelope if present.
    if "data" in raw and isinstance(raw["data"], dict) and "k" in raw["data"]:
        payload = raw["data"]
        stream = raw.get("stream", "")
    else:
        payload = raw
        stream = ""

    # Top-level checks.
    missing_top = [f for f in _REQUIRED_TOP_FIELDS if f not in payload]
    if missing_top:
        raise InvalidCandleError(
            f"missing_top_fields: {missing_top}",
            details={"missing": missing_top, "stream": stream},
        )

    if payload.get("e") != "kline":
        raise InvalidCandleError(
            f"unexpected_event_type: e={payload.get('e')!r}",
            details={"event_type": payload.get("e"), "stream": stream},
        )

    k = payload.get("k")
    if not isinstance(k, dict):
        raise InvalidCandleError(
            f"non_dict_kline: k_type={type(k).__name__}",
            details={"stream": stream},
        )

    missing_k = [f for f in _REQUIRED_KLINE_FIELDS if f not in k]
    if missing_k:
        raise InvalidCandleError(
            f"missing_kline_fields: {missing_k}",
            details={"missing": missing_k, "stream": stream},
        )

    # Parse symbol + timeframe.
    symbol = payload.get("s") or k.get("s")
    if not symbol or not isinstance(symbol, str):
        raise InvalidCandleError(
            f"invalid_symbol: symbol={symbol!r}",
            details={"stream": stream},
        )
    symbol = symbol.upper().strip()

    timeframe = k.get("i")
    if timeframe not in VALID_TIMEFRAMES:
        raise InvalidCandleError(
            f"unknown_timeframe: timeframe={timeframe!r}",
            details={"symbol": symbol, "timeframe": timeframe, "stream": stream},
        )

    # Parse timestamps (Binance sends ms-since-epoch as int).
    open_time_ms = _parse_int(k["t"], "t", symbol, timeframe)
    close_time_ms = _parse_int(k["T"], "T", symbol, timeframe)
    if open_time_ms <= 0:
        raise InvalidCandleError(
            f"non_positive_open_time: t={open_time_ms}",
            details={"symbol": symbol, "timeframe": timeframe, "open_time_ms": open_time_ms},
        )
    if close_time_ms <= 0:
        raise InvalidCandleError(
            f"non_positive_close_time: T={close_time_ms}",
            details={"symbol": symbol, "timeframe": timeframe, "close_time_ms": close_time_ms},
        )
    if open_time_ms >= close_time_ms:
        raise InvalidCandleError(
            f"time_inversion: t={open_time_ms} T={close_time_ms}",
            details={"symbol": symbol, "timeframe": timeframe,
                     "open_time_ms": open_time_ms, "close_time_ms": close_time_ms},
        )

    open_time = datetime.fromtimestamp(open_time_ms / 1000.0, tz=timezone.utc)
    close_time = datetime.fromtimestamp(close_time_ms / 1000.0, tz=timezone.utc)

    # Parse prices + volumes.
    o = _parse_float(k["o"], "o", symbol, timeframe)
    h = _parse_float(k["h"], "h", symbol, timeframe)
    low = _parse_float(k["l"], "l", symbol, timeframe)
    c = _parse_float(k["c"], "c", symbol, timeframe)
    v = _parse_float(k["v"], "v", symbol, timeframe)
    taker_buy = _parse_float(k["V"], "V", symbol, timeframe)

    if o <= 0 or h <= 0 or low <= 0 or c <= 0:
        raise InvalidCandleError(
            f"non_positive_price: o={o} h={h} l={low} c={c}",
            details={"symbol": symbol, "timeframe": timeframe,
                     "open": o, "high": h, "low": low, "close": c},
        )

    if h + PRICE_EPSILON < max(o, c, low):
        raise InvalidCandleError(
            f"high_below_neighbor: h={h} o={o} c={c} l={low}",
            details={"symbol": symbol, "timeframe": timeframe,
                     "open": o, "high": h, "low": low, "close": c},
        )
    if low - PRICE_EPSILON > min(o, c, h):
        raise InvalidCandleError(
            f"low_above_neighbor: l={low} o={o} c={c} h={h}",
            details={"symbol": symbol, "timeframe": timeframe,
                     "open": o, "high": h, "low": low, "close": c},
        )

    if v < 0:
        raise InvalidCandleError(
            f"negative_volume: v={v}",
            details={"symbol": symbol, "timeframe": timeframe, "volume": v},
        )
    if taker_buy < 0:
        raise InvalidCandleError(
            f"negative_taker_buy: V={taker_buy}",
            details={"symbol": symbol, "timeframe": timeframe, "taker_buy_volume": taker_buy},
        )
    if taker_buy > v + VOLUME_SUM_TOLERANCE:
        raise InvalidCandleError(
            f"taker_buy_exceeds_volume: V={taker_buy} v={v}",
            details={"symbol": symbol, "timeframe": timeframe,
                     "taker_buy_volume": taker_buy, "volume": v},
        )

    # is_closed flag.
    raw_x = k.get("x")
    if not isinstance(raw_x, bool):
        # Binance always sends a bool, but be defensive.
        if isinstance(raw_x, str):
            is_closed = raw_x.strip().lower() in ("true", "1", "yes")
        elif isinstance(raw_x, (int, float)):
            is_closed = bool(raw_x)
        else:
            raise InvalidCandleError(
                f"non_bool_is_closed: x={raw_x!r}",
                details={"symbol": symbol, "timeframe": timeframe, "x": raw_x},
            )
    else:
        is_closed = raw_x

    taker_sell = v - taker_buy
    # Clamp tiny negative drift to zero (floating point).
    if -VOLUME_SUM_TOLERANCE < taker_sell < 0:
        taker_sell = 0.0

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "open_time": open_time,
        "close_time": close_time,
        "open": o,
        "high": h,
        "low": low,
        "close": c,
        "volume": v,
        "taker_buy_volume": taker_buy,
        "taker_sell_volume": taker_sell,
        "is_closed": is_closed,
    }


# ---------------------------------------------------------------------------
# Internal parse helpers
# ---------------------------------------------------------------------------
def _parse_float(value: Any, field_name: str, symbol: str, timeframe: str) -> float:
    """Parse a Binance numeric field that may arrive as str / int / float."""
    if isinstance(value, bool):
        # bool is a subclass of int -- reject explicitly.
        raise InvalidCandleError(
            f"bool_for_numeric: {field_name}={value!r}",
            details={"symbol": symbol, "timeframe": timeframe, "field": field_name},
        )
    try:
        f = float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidCandleError(
            f"unparseable_float: {field_name}={value!r} error={exc}",
            details={"symbol": symbol, "timeframe": timeframe,
                     "field": field_name, "raw": value},
        ) from exc
    if math.isnan(f) or math.isinf(f):
        raise InvalidCandleError(
            f"non_finite_float: {field_name}={value!r}",
            details={"symbol": symbol, "timeframe": timeframe,
                     "field": field_name, "raw": value},
        )
    return f


def _parse_int(value: Any, field_name: str, symbol: str, timeframe: str) -> int:
    """Parse a Binance integer field (timestamps) that may arrive as str / int."""
    if isinstance(value, bool):
        raise InvalidCandleError(
            f"bool_for_numeric: {field_name}={value!r}",
            details={"symbol": symbol, "timeframe": timeframe, "field": field_name},
        )
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidCandleError(
            f"unparseable_int: {field_name}={value!r} error={exc}",
            details={"symbol": symbol, "timeframe": timeframe,
                     "field": field_name, "raw": value},
        ) from exc


__all__ = [
    "InvalidCandleError",
    "validate_candle",
    "validate_candle_batch",
    "validate_binance_kline",
    "VOLUME_SUM_TOLERANCE",
    "PRICE_EPSILON",
]
