"""Quote-revision acceptance has exactly one writer per schema.

Registry schema (market_source_registry exists): only the registry-driven
block (lineage 'metric-history-v2' / 'quote-revision') may run; the 043
legacy block (lineage 'metric-history-v1') must not execute at all.
Pre-registry schema: only the legacy block runs.

A08 2026-08-23 [KNOWN, 3308 probe]: both blocks ran on the live schema and
rewrote the same 52,138 market_metric_history_acceptance rows twice per run
with different lineage_sha256 (legacy set is a strict subset of the registry
set).  This test fails on that code: set CARDZ_TEST_REBUILD_036_PATH to an
older copy of pipelines/rebuild_036.py to watch it fire.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import current_quote_revision as cqr  # noqa: E402

_override = os.environ.get("CARDZ_TEST_REBUILD_036_PATH", "").strip()
if _override:
    _spec = importlib.util.spec_from_file_location("rebuild_036_under_test", _override)
    mod = importlib.util.module_from_spec(_spec)
    assert _spec.loader is not None
    _spec.loader.exec_module(mod)
else:
    import rebuild_036 as mod  # noqa: E402

ACCEPT = "INSERT INTO market_metric_history_acceptance"
LEGACY = "'metric-history-v1','psa10_price','quote-revision'"
REGISTRY = "'metric-history-v2','psa10_price','quote-revision'"
GUARD = "IF(market_metric_history_acceptance.lineage_sha256<>VALUES(lineage_sha256)"
ROWS = 7

checks = 0


def ok(cond: bool, label: str) -> None:
    global checks
    checks += 1
    if not cond:
        raise AssertionError(label)


class FakeCursor:
    def __init__(self, registry_n: int) -> None:
        self.registry_n = registry_n
        self.executed: list[str] = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.executed.append(sql)
        self.rowcount = ROWS if ACCEPT in sql else 0

    def executemany(self, sql, seq):
        self.executed.append(sql)
        self.rowcount = len(list(seq))

    def fetchone(self):
        return {"n": self.registry_n}

    def fetchall(self):
        return []


# The three helpers imported inside the function are not under test.
cqr.apply_eligible_current_quote_revision_view = lambda cur: None
cqr.bootstrap_from_eligible_observations = lambda cur: {"revisionsWritten": 0}
cqr.reconstruct_legacy_generation_quotes = lambda *a, **k: {
    "revisionsWritten": 0, "reusedExistingResolutions": 0}


def run(registry_n: int):
    cur = FakeCursor(registry_n)
    inserted = mod._activation_accept_history(cur, "2026-08-23 10:00:00", {"a" * 64})
    quote_accepts = [s for s in cur.executed if ACCEPT in s and "'quote-revision'" in s]
    legacy = [s for s in quote_accepts if LEGACY in s]
    registry = [s for s in quote_accepts if REGISTRY in s]
    return cur, inserted, quote_accepts, legacy, registry


# 1. registry schema: one writer, the registry block
cur, inserted, quote_accepts, legacy, registry = run(1)
ok(len(legacy) == 0, f"legacy block executed on a registry schema ({len(legacy)}x)")
ok(len(registry) == 1, f"registry block must run exactly once, ran {len(registry)}x")
ok(len(quote_accepts) == 1, f"exactly one quote-revision acceptance writer, saw {len(quote_accepts)}")
ok(inserted.get("legacyQuoteAcceptanceRows") == 0, "legacyQuoteAcceptanceRows must report 0 when skipped")
ok(inserted.get("psa10PriceQuoteRevisions") == ROWS, "psa10PriceQuoteRevisions comes from the registry block")
ok(GUARD in registry[0] and "accepted_at=VALUES(accepted_at)" not in registry[0],
   "registry block keeps the conditional accepted_at stamp (A07)")
ok("market_source_registry sr" in registry[0], "registry block joins market_source_registry")
detect_idx = next(i for i, s in enumerate(cur.executed) if "table_name='market_source_registry'" in s)
first_quote_idx = cur.executed.index(quote_accepts[0])
ok(detect_idx < first_quote_idx, "registry detection must run before any quote acceptance writer")

# 2. pre-registry schema: one writer, the legacy block
cur, inserted, quote_accepts, legacy, registry = run(0)
ok(len(legacy) == 1, f"legacy block must run exactly once without a registry, ran {len(legacy)}x")
ok(len(registry) == 0, "registry block must not run without a registry")
ok(len(quote_accepts) == 1, "exactly one writer on a pre-registry schema")
ok(inserted.get("psa10PriceQuoteRevisions") == ROWS, "psa10PriceQuoteRevisions comes from the legacy block")
ok(GUARD in legacy[0] and "accepted_at=VALUES(accepted_at)" not in legacy[0],
   "legacy block keeps the conditional accepted_at stamp (A07)")
ok("market_source_registry" not in legacy[0], "legacy block must not reference the registry")

print(f"CHECKS {checks} OK")
