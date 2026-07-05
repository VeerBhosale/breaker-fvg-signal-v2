from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from v2_common import V2_ROOT, read_csv, rel, utc_stamp, write_csv, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit feature parity between approved model preprocess files and a feature-complete CSV.")
    parser.add_argument("--feature-complete", type=Path, required=True)
    parser.add_argument("--long-preprocess", type=Path, default=Path("Newtest/Breaker_Based/signal_model/models/trade_system_long_entry_permission_v1_preprocess.json"))
    parser.add_argument("--short-preprocess", type=Path, default=Path("Newtest/Breaker_Based/signal_model_short/models/short_signal_ssl_travel_oof_v1_1h_2y_all_research_short_goal_v1_current_range50_or_fvg50_hit_at_least_2_ssl_all_features_preprocess.json"))
    parser.add_argument("--run-id", default=None)
    return parser.parse_args()


def load_features(path: Path) -> List[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ["used_features", "feature_columns", "features"]:
        value = data.get(key)
        if isinstance(value, list):
            return [str(item) for item in value]
    raise ValueError(f"No feature list found in {path}")


def audit_side(side: str, features: List[str], rows: List[Dict[str, str]]) -> Dict[str, Any]:
    columns = set(rows[0].keys()) if rows else set()
    missing_columns = [feature for feature in features if feature not in columns]
    present = [feature for feature in features if feature in columns]
    value_missing_counts = {}
    for feature in present:
        missing = sum(1 for row in rows if row.get(feature, "") in ("", "nan", "None", "null"))
        if missing:
            value_missing_counts[feature] = missing
    return {
        "side": side,
        "required_feature_count": len(features),
        "present_feature_count": len(present),
        "missing_feature_column_count": len(missing_columns),
        "missing_feature_columns": missing_columns,
        "features_with_missing_values_count": len(value_missing_counts),
        "max_missing_values_for_a_feature": max(value_missing_counts.values()) if value_missing_counts else 0,
        "artifact_columns_ready": len(missing_columns) == 0,
    }


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"v2_feature_parity_{utc_stamp()}"
    feature_rows = read_csv(args.feature_complete)
    long_features = load_features(args.long_preprocess)
    short_features = load_features(args.short_preprocess)
    side_reports = [
        audit_side("long", long_features, feature_rows),
        audit_side("short", short_features, feature_rows),
    ]
    rows = []
    for report in side_reports:
        rows.append({key: value for key, value in report.items() if key != "missing_feature_columns"})
    csv_path = V2_ROOT / "reports" / f"{run_id}_summary.csv"
    write_csv(csv_path, rows)
    audit = {
        "version": "SIGNAL_MODEL_V2_FEATURE_PARITY_AUDIT",
        "run_id": run_id,
        "generated_at": utc_stamp(),
        "feature_complete": rel(args.feature_complete),
        "long_preprocess": rel(args.long_preprocess),
        "short_preprocess": rel(args.short_preprocess),
        "row_count": len(feature_rows),
        "side_reports": side_reports,
        "summary_csv": rel(csv_path),
        "production_ready": all(report["artifact_columns_ready"] for report in side_reports),
        "note": "A side can be production-ready only for rows of that side and only after value-level missingness is acceptable for the model artifact.",
    }
    audit_path = V2_ROOT / "audits" / f"{run_id}_audit.json"
    write_json(audit_path, audit)
    print(f"Wrote {audit_path}")
    print(f"Wrote {csv_path}")
    return 0 if audit["production_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

