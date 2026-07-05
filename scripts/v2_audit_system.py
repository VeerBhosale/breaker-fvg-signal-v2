from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List

from v2_common import (
    BREAKER_BASED_ROOT,
    REFERENCE_ENGINE_PATH,
    REPO_ROOT,
    SHORT_ROOT,
    V1_ROOT,
    V2_ROOT,
    ensure_dirs,
    read_json,
    rel,
    utc_stamp,
    write_json,
)


REQUIRED_DIRS = [
    "configs",
    "data/raw",
    "data/features",
    "data/signals",
    "data/liquidity",
    "data/paper",
    "data/predictions",
    "models",
    "scripts",
    "logs",
    "reports",
    "audits",
    "docs",
    "tests",
    "aws",
]

REQUIRED_V2_FILES = [
    V2_ROOT / "README.md",
    V2_ROOT / "configs" / "v2_runtime_config.example.json",
    V2_ROOT / "configs" / "v2_model_registry.json",
    V2_ROOT / "configs" / "v2_feature_source_contract.json",
    V2_ROOT / "configs" / "v2_feature_availability_policy.json",
    V2_ROOT / "docs" / "V2_ARCHITECTURE.md",
    V2_ROOT / "docs" / "V2_DATA_CONTRACTS.md",
    V2_ROOT / "docs" / "V2_RUNBOOK.md",
    V2_ROOT / "scripts" / "v2_ingest_candles.py",
    V2_ROOT / "scripts" / "v2_detect_signals_from_candles.py",
    V2_ROOT / "scripts" / "v2_build_liquidity_payloads_from_events.py",
    V2_ROOT / "scripts" / "v2_build_live_features_from_events.py",
    V2_ROOT / "scripts" / "v2_apply_signal_decision_gate.py",
    V2_ROOT / "scripts" / "v2_run_signal_inference.py",
    V2_ROOT / "scripts" / "v2_run_replay_smoke_pipeline.py",
    V2_ROOT / "scripts" / "v2_import_payload_candles_batch.py",
    V2_ROOT / "scripts" / "v2_run_replay_batch.py",
    V2_ROOT / "scripts" / "v2_run_runtime_cycle.py",
    V2_ROOT / "scripts" / "v2_run_live_paper_cycle.py",
    V2_ROOT / "scripts" / "v2_export_dashboard_bridge.py",
    V2_ROOT / "scripts" / "v2_paper_replay_from_decisions.py",
    V2_ROOT / "scripts" / "v2_aws_readiness_audit.py",
    V2_ROOT / "tests" / "test_v2_static_contracts.py",
    V2_ROOT / "aws" / "Dockerfile",
    V2_ROOT / "aws" / "requirements-v2.txt",
    V2_ROOT / "aws" / "env.contract.example",
    V2_ROOT / "aws" / "ecs-task-definition.template.json",
    V2_ROOT / "aws" / "eventbridge-schedule.template.json",
    V2_ROOT / "aws" / "iam-policy-runtime.template.json",
    V2_ROOT / "aws" / "s3_layout.md",
    V2_ROOT / "aws" / "deployment_plan.md",
]

SOURCE_REFERENCE_FILES = [
    REFERENCE_ENGINE_PATH,
    BREAKER_BASED_ROOT / "breaker_fvg_scan.py",
    BREAKER_BASED_ROOT / "stage_breaker_fvg_outputs.py",
    V1_ROOT / "configs" / "trade_system_model_registry_v1.json",
    V1_ROOT / "configs" / "trade_system_signal_model_artifacts_v1.json",
    V1_ROOT / "configs" / "trade_system_feature_source_manifest_v1.json",
]


def sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ensure_dirs()
    registry_path = V2_ROOT / "configs" / "v2_model_registry.json"
    contract_path = V2_ROOT / "configs" / "v2_feature_source_contract.json"
    runtime_path = V2_ROOT / "configs" / "v2_runtime_config.example.json"
    availability_policy_path = V2_ROOT / "configs" / "v2_feature_availability_policy.json"

    checks: List[Dict[str, Any]] = []
    for rel_dir in REQUIRED_DIRS:
        path = V2_ROOT / rel_dir
        checks.append({"item": f"dir:{rel_dir}", "ok": path.is_dir(), "path": rel(path)})

    for path in [registry_path, contract_path, runtime_path, availability_policy_path]:
        checks.append({"item": f"config:{path.name}", "ok": path.exists(), "path": rel(path)})

    for path in REQUIRED_V2_FILES:
        checks.append({"item": f"v2_file:{path.name}", "ok": path.exists(), "path": rel(path)})

    registry = read_json(registry_path) if registry_path.exists() else {}
    for key in ["approved_liquidity_model", "approved_long_signal_model", "approved_short_signal_model"]:
        node = registry.get(key, {})
        for path_key in ["model_path", "preprocess_path", "feature_contract", "feature_columns", "registry_path"]:
            value = node.get(path_key)
            if value:
                p = REPO_ROOT / value
                checks.append({"item": f"{key}:{path_key}", "ok": p.exists(), "path": rel(p)})

    source_hashes = []
    for path in SOURCE_REFERENCE_FILES:
        source_hashes.append({"path": rel(path), "exists": path.exists(), "sha256": sha256(path)})

    audit = {
        "version": "SIGNAL_MODEL_V2_SYSTEM_AUDIT",
        "generated_at": utc_stamp(),
        "v2_root": rel(V2_ROOT),
        "v1_root": rel(V1_ROOT),
        "short_root": rel(SHORT_ROOT),
        "checks": checks,
        "source_reference_hashes": source_hashes,
        "summary": {
            "check_count": len(checks),
            "failed_count": sum(1 for c in checks if not c["ok"]),
            "original_engine_files_modified_by_audit": False,
            "production_ready": False,
            "reason": (
                "V2 boundary initialized; fresh ingestion, both-side detection, decision-time liquidity scoring, "
                "side-aware approved artifact scoring, dashboard bridge, paper replay, and burn-in orchestration "
                "are smoke-tested. Production readiness remains false until full-universe burn-in, broad feature "
                "substitution validation, permissioned paper-entry evidence, clean original-source baseline, and "
                "actual AWS deployment validation are proven."
            )
        },
    }
    out = V2_ROOT / "audits" / "v2_system_audit_latest.json"
    write_json(out, audit)
    print(f"Wrote {out}")
    return 0 if audit["summary"]["failed_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
