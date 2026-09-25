#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""consolidate_pc_map retires a map row whose binding was rejected in place.

2026-09-25: nine WOTC 1st Edition cards were bound to their Unlimited PC
products. The reject manifest turned each exact row into rejected, which left
the old product owned by nobody, so consolidate kept the row (it only retired
a row when ANOTHER variant held its product). pc-identity-reverify reads this
map as its second opinion and held every correct 1st Edition proposal as
map_product_mismatch; the rebind only went through by pointing the judge at a
scratch copy of the map with those rows cut out.

The fixture is a whole consolidation run against a temporary tree: no DB, no
runtime files. The DB readers are swapped for fixture rows; the verdict
predicate (rebuild_036.rejection_is_verdict) is the real one.

Run: python -X utf8 scripts/test_consolidate_pc_map_rejected_in_place.py
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import consolidate_pc_map as CM  # noqa: E402

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}: got {got!r}, want {want!r}")


def pc_url(slug: str) -> str:
    return f"https://www.pricecharting.com/game/pokemon-fixture/{slug}"


def map_row(variant_id: int, product_id: int) -> dict:
    return {
        "variant_id": variant_id,
        "pc_product_id": product_id,
        "pc_url": pc_url(f"card-{product_id}"),
        "status": "mapped",
        "ready_for_c12": True,
        "confidence": "high",
    }


def registry_rows(active: dict[int, str]) -> list[dict]:
    rows = []
    for variant_id, product_id in active.items():
        for adapter in ("pc_ebay_sales", "en_price_ref"):
            rows.append({
                "adapter": adapter, "variantId": variant_id,
                "externalId": product_id, "name": f"card {variant_id}",
            })
    return rows


def binding(variant_id: int, product_id: str, status: str, evidence) -> dict:
    return {
        "variant_id": variant_id,
        "external_entity_id": product_id,
        "match_status": status,
        "bind_evidence_json": evidence if evidence is None else json.dumps(evidence),
    }


# Active registry: two live cards, both already in the map.
ACTIVE = {100: "5000", 200: "6000"}
MAP = [
    map_row(100, 5000),
    map_row(200, 6000),
    # v1174's shape: the Unlimited product rejected in place, nobody owns it.
    map_row(1174, 643482),
    # Collateral quarantine (psa_identity_repair style): nobody examined it.
    map_row(300, 7000),
    # A verdict action on a row that is NOT rejected: not an in-place reject.
    map_row(400, 8000),
    # The verdict names a different product than the map row: no contradiction.
    map_row(500, 9001),
    # The existing rule: product now held by an active variant.
    map_row(600, 6000),
    # A red-listed card: rejection_is_verdict counts the ruling.
    map_row(700, 7100),
    # Evidence that cannot be read counts as a verdict (fail-closed).
    map_row(800, 8100),
]
DB_REJECTED = [
    binding(1174, "643482", "rejected", {
        "action": "reject",
        "reason": "wrong-printing-edition: catalog is 1st Edition, PC 643482 is Unlimited",
    }),
    binding(300, "7000", "rejected", {"action": "quarantine-unresolved-variant-identity"}),
    binding(400, "8000", "manual_review", {"action": "reject"}),
    binding(500, "9000", "rejected", {"action": "reject-wrong-printing-source"}),
    binding(700, "7100", "rejected", {"action": "confirm", "redListed": True}),
    {"variant_id": 800, "external_entity_id": "8100", "match_status": "rejected",
     "bind_evidence_json": "{not json"},
]


def run_consolidation(tmp: Path) -> tuple[list[dict], dict]:
    canonical = tmp / "canonical.jsonl"
    registry = tmp / "registry.jsonl"
    report = tmp / "report.json"
    canonical.write_bytes("".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in MAP
    ).encode("utf-8"))
    registry.write_bytes("".join(
        json.dumps(row) + "\n" for row in registry_rows(ACTIVE)
    ).encode("utf-8"))

    saved = {name: getattr(CM, name) for name in (
        "ROOT", "CANONICAL", "REGISTRY", "MANIFEST", "REPORT", "SUPPLEMENTAL",
        "verified_binding_evidence_rows", "rejected_pc_binding_rows",
    )}
    saved_argv = sys.argv
    CM.ROOT = tmp
    CM.CANONICAL = canonical
    CM.REGISTRY = registry
    CM.MANIFEST = tmp / "absent-manifest.json"
    CM.REPORT = report
    CM.SUPPLEMENTAL = ()
    CM.verified_binding_evidence_rows = lambda: []
    CM.rejected_pc_binding_rows = lambda: [dict(row) for row in DB_REJECTED]
    sys.argv = ["consolidate_pc_map.py", "--write"]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            code = CM.main()
    finally:
        for name, value in saved.items():
            setattr(CM, name, value)
        sys.argv = saved_argv
    check("consolidation exits 0", code, 0)
    rows = [
        json.loads(line)
        for line in canonical.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return rows, json.loads(report.read_text(encoding="utf-8"))


with tempfile.TemporaryDirectory(prefix="cardz-consolidate-") as tmp_dir:
    rows, report = run_consolidation(Path(tmp_dir))

by_variant = {int(row["variant_id"]): str(row["pc_product_id"]) for row in rows}

# --- 1. the in-place reject leaves the map --------------------------------
check("v1174's rejected Unlimited product is no longer in the map",
      1174 in by_variant, False)
# What pc-identity-reverify reads (rebuild_036_reverify: map_product_by_variant)
# is now empty for the card, so the correct proposal meets no second opinion
# naming a product somebody already refused.
check("reverify's map_product for v1174 is empty",
      by_variant.get(1174, ""), "")
check("the receipt lists it",
      report.get("retiredRejectedInPlace"),
      [{"variantId": 700, "productId": "7100"},
       {"variantId": 800, "productId": "8100"},
       {"variantId": 1174, "productId": "643482"}])

# --- 2. only a verdict on THIS pair retires ---------------------------------
check("collateral quarantine keeps its row", by_variant.get(300), "7000")
check("a verdict action on a manual_review row is not an in-place reject",
      by_variant.get(400), "8000")
check("a verdict on another product does not touch this row",
      by_variant.get(500), "9001")

# --- 3. the older rule and the active rows are untouched --------------------
check("a product another active variant holds is still retired",
      600 in by_variant, False)
check("and reported as before",
      report.get("retiredStolenProducts"),
      [{"variantId": 600, "productId": "6000", "nowOwnedBy": 200}])
check("active rows stay", (by_variant.get(100), by_variant.get(200)), ("5000", "6000"))
check("active gate still clean",
      (report.get("activeMissing"), report.get("activeProductMismatch")), ([], []))
check("one row per variant", len(rows), len(by_variant))

# --- 4. an active variant is never retired, even if a stale verdict names it --
check("pairs are keyed (variant, product) as strings",
      CM.rejected_in_place_pairs([binding(100, "5000", "rejected", {"action": "reject"})]),
      {(100, "5000")})
with tempfile.TemporaryDirectory(prefix="cardz-consolidate-") as tmp_dir:
    DB_REJECTED.append(binding(100, "5000", "rejected", {"action": "reject"}))
    rows, report = run_consolidation(Path(tmp_dir))
    DB_REJECTED.pop()
check("an active variant's row survives a verdict on its own pair",
      {int(row["variant_id"]): str(row["pc_product_id"]) for row in rows}.get(100), "5000")


if FAILED:
    print("\n".join(FAILED))
    print(f"{len(FAILED)}/{CHECKS} checks FAILED")
    raise SystemExit(1)
print(f"OK {CHECKS} checks")
