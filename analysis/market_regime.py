"""
Market Regime Classification
Classifies market into 8 states for strategy selection.
"""
from config import MarketRegime, ADX_THRESHOLD, VOLATILITY_THRESHOLD

def classify_regime(klines: list, order_book: dict = None) -> dict:
    """
    Analyze market data and classify into one of 8 regimes.
    
    Args:
        klines: List of raw Binance kline data
        order_book: Optional order book data for liquidity analysis
    
    Returns:
        dict with regime, confidence, and all computed metrics
    """
    from analysis.indicators import (
        calculate_adx, calculate_volatility, calculate_momentum,
        calculate_slope, calculate_rsi, calculate_ema
    )
    from data.binance_api import extract_ohlcv
    
    ohlcv = extract_ohlcv(klines)
    closes = ohlcv['close']
    highs = ohlcv['high']
    lows = ohlcv['low']
    volumes = ohlcv['volume']
    
    current_price = closes[-1]
    
    # === Compute metrics ===
    adx_data = calculate_adx(highs, lows, closes)
    volatility = calculate_volatility(closes)
    momentum = calculate_momentum(closes)
    slope = calculate_slope(closes, 5)
    rsi = calculate_rsi(closes)
    
    # Volume trend
    vol_ma_short = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 0
    vol_ma_long = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else vol_ma_short
    volume_trend = (vol_ma_short / vol_ma_long - 1) * 100 if vol_ma_long > 0 else 0
    
    # EMA analysis
    ema20 = calculate_ema(closes, 20)
    ema50 = calculate_ema(closes, 50) if len(closes) >= 50 else ema20
    ema200 = calculate_ema(closes, 200) if len(closes) >= 200 else ema50
    
    ema20_current = ema20[-1] if ema20 else current_price
    ema50_current = ema50[-1] if ema50 else current_price
    ema200_current = ema200[-1] if ema200 else current_price
    
    price_above_ema20 = current_price > ema20_current
    price_above_ema50 = current_price > ema50_current
    ema20_above_ema50 = ema20_current > ema50_current
    
    # === Classify regime ===
    regime = None
    regime_confidence = 0.5
    reasons = []
    
    adx = adx_data['adx']
    plus_di = adx_data['plus_di']
    minus_di = adx_data['minus_di']
    
    # 1. Capitulation check (extreme conditions)
    if volatility > 5.0 and momentum < -10 and rsi < 25:
        regime = MarketRegime.CAPITULATION
        regime_confidence = 0.85
        reasons.append(f"Extreme volatility ({volatility:.1f}%) with strong negative momentum ({momentum:.1f}%) and RSI oversold ({rsi:.1f})")
    
    # 2. TREND_UP: Strong uptrend
    elif adx > ADX_THRESHOLD and plus_di > minus_di and slope > 0.3 and price_above_ema20:
        regime = MarketRegime.TREND_UP
        if slope > 1.0:
            regime_confidence = 0.9
            reasons.append(f"Strong uptrend: ADX={adx:.1f}, slope={slope:.2f}%, price above EMAs")
        elif slope > 0.5:
            regime_confidence = 0.75
            reasons.append(f"Moderate uptrend: ADX={adx:.1f}, slope={slope:.2f}%")
        else:
            regime_confidence = 0.6
            reasons.append(f"Weak uptrend: ADX={adx:.1f}, slope={slope:.2f}%")
    
    # 3. TREND_DOWN: Strong downtrend
    elif adx > ADX_THRESHOLD and minus_di > plus_di and slope < -0.3:
        regime = MarketRegime.TREND_DOWN
        if slope < -1.0:
            regime_confidence = 0.9
            reasons.append(f"Strong downtrend: ADX={adx:.1f}, slope={slope:.2f}%")
        else:
            regime_confidence = 0.7
            reasons.append(f"Downtrend: ADX={adx:.1f}, slope={slope:.2f}%")
    
    # 4. BREAKOUT: Price breaking out of range with volume
    elif volatility > VOLATILITY_THRESHOLD and abs(momentum) > 3 and volume_trend > 20:
        if momentum > 0:
            regime = MarketRegime.BREAKOUT
            regime_confidence = 0.7
            reasons.append(f"Breakout with volume surge: momentum={momentum:.1f}%, vol_trend={volume_trend:.1f}%")
    
    # 5. HIGH_VOLATILITY: High volatility without clear direction
    elif volatility > VOLATILITY_THRESHOLD and adx < ADX_THRESHOLD:
        regime = MarketRegime.HIGH_VOLATILITY
        regime_confidence = 0.65
        reasons.append(f"High volatility ({volatility:.1f}%) without trend direction (ADX={adx:.1f})")
    
    # 6. RANGE: Low ADX, stable price
    elif adx < 20 and volatility < 1.5:
        regime = MarketRegime.RANGE
        regime_confidence = 0.7
        reasons.append(f"Range-bound: ADX={adx:.1f}, low volatility ({volatility:.1f}%)")
    
    # 7. DISTRIBUTION: Bearish divergence (price up, volume down)
    elif momentum > 0 and volume_trend < -10:
        regime = MarketRegime.DISTRIBUTION
        regime_confidence = 0.6
        reasons.append(f"Distribution: price rising but volume declining ({volume_trend:.1f}%)")
    
    # 8. LOW_VOLATILITY: Default stable state
    else:
        regime = MarketRegime.LOW_VOLATILITY
        regime_confidence = 0.5
        reasons.append(f"Low volatility stable state (vol={volatility:.1f}%)")
    
    metrics = {
        'adx': adx, 'plus_di': plus_di, 'minus_di': minus_di,
        'volatility': round(volatility, 2), 'momentum': round(momentum, 2),
        'rsi': round(rsi, 1), 'slope': round(slope, 3),
        'volume_trend': round(volume_trend, 2),
        'price': current_price
    }
    
    return {
        'regime': regime,
        'confidence': regime_confidence,
        'reasons': reasons,
        'metrics': metrics
    }
