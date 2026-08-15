#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BOX (sealed) daily entrypoint for the fe-db chain. No operator_control.py edits.
  python -X utf8 pipelines/sealed_daily.py collect --adapter sealed_pc
  python -X utf8 pipelines/sealed_daily.py compose
  python -X utf8 pipelines/sealed_daily.py export --output data/public/box-subset.json
"""
from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("collect"); p.add_argument("--adapter", required=True); p.add_argument("--allow-candidates", action="store_true")
    sub.add_parser("compose")
    e = sub.add_parser("export"); e.add_argument("--output", type=Path, required=True); e.add_argument("--include-candidates", action="store_true")
    a = ap.parse_args()
    py = sys.executable
    if a.cmd == "collect":
        cmd = [py, "-X", "utf8", "-u", str(ROOT / "pipelines" / "sealed_collect.py"), "incr", "--adapter", a.adapter]
        if a.allow_candidates: cmd.append("--allow-candidates")
        return subprocess.run(cmd, cwd=str(ROOT)).returncode
    if a.cmd == "compose":
        return subprocess.run([py, "-X", "utf8", "-u", str(ROOT / "pipelines" / "sealed_price_compose.py"), "--write"], cwd=str(ROOT)).returncode
    import sealed_operator
    sealed_operator.cmd_export_sealed_subset(output=a.output, include_candidates=a.include_candidates)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
