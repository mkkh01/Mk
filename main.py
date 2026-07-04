"""
CTM Bot - Main Entry Point (asyncio-native architecture)
Single event loop: Telegram bot + periodic analysis run in harmony.
"""
import asyncio
import time
import sys

from config import MONITOR_INTERVAL_SECONDS, SUPABASE_DB_URL
from data.binance_api import get_klines, get_order_book, get_current_price
from db.supabase_client import (
    init_db, get_active_coins, save_signal, get_active_signals,
    close_signal
)
from signals.generator import generate_signal
from signals.monitor import check_trade
from utils.logger import (
    init_logger, system_start, binance_connected, supabase_connected,
    coins_loaded, analysis_start, no_signal,
    signal_generated, signal_sent, monitoring, tp_hit, sl_hit,
    error, cron_tick, cron_complete
)
from bot.telegram_bot import build_application


def analysis_cycle():
    """Single analysis cycle: check all coins, generate signals, monitor trades.
    Synchronous (runs in thread pool via asyncio.to_thread)."""
    start_time = time.time()
    cron_tick()

    try:
        coins = get_active_coins()
        if not coins:
            cron_complete(time.time() - start_time)
            return

        coins_loaded(len(coins), [c['symbol'] for c in coins])

        # PHASE 1: Generate new signals
        for coin in coins:
            symbol = coin['symbol']
            timeframes = coin['timeframes']

            for tf in timeframes:
                try:
                    analysis_start(symbol, tf)
                    klines = get_klines(symbol, tf, limit=100)
                    order_book = get_order_book(symbol)
                    result = generate_signal(symbol, tf, klines, order_book, dict(coin))

                    if result.get('has_signal'):
                        save_signal({
                            'symbol': symbol,
                            'timeframe': tf,
                            'strategy': result['strategy'],
                            'entry_price': result['entry_price'],
                            'stop_loss': result['stop_loss'],
                            'take_profit1': result['take_profit1'],
                            'take_profit2': result.get('take_profit2'),
                            'position_size': result['position_size'],
                            'risk_percent': result['risk_percent'],
                            'capital_percent': result['capital_percent'],
                            'market_regime': result['regime']['regime'],
                            'regime_details': result['regime_details']
                        })
                        signal_generated(result)
                        signal_sent(symbol)
                    else:
                        no_signal(symbol, result.get('reason', 'No clear signal'))

                except Exception as e:
                    error(f"{symbol}/{tf}", str(e))

        # PHASE 2: Monitor active trades
        active_signals = get_active_signals()
        for signal in active_signals:
            try:
                price_data = get_current_price(signal['symbol'])
                current_price = float(price_data['price'])
                result = check_trade(dict(signal), current_price)

                if result.get('hit'):
                    close_signal(signal['id'], result['result'], result['exit_price'])
                    if result['result'] == 'TP_HIT':
                        tp_hit(signal['symbol'], result['exit_price'],
                               result['profit_pct'], result['profit_usd'])
                    else:
                        sl_hit(signal['symbol'], result['exit_price'],
                               result['profit_pct'], result['profit_usd'])
                else:
                    monitoring(signal['symbol'], current_price, signal['entry_price'],
                              signal['stop_loss'], signal['take_profit1'])

            except Exception as e:
                error(f"Monitor/{signal['symbol']}", str(e))

    except Exception as e:
        error("SYSTEM", f"Analysis cycle failed: {e}")

    cron_complete(time.time() - start_time)


async def main_async():
    """Main entry point — single asyncio event loop for bot + analysis."""
    # Initialize infrastructure
    init_db()
    init_logger(SUPABASE_DB_URL)
    system_start()
    binance_connected()
    supabase_connected()

    # Build Telegram bot application
    print("Building Telegram bot application...")
    app = build_application()

    # Start bot (non-blocking — allows analysis to run alongside)
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=['message'])
    print("Telegram bot started — polling for messages")

    # Run initial analysis cycle in thread pool (avoids blocking the event loop)
    print("Running initial analysis cycle...")
    await asyncio.to_thread(analysis_cycle)

    # Periodic analysis loop
    print(f"Entering monitoring loop (interval: {MONITOR_INTERVAL_SECONDS}s)")
    while True:
        await asyncio.sleep(MONITOR_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(analysis_cycle)
        except Exception as e:
            error("MAIN", f"Unexpected error in analysis: {e}")
            await asyncio.sleep(5)


def main():
    """Synchronous entry point — bootstraps asyncio."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
