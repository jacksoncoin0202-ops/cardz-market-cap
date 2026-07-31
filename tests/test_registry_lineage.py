import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from data_routing import check_registry_docs, explain_identifier, generate_registry_docs, load_registry, validate_registry
from registry_lineage import (
    RegistryError,
    _durable_content_violations,
    render_agent_execution_architecture_html,
    render_agent_role_pack_markdown,
    render_document_authority_markdown,
    render_html_graph,
    validate_control_plane,
)


class RegistryLineageTests(unittest.TestCase):
    def test_registry_is_complete_control_plane(self):
        registry = load_registry()
        self.assertEqual(registry["schemaVersion"], "3.3")
        self.assertIsNone(registry["storagePolicy"]["canonicalRankingStorageLimit"])
        self.assertEqual(
            {index["id"] for index in registry["rankingPolicy"]["indexes"]},
            {"tcg", "pokemon", "one-piece"},
        )
        self.assertEqual(
            {view["id"] for view in registry["presentationViews"]},
            {"top100", "top300", "top300_boards", "top350", "top100_plus_200", "reserve50"},
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
        self.assertEqual(
            {role["id"] for role in registry["agentExecution"]["roles"]},
            {f"A{number:02d}" for number in range(1, 13)},
        )
        self.assertEqual(
            registry["documentAuthority"]["defaultUnregisteredStatus"],
            "reference_only",
        )
        self.assertEqual(
            [level["id"] for level in registry["agentExecution"]["progressiveDisclosure"]["levels"]],
            [f"L{number}" for number in range(6)],
        )
        self.assertTrue(
            all(
                route["availabilityPolicy"] == "runtime_discovery"
                for route in registry["agentExecution"]["capabilityRoutes"]
            )
        )
        self.assertEqual(
            {
                item["informationClassId"]
                for item in registry["agentExecution"]["progressiveDisclosure"]["sectionClassifications"]
            },
            {
                "timeless_invariant",
                "versioned_contract",
                "volatile_material",
                "evidence",
            },
        )
        self.assertTrue(registry["agentExecution"]["feedbackProtocol"]["noSilentFallback"])

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
        self.assertEqual(task["assignment"]["ownerRoleId"], "A10")
        self.assertEqual(task["ownerRole"]["id"], "A10")
        role = explain_identifier("A05")
        self.assertEqual(role["kind"], "agentRole")
        self.assertEqual(role["value"]["label"], "GemRate POP and Grader")
        self.assertEqual(role["roleDocument"]["path"], "docs/generated/roles/A05.md")
        self.assertIn(
            "code-navigation",
            {capability["id"] for capability in role["capabilities"]},
        )
        document = explain_identifier("agent-entry")
        self.assertEqual(document["kind"], "document")
        self.assertEqual(document["value"]["path"], "AGENTS.md")
        self.assertEqual(
            document["value"]["informationClassId"],
            "timeless_invariant",
        )
        colliding_document = explain_identifier("document:backend-runbook")
        self.assertEqual(colliding_document["kind"], "document")
        self.assertEqual(
            colliding_document["value"]["informationClassId"],
            "versioned_contract",
        )
        self.assertEqual(
            colliding_document["value"]["claimPolicy"],
            "runtime_verification_required",
        )
        capability = explain_identifier("code-navigation")
        self.assertEqual(capability["kind"], "capabilityRoute")
        self.assertEqual(capability["value"]["availabilityPolicy"], "runtime_discovery")
        context_level = explain_identifier("L4")
        self.assertEqual(context_level["kind"], "contextLevel")
        self.assertEqual(context_level["value"]["informationClassId"], "volatile_material")
        feedback = explain_identifier("INVARIANT_CONFLICT")
        self.assertEqual(feedback["kind"], "feedbackCategory")
        self.assertTrue(feedback["value"]["architectureChange"])

    def test_generated_docs_are_deterministic_and_detect_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            generated = generate_registry_docs(output_dir=output_dir)
            self.assertEqual(
                {path.relative_to(output_dir).as_posix() for path in generated},
                {
                    "TOOL_REGISTRY.md",
                    "DATA_LINEAGE.html",
                    "DATA_ROUTING.json",
                    "DOCUMENT_AUTHORITY.md",
                    "AGENT_EXECUTION_FUNNEL.md",
                    "AGENT_EXECUTION_ARCHITECTURE.html",
                    *{f"roles/A{number:02d}.md" for number in range(1, 13)},
                },
            )
            self.assertEqual(check_registry_docs(output_dir=output_dir), [])
            funnel = (output_dir / "AGENT_EXECUTION_FUNNEL.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("Dispatch-time assignment lookup", funnel)
            self.assertNotIn("Current assignment material", funnel)
            self.assertNotIn("QC-A01-FRONTEND-CONSUMERS", funnel)
            self.assertIn("WAVE_ID=<exact registered wave>", funnel)
            self.assertIn("ROLE_STAGE=<exact registered role stage>", funnel)
            self.assertIn("ROLE_MODE=<exact roles[].mode>", funnel)
            self.assertIn("WRITE_CLAIM_ID=<exact registered claim or NONE>", funnel)
            self.assertIn("ALLOWED_TOOL_IDS=", funnel)
            tool_registry = (output_dir / "TOOL_REGISTRY.md").read_text(
                encoding="utf-8"
            )
            data_lineage = (output_dir / "DATA_LINEAGE.html").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("| ID | Priority | Status |", tool_registry)
            self.assertNotIn("<th>Status</th>", data_lineage)
            self.assertNotIn('class="status ', data_lineage)
            (output_dir / "TOOL_REGISTRY.md").write_text("changed\n", encoding="utf-8")
            self.assertEqual(check_registry_docs(output_dir=output_dir), ["TOOL_REGISTRY.md"])

    def test_each_role_pack_is_demand_loaded_and_contains_no_live_state(self):
        registry = load_registry()
        for number in range(1, 13):
            role_id = f"A{number:02d}"
            rendered = render_agent_role_pack_markdown(registry, role_id)
            self.assertIn("Read this", rendered)
            self.assertIn("do not read the other eleven role packs", rendered)
            self.assertIn("Adapter availability is volatile material", rendered)
            self.assertIn("`role_stage`", rendered)
            self.assertIn("`write_claim_id`", rendered)
            self.assertIn("`baseline_manifest_sha256`", rendered)
            self.assertNotIn("current price:", rendered.casefold())
            self.assertNotIn("current count:", rendered.casefold())

    def test_document_authority_blocks_old_execution_paths(self):
        registry = load_registry()
        documents = {
            item["id"]: item
            for item in registry["documentAuthority"]["documents"]
        }
        for document_id in (
            "project-map-legacy",
            "agent-pipeline-index-legacy",
            "frontend-handshake-legacy",
            "incremental-handoff-legacy",
        ):
            self.assertFalse(documents[document_id]["executionAllowed"])
            self.assertFalse(documents[document_id]["instructional"])
        rendered = render_document_authority_markdown(registry)
        self.assertIn("Unregistered Markdown", rendered)
        self.assertIn("cannot direct execution", rendered)

    def test_rejects_duplicate_active_authority_domain(self):
        registry = load_registry()
        invalid = copy.deepcopy(registry)
        readme = next(
            item
            for item in invalid["documentAuthority"]["documents"]
            if item["id"] == "repository-overview"
        )
        readme["authorityDomains"] = ["agent_start"]
        with self.assertRaisesRegex(RegistryError, "multiple active documents"):
            validate_control_plane(invalid)

    def test_rejects_blocked_document_as_required_role_input(self):
        registry = load_registry()
        invalid = copy.deepcopy(registry)
        invalid["agentExecution"]["roles"][0]["requiredDocumentIds"].append(
            "project-map-legacy"
        )
        with self.assertRaisesRegex(RegistryError, "requires blocked documents"):
            validate_control_plane(invalid)

    def test_rejects_reference_only_document_that_can_direct_execution(self):
        registry = load_registry()
        invalid = copy.deepcopy(registry)
        document = next(
            item
            for item in invalid["documentAuthority"]["documents"]
            if item["id"] == "provider-api-index"
        )
        document["executionAllowed"] = True
        with self.assertRaisesRegex(RegistryError, "cannot direct execution"):
            validate_control_plane(invalid)

    def test_rejects_reference_only_document_as_required_role_input(self):
        registry = load_registry()
        invalid = copy.deepcopy(registry)
        invalid["agentExecution"]["roles"][0]["requiredDocumentIds"].append(
            "provider-api-index"
        )
        with self.assertRaisesRegex(RegistryError, "requires blocked documents"):
            validate_control_plane(invalid)

    def test_rejects_reference_only_manual_as_tool_instruction(self):
        registry = load_registry()
        invalid = copy.deepcopy(registry)
        tool = next(
            item
            for item in invalid["tools"]
            if item["id"] == "candidate-gemrate-backfill"
        )
        tool["manualRefs"].append("grade10-operator")
        with self.assertRaisesRegex(RegistryError, "non-executable manuals"):
            validate_control_plane(invalid)

    def test_rejects_unowned_open_work_item(self):
        registry = load_registry()
        invalid = copy.deepcopy(registry)
        invalid["agentExecution"]["assignments"] = [
            assignment
            for assignment in invalid["agentExecution"]["assignments"]
            if assignment["workItemId"] != "T3-GEMRATE-CANDIDATE-CLASSIFICATION"
        ]
        with self.assertRaisesRegex(RegistryError, "require exactly one owner"):
            validate_control_plane(invalid)

    def test_rejects_overlapping_exact_write_claim(self):
        registry = load_registry()
        invalid = copy.deepcopy(registry)
        invalid["agentExecution"]["writeClaims"][4]["paths"].append(
            "apps/web/src/**"
        )
        with self.assertRaisesRegex(RegistryError, "overlap exact path"):
            validate_control_plane(invalid)

    def test_rejects_nested_write_claim_scope(self):
        registry = load_registry()
        invalid = copy.deepcopy(registry)
        invalid["agentExecution"]["writeClaims"][5]["paths"].append(
            "apps/web/src/lib/**"
        )
        with self.assertRaisesRegex(RegistryError, "overlap path scopes"):
            validate_control_plane(invalid)

    def test_rejects_wave_stage_without_registered_assignment(self):
        registry = load_registry()
        invalid = copy.deepcopy(registry)
        assignment = next(
            item
            for item in invalid["agentExecution"]["assignments"]
            if item["workItemId"] == "QC-A11-CONTROL-CLI"
        )
        del assignment["roleStage"]
        with self.assertRaisesRegex(RegistryError, "wave stages require"):
            validate_control_plane(invalid)

    def test_rejects_delegation_semantic_escalation(self):
        registry = load_registry()
        mutations = (
            ("authorityInheritance", "children inherit all authority"),
            ("crossRolePolicy", "child may write across roles"),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                invalid = copy.deepcopy(registry)
                invalid["agentExecution"]["delegationPolicy"][field] = value
                with self.assertRaisesRegex(RegistryError, "delegationPolicy"):
                    validate_control_plane(invalid)

        invalid = copy.deepcopy(registry)
        invalid["agentExecution"]["delegationPolicy"]["childWriteRequirements"] = [
            "same immutable baseline"
        ]
        with self.assertRaisesRegex(RegistryError, "child write requirements"):
            validate_control_plane(invalid)

        invalid = copy.deepcopy(registry)
        invalid["agentExecution"]["delegationPolicy"]["requiredChildPromptFields"] = [
            "agent_id"
        ]
        with self.assertRaisesRegex(RegistryError, "child prompt fields"):
            validate_control_plane(invalid)

    def test_rejects_dispatch_or_handoff_identity_weakening(self):
        registry = load_registry()

        invalid = copy.deepcopy(registry)
        invalid["agentExecution"]["policy"]["toolExecutionPolicy"] = (
            "any registered tool may execute"
        )
        with self.assertRaisesRegex(RegistryError, "tool execution policy"):
            validate_control_plane(invalid)

        invalid = copy.deepcopy(registry)
        invalid["agentExecution"]["policy"]["requiredHandoffFields"].remove(
            "role_stage"
        )
        with self.assertRaisesRegex(RegistryError, "handoff fields"):
            validate_control_plane(invalid)

        invalid = copy.deepcopy(registry)
        invalid["agentExecution"]["delegationPolicy"][
            "requiredChildPromptFields"
        ].remove("write_claim_id")
        with self.assertRaisesRegex(RegistryError, "child prompt fields"):
            validate_control_plane(invalid)

    def test_rejects_feedback_semantic_escalation(self):
        registry = load_registry()
        for field, value in (
            ("decisionOwner", "A01"),
            ("architectureChange", False),
            ("agentAction", "reinterpret the invariant and continue"),
        ):
            with self.subTest(field=field):
                invalid = copy.deepcopy(registry)
                category = next(
                    item
                    for item in invalid["agentExecution"]["feedbackProtocol"]["categories"]
                    if item["id"] == "INVARIANT_CONFLICT"
                )
                category[field] = value
                with self.assertRaisesRegex(RegistryError, "INVARIANT_CONFLICT"):
                    validate_control_plane(invalid)

        invalid = copy.deepcopy(registry)
        invalid["agentExecution"]["feedbackProtocol"]["requiredFields"].remove(
            "blocked_rule_or_contract"
        )
        with self.assertRaisesRegex(RegistryError, "required fields"):
            validate_control_plane(invalid)

    def test_rejects_bulk_worker_initial_context(self):
        registry = load_registry()
        invalid = copy.deepcopy(registry)
        invalid["agentExecution"]["progressiveDisclosure"]["workerInitialDocumentIds"] = [
            "agent-entry",
            "document-authority-view",
            *[f"agent-role-a{number:02d}" for number in range(1, 13)],
        ]
        with self.assertRaisesRegex(RegistryError, "worker initial documents"):
            validate_control_plane(invalid)

    def test_rejects_duplicate_or_closed_role_stage_assignment(self):
        registry = load_registry()
        invalid = copy.deepcopy(registry)
        legacy = next(
            item
            for item in invalid["agentExecution"]["assignments"]
            if item["workItemId"] == "T5-RANKING-VIEWS"
        )
        legacy["roleStage"] = "A10"
        with self.assertRaisesRegex(RegistryError, "exactly one"):
            validate_control_plane(invalid)

        invalid = copy.deepcopy(registry)
        stage_item = next(
            item
            for item in invalid["workItems"]
            if item["id"] == "QC-A01-FRONTEND-CONSUMERS"
        )
        stage_item["status"] = "completed"
        with self.assertRaisesRegex(RegistryError, "one open work item"):
            validate_control_plane(invalid)

    def test_document_class_bindings_are_exhaustive_and_fail_closed(self):
        registry = load_registry()
        documents = {
            item["id"] for item in registry["documentAuthority"]["documents"]
        }
        bindings = registry["agentExecution"]["progressiveDisclosure"][
            "documentClassBindings"
        ]
        bound = [
            document_id
            for binding in bindings
            for document_id in binding["documentIds"]
        ]
        self.assertEqual(set(bound), documents)
        self.assertEqual(len(bound), len(set(bound)))

        invalid = copy.deepcopy(registry)
        timeless = next(
            item
            for item in invalid["agentExecution"]["progressiveDisclosure"][
                "documentClassBindings"
            ]
            if item["informationClassId"] == "timeless_invariant"
        )
        timeless["documentIds"].remove("agent-entry")
        with self.assertRaisesRegex(RegistryError, "require one information class"):
            validate_control_plane(invalid)

        invalid = copy.deepcopy(registry)
        volatile = next(
            item
            for item in invalid["agentExecution"]["progressiveDisclosure"][
                "documentClassBindings"
            ]
            if item["informationClassId"] == "volatile_material"
        )
        volatile["documentIds"].append("agent-entry")
        with self.assertRaisesRegex(RegistryError, "multiple information classes"):
            validate_control_plane(invalid)

        invalid = copy.deepcopy(registry)
        volatile = next(
            item
            for item in invalid["agentExecution"]["progressiveDisclosure"][
                "documentClassBindings"
            ]
            if item["informationClassId"] == "volatile_material"
        )
        timeless = next(
            item
            for item in invalid["agentExecution"]["progressiveDisclosure"][
                "documentClassBindings"
            ]
            if item["informationClassId"] == "timeless_invariant"
        )
        versioned = next(
            item
            for item in invalid["agentExecution"]["progressiveDisclosure"][
                "documentClassBindings"
            ]
            if item["informationClassId"] == "versioned_contract"
        )
        volatile["documentIds"].remove("operational-state")
        volatile["documentIds"].append("repository-overview")
        versioned["documentIds"].remove("repository-overview")
        timeless["documentIds"].append("operational-state")
        with self.assertRaisesRegex(RegistryError, "changed document membership"):
            validate_control_plane(invalid)

    def test_rejects_document_class_membership_or_shape_drift(self):
        registry = load_registry()
        moves = (
            ("architecture-registry", "timeless_invariant", "evidence"),
            ("repository-overview", "versioned_contract", "timeless_invariant"),
            (
                "agent-execution-architecture",
                "timeless_invariant",
                "versioned_contract",
            ),
        )
        for document_id, source_class, target_class in moves:
            with self.subTest(document_id=document_id):
                invalid = copy.deepcopy(registry)
                bindings = {
                    item["informationClassId"]: item["documentIds"]
                    for item in invalid["agentExecution"]["progressiveDisclosure"][
                        "documentClassBindings"
                    ]
                }
                bindings[source_class].remove(document_id)
                bindings[target_class].append(document_id)
                with self.assertRaisesRegex(RegistryError, "changed document membership"):
                    validate_control_plane(invalid)

        invalid = copy.deepcopy(registry)
        overview = next(
            item
            for item in invalid["documentAuthority"]["documents"]
            if item["id"] == "repository-overview"
        )
        overview["kind"] = "manual"
        with self.assertRaisesRegex(RegistryError, "invalid shape"):
            validate_control_plane(invalid)

    def test_every_document_has_collision_safe_explain_output(self):
        registry = load_registry()
        for item in registry["documentAuthority"]["documents"]:
            with self.subTest(document_id=item["id"]):
                explained = explain_identifier(f"document:{item['id']}")
                self.assertEqual(explained["kind"], "document")
                self.assertEqual(explained["resolvedIdentifier"], item["id"])
                self.assertEqual(
                    explained["value"]["informationClassId"],
                    next(
                        binding["informationClassId"]
                        for binding in registry["agentExecution"][
                            "progressiveDisclosure"
                        ]["documentClassBindings"]
                        if item["id"] in binding["documentIds"]
                    ),
                )

    def test_rejects_weakened_information_class_semantics(self):
        registry = load_registry()
        invalid = copy.deepcopy(registry)
        timeless = next(
            item
            for item in invalid["agentExecution"]["informationClasses"]
            if item["id"] == "timeless_invariant"
        )
        timeless["allowedContent"] = ["live counts", "current prices"]
        with self.assertRaisesRegex(RegistryError, "changed allowedContent semantics"):
            validate_control_plane(invalid)

    def test_runtime_manual_requires_registered_verification_route(self):
        registry = load_registry()
        invalid = copy.deepcopy(registry)
        runbook = next(
            item
            for item in invalid["documentAuthority"]["documents"]
            if item["id"] == "backend-runbook"
        )
        runbook["runtimeVerificationToolIds"] = ["missing-tool"]
        with self.assertRaisesRegex(RegistryError, "runtimeVerificationToolIds"):
            validate_control_plane(invalid)

    def test_runtime_verification_contract_is_exact_and_role_reachable(self):
        registry = load_registry()

        invalid = copy.deepcopy(registry)
        gemrate = next(
            item
            for item in invalid["documentAuthority"]["documents"]
            if item["id"] == "gemrate-source"
        )
        gemrate["runtimeVerificationToolIds"] = ["official-publisher"]
        publisher = next(
            item for item in invalid["tools"] if item["id"] == "official-publisher"
        )
        publisher["manualRefs"].append("gemrate-source")
        with self.assertRaisesRegex(RegistryError, "runtimeVerificationToolIds"):
            validate_control_plane(invalid)

        invalid = copy.deepcopy(registry)
        gemrate = next(
            item
            for item in invalid["documentAuthority"]["documents"]
            if item["id"] == "gemrate-source"
        )
        gemrate["runtimeVerificationContract"]["sideEffectClass"] = "public_write"
        with self.assertRaisesRegex(RegistryError, "runtimeVerificationContract"):
            validate_control_plane(invalid)

        invalid = copy.deepcopy(registry)
        gemrate_tool = next(
            item for item in invalid["tools"] if item["id"] == "gemrate-population"
        )
        gemrate_tool["entrypoint"] = "pipelines/publish-snapshot.mjs"
        gemrate_tool["command"] = ["node", "pipelines/publish-snapshot.mjs"]
        with self.assertRaisesRegex(
            RegistryError, "runtime verifier tool gemrate-population"
        ):
            validate_control_plane(invalid)

        invalid = copy.deepcopy(registry)
        gemrate_tool = next(
            item for item in invalid["tools"] if item["id"] == "gemrate-population"
        )
        gemrate_tool["sideEffectClass"] = "public_write"
        gemrate_tool["sideEffects"] = "advances the public latest pointer"
        with self.assertRaisesRegex(
            RegistryError, "runtime verifier tool gemrate-population"
        ):
            validate_control_plane(invalid)

        invalid = copy.deepcopy(registry)
        role_a06 = next(
            item
            for item in invalid["agentExecution"]["roles"]
            if item["id"] == "A06"
        )
        role_a06["requiredDocumentIds"].append("gemrate-source")
        with self.assertRaisesRegex(RegistryError, "role reachability"):
            validate_control_plane(invalid)

    def test_durable_content_rejects_body_snapshots(self):
        self.assertTrue(
            _durable_content_violations(
                "As of 2026-07-29, the current database has 900 cards."
            )
        )
        self.assertTrue(
            _durable_content_violations(
                "Current production count: 900 cards; status: complete."
            )
        )

    def test_stable_contract_rejects_dated_snapshot_markers(self):
        registry = load_registry()
        invalid = copy.deepcopy(registry)
        canonical = next(
            item
            for item in invalid["documentAuthority"]["documents"]
            if item["id"] == "canonical-printing"
        )
        canonical["path"] = (
            "docs/archive/2026-07-29-document-reset/"
            "CANONICAL_PRINTING_CURRENT_EVIDENCE_20260729.md"
        )
        with self.assertRaisesRegex(RegistryError, "volatile snapshot markers"):
            validate_control_plane(invalid)

    def test_rejects_role_pack_mismatch(self):
        registry = load_registry()
        invalid = copy.deepcopy(registry)
        invalid["agentExecution"]["roles"][0]["roleDocumentId"] = "agent-role-a02"
        with self.assertRaisesRegex(RegistryError, "generated role pack"):
            validate_control_plane(invalid)

    def test_rejects_capability_availability_as_static_truth(self):
        registry = load_registry()
        invalid = copy.deepcopy(registry)
        invalid["agentExecution"]["capabilityRoutes"][0]["availabilityPolicy"] = "installed"
        with self.assertRaisesRegex(RegistryError, "runtime material"):
            validate_control_plane(invalid)

    def test_rejects_feedback_that_allows_silent_fallback(self):
        registry = load_registry()
        invalid = copy.deepcopy(registry)
        invalid["agentExecution"]["feedbackProtocol"]["noSilentFallback"] = False
        with self.assertRaisesRegex(RegistryError, "silent fallback"):
            validate_control_plane(invalid)

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

    def test_agent_architecture_is_self_contained_and_shows_context_funnel(self):
        graph = render_agent_execution_architecture_html(load_registry())
        self.assertIn("L0 · AGENTS.md", graph)
        self.assertIn("L1 · ONE ROLE PACK", graph)
        self.assertIn("Timeless vs volatile", graph)
        self.assertNotIn("https://", graph)

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
