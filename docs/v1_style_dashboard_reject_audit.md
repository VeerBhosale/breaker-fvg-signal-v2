# V1-Style Dashboard And Reject Audit

Date: 2026-07-06

## Scope

This audit covers the hosted V2 V1-style dashboard at:

`site/v1-style/index.html`

The original Breaker+FVG dashboard code was used only as source/reference. No original engine or original dashboard file was edited.

## Dashboard Style Correction

The separate V1-style dashboard now follows the original dashboard shell more closely:

- 3-panel fixed layout: watchlist, chart, right signal details.
- Collapsible left and right rails.
- Original-style toolbar controls: `ISL/ISH`, `Ranges`, `Structure`, `Skeleton`, `Fit`, `Last signal`.
- Original-style right panel sections: selected signal, trade ranker, model meter, signals list, scored liquidity list.
- The controls are mapped to the V2 data actually available in `chart_payload.json`:
  - `Skeleton`: signal markers.
  - `Ranges`: entry, stop, 2R.
  - `ISL/ISH`: ranked target/adverse liquidity levels.
  - `Structure`: full scored liquidity context for the selected signal.

## Reject Audit

The hosted chart payload contains:

- Signal rows: `155`
- Tickers with signals: `100`
- Buckets: `reject = 155`
- Permissions: `no = 155`
- Public `model_score`: `0.0` for all 155 rows
- Reason: `score<=0.0850852` for all 155 rows

This is not a dashboard rendering issue.

The source V2 scoring audit shows:

- Long rows scored: `155`
- Short rows scored: `0`
- Long main gate pass rows: `0`
- Long strict gate pass rows: `0`

The approved V1 long signal scorer writes the public `score` as `0.0000000000` when the model row does not pass the artifact main gate. Because none of the fresh hosted rows passed the main gate, every row was classified as `reject` by the decision layer.

## Main Gate Thresholds

The approved long artifact main gate requires:

- `dt_liq_target_path_pressure >= 65.3480985`
- `fvg_react_quality_composite >= 0.160908`
- `foldrisk_bull_stack_pressure <= 0.56`
- `dt_liq_swept_clusters_taken_during_signal_proxy_sum <= 35.288898`
- `access_net_pressure_per_total_distance >= 5.9804278286444745`

The current hosted payload does not expose raw ungated model probability, only the gated final decision score.

## Interpretation

The dashboard is correctly showing final trade permission as `reject` for all visible signals in this hosted payload.

What was missing before this audit:

- The dashboard did not explain that the zero scores were gate-suppressed scores.
- The right panel made the all-reject state look like a UI bug.

The V1-style page now shows an audit note in the trade ranker panel whenever the hosted payload is fully rejected.

## Follow-Up If Needed

If we want the dashboard to show more than final permission, the next clean addition is to export diagnostic fields:

- raw ungated XGBoost probability
- main gate pass/fail
- per-gate feature values
- per-gate threshold deltas

Those should be exported as diagnostic-only fields and must not be confused with final entry permission.
