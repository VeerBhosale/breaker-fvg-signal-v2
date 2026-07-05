from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

from v2_common import V1_ROOT, V2_ROOT, python_exe, read_csv, rel, run_command, utc_stamp, write_csv, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run V2 bridge smoke using existing V1 approved artifact-backed pipeline.")
    parser.add_argument("--events", type=Path, required=True, help="Input standardized signal event CSV.")
    parser.add_argument("--limit", type=int, default=3, help="Rows to use for smoke.")
    parser.add_argument("--run-id", default=None)
    return parser.parse_args()


def subset_events(path: Path, limit: int, run_id: str) -> Path:
    rows = read_csv(path)
    if not rows:
        raise ValueError(f"No rows found in {path}")
    subset = rows[:limit]
    out = V2_ROOT / "data" / "signals" / f"{run_id}_events.csv"
    write_csv(out, subset)
    return out


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"v2_bridge_smoke_{utc_stamp()}"
    log_path = V2_ROOT / "logs" / f"{run_id}.jsonl"
    report_path = V2_ROOT / "reports" / f"{run_id}_report.md"
    audit_path = V2_ROOT / "audits" / f"{run_id}_audit.json"
    events_subset = subset_events(args.events, args.limit, run_id)

    feature_complete = V2_ROOT / "data" / "features" / f"{run_id}_feature_complete.csv"
    scored = V2_ROOT / "data" / "predictions" / f"{run_id}_01_scored.csv"
    decisions = V2_ROOT / "data" / "predictions" / f"{run_id}_03_decisions.csv"

    builder = V1_ROOT / "scripts" / "build_trade_system_feature_complete_inference_rows_v1.py"
    scorer = V1_ROOT / "scripts" / "score_trade_system_signal_models_v1.py"
    decision = V1_ROOT / "scripts" / "apply_trade_decision_system_v1.py"
    smoke_feature_source = V1_ROOT / "datasets" / "live_inbox" / "trade_system_long_feature_complete_smoke12.csv"

    run_command(
        [
            python_exe(),
            str(builder),
            "--events",
            str(events_subset),
            "--feature-source",
            str(smoke_feature_source),
            "--output",
            str(feature_complete),
            "--audit",
            str(V2_ROOT / "audits" / f"{run_id}_feature_complete_audit.json"),
        ],
        log_path,
        "build_feature_complete_rows",
    )

    run_command(
        [
            python_exe(),
            str(scorer),
            "--input",
            str(feature_complete),
            "--output",
            str(scored),
            "--audit",
            str(V2_ROOT / "audits" / f"{run_id}_scoring_audit.json"),
            "--mode",
            "model_artifacts",
        ],
        log_path,
        "score_signal_models",
    )

    run_command(
        [
            python_exe(),
            str(decision),
            "--input",
            str(scored),
            "--output",
            str(decisions),
            "--threshold-mode",
            "live_fixed",
        ],
        log_path,
        "apply_decision_system",
    )

    decision_rows = read_csv(decisions)
    buckets: Dict[str, int] = {}
    for row in decision_rows:
        bucket = (
            row.get("tds_decision_class")
            or row.get("bucket")
            or row.get("decision_bucket")
            or row.get("trade_bucket")
            or row.get("classification")
            or "unknown"
        )
        buckets[bucket] = buckets.get(bucket, 0) + 1

    audit: Dict[str, Any] = {
        "version": "SIGNAL_MODEL_V2_BRIDGE_SMOKE_AUDIT",
        "run_id": run_id,
        "generated_at": utc_stamp(),
        "mode": "bridge_smoke",
        "input_events": rel(args.events),
        "event_rows_used": len(read_csv(events_subset)),
        "outputs": {
            "events_subset": rel(events_subset),
            "feature_complete": rel(feature_complete),
            "scored": rel(scored),
            "decisions": rel(decisions),
            "log": rel(log_path),
            "report": rel(report_path),
        },
        "bucket_counts": buckets,
        "production_ready": False,
        "production_readiness_note": "This proves the V2 runtime boundary can call approved artifact-backed V1 scripts. It does not prove raw-candle live signal detection or full live feature parity.",
    }
    write_json(audit_path, audit)

    lines = [
        "# V2 Bridge Smoke Report",
        "",
        f"- Run ID: `{run_id}`",
        f"- Mode: `bridge_smoke`",
        f"- Input events: `{rel(args.events)}`",
        f"- Rows used: `{audit['event_rows_used']}`",
        f"- Feature-complete output: `{rel(feature_complete)}`",
        f"- Scored output: `{rel(scored)}`",
        f"- Decision output: `{rel(decisions)}`",
        f"- Log: `{rel(log_path)}`",
        "",
        "## Bucket Counts",
        "",
    ]
    if buckets:
        for bucket, count in sorted(buckets.items()):
            lines.append(f"- `{bucket}`: {count}")
    else:
        lines.append("- No bucket field found in output; inspect decision CSV.")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This is a V2 boundary smoke using V1-approved scripts/artifacts. It is not full production-live readiness.",
            "The remaining production work is raw candle ingestion, native signal detection, full decision-time feature parity, liquidity scoring from fresh visible levels, replay/paper ledger, dashboard bridge, and AWS packaging.",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"Wrote {report_path}")
    print(f"Wrote {audit_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
