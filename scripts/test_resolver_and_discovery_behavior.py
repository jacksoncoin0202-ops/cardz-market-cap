#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Behavioural quote-resolver and discovery-rotation contracts (no DB)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import daily_discovery_activation as D
from current_quote_revision import (
    LEGACY_KIND,
    legacy_resolution_evidence_sha256,
    quote_lineage_sha256,
    select_verified_legacy_candidate,
)


raw = {
    "acceptance_id": 70191,
    "variant_id": 1,
    "market_cap_usd": Decimal("292500"),
    "price_history_acceptance_id": 2,
    "population_history_acceptance_id": 3,
    "accepted_at": datetime(2026, 8, 10, 12, 0, 0),
    "metric_lineage_sha256": "a" * 64,
    "psa10_population": 100,
    "source_code": "pricecharting",
    "source_external_entity_id": "123",
    "source_period_at": "2026-08-01",
    "source_payload_sha256": "b" * 64,
    "source_record_id": 99,
    "price_history_lineage_sha256": "c" * 64,
    "population_history_lineage_sha256": "d" * 64,
}
lineage = quote_lineage_sha256(
    variant_id=1,
    source_code="pricecharting",
    source_external_entity_id="123",
    price_usd="2925",
    source_period_at="2026-08-01",
    checked_at="2026-08-10 12:00:00",
    payload_sha256="b" * 64,
    reconstruction_kind=LEGACY_KIND,
)
candidate = {
    "id": 10,
    "variant_id": 1,
    "source_code": "pricecharting",
    "source_external_entity_id": "123",
    "price_usd": Decimal("2925.000000"),
    "source_period_at": "2026-08-01",
    "checked_at": datetime(2026, 8, 10, 12, 0, 0),
    "payload_sha256": "b" * 64,
    "quote_lineage_sha256": lineage,
    "reconstructed_from_acceptance_id": 70191,
}
assert select_verified_legacy_candidate(raw, [candidate]) == candidate
assert select_verified_legacy_candidate(raw, [candidate, dict(candidate, id=11)]) is None
assert select_verified_legacy_candidate(raw, [dict(candidate, price_usd=Decimal("2836.31"))]) is None
digest = legacy_resolution_evidence_sha256(raw, lineage)
assert len(digest) == 64 and digest == legacy_resolution_evidence_sha256(raw, lineage)
print("POSITIVE_OK exact immutable legacy candidate is unique and deterministic")
print("NEGATIVE_OK duplicate/wrong-price legacy candidates fail closed")


new_ids = range(1, 61)
retry_ids = range(100, 150)
targets = D.merge_provider_targets(new_ids, retry_ids, limit=40)
assert targets == list(range(1, 41))
assert len(targets) == 40
assert D.merge_provider_targets([1, 2], [2, 3, 4], limit=4) == [1, 2, 3, 4]
print("POSITIVE_OK provider request budget hard-caps each lane at 40")


now = datetime(2026, 8, 12, 12, 0, 0)
ledger = [
    {
        "variant_id": 1,
        "discovery_status": "inactive_exact",
        "last_reviewed_at": "2026-07-01",
        "language": "en",
        "tcg": "pokemon",
    },
    {
        "variant_id": 2,
        "discovery_status": "identity_ambiguous",
        "blocker_code": "multiple_exact_bindings",
        "last_reviewed_at": "2026-07-01",
        "language": "en",
        "tcg": "pokemon",
    },
    {
        "variant_id": 3,
        "discovery_status": "source_not_found",
        "last_reviewed_at": "2026-07-01",
        "language": "en",
        "tcg": "pokemon",
        "next_due_at": now - timedelta(hours=1),
    },
]
retry = D.plan_ledger_retry_targets([], ledger, "browser", limit=40, now=now)
assert retry["targetIds"] == [3]
print("NEGATIVE_OK inactive_exact and multiple-exact never enter provider discovery")


evidence_a = "e" * 64
evidence_b = "f" * 64
first = D.attempt_lifecycle(None, 0, evidence_a)
second = D.attempt_lifecycle(evidence_a, first["consecutive"], evidence_a)
third = D.attempt_lifecycle(evidence_a, second["consecutive"], evidence_a)
changed = D.attempt_lifecycle(evidence_a, third["consecutive"], evidence_b)
success = D.attempt_lifecycle(evidence_a, third["consecutive"], evidence_a, success=True)
terminal = D.attempt_lifecycle(None, 0, evidence_a, terminal=True)
assert first == {"consecutive": 1, "nextDueHours": 24, "quarantineHours": None}
assert second == {"consecutive": 2, "nextDueHours": 24, "quarantineHours": None}
assert third == {"consecutive": 3, "nextDueHours": 168, "quarantineHours": 168}
assert changed["consecutive"] == 1 and changed["quarantineHours"] is None
assert success["consecutive"] == 0 and success["quarantineHours"] is None
assert terminal["quarantineHours"] == 24 * 3650
print("POSITIVE_OK identical evidence quarantines only on third consecutive miss")
print("POSITIVE_OK new evidence/success resets; multiple-exact is terminal quarantine")

print("ALL_OK resolver_and_discovery_behaviour")
