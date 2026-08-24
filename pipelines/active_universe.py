#!/usr/bin/env python3
"""Select the bounded CARDZ tracking universe before provider collection.

The discovery catalogue may be larger, but daily time-series collection is
limited to 300 canonical printings per TCG and card-language segment: the
market-cap Top 100 plus at most 200 candidates that can plausibly enter it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from daily_prices import PricePoint, resolve_windows
from g10_ingest import load_landing_replay, parse_effective_at, read_json
from g10_public_snapshot import normalize_collector, opaque_id
from lang_registry import SUPPORTED_CARD_LANGUAGES
from market_source_sync import (
    DEFAULT_GEMRATE,
    DEFAULT_LANDING,
    deduped_rows,
    gemrate_populations,
    g10_psa_population,
    latest,
)
from source_crosswalk import DEFAULT_OUT as DEFAULT_CROSSWALK, DEFAULT_SOURCE, build_crosswalk


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "runtime" / "private-source-map" / "active-universe.json"
LOCK_SCHEMA_VERSION = "1.0.0"
EXCLUDED_CARD_LANGUAGES = {"th"}
TRACKED_TCGS = {"pokemon", "one-piece"}
LANGUAGE_ALIASES = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "us": "en",
    "ja": "ja",
    "jp": "ja",
    "jpn": "ja",
    "japanese": "ja",
    "ko": "ko",
    "kr": "ko",
    "kor": "ko",
    "korean": "ko",
    "zh-hans": "zhCN",
    "zh-cn": "zhCN",
    "simplified-chinese": "zhCN",
    "zh-hant": "zhTW",
    "zh-tw": "zhTW",
    "traditional-chinese": "zhTW",
}


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def serialized_document(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def write_immutable(path: Path, payload: bytes) -> bool:
    """Create an immutable lock file, or verify an identical prior write."""

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
        return False
    except FileExistsError:
        if path.read_bytes() != payload:
            raise RuntimeError(f"immutable active-universe lock mismatch: {path}")
        return True


def canonical_language(value: Any) -> str:
    key = str(value or "").strip().casefold().replace("_", "-")
    return LANGUAGE_ALIASES.get(key, "")


def has_required_native_text(language: str, name: str, set_name: str) -> bool:
    """Reject English-only metadata pretending to be a Korean printing."""

    if language != "ko":
        return True
    return re.search(r"[\uac00-\ud7a3]", f"{name} {set_name}") is not None


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def choose_population(
    source_root: Path,
    gemrate_root: Path,
    replay: Any,
    source_ref: tuple[str, str],
    gemrate_id: str | None,
) -> tuple[int | None, date | None, str]:
    candidates: list[tuple[date, int, str, int]] = []
    replayed = latest(replay.grader_populations.get((*source_ref, "PSA")))
    if replayed is not None and isinstance(replayed[1], int):
        candidates.append((replayed[0], replayed[1], "canonical_daily", 3))
    if gemrate_id:
        official = gemrate_populations(gemrate_root / gemrate_id / "population.json")
        if official is not None and isinstance(official[1].get("PSA"), int):
            candidates.append((official[0], official[1]["PSA"], "official_snapshot", 4))
    fallback = g10_psa_population(source_root, source_ref)
    if isinstance(fallback, int):
        candidates.append((date.min, fallback, "bootstrap_last_good", 1))
    if not candidates:
        return None, None, "unavailable"
    observed, value, source, _ = max(candidates, key=lambda row: (row[0], row[3]))
    return value, None if observed == date.min else observed, source


def price_metrics(
    row: Mapping[str, Any],
    replay: Any,
    source_ref: tuple[str, str],
    effective_at: datetime,
) -> tuple[float | None, date | None, str, dict[str, float | None]]:
    daily = replay.prices.get(source_ref) or {}
    current = latest(daily)
    points = [
        PricePoint(datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp(), float(value), "daily")
        for day, value in daily.items()
        if isinstance(value, (int, float)) and value > 0
    ]
    windows = resolve_windows(points, effective_at.timestamp()) if points else {}
    changes: dict[str, float | None] = {
        window: (
            float(windows.get(f"change_{window}_pct", {}).get("value"))
            if isinstance(windows.get(f"change_{window}_pct", {}).get("value"), (int, float))
            else None
        )
        for window in ("1d", "7d", "30d")
    }
    upstream_30d = row.get("change30dPct")
    if changes["30d"] is None and isinstance(upstream_30d, (int, float)):
        changes["30d"] = float(upstream_30d)
    if current is not None:
        return float(current[1]), current[0], "canonical_daily", changes
    fallback = row.get("priceUsd")
    if isinstance(fallback, (int, float)) and fallback > 0:
        return float(fallback), None, "bootstrap_last_good", changes
    return None, None, "unavailable", changes


def enrich_candidates(
    source_root: Path,
    crosswalk: Mapping[str, Any],
    landing_root: Path,
    gemrate_root: Path,
    effective_at: datetime,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    replay = load_landing_replay(landing_root)
    rows = deduped_rows(source_root)
    enriched: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    for source_card in crosswalk.get("cards", []):
        if not isinstance(source_card, Mapping):
            continue
        source_ref = (
            str(source_card.get("canonicalSourceCode") or ""),
            str(source_card.get("canonicalExternalId") or ""),
        )
        market_row = rows.get(source_ref)
        if market_row is None:
            rejected["missing_bootstrap_row"] += 1
            continue
        market, row = market_row
        tcg = "one-piece" if market == "opcg" else "pokemon"
        # The exact asset/crosswalk identity is authoritative for the printing
        # language. Index constituents sometimes carry the market locale
        # instead, which must not move a card into a different language quota.
        language = canonical_language(source_card.get("language")) or canonical_language(row.get("lang"))
        if language not in SUPPORTED_CARD_LANGUAGES:
            rejected["unsupported_card_language"] += 1
            continue
        raw_collector = str(source_card.get("collectorNumberRaw") or "")
        # Japanese promo printings use a "-P" promo suffix after the slash
        # (227/S-P, 207/XY-P). Some source rows mislabel them as en/zhCN; the
        # suffix is authoritative for the printing language.
        if "/" in raw_collector and raw_collector.split("/")[-1].endswith("-P") and language != "ja":
            language = "ja"
        name = str(source_card.get("name") or "").strip()
        collector = normalize_collector(source_card.get("collectorNumberRaw"), language)
        set_name = str(source_card.get("setName") or row.get("setName") or "").strip()
        if not name:
            rejected["missing_name"] += 1
            continue
        if not set_name:
            rejected["missing_set"] += 1
            continue
        if not has_required_native_text(language, name, set_name):
            rejected["korean_localization_missing"] += 1
            continue
        if not collector.complete:
            rejected["incomplete_collector_number"] += 1
            continue

        population, population_as_of, population_source = choose_population(
            source_root,
            gemrate_root,
            replay,
            source_ref,
            str(source_card.get("gemrateId") or "") or None,
        )
        if not isinstance(population, int) or population <= 100:
            rejected["population_not_over_100"] += 1
            continue
        price, price_as_of, price_source, changes = price_metrics(row, replay, source_ref, effective_at)
        market_cap = float(price) * population if isinstance(price, (int, float)) and price > 0 else None
        pokedex_id = opaque_id(tcg, language, set_name, collector, name)
        enriched.append(
            {
                **dict(source_card),
                "pokedexId": pokedex_id,
                "pokedexStatus": "confirmed",
                "tcg": tcg,
                "language": language,
                "segment": f"{tcg}:{language}",
                "setName": set_name,
                "collectorNumber": collector.display,
                "populationPsa10": population,
                "populationAsOf": population_as_of.isoformat() if population_as_of else None,
                "populationSourceState": population_source,
                "priceUsd": round(price, 6) if isinstance(price, (int, float)) else None,
                "priceAsOf": price_as_of.isoformat() if price_as_of else None,
                "priceSourceState": price_source,
                "marketCapUsd": round(market_cap, 2) if market_cap is not None else None,
                "change1dPct": changes["1d"],
                "change7dPct": changes["7d"],
                "change30dPct": changes["30d"],
            }
        )
    return enriched, rejected


def select_segment(
    rows: Iterable[Mapping[str, Any]],
    *,
    top_limit: int,
    segment_limit: int,
    near_population: int,
) -> list[dict[str, Any]]:
    materialized = [dict(row) for row in rows]
    market_order = sorted(
        (row for row in materialized if isinstance(row.get("marketCapUsd"), (int, float))),
        key=lambda row: (-float(row["marketCapUsd"]), row["pokedexId"]),
    )
    shadow_rank = {row["pokedexId"]: rank for rank, row in enumerate(market_order, start=1)}
    eligible = [row for row in market_order if int(row["populationPsa10"]) >= 1000]
    core = eligible[:top_limit]
    core_ids = {row["pokedexId"] for row in core}
    cutoff = float(core[-1]["marketCapUsd"]) if len(core) == top_limit else None

    watch: list[dict[str, Any]] = []
    for row in materialized:
        if row["pokedexId"] in core_ids:
            continue
        population = int(row["populationPsa10"])
        rank = shadow_rank.get(row["pokedexId"])
        changes = [
            float(value)
            for value in (row.get("change7dPct"), row.get("change30dPct"))
            if isinstance(value, (int, float))
        ]
        positive_momentum = max(changes, default=0.0)
        signals: list[str] = []
        if rank is not None and rank <= segment_limit:
            signals.append("near_top100")
        if near_population <= population < 1000:
            signals.append("near_population_1000")
        if population >= 1000:
            signals.append("eligible_below_top100")
        if isinstance(row.get("change7dPct"), (int, float)) and row["change7dPct"] > 0:
            signals.append("rising_7d")
        if isinstance(row.get("change30dPct"), (int, float)) and row["change30dPct"] > 0:
            signals.append("rising_30d")
        if not signals:
            continue
        cap_ratio = 0.0
        if cutoff and isinstance(row.get("marketCapUsd"), (int, float)):
            cap_ratio = min(float(row["marketCapUsd"]) / cutoff, 1.0)
        population_progress = min(population / 1000.0, 1.0)
        momentum_score = min(max(positive_momentum, 0.0) / 25.0, 1.0)
        score = round(cap_ratio * 0.55 + population_progress * 0.30 + momentum_score * 0.15, 6)
        watch.append(
            {
                **row,
                "shadowMarketCapRank": rank,
                "selectionSignals": signals,
                "watchScore": score,
            }
        )
    watch.sort(
        key=lambda row: (
            -float(row["watchScore"]),
            -(float(row["marketCapUsd"]) if isinstance(row.get("marketCapUsd"), (int, float)) else -1.0),
            -int(row["populationPsa10"]),
            row["pokedexId"],
        )
    )
    capacity = min(max(segment_limit - len(core), 0), max(segment_limit - top_limit, 0))
    selected = [
        {**row, "role": "top100", "marketRank": rank, "selectionSignals": ["top100_market_cap"]}
        for rank, row in enumerate(core, start=1)
    ]
    selected.extend(
        {**row, "role": "watchlist", "watchPosition": position}
        for position, row in enumerate(watch[:capacity], start=1)
    )
    return selected


def build_active_universe(
    candidates: Iterable[Mapping[str, Any]],
    rejected: Mapping[str, int],
    effective_at: datetime,
    *,
    top_limit: int = 100,
    segment_limit: int = 300,
    near_population: int = 700,
) -> dict[str, Any]:
    if top_limit < 1 or segment_limit < top_limit:
        raise ValueError("segment limit must be at least the Top 100 limit")
    grouped: dict[str, dict[str, list[Mapping[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in candidates:
        grouped[str(row["segment"])][str(row["pokedexId"])].append(row)
    rejected_counts = Counter({str(key): int(value) for key, value in rejected.items()})
    segments: dict[str, Any] = {}
    active_cards: list[dict[str, Any]] = []
    for segment, identities in sorted(grouped.items()):
        rows: list[Mapping[str, Any]] = []
        for identity_rows in identities.values():
            ordered = sorted(
                identity_rows,
                key=lambda row: (
                    -int(bool(row.get("gemrateId"))),
                    -int(isinstance(row.get("snkItemId"), int)),
                    -(float(row["marketCapUsd"]) if isinstance(row.get("marketCapUsd"), (int, float)) else -1.0),
                    -int(row.get("populationPsa10") or 0),
                    str(row.get("canonicalSourceCode") or ""),
                    str(row.get("canonicalExternalId") or ""),
                ),
            )
            rows.append(ordered[0])
            if len(ordered) > 1:
                rejected_counts["duplicate_canonical_identity"] += len(ordered) - 1
        selected = select_segment(
            rows,
            top_limit=top_limit,
            segment_limit=segment_limit,
            near_population=near_population,
        )
        for position, row in enumerate(selected, start=1):
            active_cards.append({**row, "universePosition": position})
        segments[segment] = {
            "catalogCandidates": len(rows),
            "active": len(selected),
            "top100": sum(row["role"] == "top100" for row in selected),
            "watchlist": sum(row["role"] == "watchlist" for row in selected),
            "completeTop100": sum(row["role"] == "top100" for row in selected) == top_limit,
        }
    for tcg in sorted(TRACKED_TCGS):
        for language in sorted(SUPPORTED_CARD_LANGUAGES):
            segments.setdefault(
                f"{tcg}:{language}",
                {"catalogCandidates": 0, "active": 0, "top100": 0, "watchlist": 0, "completeTop100": False},
            )
    active_cards.sort(key=lambda row: (row["segment"], int(row["universePosition"]), row["pokedexId"]))
    identity = [
        {
            "pokedexId": row["pokedexId"],
            "pokedexStatus": row["pokedexStatus"],
            "canonicalSourceCode": row["canonicalSourceCode"],
            "canonicalExternalId": row["canonicalExternalId"],
            "gemrateId": row.get("gemrateId"),
            "snkItemId": row.get("snkItemId"),
            "tcg": row["tcg"],
            "language": row["language"],
            "segment": row["segment"],
            "name": row["name"],
            "setName": row["setName"],
            "collectorNumber": row["collectorNumber"],
            "populationPsa10": row["populationPsa10"],
            "populationAsOf": row["populationAsOf"],
            "populationSourceState": row["populationSourceState"],
            "priceUsd": row["priceUsd"],
            "priceAsOf": row["priceAsOf"],
            "priceSourceState": row["priceSourceState"],
            "marketCapUsd": row["marketCapUsd"],
            "change1dPct": row["change1dPct"],
            "change7dPct": row["change7dPct"],
            "change30dPct": row["change30dPct"],
            "role": row["role"],
            "marketRank": row.get("marketRank"),
            "watchPosition": row.get("watchPosition"),
            "shadowMarketCapRank": row.get("shadowMarketCapRank"),
            "selectionSignals": row["selectionSignals"],
            "watchScore": row.get("watchScore"),
            "universePosition": row["universePosition"],
        }
        for row in active_cards
    ]
    return {
        "schemaVersion": "1.0.0",
        "effectiveAt": iso_utc(effective_at),
        "policy": {
            "partition": "tcg_x_card_language",
            "segmentLimit": segment_limit,
            "top100Limit": top_limit,
            "watchlistLimit": segment_limit - top_limit,
            "populationMinimumExclusive": 100,
            "rankingPopulationMinimum": 1000,
            "nearPopulationThreshold": near_population,
            "canonicalIdentityRequired": True,
            "supportedCardLanguages": sorted(SUPPORTED_CARD_LANGUAGES),
            "excludedCardLanguages": sorted(EXCLUDED_CARD_LANGUAGES),
            "koreanNativeTextRequired": True,
        },
        "counts": {
            "active": len(identity),
            "top100": sum(row["role"] == "top100" for row in identity),
            "watchlist": sum(row["role"] == "watchlist" for row in identity),
            "gemrateExact": sum(bool(row.get("gemrateId")) for row in identity),
            "snkExact": sum(isinstance(row.get("snkItemId"), int) for row in identity),
            "rejected": sum(rejected_counts.values()),
        },
        "rejected": dict(sorted(rejected_counts.items())),
        "segments": segments,
        "payloadSha256": stable_hash(identity),
        "cards": identity,
    }


def validate_active_universe(document: Mapping[str, Any], *, require_lock: bool) -> None:
    cards = document.get("cards")
    if not isinstance(cards, list):
        raise ValueError("active universe cards are missing")
    if document.get("payloadSha256") != stable_hash(cards):
        raise ValueError("active universe payload hash is invalid")
    if any(not isinstance(card, Mapping) for card in cards):
        raise ValueError("active universe contains an invalid card")
    identities = [str(card.get("pokedexId") or "") for card in cards]
    if any(not identity for identity in identities) or len(set(identities)) != len(cards):
        raise ValueError("active universe contains duplicate or empty canonical identities")
    sources: set[tuple[str, str]] = set()
    roles: dict[str, Counter[str]] = defaultdict(Counter)
    for card in cards:
        tcg = str(card.get("tcg") or "")
        language = str(card.get("language") or "")
        segment = str(card.get("segment") or "")
        role = str(card.get("role") or "")
        source_ref = (
            str(card.get("canonicalSourceCode") or "").casefold(),
            str(card.get("canonicalExternalId") or ""),
        )
        if language not in SUPPORTED_CARD_LANGUAGES or segment != f"{tcg}:{language}":
            raise ValueError("active universe contains an invalid language segment")
        if not all(source_ref) or source_ref in sources:
            raise ValueError("active universe contains a duplicate or empty source identity")
        if role not in {"top100", "watchlist"}:
            raise ValueError("active universe contains an invalid member role")
        sources.add(source_ref)
        roles[segment][role] += 1
    for segment, counts in roles.items():
        if counts["top100"] > 100 or counts["watchlist"] > 200 or sum(counts.values()) > 300:
            raise ValueError(f"active universe segment quota is invalid: {segment}")
    if not require_lock:
        return
    lock = document.get("lock")
    if not isinstance(lock, Mapping):
        raise ValueError("active universe is not a frozen lock")
    if lock.get("schemaVersion") != LOCK_SCHEMA_VERSION:
        raise ValueError("active universe lock schema is unsupported")
    if lock.get("selectionPayloadSha256") != document.get("payloadSha256"):
        raise ValueError("active universe lock payload does not match its selected cards")
    immutable_file = str(lock.get("immutableFile") or "")
    if not immutable_file or Path(immutable_file).name != immutable_file:
        raise ValueError("active universe lock has an invalid immutable filename")


def freeze_active_universe(
    document: Mapping[str, Any],
    output: Path,
    lock_root: Path,
) -> tuple[dict[str, Any], Path, bool]:
    """Freeze one versioned selection and activate that exact immutable file."""

    unlocked = dict(document)
    unlocked.pop("lock", None)
    validate_active_universe(unlocked, require_lock=False)
    effective_at = parse_effective_at(str(unlocked.get("effectiveAt") or ""))
    lock_id = f"universe_{effective_at.strftime('%Y%m%dT%H%M%SZ')}_{str(unlocked['payloadSha256'])[:12]}"
    immutable_file = f"{lock_id}.json"
    locked = {
        **unlocked,
        "lock": {
            "schemaVersion": LOCK_SCHEMA_VERSION,
            "lockId": lock_id,
            "createdAt": iso_utc(effective_at),
            "immutableFile": immutable_file,
            "selectionPayloadSha256": unlocked["payloadSha256"],
            "selectionPolicy": "top100_plus_up_to_200_per_tcg_x_card_language",
        },
    }
    validate_active_universe(locked, require_lock=True)
    payload = serialized_document(locked)
    immutable_path = lock_root / immutable_file
    replayed = write_immutable(immutable_path, payload)
    atomic_write(output, payload)
    return locked, immutable_path, replayed


def load_active_universe_lock(output: Path, lock_root: Path) -> tuple[dict[str, Any], Path]:
    document = read_json(output)
    if not isinstance(document, Mapping):
        raise ValueError("active universe lock is invalid")
    validate_active_universe(document, require_lock=True)
    immutable_path = lock_root / str(document["lock"]["immutableFile"])
    if not immutable_path.is_file():
        raise RuntimeError(f"active-universe immutable lock is missing: {immutable_path}")
    immutable = read_json(immutable_path)
    if immutable != document:
        raise RuntimeError(f"active-universe pointer does not match immutable lock: {immutable_path}")
    return dict(document), immutable_path


def write_provider_ids(document: Mapping[str, Any], gemrate_ids: Path, snk_ids: Path) -> None:
    """Regenerate private provider worklists from the frozen selection only."""

    validate_active_universe(document, require_lock=True)
    cards = document["cards"]
    gemrate = sorted({str(card["gemrateId"]) for card in cards if isinstance(card, Mapping) and card.get("gemrateId")})
    snk = sorted({int(card["snkItemId"]) for card in cards if isinstance(card, Mapping) and isinstance(card.get("snkItemId"), int)})
    atomic_write(gemrate_ids, (("\n".join(gemrate) + "\n") if gemrate else "").encode("ascii"))
    atomic_write(snk_ids, (("\n".join(str(value) for value in snk) + "\n") if snk else "").encode("ascii"))


def self_test() -> dict[str, Any]:
    cards: list[dict[str, Any]] = []
    for index in range(1, 331):
        population = 1500 if index <= 120 else 700 + (index % 290)
        cards.append(
            {
                "pokedexId": f"ja-{index:03d}",
                "pokedexStatus": "confirmed",
                "canonicalSourceCode": "fixture",
                "canonicalExternalId": f"ja-{index:03d}",
                "gemrateId": f"gem-{index:03d}",
                "snkItemId": index,
                "tcg": "pokemon",
                "language": "ja",
                "segment": "pokemon:ja",
                "name": f"Card {index}",
                "setName": "Fixture Set",
                "collectorNumber": f"{index:03d}/999",
                "populationPsa10": population,
                "populationAsOf": "2026-07-23",
                "populationSourceState": "fixture",
                "priceUsd": 10.0,
                "priceAsOf": "2026-07-23",
                "priceSourceState": "fixture",
                "marketCapUsd": float(1_000_000 - index * 1000),
                "change1dPct": None,
                "change7dPct": 1.0 if index % 2 else -1.0,
                "change30dPct": 2.0 if index % 3 else -2.0,
            }
        )
    cards.append(
        {
            "pokedexId": "ko-001",
            "pokedexStatus": "confirmed",
            "canonicalSourceCode": "fixture",
            "canonicalExternalId": "ko-001",
            "gemrateId": "gem-ko-001",
            "snkItemId": 999,
            "tcg": "pokemon",
            "language": "ko",
            "segment": "pokemon:ko",
            "name": "피카츄",
            "setName": "프로모 카드",
            "collectorNumber": "001/999",
            "populationPsa10": 101,
            "populationAsOf": "2026-07-23",
            "populationSourceState": "fixture",
            "priceUsd": 10.0,
            "priceAsOf": "2026-07-23",
            "priceSourceState": "fixture",
            "marketCapUsd": 1000.0,
            "change1dPct": None,
            "change7dPct": 5.0,
            "change30dPct": None,
        }
    )
    document = build_active_universe(cards, {"incomplete_collector_number": 1}, datetime(2026, 7, 23, tzinfo=timezone.utc))
    with tempfile.TemporaryDirectory(prefix="cardz-active-lock-") as temporary:
        root = Path(temporary)
        output = root / "active-universe.json"
        lock_root = root / "active-universe-locks"
        gemrate_ids = root / "active-gemrate-ids.txt"
        snk_ids = root / "active-snk-ids.txt"
        locked, immutable_path, first_replayed = freeze_active_universe(document, output, lock_root)
        original_gemrate_id = str(locked["cards"][0]["gemrateId"])
        document["cards"][0]["gemrateId"] = "must-not-enter-locked-provider-list"
        reused, reused_path = load_active_universe_lock(output, lock_root)
        write_provider_ids(reused, gemrate_ids, snk_ids)
        _, _, second_replayed = freeze_active_universe(reused, output, lock_root)
        lock_checks = {
            "lockCreated": immutable_path.is_file() and not first_replayed,
            "lockReused": reused_path == immutable_path and second_replayed,
            "providerIdsFromLock": original_gemrate_id in gemrate_ids.read_text("ascii").splitlines()
            and "must-not-enter-locked-provider-list" not in gemrate_ids.read_text("ascii"),
            "lockId": reused["lock"]["lockId"],
        }
    return {
        "active": document["counts"]["active"],
        "ja": document["segments"]["pokemon:ja"],
        "ko": document["segments"]["pokemon:ko"],
        "allOver100": all(int(row["populationPsa10"]) > 100 for row in document["cards"]),
        "maxSegment": max(value["active"] for value in document["segments"].values()),
        "koreanAlias": canonical_language("kr"),
        "thaiAlias": canonical_language("th"),
        "nativeKoreanAccepted": has_required_native_text("ko", "피카츄", "프로모 카드"),
        "englishOnlyKoreanRejected": not has_required_native_text("ko", "Pikachu", "Promo Card"),
        **lock_checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the bounded CARDZ active market universe")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--crosswalk", type=Path, default=DEFAULT_CROSSWALK)
    parser.add_argument("--landing-root", type=Path, default=DEFAULT_LANDING)
    parser.add_argument("--gemrate-root", type=Path, default=DEFAULT_GEMRATE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--lock-root", type=Path)
    parser.add_argument("--gemrate-ids-out", type=Path)
    parser.add_argument("--snk-ids-out", type=Path)
    parser.add_argument(
        "--refresh-lock",
        action="store_true",
        help="explicitly recalculate and activate a new versioned Top 100 + candidate lock",
    )
    parser.add_argument("--effective-at")
    parser.add_argument("--segment-limit", type=int, default=300)
    parser.add_argument("--top-limit", type=int, default=100)
    parser.add_argument("--near-population", type=int, default=700)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), sort_keys=True))
        return 0
    output = args.out.resolve()
    lock_root = args.lock_root.resolve() if args.lock_root else output.parent / "active-universe-locks"
    gemrate_ids = (args.gemrate_ids_out or output.with_name("active-gemrate-ids.txt")).resolve()
    snk_ids = (args.snk_ids_out or output.with_name("active-snk-ids.txt")).resolve()
    if output.is_file() and not args.refresh_lock:
        existing = read_json(output)
        if isinstance(existing, Mapping) and isinstance(existing.get("lock"), Mapping):
            document, immutable_path = load_active_universe_lock(output, lock_root)
            write_provider_ids(document, gemrate_ids, snk_ids)
            print(
                json.dumps(
                    {
                        "status": "ready",
                        "lockStatus": "reused",
                        "output": str(output),
                        "immutableLock": str(immutable_path),
                        "lockId": document["lock"]["lockId"],
                        **document["counts"],
                        "segments": document["segments"],
                    },
                    sort_keys=True,
                )
            )
            return 0
    if not args.refresh_lock and any(lock_root.glob("universe_*.json")):
        raise RuntimeError(
            "immutable active-universe locks exist but the active pointer is missing or legacy; "
            "restore the intended lock or explicitly pass --refresh-lock"
        )
    source_root = args.source_root.resolve()
    if not source_root.is_dir():
        raise SystemExit(f"source root does not exist: {source_root}")
    crosswalk = read_json(args.crosswalk.resolve()) if args.crosswalk.is_file() else build_crosswalk(source_root)
    effective_at = parse_effective_at(args.effective_at) if args.effective_at else datetime.now(timezone.utc)
    candidates, rejected = enrich_candidates(
        source_root,
        crosswalk,
        args.landing_root.resolve(),
        args.gemrate_root.resolve(),
        effective_at,
    )
    document = build_active_universe(
        candidates,
        rejected,
        effective_at,
        top_limit=args.top_limit,
        segment_limit=args.segment_limit,
        near_population=args.near_population,
    )
    locked, immutable_path, replayed = freeze_active_universe(document, output, lock_root)
    write_provider_ids(locked, gemrate_ids, snk_ids)
    print(
        json.dumps(
            {
                "status": "ready",
                "lockStatus": "refreshed" if args.refresh_lock else ("replayed" if replayed else "created"),
                "output": str(output),
                "immutableLock": str(immutable_path),
                "lockId": locked["lock"]["lockId"],
                **locked["counts"],
                "segments": locked["segments"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
