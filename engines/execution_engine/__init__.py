"""
محرك التنفيذ — المكوّن الوحيد المسموح له بفتح، تعديل، أو إغلاق الصفقات.
لا يحلل السوق ولا يتخذ قرارات التداول — فقط ينفذ القرارات المعتمدة.
Simulation mode فقط. لا اتصال بمنصة تداول حقيقية.

جميع عمليات قاعدة البيانات تمر عبر Repository layer (يتولى تحويل telegram_id → UUID).
"""
import asyncio
import logging
from datetime import datetime
import uuid

from core.base import BaseEngine
from core.events import (
    RiskEvent, ExecutionEvent, EvidenceEvent, EventBus,
    HealthEvent, HealthStatus, AlertEvent, AlertLevel
)
from core.types import ExecutionResult
from core.errors import ExecutionError
from database.repositories import (
    TradeRepository, PositionRepository, UserRepository, get_session
)
from database.models import Trade, Position
from config.constants import TRADE_FEE

logger = logging.getLogger("execution_engine")


# ═══════════════════════════════════════════════════════════════
#  حالة المحرك
# ═══════════════════════════════════════════════════════════════

class ExecutionState:
    """حالات محرك التنفيذ (بالعربية — للعرض الداخلي فقط)."""
    خامل = "خامل"
    نشط = "نشط"
    متوقف = "متوقف"


class ExecutionEngine(BaseEngine):
    """ينفذ الصفقات المعتمدة. Simulation mode فقط. لا يوجد وضع حقيقي."""

    def __init__(self, event_bus: EventBus):
        super().__init__("execution_engine")
        self.event_bus = event_bus
        # لا يوجد simulation_mode متغير — دائماً محاكاة
        self._pending_orders: dict[str, dict] = {}
        self._execution_count: int = 0
        self._errors_count: int = 0
        self._telegram_id: int = 0
        self._state: str = ExecutionState.خامل
        # سجل تنفيذي للتدقيق
        self._execution_log: list[dict] = []

    # ═════════════════════════════════════════════════════════
    #  دورة الحياة
    # ═════════════════════════════════════════════════════════

    async def initialize(self) -> None:
        await self.event_bus.subscribe("RiskEvent", self._on_risk_approval)
        self._state = ExecutionState.خامل
        self.logger.info("[تنفيذ] تم التهيئة — وضع المحاكاة فقط.")

    async def start(self) -> None:
        self._running = True
        self._state = ExecutionState.نشط
        asyncio.create_task(self._heartbeat_loop())
        self.logger.info("[تنفيذ] بدأ العمل.")

    async def stop(self) -> None:
        self._running = False
        self._state = ExecutionState.متوقف
        self.logger.info("[تنفيذ] توقف.")

    # ═════════════════════════════════════════════════════════
    #  معالجة الأحداث
    # ═════════════════════════════════════════════════════════

    async def _on_risk_approval(self, event: RiskEvent):
        """استقبال موافقة محرك المخاطر وتنفيذ الصفقة."""
        if not self._running or not event.trade_allowed:
            return
        await self.execute(event)

    # ═════════════════════════════════════════════════════════
    #  التنفيذ (محاكاة فقط)
    # ═════════════════════════════════════════════════════════

    async def execute(self, risk, symbol: str = "",
                      entry_price: float = 0.0, strategy: str = "غير معروف",
                      telegram_id: int = 0, entry_reason: str = "",
                      side: str = "BUY") -> ExecutionResult:
        """
        تنفيذ صفقة بشكل محاكي (بدون اتصال بمنصة حقيقية).
        يسجل الصفقة عبر Repository layer الذي يتولى تحويل telegram_id → UUID.

        المعاملات:
            telegram_id: معرف تليجرام الخاص بالمستخدم (int). الـ Repository يحوله لـ UUID.
        """
        order_id = str(uuid.uuid4())[:8]
        slippage = entry_price * 0.001 if entry_price > 0 else 0.0
        executed_price = entry_price + slippage
        fees = executed_price * risk.position_size * TRADE_FEE

        tid = telegram_id or self._telegram_id

        # حساب Stop Loss و Take Profit
        sl_dist = getattr(risk, 'stop_loss_distance', 0) or executed_price * 0.02
        tp_dist = sl_dist * (getattr(risk, 'take_profit_ratio', 0) or 2.0)
        sl_price = executed_price - sl_dist
        tp_price = executed_price + tp_dist

        result = ExecutionResult(
            order_id=order_id,
            symbol=symbol,
            status="FILLED",
            entry_price=round(executed_price, 8),
            executed_quantity=risk.position_size,
            slippage=round(slippage, 8),
            fees=round(fees, 4),
            stop_loss=round(sl_price, 8),
            take_profit=round(tp_price, 8),
            side=side,
        )

        # التسجيل عبر Repository (يتولى تحويل UUID)
        try:
            async for session in get_session():
                # تحويل telegram_id → UUID عبر UserRepository
                user_uuid = await UserRepository.resolve_user_uuid(session, tid)
                self.logger.info(
                    f"[تنفيذ] تحويل الهوية: telegram_id={tid} → uuid={user_uuid[:8]}..."
                )

                # إنشاء الصفقة عبر TradeRepository
                trade = await TradeRepository.add(
                    session, tid,
                    symbol=symbol,
                    side=side,  # ← من معامل الدالة، ليس hardcoded
                    entry_price=executed_price,
                    quantity=risk.position_size,
                    strategy_used=strategy,
                    risk_score=80 if risk.risk_level == "LOW" else 50,
                    confidence_score=75 if risk.position_size > 0 else 50,
                    entry_reason=entry_reason,
                    market_conditions={"risk_level": risk.risk_level},
                    fees=fees,
                )

                # إنشاء المركز عبر PositionRepository (باستخدام sl_dist, tp_dist المحسوبة أعلاه)
                position = await PositionRepository.create(
                    session, tid,
                    symbol=symbol,
                    entry_price=executed_price,
                    quantity=risk.position_size,
                    stop_loss=executed_price - sl_dist,
                    take_profit=executed_price + tp_dist,
                    risk_exposure=risk.position_size * executed_price,
                )

                self.logger.info(
                    f"[تنفيذ] ✅ تم فتح الصفقة: {symbol}"
                )
                self.logger.info(
                    f"[EXECUTION] {symbol}: ✅ تنفيذ Simulation | "
                    f"الأمر=#{order_id} | السعر={executed_price:.6f} | "
                    f"الكمية={risk.position_size:.6f}"
                )
                self.logger.info(
                    f"[DATABASE] {symbol}: ✅ صفقة #{trade.id[:8]} | مركز #{position.id[:8]} | "
                    f"تبريد={getattr(trade, 'cooldown_minutes', '—')}"
                )

        except Exception as e:
            self._errors_count += 1
            self.logger.error(f"[تنفيذ] ❌ خطأ في قاعدة البيانات أثناء التنفيذ: {e}", exc_info=True)

        self._execution_count += 1

        # تسجيل في السجل التنفيذي
        self._execution_log.append({
            "order_id": order_id,
            "symbol": symbol,
            "entry_price": executed_price,
            "quantity": risk.position_size,
            "fee": fees,
            "timestamp": datetime.utcnow().isoformat(),
        })

        # نشر حدث التنفيذ
        await self.event_bus.publish(ExecutionEvent(
            order_id=order_id, symbol=symbol,
            status="FILLED", entry_price=executed_price,
            executed_quantity=risk.position_size,
            slippage=slippage, fees=fees,
        ))

        self.logger.info(
            f"[تنفيذ] {symbol} | الكمية={risk.position_size:.6f} | "
            f"السعر={executed_price:.6f} | الانزلاق={slippage:.6f} | الرسوم={fees:.4f}"
        )

        return result

    # ═════════════════════════════════════════════════════════
    #  إغلاق المركز
    # ═════════════════════════════════════════════════════════

    async def close_position(self, symbol: str, exit_price: float,
                              telegram_id: int, won: bool, exit_reason: str = ""):
        """إغلاق مركز في وضع المحاكاة. يستخدم Repository layer."""
        try:
            async for session in get_session():
                position = await PositionRepository.get_by_symbol(session, telegram_id, symbol)
                trades = await TradeRepository.get_open_trades(session, symbol)

                if position:
                    await PositionRepository.close_position(session, position)
                    self.logger.info(f"[إغلاق] تم إغلاق المركز: {symbol}")

                closed_count = 0
                for trade in trades:
                    status = "WON" if won else "LOST"
                    await TradeRepository.close_trade(session, trade, exit_price, status, exit_reason)
                    closed_count += 1
                    self.logger.info(
                        f"[إغلاق] صفقة مغلقة: {trade.symbol} | النتيجة={status} | "
                        f"سعر الخروج={exit_price}"
                    )

                if closed_count == 0:
                    self.logger.warning(f"[إغلاق] لا توجد صفقات مفتوحة للإغلاق: {symbol}")

        except Exception as e:
            self.logger.error(f"[إغلاق] خطأ أثناء إغلاق المركز {symbol}: {e}", exc_info=True)

    # ═════════════════════════════════════════════════════════
    #  مقاييس وتقارير
    # ═════════════════════════════════════════════════════════

    def get_metrics(self) -> dict:
        return {
            "execution_count": self._execution_count,
            "errors_count": self._errors_count,
            "pending_orders": len(self._pending_orders),
            "state": self._state,
        }

    def get_state(self) -> str:
        """الحالة الديناميكية الحالية للمحرك."""
        if not self._running:
            return ExecutionState.متوقف
        return self._state

    # ═════════════════════════════════════════════════════════
    #  نبض القلب
    # ═════════════════════════════════════════════════════════

    async def _heartbeat_loop(self):
        while self._running:
            await self.event_bus.publish(HealthEvent(
                engine=self.name, status=HealthStatus.HEALTHY,
                latency_ms=0, error_rate=0,
            ))
            await asyncio.sleep(5)
