from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from ebay_sold_data import (  # noqa: E402
    build_query,
    collect_from_input,
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
        result = collect_from_input([card, {**card, "canonicalExternalId": "missing"}], rows)
        self.assertEqual(result[0]["status"], "available")
        self.assertEqual(result[0]["transactions"][0]["fetchedAt"], "2026-07-23T12:00:00Z")
        self.assertEqual(result[1]["status"], "unavailable")
        self.assertEqual(result[1]["referenceFallback"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
