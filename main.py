import os
import signal
import logging
import asyncio
import threading
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("main")

# ── Global refs ──
bot_app = None
loop = None
scheduler = None
_application = None
_updater = None


async def run_analysis_cycle():
    """Main analysis cycle — pure async, no threading needed."""
    try:
        from database import db
        from data_layer import fetch_data
        from strategies_layer.strategy_selection import choose
        from execution_engine import trade_tracker
        from risk_management import risk_manager
        from monitoring import performance, alerts
        from config import ADMIN_CHAT_IDS

        if not risk_manager.is_trading_allowed():
            logger.info("Trading not allowed (stopped/circuit breaker/kill switch)")
            return

        if risk_manager.check_circuit_breaker():
            logger.warning("Circuit breaker activated!")
            chat_id = ADMIN_CHAT_IDS[0] if ADMIN_CHAT_IDS else None
            if chat_id:
                await alerts.send_alert(
                    chat_id, "قاطع الدائرة مفعل!",
                    "تم إيقاف التداول بسبب تجاوز حد الخسارة."
                )
            return

        if risk_manager.check_drawdown():
            logger.warning("Max drawdown reached!")
            db.query("UPDATE system_state SET bot_running = FALSE WHERE id = 1", fetch=False)
            return

        # Check open trades
        closed_trades = trade_tracker.check_open_trades()
        for ct in closed_trades:
            chat_id = ADMIN_CHAT_IDS[0] if ADMIN_CHAT_IDS else None
            if chat_id:
                await alerts.send_trade_closed(chat_id, ct)

        # Get active assets
        assets = db.query("SELECT * FROM assets WHERE is_active = TRUE")
        if not assets:
            logger.info("No active assets to analyze")
            return

        for asset in assets:
            for tf in asset["timeframes"]:
                try:
                    candles = fetch_data.fetch_klines(asset["symbol"], tf, limit=200)
                    if not candles or len(candles) < 50:
                        continue

                    order_book = fetch_data.fetch_order_book(asset["symbol"], limit=10)

                    params = {
                        "donchian_period": asset.get("donchian_period", 20),
                        "atr_period": asset.get("atr_period", 14),
                        "atr_sl_multiplier": asset.get("atr_sl_multiplier", 3.0),
                        "tp_ratio": asset.get("tp_ratio", 2.0),
                    }

                    signal, regime = choose(candles, order_book, params)

                    if signal:
                        trade_tracker.log_signal(signal, asset["symbol"], tf)
                        trade_id = trade_tracker.create_trade(signal, asset["symbol"], tf)

                        if trade_id:
                            logger.info(
                                f"Signal: {signal['signal']} {asset['symbol']} ({tf}) "
                                f"Conf: {signal['confidence']}% | Trade #{trade_id}"
                            )
                            chat_id = ADMIN_CHAT_IDS[0] if ADMIN_CHAT_IDS else None
                            if chat_id:
                                await alerts.send_signal_notification(
                                    chat_id, signal, asset["symbol"], tf, regime
                                )

                except Exception as e:
                    logger.error(f"Error analyzing {asset['symbol']} {tf}: {e}")
                    continue

        performance.update_performance()

        db.query(
            "UPDATE system_state SET last_check_time = NOW() WHERE id = 1",
            fetch=False
        )

        logger.info(f"Cycle complete at {datetime.utcnow().isoformat()}")

    except Exception as e:
        logger.error(f"Analysis cycle error: {e}", exc_info=True)


async def async_main():
    """
    Single event loop — everything runs here.
    No threading for async code.
    """
    global _application, _updater, loop, scheduler

    loop = asyncio.get_running_loop()
    logger.info("Event loop created")

    # ── 1. Initialize Database ──
    from database.db import init_tables
    try:
        init_tables()
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"Database init failed: {e}")
        return

    # ── 2. Build Telegram Application ──
    from bot_ui.telegram_bot import build_bot
    from monitoring.alerts import set_bot
    from config import ADMIN_CHAT_IDS

    _application = build_bot()
    if not _application:
        logger.error("Failed to build Telegram bot application")
        return

    # Set bot reference for sending alerts
    set_bot(_application.bot)

    if ADMIN_CHAT_IDS:
        logger.info(f"Admin IDs: {ADMIN_CHAT_IDS}")

    # ── 3. Start APScheduler (AsyncIOScheduler — runs IN this loop) ──
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from config import CHECK_INTERVAL

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_analysis_cycle,
        'interval',
        seconds=CHECK_INTERVAL,
        id='analysis_cycle',
        max_instances=1,
        coalesce=True
    )
    scheduler.start()
    logger.info(f"AsyncScheduler started — interval: {CHECK_INTERVAL}s")

    # ── 4. Start Telegram Bot (lifecycle: init → start → polling) ──
    try:
        await _application.initialize()
        logger.info("Telegram application initialized")

        await _application.start()
        logger.info("Telegram application started")

        _updater = _application.updater
        await _updater.start_polling()
        logger.info("Telegram polling started — bot is live!")

    except Exception as e:
        logger.error(f"Failed to start Telegram bot: {e}", exc_info=True)
        scheduler.shutdown(wait=False)
        return

    # ── 5. Keep running until shutdown signal ──
    stop_event = asyncio.Event()

    def _signal_handler(sig):
        logger.info(f"Received signal {sig} — shutting down gracefully...")
        stop_event.set()

    # Register signal handlers (works on Unix)
    try:
        loop.add_signal_handler(signal.SIGTERM, _signal_handler, signal.SIGTERM)
        loop.add_signal_handler(signal.SIGINT, _signal_handler, signal.SIGINT)
    except NotImplementedError:
        # Windows fallback — no add_signal_handler
        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)

    # Block until shutdown
    await stop_event.wait()

    # ── 6. Graceful Shutdown ──
    logger.info("Starting graceful shutdown...")

    try:
        if _updater:
            await _updater.stop()
            logger.info("Telegram updater stopped")
    except Exception as e:
        logger.error(f"Error stopping updater: {e}")

    try:
        await _application.stop()
        logger.info("Telegram application stopped")
    except Exception as e:
        logger.error(f"Error stopping application: {e}")

    try:
        await _application.shutdown()
        logger.info("Telegram application shut down")
    except Exception as e:
        logger.error(f"Error shutting down application: {e}")

    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")

    logger.info("Graceful shutdown complete.")


def start_bot():
    """
    Entry point for the bot.
    Creates ONE event loop and runs everything inside it.
    """
    global loop
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(async_main())
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt — exiting")
    except Exception as e:
        logger.error(f"Fatal error in bot: {e}", exc_info=True)
    finally:
        if loop and not loop.is_closed():
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
        logger.info("Bot process exited.")