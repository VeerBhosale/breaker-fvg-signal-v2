# Signal System V2 Current Status

Last updated: 2026-07-05

## Status Summary

V2 is initialized as a separate production boundary under:

`Newtest/Breaker_Based/signal_model_v2`

The original Breaker+FVG engine/dashboard files were not edited.

## Latest Update: Incremental Store And Extended Rolling Lookback

The previous production-style validation used `--max-input-candles 300`, which limited visible liquidity/topology to the latest 300 one-hour candles.

V2 now has an optimized rolling-history path:

- Script: `scripts/v2_update_candle_store_incremental.py`
- Runtime config: `configs/v2_runtime_config.example.json`
- Store dir: `data/raw/v2_incremental_candle_store`
- Store cap: `6000` one-hour candles per ticker
- Incremental overlap: `5` bars
- Configured runtime signal/liquidity lookback: `1500` one-hour candles

Operational meaning:

- The system keeps a local normalized candle store.
- New runs do not need to fetch the full 730-day history every time.
- If a stored ticker file exists, the updater fetches only from `last_stored_time - overlap_bars`, then appends, dedupes, and trims.
- Signal detection and decision-time liquidity extraction can now default to the configured `1500` candle rolling window through `v2_run_runtime_cycle.py`.

Validation:

- Local incremental-store smoke audit: `audits/v2_incremental_store_local_smoke_audit.json`
- Provider: `local_csv`
- Passed tickers: `1`
- Failed tickers: `0`
- New candles retained: `250`
- Static tests cover overlap merge, fetched-row replacement on duplicate timestamps, and trim behavior.

Remaining validation required:

- A network-backed full-original incremental yfinance update should be run before live scheduling.
- A full-original replay over the incremental store with the `1500` candle default should be burn-tested before replacing the older `300` candle validation as the primary production evidence.

## Latest Update: Full-Original Fresh Replay Validation

Current best production-style validation over fresh yfinance candles:

- Replay run ID: `v2_replay_original_tail300_25ticker_parallel_policy_v2_validation`
- Dashboard bridge run ID: `v2_dashboard_bridge_original_tail300_25ticker_parallel_policy_v2_validation`
- Feature validation run ID: `v2_feature_validation_original_tail300_25ticker_parallel_policy_v2_validation`
- Validation gate run ID: `v2_validation_gate_original_tail300_25ticker_parallel_policy_v2_validation`
- Paper replay run ID: `v2_paper_replay_25ticker_policy_v2_conditional_entries`
- Full-original fresh ingestion run ID: `v2_yfinance_ingest_original_179_1h_730d_full_burnin`
- Full-original fresh replay run ID: `v2_replay_original_fresh_178_tail300_parallel_burnin`
- Full-original fresh dashboard bridge run ID: `v2_dashboard_bridge_original_fresh_178_tail300_parallel_burnin`
- Full-original fresh feature validation run ID: `v2_feature_validation_original_fresh_178_tail300_parallel_burnin`
- Full-original fresh validation gate run ID: `v2_validation_gate_original_fresh_178_tail300_parallel_burnin`
- Full-original fresh paper replay run ID: `v2_paper_replay_original_fresh_178_tail300_parallel_burnin`
- Production readiness run ID: `v2_production_readiness_current`

What changed:

- The V2 feature-availability policy now treats valid decision-time absence of deeper ISL, active range context, premium/discount position, and zero-denominator focused-quality ratio fields as structural nulls instead of implementation blockers.
- This does not invent values and does not use outcome data. The approved scorer still receives the same feature schema and handles structural nulls through the approved preprocessing path.
- Static contract tests cover the new structural-null policy.
- Full-original fresh candle ingestion, replay, feature validation, dashboard bridge, paper replay, and validation gate now all have passing artifacts.

Full-original fresh ingestion:

- Deduped original-universe tickers: `178`
- Passed tickers: `178`
- Failed tickers: `0`
- Row count range: `1172` to `5054`
- Median row count: `5052`
- Output dir: `data/raw/v2_yfinance_ingest_original_179_1h_730d_full_burnin`
- Progress log: `logs/v2_yfinance_ingest_original_179_1h_730d_full_burnin.jsonl`

Full-original fresh replay result:

- Attempted tickers: `178`
- Passed tickers: `100`
- No-signal tickers: `78`
- Failed tickers: `0`
- Signal rows: `155`
- Liquidity candidate rows: `824`
- XG-scored liquidity rows: `824`
- Decision rows: `155`
- Approved-artifact decision rows: `155`
- Decision buckets:
  - `reject`: `155`

Full-original fresh feature validation:

- Feature audits: `100`
- Feature rows: `155`
- Classification-allowed rate: `1.0`
- Blocking-missing tickers: `0`
- Missing-all-rows percentage: `0.12732`
- Gate status: `passed`

Full-original fresh dashboard/API bridge:

- Bridge rows written: `155`
- Live rows written: `154`
- Signals with scored liquidity context: `155`
- Dashboard contract: `passed`, `155/155` rows complete
- Original dashboard modified: `false`

Full-original fresh paper replay:

- Decision rows read: `155`
- Entered trades: `0`
- Entry permission values: `yes`, `conditional_take_candidate`
- Notional capital: `1000000`
- Real order placement: `false`
- Uses only post-decision candles: `true`
- Net simulated PnL: `0`
- Interpretation: the latest full-original fresh run produced no permissioned entries because all `155` current-window decisions were `reject`.

Corrected 25-ticker bounded replay remains the latest proof with an actual conditional paper entry:

- Attempted tickers: `25`
- Passed tickers: `15`
- No-signal tickers: `10`
- Failed tickers: `0`
- Signal rows: `27`
- XG-scored liquidity rows: `168`
- Decision rows: `27`
- Buckets: `high_conviction: 1`, `reject: 26`
- Paper replay entered `1` conditional high-conviction candidate, with real order placement disabled, using post-decision candles only.
- Simulated PnL on that one trade: `18070.92` INR on `10L` notional.

Production-readiness audit:

- Required checks: `53/53` passed
- Local production package ready: `true`
- AWS deployable ready: `true`
- AWS deployment validated: `false`
- Real-money ready: `false`
- Remaining blockers:
  - real AWS deployment validation is not performed
  - tracked original engine/dashboard files are dirty in the worktree

Goal-completion traceability audit:

- Audit: `audits/v2_goal_completion_current_audit.json`
- Report: `reports/v2_goal_completion_current_report.md`
- Required items: `58/58` passed
- Local goal complete: `true`
- AWS deployable ready: `true`
- AWS deployment validated: `false`
- Real-money ready: `false`
- Warnings:
  - AWS deployment intentionally not performed
  - real-order placement intentionally disabled
  - original engine/dashboard tracked files are dirty in the worktree

## Latest Update: Bounded Replay, Cached Liquidity Scorer, And Normalized Decision Contract

Current verified run IDs:

- One-ticker replay: `v2_replay_1ticker_full2y_aartiind_tail300_normalized_decision`
- Ten-ticker replay: `v2_replay_original_tail300_10ticker_cached_scorer_normalized`
- Ten-ticker dashboard bridge: `v2_dashboard_bridge_original_tail300_10ticker_cached_scorer_normalized`

What changed:

- Replay and runtime scripts now support `--max-input-candles`, `--step-timeout-seconds`, and `--ticker-timeout-seconds`.
- Signal detection and liquidity payload generation can run over a bounded latest-candle window instead of replaying the full two-year ticker history for every smoke.
- `scripts/v2_score_liquidity_candidates.py` now persists the approved liquidity XG model under V2 `models/` and reloads it when the feature/training signature matches.
- This removes the repeated per-ticker retraining bottleneck from V2 replay and keeps the approved V1 scoring logic as the source of model behavior.
- `scripts/v2_run_signal_inference.py` now writes stable V2 aliases on every decision row:
  - `bucket`
  - `permission`
  - `trade_system_action`
  - `trade_action`
  - `model_score`
  - `model_probability`
  - `reason_codes`
  - `decision_source`
- The original V1 provenance columns such as `tds_decision_class`, `tds_entry_permission`, `tds_trade_action`, and `tds_reason` are preserved.

One-ticker verification:

- Ticker: `AARTIIND.NS`
- Max input candles: `300`
- Step timeout: `120` seconds
- Ticker timeout: `240` seconds
- Status: `passed`
- Signal rows: `1`
- Liquidity candidate rows: `6`
- Scored liquidity rows: `6`
- Decision rows: `1`
- Inference path: `approved_artifact_scoring`
- Decision bucket: `reject`
- Normalized decision aliases present: `true`

Ten-ticker bounded replay:

- Attempted tickers: `10`
- Passed tickers: `6`
- No-signal tickers: `4`
- Failed tickers: `0`
- Signal rows: `12`
- Liquidity candidate rows: `96`
- Scored liquidity rows: `96`
- Feature rows: `12`
- Decision rows: `12`
- Decision buckets:
  - `reject`: `11`
  - `insufficient_data`: `1`
- Approved artifact scoring rows: `11`
- Insufficient-data rows: `1`

Dashboard/API bridge over the ten-ticker replay:

- Decision rows read: `12`
- Bridge rows written: `12`
- Live rows written: `12`
- Signals with scored liquidity context: `12`
- Dashboard contract: `passed`
- Contract failed rows: `0`
- Original dashboard modified: `false`

Important limitation:

- The bounded `--max-input-candles 300` replay is a current/latest-window production smoke, not a full chronological backtest.
- Levels older than the bounded window are intentionally not visible unless a future stateful topology store is added.
- This is acceptable for runtime smoke and dashboard/API contract validation; it is not sufficient proof of final trading edge or full-universe production readiness.

Parallel replay smoke:

- Run ID: `v2_replay_original_tail300_4ticker_parallel_smoke`
- Workers: `2`
- Attempted tickers: `4`
- Passed tickers: `2`
- No-signal tickers: `2`
- Failed tickers: `0`
- Signal rows: `4`
- Liquidity candidate rows: `34`
- Scored liquidity rows: `34`
- Decision rows: `4`
- Decision buckets:
  - `reject`: `4`
- Aggregate log includes ticker submission, completion, ETA, and per-ticker launcher-log paths.
- `--workers > 1` requires `--continue-on-error` so every submitted ticker can finish with an auditable row.

## Latest Update: Production Readiness Audit

Production readiness is now tracked by a dedicated V2 audit:

- Script: `scripts/v2_production_readiness_audit.py`
- Audit: `audits/v2_production_readiness_current_audit.json`
- Report: `reports/v2_production_readiness_current_report.md`
- Required checks: `53/53` passed
- Local production package ready: `true`
- AWS deployable ready: `true`
- AWS deployment validated: `false`
- Real-money ready: `false`

Interpretation:

- The local V2 architecture now passes the required smoke evidence for full-original yfinance ingestion, full-original fresh replay, feature validation, decision-time liquidity scoring, approved artifact scoring, dashboard bridge output, paper replay safety, one corrected 25-ticker conditional paper entry, runtime orchestration, and AWS skeleton readiness.
- The local V2 package is production-ready and AWS-deployable from local evidence.
- It is not real-money ready because AWS deployment validation and explicit live-order approval are still absent.

Remaining caveats before real deployment/live-money promotion:

- Full original-universe fresh ingestion, replay, feature validation, dashboard bridge, paper replay, and validation gate are now proven on the latest 300-candle window.
- The full-original fresh run produced `155` decisions and all were `reject`; that is a valid no-trade live state, not a missing pipeline result.
- The corrected 25-ticker replay produced one `high_conviction` row and 26 `reject` rows; paper replay entered the one conditional high-conviction candidate with real order placement disabled.
- AWS skeleton is locally audit-ready, but no real ECR/ECS/S3/CloudWatch deployment validation has been performed.
- Original engine/dashboard tracked files are dirty in the worktree, so the current workspace cannot prove a clean original-source baseline.

Historical 5-ticker validation blocker, retained as baseline context:

- Script: `scripts/v2_validation_gate_audit.py`
- Audit: `audits/v2_validation_gate_5ticker_smoke_current_audit.json`
- Report: `reports/v2_validation_gate_5ticker_smoke_current_report.md`
- Validation checks: `5/12` passed
- Failed validation checks:
  - `attempted_ticker_scope`
  - `passed_ticker_scope`
  - `signal_row_scope`
  - `decision_row_scope`
  - `scored_liquidity_scope`
  - `approved_artifact_scoring_scope`
  - `paper_replay_has_entries`
- Current validation metrics:
  - Attempted tickers: `5`
  - Passed tickers: `2`
  - Signal rows: `4`
  - Decision rows: `4`
  - Scored liquidity rows: `11`
  - Approved-artifact decision rows: `2`
  - Paper entered trades: `0`

Historical failed feature-substitution baseline:

- Script: `scripts/v2_feature_broad_validation_audit.py`
- Audit: `audits/v2_burnin_5ticker_smoke_current_feature_validation_audit.json`
- Report: `reports/v2_burnin_5ticker_smoke_current_feature_validation_report.md`
- Feature validation checks: `1/5` passed
- Failed feature checks:
  - `feature_audit_scope`
  - `feature_row_scope`
  - `classification_allowed_rate`
  - `blocking_missing_ticker_limit`
- Current feature validation metrics:
  - Feature audits: `2`
  - Feature rows: `4`
  - Classification allowed rate: `0.5`
  - Blocking missing tickers: `1`
  - Missing-all-rows percentage: `0.141384`
- Current blocking ticker: `AARTIIND.NS`
- Main blocking feature group: stop-side/topology/liquidity context below the long signal.

Latest repair smoke:

- Run ID: `v2_burnin_5ticker_patch_feature_smoke_pass`
- Audit: `audits/v2_burnin_5ticker_patch_feature_smoke_pass_audit.json`
- Report: `reports/v2_burnin_5ticker_patch_feature_smoke_pass_report.md`
- Status: `passed_smoke`
- Validation passed: `true`
- Replay: `5` attempted tickers, `2` passed tickers, `3` no-signal tickers, `4` signal rows, `4` decision rows, `11` scored liquidity rows
- Feature validation: `5/5` smoke checks passed
  - Feature audits: `2`
  - Feature rows: `4`
  - Classification allowed rate: `1.0`
  - Blocking missing tickers: `0`
  - Missing-all-rows percentage: `0.132548`
- Dashboard bridge: passed after representing zero visible liquidity as `summary_only_no_visible_levels`
- Paper replay: passed in paper-disabled/no-permission mode with `0` entered trades
- Validation gate: passed under explicit 5-ticker smoke thresholds

Interpretation:

- The AARTIIND blocker was repaired without inventing fake nearest stop-side liquidity.
- Empty stop-side liquidity now produces zero aggregate score/pressure/count fields.
- Nearest stop-side fields and two-sided ratios remain structural nulls when the relevant side does not exist.
- Target-distance aliases are derived from the decision-time nearest target liquidity only when a target-side level exists.
- This retires the immediate 5-ticker feature-blocking issue, but it does not retire the full-universe production-validation blocker.

The burn-in cycle wrapper is now smoke-tested:

- Script: `scripts/v2_run_burnin_cycle.py`
- Audit: `audits/v2_burnin_5ticker_smoke_current_audit.json`
- Report: `reports/v2_burnin_5ticker_smoke_current_report.md`
- Log: `logs/v2_burnin_5ticker_smoke_current.jsonl`
- Status: `passed_smoke`
- Resume replay support: `true`
- Latest replay resumed tickers: `5/5`
- Phases run:
  - existing V2 candle directory reused
  - replay batch executed
  - feature broad validation executed and failed as expected on scope/completeness
  - dashboard bridge executed
  - paper replay executed
  - validation gate executed and failed as expected on scope
  - production-readiness audit refreshed
- Validation passed: `false`
- Failed phases: `feature_validation`, `validation_gate`

Resume behavior:

- `scripts/v2_run_replay_batch.py` supports `--resume`.
- `scripts/v2_run_burnin_cycle.py` supports `--resume-replay`.
- Existing per-ticker replay audits are reused and still included in aggregate replay counts.
- This makes interrupted full-universe burn-ins restartable without rerunning completed tickers.

## Latest Update: Fresh Yfinance Smoke + Paper Permission Gate

Fresh yfinance ingestion is now verified on a one-ticker smoke:

- Run ID: `v2_yfinance_ingest_360one_60d_fixed_smoke_v2`
- Provider: `yfinance`
- Ticker: `360ONE.NS`
- Period: `60d`
- Input rows: `395`
- Output rows: `395`
- Null/unparseable rows dropped: `0`
- Duplicate timestamps: `0`
- Status: `passed`

The yfinance parser bug was fixed. The root cause was that timezone-aware provider `Datetime` columns were being picked up by the numeric timestamp branch as nanoseconds, then incorrectly parsed as milliseconds.

Fresh yfinance live-disabled paper cycle smoke:

- Run ID: `v2_live_paper_cycle_360one_yfinance_60d_fixed_smoke`
- Status: `passed`
- Ingestion: `passed`
- Runtime cycle: `passed`
- Replay batch: `passed`
- Dashboard bridge: `passed`
- Paper replay: `passed`
- Signal rows: `1`
- Liquidity candidate rows: `2`
- Decision rows: `1`
- Decision bucket: `insufficient_data`

Paper replay was tightened after this smoke. By default, it now simulates entries only when `permission=yes`. Rows classified as `insufficient_data`, `reject`, or otherwise non-permissioned are written as skipped rows. Diagnostic replay of non-permissioned rows requires `--include-non-permission-decisions`.

Verified permission-gate recheck:

- Run ID: `v2_runtime_cycle_360one_yfinance_60d_permission_gate_recheck`
- Decision rows: `1`
- Entered trades: `0`
- Not-permissioned rows skipped: `1`
- Net PnL INR: `0`

Current system audit after the fix:

- Audit: `audits/v2_system_audit_latest.json`
- Checks: `57`
- Failures: `0`
- Scope: local file-boundary/system-contract audit only. Production-readiness status is tracked by `v2_production_readiness_current`.

## Latest Update: Short-Side Detection And Liquidity Smoke

V2 now supports a short-side detector smoke without editing the original engine. The method is an explicit V2-owned price-reflection wrapper:

- transform candles with `P' = -P`
- run the original unchanged long detector
- map detected long-style levels back to short-side fields
- keep the transform visible through `mirror_transform_applied=true`

Latest both-side detector smoke:

- Run ID: `v2_signal_detect_360one_both_side_yfinance_60d_smoke`
- Source candles: `data/raw/v2_live_paper_cycle_360one_yfinance_60d_fixed_smoke_ingest/360ONE.NS_1h.csv`
- Input rows: `395`
- Signal rows: `2`
- Side counts:
  - `long`: `1`
  - `short`: `1`
- Risk invalid rows: `0`
- Decision-before-signal rows: `0`
- Status: `passed`

The short event was then passed through the decision-time liquidity path:

- Payload run ID: `v2_liquidity_payloads_360one_both_side_yfinance_60d_smoke`
- Payloads written: `2`
- Payloads skipped: `0`
- Candidate rows: `4`
- Candidate role counts:
  - `target_side`: `2`
  - `stop_or_swept_side`: `2`
- Candidate side counts:
  - `BSL`: `2`
  - `SSL`: `2`
- Required liquidity features: `391`
- Missing liquidity feature count: `0`
- Cutoff violations: `0`
- XG liquidity score rows: `4`
- XG score range: `49.58` to `62.37`
- Aggregated signal rows: `2`
- Aggregation decision-side counts:
  - `long`: `1`
  - `short`: `1`
- Signals missing candidates: `0`

Important interpretation:

- V2 can now create standardized long and short signal events from V2-owned candles.
- V2 can now build and score visible decision-time liquidity candidates for both sides.
- V2 can now score both long and short signal rows with the approved artifact paths when their side-specific feature contracts are complete.

## Latest Update: Mixed-Side Feature Safety, Short Parity, And Long/Short Artifact Scoring

The both-side smoke now passes through feature construction and the side-aware inference gate without force-scoring incomplete or unaudited rows. The long row is feature-complete under the approved long-model contract and is scored by the approved V1 long signal artifact. The short row is feature-complete under the approved 1194-feature short artifact contract and is scored by the approved V1 short signal artifact.

Feature build:

- Run ID: `v2_features_360one_both_side_yfinance_60d_smoke`
- Output: `data/features/v2_features_360one_both_side_yfinance_60d_smoke_features.csv`
- Audit: `audits/v2_features_360one_both_side_yfinance_60d_smoke_audit.json`
- Feature rows: `2`
- Side mix: `long: 1`, `short: 1`
- Global classification status: `classification_allowed`
- Global blocking missing required features: `0`
- Global raw missing features on all rows: `152`
- Global structural-null missing features on all rows: `152`
- Long-side classification status: `classification_allowed`
- Long-side blocking missing required features: `0`
- Short-side classification status: `classification_allowed`
- Short-side required short-artifact features: `1194`
- Short-side feature complete: `true`
- Short-side available on all rows: `844`
- Short-side blocking missing required features: `0`
- Short-side raw missing required features: `350`
- Short-side structural-null missing required features: `350`
- Short-side approved inference contract: `true`

Inference gate:

- Run ID: `v2_inference_360one_both_side_yfinance_60d_side_gate_smoke`
- Audit: `audits/v2_inference_360one_both_side_yfinance_60d_side_gate_smoke_audit.json`
- Decisions: `data/predictions/v2_inference_360one_both_side_yfinance_60d_side_gate_smoke_decisions.csv`
- Inference path: `approved_artifact_scoring`
- Bucket counts: `reject: 1`, `neutral_no_edge: 1`
- Side counts: `long: 1`, `short: 1`
- Rows scored: `2`
- Rows blocked before scoring: `0`
- Long decision class: `reject`
- Short decision class: `neutral_no_edge`
- Artifact scoring: `long_scored_rows: 1`, `short_scored_rows: 1`

Implementation detail:

- Short rows use explicit V2 compatibility aliases so live-safe short fields such as `t1_sweep_high_price`, `signal_low_price`, `bear_fvg_*`, target-side SSL topology, and stop-side BSL topology are mapped into the short artifact contract.
- Those aliases are a transport/audit safety layer. They only allow scoring when the side-specific feature contract is complete and the approved short artifact/config paths are present.
- Short phase and candle-quality metrics are side-normalized into the existing feature names for audit safety.
- Side-aware inference blocks rows unless their side-specific feature contract is complete and the relevant artifact/config paths are present.
- Long rows now use a decision-time payload FVG fallback when the detector event lacks explicit `bull_fvg_*` bounds. The fallback uses the nearest active same-direction FVG from the decision-time payload and recomputes FVG reaction fields.
- Missing nearest bull/bear FVG context is treated as a structural null when no active decision-time FVG of that type exists.
- Static tests now cover this behavior.

Dashboard/API bridge:

- Run ID: `v2_dashboard_bridge_360one_both_side_scored_smoke`
- Audit: `audits/v2_dashboard_bridge_360one_both_side_scored_smoke_audit.json`
- Report: `reports/v2_dashboard_bridge_360one_both_side_scored_smoke_report.md`
- Live state: `dashboard_bridge/v2_dashboard_bridge_360one_both_side_scored_smoke/live_state.json`
- Cumulative state: `dashboard_bridge/v2_dashboard_bridge_360one_both_side_scored_smoke/cumulative_state.json`
- Signal ranker rows: `dashboard_bridge/v2_dashboard_bridge_360one_both_side_scored_smoke/signal_ranker_rows.csv`
- Bridge rows: `2`
- Live rows: `2`
- Signals with scored liquidity context: `2`
- Bucket counts: `reject: 1`, `neutral_no_edge: 1`
- Dashboard contract: `passed`, `2/2` rows complete, `0` missing required right-panel fields
- Original dashboard modified: `false`

Fresh 5-ticker replay/dashboard/paper smoke with current code:

- Replay run ID: `v2_replay_batch_5ticker_smoke_current`
- Replay audit: `audits/v2_replay_batch_5ticker_smoke_current_audit.json`
- Replay report: `reports/v2_replay_batch_5ticker_smoke_current_report.md`
- Selected tickers: `5`
- Passed tickers: `2`
- No-signal tickers: `3`
- Failed tickers: `0`
- Signal rows: `4`
- Scored liquidity rows: `11`
- Decision rows: `4`
- Decision buckets: `reject: 2`, `insufficient_data: 2`
- Approved artifact scoring tickers: `360ONE.NS`
- Insufficient-data tickers: `AARTIIND.NS` due `20` blocking required fields
- Dashboard bridge run ID: `v2_dashboard_bridge_5ticker_smoke_current`
- Dashboard bridge audit: `audits/v2_dashboard_bridge_5ticker_smoke_current_audit.json`
- Dashboard contract: `passed`, `4/4` rows complete for right-panel/API output
- Paper replay run ID: `v2_paper_replay_5ticker_smoke_current`
- Paper audit: `audits/v2_paper_replay_5ticker_smoke_current_audit.json`
- Paper rows written: `4`
- Entered trades: `0`
- Not-permissioned rows skipped: `4`
- Notional capital: `1000000`
- Ending equity: `1000000`
- Order placement enabled: `false`
- Runtime wrapper run ID: `v2_runtime_cycle_5ticker_smoke_current_reuse`
- Runtime audit: `audits/v2_runtime_cycle_5ticker_smoke_current_reuse_audit.json`
- Runtime status: `passed`
- Runtime phases: replay batch `reused`, dashboard bridge `passed`, paper replay `passed`
- Runtime production ready: `false`

## Latest Update: Feature Availability Policy + Native Artifact Scoring

Latest run ID: `v2_replay_smoke_360one_policy_gate_final`

V2 now reproduces a much larger share of the approved long-model feature contract from live-safe inputs:

- legacy `dt_liq_*` support aliases needed by the approved V1 signal scorer
- EQP-style phase metrics for impulse, sweep, reversal, full setup, pre-sweep, and pre-signal windows
- focused-quality `fq_*` formulas from the approved historical feature pipeline
- macro/universe context from a candle directory using decision-time `exact_or_previous` joins
- signal-crowding context from event rows
- technical payload context from decision-time liquidity payload JSON files:
  - premium/discount fields
  - market skeleton state/counts
  - structure-state counts
  - active FVG context
  - active SH/SL/ISH/ISL context
  - recent structure event counts
- scored-candidate topology fields rebuilt from decision-time `candidates_scored.csv`:
  - BSL above / BSL below / SSL above / SSL below ranked ladders
  - per-rank score, distance, batch percentile, price distance, and live-safe age proxy
  - BSL and SSL pairwise stack/density metrics
  - upside vs downside pressure, density, gap, and score-slope interactions
- `goal_*` liquidity-path composites
- `gtopo_*` gated topology composites
- `rerank_*` second-stage topology/path composites
- `foldrisk_*` and `foldedge_*` stability composites
- `topbucket_*` high-conviction selection composites
- V2 feature availability policy:
  - separates valid structural nulls from production-blocking missing features
  - allows artifact scoring only when `blocking_missing_all_rows_count = 0`
  - keeps raw missing counts visible in the audit

Coverage improved on the same 360ONE replay smoke input:

| Metric | Baseline Native + Liquidity | After Derived | After EQP/Focused | After Macro + Payload | After Candidate Topology | After Policy Gate |
|---|---:|---:|---:|---:|---:|---:|
| Required long features | 679 | 679 | 679 | 679 | 679 | 679 |
| Available on all rows | 248 | 323 | 389 | 474 | 481 | 501 |
| Available on partial rows | 53 | 65 | 66 | 67 | 87 | 102 |
| Missing on all rows | 378 | 291 | 224 | 138 | 111 | 76 |
| Structural-null missing on all rows | n/a | n/a | n/a | n/a | n/a | 76 |
| Blocking missing on all rows | n/a | n/a | n/a | n/a | n/a | 0 |
| Macro available on all rows | 11 / 44 | 11 / 44 | 11 / 44 | 44 / 44 | 44 / 44 | 44 / 44 |
| Technical available on all rows | 96 / 153 | 96 / 153 | 96 / 153 | 148 / 153 | 148 / 153 | 148 / 153 |
| Focused quality available on all rows | 4 / 79 | 4 / 79 | 70 / 79 | 70 / 79 | 70 / 79 | 74 / 79 |
| Composite available on all rows | 0 / 40 | 40 / 40 | 40 / 40 | 40 / 40 | 40 / 40 | 40 / 40 |
| Topology available on all rows | 28 / 212 | 54 / 212 | 54 / 212 | 54 / 212 | 61 / 212 | 75 / 212 |
| Topology available on partial rows | 16 / 212 | n/a | n/a | n/a | 48 / 212 | 62 / 212 |
| Classification status | `insufficient_data` | `insufficient_data` | `insufficient_data` | `insufficient_data` | `insufficient_data` | `approved_artifact_scoring` |

Latest policy-gated replay outputs:

- Replay report: `Newtest/Breaker_Based/signal_model_v2/reports/v2_replay_smoke_360one_policy_gate_final_report.md`
- Replay audit: `Newtest/Breaker_Based/signal_model_v2/audits/v2_replay_smoke_360one_policy_gate_final_audit.json`
- Features: `Newtest/Breaker_Based/signal_model_v2/data/features/v2_replay_smoke_360one_policy_gate_final_features.csv`
- Feature audit: `Newtest/Breaker_Based/signal_model_v2/audits/v2_replay_smoke_360one_policy_gate_final_features_audit.json`
- Feature summary: `Newtest/Breaker_Based/signal_model_v2/reports/v2_replay_smoke_360one_policy_gate_final_feature_parity_summary.csv`
- Scored liquidity candidates: `Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_replay_smoke_360one_policy_gate_final/candidates_scored.csv`
- Inference audit: `Newtest/Breaker_Based/signal_model_v2/audits/v2_replay_smoke_360one_policy_gate_final_signal_inference_audit.json`
- Decisions: `Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_replay_smoke_360one_policy_gate_final_decisions.csv`

The inference gate now allows approved artifact scoring when only structural nulls remain:

- Inference path: `approved_artifact_scoring`
- Bucket counts: `reject: 2`
- Missing required features on all rows: `76`
- Structural-null missing features on all rows: `76`
- Blocking missing features on all rows: `0`

The remaining all-row missing features are valid structural nulls in this smoke:

- `topo_*` depth fields for absent fourth/fifth ladder levels
- `topo_*_percentile` fields when candidate rank depth is absent; present candidate rows currently use the scorer's batch percentile, not a calibrated production percentile
- `topo_*_age_bars` fields when candidate rank depth is absent; present candidate rows use `current_member_age_bars_mean` as the live-safe age proxy
- adjacent pairwise stack fields where one or both levels in the pair do not exist
- `cq_reversal_vs_drop_impulse` when the denominator leg has zero measurable impulse

The policy file is:

`Newtest/Breaker_Based/signal_model_v2/configs/v2_feature_availability_policy.json`

This does not make V2 production-ready. It proves the one-ticker replay path can now produce a native feature row, score visible liquidity, aggregate topology, and classify through the approved long model artifact without using precomputed historical signal rows.

## Latest Update: Multi-Ticker Replay Orchestration

V2 now has a batch candle importer and a batch replay runner:

- `scripts/v2_import_payload_candles_batch.py`
- `scripts/v2_run_replay_batch.py`

Latest batch candle import:

- Run ID: `v2_payload_batch_import_5ticker_smoke`
- Audit: `audits/v2_payload_batch_import_5ticker_smoke_audit.json`
- Imported tickers: `5`
- Passed tickers: `5`
- Failed tickers: `0`
- Rows per ticker: `250`
- Duplicate timestamps: `0`
- Null OHLC values: `0`

Latest batch replay:

- Run ID: `v2_replay_batch_5ticker_smoke_fixed`
- Report: `reports/v2_replay_batch_5ticker_smoke_fixed_report.md`
- Audit: `audits/v2_replay_batch_5ticker_smoke_fixed_audit.json`
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

Per-ticker replay status:

| Ticker | Status | Signals | Decisions | Inference path |
|---|---:|---:|---:|---|
| `360ONE.NS` | `passed` | `2` | `2` | `approved_artifact_scoring` |
| `3MINDIA.NS` | `no_signals` | `0` | `0` | n/a |
| `AADHARHFC.NS` | `no_signals` | `0` | `0` | n/a |
| `AARTIIND.NS` | `passed` | `2` | `2` | `insufficient_data_gate` |
| `AAVAS.NS` | `no_signals` | `0` | `0` | n/a |

Important interpretation:

- `no_signals` is now treated as an explicit non-failure batch state. It means the V2 detector found no qualifying long setup in that candle window.
- Partial liquidity payload generation is tolerated only when at least one signal event has a valid decision-time payload. Skipped events remain visible in the payload audit.
- This is batch-orchestration proof, not production readiness.

## Latest Update: Dashboard/API Bridge Output

V2 now has an additive dashboard/API bridge exporter:

- `scripts/v2_export_dashboard_bridge.py`

Latest bridge export:

- Run ID: `v2_dashboard_bridge_5ticker_smoke_fixed`
- Report: `reports/v2_dashboard_bridge_5ticker_smoke_fixed_report.md`
- Audit: `audits/v2_dashboard_bridge_5ticker_smoke_fixed_audit.json`
- Live state: `dashboard_bridge/v2_dashboard_bridge_5ticker_smoke_fixed/live_state.json`
- Cumulative state: `dashboard_bridge/v2_dashboard_bridge_5ticker_smoke_fixed/cumulative_state.json`
- Ranker rows: `dashboard_bridge/v2_dashboard_bridge_5ticker_smoke_fixed/signal_ranker_rows.csv`
- Decision rows read: `4`
- Bridge rows written: `4`
- Live rows written: `2`
- Signals with scored liquidity context: `3`
- Bucket counts:
  - `reject`: `2`
  - `insufficient_data`: `2`
- Permission counts:
  - `no`: `4`

The bridge is deliberately additive. It does not replace or edit the original dashboard. It produces the data contract needed by a right-side signal-ranker panel:

- bucket and normalized permission
- model score / strict score
- entry, stop, risk
- target liquidity levels
- adverse liquidity levels
- compact scored BSL/SSL context from `candidates_scored.csv`
- reason codes and missing-data warnings
- source decision file for traceability

## Latest Update: AWS Deployment Skeleton

V2 now has an auditable AWS packaging/deployment skeleton under:

`Newtest/Breaker_Based/signal_model_v2/aws`

Added AWS files:

- `Dockerfile`
- `requirements-v2.txt`
- `env.contract.example`
- `ecs-task-definition.template.json`
- `eventbridge-schedule.template.json`
- `iam-policy-runtime.template.json`
- `s3_layout.md`
- `deployment_plan.md`

Added readiness audit:

- `scripts/v2_aws_readiness_audit.py`
- Latest audit: `audits/v2_aws_readiness_current_audit.json`
- Latest report: `reports/v2_aws_readiness_current_report.md`
- Check count: `77`
- Failed checks: `0`
- AWS skeleton ready: `true`
- AWS deployable ready: `true`
- AWS deployment validated: `false`
- Real-order ready: `false`

The AWS skeleton is intentionally paper/manual-review only:

- live trading disabled by default
- order placement disabled by default
- manual review required by default
- EventBridge schedule template disabled by default
- S3 layout, ECS/Fargate task, IAM/SSM/CloudWatch permissions, EventBridge schedule, rollback, cost controls, and artifact-versioning contracts are audited

This is not an AWS deployment and not proof of live production readiness. It is the packaging and deployment contract needed before a real AWS run.

## Latest Update: Paper Replay Ledger Skeleton

V2 now has a paper replay script:

- `scripts/v2_paper_replay_from_decisions.py`

The script consumes V2 decision rows and candle CSVs, then writes:

- `data/paper/<run_id>_trades.csv`
- `data/paper/<run_id>_equity_curve.csv`
- `data/paper/<run_id>_by_bucket.csv`
- `audits/<run_id>_audit.json`
- `reports/<run_id>_report.md`
- `logs/<run_id>.jsonl`

Safety contract:

- only post-decision candles are used: `time > decision_time`
- default entry policy: `next_touch`
- intrabar ambiguity: conservative stop-first ordering
- target-side liquidity hits use decision-time `dt_target_liquidity_*` fields
- fixed 1R/2R hits are reported only as diagnostics
- rows with `permission != yes` are skipped by default
- notional PnL defaults to `1000000` INR per trade proxy
- derivatives lot size and margin are explicitly marked unavailable until wired

This gives V2 a concrete paper-execution artifact path, but it is not yet a full production paper-trading system until validated on larger long/short batches with final entry-candidate parity.

## Latest Update: Runtime Cycle Wrapper

V2 now has a run-level wrapper:

- `scripts/v2_run_runtime_cycle.py`

The wrapper can either:

- reuse an existing replay batch audit, or
- run `v2_run_replay_batch.py` directly from a candle directory.

Then it collects decision CSVs and liquidity directories from the replay audit and runs:

1. dashboard bridge export
2. paper replay ledger

It writes one runtime-cycle log, audit, and report:

- `logs/<run_id>.jsonl`
- `audits/<run_id>_audit.json`
- `reports/<run_id>_report.md`

This is the current production-style orchestration surface. It is still paper/manual-review only and does not place orders.

## Latest Update: Live-Disabled Paper Cycle

V2 now has a top-level runner:

- `scripts/v2_run_live_paper_cycle.py`

This command performs:

1. candle ingestion from local CSV or yfinance
2. normalized V2-owned candle output
3. replay/runtime cycle
4. dashboard bridge export
5. paper replay ledger

It writes:

- `audits/<run_id>_audit.json`
- `reports/<run_id>_report.md`
- `logs/<run_id>.jsonl`
- child ingestion/runtime audits and reports

This is live-disabled and paper/manual-review only. It is the current closest shape to a production runtime command, but yfinance fresh ingestion still requires network/runtime validation.

## What Works Now

### V2 Boundary

- Required folders exist.
- V2 configs/contracts exist.
- V2 model registry points to current approved V1 liquidity, long signal, and short signal artifacts.
- System audit passes.
- Static contract tests pass with standard-library `unittest`.

### Payload Candle Import

V2 can import existing engine payload candle windows into V2-owned raw candle CSVs for replay-safe work.

Latest verified import:

- Audit: `Newtest/Breaker_Based/signal_model_v2/audits/v2_payload_import_20260704T203053Z_audit.json`
- Ticker: `360ONE.NS`
- Rows: `250`
- Duplicate times: `0`
- Null OHLC values: `0`
- Monotonic time order: `true`

### Candle Ingestion

V2 now has a general candle ingestion script for normalized candle files:

- Script: `Newtest/Breaker_Based/signal_model_v2/scripts/v2_ingest_candles.py`
- Working provider: `local_csv`
- Fresh provider: `yfinance` has passed a one-ticker 60-day smoke; it still requires full-universe/runtime burn-in before production use.
- Output contract: `time,open,high,low,close` with `time` as UTC Unix seconds.

Latest verified clean local ingestion smoke:

- Audit: `Newtest/Breaker_Based/signal_model_v2/audits/v2_candle_ingest_360one_local_latest_audit.json`
- Source: `Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_import_20260704T203053Z/360ONE.NS_1h_1747637100.csv`
- Output: `Newtest/Breaker_Based/signal_model_v2/data/raw/v2_candle_ingest_360one_local_latest/360ONE.NS_1h.csv`
- Ticker count: `1`
- Passed count: `1`
- Rows: `250`
- Duplicate time rows: `0`
- Null OHLC values: `0`
- Large gap count: `36`

### Raw Candle Signal Detection

V2 can replay long-side Breaker+FVG signal detection from V2-owned candle CSVs by importing the original dashboard exporter as source/reference and calling `analyze_ticker`.

The original engine/dashboard source file was not edited.

Latest verified replay:

- Script: `Newtest/Breaker_Based/signal_model_v2/scripts/v2_detect_signals_from_candles.py`
- Source candles: `Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_import_20260704T203053Z/360ONE.NS_1h_1747637100.csv`
- Ticker: `360ONE.NS`
- Input candles: `250`
- Long signals produced: `2`
- Output events: `Newtest/Breaker_Based/signal_model_v2/data/signals/v2_signal_detect_360one_smoke_events.csv`
- Audit: `Newtest/Breaker_Based/signal_model_v2/audits/v2_signal_detect_360one_smoke_audit.json`
- Stricter timing smoke: `Newtest/Breaker_Based/signal_model_v2/audits/v2_signal_detect_360one_next_candle_smoke_audit.json`

Important constraint:

- The original reference detector identifies swing highs using the next candle and has `FVG_CONFIRM_AFTER_SIGNAL_CANDLES = 1`.
- For dashboard compatibility, `decision_time_policy=signal_time` is available.
- For live-safety review, `decision_time_policy=next_candle_after_signal` is available.
- The final production contract must explicitly choose and validate one policy before order decisions are allowed.

### Native Live Feature Generation

V2 can now build a partial live-safe long feature row directly from:

1. V2 standardized signal events.
2. V2-owned candle CSVs.
3. Signal metrics emitted by the original reference detector.

Latest verified run:

- Script: `Newtest/Breaker_Based/signal_model_v2/scripts/v2_build_live_features_from_events.py`
- Input events: `Newtest/Breaker_Based/signal_model_v2/data/signals/v2_signal_detect_360one_smoke_events.csv`
- Input candles: `Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_import_20260704T203053Z/360ONE.NS_1h_1747637100.csv`
- Output features: `Newtest/Breaker_Based/signal_model_v2/data/features/v2_live_features_360one_with_liquidity_smoke_features.csv`
- Audit: `Newtest/Breaker_Based/signal_model_v2/audits/v2_live_features_360one_with_liquidity_smoke_audit.json`
- Summary: `Newtest/Breaker_Based/signal_model_v2/reports/v2_live_features_360one_with_liquidity_smoke_feature_parity_summary.csv`
- Rows: `2`
- Approved long model required features: `679`
- Available on all rows: `248`
- Missing on all rows: `378`
- Classification status: `insufficient_data`

Feature group coverage from the latest smoke:

| Group | Required | Available All Rows | Partial | Missing All Rows |
|---|---:|---:|---:|---:|
| base_signal_setup | 65 | 49 | 7 | 9 |
| candle_quality | 29 | 28 | 0 | 1 |
| entry | 11 | 9 | 0 | 2 |
| fvg_reaction | 29 | 11 | 17 | 1 |
| macro | 44 | 11 | 0 | 33 |
| technical | 153 | 96 | 4 | 53 |
| focused_quality | 79 | 4 | 4 | 71 |
| liquidity | 17 | 12 | 5 | 0 |
| topology | 212 | 28 | 16 | 168 |
| composite | 40 | 0 | 0 | 40 |

Interpretation:

- V2 no longer has to rely only on precomputed V1 feature-complete rows for base/candle/technical/FVG feature creation.
- V2 can score visible decision-time liquidity candidates and merge a subset of scored liquidity/topology fields into signal rows.
- V2 still cannot classify a fresh signal natively because focused quality, composite, full topology, and broader macro/context groups remain incomplete.
- This is correct behavior: partial live rows are classified as `insufficient_data`, not force-scored.

### Decision-Time Liquidity Scoring

V2 can now build decision-time liquidity payloads from detected events, extract visible candidate levels, validate the full 391-feature liquidity contract, score candidates with the approved XG liquidity model, and aggregate scores back to signal-level features.

Latest verified liquidity smoke:

- Payload builder: `Newtest/Breaker_Based/signal_model_v2/scripts/v2_build_liquidity_payloads_from_events.py`
- Payload audit: `Newtest/Breaker_Based/signal_model_v2/audits/v2_liquidity_payloads_360one_smoke_audit.json`
- Candidate builder audit: `Newtest/Breaker_Based/signal_model_v2/audits/v2_liquidity_candidates_360one_smoke_audit.json`
- Scoring audit: `Newtest/Breaker_Based/signal_model_v2/audits/v2_liquidity_scores_360one_smoke_audit.json`
- Aggregation audit: `Newtest/Breaker_Based/signal_model_v2/audits/v2_liquidity_aggregation_360one_smoke_audit.json`
- Event rows: `2`
- Decision-time payloads written: `2`
- Visible candidate rows: `7`
- Candidate feature validation: `passed`
- Liquidity feature count: `391`
- XG score range: `45.60` to `70.83`
- Aggregated signal rows: `2`
- Signals missing candidates: `0`

### Decision Gate

V2 can now convert feature rows into standardized decision rows. Since the current native feature rows are incomplete, the gate correctly outputs `insufficient_data` rather than force-scoring the signal model.

Latest verified decision gate:

- Script: `Newtest/Breaker_Based/signal_model_v2/scripts/v2_apply_signal_decision_gate.py`
- Input features: `Newtest/Breaker_Based/signal_model_v2/data/features/v2_live_features_360one_with_liquidity_smoke_features.csv`
- Input feature audit: `Newtest/Breaker_Based/signal_model_v2/audits/v2_live_features_360one_with_liquidity_smoke_audit.json`
- Output decisions: `Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_decision_gate_360one_with_liquidity_smoke_decisions.csv`
- Audit: `Newtest/Breaker_Based/signal_model_v2/audits/v2_decision_gate_360one_with_liquidity_smoke_audit.json`
- Input rows: `2`
- Output rows: `2`
- Bucket counts:
  - `insufficient_data`: `2`
- Order placement: disabled

### Signal Model Inference

V2 now has a reusable inference wrapper:

- Script: `Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_signal_inference.py`
- Feature-complete path: approved V1 signal artifact scoring plus approved live-fixed decision thresholds.
- Incomplete-row path: `insufficient_data` gate.
- Order placement: disabled.

Latest verified feature-complete inference smoke:

- Report: `Newtest/Breaker_Based/signal_model_v2/reports/v2_signal_inference_feature_complete_smoke_report.md`
- Audit: `Newtest/Breaker_Based/signal_model_v2/audits/v2_signal_inference_feature_complete_smoke_audit.json`
- Input rows: `3`
- Inference path: `approved_artifact_scoring`
- Long scored rows: `3`
- Bucket counts:
  - `ultra_high_conviction`: `1`
  - `high_conviction`: `1`
  - `neutral_no_edge`: `1`

Latest verified incomplete native inference smoke:

- Report: `Newtest/Breaker_Based/signal_model_v2/reports/v2_signal_inference_incomplete_native_smoke_report.md`
- Audit: `Newtest/Breaker_Based/signal_model_v2/audits/v2_signal_inference_incomplete_native_smoke_audit.json`
- Input rows: `2`
- Inference path: `insufficient_data_gate`
- Missing required features on all rows: `378`
- Bucket counts:
  - `insufficient_data`: `2`

### Replay Smoke Orchestrator

V2 now has a one-command replay wrapper for the current candle-to-decision path:

- Script: `Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_replay_smoke_pipeline.py`
- Purpose: execute signal detection, decision-time liquidity payload/candidate/scoring/aggregation, feature build, and decision gate in sequence.
- Output: one run-level log, audit, report, and per-step artifacts.
- Production status: replay smoke only.

Latest verified orchestrated replay:

- Run ID: `v2_replay_smoke_360one_latest`
- Report: `Newtest/Breaker_Based/signal_model_v2/reports/v2_replay_smoke_360one_latest_report.md`
- Audit: `Newtest/Breaker_Based/signal_model_v2/audits/v2_replay_smoke_360one_latest_audit.json`
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

Latest verified orchestrated replay from clean ingested candle output using macro + payload technical context:

- Run ID: `v2_replay_smoke_360one_payload_macro_latest`
- Input candles: `Newtest/Breaker_Based/signal_model_v2/data/raw/v2_candle_ingest_360one_local_latest/360ONE.NS_1h.csv`
- Report: `Newtest/Breaker_Based/signal_model_v2/reports/v2_replay_smoke_360one_payload_macro_latest_report.md`
- Audit: `Newtest/Breaker_Based/signal_model_v2/audits/v2_replay_smoke_360one_payload_macro_latest_audit.json`
- Signal rows: `2`
- Liquidity candidate rows: `7`
- Scored liquidity rows: `7`
- Aggregated signal rows: `2`
- Feature rows: `2`
- Decision rows: `2`
- Feature coverage: `474 / 679`
- Missing required features on all rows: `138`
- Signal inference path: `insufficient_data_gate`
- Decision bucket:
  - `insufficient_data`: `2`
- Observed bottleneck: approved liquidity scoring remains the slow step; this full wrapper run took about `163` seconds end to end on the 2-row smoke.

### Bridge Smoke

Mode:

`bridge_smoke`

This mode consumes standardized V1 signal event rows and calls approved V1 artifact-backed scripts from inside the V2 boundary:

1. Build feature-complete inference rows.
2. Score long/short signal model artifacts.
3. Apply trade decision buckets.
4. Write V2 logs, audits, reports, and prediction outputs.

Latest verified smoke:

- Report: `Newtest/Breaker_Based/signal_model_v2/reports/v2_bridge_smoke_20260704T202813Z_report.md`
- Audit: `Newtest/Breaker_Based/signal_model_v2/audits/v2_bridge_smoke_20260704T202813Z_audit.json`
- Rows: `3`
- Buckets:
  - `ultra_high_conviction`: 1
  - `high_conviction`: 1
  - `neutral_no_edge`: 1

## What Does Not Work Yet

V2 is not yet a complete fresh-candle production system.

Still required:

- Production-scale fresh candle ingestion across the configured universe, with retry/chunk-stitching burn-in.
- Full native V2 Breaker+FVG short-side downstream parity: feature generation, approved short artifact inference, dashboard bridge, and paper replay.
- Full decision-time feature generation for every approved model feature.
- Batch/live-scale visible BSL/SSL extraction and XG scoring beyond the current small long/short smoke.
- Full-scale V2-native paper ledger validation across long and short signals.
- Original dashboard consumption of the V2 dashboard/API bridge artifacts.
- Actual AWS build, deployment, S3/CloudWatch validation, and paper-mode burn-in.

## Feature Parity Read

Latest value-level feature audit:

- Audit: `Newtest/Breaker_Based/signal_model_v2/audits/v2_features_360one_both_side_yfinance_60d_smoke_audit.json`
- Summary: `Newtest/Breaker_Based/signal_model_v2/reports/v2_features_360one_both_side_yfinance_60d_smoke_feature_parity_summary.csv`

Result on the current both-side 360ONE smoke:

| Side | Required features | Available on all rows | Raw missing on all rows | Structural-null missing | Blocking missing | Feature complete | Approved inference contract |
|---|---:|---:|---:|---:|---:|---|---|
| Long | 679 | 521 | 158 | 158 | 0 | true | true |
| Short | 1194 | 844 | 350 | 350 | 0 | true | true |

Interpretation:

- Long feature rows are complete for approved long artifact scoring.
- Short feature rows now have zero blocking-missing fields against the 1194-feature short artifact contract.
- Short signal inference is enabled only when the side-specific feature contract is complete and the approved short artifact/config evidence exists.
- Column presence alone is not used as production proof. The audit checks value-level availability and separates structural nulls from blockers.

## Commands

System audit:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_audit_system.py
```

Static tests:

```powershell
python Newtest/Breaker_Based/signal_model_v2/tests/test_v2_static_contracts.py
```

Bridge smoke:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_bridge_smoke.py --events Newtest/Breaker_Based/signal_model/datasets/live_inbox/trade_system_minimal_events_smoke27.csv --limit 3
```

Local candle ingestion:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_ingest_candles.py --provider local_csv --ticker 360ONE.NS --source-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_import_20260704T203053Z --run-id v2_candle_ingest_360one_local_latest --min-rows 50
```

Raw candle to long signal events:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_detect_signals_from_candles.py --candles Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_import_20260704T203053Z/360ONE.NS_1h_1747637100.csv --ticker 360ONE.NS --run-id v2_signal_detect_360one_smoke
```

Stricter live-confirmation timing smoke:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_detect_signals_from_candles.py --candles Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_import_20260704T203053Z/360ONE.NS_1h_1747637100.csv --ticker 360ONE.NS --run-id v2_signal_detect_360one_next_candle_smoke --decision-time-policy next_candle_after_signal
```

Native partial live feature generation:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_build_live_features_from_events.py --events Newtest/Breaker_Based/signal_model_v2/data/signals/v2_signal_detect_360one_smoke_events.csv --candles Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_import_20260704T203053Z/360ONE.NS_1h_1747637100.csv --run-id v2_live_features_360one_smoke
```

Decision-time liquidity payloads:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_build_liquidity_payloads_from_events.py --events Newtest/Breaker_Based/signal_model_v2/data/signals/v2_signal_detect_360one_smoke_events.csv --candles Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_import_20260704T203053Z/360ONE.NS_1h_1747637100.csv --run-id v2_liquidity_payloads_360one_smoke
```

Approved liquidity candidate rows:

```powershell
python Newtest/Breaker_Based/signal_model/scripts/build_trade_system_decision_time_liquidity_candidates_v1.py --events Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_smoke/events_normalized.csv --manifest Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_smoke/payload_manifest.txt --raw-output Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_smoke/candidates_raw.csv --feature-output Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_smoke/candidates_features.csv --audit Newtest/Breaker_Based/signal_model_v2/audits/v2_liquidity_candidates_360one_smoke_audit.json
```

Approved liquidity XG scoring:

```powershell
python Newtest/Breaker_Based/signal_model/scripts/score_liquidity_decision_time_rows_v1.py Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_smoke/candidates_features.csv --output Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_smoke/candidates_scored.csv --audit Newtest/Breaker_Based/signal_model_v2/audits/v2_liquidity_scores_360one_smoke_audit.json
```

Signal-level liquidity aggregation:

```powershell
python Newtest/Breaker_Based/signal_model/scripts/aggregate_trade_system_liquidity_scores_v1.py --events Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_smoke/events_normalized.csv --scored-candidates Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_smoke/candidates_scored.csv --output Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_smoke/signal_liquidity_aggregation.csv --audit Newtest/Breaker_Based/signal_model_v2/audits/v2_liquidity_aggregation_360one_smoke_audit.json
```

Native features with scored liquidity merged:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_build_live_features_from_events.py --events Newtest/Breaker_Based/signal_model_v2/data/signals/v2_signal_detect_360one_smoke_events.csv --candles Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_import_20260704T203053Z/360ONE.NS_1h_1747637100.csv --liquidity-aggregation Newtest/Breaker_Based/signal_model_v2/data/liquidity/v2_liquidity_payloads_360one_smoke/signal_liquidity_aggregation.csv --run-id v2_live_features_360one_with_liquidity_smoke
```

Decision gate:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_apply_signal_decision_gate.py --features Newtest/Breaker_Based/signal_model_v2/data/features/v2_live_features_360one_with_liquidity_smoke_features.csv --feature-audit Newtest/Breaker_Based/signal_model_v2/audits/v2_live_features_360one_with_liquidity_smoke_audit.json --run-id v2_decision_gate_360one_with_liquidity_smoke
```

Replay smoke pipeline:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_replay_smoke_pipeline.py --candles Newtest/Breaker_Based/signal_model_v2/data/raw/v2_candle_ingest_360one_local_latest/360ONE.NS_1h.csv --ticker 360ONE.NS --run-id v2_replay_smoke_360one_payload_macro_latest --macro-candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_candle_ingest_360one_local_latest
```

AWS readiness audit:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_aws_readiness_audit.py --run-id v2_aws_readiness_current
```

Paper replay ledger smoke:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_paper_replay_from_decisions.py --decisions Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_replay_batch_5ticker_smoke_current_360one_ns_decisions.csv Newtest/Breaker_Based/signal_model_v2/data/predictions/v2_replay_batch_5ticker_smoke_current_aartiind_ns_decisions.csv --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_batch_import_5ticker_smoke --run-id v2_paper_replay_5ticker_smoke_current --notional-capital-inr 1000000
```

Runtime cycle using existing replay audit:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_runtime_cycle.py --replay-run-id v2_replay_batch_5ticker_smoke_current --candles-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_batch_import_5ticker_smoke --run-id v2_runtime_cycle_5ticker_smoke_current_reuse
```

Live-disabled local paper cycle:

```powershell
python Newtest/Breaker_Based/signal_model_v2/scripts/v2_run_live_paper_cycle.py --provider local_csv --ticker 360ONE.NS --source-dir Newtest/Breaker_Based/signal_model_v2/data/raw/v2_payload_batch_import_5ticker_smoke --run-id v2_live_paper_cycle_360one_local_smoke
```

## Production Readiness Gate

Do not call V2 production-ready until:

1. Fresh/replay candle input produces standardized Breaker+FVG signal events for both sides.
2. Those signal events generate decision-time features.
3. Visible liquidity levels are extracted and scored with the approved XG liquidity model.
4. Scored liquidity is aggregated into signal-level features.
5. Approved signal models classify rows into decision buckets.
6. Outputs include dashboard/trade-review fields.
7. Smoke tests pass on at least one small ticker batch.
8. Feature parity/missingness is explicitly reported.
9. Logs and audits are written.
10. Original engine/dashboard files remain untouched unless explicitly approved.
