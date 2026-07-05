from __future__ import annotations

import argparse
import json
from pathlib import Path

from v2_common import V2_ROOT


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a self-contained V2 dashboard HTML snapshot from dashboard_bridge artifacts."
    )
    parser.add_argument(
        "--bridge-dir",
        type=Path,
        default=V2_ROOT / "dashboard_bridge" / "latest",
        help="Directory containing live_state.json and cumulative_state.json.",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=V2_ROOT / "web" / "index.html",
        help="Dashboard template that supports window.SIGNAL_V2_EMBEDDED.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=V2_ROOT / "web" / "dashboard_snapshot.html",
        help="Output standalone dashboard HTML.",
    )
    parser.add_argument("--label", default="embedded-latest")
    return parser.parse_args()


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    args = parse_args()
    live_path = args.bridge_dir / "live_state.json"
    cumulative_path = args.bridge_dir / "cumulative_state.json"
    if not live_path.exists():
        raise FileNotFoundError(live_path)
    if not cumulative_path.exists():
        raise FileNotFoundError(cumulative_path)
    if not args.template.exists():
        raise FileNotFoundError(args.template)

    payload = {
        "label": args.label,
        "live": read_json(live_path),
        "cumulative": read_json(cumulative_path),
    }
    injected = (
        "<script>\n"
        "window.SIGNAL_V2_EMBEDDED = "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + ";\n"
        "</script>\n"
    )
    html = args.template.read_text(encoding="utf-8")
    if "</head>" not in html:
        raise ValueError("Template is missing </head> injection point.")
    html = html.replace("</head>", injected + "</head>", 1)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8", newline="\n")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
