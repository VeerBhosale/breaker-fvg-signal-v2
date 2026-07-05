# Signal System V2 Architecture

## Purpose

V2 turns fresh or replayed 1H NSE candles into ranked Breaker+FVG trade decisions.

The decision question is:

> Given this newly detected Breaker+FVG setup, should it be entered or reviewed because price is statistically likely to travel into meaningful target-side liquidity?

## End-To-End Flow

1. Ingest 1H candles for the configured universe.
2. Normalize timezone, remove duplicate candles, and audit gaps.
3. Detect Breaker+FVG signal events using a V2 port/wrapper of the original engine logic.
4. Build standardized signal event rows.
5. Generate decision-time-only features.
6. Extract visible BSL/SSL levels at decision time.
7. Score each visible level with the approved XG liquidity model.
8. Aggregate scored levels into signal-level topology/liquidity features.
9. Load approved long/short signal artifacts.
10. Classify into `ultra_high_conviction`, `high_conviction`, `neutral_no_edge`, `reject`, or `insufficient_data`.
11. Write dashboard/API outputs plus paper/live decision rows.

## Current Implementation Status

V2 has been initialized as a separate production boundary.

Current runnable mode:

- `bridge_smoke`: consumes existing V1 smoke event rows and invokes approved V1 artifact-backed scripts from the V2 boundary.

Not yet complete:

- raw-candle V2 signal detector parity
- fully live feature generation for every long-model feature group
- AWS deployment execution
- real broker integration

## Non-Negotiables

- Do not edit the original Breaker+FVG engine/dashboard files.
- Do not use post-entry outcomes as features.
- Do not use post-decision liquidity/structure as features.
- Do not force-score incomplete live rows.
- Keep long and short model evidence separately auditable.

