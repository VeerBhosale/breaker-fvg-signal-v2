# V2 AWS Deployment Plan

## Deployment Status

Plan only. Do not deploy without explicit approval.

## Candidate Runtime

- ECS/Fargate scheduled task for hourly processing.
- S3 for raw candles, feature outputs, predictions, audits, and model artifacts.
- CloudWatch Logs for JSONL process logs.
- EventBridge schedule for 1H market polling.
- Secrets Manager or SSM Parameter Store for API keys and credentials.

## Storage Layout

- `s3://.../raw_candles/`
- `s3://.../signals/`
- `s3://.../features/`
- `s3://.../liquidity/`
- `s3://.../predictions/`
- `s3://.../audits/`
- `s3://.../models/`

## Required Gates Before Deployment

- one-ticker smoke pass
- five-ticker smoke pass
- replay pass
- feature parity report
- leakage audit
- dashboard output audit
- paper ledger audit
- cost estimate
- rollback plan

## Rollback

Model artifacts and configs must be versioned. Deployment must support reverting to the previous registry/config without modifying source code.

