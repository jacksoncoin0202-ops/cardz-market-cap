#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fixtures for pipelines/gemrate_identity_intake.py (PLAN §C.6).

Every check re-seeds the bug it is guarding against and proves the guard FIRES
(POSITIVE_OK), then proves the healthy shape still passes (NEGATIVE_OK).

No MySQL, no network, no GemRate: the catalog is an in-memory fake whose cursor
answers the exact statements the module issues, and the only capture files are
the ones this file writes into its own temp directory.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import gemrate_identity_intake as intake  # noqa: E402
import rebuild_036 as rebuild  # noqa: E402

GEN = "036_TEST"
WORKSPACE = Path(tempfile.mkdtemp(prefix="cardz-intake-test-"))
NOW = datetime(2026, 8, 22, 12, 0, 0, tzinfo=timezone.utc)

LUFFY_GID = "a" * 40
PERONA_GID = "b" * 40
ALIAS_GID = "c" * 40
SETTLED_GID = "d" * 40
RULED_GID = "e" * 40
TWOROW_GID = "f" * 40
CLASH_GID = "0" * 40


def fingerprint(
    gid: str,
    *,
    name: str = "Monkey D. Luffy",
    set_name: str = "One Piece OP01-Romance Dawn",
    card_number: str = "024",
    parallel: str = "Base",
    psa_rows: int = 1,
    settled: str | None = None,
) -> dict[str, Any]:
    description = f"2022 {set_name} {name} {parallel} {card_number}"
    return {
        "gemrateId": gid,
        "description": description,
        "name": name,
        "year": "2022",
        "setName": set_name,
        "psaSetName": set_name,
        "cardNumber": card_number,
        "cardNumberFull": card_number,
        "parallel": parallel,
        "category": "tcg-cards",
        "derivedLanguage": "en",
        "canonicalUrl": f"https://www.gemrate.com/card/{gid}/slug",
        "settledId": gid if settled is None else settled,
        "slug": "slug",
        "psaRowCount": psa_rows,
        "rawSha256": "0" * 64,
    }


def member(
    gid: str,
    *,
    cohort: str = "non_qualified",
    variant_id: int | None = None,
    population: int = 900,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "gemrate_id": gid,
        "variant_id": variant_id,
        "latest_psa10_population": population,
        "cohort": cohort,
        "identity_pending": 0,
        "detail_json": json.dumps(detail if detail is not None else {"fingerprint": fingerprint(gid)}),
    }


# --------------------------------------------------------------------------
# in-memory catalog
# --------------------------------------------------------------------------


class FakeDB:
    def __init__(self) -> None:
        self.variants: dict[int, dict[str, Any]] = {}
        self.printings: dict[int, dict[str, Any]] = {}
        self.identities: dict[tuple[str, str], dict[str, Any]] = {}
        self.receipts: dict[tuple[str, str], dict[str, Any]] = {}
        self.members: dict[str, dict[str, Any]] = {}
        self.decisions: dict[str, dict[str, Any]] = {}
        self.schema_versions: set[str] = set()
        self.tables: set[str] = set()
        self.gap_rows: list[dict[str, Any]] = []
        self.committed = 0
        self.commits_baseline = 0
        self.rolled_back = 0
        self._next_id = 1000

    def add_variant(self, **row: Any) -> int:
        variant_id = self._next_id
        self._next_id += 1
        record = {
            "id": variant_id, "opaque_id": "", "tcg_code": "one-piece",
            "card_language": "en", "canonical_name": "", "set_name": "",
            "set_code": "", "printing_code": "", "collector_number": "",
        }
        record.update(row)
        self.variants[variant_id] = record
        return variant_id

    def add_printing(self, variant_id: int, sha: str, *, parallel_code: str = "base") -> None:
        self.printings[variant_id] = {
            "variant_id": variant_id,
            "canonical_printing_sha256": sha,
            "parallel_code": parallel_code,
        }


class FakeCursor:
    def __init__(self, db: FakeDB) -> None:
        self.db = db
        self._rows: list[dict[str, Any]] = []
        self.rowcount = 0
        self.lastrowid = 0

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self._rows[0]) if self._rows else None

    def _variant_join(self) -> list[dict[str, Any]]:
        rows = []
        for variant_id, variant in sorted(self.db.variants.items()):
            printing = self.db.printings.get(variant_id, {})
            rows.append({
                **variant,
                "parallel_code": printing.get("parallel_code"),
                "canonical_printing_sha256": printing.get("canonical_printing_sha256"),
            })
        return rows

    def execute(self, sql: str, params: Any = None) -> None:  # noqa: C901
        text = " ".join(sql.split())
        args = list(params or [])
        self._rows = []
        self.rowcount = 0

        if text.startswith("SET SESSION"):
            return
        if "FROM cardz_schema_version" in text:
            if str(args[0]) in self.db.schema_versions:
                self._rows = [{"ok": 1}]
            return
        if "information_schema.TABLES" in text:
            self._rows = [{"n": 1 if str(args[0]) in self.db.tables else 0}]
            return
        if text.startswith("SELECT generation_id FROM catalog_rebuild_member"):
            self._rows = [{"generation_id": GEN}]
            return
        if text.startswith("SELECT id, opaque_id, tcg_code, card_language, set_name, collector_number"):
            self._rows = [dict(v) for v in self._variant_join()]
            return
        if "FROM catalog_variant v LEFT JOIN catalog_printing_identity" in text:
            self._rows = self._variant_join()
            return
        if text.startswith("SELECT external_entity_id FROM catalog_source_identity"):
            source = str(args[0])
            self._rows = [
                {"external_entity_id": eid}
                for (code, eid) in sorted(self.db.identities)
                if code == source
            ]
            return
        if "gemrate_id IN (" in text and "catalog_rebuild_member" in text:
            wanted = {str(value) for value in args[1:]}
            self._rows = [
                dict(row) for gid, row in sorted(self.db.members.items()) if gid in wanted
            ]
            return
        if "rm.cohort <> 'non_qualified'" in text:
            self._rows = [dict(row) for row in self.db.gap_rows]
            return
        if text.startswith("INSERT INTO catalog_variant"):
            variant_id = self.db.add_variant(
                opaque_id=args[0], tcg_code=args[1], card_language=args[2],
                canonical_name=args[3], set_name=args[4], collector_number=args[5],
            )
            self.lastrowid = variant_id
            self.rowcount = 1
            return
        if text.startswith("INSERT INTO catalog_printing_identity"):
            sha = args[6]
            owner = next(
                (vid for vid, row in self.db.printings.items()
                 if row["canonical_printing_sha256"] == sha),
                None,
            )
            if owner is not None:
                raise RuntimeError(f"Duplicate entry '{sha}' for key 'canonical_printing_sha256'")
            self.db.add_printing(int(args[0]), sha, parallel_code=str(args[5]))
            self.rowcount = 1
            return
        if text.startswith("INSERT INTO catalog_source_identity"):
            self.db.identities[("gemrate", str(args[0]))] = {
                "variant_id": int(args[1]), "match_status": "exact",
                "evidence_sha256": args[2],
            }
            self.rowcount = 1
            return
        if text.startswith("INSERT INTO catalog_provider_capture_receipt"):
            self.db.receipts[("gemrate", str(args[0]))] = {
                "capture_sha256": args[1], "capture_path": args[2],
            }
            self.rowcount = 1
            return
        if text.startswith("UPDATE catalog_rebuild_member"):
            variant_id, population, cohort, pending, _at, generation, gid = args
            row = self.db.members.get(str(gid))
            if row is None or generation != GEN or row["variant_id"] is not None:
                self.rowcount = 0
                return
            row["variant_id"] = int(variant_id)
            row["latest_psa10_population"] = max(
                int(row["latest_psa10_population"]), int(population)
            )
            row["cohort"] = str(cohort)
            row["identity_pending"] = int(pending)
            self.rowcount = 1
            return
        if text.startswith("INSERT INTO market_identity_intake_decision"):
            self.db.decisions[str(args[0])] = {
                "generation_id": args[1], "decision": args[2],
                "reason_code": args[3], "variant_id": args[4],
            }
            self.rowcount = 1
            return
        raise AssertionError(f"unrouted SQL: {text[:160]}")


class FakeConnection:
    """A connection whose rollback really un-writes.

    A fake that only counted rollbacks would let a half-written apply look
    clean, which is the exact failure the abort paths below are there to catch.
    """

    def __init__(self, db: FakeDB) -> None:
        self.db = db
        self._snapshot: dict[str, Any] | None = None

    def cursor(self) -> FakeCursor:
        if self._snapshot is None:
            self._snapshot = copy.deepcopy(self.db.__dict__)
        return FakeCursor(self.db)

    def commit(self) -> None:
        self._snapshot = None
        self.db.committed += 1

    def rollback(self) -> None:
        if self._snapshot is not None:
            committed, baseline = self.db.committed, self.db.commits_baseline
            self.db.__dict__.update(copy.deepcopy(self._snapshot))
            self.db.committed, self.db.commits_baseline = committed, baseline
            self._snapshot = None
        self.db.rolled_back += 1


def run_apply(connection: FakeConnection, **kwargs: Any) -> dict[str, Any]:
    """Call intake.apply with a fresh commit watermark for the ledger probe."""
    connection.db.commits_baseline = connection.db.committed
    return intake.apply(connection, **kwargs)


def indexes_from(db: FakeDB) -> intake.CatalogIndexes:
    cursor = FakeCursor(db)
    return intake.load_catalog_indexes(cursor)


# --------------------------------------------------------------------------
# capture fixtures (only apply() needs these)
# --------------------------------------------------------------------------


def write_capture(cards_dir: Path, fp: dict[str, Any]) -> None:
    gid = fp["gemrateId"]
    card_dir = cards_dir / gid
    card_dir.mkdir(parents=True, exist_ok=True)
    raw = {
        "name": fp["name"],
        "year": fp["year"],
        "set_name": fp["setName"],
        "card_number": fp["cardNumber"],
        "parallel": fp["parallel"],
        "category": fp["category"],
        "population_data": [
            {
                "grader": "PSA",
                "description": fp["description"],
                "set_name": fp["psaSetName"],
                "card_number": fp["cardNumber"],
            }
            for _ in range(int(fp["psaRowCount"]))
        ],
    }
    raw_bytes = json.dumps(raw, ensure_ascii=False).encode("utf-8")
    (card_dir / "card.raw.json").write_bytes(raw_bytes)
    (card_dir / "card_details.json").write_text(
        json.dumps({"publicCardPage": {"canonicalUrl": fp["canonicalUrl"]}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (card_dir / "card_details.raw.receipt.json").write_text(
        json.dumps({
            "rawStatus": "captured",
            "sourcePointer": "card.raw.json",
            "contentSha256": rebuild.sha256_bytes(raw_bytes),
        }),
        encoding="utf-8",
    )


try:
    # ------------------------------------------------------------ verdicts
    db = FakeDB()
    db.members = {LUFFY_GID: member(LUFFY_GID)}
    healthy = intake.classify(
        LUFFY_GID, population=1162, member=db.members[LUFFY_GID], indexes=indexes_from(db)
    )
    assert healthy.verdict == "auto", healthy
    assert healthy.resolution == "new_variant"
    assert healthy.variant_id is None
    assert healthy.tcg_code == "one-piece"
    print("NEGATIVE_OK an evidence-complete unowned card classifies auto/new_variant")

    # Re-seed: an aliased row is a card that already lives somewhere else.
    aliased = intake.classify(
        ALIAS_GID, population=1500,
        member=member(ALIAS_GID, detail={"fingerprint": fingerprint(ALIAS_GID), "aliasOf": SETTLED_GID}),
        indexes=indexes_from(db),
    )
    assert aliased.verdict == "alias", aliased
    assert aliased.detail["aliasOf"] == SETTLED_GID
    print("POSITIVE_OK a detail_json.aliasOf row is skipped, never interned")

    # Re-seed the narrower bug: rebuild only records aliasOf when the settled
    # entity is itself a member, so reading aliasOf alone re-mints resettled ids.
    settled_only = member(SETTLED_GID, detail={"fingerprint": fingerprint(SETTLED_GID, settled=LUFFY_GID)})
    assert json.loads(settled_only["detail_json"]).get("aliasOf") is None
    verdict = intake.classify(
        SETTLED_GID, population=1400, member=settled_only, indexes=indexes_from(db)
    )
    assert verdict.verdict == "alias", verdict
    assert verdict.detail["settledId"] == LUFFY_GID
    print("POSITIVE_OK settledId != gemrate_id is an alias even with no aliasOf row")

    ruled_reason = "print_identity_unrepresentable_vs_variant_1952"
    ruled = intake.classify(
        RULED_GID, population=1517,
        member=member(RULED_GID, detail={"fingerprint": fingerprint(RULED_GID), "demotedReason": ruled_reason}),
        indexes=indexes_from(db),
    )
    assert ruled.verdict == "ruled", ruled
    assert ruled.reason_code == ruled_reason, "the ruling must come back verbatim"
    print("POSITIVE_OK a demotedReason row is skipped and its reason returned verbatim")

    # pendingReasons is forced empty for non_qualified rows (rebuild_036.py:1296):
    # a classifier reading it would call this ruled card a clean new card.
    assert json.loads(
        member(RULED_GID, detail={"fingerprint": fingerprint(RULED_GID), "demotedReason": ruled_reason,
                                  "pendingReasons": []})["detail_json"]
    )["pendingReasons"] == []
    print("NEGATIVE_OK the fixture proves pendingReasons is empty on a ruled row, so it is never the key")

    two_rows = intake.classify(
        TWOROW_GID, population=1200,
        member=member(TWOROW_GID, detail={"fingerprint": fingerprint(TWOROW_GID, psa_rows=2)}),
        indexes=indexes_from(db),
    )
    assert two_rows.verdict == "needs_human", two_rows
    assert two_rows.reason_code == "psa_rows:2"
    print("POSITIVE_OK psaRowCount != 1 goes to a human, never to auto")

    qualified = intake.classify(
        LUFFY_GID, population=1162,
        member=member(LUFFY_GID, cohort="product_ready", variant_id=7),
        indexes=indexes_from(db),
    )
    assert qualified.verdict == "already_qualified" and qualified.variant_id == 7
    missing = intake.classify(LUFFY_GID, population=1162, member=None, indexes=indexes_from(db))
    assert missing.verdict == "member_missing"
    print("NEGATIVE_OK an already-qualified member counts, and an absent member is reported not invented")

    # ------------------------------------------------- dedupe / duplication
    ambiguous_db = FakeDB()
    fields, _ = rebuild._derive_print_fields(fingerprint(LUFFY_GID))
    printing_sha = rebuild._gemrate_printing_sha(fields)
    for _ in range(2):
        ambiguous_db.add_variant(
            tcg_code="one-piece", card_language="en",
            set_name=fields["set_name"], collector_number=fields["collector_number"],
            canonical_name="Monkey D. Luffy", opaque_id=f"cmc_dup{_}",
        )
    ambiguous = intake.classify(
        LUFFY_GID, population=1162, member=member(LUFFY_GID), indexes=indexes_from(ambiguous_db)
    )
    assert ambiguous.verdict == "ambiguous", ambiguous
    assert ambiguous.reason_code == "printing_key_hits_2_variants"
    assert ambiguous not in [v for v in [ambiguous] if v.verdict == "auto"]
    interned, deferred = intake.select_interned(
        [v for v in [ambiguous] if v.verdict == "auto"],
        cap={"one-piece": 99}, max_seed=25, census_stale=False,
    )
    assert interned == [] and deferred == [], "an ambiguous row must never reach the write path"
    print("POSITIVE_OK a printing key hitting 2 variants is ambiguous and reaches no INSERT")

    # Re-seed the duplicate-product bug: the printing sha already has an owner,
    # and that owner is a DIFFERENT card wearing the same set/number/parallel.
    clash_db = FakeDB()
    incumbent = clash_db.add_variant(
        tcg_code="one-piece", card_language="en", set_name=fields["set_name"],
        collector_number=fields["collector_number"], canonical_name="Silvers Rayleigh",
        opaque_id="cmc_incumbent",
    )
    clash_db.add_printing(incumbent, printing_sha)
    clash = intake.classify(
        LUFFY_GID, population=1162, member=member(LUFFY_GID), indexes=indexes_from(clash_db)
    )
    assert clash.verdict == "ambiguous", clash
    assert clash.reason_code == f"print_identity_unrepresentable_vs_variant_{incumbent}"
    assert clash.detail["blockers"], "the reason a card is refused must be nameable"
    print("POSITIVE_OK a printing sha owned by a differently named card refuses instead of merging two cards")

    same_db = FakeDB()
    twin = same_db.add_variant(
        tcg_code="one-piece", card_language="en", set_name=fields["set_name"],
        collector_number=fields["collector_number"],
        canonical_name="2022 One Piece OP01-Romance Dawn Monkey D. Luffy Base 024",
        opaque_id="cmc_twin",
    )
    same_db.add_printing(twin, printing_sha)
    merged = intake.classify(
        LUFFY_GID, population=1162, member=member(LUFFY_GID), indexes=indexes_from(same_db)
    )
    assert merged.verdict == "auto" and merged.resolution == "existing_printing_sha"
    assert merged.variant_id == twin, "the same product must bind, not mint a second row"
    print("NEGATIVE_OK the same printing on the same card binds to the incumbent variant")

    # ------------------------------------------------------------- rulings
    # Re-seed the 2026-09 case: one card shares set + number and only the
    # parallel differs (OP13-091 red text vs variant 2255). Ambiguous every day.
    rule_db = FakeDB()
    other_print = rule_db.add_variant(
        tcg_code="one-piece", card_language="en", set_name=fields["set_name"],
        collector_number=fields["collector_number"], canonical_name="Monkey D. Luffy",
        opaque_id="cmc_other_print",
    )
    rule_db.add_printing(other_print, "f" * 64, parallel_code="manga alternate art")
    unruled = intake.classify(
        LUFFY_GID, population=1162, member=member(LUFFY_GID), indexes=indexes_from(rule_db)
    )
    assert unruled.verdict == "ambiguous", unruled
    assert unruled.reason_code == f"printing_key_conflict_vs_variant_{other_print}", unruled
    ruling = {"gemrateId": LUFFY_GID, "ruling": "new_variant", "notVariant": other_print,
              "why": "a different parallel", "ruledAt": "2026-09-24"}
    ruled_new = intake.classify(
        LUFFY_GID, population=1162, member=member(LUFFY_GID), indexes=indexes_from(rule_db),
        rulings={LUFFY_GID: ruling},
    )
    assert ruled_new.verdict == "auto" and ruled_new.resolution == "new_variant", ruled_new
    assert ruled_new.variant_id is None, "a ruled card gets its own variant, never the one it is not"
    assert ruled_new.reason_code == f"ruled_new_variant_vs_variant_{other_print}"
    assert ruled_new.detail["blockers"] and ruled_new.detail["opaqueId"]
    print("POSITIVE_OK a ruling naming the colliding variant opens a new variant, with the blockers kept")

    elsewhere = intake.classify(
        LUFFY_GID, population=1162, member=member(LUFFY_GID), indexes=indexes_from(rule_db),
        rulings={LUFFY_GID: {**ruling, "notVariant": other_print + 1}},
    )
    assert elsewhere.verdict == "ambiguous", "a ruling about another variant decides nothing here"
    print("POSITIVE_OK a ruling about a different variant leaves the card ambiguous")

    clean_db = FakeDB()
    clean = clean_db.add_variant(
        tcg_code="one-piece", card_language="en", set_name=fields["set_name"],
        collector_number=fields["collector_number"], canonical_name="Monkey D. Luffy",
        opaque_id="cmc_clean",
    )
    clean_db.add_printing(clean, "e" * 64, parallel_code="base")
    matched = intake.classify(
        LUFFY_GID, population=1162, member=member(LUFFY_GID), indexes=indexes_from(clean_db)
    )
    assert matched.verdict == "auto" and matched.resolution == "existing_printing", matched
    overruled = intake.classify(
        LUFFY_GID, population=1162, member=member(LUFFY_GID), indexes=indexes_from(clean_db),
        rulings={LUFFY_GID: {**ruling, "notVariant": clean}},
    )
    assert overruled.verdict == "needs_human", overruled
    assert overruled.reason_code == f"ruling_contradicts_clean_match_vs_variant_{clean}"
    print("POSITIVE_OK a ruling never overrides a clean match: the card goes back to a person")

    real_rulings = intake.load_rulings()
    assert real_rulings, "the checked-in rulings file must parse"
    assert {int(r["notVariant"]) for r in real_rulings.values()} >= {2255, 2272}
    assert intake.load_rulings(WORKSPACE / "no-rulings.json") == {}
    for broken in ({"notVariant": 5, "ruledAt": "2026-09-24"},
                   {"notVariant": 5, "why": "x"},
                   {"notVariant": 0, "why": "x", "ruledAt": "2026-09-24"},
                   {"ruling": "merge", "notVariant": 5, "why": "x", "ruledAt": "2026-09-24"}):
        bad_rulings = WORKSPACE / "bad-rulings.json"
        bad_rulings.write_text(json.dumps({"rulings": [
            {"gemrateId": LUFFY_GID, "ruling": "new_variant", **broken}]}), encoding="utf-8")
        try:
            intake.load_rulings(bad_rulings)
        except SystemExit:
            continue
        raise AssertionError(f"a malformed ruling was accepted: {broken}")
    print("POSITIVE_OK a ruling without a reason, a date, a variant or a known kind is refused")

    # ------------------------------------------------------------- ratchet
    autos = [
        intake.Verdict(f"{i:040d}", "auto", "new_variant", 1000 + i, tcg_code="one-piece",
                       resolution="new_variant", detail={"fields": dict(fields)})
        for i in range(5)
    ]
    interned, deferred = intake.select_interned(
        autos, cap={"one-piece": 2}, max_seed=25, census_stale=False
    )
    assert [v.population for v in interned] == [1004, 1003], "highest population first"
    assert len(deferred) == 3
    assert all(row["deferReason"] == "ratchet_cap:one-piece:2" for row in deferred)
    print("POSITIVE_OK intake stops at the ratchet headroom and reports the remainder instead of dropping it")

    interned, deferred = intake.select_interned(
        autos, cap={"one-piece": 99}, max_seed=2, census_stale=False
    )
    assert len(interned) == 2 and all(r["deferReason"] == "max_seed:2" for r in deferred)
    interned, deferred = intake.select_interned(
        autos, cap={"one-piece": 99}, max_seed=25, census_stale=False
    )
    assert len(interned) == 5 and deferred == []
    print("NEGATIVE_OK with headroom and seed budget to spare every auto row is interned")

    interned, deferred = intake.select_interned(
        autos, cap={"one-piece": 99}, max_seed=25, census_stale=True
    )
    assert interned == [] and len(deferred) == 5
    assert all(row["deferReason"] == "census_stale" for row in deferred)
    print("POSITIVE_OK a stale census interns zero cards, and says so per row")

    # -------------------------------------------------------------- census
    # The brute census is always read too; here it lives nowhere unless a
    # check below puts one in place.
    intake.CENSUS_SEARCH_ROOTS = (WORKSPACE / "no-brute",)
    census_path = WORKSPACE / "psa10_1000_plus.jsonl"
    census_path.write_text(
        "\n".join([
            json.dumps({"psa_id": LUFFY_GID, "psa_10": "1,162"}),
            json.dumps({"psa_id": PERONA_GID, "psa_10": 1084}),
            json.dumps({"psa_id": RULED_GID, "psa_10": 999}),
        ]) + "\n",
        encoding="utf-8",
    )
    os.utime(census_path, (NOW.timestamp(), NOW.timestamp()))
    fresh = intake.census(census_path, max_age_days=7, now=NOW)
    assert fresh.stale is False and fresh.missing is False
    assert fresh.rows == {LUFFY_GID: 1162, PERONA_GID: 1084}
    assert fresh.below_floor == 1, "a sub-1000 row is counted, not silently dropped"
    print("NEGATIVE_OK a fresh census parses psa_id/psa_10 and keeps only the pop>=1000 population")

    stale = intake.census(census_path, max_age_days=7, now=NOW + timedelta(days=30))
    assert stale.stale is True and stale.rows, "stale means untrusted, not empty"
    assert stale.as_report()["censusStale"] is True
    absent = intake.census(WORKSPACE / "nope.jsonl", max_age_days=7, now=NOW)
    assert absent.missing is True and absent.stale is True and absent.rows == {}
    print("POSITIVE_OK an old or unreadable census reports stale/missing and can never read as 'no new cards'")

    # Re-seed 2026-09-13..23: V2's merged census went 18 days stale while the
    # daily brute census was fresh, and intake refused every day.
    brute_root = WORKSPACE / "brute-root"
    brute = brute_root / intake.CENSUS_RELATIVE
    brute.parent.mkdir(parents=True)
    brute.write_text(
        "\n".join([
            json.dumps({"psa_id": LUFFY_GID, "psa_10": 1100}),
            json.dumps({"psa_id": PERONA_GID, "psa_10": 1090}),
            json.dumps({"psa_id": CLASH_GID, "psa_10": 1001}),
        ]) + "\n",
        encoding="utf-8",
    )
    os.utime(brute, (NOW.timestamp(), NOW.timestamp()))
    merged_file = WORKSPACE / "gemrate-qualified-latest.jsonl"
    merged_file.write_text(census_path.read_text(encoding="utf-8"), encoding="utf-8")
    old_time = (NOW - timedelta(days=18)).timestamp()
    os.utime(merged_file, (old_time, old_time))
    intake.CENSUS_SEARCH_ROOTS = (brute_root,)
    both = intake.census(merged_file, max_age_days=7, now=NOW)
    assert both.stale is False and both.missing is False, both
    assert both.rows == {LUFFY_GID: 1162, PERONA_GID: 1090, CLASH_GID: 1001}, both.rows
    assert both.path == brute and both.age_days is not None and both.age_days < 1
    assert [(s["path"], s["stale"]) for s in both.sources] == [(str(merged_file), True), (str(brute), False)]
    assert both.as_report()["censusSources"][0]["ageDays"] == 18.0
    print("POSITIVE_OK a stale merged census is read with the fresh brute one: every card, its highest pop, each age reported")

    assert intake.census(merged_file, max_age_days=7, now=NOW + timedelta(days=30)).stale is True
    lost = intake.census(WORKSPACE / "nope.jsonl", max_age_days=7, now=NOW)
    assert lost.missing is False and lost.rows == {LUFFY_GID: 1100, PERONA_GID: 1090, CLASH_GID: 1001}
    assert lost.sources[0] == {"path": str(WORKSPACE / "nope.jsonl"), "missing": True}
    assert len(intake.census(brute, max_age_days=7, now=NOW).sources) == 1
    assert len(intake.census(None, max_age_days=7, now=NOW).sources) == 1
    intake.CENSUS_SEARCH_ROOTS = (WORKSPACE / "no-brute",)
    print("POSITIVE_OK stale only when every source is; a missing file is named; one file is read once")

    # ------------------------------------------------------------- headroom
    head_db = FakeDB()
    head_db.gap_rows = [{"tcg": "pokemon", "language": "en", "population": 1200,
                         "generation_id": GEN, "variant_id": 1}]
    head = intake.headroom(FakeCursor(head_db), reserve=25)
    baseline = json.loads(rebuild.DISCOVERY_BASELINE.read_text(encoding="utf-8"))
    allowed = {str(k): int(v) for k, v in baseline["noCandidateAtPop"].items()}
    assert head["cap"]["pokemon"] == allowed["pokemon"] - 1 - 25
    assert head["cap"]["one-piece"] == allowed["one-piece"] - 25
    head_db.gap_rows = [{"tcg": "one-piece", "language": "en", "population": 1200,
                         "generation_id": GEN, "variant_id": i} for i in range(allowed["one-piece"])]
    exhausted = intake.headroom(FakeCursor(head_db), reserve=25)
    assert exhausted["cap"]["one-piece"] == 0, "a spent ratchet must offer zero seats, never negative"
    print("POSITIVE_OK the ratchet cap is baseline - census - reserve and floors at zero when spent")

    # ------------------------------------------------- apply preconditions
    pre_db = FakeDB()
    refusals = intake.apply_preconditions(FakeCursor(pre_db), fresh)
    assert f"schemaVersionMissing:{intake.SCHEMA_VERSION}" in refusals
    assert f"tableMissing:{intake.DECISION_TABLE}" in refusals
    pre_db.schema_versions.add(intake.SCHEMA_VERSION)
    pre_db.tables.add(intake.DECISION_TABLE)
    assert intake.apply_preconditions(FakeCursor(pre_db), fresh) == []
    assert any(
        r.startswith("censusStale")
        for r in intake.apply_preconditions(FakeCursor(pre_db), stale)
    )
    assert "censusMissing" in intake.apply_preconditions(FakeCursor(pre_db), absent)
    print("POSITIVE_OK --apply refuses without migration 055, without its table, and on a stale census")

    # run() must refuse BEFORE it ever reaches the lease or a write.
    run_db = FakeDB()
    run_db.members = {LUFFY_GID: member(LUFFY_GID), PERONA_GID: member(PERONA_GID)}

    def exploding_apply(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("apply() must not run while a precondition is unmet")

    real_apply = intake.apply
    intake.apply = exploding_apply  # type: ignore[assignment]
    try:
        report, code = intake.run(
            FakeConnection(run_db), census_result=fresh, do_apply=True, now=NOW
        )
    finally:
        intake.apply = real_apply  # type: ignore[assignment]
    assert code == 2 and report["ok"] is False
    assert report["applyRefused"], report["applyRefused"]
    assert report["written"] is None
    assert run_db.committed == 0 and not run_db.variants
    print("POSITIVE_OK run(--apply) with an unmet precondition exits 2 and writes nothing at all")

    report, code = intake.run(FakeConnection(run_db), census_result=fresh, do_apply=False, now=NOW)
    assert code == 0 and report["mode"] == "dry-run"
    assert report["buckets"]["auto"] == 2 and len(report["interned"]) == 2
    assert run_db.committed == 0 and not run_db.variants and not run_db.identities
    print("NEGATIVE_OK the default dry-run classifies and plans without a single write")

    # run() reads the checked-in rulings itself: the V2 stage passes none.
    rule_db.members = {LUFFY_GID: member(LUFFY_GID)}
    real_load = intake.load_rulings
    intake.load_rulings = lambda *a, **k: {LUFFY_GID: ruling}  # type: ignore[assignment]
    try:
        report, code = intake.run(FakeConnection(rule_db), census_result=fresh, do_apply=False, now=NOW)
    finally:
        intake.load_rulings = real_load  # type: ignore[assignment]
    assert code == 0 and [r["reasonCode"] for r in report["interned"]] == [
        f"ruled_new_variant_vs_variant_{other_print}"], report["interned"]
    report, code = intake.run(
        FakeConnection(rule_db), census_result=fresh, do_apply=False, now=NOW, rulings={}
    )
    assert report["interned"] == [] and report["buckets"]["ambiguous"] == 1
    print("POSITIVE_OK run() applies the checked-in rulings with no argument, and only them")

    # --------------------------------------------------------------- apply
    cards_dir = WORKSPACE / "cards"
    luffy_fp = fingerprint(LUFFY_GID)
    perona_fp = fingerprint(PERONA_GID, name="Perona", card_number="093",
                            parallel="Special Alternate Art")
    write_capture(cards_dir, luffy_fp)
    write_capture(cards_dir, perona_fp)

    ledger_calls: list[str] = []

    def fake_rebuild_ledger(cursor: Any) -> dict[str, Any]:
        # The real assert is ledger == catalog; reproduce that shape so a
        # variant minted without a ledger row fails here and not in tomorrow's
        # discover lane.
        catalog = len(cursor.db.variants)
        ledger_calls.append(f"variants={catalog}")
        if cursor.db.committed != cursor.db.commits_baseline:
            raise AssertionError("rebuild_ledger ran outside the intake transaction")
        return {"catalog": catalog, "ledger": catalog}

    import discovery_ledger

    real_ledger = discovery_ledger.rebuild_ledger
    intake.discovery_ledger.rebuild_ledger = fake_rebuild_ledger  # type: ignore[assignment]
    try:
        apply_db = FakeDB()
        apply_db.members = {
            LUFFY_GID: member(LUFFY_GID, population=965),
            PERONA_GID: member(PERONA_GID, population=965,
                               detail={"fingerprint": perona_fp}),
        }
        idx = indexes_from(apply_db)
        verdicts = [
            intake.classify(LUFFY_GID, population=1162, member=apply_db.members[LUFFY_GID], indexes=idx),
            intake.classify(PERONA_GID, population=1084, member=apply_db.members[PERONA_GID], indexes=idx),
            intake.Verdict(RULED_GID, "ruled", ruled_reason, 1517),
        ]
        autos = [v for v in verdicts if v.verdict == "auto"]
        assert len(autos) == 2
        connection = FakeConnection(apply_db)
        written = run_apply(
            connection, generation=GEN, interned=autos, verdicts=verdicts,
            now=NOW, cards_dir=cards_dir,
        )
        assert written["counts"]["variantsCreated"] == 2
        assert written["counts"]["bindings"] == 2
        assert written["counts"]["captureReceipts"] == 2
        assert written["counts"]["membersUpdated"] == 2
        assert written["counts"]["decisions"] == 3, "every verdict is recorded, not only the wins"
        assert apply_db.committed == 1 and apply_db.rolled_back == 0
        for gid in (LUFFY_GID, PERONA_GID):
            row = apply_db.members[gid]
            assert row["cohort"] == "qualified_identity", row
            assert row["identity_pending"] == 0, row
            assert row["variant_id"] is not None
            assert row["latest_psa10_population"] >= 1000
            assert ("gemrate", gid) in apply_db.identities
            assert apply_db.identities[("gemrate", gid)]["match_status"] == "exact"
            assert ("gemrate", gid) in apply_db.receipts
        assert len(apply_db.printings) == 2, "a minted variant must carry its printing sha"
        assert ledger_calls == ["variants=2"], ledger_calls
        assert apply_db.decisions[RULED_GID]["decision"] == "ruled"
        print("POSITIVE_OK apply mints, binds, receipts, moves the member to qualified_identity/pending=0 and rebuilds the ledger in one transaction")

        # Re-run the same census against the post-apply catalog: the members are
        # no longer non_qualified, so nothing is auto and nothing is written.
        idx = indexes_from(apply_db)
        replay = [
            intake.classify(gid, population=pop, member=apply_db.members[gid], indexes=idx)
            for gid, pop in ((LUFFY_GID, 1162), (PERONA_GID, 1084))
        ]
        assert [v.verdict for v in replay] == ["already_qualified", "already_qualified"]
        before = (len(apply_db.variants), len(apply_db.identities))
        run_apply(connection, generation=GEN, interned=[], verdicts=replay,
                  now=NOW, cards_dir=cards_dir)
        assert (len(apply_db.variants), len(apply_db.identities)) == before
        assert ledger_calls == ["variants=2", "variants=2"], ledger_calls
        print("NEGATIVE_OK replaying the same census after apply writes zero catalog rows")

        # Re-seed the member-update bug: the row already carries a variant, so
        # the NULL -> non-NULL guard matches nothing and intake must refuse.
        bad_db = FakeDB()
        bad_db.members = {LUFFY_GID: member(LUFFY_GID, population=965)}
        bad_idx = indexes_from(bad_db)
        bad_verdict = intake.classify(
            LUFFY_GID, population=1162, member=bad_db.members[LUFFY_GID], indexes=bad_idx
        )
        bad_db.members[LUFFY_GID]["variant_id"] = 42  # somebody bound it meanwhile
        bad_connection = FakeConnection(bad_db)
        try:
            run_apply(bad_connection, generation=GEN, interned=[bad_verdict],
                         verdicts=[bad_verdict], now=NOW, cards_dir=cards_dir)
        except RuntimeError as error:
            assert "expected exactly 1" in str(error), error
        else:
            raise AssertionError("a member update touching 0 rows must abort the whole apply")
        assert bad_db.committed == 0 and bad_db.rolled_back == 1
        assert not bad_db.identities and not bad_db.receipts
        print("POSITIVE_OK a member UPDATE that touches != 1 row aborts and rolls the whole transaction back")

        # Re-seed the ledger bug: a ledger that disagrees with the catalog has
        # to stop the transaction, not be logged and ignored.
        def refusing_ledger(cursor: Any) -> dict[str, Any]:
            raise RuntimeError("discovery ledger incomplete: ledger=1 catalog=2")

        intake.discovery_ledger.rebuild_ledger = refusing_ledger  # type: ignore[assignment]
        ledger_db = FakeDB()
        ledger_db.members = {LUFFY_GID: member(LUFFY_GID, population=965)}
        ledger_verdict = intake.classify(
            LUFFY_GID, population=1162, member=ledger_db.members[LUFFY_GID],
            indexes=indexes_from(ledger_db),
        )
        ledger_connection = FakeConnection(ledger_db)
        try:
            run_apply(ledger_connection, generation=GEN, interned=[ledger_verdict],
                         verdicts=[ledger_verdict], now=NOW, cards_dir=cards_dir)
        except RuntimeError as error:
            assert "discovery ledger incomplete" in str(error)
        else:
            raise AssertionError("an incomplete discovery ledger must abort the apply")
        assert ledger_db.committed == 0 and ledger_db.rolled_back == 1
        print("POSITIVE_OK an incomplete discovery ledger aborts the apply instead of shipping a variant no lane can see")

        # Re-seed the capture bug: the evidence a binding claims must exist now,
        # not only when the member row was written days ago.
        intake.discovery_ledger.rebuild_ledger = fake_rebuild_ledger  # type: ignore[assignment]
        gone_db = FakeDB()
        gone_db.members = {LUFFY_GID: member(LUFFY_GID, population=965)}
        gone_verdict = intake.classify(
            LUFFY_GID, population=1162, member=gone_db.members[LUFFY_GID],
            indexes=indexes_from(gone_db),
        )
        empty_cards = WORKSPACE / "no-cards"
        empty_cards.mkdir(exist_ok=True)
        gone_connection = FakeConnection(gone_db)
        try:
            run_apply(gone_connection, generation=GEN, interned=[gone_verdict],
                         verdicts=[gone_verdict], now=NOW, cards_dir=empty_cards)
        except RuntimeError as error:
            assert "capture unusable" in str(error), error
        else:
            raise AssertionError("a missing capture must abort before any binding is written")
        assert gone_db.committed == 0 and not gone_db.variants
        print("POSITIVE_OK a binding whose capture is gone aborts at write time, not at read time")
    finally:
        intake.discovery_ledger.rebuild_ledger = real_ledger  # type: ignore[assignment]

    # ------------------------------------------------------------- receipt
    receipt_path = intake.write_receipt(
        report, business_date="2026-08-22", out=WORKSPACE / "receipt.json"
    )
    saved = json.loads(receipt_path.read_text(encoding="utf-8"))
    for key in ("censusPath", "censusMtime", "censusStale", "generation", "buckets",
                "interned", "deferredByRatchet", "headroom", "needsHuman"):
        assert key in saved, key
    print("NEGATIVE_OK the receipt carries every field the morning brief has to read")

    # The baseline is never a knob intake may turn.
    source = (ROOT / "pipelines" / "gemrate_identity_intake.py").read_text(encoding="utf-8")
    assert "write_text" not in source.split("def write_receipt")[0], \
        "nothing outside the receipt writer may write a file"
    assert "DISCOVERY_BASELINE.write" not in source
    print("POSITIVE_OK intake reads the discovery baseline and never writes it")



    # ----------------------------------------------------- the V2 `intake` stage
    # The stage is the only caller that passes do_apply=True, and the morning brief
    # reads its result for "did a card cross the floor today".  A census nobody
    # could read must therefore never come back out of here looking like a quiet
    # day.  No MySQL: rebuild.connect is replaced by a counting stub.
    import contextlib  # noqa: E402
    import types  # noqa: E402

    sys.path.insert(0, str(ROOT / "pipelines"))
    import daily_chain_v2_stage as STAGE  # noqa: E402


    class StageConn:
        def __init__(self) -> None:
            self.statements: list[str] = []
            self.closed = False

        def cursor(self) -> Any:
            @contextlib.contextmanager
            def _cursor() -> Any:
                yield types.SimpleNamespace(execute=self.statements.append)

            return _cursor()

        def close(self) -> None:
            self.closed = True


    stage_calls: list[dict[str, Any]] = []
    stage_receipts: list[Path] = []
    stage_conns: list[StageConn] = []


    def stage_run(report: dict[str, Any], code: int) -> dict[str, Any]:
        real = (intake.census, intake.run, intake.write_receipt, rebuild.connect)

        def fake_census(path: Any, **kwargs: Any) -> Any:
            stage_calls.append({"call": "census", "maxAgeDays": kwargs.get("max_age_days")})
            return real[0](WORKSPACE / "nope.jsonl", max_age_days=7, now=NOW)

        def fake_run(conn: Any, **kwargs: Any) -> tuple[dict[str, Any], int]:
            stage_calls.append({"call": "run", "doApply": kwargs.get("do_apply"),
                                "maxSeed": kwargs.get("max_seed"), "conn": conn})
            return copy.deepcopy(report), code

        def fake_receipt(payload: Any, *, business_date: str, out: Any = None) -> Path:
            path = WORKSPACE / f"stage-receipt-{business_date}.json"
            path.write_text(json.dumps(dict(payload)), encoding="utf-8")
            stage_receipts.append(path)
            return path

        def fake_connect(*_a: Any, **_k: Any) -> StageConn:
            stage_conns.append(StageConn())
            return stage_conns[-1]

        intake.census = fake_census            # type: ignore[assignment]
        intake.run = fake_run                  # type: ignore[assignment]
        intake.write_receipt = fake_receipt    # type: ignore[assignment]
        rebuild.connect = fake_connect         # type: ignore[assignment]
        try:
            return STAGE.stage_identity_intake(types.SimpleNamespace(
                business_date="2026-08-22", max_age_days=7, max_seed=25,
            ))
        finally:
            intake.census, intake.run, intake.write_receipt, rebuild.connect = real


    stale_report = {
        "generation": GEN, "censusPath": "/gone.jsonl", "censusMtime": None,
        "censusStale": True, "censusMissing": True,
        "buckets": {name: 0 for name in intake.VERDICT_ORDER},
        "headroom": {"cap": {}}, "interned": [], "deferredByRatchet": [],
        "needsHuman": [],
    }
    stale_result = stage_run(stale_report, 0)
    assert stale_result["censusStale"] is True and stale_result["censusMissing"] is True
    assert stale_result["internedCount"] == 0
    assert "NOT" in stale_result.get("censusNote", ""), stale_result
    assert stage_calls[0] == {"call": "census", "maxAgeDays": 7}
    assert stage_calls[1]["call"] == "run" and stage_calls[1]["doApply"] is True
    assert stage_calls[1]["conn"] is stage_conns[-1], "run() must get the stage's own connection"
    assert stage_conns[-1].statements == ["SET SESSION max_execution_time=60000"]
    assert stage_conns[-1].closed is True, "the stage may not leak a production connection"
    assert stage_receipts and stage_receipts[-1].is_file()
    print("POSITIVE_OK a stale census leaves the stage saying so instead of 'no new cards'")

    fresh_report = dict(
        stale_report,
        censusStale=False, censusMissing=False, censusPath="/census.jsonl",
        interned=[{"variantId": 4242, "gemrateId": LUFFY_GID}],
        deferredByRatchet=[{"gemrateId": PERONA_GID}],
        needsHuman=[{"gemrateId": CLASH_GID}],
    )
    fresh_result = stage_run(fresh_report, 0)
    assert "censusNote" not in fresh_result
    assert fresh_result["internedCount"] == 1
    assert fresh_result["internedVariantIds"] == [4242]
    assert fresh_result["deferredByRatchet"] == 1 and fresh_result["needsHuman"] == 1
    print("NEGATIVE_OK a fresh census reports the cards it took in, by variant id")

    # A refusal is not a quiet day either: an unmet precondition (055 not applied)
    # has to fail the stage, not hand back a clean-looking result.
    refused_report = dict(fresh_report, applyRefused=[f"schemaVersionMissing:{intake.SCHEMA_VERSION}"])
    try:
        stage_run(refused_report, 2)
    except RuntimeError as error:
        assert "refused" in str(error) and intake.SCHEMA_VERSION in str(error), str(error)
    else:
        raise AssertionError("an intake refusal came back as success")
    assert stage_receipts[-1].is_file(), "a refusal still leaves its receipt behind"
    assert stage_conns[-1].closed is True
    print("POSITIVE_OK an intake refusal fails the stage and still writes its receipt")


finally:
    shutil.rmtree(WORKSPACE, ignore_errors=True)
