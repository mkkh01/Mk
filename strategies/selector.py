"""Strategy Selection Engine — priority-chain pattern for extensibility."""
from config import MarketRegime


class StrategyEntry:
    """A strategy with a priority and evaluation function."""

    def __init__(self, name: str, priority: int, evaluator, condition=None):
        self.name = name
        self.priority = priority  # lower = higher priority
        self.evaluator = evaluator  # fn(regime, donchian_signal, order_flow_signal, indicators) -> signal|None
        self.condition = condition  # optional fn(regime_data) -> bool


# ── Strategy definitions ──

def _trend_following_long(regime, donchian, order_flow, indicators):
    if regime in [MarketRegime.TREND_UP, MarketRegime.BREAKOUT] and donchian:
        return donchian


def _trend_following_short(regime, donchian, order_flow, indicators):
    if regime == MarketRegime.TREND_DOWN and donchian:
        return donchian


def _order_flow_range(regime, donchian, order_flow, indicators):
    if regime in [MarketRegime.HIGH_VOLATILITY, MarketRegime.RANGE] and order_flow:
        return order_flow


def _order_flow_override(regime, donchian, order_flow, indicators):
    if regime == MarketRegime.TREND_UP and order_flow and not donchian:
        of = order_flow.get('order_flow', {})
        if of.get('volume_ratio', 1) > 2.5:
            return order_flow


# Ordered by priority (lower = checked first)
STRATEGY_CHAIN = [
    StrategyEntry('trend_following_long',  10, _trend_following_long),
    StrategyEntry('trend_following_short', 20, _trend_following_short),
    StrategyEntry('order_flow_range',      30, _order_flow_range),
    StrategyEntry('order_flow_override',   40, _order_flow_override),
]

# Blocked regimes
BLOCKED_REGIMES = {MarketRegime.CAPITULATION, MarketRegime.DISTRIBUTION}


def select_strategy(regime_data: dict, donchian_signal: dict | None,
                    order_flow_signal: dict | None) -> dict:
    """
    Select the best strategy using priority-chain pattern.
    Easily extensible: add a new StrategyEntry to STRATEGY_CHAIN.
    """
    regime = regime_data.get('regime', '')
    confidence = regime_data.get('confidence', 0)

    # Guard: blocked regimes
    if regime in BLOCKED_REGIMES:
        return {
            'selected_strategy': None, 'signal': None,
            'reason': f'No trade: {regime} regime — waiting for stabilization.'
        }

    # Evaluate chain in priority order
    sorted_chain = sorted(STRATEGY_CHAIN, key=lambda s: s.priority)
    for entry in sorted_chain:
        if entry.condition and not entry.condition(regime_data):
            continue
        try:
            signal = entry.evaluator(regime, donchian_signal, order_flow_signal, None)
            if signal:
                return {
                    'selected_strategy': signal.get('strategy', entry.name),
                    'signal': signal,
                    'reason': f'Strategy "{entry.name}" matched (priority {entry.priority}) — regime: {regime}'
                }
        except Exception as e:
            print(f"[SELECTOR] {entry.name} evaluator failed: {e}")
            continue

    # Default: No trade
    reason = f'No trade: regime={regime}, Donchian={"Yes" if donchian_signal else "No"}, OrderFlow={"Yes" if order_flow_signal else "No"}'
    if regime == MarketRegime.LOW_VOLATILITY:
        reason += f', confidence={confidence:.2f}'
    return {'selected_strategy': None, 'signal': None, 'reason': reason}
