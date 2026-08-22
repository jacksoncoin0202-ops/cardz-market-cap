#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adding a third source must not need a code edit outside the registry.

Covers the area-E findings:
  F-PRICE-AGE      guardrail completeness (proved in test_daily_accept_guardrails)
  F-PRODUCT-ROUTE  operator_control._is_canonical_price_route reads the registry
  F-MINT           the quote `source IN (...)` lists are registry-derived
  F-PUBLIC-SURFACE every collected provider is named in the public-surface gate

Every check comes in a pair: the POSITIVE line proves the branch fires when the
registry changes, the NEGATIVE line proves today's answers did not move.
"""

from __future__ import annotations

import itertools
import re
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import daily_chain_v2_adapters as ADAPTERS  # noqa: E402
import current_quote_revision as CQ  # noqa: E402
import operator_control as OC  # noqa: E402
from collection_contract import SOURCE_ADAPTERS  # noqa: E402
from daily_chain_v2_contract import AdapterRegistry, route_policy_upserts  # noqa: E402


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------
class _StubAdapter:
    """Enough of the SourceAdapter protocol for AdapterRegistry.register."""

    def __init__(self, spec, worker_kind="collect", worker_payload=None):
        self.spec = spec
        self.worker_kind = worker_kind
        self.worker_payload = dict(worker_payload or {})


def _real_specs() -> dict:
    return {
        adapter.spec.source_code: adapter.spec
        for adapter in ADAPTERS.build_default_registry().enabled()
    }


def _install_registry(adapters) -> None:
    registry = AdapterRegistry(adapters)
    ADAPTERS.build_default_registry = lambda: registry
    CQ._quote_source_specs.cache_clear()


def _restore_registry() -> None:
    ADAPTERS.build_default_registry = _REAL_BUILD
    CQ._quote_source_specs.cache_clear()


_REAL_BUILD = ADAPTERS.build_default_registry


class _SqlCursor:
    """Captures the SQL a builder renders; touches no database."""

    def __init__(self):
        self.sql = ""

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return []


def _render_bootstrap_sql() -> str:
    cursor = _SqlCursor()
    CQ.bootstrap_from_eligible_observations(cursor)
    return cursor.sql


# --------------------------------------------------------------------------
# F-PRODUCT-ROUTE
# --------------------------------------------------------------------------
# Captured from the pre-change literal implementation over the full grid below.
LANGUAGES = ["en", "ja", "ko", "zhCN", "zhTW", "zh", "zh-TW", "EN", "", "fr", None]
SOURCES = ["pricecharting", "snkrdunk", "snk", "snk_psa10", "gemrate", "ebay", "", None]
PRIORITIES = [10, 20, 90, 0, None, "10", "x"]
ELIGIBLE = [0, 1, 2, None, "1", "0"]

BASELINE_TRUE = {
    ("en", "pricecharting", 10, 1),
    ("en", "pricecharting", 10, "1"),
    ("en", "pricecharting", "10", 1),
    ("en", "pricecharting", "10", "1"),
    ("en", "snkrdunk", 20, 0),
    ("en", "snkrdunk", 20, "0"),
} | {
    (language, "snkrdunk", priority, eligible)
    for language in ("ja", "ko", "zhCN", "zhTW")
    for priority in (10, "10")
    for eligible in (0, "0")
}


def _route_table() -> set:
    return {
        (language, source, priority, eligible)
        for language, source, priority, eligible in itertools.product(
            LANGUAGES, SOURCES, PRIORITIES, ELIGIBLE
        )
        if OC._is_canonical_price_route(language, source, priority, eligible)
    }


_grid = len(LANGUAGES) * len(SOURCES) * len(PRIORITIES) * len(ELIGIBLE)
_today = _route_table()
assert _grid == 3696, _grid
assert len(BASELINE_TRUE) == 22, len(BASELINE_TRUE)
assert _today == BASELINE_TRUE, sorted(_today ^ BASELINE_TRUE)
print(
    f"NEGATIVE_OK the registry-derived route answers all {_grid} pre-change"
    f" inputs exactly as the literal route did ({len(BASELINE_TRUE)} accepted)"
)

# Positive: move the lead discovery lane to the other quote source.  If the
# gate still named providers, the EN answers below could not move.
_specs = _real_specs()
_swapped = [
    _StubAdapter(replace(_specs["snkrdunk"], identity_lane="browser")),
    _StubAdapter(replace(_specs["pricecharting"], identity_lane="http")),
]
_install_registry(_swapped)
try:
    assert CQ.language_quote_route("en") == ("snkrdunk", "pricecharting"), CQ.language_quote_route("en")
    assert CQ.language_quote_route("ja") == ("pricecharting",), CQ.language_quote_route("ja")
    assert OC._is_canonical_price_route("en", "snkrdunk", 10, 1) is True
    assert OC._is_canonical_price_route("en", "pricecharting", 10, 1) is False
    assert OC._is_canonical_price_route("ja", "pricecharting", 10, 0) is True
    assert OC._is_canonical_price_route("ja", "snkrdunk", 10, 0) is False
finally:
    _restore_registry()
print(
    "POSITIVE_OK moving the lead lane in the registry moves the EN and JA route"
    " answers with it, without a code edit"
)

# Positive: a third quote source joins the non-lead route, and the priority the
# gate demands is the one the pipeline actually seeds.  The row's
# `price_route_priority` comes from market_quote_route_policy, whose only
# registry-driven writer is daily_chain_v2_contract.route_policy_upserts, so the
# assertion is driven by that function instead of a hand-picked literal -- that
# is what proves the two authorities agree.
_ACTIVATED_AT = "2026-08-22 00:00:00.000000"


def _seeded_priorities(specs) -> dict:
    return {
        row[2]: row[3]
        for row in route_policy_upserts(
            specs,
            existing_source_codes=("snkrdunk", "pricecharting"),
            activated_at=_ACTIVATED_AT,
        )
    }


_third = replace(
    _specs["snkrdunk"],
    source_code="dummy-third",
    identity_lane="http",
    route_priority=20,
    concurrency_group="host:dummy-third",
)
_install_registry(
    [_StubAdapter(_specs["snkrdunk"]), _StubAdapter(_specs["pricecharting"]), _StubAdapter(_third)]
)
try:
    _seeded = _seeded_priorities([_specs["snkrdunk"], _specs["pricecharting"], _third])
    assert _seeded == {"dummy-third": 20}, _seeded
    assert CQ.quote_source_codes() == ("snkrdunk", "dummy-third", "pricecharting")
    assert CQ.language_quote_route("en") == ("pricecharting", "snkrdunk", "dummy-third")
    assert CQ.language_quote_route("ja") == ("snkrdunk", "dummy-third")
    assert OC._is_canonical_price_route("ja", "dummy-third", _seeded["dummy-third"], 0) is True
    assert OC._is_canonical_price_route("ja", "dummy-third", 10, 0) is False
    # the lead-lane source is still not on the JA route, so eligible=1 fails
    assert OC._is_canonical_price_route("ja", "snkrdunk", 10, 1) is False
    assert OC._is_canonical_price_route("en", "pricecharting", 10, 1) is True
finally:
    _restore_registry()
print(
    "POSITIVE_OK a third quote source joins the non-lead route at exactly the"
    " priority route_policy_upserts seeds for it"
)

# Positive: the two limits an operator must know about, both fail closed.
# (a) a declared route_priority that does NOT equal the source's position in the
#     route is refused, because the seeded row and the route disagree;
# (b) on the lead language the gate only ever reaches route positions 0 and 1,
#     so a third source is refused there at every priority.
# docs/ADDING_A_SOURCE.md section 7 names both.
_mismatched = replace(_third, route_priority=30)
_install_registry(
    [
        _StubAdapter(_specs["snkrdunk"]),
        _StubAdapter(_specs["pricecharting"]),
        _StubAdapter(_mismatched),
    ]
)
try:
    _seeded_bad = _seeded_priorities([_mismatched])
    assert _seeded_bad == {"dummy-third": 30}, _seeded_bad
    assert CQ.language_quote_route("ja") == ("snkrdunk", "dummy-third")
    assert (
        OC._is_canonical_price_route("ja", "dummy-third", _seeded_bad["dummy-third"], 0)
        is False
    )
    assert CQ.language_quote_route("en") == ("pricecharting", "snkrdunk", "dummy-third")
    assert not [
        (priority, eligible)
        for priority in (10, 20, 30, 40)
        for eligible in (0, 1)
        if OC._is_canonical_price_route("en", "dummy-third", priority, eligible)
    ]
finally:
    _restore_registry()
print(
    "POSITIVE_OK a route_priority that disagrees with the route position is"
    " refused, and the lead language never reaches route position 3"
)

assert _route_table() == BASELINE_TRUE
print("NEGATIVE_OK the real registry answers are restored after the probes")


# --------------------------------------------------------------------------
# F-MINT
# --------------------------------------------------------------------------
# The literal lists that stood in current_quote_revision before the change.
PREVIOUS_QUOTE_IN_LIST = ("snkrdunk", "snk_psa10", "snk", "pricecharting")
PREVIOUS_SNK_IN_LIST = ("snkrdunk", "snk_psa10", "snk")
PREVIOUS_ALIAS_IN_LIST = ("snk", "snk_psa10")

assert CQ.all_quote_storage_source_codes() == PREVIOUS_QUOTE_IN_LIST
assert CQ.sql_source_in_list(CQ.all_quote_storage_source_codes()) == (
    "'snkrdunk','snk_psa10','snk','pricecharting'"
)
assert CQ.quote_storage_source_codes("snkrdunk") == PREVIOUS_SNK_IN_LIST
_case = CQ.canonical_quote_source_sql("p.source_code")
assert set(re.findall(r"'([a-z0-9_]+)'", _case)) == set(PREVIOUS_ALIAS_IN_LIST) | {"snkrdunk"}
assert _case.startswith("CASE WHEN p.source_code IN (") and _case.endswith(
    "THEN 'snkrdunk' ELSE p.source_code END"
)
assert CQ.default_quote_storage_source_codes() == PREVIOUS_SNK_IN_LIST
assert CQ.ungated_quote_storage_source_codes() == PREVIOUS_SNK_IN_LIST
assert CQ.DEFAULT_QUOTE_OBSERVATION_KIND == "psa10_reference_price"
_rendered = _render_bootstrap_sql()
assert "p2.source_code IN ('snkrdunk','snk_psa10','snk','pricecharting')" in _rendered
assert (
    "p2.source_code IN ('snkrdunk','snk_psa10','snk')\n"
    "               AND so2.observation_kind='psa10_reference_price'"
) in _rendered
assert "pc_psa10_local_history_v1" in _rendered
assert "p.source_code IN ('snkrdunk','snk_psa10','snk'))" in _rendered
print(
    "NEGATIVE_OK the generated quote source lists equal the previous literal"
    f" set {PREVIOUS_QUOTE_IN_LIST}"
)

# The IN lists only care about membership and route order, so this probe uses
# the tail priority (30) that puts the new source last in every generated list.
_third_tail = replace(_third, route_priority=30)
_install_registry(
    [
        _StubAdapter(_specs["snkrdunk"]),
        _StubAdapter(_specs["pricecharting"]),
        _StubAdapter(_third_tail),
    ]
)
try:
    assert CQ.all_quote_storage_source_codes() == (
        "snkrdunk",
        "snk_psa10",
        "snk",
        "pricecharting",
        "dummy-third",
    )
    # default observation kind and no language gate, both without an edit
    assert CQ.default_quote_storage_source_codes()[-1] == "dummy-third"
    assert CQ.ungated_quote_storage_source_codes()[-1] == "dummy-third"
    _third_sql = _render_bootstrap_sql()
    assert (
        "p2.source_code IN ('snkrdunk','snk_psa10','snk','pricecharting','dummy-third')"
    ) in _third_sql
    assert "p2.source_code IN ('snkrdunk','snk_psa10','snk','dummy-third')" in _third_sql
    assert "p.source_code IN ('snkrdunk','snk_psa10','snk','dummy-third'))" in _third_sql
finally:
    _restore_registry()
assert CQ.all_quote_storage_source_codes() == PREVIOUS_QUOTE_IN_LIST
print(
    "POSITIVE_OK a third quote source joins every generated IN list and gets the"
    " default observation kind with no language gate"
)


class _FakeCursor:
    def __init__(self):
        self.sql = []
        self.lastrowid = 77

    def execute(self, sql, params=None):
        self.sql.append(sql)

    def fetchone(self):
        return {"id": 77, "reconstructed_from_acceptance_id": None}

    def fetchall(self):
        return []


def _mint(source_code):
    return CQ.insert_quote_revision(
        _FakeCursor(),
        variant_id=1,
        source_code=source_code,
        source_external_entity_id="123",
        price_usd="10.000000",
        source_period_at="2026-08-01",
        checked_at="2026-08-01 00:00:00",
        payload_sha256="a" * 64,
    )


for _registered in PREVIOUS_QUOTE_IN_LIST:
    assert _mint(_registered) == 77, _registered
print(
    "NEGATIVE_OK every registered quote storage code still mints a revision"
    f" {PREVIOUS_QUOTE_IN_LIST}"
)

for _unregistered in ("ebay", "g10", "gemrate", "dummy-third"):
    try:
        _mint(_unregistered)
    except ValueError as error:
        assert "not a registered quote source" in str(error), str(error)
        assert _unregistered in str(error), str(error)
    else:
        raise AssertionError(f"unregistered mint fixture did not fire: {_unregistered}")
print("POSITIVE_OK minting a revision for an unregistered source fails closed")

_install_registry(
    [_StubAdapter(_specs["snkrdunk"]), _StubAdapter(_specs["pricecharting"]), _StubAdapter(_third)]
)
try:
    assert _mint("dummy-third") == 77
finally:
    _restore_registry()
try:
    _mint("dummy-third")
except ValueError:
    print("POSITIVE_OK registering the source is the only thing that unlocks minting")
else:
    raise AssertionError("registry-driven mint gate did not fire")


# --------------------------------------------------------------------------
# F-PUBLIC-SURFACE
# --------------------------------------------------------------------------
# No code change lands here: the gate itself lives in the release checkout.  The
# assertion below is the thing that fails the suite when a new provider is added
# and nobody adds its brand to FORBIDDEN_TOKENS.
GATE_PATH = ROOT / "scripts" / "public-surface-gate.mjs"
_gate_text = GATE_PATH.read_text(encoding="utf-8")
_block = re.search(r"FORBIDDEN_TOKENS\s*=\s*\[(.*?)\]", _gate_text, re.S)
assert _block, f"FORBIDDEN_TOKENS not found in {GATE_PATH}"
FORBIDDEN_TOKENS = tuple(re.findall(r'"([^"]+)"', _block.group(1)))
assert FORBIDDEN_TOKENS, "FORBIDDEN_TOKENS is empty"

# Which registry source owns which collection adapter is declared by the
# adapter's worker payload -- no second mapping to keep in sync.
_owner_of_adapter = {}
for _adapter in _REAL_BUILD().enabled():
    for _key in _adapter.worker_payload.get("adapters") or ():
        assert _key not in _owner_of_adapter, _key
        _owner_of_adapter[_key] = _adapter.spec.source_code

_orphans = sorted(set(SOURCE_ADAPTERS) - set(_owner_of_adapter))
assert not _orphans, (
    f"collection_contract.SOURCE_ADAPTERS keys {_orphans} are claimed by no"
    " enabled registry adapter: add them to that adapter's"
    " worker_payload['adapters'] in daily_chain_v2_adapters"
)
_collected_providers = sorted(set(_owner_of_adapter.values()))
_uncovered = [
    provider
    for provider in _collected_providers
    if not any(token.casefold() == provider.casefold() for token in FORBIDDEN_TOKENS)
]
assert not _uncovered, (
    f"providers {_uncovered} are collected but absent from FORBIDDEN_TOKENS in"
    f" {GATE_PATH}: a public surface could leak the provider name"
)
print(
    "NEGATIVE_OK every collected provider"
    f" {tuple(_collected_providers)} is named in the public-surface gate"
)

_orphan_probe = dict(SOURCE_ADAPTERS)
_orphan_probe["dummy_third_pop"] = {"lane": "http", "runner": "x", "kind": "price"}
assert sorted(set(_orphan_probe) - set(_owner_of_adapter)) == ["dummy_third_pop"]
_provider_probe = [*_collected_providers, "dummy-third"]
assert [
    provider
    for provider in _provider_probe
    if not any(token.casefold() == provider.casefold() for token in FORBIDDEN_TOKENS)
] == ["dummy-third"]
print(
    "POSITIVE_OK an unclaimed collection adapter and a provider missing from"
    " FORBIDDEN_TOKENS are both detected"
)
