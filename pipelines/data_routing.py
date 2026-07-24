#!/usr/bin/env python3
"""Validate and inspect the CARDZ market-data routing contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

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
REQUIRED_METRICS = {
    "candidate_identity",
    "psa10_population",
    "psa10_population_history",
    "psa10_reference_price",
    "tracked_sales",
    "price_change_1d_7d_30d",
    "psa10_market_cap",
}


def load_and_validate(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("rules", {}).get("populationAuthority") != "gemrate":
        raise ValueError("PSA population authority must be GemRate")
    if document.get("rules", {}).get("trackedIndexes") != ["tcg", "pokemon", "one-piece"]:
        raise ValueError("tracked indexes must be tcg, pokemon, and one-piece")
    routes = document.get("routes")
    if not isinstance(routes, list):
        raise ValueError("routes must be an array")
    by_metric = {str(route.get("metric")): route for route in routes if isinstance(route, dict)}
    missing = REQUIRED_METRICS - set(by_metric)
    if missing:
        raise ValueError(f"missing data routes: {sorted(missing)}")
    population = by_metric["psa10_population"]
    if population.get("primary") != "gemrate" or population.get("fallback") != []:
        raise ValueError("PSA population cannot use a non-GemRate fallback")
    price = by_metric["psa10_reference_price"]
    if price.get("primary") != "snk" or price.get("fallback") != []:
        raise ValueError("reference price must use SNK without a G10 ranking fallback")
    if price.get("secondary") != ["ebay_psa10_sold_validation"]:
        raise ValueError("reference price must validate against exact eBay PSA 10 sold data")
    sales = by_metric["tracked_sales"]
    if sales.get("primary") != "snk_recent_trades" or sales.get("secondary") != ["ebay_psa10_sold"]:
        raise ValueError("tracked sales must route through SNK and eBay")
    market_cap = by_metric["psa10_market_cap"]
    if set(market_cap.get("dependsOn") or []) != {"psa10_population", "psa10_reference_price"}:
        raise ValueError("market cap must depend on GemRate population and reference price")
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
