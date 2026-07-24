from __future__ import annotations

import json
import io
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import gemrate_source as source  # noqa: E402
from gemrate_source import (  # noqa: E402
    PopulationResolutionError,
    build_population_transport_run,
)


GID = "a" * 40
CARD = {
    "gemrateId": GID,
    "canonicalSourceCode": "snkrdunk",
    "canonicalExternalId": "123",
    "storageScope": "snkrdunk",
}


def direct(population: int, effective_date: str) -> dict:
    return {
        "data": {
            "gemrate_id": GID,
            "universal_gemrate_id": GID,
            "is_universal_match": True,
            "parsed_description": {"year": "2023", "cardNumber": "001"},
            "population": {
                "graders": {
                    "psa": {
                        "gemrate_id": GID,
                        "spec_id": "123",
                        "parsed_description": {"year": "2023", "cardNumber": "001"},
                    },
                },
                "population_data": {
                    "data_last_updated": effective_date,
                    "by_grader": {"psa": {"grades": {"psa_10": population}}},
                }
            },
        }
    }


def mirror(population: int, effective_date: str) -> dict:
    return {
        "population": [{"gradeName": "PSA", "topGrade": population}],
        "effectiveDate": effective_date,
    }


def website(population: int, effective_date: str) -> dict:
    return {
        "gemrate_id": GID,
        "population_data": [{
            "grader": "psa",
            "last_population_change": effective_date,
            "grades": {"g10": population},
        }],
    }


class GemRatePopulationTransportTests(unittest.TestCase):
    def test_direct_identity_receipt_retains_auditable_identity_layers(self) -> None:
        payload = direct(1200, "2026-07-23")

        receipt = source.build_direct_identity_receipt(
            GID, payload, "2026-07-24T00:00:00Z",
        )

        self.assertEqual(receipt["requestedGemrateId"], GID)
        self.assertEqual(receipt["entityGemrateId"], GID)
        self.assertEqual(receipt["universalGemrateId"], GID)
        self.assertTrue(receipt["isUniversalMatch"])
        self.assertEqual(receipt["parsedDescription"], {"year": "2023", "cardNumber": "001"})
        self.assertEqual(receipt["graders"]["psa"], {
            "gemrateId": GID,
            "specId": "123",
            "parsedDescription": {"year": "2023", "cardNumber": "001"},
        })
        self.assertEqual(receipt["sourcePointer"], "population.json")
        self.assertRegex(receipt["payloadSha256"], r"^[0-9a-f]{64}$")

    def test_direct_identity_receipt_fails_closed_on_identity_or_member_conflicts(self) -> None:
        cases = []

        wrong_entity = direct(1200, "2026-07-23")
        wrong_entity["data"]["gemrate_id"] = "b" * 40
        cases.append(wrong_entity)

        inconsistent_universal = direct(1200, "2026-07-23")
        inconsistent_universal["data"]["is_universal_match"] = None
        cases.append(inconsistent_universal)

        duplicate_member = direct(1200, "2026-07-23")
        duplicate_member["data"]["population"]["graders"]["cgc"] = {
            "gemrate_id": GID,
        }
        cases.append(duplicate_member)

        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    source.build_direct_identity_receipt(GID, payload, "2026-07-24T00:00:00Z")

    def test_direct_identity_receipt_accepts_official_universal_match_semantics(self) -> None:
        unmatched = direct(1200, "2026-07-23")
        unmatched["data"]["universal_gemrate_id"] = None
        unmatched["data"]["is_universal_match"] = False
        receipt = source.build_direct_identity_receipt(
            GID, unmatched, "2026-07-24T00:00:00Z",
        )
        self.assertIsNone(receipt["universalGemrateId"])
        self.assertFalse(receipt["isUniversalMatch"])

        member = direct(1200, "2026-07-23")
        universal_id = "b" * 40
        member["data"]["universal_gemrate_id"] = universal_id
        member["data"]["is_universal_match"] = True
        receipt = source.build_direct_identity_receipt(
            GID, member, "2026-07-24T00:00:00Z",
        )
        self.assertEqual(receipt["entityGemrateId"], GID)
        self.assertEqual(receipt["universalGemrateId"], universal_id)

    def test_page_listener_cleanup_uses_playwright_python_api(self) -> None:
        page = Mock()
        callback = Mock()

        source._remove_page_listener(page, "response", callback)

        page.remove_listener.assert_called_once_with("response", callback)

    def test_browser_launcher_prefers_configured_path_and_falls_back_to_managed_chromium(self) -> None:
        class Chromium:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def launch(self, **kwargs):
                self.calls.append(kwargs)
                return "browser"

        class Playwright:
            def __init__(self) -> None:
                self.chromium = Chromium()

        managed = Playwright()
        with patch.object(source, "_find_chrome", return_value=None):
            self.assertEqual(source._launch_chromium(managed), "browser")
        self.assertNotIn("executable_path", managed.chromium.calls[0])

        explicit = Playwright()
        with patch.object(source, "_find_chrome", return_value="/opt/browser/chrome"):
            self.assertEqual(source._launch_chromium(explicit), "browser")
        self.assertEqual(explicit.chromium.calls[0]["executable_path"], "/opt/browser/chrome")

    def test_direct_api_wins_and_retains_matching_same_day_mirror_provenance(self) -> None:
        run = build_population_transport_run(
            [CARD],
            direct_payloads={GID: direct(1200, "2026-07-23")},
            mirror_payloads={GID: mirror(1200, "2026-07-23")},
            fetched_at="2026-07-24T00:00:00Z",
        )

        self.assertTrue(run["promotable"])
        self.assertFalse(run["partial"])
        self.assertEqual(run["attempted"], 1)
        self.assertEqual(run["succeeded"], 1)
        self.assertEqual(run["failed"], 0)
        self.assertEqual(run["resolved"][0]["transport"], "direct_api")
        self.assertEqual(len(run["observations"]), 2)
        self.assertEqual(run["transports"]["direct_api"]["succeeded"], 1)
        self.assertEqual(run["transports"]["grade10_gemrate_mirror"]["succeeded"], 1)

    def test_direct_api_wins_over_matching_same_day_public_card_page(self) -> None:
        run = build_population_transport_run(
            [CARD],
            direct_payloads={GID: direct(1200, "2026-07-23")},
            website_payloads={GID: website(1200, "2026-07-23")},
            mirror_payloads={},
            fetched_at="2026-07-24T00:00:00Z",
        )

        self.assertTrue(run["promotable"])
        self.assertEqual(run["resolved"][0]["transport"], "direct_api")
        self.assertEqual(run["transports"]["gemrate_public_card_page"]["succeeded"], 1)

    def test_same_effective_day_disagreement_fails_closed(self) -> None:
        with self.assertRaisesRegex(PopulationResolutionError, "same effective date"):
            build_population_transport_run(
                [CARD],
                direct_payloads={GID: direct(1200, "2026-07-23")},
                mirror_payloads={GID: mirror(1199, "2026-07-23")},
                fetched_at="2026-07-24T00:00:00Z",
            )

    def test_public_card_page_is_selected_before_mirror_and_retains_provenance(self) -> None:
        run = build_population_transport_run(
            [CARD],
            direct_payloads={},
            website_payloads={GID: website(1200, "2026-07-23")},
            mirror_payloads={GID: mirror(1200, "2026-07-23")},
            fetched_at="2026-07-24T00:00:00Z",
        )

        self.assertTrue(run["promotable"])
        self.assertEqual(run["resolved"][0]["transport"], "gemrate_public_card_page")
        self.assertEqual(run["transports"]["gemrate_public_card_page"]["succeeded"], 1)
        self.assertEqual(len(run["observations"]), 2)

    def test_public_card_page_last_change_does_not_conflict_with_mirror_snapshot_date(self) -> None:
        run = build_population_transport_run(
            [CARD],
            direct_payloads={},
            website_payloads={GID: website(1200, "2026-07-23")},
            mirror_payloads={GID: mirror(1199, "2026-07-23")},
            fetched_at="2026-07-24T00:00:00Z",
        )

        self.assertTrue(run["promotable"])
        self.assertEqual(run["resolved"][0]["transport"], "gemrate_public_card_page")

    def test_direct_api_remains_preferred_over_newer_mirror_observation(self) -> None:
        run = build_population_transport_run(
            [CARD],
            direct_payloads={GID: direct(1200, "2026-07-22")},
            mirror_payloads={GID: mirror(1201, "2026-07-23")},
            fetched_at="2026-07-24T00:00:00Z",
        )

        self.assertTrue(run["promotable"])
        self.assertEqual(run["resolved"][0]["transport"], "direct_api")
        self.assertEqual(run["resolved"][0]["effectiveDate"], "2026-07-22")
        self.assertEqual(len(run["observations"]), 2)

    def test_mirror_can_resolve_current_population_without_key_but_never_history(self) -> None:
        run = build_population_transport_run(
            [CARD],
            direct_payloads={},
            mirror_payloads={GID: mirror(1200, "2026-07-23")},
            fetched_at="2026-07-24T00:00:00Z",
        )

        self.assertTrue(run["promotable"])
        self.assertEqual(run["resolved"][0]["transport"], "grade10_gemrate_mirror")
        self.assertEqual(run["history"], {"status": "unavailable", "reason": "current_transports_have_no_population_history"})
        self.assertEqual(run["transports"]["direct_api"]["attempted"], 0)
        self.assertEqual(run["transports"]["grade10_gemrate_mirror"]["succeeded"], 1)

    def test_unresolved_card_marks_run_partial_and_not_promotable(self) -> None:
        run = build_population_transport_run(
            [CARD],
            direct_payloads={},
            mirror_payloads={},
            fetched_at="2026-07-24T00:00:00Z",
        )

        self.assertTrue(run["partial"])
        self.assertFalse(run["promotable"])
        self.assertEqual(run["attempted"], 1)
        self.assertEqual(run["succeeded"], 0)
        self.assertEqual(run["failed"], 1)
        self.assertEqual(run["unresolved"], [{"gemrateId": GID, "reason": "no_current_population"}])

    def test_public_card_page_fixture_uses_labeled_psa_gem_mint_column(self) -> None:
        payload, reason = source.build_public_card_page_payload(
            GID,
            headers=["Grader", "POP", "Gems+", "Gem Mint", "Mint"],
            psa_row=["PSA", "95,200", "49,496", "12,345", "31,001"],
            canonical_url="https://www.gemrate.com/card/pikachu-with-grey-felt-hat",
            title="Pikachu with Grey Felt Hat",
            dom_sha256="b" * 64,
            route_verified=True,
        )

        self.assertIsNone(reason)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["population_data"][0]["grades"]["g10"], 12345)
        self.assertEqual(payload["publicCardPage"]["domSha256"], "b" * 64)

    def test_public_card_page_fixture_rejects_missing_psa_gem_mint(self) -> None:
        payload, reason = source.build_public_card_page_payload(
            GID,
            headers=["Grader", "POP", "Mint"],
            psa_row=["PSA", "95,200", "31,001"],
            canonical_url="https://www.gemrate.com/card/example",
            title="Example",
            dom_sha256="b" * 64,
            route_verified=True,
        )

        self.assertIsNone(payload)
        self.assertEqual(reason, "population_table_missing")

    def test_page_initiated_json_is_preferred_and_preserves_source_dates(self) -> None:
        payload, reason = source.build_public_card_page_json_payload(
            GID,
            response_url=f"https://www.gemrate.com/card-details?gemrate_id={GID}",
            response_status=200,
            payload={
                "gemrate_id": GID,
                "year": "2023",
                "set_name": "Scarlet & Violet Black Star Promos",
                "card_number": "085",
                "parallel": "Promo",
                "date": "2026-07-21",
                "last_population_change": "2026-07-21",
                "population_data": [{
                    "grader": "psa",
                    "grades": {"g10": 13289},
                }],
            },
            canonical_url="https://www.gemrate.com/card/pikachu-with-grey-felt-hat",
            title="Pikachu with Grey Felt Hat",
            dom_sha256="c" * 64,
            route_verified=True,
        )

        self.assertIsNone(reason)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["date"], "2026-07-21")
        self.assertEqual(payload["population_data"][0]["grades"]["g10"], 13289)
        self.assertEqual(payload["publicCardPage"]["populationMode"], "page_initiated_json")
        resolved = source._website_population(payload, "2026-07-24T00:00:00Z")
        self.assertEqual(resolved["effectiveDate"], "2026-07-24")
        self.assertEqual(resolved["effectiveDateSource"], "live_public_snapshot_fetch")
        self.assertEqual(resolved["sourceDate"], "2026-07-21")
        self.assertEqual(resolved["lastPopulationChange"], "2026-07-21")

    def test_page_initiated_json_rejects_direct_or_wrong_id_route(self) -> None:
        payload, reason = source.build_public_card_page_json_payload(
            GID,
            response_url="https://www.gemrate.com/card-details?gemrate_id=other-id",
            response_status=200,
            payload={},
            canonical_url="https://www.gemrate.com/card/example",
            title="Example",
            dom_sha256="c" * 64,
            route_verified=True,
        )

        self.assertIsNone(payload)
        self.assertEqual(reason, "page_initiated_json_route_unverified")

    def test_direct_history_uses_documented_week_interval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls: list[str] = []

            def fake_api_get(path: str, key: str, delay: float):
                calls.append(path)
                if path.startswith(f"/cards/{GID}/population?"):
                    return 200, direct(1200, "2026-07-23")
                return 200, {"data": []}

            with patch.object(source, "_api_get", side_effect=fake_api_get):
                ok, note = source.fetch_card(
                    GID, "not-a-real-key", 0.0, history=True, cards_dir=Path(directory),
                )

            self.assertTrue(ok)
            self.assertEqual(note, "ok")
            self.assertEqual(calls, [
                f"/cards/{GID}/population?parsed_description=true",
                f"/cards/{GID}/population/history?interval=week",
            ])
            receipt = json.loads((Path(directory) / GID / "identity.receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["entityGemrateId"], GID)

    def test_fetch_current_population_requests_parsed_description(self) -> None:
        calls: list[str] = []

        def fake_api_get(path: str, key: str, delay: float):
            calls.append(path)
            return 200, direct(1200, "2026-07-23")

        with patch.object(source, "_api_get", side_effect=fake_api_get):
            payload, note = source.fetch_current_population(GID, "not-a-real-key", 0.0)

        self.assertEqual(note, "ok")
        self.assertIsNotNone(payload)
        self.assertEqual(calls, [f"/cards/{GID}/population?parsed_description=true"])

    def test_fetch_card_does_not_persist_when_identity_receipt_is_invalid(self) -> None:
        invalid = direct(1200, "2026-07-23")
        invalid["data"]["universal_gemrate_id"] = None
        invalid["data"]["is_universal_match"] = True

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(source, "_api_get", return_value=(200, invalid)):
                ok, note = source.fetch_card(GID, "not-a-real-key", 0.0, history=False, cards_dir=Path(directory))

            self.assertFalse(ok)
            self.assertEqual(note, "IDENTITY_RECEIPT_INVALID")
            self.assertFalse((Path(directory) / GID / "population.json").exists())

    def test_identity_receipts_rebuilds_and_resume_requires_verified_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cards_dir = Path(directory) / "cards"
            card_dir = cards_dir / GID
            card_dir.mkdir(parents=True)
            (card_dir / "population.json").write_text(json.dumps(direct(1200, "2026-07-23")), encoding="utf-8")
            public_only_dir = cards_dir / ("b" * 40)
            public_only_dir.mkdir()
            (public_only_dir / "card_details.json").write_text("{}", encoding="utf-8")

            result = source.rebuild_direct_identity_receipts(cards_dir)
            self.assertEqual(result["attempted"], 1)
            self.assertEqual(result["succeeded"], 1)
            self.assertEqual(result["failed"], 0)
            self.assertEqual(result["cached"], 0)
            receipt_path = card_dir / "identity.receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["sourcePointer"], "population.json")
            self.assertEqual(receipt["requestedGemrateId"], GID)

            resumed = source.rebuild_direct_identity_receipts(cards_dir, resume=True)
            self.assertEqual(resumed["cached"], 1)
            self.assertEqual(resumed["succeeded"], 0)

            receipt["sourcePointer"] = "wrong.json"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            repaired = source.rebuild_direct_identity_receipts(cards_dir, resume=True)
            self.assertEqual(repaired["cached"], 0)
            self.assertEqual(repaired["succeeded"], 1)

    def test_identity_receipts_retains_offline_failure_reasons_without_touching_raw(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cards_dir = Path(directory) / "cards"
            invalid_dir = cards_dir / GID
            invalid_dir.mkdir(parents=True)
            raw_path = invalid_dir / "population.json"
            raw_path.write_text("{not json", encoding="utf-8")
            public_only_dir = cards_dir / ("b" * 40)
            public_only_dir.mkdir()
            (public_only_dir / "card_details.json").write_text("{}", encoding="utf-8")

            output = io.StringIO()
            errors = io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                rc = source.main(["identity-receipts", "--cards-dir", str(cards_dir)])

            self.assertEqual(rc, 1)
            self.assertIn("attempted=1 succeeded=0 failed=1 cached=0", output.getvalue())
            self.assertIn(GID, errors.getvalue())
            self.assertEqual(raw_path.read_text(encoding="utf-8"), "{not json")

    def test_api_dump_resume_rebuilds_missing_receipt_without_calling_api(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cards_dir = Path(directory)
            card_dir = cards_dir / GID
            card_dir.mkdir()
            (card_dir / "population.json").write_text(json.dumps(direct(1200, "2026-07-23")), encoding="utf-8")
            (card_dir / "history_full.json").write_text("{}", encoding="utf-8")

            with patch.object(source, "_api_get") as api_get:
                rc = source._run_harvest([GID], "not-a-real-key", 0.0, resume=True, history=True,
                                         limit=None, cards_dir=cards_dir)

            self.assertEqual(rc, 0)
            api_get.assert_not_called()
            receipt_path = card_dir / "identity.receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["sourcePointer"], "population.json")

            receipt["sourcePointer"] = "wrong.json"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            with patch.object(source, "_api_get") as api_get:
                self.assertEqual(source._run_harvest([GID], "not-a-real-key", 0.0, resume=True, history=True,
                                                     limit=None, cards_dir=cards_dir), 0)
            api_get.assert_not_called()
            self.assertEqual(json.loads(receipt_path.read_text(encoding="utf-8"))["sourcePointer"], "population.json")

    def test_daily_direct_success_writes_staging_and_promoted_identity_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ids_file = root / "ids.txt"
            ids_file.write_text(f"{GID}\n", encoding="utf-8")
            identities = root / "tracked-universe.json"
            identities.write_text('{"cards": [' + json.dumps(CARD) + "]}", encoding="utf-8")
            args = Namespace(
                ids_file=str(ids_file), speed="fast", with_history=False, no_history=False,
                limit=None, identity_file=str(identities), mirror_root=str(root / "missing-grade10"), volume_slug=None,
            )
            with (
                patch.dict("os.environ", {"GEMRATE_API_KEY": "not-a-real-key"}, clear=True),
                patch.object(source, "OUT_DIR", root / "gemrate"),
                patch.object(source, "CARDS_DIR", root / "gemrate" / "cards"),
                patch.object(source, "fetch_current_population", return_value=(direct(1200, "2026-07-23"), "ok")),
                patch.object(source, "_discover_recap_slugs", return_value=[]),
            ):
                self.assertEqual(source.cmd_daily(args), 0)

            run_receipts = list((root / "gemrate" / "runs").glob(f"*/cards/{GID}/identity.receipt.json"))
            self.assertEqual(len(run_receipts), 1)
            self.assertTrue((root / "gemrate" / "cards" / GID / "identity.receipt.json").is_file())

    def test_daily_invalid_direct_receipt_cannot_promote_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ids_file = root / "ids.txt"
            ids_file.write_text(f"{GID}\n", encoding="utf-8")
            identities = root / "tracked-universe.json"
            identities.write_text('{"cards": [' + json.dumps(CARD) + "]}", encoding="utf-8")
            invalid = direct(1200, "2026-07-23")
            invalid["data"]["universal_gemrate_id"] = None
            invalid["data"]["is_universal_match"] = True
            args = Namespace(
                ids_file=str(ids_file), speed="fast", with_history=False, no_history=False,
                limit=None, identity_file=str(identities), mirror_root=str(root / "missing-grade10"), volume_slug=None,
            )
            with (
                patch.dict("os.environ", {"GEMRATE_API_KEY": "not-a-real-key"}, clear=True),
                patch.object(source, "OUT_DIR", root / "gemrate"),
                patch.object(source, "CARDS_DIR", root / "gemrate" / "cards"),
                patch.object(source, "fetch_current_population", return_value=(invalid, "ok")),
                patch.object(source, "_chrome_card_details", return_value={GID: website(1200, "2026-07-23")}),
            ):
                self.assertEqual(source.cmd_daily(args), 1)

            manifest = json.loads(next((root / "gemrate" / "runs").glob("*/manifest.json")).read_text(encoding="utf-8"))
            self.assertFalse(manifest["promotable"])
            self.assertFalse(manifest["promoted"])
            self.assertEqual(manifest["directIdentityReceiptFailures"], [{
                "gemrateId": GID,
                "reason": "direct_identity_universal_null_mismatch",
            }])
            self.assertFalse((root / "gemrate" / "cards" / GID / "population.json").exists())

    def test_daily_uses_exact_grade10_mirror_without_key_and_promotes_only_complete_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ids_file = root / "ids.txt"
            ids_file.write_text(f"{GID}\n", encoding="utf-8")
            identities = root / "tracked-universe.json"
            identities.write_text('{"cards": [' + json.dumps(CARD) + "]}", encoding="utf-8")
            mirror_root = root / "grade10"
            mirror_path = mirror_root / "cards" / "snkrdunk" / "123" / "populations.json"
            mirror_path.parent.mkdir(parents=True)
            mirror_path.write_text(json.dumps(mirror(1200, "2026-07-23")), encoding="utf-8")
            args = Namespace(
                ids_file=str(ids_file), speed="fast", with_history=False, no_history=False,
                limit=None, identity_file=str(identities), mirror_root=str(mirror_root), volume_slug=None,
            )
            with (
                patch.dict("os.environ", {}, clear=True),
                patch.object(source, "OUT_DIR", root / "gemrate"),
                patch.object(source, "CARDS_DIR", root / "gemrate" / "cards"),
                patch.object(source, "_chrome_card_details", return_value={}),
                patch.object(source, "_discover_recap_slugs", return_value=[]),
            ):
                self.assertEqual(source.cmd_daily(args), 0)

            manifests = list((root / "gemrate" / "runs").glob("*/manifest.json"))
            self.assertEqual(len(manifests), 1)
            document = json.loads(manifests[0].read_text(encoding="utf-8"))
            self.assertTrue(document["promoted"])
            self.assertEqual(document["transports"]["direct_api"]["attempted"], 0)
            self.assertEqual(document["transports"]["grade10_gemrate_mirror"]["succeeded"], 1)
            self.assertEqual(document["history"]["status"], "unavailable")
            current = json.loads((root / "gemrate" / "cards" / GID / "current.json").read_text(encoding="utf-8"))
            self.assertEqual(current["authority"], "gemrate")
            self.assertEqual(current["transport"], "grade10_gemrate_mirror")
            self.assertEqual(current["populationPsa10"], 1200)

    def test_daily_partial_run_never_promotes_a_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ids_file = root / "ids.txt"
            ids_file.write_text(f"{GID}\n", encoding="utf-8")
            args = Namespace(
                ids_file=str(ids_file), speed="fast", with_history=False, no_history=False,
                limit=None, identity_file=str(root / "missing-identities.json"),
                mirror_root=str(root / "missing-grade10"), volume_slug=None,
            )
            with (
                patch.dict("os.environ", {}, clear=True),
                patch.object(source, "OUT_DIR", root / "gemrate"),
                patch.object(source, "CARDS_DIR", root / "gemrate" / "cards"),
                patch.object(source, "_chrome_card_details", return_value={}),
            ):
                self.assertEqual(source.cmd_daily(args), 1)

            manifests = list((root / "gemrate" / "runs").glob("*/manifest.json"))
            self.assertEqual(len(manifests), 1)
            document = json.loads(manifests[0].read_text(encoding="utf-8"))
            self.assertTrue(document["partial"])
            self.assertFalse(document["promotable"])
            self.assertFalse(document["promoted"])
            self.assertFalse((root / "gemrate" / "cards" / GID / "population.json").exists())

    def test_public_card_dump_writes_attempted_succeeded_failed_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ids_file = root / "ids.txt"
            ids_file.write_text(f"{GID}\n", encoding="utf-8")
            manifest_out = root / "manifest.json"
            args = Namespace(
                ids_file=str(ids_file), out=str(root / "cards"), resume=False,
                limit=None, delay=0.0, manifest_out=str(manifest_out),
            )
            with patch.object(
                source,
                "_chrome_card_pages_with_receipts",
                return_value=({GID: website(1200, "2026-07-23")}, []),
            ):
                self.assertEqual(source.cmd_public_card_dump(args), 0)

            manifest = json.loads(manifest_out.read_text(encoding="utf-8"))
            self.assertEqual(manifest["attempted"], 1)
            self.assertEqual(manifest["succeeded"], 1)
            self.assertEqual(manifest["failed"], 0)
            self.assertEqual(manifest["runStatus"], "complete")
            self.assertEqual(manifest["failureReceipts"], [])
            self.assertTrue((root / "cards" / GID / "card_details.json").is_file())

    def test_public_card_dump_preserves_raw_json_separately_and_resume_checks_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = {
                "gemrate_id": GID,
                "year": "2023",
                "set_name": "Example Set",
                "card_number": "001",
                "date": "2026-07-21",
                "population_data": [{"grader": "psa", "grades": {"g10": 1200}}],
            }
            payload, reason = source.build_public_card_page_json_payload(
                GID,
                response_url=f"https://www.gemrate.com/card-details?gemrate_id={GID}",
                response_status=200,
                payload=raw,
                canonical_url="https://www.gemrate.com/card/example",
                title="Example",
                dom_sha256="d" * 64,
                route_verified=True,
            )
            self.assertIsNone(reason)
            source._persist_public_card_capture(root / "cards", GID, payload)
            normalized = json.loads((root / "cards" / GID / "card_details.json").read_text(encoding="utf-8"))
            receipt = json.loads((root / "cards" / GID / "card_details.raw.receipt.json").read_text(encoding="utf-8"))
            self.assertNotIn(source.PRIVATE_PAGE_JSON_FIELD, normalized)
            self.assertEqual(receipt["rawStatus"], "captured")
            self.assertTrue((root / "cards" / GID / receipt["sourcePointer"]).is_file())
            self.assertTrue(source._has_complete_public_card_capture(root / "cards", GID))

            raw_path = root / "cards" / GID / receipt["sourcePointer"]
            raw_path.write_text("{}", encoding="utf-8")
            self.assertFalse(source._has_complete_public_card_capture(root / "cards", GID))
            source._persist_public_card_capture(root / "cards", GID, payload)
            self.assertTrue(source._has_complete_public_card_capture(root / "cards", GID))

    def test_public_card_collection_persists_each_chunk_before_later_interruption(self) -> None:
        second_id = "b" * 40

        def page_payload(gemrate_id: str, population: int) -> dict:
            payload, reason = source.build_public_card_page_json_payload(
                gemrate_id,
                response_url=f"https://www.gemrate.com/card-details?gemrate_id={gemrate_id}",
                response_status=200,
                payload={
                    "gemrate_id": gemrate_id,
                    "year": "2023",
                    "set_name": "Example Set",
                    "card_number": "001",
                    "date": "2026-07-21",
                    "population_data": [{"grader": "psa", "grades": {"g10": population}}],
                },
                canonical_url="https://www.gemrate.com/card/example",
                title="Example",
                dom_sha256="e" * 64,
                route_verified=True,
            )
            self.assertIsNone(reason)
            return payload

        with tempfile.TemporaryDirectory() as directory:
            cards_dir = Path(directory) / "cards"
            with patch.object(
                source,
                "_chrome_card_pages_with_receipts",
                side_effect=[({GID: page_payload(GID, 1200)}, []), OSError("interrupted")],
            ):
                result = source.collect_public_card_details(
                    [GID, second_id], cards_dir=cards_dir, delay=0.0, chunk_size=1,
                )

            self.assertTrue((cards_dir / GID / "card_details.json").is_file())
            self.assertTrue(source._has_complete_public_card_capture(cards_dir, GID))
            self.assertTrue(result["partial"])
            self.assertEqual(result["succeeded"], 1)

            calls: list[list[str]] = []

            def resumed(ids: list[str], delay: float):
                calls.append(ids)
                return {second_id: page_payload(second_id, 1201)}, []

            with patch.object(source, "_chrome_card_pages_with_receipts", side_effect=resumed):
                resumed_result = source.collect_public_card_details(
                    [GID, second_id], cards_dir=cards_dir, delay=0.0, resume=True, chunk_size=1,
                )

            self.assertEqual(calls, [[second_id]])
            self.assertTrue(resumed_result["promotable"])
            self.assertEqual(resumed_result["cached"], 1)

    def test_public_card_dump_turns_browser_failure_into_private_partial_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ids_file = root / "ids.txt"
            ids_file.write_text(f"{GID}\n", encoding="utf-8")
            manifest_out = root / "manifest.json"
            args = Namespace(
                ids_file=str(ids_file), out=str(root / "cards"), resume=False,
                limit=None, delay=0.0, manifest_out=str(manifest_out),
            )
            with patch.object(
                source,
                "_chrome_card_pages_with_receipts",
                side_effect=OSError("browser unavailable"),
            ):
                self.assertEqual(source.cmd_public_card_dump(args), 1)

            manifest = json.loads(manifest_out.read_text(encoding="utf-8"))
            self.assertTrue(manifest["partial"])
            self.assertEqual(manifest["failed"], 1)
            self.assertEqual(manifest["error"], "browser_collection_failed")
            self.assertEqual(manifest["failureReceipts"], [{
                "gemrateId": GID,
                "httpStatus": None,
                "reason": "browser_collection_failed",
            }])

    def test_public_card_dump_records_unauthorized_without_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ids_file = root / "ids.txt"
            ids_file.write_text(f"{GID}\n", encoding="utf-8")
            manifest_out = root / "manifest.json"
            args = Namespace(
                ids_file=str(ids_file), out=str(root / "cards"), resume=False,
                limit=None, delay=0.0, manifest_out=str(manifest_out),
            )
            receipt = {"gemrateId": GID, "httpStatus": 403, "reason": "unauthorized"}
            with patch.object(source, "_chrome_card_pages_with_receipts", return_value=({}, [receipt])):
                self.assertEqual(source.cmd_public_card_dump(args), 1)

            manifest = json.loads(manifest_out.read_text(encoding="utf-8"))
            self.assertEqual(manifest["runStatus"], "partial")
            self.assertFalse(manifest["promotable"])
            self.assertTrue(manifest["partial"])
            self.assertEqual(manifest["failureReceiptCount"], 1)
            self.assertEqual(manifest["failureReceipts"], [receipt])
            self.assertFalse((root / "cards" / GID / "card_details.json").exists())


if __name__ == "__main__":
    unittest.main()
