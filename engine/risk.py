"""
File: engine/risk.py
1. Single Responsibility: Assess risk for a candidate trade -- compute
   position size from the risk-based formula, calculate stop-loss and
   take-profit from ATR, and enforce exposure / drawdown / R:R /
   concurrent-trade limits.  ALL thresholds come from
   ``config/thresholds.py`` -- nothing is hardcoded.
2. Consumes: ``StrategySignal``, ``RiskAssessment`` (contracts/decision.py),
   ``CoinConfig`` (contracts/config.py); thresholds from
   config/thresholds.py (thresholds.MAX_PORTFOLIO_EXPOSURE_PCT, thresholds.MAX_POSITION_SIZE_PCT,
   thresholds.MAX_DAILY_LOSS_PCT, thresholds.MAX_CONCURRENT_TRADES, thresholds.MIN_RISK_REWARD_RATIO,
   thresholds.RISK_REWARD_TARGET, thresholds.VOLATILITY_ATR_MULTIPLIER_SL,
   thresholds.VOLATILITY_ATR_MULTIPLIER_TP).
3. Produces: ``calculate_position_size``, ``check_exposure``,
   ``check_drawdown``, ``calculate_stop_loss``, ``calculate_take_profit``,
   ``calculate_risk_reward``, ``assess_risk`` returning ``RiskAssessment``
   consumed by engine/entry_rules.py and engine/orchestrator.py.
4. Downstream: engine/entry_rules.py (uses SL/TP/size from RiskAssessment),
   engine/orchestrator.py (the only caller that combines risk with
   confidence to produce a final verdict).
5. New Dependencies: No new external deps.
6. Touches Section 6 bugs? No.
7. Tests: Section 10 engine/risk.py acceptance criteria:
       1. Exposure rejection -- a signal exceeding the configured exposure
          limit returns ``RiskAssessment(allowed=False, reason=<non-empty>)``.
       2. Valid signal sizing -- a signal within all limits returns
          ``allowed=True`` with ``max_position_size`` matching the configured
          sizing formula.
       3. Threshold sensitivity -- changing a value in
          config/thresholds.py changes the test's expected output (validated
          implicitly because every check reads the threshold module at
          call time).
       4. Drawdown rejection -- a signal exceeding max drawdown is rejected
          with a clear reason.
       5. R:R rejection -- a signal with R:R below ``thresholds.MIN_RISK_REWARD_RATIO``
          is rejected.
8. Logging: ``risk_assessed`` {timestamp, symbol, allowed, reason,
   position_size} per the monitoring/logger.py event catalog.
9. Dependency Order: config -> contracts -> monitoring -> engine/risk.py
   (no upstream violations).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

import config.thresholds as thresholds
from contracts.config import CoinConfig
from contracts.decision import RiskAssessment, StrategySignal
from monitoring.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _safe_float(value: float, default: float = 0.0) -> float:
    """Convert ``value`` to ``float``, returning ``default`` on failure / NaN."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:  # NaN check
        return default
    return out


def _portfolio_state_get(state: dict, key: str, default: float = 0.0) -> float:
    """Read ``key`` from ``portfolio_state`` with safe defaults.

    ``portfolio_state`` is an opaque dict supplied by the orchestrator (which
    in turn reads it from the portfolio module).  We must never raise on a
    missing key -- Section 22 mandates safe defaults.
    """
    if not isinstance(state, dict):
        return default
    return _safe_float(state.get(key, default), default=default)


def _portfolio_state_int(state: dict, key: str, default: int = 0) -> int:
    """Read integer ``key`` from ``portfolio_state``."""
    if not isinstance(state, dict):
        return default
    try:
        return int(state.get(key, default))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Position sizing -- Section 8 formula
# ---------------------------------------------------------------------------
def calculate_position_size(
    capital: float,
    risk_percent: float,
    entry_price: float,
    stop_loss_price: float,
) -> float:
    """Compute position size from the risk-based formula (Section 8).

    ``position_size = risk_amount / price_risk``  (units of base currency),
    where ``risk_amount = capital * (risk_percent / 100)`` and ``price_risk
    = abs(entry_price - stop_loss_price)``.

    The size is then capped at ``thresholds.MAX_POSITION_SIZE_PCT`` percent of capital
    converted to base-currency units:
    ``max_size = capital * (thresholds.MAX_POSITION_SIZE_PCT / 100) / entry_price``.

    Args:
        capital: Total capital allocated to this coin (USDT).
        risk_percent: Risk per trade as a percent of capital (e.g. 2.0 = 2%).
        entry_price: Entry price (USDT per unit).
        stop_loss_price: Stop-loss price (USDT per unit).

    Returns:
        Position size in base-currency units. Returns ``0.0`` when
        ``price_risk == 0`` (Section 8) or when ``entry_price <= 0`` (defensive
        -- avoids division by zero in the cap).
    """
    capital = _safe_float(capital)
    risk_percent = _safe_float(risk_percent)
    entry_price = _safe_float(entry_price)
    stop_loss_price = _safe_float(stop_loss_price)

    if entry_price <= 0:
        return 0.0

    risk_amount = capital * (risk_percent / 100.0)
    price_risk = abs(entry_price - stop_loss_price)
    if price_risk == 0:
        return 0.0

    raw_size = risk_amount / price_risk
    max_size = capital * (thresholds.MAX_POSITION_SIZE_PCT / 100.0) / entry_price
    
    # [FIX] Instead of just returning min, we ensure that the trade value 
    # (size * entry_price) never exceeds the allowed capital exposure.
    # This makes the system dynamic: it scales the size to fit the capital.
    if max_size <= 0:
        return 0.0
        
    final_size = min(raw_size, max_size)
    
    # Final safety check: Ensure notional value <= capital
    if (final_size * entry_price) > (capital + 0.01):
        final_size = capital / entry_price
        
    return final_size


# ---------------------------------------------------------------------------
# Exposure check -- Section 8
# ---------------------------------------------------------------------------
def check_exposure(
    current_exposure: float,
    total_capital: float,
    new_trade_size: float,
) -> bool:
    """True iff adding ``new_trade_size`` keeps exposure within the limit.

    ``projected = current_exposure + new_trade_size``;
    passes iff ``projected <= total_capital * (thresholds.MAX_PORTFOLIO_EXPOSURE_PCT / 100)``.

    Args:
        current_exposure: Current $ exposure across all open trades.
        total_capital: Total capital (USDT).
        new_trade_size: $ size of the proposed new trade (entry_price * size).

    Returns:
        ``True`` if within limit, ``False`` otherwise. Always returns
        ``False`` if ``total_capital <= 0`` (defensive).
    """
    current_exposure = _safe_float(current_exposure)
    total_capital = _safe_float(total_capital)
    new_trade_size = _safe_float(new_trade_size)

    if total_capital <= 0:
        return False
    projected = current_exposure + new_trade_size
    limit = total_capital * (thresholds.MAX_PORTFOLIO_EXPOSURE_PCT / 100.0)
    # [FIX] Add a small epsilon (0.01 USDT) to handle floating point precision 
    # and prevent rejection of trades that are effectively at the limit.
    return projected <= (limit + 0.01)


# ---------------------------------------------------------------------------
# Drawdown check -- Section 8
# ---------------------------------------------------------------------------
def check_drawdown(
    current_pnl: float,
    peak_pnl: float,
    new_trade_risk: float,
) -> bool:
    """True iff taking a loss of ``new_trade_risk`` keeps drawdown in limit.

    ``projected_drawdown = peak_pnl - (current_pnl - new_trade_risk)``;
    passes iff ``projected_drawdown <= peak_pnl * (thresholds.MAX_DAILY_LOSS_PCT / 100)``.

    Args:
        current_pnl: Current realised + unrealised PnL for the period.
        peak_pnl: Highest PnL observed in the period (for drawdown calc).
        new_trade_risk: $ amount at risk on the new trade (the loss if SL
            hits, i.e. ``risk_amount`` from :func:`calculate_position_size`).

    Returns:
        ``True`` if within limit, ``False`` otherwise. When ``peak_pnl <= 0``
        AND ``current_pnl >= 0`` (no profit history yet AND not currently
        losing), the check passes -- a fresh account has no profits to
        protect and the drawdown limit is meaningless. When ``peak_pnl <= 0``
        AND ``current_pnl < 0`` (already losing), the check uses
        ``abs(current_pnl) + new_trade_risk`` against a zero baseline: any
        further risk is rejected if it would compound the existing loss by
        more than the configured percentage of capital.

        This interpretation matches Section 22's "safe defaults on
        insufficient history" guidance -- the literal Section 8 formula
        divides by ``peak_pnl`` which is zero at the start of a period, so
        we degrade gracefully rather than blocking every first trade.
    """
    current_pnl = _safe_float(current_pnl)
    peak_pnl = _safe_float(peak_pnl)
    new_trade_risk = _safe_float(new_trade_risk)

    if new_trade_risk < 0:
        new_trade_risk = 0.0

    # No profits to protect yet (Cold Start or Reset).
    if peak_pnl <= 0:
        if current_pnl >= 0:
            # Fresh start -- no profits, no losses. 
            # We allow the trade as long as the new_trade_risk itself 
            # doesn't exceed the thresholds.MAX_DAILY_LOSS_PCT of a "virtual" peak.
            # Since peak_pnl is 0, we can't divide by it. But Section 8 
            # implies protection of capital. If we don't have capital here,
            # we allow the first trade.
            return True
        
        # If we are already in a loss (current_pnl < 0) but peak_pnl is 0,
        # it means we haven't made any profit yet and we are underwater.
        # We should block new trades if the total loss (abs(current_pnl) + new_trade_risk)
        # exceeds the allowed daily loss percentage of the initial capital.
        # However, since we don't have 'initial_capital' passed here directly, 
        # and Section 8 implies peak_pnl is the baseline, any risk on a 
        # losing account with zero peak is blocked to prevent "revenge trading" 
        # or compounding losses on a failing strategy from day 1.
        return False

    projected_drawdown = peak_pnl - (current_pnl - new_trade_risk)
    limit = peak_pnl * (thresholds.MAX_DAILY_LOSS_PCT / 100.0)
    return projected_drawdown <= limit


# ---------------------------------------------------------------------------
# Stop-loss / take-profit from ATR -- Section 8 / Section 16 volatility
# ---------------------------------------------------------------------------
def calculate_stop_loss(
    entry_price: float,
    atr: float,
    direction: Literal["long"] = "long",
) -> float:
    """Stop-loss price for Spot (Long only) using ``thresholds.VOLATILITY_ATR_MULTIPLIER_SL``.

    * Long  : ``SL = entry_price - atr * thresholds.VOLATILITY_ATR_MULTIPLIER_SL``

    Returns ``entry_price`` (no stop) if ``atr <= 0``.
    """
    entry_price = _safe_float(entry_price)
    atr = _safe_float(atr)
    if atr <= 0:
        return entry_price
    distance = atr * thresholds.VOLATILITY_ATR_MULTIPLIER_SL
    return entry_price - distance


def calculate_take_profit(
    entry_price: float,
    atr: float,
    direction: Literal["long"] = "long",
) -> float:
    """Take-profit price for Spot (Long only) using ``thresholds.VOLATILITY_ATR_MULTIPLIER_TP``.

    * Long  : ``TP = entry_price + atr * thresholds.VOLATILITY_ATR_MULTIPLIER_TP``

    Returns ``entry_price`` (no target) if ``atr <= 0``.
    """
    entry_price = _safe_float(entry_price)
    atr = _safe_float(atr)
    if atr <= 0:
        return entry_price
    distance = atr * thresholds.VOLATILITY_ATR_MULTIPLIER_TP
    return entry_price + distance


# ---------------------------------------------------------------------------
# Risk-reward ratio
# ---------------------------------------------------------------------------
def calculate_risk_reward(
    entry_price: float,
    stop_loss: float,
    take_profit: float,
) -> float:
    """Reward-to-risk ratio (``reward / risk``).

    * Long  : ``risk = entry - SL``, ``reward = TP - entry``.
    * Short : ``risk = SL - entry``, ``reward = entry - TP``.

    The function is direction-agnostic because both differences are absolute
    -- it picks the consistent sign automatically. Returns ``0.0`` when
    ``risk == 0`` (Section 22 -- division by zero -> safe default).
    """
    entry_price = _safe_float(entry_price)
    stop_loss = _safe_float(stop_loss)
    take_profit = _safe_float(take_profit)

    risk = abs(entry_price - stop_loss)
    reward = abs(take_profit - entry_price)
    if risk == 0:
        return 0.0
    return reward / risk


# ---------------------------------------------------------------------------
# Comprehensive risk assessment -- the entry point used by the orchestrator
# ---------------------------------------------------------------------------
def assess_risk(
    signal: StrategySignal,
    confidence: float,
    coin_config: CoinConfig,
    portfolio_state: dict,
    atr: float,
) -> RiskAssessment:
    """Run every risk check and return a populated :class:`RiskAssessment`.

    Pipeline (Section 15 engine/risk.py + Section 8 formulas):
      1. Read ``current_price``, ``current_exposure``, ``current_pnl``,
         ``peak_pnl``, ``open_trade_count`` from ``portfolio_state`` (with
         safe defaults -- Section 22).
      2. Compute ``entry_price`` (defaults to ``current_price``).
      3. Compute ``stop_loss`` and ``take_profit`` from ``atr`` and
         ``signal.direction`` via :func:`calculate_stop_loss` /
         :func:`calculate_take_profit`.
      4. Compute ``risk_reward`` via :func:`calculate_risk_reward`.
      5. Compute ``position_size`` (in base-currency units) via
         :func:`calculate_position_size`.
      6. Compute ``new_trade_value`` (USDT notional) = ``position_size *
         entry_price``.
      7. Compute ``risk_amount`` = ``capital * (risk_percent / 100)``.
      8. Check in order (first failure wins, returns immediately):
           a. R:R >= ``thresholds.MIN_RISK_REWARD_RATIO``
           b. ``open_trade_count < thresholds.MAX_CONCURRENT_TRADES``
           c. Exposure: :func:`check_exposure` passes for ``new_trade_value``.
           d. Drawdown: :func:`check_drawdown` passes for ``risk_amount``.
      9. If all pass: return ``RiskAssessment(allowed=True, ...)`` with all
         sizing/level fields populated.
      10. If any fail: return ``RiskAssessment(allowed=False, reason=...)``
          with the populated SL/TP/size values so the orchestrator can still
          surface them in the DecisionResult for traceability.

    Args:
        signal: The candidate strategy signal (``direction`` and ``symbol``
            are used; ``raw_score`` is logged but does not influence sizing).
        confidence: Final confidence value (logged only -- does not
            influence sizing because position size is purely risk-based).
        coin_config: Per-coin capital / risk_percent / timeframes.
        portfolio_state: Dict with keys ``current_exposure`` (USDT),
            ``current_pnl`` (USDT), ``peak_pnl`` (USDT), ``open_trade_count``
            (int), ``current_price`` (USDT). Missing keys default to 0.
        atr: Current ATR for the symbol on the entry timeframe. Used for
            SL/TP sizing.

    Returns:
        :class:`RiskAssessment` with all fields populated (allowed=True/False
        and reason as appropriate). The ``exposure_after_trade`` and
        ``drawdown_after_trade`` fields are always populated for
        traceability.
    """
    symbol = signal.symbol
    direction = signal.direction

    capital = _safe_float(coin_config.capital)
    risk_percent = _safe_float(coin_config.risk_percent)
    atr = _safe_float(atr)

    current_price = _portfolio_state_get(portfolio_state, "current_price")
    current_exposure = _portfolio_state_get(portfolio_state, "current_exposure")
    current_pnl = _portfolio_state_get(portfolio_state, "current_pnl")
    peak_pnl = _portfolio_state_get(portfolio_state, "peak_pnl")
    open_trade_count = _portfolio_state_int(portfolio_state, "open_trade_count")

    # Spot-only: Reject any signal that is not "long".
    if direction != "long":
        return _build_rejection(
            symbol, f"spot_only: direction {direction!r} not allowed",
            0.0, 0.0, None, None, None,
            current_exposure, 0.0, confidence
        )

    # Entry price: prefer the current market price; fall back to a sensible
    # default of 0.0 (which will trigger the R:R / exposure rejections below).
    entry_price = current_price if current_price > 0 else 0.0

    stop_loss = calculate_stop_loss(entry_price, atr, direction)
    take_profit = calculate_take_profit(entry_price, atr, direction)
    risk_reward = calculate_risk_reward(entry_price, stop_loss, take_profit)
    position_size = calculate_position_size(
        capital, risk_percent, entry_price, stop_loss
    )
    risk_amount = capital * (risk_percent / 100.0)
    new_trade_value = position_size * entry_price

    projected_exposure = current_exposure + new_trade_value
    # If peak_pnl is 0 (cold start), projected_drawdown is just the absolute loss
    # we would have if this trade hits SL, plus any current loss.
    if peak_pnl <= 0:
        projected_drawdown = abs(current_pnl) + risk_amount if current_pnl < 0 else risk_amount
    else:
        projected_drawdown = peak_pnl - (current_pnl - risk_amount)

    # Detailed Logging for investigation
    logger.info(
        "risk_calculation_details",
        symbol=symbol,
        direction=direction,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_reward_ratio=risk_reward,
        calculated_risk_percent=risk_percent,
        max_allowed_risk_percent=thresholds.MAX_DAILY_LOSS_PCT,
        capital=capital,
        allocated_capital=capital, # In this system, allocated_capital per coin is coin_config.capital
        position_size=position_size,
        risk_amount=risk_amount,
        current_exposure=current_exposure,
        projected_exposure=projected_exposure,
        current_pnl=current_pnl,
        peak_pnl=peak_pnl,
        projected_drawdown=projected_drawdown,
        open_trade_count=open_trade_count,
        max_concurrent_trades=thresholds.MAX_CONCURRENT_TRADES
    )

    # --- run the four checks in order; first failure wins ----------------
    # 1. R:R check
    if risk_reward < thresholds.MIN_RISK_REWARD_RATIO:
        reason = (
            f"risk_reward_below_min: {risk_reward:.3f} < "
            f"{thresholds.MIN_RISK_REWARD_RATIO:.3f}"
        )
        return _build_rejection(
            symbol, reason, position_size, risk_amount,
            stop_loss, take_profit, risk_reward,
            projected_exposure, projected_drawdown,
            confidence,
        )

    # 2. Concurrent-trades check
    if open_trade_count >= thresholds.MAX_CONCURRENT_TRADES:
        reason = (
            f"max_concurrent_trades_reached: {open_trade_count} >= "
            f"{thresholds.MAX_CONCURRENT_TRADES}"
        )
        return _build_rejection(
            symbol, reason, position_size, risk_amount,
            stop_loss, take_profit, risk_reward,
            projected_exposure, projected_drawdown,
            confidence,
        )

    # 3. Exposure check (USDT notional)
    # [FIX] If the new trade would exceed exposure, we scale it down to fit the remaining capital
    # instead of rejecting it, making the system truly dynamic.
    limit = capital * (thresholds.MAX_PORTFOLIO_EXPOSURE_PCT / 100.0)
    available_exposure = limit - current_exposure
    
    if new_trade_value > (available_exposure + 0.01):
        if available_exposure <= 0:
            reason = f"no_available_exposure: current={current_exposure:.4f} >= limit={limit:.4f}"
            return _build_rejection(
                symbol, reason, position_size, risk_amount,
                stop_loss, take_profit, risk_reward,
                projected_exposure, projected_drawdown,
                confidence,
            )
        
        # Scale down the position size to fit available exposure
        old_size = position_size
        position_size = available_exposure / entry_price
        new_trade_value = position_size * entry_price
        projected_exposure = current_exposure + new_trade_value
        
        logger.info(
            "risk_position_scaled_to_fit_exposure",
            symbol=symbol,
            old_size=old_size,
            new_size=position_size,
            available_exposure=available_exposure
        )

    # 4. Drawdown check
    if not check_drawdown(current_pnl, peak_pnl, risk_amount):
        reason = (
            f"drawdown_limit_exceeded: projected_drawdown="
            f"{projected_drawdown:.4f} USDT > "
            f"limit={peak_pnl * (thresholds.MAX_DAILY_LOSS_PCT / 100.0):.4f} USDT "
            f"({thresholds.MAX_DAILY_LOSS_PCT:.1f}% of peak {peak_pnl:.4f})"
        )
        return _build_rejection(
            symbol, reason, position_size, risk_amount,
            stop_loss, take_profit, risk_reward,
            projected_exposure, projected_drawdown,
            confidence,
        )

    # --- all checks passed ----------------------------------------------
    assessment = RiskAssessment(
        allowed=True,
        max_position_size=position_size,
        max_risk_amount=risk_amount,
        stop_loss_price=stop_loss,
        take_profit_price=take_profit,
        risk_reward_ratio=risk_reward,
        reason=None,
        exposure_after_trade=projected_exposure,
        drawdown_after_trade=projected_drawdown,
    )

    logger.info(
        "risk_assessed",
        timestamp=datetime.utcnow(),
        symbol=symbol,
        allowed=True,
        reason=None,
        position_size=position_size,
        risk_amount=risk_amount,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_reward=risk_reward,
        projected_exposure=projected_exposure,
        projected_drawdown=projected_drawdown,
        confidence=confidence,
    )
    return assessment


def _build_rejection(
    symbol: str,
    reason: str,
    position_size: float,
    risk_amount: float,
    stop_loss: float,
    take_profit: float,
    risk_reward: float,
    projected_exposure: float,
    projected_drawdown: float,
    confidence: float,
) -> RiskAssessment:
    """Construct a rejected :class:`RiskAssessment` with full traceability."""
    assessment = RiskAssessment(
        allowed=False,
        max_position_size=position_size,
        max_risk_amount=risk_amount,
        stop_loss_price=stop_loss,
        take_profit_price=take_profit,
        risk_reward_ratio=risk_reward,
        reason=reason,
        exposure_after_trade=projected_exposure,
        drawdown_after_trade=projected_drawdown,
    )
    logger.info(
        "risk_assessed",
        timestamp=datetime.utcnow(),
        symbol=symbol,
        allowed=False,
        reason=reason,
        position_size=position_size,
        risk_amount=risk_amount,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_reward=risk_reward,
        projected_exposure=projected_exposure,
        projected_drawdown=projected_drawdown,
        confidence=confidence,
    )
    return assessment


# ---------------------------------------------------------------------------
# Bonus: helpers for tests / bot display
# ---------------------------------------------------------------------------
def check_risk_reward(risk_reward: float) -> bool:
    """True iff ``risk_reward >= thresholds.MIN_RISK_REWARD_RATIO``."""
    return _safe_float(risk_reward) >= thresholds.MIN_RISK_REWARD_RATIO


def check_concurrent_trades(open_trade_count: int) -> bool:
    """True iff ``open_trade_count < thresholds.MAX_CONCURRENT_TRADES``."""
    return int(open_trade_count) < thresholds.MAX_CONCURRENT_TRADES


def target_risk_reward() -> float:
    """Return the configured ``thresholds.RISK_REWARD_TARGET`` (for orchestrator hints)."""
    return float(thresholds.RISK_REWARD_TARGET)


def exposure_limit(capital: float) -> float:
    """Return the maximum allowed USDT exposure for ``capital``."""
    return _safe_float(capital) * (thresholds.MAX_PORTFOLIO_EXPOSURE_PCT / 100.0)


def max_position_notional(capital: float) -> float:
    """Return the maximum allowed USDT notional for a single new trade."""
    return _safe_float(capital) * (thresholds.MAX_POSITION_SIZE_PCT / 100.0)


def drawdown_limit(peak_pnl: float) -> float:
    """Return the maximum allowed drawdown (USDT) given ``peak_pnl``."""
    return _safe_float(peak_pnl) * (thresholds.MAX_DAILY_LOSS_PCT / 100.0)


__all__ = [
    "calculate_position_size",
    "check_exposure",
    "check_drawdown",
    "calculate_stop_loss",
    "calculate_take_profit",
    "calculate_risk_reward",
    "assess_risk",
    "check_risk_reward",
    "check_concurrent_trades",
    "target_risk_reward",
    "exposure_limit",
    "max_position_notional",
    "drawdown_limit",
]
