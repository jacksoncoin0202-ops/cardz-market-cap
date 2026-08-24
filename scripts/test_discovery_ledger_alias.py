#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""What the discovery ledger may call an ambiguous identity, both directions.

2026-08-24: v1020 and v1040 sat in the ledger as `identity_ambiguous /
multiple_exact_bindings` while both of their SNKRDUNK rows named the SAME
external id -- 128132 and 128110 -- one filed under `snkrdunk` and one under
the legacy `snk_psa10`. catalog_source_identity is keyed
(source_code, external_entity_id), so two spellings of one provider are two
ROWS about one product, and the ledger counted rows.

Two products really are ambiguous and must keep blocking, so both directions
are pinned here: the alias pair counts as one, and two genuinely different
external ids on one canonical source still raise the blocker.

Run: python -X utf8 scripts/test_discovery_ledger_alias.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import discovery_ledger as L  # noqa: E402

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got: Any, want: Any) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}: got {got!r}, want {want!r}")


class _Cursor:
    """Answers the ledger's three reads from fixtures; records the writes."""

    def __init__(self, identities: list[dict[str, Any]], variants: list[dict[str, Any]]):
        self.identities = identities
        self.variants = variants
        self.inserts: list[tuple] = []
        self._result: list[dict[str, Any]] = []

    def execute(self, sql: str, params: tuple = ()) -> None:
        text = " ".join(sql.split())
        if text.startswith("INSERT INTO market_identity_discovery_ledger"):
            self.inserts.append(params)
            self._result = []
        elif "COUNT(*) AS n FROM market_identity_discovery_ledger" in text:
            self._result = [{"n": len(self.variants)}]
        elif "COUNT(*) AS n FROM catalog_variant" in text:
            self._result = [{"n": len(self.variants)}]
        elif "FROM catalog_source_identity" in text:
            self._result = list(self.identities)
        elif "FROM catalog_variant v" in text:
            self._result = list(self.variants)
        else:
            raise AssertionError(f"unexpected statement: {text[:90]}")

    def fetchall(self) -> list[dict[str, Any]]:
        return self._result

    def fetchone(self) -> dict[str, Any] | None:
        return self._result[0] if self._result else None


def identity(source_code: str, external: str, variant_id: int = 1020,
             status: str = "exact") -> dict[str, Any]:
    return {
        "variant_id": variant_id,
        "source_code": source_code,
        "match_status": status,
        "external_entity_id": external,
        "evidence_sha256": f"{source_code}-{external}",
    }


VARIANT = [{"variant_id": 1020, "catalog_status": "active", "card_language": "ja"}]


def ledger(identities: list[dict[str, Any]]) -> dict[str, Any]:
    """The one row this ledger run wrote, as named fields."""

    cursor = _Cursor(identities, VARIANT)
    L.rebuild_ledger(cursor)
    params = cursor.inserts[-1]
    return {
        "variant_id": params[0], "catalog_status": params[1], "discovery": params[2],
        "pc_status": params[3], "snk_status": params[4], "blocker": params[5],
        "detail": json.loads(params[6]),
    }


# --- 1. the alias fold ------------------------------------------------------
check("snk_psa10 is the same provider as snkrdunk",
      L.canonical_source_code("snk_psa10"), "snkrdunk")
check("so is the bare snk", L.canonical_source_code("snk"), "snkrdunk")
check("pricecharting answers to itself",
      L.canonical_source_code("pricecharting"), "pricecharting")
check("a source this ledger does not read has no family",
      L.canonical_source_code("ebay"), "")


# --- 2. v1020's shape: two rows, one product -------------------------------
alias = ledger([identity("snkrdunk", "128132"), identity("snk_psa10", "128132")])
check("one product under two provider spellings is one exact binding",
      alias["detail"]["snkExactCount"], 1)
check("so the card is not ambiguous", alias["discovery"], "active_exact")
check("and nothing blocks it", alias["blocker"], None)
check("the receipt names the product once", alias["detail"]["snkExactIds"], ["128132"])
check("the snk column reads exact", alias["snk_status"], "exact")
# v1040's own ids, same shape, so the pin is the rule and not one number.
alias2 = ledger([identity("snk_psa10", "128110", 1020), identity("snkrdunk", "128110")])
check("v1040's pair folds the same way", alias2["blocker"], None)


# --- 3. two PRODUCTS still block -------------------------------------------
two = ledger([identity("snkrdunk", "128132"), identity("snk", "999999")])
check("two different products on one provider are two exact bindings",
      two["detail"]["snkExactCount"], 2)
check("that is an ambiguous identity", two["discovery"], "identity_ambiguous")
check("and it keeps blocking", two["blocker"], "multiple_exact_bindings")
check("the snk column says ambiguous", two["snk_status"], "ambiguous")
check("both products are named in the receipt",
      two["detail"]["snkExactIds"], ["128132", "999999"])

# PriceCharting is keyed the same way and gets the same counting.
pc_two = ledger([identity("pricecharting", "5326001"),
                 identity("pricecharting", "5399884")])
check("two PriceCharting products block as well", pc_two["blocker"],
      "multiple_exact_bindings")
check("and the pc column says ambiguous", pc_two["pc_status"], "ambiguous")

# A non-exact row is a candidate, not a binding: counted per row, and never a
# multiple_exact_bindings blocker.
nonexact = ledger([identity("snkrdunk", "128132", status="manual_review"),
                   identity("snk_psa10", "128132", status="manual_review")])
check("candidates do not become exact bindings", nonexact["detail"]["snkExactCount"], 0)
check("they hold as nonexact_only", nonexact["blocker"], "nonexact_only")


# --- 4. the aggregation itself ---------------------------------------------
agg = L.aggregate_source_identities([
    identity("snkrdunk", "128132"), identity("snk_psa10", "128132"),
    identity("snk", "128132", status="rejected"),
    identity("pricecharting", "5326001"),
])
check("the two spellings land in one family", sorted(agg[1020]), ["pricecharting", "snkrdunk"])
check("naming one product", len(agg[1020]["snkrdunk"]["exact"]), 1)
check("with the non-exact row counted per row", agg[1020]["snkrdunk"]["nonexact"], 1)
# A NULL match_status is neither, exactly as SUM(match_status='exact') and
# SUM(match_status<>'exact') both skipped it.
null_status = L.aggregate_source_identities([
    dict(identity("snkrdunk", "128132"), match_status=None)])
check("a NULL status is neither an exact nor a non-exact row", null_status, {})
# Two exact rows that name no product at all cannot be proved to be one
# binding, so they must not fold into one and hide an ambiguity.
nameless = L.aggregate_source_identities([
    identity("snkrdunk", ""), identity("snk_psa10", "")])
check("exact rows with no external id never fold together",
      len(nameless[1020]["snkrdunk"]["exact"]), 2)


for line in FAILED:
    print(line)
print(f"{CHECKS - len(FAILED)}/{CHECKS} checks passed")
raise SystemExit(1 if FAILED else 0)
