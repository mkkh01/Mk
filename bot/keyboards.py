from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton

def get_main_menu():
    """لوحة التحكم الرئيسية (القائمة السفلية الثابتة)"""
    keyboard = [
        [KeyboardButton("📈 الأسعار المباشرة")],
        [KeyboardButton("➕ إضافة عملة"), KeyboardButton("➖ حذف عملة")],
        [KeyboardButton("⚙️ تعديل العملة"), KeyboardButton("💰 إدارة رأس المال")],
        [KeyboardButton("📊 الإحصائيات"), KeyboardButton("📋 سجل الصفقات")],
        [KeyboardButton("🧠 تقرير الذكاء الاصطناعي"), KeyboardButton("🎯 تقرير الأداء")],
        [KeyboardButton("⏸ إيقاف التداول"), KeyboardButton("▶️ تشغيل التداول")],
        [KeyboardButton("🛑 إيقاف الطوارئ")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_capital_management_menu():
    keyboard = [
        [InlineKeyboardButton("💵 تعديل رأس المال الأساسي", callback_data='edit_base_capital')],
        [InlineKeyboardButton("🔙 رجوع للرئيسية", callback_data='main_menu')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_risk_management_menu():
    keyboard = [
        [
            InlineKeyboardButton("0.5%", callback_data='set_risk_0.5'),
            InlineKeyboardButton("1.0%", callback_data='set_risk_1.0'),
            InlineKeyboardButton("1.5%", callback_data='set_risk_1.5')
        ],
        [InlineKeyboardButton("🔙 رجوع للرئيسية", callback_data='main_menu')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_timeframe_menu():
    keyboard = [
        [
            InlineKeyboardButton("5m", callback_data='tf_5m'),
            InlineKeyboardButton("15m", callback_data='tf_15m'),
            InlineKeyboardButton("1h", callback_data='tf_1h')
        ],
        [InlineKeyboardButton("4h", callback_data='tf_4h'), InlineKeyboardButton("1d", callback_data='tf_1d')],
        [InlineKeyboardButton("🔙 رجوع", callback_data='main_menu')]
    ]
    return InlineKeyboardMarkup(keyboard)
