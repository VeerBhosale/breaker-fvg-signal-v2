from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

from v2_common import V2_ROOT, read_json, rel, utc_stamp, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate whether a V2 replay/dashboard/paper run is broad enough to count as production-readiness evidence. "
            "This is an evidence gate, not a model trainer."
        )
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--replay-audit", type=Path, required=True)
    parser.add_argument("--dashboard-audit", type=Path, default=None)
    parser.add_argument("--paper-audit", type=Path, default=None)
    parser.add_argument("--min-attempted-tickers", type=int, default=25)
    parser.add_argument("--min-passed-tickers", type=int, default=10)
    parser.add_argument("--min-signal-rows", type=int, default=50)
    parser.add_argument("--min-decision-rows", type=int, default=50)
    parser.add_argument("--min-scored-liquidity-rows", type=int, default=100)
    parser.add_argument("--min-approved-artifact-decision-rows", type=int, default=25)
    parser.add_argument("--min-entered-trades", type=int, default=5)
    parser.add_argument("--max-failed-tickers", type=int, default=0)
    return parser.parse_args()


def value_at(payload: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def add_check(
    checks: List[Dict[str, Any]],
    name: str,
    ok: bool,
    actual: Any,
    expected: Any,
    detail: Any = "",
) -> None:
    checks.append(
        {
            "name": name,
            "ok": bool(ok),
            "actual": actual,
            "expected": expected,
            "detail": detail,
        }
    )


def write_report(path: Path, audit: Dict[str, Any]) -> None:
    lines = [
        "# V2 Validation Gate Report",
        "",
        f"- Run ID: `{audit['run_id']}`",
        f"- Generated: `{audit['generated_at']}`",
        f"- Passed: `{str(audit['passed']).lower()}`",
        f"- Checks passed: `{audit['summary']['checks_passed']}/{audit['summary']['checks_total']}`",
        "",
        "## Inputs",
        "",
        f"- Replay audit: `{audit['inputs']['replay_audit']}`",
        f"- Dashboard audit: `{audit['inputs'].get('dashboard_audit') or ''}`",
        f"- Paper audit: `{audit['inputs'].get('paper_audit') or ''}`",
        "",
        "## Metrics",
        "",
    ]
    for key, value in audit["metrics"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Checks", "", "| Check | Status | Actual | Expected |", "|---|---:|---:|---|"])
    for check in audit["checks"]:
        lines.append(
            "| {name} | `{status}` | `{actual}` | `{expected}` |".format(
                name=check["name"],
                status="pass" if check["ok"] else "fail",
                actual=str(check["actual"]).replace("|", "/"),
                expected=str(check["expected"]).replace("|", "/"),
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "A failed validation gate means the V2 run may still be a valid smoke test, but it is not broad enough to use as production-readiness evidence.",
            "This gate is intentionally stricter than unit tests and smoke tests.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"v2_validation_gate_{utc_stamp()}"
    audit_path = V2_ROOT / "audits" / f"{run_id}_audit.json"
    report_path = V2_ROOT / "reports" / f"{run_id}_report.md"

    replay = read_json(args.replay_audit)
    dashboard = read_json(args.dashboard_audit) if args.dashboard_audit else {}
    paper = read_json(args.paper_audit) if args.paper_audit else {}

    ticker_rows = replay.get("tickers") or []
    approved_artifact_decision_rows = sum(
        int(row.get("decision_rows", 0) or 0)
        for row in ticker_rows
        if row.get("signal_inference_path") == "approved_artifact_scoring"
    )
    insufficient_data_rows = int(value_at(replay, "summary", "decision_bucket_counts", "insufficient_data", default=0) or 0)
    reject_rows = int(value_at(replay, "summary", "decision_bucket_counts", "reject", default=0) or 0)
    permissioned_rows = int(value_at(paper, "summary", "entered_trades", default=0) or 0)

    metrics = {
        "attempted_tickers": int(replay.get("attempted_count", 0) or 0),
        "passed_tickers": int(replay.get("passed_count", 0) or 0),
        "failed_tickers": int(replay.get("failed_count", 0) or 0),
        "no_signal_tickers": int(replay.get("no_signal_count", 0) or 0),
        "signal_rows": int(value_at(replay, "summary", "signal_rows", default=0) or 0),
        "decision_rows": int(value_at(replay, "summary", "decision_rows", default=0) or 0),
        "scored_liquidity_rows": int(value_at(replay, "summary", "scored_liquidity_rows", default=0) or 0),
        "approved_artifact_decision_rows": approved_artifact_decision_rows,
        "insufficient_data_rows": insufficient_data_rows,
        "reject_rows": reject_rows,
        "dashboard_contract_ok": bool(value_at(dashboard, "dashboard_contract", "contract_ok", default=False)),
        "dashboard_rows": int(dashboard.get("bridge_rows_written", 0) or 0) if dashboard else 0,
        "paper_entered_trades": permissioned_rows,
        "paper_order_placement_enabled": bool(value_at(paper, "summary", "order_placement_enabled", default=False)),
        "paper_uses_only_post_decision_candles": bool(value_at(paper, "summary", "uses_only_post_decision_candles", default=False)),
    }

    checks: List[Dict[str, Any]] = []
    add_check(checks, "attempted_ticker_scope", metrics["attempted_tickers"] >= args.min_attempted_tickers, metrics["attempted_tickers"], f">= {args.min_attempted_tickers}")
    add_check(checks, "passed_ticker_scope", metrics["passed_tickers"] >= args.min_passed_tickers, metrics["passed_tickers"], f">= {args.min_passed_tickers}")
    add_check(checks, "failed_ticker_limit", metrics["failed_tickers"] <= args.max_failed_tickers, metrics["failed_tickers"], f"<= {args.max_failed_tickers}")
    add_check(checks, "signal_row_scope", metrics["signal_rows"] >= args.min_signal_rows, metrics["signal_rows"], f">= {args.min_signal_rows}")
    add_check(checks, "decision_row_scope", metrics["decision_rows"] >= args.min_decision_rows, metrics["decision_rows"], f">= {args.min_decision_rows}")
    add_check(
        checks,
        "scored_liquidity_scope",
        metrics["scored_liquidity_rows"] >= args.min_scored_liquidity_rows,
        metrics["scored_liquidity_rows"],
        f">= {args.min_scored_liquidity_rows}",
    )
    add_check(
        checks,
        "approved_artifact_scoring_scope",
        metrics["approved_artifact_decision_rows"] >= args.min_approved_artifact_decision_rows,
        metrics["approved_artifact_decision_rows"],
        f">= {args.min_approved_artifact_decision_rows}",
    )
    if dashboard:
        add_check(checks, "dashboard_contract", metrics["dashboard_contract_ok"], metrics["dashboard_contract_ok"], "true")
        add_check(checks, "dashboard_row_coverage", metrics["dashboard_rows"] == metrics["decision_rows"], metrics["dashboard_rows"], f"= decision_rows {metrics['decision_rows']}")
    if paper:
        add_check(checks, "paper_replay_has_entries", metrics["paper_entered_trades"] >= args.min_entered_trades, metrics["paper_entered_trades"], f">= {args.min_entered_trades}")
        add_check(checks, "paper_replay_safe_no_orders", not metrics["paper_order_placement_enabled"], metrics["paper_order_placement_enabled"], "false")
        add_check(
            checks,
            "paper_replay_post_decision_only",
            metrics["paper_uses_only_post_decision_candles"],
            metrics["paper_uses_only_post_decision_candles"],
            "true",
        )

    passed = all(check["ok"] for check in checks)
    audit = {
        "version": "SIGNAL_MODEL_V2_VALIDATION_GATE_AUDIT",
        "run_id": run_id,
        "generated_at": utc_stamp(),
        "passed": passed,
        "production_ready": False,
        "inputs": {
            "replay_audit": rel(args.replay_audit),
            "dashboard_audit": rel(args.dashboard_audit) if args.dashboard_audit else "",
            "paper_audit": rel(args.paper_audit) if args.paper_audit else "",
        },
        "thresholds": {
            "min_attempted_tickers": args.min_attempted_tickers,
            "min_passed_tickers": args.min_passed_tickers,
            "min_signal_rows": args.min_signal_rows,
            "min_decision_rows": args.min_decision_rows,
            "min_scored_liquidity_rows": args.min_scored_liquidity_rows,
            "min_approved_artifact_decision_rows": args.min_approved_artifact_decision_rows,
            "min_entered_trades": args.min_entered_trades,
            "max_failed_tickers": args.max_failed_tickers,
        },
        "metrics": metrics,
        "checks": checks,
        "summary": {
            "checks_total": len(checks),
            "checks_passed": sum(1 for check in checks if check["ok"]),
            "checks_failed": sum(1 for check in checks if not check["ok"]),
            "failed_checks": [check["name"] for check in checks if not check["ok"]],
        },
        "report": rel(report_path),
        "interpretation": (
            "This run is broad enough for production validation evidence."
            if passed
            else "This run remains smoke evidence only; it is not broad enough for production validation."
        ),
    }
    write_json(audit_path, audit)
    write_report(report_path, audit)
    print(f"Wrote {audit_path}")
    print(f"Wrote {report_path}")
    print(
        "validation_passed={passed} checks={passed_count}/{total} failed={failed}".format(
            passed=passed,
            passed_count=audit["summary"]["checks_passed"],
            total=audit["summary"]["checks_total"],
            failed=",".join(audit["summary"]["failed_checks"]),
        )
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
