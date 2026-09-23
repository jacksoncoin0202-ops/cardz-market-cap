#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove `sealed_daily.py release` flips only a due SKU, and never without a provenance line.

2026-09-23: M6a (on sale 2026-09-16) and OP-17 JP still sat at 'unreleased' in catalog_sealed_product.
Nothing in this tree flipped status, and Yahoo sold search, price triage and gaps skip unreleased rows.
  - release_due is the one rule behind scan's releaseDue list and the flip: unreleased, month has come.
  - a future month, an active row, an empty month or an unknown SKU is refused with no UPDATE.
  - catalog-changes.jsonl already holds the line when the commit lands; an UPDATE that matched
    nothing commits nothing and logs nothing.
No MySQL, no network: db / load_env / subprocess.run are faked.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import sealed_operator  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="sealed-release-"))
LOG = TMP / "catalog-changes.jsonl"
MONTH = "2026-09"
M6A = {"id": 356, "sku_id": "ptcg:jp:m6a:booster-box:std", "slug": "ptcg-jp-m6a-booster-box-std", "group_code": "ptcg-jp",
       "status": "unreleased", "release_month": "2026-09"}
OP17 = {"id": 36, "sku_id": "optcg:jp:op-17:booster-box:std", "slug": "optcg-jp-op-17-booster-box-std", "group_code": "optcg-jp",
        "status": "unreleased", "release_month": "2026-08"}
EB05 = {"id": 46, "sku_id": "optcg:jp:eb-05:booster-box:std", "slug": "optcg-jp-eb-05-booster-box-std", "group_code": "optcg-jp",
        "status": "unreleased", "release_month": "2026-10"}
LIVE = {"id": 1, "sku_id": "ptcg:jp:sv1:booster-box:std", "slug": "ptcg-jp-sv1-booster-box-std", "group_code": "ptcg-jp",
        "status": "active", "release_month": "2023-01"}
UNDATED = {**EB05, "id": 47, "sku_id": "x:undated", "slug": "x-undated", "release_month": ""}


class Cursor:
    def __init__(self, row, matched=1):
        self.row, self.matched, self.rowcount, self.sql = row, matched, 0, []

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        self.sql.append((sql, params))
        if sql.startswith("UPDATE"):
            self.rowcount = self.matched

    def fetchone(self):
        return self.row


class Conn:
    def __init__(self, cur):
        self.cur, self.commits, self.closed = cur, [], False

    def cursor(self):
        return self.cur

    def commit(self):
        # what the provenance log held at the moment the flip became durable
        self.commits.append(LOG.read_text(encoding="utf-8") if LOG.is_file() else "")

    def close(self):
        self.closed = True


def release(row, matched=1):
    cur = Cursor(dict(row) if row else None, matched)
    conn = Conn(cur)
    sealed_operator.db = lambda: conn
    try:
        doc, err = sealed_operator.cmd_sealed_release(sku=(row or {}).get("slug", "nope"), actor="daddy", note="n"), None
    except SystemExit as exc:
        doc, err = None, str(exc)
    return doc, err, cur, conn


def updates(cur):
    return [s for s in cur.sql if s[0].startswith("UPDATE")]


def main() -> int:
    sealed_operator.load_env = lambda: None
    sealed_operator.OUT_DIR = TMP
    sealed_operator.current_month = lambda: MONTH
    assert len(MONTH) == 7, "catalog release_month is YYYY-MM"

    due = sealed_operator.release_due
    assert due(M6A, MONTH) and due(OP17, MONTH), "a box whose month is now or past is due"
    assert not due(EB05, MONTH), "a box whose month is still ahead is not due"
    assert not due(LIVE, MONTH), "an active box is not due again"
    assert not due(UNDATED, MONTH), "no release month, no flip"
    print("POSITIVE_OK release_due: unreleased and month <= now; future / active / undated are not due")

    doc, err, cur, conn = release(M6A)
    assert err is None and doc["sku"] == M6A["sku_id"] and (doc["from"], doc["to"]) == ("unreleased", "active"), (err, doc)
    assert updates(cur) == [("UPDATE catalog_sealed_product SET status='active' WHERE id=%s AND status='unreleased'", (356,))], cur.sql
    lines = [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1 and (lines[0]["sku"], lines[0]["actor"], lines[0]["note"]) == (M6A["sku_id"], "daddy", "n"), lines
    assert len(conn.commits) == 1 and M6A["sku_id"] in conn.commits[0], \
        "the provenance line must be on disk before the flip commits: %r" % conn.commits
    assert conn.closed
    print("POSITIVE_OK a due SKU flips to active with one UPDATE; its catalog-changes line is written before the commit")

    for row, why in ((EB05, "month still ahead"), (LIVE, "already active"), (UNDATED, "no release month"), (None, "unknown sku")):
        before = LOG.read_text(encoding="utf-8")
        doc, err, cur, conn = release(row)
        assert err and doc is None, f"{why}: must be refused, got {doc}"
        assert not updates(cur) and not conn.commits and conn.closed, f"{why}: refused, yet {cur.sql} / commits {conn.commits}"
        assert LOG.read_text(encoding="utf-8") == before, f"{why}: a refused release must not log a change"
    print("NEGATIVE_OK future month / active / undated / unknown SKU are refused: no UPDATE, no commit, no log line")

    before = LOG.read_text(encoding="utf-8")
    doc, err, cur, conn = release(OP17, matched=0)
    assert err and "expected 1" in err and not conn.commits, (err, conn.commits)
    assert LOG.read_text(encoding="utf-8") == before, "an UPDATE that matched nothing must not log a change"
    print("NEGATIVE_OK an UPDATE that matched no row commits nothing and logs nothing")

    rows = [M6A, OP17, EB05, LIVE, UNDATED]
    sealed_operator._products = lambda cur: [dict(r) for r in rows]
    sealed_operator._bind_counts = lambda cur: {}
    sealed_operator.db = lambda: Conn(Cursor(None))
    sealed_operator.subprocess.run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    listed = [d["sku"] for d in sealed_operator.cmd_sealed_scan()["releaseDue"]]
    assert listed == [r["sku_id"] for r in rows if due(r, MONTH)] == [M6A["sku_id"], OP17["sku_id"]], \
        "scan's releaseDue must be exactly what release would accept: %r" % listed
    print("POSITIVE_OK scan lists as releaseDue exactly the SKUs release accepts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
