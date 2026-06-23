"""
نماذج قاعدة البيانات — تعريفات SQLAlchemy ORM.
مُطابقة تماماً، مدفوعة بالأحداث. لا تحتوي على منطق أعمال.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column, String, Float, Integer, Boolean, DateTime, Text,
    ForeignKey, JSON, Numeric, Index, BigInteger, UniqueConstraint
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import func
import uuid


def gen_uuid() -> str:
    """توليد UUID فريد للمفاتيح الأساسية."""
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """الصنف الأساسي لكل نماذج SQLAlchemy."""
    def __repr__(self):
        return f"<{self.__class__.__name__}>"


# ── 1. المستخدمون ───────────────────────────────────────────
class User(Base):
    """سجل المستخدم — المصدر الوحيد للحقيقة لـ UUID المستخدم."""
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    telegram_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")  # ACTIVE, SUSPENDED
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    total_capital: Mapped[float] = mapped_column(Float, default=1000.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    emergency_stop: Mapped[bool] = mapped_column(Boolean, default=False)
    consecutive_losses: Mapped[int] = mapped_column(Integer, default=0)
    max_drawdown_limit: Mapped[float] = mapped_column(Float, default=10.0)
    risk_per_trade: Mapped[float] = mapped_column(Float, default=1.0)

    coins: Mapped[list["Coin"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    trades: Mapped[list["Trade"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    positions: Mapped[list["Position"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    portfolio_snapshots: Mapped[list["PortfolioSnapshot"]] = relationship(back_populates="user", cascade="all, delete-orphan")


# ── 2. العملات (الإعداد) ────────────────────────────────────
class Coin(Base):
    """
    إعدادات العملة للمستخدم.
    V4.0: `timeframes` أصبح JSON بدلاً من `timeframe` السابق (قائمة أطر زمنية).
    `capital_allocated` مطلوب من المستخدم — لا قيمة افتراضية.
    """
    __tablename__ = "coins"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    capital_allocated: Mapped[float] = mapped_column(Float, nullable=False)  # إجباري من المستخدم
    risk_per_trade: Mapped[float] = mapped_column(Float, default=1.0)
    timeframes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # قائمة أطر زمنية، افتراضي ["15m"]
    min_entry_size: Mapped[float] = mapped_column(Float, default=0.0)  # الحد الأدنى لحجم الصفقة
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="coins")

    def __init__(self, **kwargs):
        """تهيئة العملة مع ضمان أن timeframes تحتوي على ["15m"] افتراضياً إذا كانت فارغة."""
        super().__init__(**kwargs)
        if not self.timeframes:
            self.timeframes = ["15m"]

    __table_args__ = (
        Index("idx_coins_user_symbol", "user_id", "symbol"),
    )


# ── 3. بيانات السوق (ذاكرة مؤقتة) ─────────────────────────
class MarketData(Base):
    """
    بيانات السوق الخام — مخزنة لكل إطار زمني بشكل منفصل.
    V4.0: أضيف حقل `timeframe` للعزل بين الأطر الزمنية.
    """
    __tablename__ = "market_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False, default="15m")  # عزل الأطر الزمنية
    price: Mapped[float] = mapped_column(Float, default=0.0)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    bid: Mapped[float] = mapped_column(Float, default=0.0)
    ask: Mapped[float] = mapped_column(Float, default=0.0)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_market_data_symbol_tf_ts", "symbol", "timeframe", "timestamp"),
    )


# ── 3b. سجل الشموع (OHLCV Candle Cache) ────────────────────
class CandleCache(Base):
    """
    تخزين الشموع المغلقة — بديل عن Binance REST عند الحظر.
    يُملأ تلقائياً من WebSocket عند إغلاق كل شمعة.
    """
    __tablename__ = "candle_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    open_time: Mapped[int] = mapped_column(BigInteger, nullable=False)  # ms timestamp
    open: Mapped[float] = mapped_column(Float, default=0.0)
    high: Mapped[float] = mapped_column(Float, default=0.0)
    low: Mapped[float] = mapped_column(Float, default=0.0)
    close: Mapped[float] = mapped_column(Float, default=0.0)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    stored_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_candle_cache_sym_tf", "symbol", "timeframe"),
        Index("idx_candle_cache_ts", "open_time"),
        UniqueConstraint("symbol", "timeframe", "open_time", name="uq_candle"),
    )


# ── 4. حالة السوق (النظام) ─────────────────────────────────
class MarketState(Base):
    """
    حالة السوق لكل رمز وإطار زمني.
    V4.0: أضيف حقل `timeframe` مع فهرس مركب.
    """
    __tablename__ = "market_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False, default="15m")  # عزل الأطر الزمنية
    regime: Mapped[str] = mapped_column(String(30), default="UNKNOWN")
    trend_direction: Mapped[str] = mapped_column(String(10), default="NONE")
    trend_strength: Mapped[float] = mapped_column(Float, default=0.0)
    momentum: Mapped[float] = mapped_column(Float, default=0.0)
    volatility: Mapped[float] = mapped_column(Float, default=0.0)
    liquidity_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_market_state_symbol_tf_ts", "symbol", "timeframe", "timestamp"),
    )


# ── 5. الإشارات (إشارات الاستراتيجيات) ─────────────────────
class Signal(Base):
    """
    إشارات التداول من الاستراتيجيات.
    V4.0: أضيف حقل `timeframe` لمعرفة أي إطار زمني أنتج الإشارة.
    """
    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False, default="15m")  # الإطار الزمني المُنتِج للإشارة
    strategy_name: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)  # BUY/SELL/HOLD
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    score_breakdown: Mapped[Optional[dict]] = mapped_column(JSON)
    market_conditions: Mapped[Optional[dict]] = mapped_column(JSON)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    decision_trace: Mapped[Optional["DecisionTrace"]] = relationship(back_populates="signal", uselist=False)

    __table_args__ = (
        Index("idx_signals_symbol_ts", "symbol", "timestamp"),
        Index("idx_signals_strategy", "strategy_name"),
        Index("idx_signals_symbol_tf_ts", "symbol", "timeframe", "timestamp"),
    )


# ── 6. الصفقات (منفَّذة، غير قابلة للتعديل) ────────────────
class Trade(Base):
    """سجل الصفقات المنفَّذة — غير قابل للتعديل بعد الإغلاق."""
    __tablename__ = "trades"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)  # BUY/SELL
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[Optional[float]] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    strategy_used: Mapped[Optional[str]] = mapped_column(String(50))
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN, WON, LOST
    entry_reason: Mapped[Optional[str]] = mapped_column(Text)
    exit_reason: Mapped[Optional[str]] = mapped_column(Text)
    market_conditions: Mapped[Optional[dict]] = mapped_column(JSON)
    opened_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    user: Mapped["User"] = relationship(back_populates="trades")
    risk_events: Mapped[list["RiskEvent"]] = relationship(back_populates="trade", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_trades_user_symbol", "user_id", "symbol"),
        Index("idx_trades_opened_at", "opened_at"),
        Index("idx_trades_status", "status"),
    )


# ── 7. المراكز (مفتوحة) ─────────────────────────────────────
class Position(Base):
    """المراكز المفتوحة حالياً للمستخدم."""
    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), default="BUY")  # BUY / SELL
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[Optional[float]] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float)
    take_profit: Mapped[Optional[float]] = mapped_column(Float)
    trailing_stop: Mapped[Optional[float]] = mapped_column(Float)
    risk_exposure: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    close_reason: Mapped[Optional[str]] = mapped_column(String(50))
    opened_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    user: Mapped["User"] = relationship(back_populates="positions")


# ── 8. أحداث المخاطر ────────────────────────────────────────
class RiskEvent(Base):
    """أحداث إدارة المخاطر المرتبطة بالصفقات."""
    __tablename__ = "risk_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    trade_id: Mapped[str] = mapped_column(String(36), ForeignKey("trades.id"), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    action_taken: Mapped[str] = mapped_column(String(50), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    trade: Mapped["Trade"] = relationship(back_populates="risk_events")


# ── 9. لقطات المحفظة ───────────────────────────────────────
class PortfolioSnapshot(Base):
    """لقطة لحظية لحالة المحفظة."""
    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    total_balance: Mapped[float] = mapped_column(Float, default=0.0)
    available_balance: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    exposure: Mapped[float] = mapped_column(Float, default=0.0)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="portfolio_snapshots")

    __table_args__ = (
        Index("idx_portfolio_user_ts", "user_id", "timestamp"),
    )


# ── 10. أحداث الحيتان ──────────────────────────────────────
class WhaleEvent(Base):
    """تتبع صفقات الحيتان الكبيرة."""
    __tablename__ = "whale_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    value_usd: Mapped[float] = mapped_column(Float, default=0.0)
    direction: Mapped[str] = mapped_column(String(10), default="IN")
    is_market_trade: Mapped[bool] = mapped_column(Boolean, default=False)
    action_label: Mapped[Optional[str]] = mapped_column(String(100))
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_whale_symbol_ts", "symbol", "timestamp"),
    )


# ── 11. أحداث الأخبار ───────────────────────────────────────
class NewsEvent(Base):
    """أخبار السوق وتأثيرها."""
    __tablename__ = "news_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[Optional[str]] = mapped_column(Text)
    source: Mapped[Optional[str]] = mapped_column(String(100))
    impact_score: Mapped[float] = mapped_column(Float, default=0.0)
    related_symbols: Mapped[Optional[list]] = mapped_column(JSON)
    sentiment: Mapped[str] = mapped_column(String(20), default="NEUTRAL")
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ── 12. إحصائيات الاستراتيجيات ─────────────────────────────
class StrategyStat(Base):
    """إحصائيات أداء الاستراتيجيات لكل رمز وإطار زمني."""
    __tablename__ = "strategy_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_name: Mapped[str] = mapped_column(String(50), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    avg_profit: Mapped[float] = mapped_column(Float, default=0.0)
    avg_loss: Mapped[float] = mapped_column(Float, default=0.0)
    drawdown: Mapped[float] = mapped_column(Float, default=0.0)
    total_trades: Mapped[int] = mapped_column(Integer, default=0)
    timeframe: Mapped[str] = mapped_column(String(10), default="15m")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_strategy_stats_name", "strategy_name"),
    )


# ── 13. سجلات النظام (مسار التدقيق) ────────────────────────
class SystemLog(Base):
    """سجل تدقيق لكل أحداث النظام."""
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    module: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[Optional[dict]] = mapped_column(JSON)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_logs_level_ts", "level", "timestamp"),
        Index("idx_logs_module", "module"),
    )


# ── 14. أثر القرار ──────────────────────────────────────────
class DecisionTrace(Base):
    """تتبع مسار القرار من الإشارة إلى التنفيذ."""
    __tablename__ = "decision_trace"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    signal_id: Mapped[str] = mapped_column(String(36), ForeignKey("signals.id"), nullable=False)
    evidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    final_decision: Mapped[str] = mapped_column(String(10), nullable=False)
    reasoning: Mapped[Optional[str]] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    signal: Mapped["Signal"] = relationship(back_populates="decision_trace")
