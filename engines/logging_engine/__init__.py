"""
محرك السجلات — تسجيل مركزي لجميع الأحداث.
كل حدث يُسجل مع الطابع الزمني والوحدة والمستوى والسياق.

V4.0 — جميع الرسائل والإخراج بالعربية.
المستويات: معلومات | تحذير | خطأ | حرج
"""
import logging
import traceback
from datetime import datetime
from core.base import BaseEngine
from core.events import LogEvent, LogLevel
from database.repositories import LogRepository, get_session
from database.models import SystemLog

# ── تعيين المستويات: الإنجليزية LogLevel → العربية ──────────
_LEVEL_TO_ARABIC = {
    LogLevel.DEBUG: "معلومات",
    LogLevel.INFO: "معلومات",
    LogLevel.WARNING: "تحذير",
    LogLevel.ERROR: "خطأ",
    LogLevel.CRITICAL: "حرج",
}

# ── تعيين المستويات العربية → دوال logger بايثون ────────────
_ARABIC_TO_PYTHON_LEVEL = {
    "معلومات": "info",
    "تحذير": "warning",
    "خطأ": "error",
    "حرج": "critical",
}

# ── وسوم السجلات ────────────────────────────────────────────
_LEVEL_TAGS = {
    "معلومات": "[سجلات]",
    "تحذير": "[تحذير]",
    "خطأ": "[خطأ]",
    "حرج": "[حرج]",
}


class LoggingEngine(BaseEngine):
    """محرك تسجيل مركزي. كل حدث يمر يُحفظ في قاعدة البيانات والطرفية."""

    def __init__(self):
        super().__init__("محرك_السجلات")
        self._queue: list = []

    async def initialize(self) -> None:
        self.logger.info("[سجلات] تم تهيئة محرك السجلات بنجاح.")

    async def start(self) -> None:
        self._running = True
        self.logger.info("[سجلات] بدأ محرك السجلات في العمل.")

    async def stop(self) -> None:
        self._running = False
        if self._queue:
            await self._flush()
        self.logger.info("[سجلات] توقف محرك السجلات.")

    # ─────────────────────────────────────────────────────────
    #  تحويل المستوى إلى العربية
    # ─────────────────────────────────────────────────────────

    def _resolve_level(self, level) -> str:
        """
        يحول أي صيغة مستوى إلى العربية.
        يقبل: LogLevel enum، نص إنجليزي (INFO/WARNING/...)، أو نص عربي مباشر.
        """
        if isinstance(level, LogLevel):
            return _LEVEL_TO_ARABIC.get(level, "معلومات")
        if level in ("معلومات", "تحذير", "خطأ", "حرج"):
            return level
        # محاولة تحويل النص الإنجليزي (حالة نادرة للتوافق الخلفي)
        eng_map = {
            "DEBUG": "معلومات", "INFO": "معلومات",
            "WARNING": "تحذير", "ERROR": "خطأ", "CRITICAL": "حرج",
        }
        return eng_map.get(str(level).upper(), "معلومات")

    # ─────────────────────────────────────────────────────────
    #  واجهة التسجيل الرئيسية
    # ─────────────────────────────────────────────────────────

    async def log(self, level, module: str, message: str,
                  context: dict = None, exception: Exception = None):
        """
        نقطة الدخول الرئيسية للتسجيل.
        يُسجل في قاعدة البيانات + الطرفية.

        Args:
            level: LogLevel enum أو نص عربي (معلومات/تحذير/خطأ/حرج)
            module: اسم الوحدة المُنتجة للسجل
            message: نص الرسالة بالعربية
            context: قاموس سياق اختياري
            exception: استثناء اختياري لتضمين تتبع المكدس
        """
        arabic_level = self._resolve_level(level)

        entry = {
            "level": arabic_level,
            "module": module,
            "message": message,
            "context": context or {},
            "stack_trace": traceback.format_exc() if exception else None,
        }
        self._queue.append(entry)

        # ── إخراج الطرفية ──
        python_level = _ARABIC_TO_PYTHON_LEVEL.get(arabic_level, "info")
        log_func = getattr(self.logger, python_level, self.logger.info)
        tag = _LEVEL_TAGS.get(arabic_level, "[سجلات]")
        log_func(f"{tag} [{module}] {message}")

    async def log_event(self, event: LogEvent):
        """
        تسجيل حدث منظم من نوع LogEvent.

        Args:
            event: نسخة من LogEvent تحتوي على المستوى والوحدة والرسالة والسياق
        """
        await self.log(
            level=event.level,
            module=event.module,
            message=event.message,
            context=event.context,
        )

    # ─────────────────────────────────────────────────────────
    #  حفظ السجلات في قاعدة البيانات
    # ─────────────────────────────────────────────────────────

    async def _flush(self):
        """حفظ السجلات المؤقتة من الطابور إلى قاعدة البيانات."""
        count = len(self._queue)
        if count == 0:
            return
        try:
            async for session in get_session():
                for entry in self._queue:
                    log_entry = SystemLog(
                        level=entry["level"],
                        module=entry["module"],
                        message=entry["message"],
                        context=entry["context"],
                    )
                    session.add(log_entry)
                await session.commit()
            self.logger.info(f"[سجلات] تم حفظ {count} سجل في قاعدة البيانات.")
        except Exception as e:
            self.logger.error(f"[خطأ] فشل حفظ السجلات في قاعدة البيانات: {e}")
        finally:
            self._queue.clear()

    # ─────────────────────────────────────────────────────────
    #  استعلام السجلات
    # ─────────────────────────────────────────────────────────

    async def query_recent(self, limit: int = 50) -> list:
        """
        استرجاع أحدث السجلات من قاعدة البيانات.

        Args:
            limit: الحد الأقصى لعدد السجلات المُسترجعة (افتراضي: 50)

        Returns:
            قائمة بأحدث سجلات SystemLog
        """
        async for session in get_session():
            return await LogRepository.get_recent(session, limit)
