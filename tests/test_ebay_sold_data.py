from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from ebay_sold_data import (  # noqa: E402
    TERMINAL_READY,
    TERMINAL_SOURCE_IDENTITY_NOT_EXACT,
    TERMINAL_UNAVAILABLE,
    TERMINAL_VERIFIED_NONE,
    apply_snk_harvest_fill,
    build_coverage_receipt,
    build_query,
    classify_card_sales,
    classify_listing,
    classify_qc_cohort,
    classify_qc_cohort_card,
    collect_from_input,
    count_snk_window_trades,
    dedupe_transactions,
    fill_classify_qc_cohort,
    liquidity_band,
    normalize_transaction,
    reference_fallback,
)

CARD = {
    "name": "Shanks Manga Alternate Art",
    "tcg": "one-piece",
    "collectorNumber": "OP01-120",
    "language": "ja",
    "edition": "Manga Alternate",
    "parallel": "Manga",
    "finish": "Foil",
}


class EbaySoldDataTests(unittest.TestCase):
    def listing(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "sold": True,
            "listing_status": "completed_sold",
            "title": "2022 One Piece OP01-120 Shanks Manga Alternate Art Japanese PSA 10 Foil",
            "url": "https://www.ebay.com/itm/123456789",
            "sold_date": "Jul 22, 2026",
            "price_amount": 2030,
            "currency": "USD",
            "language": "Japanese",
            "tcg": "One Piece",
            "edition": "Manga Alternate",
            "parallel": "Manga",
            "finish": "Foil",
            "quantity": 1,
        }
        payload.update(overrides)
        return payload

    def test_query_contains_exact_identity_and_grade(self) -> None:
        self.assertEqual(build_query(CARD), "Shanks Manga Alternate Art OP01-120 Japanese PSA 10")

    def test_accepts_exact_psa10_sold_transaction_with_full_identity(self) -> None:
        row = normalize_transaction(
            CARD,
            self.listing(),
            fetched_at="2026-07-23T12:00:00Z",
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["unitPrice"], 2030)
        self.assertEqual(row["transactionValue"], 2030)
        self.assertEqual(row["soldDate"], "2026-07-22")
        self.assertEqual(row["fetchedAt"], "2026-07-23T12:00:00Z")
        self.assertFalse(row["isBundle"])

    def test_rejects_identity_or_grade_mismatch(self) -> None:
        for overrides in (
            {"title": "One Piece OP01-120 Shanks raw card PSA 10 quality"},
            {"title": "One Piece OP01-121 Shanks Manga Alternate Art Japanese PSA 10 Foil"},
            {"title": "One Piece OP01-120 Shanks Manga Alternate Art Japanese PSA 9 Foil"},
            {"language": "English"},
            {"parallel": "Alternate Art"},
            {"listing_status": "active"},
        ):
            self.assertIsNone(normalize_transaction(CARD, self.listing(**overrides)))

    def test_classify_listing_exposes_bundle_grade_printing_reasons(self) -> None:
        grade = classify_listing(
            CARD,
            self.listing(title="One Piece OP01-120 Shanks Manga Alternate Art Japanese PSA 9 Foil"),
        )
        self.assertEqual(grade["decision"], "rejected")
        self.assertIn("grade_mismatch", grade["reasons"])

        printing = classify_listing(CARD, self.listing(parallel="Alternate Art"))
        self.assertEqual(printing["decision"], "rejected")
        self.assertIn("parallel_mismatch", printing["reasons"])

        bundle = classify_listing(
            CARD,
            self.listing(
                title="One Piece OP01-120 Shanks Manga Japanese PSA 10 lot of cards Foil",
                quantity=1,
            ),
        )
        self.assertEqual(bundle["decision"], "rejected")
        self.assertIn("bundle_quantity_missing", bundle["reasons"])

    def test_bundle_preserves_total_quantity_and_unit_value(self) -> None:
        row = normalize_transaction(
            CARD,
            self.listing(url="https://www.ebay.com/itm/222", price_amount=6000, quantity=3),
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["quantity"], 3)
        self.assertEqual(row["transactionValue"], 6000)
        self.assertEqual(row["unitPrice"], 2000)
        self.assertTrue(row["isBundle"])

    def test_same_day_same_price_sales_keep_distinct_listing_ids(self) -> None:
        one = normalize_transaction(CARD, self.listing(url="https://www.ebay.com/itm/1"))
        two = normalize_transaction(CARD, self.listing(url="https://www.ebay.com/itm/2"))
        self.assertIsNotNone(one)
        self.assertIsNotNone(two)
        assert one is not None and two is not None
        self.assertNotEqual(one["transactionId"], two["transactionId"])

    def test_dedupe_collapses_duplicate_transaction_ids(self) -> None:
        one = normalize_transaction(CARD, self.listing(url="https://www.ebay.com/itm/1"))
        assert one is not None
        duplicate = {**one, "unitPrice": 9999}
        deduped = dedupe_transactions([one, one, duplicate])
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["unitPrice"], 9999)

    def test_reference_fallback_requires_three_exact_non_bundle_sales_in_30_days(self) -> None:
        as_of = datetime(2026, 7, 24, tzinfo=timezone.utc)
        transactions = [
            normalize_transaction(CARD, self.listing(url=f"https://www.ebay.com/itm/{index}", sold_date=f"Jul {20 + index}, 2026", price_amount=2000 + index))
            for index in range(3)
        ]
        assert all(transaction is not None for transaction in transactions)
        eligible = reference_fallback([transaction for transaction in transactions if transaction], as_of)
        self.assertEqual(eligible["status"], "ready")
        self.assertEqual(eligible["salesCount"], 3)
        self.assertEqual(eligible["referencePriceUsd"], 2001)

        unavailable = reference_fallback([transactions[0]], as_of)
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertIsNone(unavailable["referencePriceUsd"])

        bundle = normalize_transaction(CARD, self.listing(url="https://www.ebay.com/itm/bundle", price_amount=6000, quantity=3))
        assert bundle is not None
        only_bundle = reference_fallback([bundle, *transactions[:2]], as_of)
        self.assertEqual(only_bundle["status"], "unavailable")

    def test_input_adapter_keeps_unmatched_cards_unavailable(self) -> None:
        card = {**CARD, "canonicalSourceCode": "snkrdunk", "canonicalExternalId": "42", "pokedexId": "cmc_test"}
        rows = [
            {
                "sourceCode": "snkrdunk",
                "externalEntityId": "42",
                "fetchedAt": "2026-07-23T12:00:00Z",
                "listings": [self.listing(url="https://www.ebay.com/itm/42")],
            }
        ]
        result = collect_from_input(
            [card, {**card, "canonicalExternalId": "missing"}],
            rows,
            as_of=datetime(2026, 7, 24, tzinfo=timezone.utc),
        )
        self.assertEqual(result[0]["status"], "unavailable")
        self.assertEqual(result[0]["transactions"][0]["fetchedAt"], "2026-07-23T12:00:00Z")
        self.assertEqual(result[0]["classification"]["terminalState"], TERMINAL_UNAVAILABLE)
        self.assertEqual(result[1]["status"], "unavailable")
        self.assertEqual(result[1]["classification"]["terminalState"], TERMINAL_UNAVAILABLE)
        self.assertEqual(result[1]["referenceFallback"]["status"], "unavailable")
        self.assertIsNone(result[1]["coverageReceipt"])
        self.assertFalse(result[1]["classification"]["noSalesClaim"]["claimed"])

    def test_empty_source_row_with_coverage_is_verified_none(self) -> None:
        card = {**CARD, "canonicalSourceCode": "snkrdunk", "canonicalExternalId": "empty", "pokedexId": "cmc_empty"}
        rows = [
            {
                "sourceCode": "snkrdunk",
                "externalEntityId": "empty",
                "fetchedAt": "2026-07-23T12:00:00Z",
                "listings": [],
            }
        ]
        result = collect_from_input([card], rows, as_of=datetime(2026, 7, 24, tzinfo=timezone.utc), recheck_days=7)
        self.assertEqual(result[0]["status"], "verified_none")
        self.assertEqual(result[0]["classification"]["terminalState"], TERMINAL_VERIFIED_NONE)
        self.assertEqual(result[0]["classification"]["liquidity"], "none")
        claim = result[0]["classification"]["noSalesClaim"]
        self.assertTrue(claim["claimed"])
        self.assertEqual(claim["windowStart"], "2026-06-24")
        self.assertEqual(claim["windowEnd"], "2026-07-24")
        self.assertEqual(claim["recheckAt"], "2026-07-31T00:00:00Z")
        self.assertEqual(claim["coverageFetchedAt"], "2026-07-23T12:00:00Z")
        receipt = result[0]["coverageReceipt"]
        assert receipt is not None
        self.assertEqual(receipt["listingCountObserved"], 0)
        self.assertEqual(receipt["acceptedCount"], 0)

    def test_verified_none_forbidden_without_complete_coverage_fields(self) -> None:
        incomplete = {
            "windowStart": "2026-06-24",
            "windowEnd": "2026-07-24",
            # missing recheckAt + fetchedAt
        }
        classification = classify_card_sales(
            accepted=[],
            rejected_reasons={},
            coverage_receipt=incomplete,
            as_of=datetime(2026, 7, 24, tzinfo=timezone.utc),
        )
        self.assertEqual(classification["terminalState"], TERMINAL_UNAVAILABLE)
        self.assertFalse(classification["noSalesClaim"]["claimed"])

    def test_liquidity_bands(self) -> None:
        self.assertEqual(liquidity_band(0), "none")
        self.assertEqual(liquidity_band(1), "low")
        self.assertEqual(liquidity_band(3), "medium")
        self.assertEqual(liquidity_band(10), "high")

    def test_coverage_receipt_binds_window_and_recheck(self) -> None:
        receipt = build_coverage_receipt(
            source_code="ebay",
            external_entity_id="1",
            fetched_at="2026-07-23T12:00:00Z",
            as_of=datetime(2026, 7, 24, tzinfo=timezone.utc),
            listing_count_observed=2,
            accepted_count=0,
            rejected_count=2,
            rejection_reasons={"grade_mismatch": 2},
        )
        self.assertEqual(receipt["windowStart"], "2026-06-24")
        self.assertEqual(receipt["windowEnd"], "2026-07-24")
        self.assertEqual(receipt["recheckAt"], "2026-07-31T00:00:00Z")
        self.assertTrue(receipt["missingIsNotZero"])

    def test_qc_cohort_all_terminal_and_no_fake_verified_none(self) -> None:
        report = {
            "asOf": "2026-07-29T09:02:59Z",
            "cards": [
                {
                    "id": "cmc_ready",
                    "variantId": 1,
                    "tcg": "pokemon",
                    "marketRank": 1,
                    "segment": "qualified",
                    "blockers": [],
                    "warnings": ["bundle_sale_excluded"],
                    "facts": {
                        "sales30d": {
                            "purePsa10Count": 4,
                            "bundleRowsExcluded": 1,
                            "otherGradeRowsExcluded": 0,
                            "sources": ["ebay"],
                        }
                    },
                },
                {
                    "id": "cmc_missing",
                    "variantId": 2,
                    "tcg": "pokemon",
                    "marketRank": 2,
                    "segment": "qualified",
                    "blockers": ["psa10_sales_30d_missing"],
                    "warnings": ["non_psa10_sale_excluded"],
                    "facts": {
                        "sales30d": {
                            "purePsa10Count": 0,
                            "bundleRowsExcluded": 0,
                            "otherGradeRowsExcluded": 3,
                            "sources": [],
                        }
                    },
                },
                {
                    "id": "cmc_identity",
                    "variantId": 3,
                    "tcg": "one-piece",
                    "marketRank": 3,
                    "segment": "qualified",
                    "blockers": ["sale_source_identity_not_exact", "psa10_sales_30d_missing"],
                    "warnings": [],
                    "facts": {
                        "sales30d": {
                            "purePsa10Count": 0,
                            "bundleRowsExcluded": 0,
                            "otherGradeRowsExcluded": 0,
                            "sources": [],
                        }
                    },
                },
            ],
        }
        result = classify_qc_cohort(report, expected_count=3)
        states = {row["cardId"]: row["terminalState"] for row in result["shards"]}
        self.assertEqual(states["cmc_ready"], TERMINAL_UNAVAILABLE)
        self.assertEqual(states["cmc_missing"], TERMINAL_UNAVAILABLE)
        self.assertEqual(states["cmc_identity"], TERMINAL_SOURCE_IDENTITY_NOT_EXACT)
        self.assertEqual(result["summary"]["verifiedNoneCount"], 0)
        self.assertTrue(result["summary"]["allTerminal"])
        self.assertFalse(result["summary"]["acceptance"]["fake_verified_none_without_coverage"])
        missing = classify_qc_cohort_card(report["cards"][1], as_of=datetime(2026, 7, 29, 9, 2, 59, tzinfo=timezone.utc))
        self.assertIn("coverage_evidence_missing", missing["reasonCodes"])
        self.assertEqual(missing["rejectionReasons"].get("grade_mismatch"), 3)

    def test_snk_window_rejects_bundle_labels_and_counts_pure(self) -> None:
        harvest = {
            "fetched_at": "2026-07-28T12:00:00Z",
            "recent_trades": [
                {"label": "1枚", "title": "PSA10", "soldAt": "2026-07-20T00:00:00Z", "price": 1000},
                {"label": "2枚", "title": "PSA10", "soldAt": "2026-07-21T00:00:00Z", "price": 2000},
                {"label": "1枚", "title": "PSA10", "soldAt": "2026-05-01T00:00:00Z", "price": 900},
            ],
            "daily_activity": {"2026-07-20": {"count": 1, "value_jpy": 1000}},
        }
        window = count_snk_window_trades(harvest, as_of=datetime(2026, 7, 29, tzinfo=timezone.utc))
        self.assertEqual(window["purePsa10Count"], 1)
        self.assertEqual(window["bundleRowsExcluded"], 1)
        self.assertEqual(window["method"], "recent_trades_pure")

    def test_snk_empty_harvest_promotes_verified_none_with_coverage(self) -> None:
        base = classify_qc_cohort_card(
            {
                "id": "cmc_empty_fill",
                "variantId": 9,
                "tcg": "pokemon",
                "marketRank": 9,
                "segment": "qualified",
                "blockers": ["psa10_sales_30d_missing"],
                "warnings": [],
                "facts": {
                    "sales30d": {
                        "purePsa10Count": 0,
                        "bundleRowsExcluded": 0,
                        "otherGradeRowsExcluded": 0,
                        "sources": [],
                    }
                },
            },
            as_of=datetime(2026, 7, 29, 9, 2, 59, tzinfo=timezone.utc),
        )
        self.assertEqual(base["terminalState"], TERMINAL_UNAVAILABLE)
        filled = apply_snk_harvest_fill(
            base,
            snk_item_id="12345",
            harvest={
                "fetched_at": "2026-07-28T18:00:00Z",
                "recent_trades": [],
                "daily_activity": {},
                "_harvestPath": "fixture.jsonl",
            },
            as_of=datetime(2026, 7, 29, 9, 2, 59, tzinfo=timezone.utc),
        )
        self.assertEqual(filled["terminalState"], TERMINAL_VERIFIED_NONE)
        self.assertEqual(filled["liquidity"], "none")
        self.assertTrue(filled["classification"]["noSalesClaim"]["claimed"])
        self.assertEqual(filled["coverageReceipt"]["fetchedAt"], "2026-07-28T18:00:00Z")
        self.assertEqual(filled["fill"]["promotion"], "harvest_verified_none")

    def test_snk_harvest_sales_promote_ready_and_reject_identity_blocker(self) -> None:
        base = classify_qc_cohort_card(
            {
                "id": "cmc_ident_fill",
                "variantId": 10,
                "tcg": "one-piece",
                "marketRank": 10,
                "segment": "qualified",
                "blockers": ["sale_source_identity_not_exact", "psa10_sales_30d_missing"],
                "warnings": [],
                "facts": {
                    "sales30d": {
                        "purePsa10Count": 0,
                        "bundleRowsExcluded": 0,
                        "otherGradeRowsExcluded": 0,
                        "sources": [],
                    }
                },
            },
            as_of=datetime(2026, 7, 29, 9, 2, 59, tzinfo=timezone.utc),
        )
        self.assertEqual(base["terminalState"], TERMINAL_SOURCE_IDENTITY_NOT_EXACT)
        filled = apply_snk_harvest_fill(
            base,
            snk_item_id="999",
            harvest={
                "fetched_at": "2026-07-28T18:00:00Z",
                "recent_trades": [
                    {"label": "1枚", "title": "PSA10", "soldAt": "2026-07-15T00:00:00Z", "price": 5000},
                    {"label": "1枚", "title": "PSA10", "soldAt": "2026-07-16T00:00:00Z", "price": 5100},
                    {"label": "1枚", "title": "PSA10", "soldAt": "2026-07-17T00:00:00Z", "price": 5200},
                ],
                "daily_activity": {},
            },
            as_of=datetime(2026, 7, 29, tzinfo=timezone.utc),
        )
        self.assertEqual(filled["terminalState"], TERMINAL_UNAVAILABLE)
        self.assertEqual(filled["purePsa10Count"], 3)
        self.assertEqual(filled["liquidity"], "medium")
        self.assertEqual(filled["fill"]["promotion"], "harvest_ready")

    def test_fill_classify_does_not_fake_verified_none_without_harvest(self) -> None:
        report = {
            "asOf": "2026-07-29T09:02:59Z",
            "cards": [
                {
                    "id": "cmc_no_harvest",
                    "variantId": 1,
                    "tcg": "pokemon",
                    "marketRank": 1,
                    "segment": "qualified",
                    "blockers": ["psa10_sales_30d_missing"],
                    "warnings": [],
                    "facts": {
                        "sales30d": {
                            "purePsa10Count": 0,
                            "bundleRowsExcluded": 0,
                            "otherGradeRowsExcluded": 0,
                            "sources": [],
                        }
                    },
                }
            ],
        }
        result = fill_classify_qc_cohort(
            report,
            snk_bindings={},
            snk_harvests={},
            expected_count=1,
        )
        self.assertEqual(result["summary"]["verifiedNoneCount"], 0)
        self.assertEqual(result["summary"]["unavailableCount"], 1)
        self.assertEqual(result["shards"][0]["terminalState"], TERMINAL_UNAVAILABLE)
        self.assertFalse(result["summary"]["acceptance"]["fake_verified_none_without_coverage"])


if __name__ == "__main__":
    unittest.main()
