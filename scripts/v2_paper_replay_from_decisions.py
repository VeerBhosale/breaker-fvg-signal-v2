from __future__ import annotations

import argparse
import glob
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from v2_common import V2_ROOT, append_jsonl, ensure_dirs, read_csv, rel, utc_stamp, write_csv, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replay V2 decision rows against post-decision candles. This produces a conservative paper ledger; "
            "it does not place orders."
        )
    )
    parser.add_argument("--decisions", nargs="+", required=True, help="Decision CSV path(s) or glob pattern(s).")
    parser.add_argument("--candles-dir", type=Path, required=True, help="Directory containing <ticker>_1h.csv files.")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--entry-policy", choices=["next_touch", "next_open"], default="next_touch")
    parser.add_argument("--max-hold-bars", type=int, default=120)
    parser.add_argument("--notional-capital-inr", type=float, default=1_000_000.0)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    parser.add_argument("--brokerage-bps", type=float, default=0.0)
    parser.add_argument(
        "--entry-permission-values",
        nargs="+",
        default=["yes"],
        help=(
            "Permission values eligible for paper entry. Default is strict yes only. "
            "Use explicit values such as conditional_take_candidate for validation runs."
        ),
    )
    parser.add_argument(
        "--include-non-permission-decisions",
        action="store_true",
        help="Diagnostic mode only: simulate all rows regardless of entry-permission-values.",
    )
    return parser.parse_args()


def resolve_paths(items: Sequence[str]) -> List[Path]:
    paths: List[Path] = []
    for item in items:
        matches = [Path(match) for match in glob.glob(item)]
        paths.extend(matches if matches else [Path(item)])
    seen: set[str] = set()
    result: List[Path] = []
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def to_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    number = to_float(value)
    if number is None:
        return None
    return int(number)


def clean_text(value: Any) -> str:
    return "" if value is None else str(value)


def first_present(row: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in ("", None):
            return str(value)
    return ""


def load_decisions(paths: Iterable[Path]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in paths:
        for row in read_csv(path):
            row["_source_decision_file"] = rel(path)
            rows.append(row)
    return rows


def candle_path_for_ticker(candles_dir: Path, ticker: str) -> Path | None:
    exact = candles_dir / f"{ticker}_1h.csv"
    if exact.exists():
        return exact
    matches = list(candles_dir.glob(f"{ticker}*.csv"))
    if matches:
        return matches[0]
    safe = ticker.replace(".", "_").lower()
    for path in candles_dir.glob("*.csv"):
        if path.stem.replace(".", "_").lower().startswith(safe):
            return path
    return None


def load_candles(path: Path) -> List[Dict[str, float]]:
    candles: List[Dict[str, float]] = []
    for row in read_csv(path):
        time_value = to_int(row.get("time"))
        open_value = to_float(row.get("open"))
        high_value = to_float(row.get("high"))
        low_value = to_float(row.get("low"))
        close_value = to_float(row.get("close"))
        if None in (time_value, open_value, high_value, low_value, close_value):
            continue
        if high_value < low_value:
            continue
        candles.append(
            {
                "time": float(time_value),
                "open": float(open_value),
                "high": float(high_value),
                "low": float(low_value),
                "close": float(close_value),
            }
        )
    candles.sort(key=lambda item: item["time"])
    deduped: List[Dict[str, float]] = []
    seen: set[float] = set()
    for candle in candles:
        if candle["time"] in seen:
            continue
        seen.add(candle["time"])
        deduped.append(candle)
    return deduped


def side_from_row(row: Dict[str, Any]) -> str:
    return first_present(row, "direction", "side", "tds_side").lower() or "long"


def bucket_from_row(row: Dict[str, Any]) -> str:
    return first_present(row, "bucket", "tds_decision_class", "decision_class") or "unknown"


def permission_from_row(row: Dict[str, Any]) -> str:
    value = first_present(row, "tds_entry_permission", "entry_permission", "permission")
    text = value.strip().lower()
    if text in {"yes", "allow", "trade", "take_trade", "entry_yes"}:
        return "yes"
    if text in {"no", "reject", "skip", "skip_no_trade", "skip_reject"}:
        return "no"
    return text or "unknown"


def normalize_permission_value(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"allow", "trade", "take_trade", "entry_yes"}:
        return "yes"
    if text in {"reject", "skip", "skip_no_trade", "skip_reject"}:
        return "no"
    return text or "unknown"


def target_levels(row: Dict[str, Any], direction: str, entry: float) -> List[Dict[str, Any]]:
    levels: List[Dict[str, Any]] = []
    for idx in range(1, 6):
        price = to_float(row.get(f"dt_target_liquidity_{idx}_midpoint"))
        score = to_float(row.get(f"dt_target_liquidity_{idx}_score"))
        side = row.get(f"dt_target_liquidity_{idx}_side") or ""
        pool_id = row.get(f"dt_target_liquidity_{idx}_pool_id") or ""
        distance_atr = to_float(row.get(f"dt_target_liquidity_{idx}_distance_atr"))
        if price is None:
            continue
        if direction == "short" and price >= entry:
            continue
        if direction != "short" and price <= entry:
            continue
        levels.append(
            {
                "rank": idx,
                "price": price,
                "score": score if score is not None else 0.0,
                "side": side,
                "pool_id": pool_id,
                "distance_atr": distance_atr,
            }
        )
    reverse = direction == "short"
    levels.sort(key=lambda item: item["price"], reverse=reverse)
    return levels


def favorable_r(direction: str, entry: float, risk: float, candle: Dict[str, float]) -> float:
    if direction == "short":
        return (entry - candle["low"]) / risk
    return (candle["high"] - entry) / risk


def adverse_r(direction: str, entry: float, risk: float, candle: Dict[str, float]) -> float:
    if direction == "short":
        return (entry - candle["high"]) / risk
    return (candle["low"] - entry) / risk


def price_hit(direction: str, candle: Dict[str, float], price: float) -> bool:
    if direction == "short":
        return candle["low"] <= price
    return candle["high"] >= price


def stop_hit(direction: str, candle: Dict[str, float], stop: float) -> bool:
    if direction == "short":
        return candle["high"] >= stop
    return candle["low"] <= stop


def exit_r(direction: str, entry: float, risk: float, exit_price: float) -> float:
    if direction == "short":
        return (entry - exit_price) / risk
    return (exit_price - entry) / risk


def slippage_adjusted_entry(direction: str, entry: float, slippage_bps: float) -> float:
    adjustment = entry * slippage_bps / 10000.0
    return entry - adjustment if direction == "short" else entry + adjustment


def slippage_adjusted_exit(direction: str, exit_price: float, slippage_bps: float) -> float:
    adjustment = exit_price * slippage_bps / 10000.0
    return exit_price + adjustment if direction == "short" else exit_price - adjustment


def find_entry_index(
    future_candles: List[Dict[str, float]], direction: str, entry: float, policy: str
) -> Tuple[int | None, float | None]:
    if not future_candles:
        return None, None
    if policy == "next_open":
        return 0, future_candles[0]["open"]
    for idx, candle in enumerate(future_candles):
        if candle["low"] <= entry <= candle["high"]:
            return idx, entry
    return None, None


def replay_one(
    row: Dict[str, Any],
    candles: List[Dict[str, float]],
    entry_policy: str,
    max_hold_bars: int,
    notional: float,
    slippage_bps: float,
    brokerage_bps: float,
    entry_permission_values: set[str],
    include_non_permission_decisions: bool,
) -> Dict[str, Any]:
    signal_id = row.get("signal_id") or row.get("candidate_row_id") or ""
    ticker = row.get("ticker") or ""
    direction = "short" if side_from_row(row) == "short" else "long"
    decision_time = to_int(row.get("decision_time"))
    raw_entry = to_float(first_present(row, "entry_price", "entry_variant_entry_price"))
    stop = to_float(first_present(row, "stop_price", "entry_variant_stop_price"))
    risk = to_float(first_present(row, "risk", "entry_variant_risk"))
    bucket = bucket_from_row(row)
    permission = permission_from_row(row)

    base = {
        "paper_trade_id": f"{signal_id}|paper_v1",
        "signal_id": signal_id,
        "ticker": ticker,
        "direction": direction,
        "decision_time": decision_time if decision_time is not None else "",
        "bucket": bucket,
        "permission": permission,
        "source_decision_file": row.get("_source_decision_file", ""),
        "entry_policy": entry_policy,
        "intrabar_policy": "conservative_stop_first",
        "max_hold_bars": max_hold_bars,
        "notional_per_trade_inr": round(notional, 2),
        "slippage_bps": slippage_bps,
        "brokerage_bps": brokerage_bps,
        "lot_size_available": False,
        "margin_proxy_available": False,
    }

    if decision_time is None or raw_entry is None or stop is None or risk is None or risk <= 0:
        return {
            **base,
            "entry_status": "invalid_decision_row",
            "exit_reason": "missing_or_invalid_decision_time_entry_stop_risk",
            "final_r": "",
            "gross_pnl_inr": 0.0,
        }

    if permission not in entry_permission_values and not include_non_permission_decisions:
        return {
            **base,
            "entry_status": "not_permissioned",
            "entry_price": raw_entry,
            "stop_price": stop,
            "initial_risk": risk,
            "exit_reason": "permission_not_entry_eligible",
            "final_r": 0.0,
            "gross_pnl_inr": 0.0,
            "net_pnl_inr": 0.0,
        }

    entry = slippage_adjusted_entry(direction, raw_entry, slippage_bps)
    future = [candle for candle in candles if candle["time"] > decision_time]
    if max_hold_bars > 0:
        future = future[:max_hold_bars]
    entry_index, fill_price = find_entry_index(future, direction, entry, entry_policy)
    targets = target_levels(row, direction, entry)

    target_1r = entry - risk if direction == "short" else entry + risk
    target_2r = entry - 2 * risk if direction == "short" else entry + 2 * risk

    if entry_index is None or fill_price is None:
        return {
            **base,
            "entry_status": "no_entry",
            "entry_price": round(entry, 6),
            "raw_entry_price": raw_entry,
            "stop_price": stop,
            "initial_risk": risk,
            "target_1r_price": round(target_1r, 6),
            "target_2r_price": round(target_2r, 6),
            "target_side_levels_available": len(targets),
            "target_side_levels_hit": 0,
            "hit_at_least_1_target_liq": False,
            "hit_at_least_2_target_liq": False,
            "exit_reason": "entry_not_touched_after_decision",
            "final_r": 0.0,
            "gross_pnl_inr": 0.0,
        }

    active_stop = stop
    hit_levels: List[Dict[str, Any]] = []
    hit_pool_ids: set[str] = set()
    first_target_hit_time: float | None = None
    first_target_hit_bars: int | None = None
    hit_1r = False
    hit_2r = False
    max_fav = 0.0
    max_adv = 0.0
    exit_price: float | None = None
    exit_time: float | None = None
    exit_offset: int | None = None
    exit_reason = "end_of_window"
    entered_candles = future[entry_index:]

    for offset, candle in enumerate(entered_candles):
        max_fav = max(max_fav, favorable_r(direction, fill_price, risk, candle))
        max_adv = min(max_adv, adverse_r(direction, fill_price, risk, candle))

        if stop_hit(direction, candle, active_stop):
            exit_price = slippage_adjusted_exit(direction, active_stop, slippage_bps)
            exit_time = candle["time"]
            exit_offset = offset
            exit_reason = "stop_or_trailing_stop_hit"
            break

        if price_hit(direction, candle, target_1r):
            hit_1r = True
        if price_hit(direction, candle, target_2r):
            hit_2r = True

        newly_hit: List[Dict[str, Any]] = []
        for level in targets:
            pool_id = str(level.get("pool_id") or level["rank"])
            if pool_id in hit_pool_ids:
                continue
            if price_hit(direction, candle, float(level["price"])):
                hit_pool_ids.add(pool_id)
                newly_hit.append(level)
        if newly_hit:
            if first_target_hit_time is None:
                first_target_hit_time = candle["time"]
                first_target_hit_bars = offset
            hit_levels.extend(newly_hit)
            if direction == "short":
                active_stop = min(active_stop, fill_price)
                if len(hit_levels) >= 2:
                    active_stop = min(active_stop, float(hit_levels[-2]["price"]))
            else:
                active_stop = max(active_stop, fill_price)
                if len(hit_levels) >= 2:
                    active_stop = max(active_stop, float(hit_levels[-2]["price"]))

    if exit_price is None:
        last_candle = entered_candles[-1]
        exit_price = slippage_adjusted_exit(direction, last_candle["close"], slippage_bps)
        exit_time = last_candle["time"]
        exit_offset = len(entered_candles) - 1

    final_r = exit_r(direction, fill_price, risk, exit_price)
    quantity = notional / fill_price if fill_price else 0.0
    gross_pnl = final_r * risk * quantity
    brokerage = notional * brokerage_bps / 10000.0 * 2
    net_pnl = gross_pnl - brokerage
    target_score_sum = sum(float(level.get("score") or 0.0) for level in hit_levels)
    max_target_reached_distance_r = 0.0
    if hit_levels:
        if direction == "short":
            max_target_reached_distance_r = max((fill_price - float(level["price"])) / risk for level in hit_levels)
        else:
            max_target_reached_distance_r = max((float(level["price"]) - fill_price) / risk for level in hit_levels)

    return {
        **base,
        "entry_status": "entered",
        "entry_time": int(entered_candles[0]["time"]),
        "entry_price": round(fill_price, 6),
        "raw_entry_price": raw_entry,
        "stop_price": stop,
        "initial_risk": risk,
        "target_1r_price": round(target_1r, 6),
        "target_2r_price": round(target_2r, 6),
        "hit_1r": hit_1r,
        "hit_2r": hit_2r,
        "target_side_levels_available": len(targets),
        "target_side_levels_hit": len(hit_levels),
        "hit_at_least_1_target_liq": len(hit_levels) >= 1,
        "hit_at_least_2_target_liq": len(hit_levels) >= 2,
        "target_score_sum_hit": round(target_score_sum, 6),
        "first_target_hit_time": int(first_target_hit_time) if first_target_hit_time is not None else "",
        "first_target_hit_bars": first_target_hit_bars if first_target_hit_bars is not None else "",
        "max_target_reached_distance_r": round(max_target_reached_distance_r, 6),
        "exit_time": int(exit_time) if exit_time is not None else "",
        "exit_price": round(exit_price, 6),
        "exit_reason": exit_reason,
        "final_r": round(final_r, 6),
        "max_favorable_r": round(max_fav, 6),
        "max_adverse_r": round(max_adv, 6),
        "bars_held": exit_offset if exit_offset is not None else "",
        "quantity_proxy": round(quantity, 6),
        "gross_pnl_inr": round(gross_pnl, 2),
        "brokerage_proxy_inr": round(brokerage, 2),
        "net_pnl_inr": round(net_pnl, 2),
        "return_on_notional_pct": round((net_pnl / notional) * 100.0, 6) if notional else 0.0,
        "risk_capital_inr": round(risk * quantity, 2),
        "derivatives_contract_pnl_proxy_inr": round(net_pnl, 2),
    }


def grouped_counts(rows: Iterable[Dict[str, Any]], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def rate(rows: List[Dict[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    return sum(1 for row in rows if str(row.get(key)).lower() == "true") / len(rows)


def build_equity_curve(trades: List[Dict[str, Any]], starting_equity: float) -> Tuple[List[Dict[str, Any]], float]:
    entered = [row for row in trades if row.get("entry_status") == "entered"]
    entered.sort(key=lambda row: (to_int(row.get("exit_time")) or 0, row.get("paper_trade_id") or ""))
    curve: List[Dict[str, Any]] = []
    cumulative = 0.0
    peak = starting_equity
    max_drawdown = 0.0
    for idx, row in enumerate(entered, start=1):
        pnl = to_float(row.get("net_pnl_inr")) or 0.0
        cumulative += pnl
        equity = starting_equity + cumulative
        peak = max(peak, equity)
        drawdown = equity - peak
        max_drawdown = min(max_drawdown, drawdown)
        curve.append(
            {
                "sequence": idx,
                "paper_trade_id": row.get("paper_trade_id", ""),
                "signal_id": row.get("signal_id", ""),
                "ticker": row.get("ticker", ""),
                "bucket": row.get("bucket", ""),
                "permission": row.get("permission", ""),
                "exit_time": row.get("exit_time", ""),
                "final_r": row.get("final_r", ""),
                "net_pnl_inr": round(pnl, 2),
                "cumulative_pnl_inr": round(cumulative, 2),
                "equity_inr": round(equity, 2),
                "drawdown_inr": round(drawdown, 2),
            }
        )
    return curve, round(max_drawdown, 2)


def by_bucket_summary(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for bucket in sorted({str(row.get("bucket") or "unknown") for row in trades}):
        subset = [row for row in trades if str(row.get("bucket") or "unknown") == bucket]
        entered = [row for row in subset if row.get("entry_status") == "entered"]
        rows.append(
            {
                "bucket": bucket,
                "decision_rows": len(subset),
                "entered_trades": len(entered),
                "hit_at_least_1_target_liq_rate": round(rate(entered, "hit_at_least_1_target_liq"), 6),
                "hit_at_least_2_target_liq_rate": round(rate(entered, "hit_at_least_2_target_liq"), 6),
                "hit_1r_rate": round(rate(entered, "hit_1r"), 6),
                "hit_2r_rate": round(rate(entered, "hit_2r"), 6),
                "avg_final_r": round(
                    sum(to_float(row.get("final_r")) or 0.0 for row in entered) / len(entered), 6
                )
                if entered
                else 0.0,
                "net_pnl_inr": round(sum(to_float(row.get("net_pnl_inr")) or 0.0 for row in entered), 2),
            }
        )
    return rows


def write_report(path: Path, audit: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = audit["summary"]
    lines = [
        "# V2 Paper Replay Report",
        "",
        f"Run ID: `{audit['run_id']}`",
        f"Generated: `{audit['generated_at']}`",
        "",
        "## Summary",
        "",
        f"- Decision rows read: `{summary['decision_rows_read']}`",
        f"- Entered trades: `{summary['entered_trades']}`",
        f"- No-entry rows: `{summary['no_entry_rows']}`",
        f"- Not-permissioned rows skipped: `{summary['not_permissioned_rows']}`",
        f"- Hit at least 1 target-liquidity level: `{summary['hit_at_least_1_target_liq_rate']}`",
        f"- Hit at least 2 target-liquidity levels: `{summary['hit_at_least_2_target_liq_rate']}`",
        f"- Average final R: `{summary['avg_final_r']}`",
        f"- Net PnL INR: `{summary['net_pnl_inr']}`",
        f"- Ending equity INR: `{summary['ending_equity_inr']}`",
        f"- Max drawdown INR: `{summary['max_drawdown_inr']}`",
        "",
        "## Safety Notes",
        "",
        "- Only candles with `time > decision_time` are used for entry and outcome simulation.",
        "- Intrabar ambiguity is resolved with conservative stop-first ordering.",
        "- This is a paper replay ledger only. It does not place orders.",
        f"- Entry-eligible permission values: `{', '.join(audit.get('entry_permission_values', []))}`.",
        "- By default, only rows with `permission=yes` are simulated as entries unless a run explicitly supplies additional permission values.",
        "- Derivatives PnL is currently a notional proxy unless lot size and margin data are supplied later.",
        "",
        "## By Bucket",
        "",
        "| Bucket | Decisions | Entered | Hit 1 target | Hit 2 targets | Hit 1R | Hit 2R | Avg R | Net PnL |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in audit["by_bucket"]:
        lines.append(
            f"| `{row['bucket']}` | {row['decision_rows']} | {row['entered_trades']} | "
            f"{row['hit_at_least_1_target_liq_rate']} | {row['hit_at_least_2_target_liq_rate']} | "
            f"{row['hit_1r_rate']} | {row['hit_2r_rate']} | {row['avg_final_r']} | {row['net_pnl_inr']} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"v2_paper_replay_{utc_stamp()}"
    ensure_dirs()
    log_path = V2_ROOT / "logs" / f"{run_id}.jsonl"
    trades_path = V2_ROOT / "data" / "paper" / f"{run_id}_trades.csv"
    equity_path = V2_ROOT / "data" / "paper" / f"{run_id}_equity_curve.csv"
    bucket_path = V2_ROOT / "data" / "paper" / f"{run_id}_by_bucket.csv"
    audit_path = V2_ROOT / "audits" / f"{run_id}_audit.json"
    report_path = V2_ROOT / "reports" / f"{run_id}_report.md"

    decision_paths = resolve_paths(args.decisions)
    append_jsonl(
        log_path,
        {
            "ts": utc_stamp(),
            "event": "start",
            "decision_files": [str(path) for path in decision_paths],
            "candles_dir": str(args.candles_dir),
            "entry_policy": args.entry_policy,
            "entry_permission_values": args.entry_permission_values,
        },
    )

    decisions = load_decisions(decision_paths)
    entry_permission_values = {normalize_permission_value(value) for value in args.entry_permission_values}
    candles_by_ticker: Dict[str, List[Dict[str, float]]] = {}
    missing_candles: List[str] = []
    trades: List[Dict[str, Any]] = []

    for idx, row in enumerate(decisions, start=1):
        ticker = row.get("ticker") or ""
        if ticker not in candles_by_ticker:
            path = candle_path_for_ticker(args.candles_dir, ticker)
            if path is None:
                candles_by_ticker[ticker] = []
                missing_candles.append(ticker)
            else:
                candles_by_ticker[ticker] = load_candles(path)
        trade = replay_one(
            row=row,
            candles=candles_by_ticker.get(ticker, []),
            entry_policy=args.entry_policy,
            max_hold_bars=args.max_hold_bars,
            notional=args.notional_capital_inr,
            slippage_bps=args.slippage_bps,
            brokerage_bps=args.brokerage_bps,
            entry_permission_values=entry_permission_values,
            include_non_permission_decisions=args.include_non_permission_decisions,
        )
        if not candles_by_ticker.get(ticker):
            trade["entry_status"] = "missing_candles"
            trade["exit_reason"] = "missing_candle_file_or_empty_candles"
        trades.append(trade)
        append_jsonl(log_path, {"ts": utc_stamp(), "event": "replayed_decision", "index": idx, "ticker": ticker, "entry_status": trade.get("entry_status")})

    equity_curve, max_drawdown = build_equity_curve(trades, args.notional_capital_inr)
    by_bucket = by_bucket_summary(trades)
    entered = [row for row in trades if row.get("entry_status") == "entered"]
    no_entry = [row for row in trades if row.get("entry_status") == "no_entry"]
    not_permissioned = [row for row in trades if row.get("entry_status") == "not_permissioned"]
    net_pnl = round(sum(to_float(row.get("net_pnl_inr")) or 0.0 for row in entered), 2)
    avg_r = round(sum(to_float(row.get("final_r")) or 0.0 for row in entered) / len(entered), 6) if entered else 0.0

    write_csv(trades_path, trades)
    write_csv(equity_path, equity_curve)
    write_csv(bucket_path, by_bucket)

    audit = {
        "version": "SIGNAL_MODEL_V2_PAPER_REPLAY_AUDIT",
        "run_id": run_id,
        "generated_at": utc_stamp(),
        "decision_files": [rel(path) for path in decision_paths],
        "candles_dir": rel(args.candles_dir),
        "outputs": {
            "trades": rel(trades_path),
            "equity_curve": rel(equity_path),
            "by_bucket": rel(bucket_path),
            "report": rel(report_path),
            "log": rel(log_path),
        },
        "entry_policy": args.entry_policy,
        "intrabar_policy": "conservative_stop_first",
        "max_hold_bars": args.max_hold_bars,
        "notional_capital_inr": args.notional_capital_inr,
        "slippage_bps": args.slippage_bps,
        "brokerage_bps": args.brokerage_bps,
        "entry_permission_values": sorted(entry_permission_values),
        "include_non_permission_decisions": bool(args.include_non_permission_decisions),
        "missing_candle_tickers": sorted(set(missing_candles)),
        "bucket_counts": grouped_counts(trades, "bucket"),
        "permission_counts": grouped_counts(trades, "permission"),
        "entry_status_counts": grouped_counts(trades, "entry_status"),
        "exit_reason_counts": grouped_counts(trades, "exit_reason"),
        "by_bucket": by_bucket,
        "summary": {
            "decision_rows_read": len(decisions),
            "paper_rows_written": len(trades),
            "entered_trades": len(entered),
            "no_entry_rows": len(no_entry),
            "not_permissioned_rows": len(not_permissioned),
            "missing_candle_rows": sum(1 for row in trades if row.get("entry_status") == "missing_candles"),
            "hit_at_least_1_target_liq_rate": round(rate(entered, "hit_at_least_1_target_liq"), 6),
            "hit_at_least_2_target_liq_rate": round(rate(entered, "hit_at_least_2_target_liq"), 6),
            "hit_1r_rate": round(rate(entered, "hit_1r"), 6),
            "hit_2r_rate": round(rate(entered, "hit_2r"), 6),
            "avg_final_r": avg_r,
            "net_pnl_inr": net_pnl,
            "ending_equity_inr": round(args.notional_capital_inr + net_pnl, 2),
            "max_drawdown_inr": max_drawdown,
            "order_placement_enabled": False,
            "uses_only_post_decision_candles": True,
            "passed": len(trades) == len(decisions),
        },
    }
    write_json(audit_path, audit)
    write_report(report_path, audit)
    append_jsonl(
        log_path,
        {
            "ts": utc_stamp(),
            "event": "finish",
            "decision_rows": len(decisions),
            "entered_trades": len(entered),
            "audit": rel(audit_path),
            "report": rel(report_path),
        },
    )
    print(f"Wrote {trades_path}")
    print(f"Wrote {equity_path}")
    print(f"Wrote {bucket_path}")
    print(f"Wrote {audit_path}")
    print(f"Wrote {report_path}")
    return 0 if audit["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
