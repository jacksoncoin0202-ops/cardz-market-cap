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

src = Path(R.__file__).read_text(encoding="utf-8")
assert "SET SESSION innodb_lock_wait_timeout=30" in src
assert "SET SESSION lock_wait_timeout=30" in src
print("POSITIVE_OK daily-accept sets 30s lock wait timeouts")

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
# 40 was a PriceCharting-only exemption for a month-old chart level.  The
# price is a real sale now, so the EN lane keeps the same one-cycle age as
# every other source (owner 2026-08-23).
assert R._price_max_age_days("pricecharting") == 30
assert R._price_max_age_days("pricecharting_sales") == 30
assert R._price_max_age_days("snkrdunk_sales") == 30
assert R._price_max_age_days("snkrdunk") == 30
print("POSITIVE_OK source-cycle ages and two-percent ranking guards hold")

# F-PRICE-AGE: the shipped policy must carry one cycle age per registry quote
# source.  A missing entry used to fall back to `default` in silence.
import json  # noqa: E402
import tempfile  # noqa: E402

sys.path.insert(0, str(ROOT / "pipelines"))
from current_quote_revision import quote_source_codes  # noqa: E402

REGISTRY_QUOTE_SOURCES = quote_source_codes()
assert REGISTRY_QUOTE_SOURCES, "registry declares no quote source"
shipped = json.loads(R.DAILY_GUARDRAILS_PATH.read_text(encoding="utf-8"))
for _source in REGISTRY_QUOTE_SOURCES:
    assert _source in shipped["priceMaxAgeDaysBySource"], _source
print(
    "POSITIVE_OK shipped guardrail declares a price age for every registry"
    f" quote source {REGISTRY_QUOTE_SOURCES}"
)

_shipped_path = R.DAILY_GUARDRAILS_PATH
with tempfile.TemporaryDirectory(prefix="cardz-e-priceage-") as _tmp:
    _probe = Path(_tmp) / "daily-release-guardrails.json"

    def _load_with(policy: dict) -> dict:
        _probe.write_text(json.dumps(policy), encoding="utf-8")
        R.DAILY_GUARDRAILS_PATH = _probe
        try:
            return R._load_daily_guardrails()
        finally:
            R.DAILY_GUARDRAILS_PATH = _shipped_path

    # Negative: a complete policy still loads, and an unknown extra source is
    # never required, so this gate cannot start rejecting today's policy.
    _complete = json.loads(_shipped_path.read_text(encoding="utf-8"))
    assert _load_with(_complete)["priceMaxAgeDaysBySource"]["default"] == 30
    print("NEGATIVE_OK the shipped guardrail policy still loads unchanged")

    # Positive: drop one registry quote source's entry -> fail closed, naming
    # the source and the file.
    _missing = json.loads(json.dumps(_complete))
    _dropped = REGISTRY_QUOTE_SOURCES[0]
    _missing["priceMaxAgeDaysBySource"].pop(_dropped)
    try:
        _load_with(_missing)
    except RuntimeError as error:
        assert _dropped in str(error), str(error)
        assert "daily-release-guardrails.json" in str(error), str(error)
        print(
            "POSITIVE_OK a registry quote source with no price-age entry fails"
            f" closed: {error}"
        )
    else:
        raise AssertionError("missing price-age fixture did not fire")

    # Positive: an entry that exists but is not a usable age is still invalid.
    _zero = json.loads(json.dumps(_complete))
    _zero["priceMaxAgeDaysBySource"][_dropped] = 0
    try:
        _load_with(_zero)
    except RuntimeError as error:
        assert "price ages are invalid" in str(error), str(error)
        print("POSITIVE_OK a zero price age is rejected")
    else:
        raise AssertionError("zero price-age fixture did not fire")

assert R.DAILY_GUARDRAILS_PATH == _shipped_path
