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
    "ranks": {},
}

# Negative fixture: the old receipt silently discarded both decision counts.
missing_ranked = dict(complete)
missing_ranked.pop("ranked")
try:
    R._daily_accept_canonical_receipt(missing_ranked)
except KeyError:
    print("NEGATIVE_OK receipt without ranked count was rejected")
else:
    raise AssertionError("negative daily-accept receipt fixture did not fire")

receipt = R._daily_accept_canonical_receipt(complete)
assert tuple(receipt) == R.DAILY_ACCEPT_CANONICAL_FIELDS
assert receipt["ranked"] == 1314
assert receipt["awaitingFreshPrice"] == 8
assert "ranks" not in receipt
print("POSITIVE_OK receipt carries accepted, ranked, awaiting, and generation sha")

