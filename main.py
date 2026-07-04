"""
CTM Bot — Main Entry Point (webhook mode for Render)
v2.0 — unified indicators, live prices, risk engine, pause/resume
"""
import os, time, threading
from config import MONITOR_INTERVAL_SECONDS, SUPABASE_DB_URL, WEBHOOK_BASE_URL
from data.binance_api import get_klines, get_order_book, get_live_price
from db.supabase_client import (
    init_db, get_active_coins, save_signal, get_active_signals, close_signal
)
from signals.generator import generate_signal, precompute_indicators
from signals.monitor import check_trade
from utils.logger import (
    init_logger, system_start, binance_connected, supabase_connected,
    coins_loaded, analysis_start, fetch_data_start, fetch_data_done,
    no_signal, signal_generated, signal_sent, monitoring, tp_hit, sl_hit,
    error, cron_tick, cron_complete, _log
)
from utils.price_cache import update_price
from utils.state import (
    mark_ready, tick_cycle, set_coin_count, inc_error, is_ready, get_state,
    is_system_active,
)
from utils.risk_manager import check_pre_trade_risk, get_portfolio_summary
from utils.report import generate_report
from bot.telegram_bot import build_application, run_webhook


def analysis_cycle():
    start_time = time.time()
    cron_tick()

    # ── PAUSE CHECK ──
    if not is_system_active():
        state = get_state()
        reason = state.get('circuit_breaker_reason') or 'إيقاف مؤقت يدوي'
        _log("⏸️", "SYSTEM", f"النظام متوقف — {reason}")
        cron_complete(time.time() - start_time)
        tick_cycle(time.time() - start_time)
        return

    try:
        coins = get_active_coins()
        if not coins:
            _log("⚠️", "SYSTEM", "لا توجد عملات نشطة للتتبع")
            cron_complete(time.time() - start_time)
            tick_cycle(time.time() - start_time)
            return

        coins_loaded(len(coins), [c['symbol'] for c in coins])
        set_coin_count(len(coins))

        # ── PORTFOLIO SUMMARY (each cycle) ──
        try:
            summary = get_portfolio_summary()
            _log("📊", "RISK",
                 f"المحفظة: {summary['coins_count']} عملات | تعرض {summary['exposure_pct']}% | "
                 f"ربح يومي {summary.get('daily_pnl') or 0:.2f}$ | خسائر متتالية {summary['consecutive_losses']}")
        except Exception as e:
            error("RISK", f"Portfolio summary failed: {e}")

        for coin in coins:
            symbol = coin['symbol']
            capital_value = coin.get('capital_value', 100.0)
            risk_percent = coin.get('risk_percent', 2.0)

            for tf in coin.get('timeframes', ['1h']):
                try:
                    analysis_start(symbol, tf)

                    # ── LIVE PRICE (ticker priority) ──
                    fetch_data_start(symbol)
                    live = get_live_price(symbol)
                    if live['price'] > 0:
                        update_price(symbol, live['price'])
                        _log("💵", "DATA",
                             f"{symbol}: سعر حي {live['price']:.4f} (المصدر: {live['source']})")
                    time.sleep(0.15)

                    # ── KLINES ──
                    klines = get_klines(symbol, tf, limit=100)
                    time.sleep(0.15)

                    # ── ORDER BOOK ──
                    order_book = get_order_book(symbol)
                    fetch_data_done(symbol, len(klines) if isinstance(klines, list) else 0)

                    if not klines or len(klines) < 20:
                        no_signal(symbol, f"بيانات غير كافية ({len(klines) if klines else 0} شمعة)")
                        continue

                    # ── PRECOMPUTE INDICATORS (once per coin/timeframe) ──
                    indicators = precompute_indicators(klines)
                    if indicators.get('atr', 0) > 0:
                        _log("📐", "INDICATOR",
                             f"{symbol}/{tf}: ATR={indicators['atr']:.8f} "
                             f"RSI={indicators.get('rsi', 0):.1f} "
                             f"ADX={indicators.get('adx', {}).get('adx', 0):.1f}")

                    # Use live price in regime metrics if available
                    if live['price'] > 0:
                        from analysis.market_regime import classify_regime
                        # classify_regime still uses kline prices for indicators;
                        # live price updates the cache for display purposes

                    # ── GENERATE SIGNAL ──
                    result = generate_signal(symbol, tf, klines, order_book, dict(coin), indicators)

                    # ── DETAILED REPORT (console only) ──
                    try:
                        dbg = result.get('_debug', {})
                        report = generate_report(symbol, tf, klines, order_book,
                                                result.get('regime', {}),
                                                dbg.get('donchian_signal'),
                                                dbg.get('order_flow_signal'),
                                                dbg.get('decision', {}), {})
                        print(f"\n{report}")
                    except Exception:
                        pass

                    # ── RISK CHECK BEFORE SAVING SIGNAL ──
                    if result.get('has_signal'):
                        allowed, risk_reason = check_pre_trade_risk(
                            symbol, capital_value, risk_percent
                        )
                        if not allowed:
                            _log("🚫", "RISK", f"{symbol}: إشارة مرفوضة — {risk_reason}")
                            no_signal(symbol, f"Risk blocked: {risk_reason}")
                            continue

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
                        no_signal(symbol, result.get('reason', 'No signal'))

                except Exception as e:
                    error(f"{symbol}/{tf}", str(e))
                    inc_error()

        # ── MONITOR ACTIVE TRADES ──
        for signal in get_active_signals():
            try:
                time.sleep(0.15)
                live = get_live_price(signal['symbol'])
                current_price = float(live['price'])
                if current_price <= 0:
                    continue
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
        inc_error()

    duration = time.time() - start_time
    cron_complete(duration)
    tick_cycle(duration)
    if not is_ready():
        mark_ready()


def main():
    init_db()
    init_logger(SUPABASE_DB_URL)
    system_start()
    binance_connected()
    supabase_connected()

    _log("📡", "SYSTEM", "تفعيل جلب الأسعار الحية (ticker أولاً)")
    _log("📐", "SYSTEM", "مؤشرات موحدة — تحسب مرة واحدة لكل دورة")
    _log("🛡️", "SYSTEM", "محرك المخاطر نشط")

    print("Running initial analysis...")
    analysis_cycle()
    print(f"Initial analysis done. State: {get_state()}")

    def analysis_loop():
        while True:
            time.sleep(MONITOR_INTERVAL_SECONDS)
            try:
                analysis_cycle()
            except Exception as e:
                error("MAIN", str(e))
                inc_error()
                time.sleep(10)

    threading.Thread(target=analysis_loop, daemon=True).start()

    app = build_application()

    port = int(os.environ.get('PORT', 10000))
    base = WEBHOOK_BASE_URL or f'http://localhost:{port}'
    webhook_url = f"{base}/webhook"

    if not WEBHOOK_BASE_URL:
        print(f"⚠️  WEBHOOK_BASE_URL not set — using {webhook_url}")
        print(f"   Set RENDER_EXTERNAL_URL or WEBHOOK_BASE_URL for production")

    print(f"Starting webhook on port {port}, url={webhook_url}")
    run_webhook(app, webhook_url, port)


if __name__ == "__main__":
    main()
