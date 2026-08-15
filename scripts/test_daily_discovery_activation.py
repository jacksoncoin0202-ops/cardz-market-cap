#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily discovery must target only new IDs and sit before daily acceptance."""
from __future__ import annotations

import json
import os
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




# Ledger rotation fills residual capacity with oldest unresolved cards; new gaps still win.
ledger_rows = [
    {"variant_id": 50, "discovery_status": "source_not_found", "last_reviewed_at": "2026-08-01 00:00:00", "language": "ja", "tcg": "pokemon"},
    {"variant_id": 60, "discovery_status": "inactive_unresolved", "last_reviewed_at": "2026-07-01 00:00:00", "language": "en", "tcg": "pokemon"},
    {"variant_id": 70, "discovery_status": "active_exact", "last_reviewed_at": "2026-08-01 00:00:00", "language": "ja", "tcg": "pokemon"},
]
retry_http = D.plan_ledger_retry_targets(rows, ledger_rows, "http", limit=10)
assert retry_http["targetIds"] == [50]
retry_browser = D.plan_ledger_retry_targets(rows, ledger_rows, "browser", limit=10)
assert retry_browser["targetIds"] == [60]
print("POSITIVE_OK unresolved ledger cards rotate into residual daily capacity")

# Ordinary misses are durable outcomes, not a fatal gate for the other 1,322
# members. The command owns only fatal transport/DB exceptions; its normal tail
# always returns zero after writing the cursor and attempt receipt.
source = (ROOT / "pipelines" / "daily_discovery_activation.py").read_text(encoding="utf-8")
assert '"dailyDiscovery": "blocked"' not in source
assert "return 1" not in source[source.index("def cmd_daily_discover_activate"):]
assert '"dailyDiscovery": "complete"' in source
print("POSITIVE_OK ordinary unresolved discovery no longer blocks daily-accept")

for script_name in ("nightly_collect_accept.ps1", "morning_browser_lanes.ps1"):
    source = (ROOT / "scripts" / script_name).read_text(encoding="utf-8-sig")
    discover_at = source.index("daily-discover-activate")
    accept_at = source.index('operator_control.py" daily-accept')
    assert discover_at < accept_at, script_name
    assert "CARDZ_DAILY_CHAIN" in source, script_name
    assert "$discoverExit -eq 0" not in source, script_name
    assert "daily-accept still runs" in source, script_name
print("POSITIVE_OK scheduled chains keep daily-accept after discovery failure")

morning = (ROOT / "scripts" / "morning_browser_lanes.ps1").read_text(encoding="utf-8-sig")
http_at = morning.index('incr --adapter http')
cdp_if_at = morning.index("if ($cdpExit -eq 0)")
assert http_at < cdp_if_at
assert "incr --adapter browser --ensure-browser" in morning
print("POSITIVE_OK morning HTTP incr runs even if CDP is down")

sh = (ROOT / "scripts" / "daily_public_release.sh").read_text(encoding="utf-8")
ps1 = (ROOT / "scripts" / "daily_public_release.ps1").read_text(encoding="utf-8-sig")
assert "--scheduled) STAMP_ARGS+=(--scheduled)" in sh
assert "stamp_autonomy" in sh
assert 'env "CARDZ_DAILY_CHAIN=' in ps1
for wrapper_name in (
    "morning_browser_lanes.ps1",
    "refresh_publish.ps1",
    "nightly_collect_accept.ps1",
):
    wrapper = (ROOT / "scripts" / wrapper_name).read_text(encoding="utf-8-sig")
    assert "Test-LaunchedByTaskScheduler" in wrapper, wrapper_name
print("POSITIVE_OK autonomy plumbing: --scheduled + env prefix + TS parent detect")
assert (ROOT / "scripts" / "notify_hermes.py").is_file()
nightly = (ROOT / "scripts" / "nightly_collect_accept.ps1").read_text(encoding="utf-8-sig")
refresh = (ROOT / "scripts" / "refresh_publish.ps1").read_text(encoding="utf-8-sig")
assert 'notify_hermes.py" chain --chain nightly' in nightly
assert "--notify-on failure" in nightly
assert 'notify_hermes.py" chain --chain morning' in morning
assert "--notify-on always" in morning
assert 'notify_hermes.py" digest' in morning
assert 'notify_hermes.py" chain --chain refresh' in refresh
assert "CRASH (see log)" in nightly and "CRASH (see log)" in morning
assert "notify_release" in sh
print("POSITIVE_OK Hermes notify call sites present")
assert (ROOT / "scripts" / "preflight_daily_chain.ps1").is_file()
assert (ROOT / "scripts" / "watchdog_live_release.ps1").is_file()
assert "preflight_daily_chain.ps1" in nightly
assert "preflight_daily_chain.ps1" in morning
assert "preflight_daily_chain.ps1" in refresh
print("POSITIVE_OK preflight wired into three wrappers")

os.environ["CARDZ_DAILY_CHAIN"] = "1"
try:
    assert D.scheduled_daily_chain() is True
    assert D._run_activation("036_fixture") == "deferred"
finally:
    os.environ.pop("CARDZ_DAILY_CHAIN", None)
assert D.scheduled_daily_chain() is False
print("POSITIVE_OK scheduled daily chain defers 036 e2e instead of S0-aborting")


# last_reviewed_at must not reset on unchanged rebuild, or rotation is fake (always lowest ids)
from discovery_ledger import rebuild_ledger  # noqa: E402
class _LedgerCursor:
    def __init__(self):
        self.rows = {}
        self._last = None
        self.queries = []
    def execute(self, sql, params=()):
        self.queries.append((sql, params))
        sql_l = " ".join(sql.split()).lower()
        if sql_l.startswith("select v.id as variant_id"):
            self._last = "select_variants"
        elif "insert into market_identity_discovery_ledger" in sql_l:
            variant_id = int(params[0])
            discovery = params[2]
            blocker = params[5]
            now = params[7]
            existing = self.rows.get(variant_id)
            if existing is None:
                self.rows[variant_id] = {"discovery_status": discovery, "blocker_code": blocker, "last_reviewed_at": now}
            else:
                # emulate IF status changed
                if discovery != existing["discovery_status"] or (blocker or "") != (existing["blocker_code"] or ""):
                    existing["discovery_status"] = discovery
                    existing["blocker_code"] = blocker
                    existing["last_reviewed_at"] = now
                # else keep last_reviewed_at
            self._last = "insert"
        elif "select count(*) as n from market_identity_discovery_ledger" in sql_l:
            self._last = "count_ledger"
        elif "select count(*) as n from catalog_variant" in sql_l:
            self._last = "count_catalog"
        else:
            self._last = "other"
    def fetchall(self):
        if self._last == "select_variants":
            return [
                {"variant_id": 1, "catalog_status": "active", "card_language": "ja", "pc_exact_n": 0, "pc_nonexact_n": 0, "pc_exact": None, "snk_exact_n": 0, "snk_nonexact_n": 0, "snk_exact": None},
                {"variant_id": 2, "catalog_status": "active", "card_language": "en", "pc_exact_n": 0, "pc_nonexact_n": 0, "pc_exact": None, "snk_exact_n": 0, "snk_nonexact_n": 0, "snk_exact": None},
            ]
        return []
    def fetchone(self):
        if self._last == "count_ledger":
            return {"n": len(self.rows)}
        if self._last == "count_catalog":
            return {"n": len(self.rows) or 2}
        return {"n": 0}

# lightweight: ensure ON DUPLICATE SQL preserves last_reviewed_at when status unchanged
src = (ROOT / "pipelines" / "discovery_ledger.py").read_text(encoding="utf-8")
assert "last_reviewed_at=IF(" in src
assert "VALUES(discovery_status)<>market_identity_discovery_ledger.discovery_status" in src
print("POSITIVE_OK discovery ledger keeps last_reviewed_at unless status changes")

