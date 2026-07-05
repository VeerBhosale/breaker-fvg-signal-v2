from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence


V2_ROOT = Path(__file__).resolve().parents[1]


def _is_nested_legacy_layout() -> bool:
    return (
        V2_ROOT.name == "signal_model_v2"
        and V2_ROOT.parent.name == "Breaker_Based"
        and V2_ROOT.parent.parent.name == "Newtest"
    )


def _path_from_env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).resolve() if value else default


REPO_ROOT = _path_from_env(
    "BREAKER_FVG_WORKSPACE_ROOT",
    V2_ROOT.parents[3] if _is_nested_legacy_layout() else V2_ROOT,
)
BREAKER_BASED_ROOT = _path_from_env(
    "BREAKER_FVG_BREAKER_BASED_ROOT",
    V2_ROOT.parent if _is_nested_legacy_layout() else REPO_ROOT / "external" / "Breaker_Based",
)
V1_ROOT = _path_from_env("BREAKER_FVG_V1_SIGNAL_MODEL_ROOT", BREAKER_BASED_ROOT / "signal_model")
SHORT_ROOT = _path_from_env("BREAKER_FVG_SHORT_SIGNAL_MODEL_ROOT", BREAKER_BASED_ROOT / "signal_model_short")
REFERENCE_ENGINE_PATH = _path_from_env(
    "BREAKER_FVG_REFERENCE_ENGINE",
    BREAKER_BASED_ROOT / "breaker_fvg_dashboard_export.py",
)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_dirs() -> None:
    for rel in [
        "configs",
        "data/raw",
        "data/features",
        "data/signals",
        "data/liquidity",
        "data/paper",
        "data/predictions",
        "models",
        "scripts",
        "logs",
        "reports",
        "audits",
        "docs",
        "tests",
        "aws",
        "runtime",
        "dashboard_bridge",
    ]:
        (V2_ROOT / rel).mkdir(parents=True, exist_ok=True)


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")


def append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(payload, sort_keys=True) + "\n")


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: Sequence[Dict[str, Any]], fieldnames: Sequence[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        seen: List[str] = []
        for row in rows:
            for key in row.keys():
                if key not in seen:
                    seen.append(key)
        fieldnames = seen
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def run_command(command: Sequence[str], log_path: Path, step: str, timeout_seconds: int | None = None) -> Dict[str, Any]:
    start = time.time()
    append_jsonl(
        log_path,
        {
            "ts": utc_stamp(),
            "step": step,
            "event": "start",
            "command": list(command),
            "timeout_seconds": timeout_seconds,
        },
    )
    try:
        proc = subprocess.run(
            list(command),
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = round(time.time() - start, 3)
        payload = {
            "ts": utc_stamp(),
            "step": step,
            "event": "timeout",
            "timeout_seconds": timeout_seconds,
            "elapsed_seconds": elapsed,
            "stdout_tail": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
        }
        append_jsonl(log_path, payload)
        raise RuntimeError(f"{step} timed out after {timeout_seconds} seconds. See {log_path}") from exc
    elapsed = round(time.time() - start, 3)
    payload = {
        "ts": utc_stamp(),
        "step": step,
        "event": "finish",
        "returncode": proc.returncode,
        "elapsed_seconds": elapsed,
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
    }
    append_jsonl(log_path, payload)
    if proc.returncode != 0:
        raise RuntimeError(f"{step} failed with return code {proc.returncode}. See {log_path}")
    return payload


def python_exe() -> str:
    return sys.executable or "python"


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(path)
