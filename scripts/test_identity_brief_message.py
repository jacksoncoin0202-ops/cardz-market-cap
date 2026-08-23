#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fixtures for the morning identity brief (pipelines/identity_brief.py).

Every check re-seeds the failure it guards against and proves the guard FIRES,
then proves the healthy shape stays quiet.  Nothing here touches MySQL, the
network, Telegram, or the real journal: the cursor is a fixture, the artifacts
live in a temp directory, and the dedupe state is a dict.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import identity_brief as B  # noqa: E402

NOW = datetime(2026, 8, 23, 0, 30, tzinfo=timezone.utc)
WORKSPACE = Path(tempfile.mkdtemp(prefix="cardz-identity-brief-"))
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def cleanup() -> None:
    shutil.rmtree(WORKSPACE, ignore_errors=True)


def member(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "gemrate_id": "a" * 40,
        "variant_id": 1000,
        "pop": 1200,
        "cohort": "qualified_market_pending",
        "identity_pending": 0,
        "detail_json": "{}",
        "computed_at": datetime(2026, 8, 20, 17, 16, tzinfo=timezone.utc),
        "tcg_code": "one-piece",
        "card_language": "en",
        "canonical_name": "Monkey D. Luffy",
        "set_name": "Paramount War",
        "set_code": "OP02",
        "collector_number": "013",
        "discovery_status": "identity_ambiguous",
        "blocker_code": "print_signature_mismatch",
        "attempt_count": 4,
        "next_due_at": None,
        "quarantine_until": None,
        "last_outcome": "held",
        "last_reviewed_at": datetime(2026, 8, 22, 12, 8, tzinfo=timezone.utc),
        "exact_n": 0,
        "nonexact_n": 1,
        "pc_review_id": "10395109",
        "snk_review_id": None,
        "ruling": None,
    }
    row.update(overrides)
    return row


class FixtureCursor:
    """Answers the three statements `collect` issues, and nothing else."""

    def __init__(self, members: list[dict[str, Any]], rulings: list[dict[str, Any]] | None = None,
                 generation: str = "036_20260808T084217Z") -> None:
        self.members = members
        self.rulings = rulings or []
        self.generation = generation
        self.statements: list[str] = []
        self._result: list[dict[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.statements.append(" ".join(sql.split()))
        if "ORDER BY computed_at DESC LIMIT 1" in sql:
            self._result = [{"generation_id": self.generation}]
        elif "FROM catalog_rebuild_member rm" in sql:
            assert params is not None and params[0] == self.generation
            self._result = list(self.members)
        elif "FROM catalog_source_identity" in sql:
            self._result = list(self.rulings)
        else:  # a statement nobody accounted for must not read as empty data
            raise AssertionError(f"unexpected statement: {sql[:80]}")

    def fetchall(self) -> list[dict[str, Any]]:
        return self._result

    def fetchone(self) -> dict[str, Any] | None:
        return self._result[0] if self._result else None


def collect(members: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    cursor = FixtureCursor(members, kwargs.pop("rulings", None))
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("business_date", "2026-08-23")
    kwargs.setdefault("commands", {B.BIND_COMMAND: True, B.RULE_COMMAND: True})
    return B.collect(cursor, **kwargs)


try:
    # ------------------------------------------------------- rule 1: partition
    rows = [
        member(variant_id=1, cohort="product_ready", exact_n=1),
        member(variant_id=2, cohort="qualified_market_pending", exact_n=1),
        member(variant_id=3, cohort="qualified_identity", exact_n=1),
        member(variant_id=4, attempt_count=6),                       # needs you
        member(variant_id=5, attempt_count=0),                       # chain retry
        member(variant_id=6, ruling="snkrdunk: operator-zero-20260814: held"),
        member(variant_id=7, card_language="zhCN"),                  # unjudgeable
        member(variant_id=8, cohort="non_qualified",
               detail_json='{"aliasOf":"b0","pendingReasons":[]}'),
        member(variant_id=None, cohort="non_qualified",
               detail_json='{"demotedReason":"provider_carries_no_collector_number"}'),
    ]
    data = collect(rows)
    assert data["population"] == len(rows) == 9
    assert sum(data["headline"].values()) == data["population"]
    assert data["headline"] == {
        B.HEADLINE_LIVE: 1,
        B.HEADLINE_IDENTITY_NOT_LIVE: 2,
        B.HEADLINE_NEEDS_YOU: 1,
        B.HEADLINE_NOT_YOURS: 5,
    }
    assert data["detail"] == {
        B.DETAIL_CHAIN_WILL_RETRY: 1, B.DETAIL_DECIDED: 3, B.DETAIL_UNJUDGEABLE: 1
    }
    print("NEGATIVE_OK the four headline numbers partition the pop>=1000 population exactly once")

    # Re-seed the bug the partition assert exists for: a classifier that drops a
    # row into a bucket the report does not print.  Before the assert this was
    # silent -- the card simply stopped existing in the morning message.
    real_classify = B.classify

    def leaky_classify(row: Any, **kwargs: Any) -> dict[str, Any]:
        verdict = real_classify(row, **kwargs)
        if row.get("variant_id") == 4:
            return {**verdict, "headline": "somewhere_else"}
        return verdict

    B.classify = leaky_classify  # type: ignore[assignment]
    try:
        collect(rows)
        raise AssertionError("the partition assert did not fire on a dropped card")
    except AssertionError as error:
        assert "冇入任何一個 headline bucket" in str(error), error
    finally:
        B.classify = real_classify  # type: ignore[assignment]
    print("POSITIVE_OK a card that falls out of every headline bucket aborts instead of vanishing")

    # ------------------------------------ rule 3: decided / retry are counted only
    settled = [
        ("next_due", member(variant_id=11, attempt_count=9, next_due_at=NOW + timedelta(days=1))),
        ("quarantined", member(variant_id=12, attempt_count=9, quarantine_until=NOW + timedelta(days=2))),
        ("never_attempted", member(variant_id=13, attempt_count=0)),
        ("operator_ruled", member(variant_id=14, attempt_count=9,
                                  ruling="pricecharting: operator-zero-20260814: held")),
        ("alias_of", member(variant_id=15, attempt_count=9, detail_json='{"aliasOf":"c1"}')),
        ("demoted", member(variant_id=16, attempt_count=9,
                           detail_json='{"demotedReason":"print_identity_unrepresentable"}')),
    ]
    for reason_code, row in settled:
        verdict = B.classify(row, now=NOW)
        assert verdict["headline"] == B.HEADLINE_NOT_YOURS, (reason_code, verdict)
        assert verdict["reasonCode"] == reason_code, (reason_code, verdict)
    red_row = member(variant_id=17, attempt_count=9)
    assert B.classify(red_row, now=NOW, red_listed=[17])["reasonCode"] == "red_listed"
    settled_data = collect([row for _, row in settled] + [red_row], red_listed=[17])
    assert settled_data["headline"][B.HEADLINE_NEEDS_YOU] == 0
    assert settled_data["needsYou"] == []
    print("POSITIVE_OK decided and chain-owned rows are counted and never listed as work for the owner")

    # Negative: strip each settling fact and the same card becomes NEEDS-YOU, so
    # the rules above are doing the work rather than the row being inert.
    for _, row in settled:
        bare = dict(row)
        bare.update(next_due_at=None, quarantine_until=None, attempt_count=9,
                    ruling=None, detail_json="{}")
        assert B.classify(bare, now=NOW)["headline"] == B.HEADLINE_NEEDS_YOU
    assert B.classify(red_row, now=NOW, red_listed=[])["headline"] == B.HEADLINE_NEEDS_YOU
    # A reason without the operator- prefix is not a ruling (rebuild_036:1501).
    assert B.classify(member(variant_id=18, attempt_count=9,
                             ruling="zero-20260814: live same-number already on board"),
                      now=NOW)["headline"] == B.HEADLINE_NEEDS_YOU
    assert B.ruling_text("zero-20260814: x") == ""
    assert B.ruling_text("pricecharting: zero-20260814: x") == ""
    assert B.ruling_text("operator-daddy-20260823: x") == "operator-daddy-20260823: x"
    assert B.ruling_text("snkrdunk: operator-zero-20260814: x") == "snkrdunk: operator-zero-20260814: x"
    rebuild_source = (ROOT / "pipelines" / "rebuild_036.py").read_text(encoding="utf-8")
    assert f'OPERATOR_RULING_REASON_PREFIX = "{B.OPERATOR_RULING_REASON_PREFIX}"' in rebuild_source
    print("NEGATIVE_OK the same card is work for the owner once the settling fact is removed")

    # ------------------------------------- 有身份但未出街 (the assignment's wording)
    stuck = collect([
        member(variant_id=21, cohort="qualified_identity", exact_n=1),
        member(variant_id=22, cohort="qualified_market_pending", exact_n=1,
               ruling="pricecharting: operator-zero-20260814: cannot become product_ready"),
    ])
    assert stuck["headline"][B.HEADLINE_IDENTITY_NOT_LIVE] == 2
    assert stuck["identityNotLiveRuled"] == 1
    message, _ = B.render(stuck, seen={}, now=NOW)
    assert "⛔ <b>有身份但未出街</b> 2" in message
    assert "qualified_identity" in message and "qualified_market_pending" in message
    assert "1 條有 operator ruling" in message
    print("POSITIVE_OK cards stuck at qualified_identity/qualified_market_pending are reported as 有身份但未出街")

    # ------------------------------------------ rule 5: line 2 states the cutoff
    closed = B.identity_phase_state(
        [{"status": "DEGRADED", "last_error_code": "IDENTITY_CUTOFF"},
         {"status": "COMPLETED", "last_error_code": None}],
        run_id="cardz-v2:2026-08-23", business_date="2026-08-23",
    )
    assert closed["closedByCutoff"] and closed["cutoffTasks"] == 1
    closed_message, _ = B.render({**stuck, "identityPhase": closed}, seen={}, now=NOW)
    assert "IDENTITY_CUTOFF" in closed_message.splitlines()[1]
    assert "閂咗" in closed_message.splitlines()[1]
    print("POSITIVE_OK a cutoff-closed identity phase is stated on line 2 instead of reading as fresh lane output")

    ran = B.identity_phase_state(
        [{"status": "COMPLETED", "last_error_code": None}],
        run_id="cardz-v2:2026-08-23", business_date="2026-08-23",
    )
    ran_message, _ = B.render({**stuck, "identityPhase": ran}, seen={}, now=NOW)
    assert "未被 IDENTITY_CUTOFF 閂" in ran_message.splitlines()[1]
    unknown_message, _ = B.render({**stuck, "identityPhase": B.identity_phase_state(None)},
                                  seen={}, now=NOW)
    assert "未知" in unknown_message.splitlines()[1]
    assert "唔代表今朝條 lane 行過" in unknown_message.splitlines()[1]
    print("NEGATIVE_OK a phase that ran, and an unreadable journal, each say so on line 2 in their own words")

    # ------------------------------- rule 4: every listed hold carries its date
    artifacts = WORKSPACE / "rebuild-036"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "pc-identity-reverify-20260822T120850Z.json").write_text(
        json.dumps({"counts": {"printSignatureMismatch": 46},
                    "held": [{"variant_id": 1915, "pid": "10395109",
                              "reason": "print_signature_mismatch",
                              "detail": "page=[2nd Anniversary] printing= parallel=base"}]}),
        encoding="utf-8",
    )
    holds = B.load_reverify_holds(artifacts)
    assert holds[1915]["artifactDay"] == "2026-08-22"
    assert holds[1915]["artifact"] == "pc-identity-reverify-20260822T120850Z.json"
    dated = collect([member(variant_id=1915, attempt_count=9)], holds=holds)
    assert dated["needsYou"][0]["evidenceDay"] == "2026-08-22"
    dated_message, _ = B.render(dated, seen={}, now=NOW)
    hold_lines = [line for line in dated_message.splitlines() if "hold=" in line]
    assert hold_lines and all(DATE_RE.search(line) for line in hold_lines), hold_lines
    assert "pc-identity-reverify-20260822T120850Z.json 2026-08-22" in dated_message
    print("POSITIVE_OK a listed hold names the artifact and the date its reason came from")

    # Re-seed: no artifact at all.  The line must fall back to the ledger date or
    # say plainly that no lane ever looked -- never render a blank provenance.
    orphan = collect([member(variant_id=1916, attempt_count=9, last_reviewed_at=None)], holds={})
    assert orphan["needsYou"][0]["evidenceFrom"] == "冇任何 lane 記錄"
    orphan_message, _ = B.render(orphan, seen={}, now=NOW)
    assert "冇任何 lane 記錄" in orphan_message
    ledger_only = collect([member(variant_id=1917, attempt_count=9)], holds={})
    assert ledger_only["needsYou"][0]["evidenceDay"] == "2026-08-22"
    print("NEGATIVE_OK a hold with no artifact falls back to the ledger date or says no lane ever looked")

    # ------------------------------- rule 2: the cap is structural, not a truncation
    backlog = [member(variant_id=2000 + n, attempt_count=9, pop=5000 - n) for n in range(200)]
    big = collect(backlog)
    assert big["headline"][B.HEADLINE_NEEDS_YOU] == 200
    big_message, _ = B.render(big, seen={}, now=NOW)
    assert len(big_message) <= B.MESSAGE_BUDGET, len(big_message)
    assert "等你綁</b> 200" in big_message          # the count stays honest
    listed_rows = [line for line in big_message.splitlines() if line.startswith("  • ")]
    assert len(listed_rows) <= B.MAX_NEEDS_YOU, len(listed_rows)
    assert "其餘 195 張見全名單檔，唔喺度截字" in big_message
    assert big_message.count("<code>") == big_message.count("</code>")
    assert not big_message.endswith("…")
    print("POSITIVE_OK 200 backlog cards render under the Telegram budget with the count intact")

    # Re-seed: bypass the cap and render every row, which is what produced a
    # message Telegram cut mid-command.
    uncapped = B._render_at(big, big["needsYou"], with_commands=len(big["needsYou"]), repeat_n=0)
    assert len(uncapped) > B.MESSAGE_BUDGET, len(uncapped)
    cut = uncapped[:B.MESSAGE_BUDGET]
    assert cut.count("<code>") != cut.count("</code>")   # a half command survives a cut
    print("POSITIVE_OK rendering every row overflows the budget and a string cut splits a command in half")

    # ------------------------------------------------------- rule 6: zero prints
    empty = collect([])
    empty_message, _ = B.render(empty, seen={}, now=NOW)
    for expected in (
        "🙋 <b>等你綁</b> 0", "⏳ chain 自己再試 0", "🔒 已裁決 0", "🈲 chain 判唔到 0",
        "⛔ <b>有身份但未出街</b> 0", "🖐 尋日 operator override：0", "母體 0",
    ):
        assert expected in empty_message, expected
    print("NEGATIVE_OK a silent morning prints its zeros so it cannot be mistaken for a broken stage")

    # ------------------------------------------------------------ rule 7: dedupe
    one = collect([member(variant_id=3001, attempt_count=9)], holds=holds)
    first_message, state = B.render(one, seen={}, now=NOW)
    assert "v3001" in first_message
    assert list(state) and all(len(key) == 64 for key in state)
    same_message, _ = B.render(one, seen=state, now=NOW + timedelta(days=1))
    assert "v3001" not in same_message
    assert "之前講過而 reason 冇變 1 張" in same_message
    refloat_message, _ = B.render(one, seen=state, now=NOW + timedelta(days=B.REFLOAT_DAYS + 1))
    assert "v3001" in refloat_message
    changed = collect([member(variant_id=3001, attempt_count=9,
                              blocker_code="product_mismatch")], holds={})
    changed_message, _ = B.render(changed, seen=state, now=NOW + timedelta(days=1))
    assert "v3001" in changed_message
    assert B.hold_key(3001, "a") != B.hold_key(3001, "b")
    print("POSITIVE_OK a repeat hold stays quiet, a changed reason and a 14-day-old hold both refloat")

    notify_source = (ROOT / "scripts" / "notify_hermes.py").read_text(encoding="utf-8")
    assert "seen_manual_review" in notify_source          # the legacy key still exists
    assert B.SEEN_STATE_KEY == "identity_brief_seen"
    assert B.SEEN_STATE_KEY not in notify_source          # and this report does not share it
    print("NEGATIVE_OK the brief keeps its own dedupe key instead of silencing the legacy 037 digest")

    # ------------------------------------------------- links survive, data escapes
    hostile = collect([member(variant_id=4001, attempt_count=9,
                              canonical_name="<script>alert(1)</script>")], holds=holds)
    hostile_message, _ = B.render(hostile, seen={}, now=NOW)
    assert '<a href="' in hostile_message                 # links are markup, not text
    assert "<script>" not in hostile_message
    assert "&lt;script&gt;" in hostile_message
    print("POSITIVE_OK card data is escaped while the links stay real markup")

    chain_source = (ROOT / "pipelines" / "daily_chain_v2.py").read_text(encoding="utf-8")
    start = chain_source.index("ALWAYS_ALERT_EVENTS")
    always_alert = chain_source[start:chain_source.index("\n}", start)]
    assert B.BRIEF_EVENT_TYPE == "identity.brief"
    assert B.BRIEF_EVENT_TYPE not in always_alert, always_alert
    assert "html.escape" in chain_source                  # the escaping path it must avoid
    print("NEGATIVE_OK identity.brief is absent from ALWAYS_ALERT_EVENTS so its links never reach html.escape")

    # ------------------------------- commands the brief hands out must be real
    assert B.operator_commands_available("") == {B.BIND_COMMAND: False, B.RULE_COMMAND: False}
    absent = collect([member(variant_id=5001, attempt_count=9)], holds=holds,
                     commands={B.BIND_COMMAND: False, B.RULE_COMMAND: False})
    absent_message, _ = B.render(absent, seen={}, now=NOW)
    assert f"# {B.BIND_COMMAND} 未落地" in absent_message
    assert f"# {B.RULE_COMMAND} 未落地" in absent_message
    fake_source = (
        f'sub.add_parser("{B.BIND_COMMAND}", help="x")\n'
        f"sub.add_parser('{B.RULE_COMMAND}', help=\"y\")\n"
    )
    assert B.operator_commands_available(fake_source) == {B.BIND_COMMAND: True, B.RULE_COMMAND: True}
    present = collect([member(variant_id=5001, attempt_count=9)], holds=holds)
    present_message, _ = B.render(present, seen={}, now=NOW)
    assert "未落地" not in present_message
    assert f"operator_control.py {B.BIND_COMMAND} --variant-id 5001" in present_message
    assert f"operator_control.py {B.RULE_COMMAND} --source-code pricecharting" in present_message
    print("POSITIVE_OK a subcommand operator_control does not declare is marked instead of pasted as if it worked")

    # And against the REAL parser: whatever operator_control declares today, the
    # marker must agree with it in both directions.  This is the ratchet that
    # stops the brief printing a command nobody can paste.
    live_commands = B._read_operator_commands(B.DEFAULT_OPERATOR_CONTROL)
    live = collect([member(variant_id=5002, attempt_count=9)], holds=holds, commands=live_commands)
    live_message, _ = B.render(live, seen={}, now=NOW)
    for name, declared in live_commands.items():
        assert (f"# {name} 未落地" in live_message) is (not declared), (name, declared)
        assert f"operator_control.py {name} " in live_message
    print("NEGATIVE_OK the marker agrees with what operator_control.py actually declares today")

    # ------------------------- the copied lane budget may not drift from its owner
    activation_source = (ROOT / "pipelines" / "daily_discovery_activation.py").read_text(encoding="utf-8")
    found = re.search(r"^DAILY_PROVIDER_LIMIT\s*=\s*(\d+)", activation_source, re.M)
    assert found is not None
    assert B.LANE_DAILY_BUDGET == int(found.group(1)), (B.LANE_DAILY_BUDGET, found.group(1))
    print("NEGATIVE_OK the lane budget printed in the brief is the one daily_discovery_activation enforces")

    # ---------------------------------- new cards: no receipt is not "no new cards"
    no_receipt = collect([], intake=B.load_intake("2026-08-23", WORKSPACE / "daily-chain-v2"))
    no_receipt_message, _ = B.render(no_receipt, seen={}, now=NOW)
    assert "今日未行過 intake" in no_receipt_message
    assert "<b>唔代表冇新卡</b>" in no_receipt_message
    intake_dir = WORKSPACE / "daily-chain-v2"
    intake_dir.mkdir(parents=True, exist_ok=True)
    (intake_dir / "identity-intake-2026-08-23.json").write_text(
        json.dumps({"censusPath": "x", "censusMtime": "2026-08-20T23:17:00Z", "censusStale": True,
                    "buckets": {"auto": 16, "alias": 7},
                    "interned": [{"gemrateId": "d" * 40, "name": "Snorlax VMAX UR",
                                  "pop": 1240, "verdict": "auto"}],
                    "deferredByRatchet": []}),
        encoding="utf-8",
    )
    receipt = B.load_intake("2026-08-23", intake_dir)
    assert receipt["present"]
    fresh_census = collect([], intake=receipt, census=B.census_state(receipt))
    fresh_message, _ = B.render(fresh_census, seen={}, now=NOW)
    assert "🆕 <b>新卡</b> 1" in fresh_message
    assert "⚠️ census 過期 2026-08-20" in fresh_message
    assert "https://www.gemrate.com/card/" + "d" * 40 in fresh_message
    print("POSITIVE_OK a missing intake receipt never reads as 'no new cards', and a stale census is marked")

    # --------------------------------------------- gap ratchet against the baseline
    ratchet = collect([
        member(variant_id=6001, attempt_count=9, tcg_code="one-piece"),
        member(variant_id=6002, attempt_count=9, tcg_code="pokemon"),
        member(variant_id=6003, cohort="non_qualified", exact_n=0,
               detail_json='{"aliasOf":"z"}', tcg_code="one-piece"),
    ], baseline=json.loads((ROOT / "pipelines" / "discovery_coverage_baseline.json")
                           .read_text(encoding="utf-8")))
    assert ratchet["gapCensus"] == {"one-piece": 1, "pokemon": 1}   # non_qualified excluded
    assert ratchet["gapBaseline"] == {"one-piece": 144, "pokemon": 413}
    ratchet_message, _ = B.render(ratchet, seen={}, now=NOW)
    assert "one-piece 1/144" in ratchet_message and "pokemon 1/413" in ratchet_message
    print("NEGATIVE_OK the gap census counts the same rows rebuild_036's ratchet counts and prints them against the baseline")

    # ------------------------------------------------- yesterday's overrides count
    override_rows = [
        {"source_code": "pricecharting", "variant_id": 1203, "external_entity_id": "11012112",
         "reason": "operator-daddy-20260822: superseded", "updated_at": "2026-08-22 09:00:00"},
        {"source_code": "snkrdunk", "variant_id": 1448, "external_entity_id": "471534",
         "reason": "operator-daddy-20260821: older", "updated_at": "2026-08-21 09:00:00"},
    ]
    overrides = collect([], rulings=override_rows)
    assert len(overrides["overridesYesterday"]) == 1
    override_message, _ = B.render(overrides, seen={}, now=NOW)
    assert "🖐 尋日 operator override：1" in override_message
    print("NEGATIVE_OK only rulings written yesterday count as yesterday's overrides")

    # --------------------------------------------------- the read path is read only
    cursor = FixtureCursor([member()])
    B.collect(cursor, now=NOW, business_date="2026-08-23")
    joined = " ".join(cursor.statements).upper()
    for forbidden in ("INSERT", "UPDATE", "DELETE", "REPLACE", "ALTER", "DROP", "COMMIT"):
        # Word boundaries: `updated_at` is a column, not a write.
        assert re.search(rf"\b{forbidden}\b", joined) is None, forbidden
    source = (ROOT / "pipelines" / "identity_brief.py").read_text(encoding="utf-8")
    assert "OPERATOR_CARD_PRODUCT_PROJECTION" not in source.upper()   # the 6272s runaway view
    assert "SET SESSION max_execution_time" in source
    print("NEGATIVE_OK the brief only reads, never touches the runaway projection view, and caps its own session")



    # ------------------------------------------- the V2 `brief` stage + delivery
    # The stage renders once and hands the finished HTML to the journal; the
    # journal is the only delivery path, and it must carry the markup through
    # untouched.  Nothing here builds a real brief: B.build is a fixture.
    import os  # noqa: E402
    import types  # noqa: E402

    sys.path.insert(0, str(ROOT / "scripts"))
    import daily_chain_v2_stage as STAGE  # noqa: E402
    import notify_hermes as NH  # noqa: E402
    from daily_chain_v2_journal import Journal  # noqa: E402

    brief_html = (
        '🌅 身份日報 2026-08-23\n'
        '<a href="https://www.pricecharting.com/game/x">v3001 Monkey D. Luffy</a>\n'
        '&lt;script&gt;'
    )
    saved_seen: list[dict[str, Any]] = []
    build_calls: list[dict[str, Any]] = []
    TODAY_KEY = "h" * 64
    HOLLOW_HTML = "🌅 身份日報 2026-08-23（等你綁 2；列 0 張，其餘見全名單檔）"
    stage_seen: dict[str, Any] = {"old" * 21 + "x": "2026-08-01"}

    def fake_build(**kwargs: Any) -> dict[str, Any]:
        build_calls.append(kwargs)
        # render() is NOT idempotent across its own stamp: it drops the rows
        # `seen` already lists.  A brief built after today's rows were stamped
        # is hollow -- a bare count, no rows, no bind commands.
        hollow = TODAY_KEY in (kwargs.get("seen") or {})
        return {
            "message": HOLLOW_HTML if hollow else brief_html,
            "data": {"generation": "036_TEST", "population": 4242,
                     "needsYou": [{"variantId": 3001}, {"variantId": 3002}]},
            "seen": {TODAY_KEY: "2026-08-23"},
        }

    def fake_save_seen(seen: Any, path: Any = None) -> None:
        saved_seen.append(dict(seen))
        stage_seen.update(seen)

    state_db = WORKSPACE / "brief-journal.sqlite3"
    journal = Journal(state_db)
    journal.initialise()
    brief_run_id = journal.ensure_run(
        business_date="2026-08-23",
        source_cutoff_at="2026-08-23T01:15:00+00:00",
        sla_at="2026-08-23T02:00:00+00:00",
        final_at="2026-08-23T08:00:00+00:00",
    )["run_id"]
    real_build, real_load, real_save = B.build, B.load_seen, B.save_seen
    real_env = {key: os.environ.get(key) for key in ("CARDZ_V2_STATE_DB", "CARDZ_V2_RUN_ID")}
    try:
        B.build = fake_build                                   # type: ignore[assignment]
        B.load_seen = lambda path=None: dict(stage_seen)        # type: ignore[assignment]
        B.save_seen = fake_save_seen                            # type: ignore[assignment]
        os.environ["CARDZ_V2_STATE_DB"] = str(state_db)
        os.environ["CARDZ_V2_RUN_ID"] = brief_run_id
        stage_result = STAGE.stage_identity_brief(
            types.SimpleNamespace(business_date="2026-08-23")
        )

        events = journal.pending_events(brief_run_id)
        brief_events = [row for row in events if row["event_type"] == B.BRIEF_EVENT_TYPE]
        assert len(brief_events) == 1, events
        payload = json.loads(brief_events[0]["payload_json"])
        assert payload["message"] == brief_html, "the stage must journal the rendered HTML itself"
        assert '<a href="' in payload["message"] and "&lt;script&gt;" in payload["message"]
        assert payload["businessDate"] == "2026-08-23" and payload["runId"] == brief_run_id
        assert build_calls[0]["seen"] == {"old" * 21 + "x": "2026-08-01"}, build_calls[0]
        # The stamp travels in the payload and is applied by deliver_events, so
        # nothing is marked shown before the owner has actually received it.
        assert payload["seen"] == {TODAY_KEY: "2026-08-23"}, payload
        assert saved_seen == [], "a brief nobody has received yet must not be stamped"
        assert stage_result["eventType"] == B.BRIEF_EVENT_TYPE
        assert stage_result["needsYou"] == 2 and stage_result["messageChars"] == len(brief_html)
        print("POSITIVE_OK the brief stage journals its rendered HTML verbatim and defers the stamp to delivery")

        # Same brief twice in one run is one message; a changed brief is a new one.
        STAGE.stage_identity_brief(types.SimpleNamespace(business_date="2026-08-23"))
        again = [row for row in journal.pending_events(brief_run_id)
                 if row["event_type"] == B.BRIEF_EVENT_TYPE]
        assert len(again) == 1, "a retried brief stage must not send the same brief twice"
        assert json.loads(again[0]["payload_json"])["message"] == brief_html, \
            "the retry must still carry the full row list, not a hollow re-render"
        brief_html = brief_html + "\n🖐 尋日 operator override：1"
        STAGE.stage_identity_brief(types.SimpleNamespace(business_date="2026-08-23"))
        changed_events = [row for row in journal.pending_events(brief_run_id)
                          if row["event_type"] == B.BRIEF_EVENT_TYPE]
        assert len(changed_events) == 2, "a brief that changed has something new to say"
        print("NEGATIVE_OK a retried brief dedupes on its own text while a changed brief still ships")
    finally:
        B.build, B.load_seen, B.save_seen = real_build, real_load, real_save
        for key, value in real_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # The seen state is shared with the legacy 037 digest inside one file, so a
    # save must touch this report's key and nothing else.
    state_file = WORKSPACE / "hermes_notify_state.json"
    state_file.write_text(
        json.dumps({"seen_manual_review": {"legacy": "2026-08-01"}, "last_test": "keep"}),
        encoding="utf-8",
    )
    assert B.load_seen(state_file) == {}
    B.save_seen({"k" * 64: "2026-08-23"}, state_file)
    after = json.loads(state_file.read_text(encoding="utf-8"))
    assert after["seen_manual_review"] == {"legacy": "2026-08-01"}
    assert after["last_test"] == "keep"
    assert after[B.SEEN_STATE_KEY] == {"k" * 64: "2026-08-23"}
    assert B.load_seen(state_file) == {"k" * 64: "2026-08-23"}
    print("POSITIVE_OK saving the brief's dedupe state leaves the legacy digest's key alone")

    # ------------------------------------------------ notify_hermes identity-brief
    notify_state: dict[str, Any] = {"seen_manual_review": {"legacy": "2026-08-01"}}
    sent: list[str] = []
    real_send, real_load_state, real_save_state = NH.send_message, NH._load_state, NH._save_state
    real_nh_build = B.build
    delivered = [True]
    try:
        B.build = fake_build                                   # type: ignore[assignment]
        NH._load_state = lambda: json.loads(json.dumps(notify_state))  # type: ignore[assignment]
        NH._save_state = lambda state: notify_state.update(state)      # type: ignore[assignment]

        def fake_send(text: str) -> bool:
            sent.append(text)
            return delivered[0]

        NH.send_message = fake_send                            # type: ignore[assignment]

        delivered[0] = False
        failed = NH.cmd_identity_brief(
            types.SimpleNamespace(business_date="2026-08-23", dry_run=False)
        )
        assert failed == 1 and sent, "a dropped send has to be reported, not swallowed"
        assert B.SEEN_STATE_KEY not in notify_state, \
            "a brief nobody received must not be marked as already shown"

        delivered[0] = True
        assert NH.cmd_identity_brief(
            types.SimpleNamespace(business_date="2026-08-23", dry_run=False)
        ) == 0
        assert sent[-1] == brief_html, "the delivered text is the rendered brief"
        assert notify_state[B.SEEN_STATE_KEY] == {"h" * 64: "2026-08-23"}
        assert notify_state["seen_manual_review"] == {"legacy": "2026-08-01"}
        print("POSITIVE_OK a dropped brief keeps its rows unseen and a delivered one stamps only its own key")
    finally:
        B.build = real_nh_build                                # type: ignore[assignment]
        NH.send_message, NH._load_state, NH._save_state = real_send, real_load_state, real_save_state

finally:
    cleanup()
