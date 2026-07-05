from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence

import numpy as np
import pandas as pd


V1_SIGNAL_SCRIPTS = Path(r"D:\Coding\Python Codes\Newtest\Breaker_Based\signal_model\scripts")
V1_SIGNAL_MODEL = Path(r"D:\Coding\Python Codes\Newtest\Breaker_Based\signal_model")
DEFAULT_REGISTRY = V1_SIGNAL_MODEL / "configs" / "trade_system_signal_model_artifacts_v1.json"
DEFAULT_AUDIT = Path(r"D:\Coding\Python Codes\breaker-fvg-signal-v2\audits\v2_raw_signal_score_diagnostics_audit.json")

if str(V1_SIGNAL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(V1_SIGNAL_SCRIPTS))

from score_trade_system_signal_models_v1 import (  # noqa: E402
    add_long_artifact_features,
    gate_mask,
    infer_side,
    score_with_preprocess,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Add V2 dashboard diagnostics for the approved long signal model without changing final trade decisions. "
            "The output preserves existing score/bucket/permission columns and adds ungated raw score plus gate flags."
        )
    )
    parser.add_argument("--input", nargs="+", required=True, help="Input decision CSV path(s) or glob pattern(s).")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact-registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    return parser.parse_args()


def resolve_inputs(items: Sequence[str]) -> List[Path]:
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


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def read_inputs(paths: Sequence[Path]) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for path in paths:
        if not path.exists():
            continue
        frame = read_csv(path)
        frame["_raw_diagnostics_source_file"] = str(path)
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False).fillna("")


def write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, quoting=csv.QUOTE_MINIMAL)


def value_is_blank(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip().lower()
    return text in {"", "nan", "none", "null"}


def as_float(value: Any) -> float | None:
    if value_is_blank(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def bool_text(value: bool) -> str:
    return "true" if bool(value) else "false"


def gate_failures(frame: pd.DataFrame, specs: Sequence[Sequence[Any]], thresholds: Dict[str, Any]) -> pd.Series:
    failures: List[str] = []
    for idx, row in frame.iterrows():
        row_failures: List[str] = []
        for spec in specs:
            col, op, _q = spec
            threshold = as_float(thresholds.get(col))
            value = as_float(row.get(col))
            passed = False
            if value is not None and threshold is not None:
                if op == ">=":
                    passed = value >= threshold
                elif op == "<=":
                    passed = value <= threshold
            if not passed:
                display_value = "missing" if value is None else f"{value:.6g}"
                display_threshold = "missing" if threshold is None else f"{threshold:.6g}"
                row_failures.append(f"{col}={display_value} needs {op}{display_threshold}")
        failures.append("|".join(row_failures))
    return pd.Series(failures, index=frame.index)


def ensure_signal_side(frame: pd.DataFrame) -> None:
    if "signal_model_side" not in frame.columns:
        frame["signal_model_side"] = [infer_side(row) for row in frame.to_dict("records")]
    else:
        missing = frame["signal_model_side"].map(value_is_blank)
        if missing.any():
            inferred = [infer_side(row) for row in frame.loc[missing].to_dict("records")]
            frame.loc[missing, "signal_model_side"] = inferred


def add_diagnostics(frame: pd.DataFrame, registry_path: Path) -> Dict[str, Any]:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    ensure_signal_side(frame)
    for col in [
        "raw_model_score",
        "main_gate_pass",
        "strict_gate_pass",
        "score_gate_suppressed",
        "main_gate_failures",
        "strict_gate_failures",
    ]:
        if col not in frame.columns:
            frame[col] = ""

    long_mask = frame["signal_model_side"].astype(str).str.lower().eq("long")
    if not long_mask.any():
        return {
            "long_rows": 0,
            "long_raw_scored_rows": 0,
            "long_main_gate_pass_rows": 0,
            "long_strict_gate_pass_rows": 0,
        }

    long_info = registry["long"]
    model_path = Path(long_info["model_path"])
    preprocess_path = Path(long_info["preprocess_path"])
    preprocess = json.loads(preprocess_path.read_text(encoding="utf-8"))

    long_frame = frame.loc[long_mask].copy()
    add_long_artifact_features(long_frame)
    main_gate = gate_mask(long_frame, preprocess["main_gate_specs"], preprocess["main_gate_thresholds"])
    strict_gate_base = gate_mask(
        long_frame,
        preprocess["strict_post_gate_specs"],
        preprocess["strict_post_gate_thresholds"],
    )
    strict_gate = main_gate & strict_gate_base
    predictions = score_with_preprocess(model_path, preprocess_path, long_frame)

    main_failures = gate_failures(long_frame, preprocess["main_gate_specs"], preprocess["main_gate_thresholds"])
    strict_failures = gate_failures(
        long_frame,
        preprocess["strict_post_gate_specs"],
        preprocess["strict_post_gate_thresholds"],
    )

    for idx in long_frame.index:
        raw = float(predictions.loc[idx])
        final_score = as_float(frame.loc[idx].get("score"))
        main_pass = bool(main_gate.loc[idx])
        strict_pass = bool(strict_gate.loc[idx])
        frame.loc[idx, "raw_model_score"] = f"{raw:.10f}"
        frame.loc[idx, "main_gate_pass"] = bool_text(main_pass)
        frame.loc[idx, "strict_gate_pass"] = bool_text(strict_pass)
        frame.loc[idx, "score_gate_suppressed"] = bool_text(final_score == 0.0 and not main_pass)
        frame.loc[idx, "main_gate_failures"] = str(main_failures.loc[idx])
        frame.loc[idx, "strict_gate_failures"] = str(strict_failures.loc[idx])

    return {
        "long_rows": int(long_mask.sum()),
        "long_raw_scored_rows": int(len(long_frame)),
        "long_main_gate_pass_rows": int(main_gate.sum()),
        "long_strict_gate_pass_rows": int(strict_gate.sum()),
        "raw_score_min": float(np.nanmin(predictions.to_numpy())) if len(predictions) else None,
        "raw_score_max": float(np.nanmax(predictions.to_numpy())) if len(predictions) else None,
        "raw_score_mean": float(np.nanmean(predictions.to_numpy())) if len(predictions) else None,
    }


def main() -> int:
    args = parse_args()
    input_paths = resolve_inputs(args.input)
    frame = read_inputs(input_paths)
    audit = {
        "version": "SIGNAL_V2_RAW_SCORE_DIAGNOSTICS_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": [str(path) for path in input_paths],
        "output": str(args.output),
        "artifact_registry": str(args.artifact_registry),
        "rows": int(len(frame)),
    }
    audit.update(add_diagnostics(frame, args.artifact_registry))
    write_csv(args.output, frame)
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(json.dumps(audit, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
