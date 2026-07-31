from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock

from pipelines import pc_ungraded_reference_ingest as ingest


class PcUngradedReferenceIngestTests(unittest.TestCase):
    def test_load_candidates_requires_exact_ready_high_map_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "map.jsonl"
            path.write_text("\n".join([
                json.dumps({"variant_id": 7, "pc_product_id": 123, "pc_url": "https://www.pricecharting.com/game/pokemon-x/test", "ready_for_c12": True, "confidence": "high"}),
                json.dumps({"variant_id": 8, "pc_product_id": 456, "pc_url": "https://www.pricecharting.com/game/pokemon-x/test", "ready_for_c12": True, "confidence": "low"}),
                json.dumps({"variant_id": 9, "pc_product_id": 789, "pc_url": "http://www.pricecharting.com/game/pokemon-x/test", "ready_for_c12": True, "confidence": "high"}),
            ]) + "\n", encoding="utf-8")
            rows = ingest.load_candidate_rows(path)
        self.assertEqual([(row["variant_id"], row["pc_product_id"]) for row in rows], [(7, "123")])

    def test_partition_requires_same_variant_and_product_exact_binding(self) -> None:
        cursor = Mock()
        cursor.fetchall.return_value = [{"variant_id": 7, "external_entity_id": "123"}]
        accepted, rejected = ingest.partition_exact_bindings(cursor, [
            {"variant_id": 7, "pc_product_id": "123"},
            {"variant_id": 8, "pc_product_id": "123"},
        ])
        self.assertEqual([row["variant_id"] for row in accepted], [7])
        self.assertEqual([row["variant_id"] for row in rejected], [8])
        self.assertIn("match_status='exact'", cursor.execute.call_args.args[0])

    def test_collect_requires_html_product_id_and_positive_ungraded_last(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            html_path = Path(directory) / "saved.html"
            html = b'<link rel="canonical" href="https://www.pricecharting.com/game/pokemon-x/test">'
            html_path.write_bytes(html)
            row = {"variant_id": 7, "pc_product_id": "123", "pc_url": "https://www.pricecharting.com/game/pokemon-x/test", "htmlPath": str(html_path)}
            parsed = {
                "ok": True, "product": {"id": 123},
                "chart": {"used": {"label": "Ungraded", "last": [1704067200000, 1234], "last_usd": 12.34}},
            }
            original = ingest.parse_product_html
            ingest.parse_product_html = lambda *_args, **_kwargs: parsed
            try:
                references, outcomes = ingest.collect_references([row])
            finally:
                ingest.parse_product_html = original
        self.assertEqual(outcomes[0]["status"], "accepted")
        self.assertEqual(references[0]["price_usd"], "12.340000")
        self.assertEqual(references[0]["observed_at"], datetime(2024, 1, 1))
        self.assertEqual(references[0]["payload_sha256"], hashlib.sha256(html).hexdigest())

    def test_collect_rejects_product_mismatch_and_zero_ungraded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            html_path = Path(directory) / "saved.html"
            html_path.write_bytes(b'<link rel="canonical" href="https://www.pricecharting.com/game/pokemon-x/test">')
            base = {"variant_id": 7, "pc_product_id": "123", "pc_url": "https://www.pricecharting.com/game/pokemon-x/test", "htmlPath": str(html_path)}
            original = ingest.parse_product_html
            ingest.parse_product_html = lambda *_args, **_kwargs: {"ok": True, "product": {"id": 999}, "chart": {}}
            try:
                _refs, outcomes = ingest.collect_references([base])
            finally:
                ingest.parse_product_html = original
        self.assertEqual(outcomes[0]["status"], "product_id_mismatch")

    def test_write_is_insert_ignore_and_never_market_price_observation(self) -> None:
        source = Path(ingest.__file__).read_text(encoding="utf-8")
        migration = (Path(ingest.__file__).parents[0] / "migrations" / "019_ungraded_reference_price.mysql.sql").read_text(encoding="utf-8")
        self.assertIn("INSERT IGNORE INTO market_ungraded_reference_price", source)
        self.assertNotIn("INTO market_price_observation", source)
        self.assertIn("observed_at, source_payload_sha256", migration)
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.rowcount = 1
        inserted = ingest.write_references(connection, [{
            "variant_id": 7, "external_entity_id": "123", "source_url": "https://www.pricecharting.com/game/pokemon-x/test",
            "observed_at": datetime(2024, 1, 1), "price_usd": "12.340000", "payload_sha256": "a" * 64,
        }])
        self.assertEqual(inserted, 1)
        self.assertIn("INSERT IGNORE", cursor.executemany.call_args.args[0])
        connection.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
