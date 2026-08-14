from __future__ import annotations

import csv
import io
import sys
import zipfile
from pathlib import Path

import requests

SYMBOLS = ["ADAUSDT", "DOTUSDT", "LINKUSDT", "NEARUSDT", "XLMUSDT", "XRPUSDT"]
INTERVALS = ["15m", "1h", "4h"]
MONTHS = ["2026-06", "2026-07"]
BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"


def download_month(symbol: str, interval: str, month: str) -> bytes:
    url = f"{BASE_URL}/{symbol}/{interval}/{symbol}-{interval}-{month}.zip"
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.content


def convert_rows(raw_zip: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as archive:
        csv_names = [name for name in archive.namelist() if name.endswith(".csv")]
        if len(csv_names) != 1:
            raise RuntimeError(f"expected one CSV in archive, got {csv_names}")
        with archive.open(csv_names[0]) as stream:
            text = io.TextIOWrapper(stream, encoding="utf-8")
            rows: list[list[str]] = []
            for row in csv.reader(text):
                if not row or not row[0].isdigit():
                    continue
                if len(row) < 11:
                    raise RuntimeError(f"unexpected kline row width: {len(row)}")
                rows.append(row)
            return rows


def write_symbol_interval(out_dir: Path, symbol: str, interval: str) -> int:
    merged: dict[int, list[str]] = {}
    for month in MONTHS:
        for row in convert_rows(download_month(symbol, interval, month)):
            merged[int(row[0])] = row

    output = out_dir / f"{symbol}_{interval}.csv"
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "symbol",
                "timeframe",
                "open_time_ms",
                "close_time_ms",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "taker_buy_volume",
                "taker_sell_volume",
                "is_closed",
                "source",
            ]
        )
        for open_time_ms in sorted(merged):
            row = merged[open_time_ms]
            volume = float(row[5])
            taker_buy_volume = float(row[9])
            writer.writerow(
                [
                    symbol,
                    interval,
                    row[0],
                    row[6],
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    row[9],
                    max(volume - taker_buy_volume, 0.0),
                    "true",
                    "binance_data_vision_spot_monthly",
                ]
            )
    return len(merged)


def main() -> int:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ".replay_data")
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[str] = []
    for symbol in SYMBOLS:
        for interval in INTERVALS:
            count = write_symbol_interval(out_dir, symbol, interval)
            manifest.append(f"{symbol},{interval},{count}")
            print(f"{symbol} {interval}: {count} rows")
    (out_dir / "MANIFEST.csv").write_text(
        "symbol,timeframe,rows,source_as_of\n"
        + "\n".join(f"{line},2026-07-31" for line in manifest)
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
