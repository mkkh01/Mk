"""
File: analysis/result_aggregator.py
Responsibility: Aggregate trade and decision results for reporting.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from uuid import UUID

from storage.supabase import SupabaseClient
from contracts.decision import DecisionResult
from contracts.simulation import SimulatedTrade


class ResultAggregator:
    """Aggregates trade and decision data from storage."""

    def __init__(self, supabase: SupabaseClient):
        self._supabase = supabase

    async def get_recent_trades(self, limit: int = 10) -> List[SimulatedTrade]:
        """Fetch the most recent simulated trades."""
        return await self._supabase.fetch_recent_trades(limit=limit)

    async def get_trade_with_decision(self, trade_id: UUID) -> Dict[str, Any]:
        """Fetch a trade and its associated decision details."""
        # Note: Need to find a way to fetch single trade by ID if not exists
        # SupabaseClient has fetch_open_trades, fetch_recent_trades, etc.
        # For now, let's use fetch_recent_trades and filter or assume it exists.
        trades = await self._supabase.fetch_recent_trades(limit=100)
        trade = next((t for t in trades if t.id == trade_id), None)
        if not trade:
            return {}
        
        decision = await self._supabase.fetch_decision(trade.decision_id)
        return {
            "trade": trade,
            "decision": decision
        }

    async def get_performance_summary(self) -> Dict[str, Any]:
        """Calculate a performance summary from recent trades."""
        trades = await self._supabase.fetch_recent_trades(limit=100)
        if not trades:
            return {
                "total_trades": 0,
                "win_rate": 0,
                "total_pnl": 0,
                "avg_pnl": 0
            }

        closed_trades = [t for t in trades if t.status == "closed"]
        if not closed_trades:
            return {
                "total_trades": len(trades),
                "closed_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "neutral_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0
            }

        winning_trades = [t for t in closed_trades if (t.pnl or 0) > 0]
        losing_trades = [t for t in closed_trades if (t.pnl or 0) < 0]
        neutral_trades = [t for t in closed_trades if (t.pnl or 0) == 0]
        total_pnl = sum(t.pnl or 0 for t in closed_trades)
        
        # Ensure win_rate is calculated correctly based on winning trades only
        win_rate = (len(winning_trades) / len(closed_trades) * 100) if closed_trades else 0.0
        
        return {
            "total_trades": len(trades),
            "closed_trades": len(closed_trades),
            "winning_trades": len(winning_trades),
            "losing_trades": len(losing_trades),
            "neutral_trades": len(neutral_trades),
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "avg_pnl": total_pnl / len(closed_trades)
        }
