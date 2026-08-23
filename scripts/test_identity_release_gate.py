#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§A Step 1 + Step 2: releasing an identity without loosening a gate.

Every check re-seeds the bug it is about and proves the new behaviour FIRES on
the broken shape while the healthy shape keeps working. Nothing here reaches
MySQL, PriceCharting, SNKRDUNK or the network: the connection is a scripted
fake, the replay artifact is a temp directory, and the only page parsing that
happens is the one this file writes.

Run: python -X utf8 scripts/test_identity_release_gate.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import leftover5_go  # noqa: E402
import op_identity_rules  # noqa: E402
import operator_control as OC  # noqa: E402
import rebuild_036 as R  # noqa: E402

WORKSPACE = Path(tempfile.mkdtemp(prefix="cardz-identity-release-gate-"))


# ---------------------------------------------------------------------------
# Catalog rows, copied verbatim out of MySQL 3308 on 2026-08-23 (read-only).
# The brackets are the ones the 2026-08-22 reverify artifact recorded holding
# each card: data/runtime/rebuild-036/pc-identity-reverify-20260822T120850Z.json
# ---------------------------------------------------------------------------
def variant(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "tcg_code": "one-piece", "card_language": "en",
        "set_name": "", "collector_number": "", "canonical_name": "",
        "v_set_code": "", "v_printing_code": "",
        "parallel_code": "base", "printing_code": "",
    }
    row.update(over)
    return row


V2146 = variant(  # Nico Robin, the 1st Anniversary Set print of OP01-017
    set_name="One Piece English Version 1st Anniversary Set",
    collector_number="OP01-017",
    canonical_name="2024 One Piece English Version 1st Anniversary Set"
                   " Nico Robin Base OP01-017",
)
V2126 = variant(  # Nico Robin again -- the Film Red print of the SAME number
    set_name="One Piece Premium Card Collection -One Piece Film Red-",
    collector_number="OP01-017",
    canonical_name="2023 One Piece Premium Card Collection -One Piece Film Red-"
                   " Nico Robin Base OP01-017",
)
V1876 = variant(
    set_name="One Piece Gift Collection 2023", collector_number="OP02-059",
    canonical_name="2023 One Piece Gift Collection 2023 Boa Hancock Base OP02-059",
)
V1974 = variant(
    set_name="One Piece Premium Card Collection -Best Selection Vol.4 -",
    collector_number="OP09-070",
    canonical_name="2025 One Piece Premium Card Collection -Best Selection Vol.4 -"
                   " Nami Base OP09-070",
)
V1424 = variant(  # an OP05 BOOSTER card whose parallel happens to read "1st Anniversary"
    set_name="One Piece OP05-Awakening of the New Era", collector_number="ST01-012",
    v_set_code="ST01", printing_code="base",
    canonical_name="2023 One Piece OP05-Awakening of the New Era"
                   " Monkey D. Luffy 1st Anniversary ST01-012",
)
V2172 = variant(  # catalog says "25th Edition"; PriceCharting says "[25th Anniversary]"
    set_name="One Piece Premium Card Collection 25th Edition",
    collector_number="OP01-001",
    canonical_name="2023 One Piece Premium Card Collection 25th Edition"
                   " Roronoa Zoro Base OP01-001",
)
V1745 = variant(  # printing_code names a treatment: [SP Foil] is another product
    set_name="One Piece OP08-Two Legends", collector_number="OP03-112",
    v_set_code="OP03", printing_code="sp", parallel_code="r",
    canonical_name="2024 One Piece OP08-Two Legends Charlotte Pudding"
                   " Special Alternate Art OP03-112",
)
V2096 = variant(
    set_name="One Piece OP09-Emperors in the New World", collector_number="OP09-004",
    parallel_code="wanted alternate art",
    canonical_name="2024 One Piece OP09-Emperors in the New World Shanks"
                   " Wanted Alternate Art OP09-004",
)
V2033 = variant(  # the PRB01 reissue of OP05-119
    card_language="ja",
    set_name="One Piece Japanese PRB01-Premium Booster -One Piece Card the Best-",
    collector_number="OP05-119",
    canonical_name="2024 One Piece Japanese PRB01-Premium Booster"
                   " -One Piece Card the Best- Monkey D. Luffy Base OP05-119",
)
V267 = variant(  # the OP05 booster card that PRINTS OP05-119
    card_language="ja", set_name="One Piece Japanese OP05-Awakening of the New Era",
    collector_number="OP05-119", v_set_code="OP05",
    printing_code="aa", parallel_code="Alternate Art",
    canonical_name="2023 One Piece Japanese OP05-Awakening of the New Era"
                   " Monkey D. Luffy Alternate Art OP05-119",
)

RULING = ("operator-zero-20260814: live same-number already on board; GemRate"
          " identity kept; PC/SNK exact rejected so card cannot become product_ready")


class FakeCursor:
    def __init__(self, db: "FakeDB") -> None:
        self._db = db
        self._rows: list[dict[str, Any]] = []
        self.rowcount = 0

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        self._db.log.append((sql, params))
        self._rows, self.rowcount = self._db.answer(sql, params)

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def close(self) -> None:
        return None


class FakeDB:
    """A connection that answers by SQL shape and records everything asked."""

    def __init__(self, answer) -> None:
        self._answer = answer
        self.log: list[tuple[str, Any]] = []
        self.commits = 0
        self.rollbacks = 0

    def answer(self, sql: str, params: Any) -> tuple[list[dict[str, Any]], int]:
        return self._answer(sql, params)

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        return None


try:
    # =======================================================================
    # Step 1 -- a bracket that names OUR product, and only ours
    # =======================================================================

    # The bug: nine One Piece cards sat print_signature_mismatch on 2026-08-22
    # holding their own product's page, because the bracket comparison can only
    # read a bracket as a TREATMENT and these brackets are PRODUCTS.
    for label, bracket, row in [
        ("1st Anniversary Set", "1st Anniversary", V2146),
        ("Gift Collection", "Gift Collection", V1876),
        ("Best Selection", "Best Selection", V1974),
        ("Film Red", "Red", V2126),
    ]:
        assert R._pc_print_signature_ok(bracket, row) is True, label
        assert R._pc_bracket_names_our_product(bracket, row) is True, label
    print("POSITIVE_OK a bracket the catalog's own set_name already says is the card's product")

    # RE-SEED. The refusal below is the whole anti-duplicate mechanism, so it
    # has to be shown failing. Corroborating on canonical_name -- the field the
    # rule deliberately does NOT read -- is exactly the mistake, and V1424 is
    # the card that punishes it: an OP05 BOOSTER row whose parallel wording
    # happens to read "1st Anniversary". Under the seeded rule the anniversary
    # product's page becomes acceptable for a booster card.
    def _seeded_bracket_names_our_product(page_parallel: str, row: Any) -> bool:
        agrees, _why = op_identity_rules.product_agrees(
            page_parallel, "",
            str(row.get("set_name") or "") + " " + str(row.get("canonical_name") or ""),
        )
        return agrees

    real_helper = R._pc_bracket_names_our_product
    try:
        R._pc_bracket_names_our_product = _seeded_bracket_names_our_product
        assert R._pc_print_signature_ok("1st Anniversary", V1424) is True
    finally:
        R._pc_bracket_names_our_product = real_helper
    assert R._pc_bracket_names_our_product("1st Anniversary", V1424) is False
    assert R._pc_print_signature_ok("1st Anniversary", V1424) is False
    print("POSITIVE_OK re-seeded (corroborate on canonical_name) the booster card takes the"
          " anniversary page; the shipped rule refuses it")

    # Two cards, one collector number, two products: neither may take the
    # other's page. This is "一個產品唔應該有兩次" expressed as a gate.
    assert R._pc_print_signature_ok("1st Anniversary", V2126) is False
    assert R._pc_print_signature_ok("Red", V2146) is False
    assert R._pc_print_signature_ok("Gift Collection", V2146) is False
    print("NEGATIVE_OK OP01-017's two prints cannot take each other's product page")

    # A bracket that names a TREATMENT never reaches the new branch, and a
    # bracket our set_name does not say is refused even when it does.
    assert R._pc_bracket_printing_code("Wanted") == "wanted"
    assert R._pc_bracket_printing_code("Wanted Poster") == "wanted"
    assert R._pc_print_signature_ok("Wanted", V2096) is False
    assert R._pc_print_signature_ok("SP Foil", V1745) is False
    assert R._pc_print_signature_ok(
        "SP Foil", {"parallel_code": "sr-spc", "printing_code": "sp"}) is False
    assert R._pc_print_signature_ok("25th Anniversary", V2172) is False
    assert R._pc_bracket_names_our_product("25th Anniversary", V2172) is False
    print("NEGATIVE_OK treatment brackets, [SP Foil] and a bracket the catalog"
          " spells differently all stay refused")

    # A bracket that is nothing but a set code carries no distinctive word, so
    # it is answered by the codes the ROW names -- never by the code its
    # collector number prints, which both of these cards print.
    assert R._pc_bracket_names_our_product("PRB01", V2033) is True
    assert R._pc_print_signature_ok("PRB01", V2033) is True
    assert R._pc_bracket_names_our_product("PRB01", V267) is False
    assert R._pc_print_signature_ok("PRB01", V267) is False
    assert R._pc_bracket_names_our_product("OP05", V2033) is False
    assert R._pc_bracket_names_our_product("OP05", V267) is True
    print("NEGATIVE_OK a set-code bracket is answered by the row's own set code,"
          " not by the set its number prints")

    # The bracket-less contract 3e proved is untouched: the new branch re-asks
    # it, so a printing_code that claims a treatment is refused either way.
    assert R._pc_print_signature_ok(
        "", {"parallel_code": "sec", "printing_code": "sp"}) is False
    assert R._pc_print_signature_ok(
        "", {"parallel_code": "sec", "printing_code": "base"}) is True
    assert R._pc_bracket_names_our_product("", V2146) is False
    assert R._pc_print_signature_ok("Gift Collection", {
        **V1876, "printing_code": "sp"}) is False
    print("NEGATIVE_OK the bracket-less base-print rule still decides, and a treated"
          " printing_code is refused with the product bracket too")

    # Pokemon is out of scope on purpose: the vocabulary was derived on One
    # Piece and a second game deciding identities on it is a shipped defect.
    assert R._pc_print_signature_ok("Red", {**V2126, "tcg_code": "pokemon"}) is False
    print("NEGATIVE_OK the product-bracket branch is scoped to one-piece")

    # =======================================================================
    # Step 2a -- stage_pc_replay honours an operator ruling
    # =======================================================================
    PID = "9990001"
    VID = 999001
    REPLAY = WORKSPACE / "replay"
    REPLAY.mkdir(parents=True, exist_ok=True)
    (REPLAY / "replay-manifest.json").write_text(
        json.dumps({"rejects": 0}), encoding="utf-8")
    html_path = REPLAY / f"{PID}.html"
    html_path.write_text("<html>fixture</html>", encoding="utf-8")
    (REPLAY / f"{PID}.json").write_text(json.dumps({
        "pcProductId": PID,
        "captureSha256": R.sha256_file(html_path),
        "capturePath": f"fixture/{PID}.html",
        "capturedAtUtc": "2026-08-22T12:00:00Z",
        "parserVersion": "fixture_v1",
    }), encoding="utf-8")

    IDENTITY = {
        "tcg": "one-piece", "language": "en", "collector": "OP09-004",
        "setText": "One Piece OP09-Emperors in the New World",
        "parallel": "Wanted",  # a treatment bracket: refused by the gate, always
        "canonicalUrl": "https://www.pricecharting.com/game/x/y",
        "heading": "Shanks [Wanted] OP09-004",
    }

    def binding(bind_evidence_json: Any, status: str = "exact") -> dict[str, Any]:
        row = dict(V2096)
        row.update({
            "pid": PID, "variant_id": VID, "match_status": status,
            "bind_evidence_json": bind_evidence_json,
            "canonical_printing_sha256": "", "p_tcg_code": "", "p_card_language": "",
            "p_set_code": "", "p_collector_number": "", "p_edition_code": "",
            "p_finish_code": "",
        })
        return row

    # The pin that already protects some rows here must NOT be what is being
    # measured, or this whole section proves nothing about the ruling.
    assert leftover5_go.hold_exact_against_refresh("pricecharting", VID, PID) is False

    def run_replay(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], FakeDB]:
        def answer(sql: str, params: Any) -> tuple[list[dict[str, Any]], int]:
            if "cardz_schema_version" in sql:
                return [{"1": 1}], 1
            if "FROM catalog_source_identity si" in sql:
                return list(rows), len(rows)
            if "cardz_rebuild_checkpoint" in sql:
                return [{"output_sha256": "deadbeef"}], 1
            return [], 1

        db = FakeDB(answer)
        real_dir, real_identity = R._pc_replay_dir, R._pc_page_identity
        stub_db_runtime = types.ModuleType("db_runtime")
        stub_db_runtime.migrate = lambda *a, **k: None  # type: ignore[attr-defined]
        previous = sys.modules.get("db_runtime")
        try:
            R._pc_replay_dir = lambda generation: REPLAY
            R._pc_page_identity = lambda html: (dict(IDENTITY), "")
            sys.modules["db_runtime"] = stub_db_runtime
            result = R.stage_pc_replay(SimpleNamespace(conn=db, generation="036_fixture"))
        finally:
            R._pc_replay_dir, R._pc_page_identity = real_dir, real_identity
            if previous is None:
                sys.modules.pop("db_runtime", None)
            else:
                sys.modules["db_runtime"] = previous
        return result, db

    def downgrades(db: FakeDB) -> list[Any]:
        return [
            params for sql, params in db.log
            if sql.startswith("UPDATE catalog_source_identity SET match_status=")
        ]

    # RE-SEED: the row exactly as it was before anybody ruled on it. This IS
    # the 2026-08-22 bug -- the reverify lane held the card for its owner's
    # ruling and stage_pc_replay downgraded the very same row on the next
    # replay, because this stage never read the reason.
    unruled, db_unruled = run_replay([binding(json.dumps({"action": "confirm"}))])
    assert unruled["counts"]["parallelSoftMismatch"] == 1
    assert unruled["counts"]["downgradedToReview"] == 1
    assert unruled["counts"].get("operatorRuled", 0) == 0
    assert downgrades(db_unruled) == [("manual_review", PID)]
    print("POSITIVE_OK re-seeded (no ruling on the row) stage_pc_replay downgrades the"
          " binding to manual_review, exactly as it did on 2026-08-22")

    ruled, db_ruled = run_replay([
        binding(json.dumps({"action": "accept", "reason": RULING,
                            "evidence": {"path": "x"}}))
    ])
    assert ruled["counts"]["parallelSoftMismatch"] == 1
    assert ruled["counts"]["downgradedToReview"] == 0
    assert ruled["counts"]["operatorRuled"] == 1
    assert downgrades(db_ruled) == []
    print("NEGATIVE_OK the same row carrying an operator ruling keeps its status and"
          " is counted, and no UPDATE is issued")

    # The prefix is the only key. A reason that names no operator protects
    # nothing -- otherwise every lane's own prose would outrank the page.
    unprefixed, db_unprefixed = run_replay([
        binding(json.dumps({"action": "accept",
                            "reason": "zero-20260814: live same-number already on board"}))
    ])
    assert unprefixed["counts"]["downgradedToReview"] == 1
    assert unprefixed["counts"].get("operatorRuled", 0) == 0
    assert downgrades(db_unprefixed) == [("manual_review", PID)]
    print("NEGATIVE_OK a reason without the operator- prefix protects nothing")

    # A ruling never PROMOTES: the guard sits after the gate refused the page,
    # so the row keeps manual_review rather than being restamped exact.
    held_review, db_review = run_replay([
        binding(json.dumps({"action": "accept", "reason": RULING}), status="manual_review")
    ])
    assert held_review["counts"]["operatorRuled"] == 1
    assert held_review["counts"]["restampedExact"] == 0
    assert held_review["counts"]["upgradedFromReview"] == 0
    assert downgrades(db_review) == []
    print("NEGATIVE_OK a ruling holds a row where it is; it never promotes one")

    # Without bind_evidence_json in the Phase-D select list the guard would
    # read None on every row forever and quietly never fire.
    phase_d = [sql for sql, _ in db_ruled.log if "FROM catalog_source_identity si" in sql]
    assert len(phase_d) == 1
    assert " si.bind_evidence_json," in phase_d[0]
    print("NEGATIVE_OK the Phase-D select list actually asks for si.bind_evidence_json")

    # =======================================================================
    # Step 2b -- operator-rule is the only sanctioned writer of a ruling
    # =======================================================================
    RULED_AT = "2026-08-23T04:05:06.000000Z"
    BEFORE_RULED = {
        "action": "accept", "reason": RULING,
        "evidence": {"type": "provider_page", "sha256": "abc", "path": "p",
                     "canonicalUrl": "u", "capturedAt": "2026-08-22T12:00:00Z"},
    }

    # RE-SEED: this is what operator_adjudication_20260822 did -- write over a
    # standing ruling with no supersede flag, no before image and no receipt.
    after, why = OC.operator_ruling_payload(
        json.dumps(BEFORE_RULED), actor="daddy", action="accept",
        reason_text="page proves it", supersede=False, ruled_at=RULED_AT,
    )
    assert after is None
    assert "already carries an operator ruling" in why and RULING in why
    print("POSITIVE_OK re-seeded (overwrite a standing ruling) operator-rule refuses and"
          " prints the ruling it would have replaced")

    after, why = OC.operator_ruling_payload(
        json.dumps(BEFORE_RULED), actor="daddy", action="accept",
        reason_text="  page  proves  it  ", supersede=True, ruled_at=RULED_AT,
    )
    assert why == "" and after is not None
    assert after["reason"] == "operator-daddy-2026-08-23: page proves it"
    assert R.operator_ruling(after) == after["reason"]
    assert R.operator_ruling(json.dumps(after)) == after["reason"]
    assert after["operatorRuling"]["supersedes"] == RULING
    assert after["previousAction"] == "accept"
    # The evidence block is what proved the binding; a ruling may not edit it.
    assert after["evidence"] == BEFORE_RULED["evidence"]
    print("NEGATIVE_OK --supersede writes an operator- reason that names what it replaced"
          " and leaves the evidence block untouched")

    after, why = OC.operator_ruling_payload(
        {"action": "confirm"}, actor="daddy", action="reject-wrong-printing-source",
        reason_text="replaced by product 10610111", supersede=False, ruled_at=RULED_AT,
    )
    assert after is not None and why == ""
    assert after["action"] in R.REJECTION_VERDICT_ACTIONS
    assert after["previousAction"] == "confirm"
    print("NEGATIVE_OK a row nobody has ruled on needs no --supersede, and a retiring"
          " verdict is one rebuild_036 already counts as a rejection")

    for label, kwargs in [
        ("actor", {"actor": "Daddy Smith", "action": "accept", "reason_text": "x"}),
        ("action", {"actor": "daddy", "action": "Accept It", "reason_text": "x"}),
        ("reason", {"actor": "daddy", "action": "accept", "reason_text": "   "}),
    ]:
        bad, why = OC.operator_ruling_payload(
            {}, supersede=False, ruled_at=RULED_AT, **kwargs)
        assert bad is None, label
        assert label in why, (label, why)
    bad, why = OC.operator_ruling_payload(
        "{not json", actor="daddy", action="accept", reason_text="x",
        supersede=True, ruled_at=RULED_AT)
    assert bad is None and "not JSON" in why
    print("NEGATIVE_OK a bad actor, a bad action, an empty reason and unreadable evidence"
          " are each refused before anything is written")

    # --- the command around it: fail-closed, and never silently -------------
    OC.RULING_DIR = WORKSPACE / "rulings"
    ROW = {"variant_id": VID, "source_code": "pricecharting", "external_entity_id": PID,
           "match_status": "exact", "bind_evidence_json": json.dumps({"action": "confirm"}),
           "evidence_sha256": "sha"}
    RECEIPT = {"capture_sha256": "abc", "capture_path": "p",
               "captured_at": "2026-08-22 12:00:00"}

    def rule_db(row: dict[str, Any] | None, receipt: dict[str, Any] | None,
                update_rowcount: int = 1) -> FakeDB:
        def answer(sql: str, params: Any) -> tuple[list[dict[str, Any]], int]:
            if sql.startswith("SELECT variant_id, source_code"):
                return ([row] if row else []), 1
            if "catalog_provider_capture_receipt" in sql:
                return ([receipt] if receipt else []), 1
            if sql.startswith("UPDATE catalog_source_identity"):
                return [], update_rowcount
            return [], 1
        return FakeDB(answer)

    def call_rule(db: FakeDB, **over: Any) -> int:
        kwargs: dict[str, Any] = dict(
            source_code="pricecharting", variant_id=VID, external_id=PID,
            actor="daddy", action="accept", reason_text="the page proves it",
            conn=db,
        )
        kwargs.update(over)
        return OC.cmd_operator_rule(**kwargs)

    # RE-SEED: a ruling with nothing captured behind it. That is an opinion,
    # and an opinion no gate can overturn is the worst row in the table.
    db = rule_db(ROW, None)
    try:
        call_rule(db, write=True)
        raise AssertionError("a ruling with no capture receipt must not be written")
    except SystemExit as exc:
        assert "catalog_provider_capture_receipt" in str(exc)
    assert not [sql for sql, _ in db.log if sql.startswith("UPDATE")]
    assert db.commits == 0
    print("POSITIVE_OK re-seeded (no provider capture) operator-rule refuses to rule and"
          " writes nothing")

    db = rule_db({**ROW, "variant_id": VID + 1}, RECEIPT)
    try:
        call_rule(db, write=True)
        raise AssertionError("a pid that belongs to another variant must be refused")
    except SystemExit as exc:
        assert "belongs to variant" in str(exc)
    assert db.commits == 0
    db = rule_db(None, RECEIPT)
    try:
        call_rule(db, write=True)
        raise AssertionError("operator-rule must not open a binding row")
    except SystemExit as exc:
        assert "does not" in str(exc) and "open one" in str(exc)
    print("NEGATIVE_OK a pid bound to another variant, and a pid bound to none, are both"
          " refused before any write")

    db = rule_db(ROW, RECEIPT)
    assert call_rule(db) == 0
    assert not [sql for sql, _ in db.log if sql.startswith("UPDATE")]
    assert db.commits == 0
    assert not OC.RULING_DIR.exists()
    print("NEGATIVE_OK without --write the command is a dry run: no UPDATE, no commit,"
          " no receipt file")

    db = rule_db(ROW, RECEIPT)
    assert call_rule(db, write=True) == 0
    updates = [(sql, params) for sql, params in db.log if sql.startswith("UPDATE")]
    assert len(updates) == 1 and db.commits == 1 and db.rollbacks == 0
    assert "updated_at=NOW()" in updates[0][0]
    assert updates[0][1][1:] == ("pricecharting", PID, VID)
    written = json.loads(updates[0][1][0])
    assert R.operator_ruling(written).startswith("operator-daddy-")
    receipts = sorted(OC.RULING_DIR.glob("ruling-*.json"))
    assert len(receipts) == 1
    record = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert record["applied"] is True and record["rowcount"] == 1
    assert json.loads(record["before"]) == {"action": "confirm"}
    assert json.loads(record["after"])["reason"] == written["reason"]
    print("NEGATIVE_OK --write updates exactly one row by primary key and leaves a receipt"
          " carrying the before and after images")

    # The chain moving the row underneath us is not success.
    shutil.rmtree(OC.RULING_DIR, ignore_errors=True)
    db = rule_db(ROW, RECEIPT, update_rowcount=0)
    assert call_rule(db, write=True) == 2
    assert db.commits == 0 and db.rollbacks == 1
    record = json.loads(sorted(OC.RULING_DIR.glob("ruling-*.json"))[0].read_text(
        encoding="utf-8"))
    assert record["applied"] is False and record["rowcount"] == 0
    print("NEGATIVE_OK an UPDATE that changes zero rows rolls back, exits 2, and says so"
          " in the receipt")

    # =======================================================================
    # Step 2c -- the SNK reverify lane can be scoped to one card
    # =======================================================================
    SNK_ROOT = WORKSPACE / "snk-root"
    (SNK_ROOT / "pipelines").mkdir(parents=True, exist_ok=True)
    (SNK_ROOT / "pipelines" / "snk_market_data.py").write_text("# fixture", encoding="utf-8")

    class _Harvested(Exception):
        pass

    SNK_ROWS = [
        {"iid": "198702", "variant_id": 1203},
        {"iid": "471534", "variant_id": 1448},
        {"iid": "349470", "variant_id": 1760},
    ]

    def snk_worklist(variant_ids: list[int] | None) -> tuple[list[int], list[str]]:
        captured: dict[str, Any] = {}

        def answer(sql: str, params: Any) -> tuple[list[dict[str, Any]], int]:
            if "FROM catalog_source_identity si" not in sql:
                return [], 1
            captured["sql"] = sql
            wanted = set(params or ())
            rows = [
                dict(row, match_status="manual_review", bind_evidence_json=None,
                     ruled_elsewhere=None)
                for row in SNK_ROWS
                if not wanted or row["variant_id"] in wanted
            ]
            return rows, len(rows)

        stub = types.ModuleType("snk_market_data")
        stub.PSA10_CONDITION = "psa10"  # type: ignore[attr-defined]

        def _run(worklist, *a, **k):  # noqa: ANN001
            captured["worklist"] = list(worklist)
            raise _Harvested

        stub.run = _run  # type: ignore[attr-defined]
        previous = sys.modules.get("snk_market_data")
        real_root, real_connect = R.ROOT, R.connect
        sys.modules["snk_market_data"] = stub
        try:
            R.ROOT = SNK_ROOT
            R.connect = lambda *a, **k: FakeDB(answer)
            args = SimpleNamespace(credentials_env=None, write=False,
                                   variant_ids=variant_ids)
            try:
                R.cmd_snk_identity_reverify(args)
                raise AssertionError("the fixture harvest should have stopped the lane")
            except _Harvested:
                pass
        finally:
            R.ROOT, R.connect = real_root, real_connect
            if previous is None:
                sys.modules.pop("snk_market_data", None)
            else:
                sys.modules["snk_market_data"] = previous
        return captured.get("worklist", []), [captured["sql"]]

    # RE-SEED: the lane as it shipped -- no scoping at all. Ruling on one card
    # sends the provider a worklist of every held binding in the table.
    worklist, [sql] = snk_worklist(None)
    assert worklist == [198702, 349470, 471534]
    assert "si.variant_id IN" not in sql
    print("POSITIVE_OK re-seeded (no --variant-id) the SNK lane harvests every held"
          " binding, which is what a three-row supersede used to cost")

    worklist, [sql] = snk_worklist([1203])
    assert worklist == [198702]
    assert "si.variant_id IN (%s)" in sql
    print("NEGATIVE_OK --variant-id scopes the SNK lane to the card being ruled on")

    parser_flags = (ROOT / "pipelines" / "operator_control.py").read_text(encoding="utf-8")
    assert 'p_snk_reverify.add_argument(\n        "--variant-id"' in parser_flags
    assert "operator-rule" not in OC.READ_ONLY_COMMANDS
    assert "snk-identity-reverify" not in OC.READ_ONLY_COMMANDS
    print("NEGATIVE_OK snk-identity-reverify takes --variant-id, and neither writer sits"
          " in READ_ONLY_COMMANDS, so both take the operator lease")

finally:
    shutil.rmtree(WORKSPACE, ignore_errors=True)
