"""
المدخل الرئيسي — تهيئة النظام، بدء المحركات، تشغيل البوت.
لا يحتوي على منطق أعمال — تنسيق خالص.
جميع السجلات بالعربية. لا قيم افتراضية — كل شيء من DB أو engine state.
"""
import asyncio
import logging
import sys
import os
from datetime import datetime

# ── السجلات المنظمة ────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("النظام")

# ── الأساسيات ──────────────────────────────────────────────
from core.events import EventBus
from core.types import SystemState

# ── الإعدادات ──────────────────────────────────────────────
from config.settings import get_settings
from config.constants import ADMIN_ID, SYSTEM_NAME

# ── قاعدة البيانات ─────────────────────────────────────────
from database.repositories import init_db, close_db

# ── المحركات ───────────────────────────────────────────────
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

# ── الخدمات ────────────────────────────────────────────────
from services.analysis_service import AnalysisService
from services.trading_service import TradingService
from services.portfolio_service import PortfolioService
from services.risk_service import RiskService

# ── البوت ──────────────────────────────────────────────────
from bots.telegram.bot import TelegramEngine

# ── خادم الإبقاء ───────────────────────────────────────────
from keep_alive import keep_alive


def print_banner():
    """عرض شعار بدء التشغيل."""
    print()
    print("╔══════════════════════════════════════╗")
    print(f"║  {SYSTEM_NAME}  ║")
    print("║  Clean Architecture | محركات | خدمات ║")
    print("╚══════════════════════════════════════╝")
    print()


async def main():
    """تنسيق بدء تشغيل النظام بالكامل."""
    print_banner()

    logger.info("═" * 40)
    logger.info(f"[النظام] بدء تشغيل {SYSTEM_NAME}")
    logger.info(f"[النظام] {datetime.utcnow().isoformat()} UTC")
    logger.info("═" * 40)

    # ═════════════════════════════════════════════════════════
    # [1/10] تحميل الإعدادات
    # ═════════════════════════════════════════════════════════
    logger.info("[النظام] [1/10] تحميل الإعدادات...")
    settings = get_settings()
    missing = settings.validate()
    if missing:
        logger.critical(f"[الإعدادات] ❌ متغيرات البيئة المفقودة: {missing}")
        sys.exit(1)
    logger.info("[الإعدادات] ✅ تم تحميل الإعدادات")
    logger.debug(f"[الإعدادات] مدير النظام: {settings.admin_id}")
    logger.debug(f"[الإعدادات] المنفذ: {settings.port}")

    config_engine = ConfigEngine()
    await config_engine.initialize()
    await config_engine.start()
    logger.info("[الإعدادات] ✅ محرك الإعدادات جاهز")

    # ═════════════════════════════════════════════════════════
    # [2/10] بدء خادم keep-alive
    # ═════════════════════════════════════════════════════════
    logger.info("[النظام] [2/10] بدء خادم الإبقاء...")
    keep_alive()
    logger.info(f"[خادم] ✅ خادم الإبقاء يعمل على المنفذ {settings.port}")

    # ═════════════════════════════════════════════════════════
    # [3/10] تهيئة ناقل الأحداث
    # ═════════════════════════════════════════════════════════
    logger.info("[النظام] [3/10] تهيئة ناقل الأحداث...")
    event_bus = EventBus()
    logger.info("[أحداث] ✅ ناقل الأحداث جاهز")

    # ═════════════════════════════════════════════════════════
    # [4/10] الاتصال بقاعدة البيانات
    # ═════════════════════════════════════════════════════════
    logger.info("[النظام] [4/10] الاتصال بقاعدة البيانات...")
    try:
        await init_db()
        logger.info("[قاعدة البيانات] ✅ تم الاتصال")
    except Exception as e:
        logger.critical(f"[قاعدة البيانات] ❌ فشل الاتصال: {e}", exc_info=True)
        sys.exit(1)

    # محرك السجلات
    logging_engine = LoggingEngine()
    await logging_engine.initialize()
    await logging_engine.start()
    logger.info("[سجلات] ✅ محرك السجلات جاهز")

    # ═════════════════════════════════════════════════════════
    # [5/10] بدء محرك بيانات السوق
    # ═════════════════════════════════════════════════════════
    logger.info("[النظام] [5/10] بدء محرك بيانات السوق...")
    market_data_engine = MarketDataEngine(event_bus)
    await market_data_engine.initialize()
    await market_data_engine.start()
    logger.info("[بيانات السوق] ✅ المحرك يعمل")

    # ═════════════════════════════════════════════════════════
    # [6/10] بدء محرك التحليل
    # ═════════════════════════════════════════════════════════
    logger.info("[النظام] [6/10] بدء محرك التحليل...")
    market_analyzer = MarketAnalyzer(event_bus)
    await market_analyzer.initialize()
    await market_analyzer.start()
    logger.info("[تحليل] ✅ المحلل يعمل")

    # ═════════════════════════════════════════════════════════
    # [7/10] بدء محركات الاستراتيجيات
    # ═════════════════════════════════════════════════════════
    logger.info("[النظام] [7/10] بدء محركات الاستراتيجيات...")
    strategy_engine = StrategyEngine(event_bus)
    await strategy_engine.initialize()
    await strategy_engine.start()
    logger.info("[استراتيجيات] ✅ المحرك يعمل")

    # ═════════════════════════════════════════════════════════
    # [8/10] بدء محركات الأدلة والمخاطر
    # ═════════════════════════════════════════════════════════
    logger.info("[النظام] [8/10] بدء محركات الأدلة والمخاطر...")
    evidence_engine = EvidenceEngine(event_bus)
    await evidence_engine.initialize()
    await evidence_engine.start()
    logger.info("[أدلة] ✅ محرك الأدلة يعمل")

    risk_engine = RiskEngine(event_bus)
    await risk_engine.initialize()
    await risk_engine.start()
    logger.info("[مخاطر] ✅ محرك المخاطر يعمل")

    # محرك التنفيذ (وضع المحاكاة فقط)
    execution_engine = ExecutionEngine(event_bus)
    await execution_engine.initialize()
    await execution_engine.start()
    logger.info("[تنفيذ] ✅ محرك التنفيذ يعمل (وضع المحاكاة)")

    # محرك المحفظة — الرصيد الابتدائي من settings (انتقالي لحين قراءته من DB)
    initial_balance = settings.default_capital
    portfolio_engine = PortfolioEngine(event_bus, initial_balance=initial_balance)
    await portfolio_engine.initialize()
    await portfolio_engine.start()
    logger.info(f"[محفظة] ✅ المحفظة جاهزة — الرصيد الابتدائي: {initial_balance:.2f}")

    # محرك التعلم
    learning_engine = LearningEngine(event_bus)
    await learning_engine.initialize()
    await learning_engine.start()
    logger.info("[تعلم] ✅ محرك التعلم يعمل")

    # محرك التقارير
    reporting_engine = ReportingEngine(event_bus)
    await reporting_engine.initialize()
    await reporting_engine.start()
    logger.info("[تقارير] ✅ محرك التقارير يعمل")

    # مراقب الصحة
    health_monitor = HealthMonitor(event_bus)
    await health_monitor.initialize()
    await health_monitor.start()
    logger.info("[صحة] ✅ مراقب الصحة يعمل")

    # ═════════════════════════════════════════════════════════
    #  إنشاء الخدمات
    # ═════════════════════════════════════════════════════════
    analysis_service = AnalysisService(market_data_engine, market_analyzer, strategy_engine)

    trading_service = TradingService(
        evidence_engine, risk_engine, execution_engine,
        market_analyzer, strategy_engine, market_data_engine,
        analysis_service,
    )

    portfolio_service = PortfolioService(
        portfolio_engine, reporting_engine, learning_engine, health_monitor
    )

    risk_service = RiskService(risk_engine)
    logger.info("[خدمات] ✅ جميع الخدمات الأربع جاهزة")

    # ═════════════════════════════════════════════════════════
    #  تعيين هوية المستخدم
    # ═════════════════════════════════════════════════════════
    telegram_id: int = settings.admin_id
    execution_engine._telegram_id = telegram_id
    portfolio_engine._telegram_id = telegram_id
    learning_engine.user_id = str(telegram_id)
    logger.info(f"[هوية] معرف تليجرام: {telegram_id}")

    # ═════════════════════════════════════════════════════════
    # [9/10] مزامنة العملات من قاعدة البيانات
    # ═════════════════════════════════════════════════════════
    logger.info("[النظام] [9/10] مزامنة العملات من قاعدة البيانات...")
    symbols, coins = await analysis_service.sync_symbols_from_db(telegram_id)

    if coins:
        # عرض تفاصيل العملات المحملة
        all_tfs: set[str] = set()
        for coin in coins:
            tfs = getattr(coin, 'timeframes', ["15m"])
            if not tfs:
                tfs = ["15m"]
            if isinstance(tfs, str):
                tfs = [tfs]
            for tf in tfs:
                all_tfs.add(str(tf))
            logger.info(
                f"[عملة] {coin.symbol} | "
                f"الأطر: {', '.join(str(t) for t in tfs)} | "
                f"رأس المال: {coin.allocated_capital:.2f}"
            )
        logger.info(
            f"[مزامنة] ✅ تم تحميل {len(symbols)} عملة | "
            f"الأطر الزمنية: {', '.join(sorted(all_tfs)) if all_tfs else '15m'}"
        )
    else:
        logger.warning("[مزامنة] ⚠️ لا توجد عملات نشطة — أضف عملات عبر البوت")
        logger.info("[مزامنة] الأطر الزمنية: 15m, 1h")

    portfolio_service.mark_synced()

    # ═════════════════════════════════════════════════════════
    # [10/10] بدء بوت تيليجرام
    # ═════════════════════════════════════════════════════════
    logger.info("[النظام] [10/10] بدء بوت تيليجرام...")
    telegram_engine = TelegramEngine(
        token=settings.telegram_token,
        admin_id=settings.admin_id,
        analysis_service=analysis_service,
        trading_service=trading_service,
        portfolio_service=portfolio_service,
        risk_service=risk_service,
    )
    logger.info("[بوت] ✅ محرك البوت جاهز")

    # ═════════════════════════════════════════════════════════
    #  حلقة التداول
    # ═════════════════════════════════════════════════════════

    async def trading_loop():
        """
        دورة تداول دورية — معالجة جميع العملات النشطة.
        لكل دورة:
          1. تحميل العملات النشطة من DB
          2. لكل عملة: تحليل جميع الأطر الزمنية
          3. لكل عملة: معالجة الصفقة (تجميع الإشارات ← الأدلة ← المخاطر ← التنفيذ)
        """
        from database.repositories import CoinRepository, get_session

        cycle = 0
        while True:
            cycle += 1
            cycle_start = datetime.utcnow()
            try:
                # التحقق من السماح بالتداول
                trading_allowed = (
                    health_monitor.is_trading_safe() and
                    risk_service.is_trading_allowed()
                )

                if not trading_allowed:
                    risk_status = risk_service.get_risk_status()
                    logger.debug(
                        f"[الدورة #{cycle}] ⛔ التداول متوقف: "
                        f"صحة={health_monitor.system_state} | "
                        f"مخاطر={risk_status.get('block_reason', 'محظور')}"
                    )
                else:
                    # تحميل العملات النشطة
                    active_coins = []
                    try:
                        async for session in get_session():
                            active_coins = await CoinRepository.get_all_active(
                                session, telegram_id
                            )
                    except Exception as e:
                        logger.error(f"[الدورة #{cycle}] ❌ خطأ في تحميل العملات: {e}")
                        await asyncio.sleep(60)
                        continue

                    if not active_coins:
                        logger.debug(f"[الدورة #{cycle}] ⏸️ لا توجد عملات نشطة")
                    else:
                        logger.info(
                            f"[الدورة #{cycle}] 🔄 معالجة {len(active_coins)} عملة..."
                        )

                        for coin in active_coins:
                            symbol = coin.symbol

                            # قراءة الأطر الزمنية للعملة
                            timeframes = getattr(coin, 'timeframes', ["15m"])
                            if not timeframes:
                                timeframes = ["15m"]
                            if isinstance(timeframes, str):
                                timeframes = [timeframes]
                            tfs_str = [str(t) for t in timeframes]

                            try:
                                # ── تحليل جميع الأطر الزمنية ──────────────
                                logger.debug(
                                    f"[{symbol}] 🔍 تحليل {len(tfs_str)} أطر زمنية: "
                                    f"{', '.join(tfs_str)}"
                                )

                                for tf in tfs_str:
                                    logger.debug(f"[{symbol}] [{tf}] جلب البيانات...")
                                    logger.debug(f"[{symbol}] [{tf}] تحديث المؤشرات...")
                                    logger.debug(f"[{symbol}] [{tf}] تشغيل الاستراتيجيات...")
                                    logger.debug(f"[{symbol}] [{tf}] تحليل الإشارات...")

                                # تشغيل دورة التحليل الكاملة
                                await analysis_service.run_full_analysis_cycle(symbol)

                                # ── معالجة الصفقة ─────────────────────────
                                result = await trading_service.process_symbol(
                                    symbol, telegram_id
                                )

                                if result:
                                    evidence, risk_decision, execution = result
                                    if execution:
                                        logger.info(
                                            f"[{symbol}] ✅ صفقة منفذة: "
                                            f"{execution.symbol} | "
                                            f"السعر={execution.entry_price:.2f} | "
                                            f"الكمية={execution.executed_quantity:.6f}"
                                        )
                                else:
                                    logger.debug(
                                        f"[{symbol}] ⏭️ لم تنتج الصفقة — "
                                        f"لا توجد إشارات كافية"
                                    )

                            except Exception as e:
                                logger.error(
                                    f"[{symbol}] ❌ خطأ في المعالجة: {e}",
                                    exc_info=True
                                )

                            # تأخير بسيط بين العملات
                            await asyncio.sleep(0.5)

                        # تحديث إحصائيات خدمة المحفظة
                        trading_status = trading_service.get_status()
                        portfolio_service.update_signal_stats(
                            processed=trading_status.get("signals_processed", 0),
                            rejected=trading_status.get("signals_rejected", 0),
                            reasons=trading_status.get("rejection_reasons", {}),
                            cycle_duration=trading_status.get("last_cycle_duration", 0),
                        )

                        cycle_duration = (datetime.utcnow() - cycle_start).total_seconds()
                        logger.info(
                            f"[الدورة #{cycle}] ✅ اكتملت في {cycle_duration:.1f} ثانية"
                        )

            except Exception as e:
                logger.error(
                    f"[الدورة #{cycle}] ❌ خطأ غير متوقع: {e}",
                    exc_info=True
                )

            # انتظار دقيقتين بين الدورات
            await asyncio.sleep(120)

    asyncio.create_task(trading_loop())
    logger.info("[تداول] ✅ حلقة التداول بدأت (دورة كل دقيقتين)")

    # ═════════════════════════════════════════════════════════
    #  النظام جاهز
    # ═════════════════════════════════════════════════════════
    logger.info("═" * 40)
    logger.info("[النظام] ✅ جميع المحركات جاهزة — النظام يعمل")
    logger.info(f"[صحة] حالة النظام: {health_monitor.system_state}")
    logger.info("═" * 40)

    # ═════════════════════════════════════════════════════════
    #  بدء بوت تيليجرام (حظر حتى الإيقاف)
    # ═════════════════════════════════════════════════════════
    try:
        await telegram_engine.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("[النظام] ⚠️ تم استقبال إشارة إيقاف")
    finally:
        # ═════════════════════════════════════════════════════
        #  الإيقاف التدريجي
        # ═════════════════════════════════════════════════════
        logger.info("═" * 40)
        logger.info("[النظام] ⏳ بدء الإيقاف التدريجي...")
        logger.info("═" * 40)

        shutdown_order = [
            ("بوت تيليجرام", telegram_engine.stop),
            ("مراقب الصحة", health_monitor.stop),
            ("التقارير", reporting_engine.stop),
            ("التعلم", learning_engine.stop),
            ("المحفظة", portfolio_engine.stop),
            ("التنفيذ", execution_engine.stop),
            ("المخاطر", risk_engine.stop),
            ("الأدلة", evidence_engine.stop),
            ("الاستراتيجيات", strategy_engine.stop),
            ("المحلل", market_analyzer.stop),
            ("بيانات السوق", market_data_engine.stop),
            ("السجلات", logging_engine.stop),
            ("الإعدادات", config_engine.stop),
        ]

        for name, stop_fn in shutdown_order:
            try:
                await stop_fn()
                logger.info(f"[إيقاف] ✅ {name} — تم الإيقاف")
            except Exception as e:
                logger.error(f"[إيقاف] ❌ {name} — خطأ: {e}")

        await close_db()
        logger.info("[إيقاف] ✅ اتصالات قاعدة البيانات — مغلقة")

        logger.info("═" * 40)
        logger.info("[النظام] ✅ اكتمل الإيقاف التدريجي — إلى اللقاء")
        logger.info("═" * 40)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
