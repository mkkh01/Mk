import ssl
from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import Text, func, JSON

from config import DATABASE_URL


# SSL إعداد الاتصال (Supabase / Render)
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE


# Async Engine
engine = create_async_engine(
    DATABASE_URL,
    connect_args={
        "ssl": ssl_context,
        "command_timeout": 60,
    },
    pool_pre_ping=True,
    pool_recycle=3600,
)


# Session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)


# Base class
class Base(DeclarativeBase):
    def __repr__(self):
        return f"<{self.__class__.__name__}>"


# -------------------- User Config --------------------
class UserConfig(Base):
    __tablename__ = "user_config_v4"

    telegram_id: Mapped[int] = mapped_column(primary_key=True)
    total_capital: Mapped[float] = mapped_column(default=1000.0)
    is_active: Mapped[bool] = mapped_column(default=False)
    emergency_stop: Mapped[bool] = mapped_column(default=False)
    consecutive_losses: Mapped[int] = mapped_column(default=0)
    max_drawdown_limit: Mapped[float] = mapped_column(default=10.0)
    risk_per_trade: Mapped[float] = mapped_column(default=1.0)


# -------------------- Tracked Coins --------------------
class TrackedCoin(Base):
    __tablename__ = "tracked_coins_v4"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(unique=True, nullable=False)
    capital: Mapped[float] = mapped_column(default=100.0)
    risk_percentage: Mapped[float] = mapped_column(default=1.0)
    timeframe: Mapped[str] = mapped_column(default="15m")
    enabled: Mapped[bool] = mapped_column(default=True)
    added_at: Mapped[datetime] = mapped_column(server_default=func.now())


# -------------------- Live Trades --------------------
class LiveTrade(Base):
    __tablename__ = "live_trades_v4"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(nullable=False)
    type: Mapped[str] = mapped_column(nullable=False)  # BUY / SELL

    entry_price: Mapped[float] = mapped_column(nullable=False)
    exit_price: Mapped[Optional[float]] = mapped_column(nullable=True)

    stop_loss: Mapped[Optional[float]] = mapped_column(nullable=True)
    take_profit: Mapped[Optional[float]] = mapped_column(nullable=True)

    amount: Mapped[float] = mapped_column(nullable=False)

    status: Mapped[str] = mapped_column(default="OPEN")  # OPEN, WON, LOST
    pnl: Mapped[float] = mapped_column(default=0.0)

    duration: Mapped[Optional[int]] = mapped_column(nullable=True)  # seconds
    score: Mapped[float] = mapped_column(default=0.0)

    entry_reason: Mapped[Optional[str]] = mapped_column(Text)
    exit_reason: Mapped[Optional[str]] = mapped_column(Text)
    market_state: Mapped[Optional[str]] = mapped_column(Text)

    timestamp: Mapped[datetime] = mapped_column(server_default=func.now())
    closed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)


# -------------------- Shadow Trades --------------------
class ShadowTrade(Base):
    __tablename__ = "shadow_trades_v4"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(nullable=False)

    indicators_snapshot: Mapped[Optional[dict]] = mapped_column(JSON)
    market_state: Mapped[Optional[str]] = mapped_column(Text)

    score: Mapped[float] = mapped_column(default=0.0)
    result: Mapped[Optional[str]] = mapped_column(Text)  # WIN / LOSS

    timestamp: Mapped[datetime] = mapped_column(server_default=func.now())


# -------------------- Init DB --------------------
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print("Database initialized successfully.")
