from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from v2_common import V2_ROOT, append_jsonl, read_csv, rel, utc_stamp, write_csv, write_json


TIME_COLUMNS = ["time", "timestamp", "datetime", "date", "Date", "Datetime"]
OHLC_ALIASES = {
    "open": ["open", "Open", "OPEN"],
    "high": ["high", "High", "HIGH"],
    "low": ["low", "Low", "LOW"],
    "close": ["close", "Close", "CLOSE", "adj close", "Adj Close"],
}
INTERVAL_SECONDS = {"1h": 60 * 60, "60m": 60 * 60}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest and normalize V2 candle data from local CSVs or an optional yfinance provider."
    )
    parser.add_argument("--provider", choices=["local_csv", "yfinance"], required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--ticker", action="append", default=[], help="Ticker to ingest. Can be repeated.")
    parser.add_argument("--universe-file", type=Path, default=None, help="CSV universe file. First column is used if no symbol/ticker column exists.")
    parser.add_argument("--source-dir", type=Path, default=None, help="Directory containing local candle CSVs.")
    parser.add_argument("--source-manifest", type=Path, default=None, help="CSV with ticker,path columns for local_csv provider.")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--period", default="730d", help="yfinance period when start/end are not supplied.")
    parser.add_argument("--start", default=None, help="Optional yfinance start date.")
    parser.add_argument("--end", default=None, help="Optional yfinance end date.")
    parser.add_argument("--input-timezone", default="Asia/Calcutta", help="Timezone for naive local datetimes.")
    parser.add_argument("--min-rows", type=int, default=50)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--retry-sleep-seconds", type=float, default=5.0)
    parser.add_argument("--allow-partial", action="store_true", help="Return success when some tickers fail but at least one passes.")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--audit", type=Path, default=None)
    return parser.parse_args()


def load_tickers(args: argparse.Namespace) -> List[str]:
    tickers: List[str] = []
    for ticker in args.ticker:
        ticker = str(ticker).strip()
        if ticker:
            tickers.append(ticker)
    if args.universe_file:
        rows = read_csv(args.universe_file)
        if not rows:
            raise ValueError(f"Universe file has no rows: {args.universe_file}")
        columns = list(rows[0].keys())
        preferred = next((c for c in ["ticker", "symbol", "Symbol", "SYMBOL", "nse_symbol"] if c in columns), columns[0])
        for row in rows:
            value = str(row.get(preferred, "")).strip()
            if value:
                tickers.append(value)
    deduped: List[str] = []
    seen = set()
    for ticker in tickers:
        if ticker not in seen:
            deduped.append(ticker)
            seen.add(ticker)
    if not deduped:
        raise ValueError("No tickers supplied. Use --ticker and/or --universe-file.")
    return deduped


def load_manifest(path: Path | None) -> Dict[str, Path]:
    if not path:
        return {}
    rows = read_csv(path)
    mapping: Dict[str, Path] = {}
    for row in rows:
        ticker = str(row.get("ticker") or row.get("symbol") or "").strip()
        value = str(row.get("path") or row.get("file") or "").strip()
        if ticker and value:
            p = Path(value)
            mapping[ticker] = p if p.is_absolute() else (path.parent / p)
    return mapping


def find_local_csv(ticker: str, source_dir: Path | None, manifest: Dict[str, Path]) -> Path:
    if ticker in manifest:
        return manifest[ticker]
    if source_dir is None:
        raise ValueError(f"No --source-dir or manifest entry supplied for {ticker}")
    exact_candidates = [
        source_dir / f"{ticker}.csv",
        source_dir / f"{ticker}_1h.csv",
        source_dir / f"{ticker}_1H.csv",
    ]
    for path in exact_candidates:
        if path.exists():
            return path
    matches = sorted(source_dir.glob(f"{ticker}*.csv"))
    if not matches:
        raise FileNotFoundError(f"No local CSV found for {ticker} in {source_dir}")
    if len(matches) > 1:
        # Deterministic, but force the ambiguity into the audit by choosing the shortest name first.
        matches = sorted(matches, key=lambda p: (len(p.name), p.name))
    return matches[0]


def select_column(columns: List[str], aliases: List[str]) -> str | None:
    lowered = {column.lower(): column for column in columns}
    for alias in aliases:
        if alias in columns:
            return alias
        if alias.lower() in lowered:
            return lowered[alias.lower()]
    return None


def parse_time_series(series: pd.Series, input_timezone: str) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(series):
        parsed = pd.to_datetime(series, errors="coerce")
        if parsed.dt.tz is None:
            tz = ZoneInfo(input_timezone)
            parsed = parsed.dt.tz_localize(tz, nonexistent="shift_forward", ambiguous="NaT")
        return parsed.dt.tz_convert("UTC")

    numeric = pd.to_numeric(series, errors="coerce")
    numeric_ratio = float(numeric.notna().mean()) if len(series) else 0.0
    if numeric_ratio >= 0.5:
        median_value = float(numeric.dropna().median()) if numeric.notna().any() else 0.0
        unit = "ms" if median_value > 10_000_000_000 else "s"
        return pd.to_datetime(numeric, unit=unit, utc=True, errors="coerce")

    parsed = pd.to_datetime(series, errors="coerce")
    if parsed.notna().sum() == 0:
        return pd.to_datetime(series.astype(str), errors="coerce", utc=True)

    try:
        parsed_tz = parsed.dt.tz
    except (AttributeError, TypeError):
        return pd.to_datetime(series.astype(str), errors="coerce", utc=True)

    if parsed_tz is None:
        tz = ZoneInfo(input_timezone)
        parsed = parsed.dt.tz_localize(tz, nonexistent="shift_forward", ambiguous="NaT")
    return parsed.dt.tz_convert("UTC")


def normalize_frame(raw: pd.DataFrame, input_timezone: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    columns = list(raw.columns)
    time_col = next((column for column in TIME_COLUMNS if column in columns), None)
    if time_col is None:
        time_col = select_column(columns, TIME_COLUMNS)
    if time_col is None:
        raise ValueError(f"No supported time column found. Columns: {columns}")

    selected: Dict[str, str] = {}
    for target, aliases in OHLC_ALIASES.items():
        source = select_column(columns, aliases)
        if source is None:
            raise ValueError(f"No supported {target} column found. Columns: {columns}")
        selected[target] = source

    out = pd.DataFrame(index=raw.index)
    parsed_time = parse_time_series(raw[time_col], input_timezone)
    out["time"] = pd.NA
    valid_time = parsed_time.notna()
    out.loc[valid_time, "time"] = (parsed_time[valid_time].astype("int64") // 1_000_000_000).astype("int64")
    for target, source in selected.items():
        out[target] = pd.to_numeric(raw[source], errors="coerce")

    before_drop = len(out)
    out = out.dropna(subset=["time", "open", "high", "low", "close"])
    out["time"] = out["time"].astype("int64")
    after_null_drop = len(out)
    invalid_ohlc = int(((out["high"] < out["low"]) | (out["open"] <= 0) | (out["high"] <= 0) | (out["low"] <= 0) | (out["close"] <= 0)).sum())
    out = out[(out["high"] >= out["low"]) & (out["open"] > 0) & (out["high"] > 0) & (out["low"] > 0) & (out["close"] > 0)]
    before_dedup = len(out)
    out = out.drop_duplicates(subset=["time"], keep="last").sort_values("time").reset_index(drop=True)

    audit = {
        "input_rows": int(len(raw)),
        "rows_after_null_drop": int(after_null_drop),
        "rows_after_invalid_ohlc_drop": int(before_dedup),
        "output_rows": int(len(out)),
        "null_or_unparseable_rows_dropped": int(before_drop - after_null_drop),
        "invalid_ohlc_rows_dropped": invalid_ohlc,
        "duplicate_time_rows_removed": int(before_dedup - len(out)),
        "time_column": time_col,
        "ohlc_columns": selected,
    }
    return out[["time", "open", "high", "low", "close"]], audit


def audit_normalized(frame: pd.DataFrame, min_rows: int, interval: str) -> Dict[str, Any]:
    times = frame["time"].tolist() if not frame.empty else []
    duplicate_count = len(times) - len(set(times))
    monotonic = all(times[index] < times[index + 1] for index in range(len(times) - 1))
    expected = INTERVAL_SECONDS.get(interval.lower(), 60 * 60)
    gaps = []
    if len(times) >= 2:
        gaps = [int(times[index + 1] - times[index]) for index in range(len(times) - 1)]
    large_gap_count = sum(1 for gap in gaps if gap > expected * 1.75)
    nonpositive_gap_count = sum(1 for gap in gaps if gap <= 0)
    null_ohlc = int(frame[["open", "high", "low", "close"]].isna().sum().sum()) if not frame.empty else 0
    return {
        "row_count": int(len(frame)),
        "first_time": int(min(times)) if times else None,
        "last_time": int(max(times)) if times else None,
        "duplicate_time_count": int(duplicate_count),
        "monotonic": bool(monotonic),
        "null_ohlc_count": int(null_ohlc),
        "large_gap_count": int(large_gap_count),
        "nonpositive_gap_count": int(nonpositive_gap_count),
        "min_rows": int(min_rows),
        "passed": bool(
            len(frame) >= min_rows
            and duplicate_count == 0
            and monotonic
            and null_ohlc == 0
            and nonpositive_gap_count == 0
        ),
    }


def read_local_ticker(ticker: str, args: argparse.Namespace, manifest: Dict[str, Path]) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    path = find_local_csv(ticker, args.source_dir, manifest)
    raw = pd.read_csv(path)
    frame, normalization = normalize_frame(raw, args.input_timezone)
    normalization["source_path"] = rel(path)
    return frame, normalization


def read_yfinance_ticker(ticker: str, args: argparse.Namespace) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    try:
        import yfinance as yf  # type: ignore
    except ImportError as exc:
        raise RuntimeError("yfinance provider requested but yfinance is not installed") from exc

    last_error: str | None = None
    raw = pd.DataFrame()
    for attempt in range(1, args.max_retries + 1):
        try:
            kwargs: Dict[str, Any] = {"interval": args.interval, "auto_adjust": False, "progress": False}
            if args.start or args.end:
                if args.start:
                    kwargs["start"] = args.start
                if args.end:
                    kwargs["end"] = args.end
            else:
                kwargs["period"] = args.period
            raw = yf.download(ticker, **kwargs)
            if not raw.empty:
                break
            last_error = "empty dataframe"
        except Exception as exc:  # pragma: no cover - network/provider behavior
            last_error = str(exc)
        if attempt < args.max_retries:
            time.sleep(args.retry_sleep_seconds)
    if raw.empty:
        raise RuntimeError(f"yfinance returned no data for {ticker}: {last_error}")

    raw = raw.reset_index()
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [str(column[0]) for column in raw.columns]
    frame, normalization = normalize_frame(raw, args.input_timezone)
    normalization["source_path"] = "yfinance"
    normalization["provider_period"] = args.period
    normalization["provider_start"] = args.start
    normalization["provider_end"] = args.end
    return frame, normalization


def ingest_ticker(ticker: str, args: argparse.Namespace, manifest: Dict[str, Path], output_dir: Path) -> Dict[str, Any]:
    if args.provider == "local_csv":
        frame, normalization = read_local_ticker(ticker, args, manifest)
    else:
        frame, normalization = read_yfinance_ticker(ticker, args)

    quality = audit_normalized(frame, args.min_rows, args.interval)
    out_path = output_dir / f"{ticker}_{args.interval}.csv"
    rows = frame.to_dict(orient="records")
    write_csv(out_path, rows, fieldnames=["time", "open", "high", "low", "close"])
    return {
        "ticker": ticker,
        "provider": args.provider,
        "output": rel(out_path),
        "normalization": normalization,
        "quality": quality,
        "passed": bool(quality["passed"]),
    }


def progress_payload(start_time: float, index: int, total: int) -> Dict[str, Any]:
    elapsed = max(time.time() - start_time, 0.0)
    completed = max(index, 1)
    remaining = max(total - index, 0)
    avg = elapsed / completed
    return {
        "elapsed_seconds": round(elapsed, 3),
        "avg_seconds_per_ticker": round(avg, 3),
        "remaining_tickers": remaining,
        "eta_seconds": round(avg * remaining, 3),
    }


def main() -> int:
    args = parse_args()
    run_id = args.run_id or f"v2_candle_ingest_{utc_stamp()}"
    output_dir = args.output_dir or (V2_ROOT / "data" / "raw" / run_id)
    audit_path = args.audit or (V2_ROOT / "audits" / f"{run_id}_audit.json")
    log_path = V2_ROOT / "logs" / f"{run_id}.jsonl"
    output_dir.mkdir(parents=True, exist_ok=True)

    tickers = load_tickers(args)
    manifest = load_manifest(args.source_manifest)
    start_time = time.time()
    append_jsonl(
        log_path,
        {
            "ts": utc_stamp(),
            "event": "start",
            "run_id": run_id,
            "provider": args.provider,
            "ticker_count": len(tickers),
            "output_dir": rel(output_dir),
        },
    )

    outputs: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for index, ticker in enumerate(tickers, start=1):
        append_jsonl(log_path, {"ts": utc_stamp(), "event": "ticker_start", "ticker": ticker, "index": index, "total": len(tickers)})
        try:
            result = ingest_ticker(ticker, args, manifest, output_dir)
            outputs.append(result)
            append_jsonl(
                log_path,
                {
                    "ts": utc_stamp(),
                    "event": "ticker_finish",
                    "ticker": ticker,
                    "passed": result["passed"],
                    "rows": result["quality"]["row_count"],
                    "output": result["output"],
                    **progress_payload(start_time, index, len(tickers)),
                },
            )
            if not result["passed"]:
                failures.append({"ticker": ticker, "error": "quality_audit_failed", "quality": result["quality"]})
        except Exception as exc:
            failure = {"ticker": ticker, "error": str(exc)}
            failures.append(failure)
            append_jsonl(
                log_path,
                {"ts": utc_stamp(), "event": "ticker_failed", **failure, **progress_payload(start_time, index, len(tickers))},
            )

    passed_count = sum(1 for item in outputs if item["passed"])
    failed_count = len(failures)
    audit = {
        "version": "SIGNAL_MODEL_V2_CANDLE_INGEST_AUDIT",
        "run_id": run_id,
        "generated_at": utc_stamp(),
        "provider": args.provider,
        "interval": args.interval,
        "ticker_count": len(tickers),
        "passed_count": passed_count,
        "failed_count": failed_count,
        "allow_partial": bool(args.allow_partial),
        "output_dir": rel(output_dir),
        "log": rel(log_path),
        "outputs": outputs,
        "failures": failures,
        "passed": bool(passed_count > 0 and (failed_count == 0 or args.allow_partial)),
    }
    write_json(audit_path, audit)
    append_jsonl(log_path, {"ts": utc_stamp(), "event": "finish", "passed": audit["passed"], "passed_count": passed_count, "failed_count": failed_count, "audit": rel(audit_path)})
    print(f"Wrote {audit_path}")
    return 0 if audit["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
