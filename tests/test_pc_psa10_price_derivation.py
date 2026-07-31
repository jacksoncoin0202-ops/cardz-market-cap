from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pc_psa10_price_derivation", ROOT / "pipelines" / "pc_psa10_price_derivation.py")
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)

QC_SPEC = importlib.util.spec_from_file_location("pc_qc", ROOT / "pipelines" / "canonical_db_qc.py")
assert QC_SPEC and QC_SPEC.loader
qc = importlib.util.module_from_spec(QC_SPEC)
sys.modules[QC_SPEC.name] = qc
QC_SPEC.loader.exec_module(qc)

MATERIALIZE_SPEC = importlib.util.spec_from_file_location("pc_materialize", ROOT / "pipelines" / "pc_psa10_price_materialize.py")
assert MATERIALIZE_SPEC and MATERIALIZE_SPEC.loader
materialize = importlib.util.module_from_spec(MATERIALIZE_SPEC)
sys.modules[MATERIALIZE_SPEC.name] = materialize
MATERIALIZE_SPEC.loader.exec_module(materialize)

AS_OF = datetime(2026, 7, 31, 12, tzinfo=timezone.utc)


class PcPsa10PriceDerivationTests(unittest.TestCase):
    def sale(self, fingerprint: str, price: int, days: int = 1, **overrides: object) -> dict[str, object]:
        row: dict[str, object] = {
            "variant_id": 7, "source_code": "ebay", "external_entity_id": "pc:123",
            "grader_code": "PSA", "grade_label": "PSA 10", "coverage_status": "partial",
            "timestamp_quality": "exact_date", "sold_at": (AS_OF - timedelta(days=days)).replace(tzinfo=None),
            "unit_price_usd": price, "transaction_fingerprint": fingerprint,
        }
        row.update(overrides)
        return row

    def test_ebay_median_requires_three_exact_psa10_sales_and_binds_all_fingerprints(self) -> None:
        result, reason = module.select_ebay_median(
            variant_id=7, pc_product_id="123",
            sales=[self.sale("a", 100), self.sale("b", 200, grade_label="10"), self.sale("c", 99999), self.sale("raw", 20, grade_label="Raw")],
            as_of=AS_OF,
        )
        self.assertEqual(reason, "accepted")
        assert result is not None
        self.assertEqual(result["price_usd"], "200.000000")
        self.assertEqual(result["observed_date"], AS_OF.date())
        self.assertEqual(result["sale_fingerprints"], ["a", "b", "c"])
        plan = module.plan_rows([result], as_of=AS_OF)
        self.assertEqual(plan[0]["payload"]["selectedSaleFingerprints"], ["a", "b", "c"])
        self.assertEqual(plan[0]["payload"]["latestSoldDate"], "2026-07-30")

    def test_ebay_median_rejects_bundle_pre_filtered_missing_and_out_of_window_rows(self) -> None:
        result, reason = module.select_ebay_median(
            variant_id=7, pc_product_id="123",
            sales=[self.sale("a", 100), self.sale("b", 200, days=31), self.sale("c", 300, coverage_status="quarantined")], as_of=AS_OF,
        )
        self.assertIsNone(result)
        self.assertEqual(reason, "insufficient_exact_ebay_psa10_sales")

    def test_direct_pc_requires_latest_explicit_psa10_not_stale_last_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pc.html"
            path.write_text('''<link rel="canonical" href="https://www.pricecharting.com/game/pokemon/x"><script>VGPC.product = {id:123}; VGPC.chart_data = {"manualonly":[[1780000000000,0]]};</script>''', encoding="utf-8")
            value, reason = module.validate_pc_psa10({"variant_id": 7, "pc_product_id": "123", "pc_url": "https://www.pricecharting.com/game/pokemon/x", "htmlPath": str(path)})
        self.assertIsNone(value)
        self.assertEqual(reason, "no_positive_psa10_field")

    def test_plan_is_replay_stable_for_fixed_as_of(self) -> None:
        result, _ = module.select_ebay_median(variant_id=7, pc_product_id="123", sales=[self.sale("a", 100), self.sale("b", 200), self.sale("c", 300)], as_of=AS_OF)
        assert result is not None
        self.assertEqual(module.plan_rows([result], as_of=AS_OF), module.plan_rows([result], as_of=AS_OF))

    def test_derivation_keeps_pc_guide_and_ebay_median_as_separate_families(self) -> None:
        direct = {
            "variant_id": 7,
            "source_code": "pricecharting",
            "external_entity_id": "123",
            "source_url": "https://www.pricecharting.com/game/pokemon/x",
            "observed_date": AS_OF.date(),
            "price_usd": "175.000000",
            "method": "pricecharting_explicit_psa10_field_v1",
            "artifact_sha256": "a" * 64,
            "field": "VGPC.chart_data.manualonly.last",
            "latest_sold_date": None,
            "sale_fingerprints": [],
        }
        with patch.object(module, "validate_pc_psa10", return_value=(direct, "accepted")):
            derived, outcomes = module.derive(
                [{
                    "variant_id": 7,
                    "pc_product_id": "123",
                    "pc_url": "https://www.pricecharting.com/game/pokemon/x",
                }],
                [self.sale("a", 100), self.sale("b", 200), self.sale("c", 300)],
                as_of=AS_OF,
            )
        self.assertEqual(
            [row["source_code"] for row in derived],
            ["pricecharting", "ebay"],
        )
        self.assertEqual(outcomes["pricecharting_explicit_psa10"], 1)
        self.assertEqual(outcomes["ebay_psa10_30d_median"], 1)

    def test_qc_selects_exact_pricecharting_ahead_of_ebay_and_snk(self) -> None:
        source_rows = [{"source_code": "pricecharting", "external_entity_id": "123", "match_status": "exact"}]
        price, meta = qc.select_display_exact_price([
            {"source_code": "snk_psa10", "price_usd": 150, "effective_at": AS_OF, "source_priority": 10},
            {"source_code": "ebay", "price_usd": 200, "effective_at": AS_OF, "source_priority": 90},
            {"source_code": "pricecharting", "price_usd": 175, "effective_at": AS_OF, "source_priority": 95},
        ], source_rows)
        self.assertEqual(price["source_code"], "pricecharting")
        self.assertEqual(meta["mode"], "priority_pricecharting_explicit_psa10")

    def test_materializer_run_key_fits_market_ingest_schema(self) -> None:
        self.assertEqual(len(materialize.run_key_for_plan("a" * 64)), 64)

    def test_materializer_rejects_tampered_or_non_direct_rows(self) -> None:
        row = {
            "variantId": 7, "sourceCode": "pricecharting", "observedDate": "2026-07-30", "effectiveAt": "2026-07-31T12:00:00Z",
            "priceUsd": "100.000000", "sourcePriority": 95, "metricStatus": "ready",
            "payload": {"contract": module.CONTRACT if hasattr(module, "CONTRACT") else "pc_psa10_current_price_v1", "variantId": 7, "source": "pricecharting", "externalEntityId": "123", "method": "pricecharting_explicit_psa10_field_v1", "field": "VGPC.chart_data.manualonly.last", "artifactSha256": "a" * 64, "sourceUrl": "https://www.pricecharting.com/game/pokemon/x", "selectedSaleFingerprints": [], "latestSoldDate": None, "asOf": "2026-07-31T12:00:00Z"},
        }
        row["payloadSha256"] = module.sha256(row["payload"])
        doc = {"asOf": "2026-07-31T12:00:00Z", "rows": [row]}
        self.assertEqual(
            len(materialize.validate_plan_rows(doc, now=AS_OF)),
            1,
        )
        row["payload"]["field"] = "VGPC.chart_data.graded.last"
        with self.assertRaises(ValueError):
            materialize.validate_plan_rows(doc, now=AS_OF)

    def test_materializer_rejects_future_dated_plan(self) -> None:
        with self.assertRaisesRegex(ValueError, "future-dated"):
            materialize.validate_plan_rows(
                {"asOf": "2026-07-31T12:00:01Z", "rows": []},
                now=AS_OF,
            )

    def test_materializer_accepts_and_replays_exact_ebay_median(self) -> None:
        result, reason = module.select_ebay_median(
            variant_id=7,
            pc_product_id="123",
            sales=[self.sale("a", 100), self.sale("b", 200), self.sale("c", 300)],
            as_of=AS_OF,
            source_url="https://www.pricecharting.com/game/pokemon/x",
        )
        self.assertEqual(reason, "accepted")
        assert result is not None
        row = module.plan_rows([result], as_of=AS_OF)[0]
        doc = {"asOf": "2026-07-31T12:00:00Z", "rows": [row]}
        validated = materialize.validate_plan_rows(doc, now=AS_OF)
        self.assertEqual(validated[0]["sourceCode"], "ebay")
        materialize.recheck_ebay_rows(
            validated,
            [self.sale("a", 100), self.sale("b", 200), self.sale("c", 300)],
            as_of=AS_OF,
        )
        tampered = deepcopy(row)
        tampered["payload"]["selectedSaleFingerprints"] = ["a", "b"]
        tampered["payloadSha256"] = module.sha256(tampered["payload"])
        with self.assertRaisesRegex(ValueError, "invalid exact eBay"):
            materialize.validate_plan_rows(
                {"asOf": "2026-07-31T12:00:00Z", "rows": [tampered]},
                now=AS_OF,
            )

    def test_materializer_replay_detects_zero_changes(self) -> None:
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def execute(self, *_args): return None
            def fetchall(self):
                return [{"variant_id": 7, "source_code": "pricecharting", "observed_date": "2026-07-30", "effective_at": datetime(2026, 7, 31, 12), "price_usd": "100.000000", "source_priority": 95, "metric_status": "ready", "payload_sha256": "p" * 64}]
        class Connection:
            def cursor(self): return Cursor()
        row = {"variantId": 7, "sourceCode": "pricecharting", "sourcePriority": 95, "observedDate": "2026-07-30", "effectiveAt": "2026-07-31T12:00:00Z", "priceUsd": "100.000000", "payloadSha256": "p" * 64}
        self.assertEqual(materialize.changed_rows(Connection(), [row]), [])

    def test_materializer_rejects_extra_binding_or_duplicate_product_owner(self) -> None:
        rows = [{"variantId": 7, "payload": {"externalEntityId": "123"}}]
        with self.assertRaisesRegex(ValueError, "ambiguous exact PriceCharting binding"):
            materialize.assert_binding_ownership(rows, [
                {"canonical_variant_id": 7, "bound_variant_id": 7, "external_entity_id": "123"},
                {"canonical_variant_id": 7, "bound_variant_id": 7, "external_entity_id": "456"},
            ])
        with self.assertRaisesRegex(ValueError, "ambiguous owner"):
            materialize.assert_binding_ownership(rows, [
                {"canonical_variant_id": 7, "bound_variant_id": 7, "external_entity_id": "123"},
                {"canonical_variant_id": 9, "bound_variant_id": 9, "external_entity_id": "123"},
            ])


if __name__ == "__main__":
    unittest.main()
