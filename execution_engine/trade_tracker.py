import logging
from database import db
from data_layer import fetch_data

logger = logging.getLogger("trade_tracker")

def create_trade(signal, symbol, timeframe):
    """Create a new paper trade (auto-tracked)."""
    sql = """
        INSERT INTO tracked_trades 
        (symbol, timeframe, strategy, direction, entry_price, stop_loss, take_profit,
         regime_at_entry, confidence, atr_at_entry, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN')
        RETURNING id
    """
    result = db.query_one(sql, (
        symbol, timeframe, signal["strategy"], signal["signal"],
        signal["entry_price"], signal["stop_loss"], signal["take_profit"],
        signal.get("regime", "UNKNOWN"), signal["confidence"], signal.get("atr", 0)
    ))
    
    trade_id = result["id"] if result else None
    logger.info(f"Trade created: #{trade_id} {signal['signal']} {symbol} @ {signal['entry_price']}")
    return trade_id

def log_signal(signal, symbol, timeframe):
    """Log every signal for analysis."""
    sql = """
        INSERT INTO signals_log 
        (symbol, timeframe, strategy, signal, price, stop_loss, take_profit, regime, confidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    db.query(sql, (
        symbol, timeframe, signal["strategy"], signal["signal"],
        signal["entry_price"], signal["stop_loss"], signal["take_profit"],
        signal.get("regime", "UNKNOWN"), signal["confidence"]
    ), fetch=False)

def check_open_trades():
    """
    Check all open trades against current prices.
    Returns list of trades that just closed (SL or TP hit).
    """
    open_trades = db.query(
        "SELECT * FROM tracked_trades WHERE status = 'OPEN' ORDER BY entry_time DESC"
    )
    
    if not open_trades:
        return []
    
    closed = []
    
    for trade in open_trades:
        symbol = trade["symbol"]
        current_price = fetch_data.fetch_current_price(symbol)
        
        if not current_price:
            logger.warning(f"Could not get price for {symbol}")
            continue
        
        # Update current price
        db.query(
            "UPDATE tracked_trades SET current_price = %s WHERE id = %s",
            (current_price, trade["id"]),
            fetch=False
        )
        
        direction = trade["direction"]
        sl = trade["stop_loss"]
        tp = trade["take_profit"]
        entry = trade["entry_price"]
        closed_now = False
        exit_reason = None
        
        if direction == "BUY":
            if current_price <= sl:
                closed_now = True
                exit_reason = "SL"
            elif current_price >= tp:
                closed_now = True
                exit_reason = "TP"
        else:  # SELL
            if current_price >= sl:
                closed_now = True
                exit_reason = "SL"
            elif current_price <= tp:
                closed_now = True
                exit_reason = "TP"
        
        if closed_now:
            # Calculate P&L
            if direction == "BUY":
                pnl_pct = ((current_price - entry) / entry) * 100
            else:
                pnl_pct = ((entry - current_price) / entry) * 100
            
            status = "CLOSED_SL" if exit_reason == "SL" else "CLOSED_TP"
            
            db.query(
                """UPDATE tracked_trades 
                   SET status = %s, exit_time = NOW(), exit_reason = %s,
                       current_price = %s, pnl_pct = %s
                   WHERE id = %s""",
                (status, exit_reason, current_price, round(pnl_pct, 4), trade["id"]),
                fetch=False
            )
            
            closed.append({
                "id": trade["id"],
                "symbol": symbol,
                "direction": direction,
                "entry": entry,
                "exit": current_price,
                "sl": sl,
                "tp": tp,
                "pnl_pct": round(pnl_pct, 4),
                "reason": exit_reason,
                "strategy": trade["strategy"]
            })
            
            logger.info(f"Trade #{trade['id']} CLOSED ({exit_reason}): {symbol} {direction} "
                       f"PnL={pnl_pct:+.2f}%")
    
    return closed

def get_open_trades():
    """Get all currently open trades."""
    return db.query(
        "SELECT * FROM tracked_trades WHERE status = 'OPEN' ORDER BY entry_time DESC"
    )

def get_recent_trades(limit=10):
    """Get recent closed trades."""
    return db.query(
        """SELECT * FROM tracked_trades 
           WHERE status LIKE 'CLOSED%%' 
           ORDER BY exit_time DESC NULLS LAST LIMIT %s""",
        (limit,)
    )