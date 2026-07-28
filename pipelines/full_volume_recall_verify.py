#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全腳本低門檻召回 → 二次 QC → 過先入 DB。

用戶方針：所有劇本都低門檻命中，翻嚟二次 check，通過先 mark（一次記低）。

  python -X utf8 pipelines/full_volume_recall_verify.py
  python -X utf8 pipelines/full_volume_recall_verify.py --write
  python -X utf8 pipelines/full_volume_recall_verify.py --write --with-trades
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "data/runtime/private-source-map"
REPORT_DIR = MAP / "qualified-pool-reports"
PY = sys.executable


def load_env() -> None:
    env = ROOT / "data/runtime/config/backend.env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().replace("\r", ""))
    os.environ.setdefault("CARDZ_DB_HOST", "127.0.0.1")


def run_py(args: list[str], *, cwd: Path | None = None) -> dict[str, Any]:
    cmd = [PY, "-X", "utf8", *args]
    print("+", " ".join(cmd), flush=True)
    p = subprocess.run(
        cmd,
        cwd=str(cwd or ROOT),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    # try parse last JSON object from stdout
    parsed = None
    for chunk in reversed((p.stdout or "").split("\n{")):
        text = chunk if chunk.startswith("{") else "{" + chunk
        try:
            parsed = json.loads(text.strip().split("\nreport")[0].strip())
            break
        except json.JSONDecodeError:
            continue
    return {
        "cmd": args,
        "exit": p.returncode,
        "tail": "\n".join(out.strip().splitlines()[-30:]),
        "json": parsed,
    }


def status() -> dict[str, Any]:
    r = run_py(["pipelines/qualified_pool_operator.py", "status"])
    return r.get("json") or {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--recall-min", type=int, default=30)
    ap.add_argument("--with-trades", action="store_true", help="after identity: snk pull+ingest trades")
    ap.add_argument("--with-g10-cache", action="store_true", help="re-run g10 sales_cache snkrdunk")
    args = ap.parse_args()
    load_env()

    steps: list[dict[str, Any]] = []
    before = status()
    steps.append({"step": "status_before", "data": before})

    wflag = ["--write"] if args.write else []

    # 1) semi-auto identity: clean + SNK + eBay (recall→verify inside)
    steps.append(
        {
            "step": "semi_auto_identity",
            "result": run_py(
                [
                    "pipelines/semi_auto_identity.py",
                    "run",
                    *wflag,
                    "--recall-min",
                    str(args.recall_min),
                ]
            ),
        }
    )

    # 2) clone sales from twin variants (strict name+collector already inside)
    steps.append(
        {
            "step": "fill_watchlist_sales_stock",
            "result": run_py(
                ["pipelines/fill_watchlist_sales_stock.py", *(["--write"] if args.write else [])]
            ),
        }
    )

    # 3) harvest chip prices for bound SNK
    if args.write:
        steps.append(
            {
                "step": "ingest_harvest_prices",
                "result": run_py(["pipelines/bind_snk_watchlist.py", "ingest-harvest-prices"]),
            }
        )

    # 4) optional G10 multi-year sales
    if args.write and args.with_g10_cache:
        steps.append(
            {
                "step": "g10_sales_cache",
                "result": run_py(
                    [
                        "pipelines/g10_sales_cache_ingest.py",
                        "--write",
                        "--platforms",
                        "snkrdunk",
                    ]
                ),
            }
        )

    # 5) live SNK trades for bound ids
    if args.write and args.with_trades:
        ids_file = MAP / "qualified-940-snk-ids.txt"
        if ids_file.is_file() and ids_file.stat().st_size > 0:
            harvest = MAP / "snk-psa10-liquidity-full.jsonl"
            # always new out path to avoid incomplete-run guard
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            out = MAP / f"snk-psa10-bound-{stamp}.jsonl"
            steps.append(
                {
                    "step": "snk_market_data",
                    "result": run_py(
                        [
                            "pipelines/snk_market_data.py",
                            "--ids-file",
                            str(ids_file),
                            "--condition",
                            "trading_card_single_psa10",
                            "--out",
                            str(out),
                            "--delay",
                            "0.25",
                        ]
                    ),
                }
            )
            # promote partial if needed
            partial = Path(str(out) + ".partial")
            if partial.is_file() and not out.is_file():
                partial.replace(out)
            if out.is_file():
                steps.append(
                    {
                        "step": "ingest_snk_trades",
                        "result": run_py(
                            [
                                "pipelines/ingest_snk_trades_sales.py",
                                "--harvest",
                                str(out),
                            ]
                        ),
                    }
                )

    after = status()
    steps.append({"step": "status_after", "data": after})

    summary = {
        "write": args.write,
        "recallMin": args.recall_min,
        "withTrades": args.with_trades,
        "before": before,
        "after": after,
        "steps": [
            {
                "step": s["step"],
                "exit": (s.get("result") or {}).get("exit"),
                "json": (s.get("result") or {}).get("json"),
            }
            for s in steps
            if s["step"] not in {"status_before", "status_after"}
        ],
        "delta": {
            k: (after or {}).get(k, 0) - (before or {}).get(k, 0)
            for k in (
                "snk_id",
                "ebay_id",
                "snk_price",
                "ebay_price",
                "sale_any",
                "sale_30d",
                "sale_7d",
                "any_price",
            )
        },
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    rep = REPORT_DIR / f"full_volume_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    rep.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    print("report", rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
