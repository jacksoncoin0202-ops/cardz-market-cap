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
from typing import Any, Iterable, Mapping, Sequence

import pymysql
from pymysql.connections import Connection


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_CONFIG = ROOT / "data" / "runtime" / "config" / "backend.env"
DEFAULT_MIGRATIONS = ROOT / "pipelines" / "migrations"
DEFAULT_ACTIVE = ROOT / "data" / "runtime" / "private-source-map" / "tracked-universe.json"
DEFAULT_LANDING = ROOT / "data" / "runtime" / "private-landing"
DEFAULT_FX = ROOT / "data" / "runtime" / "private-fx" / "latest.json"
DEFAULT_GEMRATE_RECEIPT_MAPPINGS = (
    ROOT / "data" / "runtime" / "private-source-map" / "gemrate-receipt-mappings.json"
)
DEFAULT_PRINTING_DECISIONS = (
    ROOT / "data" / "runtime" / "private-source-map" / "printing-decisions"
)
DEFAULT_PRINTING_CANDIDATES = (
    ROOT / "data" / "runtime" / "private-source-map" / "printing-candidates"
)
DEFAULT_CANONICAL_DB_QC = (
    ROOT / "data" / "runtime" / "private-reports" / "canonical-db-qc"
)
DEFAULT_IDENTITY_QUARANTINE = (
    ROOT
    / "data"
    / "runtime"
    / "private-source-map"
    / "snk-image-review"
    / "identity-quarantine"
)
PRINTING_EVIDENCE_ROOTS = (
    ROOT / "data" / "runtime" / "private-landing",
    ROOT / "data" / "runtime" / "private-source-map",
    ROOT / "data" / "runtime" / "private-reports",
)
PRINTING_LOCKS = (
    "cardz_market_cap_import",
    "cardz_market_alert_evaluation",
    "cardz_market_cap_universe_authority",
    "cardz.printing_identity_materialize.v1",
)


def load_db_env(path: Path = DEFAULT_DB_CONFIG) -> None:
    """Load the private DB config without leaking Windows CRLF into credentials."""

    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


PRINTING_FIELDS = (
    "tcgCode",
    "cardLanguage",
    "setName",
    "collectorNumber",
    "editionCode",
    "parallelCode",
    "finishCode",
)
PRINTING_PLACEHOLDERS = {
    "unknown",
    "unspecified",
    "n/a",
    "na",
    "none",
    "null",
    "tbd",
    "placeholder",
    "sample",
}
PRINTING_EVIDENCE_TYPES = {
    "human_verified_source_field",
    "vision_verified_source_field",
}
PRINTING_FIELD_RECEIPT_TYPE = "canonical_printing_field_evidence"
CANONICAL_QC_RECEIPT_COUNT_KEYS = {
    "qualified",
    "monitoring",
    "releaseReadyQualified",
    "releaseBlockedQualified",
}
GRADERS = {"PSA", "BGS", "CGC", "SGC", "TAG"}
SUPPORTED_CARD_LANGUAGES = {"en", "ja", "ko", "zhCN", "zhTW"}
GEMRATE_ALIAS_TYPES = {"entity", "universal", "grader_member", "spec"}
GEMRATE_HEX_ID = re.compile(r"[0-9a-f]{40}")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
QC_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}")


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


MONITORING_STATE_BANDS: dict[str, tuple[int, int]] = {
    "pre_entry_population_971_999": (971, 999),
    "buffer_850_999": (850, 999),
}
MONITORING_POLICY_RANGES: dict[str, dict[str, int]] = {
    "pre_entry_population_971_999": {"minimum": 971, "maximum": 999},
    "buffer_850_999": {"minimum": 850, "maximum": 999},
}


def _required_card_identity(
    raw: Mapping[str, Any],
    *,
    label: str,
    require_confirmed: bool,
    require_language: bool,
) -> tuple[str, str]:
    required = (
        "pokedexId", "canonicalSourceCode", "canonicalExternalId", "tcg",
        "name", "setName", "collectorNumber",
    )
    if require_language:
        required = (*required, "language")
    if any(not raw.get(key) for key in required):
        raise ValueError(f"{label} is incomplete")
    if require_confirmed and raw.get("pokedexStatus") != "confirmed":
        raise ValueError(f"{label} identity is not confirmed")
    if str(raw.get("tcg")) not in {"pokemon", "one-piece"}:
        raise ValueError(f"{label} has an unsupported TCG")
    if raw.get("language") is not None and str(raw.get("language")) not in SUPPORTED_CARD_LANGUAGES:
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
    require_language: bool,
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
            require_language=require_language,
        )
        source_ref = (str(raw["canonicalSourceCode"]).casefold(), str(raw["canonicalExternalId"]))
        opaque_id = str(raw["pokedexId"])
        if source_ref in seen_source or opaque_id in seen_opaque:
            raise ValueError("complete-ranking universe contains duplicate canonical identity")
        if require_exact_population and not _is_exact_gemrate_population(raw, minimum=1000):
            raise ValueError("complete-ranking universe contains a card without exact GemRate POP >= 1000")
        memberships = raw.get("rankMemberships")
        if memberships in (None, {}):
            continue
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
        _validate_complete_ranks(
            cards,
            require_exact_population=(
                schema_version == "5.0.0"
                and policy.get("requireExactPopulation", True)
            ),
            require_language=schema_version == "4.0.0",
        )
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
        extra_ranges = policy.get("monitoringAdditionalBandsInclusive")
        if extra_ranges is not None and extra_ranges != {"buffer_850_999": {"minimum": 850, "maximum": 999}}:
            raise ValueError("additional monitoring population bands are invalid")
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
                require_language=False,
            )
            opaque_id = str(raw["pokedexId"])
            if source_ref in seen_source or opaque_id in seen_opaque:
                raise ValueError("pre-entry monitoring candidate duplicates a canonical identity")
            state = raw.get("monitoringState")
            band = MONITORING_STATE_BANDS.get(str(state))
            if band is None:
                raise ValueError("pre-entry monitoring candidate has an invalid collection policy")
            if not _is_exact_gemrate_population(raw, minimum=band[0], maximum=band[1]):
                raise ValueError(f"pre-entry monitoring candidate does not have an exact GemRate POP {band[0]}-{band[1]}")
            if raw.get("collectionCadence") != "daily":
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


def _upsert_variant_resolution(cursor: Any, card: Mapping[str, Any]) -> tuple[int, bool]:
    opaque_id = str(card["pokedexId"])
    identity = (
        str(card["tcg"]),
        str(card["setName"]),
        str(card["collectorNumber"]),
    )
    incoming_identity_status = (
        "confirmed"
        if str(card.get("identityStatus") or "").strip().casefold()
        in {"", "confirmed", "exact_confirmed"}
        else "provisional"
    )
    source_code = str(card["canonicalSourceCode"]).casefold()
    external_id = str(card["canonicalExternalId"])
    if incoming_identity_status == "provisional":
        # A relaxed Grade10 overlay may expose a source ID which already has a
        # durable canonical owner.  Keep that owner instead of creating a
        # second variant or moving the source identity.  This is deliberately
        # limited to provisional intake; confirmed canonical imports retain the
        # existing fail-closed ownership check below.
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
            return int(existing_source["variant_id"]), True
    cursor.execute(
        """
        SELECT v.id, v.tcg_code, v.set_name, v.collector_number,
               alias.canonical_variant_id
        FROM catalog_variant AS v
        LEFT JOIN catalog_variant_alias AS alias ON alias.duplicate_variant_id=v.id
        WHERE v.opaque_id = %s
        FOR UPDATE
        """,
        (opaque_id,),
    )
    existing_variant = cursor.fetchone()
    if existing_variant:
        existing_identity = (
            str(existing_variant["tcg_code"]),
            str(existing_variant["set_name"]),
            str(existing_variant["collector_number"]),
        )
        if existing_identity != identity:
            raise ValueError(f"opaque_id canonical identity changed: {opaque_id}")
        alias_target = existing_variant.get("canonical_variant_id")
        is_alias = alias_target is not None
        variant_id = int(alias_target) if is_alias else int(existing_variant["id"])
        if not is_alias:
            cursor.execute(
                """UPDATE catalog_variant
                SET canonical_name = %s,
                    identity_status = CASE
                        WHEN identity_status = 'confirmed' THEN 'confirmed'
                        ELSE %s
                    END
                WHERE id = %s
                """,
                (str(card["name"]), incoming_identity_status, variant_id),
            )
    else:
        cursor.execute(
            """
            INSERT INTO catalog_variant
                (opaque_id, tcg_code, canonical_name, set_name, collector_number, identity_status)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (opaque_id, identity[0], str(card["name"]), *identity[1:], incoming_identity_status),
        )
        variant_id = int(cursor.lastrowid)
        is_alias = False
    source_identities = [(source_code, external_id)]
    if incoming_identity_status != "provisional":
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
    return variant_id, is_alias


def upsert_variant(cursor: Any, card: Mapping[str, Any]) -> int:
    variant_id, _is_alias = _upsert_variant_resolution(cursor, card)
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
    formal_source_refs = {
        (
            str(card["canonicalSourceCode"]).casefold(),
            str(card["canonicalExternalId"]),
        )
        for card in document.get("cards", [])
        if isinstance(card, Mapping)
    }
    with connection.cursor() as cursor:
        representatives: dict[int, tuple[Mapping[str, Any], bool]] = {}
        for card in cards:
            variant_id, is_alias = _upsert_variant_resolution(cursor, card)
            source_ref = (
                str(card["canonicalSourceCode"]).casefold(),
                str(card["canonicalExternalId"]),
            )
            existing_source_variant = mapping.get(source_ref)
            if existing_source_variant is not None and existing_source_variant != variant_id:
                raise RuntimeError(f"active-universe source ref changed owner: {source_ref}")
            mapping[source_ref] = variant_id
            current = representatives.get(variant_id)
            # A frozen source file may contain both the canonical opaque row and
            # one or more alias opaque rows. Keep every source ref in `mapping`,
            # but materialize one DB member. A formal tracked role outranks a
            # pre-entry monitoring role; only ties prefer the canonical opaque
            # row. This prevents identity convergence from demoting a card that
            # was already accepted into the formal roster.
            candidate_priority = (
                int(
                    bool(card.get("rankMemberships"))
                    or (
                        document.get("schemaVersion") == "5.0.0"
                        and source_ref in formal_source_refs
                    )
                    or card.get("trackingOrigin") == "psa10_over1000_exhaustive_20260725"
                    or document.get("schemaVersion") == "1.0.0"
                ),
                int(not is_alias),
            )
            current_priority = (
                (
                    int(
                        bool(current[0].get("rankMemberships"))
                        or (
                            document.get("schemaVersion") == "5.0.0"
                            and (
                                str(current[0]["canonicalSourceCode"]).casefold(),
                                str(current[0]["canonicalExternalId"]),
                            )
                            in formal_source_refs
                        )
                        or current[0].get("trackingOrigin") == "psa10_over1000_exhaustive_20260725"
                        or document.get("schemaVersion") == "1.0.0"
                    ),
                    int(not current[1]),
                )
                if current is not None
                else None
            )
            if current_priority is None or candidate_priority > current_priority:
                representatives[variant_id] = (card, is_alias)

        canonical_member_count = len(representatives)
        cursor.execute(
            """
            INSERT INTO market_universe_lock
                (lock_sha256, effective_at, policy_json, member_count, is_current)
            VALUES (%s, %s, %s, %s, 0)
            ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id), member_count = VALUES(member_count)
            """,
            (
                lock_hash,
                effective_at,
                canonical_json(document.get("policy") or {}).decode("utf-8"),
                canonical_member_count,
            ),
        )
        lock_id = int(cursor.lastrowid)
        if not lock_id:
            cursor.execute("SELECT id FROM market_universe_lock WHERE lock_sha256 = %s", (lock_hash,))
            lock_id = int(cursor.fetchone()["id"])
        cursor.execute(
            "DELETE FROM market_universe_member WHERE universe_lock_id=%s",
            (lock_id,),
        )
        for variant_id, (card, _is_alias) in representatives.items():
            memberships = card.get("rankMemberships")
            is_ranked_union = isinstance(memberships, Mapping) and bool(memberships)
            source_ref = (
                str(card["canonicalSourceCode"]).casefold(),
                str(card["canonicalExternalId"]),
            )
            is_schema5_formal = (
                document.get("schemaVersion") == "5.0.0"
                and source_ref in formal_source_refs
            )
            is_monitored = str(card.get("monitoringState") or "") in MONITORING_STATE_BANDS
            is_legacy_member = document.get("schemaVersion") == "1.0.0"
            # Operator-expanded tracked cards (exhaustive PSA 10 census) hold
            # no formal rank until their first validated price lands; they are
            # still daily-collection members of the lock.
            is_expanded_tracked = card.get("trackingOrigin") == "psa10_over1000_exhaustive_20260725"
            if (
                not is_ranked_union
                and not is_schema5_formal
                and not is_monitored
                and not is_legacy_member
                and not is_expanded_tracked
            ):
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
                    "tracked" if (is_ranked_union or is_schema5_formal or is_expanded_tracked) else ("pre-entry" if is_monitored else str(card["segment"])),
                    "candidate" if (is_ranked_union or is_schema5_formal or is_expanded_tracked) else ("monitoring" if is_monitored else str(card["role"])),
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
                        } if is_monitored else card.get("selectionSignals") or [])
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
                required = ("tcg", "set", "collectorNumber")
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
                    str(payload.get("edition") or "").strip().casefold(),
                    str(payload.get("parallel") or "").strip().casefold(),
                    str(payload.get("finish") or "").strip().casefold(),
                ]
                printing_hash = sha256("|".join(printing_parts).encode("utf-8"))
                cursor.execute(
                    """
                    INSERT INTO catalog_printing_identity
                        (variant_id, tcg_code, set_name, collector_number,
                         edition_code, parallel_code, finish_code, canonical_printing_sha256,
                         identity_status, evidence_sha256)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'candidate', %s)
                    ON DUPLICATE KEY UPDATE
                        tcg_code=VALUES(tcg_code), set_name=VALUES(set_name),
                        collector_number=VALUES(collector_number),
                        edition_code=VALUES(edition_code), parallel_code=VALUES(parallel_code),
                        finish_code=VALUES(finish_code), identity_status=VALUES(identity_status),
                        evidence_sha256=VALUES(evidence_sha256)
                    """,
                    (
                        variant_id, printing_parts[0], str(payload["set"]), str(payload["collectorNumber"]),
                        str(payload.get("edition") or ""),
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
        if len(variants) != len(cards):
            raise RuntimeError("active-universe source refs did not resolve one-to-one")
        canonical_members = len(set(variants.values()))
        result = {
            "active": canonical_members,
            "sourceEntries": len(cards),
            "aliasesCollapsed": len(cards) - canonical_members,
            "batches": 0,
            "replayedBatches": 0,
            "observations": 0,
        }
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
        promote_lock(connection, lock_id, canonical_members, commit=False)
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


def normalize_printing_value(value: Any) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def printing_field_receipt_payload(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical payload sealed by a field-level printing evidence receipt."""

    return {
        "type": receipt.get("type"),
        "schemaVersion": receipt.get("schemaVersion"),
        "variantId": receipt.get("variantId"),
        "field": receipt.get("field"),
        "rawValue": str(receipt.get("rawValue") or "").strip(),
        "normalizedValue": normalize_printing_value(receipt.get("normalizedValue")),
        "evidenceType": receipt.get("evidenceType"),
        "extractorVersion": str(receipt.get("extractorVersion") or "").strip(),
        "sourceCode": str(receipt.get("sourceCode") or "").strip(),
        "externalEntityId": str(receipt.get("externalEntityId") or "").strip(),
    }


def printing_field_receipt_sha256(receipt: Mapping[str, Any]) -> str:
    return sha256(canonical_json(printing_field_receipt_payload(receipt)))


def validate_printing_field_receipt(
    receipt: Mapping[str, Any],
    *,
    variant_id: int,
    field: str,
    evidence: Mapping[str, Any],
) -> None:
    """Bind one claimed value to its variant, field, source and evidence hash."""

    payload = printing_field_receipt_payload(receipt)
    if (
        payload["type"] != PRINTING_FIELD_RECEIPT_TYPE
        or payload["schemaVersion"] != 1
    ):
        raise ValueError("field_receipt_schema_invalid")
    try:
        receipt_variant_id = int(payload["variantId"])
    except (TypeError, ValueError) as error:
        raise ValueError("field_receipt_variant_invalid") from error
    if receipt_variant_id != int(variant_id):
        raise ValueError("field_receipt_variant_mismatch")
    if payload["field"] != field:
        raise ValueError("field_receipt_field_mismatch")
    if (
        payload["rawValue"] != str(evidence.get("rawValue") or "").strip()
        or payload["normalizedValue"]
        != normalize_printing_value(evidence.get("normalizedValue"))
    ):
        raise ValueError("field_receipt_value_mismatch")
    for key in (
        "evidenceType",
        "extractorVersion",
        "sourceCode",
        "externalEntityId",
    ):
        expected = (
            str(evidence.get(key) or "").strip()
            if key != "evidenceType"
            else evidence.get(key)
        )
        if payload[key] != expected:
            raise ValueError("field_receipt_metadata_mismatch")
    declared_hash = str(receipt.get("evidenceSha256") or "").casefold()
    if (
        not SHA256_HEX.fullmatch(declared_hash)
        or declared_hash != printing_field_receipt_sha256(receipt)
    ):
        raise ValueError("field_receipt_evidence_hash_mismatch")


def complete_printing_identity(identity: Mapping[str, Any]) -> bool:
    values = [normalize_printing_value(identity.get(field)) for field in PRINTING_FIELDS]
    return all(value and value not in PRINTING_PLACEHOLDERS for value in values)


def complete_printing_collector(identity: Mapping[str, Any]) -> bool:
    """Reject Pokemon numerator-only collectors from canonical materialization."""

    if normalize_printing_value(identity.get("tcgCode")) != "pokemon":
        return True
    collector = re.sub(
        r"\s+",
        "",
        str(identity.get("collectorNumber") or "").upper(),
    )
    return not (
        re.fullmatch(r"\d{1,4}", collector)
        or re.fullmatch(r"(?:GG|SV|TG|RC)\d{1,4}", collector)
    )


def printing_identity_sha256(identity: Mapping[str, Any]) -> str:
    """7-part printing hash including cardLanguage (see card_identity)."""
    if not complete_printing_identity(identity):
        raise ValueError("missing_printing_field")
    from card_identity import printing_key7, printing_key7_sha256

    key = printing_key7(
        identity.get("tcgCode"),
        identity.get("cardLanguage"),
        identity.get("setName"),
        identity.get("collectorNumber"),
        identity.get("editionCode"),
        identity.get("parallelCode"),
        identity.get("finishCode"),
    )
    if not key[1]:
        raise ValueError("missing_card_language")
    return printing_key7_sha256(key)


def _path_within(path: Path, roots: Sequence[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def resolve_printing_evidence_path(
    raw_path: Any,
    *,
    allowed_roots: Sequence[Path] = PRINTING_EVIDENCE_ROOTS,
) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        raise ValueError("source_receipt_path_missing")
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    resolved = candidate.resolve()
    roots = tuple(root.resolve() for root in allowed_roots)
    if not _path_within(resolved, roots):
        raise ValueError("source_receipt_path_outside_private_evidence_roots")
    if not resolved.is_file():
        raise ValueError("source_receipt_missing")
    return resolved


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def load_canonical_db_qc_bundle(
    *,
    qc_root: Path = DEFAULT_CANONICAL_DB_QC,
    qc_run: str | None = None,
) -> dict[str, Any]:
    root = qc_root.resolve()
    if qc_run:
        if QC_RUN_ID.fullmatch(qc_run) is None:
            raise ValueError("canonical_db_qc_run_id_invalid")
        receipt_path = root / qc_run / "receipt.json"
    else:
        receipts = list(root.glob("*/receipt.json"))
        if not receipts:
            raise ValueError("canonical_db_qc_receipt_missing")
        def authority_key(path: Path) -> tuple[datetime, str]:
            try:
                document = json.loads(path.read_bytes())
                return parse_datetime(document.get("asOf")), path.parent.name
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                return datetime.min, path.parent.name

        receipt_path = max(
            receipts,
            key=authority_key,
        )
    report_path = receipt_path.with_name("report.json")
    if not receipt_path.is_file() or not report_path.is_file():
        raise ValueError("canonical_db_qc_bundle_incomplete")
    receipt_bytes = receipt_path.read_bytes()
    report_bytes = report_path.read_bytes()
    receipt = json.loads(receipt_bytes)
    report = json.loads(report_bytes)
    if not isinstance(receipt, Mapping) or not isinstance(report, Mapping):
        raise ValueError("canonical_db_qc_bundle_invalid")
    report_sha = sha256(report_bytes)
    receipt_counts = receipt.get("counts")
    report_counts = report.get("counts")
    valid = (
        receipt.get("runId") == receipt_path.parent.name
        and report.get("runId") == receipt_path.parent.name
        and receipt.get("database") == "cardz_market_cap"
        and receipt.get("readOnly") is True
        and receipt.get("asOf") == report.get("asOf")
        and receipt.get("status") == report.get("status")
        and isinstance(receipt_counts, Mapping)
        and isinstance(report_counts, Mapping)
        and set(receipt_counts) == CANONICAL_QC_RECEIPT_COUNT_KEYS
        and all(report_counts.get(key) == value for key, value in receipt_counts.items())
        and receipt.get("releaseGate") == report.get("releaseGate")
        and receipt.get("reportSha256") == report_sha
        and isinstance(report.get("cards"), list)
    )
    if not valid:
        raise ValueError("canonical_db_qc_bundle_hash_or_contract_invalid")
    try:
        qc_as_of = parse_datetime(receipt.get("asOf"))
    except ValueError as exc:
        raise ValueError("canonical_db_qc_as_of_invalid") from exc
    if qc_as_of > datetime.now(timezone.utc).replace(tzinfo=None):
        raise ValueError("canonical_db_qc_future_dated")
    qualified_cards = [
        dict(card)
        for card in report["cards"]
        if isinstance(card, Mapping) and card.get("segment") == "qualified"
    ]
    if any(
        int(card.get("variantId") or 0) <= 0
        or not str(card.get("id") or "").strip()
        or SHA256_HEX.fullmatch(str(card.get("evidenceSha256") or "")) is None
        for card in qualified_cards
    ):
        raise ValueError("canonical_db_qc_card_evidence_invalid")
    return {
        "runId": str(report["runId"]),
        "asOf": str(report["asOf"]),
        "reportSha256": report_sha,
        "receiptSha256": sha256(receipt_bytes),
        "cards": qualified_cards,
    }


def load_identity_quarantine_bundle(
    root: Path = DEFAULT_IDENTITY_QUARANTINE,
) -> tuple[set[int], str, dict[int, dict[str, str]]]:
    quarantines: dict[str, dict[str, Any]] = {}
    resolutions: dict[str, dict[str, Any]] = {}
    manifest: list[dict[str, str]] = []
    if not root.is_dir():
        return set(), sha256(canonical_json(manifest)), {}
    for path in sorted(root.glob("*.json")):
        raw_bytes = path.read_bytes()
        manifest.append(
            {"path": _portable_path(path), "sha256": sha256(raw_bytes)}
        )
        receipt = json.loads(raw_bytes)
        if not isinstance(receipt, Mapping):
            raise ValueError(f"identity_quarantine_receipt_invalid:{path.name}")
        declared = str(receipt.get("receiptSha256") or "")
        body = dict(receipt)
        body.pop("receiptSha256", None)
        if (
            path.stem != declared
            or sha256(canonical_json(body)) != declared
            or not isinstance(receipt.get("variantId"), int)
        ):
            raise ValueError(f"identity_quarantine_receipt_invalid:{path.name}")
        receipt_type = receipt.get("type")
        if receipt_type == "identity_quarantine":
            quarantines[declared] = dict(receipt)
        elif receipt_type == "identity_quarantine_resolution":
            supersedes = str(receipt.get("supersedesReceiptSha256") or "")
            selected_snk_id = str(receipt.get("selectedSnkId") or "")
            if (
                receipt.get("schemaVersion") != 1
                or receipt.get("policyVersion")
                != "identity-quarantine-resolution-v1"
                or SHA256_HEX.fullmatch(supersedes) is None
                or not selected_snk_id
                or SHA256_HEX.fullmatch(
                    str(receipt.get("preSourceIdentitySha256") or "")
                )
                is None
                or SHA256_HEX.fullmatch(
                    str(receipt.get("postSourceIdentitySha256") or "")
                )
                is None
                or not str(receipt.get("actor") or "").strip()
                or parse_datetime(receipt.get("resolvedAt"))
                > datetime.now(timezone.utc).replace(tzinfo=None)
                or supersedes in resolutions
            ):
                raise ValueError(
                    f"identity_quarantine_resolution_invalid:{path.name}"
                )
            resolutions[supersedes] = dict(receipt)
        else:
            raise ValueError(f"identity_quarantine_receipt_invalid:{path.name}")

    resolved_by_variant: dict[int, dict[str, str]] = {}
    active_variants: set[int] = set()
    quarantines_by_variant: dict[int, list[str]] = {}
    for receipt_sha, quarantine in quarantines.items():
        variant_id = int(quarantine["variantId"])
        quarantines_by_variant.setdefault(variant_id, []).append(receipt_sha)
    for supersedes, resolution in resolutions.items():
        quarantine = quarantines.get(supersedes)
        if (
            quarantine is None
            or int(quarantine["variantId"]) != int(resolution["variantId"])
        ):
            raise ValueError(
                f"identity_quarantine_resolution_unknown_or_mismatched:{supersedes}"
            )
        original_snk_ids = sorted(
            {
                str(value)
                for value in (
                    list(quarantine.get("snkIds") or [])
                    + ([quarantine["snkId"]] if quarantine.get("snkId") else [])
                )
                if str(value)
            }
        )
        expected_pre_sha = sha256(
            canonical_json(
                {
                    "variantId": int(quarantine["variantId"]),
                    "snkIds": original_snk_ids,
                }
            )
        )
        if resolution.get("preSourceIdentitySha256") != expected_pre_sha:
            raise ValueError(
                f"identity_quarantine_resolution_prestate_mismatch:{supersedes}"
            )

    for variant_id, receipt_shas in quarantines_by_variant.items():
        unresolved = [receipt_sha for receipt_sha in receipt_shas if receipt_sha not in resolutions]
        if unresolved:
            active_variants.add(variant_id)
            continue
        selected_ids = {
            str(resolutions[receipt_sha]["selectedSnkId"]) for receipt_sha in receipt_shas
        }
        post_hashes = {
            str(resolutions[receipt_sha]["postSourceIdentitySha256"])
            for receipt_sha in receipt_shas
        }
        if len(selected_ids) != 1 or len(post_hashes) != 1:
            raise ValueError(
                f"identity_quarantine_resolution_conflict:{variant_id}"
            )
        resolved_by_variant[variant_id] = {
            "selectedSnkId": selected_ids.pop(),
            "postSourceIdentitySha256": post_hashes.pop(),
        }
    return (
        active_variants,
        sha256(canonical_json(manifest)),
        resolved_by_variant,
    )


def load_identity_quarantine_variants(
    root: Path = DEFAULT_IDENTITY_QUARANTINE,
) -> set[int]:
    return load_identity_quarantine_bundle(root)[0]


def load_printing_decisions(
    root: Path,
) -> tuple[dict[int, list[dict[str, Any]]], list[dict[str, Any]], str]:
    by_variant: dict[int, list[dict[str, Any]]] = {}
    invalid: list[dict[str, Any]] = []
    manifest: list[dict[str, str]] = []
    if not root.is_dir():
        return by_variant, invalid, sha256(canonical_json(manifest))
    for path in sorted(root.glob("*.json")):
        raw_bytes = path.read_bytes()
        digest = sha256(raw_bytes)
        manifest.append({"path": _portable_path(path), "sha256": digest})
        if path.stem != digest:
            invalid.append(
                {
                    "variantId": None,
                    "reason": "printing_receipt_not_content_addressed",
                    "receiptPath": _portable_path(path),
                    "receiptSha256": digest,
                }
            )
            continue
        try:
            receipt = json.loads(raw_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError):
            invalid.append(
                {
                    "variantId": None,
                    "reason": "printing_receipt_invalid_json",
                    "receiptPath": _portable_path(path),
                    "receiptSha256": digest,
                }
            )
            continue
        variant_id = receipt.get("variantId") if isinstance(receipt, Mapping) else None
        if not isinstance(variant_id, int):
            invalid.append(
                {
                    "variantId": None,
                    "reason": "printing_receipt_variant_missing",
                    "receiptPath": _portable_path(path),
                    "receiptSha256": digest,
                }
            )
            continue
        by_variant.setdefault(variant_id, []).append(
            {
                "receipt": dict(receipt),
                "path": path.resolve(),
                "sha256": digest,
            }
        )
    return by_variant, invalid, sha256(canonical_json(manifest))


def load_printing_db_state(
    cursor: Any,
    variant_ids: Sequence[int],
    *,
    for_update: bool = False,
) -> tuple[dict[int, dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    if not variant_ids:
        return {}, {}
    ids = sorted(set(int(value) for value in variant_ids))
    placeholders = ", ".join(["%s"] * len(ids))
    lock_clause = " FOR UPDATE" if for_update else ""
    cursor.execute(
        f"""
        SELECT v.id AS variant_id, v.opaque_id, v.tcg_code, v.card_language, v.set_name,
               v.collector_number, v.identity_status AS variant_identity_status,
               alias.canonical_variant_id,
               p.tcg_code AS printing_tcg_code, p.card_language AS printing_card_language,
               p.set_name AS printing_set_name,
               p.collector_number AS printing_collector_number,
               p.edition_code, p.parallel_code, p.finish_code,
               p.canonical_printing_sha256,
               p.identity_status AS printing_identity_status,
               p.evidence_sha256 AS printing_evidence_sha256
        FROM catalog_variant v
        LEFT JOIN catalog_variant_alias alias ON alias.duplicate_variant_id=v.id
        LEFT JOIN catalog_printing_identity p ON p.variant_id=v.id
        WHERE v.id IN ({placeholders})
        ORDER BY v.id{lock_clause}
        """,
        tuple(ids),
    )
    variants = {int(row["variant_id"]): dict(row) for row in cursor.fetchall()}
    cursor.execute(
        f"""
        SELECT variant_id, source_code, external_entity_id, match_status
        FROM catalog_source_identity
        WHERE variant_id IN ({placeholders})
        ORDER BY variant_id, source_code, external_entity_id{lock_clause}
        """,
        tuple(ids),
    )
    sources: dict[int, list[dict[str, Any]]] = {}
    for row in cursor.fetchall():
        sources.setdefault(int(row["variant_id"]), []).append(dict(row))
    return variants, sources


def printing_db_fingerprint(
    variant: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
) -> str:
    payload = {
        "variant": {
            key: variant.get(key)
            for key in (
                "variant_id",
                "opaque_id",
                "tcg_code",
                "card_language",
                "set_name",
                "collector_number",
                "variant_identity_status",
                "canonical_variant_id",
                "printing_tcg_code",
                "printing_card_language",
                "printing_set_name",
                "printing_collector_number",
                "edition_code",
                "parallel_code",
                "finish_code",
                "canonical_printing_sha256",
                "printing_identity_status",
                "printing_evidence_sha256",
            )
        },
        "sources": [
            {
                key: row.get(key)
                for key in (
                    "source_code",
                    "external_entity_id",
                    "match_status",
                )
            }
            for row in sorted(
                sources,
                key=lambda row: (
                    str(row.get("source_code") or ""),
                    str(row.get("external_entity_id") or ""),
                ),
            )
        ],
    }
    return sha256(canonical_json(payload))


def snk_source_identity_sha256(
    variant_id: int,
    sources: Sequence[Mapping[str, Any]],
) -> str:
    return sha256(
        canonical_json(
            {
                "variantId": int(variant_id),
                "sourceCode": "snkrdunk",
                "bindings": sorted(
                    (
                        {
                            "externalEntityId": str(
                                row.get("external_entity_id") or ""
                            ),
                            "matchStatus": str(row.get("match_status") or ""),
                        }
                        for row in sources
                        if str(row.get("source_code") or "") == "snkrdunk"
                    ),
                    key=lambda item: (
                        item["externalEntityId"],
                        item["matchStatus"],
                    ),
                ),
            }
        )
    )


def _validate_printing_decision(
    decision: Mapping[str, Any],
    *,
    receipt_path: Path,
    receipt_sha256: str,
    card: Mapping[str, Any],
    variant: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
    quarantine_variants: set[int],
    resolved_quarantines: Mapping[int, Mapping[str, str]],
    allowed_evidence_roots: Sequence[Path],
) -> dict[str, Any]:
    variant_id = int(card["variantId"])
    if variant_id in quarantine_variants:
        raise ValueError("identity_quarantined")
    if decision.get("type") != "canonical_printing_approval":
        raise ValueError("printing_receipt_type_invalid")
    if decision.get("schemaVersion") != 1 or decision.get("decision") != "approve":
        raise ValueError("printing_receipt_contract_invalid")
    if int(decision.get("variantId") or 0) != variant_id:
        raise ValueError("printing_receipt_variant_mismatch")
    if str(card.get("opaqueId") or "") != str(variant.get("opaque_id") or ""):
        raise ValueError("qc_card_identity_mismatch")
    if str(decision.get("opaqueId") or "") != str(variant.get("opaque_id") or ""):
        raise ValueError("printing_receipt_opaque_id_mismatch")
    if not str(decision.get("actor") or "").strip():
        raise ValueError("printing_receipt_actor_missing")
    approved_at = str(decision.get("approvedAt") or "")
    parsed_approved_at = parse_datetime(approved_at)
    if parsed_approved_at > datetime.now(timezone.utc).replace(tzinfo=None):
        raise ValueError("printing_receipt_future_dated")
    policy_version = str(decision.get("policyVersion") or "")
    if policy_version not in {"canonical-printing-v1", "canonical-printing-v2-pc"}:
        raise ValueError("printing_policy_version_invalid")
    if str(decision.get("qcRunId") or "") != str(card.get("qcRunId") or ""):
        raise ValueError("printing_receipt_qc_run_mismatch")
    if str(decision.get("cardEvidenceSha256") or "") != str(
        card.get("cardEvidenceSha256") or ""
    ):
        raise ValueError("printing_receipt_card_evidence_mismatch")

    if (
        str(variant.get("variant_identity_status") or "") != "confirmed"
        or variant.get("canonical_variant_id") is not None
    ):
        raise ValueError("alias_or_duplicate_unresolved")

    identity = decision.get("identity")
    fields = decision.get("fields")
    bindings = decision.get("sourceBindings")
    if not isinstance(identity, Mapping) or not isinstance(fields, Mapping):
        raise ValueError("printing_receipt_fields_missing")
    if not isinstance(bindings, list):
        raise ValueError("printing_source_bindings_missing")
    identity = {field: str(identity.get(field) or "").strip() for field in PRINTING_FIELDS}
    if not complete_printing_identity(identity):
        raise ValueError("missing_printing_field")
    if not complete_printing_collector(identity):
        raise ValueError("collector_number_incomplete")
    base_expected = {
        "tcgCode": variant.get("tcg_code"),
        "cardLanguage": variant.get("card_language"),
        "setName": variant.get("set_name"),
        "collectorNumber": variant.get("collector_number"),
    }
    for field, expected in base_expected.items():
        if normalize_printing_value(identity[field]) != normalize_printing_value(expected):
            raise ValueError("canonical_base_mismatch")

    exact_sources = {
        (str(row.get("source_code") or ""), str(row.get("external_entity_id") or ""))
        for row in sources
        if str(row.get("match_status") or "") == "exact"
    }
    declared_bindings = {
        (
            str(binding.get("sourceCode") or ""),
            str(binding.get("externalEntityId") or ""),
        )
        for binding in bindings
        if isinstance(binding, Mapping)
    }
    if (
        len(bindings) != 2
        or len(declared_bindings) != 2
        or not declared_bindings.issubset(exact_sources)
    ):
        raise ValueError("non_exact_or_discovery_evidence")
    gemrate_bindings = [value for value in declared_bindings if value[0] == "gemrate"]
    snk_bindings = [value for value in declared_bindings if value[0] == "snkrdunk"]
    pricecharting_bindings = [
        value for value in declared_bindings if value[0] == "pricecharting"
    ]
    exact_gemrate = [value for value in exact_sources if value[0] == "gemrate"]
    exact_snk = [value for value in exact_sources if value[0] == "snkrdunk"]
    exact_pricecharting = [
        value for value in exact_sources if value[0] == "pricecharting"
    ]
    if policy_version == "canonical-printing-v1":
        valid_route = (
            len(gemrate_bindings) == 1
            and len(snk_bindings) == 1
            and not pricecharting_bindings
            and len(exact_gemrate) == 1
            and len(exact_snk) == 1
        )
    else:
        # catalog_source_identity has (source_code, external_entity_id) as its
        # primary key. Requiring one exact PC row therefore proves one product
        # id owned by this variant, rather than a reusable display/title match.
        if any(str(row.get("source_code") or "") == "snkrdunk" for row in sources):
            raise ValueError("snkrdunk_conflict_for_v2")
        pc_id = pricecharting_bindings[0][1] if len(pricecharting_bindings) == 1 else ""
        valid_route = (
            len(gemrate_bindings) == 1
            and len(pricecharting_bindings) == 1
            and not snk_bindings
            and len(exact_gemrate) == 1
            and len(exact_pricecharting) == 1
            and not exact_snk
            and bool(re.fullmatch(r"[1-9][0-9]*", pc_id))
        )
        if not re.fullmatch(r"[1-9][0-9]*", pc_id):
            raise ValueError("pricecharting_product_id_invalid")
    if not valid_route:
        raise ValueError("ambiguous_source_binding")
    resolution = resolved_quarantines.get(variant_id)
    if policy_version == "canonical-printing-v1" and resolution is not None and (
        snk_bindings[0][1] != str(resolution.get("selectedSnkId") or "")
        or snk_source_identity_sha256(variant_id, sources)
        != str(resolution.get("postSourceIdentitySha256") or "")
    ):
        raise ValueError("identity_quarantine_resolution_drift")

    field_receipts: list[dict[str, Any]] = []
    for field in PRINTING_FIELDS:
        evidence = fields.get(field)
        if not isinstance(evidence, Mapping):
            raise ValueError("missing_printing_field")
        raw_value = str(evidence.get("rawValue") or "").strip()
        normalized_value = normalize_printing_value(evidence.get("normalizedValue"))
        if (
            not raw_value
            or not normalized_value
            or normalized_value in PRINTING_PLACEHOLDERS
            or normalized_value != normalize_printing_value(identity[field])
        ):
            raise ValueError("inferred_or_defaulted_value")
        if evidence.get("evidenceType") not in PRINTING_EVIDENCE_TYPES:
            raise ValueError("non_exact_or_discovery_evidence")
        if not str(evidence.get("extractorVersion") or "").strip():
            raise ValueError("extractor_version_missing")
        binding = (
            str(evidence.get("sourceCode") or ""),
            str(evidence.get("externalEntityId") or ""),
        )
        if binding not in declared_bindings:
            raise ValueError("provider_field_conflict")
        source_path = resolve_printing_evidence_path(
            evidence.get("sourceReceiptPath"),
            allowed_roots=allowed_evidence_roots,
        )
        declared_sha = str(evidence.get("sourceReceiptSha256") or "").casefold()
        actual_sha = sha256(source_path.read_bytes())
        if not SHA256_HEX.fullmatch(declared_sha) or declared_sha != actual_sha:
            raise ValueError("receipt_hash_mismatch")
        source_receipt = read_json(source_path)
        if not isinstance(source_receipt, Mapping):
            raise ValueError("field_receipt_schema_invalid")
        validate_printing_field_receipt(
            source_receipt,
            variant_id=variant_id,
            field=field,
            evidence=evidence,
        )
        field_receipts.append(
            {
                "field": field,
                "sourceCode": binding[0],
                "externalEntityId": binding[1],
                "sourceReceiptPath": _portable_path(source_path),
                "sourceReceiptSha256": actual_sha,
                "extractorVersion": str(evidence["extractorVersion"]),
                "evidenceType": str(evidence["evidenceType"]),
            }
        )

    printing_sha = printing_identity_sha256(identity)
    existing_status = str(variant.get("printing_identity_status") or "")
    existing_tuple = {
        "tcgCode": variant.get("printing_tcg_code"),
        "cardLanguage": variant.get("printing_card_language"),
        "setName": variant.get("printing_set_name"),
        "collectorNumber": variant.get("printing_collector_number"),
        "editionCode": variant.get("edition_code"),
        "parallelCode": variant.get("parallel_code"),
        "finishCode": variant.get("finish_code"),
    }
    # Incomplete legacy rows marked canonical are not a locked seven-field identity.
    # Only a complete existing canonical identity can conflict or no-op.
    if existing_status == "canonical" and complete_printing_identity(existing_tuple):
        same = (
            printing_identity_sha256(existing_tuple) == printing_sha
            and str(variant.get("canonical_printing_sha256") or "") == printing_sha
            and str(variant.get("printing_evidence_sha256") or "") == receipt_sha256
        )
        if same:
            operation = "noop"
        else:
            supersedes = str(
                decision.get("supersedesEvidenceSha256") or ""
            ).casefold()
            repair_reason = str(decision.get("repairReason") or "").strip()
            if (
                SHA256_HEX.fullmatch(supersedes) is None
                or supersedes
                != str(variant.get("printing_evidence_sha256") or "").casefold()
                or not repair_reason
            ):
                raise ValueError("existing_canonical_conflict")
            operation = "replace"
    else:
        operation = "upsert"

    post_apply_variant = dict(variant)
    post_apply_variant.update(
        {
            "printing_tcg_code": identity["tcgCode"],
            "printing_card_language": identity["cardLanguage"],
            "printing_set_name": identity["setName"],
            "printing_collector_number": identity["collectorNumber"],
            "edition_code": identity["editionCode"],
            "parallel_code": identity["parallelCode"],
            "finish_code": identity["finishCode"],
            "canonical_printing_sha256": printing_sha,
            "printing_identity_status": "canonical",
            "printing_evidence_sha256": receipt_sha256,
        }
    )
    return {
        "variantId": variant_id,
        "opaqueId": str(variant["opaque_id"]),
        "qcRunId": str(card["qcRunId"]),
        "cardEvidenceSha256": str(card["cardEvidenceSha256"]),
        **identity,
        "canonicalPrintingSha256": printing_sha,
        "approvalReceiptPath": _portable_path(receipt_path),
        "approvalReceiptSha256": receipt_sha256,
        "fieldEvidence": field_receipts,
        "dbFingerprint": printing_db_fingerprint(variant, sources),
        "postApplyDbFingerprint": printing_db_fingerprint(post_apply_variant, sources),
        "operation": operation,
    }


def printing_plan_payload(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": document.get("schemaVersion"),
        "status": document.get("status"),
        "asOf": document.get("asOf"),
        "qcRunId": document.get("qcRunId"),
        "qcReportSha256": document.get("qcReportSha256"),
        "qcReceiptSha256": document.get("qcReceiptSha256"),
        "decisionManifestSha256": document.get("decisionManifestSha256"),
        "identityQuarantineManifestSha256": document.get(
            "identityQuarantineManifestSha256"
        ),
        "rows": document.get("rows"),
        "quarantineSha256": document.get("quarantineSha256"),
        "counts": document.get("counts"),
    }


def printing_plan_sha256(document: Mapping[str, Any]) -> str:
    return sha256(canonical_json(printing_plan_payload(document)))


def build_printing_plan(
    connection: Connection,
    *,
    decision_root: Path = DEFAULT_PRINTING_DECISIONS,
    qc_root: Path = DEFAULT_CANONICAL_DB_QC,
    qc_run: str | None = None,
    quarantine_root: Path = DEFAULT_IDENTITY_QUARANTINE,
    allowed_evidence_roots: Sequence[Path] = PRINTING_EVIDENCE_ROOTS,
) -> dict[str, Any]:
    qc = load_canonical_db_qc_bundle(qc_root=qc_root, qc_run=qc_run)
    cards = sorted(
        (
            {
                "variantId": int(card.get("variantId") or 0),
                "opaqueId": str(card.get("id") or ""),
                "qcRunId": qc["runId"],
                "cardEvidenceSha256": str(card.get("evidenceSha256") or ""),
                "marketRank": card.get("marketRank"),
            }
            for card in qc["cards"]
            if int(card.get("variantId") or 0) > 0
        ),
        key=lambda card: (
            card["marketRank"] is None,
            int(card["marketRank"] or 10**9),
            card["variantId"],
        ),
    )
    decisions, invalid, decision_manifest_sha = load_printing_decisions(decision_root)
    (
        quarantine_variants,
        identity_quarantine_manifest_sha,
        resolved_quarantines,
    ) = (
        load_identity_quarantine_bundle(quarantine_root)
    )
    with connection.cursor() as cursor:
        assert_canonical_database(cursor)
        variants, sources = load_printing_db_state(
            cursor,
            [card["variantId"] for card in cards],
        )

    rows: list[dict[str, Any]] = []
    quarantine: list[dict[str, Any]] = list(invalid)
    card_by_variant = {card["variantId"]: card for card in cards}
    for card in cards:
        variant_id = card["variantId"]
        receipts = [
            item
            for item in decisions.get(variant_id, [])
            if isinstance(item.get("receipt"), Mapping)
            and item["receipt"].get("type") == "canonical_printing_approval"
            and item["receipt"].get("schemaVersion") == 1
            and item["receipt"].get("decision") == "approve"
            and str(item["receipt"].get("qcRunId") or "") == card["qcRunId"]
            and str(item["receipt"].get("opaqueId") or "") == card["opaqueId"]
            and str(item["receipt"].get("cardEvidenceSha256") or "")
            == card["cardEvidenceSha256"]
        ]
        if not receipts:
            quarantine.append(
                {
                    "variantId": variant_id,
                    "opaqueId": card["opaqueId"],
                    "marketRank": card["marketRank"],
                    "reason": "missing_printing_receipt",
                }
            )
            continue
        variant = variants.get(variant_id)
        if variant is None:
            quarantine.append(
                {
                    "variantId": variant_id,
                    "opaqueId": card["opaqueId"],
                    "marketRank": card["marketRank"],
                    "reason": "canonical_variant_missing",
                }
            )
            continue
        valid_rows: list[dict[str, Any]] = []
        invalid_receipts: list[tuple[dict[str, Any], str]] = []
        for item in receipts:
            try:
                valid_rows.append(
                    _validate_printing_decision(
                        item["receipt"],
                        receipt_path=item["path"],
                        receipt_sha256=item["sha256"],
                        card=card,
                        variant=variant,
                        sources=sources.get(variant_id, []),
                        quarantine_variants=quarantine_variants,
                        resolved_quarantines=resolved_quarantines,
                        allowed_evidence_roots=allowed_evidence_roots,
                    )
                )
            except (OSError, ValueError) as exc:
                invalid_receipts.append((item, str(exc)))
        if len(valid_rows) > 1:
            quarantine.append(
                {
                    "variantId": variant_id,
                    "opaqueId": card["opaqueId"],
                    "marketRank": card["marketRank"],
                    "reason": "ambiguous_printing_receipts",
                    "receiptCount": len(valid_rows),
                }
            )
            continue
        if not valid_rows:
            item, reason = invalid_receipts[0]
            quarantine.append(
                {
                    "variantId": variant_id,
                    "opaqueId": card["opaqueId"],
                    "marketRank": card["marketRank"],
                    "reason": reason,
                    "approvalReceiptPath": _portable_path(item["path"]),
                    "approvalReceiptSha256": item["sha256"],
                }
            )
            continue
        rows.append(valid_rows[0])

    for variant_id, receipts in decisions.items():
        if variant_id not in card_by_variant:
            quarantine.append(
                {
                    "variantId": variant_id,
                    "reason": "receipt_not_in_qualified_qc_target",
                    "receiptCount": len(receipts),
                }
            )

    by_hash: dict[str, list[int]] = {}
    for row in rows:
        by_hash.setdefault(row["canonicalPrintingSha256"], []).append(row["variantId"])
    duplicate_hashes = {
        digest: variant_ids
        for digest, variant_ids in by_hash.items()
        if len(variant_ids) > 1
    }
    if duplicate_hashes:
        rows = [
            row
            for row in rows
            if row["canonicalPrintingSha256"] not in duplicate_hashes
        ]
        for digest, variant_ids in sorted(duplicate_hashes.items()):
            for variant_id in variant_ids:
                quarantine.append(
                    {
                        "variantId": variant_id,
                        "reason": "duplicate_printing_hash",
                        "canonicalPrintingSha256": digest,
                        "collidingVariantIds": variant_ids,
                    }
                )

    if rows:
        with connection.cursor() as cursor:
            placeholders = ", ".join(["%s"] * len(rows))
            cursor.execute(
                f"""
                SELECT canonical_printing_sha256, variant_id
                FROM catalog_printing_identity
                WHERE canonical_printing_sha256 IN ({placeholders})
                """,
                tuple(row["canonicalPrintingSha256"] for row in rows),
            )
            owners = {
                str(item["canonical_printing_sha256"]): int(item["variant_id"])
                for item in cursor.fetchall()
            }
        kept: list[dict[str, Any]] = []
        rows_by_variant = {int(row["variantId"]): row for row in rows}
        for row in rows:
            owner = owners.get(row["canonicalPrintingSha256"])
            if owner is not None and owner != row["variantId"]:
                owner_row = rows_by_variant.get(owner)
                owner_variant = variants.get(owner) or {}
                owner_releases_hash = (
                    owner_row is not None
                    and owner_row.get("operation") == "replace"
                    and str(owner_variant.get("canonical_printing_sha256") or "")
                    == row["canonicalPrintingSha256"]
                    and owner_row.get("canonicalPrintingSha256")
                    != row["canonicalPrintingSha256"]
                )
                if owner_releases_hash:
                    kept.append(row)
                    continue
                quarantine.append(
                    {
                        "variantId": row["variantId"],
                        "reason": "duplicate_printing_hash",
                        "canonicalPrintingSha256": row["canonicalPrintingSha256"],
                        "existingVariantId": owner,
                    }
                )
            else:
                kept.append(row)
        rows = kept

    rows.sort(key=lambda row: row["variantId"])
    quarantine.sort(
        key=lambda item: (
            item.get("marketRank") is None,
            int(item.get("marketRank") or 10**9),
            int(item.get("variantId") or 0),
            str(item.get("reason") or ""),
        )
    )
    quarantine_sha = sha256(canonical_json(quarantine))
    document: dict[str, Any] = {
        "schemaVersion": "canonical-printing-plan/v1",
        "status": "ready" if rows else "blocked",
        "asOf": qc["asOf"],
        "qcRunId": qc["runId"],
        "qcReportSha256": qc["reportSha256"],
        "qcReceiptSha256": qc["receiptSha256"],
        "decisionManifestSha256": decision_manifest_sha,
        "identityQuarantineManifestSha256": identity_quarantine_manifest_sha,
        "rows": rows,
        "quarantine": quarantine,
        "quarantineSha256": quarantine_sha,
        "counts": {
            "qualifiedQcCards": len(cards),
            "approvedRows": len(rows),
            "quarantinedRows": len(quarantine),
        },
    }
    document["planSha256"] = printing_plan_sha256(document)
    return document


def write_printing_candidate(document: Mapping[str, Any], output_root: Path) -> Path:
    plan_sha = str(document.get("planSha256") or "")
    if not SHA256_HEX.fullmatch(plan_sha) or printing_plan_sha256(document) != plan_sha:
        raise ValueError("printing plan hash is invalid")
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / f"printing_{plan_sha}.json"
    content = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if path.is_file():
        if path.read_text(encoding="utf-8") != content:
            raise RuntimeError("immutable printing candidate path already has different bytes")
        return path
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    try:
        os.link(temporary, path)
    except FileExistsError:
        if path.read_text(encoding="utf-8") != content:
            raise RuntimeError(
                "immutable printing candidate path already has different bytes"
            )
    finally:
        temporary.unlink(missing_ok=True)
    return path


def acquire_printing_locks(cursor: Any) -> list[str]:
    acquired: list[str] = []
    for lock_name in PRINTING_LOCKS:
        cursor.execute("SELECT GET_LOCK(%s, 0) AS acquired", (lock_name,))
        row = cursor.fetchone()
        if row is None or int(row.get("acquired") or 0) != 1:
            for held in reversed(acquired):
                cursor.execute("SELECT RELEASE_LOCK(%s)", (held,))
            raise RuntimeError(f"database writer lock is busy: {lock_name}")
        acquired.append(lock_name)
    return acquired


def assert_canonical_database(cursor: Any) -> None:
    cursor.execute("SELECT DATABASE() AS database_name")
    row = cursor.fetchone()
    database = str((row or {}).get("database_name") or "")
    if database != "cardz_market_cap":
        raise RuntimeError(
            f"printing materializer refuses non-canonical database: {database or 'none'}"
        )


def release_printing_locks(cursor: Any, acquired: Sequence[str]) -> None:
    for lock_name in reversed(acquired):
        cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))


def load_printing_authority_snapshot(
    *,
    qc_run: str,
    qc_root: Path,
    decision_root: Path,
    quarantine_root: Path,
) -> dict[str, Any]:
    exact_qc = load_canonical_db_qc_bundle(qc_root=qc_root, qc_run=qc_run)
    current_qc = load_canonical_db_qc_bundle(qc_root=qc_root)
    if exact_qc != current_qc:
        raise ValueError("stale_plan:qc_authority")
    _, _, decision_manifest_sha = load_printing_decisions(decision_root)
    (
        quarantine_variants,
        quarantine_manifest_sha,
        resolved_quarantines,
    ) = load_identity_quarantine_bundle(quarantine_root)
    fingerprint = sha256(
        canonical_json(
            {
                "qcRunId": current_qc["runId"],
                "qcReportSha256": current_qc["reportSha256"],
                "qcReceiptSha256": current_qc["receiptSha256"],
                "decisionManifestSha256": decision_manifest_sha,
                "identityQuarantineManifestSha256": quarantine_manifest_sha,
            }
        )
    )
    return {
        "qc": current_qc,
        "decisionManifestSha256": decision_manifest_sha,
        "identityQuarantineManifestSha256": quarantine_manifest_sha,
        "quarantineVariants": quarantine_variants,
        "resolvedQuarantines": resolved_quarantines,
        "fingerprint": fingerprint,
    }


def materialize_printing_candidate(
    connection: Connection,
    candidate: Mapping[str, Any],
    *,
    expected_plan_sha256: str,
    commit: bool,
    decision_root: Path = DEFAULT_PRINTING_DECISIONS,
    qc_root: Path = DEFAULT_CANONICAL_DB_QC,
    quarantine_root: Path = DEFAULT_IDENTITY_QUARANTINE,
    allowed_evidence_roots: Sequence[Path] = PRINTING_EVIDENCE_ROOTS,
) -> dict[str, Any]:
    declared = str(candidate.get("planSha256") or "")
    candidate_rows = candidate.get("rows")
    candidate_quarantine = candidate.get("quarantine")
    candidate_counts = candidate.get("counts")
    row_variant_ids = (
        [int(row.get("variantId") or 0) for row in candidate_rows]
        if isinstance(candidate_rows, list)
        and all(isinstance(row, Mapping) for row in candidate_rows)
        else []
    )
    row_printing_hashes = (
        [str(row.get("canonicalPrintingSha256") or "") for row in candidate_rows]
        if isinstance(candidate_rows, list)
        and all(isinstance(row, Mapping) for row in candidate_rows)
        else []
    )
    if (
        candidate.get("schemaVersion") != "canonical-printing-plan/v1"
        or not SHA256_HEX.fullmatch(expected_plan_sha256)
        or declared != expected_plan_sha256
        or printing_plan_sha256(candidate) != declared
        or not isinstance(candidate_rows, list)
        or not all(isinstance(row, Mapping) for row in candidate_rows)
        or not isinstance(candidate_quarantine, list)
        or not all(isinstance(row, Mapping) for row in candidate_quarantine)
        or sha256(canonical_json(candidate_quarantine))
        != candidate.get("quarantineSha256")
        or not isinstance(candidate_counts, Mapping)
        or candidate_counts.get("approvedRows") != len(candidate_rows)
        or candidate_counts.get("quarantinedRows") != len(candidate_quarantine)
        or candidate.get("status")
        != ("ready" if len(candidate_rows) > 0 else "blocked")
        or any(variant_id <= 0 for variant_id in row_variant_ids)
        or len(set(row_variant_ids)) != len(row_variant_ids)
        or any(
            SHA256_HEX.fullmatch(digest) is None for digest in row_printing_hashes
        )
        or len(set(row_printing_hashes)) != len(row_printing_hashes)
    ):
        raise ValueError("stale_plan")

    candidate_qc_run = str(candidate.get("qcRunId") or "")
    authority = load_printing_authority_snapshot(
        qc_run=candidate_qc_run,
        qc_root=qc_root,
        decision_root=decision_root,
        quarantine_root=quarantine_root,
    )
    current_qc = authority["qc"]
    if any(
        candidate.get(key) != current_qc[value]
        for key, value in (
            ("qcRunId", "runId"),
            ("asOf", "asOf"),
            ("qcReportSha256", "reportSha256"),
            ("qcReceiptSha256", "receiptSha256"),
        )
    ):
        raise ValueError("stale_plan:qc_authority")
    if candidate_counts.get("qualifiedQcCards") != len(current_qc["cards"]):
        raise ValueError("stale_plan:qc_card_count")
    if (
        candidate.get("decisionManifestSha256")
        != authority["decisionManifestSha256"]
    ):
        raise ValueError("stale_plan:decision_manifest")
    if (
        candidate.get("identityQuarantineManifestSha256")
        != authority["identityQuarantineManifestSha256"]
    ):
        raise ValueError("stale_plan:identity_quarantine_manifest")
    quarantine_variants = authority["quarantineVariants"]
    resolved_quarantines = authority["resolvedQuarantines"]
    qc_cards = {
        int(card["variantId"]): {
            "variantId": int(card["variantId"]),
            "opaqueId": str(card["id"]),
            "qcRunId": current_qc["runId"],
            "cardEvidenceSha256": str(card["evidenceSha256"]),
        }
        for card in current_qc["cards"]
    }

    rows = [dict(row) for row in candidate_rows]
    if commit and not rows:
        raise ValueError("no_approved_printing_rows")
    acquired: list[str] = []
    changed = 0
    replayed = 0
    try:
        with connection.cursor() as cursor:
            assert_canonical_database(cursor)
            acquired = acquire_printing_locks(cursor)
            connection.begin()
            locked_authority = load_printing_authority_snapshot(
                qc_run=candidate_qc_run,
                qc_root=qc_root,
                decision_root=decision_root,
                quarantine_root=quarantine_root,
            )
            if locked_authority["fingerprint"] != authority["fingerprint"]:
                raise RuntimeError("stale_plan:authority_changed_after_lock")
            quarantine_variants = locked_authority["quarantineVariants"]
            resolved_quarantines = locked_authority["resolvedQuarantines"]
            variants, sources = load_printing_db_state(
                cursor,
                [int(row["variantId"]) for row in rows],
                for_update=True,
            )
            execution_rows = sorted(
                rows,
                key=lambda row: (
                    row.get("operation") != "replace",
                    int(row["variantId"]),
                ),
            )
            for row in execution_rows:
                variant_id = int(row["variantId"])
                variant = variants.get(variant_id)
                if variant is None:
                    raise RuntimeError(f"stale_plan:variant_missing:{variant_id}")
                current_fingerprint = printing_db_fingerprint(
                    variant, sources.get(variant_id, [])
                )
                if current_fingerprint not in {
                    str(row.get("dbFingerprint") or ""),
                    str(row.get("postApplyDbFingerprint") or ""),
                }:
                    raise RuntimeError(f"stale_plan:db_fingerprint:{variant_id}")

                receipt_path = resolve_printing_evidence_path(
                    row.get("approvalReceiptPath"),
                    allowed_roots=allowed_evidence_roots,
                )
                receipt_sha = sha256(receipt_path.read_bytes())
                if receipt_sha != str(row.get("approvalReceiptSha256") or ""):
                    raise RuntimeError(f"receipt_hash_mismatch:{variant_id}")
                decision = read_json(receipt_path)
                card = qc_cards.get(variant_id)
                if card is None:
                    raise RuntimeError(f"stale_plan:qc_card_missing:{variant_id}")
                revalidated = _validate_printing_decision(
                    decision,
                    receipt_path=receipt_path,
                    receipt_sha256=receipt_sha,
                    card=card,
                    variant=variant,
                    sources=sources.get(variant_id, []),
                    quarantine_variants=quarantine_variants,
                    resolved_quarantines=resolved_quarantines,
                    allowed_evidence_roots=allowed_evidence_roots,
                )
                for key in (
                    "qcRunId",
                    "cardEvidenceSha256",
                    "tcgCode",
                    "cardLanguage",
                    "setName",
                    "collectorNumber",
                    "editionCode",
                    "parallelCode",
                    "finishCode",
                    "canonicalPrintingSha256",
                    "approvalReceiptSha256",
                    "postApplyDbFingerprint",
                ):
                    if revalidated.get(key) != row.get(key):
                        raise RuntimeError(f"stale_plan:row_drift:{variant_id}:{key}")
                if revalidated["operation"] == "noop":
                    replayed += 1
                    continue

                cursor.execute(
                    """
                    SELECT variant_id
                    FROM catalog_printing_identity
                    WHERE canonical_printing_sha256=%s AND variant_id<>%s
                    FOR UPDATE
                    """,
                    (row["canonicalPrintingSha256"], variant_id),
                )
                if cursor.fetchone() is not None:
                    raise RuntimeError(f"duplicate_printing_hash:{variant_id}")
                cursor.execute(
                    """
                    INSERT INTO catalog_printing_identity
                        (variant_id, tcg_code, card_language, set_name, collector_number,
                         edition_code, parallel_code, finish_code,
                         canonical_printing_sha256, identity_status, evidence_sha256)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'canonical', %s)
                    ON DUPLICATE KEY UPDATE
                        tcg_code=VALUES(tcg_code), set_name=VALUES(set_name),
                        card_language=VALUES(card_language),
                        collector_number=VALUES(collector_number),
                        edition_code=VALUES(edition_code),
                        parallel_code=VALUES(parallel_code),
                        finish_code=VALUES(finish_code),
                        canonical_printing_sha256=VALUES(canonical_printing_sha256),
                        identity_status='canonical',
                        evidence_sha256=VALUES(evidence_sha256)
                    """,
                    (
                        variant_id,
                        row["tcgCode"],
                        row["cardLanguage"],
                        row["setName"],
                        row["collectorNumber"],
                        row["editionCode"],
                        row["parallelCode"],
                        row["finishCode"],
                        row["canonicalPrintingSha256"],
                        row["approvalReceiptSha256"],
                    ),
                )
                changed += 1
            if rows:
                placeholders = ", ".join(["%s"] * len(rows))
                cursor.execute(
                    f"""
                    SELECT variant_id, tcg_code, card_language, set_name, collector_number,
                           edition_code, parallel_code, finish_code,
                           canonical_printing_sha256, identity_status,
                           evidence_sha256
                    FROM catalog_printing_identity
                    WHERE variant_id IN ({placeholders})
                    ORDER BY variant_id
                    FOR UPDATE
                    """,
                    tuple(int(row["variantId"]) for row in rows),
                )
                stored = {
                    int(item["variant_id"]): dict(item) for item in cursor.fetchall()
                }
                expected_columns = {
                    "tcgCode": "tcg_code",
                    "cardLanguage": "card_language",
                    "setName": "set_name",
                    "collectorNumber": "collector_number",
                    "editionCode": "edition_code",
                    "parallelCode": "parallel_code",
                    "finishCode": "finish_code",
                    "canonicalPrintingSha256": "canonical_printing_sha256",
                    "approvalReceiptSha256": "evidence_sha256",
                }
                for row in rows:
                    variant_id = int(row["variantId"])
                    stored_row = stored.get(variant_id)
                    if (
                        stored_row is None
                        or stored_row.get("identity_status") != "canonical"
                        or any(
                            stored_row.get(column) != row.get(field)
                            for field, column in expected_columns.items()
                        )
                    ):
                        raise RuntimeError(
                            f"printing_materialize_readback_mismatch:{variant_id}"
                        )
            final_authority = load_printing_authority_snapshot(
                qc_run=candidate_qc_run,
                qc_root=qc_root,
                decision_root=decision_root,
                quarantine_root=quarantine_root,
            )
            if final_authority["fingerprint"] != locked_authority["fingerprint"]:
                raise RuntimeError("stale_plan:authority_changed_before_commit")
        if commit:
            connection.commit()
        else:
            connection.rollback()
    except Exception:
        connection.rollback()
        raise
    finally:
        if acquired:
            with connection.cursor() as cursor:
                release_printing_locks(cursor, acquired)
    return {
        "planSha256": declared,
        "candidateRows": len(rows),
        "changedRows": changed if commit else 0,
        "wouldChangeRows": changed,
        "replayedRows": replayed,
        "committed": bool(commit),
    }


def resolved_universe_variants(
    cursor: Any,
    cards: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    opaque_ids = [str(card["pokedexId"]) for card in cards]
    if not opaque_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(opaque_ids))
    cursor.execute(
        f"""
        SELECT v.opaque_id, COALESCE(alias.canonical_variant_id, v.id) AS resolved_variant_id
        FROM catalog_variant AS v
        LEFT JOIN catalog_variant_alias AS alias ON alias.duplicate_variant_id=v.id
        WHERE v.opaque_id IN ({placeholders})
        """,
        tuple(opaque_ids),
    )
    resolved = {
        str(row["opaque_id"]): int(row["resolved_variant_id"])
        for row in cursor.fetchall()
    }
    missing = sorted(set(opaque_ids) - set(resolved))
    provisional = [
        card
        for card in cards
        if str(card.get("pokedexId")) in missing
        and str(card.get("identityStatus") or "").strip().casefold() == "provisional"
    ]
    if provisional:
        placeholders = ", ".join(["(%s, %s)"] * len(provisional))
        values: list[str] = []
        for card in provisional:
            values.extend(
                [
                    str(card["canonicalSourceCode"]).casefold(),
                    str(card["canonicalExternalId"]),
                ]
            )
        cursor.execute(
            f"""
            SELECT s.source_code, s.external_entity_id,
                   COALESCE(alias.canonical_variant_id, s.variant_id) AS resolved_variant_id
            FROM catalog_source_identity AS s
            LEFT JOIN catalog_variant_alias AS alias ON alias.duplicate_variant_id=s.variant_id
            WHERE (s.source_code, s.external_entity_id) IN ({placeholders})
            """,
            tuple(values),
        )
        by_source = {
            (str(row["source_code"]).casefold(), str(row["external_entity_id"])): int(row["resolved_variant_id"])
            for row in cursor.fetchall()
        }
        for card in provisional:
            source_ref = (
                str(card["canonicalSourceCode"]).casefold(),
                str(card["canonicalExternalId"]),
            )
            variant_id = by_source.get(source_ref)
            if variant_id is not None:
                resolved[str(card["pokedexId"])] = variant_id
    missing = sorted(set(opaque_ids) - set(resolved))
    if missing:
        raise RuntimeError(
            f"active-universe catalog identities are missing: {missing[:5]}"
        )
    return resolved


def status(connection: Connection, active_document: Mapping[str, Any]) -> dict[str, Any]:
    cards = validate_active_universe(active_document)
    tables = (
        "catalog_variant", "catalog_variant_alias", "catalog_provider_identity_alias", "market_universe_member", "market_ingest_run", "market_source_observation",
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
        resolved = resolved_universe_variants(cursor, cards)
        expected = len(set(resolved.values()))
        if (
            int(current["member_count"]) != expected
            or int(counts["members"]) != expected
            or int(counts["variants"]) != expected
        ):
            raise RuntimeError("database active-universe integrity gate failed")
        formal_cards = active_document.get("cards")
        monitoring = active_document.get("monitoringCandidates", [])
        formal_source = formal_cards if isinstance(formal_cards, list) else cards
        monitoring_source = monitoring if isinstance(monitoring, list) else []
        formal_members = len(
            {
                resolved[str(card["pokedexId"])]
                for card in formal_source
            }
        )
        monitoring_members = len(
            {
                resolved[str(card["pokedexId"])]
                for card in monitoring_source
            }
        )
        result["activeUniverse"] = {
            "members": expected,
            "sourceEntries": len(cards),
            "aliasesCollapsed": len(cards) - expected,
            "formalMembers": formal_members,
            "formalSourceEntries": len(formal_source),
            "monitoringMembers": monitoring_members,
            "monitoringSourceEntries": len(monitoring_source),
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
    load_db_env()
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
    printing_plan_parser = sub.add_parser(
        "printing-plan",
        help="build one immutable receipt-driven canonical printing candidate",
    )
    add_connection_args(printing_plan_parser)
    printing_plan_parser.add_argument(
        "--decision-root", type=Path, default=DEFAULT_PRINTING_DECISIONS
    )
    printing_plan_parser.add_argument("--qc-root", type=Path, default=DEFAULT_CANONICAL_DB_QC)
    printing_plan_parser.add_argument("--qc-run", required=True)
    printing_plan_parser.add_argument(
        "--quarantine-root", type=Path, default=DEFAULT_IDENTITY_QUARANTINE
    )
    printing_plan_parser.add_argument(
        "--output-root", type=Path, default=DEFAULT_PRINTING_CANDIDATES
    )
    printing_materialize_parser = sub.add_parser(
        "printing-materialize",
        help="validate or transactionally materialize an exact printing plan hash",
    )
    add_connection_args(printing_materialize_parser)
    printing_materialize_parser.add_argument("--candidate", type=Path, required=True)
    printing_materialize_parser.add_argument("--plan-sha256", required=True)
    printing_materialize_parser.add_argument(
        "--decision-root", type=Path, default=DEFAULT_PRINTING_DECISIONS
    )
    printing_materialize_parser.add_argument(
        "--qc-root", type=Path, default=DEFAULT_CANONICAL_DB_QC
    )
    printing_materialize_parser.add_argument(
        "--quarantine-root", type=Path, default=DEFAULT_IDENTITY_QUARANTINE
    )
    printing_materialize_parser.add_argument(
        "--apply",
        action="store_true",
        help="commit the transaction; without this flag the exact plan is rolled back",
    )
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
        elif args.command == "printing-plan":
            document = build_printing_plan(
                connection,
                decision_root=args.decision_root.resolve(),
                qc_root=args.qc_root.resolve(),
                qc_run=args.qc_run,
                quarantine_root=args.quarantine_root.resolve(),
            )
            connection.rollback()
            candidate_path = write_printing_candidate(
                document, args.output_root.resolve()
            )
            report = {
                "action": "printing-plan",
                "status": "complete",
                "databaseReadOnly": True,
                "candidatePath": _portable_path(candidate_path),
                "planSha256": document["planSha256"],
                "qcRunId": document["qcRunId"],
                "candidateStatus": document["status"],
                "counts": document["counts"],
            }
        elif args.command == "printing-materialize":
            candidate = read_json(args.candidate.resolve())
            if not isinstance(candidate, Mapping):
                raise ValueError("printing candidate is invalid")
            report = {
                "action": "printing-materialize",
                "status": "complete",
                "candidatePath": _portable_path(args.candidate.resolve()),
                "qcRunId": candidate.get("qcRunId"),
                **materialize_printing_candidate(
                    connection,
                    candidate,
                    expected_plan_sha256=args.plan_sha256,
                    commit=args.apply,
                    decision_root=args.decision_root.resolve(),
                    qc_root=args.qc_root.resolve(),
                    quarantine_root=args.quarantine_root.resolve(),
                ),
            }
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
