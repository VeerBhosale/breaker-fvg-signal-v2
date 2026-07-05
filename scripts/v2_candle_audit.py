from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

from v2_common import read_csv, utc_stamp, write_json


TIME_COLUMNS = ["time", "timestamp", "datetime", "date"]
REQUIRED_OHLC = ["open", "high", "low", "close"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit a candle CSV for V2 ingestion readiness.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = read_csv(args.input)
    columns = set(rows[0].keys()) if rows else set()
    time_col = next((c for c in TIME_COLUMNS if c in columns), None)
    missing = [c for c in REQUIRED_OHLC if c not in columns]
    duplicate_times = 0
    monotonic = True
    null_ohlc = 0
    if time_col:
        seen = set()
        prev = None
        for row in rows:
            t = row.get(time_col, "")
            if t in seen:
                duplicate_times += 1
            seen.add(t)
            if prev is not None and str(t) < str(prev):
                monotonic = False
            prev = t
            if any(row.get(c, "") in ("", "nan", "None", "null") for c in REQUIRED_OHLC if c in row):
                null_ohlc += 1

    audit: Dict[str, Any] = {
        "version": "SIGNAL_MODEL_V2_CANDLE_AUDIT",
        "generated_at": utc_stamp(),
        "input": str(args.input),
        "row_count": len(rows),
        "columns": sorted(columns),
        "time_column": time_col,
        "missing_ohlc_columns": missing,
        "duplicate_time_count": duplicate_times,
        "time_monotonic_string_check": monotonic,
        "null_ohlc_count": null_ohlc,
        "passed": bool(rows and time_col and not missing and duplicate_times == 0 and monotonic and null_ohlc == 0),
    }
    write_json(args.output, audit)
    print(f"Wrote {args.output}")
    return 0 if audit["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

