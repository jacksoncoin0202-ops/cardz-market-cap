#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove `sealed_daily.py release / add-product / set-product` change the catalog only as meant, and never
without a provenance line.

2026-09-23: M6a (on sale 2026-09-16) and OP-17 JP still sat at 'unreleased' in catalog_sealed_product.
Nothing in this tree flipped status, and Yahoo sold search, price triage and gaps skip unreleased rows.
  - release_due is the one rule behind scan's releaseDue list and the flip: unreleased, month has come.
  - a future month, an active row, an empty month or an unknown SKU is refused with no UPDATE.
  - catalog-changes.jsonl already holds the line when the commit lands; an UPDATE that matched
    nothing commits nothing and logs nothing.
Same day: nothing in this tree could add a box (the seed script lives in the read-only tree) or fix a wrong
catalog fact. OP-17 JP's name_jp '世界最強の戦士達' (official: 世界最強の戦士) kept all its Yahoo sales out.
  - add-product inserts 'unreleased' only, refuses a known SKU, a bad month and an unknown group/kind.
  - set-product updates only the facts that differ; a JP name moves the Yahoo hint built from the old name.
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


class Script(Cursor):
    """fetchone answers in order; each INSERT/UPDATE matches the next count in `matched`."""

    def __init__(self, answers, matched=()):
        super().__init__(None)
        self.answers, self.counts, self.lastrowid = list(answers), list(matched), 357

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        self.sql.append((sql, params))
        if sql.startswith(("INSERT", "UPDATE")):
            self.rowcount = self.counts.pop(0) if self.counts else 1

    def fetchone(self):
        return self.answers.pop(0) if self.answers else None


def release(row, matched=1):
    cur = Cursor(dict(row) if row else None, matched)
    conn = Conn(cur)
    sealed_operator.db = lambda: conn
    try:
        doc, err = sealed_operator.cmd_sealed_release(sku=(row or {}).get("slug", "nope"), actor="daddy", note="n"), None
    except SystemExit as exc:
        doc, err = None, str(exc)
    return doc, err, cur, conn


def call(fn, cur, **kwargs):
    conn = Conn(cur)
    sealed_operator.db = lambda: conn
    try:
        doc, err = fn(**kwargs), None
    except SystemExit as exc:
        doc, err = None, str(exc)
    return doc, err, conn


def updates(cur):
    return [s for s in cur.sql if s[0].startswith("UPDATE")]


def writes(cur):
    return [s for s in cur.sql if s[0].startswith(("INSERT", "UPDATE"))]


OP18 = dict(game="optcg", lang="jp", set_code="OP-18", product_kind="booster-box", print_wave="std",
            name_en="The Dominance of God", name_jp="神の支配", release_month="2026-11", packs_per_box=24,
            official_url="https://one-piece.com/news/81629/index.html", actor="claude", note="one-piece.com news 81629")
OP17_ROW = {"id": 36, "sku_id": "optcg:jp:OP-17:booster-box:std", "lang": "jp", "group_code": "optcg-jp", "print_wave": "std",
            "name_en": "The World's Strongest Warriors", "name_jp": "世界最強の戦士達", "release_month": "2026-08",
            "packs_per_box": 24, "official_url": "https://en.onepiece-cardgame.com/products/boosters/op17.php"}
OP17_OLD_Q = ("https://auctions.yahoo.co.jp/closedsearch/closedsearch?p=%E3%83%AF%E3%83%B3%E3%83%94%E3%83%BC%E3%82%B9%E3%82%AB"
              "%E3%83%BC%E3%83%89%20%E4%B8%96%E7%95%8C%E6%9C%80%E5%BC%B7%E3%81%AE%E6%88%A6%E5%A3%AB%E9%81%94%20BOX&va=%E3%83%AF"
              "%E3%83%B3%E3%83%94%E3%83%BC%E3%82%B9%E3%82%AB%E3%83%BC%E3%83%89%20%E4%B8%96%E7%95%8C%E6%9C%80%E5%BC%B7%E3%81%AE"
              "%E6%88%A6%E5%A3%AB%E9%81%94%20BOX")  # the hint in the DB on 2026-09-23, copied byte for byte


def catalog_tests() -> None:
    add = sealed_operator.cmd_sealed_add_product
    assert sealed_operator.sku_slug("ptcg:jp:SM3+:booster-box:std") == "ptcg-jp-sm3plus-booster-box-std"
    cur = Script([{"n": 40}, None])
    before = LOG.read_text(encoding="utf-8")
    doc, err, conn = call(add, cur, **OP18)
    assert err is None and doc["sku"] == "optcg:jp:OP-18:booster-box:std" and doc["slug"] == "optcg-jp-op-18-booster-box-std", (err, doc)
    ins = writes(cur)
    assert ins and "'unreleased'" in ins[0][0], "a new box must go in unreleased: %r" % ins
    assert ins[0][1][:9] == ("optcg:jp:OP-18:booster-box:std", "optcg-jp-op-18-booster-box-std", "optcg", "jp", "optcg-jp",
                             "OP-18", "The Dominance of God", "神の支配", "2026-11"), ins[0][1]
    assert len(ins) == 2 and ins[1][1][0] == 357 and ins[1][1][1] == OP18["official_url"] and len(ins[1][1][2]) == 64, \
        "official hint for image harvest: %r" % ins
    assert len(conn.commits) == 1 and "OP-18" in conn.commits[0][len(before):], "the add line must be on disk before the commit"
    assert not sealed_operator.release_due({"status": doc["status"], "release_month": doc["releaseMonth"]}, MONTH), \
        "a November box added in September is not due yet"
    print("POSITIVE_OK add-product inserts one unreleased row plus its official hint; its line is written before the commit")

    for answers, kwargs, why in (([{"n": 40}, {"sku_id": "optcg:jp:OP-18:booster-box:std"}], OP18, "SKU already there"),
                                 ([{"n": 0}, None], {**OP18, "lang": "jpn"}, "unknown group"),
                                 ([{"n": 40}, None], {**OP18, "release_month": "2026-11-22"}, "day in the month"),
                                 ([{"n": 40}, None], {**OP18, "release_month": "2026-13"}, "month 13"),
                                 ([{"n": 40}, None], {**OP18, "packs_per_box": 0}, "0 packs")):
        before = LOG.read_text(encoding="utf-8")
        cur = Script(answers)
        doc, err, conn = call(add, cur, **kwargs)
        assert err and doc is None and not writes(cur) and not conn.commits, f"{why}: must be refused, got {doc} {cur.sql}"
        assert LOG.read_text(encoding="utf-8") == before, f"{why}: a refused add must not log"
    print("NEGATIVE_OK add-product refuses a known SKU, an unknown group, a bad month and 0 packs: no write, no log")

    fix = sealed_operator.cmd_sealed_set_product
    before = LOG.read_text(encoding="utf-8")
    cur = Script([dict(OP17_ROW)])
    doc, err, conn = call(fix, cur, sku="optcg-jp-op-17-booster-box-std", actor="claude", note="official op17 page",
                          fields={"name_jp": "世界最強の戦士", "release_month": "2026-08", "name_en": None})
    ups = updates(cur)
    assert err is None and ups[0] == ("UPDATE catalog_sealed_product SET name_jp=%s WHERE id=%s", ("世界最強の戦士", 36)), \
        "only the fact that differs is written: %r" % ((err, ups),)
    assert len(ups) == 2 and ups[1][1][3] == OP17_OLD_Q and "%E9%81%94" not in ups[1][1][0] and ups[1][1][2] == 36, \
        "the Yahoo hint built from the old name must move to the new name: %r" % ups
    assert doc["yahooHint"]["moved"] == 1 and doc["changes"] == {"name_jp": {"from": "世界最強の戦士達", "to": "世界最強の戦士"}}, doc
    assert len(conn.commits) == 1 and "set-product" in conn.commits[0][len(before):], "the fix line must be on disk before the commit"
    print("POSITIVE_OK set-product writes only what differs; OP-17 JP's name fix moves its Yahoo query off '世界最強の戦士達'")

    cur = Script([{**OP17_ROW, "id": 35, "lang": "en", "group_code": "optcg-en"}])
    doc, err, conn = call(fix, cur, sku="optcg-en-op-17-booster-box-std", actor="claude", note="n", fields={"name_jp": "世界最強の戦士"})
    assert err is None and len(updates(cur)) == 1 and "yahooHint" not in doc, "an EN box has no Yahoo hint to move: %r" % cur.sql
    for fields, row, why in (({"name_jp": "世界最強の戦士達"}, OP17_ROW, "no difference"), ({"release_month": "2025-1"}, OP17_ROW, "bad month"),
                             # the row carries a status so that, without the gate, the flip would reach the UPDATE
                             ({"status": "active"}, {**OP17_ROW, "status": "unreleased"}, "status is not a catalog fact"),
                             ({"packs_per_box": 24}, None, "unknown sku")):
        before = LOG.read_text(encoding="utf-8")
        cur = Script([dict(row)] if row else [])
        doc, err, conn = call(fix, cur, sku="x", actor="claude", note="n", fields=fields)
        assert err and doc is None and not writes(cur) and not conn.commits, f"{why}: must be refused, got {doc} {cur.sql}"
        assert LOG.read_text(encoding="utf-8") == before, f"{why}: a refused fix must not log"
    print("NEGATIVE_OK set-product refuses no-op, bad month, non-fact fields and unknown SKUs: no write, no log")


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

    catalog_tests()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
