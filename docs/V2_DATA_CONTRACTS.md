# V2 Data Contracts

## Raw Candle Row

Canonical V2 candle files are CSVs under `data/raw/<run_id>/` with one row per candle:

- `time`: Unix epoch seconds in UTC.
- `open`
- `high`
- `low`
- `close`

Required invariants:

- Rows are sorted by `time`.
- `time` is unique per ticker file.
- OHLC values are numeric and positive.
- `high >= low`.
- Corrupt/unparseable rows are dropped and counted in the ingestion audit.
- Large market-session gaps are reported but do not automatically fail the audit, because NSE hourly data naturally has overnight/weekend gaps.

The ingestion audit must report:

- provider
- source path or provider parameters
- row counts before/after normalization
- duplicate time rows removed
- null/unparseable rows dropped
- invalid OHLC rows dropped
- first/last timestamp
- large gap count
- per-ticker pass/fail status

## Signal Event Row

Minimum fields:

- `signal_id`
- `candidate_row_id`
- `ticker`
- `side`
- `direction`
- `decision_time`
- `signal_time`
- `signal_timestamp`
- `entry_model_variant`

Recommended fields:

- `entry_price`
- `stop_price`
- `risk`
- `target_1r`
- `target_2r`
- `feature_cutoff_time`
- `source_payload_path`
- `source_engine_version`

Long-side reference fields:

- `t1_sweep_low_price`
- `t2_high_price`
- `t3_low_price`
- `signal_high_price`
- `bull_fvg_lower_price`
- `bull_fvg_upper_price`

Short-side reference fields:

- `t1_sweep_high_price`
- `t2_low_price`
- `t3_high_price`
- `signal_low_price`
- `bear_fvg_lower_price`
- `bear_fvg_upper_price`

Short signal detection currently uses a V2-owned price-reflection wrapper around the unchanged long reference detector. Rows emitted by that path set `mirror_transform_applied=true`.

## Liquidity Level Row

One row per visible BSL/SSL level at decision time:

- `signal_id`
- `ticker`
- `decision_time`
- `level_id`
- `side`
- `price`
- `distance_atr`
- `level_type`
- `cluster_id`
- `cluster_order`
- `age_bars`
- `touch_count`
- `xg_liquidity_score`
- `score_percentile`
- `score_decile`

## Signal-Level Scored Topology Row

Aggregated from scored liquidity rows:

- nearest target-side score/distance
- second target-side score/distance
- target-side score sum/max/mean
- stop-side score sum/max/mean
- target-minus-stop pressure
- pair density / stack quality
- distance-weighted score metrics

## Prediction Row

Required output fields:

- `signal_id`
- `ticker`
- `side`
- `decision_time`
- `bucket`
- `permission`
- `model_score`
- `entry`
- `stop`
- `risk`
- `target_liquidity_levels`
- `reason_codes`
- `missing_feature_count`
- `status`
