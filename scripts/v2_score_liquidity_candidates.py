from __future__ import annotations

import argparse
import hashlib
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from v2_common import BREAKER_BASED_ROOT, V2_ROOT, write_json


BASE_DIR = BREAKER_BASED_ROOT
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from signal_model.scripts.score_liquidity_decision_time_rows_v1 import (  # noqa: E402
    DEFAULT_FEATURE_LIST,
    DEFAULT_TRAIN_D29,
    DEFAULT_TRAIN_D31,
    load_features,
    train_reference_model,
)
from signal_model.scripts.validate_liquidity_decision_time_rows_v1 import validate_rows  # noqa: E402


DEFAULT_MODEL_ARTIFACT = V2_ROOT / "models" / "liquidity_v1_2_d31_pruned_decision_time_xgbregressor.pkl"
DEFAULT_MODEL_AUDIT = V2_ROOT / "models" / "liquidity_v1_2_d31_pruned_decision_time_xgbregressor_audit.json"
DEFAULT_OUTPUT = V2_ROOT / "data" / "liquidity" / "v2_liquidity_candidate_scores.csv"
DEFAULT_AUDIT = V2_ROOT / "audits" / "v2_liquidity_candidate_scores_audit.json"
APPROVED_MODEL_VERSION = "liquidity_v1_2_d31_pruned_decision_time"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Score validated decision-time liquidity candidate rows with the approved V1.2 liquidity model, "
            "using a V2-owned cached model artifact instead of retraining for every ticker."
        )
    )
    parser.add_argument("input_file", type=Path)
    parser.add_argument("--feature-list", type=Path, default=DEFAULT_FEATURE_LIST)
    parser.add_argument("--train-d29", type=Path, default=DEFAULT_TRAIN_D29)
    parser.add_argument("--train-d31", type=Path, default=DEFAULT_TRAIN_D31)
    parser.add_argument("--model-artifact", type=Path, default=DEFAULT_MODEL_ARTIFACT)
    parser.add_argument("--model-audit", type=Path, default=DEFAULT_MODEL_AUDIT)
    parser.add_argument("--rebuild-model", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    return parser.parse_args()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def file_fingerprint(path: Path) -> Dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def model_signature(features: List[str], train_d29: Path, train_d31: Path) -> Dict[str, Any]:
    return {
        "approved_model_version": APPROVED_MODEL_VERSION,
        "feature_count": len(features),
        "feature_sha256": sha256_text("\n".join(features)),
        "train_d29": file_fingerprint(train_d29),
        "train_d31": file_fingerprint(train_d31),
    }


def read_model_audit(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import json

        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_or_train_model(
    features: List[str],
    train_d29: Path,
    train_d31: Path,
    model_artifact: Path,
    model_audit: Path,
    rebuild_model: bool,
) -> Tuple[Any, Dict[str, Any]]:
    signature = model_signature(features, train_d29, train_d31)
    existing_audit = read_model_audit(model_audit)
    can_load = (
        not rebuild_model
        and model_artifact.exists()
        and existing_audit.get("signature") == signature
    )
    if can_load:
        with model_artifact.open("rb") as handle:
            model = pickle.load(handle)
        return model, {
            "cache_status": "loaded_existing_artifact",
            "model_artifact": str(model_artifact),
            "model_audit": str(model_audit),
            "signature": signature,
        }

    model = train_reference_model(features, train_d29, train_d31)
    model_artifact.parent.mkdir(parents=True, exist_ok=True)
    with model_artifact.open("wb") as handle:
        pickle.dump(model, handle)
    audit = {
        "version": "SIGNAL_MODEL_V2_LIQUIDITY_MODEL_ARTIFACT_AUDIT",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cache_status": "trained_and_saved_artifact",
        "model_artifact": str(model_artifact),
        "signature": signature,
        "source": "signal_model.scripts.score_liquidity_decision_time_rows_v1.train_reference_model",
        "note": (
            "The original V1 scorer recreated the promoted model in memory for each scoring call. "
            "V2 persists the same recreated model as an inference artifact to avoid per-ticker retraining."
        ),
    }
    write_json(model_audit, audit)
    return model, {
        "cache_status": "trained_and_saved_artifact",
        "model_artifact": str(model_artifact),
        "model_audit": str(model_audit),
        "signature": signature,
    }


def score_rows(
    input_file: Path,
    feature_list_file: Path,
    train_d29: Path,
    train_d31: Path,
    model_artifact: Path,
    model_audit: Path,
    rebuild_model: bool,
    output_file: Path,
    audit_file: Path,
) -> Dict[str, Any]:
    validation = validate_rows(input_file, feature_list_file)
    if not validation["pass"]:
        audit = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "input_file": str(input_file),
            "output_file": str(output_file),
            "validation_pass": False,
            "validation": validation,
            "note": "Scoring refused because decision-time row validation failed.",
        }
        write_json(audit_file, audit)
        return audit

    features = load_features(feature_list_file)
    model, model_info = load_or_train_model(features, train_d29, train_d31, model_artifact, model_audit, rebuild_model)
    df = pd.read_csv(input_file, low_memory=False)
    X = df[features].apply(pd.to_numeric, errors="coerce")
    scored = df.copy()
    scored["approved_model_version"] = APPROVED_MODEL_VERSION
    scored["approved_model_score"] = np.clip(model.predict(X), 0, 100)
    scored["approved_model_percentile"] = scored["approved_model_score"].rank(pct=True, method="average")
    scored["approved_model_decile"] = (
        pd.qcut(scored["approved_model_score"].rank(method="first"), q=10, labels=False, duplicates="drop") + 1
    ).astype("Int64")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    scored.to_csv(output_file, index=False)

    audit = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_file": str(input_file),
        "output_file": str(output_file),
        "validation_pass": True,
        "rows": int(len(scored)),
        "feature_count": int(len(features)),
        "train_d29": str(train_d29),
        "train_d31": str(train_d31),
        "approved_model_version": APPROVED_MODEL_VERSION,
        "model_info": model_info,
        "score_min": float(scored["approved_model_score"].min()) if len(scored) else None,
        "score_max": float(scored["approved_model_score"].max()) if len(scored) else None,
        "score_mean": float(scored["approved_model_score"].mean()) if len(scored) else None,
        "validation": validation,
    }
    write_json(audit_file, audit)
    return audit


def main() -> int:
    args = parse_args()
    audit = score_rows(
        args.input_file,
        args.feature_list,
        args.train_d29,
        args.train_d31,
        args.model_artifact,
        args.model_audit,
        args.rebuild_model,
        args.output,
        args.audit,
    )
    print(f"Validation pass: {audit['validation_pass']}")
    print(f"Wrote audit: {args.audit}")
    if audit["validation_pass"]:
        print(f"Wrote scores: {args.output}")
        print(f"Model cache: {audit['model_info']['cache_status']}")
    return 0 if audit["validation_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
