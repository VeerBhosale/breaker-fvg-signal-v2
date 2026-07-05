from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from v2_common import V2_ROOT, append_jsonl, python_exe, read_json, rel, run_command, utc_stamp, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run or reuse a V2 replay batch, then publish dashboard bridge artifacts and paper replay outputs "
            "under one runtime-cycle audit."
        )
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--runtime-config", type=Path, default=V2_ROOT / "configs" / "v2_runtime_config.example.json")
    parser.add_argument("--candles-dir", type=Path, default=None)
    parser.add_argument("--replay-run-id", default=None, help="Reuse an existing replay batch audit instead of rerunning replay.")
    parser.add_argument("--tickers", default=None)
    parser.add_argument("--limit", type=int, default=None)
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
    parser.add_argument("--notional-capital-inr", type=float, default=None)
    parser.add_argument("--entry-policy", choices=["next_touch", "next_open"], default="next_touch")
    parser.add_argument("--max-hold-bars", type=int, default=120)
    parser.add_argument("--skip-dashboard", action="store_true")
    parser.add_argument("--skip-paper", action="store_true")
    return parser.parse_args()


def safe_token(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")
    return cleaned or "item"


def decision_and_liquidity_paths_from_replay(replay_audit: Dict[str, Any]) -> Tuple[List[Path], List[Path]]:
    decisions: List[Path] = []
    liquidity_dirs: List[Path] = []
    for row in replay_audit.get("tickers", []):
        if int(row.get("decision_rows", 0) or 0) <= 0:
            continue
        ticker_run_id = str(row.get("run_id") or "")
        if not ticker_run_id:
            continue
        decision_path = V2_ROOT / "data" / "predictions" / f"{ticker_run_id}_decisions.csv"
        liquidity_dir = V2_ROOT / "data" / "liquidity" / ticker_run_id
        if decision_path.exists():
            decisions.append(decision_path)
        if liquidity_dir.exists():
            liquidity_dirs.append(liquidity_dir)
    return decisions, liquidity_dirs


def write_report(path: Path, audit: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = audit["summary"]
    lines = [
        "# V2 Runtime Cycle Report",
        "",
        f"Run ID: `{audit['run_id']}`",
        f"Generated: `{audit['generated_at']}`",
        f"Runtime config: `{audit['runtime_config']}`",
        "",
        "## Summary",
        "",
        f"- Status: `{audit['status']}`",
        f"- Replay mode: `{audit['replay_mode']}`",
        f"- Decision files: `{summary['decision_file_count']}`",
        f"- Liquidity dirs: `{summary['liquidity_dir_count']}`",
        f"- Replay workers: `{audit.get('workers')}`",
        f"- Dashboard bridge ran: `{summary['dashboard_bridge_ran']}`",
        f"- Paper replay ran: `{summary['paper_replay_ran']}`",
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
            "- This runtime cycle does not place orders.",
            "- Runtime config live/order-placement safety defaults are audited separately.",
            "- Reused replay mode is valid for orchestration testing only; a fresh runtime run should execute replay directly.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def phase_payload(name: str, status: str, outputs: Dict[str, str] | None = None, error: str | None = None) -> Dict[str, Any]:
    return {"name": name, "status": status, "outputs": outputs or {}, "error": error or ""}


def resolve_effective_max_input_candles(cli_value: int | None, runtime_config: Dict[str, Any]) -> Tuple[int | None, str]:
    if cli_value is not None:
        return cli_value, "cli"
    rolling_config = runtime_config.get("rolling_history", {})
    configured_lookback = rolling_config.get("runtime_lookback_candles")
    if configured_lookback in ("", None):
        return None, "unset"
    return int(configured_lookback), "runtime_config"


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"v2_runtime_cycle_{utc_stamp()}"
    log_path = V2_ROOT / "logs" / f"{run_id}.jsonl"
    audit_path = V2_ROOT / "audits" / f"{run_id}_audit.json"
    report_path = V2_ROOT / "reports" / f"{run_id}_report.md"
    runtime_config = read_json(args.runtime_config)
    effective_max_input_candles, max_input_candles_source = resolve_effective_max_input_candles(
        args.max_input_candles, runtime_config
    )
    paper_config = runtime_config.get("modes", {}).get("paper", {})
    notional = args.notional_capital_inr
    if notional is None:
        notional = float(paper_config.get("notional_capital_inr") or 1_000_000.0)

    append_jsonl(
        log_path,
        {
            "ts": utc_stamp(),
            "event": "start",
            "run_id": run_id,
            "runtime_config": str(args.runtime_config),
            "candles_dir": str(args.candles_dir) if args.candles_dir else "",
            "replay_run_id": args.replay_run_id or "",
            "max_input_candles": effective_max_input_candles,
            "max_input_candles_source": max_input_candles_source,
            "step_timeout_seconds": args.step_timeout_seconds,
            "ticker_timeout_seconds": args.ticker_timeout_seconds,
            "workers": args.workers,
        },
    )

    phases: List[Dict[str, Any]] = []
    replay_run_id = args.replay_run_id or f"{run_id}_replay"
    replay_audit_path = V2_ROOT / "audits" / f"{replay_run_id}_audit.json"
    replay_mode = "reused_existing_replay_audit" if args.replay_run_id else "executed_replay_batch"

    try:
        if args.replay_run_id:
            if not replay_audit_path.exists():
                raise FileNotFoundError(f"Replay audit does not exist: {replay_audit_path}")
            replay_audit = read_json(replay_audit_path)
            phases.append(
                phase_payload(
                    "replay_batch",
                    "reused",
                    {"audit": rel(replay_audit_path), "report": rel(V2_ROOT / "reports" / f"{replay_run_id}_report.md")},
                )
            )
        else:
            if args.candles_dir is None:
                raise ValueError("--candles-dir is required when replay is not reused.")
            command: List[str] = [
                python_exe(),
                str(V2_ROOT / "scripts" / "v2_run_replay_batch.py"),
                "--candles-dir",
                str(args.candles_dir),
                "--run-id",
                replay_run_id,
                "--decision-time-policy",
                args.decision_time_policy,
                "--continue-on-error",
            ]
            if args.tickers:
                command.extend(["--tickers", args.tickers])
            if args.limit is not None:
                command.extend(["--limit", str(args.limit)])
            if effective_max_input_candles is not None:
                command.extend(["--max-input-candles", str(effective_max_input_candles)])
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
            run_command(command, log_path, "replay_batch")
            replay_audit = read_json(replay_audit_path)
            phases.append(
                phase_payload(
                    "replay_batch",
                    replay_audit.get("status", "unknown"),
                    {"audit": rel(replay_audit_path), "report": rel(V2_ROOT / "reports" / f"{replay_run_id}_report.md")},
                )
            )

        decision_paths, liquidity_dirs = decision_and_liquidity_paths_from_replay(replay_audit)
        append_jsonl(
            log_path,
            {
                "ts": utc_stamp(),
                "event": "collected_replay_outputs",
                "decision_count": len(decision_paths),
                "liquidity_dir_count": len(liquidity_dirs),
            },
        )

        dashboard_run_id = f"{run_id}_dashboard_bridge"
        if args.skip_dashboard:
            phases.append(phase_payload("dashboard_bridge", "skipped"))
        elif decision_paths:
            command = [
                python_exe(),
                str(V2_ROOT / "scripts" / "v2_export_dashboard_bridge.py"),
                "--decisions",
                *[str(path) for path in decision_paths],
                "--run-id",
                dashboard_run_id,
            ]
            if liquidity_dirs:
                command.extend(["--liquidity-dirs", *[str(path) for path in liquidity_dirs]])
            run_command(command, log_path, "dashboard_bridge")
            phases.append(
                phase_payload(
                    "dashboard_bridge",
                    "passed",
                    {
                        "audit": rel(V2_ROOT / "audits" / f"{dashboard_run_id}_audit.json"),
                        "report": rel(V2_ROOT / "reports" / f"{dashboard_run_id}_report.md"),
                        "bridge_dir": rel(V2_ROOT / "dashboard_bridge" / dashboard_run_id),
                    },
                )
            )
        else:
            phases.append(phase_payload("dashboard_bridge", "skipped_no_decisions"))

        paper_run_id = f"{run_id}_paper"
        if args.skip_paper:
            phases.append(phase_payload("paper_replay", "skipped"))
        elif decision_paths and args.candles_dir is not None:
            run_command(
                [
                    python_exe(),
                    str(V2_ROOT / "scripts" / "v2_paper_replay_from_decisions.py"),
                    "--decisions",
                    *[str(path) for path in decision_paths],
                    "--candles-dir",
                    str(args.candles_dir),
                    "--run-id",
                    paper_run_id,
                    "--entry-policy",
                    args.entry_policy,
                    "--max-hold-bars",
                    str(args.max_hold_bars),
                    "--notional-capital-inr",
                    str(notional),
                ],
                log_path,
                "paper_replay",
            )
            phases.append(
                phase_payload(
                    "paper_replay",
                    "passed",
                    {
                        "audit": rel(V2_ROOT / "audits" / f"{paper_run_id}_audit.json"),
                        "report": rel(V2_ROOT / "reports" / f"{paper_run_id}_report.md"),
                        "trades": rel(V2_ROOT / "data" / "paper" / f"{paper_run_id}_trades.csv"),
                        "equity_curve": rel(V2_ROOT / "data" / "paper" / f"{paper_run_id}_equity_curve.csv"),
                    },
                )
            )
        elif decision_paths:
            phases.append(phase_payload("paper_replay", "skipped_missing_candles_dir"))
        else:
            phases.append(phase_payload("paper_replay", "skipped_no_decisions"))

        hard_failed = any(phase["status"] in {"failed", "error"} for phase in phases)
        status = "passed" if not hard_failed and decision_paths else "partial" if not hard_failed else "failed"
        audit = {
            "version": "SIGNAL_MODEL_V2_RUNTIME_CYCLE_AUDIT",
            "run_id": run_id,
            "generated_at": utc_stamp(),
            "runtime_config": rel(args.runtime_config),
            "candles_dir": rel(args.candles_dir) if args.candles_dir else "",
            "replay_mode": replay_mode,
            "replay_run_id": replay_run_id,
            "max_input_candles": effective_max_input_candles,
            "max_input_candles_source": max_input_candles_source,
            "step_timeout_seconds": args.step_timeout_seconds,
            "ticker_timeout_seconds": args.ticker_timeout_seconds,
            "workers": args.workers,
            "replay_audit": rel(replay_audit_path),
            "status": status,
            "production_ready": False,
            "phases": phases,
            "summary": {
                "decision_file_count": len(decision_paths),
                "liquidity_dir_count": len(liquidity_dirs),
                "dashboard_bridge_ran": any(phase["name"] == "dashboard_bridge" and phase["status"] == "passed" for phase in phases),
                "paper_replay_ran": any(phase["name"] == "paper_replay" and phase["status"] == "passed" for phase in phases),
                "production_ready": False,
            },
            "log": rel(log_path),
            "report": rel(report_path),
        }
    except Exception as exc:
        phases.append(phase_payload("runtime_cycle", "failed", error=str(exc)))
        audit = {
            "version": "SIGNAL_MODEL_V2_RUNTIME_CYCLE_AUDIT",
            "run_id": run_id,
            "generated_at": utc_stamp(),
            "runtime_config": rel(args.runtime_config),
            "candles_dir": rel(args.candles_dir) if args.candles_dir else "",
            "replay_mode": replay_mode,
            "replay_run_id": replay_run_id,
            "max_input_candles": effective_max_input_candles,
            "max_input_candles_source": max_input_candles_source,
            "step_timeout_seconds": args.step_timeout_seconds,
            "ticker_timeout_seconds": args.ticker_timeout_seconds,
            "workers": args.workers,
            "replay_audit": rel(replay_audit_path),
            "status": "failed",
            "production_ready": False,
            "phases": phases,
            "summary": {
                "decision_file_count": 0,
                "liquidity_dir_count": 0,
                "dashboard_bridge_ran": False,
                "paper_replay_ran": False,
                "production_ready": False,
            },
            "error": str(exc),
            "log": rel(log_path),
            "report": rel(report_path),
        }

    write_json(audit_path, audit)
    write_report(report_path, audit)
    append_jsonl(log_path, {"ts": utc_stamp(), "event": "finish", "status": audit["status"], "audit": rel(audit_path)})
    print(f"Wrote {audit_path}")
    print(f"Wrote {report_path}")
    return 0 if audit["status"] in {"passed", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
