from __future__ import annotations

import json
import hashlib
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from gemrate_candidate_backfill import (  # noqa: E402
    apply_public_receipt_identity_proposals,
    build_public_card_details_worklist,
    build_candidate_roster,
    merge_receipt_mappings,
    persist_receipt_identity_mappings,
    resolve_immutable_mirror_root,
    run_candidate_backfill,
    run_offline_backfill,
)
import gemrate_candidate_backfill as candidate_backfill  # noqa: E402
from gemrate_source import (  # noqa: E402
    PRIVATE_PAGE_JSON_FIELD,
    _persist_public_card_capture,
    build_public_card_page_payload,
)


def direct_payload(gemrate_id: str, population: int, effective_date: str = "2026-07-23") -> dict:
    return {
        "data": {
            "gemrate_id": gemrate_id,
            "population": {
                "population_data": {
                    "data_last_updated": effective_date,
                    "by_grader": {"psa": {"grades": {"psa_10": population}}},
                }
            },
        }
    }


def mirror_payload(population: int, effective_date: str = "2026-07-24") -> dict:
    return {"effectiveDate": effective_date, "population": [{"gradeName": "PSA", "topGrade": population}]}


def public_payload(gemrate_id: str, population: int, effective_date: str = "2026-07-23") -> dict:
    return {
        "gemrate_id": gemrate_id,
        "population_data": [
            {
                "grader": "psa",
                "last_population_change": effective_date,
                "grades": {"g10": population},
            }
        ],
    }


def public_payload_multi_grader(
    gemrate_id: str,
    populations: dict[str, int],
    effective_date: str = "2026-07-23",
) -> dict:
    grader_key = {"PSA": "psa", "CGC": "cgc", "BGS": "beckett", "SGC": "sgc"}
    rows = []
    for code in ("PSA", "CGC", "BGS", "SGC"):
        if code not in populations:
            continue
        if code == "BGS":
            grades = {"g10p": populations[code], "g10b": 0, "g9_5": 0}
        else:
            grades = {"g10": populations[code]}
        row = {"grader": grader_key[code], "grades": grades}
        if code == "PSA":
            row["last_population_change"] = effective_date
        rows.append(row)
    return {"gemrate_id": gemrate_id, "population_data": rows}


def public_receipt(
    gemrate_id: str,
    *,
    population: int = 1200,
    set_name: str = "One Piece Japanese OP01-Romance Dawn",
    card_number: str = "OP01-120",
    parallel: str = "Manga Alternate Art",
) -> dict:
    payload = public_payload(gemrate_id, population)
    payload["publicCardPage"] = {
        "canonicalUrl": f"https://www.gemrate.com/card/{gemrate_id}",
        "title": "GemRate fixture",
        "routeVerified": True,
        "populationMode": "page_initiated_json",
        "identity": {
            "year": "2022",
            "set_name": set_name,
            "card_number": card_number,
            "parallel": parallel,
        },
    }
    return payload


def persist_verified_public_capture(
    cards_root: Path,
    gemrate_id: str,
    payload: dict,
    *,
    fetched_at: str | None = None,
) -> None:
    captured = dict(payload)
    page = dict(captured.get("publicCardPage") or {})
    page.update({
        "canonicalUrl": f"https://www.gemrate.com/card/{gemrate_id}",
        "title": str(page.get("title") or "GemRate fixture"),
        "routeVerified": True,
        "populationMode": str(page.get("populationMode") or "page_initiated_json"),
    })
    captured["publicCardPage"] = page
    captured[PRIVATE_PAGE_JSON_FIELD] = {"gemrate_id": gemrate_id, "fixture": True}
    normalized = _persist_public_card_capture(cards_root, gemrate_id, captured)
    if fetched_at is not None:
        card_dir = cards_root / gemrate_id
        receipt = normalized["privateSourceReceipt"]
        receipt["fetchedAt"] = fetched_at
        normalized["privateSourceReceipt"] = receipt
        (card_dir / "card_details.raw.receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (card_dir / "card_details.json").write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def persist_verified_direct_population(
    cards_root: Path,
    gemrate_id: str,
    payload: dict,
    *,
    fetched_at: str = "2026-07-23T00:00:00Z",
) -> None:
    card = cards_root / gemrate_id
    card.mkdir(parents=True, exist_ok=True)
    card.joinpath("population.json").write_text(json.dumps(payload), encoding="utf-8")
    card.joinpath("identity.receipt.json").write_text(json.dumps({
        "requestedGemrateId": gemrate_id,
        "entityGemrateId": gemrate_id,
        "payloadSha256": hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "sourcePointer": "population.json",
        "fetchedAt": fetched_at,
    }), encoding="utf-8")


def direct_identity_payload(gemrate_id: str, *, universal_id: str | None = None) -> dict:
    return {
        "data": {
            "gemrate_id": gemrate_id,
            "parsed_description": {"cardNumber": "OP01-120"},
            "population": {"graders": {"psa": {
                "gemrate_id": gemrate_id, "spec_id": "123",
                "parsed_description": {"cardNumber": "OP01-120"},
            }}},
        }
    }


def persist_direct_identity_receipt(cards_root: Path, gemrate_id: str, payload: dict, *, universal_id: str | None = None) -> None:
    card = cards_root / gemrate_id
    card.mkdir(parents=True, exist_ok=True)
    card.joinpath("population.json").write_text(json.dumps(payload), encoding="utf-8")
    card.joinpath("identity.receipt.json").write_text(json.dumps({
        "requestedGemrateId": gemrate_id, "entityGemrateId": gemrate_id,
        "universalGemrateId": universal_id, "isUniversalMatch": universal_id is not None,
        "parsedDescription": payload["data"]["parsed_description"],
        "graders": {"psa": {"gemrateId": gemrate_id, "specId": "123", "parsedDescription": {"cardNumber": "OP01-120"}}},
        "payloadSha256": hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        "sourcePointer": "population.json", "fetchedAt": "2026-07-24T00:00:00Z",
    }), encoding="utf-8")


class GemRateCandidateBackfillTests(unittest.TestCase):
    def test_public_capture_normalizes_all_grader_rows(self) -> None:
        gemrate_id = "a" * 40
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            persist_verified_public_capture(
                root / "public",
                gemrate_id,
                public_payload_multi_grader(gemrate_id, {"PSA": 1200, "CGC": 300, "BGS": 45, "SGC": 12}),
                fetched_at="2026-07-24T00:00:00Z",
            )
            normalized = json.loads((root / "public" / gemrate_id / "card_details.json").read_text(encoding="utf-8"))
            manifest = run_offline_backfill(
                [{"gemrateId": gemrate_id, "tcg": "pokemon", "identity": None}],
                direct_root=root / "direct",
                public_root=root / "public",
                mirror_root=root / "mirror",
                as_of=date(2026, 7, 24),
            )

        graders = {row["grader"]: row["grades"]["g10"] for row in normalized["population_data"]}
        self.assertEqual(graders, {"psa": 1200, "cgc": 300, "beckett": 45, "sgc": 12})
        row = manifest["candidates"][0]
        self.assertEqual(row["status"], "resolved")
        self.assertEqual(row["populationPsa10"], 1200)

    def test_beckett_top_grade_uses_pristine_not_black_label(self) -> None:
        gemrate_id = "b" * 40
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            persist_verified_public_capture(
                root / "public",
                gemrate_id,
                {
                    "gemrate_id": gemrate_id,
                    "population_data": [
                        {"grader": "psa", "grades": {"g10": 500}, "last_population_change": "2026-07-23"},
                        {"grader": "beckett", "grades": {"g10p": 7, "g10b": 99, "g9_5": 40}},
                    ],
                },
                fetched_at="2026-07-24T00:00:00Z",
            )
            normalized = json.loads((root / "public" / gemrate_id / "card_details.json").read_text(encoding="utf-8"))

        graders = {row["grader"]: row["grades"]["g10"] for row in normalized["population_data"]}
        self.assertEqual(graders, {"psa": 500, "beckett": 7})

    def test_beckett_row_without_pristine_contributes_no_bgs_point(self) -> None:
        gemrate_id = "d" * 40
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            persist_verified_public_capture(
                root / "public",
                gemrate_id,
                {
                    "gemrate_id": gemrate_id,
                    "population_data": [
                        {"grader": "psa", "grades": {"g10": 500}, "last_population_change": "2026-07-23"},
                        {"grader": "beckett", "pop_results": True},
                    ],
                },
                fetched_at="2026-07-24T00:00:00Z",
            )
            normalized = json.loads((root / "public" / gemrate_id / "card_details.json").read_text(encoding="utf-8"))

        graders = {row["grader"]: row["grades"]["g10"] for row in normalized["population_data"]}
        self.assertEqual(graders, {"psa": 500})

    def test_direct_receipt_exact_confirmation_is_preferred(self) -> None:
        gemrate_id = "c" * 40
        candidate = {
            "gemrateId": gemrate_id, "tcg": "one-piece", "setEvidence": "Romance Dawn",
            "collectorNumberEvidence": "OP01-120", "languageEvidence": "Japanese",
            "editionEvidence": "standard", "parallelEvidence": "Manga", "finishEvidence": "foil",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            persist_direct_identity_receipt(root / "direct", gemrate_id, direct_identity_payload(gemrate_id))
            candidates, reviews = apply_public_receipt_identity_proposals(
                [candidate], public_root=root / "public", direct_root=root / "direct",
            )
        self.assertEqual(reviews, [])
        self.assertEqual(candidates[0]["identityStatus"], "exact_confirmed")
        self.assertEqual(candidates[0]["canonicalSourceCode"], "gemrate_direct")

    def test_direct_public_identity_conflict_is_review_not_rebind(self) -> None:
        gemrate_id = "d" * 40
        candidate = {
            "gemrateId": gemrate_id, "tcg": "one-piece", "setEvidence": "Romance Dawn",
            "collectorNumberEvidence": "OP01-120", "languageEvidence": "Japanese",
            "editionEvidence": "standard", "parallelEvidence": "Manga", "finishEvidence": "foil",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            persist_direct_identity_receipt(root / "direct", gemrate_id, direct_identity_payload(gemrate_id))
            public = public_receipt(gemrate_id, set_name="Romance Dawn", card_number="OP01-121", parallel="Manga")
            persist_verified_public_capture(root / "public", gemrate_id, public)
            candidates, reviews = apply_public_receipt_identity_proposals(
                [candidate], public_root=root / "public", direct_root=root / "direct",
            )
        self.assertEqual(candidates[0]["identityStatus"], "review")
        self.assertIn("gemrate_direct_public_identity_conflict", reviews[0]["reasons"])

    def test_exact_receipt_mapping_replaces_unsettled_base_review_for_same_gemrate_id(self) -> None:
        gemrate_id = "e" * 40
        candidate = {
            "gemrateId": gemrate_id, "tcg": "one-piece", "setEvidence": "Romance Dawn",
            "collectorNumberEvidence": "OP01-120", "languageEvidence": "Japanese",
            "editionEvidence": "standard", "parallelEvidence": "Manga", "finishEvidence": "foil",
            "identityStatus": "review",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            persist_direct_identity_receipt(root / "direct", gemrate_id, direct_identity_payload(gemrate_id))
            candidates, reviews = apply_public_receipt_identity_proposals(
                [candidate], public_root=root / "public", direct_root=root / "direct",
            )
        self.assertEqual(reviews, [])
        self.assertEqual(candidates[0]["identityStatus"], "exact_confirmed")
        self.assertEqual(candidates[0]["canonicalPrintingKey"], "one-piece|romance dawn|op01-120|ja|standard|manga|foil")

    def test_exact_receipt_never_replaces_a_real_candidate_identity_conflict(self) -> None:
        gemrate_id = "f" * 40
        candidate = {
            "gemrateId": gemrate_id, "tcg": "one-piece", "setEvidence": "Romance Dawn",
            "collectorNumberEvidence": "OP01-120", "languageEvidence": "Japanese",
            "editionEvidence": "standard", "parallelEvidence": "Manga", "finishEvidence": "foil",
            "identityStatus": "review",
            "identityReviewReasons": ["duplicate_gemrate_id_collectorNumberEvidence_conflict"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            persist_direct_identity_receipt(root / "direct", gemrate_id, direct_identity_payload(gemrate_id))
            candidates, reviews = apply_public_receipt_identity_proposals(
                [candidate], public_root=root / "public", direct_root=root / "direct",
            )
        self.assertEqual(candidates[0]["identityStatus"], "review")
        self.assertEqual(reviews[0]["reasons"], candidate["identityReviewReasons"])

    def test_direct_receipt_hash_or_pointer_mismatch_is_rejected(self) -> None:
        gemrate_id = "e" * 40
        candidate = {
            "gemrateId": gemrate_id, "tcg": "one-piece", "setEvidence": "Romance Dawn",
            "collectorNumberEvidence": "OP01-120", "languageEvidence": "Japanese",
            "editionEvidence": "standard", "parallelEvidence": "Manga", "finishEvidence": "foil",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            persist_direct_identity_receipt(root / "direct", gemrate_id, direct_identity_payload(gemrate_id))
            receipt_path = root / "direct" / gemrate_id / "identity.receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["sourcePointer"] = "missing.json"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            candidates, reviews = apply_public_receipt_identity_proposals(
                [candidate], public_root=root / "public", direct_root=root / "direct",
            )
        self.assertEqual(candidates[0]["identityStatus"], "review")
        self.assertEqual(reviews[0]["reasons"], ["direct_receipt_source_pointer_invalid"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            persist_direct_identity_receipt(root / "direct", gemrate_id, direct_identity_payload(gemrate_id))
            payload_path = root / "direct" / gemrate_id / "population.json"
            tampered = json.loads(payload_path.read_text(encoding="utf-8"))
            tampered["data"]["gemrate_id"] = "f" * 40
            payload_path.write_text(json.dumps(tampered), encoding="utf-8")
            candidates, reviews = apply_public_receipt_identity_proposals(
                [candidate], public_root=root / "public", direct_root=root / "direct",
            )
        self.assertEqual(candidates[0]["identityStatus"], "review")
        self.assertEqual(reviews[0]["reasons"], ["direct_receipt_payload_sha256_mismatch"])

    def test_public_receipt_can_confirm_new_one_piece_identity_without_g10_crosswalk(self) -> None:
        gemrate_id = "a" * 40
        candidate = {
            "gemrateId": gemrate_id,
            "tcg": "one-piece",
            "nameEvidence": "Shanks",
            "setEvidence": "One Piece Japanese OP01-Romance Dawn",
            "collectorNumberEvidence": "OP01-120",
            "parallelEvidence": "Manga Alternate Art",
            "editionEvidence": "standard",
            "finishEvidence": "foil",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            persist_verified_public_capture(root, gemrate_id, public_receipt(gemrate_id))
            candidates, reviews = apply_public_receipt_identity_proposals([candidate], public_root=root)

        row = candidates[0]
        self.assertEqual(reviews, [])
        self.assertEqual(row["identityStatus"], "exact_confirmed")
        self.assertEqual(row["canonicalIdentity"], {
            "tcg": "one-piece",
            "setName": "One Piece Japanese OP01-Romance Dawn",
            "collectorNumber": "OP01-120",
            "language": "ja",
            "edition": "standard",
            "parallel": "Manga Alternate Art",
            "finish": "foil",
        })
        self.assertEqual(row["canonicalSourceCode"], "gemrate_public_card_page")
        self.assertIsNone(row["snkItemId"])

    def test_candidate_backfill_uses_confirmed_receipt_identity_for_population_and_snk_crosswalk(self) -> None:
        gemrate_id = "f" * 40
        candidate = {
            "gemrateId": gemrate_id,
            "tcg": "one-piece",
            "setEvidence": "One Piece Japanese OP01-Romance Dawn",
            "collectorNumberEvidence": "OP01-120",
            "parallelEvidence": "Manga Alternate Art",
            "editionEvidence": "standard",
            "finishEvidence": "foil",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            persist_verified_public_capture(root / "public", gemrate_id, public_receipt(gemrate_id))
            manifest, _ = run_candidate_backfill(
                [candidate],
                direct_root=root / "direct",
                public_root=root / "public",
                mirror_root=root / "mirror",
                as_of=date.today(),
            )

        row = manifest["candidates"][0]
        self.assertEqual(row["status"], "resolved")
        self.assertEqual(row["identityStatus"], "exact_confirmed")
        self.assertEqual(row["canonicalIdentity"]["collectorNumber"], "OP01-120")
        self.assertEqual(row["snkEligibility"], "eligible")
        self.assertEqual(manifest["publicReceiptIdentityResolution"]["confirmed"], 1)

    def test_public_receipt_language_conflict_enters_review_without_guessing(self) -> None:
        gemrate_id = "b" * 40
        candidate = {
            "gemrateId": gemrate_id,
            "tcg": "one-piece",
            "setEvidence": "One Piece Japanese OP01-Romance Dawn",
            "collectorNumberEvidence": "OP01-120",
            "parallelEvidence": "Manga Alternate Art",
            "languageEvidence": "English",
            "editionEvidence": "standard",
            "finishEvidence": "foil",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            persist_verified_public_capture(root, gemrate_id, public_receipt(gemrate_id))
            candidates, reviews = apply_public_receipt_identity_proposals([candidate], public_root=root)

        self.assertEqual(candidates[0]["identityStatus"], "review")
        self.assertIn("language_conflict", candidates[0]["identityReviewReasons"])
        self.assertEqual(reviews[0]["gemrateId"], gemrate_id)

    def test_fresh_receipt_conflict_demotes_existing_confirmation_to_review(self) -> None:
        gemrate_id = "a" * 40
        candidate = {
            "gemrateId": gemrate_id,
            "tcg": "one-piece",
            "identityStatus": "exact_confirmed",
            "canonicalPrintingKey": "one-piece|one piece japanese op01-romance dawn|op01-121|ja|standard|manga alternate art|foil",
            "canonicalIdentity": {
                "tcg": "one-piece", "setName": "One Piece Japanese OP01-Romance Dawn",
                "collectorNumber": "OP01-121", "language": "ja", "edition": "standard",
                "parallel": "Manga Alternate Art", "finish": "foil",
            },
            "canonicalSource": {"sourceCode": "snkrdunk", "externalId": "123", "storageScope": "opcg", "snkItemId": 123},
            "setEvidence": "One Piece Japanese OP01-Romance Dawn",
            "collectorNumberEvidence": "OP01-121",
            "parallelEvidence": "Manga Alternate Art",
            "editionEvidence": "standard",
            "finishEvidence": "foil",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            persist_verified_public_capture(root, gemrate_id, public_receipt(gemrate_id))
            candidates, reviews = apply_public_receipt_identity_proposals([candidate], public_root=root)

        self.assertEqual(candidates[0]["identityStatus"], "review")
        self.assertIn("collector_number_conflict", candidates[0]["identityReviewReasons"])
        self.assertEqual(reviews[0]["gemrateId"], gemrate_id)

    def test_public_receipt_short_candidate_number_and_parallel_conflict_are_reviewed(self) -> None:
        gemrate_id = "c" * 40
        candidate = {
            "gemrateId": gemrate_id,
            "tcg": "one-piece",
            "setEvidence": "One Piece Japanese OP01-Romance Dawn",
            "collectorNumberEvidence": "120",
            "parallelEvidence": "Alternate Art",
            "editionEvidence": "standard",
            "finishEvidence": "foil",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            persist_verified_public_capture(root, gemrate_id, public_receipt(gemrate_id))
            candidates, reviews = apply_public_receipt_identity_proposals([candidate], public_root=root)

        self.assertEqual(candidates[0]["identityStatus"], "review")
        self.assertEqual(reviews[0]["reasons"], ["collector_number_conflict", "parallel_conflict"])

    def test_public_receipt_missing_or_ambiguous_evidence_never_promotes_identity(self) -> None:
        missing_id = "d" * 40
        ambiguous_id = "e" * 40
        candidate = {
            "gemrateId": ambiguous_id,
            "tcg": "one-piece",
            "setEvidence": "One Piece Japanese OP01-Romance Dawn",
            "collectorNumberEvidence": "OP01-120",
            "parallelEvidence": "Manga Alternate Art",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            persist_verified_public_capture(root, ambiguous_id, public_receipt(ambiguous_id))
            candidates, reviews = apply_public_receipt_identity_proposals(
                [{"gemrateId": missing_id, "tcg": "one-piece"}, candidate], public_root=root,
            )

        by_id = {row["gemrateId"]: row for row in candidates}
        self.assertEqual(by_id[missing_id]["identityStatus"], "unmapped")
        self.assertEqual(by_id[ambiguous_id]["identityStatus"], "review")
        self.assertEqual(reviews[0]["reasons"], ["edition_unavailable", "finish_unavailable"])

    def test_roster_combines_pokemon_with_one_piece_discovery_without_search_pop(self) -> None:
        pokemon_id = "a" * 40
        one_piece_id = "b" * 40
        roster = build_candidate_roster(
            {"cards": [{"tcg": "pokemon", "gemrateId": pokemon_id, "name": "Pikachu"}]},
            {"candidates": [{"gemrateId": one_piece_id, "nameEvidence": "Luffy", "searchGemSignal": 999999}]},
            {"cards": []},
        )

        self.assertEqual([row["tcg"] for row in roster], ["one-piece", "pokemon"])
        self.assertNotIn("searchGemSignal", roster[0])
        self.assertNotIn("populationPsa10", roster[0])

    def test_roster_carries_exact_crosswalk_identity_for_snk(self) -> None:
        gemrate_id = "a" * 40
        roster = build_candidate_roster(
            {"cards": []},
            {"candidates": [{"gemrateId": gemrate_id}]},
            {"cards": [{
                "gemrateId": gemrate_id,
                "market": "one-piece",
                "canonicalSourceCode": "snkrdunk",
                "canonicalExternalId": "123",
                "storageScope": "opcg",
                "snkItemId": 456,
                "setName": "Romance Dawn",
                "collectorNumberRaw": "OP01-120",
                "language": "ja",
            }]},
        )

        row = roster[0]
        self.assertEqual(row["identityStatus"], "exact_confirmed")
        self.assertEqual(row["canonicalSource"], {
            "sourceCode": "snkrdunk", "externalId": "123", "storageScope": "opcg", "snkItemId": 456,
        })
        self.assertEqual(row["canonicalIdentity"]["collectorNumber"], "OP01-120")

    def test_duplicate_gemrate_candidate_evidence_enters_review_without_overwrite(self) -> None:
        gemrate_id = "a" * 40
        roster = build_candidate_roster(
            {"cards": [{
                "tcg": "pokemon", "gemrateId": gemrate_id, "name": "Pikachu",
                "collectorNumber": "001/S-P", "setName": "Promo", "language": "ja",
            }]},
            {"candidates": [{
                "gemrateId": gemrate_id, "collectorNumberEvidence": "OP01-120", "setEvidence": "Romance Dawn",
            }]},
            {"cards": []},
        )

        self.assertEqual(len(roster), 1)
        self.assertEqual(roster[0]["identityStatus"], "review")
        self.assertIn("duplicate_gemrate_id_tcg_conflict", roster[0]["identityReviewReasons"])
        self.assertEqual(roster[0]["collectorNumberEvidence"], "001/S-P")

    def test_duplicate_gemrate_language_aliases_do_not_create_false_review(self) -> None:
        gemrate_id = "a" * 40
        roster = build_candidate_roster(
            {"cards": [{
                "tcg": "pokemon", "gemrateId": gemrate_id, "name": "Pikachu",
                "collectorNumber": "001/S-P", "setName": "Promo", "language": "ja",
            }]},
            {"candidates": []},
            {"cards": [{
                "gemrateId": gemrate_id,
                "market": "pokemon",
                "canonicalSourceCode": "snkrdunk",
                "canonicalExternalId": "123",
                "storageScope": "pokemon",
                "collectorNumberRaw": "001/S-P",
                "setName": "Promo",
                "language": "jp",
            }]},
        )

        self.assertEqual(len(roster), 1)
        self.assertEqual(roster[0]["identityStatus"], "exact_confirmed")
        self.assertNotIn("duplicateGemrateIdReviewReasons", roster[0])

        chinese_id = "b" * 40
        chinese = build_candidate_roster(
            {"cards": [{
                "tcg": "pokemon", "gemrateId": chinese_id, "name": "Pikachu",
                "collectorNumber": "001/SV-P", "setName": "Promo", "language": "zh-hans",
            }]},
            {"candidates": []},
            {"cards": [{
                "gemrateId": chinese_id,
                "market": "pokemon",
                "canonicalSourceCode": "gemrate",
                "canonicalExternalId": chinese_id,
                "storageScope": "pokemon",
                "collectorNumberRaw": "001/SV-P",
                "setName": "Promo",
                "language": "zhCN",
            }]},
        )
        self.assertEqual(chinese[0]["identityStatus"], "exact_confirmed")
        self.assertNotIn("duplicateGemrateIdReviewReasons", chinese[0])

    def test_duplicate_gemrate_collector_format_evidence_does_not_create_false_review(self) -> None:
        gemrate_id = "c" * 40
        roster = build_candidate_roster(
            {"cards": [{
                "tcg": "pokemon", "gemrateId": gemrate_id, "name": "Pikachu",
                "collectorNumber": "110/080", "setName": "Promo", "language": "ja",
            }]},
            {"candidates": []},
            {"cards": [{
                "gemrateId": gemrate_id,
                "market": "pokemon",
                "canonicalSourceCode": "gemrate",
                "canonicalExternalId": gemrate_id,
                "storageScope": "pokemon",
                "collectorNumberRaw": "110/80",
                "setName": "Promo",
                "language": "ja",
            }]},
        )
        self.assertEqual(roster[0]["identityStatus"], "exact_confirmed")
        self.assertNotIn("duplicateGemrateIdReviewReasons", roster[0])

        mismatched = build_candidate_roster(
            {"cards": [{
                "tcg": "pokemon", "gemrateId": gemrate_id, "name": "Pikachu",
                "collectorNumber": "085", "setName": "Promo", "language": "ja",
            }]},
            {"candidates": []},
            {"cards": [{
                "gemrateId": gemrate_id,
                "market": "pokemon",
                "canonicalSourceCode": "gemrate",
                "canonicalExternalId": gemrate_id,
                "storageScope": "pokemon",
                "collectorNumberRaw": "085/SVP",
                "setName": "Promo",
                "language": "ja",
            }]},
        )
        self.assertEqual(mismatched[0]["identityStatus"], "review")
        self.assertIn("duplicate_gemrate_id_collectorNumberEvidence_conflict", mismatched[0]["identityReviewReasons"])

    def test_receipt_mapping_persists_atomically_and_blocks_rebind(self) -> None:
        gemrate_id = "a" * 40
        candidate = {
            "gemrateId": gemrate_id,
            "identityStatus": "exact_confirmed",
            "canonicalPrintingKey": "one-piece|romance dawn|op01-120|ja|standard|manga|foil",
            "canonicalSource": {"sourceCode": "gemrate_public_card_page"},
            "canonicalIdentity": {
                "tcg": "one-piece", "setName": "Romance Dawn", "collectorNumber": "OP01-120",
                "language": "ja", "edition": "standard", "parallel": "Manga", "finish": "foil",
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "receipt-mappings.json"
            self.assertEqual(
                persist_receipt_identity_mappings([candidate], path=path),
                {"added": 1, "total": 1, "aliasesAdded": 0, "aliasesRefreshed": 0, "aliasesTotal": 0},
            )
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["schemaVersion"], 2)
            self.assertEqual(saved["cards"][0]["gemrateId"], gemrate_id)
            conflict = dict(candidate)
            conflict["canonicalPrintingKey"] = "one-piece|romance dawn|op01-121|ja|standard|manga|foil"
            with self.assertRaisesRegex(RuntimeError, "GemRate ID rebind blocked"):
                persist_receipt_identity_mappings([conflict], path=path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), saved)
        with self.assertRaisesRegex(RuntimeError, "GemRate ID rebind blocked"):
            merge_receipt_mappings(
                {"cards": [{"gemrateId": gemrate_id, "canonicalPrintingKey": "one"}]},
                {"cards": [{"gemrateId": gemrate_id, "canonicalPrintingKey": "two", "identityStatus": "confirmed"}]},
            )

    def test_schema_two_aliases_do_not_promote_universal_or_member_and_block_cross_printing_rebind(self) -> None:
        entity_id = "a" * 40
        universal_id = "b" * 40
        candidate = {
            "gemrateId": entity_id, "identityStatus": "exact_confirmed",
            "canonicalPrintingKey": "one-piece|romance dawn|op01-120|ja|standard|manga|foil",
            "canonicalSource": {"sourceCode": "gemrate_direct", "storageScope": "gemrate_direct"},
            "canonicalIdentity": {"tcg": "one-piece", "setName": "Romance Dawn", "collectorNumber": "OP01-120", "language": "ja", "edition": "standard", "parallel": "Manga", "finish": "foil"},
             "directIdentityReceipt": {
                 "requestedGemrateId": entity_id, "entityGemrateId": entity_id,
                 "universalGemrateId": universal_id, "payloadSha256": "c" * 64,
                 "graders": {"psa": {"gemrateId": "d" * 40, "specId": "123"}},
                 "sourcePointer": "population.json", "fetchedAt": "2026-07-24T00:00:00Z",
             },
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "receipt-mappings.json"
            persisted = persist_receipt_identity_mappings([candidate], path=path)
            self.assertEqual(persisted["aliasesTotal"], 4)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([row["gemrateId"] for row in saved["cards"]], [entity_id])
            alias_only = {"schemaVersion": 2, "cards": [], "gemrateAliases": saved["gemrateAliases"]}
            self.assertEqual(merge_receipt_mappings({"cards": []}, alias_only)["cards"], [])
            refreshed = dict(candidate)
            refreshed["directIdentityReceipt"] = dict(candidate["directIdentityReceipt"])
            refreshed["directIdentityReceipt"]["payloadSha256"] = "e" * 64
            result = persist_receipt_identity_mappings([refreshed], path=path)
            self.assertEqual(result["aliasesAdded"], 0)
            self.assertEqual(result["aliasesRefreshed"], 4)
            refreshed_saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(all(row["receiptPayloadSha256"] == "e" * 64 for row in refreshed_saved["gemrateAliases"]))

            second_entity = "f" * 40
            same_printing = dict(candidate)
            same_printing["gemrateId"] = second_entity
            same_printing["directIdentityReceipt"] = {
                **candidate["directIdentityReceipt"],
                "requestedGemrateId": second_entity,
                "entityGemrateId": second_entity,
                "graders": {"psa": {"gemrateId": second_entity, "specId": "456"}},
            }
            same_result = persist_receipt_identity_mappings([same_printing], path=path)
            self.assertEqual(same_result["aliasesAdded"], 4)
            same_saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                len([row for row in same_saved["gemrateAliases"] if row["aliasType"] == "universal"]),
                2,
            )

            rebound = dict(same_printing)
            rebound["canonicalPrintingKey"] = "one-piece|romance dawn|op01-121|ja|standard|manga|foil"
            rebound["canonicalIdentity"] = {
                **same_printing["canonicalIdentity"],
                "collectorNumber": "OP01-121",
            }
            with self.assertRaisesRegex(RuntimeError, "GemRate alias rebind blocked"):
                persist_receipt_identity_mappings([rebound], path=path)

    def test_spec_aliases_are_namespaced_by_grader(self) -> None:
        entity_id = "9" * 40
        candidate = {
            "gemrateId": entity_id, "identityStatus": "exact_confirmed",
            "canonicalPrintingKey": "one-piece|romance dawn|op01-120|ja|standard|manga|foil",
            "canonicalSource": {"sourceCode": "gemrate_direct", "storageScope": "gemrate_direct"},
            "canonicalIdentity": {"tcg": "one-piece", "setName": "Romance Dawn", "collectorNumber": "OP01-120", "language": "ja", "edition": "standard", "parallel": "Manga", "finish": "foil"},
            "directIdentityReceipt": {
                "requestedGemrateId": entity_id, "entityGemrateId": entity_id,
                "universalGemrateId": None, "payloadSha256": "c" * 64,
                "graders": {
                    "psa": {"gemrateId": "a" * 40, "specId": "123"},
                    "bgs": {"gemrateId": "b" * 40, "specId": "123"},
                },
                "sourcePointer": "population.json", "fetchedAt": "2026-07-24T00:00:00Z",
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "receipt-mappings.json"
            persist_receipt_identity_mappings([candidate], path=path)
            saved = json.loads(path.read_text(encoding="utf-8"))
        specs = [row for row in saved["gemrateAliases"] if row["aliasType"] == "spec"]
        self.assertEqual({row["grader"] for row in specs}, {"psa", "bgs"})

    def test_default_mirror_resolver_requires_the_frozen_landing_payload(self) -> None:
        run_id = "a" * 64
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "g10-full-freeze.json"
            landing_manifest = root / "landing" / "g10" / "full" / run_id / "manifest.json"
            landing_manifest.parent.mkdir(parents=True)
            landing_manifest.write_text('{"runId":"' + run_id + '"}', encoding="utf-8")
            payload = landing_manifest.parent / "payload"
            payload.mkdir()
            manifest.write_text(json.dumps({
                "runId": run_id,
                "landingManifestSha256": __import__("hashlib").sha256(landing_manifest.read_bytes()).hexdigest(),
            }), encoding="utf-8")
            self.assertEqual(resolve_immutable_mirror_root(manifest, root / "landing"), payload)
            payload.rmdir()
            with self.assertRaisesRegex(RuntimeError, "immutable mirror payload is absent"):
                resolve_immutable_mirror_root(manifest, root / "landing")

    def test_direct_wins_mirror_and_history_is_direct_only(self) -> None:
        gemrate_id = "a" * 40
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            direct = root / "direct" / gemrate_id
            persist_verified_direct_population(
                root / "direct",
                gemrate_id,
                direct_payload(gemrate_id, 1200, "2026-07-24"),
            )
            (direct / "history_full.json").write_text("{}", encoding="utf-8")
            mirror = root / "mirror" / "cards" / "ptcg" / "card-1"
            mirror.mkdir(parents=True)
            (mirror / "populations.json").write_text(json.dumps(mirror_payload(1200)), encoding="utf-8")
            roster = [{"gemrateId": gemrate_id, "tcg": "pokemon", "identity": {"storageScope": "ptcg", "canonicalExternalId": "card-1"}}]

            manifest = run_offline_backfill(roster, direct_root=root / "direct", mirror_root=root / "mirror", as_of=date(2026, 7, 24))

        row = manifest["candidates"][0]
        self.assertEqual(row["status"], "resolved")
        self.assertEqual(row["populationPsa10"], 1200)
        self.assertEqual(row["currentTransport"], "gemrate_direct")
        self.assertEqual(row["historyStatus"], "ready")
        self.assertEqual(manifest["actualCounts"]["directCurrent"], 1)
        self.assertEqual(manifest["actualCounts"]["mirrorCurrent"], 1)

    def test_direct_api_remains_preferred_over_newer_mirror_observation(self) -> None:
        gemrate_id = "a" * 40
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            persist_verified_direct_population(
                root / "direct",
                gemrate_id,
                direct_payload(gemrate_id, 1200, "2026-07-23"),
            )
            mirror = root / "mirror" / "cards" / "ptcg" / "card-1"
            mirror.mkdir(parents=True)
            (mirror / "populations.json").write_text(
                json.dumps({"effectiveDate": "2026-07-24", **mirror_payload(1201)}), encoding="utf-8"
            )
            manifest = run_offline_backfill(
                [{
                    "gemrateId": gemrate_id,
                    "tcg": "pokemon",
                    "identity": {"storageScope": "ptcg", "canonicalExternalId": "card-1"},
                }],
                direct_root=root / "direct",
                mirror_root=root / "mirror",
                as_of=date(2026, 7, 24),
            )

        self.assertEqual(manifest["candidates"][0]["populationPsa10"], 1200)
        self.assertEqual(manifest["candidates"][0]["currentTransport"], "gemrate_direct")

    def test_exact_mirror_can_only_supply_current_and_missing_identity_is_unavailable(self) -> None:
        mirror_id = "a" * 40
        unavailable_id = "b" * 40
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mirror = root / "mirror" / "cards" / "opcg" / "card-2"
            mirror.mkdir(parents=True)
            (mirror / "populations.json").write_text(json.dumps(mirror_payload(999)), encoding="utf-8")
            roster = [
                {"gemrateId": mirror_id, "tcg": "one-piece", "identity": {"storageScope": "opcg", "canonicalExternalId": "card-2"}},
                {"gemrateId": unavailable_id, "tcg": "one-piece", "identity": None},
            ]
            manifest = run_offline_backfill(roster, direct_root=root / "direct", mirror_root=root / "mirror", as_of=date(2026, 7, 24))

        rows = {row["gemrateId"]: row for row in manifest["candidates"]}
        self.assertEqual(rows[mirror_id]["status"], "below-threshold")
        self.assertEqual(rows[mirror_id]["currentTransport"], "grade10_gemrate_mirror")
        self.assertEqual(rows[mirror_id]["historyStatus"], "unavailable")
        self.assertEqual(rows[unavailable_id]["status"], "unavailable")
        self.assertTrue(manifest["partial"])
        self.assertFalse(manifest["promotable"])
        self.assertTrue(manifest["classificationComplete"])
        self.assertEqual(manifest["retryCount"], 1)

    def test_public_card_details_is_current_population_fallback_with_provenance(self) -> None:
        gemrate_id = "a" * 40
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            persist_verified_public_capture(root / "public", gemrate_id, public_payload(gemrate_id, 1200), fetched_at="2026-07-24T00:00:00Z")
            manifest = run_offline_backfill(
                [{"gemrateId": gemrate_id, "tcg": "pokemon", "identity": None}],
                direct_root=root / "direct",
                public_root=root / "public",
                mirror_root=root / "mirror",
                as_of=date(2026, 7, 24),
            )

        row = manifest["candidates"][0]
        self.assertEqual(row["status"], "resolved")
        self.assertEqual(row["currentTransport"], "gemrate_public_card_page")
        self.assertEqual(row["historyStatus"], "unavailable")
        self.assertEqual(manifest["actualCounts"]["publicCurrent"], 1)
        self.assertEqual(row["transportObservations"][0]["transport"], "gemrate_public_card_page")
        self.assertEqual(manifest["attempted"], 1)
        self.assertEqual(manifest["succeeded"], 1)
        self.assertEqual(manifest["failed"], 0)

    def test_public_cache_requires_matching_receipt_raw_hash_and_fresh_fetch(self) -> None:
        gemrate_id = "a" * 40
        as_of = date.today()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            card_dir = root / "public" / gemrate_id
            card_dir.mkdir(parents=True)
            (card_dir / "card_details.json").write_text(
                json.dumps(public_payload(gemrate_id, 1200)), encoding="utf-8"
            )
            missing_receipt = run_offline_backfill(
                [{"gemrateId": gemrate_id, "tcg": "pokemon", "identity": None}],
                direct_root=root / "direct",
                public_root=root / "public",
                mirror_root=root / "mirror",
                as_of=as_of,
            )
            self.assertEqual(missing_receipt["candidates"][0]["status"], "unavailable")

            persist_verified_public_capture(root / "public", gemrate_id, public_payload(gemrate_id, 1200))
            verified = run_offline_backfill(
                [{"gemrateId": gemrate_id, "tcg": "pokemon", "identity": None}],
                direct_root=root / "direct",
                public_root=root / "public",
                mirror_root=root / "mirror",
                as_of=as_of,
            )
            self.assertEqual(verified["candidates"][0]["currentTransport"], "gemrate_public_card_page")

            receipt_path = card_dir / "card_details.raw.receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["fetchedAt"] = f"{(as_of - timedelta(days=3)).isoformat()}T00:00:00Z"
            document_path = card_dir / "card_details.json"
            document = json.loads(document_path.read_text(encoding="utf-8"))
            document["privateSourceReceipt"] = receipt
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            document_path.write_text(json.dumps(document), encoding="utf-8")
            stale = run_offline_backfill(
                [{"gemrateId": gemrate_id, "tcg": "pokemon", "identity": None}],
                direct_root=root / "direct",
                public_root=root / "public",
                mirror_root=root / "mirror",
                as_of=as_of,
            )
            self.assertEqual(stale["candidates"][0]["status"], "unavailable")

            persist_verified_public_capture(root / "public", gemrate_id, public_payload(gemrate_id, 1200))
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            raw_path = card_dir / receipt["sourcePointer"]
            raw_path.write_text("{}", encoding="utf-8")
            corrupted = run_offline_backfill(
                [{"gemrateId": gemrate_id, "tcg": "pokemon", "identity": None}],
                direct_root=root / "direct",
                public_root=root / "public",
                mirror_root=root / "mirror",
                as_of=as_of,
            )
            self.assertEqual(corrupted["candidates"][0]["status"], "unavailable")
            worklist = build_public_card_details_worklist(
                [{"gemrateId": gemrate_id, "tcg": "pokemon"}],
                direct_root=root / "direct",
                public_root=root / "public",
                as_of=as_of,
            )
            self.assertEqual(worklist["ids"], [gemrate_id])

    def test_public_cache_accepts_verified_dom_evidence_receipt(self) -> None:
        gemrate_id = "b" * 40
        as_of = date.today()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload, reason = build_public_card_page_payload(
                gemrate_id,
                headers=["Grader", "POP", "Gem Mint"],
                psa_row=["PSA", "1,500", "1,200"],
                canonical_url="https://www.gemrate.com/card/exact-card",
                title="Exact card",
                dom_sha256="c" * 64,
                route_verified=True,
            )
            self.assertIsNone(reason)
            self.assertIsNotNone(payload)
            persist_verified_public_capture(root / "public", gemrate_id, payload)
            manifest = run_offline_backfill(
                [{"gemrateId": gemrate_id, "tcg": "pokemon", "identity": None}],
                direct_root=root / "direct",
                public_root=root / "public",
                mirror_root=root / "mirror",
                as_of=as_of,
            )

        row = manifest["candidates"][0]
        self.assertEqual(row["currentTransport"], "gemrate_public_card_page")
        self.assertEqual(manifest["actualCounts"]["publicCurrent"], 1)

    def test_population_thresholds_keep_only_971_to_999_in_pre_entry_radar(self) -> None:
        high_id = "a" * 40
        radar_id = "b" * 40
        outside_id = "c" * 40
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for gemrate_id, population in ((high_id, 1000), (radar_id, 971), (outside_id, 970)):
                persist_verified_direct_population(
                    root / "direct",
                    gemrate_id,
                    direct_payload(gemrate_id, population),
                )
            manifest = run_offline_backfill(
                [
                    {"gemrateId": high_id, "tcg": "pokemon", "identity": None},
                    {"gemrateId": radar_id, "tcg": "pokemon", "identity": None},
                    {"gemrateId": outside_id, "tcg": "pokemon", "identity": None},
                ],
                direct_root=root / "direct",
                public_root=root / "public",
                mirror_root=root / "mirror",
                as_of=date(2026, 7, 24),
            )

        rows = {row["gemrateId"]: row for row in manifest["candidates"]}
        self.assertEqual(rows[high_id]["trackingStatus"], "eligible")
        self.assertEqual(rows[radar_id]["trackingStatus"], "pre_entry_radar")
        self.assertEqual(rows[outside_id]["trackingStatus"], "outside_radar")
        self.assertEqual(manifest["actualCounts"]["eligible"], 1)
        self.assertEqual(manifest["actualCounts"]["preEntryRadar"], 1)
        self.assertEqual(manifest["actualCounts"]["outsideRadar"], 1)

    def test_public_card_page_value_is_not_compared_to_direct_source_date(self) -> None:
        gemrate_id = "a" * 40
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            persist_verified_direct_population(
                root / "direct", gemrate_id, direct_payload(gemrate_id, 1200)
            )
            persist_verified_public_capture(
                root / "public", gemrate_id, public_payload(gemrate_id, 1199)
            )
            manifest = run_offline_backfill(
                [{"gemrateId": gemrate_id, "tcg": "pokemon", "identity": None}],
                direct_root=root / "direct",
                public_root=root / "public",
                mirror_root=root / "mirror",
                as_of=date(2026, 7, 24),
            )

        row = manifest["candidates"][0]
        self.assertEqual(row["status"], "resolved")
        self.assertEqual(row["currentTransport"], "gemrate_direct")
        self.assertEqual(manifest["attempted"], 1)
        self.assertEqual(manifest["succeeded"], 1)
        self.assertEqual(manifest["failed"], 0)
        self.assertTrue(manifest["classificationComplete"])
        self.assertFalse(manifest["rankingPromotable"])

    def test_conflicting_same_day_transports_are_review_and_unsettled_identity_is_not_carried(self) -> None:
        direct_id = "a" * 40
        conflict_id = "b" * 40
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for gemrate_id, population in ((direct_id, 1300), (conflict_id, 1200)):
                persist_verified_direct_population(
                    root / "direct", gemrate_id, direct_payload(gemrate_id, population)
                )
            mirror = root / "mirror" / "cards" / "ptcg" / "conflict"
            mirror.mkdir(parents=True)
            mirror_file = mirror / "populations.json"
            mirror_file.write_text(json.dumps(mirror_payload(1199, "2026-07-23")), encoding="utf-8")
            # Backdate the cache write so the date-free mirror payload reads as
            # observed on the run's as_of date (the scenario under test), not
            # wall-clock today.
            observed = datetime(2026, 7, 23, tzinfo=timezone.utc).timestamp()
            os.utime(mirror_file, (observed, observed))
            first = run_offline_backfill(
                [
                    {"gemrateId": direct_id, "tcg": "pokemon", "identity": None},
                    {"gemrateId": conflict_id, "tcg": "pokemon", "identity": {"storageScope": "ptcg", "canonicalExternalId": "conflict"}},
                ],
                direct_root=root / "direct",
                mirror_root=root / "mirror",
                as_of=date(2026, 7, 23),
            )
            resumed = run_offline_backfill(
                [{"gemrateId": direct_id, "tcg": "pokemon", "identity": None}],
                direct_root=root / "missing",
                mirror_root=root / "missing",
                as_of=date(2026, 7, 24),
                checkpoint=first,
                resume=True,
            )

        rows = {row["gemrateId"]: row for row in first["candidates"]}
        self.assertEqual(rows[conflict_id]["status"], "review")
        self.assertEqual(resumed["actualCounts"]["carriedForward"], 0)
        self.assertEqual(resumed["candidates"][0]["status"], "unavailable")

    def test_resume_never_carries_checkpoint_over_current_identity_review(self) -> None:
        gemrate_id = "a" * 40
        checkpoint = {
            "asOf": "2026-07-23",
            "candidates": [{
                "gemrateId": gemrate_id,
                "tcg": "pokemon",
                "status": "resolved",
                "populationPsa10": 1200,
                "effectiveDate": "2026-07-23",
                "identityStatus": "exact_confirmed",
                "canonicalPrintingKey": "pokemon|set|001/001|ja|standard||holo",
            }],
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = run_offline_backfill(
                [{
                    "gemrateId": gemrate_id,
                    "tcg": "pokemon",
                    "identityStatus": "review",
                    "identityReviewReasons": ["gemrate_direct_public_identity_conflict"],
                    "canonicalPrintingKey": "pokemon|set|001/001|ja|standard||holo",
                }],
                direct_root=root / "direct",
                public_root=root / "public",
                mirror_root=root / "mirror",
                as_of=date(2026, 7, 24),
                checkpoint=checkpoint,
                resume=True,
            )
        self.assertEqual(result["actualCounts"]["carriedForward"], 0)
        self.assertEqual(result["candidates"][0]["status"], "review")
        self.assertEqual(result["candidates"][0]["reason"], "identity_conflict")

    def test_stale_checkpoint_and_cached_population_are_not_treated_as_current(self) -> None:
        gemrate_id = "a" * 40
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            persist_verified_direct_population(
                root / "direct",
                gemrate_id,
                direct_payload(gemrate_id, 1200, "2026-07-20"),
                fetched_at="2026-07-20T00:00:00Z",
            )
            checkpoint = run_offline_backfill(
                [{"gemrateId": gemrate_id, "tcg": "pokemon", "identity": None}],
                direct_root=root / "direct",
                public_root=root / "public",
                mirror_root=root / "mirror",
                as_of=date(2026, 7, 20),
            )
            retried = run_offline_backfill(
                [{"gemrateId": gemrate_id, "tcg": "pokemon", "identity": None}],
                direct_root=root / "direct",
                public_root=root / "public",
                mirror_root=root / "mirror",
                as_of=date(2026, 7, 24),
                checkpoint=checkpoint,
                resume=True,
            )
            worklist = build_public_card_details_worklist(
                [{"gemrateId": gemrate_id, "tcg": "pokemon"}],
                direct_root=root / "direct",
                public_root=root / "public",
                as_of=date(2026, 7, 24),
            )

        self.assertEqual(retried["actualCounts"]["carriedForward"], 0)
        self.assertEqual(retried["candidates"][0]["status"], "unavailable")
        self.assertEqual(worklist["ids"], [gemrate_id])

    def test_keyless_worklist_uses_only_exact_ids_and_never_search_population(self) -> None:
        first_id = "a" * 40
        second_id = "b" * 40
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            persist_verified_direct_population(
                root / "direct", first_id, direct_payload(first_id, 1200)
            )
            worklist = build_public_card_details_worklist(
                [
                    {"gemrateId": first_id, "tcg": "pokemon", "searchGemSignal": 999999},
                    {"gemrateId": second_id, "tcg": "one-piece", "searchGemSignal": 999999},
                ],
                direct_root=root / "direct",
                public_root=root / "public",
                as_of=date(2026, 7, 24),
            )

        self.assertEqual(worklist["ids"], [second_id])
        self.assertEqual(worklist["worklist"], [{"gemrateId": second_id, "tcg": "one-piece"}])
        self.assertNotIn("searchGemSignal", worklist["worklist"][0])

    def test_keyless_collection_writes_exact_cache_then_classifies_without_search_population(self) -> None:
        gemrate_id = "a" * 40
        calls: list[dict] = []

        def collector(ids: list[str], *, cards_dir: Path, delay: float, resume: bool) -> dict:
            calls.append({"ids": ids, "delay": delay, "resume": resume})
            persist_verified_public_capture(cards_dir, gemrate_id, public_payload(gemrate_id, 1200), fetched_at="2026-07-24T00:00:00Z")
            return {"attempted": 1, "succeeded": 1, "failed": 0, "cached": 0, "partial": False}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stale = root / "public" / gemrate_id
            stale.mkdir(parents=True)
            (stale / "card_details.json").write_text(
                json.dumps(public_payload("f" * 40, 1200)), encoding="utf-8"
            )
            manifest, worklist = run_candidate_backfill(
                [{"gemrateId": gemrate_id, "tcg": "pokemon", "identity": None, "searchGemSignal": 999999}],
                direct_root=root / "direct",
                public_root=root / "public",
                mirror_root=root / "mirror",
                as_of=date(2026, 7, 24),
                collect_public=True,
                public_delay=0.1,
                public_collector=collector,
            )

        self.assertEqual(calls, [{"ids": [gemrate_id], "delay": 0.1, "resume": False}])
        self.assertEqual(worklist["ids"], [gemrate_id])
        self.assertEqual(manifest["candidates"][0]["populationPsa10"], 1200)
        self.assertEqual(manifest["candidates"][0]["currentTransport"], "gemrate_public_card_page")
        self.assertTrue(manifest["classificationComplete"])
        self.assertFalse(manifest["rankingPromotable"])
        self.assertEqual(manifest["candidates"][0]["snkEligibility"], "review")

    def test_resume_retries_unavailable_and_direct_wins_page_and_mirror(self) -> None:
        gemrate_id = "a" * 40
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = run_offline_backfill(
                [{"gemrateId": gemrate_id, "tcg": "pokemon", "identity": None}],
                direct_root=root / "direct",
                public_root=root / "public",
                mirror_root=root / "mirror",
                as_of=date(2026, 7, 24),
            )
            mirror = root / "mirror" / "cards" / "ptcg" / "card-1"
            mirror.mkdir(parents=True)
            persist_verified_direct_population(
                root / "direct", gemrate_id, direct_payload(gemrate_id, 1200)
            )
            persist_verified_public_capture(
                root / "public", gemrate_id, public_payload(gemrate_id, 1199)
            )
            (mirror / "populations.json").write_text(json.dumps(mirror_payload(1200)), encoding="utf-8")
            retried = run_offline_backfill(
                [{"gemrateId": gemrate_id, "tcg": "pokemon", "identity": {"storageScope": "ptcg", "canonicalExternalId": "card-1"}}],
                direct_root=root / "direct",
                public_root=root / "public",
                mirror_root=root / "mirror",
                as_of=date(2026, 7, 24),
                checkpoint=first,
                resume=True,
            )

        self.assertEqual(first["candidates"][0]["status"], "unavailable")
        self.assertEqual(retried["actualCounts"]["carriedForward"], 0)
        self.assertEqual(retried["candidates"][0]["status"], "resolved")
        self.assertEqual(retried["candidates"][0]["currentTransport"], "gemrate_direct")
        self.assertTrue(retried["classificationComplete"])
        self.assertFalse(retried["rankingPromotable"])

    def test_cli_writes_ids_worklist_and_accepts_complete_unavailable_classification(self) -> None:
        gemrate_id = "a" * 40
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            active = root / "active.json"
            discovery = root / "one-piece.json"
            crosswalk = root / "crosswalk.json"
            receipt_mappings = root / "receipt-mappings.json"
            out = root / "out"
            active.write_text('{"cards": []}', encoding="utf-8")
            discovery.write_text(json.dumps({"candidates": [{"gemrateId": gemrate_id}]}), encoding="utf-8")
            crosswalk.write_text('{"cards": []}', encoding="utf-8")
            args = [
                "gemrate_candidate_backfill.py",
                "--active-universe", str(active),
                "--one-piece-candidates", str(discovery),
                "--crosswalk", str(crosswalk),
                "--receipt-mappings", str(receipt_mappings),
                "--direct-root", str(root / "direct"),
                "--public-root", str(root / "public"),
                "--mirror-root", str(root / "mirror"),
                "--out", str(out),
                "--as-of", "2026-07-24",
            ]
            with patch.object(sys, "argv", args):
                self.assertEqual(candidate_backfill.main(), 0)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            worklist = json.loads((out / "public-card-details-worklist.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["classificationComplete"])
            self.assertFalse(manifest["rankingPromotable"])
            self.assertEqual(worklist["ids"], [gemrate_id])
            self.assertEqual((out / "public-card-details-ids.txt").read_text(encoding="utf-8"), f"{gemrate_id}\n")
            with patch.object(sys, "argv", [*args, "--require-ranking-ready"]):
                self.assertEqual(candidate_backfill.main(), 1)

    def test_atomic_checkpoint_interruption_keeps_last_valid_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            checkpoint = Path(temp) / "checkpoint.json"
            original = {"asOf": "2026-07-23", "candidates": [{"gemrateId": "a" * 40}]}
            replacement = {"asOf": "2026-07-24", "candidates": [{"gemrateId": "b" * 40}]}
            checkpoint.write_text(json.dumps(original), encoding="utf-8")
            with patch.object(candidate_backfill.os, "replace", side_effect=InterruptedError("simulated stop")):
                with self.assertRaises(InterruptedError):
                    candidate_backfill._atomic_write_json(checkpoint, replacement)
            self.assertEqual(json.loads(checkpoint.read_text(encoding="utf-8")), original)
            self.assertEqual(list(checkpoint.parent.glob(".checkpoint.json.*.tmp")), [])

    def test_atomic_checkpoint_successfully_replaces_complete_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            checkpoint = Path(temp) / "checkpoint.json"
            document = {"asOf": "2026-07-24", "candidates": [{"gemrateId": "c" * 40}]}
            candidate_backfill._atomic_write_json(checkpoint, document)
            self.assertEqual(json.loads(checkpoint.read_text(encoding="utf-8")), document)


if __name__ == "__main__":
    unittest.main()
