"""
File: app/workflow_endpoints.py
Responsibility: Provide REST API endpoints for retrieving and displaying workflow logs
from Supabase. These endpoints are used by Render's log viewer to show trade analysis,
decisions, and results in a structured format.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional, List

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel
from contracts.decision import DecisionResult
from contracts.simulation import SimulatedTrade
from storage.supabase import SupabaseClient
from storage.redis_cache import RedisCache

router = APIRouter(prefix="/api/workflow", tags=["workflow"])

# ============================================================================
# Dependencies
# ============================================================================
def get_supabase_client(request: Request) -> SupabaseClient:
    return request.app.state.supabase

def get_redis_cache(request: Request) -> RedisCache:
    return request.app.state.redis

# ============================================================================
# Response Models
# ============================================================================
class DecisionBriefResponse(BaseModel):
    created_at: Optional[str]
    final_verdict: bool
    score: float
    confidence: float
    rejection_reason: Optional[str]

class TradeBriefResponse(BaseModel):
    opened_at: Optional[str]
    status: str
    direction: str
    entry_price: float
    pnl: Optional[float]
    close_reason: Optional[str]

class WorkflowStatusResponse(BaseModel):
    symbol: str
    recent_decisions: List[DecisionBriefResponse]
    recent_trades: List[TradeBriefResponse]

class DecisionSummaryResponse(BaseModel):
    symbol: str
    period_hours: int
    total_decisions: int
    approved_decisions: int
    rejected_decisions: int
    approval_rate: float
    top_rejection_reasons: dict[str, int]

class TradeSummaryResponse(BaseModel):
    symbol: str
    period_hours: int
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float

# ============================================================================
# Endpoints
# ============================================================================
@router.get("/status/{symbol}", response_model=WorkflowStatusResponse)
async def get_workflow_status(
    symbol: str,
    supabase: SupabaseClient = Depends(get_supabase_client),
):
    """Get workflow status for a specific symbol."""
    decisions = await supabase.fetch_decisions_by_symbol(symbol=symbol, limit=10)
    trades = await supabase.fetch_trades_by_symbol(symbol=symbol, limit=10)
    
    return WorkflowStatusResponse(
        symbol=symbol,
        recent_decisions=[
            DecisionBriefResponse(
                created_at=d.timestamp.isoformat() if hasattr(d, 'timestamp') else None,
                final_verdict=d.final_verdict,
                score=d.score,
                confidence=d.confidence,
                rejection_reason=d.rejection_reason,
            )
            for d in decisions
        ],
        recent_trades=[
            TradeBriefResponse(
                opened_at=t.opened_at.isoformat() if t.opened_at else None,
                status=t.status,
                direction=t.direction,
                entry_price=float(t.entry_price),
                pnl=float(t.pnl) if t.pnl else None,
                close_reason=t.close_reason,
            )
            for t in trades
        ],
    )

@router.get("/decisions/{symbol}", response_model=DecisionSummaryResponse)
async def get_decision_summary(
    symbol: str, 
    hours: int = Query(24, ge=1, le=720),
    supabase: SupabaseClient = Depends(get_supabase_client),
):
    """Get decision summary for a symbol."""
    decisions = await supabase.fetch_decisions_by_symbol(symbol=symbol, limit=1000)
    
    # Filter by time
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
    decisions = [
        d for d in decisions
        if (hasattr(d, 'timestamp') and d.timestamp >= cutoff_time)
    ]
    
    total = len(decisions)
    approved = sum(1 for d in decisions if d.final_verdict)
    rejected = total - approved
    
    rejection_reasons = {}
    for d in decisions:
        if not d.final_verdict and d.rejection_reason:
            reason = d.rejection_reason
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
    
    approval_rate = (approved / total * 100) if total > 0 else 0.0
    
    return DecisionSummaryResponse(
        symbol=symbol,
        period_hours=hours,
        total_decisions=total,
        approved_decisions=approved,
        rejected_decisions=rejected,
        approval_rate=round(approval_rate, 2),
        top_rejection_reasons=dict(sorted(
            rejection_reasons.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:5]),
    )

@router.get("/trades/{symbol}", response_model=TradeSummaryResponse)
async def get_trade_summary(
    symbol: str, 
    hours: int = Query(24, ge=1, le=720),
    supabase: SupabaseClient = Depends(get_supabase_client),
):
    """Get trade summary for a symbol."""
    trades = await supabase.fetch_trades_by_symbol(symbol=symbol, limit=1000)
    
    # Filter by time and closed trades only
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
    trades = [
        t for t in trades
        if t.status == "closed"
        and t.closed_at and t.closed_at >= cutoff_time
    ]
    
    total = len(trades)
    winning = sum(1 for t in trades if t.pnl and t.pnl > 0)
    losing = total - winning
    total_pnl = sum(t.pnl or 0 for t in trades)
    
    win_rate = (winning / total * 100) if total > 0 else 0.0
    
    return TradeSummaryResponse(
        symbol=symbol,
        period_hours=hours,
        total_trades=total,
        winning_trades=winning,
        losing_trades=losing,
        win_rate=round(win_rate, 2),
        total_pnl=round(total_pnl, 2),
    )

def setup_workflow_endpoints(app: Any) -> None:
    """Setup workflow endpoints on a FastAPI app."""
    app.include_router(router)
