from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

from v2_common import V2_ROOT, append_jsonl, read_csv, rel, utc_stamp, write_csv, write_json
from v2_ingest_candles import (
    INTERVAL_SECONDS,
    audit_normalized,
    load_manifest,
    load_tickers,
    normalize_frame,
    read_local_ticker,
    read_yfinance_ticker,
)


DEFAULT_STORE = V2_ROOT / "data" / "raw" / "v2_incremental_candle_store"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Maintain an append/merge V2 candle store. Existing ticker files are updated by fetching only "
            "new candles since the last stored timestamp, with a small overlap window for provider revisions."
        )
    )
    parser.add_argument("--provider", choices=["local_csv", "yfinance"], required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--ticker", action="append", default=[], help="Ticker to update. Can be repeated.")
    parser.add_argument("--universe-file", type=Path, default=None)
    parser.add_argument("--source-dir", type=Path, default=None, help="Source dir for local_csv provider.")
    parser.add_argument("--source-manifest", type=Path, default=None)
    parser.add_argument("--store-dir", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--initial-period", default="730d", help="Initial yfinance period when no store exists.")
    parser.add_argument("--input-timezone", default="Asia/Calcutta")
    parser.add_argument("--min-rows", type=int, default=50)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--retry-sleep-seconds", type=float, default=5.0)
    parser.add_argument("--overlap-bars", type=int, default=5)
    parser.add_argument(
        "--max-store-candles",
        type=int,
        default=6000,
        help="Keep only latest N candles per ticker after merge. Use 0 to keep all stored candles.",
    )
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--audit", type=Path, default=None)
    return parser.parse_args()


def read_existing(path: Path) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    if not path.exists():
        empty = pd.DataFrame(columns=["time", "open", "high", "low", "close"])
        return empty, {
            "exists": False,
            "row_count": 0,
            "first_time": None,
            "last_time": None,
        }
    raw = pd.read_csv(path)
    frame, normalization = normalize_frame(raw, "UTC")
    return frame, {
        "exists": True,
        "row_count": int(len(frame)),
        "first_time": int(frame["time"].min()) if not frame.empty else None,
        "last_time": int(frame["time"].max()) if not frame.empty else None,
        "normalization": normalization,
    }


def fetch_yfinance_increment(ticker: str, args: argparse.Namespace, existing: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    if existing.empty:
        fetch_args = argparse.Namespace(**vars(args))
        fetch_args.period = args.initial_period
        fetch_args.start = None
        fetch_args.end = None
        frame, normalization = read_yfinance_ticker(ticker, fetch_args)
        normalization["fetch_mode"] = "initial_period"
        normalization["fetch_start_utc"] = None
        normalization["fetch_end_utc"] = None
        return frame, normalization

    interval_seconds = INTERVAL_SECONDS.get(args.interval.lower(), 3600)
    overlap_seconds = max(int(args.overlap_bars), 0) * interval_seconds
    last_time = int(existing["time"].max())
    fetch_start_ts = max(0, last_time - overlap_seconds)
    fetch_start = datetime.fromtimestamp(fetch_start_ts, tz=timezone.utc)
    fetch_end = datetime.now(timezone.utc) + timedelta(days=1)

    fetch_args = argparse.Namespace(**vars(args))
    fetch_args.period = args.initial_period
    fetch_args.start = fetch_start.isoformat()
    fetch_args.end = fetch_end.isoformat()
    frame, normalization = read_yfinance_ticker(ticker, fetch_args)
    normalization["fetch_mode"] = "incremental_start_end"
    normalization["fetch_start_utc"] = fetch_start.isoformat()
    normalization["fetch_end_utc"] = fetch_end.isoformat()
    normalization["last_existing_time"] = last_time
    normalization["overlap_bars"] = int(args.overlap_bars)
    return frame, normalization


def fetch_local_increment(ticker: str, args: argparse.Namespace, manifest: Dict[str, Path]) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    frame, normalization = read_local_ticker(ticker, args, manifest)
    normalization["fetch_mode"] = "local_source_merge"
    return frame, normalization


def merge_frames(existing: pd.DataFrame, incoming: pd.DataFrame, max_store_candles: int) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    existing_times = set(existing["time"].astype("int64").tolist()) if not existing.empty else set()
    incoming_times = set(incoming["time"].astype("int64").tolist()) if not incoming.empty else set()
    combined = pd.concat([existing, incoming], ignore_index=True) if not existing.empty else incoming.copy()
    before_dedup = len(combined)
    combined = combined.drop_duplicates(subset=["time"], keep="last").sort_values("time").reset_index(drop=True)
    before_trim = len(combined)
    trimmed_rows = 0
    if max_store_candles and max_store_candles > 0 and len(combined) > max_store_candles:
        trimmed_rows = int(len(combined) - max_store_candles)
        combined = combined.tail(max_store_candles).reset_index(drop=True)
    final_times = set(combined["time"].astype("int64").tolist()) if not combined.empty else set()
    truly_new_times = incoming_times.difference(existing_times)
    retained_new_times = truly_new_times.intersection(final_times)
    return combined[["time", "open", "high", "low", "close"]], {
        "existing_rows": int(len(existing)),
        "incoming_rows": int(len(incoming)),
        "combined_rows_before_dedup": int(before_dedup),
        "duplicate_time_rows_removed": int(before_dedup - before_trim),
        "merged_rows_before_trim": int(before_trim),
        "trimmed_rows": trimmed_rows,
        "new_timestamps_seen": int(len(truly_new_times)),
        "new_timestamps_retained": int(len(retained_new_times)),
        "output_rows": int(len(combined)),
        "first_time": int(combined["time"].min()) if not combined.empty else None,
        "last_time": int(combined["time"].max()) if not combined.empty else None,
    }


def update_ticker(ticker: str, args: argparse.Namespace, manifest: Dict[str, Path]) -> Dict[str, Any]:
    out_path = args.store_dir / f"{ticker}_{args.interval}.csv"
    existing, existing_audit = read_existing(out_path)
    if args.provider == "yfinance":
        incoming, fetch_audit = fetch_yfinance_increment(ticker, args, existing)
    else:
        incoming, fetch_audit = fetch_local_increment(ticker, args, manifest)

    merged, merge_audit = merge_frames(existing, incoming, args.max_store_candles)
    quality = audit_normalized(merged, args.min_rows, args.interval)
    write_csv(out_path, merged.to_dict(orient="records"), fieldnames=["time", "open", "high", "low", "close"])

    previous_last = existing_audit.get("last_time")
    current_last = merge_audit.get("last_time")
    advanced_by_seconds = (
        int(current_last) - int(previous_last)
        if previous_last is not None and current_last is not None
        else None
    )
    return {
        "ticker": ticker,
        "provider": args.provider,
        "output": rel(out_path),
        "existing": existing_audit,
        "fetch": fetch_audit,
        "merge": merge_audit,
        "quality": quality,
        "advanced_by_seconds": advanced_by_seconds,
        "passed": bool(quality["passed"]),
    }


def progress_payload(start: datetime, index: int, total: int) -> Dict[str, Any]:
    elapsed = max((datetime.now(timezone.utc) - start).total_seconds(), 0.0)
    completed = max(index, 1)
    remaining = max(total - index, 0)
    avg = elapsed / completed
    return {
        "elapsed_seconds": round(elapsed, 3),
        "avg_seconds_per_ticker": round(avg, 3),
        "remaining_tickers": remaining,
        "eta_seconds": round(avg * remaining, 3),
    }


def write_report(path: Path, audit: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# V2 Incremental Candle Store Update",
        "",
        f"Run ID: `{audit['run_id']}`",
        f"Generated: `{audit['generated_at']}`",
        f"Provider: `{audit['provider']}`",
        f"Store dir: `{audit['store_dir']}`",
        "",
        "## Summary",
        "",
        f"- Tickers: `{audit['ticker_count']}`",
        f"- Passed: `{audit['passed_count']}`",
        f"- Failed: `{audit['failed_count']}`",
        f"- Initial fetches: `{audit['initial_fetch_count']}`",
        f"- Incremental fetches: `{audit['incremental_fetch_count']}`",
        f"- New candles retained: `{audit['new_timestamps_retained']}`",
        f"- Max store candles: `{audit['max_store_candles']}`",
        f"- Passed: `{str(audit['passed']).lower()}`",
        "",
        "## Contract",
        "",
        "- Existing and fetched candles are normalized to `time,open,high,low,close`.",
        "- Existing rows are merged with provider rows and deduped by `time`, keeping the fetched row on overlap.",
        "- Incremental yfinance mode fetches from `last_stored_time - overlap_bars` instead of refetching the full period.",
        "- The output store is safe to pass directly as `--candles-dir` into V2 replay/runtime scripts.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"v2_incremental_candle_store_{utc_stamp()}"
    args.store_dir.mkdir(parents=True, exist_ok=True)
    audit_path = args.audit or (V2_ROOT / "audits" / f"{run_id}_audit.json")
    report_path = V2_ROOT / "reports" / f"{run_id}_report.md"
    log_path = V2_ROOT / "logs" / f"{run_id}.jsonl"

    tickers = load_tickers(args)
    manifest = load_manifest(args.source_manifest)
    started = datetime.now(timezone.utc)
    append_jsonl(
        log_path,
        {
            "ts": utc_stamp(),
            "event": "start",
            "run_id": run_id,
            "provider": args.provider,
            "ticker_count": len(tickers),
            "store_dir": rel(args.store_dir),
            "overlap_bars": args.overlap_bars,
            "max_store_candles": args.max_store_candles,
        },
    )

    outputs: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for index, ticker in enumerate(tickers, start=1):
        append_jsonl(log_path, {"ts": utc_stamp(), "event": "ticker_start", "ticker": ticker, "index": index, "total": len(tickers)})
        try:
            result = update_ticker(ticker, args, manifest)
            outputs.append(result)
            if not result["passed"]:
                failures.append({"ticker": ticker, "error": "quality_audit_failed", "quality": result["quality"]})
            append_jsonl(
                log_path,
                {
                    "ts": utc_stamp(),
                    "event": "ticker_finish",
                    "ticker": ticker,
                    "passed": result["passed"],
                    "output_rows": result["merge"]["output_rows"],
                    "new_timestamps_retained": result["merge"]["new_timestamps_retained"],
                    "advanced_by_seconds": result["advanced_by_seconds"],
                    **progress_payload(started, index, len(tickers)),
                },
            )
        except Exception as exc:
            failure = {"ticker": ticker, "error": str(exc)}
            failures.append(failure)
            append_jsonl(log_path, {"ts": utc_stamp(), "event": "ticker_failed", **failure, **progress_payload(started, index, len(tickers))})

    passed_count = sum(1 for item in outputs if item["passed"])
    initial_fetch_count = sum(1 for item in outputs if item.get("fetch", {}).get("fetch_mode") == "initial_period")
    incremental_fetch_count = sum(1 for item in outputs if item.get("fetch", {}).get("fetch_mode") == "incremental_start_end")
    audit = {
        "version": "SIGNAL_MODEL_V2_INCREMENTAL_CANDLE_STORE_AUDIT",
        "run_id": run_id,
        "generated_at": utc_stamp(),
        "provider": args.provider,
        "interval": args.interval,
        "ticker_count": len(tickers),
        "passed_count": passed_count,
        "failed_count": len(failures),
        "allow_partial": bool(args.allow_partial),
        "store_dir": rel(args.store_dir),
        "log": rel(log_path),
        "report": rel(report_path),
        "initial_period": args.initial_period,
        "overlap_bars": int(args.overlap_bars),
        "max_store_candles": int(args.max_store_candles),
        "initial_fetch_count": initial_fetch_count,
        "incremental_fetch_count": incremental_fetch_count,
        "new_timestamps_retained": sum(int(item.get("merge", {}).get("new_timestamps_retained") or 0) for item in outputs),
        "outputs": outputs,
        "failures": failures,
        "passed": bool(passed_count > 0 and (not failures or args.allow_partial)),
    }
    write_json(audit_path, audit)
    write_report(report_path, audit)
    append_jsonl(log_path, {"ts": utc_stamp(), "event": "finish", "passed": audit["passed"], "audit": rel(audit_path)})
    print(f"Wrote {audit_path}")
    print(f"Wrote {report_path}")
    return 0 if audit["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
