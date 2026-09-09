# -*- coding: utf-8 -*-
"""الإعدادات المركزية - V1 حسب مواصفة CT (الفريمات الصغيرة).

الأدوار:
  HTF = السياق (الاتجاه)      → 1h
  MTF = الإعداد (Swing/Fib)   → 15m
  LTF = الإشعال (توقيت+تأكيد) → 5m
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


def _bool(key: str, default: bool) -> bool:
    v = (os.getenv(key) or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def _list(key: str, default: str) -> list:
    raw = os.getenv(key, default) or ""
    return [x.strip() for x in raw.split(",") if x.strip()]


# أزواج التداول الثلاثون (سبوت USDT)
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

    # --- الدورة واللحظي ---
    CYCLE_SECONDS: int = _int("CYCLE_SECONDS", 60)
    MONITOR_SECONDS: int = _int("MONITOR_SECONDS", 5)
    WS_ENABLE: int = _int("WS_ENABLE", 1)
    PORT: int = _int("PORT", 10000)

    # --- السوق والرموز ---
    SYMBOLS: list = _list("SYMBOLS", ",".join(DEFAULT_SYMBOLS))
    ALLOW_SHORTS: bool = _bool("ALLOW_SHORTS", True)  # المواصفة V1 سبوت شراء فقط؛ يُفتح البيع بهذا المفتاح

    # --- الفريمات الثلاثة (§7 بديل الفريمات الصغيرة) ---
    HTF: str = _str("HTF", "1h")    # السياق: اتجاه EMA200
    MTF: str = _str("MTF", "15m")   # الإعداد: Swing + Fibonacci
    LTF: str = _str("LTF", "5m")    # الإشعال: Stochastic + تأكيد سعري
    KLINES_LIMIT: int = _int("KLINES_LIMIT", 260)

    # --- EMA (§8) ---
    EMA_LEN: int = _int("EMA_LEN", 200)
    SLOPE_LOOKBACK: int = _int("SLOPE_LOOKBACK", 10)
    SLOPE_STRONG: float = _float("SLOPE_STRONG", 0.002)
    SLOPE_WEAK: float = _float("SLOPE_WEAK", 0.0005)

    # --- Stochastic على LTF (§13) ---
    STOCH_K: int = _int("STOCH_K", 14)
    STOCH_SMOOTH: int = _int("STOCH_SMOOTH", 3)
    STOCH_D: int = _int("STOCH_D", 3)
    STOCH_OS: float = _float("STOCH_OS", 20)
    STOCH_OB: float = _float("STOCH_OB", 80)
    CROSS_LOOKBACK: int = _int("CROSS_LOOKBACK", 2)  # أقصى قدم للتقاطع (شموع LTF)

    # --- Pivot/Swing/Fibonacci (§9-12) ---
    PIVOT_K: int = _int("PIVOT_K", 3)          # شموع التأكيد يمين/يسار
    FIB_LOOKBACK: int = _int("FIB_LOOKBACK", 120)
    FIB_TOL_PCT: float = _float("FIB_TOL_PCT", 0.4)
    MIN_SWING_PCT: float = _float("MIN_SWING_PCT", 1.0)
    LEVEL_TOL_PCT: float = _float("LEVEL_TOL_PCT", 0.3)  # سماحية المستوى الأفقي

    # --- القرار (§17-19) ---
    MIN_SCORE: int = _int("MIN_SCORE", 70)
    WATCH_SCORE: int = _int("WATCH_SCORE", 60)

    # --- التقلب (§35) ---
    VOL_MIN: float = _float("VOL_MIN", 0.0005)  # ATR/Close الأدنى
    VOL_MAX: float = _float("VOL_MAX", 0.05)    # ATR/Close الأقصى

    # --- المخاطر (§21-25) ---
    START_BALANCE: float = _float("START_BALANCE", 10000.0)
    RISK_PCT: float = _float("RISK_PCT", 1.0)
    LEVERAGE: int = _int("LEVERAGE", 1)  # 1 = بدون رافعة
    MAX_OPEN_TRADES: int = _int("MAX_OPEN_TRADES", 3)       # §25
    MAX_PORTFOLIO_RISK: float = _float("MAX_PORTFOLIO_RISK", 3.0)  # % §25
    MAX_PER_SYMBOL: int = _int("MAX_PER_SYMBOL", 1)
    RR_MIN: float = _float("RR_MIN", 1.3)       # من 1:1.3 فأعلى (ضمن قيم البحث §23)
    ATR_SL_MULT: float = _float("ATR_SL_MULT", 0.2)  # §21
    FEE_PCT: float = _float("FEE_PCT", 0.1)
    SLIPPAGE_BPS: float = _float("SLIPPAGE_BPS", 5)  # §29
    MAX_HOLD_HOURS: float = _float("MAX_HOLD_HOURS", 48)
    COOLDOWN_MINUTES: int = _int("COOLDOWN_MINUTES", 30)  # §32
    MIN_NOTIONAL: float = _float("MIN_NOTIONAL", 5.0)
    MAX_NOTIONAL_PCT: float = _float("MAX_NOTIONAL_PCT", 30.0)  # سقف قيمة الصفقة %

    # --- مصادر البيانات ---
    DATA_SOURCES: list = _list("DATA_SOURCES", "binance,okx,gate,kucoin")


settings = Settings()
