#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed fixtures for `pipelines/operator_bind.py` (bind-url).

Every check re-seeds the bug it is about and proves the guard FIRES on the
broken shape (POSITIVE_OK), then proves it stays quiet on the healthy one
(NEGATIVE_OK). Nothing here reaches MySQL, a browser, a socket, or CDP: the
database is a recording fake, the PriceCharting pages are written into a temp
directory, and every outward seam in operator_bind is replaced by name -- the
network seams are replaced with functions that raise, so a check that reached
the wire would fail rather than pass slowly.
"""
from __future__ import annotations

import inspect
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import operator_bind as OB  # noqa: E402
import rebuild_036 as R  # noqa: E402

WORKSPACE = Path(tempfile.mkdtemp(prefix="cardz-bind-url-"))
NOW = datetime(2026, 8, 23, 4, 30, 0, tzinfo=timezone.utc)

CONSOLE = "one-piece-emperors-in-the-new-world"
SLUG = "shanks-2nd-anniversary-op09-001"
PC_URL = f"https://www.pricecharting.com/game/{CONSOLE}/{SLUG}"
OTHER_PC_URL = (
    "https://www.pricecharting.com/game/one-piece-japanese-awakening-of-the-new-era/"
    "monkeydluffy-prb01-op05-119"
)
PID = "10395109"
VID = 1915


# ------------------------------------------------------------------ fixtures


def pc_page(product_id: str, console: str = CONSOLE, slug: str = SLUG) -> str:
    """A PriceCharting product page carrying exactly what the parser reads."""

    return (
        "<html><head>"
        f'<link rel="canonical" href="https://www.pricecharting.com/game/{console}/{slug}">'
        "</head><body>"
        '<h1 id="product_name" class="chart_title">Shanks [2nd Anniversary] OP09-001 '
        f'<a href="/console/{console}">One Piece Emperors in the New World</a></h1>'
        f"<script>VGPC.product = {{ id: {product_id}, name: 'x' }};</script>"
        "</body></html>"
    )


def write_capture(pages_dir: Path, variant_id: int, product_id: str) -> Path:
    pages_dir.mkdir(parents=True, exist_ok=True)
    path = pages_dir / f"{variant_id}_{product_id}.html"
    path.write_text(pc_page(product_id), encoding="utf-8")
    return path


class FakeCursor:
    def __init__(self, db: "FakeDb") -> None:
        self.db = db
        self._rows: list[dict[str, Any]] = []
        self.rowcount = 0

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = ()) -> None:
        text = " ".join(sql.split())
        self.db.executed.append((text, tuple(params or ())))
        self._rows = []
        self.rowcount = 0
        if text.startswith("SET SESSION"):
            return
        if "FROM catalog_source_identity WHERE variant_id=%s" in text:
            self._rows = [
                dict(row) for row in self.db.rows.values()
                if int(row["variant_id"]) == int(params[0])
            ]
            return
        if text.startswith("SELECT source_code, external_entity_id, variant_id"):
            row = self.db.rows.get((params[0], str(params[1])))
            self._rows = [dict(row)] if row else []
            return
        if text.startswith("SELECT match_status FROM catalog_source_identity"):
            row = self.db.rows.get((params[0], str(params[1])))
            self._rows = [{"match_status": row["match_status"]}] if row else []
            return
        if text.startswith("SELECT evidence_sha256 FROM catalog_source_identity"):
            row = self.db.rows.get((params[0], str(params[1])))
            self._rows = [{"evidence_sha256": row["evidence_sha256"]}] if row else []
            return
        if text.startswith("SELECT CAST(bind_evidence_json AS CHAR)"):
            row = self.db.rows.get((params[0], str(params[1])))
            if row and str(row["evidence_sha256"]) == str(params[3]):
                self._rows = [{"bind_evidence_json": row["bind_evidence_json"]}]
            return
        if text.startswith("INSERT INTO catalog_source_identity"):
            key = (params[0], str(params[1]))
            if key in self.db.rows:
                raise RuntimeError('(1062, "Duplicate entry")')
            # The status is read out of the statement, not assumed. A fake that
            # hardcodes 'manual_review' cannot notice the day the INSERT starts
            # writing 'exact', which is the one thing this command must never do.
            literal = re.search(r"VALUES \(%s,%s,%s,'([a-z_]+)'", text)
            assert literal, text
            self.db.rows[key] = {
                "source_code": params[0], "external_entity_id": str(params[1]),
                "variant_id": int(params[2]), "match_status": literal.group(1),
                "evidence_sha256": params[3],
                "bind_evidence_json": params[5],
                "updated_at": "2026-08-23 04:30:00",
            }
            self.rowcount = 1
            return
        if text.startswith("UPDATE catalog_source_identity"):
            self.rowcount = self.db.update_rowcount
            if self.rowcount and "SET bind_evidence_json=%s" in text:
                key = (params[2], str(params[3]))
                if key in self.db.rows:
                    self.db.rows[key]["bind_evidence_json"] = params[0]
                    self.db.rows[key]["evidence_sha256"] = params[1]
            elif self.rowcount:
                key = (params[3], str(params[4]))
                if key in self.db.rows:
                    self.db.rows[key]["bind_evidence_json"] = params[2]
                    self.db.rows[key]["evidence_sha256"] = params[0]
            return
        if text.startswith("INSERT INTO catalog_provider_capture_receipt"):
            self.db.receipts.append(tuple(params))
            self.rowcount = 1
            return
        raise AssertionError(f"unrouted SQL in the fake: {text[:120]}")

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class FakeDb:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows or []:
            self.rows[(row["source_code"], str(row["external_entity_id"]))] = row
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.receipts: list[tuple[Any, ...]] = []
        self.commits = 0
        self.update_rowcount = 1

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def mutations(self) -> list[str]:
        return [
            sql for sql, _ in self.executed
            if sql.startswith(("INSERT", "UPDATE", "DELETE", "REPLACE"))
        ]


def si_row(
    source_code: str, external_entity_id: str, variant_id: int,
    match_status: str = "manual_review", evidence: Any = None,
) -> dict[str, Any]:
    return {
        "source_code": source_code,
        "external_entity_id": str(external_entity_id),
        "variant_id": int(variant_id),
        "match_status": match_status,
        "evidence_sha256": "0" * 64,
        "bind_evidence_json": (
            json.dumps(evidence, ensure_ascii=False) if evidence is not None else None
        ),
        "updated_at": "2026-08-20 00:00:00",
    }


def judge_report(
    *, held: list[dict[str, Any]] | None = None,
    promoted: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "pcIdentityReverify": True,
        "counts": {"reviewBindings": 1, "promoted": len(promoted or [])},
        "promotable": len(promoted or []),
        "promotedSample": promoted or [],
        "held": held or [],
    }


def empty_judge(*a: Any, **k: Any) -> dict[str, Any]:
    return judge_report()


def refuse_to_fetch(*a: Any, **k: Any) -> Any:
    raise AssertionError("a check reached the network")


def run(**kw: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "variant_id": VID, "url": PC_URL, "actor": "daddy",
        "allow_fetch": False, "receipts_dir": WORKSPACE / "receipts", "now": NOW,
    }
    params.update(kw)
    return OB.apply_one(**params)


def steps_of(report: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(entry["step"]): entry for entry in report["steps"]}


CHECKS = 0


def ok(label: str, positive: bool = True) -> None:
    global CHECKS
    CHECKS += 1
    print(f"{'POSITIVE_OK' if positive else 'NEGATIVE_OK'} {label}")


try:
    PAGES = WORKSPACE / "full900"
    capture = write_capture(PAGES, VID, PID)
    # The fixture has to be a page this repo's own parser accepts, or every
    # check below would be testing the fixture instead of the contract.
    identity, why = R._pc_page_identity(capture.read_text(encoding="utf-8"))
    assert identity is not None, why
    assert R._pc_page_product_id(capture.read_text(encoding="utf-8")) == PID

    # Kept before the seams are replaced, so the SNK scoping check below can
    # exercise the real implementation rather than this file's stub.
    REAL_RUN_JUDGE = OB.run_judge

    OB.red_listed_variants = lambda: set()  # type: ignore[assignment]
    OB.run_judge = empty_judge  # type: ignore[assignment]
    OB.fetch_pricecharting = refuse_to_fetch  # type: ignore[assignment]
    OB.fetch_snkrdunk = refuse_to_fetch  # type: ignore[assignment]

    # ------------------------------------------------------- 0. URL table
    parsed = OB.parse_url(PC_URL)
    assert parsed.source_code == "pricecharting"
    # The product id is NOT in a PriceCharting URL. A table that invented one
    # here is a table that binds a slug's trailing digits as a product id.
    assert parsed.external_entity_id == ""
    assert parsed.canonical_path == f"/game/{CONSOLE}/{SLUG}"
    for url, expected in (
        ("https://snkrdunk.com/en/trading-cards/471534", "471534"),
        ("https://snkrdunk.com/trading-cards/471534", "471534"),
        ("https://www.snkrdunk.com/trading-cards/471534/anything", "471534"),
    ):
        snk = OB.parse_url(url)
        assert snk.source_code == "snkrdunk" and snk.external_entity_id == expected
    ok("a PriceCharting paste yields no external id until the page speaks", False)

    for bad, verdict in (
        ("https://gemrate.com/card/" + "a" * 40 + "/luffy",
         "gemrate_is_the_identity_authority"),
        ("https://tcgplayer.com/product/12345", "url_not_in_vocabulary"),
        ("https://www.pricecharting.com/console/one-piece", "url_not_in_vocabulary"),
        ("", "url_missing"),
    ):
        try:
            OB.parse_url(bad)
            raise AssertionError(f"the URL table accepted {bad!r}")
        except OB.BindStop as stop:
            assert stop.verdict == verdict, (bad, stop.verdict)
            if verdict == "url_not_in_vocabulary":
                assert "pricecharting.com/game/" in stop.detail
                assert "snkrdunk.com/trading-cards/" in stop.detail
            if verdict == "gemrate_is_the_identity_authority":
                assert "gemrate_identity_intake" in stop.detail
    ok("the URL table refuses gemrate and anything it does not know, listing what it knows")

    # ------------------------------------------------ 1. the red list wins
    OB.red_listed_variants = lambda: {VID}  # type: ignore[assignment]
    db = FakeDb([si_row("pricecharting", PID, VID)])
    report = run(conn=db, write=True, pages_dir=PAGES,
                 operator_ruling_slug="operator-daddy-20260823", note="I am sure")
    assert report["verdict"] == "red_listed", report["verdict"]
    assert report["exitCode"] == OB.EXIT_REFUSED
    assert steps_of(report)[1]["status"] == OB.STATUS_REFUSED
    assert db.mutations() == [], db.mutations()
    assert "red-sheet-036-release.json" in steps_of(report)[1]["detail"]
    ok("a red-listed card is refused even with --write AND --operator-ruling")

    OB.red_listed_variants = lambda: {VID + 1}  # type: ignore[assignment]
    report = run(conn=FakeDb([si_row("pricecharting", PID, VID)]), pages_dir=PAGES)
    assert steps_of(report)[1]["status"] == OB.STATUS_OK, report
    ok("a card that is not on the sheet walks past step 1", False)

    OB.red_listed_variants = lambda: set()  # type: ignore[assignment]

    # ------------------------------------- 2. a standing ruling holds the row
    RULED = {"reason": "operator-zero-20260814: live same-number already on board"}
    db = FakeDb([
        si_row("pricecharting", PID, VID),
        si_row("snkrdunk", "198702", VID, "exact", RULED),
    ])
    report = run(conn=db, write=True, pages_dir=PAGES)
    assert report["verdict"] == "standing_operator_ruling", report["verdict"]
    assert "operator-zero-20260814" in steps_of(report)[2]["detail"]
    # The ruling lives on the SNKRDUNK row and the paste is a PriceCharting
    # one: a ruling is about the CARD, and reading only the row being written
    # is how v1203 came one --write away from exact on 2026-08-22.
    assert "snkrdunk/198702" in steps_of(report)[2]["detail"]
    assert db.mutations() == []
    ok("a ruling on a SIBLING row still holds a PriceCharting paste")

    # Re-seed the bug the prefix exists to catch: the same sentence without
    # `operator-` in front is NOT a ruling and must hold nothing.
    db = FakeDb([
        si_row("pricecharting", PID, VID),
        si_row("snkrdunk", "198702", VID, "exact",
               {"reason": "zero-20260814: live same-number already on board"}),
    ])
    report = run(conn=db, pages_dir=PAGES)
    assert steps_of(report)[2]["status"] == OB.STATUS_OK, report
    assert steps_of(report)[2]["standingRuling"] == ""
    ok("a reason without the operator- prefix protects nothing", False)

    # ---------------------------------------- 3. no page, no verdict, no fetch
    db = FakeDb([si_row("pricecharting", PID, VID)])
    report = run(conn=db, url=OTHER_PC_URL, pages_dir=PAGES)
    assert report["verdict"] == "fresh_page_required", report["verdict"]
    assert steps_of(report)[3]["status"] == OB.STATUS_BLOCKED
    assert str(OB.CDP_PORT) in steps_of(report)[3]["detail"]
    assert db.mutations() == []
    ok("a paste with no matching capture is reported BLOCKED, never fetched behind the operator's back")

    db = FakeDb([si_row("snkrdunk", "471534", VID)])
    report = run(conn=db, url="https://snkrdunk.com/trading-cards/471534",
                 pages_dir=PAGES)
    assert report["verdict"] == "fresh_master_required", report["verdict"]
    assert steps_of(report)[3]["status"] == OB.STATUS_BLOCKED
    ok("a snkrdunk paste with no live harvest is blocked, not guessed")

    db = FakeDb([si_row("pricecharting", PID, VID)])
    report = run(conn=db, pages_dir=PAGES)
    assert steps_of(report)[3]["status"] == OB.STATUS_OK, report
    assert steps_of(report)[3]["productId"] == PID
    ok("the capture whose own canonical URL IS the pasted page is reused", False)

    # ------------------------------------- 4. the page must be that product
    # Re-seeded through the fetch seam, because that is the only path on which
    # an unproved page can reach step 4 at all.
    def fresh(name: str) -> Path:
        # A fresh EMPTY directory each time: a leftover capture from the last
        # check would be reused at step 3 and the fetch seam would never run,
        # which is exactly how a fetch-path check silently stops testing the
        # fetch path.
        path = WORKSPACE / "fetched" / name
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def fetch_returning(html: str, product_id: str):
        def _fetch(url: str, pages_dir: Path, variant_id: int, **kw: Any):
            target = Path(pages_dir) / f"{variant_id}_{product_id}.html"
            target.write_text(html, encoding="utf-8")
            return target, html, product_id
        return _fetch

    OB.fetch_pricecharting = fetch_returning(  # type: ignore[assignment]
        "<html><body>nothing the parser can read</body></html>", PID,
    )
    db = FakeDb([si_row("pricecharting", PID, VID)])
    report = run(conn=db, write=True, pages_dir=fresh("unparseable"), allow_fetch=True)
    assert report["verdict"] == "page_parse_failed", report["verdict"]
    assert steps_of(report)[4]["status"] == OB.STATUS_REFUSED
    assert db.mutations() == []
    ok("a fetched page the parser cannot read is refused at step 4 with zero writes")

    # A redirect capture: the fetch says one id, the page body says another.
    OB.fetch_pricecharting = fetch_returning(  # type: ignore[assignment]
        pc_page("999999"), PID,
    )
    db = FakeDb([si_row("pricecharting", PID, VID)])
    report = run(conn=db, write=True, pages_dir=fresh("redirect"), allow_fetch=True)
    assert report["verdict"] == "page_product_mismatch", report["verdict"]
    assert "999999" in steps_of(report)[4]["detail"]
    assert db.mutations() == []
    ok("a page whose own product id is not the bound id writes nothing")

    # A capture the judge's own finder cannot reach would become a proposal
    # held `page_missing` the moment it is written.
    OB.fetch_pricecharting = (  # type: ignore[assignment]
        lambda url, pages_dir, variant_id, **kw: (
            WORKSPACE / "nowhere" / f"{variant_id}_{PID}.html", pc_page(PID), PID
        )
    )
    db = FakeDb([si_row("pricecharting", PID, VID)])
    report = run(conn=db, write=True, pages_dir=fresh("invisible"), allow_fetch=True)
    assert report["verdict"] == "capture_not_visible_to_judge", report["verdict"]
    assert db.mutations() == []
    ok("a capture pc_capture_for_product cannot reach is refused before it becomes a doomed proposal")

    OB.fetch_pricecharting = fetch_returning(pc_page(PID), PID)  # type: ignore[assignment]
    db = FakeDb([si_row("pricecharting", PID, VID)])
    report = run(conn=db, write=True, pages_dir=fresh("good"), allow_fetch=True)
    assert steps_of(report)[4]["status"] == OB.STATUS_OK, report
    ok("a fetched page that proves its own id passes step 4", False)

    OB.fetch_pricecharting = refuse_to_fetch  # type: ignore[assignment]

    # ------------------------- one product, one variant (the PK, checked early)
    db = FakeDb([si_row("pricecharting", PID, 4242, "exact")])
    report = run(conn=db, write=True, pages_dir=PAGES)
    assert report["verdict"] == "external_id_owned_by_another_variant", report
    assert "4242" in steps_of(report)[5]["detail"]
    assert db.mutations() == []
    ok("a product id already owned by another variant is refused: one product cannot be two cards")

    # ---------------------------------- 5. the proposal is never `exact`
    held_judge = lambda *a, **k: judge_report(  # noqa: E731
        held=[{"variant_id": VID, "pid": PID,
               "reason": "print_signature_mismatch",
               "detail": "page=[2nd Anniversary] printing="}]
    )
    OB.run_judge = held_judge  # type: ignore[assignment]
    db = FakeDb([si_row("pricecharting", PID, VID)])
    report = run(conn=db, write=True, pages_dir=PAGES)
    joined = " ".join(db.mutations())
    assert any(sql.startswith("UPDATE catalog_source_identity")
               for sql in db.mutations()), db.mutations()
    assert "match_status='exact'" not in joined and "'exact'" not in joined
    assert "manual_review" in joined
    assert db.receipts and db.receipts[0][0] == "pricecharting"
    assert report["verdict"] == "held_by_judge", report["verdict"]
    assert report["gate"] == "print_signature_mismatch"
    assert steps_of(report)[7]["detail"].startswith("print_signature_mismatch")
    assert db.rows[("pricecharting", PID)]["match_status"] == "manual_review"
    ok("a held paste writes a manual_review proposal, never exact, and reports the gate by name")

    # The same rule on the OTHER write path: a variant with no row for this
    # product id gets an INSERT, and that INSERT is a proposal too.
    db = FakeDb([])
    report = run(conn=db, write=True, pages_dir=PAGES)
    inserts = [sql for sql in db.mutations()
               if sql.startswith("INSERT INTO catalog_source_identity")]
    assert len(inserts) == 1, db.mutations()
    assert "'manual_review'" in inserts[0] and "'exact'" not in inserts[0]
    assert db.rows[("pricecharting", PID)]["match_status"] == "manual_review"
    assert report["verdict"] == "held_by_judge", report["verdict"]
    ok("a brand-new binding is INSERTed at manual_review, never at exact")

    db = FakeDb([si_row("pricecharting", PID, VID)])
    report = run(conn=db, pages_dir=PAGES)
    assert db.mutations() == [], db.mutations()
    assert db.commits == 0
    assert steps_of(report)[5]["status"] == OB.STATUS_SKIPPED
    assert steps_of(report)[5]["wouldWrite"] is True
    ok("dry run touches nothing: zero INSERT, zero UPDATE, zero commit", False)

    # A rejection somebody reasoned about does not reopen on a paste alone.
    db = FakeDb([si_row("pricecharting", PID, VID, "rejected",
                        {"action": "reject-wrong-printing-source"})])
    report = run(conn=db, write=True, pages_dir=PAGES)
    assert report["verdict"] == "reasoned_rejection", report["verdict"]
    assert db.mutations() == []
    ok("a reasoned rejection needs an operator ruling, not a paste")

    db = FakeDb([si_row("pricecharting", PID, VID, "rejected",
                        {"action": "pc-identity-discover"})])
    report = run(conn=db, write=True, pages_dir=PAGES)
    assert report["verdict"] == "held_by_judge", report["verdict"]
    ok("collateral quarantine (nobody reasoned about it) is reconsidered as usual", False)

    db = FakeDb([si_row("pricecharting", PID, VID, "exact")])
    report = run(conn=db, write=True, pages_dir=PAGES)
    assert report["verdict"] == "already_exact", report["verdict"]
    assert db.mutations() == []
    ok("an already-exact row is left alone instead of being re-proposed")

    # ------------------------------------------- 8. the operator ruling
    for slug in ("zero-20260814", "daddy"):
        db = FakeDb([si_row("pricecharting", PID, VID)])
        report = run(conn=db, write=True, pages_dir=PAGES,
                     operator_ruling_slug=slug, note="because")
        assert report["verdict"] == "operator_ruling_prefix_required", report
        assert report["exitCode"] == OB.EXIT_USAGE != 0
        assert db.mutations() == []
    db = FakeDb([si_row("pricecharting", PID, VID)])
    report = run(conn=db, write=True, pages_dir=PAGES,
                 operator_ruling_slug="operator-daddy-20260823", note="  ")
    assert report["verdict"] == "operator_ruling_needs_note", report
    assert report["exitCode"] == OB.EXIT_USAGE
    assert db.mutations() == []
    ok("--operator-ruling refuses a slug without the operator- prefix and refuses a ruling with no reason")

    db = FakeDb([si_row("pricecharting", PID, VID)])
    report = run(conn=db, write=True, pages_dir=PAGES,
                 operator_ruling_slug="operator-daddy-20260823",
                 note="page proves OP09-001; bracket is our own product name")
    assert steps_of(report)[8]["status"] == OB.STATUS_OK, report
    stamped = json.loads(db.rows[("pricecharting", PID)]["bind_evidence_json"])
    reason = stamped["reason"]
    assert reason.startswith("operator-daddy-20260823: "), reason
    # The whole point: the lanes must read it back through the repo's own
    # decider, on this row and on any sibling row of the same variant.
    assert R.operator_ruling(json.dumps(stamped)) == reason
    sibling_rows = [
        si_row("snkrdunk", "198702", VID),
        dict(db.rows[("pricecharting", PID)]),
    ]
    seen, where = OB.standing_ruling(sibling_rows, "snkrdunk", "198702")
    assert seen == reason and where == f"pricecharting/{PID}", (seen, where)
    assert stamped["operatorRuling"]["actor"] == "daddy"
    assert "print_signature_mismatch" in stamped["operatorRuling"]["overrides"]
    ok("the ruling bind-url writes is read back by rebuild_036.operator_ruling on the row AND on a sibling")

    db = FakeDb([si_row("pricecharting", PID, VID)])
    report = run(conn=db, pages_dir=PAGES,
                 operator_ruling_slug="operator-daddy-20260823", note="dry")
    assert steps_of(report)[8]["status"] == OB.STATUS_SKIPPED
    assert db.mutations() == []
    ok("a dry run with --operator-ruling stamps nothing", False)

    # ----------------------------------- 10. rowcount == 0 is never success
    db = FakeDb([si_row("pricecharting", PID, VID)])
    db.update_rowcount = 0
    report = run(conn=db, write=True, pages_dir=PAGES)
    assert report["verdict"] == "chain_changed_row", report["verdict"]
    assert report["exitCode"] == OB.EXIT_CHAIN_CHANGED == 2
    assert "the chain just changed this row" in steps_of(report)[5]["detail"]
    assert db.commits == 0
    ok("a proposal UPDATE that changes zero rows exits 2 and says the chain moved the row")

    # The row appeared between the read and the INSERT: duplicate key is the
    # same event wearing the other shape.
    class RaceDb(FakeDb):
        def cursor(self) -> FakeCursor:
            cursor = FakeCursor(self)
            outer = self

            def execute(sql: str, params: Any = ()) -> None:
                text = " ".join(sql.split())
                if text.startswith("INSERT INTO catalog_source_identity"):
                    outer.executed.append((text, tuple(params or ())))
                    raise RuntimeError('(1062, "Duplicate entry")')
                FakeCursor.execute(cursor, sql, params)

            cursor.execute = execute  # type: ignore[method-assign]
            return cursor

    db = RaceDb([])
    report = run(conn=db, write=True, pages_dir=PAGES)
    assert report["verdict"] == "chain_changed_row", report["verdict"]
    assert report["exitCode"] == 2
    assert db.commits == 0
    ok("a proposal INSERT that loses a race exits 2 instead of reporting a bind")

    db = FakeDb([si_row("pricecharting", PID, VID)])
    OB.run_judge = lambda *a, **k: judge_report(  # type: ignore[assignment]
        promoted=[{"variant_id": VID, "pid": PID}]
    )
    report = run(conn=db, write=True, pages_dir=PAGES)
    assert report["verdict"] == "chain_changed_row", report["verdict"]
    assert report["exitCode"] == 2
    assert steps_of(report)[10]["status"] == OB.STATUS_CHAIN_CHANGED
    assert steps_of(report)[9]["status"] == OB.STATUS_SKIPPED
    ok("a promotion the judge claims but the table does not show is a chain change, never a success")

    db = FakeDb([si_row("pricecharting", PID, VID)])

    def promote_and_land(*a: Any, **k: Any) -> dict[str, Any]:
        db.rows[("pricecharting", PID)]["match_status"] = "exact"
        return judge_report(promoted=[{"variant_id": VID, "pid": PID}])

    OB.run_judge = promote_and_land  # type: ignore[assignment]
    froze: list[tuple[Any, ...]] = []
    OB._freeze = lambda *a: froze.append(a)  # type: ignore[assignment]
    report = run(conn=db, write=True, pages_dir=PAGES, freeze=True)
    assert report["verdict"] == "bound_exact", report["verdict"]
    assert report["exitCode"] == 0
    assert steps_of(report)[10]["status"] == OB.STATUS_OK
    assert froze == [(VID, "pricecharting", "daddy", None)], froze
    ok("a promotion visible in the table reports bound_exact and only then freezes", False)

    db = FakeDb([si_row("pricecharting", PID, VID)])
    OB.run_judge = held_judge  # type: ignore[assignment]
    froze.clear()
    report = run(conn=db, write=True, pages_dir=PAGES, freeze=True)
    assert froze == [], froze
    assert steps_of(report)[9]["status"] == OB.STATUS_SKIPPED
    assert "--freeze needs an exact row" in steps_of(report)[9]["detail"]
    ok("--freeze on a held row freezes nothing")

    # -------------------------- the SNK lane may not be judged unscoped yet
    # `cmd_snk_identity_reverify` selects EVERY held snkrdunk row and harvests
    # all of them. Until it reads variant_ids, judging one paste would spend a
    # full provider sweep, so the real run_judge has to refuse rather than
    # start one.
    saved_supports = OB.snk_judge_supports_scoping
    OB.snk_judge_supports_scoping = lambda: False  # type: ignore[assignment]
    try:
        REAL_RUN_JUDGE("snkrdunk", 1203, write=False, pages_dir=None,
                       map_path=None, credentials_env=None)
        raise AssertionError("bind-url judged snkrdunk with an unscoped lane")
    except OB.BindStop as stop:
        assert stop.verdict == "snk_judge_not_scoped", stop.verdict
        assert stop.status == OB.STATUS_BLOCKED
        assert stop.step == 6
    finally:
        OB.snk_judge_supports_scoping = saved_supports  # type: ignore[assignment]
    ok("bind-url refuses to judge a snkrdunk paste while the SNK lane still ignores variant_ids")

    # The guard reads the lane's own source, so it answers about the tree it is
    # running in rather than about a flag somebody set elsewhere.
    snk_source = inspect.getsource(R.cmd_snk_identity_reverify)
    assert OB.snk_judge_supports_scoping() == ("variant_ids" in snk_source)
    assert "variant_ids" in inspect.getsource(R.cmd_pc_identity_reverify)
    ok("the scoping guard answers from the SNK lane's own source, and the PC lane already reads variant_ids", False)

    # ------------------------------------------------------- the receipt
    OB.run_judge = empty_judge  # type: ignore[assignment]
    db = FakeDb([si_row("pricecharting", PID, VID)])
    report = run(conn=db, pages_dir=PAGES)
    receipt = Path(report["receiptPath"])
    assert receipt.is_file()
    saved = json.loads(receipt.read_text(encoding="utf-8"))
    assert saved["variantId"] == VID and saved["url"] == PC_URL
    numbers = [entry["step"] for entry in saved["steps"]]
    assert numbers == sorted(numbers)
    assert set(numbers) >= set(range(1, 11))
    ok("every run leaves a receipt carrying all ten steps in order", False)

    print(f"CHECKS={CHECKS}")
finally:
    shutil.rmtree(WORKSPACE, ignore_errors=True)
