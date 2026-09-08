# -*- coding: utf-8 -*-
"""محرك المحفظة الورقية (بدون رافعة): تحجيم الصفقات + متابعة الخروج + الإحصائيات."""
import uuid
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def position_size(equity: float, risk_pct: float, entry: float, sl: float,
                  leverage: int = 1, min_notional: float = 5.0) -> dict | None:
    """حجم الصفقة من المخاطرة (بدون رافعة: الهامش = كامل القيمة). يرجع None إذا تعذر."""
    if equity <= 0 or entry <= 0:
        return None
    risk_amount = equity * risk_pct / 100
    dist = abs(entry - sl)
    if dist <= 0:
        return None
    qty = risk_amount / dist
    notional = qty * entry
    margin = notional / max(leverage, 1)
    max_margin = equity * 0.30  # سقف حجم الصفقة 30% من المحفظة
    if margin > max_margin and margin > 0:
        scale = max_margin / margin
        qty *= scale
        notional = qty * entry
        margin = notional / max(leverage, 1)
        risk_amount = qty * dist
    if notional < min_notional:
        return None
    return {"qty": qty, "notional": notional, "margin": margin, "risk_amount": risk_amount}


def build_open_trade(signal, sizing: dict, leverage: int = 1) -> dict:
    return {
        "id": uuid.uuid4().hex[:12],
        "symbol": signal.symbol,
        "side": signal.side,
        "entry_price": signal.entry,
        "qty": sizing["qty"],
        "leverage": leverage,
        "margin": sizing["margin"],
        "notional": sizing["notional"],
        "sl": signal.sl,
        "tp": signal.tp,
        "risk_amount": sizing["risk_amount"],
        "entry_time": _now_iso(),
        "entry_reasons": signal.reasons,
        "snapshot": signal.snapshot,
        "current_price": signal.entry,
        "unrealized_pnl": 0.0,
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
    """يفحص هل يجب إغلاق الصفقة. يرجع (نعم/لا، السبب، سعر الخروج)."""
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


def close_trade(trade: dict, exit_price: float, exit_reason: str, fee_pct: float) -> dict:
    qty = trade["qty"]
    if trade["side"] == "LONG":
        gross = (exit_price - trade["entry_price"]) * qty
    else:
        gross = (trade["entry_price"] - exit_price) * qty
    fees = (trade["notional"] + qty * exit_price) * fee_pct / 100
    pnl = gross - fees
    margin = trade.get("margin") or 0
    risk = trade.get("risk_amount") or 0
    now = datetime.now(timezone.utc)
    try:
        entry_dt = datetime.fromisoformat(trade["entry_time"])
        dur_min = (now - entry_dt).total_seconds() / 60
    except Exception:
        dur_min = 0
    return {
        **{k: v for k, v in trade.items() if k not in ("current_price", "unrealized_pnl", "updated_at")},
        "exit_price": exit_price,
        "exit_time": now.isoformat(),
        "exit_reason": exit_reason,
        "pnl": round(pnl, 4),
        "pnl_pct": round((pnl / margin * 100) if margin else 0, 2),
        "r_multiple": round((pnl / risk) if risk else 0, 2),
        "fees": round(fees, 4),
        "result": "WIN" if pnl > 0 else "LOSS",
        "duration_min": round(dur_min, 1),
    }


def compute_performance(open_trades: list, closed_trades: list, realized: float,
                        start_balance: float, open_pnl: float) -> dict:
    wins = [t for t in closed_trades if t.get("result") == "WIN"]
    losses = [t for t in closed_trades if t.get("result") == "LOSS"]
    gross_win = sum(t.get("pnl", 0) for t in wins)
    gross_loss = abs(sum(t.get("pnl", 0) for t in losses))
    balance = start_balance + realized
    equity = balance + open_pnl
    per_symbol: dict = {}
    for t in closed_trades:
        s = per_symbol.setdefault(t.get("symbol", "?"), {"n": 0, "pnl": 0.0, "wins": 0})
        s["n"] += 1
        s["pnl"] += t.get("pnl", 0)
        if t.get("result") == "WIN":
            s["wins"] += 1
    best = worst = None
    if per_symbol:
        best = max(per_symbol.items(), key=lambda kv: kv[1]["pnl"])[0]
        worst = min(per_symbol.items(), key=lambda kv: kv[1]["pnl"])[0]
    n = len(closed_trades)
    return {
        "equity": round(equity, 2),
        "balance": round(balance, 2),
        "realized_pnl": round(realized, 2),
        "open_pnl": round(open_pnl, 2),
        "return_pct": round((equity - start_balance) / start_balance * 100, 2),
        "total_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "winrate": round(len(wins) / n * 100, 1) if n else 0,
        "avg_win": round(gross_win / len(wins), 2) if wins else 0,
        "avg_loss": round(gross_loss / len(losses), 2) if losses else 0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else (round(gross_win, 2) if gross_win else 0),
        "best_symbol": best,
        "worst_symbol": worst,
        "open_count": len(open_trades),
        "per_symbol": {k: {"n": v["n"], "pnl": round(v["pnl"], 2)} for k, v in per_symbol.items()},
    }
