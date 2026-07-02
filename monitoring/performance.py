import logging
from database import db
from datetime import datetime, timedelta

logger = logging.getLogger("performance")

def update_performance():
    """Recalculate all performance metrics."""
    today = datetime.utcnow().date()
    
    # Update today's snapshot
    stats = db.query_one(
        """SELECT 
              COUNT(*) as total,
              SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
              SUM(CASE WHEN pnl_pct <= 0 THEN 1 ELSE 0 END) as losses,
              COALESCE(SUM(pnl_pct), 0) as pnl,
              COALESCE(AVG(CASE WHEN pnl_pct > 0 THEN pnl_pct END), 0) as avg_win,
              COALESCE(AVG(CASE WHEN pnl_pct <= 0 THEN ABS(pnl_pct) END), 0) as avg_loss
           FROM tracked_trades 
           WHERE status LIKE 'CLOSED%%' 
           AND exit_time::date = %s""",
        (today,)
    )
    
    if stats and stats["total"] and stats["total"] > 0:
        win_rate = (stats["wins"] / stats["total"]) * 100
        total_wins = stats["wins"] * stats["avg_win"] if stats["avg_win"] else 0
        total_losses = stats["losses"] * stats["avg_loss"] if stats["avg_loss"] else 0
        profit_factor = total_wins / total_losses if total_losses > 0 else 999
        
        # Get max drawdown
        dd_result = db.query_one(
            "SELECT max_drawdown_pct FROM system_state WHERE id = 1"
        )
        max_dd = dd_result["max_drawdown_pct"] if dd_result else 0
        
        db.query(
            """INSERT INTO performance_snapshots 
               (snapshot_date, total_trades, wins, losses, pnl_pct, 
                max_drawdown_pct, win_rate, profit_factor, avg_win_pct, avg_loss_pct)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (snapshot_date) DO UPDATE SET
               total_trades = EXCLUDED.total_trades,
               wins = EXCLUDED.wins,
               losses = EXCLUDED.losses,
               pnl_pct = EXCLUDED.pnl_pct,
               max_drawdown_pct = EXCLUDED.max_drawdown_pct,
               win_rate = EXCLUDED.win_rate,
               profit_factor = EXCLUDED.profit_factor,
               avg_win_pct = EXCLUDED.avg_win_pct,
               avg_loss_pct = EXCLUDED.avg_loss_pct""",
            (today, stats["total"], stats["wins"], stats["losses"],
             round(stats["pnl"], 4), round(max_dd, 4),
             round(win_rate, 2), round(profit_factor, 2),
             round(stats["avg_win"], 4), round(stats["avg_loss"], 4)),
            fetch=False
        )
        
        # Update system state
        db.query(
            """UPDATE system_state SET 
               total_trades = %s, winning_trades = %s, losing_trades = %s, daily_pnl_pct = %s
               WHERE id = 1""",
            (stats["total"], stats["wins"], stats["losses"], round(stats["pnl"], 4)),
            fetch=False
        )

def get_dashboard_stats():
    """Get comprehensive stats for the dashboard."""
    state = db.query_one("SELECT * FROM system_state WHERE id = 1")
    today = datetime.utcnow().date()
    
    today_perf = db.query_one(
        "SELECT * FROM performance_snapshots WHERE snapshot_date = %s", (today,)
    )
    
    # All-time stats
    all_time = db.query_one(
        """SELECT 
              COUNT(*) as total_trades,
              SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
              SUM(CASE WHEN pnl_pct <= 0 THEN 1 ELSE 0 END) as losses,
              COALESCE(SUM(pnl_pct), 0) as total_pnl,
              COALESCE(MAX(pnl_pct), 0) as best_trade,
              COALESCE(MIN(pnl_pct), 0) as worst_trade
           FROM tracked_trades WHERE status LIKE 'CLOSED%%'"""
    )
    
    return {
        "state": state,
        "today": today_perf,
        "all_time": all_time
    }