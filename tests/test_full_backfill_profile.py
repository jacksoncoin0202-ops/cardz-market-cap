from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from backend import (  # noqa: E402
    candidate_backfill_command,
    full_backfill_bootstrap_command,
    require_candidate_backfill_ready,
    require_snk_refill_ready,
    fx_refresh_command,
    snk_refill_command,
    run_full_backfill,
)


class FullBackfillProfileTests(unittest.TestCase):
    def test_bootstrap_phase_is_private_only_and_does_not_publish(self) -> None:
        command = full_backfill_bootstrap_command(Path("python"), "staging")
        self.assertIn("--refresh-bootstrap-source", command)
        self.assertIn("--bootstrap-only", command)
        self.assertNotIn("--publish", command)

    def test_candidate_collection_is_explicit(self) -> None:
        command = candidate_backfill_command(
            Path("python"), output=ROOT / "data/runtime/private-source-map/test-candidates", collect_public=False
        )
        self.assertIn("--resume", command)
        self.assertIn("--use-canonical-db-identities", command)
        self.assertNotIn("--collect-public", command)
        enabled = candidate_backfill_command(
            Path("python"), output=ROOT / "data/runtime/private-source-map/test-candidates", collect_public=True
        )
        self.assertIn("--collect-public", enabled)

    def test_partial_source_collection_blocks_before_canonical_write(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "source collection is partial"):
            require_candidate_backfill_ready(
                {
                    "classificationComplete": True,
                    "partial": True,
                    "candidates": [{"status": "unavailable"}],
                    "keylessPublicCollection": {"enabled": True, "partial": True},
                }
            )

    def test_unresolved_candidate_is_retry_evidence_not_a_global_ingest_blocker(self) -> None:
        self.assertEqual(
            require_candidate_backfill_ready(
                {
                    "classificationComplete": True,
                    "partial": True,
                    "rankingPromotable": False,
                    "candidates": [{"status": "resolved", "trackingStatus": "eligible", "identityStatus": "review", "snkItemId": 123}],
                }
            ),
            [],
        )
        seeds = require_candidate_backfill_ready(
            {
                "classificationComplete": True,
                "partial": False,
                "rankingPromotable": True,
                "candidates": [
                    {
                        "status": "resolved",
                        "trackingStatus": "eligible",
                        "identityStatus": "exact_confirmed",
                        "snkItemId": 123,
                    },
                    {
                        "status": "below-threshold",
                        "trackingStatus": "pre_entry_radar",
                        "identityStatus": "exact_confirmed",
                        "snkItemId": 456,
                    },
                ],
            }
        )
        self.assertEqual(seeds, [123, 456])

    def test_snk_refill_returns_all_newly_resolved_candidate_ids(self) -> None:
        command = snk_refill_command(
            Path("python"),
            candidate_manifest=ROOT / "candidate.json",
            output=ROOT / "worklist.json",
            seed_ids=[123, 456],
        )
        self.assertIn("--price-refill-candidates", command)
        self.assertIn("--price-refill-seeds", command)
        self.assertEqual(
            require_snk_refill_ready(
                {
                    "counts": {"review": 1, "resolved": 2},
                    "resolvedItemIds": [789, 123],
                    "cards": [
                        {"status": "resolved", "snkItemId": 123},
                        {"status": "resolved", "snkItemId": 789},
                        {"status": "review", "snkItemId": 456},
                    ],
                },
                [123],
            ),
            [123, 789],
        )

    def test_snk_refill_rejects_resolved_id_manifest_mismatch(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "do not match"):
            require_snk_refill_ready(
                {
                    "counts": {"resolved": 1},
                    "resolvedItemIds": [123, 789],
                    "cards": [{"status": "resolved", "snkItemId": 123}],
                },
                [123],
            )

    def test_full_profile_orders_private_gates_before_database_import(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_root = root / "candidates"
            candidate_root.mkdir()
            (candidate_root / "manifest.json").write_text(
                '{"classificationComplete":true,"partial":false,"rankingPromotable":true,"candidates":['
                '{"status":"resolved","trackingStatus":"eligible","identityStatus":"exact_confirmed","snkItemId":123}]}',
                encoding="utf-8",
            )
            snk_worklist = root / "snk-worklist.json"
            snk_worklist.write_text(
                '{"counts":{"review":0,"unavailable":0,"resolved":1},'
                '"resolvedItemIds":[123],"cards":[{"status":"resolved","snkItemId":123}]}',
                encoding="utf-8",
            )
            args = SimpleNamespace(
                mode="staging",
                full_backfill_candidate_out=candidate_root,
                full_backfill_snk_refill_out=snk_worklist,
                collect_public_candidates=False,
                require_gemrate_refresh=False,
                presentation_view="top300",
                json=False,
            )
            commands: list[list[str]] = []
            events: list[str] = []
            migration_offsets: list[int] = []
            with (
                mock.patch("backend.run_data_routing_tool", side_effect=lambda _python: events.append("routes")),
                mock.patch(
                    "backend.run_database_tool",
                    side_effect=lambda *_args: (
                        events.append("migrate"),
                        migration_offsets.append(len(commands)),
                    ),
                ),
                mock.patch("backend.subprocess.run", side_effect=lambda command, **_kwargs: commands.append(command)),
            ):
                run_full_backfill(Path("python"), args=args, config={"CARDZ_DB_HOST": "db"}, external=True)

        self.assertEqual(events, ["routes", "migrate"])
        self.assertEqual(migration_offsets, [3])
        self.assertIn("--bootstrap-only", commands[0])
        self.assertTrue(commands[1][-1].endswith("source_crosswalk.py"))
        self.assertTrue(commands[2][-1].endswith("tracked_universe.py"))
        self.assertIn("gemrate_candidate_backfill.py", " ".join(commands[3]))
        self.assertNotIn("--collect-public", commands[3])
        self.assertIn("fx_rates.py", " ".join(commands[4]))
        self.assertIn("snkrdunk_bulk.py", " ".join(commands[5]))
        self.assertIn("snk_market_data.py", " ".join(commands[6]))
        self.assertIn("tracked_universe.py", " ".join(commands[7]))
        self.assertIn("--candidate-manifest", commands[7])
        self.assertIn("--snk-worklist", commands[7])
        self.assertIn("run_daily.py", " ".join(commands[8]))
        self.assertIn("--backend-only", commands[8])
        self.assertIn("--active-universe", commands[8])
        self.assertIn("--skip-fx-refresh", commands[8])
        self.assertNotIn("--snk-run", commands[8])


if __name__ == "__main__":
    unittest.main()
