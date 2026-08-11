#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily acceptance receipts must expose the counts that decide publication."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import rebuild_036 as R  # noqa: E402


complete = {
    "accepted": 1322,
    "ranked": 1314,
    "awaitingFreshPrice": 8,
    "rankingGenerationSha256": "a" * 64,
    "rankedGuard": {"minimumRanked": 1288, "maximumAwaitingFreshPrice": 26},
    "ranks": {},
}

# Negative fixture: the old receipt silently discarded both decision counts.
missing_ranked = dict(complete)
missing_ranked.pop("ranked")
try:
    R._canonical_ranking_receipt(missing_ranked)
except KeyError:
    print("NEGATIVE_OK receipt without ranked count was rejected")
else:
    raise AssertionError("negative daily-accept receipt fixture did not fire")

receipt = R._canonical_ranking_receipt(complete)
assert tuple(receipt) == R.DAILY_ACCEPT_CANONICAL_FIELDS
assert receipt["ranked"] == 1314
assert receipt["awaitingFreshPrice"] == 8
assert "ranks" not in receipt
print("POSITIVE_OK receipt carries accepted, ranked, awaiting, and generation sha")

# The measured 08-31 failure (1,314 -> 369 ranked) must stop before any
# canonical row is written.
try:
    R._assert_ranking_guardrail(
        previous={"ranked": 1314},
        ranked=369,
        accepted=1322,
    )
except SystemExit as error:
    assert "ranked cards fell" in str(error)
    print("NEGATIVE_OK measured 08-31 ranking collapse was rejected")
else:
    raise AssertionError("08-31 ranking-collapse fixture did not fire")

guard = R._assert_ranking_guardrail(
    previous={"ranked": 1314},
    ranked=1314,
    accepted=1322,
)
assert guard["minimumRanked"] == 1288
assert guard["maximumAwaitingFreshPrice"] == 26
assert R._price_max_age_days("pricecharting") == 40
assert R._price_max_age_days("snkrdunk") == 30
print("POSITIVE_OK source-cycle ages and two-percent ranking guards hold")
