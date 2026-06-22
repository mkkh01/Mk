"""
Repository layer — all database queries.
Each repository handles exactly one entity. No business logic.
"""
import ssl
import logging
from datetime import datetime
from typing import Optional, List, AsyncGenerator

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select, delete, func, update, and_
from sqlalchemy.orm import selectinload

from config.settings import get_settings
from database.models import Base, User, Coin, MarketData, MarketState, Signal, Trade, Position, RiskEvent, PortfolioSnapshot, WhaleEvent, NewsEvent, StrategyStat, SystemLog, DecisionTrace

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
    logger.info("Database initialized successfully.")


async def close_db() -> None:
    if _engine:
        await _engine.dispose()
        logger.info("Database connections closed.")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    if _async_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with _async_session_factory() as session:
        yield session


# ── User Repository ─────────────────────────────────────────
class UserRepository:
    @staticmethod
    async def get_or_create(session: AsyncSession, telegram_id: int) -> User:
        result = await session.execute(
            select(User).where(User.telegram_id == str(telegram_id))
        )
        user = result.scalars().first()
        if not user:
            user = User(telegram_id=str(telegram_id))
            session.add(user)
            await session.commit()
        return user

    @staticmethod
    async def get_by_telegram_id(session: AsyncSession, telegram_id: int) -> Optional[User]:
        result = await session.execute(
            select(User).where(User.telegram_id == str(telegram_id))
        )
        return result.scalars().first()

    @staticmethod
    async def update_status(session: AsyncSession, user: User, is_active: bool, emergency_stop: bool = False):
        user.is_active = is_active
        user.emergency_stop = emergency_stop
        await session.commit()


# ── Coin Repository ─────────────────────────────────────────
class CoinRepository:
    @staticmethod
    async def get_all_active(session: AsyncSession, user_id: str) -> List[Coin]:
        result = await session.execute(
            select(Coin).where(and_(Coin.user_id == user_id, Coin.is_active == True))
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_all(session: AsyncSession, user_id: str) -> List[Coin]:
        result = await session.execute(
            select(Coin).where(Coin.user_id == user_id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_by_symbol(session: AsyncSession, user_id: str, symbol: str) -> Optional[Coin]:
        result = await session.execute(
            select(Coin).where(and_(Coin.user_id == user_id, Coin.symbol == symbol))
        )
        return result.scalars().first()

    @staticmethod
    async def add(session: AsyncSession, coin: Coin):
        session.add(coin)
        await session.commit()

    @staticmethod
    async def delete_by_symbol(session: AsyncSession, user_id: str, symbol: str):
        await session.execute(
            delete(Coin).where(and_(Coin.user_id == user_id, Coin.symbol == symbol))
        )
        await session.commit()

    @staticmethod
    async def update(session: AsyncSession, coin: Coin, **kwargs):
        for key, value in kwargs.items():
            setattr(coin, key, value)
        await session.commit()


# ── Trade Repository ────────────────────────────────────────
class TradeRepository:
    @staticmethod
    async def get_open_trades(session: AsyncSession, symbol: str) -> List[Trade]:
        result = await session.execute(
            select(Trade).where(
                and_(Trade.symbol == symbol, Trade.status == "OPEN")
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_open_trades_for_user(session: AsyncSession, user_id: str) -> List[Trade]:
        result = await session.execute(
            select(Trade).where(
                and_(Trade.user_id == user_id, Trade.status == "OPEN")
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_closed_trades(session: AsyncSession, user_id: str, limit: int = 20) -> List[Trade]:
        result = await session.execute(
            select(Trade)
            .where(and_(Trade.user_id == user_id, Trade.status != "OPEN"))
            .order_by(Trade.closed_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_all_closed(session: AsyncSession, user_id: str) -> List[Trade]:
        result = await session.execute(
            select(Trade).where(
                and_(Trade.user_id == user_id, Trade.status != "OPEN")
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def add(session: AsyncSession, trade: Trade):
        session.add(trade)
        await session.commit()

    @staticmethod
    async def close_trade(session: AsyncSession, trade: Trade, exit_price: float, status: str, exit_reason: str):
        trade.exit_price = exit_price
        trade.status = status
        trade.exit_reason = exit_reason
        trade.closed_at = datetime.utcnow()
        trade.pnl = ((exit_price - trade.entry_price) / trade.entry_price) * trade.quantity
        await session.commit()

    @staticmethod
    async def has_open_trade(session: AsyncSession, user_id: str, symbol: str) -> bool:
        result = await session.execute(
            select(Trade).where(
                and_(Trade.user_id == user_id, Trade.symbol == symbol, Trade.status == "OPEN")
            )
        )
        return result.scalars().first() is not None


# ── Signal Repository ───────────────────────────────────────
class SignalRepository:
    @staticmethod
    async def save(session: AsyncSession, signal: Signal):
        session.add(signal)
        await session.commit()

    @staticmethod
    async def get_recent(session: AsyncSession, symbol: str, limit: int = 10) -> List[Signal]:
        result = await session.execute(
            select(Signal)
            .where(Signal.symbol == symbol)
            .order_by(Signal.timestamp.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


# ── Position Repository ─────────────────────────────────────
class PositionRepository:
    @staticmethod
    async def get_open(session: AsyncSession, user_id: str) -> List[Position]:
        result = await session.execute(
            select(Position).where(
                and_(Position.user_id == user_id, Position.status == "OPEN")
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_by_symbol(session: AsyncSession, user_id: str, symbol: str) -> Optional[Position]:
        result = await session.execute(
            select(Position).where(
                and_(Position.user_id == user_id, Position.symbol == symbol, Position.status == "OPEN")
            )
        )
        return result.scalars().first()

    @staticmethod
    async def close_position(session: AsyncSession, position: Position):
        position.status = "CLOSED"
        await session.commit()


# ── Portfolio Repository ────────────────────────────────────
class PortfolioRepository:
    @staticmethod
    async def save_snapshot(session: AsyncSession, snapshot: PortfolioSnapshot):
        session.add(snapshot)
        await session.commit()

    @staticmethod
    async def get_latest(session: AsyncSession, user_id: str) -> Optional[PortfolioSnapshot]:
        result = await session.execute(
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.user_id == user_id)
            .order_by(PortfolioSnapshot.timestamp.desc())
            .limit(1)
        )
        return result.scalars().first()


# ── Log Repository ──────────────────────────────────────────
class LogRepository:
    @staticmethod
    async def save(session: AsyncSession, log_entry: SystemLog):
        session.add(log_entry)
        await session.commit()

    @staticmethod
    async def get_recent(session: AsyncSession, limit: int = 50) -> List[SystemLog]:
        result = await session.execute(
            select(SystemLog)
            .order_by(SystemLog.timestamp.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


# ── Decision Trace Repository ───────────────────────────────
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


# ── Whale Event Repository ──────────────────────────────────
class WhaleEventRepository:
    @staticmethod
    async def save(session: AsyncSession, event: WhaleEvent):
        session.add(event)
        await session.commit()

    @staticmethod
    async def get_recent_by_symbol(session: AsyncSession, symbol: str, limit: int = 5) -> List[WhaleEvent]:
        result = await session.execute(
            select(WhaleEvent)
            .where(WhaleEvent.symbol == symbol)
            .order_by(WhaleEvent.timestamp.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


# ── Strategy Stat Repository ────────────────────────────────
class StrategyStatRepository:
    @staticmethod
    async def upsert(session: AsyncSession, strategy_name: str, symbol: str,
                     win_rate: float, avg_profit: float, avg_loss: float,
                     drawdown: float, total_trades: int, timeframe: str):
        result = await session.execute(
            select(StrategyStat).where(
                and_(StrategyStat.strategy_name == strategy_name, StrategyStat.symbol == symbol)
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


# ── Market State Repository ─────────────────────────────────
class MarketStateRepository:
    @staticmethod
    async def save(session: AsyncSession, state: MarketState):
        session.add(state)
        await session.commit()

    @staticmethod
    async def get_latest(session: AsyncSession, symbol: str) -> Optional[MarketState]:
        result = await session.execute(
            select(MarketState)
            .where(MarketState.symbol == symbol)
            .order_by(MarketState.timestamp.desc())
            .limit(1)
        )
        return result.scalars().first()
