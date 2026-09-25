#!/usr/bin/env python3
"""Build the daily CARDZ-known hybrid GemRate census exact-refresh worklist.

The fresh brute snapshot supplies current POP for IDs covered by its public set
rows. Exact card-page refresh is therefore limited to known IDs missing from
that snapshot plus newly qualified IDs not already known to CARDZ. The database
is opened as one read-only consistent snapshot; this script never writes to
CARDZ DB or either authority checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping


HEX40 = re.compile(r"^[0-9a-f]{40}$")
SQL_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")
FORMAL_MIN_POP = 1000
RADAR_MIN_POP = 971
DEFAULT_MINIMUM_WORKLIST = 0
WORK_DIR = Path(__file__).resolve().parent


def utc_iso(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_value(item) for item in value]
    if isinstance(value, datetime):
        return utc_iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def choose_existing(*candidates: Path) -> Path:
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[-1]


def default_authority_repo() -> Path:
    return choose_existing(
        Path("/mnt/c/Users/jackson0202/Documents/Playground/cardz-market-cap-fe-db-20260805"),
        Path(r"C:\Users\jackson0202\Documents\Playground\cardz-market-cap-fe-db-20260805"),
    )


def default_legacy_repo() -> Path:
    return choose_existing(
        Path("/mnt/c/Users/jackson0202/Documents/Playground/cardz-market-cap"),
        Path(r"C:\Users\jackson0202\Documents\Playground\cardz-market-cap"),
    )


def assert_valid_ids(source_name: str, ids: Iterable[str]) -> set[str]:
    normalized = {str(value).strip().lower() for value in ids}
    invalid = sorted(value for value in normalized if not HEX40.fullmatch(value))
    if invalid:
        raise RuntimeError(f"invalid_gemrate_ids:{source_name}:{','.join(invalid[:10])}")
    return normalized


def parse_population(value: Any, *, label: str) -> int:
    try:
        population = int(value)
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"invalid_population:{label}:{value!r}") from error
    if population < 0:
        raise RuntimeError(f"negative_population:{label}:{population}")
    return population


def file_provenance(path: Path, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "path": str(path),
        "present": True,
        "sha256": sha256_file(path),
        "modifiedAt": utc_iso(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)),
    }


def load_id_file(path: Path, *, required: bool) -> tuple[set[str], dict[str, Any]]:
    if not path.is_file():
        if required:
            raise RuntimeError(f"missing_id_file:{path}")
        return set(), {"kind": "id_file", "path": str(path), "present": False}
    ids: set[str] = set()
    data_lines = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            value = line.strip().lower()
            if not value or value.startswith("#"):
                continue
            data_lines += 1
            if not HEX40.fullmatch(value):
                raise RuntimeError(f"invalid_gemrate_id:id_file:{path}:{line_number}:{value}")
            if value in ids:
                raise RuntimeError(f"duplicate_gemrate_id:id_file:{path}:{line_number}:{value}")
            ids.add(value)
    if required and not ids:
        raise RuntimeError(f"empty_id_file:{path}")
    details = file_provenance(path, "id_file")
    details["dataLines"] = data_lines
    return ids, details


def load_brute_psa_ids(
    path: Path, *, required: bool, minimum_population: int | None = None
) -> tuple[set[str], dict[str, Any]]:
    if not path.is_file():
        if required:
            raise RuntimeError(f"missing_brute_census:{path}")
        return set(), {"kind": "brute_jsonl", "path": str(path), "present": False}
    ids: set[str] = set()
    row_count = 0
    rows_without_psa_id = 0
    duplicate_psa_id_rows = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row_count += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError(f"invalid_json:brute:{path}:{line_number}:{error.msg}") from error
            if not isinstance(row, dict):
                raise RuntimeError(f"non_object_row:brute:{path}:{line_number}")
            gemrate_id = str(row.get("psa_id") or "").strip().lower()
            if not gemrate_id:
                rows_without_psa_id += 1
                continue
            if not HEX40.fullmatch(gemrate_id):
                raise RuntimeError(f"invalid_gemrate_id:brute:{path}:{line_number}:{gemrate_id}")
            if minimum_population is not None:
                population = parse_population(
                    row.get("psa_10"), label=f"brute:{path.name}:{line_number}:{gemrate_id}"
                )
                if population < minimum_population:
                    raise RuntimeError(
                        f"brute_below_required_population:{path.name}:{gemrate_id}:{population}"
                    )
            if gemrate_id in ids:
                # The public set snapshot can list one provider card under
                # multiple set rows.  This is source-listing duplication, not
                # a second GemRate identity; the worklist contract is unique
                # IDs, so retain it once and preserve the duplicate count in
                # provenance.
                duplicate_psa_id_rows += 1
                continue
            ids.add(gemrate_id)
    if required and not ids:
        raise RuntimeError(f"empty_brute_census:{path}")
    details = file_provenance(path, "brute_jsonl")
    details.update(
        {
            "rows": row_count,
            "rowsWithoutPsaId": rows_without_psa_id,
            "duplicatePsaIdRows": duplicate_psa_id_rows,
        }
    )
    return ids, details


def load_cache_dir_ids(path: Path) -> tuple[set[str], dict[str, Any]]:
    if not path.is_dir():
        return set(), {"kind": "card_cache", "path": str(path), "present": False}
    ids = {
        entry.name.lower()
        for entry in path.iterdir()
        if entry.is_dir() and HEX40.fullmatch(entry.name.lower())
    }
    return assert_valid_ids(str(path), ids), {
        "kind": "card_cache",
        "path": str(path),
        "present": True,
    }


def manifest_gemrate_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).lower()
            if "gemrate" in normalized_key and "id" in normalized_key and isinstance(item, str):
                candidate = item.strip().lower()
                if HEX40.fullmatch(candidate):
                    found.add(candidate)
            found.update(manifest_gemrate_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(manifest_gemrate_ids(item))
    return found


def load_manifest_ids(runs_root: Path) -> tuple[set[str], dict[str, Any]]:
    if not runs_root.is_dir():
        return set(), {"kind": "manifest_tree", "path": str(runs_root), "present": False}
    paths = sorted(
        set(runs_root.glob("*/manifest.json"))
        | set(runs_root.glob("*/public-card-dump-manifest-*.json"))
    )
    ids: set[str] = set()
    for manifest in paths:
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"invalid_manifest:{manifest}:{error}") from error
        ids.update(manifest_gemrate_ids(payload))
    return assert_valid_ids(str(runs_root), ids), {
        "kind": "manifest_tree",
        "path": str(runs_root),
        "present": True,
        "manifestFiles": len(paths),
    }


def load_db_sources(
    authority_repo: Path,
) -> tuple[dict[str, Any], dict[str, int | None], dict[str, Any], dict[str, set[str]], int]:
    pipelines = authority_repo / "pipelines"
    if not pipelines.is_dir():
        raise RuntimeError(f"missing_authority_pipelines:{pipelines}")
    sys.path.insert(0, str(pipelines))
    from qualified_pool_operator import db  # type: ignore

    connection = db()
    try:
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            cursor.execute(
                "SELECT DATABASE() AS database_name,CURRENT_TIMESTAMP(6) AS selected_at,"
                "@@version AS server_version"
            )
            database_identity = dict(cursor.fetchone())
            cursor.execute(
                "SELECT generation_id,MAX(computed_at) AS computed_at,COUNT(*) AS member_count "
                "FROM catalog_rebuild_member GROUP BY generation_id "
                "ORDER BY computed_at DESC,generation_id DESC LIMIT 1"
            )
            generation = dict(cursor.fetchone() or {})
            if not generation:
                raise RuntimeError("catalog_rebuild_member_empty")
            cursor.execute(
                "SELECT gemrate_id,latest_psa10_population FROM catalog_rebuild_member "
                "WHERE generation_id=%s",
                (generation["generation_id"],),
            )
            population_rows = [dict(row) for row in cursor.fetchall()]

            cursor.execute(
                "SELECT DISTINCT external_entity_id AS gid FROM catalog_source_identity "
                "WHERE source_code='gemrate' AND external_entity_id REGEXP '^[0-9a-f]{40}$'"
            )
            identity_ids = {str(row["gid"]).lower() for row in cursor.fetchall()}
            cursor.execute(
                "SELECT DISTINCT external_entity_id AS gid FROM market_source_observation "
                "WHERE source_code='gemrate' AND external_entity_id REGEXP '^[0-9a-f]{40}$'"
            )
            observation_ids = {str(row["gid"]).lower() for row in cursor.fetchall()}
            cursor.execute(
                "SELECT c.table_name AS table_name,c.column_name AS column_name "
                "FROM information_schema.columns c "
                "JOIN information_schema.tables t ON t.table_schema=c.table_schema "
                "AND t.table_name=c.table_name AND t.table_type='BASE TABLE' "
                "WHERE c.table_schema=DATABASE() AND c.column_name LIKE '%gemrate%id%'"
            )
            columns = [
                (str(row["table_name"]), str(row["column_name"]))
                for row in cursor.fetchall()
            ]
            named_column_ids: set[str] = set()
            for table_name, column_name in columns:
                if not SQL_IDENTIFIER.fullmatch(table_name) or not SQL_IDENTIFIER.fullmatch(column_name):
                    raise RuntimeError(f"unsafe_db_identifier:{table_name}.{column_name}")
                cursor.execute(
                    f"SELECT DISTINCT `{column_name}` AS gid FROM `{table_name}` "
                    f"WHERE `{column_name}` REGEXP '^[0-9a-f]{{40}}$'"
                )
                named_column_ids.update(str(row["gid"]).lower() for row in cursor.fetchall())

        populations: dict[str, int | None] = {}
        for row_number, row in enumerate(population_rows, 1):
            gemrate_id = str(row.get("gemrate_id") or "").strip().lower()
            if not HEX40.fullmatch(gemrate_id):
                raise RuntimeError(
                    f"invalid_gemrate_id:latest_generation:{row_number}:{gemrate_id or '<empty>'}"
                )
            raw_population = row.get("latest_psa10_population")
            population = (
                None
                if raw_population is None
                else parse_population(
                    raw_population,
                    label=f"latest_generation:{row_number}:{gemrate_id}",
                )
            )
            if gemrate_id in populations:
                raise RuntimeError(f"duplicate_gemrate_id:latest_generation:{gemrate_id}")
            populations[gemrate_id] = population
        if not populations:
            raise RuntimeError("latest_generation_has_no_gemrate_ids")
        sources = {
            "db_catalog_source_identity": assert_valid_ids(
                "db_catalog_source_identity", identity_ids
            ),
            "db_market_source_observation": assert_valid_ids(
                "db_market_source_observation", observation_ids
            ),
            "db_gemrate_named_columns": assert_valid_ids(
                "db_gemrate_named_columns", named_column_ids
            ),
        }
        connection.rollback()
        return generation, populations, database_identity, sources, len(columns)
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def build(args: argparse.Namespace) -> dict[str, Any]:
    authority_repo = args.authority_repo.resolve()
    legacy_repo = args.legacy_repo.resolve()
    ids_output = args.ids_output.resolve()
    excluded_registry_output = args.excluded_registry_output.resolve()
    metadata_output = args.metadata_output.resolve()
    fresh_brute_all = (
        args.fresh_brute_all.resolve()
        if args.fresh_brute_all
        else authority_repo / "data" / "private" / "gemrate_brute" / "all_cards.jsonl"
    )
    fresh_brute_qualified = (
        args.fresh_brute_qualified.resolve()
        if args.fresh_brute_qualified
        else authority_repo / "data" / "private" / "gemrate_brute" / "psa10_1000_plus.jsonl"
    )

    generation, db_populations, database_identity, db_sources, db_column_count = (
        load_db_sources(authority_repo)
    )
    sources: dict[str, set[str]] = {}
    provenance: dict[str, dict[str, Any]] = {}

    def add(name: str, result: tuple[set[str], dict[str, Any]]) -> None:
        ids, details = result
        sources[name] = assert_valid_ids(name, ids)
        provenance[name] = details

    add(
        "ids_file_current",
        load_id_file(authority_repo / "pipelines" / "gemrate_ids.txt", required=True),
    )
    add(
        "ids_file_legacy",
        load_id_file(legacy_repo / "pipelines" / "gemrate_ids.txt", required=False),
    )
    for name, ids in db_sources.items():
        sources[name] = ids
        provenance[name] = {
            "kind": "database_snapshot",
            "database": database_identity.get("database_name"),
        }
    provenance["db_gemrate_named_columns"]["columnsScanned"] = db_column_count
    sources["db_latest_catalog_generation"] = set(db_populations)
    provenance["db_latest_catalog_generation"] = {
        "kind": "database_snapshot",
        "database": database_identity.get("database_name"),
        "generationId": generation.get("generation_id"),
        "computedAt": json_value(generation.get("computed_at")),
    }
    add(
        "cards_cache_current",
        load_cache_dir_ids(authority_repo / "data" / "private" / "gemrate" / "cards"),
    )
    add(
        "cards_cache_legacy",
        load_cache_dir_ids(legacy_repo / "data" / "private" / "gemrate" / "cards"),
    )
    add(
        "manifests_current",
        load_manifest_ids(authority_repo / "data" / "private" / "gemrate" / "runs"),
    )
    add(
        "manifests_legacy",
        load_manifest_ids(legacy_repo / "data" / "private" / "gemrate" / "runs"),
    )
    add(
        "brute_legacy_all_cards",
        load_brute_psa_ids(
            legacy_repo / "data" / "private" / "gemrate_brute" / "all_cards.jsonl",
            required=False,
        ),
    )
    add("brute_fresh_all_cards", load_brute_psa_ids(fresh_brute_all, required=True))
    add(
        "brute_fresh_qualified",
        load_brute_psa_ids(
            fresh_brute_qualified, required=True, minimum_population=FORMAL_MIN_POP
        ),
    )

    membership_count: dict[str, int] = {}
    for ids in sources.values():
        for gemrate_id in ids:
            membership_count[gemrate_id] = membership_count.get(gemrate_id, 0) + 1
    cumulative: set[str] = set()
    for name, ids in sources.items():
        previous = set(cumulative)
        cumulative.update(ids)
        provenance[name].update(
            {
                "idCount": len(ids),
                "newIdsInCumulativeUnion": len(ids - previous),
                "uniqueOnlyToThisSource": sum(
                    membership_count[gemrate_id] == 1 for gemrate_id in ids
                ),
            }
        )
    db_formal_ids = {
        gemrate_id
        for gemrate_id, population in db_populations.items()
        if population is not None and population >= FORMAL_MIN_POP
    }
    db_radar_ids = {
        gemrate_id
        for gemrate_id, population in db_populations.items()
        if population is not None and RADAR_MIN_POP <= population < FORMAL_MIN_POP
    }
    verified_known = set(db_populations)
    fresh_all_ids = sources["brute_fresh_all_cards"]
    fresh_qualified_ids = sources["brute_fresh_qualified"]
    registry_ids = sources["ids_file_current"] | sources["ids_file_legacy"]
    if not fresh_qualified_ids <= fresh_all_ids:
        missing_from_snapshot = sorted(fresh_qualified_ids - fresh_all_ids)
        raise RuntimeError(
            "fresh_qualified_not_in_fresh_all_snapshot:"
            + ",".join(missing_from_snapshot[:10])
        )
    verified_covered_by_fresh = verified_known & fresh_all_ids
    verified_missing_from_fresh = verified_known - fresh_all_ids
    fresh_outside_verified = fresh_all_ids - verified_known
    fresh_qualified_outside_verified = fresh_qualified_ids - verified_known
    fresh_below_threshold_excluded = fresh_outside_verified - fresh_qualified_ids
    coverage_universe = verified_known | fresh_all_ids
    exact_refresh_ids = verified_missing_from_fresh | fresh_qualified_outside_verified
    registry_verified = registry_ids & verified_known
    registry_fresh_only = (registry_ids & fresh_all_ids) - verified_known
    unverified_registry_excluded = registry_ids - coverage_universe
    if verified_missing_from_fresh & fresh_qualified_outside_verified:
        raise AssertionError("hybrid_refresh_partitions_overlap")
    if len(fresh_outside_verified) != (
        len(fresh_qualified_outside_verified) + len(fresh_below_threshold_excluded)
    ):
        raise AssertionError("fresh_outside_verified_partition_mismatch")
    if exact_refresh_ids & unverified_registry_excluded:
        raise AssertionError("refresh_and_excluded_registry_overlap")
    if len(registry_ids) != (
        len(registry_verified)
        + len(registry_fresh_only)
        + len(unverified_registry_excluded)
    ):
        raise AssertionError("registry_candidate_partition_mismatch")
    if args.minimum_worklist > 0 and len(exact_refresh_ids) < args.minimum_worklist:
        raise RuntimeError(
            f"implausibly_small_hybrid_refresh_worklist:{len(exact_refresh_ids)}"
            f"<minimum:{args.minimum_worklist}"
        )
    if not all(HEX40.fullmatch(gemrate_id) for gemrate_id in exact_refresh_ids):
        raise AssertionError("invalid_exact_refresh_gemrate_id")
    ordered_ids = sorted(exact_refresh_ids)
    if len(ordered_ids) != len(set(ordered_ids)):
        raise AssertionError("duplicate_output_gemrate_id")

    for name, details in provenance.items():
        if name == "brute_fresh_all_cards":
            details["hybridCensusRole"] = "current_snapshot_coverage"
        elif name == "brute_fresh_qualified":
            details["hybridCensusRole"] = "qualified_outside_verified_confirmation"
        elif name == "db_latest_catalog_generation":
            details["hybridCensusRole"] = "verified_known_authority"
        elif name in ("ids_file_current", "ids_file_legacy"):
            details["hybridCensusRole"] = "registry_provenance_not_verification"
        else:
            details["hybridCensusRole"] = "context_provenance_not_scope_authority"
    if db_formal_ids & db_radar_ids:
        raise AssertionError("formal_radar_overlap")

    atomic_write_text(ids_output, "".join(f"{gemrate_id}\n" for gemrate_id in ordered_ids))
    ids_sha256 = sha256_file(ids_output)
    ordered_excluded_registry_ids = sorted(unverified_registry_excluded)
    atomic_write_text(
        excluded_registry_output,
        "".join(f"{gemrate_id}\n" for gemrate_id in ordered_excluded_registry_ids),
    )
    excluded_registry_sha256 = sha256_file(excluded_registry_output)
    counts = {
        "dbFormal": len(db_formal_ids),
        "dbRadar": len(db_radar_ids),
        "dbFormalAndRadar": len(db_formal_ids | db_radar_ids),
        "verifiedCatalogGeneration": len(verified_known),
        "freshSnapshotAll": len(fresh_all_ids),
        "freshBruteQualified": len(fresh_qualified_ids),
        "verifiedCoveredByFreshSnapshot": len(verified_covered_by_fresh),
        "verifiedMissingFromFreshSnapshot": len(verified_missing_from_fresh),
        "freshOutsideVerified": len(fresh_outside_verified),
        "freshQualifiedOutsideVerified": len(fresh_qualified_outside_verified),
        "freshBelowThresholdExcludedFromExactRefresh": len(
            fresh_below_threshold_excluded
        ),
        "coverageUniverse": len(coverage_universe),
        "exactRefreshVerifiedMissingFromSnapshot": len(verified_missing_from_fresh),
        "exactRefreshNewQualifiedConfirmation": len(fresh_qualified_outside_verified),
        "exactRefreshWorklist": len(exact_refresh_ids),
        "registryCandidates": len(registry_ids),
        "registryVerifiedByLatestGeneration": len(registry_verified),
        "registryFreshSnapshotOnly": len(registry_fresh_only),
        "unverifiedRegistryCandidatesExcluded": len(unverified_registry_excluded),
        "freshQualifiedOverlapVerified": len(fresh_qualified_ids & verified_known),
        "currentIdsFile": len(sources["ids_file_current"]),
    }
    metadata = {
        "contract": "gemrate_verified_hybrid_census_worklist_v1",
        "status": "COMPLETE_VERIFIED_HYBRID_CENSUS_WORKLIST",
        "generatedAt": utc_iso(),
        "scope": {
            "name": "Verified CARDZ hybrid GemRate census worklist",
            "providerGlobal": False,
            "description": "Exact refresh of latest-generation verified IDs absent from the fresh set snapshot plus fresh qualified IDs outside that verified generation",
        },
        "providerGlobal": False,
        "thresholds": {
            "formalPsa10Min": FORMAL_MIN_POP,
            "radarPsa10Min": RADAR_MIN_POP,
            "radarPsa10Max": FORMAL_MIN_POP - 1,
            "minimumAcceptedExactRefreshWorklist": args.minimum_worklist,
        },
        "databaseSnapshot": json_value(database_identity),
        "latestRebuildGeneration": json_value(generation),
        "freshSnapshot": {
            "allCards": {
                "path": provenance["brute_fresh_all_cards"]["path"],
                "sha256": provenance["brute_fresh_all_cards"]["sha256"],
                "modifiedAt": provenance["brute_fresh_all_cards"]["modifiedAt"],
                "uniquePsaIds": len(fresh_all_ids),
            },
            "qualified": {
                "path": provenance["brute_fresh_qualified"]["path"],
                "sha256": provenance["brute_fresh_qualified"]["sha256"],
                "modifiedAt": provenance["brute_fresh_qualified"]["modifiedAt"],
                "uniquePsaIds": len(fresh_qualified_ids),
            },
        },
        "counts": counts,
        "sourceCounts": {name: len(ids) for name, ids in sources.items()},
        "sourceProvenance": provenance,
        "worklistCount": len(ordered_ids),
        "worklistSha256": ids_sha256,
        "sortedUniqueIdsSha256": ids_sha256,
        "worklist": {
            "idsPath": str(ids_output),
            "count": len(ordered_ids),
            "sha256": ids_sha256,
            "ordering": "GemRate ID ascending",
        },
        "unverifiedRegistryCandidatesExcludedCount": len(
            ordered_excluded_registry_ids
        ),
        "unverifiedRegistryCandidatesExcludedSha256": excluded_registry_sha256,
        "unverifiedRegistryCandidatesExcluded": {
            "idsPath": str(excluded_registry_output),
            "count": len(ordered_excluded_registry_ids),
            "sha256": excluded_registry_sha256,
            "ordering": "GemRate ID ascending",
            "reason": "Present in current/legacy registry but absent from both latest catalog generation and fresh snapshot; not verified for exact refresh",
            "registrySources": ["ids_file_current", "ids_file_legacy"],
        },
        "metadataPath": str(metadata_output),
    }
    atomic_write_text(
        metadata_output,
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authority-repo", type=Path, default=default_authority_repo())
    parser.add_argument("--legacy-repo", type=Path, default=default_legacy_repo())
    parser.add_argument("--fresh-brute-all", type=Path)
    parser.add_argument("--fresh-brute-qualified", type=Path)
    parser.add_argument(
        "--minimum-worklist",
        "--minimum-union",
        dest="minimum_worklist",
        type=int,
        default=DEFAULT_MINIMUM_WORKLIST,
    )
    parser.add_argument(
        "--ids-output",
        type=Path,
        default=WORK_DIR / "gemrate_full_census_refresh_ids.txt",
    )
    parser.add_argument(
        "--excluded-registry-output",
        type=Path,
        default=WORK_DIR / "gemrate_unverified_registry_candidates_excluded.txt",
    )
    parser.add_argument(
        "--metadata-output",
        type=Path,
        default=WORK_DIR / "gemrate_full_census_refresh_metadata.json",
    )
    args = parser.parse_args()
    metadata = build(args)
    print(json.dumps(metadata["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
