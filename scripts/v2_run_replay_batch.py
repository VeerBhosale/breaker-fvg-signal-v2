from __future__ import annotations

import argparse
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Sequence

from v2_common import V2_ROOT, append_jsonl, python_exe, read_json, rel, run_command, utc_stamp, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the V2 replay smoke pipeline over a directory of V2-owned candle CSVs with strict per-ticker "
            "JSONL progress and aggregate audit/report outputs."
        )
    )
    parser.add_argument("--candles-dir", type=Path, required=True)
    parser.add_argument("--tickers", default=None, help="Optional comma-separated ticker list.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--decision-time-policy",
        choices=["signal_time", "next_candle_after_signal"],
        default="signal_time",
    )
    parser.add_argument("--macro-candles-dir", type=Path, default=None)
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue running remaining tickers after a ticker failure. Aggregate audit still fails if any ticker fails.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse existing per-ticker replay audits for this run-id instead of rerunning completed tickers.",
    )
    parser.add_argument(
        "--max-input-candles",
        type=int,
        default=None,
        help="Optional latest-N candle bound passed into per-ticker signal detection.",
    )
    parser.add_argument(
        "--step-timeout-seconds",
        type=int,
        default=300,
        help="Hard timeout passed into each per-ticker pipeline step.",
    )
    parser.add_argument(
        "--ticker-timeout-seconds",
        type=int,
        default=900,
        help="Hard timeout for each per-ticker replay pipeline subprocess.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "Number of tickers to process concurrently. Values above 1 require --continue-on-error "
            "so every submitted ticker can finish with an auditable row."
        ),
    )
    return parser.parse_args()


def parse_tickers(raw: str | None) -> List[str] | None:
    if not raw:
        return None
    return [item.strip() for item in raw.split(",") if item.strip()]


def safe_run_id_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_").lower() or "ticker"


def ticker_from_candle_file(path: Path) -> str:
    name = path.name
    suffix = "_1h.csv"
    if name.endswith(suffix):
        return name[: -len(suffix)]
    return path.stem


def select_candle_files(candles_dir: Path, tickers: Sequence[str] | None, limit: int | None) -> List[Path]:
    if tickers:
        files = [candles_dir / f"{ticker}_1h.csv" for ticker in tickers]
    else:
        files = sorted(candles_dir.glob("*_1h.csv"), key=lambda path: path.name)
    existing = [path for path in files if path.exists()]
    if limit is not None:
        existing = existing[:limit]
    return existing


def merge_bucket_counts(rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    merged: Dict[str, int] = {}
    for row in rows:
        bucket_counts = row.get("decision_bucket_counts") or {}
        for bucket, count in bucket_counts.items():
            merged[bucket] = merged.get(bucket, 0) + int(count)
    return dict(sorted(merged.items()))


def progress_payload(start_time: float, index: int, total: int) -> Dict[str, Any]:
    elapsed = max(time.time() - start_time, 0.0)
    completed = max(index, 1)
    remaining = max(total - index, 0)
    avg = elapsed / completed
    return {
        "elapsed_seconds": round(elapsed, 3),
        "avg_seconds_per_ticker": round(avg, 3),
        "remaining_tickers": remaining,
        "eta_seconds": round(avg * remaining, 3),
    }


def row_from_ticker_audit(
    ticker: str,
    candles: Path,
    ticker_run_id: str,
    ticker_audit_path: Path,
    ticker_report_path: Path,
    resumed: bool = False,
) -> Dict[str, Any]:
    ticker_audit = read_json(ticker_audit_path)
    ticker_summary = ticker_audit.get("summary", {})
    ticker_error = str(ticker_audit.get("error") or "")
    status = str(ticker_audit.get("status", "unknown"))
    if "Signal detection produced zero rows" in ticker_error:
        status = "no_signals"
    row: Dict[str, Any] = {
        "ticker": ticker,
        "candles": rel(candles),
        "run_id": ticker_run_id,
        "audit": rel(ticker_audit_path),
        "report": rel(ticker_report_path),
        "status": status,
        "resumed": bool(resumed),
        "signal_rows": ticker_summary.get("signal_rows", 0),
        "liquidity_candidate_rows": ticker_summary.get("liquidity_candidate_rows", 0),
        "scored_liquidity_rows": ticker_summary.get("scored_liquidity_rows", 0),
        "aggregated_signal_rows": ticker_summary.get("aggregated_signal_rows", 0),
        "feature_rows": ticker_summary.get("feature_rows", 0),
        "decision_rows": ticker_summary.get("decision_rows", 0),
        "classification_allowed": ticker_summary.get("classification_allowed"),
        "signal_inference_path": ticker_summary.get("signal_inference_path"),
        "decision_bucket_counts": ticker_summary.get("decision_bucket_counts", {}),
    }
    if status == "no_signals":
        row.update(
            {
                "signal_rows": 0,
                "liquidity_candidate_rows": 0,
                "scored_liquidity_rows": 0,
                "aggregated_signal_rows": 0,
                "feature_rows": 0,
                "decision_rows": 0,
                "error": ticker_error,
            }
        )
    elif status != "passed":
        row["error"] = ticker_error
    return row


def replay_command(
    replay_script: Path,
    candles: Path,
    ticker: str,
    ticker_run_id: str,
    decision_time_policy: str,
    macro_candles_dir: Path,
    max_input_candles: int | None,
    step_timeout_seconds: int,
) -> List[str]:
    command = [
        python_exe(),
        str(replay_script),
        "--candles",
        str(candles),
        "--ticker",
        ticker,
        "--run-id",
        ticker_run_id,
        "--decision-time-policy",
        decision_time_policy,
        "--macro-candles-dir",
        str(macro_candles_dir),
    ]
    if max_input_candles is not None:
        command.extend(["--max-input-candles", str(max_input_candles)])
    command.extend(["--step-timeout-seconds", str(step_timeout_seconds)])
    return command


def run_ticker_job(
    *,
    candles: Path,
    run_id: str,
    replay_script: Path,
    decision_time_policy: str,
    macro_candles_dir: Path,
    resume: bool,
    max_input_candles: int | None,
    step_timeout_seconds: int,
    ticker_timeout_seconds: int,
) -> Dict[str, Any]:
    ticker = ticker_from_candle_file(candles)
    ticker_run_id = f"{run_id}_{safe_run_id_token(ticker)}"
    ticker_audit_path = V2_ROOT / "audits" / f"{ticker_run_id}_audit.json"
    ticker_report_path = V2_ROOT / "reports" / f"{ticker_run_id}_report.md"
    launcher_log_path = V2_ROOT / "logs" / f"{ticker_run_id}_launcher.jsonl"
    row: Dict[str, Any] = {
        "ticker": ticker,
        "candles": rel(candles),
        "run_id": ticker_run_id,
        "audit": rel(ticker_audit_path),
        "report": rel(ticker_report_path),
        "launcher_log": rel(launcher_log_path),
        "status": "started",
        "resumed": False,
    }
    if resume and ticker_audit_path.exists():
        resumed_row = row_from_ticker_audit(
            ticker, candles, ticker_run_id, ticker_audit_path, ticker_report_path, resumed=True
        )
        if resumed_row.get("status") in {"passed", "no_signals"}:
            resumed_row["launcher_log"] = rel(launcher_log_path)
            return resumed_row
    try:
        run_command(
            replay_command(
                replay_script,
                candles,
                ticker,
                ticker_run_id,
                decision_time_policy,
                macro_candles_dir,
                max_input_candles,
                step_timeout_seconds,
            ),
            launcher_log_path,
            f"replay_{ticker}",
            ticker_timeout_seconds,
        )
        row = row_from_ticker_audit(ticker, candles, ticker_run_id, ticker_audit_path, ticker_report_path)
        row["launcher_log"] = rel(launcher_log_path)
        return row
    except Exception as exc:
        error = str(exc)
        ticker_audit = read_json(ticker_audit_path) if ticker_audit_path.exists() else {}
        ticker_error = str(ticker_audit.get("error") or error)
        if "Signal detection produced zero rows" in ticker_error:
            row.update(
                {
                    "status": "no_signals",
                    "signal_rows": 0,
                    "liquidity_candidate_rows": 0,
                    "scored_liquidity_rows": 0,
                    "aggregated_signal_rows": 0,
                    "feature_rows": 0,
                    "decision_rows": 0,
                    "error": ticker_error,
                }
            )
        else:
            row.update({"status": "failed", "error": error})
        return row


def write_report(path: Path, audit: Dict[str, Any]) -> None:
    lines: List[str] = [
        "# V2 Replay Batch Report",
        "",
        f"- Run ID: `{audit.get('run_id')}`",
        f"- Candle directory: `{audit.get('candles_dir')}`",
        f"- Decision-time policy: `{audit.get('decision_time_policy')}`",
        f"- Max detector input candles: `{audit.get('max_input_candles')}`",
        f"- Step timeout seconds: `{audit.get('step_timeout_seconds')}`",
        f"- Ticker timeout seconds: `{audit.get('ticker_timeout_seconds')}`",
        f"- Workers: `{audit.get('workers')}`",
        f"- Status: `{audit.get('status')}`",
        f"- Passed tickers: `{audit.get('passed_count')}`",
        f"- No-signal tickers: `{audit.get('no_signal_count')}`",
        f"- Failed tickers: `{audit.get('failed_count')}`",
        f"- Resumed tickers: `{audit.get('resumed_count', 0)}`",
        "",
        "## Aggregate Summary",
        "",
    ]
    summary = audit.get("summary", {})
    for key in [
        "signal_rows",
        "liquidity_candidate_rows",
        "scored_liquidity_rows",
        "aggregated_signal_rows",
        "feature_rows",
        "decision_rows",
    ]:
        lines.append(f"- {key}: `{summary.get(key, 0)}`")
    lines.extend(["", "## Decision Buckets", ""])
    bucket_counts = summary.get("decision_bucket_counts") or {}
    if bucket_counts:
        for bucket, count in bucket_counts.items():
            lines.append(f"- `{bucket}`: `{count}`")
    else:
        lines.append("- No decision buckets available.")
    lines.extend(["", "## Per Ticker", ""])
    lines.append("| Ticker | Status | Signals | Decisions | Path | Buckets | Error |")
    lines.append("|---|---:|---:|---:|---|---|---|")
    for row in audit.get("tickers", []):
        buckets = ", ".join(f"{key}:{value}" for key, value in (row.get("decision_bucket_counts") or {}).items())
        lines.append(
            "| {ticker} | `{status}` | {signals} | {decisions} | `{path}` | {buckets} | {error} |".format(
                ticker=row.get("ticker"),
                status=row.get("status"),
                signals=row.get("signal_rows", 0),
                decisions=row.get("decision_rows", 0),
                path=row.get("signal_inference_path") or "",
                buckets=buckets or "",
                error=(row.get("error") or "").replace("|", "/"),
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This batch report is an orchestration smoke layer over the existing V2 replay pipeline.",
            "Each ticker has its own replay audit, feature audit, inference audit, and JSONL step log.",
            "The aggregate run is not production-ready proof by itself; it is evidence that V2 can run more than one ticker with auditable progress.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"v2_replay_batch_{utc_stamp()}"
    log_path = V2_ROOT / "logs" / f"{run_id}.jsonl"
    audit_path = V2_ROOT / "audits" / f"{run_id}_audit.json"
    report_path = V2_ROOT / "reports" / f"{run_id}_report.md"
    macro_candles_dir = args.macro_candles_dir or args.candles_dir
    requested_tickers = parse_tickers(args.tickers)
    candle_files = select_candle_files(args.candles_dir, requested_tickers, args.limit)
    start_time = time.time()
    workers = max(int(args.workers or 1), 1)
    if workers > 1 and not args.continue_on_error:
        raise ValueError("--workers > 1 requires --continue-on-error for deterministic aggregate audit output.")

    append_jsonl(
        log_path,
        {
            "ts": utc_stamp(),
            "event": "start",
            "run_id": run_id,
            "candles_dir": str(args.candles_dir),
            "requested_tickers": requested_tickers,
            "limit": args.limit,
            "selected_candle_count": len(candle_files),
            "resume": bool(args.resume),
            "max_input_candles": args.max_input_candles,
            "step_timeout_seconds": args.step_timeout_seconds,
            "ticker_timeout_seconds": args.ticker_timeout_seconds,
            "workers": workers,
        },
    )

    replay_script = V2_ROOT / "scripts" / "v2_run_replay_smoke_pipeline.py"
    ticker_rows: List[Dict[str, Any]] = []
    if workers == 1:
        for index, candles in enumerate(candle_files, start=1):
            ticker = ticker_from_candle_file(candles)
            ticker_run_id = f"{run_id}_{safe_run_id_token(ticker)}"
            append_jsonl(
                log_path,
                {
                    "ts": utc_stamp(),
                    "event": "ticker_start",
                    "index": index,
                    "total": len(candle_files),
                    "ticker": ticker,
                    "candles": rel(candles),
                    "ticker_run_id": ticker_run_id,
                },
            )
            row = run_ticker_job(
                candles=candles,
                run_id=run_id,
                replay_script=replay_script,
                decision_time_policy=args.decision_time_policy,
                macro_candles_dir=macro_candles_dir,
                resume=args.resume,
                max_input_candles=args.max_input_candles,
                step_timeout_seconds=args.step_timeout_seconds,
                ticker_timeout_seconds=args.ticker_timeout_seconds,
            )
            ticker_rows.append(row)
            event = {
                "passed": "ticker_finish",
                "no_signals": "ticker_no_signals",
                "failed": "ticker_failed",
            }.get(str(row.get("status")), "ticker_finish")
            append_jsonl(
                log_path,
                {"ts": utc_stamp(), "event": event, **row, **progress_payload(start_time, index, len(candle_files))},
            )
            if row.get("status") == "failed" and not args.continue_on_error:
                break
    else:
        future_to_meta: Dict[Any, Dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for index, candles in enumerate(candle_files, start=1):
                ticker = ticker_from_candle_file(candles)
                ticker_run_id = f"{run_id}_{safe_run_id_token(ticker)}"
                append_jsonl(
                    log_path,
                    {
                        "ts": utc_stamp(),
                        "event": "ticker_submitted",
                        "index": index,
                        "total": len(candle_files),
                        "ticker": ticker,
                        "candles": rel(candles),
                        "ticker_run_id": ticker_run_id,
                    },
                )
                future = executor.submit(
                    run_ticker_job,
                    candles=candles,
                    run_id=run_id,
                    replay_script=replay_script,
                    decision_time_policy=args.decision_time_policy,
                    macro_candles_dir=macro_candles_dir,
                    resume=args.resume,
                    max_input_candles=args.max_input_candles,
                    step_timeout_seconds=args.step_timeout_seconds,
                    ticker_timeout_seconds=args.ticker_timeout_seconds,
                )
                future_to_meta[future] = {"index": index, "ticker": ticker}
            completed = 0
            indexed_rows: List[tuple[int, Dict[str, Any]]] = []
            for future in as_completed(future_to_meta):
                meta = future_to_meta[future]
                completed += 1
                try:
                    row = future.result()
                except Exception as exc:
                    row = {
                        "ticker": meta["ticker"],
                        "status": "failed",
                        "error": str(exc),
                        "resumed": False,
                    }
                indexed_rows.append((int(meta["index"]), row))
                event = {
                    "passed": "ticker_finish",
                    "no_signals": "ticker_no_signals",
                    "failed": "ticker_failed",
                }.get(str(row.get("status")), "ticker_finish")
                append_jsonl(
                    log_path,
                    {
                        "ts": utc_stamp(),
                        "event": event,
                        "completed": completed,
                        "total": len(candle_files),
                        **row,
                        **progress_payload(start_time, completed, len(candle_files)),
                    },
                )
        ticker_rows = [row for _, row in sorted(indexed_rows, key=lambda item: item[0])]

    passed_count = sum(1 for row in ticker_rows if row.get("status") == "passed")
    no_signal_count = sum(1 for row in ticker_rows if row.get("status") == "no_signals")
    failed_count = sum(1 for row in ticker_rows if row.get("status") == "failed")
    resumed_count = sum(1 for row in ticker_rows if row.get("resumed"))
    summary = {
        "signal_rows": sum(int(row.get("signal_rows", 0) or 0) for row in ticker_rows),
        "liquidity_candidate_rows": sum(int(row.get("liquidity_candidate_rows", 0) or 0) for row in ticker_rows),
        "scored_liquidity_rows": sum(int(row.get("scored_liquidity_rows", 0) or 0) for row in ticker_rows),
        "aggregated_signal_rows": sum(int(row.get("aggregated_signal_rows", 0) or 0) for row in ticker_rows),
        "feature_rows": sum(int(row.get("feature_rows", 0) or 0) for row in ticker_rows),
        "decision_rows": sum(int(row.get("decision_rows", 0) or 0) for row in ticker_rows),
        "decision_bucket_counts": merge_bucket_counts(ticker_rows),
    }
    audit = {
        "version": "SIGNAL_MODEL_V2_REPLAY_BATCH_AUDIT",
        "run_id": run_id,
        "generated_at": utc_stamp(),
        "candles_dir": rel(args.candles_dir),
        "macro_candles_dir": rel(macro_candles_dir),
        "requested_tickers": requested_tickers,
        "limit": args.limit,
        "decision_time_policy": args.decision_time_policy,
        "max_input_candles": args.max_input_candles,
        "step_timeout_seconds": args.step_timeout_seconds,
        "ticker_timeout_seconds": args.ticker_timeout_seconds,
        "workers": workers,
        "selected_candle_count": len(candle_files),
        "attempted_count": len(ticker_rows),
        "resumed_count": resumed_count,
        "passed_count": passed_count,
        "no_signal_count": no_signal_count,
        "failed_count": failed_count,
        "status": (
            "passed"
            if candle_files and failed_count == 0 and (passed_count + no_signal_count) == len(candle_files)
            else "failed"
        ),
        "production_ready": False,
        "summary": summary,
        "tickers": ticker_rows,
        "log": rel(log_path),
        "report": rel(report_path),
    }
    write_json(audit_path, audit)
    write_report(report_path, audit)
    append_jsonl(log_path, {"ts": utc_stamp(), "event": "finish", "status": audit["status"], "audit": rel(audit_path)})
    print(f"Wrote {report_path}")
    print(f"Wrote {audit_path}")
    return 0 if audit["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
