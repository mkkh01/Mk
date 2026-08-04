
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from config import thresholds

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel

from contracts.config import CoinConfig
from monitoring.logger import get_logger
from portfolio.performance import PerformanceCalculator
from storage.redis_cache import RedisCache
from storage.supabase import SupabaseClient

logger = get_logger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# Dependency to get SupabaseClient and RedisCache instances
def get_supabase_client(request: Request) -> SupabaseClient:
    return request.app.state.supabase

def get_redis_cache(request: Request) -> RedisCache:
    return request.app.state.redis

def get_performance_calculator(request: Request) -> PerformanceCalculator:
    return request.app.state.performance_calculator


# ============================================================================
# Response Models
# ============================================================================
class LivePriceResponse(BaseModel):
    symbol: str
    price: float
    timestamp: datetime


class ThresholdsResponse(BaseModel):
    # Market Structure
    SWING_LOOKBACK: int
    MIN_SWING_SIZE_PCT: float
    BOS_CONFIRMATION_CANDLES: int
    CHOCH_CONFIRMATION_CANDLES: int

    # Smart Money Concepts
    OB_MIN_IMPULSE_PCT: float
    OB_MAX_CANDLES_BACK: int
    FVG_MIN_GAP_PCT: float
    LIQUIDITY_SWEEP_STRENGTH_THRESHOLD: float
    LIQUIDITY_CLUSTER_TOLERANCE_PCT: float
    LIQUIDITY_CLUSTER_LOOKBACK: int

    # Trend
    TREND_EMA_FAST: int
    TREND_EMA_SLOW: int
    TREND_ADX_THRESHOLD: float
    TREND_STRENGTH_THRESHOLD: float
    ADX_PERIOD: int

    # Momentum
    MOMENTUM_RSI_PERIOD: int
    MOMENTUM_RSI_OVERBOUGHT: int
    MOMENTUM_RSI_OVERSOLD: int
    MOMENTUM_MACD_FAST: int
    MOMENTUM_MACD_SLOW: int
    MOMENTUM_MACD_SIGNAL: int
    MOMENTUM_STOCH_PERIOD: int
    MOMENTUM_STOCH_SMOOTH_K: int
    MOMENTUM_STOCH_SMOOTH_D: int
    MOMENTUM_STOCH_OVERBOUGHT: int
    MOMENTUM_STOCH_OVERSOLD: int

    # Volatility
    VOLATILITY_ATR_PERIOD: int
    VOLATILITY_ATR_MULTIPLIER_SL: float
    VOLATILITY_ATR_MULTIPLIER_TP: float
    VOLATILITY_BB_PERIOD: int
    VOLATILITY_BB_STD: float
    HIGH_VOLATILITY_THRESHOLD: float
    VOLATILITY_BB_RANGING_PCT: float

    # Sessions (UTC hours)
    ASIAN_START_UTC: int
    ASIAN_END_UTC: int
    LONDON_START_UTC: int
    LONDON_END_UTC: int
    NY_START_UTC: int
    NY_END_UTC: int

    # Risk Management
    MAX_PORTFOLIO_EXPOSURE_PCT: float
    MAX_POSITION_SIZE_PCT: float
    MAX_DAILY_LOSS_PCT: float
    MAX_CONCURRENT_TRADES: int
    MIN_RISK_REWARD_RATIO: float
    RISK_REWARD_TARGET: float

    # Confidence Scoring
    CONFIDENCE_THRESHOLD: float
    HTF_ALIGNMENT_WEIGHT: float
    STRUCTURE_WEIGHT: float
    MOMENTUM_WEIGHT: float
    LIQUIDITY_WEIGHT: float
    SESSION_WEIGHT: float
    REGIME_MODIFIER_TRENDING: float
    REGIME_MODIFIER_RANGING: float
    REGIME_MODIFIER_VOLATILE: float

    # Entry Rules
    ENTRY_LIMIT_OFFSET_PCT: float
    ENTRY_TIMEOUT_MINUTES: int
    MAX_ENTRY_RETRIES: int

    # Simulation
    MAKER_FEE_PCT: float
    TAKER_FEE_PCT: float
    SLIPPAGE_PCT: float

    # WebSocket / Ingest
    WS_INITIAL_BACKOFF_SECONDS: int
    WS_MAX_BACKOFF_SECONDS: int
    WS_STABLE_RESET_SECONDS: int
    WS_STALE_MULTIPLIER: float
    WS_REST_RETRY_COUNT: int
    WS_RESUME_PAD_CANDLES: int


class SystemHealthResponse(BaseModel):
    scan_cycles: int
    pairs_analyzed: int
    strategies_run: int
    opportunities_found: int
    opportunities_rejected: int
    rejection_reasons: dict[str, int]
    errors: int
    last_data_at: Optional[datetime]
    total_score_sum: float
    total_confidence_sum: float
    total_analysis_time_ms: float
    db_writes: int
    telegram_sent: int
    engine_running: bool
    active_coins: list[str]


class CycleSummaryResponse(BaseModel):
    """Dynamic cycle summary derived from health_manager stats."""
    pairs_analyzed: int
    bullish: int
    bearish: int
    sideways: int
    signals_found: int
    approved: int
    rejected: int
    rejection_reasons: dict[str, int]
    avg_strategy_score: float
    avg_confidence: float
    avg_analysis_time_ms: float
    telegram_messages: int
    database_writes: int
    warnings: int
    errors: int
    system_health: str
    formatted_text: str
    analyses_executed: int
    scan_cycles: int


class OverallPerformanceResponse(BaseModel):
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    average_pnl: float
    max_drawdown: float
    max_drawdown_percent: float
    sharpe_ratio: Optional[float]
    profit_factor: Optional[float]
    consecutive_wins: int
    consecutive_losses: int


class StrategyPerformanceResponse(BaseModel):
    symbol: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    average_pnl: float
    max_drawdown: float
    max_drawdown_percent: float
    sharpe_ratio: Optional[float]
    profit_factor: Optional[float]
    consecutive_wins: int
    consecutive_losses: int


# ============================================================================
# Endpoints
# ============================================================================
@router.get("/live_prices/{symbol}", response_model=LivePriceResponse)
async def get_live_price_endpoint(
    symbol: str,
    redis_cache: RedisCache = Depends(get_redis_cache),
) -> LivePriceResponse:
    price_data = await redis_cache.get_live_price(symbol)
    if price_data:
        price, timestamp = price_data
        return LivePriceResponse(symbol=symbol, price=price, timestamp=timestamp)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Live price not found")


@router.get("/thresholds", response_model=ThresholdsResponse)
async def get_thresholds_endpoint():
    from config import thresholds
    # Dynamically get all uppercase attributes from the thresholds module
    threshold_values = {
        key: getattr(thresholds, key)
        for key in dir(thresholds)
        if key.isupper() and not key.startswith("__")
    }
    return ThresholdsResponse(**threshold_values)


@router.get("/cycle_summary", response_model=CycleSummaryResponse)
async def get_cycle_summary_endpoint(request: Request) -> CycleSummaryResponse:
    """Return a dynamic cycle-summary payload built from health_manager stats.

    The numbers mirror exactly what the Render log stream shows in the
    ``health_summary`` event (formatted by ``format_cycle_summary``), so the
    dashboard can display the same information in a visual card.
    """
    ct_app_instance = request.app.state.ct_app_instance
    if not ct_app_instance:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Application not initialized")

    from monitoring.health_manager import health_manager, HealthStatus

    stats = await health_manager.get_stats()
    analyzed_count = stats.get("analyses_executed", 0)

    # Compute averages from accumulated sums
    analyses = max(1, analyzed_count)
    avg_score = (stats.get("total_score_sum", 0.0) / analyses) * 100.0
    avg_conf = (stats.get("total_confidence_sum", 0.0) / analyses) * 100.0
    avg_time = stats.get("total_analysis_time_ms", 0.0) / analyses

    # Derive system health
    health_summary = await health_manager.get_overall_health()
    status_map = {
        HealthStatus.OK: "EXCELLENT",
        HealthStatus.WARNING: "GOOD",
        HealthStatus.ERROR: "POOR",
        HealthStatus.CRITICAL: "CRITICAL",
    }
    system_health = status_map.get(health_summary["status"], "UNKNOWN")

    # Build formatted text (same formatter used in logs)
    from monitoring.report_formatter import format_cycle_summary
    formatted_text = format_cycle_summary(
        pairs_analyzed=len(stats.get("unique_symbols_seen", set())),
        bullish_count=stats.get("bullish_count", 0),
        bearish_count=stats.get("bearish_count", 0),
        sideways_count=stats.get("sideways_count", 0),
        signals_found=stats.get("signals_emitted", 0),
        approved_count=stats.get("opportunities_found", 0),
        rejected_count=stats.get("opportunities_rejected", 0),
        rejection_reasons=stats.get("rejection_reasons", {}),
        avg_strategy_score=avg_score,
        avg_confidence=avg_conf,
        avg_analysis_time=avg_time,
        telegram_count=stats.get("telegram_sent", 0),
        database_writes=stats.get("db_writes", 0),
        warnings_count=stats.get("warnings_count", 0),
        errors_count=stats.get("errors_count", 0),
        system_health=system_health,
    )

    return CycleSummaryResponse(
        pairs_analyzed=len(stats.get("unique_symbols_seen", set())),
        bullish=stats.get("bullish_count", 0),
        bearish=stats.get("bearish_count", 0),
        sideways=stats.get("sideways_count", 0),
        signals_found=stats.get("signals_emitted", 0),
        approved=stats.get("opportunities_found", 0),
        rejected=stats.get("opportunities_rejected", 0),
        rejection_reasons=stats.get("rejection_reasons", {}),
        avg_strategy_score=round(avg_score, 1),
        avg_confidence=round(avg_conf, 1),
        avg_analysis_time_ms=round(avg_time, 0),
        telegram_messages=stats.get("telegram_sent", 0),
        database_writes=stats.get("db_writes", 0),
        warnings=stats.get("warnings_count", 0),
        errors=stats.get("errors_count", 0),
        system_health=system_health,
        formatted_text=formatted_text,
        analyses_executed=analyzed_count,
        scan_cycles=stats.get("scan_cycles", 0),
    )


@router.get("/system_health", response_model=SystemHealthResponse)
async def get_system_health_endpoint(request: Request) -> SystemHealthResponse:
    ct_app_instance = request.app.state.ct_app_instance
    if not ct_app_instance:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Application not initialized")
    
    from monitoring.health_manager import health_manager
    stats = await health_manager.get_stats()
    
    health_stats = {
        "scan_cycles": stats.get("scan_cycles", 0),
        "pairs_analyzed": stats.get("analyses_executed", 0),
        "strategies_run": stats.get("strategies_run", 0),
        "opportunities_found": stats.get("opportunities_found", 0),
        "opportunities_rejected": stats.get("opportunities_rejected", 0),
        "rejection_reasons": {}, # Requires dedicated analytics
        "errors": stats.get("errors", 0),
        "last_data_at": None, # Requires dedicated analytics
        "total_score_sum": 0.0, # Requires dedicated analytics
        "total_confidence_sum": 0.0, # Requires dedicated analytics
        "total_analysis_time_ms": 0.0, # Requires dedicated analytics
        "db_writes": stats.get("db_writes", 0),
        "telegram_sent": stats.get("telegram_sent", 0),
        "engine_running": ct_app_instance._engine_running,
    }
    
    # Fetch active coins for the dashboard
    active_coins = []
    try:
        coins = await ct_app_instance._supabase.fetch_all_coins(only_active=True)
        active_coins = [coin.symbol for coin in coins]
    except Exception as exc:
        logger.error(
            "error",
            timestamp=datetime.now(timezone.utc),
            module="app.dashboard_endpoints",
            error_type=type(exc).__name__,
            error_message=f"Failed to fetch active coins for health dashboard: {exc}",
        )

    health_stats["active_coins"] = active_coins

    return SystemHealthResponse(**health_stats)


@router.get("/overall_performance", response_model=OverallPerformanceResponse)
async def get_overall_performance_endpoint(
    performance_calculator: PerformanceCalculator = Depends(get_performance_calculator),
) -> OverallPerformanceResponse:
    metrics = await performance_calculator.calculate_metrics()
    if metrics:
        return OverallPerformanceResponse(
            total_trades=metrics.total_trades,
            winning_trades=metrics.winning_trades,
            losing_trades=metrics.losing_trades,
            win_rate=metrics.win_rate,
            total_pnl=metrics.total_pnl,
            average_pnl=metrics.average_pnl,
            max_drawdown=metrics.max_drawdown,
            max_drawdown_percent=metrics.max_drawdown_percent,
            sharpe_ratio=metrics.sharpe_ratio,
            profit_factor=metrics.profit_factor,
            consecutive_wins=metrics.consecutive_wins,
            consecutive_losses=metrics.consecutive_losses,
        )
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Overall performance metrics not found")


@router.get("/strategy_performance/{symbol}", response_model=StrategyPerformanceResponse)
async def get_strategy_performance_endpoint(
    symbol: str,
    performance_calculator: PerformanceCalculator = Depends(get_performance_calculator),
) -> StrategyPerformanceResponse:
    metrics = await performance_calculator.calculate_metrics(symbol=symbol)
    if metrics:
        return StrategyPerformanceResponse(
            symbol=symbol,
            total_trades=metrics.total_trades,
            winning_trades=metrics.winning_trades,
            losing_trades=metrics.losing_trades,
            win_rate=metrics.win_rate,
            total_pnl=metrics.total_pnl,
            average_pnl=metrics.average_pnl,
            max_drawdown=metrics.max_drawdown,
            max_drawdown_percent=metrics.max_drawdown_percent,
            sharpe_ratio=metrics.sharpe_ratio,
            profit_factor=metrics.profit_factor,
            consecutive_wins=metrics.consecutive_wins,
            consecutive_losses=metrics.consecutive_losses,
        )
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Performance metrics not found for symbol")


def setup_dashboard_endpoints(app: Any, ct_app_instance: Any) -> None:
    app.state.ct_app_instance = ct_app_instance
    app.include_router(router)
