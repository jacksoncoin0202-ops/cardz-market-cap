#!/usr/bin/env python3
"""Validate and inspect the CARDZ market-data routing contract."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from registry_lineage import (
    RegistryError,
    check_docs,
    explain_identifier as explain_registry_identifier,
    generate_docs,
    render_html_graph,
    render_json,
    render_markdown,
    validate_control_plane,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROUTES = ROOT / "config" / "data-routing.json"
DEFAULT_RELEASE_PROFILE = "relaxed-launch-v1"
RELEASE_PROFILE_IDS = ("strict-v1", "relaxed-launch-v1")
STRICT_IDENTITY_AUTHORITIES = ["gemrate"]
RECOGNIZED_IDENTITY_AUTHORITIES = ["gemrate", "snk", "pricecharting", "psa"]
STRICT_BOOTSTRAP_SOURCES = {"data": [], "images": []}
RELAXED_BOOTSTRAP_SOURCES = {"data": ["grade10"], "images": ["grade10"]}
IMAGE_HARD_EXCLUSIONS = [
    "not_public_allowed",
    "not_raw_front",
    "asset_unreadable",
    "content_hash_invalid",
    "geometry_invalid",
    "sample_or_placeholder",
    "human_rejected",
    "cross_card_duplicate",
    "cross_tcg_duplicate",
]
REQUIRED_METRICS = {
    "candidate_identity",
    "psa10_population",
    "psa10_population_history",
    "psa10_reference_price",
    "tracked_sales",
    "price_change_1d_7d_30d",
    "psa10_market_cap",
}
FORBIDDEN_EXTERNAL_PRODUCT_MARKERS = {
    "cardzos",
    "cardzpas10",
    "jlp",
    "kado",
}
FORBIDDEN_STALE_PRINTING_MARKERS = {
    "locale does not split identity",
    "language remains provenance only",
    "all six printing fields",
}
CANONICAL_PRINTING_IDENTITY = {
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
}
CANONICAL_PRINTING_PROMOTION_RULE = (
    "exact GemRate and exact SNK bindings are necessary but not sufficient; "
    "all seven printing fields (tcgCode, cardLanguage, setName, collectorNumber, "
    "editionCode, parallelCode, finishCode), the QC card evidence hash and "
    "content-addressed field-level source receipts must match before canonicalization; "
    "cardLanguage is identity-bearing and separates otherwise identical JA/EN printings; "
    "ranking boards may remain language-combined"
)


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        + b"\n"
    )


def release_profile_policy(
    document: Mapping[str, Any], profile_id: str = DEFAULT_RELEASE_PROFILE
) -> dict[str, Any]:
    """Return a detached named release policy from the routing registry."""

    normalized = str(profile_id).strip()
    if normalized not in RELEASE_PROFILE_IDS:
        choices = ", ".join(RELEASE_PROFILE_IDS)
        raise ValueError(
            f"unknown release profile {normalized!r}; expected one of {choices}"
        )
    profiles = document.get("releaseProfiles")
    if not isinstance(profiles, Mapping):
        raise ValueError("releaseProfiles must be an object")
    policy = profiles.get(normalized)
    if not isinstance(policy, Mapping):
        raise ValueError(
            f"release profile {normalized!r} is missing from releaseProfiles"
        )
    return copy.deepcopy(dict(policy))


def release_profile_sha256(profile_id: str, policy: Mapping[str, Any]) -> str:
    """Hash the profile name and policy together for immutable run bindings."""

    payload = {
        "releaseProfile": str(profile_id).strip(),
        "policy": dict(policy),
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def load_release_profile(
    document: Mapping[str, Any], profile_id: str = DEFAULT_RELEASE_PROFILE
) -> dict[str, Any]:
    """Return the deterministic public envelope consumed by QC and export receipts."""

    normalized = str(profile_id).strip()
    policy = release_profile_policy(document, normalized)
    return {
        "releaseProfile": normalized,
        "policy": policy,
        "policySha256": release_profile_sha256(normalized, policy),
    }


def _validate_release_profiles(
    document: Mapping[str, Any], legacy_public_release: Mapping[str, Any]
) -> None:
    if document.get("defaultReleaseProfile") != DEFAULT_RELEASE_PROFILE:
        raise ValueError(
            "defaultReleaseProfile must be relaxed-launch-v1; strict-v1 is explicit audit only"
        )
    profiles = document.get("releaseProfiles")
    if not isinstance(profiles, Mapping) or set(profiles) != set(RELEASE_PROFILE_IDS):
        raise ValueError(
            "releaseProfiles must contain exactly strict-v1 and relaxed-launch-v1"
        )

    strict = release_profile_policy(document, "strict-v1")
    relaxed = release_profile_policy(document, "relaxed-launch-v1")
    legacy_fields = (
        "coverageClaims",
        "requestedCount",
        "minimumVerifiedCount",
        "priceFreshnessHoursMaximum",
        "populationFreshnessHoursMaximum",
        "trackedPsa10Sales30dMinimumInclusive",
        "imageSemanticStatus",
        "acceptedBootstrapSources",
        "publisher",
        "pointerPromotion",
    )
    if any(relaxed.get(field) != legacy_public_release.get(field) for field in legacy_fields):
        raise ValueError(
            "relaxed-launch-v1 must remain byte-for-byte compatible with publicReleasePolicy fields"
        )

    if (
        strict.get("identityStatus") != "confirmed"
        or strict.get("allowedIdentityStatuses") != ["confirmed"]
        or strict.get("minimumExactAuthorityBindings") != 1
        or strict.get("recognizedIdentityAuthorities") != STRICT_IDENTITY_AUTHORITIES
        or strict.get("acceptedBootstrapSources") != STRICT_BOOTSTRAP_SOURCES
        or strict.get("requireCompleteSevenPartPrinting") is not True
        or strict.get("requireCanonicalPrinting") is not True
        or strict.get("identityConflictAction") != "block"
        or strict.get("priceSelection") != "arithmetic_mean_of_fresh_authority_families"
        or strict.get("priceSpreadMaximumRatio") != 2.0
        or strict.get("priceSpreadAction") != "block"
        or strict.get("lastGoodMaximumHours") != 48
        or strict.get("imageHumanReviewRequired") is not True
        or strict.get("imageHardExclusions") != IMAGE_HARD_EXCLUSIONS
        or strict.get("allPoolFailureAction") != "block_release"
        or strict.get("publicCardsMaximum") != 500
        or strict.get("publicImageAssetsMaximum") != 1500
    ):
        raise ValueError("strict-v1 release profile no longer matches the strict release contract")

    if (
        relaxed.get("coverageClaims") != legacy_public_release.get("coverageClaims")
        or relaxed.get("requestedCount") != 100
        or relaxed.get("minimumVerifiedCount") != 100
        or relaxed.get("priceFreshnessHoursMaximum") != 30 * 24
        or relaxed.get("populationFreshnessHoursMaximum") != 168
        or relaxed.get("trackedPsa10Sales30dMinimumInclusive") != 5
        or relaxed.get("imageSemanticStatus") != "highest_confidence_eligible"
        or relaxed.get("identityStatus") != "provisional"
        or relaxed.get("allowedIdentityStatuses") != ["confirmed", "provisional"]
        or relaxed.get("minimumExactAuthorityBindings") != 1
        or relaxed.get("recognizedIdentityAuthorities") != RECOGNIZED_IDENTITY_AUTHORITIES
        or relaxed.get("acceptedBootstrapSources") != RELAXED_BOOTSTRAP_SOURCES
        or relaxed.get("requireCompleteSevenPartPrinting") is not True
        or relaxed.get("requireCanonicalPrinting") is not False
        or relaxed.get("identityConflictAction") != "block"
        or relaxed.get("priceSelection")
        != "pricecharting_explicit_psa10_then_ebay_exact_psa10_sold_median_then_snk_exact_psa10"
        or relaxed.get("priceSpreadMaximumRatio") != 2.0
        or relaxed.get("priceSpreadAction") != "warning"
        or relaxed.get("lastGoodMaximumHours") != 30 * 24
        or relaxed.get("imageHumanReviewRequired") is not False
        or relaxed.get("imageHardExclusions") != IMAGE_HARD_EXCLUSIONS
        or relaxed.get("allPoolFailureAction") != "per_card_exclude"
        or relaxed.get("publicCardsMaximum") != 1000
        or relaxed.get("publicImageAssetsMaximum") != 3000
        or relaxed.get("publisher") != legacy_public_release.get("publisher")
        or relaxed.get("pointerPromotion") != legacy_public_release.get("pointerPromotion")
    ):
        raise ValueError("relaxed-launch-v1 release profile no longer matches the approved launch contract")


def load_and_validate(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    serialized = json.dumps(document, ensure_ascii=False, sort_keys=True).casefold()
    forbidden = sorted(
        marker
        for marker in FORBIDDEN_EXTERNAL_PRODUCT_MARKERS
        if marker in serialized
    )
    if forbidden:
        raise ValueError(
            "Cardz Market Cap routing cannot reference external products: "
            + ", ".join(forbidden)
        )
    stale_printing = sorted(
        marker for marker in FORBIDDEN_STALE_PRINTING_MARKERS if marker in serialized
    )
    if stale_printing:
        raise ValueError(
            "canonical printing must use seven-part language-inclusive identity; "
            "stale language-neutral printing text is forbidden: "
            + ", ".join(stale_printing)
        )
    rules = document.get("rules", {})
    if rules.get("populationAuthority") != "gemrate":
        raise ValueError("PSA population authority must be GemRate")
    if rules.get("trackedIndexes") != ["tcg", "pokemon", "one-piece"]:
        raise ValueError("tracked indexes must be tcg, pokemon, and one-piece")
    if (
        rules.get("canonicalPrintingIdentity") != CANONICAL_PRINTING_IDENTITY
        or rules.get("languagePartitioning") is not False
        or rules.get("languagePartitioningScope") != "ranking_board_grouping_only"
    ):
        raise ValueError(
            "canonical printing must use seven language-inclusive identity fields; "
            "language-combined handling is ranking-board grouping only"
        )
    routes = document.get("routes")
    if not isinstance(routes, list):
        raise ValueError("routes must be an array")
    by_metric = {str(route.get("metric")): route for route in routes if isinstance(route, dict)}
    missing = REQUIRED_METRICS - set(by_metric)
    if missing:
        raise ValueError(f"missing data routes: {sorted(missing)}")
    identity = by_metric["candidate_identity"]
    if (
        identity.get("transport", {}).get("promotionRule")
        != CANONICAL_PRINTING_PROMOTION_RULE
    ):
        raise ValueError(
            "candidate identity promotion must preserve the seven-part "
            "language-inclusive canonical printing contract"
        )
    population = by_metric["psa10_population"]
    if population.get("primary") != "gemrate" or population.get("fallback") != []:
        raise ValueError("PSA population cannot use a non-GemRate fallback")
    price = by_metric["psa10_reference_price"]
    expected_price_sources = [
        "pricecharting_explicit_psa10",
        "snk_exact_psa10",
        "ebay_exact_psa10_sold_median",
    ]
    expected_price_qc = {
        "minimumFreshExactSourceFamilies": 1,
        "freshnessHours": 48,
        "maximumPriceRatio": 2.0,
        "aggregation": "arithmetic_mean_of_fresh_authority_families",
        "failureLane": "psa10_price",
    }
    if (
        price.get("primary") != "cardz_cross_source_qc"
        or price.get("acceptedPrimarySources") != expected_price_sources
        or price.get("qcRule") != expected_price_qc
        or price.get("fallback") != []
    ):
        raise ValueError(
            "reference price must use exact PC, SNK and eBay under CARDZ cross-source QC"
        )
    sales = by_metric["tracked_sales"]
    if (
        sales.get("primary") != "snk_grade10_pricecharting_exact_psa10_sales"
        or sales.get("acceptedPrimarySources")
        != [
            "snk_exact_recent_trades",
            "grade10_exact_ebay_psa10_completed_sales",
            "pricecharting_product_page_ebay_completed_sales",
        ]
    ):
        raise ValueError(
            "tracked sales must route equally through exact SNK, Grade10 eBay, "
            "and PriceCharting eBay"
        )
    market_cap = by_metric["psa10_market_cap"]
    if set(market_cap.get("dependsOn") or []) != {"psa10_population", "psa10_reference_price"}:
        raise ValueError("market cap must depend on GemRate population and reference price")
    public_release = document.get("publicReleasePolicy")
    if (
        not isinstance(public_release, dict)
        or public_release.get("trackedPsa10Sales30dMinimumInclusive") != 5
        or "trackedPsa10Sales30dMinimumExclusive" in public_release
    ):
        raise ValueError(
            "public release requires at least 5 exact pure PSA10 sales in 30 days"
        )
    _validate_release_profiles(document, public_release)
    validate_control_plane(document)
    return document


def load_registry(path: Path = DEFAULT_ROUTES) -> dict[str, Any]:
    """Load the one routing registry that controls backend data lineage."""

    return load_and_validate(path.resolve())


def validate_registry(document: dict[str, Any]) -> dict[str, Any]:
    """Validate control-plane metadata supplied by an importing backend command."""

    validate_control_plane(document)
    return document


def explain_identifier(identifier: str, path: Path = DEFAULT_ROUTES) -> dict[str, Any]:
    return explain_registry_identifier(load_registry(path), identifier)


def render_registry(document: dict[str, Any], output_format: str) -> str:
    renderers = {
        "json": render_json,
        "markdown": render_markdown,
        "html": render_html_graph,
    }
    return renderers[output_format](document)


def generate_registry_docs(path: Path = DEFAULT_ROUTES, output_dir: Path = ROOT / "docs" / "generated") -> list[Path]:
    return generate_docs(load_registry(path), output_dir)


def check_registry_docs(path: Path = DEFAULT_ROUTES, output_dir: Path = ROOT / "docs" / "generated") -> list[str]:
    return check_docs(load_registry(path), output_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect CARDZ data-source routing")
    parser.add_argument("--routes", type=Path, default=DEFAULT_ROUTES)
    parser.add_argument("--metric")
    parser.add_argument("--identifier", help="Explain a route, tool, profile, view or consumer identifier")
    parser.add_argument("--format", choices=("json", "markdown", "html"), default="json")
    parser.add_argument("--output", type=Path, help="Write rendered registry output to this file")
    parser.add_argument("--generate-docs", type=Path, metavar="DIR")
    parser.add_argument("--check-docs", type=Path, metavar="DIR")
    args = parser.parse_args()
    document = load_and_validate(args.routes.resolve())

    if args.generate_docs:
        written = generate_docs(document, args.generate_docs.resolve())
        print(json.dumps({"status": "generated", "files": [str(path) for path in written]}, ensure_ascii=False))
        return 0
    if args.check_docs:
        drift = check_docs(document, args.check_docs.resolve())
        print(json.dumps({"status": "valid" if not drift else "drift", "drift": drift}, ensure_ascii=False))
        return 0 if not drift else 1
    if args.identifier:
        rendered = json.dumps(explain_registry_identifier(document, args.identifier), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8", newline="\n")
        else:
            print(rendered, end="")
        return 0
    if args.format != "json" or args.output:
        rendered = render_registry(document, args.format)
        if args.output:
            args.output.write_text(rendered, encoding="utf-8", newline="\n")
        else:
            print(rendered, end="")
        return 0
    routes = document["routes"]
    if args.metric:
        routes = [route for route in routes if route["metric"] == args.metric]
        if not routes:
            raise SystemExit(f"unknown metric: {args.metric}")
    print(json.dumps({"status": "valid", "routes": routes}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RegistryError as exc:
        raise SystemExit(str(exc))
