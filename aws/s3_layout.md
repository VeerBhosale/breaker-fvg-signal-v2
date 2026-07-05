# S3 Layout

Use one bucket with a strict prefix boundary for V2.

```text
s3://<bucket>/signal_model_v2/
  configs/
    v2_runtime_config.<env>.json
    v2_model_registry.<version>.json
  models/
    liquidity/<version>/
    signal_long/<version>/
    signal_short/<version>/
  raw_candles/
    interval=1h/date=YYYY-MM-DD/<ticker>.csv
  signals/
    run_id=<run_id>/
  liquidity/
    run_id=<run_id>/
  features/
    run_id=<run_id>/
  predictions/
    run_id=<run_id>/
  dashboard_bridge/
    run_id=<run_id>/
  paper_ledger/
    run_id=<run_id>/
  audits/
    run_id=<run_id>/
  logs/
    run_id=<run_id>/
  reports/
    run_id=<run_id>/
```

Rules:

- Training artifacts and inference artifacts must be versioned separately.
- Live/paper outputs must write to run-specific prefixes.
- No script should overwrite model artifacts in place.
- Real order state, if added later, must use a separate prefix and stricter IAM policy.
