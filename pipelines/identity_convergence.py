#!/usr/bin/env python3
"""Merge only pre-confirmed duplicate variants into their canonical variant.

The authoritative input is ``catalog_printing_identity``:

* ``canonical`` + ``duplicate`` rows with the same complete printing fields
  are eligible.
* ``catalog_printing_identity.review`` rows are never selected or modified.
  Existing terminal review records only have a non-null ``resolved_variant_id``
  repointed when that target has become an alias.
* raw ``market_source_observation`` rows are never updated or deleted.

Historical derived snapshots (``market_index_constituent`` and
``market_candidate_daily_snapshot``) remain immutable evidence and keep their
original variant IDs; readers can resolve them through ``catalog_variant_alias``.

The command is dry-run by default. A real commit requires both ``--apply`` and
the exact plan hash printed by a preceding dry-run. Migration 011 must already
be installed. Run only while canonical DB writers are stopped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args  # noqa: E402


IMPORT_ADVISORY_LOCK = "cardz_market_cap_import"
ALERT_ADVISORY_LOCK = "cardz_market_alert_evaluation"
CONVERGENCE_ADVISORY_LOCK = "cardz.identity_convergence.v1"
ADVISORY_LOCKS = (
    IMPORT_ADVISORY_LOCK,
    ALERT_ADVISORY_LOCK,
    CONVERGENCE_ADVISORY_LOCK,
)


@dataclass(frozen=True)
class DuplicatePair:
    duplicate_variant_id: int
    canonical_variant_id: int
    evidence_sha256: str


@dataclass(frozen=True)
class CollisionTable:
    name: str
    logical_key: tuple[str, ...]
    freshness_order: tuple[str, ...]


@dataclass(frozen=True)
class KeyedTable:
    name: str
    logical_key: tuple[str, ...]
    freshness_column: str


COLLISION_TABLES = (
    CollisionTable(
        "market_grader_population_observation",
        ("grader_code", "source_code", "observed_date"),
        ("effective_at", "id"),
    ),
    CollisionTable(
        "market_price_observation",
        ("source_code", "observed_date"),
        ("effective_at", "id"),
    ),
    CollisionTable(
        "market_daily_sales_aggregate",
        ("source_code", "observed_date"),
        ("id",),
    ),
    CollisionTable(
        "market_tracked_sales_aggregate",
        ("grader_code", "grade_label", "window_code", "window_end_at", "aggregate_sha256"),
        ("computed_at", "id"),
    ),
    CollisionTable(
        "market_population_transport_observation",
        (
            "authority_code",
            "transport_code",
            "grader_code",
            "grade_label",
            "effective_date",
        ),
        ("fetched_at", "id"),
    ),
)

KEYED_TABLES = (
    KeyedTable("catalog_variant_locale", ("locale_code",), "updated_at"),
    KeyedTable(
        "catalog_story_pointer",
        ("locale_code", "source_version_sha256"),
        "observed_at",
    ),
    KeyedTable(
        "market_image_source_pointer",
        ("image_kind", "source_version_sha256"),
        "observed_at",
    ),
)

SIMPLE_VARIANT_REFS = (
    ("catalog_source_identity", "variant_id"),
    ("catalog_provider_identity_alias", "variant_id"),
    ("market_sale_observation", "variant_id"),
    ("market_alert", "variant_id"),
    ("market_identity_review_queue", "resolved_variant_id"),
    ("market_identity_review_resolution", "resolved_variant_id"),
)

IMMUTABLE_HISTORICAL_VARIANT_TABLES = (
    "market_index_constituent",
    "market_candidate_daily_snapshot",
)


def canonical_json(value: Any) -> bytes:
    def encode(item: Any) -> Any:
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        if isinstance(item, Decimal):
            return str(item)
        raise TypeError(f"unsupported canonical JSON value: {type(item).__name__}")

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=encode,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def acquire_locks(cursor: Any) -> list[str]:
    acquired: list[str] = []
    for lock_name in ADVISORY_LOCKS:
        cursor.execute("SELECT GET_LOCK(%s, 0) AS acquired", (lock_name,))
        row = cursor.fetchone()
        if row is None or int(row["acquired"] or 0) != 1:
            for held in reversed(acquired):
                cursor.execute("SELECT RELEASE_LOCK(%s)", (held,))
            raise RuntimeError(f"database writer lock is busy: {lock_name}")
        acquired.append(lock_name)
    return acquired


def release_locks(cursor: Any, acquired: Sequence[str]) -> None:
    for lock_name in reversed(acquired):
        cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))


def assert_schema(cursor: Any) -> None:
    cursor.execute(
        """
        SELECT COUNT(*) AS n
        FROM information_schema.tables
        WHERE table_schema=DATABASE()
          AND table_name IN ('catalog_variant_alias', 'market_identity_review_resolution')
        """
    )
    if int(cursor.fetchone()["n"]) != 2:
        raise RuntimeError("migration 011 is not installed")


def load_confirmed_pairs(cursor: Any) -> list[DuplicatePair]:
    cursor.execute(
        """
        SELECT
            duplicate_identity.variant_id AS duplicate_variant_id,
            canonical_identity.variant_id AS canonical_variant_id,
            duplicate_identity.evidence_sha256 AS evidence_sha256
        FROM catalog_printing_identity AS duplicate_identity
        JOIN catalog_printing_identity AS canonical_identity
          ON canonical_identity.tcg_code=duplicate_identity.tcg_code
         AND canonical_identity.set_name=duplicate_identity.set_name
         AND canonical_identity.collector_number=duplicate_identity.collector_number
         AND canonical_identity.edition_code=duplicate_identity.edition_code
         AND canonical_identity.parallel_code=duplicate_identity.parallel_code
         AND canonical_identity.finish_code=duplicate_identity.finish_code
         AND canonical_identity.identity_status='canonical'
        WHERE duplicate_identity.identity_status='duplicate'
        ORDER BY duplicate_identity.variant_id
        FOR UPDATE
        """
    )
    raw = cursor.fetchall()
    pairs = [
        DuplicatePair(
            duplicate_variant_id=int(row["duplicate_variant_id"]),
            canonical_variant_id=int(row["canonical_variant_id"]),
            evidence_sha256=str(row["evidence_sha256"]),
        )
        for row in raw
    ]
    by_duplicate: dict[int, set[int]] = {}
    for pair in pairs:
        by_duplicate.setdefault(pair.duplicate_variant_id, set()).add(pair.canonical_variant_id)
        if pair.duplicate_variant_id == pair.canonical_variant_id:
            raise RuntimeError(f"variant {pair.duplicate_variant_id} aliases itself")
    ambiguous = {
        duplicate: sorted(canonicals)
        for duplicate, canonicals in by_duplicate.items()
        if len(canonicals) != 1
    }
    if ambiguous:
        raise RuntimeError(f"duplicate variants have ambiguous canonicals: {ambiguous}")
    return pairs


def load_current_universe(cursor: Any) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    cursor.execute(
        """
        SELECT id, lock_sha256, effective_at, policy_json, member_count
        FROM market_universe_lock
        WHERE is_current=1
        ORDER BY effective_at DESC, id DESC
        FOR UPDATE
        """
    )
    locks = list(cursor.fetchall())
    if len(locks) > 1:
        raise RuntimeError("more than one current universe lock exists")
    if not locks:
        return None, []
    lock = dict(locks[0])
    cursor.execute(
        """
        SELECT variant_id, segment_code, member_role, market_rank, watch_position,
               watch_score, selection_signals_json
        FROM market_universe_member
        WHERE universe_lock_id=%s
        ORDER BY variant_id
        FOR UPDATE
        """,
        (int(lock["id"]),),
    )
    return lock, [dict(row) for row in cursor.fetchall()]


def load_affected_table_state(
    cursor: Any,
    pairs: Sequence[DuplicatePair],
) -> dict[str, dict[str, int | str | None]]:
    variant_ids = sorted(
        {
            variant_id
            for pair in pairs
            for variant_id in (pair.duplicate_variant_id, pair.canonical_variant_id)
        }
    )
    if not variant_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(variant_ids))
    table_refs: list[tuple[str, str, tuple[str, ...], str | None]] = [
        (
            "catalog_variant",
            "id",
            ("id", "identity_status"),
            "id",
        ),
        (
            "catalog_variant_alias",
            "duplicate_variant_id",
            ("duplicate_variant_id", "canonical_variant_id"),
            "duplicate_variant_id",
        ),
        (
            "catalog_printing_identity",
            "variant_id",
            (
                "variant_id",
                "identity_status",
                "tcg_code",
                "set_name",
                "collector_number",
                "edition_code",
                "parallel_code",
                "finish_code",
                "evidence_sha256",
            ),
            "variant_id",
        ),
    ]
    for spec in COLLISION_TABLES:
        table_refs.append(
            (
                spec.name,
                "variant_id",
                tuple(dict.fromkeys(("id", "variant_id", *spec.logical_key, *spec.freshness_order))),
                "id",
            )
        )
    for spec in KEYED_TABLES:
        table_refs.append(
            (
                spec.name,
                "variant_id",
                ("variant_id", *spec.logical_key, spec.freshness_column),
                None,
            )
        )
    simple_columns = {
        "catalog_source_identity": (
            "source_code",
            "external_entity_id",
            "variant_id",
        ),
        "catalog_provider_identity_alias": (
            "provider_code",
            "alias_type",
            "alias_value",
            "grader_code",
            "requested_external_entity_id",
            "variant_id",
        ),
        "market_sale_observation": ("id", "variant_id"),
        "market_alert": ("id", "variant_id"),
        "market_identity_review_queue": ("id", "resolved_variant_id"),
        "market_identity_review_resolution": ("id", "resolved_variant_id"),
    }
    simple_id_columns = {
        "market_sale_observation": "id",
        "market_alert": "id",
        "market_identity_review_queue": "id",
        "market_identity_review_resolution": "id",
    }
    for table, column in SIMPLE_VARIANT_REFS:
        table_refs.append(
            (
                table,
                column,
                simple_columns[table],
                simple_id_columns.get(table),
            )
        )
    table_refs.extend(
        [
            (
                "market_image_asset",
                "variant_id",
                ("id", "variant_id", "image_kind", "content_sha256", "captured_at"),
                "id",
            ),
            (
                "market_index_constituent",
                "variant_id",
                ("index_snapshot_id", "variant_id"),
                None,
            ),
            (
                "market_candidate_daily_snapshot",
                "variant_id",
                ("id", "evaluation_id", "variant_id"),
                "id",
            ),
        ]
    )

    state: dict[str, dict[str, int | str | None]] = {}
    for table, column, selected_columns, id_column in table_refs:
        cursor.execute(
            f"SELECT {', '.join(selected_columns)} FROM {table} "
            f"WHERE {column} IN ({placeholders})",
            tuple(variant_ids),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        state[table] = table_state_entry(rows, id_column=id_column)
    cursor.execute(
        f"""
        SELECT qc.id, qc.image_asset_id, qc.qc_version, qc.checked_at,
               asset.variant_id AS asset_variant_id
        FROM market_image_qc AS qc
        JOIN market_image_asset AS asset ON asset.id=qc.image_asset_id
        WHERE asset.variant_id IN ({placeholders})
        """,
        tuple(variant_ids),
    )
    state["market_image_qc"] = table_state_entry(
        [dict(row) for row in cursor.fetchall()],
        id_column="id",
    )
    return dict(sorted(state.items()))


def table_state_entry(
    rows: Sequence[Mapping[str, Any]],
    *,
    id_column: str | None,
) -> dict[str, int | str | None]:
    ordered = sorted((dict(row) for row in rows), key=canonical_json)
    identifiers = [
        int(row[id_column])
        for row in ordered
        if id_column is not None and row.get(id_column) is not None
    ]
    return {
        "rows": len(ordered),
        "maxId": max(identifiers) if identifiers else None,
        "operationSha256": sha256_json(ordered),
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _resolved_universe_members(
    rows: Iterable[Mapping[str, Any]],
    aliases: Mapping[int, int],
) -> tuple[list[dict[str, Any]], int]:
    prepared: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        row["_original_variant_id"] = int(row["variant_id"])
        prepared.append(row)
    grouped: dict[int, list[dict[str, Any]]] = {}
    changed = 0
    for row in prepared:
        original_id = int(row["_original_variant_id"])
        resolved_id = int(aliases.get(original_id, original_id))
        row["variant_id"] = resolved_id
        row["selection_signals_json"] = _json_value(row["selection_signals_json"])
        grouped.setdefault(resolved_id, []).append(row)
        if resolved_id != original_id:
            changed += 1

    result: list[dict[str, Any]] = []
    for variant_id, candidates in sorted(grouped.items()):
        # One printing can appear once as a formal tracked card and once as a
        # pre-entry monitor. Convergence must preserve the stronger accepted
        # role instead of blindly preferring the canonical opaque row.
        strongest_role = max(
            int(
                str(row["segment_code"]) != "pre-entry"
                and str(row["member_role"]) != "monitoring"
            )
            for row in candidates
        )
        strongest = [
            row
            for row in candidates
            if int(
                str(row["segment_code"]) != "pre-entry"
                and str(row["member_role"]) != "monitoring"
            )
            == strongest_role
        ]
        roles = {
            (str(row["segment_code"]), str(row["member_role"]))
            for row in strongest
        }
        if len(roles) != 1:
            raise RuntimeError(
                f"universe membership conflict for canonical variant {variant_id}: {sorted(roles)}"
            )
        selected = next(
            (row for row in strongest if int(row["_original_variant_id"]) == variant_id),
            min(strongest, key=lambda row: int(row["_original_variant_id"])),
        )
        selected.pop("_original_variant_id", None)
        result.append(selected)
    return result, changed


def build_plan(
    pairs: Sequence[DuplicatePair],
    current_lock: Mapping[str, Any] | None,
    current_members: Sequence[Mapping[str, Any]],
    affected_table_state: Mapping[str, Mapping[str, int | str | None]] | None = None,
) -> dict[str, Any]:
    aliases = {pair.duplicate_variant_id: pair.canonical_variant_id for pair in pairs}
    resolved_members, universe_changes = _resolved_universe_members(current_members, aliases)
    return {
        "schemaVersion": "1.0",
        "pairs": [
            {
                "duplicateVariantId": pair.duplicate_variant_id,
                "canonicalVariantId": pair.canonical_variant_id,
                "evidenceSha256": pair.evidence_sha256,
            }
            for pair in pairs
        ],
        "affectedTableState": dict(affected_table_state or {}),
        "immutableHistoricalTables": list(IMMUTABLE_HISTORICAL_VARIANT_TABLES),
        "currentUniverse": (
            {
                "id": int(current_lock["id"]),
                "lockSha256": str(current_lock["lock_sha256"]),
                "memberCount": int(current_lock["member_count"]),
                "resolvedMemberCount": len(resolved_members),
                "resolvedMembersSha256": sha256_json(resolved_members),
                "changes": universe_changes,
            }
            if current_lock
            else None
        ),
    }


def _order_key(row: Mapping[str, Any], columns: Sequence[str]) -> tuple[Any, ...]:
    return tuple((row.get(column) is not None, row.get(column)) for column in columns)


def merge_collision_table(
    cursor: Any,
    spec: CollisionTable,
    duplicate_variant_id: int,
    canonical_variant_id: int,
) -> dict[str, int]:
    columns = ("id", "variant_id", *spec.logical_key, *spec.freshness_order)
    selected = ", ".join(dict.fromkeys(columns))
    cursor.execute(
        f"SELECT {selected} FROM {spec.name} "
        "WHERE variant_id IN (%s, %s) FOR UPDATE",
        (duplicate_variant_id, canonical_variant_id),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        key = tuple(row[column] for column in spec.logical_key)
        groups.setdefault(key, []).append(row)

    deleted = 0
    moved = 0
    for members in groups.values():
        if not any(int(row["variant_id"]) == duplicate_variant_id for row in members):
            continue
        winner = max(members, key=lambda row: _order_key(row, spec.freshness_order))
        loser_ids = [int(row["id"]) for row in members if int(row["id"]) != int(winner["id"])]
        if loser_ids:
            placeholders = ", ".join(["%s"] * len(loser_ids))
            cursor.execute(f"DELETE FROM {spec.name} WHERE id IN ({placeholders})", tuple(loser_ids))
            deleted += len(loser_ids)
        if int(winner["variant_id"]) == duplicate_variant_id:
            cursor.execute(
                f"UPDATE {spec.name} SET variant_id=%s WHERE id=%s",
                (canonical_variant_id, int(winner["id"])),
            )
            moved += 1
    return {"moved": moved, "deletedCollisions": deleted}


def _row_locator(
    variant_id: int,
    row: Mapping[str, Any],
    columns: Sequence[str],
) -> tuple[str, tuple[Any, ...]]:
    predicates = ["variant_id=%s", *(f"{column}=%s" for column in columns)]
    return " AND ".join(predicates), (variant_id, *(row[column] for column in columns))


def merge_keyed_table(
    cursor: Any,
    spec: KeyedTable,
    duplicate_variant_id: int,
    canonical_variant_id: int,
) -> dict[str, int]:
    columns = ("variant_id", *spec.logical_key, spec.freshness_column)
    cursor.execute(
        f"SELECT {', '.join(columns)} FROM {spec.name} "
        "WHERE variant_id IN (%s, %s) FOR UPDATE",
        (duplicate_variant_id, canonical_variant_id),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        key = tuple(row[column] for column in spec.logical_key)
        groups.setdefault(key, []).append(row)

    moved = 0
    deleted = 0
    for members in groups.values():
        if not any(int(row["variant_id"]) == duplicate_variant_id for row in members):
            continue
        winner = max(
            members,
            key=lambda row: (
                row.get(spec.freshness_column) is not None,
                row.get(spec.freshness_column),
                int(row["variant_id"]) == canonical_variant_id,
            ),
        )
        for loser in members:
            if loser is winner:
                continue
            where, args = _row_locator(
                int(loser["variant_id"]),
                loser,
                spec.logical_key,
            )
            cursor.execute(f"DELETE FROM {spec.name} WHERE {where}", args)
            deleted += 1
        if int(winner["variant_id"]) == duplicate_variant_id:
            where, args = _row_locator(
                duplicate_variant_id,
                winner,
                spec.logical_key,
            )
            cursor.execute(
                f"UPDATE {spec.name} SET variant_id=%s, {spec.freshness_column}=%s "
                f"WHERE {where}",
                (
                    canonical_variant_id,
                    winner[spec.freshness_column],
                    *args,
                ),
            )
            moved += 1
    return {"moved": moved, "deletedCollisions": deleted}


def merge_image_qc(
    cursor: Any,
    loser_asset_id: int,
    winner_asset_id: int,
) -> dict[str, int]:
    cursor.execute(
        """
        SELECT id, image_asset_id, qc_version, checked_at
        FROM market_image_qc
        WHERE image_asset_id IN (%s, %s)
        FOR UPDATE
        """,
        (loser_asset_id, winner_asset_id),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row["qc_version"]), []).append(row)
    moved = 0
    deleted = 0
    for members in groups.values():
        if not any(int(row["image_asset_id"]) == loser_asset_id for row in members):
            continue
        winner = max(
            members,
            key=lambda row: (
                row.get("checked_at") is not None,
                row.get("checked_at"),
                int(row["id"]),
            ),
        )
        loser_ids = [int(row["id"]) for row in members if int(row["id"]) != int(winner["id"])]
        if loser_ids:
            placeholders = ", ".join(["%s"] * len(loser_ids))
            cursor.execute(
                f"DELETE FROM market_image_qc WHERE id IN ({placeholders})",
                tuple(loser_ids),
            )
            deleted += len(loser_ids)
        if int(winner["image_asset_id"]) == loser_asset_id:
            cursor.execute(
                "UPDATE market_image_qc SET image_asset_id=%s WHERE id=%s",
                (winner_asset_id, int(winner["id"])),
            )
            moved += 1
    return {"moved": moved, "deletedCollisions": deleted}


def merge_image_assets(
    cursor: Any,
    duplicate_variant_id: int,
    canonical_variant_id: int,
) -> dict[str, int]:
    cursor.execute(
        """
        SELECT id, variant_id, image_kind, content_sha256, captured_at
        FROM market_image_asset
        WHERE variant_id IN (%s, %s)
        FOR UPDATE
        """,
        (duplicate_variant_id, canonical_variant_id),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row["image_kind"]), str(row["content_sha256"]))
        groups.setdefault(key, []).append(row)

    totals = {
        "moved": 0,
        "deletedCollisions": 0,
        "qcMoved": 0,
        "qcDeletedCollisions": 0,
    }
    for members in groups.values():
        if not any(int(row["variant_id"]) == duplicate_variant_id for row in members):
            continue
        winner = max(
            members,
            key=lambda row: (
                row.get("captured_at") is not None,
                row.get("captured_at"),
                int(row["id"]),
            ),
        )
        for loser in members:
            if loser is winner:
                continue
            qc = merge_image_qc(cursor, int(loser["id"]), int(winner["id"]))
            totals["qcMoved"] += qc["moved"]
            totals["qcDeletedCollisions"] += qc["deletedCollisions"]
            cursor.execute("DELETE FROM market_image_asset WHERE id=%s", (int(loser["id"]),))
            totals["deletedCollisions"] += 1
        if int(winner["variant_id"]) == duplicate_variant_id:
            cursor.execute(
                "UPDATE market_image_asset SET variant_id=%s WHERE id=%s",
                (canonical_variant_id, int(winner["id"])),
            )
            totals["moved"] += 1
    return totals


def merge_simple_reference(
    cursor: Any,
    table: str,
    column: str,
    duplicate_variant_id: int,
    canonical_variant_id: int,
) -> int:
    cursor.execute(
        f"UPDATE {table} SET {column}=%s WHERE {column}=%s",
        (canonical_variant_id, duplicate_variant_id),
    )
    return int(cursor.rowcount)


def ensure_alias(
    cursor: Any,
    pair: DuplicatePair,
    plan_sha256: str,
    merged_at: datetime,
) -> bool:
    cursor.execute(
        """
        SELECT canonical_variant_id
        FROM catalog_variant_alias
        WHERE duplicate_variant_id=%s
        FOR UPDATE
        """,
        (pair.duplicate_variant_id,),
    )
    existing = cursor.fetchone()
    if existing:
        if int(existing["canonical_variant_id"]) != pair.canonical_variant_id:
            raise RuntimeError(
                f"variant {pair.duplicate_variant_id} already aliases "
                f"{existing['canonical_variant_id']}"
            )
        return False
    cursor.execute(
        """
        INSERT INTO catalog_variant_alias
            (duplicate_variant_id, canonical_variant_id, reason_code, evidence_sha256,
             convergence_plan_sha256, merged_at)
        VALUES (%s, %s, 'confirmed_printing_duplicate', %s, %s, %s)
        """,
        (
            pair.duplicate_variant_id,
            pair.canonical_variant_id,
            pair.evidence_sha256,
            plan_sha256,
            merged_at,
        ),
    )
    return True


def replace_current_universe(
    cursor: Any,
    current_lock: Mapping[str, Any] | None,
    current_members: Sequence[Mapping[str, Any]],
    aliases: Mapping[int, int],
    plan_sha256: str,
    effective_at: datetime,
) -> dict[str, Any]:
    if current_lock is None:
        return {"changed": False, "reason": "no_current_lock"}
    members, changes = _resolved_universe_members(current_members, aliases)
    if changes == 0:
        return {"changed": False, "reason": "no_duplicate_members"}

    lock_document = {
        "supersedesLockSha256": str(current_lock["lock_sha256"]),
        "identityConvergencePlanSha256": plan_sha256,
        "members": members,
    }
    lock_sha256 = sha256_json(lock_document)
    policy = _json_value(current_lock["policy_json"])
    if not isinstance(policy, dict):
        raise RuntimeError("current universe policy_json is not an object")
    policy = dict(policy)
    policy["identityConvergence"] = {
        "supersedesLockId": int(current_lock["id"]),
        "planSha256": plan_sha256,
    }

    cursor.execute(
        "SELECT id FROM market_universe_lock WHERE lock_sha256=%s FOR UPDATE",
        (lock_sha256,),
    )
    existing = cursor.fetchone()
    if existing:
        new_lock_id = int(existing["id"])
    else:
        cursor.execute(
            """
            INSERT INTO market_universe_lock
                (lock_sha256, effective_at, policy_json, member_count, is_current)
            VALUES (%s, %s, %s, %s, 0)
            """,
            (
                lock_sha256,
                effective_at,
                canonical_json(policy).decode("utf-8"),
                len(members),
            ),
        )
        new_lock_id = int(cursor.lastrowid)
        cursor.executemany(
            """
            INSERT INTO market_universe_member
                (universe_lock_id, variant_id, segment_code, member_role, market_rank,
                 watch_position, watch_score, selection_signals_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    new_lock_id,
                    int(row["variant_id"]),
                    row["segment_code"],
                    row["member_role"],
                    row["market_rank"],
                    row["watch_position"],
                    row["watch_score"],
                    canonical_json(row["selection_signals_json"]).decode("utf-8"),
                )
                for row in members
            ],
        )
    cursor.execute("UPDATE market_universe_lock SET is_current=0 WHERE is_current=1")
    cursor.execute("UPDATE market_universe_lock SET is_current=1 WHERE id=%s", (new_lock_id,))
    return {
        "changed": True,
        "oldLockId": int(current_lock["id"]),
        "newLockId": new_lock_id,
        "lockSha256": lock_sha256,
        "oldMemberCount": len(current_members),
        "newMemberCount": len(members),
    }


def _guard_fingerprint(cursor: Any) -> dict[str, int]:
    cursor.execute("SELECT COUNT(*) AS n FROM market_source_observation")
    raw_count = int(cursor.fetchone()["n"])
    cursor.execute(
        """
        SELECT COUNT(*) AS n
        FROM catalog_printing_identity
        WHERE identity_status='review'
        """
    )
    printing_review_count = int(cursor.fetchone()["n"])
    cursor.execute("SELECT COUNT(*) AS n FROM market_identity_review_queue")
    queue_count = int(cursor.fetchone()["n"])
    return {
        "rawSourceObservationRows": raw_count,
        "printingReviewRows": printing_review_count,
        "reviewQueueRows": queue_count,
    }


def apply_plan(
    connection: Any,
    pairs: Sequence[DuplicatePair],
    current_lock: Mapping[str, Any] | None,
    current_members: Sequence[Mapping[str, Any]],
    plan_sha256: str,
    *,
    commit: bool,
) -> dict[str, Any]:
    merged_at = datetime.now(timezone.utc).replace(tzinfo=None)
    stats: dict[str, Any] = {
        "pairs": len(pairs),
        "aliasesCreated": 0,
        "simpleMoves": {},
        "collisionTables": {},
        "keyedTables": {},
        "imageAssets": {
            "moved": 0,
            "deletedCollisions": 0,
            "qcMoved": 0,
            "qcDeletedCollisions": 0,
        },
        "immutableHistoricalTables": list(IMMUTABLE_HISTORICAL_VARIANT_TABLES),
    }
    with connection.cursor() as cursor:
        before = _guard_fingerprint(cursor)
        for pair in pairs:
            if ensure_alias(cursor, pair, plan_sha256, merged_at):
                stats["aliasesCreated"] += 1
            for table, column in SIMPLE_VARIANT_REFS:
                stats["simpleMoves"][table] = stats["simpleMoves"].get(
                    table,
                    0,
                ) + merge_simple_reference(
                    cursor,
                    table,
                    column,
                    pair.duplicate_variant_id,
                    pair.canonical_variant_id,
                )
            for spec in COLLISION_TABLES:
                outcome = merge_collision_table(
                    cursor,
                    spec,
                    pair.duplicate_variant_id,
                    pair.canonical_variant_id,
                )
                totals = stats["collisionTables"].setdefault(
                    spec.name,
                    {"moved": 0, "deletedCollisions": 0},
                )
                totals["moved"] += outcome["moved"]
                totals["deletedCollisions"] += outcome["deletedCollisions"]
            for spec in KEYED_TABLES:
                outcome = merge_keyed_table(
                    cursor,
                    spec,
                    pair.duplicate_variant_id,
                    pair.canonical_variant_id,
                )
                totals = stats["keyedTables"].setdefault(
                    spec.name,
                    {"moved": 0, "deletedCollisions": 0},
                )
                totals["moved"] += outcome["moved"]
                totals["deletedCollisions"] += outcome["deletedCollisions"]
            image_outcome = merge_image_assets(
                cursor,
                pair.duplicate_variant_id,
                pair.canonical_variant_id,
            )
            for key, value in image_outcome.items():
                stats["imageAssets"][key] += value
            cursor.execute(
                "UPDATE catalog_variant SET identity_status='alias' WHERE id=%s",
                (pair.duplicate_variant_id,),
            )

        stats["universe"] = replace_current_universe(
            cursor,
            current_lock,
            current_members,
            {pair.duplicate_variant_id: pair.canonical_variant_id for pair in pairs},
            plan_sha256,
            merged_at,
        )
        after = _guard_fingerprint(cursor)
        if before != after:
            raise RuntimeError(f"raw/review guard changed: before={before}, after={after}")
        stats["guard"] = after

    if commit:
        connection.commit()
    else:
        connection.rollback()
    stats["committed"] = commit
    return stats


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_connection_args(parser)
    parser.add_argument("--apply", action="store_true", help="commit; default is rollback dry-run")
    parser.add_argument(
        "--expected-plan-sha256",
        help="required with --apply; copy the exact hash from a fresh dry-run",
    )
    args = parser.parse_args(argv)
    connection = connection_from_args(args)
    acquired_locks: list[str] = []
    try:
        with connection.cursor() as cursor:
            acquired_locks = acquire_locks(cursor)
            assert_schema(cursor)
            pairs = load_confirmed_pairs(cursor)
            current_lock, current_members = load_current_universe(cursor)
            affected_table_state = load_affected_table_state(cursor, pairs)
        plan = build_plan(
            pairs,
            current_lock,
            current_members,
            affected_table_state,
        )
        plan_sha256 = sha256_json(plan)
        if args.apply and args.expected_plan_sha256 != plan_sha256:
            raise RuntimeError(
                "--apply requires the exact --expected-plan-sha256 from this dry-run: "
                f"{plan_sha256}"
            )
        report = {
            "mode": "apply" if args.apply else "dry-run",
            "planSha256": plan_sha256,
            "plan": plan,
            "result": apply_plan(
                connection,
                pairs,
                current_lock,
                current_members,
                plan_sha256,
                commit=bool(args.apply),
            ),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0
    except Exception:
        connection.rollback()
        raise
    finally:
        if acquired_locks:
            with connection.cursor() as cursor:
                release_locks(cursor, acquired_locks)
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
