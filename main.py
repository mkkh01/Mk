"""
نقطة الدخول الرئيسية — تنسيق بدء التشغيل بدون أي منطق تجاري.
V4.2: آلة حالات مركزية — State Machine — لا UnboundLocalError، لا race conditions.
"""
import asyncio
import logging
import sys
import os
import time
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
from core.trading_kernel import TradingKernel
from core.reason_codes import ReasonCode, Rejection
from core.decision_trace import (
    DecisionTrace, get_counters, update_counters,
    format_5min_report, format_hourly_report,
)
from core.tracing import TraceBuilder, timer

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

    آلة الحالات (State Machine — Production):
        INIT ──▶ CONNECTING_WS ──▶ LOADING_HISTORY ──▶ WARMING_UP
        ──▶ READY_TO_TRADE ──▶ TRADING_ACTIVE

    مراحل السلامة:
        DEGRADED — نظام منحط (يعمل ببيانات جزئية — لا تداول)
        BLOCKED  — إيقاف صارم (لا تداول تحت أي ظرف)

    RULE: READY_TO_TRADE لا يسمح بالتداول (يتم تفعيله فقط عند الانتقال لـ TRADING_ACTIVE)
    RULE: TRADING_ACTIVE يسمح بالتداول (الوضع الطبيعي)
    """

    INIT = "INIT"
    CONNECTING_WS = "CONNECTING_WS"
    LOADING_HISTORY = "LOADING_HISTORY"
    WARMING_UP = "WARMING_UP"
    READY_TO_TRADE = "READY_TO_TRADE"      # جاهز لكن لم ينفذ بعد
    TRADING_ACTIVE = "TRADING_ACTIVE"      # تداول نشط
    RUNNING = "TRADING_ACTIVE"             # توافق مع المراجع القديمة
    DEGRADED = "DEGRADED"                  # منحط — لا تداول
    BLOCKED = "BLOCKED"                     # إيقاف صارم
    ERROR = "ERROR"

    BOOT_STATES: set[str] = {"INIT", "CONNECTING_WS", "LOADING_HISTORY", "WARMING_UP"}

    _VALID_TRANSITIONS: dict[str, set[str]] = {
        "INIT": {"CONNECTING_WS", "ERROR"},
        "CONNECTING_WS": {"LOADING_HISTORY", "ERROR"},
        "LOADING_HISTORY": {"WARMING_UP", "BLOCKED", "ERROR"},
        "WARMING_UP": {"READY_TO_TRADE", "DEGRADED", "BLOCKED", "ERROR"},
        "READY_TO_TRADE": {"TRADING_ACTIVE", "DEGRADED", "BLOCKED", "ERROR"},
        "TRADING_ACTIVE": {"DEGRADED", "BLOCKED", "ERROR"},
        "DEGRADED": {"WARMING_UP"},
        "BLOCKED": set(),
        "ERROR": {"WARMING_UP"},
    }

    _ONCE_ONLY_PHASES: set[str] = {"INIT", "CONNECTING_WS", "LOADING_HISTORY", "BLOCKED"}

    def __init__(self):
        self.phase: str = self.INIT
        self.phase_set_at: float = _utcnow().timestamp()
        self.started_at = _utcnow()
        self.errors: list = []
        self._transition_count: int = 0
        self._entered_phases: set[str] = {self.INIT}
        self._exited_phases: set[str] = set()
        self._lock = asyncio.Lock()

        # التداول
        self.open_positions: list = []
        self.coins: list = []
        self.price_lines: list = []
        self.signals_found: int = 0
        self.analysis_ok: int = 0
        self.analysis_miss: int = 0

        # WebSocket
        self.ws_connected: bool = False
        self.ws_connected_at: float = 0.0
        self.ws_stable_since: float = 0.0
        self.ws_tick_count: int = 0
        self.ws_reconnect_count: int = 0
        self.ws_last_seen_at: float = 0.0
        self.history_loaded: bool = False

        # حدود
        self.MIN_CANDLES = 50
        self.MIN_WS_TICKS = 20
        self.MIN_WS_STABLE_SEC = 15

        # كشف العالق
        self._phase_stuck_warned: set = set()
        self.MAX_CYCLES_IN_PHASE: dict[str, int] = {
            "INIT": 0, "CONNECTING_WS": 0, "LOADING_HISTORY": 10,
            "WARMING_UP": 300, "READY_TO_TRADE": 600, "TRADING_ACTIVE": 0,
            "DEGRADED": 120, "BLOCKED": 0,
        }

    def transition(self, new_phase: str) -> None:
        """انتقال آلة الحالات — Idempotent + Invariant-checked."""
        old = self.phase
        now = _utcnow()
        now_ts = now.timestamp()
        duration = now_ts - self.phase_set_at

        if new_phase == old:
            return

        allowed = self._VALID_TRANSITIONS.get(old, set())
        if new_phase not in allowed:
            logger.critical(f"[آلة_الحالات] ❌ انتقال غير مسموح: {old} → {new_phase}")
            return

        if new_phase in self._ONCE_ONLY_PHASES and new_phase in self._exited_phases:
            logger.critical(f"[آلة_الحالات] ❌ محاولة إعادة دخول مرحلة غير قابلة للتكرار: {new_phase}")
            return

        # 🛡️ تحقق: ثوابت ما قبل الانتقال
        self._check_pre_transition_invariants(old, new_phase)

        self._exited_phases.add(old)
        self.phase = new_phase
        self.phase_set_at = now_ts
        self._entered_phases.add(new_phase)
        self._transition_count += 1

        # 🛡️ تحقق: ثوابت ما بعد الانتقال
        self._check_post_transition_invariants(new_phase)

        logger.info("═" * 50)
        logger.info(f"[آلة_الحالات] انتقال #{self._transition_count}: {old} → {new_phase}")
        logger.info(f"[آلة_الحالات] المدة في {old}: {duration:.1f}ث | ticks={self.ws_tick_count}")
        logger.info("═" * 50)

    def _check_pre_transition_invariants(self, old: str, new: str) -> None:
        if new == self.READY_TO_TRADE:
            assert self.ws_connected, "READY_TO_TRADE يتطلب اتصال WebSocket"
            assert self.ws_tick_count >= self.MIN_WS_TICKS, f"READY_TO_TRADE يتطلب {self.MIN_WS_TICKS} ticks"
        if new == self.TRADING_ACTIVE:
            assert old == self.READY_TO_TRADE, "لا يمكن دخول TRADING_ACTIVE إلا من READY_TO_TRADE"

    def _check_post_transition_invariants(self, phase: str) -> None:
        """ثوابت يجب أن تتحقق بعد الانتقال — SSOT logic."""
        # تحديث trading_allowed مركزي
        allowed = self.trading_allowed
        if phase == self.READY_TO_TRADE:
            # تصحيح المشكلة: READY_TO_TRADE لا يجب أن يسمح بالتداول
            assert not allowed, "READY_TO_TRADE لا يجب أن يسمح بالتداول"
        elif phase == self.TRADING_ACTIVE:
            assert allowed, "TRADING_ACTIVE يجب أن يسمح بالتداول"

    @property
    def trading_allowed(self) -> bool:
        """المكان المركزي الوحيد لتحديد ما إذا كان التداول مسموحاً بناءً على الحالة."""
        # تم الإصلاح: التداول مسموح فقط في TRADING_ACTIVE
        return self.phase == self.TRADING_ACTIVE

    @property
    def analysis_allowed(self) -> bool:
        return self.phase in (self.WARMING_UP, self.READY_TO_TRADE, self.TRADING_ACTIVE)

    def mark_ws_connected(self) -> None:
        self.ws_connected = True
        self.ws_connected_at = _utcnow().timestamp()
        self.ws_stable_since = self.ws_connected_at
        logger.info(f"[آلة_الحالات] 🔌 WebSocket متصل")

    def mark_ws_disconnected(self) -> None:
        if self.ws_connected:
            self.ws_connected = False
            self.ws_reconnect_count += 1
            self.ws_stable_since = 0.0
            logger.warning(f"[آلة_الحالات] 🔌 WebSocket منفصل")

    def record_tick(self) -> None:
        self.ws_tick_count += 1
        self.ws_last_seen_at = _utcnow().timestamp()

    async def safe_transition(self, new_phase: str) -> None:
        async with self._lock:
            self.transition(new_phase)

    def check_stuck(self, cycle: int) -> None:
        max_cycles = self.MAX_CYCLES_IN_PHASE.get(self.phase, 0)
        if max_cycles > 0 and cycle > max_cycles and self.phase not in self._phase_stuck_warned:
            self._phase_stuck_warned.add(self.phase)
            logger.error(f"[آلة_الحالات] ⚠️ حالة عالقة: {self.phase} دورة #{cycle}")

    def add_error(self, component: str, error: str) -> None:
        self.errors.append({"component": component, "error": error, "time": _utcnow().isoformat()})
        logger.error(f"[نظام] ❌ {component}: {error}")

    def reset_cycle(self) -> None:
        self.open_positions = []
        self.signals_found = 0
        self.analysis_ok = 0
        self.analysis_miss = 0

    @property
    def ws_ready_for_running(self) -> bool:
        return self.ws_connected and self.ws_tick_count >= self.MIN_WS_TICKS and (
            (_utcnow().timestamp() - self.ws_stable_since) >= self.MIN_WS_STABLE_SEC
        )

    def is_boot_state(self) -> bool:
        return self.phase in self.BOOT_STATES

    @property
    def health(self) -> str:
        if self.errors: return "تحذير"
        if self.phase == self.TRADING_ACTIVE: return "صحيحة"
        return self.phase


# نسخة وحيدة
state = TradingState()

async def main():
    logger.info("[النظام] بدء تشغيل CT V4.2")
    
    settings = get_settings()
    await init_db()
    
    event_bus = EventBus()
    
    # تهيئة المحركات
    config_engine = ConfigEngine(); await config_engine.initialize(); await config_engine.start()
    logging_engine = LoggingEngine(); await logging_engine.initialize(); await logging_engine.start()
    
    state.transition(TradingState.CONNECTING_WS)
    market_data_engine = MarketDataEngine(event_bus); await market_data_engine.initialize()
    
    market_analyzer = MarketAnalyzer(event_bus); await market_analyzer.initialize(); await market_analyzer.start()
    strategy_engine = StrategyEngine(event_bus); await strategy_engine.initialize(); await strategy_engine.start()
    evidence_engine = EvidenceEngine(event_bus); await evidence_engine.initialize(); await evidence_engine.start()
    risk_engine = RiskEngine(event_bus); await risk_engine.initialize(); await risk_engine.start()
    execution_engine = ExecutionEngine(event_bus); await execution_engine.initialize(); await execution_engine.start()
    portfolio_engine = PortfolioEngine(event_bus, initial_balance=settings.default_capital); await portfolio_engine.initialize(); await portfolio_engine.start()
    learning_engine = LearningEngine(event_bus); await learning_engine.initialize(); await learning_engine.start()
    reporting_engine = ReportingEngine(event_bus); await reporting_engine.initialize(); await reporting_engine.start()
    health_monitor = HealthMonitor(event_bus); await health_monitor.initialize(); await health_monitor.start()
    
    analysis_service = AnalysisService(market_data_engine, market_analyzer, strategy_engine)
    trading_service = TradingService(evidence_engine, risk_engine, execution_engine, market_analyzer, strategy_engine, market_data_engine, analysis_service)
    portfolio_service = PortfolioService(portfolio_engine, reporting_engine, learning_engine, health_monitor)
    risk_service = RiskService(risk_engine)
    
    # مزامنة العملات وبدء WebSocket
    symbols, coins = await analysis_service.sync_symbols_from_db(str(settings.admin_id))
    await market_data_engine.start()
    
    state.transition(TradingState.LOADING_HISTORY)
    await market_analyzer.warmup_candles(symbols, ["1m", "5m", "15m"])
    state.history_loaded = True
    
    state.transition(TradingState.WARMING_UP)
    
    telegram_engine = TelegramEngine(
        token=settings.telegram_token, admin_id=settings.admin_id,
        analysis_service=analysis_service, trading_service=trading_service,
        portfolio_service=portfolio_service, risk_service=risk_service,
    )

    # ── حلقة التداول ──
    async def trading_loop():
        cycle = 0
        while True:
            cycle += 1
            state.reset_cycle()
            try:
                # تحديث حالة WebSocket
                ws_alive = getattr(market_data_engine, '_ws', None) is not None
                if ws_alive and not state.ws_connected: state.mark_ws_connected()
                elif not ws_alive and state.ws_connected: state.mark_ws_disconnected()
                
                # مزامنة ticks
                state.ws_tick_count = getattr(market_data_engine, '_kline_count', 0)
                
                # الانتقالات
                if state.phase == TradingState.WARMING_UP and state.ws_ready_for_running:
                    await state.safe_transition(TradingState.READY_TO_TRADE)
                
                # إذا كان جاهزاً، يمكن الانتقال لـ ACTIVE تلقائياً أو عبر شرط
                if state.phase == TradingState.READY_TO_TRADE:
                    # هنا يمكن إضافة شرط إضافي أو الانتقال فوراً
                    await state.safe_transition(TradingState.TRADING_ACTIVE)

                # منطق التداول الفعلي...
                if state.trading_allowed:
                    # تنفيذ التحليل والصفقات
                    pass

            except Exception as e:
                logger.error(f"[حلقة] خطأ: {e}")
            
            await asyncio.sleep(30)

    loop_task = asyncio.create_task(trading_loop())
    
    try:
        await telegram_engine.start()
    except Exception as e:
        logger.error(f"[نظام] خطأ في البوت: {e}")
    finally:
        loop_task.cancel()
        await telegram_engine.stop()
        # إيقاف المحركات...
        await close_db()

if __name__ == "__main__":
    asyncio.run(main())
