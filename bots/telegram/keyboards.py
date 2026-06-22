"""
Telegram Keyboards — UI layouts. No logic.
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton


def get_main_menu():
    keyboard = [
        [KeyboardButton("📈 الأسعار المباشرة")],
        [KeyboardButton("➕ إضافة عملة"), KeyboardButton("➖ حذف عملة")],
        [KeyboardButton("⚙️ تعديل العملة")],
        [KeyboardButton("📊 الإحصائيات"), KeyboardButton("📋 سجل الصفقات")],
        [KeyboardButton("🧠 توصيات النظام"), KeyboardButton("📡 حالة النظام")],
        [KeyboardButton("⏸ إيقاف التداول"), KeyboardButton("▶️ تشغيل التداول")],
        [KeyboardButton("🛑 إيقاف الطوارئ")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_capital_management_menu():
    keyboard = [
        [InlineKeyboardButton("💵 تعديل رأس المال الأساسي", callback_data="edit_base_capital")],
        [InlineKeyboardButton("🔙 رجوع للرئيسية", callback_data="main_menu")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_risk_management_menu():
    keyboard = [
        [
            InlineKeyboardButton("0.5%", callback_data="set_risk_0.5"),
            InlineKeyboardButton("1.0%", callback_data="set_risk_1.0"),
            InlineKeyboardButton("1.5%", callback_data="set_risk_1.5"),
        ],
        [InlineKeyboardButton("🔙 رجوع للرئيسية", callback_data="main_menu")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_timeframe_menu(selected_timeframes=None):
    """
    Multi-select timeframe menu with checkbox-style emulated UI.
    
    Args:
        selected_timeframes: set of selected timeframe strings (e.g. {'1m', '4h'})
    
    Returns:
        InlineKeyboardMarkup with toggle buttons and a done button.
    """
    if selected_timeframes is None:
        selected_timeframes = set()

    tfs = [
        ("1m", "1m"), ("5m", "5m"), ("15m", "15m"),
        ("1h", "1h"), ("4h", "4h"), ("1d", "1d"),
    ]
    buttons = []
    row = []
    for tf, display in tfs:
        checked = "✅" if tf in selected_timeframes else "☑️"
        row.append(InlineKeyboardButton(
            f"{checked} {display}",
            callback_data=f'tf_toggle_{tf}'
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []

    buttons.append([InlineKeyboardButton("✅ تم - حفظ", callback_data='tf_done')])
    buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")])
    return InlineKeyboardMarkup(buttons)
