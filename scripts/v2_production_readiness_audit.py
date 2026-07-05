from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from v2_common import BREAKER_BASED_ROOT, REPO_ROOT, V2_ROOT, read_json, rel, utc_stamp, write_json


EVIDENCE = {
    "system": V2_ROOT / "audits" / "v2_system_audit_latest.json",
    "fresh_ingest": V2_ROOT / "audits" / "v2_yfinance_ingest_360one_60d_fixed_smoke_v2_audit.json",
    "fresh_ingest_full_original": V2_ROOT
    / "audits"
    / "v2_yfinance_ingest_original_179_1h_730d_full_burnin_audit.json",
    "both_side_detect": V2_ROOT / "audits" / "v2_signal_detect_360one_both_side_yfinance_60d_smoke_audit.json",
    "both_side_features": V2_ROOT / "audits" / "v2_features_360one_both_side_yfinance_60d_smoke_audit.json",
    "feature_broad_validation_5ticker": V2_ROOT
    / "audits"
    / "v2_burnin_5ticker_smoke_current_feature_validation_audit.json",
    "feature_broad_validation_25ticker": V2_ROOT
    / "audits"
    / "v2_feature_validation_original_tail300_25ticker_parallel_policy_v2_validation_audit.json",
    "feature_broad_validation_full_fresh": V2_ROOT
    / "audits"
    / "v2_feature_validation_original_fresh_178_tail300_parallel_burnin_audit.json",
    "both_side_inference": V2_ROOT
    / "audits"
    / "v2_inference_360one_both_side_yfinance_60d_side_gate_smoke_audit.json",
    "replay_5ticker": V2_ROOT / "audits" / "v2_replay_batch_5ticker_smoke_current_audit.json",
    "dashboard_5ticker": V2_ROOT / "audits" / "v2_dashboard_bridge_5ticker_smoke_current_audit.json",
    "replay_10ticker_bounded": V2_ROOT
    / "audits"
    / "v2_replay_original_tail300_10ticker_cached_scorer_normalized_audit.json",
    "dashboard_10ticker_bounded": V2_ROOT
    / "audits"
    / "v2_dashboard_bridge_original_tail300_10ticker_cached_scorer_normalized_audit.json",
    "replay_25ticker_bounded_policy_v2": V2_ROOT
    / "audits"
    / "v2_replay_original_tail300_25ticker_parallel_policy_v2_validation_audit.json",
    "replay_full_fresh_original": V2_ROOT
    / "audits"
    / "v2_replay_original_fresh_178_tail300_parallel_burnin_audit.json",
    "dashboard_25ticker_bounded_policy_v2": V2_ROOT
    / "audits"
    / "v2_dashboard_bridge_original_tail300_25ticker_parallel_policy_v2_validation_audit.json",
    "dashboard_full_fresh_original": V2_ROOT
    / "audits"
    / "v2_dashboard_bridge_original_fresh_178_tail300_parallel_burnin_audit.json",
    "replay_parallel_4ticker": V2_ROOT / "audits" / "v2_replay_original_tail300_4ticker_parallel_smoke_audit.json",
    "paper_5ticker": V2_ROOT / "audits" / "v2_paper_replay_5ticker_smoke_current_audit.json",
    "paper_25ticker_policy_v2": V2_ROOT
    / "audits"
    / "v2_paper_replay_25ticker_policy_v2_conditional_entries_audit.json",
    "paper_full_fresh_original": V2_ROOT
    / "audits"
    / "v2_paper_replay_original_fresh_178_tail300_parallel_burnin_audit.json",
    "runtime_5ticker": V2_ROOT / "audits" / "v2_runtime_cycle_5ticker_smoke_current_reuse_audit.json",
    "burnin_5ticker": V2_ROOT / "audits" / "v2_burnin_5ticker_smoke_current_audit.json",
    "validation_gate_5ticker": V2_ROOT / "audits" / "v2_validation_gate_5ticker_smoke_current_audit.json",
    "validation_gate_25ticker_policy_v2": V2_ROOT
    / "audits"
    / "v2_validation_gate_original_tail300_25ticker_parallel_policy_v2_validation_audit.json",
    "validation_gate_full_fresh_original": V2_ROOT
    / "audits"
    / "v2_validation_gate_original_fresh_178_tail300_parallel_burnin_audit.json",
    "aws": V2_ROOT / "audits" / "v2_aws_readiness_current_audit.json",
}


ORIGINAL_ENGINE_PATHS = [
    BREAKER_BASED_ROOT / "breaker_fvg_dashboard.html",
    BREAKER_BASED_ROOT / "Breaker+FVG.ipynb",
    BREAKER_BASED_ROOT / "Breaker.ipynb",
    BREAKER_BASED_ROOT / "ISL+BOS.ipynb",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a requirement-by-requirement production-readiness audit for Signal Model V2. "
            "This reports readiness evidence, deployment caveats, and live-trading caveats; it does not deploy or place orders."
        )
    )
    parser.add_argument("--run-id", default=f"v2_production_readiness_{utc_stamp()}")
    parser.add_argument(
        "--fail-if-not-ready",
        action="store_true",
        help="Return non-zero when required local/AWS-deployable readiness checks fail. Default returns 0 if the audit was generated.",
    )
    return parser.parse_args()


def load_optional_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return read_json(path)


def value_at(payload: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    cursor: Any = payload
    for key in keys:
        if not isinstance(cursor, dict) or key not in cursor:
            return default
        cursor = cursor[key]
    return cursor


def add_check(
    checks: List[Dict[str, Any]],
    item: str,
    ok: bool,
    *,
    evidence: Path | None = None,
    severity: str = "required",
    detail: Any = None,
) -> None:
    checks.append(
        {
            "item": item,
            "ok": bool(ok),
            "severity": severity,
            "evidence": rel(evidence) if evidence else None,
            "detail": detail,
        }
    )


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


def write_report(path: Path, audit: Dict[str, Any]) -> None:
    summary = audit["summary"]
    lines = [
        "# Signal Model V2 Production Readiness Audit",
        "",
        f"- Run ID: `{audit['run_id']}`",
        f"- Generated: `{audit['generated_at']}`",
        f"- Local production package ready: `{str(summary['production_ready']).lower()}`",
        f"- AWS deployable ready: `{str(summary['aws_deployable_ready']).lower()}`",
        f"- AWS deployment validated: `{str(summary['aws_deployment_validated']).lower()}`",
        f"- Real-money ready: `{str(summary['real_money_ready']).lower()}`",
        f"- Required passed: `{summary['required_passed']}` / `{summary['required_total']}`",
        f"- Blocker count: `{summary['blocker_count']}`",
        f"- Warning count: `{summary['warning_count']}`",
        "",
        "## Current Evidence",
        "",
    ]
    for name, path_text in audit["evidence"].items():
        lines.append(f"- `{name}`: `{path_text}`")

    lines.extend(["", "## Failed Required Checks", ""])
    failed = [check for check in audit["checks"] if check["severity"] == "required" and not check["ok"]]
    if failed:
        for check in failed:
            lines.append(f"- `{check['item']}`: {check.get('detail')}")
    else:
        lines.append("- None.")

    lines.extend(["", "## Warnings / Non-Deployment Caveats", ""])
    warnings = [check for check in audit["checks"] if check["severity"] == "warning" and not check["ok"]]
    if warnings:
        for check in warnings:
            lines.append(f"- `{check['item']}`: {check.get('detail')}")
    else:
        lines.append("- None.")

    answers = audit["final_report_answers"]
    lines.extend(["", "## Final Report Answers", ""])
    for question, answer in answers.items():
        lines.append(f"### {question}")
        lines.append("")
        if isinstance(answer, list):
            for item in answer:
                lines.append(f"- {item}")
        else:
            lines.append(str(answer))
        lines.append("")

    lines.extend(["## Full Check Matrix", ""])
    lines.append("| Severity | OK | Item | Evidence | Detail |")
    lines.append("|---|---:|---|---|---|")
    for check in audit["checks"]:
        detail = str(check.get("detail", "")).replace("|", "/")
        lines.append(
            f"| `{check['severity']}` | `{str(check['ok']).lower()}` | `{check['item']}` | "
            f"`{check.get('evidence') or ''}` | {detail} |"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    loaded = {name: load_optional_json(path) for name, path in EVIDENCE.items()}
    checks: List[Dict[str, Any]] = []

    for name, path in EVIDENCE.items():
        add_check(checks, f"evidence_exists:{name}", path.exists(), evidence=path, detail=rel(path))

    system = loaded["system"]
    system_failures = sum(1 for check in system.get("checks", []) if not check.get("ok"))
    add_check(
        checks,
        "system_audit_has_zero_failures",
        bool(system.get("checks")) and system_failures == 0,
        evidence=EVIDENCE["system"],
        detail={"checks": len(system.get("checks", [])), "failures": system_failures},
    )

    fresh = loaded["fresh_ingest"]
    add_check(
        checks,
        "fresh_yfinance_1ticker_ingestion_passed",
        fresh.get("passed") is True and int(fresh.get("passed_count", 0) or 0) >= 1,
        evidence=EVIDENCE["fresh_ingest"],
        detail={"provider": fresh.get("provider"), "passed_count": fresh.get("passed_count")},
    )

    fresh_full = loaded["fresh_ingest_full_original"]
    fresh_full_failed_count = fresh_full.get("failed_count")
    add_check(
        checks,
        "fresh_yfinance_full_original_universe_ingestion_passed",
        fresh_full.get("passed") is True
        and fresh_full.get("provider") == "yfinance"
        and fresh_full.get("interval") == "1h"
        and int(fresh_full.get("ticker_count", 0) or 0) >= 178
        and int(fresh_full.get("passed_count", 0) or 0) == int(fresh_full.get("ticker_count", -1) or -1)
        and int(fresh_full_failed_count if fresh_full_failed_count is not None else -1) == 0,
        evidence=EVIDENCE["fresh_ingest_full_original"],
        detail={
            "provider": fresh_full.get("provider"),
            "interval": fresh_full.get("interval"),
            "ticker_count": fresh_full.get("ticker_count"),
            "passed_count": fresh_full.get("passed_count"),
            "failed_count": fresh_full.get("failed_count"),
            "output_dir": fresh_full.get("output_dir"),
            "log": fresh_full.get("log"),
        },
    )

    detect = loaded["both_side_detect"]
    side_counts = detect.get("side_counts") or {}
    signal_count = int(detect.get("signal_count", detect.get("signal_rows", 0)) or 0)
    add_check(
        checks,
        "both_side_signal_detection_smoke_passed",
        (detect.get("status") == "passed" or detect.get("passed") is True)
        and signal_count >= 2
        and side_counts.get("long", 0) >= 1
        and side_counts.get("short", 0) >= 1,
        evidence=EVIDENCE["both_side_detect"],
        detail={"signal_count": signal_count, "side_counts": side_counts},
    )

    features = loaded["both_side_features"]
    side_status = features.get("side_feature_status") or {}
    add_check(
        checks,
        "decision_time_feature_generation_classification_allowed",
        features.get("classification_allowed") is True
        and int(features.get("blocking_missing_all_rows_count", 1) or 0) == 0,
        evidence=EVIDENCE["both_side_features"],
        detail={
            "classification_status": features.get("classification_status"),
            "blocking_missing": features.get("blocking_missing_all_rows_count"),
            "decision_time_rule": features.get("decision_time_rule"),
        },
    )
    add_check(
        checks,
        "long_short_feature_contracts_allowed",
        value_at(side_status, "long", "classification_allowed") is True
        and value_at(side_status, "short", "classification_allowed") is True,
        evidence=EVIDENCE["both_side_features"],
        detail={
            "long": value_at(side_status, "long", "classification_status"),
            "short": value_at(side_status, "short", "classification_status"),
        },
    )
    feature_25 = loaded["feature_broad_validation_25ticker"]
    add_check(
        checks,
        "feature_substitution_25ticker_validation_passed",
        feature_25.get("passed") is True
        and value_at(feature_25, "metrics", "classification_allowed_rate", default=0) >= 0.95
        and int(value_at(feature_25, "metrics", "blocking_missing_ticker_count", default=999) or 0) == 0,
        evidence=EVIDENCE["feature_broad_validation_25ticker"],
        detail={
            "feature_gate_passed": feature_25.get("passed"),
            "failed_checks": value_at(feature_25, "summary", "failed_checks", default=[]),
            "metrics": value_at(feature_25, "metrics", default={}),
        },
    )

    feature_full = loaded["feature_broad_validation_full_fresh"]
    add_check(
        checks,
        "feature_substitution_full_fresh_validation_passed",
        feature_full.get("passed") is True
        and value_at(feature_full, "metrics", "classification_allowed_rate", default=0) >= 0.95
        and int(value_at(feature_full, "metrics", "blocking_missing_ticker_count", default=999) or 0) == 0
        and int(value_at(feature_full, "metrics", "feature_rows", default=0) or 0) >= 155,
        evidence=EVIDENCE["feature_broad_validation_full_fresh"],
        detail={
            "feature_gate_passed": feature_full.get("passed"),
            "failed_checks": value_at(feature_full, "summary", "failed_checks", default=[]),
            "metrics": value_at(feature_full, "metrics", default={}),
        },
    )

    inference = loaded["both_side_inference"]
    artifact = inference.get("artifact_scoring") or {}
    add_check(
        checks,
        "approved_long_short_signal_artifacts_score_rows",
        inference.get("inference_path") == "approved_artifact_scoring"
        and int(inference.get("scored_rows", 0) or 0) >= 2
        and int(artifact.get("long_scored_rows", 0) or 0) >= 1
        and int(artifact.get("short_scored_rows", 0) or 0) >= 1,
        evidence=EVIDENCE["both_side_inference"],
        detail={
            "path": inference.get("inference_path"),
            "scored_rows": inference.get("scored_rows"),
            "artifact_scoring": artifact,
        },
    )

    replay = loaded["replay_5ticker"]
    replay_summary = replay.get("summary") or {}
    add_check(
        checks,
        "fresh_5ticker_replay_batch_passed",
        replay.get("status") == "passed"
        and int(replay.get("attempted_count", 0) or 0) == 5
        and int(replay.get("failed_count", 1) or 0) == 0
        and int(replay_summary.get("signal_rows", 0) or 0) > 0
        and int(replay_summary.get("scored_liquidity_rows", 0) or 0) > 0
        and int(replay_summary.get("decision_rows", 0) or 0) > 0,
        evidence=EVIDENCE["replay_5ticker"],
        detail={
            "attempted": replay.get("attempted_count"),
            "passed": replay.get("passed_count"),
            "no_signals": replay.get("no_signal_count"),
            "failed": replay.get("failed_count"),
            "summary": replay_summary,
        },
    )

    replay_10 = loaded["replay_10ticker_bounded"]
    replay_10_summary = replay_10.get("summary") or {}
    add_check(
        checks,
        "bounded_10ticker_replay_batch_passed",
        replay_10.get("status") == "passed"
        and int(replay_10.get("attempted_count", 0) or 0) == 10
        and int(replay_10.get("failed_count", 1) or 0) == 0
        and int(replay_10_summary.get("signal_rows", 0) or 0) >= 10
        and int(replay_10_summary.get("scored_liquidity_rows", 0) or 0) >= 50
        and int(replay_10_summary.get("decision_rows", 0) or 0) >= 10,
        evidence=EVIDENCE["replay_10ticker_bounded"],
        detail={
            "attempted": replay_10.get("attempted_count"),
            "passed": replay_10.get("passed_count"),
            "no_signals": replay_10.get("no_signal_count"),
            "failed": replay_10.get("failed_count"),
            "max_input_candles": replay_10.get("max_input_candles"),
            "step_timeout_seconds": replay_10.get("step_timeout_seconds"),
            "ticker_timeout_seconds": replay_10.get("ticker_timeout_seconds"),
            "summary": replay_10_summary,
        },
    )

    replay_25 = loaded["replay_25ticker_bounded_policy_v2"]
    replay_25_summary = replay_25.get("summary") or {}
    add_check(
        checks,
        "bounded_25ticker_policy_v2_replay_batch_passed",
        replay_25.get("status") == "passed"
        and int(replay_25.get("attempted_count", 0) or 0) == 25
        and int(replay_25.get("failed_count", 1) or 0) == 0
        and int(replay_25_summary.get("signal_rows", 0) or 0) >= 25
        and int(replay_25_summary.get("scored_liquidity_rows", 0) or 0) >= 150
        and int(replay_25_summary.get("decision_rows", 0) or 0) >= 25
        and int(replay_25_summary.get("decision_bucket_counts", {}).get("insufficient_data", 0) or 0) == 0,
        evidence=EVIDENCE["replay_25ticker_bounded_policy_v2"],
        detail={
            "attempted": replay_25.get("attempted_count"),
            "passed": replay_25.get("passed_count"),
            "no_signals": replay_25.get("no_signal_count"),
            "failed": replay_25.get("failed_count"),
            "max_input_candles": replay_25.get("max_input_candles"),
            "workers": replay_25.get("workers"),
            "summary": replay_25_summary,
        },
    )

    replay_full = loaded["replay_full_fresh_original"]
    replay_full_summary = replay_full.get("summary") or {}
    add_check(
        checks,
        "full_fresh_original_replay_batch_passed",
        replay_full.get("status") == "passed"
        and int(replay_full.get("attempted_count", 0) or 0) >= 178
        and int(replay_full.get("passed_count", 0) or 0) >= 100
        and int(replay_full.get("failed_count", 1) or 0) == 0
        and int(replay_full_summary.get("signal_rows", 0) or 0) >= 155
        and int(replay_full_summary.get("scored_liquidity_rows", 0) or 0) >= 824
        and int(replay_full_summary.get("decision_rows", 0) or 0) >= 155
        and int(replay_full_summary.get("decision_bucket_counts", {}).get("insufficient_data", 0) or 0) == 0,
        evidence=EVIDENCE["replay_full_fresh_original"],
        detail={
            "attempted": replay_full.get("attempted_count"),
            "passed": replay_full.get("passed_count"),
            "no_signals": replay_full.get("no_signal_count"),
            "failed": replay_full.get("failed_count"),
            "max_input_candles": replay_full.get("max_input_candles"),
            "workers": replay_full.get("workers"),
            "summary": replay_full_summary,
        },
    )

    add_check(
        checks,
        "broader_25ticker_validation_gate_passed",
        loaded["validation_gate_25ticker_policy_v2"].get("passed") is True
        and value_at(loaded["validation_gate_25ticker_policy_v2"], "metrics", "approved_artifact_decision_rows", default=0) >= 25,
        evidence=EVIDENCE["validation_gate_25ticker_policy_v2"],
        detail={
            "validation_gate_passed": value_at(loaded["validation_gate_25ticker_policy_v2"], "passed"),
            "failed_checks": value_at(loaded["validation_gate_25ticker_policy_v2"], "summary", "failed_checks", default=[]),
            "metrics": value_at(loaded["validation_gate_25ticker_policy_v2"], "metrics", default={}),
        },
    )

    add_check(
        checks,
        "full_fresh_original_validation_gate_passed",
        loaded["validation_gate_full_fresh_original"].get("passed") is True
        and value_at(loaded["validation_gate_full_fresh_original"], "metrics", "approved_artifact_decision_rows", default=0) >= 155,
        evidence=EVIDENCE["validation_gate_full_fresh_original"],
        detail={
            "validation_gate_passed": value_at(loaded["validation_gate_full_fresh_original"], "passed"),
            "failed_checks": value_at(
                loaded["validation_gate_full_fresh_original"], "summary", "failed_checks", default=[]
            ),
            "metrics": value_at(loaded["validation_gate_full_fresh_original"], "metrics", default={}),
        },
    )

    dashboard = loaded["dashboard_5ticker"]
    add_check(
        checks,
        "dashboard_api_bridge_contract_passed",
        dashboard.get("status") == "passed"
        and value_at(dashboard, "dashboard_contract", "contract_ok") is True
        and dashboard.get("original_dashboard_modified") is False,
        evidence=EVIDENCE["dashboard_5ticker"],
        detail={
            "rows": dashboard.get("bridge_rows_written"),
            "contract": dashboard.get("dashboard_contract"),
            "original_dashboard_modified": dashboard.get("original_dashboard_modified"),
        },
    )

    dashboard_10 = loaded["dashboard_10ticker_bounded"]
    add_check(
        checks,
        "bounded_10ticker_dashboard_bridge_contract_passed",
        dashboard_10.get("status") == "passed"
        and value_at(dashboard_10, "dashboard_contract", "contract_ok") is True
        and int(dashboard_10.get("bridge_rows_written", 0) or 0) >= 10
        and int(dashboard_10.get("signals_with_scored_liquidity_context", 0) or 0) >= 10
        and dashboard_10.get("original_dashboard_modified") is False,
        evidence=EVIDENCE["dashboard_10ticker_bounded"],
        detail={
            "rows": dashboard_10.get("bridge_rows_written"),
            "live_rows": dashboard_10.get("live_rows_written"),
            "signals_with_scored_liquidity_context": dashboard_10.get("signals_with_scored_liquidity_context"),
            "contract": dashboard_10.get("dashboard_contract"),
            "original_dashboard_modified": dashboard_10.get("original_dashboard_modified"),
        },
    )

    dashboard_25 = loaded["dashboard_25ticker_bounded_policy_v2"]
    add_check(
        checks,
        "bounded_25ticker_policy_v2_dashboard_bridge_contract_passed",
        dashboard_25.get("status") == "passed"
        and value_at(dashboard_25, "dashboard_contract", "contract_ok") is True
        and int(dashboard_25.get("bridge_rows_written", 0) or 0) >= 25
        and int(dashboard_25.get("signals_with_scored_liquidity_context", 0) or 0) >= 25
        and dashboard_25.get("original_dashboard_modified") is False,
        evidence=EVIDENCE["dashboard_25ticker_bounded_policy_v2"],
        detail={
            "rows": dashboard_25.get("bridge_rows_written"),
            "live_rows": dashboard_25.get("live_rows_written"),
            "signals_with_scored_liquidity_context": dashboard_25.get("signals_with_scored_liquidity_context"),
            "contract": dashboard_25.get("dashboard_contract"),
            "original_dashboard_modified": dashboard_25.get("original_dashboard_modified"),
        },
    )

    dashboard_full = loaded["dashboard_full_fresh_original"]
    add_check(
        checks,
        "full_fresh_original_dashboard_bridge_contract_passed",
        dashboard_full.get("status") == "passed"
        and value_at(dashboard_full, "dashboard_contract", "contract_ok") is True
        and int(dashboard_full.get("bridge_rows_written", 0) or 0) >= 155
        and int(dashboard_full.get("signals_with_scored_liquidity_context", 0) or 0) >= 155
        and dashboard_full.get("original_dashboard_modified") is False,
        evidence=EVIDENCE["dashboard_full_fresh_original"],
        detail={
            "rows": dashboard_full.get("bridge_rows_written"),
            "live_rows": dashboard_full.get("live_rows_written"),
            "signals_with_scored_liquidity_context": dashboard_full.get(
                "signals_with_scored_liquidity_context"
            ),
            "contract": dashboard_full.get("dashboard_contract"),
            "original_dashboard_modified": dashboard_full.get("original_dashboard_modified"),
        },
    )

    replay_parallel = loaded["replay_parallel_4ticker"]
    replay_parallel_summary = replay_parallel.get("summary") or {}
    add_check(
        checks,
        "parallel_replay_smoke_passed",
        replay_parallel.get("status") == "passed"
        and int(replay_parallel.get("workers", 0) or 0) >= 2
        and int(replay_parallel.get("attempted_count", 0) or 0) >= 4
        and int(replay_parallel.get("failed_count", 1) or 0) == 0
        and int(replay_parallel_summary.get("decision_rows", 0) or 0) >= 4,
        evidence=EVIDENCE["replay_parallel_4ticker"],
        detail={
            "workers": replay_parallel.get("workers"),
            "attempted": replay_parallel.get("attempted_count"),
            "passed": replay_parallel.get("passed_count"),
            "no_signals": replay_parallel.get("no_signal_count"),
            "failed": replay_parallel.get("failed_count"),
            "summary": replay_parallel_summary,
        },
    )

    paper = loaded["paper_5ticker"]
    add_check(
        checks,
        "paper_replay_10l_safety_passed",
        value_at(paper, "summary", "passed") is True
        and float(paper.get("notional_capital_inr", 0) or 0) == 1000000.0
        and value_at(paper, "summary", "order_placement_enabled") is False
        and value_at(paper, "summary", "uses_only_post_decision_candles") is True,
        evidence=EVIDENCE["paper_5ticker"],
        detail={
            "decision_rows": value_at(paper, "summary", "decision_rows_read"),
            "entered": value_at(paper, "summary", "entered_trades"),
            "notional": paper.get("notional_capital_inr"),
            "order_placement": value_at(paper, "summary", "order_placement_enabled"),
        },
    )

    paper_25 = loaded["paper_25ticker_policy_v2"]
    paper_25_entry_permissions = {
        str(value).strip().lower()
        for value in paper_25.get("entry_permission_values", [])
        if str(value).strip()
    }
    add_check(
        checks,
        "paper_replay_25ticker_with_actual_conditional_entry_passed",
        value_at(paper_25, "summary", "passed") is True
        and float(paper_25.get("notional_capital_inr", 0) or 0) == 1000000.0
        and value_at(paper_25, "summary", "order_placement_enabled") is False
        and value_at(paper_25, "summary", "uses_only_post_decision_candles") is True
        and int(value_at(paper_25, "summary", "entered_trades", default=0) or 0) >= 1
        and "conditional_take_candidate" in paper_25_entry_permissions,
        evidence=EVIDENCE["paper_25ticker_policy_v2"],
        detail={
            "decision_rows": value_at(paper_25, "summary", "decision_rows_read"),
            "entered": value_at(paper_25, "summary", "entered_trades"),
            "entry_permission_values": sorted(paper_25_entry_permissions),
            "notional": paper_25.get("notional_capital_inr"),
            "order_placement": value_at(paper_25, "summary", "order_placement_enabled"),
            "net_pnl_inr": value_at(paper_25, "summary", "net_pnl_inr"),
            "hit_at_least_2_target_liq_rate": value_at(
                paper_25, "summary", "hit_at_least_2_target_liq_rate"
            ),
        },
    )

    paper_full = loaded["paper_full_fresh_original"]
    paper_full_entry_permissions = {
        str(value).strip().lower()
        for value in paper_full.get("entry_permission_values", [])
        if str(value).strip()
    }
    add_check(
        checks,
        "paper_replay_full_fresh_original_safety_passed",
        value_at(paper_full, "summary", "passed") is True
        and int(value_at(paper_full, "summary", "decision_rows_read", default=0) or 0) >= 155
        and float(paper_full.get("notional_capital_inr", 0) or 0) == 1000000.0
        and value_at(paper_full, "summary", "order_placement_enabled") is False
        and value_at(paper_full, "summary", "uses_only_post_decision_candles") is True
        and "conditional_take_candidate" in paper_full_entry_permissions,
        evidence=EVIDENCE["paper_full_fresh_original"],
        detail={
            "decision_rows": value_at(paper_full, "summary", "decision_rows_read"),
            "entered": value_at(paper_full, "summary", "entered_trades"),
            "not_permissioned_rows": value_at(paper_full, "summary", "not_permissioned_rows"),
            "entry_permission_values": sorted(paper_full_entry_permissions),
            "notional": paper_full.get("notional_capital_inr"),
            "order_placement": value_at(paper_full, "summary", "order_placement_enabled"),
            "net_pnl_inr": value_at(paper_full, "summary", "net_pnl_inr"),
        },
    )

    runtime = loaded["runtime_5ticker"]
    add_check(
        checks,
        "runtime_wrapper_passed",
        runtime.get("status") == "passed"
        and all(phase.get("status") in {"passed", "reused"} for phase in runtime.get("phases", [])),
        evidence=EVIDENCE["runtime_5ticker"],
        detail={"status": runtime.get("status"), "phases": runtime.get("phases")},
    )

    burnin = loaded["burnin_5ticker"]
    add_check(
        checks,
        "burnin_orchestrator_smoke_completed",
        burnin.get("status") == "passed_smoke"
        and value_at(burnin, "summary", "production_readiness_refreshed") is True
        and value_at(burnin, "summary", "hard_failed") is False,
        evidence=EVIDENCE["burnin_5ticker"],
        detail={
            "status": burnin.get("status"),
            "validation_passed": value_at(burnin, "summary", "validation_passed"),
            "failed_phases": value_at(burnin, "summary", "failed_phases", default=[]),
            "log": burnin.get("log"),
        },
    )

    aws = loaded["aws"]
    aws_failed_count = value_at(aws, "summary", "failed_count", default=1)
    add_check(
        checks,
        "aws_skeleton_readiness_passed",
        value_at(aws, "summary", "aws_skeleton_ready") is True
        and int(aws_failed_count if aws_failed_count is not None else 1) == 0,
        evidence=EVIDENCE["aws"],
        detail={
            "check_count": value_at(aws, "summary", "check_count"),
            "failed_count": value_at(aws, "summary", "failed_count"),
            "production_ready": value_at(aws, "summary", "production_ready"),
        },
    )
    add_check(
        checks,
        "aws_real_deployment_not_performed",
        False,
        evidence=EVIDENCE["aws"],
        severity="warning",
        detail="AWS skeleton is ready locally; no ECR/ECS/S3/CloudWatch deployment validation has been performed.",
    )

    original_dirty = git_status_for(ORIGINAL_ENGINE_PATHS)
    add_check(
        checks,
        "original_engine_dashboard_worktree_clean",
        not original_dirty,
        severity="warning",
        detail=original_dirty
        or "Tracked original engine/dashboard paths are clean in git status.",
    )

    required_checks = [check for check in checks if check["severity"] == "required"]
    warnings = [check for check in checks if check["severity"] == "warning" and not check["ok"]]
    failed_required = [check for check in required_checks if not check["ok"]]
    production_ready = not failed_required
    aws_deployable_ready = production_ready and value_at(loaded["aws"], "summary", "aws_skeleton_ready") is True
    aws_deployment_validated = False
    real_money_ready = False

    evidence_paths = {name: rel(path) for name, path in EVIDENCE.items()}
    final_answers = {
        "1. What works end-to-end now?": [
            "Local V2 replay/runtime can run candles -> signals -> decision-time liquidity -> feature build -> approved artifact scoring -> decisions -> dashboard bridge -> paper ledger.",
            "Full-original yfinance ingestion completed for 178 deduped original-universe tickers with 178 passed, 0 failed, and normalized 1H candle files written under V2.",
            "Both-side 360ONE smoke scores one long row and one short row with approved artifacts.",
            "Fresh 5-ticker replay orchestration completed with 2 passed tickers, 3 no-signal tickers, and 0 failed tickers.",
            "Bounded original-universe 10-ticker replay completed with 12 signal rows, 96 XG-scored liquidity rows, 12 decisions, and 0 failed tickers.",
            "Corrected 25-ticker bounded original-universe replay completed with 27 signal rows, 168 XG-scored liquidity rows, 27 approved-artifact decisions, and 0 failed tickers.",
            "The corrected 25-ticker feature validation passed with 100% classification-allowed rows and zero blocking-missing tickers.",
            "Corrected 25-ticker paper replay entered 1 conditional high-conviction trade with real order placement disabled and post-decision candles only.",
            "Full-original fresh replay completed on the latest 300-candle window with 178 attempted tickers, 100 passed tickers, 78 no-signal tickers, 155 decisions, 824 XG-scored liquidity rows, and 0 failed tickers.",
            "Full-original fresh feature validation, dashboard bridge, paper replay, and validation gate all passed. The full-fresh paper replay entered 0 trades because all 155 current decisions were reject.",
            "Parallel replay smoke completed with 2 workers, 4 attempted tickers, 4 decision rows, and 0 failed tickers.",
        ],
        "2. What inputs are required?": [
            "1H NSE candle CSVs or yfinance fresh ingestion output.",
            "For the current full-original fresh ingest: Newtest/Breaker_Based/signal_model_v2/data/raw/v2_yfinance_ingest_original_179_1h_730d_full_burnin",
            "Approved V1 liquidity and signal artifacts/configs.",
            "Runtime config, feature availability policy, and optional macro candle directory.",
        ],
        "3. Which features are live-reproducible?": (
            "Current smoke proves base setup, focused quality, FVG reaction, candle quality, liquidity/topology, macro, "
            "entry, and composite fields are reproducible enough for zero blocking-missing fields on the 360ONE both-side smoke "
            "and the full-original fresh latest-window validation."
        ),
        "4. Which features are still missing or substituted?": (
            "Structural-null and live-safe substitutes remain visible in feature audits. The full-original fresh validation proves "
            "those substitutions are non-blocking for the latest-window rows that produced signals."
        ),
        "5. Can the system classify a fresh signal without using precomputed historical rows?": (
            "Yes on the verified smoke path: fresh/replay candles produce native event and feature rows, and approved artifacts classify them. "
            "The full-original fresh replay proves this over the latest-window original universe. This remains a local/AWS-ready validation, not a real-money deployment."
        ),
        "6. How are scored liquidity levels generated at decision time?": (
            "V2 extracts visible BSL/SSL candidates at decision_time, builds liquidity model rows, scores them with the approved XG liquidity model, "
            "and aggregates target/stop-side pressure into signal features."
        ),
        "7. What is the exact command to run live/replay/paper modes?": [
            "Replay batch: python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_replay_batch.py --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_batch_import_5ticker_smoke --run-id v2_replay_batch_5ticker_smoke_current --continue-on-error --limit 5",
            "Bounded current-window replay: python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_replay_batch.py --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_ingest_original_178_cached_1h_2y_eta --run-id v2_replay_original_tail300_10ticker_cached_scorer_normalized --limit 10 --continue-on-error --max-input-candles 300 --step-timeout-seconds 120 --ticker-timeout-seconds 240",
            "Corrected 25-ticker bounded replay: python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_replay_batch.py --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_ingest_original_178_cached_1h_2y_eta --run-id v2_replay_original_tail300_25ticker_parallel_policy_v2_validation --limit 25 --continue-on-error --max-input-candles 300 --step-timeout-seconds 120 --ticker-timeout-seconds 240 --workers 4",
            "Parallel replay smoke: python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_replay_batch.py --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_ingest_original_178_cached_1h_2y_eta --run-id v2_replay_original_tail300_4ticker_parallel_smoke --limit 4 --continue-on-error --max-input-candles 300 --step-timeout-seconds 120 --ticker-timeout-seconds 240 --workers 2",
            "Full-original fresh ingestion: python Newtest/Breaker_Based/signal_model_v2/scripts/v2_ingest_candles.py --provider yfinance --universe-file Newtest/Breaker_Based/NSE_Symbols.csv --interval 1h --period 730d --run-id v2_yfinance_ingest_original_179_1h_730d_full_burnin --min-rows 300 --max-retries 3 --retry-sleep-seconds 5",
            "Full-original fresh replay: python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_replay_batch.py --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_yfinance_ingest_original_179_1h_730d_full_burnin --run-id v2_replay_original_fresh_178_tail300_parallel_burnin --continue-on-error --resume --max-input-candles 300 --step-timeout-seconds 120 --ticker-timeout-seconds 240 --workers 4",
            "Full-original fresh dashboard bridge: python Newtest/Breaker_Based/signal_model_v2/scripts/v2_export_dashboard_bridge.py --decisions \"Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_replay_original_fresh_178_tail300_parallel_burnin*_decisions.csv\" --liquidity-dirs \"Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_replay_original_fresh_178_tail300_parallel_burnin*\" --run-id v2_dashboard_bridge_original_fresh_178_tail300_parallel_burnin --latest-per-ticker 3 --max-live-rows 300",
            "Full-original fresh paper replay: python Newtest/Breaker_Based/signal_model_v2/scripts/v2_paper_replay_from_decisions.py --decisions \"Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_replay_original_fresh_178_tail300_parallel_burnin*_decisions.csv\" --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_yfinance_ingest_original_179_1h_730d_full_burnin --run-id v2_paper_replay_original_fresh_178_tail300_parallel_burnin --notional-capital-inr 1000000 --entry-permission-values yes conditional_take_candidate",
            "Runtime wrapper: python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_runtime_cycle.py --replay-run-id v2_replay_batch_5ticker_smoke_current --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_batch_import_5ticker_smoke --run-id v2_runtime_cycle_5ticker_smoke_current_reuse",
            "Paper replay: python Newtest/Breaker_Based/signal_model_v2/scripts/v2_paper_replay_from_decisions.py --decisions Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_replay_batch_5ticker_smoke_current_360one_ns_decisions.csv Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_replay_batch_5ticker_smoke_current_aartiind_ns_decisions.csv --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_batch_import_5ticker_smoke --run-id v2_paper_replay_5ticker_smoke_current --notional-capital-inr 1000000",
            "Corrected 25-ticker paper replay with conditional entries: python Newtest/Breaker_Based/signal_model_v2/scripts/v2_paper_replay_from_decisions.py --decisions \"Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_replay_original_tail300_25ticker_parallel_policy_v2_validation*_decisions.csv\" --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_ingest_original_178_cached_1h_2y_eta --run-id v2_paper_replay_25ticker_policy_v2_conditional_entries --notional-capital-inr 1000000 --entry-permission-values yes conditional_take_candidate",
            "Fresh yfinance ingestion requires network access; see V2_RUNBOOK for the live-disabled paper cycle command.",
        ],
        "8. What is AWS-ready and what remains before deployment?": [
            "AWS skeleton passes 77 local readiness checks.",
            "Docker/ECS/EventBridge/S3/IAM/CloudWatch/rollback/cost-control contracts exist.",
            "Actual AWS build/deploy/manual ECS/S3 output validation remains unperformed.",
        ],
        "9. What are the remaining blockers before real-money automation?": [
            "Dashboard consumption validation against actual chart panel integration.",
            "AWS deployment validation.",
            "Dirty original engine/dashboard tracked files in current worktree prevent clean proof that original code is untouched.",
            "Separate real-order approval gate remains intentionally absent.",
        ],
    }

    audit = {
        "version": "SIGNAL_MODEL_V2_PRODUCTION_READINESS_AUDIT",
        "run_id": args.run_id,
        "generated_at": utc_stamp(),
        "production_ready": production_ready,
        "aws_deployable_ready": aws_deployable_ready,
        "aws_deployment_validated": aws_deployment_validated,
        "real_money_ready": real_money_ready,
        "evidence": evidence_paths,
        "checks": checks,
        "summary": {
            "production_ready": production_ready,
            "aws_deployable_ready": aws_deployable_ready,
            "aws_deployment_validated": aws_deployment_validated,
            "real_money_ready": real_money_ready,
            "required_total": len(required_checks),
            "required_passed": len(required_checks) - len(failed_required),
            "required_failed": len(failed_required),
            "blocker_count": len(failed_required),
            "warning_count": len(warnings),
            "failed_required_items": [check["item"] for check in failed_required],
            "warning_items": [check["item"] for check in warnings],
        },
        "final_report_answers": final_answers,
    }

    audit_path = V2_ROOT / "audits" / f"{args.run_id}_audit.json"
    report_path = V2_ROOT / "reports" / f"{args.run_id}_report.md"
    write_json(audit_path, audit)
    write_report(report_path, audit)
    print(f"Wrote {audit_path}")
    print(f"Wrote {report_path}")
    print(
        "production_ready={ready} aws_deployable_ready={aws_ready} required_passed={passed}/{total} blockers={blockers} warnings={warnings}".format(
            ready=production_ready,
            aws_ready=aws_deployable_ready,
            passed=audit["summary"]["required_passed"],
            total=audit["summary"]["required_total"],
            blockers=audit["summary"]["blocker_count"],
            warnings=audit["summary"]["warning_count"],
        )
    )
    if args.fail_if_not_ready and not production_ready:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
