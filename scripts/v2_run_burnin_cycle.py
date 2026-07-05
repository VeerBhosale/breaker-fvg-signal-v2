from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

from v2_common import V2_ROOT, append_jsonl, python_exe, read_json, rel, run_command, utc_stamp, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a V2 production-validation burn-in cycle with strict logs. "
            "The cycle can reuse existing V2 candles or fetch/normalize candles first, then runs replay, "
            "dashboard bridge, paper replay, validation gate, and production-readiness refresh."
        )
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--candles-dir", type=Path, default=None, help="Existing V2-owned candle directory.")
    parser.add_argument("--ingest-provider", choices=["local_csv", "yfinance"], default=None)
    parser.add_argument("--universe-file", type=Path, default=None)
    parser.add_argument("--ticker", action="append", default=[])
    parser.add_argument("--tickers", default=None, help="Comma-separated ticker list for replay/ingest.")
    parser.add_argument("--source-dir", type=Path, default=None, help="local_csv source dir for ingestion.")
    parser.add_argument("--period", default="730d")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--min-rows", type=int, default=50)
    parser.add_argument("--allow-partial-ingest", action="store_true")
    parser.add_argument("--resume-replay", action="store_true", help="Pass --resume into v2_run_replay_batch.")
    parser.add_argument("--reuse-replay-run-id", default=None, help="Reuse an existing replay audit instead of rerunning replay.")
    parser.add_argument("--decision-time-policy", choices=["signal_time", "next_candle_after_signal"], default="signal_time")
    parser.add_argument(
        "--max-input-candles",
        type=int,
        default=None,
        help="Optional latest-N candle bound passed into signal detection during replay.",
    )
    parser.add_argument("--step-timeout-seconds", type=int, default=300)
    parser.add_argument("--ticker-timeout-seconds", type=int, default=900)
    parser.add_argument("--workers", type=int, default=1, help="Ticker-level replay workers passed to v2_run_replay_batch.")
    parser.add_argument("--notional-capital-inr", type=float, default=1_000_000.0)
    parser.add_argument("--entry-policy", choices=["next_touch", "next_open"], default="next_touch")
    parser.add_argument("--max-hold-bars", type=int, default=120)
    parser.add_argument("--skip-dashboard", action="store_true")
    parser.add_argument("--skip-paper", action="store_true")
    parser.add_argument("--skip-production-readiness-refresh", action="store_true")
    parser.add_argument("--validation-min-attempted-tickers", type=int, default=25)
    parser.add_argument("--validation-min-passed-tickers", type=int, default=10)
    parser.add_argument("--validation-min-signal-rows", type=int, default=50)
    parser.add_argument("--validation-min-decision-rows", type=int, default=50)
    parser.add_argument("--validation-min-scored-liquidity-rows", type=int, default=100)
    parser.add_argument("--validation-min-approved-artifact-decision-rows", type=int, default=25)
    parser.add_argument("--validation-min-entered-trades", type=int, default=5)
    parser.add_argument("--feature-validation-min-audits", type=int, default=25)
    parser.add_argument("--feature-validation-min-rows", type=int, default=50)
    parser.add_argument("--feature-validation-min-allowed-rate", type=float, default=0.95)
    parser.add_argument("--feature-validation-max-blocking-tickers", type=int, default=0)
    parser.add_argument("--feature-validation-max-missing-all-pct", type=float, default=0.25)
    return parser.parse_args()


def parse_tickers(args: argparse.Namespace) -> List[str]:
    tickers: List[str] = []
    tickers.extend(str(item).strip() for item in args.ticker if str(item).strip())
    if args.tickers:
        tickers.extend(item.strip() for item in args.tickers.split(",") if item.strip())
    seen: set[str] = set()
    result: List[str] = []
    for ticker in tickers:
        if ticker not in seen:
            seen.add(ticker)
            result.append(ticker)
    return result


def phase(name: str, status: str, outputs: Dict[str, Any] | None = None, error: str = "") -> Dict[str, Any]:
    return {"name": name, "status": status, "outputs": outputs or {}, "error": error}


def decision_paths_from_replay(replay_audit: Dict[str, Any]) -> List[Path]:
    paths: List[Path] = []
    for row in replay_audit.get("tickers", []):
        run_id = str(row.get("run_id") or "")
        if not run_id or int(row.get("decision_rows", 0) or 0) <= 0:
            continue
        path = V2_ROOT / "data" / "predictions" / f"{run_id}_decisions.csv"
        if path.exists():
            paths.append(path)
    return paths


def liquidity_dirs_from_replay(replay_audit: Dict[str, Any]) -> List[Path]:
    paths: List[Path] = []
    for row in replay_audit.get("tickers", []):
        run_id = str(row.get("run_id") or "")
        if not run_id or int(row.get("decision_rows", 0) or 0) <= 0:
            continue
        path = V2_ROOT / "data" / "liquidity" / run_id
        if path.exists():
            paths.append(path)
    return paths


def write_report(path: Path, audit: Dict[str, Any]) -> None:
    lines = [
        "# V2 Burn-In Cycle Report",
        "",
        f"- Run ID: `{audit['run_id']}`",
        f"- Generated: `{audit['generated_at']}`",
        f"- Status: `{audit['status']}`",
        f"- Validation passed: `{str(audit['summary']['validation_passed']).lower()}`",
        f"- Production readiness refreshed: `{str(audit['summary']['production_readiness_refreshed']).lower()}`",
        "",
        "## Inputs",
        "",
        f"- Candles dir: `{audit.get('candles_dir') or ''}`",
        f"- Ingest provider: `{audit.get('ingest_provider') or ''}`",
        f"- Universe file: `{audit.get('universe_file') or ''}`",
        f"- Tickers: `{', '.join(audit.get('tickers') or [])}`",
        f"- Replay workers: `{audit.get('workers')}`",
        "",
        "## Phases",
        "",
        "| Phase | Status | Outputs | Error |",
        "|---|---:|---|---|",
    ]
    for item in audit.get("phases", []):
        outputs = ", ".join(f"{key}={value}" for key, value in (item.get("outputs") or {}).items())
        lines.append(
            "| {name} | `{status}` | {outputs} | {error} |".format(
                name=item.get("name"),
                status=item.get("status"),
                outputs=outputs.replace("|", "/"),
                error=str(item.get("error") or "").replace("|", "/"),
            )
        )
    lines.extend(["", "## Summary", ""])
    for key, value in audit.get("summary", {}).items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This is the command wrapper for production-scale burn-in. Passing the burn-in runner means the pipeline executed and wrote auditable artifacts.",
            "It does not mean the model is production-ready unless the validation gate and production-readiness audit pass.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"v2_burnin_cycle_{utc_stamp()}"
    log_path = V2_ROOT / "logs" / f"{run_id}.jsonl"
    audit_path = V2_ROOT / "audits" / f"{run_id}_audit.json"
    report_path = V2_ROOT / "reports" / f"{run_id}_report.md"
    tickers = parse_tickers(args)
    phases: List[Dict[str, Any]] = []

    append_jsonl(
        log_path,
        {
            "ts": utc_stamp(),
            "event": "start",
            "run_id": run_id,
            "candles_dir": str(args.candles_dir or ""),
            "ingest_provider": args.ingest_provider or "",
            "universe_file": str(args.universe_file or ""),
            "tickers": tickers,
            "limit": args.limit,
            "resume_replay": bool(args.resume_replay),
            "max_input_candles": args.max_input_candles,
            "step_timeout_seconds": args.step_timeout_seconds,
            "ticker_timeout_seconds": args.ticker_timeout_seconds,
            "workers": args.workers,
        },
    )

    candles_dir = args.candles_dir
    hard_failed = False

    try:
        if candles_dir is None:
            if args.ingest_provider is None:
                raise ValueError("Either --candles-dir or --ingest-provider is required.")
            ingest_run_id = f"{run_id}_ingest"
            command = [
                python_exe(),
                str(V2_ROOT / "scripts" / "v2_ingest_candles.py"),
                "--provider",
                args.ingest_provider,
                "--run-id",
                ingest_run_id,
                "--interval",
                args.interval,
                "--min-rows",
                str(args.min_rows),
            ]
            if args.universe_file:
                command.extend(["--universe-file", str(args.universe_file)])
            for ticker in tickers:
                command.extend(["--ticker", ticker])
            if args.source_dir:
                command.extend(["--source-dir", str(args.source_dir)])
            if args.ingest_provider == "yfinance":
                command.extend(["--period", args.period])
                if args.start:
                    command.extend(["--start", args.start])
                if args.end:
                    command.extend(["--end", args.end])
            if args.allow_partial_ingest:
                command.append("--allow-partial")
            run_command(command, log_path, "ingest")
            ingest_audit_path = V2_ROOT / "audits" / f"{ingest_run_id}_audit.json"
            ingest_audit = read_json(ingest_audit_path)
            candles_dir = V2_ROOT / "data" / "raw" / ingest_run_id
            phases.append(
                phase(
                    "ingest",
                    "passed" if ingest_audit.get("passed") else "failed",
                    {
                        "audit": rel(ingest_audit_path),
                        "output_dir": rel(candles_dir),
                        "passed_count": ingest_audit.get("passed_count"),
                        "failed_count": ingest_audit.get("failed_count"),
                    },
                )
            )
        else:
            phases.append(phase("ingest", "skipped_existing_candles", {"candles_dir": rel(candles_dir)}))

        replay_run_id = args.reuse_replay_run_id or f"{run_id}_replay"
        replay_audit_path = V2_ROOT / "audits" / f"{replay_run_id}_audit.json"
        if args.reuse_replay_run_id:
            if not replay_audit_path.exists():
                raise FileNotFoundError(f"Replay audit does not exist: {replay_audit_path}")
            replay_audit = read_json(replay_audit_path)
            phases.append(phase("replay", "reused", {"audit": rel(replay_audit_path)}))
        else:
            if candles_dir is None:
                raise ValueError("No candles directory available for replay.")
            command = [
                python_exe(),
                str(V2_ROOT / "scripts" / "v2_run_replay_batch.py"),
                "--candles-dir",
                str(candles_dir),
                "--run-id",
                replay_run_id,
                "--decision-time-policy",
                args.decision_time_policy,
                "--continue-on-error",
            ]
            if tickers:
                command.extend(["--tickers", ",".join(tickers)])
            if args.limit is not None:
                command.extend(["--limit", str(args.limit)])
            if args.resume_replay:
                command.append("--resume")
            if args.max_input_candles is not None:
                command.extend(["--max-input-candles", str(args.max_input_candles)])
            command.extend(
                [
                    "--step-timeout-seconds",
                    str(args.step_timeout_seconds),
                    "--ticker-timeout-seconds",
                    str(args.ticker_timeout_seconds),
                    "--workers",
                    str(args.workers),
                ]
            )
            run_command(command, log_path, "replay")
            replay_audit = read_json(replay_audit_path)
            phases.append(
                phase(
                    "replay",
                    replay_audit.get("status", "unknown"),
                    {
                        "audit": rel(replay_audit_path),
                        "attempted": replay_audit.get("attempted_count"),
                        "passed": replay_audit.get("passed_count"),
                        "signals": replay_audit.get("summary", {}).get("signal_rows"),
                        "decisions": replay_audit.get("summary", {}).get("decision_rows"),
                    },
                )
            )

        decision_paths = decision_paths_from_replay(replay_audit)
        liquidity_dirs = liquidity_dirs_from_replay(replay_audit)

        feature_validation_run_id = f"{run_id}_feature_validation"
        feature_validation_audit_path = V2_ROOT / "audits" / f"{feature_validation_run_id}_audit.json"
        try:
            run_command(
                [
                    python_exe(),
                    str(V2_ROOT / "scripts" / "v2_feature_broad_validation_audit.py"),
                    "--run-id",
                    feature_validation_run_id,
                    "--replay-audit",
                    str(replay_audit_path),
                    "--min-feature-audits",
                    str(args.feature_validation_min_audits),
                    "--min-feature-rows",
                    str(args.feature_validation_min_rows),
                    "--min-classification-allowed-rate",
                    str(args.feature_validation_min_allowed_rate),
                    "--max-blocking-missing-tickers",
                    str(args.feature_validation_max_blocking_tickers),
                    "--max-missing-all-rows-pct",
                    str(args.feature_validation_max_missing_all_pct),
                ],
                log_path,
                "feature_validation",
            )
            phases.append(phase("feature_validation", "passed", {"audit": rel(feature_validation_audit_path)}))
        except Exception as exc:
            feature_validation_audit = read_json(feature_validation_audit_path) if feature_validation_audit_path.exists() else {}
            phases.append(
                phase(
                    "feature_validation",
                    "failed",
                    {
                        "audit": rel(feature_validation_audit_path) if feature_validation_audit_path.exists() else "",
                        "failed_checks": feature_validation_audit.get("summary", {}).get("failed_checks", []),
                    },
                    str(exc),
                )
            )

        dashboard_audit_path: Path | None = None
        if args.skip_dashboard:
            phases.append(phase("dashboard_bridge", "skipped"))
        elif decision_paths:
            dashboard_run_id = f"{run_id}_dashboard_bridge"
            dashboard_audit_path = V2_ROOT / "audits" / f"{dashboard_run_id}_audit.json"
            command = [
                python_exe(),
                str(V2_ROOT / "scripts" / "v2_export_dashboard_bridge.py"),
                "--run-id",
                dashboard_run_id,
                "--decisions",
                *[str(path) for path in decision_paths],
            ]
            if liquidity_dirs:
                command.extend(["--liquidity-dirs", *[str(path) for path in liquidity_dirs]])
            run_command(command, log_path, "dashboard_bridge")
            phases.append(phase("dashboard_bridge", "passed", {"audit": rel(dashboard_audit_path)}))
        else:
            phases.append(phase("dashboard_bridge", "skipped_no_decisions"))

        paper_audit_path: Path | None = None
        if args.skip_paper:
            phases.append(phase("paper_replay", "skipped"))
        elif decision_paths and candles_dir is not None:
            paper_run_id = f"{run_id}_paper"
            paper_audit_path = V2_ROOT / "audits" / f"{paper_run_id}_audit.json"
            command = [
                python_exe(),
                str(V2_ROOT / "scripts" / "v2_paper_replay_from_decisions.py"),
                "--run-id",
                paper_run_id,
                "--candles-dir",
                str(candles_dir),
                "--entry-policy",
                args.entry_policy,
                "--max-hold-bars",
                str(args.max_hold_bars),
                "--notional-capital-inr",
                str(args.notional_capital_inr),
                "--decisions",
                *[str(path) for path in decision_paths],
            ]
            run_command(command, log_path, "paper_replay")
            phases.append(phase("paper_replay", "passed", {"audit": rel(paper_audit_path)}))
        else:
            phases.append(phase("paper_replay", "skipped_no_decisions"))

        validation_run_id = f"{run_id}_validation_gate"
        validation_audit_path = V2_ROOT / "audits" / f"{validation_run_id}_audit.json"
        command = [
            python_exe(),
            str(V2_ROOT / "scripts" / "v2_validation_gate_audit.py"),
            "--run-id",
            validation_run_id,
            "--replay-audit",
            str(replay_audit_path),
            "--min-attempted-tickers",
            str(args.validation_min_attempted_tickers),
            "--min-passed-tickers",
            str(args.validation_min_passed_tickers),
            "--min-signal-rows",
            str(args.validation_min_signal_rows),
            "--min-decision-rows",
            str(args.validation_min_decision_rows),
            "--min-scored-liquidity-rows",
            str(args.validation_min_scored_liquidity_rows),
            "--min-approved-artifact-decision-rows",
            str(args.validation_min_approved_artifact_decision_rows),
            "--min-entered-trades",
            str(args.validation_min_entered_trades),
        ]
        if dashboard_audit_path is not None:
            command.extend(["--dashboard-audit", str(dashboard_audit_path)])
        if paper_audit_path is not None:
            command.extend(["--paper-audit", str(paper_audit_path)])
        try:
            run_command(command, log_path, "validation_gate")
            validation_audit = read_json(validation_audit_path)
            phases.append(phase("validation_gate", "passed", {"audit": rel(validation_audit_path)}))
        except Exception as exc:
            validation_audit = read_json(validation_audit_path) if validation_audit_path.exists() else {}
            phases.append(
                phase(
                    "validation_gate",
                    "failed",
                    {
                        "audit": rel(validation_audit_path) if validation_audit_path.exists() else "",
                        "failed_checks": validation_audit.get("summary", {}).get("failed_checks", []),
                    },
                    str(exc),
                )
            )

        production_audit_path: Path | None = None
        if args.skip_production_readiness_refresh:
            phases.append(phase("production_readiness", "skipped"))
        else:
            production_run_id = "v2_production_readiness_current"
            production_audit_path = V2_ROOT / "audits" / f"{production_run_id}_audit.json"
            run_command(
                [
                    python_exe(),
                    str(V2_ROOT / "scripts" / "v2_production_readiness_audit.py"),
                    "--run-id",
                    production_run_id,
                ],
                log_path,
                "production_readiness",
            )
            production_audit = read_json(production_audit_path)
            phases.append(
                phase(
                    "production_readiness",
                    "passed",
                    {
                        "audit": rel(production_audit_path),
                        "ready": production_audit.get("summary", {}).get("production_ready"),
                        "blockers": production_audit.get("summary", {}).get("blocker_count"),
                    },
                )
            )
    except Exception as exc:
        hard_failed = True
        phases.append(phase("burnin_cycle", "failed", error=str(exc)))
        append_jsonl(log_path, {"ts": utc_stamp(), "event": "failed", "error": str(exc)})

    validation_phase = next((item for item in phases if item["name"] == "validation_gate"), {})
    validation_passed = validation_phase.get("status") == "passed"
    production_phase = next((item for item in phases if item["name"] == "production_readiness"), {})
    production_refreshed = production_phase.get("status") == "passed"
    status = "failed" if hard_failed else "passed_smoke" if phases and phases[-1].get("status") != "failed" else "partial"
    audit = {
        "version": "SIGNAL_MODEL_V2_BURNIN_CYCLE_AUDIT",
        "run_id": run_id,
        "generated_at": utc_stamp(),
        "status": status,
        "candles_dir": rel(candles_dir) if candles_dir else "",
        "ingest_provider": args.ingest_provider or "",
        "universe_file": rel(args.universe_file) if args.universe_file else "",
        "tickers": tickers,
        "limit": args.limit,
        "resume_replay": bool(args.resume_replay),
        "max_input_candles": args.max_input_candles,
        "step_timeout_seconds": args.step_timeout_seconds,
        "ticker_timeout_seconds": args.ticker_timeout_seconds,
        "workers": args.workers,
        "log": rel(log_path),
        "report": rel(report_path),
        "phases": phases,
        "summary": {
            "hard_failed": hard_failed,
            "validation_passed": validation_passed,
            "production_readiness_refreshed": production_refreshed,
            "phase_count": len(phases),
            "failed_phases": [item["name"] for item in phases if item.get("status") == "failed"],
        },
    }
    write_json(audit_path, audit)
    write_report(report_path, audit)
    append_jsonl(log_path, {"ts": utc_stamp(), "event": "finish", "status": status, "audit": rel(audit_path)})
    print(f"Wrote {audit_path}")
    print(f"Wrote {report_path}")
    print(
        "burnin_status={status} validation_passed={validation} failed_phases={failed}".format(
            status=status,
            validation=validation_passed,
            failed=",".join(audit["summary"]["failed_phases"]),
        )
    )
    return 1 if hard_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
