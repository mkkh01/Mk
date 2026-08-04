"""
File: data/cleaners.py
1. Single Responsibility: Repair / tidy a list of ``Candle`` objects -- remove
   statistical outliers, fill missing slots, deduplicate, sort, normalise the
   taker-volume identity. Pure data hygiene; no business logic, no storage.
2. Consumes: contracts.market.Candle, numpy.
3. Produces: remove_outliers(), fill_gaps(), deduplicate(), sort_and_dedupe(),
   normalize_volume().
4. Downstream: ingest/binance_ws.py (per-message + on-resume gap-fill),
   engine/orchestrator.py (defensive pass before structure detection),
   tests/unit/test_storage.py.
5. New Dependencies: numpy (already in requirements.txt and in
   config/thresholds.py's "Volume Profile" dependencies).
6. Touches Section 6 bugs? Yes -- Bug 2 (CVD). ``normalize_volume`` enforces
   the invariant ``taker_buy_volume + taker_sell_volume == volume`` so that
   downstream CVD calculations (Section 6 Bug 2) can rely on real taker
   volumes rather than candle-colour heuristics. ``fill_gaps`` is also a
   Bug 3 guard: synthetic candles are always emitted with ``is_closed=True``
   so they cannot be misclassified as live / repainting candles.
7. Tests: tests/unit/test_cleaners.py -- outlier removal on a synthetic
   spike, gap-fill interpolation accuracy, dedupe idempotency, volume
   normalisation round-trip, sort stability.
8. Logging: candles_cleaned, gaps_filled, outliers_removed, candles_deduped,
   volumes_normalized (info-level summaries only; per-candle diagnostics are
   debug to avoid log spam on large batches).
9. Dependency Order: config -> contracts -> data/validators.py ->
   data/cleaners.py. No upstream violations.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from config.thresholds import TIMEFRAME_TO_SECONDS
from contracts.market import Candle
from data.validators import VOLUME_SUM_TOLERANCE, validate_candle
from monitoring.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Module-local cleaning defaults
# ---------------------------------------------------------------------------
# These are *cleaning* defaults, not engine / risk thresholds. They live here
# (mirroring the pattern in storage/redis_cache.py for TTLs) so they remain
# close to the code that uses them.

DEFAULT_OUTLIER_Z_THRESHOLD = 3.0
"""Default z-score threshold for ``remove_outliers`` (≈99.7% of a normal dist)."""

MAX_GAP_FILL_CANDLES = 1000
"""Hard cap on the number of synthetic candles a single ``fill_gaps`` call may
emit -- protects against pathological inputs (e.g. mismatched timeframe).
Anything larger is almost certainly a config error and should be visible in
the log, not silently absorbed."""

DEFAULT_OUTLIER_MIN_SAMPLE = 10
"""Minimum number of candles required before z-score outlier removal is
meaningful. Smaller batches are returned unchanged with a debug log."""


# ---------------------------------------------------------------------------
# Outlier removal
# ---------------------------------------------------------------------------
def remove_outliers(
    candles: list[Candle],
    z_threshold: float = DEFAULT_OUTLIER_Z_THRESHOLD,
) -> list[Candle]:
    """Drop candles whose close price is a z-score outlier.

    Uses a robust-ish approach: the z-score is computed against the mean and
    standard deviation of the close prices. Candles whose ``|z| > z_threshold``
    are removed. For very small batches (fewer than
    ``DEFAULT_OUTLIER_MIN_SAMPLE``) the function is a no-op -- z-scores are
    meaningless on tiny samples and we'd risk dropping legitimate data.

    Args:
        candles:      Input candles (any order; mixed symbols/timeframes are
                      fine but the comparison is global -- callers normally
                      pre-group by ``(symbol, timeframe)``).
        z_threshold:  Positive float. Defaults to ``3.0`` (≈99.7% confidence
                      band on a normal distribution). Must be > 0.

    Returns:
        A new list of candles with the outliers removed, in their original
        relative order. If the input is empty or too small, it is returned
        unchanged.

    Raises:
        ValueError: if ``z_threshold`` is not positive.
    """
    if z_threshold <= 0:
        raise ValueError(f"z_threshold must be > 0, got {z_threshold}")

    if len(candles) < DEFAULT_OUTLIER_MIN_SAMPLE:
        logger.debug(
            "outliers_skipped_small_sample",
            timestamp=datetime.now(timezone.utc),
            count=len(candles),
            min_sample=DEFAULT_OUTLIER_MIN_SAMPLE,
        )
        return list(candles)

    closes = np.fromiter((c.close for c in candles), dtype=np.float64, count=len(candles))
    mean = float(np.mean(closes))
    std = float(np.std(closes, ddof=0))

    # If std is zero (all closes identical) or numerically vanishing, there's
    # nothing to flag as an outlier.
    if std < 1e-12:
        logger.debug(
            "outliers_skipped_zero_std",
            timestamp=datetime.now(timezone.utc),
            count=len(candles),
            mean=mean,
        )
        return list(candles)

    z_scores = np.abs((closes - mean) / std)
    keep_mask = z_scores <= z_threshold
    kept = [c for c, keep in zip(candles, keep_mask) if bool(keep)]
    removed_count = int((~keep_mask).sum())

    if removed_count > 0:
        logger.info(
            "outliers_removed",
            timestamp=datetime.now(timezone.utc),
            total=len(candles),
            removed=removed_count,
            kept=len(kept),
            z_threshold=z_threshold,
            mean=mean,
            std=std,
        )
    return kept


# ---------------------------------------------------------------------------
# Gap filling
# ---------------------------------------------------------------------------
def fill_gaps(
    candles: list[Candle],
    timeframe_seconds: int,
) -> list[Candle]:
    """Fill missing candles in a time-sorted sequence by linear interpolation.

    Given a list of candles sorted ascending by ``open_time`` (use
    ``sort_and_dedupe`` first if unsure), this function walks each adjacent
    pair and synthesises any missing candles. A candle is "missing" when
    ``next.open_time != prev.open_time + timeframe_seconds``.

    Synthetic candles are constructed by linearly interpolating OHLC and
    volume fields between the bracketing pair. They are always emitted with
    ``is_closed=True`` so that downstream consumers (storage, engine) treat
    them as immutable -- this is a Section 6 Bug 3 guardrail.

    Taker volumes are split using the bracketing ratio
    ``prev.taker_buy_volume / prev.volume`` so the invariant
    ``taker_buy_volume + taker_sell_volume == volume`` is preserved exactly.

    Args:
        candles:           Input candles. Will be sorted ascending by
                           ``open_time`` if not already (duplicates are kept as-is;
                           run ``deduplicate`` first to drop them).
        timeframe_seconds: The candle interval in seconds. Must be > 0. The
                           caller is expected to obtain this from
                           ``config.thresholds.timeframe_to_seconds``.

    Returns:
        A new list of candles with all gaps filled, sorted ascending by
        ``open_time``. The total length is ``original + n_synthetic``.

    Raises:
        ValueError: if ``timeframe_seconds <= 0`` or if the number of
            required synthetic candles exceeds ``MAX_GAP_FILL_CANDLES``.
    """
    if timeframe_seconds <= 0:
        raise ValueError(f"timeframe_seconds must be > 0, got {timeframe_seconds}")

    if len(candles) <= 1:
        return list(candles)

    # Defensive sort. Log if we had to reorder, so callers can spot bad inputs.
    pre_sorted = all(
        candles[i].open_time <= candles[i + 1].open_time for i in range(len(candles) - 1)
    )
    if not pre_sorted:
        logger.warning(
            "candles_cleaned",
            timestamp=datetime.now(timezone.utc),
            action="pre_sort_for_gap_fill",
            count=len(candles),
        )
    candles = sorted(candles, key=lambda c: c.open_time)

    interval = timedelta(seconds=timeframe_seconds)
    out: list[Candle] = [candles[0]]
    total_synthetic = 0

    for i in range(1, len(candles)):
        prev = candles[i - 1]
        cur = candles[i]

        # If duplicate or overlapping (cur.open_time <= prev.open_time), skip.
        if cur.open_time <= prev.open_time:
            # Caller should have deduped -- but be defensive and skip silently.
            continue

        gap_delta = cur.open_time - prev.open_time
        gap_steps = int(gap_delta.total_seconds() // timeframe_seconds)
        # The number of candles that should sit strictly between prev and cur.
        missing = gap_steps - 1

        if missing <= 0:
            out.append(cur)
            continue

        if missing > MAX_GAP_FILL_CANDLES:
            raise ValueError(
                f"gap too large to fill: missing={missing} "
                f"(cap={MAX_GAP_FILL_CANDLES}) between "
                f"{prev.open_time.isoformat()} and {cur.open_time.isoformat()}"
            )

        # Linear interpolation factors: t in (0, 1) for each missing slot.
        # Slot k=1..missing sits at t = k / (missing + 1).
        # Defensive: divide-by-zero is impossible here because missing >= 1.
        prev_volume = prev.volume if prev.volume > 0 else cur.volume
        # Use the *ratio* of the prior candle to split taker volumes so the
        # synthetic candle honours the taker-volume identity (Section 6 Bug 2).
        if prev_volume > 0:
            buy_ratio = max(0.0, min(1.0, prev.taker_buy_volume / prev_volume))
        else:
            buy_ratio = 0.5

        for k in range(1, missing + 1):
            t = k / (missing + 1)
            syn_open_time = prev.open_time + interval * k
            syn_close_time = syn_open_time + interval - timedelta(milliseconds=1)
            syn_open = _lerp(prev.close, cur.open, t)
            syn_close = _lerp(prev.close, cur.open, t)
            syn_high = max(prev.close, cur.open, syn_open, syn_close)
            syn_low = min(prev.close, cur.open, syn_open, syn_close)
            syn_volume = _lerp(prev.volume, cur.volume, t)
            syn_buy = syn_volume * buy_ratio
            syn_sell = syn_volume - syn_buy

            synthetic = Candle(
                symbol=prev.symbol,
                timeframe=prev.timeframe,
                open_time=syn_open_time,
                close_time=syn_close_time,
                open=syn_open,
                high=syn_high,
                low=syn_low,
                close=syn_close,
                volume=syn_volume,
                taker_buy_volume=syn_buy,
                taker_sell_volume=syn_sell,
                is_closed=True,  # Section 6 Bug 3 -- synthetic candles are immutable.
            )
            out.append(synthetic)
            total_synthetic += 1

        out.append(cur)

    if total_synthetic > 0:
        logger.info(
            "gaps_filled",
            timestamp=datetime.now(timezone.utc),
            symbol=candles[0].symbol,
            timeframe=candles[0].timeframe,
            original=len(candles),
            synthetic=total_synthetic,
            final=len(out),
            timeframe_seconds=timeframe_seconds,
        )

    return out


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------
def deduplicate(candles: list[Candle]) -> list[Candle]:
    """Remove duplicate candles, keeping the *last* occurrence of each
    ``(symbol, timeframe, open_time)`` triple.

    "Last occurrence" means: if the input contains two candles with the same
    natural key, the one closer to the end of the input list wins. This makes
    the function useful for the resume / gap-fill path where a fresh REST
    fetch should overwrite a stale cached copy.

    Args:
        candles: Input candles (any order).

    Returns:
        A new list with duplicates removed, in input order of the surviving
        (last) copy.
    """
    if not candles:
        return []

    # Walk the list once to find the index of the last occurrence of each key.
    last_idx: dict[tuple[str, str, datetime], int] = {}
    for i, c in enumerate(candles):
        last_idx[(c.symbol, c.timeframe, c.open_time)] = i

    # Emit each candle only when we're at its last_idx -- this preserves the
    # original order of the surviving (last-wins) copies.
    out: list[Candle] = []
    emitted: set[tuple[str, str, datetime]] = set()
    for i, c in enumerate(candles):
        key = (c.symbol, c.timeframe, c.open_time)
        if i == last_idx[key] and key not in emitted:
            emitted.add(key)
            out.append(c)

    removed = len(candles) - len(out)
    if removed > 0:
        logger.info(
            "candles_deduped",
            timestamp=datetime.now(timezone.utc),
            total=len(candles),
            removed=removed,
            kept=len(out),
        )
    return out


def sort_and_dedupe(candles: list[Candle]) -> list[Candle]:
    """Sort ascending by ``open_time`` and remove duplicates in one pass.

    Convenience wrapper combining ``deduplicate`` and an ``open_time`` sort.
    Useful before structure-detection passes that assume a strictly ascending,
    duplicate-free sequence.

    Args:
        candles: Input candles (any order, possibly with duplicates).

    Returns:
        A new list, sorted ascending by ``open_time``, with duplicates removed
        (last-wins semantics, see ``deduplicate``).
    """
    deduped = deduplicate(candles)
    deduped.sort(key=lambda c: c.open_time)
    return deduped


# ---------------------------------------------------------------------------
# Volume normalisation
# ---------------------------------------------------------------------------
def normalize_volume(candles: list[Candle]) -> list[Candle]:
    """Repair the taker-volume identity on each candle.

    For every candle this enforces::

        taker_buy_volume + taker_sell_volume == volume

    within ``VOLUME_SUM_TOLERANCE``. Three cases are handled:

      1. **Already correct** -- candle is returned unchanged.
      2. **``taker_buy_volume`` is correct, ``taker_sell_volume`` is wrong** --
         ``taker_sell_volume`` is recomputed as ``volume - taker_buy_volume``.
         This is the normal Binance-WS case where the upstream only sends
         ``taker_buy_volume`` (``V``) and we must derive ``taker_sell_volume``.
      3. **Both taker volumes are wrong / missing** -- ``taker_buy_volume`` is
         set to ``volume / 2`` and ``taker_sell_volume`` is set to the
         remainder. This is a last-resort repair; the caller will see a
         ``volumes_normalized`` log entry with ``strategy=split_half``.

    A candle is considered "case 3" when::

        abs(taker_buy_volume + taker_sell_volume - volume) > tolerance
        AND taker_buy_volume is missing OR > volume + tolerance OR < 0

    Args:
        candles: Input candles.

    Returns:
        A new list of candles with the taker-volume identity satisfied. The
        original list is not mutated. Non-volume fields are preserved.
    """
    if not candles:
        return []

    out: list[Candle] = []
    repaired_sell = 0
    repaired_split = 0
    already_ok = 0

    for c in candles:
        buy = c.taker_buy_volume
        sell = c.taker_sell_volume
        vol = c.volume

        # Case 1: already valid.
        if (
            buy >= 0
            and sell >= 0
            and buy <= vol + VOLUME_SUM_TOLERANCE
            and abs(buy + sell - vol) <= VOLUME_SUM_TOLERANCE
        ):
            out.append(c)
            already_ok += 1
            continue

        # Case 2: buy is plausible, recompute sell.
        if 0.0 <= buy <= vol + VOLUME_SUM_TOLERANCE:
            new_sell = max(0.0, vol - buy)
            out.append(_replace_taker(c, taker_buy_volume=buy, taker_sell_volume=new_sell))
            repaired_sell += 1
            continue

        # Case 3: buy is bad too -- split half-and-half.
        half = vol / 2.0 if vol > 0 else 0.0
        out.append(_replace_taker(c, taker_buy_volume=half, taker_sell_volume=vol - half))
        repaired_split += 1

    if repaired_sell + repaired_split > 0:
        logger.info(
            "volumes_normalized",
            timestamp=datetime.now(timezone.utc),
            total=len(candles),
            already_ok=already_ok,
            repaired_sell=repaired_sell,
            repaired_split=repaired_split,
        )
    return out


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _lerp(a: float, b: float, t: float) -> float:
    """Linear interpolation between ``a`` and ``b`` at parameter ``t`` in [0,1]."""
    return a + (b - a) * t


def _replace_taker(
    candle: Candle,
    taker_buy_volume: float,
    taker_sell_volume: float,
) -> Candle:
    """Return a copy of ``candle`` with the two taker-volume fields replaced.

    ``Candle`` is a frozen pydantic model (``ConfigDict(frozen=True)``), so we
    can't mutate in place. ``model_copy(update=...)`` is the documented way to
    produce a modified frozen model.
    """
    return candle.model_copy(
        update={
            "taker_buy_volume": taker_buy_volume,
            "taker_sell_volume": taker_sell_volume,
        }
    )


# ---------------------------------------------------------------------------
# Convenience: full clean pipeline
# ---------------------------------------------------------------------------
def clean_pipeline(
    candles: list[Candle],
    timeframe_seconds: Optional[int] = None,
    z_threshold: float = DEFAULT_OUTLIER_Z_THRESHOLD,
) -> list[Candle]:
    """Run the full cleaning pipeline in the canonical order:

      1. ``normalize_volume``    -- repair taker-volume identity (Bug 2 guard).
      2. ``sort_and_dedupe``     -- unique keys, ascending order.
      3. ``fill_gaps``           -- linear-interpolate missing candles.
      4. ``remove_outliers``     -- drop z-score outliers on close price.
      5. ``validate_candle``     -- final sanity pass (drop anything invalid).

    Args:
        candles:           Input candles (any order, any state).
        timeframe_seconds: Required for ``fill_gaps``. If ``None``, the
                           timeframe is read from the first candle (all
                           candles must share the same timeframe in that case).
        z_threshold:       Z-score threshold for outlier removal.

    Returns:
        A new list of candles, ready for storage / engine consumption.

    Raises:
        ValueError: if ``timeframe_seconds`` cannot be derived and the
            candles list is non-empty.
    """
    if not candles:
        return []

    if timeframe_seconds is None:
        tf = candles[0].timeframe
        if tf not in TIMEFRAME_TO_SECONDS:
            raise ValueError(f"cannot derive timeframe_seconds from {tf!r}")
        timeframe_seconds = TIMEFRAME_TO_SECONDS[tf]

    # 1. Normalise taker volumes first so all downstream steps see consistent
    #    data (the validators + gap-fill both rely on the identity).
    step1 = normalize_volume(candles)
    # 2. Sort + dedupe.
    step2 = sort_and_dedupe(step1)
    # 3. Fill gaps. All candles share the same symbol/timeframe by contract
    #    when this helper is invoked; we still pass per-candle metadata
    #    through transparently.
    step3 = fill_gaps(step2, timeframe_seconds=timeframe_seconds)
    # 4. Remove outliers.
    step4 = remove_outliers(step3, z_threshold=z_threshold)
    # 5. Final validation pass -- drop anything that still fails.
    step5: list[Candle] = []
    for c in step4:
        try:
            validate_candle(c)
            step5.append(c)
        except Exception as exc:  # noqa: BLE001 -- InvalidCandleError is the expected one.
            logger.warning(
                "candles_cleaned",
                timestamp=datetime.now(timezone.utc),
                action="drop_invalid_post_clean",
                symbol=c.symbol,
                timeframe=c.timeframe,
                open_time=c.open_time.isoformat(),
                reason=str(exc),
            )

    logger.info(
        "candles_cleaned",
        timestamp=datetime.now(timezone.utc),
        action="pipeline_complete",
        input_count=len(candles),
        output_count=len(step5),
        timeframe_seconds=timeframe_seconds,
        z_threshold=z_threshold,
    )
    return step5


__all__ = [
    "remove_outliers",
    "fill_gaps",
    "deduplicate",
    "sort_and_dedupe",
    "normalize_volume",
    "clean_pipeline",
    "DEFAULT_OUTLIER_Z_THRESHOLD",
    "MAX_GAP_FILL_CANDLES",
    "DEFAULT_OUTLIER_MIN_SAMPLE",
]
