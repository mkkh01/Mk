"""
Main Entry Point — Initialize config, start engines, start bots.
NO business logic here. Pure orchestration.
"""
import asyncio
import logging
import sys
import os

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")

# ── Core ────────────────────────────────────────────────────
from core.events import EventBus
from core.types import SystemState

# ── Config ──────────────────────────────────────────────────
from config.settings import get_settings
from config.constants import ADMIN_ID

# ── Database ────────────────────────────────────────────────
from database.repositories import init_db, close_db

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


async def main():
    """Orchestrate system startup."""
    print("🚀 جاري إقلاع نظام التداول المؤسسي CT V4.0...")
    print("⚙️  Clean Architecture | 14 Engines | Event-Driven")

    # ── 1. Config ───────────────────────────────────────────
    settings = get_settings()
    missing = settings.validate()
    if missing:
        logger.critical(f"Missing required env vars: {missing}")
        logger.critical("Set them in .env file or environment variables.")
        sys.exit(1)

    logger.info(f"Config loaded: {settings.mask_secrets()}")

    # ── 2. Keep-Alive Server ────────────────────────────────
    keep_alive()
    logger.info(f"Keep-alive server started on port {settings.port}")

    # ── 3. Event Bus ────────────────────────────────────────
    event_bus = EventBus()
    logger.info("Event Bus initialized.")

    # ── 4. Database ─────────────────────────────────────────
    await init_db()
    logger.info("Database initialized.")

    # ── 5. Engines (order matters) ──────────────────────────

    # Config Engine
    config_engine = ConfigEngine()
    await config_engine.initialize()
    await config_engine.start()

    # Logging Engine
    logging_engine = LoggingEngine()
    await logging_engine.initialize()
    await logging_engine.start()

    # Market Data Engine
    market_data_engine = MarketDataEngine(event_bus)
    await market_data_engine.initialize()

    # Market Analyzer
    market_analyzer = MarketAnalyzer(event_bus)
    await market_analyzer.initialize()

    # Strategy Engine
    strategy_engine = StrategyEngine(event_bus)
    await strategy_engine.initialize()

    # Evidence Engine
    evidence_engine = EvidenceEngine(event_bus)
    await evidence_engine.initialize()

    # Risk Engine
    risk_engine = RiskEngine(event_bus)
    await risk_engine.initialize()

    # Execution Engine (simulation mode)
    execution_engine = ExecutionEngine(event_bus, simulation_mode=True)
    await execution_engine.initialize()

    # Portfolio Engine
    portfolio_engine = PortfolioEngine(event_bus, initial_balance=settings.default_capital)
    await portfolio_engine.initialize()

    # Learning Engine
    learning_engine = LearningEngine(event_bus)
    await learning_engine.initialize()

    # Reporting Engine
    reporting_engine = ReportingEngine(event_bus)
    await reporting_engine.initialize()

    # Health Monitor
    health_monitor = HealthMonitor(event_bus)
    await health_monitor.initialize()

    # ── 6. Services ─────────────────────────────────────────
    analysis_service = AnalysisService(market_data_engine, market_analyzer, strategy_engine)
    trading_service = TradingService(
        evidence_engine, risk_engine, execution_engine,
        market_analyzer, strategy_engine, market_data_engine
    )
    portfolio_service = PortfolioService(
        portfolio_engine, reporting_engine, learning_engine, health_monitor
    )
    risk_service = RiskService(risk_engine)

    logger.info("Services initialized.")

    # ── 7. Telegram Bot ─────────────────────────────────────
    telegram_engine = TelegramEngine(
        token=settings.telegram_token,
        admin_id=settings.admin_id,
        analysis_service=analysis_service,
        trading_service=trading_service,
        portfolio_service=portfolio_service,
        risk_service=risk_service,
    )

    # ── 8. Start Engines (in order) ─────────────────────────
    # Start engines that subscribe to events first
    await market_data_engine.start()
    await market_analyzer.start()
    await strategy_engine.start()
    await evidence_engine.start()
    await risk_engine.start()
    await execution_engine.start()
    await portfolio_engine.start()
    await learning_engine.start()
    await reporting_engine.start()
    await health_monitor.start()

    # Set user IDs on engines that need them
    execution_engine._admin_id = settings.admin_id
    user_id_str = str(settings.admin_id)
    portfolio_engine.user_id = user_id_str
    learning_engine.user_id = user_id_str

    # ── 9. Sync symbols from database ───────────────────────
    await analysis_service.sync_symbols_from_db(user_id_str)

    # ── 10. Trading Loop ────────────────────────────────────
    async def trading_loop():
        """Periodic trading cycle for all active symbols."""
        from database.repositories import CoinRepository, get_session
        while True:
            try:
                async for session in get_session():
                    coins = await CoinRepository.get_all_active(session, user_id_str)
                    for coin in coins:
                        if health_monitor.is_trading_safe() and risk_service.is_trading_allowed():
                            result = await trading_service.process_symbol(coin.symbol, user_id_str)
                            if result:
                                evidence, risk_decision, execution = result
                                if execution:
                                    logger.info(
                                        f"Trade executed: {coin.symbol} → "
                                        f"{evidence.decision} ({evidence.final_score:.0f})"
                                    )
                        await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Trading loop error: {e}", exc_info=True)
            await asyncio.sleep(120)  # 2-minute cycle

    asyncio.create_task(trading_loop())

    logger.info("✅ النظام المؤسسي جاهز بالكامل.")
    print("✅ All 14 engines started. System operational.")

    # ── 11. Start Telegram Bot (blocking) ───────────────────
    try:
        await telegram_engine.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
    finally:
        # ── Graceful Shutdown ───────────────────────────────
        await telegram_engine.stop()
        await health_monitor.stop()
        await reporting_engine.stop()
        await learning_engine.stop()
        await portfolio_engine.stop()
        await execution_engine.stop()
        await risk_engine.stop()
        await evidence_engine.stop()
        await strategy_engine.stop()
        await market_analyzer.stop()
        await market_data_engine.stop()
        await logging_engine.stop()
        await config_engine.stop()
        await close_db()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
