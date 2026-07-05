from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, Dict, List

from v2_common import V2_ROOT, ensure_dirs, read_json, rel, utc_stamp, write_json


AWS_ROOT = V2_ROOT / "aws"

REQUIRED_AWS_FILES = [
    "README.md",
    "Dockerfile",
    "requirements-v2.txt",
    "env.contract.example",
    "ecs-task-definition.template.json",
    "eventbridge-schedule.template.json",
    "iam-policy-runtime.template.json",
    "s3_layout.md",
    "deployment_plan.md",
]

REQUIRED_RUNTIME_KEYS = [
    "timezone",
    "interval",
    "universes",
    "modes",
    "data_ingestion",
    "v1_bridge",
    "hard_rules",
]

FORBIDDEN_SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"aws_secret_access_key", re.IGNORECASE),
    re.compile(r"aws_access_key_id", re.IGNORECASE),
    re.compile(r"BEGIN (RSA|OPENSSH|EC) PRIVATE KEY"),
]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def add_check(checks: List[Dict[str, Any]], item: str, ok: bool, path: Path | None = None, detail: Any = None) -> None:
    payload: Dict[str, Any] = {"item": item, "ok": bool(ok)}
    if path is not None:
        payload["path"] = rel(path)
    if detail is not None:
        payload["detail"] = detail
    checks.append(payload)


def has_forbidden_secret(text: str) -> List[str]:
    hits = []
    for pattern in FORBIDDEN_SECRET_PATTERNS:
        if pattern.search(text):
            hits.append(pattern.pattern)
    return hits


def contains_all(text: str, terms: List[str]) -> bool:
    lowered = text.lower()
    return all(term.lower() in lowered for term in terms)


def write_report(path: Path, audit: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = audit["summary"]
    lines = [
        "# V2 AWS Readiness Audit",
        "",
        f"Generated: `{audit['generated_at']}`",
        f"Run ID: `{audit['run_id']}`",
        "",
        "## Summary",
        "",
        f"- Check count: `{summary['check_count']}`",
        f"- Failed count: `{summary['failed_count']}`",
        f"- AWS skeleton ready: `{str(summary['aws_skeleton_ready']).lower()}`",
        f"- AWS deployable ready: `{str(summary['aws_deployable_ready']).lower()}`",
        f"- AWS deployment validated: `{str(summary['aws_deployment_validated']).lower()}`",
        f"- Real-order ready: `{str(summary['real_order_ready']).lower()}`",
        f"- Reason: {summary['reason']}",
        "",
        "## Failed Checks",
        "",
    ]
    failed = [check for check in audit["checks"] if not check["ok"]]
    if failed:
        for check in failed:
            lines.append(f"- `{check['item']}`: {check.get('detail', check.get('path', 'failed'))}")
    else:
        lines.append("- None.")
    lines.extend(["", "## Notes", ""])
    for note in audit["notes"]:
        lines.append(f"- {note}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Signal Model V2 AWS deployment skeleton readiness.")
    parser.add_argument("--run-id", default=f"v2_aws_readiness_{utc_stamp()}")
    args = parser.parse_args()

    ensure_dirs()
    checks: List[Dict[str, Any]] = []
    notes: List[str] = []

    for name in REQUIRED_AWS_FILES:
        path = AWS_ROOT / name
        add_check(checks, f"aws_file:{name}", path.exists(), path)

    runtime_path = V2_ROOT / "configs" / "v2_runtime_config.example.json"
    runtime = read_json(runtime_path) if runtime_path.exists() else {}
    add_check(checks, "runtime_config:exists", runtime_path.exists(), runtime_path)
    for key in REQUIRED_RUNTIME_KEYS:
        add_check(checks, f"runtime_config:key:{key}", key in runtime, runtime_path)

    modes = runtime.get("modes", {})
    live = modes.get("live", {})
    data_ingestion = runtime.get("data_ingestion", {})
    yfinance = data_ingestion.get("yfinance", {})
    hard_rules = runtime.get("hard_rules", {})

    add_check(checks, "safety:live_mode_disabled_by_default", live.get("enabled") is False, runtime_path, live)
    add_check(
        checks,
        "safety:order_placement_disabled_by_default",
        live.get("order_placement_enabled") is False,
        runtime_path,
        live,
    )
    add_check(
        checks,
        "safety:manual_review_required_by_default",
        live.get("manual_review_required") is True,
        runtime_path,
        live,
    )
    add_check(
        checks,
        "safety:no_original_engine_edits_rule",
        hard_rules.get("no_original_engine_edits") is True,
        runtime_path,
        hard_rules,
    )
    add_check(
        checks,
        "safety:decision_time_only_rule",
        hard_rules.get("decision_time_only_features") is True,
        runtime_path,
        hard_rules,
    )
    add_check(
        checks,
        "ingestion:fresh_provider_network_flagged",
        yfinance.get("network_required") is True,
        runtime_path,
        yfinance,
    )

    dockerfile = AWS_ROOT / "Dockerfile"
    docker_text = read_text(dockerfile)
    add_check(checks, "container:uses_python_base", "FROM python:" in docker_text, dockerfile)
    add_check(checks, "container:copies_v2_requirements", "requirements-v2.txt" in docker_text, dockerfile)
    add_check(checks, "container:default_command_is_audit", "v2_aws_readiness_audit.py" in docker_text, dockerfile)
    add_check(
        checks,
        "container:no_order_enable_env",
        "V2_ORDER_PLACEMENT_ENABLED=true" not in docker_text,
        dockerfile,
    )

    env_path = AWS_ROOT / "env.contract.example"
    env_text = read_text(env_path)
    add_check(checks, "env:order_placement_false", "V2_ORDER_PLACEMENT_ENABLED=false" in env_text, env_path)
    add_check(checks, "env:manual_review_true", "V2_MANUAL_REVIEW_REQUIRED=true" in env_text, env_path)
    add_check(checks, "env:no_plaintext_aws_secrets", not has_forbidden_secret(env_text), env_path)

    ecs_path = AWS_ROOT / "ecs-task-definition.template.json"
    ecs_text = read_text(ecs_path)
    add_check(checks, "ecs:uses_fargate", '"FARGATE"' in ecs_text, ecs_path)
    add_check(checks, "ecs:order_placement_false", '"V2_ORDER_PLACEMENT_ENABLED"' in ecs_text and '"false"' in ecs_text, ecs_path)
    add_check(checks, "ecs:uses_ssm_secret_reference", "arn:aws:ssm" in ecs_text, ecs_path)
    add_check(checks, "ecs:uses_cloudwatch_awslogs", '"logDriver": "awslogs"' in ecs_text, ecs_path)
    add_check(checks, "ecs:startup_command_is_readiness_audit", "v2_aws_readiness_audit.py" in ecs_text, ecs_path)
    add_check(checks, "ecs:no_plaintext_aws_secrets", not has_forbidden_secret(ecs_text), ecs_path)

    schedule_path = AWS_ROOT / "eventbridge-schedule.template.json"
    schedule_text = read_text(schedule_path)
    add_check(checks, "eventbridge:schedule_disabled_by_default", '"State": "DISABLED"' in schedule_text, schedule_path)
    add_check(checks, "eventbridge:cron_present", '"ScheduleExpression": "cron(' in schedule_text, schedule_path)
    add_check(checks, "eventbridge:uses_ecs_fargate_target", '"LaunchType": "FARGATE"' in schedule_text, schedule_path)
    add_check(
        checks,
        "eventbridge:order_placement_false_input",
        "order_placement_enabled" in schedule_text and "false" in schedule_text,
        schedule_path,
    )

    req_path = AWS_ROOT / "requirements-v2.txt"
    req_text = read_text(req_path)
    for package in ["pandas", "numpy", "scikit-learn", "xgboost", "joblib", "yfinance"]:
        add_check(checks, f"requirements:{package}", package in req_text, req_path)

    s3_path = AWS_ROOT / "s3_layout.md"
    s3_text = read_text(s3_path)
    for prefix in [
        "configs/",
        "models/",
        "raw_candles/",
        "signals/",
        "liquidity/",
        "features/",
        "predictions/",
        "dashboard_bridge/",
        "paper_ledger/",
        "audits/",
        "logs/",
        "reports/",
    ]:
        add_check(checks, f"s3_layout:prefix:{prefix}", prefix in s3_text, s3_path)
    add_check(
        checks,
        "s3_layout:artifact_versioning_rule",
        contains_all(s3_text, ["versioned separately", "run-specific prefixes", "No script should overwrite"]),
        s3_path,
    )

    deployment_path = AWS_ROOT / "deployment_plan.md"
    deployment_text = read_text(deployment_path)
    deployment_requirements = {
        "runtime_shape": ["EventBridge", "ECS Fargate", "S3", "CloudWatch"],
        "manual_paper_validation": ["manual ECS task", "paper mode", "Compare generated S3 outputs"],
        "rollback": ["Rollback", "previous ECR image", "previous model registry", "Do not overwrite"],
        "cost_controls": ["Cost Controls", "scheduled Fargate", "CloudWatch log retention", "lifecycle rules"],
        "real_orders_disabled": ["Live order placement", "disabled", "separate approval gate"],
    }
    for name, terms in deployment_requirements.items():
        add_check(checks, f"deployment_plan:{name}", contains_all(deployment_text, terms), deployment_path)

    iam_path = AWS_ROOT / "iam-policy-runtime.template.json"
    iam_text = read_text(iam_path)
    for action in ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "ssm:GetParameter", "logs:PutLogEvents"]:
        add_check(checks, f"iam:action:{action}", action in iam_text, iam_path)

    for path in AWS_ROOT.glob("*"):
        if path.is_file() and path.suffix in {".json", ".md", ".example", ".txt"}:
            hits = has_forbidden_secret(read_text(path))
            add_check(checks, f"secrets_scan:{path.name}", not hits, path, hits if hits else None)

    failed_count = sum(1 for check in checks if not check["ok"])
    aws_skeleton_ready = failed_count == 0
    notes.append("This audit validates local AWS packaging contracts only. It does not deploy resources.")
    notes.append("The EventBridge schedule and order placement defaults remain disabled.")
    notes.append("A passing audit means the package is AWS-deployable, not that AWS infrastructure has been created or live orders are allowed.")

    audit = {
        "version": "SIGNAL_MODEL_V2_AWS_READINESS_AUDIT",
        "run_id": args.run_id,
        "generated_at": utc_stamp(),
        "aws_root": rel(AWS_ROOT),
        "checks": checks,
        "notes": notes,
        "summary": {
            "check_count": len(checks),
            "failed_count": failed_count,
            "aws_skeleton_ready": aws_skeleton_ready,
            "aws_deployable_ready": aws_skeleton_ready,
            "aws_deployment_validated": False,
            "real_order_ready": False,
            "production_ready": aws_skeleton_ready,
            "reason": "AWS skeleton is deployable from local packaging contracts; real AWS deployment and live orders remain disabled until explicitly approved."
            if aws_skeleton_ready
            else "AWS skeleton has failed readiness checks.",
        },
    }

    audit_path = V2_ROOT / "audits" / f"{args.run_id}_audit.json"
    report_path = V2_ROOT / "reports" / f"{args.run_id}_report.md"
    write_json(audit_path, audit)
    write_report(report_path, audit)
    print(f"Wrote {audit_path}")
    print(f"Wrote {report_path}")
    print(f"aws_skeleton_ready={aws_skeleton_ready} failed_count={failed_count}")
    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
