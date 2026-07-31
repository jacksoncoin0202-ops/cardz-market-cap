#!/usr/bin/env python3
"""Canonical-MySQL authority for the CARDZ collection universe.

This module deliberately separates three operations:

* build an immutable schema-5 candidate from current canonical DB facts;
* materialize that exact candidate without changing the current lock;
* explicitly promote a fully verified materialized lock in one transaction.

The tracked-universe file is a consumer artefact, not an input to candidate
selection.  Candidate selection uses only exact, non-estimated GemRate PSA 10
population observations attached to confirmed canonical variants.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import pymysql
from pymysql.connections import Connection

try:
    from db_runtime import (
        active_universe_lock_hash,
        canonical_json,
        parse_datetime,
        validate_active_universe,
    )
except ModuleNotFoundError:  # pragma: no cover - package import path
    from pipelines.db_runtime import (  # type: ignore[no-redef]
        active_universe_lock_hash,
        canonical_json,
        parse_datetime,
        validate_active_universe,
    )


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DATABASE = "cardz_market_cap"
DEFAULT_ACTIVE = ROOT / "data" / "runtime" / "private-source-map" / "tracked-universe.json"
DEFAULT_CANDIDATE_ROOT = (
    ROOT / "data" / "runtime" / "private-source-map" / "universe-candidates"
)
DEFAULT_PUBLISH_ROOT = ROOT / "data" / "public" / "publish-staging"
DEFAULT_CANONICAL_DB_QC_ROOT = (
    ROOT / "data" / "runtime" / "private-reports" / "canonical-db-qc"
)
GEMRATE_ID = re.compile(r"[0-9a-f]{40}")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
GENERATION_ID = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,126}[A-Za-z0-9])?")
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}")
MEDIA_ASSET_KEY = re.compile(
    r"market-assets/([0-9a-f]{64})(?:_(200|600))?\.webp"
)
PUBLIC_MEDIA_SPECS = {
    "base": ("", None, None),
    "200": ("_200", 200, 280),
    "600": ("_600", 429, 600),
}
POPULATION_LABELS = ("10", "PSA 10", "top")
SUPPORTED_TCGS = ("pokemon", "one-piece")
ADVISORY_LOCK = "cardz_market_cap_universe_authority"
MAX_GENERATION_AGE_HOURS = 48.0


LATEST_EXACT_POPULATION_SQL = """
    SELECT
        observation.id AS observation_id,
        observation.variant_id AS observed_variant_id,
        COALESCE(observation_alias.canonical_variant_id, observation.variant_id)
            AS resolved_variant_id,
        canonical_variant.opaque_id,
        canonical_variant.tcg_code,
        canonical_variant.card_language,
        canonical_variant.canonical_name,
        canonical_variant.set_name,
        canonical_variant.collector_number,
        observation.external_entity_id AS gemrate_id,
        observation.top_grade_population,
        observation.effective_at,
        observation.observed_date,
        observation.payload_sha256,
        printing_identity.tcg_code AS printing_tcg_code,
        printing_identity.card_language AS printing_card_language,
        printing_identity.set_name AS printing_set_name,
        printing_identity.collector_number AS printing_collector_number,
        printing_identity.edition_code,
        printing_identity.parallel_code,
        printing_identity.finish_code,
        printing_identity.canonical_printing_sha256,
        printing_identity.identity_status AS printing_identity_status,
        COALESCE(printing_hash_counts.identity_count, 0) AS printing_hash_count,
        COALESCE(printing_tuple_counts.identity_count, 0) AS printing_tuple_count
    FROM market_grader_population_observation AS observation
    LEFT JOIN catalog_variant_alias AS observation_alias
        ON observation_alias.duplicate_variant_id = observation.variant_id
    JOIN catalog_variant AS canonical_variant
        ON canonical_variant.id =
           COALESCE(observation_alias.canonical_variant_id, observation.variant_id)
    JOIN catalog_source_identity AS source_identity
        ON source_identity.source_code = 'gemrate'
       AND source_identity.external_entity_id = observation.external_entity_id
       AND source_identity.match_status = 'exact'
    LEFT JOIN catalog_variant_alias AS source_alias
        ON source_alias.duplicate_variant_id = source_identity.variant_id
    LEFT JOIN catalog_printing_identity AS printing_identity
        ON printing_identity.variant_id = canonical_variant.id
    LEFT JOIN (
        SELECT LOWER(TRIM(canonical_printing_sha256)) AS identity_key,
               COUNT(*) AS identity_count
        FROM catalog_printing_identity
        WHERE LOWER(TRIM(identity_status)) = 'canonical'
        GROUP BY LOWER(TRIM(canonical_printing_sha256))
    ) AS printing_hash_counts
        ON printing_hash_counts.identity_key =
           LOWER(TRIM(printing_identity.canonical_printing_sha256))
    LEFT JOIN (
        SELECT LOWER(TRIM(tcg_code)) AS tcg_code,
               LOWER(TRIM(card_language)) AS card_language,
               LOWER(TRIM(set_name)) AS set_name,
               LOWER(TRIM(collector_number)) AS collector_number,
               LOWER(TRIM(edition_code)) AS edition_code,
               LOWER(TRIM(parallel_code)) AS parallel_code,
               LOWER(TRIM(finish_code)) AS finish_code,
               COUNT(*) AS identity_count
        FROM catalog_printing_identity
        WHERE LOWER(TRIM(identity_status)) = 'canonical'
        GROUP BY LOWER(TRIM(tcg_code)), LOWER(TRIM(card_language)),
                 LOWER(TRIM(set_name)),
                 LOWER(TRIM(collector_number)), LOWER(TRIM(edition_code)),
                 LOWER(TRIM(parallel_code)), LOWER(TRIM(finish_code))
    ) AS printing_tuple_counts
        ON printing_tuple_counts.tcg_code =
           LOWER(TRIM(printing_identity.tcg_code))
       AND printing_tuple_counts.card_language =
           LOWER(TRIM(printing_identity.card_language))
       AND printing_tuple_counts.set_name =
           LOWER(TRIM(printing_identity.set_name))
       AND printing_tuple_counts.collector_number =
           LOWER(TRIM(printing_identity.collector_number))
       AND printing_tuple_counts.edition_code =
           LOWER(TRIM(printing_identity.edition_code))
       AND printing_tuple_counts.parallel_code =
           LOWER(TRIM(printing_identity.parallel_code))
       AND printing_tuple_counts.finish_code =
           LOWER(TRIM(printing_identity.finish_code))
    WHERE observation.source_code = 'gemrate'
      AND observation.grader_code = 'PSA'
      AND observation.top_grade_label IN ('10', 'PSA 10', 'top')
      AND observation.estimated = 0
      AND canonical_variant.identity_status = 'confirmed'
      AND canonical_variant.tcg_code IN ('pokemon', 'one-piece')
      AND COALESCE(source_alias.canonical_variant_id, source_identity.variant_id) =
          COALESCE(observation_alias.canonical_variant_id, observation.variant_id)
"""


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _date_iso(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


def _json_value(value: Any) -> Any:
    if isinstance(value, (str, bytes, bytearray)):
        raw = value.decode("utf-8") if isinstance(value, (bytes, bytearray)) else value
        return json.loads(raw)
    return value


def _normalized_json(value: Any) -> bytes:
    return canonical_json(_json_value(value))


def _printing_key(*parts: Any) -> tuple[str, str, str, str, str, str, str]:
    return tuple(str(part or "").strip().casefold() for part in parts)  # type: ignore[return-value]


def _printing_key_sha256(key: Sequence[str]) -> str:
    return hashlib.sha256("|".join(key).encode("utf-8")).hexdigest()


def _printing_row_identity(
    row: Mapping[str, Any],
) -> tuple[tuple[str, str, str, str, str, str, str], dict[str, Any]] | None:
    key = _printing_key(
        row.get("printing_tcg_code"),
        row.get("printing_card_language"),
        row.get("printing_set_name"),
        row.get("printing_collector_number"),
        row.get("edition_code"),
        row.get("parallel_code"),
        row.get("finish_code"),
    )
    base_key = _printing_key(
        row.get("tcg_code"),
        row.get("card_language"),
        row.get("set_name"),
        row.get("collector_number"),
    )
    stored_hash = str(row.get("canonical_printing_sha256") or "").strip().casefold()
    if (
        str(row.get("printing_identity_status") or "").strip().casefold()
        != "canonical"
        or any(not part for part in key)
        or key[:4] != base_key
        or not SHA256_HEX.fullmatch(stored_hash)
        or stored_hash != _printing_key_sha256(key)
        or int(row.get("printing_hash_count") or 0) != 1
        or int(row.get("printing_tuple_count") or 0) != 1
    ):
        return None
    return key, {
        "status": "canonical",
        "cardLanguage": str(row["printing_card_language"]).strip(),
        "setName": str(row["printing_set_name"]).strip(),
        "collectorNumber": str(row["printing_collector_number"]).strip(),
        "editionCode": str(row["edition_code"]).strip(),
        "parallelCode": str(row["parallel_code"]).strip(),
        "finishCode": str(row["finish_code"]).strip(),
        "canonicalPrintingSha256": stored_hash,
    }


def _candidate_printing_key(
    card: Mapping[str, Any],
) -> tuple[tuple[str, str, str, str, str, str, str], str]:
    identity = card.get("printingIdentity")
    if not isinstance(identity, Mapping) or identity.get("status") != "canonical":
        raise ValueError("universe candidate has no canonical printing identity")
    key = _printing_key(
        card.get("tcg"),
        identity.get("cardLanguage"),
        identity.get("setName"),
        identity.get("collectorNumber"),
        identity.get("editionCode"),
        identity.get("parallelCode"),
        identity.get("finishCode"),
    )
    stored_hash = str(identity.get("canonicalPrintingSha256") or "").strip().casefold()
    if (
        any(not part for part in key)
        or (key[0], key[2], key[3])
        != _printing_key(
            card.get("tcg"), card.get("setName"), card.get("collectorNumber")
        )
        or not SHA256_HEX.fullmatch(stored_hash)
        or stored_hash != _printing_key_sha256(key)
    ):
        raise ValueError("universe candidate canonical printing identity is invalid")
    return key, stored_hash


def _assert_canonical_database(cursor: Any) -> None:
    cursor.execute("SELECT DATABASE() AS database_name")
    row = cursor.fetchone()
    database = str((row or {}).get("database_name") or "")
    if database != CANONICAL_DATABASE:
        raise RuntimeError(
            f"universe authority refuses non-canonical database: {database or 'none'}"
        )


def _population_order(row: Mapping[str, Any]) -> tuple[str, str, int]:
    return (
        _iso(row["effective_at"]),
        _date_iso(row["observed_date"]),
        int(row["observation_id"]),
    )


def latest_exact_population_rows(connection: Connection) -> list[dict[str, Any]]:
    """Return one deterministic latest exact GemRate PSA10 row per canonical variant."""

    with connection.cursor() as cursor:
        _assert_canonical_database(cursor)
        cursor.execute(LATEST_EXACT_POPULATION_SQL)
        fetched = [dict(row) for row in cursor.fetchall()]

    latest: dict[int, dict[str, Any]] = {}
    for row in fetched:
        gemrate_id = str(row.get("gemrate_id") or "").strip().casefold()
        if not GEMRATE_ID.fullmatch(gemrate_id):
            raise RuntimeError("canonical DB contains an invalid exact GemRate identity")
        population = row.get("top_grade_population")
        if not isinstance(population, int) or isinstance(population, bool) or population < 0:
            raise RuntimeError("canonical DB contains an invalid exact PSA10 population")
        variant_id = int(row["resolved_variant_id"])
        current = latest.get(variant_id)
        if current is None or _population_order(row) > _population_order(current):
            row["gemrate_id"] = gemrate_id
            latest[variant_id] = row
    return sorted(
        latest.values(),
        key=lambda row: (
            str(row["tcg_code"]),
            str(row["set_name"]).casefold(),
            str(row["collector_number"]).casefold(),
            str(row["opaque_id"]),
            int(row["resolved_variant_id"]),
        ),
    )


def _candidate_card(
    row: Mapping[str, Any],
    printing_identity: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "pokedexId": str(row["opaque_id"]),
        "canonicalSourceCode": "gemrate",
        "canonicalExternalId": str(row["gemrate_id"]),
        "gemrateId": str(row["gemrate_id"]),
        "pokedexStatus": "confirmed",
        "tcg": str(row["tcg_code"]),
        "name": str(row["canonical_name"]),
        "setName": str(row["set_name"]),
        "collectorNumber": str(row["collector_number"]),
        "populationPsa10": int(row["top_grade_population"]),
        "populationAsOf": _iso(row["effective_at"]),
        "populationObservedDate": _date_iso(row["observed_date"]),
        "populationPayloadSha256": str(row["payload_sha256"]),
        "populationSourceState": "gemrate_exact",
        "populationEstimated": False,
        "printingIdentity": dict(printing_identity),
        "rankMemberships": {},
    }


def _select_candidate_members(
    connection: Connection,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    rows = latest_exact_population_rows(connection)
    qualified: list[dict[str, Any]] = []
    monitoring: list[dict[str, Any]] = []
    discovery_rows = [
        row for row in rows if int(row["top_grade_population"]) >= 971
    ]
    printing_rows = [
        (row, resolved)
        for row in discovery_rows
        if (resolved := _printing_row_identity(row)) is not None
    ]
    key_counts = Counter(resolved[0] for _row, resolved in printing_rows)
    hash_counts = Counter(
        str(resolved[1]["canonicalPrintingSha256"])
        for _row, resolved in printing_rows
    )
    eligible_rows = [
        (row, resolved[1])
        for row, resolved in printing_rows
        if key_counts[resolved[0]] == 1
        and hash_counts[str(resolved[1]["canonicalPrintingSha256"])] == 1
    ]
    for row, printing_identity in eligible_rows:
        population = int(row["top_grade_population"])
        card = _candidate_card(row, printing_identity)
        if population >= 1000:
            qualified.append(card)
        elif 971 <= population <= 999:
            monitoring.append(
                {
                    **card,
                    "monitoringState": "pre_entry_population_971_999",
                    "collectionCadence": "daily",
                    "reasons": ["population_971_999"],
                }
            )
    return qualified, monitoring, len(discovery_rows) - len(eligible_rows)


def _candidate_document(
    qualified: list[dict[str, Any]],
    monitoring: list[dict[str, Any]],
    discovery_evidence: int,
) -> dict[str, Any]:
    if not qualified:
        raise RuntimeError(
            "canonical DB has no promotable exact GemRate PSA10 POP >= 1000 "
            "candidate with a unique canonical printing identity"
        )

    effective_at = max(
        card["populationAsOf"] for card in [*qualified, *monitoring]
    )
    policy = {
        "indexes": ["tcg", "pokemon", "one-piece"],
        "canonicalMembership": "complete_eligible",
        "languagePartitioning": False,
        "requireExactPopulation": True,
        "populationAuthority": "gemrate",
        "populationMinimumInclusive": 1000,
        "monitoringPopulationRangeInclusive": {"minimum": 971, "maximum": 999},
        "selection": "latest_exact_psa10_per_canonical_variant",
        "requireCanonicalPrintingIdentity": True,
        "printingIdentityUniqueness": "canonical_sha256_and_full_tuple",
    }
    document: dict[str, Any] = {
        "schemaVersion": "5.0.0",
        "generatedAt": effective_at,
        "effectiveAt": effective_at,
        "authority": {
            "database": CANONICAL_DATABASE,
            "source": "canonical_mysql",
            "population": "gemrate_psa10_exact",
        },
        "policy": policy,
        "cards": qualified,
        "monitoringCandidates": monitoring,
        "counts": {
            "qualified": len(qualified),
            "monitoring": len(monitoring),
            "collection": len(qualified) + len(monitoring),
            "discoveryEvidence": discovery_evidence,
        },
    }
    document["payloadSha256"] = hashlib.sha256(canonical_json(qualified)).hexdigest()
    document["monitoringPayloadSha256"] = hashlib.sha256(
        canonical_json(monitoring)
    ).hexdigest()
    document["collectionPayloadSha256"] = hashlib.sha256(
        canonical_json(
            {"cards": qualified, "monitoringCandidates": monitoring}
        )
    ).hexdigest()
    _validate_authority_candidate(document)
    return document


def build_candidate(connection: Connection) -> dict[str, Any]:
    """Build a deterministic schema-5 candidate from canonical DB facts only."""

    return _candidate_document(*_select_candidate_members(connection))


def write_immutable_candidate(
    document: Mapping[str, Any],
    output_root: Path = DEFAULT_CANDIDATE_ROOT,
) -> dict[str, Any]:
    """Write a content-addressed candidate without touching the active file."""

    _validate_authority_candidate(document)
    lock_hash = active_universe_lock_hash(document)
    output_root = output_root.resolve()
    destination = output_root / f"universe_{lock_hash}.json"
    if destination.resolve() == DEFAULT_ACTIVE.resolve():
        raise RuntimeError("candidate build refuses to write the active-universe file")
    payload = _candidate_bytes(document)
    output_root.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as handle:
            handle.write(payload)
    except FileExistsError:
        if destination.read_bytes() != payload:
            raise RuntimeError(
                f"immutable universe candidate mismatch: {destination}"
            ) from None
        replayed = True
    else:
        replayed = False
    return {
        "candidate": str(destination),
        "lockSha256": lock_hash,
        "qualified": len(document["cards"]),
        "monitoring": len(document.get("monitoringCandidates", [])),
        "discoveryEvidence": int(document["counts"]["discoveryEvidence"]),
        "replayed": replayed,
    }


def _candidate_bytes(document: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def archive_active_evidence(
    active_path: Path,
    archive_root: Path | None = None,
) -> dict[str, Any] | None:
    """Archive the exact pre-promotion active bytes under their content hash."""

    active_path = active_path.resolve()
    if not active_path.is_file():
        return None
    payload = active_path.read_bytes()
    content_hash = hashlib.sha256(payload).hexdigest()
    root = (archive_root or (active_path.parent / "universe-locks")).resolve()
    destination = root / f"active_{content_hash}.json"
    root.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if destination.read_bytes() != payload:
            raise RuntimeError(
                f"immutable active-universe evidence mismatch: {destination}"
            ) from None
        replayed = True
    else:
        replayed = False
    return {
        "path": str(destination),
        "contentSha256": content_hash,
        "replayed": replayed,
    }


def atomic_replace_active(
    document: Mapping[str, Any],
    active_path: Path,
) -> dict[str, Any]:
    """Atomically replace the active consumer after the DB promotion commits."""

    _validate_authority_candidate(document)
    active_path = active_path.resolve()
    payload = _candidate_bytes(document)
    lock_hash = active_universe_lock_hash(document)
    active_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = active_path.with_name(
        f".{active_path.name}.{os.getpid()}.{lock_hash}.tmp"
    )
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, active_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    if active_path.read_bytes() != payload:
        raise RuntimeError("active-universe atomic replacement verification failed")
    return {
        "path": str(active_path),
        "lockSha256": lock_hash,
        "contentSha256": hashlib.sha256(payload).hexdigest(),
    }


def read_candidate(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schemaVersion") != "5.0.0":
        raise ValueError("universe candidate must be a schema-5 object")
    validate_active_universe(document)
    return document


def _candidate_members(document: Mapping[str, Any]) -> list[tuple[Mapping[str, Any], bool]]:
    formal = document.get("cards")
    monitoring = document.get("monitoringCandidates")
    if not isinstance(formal, list) or not isinstance(monitoring, list):
        raise ValueError("schema-5 candidate member lists are invalid")
    return [
        *((card, True) for card in formal),
        *((card, False) for card in monitoring),
    ]


def _validate_authority_candidate(document: Mapping[str, Any]) -> None:
    validate_active_universe(document)
    if document.get("schemaVersion") != "5.0.0":
        raise ValueError("universe authority candidate must use schema 5")
    policy = document.get("policy")
    if (
        not isinstance(policy, Mapping)
        or policy.get("requireCanonicalPrintingIdentity") is not True
        or policy.get("printingIdentityUniqueness")
        != "canonical_sha256_and_full_tuple"
    ):
        raise ValueError("universe candidate canonical printing policy is invalid")
    members = _candidate_members(document)
    keys: list[tuple[str, str, str, str, str, str]] = []
    hashes: list[str] = []
    for card, _formal in members:
        if not isinstance(card, Mapping):
            raise ValueError("universe candidate contains an invalid member")
        key, printing_hash = _candidate_printing_key(card)
        keys.append(key)
        hashes.append(printing_hash)
    if len(set(keys)) != len(keys) or len(set(hashes)) != len(hashes):
        raise ValueError("universe candidate contains duplicate canonical printings")
    counts = document.get("counts")
    if (
        not isinstance(counts, Mapping)
        or counts.get("qualified") != len(document["cards"])
        or counts.get("monitoring") != len(document["monitoringCandidates"])
        or counts.get("collection") != len(members)
        or not isinstance(counts.get("discoveryEvidence"), int)
        or isinstance(counts.get("discoveryEvidence"), bool)
        or int(counts["discoveryEvidence"]) < 0
    ):
        raise ValueError("universe candidate counts are invalid")


def _live_printing_rows(
    cursor: Any,
    variant_ids: Sequence[int],
) -> dict[int, dict[str, Any]]:
    placeholders = ", ".join(["%s"] * len(variant_ids))
    cursor.execute(
        f"""
        SELECT
            printing.variant_id,
            variant.tcg_code,
            variant.card_language,
            variant.set_name,
            variant.collector_number,
            printing.tcg_code AS printing_tcg_code,
            printing.card_language AS printing_card_language,
            printing.set_name AS printing_set_name,
            printing.collector_number AS printing_collector_number,
            printing.edition_code,
            printing.parallel_code,
            printing.finish_code,
            printing.canonical_printing_sha256,
            printing.identity_status AS printing_identity_status,
            COALESCE(hash_counts.identity_count, 0) AS printing_hash_count,
            COALESCE(tuple_counts.identity_count, 0) AS printing_tuple_count
        FROM catalog_variant AS variant
        LEFT JOIN catalog_printing_identity AS printing
            ON printing.variant_id = variant.id
        LEFT JOIN (
            SELECT LOWER(TRIM(canonical_printing_sha256)) AS identity_key,
                   COUNT(*) AS identity_count
            FROM catalog_printing_identity
            WHERE LOWER(TRIM(identity_status)) = 'canonical'
            GROUP BY LOWER(TRIM(canonical_printing_sha256))
        ) AS hash_counts
            ON hash_counts.identity_key =
               LOWER(TRIM(printing.canonical_printing_sha256))
        LEFT JOIN (
            SELECT LOWER(TRIM(tcg_code)) AS tcg_code,
                   LOWER(TRIM(card_language)) AS card_language,
                   LOWER(TRIM(set_name)) AS set_name,
                   LOWER(TRIM(collector_number)) AS collector_number,
                   LOWER(TRIM(edition_code)) AS edition_code,
                   LOWER(TRIM(parallel_code)) AS parallel_code,
                   LOWER(TRIM(finish_code)) AS finish_code,
                   COUNT(*) AS identity_count
            FROM catalog_printing_identity
            WHERE LOWER(TRIM(identity_status)) = 'canonical'
            GROUP BY LOWER(TRIM(tcg_code)), LOWER(TRIM(card_language)),
                     LOWER(TRIM(set_name)),
                     LOWER(TRIM(collector_number)), LOWER(TRIM(edition_code)),
                     LOWER(TRIM(parallel_code)), LOWER(TRIM(finish_code))
        ) AS tuple_counts
            ON tuple_counts.tcg_code = LOWER(TRIM(printing.tcg_code))
           AND tuple_counts.card_language =
               LOWER(TRIM(printing.card_language))
           AND tuple_counts.set_name = LOWER(TRIM(printing.set_name))
           AND tuple_counts.collector_number =
               LOWER(TRIM(printing.collector_number))
           AND tuple_counts.edition_code = LOWER(TRIM(printing.edition_code))
           AND tuple_counts.parallel_code = LOWER(TRIM(printing.parallel_code))
           AND tuple_counts.finish_code = LOWER(TRIM(printing.finish_code))
        WHERE variant.id IN ({placeholders})
        """,
        tuple(variant_ids),
    )
    return {
        int(row["variant_id"]): dict(row)
        for row in cursor.fetchall()
        if row.get("variant_id") is not None
    }


def _resolved_candidate_variants(
    cursor: Any,
    document: Mapping[str, Any],
) -> dict[str, int]:
    members = _candidate_members(document)
    opaque_ids = [str(card["pokedexId"]) for card, _formal in members]
    placeholders = ", ".join(["%s"] * len(opaque_ids))
    cursor.execute(
        f"""
        SELECT
            variant.opaque_id,
            COALESCE(alias.canonical_variant_id, variant.id) AS resolved_variant_id
        FROM catalog_variant AS variant
        LEFT JOIN catalog_variant_alias AS alias
            ON alias.duplicate_variant_id = variant.id
        WHERE variant.opaque_id IN ({placeholders})
        """,
        tuple(opaque_ids),
    )
    resolved = {
        str(row["opaque_id"]): int(row["resolved_variant_id"])
        for row in cursor.fetchall()
    }
    missing = sorted(set(opaque_ids) - set(resolved))
    if missing:
        raise RuntimeError(f"candidate catalog identities are missing: {missing[:5]}")

    source_ids = [str(card["canonicalExternalId"]) for card, _formal in members]
    source_placeholders = ", ".join(["%s"] * len(source_ids))
    cursor.execute(
        f"""
        SELECT
            source.external_entity_id,
            COALESCE(alias.canonical_variant_id, source.variant_id) AS resolved_variant_id
        FROM catalog_source_identity AS source
        LEFT JOIN catalog_variant_alias AS alias
            ON alias.duplicate_variant_id = source.variant_id
        WHERE source.source_code = 'gemrate'
          AND source.match_status = 'exact'
          AND source.external_entity_id IN ({source_placeholders})
        """,
        tuple(source_ids),
    )
    source_resolved = {
        str(row["external_entity_id"]): int(row["resolved_variant_id"])
        for row in cursor.fetchall()
    }
    for card, _formal in members:
        opaque_id = str(card["pokedexId"])
        external_id = str(card["canonicalExternalId"])
        variant_id = resolved[opaque_id]
        if source_resolved.get(external_id) != variant_id:
            raise RuntimeError(
                f"candidate GemRate identity does not resolve to its canonical variant: {external_id}"
            )
        declared = card.get("canonicalVariantId")
        if declared is not None and int(declared) != variant_id:
            raise RuntimeError(
                f"candidate canonical variant changed after build: {opaque_id}"
            )
    if len(set(resolved.values())) != len(members):
        raise RuntimeError("candidate members collapse to duplicate canonical variants")
    live_printings = _live_printing_rows(
        cursor,
        sorted(set(resolved.values())),
    )
    for card, _formal in members:
        variant_id = resolved[str(card["pokedexId"])]
        live = _printing_row_identity(live_printings.get(variant_id, {}))
        if live is None or live[0] != _candidate_printing_key(card)[0]:
            raise RuntimeError(
                "candidate canonical printing identity changed after build: "
                f"{card['pokedexId']}"
            )
    return resolved


def _expected_member_rows(
    cursor: Any,
    document: Mapping[str, Any],
) -> list[dict[str, Any]]:
    resolved = _resolved_candidate_variants(cursor, document)
    rows: list[dict[str, Any]] = []
    for card, formal in _candidate_members(document):
        memberships = card.get("rankMemberships")
        rank = (
            memberships.get("tcg")
            if isinstance(memberships, Mapping)
            and isinstance(memberships.get("tcg"), int)
            else None
        )
        evidence = {
            "authority": "gemrate",
            "populationAsOf": card["populationAsOf"],
            "populationObservedDate": card["populationObservedDate"],
            "populationPayloadSha256": card["populationPayloadSha256"],
            "populationPsa10": card["populationPsa10"],
            "canonicalPrintingSha256": card["printingIdentity"][
                "canonicalPrintingSha256"
            ],
        }
        if formal:
            evidence["rankMemberships"] = memberships or {}
        else:
            evidence.update(
                {
                    "collectionCadence": card["collectionCadence"],
                    "monitoringState": card["monitoringState"],
                    "reasons": card.get("reasons") or [],
                }
            )
        rows.append(
            {
                "variant_id": resolved[str(card["pokedexId"])],
                "segment_code": "tracked" if formal else "pre-entry",
                "member_role": "candidate" if formal else "monitoring",
                "market_rank": rank,
                "watch_position": None,
                "watch_score": None,
                "selection_signals_json": canonical_json(evidence).decode("utf-8"),
            }
        )
    return sorted(rows, key=lambda row: int(row["variant_id"]))


def _database_member_rows(cursor: Any, lock_id: int) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT variant_id, segment_code, member_role, market_rank, watch_position,
               watch_score, selection_signals_json
        FROM market_universe_member
        WHERE universe_lock_id = %s
        ORDER BY variant_id
        """,
        (lock_id,),
    )
    rows: list[dict[str, Any]] = []
    for raw in cursor.fetchall():
        row = dict(raw)
        row["variant_id"] = int(row["variant_id"])
        row["market_rank"] = (
            int(row["market_rank"]) if row.get("market_rank") is not None else None
        )
        row["watch_position"] = (
            int(row["watch_position"])
            if row.get("watch_position") is not None
            else None
        )
        row["watch_score"] = (
            str(row["watch_score"]) if row.get("watch_score") is not None else None
        )
        row["selection_signals_json"] = _normalized_json(
            row["selection_signals_json"]
        ).decode("utf-8")
        rows.append(row)
    return rows


def _candidate_effective_at(document: Mapping[str, Any]) -> datetime:
    return parse_datetime(document.get("effectiveAt"))


def _database_datetime(value: Any) -> datetime:
    if isinstance(value, datetime) and value.tzinfo is None:
        return value
    return parse_datetime(value)


def _lock_metadata_matches(
    row: Mapping[str, Any],
    document: Mapping[str, Any],
    expected_members: int,
) -> bool:
    return (
        int(row["member_count"]) == expected_members
        and _database_datetime(row["effective_at"]) == _candidate_effective_at(document)
        and _normalized_json(row["policy_json"])
        == canonical_json(document.get("policy") or {})
    )


def materialize_candidate(
    connection: Connection,
    document: Mapping[str, Any],
    *,
    promote: bool,
) -> dict[str, Any]:
    """Materialize and optionally promote a candidate in one transaction."""

    _validate_authority_candidate(document)
    lock_hash = active_universe_lock_hash(document)
    acquired = False
    created = False
    replayed = False
    prior_current_ids: list[int] = []
    with connection.cursor() as cursor:
        _assert_canonical_database(cursor)
        cursor.execute("SELECT GET_LOCK(%s, 0) AS acquired", (ADVISORY_LOCK,))
        acquired = int((cursor.fetchone() or {}).get("acquired") or 0) == 1
    if not acquired:
        raise RuntimeError("another universe materialization is already running")

    try:
        connection.begin()
        with connection.cursor() as cursor:
            expected_rows = _expected_member_rows(cursor, document)
            cursor.execute(
                """
                SELECT id, effective_at, policy_json, member_count, is_current
                FROM market_universe_lock
                WHERE lock_sha256 = %s
                FOR UPDATE
                """,
                (lock_hash,),
            )
            lock = cursor.fetchone()
            if lock is not None:
                lock_id = int(lock["id"])
                actual_rows = _database_member_rows(cursor, lock_id)
                if not _lock_metadata_matches(lock, document, len(expected_rows)) or (
                    actual_rows != expected_rows
                ):
                    raise RuntimeError(
                        "existing universe lock with matching hash is corrupt; refusing rewrite"
                    )
                replayed = True
            else:
                cursor.execute(
                    """
                    INSERT INTO market_universe_lock
                        (lock_sha256, effective_at, policy_json, member_count, is_current)
                    VALUES (%s, %s, %s, %s, 0)
                    """,
                    (
                        lock_hash,
                        _candidate_effective_at(document),
                        canonical_json(document.get("policy") or {}).decode("utf-8"),
                        len(expected_rows),
                    ),
                )
                lock_id = int(cursor.lastrowid)
                cursor.executemany(
                    """
                    INSERT INTO market_universe_member
                        (universe_lock_id, variant_id, segment_code, member_role,
                         market_rank, watch_position, watch_score,
                         selection_signals_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            lock_id,
                            row["variant_id"],
                            row["segment_code"],
                            row["member_role"],
                            row["market_rank"],
                            row["watch_position"],
                            row["watch_score"],
                            row["selection_signals_json"],
                        )
                        for row in expected_rows
                    ],
                )
                if _database_member_rows(cursor, lock_id) != expected_rows:
                    raise RuntimeError(
                        "materialized universe lock failed exact member verification"
                    )
                created = True

            cursor.execute(
                "SELECT id FROM market_universe_lock WHERE is_current = 1 FOR UPDATE"
            )
            prior_current_ids = [int(row["id"]) for row in cursor.fetchall()]
            promoted = promote and prior_current_ids != [lock_id]
            if promoted:
                cursor.execute(
                    "UPDATE market_universe_lock SET is_current = 0 "
                    "WHERE is_current = 1 AND id <> %s",
                    (lock_id,),
                )
                cursor.execute(
                    "UPDATE market_universe_lock SET is_current = 1 WHERE id = %s",
                    (lock_id,),
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT RELEASE_LOCK(%s)", (ADVISORY_LOCK,))

    return {
        "lockId": lock_id,
        "lockSha256": lock_hash,
        "members": len(expected_rows),
        "created": created,
        "replayed": replayed,
        "promoted": bool(promote and prior_current_ids != [lock_id]),
        "priorCurrentLockIds": prior_current_ids,
    }


def promote_candidate(
    connection: Connection,
    document: Mapping[str, Any],
    *,
    active_path: Path = DEFAULT_ACTIVE,
    archive_root: Path | None = None,
) -> dict[str, Any]:
    """Archive active evidence, promote the DB, then replace the active consumer."""

    _validate_authority_candidate(document)
    archive = archive_active_evidence(active_path, archive_root)
    database = materialize_candidate(connection, document, promote=True)
    active = atomic_replace_active(document, active_path)
    return {
        **database,
        "archivedActive": archive,
        "activeUniverse": active,
    }


def active_evidence_integrity(
    active_path: Path | None,
    expected_hash: str | None,
) -> tuple[str | None, str | None, list[str]]:
    if active_path is None:
        return None, None, []
    try:
        active_document = read_candidate(active_path)
        active_hash = active_universe_lock_hash(active_document)
        blockers = (
            []
            if expected_hash is None or active_hash == expected_hash
            else ["active_universe_hash_mismatch"]
        )
        return active_hash, None, blockers
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        return None, error.__class__.__name__, ["active_universe_invalid"]


def universe_integrity(
    connection: Connection,
    *,
    active_path: Path | None = DEFAULT_ACTIVE,
) -> tuple[dict[str, Any], list[str]]:
    qualified, monitoring, discovery_evidence = _select_candidate_members(
        connection
    )
    candidate = (
        _candidate_document(qualified, monitoring, discovery_evidence)
        if qualified
        else None
    )
    expected_hash = (
        active_universe_lock_hash(candidate) if candidate is not None else None
    )
    expected_count = len(qualified) + len(monitoring)
    blockers: list[str] = []
    if candidate is None:
        blockers.append("universe_candidate_no_qualified_printings")
    with connection.cursor() as cursor:
        _assert_canonical_database(cursor)
        cursor.execute(
            """
            SELECT id, lock_sha256, effective_at, policy_json, member_count, is_current
            FROM market_universe_lock
            WHERE is_current = 1
            ORDER BY id DESC
            LIMIT 1
            """
        )
        current = cursor.fetchone()
        if current is None:
            current_hash = None
            current_id = None
            stored_members = 0
            stored_variants = 0
            blockers.append("current_universe_lock_missing")
        else:
            current_id = int(current["id"])
            current_hash = str(current["lock_sha256"])
            cursor.execute(
                """
                SELECT COUNT(*) AS members, COUNT(DISTINCT variant_id) AS variants
                FROM market_universe_member
                WHERE universe_lock_id = %s
                """,
                (current_id,),
            )
            counts = cursor.fetchone() or {}
            stored_members = int(counts.get("members") or 0)
            stored_variants = int(counts.get("variants") or 0)
            if (
                int(current["member_count"]) != stored_members
                or stored_members != stored_variants
            ):
                blockers.append("current_universe_member_count_mismatch")
            if expected_hash is not None and current_hash != expected_hash:
                blockers.append("current_universe_hash_mismatch")
            elif candidate is not None:
                expected_rows = _expected_member_rows(cursor, candidate)
                if (
                    not _lock_metadata_matches(current, candidate, len(expected_rows))
                    or _database_member_rows(cursor, current_id) != expected_rows
                ):
                    blockers.append("current_universe_content_mismatch")

    active_hash, active_error, active_blockers = active_evidence_integrity(
        active_path, expected_hash
    )
    blockers.extend(active_blockers)

    report = {
        "status": "valid" if not blockers else "blocked",
        "database": CANONICAL_DATABASE,
        "candidateHash": expected_hash,
        "currentHash": current_hash,
        "matchesCandidate": (
            expected_hash is not None and current_hash == expected_hash
        ),
        "currentLockId": current_id,
        "candidateQualified": len(qualified),
        "candidateMonitoring": len(monitoring),
        "candidateDiscoveryEvidence": discovery_evidence,
        "candidateMembers": expected_count,
        "currentDeclaredMembers": (
            int(current["member_count"]) if current is not None else 0
        ),
        "currentStoredMembers": stored_members,
        "currentDistinctVariants": stored_variants,
        "activeFileHash": active_hash,
        "activeFileError": active_error,
    }
    return report, blockers


def qc_pending(connection: Connection) -> tuple[dict[str, Any], dict[str, Any]]:
    with connection.cursor() as cursor:
        _assert_canonical_database(cursor)
        cursor.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM market_image_qc WHERE public_allowed = 1)
                AS image_passed,
              (SELECT COUNT(*) FROM market_image_qc WHERE public_allowed = 0)
                AS image_rejected,
              (SELECT COUNT(*) FROM market_ingest_run
                 WHERE status IN ('failed', 'error'))
                AS failed_ingest_runs,
              (SELECT COUNT(*) FROM market_identity_review_queue
                 WHERE status = 'pending')
                AS identity_reviews,
              (SELECT COUNT(*) FROM market_ingest_run
                 WHERE status IN ('queued', 'running'))
                AS ingest_runs,
              (SELECT COUNT(*) FROM market_alert
                 WHERE status IN ('observing', 'open', 'acknowledged'))
                AS open_alerts
            """
        )
        row = cursor.fetchone() or {}
    qc = {
        "status": "available",
        "imageChecksPassed": int(row.get("image_passed") or 0),
        "imageChecksRejected": int(row.get("image_rejected") or 0),
        "failedIngestRuns": int(row.get("failed_ingest_runs") or 0),
    }
    pending = {
        "status": "available",
        "identityReviews": int(row.get("identity_reviews") or 0),
        "ingestRuns": int(row.get("ingest_runs") or 0),
        "openAlerts": int(row.get("open_alerts") or 0),
    }
    return qc, pending


def latest_canonical_db_qc_status(
    root: Path = DEFAULT_CANONICAL_DB_QC_ROOT,
    *,
    reference_now: datetime | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Verify and summarize the newest immutable full-DB QC receipt."""

    empty = {
        "fullDbQcStatus": "unavailable",
        "fullDbQcRunId": None,
        "fullDbQcAsOf": None,
        "fullDbQcAgeHours": None,
        "fullDbQcQualified": 0,
        "fullDbQcReleaseReady": 0,
        "fullDbQcReleaseBlocked": 0,
        "fullDbQcReportSha256": None,
    }
    try:
        receipts = list(root.resolve().glob("*/receipt.json"))
        if not receipts:
            return empty, ["canonical_db_qc_receipt_missing"]
        receipt_path = max(
            receipts,
            key=lambda path: (path.stat().st_mtime_ns, path.as_posix()),
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        report_path = receipt_path.with_name("report.json")
        report_bytes = report_path.read_bytes()
        report = json.loads(report_bytes)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {
            **empty,
            "fullDbQcStatus": "invalid",
        }, ["canonical_db_qc_evidence_invalid"]

    if not isinstance(receipt, Mapping) or not isinstance(report, Mapping):
        return {
            **empty,
            "fullDbQcStatus": "invalid",
        }, ["canonical_db_qc_evidence_invalid"]

    run_id = receipt.get("runId")
    as_of = receipt.get("asOf")
    status = receipt.get("status")
    counts = receipt.get("counts")
    gate = receipt.get("releaseGate")
    report_counts = report.get("counts")
    declared_hash = receipt.get("reportSha256")
    computed_hash = hashlib.sha256(report_bytes).hexdigest()
    valid = (
        isinstance(run_id, str)
        and run_id == receipt_path.parent.name
        and receipt.get("readOnly") is True
        and receipt.get("database") == CANONICAL_DATABASE
        and isinstance(as_of, str)
        and _parse_utc_timestamp(as_of) is not None
        and status in {"passed", "blocked"}
        and isinstance(counts, Mapping)
        and isinstance(report_counts, Mapping)
        and isinstance(gate, Mapping)
        and isinstance(declared_hash, str)
        and SHA256_HEX.fullmatch(declared_hash) is not None
        and declared_hash == computed_hash
        and report.get("runId") == run_id
        and report.get("asOf") == as_of
        and report.get("status") == status
        and all(report_counts.get(key) == value for key, value in counts.items())
        and report.get("releaseGate") == gate
    )
    if not valid:
        return {
            **empty,
            "fullDbQcStatus": "invalid",
            "fullDbQcRunId": run_id if isinstance(run_id, str) else None,
        }, ["canonical_db_qc_evidence_invalid"]

    qualified = counts.get("qualified")
    release_ready = counts.get("releaseReadyQualified")
    release_blocked = counts.get("releaseBlockedQualified")
    if not all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in (qualified, release_ready, release_blocked)
    ):
        return {
            **empty,
            "fullDbQcStatus": "invalid",
            "fullDbQcRunId": run_id,
        }, ["canonical_db_qc_evidence_invalid"]

    now = (reference_now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    observed_at = _parse_utc_timestamp(as_of)
    assert observed_at is not None
    age_hours = round((now - observed_at).total_seconds() / 3600, 3)
    blockers: list[str] = []
    if status != "passed" or gate.get("eligible") is not True:
        blockers.append("canonical_db_qc_failed")
    if age_hours < 0:
        blockers.append("canonical_db_qc_future_dated")
    elif age_hours > 24:
        blockers.append("canonical_db_qc_stale")
    return {
        "fullDbQcStatus": status,
        "fullDbQcRunId": run_id,
        "fullDbQcAsOf": as_of,
        "fullDbQcAgeHours": age_hours,
        "fullDbQcQualified": qualified,
        "fullDbQcReleaseReady": release_ready,
        "fullDbQcReleaseBlocked": release_blocked,
        "fullDbQcReportSha256": declared_hash,
    }, blockers


def _parse_utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _public_qc_receipt_valid(
    snapshot: Mapping[str, Any],
    receipt: Any,
    pointer: Mapping[str, Any],
) -> bool:
    if not isinstance(receipt, Mapping):
        return False
    generation = snapshot.get("generation")
    top100 = snapshot.get("top100")
    watchlist = snapshot.get("watchlist")
    coverage = snapshot.get("coverage")
    if (
        not isinstance(generation, Mapping)
        or not isinstance(top100, list)
        or not isinstance(watchlist, list)
        or not isinstance(coverage, Mapping)
    ):
        return False
    expected_cards = [*top100, *watchlist]
    expected_claim = "verified-top-100" if len(top100) == 100 else "verified-top-n"
    cards = receipt.get("cards")
    if (
        type(receipt.get("schemaVersion")) is not int
        or receipt.get("schemaVersion") != 1
        or receipt.get("generationId") != generation.get("id")
        or _parse_utc_timestamp(receipt.get("checkedAt")) is None
        or receipt.get("status") != "passed"
        or receipt.get("claim") != expected_claim
        or receipt.get("claim") != coverage.get("claim")
        or receipt.get("requestedCount") != 100
        or receipt.get("requestedCount") != coverage.get("requestedCount")
        or receipt.get("verifiedCount") != len(top100)
        or receipt.get("verifiedCount") != coverage.get("verifiedCount")
        or receipt.get("blockers") != []
        or not isinstance(cards, list)
        or len(cards) != len(expected_cards)
    ):
        return False
    for card, decision in zip(expected_cards, cards, strict=True):
        if not isinstance(card, Mapping) or not isinstance(decision, Mapping):
            return False
        image = card.get("image")
        image_hash = image.get("sha256") if isinstance(image, Mapping) else None
        evidence_hash = decision.get("evidenceSha256")
        if (
            decision.get("id") != card.get("id")
            or not isinstance(image_hash, str)
            or not SHA256_HEX.fullmatch(image_hash)
            or decision.get("imageSha256") != image_hash
            or decision.get("decision") != "passed"
            or not isinstance(evidence_hash, str)
            or not SHA256_HEX.fullmatch(evidence_hash)
        ):
            return False
    production = (
        generation.get("mode") == "production"
        and generation.get("productionEligible") is True
    )
    if not production:
        return True

    db_qc = generation.get("dbQc")
    receipt_snapshot_payload = {
        **snapshot,
        "generation": {
            **generation,
            "contentSha256": "",
            "qcReceiptSha256": "",
        },
    }
    expected_snapshot_hash = hashlib.sha256(
        canonical_json(receipt_snapshot_payload)
    ).hexdigest()
    if (
        not isinstance(db_qc, Mapping)
        or not isinstance(db_qc.get("runId"), str)
        or not RUN_ID.fullmatch(db_qc["runId"])
        or db_qc.get("database") != CANONICAL_DATABASE
        or not isinstance(db_qc.get("receiptSha256"), str)
        or not SHA256_HEX.fullmatch(db_qc["receiptSha256"])
        or not isinstance(db_qc.get("universeCandidateSha256"), str)
        or not SHA256_HEX.fullmatch(db_qc["universeCandidateSha256"])
        or receipt.get("runId") != db_qc.get("runId")
        or receipt.get("dbQc") != db_qc
        or receipt.get("dbQcReceiptSha256") != db_qc.get("receiptSha256")
        or receipt.get("universeCandidateSha256")
        != db_qc.get("universeCandidateSha256")
        or receipt.get("snapshotContentSha256") != expected_snapshot_hash
        or pointer.get("dbQc") != db_qc
    ):
        return False

    expected_assets: dict[str, dict[str, Any]] = {}
    for card, decision in zip(expected_cards, cards, strict=True):
        assert isinstance(card, Mapping) and isinstance(decision, Mapping)
        image = card["image"]
        assert isinstance(image, Mapping)
        base_hash = str(image["sha256"])
        card_media = decision.get("media")
        if not isinstance(card_media, Mapping):
            return False
        for variant, (suffix, width, height) in PUBLIC_MEDIA_SPECS.items():
            if variant == "base":
                width = image.get("width")
                height = image.get("height")
                if (
                    not isinstance(width, int)
                    or isinstance(width, bool)
                    or width <= 0
                    or not isinstance(height, int)
                    or isinstance(height, bool)
                    or height <= 0
                ):
                    return False
            entry = card_media.get(variant)
            expected_key = f"market-assets/{base_hash}{suffix}.webp"
            if (
                not isinstance(entry, Mapping)
                or entry.get("key") != expected_key
                or not isinstance(entry.get("sha256"), str)
                or not SHA256_HEX.fullmatch(entry["sha256"])
                or entry.get("width") != width
                or entry.get("height") != height
                or (variant == "base" and entry.get("sha256") != base_hash)
            ):
                return False
            expected_assets[expected_key] = {
                "key": expected_key,
                "sha256": entry["sha256"],
                "width": width,
                "height": height,
                "variant": variant,
                "baseSha256": base_hash,
            }

    receipt_media = receipt.get("media")
    receipt_assets = (
        receipt_media.get("assets")
        if isinstance(receipt_media, Mapping)
        else None
    )
    if (
        not isinstance(receipt_media, Mapping)
        or receipt_media.get("prefix") != "market-assets/"
        or not isinstance(receipt_assets, list)
        or len(receipt_assets) != len(expected_assets)
    ):
        return False
    actual_assets: dict[str, Mapping[str, Any]] = {}
    for entry in receipt_assets:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("key"), str):
            return False
        actual_assets[entry["key"]] = entry
    if len(actual_assets) != len(receipt_assets):
        return False
    for key, expected in expected_assets.items():
        actual = actual_assets.get(key)
        if actual is None or any(
            actual.get(field) != value for field, value in expected.items()
        ):
            return False

    pointer_media = pointer.get("media")
    pointer_assets = (
        pointer_media.get("assets")
        if isinstance(pointer_media, Mapping)
        else None
    )
    if (
        not isinstance(pointer_assets, list)
        or len(pointer_assets) != len(expected_assets)
    ):
        return False
    for entry in pointer_assets:
        if not isinstance(entry, Mapping):
            return False
        receipt_entry = actual_assets.get(str(entry.get("key") or ""))
        if receipt_entry is None or any(
            entry.get(field) != receipt_entry.get(field)
            for field in ("key", "sha256", "width", "height")
        ):
            return False
    return True


def _pointer_media_contract(
    pointer: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    blockers: list[str] = []
    media = pointer.get("media")
    report = {
        "prefix": None,
        "hashCount": 0,
        "assetCount": 0,
        "remoteVerified": False,
        "remoteScope": None,
    }
    if not isinstance(media, Mapping):
        return report, ["generation_media_manifest_missing"]

    prefix = media.get("prefix")
    hashes = media.get("hashes")
    assets = media.get("assets")
    remote_verified = media.get("remoteVerified") is True
    remote_scope = media.get("remoteScope")
    report.update(
        {
            "prefix": prefix,
            "hashCount": len(hashes) if isinstance(hashes, list) else 0,
            "assetCount": len(assets) if isinstance(assets, list) else 0,
            "remoteVerified": remote_verified,
            "remoteScope": remote_scope,
        }
    )
    if prefix != "market-assets/":
        blockers.append("generation_media_prefix_invalid")

    hashes_valid = (
        isinstance(hashes, list)
        and 0 < len(hashes) <= 500
        and all(
            isinstance(value, str) and SHA256_HEX.fullmatch(value)
            for value in hashes
        )
        and len(set(hashes)) == len(hashes)
    )
    if not hashes_valid:
        blockers.append("generation_media_hashes_invalid")

    assets_valid = isinstance(assets, list) and len(assets) <= 1500
    asset_keys: list[str] = []
    if assets_valid:
        for asset in assets:
            if not isinstance(asset, Mapping):
                assets_valid = False
                break
            key = asset.get("key")
            content_hash = asset.get("sha256")
            match = MEDIA_ASSET_KEY.fullmatch(str(key or ""))
            if (
                match is None
                or not isinstance(content_hash, str)
                or not SHA256_HEX.fullmatch(content_hash)
                or (match.group(2) is None and content_hash != match.group(1))
            ):
                assets_valid = False
                break
            asset_keys.append(str(key))
    if hashes_valid and assets_valid:
        expected_keys = {
            f"market-assets/{base_hash}{suffix}.webp"
            for base_hash in hashes
            for suffix in ("", "_200", "_600")
        }
        assets_valid = (
            len(asset_keys) == len(set(asset_keys))
            and set(asset_keys) == expected_keys
        )
    else:
        assets_valid = False
    if not assets_valid:
        blockers.append("generation_media_assets_invalid")

    if not remote_verified:
        blockers.append("generation_media_remote_unverified")
    elif not (
        isinstance(remote_scope, str)
        and re.fullmatch(r"[0-9a-f]{16}", remote_scope)
    ):
        blockers.append("generation_media_remote_scope_invalid")
    return report, blockers


def generation_status(
    publish_root: Path = DEFAULT_PUBLISH_ROOT,
    *,
    reference_now: datetime | None = None,
) -> tuple[dict[str, Any], list[str]]:
    blockers: list[str] = []
    pointer_path = publish_root / "latest.json"
    report: dict[str, Any] = {
        "status": "missing",
        "pointerPresent": pointer_path.is_file(),
        "generationId": None,
        "mode": None,
        "productionEligible": False,
        "qcReceiptSha256": None,
        "generatedAt": None,
        "ageHours": None,
        "media": {
            "prefix": None,
            "hashCount": 0,
            "assetCount": 0,
            "remoteVerified": False,
            "remoteScope": None,
        },
    }
    if not pointer_path.is_file():
        return report, ["generation_pointer_missing"]
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        if not isinstance(pointer, Mapping):
            raise ValueError("pointer is not an object")
        generation_id = str(pointer.get("generationId") or "")
        snapshot_key = str(pointer.get("snapshotKey") or "")
        pointer_hash = str(pointer.get("sha256") or "")
        if not GENERATION_ID.fullmatch(generation_id) or not snapshot_key:
            raise ValueError("pointer contract is incomplete")
        if pointer.get("schemaVersion") != 1:
            blockers.append("generation_pointer_schema_invalid")
        expected_snapshot_key = f"generations/{generation_id}/snapshot.json"
        if snapshot_key != expected_snapshot_key:
            blockers.append("generation_snapshot_key_invalid")
        if not SHA256_HEX.fullmatch(pointer_hash):
            blockers.append("generation_pointer_hash_invalid")
        snapshot_path = (publish_root / Path(*snapshot_key.split("/"))).resolve()
        if publish_root.resolve() not in snapshot_path.parents:
            raise ValueError("pointer escapes publish root")
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if not isinstance(snapshot, Mapping):
            raise ValueError("snapshot is not an object")
        generation = snapshot.get("generation")
        if not isinstance(generation, Mapping):
            raise ValueError("snapshot generation is missing")
        internal_id = str(generation.get("id") or "")
        content_hash = str(generation.get("contentSha256") or "")
        hash_input = {
            **snapshot,
            "generation": {**generation, "contentSha256": ""},
        }
        computed_hash = hashlib.sha256(canonical_json(hash_input)).hexdigest()
        generation_blockers = [
            str(item) for item in generation.get("blockers", []) if str(item)
        ]
        if generation_id != internal_id:
            blockers.append("generation_id_mismatch")
        if SHA256_HEX.fullmatch(pointer_hash) and pointer_hash != content_hash:
            blockers.append("generation_hash_mismatch")
        if computed_hash != content_hash:
            blockers.append("generation_content_hash_mismatch")
        if generation.get("mode") != "production":
            blockers.append("generation_not_production")
        if generation.get("productionEligible") is not True:
            blockers.append("generation_not_eligible")
        if generation_blockers:
            blockers.append("generation_declares_blockers")
        qc_receipt = generation.get("qcReceiptSha256")
        if generation.get("productionEligible") is True and not (
            isinstance(qc_receipt, str) and SHA256_HEX.fullmatch(qc_receipt)
        ):
            blockers.append("generation_qc_receipt_missing")

        generated_at = generation.get("generatedAt")
        generated_datetime = _parse_utc_timestamp(generated_at)
        age_hours: float | None = None
        if generated_datetime is None:
            blockers.append("generation_generated_at_invalid")
        else:
            now = reference_now or datetime.now(timezone.utc)
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            age_hours = round(
                (
                    now.astimezone(timezone.utc) - generated_datetime
                ).total_seconds()
                / 3600,
                6,
            )
            if age_hours < 0:
                blockers.append("generation_in_future")
            elif age_hours > MAX_GENERATION_AGE_HOURS:
                blockers.append("generation_stale")
        pointer_generated_at = pointer.get("generatedAt")
        if (
            pointer_generated_at is not None
            and pointer_generated_at != generated_at
        ):
            blockers.append("generation_pointer_generated_at_mismatch")

        expected_receipt_key = (
            f"generations/{generation_id}/qc-receipt.json"
        )
        receipt_key = pointer.get("qcReceiptKey")
        pointer_receipt_hash = pointer.get("qcReceiptSha256")
        receipt_file_hash: str | None = None
        receipt_document: Any = None
        if receipt_key != expected_receipt_key:
            blockers.append("generation_qc_receipt_key_invalid")
        else:
            receipt_path = (
                publish_root / Path(*expected_receipt_key.split("/"))
            ).resolve()
            try:
                receipt_bytes = receipt_path.read_bytes()
                receipt_file_hash = hashlib.sha256(receipt_bytes).hexdigest()
                receipt_document = json.loads(receipt_bytes)
            except OSError:
                blockers.append("generation_qc_receipt_file_missing")
            except (UnicodeDecodeError, json.JSONDecodeError):
                blockers.append("generation_qc_receipt_invalid")
        if (
            receipt_document is not None
            and not _public_qc_receipt_valid(
                snapshot,
                receipt_document,
                pointer,
            )
        ):
            blockers.append("generation_qc_receipt_invalid")
        if not (
            isinstance(pointer_receipt_hash, str)
            and SHA256_HEX.fullmatch(pointer_receipt_hash)
        ):
            blockers.append("generation_pointer_qc_receipt_hash_invalid")
        else:
            if receipt_file_hash is not None and (
                receipt_file_hash != pointer_receipt_hash
            ):
                blockers.append("generation_qc_receipt_file_hash_mismatch")
            if qc_receipt != pointer_receipt_hash:
                blockers.append("generation_qc_receipt_hash_mismatch")

        media, media_blockers = _pointer_media_contract(pointer)
        blockers.extend(media_blockers)
        report = {
            "status": "valid" if not blockers else "blocked",
            "pointerPresent": True,
            "pointerGenerationId": generation_id,
            "generationId": internal_id or None,
            "mode": generation.get("mode"),
            "productionEligible": generation.get("productionEligible") is True,
            "generatedAt": generated_at,
            "ageHours": age_hours,
            "effectiveAt": generation.get("effectiveAt"),
            "contentSha256": content_hash or None,
            "computedContentSha256": computed_hash,
            "pointerSha256": pointer_hash,
            "qcReceiptSha256": qc_receipt,
            "qcReceiptKey": receipt_key,
            "pointerQcReceiptSha256": pointer_receipt_hash,
            "qcReceiptFileSha256": receipt_file_hash,
            "media": media,
            "declaredBlockers": generation_blockers,
        }
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        report["status"] = "invalid"
        blockers.append("generation_pointer_invalid")
    return report, list(dict.fromkeys(blockers))


def machine_status(
    connection: Connection,
    *,
    active_path: Path | None = DEFAULT_ACTIVE,
    publish_root: Path = DEFAULT_PUBLISH_ROOT,
) -> dict[str, Any]:
    integrity, integrity_blockers = universe_integrity(
        connection, active_path=active_path
    )
    qc, pending = qc_pending(connection)
    full_db_qc, full_db_qc_blockers = latest_canonical_db_qc_status()
    qc.update(full_db_qc)
    generation, generation_blockers = generation_status(publish_root)
    blockers = list(
        dict.fromkeys(
            [
                *integrity_blockers,
                *full_db_qc_blockers,
                *generation_blockers,
            ]
        )
    )
    return {
        "database": {
            "authority": "canonical_mysql",
            "name": CANONICAL_DATABASE,
            "connected": True,
        },
        "universeIntegrity": integrity,
        "qc": qc,
        "pending": pending,
        "generation": generation,
        "releaseGate": {
            "eligible": not blockers,
            "blockers": blockers,
        },
    }


def connect_from_values(values: Mapping[str, str], *, read_only: bool) -> Connection:
    database = str(values.get("CARDZ_DB_NAME") or CANONICAL_DATABASE).strip()
    if database != CANONICAL_DATABASE:
        raise RuntimeError(
            f"universe authority refuses non-canonical database: {database}"
        )
    ssl_ca = str(values.get("CARDZ_DB_SSL_CA") or "").strip()
    return pymysql.connect(
        host=str(values.get("CARDZ_DB_HOST") or "127.0.0.1").strip(),
        port=int(str(values.get("CARDZ_DB_PORT") or "3308").strip()),
        user=str(values.get("CARDZ_DB_USER") or "cardz").strip(),
        password=str(values.get("CARDZ_DB_PASSWORD") or "").strip(),
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=read_only,
        connect_timeout=2,
        read_timeout=3,
        write_timeout=3,
        ssl={"ca": ssl_ca} if ssl_ca else None,
    )


def _environment_values(args: argparse.Namespace) -> dict[str, str]:
    values = {
        "CARDZ_DB_HOST": args.host,
        "CARDZ_DB_PORT": str(args.port),
        "CARDZ_DB_NAME": args.database,
        "CARDZ_DB_USER": args.user,
        "CARDZ_DB_PASSWORD": os.environ.get("CARDZ_DB_PASSWORD", ""),
        "CARDZ_DB_SSL_CA": os.environ.get("CARDZ_DB_SSL_CA", ""),
    }
    if not values["CARDZ_DB_PASSWORD"]:
        raise RuntimeError("CARDZ_DB_PASSWORD is required")
    return values


def _add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default=os.environ.get("CARDZ_DB_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("CARDZ_DB_PORT", "3308"))
    )
    parser.add_argument(
        "--database", default=os.environ.get("CARDZ_DB_NAME", CANONICAL_DATABASE)
    )
    parser.add_argument("--user", default=os.environ.get("CARDZ_DB_USER", "cardz"))


def main() -> int:
    parser = argparse.ArgumentParser(description="CARDZ canonical universe authority")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-candidate")
    _add_connection_args(build)
    build.add_argument("--output-root", type=Path, default=DEFAULT_CANDIDATE_ROOT)
    for command in ("materialize", "promote"):
        action = sub.add_parser(command)
        _add_connection_args(action)
        action.add_argument("--candidate", type=Path, required=True)
        if command == "promote":
            action.add_argument(
                "--active-universe",
                type=Path,
                default=DEFAULT_ACTIVE,
            )
    status = sub.add_parser("status")
    _add_connection_args(status)
    status.add_argument("--active-universe", type=Path, default=DEFAULT_ACTIVE)
    status.add_argument("--publish-root", type=Path, default=DEFAULT_PUBLISH_ROOT)
    args = parser.parse_args()

    values = _environment_values(args)
    connection = connect_from_values(
        values, read_only=args.command in {"build-candidate", "status"}
    )
    try:
        if args.command == "build-candidate":
            report = write_immutable_candidate(
                build_candidate(connection), args.output_root
            )
        elif args.command == "materialize":
            report = materialize_candidate(
                connection,
                read_candidate(args.candidate.resolve()),
                promote=False,
            )
        elif args.command == "promote":
            report = promote_candidate(
                connection,
                read_candidate(args.candidate.resolve()),
                active_path=args.active_universe.resolve(),
            )
        else:
            report = machine_status(
                connection,
                active_path=args.active_universe.resolve(),
                publish_root=args.publish_root.resolve(),
            )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
