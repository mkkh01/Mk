import yfinance as yf
import requests
import json
import pandas as pd
from datetime import datetime, timedelta

class MacroAnalyzer:
    def __init__(self):
        self.cache = {}
        self.cache_duration = timedelta(hours=1) # تحديث البيانات كل ساعة لمنع حظر الـ API

    def _is_cache_valid(self, key):
        if key in self.cache:
            timestamp, _ = self.cache[key]
            return datetime.now() - timestamp < self.cache_duration
        return False

    def get_dxy(self) -> float:
        """جلب مؤشر الدولار الأمريكي (DXY) بأمان ديناميكي من Yahoo Finance"""
        key = "DXY"
        if self._is_cache_valid(key):
            return self.cache[key][1]
        
        try:
            # جلب المؤشر الفعلي عبر الرمز الشائع في ياهو فاينانس
            dxy_data = yf.Ticker("DX-Y.NYB").history(period="5d")
            if not dxy_data.empty: 
                dxy_value = float(dxy_data["Close"].iloc[-1])
                print(f"📈 [MACRO] تم جلب قيمة حية لمؤشر الدولار DXY: {dxy_value:.2f}")
            else:
                dxy_value = 103.0 # قيمة افتراضية مرنة في حال غياب البيانات
            
            self.cache[key] = (datetime.now(), dxy_value)
            return dxy_value
        except Exception as e:
            print(f"⚠️ [MACRO] خطأ في جلب DXY الفعلي: {e}. استخدام قيمة احتياطية.")
            return 103.0 

    def get_market_regime(self) -> str:
        """
        تحديد وضع السوق بشكل ديناميكي (Dynamic Market Regime) بدون أرقام ثابتة
        يعتمد على مقارنة ناسداك بمتوسطه وبنية مؤشر الخوف والجشع
        """
        try:
            dxy = self.get_dxy()
            fng = self.get_fear_and_greed()
            
            # جلب بيانات ناسداك لفترة 30 يوماً للحساب الديناميكي بدلاً من الرقم الثابت القديم
            ndx_ticker = yf.Ticker("^NDX")
            ndx_hist = ndx_ticker.history(period="30d")
            
            if ndx_hist.empty or len(ndx_hist) < 20:
                print("⚠️ [MACRO] بيانات ناسداك غير كافية للحساب الديناميكي، العودة للوضع المحايد.")
                return "NEUTRAL"
                
            current_ndx = ndx_hist["Close"].iloc[-1]
            # حساب المتوسط المتحرك البسيط لـ 20 يوماً كخط أساسي ديناميكي لاتجاه السوق الحالي
            ndx_sma20 = ndx_hist["Close"].rolling(window=20).mean().iloc[-1]
            
            print(f"📊 [MACRO ANALYSIS] NDX الحالي: {current_ndx:.2f} | المتوسط المتحرك الديناميكي (SMA20): {ndx_sma20:.2f} | الخوف والجشع: {fng}")

            # ⚖️ منطق ديناميكي متطور ومتوافق مع تغير السنين والمستويات السعرية:
            # RISK_OFF: ناسداك تحت متوسطه، والخوف مسيطر (أقل من 35)
            if current_ndx < ndx_sma20 and fng < 35:
                return "RISK_OFF"
                
            # RISK_ON: ناسداك فوق متوسطه، والجشع مسيطر (فوق 65)
            elif current_ndx > ndx_sma20 and fng > 65:
                return "RISK_ON"
                
            else:
                return "NEUTRAL"
                
        except Exception as main_macro_err:
            print(f"🚨 [MACRO ERROR] فشل تحليل وضع السوق بالكامل: {main_macro_err}")
            return "NEUTRAL"

    def get_ndx(self) -> float:
        """جلب مؤشر ناسداك 100 (NDX) اللحظي"""
        key = "NDX"
        if self._is_cache_valid(key):
            return self.cache[key][1]

        try:
            ndx_data = yf.Ticker("^NDX").history(period="1d")
            if not ndx_data.empty: 
                ndx_value = float(ndx_data["Close"].iloc[-1])
                self.cache[key] = (datetime.now(), ndx_value)
                return ndx_value
            else:
                return 18000.0 # تحديث القيمة الافتراضية لتناسب مستويات العصر الحالي
        except Exception as e:
            print(f"خطأ في جلب NDX: {e}")
            return 18000.0

    def get_fear_and_greed(self) -> int:
        """جلب مؤشر الخوف والجشع (Fear & Greed Index) من الـ API الرسمي"""
        key = "FNG"
        if self._is_cache_valid(key):
            return self.cache[key][1]

        try:
            url = "https://api.alternative.me/fng/?limit=1"
            response = requests.get(url, timeout=10)
            data = response.json()
            fng_value = int(data["data"][0]["value"])
            self.cache[key] = (datetime.now(), fng_value)
            return fng_value
        except Exception as e:
            print(f"خطأ في جلب مؤشر الخوف والجشع: {e}")
            return 50 # محايد عند حدوث خطأ في الـ API
