import logging
from database import db
from datetime import datetime, timedelta

logger = logging.getLogger("risk_management")

def get_system_state():
    """Get current system state."""
    return db.query_one("SELECT * FROM system_state WHERE id = 1")

def update_daily_pnl():
    """Calculate today's P&L from closed trades."""
    today = datetime.utcnow().date()
    result = db.query_one(
        """SELECT 
              COUNT(*) as trades,
              SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
              SUM(CASE WHEN pnl_pct <= 0 THEN 1 ELSE 0 END) as losses,
              COALESCE(SUM(pnl_pct), 0) as daily_pnl
           FROM tracked_trades 
           WHERE status LIKE 'CLOSED%%' 
           AND exit_time::date = %s""",
        (today,)
    )
    return result

def check_circuit_breaker():
    """
    Check if daily loss exceeds threshold.
    Returns True if circuit breaker should be active.
    """
    state = get_system_state()
    if state and state["circuit_breaker_active"]:
        return True
    
    from config import MAX_DAILY_LOSS_PCT
    today_data = update_daily_pnl()
    
    if today_data and today_data["daily_pnl"] is not None:
        daily_loss = today_data["daily_pnl"]
        if daily_loss <= -MAX_DAILY_LOSS_PCT:
            db.query(
                "UPDATE system_state SET circuit_breaker_active = TRUE WHERE id = 1",
                fetch=False
            )
            logger.warning(f"CIRCUIT BREAKER ACTIVATED! Daily PnL: {daily_loss:.2f}%")
            return True
    
    # Check consecutive losses
    recent = db.query(
        """SELECT pnl_pct FROM tracked_trades 
           WHERE status LIKE 'CLOSED%%' 
           ORDER BY exit_time DESC NULLS LAST LIMIT 20"""
    )
    
    if recent:
        consec = 0
        for r in recent:
            if r["pnl_pct"] is not None and r["pnl_pct"] <= 0:
                consec += 1
            else:
                break
        
        from config import MAX_CONSECUTIVE_LOSSES
        if consec >= MAX_CONSECUTIVE_LOSSES:
            db.query(
                "UPDATE system_state SET circuit_breaker_active = TRUE WHERE id = 1",
                fetch=False
            )
            logger.warning(f"CIRCUIT BREAKER: {consec} consecutive losses!")
            return True
        
        db.query(
            "UPDATE system_state SET consecutive_losses = %s WHERE id = 1",
            (consec,), fetch=False
        )
    
    return False

def check_drawdown():
    """Calculate and check max drawdown."""
    # Simple drawdown: lowest point vs highest point in equity curve
    result = db.query_one(
        """SELECT 
              COALESCE(SUM(pnl_pct), 0) as total_pnl,
              COUNT(*) as total_trades
           FROM tracked_trades WHERE status LIKE 'CLOSED%%'"""
    )
    
    if result and result["total_pnl"] is not None:
        total = result["total_pnl"]
        # Get running max and current drawdown
        trades = db.query(
            """SELECT pnl_pct FROM tracked_trades 
               WHERE status LIKE 'CLOSED%%' ORDER BY exit_time ASC NULLS LAST"""
        )
        
        if trades:
            peak = 0
            max_dd = 0
            running = 0
            for t in trades:
                running += t["pnl_pct"] or 0
                if running > peak:
                    peak = running
                dd = peak - running
                if dd > max_dd:
                    max_dd = dd
            
            db.query(
                "UPDATE system_state SET max_drawdown_pct = %s, total_pnl_pct = %s WHERE id = 1",
                (round(max_dd, 4), round(running, 4)),
                fetch=False
            )
            
            from config import MAX_DRAWDOWN_PCT
            if max_dd >= MAX_DRAWDOWN_PCT:
                logger.warning(f"MAX DRAWDOWN REACHED: {max_dd:.2f}%")
                return True
    
    return False

def reset_circuit_breaker():
    """Reset circuit breaker (new day)."""
    db.query(
        "UPDATE system_state SET circuit_breaker_active = FALSE WHERE id = 1",
        fetch=False
    )

def is_trading_allowed():
    """Check if trading is currently allowed."""
    state = get_system_state()
    if not state:
        return True
    if state["kill_switch_active"]:
        return False
    if state["circuit_breaker_active"]:
        return False
    if not state["bot_running"]:
        return False
    return True