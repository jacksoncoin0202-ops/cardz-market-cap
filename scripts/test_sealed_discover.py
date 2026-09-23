from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipelines"))

import sealed_fullname_backfill as fullname  # noqa: E402
import sealed_live_qualify as live_qualify  # noqa: E402
import sealed_pc_inventory_ingest as pc_ingest  # noqa: E402
from sealed_discover_lib import (  # noqa: E402
    expand_pc_image_sizes,
    grammar_full_name_en,
    grammar_full_name_ja,
    insert_candidate_bind,
    is_pc_box_url,
    match_pc_inventory,
    parse_pc_category_consoles,
    parse_pc_games_table,
    parse_pc_usd,
    pc_cover_urls,
    pc_slug_variants,
    pick_pc_inventory_box,
    same_item_ids,
    score_snk_box,
    yahoo_jp_query,
    yahoo_query_needs_rewrite,
)


def test_snk_score_jp_quoted_set():
    sku = {
        "game": "ptcg",
        "lang": "jp",
        "name_en": "Triplet Beat",
        "name_jp": "強化拡張パック トリプレットビート",
        "print_wave": "std",
    }
    score, reason = score_snk_box(
        sku,
        'Pokemon Card Game Scarlet & Violet Enhanced Expansion Pack "Triplet Beat" Box',
        "ポケモンカードゲーム スカーレット&バイオレット 強化拡張パック「トリプレットビート」ボックス",
    )
    assert reason == "ok"
    assert score >= 0.5


def test_snk_rejects_promo_single():
    sku = {"game": "optcg", "lang": "jp", "name_en": "Romance Dawn", "name_jp": "ロマンスドーン", "print_wave": "wave2"}
    score, reason = score_snk_box(
        sku,
        "Cavendish C-P [OP01-008] (Booster Pack ROMANCE DAWN)",
        "キャベンディッシュ C-P [OP01-008] (ブースターパック ロマンスドーン BOX封入特典)",
    )
    assert reason == "not_box"
    assert score == 0.0


def test_snk_rejects_en_for_jp_sku():
    sku = {"game": "optcg", "lang": "jp", "name_en": "Romance Dawn", "name_jp": "ロマンスドーン", "print_wave": "std"}
    score, reason = score_snk_box(
        sku,
        "ONE PIECE Card Game Booster Pack Romance Dawn EN Box",
        "ワンピースカードゲーム ブースターパック ロマンスドーン 英語版 ボックス",
    )
    assert reason == "lang_mismatch"
    assert score == 0.0


def test_pc_cover_prefers_large_size():
    html = '''<div class="cover"><img src="https://storage.googleapis.com/images.pricecharting.com/kkqoyg3d24fssskh/240.jpg"></div>'''
    urls = pc_cover_urls(html)
    assert urls[0].endswith("/1600.jpg")
    assert expand_pc_image_sizes(urls[-1])[0].endswith("/1600.jpg")


def test_pc_box_url_and_slugs():
    assert is_pc_box_url("https://www.pricecharting.com/game/one-piece-romance-dawn/booster-box")
    assert not is_pc_box_url("https://www.pricecharting.com/game/one-piece-romance-dawn/elite-trainer-box")
    variants = pc_slug_variants("one-piece-japanese-romance-dawn", "wave1")
    assert variants[0].endswith("/booster-box-1st-edition")


def test_fullname_grammar_never_empty():
    product = {
        "game": "optcg",
        "lang": "jp",
        "name_en": "Romance Dawn",
        "name_jp": "ロマンスドーン",
        "product_kind": "booster-box",
        "print_wave": "wave1",
    }
    assert "ロマンスドーン" in grammar_full_name_ja(product)
    assert "1st Edition" in grammar_full_name_en(product)


def test_pc_games_table_is_row_bounded():
    html = """
    <link rel="canonical" href="https://www.pricecharting.com/console/one-piece-romance-dawn">
    <table id="games_table"><tbody>
    <tr id="product-1"><td class="title"><a href="/game/one-piece-romance-dawn/nami-op01-016">Nami OP01-016</a></td>
    <td class="price numeric used_price"><span class="js-price">$10.00</span></td></tr>
    <tr id="product-2"><td class="title"><a href="/game/one-piece-romance-dawn/booster-box">Booster Box </a></td>
    <td class="price numeric used_price"><span class="js-price">$1,456.44</span></td></tr>
    </tbody></table>
    """
    rows = parse_pc_games_table(html)
    assert [r["kind"] for r in rows] == ["card", "box"]
    assert rows[1]["hasPrice"] and rows[1]["ungraded"] == "$1,456.44"


def test_pc_category_skips_starter_and_nav_noise():
    html = """
    <a href="/console/nes">Nintendo NES</a>
    <a href="/console/one-piece-romance-dawn">One Piece OP01</a>
    <a href="/console/one-piece-japanese-starter-deck-1-straw-hat-crew">Japanese Starter Deck 1</a>
    """
    consoles = parse_pc_category_consoles(html)
    slugs = {c["slug"]: c["kind"] for c in consoles}
    assert slugs["one-piece-romance-dawn"] == "en-set"
    assert slugs["one-piece-japanese-starter-deck-1-straw-hat-crew"] == "skip"
    assert "nes" not in slugs


def test_pc_inventory_match_wave_and_usd():
    assert parse_pc_usd("$1,456.44") == 1456.44
    sku_w1 = {"game": "optcg", "lang": "en", "name_en": "Romance Dawn", "set_code": "OP-01", "print_wave": "wave1", "product_kind": "booster-box"}
    boxes = [
        {"slug": "booster-box", "title": "Booster Box", "console": "one-piece-romance-dawn", "consoleTitle": "One Piece OP01"},
        {"slug": "booster-box-blue-bottom", "title": "Booster Box [Blue Bottom]", "console": "one-piece-romance-dawn"},
    ]
    assert pick_pc_inventory_box(sku_w1, boxes)["slug"] == "booster-box-blue-bottom"
    by_console = {"one-piece-romance-dawn": boxes, "pokemon-korean-abyss-eye": [{"slug": "booster-box", "title": "Booster Box"}]}
    picked = match_pc_inventory(sku_w1, by_console, "https://www.pricecharting.com/console/one-piece-romance-dawn")
    assert picked and picked["slug"] == "booster-box-blue-bottom"
    assert match_pc_inventory({**sku_w1, "lang": "jp"}, by_console, "") is None


def test_yahoo_rewrite_wave_english():
    url = "https://auctions.yahoo.co.jp/closedsearch/closedsearch?p=ROMANCE+DAWN+Wave+1+BOX"
    assert yahoo_query_needs_rewrite(url, "jp")
    query = yahoo_jp_query("ロマンスドーン", "Romance Dawn", "wave1")
    assert "初版" in query and "BOX" in query


class BindCursor:
    """catalog_sealed_source_identity in memory (all rows snkrdunk). The item lookup answers only the ids the query
    names, so a lookup that leaves out a spelling misses that row the way MySQL would; every statement recorded."""

    def __init__(self, *rows):
        self.rows, self.sql, self.last = [dict(r) for r in rows], [], []

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        self.sql.append(sql)
        if sql.startswith("SELECT") and "external_entity_id IN" in sql:
            ids = params[1:]
            self.last = [r for r in self.rows if r["external_entity_id"] in ids]
        elif sql.startswith("SELECT") and "WHERE sealed_id=%s" in sql:
            self.last = [r for r in self.rows if r["sealed_id"] == params[0] and r["match_status"] != "rejected"]
        elif sql.startswith("SELECT"):
            raise AssertionError(f"unexpected lookup: {sql}")

    def fetchone(self):
        return self.last[0] if self.last else None

    def fetchall(self):
        return list(self.last)


def _row(sealed_id, ext, status):
    return {"sealed_id": sealed_id, "external_entity_id": ext, "match_status": status}


def _bind(cur, sealed_id, ext="apparels:552991"):
    return insert_candidate_bind(cur, source="snkrdunk", external_id=ext, sealed_id=sealed_id,
                                 url="https://snkrdunk.com/" + ext.replace(":", "/"), note="{}", origin="search")


def _writes(cur):
    return [s for s in cur.sql if s.startswith(("UPDATE", "INSERT"))]


def test_candidate_bind_never_reopens_a_reject():
    # 2026-09-23: the weekly SNK search found apparels:552991 (a DIESEL T-shirt) for BW1B again and flipped its
    # rejected row back to candidate, one bulk accept away from pricing a box off a T-shirt.
    cur = BindCursor(_row(307, "apparels:552991", "rejected"))
    status = _bind(cur, 307)
    assert status == "already_rejected" and not _writes(cur), "a rejected item was reopened: %r %r" % (status, cur.sql)
    cur = BindCursor(_row(307, "apparels:552991", "candidate"))
    assert _bind(cur, 307) == "updated" and cur.sql[-1].startswith("UPDATE"), cur.sql
    cur = BindCursor()
    assert _bind(cur, 356, "apparels:881421") == "inserted" and cur.sql[-1].startswith("INSERT"), cur.sql
    cur = BindCursor(_row(41, "apparels:767625", "exact"))
    assert _bind(cur, 45, "apparels:767625") == "conflict_exact" and len(cur.sql) == 1, cur.sql


def test_candidate_bind_sees_every_snk_spelling():
    # 2026-09-23: SNK's adapter fetches /v1/apparels/{itemId} whatever the prefix. OP-01 EN (id 1) held
    # trading-cards:136031, the JP box OP-01 JP (id 2) holds as apparels:136031; six boxes sat on two SKUs each.
    assert same_item_ids("snkrdunk", "trading-cards:136031") == ("trading-cards:136031", "apparel-groups:136031", "apparels:136031")
    assert same_item_ids("pricecharting", "one-piece-romance-dawn/booster-box") == ("one-piece-romance-dawn/booster-box",)
    cur = BindCursor(_row(2, "apparels:136031", "exact"))
    status = _bind(cur, 1, "trading-cards:136031")
    assert status == "conflict_exact" and not _writes(cur), \
        "OP-01 EN took the OP-01 JP box under its other spelling: %r %r" % (status, cur.sql)
    cur = BindCursor(_row(307, "apparels:552991", "rejected"))
    status = _bind(cur, 307, "trading-cards:552991")
    assert status == "already_rejected" and not _writes(cur), "a reject must hold under every spelling: %r %r" % (status, cur.sql)
    cur = BindCursor(_row(356, "apparels:881421", "candidate"))
    status = _bind(cur, 356, "trading-cards:881421")
    assert status == "already_bound" and not _writes(cur), "one item is one row per SKU: %r %r" % (status, cur.sql)
    # Another SKU's reject rules on that SKU only; the row still owns its key, so that exact id cannot be added.
    cur = BindCursor(_row(242, "apparels:58920", "rejected"))
    status = _bind(cur, 999, "apparels:58920")
    assert status == "conflict_other" and not _writes(cur), "that key is another SKU's row: %r %r" % (status, cur.sql)
    cur = BindCursor(_row(242, "apparels:58920", "rejected"))
    assert _bind(cur, 999, "trading-cards:58920") == "inserted" and cur.sql[-1].startswith("INSERT"), cur.sql


def test_candidate_bind_never_demotes_an_accept():
    # 2026-09-23 a scan's PC inventory ingest walks every SKU; it found OP-02 EN's accepted box again and set the row
    # back to candidate (193 accepted PC binds), overwriting the note that named the product.
    for source, ext in (("pricecharting", "one-piece-paramount-war/booster-box"), ("snkrdunk", "trading-cards:136031")):
        cur = BindCursor(_row(5, ext, "exact"))
        status = insert_candidate_bind(cur, source=source, external_id=ext, sealed_id=5, url="https://x/" + ext,
                                       note="{}", origin="console-inventory")
        assert status == "already_exact" and not _writes(cur), \
            "an accepted bind was demoted to candidate: %r %r" % (status, cur.sql)


class NameCursor:
    """Answers the full-name loaders by table: source freezes, then identity rows of the queried source."""

    def __init__(self, freezes, idents):
        self.freezes, self.idents, self.last = freezes, idents, []

    def execute(self, sql, params=()):
        if "operator_sealed_binding_freeze" in sql:
            self.last = self.freezes
        else:
            source = "snkrdunk" if "'snkrdunk'" in sql else "pricecharting"
            self.last = [r for r in self.idents if r["source"] == source]

    def fetchall(self):
        return self.last


def test_fullname_only_from_accepted_binds():
    # 2026-09-23 dry run: the backfill would have named BW1B after a DIESEL T-shirt and BW1W after the Shiny
    # Collection box, both unreviewed SNK candidates. Only an accepted source bind may name a product.
    def snk(en, ja=""):
        return json.dumps({"snkName": en, "snkLocalized": ja})

    freezes = [
        {"sealed_id": 41, "source_code": "snkrdunk", "external_entity_id": "trading-cards:767625", "acceptance_status": "accepted"},
        {"sealed_id": 69, "source_code": "pricecharting", "external_entity_id": "pokemon-151/booster-box", "acceptance_status": "accepted"},
        {"sealed_id": 308, "source_code": "snkrdunk", "external_entity_id": "apparels:480865", "acceptance_status": "revoked"},
    ]
    idents = [
        {"source": "snkrdunk", "sealed_id": 41, "external_entity_id": "trading-cards:767625", "note": snk("EB-03 EN Box", "EB-03 英語版")},
        {"source": "snkrdunk", "sealed_id": 307, "external_entity_id": "apparels:552991", "note": snk('DIESEL T-BOXT-R29 "BLACK"')},
        {"source": "snkrdunk", "sealed_id": 308, "external_entity_id": "apparels:480865", "note": snk("Shiny Collection 1ED Box")},
        {"source": "pricecharting", "sealed_id": 69, "external_entity_id": "pokemon-151/booster-box",
         "note": json.dumps({"pcName": "151 Booster Box"}), "canonical_url": None},
        {"source": "pricecharting", "sealed_id": 35, "external_entity_id": "yugioh-x/booster-box",
         "note": json.dumps({"pcName": "Yu-Gi-Oh Booster Box"}), "canonical_url": None},
    ]
    cur = NameCursor(freezes, idents)
    accepted = fullname.accepted_binds(cur)
    names = fullname.load_snk_names(cur, accepted), fullname.load_pc_names(cur, accepted)
    assert names == ({41: {"en": "EB-03 EN Box", "ja": "EB-03 英語版", "source": "snkrdunk"}},
                     {69: {"en": "151 Booster Box", "ja": "", "source": "pricecharting"}}), \
        "only an accepted bind may name a product: %r" % (names,)


class QualifyCursor:
    """live-qualify's two reads (products, candidate binds) from fixed rows; every statement recorded."""

    def __init__(self, products, candidates):
        self.products, self.candidates, self.sql, self.last, self.rowcount = products, candidates, [], [], 0

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        self.sql.append((sql, params))
        self.last = (self.products if "FROM catalog_sealed_product" in sql
                     else self.candidates if "WHERE match_status='candidate'" in sql else [])
        self.rowcount = 1 if sql.startswith("UPDATE") else 0

    def fetchall(self):
        return list(self.last)


class QualifyConn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self):
        return self.cur

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_live_qualify_apply_never_accepts():
    # 2026-08-14 live-qualify --apply accepted all 451 sealed source binds and froze 334 box images, none looked at:
    # /box showed a Game Boy jukebox on BW2 and JP boxes behind EN SKUs. OP-06 EN got its JP box from a re-score
    # on the English name alone; BW1B/BW1W got DIESEL T-shirts frozen as box art.
    def product(sid, sku, game, lang, en, jp):
        return {"id": sid, "sku_id": sku, "slug": sku.replace(":", "-").lower(), "game": game, "lang": lang,
                "group_code": f"{game}-{lang}", "set_code": sku.split(":")[2], "name_en": en, "name_jp": jp,
                "print_wave": "std", "product_kind": "booster-box", "status": "active"}

    def cand(sid, ext, en, ja):
        return {"sealed_id": sid, "source_code": "snkrdunk", "external_entity_id": ext, "canonical_url": None,
                "note": json.dumps({"snkName": en, "snkLocalized": ja}), "match_status": "candidate", "resolved": 1}

    products = [product(13, "optcg:en:OP-06:booster-box:std", "optcg", "en", "Wings of the Captain", "双璧の覇者"),
                product(307, "ptcg:jp:BW1B:booster-box:std", "ptcg", "jp", "Black Collection", "ブラックコレクション"),
                product(356, "ptcg:jp:M6a:booster-box:std", "ptcg", "jp", "30th Celebration Collection", "拡張パック 30th CELEBRATION")]
    candidates = [cand(13, "trading-cards:145974", "ONE PIECE Card Game Wings Of The Captain Box", "ワンピースカードゲーム 双璧の覇者 ボックス"),
                  cand(307, "apparels:552991", 'DIESEL T-BOXT-R29 "BLACK"', 'ディーゼル ティーボックスティーアール 29 "ブラック"'),
                  cand(356, "apparels:881421", 'Pokemon Card Game MEGA Expansion Pack "30th CELEBRATION" Box',
                       "ポケモンカードゲームMEGA 拡張パック「30th CELEBRATION」ボックス")]
    cur = QualifyCursor(products, candidates)
    live_qualify.load_env, live_qualify.db = (lambda: None), (lambda: QualifyConn(cur))
    live_qualify.OUT_DIR = Path(tempfile.mkdtemp(prefix="live-qualify-"))
    assert live_qualify.run(apply=True) == 0
    writes = [s for s in cur.sql if not s[0].startswith("SELECT")]
    assert len(writes) == 1 and "SET match_status='rejected'" in writes[0][0] and writes[0][1][2] == "apparels:552991", \
        "live-qualify must never accept or freeze, only reject junk: %r" % (writes,)
    receipt = json.loads((live_qualify.OUT_DIR / "live-qualify-receipt.json").read_text(encoding="utf-8"))
    verdict = {d["ext"]: (d["decision"], d["reason"]) for key in ("rejectSample", "holdSample", "acceptSample") for d in receipt[key]}
    assert verdict["trading-cards:145974"] == ("hold", "snk_lang_mismatch"), "OP-06 EN read its JP box as EN: %r" % (verdict,)
    assert verdict["apparels:881421"][0] == "accept" and receipt["applied"] == {"rejected": 1}, \
        "an accept verdict is listed, never applied: %r" % ((verdict, receipt["applied"]),)


class LiteConn:
    """MySQL-flavoured SQL on in-memory sqlite (%s -> ?), dict rows, so the ingest's own queries run for real.
    close() keeps the data for the asserts."""

    def __init__(self, script):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(script)

    def cursor(self):
        return LiteCursor(self.conn.cursor())

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        pass


class LiteCursor:
    def __init__(self, cur):
        self.cur, self.rowcount = cur, 0

    def execute(self, sql, params=()):
        self.cur.execute(sql.replace("%s", "?"), tuple(params or ()))
        self.rowcount = self.cur.rowcount

    def fetchone(self):
        row = self.cur.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self.cur.fetchall()]


PC_GAME = "https://www.pricecharting.com/game/"
PC_DB = f"""
CREATE TABLE catalog_sealed_product (id INTEGER PRIMARY KEY, sku_id TEXT, game TEXT, lang TEXT, group_code TEXT,
  set_code TEXT, name_en TEXT, print_wave TEXT, product_kind TEXT, status TEXT);
CREATE TABLE catalog_sealed_source_hint (sealed_id INTEGER, source_code TEXT, url TEXT);
CREATE TABLE catalog_sealed_source_identity (source_code TEXT, external_entity_id TEXT, sealed_id INTEGER, canonical_url TEXT,
  match_status TEXT, resolved INTEGER, evidence_sha256 TEXT, note TEXT, PRIMARY KEY (source_code, external_entity_id));
CREATE TABLE operator_sealed_binding_freeze (sealed_id INTEGER, freeze_kind TEXT, source_code TEXT, external_entity_id TEXT,
  acceptance_status TEXT, PRIMARY KEY (sealed_id, freeze_kind, source_code));
INSERT INTO catalog_sealed_product VALUES
  (5, 'optcg:en:OP-02:booster-box:std', 'optcg', 'en', 'optcg-en', 'OP-02', 'Paramount War', 'std', 'booster-box', 'active'),
  (179, 'ptcg:en:JU:booster-box:std', 'ptcg', 'en', 'ptcg-en', 'JU', 'Jungle', 'std', 'booster-box', 'active'),
  (400, 'ptcg:en:ME02:booster-box:std', 'ptcg', 'en', 'ptcg-en', 'ME02', 'Phantasmal Flames', 'std', 'booster-box', 'active');
INSERT INTO catalog_sealed_source_identity VALUES
  ('pricecharting', 'one-piece-paramount-war/booster-box', 5, '{PC_GAME}one-piece-paramount-war/booster-box', 'exact', 1, '', '{{}}'),
  ('pricecharting', 'pokemon-jungle/booster-box-1st-edition', 179, '{PC_GAME}pokemon-jungle/booster-box-1st-edition', 'candidate', 1, '', '{{}}'),
  ('pricecharting', 'pokemon-jungle/booster-box', 179, '{PC_GAME}pokemon-jungle/booster-box', 'candidate', 1, '', '{{}}');
INSERT INTO operator_sealed_binding_freeze VALUES
  (5, 'source', 'pricecharting', 'one-piece-paramount-war/booster-box', 'accepted'),
  (179, 'source', 'pricecharting', 'pokemon-jungle/booster-box-1st-edition', 'accepted');
"""


def test_pc_inventory_prices_only_the_accepted_item():
    # 2026-09-23 a scan's PC inventory ingest priced every SKU it matched: JU EN, frozen on the 1st edition Jungle box,
    # got the unlimited box's price from its candidate row, which /box showed. A candidate is unreviewed.
    def box(console, slug, pid, usd):
        return {"console": console, "consoleTitle": console.replace("-", " ").title(), "slug": slug, "title": slug,
                "href": f"{PC_GAME}{console}/{slug}", "pid": pid, "ungraded": f"${usd:,.2f}"}

    inventory = Path(tempfile.mkdtemp(prefix="pc-inventory-")) / "console-inventory.json"
    inventory.write_text(json.dumps({"boxes": [
        box("one-piece-paramount-war", "booster-box", "p5", 180.0),
        box("pokemon-jungle", "booster-box", "p179u", 13584.68),
        box("pokemon-jungle", "booster-box-1st-edition", "p179f", 14375.0),
        box("pokemon-phantasmal-flames", "booster-box", "p400", 210.0),
    ]}), encoding="utf-8")
    conn, priced = LiteConn(PC_DB), []
    pc_ingest.db = lambda: conn
    pc_ingest.warehouse_sealed = lambda cur, **kw: None
    pc_ingest.upsert_sealed_price = lambda cur, **kw: priced.append((kw["sealed_id"], kw["external_entity_id"], kw["price_usd"]))
    doc = pc_ingest.ingest_inventory(inventory)
    assert doc["statuses"] == {"already_exact": 1, "updated": 1, "inserted": 1}, doc["statuses"]
    assert not [p for p in priced if p[0] == 179], "JU EN took the unlimited Jungle box's price from a candidate: %r" % priced
    assert not [p for p in priced if p[0] == 400], "an unreviewed candidate priced a new SKU: %r" % priced
    assert priced == [(5, "one-piece-paramount-war/booster-box", 180.0)], "the accepted item keeps today's table price: %r" % priced
    cur = conn.cursor()
    cur.execute("SELECT match_status FROM catalog_sealed_source_identity WHERE sealed_id=5")
    assert cur.fetchall() == [{"match_status": "exact"}], "the ingest demoted OP-02 EN's accepted bind"


if __name__ == "__main__":
    ran = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"POSITIVE_OK {name}")
            ran += 1
    if ran < 1:
        raise SystemExit("no test_* functions ran")
    print(f"{ran} sealed discover tests passed")
