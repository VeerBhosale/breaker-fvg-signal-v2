from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

from v2_common import V2_ROOT, append_jsonl, read_csv, read_json, rel, utc_stamp, write_csv, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply V2 signal decision gate. Incomplete feature rows become insufficient_data decisions."
    )
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--feature-audit", type=Path, required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--audit", type=Path, default=None)
    return parser.parse_args()


def row_value(row: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in ("", None):
            return value
    return ""


def insufficient_decision(row: Dict[str, Any], reason: str, missing_count: int) -> Dict[str, Any]:
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
        "entry_price": row_value(row, "entry_price", "entry_variant_entry_price"),
        "stop_price": row_value(row, "stop_price", "entry_variant_stop_price"),
        "risk": row_value(row, "risk", "entry_variant_risk"),
        "target_liquidity_score_max": row.get("xg_liq_target_side_score_max", ""),
        "target_liquidity_nearest_distance_atr": row.get("xg_liq_target_side_nearest_distance_atr", ""),
        "target_minus_stop_pressure": row.get("xg_liq_target_minus_stop_pressure", ""),
        "reason_codes": reason,
        "missing_required_feature_count": missing_count,
        "v2_decision_gate_version": "V2_INSUFFICIENT_DATA_GATE_V1",
    }


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"v2_decision_gate_{utc_stamp()}"
    log_path = V2_ROOT / "logs" / f"{run_id}.jsonl"
    output_path = args.output or (V2_ROOT / "data" / "predictions" / f"{run_id}_decisions.csv")
    audit_path = args.audit or (V2_ROOT / "audits" / f"{run_id}_audit.json")

    append_jsonl(log_path, {"ts": utc_stamp(), "event": "start", "features": str(args.features), "feature_audit": str(args.feature_audit)})
    features = read_csv(args.features)
    feature_audit = read_json(args.feature_audit)
    classification_allowed = bool(feature_audit.get("classification_allowed"))
    classification_status = str(feature_audit.get("classification_status") or "")
    missing_count = int(feature_audit.get("missing_all_rows_count") or 0)
    blocking_missing_count = int(feature_audit.get("blocking_missing_all_rows_count", missing_count) or 0)

    decisions: List[Dict[str, Any]] = []
    if not classification_allowed:
        reason = (
            f"{classification_status or 'insufficient_data'}:"
            f"blocking_missing_required_features={blocking_missing_count};raw_missing_features={missing_count}"
        )
        decisions = [insufficient_decision(row, reason, blocking_missing_count) for row in features]
    else:
        # This branch is intentionally conservative until a full scorer output contract is wired.
        reason = "classification_allowed_but_v2_model_scorer_not_wired"
        decisions = [insufficient_decision(row, reason, 0) for row in features]

    write_csv(output_path, decisions)
    bucket_counts: Dict[str, int] = {}
    for decision in decisions:
        bucket = str(decision.get("bucket") or "unknown")
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

    audit = {
        "version": "SIGNAL_MODEL_V2_DECISION_GATE_AUDIT",
        "run_id": run_id,
        "generated_at": utc_stamp(),
        "features": rel(args.features),
        "feature_audit": rel(args.feature_audit),
        "output": rel(output_path),
        "log": rel(log_path),
        "input_rows": len(features),
        "output_rows": len(decisions),
        "classification_allowed": classification_allowed,
        "source_classification_status": classification_status,
        "missing_all_rows_count": missing_count,
        "blocking_missing_all_rows_count": blocking_missing_count,
        "bucket_counts": bucket_counts,
        "order_placement_enabled": False,
        "passed": len(decisions) == len(features),
    }
    write_json(audit_path, audit)
    append_jsonl(log_path, {"ts": utc_stamp(), "event": "finish", "rows": len(decisions), "bucket_counts": bucket_counts, "audit": rel(audit_path)})
    print(f"Wrote {output_path}")
    print(f"Wrote {audit_path}")
    return 0 if audit["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
