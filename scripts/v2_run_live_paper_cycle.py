from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

from v2_common import REPO_ROOT, V2_ROOT, append_jsonl, python_exe, read_csv, read_json, rel, run_command, utc_stamp, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a live-disabled/paper V2 cycle: ingest candles, replay signals/liquidity/model decisions, "
            "export dashboard bridge, and write paper ledger outputs."
        )
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--runtime-config", type=Path, default=V2_ROOT / "configs" / "v2_runtime_config.example.json")
    parser.add_argument("--provider", choices=["local_csv", "yfinance"], default=None)
    parser.add_argument("--ticker", action="append", default=[])
    parser.add_argument("--universe", choices=["original", "expanded_research"], default=None)
    parser.add_argument("--universe-file", type=Path, default=None)
    parser.add_argument("--source-dir", type=Path, default=None)
    parser.add_argument("--source-manifest", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--period", default=None)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--decision-time-policy", choices=["signal_time", "next_candle_after_signal"], default="signal_time")
    parser.add_argument("--entry-policy", choices=["next_touch", "next_open"], default="next_touch")
    parser.add_argument("--max-hold-bars", type=int, default=120)
    parser.add_argument("--notional-capital-inr", type=float, default=None)
    parser.add_argument("--skip-dashboard", action="store_true")
    parser.add_argument("--skip-paper", action="store_true")
    return parser.parse_args()


def repo_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def read_universe_file(path: Path) -> List[str]:
    rows = read_csv(path)
    if not rows:
        return []
    columns = list(rows[0].keys())
    preferred = next((col for col in ["ticker", "symbol", "Symbol", "SYMBOL", "nse_symbol"] if col in columns), columns[0])
    tickers: List[str] = []
    seen: set[str] = set()
    for row in rows:
        ticker = str(row.get(preferred, "")).strip()
        if ticker and ticker not in seen:
            seen.add(ticker)
            tickers.append(ticker)
    return tickers


def resolve_tickers(args: argparse.Namespace, config: Dict[str, Any]) -> List[str]:
    tickers: List[str] = []
    for ticker in args.ticker:
        ticker = str(ticker).strip()
        if ticker:
            tickers.append(ticker)

    universe_files: List[Path] = []
    if args.universe_file:
        universe_files.append(args.universe_file)
    elif args.universe:
        universe_node = config.get("universes", {}).get(args.universe)
        if isinstance(universe_node, str):
            path = repo_path(universe_node)
            if path is not None:
                universe_files.append(path)
        elif isinstance(universe_node, list):
            for item in universe_node:
                path = repo_path(str(item))
                if path is not None:
                    universe_files.append(path)

    for path in universe_files:
        tickers.extend(read_universe_file(path))

    deduped: List[str] = []
    seen: set[str] = set()
    for ticker in tickers:
        if ticker not in seen:
            seen.add(ticker)
            deduped.append(ticker)
    if args.limit is not None:
        deduped = deduped[: args.limit]
    if not deduped:
        raise ValueError("No tickers resolved. Use --ticker, --universe, or --universe-file.")
    return deduped


def write_report(path: Path, audit: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = audit["summary"]
    lines = [
        "# V2 Live-Disabled Paper Cycle Report",
        "",
        f"Run ID: `{audit['run_id']}`",
        f"Generated: `{audit['generated_at']}`",
        "",
        "## Summary",
        "",
        f"- Status: `{audit['status']}`",
        f"- Provider: `{audit['provider']}`",
        f"- Tickers requested: `{summary['ticker_count']}`",
        f"- Ingest passed: `{summary['ingest_passed']}`",
        f"- Runtime cycle status: `{summary['runtime_cycle_status']}`",
        f"- Production ready: `{str(summary['production_ready']).lower()}`",
        "",
        "## Phase Outputs",
        "",
    ]
    for phase in audit.get("phases", []):
        lines.append(f"- `{phase['name']}`: `{phase['status']}`")
        for key, value in phase.get("outputs", {}).items():
            lines.append(f"  - {key}: `{value}`")
        if phase.get("error"):
            lines.append(f"  - error: `{phase['error']}`")
    lines.extend(
        [
            "",
            "## Safety Notes",
            "",
            "- This command is live-disabled and paper/manual-review oriented.",
            "- It does not place orders.",
            "- Network fetching through yfinance requires explicit network-capable runtime.",
            "- Real-money deployment remains blocked until separate live-order gates are built and approved.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def phase(name: str, status: str, outputs: Dict[str, str] | None = None, error: str | None = None) -> Dict[str, Any]:
    return {"name": name, "status": status, "outputs": outputs or {}, "error": error or ""}


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"v2_live_paper_cycle_{utc_stamp()}"
    log_path = V2_ROOT / "logs" / f"{run_id}.jsonl"
    audit_path = V2_ROOT / "audits" / f"{run_id}_audit.json"
    report_path = V2_ROOT / "reports" / f"{run_id}_report.md"
    config = read_json(args.runtime_config)
    ingestion_config = config.get("data_ingestion", {})
    paper_config = config.get("modes", {}).get("paper", {})
    provider = args.provider or str(ingestion_config.get("default_provider") or "local_csv")
    period = args.period or str(ingestion_config.get("yfinance", {}).get("period") or "730d")
    source_dir = args.source_dir or repo_path(ingestion_config.get("local_csv", {}).get("source_dir"))
    source_manifest = args.source_manifest or repo_path(ingestion_config.get("local_csv", {}).get("source_manifest"))
    notional = args.notional_capital_inr
    if notional is None:
        notional = float(paper_config.get("notional_capital_inr") or 1_000_000.0)
    tickers = resolve_tickers(args, config)
    phases: List[Dict[str, Any]] = []

    append_jsonl(
        log_path,
        {
            "ts": utc_stamp(),
            "event": "start",
            "run_id": run_id,
            "provider": provider,
            "ticker_count": len(tickers),
            "tickers": tickers,
        },
    )

    ingest_run_id = f"{run_id}_ingest"
    ingest_audit_path = V2_ROOT / "audits" / f"{ingest_run_id}_audit.json"
    ingest_output_dir = V2_ROOT / "data" / "raw" / ingest_run_id
    runtime_run_id = f"{run_id}_runtime"
    runtime_audit_path = V2_ROOT / "audits" / f"{runtime_run_id}_audit.json"

    try:
        ingest_command = [
            python_exe(),
            str(V2_ROOT / "scripts" / "v2_ingest_candles.py"),
            "--provider",
            provider,
            "--run-id",
            ingest_run_id,
            "--output-dir",
            str(ingest_output_dir),
            "--audit",
            str(ingest_audit_path),
            "--interval",
            str(ingestion_config.get("interval") or "1h"),
            "--min-rows",
            str(ingestion_config.get("min_rows") or 50),
            "--input-timezone",
            str(ingestion_config.get("input_timezone") or config.get("timezone") or "Asia/Calcutta"),
        ]
        for ticker in tickers:
            ingest_command.extend(["--ticker", ticker])
        if provider == "local_csv":
            if source_dir is not None:
                ingest_command.extend(["--source-dir", str(source_dir)])
            if source_manifest is not None:
                ingest_command.extend(["--source-manifest", str(source_manifest)])
        else:
            ingest_command.extend(
                [
                    "--period",
                    period,
                    "--max-retries",
                    str(ingestion_config.get("max_retries") or 3),
                    "--retry-sleep-seconds",
                    str(ingestion_config.get("retry_sleep_seconds") or 5),
                ]
            )
            if args.start:
                ingest_command.extend(["--start", args.start])
            if args.end:
                ingest_command.extend(["--end", args.end])
        if args.allow_partial or bool(ingestion_config.get("allow_partial")):
            ingest_command.append("--allow-partial")

        run_command(ingest_command, log_path, "candle_ingestion")
        ingest_audit = read_json(ingest_audit_path)
        phases.append(
            phase(
                "candle_ingestion",
                "passed" if ingest_audit.get("passed") else "failed",
                {"audit": rel(ingest_audit_path), "output_dir": rel(ingest_output_dir)},
            )
        )

        runtime_command = [
            python_exe(),
            str(V2_ROOT / "scripts" / "v2_run_runtime_cycle.py"),
            "--run-id",
            runtime_run_id,
            "--runtime-config",
            str(args.runtime_config),
            "--candles-dir",
            str(ingest_output_dir),
            "--decision-time-policy",
            args.decision_time_policy,
            "--entry-policy",
            args.entry_policy,
            "--max-hold-bars",
            str(args.max_hold_bars),
            "--notional-capital-inr",
            str(notional),
        ]
        if args.skip_dashboard:
            runtime_command.append("--skip-dashboard")
        if args.skip_paper:
            runtime_command.append("--skip-paper")
        run_command(runtime_command, log_path, "runtime_cycle")
        runtime_audit = read_json(runtime_audit_path)
        phases.append(
            phase(
                "runtime_cycle",
                runtime_audit.get("status", "unknown"),
                {"audit": rel(runtime_audit_path), "report": rel(V2_ROOT / "reports" / f"{runtime_run_id}_report.md")},
            )
        )
        status = "passed" if ingest_audit.get("passed") and runtime_audit.get("status") in {"passed", "partial"} else "failed"
    except Exception as exc:
        phases.append(phase("live_paper_cycle", "failed", error=str(exc)))
        status = "failed"

    audit = {
        "version": "SIGNAL_MODEL_V2_LIVE_DISABLED_PAPER_CYCLE_AUDIT",
        "run_id": run_id,
        "generated_at": utc_stamp(),
        "runtime_config": rel(args.runtime_config),
        "provider": provider,
        "tickers": tickers,
        "status": status,
        "production_ready": False,
        "order_placement_enabled": False,
        "phases": phases,
        "summary": {
            "ticker_count": len(tickers),
            "ingest_passed": any(item["name"] == "candle_ingestion" and item["status"] == "passed" for item in phases),
            "runtime_cycle_status": next((item["status"] for item in phases if item["name"] == "runtime_cycle"), "not_run"),
            "production_ready": False,
        },
        "outputs": {
            "ingest_audit": rel(ingest_audit_path),
            "ingest_output_dir": rel(ingest_output_dir),
            "runtime_audit": rel(runtime_audit_path),
            "log": rel(log_path),
            "report": rel(report_path),
        },
    }
    write_json(audit_path, audit)
    write_report(report_path, audit)
    append_jsonl(log_path, {"ts": utc_stamp(), "event": "finish", "status": status, "audit": rel(audit_path)})
    print(f"Wrote {audit_path}")
    print(f"Wrote {report_path}")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
