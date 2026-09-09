# -*- coding: utf-8 -*-
"""محرك المحفظة الورقية (§24 + §27-29): تحجيم + تنفيذ بانزلاق + مقاييس R.

الحالات (§27): CANDIDATE → VALIDATED → APPROVED → OPEN → MONITORING → CLOSED
(تُمثَّل بحقول status/state/exit_code على الإشارة والصفقة).
"""
import math
import uuid
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _exit_code(reason: str) -> str:
    r = reason or ""
    if "الهدف" in r:
        return "TP"
    if "وقف" in r:
        return "SL"
    if "معاكسة" in r:
        return "REVERSE"
    if "المدة" in r:
        return "TIMEOUT"
    return "MANUAL"


# =====================================================
#  نموذج التنفيذ: انزلاق (§29) + رسوم
# =====================================================

def fill_price(side: str, price: float, bps: float, is_entry: bool) -> float:
    """سعر التنفيذ بعد الانزلاق (دائماً أسوأ من المطلوب)."""
    slip = (bps or 0) / 10000
    if side == "LONG":
        return price * (1 + slip) if is_entry else price * (1 - slip)
    return price * (1 - slip) if is_entry else price * (1 + slip)


def position_size(equity: float, risk_pct: float, entry: float, sl: float,
                  leverage: int = 1, min_notional: float = 5.0,
                  max_notional_pct: float = 30.0) -> dict | None:
    """§24: الحجم من المخاطرة + حدود القيمة + تقريب الكمية (step)."""
    if equity <= 0 or entry <= 0:
        return None
    risk_amount = equity * risk_pct / 100
    dist = abs(entry - sl)
    if dist <= 0:
        return None
    qty = risk_amount / dist
    notional = qty * entry
    margin = notional / max(leverage, 1)
    max_margin = equity * max_notional_pct / 100 / max(leverage, 1)
    if margin > max_margin and margin > 0:
        scale = max_margin / margin
        qty *= scale
        notional = qty * entry
        margin = notional / max(leverage, 1)
        risk_amount = qty * dist
    if notional < min_notional:
        return None
    if margin > equity:  # لا يملك النقد الكافي
        return None
    qty = float(f"{qty:.6f}")  # محاكاة step size
    if qty <= 0:
        return None
    notional = qty * entry
    margin = notional / max(leverage, 1)
    return {"qty": qty, "notional": notional, "margin": margin,
            "risk_amount": qty * dist}


def build_open_trade(signal, sizing: dict, leverage: int = 1,
                     slippage_bps: float = 5) -> dict:
    req = signal.entry
    filled = fill_price(signal.side, req, slippage_bps, True)
    slip_cost = abs(filled - req) * sizing["qty"]
    return {
        "id": uuid.uuid4().hex[:12],
        "signal_id": getattr(signal, "signal_id", ""),
        "setup_id": getattr(signal, "setup_id", ""),
        "state": "OPEN",
        "symbol": signal.symbol,
        "side": signal.side,
        "entry_price": filled,
        "qty": sizing["qty"],
        "leverage": leverage,
        "margin": sizing["margin"],
        "notional": sizing["notional"],
        "sl": signal.stop_loss,
        "tp": signal.take_profit,
        "risk_amount": sizing["risk_amount"],
        "entry_time": _now_iso(),
        "entry_reasons": signal.reasons,
        "snapshot": {**(signal.snapshot or {}), "entry_requested": req,
                     "entry_slip": round(slip_cost, 4),
                     "strength": getattr(signal, "score", ""),
                     "score": getattr(signal, "score", 0)},
        "current_price": filled,
        "unrealized_pnl": round(-slip_cost, 4),  # سيُحسب بدقة مع الرسوم في الدورة
    }


def unrealized(trade: dict, live_price: float, fee_pct: float) -> float:
    qty = trade["qty"]
    if trade["side"] == "LONG":
        gross = (live_price - trade["entry_price"]) * qty
    else:
        gross = (trade["entry_price"] - live_price) * qty
    fees = (trade["notional"] + qty * live_price) * fee_pct / 100
    return gross - fees


def check_exit(trade: dict, live_price: float, entry_dt, now_dt,
               max_hold_hours: float, opposite_signal: bool = False) -> tuple[bool, str, float]:
    if live_price is None or live_price <= 0:
        return False, "", 0.0
    side = trade["side"]
    sl, tp = trade["sl"], trade["tp"]
    if side == "LONG":
        if live_price <= sl:
            return True, "ضرب وقف الخسارة 🛑", sl
        if live_price >= tp:
            return True, "تحقيق الهدف 🎯", tp
    else:
        if live_price >= sl:
            return True, "ضرب وقف الخسارة 🛑", sl
        if live_price <= tp:
            return True, "تحقيق الهدف 🎯", tp
    if opposite_signal:
        return True, "إشارة معاكسة من الاستراتيجية 🔄", live_price
    try:
        held_h = (now_dt - entry_dt).total_seconds() / 3600
    except Exception:
        held_h = 0
    if held_h >= max_hold_hours:
        return True, f"انتهت المدة القصوى ({max_hold_hours:.0f} ساعة) ⏱️", live_price
    return False, "", 0.0


def close_trade(trade: dict, exit_requested: float, exit_reason: str,
                fee_pct: float, slippage_bps: float = 5) -> dict:
    qty = trade["qty"]
    filled = fill_price(trade["side"], exit_requested, slippage_bps, False)
    if trade["side"] == "LONG":
        gross = (filled - trade["entry_price"]) * qty
    else:
        gross = (trade["entry_price"] - filled) * qty
    fees = (trade["notional"] + qty * filled) * fee_pct / 100
    pnl = gross - fees
    margin = trade.get("margin") or 0
    risk = trade.get("risk_amount") or 0
    entry_slip = (trade.get("snapshot") or {}).get("entry_slip", 0) or 0
    exit_slip = abs(filled - exit_requested) * qty
    now = datetime.now(timezone.utc)
    try:
        entry_dt = datetime.fromisoformat(trade["entry_time"])
        dur_min = (now - entry_dt).total_seconds() / 60
    except Exception:
        dur_min = 0
    snap = dict(trade.get("snapshot") or {})
    snap.update({"exit_requested": exit_requested,
                 "slippage_cost": round(entry_slip + exit_slip, 4)})
    return {
        **{k: v for k, v in trade.items()
           if k not in ("current_price", "unrealized_pnl", "updated_at")},
        "state": "CLOSED",
        "exit_price": filled,
        "exit_time": now.isoformat(),
        "exit_reason": exit_reason,
        "exit_code": _exit_code(exit_reason),
        "snapshot": snap,
        "pnl": round(pnl, 4),
        "pnl_pct": round((pnl / margin * 100) if margin else 0, 2),
        "r_multiple": round((pnl / risk) if risk else 0, 2),
        "fees": round(fees, 4),
        "result": "WIN" if pnl > 0 else "LOSS",
        "duration_min": round(dur_min, 1),
    }


# =====================================================
#  الأداء (§44-46)
# =====================================================

def _std(xs: list) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mu = sum(xs) / n
    var = sum((x - mu) ** 2 for x in xs) / (n - 1)
    return math.sqrt(var) if var > 0 else 0.0


def max_drawdown_pct(equity_curve: list) -> float:
    peak, mdd = 0.0, 0.0
    for e in equity_curve:
        peak = max(peak, e)
        if peak > 0:
            mdd = max(mdd, (peak - e) / peak * 100)
    return round(mdd, 2)


def compute_performance(open_trades: list, closed_trades: list, realized: float,
                        start_balance: float, open_pnl: float) -> dict:
    wins = [t for t in closed_trades if t.get("result") == "WIN"]
    losses = [t for t in closed_trades if t.get("result") == "LOSS"]
    gross_win = sum(t.get("pnl", 0) for t in wins)
    gross_loss = abs(sum(t.get("pnl", 0) for t in losses))
    balance = start_balance + realized
    equity = balance + open_pnl
    n = len(closed_trades)

    r_all = [float(t.get("r_multiple", 0) or 0) for t in closed_trades]
    r_win = [r for r in r_all if r > 0]
    r_loss = [r for r in r_all if r <= 0]
    pwin = len(wins) / n if n else 0
    expectancy_r = round(pwin * (sum(r_win) / len(r_win) if r_win else 0)
                         - (1 - pwin) * (abs(sum(r_loss) / len(r_loss)) if r_loss else 0), 3)

    ordered = sorted(closed_trades, key=lambda t: t.get("exit_time", ""))
    curve, run = [], start_balance
    for t in ordered:
        run += t.get("pnl", 0) or 0
        curve.append(run)
    mdd = max_drawdown_pct(curve) if curve else 0.0
    sd = _std(r_all)
    sharpe_like = round((sum(r_all) / n) / sd * math.sqrt(n), 2) if n > 2 and sd else 0.0
    avg_hold = round(sum(float(t.get("duration_min", 0) or 0) for t in closed_trades) / n, 1) if n else 0

    per_symbol: dict = {}
    per_side = {"LONG": {"n": 0, "pnl": 0.0}, "SHORT": {"n": 0, "pnl": 0.0}}
    for t in closed_trades:
        s = per_symbol.setdefault(t.get("symbol", "?"), {"n": 0, "pnl": 0.0, "wins": 0})
        s["n"] += 1
        s["pnl"] += t.get("pnl", 0)
        if t.get("result") == "WIN":
            s["wins"] += 1
        sd_side = per_side.get(t.get("side", ""), None)
        if sd_side is not None:
            sd_side["n"] += 1
            sd_side["pnl"] += t.get("pnl", 0)
    best = worst = None
    if per_symbol:
        best = max(per_symbol.items(), key=lambda kv: kv[1]["pnl"])[0]
        worst = min(per_symbol.items(), key=lambda kv: kv[1]["pnl"])[0]

    return {
        "equity": round(equity, 2), "balance": round(balance, 2),
        "realized_pnl": round(realized, 2), "open_pnl": round(open_pnl, 2),
        "return_pct": round((equity - start_balance) / start_balance * 100, 2),
        "total_trades": n, "wins": len(wins), "losses": len(losses),
        "winrate": round(len(wins) / n * 100, 1) if n else 0,
        "avg_win": round(gross_win / len(wins), 2) if wins else 0,
        "avg_loss": round(gross_loss / len(losses), 2) if losses else 0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else (round(gross_win, 2) if gross_win else 0),
        "expectancy_r": expectancy_r,
        "avg_r": round(sum(r_all) / n, 3) if n else 0,
        "max_drawdown_pct": mdd,
        "sharpe_like": sharpe_like,
        "avg_hold_min": avg_hold,
        "best_symbol": best, "worst_symbol": worst,
        "per_side": {k: {"n": v["n"], "pnl": round(v["pnl"], 2)} for k, v in per_side.items()},
        "open_count": len(open_trades),
        "per_symbol": {k: {"n": v["n"], "pnl": round(v["pnl"], 2)} for k, v in per_symbol.items()},
    }
