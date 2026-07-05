# V1-Style Dashboard Marking And Reject Rerun Audit

Date: 2026-07-06

## Scope

This audit covers the V2 standalone V1-style dashboard and the fresh original-universe signal decision payload.

Original Breaker+FVG dashboard/engine files were used only as source reference. They were not edited.

## Dashboard Marking Changes

Implemented:

- BSL/SSL structure markings now use original-style `level_ledgers`, `liquidity_clusters`, and `merged_fvg_zones`.
- FVG text markers and FVG edge line markings were removed from the chart.
- FVGs are kept as boxes only.
- The older simplified scored-liquidity chart line overlays were removed from the chart path.
- Entry, stop, and 2R are no longer full-chart horizontal lines.
- Entry, stop, and 2R now render from the first candle after signal generation for 14 candles.
- Right panel now displays rejection reasoning, including raw score, final gated score, main gate status, strict gate status, and exact failed gate conditions.

## Browser-Level Validation

Validated with a browser runtime check using a mocked Lightweight Charts API so the dashboard JavaScript path executes locally without CDN/network dependency.

Example ticker: `TVSMOTOR.NS`

- Signal markers: `2`
- Visible SSL cluster bands: `9`
- Visible BSL cluster bands: `16`
- Structure line series: `8`
- Structure/FVG canvases: `10`
- FVG text marker present: `false`
- Entry segment points: `2`
- Stop segment points: `2`
- 2R segment points: `2`
- Rejection reasoning visible: `true`
- Gate failure text visible: `true`

## Signal Scoring Rerun / Audit

Source diagnostic decision file:

`data/predictions/v2_replay_original_fresh_178_tail300_parallel_burnin_decisions_raw_diagnostics.csv`

Rows:

- Total signals: `155`
- Long rows: `155`
- Rows scored by model artifacts: `155`
- `signal_model_score_ready=true`: `155`
- `signal_model_score_source=model_artifacts`: `155`
- Missing score fields: `0`

Raw ungated model score:

- Count: `155`
- Minimum: `0.0156463366`
- Maximum: `0.8698372841`
- Mean: `0.2137101556`

Final decision:

- `reject`: `155`
- `permission=no`: `155`
- Public/final score: `0.0` for all rows

Gate outcome:

- Main gate pass: `0 / 155`
- Strict gate pass: `0 / 155`
- Score gate suppressed: `155 / 155`

## Interpretation

The all-reject dashboard result is not caused by missing scoring features or absent model output.

The rows were feature-complete enough to score:

- every row produced an ungated raw model score,
- no score fields were missing,
- all rows used the model artifact scorer.

The all-reject result is caused by the approved long entry process main gate. Every fresh hosted row failed at least one main-gate condition, so the public final score was suppressed to `0.0`, and the trade decision layer classified every row as `reject`.

This is a process/gate strictness result, not a dashboard display bug.

## Example Rejection Detail

For `ABB.NS|long|1781235900|5240cafbb1be`:

- Raw score: `0.1708523333`
- Final score: `0.0`
- Main gate pass: `false`
- Main gate failures:
  - `dt_liq_target_path_pressure=53.3388 needs >=65.3481`
  - `foldrisk_bull_stack_pressure=0.63 needs <=0.56`
  - `dt_liq_swept_clusters_taken_during_signal_proxy_sum=97.946 needs <=35.2889`
  - `access_net_pressure_per_total_distance=-5.10184 needs >=5.98043`

## Remaining Model Question

The evidence shows the current approved gate is extremely strict on this fresh hosted run. If this is not desirable, the next model work should not treat the dashboard as broken. It should test whether the approved entry gate thresholds are too strict for live/fresh original-universe replay, or whether the current signal candidates genuinely do not match the original promoted high-conviction regime.
