# -*- coding: utf-8 -*-
"""الإعدادات المركزية للنظام.

كل القيم تُقرأ من متغيرات البيئة (لوحة Render ← Environment)
مع قيم افتراضية مدروسة، فلا شيء حساس مكتوب داخل الكود.
"""
import os
import re


def _str(key: str, default: str = "") -> str:
    v = os.getenv(key)
    if v is None or v == "":
        return default
    return v.strip()


def _token(key: str) -> str:
    """للقيم الحساسة: يزيل كل المسافات والأسطر الجديدة (أخطاء النسخ/اللصق)."""
    return re.sub(r"\s+", "", os.getenv(key, "") or "")


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, default))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _list(key: str, default: str) -> list:
    raw = os.getenv(key, default) or ""
    return [x.strip() for x in raw.split(",") if x.strip()]


# =====================================================
#  أزواج التداول الثلاثون (بصيغة موحدة - تُترجم حسب المنصة)
#  تم اختيارها: سيولة عالية + متوفرة على OKX و Gate و KuCoin
# =====================================================
DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "TRXUSDT",
    "DOTUSDT", "POLUSDT", "ATOMUSDT", "NEARUSDT", "ARBUSDT",
    "OPUSDT", "SUIUSDT", "APTUSDT", "INJUSDT", "LTCUSDT",
    "BCHUSDT", "FILUSDT", "ETCUSDT", "HBARUSDT", "ICPUSDT",
    "RENDERUSDT", "PEPEUSDT", "WIFUSDT", "FETUSDT", "AAVEUSDT",
]


class Settings:
    # --- خدمات خارجية ---
    SUPABASE_URL: str = _str("SUPABASE_URL")
    SUPABASE_KEY: str = _str("SUPABASE_KEY")
    REDIS_URL: str = _str("REDIS_URL")

    TELEGRAM_BOT_TOKEN: str = _token("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_IDS: list = _list("TELEGRAM_CHAT_ID", "")
    WEBHOOK_BASE_URL: str = _str("WEBHOOK_BASE_URL").rstrip("/")
    WEBHOOK_SECRET: str = _str("WEBHOOK_SECRET")

    # --- الدورة ---
    CYCLE_SECONDS: int = _int("CYCLE_SECONDS", 60)
    MONITOR_SECONDS: int = _int("MONITOR_SECONDS", 5)  # المراقبة اللحظية للوقف/الهدف
    WS_ENABLE: int = _int("WS_ENABLE", 1)              # بث الأسعار اللحظي 1=يعمل
    PORT: int = _int("PORT", 10000)

    # --- الأزواج والفريمات ---
    SYMBOLS: list = _list("SYMBOLS", ",".join(DEFAULT_SYMBOLS))
    TREND_TF: str = _str("TREND_TF", "1h")     # فريم الترند (EMA200 الكبير)
    ENTRY_TF: str = _str("ENTRY_TF", "15m")    # فريم الدخول (EMA200 + ستوكاستيك + فيبو)
    KLINES_LIMIT: int = _int("KLINES_LIMIT", 260)

    # --- المؤشرات (من ملف الاستراتيجية) ---
    EMA_LEN: int = _int("EMA_LEN", 200)
    STOCH_K: int = _int("STOCH_K", 14)
    STOCH_SMOOTH: int = _int("STOCH_SMOOTH", 3)
    STOCH_D: int = _int("STOCH_D", 3)
    STOCH_OS: float = _float("STOCH_OS", 20)   # تشبع بيعي
    STOCH_OB: float = _float("STOCH_OB", 80)   # تشبع شرائي
    FIB_LOOKBACK: int = _int("FIB_LOOKBACK", 120)   # نافذة البحث عن القاع/القمة (شمعة)
    FIB_TOL_PCT: float = _float("FIB_TOL_PCT", 0.6) # سماحية منطقة الدخول %
    MIN_SWING_PCT: float = _float("MIN_SWING_PCT", 1.0)  # أقل حجم موجة مقبول %

    # --- المحفظة الورقية وإدارة المخاطر ---
    START_BALANCE: float = _float("START_BALANCE", 10000.0)
    RISK_PCT: float = _float("RISK_PCT", 1.0)   # مخاطرة % من المحفظة لكل صفقة
    LEVERAGE: int = _int("LEVERAGE", 1)  # 1 = بدون رافعة
    MAX_OPEN_TRADES: int = _int("MAX_OPEN_TRADES", 6)
    MAX_PER_SYMBOL: int = _int("MAX_PER_SYMBOL", 1)
    RR_MIN: float = _float("RR_MIN", 1.3)       # أقل نسبة عائد/مخاطرة (من 1:1.3 فأعلى)
    FEE_PCT: float = _float("FEE_PCT", 0.1)     # رسوم التداول لكل جهة % (سبوت 0.1)
    MAX_HOLD_HOURS: float = _float("MAX_HOLD_HOURS", 48)

    # --- مصادر البيانات بالترتيب (تبديل تلقائي عند الفشل) ---
    DATA_SOURCES: list = _list("DATA_SOURCES", "binance,okx,gate,kucoin")


settings = Settings()
