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
Same day, accept-binding: six SNK boxes sat accepted on two SKUs each, one per spelling (EB-03 EN held
trading-cards:767625, EB-05 EN apparels:767625, and SNK fetches both as item 767625).
  - a source item another SKU holds live, under any spelling, is refused: no write, exit non-zero.
  - another SKU's reject does not block; a bulk run commits the clean SKUs and still exits non-zero.
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


class AcceptCursor:
    """catalog_sealed_product + catalog_sealed_source_identity in memory for accept-binding. Each lookup answers from
    the query's own ids and its rejected filter, so a query that drops a spelling or the filter shows in the result."""

    def __init__(self, products, binds):
        self.products, self.binds, self.sql, self.last, self.rowcount = products, [dict(b) for b in binds], [], [], 0

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        self.sql.append((sql, params))
        live = "match_status<>'rejected'" in sql
        if sql.startswith("SELECT id, sku_id FROM catalog_sealed_product"):
            self.last = [p for p in self.products if params[0] in (p["sku_id"], p["slug"])]
        elif sql.startswith("SELECT p.id, p.sku_id FROM catalog_sealed_source_identity"):
            ids = {b["sealed_id"] for b in self.binds if b["source_code"] == params[0] and b["match_status"] == "candidate"}
            self.last = [p for p in self.products if p["id"] in ids]
        elif sql.startswith("SELECT external_entity_id FROM catalog_sealed_source_identity"):
            self.last = [b for b in self.binds if (b["sealed_id"], b["source_code"]) == tuple(params)
                         and (not live or b["match_status"] != "rejected")]
        elif sql.startswith("SELECT p.id, p.group_code, p.status, EXISTS"):
            self.last = [{"id": p["id"], "group_code": p.get("group_code", "optcg-en"), "status": p.get("status", "active"),
                          "identity_frozen": p.get("identity_frozen", 1)} for p in self.products if p["id"] in params]
        elif sql.startswith("SELECT p.sku_id FROM catalog_sealed_source_identity"):
            source, *ids, sealed_id = params
            sku = {p["id"]: p["sku_id"] for p in self.products}
            self.last = [{"sku_id": sku[b["sealed_id"]]} for b in self.binds
                         if b["source_code"] == source and b["external_entity_id"] in ids and b["sealed_id"] != sealed_id
                         and (not live or b["match_status"] != "rejected")]
        elif sql.startswith("SELECT"):
            raise AssertionError(f"unexpected lookup: {sql}")

    def fetchone(self):
        return self.last[0] if self.last else None

    def fetchall(self):
        return list(self.last)


EB03_P = {"id": 41, "sku_id": "optcg:en:EB-03:booster-box:std", "slug": "optcg-en-eb-03-booster-box-std"}
EB05_P = {"id": 45, "sku_id": "optcg:en:EB-05:booster-box:std", "slug": "optcg-en-eb-05-booster-box-std"}
M6A_P = {"id": 356, "sku_id": M6A["sku_id"], "slug": M6A["slug"], "group_code": "ptcg-jp"}
OTHER_P = {"id": 999, "sku_id": "ptcg:jp:X:booster-box:std", "slug": "ptcg-jp-x-booster-box-std", "group_code": "ptcg-jp"}
OP02_P = {"id": 5, "sku_id": "optcg:en:OP-02:booster-box:std", "slug": "optcg-en-op-02-booster-box-std"}
OP18_P = {"id": 357, "sku_id": "optcg:jp:OP-18:booster-box:std", "slug": "optcg-jp-op-18-booster-box-std",
          "group_code": "optcg-jp", "status": "unreleased"}
NEW_P = {"id": 358, "sku_id": "optcg:en:OP-18:booster-box:std", "slug": "optcg-en-op-18-booster-box-std", "identity_frozen": 0}
BINDS = [
    {"sealed_id": 41, "source_code": "snkrdunk", "external_entity_id": "trading-cards:767625", "match_status": "exact"},
    {"sealed_id": 45, "source_code": "snkrdunk", "external_entity_id": "apparels:767625", "match_status": "candidate"},
    {"sealed_id": 356, "source_code": "snkrdunk", "external_entity_id": "apparels:881421", "match_status": "candidate"},
    {"sealed_id": 999, "source_code": "snkrdunk", "external_entity_id": "trading-cards:881421", "match_status": "rejected"},
    {"sealed_id": 5, "source_code": "snkrdunk", "external_entity_id": "apparels:500", "match_status": "candidate"},
    {"sealed_id": 357, "source_code": "snkrdunk", "external_entity_id": "apparels:357", "match_status": "candidate"},
    {"sealed_id": 358, "source_code": "snkrdunk", "external_entity_id": "apparels:358", "match_status": "candidate"},
]


def accept_tests() -> None:
    accept = sealed_operator.cmd_sealed_accept_binding
    products = [EB03_P, EB05_P, M6A_P, OTHER_P, OP02_P, OP18_P, NEW_P]
    one = dict(kind="source", source_code="snkrdunk", actor="daddy", note="n", all_resolved=False, group=None)

    cur = AcceptCursor(products, BINDS)
    doc, err, conn = call(accept, cur, sku=EB05_P["slug"], **one)
    assert err and "refused 1" in err and EB03_P["sku_id"] in err and doc is None and not writes(cur), \
        "EB-05 EN accepted the EB-03 EN box under its other spelling: %r" % ((err, cur.sql),)
    print("NEGATIVE_OK accept-binding refuses a source item another SKU holds under another spelling: no write, exit non-zero")

    cur = AcceptCursor(products, BINDS)
    doc, err, conn = call(accept, cur, sku=M6A_P["slug"], **one)
    assert err is None and doc["accepted"] == 1 and doc["refused"] == [], \
        "another SKU's reject of the same item must not block this SKU: %r" % ((err, doc),)
    ws = writes(cur)
    assert ws[0] == ("UPDATE catalog_sealed_source_identity SET match_status='exact' WHERE sealed_id=%s AND source_code=%s "
                     "AND external_entity_id=%s", (356, "snkrdunk", "apparels:881421")), ws
    assert len(ws) == 2 and ws[1][0].startswith("INSERT INTO operator_sealed_binding_freeze") and len(conn.commits) == 1, ws
    print("POSITIVE_OK accept-binding accepts an item no other SKU holds live; another SKU's reject does not block")

    cur = AcceptCursor(products, BINDS)
    doc, err, conn = call(accept, cur, sku=None, **{**one, "all_resolved": True})
    exacts = [s[1] for s in writes(cur) if s[0].startswith("UPDATE")]
    assert err and "refused 4" in err and exacts == [(5, "snkrdunk", "apparels:500")] and len(conn.commits) == 1, \
        "bulk must keep the clean accept, refuse the shared item and exit non-zero: %r" % ((err, exacts, conn.commits),)
    for sku_id, why in ((M6A_P["sku_id"], "ptcg-jp"), (OP18_P["sku_id"], "unreleased"), (NEW_P["sku_id"], "identity not accepted")):
        assert f"{sku_id} bulk refused: {why}" in err, f"bulk accepted {sku_id} ({why}): {err}"
    print("NEGATIVE_OK bulk accept commits the clean SKUs; refuses the shared item, ptcg-jp, unreleased and unreviewed "
          "SKUs and still exits non-zero")


CORRECTIONS_DB = """
CREATE TABLE catalog_sealed_product (id INTEGER PRIMARY KEY, sku_id TEXT, slug TEXT, group_code TEXT, status TEXT);
CREATE TABLE catalog_sealed_source_identity (source_code TEXT, external_entity_id TEXT, sealed_id INTEGER, canonical_url TEXT,
  match_status TEXT, resolved INTEGER, evidence_sha256 TEXT, note TEXT, PRIMARY KEY (source_code, external_entity_id));
CREATE TABLE operator_sealed_binding_freeze (sealed_id INTEGER, freeze_kind TEXT, source_code TEXT, external_entity_id TEXT,
  content_sha256 TEXT, acceptance_status TEXT, actor TEXT, evidence_sha256 TEXT, note TEXT, accepted_at TEXT,
  PRIMARY KEY (sealed_id, freeze_kind, source_code));
CREATE TABLE market_sealed_sale_observation (id INTEGER PRIMARY KEY, sealed_id INTEGER, source_code TEXT, metric_status TEXT);
CREATE TABLE market_sealed_price_observation (id INTEGER PRIMARY KEY, sealed_id INTEGER, source_code TEXT, metric_status TEXT);
CREATE TABLE market_sealed_image_asset (sealed_id INTEGER, image_kind TEXT, content_sha256 TEXT, captured_at TEXT);
INSERT INTO catalog_sealed_product VALUES
  (218, 'ptcg:jp:S10b:booster-box:std', 's10b', 'ptcg-jp', 'active'),
  (48, 'optcg:jp:PRB-01:booster-box:std', 'prb-01', 'optcg-jp', 'active'),
  (50, 'optcg:jp:PRB-02:booster-box:std', 'prb-02', 'optcg-jp', 'active'),
  (13, 'optcg:en:OP-06:booster-box:std', 'op-06-en', 'optcg-en', 'active');
INSERT INTO catalog_sealed_source_identity VALUES
  ('snkrdunk', 'trading-cards:100', 218, NULL, 'exact', 1, '', ''),
  ('snkrdunk', 'apparels:101', 218, NULL, 'candidate', 1, '', ''),
  ('snkrdunk', 'apparels:300', 50, NULL, 'exact', 1, '', ''),
  ('snkrdunk', 'apparels:301', 50, NULL, 'candidate', 1, '', ''),
  ('snkrdunk', 'apparels:400', 48, NULL, 'candidate', 1, '', ''),
  ('snkrdunk', 'trading-cards:400', 13, NULL, 'candidate', 1, '', '');
INSERT INTO operator_sealed_binding_freeze (sealed_id, freeze_kind, source_code, external_entity_id, acceptance_status) VALUES
  (218, 'source', 'snkrdunk', 'apparels:100', 'accepted'),
  (50, 'source', 'snkrdunk', 'apparels:300', 'accepted'),
  (13, 'source', 'snkrdunk', 'apparels:999', 'accepted'),
  (218, 'image', '', 'sha-wrong', 'accepted');
INSERT INTO market_sealed_sale_observation VALUES (1, 179, 'ebay', 'ok'), (2, 179, 'ebay', 'outlier_trimmed'),
  (3, 179, 'ebay', 'rejected_foreign_edition'), (4, 179, 'ebay', 'ok');
INSERT INTO market_sealed_price_observation VALUES (7, 218, 'snkrdunk', 'ok');
INSERT INTO market_sealed_image_asset VALUES (218, 'box_front', 'sha-wrong', '2026-09-01'), (218, 'box_front', 'sha-right', '2026-08-01');
"""


def correction_tests() -> None:
    """2026-09-23 R0/R4: nothing in this tree could take a wrong bind, row or image down short of a delete. The
    commands run their own SQL on sqlite; each change is on disk in catalog-changes.jsonl before its commit."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import re

    from test_sealed_discover import LiteConn, LiteCursor

    class MyCursor(LiteCursor):
        def execute(self, sql, params=()):
            sql = sql.replace("ON DUPLICATE KEY UPDATE", "ON CONFLICT DO UPDATE SET")
            super().execute(re.sub(r"\bVALUES\((\w+)\)", r"excluded.\1", sql), params)

    class Lite(LiteConn):
        commits: list[str] = []

        def cursor(self):
            return MyCursor(self.conn.cursor())

        def commit(self):
            self.commits.append(LOG.read_text(encoding="utf-8") if LOG.is_file() else "")
            super().commit()

    lite = Lite(CORRECTIONS_DB)
    sealed_operator.db = lambda: lite
    q = lite.conn.cursor()

    def rows(sql):
        return [tuple(r) for r in q.execute(sql).fetchall()]

    def run(fn, **kw):
        before, n = LOG.read_text(encoding="utf-8") if LOG.is_file() else "", len(lite.commits)
        try:
            doc, err = fn(actor="claude", note="evidence", **kw), None
        except SystemExit as exc:
            lite.rollback()
            doc, err = None, str(exc)
        if err:
            assert len(lite.commits) == n and (LOG.read_text(encoding="utf-8") if LOG.is_file() else "") == before, \
                f"a refused {fn.__name__} committed or logged: {err}"
        else:
            assert len(lite.commits) == n + 1 and doc["action"] in lite.commits[-1].splitlines()[-1], \
                f"{fn.__name__}: the catalog-changes line must be on disk before the commit"
        return doc, err

    quarantine = sealed_operator.cmd_sealed_quarantine
    doc, err = run(quarantine, table="sale", ids=[1, 2], restore=False)
    assert err is None and rows("SELECT id, metric_status FROM market_sealed_sale_observation WHERE id<3") == \
        [(1, "quarantined"), (2, "quarantined")], (err, doc)
    doc, err = run(quarantine, table="sale", ids=[3, 4], restore=False)
    assert err and "rejected_foreign_edition" in err and rows("SELECT metric_status FROM market_sealed_sale_observation WHERE id=4") == [("ok",)], \
        "a QC-rejected row in the list must refuse the whole call: %r" % err
    doc, err = run(quarantine, table="sale", ids=[1, 4], restore=True)
    assert err and "4" in err, "restore must touch quarantined rows only (never turn a QC reject ok): %r" % err
    doc, err = run(quarantine, table="sale", ids=[1], restore=True)
    assert err is None and rows("SELECT metric_status FROM market_sealed_sale_observation WHERE id=1") == [("ok",)], err
    doc, err = run(quarantine, table="price", ids=[7, 8], restore=False)
    assert err and "missing" in err, "an unknown id must refuse the call: %r" % err
    print("POSITIVE_OK quarantine moves counted rows out and back; a QC reject, a non-quarantined restore or an unknown id "
          "refuses the whole call with no write")

    reject = sealed_operator.cmd_sealed_reject_binding
    doc, err = run(reject, sku="s10b", source_code="snkrdunk", external_id="apparels:100")
    assert err is None and doc["freezeRejected"] and doc["ext"] == ["trading-cards:100"], (err, doc)
    assert rows("SELECT external_entity_id, match_status FROM catalog_sealed_source_identity WHERE sealed_id=218") == \
        [("trading-cards:100", "rejected"), ("apparels:101", "candidate")], "reject must take every spelling of the item, only it"
    assert rows("SELECT acceptance_status FROM operator_sealed_binding_freeze WHERE sealed_id=218 AND freeze_kind='source'") == \
        [("rejected",)], "the freeze naming the rejected item must turn rejected"
    doc, err = run(reject, sku="s10b", source_code="snkrdunk", external_id="apparels:100")
    assert err and "no live" in err, "rejecting twice must refuse: %r" % err
    doc, err = run(reject, sku="op-06-en", source_code="snkrdunk", external_id="apparels:400")
    assert err is None and not doc["freezeRejected"] and rows(
        "SELECT acceptance_status FROM operator_sealed_binding_freeze WHERE sealed_id=13") == [("accepted",)], \
        "a freeze naming another item must stay: %r" % ((err, doc),)
    print("POSITIVE_OK reject-binding rejects every spelling of one item and the freeze only when it names that item")

    move = sealed_operator.cmd_sealed_move_binding
    doc, err = run(move, source_code="snkrdunk", external_id="apparels:300", from_sku="prb-02", to_sku="prb-01")
    assert err is None and doc["freezeRejected"], (err, doc)
    assert rows("SELECT sealed_id, match_status FROM catalog_sealed_source_identity WHERE external_entity_id='apparels:300'") == \
        [(48, "candidate")] and rows("SELECT acceptance_status FROM operator_sealed_binding_freeze WHERE sealed_id=50 "
                                     "AND freeze_kind='source'") == [("rejected",)], "PRB-02's box must move to PRB-01 as a candidate"
    lite.conn.execute("UPDATE catalog_sealed_source_identity SET match_status='rejected' WHERE external_entity_id='trading-cards:400'")
    doc, err = run(move, source_code="snkrdunk", external_id="apparels:400", from_sku="prb-01", to_sku="prb-02")
    assert err and "not on" in err, "an item another SKU also holds (even rejected) must not move: %r" % err
    print("POSITIVE_OK move-binding moves an item held by one SKU as a candidate and rejects the old freeze; "
          "an item on two SKUs is refused")

    accept = sealed_operator.cmd_sealed_accept_binding
    one = dict(sku="prb-01", kind="source", source_code="snkrdunk", actor="claude", note="n", all_resolved=False, group=None)
    try:
        accept(**one, external_id="apparels:777")
        raise AssertionError("accept --ext on an item the SKU does not hold must refuse")
    except SystemExit as exc:
        lite.rollback()
        assert "no live" in str(exc), exc
    accept(**one, external_id="apparels:300")
    assert rows("SELECT external_entity_id, acceptance_status FROM operator_sealed_binding_freeze WHERE sealed_id=48") == \
        [("apparels:300", "accepted")], "accept --ext must freeze the named item"
    print("POSITIVE_OK accept-binding --ext freezes the named item; an item the SKU does not hold is refused")

    revoke = sealed_operator.cmd_sealed_revoke_image
    doc, err = run(revoke, sku="s10b")
    assert err is None and doc["sha"] == "sha-wrong" and rows(
        "SELECT acceptance_status FROM operator_sealed_binding_freeze WHERE sealed_id=218 AND freeze_kind='image'") == [("rejected",)], err
    doc, err = run(revoke, sku="s10b")
    assert err and "no accepted image" in err, err
    accept(**{**one, "sku": "s10b", "kind": "image", "source_code": ""}, external_id="sha-right")
    assert rows("SELECT external_entity_id, acceptance_status FROM operator_sealed_binding_freeze WHERE sealed_id=218 "
                "AND freeze_kind='image'") == [("sha-right", "accepted")], "accept --kind image --ext must take the named sha, not the latest"
    print("POSITIVE_OK revoke-image rejects the image freeze; accept --kind image --ext puts the named asset back")

    add = sealed_operator.cmd_sealed_add_binding
    doc, err = run(add, sku="s10b", source_code="snkrdunk", external_id="apparels:102", url="https://snkrdunk.com/apparels/102")
    assert err and "already_bound" in err, "a SKU with a live bind must reject it before another goes in: %r" % err
    run(reject, sku="s10b", source_code="snkrdunk", external_id="apparels:101")
    doc, err = run(add, sku="s10b", source_code="snkrdunk", external_id="apparels:102", url="https://snkrdunk.com/apparels/102")
    assert err is None and doc["status"] == "inserted", (err, doc)
    doc, err = run(add, sku="s10b", source_code="snkrdunk", external_id="apparels:100", url="https://snkrdunk.com/apparels/100")
    assert err and "already_rejected" in err, "add-binding must not reopen a reject: %r" % err
    doc, err = run(add, sku="s10b", source_code="snkrdunk", external_id="apparels:300", url="https://snkrdunk.com/apparels/300")
    assert err and "conflict" in err, "add-binding must not take an item another SKU holds: %r" % err
    print("POSITIVE_OK add-binding inserts a candidate; a rejected item or one another SKU holds is refused")


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
    accept_tests()
    correction_tests()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
