from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

from v2_common import (
    V1_ROOT,
    V2_ROOT,
    append_jsonl,
    python_exe,
    read_csv,
    read_json,
    rel,
    run_command,
    utc_stamp,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the current V2 replay smoke pipeline from V2-owned candles to native decision rows. "
            "This is a reproducible smoke wrapper; it does not make the system production-ready by itself."
        )
    )
    parser.add_argument("--candles", type=Path, required=True, help="V2 raw candle CSV with time/open/high/low/close.")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--decision-time-policy",
        choices=["signal_time", "next_candle_after_signal"],
        default="signal_time",
    )
    parser.add_argument(
        "--macro-candles-dir",
        type=Path,
        default=None,
        help="Optional macro universe candle directory. Defaults to the input candle file parent for smoke runs.",
    )
    parser.add_argument(
        "--max-input-candles",
        type=int,
        default=None,
        help="Optional latest-N candle bound passed into signal detection for live-style runtime checks.",
    )
    parser.add_argument(
        "--step-timeout-seconds",
        type=int,
        default=300,
        help="Hard timeout for each subprocess step in this per-ticker pipeline.",
    )
    return parser.parse_args()


def row_count(path: Path) -> int:
    return len(read_csv(path)) if path.exists() else 0


def safe_audit(path: Path) -> Dict[str, Any]:
    return read_json(path) if path.exists() else {}


def write_report(path: Path, audit: Dict[str, Any]) -> None:
    outputs = audit.get("outputs", {})
    summary = audit.get("summary", {})
    bucket_counts = summary.get("decision_bucket_counts", {})
    lines: List[str] = [
        "# V2 Replay Smoke Pipeline Report",
        "",
        f"- Run ID: `{audit.get('run_id')}`",
        f"- Ticker: `{audit.get('ticker')}`",
        f"- Candle source: `{audit.get('candles')}`",
        f"- Decision-time policy: `{audit.get('decision_time_policy')}`",
        f"- Max detector input candles: `{audit.get('max_input_candles')}`",
        f"- Step timeout seconds: `{audit.get('step_timeout_seconds')}`",
        f"- Status: `{audit.get('status')}`",
        "",
        "## Outputs",
        "",
    ]
    for key, value in outputs.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Signal rows: `{summary.get('signal_rows', 0)}`",
            f"- Liquidity candidate rows: `{summary.get('liquidity_candidate_rows', 0)}`",
            f"- Scored liquidity rows: `{summary.get('scored_liquidity_rows', 0)}`",
            f"- Aggregated signal rows: `{summary.get('aggregated_signal_rows', 0)}`",
            f"- Feature rows: `{summary.get('feature_rows', 0)}`",
            f"- Decision rows: `{summary.get('decision_rows', 0)}`",
            f"- Feature coverage all rows: `{summary.get('feature_available_all_rows', 0)} / {summary.get('required_features', 0)}`",
            f"- Missing required features all rows: `{summary.get('missing_features_all_rows', 0)}`",
            f"- Structural-null missing features all rows: `{summary.get('structural_nullable_missing_features_all_rows', 0)}`",
            f"- Blocking missing features all rows: `{summary.get('blocking_missing_features_all_rows', 0)}`",
            f"- Classification allowed: `{summary.get('classification_allowed')}`",
            f"- Signal inference path: `{summary.get('signal_inference_path')}`",
            "",
            "## Decision Buckets",
            "",
        ]
    )
    if bucket_counts:
        for bucket, count in sorted(bucket_counts.items()):
            lines.append(f"- `{bucket}`: `{count}`")
    else:
        lines.append("- No decision bucket counts available.")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This pipeline proves the current replay path is wired end to end for a small candle file.",
            (
                "The feature audit allowed approved-artifact scoring because all remaining all-row missing fields "
                "were classified as valid structural nulls by the V2 availability policy."
                if summary.get("signal_inference_path") == "approved_artifact_scoring"
                else "Native rows are still gated as `insufficient_data` because production-blocking features remain missing."
            ),
            "This does not prove production readiness; larger multi-ticker replay, short-side parity, paper ledger, dashboard/API bridge, and AWS packaging are still required.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"v2_replay_smoke_{args.ticker.replace('.', '_')}_{utc_stamp()}"
    log_path = V2_ROOT / "logs" / f"{run_id}.jsonl"
    audit_path = V2_ROOT / "audits" / f"{run_id}_audit.json"
    report_path = V2_ROOT / "reports" / f"{run_id}_report.md"

    events = V2_ROOT / "data" / "signals" / f"{run_id}_events.csv"
    detect_audit = V2_ROOT / "audits" / f"{run_id}_detect_audit.json"

    liq_dir = V2_ROOT / "data" / "liquidity" / run_id
    normalized_events = liq_dir / "events_normalized.csv"
    payload_dir = liq_dir / "payloads"
    payload_manifest = liq_dir / "payload_manifest.txt"
    liquidity_raw = liq_dir / "candidates_raw.csv"
    liquidity_features = liq_dir / "candidates_features.csv"
    liquidity_scored = liq_dir / "candidates_scored.csv"
    liquidity_aggregation = liq_dir / "signal_liquidity_aggregation.csv"

    payload_audit = V2_ROOT / "audits" / f"{run_id}_liquidity_payloads_audit.json"
    candidate_audit = V2_ROOT / "audits" / f"{run_id}_liquidity_candidates_audit.json"
    score_audit = V2_ROOT / "audits" / f"{run_id}_liquidity_scores_audit.json"
    aggregation_audit = V2_ROOT / "audits" / f"{run_id}_liquidity_aggregation_audit.json"

    features = V2_ROOT / "data" / "features" / f"{run_id}_features.csv"
    feature_audit = V2_ROOT / "audits" / f"{run_id}_features_audit.json"
    decisions = V2_ROOT / "data" / "predictions" / f"{run_id}_decisions.csv"
    signal_inference_audit = V2_ROOT / "audits" / f"{run_id}_signal_inference_audit.json"
    signal_scored = V2_ROOT / "data" / "predictions" / f"{run_id}_signal_scored.csv"

    detector = V2_ROOT / "scripts" / "v2_detect_signals_from_candles.py"
    payload_builder = V2_ROOT / "scripts" / "v2_build_liquidity_payloads_from_events.py"
    feature_builder = V2_ROOT / "scripts" / "v2_build_live_features_from_events.py"
    signal_inference = V2_ROOT / "scripts" / "v2_run_signal_inference.py"
    candidate_builder = V1_ROOT / "scripts" / "build_trade_system_decision_time_liquidity_candidates_v1.py"
    liquidity_scorer = V2_ROOT / "scripts" / "v2_score_liquidity_candidates.py"
    liquidity_aggregator = V1_ROOT / "scripts" / "aggregate_trade_system_liquidity_scores_v1.py"

    outputs = {
        "events": rel(events),
        "normalized_events": rel(normalized_events),
        "liquidity_features": rel(liquidity_features),
        "liquidity_scored": rel(liquidity_scored),
        "liquidity_aggregation": rel(liquidity_aggregation),
        "liquidity_payload_dir": rel(payload_dir),
        "features": rel(features),
        "decisions": rel(decisions),
        "log": rel(log_path),
        "report": rel(report_path),
    }
    audit: Dict[str, Any] = {
        "version": "SIGNAL_MODEL_V2_REPLAY_SMOKE_PIPELINE_AUDIT",
        "run_id": run_id,
        "generated_at": utc_stamp(),
        "ticker": args.ticker,
        "candles": rel(args.candles),
        "decision_time_policy": args.decision_time_policy,
        "max_input_candles": args.max_input_candles,
        "step_timeout_seconds": args.step_timeout_seconds,
        "outputs": outputs,
        "status": "started",
        "production_ready": False,
    }

    try:
        run_command(
            [
                python_exe(),
                str(detector),
                "--candles",
                str(args.candles),
                "--ticker",
                args.ticker,
                "--run-id",
                run_id,
                "--decision-time-policy",
                args.decision_time_policy,
                "--output",
                str(events),
                "--audit",
                str(detect_audit),
            ]
            + (
                ["--max-input-candles", str(args.max_input_candles)]
                if args.max_input_candles is not None
                else []
            ),
            log_path,
            "detect_signals",
            args.step_timeout_seconds,
        )
        if row_count(events) == 0:
            raise RuntimeError("Signal detection produced zero rows; pipeline cannot continue.")

        try:
            run_command(
                [
                    python_exe(),
                    str(payload_builder),
                    "--events",
                    str(events),
                    "--candles",
                    str(args.candles),
                    "--run-id",
                    run_id,
                    "--normalized-events",
                    str(normalized_events),
                    "--payload-dir",
                    str(payload_dir),
                    "--manifest",
                    str(payload_manifest),
                    "--audit",
                    str(payload_audit),
                ]
                + (
                    ["--max-input-candles", str(args.max_input_candles)]
                    if args.max_input_candles is not None
                    else []
                ),
                log_path,
                "build_liquidity_payloads",
                args.step_timeout_seconds,
            )
        except RuntimeError:
            payload_audit_payload = safe_audit(payload_audit)
            if int(payload_audit_payload.get("payloads_written", 0) or 0) <= 0:
                raise
            append_jsonl(
                log_path,
                {
                    "ts": utc_stamp(),
                    "step": "build_liquidity_payloads",
                    "event": "partial_payloads_continue",
                    "payloads_written": payload_audit_payload.get("payloads_written", 0),
                    "payloads_skipped": payload_audit_payload.get("payloads_skipped", 0),
                    "audit": rel(payload_audit),
                },
            )

        run_command(
            [
                python_exe(),
                str(candidate_builder),
                "--events",
                str(normalized_events),
                "--manifest",
                str(payload_manifest),
                "--raw-output",
                str(liquidity_raw),
                "--feature-output",
                str(liquidity_features),
                "--audit",
                str(candidate_audit),
            ],
            log_path,
            "build_liquidity_candidates",
            args.step_timeout_seconds,
        )

        run_command(
            [
                python_exe(),
                str(liquidity_scorer),
                str(liquidity_features),
                "--output",
                str(liquidity_scored),
                "--audit",
                str(score_audit),
            ],
            log_path,
            "score_liquidity_candidates",
            args.step_timeout_seconds,
        )

        run_command(
            [
                python_exe(),
                str(liquidity_aggregator),
                "--events",
                str(normalized_events),
                "--scored-candidates",
                str(liquidity_scored),
                "--output",
                str(liquidity_aggregation),
                "--audit",
                str(aggregation_audit),
            ],
            log_path,
            "aggregate_liquidity_scores",
            args.step_timeout_seconds,
        )

        run_command(
            [
                python_exe(),
                str(feature_builder),
                "--events",
                str(events),
                "--candles",
                str(args.candles),
                "--liquidity-aggregation",
                str(liquidity_aggregation),
                "--liquidity-scored-candidates",
                str(liquidity_scored),
                "--liquidity-payload-dir",
                str(payload_dir),
                "--macro-candles-dir",
                str(args.macro_candles_dir or args.candles.parent),
                "--run-id",
                run_id,
                "--output",
                str(features),
                "--audit",
                str(feature_audit),
            ],
            log_path,
            "build_live_features",
            args.step_timeout_seconds,
        )

        run_command(
            [
                python_exe(),
                str(signal_inference),
                "--features",
                str(features),
                "--feature-audit",
                str(feature_audit),
                "--run-id",
                f"{run_id}_signal_inference",
                "--scored-output",
                str(signal_scored),
                "--decision-output",
                str(decisions),
                "--audit",
                str(signal_inference_audit),
            ],
            log_path,
            "run_signal_inference",
            args.step_timeout_seconds,
        )

        feature_audit_payload = safe_audit(feature_audit)
        signal_inference_audit_payload = safe_audit(signal_inference_audit)
        audit["status"] = "passed"
        audit["summary"] = {
            "signal_rows": row_count(events),
            "payloads_written": safe_audit(payload_audit).get("payloads_written", 0),
            "payloads_skipped": safe_audit(payload_audit).get("payloads_skipped", 0),
            "liquidity_candidate_rows": row_count(liquidity_features),
            "scored_liquidity_rows": row_count(liquidity_scored),
            "aggregated_signal_rows": row_count(liquidity_aggregation),
            "feature_rows": row_count(features),
            "decision_rows": row_count(decisions),
            "required_features": feature_audit_payload.get("required_feature_count", 0),
            "feature_available_all_rows": feature_audit_payload.get("available_all_rows_count", 0),
            "missing_features_all_rows": feature_audit_payload.get("missing_all_rows_count", 0),
            "structural_nullable_missing_features_all_rows": feature_audit_payload.get(
                "structural_nullable_missing_all_rows_count", 0
            ),
            "blocking_missing_features_all_rows": feature_audit_payload.get("blocking_missing_all_rows_count", 0),
            "classification_allowed": feature_audit_payload.get("classification_allowed"),
            "signal_inference_path": signal_inference_audit_payload.get("inference_path"),
            "decision_bucket_counts": signal_inference_audit_payload.get("bucket_counts", {}),
        }
        audit["step_audits"] = {
            "detect": rel(detect_audit),
            "payloads": rel(payload_audit),
            "candidates": rel(candidate_audit),
            "scores": rel(score_audit),
            "aggregation": rel(aggregation_audit),
            "features": rel(feature_audit),
            "signal_inference": rel(signal_inference_audit),
        }
        write_json(audit_path, audit)
        write_report(report_path, audit)
        print(f"Wrote {report_path}")
        print(f"Wrote {audit_path}")
        return 0
    except Exception as exc:
        audit["status"] = "failed"
        audit["error"] = str(exc)
        audit["summary"] = {
            "signal_rows": row_count(events),
            "payloads_written": safe_audit(payload_audit).get("payloads_written", 0),
            "payloads_skipped": safe_audit(payload_audit).get("payloads_skipped", 0),
            "liquidity_candidate_rows": row_count(liquidity_features),
            "scored_liquidity_rows": row_count(liquidity_scored),
            "aggregated_signal_rows": row_count(liquidity_aggregation),
            "feature_rows": row_count(features),
            "decision_rows": row_count(decisions),
        }
        write_json(audit_path, audit)
        write_report(report_path, audit)
        print(f"Wrote failed audit {audit_path}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
