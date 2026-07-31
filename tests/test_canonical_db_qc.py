from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "canonical_db_qc",
    ROOT / "pipelines" / "canonical_db_qc.py",
)
assert SPEC and SPEC.loader
qc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qc)


AS_OF = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def geometry_pass(_path: Path) -> dict[str, str]:
    return {"status": "passed", "policyId": "test-geometry"}


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def candidate_card(
    card_id: str = "cmc_one",
    *,
    tcg: str = "pokemon",
    gemrate_id: str = "a" * 40,
    population: int = 1200,
) -> dict[str, object]:
    return {
        "pokedexId": card_id,
        "canonicalSourceCode": "gemrate",
        "canonicalExternalId": gemrate_id,
        "gemrateId": gemrate_id,
        "pokedexStatus": "confirmed",
        "tcg": tcg,
        "cardLanguage": "en",
        "name": f"Card {card_id}",
        "setName": f"Set {card_id}",
        "collectorNumber": f"{card_id}/100",
        "populationPsa10": population,
        "populationAsOf": iso(AS_OF - timedelta(days=1)),
        "populationObservedDate": (AS_OF - timedelta(days=1)).date().isoformat(),
        "populationPayloadSha256": "1" * 64,
        "populationSourceState": "gemrate_exact",
        "populationEstimated": False,
        "rankMemberships": {},
    }


def candidate(
    cards: list[dict[str, object]] | None = None,
    monitoring: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    cards = cards or [candidate_card()]
    monitoring = monitoring or []
    collection = {"cards": cards, "monitoringCandidates": monitoring}
    return {
        "schemaVersion": "5.0.0",
        "cards": cards,
        "monitoringCandidates": monitoring,
        "counts": {
            "qualified": len(cards),
            "monitoring": len(monitoring),
            "collection": len(cards) + len(monitoring),
        },
        "collectionPayloadSha256": hashlib.sha256(
            json.dumps(
                collection,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
    }


def facts_for(
    asset_path: Path,
    *,
    card: dict[str, object] | None = None,
    variant_id: int = 1,
    image_hash: str | None = None,
    market_rank: int = 1,
) -> dict[str, object]:
    card = card or candidate_card()
    image_hash = image_hash or hashlib.sha256(asset_path.read_bytes()).hexdigest()
    card_id = str(card["pokedexId"])
    tcg = str(card["tcg"])
    set_name = str(card["setName"])
    collector = str(card["collectorNumber"])
    population = int(card["populationPsa10"])
    gemrate_id = str(card["gemrateId"])
    snk_id = str(100000 + variant_id)
    ebay_id = f"00000000-0000-4000-8000-{variant_id:012d}"
    printing_key = (
        tcg.casefold(),
        "en",
        set_name.casefold(),
        collector.casefold(),
        "base",
        "standard",
        "regular",
    )
    printing_hash = hashlib.sha256(
        "|".join(printing_key).encode("utf-8")
    ).hexdigest()
    source_version_sha256 = "3" * 64
    approval_binding_sha256 = hashlib.sha256(
        "|".join(
            (
                str(variant_id),
                str(variant_id),
                image_hash,
                source_version_sha256,
                printing_hash,
                "en",
            )
        ).encode("utf-8")
    ).hexdigest()
    return {
        "catalogByOpaque": {
            card_id: {
                "variant_id": variant_id,
                "opaque_id": card_id,
                "tcg_code": tcg,
                "card_language": "en",
                "canonical_name": card["name"],
                "set_name": set_name,
                "collector_number": collector,
                "identity_status": "confirmed",
                "printing_tcg_code": tcg,
                "printing_card_language": "en",
                "printing_set_name": set_name,
                "printing_collector_number": collector,
                "edition_code": "base",
                "parallel_code": "standard",
                "finish_code": "regular",
                "canonical_printing_sha256": printing_hash,
                "printing_identity_status": "canonical",
                "printing_evidence_sha256": "e" * 64,
            }
        },
        "sourceIdentitiesByVariant": {
            variant_id: [
                {
                    "variant_id": variant_id,
                    "source_code": "gemrate",
                    "external_entity_id": gemrate_id,
                    "match_status": "exact",
                    "evidence_sha256": "1" * 64,
                },
                {
                    "variant_id": variant_id,
                    "source_code": "snkrdunk",
                    "external_entity_id": snk_id,
                    "match_status": "exact",
                    "evidence_sha256": "2" * 64,
                },
                {
                    "variant_id": variant_id,
                    "source_code": "ebay",
                    "external_entity_id": ebay_id,
                    "match_status": "exact",
                    "evidence_sha256": "3" * 64,
                },
            ]
        },
        "pricesByVariant": {
            variant_id: [
                {
                    "variant_id": variant_id,
                    "id": 1,
                    "source_code": "snk_psa10",
                    "observed_date": (AS_OF - timedelta(days=30)).date(),
                    "effective_at": AS_OF - timedelta(days=30),
                    "price_usd": 90,
                    "source_priority": 10,
                    "metric_status": "ready",
                },
                {
                    "variant_id": variant_id,
                    "id": 2,
                    "source_code": "snk_psa10",
                    "observed_date": AS_OF.date(),
                    "effective_at": AS_OF - timedelta(hours=1),
                    "price_usd": 100,
                    "source_priority": 10,
                    "metric_status": "ready",
                },
                {
                    "variant_id": variant_id,
                    "id": 3,
                    "source_code": "ebay",
                    "observed_date": (AS_OF - timedelta(days=30)).date(),
                    "effective_at": AS_OF - timedelta(days=30),
                    "price_usd": 90,
                    "source_priority": 40,
                    "metric_status": "ready",
                },
                {
                    "variant_id": variant_id,
                    "id": 4,
                    "source_code": "ebay",
                    "observed_date": AS_OF.date(),
                    "effective_at": AS_OF - timedelta(hours=1),
                    "price_usd": 100,
                    "source_priority": 40,
                    "metric_status": "ready",
                },
            ]
        },
        "salesByVariant": {
            variant_id: [
                {
                    "variant_id": variant_id,
                    "id": sale_id,
                    "source_code": "snk_psa10",
                    "external_entity_id": snk_id,
                    "grader_code": "PSA",
                    "grade_label": "PSA 10",
                    "sold_at": AS_OF - timedelta(days=sale_id),
                    "fetched_at": AS_OF - timedelta(hours=12),
                    "timestamp_quality": "exact",
                    "unit_price_usd": 100,
                    "quantity": 1,
                    "transaction_value_usd": 100,
                    "coverage_status": "partial",
                    "source_payload_sha256": "4" * 64,
                }
                for sale_id in range(1, 11)
            ]
        },
        "indexByVariant": {
            variant_id: [
                {
                    "variant_id": variant_id,
                    "index_code": "tcg-combined",
                    "index_version": "psa10-v3-complete",
                    "effective_at": AS_OF - timedelta(hours=1),
                    "effective_date": AS_OF.date(),
                    "rank_position": market_rank,
                    "reference_price_usd": 100,
                    "psa10_population": population,
                    "market_cap_usd": 100 * population,
                    "metric_status": "ready",
                }
            ]
        },
        "imagesByVariant": {
            variant_id: [
                {
                    "variant_id": variant_id,
                    "asset_variant_id": variant_id,
                    "asset_id": variant_id,
                    "image_kind": "raw_front",
                    "content_sha256": image_hash,
                    "private_object_key": str(asset_path),
                    "mime_type": "image/webp",
                    "width_px": 400,
                    "height_px": 560,
                    "source_version_sha256": source_version_sha256,
                    "captured_at": AS_OF - timedelta(days=1),
                    "qc_id": variant_id,
                    "semantic_match_status": "human_or_vision_confirmed",
                    "card_number_match": 1,
                    "language_match": 1,
                    "tcg_match": 1,
                    "raw_front_confirmed": 1,
                    "public_allowed": 1,
                    "rejection_reason": None,
                    "checked_at": AS_OF - timedelta(hours=2),
                    "qc_version": "human-review-v2",
                    "source_path": str(asset_path),
                    "pointer_public_allowed": 1,
                    "approval_image_asset_id": variant_id,
                    "approval_variant_id": variant_id,
                    "approval_content_sha256": image_hash,
                    "approval_source_version_sha256": source_version_sha256,
                    "approval_printing_sha256": printing_hash,
                    "approval_expected_language": "en",
                    "approval_binding_sha256": approval_binding_sha256,
                    "approval_decision_code_sha256": "d" * 64,
                    "current_image_printing_sha256": printing_hash,
                    "current_image_printing_language": "en",
                    "current_image_printing_status": "canonical",
                    "registered_rejection_content_sha256": None,
                }
            ]
        },
        "rowCounts": {
            "catalog": 1,
            "sourceIdentities": 2,
            "prices": 2,
            "sales": 10,
            "indexConstituents": 1,
            "imageRows": 1,
        },
    }


def merge_facts(*documents: dict[str, object]) -> dict[str, object]:
    merged: dict[str, object] = {
        "catalogByOpaque": {},
        "sourceIdentitiesByVariant": {},
        "pricesByVariant": {},
        "salesByVariant": {},
        "indexByVariant": {},
        "imagesByVariant": {},
        "rowCounts": {},
    }
    for document in documents:
        for key in (
            "catalogByOpaque",
            "sourceIdentitiesByVariant",
            "pricesByVariant",
            "salesByVariant",
            "indexByVariant",
            "imagesByVariant",
        ):
            merged[key].update(document[key])
        for key, value in document["rowCounts"].items():
            merged["rowCounts"][key] = merged["rowCounts"].get(key, 0) + value
    return merged


class CanonicalDbQcTests(unittest.TestCase):
    def test_audit_population_uses_deterministic_discovery_rows_even_when_formal_is_zero(
        self,
    ) -> None:
        rows = [
            {
                "resolved_variant_id": 1,
                "opaque_id": "cmc_qualified",
                "tcg_code": "pokemon",
                "canonical_name": "Qualified",
                "set_name": "Set",
                "collector_number": "001",
                "gemrate_id": "a" * 40,
                "top_grade_population": 1200,
                "effective_at": AS_OF,
                "observed_date": AS_OF.date(),
                "payload_sha256": "1" * 64,
            },
            {
                "resolved_variant_id": 2,
                "opaque_id": "cmc_monitoring",
                "tcg_code": "one-piece",
                "canonical_name": "Monitoring",
                "set_name": "Set",
                "collector_number": "002",
                "gemrate_id": "b" * 40,
                "top_grade_population": 980,
                "effective_at": AS_OF,
                "observed_date": AS_OF.date(),
                "payload_sha256": "2" * 64,
            },
        ]
        with (
            mock.patch.object(qc, "latest_exact_population_rows", return_value=rows),
            mock.patch.object(qc, "_printing_row_identity", return_value=None),
            mock.patch.object(
                qc,
                "build_formal_universe_candidate",
                return_value={"counts": {"qualified": 0}, "cards": []},
            ),
            mock.patch.object(qc, "active_universe_lock_hash", return_value="a" * 64),
        ):
            document, counts, input_hash, formal_candidate, formal_lock_sha = qc.build_audit_input(object())
        self.assertEqual(counts["discoveryEvidenceCount"], 2)
        self.assertEqual(counts["auditedCandidates"], 2)
        self.assertEqual(counts["populationQualified"], 1)
        self.assertEqual(counts["populationMonitoring"], 1)
        self.assertEqual(counts["formalEligible"], 0)
        self.assertEqual(len(document["cards"]), 1)
        self.assertEqual(len(document["monitoringCandidates"]), 1)
        self.assertRegex(input_hash, r"^[0-9a-f]{64}$")
        self.assertEqual(formal_candidate["counts"]["qualified"], 0)
        self.assertRegex(formal_lock_sha, r"^[0-9a-f]{64}$")

    def test_complete_exact_card_passes_and_receipt_binds_report_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "one.webp"
            asset.write_bytes(b"verified-card-image")
            report = qc.audit_candidate(
                candidate(),
                facts_for(asset),
                universe_hash="a" * 64,
                run_id="qc_fixture_pass",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
            )
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["counts"]["releaseReadyQualified"], 1)
            self.assertTrue(report["releaseGate"]["eligible"])
            self.assertEqual(
                report["cards"][0]["facts"]["price"]["priorityMeta"][
                    "crossSourceQc"
                ]["status"],
                "confirmed",
            )
            output = qc.write_report_and_receipt(report, root / "reports")
            receipt = json.loads(Path(output["receipt"]).read_text(encoding="utf-8"))
            self.assertEqual(receipt["reportSha256"], output["reportSha256"])
            self.assertEqual(
                hashlib.sha256(Path(output["report"]).read_bytes()).hexdigest(),
                output["reportSha256"],
            )
            self.assertFalse(output["replayed"])
            self.assertTrue(
                qc.write_report_and_receipt(report, root / "reports")["replayed"]
            )

    def test_discovery_only_missing_printing_does_not_add_market_cap_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "one.webp"
            asset.write_bytes(b"verified-card-image")
            formal = candidate_card("cmc_formal", gemrate_id="a" * 40)
            discovery_only = candidate_card("cmc_discovery", gemrate_id="b" * 40)
            facts = facts_for(asset, card=formal, variant_id=1)
            facts["formalVariantIds"] = [1]
            report = qc.audit_candidate(
                candidate(cards=[formal, discovery_only]),
                facts,
                universe_hash="a" * 64,
                run_id="qc_formal_cohort",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
            )
            by_id = {row["id"]: row for row in report["cards"]}
            self.assertNotIn("market_cap_not_materialized", by_id["cmc_formal"]["blockers"])
            self.assertNotIn("market_cap_not_materialized", by_id["cmc_discovery"]["blockers"])
            self.assertEqual(
                by_id["cmc_discovery"]["facts"]["marketCap"]["status"],
                "not_formal",
            )

    def test_correct_canvas_with_shrunken_card_is_release_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "shrunken.webp"
            image = Image.new("RGBA", (429, 600), (0, 0, 0, 0))
            ImageDraw.Draw(image).rounded_rectangle(
                (100, 120, 329, 480),
                radius=20,
                fill=(20, 40, 60, 255),
            )
            image.save(asset, format="WEBP", lossless=True)
            report = qc.audit_candidate(
                candidate(),
                facts_for(asset),
                universe_hash="b" * 64,
                run_id="qc_shrunken_card",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
            )
            card = report["cards"][0]
            self.assertIn("image_canvas_geometry_invalid", card["blockers"])
            self.assertIn(
                "card_fill_too_small",
                card["facts"]["image"]["geometry"]["reasons"],
            )

    def test_pricecharting_anchor_uses_chart_observed_date_not_fetch_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "pc-anchor.webp"
            asset.write_bytes(b"pc-anchor-image")
            facts = facts_for(asset)
            facts["sourceIdentitiesByVariant"][1] = [
                facts["sourceIdentitiesByVariant"][1][0],
                {"variant_id": 1, "source_code": "pricecharting", "external_entity_id": "123", "match_status": "exact", "evidence_sha256": "2" * 64},
            ]
            facts["pricesByVariant"][1] = [{
                "variant_id": 1, "id": 3, "source_code": "pricecharting",
                "observed_date": (AS_OF - timedelta(days=30)).date(),
                "effective_at": AS_OF - timedelta(hours=1), "price_usd": 100,
                "source_priority": 95, "metric_status": "ready",
            }]
            report = qc.audit_candidate(candidate(), facts, universe_hash="c" * 64,
                run_id="qc_pc_observed_anchor", as_of=AS_OF, assets_root=root,
                sample_scan=lambda _path, _card_id: None, geometry_scan=geometry_pass)
            self.assertNotIn("price_anchor_source_method_mismatch", report["cards"][0]["blockers"])
            self.assertEqual(report["cards"][0]["facts"]["priceAnchor"]["asOf"], "2026-06-29T00:00:00Z")

    def test_other_source_history_does_not_block_a_valid_current_price(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "other-source-anchor.webp"
            asset.write_bytes(b"other-source-anchor-image")
            facts = facts_for(asset)
            facts["pricesByVariant"][1] = [
                row
                for row in facts["pricesByVariant"][1]
                if row["id"] in {1, 4}
            ]
            report = qc.audit_candidate(
                candidate(),
                facts,
                universe_hash="d" * 64,
                run_id="qc_other_source_anchor",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
            )
            card = report["cards"][0]
            self.assertNotIn(
                "price_anchor_source_method_mismatch",
                card["blockers"],
            )
            self.assertIn(
                "price_anchor_source_method_mismatch",
                card["warnings"],
            )
            self.assertEqual(report["status"], "passed")

    def test_current_price_accepts_one_fresh_exact_authoritative_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "single-source-price.webp"
            asset.write_bytes(b"single-source-price-image")
            facts = facts_for(asset)
            facts["pricesByVariant"][1] = [
                row
                for row in facts["pricesByVariant"][1]
                if row["source_code"] == "snk_psa10"
            ]
            report = qc.audit_candidate(
                candidate(),
                facts,
                universe_hash="3" * 64,
                run_id="qc_single_source_price",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
            )
            self.assertNotIn(
                "exact_psa10_price_insufficient_independent_sources",
                report["cards"][0]["blockers"],
            )
            cross_source = report["cards"][0]["facts"]["price"]["priorityMeta"][
                "crossSourceQc"
            ]
            self.assertEqual(cross_source["freshSourceCount"], 1)
            self.assertEqual(cross_source["requiredSources"], 1)
            self.assertEqual(cross_source["status"], "confirmed")

    def test_current_price_averages_authority_families_inside_two_times(self) -> None:
        facts = facts_for(Path("unused.webp"), image_hash="a" * 64)
        for row in facts["pricesByVariant"][1]:
            if (
                row["source_code"] == "ebay"
                and row["effective_at"] == AS_OF - timedelta(hours=1)
            ):
                row["price_usd"] = 120
        selected, meta = qc.select_display_exact_price(
            facts["pricesByVariant"][1],
            facts["sourceIdentitiesByVariant"][1],
            as_of=AS_OF,
        )
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(str(selected["price_usd"]), "110.000000")
        self.assertEqual(meta["mode"], "authority_family_mean")
        self.assertEqual(meta["authorityMeanSources"], ["ebay", "snk"])

    def test_current_price_rejects_exact_source_spread_over_two_times(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "price-spread.webp"
            asset.write_bytes(b"price-spread-image")
            facts = facts_for(asset)
            for row in facts["pricesByVariant"][1]:
                if (
                    row["source_code"] == "ebay"
                    and row["effective_at"] == AS_OF - timedelta(hours=1)
                ):
                    row["price_usd"] = 250
            report = qc.audit_candidate(
                candidate(),
                facts,
                universe_hash="4" * 64,
                run_id="qc_price_spread_gt_2x",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
            )
            self.assertIn(
                "exact_psa10_price_source_spread_gt_2x",
                report["cards"][0]["blockers"],
            )
            cross_source = report["cards"][0]["facts"]["price"]["priorityMeta"][
                "crossSourceQc"
            ]
            self.assertEqual(cross_source["freshSourceCount"], 2)
            self.assertEqual(cross_source["observedRatio"], "2.500000")

    def test_false_green_facts_are_release_blockers_not_repairs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "sample.webp"
            asset.write_bytes(b"sample-image")
            facts = facts_for(asset)
            card = candidate_card()
            card["populationAsOf"] = iso(AS_OF + timedelta(days=1))
            facts["pricesByVariant"][1] = [
                {
                    "variant_id": 1,
                    "id": 9,
                    "source_code": "snk_psa10",
                    "effective_at": AS_OF + timedelta(hours=1),
                    "price_usd": 100,
                    "source_priority": 10,
                    "metric_status": "ready",
                }
            ]
            for sale in facts["salesByVariant"][1]:
                sale["quantity"] = 2
            facts["indexByVariant"][1][0]["market_cap_usd"] = 1
            facts["imagesByVariant"][1][0].update(
                {
                    "semantic_match_status": "meta_unreviewed",
                    "public_allowed": 0,
                    "rejection_reason": "SAMPLE watermark",
                }
            )
            report = qc.audit_candidate(
                candidate([card]),
                facts,
                universe_hash="b" * 64,
                run_id="qc_fixture_blocked",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
            )
            blockers = report["cards"][0]["blockers"]
            for expected in (
                "population_future_dated",
                "future_price_metric",
                "exact_psa10_price_missing",
                "psa10_sales_30d_missing",
                "market_cap_formula_mismatch",
                "image_not_human_or_vision_confirmed",
                "image_sample_or_placeholder",
            ):
                self.assertIn(expected, blockers)
            self.assertEqual(report["status"], "blocked")
            self.assertFalse(report["releaseGate"]["eligible"])
            self.assertGreater(report["releaseGate"]["blockerCount"], 0)

    def test_legacy_image_qc_version_cannot_enter_release_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "card.webp"
            asset.write_bytes(b"card-image")
            facts = facts_for(asset)
            facts["imagesByVariant"][1][0]["qc_version"] = "human-review-v1"
            report = qc.audit_candidate(
                candidate(),
                facts,
                universe_hash="b" * 64,
                run_id="qc_legacy_image_review",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
            )
            self.assertIn(
                "image_qc_version_not_current",
                report["cards"][0]["blockers"],
            )

    def test_image_requires_exact_public_source_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "card.webp"
            asset.write_bytes(b"card-image")
            for field, value, expected in (
                ("source_path", None, "image_source_pointer_missing"),
                ("pointer_public_allowed", 0, "image_source_pointer_disabled"),
            ):
                with self.subTest(field=field):
                    facts = facts_for(asset)
                    facts["imagesByVariant"][1][0][field] = value
                    report = qc.audit_candidate(
                        candidate(),
                        facts,
                        universe_hash="b" * 64,
                        run_id=f"qc_pointer_{field}",
                        as_of=AS_OF,
                        assets_root=root,
                        sample_scan=lambda _path, _card_id: None,
                        geometry_scan=geometry_pass,
                    )
                    self.assertIn(expected, report["cards"][0]["blockers"])

    def test_image_review_binding_fails_after_printing_or_language_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "card.webp"
            asset.write_bytes(b"card-image")
            for field, value in (
                ("current_image_printing_sha256", "f" * 64),
                ("current_image_printing_language", "ja"),
                ("approval_binding_sha256", "0" * 64),
            ):
                with self.subTest(field=field):
                    facts = facts_for(asset)
                    facts["imagesByVariant"][1][0][field] = value
                    report = qc.audit_candidate(
                        candidate(),
                        facts,
                        universe_hash="b" * 64,
                        run_id=f"qc_binding_{field}",
                        as_of=AS_OF,
                        assets_root=root,
                        sample_scan=lambda _path, _card_id: None,
                        geometry_scan=geometry_pass,
                    )
                    self.assertIn(
                        "image_review_binding_drift",
                        report["cards"][0]["blockers"],
                    )

    def test_registered_human_rejection_is_a_final_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "card.webp"
            asset.write_bytes(b"card-image")
            facts = facts_for(asset)
            row = facts["imagesByVariant"][1][0]
            row["registered_rejection_content_sha256"] = row["content_sha256"]
            report = qc.audit_candidate(
                candidate(),
                facts,
                universe_hash="b" * 64,
                run_id="qc_registered_rejection",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
            )
            self.assertIn(
                "image_historically_human_rejected",
                report["cards"][0]["blockers"],
            )

    def test_known_sample_source_is_rejected_before_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "official-one-piece.webp"
            asset.write_bytes(b"official-sample-source")
            card = candidate_card(tcg="one-piece")
            facts = facts_for(asset, card=card)
            facts["imagesByVariant"][1][0]["source_path"] = (
                "https://asia-en.onepiece-cardgame.com/"
                "images/cardlist/card/OP01-001.png"
            )
            sample_scan = mock.Mock(return_value=None)
            report = qc.audit_candidate(
                candidate([card]),
                facts,
                universe_hash="b" * 64,
                run_id="qc_known_sample_source",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=sample_scan,
                geometry_scan=geometry_pass,
            )
            image = report["cards"][0]["facts"]["image"]
            self.assertIn(
                "image_source_known_sample",
                report["cards"][0]["blockers"],
            )
            self.assertEqual(image["sourcePolicy"]["status"], "reject")
            sample_scan.assert_not_called()

    def test_image_language_match_is_required_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "language.webp"
            asset.write_bytes(b"language-qc-image")
            for value in (False, None):
                with self.subTest(language_match=value):
                    facts = facts_for(asset)
                    if value is None:
                        del facts["imagesByVariant"][1][0]["language_match"]
                    else:
                        facts["imagesByVariant"][1][0]["language_match"] = value
                    report = qc.audit_candidate(
                        candidate(),
                        facts,
                        universe_hash="c" * 64,
                        run_id=f"qc_fixture_language_{value}",
                        as_of=AS_OF,
                        assets_root=root,
                        sample_scan=lambda _path, _card_id: None,
                        geometry_scan=geometry_pass,
                    )
                    self.assertIn(
                        "image_language_match_failed",
                        report["cards"][0]["blockers"],
                    )
                    self.assertFalse(report["releaseGate"]["eligible"])

    def test_sale_evidence_mutations_are_release_blockers(self) -> None:
        cases = (
            ("timestamp_quality", "unparsed", "sale_timestamp_quality_invalid"),
            ("coverage_status", "quarantined", "sale_coverage_status_invalid"),
            ("source_payload_sha256", "not-a-sha256", "sale_payload_hash_invalid"),
            ("external_entity_id", "999999", "sale_source_identity_not_exact"),
            ("transaction_value_usd", 100.001, "psa10_sale_value_invalid"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "sale-qc.webp"
            asset.write_bytes(b"verified-sale-qc-image")
            for field, value, expected in cases:
                with self.subTest(field=field):
                    facts = facts_for(asset)
                    for sale in facts["salesByVariant"][1]:
                        sale[field] = value
                    report = qc.audit_candidate(
                        candidate(),
                        facts,
                        universe_hash="d" * 64,
                        run_id=f"qc_fixture_sale_{field}",
                        as_of=AS_OF,
                        assets_root=root,
                        sample_scan=lambda _path, _card_id: None,
                        geometry_scan=geometry_pass,
                    )
                    blockers = report["cards"][0]["blockers"]
                    self.assertIn(expected, blockers)
                    self.assertIn("psa10_sales_30d_missing", blockers)
                    self.assertEqual(
                        report["cards"][0]["facts"]["sales30d"]["purePsa10Count"],
                        0,
                    )
                    self.assertFalse(report["releaseGate"]["eligible"])

    def test_live_sale_quality_contracts_remain_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "sale-quality.webp"
            asset.write_bytes(b"verified-live-sale-quality-image")
            cases = [
                ("timestamp_quality", value)
                for value in sorted(qc.ACCEPTED_SALE_TIMESTAMP_QUALITIES)
            ] + [
                ("coverage_status", value)
                for value in sorted(qc.ACCEPTED_SALE_COVERAGE_STATUSES)
            ]
            for field, value in cases:
                with self.subTest(field=field, value=value):
                    facts = facts_for(asset)
                    for sale in facts["salesByVariant"][1]:
                        sale[field] = value
                    report = qc.audit_candidate(
                        candidate(),
                        facts,
                        universe_hash="e" * 64,
                        run_id=f"qc_fixture_sale_{field}_{value}",
                        as_of=AS_OF,
                        assets_root=root,
                        sample_scan=lambda _path, _card_id: None,
                        geometry_scan=geometry_pass,
                    )
                    self.assertEqual(report["status"], "passed")
                    self.assertEqual(
                        report["cards"][0]["facts"]["sales30d"]["purePsa10Count"],
                        10,
                    )

    def test_nine_valid_sales_are_low_liquidity_and_ten_pass(self) -> None:
        self.assertEqual(qc.MIN_PURE_PSA10_SALES_30D, 10)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "liquidity.webp"
            asset.write_bytes(b"verified-liquidity-image")
            nine = facts_for(asset)
            nine["salesByVariant"][1] = nine["salesByVariant"][1][:9]
            nine_report = qc.audit_candidate(
                candidate(),
                nine,
                universe_hash="f" * 64,
                run_id="qc_fixture_nine_sales",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
            )
            self.assertIn(
                "psa10_sales_30d_insufficient",
                nine_report["cards"][0]["blockers"],
            )
            self.assertFalse(nine_report["releaseGate"]["eligible"])

            ten_report = qc.audit_candidate(
                candidate(),
                facts_for(asset),
                universe_hash="0" * 64,
                run_id="qc_fixture_ten_sales",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
            )
            self.assertEqual(ten_report["status"], "passed")

    def test_unbound_sale_does_not_block_card_with_ten_exact_sales(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "unbound-extra.webp"
            asset.write_bytes(b"verified-unbound-extra-image")
            facts = facts_for(asset)
            unbound = copy.deepcopy(facts["salesByVariant"][1][0])
            unbound["external_entity_id"] = "999999"
            facts["salesByVariant"][1].append(unbound)

            report = qc.audit_candidate(
                candidate(),
                facts,
                universe_hash="1" * 64,
                run_id="qc_fixture_ten_exact_one_unbound",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
            )
            card = report["cards"][0]
            self.assertEqual(card["facts"]["sales30d"]["purePsa10Count"], 10)
            self.assertEqual(card["facts"]["sales30d"]["sourceIdentityRowsExcluded"], 1)
            self.assertNotIn("sale_source_identity_not_exact", card["blockers"])
            self.assertIn("sale_source_identity_not_exact", card["warnings"])
            self.assertEqual(report["status"], "passed")

    def test_unbound_sale_blocks_card_when_nine_exact_sales_remain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "unbound-short.webp"
            asset.write_bytes(b"verified-unbound-short-image")
            facts = facts_for(asset)
            facts["salesByVariant"][1] = facts["salesByVariant"][1][:9]
            unbound = copy.deepcopy(facts["salesByVariant"][1][0])
            unbound["external_entity_id"] = "999999"
            facts["salesByVariant"][1].append(unbound)

            report = qc.audit_candidate(
                candidate(),
                facts,
                universe_hash="2" * 64,
                run_id="qc_fixture_nine_exact_one_unbound",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
            )
            card = report["cards"][0]
            self.assertEqual(card["facts"]["sales30d"]["purePsa10Count"], 9)
            self.assertEqual(card["facts"]["sales30d"]["sourceIdentityRowsExcluded"], 1)
            self.assertIn("psa10_sales_30d_insufficient", card["blockers"])
            self.assertIn("sale_source_identity_not_exact", card["blockers"])
            self.assertFalse(report["releaseGate"]["eligible"])

    def test_sale_source_binding_is_provider_aware(self) -> None:
        ebay_id = "02c08f51-d76c-4aca-81f5-67c462bba310"
        source_rows = [
            {
                "source_code": "snkrdunk",
                "external_entity_id": "001234",
                "match_status": "exact",
            },
            {
                "source_code": "ebay",
                "external_entity_id": ebay_id.upper(),
                "match_status": "exact",
            },
        ]
        self.assertTrue(
            qc.sale_source_bound(
                source_rows,
                {"source_code": "snk_grade", "external_entity_id": "1234"},
            )
        )
        self.assertTrue(
            qc.sale_source_bound(
                [
                    {
                        "source_code": "snk_psa10",
                        "external_entity_id": "001234",
                        "match_status": "exact",
                    }
                ],
                {"source_code": "snkrdunk", "external_entity_id": "1234"},
            )
        )
        self.assertTrue(
            qc.sale_source_bound(
                source_rows,
                {"source_code": "ebay", "external_entity_id": ebay_id},
            )
        )
        self.assertFalse(
            qc.sale_source_bound(
                source_rows,
                {"source_code": "ebay", "external_entity_id": "pc:1234"},
            )
        )
        self.assertFalse(
            qc.sale_source_bound(
                source_rows,
                {
                    "source_code": "ebay",
                    "external_entity_id": "06a5a75e-f5d8-42dd-95b9-0a76ee9f3def",
                },
            )
        )

    def test_exact_pricecharting_ebay_sales_have_same_gate_as_snk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "pc-sales.webp"
            asset.write_bytes(b"verified-pc-sales-image")
            facts = facts_for(asset)
            facts["sourceIdentitiesByVariant"][1].append(
                {
                    "variant_id": 1,
                    "source_code": "pricecharting",
                    "external_entity_id": "123",
                    "match_status": "exact",
                    "evidence_sha256": "5" * 64,
                }
            )
            for sale in facts["salesByVariant"][1]:
                sale["source_code"] = "ebay"
                sale["external_entity_id"] = "pc:123"

            accepted = qc.audit_candidate(
                candidate(),
                facts,
                universe_hash="1" * 64,
                run_id="qc_fixture_pc_sales_exact",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
            )
            self.assertNotIn(
                "sale_source_identity_not_exact",
                accepted["cards"][0]["blockers"],
            )
            self.assertEqual(
                accepted["cards"][0]["facts"]["sales30d"]["purePsa10Count"],
                10,
            )

            for sale in facts["salesByVariant"][1]:
                sale["external_entity_id"] = "pc:999"
            rejected = qc.audit_candidate(
                candidate(),
                facts,
                universe_hash="2" * 64,
                run_id="qc_fixture_pc_sales_wrong_product",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
            )
            self.assertIn(
                "sale_source_identity_not_exact",
                rejected["cards"][0]["blockers"],
            )
            self.assertIn(
                "psa10_sales_30d_missing",
                rejected["cards"][0]["blockers"],
            )

    def test_exact_grade10_ebay_sales_have_same_gate_as_snk_and_pc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "g10-sales.webp"
            asset.write_bytes(b"verified-g10-sales-image")
            facts = facts_for(asset)
            snk_id = next(
                row["external_entity_id"]
                for row in facts["sourceIdentitiesByVariant"][1]
                if row["source_code"] == "snkrdunk"
            )
            for sale in facts["salesByVariant"][1]:
                sale["source_code"] = "ebay"
                sale["external_entity_id"] = f"0{snk_id}"

            accepted = qc.audit_candidate(
                candidate(),
                facts,
                universe_hash="3" * 64,
                run_id="qc_fixture_g10_sales_exact",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
            )
            self.assertNotIn(
                "sale_source_identity_not_exact",
                accepted["cards"][0]["blockers"],
            )
            self.assertEqual(
                accepted["cards"][0]["facts"]["sales30d"]["purePsa10Count"],
                10,
            )

    def test_cross_tcg_shared_image_and_duplicate_printing_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "shared.webp"
            asset.write_bytes(b"same-image-for-two-cards")
            shared_hash = hashlib.sha256(asset.read_bytes()).hexdigest()
            first = candidate_card("cmc_pokemon", tcg="pokemon", gemrate_id="a" * 40)
            second = candidate_card(
                "cmc_one_piece",
                tcg="one-piece",
                gemrate_id="b" * 40,
            )
            first_facts = facts_for(
                asset,
                card=first,
                variant_id=1,
                image_hash=shared_hash,
                market_rank=1,
            )
            second_facts = facts_for(
                asset,
                card=second,
                variant_id=2,
                image_hash=shared_hash,
                market_rank=2,
            )
            report = qc.audit_candidate(
                candidate([first, second]),
                merge_facts(first_facts, second_facts),
                universe_hash="c" * 64,
                run_id="qc_fixture_duplicate",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
            )
            self.assertEqual(report["duplicateImageGroups"], 1)
            self.assertTrue(
                all(
                    "image_cross_tcg_duplicate" in row["blockers"]
                    for row in report["cards"]
                )
            )
            self.assertEqual(
                report["reviewQueues"]["imageDuplicate"]["topRankedCount"],
                2,
            )
            self.assertEqual(
                report["reviewQueues"]["imageDuplicate"]["slaPolicy"],
                {"topRanked": "24h", "other": "3_business_days"},
            )
            self.assertTrue(
                all(
                    row["sla"] == "24h"
                    for row in report["reviewQueues"]["imageDuplicate"]["cards"]
                )
            )

    def test_monitoring_gaps_are_queued_but_do_not_false_block_qualified_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "one.webp"
            asset.write_bytes(b"verified-card-image")
            monitoring = candidate_card(
                "cmc_monitoring",
                gemrate_id="c" * 40,
                population=980,
            )
            document = candidate(monitoring=[monitoring])
            report = qc.audit_candidate(
                document,
                facts_for(asset),
                universe_hash="d" * 64,
                run_id="qc_fixture_monitoring",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
            )
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["counts"]["releaseReadyQualified"], 1)
            monitoring_row = next(
                row for row in report["cards"] if row["segment"] == "monitoring"
            )
            self.assertEqual(monitoring_row["decision"], "monitoring")
            self.assertIn("catalog_identity_missing", monitoring_row["blockers"])
            self.assertGreater(report["counts"]["reviewQueueCards"], 0)

    def test_same_run_id_cannot_overwrite_different_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "one.webp"
            asset.write_bytes(b"verified-card-image")
            report = qc.audit_candidate(
                candidate(),
                facts_for(asset),
                universe_hash="e" * 64,
                run_id="qc_fixture_immutable",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
            )
            qc.write_report_and_receipt(report, root / "reports")
            changed = copy.deepcopy(report)
            changed["status"] = "blocked"
            with self.assertRaisesRegex(
                qc.CanonicalDbQcError,
                "immutable QC evidence",
            ):
                qc.write_report_and_receipt(changed, root / "reports")

    def test_query_helper_rejects_mutating_sql_before_cursor_use(self) -> None:
        class NoConnection:
            def cursor(self):  # pragma: no cover - must never be reached
                raise AssertionError("cursor should not be opened")

        with self.assertRaisesRegex(qc.CanonicalDbQcError, "non-read-only"):
            qc._fetch(NoConnection(), "UPDATE catalog_variant SET canonical_name='x'")

    def test_qc_indexes_bind_the_matching_formal_active_lock(self) -> None:
        lock_hash = "a" * 64
        evaluations = [
            {
                "id": 92,
                "publish_gate_status": "pending",
                "universe_lock_id": 23,
                "member_count": 994,
            },
            {
                "id": 91,
                "publish_gate_status": "pending",
                "universe_lock_id": 26,
                "member_count": 3,
            },
        ]
        members = [{"variant_id": 1}, {"variant_id": 2}, {"variant_id": 3}]
        with mock.patch.object(qc, "_fetch", side_effect=[evaluations, members]):
            self.assertEqual(
                qc.matching_qc_index_evaluation_id(
                    object(),
                    lock_hash,
                    [1, 2, 3],
                ),
                91,
            )

    def test_qc_indexes_reject_formal_lock_member_drift(self) -> None:
        evaluations = [{"id": 91, "publish_gate_status": "pending", "universe_lock_id": 26, "member_count": 3}]
        drifted_members = [{"variant_id": 1}, {"variant_id": 2}, {"variant_id": 4}]
        with mock.patch.object(qc, "_fetch", side_effect=[evaluations, drifted_members]):
            self.assertIsNone(
                qc.matching_qc_index_evaluation_id(
                    object(), "a" * 64, [1, 2, 3]
                )
            )

    def test_qc_indexes_reject_invalid_formal_lock_hash_before_query(self) -> None:
        with mock.patch.object(qc, "_fetch") as fetch:
            self.assertIsNone(
                qc.matching_qc_index_evaluation_id(object(), "not-a-lock-hash", [1])
            )
        fetch.assert_not_called()

    def test_database_snapshot_explicitly_sets_read_only_transaction(self) -> None:
        statements: list[str] = []

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql):
                statements.append(sql)

            def fetchone(self):
                return {"database_name": "cardz_market_cap"}

        class Connection:
            def __init__(self):
                self.autocommit_value = None

            def autocommit(self, value):
                self.autocommit_value = value

            def cursor(self):
                return Cursor()

        connection = Connection()
        qc.begin_read_only_snapshot(connection)
        self.assertFalse(connection.autocommit_value)
        self.assertEqual(
            statements,
            [
                "SET SESSION TRANSACTION READ ONLY",
                "START TRANSACTION WITH CONSISTENT SNAPSHOT",
                "SELECT DATABASE() AS database_name",
            ],
        )

    def test_relaxed_identity_allows_one_or_many_exact_routes_but_blocks_explicit_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "one.webp"
            asset.write_bytes(b"release-image")
            facts = facts_for(asset)
            catalog = dict(facts["catalogByOpaque"]["cmc_one"])
            catalog["printing_identity_status"] = "candidate"
            policy = qc.load_release_profile(
                qc.load_registry(ROOT / "config" / "data-routing.json"),
                "relaxed-launch-v1",
            )["policy"]
            card = candidate_card()

            status, _key, _evidence, blockers = qc.release_identity(
                catalog, [], card, policy
            )
            self.assertIsNone(status)
            self.assertIn("identity_authority_exact_binding_insufficient", blockers)

            exact = [
                {
                    "source_code": "snkrdunk",
                    "external_entity_id": "100001",
                    "match_status": "exact",
                    "evidence_sha256": "a" * 64,
                }
            ]
            status, _key, evidence, blockers = qc.release_identity(
                catalog, exact, card, policy
            )
            self.assertEqual(status, "provisional")
            self.assertEqual(blockers, [])
            self.assertEqual(evidence["exactAuthorityBindings"], {"snk": ["100001"]})

            multi_route = [
                *exact,
                {
                    "source_code": "snk",
                    "external_entity_id": "100002",
                    "match_status": "exact",
                    "evidence_sha256": "b" * 64,
                },
            ]
            status, _key, _evidence, blockers = qc.release_identity(
                catalog, multi_route, card, policy
            )
            self.assertEqual(status, "provisional")
            self.assertNotIn("identity_authority_conflict", blockers)

            explicit_conflict = [
                *exact,
                {
                    "source_code": "snk",
                    "external_entity_id": "999999",
                    "match_status": "conflict",
                    "evidence_sha256": "c" * 64,
                },
            ]
            status, _key, _evidence, blockers = qc.release_identity(
                catalog, explicit_conflict, card, policy
            )
            self.assertIsNone(status)
            self.assertIn("identity_authority_conflict", blockers)

    def test_relaxed_sales_price_and_spread_policy_matrix(self) -> None:
        release = qc.load_release_profile(
            qc.load_registry(ROOT / "config" / "data-routing.json"),
            "relaxed-launch-v1",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "one.webp"
            asset.write_bytes(b"release-image")

            five_sales = facts_for(asset)
            five_sales["salesByVariant"][1] = five_sales["salesByVariant"][1][:5]
            passed = qc.audit_candidate(
                candidate(),
                five_sales,
                universe_hash="f" * 64,
                run_id="qc_relaxed_five_sales",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
                release_profile=release,
            )
            self.assertNotIn("psa10_sales_30d_insufficient", passed["cards"][0]["blockers"])
            self.assertEqual(passed["cards"][0]["facts"]["sales30d"]["purePsa10Count"], 5)

            four_sales = facts_for(asset)
            four_sales["salesByVariant"][1] = four_sales["salesByVariant"][1][:4]
            blocked = qc.audit_candidate(
                candidate(),
                four_sales,
                universe_hash="e" * 64,
                run_id="qc_relaxed_four_sales",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
                release_profile=release,
            )
            self.assertIn("psa10_sales_30d_insufficient", blocked["cards"][0]["blockers"])

            spread = facts_for(asset)
            for row in spread["pricesByVariant"][1]:
                if row["source_code"] == "ebay" and row["effective_at"] == AS_OF - timedelta(hours=1):
                    row["price_usd"] = 250
            warning = qc.audit_candidate(
                candidate(),
                spread,
                universe_hash="d" * 64,
                run_id="qc_relaxed_price_spread",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
                release_profile=release,
            )
            self.assertNotIn("exact_psa10_price_source_spread_gt_2x", warning["cards"][0]["blockers"])
            self.assertIn("exact_psa10_price_source_spread_gt_2x", warning["cards"][0]["warnings"])

            stale = facts_for(asset)
            stale["pricesByVariant"][1] = [
                {
                    **row,
                    "effective_at": AS_OF - timedelta(hours=721),
                    "observed_date": (AS_OF - timedelta(hours=721)).date(),
                }
                for row in stale["pricesByVariant"][1]
            ]
            stale_report = qc.audit_candidate(
                candidate(),
                stale,
                universe_hash="c" * 64,
                run_id="qc_relaxed_price_stale",
                as_of=AS_OF,
                assets_root=root,
                sample_scan=lambda _path, _card_id: None,
                geometry_scan=geometry_pass,
                release_profile=release,
            )
            self.assertIn("exact_psa10_price_stale", stale_report["cards"][0]["blockers"])


if __name__ == "__main__":
    unittest.main()
