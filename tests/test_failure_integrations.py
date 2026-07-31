from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pipelines import c11_pc_sold_ingest
from pipelines import pc_full_shard_runner
from pipelines import pc_full_serial_driver
from pipelines import pricecharting_cf_session
from pipelines import pricecharting_ebay_export
from pipelines import run_daily
from pipelines import snkrdunk_bulk


class FailureIntegrationTests(unittest.TestCase):
    def test_pc_historical_import_uses_stable_fallback_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "shard_0.json").write_text(
                json.dumps([{"vid": 7, "name": "Test"}]),
                encoding="utf-8",
            )
            (root / "results_shard_0.jsonl").write_text(
                json.dumps({"vid": 7, "status": "resolve_mismatch"}) + "\n",
                encoding="utf-8",
            )
            with (
                patch.object(pc_full_shard_runner, "OUT_ROOT", root),
                patch.object(pc_full_shard_runner, "record_item_outcome") as record,
            ):
                pc_full_shard_runner.import_existing_results()
                first_stamp = record.call_args.kwargs["occurred_at"]
                record.reset_mock()
                pc_full_shard_runner.import_existing_results()
                second_stamp = record.call_args.kwargs["occurred_at"]
            self.assertEqual(first_stamp, second_stamp)

    def test_pc_agent_retry_can_target_one_card_and_prepend_researched_url(self) -> None:
        selected = pc_full_shard_runner.select_target_work(
            [
                {"vid": 7, "urls": ["https://example.test/old"]},
                {"vid": 8, "urls": []},
            ],
            variant_id=7,
            candidate_urls=["https://example.test/correct"],
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["vid"], 7)
        self.assertEqual(
            selected[0]["urls"],
            ["https://example.test/correct", "https://example.test/old"],
        )

    def test_pc_product_links_decode_html_entities(self) -> None:
        links = pc_full_shard_runner.extract_product_links(
            '<a href="https://www.pricecharting.com/game/'
            'pokemon-japanese-scarlet-&amp;-violet-151/venusaur-ex-184">'
        )

        self.assertEqual(
            links,
            [
                "https://www.pricecharting.com/game/"
                "pokemon-japanese-scarlet-&-violet-151/venusaur-ex-184"
            ],
        )

    def test_pc_bulk_fetch_timeout_yields_quickly_to_card_retry_queue(self) -> None:
        self.assertLessEqual(pc_full_shard_runner.PC_BULK_FETCH_TIMEOUT_S, 30)

    def test_pc_done_set_requires_current_exact_db_binding(self) -> None:
        done = pc_full_shard_runner.verified_done_variants(
            [
                {"vid": 1, "have_pc": True},
                {"vid": 2, "have_pc": True},
                {"vid": 3, "have_pc": False},
            ],
            {2, 3},
        )

        self.assertEqual(done, {2, 3})

    def test_serial_driver_stops_on_first_failed_shard(self) -> None:
        completed = [
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 2),
        ]
        with (
            patch.object(pc_full_serial_driver.subprocess, "run", side_effect=completed) as run,
            patch.object(pc_full_serial_driver.sys, "argv", ["driver", "--shards", "6"]),
            patch.object(pc_full_serial_driver, "record_failure"),
            patch.object(pc_full_serial_driver, "record_resolution"),
        ):
            code = pc_full_serial_driver.main()
        self.assertEqual(code, 2)
        self.assertEqual(run.call_count, 2)

    def test_serial_driver_can_consolidate_without_rerunning_provider_shards(self) -> None:
        summary = {
            "rows": 1,
            "duplicateRows": 0,
            "staleRowsSkipped": 0,
            "nonExactRowsSkipped": 0,
            "output": "/tmp/full.jsonl",
        }
        with (
            patch.object(
                pc_full_serial_driver.sys,
                "argv",
                ["driver", "--shards", "6", "--consolidate-only"],
            ),
            patch.object(pc_full_serial_driver.subprocess, "run") as run,
            patch.object(
                pc_full_serial_driver,
                "load_exact_product_by_variant",
                return_value={1: "11"},
            ),
            patch.object(
                pc_full_serial_driver,
                "consolidate_maps",
                return_value=summary,
            ) as consolidate,
            patch.object(pc_full_serial_driver, "record_resolution"),
        ):
            code = pc_full_serial_driver.main()

        self.assertEqual(code, 0)
        run.assert_not_called()
        consolidate.assert_called_once_with(
            6,
            exact_product_by_variant={1: "11"},
        )

    def test_serial_driver_builds_one_deterministic_deduplicated_map(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [
                {
                    "variant_id": 2,
                    "pc_product_id": 22,
                    "pc_url": "https://example.test/22",
                },
                {
                    "variant_id": 1,
                    "pc_product_id": 11,
                    "pc_url": "https://example.test/11",
                },
            ]
            (root / "c11_pc_ebay_map_full900_shard_0.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )
            (root / "c11_pc_ebay_map_full900_shard_1.jsonl").write_text(
                json.dumps(rows[0]) + "\n",
                encoding="utf-8",
            )
            output = root / "full.jsonl"
            with patch.object(pc_full_serial_driver, "MAP_DIR", root):
                summary = pc_full_serial_driver.consolidate_maps(2, output)
            self.assertEqual(summary["rows"], 2)
            self.assertEqual(summary["duplicateRows"], 1)
            written = [
                json.loads(line)
                for line in output.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([row["variant_id"] for row in written], [1, 2])

    def test_serial_driver_rejects_conflicting_products_for_one_variant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for shard, product_id in enumerate((11, 12)):
                (root / f"c11_pc_ebay_map_full900_shard_{shard}.jsonl").write_text(
                    json.dumps(
                        {
                            "variant_id": 1,
                            "pc_product_id": product_id,
                            "pc_url": f"https://example.test/{product_id}",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
            with (
                patch.object(pc_full_serial_driver, "MAP_DIR", root),
                self.assertRaisesRegex(ValueError, "conflicting PriceCharting"),
            ):
                pc_full_serial_driver.consolidate_maps(2, root / "full.jsonl")

    def test_serial_driver_uses_current_exact_identity_over_stale_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for shard, product_id in enumerate((11, 12)):
                (root / f"c11_pc_ebay_map_full900_shard_{shard}.jsonl").write_text(
                    json.dumps(
                        {
                            "variant_id": 1,
                            "pc_product_id": product_id,
                            "pc_url": f"https://example.test/{product_id}",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
            output = root / "full.jsonl"
            with patch.object(pc_full_serial_driver, "MAP_DIR", root):
                summary = pc_full_serial_driver.consolidate_maps(
                    2,
                    output,
                    exact_product_by_variant={1: "12"},
                )
            written = [
                json.loads(line)
                for line in output.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([row["pc_product_id"] for row in written], [12])
            self.assertEqual(summary["staleRowsSkipped"], 1)

    def test_serial_driver_includes_targeted_retry_map_fragments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "c11_pc_ebay_map_full900_shard_0.jsonl").write_text(
                json.dumps(
                    {
                        "variant_id": 1,
                        "pc_product_id": 11,
                        "pc_url": "https://example.test/11",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "c11_pc_ebay_map_full900_shard_shard_0.jsonl").write_text(
                json.dumps(
                    {
                        "variant_id": 2,
                        "pc_product_id": 22,
                        "pc_url": "https://example.test/22",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output = root / "full.jsonl"
            with patch.object(pc_full_serial_driver, "MAP_DIR", root):
                summary = pc_full_serial_driver.consolidate_maps(
                    1,
                    output,
                    exact_product_by_variant={1: "11", 2: "22"},
                )

            written = [
                json.loads(line)
                for line in output.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([row["variant_id"] for row in written], [1, 2])
            self.assertEqual(summary["rows"], 2)

    def test_bulk_retry_does_not_treat_prior_error_row_as_done(self) -> None:
        class FakeApi:
            def __init__(self, **_kwargs):
                pass

            def fetch_x_version(self):
                return "test-version"

            def pull_card(self, item_id, condition_code=None):
                return {
                    "item_id": item_id,
                    "product_number": "TEST-001",
                    "condition": condition_code,
                }

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "snk.jsonl"
            output.write_text(
                json.dumps({"item_id": 42, "error": "old failure"}) + "\n",
                encoding="utf-8",
            )
            with (
                patch.object(snkrdunk_bulk, "SnkrdunkApi", FakeApi),
                patch.object(snkrdunk_bulk, "record_failure") as record_failure,
                patch.object(snkrdunk_bulk, "record_resolution") as record_resolution,
            ):
                report = snkrdunk_bulk.pull_all(
                    [42],
                    output,
                    delay=0,
                    condition_code="trading_card_single_psa10",
                )

            rows = [
                json.loads(line)
                for line in output.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(report["fetched"], 1)
            self.assertEqual(report["failed"], 0)
            self.assertEqual(report["skipped_existing"], 0)
            self.assertNotIn("error", rows[-1])
            record_failure.assert_not_called()
            record_resolution.assert_called_once()

    def test_daily_subprocess_failure_is_recorded_without_command_payload(self) -> None:
        error = subprocess.CalledProcessError(7, ["python", "worker.py", "--token", "supersecret"])
        with (
            patch.object(run_daily.subprocess, "run", side_effect=error),
            patch.object(run_daily, "record_failure") as record_failure,
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                run_daily.run_checked(
                    ["python", "worker.py", "--token", "supersecret"],
                    cwd=Path("."),
                    timeout=30,
                )

        call = record_failure.call_args.kwargs
        self.assertEqual(call["reason_code"], "nonzero_exit")
        self.assertNotIn("command", call["context"])
        self.assertNotIn("supersecret", json.dumps(call))

    def test_c11_outcomes_keep_retryable_and_review_states_separate(self) -> None:
        reports = [
            {"variant_id": 1, "status": "ok", "accepted": 4},
            {"variant_id": 2, "status": "no_html"},
            {"variant_id": 3, "status": "parse_fail", "error": "bad html"},
            {"variant_id": 4, "status": "no_psa10_sales"},
            {"variant_id": 5, "status": "zero_after_verify"},
            {"variant_id": 6, "status": "catalog_variant_missing"},
        ]
        metadata = {variant_id: {"pc_url": f"https://example.test/{variant_id}"} for variant_id in range(1, 7)}
        with (
            patch.object(c11_pc_sold_ingest, "record_failure") as record_failure,
            patch.object(c11_pc_sold_ingest, "record_resolution") as record_resolution,
        ):
            c11_pc_sold_ingest.record_card_outcomes(reports, metadata)

        self.assertEqual(record_resolution.call_count, 1)
        retryability = {
            call.kwargs["item_key"]: call.kwargs["retryable"]
            for call in record_failure.call_args_list
        }
        self.assertEqual(
            retryability,
            {2: True, 3: True, 4: False, 5: False, 6: False},
        )

    def test_c11_partitions_missing_catalog_variants_before_write(self) -> None:
        cursor = Mock()
        cursor.fetchall.return_value = [{"id": 1}, {"id": 3}]
        accepted, missing = c11_pc_sold_ingest.partition_existing_variants(
            cursor,
            [
                {"variant_id": 1},
                {"variant_id": 2},
                {"variant_id": 3},
            ],
        )

        self.assertEqual([row["variant_id"] for row in accepted], [1, 3])
        self.assertEqual([row["variant_id"] for row in missing], [2])
        cursor.execute.assert_called_once()

    def test_pricecharting_fetch_failure_is_recorded(self) -> None:
        with (
            patch.object(pricecharting_cf_session, "_cmd_fetch_once", return_value=2),
            patch.object(pricecharting_cf_session, "record_failure") as record_failure,
            patch.object(pricecharting_cf_session, "record_resolution") as record_resolution,
        ):
            code = pricecharting_cf_session.cmd_fetch(
                "https://example.test/card?token=secret",
                None,
            )
        self.assertEqual(code, 2)
        record_failure.assert_called_once()
        record_resolution.assert_not_called()

    def test_pricecharting_plain_404_is_terminal(self) -> None:
        html = (
            "<html><body><pre>404 page not found\n"
            "</pre></body></html>"
        )
        self.assertTrue(
            pricecharting_cf_session._is_terminal_not_found(404, "", html)
        )
        self.assertFalse(
            pricecharting_cf_session._is_terminal_not_found(
                403,
                "Just a moment...",
                "<script src='/cdn-cgi/challenge-platform'></script>",
            )
        )

        with (
            patch.object(pricecharting_cf_session, "_cmd_fetch_once", return_value=4),
            patch.object(pricecharting_cf_session, "record_failure") as record_failure,
            patch.object(pricecharting_cf_session, "record_resolution") as record_resolution,
        ):
            code = pricecharting_cf_session.cmd_fetch(
                "https://www.pricecharting.com/product/404",
                None,
            )
        self.assertEqual(code, 4)
        self.assertFalse(record_failure.call_args.kwargs["retryable"])
        self.assertEqual(
            record_failure.call_args.kwargs["reason_code"],
            "http_not_found",
        )
        record_resolution.assert_not_called()

    def test_pricecharting_export_records_all_failed_rows_before_aborting(self) -> None:
        rows = [
            {"canonicalSourceCode": "snkrdunk", "canonicalExternalId": "1"},
            {"canonicalSourceCode": "snkrdunk", "canonicalExternalId": "2"},
        ]
        with (
            patch.object(pricecharting_ebay_export, "record_failure") as record_failure,
            patch.object(pricecharting_ebay_export, "record_resolution"),
        ):
            with self.assertRaisesRegex(RuntimeError, "2 failed row"):
                pricecharting_ebay_export.export_map(rows)
        self.assertEqual(record_failure.call_count, 2)


if __name__ == "__main__":
    unittest.main()
