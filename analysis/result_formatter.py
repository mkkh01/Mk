"""
File: analysis/result_formatter.py
Responsibility: Format trade analysis results for various outputs (Logs, Telegram, JSON).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from contracts.decision import DecisionResult
from contracts.simulation import SimulatedTrade


class ResultFormatter:
    """Formats trade results into human-readable and machine-readable formats."""

    @staticmethod
    def format_trade_log(trade: SimulatedTrade, decision: Optional[DecisionResult] = None) -> str:
        """Format a trade result for console/file logs."""
        status_emoji = "✅" if (trade.pnl or 0) > 0 else "❌" if trade.status == "closed" else "⏳"
        pnl_str = f"{trade.pnl:.2f}" if trade.pnl is not None else "N/A"
        
        lines = [
            f"TRADE {trade.id} {status_emoji}",
            f"Symbol: {trade.symbol} | Direction: {trade.direction}",
            f"Entry: {trade.entry_price} | PnL: {pnl_str} ({trade.status})",
        ]
        
        if decision:
            strategy_name = "N/A"
            if decision.component_signals:
                strategy_name = decision.component_signals[0].strategy_name
            elif decision.entry and hasattr(decision.entry, 'strategy_name'): # Fallback for safety
                strategy_name = getattr(decision.entry, 'strategy_name')
                
            lines.append(f"Strategy: {strategy_name}")
            lines.append(f"Score: {decision.score:.4f} | Confidence: {decision.confidence:.4f}")
            lines.append(f"Reason: {decision.rejection_reason or 'Entry Approved'}")

        return "\n".join(lines)

    @staticmethod
    def format_trade_telegram(trade: SimulatedTrade, decision: Optional[DecisionResult] = None) -> str:
        """Format a trade result for Telegram messages."""
        status_emoji = "🟢" if (trade.pnl or 0) > 0 else "🔴" if trade.status == "closed" else "🔵"
        pnl_str = f"{trade.pnl:.4f}" if trade.pnl is not None else "قيد التنفيذ"
        
        msg = (
            f"{status_emoji} *تقرير صفقة: {trade.symbol}*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🔹 *الاتجاه:* {trade.direction.upper()}\n"
            f"🔹 *سعر الدخول:* `{trade.entry_price:.6f}`\n"
            f"🔹 *الحالة:* {trade.status}\n"
            f"🔹 *الربح/الخسارة:* `{pnl_str}`\n"
        )
        
        if decision:
            strategy_name = "N/A"
            if decision.component_signals:
                strategy_name = decision.component_signals[0].strategy_name
            elif decision.entry and hasattr(decision.entry, 'strategy_name'):
                strategy_name = getattr(decision.entry, 'strategy_name')

            msg += (
                f"🔹 *الاستراتيجية:* {strategy_name}\n"
                f"🔹 *التقييم:* `{decision.score:.2f}` | *الثقة:* `{decision.confidence:.2f}`\n"
                f"🔹 *السبب:* {decision.rejection_reason or 'تمت الموافقة'}\n"
            )
            
        msg += f"━━━━━━━━━━━━━━━\n"
        msg += f"🕒 {trade.opened_at.strftime('%Y-%m-%d %H:%M:%S')}"
        
        return msg

    @staticmethod
    def to_json(trade: SimulatedTrade, decision: Optional[DecisionResult] = None) -> str:
        """Format a trade result as JSON."""
        data = {
            "trade": trade.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json") if decision else None
        }
        return json.dumps(data, indent=2)

    @staticmethod
    def format_summary_telegram(summary: Dict[str, Any]) -> str:
        """Format a performance summary for Telegram."""
        # [FIX] Use .get() for all keys to prevent KeyError if summary is incomplete
        total_trades = summary.get('total_trades', 0)
        win_rate = summary.get('win_rate', 0.0)
        total_pnl = summary.get('total_pnl', 0.0)
        avg_pnl = summary.get('avg_pnl', 0.0)
        
        msg = (
            f"📊 *ملخص أداء النظام*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📈 *إجمالي الصفقات:* {total_trades}\n"
            f"✅ *الصفقات الناجحة:* {summary.get('winning_trades', 0)}\n"
            f"➖ *الصفقات المتعادلة:* {summary.get('neutral_trades', 0)}\n"
            f"❌ *الصفقات الخاسرة:* {summary.get('losing_trades', 0)}\n"
            f"🎯 *نسبة النجاح:* `{win_rate:.2f}%`\n"
            f"💰 *إجمالي الأرباح:* `{total_pnl:.2f}`\n"
            f"📉 *متوسط الربح:* `{avg_pnl:.2f}`\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🕒 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return msg
