"""
Main Entry Point — Initialize config, start engines, start bots.
NO business logic here. Pure orchestration.
"""
import asyncio
import logging
import sys
import os
from datetime import datetime

# ── Structured Logging ──────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
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


def log_banner():
    banner = """
╔══════════════════════════════════════════════════╗
║     CT V4.0 — Professional AI Spot Trading       ║
║     Clean Architecture | 14 Engines | Event-Driven║
╚══════════════════════════════════════════════════╝"""
    print(banner)
    logger.info("=" * 50)
    logger.info(f"STARTUP: {datetime.utcnow().isoformat()}Z")


async def main():
    """Orchestrate system startup."""
    log_banner()

    # ── 1. Config Engine ────────────────────────────────────
    logger.info("[1/11] Loading configuration...")
    settings = get_settings()
    missing = settings.validate()
    if missing:
        logger.critical(f"Missing required env vars: {missing}")
        sys.exit(1)
    logger.info(f"[CONFIG] Admin ID: {settings.admin_id}")
    logger.info(f"[CONFIG] Capital: {settings.default_capital} | Fee: {settings.trade_fee}")
    logger.info(f"[CONFIG] Binance WS: {settings.binance_ws_url}")

    config_engine = ConfigEngine()
    await config_engine.initialize()
    await config_engine.start()
    logger.info("[CONFIG] ✅ Config Engine started.")

    # ── 2. Keep-Alive Server ────────────────────────────────
    keep_alive()
    logger.info(f"[2/11] Keep-alive server started on port {settings.port}")

    # ── 3. Event Bus ────────────────────────────────────────
    event_bus = EventBus()
    logger.info("[3/11] Event Bus initialized.")

    # ── 4. Logging Engine ───────────────────────────────────
    logging_engine = LoggingEngine()
    await logging_engine.initialize()
    await logging_engine.start()
    logger.info("[4/11] ✅ Logging Engine started.")

    # ── 5. Database ─────────────────────────────────────────
    logger.info("[5/11] Connecting to database...")
    try:
        await init_db()
        logger.info("[DATABASE] ✅ Connected. Tables created/verified.")
    except Exception as e:
        logger.critical(f"[DATABASE] ❌ Connection failed: {e}", exc_info=True)
        sys.exit(1)

    # ── 6. Market Data Engine ───────────────────────────────
    market_data_engine = MarketDataEngine(event_bus)
    await market_data_engine.initialize()
    logger.info("[6/11] Market Data Engine initialized.")

    # ── 7. Market Analyzer ──────────────────────────────────
    market_analyzer = MarketAnalyzer(event_bus)
    await market_analyzer.initialize()
    logger.info("[7/11] Market Analyzer initialized.")

    # ── 8. Strategy Engine ──────────────────────────────────
    strategy_engine = StrategyEngine(event_bus)
    await strategy_engine.initialize()
    logger.info("[8/11] Strategy Engine initialized.")

    # ── 9. Evidence Engine ──────────────────────────────────
    evidence_engine = EvidenceEngine(event_bus)
    await evidence_engine.initialize()
    logger.info("[9/11] Evidence Engine initialized.")

    # ── 10. Risk Engine ─────────────────────────────────────
    risk_engine = RiskEngine(event_bus)
    await risk_engine.initialize()
    logger.info("[10/11] Risk Engine initialized.")

    # ── 11. Execution Engine (simulation mode) ──────────────
    execution_engine = ExecutionEngine(event_bus, simulation_mode=True)
    await execution_engine.initialize()
    logger.info("[11/11] Execution Engine initialized (SIMULATION MODE).")

    # ── Portfolio Engine ────────────────────────────────────
    portfolio_engine = PortfolioEngine(event_bus, initial_balance=settings.default_capital)
    await portfolio_engine.initialize()
    logger.info(f"[PORTFOLIO] Initialized with balance={settings.default_capital}")

    # ── Learning Engine ─────────────────────────────────────
    learning_engine = LearningEngine(event_bus)
    await learning_engine.initialize()
    logger.info("[LEARNING] Engine initialized.")

    # ── Reporting Engine ────────────────────────────────────
    reporting_engine = ReportingEngine(event_bus)
    await reporting_engine.initialize()
    logger.info("[REPORTING] Engine initialized.")

    # ── Health Monitor ──────────────────────────────────────
    health_monitor = HealthMonitor(event_bus)
    await health_monitor.initialize()
    logger.info("[HEALTH] Monitor initialized.")

    # ── Services ────────────────────────────────────────────
    analysis_service = AnalysisService(market_data_engine, market_analyzer, strategy_engine)
    trading_service = TradingService(
        evidence_engine, risk_engine, execution_engine,
        market_analyzer, strategy_engine, market_data_engine
    )
    portfolio_service = PortfolioService(
        portfolio_engine, reporting_engine, learning_engine, health_monitor
    )
    risk_service = RiskService(risk_engine)
    logger.info("[SERVICES] All 4 services initialized.")

    # ── Telegram Bot ────────────────────────────────────────
    telegram_engine = TelegramEngine(
        token=settings.telegram_token,
        admin_id=settings.admin_id,
        analysis_service=analysis_service,
        trading_service=trading_service,
        portfolio_service=portfolio_service,
        risk_service=risk_service,
    )
    logger.info("[TELEGRAM] Bot engine created.")

    # ── Start All Engines ───────────────────────────────────
    logger.info("─" * 40)
    logger.info("Starting all engines...")
    await market_data_engine.start()
    logger.info("[ENGINE] ✅ Market Data Engine started.")
    await market_analyzer.start()
    logger.info("[ENGINE] ✅ Market Analyzer started.")
    await strategy_engine.start()
    logger.info("[ENGINE] ✅ Strategy Engine started.")
    await evidence_engine.start()
    logger.info("[ENGINE] ✅ Evidence Engine started.")
    await risk_engine.start()
    logger.info("[ENGINE] ✅ Risk Engine started.")
    await execution_engine.start()
    logger.info("[ENGINE] ✅ Execution Engine started.")
    await portfolio_engine.start()
    logger.info("[ENGINE] ✅ Portfolio Engine started.")
    await learning_engine.start()
    logger.info("[ENGINE] ✅ Learning Engine started.")
    await reporting_engine.start()
    logger.info("[ENGINE] ✅ Reporting Engine started.")
    await health_monitor.start()
    logger.info("[ENGINE] ✅ Health Monitor started.")

    # ── Set user IDs ────────────────────────────────────────
    user_id_str = str(settings.admin_id)
    execution_engine._admin_id = settings.admin_id
    portfolio_engine.user_id = user_id_str
    learning_engine.user_id = user_id_str
    logger.info(f"[SYSTEM] User ID set: {user_id_str}")

    # ── Sync symbols from database ──────────────────────────
    logger.info("[SYNC] Loading active trading symbols from database...")
    symbols, coins = await analysis_service.sync_symbols_from_db(user_id_str)
    logger.info(f"[SYNC] Loaded {len(symbols)} active symbols.")
    for coin in coins:
        logger.info(f"[SYMBOL] {coin.symbol} | TF={coin.timeframe} | Capital={coin.capital_allocated}")

    # ── Trading Loop ────────────────────────────────────────
    async def trading_loop():
        """Periodic trading cycle for all active symbols."""
        from database.repositories import CoinRepository, get_session
        cycle = 0
        while True:
            cycle += 1
            try:
                trading_allowed = health_monitor.is_trading_safe() and risk_service.is_trading_allowed()
                if not trading_allowed:
                    logger.debug(f"[TRADE CYCLE #{cycle}] Trading blocked (health or risk).")
                else:
                    async for session in get_session():
                        coins = await CoinRepository.get_all_active(session, user_id_str)
                        logger.debug(f"[TRADE CYCLE #{cycle}] Processing {len(coins)} symbols...")
                        for coin in coins:
                            try:
                                result = await trading_service.process_symbol(coin.symbol, user_id_str)
                                if result:
                                    evidence, risk_decision, execution = result
                                    if execution:
                                        logger.info(
                                            f"[TRADE] ✅ {coin.symbol}: {evidence.decision} "
                                            f"({evidence.final_score:.0f}%) | "
                                            f"Qty={execution.executed_quantity:.6f}"
                                        )
                            except Exception as e:
                                logger.error(f"[TRADE] Error processing {coin.symbol}: {e}")
                            await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"[TRADE CYCLE #{cycle}] Error: {e}", exc_info=True)
            await asyncio.sleep(120)  # 2-minute cycle

    asyncio.create_task(trading_loop())
    logger.info("[TRADE] Trading loop started (2-min cycle).")

    # ── System Ready ────────────────────────────────────────
    logger.info("=" * 50)
    logger.info("✅ SYSTEM READY — All 14 engines operational.")
    logger.info(f"[HEALTH] System state: {health_monitor.system_state}")
    logger.info("=" * 50)

    # ── Start Telegram Bot (blocking) ───────────────────────
    try:
        await telegram_engine.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("[SHUTDOWN] Signal received.")
    finally:
        # ── Graceful Shutdown ───────────────────────────────
        logger.info("[SHUTDOWN] Stopping engines...")
        shutdown_order = [
            ("Telegram", telegram_engine.stop),
            ("Health Monitor", health_monitor.stop),
            ("Reporting", reporting_engine.stop),
            ("Learning", learning_engine.stop),
            ("Portfolio", portfolio_engine.stop),
            ("Execution", execution_engine.stop),
            ("Risk", risk_engine.stop),
            ("Evidence", evidence_engine.stop),
            ("Strategy", strategy_engine.stop),
            ("Market Analyzer", market_analyzer.stop),
            ("Market Data", market_data_engine.stop),
            ("Logging", logging_engine.stop),
            ("Config", config_engine.stop),
        ]
        for name, stop_fn in shutdown_order:
            try:
                await stop_fn()
                logger.info(f"[SHUTDOWN] {name} stopped.")
            except Exception as e:
                logger.error(f"[SHUTDOWN] {name} error: {e}")
        await close_db()
        logger.info("[SHUTDOWN] Database connections closed.")
        logger.info("=" * 50)
        logger.info("✅ Shutdown complete. Goodbye.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
