from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.thresholds import ENTRY_TIMEOUT_MINUTES  # noqa: E402
from replay.runner import load_candles  # noqa: E402


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _revalue(report: dict[str, Any], candles: dict[tuple[str, str], list[Any]]) -> dict[str, Any]:
    timeframe = report.get("trigger_timeframe", "15m")
    for outcome in report.get("outcomes", []):
        symbol = outcome["symbol"]
        decision_open = _aware(outcome["decision_time"])
        series = candles.get((symbol, timeframe), [])
        source = next((candle for candle in series if candle.open_time == decision_open), None)
        decision_close = source.close_time if source is not None else decision_open
        valid_until = decision_close + timedelta(minutes=ENTRY_TIMEOUT_MINUTES)
        future = [
            candle for candle in series if candle.is_closed and candle.open_time > decision_open
        ]
        fill_candle = next(
            (
                candle for candle in future
                if candle.open_time < valid_until
                and candle.low <= float(outcome["entry_price"])
            ),
            None,
        )
        outcome.update(
            {
                "filled": fill_candle is not None,
                "outcome": "no_fill" if fill_candle is None else "open_at_end",
                "exit_price": None,
                "exit_time": None,
                "fill_time": fill_candle.close_time.isoformat() if fill_candle else None,
                "r_multiple": None,
                "ambiguous_bar": False,
            }
        )
        if fill_candle is None:
            continue

        entry = float(outcome["entry_price"])
        stop_loss = float(outcome["stop_loss"])
        take_profit = float(outcome["take_profit"])
        risk_per_unit = max(entry - stop_loss, 0.0)
        for candle in future:
            if candle.close_time <= fill_candle.close_time:
                continue
            hit_stop = candle.low <= stop_loss
            hit_target = candle.high >= take_profit
            if not hit_stop and not hit_target:
                continue
            ambiguous = hit_stop and hit_target
            hit_target_first = hit_target and not hit_stop
            exit_price = take_profit if hit_target_first else stop_loss
            outcome.update(
                {
                    "outcome": "tp" if hit_target_first else "sl",
                    "exit_price": exit_price,
                    "exit_time": candle.close_time.isoformat(),
                    "r_multiple": (
                        (exit_price - entry) / risk_per_unit
                        if risk_per_unit > 0
                        else None
                    ),
                    "ambiguous_bar": ambiguous,
                }
            )
            break
    report["replay_evaluation_version"] = "historical-limit-timeout-v2"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Revalue replay outcomes with historical limit expiry.")
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("input_report", type=Path)
    parser.add_argument("output_report", type=Path)
    args = parser.parse_args()

    with args.input_report.open(encoding="utf-8") as stream:
        report = json.load(stream)
    symbols = report["symbols"]
    timeframes = [report.get("trigger_timeframe", "15m")]
    candles = load_candles(args.data_dir, symbols, timeframes)
    corrected = _revalue(report, candles)
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.write_text(
        json.dumps(corrected, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(corrected["outcomes"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
