# -*- coding: utf-8 -*-
"""محرك القرار: عقد الموافقة (§65) + نظام النقاط (§18) + الحدود (§19)."""
import uuid
from dataclasses import dataclass, field


@dataclass
class Signal:
    signal_id: str
    symbol: str
    direction: str          # LONG / SHORT
    htf: str
    mtf: str
    ltf: str
    ema_state: str
    ema_slope: float | None
    swing_low: float
    swing_high: float
    swing_id: str
    fib_zone: str           # 61.8 / 50 / 78.6 الأقرب
    fib_price_low: float
    fib_price_high: float
    stoch_k: float
    stoch_d: float
    stoch_cross: str        # BULLISH / BEARISH / NONE
    price_confirmation: str
    score: int
    score_parts: dict = field(default_factory=dict)
    entry: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    rr: float = 0.0
    setup_id: str = ""
    status: str = "APPROVED"   # APPROVED / WATCH
    reasons: list = field(default_factory=list)
    snapshot: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class Decision:
    approved: bool
    direction: str = ""
    trend_valid: bool = False
    swing_valid: bool = False
    fib_valid: bool = False
    stochastic_valid: bool = False
    confirmation_valid: bool = False
    rr_valid: bool = False
    portfolio_valid: bool = True
    score: int = 0
    rejects: list = field(default_factory=list)
    signal: Signal | None = None
    debug: dict = field(default_factory=dict)

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:12]


def new_signal_id() -> str:
    return uuid.uuid4().hex[:12]


def grade(score: int) -> str:
    if score >= 90:
        return "PREMIUM 🏆"
    if score >= 80:
        return "HIGH QUALITY 💪"
    if score >= 70:
        return "VALID SETUP ✅"
    if score >= 60:
        return "WATCH 👀"
    return "REJECT ❌"


def compute_score(p: dict) -> tuple[int, dict]:
    """مجموع 0-100 حسب أوزان §18. p: قيم مُجمّعة من الاستراتيجية."""
    parts: dict = {}

    # --- Trend 25 ---
    dist = abs(p.get("ema_dist_pct", 0) or 0)   # بعد السعر عن EMA %
    t_pos = 10 if dist >= 0.5 else (7 if dist >= 0.2 else 4)
    slope = p.get("slope_state", "")
    t_slope = {"Strong Up": 10, "Weak Up": 6, "Flat": 2}.get(slope, 0) \
        if p.get("side") == "LONG" else {"Strong Down": 10, "Weak Down": 6, "Flat": 2}.get(slope, 0)
    t_htf = 5 if p.get("htf_agree") else 0
    parts["trend"] = t_pos + t_slope + t_htf

    # --- Fibonacci 25 ---
    prox618 = p.get("prox_618", 1.0)  # قرب نسبي 0-1 من 61.8
    prox50 = p.get("prox_50", 1.0)
    f618 = round(12 * max(0.0, min(1.0, prox618)))
    f50 = round(7 * max(0.0, min(1.0, prox50)))
    f_sw = round(max(0.0, min(6.0, p.get("swing_quality", 0) or 0)))
    parts["fibonacci"] = f618 + f50 + f_sw

    # --- Stochastic 20 ---
    k = p.get("stoch_k", 50)
    side = p.get("side")
    if side == "LONG":
        s_ext = 8 if k < 20 else (4 if k < 30 else 0)
    else:
        s_ext = 8 if k > 80 else (4 if k > 70 else 0)
    cross_age = p.get("cross_age_bars", 99)
    s_cross = 8 if cross_age == 0 else (5 if cross_age == 1 else 0)
    s_mom = 4 if p.get("momentum_recovery") else 0
    parts["stochastic"] = s_ext + s_cross + s_mom

    # --- Price Confirmation 20 ---
    conf = p.get("confirmation", "")
    c_main = 10 if conf in ("ENGULFING", "REJECTION", "HAMMER", "SHOOTING_STAR") else 0
    c_trig = 6 if p.get("trigger_break") else 0
    c_vol = 4 if p.get("volume_ok") else 0
    parts["confirmation"] = c_main + c_trig + c_vol

    # --- Zone Confluence 10 ---
    z = p.get("zone_conf", {}) or {}
    z_lvl = 4 if z.get("horizontal") else 0
    z_ema = 3 if z.get("ema_overlap") else 0
    z_re = 3 if (z.get("prev_reactions") or 0) >= 1 else 0
    parts["zone"] = z_lvl + z_ema + z_re

    total = sum(parts.values())
    if p.get("regime") == "RANGING":
        total -= 10  # §34: خفض الثقة في التذبذب
    total = max(0, min(100, int(round(total))))
    parts["total"] = total
    parts["grade"] = grade(total)
    return total, parts
