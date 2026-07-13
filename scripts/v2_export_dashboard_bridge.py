from __future__ import annotations

import argparse
import csv
import glob
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from v2_common import V2_ROOT, append_jsonl, read_csv, rel, utc_stamp, write_csv, write_json


TARGET_PREFIX = "dt_target_liquidity_"
ADVERSE_PREFIX = "dt_adverse_liquidity_"
SIGNAL_MODEL_ROOT = Path(r"D:\Coding\Python Codes\Newtest\Breaker_Based\signal_model")
USEFUL_FINDINGS_PREDICTIONS = (
    SIGNAL_MODEL_ROOT / "datasets" / "predictions" / "signal_useful_findings_second_stage_v1_predictions.csv"
)
APPROVED_TRADE_LAYER = "APPROVED_TRADE_DECISION_V1"
ENTRY_PERMISSION_PROCESS = "ENTRY_PERMISSION_ULTRA_ONLY_V1"
ACTIVE_SELECTION_PROCESS = "USEFUL_FINDINGS_SECOND_STAGE_V1_ACTIVE"
MIXED_RANK_PROCESS = "MIXED_RANKED_BSL_TOP20_V1_CURRENT_ANALOG"
MIXED_RANK_RAW_ALIAS = "low_swept_pressure_access_q50 / current_topbucket"
MIXED_RANK_LINEAGE = "mixed-train, original-current"
MIXED_RANK_SOURCE = "approved_long_artifact_raw_score_ranked_current_rows"
USEFUL_ACTIVE_PERMISSIONS = {
    "ultra_high_conviction": "take_candidate",
    "high_conviction": "take_candidate",
    "neutral_no_edge": "no",
    "reject": "no",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export V2 decision rows into dashboard/API bridge artifacts. This is additive output only; it does not "
            "modify the original dashboard."
        )
    )
    parser.add_argument("--decisions", nargs="+", required=True, help="Decision CSV path(s) or glob pattern(s).")
    parser.add_argument(
        "--liquidity-dirs",
        nargs="*",
        default=[],
        help="Optional liquidity run directories containing candidates_scored.csv.",
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--latest-per-ticker", type=int, default=1)
    parser.add_argument("--max-live-rows", type=int, default=50)
    return parser.parse_args()


def resolve_paths(items: Sequence[str]) -> List[Path]:
    paths: List[Path] = []
    for item in items:
        matches = [Path(match) for match in glob.glob(item)]
        if matches:
            paths.extend(matches)
        else:
            paths.append(Path(item))
    seen: set[str] = set()
    resolved: List[Path] = []
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            resolved.append(path)
    return resolved


def to_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    numeric = to_float(value)
    if numeric is None:
        return None
    return int(numeric)


def boolish(value: Any) -> bool | None:
    if value in ("", None):
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return None


def clean_value(value: Any) -> Any:
    numeric = to_float(value)
    if numeric is not None:
        return numeric
    if value == "":
        return None
    return value


def first_float(row: Dict[str, Any], keys: Sequence[str]) -> float | None:
    for key in keys:
        numeric = to_float(row.get(key))
        if numeric is not None:
            return numeric
    return None


def first_value(row: Dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in ("", None):
            return value
    return None


def setup_levels_from_row(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Export the same setup-level annotations used by the original chart dashboard."""
    specs = [
        (
            "T3 Low",
            "L",
            "#ef5350",
            ["t3_low_price"],
            ["t3_low_time"],
        ),
        (
            "T2 High",
            "H",
            "#2962ff",
            ["t2_high_price"],
            ["t2_high_time"],
        ),
        (
            "Current ISL",
            "ISL",
            "#f9a825",
            ["current_isl_price", "isl_level"],
            ["current_isl_time", "t1_sweep_low_time", "signal_time", "decision_time"],
        ),
        (
            "Base ISL",
            "ISLB",
            "#ffb74d",
            ["base_isl_price"],
            ["base_isl_time", "t3_low_time"],
        ),
        (
            "Base ISH",
            "BSL",
            "#26a69a",
            ["base_ish_price"],
            ["base_ish_time", "t2_high_time"],
        ),
    ]
    levels: List[Dict[str, Any]] = []
    for name, label, color, price_keys, time_keys in specs:
        price = first_float(row, price_keys)
        time = to_int(first_value(row, time_keys))
        if price is None or time is None:
            continue
        levels.append(
            {
                "name": name,
                "label": label,
                "color": color,
                "price": price,
                "time": time,
            }
        )
    return levels


def setup_fvg_zones_from_row(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    lower = first_float(
        row,
        [
            "bull_fvg_lower",
            "bull_fvg_lower_price",
            "tech_nearest_bull_fvg_lower",
        ],
    )
    upper = first_float(
        row,
        [
            "bull_fvg_upper",
            "bull_fvg_upper_price",
            "tech_nearest_bull_fvg_upper",
        ],
    )
    if lower is None or upper is None:
        return []
    zone_time = to_int(
        first_value(
            row,
            [
                "bull_fvg_time",
                "tech_nearest_bull_fvg_created_time",
                "t1_sweep_low_time",
                "signal_time",
                "decision_time",
            ],
        )
    )
    return [
        {
            "label": "Bull FVG",
            "side": "bull",
            "lower": min(lower, upper),
            "upper": max(lower, upper),
            "time": zone_time,
        }
    ]


def signal_time_key(row: Dict[str, Any]) -> tuple[str, int | None]:
    ticker = str(row.get("ticker") or row.get("ticker_norm") or "").strip()
    decision_time = to_int(row.get("decision_time"))
    signal_id = str(row.get("signal_id") or "")
    if decision_time is None and "|" in signal_id:
        parts = signal_id.split("|")
        if len(parts) >= 3:
            decision_time = to_int(parts[2])
    return ticker, decision_time


def load_optional_process_rows(path: Path) -> tuple[Dict[str, Dict[str, Any]], Dict[tuple[str, int | None], Dict[str, Any]]]:
    by_signal_id: Dict[str, Dict[str, Any]] = {}
    by_time: Dict[tuple[str, int | None], Dict[str, Any]] = {}
    if not path.exists():
        return by_signal_id, by_time
    for row in read_csv(path):
        signal_id = row.get("signal_id")
        if signal_id:
            by_signal_id[signal_id] = row
        by_time[signal_time_key(row)] = row
    return by_signal_id, by_time


def process_lookup(
    row: Dict[str, Any],
    by_signal_id: Dict[str, Dict[str, Any]],
    by_time: Dict[tuple[str, int | None], Dict[str, Any]],
) -> Dict[str, Any]:
    signal_id = row.get("signal_id")
    if signal_id and signal_id in by_signal_id:
        return by_signal_id[signal_id]
    return by_time.get(signal_time_key(row), {})


def clean_broad_bucket_from_useful(row: Dict[str, Any]) -> str | None:
    if not row:
        return None
    if str(row.get("clean_broad_v2_member") or "").strip() in {"1", "1.0", "true", "True"}:
        return "best_combined_v2_member"
    if str(row.get("clean_broad_v2_looser_member") or "").strip() in {"1", "1.0", "true", "True"}:
        return "best_combined_v2_looser_member"
    return None


def active_from_useful_findings(
    useful_bucket: Any,
    useful_score: float | None,
    raw_model_score: float | None,
    final_model_score: float | None,
    useful_row: Dict[str, Any],
) -> Dict[str, Any]:
    bucket = str(useful_bucket or "").strip() or "insufficient_data"
    permission = USEFUL_ACTIVE_PERMISSIONS.get(bucket, "cannot_classify")
    active_score = useful_score
    if active_score is None:
        active_score = raw_model_score if raw_model_score is not None else final_model_score
    if bucket == "insufficient_data":
        gate_failures = "no useful-findings process row matched this signal_id/ticker_time"
    else:
        gate_failures = useful_row.get("rejection_reason") or useful_row.get("gate_failures") or None
    return {
        "bucket": bucket,
        "permission": permission,
        "score": active_score,
        "gate_failures": gate_failures,
    }


def load_decision_rows(paths: Iterable[Path]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        for row in read_csv(path):
            row["_source_decision_file"] = rel(path)
            rows.append(row)
    return rows


def load_liquidity_rows(paths: Iterable[Path]) -> Dict[str, List[Dict[str, Any]]]:
    by_signal: Dict[str, List[Dict[str, Any]]] = {}
    for path in paths:
        scored = path / "candidates_scored.csv" if path.is_dir() else path
        if not scored.exists():
            continue
        for row in read_csv(scored):
            signal_id = row.get("signal_id")
            if not signal_id:
                continue
            by_signal.setdefault(signal_id, []).append(row)
    for signal_id, rows in by_signal.items():
        rows.sort(
            key=lambda row: (
                row.get("candidate_role") != "target_side",
                -(to_float(row.get("approved_model_score")) or -1.0),
                to_float(row.get("candidate_distance_to_signal_atr")) or 999999.0,
            )
        )
    return by_signal


def extract_ranked_levels(row: Dict[str, Any], prefix: str, max_levels: int = 5) -> List[Dict[str, Any]]:
    levels: List[Dict[str, Any]] = []
    for rank in range(1, max_levels + 1):
        pool_id = row.get(f"{prefix}{rank}_pool_id")
        score = to_float(row.get(f"{prefix}{rank}_score"))
        distance = to_float(row.get(f"{prefix}{rank}_distance_atr"))
        midpoint = to_float(row.get(f"{prefix}{rank}_midpoint"))
        side = row.get(f"{prefix}{rank}_side")
        if not any(value not in ("", None) for value in [pool_id, score, distance, midpoint, side]):
            continue
        levels.append(
            {
                "rank": rank,
                "pool_id": pool_id or None,
                "side": side or None,
                "price": midpoint,
                "distance_atr": distance,
                "score": score,
            }
        )
    return levels


def compact_scored_candidates(rows: Sequence[Dict[str, Any]], max_rows: int = 10) -> List[Dict[str, Any]]:
    compact: List[Dict[str, Any]] = []
    for row in rows[:max_rows]:
        compact.append(
            {
                "candidate_role": row.get("candidate_role"),
                "side": row.get("candidate_side") or row.get("side"),
                "price": to_float(row.get("midpoint") or row.get("upper") or row.get("lower")),
                "distance_atr": to_float(row.get("candidate_distance_to_signal_atr") or row.get("eval_distance_to_pool_atr")),
                "score": to_float(row.get("approved_model_score")),
                "percentile": to_float(row.get("approved_model_percentile")),
                "decile": to_int(row.get("approved_model_decile")),
                "pool_id": row.get("candidate_pool_id") or row.get("pool_id"),
            }
        )
    return compact


def bucket_from_row(row: Dict[str, Any]) -> str:
    for key in ["tds_decision_class", "decision_bucket", "bucket"]:
        if row.get(key):
            return str(row[key])
    if row.get("tds_entry_permission") == "reject":
        return "reject"
    return "not_classified"


def permission_from_row(row: Dict[str, Any]) -> str:
    for key in ["tds_entry_permission", "entry_permission", "permission"]:
        if row.get(key):
            raw = str(row[key]).strip().lower()
            if raw in {"reject", "no", "false", "0", "skip_reject", "skip_no_trade"}:
                return "no"
            if raw in {"yes", "true", "1", "allow", "allowed", "entry_allowed"}:
                return "yes"
            if raw in {"review", "manual_review"}:
                return "review"
            return raw
    bucket = bucket_from_row(row)
    return "no" if bucket in {"reject", "insufficient_data", "not_classified"} else "review"


def make_dashboard_row(
    row: Dict[str, Any],
    liquidity_by_signal: Dict[str, List[Dict[str, Any]]],
    useful_by_signal_id: Dict[str, Dict[str, Any]] | None = None,
    useful_by_time: Dict[tuple[str, int | None], Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    signal_id = row.get("signal_id") or ""
    target_levels = extract_ranked_levels(row, TARGET_PREFIX)
    adverse_levels = extract_ranked_levels(row, ADVERSE_PREFIX)
    scored_context = compact_scored_candidates(liquidity_by_signal.get(signal_id, []))
    approved_bucket = bucket_from_row(row)
    approved_permission = permission_from_row(row)
    score = to_float(row.get("score"))
    if score is None:
        score = to_float(row.get("prediction"))
    strict_score = to_float(row.get("strict_score"))
    raw_model_score = first_float(
        row,
        [
            "raw_model_score",
            "ungated_model_score",
            "artifact_raw_model_score",
            "signal_model_raw_score",
        ],
    )
    main_gate_pass = boolish(row.get("main_gate_pass") or row.get("long_artifact_main_gate_pass"))
    strict_gate_pass = boolish(row.get("strict_gate_pass") or row.get("long_artifact_strict_gate_pass"))
    risk = to_float(row.get("risk"))
    entry = to_float(row.get("entry_price"))
    stop = to_float(row.get("stop_price"))
    decision_time = to_int(row.get("decision_time"))
    reason = row.get("tds_reason") or row.get("reason_codes") or row.get("_v2_block_reason") or None
    main_gate_failures = row.get("main_gate_failures") or None
    strict_gate_failures = row.get("strict_gate_failures") or None
    score_gate_suppressed = (
        raw_model_score is not None
        and score is not None
        and score == 0
        and main_gate_pass is False
    )
    mixed_rank_score = raw_model_score
    mixed_rank_score_basis = "raw_model_score"
    if mixed_rank_score is None:
        mixed_rank_score = score
        mixed_rank_score_basis = "final_model_score_fallback"
    if mixed_rank_score is None:
        mixed_rank_score_basis = None
    rejection_detail = None
    if score_gate_suppressed and main_gate_failures:
        rejection_detail = f"main_gate_failed:{main_gate_failures}"
    elif strict_gate_pass is False and strict_gate_failures:
        rejection_detail = f"strict_gate_failed:{strict_gate_failures}"
    elif reason:
        rejection_detail = str(reason)
    missing_fields = (
        row.get("tds_missing_fields")
        or row.get("missing_fields")
        or row.get("signal_model_missing_score_fields")
        or None
    )
    if not missing_fields and row.get("missing_required_feature_count") not in ("", None):
        missing_fields = f"missing_required_feature_count={row.get('missing_required_feature_count')}"
    useful_row = process_lookup(row, useful_by_signal_id or {}, useful_by_time or {})
    useful_bucket = (
        useful_row.get("conviction_label")
        or useful_row.get("decision_class")
        or None
    )
    useful_score = first_float(
        useful_row,
        [
            "live_score_plus_useful_oof_score",
            "clean_score_plus_useful_oof_score",
            "useful_global_oof_score",
            "clean_useful_oof_score",
        ],
    )
    clean_broad_bucket = (
        clean_broad_bucket_from_useful(useful_row)
        or row.get("clean_broad_filter_bucket")
        or None
    )
    active_selection = active_from_useful_findings(
        useful_bucket,
        useful_score,
        raw_model_score,
        score,
        useful_row,
    )
    active_bucket = active_selection["bucket"]
    active_permission = active_selection["permission"]
    active_score = active_selection["score"]
    active_gate_failures = active_selection["gate_failures"]
    active_missing_fields = missing_fields
    if active_bucket == "insufficient_data" and not active_missing_fields:
        active_missing_fields = active_gate_failures
    process_resolution_status = (
        "useful_findings_active; entry_permission_artifact_and_mixed_rank_exposed_separately"
    )
    return {
        "signal_id": signal_id,
        "candidate_row_id": row.get("candidate_row_id"),
        "ticker": row.get("ticker"),
        "direction": row.get("direction") or row.get("side"),
        "decision_time": decision_time,
        "signal_time": to_int(row.get("signal_time")),
        "feature_cutoff_time": to_int(row.get("feature_cutoff_time")),
        "bucket": active_bucket,
        "permission": active_permission,
        "active_process_name": ACTIVE_SELECTION_PROCESS,
        "active_trade_bucket": active_bucket,
        "active_trade_permission": active_permission,
        "active_raw_score": active_score,
        "active_final_score": active_score,
        "active_gate_failures": active_gate_failures,
        "entry_permission_artifact_bucket": approved_bucket,
        "entry_permission_artifact_permission": approved_permission,
        "entry_permission_artifact_raw_score": raw_model_score,
        "entry_permission_artifact_final_score": score,
        "entry_permission_artifact_gate_failures": rejection_detail or main_gate_failures or strict_gate_failures or reason,
        "useful_findings_bucket": useful_bucket,
        "useful_findings_score": useful_score,
        "useful_findings_source": useful_row.get("prediction_source") or None,
        "clean_broad_bsl_bucket": clean_broad_bucket,
        "clean_broad_bsl_source": "signal_useful_findings_second_stage_v1" if clean_broad_bucket else None,
        "raw_ungated_score": raw_model_score,
        "process_resolution_status": process_resolution_status,
        "approved_trade_layer": APPROVED_TRADE_LAYER,
        "approved_trade_bucket": approved_bucket,
        "approved_entry_permission": approved_permission,
        "approved_raw_score": raw_model_score,
        "approved_final_score": score,
        "approved_strict_score": strict_score,
        "approved_main_gate_pass": main_gate_pass,
        "approved_strict_gate_pass": strict_gate_pass,
        "approved_score_gate_suppressed": score_gate_suppressed,
        "approved_gate_failures": rejection_detail or main_gate_failures or strict_gate_failures or reason,
        "mixed_rank_process": MIXED_RANK_PROCESS,
        "mixed_rank_raw_alias": MIXED_RANK_RAW_ALIAS,
        "mixed_rank_lineage": MIXED_RANK_LINEAGE,
        "mixed_rank_source": MIXED_RANK_SOURCE,
        "mixed_rank_score": mixed_rank_score,
        "mixed_rank_score_basis": mixed_rank_score_basis,
        "mixed_rank_bucket": None,
        "mixed_rank_percentile": None,
        "mixed_rank_rank": None,
        "mixed_rank_population": None,
        "clean_broad_filter_bucket": row.get("clean_broad_filter_bucket") or None,
        "permission_raw": row.get("tds_entry_permission") or row.get("entry_permission") or row.get("permission") or None,
        "trade_action": row.get("tds_trade_action") or None,
        "model_score": active_score,
        "raw_model_score": raw_model_score,
        "main_gate_pass": main_gate_pass,
        "strict_gate_pass": strict_gate_pass,
        "score_gate_suppressed": score_gate_suppressed,
        "main_gate_failures": main_gate_failures,
        "strict_gate_failures": strict_gate_failures,
        "strict_score": strict_score,
        "score_ready": boolish(row.get("signal_model_score_ready")),
        "score_source": row.get("signal_model_score_source") or None,
        "entry": entry,
        "stop": stop,
        "risk": risk,
        "risk_pct_of_entry": (risk / entry) if risk is not None and entry else None,
        "reason": reason,
        "rejection_detail": rejection_detail,
        "hard_gate_note": row.get("tds_hard_gate_note") or None,
        "missing_fields": active_missing_fields,
        "target_liquidity": target_levels,
        "adverse_liquidity": adverse_levels,
        "scored_liquidity_context": scored_context,
        "setup_levels": setup_levels_from_row(row),
        "fvg_zones": setup_fvg_zones_from_row(row),
        "liquidity_context_status": (
            "scored_levels"
            if scored_context
            else "ranked_levels"
            if target_levels or adverse_levels
            else "summary_only_no_visible_levels"
        ),
        "summary_metrics": {
            "target_liquidity_count": to_float(row.get("dt_target_liquidity_count")),
            "target_liquidity_score_sum": to_float(row.get("dt_target_liquidity_score_sum")),
            "target_liquidity_score_max": to_float(row.get("dt_target_liquidity_score_max")),
            "target_liquidity_nearest_distance_atr": to_float(row.get("dt_target_liquidity_nearest_distance_atr")),
            "adverse_liquidity_count": to_float(row.get("dt_adverse_liquidity_count")),
            "adverse_liquidity_score_sum": to_float(row.get("dt_adverse_liquidity_score_sum")),
            "target_minus_adverse_pressure": to_float(row.get("dt_target_minus_adverse_liquidity_pressure")),
            "xg_target_minus_stop_pressure": to_float(row.get("xg_liq_target_minus_stop_pressure")),
            "topology_candidate_count": to_float(row.get("topology_scored_candidate_count")),
            "topbucket_target_pressure_minus_drag": to_float(row.get("topbucket_target_pressure_minus_drag")),
        },
        "source_decision_file": row.get("_source_decision_file"),
    }


def assign_mixed_rank_fields(rows: Sequence[Dict[str, Any]]) -> None:
    scored_rows = [row for row in rows if to_float(row.get("mixed_rank_score")) is not None]
    scored_rows.sort(
        key=lambda row: (
            -(to_float(row.get("mixed_rank_score")) or -1.0),
            str(row.get("ticker") or ""),
            str(row.get("signal_id") or ""),
        )
    )
    n = len(scored_rows)
    if n == 0:
        for row in rows:
            row["mixed_rank_bucket"] = "mixed_rank_unavailable"
        return

    top20_count = max(1, math.ceil(n * 0.20))
    top30_count = max(top20_count, math.ceil(n * 0.30))
    bottom20_start = max(0, n - max(1, math.ceil(n * 0.20)))

    for idx, row in enumerate(scored_rows):
        rank = idx + 1
        percentile = 1.0 if n == 1 else 1.0 - (idx / (n - 1))
        if idx < top20_count:
            bucket = "mixed_top20"
        elif idx < top30_count:
            bucket = "mixed_top30_not_top20"
        elif idx >= bottom20_start:
            bucket = "mixed_bottom20"
        else:
            bucket = "mixed_middle"
        row["mixed_rank_rank"] = rank
        row["mixed_rank_percentile"] = percentile
        row["mixed_rank_bucket"] = bucket
        row["mixed_rank_population"] = n


def process_layer_summary(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    approved_positive = [
        row for row in rows if row.get("approved_trade_bucket") in {"ultra_high_conviction", "high_conviction"}
    ]
    mixed_top20 = [row for row in rows if row.get("mixed_rank_bucket") == "mixed_top20"]
    approved_ids = {row.get("signal_id") for row in approved_positive}
    mixed_ids = {row.get("signal_id") for row in mixed_top20}
    high_mixed_rejected = [
        row
        for row in mixed_top20
        if row.get("approved_trade_bucket") in {"reject", "neutral_no_edge", "insufficient_data"}
    ]
    return {
        "active_selection_process": ACTIVE_SELECTION_PROCESS,
        "active_trade_bucket_counts": count_values(rows, "active_trade_bucket"),
        "active_trade_permission_counts": count_values(rows, "active_trade_permission"),
        "useful_findings_bucket_counts": count_values(rows, "useful_findings_bucket"),
        "approved_trade_bucket_counts": count_values(rows, "approved_trade_bucket"),
        "approved_entry_permission_counts": count_values(rows, "approved_entry_permission"),
        "mixed_rank_bucket_counts": count_values(rows, "mixed_rank_bucket"),
        "approved_ultra_or_high_count": len(approved_positive),
        "mixed_top20_count": len(mixed_top20),
        "approved_ultra_or_high_and_mixed_top20_overlap": len(approved_ids & mixed_ids),
        "approved_ultra_or_high_not_mixed_top20": len(approved_ids - mixed_ids),
        "mixed_top20_rejected_or_nonpermissioned": len(high_mixed_rejected),
        "mixed_rank_lineage": MIXED_RANK_LINEAGE,
        "mixed_rank_process": MIXED_RANK_PROCESS,
        "mixed_rank_raw_alias": MIXED_RANK_RAW_ALIAS,
        "mixed_rank_source": MIXED_RANK_SOURCE,
    }


def latest_rows(rows: Sequence[Dict[str, Any]], latest_per_ticker: int, max_live_rows: int) -> List[Dict[str, Any]]:
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            row.get("ticker") or "",
            -(row.get("decision_time") or 0),
            row.get("signal_id") or "",
        ),
    )
    counts: Dict[tuple[str, str], int] = {}
    selected: List[Dict[str, Any]] = []
    for row in sorted_rows:
        key = (str(row.get("ticker") or ""), str(row.get("direction") or ""))
        count = counts.get(key, 0)
        if count >= latest_per_ticker:
            continue
        counts[key] = count + 1
        selected.append(row)
    selected.sort(key=lambda row: (-(row.get("decision_time") or 0), row.get("ticker") or ""))
    return selected[:max_live_rows]


def write_report(path: Path, audit: Dict[str, Any]) -> None:
    bucket_counts = audit.get("bucket_counts", {})
    permission_counts = audit.get("permission_counts", {})
    process_layers = audit.get("process_layers") or {}
    lines = [
        "# V2 Dashboard/API Bridge Report",
        "",
        f"- Run ID: `{audit.get('run_id')}`",
        f"- Decision rows read: `{audit.get('decision_rows_read')}`",
        f"- Bridge rows written: `{audit.get('bridge_rows_written')}`",
        f"- Live rows written: `{audit.get('live_rows_written')}`",
        f"- Signals with scored liquidity context: `{audit.get('signals_with_scored_liquidity_context')}`",
        f"- Status: `{audit.get('status')}`",
        "",
        "## Outputs",
        "",
    ]
    for key, value in (audit.get("outputs") or {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Buckets", ""])
    for key, value in bucket_counts.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Permissions", ""])
    for key, value in permission_counts.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Process Layers", ""])
    lines.append(
        "The dashboard bridge keeps approved trade decisions separate from mixed-ranked/topbucket diagnostics."
    )
    lines.append("")
    lines.append(f"- Approved layer: `{APPROVED_TRADE_LAYER}`")
    lines.append(f"- Mixed-rank process: `{process_layers.get('mixed_rank_process')}`")
    lines.append(f"- Mixed-rank raw alias: `{process_layers.get('mixed_rank_raw_alias')}`")
    lines.append(f"- Mixed-rank lineage: `{process_layers.get('mixed_rank_lineage')}`")
    lines.append(f"- Mixed-rank source: `{process_layers.get('mixed_rank_source')}`")
    lines.append(
        f"- Approved ultra/high overlap with mixed top20: `{process_layers.get('approved_ultra_or_high_and_mixed_top20_overlap')}`"
    )
    lines.append(
        f"- Mixed top20 rejected/nonpermissioned by approved scorer: `{process_layers.get('mixed_top20_rejected_or_nonpermissioned')}`"
    )
    lines.extend(["", "### Approved Trade Buckets", ""])
    for key, value in (process_layers.get("approved_trade_bucket_counts") or {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "### Mixed Rank Buckets", ""])
    for key, value in (process_layers.get("mixed_rank_bucket_counts") or {}).items():
        lines.append(f"- `{key}`: `{value}`")
    contract = audit.get("dashboard_contract") or {}
    lines.extend(
        [
            "",
            "## Dashboard Contract",
            "",
            f"- Contract OK: `{contract.get('contract_ok')}`",
            f"- Checked rows: `{contract.get('checked_rows')}`",
            f"- Failed rows: `{contract.get('contract_failed_rows')}`",
            f"- Missing by field: `{contract.get('missing_by_field', {})}`",
        ]
    )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This is an additive bridge artifact for dashboard/API consumers. It does not modify the original dashboard.",
            "The live-state JSON contains latest rows per ticker/direction. The cumulative JSON contains all bridge rows plus aggregate counts.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def count_values(rows: Sequence[Dict[str, Any]], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "missing")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def missing_dashboard_fields(row: Dict[str, Any]) -> List[str]:
    required = [
        "signal_id",
        "ticker",
        "direction",
        "decision_time",
        "bucket",
        "permission",
        "entry",
        "stop",
        "risk",
        "reason",
    ]
    missing = [key for key in required if row.get(key) in ("", None)]
    bucket = str(row.get("bucket") or "").strip().lower()
    if bucket == "insufficient_data":
        if row.get("missing_fields") in ("", None):
            missing.append("missing_fields")
    elif row.get("model_score") in ("", None):
        missing.append("model_score")
    summary = row.get("summary_metrics") if isinstance(row.get("summary_metrics"), dict) else {}
    has_summary_liquidity_context = any(
        summary.get(key) is not None
        for key in [
            "target_liquidity_count",
            "adverse_liquidity_count",
            "xg_target_minus_stop_pressure",
            "topology_candidate_count",
        ]
    )
    has_process_rank_context = any(
        row.get(key) not in ("", None)
        for key in [
            "active_final_score",
            "useful_findings_score",
            "mixed_rank_score",
            "raw_ungated_score",
        ]
    )
    if (
        bucket != "insufficient_data"
        and not row.get("scored_liquidity_context")
        and not row.get("target_liquidity")
        and not row.get("adverse_liquidity")
        and not has_summary_liquidity_context
        and not has_process_rank_context
    ):
        missing.append("scored_or_ranked_liquidity_context")
    return missing


def dashboard_contract_summary(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    missing_by_field: Dict[str, int] = {}
    failed_rows = 0
    for row in rows:
        missing = missing_dashboard_fields(row)
        if missing:
            failed_rows += 1
        for field in missing:
            missing_by_field[field] = missing_by_field.get(field, 0) + 1
    return {
        "checked_rows": len(rows),
        "contract_failed_rows": failed_rows,
        "contract_passed_rows": len(rows) - failed_rows,
        "missing_by_field": dict(sorted(missing_by_field.items())),
        "contract_ok": failed_rows == 0 and bool(rows),
        "required_fields": [
            "signal_id",
            "ticker",
            "direction",
            "decision_time",
            "bucket",
            "permission",
            "entry",
            "stop",
            "risk",
            "reason",
            "model_score_non_insufficient",
            "missing_fields_for_insufficient_data",
            "scored_or_ranked_liquidity_context",
        ],
    }


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"v2_dashboard_bridge_{utc_stamp()}"
    out_dir = V2_ROOT / "dashboard_bridge" / run_id
    log_path = V2_ROOT / "logs" / f"{run_id}.jsonl"
    audit_path = V2_ROOT / "audits" / f"{run_id}_audit.json"
    report_path = V2_ROOT / "reports" / f"{run_id}_report.md"
    live_json = out_dir / "live_state.json"
    cumulative_json = out_dir / "cumulative_state.json"
    rows_csv = out_dir / "signal_ranker_rows.csv"

    decision_paths = resolve_paths(args.decisions)
    liquidity_paths = resolve_paths(args.liquidity_dirs)
    append_jsonl(
        log_path,
        {
            "ts": utc_stamp(),
            "event": "start",
            "decision_paths": [str(path) for path in decision_paths],
            "liquidity_paths": [str(path) for path in liquidity_paths],
            "run_id": run_id,
        },
    )

    decision_rows = load_decision_rows(decision_paths)
    liquidity_by_signal = load_liquidity_rows(liquidity_paths)
    useful_by_signal_id, useful_by_time = load_optional_process_rows(USEFUL_FINDINGS_PREDICTIONS)
    bridge_rows = [
        make_dashboard_row(row, liquidity_by_signal, useful_by_signal_id, useful_by_time)
        for row in decision_rows
    ]
    assign_mixed_rank_fields(bridge_rows)
    live_rows = latest_rows(bridge_rows, args.latest_per_ticker, args.max_live_rows)

    row_csv_records: List[Dict[str, Any]] = []
    for row in bridge_rows:
        first_target = (row.get("target_liquidity") or [{}])[0] if row.get("target_liquidity") else {}
        first_scored = (
            (row.get("scored_liquidity_context") or [{}])[0]
            if row.get("scored_liquidity_context")
            else {}
        )
        row_csv_records.append(
            {
                "signal_id": row.get("signal_id"),
                "ticker": row.get("ticker"),
                "direction": row.get("direction"),
                "decision_time": row.get("decision_time"),
                "bucket": row.get("bucket"),
                "permission": row.get("permission"),
                "active_process_name": row.get("active_process_name"),
                "active_trade_bucket": row.get("active_trade_bucket"),
                "active_trade_permission": row.get("active_trade_permission"),
                "active_raw_score": row.get("active_raw_score"),
                "active_final_score": row.get("active_final_score"),
                "active_gate_failures": row.get("active_gate_failures"),
                "entry_permission_artifact_bucket": row.get("entry_permission_artifact_bucket"),
                "entry_permission_artifact_permission": row.get("entry_permission_artifact_permission"),
                "entry_permission_artifact_raw_score": row.get("entry_permission_artifact_raw_score"),
                "entry_permission_artifact_final_score": row.get("entry_permission_artifact_final_score"),
                "entry_permission_artifact_gate_failures": row.get("entry_permission_artifact_gate_failures"),
                "approved_trade_bucket": row.get("approved_trade_bucket"),
                "approved_entry_permission": row.get("approved_entry_permission"),
                "approved_raw_score": row.get("approved_raw_score"),
                "approved_final_score": row.get("approved_final_score"),
                "approved_main_gate_pass": row.get("approved_main_gate_pass"),
                "approved_strict_gate_pass": row.get("approved_strict_gate_pass"),
                "approved_gate_failures": row.get("approved_gate_failures"),
                "mixed_rank_process": row.get("mixed_rank_process"),
                "mixed_rank_raw_alias": row.get("mixed_rank_raw_alias"),
                "mixed_rank_lineage": row.get("mixed_rank_lineage"),
                "mixed_rank_score": row.get("mixed_rank_score"),
                "mixed_rank_percentile": row.get("mixed_rank_percentile"),
                "mixed_rank_bucket": row.get("mixed_rank_bucket"),
                "mixed_rank_rank": row.get("mixed_rank_rank"),
                "mixed_rank_population": row.get("mixed_rank_population"),
                "useful_findings_bucket": row.get("useful_findings_bucket"),
                "useful_findings_score": row.get("useful_findings_score"),
                "useful_findings_source": row.get("useful_findings_source"),
                "clean_broad_bsl_bucket": row.get("clean_broad_bsl_bucket"),
                "clean_broad_bsl_source": row.get("clean_broad_bsl_source"),
                "clean_broad_filter_bucket": row.get("clean_broad_filter_bucket"),
                "raw_ungated_score": row.get("raw_ungated_score"),
                "process_resolution_status": row.get("process_resolution_status"),
                "model_score": row.get("model_score"),
                "raw_model_score": row.get("raw_model_score"),
                "main_gate_pass": row.get("main_gate_pass"),
                "strict_gate_pass": row.get("strict_gate_pass"),
                "score_gate_suppressed": row.get("score_gate_suppressed"),
                "strict_score": row.get("strict_score"),
                "entry": row.get("entry"),
                "stop": row.get("stop"),
                "risk": row.get("risk"),
                "target_1_side": first_target.get("side"),
                "target_1_price": first_target.get("price"),
                "target_1_distance_atr": first_target.get("distance_atr"),
                "target_1_score": first_target.get("score"),
                "scored_liquidity_1_role": first_scored.get("candidate_role"),
                "scored_liquidity_1_side": first_scored.get("side"),
                "scored_liquidity_1_price": first_scored.get("price"),
                "scored_liquidity_1_distance_atr": first_scored.get("distance_atr"),
                "scored_liquidity_1_score": first_scored.get("score"),
                "reason": row.get("reason"),
                "missing_fields": row.get("missing_fields"),
            }
        )

    outputs = {
        "live_state": rel(live_json),
        "cumulative_state": rel(cumulative_json),
        "signal_ranker_rows": rel(rows_csv),
        "log": rel(log_path),
        "report": rel(report_path),
    }
    cumulative_payload = {
        "schema_version": "SIGNAL_MODEL_V2_DASHBOARD_CUMULATIVE_STATE_V1",
        "run_id": run_id,
        "generated_at": utc_stamp(),
        "decision_sources": [rel(path) for path in decision_paths],
        "liquidity_sources": [rel(path) for path in liquidity_paths],
        "row_count": len(bridge_rows),
        "bucket_counts": count_values(bridge_rows, "bucket"),
        "permission_counts": count_values(bridge_rows, "permission"),
        "process_layers": process_layer_summary(bridge_rows),
        "rows": bridge_rows,
    }
    live_payload = {
        "schema_version": "SIGNAL_MODEL_V2_DASHBOARD_LIVE_STATE_V1",
        "run_id": run_id,
        "generated_at": utc_stamp(),
        "row_count": len(live_rows),
        "rows": live_rows,
    }
    write_json(cumulative_json, cumulative_payload)
    write_json(live_json, live_payload)
    write_csv(rows_csv, row_csv_records)

    signals_with_liq = sum(1 for row in bridge_rows if row.get("scored_liquidity_context"))
    contract = dashboard_contract_summary(bridge_rows)
    audit = {
        "version": "SIGNAL_MODEL_V2_DASHBOARD_BRIDGE_AUDIT",
        "run_id": run_id,
        "generated_at": utc_stamp(),
        "decision_sources": [rel(path) for path in decision_paths],
        "liquidity_sources": [rel(path) for path in liquidity_paths],
        "decision_rows_read": len(decision_rows),
        "bridge_rows_written": len(bridge_rows),
        "live_rows_written": len(live_rows),
        "signals_with_scored_liquidity_context": signals_with_liq,
        "dashboard_contract": contract,
        "bucket_counts": cumulative_payload["bucket_counts"],
        "permission_counts": cumulative_payload["permission_counts"],
        "process_layers": cumulative_payload["process_layers"],
        "outputs": outputs,
        "original_dashboard_modified": False,
        "status": "passed" if contract["contract_ok"] else "failed",
        "log": rel(log_path),
    }
    write_json(audit_path, audit)
    write_report(report_path, audit)
    append_jsonl(log_path, {"ts": utc_stamp(), "event": "finish", "status": audit["status"], "audit": rel(audit_path)})
    print(f"Wrote {report_path}")
    print(f"Wrote {audit_path}")
    return 0 if audit["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
