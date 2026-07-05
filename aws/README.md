# AWS Readiness Skeleton

This folder contains a deployable-shape skeleton for Signal Model V2. It is not an AWS deployment by itself and it does not enable live trading.

## Files

- `Dockerfile` - container packaging skeleton. Build context is the repository root.
- `requirements-v2.txt` - Python runtime dependencies needed by current V2 scripts and approved artifact wrappers.
- `env.contract.example` - required environment variables and safety defaults.
- `ecs-task-definition.template.json` - ECS Fargate task template, paper/manual-review only.
- `eventbridge-schedule.template.json` - disabled EventBridge schedule template.
- `iam-policy-runtime.template.json` - least-practical runtime policy template for S3, SSM, and logs.
- `s3_layout.md` - storage contract for configs, models, candles, logs, audits, predictions, and dashboard bridge files.
- `deployment_plan.md` - build, deploy, rollback, and production-readiness plan.

## Hard Safety Defaults

- `V2_LIVE_TRADING_ENABLED=false`
- `V2_ORDER_PLACEMENT_ENABLED=false`
- `V2_MANUAL_REVIEW_REQUIRED=true`
- EventBridge schedule template state is `DISABLED`.

## Local Readiness Command

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_aws_readiness_audit.py --run-id v2_aws_readiness_current
```

## Container Build Shape

```powershell
docker build -f Newtest/Breaker_Based/signal_model_v2/aws/Dockerfile -t signal-model-v2:local .
docker run --rm signal-model-v2:local
```

The container default command runs only the AWS readiness audit. A separate runtime command should be selected for paper/live replay once those modes are approved.
