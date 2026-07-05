# Standalone Repo Setup

This repository is the V2 production boundary. It must stay separate from the original Breaker+FVG dashboard/engine repository.

## Repository Boundary

The repo root is the V2 root:

```text
breaker-fvg-signal-v2/
```

Use these local folders for generated runtime outputs:

```text
data/
logs/
audits/
reports/
dashboard_bridge/
```

These folders are ignored by git.

## External Reference Sources

V2 may read the old Breaker+FVG code and V1 model artifacts, but it must not write to them.

Set these environment variables when running V2 from this standalone repo:

```powershell
$env:BREAKER_FVG_BREAKER_BASED_ROOT = "D:\Coding\Python Codes\Newtest\Breaker_Based"
$env:BREAKER_FVG_V1_SIGNAL_MODEL_ROOT = "D:\Coding\Python Codes\Newtest\Breaker_Based\signal_model"
$env:BREAKER_FVG_SHORT_SIGNAL_MODEL_ROOT = "D:\Coding\Python Codes\Newtest\Breaker_Based\signal_model_short"
$env:BREAKER_FVG_REFERENCE_ENGINE = "D:\Coding\Python Codes\Newtest\Breaker_Based\breaker_fvg_dashboard_export.py"
```

If these variables are not set, V2 looks for external reference sources under:

```text
external/Breaker_Based/
```

That default is intentional. It prevents the standalone repo from silently writing into or depending on the old workspace.

## Runtime Workflow

Update the local candle store:

```powershell
python scripts/v2_update_candle_store_incremental.py --provider yfinance --universe-file D:\Coding\Python Codes\Newtest\Breaker_Based\NSE_Symbols.csv --interval 1h --store-dir data/raw/v2_incremental_candle_store --run-id v2_incremental_store_original_live --initial-period 730d --min-rows 300 --overlap-bars 5 --max-store-candles 6000 --max-retries 3 --retry-sleep-seconds 5
```

Run the decision cycle:

```powershell
python scripts/v2_run_runtime_cycle.py --candles-dir data/raw/v2_incremental_candle_store --run-id v2_runtime_cycle_original_incremental_1500 --workers 4 --step-timeout-seconds 180 --ticker-timeout-seconds 600
```

The runtime uses the configured `rolling_history.runtime_lookback_candles` value from:

```text
configs/v2_runtime_config.example.json
```

Current default:

```text
1500 one-hour candles
```

## AWS Separation

Use separate V2 AWS resources:

```text
ECR: breaker-fvg-signal-v2
ECS cluster/service: breaker-fvg-v2-*
S3 bucket/prefix: breaker-fvg-v2-*
CloudWatch log group: /breaker-fvg/v2
IAM role: breaker-fvg-v2-runtime-role
EventBridge rule: breaker-fvg-v2-paper-schedule
```

Do not reuse V1 dashboard or V1 production AWS resources.

## Git Rules

Commit source/config/docs/tests/AWS templates/model manifests.

Do not commit:

```text
data/
logs/
audits/
reports/
dashboard_bridge/
.env
```

## Safety Rules

- Real order placement stays disabled by default.
- Live mode stays disabled by default.
- Manual review remains required until an explicit deployment gate is added.
- The original Breaker+FVG files are source/reference only.

