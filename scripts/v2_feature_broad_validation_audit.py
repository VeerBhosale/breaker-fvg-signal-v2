from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

from v2_common import V2_ROOT, read_json, rel, utc_stamp, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate V2 per-ticker feature audits into a broad feature-production validation gate. "
            "This proves whether live-safe feature generation is complete enough across a replay batch."
        )
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--replay-audit", type=Path, required=True)
    parser.add_argument("--min-feature-audits", type=int, default=25)
    parser.add_argument("--min-feature-rows", type=int, default=50)
    parser.add_argument("--min-classification-allowed-rate", type=float, default=0.95)
    parser.add_argument("--max-blocking-missing-tickers", type=int, default=0)
    parser.add_argument("--max-missing-all-rows-pct", type=float, default=0.25)
    return parser.parse_args()


def value_at(payload: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    cursor: Any = payload
    for key in keys:
        if not isinstance(cursor, dict):
            return default
        cursor = cursor.get(key)
    return default if cursor is None else cursor


def add_check(checks: List[Dict[str, Any]], name: str, ok: bool, actual: Any, expected: Any) -> None:
    checks.append({"name": name, "ok": bool(ok), "actual": actual, "expected": expected})


def feature_audit_path_for_ticker_run(ticker_run_id: str) -> Path:
    return V2_ROOT / "audits" / f"{ticker_run_id}_features_audit.json"


def write_report(path: Path, audit: Dict[str, Any]) -> None:
    lines = [
        "# V2 Feature Broad Validation Report",
        "",
        f"- Run ID: `{audit['run_id']}`",
        f"- Generated: `{audit['generated_at']}`",
        f"- Passed: `{str(audit['passed']).lower()}`",
        f"- Checks passed: `{audit['summary']['checks_passed']}/{audit['summary']['checks_total']}`",
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
    lines.extend(["", "## Per Ticker", ""])
    lines.append("| Ticker | Feature Rows | Allowed | Blocking Missing | Missing All Rows | Feature Audit |")
    lines.append("|---|---:|---:|---:|---:|---|")
    for row in audit["tickers"]:
        lines.append(
            "| {ticker} | {rows} | `{allowed}` | {blocking} | {missing} | `{audit}` |".format(
                ticker=row.get("ticker"),
                rows=row.get("feature_rows", 0),
                allowed=str(row.get("classification_allowed")).lower(),
                blocking=row.get("blocking_missing_all_rows_count", 0),
                missing=row.get("missing_all_rows_count", 0),
                audit=row.get("feature_audit", ""),
            )
        )
    lines.extend(["", "## Blocking Feature Samples", ""])
    blocking = audit.get("blocking_feature_sample_counts") or {}
    if blocking:
        for name, count in list(blocking.items())[:50]:
            lines.append(f"- `{name}`: `{count}`")
    else:
        lines.append("- No blocking feature samples.")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "A failed gate means the current replay evidence is not broad or complete enough to retire the feature-substitution production blocker.",
            "Structural-null missing features may be acceptable only when the side-specific audit still allows approved artifact scoring.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"v2_feature_broad_validation_{utc_stamp()}"
    audit_path = V2_ROOT / "audits" / f"{run_id}_audit.json"
    report_path = V2_ROOT / "reports" / f"{run_id}_report.md"
    replay = read_json(args.replay_audit)

    ticker_rows: List[Dict[str, Any]] = []
    blocking_counter: Counter[str] = Counter()
    group_totals: Dict[str, Dict[str, float]] = defaultdict(lambda: {"required": 0.0, "available_all": 0.0, "missing_all": 0.0})
    side_status_counts: Counter[str] = Counter()

    for ticker in replay.get("tickers", []):
        ticker_run_id = str(ticker.get("run_id") or "")
        if int(ticker.get("feature_rows", 0) or 0) <= 0 or not ticker_run_id:
            continue
        feature_audit_path = feature_audit_path_for_ticker_run(ticker_run_id)
        if not feature_audit_path.exists():
            ticker_rows.append(
                {
                    "ticker": ticker.get("ticker"),
                    "run_id": ticker_run_id,
                    "feature_audit": rel(feature_audit_path),
                    "feature_audit_exists": False,
                    "classification_allowed": False,
                    "feature_rows": 0,
                    "blocking_missing_all_rows_count": 999999,
                    "missing_all_rows_count": 999999,
                }
            )
            continue
        feature_audit = read_json(feature_audit_path)
        blocking_samples = list(feature_audit.get("blocking_missing_features_sample") or [])
        for feature in blocking_samples:
            blocking_counter[str(feature)] += 1
        for group in feature_audit.get("group_summary") or []:
            name = str(group.get("group") or "unknown")
            group_totals[name]["required"] += float(group.get("required_features", 0) or 0)
            group_totals[name]["available_all"] += float(group.get("available_all_rows", 0) or 0)
            group_totals[name]["missing_all"] += float(group.get("missing_all_rows", 0) or 0)
        for side, status in (feature_audit.get("side_feature_status") or {}).items():
            side_status_counts[f"{side}:{status.get('classification_status')}"] += 1
        ticker_rows.append(
            {
                "ticker": ticker.get("ticker"),
                "run_id": ticker_run_id,
                "feature_audit": rel(feature_audit_path),
                "feature_audit_exists": True,
                "classification_allowed": bool(feature_audit.get("classification_allowed")),
                "classification_status": feature_audit.get("classification_status"),
                "feature_rows": int(feature_audit.get("row_count", ticker.get("feature_rows", 0)) or 0),
                "required_feature_count": int(feature_audit.get("required_feature_count", 0) or 0),
                "blocking_missing_all_rows_count": int(feature_audit.get("blocking_missing_all_rows_count", 0) or 0),
                "missing_all_rows_count": int(feature_audit.get("missing_all_rows_count", 0) or 0),
                "structural_nullable_missing_all_rows_count": int(feature_audit.get("structural_nullable_missing_all_rows_count", 0) or 0),
            }
        )

    feature_audit_count = len(ticker_rows)
    feature_rows = sum(row["feature_rows"] for row in ticker_rows)
    allowed_count = sum(1 for row in ticker_rows if row["classification_allowed"])
    blocking_tickers = sum(1 for row in ticker_rows if int(row["blocking_missing_all_rows_count"] or 0) > 0)
    required_feature_instances = sum(int(row.get("required_feature_count", 0) or 0) for row in ticker_rows)
    missing_all_instances = sum(int(row.get("missing_all_rows_count", 0) or 0) for row in ticker_rows)
    allowed_rate = allowed_count / feature_audit_count if feature_audit_count else 0.0
    missing_all_rows_pct = missing_all_instances / required_feature_instances if required_feature_instances else 1.0

    group_summary = {}
    for group, values in sorted(group_totals.items()):
        required = values["required"]
        group_summary[group] = {
            "required_feature_instances": int(required),
            "available_all_rows_instances": int(values["available_all"]),
            "missing_all_rows_instances": int(values["missing_all"]),
            "available_all_rows_pct": round(values["available_all"] / required, 6) if required else 0.0,
        }

    metrics = {
        "feature_audit_count": feature_audit_count,
        "feature_rows": feature_rows,
        "classification_allowed_count": allowed_count,
        "classification_allowed_rate": round(allowed_rate, 6),
        "blocking_missing_ticker_count": blocking_tickers,
        "required_feature_instances": required_feature_instances,
        "missing_all_rows_instances": missing_all_instances,
        "missing_all_rows_pct": round(missing_all_rows_pct, 6),
        "side_status_counts": dict(sorted(side_status_counts.items())),
    }

    checks: List[Dict[str, Any]] = []
    add_check(checks, "feature_audit_scope", feature_audit_count >= args.min_feature_audits, feature_audit_count, f">= {args.min_feature_audits}")
    add_check(checks, "feature_row_scope", feature_rows >= args.min_feature_rows, feature_rows, f">= {args.min_feature_rows}")
    add_check(
        checks,
        "classification_allowed_rate",
        allowed_rate >= args.min_classification_allowed_rate,
        round(allowed_rate, 6),
        f">= {args.min_classification_allowed_rate}",
    )
    add_check(
        checks,
        "blocking_missing_ticker_limit",
        blocking_tickers <= args.max_blocking_missing_tickers,
        blocking_tickers,
        f"<= {args.max_blocking_missing_tickers}",
    )
    add_check(
        checks,
        "missing_all_rows_pct_limit",
        missing_all_rows_pct <= args.max_missing_all_rows_pct,
        round(missing_all_rows_pct, 6),
        f"<= {args.max_missing_all_rows_pct}",
    )

    passed = all(check["ok"] for check in checks)
    audit = {
        "version": "SIGNAL_MODEL_V2_FEATURE_BROAD_VALIDATION_AUDIT",
        "run_id": run_id,
        "generated_at": utc_stamp(),
        "passed": passed,
        "production_ready": False,
        "inputs": {"replay_audit": rel(args.replay_audit)},
        "thresholds": {
            "min_feature_audits": args.min_feature_audits,
            "min_feature_rows": args.min_feature_rows,
            "min_classification_allowed_rate": args.min_classification_allowed_rate,
            "max_blocking_missing_tickers": args.max_blocking_missing_tickers,
            "max_missing_all_rows_pct": args.max_missing_all_rows_pct,
        },
        "metrics": metrics,
        "checks": checks,
        "tickers": ticker_rows,
        "group_summary": group_summary,
        "blocking_feature_sample_counts": dict(blocking_counter.most_common()),
        "summary": {
            "checks_total": len(checks),
            "checks_passed": sum(1 for check in checks if check["ok"]),
            "checks_failed": sum(1 for check in checks if not check["ok"]),
            "failed_checks": [check["name"] for check in checks if not check["ok"]],
        },
        "report": rel(report_path),
        "interpretation": (
            "Feature broad validation passed for the supplied replay audit."
            if passed
            else "Feature broad validation failed; current evidence does not retire the feature-substitution blocker."
        ),
    }
    write_json(audit_path, audit)
    write_report(report_path, audit)
    print(f"Wrote {audit_path}")
    print(f"Wrote {report_path}")
    print(
        "feature_validation_passed={passed} checks={passed_count}/{total} failed={failed}".format(
            passed=passed,
            passed_count=audit["summary"]["checks_passed"],
            total=audit["summary"]["checks_total"],
            failed=",".join(audit["summary"]["failed_checks"]),
        )
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
