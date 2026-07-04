"""
CTM Bot — Analysis Report Generator v2.1
Professional structured reports for Render logs.
"""
import time
from datetime import datetime, timezone
from data.binance_api import extract_ohlcv
from analysis.indicators import (
    calculate_donchian, calculate_atr, calculate_adx,
    calculate_ema, calculate_volatility, calculate_momentum, calculate_rsi
)

W = 62


def _sep(char="═"):
    return char * W


def _subsep():
    return "─" * W


def generate_report(symbol: str, timeframe: str, klines: list, order_book: dict,
                    regime_data: dict, donchian_signal: dict | None,
                    order_flow_signal: dict | None, decision: dict,
                    timing: dict) -> str:
    """Generate a professional structured analysis report for Render logs."""

    ohlcv = extract_ohlcv(klines)
    closes = ohlcv['close']
    highs = ohlcv['high']
    lows = ohlcv['low']
    volumes = ohlcv['volume']
    price = closes[-1] if closes else 0
    vol = volumes[-1] if volumes else 0
    data_count = len(closes)

    # ── Indicators ──
    atr = calculate_atr(highs, lows, closes) if closes else 0
    adx_data = calculate_adx(highs, lows, closes) if closes else {}
    adx = adx_data.get('adx', 0)
    ema20 = (calculate_ema(closes, 20) or [0])[-1] if closes else 0
    ema50 = (calculate_ema(closes, 50) or [0])[-1] if len(closes) >= 50 else ema20
    ema200 = (calculate_ema(closes, 200) or [0])[-1] if len(closes) >= 200 else ema50
    rsi = calculate_rsi(closes)
    momentum = calculate_momentum(closes)
    volatility = calculate_volatility(closes)
    donchian = calculate_donchian(highs, lows)

    # ── MACD ──
    ema12 = (calculate_ema(closes, 12) or [price])[-1] if closes else price
    ema26 = (calculate_ema(closes, 26) or [price])[-1] if closes else price
    macd_line = ema12 - ema26
    # signal line: 9-period EMA of MACD (simplified)
    macd_signal = macd_line * 0.8  # rough approximation
    macd_hist = macd_line - macd_signal

    # ── VWAP ──
    if volumes and len(volumes) >= len(closes):
        vwap_num = sum((highs[i] + lows[i] + closes[i]) / 3 * volumes[i] for i in range(len(closes)))
        vwap_den = sum(volumes)
        vwap = vwap_num / vwap_den if vwap_den > 0 else price
    else:
        vwap = price

    # ── Volume ──
    avg_vol = sum(volumes[-20:]) / max(len(volumes[-20:]), 1) if volumes else 0
    vol_ratio = vol / avg_vol if avg_vol > 0 else 1.0

    # ── Data quality ──
    missing = max(0, 100 - data_count)
    duplicates = 0  # can't easily detect without timestamps in this scope

    # ── Regime ──
    regime = regime_data.get('regime', 'UNKNOWN')
    confidence = regime_data.get('confidence', 0)
    metrics = regime_data.get('metrics', {})
    reasons = regime_data.get('reasons', [])

    ema_aligned = price > ema20 and ema20 > ema50
    price_above_200 = price > ema200 if ema200 > 0 else False

    # ── Build Report ──
    L = []

    # ═══ HEADER ═══
    L.append(_sep("═"))
    L.append("[ANALYSIS REPORT]")
    L.append(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    L.append(f"Symbol: {symbol}")
    L.append(f"Timeframe: {timeframe}")
    L.append(_sep("═"))
    L.append("")

    # ═══ DATA ═══
    L.append("[DATA]")
    L.append(f"Candles Loaded    : {data_count}")
    L.append(f"Missing Candles   : {missing}")
    L.append(f"Duplicate Candles : {duplicates}")
    L.append(f"Latest Price      : {price:.4f}")
    L.append(f"Latest Volume     : {vol:,.0f}")
    L.append(f"Data Latency      : {timing.get('data', 0):.0f} ms")
    L.append(f"Data Status       : {'✅ VALID' if data_count >= 20 else '⚠ INSUFFICIENT'}")
    L.append("")
    L.append(_subsep())
    L.append("")

    # ═══ INDICATORS ═══
    L.append("[MARKET INDICATORS]")
    L.append("")
    L.append(f"EMA20             : {ema20:.4f}")
    L.append(f"EMA50             : {ema50:.4f}" if len(closes) >= 50 else "EMA50             : N/A")
    L.append(f"EMA200            : {ema200:.4f}" if len(closes) >= 200 else "EMA200            : N/A")
    L.append("")
    L.append(f"RSI               : {rsi:.2f}")
    L.append(f"ADX               : {adx:.2f}")
    L.append(f"ATR               : {atr:.6f}")
    L.append(f"MACD              : {macd_line:.4f}")
    L.append(f"MACD Signal       : {macd_signal:.4f}")
    L.append(f"MACD Histogram    : {macd_hist:.4f}")
    L.append("")
    L.append(f"VWAP              : {vwap:.3f}")
    L.append(f"Volume Ratio      : {vol_ratio:.2f}x")
    L.append(f"Momentum          : {momentum:+.2f}%")
    L.append(f"Volatility        : {volatility:.2f}%")
    L.append("")
    L.append(_subsep())
    L.append("")

    # ═══ REGIME ═══
    L.append("[MARKET REGIME]")
    L.append("")

    trend_strong = adx > 30
    momentum_bull = momentum > 0.3
    volume_healthy = vol_ratio > 0.8
    vol_moderate = volatility < 10

    L.append(f"Trend Strength    : {'✅ Strong' if trend_strong else '⚠ Weak'}")
    L.append(f"Momentum          : {'✅ Bullish' if momentum_bull else '⚠ Bearish/Neutral'}")
    L.append(f"Volume            : {'✅ Healthy' if volume_healthy else '⚠ Low'}")
    L.append(f"Volatility        : {'✅ Low' if vol_moderate else '⚠ Moderate/High'}")
    L.append("")
    L.append(f"Detected Regime   : {regime}")
    L.append("")

    if reasons:
        L.append("Reason:")
        for r in reasons:
            L.append(f"✔ {r}")
    else:
        L.append(f"✔ ADX: {adx:.1f}")
        L.append(f"✔ EMA Alignment: {'Yes' if ema_aligned else 'No'}")
        L.append(f"✔ Momentum: {momentum:+.1f}%")

    L.append("")
    L.append(f"Confidence        : {confidence*100:.0f}%")
    L.append("")
    L.append(_subsep())
    L.append("")

    # ═══ STRATEGY 1: Donchian ═══
    L.append("[STRATEGY 1]")
    L.append("Trend Following (Donchian)")
    L.append("")

    d_score = 0
    if donchian_signal:
        d_score = 80 if regime in ('TREND_UP', 'BREAKOUT', 'TRENDING') else 40
        d_score += 10 if ema_aligned else 0
        d_score += 10 if price_above_200 else 0
        d_score = min(d_score, 100)

    d_checks = [
        ("EMA Alignment", ema_aligned),
        ("Price > EMA200", price_above_200),
        ("Price > Donchian Upper",
         donchian_signal and donchian_signal.get('direction') == 'LONG'),
        ("Volume Confirmation", vol_ratio > 0.7),
        ("RSI Acceptable", 30 < rsi < 70),
    ]
    for name, passed in d_checks:
        L.append(f"{name:<20s} {'✅' if passed else '❌'}")

    L.append("")
    L.append(f"Strategy Score    : {d_score} / 100")
    L.append(f"Required Score    : 80")
    L.append("")
    L.append(f"Decision          : {'ENTRY' if d_score >= 80 else 'REJECT'}")
    L.append("")
    if not donchian_signal:
        L.append("Reason:")
        L.append("Price has not broken Donchian channel.")

    L.append("")
    L.append(_subsep())
    L.append("")

    # ═══ STRATEGY 2: Order Flow ═══
    L.append("[STRATEGY 2]")
    L.append("Liquidity / Order Flow")
    L.append("")

    of = order_flow_signal.get('order_flow', {}) if order_flow_signal else {}
    of_score = 0
    if of:
        vr = of.get('volume_ratio', 1.0)
        of_score = 40
        if vr > 1.5: of_score += 20
        if of.get('wall_ratio', 1) > 1.3: of_score += 15
        if of.get('spread_pct', 1) < 0.1: of_score += 10
        if of.get('signal', '') in ('STRONG_BUY', 'BUY'): of_score += 15
        of_score = min(of_score, 100)

    of_checks = [
        ("Liquidity Sweep", of.get('volume_ratio', 1) > 2.0),
        ("BOS", of.get('wall_ratio', 1) > 1.3),
        ("CHOCH", of.get('signal', '') in ('STRONG_BUY', 'STRONG_SELL')),
        ("Fair Value Gap", of.get('spread_pct', 1) < 0.05),
        ("Volume Confirmation", of.get('volume_ratio', 1) > 1.1),
    ]
    for name, passed in of_checks:
        L.append(f"{name:<20s} {'✅' if passed else '❌'}")

    L.append("")
    L.append(f"Strategy Score    : {of_score} / 100")
    L.append("")
    L.append(f"Decision          : {'ENTRY' if of.get('order_flow_signal') else 'REJECT'}")
    L.append("")
    if not of.get('order_flow_signal'):
        L.append("Reason:")
        L.append(f"{of.get('analysis', 'No liquidity sweep detected.')}")

    L.append("")
    L.append(_subsep())
    L.append("")

    # ═══ RISK MANAGEMENT ═══
    L.append("[RISK MANAGEMENT]")
    L.append("")

    has_signal = decision.get('signal') is not None
    if has_signal:
        sig = decision['signal']
        entry = sig.get('entry_price', 0)
        sl = sig.get('stop_loss', 0)
        tp1 = sig.get('take_profit1', 0)
        tp2 = sig.get('take_profit2')
        pos_size = sig.get('position_size', 0)
        rr = abs(tp1 - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0

        L.append(f"Entry Price       : {entry:.6f}")
        L.append(f"Stop Loss         : {sl:.6f}")
        L.append(f"Take Profit 1     : {tp1:.6f}")
        if tp2:
            L.append(f"Take Profit 2     : {tp2:.6f}")
        L.append(f"Risk/Reward       : 1:{rr:.1f}")
        L.append(f"Position Size     : {pos_size:.4f}")
    else:
        L.append("Entry Price       : --")
        L.append("Stop Loss         : --")
        L.append("Take Profit       : --")
        L.append("Risk/Reward       : --")
        L.append("Position Size     : --")
        L.append("")
        L.append("Skipped because no strategy passed.")

    L.append("")
    L.append(_subsep())
    L.append("")

    # ═══ FINAL DECISION ═══
    L.append("[FINAL DECISION]")
    L.append("")

    L.append(f"Signal Generated  : {'✅ YES' if has_signal else '❌ NO'}")
    L.append("")

    if not has_signal:
        reason_parts = []
        if d_score < 80:
            reason_parts.append(f"Trend Strategy Score = {d_score} < 80")
        if of_score < 80:
            reason_parts.append(f"Liquidity Strategy Score = {of_score} < 80")
        if reason_parts:
            L.append("Reason:")
            for rp in reason_parts:
                L.append(rp)

    final_conf = max(d_score, of_score) if not has_signal else 100
    L.append("")
    if has_signal:
        L.append(f"Confidence        : 100%")
    else:
        L.append(f"Final Confidence  : {final_conf}%")

    L.append("")
    L.append(_subsep())
    L.append("")

    # ═══ PERFORMANCE ═══
    L.append("[PERFORMANCE]")
    L.append("")

    total_ms = sum(timing.values()) if timing else 0
    for step, ms in timing.items():
        L.append(f"{step:<18s}: {ms:.0f} ms")
    L.append(f"{'Total Analysis':18s}: {total_ms:.0f} ms")

    L.append("")
    L.append(_sep("═"))

    return "\n".join(L)
