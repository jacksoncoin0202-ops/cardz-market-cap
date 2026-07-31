#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PM semi-auto orchestrator: Wave1 missing → Wave2 upgrade → Wave3 prefer-SNK.

Runs snk_image_ingest.py as subprocesses so each wave has its own report + checkpoint.
After waves, builds residual queue (reject / needs_review / error) for AI fact-check.

Usage:
  python -X utf8 pipelines\\snk_image_semi_auto.py --write
  python -X utf8 pipelines\\snk_image_semi_auto.py --write --delay 0.25
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
REPORT_DIR = ROOT / "data" / "runtime" / "private-source-map" / "qualified-pool-reports"
INGEST = ROOT / "pipelines" / "snk_image_ingest.py"


def run_wave(wave: str, *, write: bool, limit: int, delay: float, watchlist_only: bool) -> dict:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cmd = [
        PY,
        "-X",
        "utf8",
        str(INGEST),
        "--wave",
        wave,
        "--limit",
        str(limit),
        "--delay",
        str(delay),
    ]
    if write:
        cmd.append("--write")
    if watchlist_only:
        cmd.append("--watchlist-only")
    env = os.environ.copy()
    env["CARDZ_DB_HOST"] = "127.0.0.1"
    # load backend.env into env
    env_path = ROOT / "data" / "runtime" / "config" / "backend.env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip("\r"))

    print(f"\n======== WAVE {wave} START {ts} ========\n", flush=True)
    print("CMD:", " ".join(cmd), flush=True)
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        capture_output=False,
    )
    # pick newest summary for this wave mode
    mode = {"1": "missing", "2": "upgrade", "3": "prefer"}[wave]
    summaries = sorted(REPORT_DIR.glob(f"snk-image-ingest-{mode}-*_summary.json"), reverse=True)
    summary = {}
    if summaries:
        summary = json.loads(summaries[0].read_text(encoding="utf-8"))
    return {
        "wave": wave,
        "exit": proc.returncode,
        "summary": summary,
        "summaryPath": str(summaries[0]) if summaries else None,
    }


def collect_residuals(report_globs: list[str]) -> list[dict]:
    residual: list[dict] = []
    for pattern in report_globs:
        for path in sorted(REPORT_DIR.glob(pattern)):
            if path.name.endswith("_summary.json"):
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("status") in ("reject", "needs_review", "error"):
                    row["_report"] = str(path)
                    residual.append(row)
    return residual


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--delay", type=float, default=0.28)
    parser.add_argument("--limit", type=int, default=2000, help="per-wave cap")
    parser.add_argument("--watchlist-only", action="store_true", help="restrict wave3 to watchlist")
    parser.add_argument("--waves", default="1,2,3", help="comma list e.g. 1,2,3")
    args = parser.parse_args()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    waves = [w.strip() for w in args.waves.split(",") if w.strip()]
    results = []
    for w in waves:
        wl = args.watchlist_only if w == "3" else False
        results.append(
            run_wave(
                w,
                write=args.write,
                limit=args.limit,
                delay=args.delay,
                watchlist_only=wl,
            )
        )

    # residual from today's reports
    residual = collect_residuals(
        [
            "snk-image-ingest-missing-*.jsonl",
            "snk-image-ingest-upgrade-*.jsonl",
            "snk-image-ingest-prefer-*.jsonl",
        ]
    )
    # de-dupe by snkId keep last
    by_key: dict[str, dict] = {}
    for r in residual:
        key = f"{r.get('snkId')}:{r.get('variantId')}"
        by_key[key] = r
    residual_u = list(by_key.values())

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = {
        "ts": ts,
        "write": args.write,
        "waves": results,
        "residualCount": len(residual_u),
        "residualByStatus": {},
        "residual": residual_u[:200],  # cap in summary; full file separate
    }
    for r in residual_u:
        st = r.get("status") or "?"
        out["residualByStatus"][st] = out["residualByStatus"].get(st, 0) + 1

    full_path = REPORT_DIR / f"snk-image-semi-auto-{ts}.json"
    residual_path = REPORT_DIR / f"snk-image-residual-{ts}.jsonl"
    full_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    with residual_path.open("w", encoding="utf-8") as f:
        for r in residual_u:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

    print("\n======== SEMI-AUTO DONE ========", flush=True)
    print(json.dumps({k: out[k] for k in ("ts", "write", "residualCount", "residualByStatus")}, indent=2))
    print("report:", full_path)
    print("residual:", residual_path)
    for r in results:
        s = r.get("summary") or {}
        print(
            f"wave{r['wave']}: exit={r['exit']} written={s.get('written')} "
            f"reject={s.get('reject')} needs_review={s.get('needs_review')} error={s.get('error')} "
            f"targets={s.get('targets')}"
        )
    return 0 if all(r["exit"] == 0 for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
