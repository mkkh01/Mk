"""
مراقب الصحة — يراقب جميع المحركات وصحة النظام بشكل مستمر.
يكتشف الأعطال، يشغّل الاسترداد، ويرسل التنبيهات. لا يتداول.

الخدمات المراقَبة:
  قاعدة البيانات · بيانات السوق · المحلل · الاستراتيجيات · التنفيذ · تليجرام

الحالات (بالعربية):
  صحيحة → تحذير → متدهورة → فاشلة → آمنة

القاعدة: لا يُعلن "متدهورة" إلا إذا كان هناك فشل حقيقي في الخدمة
         (انقطاع نبض القلب). التحذيرات فقط (موارد عالية، تأخير) لا ترفع الحالة لـ"متدهورة".
"""
import asyncio
import logging
import psutil
from datetime import datetime, timedelta
from typing import Optional

from core.base import BaseEngine
from core.events import AlertEvent, AlertLevel, HealthEvent, HealthStatus, EventBus
from config.constants import HEARTBEAT_INTERVAL_SEC


# ═══════════════════════════════════════════════════════════════
#  حالات النظام (بالعربية)
# ═══════════════════════════════════════════════════════════════

class SystemHealth:
    """حالات صحة النظام — بالقيم العربية."""
    صحيحة = "صحيحة"
    تحذير = "تحذير"
    متدهورة = "متدهورة"
    فاشلة = "فاشلة"
    آمنة = "آمنة"

    # خريطة الأولوية (من الأدنى إلى الأعلى خطورة)
    _priority = {
        صحيحة: 0,
        تحذير: 1,
        متدهورة: 2,
        فاشلة: 3,
        آمنة: 4,
    }

    @classmethod
    def worse(cls, a: str, b: str) -> str:
        """ترجع الحالة الأسوأ بين حالتين."""
        return a if cls._priority.get(a, 0) >= cls._priority.get(b, 0) else b


class ServiceStatus:
    """حالة الخدمة الفردية."""
    صحيحة = "صحيحة"
    تحذير = "تحذير"
    متدهورة = "متدهورة"
    فاشلة = "فاشلة"


# ═══════════════════════════════════════════════════════════════
#  مراقب الصحة
# ═══════════════════════════════════════════════════════════════

class HealthMonitor(BaseEngine):
    """يراقب صحة النظام. يشغّل الاسترداد عند الحاجة."""

    # أسماء المحركات المتوقعة للمراقبة
    MONITORED_SERVICES = [
        "database",
        "market_data",
        "market_analyzer",
        "strategy_engine",
        "execution_engine",
        "telegram_bot",
    ]

    def __init__(self, event_bus: EventBus):
        super().__init__("health_monitor")
        self.event_bus = event_bus
        # حالة كل خدمة
        self._service_statuses: dict[str, dict] = {}
        # آخر نبض قلب لكل محرك
        self._last_heartbeats: dict[str, datetime] = {}
        # سجل التنبيهات
        self._alerts: list[AlertEvent] = []
        # حالة النظام العامة
        self.system_state: str = SystemHealth.صحيحة
        # إعدادات
        self._check_interval = HEARTBEAT_INTERVAL_SEC
        self._heartbeat_timeout = 120  # ثواني قبل اعتبار المحرك فاشلاً (ضعف مدة النبض 60s)
        self._warning_timeout = 65   # ثواني قبل تحذير (أطول قليلاً من مدة النبض 60s)
        self._max_alerts = 100
        # عداد الأخطاء لكل خدمة
        self._error_counts: dict[str, int] = {}

    # ═════════════════════════════════════════════════════════
    #  دورة الحياة
    # ═════════════════════════════════════════════════════════

    async def initialize(self) -> None:
        await self.event_bus.subscribe("HealthEvent", self._on_health_event)
        self.logger.info("[مراقبة] تم تهيئة مراقب الصحة.")

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._monitor_loop())
        self.logger.info("[مراقبة] بدأ مراقب الصحة العمل.")

    async def stop(self) -> None:
        self._running = False
        self.logger.info("[مراقبة] توقف مراقب الصحة.")

    # ═════════════════════════════════════════════════════════
    #  استقبال نبض القلب
    # ═════════════════════════════════════════════════════════

    async def _on_health_event(self, event: HealthEvent):
        """استقبال نبض القلب من أي محرك."""
        self._service_statuses[event.engine] = {
            "status": event.status.value,
            "latency_ms": event.latency_ms,
            "error_rate": event.error_rate,
            "memory_usage": event.memory_usage,
        }
        self._last_heartbeats[event.engine] = event.timestamp or datetime.utcnow()

    # ═════════════════════════════════════════════════════════
    #  حلقة المراقبة الرئيسية
    # ═════════════════════════════════════════════════════════

    async def _monitor_loop(self):
        """حلقة المراقبة الرئيسية."""
        while self._running:
            try:
                await self._check_all_services()
                await self._check_system_resources()
                await self._evaluate_system_state()
                await asyncio.sleep(self._check_interval)
            except Exception as e:
                self.logger.error(f"[مراقبة] خطأ في دورة الفحص: {e}", exc_info=True)

    # ═════════════════════════════════════════════════════════
    #  فحص الخدمات
    # ═════════════════════════════════════════════════════════

    async def _check_all_services(self):
        """فحص نضارة نبض القلب لجميع الخدمات المسجلة."""
        now = datetime.utcnow()
        for service_name in list(self._last_heartbeats.keys()):
            last_hb = self._last_heartbeats[service_name]
            age = (now - last_hb).total_seconds()

            old_status = self._service_statuses.get(service_name, {}).get("status", "UNKNOWN")

            if age > self._heartbeat_timeout:
                # انقطع النبض تماماً → فاشلة
                if old_status != ServiceStatus.فاشلة:
                    self._service_statuses[service_name] = {
                        "status": ServiceStatus.فاشلة,
                        "latency_ms": 0,
                        "error_rate": 1.0,
                        "memory_usage": 0,
                        "last_seen_sec": age,
                    }
                    self._increment_error(service_name)
                    await self._send_alert(
                        AlertLevel.CRITICAL, service_name,
                        f"الخدمة {service_name} فاشلة — انقطع نبض القلب ({age:.0f} ثانية)"
                    )
                    self.logger.error(
                        f"[صحة] ❌ {service_name}: فاشلة — آخر نبض منذ {age:.0f} ثانية"
                    )

            elif age > self._warning_timeout:
                # تأخر النبض لكنه لم ينقطع → تحذير
                if old_status not in (ServiceStatus.تحذير, ServiceStatus.فاشلة):
                    self._service_statuses[service_name] = {
                        "status": ServiceStatus.تحذير,
                        "latency_ms": 0,
                        "error_rate": 0,
                        "memory_usage": 0,
                        "last_seen_sec": age,
                    }
                    self.logger.warning(
                        f"[صحة] ⚠️ {service_name}: تحذير — آخر نبض منذ {age:.0f} ثانية"
                    )

            else:
                # النبض طازج → صحيحة (إذا لم تكن فاشلة سابقاً)
                if old_status == ServiceStatus.فاشلة:
                    self._service_statuses[service_name] = {
                        "status": ServiceStatus.صحيحة,
                        "latency_ms": self._service_statuses.get(service_name, {}).get("latency_ms", 0),
                        "error_rate": 0,
                        "memory_usage": 0,
                        "last_seen_sec": age,
                    }
                    self.logger.info(
                        f"[صحة] ✅ {service_name}: عادت للعمل — تم استرداد الخدمة"
                    )
                elif old_status != ServiceStatus.صحيحة:
                    self._service_statuses[service_name] = {
                        "status": ServiceStatus.صحيحة,
                        "latency_ms": self._service_statuses.get(service_name, {}).get("latency_ms", 0),
                        "error_rate": 0,
                        "memory_usage": 0,
                        "last_seen_sec": age,
                    }

    # ═════════════════════════════════════════════════════════
    #  فحص موارد النظام
    # ═════════════════════════════════════════════════════════

    async def _check_system_resources(self):
        """فحص CPU، الذاكرة، القرص."""
        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=0.1)  # 0.1 بدل 0.5 لتقليل الحجب

        if mem.percent > 90:
            await self._send_alert(
                AlertLevel.WARNING, "system",
                f"استهلاك الذاكرة حرج: {mem.percent}%"
            )
            self.logger.warning(f"[تنبيه] ذاكرة: {mem.percent}%")

        if cpu > 90:
            await self._send_alert(
                AlertLevel.WARNING, "system",
                f"استهلاك المعالج حرج: {cpu}%"
            )
            self.logger.warning(f"[تنبيه] معالج: {cpu}%")

    # ═════════════════════════════════════════════════════════
    #  تقييم حالة النظام
    # ═════════════════════════════════════════════════════════

    async def _evaluate_system_state(self):
        """
        تقييم الحالة العامة للنظام.
        القاعدة: لا نعلن "متدهورة" إلا بوجود فشل حقيقي في الخدمات.
        """
        service_states = [
            s.get("status", ServiceStatus.صحيحة)
            for s in self._service_statuses.values()
        ]

        failed_count = sum(1 for s in service_states if s == ServiceStatus.فاشلة)
        degraded_count = sum(1 for s in service_states if s == ServiceStatus.متدهورة)
        warning_count = sum(1 for s in service_states if s == ServiceStatus.تحذير)

        # تحديد الحالة
        if failed_count >= 2:
            new_state = SystemHealth.آمنة
        elif failed_count >= 1:
            # فشل حقيقي واحد → متدهورة (وليس تحذير)
            new_state = SystemHealth.متدهورة
        elif warning_count >= 2:
            # تحذيرات متعددة لكن لا يوجد فشل حقيقي → تحذير فقط
            new_state = SystemHealth.تحذير
        elif warning_count >= 1:
            new_state = SystemHealth.تحذير
        else:
            new_state = SystemHealth.صحيحة

        # تسجيل تغيير الحالة
        if new_state != self.system_state:
            self.logger.info(
                f"[صحة] تغيير حالة النظام: {self.system_state} → {new_state} "
                f"(فاشلة={failed_count}، تحذير={warning_count})"
            )
            self.system_state = new_state

            # تنبيه عند الحالات الحرجة
            if new_state == SystemHealth.آمنة:
                await self._send_alert(
                    AlertLevel.CRITICAL, "system",
                    f"⚠️ النظام في وضع {SystemHealth.آمنة} — توقف التداول"
                )
            elif new_state == SystemHealth.متدهورة:
                await self._send_alert(
                    AlertLevel.WARNING, "system",
                    f"⚠️ النظام {SystemHealth.متدهورة} — فشل في خدمة واحدة"
                )
            elif new_state == SystemHealth.تحذير:
                await self._send_alert(
                    AlertLevel.INFO, "system",
                    f"⚡ النظام في حالة {SystemHealth.تحذير} — {warning_count} تحذير"
                )

    # ═════════════════════════════════════════════════════════
    #  التنبيهات
    # ═════════════════════════════════════════════════════════

    async def _send_alert(self, level: AlertLevel, module: str, message: str):
        alert = AlertEvent(level=level, module=module, message=message)
        self._alerts.append(alert)
        if len(self._alerts) > self._max_alerts:
            self._alerts = self._alerts[-self._max_alerts:]
        await self.event_bus.publish(alert)
        log_level = getattr(logging, level.value, logging.INFO)
        self.logger.log(log_level, f"[تنبيه] [{module}] {message}")

    def _increment_error(self, service_name: str):
        self._error_counts[service_name] = self._error_counts.get(service_name, 0) + 1

    # ═════════════════════════════════════════════════════════
    #  تقرير الصحة الديناميكي
    # ═════════════════════════════════════════════════════════

    def get_health_report(self) -> dict:
        """
        تقرير صحة ديناميكي — يُرجع حالة كل خدمة مع metrics.
        يُحسب في الوقت الفعلي، لا يعتمد على قيم static.
        """
        now = datetime.utcnow()

        # بناء تقرير كل خدمة
        services = {}
        for name in self.MONITORED_SERVICES:
            svc = self._service_statuses.get(name, {})
            last_hb = self._last_heartbeats.get(name)
            sec_since = (now - last_hb).total_seconds() if last_hb else None

            services[name] = {
                "status": svc.get("status", "غير معروفة"),
                "latency_ms": svc.get("latency_ms", 0),
                "error_rate": svc.get("error_rate", 0),
                "memory_usage": svc.get("memory_usage", 0),
                "last_heartbeat_sec": round(sec_since, 1) if sec_since else None,
                "errors_total": self._error_counts.get(name, 0),
            }

        # أي خدمة غير موجودة في السجل تعتبر "غير معروفة"
        # أضف أي خدمات ظهرت في الأحداث ولم تكن في القائمة الأساسية
        for extra in set(self._service_statuses.keys()) - set(self.MONITORED_SERVICES):
            svc = self._service_statuses.get(extra, {})
            last_hb = self._last_heartbeats.get(extra)
            sec_since = (now - last_hb).total_seconds() if last_hb else None
            services[extra] = {
                "status": svc.get("status", "غير معروفة"),
                "latency_ms": svc.get("latency_ms", 0),
                "error_rate": svc.get("error_rate", 0),
                "memory_usage": svc.get("memory_usage", 0),
                "last_heartbeat_sec": round(sec_since, 1) if sec_since else None,
                "errors_total": self._error_counts.get(extra, 0),
            }

        # إحصائيات عامة
        statuses = [s["status"] for s in services.values()]
        failed_count = sum(1 for s in statuses if s == ServiceStatus.فاشلة)
        warning_count = sum(1 for s in statuses if s == ServiceStatus.تحذير)
        healthy_count = sum(1 for s in statuses if s == ServiceStatus.صحيحة)

        return {
            "system_state": self.system_state,
            "monitor_running": self._running,
            "timestamp": now.isoformat(),
            "summary": {
                "total_services": len(services),
                "healthy": healthy_count,
                "warning": warning_count,
                "failed": failed_count,
                "alerts_total": len(self._alerts),
            },
            "services": services,
            "trading_allowed": self.is_trading_safe(),
        }

    # ═════════════════════════════════════════════════════════
    #  استعلامات الحالة
    # ═════════════════════════════════════════════════════════

    def get_status(self) -> dict:
        """ملخص سريع لحالة النظام."""
        return {
            "system_state": self.system_state,
            "services": {
                name: svc.get("status", "غير معروفة")
                for name, svc in self._service_statuses.items()
            },
            "alerts_count": len(self._alerts),
        }

    def get_recent_alerts(self, limit: int = 10) -> list:
        """آخر التنبيهات."""
        return [
            {
                "level": a.level.value,
                "module": a.module,
                "message": a.message,
                "timestamp": a.timestamp.isoformat(),
            }
            for a in self._alerts[-limit:]
        ]

    def is_trading_safe(self) -> bool:
        """
        هل التداول آمن؟
        التداول مسموح في: صحيحة، تحذير، متدهورة
        التداول ممنوع في: فاشلة، آمنة
        """
        return self.system_state not in (SystemHealth.فاشلة, SystemHealth.آمنة)

    async def heartbeat(self) -> dict:
        return {
            "engine": self.name,
            "status": "HEALTHY" if self._running else "STOPPED",
            "system_state": self.system_state,
            "latency_ms": 0,
            "error_rate": 0,
            "last_update": datetime.utcnow(),
        }
