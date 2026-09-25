#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PSA10 latest-sale quote planner: eligibility, identity bind, timestamps (no DB).

Every fixture here is a shape that was measured on the live board and that has
already cost the board a wrong number at least once:

  * variant 1279 carries TWO SNKRDUNK item ids.  Its quote is bound to the
    older one while the $1,374-$1,697 sales sit on the other; without the
    strict-identity bind those sales become the price of a card they do not
    belong to.
  * 6,508 SNKRDUNK sale rows store `snkrdunk:807560` while the identity view
    stores `807560`.  A prefixed id mints a revision that the eligibility view
    -- which compares with a plain equality -- can never see.
  * the PriceCharting title quarantine is the only thing keeping the 2026-07-14
    v1326 "Latias" sale out of the headline price, and that receipt is exactly
    the kind of file that goes missing quietly.  Missing must mean STOP.

The last block is a min-hit assert: the fixtures themselves must produce at
least one identity rejection, one quarantine rejection and one ungated accept,
so this file cannot go green while the planner silently stopped filtering.
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import psa10_latest_sale_quote as M  # noqa: E402
from current_quote_revision import (  # noqa: E402
    SALE_QUOTE_CONTRACT,
    SALE_QUOTE_METHOD,
    mintable_quote_storage_source_codes,
)

FAILED: list[str] = []
HITS: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}{': ' + detail if detail else ''}")


AS_OF = datetime(2026, 8, 23, 18, 30, 0)
BASE = datetime(2026, 8, 20, 3, 15, 0)


class FakeCursor:
    """Answers only the two statements the planner issues, by table name."""

    def __init__(self, identities, sales):
        self._identities = identities
        self._sales = sales
        self._rows: list[dict] = []
        self.statements: list[str] = []

    def execute(self, sql, params=()):
        self.statements.append(sql)
        wanted = {value for value in params if isinstance(value, int)}
        if "operator_strict_source_identity" in sql:
            rows = self._identities
        elif "market_sale_observation" in sql:
            rows = self._sales
        else:
            raise AssertionError(f"unexpected statement: {sql[:80]}")
        self._rows = [dict(row) for row in rows if row["variant_id"] in wanted]

    def fetchall(self):
        return self._rows


def sale(sale_id, variant_id, external, days_ago, price, fingerprint, **extra):
    row = {
        "id": sale_id,
        "variant_id": variant_id,
        "external_entity_id": external,
        "sold_at": BASE - timedelta(days=days_ago),
        "unit_price_usd": price,
        "quantity": 1,
        "transaction_fingerprint": fingerprint,
        "listing_item_id": None,
        "listing_url": None,
        "listing_title": None,
    }
    row.update(extra)
    return row


# --------------------------------------------------------------------------
# 1. variant 1279: two item ids, only one of them is this card
# --------------------------------------------------------------------------
identities = [
    {"variant_id": 1279, "external_entity_id": "807560"},
    {"variant_id": 1300, "external_entity_id": "911001"},
    {"variant_id": 1444, "external_entity_id": "5834844"},
]
sales = [
    # bound id, prefixed the way SNKRDUNK landings store it
    sale(1, 1279, "snkrdunk:807560", 1, "300.00", "f-bound"),
    sale(2, 1279, "snkrdunk:807560", 30, "290.00", "f-b2"),
    sale(3, 1279, "snkrdunk:807560", 60, "310.00", "f-b3"),
    # the OTHER item id on the same card -- newer and 5x the price
    sale(4, 1279, "snkrdunk:999999", 0, "1697.00", "f-other"),
    # a card whose only sales are on an unbound id at all
    sale(5, 1300, "snkrdunk:222222", 1, "88.00", "f-unbound"),
]
cursor = FakeCursor(identities, sales)
by_variant = M.load_candidate_sales(
    cursor, source="snkrdunk", variant_ids=[1279, 1300, 1444]
)
check("the unbound item id is not admitted as this card's sale",
      [s["saleObservationId"] for s in by_variant.get(1279, [])] == [1, 2, 3],
      str(by_variant.get(1279)))
check("a variant whose every sale is on an unbound id yields nothing",
      by_variant.get(1300) in (None, []), str(by_variant.get(1300)))
if by_variant.get(1300) in (None, []):
    HITS.append("identity_rejection")
check("the prefixed sale id is stored back in the identity view's own form",
      all(s["externalEntityId"] == "807560" for s in by_variant[1279]),
      str(by_variant[1279][0]["externalEntityId"]))
check("normalisation strips only the provider prefix",
      (M.normalize_external_entity_id("snkrdunk:807560"),
       M.normalize_external_entity_id("pc:5834844"),
       M.normalize_external_entity_id("5834844")) == ("807560", "5834844", "5834844"))

# --------------------------------------------------------------------------
# 2. eligibility that lives in SQL still has to be provably right
# --------------------------------------------------------------------------
statement = next(s for s in cursor.statements if "market_sale_observation" in s)
check("both SNK grade spellings survive normalisation",
      "UPPER(REPLACE(s.grade_label,' ',''))" in statement
      and "PSA10" in M.GRADE_LABELS and "10" in M.GRADE_LABELS)
check("grade lane is pinned to PSA, not any grader",
      "UPPER(s.grader_code)='PSA'" in statement)
check("coverage is a positive allow-list, so a new status is excluded by default",
      "quarantined" not in M.COVERAGE_STATUSES
      and set(M.COVERAGE_STATUSES) == {"partial", "complete", "certified"},
      str(M.COVERAGE_STATUSES))
check("a sale with no timestamp or no price can never be picked",
      "s.unit_price_usd>0" in statement and "s.quantity>0" in statement
      and "s.sold_at IS NOT NULL" in statement)
check("relative-only timestamps are admitted only once resolved",
      "relative" not in [q for q in M.TIMESTAMP_QUALITIES if q == "relative"]
      and "relative_resolved" in M.TIMESTAMP_QUALITIES,
      str(M.TIMESTAMP_QUALITIES))

# --------------------------------------------------------------------------
# 3. PC title quarantine: subtracted, and missing means STOP
# --------------------------------------------------------------------------
pc_identities = [{"variant_id": 1326, "external_entity_id": "5834844"}]
pc_sales = [
    sale(90, 1326, "5834844", 0, "4000.00", "f-latias"),
    sale(91, 1326, "5834844", 2, "120.00", "f-real"),
    sale(92, 1326, "5834844", 20, "118.00", "f-r2"),
    sale(93, 1326, "5834844", 40, "122.00", "f-r3"),
]
pc_cursor = FakeCursor(pc_identities, pc_sales)
kept = M.load_candidate_sales(
    pc_cursor, source="pricecharting", variant_ids=[1326],
    quarantined_sale_ids={90},
)
check("a title-quarantined sale is removed before it can be picked",
      [s["saleObservationId"] for s in kept[1326]] == [91, 92, 93], str(kept))
HITS.append("quarantine_rejection")

with tempfile.TemporaryDirectory() as tmp:
    missing = Path(tmp) / "gone.json"
    try:
        M.load_title_quarantine(missing)
        raised = ""
    except FileNotFoundError as exc:
        raised = str(exc)
    check("a missing quarantine receipt is a hard stop, never an empty set",
          "quarantine receipt is missing" in raised, raised or "<no raise>")
    present = Path(tmp) / "q.json"
    present.write_text(
        json.dumps({"entries": [{"saleObservationId": 90}, {"saleObservationId": 91}]}),
        encoding="utf-8",
    )
    check("the receipt is read by saleObservationId",
          M.load_title_quarantine(present) == {90, 91})

    # 2026-09-25: the same receipt now also carries price_isolated_spike entries
    # (pc_sale_title_quarantine.py, second discriminator).  The planner must drop
    # every entry whatever its reason -- a reason filter here would let the
    # $1,485 Latias & Latios sale back in as a headline candidate.  Sale 95 is
    # the newest and IN band, so only the receipt can keep it out.
    mixed = Path(tmp) / "mixed.json"
    mixed.write_text(json.dumps({"entries": [
        {"saleObservationId": 90, "reason": "title_collector_contradiction"},
        {"saleObservationId": 95, "reason": "price_isolated_spike"},
    ]}), encoding="utf-8")
    check("every receipt entry is quarantined, whatever its reason",
          M.load_title_quarantine(mixed) == {90, 95}, str(M.load_title_quarantine(mixed)))
    spiked = M.plan(
        FakeCursor(pc_identities, pc_sales + [sale(95, 1326, "5834844", 0, "121.00", "f-spike")]),
        source="pricecharting", variant_ids=[1326], as_of=AS_OF, quarantine_receipt=mixed,
    )
    check("plan() never quotes a price_isolated_spike sale from the receipt",
          [r["saleObservationId"] for r in spiked["rows"]] == [91],
          str([r["saleObservationId"] for r in spiked["rows"]]))

# --------------------------------------------------------------------------
# 3b. SNKRDUNK sales are quarantined by the same receipt (2026-09-25)
# --------------------------------------------------------------------------
# Sale 2452342 (v126) is a JPY 1,999,999 placeholder trade among JPY 21k-23.5k
# neighbours.  The receipt keys on market_sale_observation.id, which is
# source-neutral, and the 058 history view already drops a quarantined id of
# any source -- but plan() used to read the receipt only for pricecharting, so
# the same sale could leave the history and still be minted as the price.
# Sale 7 is the newest bound sale and IN band: only the receipt keeps it out.
_RECEIPTS = tempfile.TemporaryDirectory()
NO_QUARANTINE = Path(_RECEIPTS.name) / "none.json"
NO_QUARANTINE.write_bytes(b'{"entries": []}')
snk_receipt = Path(_RECEIPTS.name) / "snk.json"
snk_receipt.write_bytes(json.dumps({"entries": [
    {"saleObservationId": 7, "variantId": 1279, "reason": "snk_price_placeholder"},
]}).encode("utf-8"))
snk_sales = sales + [sale(7, 1279, "snkrdunk:807560", 0, "305.00", "f-snk-quarantined")]
open_door = M.plan(FakeCursor(identities, snk_sales), source="snkrdunk",
                   variant_ids=[1279], as_of=AS_OF, quarantine_receipt=NO_QUARANTINE)
check("fixture: with an empty receipt the in-band SNK sale 7 IS the quote",
      [r["saleObservationId"] for r in open_door["rows"]] == [7],
      str([r["saleObservationId"] for r in open_door["rows"]]))
shut = M.plan(FakeCursor(identities, snk_sales), source="snkrdunk",
              variant_ids=[1279], as_of=AS_OF, quarantine_receipt=snk_receipt)
snk_quoted = [r["saleObservationId"] for r in shut["rows"]]
check("a quarantined SNKRDUNK sale is never the latest-sale quote", snk_quoted == [1], str(snk_quoted))
check("a quarantined SNKRDUNK sale is not even scanned as a band prior",
      shut["salesScanned"] == open_door["salesScanned"] - 1,
      f"{shut['salesScanned']} vs {open_door['salesScanned']}")
if snk_quoted == [1]:
    HITS.append("snk_quarantine_rejection")
try:
    M.plan(FakeCursor(identities, sales), source="snkrdunk", variant_ids=[1279],
           as_of=AS_OF, quarantine_receipt=Path(_RECEIPTS.name) / "gone.json")
    snk_raised = ""
except FileNotFoundError as exc:
    snk_raised = str(exc)
check("a missing receipt stops the SNKRDUNK mint too, never an empty set",
      "quarantine receipt is missing" in snk_raised, snk_raised or "<no raise>")

# Window anchors (live-db-snapshot.ts) come from operator_accepted_psa10_sales_history,
# which reads operator_eligible_accepted_psa10_sales_rows.  The NEWEST migration
# defining that view must drop a quarantined sale by id alone: a source predicate
# inside the exclusion would re-admit SNKRDUNK sales as 7d/30d anchors.
import re  # noqa: E402

VIEW_HEAD = re.compile(
    r"CREATE\s+OR\s+REPLACE\s+VIEW\s+operator_eligible_accepted_psa10_sales_rows\b", re.I)
defining = [
    p for p in sorted((ROOT / "pipelines" / "migrations").glob("*.sql"))
    if VIEW_HEAD.search(p.read_bytes().decode("utf-8"))
]
view_sql = defining[-1].read_bytes().decode("utf-8") if defining else ""
view_body = VIEW_HEAD.split(view_sql)[-1].split(";", 1)[0] if defining else ""
exclusions = [
    m.group(1) for m in re.finditer(r"NOT\s+EXISTS\s*\((.*?)\)", view_body, re.S | re.I)
    if "market_pc_sale_title_quarantine" in m.group(1)
]
check("the newest sales-rows view drops a quarantined sale by id, whatever its source",
      len(exclusions) == 1
      and re.search(r"tq\.sale_observation_id\s*=\s*s\.id", exclusions[0]) is not None
      and "source_code" not in exclusions[0].lower(),
      f"{defining[-1].name if defining else '<no view>'}: {exclusions}")

# --------------------------------------------------------------------------
# 4. plan(): the two timestamps, the payload, and registry independence
# --------------------------------------------------------------------------
plan_cursor = FakeCursor(identities, sales)
doc = M.plan(plan_cursor, source="snkrdunk", variant_ids=[1279, 1300, 1444], as_of=AS_OF,
             quarantine_receipt=NO_QUARANTINE)
check("plan touched neither the source registry nor the route policy",
      not any("source_registry" in s or "route_policy" in s
              for s in plan_cursor.statements))
row = next(r for r in doc["rows"] if r["variantId"] == 1279)
check("checked_at is the harvest clock, not the sale day",
      row["checkedAt"] == AS_OF and row["observedDate"] == (BASE - timedelta(days=1)).date()
      and row["checkedAt"].date() != row["observedDate"],
      f"{row['checkedAt']} / {row['observedDate']}")
check("the quote is the newest bound sale", row["saleObservationId"] == 1, str(row))
check("the storage code is the mintable sale lane, never the parent",
      row["storageSourceCode"] in set(mintable_quote_storage_source_codes())
      and row["storageSourceCode"] == "snkrdunk_sales", row["storageSourceCode"])
check("a card with no eligible sale is reported, not silently dropped",
      doc["noEligibleSale"] == [1300, 1444], str(doc["noEligibleSale"]))
check("the payload names the contract and the method it was minted under",
      row["payload"]["contract"] == SALE_QUOTE_CONTRACT
      and row["payload"]["method"] == SALE_QUOTE_METHOD
      and row["payload"]["source"] == "market_sale_observation")
check("the payload carries the evidence a human needs to re-judge the price",
      {"saleObservationId", "transactionFingerprint", "soldAt", "unitPriceUsd",
       "outlierGuard", "rejectedCandidates", "salesScanned"} <= set(row["payload"]),
      str(sorted(row["payload"])))
check("only three bound sales means the band is ungated and says so",
      row["ungated"] is True and row["payload"]["outlierGuard"]["ungated"] is True,
      str(row["payload"]["outlierGuard"]))
HITS.append("ungated_accept")
check("rejectedSales is present even when nothing was rejected",
      "rejectedSales" in doc and doc["rejectedSales"] == [], str(doc.get("rejectedSales")))

# replay at a different harvest clock must not churn the evidence hash
later = M.plan(
    FakeCursor(identities, sales), source="snkrdunk",
    variant_ids=[1279, 1300, 1444], as_of=AS_OF + timedelta(days=1),
    quarantine_receipt=NO_QUARANTINE,
)
later_row = next(r for r in later["rows"] if r["variantId"] == 1279)
check("replaying the same sale a day later keeps payload_sha256 identical",
      later_row["payloadSha256"] == row["payloadSha256"], row["payloadSha256"])
check("plan_sha256 is stable across the replay too",
      later["planSha256"] == doc["planSha256"], doc["planSha256"])
check("payload_sha256 is a real sha256 of the payload body",
      row["payloadSha256"] == M.payload_sha256(row["payload"]) and len(row["payloadSha256"]) == 64)

# a NEW sale must move the hash -- otherwise the price could never change
moved = M.plan(
    FakeCursor(identities, sales + [sale(6, 1279, "snkrdunk:807560", 0, "305.00", "f-new")]),
    source="snkrdunk", variant_ids=[1279], as_of=AS_OF, quarantine_receipt=NO_QUARANTINE,
)
check("a newer bound sale changes both the quote and its hash",
      moved["rows"][0]["saleObservationId"] == 6
      and moved["rows"][0]["payloadSha256"] != row["payloadSha256"],
      str(moved["rows"][0]["saleObservationId"]))

# --------------------------------------------------------------------------
# 5. the outlier band is really wired into the planner, not just imported
# --------------------------------------------------------------------------
band_sales = [sale(1, 1279, "snkrdunk:807560", 0, "4000.00", "f-poison")] + [
    sale(10 + i, 1279, "snkrdunk:807560", 5 + i, "300.00", f"f-p{i:02d}")
    for i in range(10)
]
band = M.plan(FakeCursor(identities, band_sales), source="snkrdunk",
              variant_ids=[1279], as_of=AS_OF, quarantine_receipt=NO_QUARANTINE)
check("a 13x sale is rejected by the planner and the walk-back takes the next",
      band["rows"][0]["saleObservationId"] == 10, str(band["rows"][0]["saleObservationId"]))
check("the rejection is carried in the receipt inputs with its variant",
      [(r["variantId"], r["reason"]) for r in band["rejectedSales"]] == [(1279, "above_band")],
      str(band["rejectedSales"]))
check("the walk-back is reported per variant",
      band["fallbackWalkbacks"] == [
          {"variantId": 1279, "walkbacks": 1, "chosenSaleObservationId": 10}],
      str(band["fallbackWalkbacks"]))
HITS.append("band_rejection")

# --------------------------------------------------------------------------
# 6. receipt shape
# --------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    target = Path(tmp) / "receipt.json"
    M.write_receipt(band, run_id=0, quotes_minted=len(band["rows"]),
                    path=target, dry_run=True)
    receipt = json.loads(target.read_text(encoding="utf-8"))
    check("receipt states every count the reviewer needs",
          {"rejectedSales", "fallbackWalkbacks", "noEligibleSale", "quotesMinted",
           "ungated", "salesScanned", "variantsPlanned", "planSha256", "asOf",
           "businessDate", "dryRun"} <= set(receipt), str(sorted(receipt)))
    check("a dry-run receipt admits it wrote nothing",
          receipt["dryRun"] is True and receipt["runId"] == 0, str(receipt["runId"]))

# --------------------------------------------------------------------------
# min-hit: the fixtures must exercise each rejection path at least once
# --------------------------------------------------------------------------
for hit in ("identity_rejection", "quarantine_rejection", "snk_quarantine_rejection",
            "band_rejection", "ungated_accept"):
    check(f"fixtures exercised {hit}", hit in HITS, str(HITS))
check("only mintable sale lanes are reachable from the CLI",
      set(M.SALE_LANE_BY_SOURCE) == {"pricecharting", "snkrdunk"}
      and set(M.SALE_LANE_BY_SOURCE.values()) == set(mintable_quote_storage_source_codes()),
      str(M.SALE_LANE_BY_SOURCE))

print()
if FAILED:
    print(f"FAILED {len(FAILED)}: {FAILED}")
    raise SystemExit(1)
print("all psa10 latest-sale quote planner checks passed")
