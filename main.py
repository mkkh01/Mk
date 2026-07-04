"""
CTM Bot — Main Entry Point
Bot runs in main thread (needs signal handlers), analysis in daemon thread.
"""
import time
import threading
from config import MONITOR_INTERVAL_SECONDS, SUPABASE_DB_URL
from data.binance_api import get_klines, get_order_book, get_current_price
from db.supabase_client import (
    init_db, get_active_coins, save_signal, get_active_signals, close_signal
)
from signals.generator import generate_signal
from signals.monitor import check_trade
from utils.logger import (
    init_logger, system_start, binance_connected, supabase_connected,
    coins_loaded, analysis_start, fetch_data_start, fetch_data_done,
    no_signal,
    signal_generated, signal_sent, monitoring, tp_hit, sl_hit,
    error, cron_tick, cron_complete
)
from utils.health import start_health_server
from bot.telegram_bot import run_bot


def analysis_cycle():
    start_time = time.time()
    cron_tick()
    try:
        coins = get_active_coins()
        if not coins:
            cron_complete(time.time() - start_time)
            return
        coins_loaded(len(coins), [c['symbol'] for c in coins])
        for coin in coins:
            symbol = coin['symbol']
            for tf in coin['timeframes']:
                try:
                    analysis_start(symbol, tf)
                    fetch_data_start(symbol)
                    time.sleep(0.3)
                    klines = get_klines(symbol, tf, limit=100)
                    time.sleep(0.2)
                    order_book = get_order_book(symbol)
                    fetch_data_done(symbol, len(klines) if isinstance(klines, list) else 0)
                    result = generate_signal(symbol, tf, klines, order_book, dict(coin))
                    if result.get('has_signal'):
                        save_signal({
                            'symbol': symbol, 'timeframe': tf,
                            'strategy': result['strategy'],
                            'entry_price': result['entry_price'],
                            'stop_loss': result['stop_loss'],
                            'take_profit1': result['take_profit1'],
                            'take_profit2': result.get('take_profit2'),
                            'position_size': result['position_size'],
                            'risk_percent': result['risk_percent'],
                            'capital_value': result['capital_value'],
                            'market_regime': result['regime']['regime'],
                            'regime_details': result['regime_details']
                        })
                        signal_generated(result)
                        signal_sent(symbol)
                    else:
                        no_signal(symbol, result.get('reason', 'No clear signal'))
                except Exception as e:
                    error(f"{symbol}/{tf}", str(e))
        for signal in get_active_signals():
            try:
                time.sleep(0.2)
                price_data = get_current_price(signal['symbol'])
                current_price = float(price_data['price'])
                result = check_trade(dict(signal), current_price)
                if result.get('hit'):
                    close_signal(signal['id'], result['result'], result['exit_price'])
                    if result['result'] == 'TP_HIT':
                        tp_hit(signal['symbol'], result['exit_price'], result['profit_pct'], result['profit_usd'])
                    else:
                        sl_hit(signal['symbol'], result['exit_price'], result['profit_pct'], result['profit_usd'])
                else:
                    monitoring(signal['symbol'], current_price, signal['entry_price'],
                              signal['stop_loss'], signal['take_profit1'])
            except Exception as e:
                error(f"Monitor/{signal['symbol']}", str(e))
    except Exception as e:
        error("SYSTEM", f"Analysis cycle failed: {e}")
    cron_complete(time.time() - start_time)


def main():
    init_db()
    init_logger(SUPABASE_DB_URL)
    system_start()
    binance_connected()
    supabase_connected()

    analysis_cycle()

    def analysis_loop():
        while True:
            time.sleep(MONITOR_INTERVAL_SECONDS)
            try:
                analysis_cycle()
            except Exception as e:
                error("MAIN", str(e))
                time.sleep(10)

    threading.Thread(target=analysis_loop, daemon=True).start()

    # Health check server for Render Web Service (daemon thread, no signal handlers)
    threading.Thread(target=start_health_server, daemon=True).start()

    print("Starting Telegram bot (main thread)...")
    run_bot()


if __name__ == "__main__":
    main()
