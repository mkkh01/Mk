from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(PROJECT_ROOT))

from monitoring.logger import configure_logging  # noqa: E402

SYMBOLS = ["ADAUSDT", "DOTUSDT", "LINKUSDT", "NEARUSDT", "XLMUSDT", "XRPUSDT"]
TIMEFRAMES = ["15m", "1h", "4h"]


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def async_main(
    data_dir: Path,
    output_path: Path,
    start: datetime,
    end: datetime,
    profile: str,
) -> None:
    configure_logging(logging.WARNING)
    # Import the runner only after logging is configured; its dependencies
    # create module-level loggers during import.
    from replay.runner import ReplayRunner, load_candles

    candles = load_candles(data_dir, SYMBOLS, TIMEFRAMES)
    runner = ReplayRunner(
        candles,
        symbols=SYMBOLS,
        trigger_timeframe="15m",
        start=start,
        end=end,
        profile=profile,
    )
    report = await runner.run()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.to_json() + "\n", encoding="utf-8")
    print(report.to_json())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a closed-candle replay using an isolated risk profile."
    )
    parser.add_argument("data_dir", nargs="?", default="data/replay")
    parser.add_argument("output_path", nargs="?", default="reports/replay_sample.json")
    parser.add_argument(
        "start",
        nargs="?",
        default="2026-07-28T00:00:00+00:00",
        type=_parse_datetime,
    )
    parser.add_argument(
        "end",
        nargs="?",
        default="2026-07-31T23:59:59+00:00",
        type=_parse_datetime,
    )
    parser.add_argument(
        "--profile",
        choices=("default", "1to1"),
        default="default",
        help="Replay-only risk profile; production thresholds remain unchanged.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    asyncio.run(
        async_main(
            Path(args.data_dir),
            Path(args.output_path),
            args.start,
            args.end,
            args.profile,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
