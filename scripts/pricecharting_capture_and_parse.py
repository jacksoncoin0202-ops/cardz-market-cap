# -*- coding: utf-8 -*-
"""One-shot: connect/launch CF session → fetch product → parse PSA10 bundle."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
PIPE = ROOT / "pipelines"


def run(cmd: list[str]) -> int:
    print("+", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["connect", "launch", "fetch-only"], default="connect")
    ap.add_argument("--port", type=int, default=9333)
    ap.add_argument(
        "--url",
        default="https://www.pricecharting.com/game/pokemon-base-set/charizard-4",
    )
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()

    session = [
        PY,
        "-X",
        "utf8",
        str(PIPE / "pricecharting_cf_session.py"),
    ]
    if args.mode == "connect":
        rc = run(session + ["connect", "--port", str(args.port), "--url", args.url, "--timeout", str(args.timeout)])
    elif args.mode == "launch":
        rc = run(session + ["launch", "--url", args.url, "--timeout", str(args.timeout)])
    else:
        rc = 0

    if args.mode != "fetch-only" and rc != 0:
        print("session capture failed; try launch mode or check CDP port", flush=True)

    # always try fetch with whatever storage exists
    html_out = ROOT / "data" / "private" / "pricecharting_session" / "html" / "latest.html"
    rc2 = run(session + ["fetch", "--url", args.url, "--out", str(html_out)])
    if rc2 != 0 and not html_out.exists():
        # fallback: use connect capture html if present
        candidates = list((ROOT / "data" / "private" / "pricecharting_session" / "html").glob("capture_*.html"))
        if not candidates:
            return rc2 or rc or 2
        html_out = max(candidates, key=lambda p: p.stat().st_mtime)

    parsed_out = ROOT / "data" / "private" / "pricecharting_session" / "parsed_latest.json"
    rc3 = run(
        [
            PY,
            "-X",
            "utf8",
            str(PIPE / "pricecharting_page_parse.py"),
            str(html_out),
            "--url",
            args.url,
            "--out",
            str(parsed_out),
        ]
    )
    if parsed_out.exists():
        data = json.loads(parsed_out.read_text(encoding="utf-8"))
        if data.get("ok"):
            psa = data.get("psa10") or {}
            hist = psa.get("history") or {}
            sales = psa.get("completed_sales") or {}
            print(
                "\n=== RESULT ===\n"
                f"product_id={ (data.get('product') or {}).get('id') }\n"
                f"psa10_history_points={hist.get('points')} last_usd={hist.get('last_usd')}\n"
                f"psa10_sales={sales.get('count')}\n"
                f"parsed={parsed_out}\n"
                f"html={html_out}\n",
                flush=True,
            )
        else:
            print("parse not ok:", data, flush=True)
    return rc3


if __name__ == "__main__":
    raise SystemExit(main())
