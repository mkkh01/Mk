"""CTM Bot — Risk Manager — portfolio-level risk + circuit breaker."""
from datetime import datetime, timezone
from config import MAX_DAILY_LOSS, MAX_CONSECUTIVE_LOSSES, DEFAULT_RISK_PERCENT
from db.supabase_client import get_recent_results
from utils.logger import error as _lerror, _log


def check_pre_trade_risk(symbol: str, capital_value: float, risk_percent: float) -> tuple[bool, str]:
    """
    Check if a new trade can be opened.
    
    Returns (allowed: bool, reason: str).
    """
    # 1. Circuit breaker active?
    from utils.state import get_state
    state = get_state()
    if state.get('circuit_breaker'):
        return False, f"قاطع دائرة نشط: {state.get('circuit_breaker_reason', 'غير معروف')}"

    # 2. Daily loss check
    daily_pnl = _get_daily_pnl()
    if daily_pnl is not None:
        # Daily loss as % of total tracked capital
        from db.supabase_client import get_active_coins
        coins = get_active_coins()
        total_capital = sum(c.get('capital_value', 100) for c in coins) if coins else capital_value
        daily_loss_pct = abs(daily_pnl) / total_capital * 100 if daily_pnl < 0 and total_capital > 0 else 0
        if daily_loss_pct >= MAX_DAILY_LOSS:
            from utils.state import trigger_circuit_breaker
            trigger_circuit_breaker(f"تجاوز الخسارة اليومية {daily_loss_pct:.1f}% (الحد: {MAX_DAILY_LOSS}%)")
            return False, f"الخسارة اليومية {daily_loss_pct:.1f}% تجاوزت الحد {MAX_DAILY_LOSS}%"

    # 3. Consecutive losses
    consec = _count_consecutive_losses()
    if consec >= MAX_CONSECUTIVE_LOSSES:
        from utils.state import trigger_circuit_breaker
        trigger_circuit_breaker(f"خسائر متتالية: {consec} (الحد: {MAX_CONSECUTIVE_LOSSES})")
        return False, f"خسائر متتالية ({consec}) تجاوزت الحد ({MAX_CONSECUTIVE_LOSSES})"

    # 4. Portfolio exposure check
    from db.supabase_client import get_active_signals as _act
    active_trades = _act()
    active_value = sum(t.get('capital_value', 0) for t in active_trades)
    total_value = active_value + capital_value
    coins = __import__('db.supabase_client', fromlist=['get_active_coins']).get_active_coins()
    max_portfolio = sum(c.get('capital_value', 100) for c in coins) if coins else 10000
    if total_value > max_portfolio:
        return False, f"تجاوز إجمالي التعرض: {total_value:.0f} > {max_portfolio:.0f} USDT"

    return True, "OK"


def _get_daily_pnl() -> float | None:
    """Get today's total PnL from trade_results."""
    try:
        results = get_recent_results(100)
        if not results:
            return None
        today = datetime.now(timezone.utc).date()
        daily = 0.0
        for r in results:
            closed = r.get('closed_at')
            if closed:
                if hasattr(closed, 'date'):
                    closed_date = closed.date()
                elif isinstance(closed, str):
                    closed_date = datetime.fromisoformat(closed.replace('Z', '+00:00')).date()
                else:
                    continue
                if closed_date == today:
                    daily += r.get('profit_usd', 0)
        return daily
    except Exception as e:
        _lerror("RISK", f"Daily PnL lookup failed: {e}")
        return None


def _count_consecutive_losses() -> int:
    """Count consecutive losing trades from most recent results."""
    try:
        results = get_recent_results(50)
        if not results:
            return 0
        count = 0
        for r in results:
            if r.get('profit_pct', 0) < 0:
                count += 1
            else:
                break  # stop at first win
        return count
    except Exception as e:
        _lerror("RISK", f"Consecutive loss lookup failed: {e}")
        return 0


def get_portfolio_summary() -> dict:
    """Get full portfolio risk summary."""
    from db.supabase_client import get_active_coins, get_active_signals as _act
    coins = get_active_coins()
    trades = _act()
    results = get_recent_results(100)

    total_capital = sum(c.get('capital_value', 100) for c in coins)
    active_exposure = sum(t.get('capital_value', 0) for t in trades)

    wins = sum(1 for r in results if r.get('profit_pct', 0) > 0)
    total_pnl = sum(r.get('profit_usd', 0) for r in results)

    return {
        'coins_count': len(coins),
        'total_capital': total_capital,
        'active_trades': len(trades),
        'active_exposure': active_exposure,
        'exposure_pct': round(active_exposure / total_capital * 100, 1) if total_capital > 0 else 0,
        'total_pnl': round(total_pnl, 2),
        'daily_pnl': _get_daily_pnl(),
        'consecutive_losses': _count_consecutive_losses(),
        'win_rate': round(wins / len(results) * 100, 1) if results else 0,
        'total_results': len(results),
    }
