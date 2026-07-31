"""A12 independent red-team acceptance tests.

These tests attack contracts, baseline binding, writer containment, privacy,
replay/idempotency claims, and release honesty without mutating production.
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

# Immutable baseline binding from wave-5 dispatch (must match baseline-manifest).
BASELINE_ID = "baseline_qc_20260729_wave0_c1_v1"
BASELINE_MANIFEST = (
    ROOT
    / "data/runtime/private-reports/baselines"
    / BASELINE_ID
    / "baseline-manifest.json"
)
EXPECTED_BASELINE_MANIFEST_SHA256 = (
    "a0481b45c17ba4e1a1d0d33a1b4d449d531d3903fe91f76b9e1053f87a0921bc"
)
EXPECTED_COHORT_SHA256 = (
    "2f2fbb1cac643e1580897fc0af2baf4ce746a361f32ee7d68ebb86ce3ebdde41"
)
EXPECTED_ROUTING_SHA256 = (
    "9d86cadd5dfd31e4f23540e259beee56999b79dff7bc351374532a6ab303fdea"
)
EXPECTED_DB_STATE_FINGERPRINT = (
    "b0ac9ae8c5b091fa6fa71bd5b87d8fe1130c3af04ebd22b720d7a4546cd18f25"
)

QC_RECEIPT = (
    ROOT
    / "data/runtime/private-reports/canonical-db-qc"
    / "qc_20260729_sale_contract_01"
    / "receipt.json"
)

WAVE_RECEIPT_GLOB = "data/runtime/private-reports/wave*/**/RECEIPT.json"

SECRET_PATTERNS = [
    re.compile(
        r"(?i)(api[_-]?key\s*[:=]\s*[\"'][^\"']{8,}"
        r"|password\s*[:=]\s*[\"'][^\"']{4,}"
        r"|secret\s*[:=]\s*[\"'][^\"']{8,}"
        r"|bearer\s+[A-Za-z0-9._\-]{20,}"
        r"|AKIA[0-9A-Z]{16}"
        r"|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"
        r"|ghp_[A-Za-z0-9]{20,}"
        r"|xox[baprs]-[A-Za-z0-9-]{10,})"
    ),
    re.compile(r"(?i)(mysql|postgres|mongodb(?:\+srv)?)://[^\s\"']+"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _get_any(data: dict, *keys):
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


class BaselineContractBindingTests(unittest.TestCase):
    def test_baseline_manifest_sha256_matches_dispatch(self) -> None:
        self.assertTrue(BASELINE_MANIFEST.is_file(), msg=str(BASELINE_MANIFEST))
        actual = _sha256_file(BASELINE_MANIFEST)
        self.assertEqual(actual, EXPECTED_BASELINE_MANIFEST_SHA256)

    def test_routing_sha256_matches_dispatch(self) -> None:
        routing = ROOT / "config/data-routing.json"
        self.assertEqual(_sha256_file(routing), EXPECTED_ROUTING_SHA256)

    def test_cohort_and_release_honesty_in_baseline(self) -> None:
        manifest = _load_json(BASELINE_MANIFEST)
        self.assertEqual(manifest["baselineId"], BASELINE_ID)
        self.assertEqual(
            manifest["cohort"]["universeCandidateSha256"], EXPECTED_COHORT_SHA256
        )
        self.assertEqual(manifest["dispatchDefaults"]["ROUTING_SHA256"], EXPECTED_ROUTING_SHA256)
        # Release honesty: qualified universe is not production-ready.
        self.assertEqual(manifest["cohort"]["releaseReadyQualified"], 0)
        self.assertEqual(manifest["cohort"]["releaseBlockedQualified"], 932)
        self.assertFalse(manifest["qcEvidence"]["releaseGateEligible"])
        self.assertEqual(manifest["qcEvidence"]["status"], "blocked")

    def test_live_db_fingerprint_matches_dispatch(self) -> None:
        fp_path = (
            ROOT
            / "data/runtime/private-reports/baselines"
            / BASELINE_ID
            / "db-state-fingerprint-live.json"
        )
        self.assertTrue(fp_path.is_file())
        data = _load_json(fp_path)
        self.assertEqual(data["dbStateFingerprint"], EXPECTED_DB_STATE_FINGERPRINT)
        self.assertEqual(data["cohortSha256"], EXPECTED_COHORT_SHA256)
        self.assertTrue(data.get("readOnly"))

    def test_wave_receipts_bind_baseline_and_cohort(self) -> None:
        receipts = sorted(ROOT.glob(WAVE_RECEIPT_GLOB))
        self.assertGreaterEqual(len(receipts), 10, msg="expected wave1-4 receipts")
        for path in receipts:
            if "MAIN-INTEGRATION" in str(path):
                continue
            data = _load_json(path)
            baseline = _get_any(data, "baseline_id", "baselineId")
            cohort = _get_any(data, "cohort_sha256", "cohortSha256")
            routing = _get_any(
                data,
                "routing_sha256",
                "routingSha256",
                "routing_sha256_dispatch",
                "routing_sha256_observed",
            )
            self.assertEqual(
                baseline,
                BASELINE_ID,
                msg=f"{path} baseline_id",
            )
            self.assertEqual(
                cohort,
                EXPECTED_COHORT_SHA256,
                msg=f"{path} cohort_sha256",
            )
            if routing is not None:
                self.assertEqual(
                    routing,
                    EXPECTED_ROUTING_SHA256,
                    msg=f"{path} routing_sha256",
                )


class ReleaseHonestyTests(unittest.TestCase):
    def test_canonical_db_qc_not_release_eligible(self) -> None:
        receipt = _load_json(QC_RECEIPT)
        self.assertEqual(receipt["status"], "blocked")
        self.assertTrue(receipt["readOnly"])
        self.assertFalse(receipt["releaseGate"]["eligible"])
        self.assertEqual(receipt["counts"]["releaseReadyQualified"], 0)
        self.assertEqual(receipt["counts"]["releaseBlockedQualified"], 932)
        self.assertEqual(receipt["counts"]["qualified"], 932)
        # QC green is not implied by having a receipt.
        self.assertNotEqual(receipt["status"], "passed")

    def test_missing_is_not_zero_in_price_and_derived_shards(self) -> None:
        price = _load_json(
            ROOT
            / "data/runtime/private-reports/wave3"
            / "A06-QC-A06-PRICE-HISTORY"
            / "price-candidate-shard.json"
        )
        self.assertTrue(price.get("missingIsNotZero") or price.get("missing_is_not_zero"))
        self.assertEqual(price["terminalCounts"]["currentPrice"]["MISSING"], 50)
        # Missing price cells must not be collapsed into READY zeros.
        self.assertGreater(price["terminalCounts"]["currentPrice"]["MISSING"], 0)
        self.assertEqual(
            price["terminalCounts"]["currentPrice"]["READY"]
            + price["terminalCounts"]["currentPrice"]["MISSING"]
            + price["terminalCounts"]["currentPrice"]["SOURCE_IDENTITY_NOT_EXACT"],
            932,
        )

        derived_meta = _load_json(
            ROOT
            / "data/runtime/private-reports/wave3"
            / "A10-QC-A10-DERIVED-RANKING"
            / "derive-meta.json"
        )
        a10_receipt = _load_json(
            ROOT
            / "data/runtime/private-reports/wave3"
            / "A10-QC-A10-DERIVED-RANKING"
            / "RECEIPT.json"
        )
        self.assertTrue(a10_receipt["acceptance"]["missing_is_not_zero"])
        self.assertFalse(a10_receipt["acceptance"]["invented_market_cap_zero"])
        self.assertGreater(derived_meta["counts"]["gaps"]["market_cap_null"], 0)
        self.assertGreater(derived_meta["counts"]["gaps"]["price_gap"], 0)

    def test_harvested_classification_is_not_db_written_claim(self) -> None:
        """Wave3 domain shards are candidate classifications, not DB apply proof."""
        a05 = _load_json(
            ROOT
            / "data/runtime/private-reports/wave3"
            / "A05-QC-A05-POP-GRADER"
            / "classification-meta.json"
        )
        a08 = _load_json(
            ROOT
            / "data/runtime/private-reports/wave3"
            / "A08-QC-A08-IMAGE-MEDIA"
            / "media-candidate-shard.json"
        )
        # Terminal classification can be complete while public/DB publish remains empty.
        self.assertTrue(a05["allCellsTerminal"])
        self.assertEqual(a08["publicCandidateCount"], 0)
        self.assertEqual(a08["cardCount"], 932)
        # Image QC blocked 932; harvested ≠ published.
        self.assertEqual(a08["reviewQueueCount"], 932)

    def test_production_status_not_eligible(self) -> None:
        # Machine status is volatile; if callable, must not claim production eligible.
        # Prefer reading PROJECT_STATE only as evidence if present is out of scope;
        # re-check QC receipt and baseline as durable release truth.
        receipt = _load_json(QC_RECEIPT)
        self.assertFalse(receipt["releaseGate"]["eligible"])
        blockers = receipt["releaseGate"]["blockers"]
        self.assertIn("image_not_human_or_vision_confirmed", blockers)
        self.assertEqual(blockers["image_not_human_or_vision_confirmed"], 932)
        self.assertIn("canonical_printing_missing", blockers)


class WriterContainmentTests(unittest.TestCase):
    def test_write_claims_have_no_duplicate_paths(self) -> None:
        routing = _load_json(ROOT / "config/data-routing.json")
        claims = routing["agentExecution"]["writeClaims"]
        owners: dict[str, list[str]] = {}
        for claim in claims:
            for path in claim.get("paths", []):
                owners.setdefault(path, []).append(claim["id"])
        duplicates = {path: ids for path, ids in owners.items() if len(ids) > 1}
        self.assertEqual(duplicates, {}, msg=f"overlapping claim paths: {duplicates}")

    def test_red_team_claim_is_read_only_production(self) -> None:
        routing = _load_json(ROOT / "config/data-routing.json")
        claims = {
            c["id"]: c for c in routing["agentExecution"]["writeClaims"]
        }
        self.assertIn("red-team-tests", claims)
        claim = claims["red-team-tests"]
        self.assertEqual(claim["ownerRoleId"], "A12")
        self.assertEqual(claim["mode"], "read_only_production")
        paths = set(claim["paths"])
        self.assertIn("tests/acceptance/**", paths)
        self.assertIn("data/runtime/private-reports/red-team/**", paths)

    def test_wave_receipt_write_sets_do_not_escape_declared_claims(self) -> None:
        """Spot-check that domain receipts declare write_claim_id and stay in claim tree."""
        allowed_roots = {
            "A01": ("frontend-consumers", ("apps/web/src", "data/runtime/private-reports/wave1/A01")),
            "A02": ("public-schema-exporter", ("packages/market-data", "pipelines/canonical_public_snapshot.py", "data/runtime/private-reports/wave1/A02")),
            "A03": ("canonical-db-schema", ("pipelines/", "data/runtime/private-reports/wave1/A03")),
            "A04": ("script-inventory-control", ("pipelines/script_inventory.py", "tests/test_script_inventory.py", "data/runtime/private-reports/wave1/A04")),
            "A05": ("gemrate-domain", ("pipelines/gemrate", "tests/test_gemrate", "data/runtime/private-reports/wave3/A05")),
            "A06": ("price-domain", ("pipelines/snk", "tests/test_snkrdunk", "data/runtime/private-reports/wave3/A06")),
            "A07": ("sales-domain", ("pipelines/ebay_sold_data.py", "tests/test_ebay_sold_data.py", "data/runtime/private-reports/wave3/A07")),
            "A08": ("image-media-domain", ("pipelines/verify_images.py", "tests/test_public_snapshot_qc.py", "data/runtime/private-reports/wave3/A08")),
            "A09": ("editorial-domain", ("pipelines/editorial_import.py", "tests/test_editorial_import.py", "data/runtime/private-reports/wave3/A09")),
            "A10": ("derived-ranking-domain", ("pipelines/ranking_derivation.py", "pipelines/market_alerts.py", "tests/test_ranking", "tests/test_market_alerts", "data/runtime/private-reports/wave3/A10")),
            "A11": ("runtime-control", ("scripts/backend.py", "pipelines/run_daily.py", "pipelines/run_receipts.py", "tests/test_run_daily", "tests/test_backend_cli", "data/runtime/private-reports/wave2/A11", "data/runtime/private-reports/wave4/A11")),
        }
        for path in sorted(ROOT.glob(WAVE_RECEIPT_GLOB)):
            if "MAIN-INTEGRATION" in str(path):
                continue
            data = _load_json(path)
            agent = _get_any(data, "agent_id", "agentId")
            claim = _get_any(data, "write_claim_id", "writeClaimId")
            write_set = _get_any(data, "write_set", "writeSet") or []
            if agent not in allowed_roots:
                continue
            expected_claim, prefixes = allowed_roots[agent]
            self.assertEqual(claim, expected_claim, msg=f"{path} write_claim_id")
            for entry in write_set:
                entry_norm = str(entry).replace("\\", "/")
                if entry_norm.endswith("RECEIPT.json") or entry_norm.endswith("/**"):
                    # directory claims under private-reports are fine
                    pass
                ok = any(entry_norm.startswith(p) or p in entry_norm for p in prefixes)
                self.assertTrue(
                    ok,
                    msg=f"{path} write_set entry escapes claim: {entry_norm}",
                )


class ReplayIdempotencyClaimTests(unittest.TestCase):
    def test_a11_runtime_receipt_claims_are_backed_by_artifacts(self) -> None:
        a11_dir = (
            ROOT
            / "data/runtime/private-reports/wave4"
            / "A11-QC-A11-INCREMENTAL-RUNTIME"
        )
        receipt = _load_json(a11_dir / "RECEIPT.json")
        self.assertEqual(receipt["acceptance_verdict"], "DONE")
        detail = receipt["acceptance_verdict_detail"]
        self.assertTrue(detail["full_and_incremental_share_one_engine"])
        self.assertTrue(detail["same_input_rerun_is_noop"])
        self.assertTrue(detail["retry_resumes_without_downstream_cursor_advance"])
        self.assertTrue(detail["zero_pointer_timer_mutation_in_dry_run"])

        arts = receipt["output_artifacts_with_sha256"]
        for name, claimed in arts.items():
            if name == "RECEIPT.json":
                continue
            path = a11_dir / name
            self.assertTrue(path.is_file(), msg=name)
            self.assertEqual(_sha256_file(path), claimed, msg=name)

    def test_engine_profiles_share_stages_in_source(self) -> None:
        import sys
        import tempfile
        from datetime import datetime, timezone

        sys.path.insert(0, str(ROOT / "pipelines"))
        from run_receipts import (  # noqa: WPS433
            STAGES,
            compute_input_fingerprint,
            engine_profile,
            plan_engine_run,
            profiles_share_engine,
            same_input_is_noop,
            write_input_fingerprint,
            write_stage_receipt,
        )

        self.assertTrue(profiles_share_engine("full", "incremental"))
        full = engine_profile("full")
        inc = engine_profile("incremental")
        self.assertEqual(full["stages"], inc["stages"])
        self.assertEqual(full["stages"], list(STAGES))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "stages"
            at = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
            material = {"mode": "staging", "runProfile": "full", "selector": "redteam"}
            fingerprint = compute_input_fingerprint(material)
            write_input_fingerprint(
                root,
                run_id="rt_idempotent_1",
                profile_id="full",
                fingerprint=fingerprint,
                material=material,
            )
            for stage in STAGES:
                write_stage_receipt(
                    root,
                    run_id="rt_idempotent_1",
                    stage=stage,
                    counts={"ok": 1},
                    completed_at=at,
                )
            self.assertTrue(same_input_is_noop(root, input_fingerprint=fingerprint))
            plan = plan_engine_run(
                "full",
                receipt_root=root,
                input_fingerprint=fingerprint,
                resume=True,
                dry_run=True,
            )
            self.assertEqual(plan["status"], "noop")
            self.assertFalse(plan["sideEffects"]["pointer_write"])
            self.assertFalse(plan["sideEffects"]["timer_enable"])
            self.assertFalse(plan["sideEffects"]["database_apply"])

            dry = plan_engine_run(
                "incremental",
                dry_run=True,
                allow_publish=False,
                allow_timer=False,
                allow_database_apply=False,
            )
            self.assertEqual(dry["status"], "dry_run_plan")
            self.assertFalse(dry["sideEffects"]["pointer_write"])
            self.assertFalse(dry["sideEffects"]["timer_enable"])
            self.assertFalse(dry["sideEffects"]["database_apply"])
            self.assertFalse(dry["sideEffects"]["publish"])


class PrivacyContainmentTests(unittest.TestCase):
    def test_wave_private_reports_contain_no_secret_literals(self) -> None:
        scan_roots = [
            ROOT / "data/runtime/private-reports/wave1",
            ROOT / "data/runtime/private-reports/wave2",
            ROOT / "data/runtime/private-reports/wave3",
            ROOT / "data/runtime/private-reports/wave4",
        ]
        hits: list[tuple[str, str]] = []
        scanned = 0
        for sroot in scan_roots:
            if not sroot.is_dir():
                continue
            for path in sroot.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in {".json", ".jsonl", ".md", ".txt"}:
                    continue
                if path.stat().st_size > 8_000_000:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                scanned += 1
                for pattern in SECRET_PATTERNS:
                    match = pattern.search(text)
                    if match:
                        hits.append((str(path.as_posix()), match.group(0)[:48]))
        self.assertGreater(scanned, 20)
        self.assertEqual(hits, [], msg=f"secret-like hits: {hits[:10]}")


class AcceptanceGapSpotCheckTests(unittest.TestCase):
    def test_a10_partial_and_views_not_fully_ready(self) -> None:
        receipt = _load_json(
            ROOT
            / "data/runtime/private-reports/wave3"
            / "A10-QC-A10-DERIVED-RANKING"
            / "RECEIPT.json"
        )
        self.assertEqual(receipt["acceptance_verdict"], "PARTIAL")
        meta = _load_json(
            ROOT
            / "data/runtime/private-reports/wave3"
            / "A10-QC-A10-DERIVED-RANKING"
            / "derive-meta.json"
        )
        self.assertEqual(meta["viewStatus"]["top300"], "blocked")
        self.assertEqual(meta["viewStatus"]["top350"], "blocked")
        self.assertEqual(meta["viewStatus"]["reserve50"], "blocked")

    def test_registry_qc_work_items_not_completed_in_registry(self) -> None:
        """Release honesty: agent DONE receipts ≠ registry completion ≠ published."""
        routing = _load_json(ROOT / "config/data-routing.json")
        work_items = {item["id"]: item for item in routing["workItems"]}
        for work_item_id in (
            "QC-A01-FRONTEND-CONSUMERS",
            "QC-A11-INCREMENTAL-RUNTIME",
            "QC-A12-INDEPENDENT-REDTEAM",
        ):
            item = work_items[work_item_id]
            # As of red-team pass, MAIN has not promoted registry statuses.
            self.assertIn(item["status"], {"planned", "in_progress", "blocked"})
            self.assertNotEqual(item["status"], "completed")

    def test_a08_public_candidates_empty_is_explicit(self) -> None:
        receipt = _load_json(
            ROOT
            / "data/runtime/private-reports/wave3"
            / "A08-QC-A08-IMAGE-MEDIA"
            / "RECEIPT.json"
        )
        self.assertEqual(
            receipt["acceptance_verdict_detail"]["public_candidate_count"], 0
        )
        self.assertTrue(
            receipt["acceptance_verdict_detail"][
                "public_candidates_zero_sample_and_cross_card"
            ]
        )


if __name__ == "__main__":
    unittest.main()
