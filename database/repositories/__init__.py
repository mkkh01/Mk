"""
Repository layer — all database queries.
Each repository handles exactly one entity. No business logic.

CRITICAL: users.id is UUID. All tables FK to users.id MUST use the UUID,
not the Telegram ID. UserRepository.resolve_user_uuid() handles the mapping.
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

# ── Engine Setup ────────────────────────────────────────────
_engine = None
_async_session_factory = None


async def init_db() -> None:
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
    logger.info("[DATABASE] Initialized successfully.")


async def close_db() -> None:
    if _engine:
        await _engine.dispose()
        logger.info("[DATABASE] Connections closed.")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    if _async_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with _async_session_factory() as session:
        yield session


# ═══════════════════════════════════════════════════════════════
#  User Repository — the single source of truth for user UUID
# ═══════════════════════════════════════════════════════════════

class UserRepository:
    """Manages User records. Provides Telegram ID → UUID resolution."""

    # In-process cache: telegram_id_str → user_uuid
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
        cls._uuid_cache.clear()

    @staticmethod
    async def resolve_user_uuid(session: AsyncSession, telegram_id) -> str:
        """
        Resolve a Telegram ID to the user's UUID (users.id).
        Creates the user record automatically if it doesn't exist.
        This is the ONLY method that should be used to get a valid user_id
        for foreign key references.

        Returns: str — the UUID from users.id
        """
        tid = str(telegram_id)
        logger.debug(f"[AUTH] Resolving user UUID for telegram_id={tid}")

        # 1. Check in-process cache
        cached = UserRepository._cache_get(tid)
        if cached:
            logger.debug(f"[AUTH] Cache hit: telegram_id={tid} → uuid={cached[:8]}...")
            return cached

        # 2. Query database
        result = await session.execute(
            select(User).where(User.telegram_id == tid)
        )
        user = result.scalars().first()

        if user:
            UserRepository._cache_set(tid, user.id)
            logger.info(f"[AUTH] User found: telegram_id={tid} → uuid={user.id[:8]}...")
            return user.id

        # 3. Create user
        logger.info(f"[AUTH] Creating new user for telegram_id={tid}")
        user = User(telegram_id=tid)
        session.add(user)
        await session.commit()
        await session.refresh(user)  # Ensure we have the generated UUID

        UserRepository._cache_set(tid, user.id)
        logger.info(f"[AUTH] User created: telegram_id={tid} → uuid={user.id[:8]}...")
        return user.id

    @staticmethod
    async def get_or_create(session: AsyncSession, telegram_id: int) -> User:
        """Get or create user. Returns full User object."""
        uuid_str = await UserRepository.resolve_user_uuid(session, telegram_id)
        result = await session.execute(select(User).where(User.id == uuid_str))
        return result.scalars().one()

    @staticmethod
    async def get_by_telegram_id(session: AsyncSession, telegram_id: int) -> Optional[User]:
        result = await session.execute(
            select(User).where(User.telegram_id == str(telegram_id))
        )
        return result.scalars().first()

    @staticmethod
    async def get_by_uuid(session: AsyncSession, user_uuid: str) -> Optional[User]:
        result = await session.execute(select(User).where(User.id == user_uuid))
        return result.scalars().first()

    @staticmethod
    async def update_status(session: AsyncSession, user: User,
                            is_active: bool, emergency_stop: bool = False):
        user.is_active = is_active
        user.emergency_stop = emergency_stop
        await session.commit()
        logger.info(f"[AUTH] User {user.telegram_id} status: active={is_active} stop={emergency_stop}")


# ═══════════════════════════════════════════════════════════════
#  Coin Repository
# ═══════════════════════════════════════════════════════════════

class CoinRepository:

    @staticmethod
    async def _resolve_user_id(session: AsyncSession, identifier) -> str:
        """
        Resolve user identifier (Telegram ID or UUID) to UUID.
        Accepts: int (telegram_id), str (telegram_id or UUID), or User object.
        """
        if hasattr(identifier, 'id'):  # User object
            return identifier.id
        try:
            # Try as UUID first (36-char with dashes)
            sid = str(identifier)
            if len(sid) == 36 and sid.count('-') == 4:
                return sid
        except Exception:
            pass
        # Treat as telegram_id
        return await UserRepository.resolve_user_uuid(session, identifier)

    @staticmethod
    async def get_all_active(session: AsyncSession, identifier) -> List[Coin]:
        user_uuid = await CoinRepository._resolve_user_id(session, identifier)
        result = await session.execute(
            select(Coin).where(and_(Coin.user_id == user_uuid, Coin.is_active == True))
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_all(session: AsyncSession, identifier) -> List[Coin]:
        user_uuid = await CoinRepository._resolve_user_id(session, identifier)
        result = await session.execute(
            select(Coin).where(Coin.user_id == user_uuid)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_by_symbol(session: AsyncSession, identifier, symbol: str) -> Optional[Coin]:
        user_uuid = await CoinRepository._resolve_user_id(session, identifier)
        result = await session.execute(
            select(Coin).where(and_(Coin.user_id == user_uuid, Coin.symbol == symbol))
        )
        return result.scalars().first()

    @staticmethod
    async def add(session: AsyncSession, identifier, symbol: str,
                  capital_allocated: float = 100.0,
                  risk_per_trade: float = 1.0,
                  timeframe: str = "15m") -> Coin:
        """
        Add a coin for a user. Automatically resolves user UUID.
        identifier can be: int (telegram_id), str (telegram_id or UUID), or User object.

        Handles duplicate symbol: if coin with same symbol exists, updates it.
        """
        user_uuid = await CoinRepository._resolve_user_id(session, identifier)
        logger.info(
            f"[COIN] Adding: symbol={symbol} capital={capital_allocated} "
            f"risk={risk_per_trade}% tf={timeframe} user_uuid={user_uuid[:8]}..."
        )

        # Check for existing coin with same symbol
        existing = await session.execute(
            select(Coin).where(
                and_(Coin.user_id == user_uuid, Coin.symbol == symbol)
            )
        )
        existing_coin = existing.scalars().first()

        if existing_coin:
            # Update existing
            existing_coin.capital_allocated = capital_allocated
            existing_coin.risk_per_trade = risk_per_trade
            existing_coin.timeframe = timeframe
            existing_coin.is_active = True
            await session.commit()
            logger.info(f"[COIN] Updated existing: {symbol}")
            return existing_coin

        # Create new
        coin = Coin(
            user_id=user_uuid,
            symbol=symbol,
            capital_allocated=capital_allocated,
            risk_per_trade=risk_per_trade,
            timeframe=timeframe,
        )
        session.add(coin)
        await session.commit()
        logger.info(f"[COIN] Created: {symbol} id={coin.id[:8]}...")
        return coin

    @staticmethod
    async def delete_by_symbol(session: AsyncSession, identifier, symbol: str):
        user_uuid = await CoinRepository._resolve_user_id(session, identifier)
        await session.execute(
            delete(Coin).where(and_(Coin.user_id == user_uuid, Coin.symbol == symbol))
        )
        await session.commit()
        logger.info(f"[COIN] Deleted: {symbol}")

    @staticmethod
    async def update(session: AsyncSession, coin: Coin, **kwargs):
        for key, value in kwargs.items():
            setattr(coin, key, value)
        await session.commit()
        logger.info(f"[COIN] Updated: {coin.symbol} {kwargs}")


# ═══════════════════════════════════════════════════════════════
#  Trade Repository
# ═══════════════════════════════════════════════════════════════

class TradeRepository:

    @staticmethod
    async def _resolve_user_id(session: AsyncSession, identifier) -> str:
        if hasattr(identifier, 'id'):
            return identifier.id
        return await UserRepository.resolve_user_uuid(session, identifier)

    @staticmethod
    async def get_open_trades(session: AsyncSession, symbol: str) -> List[Trade]:
        result = await session.execute(
            select(Trade).where(and_(Trade.symbol == symbol, Trade.status == "OPEN"))
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_open_trades_for_user(session: AsyncSession, identifier) -> List[Trade]:
        user_uuid = await TradeRepository._resolve_user_id(session, identifier)
        result = await session.execute(
            select(Trade).where(and_(Trade.user_id == user_uuid, Trade.status == "OPEN"))
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_closed_trades(session: AsyncSession, identifier, limit: int = 20) -> List[Trade]:
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
        """Add a trade. Handles user UUID resolution automatically."""
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
        logger.info(f"[TRADE] Created: {symbol} {side} qty={quantity:.6f} @ {entry_price}")
        return trade

    @staticmethod
    async def close_trade(session: AsyncSession, trade: Trade,
                          exit_price: float, status: str, exit_reason: str):
        trade.exit_price = exit_price
        trade.status = status
        trade.exit_reason = exit_reason
        trade.closed_at = datetime.utcnow()
        trade.pnl = ((exit_price - trade.entry_price) / trade.entry_price) * trade.quantity
        await session.commit()
        logger.info(f"[TRADE] Closed: {trade.symbol} {status} PnL={trade.pnl:.2f}")

    @staticmethod
    async def has_open_trade(session: AsyncSession, identifier, symbol: str) -> bool:
        user_uuid = await TradeRepository._resolve_user_id(session, identifier)
        result = await session.execute(
            select(Trade).where(
                and_(Trade.user_id == user_uuid, Trade.symbol == symbol, Trade.status == "OPEN")
            )
        )
        return result.scalars().first() is not None


# ═══════════════════════════════════════════════════════════════
#  Position Repository
# ═══════════════════════════════════════════════════════════════

class PositionRepository:

    @staticmethod
    async def _resolve_user_id(session: AsyncSession, identifier) -> str:
        if hasattr(identifier, 'id'):
            return identifier.id
        return await UserRepository.resolve_user_uuid(session, identifier)

    @staticmethod
    async def get_open(session: AsyncSession, identifier) -> List[Position]:
        user_uuid = await PositionRepository._resolve_user_id(session, identifier)
        result = await session.execute(
            select(Position).where(
                and_(Position.user_id == user_uuid, Position.status == "OPEN")
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_by_symbol(session: AsyncSession, identifier, symbol: str) -> Optional[Position]:
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
    async def close_position(session: AsyncSession, position: Position):
        position.status = "CLOSED"
        await session.commit()
        logger.info(f"[POSITION] Closed: {position.symbol}")

    @staticmethod
    async def create(session: AsyncSession, identifier, symbol: str,
                     entry_price: float, quantity: float,
                     stop_loss: float = None, take_profit: float = None,
                     risk_exposure: float = 0.0) -> Position:
        """Create a position. Handles user UUID resolution."""
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
        logger.info(f"[POSITION] Created: {symbol} qty={quantity:.6f}")
        return position


# ═══════════════════════════════════════════════════════════════
#  Portfolio Repository
# ═══════════════════════════════════════════════════════════════

class PortfolioRepository:

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
        """Save a portfolio snapshot. Handles user UUID resolution."""
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
        user_uuid = await PortfolioRepository._resolve_user_id(session, identifier)
        result = await session.execute(
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.user_id == user_uuid)
            .order_by(PortfolioSnapshot.timestamp.desc())
            .limit(1)
        )
        return result.scalars().first()


# ═══════════════════════════════════════════════════════════════
#  Other Repositories (unchanged, no user FK)
# ═══════════════════════════════════════════════════════════════

class SignalRepository:
    @staticmethod
    async def save(session: AsyncSession, signal: Signal):
        session.add(signal)
        await session.commit()

    @staticmethod
    async def get_recent(session: AsyncSession, symbol: str, limit: int = 10) -> List[Signal]:
        result = await session.execute(
            select(Signal).where(Signal.symbol == symbol)
            .order_by(Signal.timestamp.desc()).limit(limit)
        )
        return list(result.scalars().all())


class LogRepository:
    @staticmethod
    async def save(session: AsyncSession, log_entry: SystemLog):
        session.add(log_entry)
        await session.commit()

    @staticmethod
    async def get_recent(session: AsyncSession, limit: int = 50) -> List[SystemLog]:
        result = await session.execute(
            select(SystemLog).order_by(SystemLog.timestamp.desc()).limit(limit)
        )
        return list(result.scalars().all())


class DecisionTraceRepository:
    @staticmethod
    async def save(session: AsyncSession, trace: DecisionTrace):
        session.add(trace)
        await session.commit()

    @staticmethod
    async def get_by_signal(session: AsyncSession, signal_id: str) -> Optional[DecisionTrace]:
        result = await session.execute(
            select(DecisionTrace).where(DecisionTrace.signal_id == signal_id)
        )
        return result.scalars().first()


class WhaleEventRepository:
    @staticmethod
    async def save(session: AsyncSession, event: WhaleEvent):
        session.add(event)
        await session.commit()

    @staticmethod
    async def get_recent_by_symbol(session: AsyncSession, symbol: str, limit: int = 5) -> List[WhaleEvent]:
        result = await session.execute(
            select(WhaleEvent).where(WhaleEvent.symbol == symbol)
            .order_by(WhaleEvent.timestamp.desc()).limit(limit)
        )
        return list(result.scalars().all())


class StrategyStatRepository:
    @staticmethod
    async def upsert(session: AsyncSession, strategy_name: str, symbol: str,
                     win_rate: float, avg_profit: float, avg_loss: float,
                     drawdown: float, total_trades: int, timeframe: str):
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


class MarketStateRepository:
    @staticmethod
    async def save(session: AsyncSession, state: MarketState):
        session.add(state)
        await session.commit()

    @staticmethod
    async def get_latest(session: AsyncSession, symbol: str) -> Optional[MarketState]:
        result = await session.execute(
            select(MarketState).where(MarketState.symbol == symbol)
            .order_by(MarketState.timestamp.desc()).limit(1)
        )
        return result.scalars().first()
