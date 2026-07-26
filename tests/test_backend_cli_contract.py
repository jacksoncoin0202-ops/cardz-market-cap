from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("cardz_backend_contract", ROOT / "scripts/backend.py")
assert SPEC and SPEC.loader
backend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backend)


class BackendCliContractTests(unittest.TestCase):
    def test_doctor_is_read_only_and_reports_required_local_tools(self) -> None:
        with (
            mock.patch.object(sys, "argv", ["backend.py", "doctor"]),
            mock.patch.object(backend, "ensure_python_environment") as environment,
            mock.patch("builtins.print") as output,
        ):
            self.assertEqual(backend.main(), 0)
        environment.assert_not_called()
        document = json.loads(output.call_args.args[0])
        self.assertEqual(document["action"], "doctor")
        self.assertIn("runDaily", document["scripts"])

    def test_registry_explain_graph_and_doc_check_are_read_only(self) -> None:
        registry = {"schemaVersion": "fixture"}
        routing = mock.Mock()
        routing.load_registry.return_value = registry
        routing.render_registry.return_value = "rendered\n"
        routing.explain_identifier.return_value = {"identifier": "psa10_population", "kind": "route"}
        routing.check_registry_docs.return_value = []
        with mock.patch.object(backend, "load_data_routing_module", return_value=routing):
            with (
                mock.patch.object(sys, "argv", ["backend.py", "registry", "--json"]),
                mock.patch("builtins.print") as output,
            ):
                self.assertEqual(backend.main(), 0)
                self.assertEqual(output.call_args.args[0], "rendered\n")
            with (
                mock.patch.object(sys, "argv", ["backend.py", "explain", "psa10_population"]),
                mock.patch("builtins.print"),
            ):
                self.assertEqual(backend.main(), 0)
                routing.explain_identifier.assert_called_with("psa10_population")
            with (
                mock.patch.object(sys, "argv", ["backend.py", "graph", "--format", "html"]),
                mock.patch("builtins.print"),
            ):
                self.assertEqual(backend.main(), 0)
                routing.render_registry.assert_called_with(registry, "html")
            with (
                mock.patch.object(sys, "argv", ["backend.py", "generate-docs", "--check"]),
                mock.patch("builtins.print"),
            ):
                self.assertEqual(backend.main(), 0)
                routing.check_registry_docs.assert_called_once()

    def test_daily_defaults_to_backend_only_and_publish_requires_explicit_flag(self) -> None:
        events: list[list[str]] = []
        coverage_audit = mock.Mock()
        patches = (
            mock.patch.object(backend, "validate_external_transport"),
            mock.patch.object(backend, "runtime_config", return_value=({"CARDZ_DB_HOST": "db"}, False)),
            mock.patch.object(backend, "ensure_python_environment", return_value=Path(sys.executable)),
            mock.patch.object(backend, "run_data_routing_tool"),
            mock.patch.object(backend, "run_discovery_tool"),
            mock.patch.object(backend, "run_coverage_audit", coverage_audit),
            mock.patch.object(backend, "run_database_tool"),
            mock.patch.object(backend.subprocess, "run", side_effect=lambda command, **_kwargs: events.append(command)),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
            with mock.patch.object(sys, "argv", ["backend.py", "daily", "--external-db"]):
                self.assertEqual(backend.main(), 0)
            with mock.patch.object(sys, "argv", ["backend.py", "daily", "--external-db", "--publish"]):
                self.assertEqual(backend.main(), 0)
        self.assertIn("--backend-only", events[0])
        self.assertNotIn("--backend-only", events[1])
        coverage_audit.assert_not_called()

    def test_daily_local_publish_forwards_local_only_and_withholds_the_bucket(self) -> None:
        """--local-only must reach run_daily.py *and* strip the ambient bucket.

        run_daily.py resolves CARDZ_*_R2_BUCKET from its own environment and then
        raises on "--local-only cannot be combined with an R2 bucket", so leaking
        the bucket into the child turns the scheduled daily run into a hard failure.
        A remote publish is the only shape allowed to inherit it.
        """

        calls: list[tuple[list[str], dict[str, str]]] = []
        patches = (
            mock.patch.object(backend, "validate_external_transport"),
            mock.patch.object(backend, "runtime_config", return_value=({"CARDZ_DB_HOST": "db"}, False)),
            mock.patch.object(backend, "ensure_python_environment", return_value=Path(sys.executable)),
            mock.patch.object(backend, "run_data_routing_tool"),
            mock.patch.object(backend, "run_discovery_tool"),
            mock.patch.object(backend, "run_database_tool"),
            mock.patch.object(
                backend.subprocess,
                "run",
                side_effect=lambda command, **kwargs: calls.append((command, kwargs.get("env", {}))),
            ),
            mock.patch.dict(os.environ, {"CARDZ_PRODUCTION_R2_BUCKET": "cardz-production"}, clear=False),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
            with mock.patch.object(
                sys, "argv", ["backend.py", "daily", "--external-db", "--publish", "--local-only"]
            ):
                self.assertEqual(backend.main(), 0)
            with mock.patch.object(sys, "argv", ["backend.py", "daily", "--external-db", "--publish"]):
                self.assertEqual(backend.main(), 0)

        local_command, local_env = calls[0]
        self.assertIn("--local-only", local_command)
        self.assertNotIn("--backend-only", local_command)
        self.assertNotIn("CARDZ_PRODUCTION_R2_BUCKET", local_env)
        self.assertNotIn("CARDZ_STAGING_R2_BUCKET", local_env)

        remote_command, remote_env = calls[1]
        self.assertNotIn("--local-only", remote_command)
        self.assertEqual(remote_env["CARDZ_PRODUCTION_R2_BUCKET"], "cardz-production")

    def test_local_only_without_publish_is_rejected_instead_of_silently_ignored(self) -> None:
        with mock.patch.object(sys, "argv", ["backend.py", "daily", "--external-db", "--local-only"]):
            with self.assertRaisesRegex(RuntimeError, "--local-only only qualifies daily --publish"):
                backend.main()

    def test_seed_restore_delegates_to_empty_database_restore_only_after_confirmation(self) -> None:
        archive = ROOT / "data/private/canonical-seed.sql.gz"
        with (
            mock.patch.object(
                sys,
                "argv",
                ["backend.py", "seed-restore", "--seed-archive", str(archive), "--allow-empty-db"],
            ),
            mock.patch.object(backend, "ensure_python_environment", return_value=Path(sys.executable)),
            mock.patch.object(backend.subprocess, "run") as run,
        ):
            self.assertEqual(backend.main(), 0)
        command = run.call_args.args[0]
        self.assertIn("restore", command)
        self.assertIn("--allow-empty-db", command)
        self.assertNotIn("--seed-target", command)

        with (
            mock.patch.object(sys, "argv", ["backend.py", "seed-restore", "--seed-archive", str(archive)]),
            mock.patch.object(backend, "ensure_python_environment", return_value=Path(sys.executable)),
            self.assertRaisesRegex(RuntimeError, "allow-empty-db"),
        ):
            backend.main()

    def test_run_daily_post_derive_audit_requires_a_current_snk_run(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "SNK PSA 10"):
            backend.daily_audit_command(Path(sys.executable), None, production=False)
        command = backend.daily_audit_command(
            Path(sys.executable),
            ROOT / "data/runtime/source.jsonl",
            required_presentation_view="top300",
        )
        self.assertEqual(command[-2:], ["--require-presentation-view", "top300"])

    def test_export_snapshot_uses_canonical_database_exporter_not_g10_builder(self) -> None:
        output = ROOT / "data/runtime/test-canonical-snapshot.json"
        with (
            mock.patch.object(
                sys,
                "argv",
                ["backend.py", "export-snapshot", "--external-db", "--snapshot-output", str(output)],
            ),
            mock.patch.object(backend, "ensure_python_environment", return_value=Path(sys.executable)),
            mock.patch.object(backend, "validate_external_transport"),
            mock.patch.object(backend, "runtime_config", return_value=({"CARDZ_DB_PASSWORD": "redacted-test"}, False)),
            mock.patch.object(backend.subprocess, "run") as run,
        ):
            self.assertEqual(backend.main(), 0)
        command = run.call_args.args[0]
        self.assertIn(str(backend.CANONICAL_PUBLIC_SNAPSHOT_PATH), command)
        self.assertNotIn("g10_public_snapshot.py", " ".join(command))
        self.assertIn(str(output.resolve()), command)
        self.assertEqual(command[command.index("--view") + 1], "top300")

    def test_private_reserve_view_cannot_be_exported_or_published(self) -> None:
        with (
            mock.patch.object(
                sys,
                "argv",
                ["backend.py", "export-snapshot", "--external-db", "--presentation-view", "reserve50"],
            ),
            mock.patch.object(backend, "ensure_python_environment", return_value=Path(sys.executable)),
            mock.patch.object(backend, "validate_external_transport"),
            mock.patch.object(backend, "runtime_config", return_value=({"CARDZ_DB_PASSWORD": "redacted-test"}, False)),
            self.assertRaisesRegex(RuntimeError, "private"),
        ):
            backend.main()

    def test_daily_publish_uses_canonical_database_exporter(self) -> None:
        source = (ROOT / "pipelines/run_daily.py").read_text(encoding="utf-8")
        self.assertIn("canonical_public_snapshot.py", source)
        self.assertNotIn("build_snapshot(", source)


if __name__ == "__main__":
    unittest.main()
