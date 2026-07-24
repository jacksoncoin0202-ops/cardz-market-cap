from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
import sys
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("cardz_backend", REPO_ROOT / "scripts/backend.py")
assert SPEC and SPEC.loader
backend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backend)


class BackendEntrypointTests(unittest.TestCase):
    def test_external_database_requires_explicit_process_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            local_config = Path(temporary) / "backend.env"
            local_config.write_text(
                "CARDZ_DB_HOST=127.0.0.1\nCARDZ_DB_PORT=3308\n"
                "CARDZ_DB_NAME=local\nCARDZ_DB_USER=local\nCARDZ_DB_PASSWORD=local-secret\n",
                encoding="utf-8",
            )
            with mock.patch.object(backend, "CONFIG_PATH", local_config), mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "CARDZ_DB_HOST"):
                    backend.runtime_config(external=True)

    def test_external_database_uses_only_injected_values(self) -> None:
        injected = {
            "CARDZ_DB_HOST": "db.example.internal",
            "CARDZ_DB_PORT": "3306",
            "CARDZ_DB_NAME": "cardz",
            "CARDZ_DB_USER": "runner",
            "CARDZ_DB_PASSWORD": "injected-secret",
        }
        with mock.patch.dict(os.environ, injected, clear=True):
            config, has_env_file = backend.runtime_config(external=True)
        self.assertEqual(config, injected)
        self.assertFalse(has_env_file)

    def test_production_external_database_requires_tls_ca(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "CARDZ_DB_SSL_CA"):
                backend.validate_external_transport(external=True, mode="production")
        backend.validate_external_transport(external=False, mode="production")
        backend.validate_external_transport(external=True, mode="staging")

    def test_backend_daily_ignores_publication_bucket_environment(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "CARDZ_STAGING_R2_BUCKET": "must-not-reach-backend-only",
                "CARDZ_PRODUCTION_R2_BUCKET": "must-not-reach-backend-only",
            },
            clear=True,
        ):
            environment = backend.daily_environment({"CARDZ_DB_HOST": "db"}, external=True)
        self.assertNotIn("CARDZ_STAGING_R2_BUCKET", environment)
        self.assertNotIn("CARDZ_PRODUCTION_R2_BUCKET", environment)
        self.assertEqual(environment["CARDZ_DB_MODE"], "external")

    def test_daily_runs_discovery_before_database_or_daily_pipeline(self) -> None:
        events: list[str] = []
        with (
            mock.patch.object(sys, "argv", ["backend.py", "daily", "--external-db"]),
            mock.patch.object(backend, "validate_external_transport"),
            mock.patch.object(backend, "runtime_config", return_value=({"CARDZ_DB_HOST": "db"}, False)),
            mock.patch.object(backend, "ensure_python_environment", return_value=Path(sys.executable)),
            mock.patch.object(backend, "run_data_routing_tool", side_effect=lambda _python: events.append("routes")),
            mock.patch.object(backend, "run_discovery_tool", side_effect=lambda _python: events.append("discovery")),
            mock.patch.object(backend, "run_coverage_audit", side_effect=lambda *_args, **_kwargs: events.append("audit")),
            mock.patch.object(backend, "run_database_tool", side_effect=lambda _python, action, _env: events.append(action)),
            mock.patch.object(backend.subprocess, "run", side_effect=lambda *args, **kwargs: events.append("daily")),
        ):
            self.assertEqual(backend.main(), 0)
        self.assertEqual(events, ["routes", "discovery", "migrate", "daily"])

    def test_discovery_failure_stops_daily_before_database_changes(self) -> None:
        with (
            mock.patch.object(sys, "argv", ["backend.py", "daily", "--external-db"]),
            mock.patch.object(backend, "validate_external_transport"),
            mock.patch.object(backend, "runtime_config", return_value=({"CARDZ_DB_HOST": "db"}, False)),
            mock.patch.object(backend, "ensure_python_environment", return_value=Path(sys.executable)),
            mock.patch.object(backend, "run_data_routing_tool"),
            mock.patch.object(backend, "run_discovery_tool", side_effect=RuntimeError("discovery failed")),
            mock.patch.object(backend, "run_coverage_audit") as coverage_audit,
            mock.patch.object(backend, "run_database_tool") as database_tool,
            mock.patch.object(backend.subprocess, "run") as subprocess_run,
        ):
            with self.assertRaisesRegex(RuntimeError, "discovery failed"):
                backend.main()
        database_tool.assert_not_called()
        coverage_audit.assert_not_called()
        subprocess_run.assert_not_called()

    def test_production_backend_daily_does_not_treat_a_presentation_view_as_db_storage_gate(self) -> None:
        events: list[str] = []
        with (
            mock.patch.object(sys, "argv", ["backend.py", "daily", "--external-db", "--mode", "production"]),
            mock.patch.object(backend, "validate_external_transport"),
            mock.patch.object(backend, "runtime_config", return_value=({"CARDZ_DB_HOST": "db"}, False)),
            mock.patch.object(backend, "ensure_python_environment", return_value=Path(sys.executable)),
            mock.patch.object(backend, "run_data_routing_tool"),
            mock.patch.object(backend, "run_discovery_tool"),
            mock.patch.object(backend, "run_coverage_audit") as audit,
            mock.patch.object(backend, "run_database_tool", side_effect=lambda *_args: events.append("db")),
            mock.patch.object(backend.subprocess, "run", side_effect=lambda *_args, **_kwargs: events.append("daily")),
        ):
            self.assertEqual(backend.main(), 0)
        audit.assert_not_called()
        self.assertEqual(events, ["db", "daily"])

    def test_audit_does_not_require_database_or_docker(self) -> None:
        with (
            mock.patch.object(sys, "argv", ["backend.py", "audit", "--require-global-top350"]),
            mock.patch.object(backend, "ensure_python_environment", return_value=Path(sys.executable)),
            mock.patch.object(backend, "run_coverage_audit") as audit,
            mock.patch.object(backend, "runtime_config") as runtime_config,
            mock.patch.object(backend, "DockerRuntime") as docker_runtime,
        ):
            self.assertEqual(backend.main(), 0)
        audit.assert_called_once_with(
            Path(sys.executable),
            snk_run=None,
            required_presentation_view="top350",
        )
        runtime_config.assert_not_called()
        docker_runtime.assert_not_called()

    def test_routes_action_does_not_require_database_or_docker(self) -> None:
        with (
            mock.patch.object(sys, "argv", ["backend.py", "routes"]),
            mock.patch.object(backend, "ensure_python_environment", return_value=Path(sys.executable)),
            mock.patch.object(backend, "run_data_routing_tool") as routes,
            mock.patch.object(backend, "runtime_config") as runtime_config,
            mock.patch.object(backend, "DockerRuntime") as docker_runtime,
        ):
            self.assertEqual(backend.main(), 0)
        routes.assert_called_once_with(Path(sys.executable))
        runtime_config.assert_not_called()
        docker_runtime.assert_not_called()


if __name__ == "__main__":
    unittest.main()
