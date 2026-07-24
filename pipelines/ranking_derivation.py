#!/usr/bin/env python3
"""Deterministically derive CARDZ PSA 10 tracked market rankings."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


INDEXES = ("tcg", "pokemon", "one-piece")
# Ranking membership is complete for every eligible printing.  These ranges
# are presentation cuts only; they must never cap canonical observations.
TRACKED_INDEX_LIMIT = 350  # Backwards-compatible UI/default presentation cut.
PUBLIC_INDEX_LIMIT = 300
RESERVE_INDEX_LIMIT = TRACKED_INDEX_LIMIT - PUBLIC_INDEX_LIMIT
PRESENTATION_VIEW_RANGES: dict[str, tuple[int, int]] = {
    "top100": (1, 100),
    "top300": (1, 300),
    "top350": (1, 350),
    "reserve50": (301, 350),
}
PRESENTATION_VIEW_ALIASES = {"top100_plus_200": "top300"}
PRESENTATION_VIEWS = tuple((*PRESENTATION_VIEW_RANGES, *PRESENTATION_VIEW_ALIASES))
POPULATION_MINIMUM = 1_000
MONITORING_POPULATION_MINIMUM = 971
MONITORING_POPULATION_MAXIMUM = POPULATION_MINIMUM - 1
PRICE_FRESHNESS_HOURS = 48
PROJECTED_RANK300_PROXIMITY = 0.80
POPULATION_ESTIMATE_STATES = {"estimate", "estimated", "unavailable", "stale"}
PRICE_UNAVAILABLE_STATES = {"estimate", "estimated", "unavailable"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def identity_key(row: Mapping[str, Any]) -> str:
    explicit = str(row.get("canonicalPrintingKey") or "").strip()
    if explicit:
        return explicit
    return str(row.get("pokedexId") or "").strip()


def identity_is_complete(row: Mapping[str, Any]) -> bool:
    return (
        str(row.get("pokedexStatus") or "").strip() == "confirmed"
        and str(row.get("tcg") or "").strip() in {"pokemon", "one-piece"}
        and bool(identity_key(row))
        and bool(str(row.get("canonicalSourceCode") or "").strip())
        and bool(str(row.get("canonicalExternalId") or "").strip())
        and bool(str(row.get("collectorNumber") or "").strip())
        and bool(str(row.get("language") or "").strip())
    )


def population_is_valid(row: Mapping[str, Any]) -> bool:
    population = row.get("populationPsa10")
    state = str(row.get("populationSourceState") or "").strip().lower()
    return (
        isinstance(population, int)
        and not isinstance(population, bool)
        and population >= POPULATION_MINIMUM
        and not bool(row.get("populationEstimated"))
        and state not in POPULATION_ESTIMATE_STATES
        and bool(str(row.get("gemrateId") or "").strip())
    )


def price_is_fresh(row: Mapping[str, Any], effective_at: datetime) -> bool:
    price = row.get("priceUsd")
    state = str(row.get("priceSourceState") or "").strip().lower()
    observed_at = parse_time(row.get("priceAsOf"))
    if not isinstance(price, (int, float)) or isinstance(price, bool) or price <= 0:
        return False
    if state in PRICE_UNAVAILABLE_STATES or observed_at is None:
        return False
    if effective_at.tzinfo is None:
        effective_at = effective_at.replace(tzinfo=timezone.utc)
    age_seconds = (effective_at.astimezone(timezone.utc) - observed_at).total_seconds()
    return 0 <= age_seconds <= PRICE_FRESHNESS_HOURS * 3600


def rejection_reason(row: Mapping[str, Any], effective_at: datetime) -> str | None:
    if not identity_is_complete(row):
        return "identity_unconfirmed_or_incomplete"
    if not population_is_valid(row):
        return "population_not_gemrate_confirmed_psa10_1000"
    if not price_is_fresh(row, effective_at):
        return "reference_price_not_fresh"
    return None


def choose_canonical(rows: Iterable[Mapping[str, Any]], effective_at: datetime) -> dict[str, Any]:
    """Resolve duplicate copies of one already-exact printing deterministically."""

    ordered = sorted(
        (dict(row) for row in rows),
        key=lambda row: (
            -float(row["priceUsd"]) * int(row["populationPsa10"]),
            -parse_time(row.get("priceAsOf")).timestamp(),
            str(row.get("canonicalSourceCode") or ""),
            str(row.get("canonicalExternalId") or ""),
        ),
    )
    selected = ordered[0]
    selected["marketCapUsd"] = round(float(selected["priceUsd"]) * int(selected["populationPsa10"]), 2)
    return selected


def prior_memberships(previous: Mapping[str, Any] | None) -> dict[str, dict[str, int]]:
    if not isinstance(previous, Mapping):
        return {}
    cards = previous.get("cards")
    if not isinstance(cards, list):
        return {}
    history: dict[str, dict[str, int]] = {}
    for row in cards:
        if not isinstance(row, Mapping):
            continue
        key = identity_key(row)
        memberships = row.get("rankMemberships")
        if key and isinstance(memberships, Mapping):
            history[key] = {
                str(index): int(rank)
                for index, rank in memberships.items()
                if str(index) in INDEXES and isinstance(rank, int)
            }
    return history


def resolve_presentation_view(name: str) -> str:
    """Return the concrete rank-range view behind a public view name."""

    normalized = str(name).strip()
    concrete = PRESENTATION_VIEW_ALIASES.get(normalized, normalized)
    if concrete not in PRESENTATION_VIEW_RANGES:
        choices = ", ".join(PRESENTATION_VIEWS)
        raise ValueError(f"unknown presentation view {normalized!r}; expected one of {choices}")
    return concrete


def presentation_views(rankings: Mapping[str, list[Mapping[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Expose ordered rank references without copying canonical card facts."""

    views: dict[str, dict[str, Any]] = {}
    for name, (start_rank, end_rank) in PRESENTATION_VIEW_RANGES.items():
        views[name] = {
            "range": {"startRank": start_rank, "endRank": end_rank},
            "indexes": {
                index: [dict(row) for row in rankings[index][start_rank - 1:end_rank]]
                for index in INDEXES
            },
        }
    for name, target in PRESENTATION_VIEW_ALIASES.items():
        views[name] = {"aliasOf": target}
    return views


def publication_validation(
    rankings: Mapping[str, list[Mapping[str, Any]]],
    required_views: Iterable[str] | None,
) -> dict[str, Any]:
    """Validate only requested presentation views, never canonical ingestion."""

    requested = tuple(dict.fromkeys(str(name).strip() for name in (required_views or ()) if str(name).strip()))
    blockers: list[dict[str, Any]] = []
    views: dict[str, dict[str, Any]] = {}
    for name in PRESENTATION_VIEWS:
        concrete = resolve_presentation_view(name)
        _, end_rank = PRESENTATION_VIEW_RANGES[concrete]
        counts = {index: len(rankings[index]) for index in INDEXES}
        view_blockers = [
            {"view": name, "index": index, "required": end_rank, "actual": counts[index]}
            for index in INDEXES
            if counts[index] < end_rank
        ]
        views[name] = {
            "status": "ready" if not view_blockers else "blocked",
            "requiredPerIndex": end_rank,
            "actualPerIndex": counts,
            "blockers": view_blockers,
        }
    for name in requested:
        concrete = resolve_presentation_view(name)
        if concrete not in PRESENTATION_VIEW_RANGES:  # Defensive type narrowing for future aliases.
            raise ValueError(f"unknown presentation view {name!r}")
        blockers.extend(views[name]["blockers"])
    return {
        "status": "ready" if not blockers else "blocked",
        "requiredViews": list(requested),
        "blockers": blockers,
        "views": views,
    }


def population_is_monitorable(row: Mapping[str, Any]) -> bool:
    """Return whether an exact GemRate PSA 10 population is in the pre-entry band."""

    population = row.get("populationPsa10")
    state = str(row.get("populationSourceState") or "").strip().lower()
    return (
        isinstance(population, int)
        and not isinstance(population, bool)
        and MONITORING_POPULATION_MINIMUM <= population <= MONITORING_POPULATION_MAXIMUM
        and not bool(row.get("populationEstimated"))
        and state not in POPULATION_ESTIMATE_STATES
        and bool(str(row.get("gemrateId") or "").strip())
    )


def choose_monitoring_candidate(rows: Iterable[Mapping[str, Any]], effective_at: datetime) -> dict[str, Any]:
    """Choose one exact pre-entry observation without inventing a price."""

    def priority(row: Mapping[str, Any]) -> tuple[int, float, float, str, str]:
        observed = parse_time(row.get("priceAsOf"))
        return (
            1 if price_is_fresh(row, effective_at) else 0,
            float(row.get("priceUsd") or 0),
            observed.timestamp() if observed else 0.0,
            str(row.get("canonicalSourceCode") or ""),
            str(row.get("canonicalExternalId") or ""),
        )

    return dict(sorted((dict(row) for row in rows), key=priority, reverse=True)[0])


def rank300_cutoff(rankings: Mapping[str, list[Mapping[str, Any]]], tcg: str) -> dict[str, Any] | None:
    """Return the same-market formal cutoff used for pre-entry projection."""

    ranked = rankings.get(tcg)
    if not ranked:
        return None
    cutoff_rank = min(300, len(ranked))
    cutoff = ranked[cutoff_rank - 1]
    return {
        "marketCapUsd": float(cutoff["marketCapUsd"]),
        "rank": cutoff_rank,
        "basis": "rank300" if len(ranked) >= 300 else "lowest_formal_rank_available",
    }


def monitoring_candidates(
    rows: Iterable[Mapping[str, Any]],
    rankings: Mapping[str, list[Mapping[str, Any]]],
    effective_at: datetime,
) -> list[dict[str, Any]]:
    """Build the daily pre-entry pool without allowing it into formal ranks.

    Only exact GemRate PSA 10 populations 971--999 are eligible.  POP <=970
    stays discovery-only even when a card is new, accelerating, or expensive.
    """

    formal_keys = {identity_key(row) for ranked in rankings.values() for row in ranked}
    groups: dict[str, list[dict[str, Any]]] = {}
    for source in rows:
        row = dict(source)
        key = identity_key(row)
        if not key or key in formal_keys or not identity_is_complete(row) or not population_is_monitorable(row):
            continue
        groups.setdefault(key, []).append(row)

    candidates: list[dict[str, Any]] = []
    for key, rows_for_identity in sorted(groups.items()):
        row = choose_monitoring_candidate(rows_for_identity, effective_at)
        price_ready = price_is_fresh(row, effective_at)
        price = float(row["priceUsd"]) if price_ready else None
        projected = round(price * POPULATION_MINIMUM, 2) if price is not None else None
        cutoff = rank300_cutoff(rankings, str(row["tcg"]))
        ratio = round(projected / cutoff["marketCapUsd"], 6) if projected is not None and cutoff else None
        reasons = ["population_971_999"]
        if bool(row.get("isNew")):
            reasons.append("new_printing")
        if bool(row.get("populationAccelerating")):
            reasons.append("population_accelerating")
        if bool(row.get("priceAccelerating")):
            reasons.append("price_accelerating")
        if ratio is not None and ratio >= PROJECTED_RANK300_PROXIMITY:
            reasons.append("projected_rank300_proximity")
        candidates.append(
            {
                "identityKey": key,
                "pokedexId": row.get("pokedexId"),
                "pokedexStatus": row.get("pokedexStatus"),
                "canonicalSourceCode": row.get("canonicalSourceCode"),
                "canonicalExternalId": row.get("canonicalExternalId"),
                "gemrateId": row.get("gemrateId"),
                "snkItemId": row.get("snkItemId"),
                "tcg": row.get("tcg"),
                "language": row.get("language"),
                "name": row.get("name"),
                "setName": row.get("setName"),
                "collectorNumber": row.get("collectorNumber"),
                "edition": row.get("edition"),
                "parallel": row.get("parallel"),
                "finish": row.get("finish"),
                "populationPsa10": row["populationPsa10"],
                "populationSourceState": row.get("populationSourceState"),
                "populationEstimated": bool(row.get("populationEstimated")),
                "monitoringState": "pre_entry_population_971_999",
                "collectionCadence": "daily",
                "reasons": reasons,
                "priceStatus": "ready" if price_ready else "needs_snk_current_price",
                "currentPriceUsd": price,
                "projectedMarketCapAtPop1000Usd": projected,
                "rank300Cutoff": cutoff,
                "projectedCutoffRatio": ratio,
            }
        )
    candidates.sort(
        key=lambda row: (
            -int(row["populationPsa10"]),
            -(float(row["projectedCutoffRatio"]) if row["projectedCutoffRatio"] is not None else -1.0),
            str(row["identityKey"]),
        )
    )
    return candidates


def historical_backfill_queue(
    cards: Iterable[Mapping[str, Any]],
    previous_members: Mapping[str, Mapping[str, int]],
) -> list[dict[str, Any]]:
    """Queue incomplete history for the de-duplicated union of rank 1-350 targets."""

    queued: list[dict[str, Any]] = []
    for source in cards:
        row = dict(source)
        key = identity_key(row)
        memberships = row.get("rankMemberships")
        if not key or not isinstance(memberships, Mapping):
            continue
        target_ranks = {
            str(index): int(rank)
            for index, rank in memberships.items()
            if str(index) in INDEXES and isinstance(rank, int) and 1 <= rank <= TRACKED_INDEX_LIMIT
        }
        if not target_ranks or str(row.get("historyBackfillStatus") or "").casefold() == "ready":
            continue
        prior = previous_members.get(key, {})
        was_target = any(
            index in INDEXES and isinstance(rank, int) and 1 <= rank <= TRACKED_INDEX_LIMIT
            for index, rank in prior.items()
        )
        queued.append(
            {
                "identityKey": key,
                "pokedexId": row.get("pokedexId"),
                "tcg": row.get("tcg"),
                "rankMemberships": dict(sorted(target_ranks.items())),
                "reason": "pending_history" if was_target else "new_target",
            }
        )
    queued.sort(
        key=lambda row: (
            min(int(rank) for rank in row["rankMemberships"].values()),
            str(row["identityKey"]),
        )
    )
    return queued


def derive_rankings(
    rows: Iterable[Mapping[str, Any]],
    *,
    effective_at: datetime,
    limit: int | None = None,
    previous: Mapping[str, Any] | None = None,
    required_views: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build complete canonical memberships and independently-gated presentation views.

    ``limit`` remains accepted for legacy callers but is deliberately ignored:
    rank membership is no longer a Top-350 storage boundary.  Callers that
    publish a view must pass ``required_views`` so only that view is gated.
    """

    if limit is not None and limit < 1:
        raise ValueError("legacy rank limit must be positive")
    if effective_at.tzinfo is None:
        effective_at = effective_at.replace(tzinfo=timezone.utc)
    effective_at = effective_at.astimezone(timezone.utc)
    materialized = [dict(row) for row in rows if isinstance(row, Mapping)]
    rejected: Counter[str] = Counter()
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in materialized:
        reason = rejection_reason(row, effective_at)
        if reason:
            rejected[reason] += 1
            continue
        groups.setdefault(identity_key(row), []).append(row)
    canonical = [choose_canonical(group, effective_at) for _, group in sorted(groups.items())]
    rankings: dict[str, list[dict[str, Any]]] = {}
    for index in INDEXES:
        candidates = canonical if index == "tcg" else [row for row in canonical if row["tcg"] == index]
        rankings[index] = sorted(
            candidates,
            key=lambda row: (-float(row["marketCapUsd"]), identity_key(row)),
        )
    memberships: dict[str, dict[str, int]] = {}
    for index, ranked in rankings.items():
        for rank, row in enumerate(ranked, start=1):
            memberships.setdefault(identity_key(row), {})[index] = rank
    selected = {
        identity_key(row): row
        for ranked in rankings.values()
        for row in ranked
    }
    previous_members = prior_memberships(previous)
    cards: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    for key, row in sorted(
        selected.items(),
        key=lambda item: (int(memberships[item[0]].get("tcg", len(canonical) + 1)), item[0]),
    ):
        current = dict(sorted(memberships[key].items()))
        card = dict(row)
        card["rankMemberships"] = current
        cards.append(card)
        prior = previous_members.get(key, {})
        history.append(
            {
                "identityKey": key,
                "current": current,
                "previous": prior,
                "changed": current != prior,
            }
        )
    ranking_rows = {
        index: [
            {"identityKey": identity_key(row), "rank": rank}
            for rank, row in enumerate(ranked, start=1)
        ]
        for index, ranked in rankings.items()
    }
    publication = publication_validation(ranking_rows, required_views)
    promotion = {"status": publication["status"], "blockers": publication["blockers"]}
    view_rows = presentation_views(ranking_rows)
    monitoring = monitoring_candidates(materialized, rankings, effective_at)
    payload = {
        "effectiveAt": iso_utc(effective_at),
        "policy": {
            "formula": "gemrate_psa10_population * validated_psa10_reference_price_usd",
            "populationMinimumInclusive": POPULATION_MINIMUM,
            "priceFreshnessHoursMaximum": PRICE_FRESHNESS_HOURS,
            "canonicalMembership": "complete_eligible",
            "presentationViews": list(PRESENTATION_VIEWS),
            "indexes": list(INDEXES),
            "languagePartitioning": False,
            "canonicalCardStoredOnce": True,
            "monitoringPopulationRangeInclusive": {
                "minimum": MONITORING_POPULATION_MINIMUM,
                "maximum": MONITORING_POPULATION_MAXIMUM,
            },
            "monitoringProjectedRank300ProximityMinimum": PROJECTED_RANK300_PROXIMITY,
        },
        "counts": {
            "input": len(materialized),
            "validatedCanonical": len(canonical),
            "uniqueRanked": len(cards),
            "monitoringCandidates": len(monitoring),
            "indexes": {index: len(ranked) for index, ranked in rankings.items()},
        },
        "rejected": dict(sorted(rejected.items())),
        "promotion": promotion,
        "publication": publication,
        "rankings": ranking_rows,
        "presentationViews": view_rows,
        "cards": cards,
        "membershipHistory": history,
        "historicalBackfillQueue": historical_backfill_queue(cards, previous_members),
        "monitoringCandidates": monitoring,
        "monitoring": {
            "status": "pre_entry_daily_pool",
            "discoveryOnlyPopulationMaximum": MONITORING_POPULATION_MINIMUM - 1,
            "formalRankingPopulationMinimum": POPULATION_MINIMUM,
        },
    }
    payload["rankingSha256"] = stable_hash(
        {
            key: payload[key]
            for key in ("policy", "rankings", "presentationViews", "cards", "membershipHistory")
        }
    )
    payload["schemaVersion"] = "5.0.0"
    payload["payloadSha256"] = stable_hash(cards)
    payload["monitoringPayloadSha256"] = stable_hash(monitoring)
    payload["collectionPayloadSha256"] = stable_hash(
        {"cards": cards, "monitoringCandidates": monitoring}
    )
    return payload
