from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
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
        self.assertEqual(command[command.index("--view") + 1], "top300_boards")

    def test_import_forwards_the_exact_attempt_universe(self) -> None:
        universe = ROOT / "data/runtime/private-source-map/attempt-universe.json"
        with (
            mock.patch.object(
                sys,
                "argv",
                ["backend.py", "import", "--external-db", "--active-universe", str(universe)],
            ),
            mock.patch.object(backend, "ensure_python_environment", return_value=Path(sys.executable)),
            mock.patch.object(backend, "validate_external_transport"),
            mock.patch.object(backend, "runtime_config", return_value=({"CARDZ_DB_PASSWORD": "redacted-test"}, False)),
            mock.patch.object(backend, "run_database_tool") as run_database,
        ):
            self.assertEqual(backend.main(), 0)

        run_database.assert_called_once_with(
            Path(sys.executable),
            "import",
            {"CARDZ_DB_PASSWORD": "redacted-test"},
            "--active-universe",
            str(universe.resolve()),
        )

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

    def test_printing_plan_requires_safe_explicit_qc_run_before_config(self) -> None:
        with (
            mock.patch.object(
                sys, "argv", ["backend.py", "printing-plan", "--external-db"]
            ),
            mock.patch.object(backend, "read_only_runtime_config") as config,
            self.assertRaisesRegex(RuntimeError, "safe --qc-run"),
        ):
            backend.main()
        config.assert_not_called()

        with (
            mock.patch.object(
                sys,
                "argv",
                [
                    "backend.py",
                    "printing-plan",
                    "--external-db",
                    "--qc-run",
                    "../other",
                ],
            ),
            mock.patch.object(backend, "read_only_runtime_config") as config,
            self.assertRaisesRegex(RuntimeError, "safe --qc-run"),
        ):
            backend.main()
        config.assert_not_called()

    def test_printing_plan_delegates_exact_qc_run_without_apply(self) -> None:
        config = {"CARDZ_DB_PASSWORD": "redacted-test"}
        with (
            mock.patch.object(
                sys,
                "argv",
                [
                    "backend.py",
                    "printing-plan",
                    "--external-db",
                    "--qc-run",
                    "qc_fixture_01",
                ],
            ),
            mock.patch.object(backend, "validate_external_transport"),
            mock.patch.object(
                backend, "read_only_runtime_config", return_value=config
            ),
            mock.patch.object(backend, "run_database_tool") as run_database,
        ):
            self.assertEqual(backend.main(), 0)
        run_database.assert_called_once_with(
            Path(sys.executable),
            "printing-plan",
            config,
            "--qc-run",
            "qc_fixture_01",
        )

    def test_printing_materialize_defaults_to_rollback_and_forwards_apply(self) -> None:
        config = {"CARDZ_DB_PASSWORD": "redacted-test"}
        plan_sha = "a" * 64
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "candidate.json"
            candidate.write_text("{}", encoding="utf-8")
            common = [
                "backend.py",
                "printing-materialize",
                "--external-db",
                "--printing-candidate",
                str(candidate),
                "--printing-plan-sha256",
                plan_sha,
            ]
            with (
                mock.patch.object(sys, "argv", common),
                mock.patch.object(backend, "validate_external_transport"),
                mock.patch.object(
                    backend, "read_only_runtime_config", return_value=config
                ),
                mock.patch.object(backend, "run_database_tool") as dry_run,
            ):
                self.assertEqual(backend.main(), 0)
            dry_args = dry_run.call_args.args
            self.assertEqual(dry_args[:3], (Path(sys.executable), "printing-materialize", config))
            self.assertNotIn("--apply", dry_args)

            with (
                mock.patch.object(sys, "argv", [*common, "--apply"]),
                mock.patch.object(backend, "validate_external_transport"),
                mock.patch.object(
                    backend, "read_only_runtime_config", return_value=config
                ),
                mock.patch.object(backend, "run_database_tool") as apply_run,
            ):
                self.assertEqual(backend.main(), 0)
            self.assertEqual(apply_run.call_args.args[-1], "--apply")

    def test_printing_materialize_rejects_bad_hash_before_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "candidate.json"
            candidate.write_text("{}", encoding="utf-8")
            with (
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "backend.py",
                        "printing-materialize",
                        "--external-db",
                        "--printing-candidate",
                        str(candidate),
                        "--printing-plan-sha256",
                        "not-a-hash",
                    ],
                ),
                mock.patch.object(backend, "read_only_runtime_config") as config,
                self.assertRaisesRegex(RuntimeError, "64 lowercase hex"),
            ):
                backend.main()
            config.assert_not_called()

    def test_control_cli_actions_appear_in_help(self) -> None:
        import io

        parser_help = io.StringIO()
        with mock.patch.object(sys, "argv", ["backend.py", "--help"]):
            with mock.patch("sys.stdout", parser_help):
                with self.assertRaises(SystemExit) as raised:
                    backend.main()
        self.assertEqual(raised.exception.code, 0)
        text = parser_help.getvalue()
        for token in (
            "audit-scripts",
            "card-routes",
            "card-matrix",
            "field:",
            "script:",
            "consumer:",
            "contract:",
            "--baseline-id",
            "--cohort-sha256",
        ):
            self.assertIn(token, text)

    def _write_control_fixtures(self, root: Path) -> dict[str, Path]:
        baseline_id = "baseline_fixture_c1"
        cohort = "a" * 64
        field_lineage = {
            "schemaVersion": "1.0.0",
            "kind": "cardz-public-field-lineage-contract",
            "workItemId": "QC-A02-PUBLIC-FIELD-LINEAGE",
            "baselineId": baseline_id,
            "cohortSha256": cohort,
            "summary": {"fieldRowCount": 2},
            "fields": [
                {
                    "path": "cards[].id",
                    "mapping_status": "mapped",
                    "schema_type": "string",
                    "nullable_semantics": "not null",
                    "stale_semantics": "n/a",
                    "derived_semantics": "identity",
                    "fallback_semantics": "none",
                    "authority": "schema.ts",
                },
                {
                    "path": "cards[].marketCap",
                    "mapping_status": "mapped",
                    "schema_type": "number",
                    "nullable_semantics": "not null",
                    "stale_semantics": "window",
                    "derived_semantics": "price*pop",
                    "fallback_semantics": "none",
                    "authority": "schema.ts",
                },
                {
                    "path": "schemaVersion",
                    "mapping_status": "mapped",
                    "schema_type": "string",
                },
            ],
        }
        census = {
            "schemaVersion": "1.0.0",
            "kind": "cardz-frontend-consumer-census",
            "workItemId": "QC-A01-FRONTEND-CONSUMERS",
            "baselineId": baseline_id,
            "cohortSha256": cohort,
            "acceptance": {"unmappedConsumers": 0},
            "pages": [
                {
                    "consumerId": "page.home",
                    "route": "/",
                    "file": "apps/web/src/app/page.tsx",
                    "component": "MarketPage",
                    "dataSource": "snapshot",
                    "filters": [],
                    "sorts": [],
                    "limits": [],
                    "publicFieldsConsumed": ["cards[].id"],
                },
                {
                    "consumerId": "page.card_detail",
                    "route": "/card/[id]",
                    "file": "apps/web/src/app/card/[id]/page.tsx",
                    "component": "CardDetail",
                    "dataSource": "snapshot",
                    "filters": [],
                    "sorts": [],
                    "limits": [],
                    "publicFieldsConsumed": ["cards[].id", "cards[].marketCap"],
                },
            ],
            "apiRoutes": [
                {
                    "consumerId": "api.v1.market",
                    "route": "GET /api/v1/market",
                    "file": "apps/web/src/app/api/v1/market/route.ts",
                    "handler": "getMarketData",
                    "publicFieldsConsumed": ["cards[].id"],
                }
            ],
        }
        scripts = {
            "schemaVersion": "1.0",
            "kind": "cardz-script-lifecycle-registry",
            "workItemId": "QC-A04-SCRIPT-LIFECYCLE",
            "counts": {"executables": 1, "packageScripts": 0},
            "acceptance": {
                "pass": True,
                "unclassifiedExecutablesEqualZero": True,
                "tempAndEvidenceExecutionDenied": True,
                "eachProtectedWriterHasExactlyOneController": True,
            },
            "executables": [
                {
                    "path": "scripts/backend.py",
                    "classification": "controller",
                    "lifecycle": "active",
                    "executionPolicy": "ops_controlled",
                    "toolIds": [],
                }
            ],
            "packageScripts": [],
        }
        baseline = {
            "kind": "cardz-qc-immutable-baseline",
            "baselineId": baseline_id,
            "cohort": {
                "optionId": "C1",
                "qualifiedCount": 932,
                "universeCandidateSha256": cohort,
            },
        }
        paths = {
            "field_lineage": root / "field-lineage-contract.json",
            "frontend_consumer_census": root / "frontend-consumer-census.json",
            "script_lifecycle_registry": root / "script-lifecycle-registry.json",
            "db_fingerprint_contract": root / "db-fingerprint-contract.json",
            "live_db_state_fingerprint": root / "live-db-state-fingerprint.json",
        }
        paths["field_lineage"].write_text(json.dumps(field_lineage), encoding="utf-8")
        paths["frontend_consumer_census"].write_text(json.dumps(census), encoding="utf-8")
        paths["script_lifecycle_registry"].write_text(json.dumps(scripts), encoding="utf-8")
        paths["db_fingerprint_contract"].write_text(
            json.dumps({"kind": "cardz-db-state-fingerprint-contract", "contractId": "v1"}),
            encoding="utf-8",
        )
        paths["live_db_state_fingerprint"].write_text(
            json.dumps(
                {
                    "kind": "live-db-state-fingerprint",
                    "baselineId": baseline_id,
                    "cohortSha256": cohort,
                    "dbStateFingerprint": "b" * 64,
                    "readOnly": True,
                }
            ),
            encoding="utf-8",
        )
        baseline_path = root / "baseline-manifest.json"
        baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
        return {**paths, "baseline_manifest": baseline_path, "baseline_id": baseline_id, "cohort": cohort}  # type: ignore[dict-item]

    def test_explain_control_extensions_are_read_only_and_fixture_backed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixtures = self._write_control_fixtures(root)
            # fingerprint every fixture file before explain
            before = {path: path.read_bytes() for path in root.iterdir() if path.is_file()}
            with mock.patch.object(backend, "CONTROL_CONTRACT_PATHS", {
                "field_lineage": fixtures["field_lineage"],
                "frontend_consumer_census": fixtures["frontend_consumer_census"],
                "script_lifecycle_registry": fixtures["script_lifecycle_registry"],
                "db_fingerprint_contract": fixtures["db_fingerprint_contract"],
                "live_db_state_fingerprint": fixtures["live_db_state_fingerprint"],
            }):
                with (
                    mock.patch.object(
                        sys,
                        "argv",
                        [
                            "backend.py",
                            "explain",
                            "field:cards[].marketCap",
                            "--baseline-id",
                            fixtures["baseline_id"],
                            "--cohort-sha256",
                            fixtures["cohort"],
                        ],
                    ),
                    mock.patch("builtins.print") as output,
                ):
                    self.assertEqual(backend.main(), 0)
                field_doc = json.loads(output.call_args.args[0])
                self.assertEqual(field_doc["extension"], "field")
                self.assertEqual(field_doc["match"], "exact")
                self.assertEqual(field_doc["fields"][0]["path"], "cards[].marketCap")
                self.assertFalse(field_doc["sideEffect"]["mutatesDatabase"])

                with (
                    mock.patch.object(
                        sys,
                        "argv",
                        [
                            "backend.py",
                            "explain",
                            "script:scripts/backend.py",
                            "--baseline-id",
                            fixtures["baseline_id"],
                        ],
                    ),
                    mock.patch("builtins.print") as output,
                ):
                    self.assertEqual(backend.main(), 0)
                script_doc = json.loads(output.call_args.args[0])
                self.assertEqual(script_doc["extension"], "script")
                self.assertEqual(script_doc["match"], "exact")

                with (
                    mock.patch.object(
                        sys,
                        "argv",
                        ["backend.py", "explain", "consumer:page.card_detail"],
                    ),
                    mock.patch("builtins.print") as output,
                ):
                    self.assertEqual(backend.main(), 0)
                consumer_doc = json.loads(output.call_args.args[0])
                self.assertEqual(consumer_doc["extension"], "consumer")
                self.assertEqual(consumer_doc["consumers"][0]["route"], "/card/[id]")

                with (
                    mock.patch.object(
                        sys,
                        "argv",
                        ["backend.py", "explain", "contract:field-lineage"],
                    ),
                    mock.patch("builtins.print") as output,
                ):
                    self.assertEqual(backend.main(), 0)
                contract_doc = json.loads(output.call_args.args[0])
                self.assertEqual(contract_doc["extension"], "contract")
                self.assertEqual(contract_doc["fieldCount"], 3)

            after = {path: path.read_bytes() for path in root.iterdir() if path.is_file()}
            self.assertEqual(before, after)

    def test_explain_control_rejects_baseline_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixtures = self._write_control_fixtures(root)
            with mock.patch.object(backend, "CONTROL_CONTRACT_PATHS", {
                "field_lineage": fixtures["field_lineage"],
                "frontend_consumer_census": fixtures["frontend_consumer_census"],
                "script_lifecycle_registry": fixtures["script_lifecycle_registry"],
                "db_fingerprint_contract": fixtures["db_fingerprint_contract"],
                "live_db_state_fingerprint": fixtures["live_db_state_fingerprint"],
            }):
                with (
                    mock.patch.object(
                        sys,
                        "argv",
                        [
                            "backend.py",
                            "explain",
                            "field:cards[].id",
                            "--baseline-id",
                            "wrong-baseline",
                        ],
                    ),
                    self.assertRaisesRegex(RuntimeError, "baselineId mismatch"),
                ):
                    backend.main()

    def test_audit_scripts_card_routes_and_matrix_are_zero_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixtures = self._write_control_fixtures(root)
            sentinel = root / "must-not-change.txt"
            sentinel.write_text("immutable\n", encoding="utf-8")
            before = sentinel.read_bytes()
            contract_paths = {
                "field_lineage": fixtures["field_lineage"],
                "frontend_consumer_census": fixtures["frontend_consumer_census"],
                "script_lifecycle_registry": fixtures["script_lifecycle_registry"],
                "db_fingerprint_contract": fixtures["db_fingerprint_contract"],
                "live_db_state_fingerprint": fixtures["live_db_state_fingerprint"],
            }
            with mock.patch.object(backend, "CONTROL_CONTRACT_PATHS", contract_paths):
                with (
                    mock.patch.object(
                        sys,
                        "argv",
                        [
                            "backend.py",
                            "audit-scripts",
                            "--baseline-id",
                            fixtures["baseline_id"],
                        ],
                    ),
                    mock.patch("builtins.print") as output,
                ):
                    self.assertEqual(backend.main(), 0)
                audit_doc = json.loads(output.call_args.args[0])
                self.assertEqual(audit_doc["action"], "audit-scripts")
                self.assertEqual(audit_doc["mode"], "wave1-contract")
                self.assertFalse(audit_doc["sideEffect"]["mutatesDatabase"])
                self.assertEqual(audit_doc["acceptance"]["pass"], True)

                with (
                    mock.patch.object(
                        sys,
                        "argv",
                        [
                            "backend.py",
                            "card-routes",
                            "--baseline-id",
                            fixtures["baseline_id"],
                            "--cohort-sha256",
                            fixtures["cohort"],
                            "--card-id",
                            "card-fixture-1",
                        ],
                    ),
                    mock.patch("builtins.print") as output,
                ):
                    self.assertEqual(backend.main(), 0)
                routes_doc = json.loads(output.call_args.args[0])
                self.assertEqual(routes_doc["action"], "card-routes")
                self.assertGreaterEqual(routes_doc["count"], 2)
                self.assertEqual(routes_doc["instantiatedCardRoute"], "/card/card-fixture-1")
                self.assertTrue(any(row.get("perCard") for row in routes_doc["routes"]))

                with (
                    mock.patch.object(
                        sys,
                        "argv",
                        [
                            "backend.py",
                            "card-matrix",
                            "--baseline-id",
                            fixtures["baseline_id"],
                            "--cohort-sha256",
                            fixtures["cohort"],
                            "--baseline-manifest",
                            str(fixtures["baseline_manifest"]),
                            "--card-id",
                            "card-fixture-1",
                        ],
                    ),
                    mock.patch("builtins.print") as output,
                ):
                    self.assertEqual(backend.main(), 0)
                matrix_doc = json.loads(output.call_args.args[0])
                self.assertEqual(matrix_doc["action"], "card-matrix")
                self.assertEqual(matrix_doc["qualifiedCount"], 932)
                self.assertEqual(matrix_doc["cohortOption"], "C1")
                self.assertTrue(matrix_doc["skeletonOnly"])
                self.assertEqual(matrix_doc["dimensionCount"], 2)
                self.assertIn("cards[].marketCap", matrix_doc["rowSkeleton"]["cells"])

            self.assertEqual(sentinel.read_bytes(), before)
            # Source contracts unchanged.
            for key in (
                "field_lineage",
                "frontend_consumer_census",
                "script_lifecycle_registry",
            ):
                self.assertTrue(contract_paths[key].is_file())


if __name__ == "__main__":
    unittest.main()
