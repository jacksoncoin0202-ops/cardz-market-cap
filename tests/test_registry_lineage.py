import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from data_routing import check_registry_docs, explain_identifier, generate_registry_docs, load_registry, validate_registry
from registry_lineage import RegistryError, render_html_graph, validate_control_plane


class RegistryLineageTests(unittest.TestCase):
    def test_registry_is_complete_control_plane(self):
        registry = load_registry()
        self.assertIsNone(registry["storagePolicy"]["canonicalRankingStorageLimit"])
        self.assertEqual(
            {index["id"] for index in registry["rankingPolicy"]["indexes"]},
            {"tcg", "pokemon", "one-piece"},
        )
        self.assertEqual(
            {view["id"] for view in registry["presentationViews"]},
            {"top100", "top300", "top350", "top100_plus_200", "reserve50"},
        )
        self.assertIs(validate_registry(registry), registry)
        self.assertEqual(registry["rules"]["populationBands"]["formalRanking"]["minimumPsa10Population"], 1000)
        self.assertEqual(registry["rules"]["populationBands"]["preEntryRadar"], {
            "minimumExclusivePsa10Population": 970,
            "maximumExclusivePsa10Population": 1000,
        })
        self.assertEqual(registry["rules"]["populationBands"]["discoveryOnly"]["maximumInclusivePsa10Population"], 970)
        self.assertEqual(registry["architecture"]["entrypoint"], "config/data-routing.json")
        self.assertTrue(registry["architecture"]["nodes"])
        self.assertTrue(registry["architecture"]["edges"])
        self.assertTrue(registry["workItems"])
        self.assertTrue(all((ROOT / manual["path"]).is_file() for manual in registry["manuals"]))
        self.assertTrue(all((ROOT / test["path"]).is_file() for test in registry["tests"]))

    def test_explain_identifier_links_tool_to_consumers_and_routes(self):
        tool = explain_identifier("gemrate-population")
        self.assertEqual(tool["kind"], "tool")
        self.assertIn("psa10_population", tool["value"]["routeMetrics"])
        self.assertIn("canonical_mysql", tool["value"]["consumers"])
        profile = explain_identifier("daily")
        self.assertEqual(profile["kind"], "profile")
        self.assertIn("collect", [phase["id"] for phase in profile["value"]["phases"]])
        field = explain_identifier("cards.<id>.windows.30d")
        self.assertEqual(field["kind"], "snapshotField")
        self.assertEqual(
            set(field["metrics"]),
            {"price_change_1d_7d_30d", "tracked_sales"},
        )
        self.assertIn("market_candidate_daily_snapshot", field["databaseTargets"])
        self.assertIn("market_daily_sales_aggregate", field["databaseTargets"])
        self.assertEqual(field["lineage"][0]["importer"], "canonical-db")
        self.assertTrue(field["lineage"][0]["manuals"])
        self.assertTrue(field["lineage"][0]["tests"])
        alias = explain_identifier("market_cap")
        self.assertEqual(alias["kind"], "route")
        self.assertEqual(alias["resolvedIdentifier"], "psa10_market_cap")
        cleaning = explain_identifier("data_cleaning")
        self.assertEqual(cleaning["kind"], "dataCleaningPolicy")
        self.assertEqual(cleaning["rules"]["populationBands"]["formalRanking"]["minimumInclusivePsa10Population"], 1000)
        node = explain_identifier("database.canonical")
        self.assertEqual(node["kind"], "architectureNode")
        self.assertTrue(node["incomingEdges"])
        self.assertIn("T2-GEMRATE-ALIAS-DURABILITY", node["workItems"])
        task = explain_identifier("T5-RANKING-VIEWS")
        self.assertEqual(task["kind"], "workItem")
        self.assertIn("ranking.scopes", {node["id"] for node in task["nodes"]})

    def test_generated_docs_are_deterministic_and_detect_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            generated = generate_registry_docs(output_dir=output_dir)
            self.assertEqual({path.name for path in generated}, {"TOOL_REGISTRY.md", "DATA_LINEAGE.html", "DATA_ROUTING.json"})
            self.assertEqual(check_registry_docs(output_dir=output_dir), [])
            (output_dir / "TOOL_REGISTRY.md").write_text("changed\n", encoding="utf-8")
            self.assertEqual(check_registry_docs(output_dir=output_dir), ["TOOL_REGISTRY.md"])

    def test_rejects_canonical_ranking_storage_cap(self):
        registry = load_registry()
        invalid = copy.deepcopy(registry)
        invalid["storagePolicy"]["canonicalRankingStorageLimit"] = 300
        with self.assertRaisesRegex(RegistryError, "must not have a hard limit"):
            validate_control_plane(invalid)

    def test_html_graph_is_self_contained_and_has_profiles(self):
        graph = render_html_graph(load_registry())
        self.assertIn("<main id=\"data-lineage-graph\">", graph)
        self.assertIn("full-backfill", graph)
        self.assertIn("Metric lineage", graph)
        self.assertIn("Data cleaning layers", graph)
        self.assertIn("<svg", graph)
        self.assertIn("#020617", graph)
        self.assertIn("Work items", graph)
        self.assertNotIn("<script", graph)

    def test_rejects_wrong_population_band_boundaries(self):
        registry = load_registry()
        invalid = copy.deepcopy(registry)
        invalid["rules"]["populationBands"]["preEntryRadar"]["minimumExclusivePsa10Population"] = 969
        with self.assertRaisesRegex(RegistryError, "971-999"):
            validate_control_plane(invalid)

    def test_rejects_unknown_architecture_edge_node(self):
        registry = load_registry()
        invalid = copy.deepcopy(registry)
        invalid["architecture"]["edges"][0]["to"] = "missing.node"
        with self.assertRaisesRegex(RegistryError, "references unknown node"):
            validate_control_plane(invalid)

    def test_rejects_work_item_dependency_cycle(self):
        registry = load_registry()
        invalid = copy.deepcopy(registry)
        invalid["workItems"][0]["dependsOn"] = ["P0-GENERATED-DOCS"]
        with self.assertRaisesRegex(RegistryError, "dependency graph contains a cycle"):
            validate_control_plane(invalid)


if __name__ == "__main__":
    unittest.main()
