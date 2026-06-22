"""
ثوابت النظام — غير قابلة للتغيير، لا تحتوي على أسرار.
لا توجد قيم افتراضية لرأس المال — يجب أن يحددها المستخدم لكل عملة.
"""
from core.types import RiskLevel

# ── هوية النظام ─────────────────────────────────────────────
SYSTEM_NAME = "CT V4.0 — منصة تداول احترافية"

# ── معاملات التداول ────────────────────────────────────────
TRADE_FEE: float = 0.001
MAX_RISK_PER_TRADE: float = 0.02

# ── حدود محرك الأدلة ───────────────────────────────────────
EVIDENCE_THRESHOLD: float = 75.0          # الحد الأدنى لقبول إشارة شراء
MIN_CONFLICTING_SIGNALS: int = 2           # عدد التعارضات لتقليل الثقة
HIGH_CONFIDENCE: float = 85.0

# ── حدود التعرض ──────────────────────────────────────────
MAX_POSITION_PER_SYMBOL_PCT: float = 0.10
MAX_TOTAL_EXPOSURE_PCT: float = 0.30

# ── حدود المخاطر اليومية والأسبوعية ──────────────────────
MAX_DAILY_LOSS_PCT: float = 0.03
MAX_WEEKLY_LOSS_PCT: float = 0.07
MAX_MONTHLY_LOSS_PCT: float = 0.15
MAX_CONSECUTIVE_LOSSES: int = 5
MAX_EXPOSURE_PCT: float = 0.30
MAX_DRAWDOWN_PCT: float = 0.20
MAX_CORRELATED_EXPOSURE_PCT: float = 0.20
DRAWDOWN_REDUCE_1: float = 0.05   # 5% → تقليل حجم المركز
DRAWDOWN_REDUCE_2: float = 0.10   # 10% → خفض التردد للنصف
DRAWDOWN_STOP: float = 0.15       # 15% → إيقاف الصفقات الخطرة

# ── الفواصل الزمنية ────────────────────────────────────────
HEARTBEAT_INTERVAL_SEC: int = 5
ANALYSIS_INTERVAL_SEC: int = 120    # دقيقتان بين التحليلات
RECONNECT_DELAY_SEC: int = 5
HTF_CACHE_DURATION_SEC: int = 1800  # 30 دقيقة

# ── تعديل التقلب ───────────────────────────────────────────
VOLATILITY_RISK_MAP: dict[str, float] = {
    "LOW": 1.0,
    "MEDIUM": 0.8,
    "HIGH": 0.5,
    "EXTREME": 0.0,
}

# ── الروابط الخارجية ───────────────────────────────────────
BINANCE_WS_URL: str = "wss://stream.binance.com:9443"
FNG_API_URL: str = "https://api.alternative.me/fng/?limit=1"

# ── أوزان الجلسات (لمحرك الأدلة) ───────────────────────────
SESSION_WEIGHTS: dict[str, float] = {
    "London": 0.08,
    "New York": 0.07,
    "Asia": 0.04,
    "Overlap": 0.09,
    "Weekend": 0.02,
}

# ── مستويات المخاطر ────────────────────────────────────────
RISK_LEVEL_ORDER = [
    RiskLevel.LOW,
    RiskLevel.MEDIUM,
    RiskLevel.HIGH,
    RiskLevel.EXTREME,
]

# ── المشرف ─────────────────────────────────────────────────
ADMIN_ID: int = 1503808643
