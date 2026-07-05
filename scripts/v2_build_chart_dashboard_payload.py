from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from v2_common import V2_ROOT


DEFAULT_CANDLES_DIR = Path(
    r"D:\Coding\Python Codes\Newtest\Breaker_Based\signal_model_v2\data\raw\v2_ingest_original_178_cached_1h_2y_eta"
)
DEFAULT_LIQUIDITY_ROOT = Path(r"D:\Coding\Python Codes\Newtest\Breaker_Based\signal_model_v2\data\liquidity")
DEFAULT_LIQUIDITY_RUN_PREFIX = "v2_replay_original_fresh_178_tail300_parallel_burnin"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build static chart-dashboard payloads for the hosted Signal V2 chart dashboard."
    )
    parser.add_argument(
        "--bridge-dir",
        type=Path,
        default=V2_ROOT / "dashboard_bridge" / "latest",
        help="Bridge directory containing cumulative_state.json.",
    )
    parser.add_argument(
        "--candles-dir",
        type=Path,
        default=DEFAULT_CANDLES_DIR,
        help="Directory containing <TICKER>_1h.csv candle files.",
    )
    parser.add_argument(
        "--liquidity-root",
        type=Path,
        default=DEFAULT_LIQUIDITY_ROOT,
        help="Directory containing per-ticker liquidity run folders.",
    )
    parser.add_argument("--liquidity-run-prefix", default=DEFAULT_LIQUIDITY_RUN_PREFIX)
    parser.add_argument("--max-candles-per-ticker", type=int, default=520)
    parser.add_argument("--max-liquidity-per-signal", type=int, default=80)
    parser.add_argument(
        "--out",
        type=Path,
        default=V2_ROOT / "site" / "chart" / "data" / "chart_payload.json",
    )
    return parser.parse_args()


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def as_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    numeric = as_float(value)
    if numeric is None:
        return None
    return int(numeric)


def compact_candle(row: Dict[str, str]) -> Dict[str, float | int]:
    return {
        "t": int(float(row["time"])),
        "o": float(row["open"]),
        "h": float(row["high"]),
        "l": float(row["low"]),
        "c": float(row["close"]),
    }


def compact_signal(row: Dict[str, Any]) -> Dict[str, Any]:
    summary = row.get("summary_metrics") if isinstance(row.get("summary_metrics"), dict) else {}
    return {
        "signal_id": row.get("signal_id"),
        "ticker": row.get("ticker"),
        "direction": row.get("direction"),
        "decision_time": as_int(row.get("decision_time")),
        "signal_time": as_int(row.get("signal_time")),
        "bucket": row.get("bucket"),
        "permission": row.get("permission"),
        "trade_action": row.get("trade_action"),
        "model_score": as_float(row.get("model_score")),
        "strict_score": as_float(row.get("strict_score")),
        "entry": as_float(row.get("entry")),
        "stop": as_float(row.get("stop")),
        "risk": as_float(row.get("risk")),
        "risk_pct_of_entry": as_float(row.get("risk_pct_of_entry")),
        "reason": row.get("reason"),
        "score_ready": row.get("score_ready"),
        "score_source": row.get("score_source"),
        "target_liquidity": row.get("target_liquidity") or [],
        "adverse_liquidity": row.get("adverse_liquidity") or [],
        "summary_metrics": {
            "target_liquidity_count": as_float(summary.get("target_liquidity_count")),
            "target_liquidity_score_sum": as_float(summary.get("target_liquidity_score_sum")),
            "target_liquidity_score_max": as_float(summary.get("target_liquidity_score_max")),
            "target_liquidity_nearest_distance_atr": as_float(
                summary.get("target_liquidity_nearest_distance_atr")
            ),
            "adverse_liquidity_count": as_float(summary.get("adverse_liquidity_count")),
            "adverse_liquidity_score_sum": as_float(summary.get("adverse_liquidity_score_sum")),
            "target_minus_adverse_pressure": as_float(summary.get("target_minus_adverse_pressure")),
            "xg_target_minus_stop_pressure": as_float(summary.get("xg_target_minus_stop_pressure")),
            "topbucket_target_pressure_minus_drag": as_float(
                summary.get("topbucket_target_pressure_minus_drag")
            ),
        },
    }


def ticker_slug(ticker: str) -> str:
    return ticker.lower().replace(".", "_").replace("-", "_").replace("&", "_")


def compact_liquidity(row: Dict[str, str]) -> Dict[str, Any]:
    price = as_float(row.get("midpoint"))
    if price is None:
        lower = as_float(row.get("lower"))
        upper = as_float(row.get("upper"))
        if lower is not None and upper is not None:
            price = (lower + upper) / 2.0
    return {
        "signal_id": row.get("signal_id"),
        "role": row.get("candidate_role"),
        "side": row.get("candidate_side") or row.get("side"),
        "pool_id": row.get("candidate_pool_id") or row.get("pool_id"),
        "price": price,
        "lower": as_float(row.get("lower")),
        "upper": as_float(row.get("upper")),
        "distance_atr": as_float(row.get("candidate_distance_to_signal_atr") or row.get("eval_distance_to_pool_atr")),
        "score": as_float(row.get("approved_model_score")),
        "percentile": as_float(row.get("approved_model_percentile")),
        "decile": as_int(row.get("approved_model_decile")),
        "formation_time": as_int(row.get("formation_time")),
        "oldest_time": as_int(row.get("oldest_time")),
        "newest_time": as_int(row.get("newest_time")),
    }


def load_liquidity_by_signal(
    liquidity_root: Path,
    run_prefix: str,
    tickers: Iterable[str],
    max_rows: int,
) -> Dict[str, List[Dict[str, Any]]]:
    by_signal: Dict[str, List[Dict[str, Any]]] = {}
    for ticker in sorted(set(tickers)):
        path = liquidity_root / f"{run_prefix}_{ticker_slug(ticker)}" / "candidates_scored.csv"
        if not path.exists():
            continue
        for raw in read_csv(path):
            signal_id = raw.get("signal_id")
            if not signal_id:
                continue
            by_signal.setdefault(signal_id, []).append(compact_liquidity(raw))
    for signal_id, rows in by_signal.items():
        rows.sort(
            key=lambda item: (
                item.get("role") != "target_side",
                -(item.get("score") or -1.0),
                item.get("distance_atr") if item.get("distance_atr") is not None else 999999.0,
            )
        )
        by_signal[signal_id] = rows[:max_rows]
    return by_signal


def load_candles(candles_dir: Path, ticker: str, max_candles: int) -> List[Dict[str, float | int]]:
    path = candles_dir / f"{ticker}_1h.csv"
    if not path.exists():
        return []
    rows = [compact_candle(row) for row in read_csv(path)]
    rows.sort(key=lambda row: int(row["t"]))
    return rows[-max_candles:]


def enrich_fvg_zones(signal: Dict[str, Any], raw_rows_by_id: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    raw = raw_rows_by_id.get(signal.get("signal_id") or "", {})
    zones: List[Dict[str, Any]] = []
    candidates = [
        ("Bull FVG", "bull", "tech_nearest_bull_fvg_lower", "tech_nearest_bull_fvg_upper", "tech_nearest_bull_fvg_created_time"),
        ("Bear FVG", "bear", "tech_nearest_bear_fvg_lower", "tech_nearest_bear_fvg_upper", "tech_nearest_bear_fvg_created_time"),
        ("Setup Bull FVG", "bull", "bull_fvg_lower", "bull_fvg_upper", "signal_time"),
    ]
    for label, side, lower_key, upper_key, time_key in candidates:
        lower = as_float(raw.get(lower_key))
        upper = as_float(raw.get(upper_key))
        if lower is None or upper is None:
            continue
        zones.append(
            {
                "label": label,
                "side": side,
                "lower": min(lower, upper),
                "upper": max(lower, upper),
                "time": as_int(raw.get(time_key)),
            }
        )
    return zones


def main() -> int:
    args = parse_args()
    cumulative_path = args.bridge_dir / "cumulative_state.json"
    if not cumulative_path.exists():
        raise FileNotFoundError(cumulative_path)

    cumulative = read_json(cumulative_path)
    bridge_rows = cumulative.get("rows") or []
    if not isinstance(bridge_rows, list):
        raise ValueError("cumulative_state.json rows must be a list")

    compact_signals = [compact_signal(row) for row in bridge_rows]
    raw_by_signal_id = {str(row.get("signal_id")): row for row in bridge_rows if row.get("signal_id")}
    tickers = sorted({str(row.get("ticker")) for row in compact_signals if row.get("ticker")})
    liquidity_by_signal = load_liquidity_by_signal(
        args.liquidity_root,
        args.liquidity_run_prefix,
        tickers,
        args.max_liquidity_per_signal,
    )

    ticker_payloads: Dict[str, Any] = {}
    total_candles = 0
    total_liquidity = 0
    for ticker in tickers:
        signals = [row for row in compact_signals if row.get("ticker") == ticker]
        for signal in signals:
            signal["liquidity_levels"] = liquidity_by_signal.get(signal.get("signal_id"), [])
            signal["fvg_zones"] = enrich_fvg_zones(signal, raw_by_signal_id)
            total_liquidity += len(signal["liquidity_levels"])
        candles = load_candles(args.candles_dir, ticker, args.max_candles_per_ticker)
        total_candles += len(candles)
        ticker_payloads[ticker] = {
            "ticker": ticker,
            "candles": candles,
            "signals": sorted(signals, key=lambda row: row.get("decision_time") or 0),
        }

    payload = {
        "schema_version": "SIGNAL_MODEL_V2_CHART_DASHBOARD_PAYLOAD_V1",
        "generated_at": utc_stamp(),
        "source": {
            "bridge_run_id": cumulative.get("run_id"),
            "bridge_generated_at": cumulative.get("generated_at"),
            "candles_dir": str(args.candles_dir),
            "liquidity_root": str(args.liquidity_root),
            "liquidity_run_prefix": args.liquidity_run_prefix,
        },
        "summary": {
            "ticker_count": len(tickers),
            "signal_count": len(compact_signals),
            "candle_count": total_candles,
            "liquidity_level_count": total_liquidity,
            "bucket_counts": cumulative.get("bucket_counts") or {},
            "permission_counts": cumulative.get("permission_counts") or {},
        },
        "tickers": ticker_payloads,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8", newline="\n")
    print(args.out)
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
