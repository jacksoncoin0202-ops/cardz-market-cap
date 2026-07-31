#!/usr/bin/env python3
"""Validate and explain CARDZ's deterministic data-cleaning control plane."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES = ROOT / "config" / "data-cleaning-rules.json"
DEFAULT_SCHEMA = ROOT / "config" / "data-cleaning-rules.schema.json"


class DataCleaningRuleError(ValueError):
    """Raised when a cleaning rule weakens canonical data integrity."""


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DataCleaningRuleError(f"{label} must be an object")
    return value


def _named_layers(value: object) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, list):
        raise DataCleaningRuleError("layers must be an array")
    result: dict[str, Mapping[str, Any]] = {}
    for layer in value:
        item = _object(layer, "layer")
        layer_id = item.get("id")
        if not isinstance(layer_id, str) or not layer_id:
            raise DataCleaningRuleError("each layer requires id")
        if layer_id in result:
            raise DataCleaningRuleError(f"duplicate layer: {layer_id}")
        result[layer_id] = item
    return result


def load_and_validate(path: Path = DEFAULT_RULES, schema_path: Path = DEFAULT_SCHEMA) -> dict[str, Any]:
    """Load the rules and enforce the safety invariants without a runtime dependency."""

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise DataCleaningRuleError("data-cleaning schema must use JSON Schema draft 2020-12")
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schemaVersion") != "1.0":
        raise DataCleaningRuleError("data-cleaning rules schemaVersion must be 1.0")
    required = schema.get("required")
    if not isinstance(required, list) or any(key not in document for key in required):
        raise DataCleaningRuleError("data-cleaning rules are missing required top-level sections")
    allowed = set(schema.get("properties", {}))
    if set(document) - allowed:
        raise DataCleaningRuleError("data-cleaning rules contain unsupported top-level sections")

    layers = _named_layers(document.get("layers"))
    if set(layers) != {"raw_landing", "normalized_observation", "canonical_facts", "derived_daily_snapshot"}:
        raise DataCleaningRuleError("data layers must be raw_landing, normalized_observation, canonical_facts and derived_daily_snapshot")
    if layers["raw_landing"].get("mysqlPolicy") != "no_raw_payload_body":
        raise DataCleaningRuleError("raw landing payloads must not be stored as MySQL bodies")
    if layers["normalized_observation"].get("mysqlPolicy") != "typed observation plus payload hash and private pointer":
        raise DataCleaningRuleError("normalized observations must retain a private payload pointer")

    identity = _object(document.get("identity"), "identity")
    expected_tuple = [
        "tcg",
        "card_language",
        "set",
        "collector_number_complete",
        "edition",
        "parallel",
        "finish",
    ]
    if (
        identity.get("tuple") != expected_tuple
        or identity.get("matchPolicy")
        != "exact_all_fields; card_language is identity-bearing and splits otherwise identical canonical printings; ranking boards may remain language-combined; no first-search-result, guessed suffix or padding"
    ):
        raise DataCleaningRuleError(
            "identity must use the complete exact seven-part language-inclusive printing tuple"
        )
    if identity.get("failureTarget") != "identity_review_queue":
        raise DataCleaningRuleError("identity failures must enter identity_review_queue")

    metrics = _object(document.get("metricPolicies"), "metricPolicies")
    population = _object(metrics.get("psa10Population"), "psa10Population")
    if population.get("authority") != "gemrate" or population.get("requiredGrader") != "PSA" or population.get("requiredGrade") != "10":
        raise DataCleaningRuleError("PSA 10 population authority must be GemRate PSA grade 10")
    price = _object(metrics.get("psa10ReferencePrice"), "psa10ReferencePrice")
    if price.get("authority") != "snk_exact_psa10_history" or "used_min_price" not in price.get("forbiddenFields", []):
        raise DataCleaningRuleError("reference price must be exact SNK PSA 10 history and reject used_min_price")
    sales = _object(metrics.get("trackedSales"), "trackedSales")
    if sales.get("allowedGrades") != ["PSA 10"] or sales.get("bundlePolicy", "").startswith("exclude_bundle") is False:
        raise DataCleaningRuleError("tracked sales must be exact PSA 10 and exclude bundles from unit price")

    field_policies = _object(document.get("fieldPolicies"), "fieldPolicies")
    timestamps = _object(field_policies.get("timestamps"), "timestamps")
    if timestamps.get("relativeDate") != "quarantine_when_no_absolute_provider_date":
        raise DataCleaningRuleError("relative provider dates must be quarantined")
    currency = _object(field_policies.get("currency"), "currency")
    if currency.get("base") != "USD" or "never rewrite historical USD" not in str(currency.get("fx")):
        raise DataCleaningRuleError("currency must retain native value and dated historical FX")
    missing = _object(field_policies.get("missingValues"), "missingValues")
    if missing.get("policy") != "null_with_status_never_zero":
        raise DataCleaningRuleError("missing metrics must remain null with status, never zero")

    freshness = _object(document.get("freshness"), "freshness")
    if _object(freshness.get("psa10Population"), "population freshness").get("readyHours") != 48:
        raise DataCleaningRuleError("PSA 10 population must remain ready for at most 48 hours")
    price_freshness = _object(freshness.get("psa10ReferencePrice"), "price freshness")
    if price_freshness.get("readyHours") != 30 or price_freshness.get("staleHours") != 48:
        raise DataCleaningRuleError("PSA 10 price freshness must remain 30h ready / 48h stale")

    bands = _object(document.get("populationBands"), "populationBands")
    if _object(bands.get("formalRanking"), "formal population band").get("minimumInclusivePsa10Population") != 1000:
        raise DataCleaningRuleError("formal ranking must require PSA 10 POP >=1000")
    pre_entry = _object(bands.get("preEntryRadar"), "pre-entry population band")
    if pre_entry.get("minimumInclusivePsa10Population") != 971 or pre_entry.get("maximumInclusivePsa10Population") != 999:
        raise DataCleaningRuleError("pre-entry radar must be PSA 10 POP 971-999")
    if _object(bands.get("discoveryOnly"), "discovery population band").get("maximumInclusivePsa10Population") != 970:
        raise DataCleaningRuleError("POP <=970 must remain discovery only")

    mysql = _object(document.get("mysql"), "mysql")
    if mysql.get("rawPayloadStorage") != "private_pointer_only_after_ingest":
        raise DataCleaningRuleError("MySQL raw payload storage must be private pointer only after ingest")
    pointers = mysql.get("rawPayloadPointerTables")
    if pointers != ["market_raw_payload_object", "market_source_observation_payload_pointer", "market_source_effective_observation"]:
        raise DataCleaningRuleError("raw payload pointer tables must be explicit and complete")
    return document


def explain_rule(identifier: str, document: Mapping[str, Any] | None = None) -> dict[str, Any]:
    document = document or load_and_validate()
    aliases = {"data_cleaning": "all", "data-cleaning": "all"}
    identifier = aliases.get(identifier, identifier)
    if identifier == "all":
        return {"kind": "dataCleaningPolicy", "layers": document["layers"], "identity": document["identity"], "metricPolicies": document["metricPolicies"], "fieldPolicies": document["fieldPolicies"], "freshness": document["freshness"], "populationBands": document["populationBands"], "mysql": document["mysql"]}
    for layer in document["layers"]:
        if layer["id"] == identifier:
            return {"kind": "dataCleaningLayer", "value": layer}
    metric_aliases = {"psa10_population": "psa10Population", "psa10_reference_price": "psa10ReferencePrice", "tracked_sales": "trackedSales"}
    metric = metric_aliases.get(identifier, identifier)
    if metric in document["metricPolicies"]:
        return {"kind": "dataCleaningMetric", "value": document["metricPolicies"][metric]}
    raise DataCleaningRuleError(f"unknown data-cleaning identifier: {identifier}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or explain CARDZ data-cleaning rules")
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--explain")
    args = parser.parse_args()
    document = load_and_validate(args.rules.resolve(), args.schema.resolve())
    output: object = explain_rule(args.explain, document) if args.explain else {"status": "valid", "schemaVersion": document["schemaVersion"]}
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DataCleaningRuleError, OSError, json.JSONDecodeError) as error:
        raise SystemExit(str(error))
