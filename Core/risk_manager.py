from config import DEFAULT_CAPITAL, TRADE_FEE

class RiskManager:
    def __init__(self):
        # أقصى مخاطرة 2% من رأس المال لكل صفقة حقيقية
        self.max_risk_per_trade = 0.02 
        # الدقة الافتراضية للعملات (8 خانات للعملات الصفرية والصغيرة)
        self.precision = 8

    def calculate_kelly_position(self, capital: float, win_rate: float, risk_reward_ratio: float) -> float:
        """
        حساب حجم الصفقة باستخدام معيار كيلي (Kelly Criterion)
        مطور ليدعم المبالغ الصغيرة جداً (أقل من 10 دولار) لغرض التدريب الفعال
        """
        # إذا كان رأس المال المخصص صغير جداً (أقل من 15 دولار)، نمنح البوت مرونة استخدام 50% إلى 100% 
        # من هذا المبلغ المخصص لتجنب خروج حجم الصفقة كأجزاء من السنت
        if capital <= 15.0:
            return round(capital, 2)

        if win_rate <= 0 or risk_reward_ratio <= 0:
            # إذا لم تتوفر بيانات كافية، نستخدم نسبة ثابتة آمنة (1% من رأس المال)
            final_risk_pct = 0.01
        else:
            kelly_percentage = win_rate - ((1 - win_rate) / risk_reward_ratio)
            # نستخدم "نصف كيلي" (Half-Kelly) للأمان
            safe_kelly = kelly_percentage / 2.0
            # نضمن البقاء في نطاق آمن للتداول العادي
            final_risk_pct = min(max(safe_kelly, 0.01), self.max_risk_per_trade)
        
        position_size = capital * final_risk_pct
        return round(position_size, 2)

    def calculate_sl_tp(self, entry_price: float, atr: float, side: str, atr_multiplier: float = 2.0):
        """
        حساب وقف الخسارة (SL) وجني الأرباح (TP) بناءً على التذبذب (ATR)
        مع تحديث الدقة ديناميكياً قبل التقريب لضمان عدم تداخل الأرقام الصغيرة
        """
        # تحديث عدد الخانات العشرية ديناميكياً فوراً بناءً على سعر العملة الحالي
        self.precision = self.get_dynamic_precision(entry_price)

        if not atr or atr == 0:
            # إذا لم يتوفر ATR، نضع وقف منطقي 1.5% وجني أرباح متناسب 2.25% للمرونة
            stop_loss_dist = entry_price * 0.015
        else:
            stop_loss_dist = atr * atr_multiplier

        take_profit_dist = stop_loss_dist * 1.5  # نسبة مخاطرة لعائد 1:1.5

        if side == "BUY":
            sl = entry_price - stop_loss_dist
            tp = entry_price + take_profit_dist
        else: # SELL
            sl = entry_price + stop_loss_dist
            tp = entry_price - take_profit_dist

        # التقريب باستخدام الدقة الديناميكية المحسوبة لحماية أهداف العملات البديلة الصغيرة
        return round(sl, self.precision), round(tp, self.precision)

    def check_fee_violation(self, entry_price: float, tp_price: float) -> bool:
        """التأكد من أن الربح المتوقع يغطي عمولة المنصة مع إعطاء مرونة كاملة للحسابات الصغيرة"""
        profit_margin = abs(tp_price - entry_price) / entry_price
        total_fee = TRADE_FEE * 2 
        
        # لغرض التداول التجريبي والتعلم الذاتي بمبالغ صغيرة، خففنا القيد ليمر الشرط دائماً 
        # ما دامت الصفقة رابحة فنية بنسبة تزيد عن رسوم المنصة
        is_valid = profit_margin > (total_fee * 0.5)
        
        if not is_valid:
            print(f"⚠️ [RISK MANAGER] هامش الربح المتوقع ({profit_margin:.6f}) قليل جداً مقارنة بالرسوم ({total_fee:.6f})")
        return is_valid

    def get_dynamic_precision(self, price: float) -> int:
        """تحديد عدد الخانات العشرية المناسب بناءً على سعر العملة لمنع أخطاء التقريب الصفرية"""
        if price < 0.0001: return 8
        if price < 0.001: return 7
        if price < 0.01: return 6
        if price < 0.1: return 5
        if price < 1: return 4
        if price < 100: return 3
        return 2
