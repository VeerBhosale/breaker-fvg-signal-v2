from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from v2_common import REFERENCE_ENGINE_PATH, V2_ROOT, append_jsonl, rel, utc_stamp, write_csv, write_json


ORIGINAL_ENGINE = REFERENCE_ENGINE_PATH
INTERVAL_SECONDS = 60 * 60


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Detect Breaker+FVG signal events from V2-owned candle CSVs using the original engine as source/reference. "
            "Short detection is V2-owned and uses an auditable price-reflection wrapper around the unchanged long detector."
        )
    )
    parser.add_argument("--candles", type=Path, required=True, help="V2 raw candle CSV with time/open/high/low/close.")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--side", choices=["long", "short", "both"], default="long")
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--decision-time-policy",
        choices=["signal_time", "next_candle_after_signal"],
        default="signal_time",
        help="signal_time preserves original-dashboard compatibility; next_candle_after_signal is stricter for live confirmation review.",
    )
    parser.add_argument(
        "--max-input-candles",
        type=int,
        default=None,
        help=(
            "Optional live-safe detector bound. When set, signal detection uses only the latest N candles "
            "while the audit still records the full raw candle span."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output event CSV path. Defaults to signal_model_v2/data/signals/{run_id}_events.csv.",
    )
    parser.add_argument(
        "--audit",
        type=Path,
        default=None,
        help="Optional audit JSON path. Defaults to signal_model_v2/audits/{run_id}_audit.json.",
    )
    return parser.parse_args()


def load_original_engine() -> Any:
    spec = importlib.util.spec_from_file_location("breaker_fvg_dashboard_export_reference", ORIGINAL_ENGINE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load original engine module from {ORIGINAL_ENGINE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "analyze_ticker"):
        raise RuntimeError("Original engine module does not expose analyze_ticker")
    return module


def read_candles(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path)
    required = {"time", "open", "high", "low", "close"}
    missing = sorted(required.difference(raw.columns))
    if missing:
        raise ValueError(f"Input candle file missing required columns: {missing}")

    df = raw[["time", "open", "high", "low", "close"]].copy()
    for col in ["time", "open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["time", "open", "high", "low", "close"])
    df["time"] = df["time"].astype("int64")
    df = df.drop_duplicates(subset=["time"], keep="last").sort_values("time")
    df.index = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
    return df[["Open", "High", "Low", "Close"]]


def mirror_candles_for_short(df: pd.DataFrame) -> pd.DataFrame:
    """Reflect prices so a bearish setup can be detected by the unchanged long reference detector."""
    out = pd.DataFrame(index=df.index)
    out["Open"] = -df["Open"]
    out["High"] = -df["Low"]
    out["Low"] = -df["High"]
    out["Close"] = -df["Close"]
    return out[["Open", "High", "Low", "Close"]]


def safe_float(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def mirror_price(value: Any) -> float | None:
    number = safe_float(value)
    return -number if number is not None else None


def safe_int(value: Any) -> int | None:
    try:
        if value is None or pd.isna(value):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def stable_signal_id(ticker: str, side: str, signal: Dict[str, Any]) -> str:
    levels = signal.get("levels") or {}
    price = signal.get("price")
    sweep_level = levels.get("T1 Sweep Low")
    setup_level = levels.get("T2 High")
    if side == "short":
        price = mirror_price(price)
        sweep_level = mirror_price(sweep_level)
        setup_level = mirror_price(setup_level)
    payload = "|".join(
        [
            ticker,
            side,
            str(signal.get("time")),
            str(price),
            str(sweep_level),
            str(setup_level),
        ]
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"{ticker}|{side}|{signal.get('time')}|{digest}"


def to_event_row(
    ticker: str,
    side: str,
    signal: Dict[str, Any],
    source_candles: Path,
    decision_time_policy: str,
) -> Dict[str, Any]:
    signal_time = safe_int(signal.get("time"))
    decision_time = signal_time
    if signal_time is not None and decision_time_policy == "next_candle_after_signal":
        decision_time = signal_time + INTERVAL_SECONDS

    levels = signal.get("levels") or {}
    level_times = signal.get("level_times") or {}
    metrics = signal.get("metrics") or {}

    is_short = side == "short"
    entry = mirror_price(signal.get("price")) if is_short else safe_float(signal.get("price"))
    stop = mirror_price(levels.get("T1 Sweep Low")) if is_short else safe_float(levels.get("T1 Sweep Low"))
    if stop is None:
        stop = mirror_price(levels.get("Current ISL")) if is_short else safe_float(levels.get("Current ISL"))
    risk = (stop - entry) if is_short and entry is not None and stop is not None else (entry - stop) if entry is not None and stop is not None else None
    target_1r = entry - risk if is_short and entry is not None and risk is not None and risk > 0 else entry + risk if entry is not None and risk is not None and risk > 0 else None
    target_2r = entry - 2 * risk if is_short and entry is not None and risk is not None and risk > 0 else entry + 2 * risk if entry is not None and risk is not None and risk > 0 else None

    signal_id = stable_signal_id(ticker, side, signal)
    row: Dict[str, Any] = {
        "signal_id": signal_id,
        "candidate_row_id": f"{signal_id}|signal_close_v2_reference",
        "ticker": ticker,
        "side": side,
        "direction": side,
        "signal_time": signal_time,
        "decision_time": decision_time,
        "decision_time_policy": decision_time_policy,
        "timestamp": signal.get("timestamp"),
        "entry_model_variant": "signal_close_v2_reference",
        "entry_price": entry,
        "stop_price": stop,
        "risk": risk,
        "target_1r": target_1r,
        "target_2r": target_2r,
        "legacy_signal_score": safe_float(signal.get("score")),
        "legacy_ratio": safe_float(signal.get("ratio")),
        "legacy_atr_ratio": safe_float(signal.get("atr_ratio")),
        "legacy_fvg_atr": safe_float(signal.get("fvg_atr")),
        "legacy_isl_sweep": bool(signal.get("isl_sweep")),
        "legacy_isl_level": safe_float(signal.get("isl_level")),
        "mirror_transform_applied": is_short,
        "source_candles": rel(source_candles),
        "source_detector": rel(ORIGINAL_ENGINE),
        "source_detector_function": "analyze_ticker",
        "source_detector_side_support": "long_native_short_price_mirror" if is_short else "long_native",
        "production_entry_candidate": False,
        "production_entry_note": "Reference signal-close candidate only. Final live entry model still requires V2 candidate generator parity.",
        "metrics_json": json.dumps(metrics, sort_keys=True, default=str),
    }

    if is_short:
        for key, out_name in [
            ("T3 Low", "t3_high"),
            ("T2 High", "t2_low"),
            ("T1 Sweep Low", "t1_sweep_high"),
            ("Signal High", "signal_low"),
            ("Current ISL", "current_ish"),
            ("Base ISL", "base_ish"),
            ("Base ISH", "base_isl"),
            ("Deeper ISL", "deeper_ish"),
        ]:
            row[f"{out_name}_price"] = mirror_price(levels.get(key))
        row["bear_fvg_lower_price"] = mirror_price(levels.get("Bull FVG Upper"))
        row["bear_fvg_upper_price"] = mirror_price(levels.get("Bull FVG Lower"))
    else:
        for key, out_name in [
            ("T3 Low", "t3_low"),
            ("T2 High", "t2_high"),
            ("T1 Sweep Low", "t1_sweep_low"),
            ("Signal High", "signal_high"),
            ("Current ISL", "current_isl"),
            ("Base ISL", "base_isl"),
            ("Base ISH", "base_ish"),
            ("Deeper ISL", "deeper_isl"),
            ("Bull FVG Lower", "bull_fvg_lower"),
            ("Bull FVG Upper", "bull_fvg_upper"),
        ]:
            row[f"{out_name}_price"] = safe_float(levels.get(key))

    time_specs = [
        ("T3 Low", "t3_high" if is_short else "t3_low"),
        ("T2 High", "t2_low" if is_short else "t2_high"),
        ("T1 Sweep Low", "t1_sweep_high" if is_short else "t1_sweep_low"),
        ("Signal High", "signal_low" if is_short else "signal_high"),
        ("Current ISL", "current_ish" if is_short else "current_isl"),
        ("Base ISL", "base_ish" if is_short else "base_isl"),
        ("Base ISH", "base_isl" if is_short else "base_ish"),
        ("Deeper ISL", "deeper_ish" if is_short else "deeper_isl"),
        ("Bull FVG", "bear_fvg" if is_short else "bull_fvg"),
    ]
    for key, out_name in time_specs:
        row[f"{out_name}_time"] = safe_int(level_times.get(key))

    return row


def audit_input(df: pd.DataFrame) -> Dict[str, Any]:
    unix_times = [int(ts.timestamp()) for ts in df.index]
    return {
        "input_rows": int(len(df)),
        "first_time": min(unix_times) if unix_times else None,
        "last_time": max(unix_times) if unix_times else None,
        "duplicate_time_count": int(len(unix_times) - len(set(unix_times))),
        "monotonic_time": bool(all(unix_times[i] < unix_times[i + 1] for i in range(len(unix_times) - 1))),
        "null_ohlc_count": int(df[["Open", "High", "Low", "Close"]].isna().sum().sum()),
    }


def apply_detector_window(df: pd.DataFrame, max_input_candles: int | None) -> tuple[pd.DataFrame, Dict[str, Any]]:
    raw_input_audit = audit_input(df)
    if max_input_candles is not None and max_input_candles <= 0:
        raise ValueError("--max-input-candles must be a positive integer when supplied.")

    detector_df = df
    if max_input_candles is not None and len(df) > max_input_candles:
        detector_df = df.tail(max_input_candles).copy()

    detector_input_audit = audit_input(detector_df)
    return detector_df, {
        "max_input_candles": max_input_candles,
        "windowing_applied": bool(len(detector_df) != len(df)),
        "raw_input_rows": raw_input_audit["input_rows"],
        "detector_input_rows": detector_input_audit["input_rows"],
        "raw_first_time": raw_input_audit["first_time"],
        "raw_last_time": raw_input_audit["last_time"],
        "detector_first_time": detector_input_audit["first_time"],
        "detector_last_time": detector_input_audit["last_time"],
        "raw_input_audit": raw_input_audit,
        "detector_input_audit": detector_input_audit,
    }


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"v2_signal_detect_{utc_stamp()}"
    log_path = V2_ROOT / "logs" / f"{run_id}.jsonl"
    out_path = args.output or (V2_ROOT / "data" / "signals" / f"{run_id}_events.csv")
    audit_path = args.audit or (V2_ROOT / "audits" / f"{run_id}_audit.json")

    append_jsonl(
        log_path,
        {
            "ts": utc_stamp(),
            "event": "start",
            "ticker": args.ticker,
            "candles": str(args.candles),
            "side": args.side,
            "decision_time_policy": args.decision_time_policy,
            "max_input_candles": args.max_input_candles,
        },
    )

    raw_df = read_candles(args.candles)
    df, detector_window_audit = apply_detector_window(raw_df, args.max_input_candles)
    input_audit = detector_window_audit["detector_input_audit"]
    engine = load_original_engine()
    sides = ["long", "short"] if args.side == "both" else [args.side]
    raw_rows: List[Dict[str, Any]] = []
    detector_results: Dict[str, Any] = {}
    for side in sides:
        detector_frame = mirror_candles_for_short(df) if side == "short" else df
        result = engine.analyze_ticker(args.ticker, detector_frame, False)
        signals = (result or {}).get("signals") or []
        detector_results[side] = {
            "raw_signal_count": len(signals),
            "detector_transform": "price_reflection_p_negative" if side == "short" else "native_long_reference",
        }
        raw_rows.extend(
            to_event_row(args.ticker, side, signal, args.candles, args.decision_time_policy)
            for signal in signals
        )

    rows: List[Dict[str, Any]] = []
    invalid_risk_signal_ids: List[str] = []
    for row in raw_rows:
        risk = safe_float(row.get("risk"))
        if risk is None or risk <= 0:
            invalid_risk_signal_ids.append(str(row.get("signal_id") or ""))
            continue
        rows.append(row)
    write_csv(out_path, rows)

    risk_invalid = len(invalid_risk_signal_ids)
    decision_before_signal = 0
    side_counts: Dict[str, int] = {}
    for row in rows:
        side = str(row.get("side") or "unknown")
        side_counts[side] = side_counts.get(side, 0) + 1
        st = safe_int(row.get("signal_time"))
        dt = safe_int(row.get("decision_time"))
        if st is not None and dt is not None and dt < st:
            decision_before_signal += 1

    audit = {
        "version": "SIGNAL_MODEL_V2_SIGNAL_DETECTION_AUDIT",
        "run_id": run_id,
        "generated_at": utc_stamp(),
        "ticker": args.ticker,
        "source_candles": rel(args.candles),
        "output_events": rel(out_path),
        "log": rel(log_path),
        "original_reference_engine": rel(ORIGINAL_ENGINE),
        "original_files_modified": False,
        "detector": {
            "function": "analyze_ticker",
            "side_requested": args.side,
            "side_supported": "long_native_short_price_mirror" if "short" in sides else "long_native",
            "short_side_supported": "short" in sides,
            "short_side_method": "price_reflection_wrapper_p_negative" if "short" in sides else None,
            "fvg_confirm_after_signal_candles": getattr(engine, "FVG_CONFIRM_AFTER_SIGNAL_CANDLES", None),
            "uses_shift_minus_one_for_swing_confirmation": True,
            "decision_time_policy": args.decision_time_policy,
            "detector_results": detector_results,
        },
        "input_audit": input_audit,
        "detector_window_audit": detector_window_audit,
        "raw_signal_count": len(raw_rows),
        "signal_count": len(rows),
        "side_counts": side_counts,
        "risk_invalid_count": risk_invalid,
        "risk_invalid_filtered_count": risk_invalid,
        "risk_invalid_filtered_signal_ids_sample": [value for value in invalid_risk_signal_ids if value][:20],
        "decision_before_signal_count": decision_before_signal,
        "passed": bool(
            input_audit["input_rows"] >= 50
            and input_audit["duplicate_time_count"] == 0
            and input_audit["monotonic_time"]
            and input_audit["null_ohlc_count"] == 0
            and decision_before_signal == 0
        ),
        "production_ready": False,
        "production_readiness_note": (
            "This proves V2 can replay reference signal detection from V2-owned candles for the requested side(s). "
            "Short-side detection uses a V2-owned price mirror wrapper and still needs downstream feature/inference parity validation."
        ),
        "decision_time_warning": (
            "Original reference detector confirms swing highs with one future candle. "
            "Use next_candle_after_signal policy or a reviewed live confirmation contract before production order decisions."
        ),
    }
    write_json(audit_path, audit)

    append_jsonl(
        log_path,
        {
            "ts": utc_stamp(),
            "event": "finish",
            "ticker": args.ticker,
            "signals": len(rows),
            "raw_signals": len(raw_rows),
            "risk_invalid_filtered_count": risk_invalid,
            "side_counts": side_counts,
            "passed": audit["passed"],
            "raw_input_rows": detector_window_audit["raw_input_rows"],
            "detector_input_rows": detector_window_audit["detector_input_rows"],
            "windowing_applied": detector_window_audit["windowing_applied"],
            "output_events": rel(out_path),
            "audit": rel(audit_path),
        },
    )
    print(f"Wrote {out_path}")
    print(f"Wrote {audit_path}")
    return 0 if audit["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
