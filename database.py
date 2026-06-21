import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from datetime import datetime
from typing import Optional
from sqlalchemy import Text, func, JSON
from config import DATABASE_URL

# إعداد المحرك للعمل مع Supabase/PostgreSQL
engine = create_async_engine(
    DATABASE_URL,
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0
    }
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

class Base(DeclarativeBase):
    def __repr__(self):
        return f"<{self.__class__.__name__} {', '.join([f'{c.name}={getattr(self, c.name)!r}' for c in self.__table__.columns])}>"

class UserConfig(Base):
    __tablename__ = "user_config_v4"
    
    telegram_id: Mapped[int] = mapped_column(primary_key=True)
    total_capital: Mapped[float] = mapped_column(default=1000.0)
    is_active: Mapped[bool] = mapped_column(default=False)
    emergency_stop: Mapped[bool] = mapped_column(default=False)
    consecutive_losses: Mapped[int] = mapped_column(default=0)
    max_drawdown_limit: Mapped[float] = mapped_column(default=10.0) # 10% limit
    risk_per_trade: Mapped[float] = mapped_column(default=1.0) # 1% default
    
class TrackedCoin(Base):
    __tablename__ = "tracked_coins_v4"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(unique=True, nullable=False)
    capital: Mapped[float] = mapped_column(default=100.0)
    risk_percentage: Mapped[float] = mapped_column(default=1.0)
    timeframe: Mapped[str] = mapped_column(default="15m")
    enabled: Mapped[bool] = mapped_column(default=True)
    added_at: Mapped[datetime] = mapped_column(server_default=func.now())

class LiveTrade(Base):
    __tablename__ = "live_trades_v4"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(nullable=False)
    type: Mapped[str] = mapped_column(nullable=False) # BUY / SELL
    entry_price: Mapped[float] = mapped_column(nullable=False)
    exit_price: Mapped[Optional[float]] = mapped_column(nullable=True)
    stop_loss: Mapped[Optional[float]] = mapped_column(nullable=True)
    take_profit: Mapped[Optional[float]] = mapped_column(nullable=True)
    amount: Mapped[float] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(default="OPEN") # OPEN, WON, LOST
    pnl: Mapped[float] = mapped_column(default=0.0)
    duration: Mapped[Optional[int]] = mapped_column(nullable=True) # Seconds
    score: Mapped[float] = mapped_column(default=0.0)
    entry_reason: Mapped[Optional[str]] = mapped_column(Text)
    exit_reason: Mapped[Optional[str]] = mapped_column(Text)
    market_state: Mapped[Optional[str]] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(server_default=func.now())
    closed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

class ShadowTrade(Base):
    __tablename__ = "shadow_trades_v4"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(nullable=False)
    indicators_snapshot: Mapped[Optional[dict]] = mapped_column(JSON)
    market_state: Mapped[Optional[str]] = mapped_column(Text)
    score: Mapped[float] = mapped_column(default=0.0)
    result: Mapped[Optional[str]] = mapped_column(Text) # WIN / LOSS
    timestamp: Mapped[datetime] = mapped_column(server_default=func.now())

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        print("✅ Institutional Database Schema V4 Initialized.")
