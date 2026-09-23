#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove sealed_collect stock never copies one SKU's source item into another SKU.

2026-09-23: EB-05 EN was accepted on SNK 'apparels:767625' while EB-03 EN sits on
'trading-cards:767625'. The SNK adapter drops the namespace and GETs /v1/apparels/767625 for
both, so a first stock pull would have written the EB-03 EN box's prices into EB-05 EN.
Seven SNK items were bound to two SKUs each.
  - the fetch key is what the adapter fetches: the SNK itemId with the namespace dropped, else
    the URL (every Yahoo item has externalId 'closedsearch'; its URL carries the query).
  - stock skips a due SKU whose key another SKU also holds and reports it as blocked, also when
    nothing else was due; incr keeps refreshing SKUs that have data and only lists the sharing.
  - a SKU's accepted freeze makes only the item it names pullable: JU EN, frozen on the 1st edition
    Jungle box, also pulled its unlimited-box candidate (the binding query runs for real on sqlite).
No MySQL, no network: the loaders, db and the fetcher are faked.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import sealed_collect as sc  # noqa: E402
from sealed_runtime import load_sealed_bindings  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="sealed-shared-"))
EB03 = {"sealedId": 41, "externalId": "trading-cards:767625", "itemId": 767625, "sku": "optcg:en:EB-03:booster-box:std"}
EB05 = {"sealedId": 45, "externalId": "apparels:767625", "itemId": 767625, "sku": "optcg:en:EB-05:booster-box:std"}
OP17 = {"sealedId": 35, "externalId": "apparels:864395", "itemId": 864395, "sku": "optcg:en:OP-17:booster-box:std"}
OP06_JP = {"sealedId": 14, "externalId": "apparels:145974", "itemId": 145974, "sku": "optcg:jp:OP-06:booster-box:std"}
OP06_EN = {"sealedId": 13, "externalId": "trading-cards:145974", "itemId": 145974, "sku": "optcg:en:OP-06:booster-box:std"}
YAHOO = "https://auctions.yahoo.co.jp/closedsearch/closedsearch?p="
Y1 = {"sealedId": 277, "externalId": "closedsearch", "url": YAHOO + "a", "sku": "ptcg:jp:SM1S:booster-box:std"}
Y2 = {"sealedId": 285, "externalId": "closedsearch", "url": YAHOO + "b", "sku": "ptcg:jp:XY8b:booster-box:std"}
Y3 = {"sealedId": 302, "externalId": "closedsearch", "url": YAHOO + "b", "sku": "ptcg:jp:BW5b:booster-box:std"}
PC_A = {"sealedId": 69, "externalId": "pokemon-a/booster-box", "url": "https://www.pricecharting.com/game/pokemon-a/booster-box",
        "sku": "ptcg:en:A:booster-box:std"}
PC_B = {**PC_A, "sealedId": 70, "sku": "ptcg:en:B:booster-box:std"}

WORLD: dict = {"items": [], "have": set()}


def world(items, have=()):
    WORLD["items"], WORLD["have"] = [dict(i) for i in items], set(have)


ITEMS_WITH_DATA = sc._items_with_data
sc._load_adapter_items = lambda cur, adapter, allow_candidates: [dict(i) for i in WORLD["items"]]
sc._items_with_data = lambda cur, adapter: {sc._data_key(adapter, i["sealedId"], i["externalId"])
                                            for i in WORLD["items"] if i["sealedId"] in WORLD["have"]}
sc.load_sealed_checkpoints = lambda cur, adapter: {}
sc.checkpoint_age_hours = lambda checkpoints, key: None  # no checkpoint: incr is due


def select(adapter, mode):
    return sc._select(None, adapter, mode, limit=None, allow_candidates=False, force=False)


def skus(items):
    return [i["sku"] for i in items]


class LiteCursor:
    """MySQL-flavoured SQL on in-memory sqlite (%s -> ?), dict rows."""

    def __init__(self, script):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(script)
        self.cur = conn.cursor()

    def execute(self, sql, params=()):
        self.cur.execute(sql.replace("%s", "?"), tuple(params or ()))

    def fetchall(self):
        return [dict(r) for r in self.cur.fetchall()]


def bindings_follow_the_frozen_item():
    cur = LiteCursor("""
    CREATE TABLE catalog_sealed_product (id INTEGER PRIMARY KEY, sku_id TEXT, slug TEXT, group_code TEXT, game TEXT,
      lang TEXT, set_code TEXT, name_en TEXT, name_jp TEXT, status TEXT);
    CREATE TABLE catalog_sealed_source_identity (source_code TEXT, external_entity_id TEXT, sealed_id INTEGER,
      canonical_url TEXT, match_status TEXT, resolved INTEGER, PRIMARY KEY (source_code, external_entity_id));
    CREATE TABLE operator_sealed_binding_freeze (sealed_id INTEGER, freeze_kind TEXT, source_code TEXT,
      external_entity_id TEXT, acceptance_status TEXT, PRIMARY KEY (sealed_id, freeze_kind, source_code));
    INSERT INTO catalog_sealed_product VALUES
      (5, 'optcg:en:OP-02:booster-box:std', 'optcg-en-op-02-booster-box-std', 'optcg-en', 'optcg', 'en', 'OP-02', 'Paramount War', '', 'active'),
      (179, 'ptcg:en:JU:booster-box:std', 'ptcg-en-ju-booster-box-std', 'ptcg-en', 'ptcg', 'en', 'JU', 'Jungle', '', 'active');
    INSERT INTO catalog_sealed_source_identity VALUES
      ('pricecharting', 'one-piece-paramount-war/booster-box', 5, NULL, 'exact', 1),
      ('pricecharting', 'pokemon-jungle/booster-box-1st-edition', 179, NULL, 'candidate', 1),
      ('pricecharting', 'pokemon-jungle/booster-box', 179, NULL, 'candidate', 1);
    INSERT INTO operator_sealed_binding_freeze VALUES
      (5, 'source', 'pricecharting', 'one-piece-paramount-war/booster-box', 'accepted'),
      (179, 'source', 'pricecharting', 'pokemon-jungle/booster-box-1st-edition', 'accepted');
    """)
    got = [(r["sealed_id"], r["external_entity_id"]) for r in load_sealed_bindings(cur, source_code="pricecharting")]
    assert sorted(got) == [(5, "one-piece-paramount-war/booster-box"), (179, "pokemon-jungle/booster-box-1st-edition")], \
        "JU EN pulled the unlimited Jungle box; its freeze names the 1st edition: %r" % got
    assert len(load_sealed_bindings(cur, source_code="pricecharting", require_accepted=False)) == 3
    print("NEGATIVE_OK an accepted freeze makes only the item it names pullable, whatever the row's match_status")


def stock_follows_the_frozen_item():
    """2026-09-24: SM1S JP's SNK freeze moved off the wrong box; its quarantined rows kept stock from pulling the
    right one. Data is judged per item, SNK spellings folded; Yahoo sales carry no item."""
    cur = LiteCursor("""
    CREATE TABLE market_sealed_price_observation (sealed_id INTEGER, source_code TEXT, external_entity_id TEXT,
      metric_status TEXT);
    CREATE TABLE market_sealed_sale_observation (sealed_id INTEGER, source_code TEXT);
    INSERT INTO market_sealed_price_observation VALUES
      (277, 'snkrdunk', 'apparels:137785', 'quarantined'),
      (41, 'snkrdunk', 'apparels:767625', 'ok'),
      (179, 'pricecharting', 'pokemon-jungle/booster-box', 'ok'),
      (5, 'pricecharting', 'one-piece-paramount-war%2Fbooster-box', 'ok');
    INSERT INTO market_sealed_sale_observation VALUES (285, 'yahoo');
    """)
    moved = {"sealedId": 277, "externalId": "apparels:480200", "itemId": 480200, "sku": "ptcg:jp:SM1S:booster-box:std"}
    ju = {"sealedId": 179, "externalId": "pokemon-jungle/booster-box-1st-edition", "sku": "ptcg:en:JU:booster-box:std",
          "url": "https://www.pricecharting.com/game/pokemon-jungle/booster-box-1st-edition"}
    op02 = {"sealedId": 5, "externalId": "one-piece-paramount-war/booster-box", "sku": "optcg:en:OP-02:booster-box:std",
            "url": "https://www.pricecharting.com/game/one-piece-paramount-war/booster-box"}
    y2 = {**Y2, "externalId": "closedsearch-moved"}
    faked, sc._items_with_data = sc._items_with_data, ITEMS_WITH_DATA
    try:
        for adapter, items, stock, incr in (("sealed_snk", [moved, EB03], [moved], [EB03]),
                                            ("sealed_pc", [ju, op02], [ju], [op02]),
                                            ("sealed_yahoo", [y2], [], [y2])):
            world(items)
            got = [skus(sc._select(cur, adapter, mode, limit=None, allow_candidates=False, force=False)[0])
                   for mode in ("stock", "incr")]
            assert got == [skus(stock), skus(incr)], "%s stock/incr: %r" % (adapter, got)
    finally:
        sc._items_with_data = faked
    print("NEGATIVE_OK stock pulls a SKU whose freeze moved to an item without rows; SNK spellings / PC quoting fold")


def main() -> int:
    bindings_follow_the_frozen_item()
    stock_follows_the_frozen_item()
    world([EB03, EB05, OP17])
    selected, blocked, shared = select("sealed_snk", "stock")
    assert shared == {"snkrdunk:767625": [EB03["sku"], EB05["sku"]]}, \
        "trading-cards:767625 and apparels:767625 are one SNK item: %r" % shared
    assert skus(selected) == [OP17["sku"]], "stock must not pull a source item bound to two SKUs: %r" % skus(selected)
    assert blocked == [{"sku": EB03["sku"], "key": "snkrdunk:767625", "sharedWith": [EB05["sku"]]},
                       {"sku": EB05["sku"], "key": "snkrdunk:767625", "sharedWith": [EB03["sku"]]}], blocked
    print("NEGATIVE_OK stock skips both SKUs on one SNK item (namespace dropped) and lists them as blocked")

    world([OP06_JP, OP06_EN], have=[OP06_JP["sealedId"]])
    selected, blocked, _ = select("sealed_snk", "stock")
    assert selected == [] and skus(blocked) == [OP06_EN["sku"]], \
        "the EN box must not be stocked with the JP box it shares an item with: %r / %r" % (skus(selected), blocked)
    selected, blocked, shared = select("sealed_snk", "incr")
    assert skus(selected) == [OP06_JP["sku"]] and blocked == [] and list(shared) == ["snkrdunk:145974"], \
        "incr must keep refreshing the SKU with data and only list the sharing: %r" % ((skus(selected), blocked, shared),)
    print("POSITIVE_OK incr keeps refreshing the SKU that has data and only lists the shared item")

    world([Y1, Y2, Y3])
    selected, blocked, _ = select("sealed_yahoo", "stock")
    assert skus(selected) == [Y1["sku"]] and skus(blocked) == [Y2["sku"], Y3["sku"]], \
        "Yahoo is keyed by its search URL, not externalId 'closedsearch': %r / %r" % (skus(selected), blocked)
    world([PC_A, PC_B])
    selected, blocked, _ = select("sealed_pc", "stock")
    assert selected == [] and skus(blocked) == [PC_A["sku"], PC_B["sku"]], (selected, blocked)
    print("NEGATIVE_OK Yahoo and PC SKUs sharing one URL are blocked; a distinct Yahoo query still pulls")

    pulls: list = []
    sc.load_env = lambda: None
    sc.db = lambda: type("Conn", (), {"cursor": lambda self: None, "close": lambda self: None})()
    sc.run_snk = lambda conn, items, mode, delay: pulls.append(skus(items)) or {
        "adapter": "sealed_snk", "mode": mode, "attempted": len(items), "ok": len(items)}
    sc.COLLECT_OUT = TMP
    for items, due in (([EB03, EB05], []), ([EB03, EB05, OP17], [OP17["sku"]])):
        world(items)
        pulls.clear()
        sys.argv = ["sealed_collect.py", "stock", "--adapter", "sealed_snk"]
        assert sc.main() == 0
        report = json.loads((TMP / "last_stock.json").read_text(encoding="utf-8"))["reports"][0]
        assert pulls == ([due] if due else []), pulls
        assert skus(report.get("blocked") or []) == [EB03["sku"], EB05["sku"]], \
            "the report must carry blocked SKUs, or sealed_daily grades a skipped SKU green: %r" % report
        assert report.get("shared") == {"snkrdunk:767625": [EB03["sku"], EB05["sku"]]}, report
    print("NEGATIVE_OK last_stock.json carries blocked/shared, also when nothing else was due")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
