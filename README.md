# Breaker+FVG Signal System V2

V2 is the production boundary for turning fresh or replayed 1H NSE candles into ranked Breaker+FVG trade decisions.

This folder is intentionally separate from the original Breaker+FVG engine/dashboard. The original code is source/reference only unless explicitly approved.

Standalone setup notes are in:

```text
docs/STANDALONE_REPO_SETUP.md
```

When this repo is cloned separately, run commands from the repo root and use `scripts/...` paths. Set external V1/reference paths through environment variables before running detector/liquidity/scoring workflows.

## Current Modes

- `bridge_smoke`: Uses existing V1 smoke event rows and approved V1 artifact-backed scripts to verify the V2 runtime boundary, logging, audit, and output contracts.
- `local_candle_ingest`: Normalizes local candle CSVs into the V2 raw-candle contract with per-ticker audits.
- `yfinance_candle_ingest`: Fetches and normalizes fresh yfinance candle data; verified on a one-ticker 60-day smoke and a full-original 178-ticker ingestion burn-in.
- `incremental_candle_store`: Maintains a local append/merge candle store so live/replay cycles do not refetch the full history every time.
- `both_side_signal_detect`: Detects long events natively and short events through a V2-owned price-reflection wrapper around the unchanged original detector.
- `replay_smoke`: Runs the current V2 candle-to-decision replay chain on a small candle file.
- `live_disabled_paper_cycle`: Runs fresh/local ingestion, replay/runtime, dashboard bridge, and paper ledger without order placement.
- `live_planned`: Target mode for production-scale fresh 1H ingestion, signal detection, decision-time feature generation, liquidity scoring, and classification.

## Rolling History Contract

The current optimized production path is:

1. Keep a local normalized candle store under `data/raw/v2_incremental_candle_store`.
2. Update that store incrementally before each run.
3. Fetch only from the last stored candle minus a small overlap window, then merge/dedupe.
4. Run signal detection and decision-time liquidity extraction over a larger rolling lookback.

Configured defaults in `configs/v2_runtime_config.example.json`:

- Local store cap: `6000` one-hour candles per ticker.
- Incremental overlap: `5` candles.
- Runtime signal/liquidity lookback: `1500` one-hour candles.

This replaces the earlier `300` candle smoke window as the intended production runtime default. The older `300` candle runs remain historical validation artifacts.

Incremental update command:

```powershell
python scripts/v2_update_candle_store_incremental.py --provider yfinance --universe-file "D:\Coding\Python Codes\Newtest\Breaker_Based\NSE_Symbols.csv" --interval 1h --store-dir data/raw/v2_incremental_candle_store --run-id v2_incremental_store_original_live --initial-period 730d --min-rows 300 --overlap-bars 5 --max-store-candles 6000 --max-retries 3 --retry-sleep-seconds 5
```

Runtime cycle using the configured `1500` candle default:

```powershell
python scripts/v2_run_runtime_cycle.py --candles-dir data/raw/v2_incremental_candle_store --run-id v2_runtime_cycle_original_incremental_1500 --workers 4 --step-timeout-seconds 180 --ticker-timeout-seconds 600
```

To override the configured default for a test:

```powershell
python scripts/v2_run_runtime_cycle.py --candles-dir data/raw/v2_incremental_candle_store --run-id v2_runtime_cycle_original_incremental_custom --max-input-candles 2000 --workers 4
```

## Current Verified Production-Style Runs

The current full-original fresh validation artifact used fresh yfinance candles, a bounded latest `300` candle replay window, decision-time liquidity scoring, approved signal artifacts, dashboard bridge output, and paper replay. That run validates the pipeline, but the newer intended runtime default is the incremental store plus `1500` candle lookback above:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_ingest_candles.py --provider yfinance --universe-file Newtest/Breaker_Based/NSE_Symbols.csv --interval 1h --period 730d --run-id v2_yfinance_ingest_original_179_1h_730d_full_burnin --min-rows 300 --max-retries 3 --retry-sleep-seconds 5
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_replay_batch.py --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_yfinance_ingest_original_179_1h_730d_full_burnin --run-id v2_replay_original_fresh_178_tail300_parallel_burnin --continue-on-error --resume --max-input-candles 300 --step-timeout-seconds 120 --ticker-timeout-seconds 240 --workers 4
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_export_dashboard_bridge.py --decisions "Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_replay_original_fresh_178_tail300_parallel_burnin*_decisions.csv" --liquidity-dirs "Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_replay_original_fresh_178_tail300_parallel_burnin*" --run-id v2_dashboard_bridge_original_fresh_178_tail300_parallel_burnin --latest-per-ticker 3 --max-live-rows 300
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_feature_broad_validation_audit.py --run-id v2_feature_validation_original_fresh_178_tail300_parallel_burnin --replay-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_replay_original_fresh_178_tail300_parallel_burnin_audit.json --min-feature-audits 100 --min-feature-rows 150 --min-classification-allowed-rate 0.95 --max-blocking-missing-tickers 0 --max-missing-all-rows-pct 0.25
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_paper_replay_from_decisions.py --decisions "Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_replay_original_fresh_178_tail300_parallel_burnin*_decisions.csv" --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_yfinance_ingest_original_179_1h_730d_full_burnin --run-id v2_paper_replay_original_fresh_178_tail300_parallel_burnin --notional-capital-inr 1000000 --entry-permission-values yes conditional_take_candidate
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_validation_gate_audit.py --run-id v2_validation_gate_original_fresh_178_tail300_parallel_burnin --replay-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_replay_original_fresh_178_tail300_parallel_burnin_audit.json --dashboard-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_dashboard_bridge_original_fresh_178_tail300_parallel_burnin_audit.json --paper-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_paper_replay_original_fresh_178_tail300_parallel_burnin_audit.json --min-attempted-tickers 178 --min-passed-tickers 100 --min-signal-rows 155 --min-decision-rows 155 --min-scored-liquidity-rows 824 --min-approved-artifact-decision-rows 155 --min-entered-trades 0 --max-failed-tickers 0
```

Latest full-original fresh result:

- `178` deduped original-universe tickers ingested from yfinance, `178` passed, `0` failed.
- Replay attempted `178` tickers: `100` passed, `78` no-signal, `0` failed.
- `155` signal rows.
- `824` visible decision-time liquidity candidates.
- `824` XG-scored liquidity rows.
- `155` approved-artifact decision rows.
- Decision buckets: `reject: 155`.
- Feature validation passed with `100` feature audits, `155` feature rows, `1.0` classification-allowed rate, and `0` blocking-missing tickers.
- Dashboard bridge passed for `155/155` rows, with `155` rows containing scored liquidity context.
- Paper replay passed with real order placement disabled, post-decision candles only, and `0` entered trades because all decisions were `reject`.
- Validation gate passed `12/12`.

The corrected 25-ticker bounded run remains the latest proof with an actual conditional paper entry:

The current best V2 smoke path with an entered candidate is a corrected bounded replay over original-universe cached candles:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_replay_batch.py --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_ingest_original_178_cached_1h_2y_eta --run-id v2_replay_original_tail300_25ticker_parallel_policy_v2_validation --limit 25 --continue-on-error --max-input-candles 300 --step-timeout-seconds 120 --ticker-timeout-seconds 240 --workers 4
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_export_dashboard_bridge.py --decisions "Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_replay_original_tail300_25ticker_parallel_policy_v2_validation*_decisions.csv" --liquidity-dirs "Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_replay_original_tail300_25ticker_parallel_policy_v2_validation*" --run-id v2_dashboard_bridge_original_tail300_25ticker_parallel_policy_v2_validation --latest-per-ticker 3 --max-live-rows 100
```

Latest result:

- `25` tickers attempted.
- `15` tickers produced signal decisions.
- `10` tickers had no current-window signals.
- `0` tickers failed.
- `27` signal rows.
- `168` visible decision-time liquidity candidates.
- `168` XG-scored liquidity rows.
- `27` approved-artifact decision rows.
- Decision buckets: `high_conviction: 1`, `reject: 26`.
- Feature validation: passed with `100%` classification-allowed rows and `0` blocking-missing tickers.
- Dashboard/API bridge contract: passed for `27/27` rows.

This is a runtime/dashboard/API proof, not an edge proof. The bounded `300` candle window intentionally excludes older topology unless a future stateful topology store is added.

Parallel replay is also smoke-tested:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_replay_batch.py --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_ingest_original_178_cached_1h_2y_eta --run-id v2_replay_original_tail300_4ticker_parallel_smoke --limit 4 --continue-on-error --max-input-candles 300 --step-timeout-seconds 120 --ticker-timeout-seconds 240 --workers 2
```

Latest result:

- `4` tickers attempted.
- `2` tickers produced signal decisions.
- `2` tickers had no current-window signals.
- `0` tickers failed.
- `4` signal/decision rows.
- `34` XG-scored liquidity rows.

Parallel mode requires `--continue-on-error` and writes per-ticker launcher logs plus aggregate ETA/progress records.

## Quick Smoke

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_audit_system.py
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_ingest_candles.py --provider local_csv --ticker 360ONE.NS --source-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_import_20260704T203053Z --run-id v2_candle_ingest_360one_local_latest --min-rows 50
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_signal_inference.py --features Newtest/Breaker_Based/signal_model_v2/data/features/v2_bridge_smoke_20260704T202813Z_feature_complete.csv --run-id v2_signal_inference_feature_complete_smoke
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_replay_smoke_pipeline.py --candles Newtest/Breaker_Based/signal_model_v2/data/raw/v2_candle_ingest_360one_local_latest/360ONE.NS_1h.csv --ticker 360ONE.NS --run-id v2_replay_smoke_360one_inference_latest
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_live_paper_cycle.py --provider yfinance --ticker 360ONE.NS --period 60d --run-id v2_live_paper_cycle_360one_yfinance_60d_fixed_smoke --allow-partial
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_detect_signals_from_candles.py --candles Newtest/Breaker_Based/signal_model_v2/data/raw/v2_live_paper_cycle_360one_yfinance_60d_fixed_smoke_ingest/360ONE.NS_1h.csv --ticker 360ONE.NS --side both --run-id v2_signal_detect_360one_both_side_yfinance_60d_smoke
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_build_live_features_from_events.py --events Newtest/Breaker_Based/signal_model_v2/data/signals/v2_signal_detect_360one_both_side_yfinance_60d_smoke_events.csv --candles Newtest/Breaker_Based/signal_model_v2/data/raw/v2_live_paper_cycle_360one_yfinance_60d_fixed_smoke_ingest/360ONE.NS_1h.csv --liquidity-aggregation Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_both_side_yfinance_60d_smoke/signal_liquidity_aggregation.csv --liquidity-scored-candidates Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_both_side_yfinance_60d_smoke/candidates_scored.csv --liquidity-payload-dir Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_both_side_yfinance_60d_smoke/payloads --macro-candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_live_paper_cycle_360one_yfinance_60d_fixed_smoke_ingest --run-id v2_features_360one_both_side_yfinance_60d_smoke
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_signal_inference.py --features Newtest/Breaker_Based/signal_model_v2/data/features/v2_features_360one_both_side_yfinance_60d_smoke_features.csv --feature-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_features_360one_both_side_yfinance_60d_smoke_audit.json --run-id v2_inference_360one_both_side_yfinance_60d_side_gate_smoke
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_export_dashboard_bridge.py --run-id v2_dashboard_bridge_360one_both_side_scored_smoke --decisions Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_inference_360one_both_side_yfinance_60d_side_gate_smoke_decisions.csv --liquidity-dirs Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_both_side_yfinance_60d_smoke
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_burnin_cycle.py --run-id v2_burnin_5ticker_smoke_current --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_batch_import_5ticker_smoke --limit 5 --notional-capital-inr 1000000
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_burnin_cycle.py --run-id v2_burnin_5ticker_smoke_current --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_batch_import_5ticker_smoke --limit 5 --notional-capital-inr 1000000 --resume-replay
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_validation_gate_audit.py --run-id v2_validation_gate_5ticker_smoke_current --replay-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_replay_batch_5ticker_smoke_current_audit.json --dashboard-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_dashboard_bridge_5ticker_smoke_current_audit.json --paper-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_paper_replay_5ticker_smoke_current_audit.json
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_production_readiness_audit.py --run-id v2_production_readiness_current
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_bridge_smoke.py --events Newtest/Breaker_Based/signal_model/datasets/live_inbox/trade_system_minimal_events_smoke27.csv --limit 3
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_replay_batch.py --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_ingest_original_178_cached_1h_2y_eta --run-id v2_replay_original_tail300_10ticker_cached_scorer_normalized --limit 10 --continue-on-error --max-input-candles 300 --step-timeout-seconds 120 --ticker-timeout-seconds 240
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_replay_batch.py --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_ingest_original_178_cached_1h_2y_eta --run-id v2_replay_original_tail300_25ticker_parallel_policy_v2_validation --limit 25 --continue-on-error --max-input-candles 300 --step-timeout-seconds 120 --ticker-timeout-seconds 240 --workers 4
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_replay_batch.py --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_ingest_original_178_cached_1h_2y_eta --run-id v2_replay_original_tail300_4ticker_parallel_smoke --limit 4 --continue-on-error --max-input-candles 300 --step-timeout-seconds 120 --ticker-timeout-seconds 240 --workers 2
```

Outputs are written under:

- `logs/`
- `audits/`
- `reports/`
- `data/predictions/`

## Production Rule

Rows that cannot be feature-complete at decision time must be classified as `insufficient_data`. V2 must not force-score incomplete live rows.

Paper replay only simulates rows with `permission=yes` by default. Non-permissioned rows require an explicit diagnostic flag.

Short-side detection, decision-time liquidity scoring, short artifact feature parity, and approved short artifact inference are smoke-tested.

Mixed long/short feature generation is smoke-tested. In the current side-aware smoke, the long row is scored by the approved long artifact and classified as `reject`; the short row is scored by the approved short artifact and classified as `neutral_no_edge`. Both rows use decision-time feature gates, and incomplete rows still remain `insufficient_data` rather than being force-scored.

The latest dashboard/API bridge smoke writes additive right-panel artifacts without modifying the original dashboard:

- `dashboard_bridge/v2_dashboard_bridge_360one_both_side_scored_smoke/live_state.json`
- `dashboard_bridge/v2_dashboard_bridge_360one_both_side_scored_smoke/cumulative_state.json`
- `dashboard_bridge/v2_dashboard_bridge_360one_both_side_scored_smoke/signal_ranker_rows.csv`

The dashboard contract passed for `2/2` rows with model score, bucket, permission, entry/stop/risk, reason, and scored liquidity context present.

The current production-readiness audit passes `53/53` required local architecture checks. The local package is production-ready and AWS-deployable from local evidence. Full-original yfinance ingestion and full-original latest-window replay are now burn-tested on the deduped original universe. Real AWS deployment validation and real-order readiness remain disabled/unvalidated. The dirty tracked original engine/dashboard files remain a worktree caveat. The burn-in wrapper is smoke-tested and refreshes replay, feature broad-validation, dashboard bridge, paper replay, validation, and production-readiness artifacts under one JSONL log. Replay and burn-in now support resume mode, so interrupted full-universe runs can reuse existing per-ticker audits.

The goal-completion traceability audit is:

- Audit: `audits/v2_goal_completion_current_audit.json`
- Report: `reports/v2_goal_completion_current_report.md`
- Required items: `58/58` passed
- Local goal complete: `true`
- Warnings: AWS not actually deployed, real-order placement disabled, original engine/dashboard tracked files dirty in the worktree

Decision rows now expose stable V2 aliases (`bucket`, `permission`, `model_score`, `reason_codes`) while retaining V1 `tds_*` provenance fields. Dashboard/API bridge rows should use the V2 aliases.

Latest calibrated repair smoke:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_burnin_cycle.py --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_batch_import_5ticker_smoke --run-id v2_burnin_5ticker_patch_feature_smoke_pass --limit 5 --feature-validation-min-audits 2 --feature-validation-min-rows 4 --feature-validation-min-allowed-rate 0.95 --feature-validation-max-blocking-tickers 0 --feature-validation-max-missing-all-pct 0.25 --validation-min-attempted-tickers 5 --validation-min-passed-tickers 2 --validation-min-signal-rows 4 --validation-min-decision-rows 4 --validation-min-scored-liquidity-rows 11 --validation-min-approved-artifact-decision-rows 4 --validation-min-entered-trades 0 --skip-production-readiness-refresh
```

This run passed replay, feature validation, dashboard bridge, paper replay, and the smoke validation gate. Feature validation now has `2` feature audits, `4` feature rows, `1.0` classification-allowed rate, `0` blocking-missing tickers, and `0.132548` missing-all-rows percentage. The previous AARTIIND stop-side/topology/liquidity blocker is fixed by treating absent stop-side pools as zero aggregate pressure/count/score while keeping nearest stop-side fields and two-sided ratios as structural nulls.

The corrected 25-ticker conditional-entry paper replay read `27` decision rows, entered `1` high-conviction conditional candidate, kept real order placement disabled, used only post-decision candles, and wrote `18070.92` INR simulated net PnL on `10L` notional. The full-original fresh replay read `155` decision rows and entered `0` trades because all rows were `reject`; this is a valid no-trade live-state result.
