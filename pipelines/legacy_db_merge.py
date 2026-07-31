#!/usr/bin/env python3
"""RETIRED: legacy CARDZ/Kado merge is outside Cardz Market Cap.

The source schema must be restored on the same MySQL server as the canonical
schema.  The command is dry-run by default.  Applying a plan requires the exact
SHA-256 printed by the dry-run.

The merge keeps every legacy source row that is not already represented:

* legacy variants with an existing provider identity become inactive alias
  tombstones and their facts are mapped to the canonical survivor;
* genuinely new variants and their exact source identities are inserted;
* i18n names, set names, and market stories are merged by locale;
* ingest runs, raw evidence, normalized facts, historical universe/evaluation/
  index rows, alerts, images, and checkpoints retain their original provenance;
* canonical-only tables (including story source pointers and effective
  observation pointers) are preserved.

Existing canonical facts always win a logical-key collision.  Apply refuses to
run if the legacy row is newer than the canonical collision, so a newer fact can
never be silently discarded.

Migration 015 removed ``card_language`` from the canonical schema.  This
historical importer is intentionally blocked instead of being adapted: Kado and
JLP are not Cardz Market Cap sources.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args  # noqa: E402


SCHEMA_NAME = re.compile(r"[A-Za-z0-9_]+")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
LOCKS = (
    "cardz_market_cap_import",
    "cardz_market_alert_evaluation",
    "cardz.identity_convergence.v1",
    "cardz.legacy_db_merge.v1",
)
SOURCE_TABLES = (
    "cardz_schema_version",
    "catalog_source_identity",
    "catalog_variant",
    "catalog_variant_locale",
    "market_alert",
    "market_alert_evaluation",
    "market_alert_event",
    "market_candidate_daily_snapshot",
    "market_daily_sales_aggregate",
    "market_fx_rate_observation",
    "market_grader_population_observation",
    "market_identity_review_queue",
    "market_image_asset",
    "market_image_qc",
    "market_index_constituent",
    "market_index_snapshot",
    "market_ingest_checkpoint",
    "market_ingest_run",
    "market_price_observation",
    "market_sale_observation",
    "market_source_observation",
    "market_tracked_sales_aggregate",
    "market_universe_lock",
    "market_universe_member",
)
CANONICAL_ONLY_PRESERVED = (
    "catalog_printing_identity",
    "catalog_provider_identity_alias",
    "catalog_story_pointer",
    "catalog_variant_alias",
    "market_population_transport_observation",
    "market_raw_payload_object",
    "market_source_effective_observation",
    "market_source_observation_payload_pointer",
)


@dataclass(frozen=True)
class VariantPlan:
    old_variant_id: int
    opaque_id: str
    action: str
    target_variant_id: int | None
    imported_variant_id: int | None
    identity_target_ids: tuple[int, ...]
    metadata: Mapping[str, Any]


def canonical_json(value: Any) -> bytes:
    def encode(item: Any) -> Any:
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        if isinstance(item, Decimal):
            return str(item)
        raise TypeError(f"unsupported canonical JSON type: {type(item).__name__}")

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=encode,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def validate_schema_name(value: str) -> str:
    if not SCHEMA_NAME.fullmatch(value):
        raise ValueError(f"invalid MySQL schema name: {value!r}")
    return value


def quote_identifier(value: str) -> str:
    return f"`{validate_schema_name(value)}`"


def acquire_locks(cursor: Any) -> list[str]:
    acquired: list[str] = []
    for name in LOCKS:
        cursor.execute("SELECT GET_LOCK(%s, 0) AS acquired", (name,))
        row = cursor.fetchone()
        if not isinstance(row, Mapping) or int(row.get("acquired") or 0) != 1:
            for held in reversed(acquired):
                cursor.execute("SELECT RELEASE_LOCK(%s)", (held,))
            raise RuntimeError(f"database writer lock is busy: {name}")
        acquired.append(name)
    return acquired


def release_locks(cursor: Any, acquired: Sequence[str]) -> None:
    for name in reversed(acquired):
        cursor.execute("SELECT RELEASE_LOCK(%s)", (name,))


def assert_schemas(cursor: Any, source_schema: str, canonical_schema: str) -> None:
    if source_schema == canonical_schema:
        raise RuntimeError("legacy source schema must not be the canonical schema")
    cursor.execute(
        """
        SELECT table_schema AS schema_name, table_name AS table_name
        FROM information_schema.tables
        WHERE table_schema IN (%s, %s)
        """,
        (source_schema, canonical_schema),
    )
    found: dict[str, set[str]] = {source_schema: set(), canonical_schema: set()}
    for row in cursor.fetchall():
        found.setdefault(str(row["schema_name"]), set()).add(str(row["table_name"]))
    missing_source = sorted(set(SOURCE_TABLES) - found.get(source_schema, set()))
    if missing_source:
        raise RuntimeError(f"legacy schema is incomplete: {missing_source}")
    required_canonical = set(SOURCE_TABLES) | {
        "catalog_variant_alias",
        "market_source_effective_observation",
    }
    missing_canonical = sorted(required_canonical - found.get(canonical_schema, set()))
    if missing_canonical:
        raise RuntimeError(f"canonical schema is incomplete: {missing_canonical}")


def table_counts(cursor: Any, source_schema: str) -> dict[str, int]:
    source = quote_identifier(source_schema)
    counts: dict[str, int] = {}
    for table in SOURCE_TABLES:
        cursor.execute(f"SELECT COUNT(*) AS n FROM {source}.`{table}`")
        counts[table] = int(cursor.fetchone()["n"])
    return counts


def common_columns(
    cursor: Any,
    source_schema: str,
    canonical_schema: str,
    table: str,
) -> list[str]:
    cursor.execute(
        """
        SELECT column_name AS column_name, extra AS extra
        FROM information_schema.columns
        WHERE table_schema=%s AND table_name=%s
        ORDER BY ordinal_position
        """,
        (canonical_schema, table),
    )
    canonical = {
        str(row["column_name"]): str(row.get("extra") or "")
        for row in cursor.fetchall()
    }
    cursor.execute(
        """
        SELECT column_name AS column_name
        FROM information_schema.columns
        WHERE table_schema=%s AND table_name=%s
        ORDER BY ordinal_position
        """,
        (source_schema, table),
    )
    source = [str(row["column_name"]) for row in cursor.fetchall()]
    return [
        column
        for column in source
        if column in canonical and "auto_increment" not in canonical[column]
    ]


def identity_targets(
    cursor: Any,
    source_schema: str,
    old_variant_id: int,
) -> tuple[int, ...]:
    source = quote_identifier(source_schema)
    cursor.execute(
        f"""
        SELECT DISTINCT canonical_identity.variant_id
        FROM {source}.catalog_source_identity AS legacy_identity
        JOIN catalog_source_identity AS canonical_identity
          ON canonical_identity.source_code=legacy_identity.source_code
         AND canonical_identity.external_entity_id=legacy_identity.external_entity_id
        WHERE legacy_identity.variant_id=%s
        ORDER BY canonical_identity.variant_id
        """,
        (old_variant_id,),
    )
    return tuple(int(row["variant_id"]) for row in cursor.fetchall())


def load_variant_plan(cursor: Any, source_schema: str) -> list[VariantPlan]:
    source = quote_identifier(source_schema)
    cursor.execute(
        f"""
        SELECT
            legacy_variant.*,
            canonical_variant.id AS imported_variant_id,
            COALESCE(alias.canonical_variant_id, canonical_variant.id) AS opaque_target_id
        FROM {source}.catalog_variant AS legacy_variant
        LEFT JOIN catalog_variant AS canonical_variant
          ON canonical_variant.opaque_id=legacy_variant.opaque_id
        LEFT JOIN catalog_variant_alias AS alias
          ON alias.duplicate_variant_id=canonical_variant.id
        ORDER BY legacy_variant.id
        """
    )
    plans: list[VariantPlan] = []
    for raw in cursor.fetchall():
        row = dict(raw)
        old_id = int(row["id"])
        targets = identity_targets(cursor, source_schema, old_id)
        if len(targets) > 1:
            raise RuntimeError(
                f"legacy variant {old_id} identities point to multiple canonical variants: {targets}"
            )
        opaque_target = (
            int(row["opaque_target_id"])
            if row.get("opaque_target_id") is not None
            else None
        )
        identity_target = targets[0] if targets else None
        if (
            opaque_target is not None
            and identity_target is not None
            and opaque_target != identity_target
        ):
            raise RuntimeError(
                f"legacy variant {old_id} opaque identity maps to {opaque_target}, "
                f"provider identity maps to {identity_target}"
            )
        imported_id = (
            int(row["imported_variant_id"])
            if row.get("imported_variant_id") is not None
            else None
        )
        if imported_id is not None:
            action = "existing"
            target = opaque_target
        elif identity_target is not None:
            action = "alias_tombstone"
            target = identity_target
        else:
            action = "new_variant"
            target = None
        metadata = {
            key: row[key]
            for key in (
                "opaque_id",
                "tcg_code",
                "card_language",
                "canonical_name",
                "set_name",
                "collector_number",
                "identity_status",
                "created_at",
                "updated_at",
            )
        }
        plans.append(
            VariantPlan(
                old_variant_id=old_id,
                opaque_id=str(row["opaque_id"]),
                action=action,
                target_variant_id=target,
                imported_variant_id=imported_id,
                identity_target_ids=targets,
                metadata=metadata,
            )
        )
    return plans


def create_variant_map(cursor: Any, plans: Sequence[VariantPlan]) -> None:
    cursor.execute("DROP TEMPORARY TABLE IF EXISTS tmp_legacy_variant_map")
    cursor.execute(
        """
        CREATE TEMPORARY TABLE tmp_legacy_variant_map (
            old_variant_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
            target_variant_id BIGINT UNSIGNED NULL,
            imported_variant_id BIGINT UNSIGNED NULL,
            action_code VARCHAR(24) NOT NULL,
            opaque_id VARCHAR(96) NOT NULL
        ) ENGINE=InnoDB
        """
    )
    cursor.executemany(
        """
        INSERT INTO tmp_legacy_variant_map
            (old_variant_id, target_variant_id, imported_variant_id, action_code, opaque_id)
        VALUES (%s, %s, %s, %s, %s)
        """,
        [
            (
                plan.old_variant_id,
                plan.target_variant_id,
                plan.imported_variant_id,
                plan.action,
                plan.opaque_id,
            )
            for plan in plans
        ],
    )


def create_natural_key_maps(cursor: Any, source_schema: str) -> None:
    source = quote_identifier(source_schema)
    definitions = (
        (
            "tmp_legacy_run_map",
            """
            old_run_id BIGINT UNSIGNED PRIMARY KEY,
            target_run_id BIGINT UNSIGNED NULL
            """,
            f"""
            SELECT legacy.id, canonical.id
            FROM {source}.market_ingest_run AS legacy
            LEFT JOIN market_ingest_run AS canonical ON canonical.run_key=legacy.run_key
            """,
        ),
        (
            "tmp_legacy_lock_map",
            """
            old_lock_id BIGINT UNSIGNED PRIMARY KEY,
            target_lock_id BIGINT UNSIGNED NULL
            """,
            f"""
            SELECT legacy.id, canonical.id
            FROM {source}.market_universe_lock AS legacy
            LEFT JOIN market_universe_lock AS canonical
              ON canonical.lock_sha256=legacy.lock_sha256
            """,
        ),
        (
            "tmp_legacy_eval_map",
            """
            old_evaluation_id BIGINT UNSIGNED PRIMARY KEY,
            target_evaluation_id BIGINT UNSIGNED NULL
            """,
            f"""
            SELECT legacy.id, canonical.id
            FROM {source}.market_alert_evaluation AS legacy
            LEFT JOIN market_alert_evaluation AS canonical
              ON canonical.index_code=legacy.index_code
             AND canonical.index_version=legacy.index_version
             AND canonical.effective_date=legacy.effective_date
             AND canonical.policy_version=legacy.policy_version
             AND canonical.input_sha256=legacy.input_sha256
            """,
        ),
        (
            "tmp_legacy_index_map",
            """
            old_snapshot_id BIGINT UNSIGNED PRIMARY KEY,
            target_snapshot_id BIGINT UNSIGNED NULL
            """,
            f"""
            SELECT legacy.id, canonical.id
            FROM {source}.market_index_snapshot AS legacy
            LEFT JOIN market_index_snapshot AS canonical
              ON canonical.index_code=legacy.index_code
             AND canonical.index_version=legacy.index_version
             AND canonical.effective_date=legacy.effective_date
            """,
        ),
        (
            "tmp_legacy_asset_map",
            """
            old_asset_id BIGINT UNSIGNED PRIMARY KEY,
            target_asset_id BIGINT UNSIGNED NULL
            """,
            f"""
            SELECT legacy.id, canonical.id
            FROM {source}.market_image_asset AS legacy
            JOIN tmp_legacy_variant_map AS variant_map
              ON variant_map.old_variant_id=legacy.variant_id
            LEFT JOIN market_image_asset AS canonical
              ON canonical.variant_id=variant_map.target_variant_id
             AND canonical.image_kind=legacy.image_kind
             AND canonical.content_sha256=legacy.content_sha256
            """,
        ),
        (
            "tmp_legacy_candidate_map",
            """
            old_candidate_id BIGINT UNSIGNED PRIMARY KEY,
            target_candidate_id BIGINT UNSIGNED NULL
            """,
            f"""
            SELECT legacy.id, canonical.id
            FROM {source}.market_candidate_daily_snapshot AS legacy
            JOIN tmp_legacy_eval_map AS evaluation_map
              ON evaluation_map.old_evaluation_id=legacy.evaluation_id
            JOIN tmp_legacy_variant_map AS variant_map
              ON variant_map.old_variant_id=legacy.variant_id
            LEFT JOIN market_candidate_daily_snapshot AS canonical
              ON canonical.evaluation_id=evaluation_map.target_evaluation_id
             AND canonical.variant_id=variant_map.target_variant_id
            """,
        ),
        (
            "tmp_legacy_alert_map",
            """
            old_alert_id BIGINT UNSIGNED PRIMARY KEY,
            target_alert_id BIGINT UNSIGNED NULL
            """,
            f"""
            SELECT legacy.id, canonical.id
            FROM {source}.market_alert AS legacy
            LEFT JOIN market_alert AS canonical
              ON canonical.active_dedupe_key=legacy.active_dedupe_key
            """,
        ),
    )
    for name, columns, select_sql in definitions:
        cursor.execute(f"DROP TEMPORARY TABLE IF EXISTS {name}")
        cursor.execute(f"CREATE TEMPORARY TABLE {name} ({columns}) ENGINE=InnoDB")
        cursor.execute(f"INSERT INTO {name} {select_sql}")


def scalar(cursor: Any, sql: str, params: Sequence[Any] = ()) -> int:
    cursor.execute(sql, params)
    row = cursor.fetchone()
    return int(next(iter(row.values())) if isinstance(row, Mapping) else row[0])


def collision_guards(cursor: Any, source_schema: str) -> dict[str, int]:
    source = quote_identifier(source_schema)
    return {
        "opaqueMetadataConflict": scalar(
            cursor,
            f"""
            SELECT COUNT(*)
            FROM {source}.catalog_variant AS legacy
            JOIN catalog_variant AS imported ON imported.opaque_id=legacy.opaque_id
            WHERE imported.tcg_code<>legacy.tcg_code
               OR imported.card_language<>legacy.card_language
               OR imported.canonical_name<>legacy.canonical_name
               OR imported.set_name<>legacy.set_name
               OR imported.collector_number<>legacy.collector_number
            """,
        ),
        "providerIdentityConflict": scalar(
            cursor,
            f"""
            SELECT COUNT(*)
            FROM {source}.catalog_source_identity AS legacy
            JOIN tmp_legacy_variant_map AS variant_map
              ON variant_map.old_variant_id=legacy.variant_id
            JOIN catalog_source_identity AS canonical
              ON canonical.source_code=legacy.source_code
             AND canonical.external_entity_id=legacy.external_entity_id
            WHERE canonical.variant_id<>variant_map.target_variant_id
            """,
        ),
        "ingestRunConflict": scalar(
            cursor,
            f"""
            SELECT COUNT(*)
            FROM {source}.market_ingest_run AS legacy
            JOIN market_ingest_run AS canonical ON canonical.run_key=legacy.run_key
            WHERE canonical.source_code<>legacy.source_code
               OR canonical.payload_sha256<>legacy.payload_sha256
               OR canonical.manifest_sha256<>legacy.manifest_sha256
            """,
        ),
        "legacyPriceNewer": scalar(
            cursor,
            f"""
            SELECT COUNT(*)
            FROM {source}.market_price_observation AS legacy
            JOIN tmp_legacy_variant_map AS variant_map
              ON variant_map.old_variant_id=legacy.variant_id
            JOIN market_price_observation AS canonical
              ON canonical.variant_id=variant_map.target_variant_id
             AND canonical.source_code=legacy.source_code
             AND canonical.observed_date=legacy.observed_date
            WHERE legacy.effective_at>canonical.effective_at
            """,
        ),
        "legacyPopulationNewer": scalar(
            cursor,
            f"""
            SELECT COUNT(*)
            FROM {source}.market_grader_population_observation AS legacy
            JOIN tmp_legacy_variant_map AS variant_map
              ON variant_map.old_variant_id=legacy.variant_id
            JOIN market_grader_population_observation AS canonical
              ON canonical.variant_id=variant_map.target_variant_id
             AND canonical.grader_code=legacy.grader_code
             AND canonical.source_code=legacy.source_code
             AND canonical.observed_date=legacy.observed_date
            WHERE legacy.effective_at>canonical.effective_at
            """,
        ),
        "legacyDailySalesNewer": scalar(
            cursor,
            f"""
            SELECT COUNT(*)
            FROM {source}.market_daily_sales_aggregate AS legacy
            JOIN tmp_legacy_variant_map AS variant_map
              ON variant_map.old_variant_id=legacy.variant_id
            JOIN market_daily_sales_aggregate AS canonical
              ON canonical.variant_id=variant_map.target_variant_id
             AND canonical.source_code=legacy.source_code
             AND canonical.observed_date=legacy.observed_date
            WHERE legacy.created_at>canonical.created_at
              AND legacy.payload_sha256<>canonical.payload_sha256
            """,
        ),
        "legacyCheckpointNewer": scalar(
            cursor,
            f"""
            SELECT COUNT(*)
            FROM {source}.market_ingest_checkpoint AS legacy
            JOIN market_ingest_checkpoint AS canonical
              ON canonical.source_code=legacy.source_code
             AND canonical.stream_key=legacy.stream_key
            WHERE legacy.last_effective_at>canonical.last_effective_at
            """,
        ),
        "legacyAlertWithoutDedupe": scalar(
            cursor,
            f"""
            SELECT COUNT(*)
            FROM {source}.market_alert
            WHERE active_dedupe_key IS NULL
            """,
        ),
    }


def missing_counts(
    cursor: Any,
    source_schema: str,
    plans: Sequence[VariantPlan],
) -> dict[str, int]:
    source = quote_identifier(source_schema)
    counts: dict[str, int] = {
        "catalog_variant": sum(plan.action != "existing" for plan in plans),
        "catalog_variant_alias": sum(plan.action == "alias_tombstone" for plan in plans),
        "catalog_source_identity": scalar(
            cursor,
            f"""
            SELECT COUNT(*)
            FROM {source}.catalog_source_identity AS legacy
            LEFT JOIN catalog_source_identity AS canonical
              ON canonical.source_code=legacy.source_code
             AND canonical.external_entity_id=legacy.external_entity_id
            WHERE canonical.source_code IS NULL
            """,
        ),
        "catalog_variant_locale": scalar(
            cursor,
            f"""
            SELECT COUNT(*)
            FROM {source}.catalog_variant_locale AS legacy
            JOIN tmp_legacy_variant_map AS variant_map
              ON variant_map.old_variant_id=legacy.variant_id
            LEFT JOIN catalog_variant_locale AS canonical
              ON canonical.variant_id=variant_map.target_variant_id
             AND canonical.locale_code=legacy.locale_code
            WHERE canonical.variant_id IS NULL
            """,
        ),
        "market_ingest_run": scalar(
            cursor,
            "SELECT COUNT(*) FROM tmp_legacy_run_map WHERE target_run_id IS NULL",
        ),
        "market_universe_lock": scalar(
            cursor,
            "SELECT COUNT(*) FROM tmp_legacy_lock_map WHERE target_lock_id IS NULL",
        ),
        "market_alert_evaluation": scalar(
            cursor,
            "SELECT COUNT(*) FROM tmp_legacy_eval_map WHERE target_evaluation_id IS NULL",
        ),
        "market_index_snapshot": scalar(
            cursor,
            "SELECT COUNT(*) FROM tmp_legacy_index_map WHERE target_snapshot_id IS NULL",
        ),
        "market_image_asset": scalar(
            cursor,
            "SELECT COUNT(*) FROM tmp_legacy_asset_map WHERE target_asset_id IS NULL",
        ),
        "market_candidate_daily_snapshot": scalar(
            cursor,
            "SELECT COUNT(*) FROM tmp_legacy_candidate_map WHERE target_candidate_id IS NULL",
        ),
        "market_alert": scalar(
            cursor,
            "SELECT COUNT(*) FROM tmp_legacy_alert_map WHERE target_alert_id IS NULL",
        ),
    }
    keyed_counts = {
        "market_source_observation": f"""
            SELECT COUNT(*)
            FROM {source}.market_source_observation AS legacy
            LEFT JOIN market_source_observation AS canonical
              ON canonical.source_code=legacy.source_code
             AND canonical.external_entity_id=legacy.external_entity_id
             AND canonical.observation_kind=legacy.observation_kind
             AND canonical.observed_date=legacy.observed_date
             AND canonical.payload_sha256=legacy.payload_sha256
            WHERE canonical.id IS NULL
        """,
        "market_price_observation": f"""
            SELECT COUNT(*)
            FROM {source}.market_price_observation AS legacy
            JOIN tmp_legacy_variant_map AS variant_map
              ON variant_map.old_variant_id=legacy.variant_id
            LEFT JOIN market_price_observation AS canonical
              ON canonical.variant_id=variant_map.target_variant_id
             AND canonical.source_code=legacy.source_code
             AND canonical.observed_date=legacy.observed_date
            WHERE canonical.id IS NULL
        """,
        "market_grader_population_observation": f"""
            SELECT COUNT(*)
            FROM {source}.market_grader_population_observation AS legacy
            JOIN tmp_legacy_variant_map AS variant_map
              ON variant_map.old_variant_id=legacy.variant_id
            LEFT JOIN market_grader_population_observation AS canonical
              ON canonical.variant_id=variant_map.target_variant_id
             AND canonical.grader_code=legacy.grader_code
             AND canonical.source_code=legacy.source_code
             AND canonical.observed_date=legacy.observed_date
            WHERE canonical.id IS NULL
        """,
        "market_daily_sales_aggregate": f"""
            SELECT COUNT(*)
            FROM {source}.market_daily_sales_aggregate AS legacy
            JOIN tmp_legacy_variant_map AS variant_map
              ON variant_map.old_variant_id=legacy.variant_id
            LEFT JOIN market_daily_sales_aggregate AS canonical
              ON canonical.variant_id=variant_map.target_variant_id
             AND canonical.source_code=legacy.source_code
             AND canonical.observed_date=legacy.observed_date
            WHERE canonical.id IS NULL
        """,
        "market_fx_rate_observation": f"""
            SELECT COUNT(*)
            FROM {source}.market_fx_rate_observation AS legacy
            LEFT JOIN market_fx_rate_observation AS canonical
              ON canonical.base_currency=legacy.base_currency
             AND canonical.quote_currency=legacy.quote_currency
             AND canonical.effective_date=legacy.effective_date
            WHERE canonical.id IS NULL
        """,
        "market_sale_observation": f"""
            SELECT COUNT(*)
            FROM {source}.market_sale_observation AS legacy
            LEFT JOIN market_sale_observation AS canonical
              ON canonical.source_code=legacy.source_code
             AND canonical.external_entity_id=legacy.external_entity_id
             AND canonical.transaction_fingerprint=legacy.transaction_fingerprint
            WHERE canonical.id IS NULL
        """,
        "market_tracked_sales_aggregate": f"""
            SELECT COUNT(*)
            FROM {source}.market_tracked_sales_aggregate AS legacy
            JOIN tmp_legacy_variant_map AS variant_map
              ON variant_map.old_variant_id=legacy.variant_id
            LEFT JOIN market_tracked_sales_aggregate AS canonical
              ON canonical.variant_id=variant_map.target_variant_id
             AND canonical.grader_code=legacy.grader_code
             AND canonical.grade_label=legacy.grade_label
             AND canonical.window_code=legacy.window_code
             AND canonical.window_end_at=legacy.window_end_at
             AND canonical.aggregate_sha256=legacy.aggregate_sha256
            WHERE canonical.id IS NULL
        """,
        "market_identity_review_queue": f"""
            SELECT COUNT(*)
            FROM {source}.market_identity_review_queue AS legacy
            LEFT JOIN market_identity_review_queue AS canonical
              ON canonical.source_code=legacy.source_code
             AND canonical.external_entity_id=legacy.external_entity_id
             AND canonical.reason_code=legacy.reason_code
             AND canonical.evidence_sha256=legacy.evidence_sha256
            WHERE canonical.id IS NULL
        """,
        "market_image_qc": f"""
            SELECT COUNT(*)
            FROM {source}.market_image_qc AS legacy
            JOIN tmp_legacy_asset_map AS asset_map
              ON asset_map.old_asset_id=legacy.image_asset_id
            LEFT JOIN market_image_qc AS canonical
              ON canonical.image_asset_id=asset_map.target_asset_id
             AND canonical.qc_version=legacy.qc_version
            WHERE canonical.id IS NULL
        """,
        "market_universe_member": f"""
            SELECT COUNT(*)
            FROM {source}.market_universe_member AS legacy
            JOIN tmp_legacy_lock_map AS lock_map
              ON lock_map.old_lock_id=legacy.universe_lock_id
            JOIN tmp_legacy_variant_map AS variant_map
              ON variant_map.old_variant_id=legacy.variant_id
            LEFT JOIN market_universe_member AS canonical
              ON canonical.universe_lock_id=lock_map.target_lock_id
             AND canonical.variant_id=variant_map.target_variant_id
            WHERE canonical.variant_id IS NULL
        """,
        "market_index_constituent": f"""
            SELECT COUNT(*)
            FROM {source}.market_index_constituent AS legacy
            JOIN tmp_legacy_index_map AS snapshot_map
              ON snapshot_map.old_snapshot_id=legacy.index_snapshot_id
            JOIN tmp_legacy_variant_map AS variant_map
              ON variant_map.old_variant_id=legacy.variant_id
            LEFT JOIN market_index_constituent AS canonical
              ON canonical.index_snapshot_id=snapshot_map.target_snapshot_id
             AND canonical.variant_id=variant_map.target_variant_id
            WHERE canonical.variant_id IS NULL
        """,
        "market_alert_event": f"""
            SELECT COUNT(*)
            FROM {source}.market_alert_event AS legacy
            LEFT JOIN market_alert_event AS canonical
              ON canonical.event_key=legacy.event_key
            WHERE canonical.id IS NULL
        """,
        "market_ingest_checkpoint": f"""
            SELECT COUNT(*)
            FROM {source}.market_ingest_checkpoint AS legacy
            LEFT JOIN market_ingest_checkpoint AS canonical
              ON canonical.source_code=legacy.source_code
             AND canonical.stream_key=legacy.stream_key
            WHERE canonical.source_code IS NULL
        """,
    }
    for table, query in keyed_counts.items():
        counts[table] = scalar(cursor, query)
    return dict(sorted(counts.items()))


def build_plan(
    cursor: Any,
    source_schema: str,
    canonical_schema: str,
    source_dump_sha256: str,
) -> tuple[dict[str, Any], list[VariantPlan]]:
    plans = load_variant_plan(cursor, source_schema)
    create_variant_map(cursor, plans)
    create_natural_key_maps(cursor, source_schema)
    guards = collision_guards(cursor, source_schema)
    plan = {
        "schemaVersion": "1.0.0",
        "sourceSchema": source_schema,
        "canonicalSchema": canonical_schema,
        "sourceDumpSha256": source_dump_sha256,
        "sourceTableRows": table_counts(cursor, source_schema),
        "variantActions": [
            {
                "oldVariantId": item.old_variant_id,
                "opaqueId": item.opaque_id,
                "action": item.action,
                "targetVariantId": item.target_variant_id,
                "identityTargetIds": list(item.identity_target_ids),
                "metadata": dict(item.metadata),
            }
            for item in plans
            if item.action != "existing"
        ],
        "missingRows": missing_counts(cursor, source_schema, plans),
        "collisionGuards": guards,
        "canonicalOnlyTablesPreserved": list(CANONICAL_ONLY_PRESERVED),
        "policy": {
            "existingLogicalKey": "canonical_wins",
            "legacyNewerCollision": "fail_closed",
            "legacyCurrentUniverse": "historical_only",
            "rawEvidence": "append_immutable",
            "i18n": "merge_by_variant_and_locale",
        },
    }
    plan["planSha256"] = sha256_json(plan)
    return plan, plans


def assert_apply_safe(plan: Mapping[str, Any], expected_sha256: str) -> None:
    if not SHA256_HEX.fullmatch(expected_sha256):
        raise ValueError("--plan-sha256 must be a lowercase SHA-256")
    if str(plan["planSha256"]) != expected_sha256:
        raise RuntimeError(
            f"plan hash mismatch: expected {expected_sha256}, actual {plan['planSha256']}"
        )
    nonzero = {
        key: int(value)
        for key, value in dict(plan["collisionGuards"]).items()
        if int(value) != 0
    }
    if nonzero:
        raise RuntimeError(f"legacy merge has unsafe collisions: {nonzero}")


def refresh_maps(cursor: Any, source_schema: str) -> None:
    cursor.execute(
        f"""
        UPDATE tmp_legacy_variant_map AS variant_map
        JOIN catalog_variant AS imported ON imported.opaque_id=variant_map.opaque_id
        LEFT JOIN catalog_variant_alias AS alias
          ON alias.duplicate_variant_id=imported.id
        SET variant_map.imported_variant_id=imported.id,
            variant_map.target_variant_id=COALESCE(alias.canonical_variant_id, imported.id)
        """
    )
    checks = (
        ("variant target", "tmp_legacy_variant_map", "target_variant_id"),
        ("variant row", "tmp_legacy_variant_map", "imported_variant_id"),
    )
    for label, table, column in checks:
        missing = scalar(cursor, f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL")
        if missing:
            raise RuntimeError(f"{missing} legacy {label} mappings are unresolved")
def insert_from_source(
    cursor: Any,
    source_schema: str,
    canonical_schema: str,
    table: str,
    *,
    joins: str = "",
    overrides: Mapping[str, str] | None = None,
) -> int:
    source = quote_identifier(source_schema)
    columns = common_columns(cursor, source_schema, canonical_schema, table)
    override_map = dict(overrides or {})
    targets = ", ".join(f"`{column}`" for column in columns)
    expressions = ", ".join(
        override_map.get(column, f"legacy.`{column}`")
        for column in columns
    )
    cursor.execute(
        f"""
        INSERT IGNORE INTO `{table}` ({targets})
        SELECT {expressions}
        FROM {source}.`{table}` AS legacy
        {joins}
        """
    )
    return int(cursor.rowcount)


def insert_variants_and_aliases(
    cursor: Any,
    source_schema: str,
    plans: Sequence[VariantPlan],
    plan_sha256: str,
) -> dict[str, int]:
    source = quote_identifier(source_schema)
    inserted = 0
    aliases = 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for item in plans:
        if item.action == "existing":
            continue
        identity_status = (
            "duplicate"
            if item.action == "alias_tombstone"
            else str(item.metadata["identity_status"])
        )
        cursor.execute(
            f"""
            INSERT INTO catalog_variant
                (opaque_id, tcg_code, card_language, canonical_name, set_name,
                 collector_number, identity_status, created_at, updated_at)
            SELECT opaque_id, tcg_code, card_language, canonical_name, set_name,
                   collector_number, %s, created_at, updated_at
            FROM {source}.catalog_variant
            WHERE id=%s
            """,
            (identity_status, item.old_variant_id),
        )
        inserted += int(cursor.rowcount)
        imported_id = int(cursor.lastrowid)
        if item.action == "alias_tombstone":
            if item.target_variant_id is None:
                raise RuntimeError("alias tombstone has no canonical target")
            evidence = sha256_json(
                {
                    "sourceSchema": source_schema,
                    "oldVariantId": item.old_variant_id,
                    "opaqueId": item.opaque_id,
                    "identityTargetIds": list(item.identity_target_ids),
                }
            )
            cursor.execute(
                """
                INSERT INTO catalog_variant_alias
                    (duplicate_variant_id, canonical_variant_id, reason_code,
                     evidence_sha256, convergence_plan_sha256, merged_at)
                VALUES (%s, %s, 'legacy_wsl_identity_match', %s, %s, %s)
                """,
                (
                    imported_id,
                    item.target_variant_id,
                    evidence,
                    plan_sha256,
                    now,
                ),
            )
            aliases += int(cursor.rowcount)
    return {"variantsInserted": inserted, "aliasesInserted": aliases}


def backfill_effective_pointers(cursor: Any, source_schema: str) -> int:
    source = quote_identifier(source_schema)
    cursor.execute(
        f"""
        INSERT INTO market_source_effective_observation
            (source_code, external_entity_id, observation_kind, observed_date,
             observation_id, effective_at, selected_at)
        SELECT ranked.source_code, ranked.external_entity_id,
               ranked.observation_kind, ranked.observed_date,
               ranked.id, ranked.effective_at, UTC_TIMESTAMP(6)
        FROM (
            SELECT source.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY source.source_code, source.external_entity_id,
                                    source.observation_kind, source.observed_date
                       ORDER BY source.effective_at DESC, source.id DESC
                   ) AS winner_rank
            FROM market_source_observation AS source
            JOIN market_ingest_run AS run
              ON run.id=source.run_id AND run.status='complete'
            JOIN (
                SELECT DISTINCT source_code, external_entity_id,
                                observation_kind, observed_date
                FROM {source}.market_source_observation
            ) AS legacy_key
              ON legacy_key.source_code=source.source_code
             AND legacy_key.external_entity_id=source.external_entity_id
             AND legacy_key.observation_kind=source.observation_kind
             AND legacy_key.observed_date=source.observed_date
        ) AS ranked
        LEFT JOIN market_source_effective_observation AS current
          ON current.source_code=ranked.source_code
         AND current.external_entity_id=ranked.external_entity_id
         AND current.observation_kind=ranked.observation_kind
         AND current.observed_date=ranked.observed_date
        WHERE ranked.winner_rank=1
          AND (
              current.observation_id IS NULL
              OR current.observation_id<>ranked.id
              OR current.effective_at<>ranked.effective_at
          )
        ON DUPLICATE KEY UPDATE
            observation_id=VALUES(observation_id),
            effective_at=VALUES(effective_at),
            selected_at=VALUES(selected_at)
        """
    )
    return int(cursor.rowcount)


def apply_plan(
    connection: Any,
    cursor: Any,
    source_schema: str,
    canonical_schema: str,
    plan: Mapping[str, Any],
    plans: Sequence[VariantPlan],
) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    stats.update(
        insert_variants_and_aliases(
            cursor,
            source_schema,
            plans,
            str(plan["planSha256"]),
        )
    )
    refresh_maps(cursor, source_schema)

    stats["catalog_source_identity"] = insert_from_source(
        cursor,
        source_schema,
        canonical_schema,
        "catalog_source_identity",
        joins=(
            "JOIN tmp_legacy_variant_map AS variant_map "
            "ON variant_map.old_variant_id=legacy.variant_id"
        ),
        overrides={"variant_id": "variant_map.target_variant_id"},
    )
    stats["catalog_variant_locale"] = insert_from_source(
        cursor,
        source_schema,
        canonical_schema,
        "catalog_variant_locale",
        joins=(
            "JOIN tmp_legacy_variant_map AS variant_map "
            "ON variant_map.old_variant_id=legacy.variant_id"
        ),
        overrides={"variant_id": "variant_map.target_variant_id"},
    )
    stats["market_ingest_run"] = insert_from_source(
        cursor,
        source_schema,
        canonical_schema,
        "market_ingest_run",
    )
    create_natural_key_maps(cursor, source_schema)
    if scalar(cursor, "SELECT COUNT(*) FROM tmp_legacy_run_map WHERE target_run_id IS NULL"):
        raise RuntimeError("legacy ingest-run mapping is incomplete")

    stats["market_universe_lock"] = insert_from_source(
        cursor,
        source_schema,
        canonical_schema,
        "market_universe_lock",
        overrides={"is_current": "0"},
    )
    create_natural_key_maps(cursor, source_schema)
    if scalar(cursor, "SELECT COUNT(*) FROM tmp_legacy_lock_map WHERE target_lock_id IS NULL"):
        raise RuntimeError("legacy universe-lock mapping is incomplete")
    stats["market_universe_member"] = insert_from_source(
        cursor,
        source_schema,
        canonical_schema,
        "market_universe_member",
        joins=(
            "JOIN tmp_legacy_lock_map AS lock_map "
            "ON lock_map.old_lock_id=legacy.universe_lock_id "
            "JOIN tmp_legacy_variant_map AS variant_map "
            "ON variant_map.old_variant_id=legacy.variant_id"
        ),
        overrides={
            "universe_lock_id": "lock_map.target_lock_id",
            "variant_id": "variant_map.target_variant_id",
        },
    )
    stats["market_alert_evaluation"] = insert_from_source(
        cursor,
        source_schema,
        canonical_schema,
        "market_alert_evaluation",
        joins=(
            "JOIN tmp_legacy_lock_map AS lock_map "
            "ON lock_map.old_lock_id=legacy.universe_lock_id"
        ),
        overrides={"universe_lock_id": "lock_map.target_lock_id"},
    )
    create_natural_key_maps(cursor, source_schema)
    if scalar(
        cursor,
        "SELECT COUNT(*) FROM tmp_legacy_eval_map WHERE target_evaluation_id IS NULL",
    ):
        raise RuntimeError("legacy alert-evaluation mapping is incomplete")
    stats["market_candidate_daily_snapshot"] = insert_from_source(
        cursor,
        source_schema,
        canonical_schema,
        "market_candidate_daily_snapshot",
        joins=(
            "JOIN tmp_legacy_eval_map AS evaluation_map "
            "ON evaluation_map.old_evaluation_id=legacy.evaluation_id "
            "JOIN tmp_legacy_variant_map AS variant_map "
            "ON variant_map.old_variant_id=legacy.variant_id"
        ),
        overrides={
            "evaluation_id": "evaluation_map.target_evaluation_id",
            "variant_id": "variant_map.target_variant_id",
        },
    )
    stats["market_index_snapshot"] = insert_from_source(
        cursor,
        source_schema,
        canonical_schema,
        "market_index_snapshot",
        joins=(
            "JOIN tmp_legacy_run_map AS run_map "
            "ON run_map.old_run_id=legacy.run_id"
        ),
        overrides={"run_id": "run_map.target_run_id"},
    )
    create_natural_key_maps(cursor, source_schema)
    if scalar(
        cursor,
        "SELECT COUNT(*) FROM tmp_legacy_index_map WHERE target_snapshot_id IS NULL",
    ):
        raise RuntimeError("legacy index-snapshot mapping is incomplete")
    stats["market_index_constituent"] = insert_from_source(
        cursor,
        source_schema,
        canonical_schema,
        "market_index_constituent",
        joins=(
            "JOIN tmp_legacy_index_map AS snapshot_map "
            "ON snapshot_map.old_snapshot_id=legacy.index_snapshot_id "
            "JOIN tmp_legacy_variant_map AS variant_map "
            "ON variant_map.old_variant_id=legacy.variant_id"
        ),
        overrides={
            "index_snapshot_id": "snapshot_map.target_snapshot_id",
            "variant_id": "variant_map.target_variant_id",
        },
    )

    run_variant_tables = (
        "market_price_observation",
        "market_grader_population_observation",
        "market_daily_sales_aggregate",
        "market_sale_observation",
    )
    for table in run_variant_tables:
        stats[table] = insert_from_source(
            cursor,
            source_schema,
            canonical_schema,
            table,
            joins=(
                "JOIN tmp_legacy_run_map AS run_map "
                "ON run_map.old_run_id=legacy.run_id "
                "JOIN tmp_legacy_variant_map AS variant_map "
                "ON variant_map.old_variant_id=legacy.variant_id"
            ),
            overrides={
                "run_id": "run_map.target_run_id",
                "variant_id": "variant_map.target_variant_id",
            },
        )
    stats["market_source_observation"] = insert_from_source(
        cursor,
        source_schema,
        canonical_schema,
        "market_source_observation",
        joins=(
            "JOIN tmp_legacy_run_map AS run_map "
            "ON run_map.old_run_id=legacy.run_id"
        ),
        overrides={"run_id": "run_map.target_run_id"},
    )
    stats["market_fx_rate_observation"] = insert_from_source(
        cursor,
        source_schema,
        canonical_schema,
        "market_fx_rate_observation",
        joins=(
            "JOIN tmp_legacy_run_map AS run_map "
            "ON run_map.old_run_id=legacy.run_id"
        ),
        overrides={"run_id": "run_map.target_run_id"},
    )
    stats["market_tracked_sales_aggregate"] = insert_from_source(
        cursor,
        source_schema,
        canonical_schema,
        "market_tracked_sales_aggregate",
        joins=(
            "JOIN tmp_legacy_variant_map AS variant_map "
            "ON variant_map.old_variant_id=legacy.variant_id"
        ),
        overrides={"variant_id": "variant_map.target_variant_id"},
    )
    stats["market_identity_review_queue"] = insert_from_source(
        cursor,
        source_schema,
        canonical_schema,
        "market_identity_review_queue",
        joins=(
            "JOIN tmp_legacy_run_map AS run_map "
            "ON run_map.old_run_id=legacy.run_id "
            "LEFT JOIN tmp_legacy_variant_map AS resolved_variant_map "
            "ON resolved_variant_map.old_variant_id=legacy.resolved_variant_id"
        ),
        overrides={
            "run_id": "run_map.target_run_id",
            "resolved_variant_id": (
                "CASE WHEN legacy.resolved_variant_id IS NULL THEN NULL "
                "ELSE resolved_variant_map.target_variant_id END"
            ),
        },
    )
    stats["market_image_asset"] = insert_from_source(
        cursor,
        source_schema,
        canonical_schema,
        "market_image_asset",
        joins=(
            "JOIN tmp_legacy_variant_map AS variant_map "
            "ON variant_map.old_variant_id=legacy.variant_id"
        ),
        overrides={"variant_id": "variant_map.target_variant_id"},
    )
    create_natural_key_maps(cursor, source_schema)
    stats["market_image_qc"] = insert_from_source(
        cursor,
        source_schema,
        canonical_schema,
        "market_image_qc",
        joins=(
            "JOIN tmp_legacy_asset_map AS asset_map "
            "ON asset_map.old_asset_id=legacy.image_asset_id"
        ),
        overrides={"image_asset_id": "asset_map.target_asset_id"},
    )
    create_natural_key_maps(cursor, source_schema)
    stats["market_alert"] = insert_from_source(
        cursor,
        source_schema,
        canonical_schema,
        "market_alert",
        joins=(
            "LEFT JOIN tmp_legacy_variant_map AS variant_map "
            "ON variant_map.old_variant_id=legacy.variant_id "
            "LEFT JOIN tmp_legacy_candidate_map AS candidate_map "
            "ON candidate_map.old_candidate_id=legacy.latest_snapshot_id"
        ),
        overrides={
            "variant_id": (
                "CASE WHEN legacy.variant_id IS NULL THEN NULL "
                "ELSE variant_map.target_variant_id END"
            ),
            "latest_snapshot_id": (
                "CASE WHEN legacy.latest_snapshot_id IS NULL THEN NULL "
                "ELSE candidate_map.target_candidate_id END"
            ),
        },
    )
    create_natural_key_maps(cursor, source_schema)
    stats["market_alert_event"] = insert_from_source(
        cursor,
        source_schema,
        canonical_schema,
        "market_alert_event",
        joins=(
            "JOIN tmp_legacy_alert_map AS alert_map "
            "ON alert_map.old_alert_id=legacy.alert_id "
            "JOIN tmp_legacy_eval_map AS evaluation_map "
            "ON evaluation_map.old_evaluation_id=legacy.evaluation_id"
        ),
        overrides={
            "alert_id": "alert_map.target_alert_id",
            "evaluation_id": "evaluation_map.target_evaluation_id",
        },
    )
    stats["market_ingest_checkpoint"] = insert_from_source(
        cursor,
        source_schema,
        canonical_schema,
        "market_ingest_checkpoint",
        joins=(
            "JOIN tmp_legacy_run_map AS run_map "
            "ON run_map.old_run_id=legacy.last_run_id"
        ),
        overrides={"last_run_id": "run_map.target_run_id"},
    )
    stats["effectivePointersAffected"] = backfill_effective_pointers(
        cursor,
        source_schema,
    )

    refreshed_plan, _ = build_plan(
        cursor,
        source_schema,
        canonical_schema,
        str(plan["sourceDumpSha256"]),
    )
    residual = {
        key: int(value)
        for key, value in dict(refreshed_plan["missingRows"]).items()
        if int(value) != 0
    }
    if residual:
        raise RuntimeError(f"legacy merge left residual rows: {residual}")
    if any(int(value) for value in refreshed_plan["collisionGuards"].values()):
        raise RuntimeError(
            f"legacy merge left unsafe collisions: {refreshed_plan['collisionGuards']}"
        )
    connection.commit()
    stats["residualMissingRows"] = {}
    stats["postMergePlanSha256"] = refreshed_plan["planSha256"]
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_connection_args(parser)
    parser.add_argument("--source-schema", required=True)
    parser.add_argument("--source-dump-sha256", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--plan-sha256")
    args = parser.parse_args()
    parser.error(
        "retired: Kado/JLP legacy databases are not Cardz Market Cap sources; "
        "use the canonical incremental pipelines"
    )

    source_schema = validate_schema_name(args.source_schema)
    canonical_schema = validate_schema_name(args.database)
    dump_sha = str(args.source_dump_sha256).strip().lower()
    if not SHA256_HEX.fullmatch(dump_sha):
        raise ValueError("--source-dump-sha256 must be a lowercase SHA-256")
    if args.apply and not args.plan_sha256:
        raise ValueError("--apply requires --plan-sha256")
    if not args.apply and args.plan_sha256:
        raise ValueError("--plan-sha256 is only valid with --apply")

    connection = connection_from_args(args)
    acquired: list[str] = []
    try:
        with connection.cursor() as cursor:
            acquired = acquire_locks(cursor)
            assert_schemas(cursor, source_schema, canonical_schema)
            plan, variant_plans = build_plan(
                cursor,
                source_schema,
                canonical_schema,
                dump_sha,
            )
            if not args.apply:
                connection.rollback()
                print(json.dumps(plan, ensure_ascii=False, indent=2, default=str))
                return 0
            assert_apply_safe(plan, str(args.plan_sha256))
            stats = apply_plan(
                connection,
                cursor,
                source_schema,
                canonical_schema,
                plan,
                variant_plans,
            )
            print(
                json.dumps(
                    {
                        "status": "applied",
                        "appliedPlanSha256": plan["planSha256"],
                        "stats": stats,
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            return 0
    except Exception:
        connection.rollback()
        raise
    finally:
        if acquired:
            with connection.cursor() as cursor:
                release_locks(cursor, acquired)
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
