from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from v2_common import REPO_ROOT, V1_ROOT, V2_ROOT, append_jsonl, read_csv, rel, utc_stamp, write_csv, write_json


DEFAULT_LONG_PREPROCESS = (
    V2_ROOT.parents[1]
    / "Breaker_Based"
    / "signal_model"
    / "models"
    / "trade_system_long_entry_permission_v1_preprocess.json"
)
DEFAULT_SHORT_PREPROCESS = (
    V2_ROOT.parents[1]
    / "Breaker_Based"
    / "signal_model_short"
    / "models"
    / "short_signal_ssl_travel_oof_v1_1h_2y_all_research_short_goal_v1_current_range50_or_fvg50_hit_at_least_2_ssl_all_features_preprocess.json"
)
DEFAULT_FEATURE_AVAILABILITY_POLICY = V2_ROOT / "configs" / "v2_feature_availability_policy.json"
DEFAULT_SIGNAL_ARTIFACT_REGISTRY = V1_ROOT / "configs" / "trade_system_signal_model_artifacts_v1.json"
DEFAULT_SIGNAL_DECISION_CONFIG = V1_ROOT / "configs" / "signal_trade_decision_system_v1_config.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build V2 native live-safe signal feature rows from standardized signal events and candle CSVs."
    )
    parser.add_argument("--events", type=Path, required=True, help="V2 standardized signal event CSV.")
    parser.add_argument("--candles", type=Path, required=True, help="V2 raw candle CSV for the event ticker.")
    parser.add_argument("--preprocess", type=Path, default=DEFAULT_LONG_PREPROCESS)
    parser.add_argument("--short-preprocess", type=Path, default=DEFAULT_SHORT_PREPROCESS)
    parser.add_argument("--signal-artifact-registry", type=Path, default=DEFAULT_SIGNAL_ARTIFACT_REGISTRY)
    parser.add_argument("--signal-decision-config", type=Path, default=DEFAULT_SIGNAL_DECISION_CONFIG)
    parser.add_argument(
        "--liquidity-aggregation",
        type=Path,
        default=None,
        help="Optional signal-level scored liquidity aggregation CSV to merge by signal_id.",
    )
    parser.add_argument(
        "--liquidity-scored-candidates",
        type=Path,
        default=None,
        help=(
            "Optional per-level scored liquidity candidate CSV. When supplied, V2 rebuilds ranked "
            "decision-time BSL/SSL topology from the scored levels instead of relying only on summaries."
        ),
    )
    parser.add_argument(
        "--liquidity-payload-dir",
        type=Path,
        default=None,
        help="Optional directory containing one decision-time liquidity payload JSON per signal.",
    )
    parser.add_argument(
        "--macro-candles-dir",
        type=Path,
        default=None,
        help=(
            "Optional directory of normalized 1H candle CSVs for the macro universe. "
            "If omitted, macro context uses the current candle file as a singleton universe."
        ),
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--audit", type=Path, default=None)
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument(
        "--feature-availability-policy",
        type=Path,
        default=DEFAULT_FEATURE_AVAILABILITY_POLICY,
        help="JSON policy that marks valid structural null features separately from production-blocking missing features.",
    )
    return parser.parse_args()


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        if pd.isna(value):
            return None
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def as_int(value: Any) -> int | None:
    value = as_float(value)
    return int(value) if value is not None else None


def encode(value: Any) -> Any:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, (np.integer, np.floating)):
        value = value.item()
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return round(value, 6)
    return value


def resolve_configured_path(value: Any) -> Path:
    path = Path(str(value or ""))
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def load_json_if_present(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_signal_inference_contracts(
    *,
    long_preprocess: Path,
    short_preprocess: Path,
    artifact_registry: Path,
    decision_config: Path,
) -> Dict[str, Dict[str, Any]]:
    registry = load_json_if_present(artifact_registry)
    decisions = load_json_if_present(decision_config)

    contracts: Dict[str, Dict[str, Any]] = {}
    for side, preprocess_path in (("long", long_preprocess), ("short", short_preprocess)):
        registry_side = registry.get(side) if isinstance(registry.get(side), dict) else {}
        decision_side = decisions.get(side) if isinstance(decisions.get(side), dict) else {}
        model_path = resolve_configured_path(registry_side.get("model_path"))
        registry_preprocess_path = resolve_configured_path(registry_side.get("preprocess_path"))
        status = str(registry_side.get("status") or "available").strip().lower()
        score_column = str(decision_side.get("score_column") or "").strip()
        target = str(decision_side.get("target") or "").strip()
        checks = {
            "artifact_registry_exists": artifact_registry.exists(),
            "decision_config_exists": decision_config.exists(),
            "registry_side_present": bool(registry_side),
            "decision_side_present": bool(decision_side),
            "registry_status_available": status == "available",
            "model_path_exists": model_path.exists(),
            "registry_preprocess_path_exists": registry_preprocess_path.exists(),
            "feature_preprocess_path_exists": preprocess_path.exists(),
            "score_column_present": bool(score_column),
            "target_present": bool(target),
        }
        approved = all(checks.values())
        contracts[side] = {
            "approved_inference_contract": approved,
            "checks": checks,
            "status": "approved" if approved else "not_approved",
            "target": target,
            "score_column": score_column,
            "artifact_registry": rel(artifact_registry),
            "decision_config": rel(decision_config),
            "model_path": rel(model_path),
            "registry_preprocess_path": rel(registry_preprocess_path),
            "feature_preprocess_path": rel(preprocess_path),
        }
    return contracts


def round6(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), 6)


def bool_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return int(value.strip().lower() in {"1", "true", "yes", "y"})
    return int(bool(value))


def safe_div(left: float | None, right: float | None) -> float | None:
    left = as_float(left)
    right = as_float(right)
    if left is None or right is None or abs(right) <= 1e-12:
        return None
    return left / right


def num_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index)
    return pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)


def div_series(left: pd.Series, right: pd.Series) -> pd.Series:
    return left / right.replace(0, np.nan)


def log1p_series(series: pd.Series) -> pd.Series:
    return np.log1p(series.clip(lower=0))


def clip01(value: float | None) -> float | None:
    if value is None:
        return None
    return min(1.0, max(0.0, value))


def load_required_features(path: Path) -> List[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    features = payload.get("used_features")
    if not isinstance(features, list) or not features:
        raise ValueError(f"No used_features list found in {path}")
    return [str(item) for item in features]


def load_feature_availability_policy(path: Path | None) -> Dict[str, Any]:
    if not path or not path.exists():
        return {
            "path": str(path) if path else None,
            "structural_nullable_features": set(),
            "structural_nullable_patterns": [],
            "loaded": False,
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "path": path,
        "version": payload.get("version"),
        "structural_nullable_features": set(payload.get("structural_nullable_features") or []),
        "structural_nullable_patterns": [
            re.compile(str(pattern)) for pattern in payload.get("structural_nullable_patterns") or []
        ],
        "loaded": True,
    }


def is_structural_nullable(feature: str, policy: Dict[str, Any]) -> bool:
    if feature in policy.get("structural_nullable_features", set()):
        return True
    for pattern in policy.get("structural_nullable_patterns", []):
        if pattern.search(feature):
            return True
    return False


def read_candles(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {"time", "open", "high", "low", "close"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"Candle CSV missing required columns: {missing}")
    frame = frame[["time", "open", "high", "low", "close"]].copy()
    for column in frame.columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["time", "open", "high", "low", "close"])
    frame["time"] = frame["time"].astype("int64")
    frame = frame.drop_duplicates(subset=["time"], keep="last").sort_values("time").reset_index(drop=True)
    return frame


def true_range(frame: pd.DataFrame) -> pd.Series:
    prev_close = frame["close"].shift(1).fillna(frame["close"])
    return pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prev_close).abs(),
            (frame["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def add_candle_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    tr = true_range(out)
    out["atr14"] = tr.rolling(14, min_periods=2).mean()
    out["atr20"] = tr.rolling(20, min_periods=2).mean()
    returns = out["close"].pct_change()
    out["volatility20"] = returns.rolling(20, min_periods=5).std()
    out["volatility50"] = returns.rolling(50, min_periods=10).std()
    for window in [8, 13, 20, 21, 34, 50, 100, 200]:
        out[f"ema{window}"] = out["close"].ewm(span=window, adjust=False, min_periods=2).mean()
    for window in [20, 50]:
        mean = out["close"].rolling(window, min_periods=5).mean()
        std = out["close"].rolling(window, min_periods=5).std()
        out[f"bb{window}_mid"] = mean
        out[f"bb{window}_upper"] = mean + 2 * std
        out[f"bb{window}_lower"] = mean - 2 * std
        out[f"bb{window}_width"] = out[f"bb{window}_upper"] - out[f"bb{window}_lower"]
    return out


def row_index_at_or_before(frame: pd.DataFrame, timestamp: Any) -> int | None:
    ts = as_int(timestamp)
    if ts is None or frame.empty:
        return None
    pos = int(np.searchsorted(frame["time"].to_numpy(), ts, side="right") - 1)
    return pos if 0 <= pos < len(frame) else None


def row_index_at_or_after(frame: pd.DataFrame, timestamp: Any) -> int | None:
    ts = as_int(timestamp)
    if ts is None or frame.empty:
        return None
    pos = int(np.searchsorted(frame["time"].to_numpy(), ts, side="left"))
    return pos if 0 <= pos < len(frame) else None


def value_at(frame: pd.DataFrame, index: int | None, column: str) -> float | None:
    if index is None or column not in frame.columns or not (0 <= index < len(frame)):
        return None
    return as_float(frame.iloc[index][column])


def percentile_last(series: pd.Series, index: int, lookback: int = 100) -> float | None:
    if index is None or index < 0:
        return None
    start = max(0, index - lookback + 1)
    window = pd.to_numeric(series.iloc[start : index + 1], errors="coerce").dropna()
    current = as_float(series.iloc[index])
    if current is None or window.empty:
        return None
    return float((window <= current).mean())


def candle_quality_leg(
    frame: pd.DataFrame,
    start_index: int | None,
    end_index: int | None,
    atr20: float | None,
    direction: str,
) -> Dict[str, Any]:
    prefix = "cq_down" if direction == "down" else "cq_up"
    out: Dict[str, Any] = {}
    if start_index is None or end_index is None or end_index < start_index or frame.empty:
        out[f"{prefix}_window_bars"] = 0
        return out

    segment = frame.iloc[max(0, start_index) : min(len(frame), end_index + 1)].copy()
    if segment.empty:
        out[f"{prefix}_window_bars"] = 0
        return out

    ranges = (segment["high"] - segment["low"]).replace(0, np.nan)
    bodies = (segment["close"] - segment["open"]).abs()
    body_to_range = bodies / ranges
    close_position = (segment["close"] - segment["low"]) / ranges
    atr = atr20 if atr20 and atr20 > 0 else None

    if direction == "down":
        selected = segment["close"] < segment["open"]
        upper_wick = segment["high"] - pd.concat([segment["open"], segment["close"]], axis=1).max(axis=1)
        low_wick = selected & ((upper_wick / ranges) <= 0.10)
        no_wick = selected & ((upper_wick / ranges) <= 0.03)
        selected_name = "red"
        wick_name = "low_upper"
        no_wick_name = "no_upper"
        clean_name = "clean_drop"
    else:
        selected = segment["close"] > segment["open"]
        lower_wick = pd.concat([segment["open"], segment["close"]], axis=1).min(axis=1) - segment["low"]
        low_wick = selected & ((lower_wick / ranges) <= 0.10)
        no_wick = selected & ((lower_wick / ranges) <= 0.03)
        selected_name = "green"
        wick_name = "low_lower"
        no_wick_name = "no_lower"
        clean_name = "clean_reversal"

    selected_count = int(selected.sum())
    low_wick_count = int(low_wick.sum())
    no_wick_count = int(no_wick.sum())
    selected_bodies = bodies[selected]
    body_atr = selected_bodies / atr if atr else pd.Series(dtype="float64")
    low_wick_ratio = low_wick_count / selected_count if selected_count else 0.0
    no_wick_ratio = no_wick_count / selected_count if selected_count else 0.0

    consecutive = 0
    best_consecutive = 0
    for flag in low_wick.tolist():
        if bool(flag):
            consecutive += 1
            best_consecutive = max(best_consecutive, consecutive)
        else:
            consecutive = 0

    body_sum = as_float(body_atr.sum()) if atr else None
    body_max = as_float(body_atr.max()) if atr and not body_atr.empty else None
    clean_score = low_wick_ratio * (body_sum or 0.0)
    impulse_score = (1 + best_consecutive) * low_wick_ratio * (body_max or 0.0)

    out[f"{prefix}_window_bars"] = int(len(segment))
    out[f"{prefix}_{selected_name}_count"] = selected_count
    out[f"{prefix}_{selected_name}_{wick_name}_wick_count"] = low_wick_count
    out[f"{prefix}_{selected_name}_{wick_name}_wick_ratio"] = low_wick_ratio
    out[f"{prefix}_{selected_name}_{no_wick_name}_wick_count"] = no_wick_count
    out[f"{prefix}_{selected_name}_{no_wick_name}_wick_ratio"] = no_wick_ratio
    out[f"{prefix}_{selected_name}_body_atr_sum"] = body_sum
    out[f"{prefix}_{selected_name}_body_atr_max"] = body_max
    out[f"{prefix}_{selected_name}_body_to_range_mean"] = as_float(body_to_range[selected].mean()) if selected_count else None
    out[f"{prefix}_{selected_name}_close_position_mean"] = as_float(close_position[selected].mean()) if selected_count else None
    out[f"{prefix}_{selected_name}_{wick_name}_consecutive_max"] = best_consecutive
    out[f"{prefix}_{clean_name}_score"] = clean_score
    out[f"{prefix}_impulse_score"] = impulse_score
    return out


def remap_short_candle_quality(src: Dict[str, Any], src_direction: str, dst_direction: str) -> Dict[str, Any]:
    """Map actual short-side candle legs into the long artifact's normalized feature names."""
    if src_direction == dst_direction:
        return src
    if src_direction == "up" and dst_direction == "down":
        mapping = {
            "cq_up_window_bars": "cq_down_window_bars",
            "cq_up_green_count": "cq_down_red_count",
            "cq_up_green_low_lower_wick_count": "cq_down_red_low_upper_wick_count",
            "cq_up_green_low_lower_wick_ratio": "cq_down_red_low_upper_wick_ratio",
            "cq_up_green_no_lower_wick_count": "cq_down_red_no_upper_wick_count",
            "cq_up_green_no_lower_wick_ratio": "cq_down_red_no_upper_wick_ratio",
            "cq_up_green_body_atr_sum": "cq_down_red_body_atr_sum",
            "cq_up_green_body_atr_max": "cq_down_red_body_atr_max",
            "cq_up_green_body_to_range_mean": "cq_down_red_body_to_range_mean",
            "cq_up_green_close_position_mean": "cq_down_red_close_position_mean",
            "cq_up_green_low_lower_consecutive_max": "cq_down_red_low_upper_consecutive_max",
            "cq_up_clean_reversal_score": "cq_down_clean_drop_score",
            "cq_up_impulse_score": "cq_down_impulse_score",
        }
    elif src_direction == "down" and dst_direction == "up":
        mapping = {
            "cq_down_window_bars": "cq_up_window_bars",
            "cq_down_red_count": "cq_up_green_count",
            "cq_down_red_low_upper_wick_count": "cq_up_green_low_lower_wick_count",
            "cq_down_red_low_upper_wick_ratio": "cq_up_green_low_lower_wick_ratio",
            "cq_down_red_no_upper_wick_count": "cq_up_green_no_lower_wick_count",
            "cq_down_red_no_upper_wick_ratio": "cq_up_green_no_lower_wick_ratio",
            "cq_down_red_body_atr_sum": "cq_up_green_body_atr_sum",
            "cq_down_red_body_atr_max": "cq_up_green_body_atr_max",
            "cq_down_red_body_to_range_mean": "cq_up_green_body_to_range_mean",
            "cq_down_red_close_position_mean": "cq_up_green_close_position_mean",
            "cq_down_red_low_upper_consecutive_max": "cq_up_green_low_lower_consecutive_max",
            "cq_down_clean_drop_score": "cq_up_clean_reversal_score",
            "cq_down_impulse_score": "cq_up_impulse_score",
        }
    else:
        return src
    return {dst: src.get(src_key) for src_key, dst in mapping.items()}


PHASE_METRICS = [
    "available",
    "bars",
    "net_atr",
    "abs_net_atr",
    "slope_atr_per_bar",
    "path_range_atr_sum",
    "path_efficiency",
    "favorable_excursion_atr",
    "adverse_excursion_atr",
    "adverse_to_favorable_ratio",
    "range_atr_mean",
    "range_atr_max",
    "body_atr_mean",
    "body_atr_max",
    "body_to_range_mean",
    "body_to_range_max",
    "directional_close_fraction",
    "directional_body_fraction",
    "directional_close_pressure_mean",
    "directional_close_pressure_max",
    "relevant_wick_ratio_mean",
    "relevant_wick_ratio_max",
    "opposite_wick_ratio_mean",
    "opposite_wick_ratio_max",
    "strong_displacement_count",
    "strong_directional_body_count",
    "max_consecutive_directional_closes",
    "favorable_fvg_count",
    "opposite_fvg_count",
    "favorable_fvg_gap_atr_sum",
    "favorable_fvg_gap_atr_max",
    "first_half_slope_atr_per_bar",
    "second_half_slope_atr_per_bar",
    "acceleration_atr_per_bar",
]


def mean_or_none(values: List[float]) -> float | None:
    return float(sum(values) / len(values)) if values else None


def max_or_none(values: List[float]) -> float | None:
    return max(values) if values else None


def segment_slope(frame: pd.DataFrame, start: int | None, end: int | None, direction: int, atr: float | None) -> float | None:
    if start is None or end is None or atr is None or atr <= 0 or end < start:
        return None
    start = max(0, start)
    end = min(len(frame) - 1, end)
    if end < start:
        return None
    start_close = value_at(frame, start, "close")
    end_close = value_at(frame, end, "close")
    if start_close is None or end_close is None:
        return None
    return direction * (end_close - start_close) / atr / (end - start + 1)


def fvg_gap_at(frame: pd.DataFrame, index: int, atr: float | None) -> tuple[float | None, float | None]:
    if index < 2 or atr is None or atr <= 0:
        return None, None
    low = value_at(frame, index, "low")
    high = value_at(frame, index, "high")
    high_two_back = value_at(frame, index - 2, "high")
    low_two_back = value_at(frame, index - 2, "low")
    bull_gap = (low - high_two_back) / atr if low is not None and high_two_back is not None and low > high_two_back else None
    bear_gap = (low_two_back - high) / atr if high is not None and low_two_back is not None and high < low_two_back else None
    return bull_gap, bear_gap


def compute_phase(
    frame: pd.DataFrame,
    start_index: int | None,
    end_index: int | None,
    direction: int,
    atr: float | None,
    start_price: float | None = None,
    end_price: float | None = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {metric: None for metric in PHASE_METRICS}
    if start_index is None or end_index is None or start_index < 0 or end_index < start_index or atr is None or atr <= 0:
        out["available"] = 0
        return out
    start_index = max(0, start_index)
    end_index = min(len(frame) - 1, end_index)
    if end_index < start_index:
        out["available"] = 0
        return out

    start_ref = start_price if start_price is not None else value_at(frame, start_index, "open")
    end_ref = end_price if end_price is not None else value_at(frame, end_index, "close")
    if start_ref is None or end_ref is None:
        out["available"] = 0
        return out

    ranges: List[float] = []
    bodies: List[float] = []
    body_ratios: List[float] = []
    close_pressures: List[float] = []
    relevant_wicks: List[float] = []
    opposite_wicks: List[float] = []
    favorable_gaps: List[float] = []
    opposite_gaps: List[float] = []
    highs: List[float] = []
    lows: List[float] = []
    directional_closes = 0
    directional_bodies = 0
    strong_displacement = 0
    strong_directional_body = 0
    consecutive = 0
    max_consecutive = 0
    path_range_sum = 0.0

    for absolute_index in range(start_index, end_index + 1):
        open_ = value_at(frame, absolute_index, "open")
        high = value_at(frame, absolute_index, "high")
        low = value_at(frame, absolute_index, "low")
        close = value_at(frame, absolute_index, "close")
        if open_ is None or high is None or low is None or close is None:
            consecutive = 0
            continue
        candle_range = high - low
        body = abs(close - open_)
        if candle_range <= 0:
            consecutive = 0
            continue
        range_atr = candle_range / atr
        body_atr = body / atr
        body_ratio = body / candle_range
        ranges.append(range_atr)
        bodies.append(body_atr)
        body_ratios.append(body_ratio)
        path_range_sum += range_atr
        highs.append(high)
        lows.append(low)

        directional = close > open_ if direction > 0 else close < open_
        if directional:
            directional_closes += 1
            consecutive += 1
            max_consecutive = max(max_consecutive, consecutive)
        else:
            consecutive = 0
        if directional and body_ratio >= 0.5:
            directional_bodies += 1
        if range_atr >= 1.0:
            strong_displacement += 1
        if directional and body_ratio >= 0.6 and range_atr >= 0.6:
            strong_directional_body += 1

        close_pressure = (close - low) / candle_range if direction > 0 else (high - close) / candle_range
        relevant_wick = (min(open_, close) - low) / candle_range if direction > 0 else (high - max(open_, close)) / candle_range
        opposite_wick = (high - max(open_, close)) / candle_range if direction > 0 else (min(open_, close) - low) / candle_range
        close_pressures.append(close_pressure)
        relevant_wicks.append(relevant_wick)
        opposite_wicks.append(opposite_wick)

        bull_gap, bear_gap = fvg_gap_at(frame, absolute_index, atr)
        favorable = bull_gap if direction > 0 else bear_gap
        opposite = bear_gap if direction > 0 else bull_gap
        if favorable is not None:
            favorable_gaps.append(favorable)
        if opposite is not None:
            opposite_gaps.append(opposite)

    bars = end_index - start_index + 1
    net_atr = direction * (end_ref - start_ref) / atr
    favorable_excursion = direction * ((max(highs) if direction > 0 else min(lows)) - start_ref) / atr if highs and lows else None
    adverse_excursion = -direction * ((min(lows) if direction > 0 else max(highs)) - start_ref) / atr if highs and lows else None
    efficiency = abs(net_atr) / path_range_sum if path_range_sum > 0 else None
    mid = start_index + (end_index - start_index) // 2
    first_slope = segment_slope(frame, start_index, mid, direction, atr)
    second_slope = segment_slope(frame, mid, end_index, direction, atr)

    out.update(
        {
            "available": 1,
            "bars": bars,
            "net_atr": net_atr,
            "abs_net_atr": abs(net_atr),
            "slope_atr_per_bar": net_atr / bars if bars else None,
            "path_range_atr_sum": path_range_sum,
            "path_efficiency": efficiency,
            "favorable_excursion_atr": favorable_excursion,
            "adverse_excursion_atr": adverse_excursion,
            "adverse_to_favorable_ratio": safe_div(adverse_excursion, favorable_excursion),
            "range_atr_mean": mean_or_none(ranges),
            "range_atr_max": max_or_none(ranges),
            "body_atr_mean": mean_or_none(bodies),
            "body_atr_max": max_or_none(bodies),
            "body_to_range_mean": mean_or_none(body_ratios),
            "body_to_range_max": max_or_none(body_ratios),
            "directional_close_fraction": directional_closes / bars if bars else None,
            "directional_body_fraction": directional_bodies / bars if bars else None,
            "directional_close_pressure_mean": mean_or_none(close_pressures),
            "directional_close_pressure_max": max_or_none(close_pressures),
            "relevant_wick_ratio_mean": mean_or_none(relevant_wicks),
            "relevant_wick_ratio_max": max_or_none(relevant_wicks),
            "opposite_wick_ratio_mean": mean_or_none(opposite_wicks),
            "opposite_wick_ratio_max": max_or_none(opposite_wicks),
            "strong_displacement_count": strong_displacement,
            "strong_directional_body_count": strong_directional_body,
            "max_consecutive_directional_closes": max_consecutive,
            "favorable_fvg_count": len(favorable_gaps),
            "opposite_fvg_count": len(opposite_gaps),
            "favorable_fvg_gap_atr_sum": sum(favorable_gaps),
            "favorable_fvg_gap_atr_max": max_or_none(favorable_gaps),
            "first_half_slope_atr_per_bar": first_slope,
            "second_half_slope_atr_per_bar": second_slope,
            "acceleration_atr_per_bar": second_slope - first_slope if second_slope is not None and first_slope is not None else None,
        }
    )
    return out


def add_phase_to_row(row: Dict[str, Any], phase_name: str, values: Dict[str, Any]) -> None:
    for metric in PHASE_METRICS:
        row[f"eqp_{phase_name}_{metric}"] = values.get(metric)


def phase_quality_features(
    frame: pd.DataFrame,
    decision_index: int | None,
    t3_index: int | None,
    t2_index: int | None,
    t1_index: int | None,
    signal_high_index: int | None,
    row: Dict[str, Any],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"eqp_data_available": 0}
    atr = row.get("tech_atr20") or (value_at(frame, decision_index, "atr20") if decision_index is not None else None)
    if atr is None or atr <= 0:
        return out

    side = str(row.get("side") or row.get("direction") or "long").lower()
    is_short = side == "short"
    favorable_direction = -1 if is_short else 1
    sweep_direction = 1 if is_short else -1
    signal_price = row.get("signal_price")
    t3_low = row.get("t3_low_price")
    t2_high = row.get("t2_high_price")
    sweep_low = row.get("t1_sweep_low_price")
    signal_high = row.get("signal_high_price")
    sig_i = signal_high_index if signal_high_index is not None else decision_index

    phase_values = {
        "impulse_t3_to_t2": compute_phase(frame, t3_index, t2_index, favorable_direction, atr, t3_low, t2_high),
        "sweep_t2_to_t1": compute_phase(frame, t2_index, t1_index, sweep_direction, atr, t2_high, sweep_low),
        "reversal_t1_to_signal": compute_phase(frame, t1_index, sig_i, favorable_direction, atr, sweep_low, signal_high or signal_price),
        "full_t3_to_signal": compute_phase(frame, t3_index, sig_i, favorable_direction, atr, t3_low, signal_high or signal_price),
        "pre_sweep_6": compute_phase(
            frame,
            (t1_index - 6 if t1_index is not None else None),
            (t1_index - 1 if t1_index is not None else None),
            sweep_direction,
            atr,
        ),
        "pre_signal_6": compute_phase(
            frame,
            max(0, sig_i - 6) if sig_i is not None else None,
            max(0, sig_i - 1) if sig_i is not None else None,
            favorable_direction,
            atr,
        ),
    }
    out["eqp_data_available"] = int(any(values.get("available") == 1 for values in phase_values.values()))
    for phase_name, values in phase_values.items():
        add_phase_to_row(out, phase_name, values)

    reversal = phase_values["reversal_t1_to_signal"]
    sweep = phase_values["sweep_t2_to_t1"]
    impulse = phase_values["impulse_t3_to_t2"]
    full = phase_values["full_t3_to_signal"]
    rev_speed = as_float(reversal.get("slope_atr_per_bar"))
    sweep_speed = as_float(sweep.get("slope_atr_per_bar"))
    rev_abs = as_float(reversal.get("abs_net_atr"))
    sweep_abs = as_float(sweep.get("abs_net_atr"))
    impulse_abs = as_float(impulse.get("abs_net_atr"))
    rev_eff = as_float(reversal.get("path_efficiency"))
    sweep_eff = as_float(sweep.get("path_efficiency"))
    rev_pressure = as_float(reversal.get("directional_close_pressure_mean"))
    sweep_pressure = as_float(sweep.get("directional_close_pressure_mean"))
    sweep_bars = as_float(sweep.get("bars"))
    rev_bars = as_float(reversal.get("bars"))
    full_bars = as_float(full.get("bars"))
    rev_displacements = as_float(reversal.get("strong_displacement_count")) or 0.0
    denom = favorable_direction * (signal_high - sweep_low) if signal_high is not None and sweep_low is not None else None

    out["eqp_reversal_vs_sweep_speed_ratio"] = safe_div(rev_speed, sweep_speed)
    out["eqp_reversal_vs_sweep_abs_range_ratio"] = safe_div(rev_abs, sweep_abs)
    out["eqp_reversal_efficiency_minus_sweep_efficiency"] = rev_eff - sweep_eff if rev_eff is not None and sweep_eff is not None else None
    out["eqp_reversal_body_pressure_minus_sweep_body_pressure"] = (
        rev_pressure - sweep_pressure if rev_pressure is not None and sweep_pressure is not None else None
    )
    out["eqp_sweep_depth_vs_impulse_atr_ratio"] = safe_div(sweep_abs, impulse_abs)
    out["eqp_sweep_bars_share_of_setup"] = safe_div(sweep_bars, full_bars)
    out["eqp_reversal_bars_share_of_setup"] = safe_div(rev_bars, full_bars)
    out["eqp_signal_reclaim_t2_close_atr"] = (
        safe_div(favorable_direction * (signal_price - t2_high), atr) if signal_price is not None and t2_high is not None else None
    )
    out["eqp_signal_reclaim_t2_high_atr"] = (
        safe_div(favorable_direction * (signal_high - t2_high), atr) if signal_high is not None and t2_high is not None else None
    )
    out["eqp_signal_reversal_from_sweep_pct"] = (
        safe_div(favorable_direction * (signal_price - sweep_low), denom) if signal_price is not None and sweep_low is not None else None
    )
    out["eqp_signal_close_above_t2_high"] = (
        int(favorable_direction * (signal_price - t2_high) > 0) if signal_price is not None and t2_high is not None else None
    )
    out["eqp_signal_high_above_t2_high"] = (
        int(favorable_direction * (signal_high - t2_high) > 0) if signal_high is not None and t2_high is not None else None
    )
    out["eqp_reversal_displacement_x_efficiency"] = rev_displacements * rev_eff if rev_eff is not None else None
    out["eqp_sweep_displacement_x_inefficiency"] = (
        (as_float(sweep.get("strong_displacement_count")) or 0.0) * (1.0 - sweep_eff) if sweep_eff is not None else None
    )
    return out


def add_short_entry_quality_fields(
    row: Dict[str, Any],
    frame: pd.DataFrame,
    t3_index: int | None,
    t2_index: int | None,
    t1_index: int | None,
    signal_index: int | None,
) -> None:
    if str(row.get("side") or row.get("direction") or "").strip().lower() != "short":
        return
    atr = as_float(row.get("tech_atr20"))
    signal_price = as_float(row.get("signal_price"))
    sweep_high = as_float(row.get("t1_sweep_high_price")) or as_float(row.get("t1_sweep_low_price"))
    t3_high = as_float(row.get("t3_high_price")) or as_float(row.get("t3_low_price"))
    t2_low = as_float(row.get("t2_low_price")) or as_float(row.get("t2_high_price"))
    signal_low = as_float(row.get("signal_low_price")) or as_float(row.get("signal_high_price")) or signal_price

    row["eq_sweep_to_signal_bars"] = signal_index - t1_index if signal_index is not None and t1_index is not None else None
    row["eq_t3_to_t2_bars"] = t2_index - t3_index if t2_index is not None and t3_index is not None else None
    row["eq_t2_to_sweep_bars"] = t1_index - t2_index if t1_index is not None and t2_index is not None else None
    row["eq_sweep_high_to_signal_close_atr"] = safe_div(sweep_high - signal_price, atr) if sweep_high is not None and signal_price is not None else None
    row["eq_sweep_high_to_signal_low_atr"] = safe_div(sweep_high - signal_low, atr) if sweep_high is not None and signal_low is not None else None
    row["eq_sweep_depth_above_t3_atr"] = safe_div(sweep_high - t3_high, atr) if sweep_high is not None and t3_high is not None else None
    row["eq_signal_closes_below_t2_low"] = int(signal_price < t2_low) if signal_price is not None and t2_low is not None else None
    row["eq_signal_low_breaks_t2_low_atr"] = safe_div(t2_low - signal_low, atr) if t2_low is not None and signal_low is not None else None
    row["eq_signal_close_vs_sweep_range_pct"] = (
        safe_div(sweep_high - signal_price, sweep_high - t2_low)
        if sweep_high is not None and signal_price is not None and t2_low is not None
        else None
    )

    if signal_index is not None and 0 <= signal_index < len(frame):
        candle = frame.iloc[signal_index]
        open_ = as_float(candle.get("open"))
        high = as_float(candle.get("high"))
        low = as_float(candle.get("low"))
        close = as_float(candle.get("close"))
        candle_range = high - low if high is not None and low is not None else None
        body = abs(close - open_) if close is not None and open_ is not None else None
        row["eq_signal_range_atr"] = safe_div(candle_range, atr)
        row["eq_signal_body_atr"] = safe_div(body, atr)
        row["eq_signal_body_to_range"] = safe_div(body, candle_range)
        row["eq_signal_bearish_close_location"] = safe_div(high - close, candle_range) if high is not None and close is not None else None
        row["eq_signal_lower_wick_ratio"] = (
            safe_div(min(open_, close) - low, candle_range) if open_ is not None and close is not None and low is not None else None
        )
        row["eq_signal_upper_wick_ratio"] = (
            safe_div(high - max(open_, close), candle_range) if open_ is not None and close is not None and high is not None else None
        )
        for hours in [3, 6, 12]:
            old_close = value_at(frame, signal_index - hours, "close")
            row[f"eq_prior_{hours}bar_return_atr"] = safe_div(old_close - close, atr) if old_close is not None and close is not None else None
        prior6 = abs(as_float(row.get("eq_prior_6bar_return_atr")) or 0.0)
        row["eq_short_reversal_impulse_vs_prior_6bar_abs"] = safe_div(row.get("eq_sweep_high_to_signal_close_atr"), prior6)
        for hours in [6, 24]:
            old_close = value_at(frame, signal_index - hours, "close")
            row[f"eq_regime_return_{hours}h_atr"] = safe_div(close - old_close, atr) if old_close is not None and close is not None else None
        ema20 = value_at(frame, signal_index, "ema20")
        ema50 = value_at(frame, signal_index, "ema50")
        ema20_prev10 = value_at(frame, signal_index - 10, "ema20") if signal_index >= 10 else None
        high20 = as_float(frame["high"].iloc[max(0, signal_index - 19) : signal_index + 1].max())
        low20 = as_float(frame["low"].iloc[max(0, signal_index - 19) : signal_index + 1].min())
        row["eq_regime_close_vs_ema20_atr"] = safe_div(close - ema20, atr) if close is not None and ema20 is not None else None
        row["eq_regime_close_vs_ema50_atr"] = safe_div(close - ema50, atr) if close is not None and ema50 is not None else None
        row["eq_regime_ema20_slope_10_atr"] = safe_div(ema20 - ema20_prev10, atr) if ema20 is not None and ema20_prev10 is not None else None
        row["eq_regime_ema20_below_ema50"] = int(ema20 < ema50) if ema20 is not None and ema50 is not None else None
        row["eq_regime_close_below_ema20"] = int(close < ema20) if close is not None and ema20 is not None else None
        row["eq_regime_close_below_ema50"] = int(close < ema50) if close is not None and ema50 is not None else None
        row["eq_regime_atr20_pct_of_price"] = safe_div(atr, close)
        row["eq_regime_range20_position"] = safe_div(close - low20, high20 - low20) if close is not None and low20 is not None and high20 is not None else None

    row["eq_cohort_signals_same_bar"] = row.get("macro_signal_count_same_bar")
    row["eq_cohort_signals_3h"] = row.get("macro_signal_count_3h")
    row["eq_cohort_signals_6h"] = row.get("macro_signal_count_6h")
    row["eq_cohort_signals_24h"] = row.get("macro_signal_count_24h")
    row["eq_cohort_unique_tickers_3h"] = row.get("macro_signal_unique_tickers_3h")
    row["eq_cohort_unique_tickers_6h"] = row.get("macro_signal_unique_tickers_6h")
    row["eq_cohort_unique_tickers_24h"] = row.get("macro_signal_unique_tickers_24h")
    row["eq_cohort_legacy_score_mean_6h"] = row.get("legacy_signal_score")
    row["eq_cohort_legacy_score_p75_6h"] = row.get("legacy_signal_score")
    row["eq_cohort_legacy_score_percentile_24h"] = row.get("macro_signal_crowding_percentile_24h")
    row["eq_cohort_strong_signal_fraction_24h"] = 1.0 if (as_float(row.get("legacy_signal_score")) or 0.0) >= 60.0 else 0.0
    row["eq_cohort_same_ticker_signals_24h"] = row.get("macro_signal_same_ticker_count_24h")

    entry = as_float(row.get("entry_price"))
    stop = as_float(row.get("stop_price"))
    risk = as_float(row.get("risk"))
    base_ish = as_float(row.get("base_ish_price"))
    deeper_ish = as_float(row.get("deeper_ish_price"))
    delay = as_float(row.get("entry_variant_delay_bars"))
    for prefix in ["eq_range50_entry", "eq_fvg_current_entry"]:
        row[f"{prefix}_filled_known"] = 1
        row[f"{prefix}_delay_bars"] = delay
        row[f"{prefix}_distance_from_signal_atr"] = safe_div(signal_price - entry, atr) if signal_price is not None and entry is not None else None
        row[f"{prefix}_risk_atr"] = safe_div(risk, atr)
        row[f"{prefix}_stop_to_base_ish_atr"] = safe_div(stop - base_ish, atr) if stop is not None and base_ish is not None else None
        row[f"{prefix}_stop_to_deeper_ish_atr"] = safe_div(stop - deeper_ish, atr) if stop is not None and deeper_ish is not None else None
        row[f"{prefix}_signal_to_stop_atr"] = safe_div(stop - signal_price, atr) if stop is not None and signal_price is not None else None
        row[f"{prefix}_signal_to_1r_atr"] = safe_div(risk, atr)
        row[f"{prefix}_signal_to_2r_atr"] = safe_div(2 * risk if risk is not None else None, atr)


def technical_features(frame: pd.DataFrame, index: int | None) -> Dict[str, Any]:
    if index is None or not (0 <= index < len(frame)):
        return {}
    row = frame.iloc[index]
    atr20 = as_float(row.get("atr20"))
    close = as_float(row.get("close"))
    high = as_float(row.get("high"))
    low = as_float(row.get("low"))
    open_ = as_float(row.get("open"))
    prev_close = value_at(frame, index - 1, "close") if index > 0 else close
    candle_range = (high - low) if high is not None and low is not None else None
    body = abs(close - open_) if close is not None and open_ is not None else None

    out: Dict[str, Any] = {}
    out["tech_data_available"] = 1
    for hours in [1, 3, 6, 12, 24]:
        old = value_at(frame, index - hours, "close")
        out[f"tech_close_return_{hours}h"] = safe_div(close - old, old) if close is not None and old is not None else None

    out["tech_candle_range_atr20"] = safe_div(candle_range, atr20)
    out["tech_candle_body_atr20"] = safe_div(body, atr20)
    out["tech_candle_body_to_range"] = safe_div(body, candle_range)
    out["tech_candle_upper_wick_to_range"] = (
        safe_div(high - max(open_, close), candle_range)
        if high is not None and open_ is not None and close is not None
        else None
    )
    out["tech_candle_lower_wick_to_range"] = (
        safe_div(min(open_, close) - low, candle_range)
        if low is not None and open_ is not None and close is not None
        else None
    )
    out["tech_candle_close_position"] = safe_div(close - low, candle_range) if close is not None and low is not None else None
    out["tech_gap_from_prev_close_atr20"] = safe_div(open_ - prev_close, atr20) if open_ is not None and prev_close is not None else None
    out["tech_atr14"] = as_float(row.get("atr14"))
    out["tech_atr20"] = atr20
    out["tech_atr20_pct_of_price"] = safe_div(atr20, close)
    out["tech_atr20_percentile_100"] = percentile_last(frame["atr20"], index, 100)
    out["tech_volatility20"] = as_float(row.get("volatility20"))
    out["tech_volatility50"] = as_float(row.get("volatility50"))
    out["tech_volatility20_percentile_100"] = percentile_last(frame["volatility20"], index, 100)

    for window in [20, 50]:
        start = max(0, index - window + 1)
        high_n = as_float(frame["high"].iloc[start : index + 1].max())
        low_n = as_float(frame["low"].iloc[start : index + 1].min())
        if window == 20:
            out["tech_high20_breakout"] = int(close is not None and high_n is not None and close >= high_n)
            out["tech_low20_breakdown"] = int(close is not None and low_n is not None and close <= low_n)
        out[f"tech_close_position_{window}"] = safe_div(close - low_n, high_n - low_n) if close is not None else None

    above_count = 0
    for window in [8, 13, 21, 34, 50, 100, 200]:
        ema = as_float(row.get(f"ema{window}"))
        out[f"tech_ema{window}_distance_atr20"] = safe_div(close - ema, atr20) if close is not None else None
        out[f"tech_ema{window}_distance_pct"] = safe_div(close - ema, close) if close is not None else None
        out[f"tech_ema{window}_above"] = int(close is not None and ema is not None and close > ema)
        if close is not None and ema is not None and close > ema:
            above_count += 1
        for slope_window in [5, 10]:
            old_ema = value_at(frame, index - slope_window, f"ema{window}")
            out[f"tech_ema{window}_slope_{slope_window}"] = (
                safe_div(ema - old_ema, old_ema) if ema is not None and old_ema is not None else None
            )
    out["tech_ema_bear_stack_8_13_21"] = int(
        all(as_float(row.get(f"ema{w}")) is not None for w in [8, 13, 21])
        and row["ema8"] < row["ema13"] < row["ema21"]
    )
    out["tech_ema_bull_stack_8_13_21"] = int(
        all(as_float(row.get(f"ema{w}")) is not None for w in [8, 13, 21])
        and row["ema8"] > row["ema13"] > row["ema21"]
    )
    out["tech_ema_bear_stack_8_13_21_50"] = int(
        all(as_float(row.get(f"ema{w}")) is not None for w in [8, 13, 21, 50])
        and row["ema8"] < row["ema13"] < row["ema21"] < row["ema50"]
    )
    out["tech_ema_bull_stack_8_13_21_50"] = int(
        all(as_float(row.get(f"ema{w}")) is not None for w in [8, 13, 21, 50])
        and row["ema8"] > row["ema13"] > row["ema21"] > row["ema50"]
    )
    out["tech_ema_above_count"] = above_count
    out["tech_ema_fast_slow_spread_atr20"] = safe_div(
        as_float(row.get("ema8")) - as_float(row.get("ema50")) if as_float(row.get("ema8")) is not None and as_float(row.get("ema50")) is not None else None,
        atr20,
    )
    out["tech_ema_21_50_spread_atr20"] = safe_div(
        as_float(row.get("ema21")) - as_float(row.get("ema50")) if as_float(row.get("ema21")) is not None and as_float(row.get("ema50")) is not None else None,
        atr20,
    )

    for window in [20, 50]:
        upper = as_float(row.get(f"bb{window}_upper"))
        lower = as_float(row.get(f"bb{window}_lower"))
        width = as_float(row.get(f"bb{window}_width"))
        out[f"tech_bb{window}_percent_b"] = safe_div(close - lower, upper - lower) if close is not None else None
        out[f"tech_bb{window}_width_atr20"] = safe_div(width, atr20)
        out[f"tech_bb{window}_width_pct"] = safe_div(width, close)
        out[f"tech_bb{window}_width_percentile_100"] = percentile_last(frame[f"bb{window}_width"], index, 100)
        out[f"tech_bb{window}_distance_upper_atr20"] = safe_div(upper - close, atr20) if close is not None else None
        out[f"tech_bb{window}_distance_lower_atr20"] = safe_div(close - lower, atr20) if close is not None else None
        out[f"tech_bb{window}_above_upper"] = int(close is not None and upper is not None and close > upper)
        out[f"tech_bb{window}_below_lower"] = int(close is not None and lower is not None and close < lower)
    return out


def base_event_features(event: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    side = str(event.get("side") or event.get("direction") or "long").strip().lower()
    is_short = side == "short"
    signal_price = as_float(event.get("entry_price"))
    out["signal_price"] = signal_price
    out["v2_short_compatibility_aliases_applied"] = 0
    for time_key in [
        "t3_low_time",
        "t2_high_time",
        "t1_sweep_low_time",
        "signal_high_time",
        "t3_high_time",
        "t2_low_time",
        "t1_sweep_high_time",
        "signal_low_time",
    ]:
        out[time_key] = as_int(event.get(time_key))
    for src, dest in [
        ("legacy_signal_score", "legacy_signal_score"),
        ("legacy_ratio", "legacy_ratio"),
        ("legacy_atr_ratio", "legacy_atr_ratio"),
        ("legacy_fvg_atr", "legacy_fvg_atr"),
        ("legacy_isl_level", "isl_level"),
        ("t3_low_price", "t3_low_price"),
        ("t2_high_price", "t2_high_price"),
        ("t1_sweep_low_price", "t1_sweep_low_price"),
        ("signal_high_price", "signal_high_price"),
        ("current_isl_price", "current_isl_price"),
        ("base_isl_price", "base_isl_price"),
        ("base_ish_price", "base_ish_price"),
        ("deeper_isl_price", "deeper_isl_price"),
    ]:
        out[dest] = as_float(event.get(src))
    out["isl_sweep"] = int(str(event.get("legacy_isl_sweep", "")).lower() in {"true", "1", "yes"})
    out["bull_fvg_lower"] = as_float(event.get("bull_fvg_lower_price"))
    out["bull_fvg_upper"] = as_float(event.get("bull_fvg_upper_price"))
    if not is_short:
        protected_low = (
            out.get("current_isl_price")
            or out.get("base_isl_price")
            or out.get("t1_sweep_low_price")
            or as_float(event.get("stop_price"))
        )
        if protected_low is not None:
            out["current_isl_price"] = out.get("current_isl_price") or protected_low
            out["base_isl_price"] = out.get("base_isl_price") or protected_low
            out["isl_level"] = out.get("isl_level") or protected_low

    if is_short:
        out["v2_short_compatibility_aliases_applied"] = 1
        out["t3_high_price"] = as_float(event.get("t3_high_price"))
        out["t2_low_price"] = as_float(event.get("t2_low_price"))
        out["t1_sweep_high_price"] = as_float(event.get("t1_sweep_high_price"))
        out["signal_low_price"] = as_float(event.get("signal_low_price"))
        out["current_ish_price"] = as_float(event.get("current_ish_price"))
        out["deeper_ish_price"] = as_float(event.get("deeper_ish_price"))
        out["ish_level"] = out["current_ish_price"]
        out["protected_high_price"] = out["current_ish_price"] or as_float(event.get("stop_price"))
        out["bear_fvg_lower"] = as_float(event.get("bear_fvg_lower_price"))
        out["bear_fvg_upper"] = as_float(event.get("bear_fvg_upper_price"))
        # The approved live feature contract is currently long-side named. These
        # aliases let short rows pass through V2 audits without being force-scored.
        # Side-aware inference still blocks short classification until a short
        # feature contract is explicitly audited.
        out["t3_low_price"] = out["t3_low_price"] if out["t3_low_price"] is not None else out["t3_high_price"]
        out["t2_high_price"] = out["t2_high_price"] if out["t2_high_price"] is not None else out["t2_low_price"]
        out["t1_sweep_low_price"] = (
            out["t1_sweep_low_price"] if out["t1_sweep_low_price"] is not None else out["t1_sweep_high_price"]
        )
        out["signal_high_price"] = out["signal_high_price"] if out["signal_high_price"] is not None else out["signal_low_price"]
        out["current_isl_price"] = (
            out["current_isl_price"] if out["current_isl_price"] is not None else as_float(event.get("current_ish_price"))
        )
        out["deeper_isl_price"] = (
            out["deeper_isl_price"] if out["deeper_isl_price"] is not None else as_float(event.get("deeper_ish_price"))
        )
        out["bull_fvg_lower"] = out["bull_fvg_lower"] if out["bull_fvg_lower"] is not None else out["bear_fvg_lower"]
        out["bull_fvg_upper"] = out["bull_fvg_upper"] if out["bull_fvg_upper"] is not None else out["bear_fvg_upper"]

    metric_map = {
        "bull_fvg_fill": "bull_fvg_fill",
        "bull_fvg_age": "bull_fvg_age",
        "current_isl_swept": "current_isl_swept",
        "protected_lows": "protected_lows",
        "low_hold": "low_hold",
        "range_quality": "range_quality",
        "range_width_atr": "range_width_atr",
        "range_age": "range_age",
        "target_highs": "target_highs",
        "target_distance_atr": "target_distance_atr",
        "deeper_isl_atr": "deeper_isl_atr",
        "active_bull_fvgs": "active_bull_fvgs",
    }
    for metric_key, feature_name in metric_map.items():
        out[feature_name] = as_float(metrics.get(metric_key))
    out["current_isl_swept"] = int(bool(metrics.get("current_isl_swept"))) if metrics.get("current_isl_swept") is not None else out.get("current_isl_swept")
    if is_short:
        out["target_lows"] = as_float(metrics.get("target_highs"))
        out["active_bear_fvgs"] = as_float(metrics.get("active_bull_fvgs"))
        out["bear_fvg_age"] = as_float(metrics.get("bull_fvg_age"))
        out["bear_fvg_fill"] = as_float(metrics.get("bull_fvg_fill"))
        out["tech_engine_target_lows"] = out["target_lows"]
        out["tech_engine_active_bear_fvgs"] = out["active_bear_fvgs"]
        out["tech_engine_bear_fvg_age"] = out["bear_fvg_age"]

    for metric_key in [
        "deeper_isl_count",
        "low_clusters",
        "low_hold",
        "low_spacing_atr",
        "low_touches",
        "low_vol",
        "range_active",
        "range_age",
        "range_internal_ish",
        "range_internal_isl",
        "range_lower",
        "range_quality",
        "range_upper",
        "range_width_atr",
        "target_clusters",
        "target_distance_atr",
        "target_highs",
        "target_hold",
        "target_spacing_atr",
        "target_touches",
        "target_vol",
    ]:
        out[f"metric_{metric_key}"] = as_float(metrics.get(metric_key))

    t3 = out.get("t3_low_price")
    t2 = out.get("t2_high_price")
    t1 = out.get("t1_sweep_low_price")
    signal_high = out.get("signal_high_price")
    lower = out.get("bull_fvg_lower")
    upper = out.get("bull_fvg_upper")
    out["signal_range_atr_proxy"] = (
        out.get("legacy_atr_ratio") * out.get("legacy_fvg_atr")
        if out.get("legacy_atr_ratio") is not None and out.get("legacy_fvg_atr") is not None
        else None
    )
    out["sweep_to_signal_range"] = signal_high - t1 if signal_high is not None and t1 is not None else None
    out["sweep_to_signal_return"] = safe_div(signal_price - t1, t1) if signal_price is not None and t1 is not None else None
    out["t3_to_t2_rise"] = t2 - t3 if t2 is not None and t3 is not None else None
    out["t2_to_t1_drop"] = t2 - t1 if t2 is not None and t1 is not None else None
    out["t1_sweep_depth_vs_t3"] = t3 - t1 if t3 is not None and t1 is not None else None
    if is_short:
        out["t3_to_t2_drop"] = t3 - t2 if t2 is not None and t3 is not None else None
        out["t2_to_t1_rise"] = t1 - t2 if t2 is not None and t1 is not None else None
        out["metric_down_leg"] = out["t3_to_t2_drop"]
        out["metric_retrace"] = out["t2_to_t1_rise"]
        out["metric_target_lows"] = out.get("target_lows")
        out["metric_bearish_overlap_fvg_count"] = out.get("active_bear_fvgs")
    out["fvg_zone_size"] = upper - lower if upper is not None and lower is not None else None
    out["fvg_midpoint"] = lower + out["fvg_zone_size"] / 2 if lower is not None and out.get("fvg_zone_size") else None
    out["fvg_position_of_sweep"] = (
        safe_div(t1 - lower, out.get("fvg_zone_size")) if t1 is not None and lower is not None else None
    )
    return out


def fvg_reaction_features(row: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    atr = row.get("tech_atr20")
    lower = row.get("bull_fvg_lower")
    upper = row.get("bull_fvg_upper")
    size = row.get("fvg_zone_size")
    mid = row.get("fvg_midpoint")
    sweep_low = row.get("t1_sweep_low_price")
    t2_high = row.get("t2_high_price")
    signal_price = row.get("signal_price")
    signal_high = row.get("signal_high_price") or signal_price
    fill = row.get("bull_fvg_fill")
    age = row.get("bull_fvg_age")
    active_count = row.get("active_bull_fvgs")
    has = lower is not None and upper is not None and size is not None and size > 0

    fill_pct = min(1.0, max(0.0, fill / 100.0 if fill is not None and fill > 1 else (fill or 0.0))) if fill is not None else None
    remaining = 1.0 - fill_pct if fill_pct is not None else None
    age_decay = 1.0 / (1.0 + math.log1p(max(age or 0.0, 0.0))) if age is not None else None
    fresh_active = (remaining or 0.0) * (age_decay or 0.0) * (1.0 + math.log1p(max(active_count or 0.0, 0.0)))
    position = safe_div(sweep_low - lower, size) if has and sweep_low is not None else None
    clipped = clip01(position)
    inside = bool(has and position is not None and 0.0 <= position <= 1.0)
    lower_half = bool(inside and position is not None and position <= 0.5)
    upper_half = bool(inside and position is not None and position > 0.5)
    pierce_pct = max(0.0, safe_div(lower - sweep_low, size) or 0.0) if has and sweep_low is not None else None
    pierce_atr = max(0.0, safe_div(lower - sweep_low, atr) or 0.0) if has and sweep_low is not None else None
    dip_depth_upper = max(0.0, safe_div(upper - sweep_low, size) or 0.0) if has and sweep_low is not None else None
    dip_depth_mid = max(0.0, safe_div(mid - sweep_low, size) or 0.0) if has and mid is not None and sweep_low is not None else None
    signal_position = safe_div(signal_price - lower, size) if has and signal_price is not None else None
    reclaim_mid_pct = safe_div(signal_price - mid, size) if has and signal_price is not None and mid is not None else None
    reclaim_upper_pct = safe_div(signal_price - upper, size) if has and signal_price is not None else None
    signal_high_reclaim_upper_pct = safe_div(signal_high - upper, size) if has and signal_high is not None else None
    reclaim_from_sweep = safe_div(signal_price - sweep_low, size) if has and signal_price is not None and sweep_low is not None else None
    reversal_speed = as_float(row.get("fq_reversal_speed_atr_per_bar"))

    out["fvg_react_has_bull_fvg"] = int(has)
    out["fvg_react_active_bull_fvg_count"] = active_count
    out["fvg_react_size_atr"] = safe_div(size, atr)
    out["fvg_react_fill_pct"] = fill_pct
    out["fvg_react_remaining_pct"] = remaining
    out["fvg_react_age_bars"] = age
    out["fvg_react_age_decay"] = age_decay
    out["fvg_react_fresh_active_score"] = fresh_active
    out["fvg_react_sweep_position_raw"] = position
    out["fvg_react_sweep_position_clipped"] = clipped
    out["fvg_react_dip_depth_from_upper_pct"] = dip_depth_upper
    out["fvg_react_dip_depth_from_mid_pct"] = dip_depth_mid
    out["fvg_react_sweep_lower_half"] = int(lower_half)
    out["fvg_react_sweep_upper_half"] = int(upper_half)
    out["fvg_react_t2_started_above_zone"] = int(has and t2_high is not None and t2_high > upper)
    out["fvg_react_down_leg_entered_from_above"] = int(has and t2_high is not None and t2_high > upper and sweep_low is not None and sweep_low <= upper)
    out["fvg_react_signal_position_raw"] = signal_position
    out["fvg_react_signal_reclaimed_upper"] = int(has and signal_price is not None and signal_price >= upper)
    out["fvg_react_signal_reclaim_mid_pct"] = reclaim_mid_pct
    out["fvg_react_signal_reclaim_upper_pct"] = reclaim_upper_pct
    out["fvg_react_signal_high_reclaim_upper_pct"] = signal_high_reclaim_upper_pct
    out["fvg_react_reclaim_from_sweep_pct"] = reclaim_from_sweep
    out["fvg_react_reclaim_speed_proxy"] = reclaim_from_sweep * reversal_speed if reclaim_from_sweep is not None and reversal_speed is not None else None
    out["fvg_react_respect_and_reclaim_score"] = float(inside) * (clip01(reclaim_upper_pct) or 0.0) * (1.0 - (clip01(pierce_pct) or 0.0))
    out["fvg_react_lower_half_rejection_score"] = float(lower_half) * (clip01(reclaim_from_sweep) or 0.0) * fresh_active
    out["fvg_react_fresh_reclaim_score"] = fresh_active * (clip01((reclaim_upper_pct or 0.0) + (signal_high_reclaim_upper_pct or 0.0)) or 0.0)
    out["fvg_react_quality_composite"] = (
        (out["fvg_react_respect_and_reclaim_score"] or 0.0)
        + (out["fvg_react_lower_half_rejection_score"] or 0.0)
        + (out["fvg_react_fresh_reclaim_score"] or 0.0)
    )
    return out


def entry_features(event: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    entry = as_float(event.get("entry_price"))
    stop = as_float(event.get("stop_price"))
    risk = as_float(event.get("risk"))
    signal_price = row.get("signal_price")
    signal_time = as_int(event.get("signal_time"))
    decision_time = as_int(event.get("decision_time"))
    atr = row.get("tech_atr20")
    sweep_range = row.get("sweep_to_signal_range")
    delay = safe_div(decision_time - signal_time, 3600) if signal_time is not None and decision_time is not None else None
    return {
        "entry_variant_is_range50": int(str(event.get("entry_model_variant", "")).lower() == "range50_only"),
        "entry_variant_is_fvg_current": int("fvg" in str(event.get("entry_model_variant", "")).lower()),
        "entry_variant_entry_price": entry,
        "entry_variant_stop_price": stop,
        "entry_variant_risk": risk,
        "entry_variant_delay_bars": delay,
        "entry_variant_entry_vs_signal_atr": safe_div(entry - signal_price, atr) if entry is not None and signal_price is not None else None,
        "entry_variant_stop_vs_signal_atr": safe_div(stop - signal_price, atr) if stop is not None and signal_price is not None else None,
        "entry_variant_risk_to_signal_range": safe_div(risk, sweep_range),
        "entry_variant_delay_x_reversal": None,
        "entry_variant_delay_x_target_pressure": None,
    }


def macro_ticker_features(frame: pd.DataFrame, index: int | None) -> Dict[str, Any]:
    if index is None:
        return {}
    close = value_at(frame, index, "close")
    out: Dict[str, Any] = {}
    for hours in [1, 3, 6, 12, 24]:
        old = value_at(frame, index - hours, "close")
        out[f"macro_ticker_return_{hours}h"] = safe_div(close - old, old) if close is not None and old is not None else None
    for window in [20, 50]:
        if index >= 0:
            sma = as_float(frame["close"].iloc[max(0, index - window + 1) : index + 1].mean())
            old_sma = as_float(frame["close"].iloc[max(0, index - window - 4) : max(0, index - 4 + 1)].mean()) if index >= 5 else None
            out[f"macro_ticker_above_sma{window}"] = int(close is not None and sma is not None and close > sma)
            out[f"macro_ticker_sma{window}_slope"] = safe_div(sma - old_sma, old_sma) if sma is not None and old_sma is not None else None
    high20 = as_float(frame["high"].iloc[max(0, index - 19) : index + 1].max())
    low20 = as_float(frame["low"].iloc[max(0, index - 19) : index + 1].min())
    atr20 = value_at(frame, index, "atr20")
    out["macro_ticker_range20_atr"] = safe_div(high20 - low20, atr20) if high20 is not None and low20 is not None else None
    out["macro_ticker_volatility20"] = value_at(frame, index, "volatility20")
    return out


def ticker_from_candle_path(path: Path) -> str:
    name = path.stem
    for suffix in ["_1h", "_1H", "_hourly", "_candles"]:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def load_macro_frames(
    candles_path: Path,
    current_frame: pd.DataFrame,
    macro_candles_dir: Path | None,
    events: List[Dict[str, Any]],
) -> tuple[Dict[str, pd.DataFrame], str]:
    fallback_ticker = str((events[0].get("ticker") if events else "") or ticker_from_candle_path(candles_path))
    if not macro_candles_dir:
        return {fallback_ticker: current_frame}, "singleton_current_candle_file"

    frames: Dict[str, pd.DataFrame] = {}
    if macro_candles_dir.exists():
        for path in sorted(macro_candles_dir.glob("*.csv")):
            ticker = ticker_from_candle_path(path)
            try:
                frame = add_candle_indicators(read_candles(path))
            except (OSError, ValueError, pd.errors.EmptyDataError):
                continue
            if not frame.empty:
                frames[ticker] = frame

    if not frames:
        return {fallback_ticker: current_frame}, "singleton_current_candle_file_after_empty_macro_dir"

    if fallback_ticker not in frames:
        frames[fallback_ticker] = current_frame
    return frames, "macro_candles_dir_exact_or_previous"


def macro_observation(frame: pd.DataFrame, timestamp: Any) -> Dict[str, Any] | None:
    index = row_index_at_or_before(frame, timestamp)
    if index is None:
        return None
    close = value_at(frame, index, "close")
    high20 = as_float(frame["high"].iloc[max(0, index - 19) : index + 1].max())
    low20 = as_float(frame["low"].iloc[max(0, index - 19) : index + 1].min())
    prev_high20 = as_float(frame["high"].iloc[max(0, index - 20) : index].max()) if index > 0 else None
    prev_low20 = as_float(frame["low"].iloc[max(0, index - 20) : index].min()) if index > 0 else None
    atr20 = value_at(frame, index, "atr20")
    out = {
        "close": close,
        "range20_atr": safe_div(high20 - low20, atr20) if high20 is not None and low20 is not None else None,
        "volatility20": value_at(frame, index, "volatility20"),
        "breakout20": int(close is not None and prev_high20 is not None and close >= prev_high20),
        "breakdown20": int(close is not None and prev_low20 is not None and close <= prev_low20),
    }
    for hours in [1, 3, 6, 12, 24]:
        old = value_at(frame, index - hours, "close")
        out[f"ret_{hours}h"] = safe_div(close - old, old) if close is not None and old is not None else None
    for window in [20, 50]:
        sma = as_float(frame["close"].iloc[max(0, index - window + 1) : index + 1].mean())
        old_sma = as_float(frame["close"].iloc[max(0, index - window - 4) : max(0, index - 4 + 1)].mean()) if index >= 5 else None
        out[f"above_sma{window}"] = int(close is not None and sma is not None and close > sma)
        out[f"sma{window}_slope"] = safe_div(sma - old_sma, old_sma) if sma is not None and old_sma is not None else None
    return out


def mean(values: List[float]) -> float | None:
    clean = [value for value in values if value is not None and math.isfinite(value)]
    return float(np.mean(clean)) if clean else None


def median(values: List[float]) -> float | None:
    clean = [value for value in values if value is not None and math.isfinite(value)]
    return float(np.median(clean)) if clean else None


def population_std(values: List[float]) -> float | None:
    clean = [value for value in values if value is not None and math.isfinite(value)]
    return float(np.std(clean, ddof=0)) if clean else None


def percentile_rank(value: float | None, values: List[float]) -> float | None:
    clean = sorted(value for value in values if value is not None and math.isfinite(value))
    if value is None or not math.isfinite(value) or not clean:
        return None
    return sum(1 for item in clean if item <= value) / len(clean)


def signal_count_maps(events: List[Dict[str, Any]]) -> tuple[Dict[int, List[Dict[str, Any]]], Dict[str, List[int]], Dict[int, float]]:
    by_time: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    by_ticker: Dict[str, List[int]] = defaultdict(list)
    for event in events:
        timestamp = as_int(event.get("decision_time") or event.get("signal_time"))
        ticker = str(event.get("ticker") or "")
        if timestamp is None:
            continue
        by_time[timestamp].append(event)
        by_ticker[ticker].append(timestamp)

    counts24: Dict[int, int] = {}
    times = sorted(by_time)
    for timestamp in times:
        start = timestamp - 24 * 3600
        counts24[timestamp] = sum(len(by_time[item]) for item in times if start <= item <= timestamp)
    sorted_counts = sorted(counts24.values())
    crowding = {
        timestamp: (sum(1 for value in sorted_counts if value <= count) / len(sorted_counts)) if sorted_counts else None
        for timestamp, count in counts24.items()
    }
    return by_time, by_ticker, crowding


def count_signals_in_window(
    by_time: Dict[int, List[Dict[str, Any]]],
    timestamp: int,
    hours: int,
) -> tuple[int, int]:
    start = timestamp - hours * 3600
    events = [event for time, rows in by_time.items() if start <= time <= timestamp for event in rows]
    return len(events), len(set(str(event.get("ticker") or "") for event in events if event.get("ticker")))


def same_ticker_signal_count(by_ticker: Dict[str, List[int]], ticker: str, timestamp: int, hours: int) -> int:
    start = timestamp - hours * 3600
    return sum(1 for time in by_ticker.get(ticker, []) if start <= time <= timestamp)


def build_macro_context(
    events: List[Dict[str, Any]],
    macro_frames: Dict[str, pd.DataFrame],
    join_method: str,
) -> Dict[str, Dict[str, Any]]:
    by_time, by_ticker, crowding = signal_count_maps(events)
    context: Dict[str, Dict[str, Any]] = {}
    for event in events:
        signal_id = str(event.get("signal_id") or "")
        ticker = str(event.get("ticker") or "")
        timestamp = as_int(event.get("decision_time") or event.get("signal_time"))
        row: Dict[str, Any] = {
            "macro_join_method": join_method,
            "macro_data_available": 0,
        }
        if not signal_id or timestamp is None:
            context[signal_id] = row
            continue

        observations: Dict[str, Dict[str, Any]] = {}
        for macro_ticker, frame in macro_frames.items():
            obs = macro_observation(frame, timestamp)
            if obs is not None:
                observations[macro_ticker] = obs

        ticker_obs = observations.get(ticker)
        if observations:
            row["macro_data_available"] = 1
            row["macro_universe_ticker_count_at_time"] = len(observations)
            for horizon in ["1h", "3h", "6h", "12h", "24h"]:
                values = [as_float(obs.get(f"ret_{horizon}")) for obs in observations.values()]
                if horizon == "1h":
                    row["macro_universe_return_1h_mean"] = mean(values)
                    row["macro_universe_return_1h_median"] = median(values)
                    row["macro_universe_return_1h_std"] = population_std(values)
                    row["macro_universe_return_1h_positive_frac"] = mean([1.0 if value > 0 else 0.0 for value in values if value is not None])
                else:
                    row[f"macro_universe_return_{horizon}_mean"] = mean(values)
            for window in [20, 50]:
                row[f"macro_universe_above_sma{window}_frac"] = mean(
                    [as_float(obs.get(f"above_sma{window}")) for obs in observations.values()]
                )
                row[f"macro_universe_sma{window}_slope_mean"] = mean(
                    [as_float(obs.get(f"sma{window}_slope")) for obs in observations.values()]
                )
            row["macro_universe_range20_atr_mean"] = mean([as_float(obs.get("range20_atr")) for obs in observations.values()])
            row["macro_universe_volatility20_mean"] = mean([as_float(obs.get("volatility20")) for obs in observations.values()])
            row["macro_universe_breakout20_frac"] = mean([as_float(obs.get("breakout20")) for obs in observations.values()])
            row["macro_universe_breakdown20_frac"] = mean([as_float(obs.get("breakdown20")) for obs in observations.values()])

        if ticker_obs is not None:
            for horizon in ["1h", "3h", "6h", "12h", "24h"]:
                row[f"macro_ticker_return_{horizon}"] = ticker_obs.get(f"ret_{horizon}")
            for window in [20, 50]:
                row[f"macro_ticker_above_sma{window}"] = ticker_obs.get(f"above_sma{window}")
                row[f"macro_ticker_sma{window}_slope"] = ticker_obs.get(f"sma{window}_slope")
            row["macro_ticker_range20_atr"] = ticker_obs.get("range20_atr")
            row["macro_ticker_volatility20"] = ticker_obs.get("volatility20")
            for horizon in ["1h", "6h", "24h"]:
                ticker_return = as_float(ticker_obs.get(f"ret_{horizon}"))
                universe_values = [as_float(obs.get(f"ret_{horizon}")) for obs in observations.values()]
                row[f"macro_ticker_relative_return_{horizon}"] = (
                    ticker_return - mean(universe_values)
                    if ticker_return is not None and mean(universe_values) is not None
                    else None
                )
                row[f"macro_ticker_relative_rank_{horizon}"] = percentile_rank(ticker_return, universe_values)

        row["macro_signal_count_same_bar"] = len(by_time.get(timestamp, []))
        for hours in [3, 6, 12, 24]:
            count, unique = count_signals_in_window(by_time, timestamp, hours)
            row[f"macro_signal_count_{hours}h"] = count
            row[f"macro_signal_unique_tickers_{hours}h"] = unique
        row["macro_signal_same_ticker_count_24h"] = same_ticker_signal_count(by_ticker, ticker, timestamp, 24)
        row["macro_signal_crowding_percentile_24h"] = crowding.get(timestamp)
        context[signal_id] = row
    return context


def payload_candle_frame(candles: List[Dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(candles)
    if frame.empty:
        return frame
    required = {"time", "open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        return pd.DataFrame()
    frame = frame[["time", "open", "high", "low", "close"]].copy()
    for column in frame.columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["time", "open", "high", "low", "close"])
    frame["time"] = frame["time"].astype("int64")
    frame = frame.drop_duplicates(subset=["time"], keep="last").sort_values("time").reset_index(drop=True)
    return add_candle_indicators(frame)


def load_liquidity_payload_context(payload_dir: Path | None) -> Dict[str, Dict[str, Any]]:
    if not payload_dir or not payload_dir.exists():
        return {}
    output: Dict[str, Dict[str, Any]] = {}
    for path in sorted(payload_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        signal_id = str(payload.get("signal_id") or "")
        ticker_items = payload.get("tickers") or []
        if not signal_id or not ticker_items:
            continue
        item = ticker_items[0]
        output[signal_id] = {
            "payload": payload,
            "item": item,
            "frame": payload_candle_frame(item.get("candles") or []),
            "path": path,
        }
    return output


def fvg_state_at_decision(fvg: Dict[str, Any], frame: pd.DataFrame, decision_time: int) -> Dict[str, Any] | None:
    lower = as_float(fvg.get("original_lower"))
    upper = as_float(fvg.get("original_upper"))
    created = as_int(fvg.get("created_time") or fvg.get("candle_3_time"))
    if lower is None or upper is None or upper <= lower or created is None or created > decision_time:
        return None
    filled_at = as_int(fvg.get("filled_at"))
    if filled_at is not None and filled_at <= decision_time:
        return None

    remaining_lower = lower
    remaining_upper = upper
    if frame is not None and not frame.empty:
        decision_candles = frame[(frame["time"] > created) & (frame["time"] <= decision_time)]
        if not decision_candles.empty:
            if fvg.get("type") == "bullish":
                min_low = as_float(decision_candles["low"].min())
                if min_low is not None and min_low <= lower:
                    return None
                if min_low is not None and min_low < remaining_upper:
                    remaining_upper = max(lower, min_low)
            else:
                max_high = as_float(decision_candles["high"].max())
                if max_high is not None and max_high >= upper:
                    return None
                if max_high is not None and max_high > remaining_lower:
                    remaining_lower = min(upper, max_high)

    original_size = upper - lower
    remaining_size = max(remaining_upper - remaining_lower, 0.0)
    return {
        "id": fvg.get("id"),
        "lower": remaining_lower,
        "upper": remaining_upper,
        "original_lower": lower,
        "original_upper": upper,
        "midpoint": (remaining_lower + remaining_upper) / 2.0 if remaining_size > 0 else None,
        "remaining_size": remaining_size,
        "fill_pct": 1.0 - remaining_size / original_size if original_size > 0 else None,
        "age_bars": max(0, int((decision_time - created) / 3600)),
        "created_time": created,
        "original_size_atr": as_float(fvg.get("original_size_atr")),
        "displacement_atr": as_float(fvg.get("displacement_atr")),
    }


def payload_active_fvg_features(item: Dict[str, Any], frame: pd.DataFrame, decision_time: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    row_index = row_index_at_or_before(frame, decision_time)
    close = value_at(frame, row_index, "close")
    atr = value_at(frame, row_index, "atr20")
    ledgers = item.get("fvg_ledgers") or {}
    for side in ["bullish", "bearish"]:
        states = []
        for fvg in ledgers.get(side, []) or []:
            state = fvg_state_at_decision(fvg, frame, decision_time)
            if state is not None:
                states.append((fvg, state))
        prefix = "bull" if side == "bullish" else "bear"
        out[f"tech_active_{prefix}_fvg_count_at_decision"] = len(states)
        if not states or close is None:
            continue

        def distance(pair: tuple[Dict[str, Any], Dict[str, Any]]) -> float:
            midpoint = as_float(pair[1].get("midpoint"))
            return abs(close - midpoint) if midpoint is not None else float("inf")

        fvg, state = min(states, key=distance)
        out[f"tech_nearest_{prefix}_fvg_id"] = state.get("id")
        out[f"tech_nearest_{prefix}_fvg_lower"] = as_float(state.get("lower"))
        out[f"tech_nearest_{prefix}_fvg_upper"] = as_float(state.get("upper"))
        out[f"tech_nearest_{prefix}_fvg_original_lower"] = as_float(state.get("original_lower"))
        out[f"tech_nearest_{prefix}_fvg_original_upper"] = as_float(state.get("original_upper"))
        out[f"tech_nearest_{prefix}_fvg_created_time"] = as_float(state.get("created_time"))
        out[f"tech_nearest_{prefix}_fvg_distance_atr20"] = safe_div(distance((fvg, state)), atr)
        out[f"tech_nearest_{prefix}_fvg_remaining_size_atr20"] = safe_div(as_float(state.get("remaining_size")), atr)
        out[f"tech_nearest_{prefix}_fvg_original_size_atr"] = as_float(state.get("original_size_atr"))
        out[f"tech_nearest_{prefix}_fvg_displacement_atr"] = as_float(state.get("displacement_atr"))
        out[f"tech_nearest_{prefix}_fvg_age_bars_at_decision"] = as_float(state.get("age_bars"))
        out[f"tech_nearest_{prefix}_fvg_fill_pct_at_decision"] = as_float(state.get("fill_pct"))
    return out


def available_levels(levels: List[Dict[str, Any]], decision_time: int) -> List[Dict[str, Any]]:
    output = []
    for level in levels or []:
        level_time = as_int(level.get("time") or level.get("source_time"))
        breached_at = as_int(level.get("breached_at"))
        if level_time is None or level_time > decision_time:
            continue
        if breached_at is not None and breached_at <= decision_time:
            continue
        output.append(level)
    return output


def payload_level_context_features(item: Dict[str, Any], frame: pd.DataFrame, decision_time: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    row_index = row_index_at_or_before(frame, decision_time)
    close = value_at(frame, row_index, "close")
    atr = value_at(frame, row_index, "atr20")
    ledgers = item.get("level_ledgers") or {}
    groups = {
        "sh": available_levels(ledgers.get("swing_highs") or [], decision_time),
        "sl": available_levels(ledgers.get("swing_lows") or [], decision_time),
        "ish": available_levels(ledgers.get("ish") or [], decision_time),
        "isl": available_levels(ledgers.get("isl") or [], decision_time),
    }
    out["tech_active_sh_count_at_decision"] = len(groups["sh"])
    out["tech_active_sl_count_at_decision"] = len(groups["sl"])
    out["tech_active_ish_count_at_decision"] = len(groups["ish"])
    out["tech_active_isl_count_at_decision"] = len(groups["isl"])
    if close is not None:
        specs = [
            ("sh", "above", "tech_nearest_active_sh_above_distance_atr20"),
            ("sl", "below", "tech_nearest_active_sl_below_distance_atr20"),
            ("ish", "above", "tech_nearest_active_ish_above_distance_atr20"),
            ("isl", "below", "tech_nearest_active_isl_below_distance_atr20"),
        ]
        for group, direction, field in specs:
            prices = [as_float(level.get("price")) for level in groups[group]]
            if direction == "above":
                candidates = [price for price in prices if price is not None and price > close]
            else:
                candidates = [price for price in prices if price is not None and price < close]
            if candidates:
                nearest = min(candidates, key=lambda price: abs(price - close))
                out[field] = safe_div(abs(nearest - close), atr)

    timeline = item.get("event_timeline") or []
    for hours in [6, 12, 24]:
        start = decision_time - hours * 3600
        out[f"tech_recent_event_count_{hours}h"] = sum(
            1 for event in timeline if start <= (as_int(event.get("time")) or -1) <= decision_time
        )
    recent24 = [event for event in timeline if decision_time - 24 * 3600 <= (as_int(event.get("time")) or -1) <= decision_time]
    out["tech_recent_bullish_fvg_created_24h"] = sum(
        1 for event in recent24 if event.get("event_type") == "bullish_fvg_created"
    )
    out["tech_recent_bearish_fvg_created_24h"] = sum(
        1 for event in recent24 if event.get("event_type") == "bearish_fvg_created"
    )
    out["tech_recent_buy_level_breached_24h"] = sum(
        1 for event in recent24 if event.get("event_type") == "level_breached" and event.get("side") == "buy"
    )
    out["tech_recent_sell_level_breached_24h"] = sum(
        1 for event in recent24 if event.get("event_type") == "level_breached" and event.get("side") == "sell"
    )
    return out


def payload_state_context_features(item: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    premium = item.get("premium_discount") or {}
    zone = premium.get("zone")
    out["tech_premium_discount_active"] = bool_int(premium.get("active"))
    out["tech_premium_discount_position_pct"] = as_float(premium.get("position_pct"))
    for name in ["deep_discount", "discount", "premium", "deep_premium"]:
        out[f"tech_premium_discount_zone_{name}"] = 1 if zone == name else 0

    skeleton = item.get("market_skeleton") or {}
    state = skeleton.get("state")
    out["tech_market_state_uptrend"] = 1 if state == "uptrend" else 0
    out["tech_market_state_downtrend"] = 1 if state == "downtrend" else 0
    out["tech_market_state_range"] = 1 if state in {"range", "sideways", "neutral", "compression"} else 0
    sequence = skeleton.get("recent_sequence") or []
    out["tech_recent_skeleton_hh_count"] = sequence.count("HH")
    out["tech_recent_skeleton_hl_count"] = sequence.count("HL")
    out["tech_recent_skeleton_lh_count"] = sequence.count("LH")
    out["tech_recent_skeleton_ll_count"] = sequence.count("LL")

    structure = item.get("structure_state") or {}
    for key in [
        "active_sell_levels",
        "active_buy_levels",
        "active_isl",
        "active_ish",
        "sell_clusters",
        "buy_clusters",
        "active_bullish_fvgs",
        "active_bearish_fvgs",
    ]:
        out[f"tech_structure_{key}"] = as_float(structure.get(key))

    landscape = item.get("target_liquidity_landscape") or {}
    for side in ["long", "short"]:
        side_data = landscape.get(side) or {}
        out[f"tech_{side}_landscape_candidate_count"] = as_float(side_data.get("candidate_count"))
        out[f"tech_{side}_landscape_cluster_count"] = as_float(side_data.get("cluster_count"))
        nearest = side_data.get("nearest") or {}
        out[f"tech_{side}_nearest_level_age_bars"] = as_float(nearest.get("age_bars"))
        out[f"tech_{side}_nearest_level_touch_count"] = as_float(nearest.get("touch_count"))
        out[f"tech_{side}_nearest_level_fvg_significant"] = bool_int(nearest.get("fvg_significant"))
    return out


def build_payload_context(context_by_signal: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    output: Dict[str, Dict[str, Any]] = {}
    for signal_id, payload_context in context_by_signal.items():
        payload = payload_context.get("payload") or {}
        item = payload_context.get("item") or {}
        frame = payload_context.get("frame")
        decision_time = as_int(payload.get("decision_time"))
        row: Dict[str, Any] = {"liquidity_payload_context_available": 0}
        if not item or frame is None or frame.empty or decision_time is None:
            output[signal_id] = row
            continue
        row["liquidity_payload_context_available"] = 1
        row["dt_liq_payload_available"] = 1
        decision_index = row_index_at_or_before(frame, decision_time)
        if decision_index is not None and 0 <= decision_index < len(frame):
            row["dt_liq_atr20_at_decision"] = value_at(frame, decision_index, "atr20")
        if "time" in frame.columns:
            times = pd.to_numeric(frame["time"], errors="coerce")
            window = frame[times <= decision_time]
            if not window.empty:
                row["dt_liq_candle_count"] = float(len(window))
                row["dt_liq_window_start_time"] = as_int(window.iloc[0].get("time"))
                row["dt_liq_window_end_time"] = as_int(window.iloc[-1].get("time"))
                row["dt_liq_window_span_bars"] = float(max(len(window) - 1, 0))
        row.update(payload_state_context_features(item))
        row.update(payload_active_fvg_features(item, frame, decision_time))
        row.update(payload_level_context_features(item, frame, decision_time))
        output[signal_id] = row
    return output


def apply_payload_fvg_fallbacks(row: Dict[str, Any]) -> None:
    side = str(row.get("side") or row.get("direction") or "long").strip().lower()
    source_prefix = "bear" if side == "short" else "bull"
    lower = as_float(row.get("bull_fvg_lower"))
    upper = as_float(row.get("bull_fvg_upper"))
    payload_lower = as_float(row.get(f"tech_nearest_{source_prefix}_fvg_lower"))
    payload_upper = as_float(row.get(f"tech_nearest_{source_prefix}_fvg_upper"))
    if lower is None and payload_lower is not None:
        row["bull_fvg_lower"] = payload_lower
        lower = payload_lower
    if upper is None and payload_upper is not None:
        row["bull_fvg_upper"] = payload_upper
        upper = payload_upper

    fill = as_float(row.get("bull_fvg_fill"))
    age = as_float(row.get("bull_fvg_age"))
    payload_fill = as_float(row.get(f"tech_nearest_{source_prefix}_fvg_fill_pct_at_decision"))
    payload_age = as_float(row.get(f"tech_nearest_{source_prefix}_fvg_age_bars_at_decision"))
    if fill is None and payload_fill is not None:
        row["bull_fvg_fill"] = payload_fill
        fill = payload_fill
    if age is None and payload_age is not None:
        row["bull_fvg_age"] = payload_age
        age = payload_age
    if as_float(row.get("tech_engine_bull_fvg_fill")) is None and fill is not None:
        row["tech_engine_bull_fvg_fill"] = fill
    if as_float(row.get("tech_engine_bull_fvg_age")) is None and age is not None:
        row["tech_engine_bull_fvg_age"] = age

    if lower is not None and upper is not None and upper > lower:
        row["fvg_zone_size"] = upper - lower
        row["fvg_midpoint"] = lower + (upper - lower) / 2.0
        sweep = as_float(row.get("t1_sweep_low_price"))
        row["fvg_position_of_sweep"] = safe_div(sweep - lower, upper - lower) if sweep is not None else None
        row["fq_bull_fvg_zone_size_atr"] = safe_div(row.get("fvg_zone_size"), row.get("tech_atr20"))
        row["fq_bull_fvg_sweep_position"] = row.get("fvg_position_of_sweep")
        row["fq_bull_fvg_age"] = row.get("bull_fvg_age")
        row["fq_bull_fvg_fill"] = row.get("bull_fvg_fill")
        row.update(fvg_reaction_features(row, {}))
        row["v2_payload_fvg_fallback_applied"] = 1
        row["v2_payload_fvg_fallback_source"] = source_prefix
    else:
        row["v2_payload_fvg_fallback_applied"] = 0


def apply_short_bear_fvg_features(row: Dict[str, Any]) -> None:
    if str(row.get("side") or row.get("direction") or "").strip().lower() != "short":
        return
    lower = as_float(row.get("bear_fvg_lower")) or as_float(row.get("bull_fvg_lower"))
    upper = as_float(row.get("bear_fvg_upper")) or as_float(row.get("bull_fvg_upper"))
    if lower is None:
        lower = as_float(row.get("tech_nearest_bear_fvg_lower"))
    if upper is None:
        upper = as_float(row.get("tech_nearest_bear_fvg_upper"))
    if lower is not None and upper is not None and upper < lower:
        lower, upper = upper, lower
    fill = as_float(row.get("bear_fvg_fill")) or as_float(row.get("bull_fvg_fill"))
    age = as_float(row.get("bear_fvg_age")) or as_float(row.get("bull_fvg_age"))
    if fill is None:
        fill = as_float(row.get("tech_nearest_bear_fvg_fill_pct_at_decision"))
    if age is None:
        age = as_float(row.get("tech_nearest_bear_fvg_age_bars_at_decision"))

    row["bear_fvg_lower"] = lower
    row["bear_fvg_upper"] = upper
    row["bear_fvg_age"] = age
    row["bear_fvg_fill"] = fill
    row["fq_bear_fvg_age"] = age
    row["fq_bear_fvg_fill"] = fill
    row["fq_nearest_bear_fvg_distance_atr"] = (
        row.get("dt_liq_nearest_bear_fvg_distance_atr") or row.get("tech_nearest_bear_fvg_distance_atr20")
    )
    nearest_fill = as_float(row.get("dt_liq_nearest_bear_fvg_fill_pct")) or as_float(row.get("tech_nearest_bear_fvg_fill_pct_at_decision"))
    nearest_age = as_float(row.get("dt_liq_nearest_bear_fvg_age_bars")) or as_float(row.get("tech_nearest_bear_fvg_age_bars_at_decision"))
    nearest_size = as_float(row.get("dt_liq_nearest_bear_fvg_remaining_size_atr")) or as_float(row.get("tech_nearest_bear_fvg_remaining_size_atr20"))
    nearest_distance = as_float(row.get("fq_nearest_bear_fvg_distance_atr"))
    row["fq_nearest_bear_fvg_fill_x_age"] = nearest_fill * nearest_age if nearest_fill is not None and nearest_age is not None else None
    row["fq_nearest_bear_fvg_size_per_distance"] = safe_div(nearest_size, nearest_distance)

    atr = as_float(row.get("tech_atr20"))
    sweep_high = as_float(row.get("t1_sweep_high_price")) or as_float(row.get("t1_sweep_low_price"))
    t2_low = as_float(row.get("t2_low_price")) or as_float(row.get("t2_high_price"))
    signal_price = as_float(row.get("signal_price"))
    signal_low = as_float(row.get("signal_low_price")) or as_float(row.get("signal_high_price")) or signal_price
    active_count = as_float(row.get("active_bear_fvgs")) or as_float(row.get("tech_active_bear_fvg_count_at_decision"))
    has = lower is not None and upper is not None and upper > lower
    size = upper - lower if has else None
    mid = lower + size / 2.0 if size else None
    position = safe_div(upper - sweep_high, size) if has and sweep_high is not None else None
    inside = bool(has and position is not None and 0.0 <= position <= 1.0)
    upper_half = bool(inside and position is not None and position <= 0.5)
    pierce_pct = max(0.0, safe_div(sweep_high - upper, size) or 0.0) if has and sweep_high is not None else None
    pierce_atr = max(0.0, safe_div(sweep_high - upper, atr) or 0.0) if has and sweep_high is not None else None
    reclaim_lower = safe_div(lower - signal_price, size) if has and signal_price is not None else None
    signal_low_reclaim_lower = safe_div(lower - signal_low, size) if has and signal_low is not None else None

    row["fq_bear_fvg_zone_size_atr"] = safe_div(size, atr)
    row["fq_bear_fvg_sweep_position"] = position
    row["eq_bear_fvg_size_atr"] = row["fq_bear_fvg_zone_size_atr"]
    row["eq_bear_fvg_signal_position"] = safe_div(upper - signal_price, size) if has and signal_price is not None else None
    row["eq_bear_fvg_sweep_position"] = position
    row["eq_bear_fvg_signal_below_mid"] = int(has and signal_price is not None and mid is not None and signal_price <= mid)
    row["eq_bear_fvg_age_bars"] = age
    row["eq_signal_distance_from_bear_fvg_mid_atr"] = safe_div(mid - signal_price, atr) if mid is not None and signal_price is not None else None
    row["eq_signal_distance_from_bear_fvg_lower_atr"] = safe_div(lower - signal_price, atr) if lower is not None and signal_price is not None else None
    row["eq_sweep_inside_bear_fvg"] = int(inside)

    row["fvg_react_has_bear_fvg"] = int(has)
    row["fvg_react_active_bear_fvg_count"] = active_count
    row["fvg_react_dip_depth_from_lower_pct"] = safe_div(sweep_high - lower, size) if has and sweep_high is not None else None
    row["fvg_react_sweep_inside_zone"] = int(inside)
    row["fvg_react_sweep_respected_upper"] = int(has and sweep_high is not None and sweep_high <= upper)
    row["fvg_react_sweep_pierced_upper"] = int(has and sweep_high is not None and sweep_high > upper)
    row["fvg_react_pierce_upper_pct"] = pierce_pct
    row["fvg_react_pierce_upper_atr"] = pierce_atr
    row["fvg_react_slice_through_penalty"] = pierce_pct
    row["fvg_react_t2_started_below_zone"] = int(has and t2_low is not None and t2_low < lower)
    row["fvg_react_up_leg_entered_from_below"] = int(has and t2_low is not None and t2_low < lower and sweep_high is not None and sweep_high >= lower)
    row["fvg_react_signal_reclaimed_mid"] = int(has and signal_price is not None and mid is not None and signal_price <= mid)
    row["fvg_react_signal_reclaimed_lower"] = int(has and signal_price is not None and signal_price <= lower)
    row["fvg_react_signal_reclaim_lower_pct"] = reclaim_lower
    row["fvg_react_signal_low_reclaim_lower_pct"] = signal_low_reclaim_lower
    row["fvg_react_upper_half_rejection_score"] = float(upper_half) * (clip01(reclaim_lower) or 0.0)
    row["fvg_react_slice_failure_risk"] = (clip01(pierce_pct) or 0.0) - (clip01(reclaim_lower) or 0.0)


def percentile_to_decile(value: Any) -> int | None:
    pct = as_float(value)
    if pct is None:
        return None
    return int(min(10, max(1, math.ceil(pct * 10.0))))


def first_numeric(row: Dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = as_float(row.get(key))
        if value is not None:
            return value
    return None


def score_density_proxy(score: Any, distance_atr: Any) -> float | None:
    score_value = as_float(score)
    distance_value = as_float(distance_atr)
    if score_value is None:
        return None
    return safe_div(score_value, abs(distance_value or 0.0) + 0.25)


def apply_dt_liq_group_aliases(row: Dict[str, Any], group: str) -> None:
    row[f"dt_liq_{group}_count"] = row.get(f"topo_{group}_count")
    row[f"dt_liq_{group}_proxy_score_sum"] = row.get(f"topo_{group}_score_sum")
    row[f"dt_liq_{group}_proxy_score_mean"] = row.get(f"topo_{group}_score_mean")
    row[f"dt_liq_{group}_proxy_score_max"] = row.get(f"topo_{group}_score_max")
    row[f"dt_liq_{group}_pressure"] = row.get(f"topo_{group}_pressure")
    row[f"dt_liq_{group}_level_count_sum"] = row.get(f"topo_{group}_count")
    row[f"dt_liq_{group}_density_mean"] = safe_div(row.get(f"topo_{group}_pressure"), row.get(f"topo_{group}_count"))
    row[f"dt_liq_{group}_fvg_backed_count_sum"] = 0.0 if as_float(row.get(f"topo_{group}_count")) is not None else None
    row[f"dt_liq_{group}_fvg_member_count_sum"] = 0.0 if as_float(row.get(f"topo_{group}_count")) is not None else None

    is_bsl = group.startswith("bsl")
    for idx in range(1, 6):
        score = as_float(row.get(f"topo_{group}_{idx}_score"))
        distance = as_float(row.get(f"topo_{group}_{idx}_distance_atr"))
        row[f"dt_liq_{group}_{idx}_distance_atr"] = distance
        row[f"dt_liq_{group}_{idx}_price_distance"] = row.get(f"topo_{group}_{idx}_price_distance")
        row[f"dt_liq_{group}_{idx}_proxy_score"] = score
        row[f"dt_liq_{group}_{idx}_age_bars"] = row.get(f"topo_{group}_{idx}_age_bars")
        if score is not None:
            row[f"dt_liq_{group}_{idx}_level_count"] = 1.0
            row[f"dt_liq_{group}_{idx}_density"] = score_density_proxy(score, distance)
            row[f"dt_liq_{group}_{idx}_fvg_backed_count"] = 0.0
            row[f"dt_liq_{group}_{idx}_fvg_member_count"] = 0.0
            if is_bsl:
                row[f"dt_liq_{group}_{idx}_ish_count"] = 0.0
                row[f"dt_liq_{group}_{idx}_sh_count"] = 1.0
            else:
                row[f"dt_liq_{group}_{idx}_isl_count"] = 0.0
                row[f"dt_liq_{group}_{idx}_sl_count"] = 1.0

    pair_metrics = [
        "distance_gap_atr",
        "price_gap",
        "score_delta",
        "score_gap_abs",
        "score_ratio",
        "score_delta_per_atr_gap",
        "score_sum_per_atr_gap",
        "age_delta_bars",
        "nearer_score_dominance",
        "farther_stronger_flag",
    ]
    for right in range(2, 6):
        left = right - 1
        for metric in pair_metrics:
            row[f"dt_liq_{group}_{left}_{right}_{metric}"] = row.get(f"topo_{group}_{left}_{right}_{metric}")
            row[f"dt_liq_{group}_{right}_{metric}"] = row.get(f"topo_{group}_{left}_{right}_{metric}")

    ranked_metrics = [
        "ranked_distance_gap_min_atr",
        "ranked_distance_gap_mean_atr",
        "ranked_distance_gap_max_atr",
        "ranked_distance_gap_std_atr",
        "ranked_distance_compression_ratio",
        "ranked_score_delta_mean",
        "ranked_score_gap_abs_mean",
        "ranked_score_gap_abs_max",
        "ranked_pair_density_max",
        "ranked_pair_density_mean",
        "ranked_farther_stronger_share",
        "ranked_score_range",
        "ranked_distance_range_atr",
        "ranked_score_vs_distance_slope",
    ]
    for metric in ranked_metrics:
        row[f"dt_liq_{group}_{metric}"] = row.get(f"topo_{group}_{metric}")


def apply_short_schema_aliases_after_context(row: Dict[str, Any]) -> None:
    if str(row.get("side") or row.get("direction") or "").strip().lower() != "short":
        return

    row["eq_cohort_signals_same_bar"] = row.get("macro_signal_count_same_bar")
    row["eq_cohort_signals_3h"] = row.get("macro_signal_count_3h")
    row["eq_cohort_signals_6h"] = row.get("macro_signal_count_6h")
    row["eq_cohort_signals_24h"] = row.get("macro_signal_count_24h")
    row["eq_cohort_unique_tickers_3h"] = row.get("macro_signal_unique_tickers_3h")
    row["eq_cohort_unique_tickers_6h"] = row.get("macro_signal_unique_tickers_6h")
    row["eq_cohort_unique_tickers_24h"] = row.get("macro_signal_unique_tickers_24h")
    row["eq_cohort_legacy_score_mean_6h"] = row.get("legacy_signal_score")
    row["eq_cohort_legacy_score_p75_6h"] = row.get("legacy_signal_score")
    row["eq_cohort_legacy_score_percentile_24h"] = row.get("macro_signal_crowding_percentile_24h")
    row["eq_cohort_strong_signal_fraction_24h"] = 1.0 if (as_float(row.get("legacy_signal_score")) or 0.0) >= 60.0 else 0.0
    row["eq_cohort_same_ticker_signals_24h"] = row.get("macro_signal_same_ticker_count_24h")

    target_count = as_float(row.get("xg_liq_target_side_count")) or as_float(row.get("dt_target_liquidity_count")) or 0.0
    adverse_count = as_float(row.get("xg_liq_stop_or_swept_side_count")) or as_float(row.get("dt_adverse_liquidity_count")) or 0.0
    target_sum = first_numeric(row, "xg_liq_target_side_score_sum", "dt_target_liquidity_score_sum", "topo_ssl_below_score_sum")
    target_mean = first_numeric(row, "xg_liq_target_side_score_mean", "dt_target_liquidity_score_mean", "topo_ssl_below_score_mean")
    target_max = first_numeric(row, "xg_liq_target_side_score_max", "dt_target_liquidity_score_max", "topo_ssl_below_score_max")
    target_pressure = first_numeric(row, "xg_liq_target_side_pressure", "dt_target_liquidity_pressure", "topo_ssl_below_pressure")
    adverse_sum = first_numeric(row, "xg_liq_stop_or_swept_side_score_sum", "dt_adverse_liquidity_score_sum", "topo_bsl_above_score_sum")
    adverse_mean = first_numeric(row, "xg_liq_stop_or_swept_side_score_mean", "dt_adverse_liquidity_score_mean", "topo_bsl_above_score_mean")
    adverse_max = first_numeric(row, "xg_liq_stop_or_swept_side_score_max", "dt_adverse_liquidity_score_max", "topo_bsl_above_score_max")
    adverse_pressure = first_numeric(row, "xg_liq_stop_or_swept_side_pressure", "dt_adverse_liquidity_pressure", "topo_bsl_above_pressure")
    row["liquidity_ticker_available"] = int((target_count + adverse_count) > 0)

    target_score = row.get("dt_target_liquidity_nearest_score") or row.get("xg_liq_target_side_nearest_score") or row.get("topo_ssl_below_1_score")
    target_pct = row.get("topo_ssl_below_1_percentile")
    target_dist = row.get("dt_target_liquidity_nearest_distance_atr") or row.get("xg_liq_target_side_nearest_distance_atr") or row.get("topo_ssl_below_1_distance_atr")
    row["target_ssl_nearest_score"] = target_score
    row["target_ssl_nearest_percentile"] = target_pct
    row["target_ssl_nearest_decile"] = percentile_to_decile(target_pct)
    row["target_ssl_nearest_distance_atr"] = target_dist
    row["target_ssl_nearest_price_distance"] = row.get("topo_ssl_below_1_price_distance")

    strongest_score = row.get("dt_target_liquidity_strongest_score") or row.get("xg_liq_target_side_score_max") or row.get("topo_ssl_below_score_max")
    row["target_ssl_strongest_score"] = strongest_score
    row["target_ssl_strongest_percentile"] = target_pct
    row["target_ssl_strongest_decile"] = percentile_to_decile(target_pct)
    row["target_ssl_strongest_distance_atr"] = row.get("dt_target_liquidity_strongest_distance_atr") or target_dist
    row["target_ssl_strongest_price_distance"] = row.get("target_ssl_nearest_price_distance")
    row["target_ssl_count_below"] = target_count
    row["target_ssl_score_sum_below"] = target_sum
    row["target_ssl_score_mean_below"] = target_mean
    row["target_ssl_score_max_below"] = target_max

    swept_score = row.get("dt_adverse_liquidity_nearest_score") or row.get("xg_liq_stop_or_swept_side_nearest_score") or row.get("topo_bsl_above_1_score")
    swept_pct = row.get("topo_bsl_above_1_percentile")
    row["swept_bsl_score"] = swept_score
    row["swept_bsl_percentile"] = swept_pct
    row["swept_bsl_decile"] = percentile_to_decile(swept_pct)
    row["swept_bsl_distance_atr"] = row.get("dt_adverse_liquidity_nearest_distance_atr") or row.get("xg_liq_stop_or_swept_side_nearest_distance_atr") or row.get("topo_bsl_above_1_distance_atr")
    row["swept_bsl_price_distance"] = row.get("topo_bsl_above_1_price_distance")
    row["swept_bsl_age_to_signal_bars"] = row.get("topo_bsl_above_1_age_bars")

    row["stop_side_bsl_count_above"] = adverse_count
    row["stop_side_bsl_score_sum_above"] = adverse_sum
    row["topology_target_minus_swept_score"] = (
        (as_float(target_score) or 0.0) - (as_float(swept_score) or 0.0)
        if as_float(target_score) is not None or as_float(swept_score) is not None
        else None
    )
    row["topology_ssl_bsl_pressure_diff"] = (
        (target_pressure or 0.0) - (adverse_pressure or 0.0)
        if target_pressure is not None or adverse_pressure is not None
        else None
    )
    row["topo_downside_minus_upside_pressure"] = row.get("topology_ssl_bsl_pressure_diff")
    row["topo_downside_to_upside_pressure_ratio"] = safe_div(target_pressure, adverse_pressure)
    row["topo_nearest_ssl_below_minus_bsl_above_score"] = row.get("topology_target_minus_swept_score")
    row["topo_nearest_ssl_below_to_bsl_above_distance_ratio"] = safe_div(target_dist, row.get("swept_bsl_distance_atr"))
    row["topo_target_path_pool_count"] = target_count
    row["topo_target_path_ssl_count"] = target_count
    row["topo_target_path_score_sum"] = target_sum
    row["topo_target_path_score_max"] = target_max
    row["topo_target_path_pressure"] = target_pressure
    row["topo_stop_path_pool_count"] = adverse_count
    row["topo_stop_path_ssl_count"] = 0.0 if adverse_count is not None else None
    row["topo_stop_path_bsl_count"] = adverse_count
    row["topo_stop_path_score_sum"] = adverse_sum
    row["topo_stop_path_score_max"] = adverse_max
    row["topo_stop_path_pressure"] = adverse_pressure

    row["dt_liq_ssl_below_pressure"] = target_pressure
    row["dt_liq_bsl_above_pressure"] = adverse_pressure
    row["dt_liq_ssl_below_level_count_sum"] = target_count
    row["dt_liq_bsl_above_level_count_sum"] = adverse_count
    row["dt_liq_target_path_pressure"] = target_pressure
    row["dt_liq_stop_path_pressure"] = adverse_pressure
    row["dt_liq_target_minus_stop_pressure"] = row.get("topology_ssl_bsl_pressure_diff")
    row["dt_liq_target_path_pool_count"] = target_count
    row["dt_liq_stop_path_pool_count"] = adverse_count
    row["dt_liq_target_path_proxy_score_sum"] = target_sum
    row["dt_liq_target_path_proxy_score_max"] = target_max
    row["dt_liq_stop_path_proxy_score_sum"] = adverse_sum
    row["dt_liq_stop_path_proxy_score_max"] = adverse_max
    row["dt_liq_nearest_target_distance_atr"] = target_dist
    row["dt_liq_nearest_target_proxy_score"] = target_score
    row["dt_liq_nearest_stop_distance_atr"] = row.get("swept_bsl_distance_atr")
    row["dt_liq_nearest_stop_proxy_score"] = swept_score
    row["dt_liq_swept_clusters_taken_during_signal_count"] = adverse_count
    row["dt_liq_swept_clusters_taken_during_signal_proxy_sum"] = adverse_sum
    row["dt_liq_swept_reference_price"] = row.get("t1_sweep_high_price")
    row["dt_liq_swept_reference_time"] = row.get("t1_sweep_high_time")
    row["dt_liq_swept_cluster_distance_atr"] = row.get("swept_bsl_distance_atr")
    row["dt_liq_swept_cluster_price_distance"] = row.get("swept_bsl_price_distance")
    row["dt_liq_swept_cluster_proxy_score"] = swept_score
    row["dt_liq_swept_cluster_age_bars"] = row.get("swept_bsl_age_to_signal_bars")
    if as_float(swept_score) is not None:
        row["dt_liq_swept_cluster_level_count"] = 1.0
        row["dt_liq_swept_cluster_density"] = score_density_proxy(swept_score, row.get("swept_bsl_distance_atr"))
        row["dt_liq_swept_cluster_fvg_backed_count"] = 0.0
        row["dt_liq_swept_cluster_fvg_member_count"] = 0.0

    row["dt_liq_active_bsl_count"] = first_numeric(row, "topo_active_bsl_count", "topo_bsl_above_count")
    row["dt_liq_active_ssl_count"] = first_numeric(row, "topo_active_ssl_count", "topo_ssl_below_count")
    row["dt_liq_all_bsl_cluster_count"] = first_numeric(row, "tech_long_landscape_cluster_count", "topo_active_bsl_count")
    row["dt_liq_all_ssl_cluster_count"] = first_numeric(row, "tech_short_landscape_cluster_count", "topo_active_ssl_count")
    row["dt_liq_active_bull_fvg_count"] = first_numeric(row, "tech_structure_active_bullish_fvgs", "active_bull_fvgs")
    row["dt_liq_active_bear_fvg_count"] = first_numeric(row, "tech_structure_active_bearish_fvgs", "active_bear_fvgs")
    row["dt_liq_market_state_uptrend"] = row.get("tech_market_state_uptrend")
    row["dt_liq_long_landscape_candidate_count"] = row.get("tech_long_landscape_candidate_count")
    row["dt_liq_long_landscape_cluster_count"] = row.get("tech_long_landscape_cluster_count")
    row["dt_liq_long_nearest_level_age_bars"] = row.get("tech_long_nearest_level_age_bars")
    row["dt_liq_long_nearest_level_fvg_significant"] = row.get("tech_long_nearest_level_fvg_significant")
    row["dt_liq_short_landscape_candidate_count"] = row.get("tech_short_landscape_candidate_count")
    row["dt_liq_short_landscape_cluster_count"] = row.get("tech_short_landscape_cluster_count")
    row["dt_liq_short_nearest_level_age_bars"] = row.get("tech_short_nearest_level_age_bars")
    row["dt_liq_short_nearest_level_fvg_significant"] = row.get("tech_short_nearest_level_fvg_significant")
    row["dt_liq_nearest_bull_fvg_distance_atr"] = row.get("tech_nearest_bull_fvg_distance_atr20")
    row["dt_liq_nearest_bull_fvg_remaining_size_atr"] = row.get("tech_nearest_bull_fvg_remaining_size_atr20")
    row["dt_liq_nearest_bull_fvg_age_bars"] = row.get("tech_nearest_bull_fvg_age_bars_at_decision")
    row["dt_liq_nearest_bull_fvg_fill_pct"] = row.get("tech_nearest_bull_fvg_fill_pct_at_decision")
    row["dt_liq_nearest_bear_fvg_distance_atr"] = row.get("tech_nearest_bear_fvg_distance_atr20")
    row["dt_liq_nearest_bear_fvg_remaining_size_atr"] = row.get("tech_nearest_bear_fvg_remaining_size_atr20")
    row["dt_liq_nearest_bear_fvg_age_bars"] = row.get("tech_nearest_bear_fvg_age_bars_at_decision")
    row["dt_liq_nearest_bear_fvg_fill_pct"] = row.get("tech_nearest_bear_fvg_fill_pct_at_decision")
    row["tech_event_active_bull_fvgs"] = row.get("tech_structure_active_bullish_fvgs") or row.get("active_bull_fvgs")
    row["tech_engine_bear_fvg_age"] = row.get("tech_engine_bear_fvg_age") or row.get("bear_fvg_age")
    row["eqp_impulse_t3_to_t2_favorable_fvg_gap_atr_max"] = (
        row.get("eqp_impulse_t3_to_t2_favorable_fvg_gap_atr_max")
        or row.get("eqp_impulse_t3_to_t2_favorable_fvg_gap_atr_sum")
        or 0.0
    )
    row["fq_sweep_lower_wick_pressure"] = row.get("fq_sweep_lower_wick_pressure") or row.get("fq_sweep_upper_wick_pressure")
    row["fq_signal_reclaim_low_atr"] = row.get("fq_signal_reclaim_low_atr") or row.get("fq_signal_reclaim_high_atr")
    row["fq_sweep_break_above_t3_atr"] = row.get("fq_sweep_break_above_t3_atr") or row.get("fq_sweep_break_below_t3_atr")
    row["cq_up_clean_rally_score"] = row.get("cq_up_clean_rally_score") or row.get("cq_down_clean_drop_score")
    row["cq_down_clean_reversal_score"] = row.get("cq_down_clean_reversal_score") or row.get("cq_up_clean_reversal_score")
    row["cq_reversal_vs_rally_body_atr"] = (
        row.get("cq_reversal_vs_rally_body_atr") or row.get("cq_reversal_vs_drop_body_atr")
    )
    row["cq_reversal_vs_rally_impulse"] = (
        row.get("cq_reversal_vs_rally_impulse") or row.get("cq_reversal_vs_drop_impulse")
    )
    ssl2_score = as_float(row.get("dt_liq_ssl_below_2_proxy_score")) or 0.0
    ssl2_density = as_float(row.get("dt_liq_ssl_below_2_density")) or 0.0
    ssl2_distance = as_float(row.get("dt_liq_ssl_below_2_distance_atr")) or 0.0
    ssl_gap_12 = as_float(row.get("dt_liq_ssl_below_1_2_distance_gap_atr")) or 0.0
    row["goal_second_ssl_quality_per_gap"] = safe_div(ssl2_score * (1.0 + ssl2_density), ssl2_distance + ssl_gap_12 + 0.25) or 0.0
    row["goal_second_ssl_density_x_score"] = math.log1p(max(ssl2_density, 0.0)) * ssl2_score
    row["goal_topology_downside_per_upside_pressure"] = safe_div(
        (as_float(row.get("dt_liq_ssl_below_pressure")) or 0.0) + 1.0,
        (as_float(row.get("dt_liq_bsl_above_pressure")) or 0.0) + 1.0,
    )
    row["goal_ssl_stack_ladder_quality"] = safe_div(
        (target_sum or 0.0) * math.log1p(max(target_count, 0.0)),
        1.0 + ssl_gap_12 + ssl2_distance,
    )
    row["dt_liq_upside_minus_downside_ranked_score_slope"] = row.get("topo_upside_minus_downside_ranked_score_slope")
    row["dt_liq_upside_to_downside_pair_density_ratio"] = row.get("topo_upside_to_downside_pair_density_ratio")
    row["dt_liq_upside_minus_downside_pair_density"] = row.get("topo_upside_minus_downside_pair_density")
    row["dt_liq_upside_to_downside_gap_ratio"] = row.get("topo_upside_to_downside_gap_ratio")
    apply_dt_liq_group_aliases(row, "bsl_above")
    apply_dt_liq_group_aliases(row, "ssl_below")

    row["v20_two_ssl_score_per_second_distance"] = safe_div(
        (as_float(row.get("topo_ssl_below_1_score")) or 0.0) + (as_float(row.get("topo_ssl_below_2_score")) or 0.0),
        abs(as_float(row.get("topo_ssl_below_2_distance_atr")) or as_float(row.get("topo_ssl_below_1_distance_atr")) or 0.0) + 0.50,
    )
    row["v20_adverse_first_bsl_score_per_distance"] = safe_div(
        row.get("topo_bsl_above_1_score"),
        abs(as_float(row.get("topo_bsl_above_1_distance_atr")) or 0.0) + 0.25,
    )
    row["v20_target_access_vs_adverse_density"] = safe_div(
        row.get("v20_two_ssl_score_per_second_distance"),
        abs(as_float(row.get("v20_adverse_first_bsl_score_per_distance")) or 0.0) + 0.50,
    )
    row["v20_target_pressure_per_stop_pressure"] = safe_div(row.get("dt_liq_ssl_below_pressure"), abs(as_float(row.get("dt_liq_bsl_above_pressure")) or 0.0) + 1e-6)
    row["v20_stop_pressure_per_target_pressure"] = safe_div(row.get("dt_liq_bsl_above_pressure"), abs(as_float(row.get("dt_liq_ssl_below_pressure")) or 0.0) + 1e-6)


def engine_context_features(row: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
    atr = as_float(row.get("tech_atr20"))
    signal_price = as_float(row.get("signal_price"))
    out: Dict[str, Any] = {}
    bool_metrics = {
        "base_ish_swept": "tech_engine_base_ish_swept",
        "base_isl_swept": "tech_engine_base_isl_swept",
        "current_isl_swept": "tech_engine_current_isl_swept",
        "range_active": "tech_engine_range_active",
        "bull_fvg_retest": "tech_engine_bull_fvg_retest",
        "deeper_isl_pending": "tech_engine_deeper_isl_pending",
    }
    for metric_key, feature_name in bool_metrics.items():
        value = metrics.get(metric_key)
        out[feature_name] = int(bool(value)) if value is not None else None

    distance_metrics = {
        "base_ish": "tech_engine_base_ish_distance_atr",
        "base_isl": "tech_engine_base_isl_distance_atr",
        "current_isl": "tech_engine_current_isl_distance_atr",
        "nearest_deeper_isl": "tech_engine_nearest_deeper_isl_distance_atr",
    }
    for metric_key, feature_name in distance_metrics.items():
        value = as_float(metrics.get(metric_key))
        if value is None and metric_key == "base_isl":
            value = as_float(row.get("base_isl_price"))
        if value is None and metric_key == "current_isl":
            value = as_float(row.get("current_isl_price"))
        out[feature_name] = safe_div(abs(value - signal_price), atr) if value is not None and signal_price is not None else None

    direct_metrics = {
        "target_distance_atr": "tech_engine_target_distance_atr",
        "target_highs": "tech_engine_target_highs",
        "target_clusters": "tech_engine_target_clusters",
        "target_hold": "tech_engine_target_hold",
        "target_touches": "tech_engine_target_touches",
        "target_vol": "tech_engine_target_vol",
        "low_clusters": "tech_engine_low_clusters",
        "low_hold": "tech_engine_low_hold",
        "low_touches": "tech_engine_low_touches",
        "low_vol": "tech_engine_low_vol",
        "low_spacing_atr": "tech_engine_low_spacing_atr",
        "range_quality": "tech_engine_range_quality",
        "range_age": "tech_engine_range_age",
        "range_width_atr": "tech_engine_range_width_atr",
        "range_internal_ish": "tech_engine_range_internal_ish",
        "range_internal_isl": "tech_engine_range_internal_isl",
        "active_bull_fvgs": "tech_engine_active_bull_fvgs",
        "bull_fvg_fill": "tech_engine_bull_fvg_fill",
        "bull_fvg_age": "tech_engine_bull_fvg_age",
        "deeper_isl_count": "tech_engine_deeper_isl_count",
        "deeper_isl_atr": "tech_engine_deeper_isl_atr",
    }
    for metric_key, feature_name in direct_metrics.items():
        out[feature_name] = as_float(metrics.get(metric_key))
    return out


def build_row(event: Dict[str, Any], frame: pd.DataFrame, required_features: List[str]) -> Dict[str, Any]:
    metrics = {}
    try:
        metrics = json.loads(event.get("metrics_json") or "{}")
    except json.JSONDecodeError:
        metrics = {}

    side = str(event.get("side") or event.get("direction") or "long").lower()
    is_short = side == "short"
    t3_time = event.get("t3_high_time") if is_short else event.get("t3_low_time")
    t2_time = event.get("t2_low_time") if is_short else event.get("t2_high_time")
    t1_time = event.get("t1_sweep_high_time") if is_short else event.get("t1_sweep_low_time")
    signal_extreme_time = event.get("signal_low_time") if is_short else event.get("signal_high_time")
    decision_index = row_index_at_or_before(frame, event.get("decision_time"))
    signal_index = row_index_at_or_before(frame, event.get("signal_time"))
    t3_index = row_index_at_or_after(frame, t3_time)
    t2_index = row_index_at_or_after(frame, t2_time)
    t1_index = row_index_at_or_after(frame, t1_time)
    signal_high_index = row_index_at_or_after(frame, signal_extreme_time) or signal_index

    row: Dict[str, Any] = {
        "signal_id": event.get("signal_id"),
        "candidate_row_id": event.get("candidate_row_id"),
        "ticker": event.get("ticker"),
        "side": side,
        "direction": str(event.get("direction") or event.get("side") or "long").lower(),
        "decision_time": event.get("decision_time"),
        "signal_time": event.get("signal_time"),
        "feature_cutoff_time": event.get("decision_time"),
        "entry_model_variant": event.get("entry_model_variant"),
        "entry_price": event.get("entry_price"),
        "stop_price": event.get("stop_price"),
        "risk": event.get("risk"),
        "v2_feature_builder": "v2_build_live_features_from_events",
    }
    row.update({feature: "" for feature in required_features})
    row.update(base_event_features(event, metrics))
    row.update(technical_features(frame, decision_index))
    if is_short:
        atr20 = as_float(row.get("tech_atr20"))
        signal_price = as_float(row.get("signal_price"))
        current_ish = as_float(row.get("current_ish_price"))
        deeper_ish = as_float(row.get("deeper_ish_price"))
        row["metric_atr_baseline"] = atr20
        row["tech_engine_current_ish_distance_atr"] = (
            safe_div(current_ish - signal_price, atr20) if current_ish is not None and signal_price is not None else None
        )
        row["tech_engine_nearest_deeper_ish_distance_atr"] = (
            safe_div(deeper_ish - signal_price, atr20) if deeper_ish is not None and signal_price is not None else None
        )
    row.update(macro_ticker_features(frame, decision_index))
    row.update(engine_context_features(row, metrics))
    row.update(phase_quality_features(frame, decision_index, t3_index, t2_index, t1_index, signal_high_index, row))

    atr20 = row.get("tech_atr20")
    if is_short:
        down = remap_short_candle_quality(candle_quality_leg(frame, t2_index, t1_index, atr20, "up"), "up", "down")
        up = remap_short_candle_quality(candle_quality_leg(frame, t1_index, signal_high_index, atr20, "down"), "down", "up")
    else:
        down = candle_quality_leg(frame, t2_index, t1_index, atr20, "down")
        up = candle_quality_leg(frame, t1_index, signal_high_index, atr20, "up")
    row.update(down)
    row.update(up)
    row["cq_reversal_vs_drop_body_atr"] = safe_div(row.get("cq_up_green_body_atr_sum"), row.get("cq_down_red_body_atr_sum"))
    row["cq_reversal_vs_drop_impulse"] = safe_div(row.get("cq_up_impulse_score"), row.get("cq_down_impulse_score"))
    row["cq_clean_two_leg_composite"] = (row.get("cq_down_clean_drop_score") or 0.0) + (row.get("cq_up_clean_reversal_score") or 0.0)

    # Minimal focused-quality fields that can be computed without EQP/liquidity blocks.
    favorable_direction = -1 if is_short else 1
    signal_price = row.get("signal_price")
    sweep_low = row.get("t1_sweep_low_price")
    t3_low = row.get("t3_low_price")
    t2_high = row.get("t2_high_price")
    row["fq_stop_buffer_signal_to_sweep_atr"] = (
        safe_div(favorable_direction * (signal_price - sweep_low), atr20) if signal_price is not None and sweep_low is not None else None
    )
    row["fq_stop_buffer_signal_to_t3_atr"] = (
        safe_div(favorable_direction * (signal_price - t3_low), atr20) if signal_price is not None and t3_low is not None else None
    )
    row["fq_sweep_break_below_t3_atr"] = (
        safe_div(favorable_direction * (t3_low - sweep_low), atr20) if t3_low is not None and sweep_low is not None else None
    )
    row["fq_stop_buffer_vs_sweep_depth"] = (
        safe_div(favorable_direction * (signal_price - sweep_low), favorable_direction * (t2_high - sweep_low))
        if signal_price is not None and sweep_low is not None and t2_high is not None
        else None
    )
    row["fq_bull_fvg_zone_size_atr"] = safe_div(row.get("fvg_zone_size"), atr20)
    row["fq_bull_fvg_sweep_position"] = row.get("fvg_position_of_sweep")
    row["fq_bull_fvg_age"] = row.get("bull_fvg_age")
    row["fq_bull_fvg_fill"] = row.get("bull_fvg_fill")

    row.update(fvg_reaction_features(row, metrics))
    row.update(entry_features(event, row))
    add_short_entry_quality_fields(row, frame, t3_index, t2_index, t1_index, signal_high_index)

    return {key: encode(value) for key, value in row.items()}


def add_long_topology_aliases_from_liquidity(row: Dict[str, Any]) -> None:
    target_count = as_float(row.get("dt_target_liquidity_count"))
    adverse_count = as_float(row.get("dt_adverse_liquidity_count"))
    target_sum = as_float(row.get("dt_target_liquidity_score_sum"))
    adverse_sum = as_float(row.get("dt_adverse_liquidity_score_sum"))
    target_mean = as_float(row.get("dt_target_liquidity_score_mean"))
    adverse_mean = as_float(row.get("dt_adverse_liquidity_score_mean"))
    target_max = as_float(row.get("dt_target_liquidity_score_max"))
    adverse_max = as_float(row.get("dt_adverse_liquidity_score_max"))
    target_pressure = as_float(row.get("dt_target_liquidity_pressure"))
    adverse_pressure = as_float(row.get("dt_adverse_liquidity_pressure"))
    nearest_target_dist = as_float(row.get("dt_target_liquidity_nearest_distance_atr"))
    nearest_adverse_dist = as_float(row.get("dt_adverse_liquidity_nearest_distance_atr"))
    nearest_target_score = as_float(row.get("dt_target_liquidity_nearest_score"))
    nearest_adverse_score = as_float(row.get("dt_adverse_liquidity_nearest_score"))
    entry_price = as_float(row.get("entry_price"))

    if target_count == 0:
        target_sum = target_mean = target_max = target_pressure = 0.0
        for key in [
            "dt_target_liquidity_score_sum",
            "dt_target_liquidity_score_mean",
            "dt_target_liquidity_score_max",
            "dt_target_liquidity_pressure",
            "xg_liq_target_side_score_sum",
            "xg_liq_target_side_score_mean",
            "xg_liq_target_side_score_max",
            "xg_liq_target_side_pressure",
        ]:
            row[key] = 0.0
    if adverse_count == 0:
        adverse_sum = adverse_mean = adverse_max = adverse_pressure = 0.0
        for key in [
            "dt_adverse_liquidity_score_sum",
            "dt_adverse_liquidity_score_mean",
            "dt_adverse_liquidity_score_max",
            "dt_adverse_liquidity_pressure",
            "xg_liq_stop_or_swept_side_score_sum",
            "xg_liq_stop_or_swept_side_score_mean",
            "xg_liq_stop_or_swept_side_score_max",
            "xg_liq_stop_or_swept_side_pressure",
        ]:
            row[key] = 0.0

    row["dt_liq_target_path_pressure"] = target_pressure
    row["target_bsl_count_above"] = target_count
    row["dt_liq_target_minus_stop_pressure"] = (
        (target_pressure or 0.0) - (adverse_pressure or 0.0)
        if target_pressure is not None or adverse_pressure is not None
        else None
    )
    row["dt_liq_target_path_pool_count"] = target_count
    row["dt_liq_target_path_proxy_score_sum"] = target_sum
    row["dt_liq_nearest_target_distance_atr"] = nearest_target_dist
    row["dt_liq_ssl_below_pressure"] = adverse_pressure
    row["dt_liq_stop_path_pool_count"] = adverse_count
    row["dt_liq_swept_clusters_taken_during_signal_count"] = adverse_count
    row["dt_liq_swept_clusters_taken_during_signal_proxy_sum"] = adverse_sum
    row["dt_liq_bsl_above_2_proxy_score"] = as_float(row.get("dt_target_liquidity_2_score"))
    row["dt_liq_bsl_above_2_distance_atr"] = as_float(row.get("dt_target_liquidity_2_distance_atr"))
    row["topology_target_score_per_atr"] = safe_div(target_sum, nearest_target_dist)
    row["topology_bsl_ssl_pressure_diff"] = (
        (target_pressure or 0.0) - (adverse_pressure or 0.0)
        if target_pressure is not None or adverse_pressure is not None
        else None
    )

    row["topo_active_pool_count"] = (target_count or 0.0) + (adverse_count or 0.0)
    row["topo_active_bsl_count"] = target_count
    row["topo_active_ssl_count"] = adverse_count
    row["topo_bsl_above_count"] = target_count
    row["topo_ssl_below_count"] = adverse_count
    row["topo_bsl_above_score_sum"] = target_sum
    row["topo_bsl_above_score_mean"] = target_mean
    row["topo_bsl_above_score_max"] = target_max
    row["topo_ssl_below_score_sum"] = adverse_sum
    row["topo_ssl_below_score_mean"] = adverse_mean
    row["topo_ssl_below_score_max"] = adverse_max
    row["topo_bsl_above_pressure"] = target_pressure
    row["topo_ssl_below_pressure"] = adverse_pressure
    row["topo_upside_minus_downside_pressure"] = (
        (target_pressure or 0.0) - (adverse_pressure or 0.0)
        if target_pressure is not None or adverse_pressure is not None
        else None
    )
    row["topo_upside_to_downside_pressure_ratio"] = safe_div(target_pressure, adverse_pressure)
    row["topo_upside_score_per_pool"] = 0.0 if target_count == 0 else safe_div(target_sum, target_count)
    row["topo_downside_score_per_pool"] = 0.0 if adverse_count == 0 else safe_div(adverse_sum, adverse_count)
    row["topo_nearest_bsl_above_distance_atr"] = nearest_target_dist
    row["topo_nearest_bsl_above_score"] = nearest_target_score
    row["topo_nearest_ssl_below_distance_atr"] = nearest_adverse_dist
    row["topo_nearest_ssl_below_score"] = nearest_adverse_score
    row["topo_nearest_bsl_above_minus_ssl_below_score"] = (
        (nearest_target_score or 0.0) - (nearest_adverse_score or 0.0)
        if nearest_target_score is not None or nearest_adverse_score is not None
        else None
    )
    row["topo_nearest_bsl_above_to_ssl_below_distance_ratio"] = safe_div(nearest_target_dist, nearest_adverse_dist)

    row["topo_target_path_pool_count"] = target_count
    row["topo_target_path_bsl_count"] = target_count
    row["topo_target_path_score_sum"] = target_sum
    row["topo_target_path_score_max"] = target_max
    row["topo_target_path_pressure"] = target_pressure
    row["topo_stop_path_pool_count"] = adverse_count
    row["topo_stop_path_ssl_count"] = adverse_count
    row["topo_stop_path_bsl_count"] = 0 if adverse_count is not None else None
    row["topo_stop_path_score_sum"] = adverse_sum
    row["topo_stop_path_score_max"] = adverse_max
    row["topo_stop_path_pressure"] = adverse_pressure

    if nearest_target_dist is not None:
        for key in ["target_distance_atr", "metric_target_distance_atr", "tech_engine_target_distance_atr"]:
            if row.get(key) in ("", None):
                row[key] = nearest_target_dist

    for idx in range(1, 6):
        target_dist = as_float(row.get(f"dt_target_liquidity_{idx}_distance_atr"))
        target_score = as_float(row.get(f"dt_target_liquidity_{idx}_score"))
        target_midpoint = as_float(row.get(f"dt_target_liquidity_{idx}_midpoint"))
        adverse_dist = as_float(row.get(f"dt_adverse_liquidity_{idx}_distance_atr"))
        adverse_score = as_float(row.get(f"dt_adverse_liquidity_{idx}_score"))
        adverse_midpoint = as_float(row.get(f"dt_adverse_liquidity_{idx}_midpoint"))
        row[f"topo_bsl_above_{idx}_distance_atr"] = target_dist
        row[f"topo_bsl_above_{idx}_score"] = target_score
        row[f"topo_bsl_above_{idx}_price_distance"] = (
            abs(target_midpoint - entry_price) if target_midpoint is not None and entry_price is not None else None
        )
        row[f"topo_ssl_below_{idx}_distance_atr"] = adverse_dist
        row[f"topo_ssl_below_{idx}_score"] = adverse_score
        row[f"topo_ssl_below_{idx}_price_distance"] = (
            abs(entry_price - adverse_midpoint) if adverse_midpoint is not None and entry_price is not None else None
        )

    for left, right in [(1, 2), (2, 3), (3, 4), (4, 5)]:
        left_distance = as_float(row.get(f"topo_bsl_above_{left}_distance_atr"))
        right_distance = as_float(row.get(f"topo_bsl_above_{right}_distance_atr"))
        left_price_distance = as_float(row.get(f"topo_bsl_above_{left}_price_distance"))
        right_price_distance = as_float(row.get(f"topo_bsl_above_{right}_price_distance"))
        left_score = as_float(row.get(f"topo_bsl_above_{left}_score"))
        right_score = as_float(row.get(f"topo_bsl_above_{right}_score"))
        prefix = f"topo_bsl_above_{left}_{right}"
        distance_gap = right_distance - left_distance if right_distance is not None and left_distance is not None else None
        score_delta = right_score - left_score if right_score is not None and left_score is not None else None
        score_sum = (left_score or 0.0) + (right_score or 0.0) if left_score is not None or right_score is not None else None
        row[f"{prefix}_distance_gap_atr"] = distance_gap
        row[f"{prefix}_price_gap"] = (
            abs(right_price_distance - left_price_distance)
            if right_price_distance is not None and left_price_distance is not None
            else None
        )
        row[f"{prefix}_score_delta"] = score_delta
        row[f"{prefix}_score_gap_abs"] = abs(score_delta) if score_delta is not None else None
        row[f"{prefix}_score_ratio"] = safe_div(right_score, left_score)
        row[f"{prefix}_score_delta_per_atr_gap"] = safe_div(score_delta, abs(distance_gap)) if distance_gap is not None else None
        row[f"{prefix}_score_sum_per_atr_gap"] = safe_div(score_sum, abs(distance_gap)) if distance_gap is not None else None
        row[f"{prefix}_nearer_score_dominance"] = left_score - right_score if left_score is not None and right_score is not None else None
        row[f"{prefix}_farther_stronger_flag"] = (
            float(right_score > left_score) if right_score is not None and left_score is not None else None
        )

    row["xg_liq_target_max_minus_stop_max"] = (
        (target_max or 0.0) - (adverse_max or 0.0)
        if target_max is not None or adverse_max is not None
        else None
    )


def load_scored_liquidity_candidates(path: Path | None) -> Dict[str, List[Dict[str, str]]]:
    if not path or not path.exists():
        return {}
    by_signal: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for candidate in read_csv(path):
        signal_id = str(candidate.get("signal_id") or "")
        if signal_id:
            by_signal[signal_id].append(candidate)
    return by_signal


def candidate_score(candidate: Dict[str, Any]) -> float | None:
    return as_float(candidate.get("approved_model_score"))


def candidate_percentile(candidate: Dict[str, Any]) -> float | None:
    return as_float(candidate.get("approved_model_percentile"))


def candidate_age_bars(candidate: Dict[str, Any]) -> float | None:
    for key in [
        "current_member_age_bars_mean",
        "original_member_age_bars_mean",
        "current_member_age_bars_max",
        "original_member_age_bars_max",
    ]:
        value = as_float(candidate.get(key))
        if value is not None:
            return value
    return None


def candidate_midpoint(candidate: Dict[str, Any]) -> float | None:
    midpoint = as_float(candidate.get("midpoint"))
    if midpoint is not None:
        return midpoint
    lower = as_float(candidate.get("lower"))
    upper = as_float(candidate.get("upper"))
    if lower is not None and upper is not None:
        return (lower + upper) / 2.0
    return lower if lower is not None else upper


def candidate_distance_atr(candidate: Dict[str, Any], ref_price: float | None, atr: float | None) -> float | None:
    distance = as_float(candidate.get("candidate_distance_to_signal_atr"))
    if distance is not None:
        return abs(distance)
    midpoint = candidate_midpoint(candidate)
    if midpoint is None or ref_price is None or atr is None or atr <= 0:
        return None
    return abs(midpoint - ref_price) / atr


def candidate_price_distance(candidate: Dict[str, Any], ref_price: float | None) -> float | None:
    midpoint = candidate_midpoint(candidate)
    if midpoint is None or ref_price is None:
        return None
    return abs(midpoint - ref_price)


def split_scored_candidates_by_topology(
    candidates: List[Dict[str, str]],
    ref_price: float | None,
    atr: float | None,
) -> Dict[str, List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {
        "bsl_above": [],
        "bsl_below": [],
        "ssl_above": [],
        "ssl_below": [],
    }
    if ref_price is None:
        return groups
    for candidate in candidates:
        side = str(candidate.get("candidate_side") or candidate.get("side") or "").upper()
        midpoint = candidate_midpoint(candidate)
        if side not in {"BSL", "SSL"} or midpoint is None:
            continue
        distance_atr = candidate_distance_atr(candidate, ref_price, atr)
        enriched = {
            "side": side,
            "midpoint": midpoint,
            "distance_atr": distance_atr,
            "price_distance": candidate_price_distance(candidate, ref_price),
            "score": candidate_score(candidate),
            "percentile": candidate_percentile(candidate),
            "decile": as_float(candidate.get("approved_model_decile")),
            "age_bars": candidate_age_bars(candidate),
        }
        if side == "BSL":
            groups["bsl_above" if midpoint >= ref_price else "bsl_below"].append(enriched)
        else:
            groups["ssl_below" if midpoint <= ref_price else "ssl_above"].append(enriched)
    for group_rows in groups.values():
        group_rows.sort(
            key=lambda item: (
                item["distance_atr"] if item["distance_atr"] is not None else float("inf"),
                -(item["score"] if item["score"] is not None else -float("inf")),
            )
        )
    return groups


def group_score_stats(group_rows: List[Dict[str, Any]]) -> Dict[str, float | None]:
    scores = [row["score"] for row in group_rows if row.get("score") is not None]
    distances = [row["distance_atr"] for row in group_rows if row.get("distance_atr") is not None]
    pressure = 0.0
    pressure_count = 0
    for item in group_rows:
        score = item.get("score")
        distance = item.get("distance_atr")
        if score is None or distance is None:
            continue
        pressure += score / (1.0 + max(distance, 0.0))
        pressure_count += 1
    return {
        "count": float(len(group_rows)),
        "score_sum": sum(scores) if scores else 0.0,
        "score_mean": (sum(scores) / len(scores)) if scores else 0.0,
        "score_max": max(scores) if scores else 0.0,
        "pressure": pressure if pressure_count else 0.0,
        "nearest_distance": distances[0] if distances else None,
        "distance_range": (max(distances) - min(distances)) if len(distances) >= 2 else None,
    }


def add_ranked_group_fields(row: Dict[str, Any], group_name: str, group_rows: List[Dict[str, Any]]) -> None:
    stats = group_score_stats(group_rows)
    row[f"topo_{group_name}_count"] = stats["count"]
    row[f"topo_{group_name}_score_sum"] = stats["score_sum"]
    row[f"topo_{group_name}_score_mean"] = stats["score_mean"]
    row[f"topo_{group_name}_score_max"] = stats["score_max"]
    row[f"topo_{group_name}_pressure"] = stats["pressure"]
    for idx in range(1, 6):
        item = group_rows[idx - 1] if idx <= len(group_rows) else {}
        row[f"topo_{group_name}_{idx}_distance_atr"] = item.get("distance_atr")
        row[f"topo_{group_name}_{idx}_score"] = item.get("score")
        row[f"topo_{group_name}_{idx}_percentile"] = item.get("percentile")
        row[f"topo_{group_name}_{idx}_price_distance"] = item.get("price_distance")
        row[f"topo_{group_name}_{idx}_age_bars"] = item.get("age_bars")


def add_pairwise_group_fields(row: Dict[str, Any], group_name: str) -> None:
    density_values: List[float] = []
    gap_values: List[float] = []
    score_deltas: List[float] = []
    score_gap_abs_values: List[float] = []
    farther_flags: List[float] = []
    for left, right in [(1, 2), (2, 3), (3, 4), (4, 5)]:
        left_distance = as_float(row.get(f"topo_{group_name}_{left}_distance_atr"))
        right_distance = as_float(row.get(f"topo_{group_name}_{right}_distance_atr"))
        left_price_distance = as_float(row.get(f"topo_{group_name}_{left}_price_distance"))
        right_price_distance = as_float(row.get(f"topo_{group_name}_{right}_price_distance"))
        left_score = as_float(row.get(f"topo_{group_name}_{left}_score"))
        right_score = as_float(row.get(f"topo_{group_name}_{right}_score"))
        left_age = as_float(row.get(f"topo_{group_name}_{left}_age_bars"))
        right_age = as_float(row.get(f"topo_{group_name}_{right}_age_bars"))
        prefix = f"topo_{group_name}_{left}_{right}"
        distance_gap = right_distance - left_distance if right_distance is not None and left_distance is not None else None
        score_delta = right_score - left_score if right_score is not None and left_score is not None else None
        score_sum = (left_score or 0.0) + (right_score or 0.0) if left_score is not None or right_score is not None else None
        score_gap_abs = abs(score_delta) if score_delta is not None else None
        density = safe_div(score_sum, abs(distance_gap)) if distance_gap is not None else None
        farther_flag = float(right_score > left_score) if right_score is not None and left_score is not None else None

        row[f"{prefix}_distance_gap_atr"] = distance_gap
        row[f"{prefix}_price_gap"] = (
            abs(right_price_distance - left_price_distance)
            if right_price_distance is not None and left_price_distance is not None
            else None
        )
        row[f"{prefix}_score_delta"] = score_delta
        row[f"{prefix}_score_gap_abs"] = score_gap_abs
        row[f"{prefix}_score_ratio"] = safe_div(right_score, left_score)
        row[f"{prefix}_score_delta_per_atr_gap"] = safe_div(score_delta, abs(distance_gap)) if distance_gap is not None else None
        row[f"{prefix}_score_sum_per_atr_gap"] = density
        row[f"{prefix}_age_delta_bars"] = right_age - left_age if right_age is not None and left_age is not None else None
        row[f"{prefix}_nearer_score_dominance"] = (
            left_score - right_score if left_score is not None and right_score is not None else None
        )
        row[f"{prefix}_farther_stronger_flag"] = farther_flag

        if density is not None:
            density_values.append(density)
        if distance_gap is not None:
            gap_values.append(abs(distance_gap))
        if score_delta is not None:
            score_deltas.append(score_delta)
        if score_gap_abs is not None:
            score_gap_abs_values.append(score_gap_abs)
        if farther_flag is not None:
            farther_flags.append(farther_flag)

    distances = [
        as_float(row.get(f"topo_{group_name}_{idx}_distance_atr"))
        for idx in range(1, 6)
        if as_float(row.get(f"topo_{group_name}_{idx}_distance_atr")) is not None
    ]
    scores = [
        as_float(row.get(f"topo_{group_name}_{idx}_score"))
        for idx in range(1, 6)
        if as_float(row.get(f"topo_{group_name}_{idx}_score")) is not None
    ]
    row[f"topo_{group_name}_ranked_distance_gap_min_atr"] = min(gap_values) if gap_values else None
    row[f"topo_{group_name}_ranked_distance_gap_mean_atr"] = float(np.mean(gap_values)) if gap_values else None
    row[f"topo_{group_name}_ranked_distance_gap_max_atr"] = max(gap_values) if gap_values else None
    row[f"topo_{group_name}_ranked_distance_gap_std_atr"] = float(np.std(gap_values, ddof=0)) if gap_values else None
    row[f"topo_{group_name}_ranked_distance_compression_ratio"] = (
        safe_div(min(gap_values), max(gap_values)) if gap_values else None
    )
    row[f"topo_{group_name}_ranked_score_delta_mean"] = float(np.mean(score_deltas)) if score_deltas else None
    row[f"topo_{group_name}_ranked_score_gap_abs_mean"] = (
        float(np.mean(score_gap_abs_values)) if score_gap_abs_values else None
    )
    row[f"topo_{group_name}_ranked_score_gap_abs_max"] = max(score_gap_abs_values) if score_gap_abs_values else None
    row[f"topo_{group_name}_ranked_pair_density_max"] = max(density_values) if density_values else None
    row[f"topo_{group_name}_ranked_pair_density_mean"] = float(np.mean(density_values)) if density_values else None
    row[f"topo_{group_name}_ranked_farther_stronger_share"] = float(np.mean(farther_flags)) if farther_flags else None
    row[f"topo_{group_name}_ranked_score_range"] = (max(scores) - min(scores)) if len(scores) >= 2 else None
    row[f"topo_{group_name}_ranked_distance_range_atr"] = (
        (max(distances) - min(distances)) if len(distances) >= 2 else None
    )
    if len(distances) >= 2 and len(scores) >= 2 and len(distances) == len(scores) and np.var(distances) > 1e-12:
        row[f"topo_{group_name}_ranked_score_vs_distance_slope"] = float(
            np.cov(distances, scores, ddof=0)[0, 1] / np.var(distances)
        )
    else:
        row[f"topo_{group_name}_ranked_score_vs_distance_slope"] = None


def add_candidate_topology_features(row: Dict[str, Any], candidates: List[Dict[str, str]]) -> None:
    if not candidates:
        row["topology_scored_candidate_context_available"] = 0
        return
    ref_price = as_float(row.get("entry_price")) or as_float(row.get("signal_price"))
    atr = as_float(row.get("dt_liq_atr20_at_decision")) or as_float(row.get("tech_atr20"))
    groups = split_scored_candidates_by_topology(candidates, ref_price, atr)
    row["topology_scored_candidate_context_available"] = 1
    row["topology_scored_candidate_count"] = float(sum(len(group_rows) for group_rows in groups.values()))
    row["topology_percentile_source"] = "scored_candidate_batch_percentile"

    for group_name, group_rows in groups.items():
        add_ranked_group_fields(row, group_name, group_rows)
        add_pairwise_group_fields(row, group_name)

    bsl_above = group_score_stats(groups["bsl_above"])
    bsl_below = group_score_stats(groups["bsl_below"])
    ssl_above = group_score_stats(groups["ssl_above"])
    ssl_below = group_score_stats(groups["ssl_below"])
    is_short = str(row.get("side") or row.get("direction") or "").strip().lower() == "short"
    target_group = ssl_below if is_short else bsl_above
    stop_group = bsl_above if is_short else ssl_below
    target_nearest_score = row.get("topo_ssl_below_1_score" if is_short else "topo_bsl_above_1_score")
    target_nearest_distance = row.get(
        "topo_ssl_below_1_distance_atr" if is_short else "topo_bsl_above_1_distance_atr"
    )
    stop_nearest_score = row.get("topo_bsl_above_1_score" if is_short else "topo_ssl_below_1_score")
    stop_nearest_distance = row.get(
        "topo_bsl_above_1_distance_atr" if is_short else "topo_ssl_below_1_distance_atr"
    )

    row["topo_active_pool_count"] = float(sum(len(group_rows) for group_rows in groups.values()))
    row["topo_active_bsl_count"] = bsl_above["count"] + bsl_below["count"]
    row["topo_active_ssl_count"] = ssl_above["count"] + ssl_below["count"]
    row["topo_nearest_bsl_above_distance_atr"] = row.get("topo_bsl_above_1_distance_atr")
    row["topo_nearest_bsl_above_score"] = row.get("topo_bsl_above_1_score")
    row["topo_nearest_ssl_below_distance_atr"] = row.get("topo_ssl_below_1_distance_atr")
    row["topo_nearest_ssl_below_score"] = row.get("topo_ssl_below_1_score")
    row["topo_nearest_bsl_above_minus_ssl_below_score"] = (
        (as_float(row.get("topo_nearest_bsl_above_score")) or 0.0)
        - (as_float(row.get("topo_nearest_ssl_below_score")) or 0.0)
        if row.get("topo_nearest_bsl_above_score") not in ("", None)
        or row.get("topo_nearest_ssl_below_score") not in ("", None)
        else None
    )
    row["topo_nearest_bsl_above_to_ssl_below_distance_ratio"] = safe_div(
        as_float(row.get("topo_nearest_bsl_above_distance_atr")),
        as_float(row.get("topo_nearest_ssl_below_distance_atr")),
    )
    row["topo_upside_minus_downside_pressure"] = (
        (bsl_above["pressure"] or 0.0) - (ssl_below["pressure"] or 0.0)
        if bsl_above["pressure"] is not None or ssl_below["pressure"] is not None
        else None
    )
    row["topo_upside_to_downside_pressure_ratio"] = safe_div(bsl_above["pressure"], ssl_below["pressure"])
    row["topo_upside_score_per_pool"] = (
        0.0 if bsl_above["count"] == 0 else safe_div(bsl_above["score_sum"], bsl_above["count"])
    )
    row["topo_downside_score_per_pool"] = (
        0.0 if ssl_below["count"] == 0 else safe_div(ssl_below["score_sum"], ssl_below["count"])
    )
    row["topo_upside_minus_downside_ranked_score_slope"] = (
        (as_float(row.get("topo_bsl_above_ranked_score_vs_distance_slope")) or 0.0)
        - (as_float(row.get("topo_ssl_below_ranked_score_vs_distance_slope")) or 0.0)
        if row.get("topo_bsl_above_ranked_score_vs_distance_slope") not in ("", None)
        or row.get("topo_ssl_below_ranked_score_vs_distance_slope") not in ("", None)
        else None
    )
    row["topo_upside_to_downside_pair_density_ratio"] = safe_div(
        as_float(row.get("topo_bsl_above_ranked_pair_density_mean")),
        as_float(row.get("topo_ssl_below_ranked_pair_density_mean")),
    )
    row["topo_upside_minus_downside_pair_density"] = (
        (as_float(row.get("topo_bsl_above_ranked_pair_density_mean")) or 0.0)
        - (as_float(row.get("topo_ssl_below_ranked_pair_density_mean")) or 0.0)
        if row.get("topo_bsl_above_ranked_pair_density_mean") not in ("", None)
        or row.get("topo_ssl_below_ranked_pair_density_mean") not in ("", None)
        else None
    )
    row["topo_upside_to_downside_gap_ratio"] = safe_div(
        as_float(row.get("topo_bsl_above_ranked_distance_gap_mean_atr")),
        as_float(row.get("topo_ssl_below_ranked_distance_gap_mean_atr")),
    )

    row["xg_liq_target_side_count"] = target_group["count"]
    row["xg_liq_target_side_score_sum"] = target_group["score_sum"]
    row["xg_liq_target_side_score_mean"] = target_group["score_mean"]
    row["xg_liq_target_side_score_max"] = target_group["score_max"]
    row["xg_liq_target_side_pressure"] = target_group["pressure"]
    row["xg_liq_target_side_nearest_score"] = target_nearest_score
    row["xg_liq_target_side_nearest_distance_atr"] = target_nearest_distance
    row["xg_liq_stop_or_swept_side_count"] = stop_group["count"]
    row["xg_liq_stop_or_swept_side_score_sum"] = stop_group["score_sum"]
    row["xg_liq_stop_or_swept_side_score_mean"] = stop_group["score_mean"]
    row["xg_liq_stop_or_swept_side_score_max"] = stop_group["score_max"]
    row["xg_liq_stop_or_swept_side_pressure"] = stop_group["pressure"]
    row["xg_liq_stop_or_swept_side_nearest_score"] = stop_nearest_score
    row["xg_liq_stop_or_swept_side_nearest_distance_atr"] = stop_nearest_distance
    row["xg_liq_target_minus_stop_pressure"] = (
        (target_group["pressure"] or 0.0) - (stop_group["pressure"] or 0.0)
    )

    row["dt_liq_target_path_pressure"] = bsl_above["pressure"]
    row["dt_liq_target_minus_stop_pressure"] = (
        (bsl_above["pressure"] or 0.0) - (ssl_below["pressure"] or 0.0)
        if bsl_above["pressure"] is not None or ssl_below["pressure"] is not None
        else None
    )
    row["dt_liq_target_path_pool_count"] = bsl_above["count"]
    row["dt_liq_stop_path_pool_count"] = ssl_below["count"]
    row["dt_liq_target_path_proxy_score_sum"] = bsl_above["score_sum"]
    row["dt_liq_swept_clusters_taken_during_signal_count"] = ssl_below["count"]
    row["dt_liq_swept_clusters_taken_during_signal_proxy_sum"] = ssl_below["score_sum"]

    if not is_short and target_nearest_distance not in ("", None):
        for key in ["target_distance_atr", "metric_target_distance_atr", "tech_engine_target_distance_atr"]:
            if row.get(key) in ("", None):
                row[key] = target_nearest_distance


def add_focused_quality_features(frame: pd.DataFrame) -> None:
    atr = num_series(frame, "dt_liq_atr20_at_decision").fillna(num_series(frame, "tech_atr20"))
    if "side" in frame.columns:
        side_multiplier = frame["side"].astype(str).str.lower().eq("short").map({True: -1.0, False: 1.0})
    else:
        side_multiplier = pd.Series(1.0, index=frame.index)
    signal_price = num_series(frame, "signal_price")
    t3_low = num_series(frame, "t3_low_price")
    t2_high = num_series(frame, "t2_high_price")
    sweep_low = num_series(frame, "t1_sweep_low_price")

    imp_net = num_series(frame, "eqp_impulse_t3_to_t2_net_atr")
    imp_abs = num_series(frame, "eqp_impulse_t3_to_t2_abs_net_atr")
    imp_path = num_series(frame, "eqp_impulse_t3_to_t2_path_range_atr_sum")
    imp_eff = num_series(frame, "eqp_impulse_t3_to_t2_path_efficiency")
    imp_acc = num_series(frame, "eqp_impulse_t3_to_t2_acceleration_atr_per_bar")
    imp_body = num_series(frame, "eqp_impulse_t3_to_t2_directional_body_fraction")
    imp_pressure = num_series(frame, "eqp_impulse_t3_to_t2_directional_close_pressure_mean")
    imp_adverse = num_series(frame, "eqp_impulse_t3_to_t2_adverse_excursion_atr")
    imp_fvg = num_series(frame, "eqp_impulse_t3_to_t2_favorable_fvg_gap_atr_sum")

    sweep_abs = num_series(frame, "eqp_sweep_t2_to_t1_abs_net_atr")
    sweep_speed = num_series(frame, "eqp_sweep_t2_to_t1_slope_atr_per_bar").abs()
    sweep_path = num_series(frame, "eqp_sweep_t2_to_t1_path_range_atr_sum")
    sweep_eff = num_series(frame, "eqp_sweep_t2_to_t1_path_efficiency")
    sweep_range_max = num_series(frame, "eqp_sweep_t2_to_t1_range_atr_max")
    sweep_body_max = num_series(frame, "eqp_sweep_t2_to_t1_body_atr_max")
    sweep_body_frac = num_series(frame, "eqp_sweep_t2_to_t1_directional_body_fraction")
    sweep_pressure = num_series(frame, "eqp_sweep_t2_to_t1_directional_close_pressure_mean")
    sweep_reject_wick = num_series(frame, "eqp_sweep_t2_to_t1_opposite_wick_ratio_max")
    sweep_upper_wick = num_series(frame, "eqp_sweep_t2_to_t1_relevant_wick_ratio_max")
    sweep_disp = num_series(frame, "eqp_sweep_t2_to_t1_strong_displacement_count")
    sweep_acc = num_series(frame, "eqp_sweep_t2_to_t1_acceleration_atr_per_bar")

    rev_net = num_series(frame, "eqp_reversal_t1_to_signal_net_atr")
    rev_abs = num_series(frame, "eqp_reversal_t1_to_signal_abs_net_atr")
    rev_speed = num_series(frame, "eqp_reversal_t1_to_signal_slope_atr_per_bar")
    rev_path = num_series(frame, "eqp_reversal_t1_to_signal_path_range_atr_sum")
    rev_eff = num_series(frame, "eqp_reversal_t1_to_signal_path_efficiency")
    rev_fav = num_series(frame, "eqp_reversal_t1_to_signal_favorable_excursion_atr")
    rev_adv = num_series(frame, "eqp_reversal_t1_to_signal_adverse_excursion_atr")
    rev_body = num_series(frame, "eqp_reversal_t1_to_signal_directional_body_fraction")
    rev_pressure = num_series(frame, "eqp_reversal_t1_to_signal_directional_close_pressure_mean")
    rev_close_max = num_series(frame, "eqp_reversal_t1_to_signal_directional_close_pressure_max")
    rev_body_max = num_series(frame, "eqp_reversal_t1_to_signal_body_atr_max")
    rev_range_max = num_series(frame, "eqp_reversal_t1_to_signal_range_atr_max")
    rev_disp = num_series(frame, "eqp_reversal_t1_to_signal_strong_displacement_count")
    rev_fvg = num_series(frame, "eqp_reversal_t1_to_signal_favorable_fvg_gap_atr_sum")
    rev_fvg_max = num_series(frame, "eqp_reversal_t1_to_signal_favorable_fvg_gap_atr_max")
    rev_opp_wick = num_series(frame, "eqp_reversal_t1_to_signal_opposite_wick_ratio_max")
    rev_acc = num_series(frame, "eqp_reversal_t1_to_signal_acceleration_atr_per_bar")

    pre_sweep_range = num_series(frame, "eqp_pre_sweep_6_range_atr_mean")
    pre_sweep_body = num_series(frame, "eqp_pre_sweep_6_body_atr_mean")
    pre_sweep_body_ratio = num_series(frame, "eqp_pre_sweep_6_body_to_range_mean")
    pre_sweep_pressure = num_series(frame, "eqp_pre_sweep_6_directional_close_pressure_mean")
    pre_sweep_wick = num_series(frame, "eqp_pre_sweep_6_relevant_wick_ratio_max")

    pre_signal_range = num_series(frame, "eqp_pre_signal_6_range_atr_mean")
    pre_signal_range_max = num_series(frame, "eqp_pre_signal_6_range_atr_max")
    pre_signal_body = num_series(frame, "eqp_pre_signal_6_body_atr_mean")
    pre_signal_body_ratio = num_series(frame, "eqp_pre_signal_6_body_to_range_mean")
    pre_signal_pressure = num_series(frame, "eqp_pre_signal_6_directional_close_pressure_mean")
    pre_signal_opp_wick = num_series(frame, "eqp_pre_signal_6_opposite_wick_ratio_max")
    pre_signal_disp = num_series(frame, "eqp_pre_signal_6_strong_displacement_count")
    pre_signal_fvg = num_series(frame, "eqp_pre_signal_6_favorable_fvg_gap_atr_sum")

    reclaim_close = num_series(frame, "eqp_signal_reclaim_t2_close_atr")
    reclaim_high = num_series(frame, "eqp_signal_reclaim_t2_high_atr")
    reversal_pct = num_series(frame, "eqp_signal_reversal_from_sweep_pct")

    frame["fq_impulse_clean_power"] = imp_net * imp_eff * imp_body * imp_pressure
    frame["fq_impulse_exhaustion_drag"] = div_series(imp_adverse, imp_abs)
    frame["fq_impulse_fvg_per_path"] = div_series(imp_fvg, imp_path)
    frame["fq_impulse_acceleration_per_path"] = div_series(imp_acc, imp_path)

    frame["fq_sweep_depth_atr"] = sweep_abs
    frame["fq_sweep_speed_atr_per_bar"] = sweep_speed
    frame["fq_sweep_range_expansion"] = div_series(sweep_range_max, pre_sweep_range)
    frame["fq_sweep_body_expansion"] = div_series(sweep_body_max, pre_sweep_body)
    frame["fq_sweep_displacement_power"] = sweep_abs * sweep_speed * sweep_body_frac
    frame["fq_sweep_clean_displacement"] = sweep_disp * sweep_eff * sweep_body_frac
    frame["fq_sweep_rejection_power"] = sweep_reject_wick * sweep_abs
    frame["fq_sweep_upper_wick_pressure"] = sweep_upper_wick * sweep_abs
    frame["fq_sweep_acceleration_x_depth"] = sweep_acc * sweep_abs
    frame["fq_sweep_pressure_x_efficiency"] = sweep_pressure * sweep_eff

    frame["fq_reversal_net_atr"] = rev_net
    frame["fq_reversal_speed_atr_per_bar"] = rev_speed
    frame["fq_reversal_path_efficiency"] = rev_eff
    frame["fq_reversal_body_pressure"] = rev_body * rev_pressure
    frame["fq_reversal_displacement_power"] = rev_disp * rev_body_max * rev_eff
    frame["fq_reversal_fvg_reaction"] = rev_fvg * rev_eff
    frame["fq_reversal_fvg_per_path"] = div_series(rev_fvg, rev_path)
    frame["fq_reversal_max_fvg_per_range"] = div_series(rev_fvg_max, rev_range_max)
    frame["fq_reversal_adverse_drag"] = div_series(rev_adv, rev_fav)
    frame["fq_reversal_opposite_wick_risk"] = rev_opp_wick * rev_abs
    frame["fq_reversal_acceleration_x_body"] = rev_acc * rev_body
    frame["fq_reversal_close_pressure_power"] = rev_close_max * rev_body_max

    frame["fq_reversal_vs_sweep_speed"] = div_series(rev_speed, sweep_speed)
    frame["fq_reversal_vs_sweep_range"] = div_series(rev_abs, sweep_abs)
    frame["fq_reversal_vs_sweep_path"] = div_series(rev_path, sweep_path)
    frame["fq_reversal_efficiency_minus_sweep"] = rev_eff - sweep_eff
    frame["fq_reversal_pressure_minus_sweep"] = rev_pressure - sweep_pressure
    frame["fq_reversal_body_minus_sweep"] = rev_body - sweep_body_frac
    frame["fq_reversal_fvg_vs_sweep_depth"] = div_series(rev_fvg, sweep_abs)
    frame["fq_reversal_disp_vs_sweep_disp"] = div_series(rev_disp, sweep_disp)
    frame["fq_reversal_strength_after_deep_sweep"] = rev_net * sweep_abs * rev_eff
    frame["fq_bad_combo_deep_sweep_weak_reversal"] = div_series(sweep_abs, rev_abs) * (1 - rev_eff)

    frame["fq_signal_reclaim_close_atr"] = reclaim_close
    frame["fq_signal_reclaim_high_atr"] = reclaim_high
    frame["fq_signal_reclaim_vs_sweep_depth"] = div_series(reclaim_high, sweep_abs)
    frame["fq_signal_reversal_pct"] = reversal_pct
    frame["fq_signal_reclaim_x_reversal_pct"] = reclaim_high * reversal_pct
    frame["fq_signal_close_reclaim_x_rev_eff"] = reclaim_close * rev_eff

    frame["fq_pre_signal_range_vs_pre_sweep"] = div_series(pre_signal_range, pre_sweep_range)
    frame["fq_pre_signal_body_vs_pre_sweep"] = div_series(pre_signal_body, pre_sweep_body)
    frame["fq_pre_signal_pressure_minus_pre_sweep"] = pre_signal_pressure - pre_sweep_pressure
    frame["fq_pre_signal_body_ratio_minus_pre_sweep"] = pre_signal_body_ratio - pre_sweep_body_ratio
    frame["fq_pre_signal_displacement_density"] = div_series(pre_signal_disp, num_series(frame, "eqp_pre_signal_6_bars"))
    frame["fq_pre_signal_fvg_per_range"] = div_series(pre_signal_fvg, pre_signal_range)
    frame["fq_pre_signal_exhaustion_risk"] = pre_signal_opp_wick * pre_signal_range_max
    frame["fq_pre_sweep_wick_x_body"] = pre_sweep_wick * pre_sweep_body_ratio

    frame["fq_stop_buffer_signal_to_sweep_atr"] = div_series(side_multiplier * (signal_price - sweep_low), atr)
    frame["fq_stop_buffer_signal_to_t3_atr"] = div_series(side_multiplier * (signal_price - t3_low), atr)
    frame["fq_sweep_break_below_t3_atr"] = div_series(side_multiplier * (t3_low - sweep_low), atr)
    frame["fq_stop_buffer_vs_sweep_depth"] = div_series(
        side_multiplier * (signal_price - sweep_low),
        side_multiplier * (t2_high - sweep_low),
    )
    frame["fq_stop_risk_vs_reversal_net"] = div_series(side_multiplier * (signal_price - sweep_low), rev_net * atr)

    frame["fq_bull_fvg_zone_size_atr"] = div_series(num_series(frame, "fvg_zone_size"), atr)
    frame["fq_bull_fvg_sweep_position"] = num_series(frame, "fvg_position_of_sweep")
    frame["fq_bull_fvg_age"] = num_series(frame, "bull_fvg_age")
    frame["fq_bull_fvg_fill"] = num_series(frame, "bull_fvg_fill")
    frame["fq_bull_fvg_retest"] = num_series(frame, "bull_fvg_retest")
    nearest_bull_fvg_distance = num_series(frame, "dt_liq_nearest_bull_fvg_distance_atr").fillna(
        num_series(frame, "tech_nearest_bull_fvg_distance_atr20")
    )
    nearest_bull_fvg_fill = num_series(frame, "dt_liq_nearest_bull_fvg_fill_pct").fillna(
        num_series(frame, "tech_nearest_bull_fvg_fill_pct_at_decision")
    )
    nearest_bull_fvg_age = num_series(frame, "dt_liq_nearest_bull_fvg_age_bars").fillna(
        num_series(frame, "tech_nearest_bull_fvg_age_bars_at_decision")
    )
    nearest_bull_fvg_size = num_series(frame, "dt_liq_nearest_bull_fvg_remaining_size_atr").fillna(
        num_series(frame, "tech_nearest_bull_fvg_remaining_size_atr20")
    )
    frame["fq_nearest_bull_fvg_distance_atr"] = nearest_bull_fvg_distance
    frame["fq_nearest_bull_fvg_fill_x_age"] = (
        nearest_bull_fvg_fill * nearest_bull_fvg_age
    )
    frame["fq_nearest_bull_fvg_size_per_distance"] = div_series(nearest_bull_fvg_size, nearest_bull_fvg_distance)

    frame["fq_xg_target_pressure_advantage"] = num_series(frame, "xg_liq_target_minus_stop_pressure")
    frame["fq_xg_target_max_advantage"] = num_series(frame, "xg_liq_target_max_minus_stop_max")
    frame["fq_xg_target_score_per_distance"] = div_series(
        num_series(frame, "xg_liq_target_side_nearest_score"),
        num_series(frame, "xg_liq_target_side_nearest_distance_atr"),
    )
    frame["fq_xg_target_pressure_per_distance"] = div_series(
        num_series(frame, "xg_liq_target_side_pressure"),
        num_series(frame, "xg_liq_target_side_nearest_distance_atr"),
    )
    frame["fq_dt_target_pressure_advantage"] = num_series(frame, "dt_liq_target_minus_stop_pressure")
    frame["fq_dt_target_pressure_per_pool"] = div_series(
        num_series(frame, "dt_liq_target_path_pressure"),
        num_series(frame, "dt_liq_target_path_pool_count"),
    )
    frame["fq_dt_target_stack_score_per_distance"] = div_series(
        num_series(frame, "dt_liq_target_path_proxy_score_sum"),
        num_series(frame, "dt_liq_nearest_target_distance_atr"),
    )
    frame["fq_dt_target_vs_stop_stack_count"] = (
        num_series(frame, "dt_liq_target_path_pool_count") - num_series(frame, "dt_liq_stop_path_pool_count")
    )
    frame["fq_topology_score_distance_edge"] = num_series(frame, "topology_target_score_per_atr")
    frame["fq_topology_pressure_edge"] = num_series(frame, "topology_bsl_ssl_pressure_diff")
    for zero_when_no_target in [
        "fq_reversal_max_fvg_per_range",
        "fq_xg_target_score_per_distance",
        "fq_xg_target_pressure_per_distance",
        "fq_dt_target_pressure_per_pool",
        "fq_dt_target_stack_score_per_distance",
        "fq_topology_score_distance_edge",
    ]:
        frame[zero_when_no_target] = num_series(frame, zero_when_no_target).fillna(0)

    market_state_uptrend = num_series(frame, "dt_liq_market_state_uptrend").fillna(num_series(frame, "tech_market_state_uptrend"))
    market_state_downtrend = num_series(frame, "dt_liq_market_state_downtrend").fillna(num_series(frame, "tech_market_state_downtrend"))
    frame["fq_regime_uptrend_x_reversal"] = market_state_uptrend * rev_net
    frame["fq_regime_downtrend_x_reversal"] = market_state_downtrend * rev_net
    frame["fq_regime_range_x_reclaim"] = num_series(frame, "dt_liq_market_state_range") * reclaim_high
    frame["fq_regime_uptrend_x_target_pressure"] = num_series(frame, "dt_liq_market_state_uptrend") * num_series(frame, "xg_liq_target_side_pressure")

    frame["fq_real_reversal_composite"] = (rev_net * rev_eff * rev_body * reclaim_high).replace([np.inf, -np.inf], np.nan)
    frame["fq_fvg_reaction_composite"] = (rev_fvg * rev_eff * reclaim_high).replace([np.inf, -np.inf], np.nan)
    frame["fq_sweep_then_reversal_composite"] = (
        sweep_abs * sweep_reject_wick * rev_net * rev_eff
    ).replace([np.inf, -np.inf], np.nan)
    frame["fq_fake_reversal_risk_composite"] = (
        frame["fq_reversal_adverse_drag"].fillna(0)
        + frame["fq_reversal_opposite_wick_risk"].fillna(0)
        + frame["fq_pre_signal_exhaustion_risk"].fillna(0)
        - reclaim_high.fillna(0)
    )
    frame["fq_target_worthiness_composite"] = (
        frame["fq_xg_target_pressure_advantage"].fillna(0)
        + frame["fq_dt_target_pressure_advantage"].fillna(0)
        + frame["fq_topology_pressure_edge"].fillna(0)
    )
    frame["fq_entry_quality_composite"] = (
        frame["fq_real_reversal_composite"].fillna(0)
        + frame["fq_fvg_reaction_composite"].fillna(0)
        + frame["fq_target_worthiness_composite"].fillna(0)
        - frame["fq_fake_reversal_risk_composite"].fillna(0)
    )


def add_goal_features(frame: pd.DataFrame) -> None:
    reversal_impulse = num_series(frame, "cq_up_impulse_score").fillna(0)
    drop_impulse = num_series(frame, "cq_down_impulse_score").fillna(0)
    reversal_body = num_series(frame, "cq_up_green_body_atr_sum").fillna(0)
    drop_body = num_series(frame, "cq_down_red_body_atr_sum").fillna(0)
    reversal_eff = num_series(frame, "fq_reversal_efficiency_minus_sweep").fillna(0)
    reversal_speed = num_series(frame, "fq_reversal_speed_atr_per_bar").fillna(0)
    sweep_speed = num_series(frame, "fq_sweep_speed_atr_per_bar").fillna(0)
    target_pressure = num_series(frame, "dt_liq_target_path_pressure").fillna(0)
    target_count = num_series(frame, "target_bsl_count_above").fillna(0)
    target_score_sum = num_series(frame, "topo_bsl_above_score_sum").fillna(0)
    target_side_count = num_series(frame, "xg_liq_target_side_count").fillna(0)
    bsl2_score = num_series(frame, "dt_liq_bsl_above_2_proxy_score").fillna(0)
    bsl2_density = num_series(frame, "dt_liq_bsl_above_2_density").fillna(0)
    bsl2_distance = num_series(frame, "dt_liq_bsl_above_2_distance_atr").fillna(0)
    bsl_gap_12 = num_series(frame, "topo_bsl_above_1_2_distance_gap_atr").fillna(0)
    upside_pressure = num_series(frame, "topo_target_path_pressure").fillna(0)
    downside_pressure = num_series(frame, "dt_liq_ssl_below_pressure").fillna(0)
    stop_nearest = num_series(frame, "xg_liq_stop_or_swept_side_nearest_score").fillna(0)
    stop_mean = num_series(frame, "xg_liq_stop_or_swept_side_score_mean").fillna(0)
    swept_count = num_series(frame, "dt_liq_swept_clusters_taken_during_signal_count").fillna(0)
    swept_sum = num_series(frame, "dt_liq_swept_clusters_taken_during_signal_proxy_sum").fillna(0)
    fake_risk = num_series(frame, "fq_fake_reversal_risk_composite").fillna(0)
    fvg_quality = num_series(frame, "fvg_react_quality_composite").fillna(0)
    two_leg = num_series(frame, "cq_clean_two_leg_composite").fillna(0)
    down_clean = num_series(frame, "cq_down_clean_drop_score").fillna(0)
    down_no_wick = num_series(frame, "cq_down_red_no_upper_wick_ratio").fillna(0)
    up_close_pos = num_series(frame, "cq_up_green_close_position_mean").fillna(0)
    down_close_pos = num_series(frame, "cq_down_red_close_position_mean").fillna(0)

    frame["goal_reversal_impulse_edge"] = div_series(reversal_impulse + 0.05, drop_impulse + 0.05).round(6)
    frame["goal_reversal_body_edge"] = div_series(reversal_body + 0.05, drop_body + 0.05).round(6)
    frame["goal_reversal_efficiency_edge"] = (reversal_eff + up_close_pos - down_close_pos).round(6)
    frame["goal_reversal_speed_vs_sweep_speed"] = div_series(reversal_speed + 0.05, sweep_speed + 0.05).round(6)
    frame["goal_target_pressure_x_reversal_edge"] = (
        log1p_series(target_pressure) * frame["goal_reversal_impulse_edge"].clip(upper=10)
    ).round(6)
    frame["goal_target_count_x_reversal_edge"] = (
        log1p_series(target_count) * frame["goal_reversal_body_edge"].clip(upper=10)
    ).round(6)
    frame["goal_second_bsl_quality_per_gap"] = div_series(
        bsl2_score * (1 + bsl2_density), bsl2_distance + bsl_gap_12 + 0.25
    ).round(6)
    frame["goal_second_bsl_density_x_score"] = (log1p_series(bsl2_density) * bsl2_score).round(6)
    frame["goal_target_path_pressure_per_stop_pressure"] = div_series(
        target_pressure + 1, downside_pressure + stop_mean + 1
    ).round(6)
    frame["goal_topology_upside_per_downside_pressure"] = div_series(
        upside_pressure + 1, downside_pressure + 1
    ).round(6)
    frame["goal_stop_side_drag_risk"] = (log1p_series(stop_nearest + stop_mean) * log1p_series(swept_count + 1)).round(6)
    frame["goal_sweep_overextension_risk"] = (sweep_speed * (1 + down_clean) * (1 + down_no_wick)).round(6)
    frame["goal_swept_cluster_drag_risk"] = (
        log1p_series(swept_sum) * (1 + down_clean) * log1p_series(stop_nearest + 1)
    ).round(6)
    frame["goal_fake_reversal_adjusted_path"] = div_series(
        target_pressure * (1 + fvg_quality), 1 + fake_risk + frame["goal_sweep_overextension_risk"].fillna(0)
    ).round(6)
    frame["goal_clean_reversal_after_dirty_sweep"] = div_series(
        two_leg * (1 + reversal_speed), 1 + down_clean + sweep_speed
    ).round(6)
    frame["goal_path_minus_drag_score"] = (
        log1p_series(target_pressure + target_score_sum)
        + frame["goal_reversal_efficiency_edge"].fillna(0)
        - log1p_series(frame["goal_stop_side_drag_risk"].fillna(0) + frame["goal_sweep_overextension_risk"].fillna(0))
    ).round(6)
    frame["goal_target_breadth_quality"] = (log1p_series(target_count + target_side_count) * log1p_series(target_score_sum)).round(6)
    frame["goal_bsl_stack_ladder_quality"] = div_series(
        target_score_sum * log1p_series(target_count), 1 + bsl_gap_12 + bsl2_distance
    ).round(6)
    frame["goal_current_fvg_reaction_x_path"] = (fvg_quality * log1p_series(target_pressure + target_count)).round(6)
    frame["goal_current_fvg_reaction_minus_fake_risk"] = (
        fvg_quality - fake_risk - frame["goal_sweep_overextension_risk"].fillna(0) * 0.1
    ).round(6)


def add_approved_long_derived_features(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not rows:
        return rows
    frame = pd.DataFrame(rows)
    add_focused_quality_features(frame)
    frame["fvg_react_reclaim_speed_proxy"] = num_series(frame, "fvg_react_reclaim_speed_proxy").fillna(
        num_series(frame, "fvg_react_reclaim_from_sweep_pct") * num_series(frame, "fq_reversal_speed_atr_per_bar")
    )
    add_goal_features(frame)
    frame["entry_variant_delay_x_reversal"] = (
        num_series(frame, "entry_variant_delay_bars") * num_series(frame, "fq_real_reversal_composite")
    )
    frame["entry_variant_delay_x_target_pressure"] = (
        num_series(frame, "entry_variant_delay_bars") * num_series(frame, "dt_liq_target_path_pressure")
    )

    ladder = num_series(frame, "goal_bsl_stack_ladder_quality").fillna(0)
    breadth = num_series(frame, "goal_target_breadth_quality").fillna(0)
    target_pressure = num_series(frame, "dt_liq_target_path_pressure").fillna(0)
    bsl_count = num_series(frame, "topo_active_bsl_count").fillna(num_series(frame, "target_bsl_count_above")).fillna(0)
    stop_drag = num_series(frame, "goal_stop_side_drag_risk").fillna(0)
    fake_adjusted = num_series(frame, "goal_fake_reversal_adjusted_path").fillna(0)
    reversal = num_series(frame, "fq_real_reversal_composite").fillna(0)
    rev_drop = num_series(frame, "cq_reversal_vs_drop_impulse").fillna(0)
    down_impulse = num_series(frame, "cq_down_impulse_score").fillna(0)
    exhaustion = num_series(frame, "fq_impulse_exhaustion_drag").fillna(0)
    path_per_stop = num_series(frame, "goal_target_path_pressure_per_stop_pressure").fillna(0)
    second_quality = num_series(frame, "goal_second_bsl_quality_per_gap").fillna(0)

    reverse_gate = log1p_series(reversal) + log1p_series(rev_drop)
    drag_gate = 1.0 / (1.0 + stop_drag.clip(lower=0.0) + down_impulse.clip(lower=0.0) + exhaustion.clip(lower=0.0))
    frame["gtopo_ladder_x_reversal_gate"] = ladder * reverse_gate
    frame["gtopo_ladder_x_drag_gate"] = ladder * drag_gate
    frame["gtopo_ladder_x_reversal_x_drag_gate"] = ladder * reverse_gate * drag_gate
    frame["gtopo_breadth_x_reversal_gate"] = breadth * reverse_gate
    frame["gtopo_target_pressure_x_reversal_gate"] = target_pressure * reverse_gate
    frame["gtopo_target_pressure_x_drag_gate"] = target_pressure * drag_gate
    frame["gtopo_count_x_reversal_gate"] = bsl_count * reverse_gate
    frame["gtopo_fake_adjusted_x_drag_gate"] = fake_adjusted * drag_gate
    frame["gtopo_second_quality_x_reversal_gate"] = second_quality * reverse_gate
    frame["gtopo_path_per_stop_x_reversal_gate"] = path_per_stop * reverse_gate
    frame["gtopo_path_per_stop_x_drag_gate"] = path_per_stop * drag_gate
    frame["gtopo_reversal_gate_minus_drag"] = reverse_gate - stop_drag - down_impulse - exhaustion
    frame["gtopo_ladder_per_stop_drag"] = ladder / (1.0 + stop_drag.clip(lower=0.0))
    frame["gtopo_target_pressure_per_stop_drag"] = target_pressure / (1.0 + stop_drag.clip(lower=0.0))
    frame["gtopo_breadth_per_stop_drag"] = breadth / (1.0 + stop_drag.clip(lower=0.0))

    gap12 = num_series(frame, "topo_bsl_above_1_2_distance_gap_atr").fillna(0)
    score1 = num_series(frame, "topo_bsl_above_1_score").fillna(0)
    score2 = num_series(frame, "topo_bsl_above_2_score").fillna(0)
    dist2 = num_series(frame, "topo_bsl_above_2_distance_atr").fillna(0)
    fvg_quality = num_series(frame, "fvg_react_quality_composite").fillna(0)
    clean_reversal = log1p_series(reversal) + log1p_series(rev_drop)
    dirty_down = log1p_series(down_impulse) + log1p_series(stop_drag)
    path_quality = log1p_series(ladder) + log1p_series(target_pressure)
    frame["rerank_clean_reversal_minus_dirty_down"] = clean_reversal - dirty_down
    frame["rerank_path_quality_minus_stop_drag"] = path_quality - log1p_series(stop_drag)
    frame["rerank_ladder_x_clean_reversal"] = ladder * clean_reversal
    frame["rerank_ladder_per_dirty_down"] = ladder / (1.0 + dirty_down)
    frame["rerank_pressure_per_dirty_down"] = target_pressure / (1.0 + dirty_down)
    frame["rerank_second_bsl_quality"] = score2 / (1.0 + dist2.clip(lower=0.0))
    frame["rerank_first_second_score_sum_per_gap"] = (score1 + score2) / (1.0 + gap12.clip(lower=0.0))
    frame["rerank_count_x_second_bsl_quality"] = bsl_count * frame["rerank_second_bsl_quality"]
    frame["rerank_breadth_x_fvg_quality"] = breadth * log1p_series(fvg_quality)
    frame["rerank_clean_reversal_x_fvg_quality"] = clean_reversal * log1p_series(fvg_quality)

    premium_pos = num_series(frame, "tech_premium_discount_position_pct").fillna(0)
    deep_discount = num_series(frame, "tech_premium_discount_zone_deep_discount").fillna(0)
    bb_width = num_series(frame, "tech_bb20_width_percentile_100").fillna(0)
    bull_stack = num_series(frame, "tech_ema_bull_stack_8_13_21").fillna(0)
    bull_stack_50 = num_series(frame, "tech_ema_bull_stack_8_13_21_50").fillna(0)
    active_bull_fvgs = num_series(frame, "tech_structure_active_bullish_fvgs").fillna(0)
    bull_fvg_age = num_series(frame, "tech_engine_bull_fvg_age").fillna(0)
    frame["foldrisk_bull_stack_pressure"] = bull_stack + bull_stack_50 + bb_width
    frame["foldrisk_deep_discount_pressure"] = deep_discount * target_pressure
    frame["foldrisk_high_vol_pressure"] = bb_width * target_pressure
    frame["foldrisk_low_location_pressure"] = (1.0 - premium_pos.clip(lower=0.0, upper=1.0)) * target_pressure
    frame["foldedge_premium_fvg_quality"] = premium_pos * fvg_quality
    frame["foldedge_mature_fvg_structure"] = log1p_series(active_bull_fvgs) * log1p_series(bull_fvg_age)
    frame["foldedge_reversal_per_target_pressure"] = rev_drop / (1.0 + target_pressure.clip(lower=0.0))
    frame["foldedge_fvg_quality_per_vol"] = fvg_quality / (1.0 + bb_width.clip(lower=0.0))
    frame["foldedge_location_vol_adjusted_fvg"] = premium_pos * fvg_quality / (1.0 + bb_width.clip(lower=0.0))

    fvg_remaining = num_series(frame, "fvg_react_remaining_pct").fillna(0)
    fvg_fill = num_series(frame, "fvg_react_fill_pct").fillna(0)
    sweep_pos = num_series(frame, "fvg_react_sweep_position_clipped").fillna(0)
    nearest_bsl = num_series(frame, "topo_bsl_above_1_distance_atr").fillna(0)
    second_bsl = num_series(frame, "topo_bsl_above_2_distance_atr").fillna(0)
    nearest_score = num_series(frame, "topo_bsl_above_1_score").fillna(0)
    second_score = num_series(frame, "topo_bsl_above_2_score").fillna(0)
    swept_drag = num_series(frame, "dt_liq_swept_clusters_taken_during_signal_proxy_sum").fillna(0)
    frame["topbucket_fvg_respect_score"] = fvg_remaining + sweep_pos - fvg_fill
    frame["topbucket_target_closeness"] = 1.0 / (1.0 + nearest_bsl.clip(lower=0.0))
    frame["topbucket_second_target_closeness"] = 1.0 / (1.0 + second_bsl.clip(lower=0.0))
    frame["topbucket_near_score_per_distance"] = nearest_score / (1.0 + nearest_bsl.clip(lower=0.0))
    frame["topbucket_second_score_per_distance"] = second_score / (1.0 + second_bsl.clip(lower=0.0))
    frame["topbucket_pressure_per_nearest_distance"] = target_pressure / (1.0 + nearest_bsl.clip(lower=0.0))
    frame["topbucket_fvg_respect_x_target_closeness"] = (
        frame["topbucket_fvg_respect_score"] * frame["topbucket_target_closeness"]
    )
    frame["topbucket_fvg_quality_x_respect"] = fvg_quality * (1.0 + frame["topbucket_fvg_respect_score"])
    frame["topbucket_swept_drag_penalty"] = swept_drag / (1.0 + fvg_remaining.clip(lower=0.0))
    frame["topbucket_target_pressure_minus_drag"] = target_pressure - swept_drag

    clean = frame.replace([np.inf, -np.inf], np.nan)
    return [{key: encode(value) for key, value in row.items()} for row in clean.to_dict(orient="records")]


def feature_group(feature: str) -> str:
    if feature.startswith("topo_") or feature.startswith("gtopo_"):
        return "topology"
    if feature.startswith("xg_") or feature.startswith("dt_liq_"):
        return "liquidity"
    if feature.startswith("macro_"):
        return "macro"
    if feature.startswith("tech_"):
        return "technical"
    if feature.startswith("fq_"):
        return "focused_quality"
    if feature.startswith("cq_"):
        return "candle_quality"
    if feature.startswith("fvg_react_") or feature.startswith("fvg_"):
        return "fvg_reaction"
    if feature.startswith("entry_"):
        return "entry"
    if feature.startswith("goal_") or feature.startswith("rerank_") or feature.startswith("topbucket_"):
        return "composite"
    return "base_signal_setup"


def required_feature_status(
    rows: List[Dict[str, Any]],
    required_features: List[str],
    feature_availability_policy: Dict[str, Any],
    *,
    side: str | None = None,
    approved_inference_contract: bool | None = None,
) -> Dict[str, Any]:
    present_counts: Dict[str, int] = {}
    for feature in required_features:
        count = 0
        for row in rows:
            value = row.get(feature)
            if value not in ("", None):
                count += 1
        present_counts[feature] = count

    row_count = len(rows)
    available = [feature for feature, count in present_counts.items() if row_count and count == row_count]
    partial = [feature for feature, count in present_counts.items() if 0 < count < row_count]
    missing = [feature for feature, count in present_counts.items() if count == 0]
    structural_nullable_missing = [
        feature for feature in missing if is_structural_nullable(feature, feature_availability_policy)
    ]
    blocking_missing = [feature for feature in missing if feature not in set(structural_nullable_missing)]
    feature_complete = len(blocking_missing) == 0
    if approved_inference_contract is None:
        approved_inference_contract = side in (None, "", "long")
    classification_allowed = feature_complete and approved_inference_contract
    if classification_allowed:
        classification_status = "classification_allowed"
    elif feature_complete and not approved_inference_contract:
        classification_status = "short_inference_contract_not_approved"
    else:
        classification_status = "insufficient_data"

    return {
        "side": side,
        "row_count": row_count,
        "required_feature_count": len(required_features),
        "available_all_rows": available,
        "available_partial_rows": partial,
        "missing_all_rows": missing,
        "structural_nullable_missing_all_rows": structural_nullable_missing,
        "blocking_missing_all_rows": blocking_missing,
        "feature_complete": feature_complete,
        "approved_inference_contract": approved_inference_contract,
        "classification_allowed": classification_allowed,
        "classification_status": classification_status,
    }


def build_audit(
    run_id: str,
    rows: List[Dict[str, Any]],
    required_features: List[str],
    short_required_features: List[str],
    events_path: Path,
    candles_path: Path,
    output_path: Path,
    summary_path: Path,
    log_path: Path,
    liquidity_aggregation: Path | None,
    liquidity_scored_candidates: Path | None,
    liquidity_scored_candidate_count: int,
    macro_candles_dir: Path | None,
    macro_join_method: str,
    macro_ticker_count: int,
    liquidity_payload_dir: Path | None,
    liquidity_payload_context_count: int,
    feature_availability_policy: Dict[str, Any],
    signal_inference_contracts: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    long_contract = signal_inference_contracts.get("long", {})
    status = required_feature_status(
        rows,
        required_features,
        feature_availability_policy,
        approved_inference_contract=bool(long_contract.get("approved_inference_contract")),
    )
    row_count = status["row_count"]
    available = status["available_all_rows"]
    partial = status["available_partial_rows"]
    missing = status["missing_all_rows"]
    structural_nullable_missing = status["structural_nullable_missing_all_rows"]
    blocking_missing = status["blocking_missing_all_rows"]
    group_rows: List[Dict[str, Any]] = []
    groups = sorted(set(feature_group(feature) for feature in required_features))
    for group in groups:
        features = [feature for feature in required_features if feature_group(feature) == group]
        available_count = sum(1 for feature in features if feature in available)
        partial_count = sum(1 for feature in features if feature in partial)
        missing_count = sum(1 for feature in features if feature in missing)
        group_rows.append(
            {
                "group": group,
                "required_features": len(features),
                "available_all_rows": available_count,
                "available_partial_rows": partial_count,
                "missing_all_rows": missing_count,
                "available_all_rows_pct": round(available_count / len(features), 6) if features else 0,
            }
        )

    classification_allowed = bool(status["classification_allowed"])
    classification_status = str(status["classification_status"])
    classification_blocker = (
        ""
        if classification_allowed
        else "Production-blocking approved long-model features are still missing on all rows. V2 must not force-score these rows."
    )
    side_rows: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        side = str(row.get("side") or row.get("direction") or "unknown").strip().lower() or "unknown"
        side_rows[side].append(row)
    side_feature_status: Dict[str, Dict[str, Any]] = {}
    for side, subset in sorted(side_rows.items()):
        side_required_features = short_required_features if side == "short" and short_required_features else required_features
        side_contract = signal_inference_contracts.get(side, {})
        side_status = required_feature_status(
            subset,
            side_required_features,
            feature_availability_policy,
            side=side,
            approved_inference_contract=bool(side_contract.get("approved_inference_contract")),
        )
        side_feature_status[side] = {
            "row_count": side_status["row_count"],
            "required_feature_count": side_status["required_feature_count"],
            "available_all_rows_count": len(side_status["available_all_rows"]),
            "available_partial_rows_count": len(side_status["available_partial_rows"]),
            "missing_all_rows_count": len(side_status["missing_all_rows"]),
            "structural_nullable_missing_all_rows_count": len(side_status["structural_nullable_missing_all_rows"]),
            "blocking_missing_all_rows_count": len(side_status["blocking_missing_all_rows"]),
            "feature_complete": side_status["feature_complete"],
            "approved_inference_contract": side_status["approved_inference_contract"],
            "inference_contract_status": side_contract.get("status", "not_approved"),
            "inference_contract_target": side_contract.get("target", ""),
            "inference_contract_score_column": side_contract.get("score_column", ""),
            "inference_contract_checks": side_contract.get("checks", {}),
            "classification_allowed": side_status["classification_allowed"],
            "classification_status": side_status["classification_status"],
            "blocking_missing_features_sample": side_status["blocking_missing_all_rows"][:80],
            "missing_features_sample": side_status["missing_all_rows"][:80],
        }

    write_csv(summary_path, group_rows)
    return {
        "version": "SIGNAL_MODEL_V2_LIVE_FEATURE_BUILD_AUDIT",
        "run_id": run_id,
        "generated_at": utc_stamp(),
        "events": rel(events_path),
        "candles": rel(candles_path),
        "output": rel(output_path),
        "summary": rel(summary_path),
        "log": rel(log_path),
        "liquidity_aggregation": rel(liquidity_aggregation) if liquidity_aggregation else None,
        "liquidity_scored_candidates": rel(liquidity_scored_candidates) if liquidity_scored_candidates else None,
        "liquidity_scored_candidate_count": liquidity_scored_candidate_count,
        "topology_percentile_source": "scored_candidate_batch_percentile" if liquidity_scored_candidate_count else None,
        "macro_candles_dir": rel(macro_candles_dir) if macro_candles_dir else None,
        "macro_join_method": macro_join_method,
        "macro_ticker_count": macro_ticker_count,
        "liquidity_payload_dir": rel(liquidity_payload_dir) if liquidity_payload_dir else None,
        "liquidity_payload_context_count": liquidity_payload_context_count,
        "feature_availability_policy": (
            rel(feature_availability_policy["path"])
            if feature_availability_policy.get("loaded") and feature_availability_policy.get("path")
            else None
        ),
        "feature_availability_policy_version": feature_availability_policy.get("version"),
        "signal_inference_contracts": signal_inference_contracts,
        "row_count": row_count,
        "required_feature_count": len(required_features),
        "long_required_feature_count": len(required_features),
        "short_required_feature_count": len(short_required_features),
        "available_all_rows_count": len(available),
        "available_partial_rows_count": len(partial),
        "missing_all_rows_count": len(missing),
        "structural_nullable_missing_all_rows_count": len(structural_nullable_missing),
        "blocking_missing_all_rows_count": len(blocking_missing),
        "available_all_rows_pct": round(len(available) / len(required_features), 6) if required_features else 0,
        "classification_allowed": classification_allowed,
        "classification_status": classification_status,
        "classification_blocker": classification_blocker,
        "side_feature_status": side_feature_status,
        "missing_features_sample": missing[:80],
        "structural_nullable_missing_features_sample": structural_nullable_missing[:80],
        "blocking_missing_features_sample": blocking_missing[:80],
        "group_summary": group_rows,
        "decision_time_rule": "Features are computed from event fields and candle rows with time <= decision_time. No outcome labels are read.",
    }


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"v2_live_features_{utc_stamp()}"
    log_path = V2_ROOT / "logs" / f"{run_id}.jsonl"
    output_path = args.output or (V2_ROOT / "data" / "features" / f"{run_id}_features.csv")
    audit_path = args.audit or (V2_ROOT / "audits" / f"{run_id}_audit.json")
    summary_path = args.summary or (V2_ROOT / "reports" / f"{run_id}_feature_parity_summary.csv")

    append_jsonl(
        log_path,
        {
            "ts": utc_stamp(),
            "event": "start",
            "events": str(args.events),
            "candles": str(args.candles),
            "liquidity_scored_candidates": (
                str(args.liquidity_scored_candidates) if args.liquidity_scored_candidates else None
            ),
            "liquidity_payload_dir": str(args.liquidity_payload_dir) if args.liquidity_payload_dir else None,
            "macro_candles_dir": str(args.macro_candles_dir) if args.macro_candles_dir else None,
            "feature_availability_policy": str(args.feature_availability_policy) if args.feature_availability_policy else None,
        },
    )
    required_features = load_required_features(args.preprocess)
    short_required_features = (
        load_required_features(args.short_preprocess) if args.short_preprocess and args.short_preprocess.exists() else []
    )
    feature_availability_policy = load_feature_availability_policy(args.feature_availability_policy)
    events = read_csv(args.events)
    candles = add_candle_indicators(read_candles(args.candles))
    macro_frames, macro_join_method = load_macro_frames(args.candles, candles, args.macro_candles_dir, events)
    macro_context = build_macro_context(events, macro_frames, macro_join_method)
    liquidity_payload_raw_context = load_liquidity_payload_context(args.liquidity_payload_dir)
    liquidity_payload_context = build_payload_context(liquidity_payload_raw_context)
    scored_candidate_context = load_scored_liquidity_candidates(args.liquidity_scored_candidates)
    scored_candidate_count = sum(len(items) for items in scored_candidate_context.values())
    rows = [build_row(event, candles, required_features) for event in events]
    for row in rows:
        signal_id = str(row.get("signal_id") or "")
        if signal_id in macro_context:
            row.update(macro_context[signal_id])
        if signal_id in liquidity_payload_context:
            row.update(liquidity_payload_context[signal_id])

    if args.liquidity_aggregation:
        liquidity_rows = read_csv(args.liquidity_aggregation)
        by_signal = {row.get("signal_id"): row for row in liquidity_rows}
        for row in rows:
            aggregate = by_signal.get(row.get("signal_id"))
            if not aggregate:
                continue
            for key, value in aggregate.items():
                if key == "signal_id":
                    continue
                if key in required_features or key.startswith("dt_") or key.startswith("xg_"):
                    row[key] = value
            add_long_topology_aliases_from_liquidity(row)

    if scored_candidate_context:
        for row in rows:
            signal_id = str(row.get("signal_id") or "")
            add_candidate_topology_features(row, scored_candidate_context.get(signal_id, []))
            apply_short_schema_aliases_after_context(row)

    for row in rows:
        apply_payload_fvg_fallbacks(row)
        apply_short_bear_fvg_features(row)
        apply_short_schema_aliases_after_context(row)

    rows = add_approved_long_derived_features(rows)
    for row in rows:
        apply_short_bear_fvg_features(row)
        apply_short_schema_aliases_after_context(row)

    metadata = [
        "signal_id",
        "candidate_row_id",
        "ticker",
        "side",
        "direction",
        "decision_time",
        "signal_time",
        "feature_cutoff_time",
        "entry_model_variant",
        "entry_price",
        "stop_price",
        "risk",
        "v2_feature_builder",
    ]
    required_output_features = list(dict.fromkeys(required_features + short_required_features))
    support_columns = sorted({key for row in rows for key in row} - set(metadata) - set(required_output_features))
    write_csv(output_path, rows, fieldnames=list(dict.fromkeys(metadata + support_columns + required_output_features)))
    signal_inference_contracts = build_signal_inference_contracts(
        long_preprocess=args.preprocess,
        short_preprocess=args.short_preprocess,
        artifact_registry=args.signal_artifact_registry,
        decision_config=args.signal_decision_config,
    )
    audit = build_audit(
        run_id,
        rows,
        required_features,
        short_required_features,
        args.events,
        args.candles,
        output_path,
        summary_path,
        log_path,
        args.liquidity_aggregation,
        args.liquidity_scored_candidates,
        scored_candidate_count,
        args.macro_candles_dir,
        macro_join_method,
        len(macro_frames),
        args.liquidity_payload_dir,
        len(liquidity_payload_raw_context),
        feature_availability_policy,
        signal_inference_contracts,
    )
    write_json(audit_path, audit)
    append_jsonl(
        log_path,
        {
            "ts": utc_stamp(),
            "event": "finish",
            "rows": len(rows),
            "available_all_rows_count": audit["available_all_rows_count"],
            "missing_all_rows_count": audit["missing_all_rows_count"],
            "blocking_missing_all_rows_count": audit["blocking_missing_all_rows_count"],
            "classification_status": audit["classification_status"],
            "audit": rel(audit_path),
        },
    )
    print(f"Wrote {output_path}")
    print(f"Wrote {audit_path}")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
