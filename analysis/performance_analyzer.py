"""
File: analysis/performance_analyzer.py
Responsibility: Deep analysis of strategy and rejection performance.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from collections import Counter

from contracts.decision import DecisionResult
from contracts.simulation import SimulatedTrade


class PerformanceAnalyzer:
    """Analyzes strategy effectiveness and rejection patterns."""

    @staticmethod
    def analyze_strategies(trades: List[SimulatedTrade], decisions: List[DecisionResult]) -> Dict[str, Any]:
        """Analyze which strategies are most effective."""
        strategy_stats = {}
        
        # Map decisions to trades
        decision_map = {d.id: d for d in decisions}
        
        for trade in trades:
            decision = decision_map.get(trade.decision_id)
            if not decision:
                continue
            
            strat_name = "N/A"
            if decision.component_signals:
                strat_name = decision.component_signals[0].strategy_name
            elif decision.entry and hasattr(decision.entry, 'strategy_name'):
                strat_name = getattr(decision.entry, 'strategy_name')
            if strat_name not in strategy_stats:
                strategy_stats[strat_name] = {"total": 0, "wins": 0, "pnl": 0.0}
            
            stats = strategy_stats[strat_name]
            stats["total"] += 1
            if (trade.pnl or 0) > 0:
                stats["wins"] += 1
            stats["pnl"] += (trade.pnl or 0)
            
        return strategy_stats

    @staticmethod
    def analyze_rejections(decisions: List[DecisionResult]) -> Dict[str, Any]:
        """Analyze the most common reasons for trade rejection."""
        rejections = [d.rejection_reason for d in decisions if not d.final_verdict and d.rejection_reason]
        counts = Counter(rejections)
        
        return {
            "total_rejections": len(rejections),
            "top_reasons": dict(counts.most_common(5))
        }
