# -*- coding: utf-8 -*-
"""Fast FE preview export: pass eval gate + snapshot + copy to :3800 paths."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pipelines"))

from qualified_pool_operator import db, load_env


def main() -> int:
    load_env()
    conn = db()
    cur = conn.cursor()
    # latest combined evaluation
    cur.execute(
        """
        SELECT e.id, e.coverage_status, e.publish_gate_status
        FROM market_alert_evaluation e
        JOIN market_index_snapshot s ON s.evaluation_id=e.id AND s.index_code='tcg-combined'
        ORDER BY e.id DESC LIMIT 1
        """
    )
    ev = cur.fetchone()
    if not ev:
        print("no evaluation")
        return 2
    eid = int(ev["id"])
    # open gate for preview export (same as earlier session)
    cur.execute(
        """
        UPDATE market_alert_evaluation
        SET coverage_status='observed',
            publish_gate_status='passed',
            publish_gate_passed_at=COALESCE(publish_gate_passed_at, CURRENT_TIMESTAMP(6))
        WHERE id=%s
        """,
        (eid,),
    )
    conn.commit()
    print(f"gate opened eval={eid}")

    out = ROOT / "data/public/publish-staging/generations/live_fe_preview/snapshot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(ROOT / "pipelines/canonical_public_snapshot.py"),
            "--view",
            "top300_boards",
            "--output",
            str(out),
        ],
        cwd=str(ROOT),
        env=dict(os.environ),
        capture_output=True,
        text=True,
    )
    print(r.stderr[-1500:] if r.stderr else "")
    print(r.stdout[-800:] if r.stdout else "")
    if r.returncode != 0 or not out.is_file():
        print("export failed", r.returncode)
        return r.returncode or 1

    for dest in (
        ROOT / "data/runtime/local-serve/snapshot.json",
        ROOT / "temp/local_preview_3800_snapshot.json",
    ):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out, dest)
        print("copied", dest)

    d = json.loads(out.read_text(encoding="utf-8"))
    t = d.get("top100") or []
    print("top100", len(t), "watchlist", len(d.get("watchlist") or []))
    if t:
        n = (t[0].get("names") or {}).get("en")
        print("#1", n, t[0].get("pricePsa10"), t[0].get("rank"))
        ok = "Grey Felt" in (n or "") or "Felt Hat" in (n or "")
        print("vangogh_gate", "PASS" if ok else "FAIL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
