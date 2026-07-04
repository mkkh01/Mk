"""
CTM Bot - Main Entry Point
Orchestrates: data fetch → analysis → signal generation → monitoring → alerts.
Runs on a cron schedule (intended for Render cron jobs).
"""
import sys
import time
from datetime import datetime

from config import MONITOR_INTERVAL_SECONDS
from data.binance_api import get_klines, get_order_book, get_current_price
from db.supabase_client import (
    init_db, get_active_coins, save_signal, get_active_signals,
    close_signal
)
from signals.generator import generate_signal
from signals.monitor import check_trade
from utils.logger import (
    init_logger, system_start, binance_connected, supabase_connected,
    coins_loaded, analysis_start, strategy_selected, no_signal,
    signal_generated, signal_sent, monitoring, tp_hit, sl_hit,
    error, cron_tick, cron_complete
)
from bot.telegram_bot import run_bot
import threading
import requests

TELEGRAM_API = f"https://api.telegram.org/bot{__import__('config').TELEGRAM_BOT_TOKEN}"

def send_telegram_message(chat_id: int, text: str):
    """Send a message via Telegram API."""
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json={
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'Markdown'
        }, timeout=10)
    except Exception as e:
        print(f"Telegram send error: {e}")

def analysis_cycle():
    """Single analysis cycle: check all coins, generate signals, monitor trades."""
    start_time = time.time()
    cron_tick()
    
    try:
        # Load tracked coins
        coins = get_active_coins()
        if not coins:
            print("No active coins to analyze.")
            cron_complete(time.time() - start_time)
            return
        
        coins_loaded(len(coins), [c['symbol'] for c in coins])
        
        # === PHASE 1: Generate new signals ===
        for coin in coins:
            symbol = coin['symbol']
            timeframes = coin['timeframes']
            
            for tf in timeframes:
                try:
                    analysis_start(symbol, tf)
                    
                    # Fetch data
                    klines = get_klines(symbol, tf, limit=100)
                    order_book = get_order_book(symbol)
                    
                    # Generate signal
                    result = generate_signal(symbol, tf, klines, order_book, dict(coin))
                    
                    if result.get('has_signal'):
                        # Save signal to DB
                        signal_id = save_signal({
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
                        
                        # Send Telegram alert
                        alert = (
                            f"🎯 **إشارة جديدة!**\n\n"
                            f"🔹 **{symbol}** | {tf}\n"
                            f"📊 الاستراتيجية: {result['strategy']}\n"
                            f"📈 الحالة: {result['regime']['regime']}\n\n"
                            f"💰 دخول: `{result['entry_price']:.4f}`\n"
                            f"🛑 وقف خسارة: `{result['stop_loss']:.4f}`\n"
                            f"🎯 هدف 1: `{result['take_profit1']:.4f}`\n"
                            f"⚖️ حجم الصفقة: `{result['position_size']:.4f}`\n"
                            f"⚠️ المخاطرة: {result['risk_percent']}% من {result['capital_percent']}%\n\n"
                            f"📝 السبب: {result['reason']}"
                        )
                        # Send to a default chat (will be configured per user)
                        # For now, signals are stored in DB and viewable via bot
                    else:
                        no_signal(symbol, result.get('reason', 'No clear signal'))
                
                except Exception as e:
                    error(f"{symbol}/{tf}", str(e))
        
        # === PHASE 2: Monitor active trades ===
        active_signals = get_active_signals()
        for signal in active_signals:
            try:
                # Get current price
                price_data = get_current_price(signal['symbol'])
                current_price = float(price_data['price'])
                
                # Check trade status
                result = check_trade(dict(signal), current_price)
                
                if result.get('hit'):
                    # Trade completed
                    close_signal(signal['id'], result['result'], result['exit_price'])
                    
                    if result['result'] == 'TP_HIT':
                        tp_hit(signal['symbol'], result['exit_price'], result['profit_pct'], result['profit_usd'])
                    else:
                        sl_hit(signal['symbol'], result['exit_price'], result['profit_pct'], result['profit_usd'])
                    
                    # Send alert
                    emoji = "🎯" if result['result'] == 'TP_HIT' else "🛑"
                    alert = (
                        f"{emoji} **صفقة منتهية**\n\n"
                        f"🔹 **{signal['symbol']}** | {signal['timeframe']}\n"
                        f"📊 النتيجة: {result['result']}\n"
                        f"💰 الربح: {result['profit_pct']:+.2f}% (${result['profit_usd']:+.2f})\n"
                        f"📝 {result['detail']}"
                    )
                    # Signal completion alert stored for bot display
                else:
                    # Still active - log monitoring
                    monitoring(signal['symbol'], current_price, signal['entry_price'],
                              signal['stop_loss'], signal['take_profit1'])
            
            except Exception as e:
                error(f"Monitor/{signal['symbol']}", str(e))
    
    except Exception as e:
        error("SYSTEM", f"Analysis cycle failed: {e}")
    
    cron_complete(time.time() - start_time)

def main():
    """Main entry point."""
    # Initialize
    init_db()
    system_start()
    binance_connected()
    supabase_connected()
    
    # Run one analysis cycle immediately
    print("Running initial analysis cycle...")
    analysis_cycle()
    
    # Start Telegram bot in background thread
    print("Starting Telegram bot...")
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Continuous monitoring loop
    print(f"Entering monitoring loop (interval: {MONITOR_INTERVAL_SECONDS}s)")
    while True:
        time.sleep(MONITOR_INTERVAL_SECONDS)
        try:
            analysis_cycle()
        except Exception as e:
            error("MAIN", f"Unexpected error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
