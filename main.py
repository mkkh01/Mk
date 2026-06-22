"""
نقطة الدخول الرئيسية — تنسيق بدء التشغيل بدون أي منطق تجاري.
V4.0: تسلسل صارم، فشل مبكر، صحة مشتقة من الحالة الفعلية.
"""
import asyncio
import logging
import sys
import os
import traceback
from datetime import datetime, timezone

def _utcnow():
    """تُرجع datetime.now(tz=timezone.utc) — متوافقة مع Python 3.9+ كبديل لـ utcnow()."""
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
#  الهجرة التلقائية (Auto-Migration)
# ═══════════════════════════════════════════════════════════════

_MIGRATIONS = {
    "coins": {
        # (اسم العمود, نوع SQL, القيمة الافتراضية)
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
    """
    فحص الأعمدة المفقودة وإضافتها تلقائياً.
    لا تلمس الأعمدة الموجودة. لا تحذف شيئاً.
    تُرجع: (نجاح, قائمة الأعمدة المضافة)
    """
    from sqlalchemy import text
    from database.repositories import _engine

    if _engine is None:
        return False, []

    added = []
    try:
        async with _engine.connect() as conn:
            for table, columns in _MIGRATIONS.items():
                # جلب الأعمدة الحالية
                result = await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = :tname"
                    ),
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
#  نظام فحص ما قبل التشغيل (Pre-Flight Checks)
# ═══════════════════════════════════════════════════════════════

async def preflight_check_schema() -> tuple[bool, str]:
    """التحقق من تطابق الـ schema باستخدام استعلامات SQL خالصة (متوافقة مع async)."""
    try:
        from sqlalchemy import text
        from database.repositories import _engine

        if _engine is None:
            return False, "محرك قاعدة البيانات غير مهيأ"

        async with _engine.connect() as conn:
            # التحقق من وجود الجداول الأساسية
            required_tables = ["users", "coins", "trades", "positions",
                              "market_data", "market_state", "signals",
                              "risk_events", "portfolio_snapshots", "logs"]

            for table in required_tables:
                result = await conn.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                        "WHERE table_name = :tname)"
                    ),
                    {"tname": table},
                )
                exists = result.scalar()
                if not exists:
                    return False, f"الجدول مفقود: {table}"

            # التحقق من الأعمدة المطلوبة في جدول coins
            required_coins_columns = [
                "id", "user_id", "symbol", "capital_allocated",
                "risk_per_trade", "timeframes", "is_active"
            ]
            result = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'coins'"
                )
            )
            coins_columns = {row[0] for row in result}
            for col in required_coins_columns:
                if col not in coins_columns:
                    return False, f"العمود مفقود في جدول coins: {col}"

            # التحقق من الأعمدة المطلوبة في جدول users
            required_users_columns = ["id", "telegram_id", "total_capital", "is_active"]
            result = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'users'"
                )
            )
            users_columns = {row[0] for row in result}
            for col in required_users_columns:
                if col not in users_columns:
                    return False, f"العمود مفقود في جدول users: {col}"

        return True, "المخطط متطابق"
    except Exception as e:
        return False, f"فشل فحص المخطط: {e}"


async def preflight_check_exchange() -> tuple[bool, str]:
    """التحقق من الاتصال بـ Binance (REST API)."""
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
    """التحقق من صلاحية توكن تيليجرام."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = resp.json()
            if data.get("ok"):
                bot_name = data["result"]["username"]
                return True, f"بوت تيليجرام: @{bot_name}"
            return False, f"توكن تيليجرام غير صالح: {data.get('description', '')}"
    except Exception as e:
        return False, f"فشل الاتصال بـ Telegram API: {e}"


# ═══════════════════════════════════════════════════════════════
#  حالة النظام العالمية
# ═══════════════════════════════════════════════════════════════

_system_state = {
    "status": "بدء_التشغيل",
    "started_at": None,
    "errors": [],
    "preflight": {},
    "engines": {},
}


def set_system_status(status: str):
    _system_state["status"] = status
    logger.info(f"[النظام] الحالة ← {status}")


def record_error(component: str, error: str):
    _system_state["errors"].append({"component": component, "error": error, "time": _utcnow().isoformat()})
    logger.error(f"[النظام] ❌ {component}: {error}")


def get_system_health() -> str:
    """صحة النظام مشتقة من الحالة الفعلية — ليست ثابتة."""
    if _system_state["errors"]:
        critical = [e for e in _system_state["errors"] if "فشل" in e["error"] or "حرج" in e["error"]]
        if critical:
            return "متدهورة"
        return "تحذير"
    if _system_state["status"] == "يعمل":
        return "صحيحة"
    return _system_state["status"]


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
    _system_state["started_at"] = _utcnow()
    logger.info("═" * 50)
    logger.info("[النظام] بدء تشغيل CT V4.0")
    logger.info("═" * 50)

    # ── 1. تحميل الإعدادات ──────────────────────────────────
    set_system_status("تحميل_الإعدادات")
    logger.info("[النظام] [1/10] تحميل الإعدادات...")
    try:
        settings = get_settings()
        missing = settings.validate()
        if missing:
            record_error("الإعدادات", f"متغيرات مفقودة: {missing}")
            logger.critical(f"[النظام] ❌ فشل التحقق من الإعدادات. مطلوب: {missing}")
            sys.exit(1)
        logger.info("[الإعدادات] ✅ تم تحميل الإعدادات")
    except Exception as e:
        record_error("الإعدادات", f"فشل التحميل: {e}")
        logger.critical(f"[النظام] ❌ فشل تحميل الإعدادات: {e}", exc_info=True)
        sys.exit(1)

    # ── 2. Keep-Alive ───────────────────────────────────────
    set_system_status("بدء_الخادم")
    keep_alive()
    logger.info(f"[النظام] [2/10] خادم keep-alive بدأ على المنفذ {settings.port}")

    # ── 3. Event Bus ────────────────────────────────────────
    event_bus = EventBus()
    logger.info("[النظام] [3/10] ناقل الأحداث جاهز")

    # ── 4. قاعدة البيانات + فحص المخطط ─────────────────────
    set_system_status("الاتصال_بقاعدة_البيانات")
    logger.info("[النظام] [4/10] الاتصال بقاعدة البيانات...")
    try:
        await init_db()
        logger.info("[قاعدة البيانات] ✅ تم الاتصال بقاعدة البيانات")
    except Exception as e:
        record_error("قاعدة البيانات", f"فشل الاتصال: {e}")
        logger.critical(f"[قاعدة البيانات] ❌ فشل الاتصال: {e}", exc_info=True)
        sys.exit(1)

    # تشغيل الهجرة التلقائية قبل فحص المخطط
    logger.info("[النظام] تشغيل الهجرة التلقائية...")
    migrate_ok, added_cols = await auto_migrate()
    if not migrate_ok:
        record_error("الهجرة", "فشل الهجرة التلقائية")
        logger.warning("[الهجرة] ⚠️ فشل الهجرة — متابعة مع فحص المخطط")
    elif added_cols:
        logger.info(f"[الهجرة] أعمدة مضافة: {', '.join(added_cols)}")
    else:
        logger.info("[الهجرة] المخطط محدث — لا أعمدة مفقودة")

    # فحص المخطط بعد الهجرة
    logger.info("[النظام] فحص تطابق المخطط...")
    schema_ok, schema_msg = await preflight_check_schema()
    if not schema_ok:
        record_error("المخطط", schema_msg)
        logger.critical(f"[المخطط] ❌ {schema_msg}")
        logger.critical("[النظام] ❌ فشل فحص المخطط — توقف.")
        sys.exit(1)
    logger.info(f"[المخطط] ✅ {schema_msg}")
    _system_state["preflight"]["schema"] = "متطابق"

    # ── 5. فحص الاتصالات الخارجية ──────────────────────────
    set_system_status("فحص_الاتصالات")
    logger.info("[النظام] [5/10] فحص الاتصالات الخارجية...")

    exchange_ok, exchange_msg = await preflight_check_exchange()
    if exchange_ok:
        logger.info(f"[اتصال] ✅ {exchange_msg}")
        _system_state["preflight"]["exchange"] = "متصل"
    else:
        logger.warning(f"[اتصال] ⚠️ {exchange_msg}")
        _system_state["preflight"]["exchange"] = exchange_msg
        # لا نتوقف — Market Data Engine يستخدم WebSocket وقد ينجح

    telegram_ok, telegram_msg = await preflight_check_telegram(settings.telegram_token)
    if telegram_ok:
        logger.info(f"[اتصال] ✅ {telegram_msg}")
        _system_state["preflight"]["telegram"] = "متصل"
    else:
        record_error("تيليجرام", telegram_msg)
        logger.critical(f"[اتصال] ❌ {telegram_msg}")
        logger.critical("[النظام] ❌ توكن تيليجرام غير صالح — توقف.")
        sys.exit(1)

    # ── 6. بدء المحركات ────────────────────────────────────
    set_system_status("بدء_المحركات")
    logger.info("[النظام] [6/10] بدء المحركات الأساسية...")

    # Config + Logging
    config_engine = ConfigEngine()
    await config_engine.initialize()
    await config_engine.start()

    logging_engine = LoggingEngine()
    await logging_engine.initialize()
    await logging_engine.start()

    # Market Data Engine
    market_data_engine = MarketDataEngine(event_bus)
    await market_data_engine.initialize()
    await market_data_engine.start()
    logger.info("[محرك] ✅ بيانات السوق بدأ")

    # Market Analyzer
    market_analyzer = MarketAnalyzer(event_bus)
    await market_analyzer.initialize()
    await market_analyzer.start()
    logger.info("[محرك] ✅ محلل السوق بدأ")

    # Strategy Engine
    strategy_engine = StrategyEngine(event_bus)
    await strategy_engine.initialize()
    await strategy_engine.start()
    logger.info("[محرك] ✅ محرك الاستراتيجيات بدأ")

    # ── 7. المحركات المتقدمة ───────────────────────────────
    logger.info("[النظام] [7/10] بدء المحركات المتقدمة...")

    evidence_engine = EvidenceEngine(event_bus)
    await evidence_engine.initialize()
    await evidence_engine.start()

    risk_engine = RiskEngine(event_bus)
    await risk_engine.initialize()
    await risk_engine.start()

    execution_engine = ExecutionEngine(event_bus)
    await execution_engine.initialize()
    await execution_engine.start()

    portfolio_engine = PortfolioEngine(event_bus, initial_balance=settings.default_capital)
    await portfolio_engine.initialize()
    await portfolio_engine.start()

    learning_engine = LearningEngine(event_bus)
    await learning_engine.initialize()
    await learning_engine.start()

    reporting_engine = ReportingEngine(event_bus)
    await reporting_engine.initialize()
    await reporting_engine.start()

    health_monitor = HealthMonitor(event_bus)
    await health_monitor.initialize()
    await health_monitor.start()

    logger.info("[النظام] ✅ جميع المحركات الـ 14 بدأت")

    # ── 8. الخدمات ─────────────────────────────────────────
    set_system_status("بدء_الخدمات")
    logger.info("[النظام] [8/10] بدء الخدمات...")

    telegram_id = settings.admin_id
    execution_engine._admin_telegram_id = telegram_id
    portfolio_engine._telegram_id = telegram_id
    learning_engine.user_id = str(telegram_id)

    analysis_service = AnalysisService(market_data_engine, market_analyzer, strategy_engine)
    trading_service = TradingService(
        evidence_engine, risk_engine, execution_engine,
        market_analyzer, strategy_engine, market_data_engine,
        analysis_service
    )
    portfolio_service = PortfolioService(
        portfolio_engine, reporting_engine, learning_engine, health_monitor
    )
    risk_service = RiskService(risk_engine)

    logger.info("[النظام] ✅ جميع الخدمات بدأت")

    # ── 9. مزامنة العملات ──────────────────────────────────
    set_system_status("مزامنة_العملات")
    logger.info("[النظام] [9/10] مزامنة العملات من قاعدة البيانات...")

    symbols, coins = await analysis_service.sync_symbols_from_db(str(telegram_id))
    logger.info(f"[مزامنة] ✅ تم تحميل {len(symbols)} عملة")

    for coin in coins:
        tfs = coin.timeframes if isinstance(coin.timeframes, list) else [coin.timeframes]
        logger.info(f"[عملة] {coin.symbol} | أطر: {', '.join(tfs)} | رأس مال: {coin.capital_allocated:.2f}")

    _system_state["engines"]["market_data"] = "يعمل" if symbols else "ينتظر"
    _system_state["preflight"]["coins_loaded"] = len(symbols)

    # ── 10. بوت تيليجرام + حلقة التداول ────────────────────
    set_system_status("بدء_البوت")
    logger.info("[النظام] [10/10] بدء بوت تيليجرام...")

    telegram_engine = TelegramEngine(
        token=settings.telegram_token,
        admin_id=settings.admin_id,
        analysis_service=analysis_service,
        trading_service=trading_service,
        portfolio_service=portfolio_service,
        risk_service=risk_service,
    )

    # ── حلقة التداول ───────────────────────────────────────
    async def trading_loop():
        """حلقة تداول دورية — تعالج كل العملات بكل أطرها الزمنية."""
        from database.repositories import CoinRepository, TradeRepository, PositionRepository, get_session
        cycle = 0

        async def notify_tg(text: str):
            """إرسال إشعار للمستخدم عبر تيليجرام."""
            try:
                if telegram_engine and telegram_engine.application:
                    await telegram_engine.send_message(telegram_id, text)
            except Exception as e:
                logger.debug(f"[إشعار] فشل الإرسال: {e}")

        while True:
            cycle += 1
            cycle_start = _utcnow()

            try:
                trading_allowed = (
                    health_monitor.is_trading_safe() and
                    risk_service.is_trading_allowed()
                )

                if not trading_allowed:
                    logger.warning(
                        f"[دورة #{cycle}] ⛔ التداول معلق "
                        f"(الصحة: {health_monitor.system_state})"
                    )
                    await asyncio.sleep(30)
                    continue

                async for session in get_session():
                    coins = await CoinRepository.get_all_active(session, telegram_id)

                    # ── مراقبة المراكز المفتوحة (TP/SL) ──
                    open_positions = await PositionRepository.get_open(session, telegram_id)
                    for pos in open_positions:
                        try:
                            analysis = await market_analyzer.analyze(pos.symbol, "1m")
                            if not analysis:
                                continue
                            current_price = analysis.get("close", analysis.get("price", 0))
                            if current_price <= 0:
                                continue

                            # فحص TP/SL
                            if pos.side == "BUY" or pos.side == "LONG":
                                hit_tp = pos.take_profit and current_price >= pos.take_profit
                                hit_sl = pos.stop_loss and current_price <= pos.stop_loss
                            else:  # SELL / SHORT
                                hit_tp = pos.take_profit and current_price <= pos.take_profit
                                hit_sl = pos.stop_loss and current_price >= pos.stop_loss

                            if hit_tp or hit_sl:
                                pnl = (current_price - pos.entry_price) / pos.entry_price * 100
                                if pos.side in ("SELL", "SHORT"):
                                    pnl = -pnl
                                reason = "🎯 هدف" if hit_tp else "🛑 وقف خسارة"
                                emoji = "🟢" if pnl > 0 else "🔴"

                                await PositionRepository.close_position(
                                    session, pos, exit_price=current_price, reason=reason
                                )

                                msg = (
                                    f"{emoji} {reason}\n"
                                    f"━━━━━━━━━━━━━━\n"
                                    f"💰 {pos.symbol}\n"
                                    f"📊 السعر: {current_price:.6f}\n"
                                    f"📈 الدخول: {pos.entry_price:.6f}\n"
                                    f"💵 الربح/الخسارة: {pnl:+.2f}%\n"
                                    f"📦 الكمية: {pos.quantity:.4f}\n"
                                )
                                logger.info(
                                    f"[مركز] {pos.symbol} | {reason} | "
                                    f"سعر={current_price:.6f} | PnL={pnl:+.2f}%"
                                )
                                await notify_tg(msg)
                        except Exception as e:
                            logger.debug(f"[مركز] {pos.symbol} خطأ فحص TP/SL: {e}")

                    if not coins:
                        if cycle % 10 == 0:  # كل 10 دورات فقط
                            logger.info(f"[دورة #{cycle}] لا عملات نشطة — انتظار")
                        await asyncio.sleep(1)
                        break

                    # ── فحص الأسعار ──
                    price_lines = []
                    signals_found = 0
                    analysis_ok = 0
                    analysis_miss = 0

                    for coin in coins:
                        tfs = coin.timeframes if isinstance(coin.timeframes, list) else [coin.timeframes]

                        # جمع السعر الحالي لكل إطار
                        coin_prices = {}
                        for tf in tfs:
                            try:
                                analysis = await market_analyzer.analyze(coin.symbol, tf)
                                if analysis:
                                    price = analysis.get("close", analysis.get("price", 0))
                                    coin_prices[tf] = price
                                    analysis_ok += 1
                                    await strategy_engine.run_strategies(coin.symbol, tf, analysis)
                                else:
                                    analysis_miss += 1
                            except Exception as e:
                                analysis_miss += 1
                                logger.debug(f"[{coin.symbol}] [{tf}] خطأ تحليل: {e}")

                        # سطر سعر واحد لكل عملة
                        if coin_prices:
                            price_str = " | ".join(f"{tf}: {p:.4f}" for tf, p in sorted(coin_prices.items()))
                            price_lines.append(f"  {coin.symbol:<10} {price_str}")

                        # سطر سعر واحد لكل عملة
                        if coin_prices:
                            price_str = " | ".join(f"{tf}: {p:.4f}" for tf, p in sorted(coin_prices.items()))
                            price_lines.append(f"  {coin.symbol:<10} {price_str}")

                        # فحص إشارات التداول
                        try:
                            result = await trading_service.process_symbol(
                                coin.symbol, telegram_id
                            )
                            if result:
                                evidence, risk_decision, execution = result
                                signals_found += 1
                                if execution:
                                    logger.info(
                                        f"[صفقة #{signals_found}] {coin.symbol} | "
                                        f"{evidence.decision} | ثقة: {evidence.final_score:.0f}% | "
                                        f"كمية: {execution.executed_quantity:.6f} | "
                                        f"سعر: {execution.executed_price:.6f}"
                                    )
                                    # ── إشعار فوري بفتح الصفقة ──
                                    tp = getattr(execution, 'take_profit', None)
                                    sl = getattr(execution, 'stop_loss', None)
                                    tp_str = f"{tp:.6f}" if tp else "—"
                                    sl_str = f"{sl:.6f}" if sl else "—"
                                    order_msg = (
                                        f"🔔 **صفقة جديدة**\n"
                                        f"━━━━━━━━━━━━━━\n"
                                        f"💰 {coin.symbol}\n"
                                        f"📊 {evidence.decision}\n"
                                        f"💵 السعر: {execution.executed_price:.6f}\n"
                                        f"📦 الكمية: {execution.executed_quantity:.4f}\n"
                                        f"🎯 هدف: {tp_str}\n"
                                        f"🛑 وقف: {sl_str}\n"
                                        f"✅ ثقة: {evidence.final_score:.0f}%\n"
                                        f"🧠 السبب: {evidence.reasoning[:100]}\n"
                                    )
                                    await notify_tg(order_msg)
                                else:
                                    logger.info(
                                        f"[إشارة #{signals_found}] {coin.symbol} | "
                                        f"{evidence.decision} | ثقة: {evidence.final_score:.0f}% | "
                                        f"مرفوضة: {evidence.reasoning[:60]}"
                                    )
                        except Exception as e:
                            logger.debug(f"[{coin.symbol}] خطأ تداول: {e}")

                        await asyncio.sleep(0.5)

                    # ── تقرير الدورة ──
                    duration = (_utcnow() - cycle_start).total_seconds()
                    data_status = f"تحليلات: {analysis_ok}" if analysis_ok > 0 else "⏳ بلا بيانات"
                    summary = (
                        f"[دورة #{cycle}] {len(coins)} عملة | "
                        f"{data_status} | "
                        f"إشارات: {signals_found} | "
                        f"{duration:.1f}ث"
                    )
                    logger.info(summary)
                    if analysis_miss > analysis_ok and cycle <= 3:
                        logger.warning(
                            f"[دورة #{cycle}] ⚠️ {analysis_miss} تحليل فشل — "
                            f"قد تكون بيانات السوق لم تصل بعد (WebSocket يحتاج وقتاً)"
                        )
                    if price_lines:
                        logger.info(f"[أسعار #{cycle}]\n" + "\n".join(price_lines))

            except Exception as e:
                logger.error(f"[دورة #{cycle}] ❌ خطأ: {e}", exc_info=True)

            await asyncio.sleep(120)

    asyncio.create_task(trading_loop())
    logger.info("[النظام] حلقة التداول بدأت (دورة كل دقيقتين)")

    # ── النظام جاهز ────────────────────────────────────────
    set_system_status("يعمل")
    logger.info("═" * 50)
    logger.info("[النظام] ✅ النظام جاهز — جميع المحركات والخدمات تعمل")
    logger.info(f"[النظام] حالة النظام: {get_system_health()}")
    logger.info("═" * 50)

    # ── تشغيل بوت تيليجرام (مانع) ──────────────────────────
    try:
        await telegram_engine.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("[النظام] إشارة إيقاف")
    except Exception as e:
        record_error("البوت", f"فشل تشغيل البوت: {e}")
        logger.critical(f"[النظام] ❌ فشل تشغيل بوت تيليجرام: {e}", exc_info=True)
    finally:
        # ── إيقاف تدريجي ───────────────────────────────────
        logger.info("[النظام] بدء الإيقاف التدريجي...")
        set_system_status("إيقاف")

        shutdown_order = [
            ("البوت", telegram_engine.stop),
            ("مراقب الصحة", health_monitor.stop),
            ("التقارير", reporting_engine.stop),
            ("التعلم", learning_engine.stop),
            ("المحفظة", portfolio_engine.stop),
            ("التنفيذ", execution_engine.stop),
            ("المخاطر", risk_engine.stop),
            ("الأدلة", evidence_engine.stop),
            ("الاستراتيجيات", strategy_engine.stop),
            ("محلل السوق", market_analyzer.stop),
            ("بيانات السوق", market_data_engine.stop),
            ("السجلات", logging_engine.stop),
            ("الإعدادات", config_engine.stop),
        ]

        for name, stop_fn in shutdown_order:
            try:
                await stop_fn()
                logger.info(f"[إيقاف] {name} توقف")
            except Exception as e:
                logger.error(f"[إيقاف] {name} خطأ: {e}")

        await close_db()
        logger.info("[إيقاف] قاعدة البيانات أغلقت")
        logger.info("═" * 50)
        logger.info("[النظام] ✅ إيقاف كامل — وداعاً")
        logger.info("═" * 50)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
