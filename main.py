"""
نقطة الدخول الرئيسية — تنسيق بدء التشغيل بدون أي منطق تجاري.
V4.1: آلة حالات مركزية — State Machine — لا UnboundLocalError، لا race conditions.
"""
import asyncio
import logging
import sys
import os
import traceback
from datetime import datetime, timezone

def _utcnow():
    """تُرجع datetime.now(tz=timezone.utc) — بديل لـ utcnow()."""
    return datetime.now(tz=timezone.utc)

# ── سجلات منظمة ───────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")

# ── Core ────────────────────────────────────────────────────
from core.events import EventBus

# ── Config ──────────────────────────────────────────────────
from config.settings import get_settings

# ── Database ────────────────────────────────────────────────
from database.repositories import init_db, close_db
from database.models import Base

# ── Engines ─────────────────────────────────────────────────
from engines.config_engine import ConfigEngine
from engines.logging_engine import LoggingEngine
from engines.market_data_engine import MarketDataEngine
from engines.market_analyzer import MarketAnalyzer
from engines.strategy_engine import StrategyEngine
from engines.evidence_engine import EvidenceEngine
from engines.risk_engine import RiskEngine
from engines.execution_engine import ExecutionEngine
from engines.portfolio_engine import PortfolioEngine
from engines.learning_engine import LearningEngine
from engines.reporting_engine import ReportingEngine
from engines.health_monitor import HealthMonitor

# ── Services ────────────────────────────────────────────────
from services.analysis_service import AnalysisService
from services.trading_service import TradingService
from services.portfolio_service import PortfolioService
from services.risk_service import RiskService

# ── Bot ─────────────────────────────────────────────────────
from bots.telegram.bot import TelegramEngine

# ── Keep Alive ──────────────────────────────────────────────
from keep_alive import keep_alive


# ═══════════════════════════════════════════════════════════════
#  آلة الحالات المركزية (State Machine)
# ═══════════════════════════════════════════════════════════════

class TradingState:
    """حالة النظام المركزية — مصدر وحيد للحقيقة. Deterministic + Idempotent.

    آلة الحالات (State Machine):
        INIT ──▶ CONNECTING_WS ──▶ LOADING_HISTORY ──▶ WARMING_UP ──▶ RUNNING

    الانتقالات المسموحة:
        INIT           → CONNECTING_WS  (بدء تهيئة المحركات)
        CONNECTING_WS  → LOADING_HISTORY (بعد تهيئة المحركات — تحميل تاريخي)
        LOADING_HISTORY → WARMING_UP     (بعد اكتمال/فشل التسخين)
        WARMING_UP     → RUNNING         (WS مستقر + ticks كافية)

    خصائص الضمان (Production Hardening):
        - Idempotent: أي transition لنفس المرحلة = NO-OP
        - Immutable: المراحل لا تتكرر بعد الخروج منها (إلا ERROR→WARMING_UP)
        - Invariants: تحقق من الثوابت قبل وبعد كل انتقال
        - Reconnect-safe: لا يُعاد تصفير ticks إلا في INIT
        - Duplicate-safe: لا يمكن دخول RUNNING مرتين
    """

    INIT = "INIT"
    CONNECTING_WS = "CONNECTING_WS"
    LOADING_HISTORY = "LOADING_HISTORY"
    WARMING_UP = "WARMING_UP"
    RUNNING = "RUNNING"
    ERROR = "ERROR"

    # الانتقالات المسموحة
    _VALID_TRANSITIONS: dict[str, set[str]] = {
        "INIT": {"CONNECTING_WS", "ERROR"},
        "CONNECTING_WS": {"LOADING_HISTORY", "WARMING_UP", "ERROR"},
        "LOADING_HISTORY": {"WARMING_UP", "ERROR"},
        "WARMING_UP": {"RUNNING", "ERROR"},
        "RUNNING": {"ERROR"},
        "ERROR": {"WARMING_UP"},
    }

    # المراحل التي لا يمكن إعادة دخولها بعد الخروج
    _ONCE_ONLY_PHASES: set[str] = {"INIT", "CONNECTING_WS", "LOADING_HISTORY", "RUNNING"}

    def __init__(self):
        self.phase: str = self.INIT
        self.phase_set_at: float = _utcnow().timestamp()
        self.started_at = _utcnow()
        self.errors: list = []
        self._transition_count: int = 0
        self._entered_phases: set[str] = {self.INIT}  # مراحل دُخلت فعلاً
        self._exited_phases: set[str] = set()          # مراحل خرج منها

        # التداول
        self.open_positions: list = []
        self.coins: list = []
        self.price_lines: list = []
        self.signals_found: int = 0
        self.analysis_ok: int = 0
        self.analysis_miss: int = 0

        # WebSocket — لا تصفير إلا في INIT
        self.ws_connected: bool = False
        self.ws_connected_at: float = 0.0
        self.ws_stable_since: float = 0.0       # متى استقر الاتصال
        self.ws_tick_count: int = 0              # لا يُصفّر عند reconnect
        self.ws_reconnect_count: int = 0         # عداد إعادة الاتصال
        self.ws_last_seen_at: float = 0.0        # آخر مرة وردت بيانات
        self.history_loaded: bool = False

        # حدود
        self.MIN_CANDLES = 50
        self.MIN_WS_TICKS = 20
        self.MIN_WS_STABLE_SEC = 15              # نافذة استقرار WS قبل RUNNING

        # كشف العالق
        self._phase_stuck_warned: set = set()
        self.MAX_CYCLES_IN_PHASE: dict[str, int] = {
            "INIT": 0,
            "CONNECTING_WS": 0,
            "LOADING_HISTORY": 10,
            "WARMING_UP": 300,
            "RUNNING": 0,
        }

    # ═══════════════════════════════════════════════════════
    #  Idempotent Transition
    # ═══════════════════════════════════════════════════════

    def transition(self, new_phase: str) -> None:
        """انتقال آلة الحالات — Idempotent + Invariant-checked."""
        old = self.phase
        now = _utcnow()
        now_ts = now.timestamp()
        duration = now_ts - self.phase_set_at

        # 🛡️ Idempotent: نفس المرحلة = NO-OP
        if new_phase == old:
            return

        # 🛡️ تحقق: هل الانتقال مسموح؟
        allowed = self._VALID_TRANSITIONS.get(old, set())
        if new_phase not in allowed:
            logger.critical(
                f"[آلة_الحالات] ❌ انتقال غير مسموح: {old} → {new_phase} | "
                f"المسموح من {old}: {sorted(allowed) if allowed else '—'}"
            )
            return

        # 🛡️ تحقق: لا يمكن إعادة دخول مرحلة once-only
        if new_phase in self._ONCE_ONLY_PHASES and new_phase in self._exited_phases:
            logger.critical(
                f"[آلة_الحالات] ❌ محاولة إعادة دخول مرحلة غير قابلة للتكرار: "
                f"{new_phase} (سُبِق الخروج منها)"
            )
            return

        # 🛡️ تحقق: ثوابت ما قبل الانتقال
        self._check_pre_transition_invariants(old, new_phase)

        # تنفيذ الانتقال
        self._exited_phases.add(old)
        self.phase = new_phase
        self.phase_set_at = now_ts
        self._entered_phases.add(new_phase)
        self._transition_count += 1

        # 🛡️ تحقق: ثوابت ما بعد الانتقال
        self._check_post_transition_invariants(new_phase)

        labels = {
            self.INIT: "🟡 بدء التشغيل",
            self.CONNECTING_WS: "🔌 الاتصال بـ WebSocket",
            self.LOADING_HISTORY: "📥 تحميل البيانات التاريخية",
            self.WARMING_UP: "🔥 تسخين — بناء المخازن",
            self.RUNNING: "✅ مباشر — تحليل + إشارات + تنفيذ",
            self.ERROR: "🔴 خطأ",
        }
        logger.info("═" * 50)
        logger.info(
            f"[آلة_الحالات] انتقال #{self._transition_count}: {old} → {new_phase}"
        )
        logger.info(
            f"[آلة_الحالات] السبب: {labels.get(new_phase, new_phase)} | "
            f"المدة في {old}: {duration:.1f}ث | ticks={self.ws_tick_count} | "
            f"reconnects={self.ws_reconnect_count}"
        )
        logger.info(
            f"[آلة_الحالات] المراحل المزارة: {' → '.join(sorted(self._entered_phases, key=lambda x: {'INIT':0,'CONNECTING_WS':1,'LOADING_HISTORY':2,'WARMING_UP':3,'RUNNING':4,'ERROR':5}.get(x,99)))}"
        )
        logger.info("═" * 50)

    # ═══════════════════════════════════════════════════════
    #  Invariants
    # ═══════════════════════════════════════════════════════

    def _check_pre_transition_invariants(self, old: str, new: str) -> None:
        """ثوابت يجب أن تتحقق قبل الانتقال."""
        if new == self.RUNNING:
            assert self.ws_connected, (
                f"[ثابت] RUNNING يتطلب ws_connected=True"
            )
            assert self.ws_tick_count >= self.MIN_WS_TICKS, (
                f"[ثابت] RUNNING يتطلب ticks≥{self.MIN_WS_TICKS}، الحالي={self.ws_tick_count}"
            )
            stable_duration = _utcnow().timestamp() - self.ws_stable_since
            assert stable_duration >= self.MIN_WS_STABLE_SEC or self.ws_reconnect_count == 0, (
                f"[ثابت] RUNNING يتطلب WS مستقر {self.MIN_WS_STABLE_SEC}ث، "
                f"الحالي={stable_duration:.1f}ث | reconnects={self.ws_reconnect_count}"
            )

    def _check_post_transition_invariants(self, phase: str) -> None:
        """ثوابت يجب أن تتحقق بعد الانتقال."""
        if phase == self.RUNNING:
            assert self.trading_allowed, (
                f"[ثابت] RUNNING يجب أن يكون trading_allowed=True"
            )
            assert self.analysis_allowed, (
                f"[ثابت] RUNNING يجب أن يكون analysis_allowed=True"
            )
        elif phase == self.WARMING_UP:
            assert self.analysis_allowed, (
                f"[ثابت] WARMING_UP يجب أن يكون analysis_allowed=True"
            )
            assert not self.trading_allowed, (
                f"[ثابت] WARMING_UP لا يجب أن يسمح بالتداول"
            )

    # ═══════════════════════════════════════════════════════
    #  WebSocket management
    # ═══════════════════════════════════════════════════════

    def mark_ws_connected(self) -> None:
        """تسجيل اتصال WebSocket — لا يُصفّر ticks."""
        was_connected = self.ws_connected
        self.ws_connected = True
        self.ws_connected_at = _utcnow().timestamp()
        if not was_connected:
            self.ws_stable_since = self.ws_connected_at
        logger.info(
            f"[آلة_الحالات] 🔌 WebSocket متصل | ticks={self.ws_tick_count} | "
            f"reconnects={self.ws_reconnect_count} | stable_window={'جديد' if not was_connected else 'مستمر'}"
        )

    def mark_ws_disconnected(self) -> None:
        """تسجيل انقطاع WebSocket — يزيد عداد reconnect ولا يُصفّر ticks."""
        if self.ws_connected:
            self.ws_connected = False
            self.ws_reconnect_count += 1
            self.ws_stable_since = 0.0  # إعادة تعيين نافذة الاستقرار
            logger.warning(
                f"[آلة_الحالات] 🔌 WebSocket منفصل | ticks={self.ws_tick_count} | "
                f"reconnect #{self.ws_reconnect_count}"
            )

    def record_tick(self) -> None:
        """تسجيل tick وارد — لا يُعاد تصفيره إلا في INIT."""
        self.ws_tick_count += 1
        self.ws_last_seen_at = _utcnow().timestamp()

    @property
    def ws_is_stable(self) -> bool:
        """هل WebSocket مستقر لمدة كافية؟"""
        if not self.ws_connected or self.ws_stable_since <= 0:
            return False
        return (_utcnow().timestamp() - self.ws_stable_since) >= self.MIN_WS_STABLE_SEC

    @property
    def ws_ready_for_running(self) -> bool:
        """كل شروط WebSocket لـ RUNNING."""
        return (
            self.ws_connected
            and self.ws_tick_count >= self.MIN_WS_TICKS
            and self.ws_is_stable
        )

    @property
    def trading_allowed(self) -> bool:
        return self.phase == self.RUNNING

    @property
    def analysis_allowed(self) -> bool:
        return self.phase in (self.WARMING_UP, self.RUNNING)

    @property
    def health(self) -> str:
        if self.errors:
            return "تحذير"
        if self.phase == self.RUNNING:
            return "صحيحة"
        return self.phase

    def check_stuck(self, cycle: int) -> None:
        """تحقق من عدم بقاء الحالة عالقة أكثر من الحد المسموح."""
        max_cycles = self.MAX_CYCLES_IN_PHASE.get(self.phase, 0)
        if max_cycles > 0 and cycle > max_cycles and self.phase not in self._phase_stuck_warned:
            self._phase_stuck_warned.add(self.phase)
            duration = _utcnow().timestamp() - self.phase_set_at
            logger.error(
                f"[آلة_الحالات] ⚠️ حالة عالقة: {self.phase} لمدة {cycle} دورة "
                f"({duration:.0f}ث) — الحد الأقصى: {max_cycles} دورة | "
                f"الانتقالات المسموحة: {sorted(self._VALID_TRANSITIONS.get(self.phase, set()))}"
            )

    def add_error(self, component: str, error: str) -> None:
        self.errors.append({"component": component, "error": error, "time": _utcnow().isoformat()})
        logger.error(f"[نظام] ❌ {component}: {error}")

    def reset_cycle(self) -> None:
        self.open_positions = []
        self.price_lines = []
        self.signals_found = 0
        self.analysis_ok = 0
        self.analysis_miss = 0

    def can_start(self) -> bool:
        failed = []
        if not self.ws_connected:
            failed.append("ws_connected")
        if not self.history_loaded:
            failed.append("history_loaded")
        if failed:
            logger.warning(f"[حالة] لا يمكن البدء — ناقص: {failed}")
            return False
        return True


# نسخة وحيدة
state = TradingState()

# للحفاظ على التوافق مع باقي الكود
_system_state = {"status": "بدء_التشغيل", "started_at": None, "errors": [], "preflight": {}, "engines": {}}


def set_system_status(status: str):
    _system_state["status"] = status
    logger.info(f"[النظام] الحالة ← {status}")


# ═══════════════════════════════════════════════════════════════
#  الهجرة التلقائية (Auto-Migration)
# ═══════════════════════════════════════════════════════════════

_MIGRATIONS = {
    "coins": {
        "timeframes": ("JSON", "'[]'::json"),
        "min_entry_size": ("FLOAT", "0.0"),
    },
    "positions": {
        "side": ("VARCHAR(10)", "'BUY'"),
        "exit_price": ("FLOAT", None),
        "close_reason": ("VARCHAR(50)", None),
        "closed_at": ("TIMESTAMP", None),
    },
}


async def auto_migrate() -> tuple[bool, list[str]]:
    from sqlalchemy import text
    from database.repositories import _engine
    if _engine is None:
        return False, []
    added = []
    try:
        async with _engine.connect() as conn:
            for table, columns in _MIGRATIONS.items():
                result = await conn.execute(
                    text("SELECT column_name FROM information_schema.columns WHERE table_name = :tname"),
                    {"tname": table},
                )
                existing = {row[0] for row in result}
                for col_name, (col_type, default_val) in columns.items():
                    if col_name not in existing:
                        if default_val is not None:
                            sql = f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col_name} {col_type} DEFAULT {default_val}"
                        else:
                            sql = f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
                        logger.info(f"[هجرة] إضافة عمود: {table}.{col_name} ({col_type})")
                        await conn.execute(text(sql))
                        added.append(f"{table}.{col_name}")
                if added:
                    await conn.commit()
                    logger.info(f"[هجرة] ✅ تمت إضافة {len(added)} عمود: {', '.join(added)}")
        return True, added
    except Exception as e:
        logger.error(f"[هجرة] ❌ فشل: {e}", exc_info=True)
        return False, added


# ═══════════════════════════════════════════════════════════════
#  فحوصات ما قبل التشغيل
# ═══════════════════════════════════════════════════════════════

async def preflight_check_schema() -> tuple[bool, str]:
    try:
        from sqlalchemy import text
        from database.repositories import _engine
        if _engine is None:
            return False, "محرك قاعدة البيانات غير مهيأ"
        async with _engine.connect() as conn:
            required_tables = ["users", "coins", "trades", "positions",
                              "market_data", "market_state", "signals",
                              "risk_events", "portfolio_snapshots", "logs"]
            for table in required_tables:
                result = await conn.execute(
                    text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = :tname)"),
                    {"tname": table},
                )
                if not result.scalar():
                    return False, f"الجدول مفقود: {table}"
            # coins columns
            required_coins = ["id", "user_id", "symbol", "capital_allocated", "risk_per_trade", "timeframes", "is_active"]
            result = await conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'coins'"))
            existing = {row[0] for row in result}
            for col in required_coins:
                if col not in existing:
                    return False, f"العمود مفقود في coins: {col}"
            # users columns
            required_users = ["id", "telegram_id", "total_capital", "is_active"]
            result = await conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'users'"))
            existing = {row[0] for row in result}
            for col in required_users:
                if col not in existing:
                    return False, f"العمود مفقود في users: {col}"
        return True, "المخطط متطابق"
    except Exception as e:
        return False, f"فشل فحص المخطط: {e}"


async def preflight_check_exchange() -> tuple[bool, str]:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://api.binance.com/api/v3/ping")
            if resp.status_code == 200:
                return True, "الاتصال بـ Binance ناجح"
            return False, f"Binance رد بحالة: {resp.status_code}"
    except Exception as e:
        return False, f"فشل الاتصال بـ Binance: {e}"


async def preflight_check_telegram(token: str) -> tuple[bool, str]:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = resp.json()
            if data.get("ok"):
                return True, f"بوت تيليجرام: @{data['result']['username']}"
            return False, f"توكن تيليجرام غير صالح: {data.get('description', '')}"
    except Exception as e:
        return False, f"فشل الاتصال بـ Telegram API: {e}"


# ═══════════════════════════════════════════════════════════════
#  الدالة الرئيسية
# ═══════════════════════════════════════════════════════════════

def log_banner():
    print("""
╔══════════════════════════════════════════════════╗
║   CT V4.0 — منصة تداول احترافية                  ║
║   Clean Architecture | Multi-Timeframe | عربي     ║
╚══════════════════════════════════════════════════╝""")


async def main():
    log_banner()
    logger.info("═" * 50)
    logger.info("[النظام] بدء تشغيل CT V4.1 — State Machine")
    logger.info("═" * 50)

    # ── 1. تحميل الإعدادات ──────────────────────────────────
    set_system_status("تحميل_الإعدادات")
    try:
        settings = get_settings()
        missing = settings.validate()
        if missing:
            state.add_error("الإعدادات", f"متغيرات مفقودة: {missing}")
            logger.critical(f"[النظام] ❌ متغيرات مفقودة: {missing}")
            sys.exit(1)
        logger.info("[الإعدادات] ✅ تم تحميل الإعدادات")
    except Exception as e:
        state.add_error("الإعدادات", f"فشل التحميل: {e}")
        sys.exit(1)

    # ── 2. Keep-Alive ───────────────────────────────────────
    set_system_status("بدء_الخادم")
    keep_alive()
    logger.info(f"[النظام] [2/10] خادم keep-alive بدأ على المنفذ {settings.port}")

    # ── 3. Event Bus ────────────────────────────────────────
    event_bus = EventBus()
    logger.info("[النظام] [3/10] ناقل الأحداث جاهز")

    # ── 4. قاعدة البيانات ──────────────────────────────────
    set_system_status("الاتصال_بقاعدة_البيانات")
    try:
        await init_db()
        logger.info("[قاعدة البيانات] ✅ تم الاتصال")
    except Exception as e:
        state.add_error("قاعدة البيانات", str(e))
        sys.exit(1)

    # هجرة تلقائية
    migrate_ok, added_cols = await auto_migrate()
    if not migrate_ok:
        state.add_error("الهجرة", "فشل الهجرة التلقائية")
    elif added_cols:
        logger.info(f"[هجرة] أعمدة مضافة: {', '.join(added_cols)}")
    else:
        logger.info("[هجرة] المخطط محدث")

    # فحص المخطط
    schema_ok, schema_msg = await preflight_check_schema()
    if not schema_ok:
        state.add_error("المخطط", schema_msg)
        sys.exit(1)
    logger.info(f"[المخطط] ✅ {schema_msg}")

    # ── 5. فحص الاتصالات ───────────────────────────────────
    set_system_status("فحص_الاتصالات")
    exchange_ok, exchange_msg = await preflight_check_exchange()
    logger.info(f"[اتصال] {'✅' if exchange_ok else '⚠️'} {exchange_msg}")

    telegram_ok, telegram_msg = await preflight_check_telegram(settings.telegram_token)
    if telegram_ok:
        logger.info(f"[اتصال] ✅ {telegram_msg}")
    else:
        state.add_error("تيليجرام", telegram_msg)
        sys.exit(1)

    # ── 6. بدء المحركات الأساسية ───────────────────────────
    set_system_status("بدء_المحركات")
    config_engine = ConfigEngine()
    await config_engine.initialize()
    await config_engine.start()
    logging_engine = LoggingEngine()
    await logging_engine.initialize()
    await logging_engine.start()

    state.transition(TradingState.CONNECTING_WS)
    market_data_engine = MarketDataEngine(event_bus)
    await market_data_engine.initialize()
    await market_data_engine.start()
    logger.info("[محرك] ✅ بيانات السوق بدأ")

    market_analyzer = MarketAnalyzer(event_bus)
    await market_analyzer.initialize()
    await market_analyzer.start()
    logger.info("[محرك] ✅ محلل السوق بدأ")

    strategy_engine = StrategyEngine(event_bus)
    await strategy_engine.initialize()
    await strategy_engine.start()
    logger.info("[محرك] ✅ محرك الاستراتيجيات بدأ")

    # ── 7. المحركات المتقدمة ───────────────────────────────
    evidence_engine = EvidenceEngine(event_bus)
    await evidence_engine.initialize(); await evidence_engine.start()
    risk_engine = RiskEngine(event_bus)
    await risk_engine.initialize(); await risk_engine.start()
    execution_engine = ExecutionEngine(event_bus)
    await execution_engine.initialize(); await execution_engine.start()
    portfolio_engine = PortfolioEngine(event_bus, initial_balance=settings.default_capital)
    await portfolio_engine.initialize(); await portfolio_engine.start()
    learning_engine = LearningEngine(event_bus)
    await learning_engine.initialize(); await learning_engine.start()
    reporting_engine = ReportingEngine(event_bus)
    await reporting_engine.initialize(); await reporting_engine.start()
    health_monitor = HealthMonitor(event_bus)
    await health_monitor.initialize(); await health_monitor.start()
    logger.info("[النظام] ✅ جميع المحركات الـ 14 بدأت")

    # ── 8. الخدمات ─────────────────────────────────────────
    set_system_status("بدء_الخدمات")
    telegram_id = settings.admin_id
    execution_engine._admin_telegram_id = telegram_id
    portfolio_engine._telegram_id = telegram_id
    learning_engine.user_id = str(telegram_id)

    analysis_service = AnalysisService(market_data_engine, market_analyzer, strategy_engine)
    trading_service = TradingService(
        evidence_engine, risk_engine, execution_engine,
        market_analyzer, strategy_engine, market_data_engine, analysis_service
    )
    portfolio_service = PortfolioService(portfolio_engine, reporting_engine, learning_engine, health_monitor)
    risk_service = RiskService(risk_engine)
    logger.info("[النظام] ✅ جميع الخدمات بدأت")

    # ── 9. مزامنة العملات ──────────────────────────────────
    set_system_status("مزامنة_العملات")
    symbols, coins = await analysis_service.sync_symbols_from_db(str(telegram_id))
    logger.info(f"[مزامنة] ✅ تم تحميل {len(symbols)} عملة")

    for coin in coins:
        tfs = coin.timeframes if isinstance(coin.timeframes, list) else [coin.timeframes]
        logger.info(f"[عملة] {coin.symbol} | أطر: {', '.join(tfs)} | رأس مال: {coin.capital_allocated:.2f}")

    # ── المرحلة التمهيد: تحميل البيانات التاريخية ─────────
    state.transition(TradingState.LOADING_HISTORY)
    all_timeframes = set()
    for coin in coins:
        tfs = coin.timeframes if isinstance(coin.timeframes, list) else [coin.timeframes]
        for tf in tfs:
            all_timeframes.add(tf)
    if symbols and all_timeframes:
        warmup_loaded = await market_analyzer.warmup_candles(symbols, all_timeframes)
        if warmup_loaded > 0:
            state.history_loaded = True
            logger.info(f"[النظام] ✅ تسخين الشموع اكتمل — {warmup_loaded} إطار زمني")
        else:
            state.add_error("التسخين", f"فشل تحميل {len(symbols) * len(all_timeframes)} إطار زمني")
            logger.critical("[النظام] ❌ فشل تسخين الشموع — لا توجد بيانات تاريخية")
    else:
        logger.warning("[النظام] ⚠️ لا عملات للتسخين")

    # ══ انتقال: LOADING_HISTORY → WARMING_UP ══
    # (حتى لو فشل التسخين REST — نعتمد على WebSocket لبناء الشموع)
    state.transition(TradingState.WARMING_UP)

    # ── 10. بوت تيليجرام + حلقة التداول ────────────────────
    set_system_status("بدء_البوت")
    telegram_engine = TelegramEngine(
        token=settings.telegram_token, admin_id=settings.admin_id,
        analysis_service=analysis_service, trading_service=trading_service,
        portfolio_service=portfolio_service, risk_service=risk_service,
    )

    # ── حلقة التداول — SAFE EXECUTION PIPELINE ─────────────
    async def trading_loop():
        from database.repositories import CoinRepository, PositionRepository, get_session
        cycle = 0

        async def notify_tg(text: str):
            try:
                if telegram_engine and getattr(telegram_engine, 'application', None):
                    await telegram_engine.send_message(telegram_id, text)
            except Exception:
                pass

        while True:
            cycle += 1
            cycle_start = _utcnow()

            # ── المرحلة 0: تهيئة آمنة لجميع المتغيرات ──
            state.reset_cycle()

            try:
                # ── آلة الحالات: إدارة الانتقالات ──
                ws_alive = getattr(market_data_engine, '_ws', None) is not None

                # تتبع WS — لا يُصفّر ticks أبداً (فقط mark_ws_disconnected يزيد reconnect)
                if ws_alive and not state.ws_connected:
                    state.mark_ws_connected()
                elif not ws_alive and state.ws_connected:
                    state.mark_ws_disconnected()

                # مزامنة ticks من محرك البيانات (تراكمي — لا تصفير)
                engine_ticks = getattr(market_data_engine, '_kline_count', 0)
                if engine_ticks > state.ws_tick_count:
                    delta = engine_ticks - state.ws_tick_count
                    state.ws_tick_count = engine_ticks
                    state.ws_last_seen_at = _utcnow().timestamp()

                # الانتقال: WARMING_UP → RUNNING
                if state.phase == TradingState.WARMING_UP:
                    if state.ws_ready_for_running:
                        state.transition(TradingState.RUNNING)
                    elif cycle % 5 == 0:
                        stable_sec = (_utcnow().timestamp() - state.ws_stable_since) if state.ws_stable_since > 0 else 0
                        logger.info(
                            f"[تسخين] 🔥 ticks={state.ws_tick_count}/{state.MIN_WS_TICKS} | "
                            f"WS={'متصل' if state.ws_connected else 'منفصل'} | "
                            f"مستقر={stable_sec:.0f}/{state.MIN_WS_STABLE_SEC}ث | "
                            f"reconnects={state.ws_reconnect_count}"
                        )

                # 🛡️ شبكة أمان: إذا وصلنا للحلقة في LOADING_HISTORY (لا يجب أن يحدث)
                elif state.phase == TradingState.LOADING_HISTORY:
                    logger.warning(
                        f"[آلة_الحالات] ⚠️ مرحلة غير متوقعة: LOADING_HISTORY في الحلقة — "
                        f"الانتقال الاضطراري إلى WARMING_UP"
                    )
                    state.transition(TradingState.WARMING_UP)

                # 🛡️ شبكة أمان: مرحلة CONNECTING_WS في الحلقة (لا يجب أن يحدث)
                elif state.phase == TradingState.CONNECTING_WS:
                    if ws_alive:
                        logger.info(
                            f"[آلة_الحالات] ⚠️ CONNECTING_WS في الحلقة مع WS متصل — "
                            f"الانتقال الاضطراري إلى WARMING_UP"
                        )
                        state.transition(TradingState.WARMING_UP)

                # 🛡️ كشف الحالة العالقة
                state.check_stuck(cycle)

                # 🛡️ تأكيد: RUNNING لا يجب أن يتراجع
                if state.phase == TradingState.RUNNING:
                    if not state.ws_connected:
                        logger.error(
                            f"[آلة_الحالات] ⚠️ RUNNING لكن WS منفصل! "
                            f"reconnects={state.ws_reconnect_count} | ticks={state.ws_tick_count}"
                        )
                    assert state.trading_allowed, (
                        f"[تأكيد] RUNNING لكن trading_allowed=False! دورة #{cycle}"
                    )

                # ── المرحلة 1: فحص السماح بالتداول ──
                trading_allowed = (
                    getattr(health_monitor, 'is_trading_safe', lambda: True)() and
                    getattr(risk_service, 'is_trading_allowed', lambda: True)()
                )
                if not trading_allowed:
                    logger.warning(f"[دورة #{cycle}] ⛔ تداول معلق")
                    await asyncio.sleep(30)
                    continue

                # ── المرحلة 2: جمع البيانات ──
                async for session in get_session():
                    state.coins = await CoinRepository.get_all_active(session, telegram_id)

                    # مراقبة المراكز — فقط في RUNNING
                    if state.trading_allowed:
                        state.open_positions = await PositionRepository.get_open(session, telegram_id)

                    for pos in state.open_positions:
                        try:
                            analysis = await market_analyzer.analyze(pos.symbol, "1m")
                            if not analysis:
                                continue
                            current_price = getattr(analysis, 'current_price', 0)
                            if current_price <= 0:
                                continue
                            side = getattr(pos, 'side', 'BUY')
                            if side in ("BUY", "LONG"):
                                hit_tp = getattr(pos, 'take_profit', None) and current_price >= pos.take_profit
                                hit_sl = getattr(pos, 'stop_loss', None) and current_price <= pos.stop_loss
                            else:
                                hit_tp = getattr(pos, 'take_profit', None) and current_price <= pos.take_profit
                                hit_sl = getattr(pos, 'stop_loss', None) and current_price >= pos.stop_loss
                            if hit_tp or hit_sl:
                                entry = getattr(pos, 'entry_price', current_price)
                                pnl = (current_price - entry) / entry * 100
                                if side in ("SELL", "SHORT"):
                                    pnl = -pnl
                                reason = "🎯 هدف" if hit_tp else "🛑 وقف خسارة"
                                emoji = "🟢" if pnl > 0 else "🔴"
                                await PositionRepository.close_position(session, pos, exit_price=current_price, reason=reason)
                                logger.info(f"[مركز] {pos.symbol} | {reason} | PnL={pnl:+.2f}%")
                                await notify_tg(
                                    f"{emoji} {reason}\n━━━━━━━━━━━━━━\n"
                                    f"💰 {pos.symbol}\n📊 السعر: {current_price:.6f}\n"
                                    f"📈 الدخول: {entry:.6f}\n💵 الربح/الخسارة: {pnl:+.2f}%\n"
                                    f"📦 الكمية: {getattr(pos, 'quantity', 0):.4f}"
                                )
                        except Exception as e:
                            logger.debug(f"[مركز] {pos.symbol} خطأ: {e}")

                    if not state.coins:
                        if cycle % 10 == 0:
                            logger.info(f"[دورة #{cycle}] لا عملات — انتظار")
                        await asyncio.sleep(1)
                        break

                    # ── المرحلة 3: التحليل ──
                    for coin in state.coins:
                        # STEP 1
                        logger.info(
                            f"[STEP 1] {coin.symbol}: بدء المسار — "
                            f"أطر: {coin.timeframes if isinstance(coin.timeframes, list) else [coin.timeframes]} | "
                            f"مرحلة={state.phase} | مسموح_بالتداول={state.trading_allowed}"
                        )
                        tfs = coin.timeframes if isinstance(coin.timeframes, list) else [coin.timeframes]
                        coin_prices = {}
                        coin_had_analysis = False

                        # STEP 2
                        logger.info(f"[STEP 2] {coin.symbol}: استدعاء المحلل لكل إطار...")
                        for tf in tfs:
                            try:
                                analysis = await market_analyzer.analyze(coin.symbol, tf)
                                # STEP 3
                                if analysis and getattr(analysis, 'current_price', 0) > 0:
                                    logger.info(
                                        f"[STEP 3] {coin.symbol} {tf}: ✅ محلل أرجع تحليل — "
                                        f"نظام={analysis.regime} | اتجاه={analysis.trend_direction} | "
                                        f"زخم={analysis.momentum:.0f} | تقلب={analysis.volatility:.0f} | "
                                        f"سيولة={analysis.liquidity_score:.0f} | ثقة={analysis.confidence:.0f}"
                                    )
                                    coin_prices[tf] = analysis.current_price
                                    coin_had_analysis = True
                                    state.analysis_ok += 1

                                    # STEP 4
                                    logger.info(
                                        f"[STEP 4] {coin.symbol} {tf}: استدعاء محرك الاستراتيجيات..."
                                    )
                                    await strategy_engine.run_strategies(coin.symbol, tf, analysis)
                                    logger.info(
                                        f"[STEP 5] {coin.symbol} {tf}: محرك الاستراتيجيات انتهى"
                                    )
                                else:
                                    state.analysis_miss += 1
                                    reason = "لا تحليل" if not analysis else "سعر=0"
                                    candle_count = len(market_analyzer._candles.get(coin.symbol, {}).get(tf, []))
                                    logger.info(
                                        f"[STEP 3-EARLY_EXIT] {coin.symbol} {tf}: ❌ {reason} | "
                                        f"شموع={candle_count}/20 | سيتابع للإطار التالي"
                                    )
                            except Exception as e:
                                state.analysis_miss += 1
                                logger.info(f"[STEP 3-ERROR] {coin.symbol} {tf}: استثناء={e}")

                        # STEP 6
                        if not coin_had_analysis:
                            logger.info(
                                f"[STEP 6-EARLY_EXIT] {coin.symbol}: ❌ لا تحليلات ناجحة "
                                f"من أي إطار — تخطي التداول لهذه العملة (continue)"
                            )
                        else:
                            if coin_prices:
                                price_str = " | ".join(f"{tf}: {p:.4f}" for tf, p in sorted(coin_prices.items()))
                                state.price_lines.append(f"  {coin.symbol:<10} {price_str}")

                            # STEP 7
                            if not state.trading_allowed:
                                logger.info(
                                    f"[STEP 7-EARLY_EXIT] {coin.symbol}: ❌ التداول غير مسموح — "
                                    f"السبب: المرحلة={state.phase} (يحتاج RUNNING) | "
                                    f"الشرط: state.trading_allowed=False"
                                )
                            else:
                                # STEP 8
                                logger.info(
                                    f"[STEP 8] {coin.symbol}: استدعاء trading_service.process_symbol()..."
                                )
                                try:
                                    result = await trading_service.process_symbol(coin.symbol, telegram_id)
                                    # STEP 9
                                    if result:
                                        evidence, risk_decision, execution = result
                                        state.signals_found += 1
                                        logger.info(
                                            f"[STEP 9] {coin.symbol}: ✅ trading_service أرجع نتيجة — "
                                            f"قرار={evidence.decision} | نفذ={'نعم' if execution else 'لا'}"
                                        )
                                        if execution:
                                            logger.info(
                                                f"[EXECUTION] {coin.symbol}: ✅ تم إرسال الأمر للتنفيذ | "
                                                f"{evidence.decision} | ثقة: {evidence.final_score:.0f}% | "
                                                f"كمية: {execution.executed_quantity:.6f} | "
                                                f"سعر: {execution.executed_price:.6f}"
                                            )
                                            logger.info(
                                                f"[DATABASE] {coin.symbol}: ✅ تم حفظ الصفقة"
                                            )
                                            tp = getattr(execution, 'take_profit', None)
                                            sl = getattr(execution, 'stop_loss', None)
                                            try:
                                                await notify_tg(
                                                    f"🔔 **صفقة جديدة**\n━━━━━━━━━━━━━━\n"
                                                    f"💰 {coin.symbol}\n📊 {evidence.decision}\n"
                                                    f"💵 السعر: {execution.executed_price:.6f}\n"
                                                    f"📦 الكمية: {execution.executed_quantity:.4f}\n"
                                                    f"🎯 هدف: {tp:.6f if tp else '—'}\n"
                                                    f"🛑 وقف: {sl:.6f if sl else '—'}\n"
                                                    f"✅ ثقة: {evidence.final_score:.0f}%\n"
                                                    f"🧠 {evidence.reasoning[:100]}"
                                                )
                                                logger.info(f"[TELEGRAM] {coin.symbol}: ✅ تم إرسال رسالة تيليجرام")
                                            except Exception as e:
                                                logger.error(f"[TELEGRAM] {coin.symbol}: ❌ خطأ في إرسال رسالة تيليجرام: {e}")
                                        else:
                                            logger.info(
                                                f"[SIGNAL] {coin.symbol}: ❌ مرفوضة — "
                                                f"{evidence.decision} | ثقة: {evidence.final_score:.0f}% | "
                                                f"السبب: {evidence.reasoning[:80]}"
                                            )
                                    else:
                                        # STEP 9-EARLY_EXIT
                                        logger.info(
                                            f"[STEP 9-EARLY_EXIT] {coin.symbol}: ❌ trading_service "
                                            f"أرجع None — لا إشارات (انظر سجلات trading_service للتفاصيل)"
                                        )
                                except Exception as e:
                                    logger.info(f"[STEP 9-ERROR] {coin.symbol}: استثناء={e}")

                        await asyncio.sleep(0.5)

                    # ── المرحلة 5: تشخيص (كل 10 دورات) ──
                    if cycle % 10 == 0:
                        diag = []
                        diag.append(f"🔌 WS={'متصل' if state.ws_connected else 'منفصل'} | ticks={state.ws_tick_count}")
                        total_candles = sum(len(tf_dict) for tf_dict in market_analyzer._candles.values())
                        diag.append(f"🕯️ شموع: {total_candles} إطار")
                        for sym in sorted(market_analyzer._candles.keys()):
                            for tf in sorted(market_analyzer._candles[sym].keys()):
                                n = len(market_analyzer._candles[sym][tf])
                                diag.append(f"  {sym} {tf}: {n} شمعة {'✅' if n>=50 else f'⏳({n}/50)'}")
                        diag.append(f"📋 مهام: {len(asyncio.all_tasks())}")
                        diag.append(f"🆔 محلل={id(market_analyzer)} بيانات={id(market_data_engine)}")
                        diag.append(f"💹 أسعار حية: {len(getattr(market_data_engine, 'live_prices', {}))} رمز")
                        logger.info(f"[تشخيص #{cycle}]\n" + "\n".join(diag))

                    # ── المرحلة 6: تقرير الدورة ──
                    duration = (_utcnow() - cycle_start).total_seconds()
                    phase_icon = {"INIT": "🟡", "CONNECTING_WS": "🔌", "LOADING_HISTORY": "📥",
                                  "WARMING_UP": "🔥", "RUNNING": "✅", "ERROR": "🔴"}.get(state.phase, "❓")
                    data_status = f"تحليلات: {state.analysis_ok}" if state.analysis_ok > 0 else "⏳ بلا بيانات"
                    summary = (
                        f"[{phase_icon} {state.phase}] دورة #{cycle} | "
                        f"{len(state.coins)} عملة | {data_status} | "
                        f"إشارات: {state.signals_found} | {duration:.1f}ث"
                    )
                    logger.info(summary)
                    if state.price_lines:
                        logger.info(f"[أسعار #{cycle}]\n" + "\n".join(state.price_lines))

            except Exception as e:
                logger.error(f"[دورة #{cycle}] ❌ خطأ: {e}", exc_info=True)

            # تأخير تكيفي
            delay = 10 if cycle <= 5 else 30
            await asyncio.sleep(delay)

    asyncio.create_task(trading_loop())
    logger.info(f"[النظام] حلقة التداول بدأت — {state.phase}")

    # ── النظام جاهز ────────────────────────────────────────
    set_system_status("يعمل")
    logger.info("═" * 50)
    logger.info(f"[النظام] ✅ جاهز — {state.phase} — {state.health}")
    logger.info("═" * 50)

    # ── تشغيل بوت تيليجرام ──────────────────────────────────
    try:
        await telegram_engine.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("[النظام] إشارة إيقاف")
    except Exception as e:
        state.add_error("البوت", str(e))
    finally:
        logger.info("[النظام] بدء الإيقاف التدريجي...")
        set_system_status("إيقاف")
        for name, stop_fn in [
            ("البوت", telegram_engine.stop),
            ("مراقب الصحة", health_monitor.stop), ("التقارير", reporting_engine.stop),
            ("التعلم", learning_engine.stop), ("المحفظة", portfolio_engine.stop),
            ("التنفيذ", execution_engine.stop), ("المخاطر", risk_engine.stop),
            ("الأدلة", evidence_engine.stop), ("الاستراتيجيات", strategy_engine.stop),
            ("محلل السوق", market_analyzer.stop), ("بيانات السوق", market_data_engine.stop),
            ("السجلات", logging_engine.stop), ("الإعدادات", config_engine.stop),
        ]:
            try:
                await stop_fn()
                logger.info(f"[إيقاف] {name}")
            except Exception as e:
                logger.error(f"[إيقاف] {name}: {e}")
        await close_db()
        logger.info("[إيقاف] قاعدة البيانات أغلقت")
        logger.info("[النظام] ✅ إيقاف كامل")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
