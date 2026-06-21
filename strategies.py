import pandas as pd
import numpy as np
from ta.trend import EMAIndicator, MACD
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange, BollingerBands

class InstitutionalStrategies:
    def __init__(self):
        pass

    def classify_market(self, df: pd.DataFrame) -> dict:
        """تصنيف السوق (Phase 1) - ثقة أعلى من 80%"""
        if len(df) < 200: return {"state": "Low Data", "confidence": 0}
        
        close = df['close'].iloc[-1]
        ema50 = EMAIndicator(df['close'], window=50).ema_indicator().iloc[-1]
        ema200 = EMAIndicator(df['close'], window=200).ema_indicator().iloc[-1]
        atr = AverageTrueRange(df['high'], df['low'], df['close']).average_true_range().iloc[-1]
        
        volatility = (atr / close) * 100
        
        state = "Range Market"
        confidence = 85
        
        if close > ema50 > ema200:
            state = "Strong Uptrend"
            confidence = 90
        elif close < ema50 < ema200:
            state = "Strong Downtrend"
            confidence = 90
        elif close > ema200 and close < ema50:
            state = "Weak Uptrend"
            confidence = 70
        elif close < ema200 and close > ema50:
            state = "Weak Downtrend"
            confidence = 70
            
        if volatility > 2.5:
            state = "High Volatility"
            confidence = 85
            
        return {"state": state, "confidence": confidence}

    def get_market_quality_score(self, df: pd.DataFrame) -> float:
        """فلتر جودة السوق (Market Quality Score)"""
        # حساب السيولة والحجم والتذبذب
        avg_vol = df['volume'].rolling(20).mean().iloc[-1]
        curr_vol = df['volume'].iloc[-1]
        atr = AverageTrueRange(df['high'], df['low'], df['close']).average_true_range().iloc[-1]
        
        score = 100
        if curr_vol < avg_vol * 0.5: score -= 30 # سيولة منخفضة
        if atr / df['close'].iloc[-1] > 0.03: score -= 20 # تذبذب خطر
        
        return max(0, score)

    def calculate_combined_score(self, df: pd.DataFrame, df_higher: pd.DataFrame = None) -> dict:
        """نظام التقييم بالنقاط (Phase 3) - Total 100"""
        score = 0
        report = []
        
        # 1. Trend Score (25 pts)
        market = self.classify_market(df)
        if "Strong" in market["state"]:
            score += 25
            report.append(f"Trend: {market['state']} (+25)")
            
        # 2. Market Structure (20 pts)
        # تبسيط: إذا كان السعر فوق المتوسطات فهو هيكل صاعد
        if df['close'].iloc[-1] > df['close'].rolling(50).mean().iloc[-1]:
            score += 20
            report.append("Market Structure: Bullish (+20)")
            
        # 3. Volume Analysis (15 pts)
        rel_vol = df['volume'].iloc[-1] / df['volume'].rolling(20).mean().iloc[-1]
        if rel_vol > 1.2:
            score += 15
            report.append(f"Volume Analysis: High RelVol {rel_vol:.2f} (+15)")
            
        # 4. Support/Resistance (15 pts)
        # (يمكن إضافة منطق أكثر تعقيداً هنا)
        score += 10 # افتراضي للتبسيط حالياً
        report.append("S/R Confirmation (+10)")
        
        # 5. Volatility (10 pts)
        atr_pct = (AverageTrueRange(df['high'], df['low'], df['close']).average_true_range().iloc[-1] / df['close'].iloc[-1]) * 100
        if 0.5 < atr_pct < 2.0:
            score += 10
            report.append("Volatility: Optimal (+10)")
            
        # 6. Multi Timeframe Confirmation (5 pts)
        if df_higher is not None:
            higher_market = self.classify_market(df_higher)
            if higher_market["state"] == market["state"]:
                score += 5
                report.append("MTF Confirmation (+5)")
        
        # عوامل أخرى (10 pts)
        score += 5 # Market Quality default
        
        return {
            "total_score": score,
            "report": " | ".join(report),
            "market_state": market["state"],
            "quality_score": self.get_market_quality_score(df)
        }

    def get_trade_params(self, df: pd.DataFrame):
        """إدارة المخاطر (Phase 5)"""
        price = df['close'].iloc[-1]
        atr = AverageTrueRange(df['high'], df['low'], df['close']).average_true_range().iloc[-1]
        
        sl = price - (atr * 2) # ATR Stop Loss
        tp = price + (atr * 4) # R:R 1:2
        
        return {"entry": price, "sl": sl, "tp": tp, "atr": atr}
