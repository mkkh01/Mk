"""
CTM Bot - Analysis Report Generator
Generates detailed structured analysis reports for each coin/timeframe.
"""
from datetime import datetime
from data.binance_api import extract_ohlcv
from analysis.indicators import (
    calculate_donchian, calculate_atr, calculate_adx,
    calculate_ema, calculate_volatility, calculate_momentum, calculate_rsi
)


def generate_report(symbol: str, timeframe: str, klines: list, order_book: dict,
                    regime_data: dict, donchian_signal: dict | None,
                    order_flow_signal: dict | None, decision: dict,
                    timing: dict) -> str:
    """Generate a structured analysis report."""
    ohlcv = extract_ohlcv(klines)
    closes = ohlcv['close']
    highs = ohlcv['high']
    lows = ohlcv['low']
    volumes = ohlcv['volume']
    price = closes[-1] if closes else 0
    vol = volumes[-1] if volumes else 0
    data_count = len(closes)

    atr = calculate_atr(highs, lows, closes) if closes else 0
    adx_data = calculate_adx(highs, lows, closes) if closes else {}
    ema20 = (calculate_ema(closes, 20) or [0])[-1] if closes else 0
    ema50 = (calculate_ema(closes, 50) or [0])[-1] if len(closes) >= 50 else ema20
    ema200 = (calculate_ema(closes, 200) or [0])[-1] if len(closes) >= 200 else ema50
    rsi = calculate_rsi(closes)
    momentum = calculate_momentum(closes)
    volatility = calculate_volatility(closes)
    donchian = calculate_donchian(highs, lows)

    # Volume analysis
    avg_vol = sum(volumes[-20:]) / min(len(volumes[-20:]), 1) if volumes else 0
    vol_ratio = vol / avg_vol if avg_vol > 0 else 1.0

    regime = regime_data.get('regime', 'UNKNOWN')
    confidence = regime_data.get('confidence', 0)
    regime_metrics = regime_data.get('metrics', {})

    # EMA alignment
    ema_aligned = price > ema20 and ema20 > ema50
    price_above_200 = price > ema200 if ema200 > 0 else False

    lines = []
    W = 62
    lines.append("═" * W)
    lines.append(f"[ANALYSIS REPORT]")
    lines.append(f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    lines.append(f"Symbol: {symbol}")
    lines.append(f"Timeframe: {timeframe}")
    lines.append("═" * W)

    # DATA section
    lines.append("")
    lines.append("[DATA]")
    lines.append(f"Candles Loaded    : {data_count}")
    lines.append(f"Latest Price      : {price:.4f}")
    lines.append(f"Latest Volume     : {vol:,.0f}")
    lines.append(f"Avg Vol (20)      : {avg_vol:,.0f}")
    lines.append(f"Data Status       : ✅ VALID" if data_count >= 20 else "⚠ INSUFFICIENT")

    # INDICATORS
    lines.append("")
    lines.append("─" * W)
    lines.append("[MARKET INDICATORS]")
    lines.append(f"EMA20             : {ema20:.4f}")
    lines.append(f"EMA50             : {ema50:.4f}" if len(closes) >= 50 else f"EMA50 : N/A")
    lines.append(f"EMA200            : {ema200:.4f}" if len(closes) >= 200 else f"EMA200: N/A")
    lines.append(f"RSI               : {rsi:.2f}")
    lines.append(f"ADX               : {adx_data.get('adx', 0):.2f}")
    lines.append(f"ATR               : {atr:.4f}")
    lines.append(f"Donchian Upper    : {donchian['upper']:.4f}" if donchian else f"Donchian: N/A")
    lines.append(f"Donchian Lower    : {donchian['lower']:.4f}" if donchian else "")
    lines.append(f"Vol Ratio         : {vol_ratio:.2f}x")
    lines.append(f"Momentum          : {momentum:+.2f}%")
    lines.append(f"Volatility        : {volatility:.2f}%")

    # REGIME
    lines.append("")
    lines.append("─" * W)
    lines.append("[MARKET REGIME]")
    trend_icon = "✅" if regime in ('TREND_UP', 'BREAKOUT') else "⚠" if regime == 'TREND_DOWN' else "➖"
    lines.append(f"Detected Regime   : {trend_icon} {regime}")
    lines.append(f"Confidence        : {confidence*100:.0f}%")
    for reason in regime_data.get('reasons', []):
        lines.append(f"  • {reason}")

    # STRATEGY 1 - Donchian
    lines.append("")
    lines.append("─" * W)
    lines.append("[STRATEGY 1] Trend Following (Donchian)")
    d_score = 0
    if donchian_signal:
        d_score = 80 if regime in ('TREND_UP', 'BREAKOUT') else 40
        d_score += 10 if ema_aligned else 0
        d_score += 10 if price_above_200 else 0
        d_score = min(d_score, 100)
    checks = [
        ("EMA Alignment", ema_aligned),
        ("Price > EMA200", price_above_200),
        ("Price > Donchian Upper", donchian_signal and donchian_signal.get('direction') == 'LONG'),
        ("Volume Confirmation", vol_ratio > 0.7),
        ("RSI Acceptable", 30 < rsi < 70),
    ]
    for name, passed in checks:
        icon = "✅" if passed else "❌"
        lines.append(f"{name:20s} {icon}")
    lines.append(f"\nStrategy Score    : {d_score} / 100")
    lines.append(f"Required Score    : 80")
    lines.append(f"Decision          : {'ENTRY' if d_score >= 80 else 'REJECT'}")
    if not donchian_signal:
        lines.append("Reason: Price has not broken Donchian channel.")

    # STRATEGY 2 - Order Flow
    lines.append("")
    lines.append("─" * W)
    lines.append("[STRATEGY 2] Liquidity / Order Flow")
    of = order_flow_signal.get('order_flow', {}) if order_flow_signal else {}
    of_score = 0
    if of:
        vr = of.get('volume_ratio', 1.0)
        of_score = 40
        if vr > 1.5: of_score += 20
        if of.get('wall_ratio', 1) > 1.3: of_score += 15
        if of.get('spread_pct', 0) < 0.1: of_score += 10
        of_score = min(of_score, 100)
    of_checks = [
        ("Buy Volume > Sell", of.get('volume_ratio', 1) > 1.1),
        ("Order Wall Bullish", of.get('wall_ratio', 1) > 1.3),
        ("Spread Tight", of.get('spread_pct', 1) < 0.1),
        ("Signal Strong", of.get('signal', '') in ('STRONG_BUY', 'BUY')),
    ]
    for name, passed in of_checks:
        icon = "✅" if passed else "❌"
        lines.append(f"{name:20s} {icon}")
    lines.append(f"\nStrategy Score    : {of_score} / 100")
    lines.append(f"Decision          : {'ENTRY' if of.get('order_flow_signal') else 'REJECT'}")
    if not of.get('order_flow_signal'):
        lines.append(f"Reason: {of.get('analysis', 'No order flow signal')}")

    # FINAL DECISION
    lines.append("")
    lines.append("─" * W)
    lines.append("[FINAL DECISION]")
    has_signal = decision.get('signal') is not None
    lines.append(f"Signal Generated  : {'✅ YES' if has_signal else '❌ NO'}")
    if not has_signal:
        lines.append(f"Reason: {decision.get('reason', 'No strategy passed')}")
    final_conf = max(d_score, of_score) if not has_signal else 100
    lines.append(f"Final Confidence  : {final_conf}%")

    # PERFORMANCE
    lines.append("")
    lines.append("─" * W)
    lines.append("[PERFORMANCE]")
    total_ms = sum(timing.values()) if timing else 0
    for step, ms in timing.items():
        lines.append(f"{step:20s}: {ms:.0f} ms")
    lines.append(f"{'Total Analysis':20s}: {total_ms:.0f} ms")
    lines.append("═" * W)

    return "\n".join(lines)
