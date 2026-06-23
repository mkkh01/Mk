"""
Reason Codes — أكواد موحدة لكل قرار رفض/قبول في النظام.
لا نصوص عشوائية. كل كود = سبب محدد + الطبقة المسؤولة.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Any


class ReasonCode(str, Enum):
    """أكواد موحدة — تستخدم في كل Log رفض/قبول."""

    # ── بيانات السوق (Data Layer) ──
    RC001_NO_CANDLES = "RC001_NO_CANDLES"               # لا توجد شموع كافية
    RC002_NOT_ENOUGH_VOLUME = "RC002_NOT_ENOUGH_VOLUME" # حجم تداول غير كافٍ
    RC003_LOW_CONFIDENCE = "RC003_LOW_CONFIDENCE"       # ثقة منخفضة
    RC006_MARKET_SIDEWAYS = "RC006_MARKET_SIDEWAYS"     # سوق جانبي
    RC007_LOW_LIQUIDITY = "RC007_LOW_LIQUIDITY"         # سيولة منخفضة
    RC008_SPREAD_TOO_HIGH = "RC008_SPREAD_TOO_HIGH"     # سبريد مرتفع

    # ── الاستراتيجيات (Strategy Layer) ──
    RC009_STRATEGY_FAILED = "RC009_STRATEGY_FAILED"     # فشل تقييم استراتيجية
    RC010_NO_STRATEGY_MATCH = "RC010_NO_STRATEGY_MATCH" # لا استراتيجية مناسبة
    RC016_REGIME_MISMATCH = "RC016_REGIME_MISMATCH"     # نظام السوق غير مناسب
    RC017_CONTRACT_FAILED = "RC017_CONTRACT_FAILED"     # فشل عقد الاستراتيجية
    RC018_WRONG_TIMEFRAME = "RC018_WRONG_TIMEFRAME"     # إطار زمني غير مدعوم

    # ── محرك الدليل (Evidence Engine) ──
    RC011_EVIDENCE_FAILED = "RC011_EVIDENCE_FAILED"     # فشل محرك الدليل
    RC019_NO_DIRECTION = "RC019_NO_DIRECTION"           # لا اتجاه واضح
    RC020_CONTRADICTORY_SIGNALS = "RC020_CONTRADICTORY_SIGNALS"  # إشارات متناقضة
    RC021_NO_CONSENSUS = "RC021_NO_CONSENSUS"           # لا إجماع بين الاستراتيجيات

    # ── محرك المخاطر (Risk Engine) ──
    RC004_COOLDOWN = "RC004_COOLDOWN"                   # فترة تهدئة
    RC005_RISK_LIMIT = "RC005_RISK_LIMIT"              # حد المخاطرة
    RC012_RISK_ENGINE_BLOCKED = "RC012_RISK_ENGINE_BLOCKED"  # محرك المخاطر أوقف
    RC015_MAX_DRAWDOWN = "RC015_MAX_DRAWDOWN"          # أقصى انخفاض
    RC022_EXPOSURE_LIMIT = "RC022_EXPOSURE_LIMIT"       # حد التعرض
    RC023_DAILY_LOSS_LIMIT = "RC023_DAILY_LOSS_LIMIT"   # حد الخسارة اليومية
    RC024_CAPITAL_INSUFFICIENT = "RC024_CAPITAL_INSUFFICIENT"  # رأس مال غير كافٍ
    RC025_POSITION_EXISTS = "RC025_POSITION_EXISTS"     # مركز مفتوح مسبقاً

    # ── التنفيذ (Execution Layer) ──
    RC013_EXECUTION_BLOCKED = "RC013_EXECUTION_BLOCKED" # تنفيذ ممنوع
    RC026_INVALID_ORDER = "RC026_INVALID_ORDER"         # أمر غير صالح
    RC027_PRICE_OUT_OF_RANGE = "RC027_PRICE_OUT_OF_RANGE"  # سعر خارج النطاق

    # ── النظام (System Layer) ──
    RC014_SYSTEM_NOT_READY = "RC014_SYSTEM_NOT_READY"   # نظام غير جاهز
    RC028_WS_DISCONNECTED = "RC028_WS_DISCONNECTED"     # WebSocket منفصل
    RC029_KILLSWITCH_ACTIVE = "RC029_KILLSWITCH_ACTIVE" # مفتاح الطوارئ مفعّل
    RC030_MARKET_DATA_STALE = "RC030_MARKET_DATA_STALE" # بيانات سوق قديمة

    # ── Arbiter ──
    RC031_ARBITER_REJECTED = "RC031_ARBITER_REJECTED"   # Arbiter رفض
    RC032_TREND_CONTRADICTION = "RC032_TREND_CONTRADICTION"  # تناقض مع الاتجاه

    # ── إيجابي ──
    RC000_PASS = "RC000_PASS"                           # نجح


# ═══════════════════════════════════════════════════════════════
# كائن الرفض الموحد
# ═══════════════════════════════════════════════════════════════

@dataclass
class Rejection:
    """كائن رفض موحد — يحتوي كل معلومات الرفض."""
    engine: str                    # اسم المحرك: "Evidence" / "Risk" / "Execution" ...
    rule: str                      # اسم القاعدة: "Minimum Confidence" / "Cooldown" ...
    reason: str                    # نص وصفي
    code: ReasonCode               # كود موحد
    current_value: Any = None      # القيمة الحالية
    required_value: Any = None     # القيمة المطلوبة
    details: str = ""              # تفاصيل إضافية

    def __str__(self) -> str:
        parts = [
            f"[{self.code.value}] {self.reason}",
            f"Engine: {self.engine}",
            f"Rule: {self.rule}",
        ]
        if self.current_value is not None:
            parts.append(f"Current: {self.current_value}")
        if self.required_value is not None:
            parts.append(f"Required: {self.required_value}")
        if self.details:
            parts.append(f"Details: {self.details}")
        return " | ".join(parts)

    def __bool__(self) -> bool:
        return self.code != ReasonCode.RC000_PASS


# ═══════════════════════════════════════════════════════════════
# واصفات الأكواد
# ═══════════════════════════════════════════════════════════════

CODE_DESCRIPTIONS: dict[ReasonCode, str] = {
    ReasonCode.RC000_PASS: "تم الاجتياز بنجاح",
    ReasonCode.RC001_NO_CANDLES: "لا توجد شموع كافية للتحليل",
    ReasonCode.RC002_NOT_ENOUGH_VOLUME: "حجم التداول أقل من الحد الأدنى",
    ReasonCode.RC003_LOW_CONFIDENCE: "الثقة أقل من الحد الأدنى",
    ReasonCode.RC004_COOLDOWN: "فترة تهدئة نشطة",
    ReasonCode.RC005_RISK_LIMIT: "تم تجاوز حد المخاطرة",
    ReasonCode.RC006_MARKET_SIDEWAYS: "السوق جانبي — لا اتجاه واضح",
    ReasonCode.RC007_LOW_LIQUIDITY: "السيولة أقل من الحد الأدنى",
    ReasonCode.RC008_SPREAD_TOO_HIGH: "السبريد أعلى من المسموح",
    ReasonCode.RC009_STRATEGY_FAILED: "فشل تقييم الاستراتيجية",
    ReasonCode.RC010_NO_STRATEGY_MATCH: "لا توجد استراتيجية مناسبة",
    ReasonCode.RC011_EVIDENCE_FAILED: "فشل محرك الدليل في التحقق",
    ReasonCode.RC012_RISK_ENGINE_BLOCKED: "محرك المخاطر منع الصفقة",
    ReasonCode.RC013_EXECUTION_BLOCKED: "طبقة التنفيذ منعت الصفقة",
    ReasonCode.RC014_SYSTEM_NOT_READY: "النظام غير جاهز للتداول",
    ReasonCode.RC015_MAX_DRAWDOWN: "تم تجاوز أقصى انخفاض مسموح",
    ReasonCode.RC016_REGIME_MISMATCH: "نظام السوق غير مناسب للاستراتيجية",
    ReasonCode.RC017_CONTRACT_FAILED: "فشل عقد الاستراتيجية",
    ReasonCode.RC018_WRONG_TIMEFRAME: "الإطار الزمني غير مدعوم",
    ReasonCode.RC019_NO_DIRECTION: "لا يوجد اتجاه واضح",
    ReasonCode.RC020_CONTRADICTORY_SIGNALS: "إشارات متناقضة بين الاستراتيجيات",
    ReasonCode.RC021_NO_CONSENSUS: "لا إجماع بين الاستراتيجيات",
    ReasonCode.RC022_EXPOSURE_LIMIT: "تم تجاوز حد التعرض",
    ReasonCode.RC023_DAILY_LOSS_LIMIT: "تم تجاوز حد الخسارة اليومية",
    ReasonCode.RC024_CAPITAL_INSUFFICIENT: "رأس المال غير كافٍ للصفقة",
    ReasonCode.RC025_POSITION_EXISTS: "يوجد مركز مفتوح مسبقاً على العملة",
    ReasonCode.RC026_INVALID_ORDER: "أمر غير صالح",
    ReasonCode.RC027_PRICE_OUT_OF_RANGE: "السعر خارج النطاق المسموح",
    ReasonCode.RC028_WS_DISCONNECTED: "WebSocket منفصل — بيانات غير محدثة",
    ReasonCode.RC029_KILLSWITCH_ACTIVE: "مفتاح الطوارئ مفعّل — تداول ممنوع",
    ReasonCode.RC030_MARKET_DATA_STALE: "بيانات السوق قديمة (> 60 ثانية)",
    ReasonCode.RC031_ARBITER_REJECTED: "Arbiter رفض الصفقة",
    ReasonCode.RC032_TREND_CONTRADICTION: "الاتجاه يتعارض مع الإشارة",
}


def describe(code: ReasonCode) -> str:
    return CODE_DESCRIPTIONS.get(code, "كود غير معروف")


def reject(engine: str, rule: str, code: ReasonCode,
           current: Any = None, required: Any = None,
           details: str = "") -> Rejection:
    """إنشاء كائن رفض بسهولة."""
    return Rejection(
        engine=engine,
        rule=rule,
        reason=describe(code),
        code=code,
        current_value=current,
        required_value=required,
        details=details,
    )


def passed() -> Rejection:
    """كائن نجاح."""
    return Rejection(
        engine="System",
        rule="All",
        reason="Passed",
        code=ReasonCode.RC000_PASS,
    )
