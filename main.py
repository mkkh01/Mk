"""
CTM Bot — Main Entry Point (webhook mode for Render)
v2.0 — unified indicators, live prices, risk engine, pause/resume
"""
import os, time, threading
from config import MONITOR_INTERVAL_SECONDS, SUPABASE_DB_URL, WEBHOOK_BASE_URL
from data.binance_api import get_klines, get_order_book, get_live_price
from data.price_providers import get_price_any_source, get_klines_any_source
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

    # ── BINANCE API STATUS ──
    from data.binance_api import get_api_status as _api_check
    api_status = _api_check()
    binance_dead = api_status.get('consecutive_failures', 0) >= 3
    if binance_dead:
        _log("⚠️", "API", f"Binance ميت (فشل {api_status['consecutive_failures']} مرة) — استخدام Bybit/KuCoin مباشرة")
    elif api_status.get('consecutive_failures', 0) > 0:
        _log("⚠️", "API", f"Binance API فشل متتالي: {api_status['consecutive_failures']} | "
             f"آخر نجاح: {api_status.get('seconds_since_success', '?')}s")

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

                    # ── LIVE PRICE (multi-source, skip dead Binance) ──
                    fetch_data_start(symbol)
                    t_data_start = time.time()
                    
                    binance_price_fn = None if binance_dead else get_live_price
                    live = get_price_any_source(symbol, binance_fn=binance_price_fn)
                    if live['price'] > 0:
                        update_price(symbol, live['price'])
                        _log("💵", "DATA",
                             f"{symbol}: سعر حي {live['price']:.6f} (المصدر: {live['source']})")
                    else:
                        _log("⚠️", "DATA", f"{symbol}: فشل جميع مصادر الأسعار")
                    time.sleep(0.05)

                    # ── KLINES (multi-source, skip dead Binance) ──
                    binance_klines_fn = None if binance_dead else get_klines
                    klines = get_klines_any_source(symbol, tf, limit=100, binance_fn=binance_klines_fn)
                    time.sleep(0.05)

                    # ── ORDER BOOK (multi-source) ──
                    order_book = get_order_book(symbol)
                    # If empty, try Bybit
                    if (not order_book.get('bids') or not order_book.get('asks')) and not binance_dead:
                        pass  # Binance might recover
                    if not order_book.get('bids') or not order_book.get('asks'):
                        from data.price_providers import bybit_orderbook
                        ob_alt = bybit_orderbook(symbol)
                        if ob_alt:
                            order_book = ob_alt
                            _log("📖", "DATA", f"{symbol}: Order book من Bybit")
                    t_data_ms = (time.time() - t_data_start) * 1000

                    # Log data integrity
                    kline_count = len(klines) if isinstance(klines, list) else 0
                    ob_bids = len(order_book.get('bids', []))
                    ob_asks = len(order_book.get('asks', []))
                    fetch_data_done(symbol, kline_count)

                    # Check order book health
                    if ob_bids == 0 or ob_asks == 0:
                        _log("⚠️", "DATA", f"{symbol}: Order book empty — bids={ob_bids} asks={ob_asks}")

                    if not klines or kline_count < 20:
                        no_signal(symbol, f"بيانات غير كافية ({kline_count} شمعة)")
                        _log("⚠️", "DATA", f"{symbol}/{tf}: بيانات شموع غير كافية — {kline_count}/100 مطلوب ≥20")
                        continue

                    # Log candle summary for Telegram
                    try:
                        from data.binance_api import extract_ohlcv
                        ohlcv = extract_ohlcv(klines)
                        c = ohlcv['close']
                        if c:
                            _log("🕯️", "CANDLES",
                                 f"{symbol}/{tf}: {kline_count} شمعة | "
                                 f"سعر {c[-1]:.6f} | "
                                 f"أعلى {max(ohlcv['high']):.6f} | "
                                 f"أدنى {min(ohlcv['low']):.6f} | "
                                 f"حجم {sum(ohlcv['volume'][-5:]):,.0f}")
                    except Exception:
                        pass

                    # ── PRECOMPUTE INDICATORS (once per coin/timeframe) ──
                    t_ind_start = time.time()
                    indicators = precompute_indicators(klines)
                    t_ind_ms = (time.time() - t_ind_start) * 1000
                    if indicators.get('atr', 0) > 0:
                        _log("📐", "INDICATOR",
                             f"{symbol}/{tf}: ATR={indicators['atr']:.8f} "
                             f"RSI={indicators.get('rsi', 0):.1f} "
                             f"ADX={indicators.get('adx', {}).get('adx', 0):.1f}")

                    # Pass live price to signal generator for real-time decisions
                    t_gen_start = time.time()
                    # Pass live price so trading decisions use real-time data, not stale kline close
                    result = generate_signal(symbol, tf, klines, order_book, dict(coin),
                                            indicators, live['price'])
                    t_gen_ms = (time.time() - t_gen_start) * 1000

                    # ── DETAILED REPORT (console only) ──
                    try:
                        timing = {
                            'Data Loading': t_data_ms,
                            'Indicators': t_ind_ms,
                            'Signal Generation': t_gen_ms,
                        }
                        dbg = result.get('_debug', {})
                        report = generate_report(symbol, tf, klines, order_book,
                                                result.get('regime', {}),
                                                dbg.get('donchian_signal'),
                                                dbg.get('order_flow_signal'),
                                                dbg.get('decision', {}), timing)
                        print(f"\n{report}")

                        # Also log analysis summary to Telegram buffer
                        regime = result.get('regime', {}).get('regime', '?')
                        metrics = result.get('regime', {}).get('metrics', {})
                        has_sig = result.get('has_signal', False)
                        d_status = '✅' if dbg.get('donchian_signal') else '❌'
                        of_status = '✅' if dbg.get('order_flow_signal') else '❌'
                        sig_line = '🎯 إشارة!' if has_sig else '⏳ لا إشارة'
                        _log("📊", "REPORT",
                             f"{symbol} {tf} | "
                             f"سعر={metrics.get('price', 0):.4f} | "
                             f"{regime} | "
                             f"RSI={metrics.get('rsi', 0):.0f} ADX={metrics.get('adx', 0):.0f} | "
                             f"Donchian={d_status} OrderFlow={of_status} | "
                             f"{sig_line}")
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

    def analysis_loop():
        # Run first analysis immediately
        print("Running initial analysis...")
        analysis_cycle()
        print(f"Initial analysis done. State: {get_state()}")
        # Continue periodic analysis
        while True:
            time.sleep(MONITOR_INTERVAL_SECONDS)
            try:
                analysis_cycle()
            except Exception as e:
                error("MAIN", str(e))
                inc_error()
                time.sleep(10)

    # Start analysis in background — don't block server startup!
    threading.Thread(target=analysis_loop, daemon=True).start()

    # Build and start server immediately
    app = build_application()

    port = int(os.environ.get('PORT', 10000))
    base = WEBHOOK_BASE_URL or f'http://localhost:{port}'
    webhook_url = f"{base}/webhook"

    if not WEBHOOK_BASE_URL:
        print(f"⚠️  WEBHOOK_BASE_URL not set — using {webhook_url}")

    print(f"🚀 Starting server on port {port}")
    run_webhook(app, webhook_url, port)


if __name__ == "__main__":
    main()
