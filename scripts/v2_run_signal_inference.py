from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List, Tuple

from v2_common import V1_ROOT, V2_ROOT, python_exe, read_csv, read_json, rel, run_command, utc_stamp, write_csv, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run V2 signal inference. Incomplete feature rows are gated as insufficient_data; "
            "feature-complete rows are scored with approved V1 signal artifacts and approved decision thresholds."
        )
    )
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--feature-audit", type=Path, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--scored-output", type=Path, default=None)
    parser.add_argument("--decision-output", type=Path, default=None)
    parser.add_argument("--audit", type=Path, default=None)
    parser.add_argument("--threshold-mode", choices=["live_fixed", "historical_row"], default="live_fixed")
    return parser.parse_args()


def feature_audit_allows_classification(path: Path | None) -> tuple[bool, str, int, int]:
    if path is None:
        return True, "feature_audit_not_supplied", 0, 0
    audit = read_json(path)
    allowed = bool(audit.get("classification_allowed"))
    status = str(audit.get("classification_status") or ("classification_allowed" if allowed else "insufficient_data"))
    missing = int(audit.get("missing_all_rows_count") or 0)
    blocking = int(audit.get("blocking_missing_all_rows_count", missing) or 0)
    return allowed, status, missing, blocking


def infer_side(row: Dict[str, Any]) -> str:
    side = str(row.get("side") or row.get("direction") or row.get("signal_model_side") or "").strip().lower()
    if side in {"long", "short"}:
        return side
    signal_id = str(row.get("signal_id") or row.get("candidate_row_id") or "").lower()
    if "|long|" in signal_id:
        return "long"
    if "|short|" in signal_id:
        return "short"
    return "unknown"


def insufficient_decision(row: Dict[str, Any], reason: str, missing_count: int) -> Dict[str, Any]:
    def first_present(*keys: str) -> Any:
        for key in keys:
            value = row.get(key)
            if value not in ("", None):
                return value
        return ""

    return {
        "signal_id": row.get("signal_id", ""),
        "candidate_row_id": row.get("candidate_row_id", ""),
        "ticker": row.get("ticker", ""),
        "side": row.get("side", ""),
        "direction": row.get("direction", ""),
        "decision_time": row.get("decision_time", ""),
        "feature_cutoff_time": row.get("feature_cutoff_time", ""),
        "bucket": "insufficient_data",
        "permission": "no",
        "trade_system_action": "skip_no_trade",
        "model_score": "",
        "model_probability": "",
        "entry_price": first_present("entry_price", "entry_variant_entry_price"),
        "stop_price": first_present("stop_price", "entry_variant_stop_price"),
        "risk": first_present("risk", "entry_variant_risk"),
        "target_liquidity_score_max": row.get("xg_liq_target_side_score_max", ""),
        "target_liquidity_nearest_distance_atr": row.get("xg_liq_target_side_nearest_distance_atr", ""),
        "target_minus_stop_pressure": row.get("xg_liq_target_minus_stop_pressure", ""),
        "reason_codes": reason,
        "missing_required_feature_count": missing_count,
        "v2_decision_gate_version": "V2_SIDE_AWARE_INSUFFICIENT_DATA_GATE_V1",
    }


def side_score_plan(
    rows: List[Dict[str, Any]],
    feature_audit_path: Path | None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    sides = [infer_side(row) for row in rows]
    counts: Dict[str, int] = {}
    for side in sides:
        counts[side] = counts.get(side, 0) + 1

    allowed, feature_status, missing_count, blocking_missing_count = feature_audit_allows_classification(feature_audit_path)
    audit = read_json(feature_audit_path) if feature_audit_path else {}
    side_status = audit.get("side_feature_status") if isinstance(audit.get("side_feature_status"), dict) else {}

    allowed_rows: List[Dict[str, Any]] = []
    blocked_rows: List[Dict[str, Any]] = []
    blocked_by_reason: Dict[str, int] = {}

    for row, side in zip(rows, sides):
        row["_v2_inferred_side"] = side
        row_allowed = False
        reason = ""
        missing_for_row = blocking_missing_count
        if side == "long":
            long_status = side_status.get("long") if isinstance(side_status, dict) else None
            if isinstance(long_status, dict):
                row_allowed = bool(long_status.get("classification_allowed"))
                missing_for_row = int(long_status.get("blocking_missing_all_rows_count") or 0)
                reason = (
                    f"{long_status.get('classification_status') or feature_status}:"
                    f"blocking_missing_required_features={missing_for_row};"
                    f"raw_missing_features={long_status.get('missing_all_rows_count', missing_for_row)}"
                )
            else:
                row_allowed = allowed
                reason = (
                    f"{feature_status}:blocking_missing_required_features={blocking_missing_count};"
                    f"raw_missing_features={missing_count}"
                )
        elif side == "short":
            short_status = side_status.get("short") if isinstance(side_status, dict) else None
            if isinstance(short_status, dict):
                row_allowed = bool(short_status.get("classification_allowed"))
                missing_for_row = int(short_status.get("blocking_missing_all_rows_count") or 0)
                status = short_status.get("classification_status") or "insufficient_data"
                raw_missing = int(short_status.get("missing_all_rows_count") or missing_for_row)
                structural_missing = int(short_status.get("structural_nullable_missing_all_rows_count") or 0)
                if not bool(short_status.get("approved_inference_contract")):
                    reason = (
                        "short_inference_contract_not_approved:"
                        f"feature_complete={bool(short_status.get('feature_complete'))};"
                        f"blocking_missing_required_features={missing_for_row};"
                        f"structural_nullable_missing_features={structural_missing};"
                        f"raw_missing_features={raw_missing}"
                    )
                else:
                    reason = (
                        f"{status}:"
                        f"blocking_missing_required_features={missing_for_row};"
                        f"raw_missing_features={raw_missing}"
                    )
            else:
                row_allowed = False
                missing_for_row = 0
                reason = "short_feature_contract_not_audited_in_v2_runtime"
        else:
            row_allowed = False
            missing_for_row = 1
            reason = "could_not_infer_signal_side"

        if row_allowed:
            allowed_rows.append(row)
        else:
            blocked = dict(row)
            blocked["_v2_block_reason"] = reason
            blocked["_v2_block_missing_count"] = missing_for_row
            blocked_rows.append(blocked)
            blocked_by_reason[reason] = blocked_by_reason.get(reason, 0) + 1

    plan = {
        "side_counts": counts,
        "feature_audit_status": feature_status,
        "feature_audit_classification_allowed": allowed,
        "missing_all_rows_count": missing_count,
        "blocking_missing_all_rows_count": blocking_missing_count,
        "side_feature_status_present": bool(side_status),
        "allowed_rows": len(allowed_rows),
        "blocked_rows": len(blocked_rows),
        "blocked_by_reason": blocked_by_reason,
    }
    return allowed_rows, blocked_rows, plan


def bucket_counts(path: Path) -> Dict[str, int]:
    rows = read_csv(path) if path.exists() else []
    counts: Dict[str, int] = {}
    for row in rows:
        bucket = (
            row.get("tds_decision_class")
            or row.get("bucket")
            or row.get("decision_bucket")
            or row.get("classification")
            or "unknown"
        )
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def normalized_permission(row: Dict[str, Any]) -> str:
    raw = str(
        row.get("tds_entry_permission")
        or row.get("entry_permission")
        or row.get("permission")
        or ""
    ).strip().lower()
    bucket = str(
        row.get("tds_decision_class")
        or row.get("bucket")
        or row.get("decision_bucket")
        or ""
    ).strip().lower()
    if raw in {"take_candidate", "paper_trade_candidate", "yes", "true", "1", "allow", "allowed"}:
        return "yes"
    if raw in {"conditional_take_candidate", "review", "manual_review"}:
        return "review"
    if raw in {"reject", "skip_reject", "no", "false", "0"}:
        return "no"
    if raw in {"skip", "skip_no_edge", "cannot_classify"}:
        return "no"
    if bucket in {"reject", "neutral_no_edge", "insufficient_data"}:
        return "no"
    if bucket in {"ultra_high_conviction", "high_conviction"}:
        return "review"
    return raw or "unknown"


def normalize_decision_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Add stable V2 public aliases without removing approved V1 decision columns."""
    normalized = dict(row)
    bucket = (
        normalized.get("tds_decision_class")
        or normalized.get("bucket")
        or normalized.get("decision_bucket")
        or "not_classified"
    )
    trade_action = (
        normalized.get("tds_trade_action")
        or normalized.get("trade_system_action")
        or normalized.get("trade_action")
        or ""
    )
    reason = (
        normalized.get("tds_reason")
        or normalized.get("reason_codes")
        or normalized.get("_v2_block_reason")
        or ""
    )
    model_score = normalized.get("score")
    if model_score in ("", None):
        model_score = normalized.get("prediction")
    if model_score in ("", None):
        model_score = normalized.get("model_score", "")

    normalized["bucket"] = bucket
    normalized["permission"] = normalized_permission(normalized)
    normalized["trade_system_action"] = trade_action
    normalized["trade_action"] = trade_action
    normalized["model_score"] = model_score
    normalized["model_probability"] = model_score
    normalized["reason_codes"] = reason
    normalized["decision_source"] = "approved_v1_artifacts_v2_normalized"
    return normalized


def normalize_decision_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [normalize_decision_row(row) for row in rows]


def write_report(path: Path, audit: Dict[str, Any]) -> None:
    lines: List[str] = [
        "# V2 Signal Inference Report",
        "",
        f"- Run ID: `{audit['run_id']}`",
        f"- Status: `{audit['status']}`",
        f"- Path: `{audit['inference_path']}`",
        f"- Input features: `{audit['features']}`",
        f"- Input rows: `{audit['input_rows']}`",
        f"- Decision output: `{audit['decision_output']}`",
        "",
        "## Bucket Counts",
        "",
    ]
    for bucket, count in sorted(audit.get("bucket_counts", {}).items()):
        lines.append(f"- `{bucket}`: `{count}`")
    if not audit.get("bucket_counts"):
        lines.append("- No decision buckets found.")
    lines.extend(
        [
            "",
            "## Side-Aware Gate",
            "",
            f"- Side counts: `{audit.get('side_score_plan', {}).get('side_counts', {})}`",
            f"- Rows scored: `{audit.get('side_score_plan', {}).get('allowed_rows', 0)}`",
            f"- Rows blocked before scoring: `{audit.get('side_score_plan', {}).get('blocked_rows', 0)}`",
            f"- Block reasons: `{audit.get('side_score_plan', {}).get('blocked_by_reason', {})}`",
            "",
            "## Notes",
            "",
            audit.get("note", ""),
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"v2_signal_inference_{utc_stamp()}"
    log_path = V2_ROOT / "logs" / f"{run_id}.jsonl"
    audit_path = args.audit or (V2_ROOT / "audits" / f"{run_id}_audit.json")
    report_path = V2_ROOT / "reports" / f"{run_id}_report.md"
    scored_output = args.scored_output or (V2_ROOT / "data" / "predictions" / f"{run_id}_scored.csv")
    decision_output = args.decision_output or (V2_ROOT / "data" / "predictions" / f"{run_id}_decisions.csv")

    rows = read_csv(args.features)
    allowed, feature_status, missing_count, blocking_missing_count = feature_audit_allows_classification(args.feature_audit)
    allowed_rows, blocked_rows, plan = side_score_plan(rows, args.feature_audit)
    audit: Dict[str, Any] = {
        "version": "SIGNAL_MODEL_V2_SIGNAL_INFERENCE_AUDIT",
        "run_id": run_id,
        "generated_at": utc_stamp(),
        "features": rel(args.features),
        "feature_audit": rel(args.feature_audit) if args.feature_audit else None,
        "input_rows": len(rows),
        "scored_output": rel(scored_output),
        "decision_output": rel(decision_output),
        "log": rel(log_path),
        "threshold_mode": args.threshold_mode,
        "feature_classification_allowed": allowed,
        "feature_classification_status": feature_status,
        "missing_all_rows_count": missing_count,
        "blocking_missing_all_rows_count": blocking_missing_count,
        "side_score_plan": plan,
        "production_order_placement_enabled": False,
    }

    if not allowed_rows:
        blocked_decisions = [
            insufficient_decision(
                row,
                str(row.get("_v2_block_reason") or "side_feature_contract_not_audited"),
                int(row.get("_v2_block_missing_count") or 0),
            )
            for row in blocked_rows
        ]
        write_csv(scored_output, blocked_rows)
        write_csv(decision_output, normalize_decision_rows(blocked_decisions))
        audit.update(
            {
                "status": "passed",
                "inference_path": "insufficient_data_gate",
                "bucket_counts": bucket_counts(decision_output),
                "scored_rows": 0,
                "blocked_rows": len(blocked_rows),
                "note": (
                    "Feature audit/side gate did not allow classification, so V2 blocked scoring "
                    "and emitted row-specific insufficient_data decisions."
                ),
            }
        )
        write_json(audit_path, audit)
        write_report(report_path, audit)
        print(f"Wrote {report_path}")
        print(f"Wrote {audit_path}")
        return 0

    if blocked_rows:
        allowed_features = V2_ROOT / "data" / "features" / f"{run_id}_score_allowed_features.csv"
        allowed_scored = V2_ROOT / "data" / "predictions" / f"{run_id}_score_allowed_scored.csv"
        allowed_decisions = V2_ROOT / "data" / "predictions" / f"{run_id}_score_allowed_decisions.csv"
        allowed_audit = V2_ROOT / "audits" / f"{run_id}_score_allowed_scoring_audit.json"
        write_csv(allowed_features, allowed_rows)
        scorer = V1_ROOT / "scripts" / "score_trade_system_signal_models_v1.py"
        decision = V1_ROOT / "scripts" / "apply_trade_decision_system_v1.py"
        run_command(
            [
                python_exe(),
                str(scorer),
                "--input",
                str(allowed_features),
                "--output",
                str(allowed_scored),
                "--audit",
                str(allowed_audit),
                "--mode",
                "model_artifacts",
            ],
            log_path,
            "score_allowed_signal_artifacts",
        )
        run_command(
            [
                python_exe(),
                str(decision),
                "--input",
                str(allowed_scored),
                "--output",
                str(allowed_decisions),
                "--threshold-mode",
                args.threshold_mode,
            ],
            log_path,
            "apply_allowed_trade_decision_thresholds",
        )
        scored_rows = read_csv(allowed_scored)
        decision_rows = read_csv(allowed_decisions)
        blocked_decisions = [
            insufficient_decision(
                row,
                str(row.get("_v2_block_reason") or "side_feature_contract_not_audited"),
                int(row.get("_v2_block_missing_count") or 0),
            )
            for row in blocked_rows
        ]
        write_csv(scored_output, scored_rows + blocked_rows)
        write_csv(decision_output, normalize_decision_rows(decision_rows + blocked_decisions))
        scoring_audit = read_json(allowed_audit)
        audit.update(
            {
                "status": "passed",
                "inference_path": "side_aware_partial_artifact_scoring",
                "bucket_counts": bucket_counts(decision_output),
                "scoring_audit": rel(allowed_audit),
                "scored_rows": len(allowed_rows),
                "blocked_rows": len(blocked_rows),
                "scoring_source_counts": scoring_audit.get("source_counts", {}),
                "artifact_scoring": scoring_audit.get("artifact_scoring", {}),
                "note": (
                    "V2 scored only rows whose side had an approved feature-completeness gate. "
                    "Rows without a side-valid feature contract were emitted as insufficient_data."
                ),
            }
        )
        write_json(audit_path, audit)
        write_report(report_path, audit)
        print(f"Wrote {report_path}")
        print(f"Wrote {audit_path}")
        return 0

    scorer = V1_ROOT / "scripts" / "score_trade_system_signal_models_v1.py"
    decision = V1_ROOT / "scripts" / "apply_trade_decision_system_v1.py"
    run_command(
        [
            python_exe(),
            str(scorer),
            "--input",
            str(args.features),
            "--output",
            str(scored_output),
            "--audit",
            str(V2_ROOT / "audits" / f"{run_id}_scoring_audit.json"),
            "--mode",
            "model_artifacts",
        ],
        log_path,
        "score_signal_artifacts",
    )
    run_command(
        [
            python_exe(),
            str(decision),
            "--input",
            str(scored_output),
            "--output",
            str(decision_output),
            "--threshold-mode",
            args.threshold_mode,
        ],
        log_path,
        "apply_trade_decision_thresholds",
    )
    decision_rows = read_csv(decision_output)
    write_csv(decision_output, normalize_decision_rows(decision_rows))

    scoring_audit = read_json(V2_ROOT / "audits" / f"{run_id}_scoring_audit.json")
    audit.update(
        {
            "status": "passed",
            "inference_path": "approved_artifact_scoring",
            "bucket_counts": bucket_counts(decision_output),
            "scoring_audit": rel(V2_ROOT / "audits" / f"{run_id}_scoring_audit.json"),
            "scored_rows": len(allowed_rows),
            "blocked_rows": 0,
            "scoring_source_counts": scoring_audit.get("source_counts", {}),
            "artifact_scoring": scoring_audit.get("artifact_scoring", {}),
            "note": "Feature rows were scored by approved V1 model artifacts, then categorized with approved live-fixed decision thresholds.",
        }
    )
    write_json(audit_path, audit)
    write_report(report_path, audit)
    print(f"Wrote {report_path}")
    print(f"Wrote {audit_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
