#!/usr/bin/env python3
"""Normalize GemRate/SNK/eBay/TAG observations and derive private CARDZ rankings.

G10 supplies bootstrap evidence only. Exact GemRate observations own grader
population, while an immutable PSA 10 SNK run supplies exact price history for
directly mapped cards. TAG contributes an exact active-only fifth-grader
snapshot. No provider identifiers from this file are emitted publicly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from lang_registry import SUPPORTED_CARD_LANGUAGES

from g10_ingest import (
    canonical_json,
    iso_utc,
    iter_constituents,
    load_landing_replay,
    parse_effective_at,
    private_source_ref,
    read_json,
    sha256_bytes,
    storage_source,
)
from db_runtime import MONITORING_STATE_BANDS, active_universe_lock_hash


def _effective_population(card: Mapping[str, Any]) -> Any:
    """Prefer a fresh population observation over the universe lock value.

    The daily GemRate run can push a locked member below the POP 1000 bar
    after the lock was sealed. When that happens the universe card's own
    population is stale bootstrap evidence; the fresh observation decides
    eligibility, and the ranked derivation below already soft-skips any
    card that stays below the minimum.

    A member stays locked only while its fresh PSA 10 population is at
    least 971 (the pre-entry radar floor). Below that the card no longer
    qualifies for the complete-ranking universe at all, and leaving it in
    the lock would fail the daily run forever.
    """

    if not isinstance(card, Mapping):
        return None
    population = card.get("populationPsa10")
    band = MONITORING_STATE_BANDS.get(str(card.get("monitoringState") or ""))
    floor = band[0] if band else 1000
    if isinstance(population, int) and not isinstance(population, bool) and population >= floor:
        return population
    observations = card.get("populationObservations")
    if not isinstance(observations, list) or not observations:
        return population
    source_ref = str(card.get("canonicalSourceCode") or "")
    external_ref = str(card.get("canonicalExternalId") or "")
    fresh = [
        row
        for row in observations
        if isinstance(row, Mapping)
        and row.get("grader") == "PSA"
        and str(row.get("sourceCode") or "") == source_ref
        and str(row.get("externalId") or "") == external_ref
        and isinstance(row.get("topGradePopulation"), int)
        and not isinstance(row.get("topGradePopulation"), bool)
    ]
    if not fresh:
        return population
    observed = max(
        fresh,
        key=lambda row: (str(row.get("observedDate") or ""), str(row.get("effectiveAt") or "")),
    )
    value = observed["topGradePopulation"]
    return value if value >= 971 else population


def _population_band_floor(card: Mapping[str, Any]) -> int:
    band = MONITORING_STATE_BANDS.get(str(card.get("monitoringState") or ""))
    return band[0] if band else 1000


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "integrations" / "grade10" / "data"
DEFAULT_GEMRATE = ROOT / "data" / "private" / "gemrate" / "cards"
DEFAULT_LANDING = ROOT / "data" / "runtime" / "private-landing"
DEFAULT_FX = ROOT / "data" / "runtime" / "private-fx" / "latest.json"
DEFAULT_ACTIVE_UNIVERSE = ROOT / "data" / "runtime" / "private-source-map" / "tracked-universe.json"
TOP_GRADE = {
    "PSA": ("psa", "psa_10"),
    "BGS": ("beckett", "beckett_10_pristine"),
    "CGC": ("cgc", "cgc_10_perfect"),
    "SGC": ("sgc", "sgc_10_pristine"),
}
def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def deduped_rows(source_root: Path) -> dict[tuple[str, str], tuple[str, Mapping[str, Any]]]:
    """Read the broad discovery tree only while explicitly rebuilding a lock."""

    selected: dict[tuple[str, str], tuple[int, str, Mapping[str, Any]]] = {}
    priority = {"ptcg": 2, "ptcg100": 1, "opcg": 2}
    for market, row in iter_constituents(source_root):
        source_ref = private_source_ref(row)
        if source_ref is None:
            continue
        current = selected.get(source_ref)
        rank = priority.get(market, 0)
        if current is None or rank > current[0]:
            selected[source_ref] = (rank, market, row)
    return {key: (market, row) for key, (_, market, row) in selected.items()}


def collection_cards(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the deduplicated formal-ranking plus pre-entry collection union."""

    ranked = document.get("cards", [])
    monitoring = document.get("monitoringCandidates", [])
    if not isinstance(ranked, list) or not isinstance(monitoring, list):
        raise RuntimeError("active universe collection rows are invalid")
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for source in [*ranked, *monitoring]:
        if not isinstance(source, Mapping):
            raise RuntimeError("active universe collection card is invalid")
        row = dict(source)
        source_ref = (str(row.get("canonicalSourceCode") or ""), str(row.get("canonicalExternalId") or ""))
        if not all(source_ref):
            raise RuntimeError("active universe collection card has no canonical source identity")
        if source_ref in selected:
            raise RuntimeError("active universe collection rows are not deduplicated")
        selected[source_ref] = row
    return [selected[key] for key in sorted(selected)]


def load_active_universe(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"active universe does not exist: {path}")
    document = read_json(path)
    if not isinstance(document, dict) or document.get("schemaVersion") not in {"1.0.0", "2.0.0", "3.0.0", "4.0.0", "5.0.0"}:
        raise RuntimeError("active universe contract is invalid")
    cards = document.get("cards")
    policy = document.get("policy")
    if not isinstance(cards, list) or not cards or not isinstance(policy, Mapping):
        raise RuntimeError("active universe is empty or missing policy")
    if document.get("payloadSha256") != sha256_bytes(canonical_json(cards)):
        raise RuntimeError("active universe payload hash is invalid")
    if document.get("schemaVersion") in {"4.0.0", "5.0.0"}:
        if (
            policy.get("indexes") != ["tcg", "pokemon", "one-piece"]
            or policy.get("canonicalMembership") != "complete_eligible"
            or policy.get("languagePartitioning") is not False
            or "limitPerIndex" in policy
        ):
            raise RuntimeError("complete-ranking universe policy is invalid")
        identities: set[tuple[str, str]] = set()
        index_ranks: dict[str, list[int]] = {"tcg": [], "pokemon": [], "one-piece": []}
        for card in cards:
            if not isinstance(card, Mapping):
                raise RuntimeError("complete-ranking universe card is invalid")
            source_ref = (str(card.get("canonicalSourceCode") or ""), str(card.get("canonicalExternalId") or ""))
            memberships = card.get("rankMemberships")
            # Operator-expanded cards may await their first price before they
            # hold formal ranks; their membership stays empty until then.
            memberships_pending = memberships in (None, {})
            if (
                not all(source_ref)
                or source_ref in identities
                or card.get("pokedexStatus") != "confirmed"
                or not isinstance(memberships, Mapping)
                or (not memberships_pending and ("tcg" not in memberships or str(card.get("tcg") or "") not in memberships))
            ):
                raise RuntimeError("complete-ranking universe contains an invalid canonical member")
            if any(
                index not in index_ranks
                or not isinstance(rank, int)
                or isinstance(rank, bool)
                or rank < 1
                for index, rank in memberships.items()
            ):
                raise RuntimeError("complete-ranking universe contains an invalid rank membership")
            population = _effective_population(card)
            if not isinstance(population, int) or population < 1000:
                if not isinstance(population, int) or population < _population_band_floor(card):
                    raise RuntimeError("complete-ranking universe contains a card below POP 1000 (or its monitoring-band floor)")
            identities.add(source_ref)
            for index, rank in memberships.items():
                index_ranks[str(index)].append(int(rank))
        if any(sorted(ranks) != list(range(1, len(ranks) + 1)) for ranks in index_ranks.values()):
            raise RuntimeError("complete-ranking universe contains non-contiguous ranks")
        if document.get("schemaVersion") == "5.0.0":
            monitoring = document.get("monitoringCandidates")
            policy_range = policy.get("monitoringPopulationRangeInclusive")
            if (
                not isinstance(monitoring, list)
                or not isinstance(policy_range, Mapping)
                or policy_range.get("minimum") != 971
                or policy_range.get("maximum") != 999
                or document.get("monitoringPayloadSha256") != sha256_bytes(canonical_json(monitoring))
            ):
                raise RuntimeError("pre-entry monitoring contract is invalid")
            extra_ranges = policy.get("monitoringAdditionalBandsInclusive")
            if extra_ranges is not None and extra_ranges != {"buffer_850_999": {"minimum": 850, "maximum": 999}}:
                raise RuntimeError("additional monitoring population bands are invalid")
            monitoring_identities: set[tuple[str, str]] = set()
            for candidate in monitoring:
                if not isinstance(candidate, Mapping):
                    raise RuntimeError("pre-entry monitoring candidate is invalid")
                source_ref = (
                    str(candidate.get("canonicalSourceCode") or ""),
                    str(candidate.get("canonicalExternalId") or ""),
                )
                population = _effective_population(candidate)
                state = str(candidate.get("populationSourceState") or "").strip().lower()
                band = MONITORING_STATE_BANDS.get(str(candidate.get("monitoringState") or ""))
                if (
                    not all(source_ref)
                    or source_ref in identities
                    or source_ref in monitoring_identities
                    or candidate.get("pokedexStatus") != "confirmed"
                    or not str(candidate.get("gemrateId") or "").strip()
                    or band is None
                    or not isinstance(population, int)
                    or isinstance(population, bool)
                    or population < band[0]
                    or population > band[1]
                    or bool(candidate.get("populationEstimated"))
                    or state in {"estimate", "estimated", "unavailable", "stale"}
                    or candidate.get("collectionCadence") != "daily"
                ):
                    raise RuntimeError("pre-entry monitoring candidate is invalid")
                monitoring_identities.add(source_ref)
            # A schema-5 source batch must bind formal and pre-entry cards.
            # `payloadSha256` deliberately represents formal ranks only.
            active_universe_lock_hash(document)
        return document
    if document.get("schemaVersion") in {"2.0.0", "3.0.0"}:
        limit = policy.get("limitPerIndex")
        if (
            policy.get("indexes") != ["tcg", "pokemon", "one-piece"]
            or limit not in {300, 350}
            or policy.get("languagePartitioning") is not False
        ):
            raise RuntimeError("tracked universe policy is invalid")
        if document.get("schemaVersion") == "3.0.0" and (
            limit != 350
            or policy.get("publicLimitPerIndex") != 300
            or policy.get("reserveLimitPerIndex") != 50
        ):
            raise RuntimeError("tracked universe v3 policy is invalid")
        if limit == 350 and (
            policy.get("publicLimitPerIndex") != 300
            or policy.get("reserveLimitPerIndex") != 50
        ):
            raise RuntimeError("tracked universe public/reserve policy is invalid")
        identities: set[tuple[str, str]] = set()
        index_counts = Counter()
        for card in cards:
            if not isinstance(card, Mapping):
                raise RuntimeError("tracked universe card is invalid")
            source_ref = (str(card.get("canonicalSourceCode") or ""), str(card.get("canonicalExternalId") or ""))
            memberships = card.get("rankMemberships")
            if (
                not all(source_ref)
                or source_ref in identities
                or card.get("pokedexStatus") != "confirmed"
                or not isinstance(memberships, Mapping)
                or not memberships
            ):
                raise RuntimeError("tracked universe contains an invalid canonical member")
            if any(
                index not in {"tcg", "pokemon", "one-piece"}
                or not isinstance(rank, int)
                or rank < 1
                or rank > limit
                for index, rank in memberships.items()
            ):
                raise RuntimeError("tracked universe contains an invalid rank membership")
            if not isinstance(card.get("populationPsa10"), int) or int(card["populationPsa10"]) <= 100:
                raise RuntimeError("tracked universe contains a card at or below POP 100")
            identities.add(source_ref)
            index_counts.update(memberships.keys())
        if any(count > limit for count in index_counts.values()):
            raise RuntimeError("tracked universe exceeds an index limit")
        return document
    segment_limit = policy.get("segmentLimit")
    top_limit = policy.get("top100Limit")
    watch_limit = policy.get("watchlistLimit")
    if segment_limit != 300 or top_limit != 100 or watch_limit != 200:
        raise RuntimeError("active universe segment limit is invalid")
    segment_counts: dict[str, int] = {}
    role_counts: dict[str, dict[str, int]] = {}
    identities: set[tuple[str, str]] = set()
    for card in cards:
        if not isinstance(card, Mapping):
            raise RuntimeError("active universe card is invalid")
        source_ref = (str(card.get("canonicalSourceCode") or ""), str(card.get("canonicalExternalId") or ""))
        tcg = str(card.get("tcg") or "")
        language = str(card.get("language") or "")
        segment = str(card.get("segment") or "")
        role = str(card.get("role") or "")
        if not all(source_ref) or not segment or card.get("pokedexStatus") != "confirmed":
            raise RuntimeError("active universe contains a non-canonical card")
        if language not in SUPPORTED_CARD_LANGUAGES or segment != f"{tcg}:{language}":
            raise RuntimeError("active universe contains an invalid language segment")
        if role not in {"top100", "watchlist"}:
            raise RuntimeError("active universe contains an invalid member role")
        if not isinstance(card.get("populationPsa10"), int) or int(card["populationPsa10"]) <= 100:
            raise RuntimeError("active universe contains a card at or below POP 100")
        if source_ref in identities:
            raise RuntimeError("active universe contains a duplicate source identity")
        identities.add(source_ref)
        segment_counts[segment] = segment_counts.get(segment, 0) + 1
        counts = role_counts.setdefault(segment, {"top100": 0, "watchlist": 0})
        counts[role] += 1
    if any(value > segment_limit for value in segment_counts.values()) or any(
        counts["top100"] > top_limit or counts["watchlist"] > watch_limit
        for counts in role_counts.values()
    ):
        raise RuntimeError("active universe exceeds its per-segment limit")
    return document


def gemrate_populations(path: Path) -> tuple[date, dict[str, int]] | None:
    if not path.is_file():
        return None
    document = read_json(path)
    try:
        population = document["data"]["population"]["population_data"]
        observed = date.fromisoformat(str(population["data_last_updated"])[:10])
        by_grader = population["by_grader"]
    except (KeyError, TypeError, ValueError):
        return None
    values: dict[str, int] = {}
    for grader, (source_name, grade_key) in TOP_GRADE.items():
        source = by_grader.get(source_name)
        value = (source.get("grades") or {}).get(grade_key) if isinstance(source, Mapping) else None
        if isinstance(value, int) and value >= 0:
            values[grader] = value
    return (observed, values) if "PSA" in values else None


def gemrate_current_observations(card_root: Path) -> dict[str, tuple[date, int, str]]:
    """Resolve normalized GemRate facts while preserving the selected transport.

    The direct payload may carry all supported graders. ``current.json`` is the
    fail-closed result of direct API -> public card-details -> exact Grade10
    mirror and guarantees that keyless PSA population reaches canonical ingest.
    """

    selected: dict[str, tuple[date, int, str, int]] = {}
    direct = gemrate_populations(card_root / "population.json")
    if direct is not None:
        observed, values = direct
        for grader, value in values.items():
            selected[grader] = (observed, value, "direct_api", 3)

    current_path = card_root / "current.json"
    if current_path.is_file():
        current = read_json(current_path)
        try:
            observed = date.fromisoformat(str(current["effectiveDate"])[:10])
            value = int(current["populationPsa10"])
            transport = str(current["transport"])
        except (KeyError, TypeError, ValueError):
            observed = date.min
            value = -1
            transport = ""
        priorities = {
            "direct_api": 3,
            "gemrate_public_card_page": 2,
            "public_card_details": 2,
            "grade10_gemrate_mirror": 1,
        }
        priority = priorities.get(transport, 0)
        candidate = (observed, value, transport, priority)
        existing = selected.get("PSA")
        if value >= 0 and priority and (existing is None or (observed, priority) >= (existing[0], existing[3])):
            selected["PSA"] = candidate
        # The daily run's selected transport may carry per-grader top-grade
        # populations. Direct API rows above win per grader unless this
        # observation is newer or at least as authoritative.
        grader_populations = current.get("graderPopulations")
        if isinstance(grader_populations, Mapping) and priority:
            for grader, grader_value in grader_populations.items():
                code = str(grader).upper()
                if code not in TOP_GRADE or not isinstance(grader_value, int) or grader_value < 0:
                    continue
                grader_candidate = (observed, grader_value, transport, priority)
                grader_existing = selected.get(code)
                if grader_existing is None or (observed, priority) >= (grader_existing[0], grader_existing[3]):
                    selected[code] = grader_candidate

    return {grader: (observed, value, transport) for grader, (observed, value, transport, _) in selected.items()}


def load_snk_run(path: Path | None) -> dict[int, dict[str, Any]]:
    if path is None:
        return {}
    if not path.is_file():
        raise RuntimeError(f"SNK run does not exist: {path}")
    rows: dict[int, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError(f"invalid SNK JSONL at line {line_number}: {path}") from error
            if not isinstance(value, dict) or not isinstance(value.get("item_id"), int):
                raise RuntimeError(f"invalid SNK row at line {line_number}: {path}")
            if value.get("condition_filter") != "trading_card_single_psa10":
                raise RuntimeError(f"SNK row is not PSA 10 scoped at line {line_number}: {path}")
            if not isinstance(value.get("fetched_at"), str) or not value["fetched_at"].strip():
                raise RuntimeError(f"SNK row has no fetch timestamp at line {line_number}: {path}")
            if value["item_id"] in rows:
                raise RuntimeError(f"duplicate SNK item at line {line_number}: {path}")
            rows[value["item_id"]] = value
    return rows


def load_tag_run(path: Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    if path is None:
        return {}
    if not path.is_file():
        raise RuntimeError(f"TAG run does not exist: {path}")
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError(f"invalid TAG JSONL at line {line_number}: {path}") from error
            if not isinstance(value, dict) or value.get("schemaVersion") != "1.0.0":
                raise RuntimeError(f"invalid TAG row at line {line_number}: {path}")
            source_ref = (
                str(value.get("canonicalSourceCode") or "").casefold(),
                str(value.get("canonicalExternalId") or ""),
            )
            try:
                observed = date.fromisoformat(str(value.get("observedDate") or ""))
            except ValueError as error:
                raise RuntimeError(f"invalid TAG observed date at line {line_number}: {path}") from error
            top = value.get("topGradePopulation")
            total = value.get("total")
            if (
                not all(source_ref)
                or not isinstance(top, int)
                or top < 0
                or not isinstance(total, int)
                or total < top
                or not isinstance(value.get("tagIdentitySha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", value["tagIdentitySha256"])
            ):
                raise RuntimeError(f"invalid TAG population row at line {line_number}: {path}")
            if source_ref in rows:
                raise RuntimeError(f"duplicate TAG canonical identity at line {line_number}: {path}")
            rows[source_ref] = {**value, "observedDate": observed}
    if not rows:
        raise RuntimeError(f"TAG run contains no exact active observations: {path}")
    return rows


def load_ebay_run(path: Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    if path is None:
        return {}
    if not path.is_file():
        raise RuntimeError(f"eBay run does not exist: {path}")
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError(f"invalid eBay JSONL at line {line_number}: {path}") from error
            source_ref = (
                str(value.get("sourceCode") or ""),
                str(value.get("externalEntityId") or ""),
            ) if isinstance(value, Mapping) else ("", "")
            if not all(source_ref) or value.get("schemaVersion") != "1.0.0" or source_ref in rows:
                raise RuntimeError(f"invalid or duplicate eBay row at line {line_number}: {path}")
            rows[source_ref] = dict(value)
    return rows


def fx_jpy_per_usd(path: Path) -> tuple[float, str]:
    document = read_json(path)
    try:
        value = float(document["rates"]["JPY"]["value"])
        effective_at = str(document["rates"]["JPY"]["effectiveAt"])
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError(f"valid USD/JPY rate is missing: {path}") from error
    if value <= 0:
        raise RuntimeError(f"valid USD/JPY rate is missing: {path}")
    return value, effective_at


def observation(
    source_ref: tuple[str, str],
    kind: str,
    observed: date,
    payload: Mapping[str, Any],
    provider: str,
    priority: int,
) -> dict[str, Any]:
    source_code, external_id = source_ref
    key = f"{source_code}|{external_id}|{kind}|{observed.isoformat()}|{provider}".encode()
    return {
        "observationKey": hashlib.sha256(key).hexdigest(),
        "sourceCode": source_code,
        "externalEntityId": external_id,
        "observationKind": kind,
        "observedDate": observed.isoformat(),
        "providerCode": provider,
        "sourcePriority": priority,
        "payloadHash": sha256_bytes(canonical_json(payload)),
        "payload": dict(payload),
    }


def snk_identity_is_exact(card: Mapping[str, Any], snk: Mapping[str, Any]) -> bool:
    """Validate an explicit upstream identity attestation when it is present.

    Existing frozen mappings remain the identity authority until the collector
    emits the optional ``identity`` block.  A supplied block must be complete
    and match the canonical collector number, language, and parallel exactly;
    otherwise this source generation must not be promoted.
    """
    identity = snk.get("identity")
    if identity is None:
        return True
    if not isinstance(identity, Mapping) or identity.get("matchStatus") != "exact":
        return False
    expected = {
        "collectorNumber": str(card.get("collectorNumber") or card.get("collectorNumberRaw") or "").strip(),
        "language": str(card.get("language") or "").strip().casefold(),
        "parallel": str(card.get("parallel") or "").strip().casefold(),
    }
    actual = {
        "collectorNumber": str(identity.get("collectorNumber") or "").strip(),
        "language": str(identity.get("language") or "").strip().casefold(),
        "parallel": str(identity.get("parallel") or "").strip().casefold(),
    }
    return all(expected[key] and actual[key] == expected[key] for key in expected)


def build_source_observations(
    crosswalk: Mapping[str, Any],
    gemrate_root: Path,
    snk_rows: Mapping[int, Mapping[str, Any]],
    tag_rows: Mapping[tuple[str, str], Mapping[str, Any]],
    ebay_rows: Mapping[tuple[str, str], Mapping[str, Any]],
    jpy_per_usd: float,
    effective_at: datetime,
    history_days: int | None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    observations: list[dict[str, Any]] = []
    counts = {
        "gemrateCards": 0,
        "gemrateObservations": 0,
        "snkCards": 0,
        "snkPriceObservations": 0,
        "snkSalesObservations": 0,
        "snkMissing": 0,
        "tagCards": 0,
        "tagObservations": 0,
        "tagMissing": 0,
        "ebayCards": 0,
        "ebaySalesObservations": 0,
        "ebayUnavailable": 0,
    }
    floor = effective_at.date() - timedelta(days=history_days) if history_days is not None else date.min
    for card in collection_cards(crosswalk):
        source_ref = (str(card.get("canonicalSourceCode") or ""), str(card.get("canonicalExternalId") or ""))
        if not all(source_ref):
            continue
        gid = card.get("gemrateId")
        gemrate = gemrate_current_observations(gemrate_root / str(gid)) if gid else {}
        if gemrate:
            counts["gemrateCards"] += 1
            for grader, (observed, value, transport) in gemrate.items():
                if observed > effective_at.date():
                    raise RuntimeError("GemRate observation date is later than the market generation")
                payload = {
                    "grader": grader,
                    "topGradePopulation": value,
                    "authority": "gemrate",
                    "transport": transport,
                }
                observations.append(
                    observation(source_ref, f"grader_population_{grader.casefold()}", observed, payload, "gemrate", 200)
                )
                counts["gemrateObservations"] += 1

        if card.get("tcg") == "pokemon":
            tag = tag_rows.get(source_ref)
            if tag is None:
                counts["tagMissing"] += 1
            else:
                observed = tag["observedDate"]
                if not isinstance(observed, date) or observed > effective_at.date():
                    raise RuntimeError("TAG observation date is later than the market generation")
                payload = {
                    "grader": "TAG",
                    "topGradePopulation": int(tag["topGradePopulation"]),
                    "total": int(tag["total"]),
                }
                observations.append(
                    observation(source_ref, "grader_population_tag", observed, payload, "tag", 150)
                )
                counts["tagCards"] += 1
                counts["tagObservations"] += 1

        ebay = ebay_rows.get(source_ref)
        if ebay is not None:
            daily_ebay: dict[date, dict[str, float | int]] = {}
            for transaction in ebay.get("transactions", []):
                if not isinstance(transaction, Mapping):
                    continue
                try:
                    observed = date.fromisoformat(str(transaction.get("soldDate") or ""))
                    price = float(transaction.get("unitPrice"))
                    quantity = int(transaction.get("quantity"))
                except (TypeError, ValueError):
                    continue
                currency = str(transaction.get("currency") or "").upper()
                if observed < floor or observed > effective_at.date() or price <= 0 or quantity != 1:
                    continue
                value_usd = price if currency == "USD" else price / jpy_per_usd if currency == "JPY" else 0
                if value_usd <= 0:
                    continue
                metric = daily_ebay.setdefault(observed, {"count": 0, "value_usd": 0.0})
                metric["count"] = int(metric["count"]) + 1
                metric["value_usd"] = float(metric["value_usd"]) + value_usd
            if daily_ebay:
                counts["ebayCards"] += 1
                for observed, metric in sorted(daily_ebay.items()):
                    payload = {
                        "salesCount": int(metric["count"]),
                        "salesValueUsd": round(float(metric["value_usd"]), 6),
                        "grade": "PSA 10",
                        "coverage": "partial",
                    }
                    observations.append(
                        observation(source_ref, "tracked_sales_daily", observed, payload, "ebay_psa10", 150)
                    )
                    counts["ebaySalesObservations"] += 1
            else:
                counts["ebayUnavailable"] += 1

        snk_id = card.get("snkItemId")
        if not isinstance(snk_id, int):
            continue
        snk = snk_rows.get(snk_id)
        if snk is None:
            counts["snkMissing"] += 1
            if snk_rows:
                raise RuntimeError(f"SNK exact mapped item is missing from the completed run: {snk_id}")
            continue
        if not snk_identity_is_exact(card, snk):
            raise RuntimeError(f"SNK identity mismatch for exact mapped item: {snk_id}")
        counts["snkCards"] += 1
        fetched_at = str(snk["fetched_at"])
        daily: dict[date, float] = {}
        for point in snk.get("kline", []):
            if not isinstance(point, Mapping):
                continue
            try:
                observed = date.fromisoformat(str(point.get("date")))
                price_jpy = float(point.get("price_jpy"))
            except (TypeError, ValueError):
                continue
            if observed < floor or observed > effective_at.date() or price_jpy <= 0:
                continue
            daily[observed] = price_jpy
        for observed, price_jpy in sorted(daily.items()):
            payload = {
                "priceUsd": round(price_jpy / jpy_per_usd, 6),
                "priceJpy": price_jpy,
                "nativeCurrency": "JPY",
                "nativeValue": price_jpy,
                "grade": "PSA 10",
                "referenceMethod": "snk_daily_history",
                "fetchedAt": fetched_at,
                "fxJpyPerUsd": jpy_per_usd,
            }
            observations.append(observation(source_ref, "index_constituent", observed, payload, "snk_psa10", 200))
            counts["snkPriceObservations"] += 1

        activity = snk.get("daily_activity")
        if isinstance(activity, Mapping):
            for raw_day, raw_metric in sorted(activity.items()):
                if not isinstance(raw_metric, Mapping):
                    continue
                try:
                    observed = date.fromisoformat(str(raw_day))
                except ValueError:
                    continue
                count = raw_metric.get("count")
                value_jpy = raw_metric.get("value_jpy")
                if (
                    observed < floor
                    or observed > effective_at.date()
                    or not isinstance(count, int)
                    or count < 0
                    or not isinstance(value_jpy, (int, float))
                    or value_jpy < 0
                ):
                    continue
                payload = {
                    "salesCount": count,
                    "salesValueJpy": round(float(value_jpy), 6),
                    "salesValueUsd": round(float(value_jpy) / jpy_per_usd, 6),
                    "nativeCurrency": "JPY",
                    "nativeValue": round(float(value_jpy), 6),
                    "grade": "PSA 10",
                    "coverage": "partial",
                    "fetchedAt": fetched_at,
                    "fxJpyPerUsd": jpy_per_usd,
                }
                observations.append(
                    observation(source_ref, "tracked_sales_daily", observed, payload, "snk_psa10", 200)
                )
                counts["snkSalesObservations"] += 1

    observations.sort(
        key=lambda value: (
            value["observedDate"],
            value["sourceCode"],
            value["externalEntityId"],
            value["observationKind"],
            value["providerCode"],
        )
    )
    return observations, counts


def g10_psa_population(source_root: Path, source_ref: tuple[str, str]) -> int | None:
    """Read a bootstrap population only while explicitly rebuilding a lock."""

    source_code, external_id = source_ref
    path = source_root / "cards" / storage_source(source_code) / external_id / "populations.json"
    if not path.is_file():
        return None
    document = read_json(path)
    rows = document.get("population") if isinstance(document, Mapping) else None
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, Mapping) and str(row.get("gradeName") or "").upper() == "PSA":
            value = row.get("topGrade")
            return value if isinstance(value, int) and value >= 0 else None
    return None


def latest(values: Mapping[date, Any] | None) -> tuple[date, Any] | None:
    if not values:
        return None
    observed = max(values)
    return observed, values[observed]


def derive_rankings(
    landing_root: Path,
    effective_at: datetime,
    active_universe: Mapping[str, Any],
    price_fresh_hours: float = 48,
) -> dict[str, Any]:
    replay = load_landing_replay(landing_root)
    eligible: list[dict[str, Any]] = []
    coverage = {
        "dailyReplayPrice": 0,
        "activeLockPriceFallback": 0,
        "dailyReplayPopulation": 0,
        "activeLockPopulationFallback": 0,
    }
    collection = collection_cards(active_universe)
    formal_source_refs = {
        (
            str(card.get("canonicalSourceCode") or ""),
            str(card.get("canonicalExternalId") or ""),
        )
        for card in active_universe.get("cards", [])
        if isinstance(card, Mapping)
    }
    for universe_card in collection:
        source_ref = (
            str(universe_card.get("canonicalSourceCode") or ""),
            str(universe_card.get("canonicalExternalId") or ""),
        )
        if not all(source_ref):
            continue
        price_anchor = latest(replay.prices.get(source_ref))
        if price_anchor is not None:
            price_day, price = price_anchor
            price_at = datetime.combine(price_day, time.min, tzinfo=timezone.utc)
            coverage["dailyReplayPrice"] += 1
        else:
            value = universe_card.get("priceUsd")
            if not isinstance(value, (int, float)) or value <= 0:
                continue
            price_day_raw = universe_card.get("priceAsOf")
            if not isinstance(price_day_raw, str):
                continue
            try:
                price_day = date.fromisoformat(price_day_raw)
            except ValueError:
                continue
            price, price_at = float(value), datetime.combine(price_day, time.min, tzinfo=timezone.utc)
            coverage["activeLockPriceFallback"] += 1
        if effective_at - price_at > timedelta(hours=price_fresh_hours):
            continue

        pop_anchor = latest(replay.grader_populations.get((*source_ref, "PSA")))
        if pop_anchor is not None:
            pop_day, population = pop_anchor
            coverage["dailyReplayPopulation"] += 1
        else:
            population = universe_card.get("populationPsa10")
            population_day_raw = universe_card.get("populationAsOf")
            try:
                pop_day = date.fromisoformat(str(population_day_raw)) if population_day_raw else effective_at.date()
            except ValueError:
                pop_day = effective_at.date()
            coverage["activeLockPopulationFallback"] += 1
        if not isinstance(population, int) or population < 1000:
            if source_ref in formal_source_refs or population < _population_band_floor(universe_card):
                continue
        if source_ref not in formal_source_refs:
            continue
        eligible.append(
            {
                "sourceCode": source_ref[0],
                "externalId": source_ref[1],
                "pokedexId": universe_card["pokedexId"],
                "tcg": str(universe_card["tcg"]),
                "language": universe_card["language"],
                "segment": str(universe_card.get("segment") or universe_card["tcg"]),
                "name": str(universe_card.get("name") or ""),
                "collectorNumber": universe_card["collectorNumber"],
                "priceUsd": round(float(price), 6),
                "priceAsOf": price_at.date().isoformat(),
                "populationPsa10": population,
                "populationAsOf": pop_day.isoformat(),
                "marketCapUsd": round(float(price) * population, 2),
            }
        )

    eligible.sort(key=lambda value: (-value["marketCapUsd"], value["sourceCode"], value["externalId"]))

    def ranked(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{**row, "rank": rank} for rank, row in enumerate(list(rows), start=1)]

    pokemon = [row for row in eligible if row["tcg"] == "pokemon"]
    one_piece = [row for row in eligible if row["tcg"] == "one-piece"]
    by_segment: dict[str, list[dict[str, Any]]] = {}
    for row in eligible:
        by_segment.setdefault(str(row["segment"]), []).append(row)
    return {
        "schemaVersion": "2.0.0",
        "effectiveAt": iso_utc(effective_at),
        "formula": "PSA 10 reference price USD x exact PSA 10 population",
        "populationMinimum": 1000,
        "coverage": coverage,
        "monitoring": {
            "collectionCandidates": len(collection) - len(formal_source_refs),
            "formalRankedCards": len(formal_source_refs),
        },
        "indexes": {
            "combined": {"eligible": len(eligible), "rows": ranked(eligible)},
            "pokemon": {"eligible": len(pokemon), "rows": ranked(pokemon)},
            "onePiece": {"eligible": len(one_piece), "rows": ranked(one_piece)},
            "segments": {
                segment: {"eligible": len(rows), "rows": ranked(rows)}
                for segment, rows in sorted(by_segment.items())
            },
        },
        "presentationViews": {
            view: {
                "requiredPerIndex": limit,
                "ready": {
                    "combined": len(eligible) >= limit,
                    "pokemon": len(pokemon) >= limit,
                    "onePiece": len(one_piece) >= limit,
                },
            }
            for view, limit in {
                "top100": 100,
                "top300": 300,
                "top350": 350,
                "top100_plus_200": 300,
                "reserve50": 350,
            }.items()
        },
    }


def self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fx = root / "fx.json"
        atomic_write_json(fx, {"rates": {"JPY": {"value": 160, "effectiveAt": "2026-07-23T00:00:00Z"}}})
        value, effective = fx_jpy_per_usd(fx)
        source_ref = ("snkrdunk", "1")
        sample_crosswalk = {"cards": [{"canonicalSourceCode": source_ref[0], "canonicalExternalId": source_ref[1], "gemrateId": None, "snkItemId": 1, "tcg": "pokemon"}]}
        sample_snk = {
            1: {
                "kline": [{"date": "2026-07-22", "price_jpy": 16000}],
                "condition_filter": "trading_card_single_psa10",
                "fetched_at": "2026-07-23T00:00:00Z",
            }
        }
        sample_tag = {
            source_ref: {
                "observedDate": date(2026, 7, 23),
                "topGradePopulation": 12,
                "total": 21,
            }
        }
        observations, counts = build_source_observations(
            sample_crosswalk, root / "gemrate", sample_snk, sample_tag, {}, value,
            datetime(2026, 7, 23, tzinfo=timezone.utc), 45,
        )
        tag = next(row for row in observations if row["observationKind"] == "grader_population_tag")
        price = next(row for row in observations if row["observationKind"] == "index_constituent")
        return {
            "fx": value,
            "fxEffectiveAt": effective,
            "priceUsd": price["payload"]["priceUsd"],
            "priority": price["sourcePriority"],
            "tagTopGradePopulation": tag["payload"]["topGradePopulation"],
            "tagTotal": tag["payload"]["total"],
            "tagPriority": tag["sourcePriority"],
            "counts": counts,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize exact GemRate/SNK source observations")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE, help=argparse.SUPPRESS)
    parser.add_argument("--crosswalk", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--active-universe", type=Path, default=DEFAULT_ACTIVE_UNIVERSE)
    parser.add_argument("--gemrate-root", type=Path, default=DEFAULT_GEMRATE)
    parser.add_argument("--snk-run", type=Path)
    parser.add_argument("--tag-run", type=Path)
    parser.add_argument("--ebay-run", type=Path)
    parser.add_argument("--fx-snapshot", type=Path, default=DEFAULT_FX)
    parser.add_argument("--landing-root", type=Path, default=DEFAULT_LANDING)
    parser.add_argument("--run-id")
    parser.add_argument("--effective-at")
    parser.add_argument("--history-days", type=int, default=45)
    parser.add_argument(
        "--full-history",
        action="store_true",
        help="bootstrap every available daily SNK price point for the frozen active universe",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), sort_keys=True))
        return 0

    effective_at = parse_effective_at(args.effective_at) if args.effective_at else datetime.now(timezone.utc)
    run_id = args.run_id or effective_at.strftime("sources_%Y%m%d")
    run_root = args.landing_root.resolve() / "sources" / run_id
    batch_path = run_root / "canonical-batch.json"
    rankings_path = run_root / "rankings.json"
    manifest_path = run_root / "manifest.json"
    if batch_path.is_file() and rankings_path.is_file() and manifest_path.is_file():
        manifest = read_json(manifest_path)
        print(json.dumps({"status": "replayed", "runId": run_id, **manifest.get("counts", {})}, sort_keys=True))
        return 0
    if any(path.exists() for path in (batch_path, rankings_path, manifest_path)):
        raise RuntimeError(f"incomplete immutable source generation exists: {run_root}")

    active_universe = load_active_universe(args.active_universe.resolve())
    snk_rows = load_snk_run(args.snk_run.resolve() if args.snk_run else None)
    tag_rows = load_tag_run(args.tag_run.resolve() if args.tag_run else None)
    ebay_rows = load_ebay_run(args.ebay_run.resolve() if args.ebay_run else None)
    jpy_per_usd, fx_effective_at = fx_jpy_per_usd(args.fx_snapshot.resolve())
    observations, counts = build_source_observations(
        active_universe,
        args.gemrate_root.resolve(),
        snk_rows,
        tag_rows,
        ebay_rows,
        jpy_per_usd,
        effective_at,
        None if args.full_history else args.history_days,
    )
    batch = {
        "schemaVersion": "2.0.0",
        "runId": run_id,
        "mode": "incremental",
        "effectiveAt": iso_utc(effective_at),
        "fetchedAt": iso_utc(effective_at),
        "payloadSha256": sha256_bytes(canonical_json(observations)),
        "observations": observations,
        "rejected": [],
    }

    # Ranking is calculated from a temporary complete view so a failed derive
    # cannot leave a half-published immutable generation behind.
    run_root.mkdir(parents=True, exist_ok=True)
    batch_tmp = run_root / ".canonical-batch.pending.json"
    atomic_write_json(batch_tmp, batch)
    os.replace(batch_tmp, batch_path)
    try:
        rankings = derive_rankings(args.landing_root.resolve(), effective_at, active_universe)
        manifest = {
            "schemaVersion": "1.0.0",
            "runId": run_id,
            "effectiveAt": iso_utc(effective_at),
            "fxEffectiveAt": fx_effective_at,
            "activeUniverseSha256": active_universe_lock_hash(active_universe),
            "batchSha256": batch["payloadSha256"],
            "counts": {
                **counts,
                "activeCards": active_universe["counts"].get(
                    "active", active_universe["counts"].get("uniqueCards", 0)
                ),
                "combinedEligible": rankings["indexes"]["combined"]["eligible"],
                "pokemonEligible": rankings["indexes"]["pokemon"]["eligible"],
                "onePieceEligible": rankings["indexes"]["onePiece"]["eligible"],
            },
        }
        atomic_write_json(rankings_path, rankings)
        atomic_write_json(manifest_path, manifest)
    except Exception:
        batch_path.unlink(missing_ok=True)
        raise
    print(json.dumps({"status": "ready", "runId": run_id, **manifest["counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
