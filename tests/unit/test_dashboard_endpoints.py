from __future__ import annotations

from app.dashboard_endpoints import ThresholdsResponse
from config import thresholds


def test_thresholds_response_preserves_dynamic_entry_timing_constants() -> None:
    base_values = {
        name: getattr(thresholds, name)
        for name in ThresholdsResponse.model_fields
        if hasattr(thresholds, name)
    }
    base_values.update(
        {
            "LONG_RSI_NEAR_OVERBOUGHT": thresholds.LONG_RSI_NEAR_OVERBOUGHT,
            "MAX_LONG_EXTENSION_ATR": thresholds.MAX_LONG_EXTENSION_ATR,
            "PULLBACK_LOOKBACK_CANDLES": thresholds.PULLBACK_LOOKBACK_CANDLES,
            "MAX_LIMIT_SLIPPAGE_PCT": thresholds.MAX_LIMIT_SLIPPAGE_PCT,
        }
    )

    payload = ThresholdsResponse(**base_values).model_dump()

    assert payload["LONG_RSI_NEAR_OVERBOUGHT"] == 65.0
    assert payload["MAX_LONG_EXTENSION_ATR"] == 1.25
    assert payload["PULLBACK_LOOKBACK_CANDLES"] == 5
    assert payload["MAX_LIMIT_SLIPPAGE_PCT"] == 0.05
