#!/usr/bin/env python3
"""Standalone MySQL migration and complete-ranking replay tool.

The immutable private batches remain disaster-recovery evidence. This tool is
the only supported path for materializing them into the operational database.
It never imports an observation whose canonical identity is outside the
current validated collection lock.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import pymysql
from pymysql.connections import Connection


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MIGRATIONS = ROOT / "pipelines" / "migrations"
DEFAULT_ACTIVE = ROOT / "data" / "runtime" / "private-source-map" / "tracked-universe.json"
DEFAULT_LANDING = ROOT / "data" / "runtime" / "private-landing"
DEFAULT_FX = ROOT / "data" / "runtime" / "private-fx" / "latest.json"
DEFAULT_GEMRATE_RECEIPT_MAPPINGS = (
    ROOT / "data" / "runtime" / "private-source-map" / "gemrate-receipt-mappings.json"
)
GRADERS = {"PSA", "BGS", "CGC", "SGC", "TAG"}
SUPPORTED_CARD_LANGUAGES = {"en", "ja", "ko", "zhCN", "zhTW"}
GEMRATE_ALIAS_TYPES = {"entity", "universal", "grader_member", "spec"}
GEMRATE_HEX_ID = re.compile(r"[0-9a-f]{40}")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_datetime(value: Any, fallback_date: str | None = None) -> datetime:
    raw = str(value or "").strip()
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            pass
    if fallback_date:
        return datetime.combine(datetime.fromisoformat(fallback_date).date(), time.min)
    raise ValueError(f"invalid observation timestamp: {value!r}")


def active_universe_lock_hash(document: Mapping[str, Any]) -> str:
    """Return the identity of the complete DB collection lock.

    Schema 5 separates the formal ranking payload from the daily pre-entry
    collection pool.  The formal ranking hash intentionally excludes the
    monitoring rows, so the database lock is the validated complete collection
    payload.  Older schemas retain their original lock identity for replay
    compatibility.
    """

    if document.get("schemaVersion") == "5.0.0":
        cards = document.get("cards")
        monitoring = document.get("monitoringCandidates")
        if not isinstance(cards, list) or not isinstance(monitoring, list):
            raise ValueError("schema5 active-universe collection is invalid")
        actual = sha256(canonical_json({"cards": cards, "monitoringCandidates": monitoring}))
        if document.get("collectionPayloadSha256") != actual:
            raise ValueError("schema5 active-universe collection payload hash mismatch")
        return actual
    return str(document["payloadSha256"])


def _required_card_identity(
    raw: Mapping[str, Any],
    *,
    label: str,
    require_confirmed: bool,
) -> tuple[str, str]:
    required = (
        "pokedexId", "canonicalSourceCode", "canonicalExternalId", "tcg", "language",
        "name", "setName", "collectorNumber",
    )
    if any(not raw.get(key) for key in required):
        raise ValueError(f"{label} is incomplete")
    if require_confirmed and raw.get("pokedexStatus") != "confirmed":
        raise ValueError(f"{label} identity is not confirmed")
    if str(raw.get("tcg")) not in {"pokemon", "one-piece"}:
        raise ValueError(f"{label} has an unsupported TCG")
    if str(raw.get("language")) not in SUPPORTED_CARD_LANGUAGES:
        raise ValueError(f"{label} has an unsupported language")
    return (
        str(raw["canonicalSourceCode"]).casefold(),
        str(raw["canonicalExternalId"]),
    )


def _is_exact_gemrate_population(raw: Mapping[str, Any], *, minimum: int, maximum: int | None = None) -> bool:
    population = raw.get("populationPsa10")
    state = str(raw.get("populationSourceState") or "").strip().lower()
    return (
        isinstance(population, int)
        and not isinstance(population, bool)
        and population >= minimum
        and (maximum is None or population <= maximum)
        and not bool(raw.get("populationEstimated"))
        and state not in {"estimate", "estimated", "unavailable", "stale"}
        and bool(str(raw.get("gemrateId") or "").strip())
    )


def _validate_complete_ranks(
    cards: list[Mapping[str, Any]],
    *,
    require_exact_population: bool,
) -> None:
    index_ranks: dict[str, list[int]] = {"tcg": [], "pokemon": [], "one-piece": []}
    seen_opaque: set[str] = set()
    seen_source: set[tuple[str, str]] = set()
    for index, raw in enumerate(cards, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"complete-ranking card {index} is invalid")
        _required_card_identity(
            raw,
            label=f"complete-ranking card {index}",
            require_confirmed=require_exact_population,
        )
        source_ref = (str(raw["canonicalSourceCode"]).casefold(), str(raw["canonicalExternalId"]))
        opaque_id = str(raw["pokedexId"])
        if source_ref in seen_source or opaque_id in seen_opaque:
            raise ValueError("complete-ranking universe contains duplicate canonical identity")
        if require_exact_population and not _is_exact_gemrate_population(raw, minimum=1000):
            raise ValueError("complete-ranking universe contains a card without exact GemRate POP >= 1000")
        memberships = raw.get("rankMemberships")
        if not isinstance(memberships, Mapping) or any(
            market not in index_ranks
            or not isinstance(rank, int)
            or isinstance(rank, bool)
            or rank < 1
            for market, rank in memberships.items()
        ):
            raise ValueError("complete-ranking rank membership is invalid")
        if "tcg" not in memberships or str(raw["tcg"]) not in memberships:
            raise ValueError("complete-ranking card is missing its canonical scope membership")
        seen_source.add(source_ref)
        seen_opaque.add(opaque_id)
        for market, rank in memberships.items():
            index_ranks[str(market)].append(int(rank))
    for market, ranks in index_ranks.items():
        if sorted(ranks) != list(range(1, len(ranks) + 1)):
            raise ValueError(f"complete-ranking {market} ranks are not contiguous")


def validate_active_universe(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    cards = document.get("cards")
    if not isinstance(cards, list) or not cards:
        raise ValueError("active-universe lock has no cards")
    expected = document.get("payloadSha256")
    actual = sha256(canonical_json(cards))
    if expected != actual:
        raise ValueError("active-universe payload hash mismatch")
    schema_version = document.get("schemaVersion")
    if schema_version in {"4.0.0", "5.0.0"}:
        policy = document.get("policy")
        if (
            not isinstance(policy, Mapping)
            or policy.get("indexes") != ["tcg", "pokemon", "one-piece"]
            or policy.get("canonicalMembership") != "complete_eligible"
            or policy.get("languagePartitioning") is not False
            or "limitPerIndex" in policy
        ):
            raise ValueError("complete-ranking universe policy is invalid")
        seen_opaque: set[str] = set()
        seen_source: set[tuple[str, str]] = set()
        _validate_complete_ranks(cards, require_exact_population=schema_version == "5.0.0")
        if schema_version == "4.0.0":
            return cards

        monitoring = document.get("monitoringCandidates")
        if not isinstance(monitoring, list):
            raise ValueError("pre-entry monitoring candidates are invalid")
        if document.get("monitoringPayloadSha256") != sha256(canonical_json(monitoring)):
            raise ValueError("pre-entry monitoring payload hash mismatch")
        population_range = policy.get("monitoringPopulationRangeInclusive")
        if not isinstance(population_range, Mapping) or population_range.get("minimum") != 971 or population_range.get("maximum") != 999:
            raise ValueError("pre-entry monitoring population policy is invalid")
        for index, raw in enumerate(cards, start=1):
            source_ref = (str(raw["canonicalSourceCode"]).casefold(), str(raw["canonicalExternalId"]))
            seen_source.add(source_ref)
            seen_opaque.add(str(raw["pokedexId"]))
        for index, raw in enumerate(monitoring, start=1):
            if not isinstance(raw, Mapping):
                raise ValueError(f"pre-entry monitoring candidate {index} is invalid")
            source_ref = _required_card_identity(
                raw,
                label=f"pre-entry monitoring candidate {index}",
                require_confirmed=True,
            )
            opaque_id = str(raw["pokedexId"])
            if source_ref in seen_source or opaque_id in seen_opaque:
                raise ValueError("pre-entry monitoring candidate duplicates a canonical identity")
            if not _is_exact_gemrate_population(raw, minimum=971, maximum=999):
                raise ValueError("pre-entry monitoring candidate does not have exact GemRate POP 971-999")
            if raw.get("monitoringState") != "pre_entry_population_971_999" or raw.get("collectionCadence") != "daily":
                raise ValueError("pre-entry monitoring candidate has an invalid collection policy")
            memberships = raw.get("rankMemberships")
            if memberships not in (None, {}):
                raise ValueError("pre-entry monitoring candidate must not have formal rank membership")
            seen_source.add(source_ref)
            seen_opaque.add(opaque_id)
        return [*cards, *monitoring]
    if schema_version in {"2.0.0", "3.0.0"}:
        policy = document.get("policy")
        limit = policy.get("limitPerIndex") if isinstance(policy, Mapping) else None
        if (
            not isinstance(policy, Mapping)
            or policy.get("indexes") != ["tcg", "pokemon", "one-piece"]
            or limit not in {300, 350}
            or policy.get("languagePartitioning") is not False
        ):
            raise ValueError("tracked-universe policy is invalid")
        if schema_version == "3.0.0" and (
            limit != 350
            or policy.get("publicLimitPerIndex") != 300
            or policy.get("reserveLimitPerIndex") != 50
        ):
            raise ValueError("tracked-universe v3 policy is invalid")
        if limit == 350 and (
            policy.get("publicLimitPerIndex") != 300
            or policy.get("reserveLimitPerIndex") != 50
        ):
            raise ValueError("tracked-universe public/reserve policy is invalid")
        seen_opaque: set[str] = set()
        seen_source: set[tuple[str, str]] = set()
        index_counts = Counter()
        for index, raw in enumerate(cards, start=1):
            if not isinstance(raw, Mapping):
                raise ValueError(f"tracked-universe card {index} is invalid")
            required = (
                "pokedexId", "canonicalSourceCode", "canonicalExternalId", "tcg", "language",
                "name", "setName", "collectorNumber", "rankMemberships",
            )
            if any(not raw.get(key) for key in required):
                raise ValueError(f"tracked-universe card {index} is incomplete")
            source_ref = (str(raw["canonicalSourceCode"]).casefold(), str(raw["canonicalExternalId"]))
            opaque_id = str(raw["pokedexId"])
            if source_ref in seen_source or opaque_id in seen_opaque:
                raise ValueError("tracked-universe contains duplicate canonical identity")
            memberships = raw["rankMemberships"]
            if not isinstance(memberships, Mapping) or any(
                market not in {"tcg", "pokemon", "one-piece"}
                or not isinstance(rank, int)
                or rank < 1
                or rank > limit
                for market, rank in memberships.items()
            ):
                raise ValueError("tracked-universe rank membership is invalid")
            seen_source.add(source_ref)
            seen_opaque.add(opaque_id)
            index_counts.update(memberships.keys())
        if any(count > limit for count in index_counts.values()):
            raise ValueError(f"tracked-universe index exceeds {limit}")
        return cards
    if schema_version != "1.0.0":
        raise ValueError("active-universe schema is unsupported")
    seen_opaque: set[str] = set()
    seen_source: set[tuple[str, str]] = set()
    segment_counts: dict[str, int] = {}
    role_counts: dict[str, dict[str, int]] = {}
    for index, raw in enumerate(cards, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"active-universe card {index} is invalid")
        required = (
            "pokedexId", "canonicalSourceCode", "canonicalExternalId", "tcg", "language",
            "segment", "name", "setName", "collectorNumber", "role", "selectionSignals",
        )
        if any(not raw.get(key) for key in required):
            raise ValueError(f"active-universe card {index} is incomplete")
        opaque_id = str(raw["pokedexId"])
        source_ref = (str(raw["canonicalSourceCode"]).casefold(), str(raw["canonicalExternalId"]))
        if opaque_id in seen_opaque or source_ref in seen_source:
            raise ValueError("active-universe contains duplicate canonical identity")
        seen_opaque.add(opaque_id)
        seen_source.add(source_ref)
        tcg = str(raw["tcg"])
        language = str(raw["language"])
        segment = str(raw["segment"])
        role = str(raw["role"])
        if language not in SUPPORTED_CARD_LANGUAGES or segment != f"{tcg}:{language}":
            raise ValueError("active-universe contains an invalid language segment")
        if role not in {"top100", "watchlist"}:
            raise ValueError("active-universe contains an invalid member role")
        segment_counts[segment] = segment_counts.get(segment, 0) + 1
        counts = role_counts.setdefault(segment, {"top100": 0, "watchlist": 0})
        counts[role] += 1
        if segment_counts[segment] > 300:
            raise ValueError(f"active-universe segment exceeds 300: {segment}")
        if counts["top100"] > 100 or counts["watchlist"] > 200:
            raise ValueError(f"active-universe segment role quota is invalid: {segment}")
    return cards


def split_sql(document: str) -> list[str]:
    lines = [line for line in document.splitlines() if not line.lstrip().startswith("--")]
    return [statement.strip() for statement in "\n".join(lines).split(";") if statement.strip()]


def migration_digest(path: Path) -> str:
    """Return the immutable digest used to reject edited applied migrations."""

    return sha256(path.read_bytes())


def ensure_migration_ledger(cursor: Any) -> None:
    """Create the ledger before replaying migrations, including migration 009."""

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS cardz_migration_ledger (
            migration_file VARCHAR(255) NOT NULL,
            content_sha256 CHAR(64) NOT NULL,
            applied_at DATETIME(6) NOT NULL,
            PRIMARY KEY (migration_file)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def connection_from_args(args: argparse.Namespace, *, database: bool = True) -> Connection:
    password = args.password or os.environ.get("CARDZ_DB_PASSWORD")
    if not password:
        raise RuntimeError("CARDZ_DB_PASSWORD is required")
    ssl_ca = os.environ.get("CARDZ_DB_SSL_CA")
    ssl_options: dict[str, Any] | None = None
    if ssl_ca:
        ca_path = Path(ssl_ca).expanduser().resolve()
        if not ca_path.is_file():
            raise RuntimeError(f"CARDZ_DB_SSL_CA does not exist: {ca_path}")
        ssl_options = {"ca": str(ca_path), "check_hostname": True}
    return pymysql.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=password,
        database=args.database if database else None,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
        read_timeout=120,
        write_timeout=120,
        ssl=ssl_options,
    )


def migrate(connection: Connection, migrations: Path) -> dict[str, int]:
    applied = 0
    skipped = 0
    statements = 0
    with connection.cursor() as cursor:
        ensure_migration_ledger(cursor)
        for path in sorted(migrations.glob("*.mysql.sql")):
            migration_file = path.name
            content_sha256 = migration_digest(path)
            cursor.execute(
                "SELECT content_sha256 FROM cardz_migration_ledger WHERE migration_file = %s",
                (migration_file,),
            )
            recorded = cursor.fetchone()
            if recorded:
                if str(recorded["content_sha256"]) != content_sha256:
                    raise RuntimeError(f"applied migration content changed: {migration_file}")
                skipped += 1
                continue
            for statement in split_sql(path.read_text(encoding="utf-8")):
                cursor.execute(statement)
                statements += 1
            cursor.execute(
                """
                INSERT INTO cardz_migration_ledger (migration_file, content_sha256, applied_at)
                VALUES (%s, %s, UTC_TIMESTAMP(6))
                """,
                (migration_file, content_sha256),
            )
            applied += 1
    connection.commit()
    return {"files": applied, "skipped": skipped, "statements": statements}


def _upsert_source_identity(
    cursor: Any,
    *,
    variant_id: int,
    source_code: str,
    external_id: str,
    opaque_id: str,
) -> None:
    """Persist an exact provider identity without ever moving it to another variant."""
    evidence = sha256(canonical_json({"source": source_code, "id": external_id, "opaque": opaque_id}))
    cursor.execute(
        """
        SELECT variant_id
        FROM catalog_source_identity
        WHERE source_code = %s AND external_entity_id = %s
        FOR UPDATE
        """,
        (source_code, external_id),
    )
    existing_source = cursor.fetchone()
    if existing_source is not None:
        if int(existing_source["variant_id"]) != variant_id:
            raise ValueError(f"source identity ownership changed: {source_code}:{external_id}")
        cursor.execute(
            "UPDATE catalog_source_identity SET match_status = 'exact', evidence_sha256 = %s WHERE source_code = %s AND external_entity_id = %s",
            (evidence, source_code, external_id),
        )
        return
    cursor.execute(
        """
        INSERT INTO catalog_source_identity
            (source_code, external_entity_id, variant_id, match_status, evidence_sha256)
        VALUES (%s, %s, %s, 'exact', %s)
        """,
        (source_code, external_id, variant_id, evidence),
    )


def _validated_gemrate_alias_rows(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    schema_version = document.get("schemaVersion", 1)
    if schema_version not in {1, 2}:
        raise ValueError("GemRate receipt mapping schema is unsupported")
    raw_aliases = document.get("gemrateAliases", [])
    if not isinstance(raw_aliases, list):
        raise ValueError("GemRate receipt mapping aliases are invalid")
    aliases: list[dict[str, Any]] = []
    semantic_owners: dict[tuple[str, str, str], str] = {}
    associations: set[tuple[str, str, str, str]] = set()
    for raw in raw_aliases:
        if not isinstance(raw, Mapping):
            raise ValueError("GemRate receipt mapping alias is invalid")
        if raw.get("status") != "exact_confirmed":
            continue
        alias_type = str(raw.get("aliasType") or "")
        alias_value = str(raw.get("aliasValue") or "")
        grader = (
            str(raw.get("grader") or "").upper()
            if alias_type in {"grader_member", "spec"}
            else ""
        )
        requested = str(raw.get("requestedGemrateId") or "").casefold()
        canonical_key = str(raw.get("canonicalPrintingKey") or "")
        receipt_hash = str(raw.get("receiptPayloadSha256") or "").casefold()
        source_pointer = str(raw.get("sourcePointer") or "")
        fetched_at = str(raw.get("fetchedAt") or "")
        if (
            alias_type not in GEMRATE_ALIAS_TYPES
            or not alias_value
            or not GEMRATE_HEX_ID.fullmatch(requested)
            or not SHA256_HEX.fullmatch(receipt_hash)
            or not canonical_key
            or (alias_type in {"entity", "universal", "grader_member"} and not GEMRATE_HEX_ID.fullmatch(alias_value.casefold()))
            or (alias_type in {"grader_member", "spec"} and grader not in GRADERS)
            or (alias_type not in {"grader_member", "spec"} and grader)
            or (alias_type == "entity" and alias_value.casefold() != requested)
        ):
            raise ValueError("GemRate receipt mapping alias is incomplete")
        pointer = Path(source_pointer)
        if source_pointer != "population.json" or pointer.is_absolute() or ".." in pointer.parts:
            raise ValueError("GemRate receipt mapping source pointer is invalid")
        observed_at = parse_datetime(fetched_at)
        semantic_key = (alias_type, alias_value, grader)
        previous_owner = semantic_owners.get(semantic_key)
        if previous_owner is not None and previous_owner != canonical_key:
            raise ValueError(f"GemRate alias ownership changed: {alias_type}:{alias_value}")
        semantic_owners[semantic_key] = canonical_key
        association = (*semantic_key, requested)
        if association in associations:
            raise ValueError("GemRate receipt mapping alias association is duplicated")
        associations.add(association)
        aliases.append({
            "providerCode": "gemrate",
            "aliasType": alias_type,
            "aliasValue": alias_value,
            "graderCode": grader,
            "requestedExternalEntityId": requested,
            "canonicalPrintingKey": canonical_key,
            "receiptPayloadSha256": receipt_hash,
            "sourcePointer": source_pointer,
            "observedAt": observed_at,
        })
    return aliases


def _upsert_provider_identity_alias(
    cursor: Any,
    *,
    variant_id: int,
    alias: Mapping[str, Any],
) -> str:
    semantic = (
        str(alias["providerCode"]),
        str(alias["aliasType"]),
        str(alias["aliasValue"]),
        str(alias["graderCode"]),
    )
    association = (*semantic, str(alias["requestedExternalEntityId"]))
    cursor.execute(
        """
        SELECT DISTINCT variant_id
        FROM catalog_provider_identity_alias
        WHERE provider_code=%s AND alias_type=%s AND alias_value=%s AND grader_code=%s
        FOR UPDATE
        """,
        semantic,
    )
    if any(int(row["variant_id"]) != variant_id for row in cursor.fetchall()):
        raise ValueError(
            f"provider alias ownership changed: {semantic[0]}:{semantic[1]}:{semantic[2]}"
        )
    cursor.execute(
        """
        SELECT variant_id
        FROM catalog_provider_identity_alias
        WHERE provider_code=%s AND alias_type=%s AND alias_value=%s
          AND grader_code=%s AND requested_external_entity_id=%s
        FOR UPDATE
        """,
        association,
    )
    existing = cursor.fetchone()
    if existing is not None:
        if int(existing["variant_id"]) != variant_id:
            raise ValueError(
                f"provider alias association changed: {semantic[0]}:{semantic[1]}:{semantic[2]}"
            )
        cursor.execute(
            """
            UPDATE catalog_provider_identity_alias
            SET match_status='exact', receipt_payload_sha256=%s,
                source_pointer=%s, observed_at=%s
            WHERE provider_code=%s AND alias_type=%s AND alias_value=%s
              AND grader_code=%s AND requested_external_entity_id=%s
            """,
            (
                alias["receiptPayloadSha256"],
                alias["sourcePointer"],
                alias["observedAt"],
                *association,
            ),
        )
        return "replayed"
    cursor.execute(
        """
        INSERT INTO catalog_provider_identity_alias
            (provider_code, alias_type, alias_value, grader_code,
             requested_external_entity_id, variant_id, match_status,
             receipt_payload_sha256, source_pointer, observed_at)
        VALUES (%s, %s, %s, %s, %s, %s, 'exact', %s, %s, %s)
        """,
        (
            *association,
            variant_id,
            alias["receiptPayloadSha256"],
            alias["sourcePointer"],
            alias["observedAt"],
        ),
    )
    return "inserted"


def import_gemrate_identity_aliases(
    connection: Connection,
    document: Mapping[str, Any],
    *,
    commit: bool = True,
) -> dict[str, int]:
    aliases = _validated_gemrate_alias_rows(document)
    result = {"inserted": 0, "replayed": 0, "unanchored": 0}
    with connection.cursor() as cursor:
        for alias in aliases:
            requested = str(alias["requestedExternalEntityId"])
            cursor.execute(
                """
                SELECT variant_id
                FROM catalog_source_identity
                WHERE source_code='gemrate' AND external_entity_id=%s
                FOR UPDATE
                """,
                (requested,),
            )
            anchor = cursor.fetchone()
            if anchor is None:
                result["unanchored"] += 1
                continue
            action = _upsert_provider_identity_alias(
                cursor,
                variant_id=int(anchor["variant_id"]),
                alias=alias,
            )
            result[action] += 1
    if commit:
        connection.commit()
    return result


def upsert_variant(cursor: Any, card: Mapping[str, Any]) -> int:
    opaque_id = str(card["pokedexId"])
    identity = (
        str(card["tcg"]),
        str(card["language"]),
        str(card["setName"]),
        str(card["collectorNumber"]),
    )
    cursor.execute(
        """
        SELECT id, tcg_code, card_language, set_name, collector_number
        FROM catalog_variant
        WHERE opaque_id = %s
        FOR UPDATE
        """,
        (opaque_id,),
    )
    existing_variant = cursor.fetchone()
    if existing_variant:
        existing_identity = (
            str(existing_variant["tcg_code"]),
            str(existing_variant["card_language"]),
            str(existing_variant["set_name"]),
            str(existing_variant["collector_number"]),
        )
        if existing_identity != identity:
            raise ValueError(f"opaque_id canonical identity changed: {opaque_id}")
        variant_id = int(existing_variant["id"])
        cursor.execute(
            "UPDATE catalog_variant SET canonical_name = %s, identity_status = 'confirmed' WHERE id = %s",
            (str(card["name"]), variant_id),
        )
    else:
        cursor.execute(
            """
            INSERT INTO catalog_variant
                (opaque_id, tcg_code, card_language, canonical_name, set_name, collector_number, identity_status)
            VALUES (%s, %s, %s, %s, %s, %s, 'confirmed')
            """,
            (opaque_id, *identity[:2], str(card["name"]), *identity[2:]),
        )
        variant_id = int(cursor.lastrowid)
    source_code = str(card["canonicalSourceCode"]).casefold()
    external_id = str(card["canonicalExternalId"])
    source_identities = [(source_code, external_id)]
    gemrate_id = str(card.get("gemrateId") or "").strip().casefold()
    if re.fullmatch(r"[0-9a-f]{40}", gemrate_id) and (source_code, external_id) != ("gemrate", gemrate_id):
        source_identities.append(("gemrate", gemrate_id))
    snk_item_id = card.get("snkItemId")
    if (
        isinstance(snk_item_id, int)
        and not isinstance(snk_item_id, bool)
        and snk_item_id > 0
        and (source_code, external_id) != ("snkrdunk", str(snk_item_id))
    ):
        source_identities.append(("snkrdunk", str(snk_item_id)))
    for identity_source, identity_external_id in source_identities:
        _upsert_source_identity(
            cursor,
            variant_id=variant_id,
            source_code=identity_source,
            external_id=identity_external_id,
            opaque_id=opaque_id,
        )
    return variant_id


def import_lock(
    connection: Connection,
    document: Mapping[str, Any],
    *,
    commit: bool = True,
) -> tuple[dict[tuple[str, str], int], int]:
    cards = validate_active_universe(document)
    lock_hash = active_universe_lock_hash(document)
    effective_at = parse_datetime(document.get("effectiveAt"))
    mapping: dict[tuple[str, str], int] = {}
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO market_universe_lock
                (lock_sha256, effective_at, policy_json, member_count, is_current)
            VALUES (%s, %s, %s, %s, 0)
            ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id), member_count = VALUES(member_count)
            """,
            (lock_hash, effective_at, canonical_json(document.get("policy") or {}).decode("utf-8"), len(cards)),
        )
        lock_id = int(cursor.lastrowid)
        if not lock_id:
            cursor.execute("SELECT id FROM market_universe_lock WHERE lock_sha256 = %s", (lock_hash,))
            lock_id = int(cursor.fetchone()["id"])
        for card in cards:
            variant_id = upsert_variant(cursor, card)
            source_ref = (str(card["canonicalSourceCode"]).casefold(), str(card["canonicalExternalId"]))
            mapping[source_ref] = variant_id
            memberships = card.get("rankMemberships")
            is_ranked_union = isinstance(memberships, Mapping) and bool(memberships)
            is_pre_entry = card.get("monitoringState") == "pre_entry_population_971_999"
            is_legacy_member = document.get("schemaVersion") == "1.0.0"
            if not is_ranked_union and not is_pre_entry and not is_legacy_member:
                raise RuntimeError("active-universe member has no recognized collection role")
            cursor.execute(
                """
                INSERT INTO market_universe_member
                    (universe_lock_id, variant_id, segment_code, member_role, market_rank,
                     watch_position, watch_score, selection_signals_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    segment_code = VALUES(segment_code), member_role = VALUES(member_role),
                    market_rank = VALUES(market_rank), watch_position = VALUES(watch_position),
                    watch_score = VALUES(watch_score), selection_signals_json = VALUES(selection_signals_json)
                """,
                (
                    lock_id,
                    variant_id,
                    "tracked" if is_ranked_union else ("pre-entry" if is_pre_entry else str(card["segment"])),
                    "candidate" if is_ranked_union else ("monitoring" if is_pre_entry else str(card["role"])),
                    memberships.get("tcg") if is_ranked_union else card.get("marketRank"),
                    None if is_ranked_union else card.get("watchPosition"),
                    None if is_ranked_union else card.get("watchScore"),
                    canonical_json(
                        {"rankMemberships": memberships}
                        if is_ranked_union
                        else ({
                            "monitoringState": card["monitoringState"],
                            "collectionCadence": card["collectionCadence"],
                            "reasons": card.get("reasons") or [],
                        } if is_pre_entry else card.get("selectionSignals") or [])
                    ).decode("utf-8"),
                ),
            )
    if commit:
        connection.commit()
    return mapping, lock_id


def promote_lock(connection: Connection, lock_id: int, expected_members: int, *, commit: bool = True) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS members, COUNT(DISTINCT variant_id) AS variants "
            "FROM market_universe_member WHERE universe_lock_id = %s",
            (lock_id,),
        )
        counts = cursor.fetchone()
        if int(counts["members"]) != expected_members or int(counts["variants"]) != expected_members:
            raise RuntimeError("active-universe lock did not materialize one distinct variant per member")
        cursor.execute("UPDATE market_universe_lock SET is_current = 0 WHERE is_current = 1 AND id <> %s", (lock_id,))
        cursor.execute("UPDATE market_universe_lock SET is_current = 1 WHERE id = %s", (lock_id,))
    if commit:
        connection.commit()


def iter_batches(landing_root: Path) -> Iterable[tuple[Path, Mapping[str, Any]]]:
    materialized: list[tuple[datetime, str, Path, Mapping[str, Any]]] = []
    for path in landing_root.rglob("canonical-batch.json"):
        document = read_json(path)
        if not isinstance(document, Mapping):
            continue
        observed = parse_datetime(document.get("fetchedAt") or document.get("effectiveAt"), "1970-01-01")
        materialized.append((observed, str(document.get("runId") or ""), path, document))
    for _, _, path, document in sorted(materialized, key=lambda row: (row[0], row[1], row[2].as_posix())):
        yield path, document


def import_batch(
    connection: Connection,
    path: Path,
    batch: Mapping[str, Any],
    variants: Mapping[tuple[str, str], int],
    universe_hash: str,
    *,
    commit: bool = True,
) -> dict[str, int | bool]:
    batch_hash = sha256(canonical_json(batch))
    run_key = sha256(canonical_json({"runId": batch.get("runId"), "batch": batch_hash, "universe": universe_hash}))
    observations = batch.get("observations")
    if not isinstance(observations, list):
        raise ValueError(f"canonical batch has no observations: {path}")
    active = [
        row for row in observations
        if isinstance(row, Mapping)
        and (str(row.get("sourceCode") or "").casefold(), str(row.get("externalEntityId") or "")) in variants
    ]
    with connection.cursor() as cursor:
        cursor.execute("SELECT id, status FROM market_ingest_run WHERE run_key = %s", (run_key,))
        existing = cursor.fetchone()
        if existing and existing["status"] == "complete":
            if commit:
                connection.rollback()
            return {"inserted": 0, "observed": len(active), "replayed": True}
        started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        effective_at = parse_datetime(batch.get("effectiveAt") or batch.get("fetchedAt"), "1970-01-01")
        if existing:
            run_id = int(existing["id"])
            cursor.execute(
                "UPDATE market_ingest_run SET status='running', started_at=%s, completed_at=NULL, error_summary=NULL WHERE id=%s",
                (started_at, run_id),
            )
        else:
            cursor.execute(
                """
                INSERT INTO market_ingest_run
                    (run_key, source_code, ingest_mode, effective_at, payload_sha256, manifest_sha256,
                     status, observed_count, started_at)
                VALUES (%s, 'cardz_normalized', %s, %s, %s, %s, 'running', %s, %s)
                """,
                (
                    run_key, str(batch.get("mode") or "incremental")[:16], effective_at,
                    str(batch.get("payloadSha256") or batch_hash)[:64], batch_hash, len(active), started_at,
                ),
            )
            run_id = int(cursor.lastrowid)
        inserted = 0
        for row in active:
            source_ref = (str(row["sourceCode"]).casefold(), str(row["externalEntityId"]))
            variant_id = variants[source_ref]
            observed_date = str(row.get("observedDate") or "")
            explicit_date = observed_date if re.fullmatch(r"\d{4}-\d{2}-\d{2}", observed_date) else None
            effective = parse_datetime(row.get("effectiveAt") or batch.get("effectiveAt"), explicit_date)
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", observed_date):
                observed_date = effective.date().isoformat()
            provider = str(row.get("providerCode") or row.get("sourceCode") or "private")[:32]
            kind = str(row.get("observationKind") or "")[:40]
            payload = row.get("payload")
            if not isinstance(payload, Mapping):
                raise ValueError(f"observation payload is invalid: {path}")
            payload_hash = str(row.get("payloadHash") or sha256(canonical_json(payload)))
            observed_at = parse_datetime(batch.get("fetchedAt") or batch.get("effectiveAt"), observed_date)
            external_key = f"{source_ref[0]}:{source_ref[1]}"
            cursor.execute(
                """
                INSERT IGNORE INTO market_source_observation
                    (run_id, source_code, external_entity_id, observation_kind, effective_at, observed_date,
                     payload_sha256, payload_json, observed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id, provider, external_key, kind, effective, observed_date, payload_hash,
                    canonical_json(payload).decode("utf-8"), observed_at,
                ),
            )
            inserted += int(cursor.rowcount > 0)
            # Keep the original row as replay evidence, but do not manufacture
            # a daily market point from a batch fetch timestamp. A corrected
            # dated batch must supply the canonical price/population history.
            if explicit_date is None:
                continue
            priority = int(row.get("sourcePriority") or 100)
            if kind == "index_constituent":
                price_usd = payload.get("priceUsd")
                native_price = payload.get("priceJpy")
                if not isinstance(price_usd, (int, float)) or price_usd <= 0:
                    continue
                cursor.execute(
                    """
                    INSERT INTO market_price_observation
                        (run_id, variant_id, source_code, observed_date, effective_at, price_usd,
                         native_price, native_currency, source_priority, metric_status, payload_sha256)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'ready', %s)
                    ON DUPLICATE KEY UPDATE
                        run_id=VALUES(run_id), effective_at=VALUES(effective_at), price_usd=VALUES(price_usd),
                        native_price=VALUES(native_price), native_currency=VALUES(native_currency),
                        source_priority=VALUES(source_priority), metric_status=VALUES(metric_status),
                        payload_sha256=VALUES(payload_sha256)
                    """,
                    (
                        run_id, variant_id, provider, observed_date, effective, float(price_usd),
                        float(native_price) if isinstance(native_price, (int, float)) else None,
                        "JPY" if isinstance(native_price, (int, float)) else None, priority, payload_hash,
                    ),
                )
            elif kind.startswith("grader_population_"):
                grader = str(payload.get("grader") or kind.rsplit("_", 1)[-1]).upper()
                top = payload.get("topGradePopulation")
                total = payload.get("totalPopulation", payload.get("total"))
                if grader not in GRADERS or not isinstance(top, int) or top < 0:
                    continue
                cursor.execute(
                    """
                    INSERT INTO market_grader_population_observation
                        (run_id, variant_id, source_code, external_entity_id, grader_code, top_grade_label,
                         total_population, top_grade_population, estimated, effective_at, observed_date, payload_sha256)
                    VALUES (%s, %s, %s, %s, %s, 'top', %s, %s, 0, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        run_id=VALUES(run_id), external_entity_id=VALUES(external_entity_id),
                        total_population=VALUES(total_population), top_grade_population=VALUES(top_grade_population),
                        estimated=VALUES(estimated), effective_at=VALUES(effective_at),
                        payload_sha256=VALUES(payload_sha256)
                    """,
                    (run_id, variant_id, provider, external_key, grader, total, top, effective, observed_date, payload_hash),
                )
                authority = str(payload.get("authority") or "gemrate")[:32]
                transport = str(row.get("transportCode") or payload.get("transport") or "legacy")[:48]
                cursor.execute(
                    """
                    INSERT INTO market_population_transport_observation
                        (run_id, variant_id, authority_code, transport_code, grader_code, grade_label,
                         population_value, effective_date, fetched_at, payload_sha256)
                    VALUES (%s, %s, %s, %s, %s, 'top', %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        run_id=VALUES(run_id), population_value=VALUES(population_value),
                        fetched_at=VALUES(fetched_at), payload_sha256=VALUES(payload_sha256)
                    """,
                    (run_id, variant_id, authority, transport, grader, top, observed_date, observed_at, payload_hash),
                )
            elif kind == "tracked_sales_daily":
                count = payload.get("salesCount")
                value_usd = payload.get("salesValueUsd")
                native_value = payload.get("salesValueJpy")
                if not isinstance(count, int) or count < 0:
                    continue
                cursor.execute(
                    """
                    INSERT INTO market_daily_sales_aggregate
                        (run_id, variant_id, source_code, observed_date, sales_count, sales_value_usd,
                         native_sales_value, native_currency, coverage_status, payload_sha256)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'partial', %s)
                    ON DUPLICATE KEY UPDATE
                        run_id=VALUES(run_id), sales_count=VALUES(sales_count),
                        sales_value_usd=VALUES(sales_value_usd), native_sales_value=VALUES(native_sales_value),
                        native_currency=VALUES(native_currency), coverage_status=VALUES(coverage_status),
                        payload_sha256=VALUES(payload_sha256)
                    """,
                    (
                        run_id, variant_id, provider, observed_date, count,
                        float(value_usd) if isinstance(value_usd, (int, float)) else None,
                        float(native_value) if isinstance(native_value, (int, float)) else None,
                        "JPY" if isinstance(native_value, (int, float)) else None, payload_hash,
                    ),
                )
            elif kind == "identity_candidate":
                required = ("tcg", "set", "collectorNumber", "language")
                if payload.get("identityStatus") != "candidate" or any(not payload.get(key) for key in required):
                    reason_hash = sha256(canonical_json({"reason": "incomplete_identity_candidate", "payload": payload}))
                    cursor.execute(
                        """
                        INSERT IGNORE INTO market_identity_review_queue
                            (run_id, source_code, external_entity_id, reason_code, evidence_sha256)
                        VALUES (%s, %s, %s, 'incomplete_identity_candidate', %s)
                        """,
                        (run_id, source_ref[0], source_ref[1], reason_hash),
                    )
                    continue
                printing_parts = [
                    str(payload.get("tcg") or "").strip().casefold(),
                    str(payload.get("set") or "").strip().casefold(),
                    str(payload.get("collectorNumber") or "").strip().casefold(),
                    str(payload.get("language") or "").strip(),
                    str(payload.get("edition") or "").strip().casefold(),
                    str(payload.get("parallel") or "").strip().casefold(),
                    str(payload.get("finish") or "").strip().casefold(),
                ]
                printing_hash = sha256("|".join(printing_parts).encode("utf-8"))
                cursor.execute(
                    """
                    INSERT INTO catalog_printing_identity
                        (variant_id, tcg_code, set_name, collector_number, card_language,
                         edition_code, parallel_code, finish_code, canonical_printing_sha256,
                         identity_status, evidence_sha256)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'candidate', %s)
                    ON DUPLICATE KEY UPDATE
                        tcg_code=VALUES(tcg_code), set_name=VALUES(set_name),
                        collector_number=VALUES(collector_number), card_language=VALUES(card_language),
                        edition_code=VALUES(edition_code), parallel_code=VALUES(parallel_code),
                        finish_code=VALUES(finish_code), identity_status=VALUES(identity_status),
                        evidence_sha256=VALUES(evidence_sha256)
                    """,
                    (
                        variant_id, printing_parts[0], str(payload["set"]), str(payload["collectorNumber"]),
                        str(payload["language"]), str(payload.get("edition") or ""),
                        str(payload.get("parallel") or ""), str(payload.get("finish") or ""),
                        printing_hash, payload_hash,
                    ),
                )
            elif kind == "story_pointer":
                locale = str(payload.get("locale") or "")
                source_path = str(payload.get("sourcePath") or "")
                version_hash = str(payload.get("sourceVersionSha256") or "")
                if locale and source_path and re.fullmatch(r"[0-9a-f]{64}", version_hash):
                    cursor.execute(
                        """
                        INSERT IGNORE INTO catalog_story_pointer
                            (variant_id, locale_code, source_path, source_version_sha256, observed_at)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (variant_id, locale[:8], source_path[:500], version_hash, observed_at),
                    )
            elif kind == "image_metadata":
                version_hash = str(payload.get("sourceVersionSha256") or "")
                remote_hash = str(payload.get("remoteUrlSha256") or "")
                if re.fullmatch(r"[0-9a-f]{64}", version_hash) and re.fullmatch(r"[0-9a-f]{64}", remote_hash):
                    cursor.execute(
                        """
                        INSERT IGNORE INTO market_image_source_pointer
                            (variant_id, image_kind, remote_url_sha256, source_path,
                             source_version_sha256, public_allowed, observed_at)
                        VALUES (%s, %s, %s, %s, %s, 0, %s)
                        """,
                        (
                            variant_id, str(payload.get("imageKind") or "unverified_remote")[:24],
                            remote_hash, str(payload.get("sourcePath") or "")[:500],
                            version_hash, observed_at,
                        ),
                    )
            elif kind == "sale_observation_psa10":
                fingerprint = str(payload.get("transactionFingerprint") or "")
                unit_price = payload.get("unitPriceUsd")
                quantity = payload.get("quantity")
                transaction_value = payload.get("transactionValueUsd")
                if (
                    re.fullmatch(r"[0-9a-f]{64}", fingerprint)
                    and isinstance(unit_price, (int, float)) and unit_price > 0
                    and isinstance(quantity, int) and quantity > 0
                    and isinstance(transaction_value, (int, float)) and transaction_value > 0
                ):
                    cursor.execute(
                        """
                        INSERT IGNORE INTO market_sale_observation
                            (run_id, variant_id, source_code, external_entity_id, transaction_fingerprint,
                             grader_code, grade_label, sold_at, source_date_text, fetched_at,
                             timestamp_quality, unit_price_usd, quantity, transaction_value_usd,
                             source_payload_sha256, coverage_status)
                        VALUES (%s, %s, %s, %s, %s, 'PSA', 'PSA 10', %s, %s, %s, %s,
                                %s, %s, %s, %s, 'partial')
                        """,
                        (
                            run_id, variant_id, provider, external_key, fingerprint,
                            parse_datetime(payload.get("soldAt"), observed_date),
                            str(payload.get("sourceDateText") or "")[:100], observed_at,
                            str(payload.get("timestampQuality") or "exact")[:24],
                            float(unit_price), quantity, float(transaction_value),
                            str(payload.get("sourcePayloadSha256") or payload_hash)[:64],
                        ),
                    )
            elif kind in {"tracked_sales_1d", "tracked_sales_7d", "tracked_sales_30d"}:
                count = payload.get("count")
                value = payload.get("valueUsd")
                coverage = str(payload.get("coverage") or "unavailable")
                if isinstance(count, int) and count >= 0 and isinstance(value, (int, float)) and value >= 0:
                    window = kind.rsplit("_", 1)[-1]
                    days = {"1d": 1, "7d": 7, "30d": 30}[window]
                    window_end = parse_datetime(payload.get("asOf"), observed_date)
                    window_start = window_end - timedelta(days=days)
                    aggregate_hash = sha256(canonical_json(payload))
                    cursor.execute(
                        """
                        INSERT IGNORE INTO market_tracked_sales_aggregate
                            (variant_id, grader_code, grade_label, window_code, window_start_at,
                             window_end_at, sales_count, sales_value_usd, coverage_status,
                             aggregate_sha256, computed_at)
                        VALUES (%s, 'PSA', 'PSA 10', %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            variant_id, window, window_start, window_end, count, float(value),
                            coverage[:24], aggregate_hash, observed_at,
                        ),
                    )
        cursor.execute(
            """
            UPDATE market_ingest_run
            SET status='complete', accepted_count=%s, observed_count=%s, completed_at=%s
            WHERE id=%s
            """,
            (inserted, len(active), datetime.now(timezone.utc).replace(tzinfo=None), run_id),
        )
        cursor.execute(
            """
            INSERT INTO market_ingest_checkpoint
                (source_code, stream_key, last_effective_at, last_payload_sha256, last_run_id)
            VALUES ('cardz_normalized', 'canonical_batches', %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                last_effective_at=VALUES(last_effective_at), last_payload_sha256=VALUES(last_payload_sha256),
                last_run_id=VALUES(last_run_id)
            """,
            (effective_at, batch_hash, run_id),
        )
    if commit:
        connection.commit()
    return {"inserted": inserted, "observed": len(active), "replayed": False}


def import_all(
    connection: Connection,
    active_path: Path,
    landing_root: Path,
    receipt_mappings_path: Path | None = None,
) -> dict[str, int]:
    acquired = False
    with connection.cursor() as cursor:
        cursor.execute("SELECT GET_LOCK('cardz_market_cap_import', 0) AS acquired")
        if int(cursor.fetchone()["acquired"] or 0) != 1:
            raise RuntimeError("another CARDZ database import is already running")
        acquired = True
    try:
        document = read_json(active_path)
        if not isinstance(document, Mapping):
            raise ValueError("active-universe document is invalid")
        cards = validate_active_universe(document)
        universe_hash = active_universe_lock_hash(document)
        variants, lock_id = import_lock(connection, document, commit=False)
        if len(variants) != len(cards) or len(set(variants.values())) != len(cards):
            raise RuntimeError("active-universe identities did not resolve one-to-one")
        result = {"active": len(variants), "batches": 0, "replayedBatches": 0, "observations": 0}
        if receipt_mappings_path is not None and receipt_mappings_path.is_file():
            aliases = read_json(receipt_mappings_path)
            if not isinstance(aliases, Mapping):
                raise ValueError("GemRate receipt mappings document is invalid")
            alias_report = import_gemrate_identity_aliases(connection, aliases, commit=False)
            result.update({
                "identityAliasesInserted": alias_report["inserted"],
                "identityAliasesReplayed": alias_report["replayed"],
                "identityAliasesUnanchored": alias_report["unanchored"],
            })
        for path, batch in iter_batches(landing_root):
            report = import_batch(connection, path, batch, variants, universe_hash, commit=False)
            result["batches"] += 1
            result["replayedBatches"] += int(bool(report["replayed"]))
            result["observations"] += int(report["inserted"])
        promote_lock(connection, lock_id, len(cards), commit=False)
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        if acquired:
            # Advisory locks are connection-scoped; releasing one is immediate
            # and must not commit a failed import transaction.
            with connection.cursor() as cursor:
                cursor.execute("SELECT RELEASE_LOCK('cardz_market_cap_import')")


def status(connection: Connection, active_document: Mapping[str, Any]) -> dict[str, Any]:
    cards = validate_active_universe(active_document)
    tables = (
        "catalog_variant", "catalog_provider_identity_alias", "market_universe_member", "market_ingest_run", "market_source_observation",
        "market_price_observation", "market_grader_population_observation", "market_daily_sales_aggregate",
        "market_index_snapshot", "market_index_constituent", "market_alert_evaluation",
        "market_candidate_daily_snapshot", "market_alert", "market_alert_event",
    )
    result: dict[str, Any] = {}
    with connection.cursor() as cursor:
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) AS count FROM {table}")
            result[table] = int(cursor.fetchone()["count"])
        cursor.execute(
            "SELECT source_code, stream_key, last_effective_at FROM market_ingest_checkpoint ORDER BY source_code, stream_key"
        )
        result["checkpoints"] = [
            {**row, "last_effective_at": row["last_effective_at"].isoformat()}
            for row in cursor.fetchall()
        ]
        cursor.execute(
            "SELECT id, lock_sha256, member_count FROM market_universe_lock WHERE is_current = 1 ORDER BY id DESC LIMIT 1"
        )
        current = cursor.fetchone()
        expected_lock_hash = active_universe_lock_hash(active_document)
        if not current or current["lock_sha256"] != expected_lock_hash:
            raise RuntimeError("database current universe does not match the active-universe lock")
        cursor.execute(
            "SELECT COUNT(*) AS members, COUNT(DISTINCT variant_id) AS variants "
            "FROM market_universe_member WHERE universe_lock_id = %s",
            (current["id"],),
        )
        counts = cursor.fetchone()
        expected = len(cards)
        if (
            int(current["member_count"]) != expected
            or int(counts["members"]) != expected
            or int(counts["variants"]) != expected
        ):
            raise RuntimeError("database active-universe integrity gate failed")
        formal_cards = active_document.get("cards")
        monitoring = active_document.get("monitoringCandidates", [])
        result["activeUniverse"] = {
            "members": expected,
            "formalMembers": len(formal_cards) if isinstance(formal_cards, list) else expected,
            "monitoringMembers": len(monitoring) if isinstance(monitoring, list) else 0,
            "payloadSha256": current["lock_sha256"],
        }
        cursor.execute(
            """
            SELECT effective_date, coverage_status, eligible_count, top100_cutoff_usd,
                   unresolved_high_potential_count
            FROM market_alert_evaluation
            ORDER BY effective_date DESC, id DESC LIMIT 1
            """
        )
        latest_alert = cursor.fetchone()
        result["latestAlertEvaluation"] = (
            {
                **latest_alert,
                "effective_date": latest_alert["effective_date"].isoformat(),
                "top100_cutoff_usd": str(latest_alert["top100_cutoff_usd"])
                if latest_alert["top100_cutoff_usd"] is not None
                else None,
            }
            if latest_alert
            else None
        )
        cursor.execute(
            """
            SELECT severity, COUNT(*) AS count FROM market_alert
            WHERE status IN ('observing','open','acknowledged')
            GROUP BY severity ORDER BY severity
            """
        )
        result["activeAlerts"] = {str(row["severity"]): int(row["count"]) for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT s.index_code,s.effective_date,s.constituent_count,s.snapshot_sha256
            FROM market_index_snapshot s
            JOIN (
                SELECT index_code,MAX(id) AS latest_id
                FROM market_index_snapshot
                WHERE index_version='psa10-v3-complete'
                GROUP BY index_code
            ) latest ON latest.latest_id=s.id
            ORDER BY s.index_code
            """
        )
        result["trackedIndexes"] = {
            str(row["index_code"]): {
                "count": int(row["constituent_count"]),
                "effectiveDate": row["effective_date"].isoformat(),
                "snapshotSha256": str(row["snapshot_sha256"]),
                "publicCount": min(300, int(row["constituent_count"])),
                "reserveCount": min(50, max(0, int(row["constituent_count"]) - 300)),
                "beyondTop350Count": max(0, int(row["constituent_count"]) - 350),
            }
            for row in cursor.fetchall()
        }
    return result


def self_test() -> dict[str, Any]:
    cards = [
        {
            "pokedexId": "cmc_fixture", "canonicalSourceCode": "fixture", "canonicalExternalId": "1",
            "tcg": "pokemon", "language": "ja", "name": "ピカチュウ",
            "setName": "プロモ", "collectorNumber": "001/S-P", "rankMemberships": {"pokemon": 1, "tcg": 1},
        }
    ]
    document = {
        "schemaVersion": "4.0.0",
        "policy": {
            "indexes": ["tcg", "pokemon", "one-piece"],
            "canonicalMembership": "complete_eligible",
            "languagePartitioning": False,
        },
        "cards": cards,
        "payloadSha256": sha256(canonical_json(cards)),
    }
    validated = validate_active_universe(document)
    statements = split_sql("-- comment\nCREATE TABLE a (id INT);\nINSERT INTO a VALUES (1);")
    return {"active": len(validated), "statements": len(statements), "hashValid": True}


def add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default=os.environ.get("CARDZ_DB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CARDZ_DB_PORT", "3308")))
    parser.add_argument("--database", default=os.environ.get("CARDZ_DB_NAME", "cardz_market_cap"))
    parser.add_argument("--user", default=os.environ.get("CARDZ_DB_USER", "cardz"))
    parser.add_argument("--password")


def main() -> int:
    parser = argparse.ArgumentParser(description="CARDZ standalone MySQL runtime")
    sub = parser.add_subparsers(dest="command", required=True)
    migrate_parser = sub.add_parser("migrate")
    add_connection_args(migrate_parser)
    migrate_parser.add_argument("--migrations", type=Path, default=DEFAULT_MIGRATIONS)
    import_parser = sub.add_parser("import")
    add_connection_args(import_parser)
    import_parser.add_argument("--active-universe", type=Path, default=DEFAULT_ACTIVE)
    import_parser.add_argument("--landing-root", type=Path, default=DEFAULT_LANDING)
    import_parser.add_argument(
        "--gemrate-receipt-mappings",
        type=Path,
        default=DEFAULT_GEMRATE_RECEIPT_MAPPINGS,
    )
    status_parser = sub.add_parser("status")
    add_connection_args(status_parser)
    status_parser.add_argument("--active-universe", type=Path, default=DEFAULT_ACTIVE)
    sub.add_parser("self-test")
    args = parser.parse_args()
    if args.command == "self-test":
        print(json.dumps(self_test(), sort_keys=True))
        return 0
    connection = connection_from_args(args)
    try:
        if args.command == "migrate":
            report = migrate(connection, args.migrations.resolve())
        elif args.command == "import":
            report = import_all(
                connection,
                args.active_universe.resolve(),
                args.landing_root.resolve(),
                args.gemrate_receipt_mappings.resolve(),
            )
        else:
            active_document = read_json(args.active_universe.resolve())
            if not isinstance(active_document, Mapping):
                raise ValueError("active-universe document is invalid")
            report = status(connection, active_document)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
