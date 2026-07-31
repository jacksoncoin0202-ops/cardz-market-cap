from __future__ import annotations

import sys
import tempfile
import unittest
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import c11_pc_sold_ingest as ingest  # noqa: E402


class FakeCursor:
    def __init__(self, existing: set[tuple[str, str]]) -> None:
        self.existing = existing
        self.calls: list[tuple[str, object]] = []
        self.lastrowid = 73
        self._rows: list[dict[str, str]] = []
        self.executemany_batches: list[list[tuple[object, ...]]] = []

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, query: str, params: object = None) -> None:
        self.calls.append((query, params))
        if "SELECT external_entity_id, transaction_fingerprint" in query:
            values = list(params or [])
            pairs = zip(values[1::2], values[2::2])
            self._rows = [
                {"external_entity_id": external, "transaction_fingerprint": fingerprint}
                for external, fingerprint in pairs
                if (external, fingerprint) in self.existing
            ]

    def executemany(self, query: str, params: list[tuple[object, ...]]) -> None:
        self.calls.append((query, params))
        self.executemany_batches.append(params)

    def fetchall(self) -> list[dict[str, str]]:
        return self._rows


class FakeConnection:
    def __init__(self, existing: set[tuple[str, str]]) -> None:
        self.cursor_value = FakeCursor(existing)
        self.commits = 0

    def cursor(self) -> FakeCursor:
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        return None


def sale(number: int) -> dict[str, object]:
    return {
        "variant_id": number,
        "external_entity_id": f"pc:{number}",
        "fingerprint": f"f{number}",
        "sold_at": datetime(2026, 7, 31),
        "source_date_text": "2026-07-31",
        "unit_price_usd": ingest.Decimal("12.34"),
        "payload_sha256": f"p{number}",
    }


class C11PcSoldIngestTests(unittest.TestCase):
    def test_exact_product_gate_canonicalizes_alias_and_marks_row(self) -> None:
        rows, rejected = ingest.gate_map_rows_against_exact_products(
            [{"variant_id": 7, "pc_product_id": 123}],
            canonical_by_variant={7: 70},
            exact_products_by_canonical_variant={70: {"123"}},
        )

        self.assertEqual(rejected, [])
        self.assertEqual(rows[0]["variant_id"], 70)
        self.assertTrue(rows[0]["_pc_exact_product_gate"])

    def test_exact_product_gate_fails_closed_for_missing_stale_or_ambiguous(self) -> None:
        rows, rejected = ingest.gate_map_rows_against_exact_products(
            [
                {"variant_id": 1, "pc_product_id": 10},
                {"variant_id": 2, "pc_product_id": 20},
                {"variant_id": 3, "pc_product_id": 30},
            ],
            canonical_by_variant={},
            exact_products_by_canonical_variant={2: {"21"}, 3: {"30", "31"}},
        )

        self.assertEqual(rows, [])
        self.assertEqual(
            [row["status"] for row in rejected],
            [
                "pc_exact_identity_missing",
                "pc_exact_product_stale",
                "pc_exact_identity_ambiguous",
            ],
        )

    def test_exact_product_gate_allows_pc_sale_without_redundant_title_identity(self) -> None:
        row = {
            "variant_id": 7,
            "pc_product_id": 123,
            "collector_number": "112/165",
            "card_name": "Rhydon",
            "set_name": "Pokemon Japanese 151",
            "pc_url": "https://www.pricecharting.com/game/pokemon-japanese-scarlet-&-violet-151/rhydon-112",
            "_pc_exact_product_gate": True,
        }
        sale_row = {
            "title": "Pokemon PSA 10 certified card",
            "ebay_itm": "123456789012",
            "price_usd": 12.34,
            "date": "2026-07-30",
        }
        stats = ingest.Counter()

        verified = ingest.verify_sale(row, sale_row, stats)

        self.assertIsNotNone(verified)
        self.assertEqual(stats, ingest.Counter())

    def test_ungated_sale_keeps_legacy_title_identity_checks(self) -> None:
        row = {
            "variant_id": 7,
            "pc_product_id": 123,
            "collector_number": "112/165",
            "card_name": "Rhydon",
            "set_name": "Pokemon Japanese 151",
            "pc_url": "https://www.pricecharting.com/game/pokemon-japanese-scarlet-&-violet-151/rhydon-112",
        }
        sale_row = {
            "title": "Pokemon PSA 10 certified card",
            "ebay_itm": "123456789012",
            "price_usd": 12.34,
            "date": "2026-07-30",
        }
        stats = ingest.Counter()

        self.assertIsNone(ingest.verify_sale(row, sale_row, stats))
        self.assertEqual(stats["reject_collector"], 1)

    def test_explicit_html_path_skips_fallback_glob(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "card.html"
            path.write_text("html", encoding="utf-8")
            with patch.object(Path, "glob", side_effect=AssertionError("fallback glob called")):
                self.assertEqual(ingest.resolve_html_path({"variant_id": 1, "htmlPath": str(path)}), path)

    def test_write_sales_skips_all_existing_without_run_or_commit(self) -> None:
        rows = [sale(1), sale(2)]
        conn = FakeConnection({("pc:1", "f1"), ("pc:2", "f2")})

        result = ingest.write_sales(conn, rows)

        self.assertEqual(
            result,
            {
                "run_id": None,
                "inserted_or_updated": 0,
                "skipped_existing": 2,
                "inserted_variant_ids": [],
            },
        )
        self.assertEqual(conn.commits, 0)
        self.assertEqual(len(conn.cursor_value.calls), 1)
        self.assertIn("SELECT external_entity_id, transaction_fingerprint", conn.cursor_value.calls[0][0])

    def test_write_sales_inserts_only_unknown_sales(self) -> None:
        rows = [sale(1), sale(2)]
        conn = FakeConnection({("pc:1", "f1")})

        result = ingest.write_sales(conn, rows)

        self.assertEqual(result["inserted_or_updated"], 1)
        self.assertEqual(result["skipped_existing"], 1)
        self.assertEqual(result["inserted_variant_ids"], [2])
        self.assertEqual(conn.commits, 1)
        self.assertEqual(len(conn.cursor_value.executemany_batches), 1)
        self.assertEqual(len(conn.cursor_value.executemany_batches[0]), 1)
        self.assertEqual(conn.cursor_value.executemany_batches[0][0][1], 2)

    def test_existing_sale_lookup_is_batched_at_400_composite_keys(self) -> None:
        rows = [sale(number) for number in range(401)]
        conn = FakeConnection(set())

        self.assertEqual(ingest.existing_sale_keys(conn.cursor(), rows), set())

        self.assertEqual(len(conn.cursor_value.calls), 2)
        self.assertEqual(len(list(conn.cursor_value.calls[0][1])), 801)
        self.assertEqual(len(list(conn.cursor_value.calls[1][1])), 3)

    def test_main_skips_registry_update_when_all_sales_already_exist(self) -> None:
        row = {"variant_id": 1}
        existing_result = {
            "run_id": None,
            "inserted_or_updated": 0,
            "skipped_existing": 1,
            "inserted_variant_ids": [],
        }
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report.json"
            conn = FakeConnection(set())
            with (
                patch.object(ingest, "load_map", return_value=[row]),
                patch.object(ingest, "db", return_value=conn),
                patch.object(ingest, "partition_existing_variants", return_value=([row], [])),
                patch.object(ingest, "before_counts", return_value={"ebay_identity_v": 0, "ebay_sale_n": 0, "ebay_sale_v": 0}),
                patch.object(ingest, "collect_sales", return_value=([sale(1)], ingest.Counter(), [])),
                patch.object(ingest, "record_card_outcomes"),
                patch.object(ingest, "write_sales", return_value=existing_result),
                patch.object(ingest, "update_registry") as update_registry,
                patch.object(sys, "argv", ["c11", "--write", "--report", str(report)]),
            ):
                self.assertEqual(ingest.main(), 0)

            document = json.loads(report.read_text(encoding="utf-8"))
        update_registry.assert_not_called()
        self.assertEqual(document["registryUpdated"], 0)
        self.assertEqual(document["writeInfo"], existing_result)


if __name__ == "__main__":
    unittest.main()
