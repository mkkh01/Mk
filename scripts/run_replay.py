from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from monitoring.logger import configure_logging  # noqa: E402
from replay.runner import ReplayRunner, load_candles  # noqa: E402

SYMBOLS = ["ADAUSDT", "DOTUSDT", "LINKUSDT", "NEARUSDT", "XLMUSDT", "XRPUSDT"]
TIMEFRAMES = ["15m", "1h", "4h"]


async def async_main(
    data_dir: Path,
    output_path: Path,
    start: datetime,
    end: datetime,
) -> None:
    configure_logging(logging.WARNING)
    candles = load_candles(data_dir, SYMBOLS, TIMEFRAMES)
    runner = ReplayRunner(
        candles,
        symbols=SYMBOLS,
        trigger_timeframe="15m",
        start=start,
        end=end,
    )
    report = await runner.run()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.to_json() + "\n", encoding="utf-8")
    print(report.to_json())


def main() -> int:
    data_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "data/replay")
    output_path = Path(sys.argv[2] if len(sys.argv) > 2 else "reports/replay_sample.json")
    start = datetime.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else datetime(2026, 7, 28, tzinfo=timezone.utc)
    end = datetime.fromisoformat(sys.argv[4]) if len(sys.argv) > 4 else datetime(2026, 7, 31, 23, 59, 59, tzinfo=timezone.utc)
    asyncio.run(async_main(data_dir, output_path, start, end))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
