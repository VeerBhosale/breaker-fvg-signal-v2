# V2 Live Runtime Flow

## Live Disabled By Default

V2 may run in paper/manual-review mode. Real order placement is disabled unless explicitly implemented later behind config gates.

## Runtime Steps

1. Fetch latest 1H candles.
2. Write raw candle files.
3. Audit duplicates, timezone, gaps, and missing values.
4. Detect new signal events.
   - Current verified implementation: long-side replay through `v2_detect_signals_from_candles.py`.
   - The script uses the original `breaker_fvg_dashboard_export.py` as source/reference and calls `analyze_ticker`.
   - It writes V2-owned standardized signal events and an audit JSON.
   - Short-side signal detection is not yet verified in V2.
5. Generate feature-source files with `feature_cutoff_time <= decision_time`.
6. Score visible liquidity levels.
7. Build feature-complete inference rows.
8. Validate required fields.
9. Score long/short signal artifacts.
10. Apply decision buckets and execution gates.
11. Write dashboard/API files.
12. Append paper ledger rows.

## Logging Requirement

Every long run must write JSONL logs containing:

- run_id
- ticker
- phase
- processed counts
- elapsed seconds
- errors
- retry counts
- output paths

No V2 process should run silently for long periods.

## Signal Detection Timing Policy

The original reference detector uses the next candle to confirm swing highs and has `FVG_CONFIRM_AFTER_SIGNAL_CANDLES = 1`.

V2 therefore exposes two explicit policies:

- `signal_time`: preserves original dashboard-compatible timestamps.
- `next_candle_after_signal`: shifts the decision time one 1H candle forward for stricter live-confirmation review.

Production order logic must not be enabled until the chosen policy is validated against the signal model feature contract and paper-test ledger.
