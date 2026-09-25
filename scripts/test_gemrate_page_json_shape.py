#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The card page's /card-details JSON is captured in both its old and its 09-08 shape.

2026-09-08: GemRate renamed ``date`` to ``data_last_updated`` and the flat
``g10``/``g10p`` grades to ``psa_10``/``beckett_10_pristine``/... The capture
rejected every JSON as ``page_initiated_json_effective_date_missing`` and fell
back to the DOM table, whose receipts carry no raw file; the completeness gate
then refused 1858 staged cards a day and new-identity intake stalled from
09-06 to 09-25. The fixture keys are the ones gid 02187b47... served on 09-25.

Run: python -X utf8 scripts/test_gemrate_page_json_shape.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import gemrate_db_completeness as completeness  # noqa: E402
import gemrate_source as gs  # noqa: E402

FAILED: list[str] = []
CHECKS = 0
GID = "02187b47d67ce54a7ef1261f44afd6bf43989e2d"
PAGE = f"{gs.WEB}/card/{GID}/2025-paldean-wooper-pokemon-pfl-en-phantasmal-flames-illustration-rare-102"


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


def row(grader: str, grades: dict[str, int]) -> dict:
    return {
        "grader": grader, "gemrate_id": GID, "year": "2025", "card_number": "102",
        "set_name": "Pokemon Pfl EN-Phantasmal Flames", "parallel": "Illustration Rare",
        "name": "Paldean Wooper", "card_total_grades": 6419,
        "description": "2025 Pokemon Pfl EN-Phantasmal Flames Paldean Wooper Illustration Rare",
        "last_population_change": "2026-09-23", "grades": grades,
    }


def payload_0925() -> dict:
    return {
        "gemrate_id": GID, "universal_gemrate_id": GID, "year": "2025", "card_number": "102",
        "set_name": "Pokemon Pfl EN-Phantasmal Flames", "parallel": "Illustration Rare",
        "name": "Paldean Wooper", "category": "tcg-cards", "population_type": "Universal",
        "data_last_updated": "2026-09-24", "last_population_change": "2026-09-23",
        "population_data": [
            row("psa", {"psa_9": 2338, "psa_10": 3364, "psa_auth": 0}),
            row("beckett", {"beckett_9_5": 45, "beckett_10_black": 1, "beckett_10_pristine": 9}),
            row("sgc", {"sgc_10": 1, "sgc_10_pristine": 0}),
            row("cgc", {"cgc_10": 469, "cgc_10_pristine": 220, "cgc_10_perfect": 0}),
        ],
    }


def payload_0906() -> dict:
    return {
        "gemrate_id": GID, "year": "2025", "card_number": "102",
        "set_name": "Pokemon Pfl EN-Phantasmal Flames", "parallel": "Illustration Rare",
        "name": "Paldean Wooper", "date": "2026-09-06", "last_population_change": "2026-09-05",
        "population_data": [
            row("psa", {"g9": 2568, "g10": 3238, "auth": 0}),
            row("beckett", {"g10b": 1, "g10p": 8}),
            row("sgc", {"g10": 1, "g10p": 0}),
            row("cgc", {"g10": 449, "auth": 7}),
        ],
    }


def build(payload: dict):
    return gs.build_public_card_page_json_payload(
        GID,
        response_url=f"{gs.WEB}/card-details?gemrate_id={GID}",
        response_status=200,
        payload=payload,
        canonical_url=PAGE,
        title="Paldean Wooper",
        dom_sha256="a" * 64,
        route_verified=True,
    )


def capture_then_gate(label: str, payload: dict, want: dict) -> None:
    built, reason = build(payload)
    check(f"{label}: the page JSON is accepted", reason, None)
    if built is None:
        return
    check(f"{label}: snapshot day", built["date"], want["date"])
    check(f"{label}: transport", built["publicCardPage"]["populationMode"], "page_initiated_json")
    point = gs._website_population(built, "2026-09-25T04:40:00+00:00")
    # BGS is left out: the persist step has dropped the Beckett row on this
    # transport since before the rename (09-06 card_details has no beckett).
    got = {k: v for k, v in ((point or {}).get("graderPopulations") or {}).items() if k != "BGS"}
    check(f"{label}: per-grader top grade", got, want["graders"])
    with tempfile.TemporaryDirectory() as tmp:
        cards = Path(tmp)
        stored = gs._persist_public_card_capture(cards, GID, built)
        receipt = stored["privateSourceReceipt"]
        check(f"{label}: raw file captured, not DOM-only", receipt["rawStatus"], "captured")
        raw = json.loads((cards / GID / receipt["sourcePointer"]).read_text(encoding="utf-8"))
        errors: list[str] = []
        psa = completeness._raw_psa_population_row(raw, GID, errors, "receipt_raw")
        check(f"{label}: completeness reads the raw PSA 10", (psa or {}).get("populationPsa10"), want["graders"]["PSA"])
        check(f"{label}: completeness raises nothing", errors, [])
        check(f"{label}: staged card agrees with raw", completeness._psa10_from_card_details(stored), want["graders"]["PSA"])


def main() -> int:
    capture_then_gate(
        "09-25 shape", payload_0925(),
        {"date": "2026-09-24", "graders": {"PSA": 3364, "SGC": 1, "CGC": 469}},
    )
    capture_then_gate(
        "09-06 shape", payload_0906(),
        {"date": "2026-09-06", "graders": {"PSA": 3238, "SGC": 1, "CGC": 449}},
    )

    no_day = payload_0925()
    del no_day["data_last_updated"]
    check("a JSON with no snapshot day at all is still refused", build(no_day), (None, "page_initiated_json_effective_date_missing"))
    no_ten = payload_0925()
    no_ten["population_data"][0]["grades"] = {"psa_9": 2338}
    check("a PSA row without a 10 is still refused", build(no_ten), (None, "page_initiated_json_psa_g10_missing"))
    cgc = gs._top_grade_value("cgc", {"cgc_10_pristine": 220, "cgc_10_perfect": 0})
    check("CGC pristine/perfect never stand in for the CGC 10", cgc, None)

    for line in FAILED:
        print(line)
    print(f"{CHECKS - len(FAILED)}/{CHECKS} checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
