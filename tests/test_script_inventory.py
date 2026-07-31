#!/usr/bin/env python3
"""Acceptance tests for QC-A04 script lifecycle inventory."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from script_inventory import (  # noqa: E402
    DENIED_PREFIXES,
    PROTECTED_WRITERS,
    build_inventory,
    export_artifacts,
    is_execution_denied,
    validate_inventory,
)


class ScriptInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inventory = build_inventory(root=ROOT)
        cls.errors = validate_inventory(cls.inventory)
        cls.by_path = {
            str(item["path"]): item for item in cls.inventory["executables"]
        }

    def test_acceptance_pass(self) -> None:
        self.assertEqual(self.errors, [], msg=self.errors)
        self.assertTrue(self.inventory["acceptance"]["pass"])

    def test_unclassified_executables_equal_zero(self) -> None:
        unclassified = [
            item["path"]
            for item in self.inventory["executables"]
            if not item.get("classification")
        ]
        self.assertEqual(unclassified, [])
        self.assertTrue(self.inventory["acceptance"]["unclassifiedExecutablesEqualZero"])

    def test_temp_and_evidence_are_execution_denied(self) -> None:
        offenders = []
        for item in self.inventory["executables"]:
            path = str(item["path"])
            if not is_execution_denied(path):
                continue
            if item.get("classification") != "execution_denied":
                offenders.append((path, item.get("classification")))
            if item.get("executionPolicy") != "denied":
                offenders.append((path, item.get("executionPolicy")))
        self.assertEqual(offenders, [])
        self.assertTrue(self.inventory["acceptance"]["tempAndEvidenceExecutionDenied"])

        # Prefix contract remains hard-coded and covers both zones.
        self.assertIn("temp/", DENIED_PREFIXES)
        self.assertIn("docs/evidence/", DENIED_PREFIXES)

        # At least one denied executable exists in each zone when the tree has them.
        denied_paths = [
            p for p, item in self.by_path.items() if item.get("classification") == "execution_denied"
        ]
        if (ROOT / "temp").exists():
            self.assertTrue(any(p.startswith("temp/") for p in denied_paths))
        if (ROOT / "docs" / "evidence").exists():
            self.assertTrue(any(p.startswith("docs/evidence/") for p in denied_paths))

    def test_each_protected_writer_has_exactly_one_controller(self) -> None:
        writers = self.inventory["protectedWriters"]["protectedWriters"]
        self.assertEqual(set(writers), set(PROTECTED_WRITERS))
        for writer_id, meta in writers.items():
            self.assertEqual(
                int(meta["controllerCount"]),
                1,
                msg=f"{writer_id} controllerCount",
            )
            self.assertTrue(meta.get("controller"), msg=writer_id)
            self.assertTrue(meta.get("surface"), msg=writer_id)
            self.assertTrue(
                meta.get("surfaceExists"),
                msg=f"{writer_id} surface missing: {meta.get('surface')}",
            )
            self.assertTrue(
                meta.get("controllerExists"),
                msg=f"{writer_id} controller missing: {meta.get('controller')}",
            )
        self.assertTrue(
            self.inventory["acceptance"]["eachProtectedWriterHasExactlyOneController"]
        )
        self.assertEqual(
            self.inventory["protectedWriters"]["acceptance"]["violations"],
            [],
        )

    def test_registered_tool_entrypoints_are_classified(self) -> None:
        routes = json.loads((ROOT / "config" / "data-routing.json").read_text(encoding="utf-8"))
        for tool in routes["tools"]:
            entry = tool["entrypoint"].replace("\\", "/")
            self.assertIn(entry, self.by_path, msg=f"missing inventory for {tool['id']}")
            item = self.by_path[entry]
            self.assertTrue(item.get("classification"))
            self.assertIn(tool["id"], item.get("toolIds") or [])
            self.assertNotEqual(item.get("classification"), "execution_denied")

    def test_control_plane_roles(self) -> None:
        backend = self.by_path["scripts/backend.py"]
        daily = self.by_path["pipelines/run_daily.py"]
        publisher = self.by_path["pipelines/publish-snapshot.mjs"]
        self.assertIn("orchestrator", backend.get("roles") or [])
        self.assertIn("protected_writer_controller", backend.get("roles") or [])
        self.assertEqual(daily["classification"], "daily_orchestrator")
        self.assertIn("official-publisher", publisher.get("toolIds") or [])

    def test_call_graph_has_registry_and_orchestrator_edges(self) -> None:
        edges = self.inventory["callGraph"]["edges"]
        self.assertTrue(edges)
        kinds = {edge["kind"] for edge in edges}
        self.assertIn("registry_entrypoint", kinds)
        self.assertIn("orchestrator_invoke", kinds)
        pairs = {(edge["from"], edge["to"]) for edge in edges}
        self.assertIn(("scripts/backend.py", "pipelines/run_daily.py"), pairs)
        self.assertIn(("tool:official-publisher", "pipelines/publish-snapshot.mjs"), pairs)

    def test_export_artifacts_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            hashes = export_artifacts(self.inventory, out)
            for name in (
                "script-lifecycle-registry.json",
                "call-graph.json",
                "protected-writer-map.json",
            ):
                path = out / name
                self.assertTrue(path.is_file(), msg=name)
                document = json.loads(path.read_text(encoding="utf-8"))
                self.assertTrue(document)
                self.assertEqual(len(hashes[name]), 64)

            protected = json.loads((out / "protected-writer-map.json").read_text(encoding="utf-8"))
            for writer_id, meta in protected["protectedWriters"].items():
                self.assertEqual(meta["controllerCount"], 1, msg=writer_id)

            lifecycle = json.loads(
                (out / "script-lifecycle-registry.json").read_text(encoding="utf-8")
            )
            self.assertTrue(lifecycle["acceptance"]["pass"])
            for item in lifecycle["executables"]:
                if str(item["path"]).startswith(("temp/", "docs/evidence/")):
                    self.assertEqual(item["executionPolicy"], "denied")
                    self.assertEqual(item["classification"], "execution_denied")

    def test_is_execution_denied_helper(self) -> None:
        self.assertTrue(is_execution_denied("temp/foo.py"))
        self.assertTrue(is_execution_denied("docs/evidence/x/y.py"))
        self.assertFalse(is_execution_denied("pipelines/run_daily.py"))
        self.assertFalse(is_execution_denied("scripts/backend.py"))


if __name__ == "__main__":
    unittest.main()
