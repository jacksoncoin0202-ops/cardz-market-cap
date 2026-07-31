# -*- coding: utf-8 -*-
"""Expand pipelines/gemrate_ids.txt to FULL exact GemRate identities in DB.

Daddy: GemRate incremental must be comprehensive, not only ~900 FE cards.

Usage:
  python -X utf8 tools/expand_gemrate_ids_full.py
  python -X utf8 tools/expand_gemrate_ids_full.py --run-daily
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pipelines"))

from qualified_pool_operator import db, load_env

IDS = ROOT / "pipelines" / "gemrate_ids.txt"
BACKUP = ROOT / "pipelines" / "gemrate_ids.txt.bak_before_full"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-daily", action="store_true", help="after expand, run gemrate daily")
    args = ap.parse_args()
    load_env()
    cur = db().cursor()
    cur.execute(
        """
        SELECT DISTINCT external_entity_id
        FROM catalog_source_identity
        WHERE source_code='gemrate'
          AND match_status='exact'
          AND external_entity_id IS NOT NULL
          AND CHAR_LENGTH(external_entity_id) >= 32
        ORDER BY external_entity_id
        """
    )
    ids = [str(r["external_entity_id"]).strip() for r in cur.fetchall()]
    old = set()
    if IDS.is_file():
        old = {
            ln.strip()
            for ln in IDS.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        }
        if not BACKUP.is_file():
            BACKUP.write_text(IDS.read_text(encoding="utf-8"), encoding="utf-8")
    merged = sorted(set(ids) | old)
    IDS.write_text("\n".join(merged) + "\n", encoding="utf-8")
    print(
        f"gemrate_ids: was {len(old)} → now {len(merged)} "
        f"(from DB exact {len(ids)}, union file)"
    )
    if args.run_daily:
        env = dict(**__import__("os").environ)
        # load_env already set process env
        cmd = [
            sys.executable,
            "-X",
            "utf8",
            str(ROOT / "pipelines" / "gemrate_source.py"),
            "daily",
            "--ids-file",
            str(IDS),
        ]
        print("starting:", " ".join(cmd), flush=True)
        # non-blocking? user wants parallel - run with long timeout as child
        r = subprocess.run(cmd, cwd=str(ROOT), env=env)
        return r.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
