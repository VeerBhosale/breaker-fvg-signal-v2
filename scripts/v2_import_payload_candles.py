from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, List

from v2_common import V2_ROOT, append_jsonl, rel, utc_stamp, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import candle windows from existing engine payload JSON into V2 raw candle files.")
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--run-id", default=None)
    return parser.parse_args()


def audit_candles(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    times = [row.get("time") for row in rows]
    duplicate_count = len(times) - len(set(times))
    monotonic = all(times[i] < times[i + 1] for i in range(len(times) - 1))
    null_ohlc = 0
    for row in rows:
        for key in ["open", "high", "low", "close"]:
            if row.get(key) is None:
                null_ohlc += 1
    return {
        "row_count": len(rows),
        "first_time": min(times) if times else None,
        "last_time": max(times) if times else None,
        "duplicate_time_count": duplicate_count,
        "monotonic": monotonic,
        "null_ohlc_count": null_ohlc,
        "passed": bool(rows and duplicate_count == 0 and monotonic and null_ohlc == 0),
    }


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"v2_payload_import_{utc_stamp()}"
    log_path = V2_ROOT / "logs" / f"{run_id}.jsonl"
    append_jsonl(log_path, {"ts": utc_stamp(), "event": "start", "payload": str(args.payload)})

    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    ticker = payload.get("ticker") or "UNKNOWN"
    decision_time = payload.get("decision_time")
    out_dir = V2_ROOT / "data" / "raw" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    outputs = []
    for item in payload.get("tickers", []):
        item_ticker = item.get("ticker") or ticker
        candles = item.get("candles") or []
        out_csv = out_dir / f"{item_ticker}_1h_{decision_time}.csv"
        with out_csv.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close"])
            writer.writeheader()
            for candle in candles:
                writer.writerow({key: candle.get(key) for key in ["time", "open", "high", "low", "close"]})
        item_audit = audit_candles(candles)
        item_audit.update({"ticker": item_ticker, "output": rel(out_csv)})
        outputs.append(item_audit)
        append_jsonl(log_path, {"ts": utc_stamp(), "event": "ticker_imported", **item_audit})

    audit = {
        "version": "SIGNAL_MODEL_V2_PAYLOAD_CANDLE_IMPORT_AUDIT",
        "run_id": run_id,
        "generated_at": utc_stamp(),
        "source_payload": str(args.payload),
        "payload_ticker": ticker,
        "decision_time": decision_time,
        "outputs": outputs,
        "passed": bool(outputs and all(item["passed"] for item in outputs)),
        "log": rel(log_path),
    }
    audit_path = V2_ROOT / "audits" / f"{run_id}_audit.json"
    write_json(audit_path, audit)
    print(f"Wrote {audit_path}")
    return 0 if audit["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

