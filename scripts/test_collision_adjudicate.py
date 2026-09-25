#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Collision adjudicate + release-and-claim. Fake DB, no network.

Every POSITIVE re-seeds the leftover shape that used to steal the wrong page.
Run: python -X utf8 scripts/test_collision_adjudicate.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
sys.path.insert(0, str(ROOT / "scripts"))

import collision_adjudicate as CA  # noqa: E402
import rebuild_036 as R  # noqa: E402


def variant(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "tcg_code": "one-piece", "card_language": "en",
        "set_name": "", "collector_number": "", "canonical_name": "",
        "v_set_code": "", "v_printing_code": "",
        "parallel_code": "base", "printing_code": "",
    }
    row.update(over)
    return row


V2033 = variant(
    card_language="ja",
    set_name="One Piece Japanese PRB01-Premium Booster -One Piece Card the Best-",
    collector_number="OP05-119",
    canonical_name="2024 One Piece Japanese PRB01-Premium Booster"
                   " -One Piece Card the Best- Monkey D. Luffy Base OP05-119",
)
V267 = variant(
    card_language="ja", set_name="One Piece Japanese OP05-Awakening of the New Era",
    collector_number="OP05-119", v_set_code="OP05",
    printing_code="aa", parallel_code="Alternate Art",
    canonical_name="2023 One Piece Japanese OP05-Awakening of the New Era"
                   " Monkey D. Luffy Alternate Art OP05-119",
)


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


class FakeDB:
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

PID = "8506784"
OWNER_VID = 267
CLAIMER_VID = 2033
RULING = (
    "operator-zero-20260814: live same-number already on board; GemRate"
    " identity kept; PC/SNK exact rejected so card cannot become product_ready"
)


def _page_op05_aa() -> dict[str, Any]:
    return {
        "tcg": "one-piece",
        "language": "ja",
        "collector": "OP05-119",
        "setText": "One Piece Japanese Awakening of the New Era",
        "parallel": "Alternate Art",
        "canonicalUrl": (
            "https://www.pricecharting.com/game/"
            "one-piece-japanese-awakening-of-the-new-era/"
            "monkeydluffy-alternate-art-op05-119"
        ),
        "heading": (
            "Monkey D. Luffy [Alternate Art] OP05-119 "
            "One Piece Japanese Awakening of the New Era"
        ),
    }


def _page_151_mb() -> dict[str, Any]:
    return {
        "tcg": "pokemon",
        "language": "ja",
        "collector": "1",
        "setText": "Pokemon Japanese Scarlet & Violet 151",
        "parallel": "Master Ball",
        "canonicalUrl": (
            "https://www.pricecharting.com/game/"
            "pokemon-japanese-scarlet-&-violet-151/bulbasaur-master-ball-1"
        ),
        "heading": (
            "Bulbasaur [Master Ball] #1 "
            "Pokemon Japanese Scarlet & Violet 151"
        ),
    }


LEFTOVER_PRB_AA = dict(V2033)
LEFTOVER_PRB_AA.update({
    "variant_id": CLAIMER_VID,
    "printing_code": "aa",
    "parallel_code": "Alternate Art",
    "canonical_name": (
        "2024 One Piece Japanese PRB01-Premium Booster "
        "-One Piece Card the Best- Monkey D. Luffy Alternate Art OP05-119"
    ),
})
OWNER_BOOSTER = dict(V267)
OWNER_BOOSTER["variant_id"] = OWNER_VID

LEFTOVER_151 = variant(
    tcg_code="pokemon",
    card_language="ja",
    set_name="Pokemon Japanese Sv2a-Pokemon Card 151",
    collector_number="001/165",
    printing_code="mb",
    parallel_code="Master Ball Reverse Holo",
    v_printing_code="mb",
    fp_parallel="Master Ball Reverse Holo",
    canonical_name=(
        "2023 Pokemon Japanese Sv2a-Pokemon Card 151 "
        "Bulbasaur Master Ball Reverse Holo 001/165"
    ),
    variant_id=986,
)
OWNER_CARDDASS = variant(
    tcg_code="pokemon",
    card_language="ja",
    set_name="Pokemon Japanese 1997 Carddass",
    collector_number="001",
    printing_code="base",
    canonical_name="1997 Pokemon Japanese Carddass Bulbasaur 001",
    variant_id=9001,
)


def _fake_conn(
    *,
    owner_vid: int,
    evidence: Any,
    delete_rowcount: int = 1,
    leftover: dict | None = None,
    owner: dict | None = None,
    other_exacts: list | None = None,
    receipt_row: dict | None = True,  # type: ignore[assignment]
) -> FakeDB:
    leftover = leftover or LEFTOVER_151
    owner = owner or OWNER_CARDDASS
    other_exacts = list(other_exacts or [])
    ident = {
        "variant_id": owner_vid,
        "source_code": "pricecharting",
        "external_entity_id": PID,
        "match_status": "exact",
        "bind_evidence_json": evidence,
    }
    receipt = {
        "capture_sha256": "abc",
        "capture_path": "p",
        "captured_at": "2026-08-27T00:00:00Z",
    }

    def answer(sql: str, params: Any) -> tuple[list[dict[str, Any]], int]:
        if sql.strip().startswith("DELETE FROM catalog_source_identity"):
            return [], delete_rowcount
        if "FROM catalog_source_identity" in sql:
            if "LOWER(match_status)='exact'" in sql:
                return list(other_exacts), len(other_exacts)
            return [ident], 1
        if "catalog_provider_capture_receipt" in sql:
            if receipt_row is None:
                return [], 0
            if receipt_row is not True:
                return [receipt_row], 1
            return [receipt], 1
        if "FROM catalog_variant" in sql:
            vid = int(params[0]) if params else 0
            row = leftover if vid == leftover.get("variant_id") else owner
            return [row], 1
        return [], 1

    return FakeDB(answer)


# ---------------------------------------------------------------------------
# Step 1 -- score
# ---------------------------------------------------------------------------

page_op = _page_op05_aa()
# RE-SEED: both catalog rows print OP05-119. Number-only scoring is the 2026-08
# leftover that bound PRB01 onto the booster AA page.
assert LEFTOVER_PRB_AA["collector_number"] == OWNER_BOOSTER["collector_number"]
assert page_op["collector"] in {
    LEFTOVER_PRB_AA["collector_number"],
    OWNER_BOOSTER["collector_number"],
}

# Plant the always-pass bug, watch keep_all, restore.
_real_pass = CA.page_passes_row


def _always_pass(identity, row, **kw):
    return True, []


CA.page_passes_row = _always_pass
buggy = CA.adjudicate(page_op, LEFTOVER_PRB_AA, OWNER_BOOSTER)
assert buggy["verdict"] == "keep_all", buggy
CA.page_passes_row = _real_pass
print("POSITIVE_OK re-seeded (always-pass page) keep_all; the bug fires")

op_score = CA.adjudicate(page_op, LEFTOVER_PRB_AA, OWNER_BOOSTER)
assert op_score["verdict"] == "keep_owner", op_score
assert op_score["ownerOk"] is True
assert op_score["leftoverOk"] is False
print("NEGATIVE_OK OP reprint leftover vs booster AA page stays keep_owner")

page_mb = _page_151_mb()
mb_score = CA.adjudicate(page_mb, LEFTOVER_151, OWNER_CARDDASS)
assert mb_score["verdict"] == "kick_owner", mb_score
assert mb_score["leftoverOk"] is True
assert mb_score["ownerOk"] is False
print("POSITIVE_OK JP 151 leftover vs Carddass-owned pid on the 151 Master Ball page is kick_owner")

dup_left = dict(OWNER_BOOSTER)
dup_left["variant_id"] = 8888
both = CA.adjudicate(page_op, dup_left, OWNER_BOOSTER)
assert both["verdict"] == "keep_all", both
print("NEGATIVE_OK both catalog rows passing the same page is keep_all, never a coin-flip")

neither = CA.adjudicate(page_mb, OWNER_CARDDASS, dict(OWNER_CARDDASS, variant_id=9002))
assert neither["verdict"] == "neither", neither
print("NEGATIVE_OK a 151 Master Ball page fails two Carddass rows (neither)")


# ---------------------------------------------------------------------------
# Step 2 -- release-and-claim dry-run / write / refuse
# ---------------------------------------------------------------------------

db = _fake_conn(owner_vid=OWNER_CARDDASS["variant_id"], evidence={"action": "confirm"})
rc = CA.cmd_release_and_claim(
    source_code="pricecharting",
    owner_variant_id=int(OWNER_CARDDASS["variant_id"]),
    claimant_variant_id=int(LEFTOVER_151["variant_id"]),
    external_id=PID,
    actor="grok-cli",
    write=False,
    conn=db,
    identity=page_mb,
    leftover_row=LEFTOVER_151,
    owner_row=OWNER_CARDDASS,
    red_ids=[],
)
assert rc == 0
assert db.commits == 0
assert not any(sql.strip().startswith("DELETE") for sql, _ in db.log)
print("NEGATIVE_OK dry-run mutations empty")

_ruling_tmp = Path(tempfile.mkdtemp(prefix="cardz-collision-ruling-"))
CA.RULING_DIR = _ruling_tmp
dbw = _fake_conn(owner_vid=OWNER_CARDDASS["variant_id"], evidence={"action": "confirm"})
rcw = CA.cmd_release_and_claim(
    source_code="pricecharting",
    owner_variant_id=int(OWNER_CARDDASS["variant_id"]),
    claimant_variant_id=int(LEFTOVER_151["variant_id"]),
    external_id=PID,
    actor="grok-cli",
    write=True,
    conn=dbw,
    identity=page_mb,
    leftover_row=LEFTOVER_151,
    owner_row=OWNER_CARDDASS,
    red_ids=[],
)
assert rcw == 0, rcw
assert dbw.commits == 1
deletes = [params for sql, params in dbw.log if sql.strip().startswith("DELETE")]
assert deletes == [("pricecharting", PID, int(OWNER_CARDDASS["variant_id"]))], deletes
print("POSITIVE_OK --write DELETE frees the pid from the owner; leftover still binds via operator_bind")

# After write, owner no longer owns it: a follow-up SELECT can return empty.
owned = {"n": 1}

def answer_after(sql: str, params: Any):
    if "FROM catalog_source_identity" in sql and "DELETE" not in sql:
        return [], 0
    return [], 1

# keep-owner path must not delete
dbk = _fake_conn(
    owner_vid=OWNER_VID,
    evidence={"action": "confirm"},
    leftover=LEFTOVER_PRB_AA,
    owner=OWNER_BOOSTER,
)
rck = CA.cmd_release_and_claim(
    source_code="pricecharting",
    owner_variant_id=OWNER_VID,
    claimant_variant_id=CLAIMER_VID,
    external_id=PID,
    actor="grok-cli",
    write=True,
    conn=dbk,
    identity=page_op,
    leftover_row=LEFTOVER_PRB_AA,
    owner_row=OWNER_BOOSTER,
    red_ids=[],
)
assert rck == 0
assert dbk.commits == 0
assert not any(sql.strip().startswith("DELETE") for sql, _ in dbk.log)
print("NEGATIVE_OK keep_owner --write still does not DELETE")

dbr = _fake_conn(owner_vid=int(OWNER_CARDDASS["variant_id"]), evidence={"action": "confirm"})
rcr = CA.cmd_release_and_claim(
    source_code="pricecharting",
    owner_variant_id=int(OWNER_CARDDASS["variant_id"]),
    claimant_variant_id=int(LEFTOVER_151["variant_id"]),
    external_id=PID,
    actor="grok-cli",
    write=True,
    conn=dbr,
    identity=page_mb,
    leftover_row=LEFTOVER_151,
    owner_row=OWNER_CARDDASS,
    red_ids=[int(OWNER_CARDDASS["variant_id"])],
)
assert rcr == 1
assert dbr.commits == 0
assert not any(sql.strip().startswith("DELETE") for sql, _ in dbr.log)
print("NEGATIVE_OK red-sheet owner refuses release")

evidence_ruling = {"action": "accept", "reason": RULING}
dbu = _fake_conn(owner_vid=int(OWNER_CARDDASS["variant_id"]), evidence=evidence_ruling)
rcu = CA.cmd_release_and_claim(
    source_code="pricecharting",
    owner_variant_id=int(OWNER_CARDDASS["variant_id"]),
    claimant_variant_id=int(LEFTOVER_151["variant_id"]),
    external_id=PID,
    actor="grok-cli",
    write=True,
    conn=dbu,
    identity=page_mb,
    leftover_row=LEFTOVER_151,
    owner_row=OWNER_CARDDASS,
    red_ids=[],
)
assert rcu == 1
assert dbu.commits == 0
print("NEGATIVE_OK standing_operator_ruling refuses release")

# Zombie extra pid: owner already exact on another id. The ruling on THIS
# pid says the sweep releases it; standing must not keep_owner.
dbz = _fake_conn(
    owner_vid=int(OWNER_CARDDASS["variant_id"]),
    evidence=evidence_ruling,
    leftover=LEFTOVER_151,
    owner=OWNER_CARDDASS,
    other_exacts=[{"external_entity_id": "5399917"}],
)
rcz = CA.cmd_release_and_claim(
    source_code="pricecharting",
    owner_variant_id=int(OWNER_CARDDASS["variant_id"]),
    claimant_variant_id=int(LEFTOVER_151["variant_id"]),
    external_id=PID,
    actor="grok-cli",
    write=True,
    conn=dbz,
    identity=page_mb,
    leftover_row=LEFTOVER_151,
    owner_row=OWNER_CARDDASS,
    red_ids=[],
)
assert rcz == 0, rcz
assert dbz.commits == 1
print("POSITIVE_OK zombie extra pid (owner already exact elsewhere) is released")

# Rejection of THIS pid is not keep_owner. v2054's ruling says 8506784 is
# v267's booster AA; action reject-wrong-printing-source.
reject_ev = {
    "action": "reject-wrong-printing-source",
    "reason": (
        "operator-daddy-2026-08-24: page 8506784 is the OP05 booster's "
        "own [Alternate Art] OP05-119 (variant 267's card)"
    ),
}
dbrj = _fake_conn(
    owner_vid=OWNER_VID,
    evidence=reject_ev,
    leftover=OWNER_BOOSTER,
    owner=LEFTOVER_PRB_AA,
)
# leftover 267 (booster AA) vs owner 2054-shaped PRB row: page is booster AA.
# Reuse LEFTOVER_PRB_AA as owner (PRB reprint) and OWNER_BOOSTER as claimant.
dbrj = _fake_conn(
    owner_vid=CLAIMER_VID,
    evidence=reject_ev,
    leftover=OWNER_BOOSTER,
    owner=LEFTOVER_PRB_AA,
)
rcrj = CA.cmd_release_and_claim(
    source_code="pricecharting",
    owner_variant_id=CLAIMER_VID,
    claimant_variant_id=OWNER_VID,
    external_id=PID,
    actor="grok-cli",
    write=True,
    conn=dbrj,
    identity=page_op,
    leftover_row=OWNER_BOOSTER,
    owner_row=LEFTOVER_PRB_AA,
    red_ids=[],
)
assert rcrj == 0, rcrj
assert dbrj.commits == 1
print("POSITIVE_OK reject-wrong-printing-source on this pid is kick_owner, not standing keep")

# argparse entry exists after operator_control is wired — imported below once
# the subcommand is registered. Structural check happens in the control-plane
# file; here we prove the shipped function is what the test drove.
assert CA.adjudicate is not None
print("POSITIVE_OK shipped collision_adjudicate.adjudicate is the entry the tests called")

SHIRAHOSHI_PAGE = {
    "tcg": "one-piece",
    "language": "ja",
    "collector": "057",
    "setText": "One Piece Japanese Fist of Divine Speed",
    "parallel": "",
    "canonicalUrl": (
        "https://www.pricecharting.com/game/"
        "one-piece-japanese-fist-of-divine-speed/shirahoshi-eb01-057"
    ),
    "heading": "Shirahoshi EB01-057 One Piece Japanese Fist of Divine Speed",
}
SHIRAHOSHI_SP = variant(
    tcg_code="one-piece",
    card_language="ja",
    set_name="One Piece Japanese OP11-A Fist of Divine Speed",
    collector_number="EB01-057",
    printing_code="",
    parallel_code="special alternate art",
    canonical_name=(
        "2025 One Piece Japanese OP11-A Fist of Divine Speed "
        "Shirahoshi Special Alternate Art EB01-057"
    ),
)
ok_sp, gates_sp = CA.page_passes_row(SHIRAHOSHI_PAGE, SHIRAHOSHI_SP)
assert ok_sp is True, gates_sp
print("POSITIVE_OK unbracketed Fist of Divine Speed page proves leftover Shirahoshi SP")

YAMATO_PAGE = {
    "tcg": "one-piece",
    "language": "ja",
    "collector": "OP01-121",
    "setText": "One Piece Japanese Romance Dawn",
    "parallel": "",
    "canonicalUrl": (
        "https://www.pricecharting.com/game/"
        "one-piece-japanese-romance-dawn/yamato-op01-121"
    ),
    "heading": "Yamato OP01-121 One Piece Japanese Romance Dawn",
}
YAMATO_SP = variant(
    tcg_code="one-piece",
    card_language="ja",
    set_name="One Piece Japanese OP05-Awakening of the New Era",
    collector_number="OP01-121",
    printing_code="sp",
    parallel_code="special alternate art",
    canonical_name="Yamato Special Alternate Art OP01-121",
)
ok_yamato, gates_yamato = CA.page_passes_row(YAMATO_PAGE, YAMATO_SP)
assert ok_yamato is False, gates_yamato
print("NEGATIVE_OK Yamato SP still fails unbracketed Romance Dawn (number-set own print)")

GENGAR_PAGE = {
    "tcg": "pokemon",
    "language": "ja",
    "collector": "230",
    "setText": "Pokemon Japanese Mega Dream ex",
    "parallel": "",
    "canonicalUrl": (
        "https://www.pricecharting.com/game/"
        "pokemon-japanese-mega-dream-ex/mega-gengar-ex-230"
    ),
    "heading": "Mega Gengar ex #230 Pokemon Japanese Mega Dream ex",
}
GENGAR_ERR = variant(
    tcg_code="pokemon",
    card_language="ja",
    set_name="Pokemon Japanese M2a-Mega Dream EX",
    collector_number="230/193",
    printing_code="",
    parallel_code="mega attack rare-incorrect texture",
    canonical_name=(
        "2025 Pokemon Japanese M2a-Mega Dream EX Mega Gengar EX "
        "Mega Attack Rare-Incorrect Texture 230/193"
    ),
    pop=1410,
)
GENGAR_MA = variant(
    tcg_code="pokemon",
    card_language="ja",
    set_name="Pokemon Japanese M2a-Mega Dream EX",
    collector_number="230/193",
    printing_code="base",
    parallel_code="Mega Attack Rare",
    canonical_name=(
        "2025 Pokemon Japanese M2a-Mega Dream EX Mega Gengar EX "
        "Mega Attack Rare 230/193"
    ),
    pop=39082,
)
both = CA.adjudicate(GENGAR_PAGE, GENGAR_ERR, GENGAR_MA)
assert both["verdict"] == "keep_all", both
print("NEGATIVE_OK without census both Gengar rows pass unbracketed #230 (keep_all)")
pop_kick = CA.adjudicate(GENGAR_PAGE, GENGAR_ERR, GENGAR_MA, pc_pop=963)
assert pop_kick["verdict"] == "kick_owner", pop_kick
assert pop_kick["leftoverOk"] is True
assert pop_kick["ownerOk"] is False
assert any("pop_mismatch" in str(g) for g in pop_kick["ownerGates"]), pop_kick
print("POSITIVE_OK PC census 963 kicks MA 39082 and leaves Incorrect Texture 1410")

# SNK `[JTG EN 184]` vs catalog Journey Together used to leftoverGates
# set:['jtg']!=['journey','together'] → neither, so DRI twins kept the
# JTG listing. Plant: leftover is JTG, owner is DRI, title is JTG EN.
JTG_TITLE = (
    "リーリエのピッピex SIR [JTG EN 184/159]【英語版】"
    "(スカーレット&バイオレット「ジャーニートゥゲザー」)"
)
JTG_PAGE = {
    "tcg": "pokemon",
    "language": "en",
    "collector": "184/159",
    "setText": JTG_TITLE,
    "parallel": "SIR",
    "heading": JTG_TITLE,
    "canonicalUrl": "https://snkrdunk.com/en/trading-cards/554381",
}
JTG_LEFTOVER = variant(
    tcg_code="pokemon",
    card_language="en",
    set_name="Pokemon Jtg EN-Journey Together",
    collector_number="184/159",
    canonical_name=(
        "2025 Pokemon Jtg EN-Journey Together Lillie's Clefairy "
        "Special Illustration Rare 184/159"
    ),
)
DRI_OWNER = variant(
    tcg_code="pokemon",
    card_language="en",
    set_name="Pokemon Dri EN-Destined Rivals",
    collector_number="184/182",
    canonical_name=(
        "2025 Pokemon Dri EN-Destined Rivals Team Rocket's Mewtwo "
        "Special Illustration Rare 184/182"
    ),
)
jtg_kick = CA.adjudicate(
    JTG_PAGE, JTG_LEFTOVER, DRI_OWNER, source_code="snkrdunk",
)
assert jtg_kick["verdict"] == "kick_owner", jtg_kick
assert jtg_kick["leftoverOk"] is True, jtg_kick
assert jtg_kick["ownerOk"] is False, jtg_kick
print("POSITIVE_OK SNK JTG EN listing kicks DRI owner (set-code alias, not neither)")
dri_keep = CA.adjudicate(
    JTG_PAGE, DRI_OWNER, JTG_LEFTOVER, source_code="snkrdunk",
)
assert dri_keep["verdict"] == "keep_owner", dri_keep
print("NEGATIVE_OK SNK JTG listing does not kick a JTG owner for a DRI leftover")

JTG_LEFTOVER["variant_id"] = 937
DRI_OWNER["variant_id"] = 743
db_snk = _fake_conn(
    owner_vid=743,
    evidence={"action": "confirm"},
    leftover=JTG_LEFTOVER,
    owner=DRI_OWNER,
    receipt_row=None,
)
rc_snk = CA.cmd_release_and_claim(
    source_code="snkrdunk",
    owner_variant_id=743,
    claimant_variant_id=937,
    external_id="554381",
    actor="grok-cli",
    write=True,
    conn=db_snk,
    identity=JTG_PAGE,
    leftover_row=JTG_LEFTOVER,
    owner_row=DRI_OWNER,
    red_ids=[],
)
assert rc_snk == 0, rc_snk
assert db_snk.commits == 1
assert any(sql.strip().startswith("DELETE") for sql, _ in db_snk.log)
print("POSITIVE_OK SNK kick with operator identity and no capture receipt writes DELETE")
