"""
طبقة المستودعات — كل استعلامات قاعدة البيانات.
كل مستودع يتعامل مع كيان واحد بالضبط. لا يحتوي على منطق أعمال.

مهم: users.id هو UUID. كل الجداول التي ترتبط بـ users.id يجب أن تستخدم UUID،
وليس Telegram ID. UserRepository.resolve_user_uuid() يتولى عملية الربط.
"""
import ssl
import logging
from datetime import datetime
from typing import Optional, List, AsyncGenerator

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select, delete, func, update, and_
from sqlalchemy.orm import selectinload

from config.settings import get_settings
from database.models import (
    Base, User, Coin, MarketData, MarketState, Signal,
    Trade, Position, RiskEvent, PortfolioSnapshot,
    WhaleEvent, NewsEvent, StrategyStat, SystemLog, DecisionTrace,
)

logger = logging.getLogger("database")

# ── إعداد المحرك ────────────────────────────────────────────
_engine = None
_async_session_factory = None


async def init_db() -> None:
    """تهيئة الاتصال بقاعدة البيانات وإنشاء الجداول إن لم تكن موجودة."""
    global _engine, _async_session_factory
    settings = get_settings()
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    _engine = create_async_engine(
        settings.database.url,
        connect_args={
            "ssl": ssl_context,
            "command_timeout": 60,
        },
        pool_pre_ping=True,
        pool_recycle=3600,
    )
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    _async_session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    logger.info("[قاعدة البيانات] تمت التهيئة بنجاح.")


async def close_db() -> None:
    """إغلاق اتصالات قاعدة البيانات."""
    if _engine:
        await _engine.dispose()
        logger.info("[قاعدة البيانات] تم إغلاق الاتصالات.")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """منتج جلِسة async — يُستخدم كاعتماد في محركات النظام."""
    if _async_session_factory is None:
        raise RuntimeError("قاعدة البيانات غير مهيأة. استدعِ init_db() أولاً.")
    async with _async_session_factory() as session:
        yield session


# ═══════════════════════════════════════════════════════════════
#  مستودع المستخدم — المصدر الوحيد للحقيقة لـ UUID المستخدم
# ═══════════════════════════════════════════════════════════════

class UserRepository:
    """إدارة سجلات المستخدمين. يوفر تحويل Telegram ID → UUID."""

    # ذاكرة تخزين مؤقت داخل العملية: telegram_id_str → user_uuid
    _uuid_cache: dict[str, str] = {}

    @classmethod
    def _cache_key(cls, telegram_id) -> str:
        return str(telegram_id)

    @classmethod
    def _cache_get(cls, telegram_id) -> Optional[str]:
        return cls._uuid_cache.get(cls._cache_key(telegram_id))

    @classmethod
    def _cache_set(cls, telegram_id, user_uuid: str):
        cls._uuid_cache[cls._cache_key(telegram_id)] = user_uuid

    @classmethod
    def _cache_clear(cls):
        """مسح الذاكرة المؤقتة لتحويلات Telegram ID → UUID."""
        cls._uuid_cache.clear()

    @staticmethod
    async def resolve_user_uuid(session: AsyncSession, telegram_id) -> str:
        """
        تحويل Telegram ID إلى UUID المستخدم (users.id).
        يُنشئ سجل المستخدم تلقائياً إذا لم يكن موجوداً.
        هذه هي الطريقة الوحيدة التي يجب استخدامها للحصول على user_id صالح
        للمفاتيح الخارجية.

        تُرجع: str — UUID من users.id
        """
        tid = str(telegram_id)

        # ١. التحقق من الذاكرة المؤقتة
        cached = UserRepository._cache_get(tid)
        if cached:
            return cached

        # ٢. الاستعلام من قاعدة البيانات
        logger.info(f"[مصادقة] تحويل telegram_id={tid} → UUID")
        result = await session.execute(
            select(User).where(User.telegram_id == tid)
        )
        user = result.scalars().first()

        if user:
            UserRepository._cache_set(tid, user.id)
            logger.info(f"[مصادقة] المستخدم موجود: uuid={user.id[:8]}...")
            return user.id

        # ٣. إنشاء مستخدم جديد
        logger.info(f"[مصادقة] إنشاء مستخدم جديد: telegram_id={tid}")
        user = User(telegram_id=tid)
        session.add(user)
        await session.commit()
        await session.refresh(user)  # ضمان الحصول على UUID المُنشأ

        UserRepository._cache_set(tid, user.id)
        logger.info(f"[مصادقة] تم إنشاء المستخدم: telegram_id={tid} → uuid={user.id[:8]}...")
        return user.id

    @staticmethod
    async def get_or_create(session: AsyncSession, telegram_id: int) -> User:
        """جلب أو إنشاء مستخدم. تُرجع كائن User كاملاً."""
        uuid_str = await UserRepository.resolve_user_uuid(session, telegram_id)
        result = await session.execute(select(User).where(User.id == uuid_str))
        return result.scalars().one()

    @staticmethod
    async def get_by_telegram_id(session: AsyncSession, telegram_id: int) -> Optional[User]:
        """البحث عن مستخدم بواسطة Telegram ID."""
        result = await session.execute(
            select(User).where(User.telegram_id == str(telegram_id))
        )
        return result.scalars().first()

    @staticmethod
    async def get_by_uuid(session: AsyncSession, user_uuid: str) -> Optional[User]:
        """البحث عن مستخدم بواسطة UUID."""
        result = await session.execute(select(User).where(User.id == user_uuid))
        return result.scalars().first()

    @staticmethod
    async def update_status(session: AsyncSession, user: User,
                            is_active: bool, emergency_stop: bool = False):
        """تحديث حالة المستخدم (نشط/موقوف)."""
        user.is_active = is_active
        user.emergency_stop = emergency_stop
        await session.commit()
        logger.info(
            f"[مصادقة] تم تحديث حالة المستخدم {user.telegram_id}: "
            f"نشط={is_active} توقف_طارئ={emergency_stop}"
        )


# ═══════════════════════════════════════════════════════════════
#  مستودع العملات
# ═══════════════════════════════════════════════════════════════

class CoinRepository:
    """إدارة إعدادات العملات للمستخدمين. V4.0: timeframes أصبح JSON."""

    @staticmethod
    async def _resolve_user_id(session: AsyncSession, identifier) -> str:
        """
        تحويل معرّف المستخدم (Telegram ID أو UUID) إلى UUID.
        يقبل: int (telegram_id)، str (telegram_id أو UUID)، أو كائن User.
        """
        if hasattr(identifier, 'id'):  # كائن User
            return identifier.id
        try:
            # المحاولة كـ UUID أولاً (36 حرفاً مع شرطات)
            sid = str(identifier)
            if len(sid) == 36 and sid.count('-') == 4:
                return sid
        except Exception:
            pass
        # التعامل كـ telegram_id
        return await UserRepository.resolve_user_uuid(session, identifier)

    @staticmethod
    async def get_all_active(session: AsyncSession, identifier) -> List[Coin]:
        """جلب كل العملات النشطة للمستخدم."""
        user_uuid = await CoinRepository._resolve_user_id(session, identifier)
        result = await session.execute(
            select(Coin).where(and_(Coin.user_id == user_uuid, Coin.is_active == True))
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_all(session: AsyncSession, identifier) -> List[Coin]:
        """جلب كل العملات (بما فيها غير النشطة) للمستخدم."""
        user_uuid = await CoinRepository._resolve_user_id(session, identifier)
        result = await session.execute(
            select(Coin).where(Coin.user_id == user_uuid)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_by_symbol(session: AsyncSession, identifier, symbol: str) -> Optional[Coin]:
        """جلب إعدادات عملة محددة بالرمز للمستخدم."""
        user_uuid = await CoinRepository._resolve_user_id(session, identifier)
        result = await session.execute(
            select(Coin).where(and_(Coin.user_id == user_uuid, Coin.symbol == symbol))
        )
        return result.scalars().first()

    @staticmethod
    async def get_active_timeframes(session: AsyncSession, identifier, symbol: str) -> List[str]:
        """
        جلب قائمة الأطر الزمنية النشطة لعملة محددة.
        V4.0: تُرجع القائمة من حقل `timeframes` (JSON) بعد التحقق من أن العملة نشطة.

        تُرجع: List[str] — مثال: ["15m", "1h", "4h"]
        """
        user_uuid = await CoinRepository._resolve_user_id(session, identifier)
        result = await session.execute(
            select(Coin.timeframes).where(
                and_(
                    Coin.user_id == user_uuid,
                    Coin.symbol == symbol,
                    Coin.is_active == True
                )
            )
        )
        timeframes = result.scalars().first()
        if timeframes is None:
            logger.debug(f"[عملات] لم يتم العثور على عملة نشطة: {symbol}")
            return []
        logger.debug(f"[عملات] الأطر الزمنية النشطة لـ {symbol}: {timeframes}")
        return timeframes

    @staticmethod
    async def add(session: AsyncSession, identifier, symbol: str,
                  capital_allocated: float,
                  risk_per_trade: float = 1.0,
                  timeframes: Optional[List[str]] = None,
                  min_entry_size: float = 0.0) -> Coin:
        """
        إضافة عملة للمستخدم. `capital_allocated` إجباري — لا قيمة افتراضية.
        identifier يمكن أن يكون: int (telegram_id)، str (telegram_id أو UUID)، أو كائن User.

        يتعامل مع الرموز المكررة: إذا كانت العملة موجودة بنفس الرمز، يُحدّثها.
        """
        user_uuid = await CoinRepository._resolve_user_id(session, identifier)
        if timeframes is None:
            timeframes = ["15m"]

        logger.info(
            f"[عملات] إضافة: رمز={symbol} رأس_مال={capital_allocated} "
            f"مخاطرة={risk_per_trade}% أطر={timeframes} مستخدم={user_uuid[:8]}..."
        )

        # التحقق من وجود عملة بنفس الرمز
        existing = await session.execute(
            select(Coin).where(
                and_(Coin.user_id == user_uuid, Coin.symbol == symbol)
            )
        )
        existing_coin = existing.scalars().first()

        if existing_coin:
            # تحديث الموجود
            existing_coin.capital_allocated = capital_allocated
            existing_coin.risk_per_trade = risk_per_trade
            existing_coin.timeframes = timeframes
            existing_coin.min_entry_size = min_entry_size
            existing_coin.is_active = True
            await session.commit()
            logger.info(f"[عملات] تم تحديث الموجود: {symbol}")
            return existing_coin

        # إنشاء جديد
        coin = Coin(
            user_id=user_uuid,
            symbol=symbol,
            capital_allocated=capital_allocated,
            risk_per_trade=risk_per_trade,
            timeframes=timeframes,
            min_entry_size=min_entry_size,
        )
        session.add(coin)
        await session.commit()
        logger.info(f"[عملات] تم الإنشاء: {symbol} معرف={coin.id[:8]}...")
        return coin

    @staticmethod
    async def delete_by_symbol(session: AsyncSession, identifier, symbol: str):
        """حذف عملة بالرمز للمستخدم."""
        user_uuid = await CoinRepository._resolve_user_id(session, identifier)
        await session.execute(
            delete(Coin).where(and_(Coin.user_id == user_uuid, Coin.symbol == symbol))
        )
        await session.commit()
        logger.info(f"[عملات] تم الحذف: {symbol}")

    @staticmethod
    async def update(session: AsyncSession, coin: Coin, **kwargs):
        """تحديث حقول عملة موجودة."""
        for key, value in kwargs.items():
            setattr(coin, key, value)
        await session.commit()
        logger.info(f"[عملات] تم التحديث: {coin.symbol} {kwargs}")


# ═══════════════════════════════════════════════════════════════
#  مستودع الصفقات
# ═══════════════════════════════════════════════════════════════

class TradeRepository:
    """إدارة سجلات الصفقات المنفَّذة."""

    @staticmethod
    async def _resolve_user_id(session: AsyncSession, identifier) -> str:
        """تحويل معرّف المستخدم إلى UUID."""
        if hasattr(identifier, 'id'):
            return identifier.id
        return await UserRepository.resolve_user_uuid(session, identifier)

    @staticmethod
    async def get_open_trades(session: AsyncSession, symbol: str) -> List[Trade]:
        """جلب كل الصفقات المفتوحة لرمز معين (عبر كل المستخدمين)."""
        result = await session.execute(
            select(Trade).where(and_(Trade.symbol == symbol, Trade.status == "OPEN"))
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_open_trades_for_user(session: AsyncSession, identifier) -> List[Trade]:
        """جلب الصفقات المفتوحة لمستخدم محدد."""
        user_uuid = await TradeRepository._resolve_user_id(session, identifier)
        result = await session.execute(
            select(Trade).where(and_(Trade.user_id == user_uuid, Trade.status == "OPEN"))
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_closed_trades(session: AsyncSession, identifier, limit: int = 20) -> List[Trade]:
        """جلب آخر الصفقات المغلقة لمستخدم (الأحدث أولاً)."""
        user_uuid = await TradeRepository._resolve_user_id(session, identifier)
        result = await session.execute(
            select(Trade)
            .where(and_(Trade.user_id == user_uuid, Trade.status != "OPEN"))
            .order_by(Trade.closed_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_all_closed(session: AsyncSession, identifier) -> List[Trade]:
        """جلب كل الصفقات المغلقة لمستخدم."""
        user_uuid = await TradeRepository._resolve_user_id(session, identifier)
        result = await session.execute(
            select(Trade).where(and_(Trade.user_id == user_uuid, Trade.status != "OPEN"))
        )
        return list(result.scalars().all())

    @staticmethod
    async def add(session: AsyncSession, identifier, symbol: str,
                  side: str = "BUY", entry_price: float = 0.0,
                  quantity: float = 0.0, strategy_used: str = "unknown",
                  risk_score: float = 50.0, confidence_score: float = 50.0,
                  entry_reason: str = "", market_conditions: dict = None,
                  fees: float = 0.0) -> Trade:
        """إضافة صفقة جديدة. يحل UUID المستخدم تلقائياً."""
        user_uuid = await TradeRepository._resolve_user_id(session, identifier)
        trade = Trade(
            user_id=user_uuid,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            strategy_used=strategy_used,
            risk_score=risk_score,
            confidence_score=confidence_score,
            entry_reason=entry_reason,
            market_conditions=market_conditions or {},
            fees=fees,
        )
        session.add(trade)
        await session.commit()
        logger.info(
            f"[صفقات] تم الإنشاء: {symbol} {side} كمية={quantity:.6f} @ {entry_price}"
        )
        return trade

    @staticmethod
    async def close_trade(session: AsyncSession, trade: Trade,
                          exit_price: float, status: str, exit_reason: str):
        """إغلاق صفقة وحساب الربح/الخسارة."""
        trade.exit_price = exit_price
        trade.status = status
        trade.exit_reason = exit_reason
        trade.closed_at = datetime.utcnow()
        trade.pnl = ((exit_price - trade.entry_price) / trade.entry_price) * trade.quantity
        await session.commit()
        logger.info(
            f"[صفقات] تم الإغلاق: {trade.symbol} {status} ربح/خسارة={trade.pnl:.2f}"
        )

    @staticmethod
    async def has_open_trade(session: AsyncSession, identifier, symbol: str) -> bool:
        """التحقق من وجود صفقة مفتوحة للرمز عند المستخدم."""
        user_uuid = await TradeRepository._resolve_user_id(session, identifier)
        result = await session.execute(
            select(Trade).where(
                and_(Trade.user_id == user_uuid, Trade.symbol == symbol, Trade.status == "OPEN")
            )
        )
        return result.scalars().first() is not None


# ═══════════════════════════════════════════════════════════════
#  مستودع المراكز
# ═══════════════════════════════════════════════════════════════

class PositionRepository:
    """إدارة المراكز المفتوحة."""

    @staticmethod
    async def _resolve_user_id(session: AsyncSession, identifier) -> str:
        if hasattr(identifier, 'id'):
            return identifier.id
        return await UserRepository.resolve_user_uuid(session, identifier)

    @staticmethod
    async def get_open(session: AsyncSession, identifier) -> List[Position]:
        """جلب كل المراكز المفتوحة للمستخدم."""
        user_uuid = await PositionRepository._resolve_user_id(session, identifier)
        result = await session.execute(
            select(Position).where(
                and_(Position.user_id == user_uuid, Position.status == "OPEN")
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_by_symbol(session: AsyncSession, identifier, symbol: str) -> Optional[Position]:
        """جلب المركز المفتوح لرمز معين عند المستخدم."""
        user_uuid = await PositionRepository._resolve_user_id(session, identifier)
        result = await session.execute(
            select(Position).where(
                and_(Position.user_id == user_uuid,
                     Position.symbol == symbol,
                     Position.status == "OPEN")
            )
        )
        return result.scalars().first()

    @staticmethod
    async def close_position(session: AsyncSession, position: Position,
                             exit_price: float = None, reason: str = None):
        """إغلاق مركز مع سعر الخروج والسبب."""
        from datetime import timezone
        position.status = "CLOSED"
        position.closed_at = datetime.now(tz=timezone.utc)
        if exit_price:
            position.exit_price = exit_price
        if reason:
            position.close_reason = reason
        await session.commit()
        pnl_str = ""
        if exit_price and position.entry_price:
            pnl_pct = (exit_price - position.entry_price) / position.entry_price * 100
            pnl_str = f" | PnL={pnl_pct:+.2f}%"
        logger.info(f"[مراكز] تم الإغلاق: {position.symbol} السبب={reason}{pnl_str}")

    @staticmethod
    async def create(session: AsyncSession, identifier, symbol: str,
                     entry_price: float, quantity: float,
                     stop_loss: float = None, take_profit: float = None,
                     risk_exposure: float = 0.0) -> Position:
        """إنشاء مركز جديد. يحل UUID المستخدم تلقائياً."""
        user_uuid = await PositionRepository._resolve_user_id(session, identifier)
        position = Position(
            user_id=user_uuid,
            symbol=symbol,
            entry_price=entry_price,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_exposure=risk_exposure,
        )
        session.add(position)
        await session.commit()
        logger.info(f"[مراكز] تم الإنشاء: {symbol} كمية={quantity:.6f}")
        return position


# ═══════════════════════════════════════════════════════════════
#  مستودع المحفظة
# ═══════════════════════════════════════════════════════════════

class PortfolioRepository:
    """إدارة لقطات المحفظة."""

    @staticmethod
    async def _resolve_user_id(session: AsyncSession, identifier) -> str:
        if hasattr(identifier, 'id'):
            return identifier.id
        return await UserRepository.resolve_user_uuid(session, identifier)

    @staticmethod
    async def save_snapshot(session: AsyncSession, identifier,
                            total_balance: float, available_balance: float,
                            unrealized_pnl: float = 0.0,
                            realized_pnl: float = 0.0,
                            exposure: float = 0.0) -> PortfolioSnapshot:
        """حفظ لقطة جديدة للمحفظة."""
        user_uuid = await PortfolioRepository._resolve_user_id(session, identifier)
        snapshot = PortfolioSnapshot(
            user_id=user_uuid,
            total_balance=total_balance,
            available_balance=available_balance,
            unrealized_pnl=unrealized_pnl,
            realized_pnl=realized_pnl,
            exposure=exposure,
        )
        session.add(snapshot)
        await session.commit()
        return snapshot

    @staticmethod
    async def get_latest(session: AsyncSession, identifier) -> Optional[PortfolioSnapshot]:
        """جلب أحدث لقطة محفظة للمستخدم."""
        user_uuid = await PortfolioRepository._resolve_user_id(session, identifier)
        result = await session.execute(
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.user_id == user_uuid)
            .order_by(PortfolioSnapshot.timestamp.desc())
            .limit(1)
        )
        return result.scalars().first()


# ═══════════════════════════════════════════════════════════════
#  مستودع الإشارات
# ═══════════════════════════════════════════════════════════════

class SignalRepository:
    """إدارة إشارات التداول. V4.0: دعم التصفية حسب الإطار الزمني."""

    @staticmethod
    async def save(session: AsyncSession, signal: Signal):
        """حفظ إشارة جديدة."""
        session.add(signal)
        await session.commit()

    @staticmethod
    async def get_recent(session: AsyncSession, symbol: str, limit: int = 10) -> List[Signal]:
        """جلب أحدث الإشارات لرمز معين."""
        result = await session.execute(
            select(Signal).where(Signal.symbol == symbol)
            .order_by(Signal.timestamp.desc()).limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_recent_by_timeframe(
        session: AsyncSession, symbol: str, timeframe: str, limit: int = 10
    ) -> List[Signal]:
        """جلب أحدث الإشارات لرمز وإطار زمني محدد."""
        result = await session.execute(
            select(Signal)
            .where(and_(Signal.symbol == symbol, Signal.timeframe == timeframe))
            .order_by(Signal.timestamp.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


# ═══════════════════════════════════════════════════════════════
#  مستودع السجلات
# ═══════════════════════════════════════════════════════════════

class LogRepository:
    """إدارة سجلات النظام (مسار التدقيق)."""

    @staticmethod
    async def save(session: AsyncSession, log_entry: SystemLog):
        """حفظ سجل جديد."""
        session.add(log_entry)
        await session.commit()

    @staticmethod
    async def get_recent(session: AsyncSession, limit: int = 50) -> List[SystemLog]:
        """جلب أحدث السجلات."""
        result = await session.execute(
            select(SystemLog).order_by(SystemLog.timestamp.desc()).limit(limit)
        )
        return list(result.scalars().all())


# ═══════════════════════════════════════════════════════════════
#  مستودع أثر القرار
# ═══════════════════════════════════════════════════════════════

class DecisionTraceRepository:
    """إدارة تتبع مسار القرارات."""

    @staticmethod
    async def save(session: AsyncSession, trace: DecisionTrace):
        """حفظ أثر قرار جديد."""
        session.add(trace)
        await session.commit()

    @staticmethod
    async def get_by_signal(session: AsyncSession, signal_id: str) -> Optional[DecisionTrace]:
        """جلب أثر القرار المرتبط بإشارة محددة."""
        result = await session.execute(
            select(DecisionTrace).where(DecisionTrace.signal_id == signal_id)
        )
        return result.scalars().first()


# ═══════════════════════════════════════════════════════════════
#  مستودع أحداث الحيتان
# ═══════════════════════════════════════════════════════════════

class WhaleEventRepository:
    """إدارة تتبع صفقات الحيتان."""

    @staticmethod
    async def save(session: AsyncSession, event: WhaleEvent):
        """حفظ حدث حوت جديد."""
        session.add(event)
        await session.commit()

    @staticmethod
    async def get_recent_by_symbol(session: AsyncSession, symbol: str, limit: int = 5) -> List[WhaleEvent]:
        """جلب أحدث أحداث الحيتان لرمز معين."""
        result = await session.execute(
            select(WhaleEvent).where(WhaleEvent.symbol == symbol)
            .order_by(WhaleEvent.timestamp.desc()).limit(limit)
        )
        return list(result.scalars().all())


# ═══════════════════════════════════════════════════════════════
#  مستودع إحصائيات الاستراتيجيات
# ═══════════════════════════════════════════════════════════════

class StrategyStatRepository:
    """إدارة إحصائيات أداء الاستراتيجيات."""

    @staticmethod
    async def upsert(session: AsyncSession, strategy_name: str, symbol: str,
                     win_rate: float, avg_profit: float, avg_loss: float,
                     drawdown: float, total_trades: int, timeframe: str):
        """تحديث أو إنشاء إحصائية استراتيجية."""
        result = await session.execute(
            select(StrategyStat).where(
                and_(StrategyStat.strategy_name == strategy_name,
                     StrategyStat.symbol == symbol)
            )
        )
        stat = result.scalars().first()
        if stat:
            stat.win_rate = win_rate
            stat.avg_profit = avg_profit
            stat.avg_loss = avg_loss
            stat.drawdown = drawdown
            stat.total_trades = total_trades
            stat.timeframe = timeframe
            stat.updated_at = datetime.utcnow()
        else:
            stat = StrategyStat(
                strategy_name=strategy_name, symbol=symbol,
                win_rate=win_rate, avg_profit=avg_profit, avg_loss=avg_loss,
                drawdown=drawdown, total_trades=total_trades, timeframe=timeframe
            )
            session.add(stat)
        await session.commit()


# ═══════════════════════════════════════════════════════════════
#  مستودع حالة السوق
# ═══════════════════════════════════════════════════════════════

class MarketStateRepository:
    """
    إدارة حالة السوق لكل رمز وإطار زمني.
    V4.0: دعم التصفية حسب الإطار الزمني.
    """

    @staticmethod
    async def save(session: AsyncSession, state: MarketState):
        """حفظ حالة سوق جديدة."""
        session.add(state)
        await session.commit()

    @staticmethod
    async def get_latest(session: AsyncSession, symbol: str, timeframe: str = None) -> Optional[MarketState]:
        """
        جلب أحدث حالة سوق لرمز معين.
        إذا تم تمرير `timeframe`، يتم التصفية حسب الإطار الزمني أيضاً.
        """
        conditions = [MarketState.symbol == symbol]
        if timeframe:
            conditions.append(MarketState.timeframe == timeframe)

        result = await session.execute(
            select(MarketState)
            .where(and_(*conditions))
            .order_by(MarketState.timestamp.desc())
            .limit(1)
        )
        return result.scalars().first()

    @staticmethod
    async def get_history(
        session: AsyncSession, symbol: str, timeframe: str = None, limit: int = 20
    ) -> List[MarketState]:
        """
        جلب سجل حالات السوق لرمز معين (الأحدث أولاً).
        إذا تم تمرير `timeframe`، يتم التصفية حسب الإطار الزمني.
        """
        conditions = [MarketState.symbol == symbol]
        if timeframe:
            conditions.append(MarketState.timeframe == timeframe)

        result = await session.execute(
            select(MarketState)
            .where(and_(*conditions))
            .order_by(MarketState.timestamp.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_all_timeframes_latest(
        session: AsyncSession, symbol: str
    ) -> List[MarketState]:
        """
        جلب أحدث حالة سوق لكل إطار زمني مختلف لرمز معين.
        تُرجع قائمة — عنصر واحد لكل إطار زمني فريد، بأحدث طابع زمني له.
        """
        # استعلام فرعي: أحدث طابع زمني لكل إطار زمني
        subquery = (
            select(
                MarketState.timeframe,
                func.max(MarketState.timestamp).label("max_ts")
            )
            .where(MarketState.symbol == symbol)
            .group_by(MarketState.timeframe)
            .subquery()
        )

        result = await session.execute(
            select(MarketState)
            .join(
                subquery,
                and_(
                    MarketState.symbol == symbol,
                    MarketState.timeframe == subquery.c.timeframe,
                    MarketState.timestamp == subquery.c.max_ts
                )
            )
        )
        return list(result.scalars().all())


# ═══════════════════════════════════════════════════════════════
#  مستودع الشموع — Candle Cache (بديل REST)
# ═══════════════════════════════════════════════════════════════

class CandleCacheRepository:
    """تخزين واسترجاع الشموع المغلقة — بديل عن Binance REST عند الحظر."""

    @staticmethod
    async def upsert_candle(session: AsyncSession, symbol: str, timeframe: str,
                            open_time: int, open_p: float, high_p: float,
                            low_p: float, close_p: float, volume_v: float):
        """إدراج أو تحديث شمعة واحدة."""
        from database.models import CandleCache
        from sqlalchemy import select

        existing = await session.execute(
            select(CandleCache).where(
                CandleCache.symbol == symbol,
                CandleCache.timeframe == timeframe,
                CandleCache.open_time == open_time,
            )
        )
        row = existing.scalar_one_or_none()

        if row:
            row.open = open_p
            row.high = high_p
            row.low = low_p
            row.close = close_p
            row.volume = volume_v
        else:
            session.add(CandleCache(
                symbol=symbol, timeframe=timeframe, open_time=open_time,
                open=open_p, high=high_p, low=low_p, close=close_p, volume=volume_v,
            ))
        await session.commit()

    @staticmethod
    async def get_candles(session: AsyncSession, symbol: str, timeframe: str,
                          limit: int = 200) -> list[dict]:
        """استرجاع آخر N شمعة."""
        from database.models import CandleCache
        from sqlalchemy import select, desc

        result = await session.execute(
            select(CandleCache)
            .where(
                CandleCache.symbol == symbol,
                CandleCache.timeframe == timeframe,
            )
            .order_by(desc(CandleCache.open_time))
            .limit(limit)
        )
        rows = result.scalars().all()

        candles = []
        for row in reversed(rows):
            candles.append({
                "t": row.open_time,
                "o": row.open,
                "h": row.high,
                "l": row.low,
                "c": row.close,
                "v": row.volume,
            })
        return candles
