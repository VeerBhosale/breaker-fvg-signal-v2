# V2 Feature Parity Plan

## Objective

Make live-generated features match the historical model feature definitions closely enough that approved model artifacts remain valid.

## Required Audit

For each model feature:

- available live
- unavailable live
- substituted live-safe equivalent
- removed from production contract
- missingness rate
- source script
- decision-time cutoff proof

## Current Known Gap

Existing V1 status says these groups are automated for scoped events:

- decision-time liquidity/topology
- macro context
- technical context
- entry quality
- phase quality

V2 current smoke status after the scored-candidate topology and feature availability policy patch:

- macro context is populated for the smoke (`44 / 44` all-row coverage), but the smoke uses a singleton candle directory. Production validation still needs a full configured universe candle directory.
- technical context is mostly populated from decision-time payload JSON (`148 / 153` all-row coverage).
- focused quality and composites are mostly reproduced from decision-time phase/liquidity features.
- scored-candidate topology is now rebuilt from the decision-time `candidates_scored.csv` rows.
- topology improved to `75 / 212` all-row coverage and `62 / 212` partial-row coverage in the 2-signal 360ONE smoke.
- the remaining all-row missing fields are now classified by `configs/v2_feature_availability_policy.json`.
- the policy allows approved artifact scoring only when every all-row missing feature is a valid structural null.

Latest verification:

- Replay run: `v2_replay_smoke_360one_policy_gate_final`
- Feature coverage: `501 / 679` available on all rows
- Partial-row feature coverage: `102 / 679`
- Missing required features on all rows: `76`
- Structural-null missing features on all rows: `76`
- Blocking missing features on all rows: `0`
- Liquidity scored candidate rows used: `7`
- Signal inference path: `approved_artifact_scoring`
- Decision buckets: `reject: 2`

Remaining work:

- validate the structural-null policy on a larger multi-ticker replay, not just the 2-row 360ONE smoke
- build true live liquidity percentile support from a calibrated reference distribution; current candidate percentile is the scorer's batch percentile
- decide whether `current_member_age_bars_mean` is the final approved live-safe substitute for `topo_*_age_bars`
- expand short-side broad validation beyond the current one-ticker artifact-scoring smoke
- add paper/replay ledger validation over more than one ticker with actual permissioned entries
- keep dashboard/API bridge audits current as native decision rows evolve

V2 must not mark live production ready until the feature parity audit proves those gaps are closed or intentionally removed.
