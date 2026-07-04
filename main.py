"""
CTM Bot — Main Entry Point (webhook mode for Render)
"""
import os, time, threading
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
    no_signal, signal_generated, signal_sent, monitoring, tp_hit, sl_hit,
    error, cron_tick, cron_complete, _log
)
from utils.report import generate_report
import time as _time_module
from bot.telegram_bot import build_application, run_webhook


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
                    t0 = _time_module.time()
                    result = generate_signal(symbol, tf, klines, order_book, dict(coin))
                    timing = {'analysis': (_time_module.time() - t0) * 1000}
                    # Generate detailed report
                    dbg = result.get('_debug', {})
                    report = generate_report(symbol, tf, klines, order_book,
                                            result.get('regime', {}),
                                            dbg.get('donchian_signal'),
                                            dbg.get('order_flow_signal'),
                                            dbg.get('decision', {}),
                                            timing)
                    _log("📋", "REPORT", f"\n{report}")
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

    print("Running initial analysis...")
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

    app = build_application()

    port = int(os.environ.get('PORT', 10000))
    base = os.environ.get('RENDER_EXTERNAL_URL', f'http://localhost:{port}')
    webhook_url = f"{base}/webhook"

    print(f"Starting webhook on port {port}, url={webhook_url}")
    run_webhook(app, webhook_url, port)


if __name__ == "__main__":
    main()
