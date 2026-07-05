from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

from v2_common import BREAKER_BASED_ROOT, REPO_ROOT, V2_ROOT, read_json, rel, utc_stamp, write_json


REQUIRED_FOLDERS = [
    "configs",
    "data/raw",
    "data/features",
    "data/signals",
    "data/liquidity",
    "data/predictions",
    "models",
    "scripts",
    "logs",
    "reports",
    "audits",
    "docs",
    "tests",
    "aws",
]

REQUIRED_SCRIPTS = [
    "v2_ingest_candles.py",
    "v2_update_candle_store_incremental.py",
    "v2_detect_signals_from_candles.py",
    "v2_build_live_features_from_events.py",
    "v2_build_liquidity_payloads_from_events.py",
    "v2_score_liquidity_candidates.py",
    "v2_run_signal_inference.py",
    "v2_paper_replay_from_decisions.py",
    "v2_export_dashboard_bridge.py",
    "v2_run_runtime_cycle.py",
    "v2_run_replay_batch.py",
    "v2_aws_readiness_audit.py",
    "v2_production_readiness_audit.py",
    "v2_validation_gate_audit.py",
]

REQUIRED_DOCS = [
    "V2_ARCHITECTURE.md",
    "V2_DATA_CONTRACTS.md",
    "V2_FEATURE_PARITY_PLAN.md",
    "V2_LIVE_RUNTIME_FLOW.md",
    "V2_RUNBOOK.md",
    "V2_AWS_DEPLOYMENT_PLAN.md",
    "V2_KNOWN_LIMITATIONS.md",
    "V2_CURRENT_STATUS.md",
]

EVIDENCE = {
    "production": V2_ROOT / "audits" / "v2_production_readiness_current_audit.json",
    "aws": V2_ROOT / "audits" / "v2_aws_readiness_current_audit.json",
    "incremental_store_smoke": V2_ROOT / "audits" / "v2_incremental_store_local_smoke_audit.json",
    "ingest_full": V2_ROOT / "audits" / "v2_yfinance_ingest_original_179_1h_730d_full_burnin_audit.json",
    "replay_full": V2_ROOT / "audits" / "v2_replay_original_fresh_178_tail300_parallel_burnin_audit.json",
    "feature_full": V2_ROOT / "audits" / "v2_feature_validation_original_fresh_178_tail300_parallel_burnin_audit.json",
    "dashboard_full": V2_ROOT / "audits" / "v2_dashboard_bridge_original_fresh_178_tail300_parallel_burnin_audit.json",
    "paper_full": V2_ROOT / "audits" / "v2_paper_replay_original_fresh_178_tail300_parallel_burnin_audit.json",
    "validation_full": V2_ROOT / "audits" / "v2_validation_gate_original_fresh_178_tail300_parallel_burnin_audit.json",
    "both_side_detect": V2_ROOT / "audits" / "v2_signal_detect_360one_both_side_yfinance_60d_smoke_audit.json",
    "both_side_inference": V2_ROOT / "audits" / "v2_inference_360one_both_side_yfinance_60d_side_gate_smoke_audit.json",
}

ORIGINAL_ENGINE_PATHS = [
    BREAKER_BASED_ROOT / "breaker_fvg_dashboard.html",
    BREAKER_BASED_ROOT / "Breaker+FVG.ipynb",
    BREAKER_BASED_ROOT / "Breaker.ipynb",
    BREAKER_BASED_ROOT / "ISL+BOS.ipynb",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Map the V2 goal requirements to concrete current-state evidence."
    )
    parser.add_argument("--run-id", default=f"v2_goal_completion_{utc_stamp()}")
    parser.add_argument(
        "--run-static-tests",
        action="store_true",
        help="Run the V2 static unittest suite and record the result in this audit.",
    )
    return parser.parse_args()


def value_at(payload: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    cursor: Any = payload
    for key in keys:
        if not isinstance(cursor, dict) or key not in cursor:
            return default
        cursor = cursor[key]
    return cursor


def add_check(
    checks: List[Dict[str, Any]],
    requirement: str,
    ok: bool,
    *,
    evidence: Path | None = None,
    severity: str = "required",
    detail: Any = None,
) -> None:
    checks.append(
        {
            "requirement": requirement,
            "ok": bool(ok),
            "severity": severity,
            "evidence": rel(evidence) if evidence else None,
            "detail": detail,
        }
    )


def load_optional_json(path: Path) -> Dict[str, Any]:
    return read_json(path) if path.exists() else {}


def git_status_for(paths: List[Path]) -> List[str]:
    existing = [str(path.relative_to(REPO_ROOT)) for path in paths if path.exists()]
    if not existing:
        return []
    proc = subprocess.run(
        ["git", "status", "--short", "--", *existing],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        return [f"git_status_error:{proc.stderr.strip()}"]
    return [line for line in proc.stdout.splitlines() if line.strip()]


def run_static_tests() -> Dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "Newtest.Breaker_Based.signal_model_v2.tests.test_v2_static_contracts"],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
    )
    return {
        "returncode": proc.returncode,
        "passed": proc.returncode == 0,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


def write_report(path: Path, audit: Dict[str, Any]) -> None:
    summary = audit["summary"]
    lines = [
        "# Signal Model V2 Goal Completion Audit",
        "",
        f"- Run ID: `{audit['run_id']}`",
        f"- Generated: `{audit['generated_at']}`",
        f"- Local goal requirements passed: `{summary['required_passed']}` / `{summary['required_total']}`",
        f"- Local goal complete: `{str(summary['local_goal_complete']).lower()}`",
        f"- AWS deployable ready: `{str(summary['aws_deployable_ready']).lower()}`",
        f"- AWS deployment validated: `{str(summary['aws_deployment_validated']).lower()}`",
        f"- Real-money ready: `{str(summary['real_money_ready']).lower()}`",
        f"- Warnings: `{summary['warning_count']}`",
        "",
        "## Failed Required Items",
        "",
    ]
    failed = [check for check in audit["checks"] if check["severity"] == "required" and not check["ok"]]
    if failed:
        for check in failed:
            lines.append(f"- `{check['requirement']}`: {check.get('detail')}")
    else:
        lines.append("- None.")

    lines.extend(["", "## Warnings / External Gates", ""])
    warnings = [check for check in audit["checks"] if check["severity"] == "warning" and not check["ok"]]
    if warnings:
        for check in warnings:
            lines.append(f"- `{check['requirement']}`: {check.get('detail')}")
    else:
        lines.append("- None.")

    lines.extend(["", "## Requirement Matrix", ""])
    lines.append("| Severity | OK | Requirement | Evidence | Detail |")
    lines.append("|---|---:|---|---|---|")
    for check in audit["checks"]:
        detail = str(check.get("detail", "")).replace("|", "/")
        lines.append(
            f"| `{check['severity']}` | `{str(check['ok']).lower()}` | `{check['requirement']}` | "
            f"`{check.get('evidence') or ''}` | {detail} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    loaded = {name: load_optional_json(path) for name, path in EVIDENCE.items()}
    checks: List[Dict[str, Any]] = []

    for folder in REQUIRED_FOLDERS:
        path = V2_ROOT / folder
        add_check(checks, f"required_folder:{folder}", path.exists() and path.is_dir(), evidence=path)

    for script in REQUIRED_SCRIPTS:
        path = V2_ROOT / "scripts" / script
        add_check(checks, f"required_script:{script}", path.exists(), evidence=path)

    for doc in REQUIRED_DOCS:
        path = V2_ROOT / "docs" / doc
        add_check(checks, f"required_doc:{doc}", path.exists(), evidence=path)

    for name, path in EVIDENCE.items():
        add_check(checks, f"evidence_exists:{name}", path.exists(), evidence=path)

    runtime_config_path = V2_ROOT / "configs" / "v2_runtime_config.example.json"
    runtime_config = load_optional_json(runtime_config_path)
    rolling_history = runtime_config.get("rolling_history", {})
    add_check(
        checks,
        "rolling_history_runtime_lookback_extended",
        int(rolling_history.get("runtime_lookback_candles") or 0) > 300,
        evidence=runtime_config_path,
        detail=rolling_history,
    )
    add_check(
        checks,
        "rolling_history_incremental_store_configured",
        bool(rolling_history.get("incremental_store_dir"))
        and int(rolling_history.get("incremental_overlap_bars") or 0) >= 1
        and int(rolling_history.get("max_store_candles") or 0) >= int(rolling_history.get("runtime_lookback_candles") or 0),
        evidence=runtime_config_path,
        detail=rolling_history,
    )

    incremental = loaded["incremental_store_smoke"]
    add_check(
        checks,
        "incremental_candle_store_smoke_passed",
        incremental.get("passed") is True
        and int(incremental.get("passed_count", 0) or 0) >= 1
        and int(incremental.get("failed_count", 1) or 0) == 0
        and int(incremental.get("new_timestamps_retained", 0) or 0) >= 1,
        evidence=EVIDENCE["incremental_store_smoke"],
        detail={
            "provider": incremental.get("provider"),
            "passed_count": incremental.get("passed_count"),
            "failed_count": incremental.get("failed_count"),
            "new_timestamps_retained": incremental.get("new_timestamps_retained"),
            "max_store_candles": incremental.get("max_store_candles"),
        },
    )

    production = loaded["production"]
    production_summary = production.get("summary", {})
    add_check(
        checks,
        "production_readiness_required_checks_pass",
        value_at(production, "summary", "required_failed", default=1) == 0
        and value_at(production, "summary", "required_passed", default=0) >= 53
        and value_at(production, "summary", "production_ready") is True,
        evidence=EVIDENCE["production"],
        detail=production_summary,
    )
    add_check(
        checks,
        "aws_deployable_ready_from_local_contracts",
        value_at(production, "summary", "aws_deployable_ready") is True,
        evidence=EVIDENCE["production"],
        detail=production_summary,
    )

    ingest = loaded["ingest_full"]
    add_check(
        checks,
        "fresh_original_universe_ingestion_passed",
        ingest.get("passed") is True
        and int(ingest.get("ticker_count", 0) or 0) >= 178
        and int(ingest.get("passed_count", 0) or 0) >= 178
        and int(ingest.get("failed_count", 1) or 0) == 0,
        evidence=EVIDENCE["ingest_full"],
        detail={
            "ticker_count": ingest.get("ticker_count"),
            "passed_count": ingest.get("passed_count"),
            "failed_count": ingest.get("failed_count"),
        },
    )

    replay = loaded["replay_full"]
    replay_summary = replay.get("summary", {})
    add_check(
        checks,
        "fresh_to_decision_replay_passed",
        replay.get("status") == "passed"
        and int(replay.get("attempted_count", 0) or 0) >= 178
        and int(replay.get("failed_count", 1) or 0) == 0
        and int(replay_summary.get("signal_rows", 0) or 0) >= 155
        and int(replay_summary.get("decision_rows", 0) or 0) >= 155,
        evidence=EVIDENCE["replay_full"],
        detail={
            "attempted_count": replay.get("attempted_count"),
            "passed_count": replay.get("passed_count"),
            "no_signal_count": replay.get("no_signal_count"),
            "failed_count": replay.get("failed_count"),
            "summary": replay_summary,
        },
    )
    add_check(
        checks,
        "decision_time_liquidity_scored_and_aggregated",
        int(replay_summary.get("liquidity_candidate_rows", 0) or 0) >= 824
        and int(replay_summary.get("scored_liquidity_rows", 0) or 0) >= 824
        and int(replay_summary.get("aggregated_signal_rows", 0) or 0) >= 155,
        evidence=EVIDENCE["replay_full"],
        detail=replay_summary,
    )

    feature = loaded["feature_full"]
    add_check(
        checks,
        "decision_time_feature_validation_passed",
        feature.get("passed") is True
        and value_at(feature, "metrics", "classification_allowed_rate", default=0) >= 0.95
        and int(value_at(feature, "metrics", "blocking_missing_ticker_count", default=1) or 0) == 0,
        evidence=EVIDENCE["feature_full"],
        detail=value_at(feature, "metrics", default={}),
    )

    dashboard = loaded["dashboard_full"]
    add_check(
        checks,
        "dashboard_bridge_contract_passed",
        dashboard.get("status") == "passed"
        and value_at(dashboard, "dashboard_contract", "contract_ok") is True
        and int(dashboard.get("bridge_rows_written", 0) or 0) >= 155
        and int(dashboard.get("signals_with_scored_liquidity_context", 0) or 0) >= 155
        and dashboard.get("original_dashboard_modified") is False,
        evidence=EVIDENCE["dashboard_full"],
        detail={
            "bridge_rows_written": dashboard.get("bridge_rows_written"),
            "live_rows_written": dashboard.get("live_rows_written"),
            "signals_with_scored_liquidity_context": dashboard.get("signals_with_scored_liquidity_context"),
            "original_dashboard_modified": dashboard.get("original_dashboard_modified"),
        },
    )

    paper = loaded["paper_full"]
    add_check(
        checks,
        "paper_replay_safe_and_post_decision_only",
        value_at(paper, "summary", "passed") is True
        and value_at(paper, "summary", "order_placement_enabled") is False
        and value_at(paper, "summary", "uses_only_post_decision_candles") is True
        and float(paper.get("notional_capital_inr", 0) or 0) == 1000000.0,
        evidence=EVIDENCE["paper_full"],
        detail=value_at(paper, "summary", default={}),
    )

    validation = loaded["validation_full"]
    add_check(
        checks,
        "full_validation_gate_passed",
        validation.get("passed") is True
        and value_at(validation, "summary", "checks_failed", default=1) == 0,
        evidence=EVIDENCE["validation_full"],
        detail={
            "summary": validation.get("summary"),
            "metrics": validation.get("metrics"),
        },
    )

    both_detect = loaded["both_side_detect"]
    add_check(
        checks,
        "long_short_signal_detection_smoke_passed",
        int(value_at(both_detect, "side_counts", "long", default=0) or 0) >= 1
        and int(value_at(both_detect, "side_counts", "short", default=0) or 0) >= 1,
        evidence=EVIDENCE["both_side_detect"],
        detail=both_detect.get("side_counts"),
    )

    both_inference = loaded["both_side_inference"]
    add_check(
        checks,
        "approved_long_short_artifact_inference_smoke_passed",
        both_inference.get("inference_path") == "approved_artifact_scoring"
        and int(value_at(both_inference, "artifact_scoring", "long_scored_rows", default=0) or 0) >= 1
        and int(value_at(both_inference, "artifact_scoring", "short_scored_rows", default=0) or 0) >= 1,
        evidence=EVIDENCE["both_side_inference"],
        detail=both_inference.get("artifact_scoring"),
    )

    aws = loaded["aws"]
    add_check(
        checks,
        "aws_readiness_contract_passed",
        value_at(aws, "summary", "aws_deployable_ready") is True
        and int(value_at(aws, "summary", "failed_count", default=1) or 0) == 0,
        evidence=EVIDENCE["aws"],
        detail=aws.get("summary"),
    )

    if args.run_static_tests:
        test_result = run_static_tests()
        add_check(
            checks,
            "static_unittest_suite_passed",
            test_result["passed"],
            evidence=V2_ROOT / "tests" / "test_v2_static_contracts.py",
            detail={"returncode": test_result["returncode"], "stderr_tail": test_result["stderr"][-1000:]},
        )
    else:
        add_check(
            checks,
            "static_unittest_suite_not_run_in_completion_audit",
            False,
            evidence=V2_ROOT / "tests" / "test_v2_static_contracts.py",
            severity="warning",
            detail="Run with --run-static-tests to record current unittest evidence in this audit.",
        )

    original_dirty = git_status_for(ORIGINAL_ENGINE_PATHS)
    add_check(
        checks,
        "original_engine_dashboard_worktree_clean",
        not original_dirty,
        severity="warning",
        detail=original_dirty or "Tracked original engine/dashboard files are clean.",
    )
    add_check(
        checks,
        "aws_real_deployment_not_performed",
        False,
        evidence=EVIDENCE["aws"],
        severity="warning",
        detail="AWS deployment is intentionally not performed without explicit approval.",
    )
    add_check(
        checks,
        "real_order_placement_not_enabled",
        False,
        severity="warning",
        detail="Real-money order placement is intentionally disabled and has no approval gate implemented.",
    )

    required = [check for check in checks if check["severity"] == "required"]
    failed_required = [check for check in required if not check["ok"]]
    warnings = [check for check in checks if check["severity"] == "warning" and not check["ok"]]
    local_goal_complete = not failed_required
    aws_deployable_ready = value_at(loaded["production"], "summary", "aws_deployable_ready") is True

    audit = {
        "version": "SIGNAL_MODEL_V2_GOAL_COMPLETION_AUDIT",
        "run_id": args.run_id,
        "generated_at": utc_stamp(),
        "checks": checks,
        "summary": {
            "required_total": len(required),
            "required_passed": len(required) - len(failed_required),
            "required_failed": len(failed_required),
            "failed_required_items": [check["requirement"] for check in failed_required],
            "warning_count": len(warnings),
            "warning_items": [check["requirement"] for check in warnings],
            "local_goal_complete": local_goal_complete,
            "aws_deployable_ready": aws_deployable_ready,
            "aws_deployment_validated": False,
            "real_money_ready": False,
        },
    }

    audit_path = V2_ROOT / "audits" / f"{args.run_id}_audit.json"
    report_path = V2_ROOT / "reports" / f"{args.run_id}_report.md"
    write_json(audit_path, audit)
    write_report(report_path, audit)
    print(f"Wrote {audit_path}")
    print(f"Wrote {report_path}")
    print(
        "local_goal_complete={complete} required_passed={passed}/{total} warnings={warnings}".format(
            complete=local_goal_complete,
            passed=audit["summary"]["required_passed"],
            total=audit["summary"]["required_total"],
            warnings=audit["summary"]["warning_count"],
        )
    )
    return 0 if local_goal_complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
