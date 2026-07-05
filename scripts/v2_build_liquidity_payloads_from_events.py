from __future__ import annotations

import argparse
import csv
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from v2_common import REPO_ROOT, REFERENCE_ENGINE_PATH, V2_ROOT, append_jsonl, rel, utc_stamp, write_csv, write_json


ORIGINAL_ENGINE = REFERENCE_ENGINE_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build V2 decision-time liquidity structure payloads from signal events and V2 candle CSVs."
    )
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--candles", type=Path, required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--normalized-events", type=Path, default=None)
    parser.add_argument("--payload-dir", type=Path, default=None)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--audit", type=Path, default=None)
    parser.add_argument(
        "--max-input-candles",
        type=int,
        default=None,
        help=(
            "Optional decision-time lookback bound. For each event, use candles <= decision_time, "
            "then keep only the latest N candles for the reference liquidity payload."
        ),
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


def read_events(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_candles(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path)
    required = {"time", "open", "high", "low", "close"}
    missing = sorted(required.difference(raw.columns))
    if missing:
        raise ValueError(f"Candle CSV missing required columns: {missing}")
    frame = raw[["time", "open", "high", "low", "close"]].copy()
    for column in frame.columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["time", "open", "high", "low", "close"])
    frame["time"] = frame["time"].astype("int64")
    frame = frame.drop_duplicates(subset=["time"], keep="last").sort_values("time").reset_index(drop=True)
    return frame


def frame_for_engine(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out.index = pd.to_datetime(out["time"], unit="s", utc=True)
    out = out.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
    return out[["Open", "High", "Low", "Close"]]


def as_int(value: Any) -> int | None:
    try:
        if value in ("", None) or pd.isna(value):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def normalize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(event)
    if not out.get("signal_price"):
        out["signal_price"] = out.get("entry_price")
    if not out.get("feature_cutoff_time"):
        out["feature_cutoff_time"] = out.get("decision_time")
    if not out.get("direction"):
        out["direction"] = out.get("side") or "long"
    if not out.get("side"):
        out["side"] = out.get("direction") or "long"
    return out


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return str(value)
    if pd.isna(value) if not isinstance(value, (dict, list, tuple, str)) else False:
        return None
    return value


def write_payload(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(json_safe(payload), handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")


def build_payload_for_event(
    engine: Any,
    event: Dict[str, Any],
    candles: pd.DataFrame,
    payload_dir: Path,
    max_input_candles: int | None = None,
) -> Dict[str, Any]:
    ticker = event.get("ticker") or "UNKNOWN"
    signal_id = event.get("signal_id") or ""
    decision_time = as_int(event.get("decision_time"))
    if decision_time is None:
        return {"status": "skipped", "reason": "missing_decision_time", "signal_id": signal_id}

    if max_input_candles is not None and max_input_candles <= 0:
        raise ValueError("--max-input-candles must be a positive integer when supplied.")

    raw_truncated = candles[candles["time"] <= decision_time].copy()
    truncated = raw_truncated
    if max_input_candles is not None and len(raw_truncated) > max_input_candles:
        truncated = raw_truncated.tail(max_input_candles).copy()
    if len(truncated) < 50:
        return {
            "status": "skipped",
            "reason": "too_few_candles_at_decision_time",
            "signal_id": signal_id,
            "decision_time": decision_time,
            "candle_count": int(len(truncated)),
            "raw_cutoff_candle_count": int(len(raw_truncated)),
            "max_input_candles": max_input_candles,
        }
    analysis = engine.analyze_ticker(ticker, frame_for_engine(truncated), False)
    if analysis is None:
        return {
            "status": "skipped",
            "reason": "reference_engine_returned_none",
            "signal_id": signal_id,
            "decision_time": decision_time,
            "candle_count": int(len(truncated)),
        }

    payload = {
        "schema_version": "SIGNAL_MODEL_V2_DECISION_TIME_LIQUIDITY_PAYLOAD_V1",
        "signal_window_liquidity_export": "v2_reference_engine_decision_time_cutoff",
        "generated_at": utc_stamp(),
        "signal_id": signal_id,
        "ticker": ticker,
        "decision_time": decision_time,
        "signal_time": as_int(event.get("signal_time")),
        "direction": event.get("direction") or event.get("side") or "long",
        "side": event.get("side") or event.get("direction") or "long",
        "lookback_bars": int(len(truncated)),
        "raw_cutoff_lookback_bars": int(len(raw_truncated)),
        "max_input_candles": max_input_candles,
        "lookback_window_applied": bool(len(truncated) != len(raw_truncated)),
        "source_candle_first_time": int(truncated["time"].min()),
        "source_candle_last_time": int(truncated["time"].max()),
        "decision_time_cutoff_valid": bool(int(truncated["time"].max()) <= decision_time),
        "original_reference_engine": rel(ORIGINAL_ENGINE),
        "tickers": [analysis],
    }
    safe_signal = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in signal_id)
    payload_path = payload_dir / f"{safe_signal}.json"
    write_payload(payload_path, payload)
    return {
        "status": "written",
        "signal_id": signal_id,
        "ticker": ticker,
        "decision_time": decision_time,
        "candle_count": int(len(truncated)),
        "raw_cutoff_candle_count": int(len(raw_truncated)),
        "max_input_candles": max_input_candles,
        "lookback_window_applied": bool(len(truncated) != len(raw_truncated)),
        "payload": rel(payload_path),
        "cutoff_last_time": int(truncated["time"].max()),
    }


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"v2_liquidity_payloads_{utc_stamp()}"
    log_path = V2_ROOT / "logs" / f"{run_id}.jsonl"
    payload_dir = args.payload_dir or (V2_ROOT / "data" / "liquidity" / run_id / "payloads")
    manifest_path = args.manifest or (V2_ROOT / "data" / "liquidity" / run_id / "payload_manifest.txt")
    normalized_events_path = args.normalized_events or (V2_ROOT / "data" / "liquidity" / run_id / "events_normalized.csv")
    audit_path = args.audit or (V2_ROOT / "audits" / f"{run_id}_audit.json")

    append_jsonl(
        log_path,
        {
            "ts": utc_stamp(),
            "event": "start",
            "events": str(args.events),
            "candles": str(args.candles),
            "max_input_candles": args.max_input_candles,
        },
    )
    events = [normalize_event(event) for event in read_events(args.events)]
    candles = read_candles(args.candles)
    engine = load_original_engine()
    write_csv(normalized_events_path, events)

    results = [build_payload_for_event(engine, event, candles, payload_dir, args.max_input_candles) for event in events]
    written_paths = [result["payload"] for result in results if result.get("status") == "written"]
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("\n".join(str((REPO_ROOT / path).resolve()) for path in written_paths) + ("\n" if written_paths else ""), encoding="utf-8")

    skipped = [result for result in results if result.get("status") != "written"]
    audit = {
        "version": "SIGNAL_MODEL_V2_LIQUIDITY_PAYLOAD_BUILD_AUDIT",
        "run_id": run_id,
        "generated_at": utc_stamp(),
        "events": rel(args.events),
        "normalized_events": rel(normalized_events_path),
        "candles": rel(args.candles),
        "payload_dir": rel(payload_dir),
        "manifest": rel(manifest_path),
        "log": rel(log_path),
        "event_rows": len(events),
        "payloads_written": len(written_paths),
        "payloads_skipped": len(skipped),
        "max_input_candles": args.max_input_candles,
        "lookback_window_applied_count": sum(1 for result in results if result.get("lookback_window_applied")),
        "max_raw_cutoff_candle_count": max(
            [int(result.get("raw_cutoff_candle_count") or 0) for result in results],
            default=0,
        ),
        "max_payload_candle_count": max([int(result.get("candle_count") or 0) for result in results], default=0),
        "results": results,
        "passed": bool(events and len(written_paths) == len(events)),
        "decision_time_rule": "Each payload is built from candles with time <= that event decision_time.",
        "original_files_modified": False,
    }
    write_json(audit_path, audit)
    append_jsonl(
        log_path,
        {
            "ts": utc_stamp(),
            "event": "finish",
            "event_rows": len(events),
            "payloads_written": len(written_paths),
            "payloads_skipped": len(skipped),
            "max_input_candles": args.max_input_candles,
            "audit": rel(audit_path),
        },
    )
    print(f"Wrote {normalized_events_path}")
    print(f"Wrote {manifest_path}")
    print(f"Wrote {audit_path}")
    return 0 if audit["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
