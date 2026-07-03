import os
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

def run_analysis_cycle():
    """Main analysis cycle - runs for each asset/timeframe."""
    try:
        from database import db
        from data_layer import fetch_data
        from strategies_layer.strategy_selection import choose
        from execution_engine import trade_tracker
        from risk_management import risk_manager
        from monitoring import performance, alerts
        from config import ADMIN_CHAT_IDS
        
        # Check if trading is allowed
        if not risk_manager.is_trading_allowed():
            logger.info("Trading not allowed (stopped/circuit breaker/kill switch)")
            return
        
        # Check circuit breaker
        if risk_manager.check_circuit_breaker():
            logger.warning("Circuit breaker activated!")
            chat_id = ADMIN_CHAT_IDS[0] if ADMIN_CHAT_IDS else None
            if chat_id and loop:
                asyncio.run_coroutine_threadsafe(
                    alerts.send_alert(chat_id, "قاطع الدائرة مفعل!", "تم إيقاف التداول بسبب تجاوز حد الخسارة."),
                    loop
                )
            return
        
        # Check drawdown
        if risk_manager.check_drawdown():
            logger.warning("Max drawdown reached!")
            db.query(
                "UPDATE system_state SET bot_running = FALSE WHERE id = 1",
                fetch=False
            )
            return
        
        # Check open trades first
        closed_trades = trade_tracker.check_open_trades()
        for ct in closed_trades:
            chat_id = ADMIN_CHAT_IDS[0] if ADMIN_CHAT_IDS else None
            if chat_id and loop:
                asyncio.run_coroutine_threadsafe(
                    alerts.send_trade_closed(chat_id, ct),
                    loop
                )
        
        # Get active assets
        assets = db.query("SELECT * FROM assets WHERE is_active = TRUE")
        if not assets:
            logger.info("No active assets to analyze")
            return
        
        # Analyze each asset/timeframe
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
                            if chat_id and loop:
                                asyncio.run_coroutine_threadsafe(
                                    alerts.send_signal_notification(
                                        chat_id, signal, asset["symbol"], tf, regime
                                    ),
                                    loop
                                )
                
                except Exception as e:
                    logger.error(f"Error analyzing {asset['symbol']} {tf}: {e}")
                    continue
        
        # Update performance
        performance.update_performance()
        
        db.query(
            "UPDATE system_state SET last_check_time = NOW() WHERE id = 1",
            fetch=False
        )
        
        logger.info(f"Cycle complete at {datetime.utcnow().isoformat()}")
    
    except Exception as e:
        logger.error(f"Analysis cycle error: {e}", exc_info=True)

def schedule_jobs():
    """Setup periodic analysis jobs."""
    from apscheduler.schedulers.background import BackgroundScheduler
    from config import CHECK_INTERVAL
    
    global scheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_analysis_cycle,
        'interval',
        seconds=CHECK_INTERVAL,
        id='analysis_cycle',
        max_instances=1,
        coalesce=True
    )
    scheduler.start()
    logger.info(f"Scheduler started - interval: {CHECK_INTERVAL}s")

async def post_init(application):
    """Called after bot is initialized - set up event loop and resources."""
    global loop
    loop = asyncio.get_running_loop()
    
    # Set bot instance for alerts
    from monitoring.alerts import set_bot
    set_bot(application.bot)
    
    # Initialize database tables
    from database.db import init_tables
    init_tables()
    
    from config import ADMIN_CHAT_IDS
    if ADMIN_CHAT_IDS:
        logger.info(f"Admin IDs: {ADMIN_CHAT_IDS}")
    
    logger.info("Bot initialized successfully!")

def start_bot():
    """Start the trading bot with Telegram interface."""
    global bot_app
    
    from bot_ui.telegram_bot import build_bot
    import config
    
    bot_app = build_bot()
    if not bot_app:
        logger.error("Failed to build bot")
        return
    
    # Register post-init hook
    bot_app.post_init = post_init
    
    # Start scheduler in background
    schedule_thread = threading.Thread(target=schedule_jobs, daemon=True)
    schedule_thread.start()
    
    logger.info("Starting Telegram bot polling...")
    bot_app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    start_bot()