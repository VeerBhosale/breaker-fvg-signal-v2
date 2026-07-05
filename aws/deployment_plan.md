# AWS Deployment Plan

## Runtime Shape

Initial deployment should be paper/manual-review only:

1. EventBridge schedule triggers an ECS Fargate task after each expected NSE 1H candle close.
2. The task fetches or reads candles, runs V2 signal detection, builds decision-time features, scores visible liquidity, classifies signals, and writes dashboard bridge outputs.
3. Artifacts, logs, audits, reports, predictions, and dashboard bridge files are written to S3.
4. CloudWatch receives stdout/stderr and structured progress logs.

Live order placement is explicitly disabled in the runtime config and task template.

## Deployment Steps

1. Build container from repository root:

   ```powershell
   docker build -f Newtest/Breaker_Based/signal_model_v2/aws/Dockerfile -t signal-model-v2:local .
   ```

2. Run local container readiness:

   ```powershell
   docker run --rm signal-model-v2:local
   ```

3. Push image to ECR after local readiness passes.
4. Create S3 bucket/prefix from `s3_layout.md`.
5. Create IAM roles from the runtime policy template.
6. Register ECS task definition from the template.
7. Create EventBridge schedule in `DISABLED` state first.
8. Run one manual ECS task in paper mode.
9. Compare generated S3 outputs against local smoke outputs.
10. Enable schedule only after paper-mode validation is clean.

## Rollback

- Keep previous ECR image tags immutable.
- Keep previous model registry/config versions in S3.
- Roll back by pointing the ECS task definition to the previous image tag and previous config/model registry.
- Do not overwrite model artifact versions in place.

## Cost Controls

- Use scheduled Fargate tasks, not an always-on service, unless the dashboard/API needs persistent serving.
- Start with `cpu=2048`, `memory=4096`; tune after observed batch duration.
- Keep CloudWatch log retention finite.
- Store raw candles and audit outputs with lifecycle rules after the research retention window.

## Not Production-Ready Until

- Fresh ingestion is proven on AWS with provider/network failure handling.
- Long and short artifact scoring are validated on broader live/replay batches, not only local smoke rows.
- Feature parity and missingness gates pass for live-mode inputs across the intended ticker universe.
- Dashboard consumption of bridge artifacts is verified.
- Paper ledger produces stable equity and risk reports.
- Real order placement remains disabled until a separate approval gate is built.
