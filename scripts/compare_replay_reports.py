from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _metrics(report: dict[str, Any]) -> dict[str, Any]:
    outcomes = report.get("outcomes", [])
    filled = [item for item in outcomes if item.get("filled")]
    closed = [item for item in filled if item.get("outcome") in {"tp", "sl"}]
    wins = [item for item in closed if item.get("outcome") == "tp"]
    losses = [item for item in closed if item.get("outcome") == "sl"]
    no_fill = [item for item in outcomes if item.get("outcome") == "no_fill"]
    open_at_end = [item for item in outcomes if item.get("outcome") == "open_at_end"]

    r_values = [float(item["r_multiple"]) for item in closed if item.get("r_multiple") is not None]
    gross_profit = sum(value for value in r_values if value > 0)
    gross_loss = sum(value for value in r_values if value < 0)
    profit_factor = (
        gross_profit / abs(gross_loss) if gross_loss < 0 else None
    )

    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in r_values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)

    return {
        "profile": report.get("profile", "unknown"),
        "start": report.get("start", ""),
        "end": report.get("end", ""),
        "decisions": report.get("decisions", 0),
        "signals_found": report.get("signals_found", 0),
        "approved": report.get("approved", 0),
        "rejected": report.get("rejected", 0),
        "signal_quality_passed": report.get("signal_quality_passed", 0),
        "pre_timing_eligible": report.get("pre_timing_eligible", 0),
        "entry_timing_checked": report.get("entry_timing_checked", 0),
        "entry_timing_passed": report.get("entry_timing_passed", 0),
        "db_write_failures": report.get("db_write_failures", 0),
        "outcome_records": len(outcomes),
        "filled": len(filled),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_closed": len(wins) / len(closed) if closed else None,
        "no_fill": len(no_fill),
        "no_fill_rate_outcomes": len(no_fill) / len(outcomes) if outcomes else None,
        "open_at_end": len(open_at_end),
        "gross_profit_r": gross_profit,
        "gross_loss_r": gross_loss,
        "profit_factor": profit_factor,
        "net_r": sum(r_values),
        "max_drawdown_r": max_drawdown,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare replay report outcomes.")
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    rows = []
    for path in args.reports:
        with path.open(encoding="utf-8") as stream:
            rows.append(_metrics(json.load(stream)))

    print(json.dumps(rows, indent=2, ensure_ascii=False))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
