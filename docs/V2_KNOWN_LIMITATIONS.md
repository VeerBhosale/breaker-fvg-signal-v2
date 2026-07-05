# V2 Known Limitations

Last updated: 2026-07-05

## Current Working State

- V2 is isolated under `Newtest/Breaker_Based/signal_model_v2`.
- Original Breaker+FVG engine/dashboard files are source/reference only and are not edited by V2 scripts.
- Fresh yfinance ingestion is proven on the deduped original universe:
  - `178` tickers attempted.
  - `178` passed.
  - `0` failed.
- Full-original latest-window replay is proven:
  - `178` tickers attempted.
  - `100` tickers produced decisions.
  - `78` tickers had no current-window signal.
  - `0` failed.
  - `155` signal/decision rows.
  - `824` decision-time XG-scored liquidity rows.
- Feature broad-validation passes on the full-original latest-window replay:
  - `100` feature audits.
  - `155` feature rows.
  - `1.0` classification-allowed rate.
  - `0` blocking-missing tickers.
- Dashboard bridge output passes contract for `155/155` rows.
- Paper replay is live-order safe:
  - Real order placement disabled.
  - Uses only post-decision candles.
  - Full-fresh run entered `0` trades because all current decisions were `reject`.
- AWS packaging skeleton passes local readiness checks.

## Current Limitations

- The full-original validation is a latest-window runtime validation, not a full chronological edge backtest.
- The latest full-original fresh replay produced only `reject` decisions, so it proves a no-trade live state, not current market opportunity.
- The corrected 25-ticker bounded validation is still the latest run with an actual conditional entry:
  - `1` conditional high-conviction paper entry.
  - `18070.92` INR simulated PnL on `10L` notional.
  - This is useful runtime evidence, not statistical edge proof.
- Short-side support is smoke-tested but not yet validated at the same breadth as the long full-original latest-window replay.
- The original chart dashboard has an additive V2 bridge output path, but direct production chart-panel deployment remains separate from the V2 data contract.
- AWS infrastructure has not been created or validated in a real AWS account.
- Real order placement is intentionally disabled and has no approval gate implemented.
- The original engine/dashboard tracked files are dirty in the current worktree, so a clean-source baseline cannot be proven from git status.

## Non-Negotiable Safety Rules

- No feature may use candles, labels, liquidity, or structure created after `decision_time`.
- Rows missing required live features must become `insufficient_data`; they must not be force-scored.
- Paper/live execution must remain disabled unless explicitly approved.
- AWS deployment must not be performed without explicit approval.
