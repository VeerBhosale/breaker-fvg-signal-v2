# V2 Runbook

## 1. Audit The V2 Boundary

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_audit_system.py
```

Expected:

- Exit code `0`
- Output: `Newtest/Breaker_Based/signal_model_v2/audits/v2_system_audit_latest.json`

## 2. Run Static Tests

```powershell
python Newtest/Breaker_Based/signal_model_v2/tests/test_v2_static_contracts.py
```

Expected:

- Static contract tests pass
- `OK`

## 3. Run Bridge Smoke

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_bridge_smoke.py --events Newtest/Breaker_Based/signal_model/datasets/live_inbox/trade_system_minimal_events_smoke27.csv --limit 3
```

Expected outputs:

- `data/signals/<run_id>_events.csv`
- `data/features/<run_id>_feature_complete.csv`
- `data/predictions/<run_id>_01_scored.csv`
- `data/predictions/<run_id>_03_decisions.csv`
- `logs/<run_id>.jsonl`
- `reports/<run_id>_report.md`
- `audits/<run_id>_audit.json`

## 4. Candle Audit

For any local candle CSV:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_candle_audit.py --input path\to\candles.csv --output Newtest/Breaker_Based/signal_model_v2/audits/my_candle_audit.json
```

This only audits file shape. It does not fetch data or detect signals yet.

## 5. Ingest Candle Data

Local CSV ingestion smoke:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_ingest_candles.py --provider local_csv --ticker 360ONE.NS --source-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_import_20260704T203053Z --run-id v2_candle_ingest_360one_local_latest --min-rows 50
```

Expected outputs:

- `data/raw/<run_id>/<ticker>_1h.csv`
- `logs/<run_id>.jsonl`
- `audits/<run_id>_audit.json`

Latest verified clean result:

- Audit: `audits/v2_candle_ingest_360one_local_latest_audit.json`
- Ticker count: `1`
- Passed count: `1`
- Rows: `250`
- Duplicate time rows: `0`
- Null OHLC values: `0`
- Large gap count: `36`

Fresh yfinance ingestion smoke:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_ingest_candles.py --provider yfinance --ticker 360ONE.NS --period 60d --run-id v2_yfinance_ingest_360one_60d_fixed_smoke_v2 --allow-partial
```

Latest verified result:

- Audit: `audits/v2_yfinance_ingest_360one_60d_fixed_smoke_v2_audit.json`
- Input rows: `395`
- Output rows: `395`
- Null/unparseable rows dropped: `0`
- Duplicate timestamps: `0`
- Status: `passed`

Full-original yfinance ingestion burn-in:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_ingest_candles.py --provider yfinance --universe-file Newtest/Breaker_Based/NSE_Symbols.csv --interval 1h --period 730d --run-id v2_yfinance_ingest_original_179_1h_730d_full_burnin --min-rows 300 --max-retries 3 --retry-sleep-seconds 5
```

Latest verified result:

- Audit: `audits/v2_yfinance_ingest_original_179_1h_730d_full_burnin_audit.json`
- Deduped tickers: `178`
- Passed count: `178`
- Failed count: `0`
- Output dir: `data/raw/v2_yfinance_ingest_original_179_1h_730d_full_burnin`
- Log: `logs/v2_yfinance_ingest_original_179_1h_730d_full_burnin.jsonl`

## 5A. Incremental Candle Store

For production-style runtime, do not refetch the full history every cycle. Maintain a local normalized store and update it incrementally.

Default contract:

- Store path: `data/raw/v2_incremental_candle_store`
- Store cap: `6000` one-hour candles per ticker
- Provider overlap: `5` bars
- Runtime signal/liquidity lookback: `1500` one-hour candles
- Runtime wrapper source: `configs/v2_runtime_config.example.json -> rolling_history.runtime_lookback_candles`

Full original-universe incremental update:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_update_candle_store_incremental.py --provider yfinance --universe-file Newtest/Breaker_Based/NSE_Symbols.csv --interval 1h --store-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_incremental_candle_store --run-id v2_incremental_store_original_live --initial-period 730d --min-rows 300 --overlap-bars 5 --max-store-candles 6000 --max-retries 3 --retry-sleep-seconds 5
```

How it works:

1. If a ticker has no stored candle file, it performs the initial `730d` fetch.
2. If a ticker already has a stored file, it fetches from `last_stored_time - overlap_bars`.
3. It normalizes provider rows to `time,open,high,low,close`.
4. It appends/merges with the stored file.
5. It dedupes by `time`, keeping the fetched row on overlap.
6. It trims to `max_store_candles`.
7. It writes per-ticker progress and ETA into `logs/<run_id>.jsonl`.

Latest local smoke:

- Audit: `audits/v2_incremental_store_local_smoke_audit.json`
- Provider: `local_csv`
- Tickers: `1`
- Passed: `1`
- Failed: `0`
- New candles retained: `250`
- Output store: `data/raw/v2_incremental_candle_store`

Runtime cycle using the configured longer lookback:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_runtime_cycle.py --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_incremental_candle_store --run-id v2_runtime_cycle_original_incremental_1500 --workers 4 --step-timeout-seconds 180 --ticker-timeout-seconds 600
```

Manual override when testing:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_runtime_cycle.py --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_incremental_candle_store --run-id v2_runtime_cycle_original_incremental_2000 --max-input-candles 2000 --workers 4
```

## 6. Import Existing Payload Candles For Replay Smoke

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_import_payload_candles.py --payload Newtest/Breaker_Based/signal_model/datasets/raw/signal_window_liquidity_1h_2y_payloads/360ONE.NS/360ONE.NS_0b2296ba5f9b_1747637100.json
```

This creates V2-owned raw candle CSVs under `data/raw/<run_id>/` and writes an audit under `audits/`.

## 7. Feature Parity Audit

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_feature_parity_audit.py --feature-complete Newtest/Breaker_Based/signal_model_v2/data/features/v2_bridge_smoke_20260704T202813Z_feature_complete.csv
```

This compares a feature-complete CSV against approved long/short preprocess feature lists.

## 8. Detect Long/Short Signals From V2 Candle CSV

Dashboard-compatible signal timestamp policy:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_detect_signals_from_candles.py --candles Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_import_20260704T203053Z/360ONE.NS_1h_1747637100.csv --ticker 360ONE.NS --run-id v2_signal_detect_360one_smoke
```

Stricter next-candle decision-time policy:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_detect_signals_from_candles.py --candles Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_import_20260704T203053Z/360ONE.NS_1h_1747637100.csv --ticker 360ONE.NS --run-id v2_signal_detect_360one_next_candle_smoke --decision-time-policy next_candle_after_signal
```

Both-side detector smoke from fresh yfinance candles:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_detect_signals_from_candles.py --candles Newtest/Breaker_Based/signal_model_v2/data/raw/v2_live_paper_cycle_360one_yfinance_60d_fixed_smoke_ingest/360ONE.NS_1h.csv --ticker 360ONE.NS --side both --run-id v2_signal_detect_360one_both_side_yfinance_60d_smoke
```

Expected outputs:

- `data/signals/<run_id>_events.csv`
- `logs/<run_id>.jsonl`
- `audits/<run_id>_audit.json`

Latest verified result:

- Input candles: `250`
- Long signals: `2`
- Both timing policies passed audit.
- Original engine/dashboard source was not edited.

Latest verified both-side result:

- Audit: `audits/v2_signal_detect_360one_both_side_yfinance_60d_smoke_audit.json`
- Input candles: `395`
- Signal rows: `2`
- Side counts:
  - `long`: `1`
  - `short`: `1`
- Short detection method: `price_reflection_wrapper_p_negative`
- Risk invalid rows: `0`
- Original engine/dashboard source was not edited.

Short-side note:

- The short detector is a V2-owned mirror wrapper around the unchanged long detector.
- This proves short event creation and the current smoke also proves short artifact scoring on a feature-complete row.
- Short production readiness still requires broader replay/paper validation; the one-ticker smoke is not enough for live trading approval.

## 8A. Run Production Readiness Audit

To run the full local burn-in wrapper over an existing V2 candle directory:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_burnin_cycle.py --run-id v2_burnin_5ticker_smoke_current --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_batch_import_5ticker_smoke --limit 5 --notional-capital-inr 1000000
```

To resume the same burn-in without rerunning already audited tickers:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_burnin_cycle.py --run-id v2_burnin_5ticker_smoke_current --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_batch_import_5ticker_smoke --limit 5 --notional-capital-inr 1000000 --resume-replay
```

Current expected result on the 5-ticker candle folder:

- Burn-in status: `passed_smoke`
- Validation passed: `false`
- Failed phases: `feature_validation`, `validation_gate`
- Resume smoke: replay resumed `5/5` ticker audits
- Report: `reports/v2_burnin_5ticker_smoke_current_report.md`
- Log: `logs/v2_burnin_5ticker_smoke_current.jsonl`

## 8B. Full-Original Fresh Replay Validation

This validation artifact used the latest `300` candles per ticker, decision-time signal detection, decision-time visible liquidity extraction, approved XG liquidity scoring, approved signal artifacts, dashboard bridge export, paper replay, and a formal validation gate. It remains valid pipeline evidence, but the intended production runtime now uses the incremental store and configured `1500` candle lookback from section 5A.

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_replay_batch.py --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_yfinance_ingest_original_179_1h_730d_full_burnin --run-id v2_replay_original_fresh_178_tail300_parallel_burnin --continue-on-error --resume --max-input-candles 300 --step-timeout-seconds 120 --ticker-timeout-seconds 240 --workers 4
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_export_dashboard_bridge.py --decisions "Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_replay_original_fresh_178_tail300_parallel_burnin*_decisions.csv" --liquidity-dirs "Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_replay_original_fresh_178_tail300_parallel_burnin*" --run-id v2_dashboard_bridge_original_fresh_178_tail300_parallel_burnin --latest-per-ticker 3 --max-live-rows 300
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_feature_broad_validation_audit.py --run-id v2_feature_validation_original_fresh_178_tail300_parallel_burnin --replay-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_replay_original_fresh_178_tail300_parallel_burnin_audit.json --min-feature-audits 100 --min-feature-rows 150 --min-classification-allowed-rate 0.95 --max-blocking-missing-tickers 0 --max-missing-all-rows-pct 0.25
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_paper_replay_from_decisions.py --decisions "Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_replay_original_fresh_178_tail300_parallel_burnin*_decisions.csv" --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_yfinance_ingest_original_179_1h_730d_full_burnin --run-id v2_paper_replay_original_fresh_178_tail300_parallel_burnin --notional-capital-inr 1000000 --entry-permission-values yes conditional_take_candidate
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_validation_gate_audit.py --run-id v2_validation_gate_original_fresh_178_tail300_parallel_burnin --replay-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_replay_original_fresh_178_tail300_parallel_burnin_audit.json --dashboard-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_dashboard_bridge_original_fresh_178_tail300_parallel_burnin_audit.json --paper-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_paper_replay_original_fresh_178_tail300_parallel_burnin_audit.json --min-attempted-tickers 178 --min-passed-tickers 100 --min-signal-rows 155 --min-decision-rows 155 --min-scored-liquidity-rows 824 --min-approved-artifact-decision-rows 155 --min-entered-trades 0 --max-failed-tickers 0
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_production_readiness_audit.py --run-id v2_production_readiness_current
```

Latest verified full-original fresh result:

- Replay: `178` attempted, `100` passed, `78` no-signal, `0` failed.
- Decisions: `155` rows, all through approved artifact scoring.
- Liquidity: `824` visible decision-time candidates scored by the XG liquidity model.
- Buckets: `reject: 155`.
- Feature validation: `100` feature audits, `155` rows, `1.0` classification-allowed rate, `0` blocking-missing tickers.
- Dashboard bridge: `155/155` contract rows passed, `155` rows with scored liquidity context.
- Paper replay: `155` decision rows read, `0` entered trades because all rows were `reject`, `10L` notional, real order placement disabled.
- Validation gate: `12/12` checks passed.
- Production readiness: `53/53` required checks passed. Local package is production-ready and AWS-deployable; real AWS deployment and real-order readiness remain disabled/unvalidated.

## 8C. Corrected 25-Ticker Bounded Replay Validation

This is the current best bounded production-style validation over original-universe cached candles. It uses the cached approved liquidity scorer, bounded latest-window replay, corrected structural-null feature policy, and parallel ticker execution.

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_replay_batch.py --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_ingest_original_178_cached_1h_2y_eta --run-id v2_replay_original_tail300_25ticker_parallel_policy_v2_validation --limit 25 --continue-on-error --max-input-candles 300 --step-timeout-seconds 120 --ticker-timeout-seconds 240 --workers 4
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_export_dashboard_bridge.py --decisions "Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_replay_original_tail300_25ticker_parallel_policy_v2_validation*_decisions.csv" --liquidity-dirs "Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_replay_original_tail300_25ticker_parallel_policy_v2_validation*" --run-id v2_dashboard_bridge_original_tail300_25ticker_parallel_policy_v2_validation --latest-per-ticker 3 --max-live-rows 100
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_feature_broad_validation_audit.py --run-id v2_feature_validation_original_tail300_25ticker_parallel_policy_v2_validation --replay-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_replay_original_tail300_25ticker_parallel_policy_v2_validation_audit.json --min-feature-audits 15 --min-feature-rows 25 --min-classification-allowed-rate 0.95 --max-blocking-missing-tickers 0 --max-missing-all-rows-pct 0.25
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_validation_gate_audit.py --run-id v2_validation_gate_original_tail300_25ticker_parallel_policy_v2_validation --replay-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_replay_original_tail300_25ticker_parallel_policy_v2_validation_audit.json --dashboard-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_dashboard_bridge_original_tail300_25ticker_parallel_policy_v2_validation_audit.json --min-attempted-tickers 25 --min-passed-tickers 15 --min-signal-rows 25 --min-decision-rows 25 --min-scored-liquidity-rows 150 --min-approved-artifact-decision-rows 25 --max-failed-tickers 0
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_production_readiness_audit.py --run-id v2_production_readiness_current
```

Latest verified result:

- Replay: `25` attempted, `15` passed, `10` no-signal, `0` failed.
- Decisions: `27` rows, all through approved artifact scoring.
- Liquidity: `168` visible decision-time candidates scored by XG liquidity model.
- Buckets: `high_conviction: 1`, `reject: 26`.
- Feature validation: `15` feature audits, `27` rows, `1.0` classification-allowed rate, `0` blocking-missing tickers.
- Dashboard bridge: `27/27` contract rows passed, `27` rows with scored liquidity context.
- Paper replay: `27` decision rows read, `1` conditional high-conviction entry simulated, `10L` notional, real order placement disabled.
- Fresh full-original yfinance ingestion: `178/178` deduped original-universe tickers passed, `0` failed.
- Full-original fresh replay/dashboard/paper validation is covered in section 8B.
- Production readiness: `53/53` required checks passed. Local package is production-ready and AWS-deployable; real AWS deployment and real-order readiness remain disabled/unvalidated.

Feature broad-validation output from the burn-in:

- Audit: `audits/v2_burnin_5ticker_smoke_current_feature_validation_audit.json`
- Report: `reports/v2_burnin_5ticker_smoke_current_feature_validation_report.md`
- Checks passed: `1/5`
- Feature audits: `2`
- Feature rows: `4`
- Classification allowed rate: `0.5`
- Blocking missing tickers: `1`

First run or refresh the validation gate over the latest replay/dashboard/paper artifacts:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_validation_gate_audit.py --run-id v2_validation_gate_5ticker_smoke_current --replay-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_replay_batch_5ticker_smoke_current_audit.json --dashboard-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_dashboard_bridge_5ticker_smoke_current_audit.json --paper-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_paper_replay_5ticker_smoke_current_audit.json
```

Current expected result on the 5-ticker smoke:

- Validation passed: `false`
- Checks passed: `5/12`
- Main failures: insufficient ticker scope, row scope, approved artifact scoring scope, and zero paper entered trades

Then run the production-readiness audit:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_production_readiness_audit.py --run-id v2_production_readiness_current
```

Expected current result:

- Required checks: `53/53` passed
- Local production package ready: `true`
- AWS deployable ready: `true`
- AWS deployment validated: `false`
- Real-money ready: `false`
- Remaining real-money/deployment caveats:
  - AWS real deployment not performed
  - original engine/dashboard worktree is dirty

This audit is the current gate for deciding whether V2 is locally coherent versus actually production-ready.

Both-side feature and side-gate smoke:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_build_live_features_from_events.py --events Newtest/Breaker_Based/signal_model_v2/data/signals/v2_signal_detect_360one_both_side_yfinance_60d_smoke_events.csv --candles Newtest/Breaker_Based/signal_model_v2/data/raw/v2_live_paper_cycle_360one_yfinance_60d_fixed_smoke_ingest/360ONE.NS_1h.csv --liquidity-aggregation Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_both_side_yfinance_60d_smoke/signal_liquidity_aggregation.csv --liquidity-scored-candidates Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_both_side_yfinance_60d_smoke/candidates_scored.csv --liquidity-payload-dir Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_both_side_yfinance_60d_smoke/payloads --macro-candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_live_paper_cycle_360one_yfinance_60d_fixed_smoke_ingest --run-id v2_features_360one_both_side_yfinance_60d_smoke
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_signal_inference.py --features Newtest/Breaker_Based/signal_model_v2/data/features/v2_features_360one_both_side_yfinance_60d_smoke_features.csv --feature-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_features_360one_both_side_yfinance_60d_smoke_audit.json --run-id v2_inference_360one_both_side_yfinance_60d_side_gate_smoke
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_export_dashboard_bridge.py --run-id v2_dashboard_bridge_360one_both_side_scored_smoke --decisions Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_inference_360one_both_side_yfinance_60d_side_gate_smoke_decisions.csv --liquidity-dirs Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_both_side_yfinance_60d_smoke
```

Latest verified result:

- Feature rows: `2`
- Side counts: `long: 1`, `short: 1`
- Inference path: `approved_artifact_scoring`
- Bucket counts: `reject: 1`, `neutral_no_edge: 1`
- Rows scored: `2`
- Rows blocked before scoring: `0`
- Long decision class: `reject`
- Short decision class: `neutral_no_edge`
- Artifact scoring: `long_scored_rows: 1`, `short_scored_rows: 1`
- Feature audit side status:
  - Long: `0` blocking required fields, `approved_inference_contract=true`
  - Short: `1194` required short-artifact features, `0` blocking required fields, `feature_complete=true`, `approved_inference_contract=true`

Interpretation:

- Mixed long/short V2 rows can now pass through feature generation without crashing.
- The long row can now be scored by the approved long artifact when only approved structural nulls remain.
- The short row can now be scored by the approved short artifact when only approved structural nulls remain.
- Rows still become `insufficient_data` when their side-specific feature contract is incomplete or the artifact/config evidence is missing.
- If a detector event lacks explicit same-direction FVG bounds, the feature builder uses the nearest active same-direction FVG from the decision-time payload and recomputes FVG reaction fields.
- Short phase, candle-quality, FVG, and liquidity topology metrics are side-normalized into the short artifact contract for audit safety; scoring is only allowed when the audit proves both feature completeness and artifact/config availability.

Dashboard/API bridge result:

- Run ID: `v2_dashboard_bridge_360one_both_side_scored_smoke`
- Bridge rows: `2`
- Live rows: `2`
- Signals with scored liquidity context: `2`
- Buckets: `reject: 1`, `neutral_no_edge: 1`
- Dashboard contract: `passed`, `2/2` rows complete
- Output folder: `Newtest/Breaker_Based/signal_model_v2/dashboard_bridge/v2_dashboard_bridge_360one_both_side_scored_smoke`
- Original dashboard modified: `false`

## 9. Build Native Live Feature Rows From Detected Events

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_build_live_features_from_events.py --events Newtest/Breaker_Based/signal_model_v2/data/signals/v2_signal_detect_360one_smoke_events.csv --candles Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_import_20260704T203053Z/360ONE.NS_1h_1747637100.csv --run-id v2_live_features_360one_smoke
```

Expected outputs:

- `data/features/<run_id>_features.csv`
- `reports/<run_id>_feature_parity_summary.csv`
- `audits/<run_id>_audit.json`
- `logs/<run_id>.jsonl`

Latest verified result:

- Rows: `2`
- Approved long model required features: `679`
- Available on all rows: `208`
- Missing on all rows: `439`
- Classification status: `insufficient_data`

This is intentional. V2 must not score a model row until missing topology/liquidity/composite features are generated or explicitly removed from the production feature contract.

## 10. Build And Score Decision-Time Liquidity

Build V2 decision-time liquidity payloads:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_build_liquidity_payloads_from_events.py --events Newtest/Breaker_Based/signal_model_v2/data/signals/v2_signal_detect_360one_smoke_events.csv --candles Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_import_20260704T203053Z/360ONE.NS_1h_1747637100.csv --run-id v2_liquidity_payloads_360one_smoke
```

Build validated liquidity candidate rows:

```powershell
python Newtest/Breaker_Based/signal_model/scripts/build_trade_system_decision_time_liquidity_candidates_v1.py --events Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_smoke/events_normalized.csv --manifest Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_smoke/payload_manifest.txt --raw-output Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_smoke/candidates_raw.csv --feature-output Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_smoke/candidates_features.csv --audit Newtest/Breaker_Based/signal_model_v2/audits/v2_liquidity_candidates_360one_smoke_audit.json
```

Score candidate rows with the approved liquidity model:

```powershell
python Newtest/Breaker_Based/signal_model/scripts/score_liquidity_decision_time_rows_v1.py Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_smoke/candidates_features.csv --output Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_smoke/candidates_scored.csv --audit Newtest/Breaker_Based/signal_model_v2/audits/v2_liquidity_scores_360one_smoke_audit.json
```

Aggregate per-level scores to signal-level features:

```powershell
python Newtest/Breaker_Based/signal_model/scripts/aggregate_trade_system_liquidity_scores_v1.py --events Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_smoke/events_normalized.csv --scored-candidates Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_smoke/candidates_scored.csv --output Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_smoke/signal_liquidity_aggregation.csv --audit Newtest/Breaker_Based/signal_model_v2/audits/v2_liquidity_aggregation_360one_smoke_audit.json
```

Rebuild feature rows with liquidity aggregation, scored candidate topology, payload context, and macro context merged:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_build_live_features_from_events.py --events Newtest/Breaker_Based/signal_model_v2/data/signals/v2_replay_smoke_360one_policy_gate_final_events.csv --candles Newtest/Breaker_Based/signal_model_v2/data/raw/v2_candle_ingest_360one_local_latest/360ONE.NS_1h.csv --liquidity-aggregation Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_replay_smoke_360one_policy_gate_final/signal_liquidity_aggregation.csv --liquidity-scored-candidates Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_replay_smoke_360one_policy_gate_final/candidates_scored.csv --liquidity-payload-dir Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_replay_smoke_360one_policy_gate_final/payloads --macro-candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_candle_ingest_360one_local_latest --run-id v2_live_features_360one_policy_gate_smoke
```

Latest verified result:

- Candidate rows: `7`
- Candidate validation: `passed`
- Liquidity model feature count: `391`
- XG score range: `45.60` to `70.83`
- Aggregated signal rows: `2`
- Signals missing candidates: `0`
- Feature coverage after scored-candidate topology + payload + macro merge: `501 / 679` approved long model features available on all rows.
- Available on partial rows: `102`.
- Missing required features on all rows: `76`.
- Structural-null missing features on all rows: `76`.
- Blocking missing features on all rows: `0`.
- Classification status: `classification_allowed`.
- Macro coverage: `44 / 44`.
- Technical coverage: `148 / 153`.
- Topology coverage: `75 / 212` all-row and `62 / 212` partial-row.

Latest verified both-side liquidity result:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_build_liquidity_payloads_from_events.py --events Newtest/Breaker_Based/signal_model_v2/data/signals/v2_signal_detect_360one_both_side_yfinance_60d_smoke_events.csv --candles Newtest/Breaker_Based/signal_model_v2/data/raw/v2_live_paper_cycle_360one_yfinance_60d_fixed_smoke_ingest/360ONE.NS_1h.csv --run-id v2_liquidity_payloads_360one_both_side_yfinance_60d_smoke
```

Then candidate build, scoring, and aggregation were run on the generated `events_normalized.csv` and `payload_manifest.txt`.

Verified outputs:

- Payload audit: `audits/v2_liquidity_payloads_360one_both_side_yfinance_60d_smoke_audit.json`
- Candidate audit: `audits/v2_liquidity_candidates_360one_both_side_yfinance_60d_smoke_audit.json`
- Score audit: `audits/v2_liquidity_scores_360one_both_side_yfinance_60d_smoke_audit.json`
- Aggregation audit: `audits/v2_liquidity_aggregation_360one_both_side_yfinance_60d_smoke_audit.json`
- Payloads written: `2`
- Candidate rows: `4`
- Candidate role counts: `target_side:2`, `stop_or_swept_side:2`
- Candidate side counts: `BSL:2`, `SSL:2`
- Required liquidity features: `391`
- Missing liquidity feature count: `0`
- Cutoff violations: `0`
- Scored candidate rows: `4`
- XG score range: `49.58` to `62.37`
- Aggregated signal rows: `2`
- Aggregation decision-side counts: `long:1`, `short:1`

## 11. Apply Decision Gate

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_apply_signal_decision_gate.py --features Newtest/Breaker_Based/signal_model_v2/data/features/v2_live_features_360one_with_liquidity_smoke_features.csv --feature-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_live_features_360one_with_liquidity_smoke_audit.json --run-id v2_decision_gate_360one_with_liquidity_smoke
```

Expected outputs:

- `data/predictions/<run_id>_decisions.csv`
- `audits/<run_id>_audit.json`
- `logs/<run_id>.jsonl`

Latest verified result:

- Input rows: `2`
- Output rows: `2`
- Decision bucket: `insufficient_data`
- Entry permission: `no`
- Order placement: disabled
- Reason: native V2 rows currently have `378` approved long-model features missing on all rows.

This is the correct production behavior for incomplete rows. The V2 gate must not force-score the approved signal model until the required feature contract is satisfied or formally replaced.

## 12. Run Signal Inference

Feature-complete approved artifact scoring smoke:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_signal_inference.py --features Newtest/Breaker_Based/signal_model_v2/data/features/v2_bridge_smoke_20260704T202813Z_feature_complete.csv --run-id v2_signal_inference_feature_complete_smoke
```

Latest verified result:

- Report: `reports/v2_signal_inference_feature_complete_smoke_report.md`
- Audit: `audits/v2_signal_inference_feature_complete_smoke_audit.json`
- Input rows: `3`
- Inference path: `approved_artifact_scoring`
- Long scored rows: `3`
- Bucket counts:
  - `ultra_high_conviction`: `1`
  - `high_conviction`: `1`
  - `neutral_no_edge`: `1`

Incomplete native-row smoke:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_signal_inference.py --features Newtest/Breaker_Based/signal_model_v2/data/features/v2_replay_smoke_360one_inference_latest_features.csv --feature-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_replay_smoke_360one_inference_latest_features_audit.json --run-id v2_signal_inference_incomplete_native_smoke
```

Latest verified result:

- Report: `reports/v2_signal_inference_incomplete_native_smoke_report.md`
- Audit: `audits/v2_signal_inference_incomplete_native_smoke_audit.json`
- Input rows: `2`
- Inference path: `insufficient_data_gate`
- Missing required features on all rows: `378`
- Bucket counts:
  - `insufficient_data`: `2`

## 13. Run The Current Replay Smoke Pipeline

This runs the current V2 replay chain in one auditable command:

1. V2 candle CSV to signal events.
2. Signal events to decision-time liquidity payloads.
3. Visible liquidity candidates to approved XG liquidity scores.
4. Liquidity scores to signal-level liquidity features.
5. Signal events plus candles plus liquidity aggregation to native feature rows.
6. Native feature rows to decision rows through the V2 gate.

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_replay_smoke_pipeline.py --candles Newtest/Breaker_Based/signal_model_v2/data/raw/v2_candle_ingest_360one_local_latest/360ONE.NS_1h.csv --ticker 360ONE.NS --run-id v2_replay_smoke_360one_policy_gate_final --macro-candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_candle_ingest_360one_local_latest
```

Expected outputs:

- `data/signals/<run_id>_events.csv`
- `data/liquidity/<run_id>/`
- `data/features/<run_id>_features.csv`
- `data/predictions/<run_id>_decisions.csv`
- `logs/<run_id>.jsonl`
- `audits/<run_id>_audit.json`
- `reports/<run_id>_report.md`

Expected current behavior:

- The replay chain should run end to end.
- The feature audit should allow classification only when `blocking_missing_all_rows_count = 0`.
- In the latest smoke, the final decision bucket is `reject` because the approved long artifact scored both rows but neither passed the main entry gate.

Latest verified result:

- Report: `reports/v2_replay_smoke_360one_policy_gate_final_report.md`
- Audit: `audits/v2_replay_smoke_360one_policy_gate_final_audit.json`
- Signal rows: `2`
- Liquidity candidate rows: `7`
- Scored liquidity rows: `7`
- Aggregated signal rows: `2`
- Feature rows: `2`
- Decision rows: `2`
- Feature coverage: `501 / 679`
- Missing required features on all rows: `76`
- Structural-null missing features on all rows: `76`
- Blocking missing features on all rows: `0`
- Topology coverage: `75 / 212` all-row and `62 / 212` partial-row
- Signal inference path: `approved_artifact_scoring`
- Decision bucket:
  - `reject`: `2`

Previous baseline result from clean ingested candle output before payload + macro context:

- Command input: `data/raw/v2_candle_ingest_360one_local_latest/360ONE.NS_1h.csv`
- Report: `reports/v2_replay_smoke_360one_inference_latest_report.md`
- Audit: `audits/v2_replay_smoke_360one_inference_latest_audit.json`
- Signal rows: `2`
- Liquidity candidate rows: `7`
- Scored liquidity rows: `7`
- Aggregated signal rows: `2`
- Feature rows: `2`
- Decision rows: `2`
- Feature coverage: `248 / 679`
- Missing required features on all rows: `378`
- Decision bucket:
  - `insufficient_data`: `2`
- Signal inference path: `insufficient_data_gate`
- Observed bottleneck: liquidity candidate scoring took `66.382` seconds because the current scorer rebuilds its reference model inside the process.

## 14. Import A Multi-Ticker Payload Candle Batch

This creates one V2-owned raw candle directory from existing payload windows. It is useful for replay smoke testing without touching V1 files.

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_import_payload_candles_batch.py --limit 5 --run-id v2_payload_batch_import_5ticker_smoke --min-rows 50
```

Expected outputs:

- `data/raw/<run_id>/<ticker>_1h.csv`
- `logs/<run_id>.jsonl`
- `audits/<run_id>_audit.json`

Latest verified result:

- Audit: `audits/v2_payload_batch_import_5ticker_smoke_audit.json`
- Output dir: `data/raw/v2_payload_batch_import_5ticker_smoke`
- Imported tickers: `5`
- Passed tickers: `5`
- Failed tickers: `0`
- Rows per ticker: `250`
- Duplicate timestamps: `0`
- Null OHLC values: `0`

## 15. Run A Multi-Ticker Replay Batch

This runs the replay smoke pipeline ticker-by-ticker and writes strict progress to an aggregate JSONL log.

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_replay_batch.py --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_batch_import_5ticker_smoke --run-id v2_replay_batch_5ticker_smoke_current --continue-on-error --limit 5
```

Expected outputs:

- `logs/<run_id>.jsonl`
- `audits/<run_id>_audit.json`
- `reports/<run_id>_report.md`
- Per-ticker replay reports and audits under `reports/` and `audits/`

Latest verified result:

- Report: `reports/v2_replay_batch_5ticker_smoke_current_report.md`
- Audit: `audits/v2_replay_batch_5ticker_smoke_current_audit.json`
- Selected candle files: `5`
- Attempted tickers: `5`
- Passed tickers: `2`
- No-signal tickers: `3`
- Failed tickers: `0`
- Signal rows: `4`
- Scored liquidity rows: `11`
- Feature rows: `4`
- Decision rows: `4`
- Decision buckets:
  - `reject`: `2`
  - `insufficient_data`: `2`

Per-ticker result:

| Ticker | Status | Signals | Decisions | Inference path |
|---|---:|---:|---:|---|
| `360ONE.NS` | `passed` | `2` | `2` | `approved_artifact_scoring` |
| `3MINDIA.NS` | `no_signals` | `0` | `0` | n/a |
| `AADHARHFC.NS` | `no_signals` | `0` | `0` | n/a |
| `AARTIIND.NS` | `passed` | `2` | `2` | `insufficient_data_gate` |
| `AAVAS.NS` | `no_signals` | `0` | `0` | n/a |

Notes:

- `no_signals` is not a hard failure. It means the V2 detector found no qualifying long setup in that candle window.
- Partial liquidity payload generation is allowed only when at least one event in the ticker has a valid decision-time payload. Skipped events remain visible in the payload audit.

Bounded original-universe replay using cached liquidity scoring:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_replay_batch.py --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_ingest_original_178_cached_1h_2y_eta --run-id v2_replay_original_tail300_10ticker_cached_scorer_normalized --limit 10 --continue-on-error --max-input-candles 300 --step-timeout-seconds 120 --ticker-timeout-seconds 240
```

Latest verified result:

- Status: `passed`
- Attempted tickers: `10`
- Passed tickers: `6`
- No-signal tickers: `4`
- Failed tickers: `0`
- Signal rows: `12`
- Liquidity candidate rows: `96`
- Scored liquidity rows: `96`
- Decision rows: `12`
- Decision buckets:
  - `reject`: `11`
  - `insufficient_data`: `1`
- Report: `reports/v2_replay_original_tail300_10ticker_cached_scorer_normalized_report.md`
- Audit: `audits/v2_replay_original_tail300_10ticker_cached_scorer_normalized_audit.json`

Notes:

- `--max-input-candles 300` makes this a latest-window runtime smoke, not a full historical backtest.
- `--step-timeout-seconds` and `--ticker-timeout-seconds` prevent long silent runs.
- Liquidity scoring uses `scripts/v2_score_liquidity_candidates.py`, which loads the cached approved liquidity XG artifact from V2 `models/` when the training/feature signature matches.
- Decision rows include normalized V2 aliases (`bucket`, `permission`, `model_score`, `reason_codes`) and preserve V1 `tds_*` provenance columns.

Parallel replay smoke:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_replay_batch.py --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_ingest_original_178_cached_1h_2y_eta --run-id v2_replay_original_tail300_4ticker_parallel_smoke --limit 4 --continue-on-error --max-input-candles 300 --step-timeout-seconds 120 --ticker-timeout-seconds 240 --workers 2
```

Latest verified result:

- Workers: `2`
- Attempted tickers: `4`
- Passed tickers: `2`
- No-signal tickers: `2`
- Failed tickers: `0`
- Signal rows: `4`
- Scored liquidity rows: `34`
- Decision rows: `4`
- Decision buckets:
  - `reject`: `4`
- Report: `reports/v2_replay_original_tail300_4ticker_parallel_smoke_report.md`
- Audit: `audits/v2_replay_original_tail300_4ticker_parallel_smoke_audit.json`

Parallel mode rule:

- `--workers > 1` requires `--continue-on-error`.
- Each ticker writes its own replay audit/report and launcher JSONL log.
- The aggregate log records ticker submission, completion, ETA, and per-ticker status.

## 16. Export Dashboard/API Bridge Artifacts

This writes additive dashboard/API files from V2 decision outputs. It does not modify the original chart dashboard.

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_export_dashboard_bridge.py --decisions Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_replay_batch_5ticker_smoke_current_360one_ns_decisions.csv Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_replay_batch_5ticker_smoke_current_aartiind_ns_decisions.csv --liquidity-dirs Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_replay_batch_5ticker_smoke_current_360one_ns Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_replay_batch_5ticker_smoke_current_aartiind_ns --run-id v2_dashboard_bridge_5ticker_smoke_current
```

Expected outputs:

- `dashboard_bridge/<run_id>/live_state.json`
- `dashboard_bridge/<run_id>/cumulative_state.json`
- `dashboard_bridge/<run_id>/signal_ranker_rows.csv`
- `logs/<run_id>.jsonl`
- `audits/<run_id>_audit.json`
- `reports/<run_id>_report.md`

Latest verified result:

- Report: `reports/v2_dashboard_bridge_5ticker_smoke_current_report.md`
- Audit: `audits/v2_dashboard_bridge_5ticker_smoke_current_audit.json`
- Decision rows read: `4`
- Bridge rows written: `4`
- Live rows written: `2`
- Signals with scored liquidity context: `3`
- Bucket counts:
  - `reject`: `2`
  - `insufficient_data`: `2`
- Permission counts:
  - `no`: `4`
- Dashboard contract: `passed`, `4/4` rows complete

The bridge row contains the fields needed for a right-side signal-ranker panel:

- bucket and normalized permission
- raw model score and strict score
- entry, stop, risk
- reason and missing-field diagnostics
- ranked target/adverse liquidity levels
- compact scored-liquidity context from `candidates_scored.csv`
- source decision file for audit traceability

Dashboard bridge for the bounded ten-ticker replay:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_export_dashboard_bridge.py --decisions "Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_replay_original_tail300_10ticker_cached_scorer_normalized*_decisions.csv" --liquidity-dirs "Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_replay_original_tail300_10ticker_cached_scorer_normalized*" --run-id v2_dashboard_bridge_original_tail300_10ticker_cached_scorer_normalized --latest-per-ticker 3 --max-live-rows 50
```

Latest verified result:

- Decision rows read: `12`
- Bridge rows written: `12`
- Live rows written: `12`
- Signals with scored liquidity context: `12`
- Dashboard contract: `passed`
- Contract failed rows: `0`
- Permission counts:
  - `no`: `12`
- Original dashboard modified: `false`

## 17. AWS Readiness Audit

This validates the local AWS deployment skeleton. It does not deploy AWS resources and it does not enable live trading.

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_aws_readiness_audit.py --run-id v2_aws_readiness_current
```

Expected outputs:

- `audits/v2_aws_readiness_current_audit.json`
- `reports/v2_aws_readiness_current_report.md`

Expected safety state:

- AWS skeleton ready: `true`
- AWS deployable ready: `true`
- AWS deployment validated: `false`
- Real-order ready: `false`
- Check count: `77`
- Failed checks: `0`
- Live trading enabled: `false`
- Order placement enabled: `false`
- EventBridge schedule template: `DISABLED`
- S3 layout, ECS/Fargate, IAM/SSM/CloudWatch, EventBridge, rollback, cost controls, and artifact versioning are checked.

AWS skeleton files:

- `aws/Dockerfile`
- `aws/requirements-v2.txt`
- `aws/env.contract.example`
- `aws/ecs-task-definition.template.json`
- `aws/eventbridge-schedule.template.json`
- `aws/iam-policy-runtime.template.json`
- `aws/s3_layout.md`
- `aws/deployment_plan.md`

## 18. Paper Replay From Decision Rows

This replays V2 decision rows against post-decision candles and writes a conservative paper ledger. It does not place orders.

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_paper_replay_from_decisions.py --decisions Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_replay_batch_5ticker_smoke_current_360one_ns_decisions.csv Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_replay_batch_5ticker_smoke_current_aartiind_ns_decisions.csv --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_batch_import_5ticker_smoke --run-id v2_paper_replay_5ticker_smoke_current --notional-capital-inr 1000000
```

Expected outputs:

- `data/paper/<run_id>_trades.csv`
- `data/paper/<run_id>_equity_curve.csv`
- `data/paper/<run_id>_by_bucket.csv`
- `logs/<run_id>.jsonl`
- `audits/<run_id>_audit.json`
- `reports/<run_id>_report.md`

Paper replay safety contract:

- only candles with `time > decision_time` can trigger entry, target, or stop
- default entry policy is `next_touch`
- intrabar ambiguity uses conservative stop-first ordering
- target-liquidity hits are based on decision-time `dt_target_liquidity_*` levels
- 1R and 2R hits are diagnostics, not the primary objective
- rows with `permission != yes` are skipped by default
- diagnostic replay of non-permissioned rows requires `--include-non-permission-decisions`
- notional PnL is a proxy; lot size and margin data are not yet wired

Latest verified result:

- Report: `reports/v2_paper_replay_5ticker_smoke_current_report.md`
- Audit: `audits/v2_paper_replay_5ticker_smoke_current_audit.json`
- Decision rows read: `4`
- Entered trades: `0`
- Not-permissioned rows skipped: `4`
- Notional capital: `1000000`
- Ending equity: `1000000`
- Net PnL: `0`
- Order placement enabled: `false`

## 19. Runtime Cycle Wrapper

This ties replay-batch output, dashboard bridge export, and paper replay into one run-level audit.

Reuse an existing replay batch audit without rerunning slow liquidity scoring:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_runtime_cycle.py --replay-run-id v2_replay_batch_5ticker_smoke_current --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_batch_import_5ticker_smoke --run-id v2_runtime_cycle_5ticker_smoke_current_reuse
```

Run replay batch directly and then export dashboard/paper outputs:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_runtime_cycle.py --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_batch_import_5ticker_smoke --run-id v2_runtime_cycle_5ticker_fresh --limit 5
```

Expected outputs:

- `logs/<run_id>.jsonl`
- `audits/<run_id>_audit.json`
- `reports/<run_id>_report.md`
- dashboard bridge artifacts under `dashboard_bridge/<run_id>_dashboard_bridge/`
- paper replay outputs under `data/paper/<run_id>_paper_*`

This is the current production-style command shape. In full live mode the candle directory should be produced by fresh ingestion first; real order placement remains disabled.

Latest verified runtime-wrapper result:

- Report: `reports/v2_runtime_cycle_5ticker_smoke_current_reuse_report.md`
- Audit: `audits/v2_runtime_cycle_5ticker_smoke_current_reuse_audit.json`
- Status: `passed`
- Replay batch: `reused`
- Dashboard bridge: `passed`
- Paper replay: `passed`
- Runtime wrapper scope: local live-disabled paper cycle; production package readiness is tracked by `v2_production_readiness_current`.

## 20. Live-Disabled Paper Cycle

This is the highest-level current command shape. It ingests candle data first, then runs the runtime cycle over the normalized V2-owned candle directory.

Local CSV smoke:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_live_paper_cycle.py --provider local_csv --ticker 360ONE.NS --source-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_batch_import_5ticker_smoke --run-id v2_live_paper_cycle_360one_local_smoke
```

Fresh yfinance command shape:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_live_paper_cycle.py --provider yfinance --ticker 360ONE.NS --period 60d --run-id v2_live_paper_cycle_360one_yfinance_60d_fixed_smoke --allow-partial
```

Expected outputs:

- normalized candles under `data/raw/<run_id>_ingest/`
- ingestion audit under `audits/<run_id>_ingest_audit.json`
- runtime audit under `audits/<run_id>_runtime_audit.json`
- top-level audit under `audits/<run_id>_audit.json`
- top-level report under `reports/<run_id>_report.md`

Network access is required for the yfinance provider. The command remains paper/manual-review only and does not place orders.

Latest verified yfinance result:

- Status: `passed`
- Ingestion: `passed`
- Runtime cycle: `passed`
- Replay batch: `passed`
- Dashboard bridge: `passed`
- Paper replay: `passed`
- Decision rows: `1`
- Decision bucket: `insufficient_data`

Latest permission-gated paper replay recheck:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_runtime_cycle.py --run-id v2_runtime_cycle_360one_yfinance_60d_permission_gate_recheck --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_live_paper_cycle_360one_yfinance_60d_fixed_smoke_ingest --replay-run-id v2_live_paper_cycle_360one_yfinance_60d_fixed_smoke_runtime_replay
```

Result:

- Decision rows: `1`
- Entered trades: `0`
- Not-permissioned rows skipped: `1`
- Net PnL INR: `0`

## Current Warning

Bridge smoke plus candle signal replay is not full live production. It proves that V2 can run the approved scoring/classification chain on feature-complete event rows, and that V2 can replay long reference signal detection from V2-owned candles.

Still missing for production:

- Production-scale fresh candle fetching across the configured universe.
- Short-side downstream parity: feature generation, approved short artifact inference, dashboard bridge, and paper replay.
- Complete native decision-time feature generation.
- Batch/live-scale visible BSL/SSL scoring from the approved XG liquidity model beyond the 2-row smoke.
- Final entry-candidate generation and full-scale paper ledger validation.
