from __future__ import annotations

import importlib.util
import sys
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from run_daily import (  # noqa: E402
    backend_database_command,
    build_engine_input_material,
    canonical_db_qc_command,
    canonical_db_qc_failure_commands,
    enforce_gemrate_refresh,
    gemrate_daily_command,
    load_evaluation_id,
    market_alert_evaluation_command,
    mark_market_evaluation_passed_command,
    post_derive_audit_command,
    price_sales_gate_evaluation_command,
    requires_remote_publish,
    resolve_run_profile,
    run_checked,
    runtime_snapshot_from_pointer,
    shared_engine_entrypoint,
    source_attempt_id,
)
from run_receipts import (  # noqa: E402
    STAGES,
    assert_zero_mutation_side_effects,
    compute_input_fingerprint,
    engine_profile,
    load_completed_stages,
    next_resume_stage,
    plan_engine_run,
    profile_selector_diff,
    profiles_share_engine,
    same_input_is_noop,
    stages_to_execute,
    write_input_fingerprint,
    write_stage_receipt,
)

BACKEND_SPEC = importlib.util.spec_from_file_location(
    "cardz_backend_orchestration", ROOT / "scripts/backend.py"
)
assert BACKEND_SPEC and BACKEND_SPEC.loader
backend = importlib.util.module_from_spec(BACKEND_SPEC)
BACKEND_SPEC.loader.exec_module(backend)


class RunDailyOrchestrationTests(unittest.TestCase):
    def test_bootstrap_only_never_requires_remote_publish_configuration(self) -> None:
        self.assertFalse(
            requires_remote_publish(
                backend_only=False,
                bootstrap_only=True,
                local_only=False,
            )
        )
        self.assertTrue(
            requires_remote_publish(
                backend_only=False,
                bootstrap_only=False,
                local_only=False,
            )
        )

    def test_post_derive_audit_uses_only_the_current_snk_run(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "SNK PSA 10"):
            post_derive_audit_command(None, production=False)
        command = post_derive_audit_command(
            ROOT / "data/runtime/private-source-runs/current/snk-psa10.jsonl",
            required_presentation_view="top300",
            active_universe=ROOT / "data/runtime/private-source-map/full-backfill-active.json",
        )
        self.assertIn("--snk-run", command)
        self.assertEqual(command[-2:], ["--require-presentation-view", "top300"])
        self.assertIn("--discovery-complete", command)
        self.assertIn("--active-universe", command)

    def test_post_derive_audit_defaults_to_the_unified_public_view_gate(self) -> None:
        command = post_derive_audit_command(
            ROOT / "data/runtime/private-source-runs/current/snk-psa10.jsonl",
        )
        self.assertEqual(command[-2:], ["--require-presentation-view", "top300_boards"])

    def test_gemrate_daily_refresh_does_not_depend_on_a_trial_key(self) -> None:
        ids = ROOT / "data/runtime/private-source-map/tracked-gemrate-ids.txt"
        universe = ROOT / "data/runtime/private-source-map/tracked-universe.json"
        mirror = ROOT / "integrations/grade10/data"
        command = gemrate_daily_command(ids, universe, mirror)
        self.assertEqual(command[2], "daily")
        self.assertIn(str(ids), command)
        self.assertIn(str(universe), command)
        self.assertIn(str(mirror), command)
        self.assertNotIn("GEMRATE_API_KEY", " ".join(command))

    def test_same_day_invocations_receive_distinct_attempt_ids(self) -> None:
        first = source_attempt_id(datetime(2026, 7, 28, 0, 30, 0, 1, tzinfo=timezone.utc))
        second = source_attempt_id(datetime(2026, 7, 28, 0, 30, 0, 2, tzinfo=timezone.utc))
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("sources_20260728T"))
        self.assertTrue(second.startswith("sources_20260728T"))

    def test_required_gemrate_refresh_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "required GemRate"):
            enforce_gemrate_refresh(required=True, refreshed=False)
        enforce_gemrate_refresh(required=False, refreshed=False)
        enforce_gemrate_refresh(required=True, refreshed=True)

    def test_database_actions_use_the_resolved_active_universe(self) -> None:
        active = ROOT / "data/runtime/private-source-map/full-backfill-active.json"
        command = backend_database_command(ROOT / "scripts/backend.py", "import", active)
        self.assertEqual(command[-2:], ["--active-universe", str(active.resolve())])

    def test_public_path_uses_the_same_run_id_for_canonical_db_qc(self) -> None:
        command = canonical_db_qc_command("daily_20260729T010203000000Z")
        self.assertEqual(
            command[command.index("--run-id") + 1],
            "daily_20260729T010203000000Z",
        )
        self.assertEqual(
            command[command.index("--release-profile") + 1],
            "relaxed-launch-v1",
        )
        self.assertEqual(Path(command[1]).name, "canonical_db_qc.py")

    def test_blocked_canonical_qc_becomes_price_and_sales_agent_work(self) -> None:
        report = ROOT / "data/runtime/private-reports/canonical-db-qc/run-7/report.json"
        commands = canonical_db_qc_failure_commands(report, "run-7")
        self.assertEqual(Path(commands[0][1]).name, "qc_failure_sync.py")
        self.assertEqual(commands[0][-2:], ["--write", "--summary-only"])
        self.assertEqual(
            [command[command.index("--stage") + 1] for command in commands[1:]],
            ["psa10_price", "sales"],
        )
        for command in commands[1:]:
            self.assertEqual(Path(command[1]).name, "failure_ledger.py")
            self.assertEqual(
                command[command.index("--script") + 1],
                "pipelines/qc_failure_sync.py",
            )
            self.assertIn("run-7-", command[-1])

    def test_price_sales_gate_is_exported_from_the_immutable_qc_report(self) -> None:
        report = ROOT / "data/runtime/private-reports/canonical-db-qc/run-7/report.json"
        result = report.with_name("price-sales-gate.json")
        command = price_sales_gate_evaluation_command(
            report,
            "a" * 64,
            result,
        )
        self.assertIn("--dry-run", command)
        self.assertIn("--price-sales-only", command)
        self.assertEqual(
            command[command.index("--universe-qc-report") + 1],
            str(report.resolve()),
        )
        self.assertEqual(
            command[command.index("--result-out") + 1],
            str(result.resolve()),
        )

    def test_expected_qc_block_is_not_recorded_as_a_generic_subprocess_failure(self) -> None:
        error = __import__("subprocess").CalledProcessError(1, ["python", "canonical_db_qc.py"])
        with (
            mock.patch("run_daily.subprocess.run", side_effect=error),
            mock.patch("run_daily.record_failure") as record_failure,
        ):
            result = run_checked(
                ["python", "canonical_db_qc.py"],
                cwd=ROOT,
                timeout=10,
                allowed_returncodes=(1,),
            )
        self.assertEqual(result, 1)
        record_failure.assert_not_called()

    def test_qc_blockers_are_projected_before_public_export_is_stopped(self) -> None:
        source = (ROOT / "pipelines" / "run_daily.py").read_text(encoding="utf-8")
        main = source[source.index("def main()") :]
        qc = main.index("canonical_db_qc_command(pipeline_run_id, args.release_profile)")
        strict_guard = main.index('if args.release_profile == "strict-v1":')
        price_sales = main.index("price_sales_gate_evaluation_command(")
        project = main.index("canonical_db_qc_failure_commands(")
        blocked = main.index("canonical DB QC blocked")
        export_candidate = main.index("export_command =")
        self.assertLess(qc, price_sales)
        self.assertLess(strict_guard, price_sales)
        self.assertLess(price_sales, project)
        self.assertLess(qc, project)
        self.assertLess(project, blocked)
        self.assertLess(blocked, export_candidate)

    def test_public_qc_and_publisher_share_the_same_run_db_qc_receipt(self) -> None:
        source = (ROOT / "pipelines" / "run_daily.py").read_text(encoding="utf-8")
        self.assertEqual(source.count('"--db-qc-receipt"'), 3)
        self.assertEqual(source.count("str(canonical_db_qc_receipt)"), 3)

    def test_alert_evaluation_receipt_binds_the_post_audit_pass_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary) / "evaluation.json"
            command = market_alert_evaluation_command(receipt)
            self.assertEqual(command[-2:], ["--result-out", str(receipt.resolve())])
            receipt.write_text(json.dumps({"evaluationId": 77}), encoding="utf-8")
            self.assertEqual(load_evaluation_id(receipt), 77)
            self.assertEqual(
                mark_market_evaluation_passed_command(77)[-2:],
                ["--mark-passed-evaluation-id", "77"],
            )

    def test_evaluation_receipt_fails_closed_when_id_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary) / "evaluation.json"
            receipt.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "evaluationId"):
                load_evaluation_id(receipt)

    def test_runtime_pointer_resolves_the_immutable_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = root / "generations" / "gen-1" / "snapshot.json"
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text("{}", encoding="utf-8")
            (root / "latest.json").write_text(
                json.dumps({"snapshotKey": "generations/gen-1/snapshot.json"}),
                encoding="utf-8",
            )
            self.assertEqual(runtime_snapshot_from_pointer(root), snapshot.resolve())

    def test_runtime_pointer_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "latest.json").write_text(
                json.dumps({"snapshotKey": "../seed-snapshot.json"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "escapes"):
                runtime_snapshot_from_pointer(root)

    def test_custom_publish_out_pointer_is_forwarded_to_image_mutation_guard(self) -> None:
        source = (ROOT / "pipelines" / "run_daily.py").read_text(encoding="utf-8")
        ensure_start = source.index('str(ROOT / "pipelines" / "ensure_std_card_images.py")')
        ensure_end = source.index("cwd=ROOT", ensure_start)
        ensure_command = source[ensure_start:ensure_end]
        self.assertIn('"--pointer"', ensure_command)
        self.assertIn('str(args.publish_out.resolve() / "latest.json")', ensure_command)

    def test_full_and_incremental_share_one_engine_entrypoint_and_stages(self) -> None:
        self.assertTrue(profiles_share_engine("full", "incremental"))
        full = engine_profile("full")
        incremental = engine_profile("incremental")
        self.assertEqual(full["entrypoint"], shared_engine_entrypoint())
        self.assertEqual(incremental["entrypoint"], shared_engine_entrypoint())
        self.assertEqual(full["stages"], list(STAGES))
        self.assertEqual(incremental["stages"], list(STAGES))
        self.assertEqual(full["sharedPath"], incremental["sharedPath"])
        self.assertEqual(resolve_run_profile("full-backfill"), "full")
        self.assertEqual(resolve_run_profile("daily"), "incremental")
        full_cmd = backend.shared_engine_daily_command(
            Path("python"), mode="staging", profile="full", backend_only=True
        )
        inc_cmd = backend.shared_engine_daily_command(
            Path("python"), mode="staging", profile="incremental", backend_only=True
        )
        self.assertIn(str(backend.DAILY_RUNTIME_PATH), full_cmd)
        self.assertIn(str(backend.DAILY_RUNTIME_PATH), inc_cmd)
        self.assertEqual(full_cmd[full_cmd.index("--run-profile") + 1], "full")
        self.assertEqual(inc_cmd[inc_cmd.index("--run-profile") + 1], "incremental")
        self.assertEqual(
            full_cmd[full_cmd.index("--release-profile") + 1],
            "relaxed-launch-v1",
        )
        # Only selector/cursor/range/freshness may differ between profiles.
        diff = profile_selector_diff()
        self.assertEqual(set(diff), {"selector", "cursorPolicy", "rangePolicy", "freshnessPolicy"})
        self.assertNotEqual(full["selector"], incremental["selector"])

    def test_partial_retry_resumes_without_replaying_completed_stages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "stages"
            at = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
            material = build_engine_input_material(
                profile_id="incremental",
                active_universe=None,
                mode="staging",
                backend_only=True,
            )
            fingerprint = compute_input_fingerprint(material)
            write_input_fingerprint(
                root,
                run_id="daily_resume_1",
                profile_id="incremental",
                fingerprint=fingerprint,
                material=material,
            )
            for stage in ("ACQUIRED", "VERIFIED", "INGESTED"):
                write_stage_receipt(
                    root,
                    run_id="daily_resume_1",
                    stage=stage,
                    counts={"n": 1},
                    completed_at=at,
                )
            self.assertEqual(load_completed_stages(root), ["ACQUIRED", "VERIFIED", "INGESTED"])
            self.assertEqual(next_resume_stage(root), "DERIVED")
            remaining = stages_to_execute(root, resume=True)
            self.assertEqual(remaining, ["DERIVED", "QC_PASSED", "PUBLISHED"])
            self.assertNotIn("ACQUIRED", remaining)
            self.assertNotIn("VERIFIED", remaining)
            self.assertNotIn("INGESTED", remaining)
            plan = plan_engine_run(
                "incremental",
                receipt_root=root,
                input_fingerprint=fingerprint,
                resume=True,
                dry_run=True,
                allow_publish=False,
            )
            self.assertEqual(plan["status"], "dry_run_plan")
            self.assertEqual(plan["resumeFrom"], "DERIVED")
            self.assertEqual(plan["stagesCompleted"], ["ACQUIRED", "VERIFIED", "INGESTED"])
            # Different fingerprint must not resume (downstream cursors stay put).
            with self.assertRaisesRegex(RuntimeError, "different input fingerprint"):
                plan_engine_run(
                    "incremental",
                    receipt_root=root,
                    input_fingerprint="0" * 64,
                    resume=True,
                    dry_run=True,
                )

    def test_same_input_rerun_is_noop_and_identical_receipt_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "stages"
            at = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
            material = {"cohort": "C1", "selector": "due_delta_tracked_universe"}
            fingerprint = compute_input_fingerprint(material)
            write_input_fingerprint(
                root,
                run_id="daily_noop_1",
                profile_id="incremental",
                fingerprint=fingerprint,
                material=material,
            )
            for stage in STAGES:
                path = write_stage_receipt(
                    root,
                    run_id="daily_noop_1",
                    stage=stage,
                    counts={"cards": 3},
                    completed_at=at,
                )
                # Same payload rewrite is a no-op (immutable + identical).
                again = write_stage_receipt(
                    root,
                    run_id="daily_noop_1",
                    stage=stage,
                    counts={"cards": 3},
                    completed_at=at,
                )
                self.assertEqual(path, again)
            self.assertTrue(same_input_is_noop(root, input_fingerprint=fingerprint))
            plan = plan_engine_run(
                "incremental",
                receipt_root=root,
                input_fingerprint=fingerprint,
                resume=True,
                dry_run=True,
            )
            self.assertEqual(plan["status"], "noop")
            self.assertEqual(plan["stagesToRun"], [])
            self.assertEqual(plan["reason"], "same_input_already_complete")
            self.assertFalse(plan["sideEffects"]["pointer_write"])
            self.assertFalse(plan["sideEffects"]["timer_enable"])
            self.assertFalse(plan["sideEffects"]["database_apply"])

    def test_dry_run_plan_has_zero_pointer_and_timer_mutation(self) -> None:
        full_plan = plan_engine_run(
            "full",
            dry_run=True,
            allow_publish=False,
            allow_timer=False,
            allow_database_apply=False,
        )
        inc_plan = plan_engine_run(
            "incremental",
            dry_run=True,
            allow_publish=False,
            allow_timer=False,
            allow_database_apply=False,
        )
        for plan in (full_plan, inc_plan):
            assert_zero_mutation_side_effects(plan)
            self.assertTrue(plan["dryRun"])
            self.assertFalse(plan["sideEffects"]["pointer_write"])
            self.assertFalse(plan["sideEffects"]["timer_enable"])
            self.assertFalse(plan["sideEffects"]["database_apply"])
            self.assertFalse(plan["sideEffects"]["publish"])
            self.assertIn("PUBLISHED", plan["stagesGated"])
        with self.assertRaisesRegex(RuntimeError, "timer_enable"):
            plan_engine_run("incremental", dry_run=True, allow_timer=True)
        dry_cmd = backend.shared_engine_daily_command(
            Path("python"),
            mode="staging",
            profile="incremental",
            dry_run=True,
        )
        self.assertIn("--dry-run", dry_cmd)
        self.assertIn("--run-profile", dry_cmd)
        self.assertNotIn("--publish", dry_cmd)

    def test_run_daily_dry_run_cli_emits_plan_without_side_effects(self) -> None:
        import run_daily as daily_mod

        argv = [
            "run_daily.py",
            "--mode",
            "staging",
            "--run-profile",
            "incremental",
            "--dry-run",
            "--backend-only",
        ]
        with mock.patch.object(sys, "argv", argv), mock.patch("builtins.print") as output:
            self.assertEqual(daily_mod.main(), 0)
        document = json.loads(output.call_args.args[0])
        self.assertEqual(document["runProfile"], "incremental")
        self.assertFalse(document["pointerWrite"])
        self.assertFalse(document["timerEnable"])
        assert_zero_mutation_side_effects(document["enginePlan"])


if __name__ == "__main__":
    unittest.main()
