"""
File: monitoring/report_formatter.py
Responsibility: Format analysis results into visual "Analysis Blocks" for Render logs.
Provides structured, readable, and professional report formatting as per user requirements.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def format_progress_bar(percentage: float, width: int = 10) -> str:
    """Create a visual progress bar string."""
    filled_count = int(round(percentage * width / 100))
    bar = "█" * filled_count + "░" * (width - filled_count)
    return f"{bar} {percentage:.0f}%"


def format_analysis_report(
    symbol: str,
    timeframe: str,
    candle_time: datetime,
    last_price: float,
    regime: str,
    volatility: str,
    liquidity: str,
    volume_status: str,
    indicators: dict[str, Any],
    structure: dict[str, Any],
    strategy_scores: dict[str, float],
    decision_scores: dict[str, Any],
    total_score: float,
    confidence: float,
    quality: float,
    probability: float,
    risk_mgmt: dict[str, Any],
    final_decision: str,
    reasons: list[str],
    execution: dict[str, Any],
) -> str:
    """Format a full analysis report block for a single symbol."""
    
    lines = []
    sep_heavy = "══════════════════════════════════════════════════════════════════════════════"
    sep_light = "──────────────────────────────────────────────────────────────────────────────"
    
    # Header
    lines.append(sep_heavy)
    lines.append("📊 ANALYSIS REPORT")
    lines.append(sep_heavy)
    lines.append("")
    
    # Basic Info
    lines.append(f"🪙 Symbol        : {symbol}")
    lines.append(f"🕒 Timeframe     : {timeframe}")
    lines.append(f"📅 Candle Time   : {candle_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    # [FIX] Use dynamic precision for Last Price to support low-priced coins (VTHO, etc.)
    price_precision = 2 if last_price >= 1 else 6
    lines.append(f"💰 Last Price    : {last_price:.{price_precision}f}")
    lines.append(f"📈 Market Regime : {regime}")
    lines.append(f"📊 Volatility    : {volatility}")
    lines.append(f"🌊 Liquidity     : {liquidity}")
    lines.append(f"📦 Volume        : {volume_status}")
    lines.append("")
    
    # Indicators
    lines.append(sep_light)
    lines.append("📈 INDICATORS")
    lines.append(sep_light)
    lines.append("")
    for name, val in indicators.items():
        if isinstance(val, bool):
            status = "✅" if val else "❌"
            lines.append(f"{name:<16} {status} {('Bullish' if val else 'Bearish')}")
        else:
            lines.append(f"{name:<16} {val}")
    lines.append("")
    lines.append(f"Overall Indicator Score : {decision_scores.get('indicator_score', 0)}%")
    lines.append("")
    
    # Market Structure
    lines.append(sep_light)
    lines.append("🏛 MARKET STRUCTURE")
    lines.append(sep_light)
    lines.append("")
    for name, val in structure.items():
        status = "✅" if val else "❌"
        label = "Detected" if val else "None"
        if name.lower() == "trend":
            label = "Bullish" if val else "Bearish"
        elif name.lower() == "higher timeframe":
            label = "Aligned" if val else "Misaligned"
        elif name.lower() in ["premium zone", "discount zone"]:
            label = ""
        lines.append(f"{name:<20} {status} {label}")
    lines.append("")
    lines.append(f"Structure Score : {decision_scores.get('structure_score', 0)}%")
    lines.append("")
    
    # Strategy Scores
    lines.append(sep_light)
    lines.append("🧠 STRATEGY SCORES")
    lines.append(sep_light)
    lines.append("")
    for name, score in strategy_scores.items():
        if name.lower() == "risk filter":
            status = "PASS" if score >= 1.0 else "FAIL"
            lines.append(f"{name:<20} {'█'*10} {status}")
        else:
            lines.append(f"{name:<20} {format_progress_bar(score)}")
    lines.append("")
    
    # Decision Engine
    lines.append(sep_light)
    lines.append("📋 DECISION ENGINE")
    lines.append(sep_light)
    lines.append("")
    lines.append(f"Trend Score          : {decision_scores.get('trend_score', 0)} / 25")
    lines.append(f"Momentum Score       : {decision_scores.get('momentum_score', 0)} / 20")
    lines.append(f"Liquidity Score      : {decision_scores.get('liquidity_score', 0)} / 20")
    lines.append(f"Volume Score         : {decision_scores.get('volume_score', 0)} / 15")
    lines.append(f"SMC Score            : {decision_scores.get('smc_score', 0)} / 20")
    lines.append("")
    lines.append("────────────────────────────────")
    lines.append("")
    lines.append(f"Total Score          : {total_score:.0f} / 100")
    lines.append(f"Confidence           : {confidence:.0f}%")
    lines.append(f"Quality              : {quality:.0f}%")
    lines.append(f"Probability          : {probability:.0f}%")
    lines.append("")
    
    # Risk Management
    if risk_mgmt:
        lines.append(sep_light)
        lines.append("💼 RISK MANAGEMENT")
        lines.append(sep_light)
        lines.append("")
        # [FIX] Use dynamic precision for risk levels
        entry_p = risk_mgmt.get('entry_price', 0)
        p_prec = 2 if entry_p >= 1 else 6
        lines.append(f"Entry Price          : {entry_p:.{p_prec}f}")
        lines.append(f"Stop Loss            : {risk_mgmt.get('stop_loss', 0):.{p_prec}f}")
        lines.append(f"Take Profit          : {risk_mgmt.get('take_profit', 0):.{p_prec}f}")
        lines.append(f"Risk                 : {risk_mgmt.get('risk_pct', 0):.2f}%")
        lines.append(f"Reward               : {risk_mgmt.get('reward_pct', 0):.2f}%")
        lines.append(f"Risk/Reward          : 1 : {risk_mgmt.get('rr_ratio', 0):.2f}")
        lines.append(f"Capital Allocation   : {risk_mgmt.get('capital_alloc', 0):.0f}%")
        lines.append(f"Position Size        : {risk_mgmt.get('pos_size', 0):.4f} {symbol.replace('USDT', '')}")
        lines.append("")
    
    # Final Decision
    lines.append(sep_light)
    lines.append("🚦 FINAL DECISION")
    lines.append(sep_light)
    lines.append("")
    decision_icon = "✅" if "BUY" in final_decision or "SELL" in final_decision else "❌"
    lines.append(f"Decision             : {decision_icon} {final_decision}")
    lines.append("")
    lines.append("Reasons:")
    for reason in reasons:
        lines.append(f" • {reason}")
    lines.append("")
    
    # Execution
    lines.append(sep_light)
    lines.append("📨 EXECUTION")
    lines.append(sep_light)
    lines.append("")
    lines.append(f"Telegram             {'✅ Sent' if execution.get('telegram') else '❌ Not Sent'}")
    lines.append(f"Database             {'✅ Saved' if execution.get('database') else '❌ Not Saved'}")
    lines.append(f"Decision Stored      {'✅ Yes' if execution.get('stored') else '❌ No'}")
    lines.append(f"Latency              {execution.get('latency_ms', 0):.0f} ms")
    lines.append("")
    lines.append(sep_heavy)
    
    return "\n".join(lines)


def format_cycle_summary(
    pairs_analyzed: int,
    bullish_count: int,
    bearish_count: int,
    sideways_count: int,
    signals_found: int,
    approved_count: int,
    rejected_count: int,
    rejection_reasons: dict[str, int],
    avg_strategy_score: float,
    avg_confidence: float,
    avg_analysis_time: float,
    telegram_count: int,
    database_writes: int,
    warnings_count: int,
    errors_count: int,
    system_health: str,
) -> str:
    """Format the cycle summary report block."""
    
    lines = []
    sep_heavy = "══════════════════════════════════════════════════════════════════════════════"
    
    lines.append(sep_heavy)
    lines.append("📈 CYCLE SUMMARY")
    lines.append(sep_heavy)
    lines.append("")
    
    lines.append(f"Pairs Analyzed           : {pairs_analyzed}")
    lines.append(f"Bullish                  : {bullish_count}")
    lines.append(f"Bearish                  : {bearish_count}")
    lines.append(f"Sideways                 : {sideways_count}")
    lines.append("")
    lines.append(f"Signals Found            : {signals_found}")
    lines.append(f"Approved                 : {approved_count}")
    lines.append(f"Rejected                 : {rejected_count}")
    lines.append("")
    
    lines.append("Rejection Reasons")
    lines.append("-----------------")
    for reason, count in rejection_reasons.items():
        lines.append(f"{reason:<24} : {count}")
    lines.append("")
    
    lines.append(f"Average Strategy Score   : {avg_strategy_score:.0f}%")
    lines.append(f"Average Confidence       : {avg_confidence:.0f}%")
    lines.append(f"Average Analysis Time    : {avg_analysis_time:.0f} ms")
    lines.append("")
    lines.append(f"Telegram Messages        : {telegram_count}")
    lines.append(f"Database Writes          : {database_writes}")
    lines.append("")
    lines.append(f"Warnings                 : {warnings_count}")
    lines.append(f"Errors                   : {errors_count}")
    lines.append("")
    
    health_icon = "🟢" if system_health.upper() == "EXCELLENT" else "🟡" if system_health.upper() == "GOOD" else "🔴"
    lines.append(f"System Health            : {health_icon} {system_health.upper()}")
    lines.append(sep_heavy)
    
    return "\n".join(lines)
