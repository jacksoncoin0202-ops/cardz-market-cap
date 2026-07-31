from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from data_routing import (  # noqa: E402
    DEFAULT_ROUTES,
    load_and_validate,
    load_release_profile,
    release_profile_sha256,
)
from canonical_db_qc import MIN_PURE_PSA10_SALES_30D as DB_QC_SALES_MINIMUM  # noqa: E402
from canonical_public_snapshot import MIN_PURE_PSA10_SALES_30D as EXPORT_SALES_MINIMUM  # noqa: E402
from fe_display_eligibility import MIN_PURE_PSA10_SALES_30D as FE_SALES_MINIMUM  # noqa: E402
from public_snapshot_qc import MIN_PURE_PSA10_SALES_30D as PUBLIC_QC_SALES_MINIMUM  # noqa: E402


class DataRoutingTests(unittest.TestCase):
    def test_repository_contract_binds_population_to_gemrate_only(self) -> None:
        document = load_and_validate(DEFAULT_ROUTES)
        population = next(route for route in document["routes"] if route["metric"] == "psa10_population")
        self.assertEqual(population["primary"], "gemrate")
        self.assertEqual(population["fallback"], [])
        self.assertEqual(population["failureMode"], "exclude_from_ranking")

    def test_non_gemrate_population_fallback_is_rejected(self) -> None:
        document = json.loads(DEFAULT_ROUTES.read_text(encoding="utf-8"))
        population = next(route for route in document["routes"] if route["metric"] == "psa10_population")
        population["fallback"] = ["g10"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routes.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "non-GemRate fallback"):
                load_and_validate(path)

    def test_price_qc_rejects_derived_g10_but_exact_sales_sources_are_equal(self) -> None:
        document = load_and_validate(DEFAULT_ROUTES)
        routes = {route["metric"]: route for route in document["routes"]}
        price = routes["psa10_reference_price"]
        self.assertEqual(price["primary"], "cardz_cross_source_qc")
        self.assertEqual(
            price["acceptedPrimarySources"],
            [
                "pricecharting_explicit_psa10",
                "snk_exact_psa10",
                "ebay_exact_psa10_sold_median",
            ],
        )
        self.assertEqual(
            price["qcRule"],
            {
                "minimumFreshExactSourceFamilies": 1,
                "freshnessHours": 48,
                "maximumPriceRatio": 2.0,
                "aggregation": "arithmetic_mean_of_fresh_authority_families",
                "failureLane": "psa10_price",
            },
        )
        self.assertEqual(price["fallback"], [])
        self.assertEqual(price["bootstrapEvidence"], ["grade10_last_good"])
        sales = routes["tracked_sales"]
        self.assertEqual(
            sales["primary"],
            "snk_grade10_pricecharting_exact_psa10_sales",
        )
        self.assertEqual(
            sales["acceptedPrimarySources"],
            [
                "snk_exact_recent_trades",
                "grade10_exact_ebay_psa10_completed_sales",
                "pricecharting_product_page_ebay_completed_sales",
            ],
        )
        self.assertEqual(
            sales["bootstrapEvidence"],
            [
                "grade10_unbound_ebay_sale_history",
                "pricecharting_unbound_product_page_sales",
            ],
        )

    def test_g10_cannot_become_reference_price_fallback(self) -> None:
        document = json.loads(DEFAULT_ROUTES.read_text(encoding="utf-8"))
        price = next(route for route in document["routes"] if route["metric"] == "psa10_reference_price")
        price["fallback"] = ["grade10_last_good"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routes.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "CARDZ cross-source QC"):
                load_and_validate(path)

    def test_registry_requires_valid_data_cleaning_policy(self) -> None:
        document = json.loads(DEFAULT_ROUTES.read_text(encoding="utf-8"))
        document["dataCleaningPolicy"]["rulesPath"] = "config/not-a-rule.json"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routes.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "data-cleaning rules invalid"):
                load_and_validate(path)

    def test_default_release_is_relaxed_and_strict_remains_explicit_regression_profile(self) -> None:
        document = load_and_validate(DEFAULT_ROUTES)
        default = load_release_profile(document)
        strict = load_release_profile(document, "strict-v1")
        minimum = document["publicReleasePolicy"][
            "trackedPsa10Sales30dMinimumInclusive"
        ]
        self.assertEqual(default["releaseProfile"], "relaxed-launch-v1")
        self.assertEqual(default["policy"]["trackedPsa10Sales30dMinimumInclusive"], minimum)
        self.assertEqual(minimum, 5)
        self.assertEqual(strict["policy"]["trackedPsa10Sales30dMinimumInclusive"], 10)
        self.assertEqual(strict["policy"]["minimumExactAuthorityBindings"], 1)
        self.assertEqual(strict["policy"]["recognizedIdentityAuthorities"], ["gemrate"])
        self.assertEqual(strict["policy"]["acceptedBootstrapSources"], {"data": [], "images": []})
        self.assertEqual(
            default["policy"]["acceptedBootstrapSources"],
            {"data": ["grade10"], "images": ["grade10"]},
        )
        self.assertEqual(
            {
                DB_QC_SALES_MINIMUM,
                EXPORT_SALES_MINIMUM,
                FE_SALES_MINIMUM,
                PUBLIC_QC_SALES_MINIMUM,
            },
            {strict["policy"]["trackedPsa10Sales30dMinimumInclusive"]},
        )

    def test_named_release_profiles_are_deterministic_and_keep_strict_explicit(self) -> None:
        document = load_and_validate(DEFAULT_ROUTES)
        strict = load_release_profile(document, "strict-v1")
        relaxed = load_release_profile(document)

        self.assertEqual(strict["releaseProfile"], "strict-v1")
        self.assertEqual(
            strict["policy"]["trackedPsa10Sales30dMinimumInclusive"],
            10,
        )
        self.assertEqual(strict["policy"]["priceFreshnessHoursMaximum"], 48)
        self.assertEqual(strict["policy"]["priceSpreadAction"], "block")
        self.assertEqual(strict["policy"]["publicCardsMaximum"], 500)
        self.assertEqual(strict["policy"]["publicImageAssetsMaximum"], 1500)
        self.assertEqual(
            strict["policySha256"],
            release_profile_sha256(strict["releaseProfile"], strict["policy"]),
        )

        self.assertEqual(relaxed["releaseProfile"], "relaxed-launch-v1")
        self.assertEqual(relaxed["policy"]["minimumExactAuthorityBindings"], 1)
        self.assertEqual(
            relaxed["policy"]["trackedPsa10Sales30dMinimumInclusive"], 5
        )
        self.assertEqual(relaxed["policy"]["priceFreshnessHoursMaximum"], 30 * 24)
        self.assertEqual(relaxed["policy"]["priceSpreadAction"], "warning")
        self.assertEqual(relaxed["policy"]["identityStatus"], "provisional")
        self.assertEqual(
            relaxed["policy"]["acceptedBootstrapSources"],
            {"data": ["grade10"], "images": ["grade10"]},
        )
        self.assertEqual(relaxed["policy"]["publicCardsMaximum"], 1000)
        self.assertEqual(relaxed["policy"]["publicImageAssetsMaximum"], 3000)
        self.assertNotEqual(strict["policySha256"], relaxed["policySha256"])

        relaxed["policy"]["trackedPsa10Sales30dMinimumInclusive"] = 999
        self.assertEqual(
            document["releaseProfiles"]["relaxed-launch-v1"][
                "trackedPsa10Sales30dMinimumInclusive"
            ],
            5,
        )

    def test_unknown_release_profile_is_rejected(self) -> None:
        document = load_and_validate(DEFAULT_ROUTES)
        with self.assertRaisesRegex(ValueError, "unknown release profile"):
            load_release_profile(document, "unknown-v1")

    def test_relaxed_release_profile_contract_drift_is_rejected(self) -> None:
        document = json.loads(DEFAULT_ROUTES.read_text(encoding="utf-8"))
        document["releaseProfiles"]["relaxed-launch-v1"]["priceSpreadAction"] = "block"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routes.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "approved launch contract"):
                load_and_validate(path)

    def test_only_relaxed_launch_accepts_grade10_bootstrap_data_and_images(self) -> None:
        document = load_and_validate(DEFAULT_ROUTES)
        strict = load_release_profile(document, "strict-v1")["policy"]
        relaxed = load_release_profile(document, "relaxed-launch-v1")["policy"]
        self.assertEqual(strict["acceptedBootstrapSources"], {"data": [], "images": []})
        self.assertEqual(
            relaxed["acceptedBootstrapSources"],
            {"data": ["grade10"], "images": ["grade10"]},
        )

        mutated = json.loads(DEFAULT_ROUTES.read_text(encoding="utf-8"))
        mutated["releaseProfiles"]["relaxed-launch-v1"]["acceptedBootstrapSources"]["images"] = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routes.json"
            path.write_text(json.dumps(mutated), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "relaxed-launch-v1 must remain"):
                load_and_validate(path)

    def test_default_relaxed_profile_must_match_legacy_release_policy(self) -> None:
        document = json.loads(DEFAULT_ROUTES.read_text(encoding="utf-8"))
        document["releaseProfiles"]["relaxed-launch-v1"][
            "trackedPsa10Sales30dMinimumInclusive"
        ] = 9
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routes.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "relaxed-launch-v1 must remain"):
                load_and_validate(path)

    def test_old_positive_only_liquidity_policy_is_rejected(self) -> None:
        document = json.loads(DEFAULT_ROUTES.read_text(encoding="utf-8"))
        release = document["publicReleasePolicy"]
        release.pop("trackedPsa10Sales30dMinimumInclusive")
        release["trackedPsa10Sales30dMinimumExclusive"] = 0
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routes.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "at least 5 exact pure PSA10"):
                load_and_validate(path)

    def test_canonical_printing_contract_is_seven_part_and_language_inclusive(self) -> None:
        document = load_and_validate(DEFAULT_ROUTES)
        self.assertEqual(
            document["rules"]["canonicalPrintingIdentity"],
            {
                "partCount": 7,
                "fields": [
                    "tcgCode",
                    "cardLanguage",
                    "setName",
                    "collectorNumber",
                    "editionCode",
                    "parallelCode",
                    "finishCode",
                ],
                "languageInclusive": True,
            },
        )
        self.assertEqual(
            document["rules"]["languagePartitioningScope"],
            "ranking_board_grouping_only",
        )

        mutations = {
            "missing card language": lambda candidate: candidate["rules"][
                "canonicalPrintingIdentity"
            ]["fields"].remove("cardLanguage"),
            "language not identity-bearing": lambda candidate: candidate["rules"][
                "canonicalPrintingIdentity"
            ].update(languageInclusive=False),
            "scope not ranking-only": lambda candidate: candidate["rules"].update(
                languagePartitioningScope="canonical_printing"
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                candidate = json.loads(DEFAULT_ROUTES.read_text(encoding="utf-8"))
                mutate(candidate)
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "routes.json"
                    path.write_text(json.dumps(candidate), encoding="utf-8")
                    with self.assertRaisesRegex(
                        ValueError,
                        "seven language-inclusive identity fields",
                    ):
                        load_and_validate(path)

    def test_stale_six_part_promotion_rule_is_rejected(self) -> None:
        document = json.loads(DEFAULT_ROUTES.read_text(encoding="utf-8"))
        identity = next(
            route
            for route in document["routes"]
            if route["metric"] == "candidate_identity"
        )
        identity["transport"]["promotionRule"] = (
            "all six printing fields; language remains provenance only"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routes.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError,
                "seven-part language-inclusive",
            ):
                load_and_validate(path)

    def test_stale_language_neutral_text_anywhere_is_rejected(self) -> None:
        document = json.loads(DEFAULT_ROUTES.read_text(encoding="utf-8"))
        document["workItems"][0]["acceptance"].append(
            "locale does not split identity"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routes.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError,
                "stale language-neutral printing text",
            ):
                load_and_validate(path)

    def test_external_card_products_cannot_enter_market_cap_routing(self) -> None:
        clean = json.loads(DEFAULT_ROUTES.read_text(encoding="utf-8"))
        serialized = json.dumps(clean, ensure_ascii=False).casefold()
        for marker in ("cardzos", "cardzpas10", "jlp", "kado"):
            self.assertNotIn(marker, serialized)

        for marker in ("cardzos", "cardzpas10", "jlp", "kado"):
            document = json.loads(DEFAULT_ROUTES.read_text(encoding="utf-8"))
            document["agentExecution"]["capabilityRoutes"].append(
                {
                    "id": f"forbidden-{marker}",
                    "adapterCandidates": [marker],
                }
            )
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "routes.json"
                path.write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaisesRegex(
                    ValueError,
                    "cannot reference external products",
                ):
                    load_and_validate(path)


if __name__ == "__main__":
    unittest.main()
