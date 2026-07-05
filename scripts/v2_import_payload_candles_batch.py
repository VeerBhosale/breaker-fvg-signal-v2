from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from v2_common import V1_ROOT, V2_ROOT, append_jsonl, rel, utc_stamp, write_json


DEFAULT_PAYLOAD_ROOT = V1_ROOT / "datasets" / "raw" / "signal_window_liquidity_1h_2y_payloads"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Import a batch of existing signal/liquidity payload candle windows into a single V2-owned raw "
            "candle directory. This is for offline replay smoke testing only; it does not edit V1 payloads."
        )
    )
    parser.add_argument("--payload-root", type=Path, default=DEFAULT_PAYLOAD_ROOT)
    parser.add_argument("--tickers", default=None, help="Optional comma-separated ticker list.")
    parser.add_argument("--limit", type=int, default=None, help="Optional max tickers to import after filtering.")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--min-rows", type=int, default=50)
    return parser.parse_args()


def parse_tickers(raw: str | None) -> List[str] | None:
    if not raw:
        return None
    return [item.strip() for item in raw.split(",") if item.strip()]


def safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def select_payloads(payload_root: Path, tickers: Sequence[str] | None, limit: int | None) -> List[Path]:
    if tickers:
        ticker_dirs = [payload_root / ticker for ticker in tickers]
    else:
        ticker_dirs = sorted([path for path in payload_root.iterdir() if path.is_dir()], key=lambda path: path.name)

    selected: List[Path] = []
    for ticker_dir in ticker_dirs:
        if not ticker_dir.exists() or not ticker_dir.is_dir():
            continue
        files = sorted(ticker_dir.glob("*.json"), key=lambda path: path.name)
        if not files:
            continue
        selected.append(files[0])
        if limit is not None and len(selected) >= limit:
            break
    return selected


def normalize_candles(candles: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for candle in candles:
        row = {
            "time": safe_int(candle.get("time")),
            "open": safe_float(candle.get("open")),
            "high": safe_float(candle.get("high")),
            "low": safe_float(candle.get("low")),
            "close": safe_float(candle.get("close")),
        }
        rows.append(row)
    rows.sort(key=lambda row: (row["time"] is None, row["time"] or 0))
    return rows


def audit_candles(rows: List[Dict[str, Any]], min_rows: int) -> Dict[str, Any]:
    times = [row.get("time") for row in rows if row.get("time") is not None]
    duplicate_count = len(times) - len(set(times))
    monotonic = all(times[i] < times[i + 1] for i in range(len(times) - 1))
    null_time_count = sum(1 for row in rows if row.get("time") is None)
    null_ohlc_count = 0
    invalid_ohlc_count = 0
    for row in rows:
        values = [row.get(key) for key in ["open", "high", "low", "close"]]
        null_ohlc_count += sum(1 for value in values if value is None)
        high = row.get("high")
        low = row.get("low")
        if high is not None and low is not None and high < low:
            invalid_ohlc_count += 1
    passed = (
        len(rows) >= min_rows
        and len(times) == len(rows)
        and duplicate_count == 0
        and monotonic
        and null_time_count == 0
        and null_ohlc_count == 0
        and invalid_ohlc_count == 0
    )
    return {
        "row_count": len(rows),
        "first_time": min(times) if times else None,
        "last_time": max(times) if times else None,
        "duplicate_time_count": duplicate_count,
        "monotonic": monotonic,
        "null_time_count": null_time_count,
        "null_ohlc_count": null_ohlc_count,
        "invalid_ohlc_count": invalid_ohlc_count,
        "min_rows": min_rows,
        "passed": passed,
    }


def write_candle_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"v2_payload_batch_import_{utc_stamp()}"
    output_dir = V2_ROOT / "data" / "raw" / run_id
    log_path = V2_ROOT / "logs" / f"{run_id}.jsonl"
    audit_path = V2_ROOT / "audits" / f"{run_id}_audit.json"

    requested_tickers = parse_tickers(args.tickers)
    append_jsonl(
        log_path,
        {
            "ts": utc_stamp(),
            "event": "start",
            "payload_root": str(args.payload_root),
            "requested_tickers": requested_tickers,
            "limit": args.limit,
            "run_id": run_id,
        },
    )

    selected_payloads = select_payloads(args.payload_root, requested_tickers, args.limit)
    outputs: List[Dict[str, Any]] = []
    for payload_path in selected_payloads:
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            payload_ticker = payload.get("ticker") or payload_path.parent.name
            decision_time = payload.get("decision_time")
            items = payload.get("tickers") or []
            if not items:
                raise ValueError("payload has no tickers collection")
            item = items[0]
            ticker = item.get("ticker") or payload_ticker
            rows = normalize_candles(item.get("candles") or [])
            item_audit = audit_candles(rows, args.min_rows)
            out_csv = output_dir / f"{ticker}_1h.csv"
            write_candle_csv(out_csv, rows)
            item_payload = {
                "ticker": ticker,
                "payload_ticker": payload_ticker,
                "decision_time": decision_time,
                "source_payload": rel(payload_path),
                "output": rel(out_csv),
                **item_audit,
            }
        except Exception as exc:
            item_payload = {
                "ticker": payload_path.parent.name,
                "source_payload": rel(payload_path),
                "output": None,
                "passed": False,
                "error": str(exc),
            }
        outputs.append(item_payload)
        append_jsonl(log_path, {"ts": utc_stamp(), "event": "ticker_imported", **item_payload})

    passed_count = sum(1 for item in outputs if item.get("passed"))
    audit = {
        "version": "SIGNAL_MODEL_V2_PAYLOAD_BATCH_CANDLE_IMPORT_AUDIT",
        "run_id": run_id,
        "generated_at": utc_stamp(),
        "payload_root": str(args.payload_root),
        "requested_tickers": requested_tickers,
        "limit": args.limit,
        "output_dir": rel(output_dir),
        "selected_payload_count": len(selected_payloads),
        "imported_ticker_count": len(outputs),
        "passed_count": passed_count,
        "failed_count": len(outputs) - passed_count,
        "passed": bool(outputs and passed_count == len(outputs)),
        "outputs": outputs,
        "log": rel(log_path),
    }
    write_json(audit_path, audit)
    append_jsonl(log_path, {"ts": utc_stamp(), "event": "finish", "passed": audit["passed"], "audit": rel(audit_path)})
    print(f"Wrote {audit_path}")
    print(f"Wrote {output_dir}")
    return 0 if audit["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
