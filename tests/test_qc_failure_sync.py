from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pipelines.failure_ledger import current_failures, read_events
from pipelines.qc_failure_sync import load_report, sync_report


class QcFailureSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.ledger = self.root / "failures"
        self.report_path = self.root / "report.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_report(self, *, run_id: str, blockers: list[str]) -> tuple[dict[str, object], str]:
        report = {
            "readOnly": True,
            "database": {
                "authority": "canonical_mysql",
                "name": "cardz_market_cap",
            },
            "counts": {"catalog": 1},
            "runId": run_id,
            "cards": [
                {
                    "id": "cmc_test",
                    "variantId": 7,
                    "tcg": "pokemon",
                    "marketRank": 3,
                    "segment": "qualified",
                    "evidenceSha256": "a" * 64,
                    "facts": {
                        "identity": {
                            "cardLanguage": "en",
                            "set": "Base Set",
                            "collectorNumber": "4/102",
                            "edition": "1st",
                            "parallel": "holo",
                            "finish": "foil",
                            "printingSha256": "b" * 64,
                        }
                    },
                    "blockers": blockers,
                }
            ],
        }
        self.report_path.write_text(json.dumps(report), encoding="utf-8")
        return load_report(self.report_path)

    def test_dry_run_groups_required_lanes_without_writing_events(self) -> None:
        report, report_sha = self.write_report(
            run_id="qc_one",
            blockers=[
                "canonical_printing_missing",
                "exact_psa10_price_missing",
                "image_not_public_allowed",
                "psa10_sales_30d_missing",
                "market_cap_not_materialized",
            ],
        )
        result = sync_report(report, report_sha256=report_sha, report_path=self.report_path, ledger_root=self.ledger)
        self.assertTrue(result["dryRun"])
        self.assertEqual(result["openByLane"], {
            "canonical_printing": 1, "psa10_price": 1, "image_approval": 1,
            "sales": 1, "general": 1,
        })
        self.assertEqual(read_events(ledger_root=self.ledger), [])

    def test_write_opens_stable_agent_review_items_and_next_report_resolves_absent_lane(self) -> None:
        report, report_sha = self.write_report(
            run_id="qc_one",
            blockers=["canonical_printing_missing", "image_not_public_allowed"],
        )
        first = sync_report(report, report_sha256=report_sha, report_path=self.report_path, ledger_root=self.ledger, write=True)
        self.assertEqual(first["writtenFailures"], 2)
        failures = current_failures(ledger_root=self.ledger, source="canonical_db_qc")
        self.assertEqual([(row["stage"], row["itemKey"]) for row in failures], [
            ("canonical_printing", "cmc_test"), ("image_approval", "cmc_test"),
        ])
        self.assertTrue(all(row["retryable"] for row in failures))
        self.assertEqual(
            {row["stage"]: row["nextAction"] for row in failures},
            {
                "canonical_printing": "resolve_canonical_printing_identity",
                "image_approval": "replace_or_qc_exact_raw_front",
            },
        )
        context = failures[0]["context"]
        self.assertEqual(context["cardLanguage"], "en")
        self.assertEqual(context["set"], "Base Set")
        self.assertEqual(context["collectorNumber"], "4/102")
        self.assertEqual(context["edition"], "1st")
        self.assertEqual(context["parallel"], "holo")
        self.assertEqual(context["finish"], "foil")
        self.assertEqual(context["printingSha256"], "b" * 64)
        self.assertIn("Base Set", context["searchTerms"])
        self.assertIn("4/102", context["searchTerms"])

        repeated = sync_report(report, report_sha256=report_sha, report_path=self.report_path, ledger_root=self.ledger, write=True)
        self.assertEqual(repeated["writtenFailures"], 0)
        self.assertEqual(len(read_events(ledger_root=self.ledger)), 2)

        report, report_sha = self.write_report(
            run_id="qc_same_blockers_new_receipt",
            blockers=["canonical_printing_missing", "image_not_public_allowed"],
        )
        same_blockers = sync_report(
            report,
            report_sha256=report_sha,
            report_path=self.report_path,
            ledger_root=self.ledger,
            write=True,
        )
        self.assertEqual(same_blockers["writtenFailures"], 0)
        self.assertEqual(len(read_events(ledger_root=self.ledger)), 2)

        report, report_sha = self.write_report(run_id="qc_two", blockers=["image_not_public_allowed"])
        second = sync_report(report, report_sha256=report_sha, report_path=self.report_path, ledger_root=self.ledger, write=True)
        self.assertEqual(second["resolutions"], 1)
        self.assertEqual(second["writtenResolutions"], 1)
        failures = current_failures(ledger_root=self.ledger, source="canonical_db_qc")
        self.assertEqual([(row["stage"], row["itemKey"]) for row in failures], [("image_approval", "cmc_test")])
        self.assertEqual(len(read_events(ledger_root=self.ledger)), 3)

    def test_rejects_noncanonical_database_report(self) -> None:
        self.report_path.write_text(
            json.dumps(
                {
                    "readOnly": True,
                    "database": {
                        "authority": "canonical_mysql",
                        "name": "cardzos",
                    },
                    "counts": {"catalog": 0},
                    "runId": "wrong-db",
                    "cards": [],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "read-only cardz_market_cap"):
            load_report(self.report_path)

    def test_variant_id_rekey_resolves_old_item_and_keeps_current_lane(self) -> None:
        report, report_sha = self.write_report(
            run_id="qc_old_id",
            blockers=["psa10_sales_30d_insufficient"],
        )
        first = sync_report(
            report,
            report_sha256=report_sha,
            report_path=self.report_path,
            ledger_root=self.ledger,
            write=True,
        )
        self.assertEqual(first["writtenFailures"], 1)

        report["runId"] = "qc_new_id"
        report["cards"][0]["id"] = "cmc_rekeyed"
        self.report_path.write_text(json.dumps(report), encoding="utf-8")
        report, report_sha = load_report(self.report_path)
        second = sync_report(
            report,
            report_sha256=report_sha,
            report_path=self.report_path,
            ledger_root=self.ledger,
            write=True,
        )

        self.assertEqual(second["writtenFailures"], 1)
        self.assertEqual(second["resolutions"], 1)
        self.assertEqual(second["writtenResolutions"], 1)
        failures = current_failures(ledger_root=self.ledger, source="canonical_db_qc")
        self.assertEqual(
            [(row["stage"], row["itemKey"]) for row in failures],
            [("sales", "cmc_rekeyed")],
        )
        resolved = [
            event
            for event in read_events(ledger_root=self.ledger)
            if event.get("status") == "resolved"
        ]
        self.assertEqual(resolved[0]["resolution"], "qc_item_key_rekeyed")
        self.assertEqual(resolved[0]["context"]["currentCardId"], "cmc_rekeyed")


if __name__ == "__main__":
    unittest.main()
