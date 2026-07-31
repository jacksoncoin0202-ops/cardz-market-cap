#!/usr/bin/env python3
"""Audit the real CARDZ market-data coverage before DB or public promotion.

Provider IDs are only mappings.  A card becomes market-ready only when its
mapped GemRate population payload and exact SNK PSA 10 price row both pass
identity, freshness and value checks.  The report is private pipeline evidence
and contains refill worklists for the next automated collection run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CROSSWALK = ROOT / "data" / "runtime" / "private-source-map" / "source-crosswalk.json"
DEFAULT_ACTIVE = ROOT / "data" / "runtime" / "private-source-map" / "tracked-universe.json"
DEFAULT_GEMRATE = ROOT / "data" / "private" / "gemrate" / "cards"
DEFAULT_FX = ROOT / "data" / "runtime" / "private-fx" / "latest.json"
DEFAULT_OUT = ROOT / "data" / "runtime" / "private-reports" / "data-coverage-audit.json"
DEFAULT_G10_LANDING = ROOT / "data" / "runtime" / "private-landing" / "g10" / "full"
PSA10_CONDITION = "trading_card_single_psa10"
PRESENTATION_VIEW_LIMITS = {
    "top100": 100,
    "top300": 300,
    "top350": 350,
    "top100_plus_200": 300,
    "top300_boards": 300,
    "reserve50": 350,
}
PRESENTATION_VIEW_REQUIREMENTS = {
    "top300_boards": {"tcg": 300, "pokemon": 100, "onePiece": 100},
}
RESERVE_START_RANK = 301
RESERVE_END_RANK = 350
SUPPORTED_MARKETS = {"pokemon", "one-piece"}
SUPPORTED_LANGUAGES = {"en", "ja", "ko", "zhCN", "zhTW"}
ONE_PIECE_NUMBER = re.compile(r"^(?:OP|ST|EB|P|PRB|DON)\d{0,2}-\d{3,4}$", re.IGNORECASE)


def stable_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_bytes(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
    os.replace(temporary, path)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(value.strip()), datetime.min.time(), tzinfo=timezone.utc)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_language(value: Any) -> str:
    aliases = {
        "en": "en",
        "english": "en",
        "ja": "ja",
        "jp": "ja",
        "japanese": "ja",
        "ko": "ko",
        "kr": "ko",
        "korean": "ko",
        "zhcn": "zhCN",
        "cn": "zhCN",
        "simplified chinese": "zhCN",
        "zhtw": "zhTW",
        "tw": "zhTW",
        "traditional chinese": "zhTW",
    }
    return aliases.get(str(value or "").strip().casefold(), "")


def collector_complete(value: Any) -> bool:
    collector = str(value or "").strip().upper()
    if ONE_PIECE_NUMBER.fullmatch(collector):
        return True
    if "/" not in collector:
        return False
    left, right = collector.split("/", 1)
    return bool(re.search(r"\d", left) and re.search(r"[A-Z0-9]", right))


def collector_parts(value: Any) -> tuple[str, ...]:
    return tuple(re.findall(r"[A-Z]+[0-9]*|[0-9]+", str(value or "").upper()))


def collector_matches(expected: Any, row: Mapping[str, Any]) -> bool:
    expected_parts = collector_parts(expected)
    if not expected_parts:
        return False
    evidence = " ".join(
        str(row.get(key) or "")
        for key in ("product_number", "name", "localized_name")
    )
    evidence_parts = set(collector_parts(evidence))
    return all(part in evidence_parts for part in expected_parts)


def gemrate_population(path: Path, expected_id: str, captured_at: datetime) -> tuple[int, str, str] | None:
    if not path.is_file():
        return None
    document = read_json(path)
    data = document.get("data") if isinstance(document, Mapping) else None
    if not isinstance(data, Mapping):
        return None
    actual_id = str(data.get("universal_gemrate_id") or data.get("gemrate_id") or "").casefold()
    if actual_id != expected_id.casefold():
        return None
    population = data.get("population")
    population_data = population.get("population_data") if isinstance(population, Mapping) else None
    by_grader = population_data.get("by_grader") if isinstance(population_data, Mapping) else None
    psa = by_grader.get("psa") if isinstance(by_grader, Mapping) else None
    grades = psa.get("grades") if isinstance(psa, Mapping) else None
    value = grades.get("psa_10") if isinstance(grades, Mapping) else None
    if not isinstance(value, int) or value < 0:
        return None
    observed_raw = population_data.get("data_last_updated") if isinstance(population_data, Mapping) else None
    observed_at = parse_datetime(observed_raw)
    if observed_at is None:
        return None
    age_days = (captured_at.date() - observed_at.date()).days
    status = "ready" if age_days <= 1 else "stale" if age_days <= 2 else "unavailable"
    if status == "unavailable":
        return None
    return value, status, observed_at.date().isoformat()


def load_snk_rows(path: Path | None) -> dict[int, Mapping[str, Any]]:
    if path is None or not path.is_file():
        return {}
    rows: dict[int, Mapping[str, Any]] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        item_id = value.get("item_id") if isinstance(value, Mapping) else None
        if isinstance(item_id, int):
            rows[item_id] = value
    return rows


def g10_mirror_population(root: Path | None, card: Mapping[str, Any]) -> int | None:
    """Read the G10-carried GemRate mirror for comparison only.

    The mirror is discovery/bootstrap evidence.  It never substitutes for the
    direct GemRate population used by the ranking gate.
    """

    if root is None or not root.is_dir():
        return None
    if not (root / "cards").is_dir():
        candidates: list[tuple[int, str, Path]] = []
        for path in root.iterdir():
            if not path.is_dir() or not (path / "payload" / "cards").is_dir():
                continue
            try:
                manifest = read_json(path / "manifest.json")
                card_count = int(manifest.get("cardCount") or 0) if isinstance(manifest, Mapping) else 0
            except (OSError, json.JSONDecodeError, ValueError):
                card_count = 0
            candidates.append((card_count, path.name, path))
        if not candidates:
            return None
        root = max(candidates)[2] / "payload"
    storage = str(card.get("storageScope") or card.get("canonicalSourceCode") or "").casefold()
    external_id = str(card.get("canonicalExternalId") or "")
    path = root / "cards" / storage / external_id / "populations.json"
    if not path.is_file():
        return None
    try:
        document = read_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    populations = document.get("population") if isinstance(document, Mapping) else None
    if not isinstance(populations, list):
        return None
    for row in populations:
        if not isinstance(row, Mapping) or str(row.get("gradeName") or "").upper() != "PSA":
            continue
        value = row.get("topGrade")
        if isinstance(value, int) and value >= 0:
            return value
    return None


def load_ebay_fallback_coverage(path: Path | None, captured_at: datetime) -> dict[tuple[str, str], int]:
    """Return exact, non-duplicate PSA 10 sold counts within the last 30 days."""

    if path is None or not path.is_file():
        return {}
    earliest = captured_at.date().toordinal() - 30
    coverage: dict[tuple[str, str], int] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return coverage
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, Mapping) or row.get("schemaVersion") != "1.0.0":
            continue
        source_ref = (str(row.get("sourceCode") or "").casefold(), str(row.get("externalEntityId") or ""))
        if not all(source_ref):
            continue
        transaction_ids: set[str] = set()
        for transaction in row.get("transactions") if isinstance(row.get("transactions"), list) else []:
            if not isinstance(transaction, Mapping):
                continue
            transaction_id = str(transaction.get("transactionId") or "")
            sold_on = parse_datetime(transaction.get("soldDate"))
            price = transaction.get("unitPrice")
            quantity = transaction.get("quantity")
            if (
                not transaction_id
                or sold_on is None
                or sold_on.date().toordinal() < earliest
                or sold_on.date() > captured_at.date()
                or not isinstance(price, (int, float))
                or price <= 0
                or quantity != 1
            ):
                continue
            transaction_ids.add(transaction_id)
        coverage[source_ref] = len(transaction_ids)
    return coverage


def read_canonical_db_coverage(enabled: bool) -> tuple[dict[str, Any], dict[tuple[str, str], set[str]]]:
    """Read only canonical observation coverage when an operator opts in.

    This intentionally uses the existing ``CARDZ_DB_*`` environment contract,
    never writes, and never includes connection details in the report.
    """

    if not enabled:
        return {"state": "not_queried"}, {}
    try:
        import pymysql  # type: ignore[import-not-found]

        connection = pymysql.connect(
            host=os.environ.get("CARDZ_DB_HOST", "127.0.0.1"),
            port=int(os.environ.get("CARDZ_DB_PORT", "3308")),
            database=os.environ.get("CARDZ_DB_NAME", "cardz_market_cap"),
            user=os.environ.get("CARDZ_DB_USER", "cardz"),
            password=os.environ.get("CARDZ_DB_PASSWORD", ""),
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5,
            read_timeout=5,
            write_timeout=5,
        )
    except Exception:
        return {"state": "unavailable"}, {}
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT external_entity_id, observation_kind
                FROM market_source_observation
                WHERE source_code IN ('gemrate', 'snk_psa10', 'ebay_psa10')
                """
            )
            rows = cursor.fetchall()
    except Exception:
        return {"state": "unavailable"}, {}
    finally:
        connection.close()
    coverage: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        external = str(row.get("external_entity_id") or "")
        source_code, separator, external_id = external.partition(":")
        if separator and source_code and external_id:
            coverage[(source_code.casefold(), external_id)].add(str(row.get("observation_kind") or ""))
    return {"state": "available", "observationRows": len(rows), "identityRows": len(coverage)}, coverage


def snk_price(
    row: Mapping[str, Any] | None,
    collector_number: str,
    captured_at: datetime,
    jpy_per_usd: float,
) -> tuple[float, str, str] | tuple[None, str, None]:
    if row is None:
        return None, "snk_payload_missing", None
    if row.get("condition_filter") != PSA10_CONDITION:
        return None, "snk_grade_mismatch", None
    if not collector_matches(collector_number, row):
        return None, "snk_collector_mismatch", None
    points = row.get("kline")
    if not isinstance(points, list):
        return None, "snk_price_history_missing", None
    valid: list[tuple[date, float]] = []
    for point in points:
        if not isinstance(point, Mapping):
            continue
        observed = parse_datetime(point.get("date"))
        value = point.get("price_jpy")
        if observed is not None and isinstance(value, (int, float)) and value > 0:
            valid.append((observed.date(), float(value)))
    if not valid:
        return None, "snk_price_history_missing", None
    observed_date, price_jpy = max(valid)
    age_days = (captured_at.date() - observed_date).days
    if age_days > 2:
        return None, "snk_price_stale", None
    status = "ready" if age_days <= 1 else "stale"
    return round(price_jpy / jpy_per_usd, 6), status, observed_date.isoformat()


def load_active_overlay(path: Path | None) -> dict[tuple[str, str], Mapping[str, Any]]:
    if path is None or not path.is_file():
        return {}
    document = read_json(path)
    cards = document.get("cards") if isinstance(document, Mapping) else None
    if not isinstance(cards, list):
        return {}
    return {
        (str(card.get("canonicalSourceCode") or "").casefold(), str(card.get("canonicalExternalId") or "")): card
        for card in cards
        if isinstance(card, Mapping)
    }


def presentation_view_readiness(
    view_name: str,
    ranking_counts: Mapping[str, int],
) -> tuple[dict[str, bool], dict[str, int]]:
    minimum = PRESENTATION_VIEW_LIMITS[view_name]
    requirements = PRESENTATION_VIEW_REQUIREMENTS.get(
        view_name,
        {scope: minimum for scope in ("pokemon", "onePiece", "tcg")},
    )
    return (
        {
            scope: int(ranking_counts.get(scope, 0)) >= required
            for scope, required in requirements.items()
        },
        dict(requirements),
    )


def build_audit(
    crosswalk_path: Path,
    gemrate_root: Path,
    snk_path: Path | None,
    *,
    captured_at: datetime,
    jpy_per_usd: float,
    active_path: Path | None = None,
    discovery_complete: bool = False,
    g10_mirror_root: Path | None = None,
    ebay_path: Path | None = None,
    canonical_db: bool = False,
) -> dict[str, Any]:
    if jpy_per_usd <= 0:
        raise ValueError("jpy_per_usd must be positive")
    document = read_json(crosswalk_path)
    cards = document.get("cards") if isinstance(document, Mapping) else None
    if not isinstance(cards, list):
        raise ValueError("crosswalk cards are missing")
    active = load_active_overlay(active_path)
    snk_rows = load_snk_rows(snk_path)
    ebay_coverage = load_ebay_fallback_coverage(ebay_path, captured_at)
    canonical_db_inventory, canonical_db_coverage = read_canonical_db_coverage(canonical_db)
    counts: Counter[str] = Counter()
    quarantine: list[dict[str, Any]] = []
    ready: list[dict[str, Any]] = []
    gemrate_refill: set[str] = set()
    snk_refill: set[int] = set()
    missing_snk_mapping: list[dict[str, str]] = []
    worklist: list[dict[str, Any]] = []
    mirror_available = 0
    mirror_matches_direct = 0
    mirror_mismatches: list[dict[str, Any]] = []

    for raw in cards:
        if not isinstance(raw, Mapping):
            continue
        source_code = str(raw.get("canonicalSourceCode") or "").casefold()
        external_id = str(raw.get("canonicalExternalId") or "")
        if active and (source_code, external_id) not in active:
            continue
        counts["mapped"] += 1
        overlay = active.get((source_code, external_id), {})
        market = str(raw.get("market") or overlay.get("tcg") or "")
        language = canonical_language(raw.get("language") or overlay.get("language"))
        collector = str(overlay.get("collectorNumber") or raw.get("collectorNumberRaw") or "").strip()
        reason: str | None = None
        if market not in SUPPORTED_MARKETS:
            reason = "unsupported_tcg"
        elif language not in SUPPORTED_LANGUAGES:
            reason = "unsupported_language"
        elif not collector_complete(collector):
            reason = "collector_incomplete"

        gemrate_id = str(raw.get("gemrateId") or "").casefold()
        population = gemrate_population(
            gemrate_root / gemrate_id / "population.json",
            gemrate_id,
            captured_at,
        ) if gemrate_id else None
        if population is None:
            if gemrate_id:
                gemrate_refill.add(gemrate_id)
            reason = reason or "gemrate_payload_missing_or_invalid"
        else:
            counts["gemrateVerified"] += 1

        mirror_population = g10_mirror_population(g10_mirror_root, raw)
        if mirror_population is not None:
            mirror_available += 1
            if population is not None:
                if mirror_population == population[0]:
                    mirror_matches_direct += 1
                else:
                    mirror_mismatches.append(
                        {
                            "sourceCode": source_code,
                            "externalId": external_id,
                            "directPsa10": population[0],
                            "mirrorPsa10": mirror_population,
                        }
                    )

        snk_item_id = raw.get("snkItemId")
        if not isinstance(snk_item_id, int):
            missing_snk_mapping.append({"sourceCode": source_code, "externalId": external_id})
            price = (None, "snk_mapping_missing", None)
        else:
            price = snk_price(snk_rows.get(snk_item_id), collector, captured_at, jpy_per_usd)
            if price[0] is None:
                snk_refill.add(snk_item_id)
            else:
                counts["snkVerified"] += 1
        reason = reason or (price[1] if price[0] is None else None)
        ebay_sold_count = ebay_coverage.get((source_code, external_id), 0)
        required_sources: list[str] = []
        advisory_sources: list[str] = []
        if population is None:
            required_sources.append("gemrate_direct_psa10_population")
        if price[0] is None:
            required_sources.append("snk_exact_psa10_price")
        if mirror_population is None:
            advisory_sources.append("grade10_gemrate_mirror")
        elif population is not None and mirror_population != population[0]:
            advisory_sources.append("grade10_gemrate_mirror_mismatch")
        if ebay_sold_count < 3:
            advisory_sources.append("ebay_exact_psa10_sold_30d")
        canonical_kinds = canonical_db_coverage.get((source_code, external_id), set())
        if canonical_db_inventory["state"] == "available" and (
            "grader_population_psa" not in canonical_kinds or "index_constituent" not in canonical_kinds
        ):
            advisory_sources.append("canonical_db_observations")
        if required_sources or advisory_sources:
            worklist.append(
                {
                    "sourceCode": source_code,
                    "externalId": external_id,
                    "market": market,
                    "required": required_sources,
                    "advisory": advisory_sources,
                }
            )

        if reason is not None or population is None or price[0] is None:
            quarantine.append(
                {
                    "sourceCode": source_code,
                    "externalId": external_id,
                    "market": market,
                    "language": language or None,
                    "collectorNumber": collector or None,
                    "reason": reason or "unresolved",
                }
            )
            continue

        population_value, population_status, population_as_of = population
        price_usd, price_status, price_as_of = price
        if price_usd is None or price_as_of is None:
            continue
        counts["marketReady"] += 1
        market_cap = round(price_usd * population_value, 6)
        ready.append(
            {
                "canonicalSourceCode": source_code,
                "canonicalExternalId": external_id,
                "market": market,
                "language": language,
                "name": str(overlay.get("name") or raw.get("name") or "").strip(),
                "setName": str(overlay.get("setName") or raw.get("setName") or "").strip(),
                "collectorNumber": collector,
                "populationPsa10": population_value,
                "populationStatus": population_status,
                "populationAsOf": population_as_of,
                "pricePsa10Usd": price_usd,
                "priceStatus": price_status,
                "priceAsOf": price_as_of,
                "marketCapUsd": market_cap,
            }
        )

    eligible = [row for row in ready if row["populationPsa10"] >= 1000]
    counts["top100Eligible"] = len(eligible)
    counts["top350Eligible"] = len(eligible)
    for metric in ("mapped", "gemrateVerified", "snkVerified", "marketReady", "top100Eligible", "top350Eligible"):
        counts.setdefault(metric, 0)
    tracked = [row for row in ready if row["populationPsa10"] > 100]
    key = lambda row: (-float(row["marketCapUsd"]), row["market"], row["canonicalExternalId"])
    combined = sorted(eligible, key=key)
    for rank, row in enumerate(combined, start=1):
        row["rank"] = rank
    combined_ids = {(row["canonicalSourceCode"], row["canonicalExternalId"]) for row in combined}
    watchlist = [
        row for row in sorted(tracked, key=key)
        if (row["canonicalSourceCode"], row["canonicalExternalId"]) not in combined_ids
    ][:200]
    for rank, row in enumerate(watchlist, start=len(combined) + 1):
        row["rank"] = rank
    by_market: dict[str, list[dict[str, Any]]] = {}
    for market in sorted(SUPPORTED_MARKETS):
        rows = sorted((row for row in eligible if row["market"] == market), key=key)
        by_market[market] = [{**row, "rank": rank} for rank, row in enumerate(rows, start=1)]
    segment_counts: Counter[str] = Counter(
        f"{row['market']}:{row['language']}" for row in ready
    )
    ranking_counts = {
        "pokemon": len(by_market["pokemon"]),
        "onePiece": len(by_market["one-piece"]),
        "tcg": len(combined),
    }
    current_facts_verified = (
        discovery_complete
        and not gemrate_refill
        and not snk_refill
        and not missing_snk_mapping
    )
    presentation_views: dict[str, dict[str, Any]] = {}
    for view_name, minimum_rank in PRESENTATION_VIEW_LIMITS.items():
        ready, requirements = presentation_view_readiness(view_name, ranking_counts)
        presentation_views[view_name] = {
            "minimumRank": minimum_rank,
            "requirements": requirements,
            "range": (
                {"start": RESERVE_START_RANK, "end": RESERVE_END_RANK}
                if view_name == "reserve50"
                else {"start": 1, "end": minimum_rank}
            ),
            "ready": ready,
            "verified": current_facts_verified and all(ready.values()),
        }
    top350_ready = presentation_views["top350"]["ready"]
    global_top100_verified = bool(presentation_views["top100"]["verified"])
    global_top350_verified = bool(presentation_views["top350"]["verified"])

    report: dict[str, Any] = {
        "schemaVersion": "1.2.0",
        "capturedAt": iso_utc(captured_at),
        "counts": dict(sorted(counts.items())),
        "coverage": {
            "discoveryComplete": discovery_complete,
            "globalTop100Verified": global_top100_verified,
            "globalTop350Verified": global_top350_verified,
            "top350Ready": top350_ready,
            "currentFactsVerified": current_facts_verified,
            "rankingCounts": ranking_counts,
            "presentationViews": presentation_views,
            "segmentMarketReady": dict(sorted(segment_counts.items())),
            "status": "ready" if current_facts_verified else "blocked",
        },
        "rankings": {
            "combined": combined,
            "byMarket": by_market,
            "watchlist": watchlist,
        },
        "sourceInventory": {
            "gemrateDirect": {"verifiedPsa10Cards": counts["gemrateVerified"]},
            "grade10GemrateMirror": {
                "availablePsa10Cards": mirror_available,
                "matchesDirect": mirror_matches_direct,
                "mismatches": sorted(mirror_mismatches, key=lambda row: (row["sourceCode"], row["externalId"])),
            },
            "snk": {"verifiedExactPsa10Cards": counts["snkVerified"]},
            "ebay": {
                "available": ebay_path is not None and ebay_path.is_file(),
                "fallbackReadyCards": sum(count >= 3 for count in ebay_coverage.values()),
            },
            "canonicalDb": canonical_db_inventory,
        },
        "gapWorklist": sorted(
            worklist,
            key=lambda row: (not bool(row["required"]), row["market"], row["sourceCode"], row["externalId"]),
        ),
        "refill": {
            "gemrateIds": sorted(gemrate_refill),
            "snkItemIds": sorted(snk_refill),
            "missingSnkMapping": sorted(
                missing_snk_mapping,
                key=lambda row: (row["sourceCode"], row["externalId"]),
            ),
        },
        "quarantine": sorted(
            quarantine,
            key=lambda row: (row["reason"], row["sourceCode"], row["externalId"]),
        ),
    }
    report["payloadSha256"] = hashlib.sha256(stable_json(report)).hexdigest()
    return report


def jpy_rate(path: Path) -> float:
    document = read_json(path)
    rates = document.get("rates") if isinstance(document, Mapping) else None
    jpy = rates.get("JPY") if isinstance(rates, Mapping) else None
    if isinstance(jpy, Mapping):
        value = jpy.get("value")
    else:
        value = jpy
    if not isinstance(value, (int, float)) or value <= 0:
        raise ValueError("FX snapshot has no positive JPY rate")
    return float(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit real CARDZ GemRate/SNK coverage")
    parser.add_argument("--crosswalk", type=Path, default=DEFAULT_CROSSWALK)
    parser.add_argument("--active-universe", type=Path, default=DEFAULT_ACTIVE)
    parser.add_argument("--gemrate-root", type=Path, default=DEFAULT_GEMRATE)
    parser.add_argument("--snk-run", type=Path)
    parser.add_argument("--g10-mirror-root", type=Path, default=DEFAULT_G10_LANDING)
    parser.add_argument("--ebay-run", type=Path)
    parser.add_argument("--canonical-db", action="store_true", help="Read canonical MySQL observation coverage; never writes")
    parser.add_argument("--fx-snapshot", type=Path, default=DEFAULT_FX)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--discovery-complete", action="store_true")
    parser.add_argument(
        "--require-presentation-view",
        choices=sorted(PRESENTATION_VIEW_LIMITS),
        help="Fail closed unless all ranking scopes can supply this display-only view",
    )
    parser.add_argument("--require-global-top350", action="store_true")
    args = parser.parse_args()

    report = build_audit(
        args.crosswalk.resolve(),
        args.gemrate_root.resolve(),
        args.snk_run.resolve() if args.snk_run else None,
        captured_at=datetime.now(timezone.utc),
        jpy_per_usd=jpy_rate(args.fx_snapshot.resolve()),
        active_path=args.active_universe.resolve() if args.active_universe else None,
        discovery_complete=args.discovery_complete,
        g10_mirror_root=args.g10_mirror_root.resolve() if args.g10_mirror_root else None,
        ebay_path=args.ebay_run.resolve() if args.ebay_run else None,
        canonical_db=args.canonical_db,
    )
    atomic_write_json(args.out.resolve(), report)
    print(json.dumps({"output": str(args.out.resolve()), **report["counts"], **report["coverage"]}, sort_keys=True))
    required_view = "top350" if args.require_global_top350 else args.require_presentation_view
    if required_view and not report["coverage"]["presentationViews"][required_view]["verified"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
