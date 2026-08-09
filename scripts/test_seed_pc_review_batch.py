#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One PriceCharting product belongs to one variant -- inside a batch too.

seed_pc_review_from_map asked the DB whether each product was already spoken
for, which is the right question and the wrong scope: the batch it was building
was invisible to it. Two pokemon gap cards resolved to the same product page on
2026-08-09, both passed, and the INSERT died on the primary key -- so the whole
seeding transaction rolled back and nothing was written at all.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seed_pc_review_from_map import drop_contested  # noqa: E402

FAILED: list[str] = []


def check(label: str, got: object, want: object) -> None:
    if got == want:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}: got {got!r}, want {want!r}")


def row(variant_id: int, product_id: str) -> dict:
    return {"variant_id": variant_id, "product_id": product_id, "url": "", "html": ""}


# The real collision: variants 1194 and 2237 both resolved to product 643327
# (Pokemon Jungle "Pikachu #60"). Neither proved it owns the page, so neither
# is seeded -- the first arrival does not win by arrival order.
keep, dropped = drop_contested([
    row(2185, "1368342"), row(1194, "643327"), row(2237, "643327"),
])
check("the uncontested row survives", [e["variant_id"] for e in keep], [2185])
check("both claimants are dropped",
      sorted(e["variant_id"] for e in dropped), [1194, 2237])
check("the drop names the rule",
      {e["reason"] for e in dropped}, {"product_claimed_twice_in_batch"})
check("each dropped row names the other claimant",
      {tuple(e["claimedBy"]) for e in dropped}, {(1194, 2237)})

# One variant appearing twice for one product is still a contest: the batch
# would insert the same primary key twice, which is the crash we are fixing.
keep, dropped = drop_contested([row(7, "111"), row(7, "111")])
check("a repeated identical row is contested too", keep, [])

# Three-way, and a second independent product in the same batch.
keep, dropped = drop_contested([
    row(1, "aaa"), row(2, "aaa"), row(3, "aaa"), row(4, "bbb"),
])
check("a three-way contest drops all three",
      [e["variant_id"] for e in keep], [4])
check("three-way rows list all three claimants",
      {tuple(e["claimedBy"]) for e in dropped}, {(1, 2, 3)})

# Nothing to do is not an error.
check("an empty batch stays empty", drop_contested([]), ([], []))
check("a batch with no collision is untouched",
      drop_contested([row(1, "a"), row(2, "b")])[1], [])

print()
if FAILED:
    print(f"{len(FAILED)} FAILED: {FAILED}")
    raise SystemExit(1)
print("seed batch product-ownership rule holds")
