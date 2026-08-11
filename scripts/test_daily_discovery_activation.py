#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily discovery must target only new IDs and sit before daily acceptance."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import daily_discovery_activation as D  # noqa: E402
import pc_identity_discover as PC  # noqa: E402
import snk_identity_discover as SNK  # noqa: E402


GENERATION = "036_20260808T084217Z"


# Negative fixture: without an explicit cursor, every historical gap looks new
# and a scheduled run would rescan hundreds of cards. Missing state must stop.
with tempfile.TemporaryDirectory() as folder:
    missing = Path(folder) / "state.json"
    try:
        D._load_state(missing, GENERATION)
    except SystemExit as error:
        assert "not initialized" in str(error)
        print("NEGATIVE_OK uninitialized discovery cursor was rejected")
    else:
        raise AssertionError("missing discovery state did not fail closed")

    state_path = Path(folder) / "state.json"
    D._write_state(state_path, GENERATION, [10, 20], [], "fixture")
    loaded = D._load_state(state_path, GENERATION)
    assert loaded["knownGapIds"] == [10, 20]

    bad = dict(loaded)
    bad["pendingActivationIds"] = [20]
    state_path.write_text(json.dumps(bad), encoding="utf-8")
    try:
        D._load_state(state_path, GENERATION)
    except SystemExit as error:
        assert "overlap" in str(error)
        print("NEGATIVE_OK overlapping gap and activation state was rejected")
    else:
        raise AssertionError("overlapping state did not fail closed")


rows = [
    {"variant_id": 10, "language": "ja", "tcg": "pokemon"},
    {"variant_id": 30, "language": "en", "tcg": "one-piece"},
    {"variant_id": 40, "language": "zh-TW", "tcg": "pokemon"},
]
http = D.plan_gap_delta([10, 20], rows, "http")
assert http == {
    "newIds": [30, 40],
    "targetIds": [40],
    "deferredIds": [30],
    "resolvedKnownIds": [20],
    "currentIds": [10, 30, 40],
}
browser = D.plan_gap_delta([10, 20], rows, "browser")
assert browser["targetIds"] == [30]
assert browser["deferredIds"] == [40]
print("POSITIVE_OK exact new IDs route to HTTP or browser without rescanning old gaps")


class _Cursor:
    def __init__(self, sink: list[tuple[str, tuple[object, ...]]]):
        self.sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params=()):
        self.sink.append((sql, tuple(params)))

    def fetchall(self):
        return []


class _Conn:
    def __init__(self):
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def cursor(self):
        return _Cursor(self.calls)


original_red_list = SNK.R.red_listed_variants
SNK.R.red_listed_variants = lambda: [1717, 1741]
try:
    for label, selector, args in (
        ("snk", SNK.select_targets, (GENERATION, 1000, "", 0, [901, 902])),
        ("pc", PC.select_targets, (GENERATION, 1000, "pokemon", 0, "en", [901, 902])),
    ):
        conn = _Conn()
        selector(conn, *args)
        sql, params = conn.calls[-1]
        assert "v.id IN (" in sql, label
        assert 901 in params and 902 in params, label
finally:
    SNK.R.red_listed_variants = original_red_list

rebuild_source = (ROOT / "pipelines" / "rebuild_036.py").read_text(encoding="utf-8")
assert "si.variant_id IN" in rebuild_source
print("POSITIVE_OK both discoverers and PC reverify enforce the variant-ID scope")


for script_name in ("nightly_collect_accept.ps1", "morning_browser_lanes.ps1"):
    source = (ROOT / "scripts" / script_name).read_text(encoding="utf-8-sig")
    discover_at = source.index("daily-discover-activate")
    accept_at = source.index('operator_control.py" daily-accept')
    assert discover_at < accept_at, script_name
    assert "$discoverExit -eq 0" in source, script_name
print("POSITIVE_OK both scheduled chains gate daily-accept behind discovery")
