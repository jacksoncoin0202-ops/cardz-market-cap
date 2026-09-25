#!/usr/bin/env python3
"""Generate the CARDZ GemRate DB completeness handoff and daily GROK queue.

The generator is deliberately read-only: it opens one consistent database
snapshot, reconciles every in-scope fresh GemRate PSA ID, asserts all primary
bucket totals, and only then atomically writes the JSON + Markdown deliverables.

The authoritative input mode is hybrid: one fresh fixed public-set snapshot
plus an exact public-card-page refresh for known IDs missing from that snapshot
and fresh newly-qualified confirmations. Exact-page request IDs are provenance,
never PSA IDs; any qualified raw PSA row without its own GemRate ID stops the
census. Neither input nor their union is a provider-global census.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SNK_SOURCES = ("snkrdunk", "snk", "snk_psa10")
MARKET_SOURCES = ("pricecharting", *SNK_SOURCES)
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, None: 4}


class IncompleteCensus(RuntimeError):
    pass


def utc_iso(value: datetime | None = None) -> str:
    dt = value or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_instant(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_value(v) for v in value]
    if isinstance(value, datetime):
        return utc_iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def compact_text(value: Any, limit: int = 500) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    else:
        text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise IncompleteCensus(f"invalid_json:{path.name}:{line_number}:{error.msg}") from error
            if not isinstance(row, dict):
                raise IncompleteCensus(f"non_object_row:{path.name}:{line_number}")
            rows.append(row)
    return rows


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise IncompleteCensus(f"missing_{label}:{path}") from error
    except json.JSONDecodeError as error:
        raise IncompleteCensus(f"invalid_json:{label}:{path}:{error.msg}") from error
    if not isinstance(value, dict):
        raise IncompleteCensus(f"non_object_json:{label}:{path}")
    return value


def validate_fresh_census(
    census_path: Path,
    not_before: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any], set[str]]:
    all_cards_path = census_path.with_name("all_cards.jsonl")
    failed_path = census_path.with_name("failed_sets.jsonl")
    errors: list[str] = []
    threshold = not_before.timestamp()

    for path, label in ((census_path, "qualified_census"), (all_cards_path, "all_cards")):
        if not path.is_file():
            errors.append(f"missing_{label}:{path}")
        elif path.stat().st_mtime < threshold:
            errors.append(
                f"stale_{label}:mtime={utc_iso(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))}"
            )
    if errors:
        raise IncompleteCensus(";".join(errors))

    failed_rows: list[dict[str, Any]] = []
    fresh_failed_artifact = failed_path.is_file() and failed_path.stat().st_mtime >= threshold
    if fresh_failed_artifact:
        failed_rows = read_jsonl(failed_path)
        if failed_rows:
            errors.append(f"failed_sets:{len(failed_rows)}")

    census_rows = read_jsonl(census_path)
    census_by_id: dict[str, int] = {}
    duplicate_ids: list[str] = []
    for index, row in enumerate(census_rows, 1):
        psa_id = str(row.get("psa_id") or "").strip().lower()
        if not HEX40.fullmatch(psa_id):
            errors.append(f"invalid_psa_id:census:{index}:{psa_id or '<empty>'}")
            continue
        try:
            population = int(row.get("psa_10") or 0)
        except (TypeError, ValueError):
            population = -1
        if population < 1000:
            errors.append(f"population_below_1000:{psa_id}:{population}")
        if psa_id in census_by_id:
            duplicate_ids.append(psa_id)
        else:
            census_by_id[psa_id] = population
    if duplicate_ids:
        errors.append("duplicate_psa_ids:" + ",".join(sorted(set(duplicate_ids))[:20]))
    if not census_rows:
        errors.append("empty_census")

    all_count = 0
    set_ids: set[str] = set()
    set_names: dict[str, str] = {}
    all_psa_ids: set[str] = set()
    all_rows_without_valid_psa_id = 0
    recomputed: dict[str, int] = {}
    recomputed_duplicates: list[str] = []
    with all_cards_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            all_count += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                errors.append(f"invalid_json:all_cards:{line_number}:{error.msg}")
                continue
            set_id = str(row.get("_set_id") or "").strip()
            if not set_id:
                errors.append(f"missing_set_id:all_cards:{line_number}")
            else:
                set_ids.add(set_id)
                set_names.setdefault(set_id, str(row.get("_set_name") or ""))
            try:
                population = int(row.get("psa_10") or 0)
            except (TypeError, ValueError):
                population = 0
            psa_id = str(row.get("psa_id") or "").strip().lower()
            if not HEX40.fullmatch(psa_id):
                # The public set snapshot also contains grader-only rows with no
                # PSA identity.  They are outside the PSA-ID universe unless
                # their PSA 10 population itself crosses the policy threshold.
                all_rows_without_valid_psa_id += 1
                if population >= 1000:
                    errors.append(
                        "qualified_all_cards_row_missing_valid_psa_id:"
                        f"line={line_number}:population={population}"
                    )
                continue
            all_psa_ids.add(psa_id)
            if population < 1000:
                continue
            if psa_id in recomputed:
                recomputed_duplicates.append(psa_id)
            else:
                recomputed[psa_id] = population
    if recomputed_duplicates:
        errors.append("duplicate_qualified_psa_ids_in_all_cards:" + ",".join(sorted(set(recomputed_duplicates))[:20]))
    if recomputed != census_by_id:
        missing = sorted(set(recomputed) - set(census_by_id))
        extra = sorted(set(census_by_id) - set(recomputed))
        drift = sorted(k for k in set(recomputed) & set(census_by_id) if recomputed[k] != census_by_id[k])
        errors.append(
            "census_recompute_mismatch:"
            f"missing={len(missing)},extra={len(extra)},population_drift={len(drift)}"
        )
    if failed_rows:
        errors.extend(
            f"failed_set:{row.get('set_id')}:{compact_text(row.get('error'), 160)}"
            for row in failed_rows[:20]
        )
    qualified_sha = sha256_file(census_path)
    for row in census_rows:
        psa_id = str(row.get("psa_id") or "").strip().lower()
        raw_full_name = row.get("psa_details")
        if not isinstance(raw_full_name, str) or not raw_full_name.strip():
            errors.append(f"missing_fresh_psa_authority_full_name:{psa_id or '<empty>'}")
            continue
        row["name"] = raw_full_name
        row["full_name"] = raw_full_name
        row["raw_full_name"] = raw_full_name
        row["name_source"] = "fresh_fixed_snapshot.psa_details"
        row["name_provenance"] = {
            "authority": "gemrate",
            "grader": "psa",
            "field": "psa_details",
            "artifactPath": str(census_path),
            "artifactSha256": qualified_sha,
            "rowKey": psa_id,
            "observedDate": row.get("psa_date"),
            "preservation": "verbatim_no_compact_no_split",
        }
    if errors:
        raise IncompleteCensus(";".join(errors))

    census_stat = census_path.stat()
    all_stat = all_cards_path.stat()
    psa_dates = sorted(str(r.get("psa_date") or "") for r in census_rows if r.get("psa_date"))
    metadata = {
        "status": "INCOMPLETE_PROVIDER_GLOBAL",
        "candidateScopeStatus": "COMPLETE_FIXED_PUBLIC_SNAPSHOT",
        "source": "GemRate universal-pop-report fixed inline set snapshot, all visible TCG entries",
        "scopeKind": "fixed_public_inline_set_snapshot",
        "scope": (
            "GemRate fixed public inline set snapshot only; this is not a provider-global catalog "
            "or provider-global census"
        ),
        "providerGlobal": False,
        "limitation": (
            "The public universal-pop-report embeds a fixed set list and exposes no complete "
            "provider-wide set directory. Counts from this mode are snapshot-bounded."
        ),
        "path": str(census_path),
        "capturedAt": utc_iso(datetime.fromtimestamp(census_stat.st_mtime, timezone.utc)),
        "refreshNotBefore": utc_iso(not_before),
        "sha256": qualified_sha,
        "rowCount": len(census_rows),
        "uniquePsaIdCount": len(census_by_id),
        "populationFloor": 1000,
        "sourceSetCount": len(set_ids),
        "sourceSetCardCount": all_count,
        "sourceUniquePsaIdCount": len(all_psa_ids),
        "sourceRowsWithoutValidPsaIdCount": all_rows_without_valid_psa_id,
        "qualifiedSourceSetCount": len({str(r.get("_set_id") or "") for r in census_rows}),
        "failedSetCount": 0,
        "staleFailedSetArtifactIgnored": bool(failed_path.is_file() and not fresh_failed_artifact),
        "allCardsCapturedAt": utc_iso(datetime.fromtimestamp(all_stat.st_mtime, timezone.utc)),
        "allCardsPath": str(all_cards_path),
        "allCardsSha256": sha256_file(all_cards_path),
        "psaObservationDateMin": psa_dates[0] if psa_dates else None,
        "psaObservationDateMax": psa_dates[-1] if psa_dates else None,
    }
    return census_rows, metadata, all_psa_ids


def _manifest_int(manifest: Mapping[str, Any], key: str, errors: list[str]) -> int:
    try:
        return int(manifest.get(key))
    except (TypeError, ValueError):
        errors.append(f"invalid_manifest_count:{key}:{manifest.get(key)!r}")
        return -1


def _raw_psa_population_row(
    details: Mapping[str, Any],
    request_id: str,
    errors: list[str],
    artifact_label: str,
) -> dict[str, Any] | None:
    """Return the sole provider-native PSA row without borrowing another ID space."""

    population_data = details.get("population_data")
    if not isinstance(population_data, list):
        errors.append(f"missing_population_data:{artifact_label}:{request_id}")
        return None
    psa_rows = [
        row
        for row in population_data
        if isinstance(row, Mapping) and str(row.get("grader") or "").strip().lower() == "psa"
    ]
    if len(psa_rows) != 1:
        errors.append(f"expected_one_psa_population_row:{artifact_label}:{request_id}:found={len(psa_rows)}")
        return None
    raw_row = psa_rows[0]
    grades = raw_row.get("grades")
    if not isinstance(grades, Mapping):
        errors.append(f"missing_psa_grades:{artifact_label}:{request_id}")
        return None
    # Provider-native raw: g10 before 2026-09-08, psa_10 since. The key choice
    # lives in gemrate_source so capture and this gate cannot drift apart.
    import gemrate_source

    raw_g10 = gemrate_source.page_top_grade_raw("psa", grades)
    try:
        population = int(raw_g10)
    except (TypeError, ValueError):
        errors.append(
            f"invalid_psa_g10:{artifact_label}:{request_id}:{raw_g10!r}"
        )
        return None
    if population < 0:
        errors.append(f"negative_psa_g10:{artifact_label}:{request_id}:{population}")
        return None

    raw_psa_id = str(raw_row.get("gemrate_id") or "").strip().lower()
    full_name = raw_row.get("description")
    if not isinstance(full_name, str) or not full_name.strip():
        errors.append(f"missing_psa_row_authority_full_name:{artifact_label}:{request_id}")
        return None
    return {
        "psaId": raw_psa_id if HEX40.fullmatch(raw_psa_id) else None,
        "rawPsaId": raw_psa_id or None,
        "populationPsa10": population,
        "fullName": full_name,
        "row": dict(raw_row),
    }


def _replacement_pairs_sha256(pairs: Iterable[tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for request_id, psa_id in sorted(pairs):
        digest.update(f"{request_id}\t{psa_id}\n".encode("ascii"))
    return digest.hexdigest()


def _psa10_from_card_details(details: Mapping[str, Any]) -> int | None:
    rows = details.get("population_data")
    if not isinstance(rows, list):
        return None
    psa_rows = [
        row
        for row in rows
        if isinstance(row, Mapping) and str(row.get("grader") or "").strip().lower() == "psa"
    ]
    if len(psa_rows) != 1:
        return None
    grades = psa_rows[0].get("grades")
    if not isinstance(grades, Mapping):
        return None
    try:
        return int(grades.get("g10"))
    except (TypeError, ValueError):
        return None


def validate_worklist_metadata(
    metadata_path: Path,
    not_before: datetime,
) -> tuple[dict[str, Any], Path, list[str], str, Path, list[str], str]:
    errors: list[str] = []
    threshold = not_before.timestamp()
    if not metadata_path.is_file():
        raise IncompleteCensus(f"missing_worklist_metadata:{metadata_path}")
    if metadata_path.stat().st_mtime < threshold:
        errors.append(
            "stale_worklist_metadata:"
            f"mtime={utc_iso(datetime.fromtimestamp(metadata_path.stat().st_mtime, timezone.utc))}"
        )
    metadata = read_json_object(metadata_path, "worklist_metadata")
    if metadata.get("contract") != "gemrate_verified_hybrid_census_worklist_v1":
        errors.append(f"invalid_worklist_contract:{metadata.get('contract')!r}")
    if metadata.get("status") != "COMPLETE_VERIFIED_HYBRID_CENSUS_WORKLIST":
        errors.append(f"invalid_worklist_status:{metadata.get('status')!r}")
    if metadata.get("providerGlobal") is not False:
        errors.append(f"worklist_top_level_provider_global_must_be_false:{metadata.get('providerGlobal')!r}")
    scope = metadata.get("scope")
    if not isinstance(scope, Mapping):
        scope = {}
        errors.append("invalid_worklist_scope")
    if scope.get("name") != "Verified CARDZ hybrid GemRate census worklist":
        errors.append(f"invalid_worklist_scope_name:{scope.get('name')!r}")
    if scope.get("providerGlobal") is not False:
        errors.append(f"worklist_provider_global_must_be_false:{scope.get('providerGlobal')!r}")
    worklist = metadata.get("worklist")
    if not isinstance(worklist, Mapping):
        worklist = {}
        errors.append("invalid_worklist_descriptor")
    if worklist.get("ordering") != "GemRate ID ascending":
        errors.append(f"invalid_worklist_ordering:{worklist.get('ordering')!r}")

    counts = metadata.get("counts")
    if not isinstance(counts, Mapping):
        counts = {}
        errors.append("invalid_worklist_counts")
    required_count_keys = (
        "verifiedCatalogGeneration",
        "freshSnapshotAll",
        "verifiedCoveredByFreshSnapshot",
        "verifiedMissingFromFreshSnapshot",
        "freshOutsideVerified",
        "freshQualifiedOutsideVerified",
        "freshBelowThresholdExcludedFromExactRefresh",
        "coverageUniverse",
        "exactRefreshVerifiedMissingFromSnapshot",
        "exactRefreshNewQualifiedConfirmation",
        "exactRefreshWorklist",
        "registryCandidates",
        "registryVerifiedByLatestGeneration",
        "registryFreshSnapshotOnly",
        "unverifiedRegistryCandidatesExcluded",
    )
    parsed_counts: dict[str, int] = {}
    for key in required_count_keys:
        try:
            parsed_counts[key] = int(counts.get(key))
        except (TypeError, ValueError):
            errors.append(f"invalid_worklist_counts.{key}:{counts.get(key)!r}")
    if len(parsed_counts) == len(required_count_keys):
        if parsed_counts["verifiedCatalogGeneration"] != (
            parsed_counts["verifiedCoveredByFreshSnapshot"]
            + parsed_counts["verifiedMissingFromFreshSnapshot"]
        ):
            errors.append("verified_generation_partition_cardinality_mismatch")
        if parsed_counts["freshSnapshotAll"] != (
            parsed_counts["verifiedCoveredByFreshSnapshot"] + parsed_counts["freshOutsideVerified"]
        ):
            errors.append("fresh_snapshot_partition_cardinality_mismatch")
        if parsed_counts["freshBelowThresholdExcludedFromExactRefresh"] != (
            parsed_counts["freshOutsideVerified"] - parsed_counts["freshQualifiedOutsideVerified"]
        ):
            errors.append("fresh_below_threshold_exclusion_cardinality_mismatch")
        if parsed_counts["coverageUniverse"] != (
            parsed_counts["verifiedCatalogGeneration"] + parsed_counts["freshOutsideVerified"]
        ):
            errors.append("coverage_union_cardinality_mismatch")
        if (
            parsed_counts["exactRefreshVerifiedMissingFromSnapshot"]
            != parsed_counts["verifiedMissingFromFreshSnapshot"]
        ):
            errors.append("exact_refresh_missing_verified_cardinality_mismatch")
        if (
            parsed_counts["exactRefreshNewQualifiedConfirmation"]
            != parsed_counts["freshQualifiedOutsideVerified"]
        ):
            errors.append("exact_refresh_new_qualified_cardinality_mismatch")
        if parsed_counts["exactRefreshWorklist"] != (
            parsed_counts["exactRefreshVerifiedMissingFromSnapshot"]
            + parsed_counts["exactRefreshNewQualifiedConfirmation"]
        ):
            errors.append("exact_refresh_worklist_cardinality_mismatch")
        if parsed_counts["registryCandidates"] != (
            parsed_counts["registryVerifiedByLatestGeneration"]
            + parsed_counts["registryFreshSnapshotOnly"]
            + parsed_counts["unverifiedRegistryCandidatesExcluded"]
        ):
            errors.append("registry_candidate_partition_cardinality_mismatch")

    source_provenance = metadata.get("sourceProvenance")
    if not isinstance(source_provenance, Mapping):
        source_provenance = {}
        errors.append("invalid_worklist_source_provenance")
    fresh_all_provenance = source_provenance.get("brute_fresh_all_cards")
    fresh_qualified_provenance = source_provenance.get("brute_fresh_qualified")
    if not isinstance(fresh_all_provenance, Mapping):
        errors.append("missing_fresh_all_source_provenance")
    elif fresh_all_provenance.get("hybridCensusRole") != "current_snapshot_coverage":
        errors.append("invalid_fresh_all_hybrid_census_role")
    if not isinstance(fresh_qualified_provenance, Mapping):
        errors.append("missing_fresh_qualified_source_provenance")
    elif fresh_qualified_provenance.get("hybridCensusRole") != "qualified_outside_verified_confirmation":
        errors.append("invalid_fresh_qualified_hybrid_census_role")

    raw_ids_path = str(worklist.get("idsPath") or "").strip()
    if not raw_ids_path:
        errors.append("missing_worklist_ids_path")
        ids_path = metadata_path.with_name("_missing_worklist_ids_")
    else:
        ids_path = Path(raw_ids_path)
        if not ids_path.is_absolute():
            ids_path = (metadata_path.parent / ids_path).resolve()
    if not ids_path.is_file():
        errors.append(f"missing_worklist_ids:{ids_path}")
        worklist_ids: list[str] = []
        actual_sha = ""
    else:
        if ids_path.stat().st_mtime < threshold:
            errors.append(
                "stale_worklist_ids:"
                f"mtime={utc_iso(datetime.fromtimestamp(ids_path.stat().st_mtime, timezone.utc))}"
            )
        actual_sha = sha256_file(ids_path)
        worklist_ids = [line.strip().lower() for line in ids_path.read_text(encoding="utf-8").splitlines()]
        if any(not gemrate_id for gemrate_id in worklist_ids):
            errors.append("blank_worklist_gemrate_id")
        invalid_ids = [gemrate_id for gemrate_id in worklist_ids if not HEX40.fullmatch(gemrate_id)]
        if invalid_ids:
            errors.append("invalid_worklist_gemrate_ids:" + ",".join(invalid_ids[:20]))
        if len(worklist_ids) != len(set(worklist_ids)):
            errors.append("duplicate_worklist_gemrate_ids")
        if worklist_ids != sorted(worklist_ids):
            errors.append("worklist_gemrate_ids_not_sorted")

    count_claims = {
        "worklistCount": metadata.get("worklistCount"),
        "worklist.count": worklist.get("count"),
    }
    for label, raw_count in count_claims.items():
        try:
            claimed_count = int(raw_count)
        except (TypeError, ValueError):
            errors.append(f"invalid_{label}:{raw_count!r}")
            continue
        if claimed_count != len(worklist_ids):
            errors.append(f"{label}_mismatch:claimed={claimed_count},actual={len(worklist_ids)}")
    if parsed_counts.get("exactRefreshWorklist") != len(worklist_ids):
        errors.append(
            "counts.exactRefreshWorklist_mismatch:"
            f"claimed={parsed_counts.get('exactRefreshWorklist')},actual={len(worklist_ids)}"
        )

    hash_claims = {
        "worklistSha256": metadata.get("worklistSha256"),
        "sortedUniqueIdsSha256": metadata.get("sortedUniqueIdsSha256"),
        "worklist.sha256": worklist.get("sha256"),
    }
    for label, raw_digest in hash_claims.items():
        claimed_digest = str(raw_digest or "").strip().lower()
        if not HEX64.fullmatch(claimed_digest):
            errors.append(f"invalid_{label}:{claimed_digest or '<empty>'}")
        elif claimed_digest != actual_sha:
            errors.append(f"{label}_mismatch:claimed={claimed_digest},actual={actual_sha}")

    excluded = metadata.get("unverifiedRegistryCandidatesExcluded")
    if not isinstance(excluded, Mapping):
        excluded = {}
        errors.append("invalid_unverified_registry_exclusion_descriptor")
    if excluded.get("ordering") != "GemRate ID ascending":
        errors.append(f"invalid_excluded_ordering:{excluded.get('ordering')!r}")
    if not str(excluded.get("reason") or "").strip():
        errors.append("missing_unverified_registry_exclusion_reason")
    raw_excluded_path = str(excluded.get("idsPath") or "").strip()
    if raw_excluded_path:
        excluded_path = Path(raw_excluded_path)
        if not excluded_path.is_absolute():
            excluded_path = (metadata_path.parent / excluded_path).resolve()
    else:
        excluded_path = metadata_path.with_name("_missing_excluded_registry_ids_")
        errors.append("missing_excluded_registry_ids_path")
    if excluded_path.is_file():
        if excluded_path.stat().st_mtime < threshold:
            errors.append(
                "stale_excluded_registry_ids:"
                f"mtime={utc_iso(datetime.fromtimestamp(excluded_path.stat().st_mtime, timezone.utc))}"
            )
        excluded_sha = sha256_file(excluded_path)
        excluded_ids = [
            line.strip().lower()
            for line in excluded_path.read_text(encoding="utf-8").splitlines()
        ]
        invalid_excluded = [gemrate_id for gemrate_id in excluded_ids if not HEX40.fullmatch(gemrate_id)]
        if invalid_excluded:
            errors.append("invalid_excluded_registry_ids:" + ",".join(invalid_excluded[:20]))
        if len(excluded_ids) != len(set(excluded_ids)):
            errors.append("duplicate_excluded_registry_ids")
        if excluded_ids != sorted(excluded_ids):
            errors.append("excluded_registry_ids_not_sorted")
    else:
        excluded_sha = ""
        excluded_ids = []
        errors.append(f"missing_excluded_registry_ids:{excluded_path}")
    if set(worklist_ids) & set(excluded_ids):
        errors.append("worklist_and_excluded_registry_ids_overlap")
    excluded_count_claims = {
        "unverifiedRegistryCandidatesExcludedCount": metadata.get(
            "unverifiedRegistryCandidatesExcludedCount"
        ),
        "unverifiedRegistryCandidatesExcluded.count": excluded.get("count"),
        "counts.unverifiedRegistryCandidatesExcluded": parsed_counts.get(
            "unverifiedRegistryCandidatesExcluded"
        ),
    }
    for label, raw_count in excluded_count_claims.items():
        try:
            claimed_count = int(raw_count)
        except (TypeError, ValueError):
            errors.append(f"invalid_{label}:{raw_count!r}")
            continue
        if claimed_count != len(excluded_ids):
            errors.append(f"{label}_mismatch:claimed={claimed_count},actual={len(excluded_ids)}")
    excluded_hash_claims = {
        "unverifiedRegistryCandidatesExcludedSha256": metadata.get(
            "unverifiedRegistryCandidatesExcludedSha256"
        ),
        "unverifiedRegistryCandidatesExcluded.sha256": excluded.get("sha256"),
    }
    for label, raw_digest in excluded_hash_claims.items():
        claimed_digest = str(raw_digest or "").strip().lower()
        if not HEX64.fullmatch(claimed_digest):
            errors.append(f"invalid_{label}:{claimed_digest or '<empty>'}")
        elif claimed_digest != excluded_sha:
            errors.append(f"{label}_mismatch:claimed={claimed_digest},actual={excluded_sha}")

    generated_at_raw = str(metadata.get("generatedAt") or "").strip()
    try:
        generated_at = parse_instant(generated_at_raw)
    except (TypeError, ValueError):
        generated_at = None
        errors.append(f"invalid_worklist_generated_at:{generated_at_raw or '<empty>'}")
    if generated_at is not None and generated_at < not_before:
        errors.append(f"stale_worklist_generation:generatedAt={utc_iso(generated_at)}")
    if not worklist_ids:
        errors.append("empty_worklist")
    if errors:
        raise IncompleteCensus(";".join(errors))
    return (
        metadata,
        ids_path,
        worklist_ids,
        actual_sha,
        excluded_path,
        excluded_ids,
        excluded_sha,
    )


def validate_exact_page_manifest(
    manifest_path: Path,
    worklist_metadata_path: Path,
    not_before: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, int]]:
    """Build strict PSA rows from immutable exact-page evidence.

    Manifest/card-directory IDs are request identities only.  The census key is
    exclusively ``population_data[grader=psa].gemrate_id`` from the receipt-
    bound raw payload.  Missing qualified PSA-row IDs are returned as explicit
    blockers instead of being replaced by the request ID.
    """

    errors: list[str] = []
    threshold = not_before.timestamp()
    (
        worklist_metadata,
        worklist_ids_path,
        worklist_ids,
        worklist_sha,
        excluded_ids_path,
        excluded_ids,
        excluded_sha,
    ) = validate_worklist_metadata(worklist_metadata_path, not_before)
    worklist_id_set = set(worklist_ids)
    excluded_id_set = set(excluded_ids)
    if not manifest_path.is_file():
        raise IncompleteCensus(f"missing_exact_page_manifest:{manifest_path}")
    if manifest_path.stat().st_mtime < threshold:
        errors.append(
            "stale_exact_page_manifest:"
            f"mtime={utc_iso(datetime.fromtimestamp(manifest_path.stat().st_mtime, timezone.utc))}"
        )

    manifest = read_json_object(manifest_path, "exact_page_manifest")
    attempted = _manifest_int(manifest, "attempted", errors)
    succeeded = _manifest_int(manifest, "succeeded", errors)
    failed = _manifest_int(manifest, "failed", errors)
    if attempted <= 0:
        errors.append(f"empty_manifest_attempt:{attempted}")
    # Daily refresh only calls the verified worklist.  The one-off 2026-08-25
    # recovery run also attempted the registry-only exclusions so that every
    # legacy candidate received an explicit failure receipt.  Accept both
    # evidence shapes; exclusions are never required network work afterwards.
    attempted_registry_exclusions = attempted == len(worklist_ids) + len(excluded_ids)
    if attempted not in {len(worklist_ids), len(worklist_ids) + len(excluded_ids)}:
        errors.append(
            f"source_run_attempted_scope_mismatch:attempted={attempted},"
            f"verified={len(worklist_ids)},excluded={len(excluded_ids)}"
        )
    expected_partial = failed > 0
    if manifest.get("partial") is not expected_partial:
        errors.append(f"source_run_partial_mismatch:{manifest.get('partial')!r}!={expected_partial!r}")
    if failed and manifest.get("promotable") is not False:
        errors.append(f"partial_source_run_must_not_be_promotable:{manifest.get('promotable')!r}")
    if failed and manifest.get("promoted") is not False:
        errors.append(f"partial_source_run_must_not_be_promoted:{manifest.get('promoted')!r}")

    fetched_at_raw = str(manifest.get("fetchedAt") or "").strip()
    try:
        fetched_at = parse_instant(fetched_at_raw)
    except (TypeError, ValueError):
        fetched_at = None
        errors.append(f"invalid_manifest_fetched_at:{fetched_at_raw or '<empty>'}")
    if fetched_at is not None and fetched_at < not_before:
        errors.append(f"stale_manifest_fetch:fetchedAt={utc_iso(fetched_at)}")

    observations = manifest.get("observations")
    if not isinstance(observations, list):
        observations = []
        errors.append("manifest_observations_not_list")
    if len(observations) != succeeded:
        errors.append(f"observation_count_mismatch:observations={len(observations)},succeeded={succeeded}")
    unresolved = manifest.get("unresolved")
    if not isinstance(unresolved, list):
        unresolved = []
        errors.append("manifest_unresolved_not_list")
    if len(unresolved) != failed:
        errors.append(f"unresolved_count_mismatch:unresolved={len(unresolved)},failed={failed}")

    observations_by_id: dict[str, dict[str, Any]] = {}
    duplicate_observations = 0
    observation_dates: list[str] = []
    for index, raw in enumerate(observations, 1):
        if not isinstance(raw, dict):
            errors.append(f"non_object_observation:{index}")
            continue
        gemrate_id = str(raw.get("gemrateId") or "").strip().lower()
        if not HEX40.fullmatch(gemrate_id):
            errors.append(f"invalid_observation_gemrate_id:{index}:{gemrate_id or '<empty>'}")
            continue
        if str(raw.get("authority") or "").lower() != "gemrate":
            errors.append(f"non_gemrate_authority:{gemrate_id}:{raw.get('authority')!r}")
        if str(raw.get("transport") or "") != "gemrate_public_card_page":
            errors.append(f"non_exact_page_transport:{gemrate_id}:{raw.get('transport')!r}")
        try:
            population = int(raw.get("populationPsa10"))
        except (TypeError, ValueError):
            population = -1
            errors.append(f"invalid_psa10_population:{gemrate_id}:{raw.get('populationPsa10')!r}")
        if population < 0:
            errors.append(f"negative_psa10_population:{gemrate_id}:{population}")
        normalized = dict(raw)
        normalized["gemrateId"] = gemrate_id
        normalized["populationPsa10"] = population
        existing = observations_by_id.get(gemrate_id)
        if existing is not None:
            duplicate_observations += 1
            if int(existing["populationPsa10"]) != population:
                errors.append(
                    "conflicting_duplicate_observation:"
                    f"{gemrate_id}:{existing['populationPsa10']}!={population}"
                )
            continue
        observations_by_id[gemrate_id] = normalized
        observed_date = str(raw.get("sourceDate") or raw.get("effectiveDate") or "").strip()
        if observed_date:
            observation_dates.append(observed_date)

    unresolved_by_id: dict[str, str] = {}
    for index, raw in enumerate(unresolved, 1):
        if not isinstance(raw, Mapping):
            errors.append(f"non_object_unresolved:{index}")
            continue
        gemrate_id = str(raw.get("gemrateId") or "").strip().lower()
        if not HEX40.fullmatch(gemrate_id):
            errors.append(f"invalid_unresolved_gemrate_id:{index}:{gemrate_id or '<empty>'}")
            continue
        if gemrate_id in unresolved_by_id:
            errors.append(f"duplicate_unresolved_gemrate_id:{gemrate_id}")
            continue
        unresolved_by_id[gemrate_id] = str(raw.get("reason") or "unreported").strip() or "unreported"

    observation_id_set = set(observations_by_id)
    if not observation_id_set <= worklist_id_set:
        extra = sorted(observation_id_set - worklist_id_set)
        errors.append(
            "manifest_observations_outside_worklist:"
            f"count={len(extra)}:{','.join(extra[:20])}"
        )
    unresolved_id_set = set(unresolved_by_id)
    unexpected_unresolved = sorted(unresolved_id_set - worklist_id_set - excluded_id_set)
    if unexpected_unresolved:
        errors.append(
            "manifest_unresolved_outside_bound_attempt_scope:"
            f"count={len(unexpected_unresolved)}:{','.join(unexpected_unresolved[:20])}"
        )
    missing_excluded = sorted(excluded_id_set - unresolved_id_set)
    if attempted_registry_exclusions and missing_excluded:
        errors.append(
            "manifest_excluded_registry_ids_not_unresolved:"
            f"count={len(missing_excluded)}:{','.join(missing_excluded[:20])}"
        )
    scope_failure_ids = sorted(unresolved_id_set & worklist_id_set)
    if observation_id_set | set(scope_failure_ids) != worklist_id_set:
        missing = sorted(worklist_id_set - observation_id_set - set(scope_failure_ids))
        errors.append(
            "manifest_worklist_not_fully_accounted:"
            f"missing={len(missing)}:{','.join(missing[:20])}"
        )
    if observation_id_set & unresolved_id_set:
        errors.append("manifest_observation_and_unresolved_sets_overlap")
    if len(observation_id_set | unresolved_id_set) != attempted:
        errors.append(
            "manifest_attempted_union_count_mismatch:"
            f"union={len(observation_id_set | unresolved_id_set)},attempted={attempted}"
        )
    if succeeded != len(observations_by_id):
        errors.append(
            f"manifest_succeeded_observation_count_mismatch:"
            f"succeeded={succeeded},unique_observations={len(observations_by_id)}"
        )
    if failed != len(unresolved_by_id):
        errors.append(
            f"manifest_failed_unresolved_count_mismatch:"
            f"failed={failed},unique_unresolved={len(unresolved_by_id)}"
        )

    staged_root = manifest_path.parent / "cards"
    census_by_psa_id: dict[str, dict[str, Any]] = {}
    strict_observed_populations: dict[str, int] = {}
    staged_hashes: list[tuple[str, str]] = []
    raw_hashes: list[tuple[str, str]] = []
    replacement_pairs: list[tuple[str, str]] = []
    request_to_provider_entity_pairs: list[tuple[str, str]] = []
    request_layer_provenance_anomalies: list[dict[str, Any]] = []
    missing_strict_psa_id_request_ids: list[str] = []
    unresolved_qualified_printings: list[dict[str, Any]] = []
    qualified_request_rows_with_strict_id = 0
    duplicate_strict_psa_row_count = 0
    raw_psa_row_count = 0

    for request_id, observation in sorted(observations_by_id.items()):
        card_dir = staged_root / request_id
        details_path = card_dir / "card_details.json"
        try:
            details = read_json_object(details_path, f"staged_card_details:{request_id}")
        except IncompleteCensus as error:
            errors.append(str(error))
            continue
        staged_hashes.append((request_id, sha256_file(details_path)))
        staged_id = str(details.get("gemrate_id") or "").strip().lower()
        if staged_id != request_id:
            request_layer_provenance_anomalies.append(
                {"requestId": request_id, "field": "card_details.gemrate_id", "value": staged_id or None}
            )
        staged_population = _psa10_from_card_details(details)
        observation_population = int(observation["populationPsa10"])
        if staged_population != observation_population:
            request_layer_provenance_anomalies.append(
                {
                    "requestId": request_id,
                    "field": "card_details.populationPsa10",
                    "value": staged_population,
                    "manifestValue": observation_population,
                }
            )

        public_page = details.get("publicCardPage")
        if not isinstance(public_page, Mapping):
            public_page = {}
            errors.append(f"missing_public_card_page:{request_id}")
        provider_id = str(public_page.get("providerEntityGemrateId") or "").strip().lower()
        if provider_id and not HEX40.fullmatch(provider_id):
            request_layer_provenance_anomalies.append(
                {"requestId": request_id, "field": "publicCardPage.providerEntityGemrateId", "value": provider_id}
            )
        elif provider_id and provider_id != request_id:
            # Public card routes may canonicalize/redirect to another provider
            # entity.  Both values are provenance only; neither may substitute
            # for the raw PSA grader row identity.
            request_to_provider_entity_pairs.append((request_id, provider_id))
        if public_page.get("routeVerified") is not True:
            request_layer_provenance_anomalies.append(
                {"requestId": request_id, "field": "publicCardPage.routeVerified", "value": public_page.get("routeVerified")}
            )
        canonical_url = compact_text(public_page.get("canonicalUrl"))
        if not canonical_url or not canonical_url.startswith("https://www.gemrate.com/card/"):
            request_layer_provenance_anomalies.append(
                {"requestId": request_id, "field": "publicCardPage.canonicalUrl", "value": canonical_url}
            )

        receipt = details.get("privateSourceReceipt")
        if not isinstance(receipt, Mapping):
            receipt = {}
            errors.append(f"missing_private_source_receipt:{request_id}")
        if str(receipt.get("gemrateId") or "").strip().lower() != request_id:
            request_layer_provenance_anomalies.append(
                {
                    "requestId": request_id,
                    "field": "privateSourceReceipt.gemrateId",
                    "value": str(receipt.get("gemrateId") or "").strip().lower() or None,
                }
            )
        if str(receipt.get("transport") or "") != "gemrate_public_card_page":
            errors.append(f"receipt_non_exact_page_transport:{request_id}:{receipt.get('transport')!r}")

        receipt_sha = str(receipt.get("contentSha256") or "").strip().lower()
        if not HEX64.fullmatch(receipt_sha):
            errors.append(f"invalid_staged_raw_content_sha:{request_id}:{receipt_sha or '<empty>'}")
        source_pointer = str(receipt.get("sourcePointer") or "").strip()
        pointer_path = Path(source_pointer) if source_pointer else Path("_missing_raw_pointer_")
        if not source_pointer or pointer_path.is_absolute() or ".." in pointer_path.parts:
            errors.append(f"invalid_staged_raw_source_pointer:{request_id}:{source_pointer or '<empty>'}")
            raw_path = card_dir / "_missing_raw_pointer_"
        else:
            raw_path = (card_dir / pointer_path).resolve()
            try:
                raw_path.relative_to(card_dir.resolve())
            except ValueError:
                errors.append(f"staged_raw_pointer_escapes_card_dir:{request_id}:{source_pointer}")
        try:
            raw = read_json_object(raw_path, f"staged_raw_card_details:{request_id}")
        except IncompleteCensus as error:
            errors.append(str(error))
            raw = {}
        if raw_path.is_file():
            raw_sha = sha256_file(raw_path)
            if raw_sha != receipt_sha:
                errors.append(
                    f"staged_raw_sha_mismatch:{request_id}:receipt={receipt_sha},actual={raw_sha}"
                )
        else:
            raw_sha = None
        if raw_sha:
            raw_hashes.append((request_id, raw_sha))

        psa_row = _raw_psa_population_row(raw, request_id, errors, "receipt_raw")
        if psa_row is None:
            continue
        raw_psa_row_count += 1
        raw_population = int(psa_row["populationPsa10"])
        if raw_population != observation_population:
            request_layer_provenance_anomalies.append(
                {
                    "requestId": request_id,
                    "field": "manifest.populationPsa10",
                    "value": observation_population,
                    "rawPsaAuthorityValue": raw_population,
                }
            )
        raw_full_name = psa_row["fullName"]
        raw_psa_identity = psa_row["row"]
        strict_psa_id = psa_row["psaId"]
        set_name = raw_psa_identity.get("set_name") or raw_psa_identity.get("setName")
        card_number = raw_psa_identity.get("card_number") or raw_psa_identity.get("cardNumber")
        parallel = raw_psa_identity.get("parallel") or raw_psa_identity.get("card_set_parallel")
        year = raw_psa_identity.get("year")
        source_date = observation.get("sourceDate") or details.get("date") or observation.get("effectiveDate")

        raw_receipt_provenance = {
            "authority": "gemrate",
            "transport": observation.get("transport"),
            "requestId": request_id,
            "providerEntityGemrateId": provider_id or None,
            "artifactPath": str(raw_path),
            "artifactSha256": raw_sha,
            "receiptPath": str(details_path),
            "receiptContentSha256": receipt_sha or None,
            "sourcePointer": source_pointer,
            "populationField": "population_data[grader=psa].grades.g10",
            "identityField": "population_data[grader=psa].gemrate_id",
            "fullNameField": "population_data[grader=psa].description",
            "observedDate": source_date,
            "preservation": "verbatim_no_compact_no_split",
        }

        if strict_psa_id is None:
            missing_strict_psa_id_request_ids.append(request_id)
            if raw_population >= 1000:
                unresolved_qualified_printings.append(
                    {
                        "requestId": request_id,
                        "pop": raw_population,
                        "populationPsa10": raw_population,
                        "fullName": raw_full_name,
                        "year": year,
                        "set": set_name,
                        "setName": set_name,
                        "number": card_number,
                        "collectorNumber": card_number,
                        "parallel": parallel,
                        "rawReceiptProvenance": raw_receipt_provenance,
                        "primaryReason": "qualified_raw_psa_row_missing_valid_gemrate_id",
                        "allowedNextAction": "research_and_return_strict_psa_identity_evidence_only",
                    }
                )
            continue

        if request_id != strict_psa_id:
            replacement_pairs.append((request_id, strict_psa_id))
        existing_population = strict_observed_populations.get(strict_psa_id)
        if existing_population is not None:
            duplicate_strict_psa_row_count += 1
            if existing_population != raw_population:
                errors.append(
                    f"conflicting_strict_psa_population:{strict_psa_id}:"
                    f"existing={existing_population},request={request_id},raw={raw_population}"
                )
        else:
            strict_observed_populations[strict_psa_id] = raw_population

        if raw_population < 1000:
            continue
        qualified_request_rows_with_strict_id += 1
        request_evidence = {
            "requestId": request_id,
            "canonicalUrl": canonical_url,
            "rawArtifactPath": str(raw_path),
            "rawArtifactSha256": raw_sha,
            "receiptPath": str(details_path),
        }
        candidate = {
                "psa_id": strict_psa_id,
                "psa_10": raw_population,
                "name": raw_full_name,
                "full_name": raw_full_name,
                "raw_full_name": raw_full_name,
                "name_source": "exact_staged_raw.population_data[grader=psa].description",
                "name_provenance": {
                    **raw_receipt_provenance,
                    "field": "population_data[grader=psa].description",
                    "psaId": strict_psa_id,
                },
                "category": raw_psa_identity.get("category") or details.get("category"),
                "year": year,
                "set_name": set_name,
                "card_number": card_number,
                "parallel": parallel,
                "psa_details": {
                    "authority": observation.get("authority"),
                    "transport": observation.get("transport"),
                    "strictPsaId": strict_psa_id,
                    "requestIds": [request_id],
                    "requestEvidence": [request_evidence],
                    "graderPopulations": observation.get("graderPopulations"),
                    "rawPsaPopulationRow": json_value(raw_psa_identity),
                    "sourceDate": observation.get("sourceDate"),
                    "effectiveDate": observation.get("effectiveDate"),
                    "fetchedAt": observation.get("fetchedAt"),
                    "authorityFullName": raw_full_name,
                    "publicPageTitle": public_page.get("title"),
                    "rawContentSha256": raw_sha,
                },
                "psa_url": canonical_url,
                "psa_date": source_date,
                "_set_id": raw_psa_identity.get("set_id") or raw_psa_identity.get("setId"),
                "_set_name": set_name,
            }
        existing = census_by_psa_id.get(strict_psa_id)
        if existing is None:
            census_by_psa_id[strict_psa_id] = candidate
        else:
            if (
                int(existing["psa_10"]) != raw_population
                or existing["raw_full_name"] != raw_full_name
                or existing.get("year") != year
                or existing.get("set_name") != set_name
                or existing.get("card_number") != card_number
                or existing.get("parallel") != parallel
            ):
                errors.append(
                    f"conflicting_duplicate_strict_psa_identity:{strict_psa_id}:request={request_id}"
                )
            else:
                existing_details = existing["psa_details"]
                existing_details["requestIds"].append(request_id)
                existing_details["requestEvidence"].append(request_evidence)

    if not census_by_psa_id and not unresolved_qualified_printings:
        errors.append("empty_qualified_exact_page_census")
    if errors:
        raise IncompleteCensus(";".join(errors))

    census_rows = list(census_by_psa_id.values())
    unresolved_qualified_printings.sort(
        key=lambda row: (-int(row["populationPsa10"]), str(row["requestId"]))
    )

    aggregate = hashlib.sha256()
    for request_id, digest in staged_hashes:
        aggregate.update(f"details:{request_id}:{digest}\n".encode("ascii"))
    for request_id, digest in raw_hashes:
        aggregate.update(f"raw:{request_id}:{digest}\n".encode("ascii"))
    replacement_pairs_sha = _replacement_pairs_sha256(replacement_pairs)
    missing_id_digest = hashlib.sha256()
    for request_id in sorted(missing_strict_psa_id_request_ids):
        missing_id_digest.update(f"{request_id}\n".encode("ascii"))
    source_failures = [
        {
            "requestId": request_id,
            "reason": unresolved_by_id[request_id],
            "latestStoredPopulation": None,
            "cohort": None,
        }
        for request_id in scope_failure_ids
    ]
    has_scope_blockers = bool(unresolved_qualified_printings or source_failures)
    dates = sorted(observation_dates)
    strict_known_summary = {
        "worklistRequestIds": len(worklist_ids),
        "successfulExactPageRequests": len(observations_by_id),
        "sourceFailureRequests": len(source_failures),
        "rawPsaRows": raw_psa_row_count,
        "rawPsaRowsWithValidStrictId": raw_psa_row_count - len(missing_strict_psa_id_request_ids),
        "rawPsaRowsMissingValidStrictId": len(missing_strict_psa_id_request_ids),
        "rawPsaRowsMissingValidStrictIdBelowPopulationFloor": (
            len(missing_strict_psa_id_request_ids) - len(unresolved_qualified_printings)
        ),
        "strictUniquePsaIdsObserved": len(strict_observed_populations),
        "qualifiedRequestRowsWithStrictPsaId": qualified_request_rows_with_strict_id,
        "qualifiedStrictUniquePsaIds": len(census_by_psa_id),
        "duplicateStrictPsaRowCount": duplicate_strict_psa_row_count,
        "unresolvedQualifiedPrintings": len(unresolved_qualified_printings),
    }
    metadata = {
        "status": "INCOMPLETE_CENSUS" if has_scope_blockers else "COMPLETE_VERIFIED_SCOPE_EXACT_PAGE_LANE",
        "declaredVerifiedScopeStatus": "INCOMPLETE" if has_scope_blockers else "COMPLETE",
        "candidateScopeStatus": worklist_metadata.get("status"),
        "source": "GemRate exact public card pages for the verified hybrid census worklist",
        "scopeKind": "verified_cardz_hybrid_exact_page_lane",
        "scope": (
            "exact refresh of verified latest-generation IDs missing from the fresh fixed snapshot "
            "plus fresh qualified IDs outside that verified generation"
        ),
        "providerGlobal": False,
        "limitation": (
            "Request/page IDs are not PSA IDs. Only the receipt-bound raw PSA population row can "
            "supply psaId; unresolved qualified PSA-row identities and source failures stop census completion."
        ),
        "path": str(manifest_path),
        "manifestPath": str(manifest_path),
        "immutableStagedCardDetailsRoot": str(staged_root),
        "capturedAt": utc_iso(fetched_at) if fetched_at is not None else None,
        "refreshNotBefore": utc_iso(not_before),
        "sha256": sha256_file(manifest_path),
        "immutableStagedEvidenceAggregateSha256": aggregate.hexdigest(),
        "manifestSchemaVersion": manifest.get("schemaVersion"),
        "manifestRunId": manifest.get("runId"),
        "worklistMetadataPath": str(worklist_metadata_path),
        "worklistMetadataSha256": sha256_file(worklist_metadata_path),
        "worklistIdsPath": str(worklist_ids_path),
        "worklistIdsSha256": worklist_sha,
        "worklistCount": len(worklist_ids),
        "excludedRegistryIdsPath": str(excluded_ids_path),
        "excludedRegistryIdsSha256": excluded_sha,
        "excludedRegistryIdsCount": len(excluded_ids),
        "excludedRegistryIdsAttempted": attempted_registry_exclusions,
        "excludedRegistryContract": json_value(
            worklist_metadata.get("unverifiedRegistryCandidatesExcluded")
        ),
        "excludedRegistryReason": get_nested(
            worklist_metadata,
            "unverifiedRegistryCandidatesExcluded",
            "reason",
        ),
        "worklistScope": json_value(worklist_metadata.get("scope")),
        "worklistCounts": json_value(worklist_metadata.get("counts")),
        "worklistSourceProvenance": json_value(worklist_metadata.get("sourceProvenance")),
        "worklistFreshSnapshot": json_value(worklist_metadata.get("freshSnapshot")),
        "manifestAttempted": attempted,
        "manifestSucceeded": succeeded,
        "manifestFailed": failed,
        "manifestPartial": bool(manifest.get("partial")),
        "manifestPromotable": bool(manifest.get("promotable")),
        "manifestPromoted": bool(manifest.get("promoted")),
        "sourceRunAttempted": attempted,
        "sourceRunSucceeded": succeeded,
        "sourceRunFailed": failed,
        "sourceRunPartial": bool(manifest.get("partial")),
        "sourceRunPromotable": bool(manifest.get("promotable")),
        "sourceRunPromoted": bool(manifest.get("promoted")),
        "sourceRunUnresolvedReasons": dict(sorted(Counter(unresolved_by_id.values()).items())),
        "sourceFailures": source_failures,
        "sourceObservationCount": len(observations),
        "uniqueRequestIdCount": len(observations_by_id),
        "requestLayerProvenanceAnomalies": json_value(request_layer_provenance_anomalies),
        "requestLayerProvenanceAnomalyCount": len(request_layer_provenance_anomalies),
        "duplicateObservationCount": duplicate_observations,
        "filteredBelowPopulationFloorCount": sum(
            1 for population in strict_observed_populations.values() if population < 1000
        ),
        "rowCount": len(census_rows),
        "uniquePsaIdCount": len(census_by_psa_id),
        "populationFloor": 1000,
        "exactPageTransport": "gemrate_public_card_page",
        "psaIdentityAuthorityField": "population_data[grader=psa].gemrate_id",
        "fullNameAuthorityField": "population_data[grader=psa].description",
        "requestedToStrictPsaIdReplacements": {
            "count": len(replacement_pairs),
            "aggregateSha256": replacement_pairs_sha,
            "canonicalBytes": "requestedId<TAB>strictPsaId<LF>, sorted by pair",
        },
        "requestToProviderEntityRedirects": {
            "count": len(request_to_provider_entity_pairs),
            "aggregateSha256": _replacement_pairs_sha256(request_to_provider_entity_pairs),
            "canonicalBytes": "requestId<TAB>providerEntityGemrateId<LF>, sorted by pair",
            "identityRole": "provenance_only",
        },
        "missingStrictPsaIdRequestIds": {
            "count": len(missing_strict_psa_id_request_ids),
            "aggregateSha256": missing_id_digest.hexdigest(),
            "canonicalBytes": "requestId<LF>, sorted ascending",
        },
        "strictKnownSummary": strict_known_summary,
        "unresolvedQualifiedPrintings": unresolved_qualified_printings,
        "psaObservationDateMin": dates[0] if dates else None,
        "psaObservationDateMax": dates[-1] if dates else None,
    }
    return census_rows, metadata, strict_observed_populations


def build_hybrid_census(
    fresh_rows: list[dict[str, Any]],
    fresh_meta: dict[str, Any],
    fresh_all_psa_ids: set[str],
    exact_rows: list[dict[str, Any]],
    exact_meta: dict[str, Any],
    exact_observed_populations: Mapping[str, int],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    errors: list[str] = []
    snapshot_claim = exact_meta.get("worklistFreshSnapshot")
    if not isinstance(snapshot_claim, Mapping):
        snapshot_claim = {}
        errors.append("missing_worklist_fresh_snapshot_contract")
    all_claim = snapshot_claim.get("allCards")
    qualified_claim = snapshot_claim.get("qualified")
    if not isinstance(all_claim, Mapping):
        all_claim = {}
        errors.append("missing_worklist_fresh_all_cards_contract")
    if not isinstance(qualified_claim, Mapping):
        qualified_claim = {}
        errors.append("missing_worklist_fresh_qualified_contract")

    artifact_checks = (
        ("all_cards", fresh_meta.get("allCardsSha256"), all_claim.get("sha256")),
        ("qualified", fresh_meta.get("sha256"), qualified_claim.get("sha256")),
    )
    for label, actual, claimed in artifact_checks:
        if str(actual or "").lower() != str(claimed or "").lower():
            errors.append(f"fresh_snapshot_{label}_sha_mismatch:actual={actual},claimed={claimed}")
    path_checks = (
        ("all_cards", fresh_meta.get("allCardsPath"), all_claim.get("path")),
        ("qualified", fresh_meta.get("path"), qualified_claim.get("path")),
    )
    for label, actual, claimed in path_checks:
        if not actual or not claimed or Path(str(actual)).resolve() != Path(str(claimed)).resolve():
            errors.append(f"fresh_snapshot_{label}_path_mismatch:actual={actual},claimed={claimed}")
    count_checks = (
        ("all_cards_unique", fresh_meta.get("sourceUniquePsaIdCount"), all_claim.get("uniquePsaIds")),
        ("qualified_unique", fresh_meta.get("uniquePsaIdCount"), qualified_claim.get("uniquePsaIds")),
    )
    for label, actual, claimed in count_checks:
        try:
            if int(actual) != int(claimed):
                errors.append(f"fresh_snapshot_{label}_count_mismatch:actual={actual},claimed={claimed}")
        except (TypeError, ValueError):
            errors.append(f"fresh_snapshot_{label}_invalid_count:actual={actual},claimed={claimed}")

    counts = exact_meta.get("worklistCounts")
    if not isinstance(counts, Mapping):
        counts = {}
        errors.append("missing_hybrid_worklist_counts")
    try:
        expected_worklist = int(counts.get("exactRefreshWorklist"))
        coverage_universe = int(counts.get("coverageUniverse"))
        fresh_snapshot_all = int(counts.get("freshSnapshotAll"))
        fresh_below_excluded = int(counts.get("freshBelowThresholdExcludedFromExactRefresh"))
    except (TypeError, ValueError):
        expected_worklist = coverage_universe = fresh_snapshot_all = fresh_below_excluded = -1
        errors.append("invalid_hybrid_worklist_counts")
    source_failures = exact_meta.get("sourceFailures")
    if not isinstance(source_failures, list):
        source_failures = []
        errors.append("invalid_exact_source_failures")
    successful_request_count = int(exact_meta.get("uniqueRequestIdCount") or 0)
    if expected_worklist != successful_request_count + len(source_failures):
        errors.append(
            f"exact_request_worklist_accounting_mismatch:successful={successful_request_count},"
            f"failed={len(source_failures)},expected={expected_worklist}"
        )
    if fresh_snapshot_all != int(fresh_meta.get("sourceUniquePsaIdCount") or -1):
        errors.append(
            f"fresh_snapshot_coverage_mismatch:artifact={fresh_meta.get('sourceUniquePsaIdCount')},"
            f"worklist={fresh_snapshot_all}"
        )

    fresh_by_id = {str(row["psa_id"]).lower(): row for row in fresh_rows}
    exact_by_id = {str(row["psa_id"]).lower(): row for row in exact_rows}
    if len(fresh_all_psa_ids) != fresh_snapshot_all:
        errors.append(
            f"fresh_all_id_set_count_mismatch:actual={len(fresh_all_psa_ids)},expected={fresh_snapshot_all}"
        )
    if not set(fresh_by_id) <= fresh_all_psa_ids:
        errors.append("fresh_qualified_ids_not_subset_of_fresh_all")
    overlap_ids = sorted(set(fresh_by_id) & set(exact_observed_populations))
    exact_snapshot_overlap = set(exact_observed_populations) & fresh_all_psa_ids
    consistent_ids = [
        gemrate_id
        for gemrate_id in overlap_ids
        if int(fresh_by_id[gemrate_id]["psa_10"]) == int(exact_observed_populations[gemrate_id])
    ]
    changed_ids = sorted(set(overlap_ids) - set(consistent_ids))
    dropped_ids = [
        gemrate_id
        for gemrate_id in overlap_ids
        if int(exact_observed_populations[gemrate_id]) < 1000
    ]

    merged_by_id = dict(fresh_by_id)
    for gemrate_id, population in exact_observed_populations.items():
        if int(population) < 1000:
            merged_by_id.pop(gemrate_id, None)
            continue
        exact_row = exact_by_id.get(gemrate_id)
        if exact_row is None:
            errors.append(f"missing_qualified_exact_row:{gemrate_id}:{population}")
            continue
        merged_by_id[gemrate_id] = exact_row
    if any(int(row.get("psa_10") or 0) < 1000 for row in merged_by_id.values()):
        errors.append("hybrid_census_contains_below_threshold_row")
    if errors:
        raise IncompleteCensus(";".join(errors))

    merged_rows = list(merged_by_id.values())
    dates = sorted(str(row.get("psa_date") or "") for row in merged_rows if row.get("psa_date"))
    captured_values: list[datetime] = []
    for raw in (fresh_meta.get("capturedAt"), exact_meta.get("capturedAt")):
        if raw:
            captured_values.append(parse_instant(str(raw)))
    source_digest = hashlib.sha256()
    for label, digest in (
        ("fresh_qualified", str(fresh_meta["sha256"])),
        ("fresh_all", str(fresh_meta["allCardsSha256"])),
        ("exact_manifest", str(exact_meta["sha256"])),
        ("exact_worklist", str(exact_meta["worklistIdsSha256"])),
        ("excluded_registry", str(exact_meta["excludedRegistryIdsSha256"])),
        (
            "request_to_strict_psa_replacements",
            str(get_nested(exact_meta, "requestedToStrictPsaIdReplacements", "aggregateSha256")),
        ),
    ):
        source_digest.update(f"{label}:{digest}\n".encode("ascii"))

    unresolved_qualified = exact_meta.get("unresolvedQualifiedPrintings")
    if not isinstance(unresolved_qualified, list):
        unresolved_qualified = []
        errors.append("invalid_unresolved_qualified_printings")
    if errors:
        raise IncompleteCensus(";".join(errors))

    has_census_blockers = bool(unresolved_qualified or source_failures)
    merge_meta = {
        "freshQualifiedRows": len(fresh_by_id),
        "exactPageQualifiedRows": len(exact_by_id),
        "exactPageStrictPsaIdsObserved": len(exact_observed_populations),
        "exactPageSuccessfulRequestRows": successful_request_count,
        "overlapExactOverrides": len(overlap_ids),
        "exactStrictIdsCoveredByFreshAllSnapshot": len(exact_snapshot_overlap),
        "overlapPopulationConsistent": len(consistent_ids),
        "overlapPopulationChanged": len(changed_ids),
        "overlapDroppedBelowPopulationFloor": len(dropped_ids),
        "exactQualifiedOutsideFreshSnapshot": len(set(exact_by_id) - set(fresh_by_id)),
        "finalQualifiedRows": len(merged_rows),
        "precedence": "exact public-card-page observation overrides fixed-snapshot observation",
    }
    exact_strict_summary = exact_meta.get("strictKnownSummary")
    if not isinstance(exact_strict_summary, Mapping):
        exact_strict_summary = {}
    strict_known_summary = {
        "freshQualifiedStrictPsaIds": len(fresh_by_id),
        "exactQualifiedStrictPsaIds": len(exact_by_id),
        "exactObservedStrictPsaIdsAllPopulations": len(exact_observed_populations),
        "knownQualifiedStrictPsaIdsAfterMerge": len(merged_rows),
        "unresolvedQualifiedPrintings": len(unresolved_qualified),
        "sourceFailureRequests": len(source_failures),
        "exactLane": json_value(exact_strict_summary),
    }
    blockers: list[str] = []
    if unresolved_qualified:
        blockers.append("qualified_raw_psa_rows_missing_valid_psa_id")
    if source_failures:
        blockers.append("exact_page_source_failures_within_verified_worklist")
    evidence_summary = {
        "censusBlockers": blockers,
        "requestIdIsNeverPsaId": True,
        "strictPsaIdField": "population_data[grader=psa].gemrate_id",
        "exactFullNameField": "population_data[grader=psa].description",
        "freshFullNameField": "psa_details",
        "requestedToStrictPsaIdReplacements": json_value(
            exact_meta.get("requestedToStrictPsaIdReplacements")
        ),
        "missingStrictPsaIdRequestIds": json_value(exact_meta.get("missingStrictPsaIdRequestIds")),
        "unresolvedQualifiedPrintingCount": len(unresolved_qualified),
        "sourceFailureCount": len(source_failures),
    }
    metadata = {
        "status": "INCOMPLETE_CENSUS" if has_census_blockers else "INCOMPLETE_PROVIDER_GLOBAL",
        "providerGlobalStatus": "INCOMPLETE_PROVIDER_GLOBAL",
        "declaredVerifiedScopeStatus": "INCOMPLETE" if has_census_blockers else "COMPLETE",
        "candidateScopeStatus": (
            "INCOMPLETE_VERIFIED_GENERATION_PLUS_PUBLIC_SNAPSHOT_COVERAGE"
            if has_census_blockers
            else "COMPLETE_VERIFIED_GENERATION_PLUS_PUBLIC_SNAPSHOT_COVERAGE"
        ),
        "source": "verified hybrid GemRate fixed public-set snapshot plus exact public-card-page refresh",
        "scopeKind": "verified_cardz_hybrid_census",
        "scope": (
            "PSA10 threshold evaluation across the verified latest catalog generation plus every ID "
            "in the fresh fixed public-set snapshot; currently incomplete where strict PSA-row identity "
            "or exact-page source evidence is unresolved, and never provider-global"
        ),
        "providerGlobal": False,
        "limitation": (
            "GemRate exposes no complete provider-wide public ID or set directory. Coverage is "
            "bounded to the declared verified generation plus fresh fixed-snapshot universe. Request IDs "
            "cannot substitute for PSA IDs; unresolved strict identities prevent a complete census."
        ),
        "path": {
            "freshQualified": fresh_meta.get("path"),
            "freshAllCards": fresh_meta.get("allCardsPath"),
            "exactManifest": exact_meta.get("manifestPath"),
            "exactWorklist": exact_meta.get("worklistIdsPath"),
        },
        "capturedAt": utc_iso(max(captured_values)) if captured_values else None,
        "refreshNotBefore": exact_meta.get("refreshNotBefore"),
        "sha256": source_digest.hexdigest(),
        "rowCount": len(merged_rows),
        "uniquePsaIdCount": len(merged_by_id),
        "knownStrictQualifiedPsaIdCount": len(merged_by_id),
        "populationFloor": 1000,
        "coverageUniverseCount": coverage_universe,
        "coverageUniverseIdentitySpace": "manifest request IDs plus fresh snapshot PSA IDs",
        "freshSnapshotAllCount": fresh_snapshot_all,
        "freshSnapshotQualifiedCount": len(fresh_by_id),
        "freshBelowThresholdExcludedFromExactRefresh": fresh_below_excluded,
        "worklistCount": exact_meta.get("worklistCount"),
        "worklistIdsSha256": exact_meta.get("worklistIdsSha256"),
        "unverifiedRegistryCandidatesExcludedCount": exact_meta.get("excludedRegistryIdsCount"),
        "unverifiedRegistryCandidatesExcludedSha256": exact_meta.get("excludedRegistryIdsSha256"),
        "unverifiedRegistryCandidatesExcludedReason": exact_meta.get("excludedRegistryReason"),
        "manifestRunId": exact_meta.get("manifestRunId"),
        "manifestAttempted": exact_meta.get("manifestAttempted"),
        "manifestSucceeded": exact_meta.get("manifestSucceeded"),
        "manifestFailed": exact_meta.get("manifestFailed"),
        "manifestPartial": exact_meta.get("manifestPartial"),
        "manifestPromotable": exact_meta.get("manifestPromotable"),
        "manifestPromoted": exact_meta.get("manifestPromoted"),
        "sourceRunPartial": exact_meta.get("sourceRunPartial"),
        "sourceRunFailed": exact_meta.get("sourceRunFailed"),
        "sourceRunUnresolvedReasons": exact_meta.get("sourceRunUnresolvedReasons"),
        "sourceFailures": json_value(source_failures),
        "unresolvedQualifiedPrintings": json_value(unresolved_qualified),
        "strictKnownSummary": strict_known_summary,
        "evidenceSummary": evidence_summary,
        "worklistCounts": json_value(counts),
        "worklistSourceProvenance": exact_meta.get("worklistSourceProvenance"),
        "freshSnapshot": json_value(fresh_meta),
        "exactPageRefresh": json_value(exact_meta),
        "merge": merge_meta,
        "psaObservationDateMin": dates[0] if dates else None,
        "psaObservationDateMax": dates[-1] if dates else None,
    }
    return merged_rows, metadata


def chunks(values: Sequence[Any], size: int = 300) -> Iterable[Sequence[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def fetch_in(cursor: Any, sql: str, values: Sequence[Any], prefix: Sequence[Any] = ()) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for group in chunks(list(values)):
        placeholders = ",".join(["%s"] * len(group))
        cursor.execute(sql.format(placeholders=placeholders), [*prefix, *group])
        result.extend(dict(row) for row in cursor.fetchall())
    return result


def alias_target_from_member(gemrate_id: str, member: Mapping[str, Any] | None) -> str | None:
    if not member:
        return None
    detail = parse_json(member.get("detail_json"))
    raw = detail.get("aliasOf")
    if isinstance(raw, dict):
        raw = raw.get("gemrateId") or raw.get("settledId") or raw.get("id")
    fingerprint = detail.get("fingerprint") if isinstance(detail.get("fingerprint"), dict) else {}
    settled = str(fingerprint.get("settledId") or "").strip().lower()
    candidate = str(raw or "").strip().lower()
    if not candidate and settled and settled != gemrate_id:
        candidate = settled
    return candidate if HEX40.fullmatch(candidate) and candidate != gemrate_id else None


def get_nested(data: Mapping[str, Any], *path: str) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def evidence_url(evidence: Mapping[str, Any]) -> str | None:
    preferred = ("canonicalUrl", "productUrl", "sourceUrl", "proposedUrl", "url", "finalUrl")
    for key in preferred:
        value = evidence.get(key)
        if isinstance(value, str) and value.startswith(("https://", "http://")):
            return value
    for value in evidence.values():
        if isinstance(value, Mapping):
            found = evidence_url(value)
            if found:
                return found
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping):
                    found = evidence_url(item)
                    if found:
                        return found
    return None


def evidence_reason(evidence: Mapping[str, Any], status: str) -> str:
    preferred = ("primaryReason", "reasonCode", "reason", "discoveryReason", "operatorRuling", "ruling")
    stack: list[Mapping[str, Any]] = [evidence]
    while stack:
        current = stack.pop(0)
        for key in preferred:
            if key in current:
                value = compact_text(current.get(key))
                if value:
                    return value
        for value in current.values():
            if isinstance(value, Mapping):
                stack.append(value)
    lowered = status.lower()
    if "rejected" in lowered or "conflict" in lowered:
        return f"existing_{lowered}_ruling"
    return "unsearched"


def source_family(source_code: str) -> str:
    return "snkrdunk" if source_code in SNK_SOURCES else source_code


def infer_tcg(gemrate_row: Mapping[str, Any], catalog: Mapping[str, Any] | None) -> str:
    if catalog and catalog.get("tcg_code"):
        return str(catalog["tcg_code"])
    text = " ".join(
        str(gemrate_row.get(key) or "")
        for key in ("set_name", "_set_name", "card_details", "psa_details", "checklist_details")
    ).lower()
    if "one piece" in text:
        return "one_piece"
    if "pokemon" in text or "pokémon" in text:
        return "pokemon"
    return "unknown"


def pc_bucket(identities: list[dict[str, Any]]) -> str:
    if any(str(row.get("match_status") or "").lower() == "exact" and str(row.get("external_entity_id") or "").isdigit() for row in identities):
        return "exact_numeric"
    if any(str(row.get("match_status") or "").lower() == "exact" for row in identities):
        return "exact_non_numeric"
    statuses = [str(row.get("match_status") or "").lower() for row in identities]
    if any("manual" in status for status in statuses):
        return "manual_review"
    if any("conflict" in status for status in statuses):
        return "conflict"
    if any(status.startswith("rejected") or status == "rejected" for status in statuses):
        return "rejected"
    if identities:
        return "other_non_exact"
    return "no_row"


def snk_bucket(identities: list[dict[str, Any]], strict_keys: set[tuple[str, str]]) -> str:
    canonical_exact = [
        row for row in identities
        if row.get("source_code") == "snkrdunk" and str(row.get("match_status") or "").lower() == "exact"
    ]
    if any(("snkrdunk", str(row.get("external_entity_id") or "")) in strict_keys for row in canonical_exact):
        return "strict_exact"
    if canonical_exact:
        return "exact_but_not_strict"
    if any(
        row.get("source_code") in ("snk", "snk_psa10")
        and str(row.get("match_status") or "").lower() == "exact"
        for row in identities
    ):
        return "legacy_alias_only"
    statuses = [str(row.get("match_status") or "").lower() for row in identities]
    if any("manual" in status for status in statuses) or any(
        status and "conflict" not in status and not status.startswith("rejected") for status in statuses
    ):
        return "manual_review"
    if any("conflict" in status for status in statuses):
        return "conflict"
    if any(status.startswith("rejected") or status == "rejected" for status in statuses):
        return "rejected"
    return "no_row"


def load_database_snapshot(
    repo: Path,
    census_ids: list[str],
    source_failure_ids: Sequence[str] = (),
) -> dict[str, Any]:
    sys.path.insert(0, str(repo / "pipelines"))
    from qualified_pool_operator import db  # type: ignore

    connection = db()
    try:
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")

            cursor.execute("SELECT DATABASE() AS database_name,CURRENT_TIMESTAMP(6) AS selected_at,@@version AS server_version")
            database_identity = dict(cursor.fetchone())

            cursor.execute(
                "SELECT id,lock_sha256,effective_at,member_count,created_at "
                "FROM market_universe_lock WHERE is_current=1 ORDER BY id DESC"
            )
            current_locks = [dict(row) for row in cursor.fetchall()]
            if len(current_locks) != 1:
                raise RuntimeError(f"expected_one_current_universe_lock:found={len(current_locks)}")
            current_lock = current_locks[0]
            cursor.execute(
                "SELECT COUNT(*) AS actual_member_count FROM market_universe_member WHERE universe_lock_id=%s",
                (current_lock["id"],),
            )
            current_lock["actual_member_count"] = int(cursor.fetchone()["actual_member_count"])

            cursor.execute(
                "SELECT generation_id,MAX(computed_at) AS computed_at,COUNT(*) AS member_count "
                "FROM catalog_rebuild_member GROUP BY generation_id ORDER BY computed_at DESC LIMIT 1"
            )
            generation = dict(cursor.fetchone() or {})
            if not generation:
                raise RuntimeError("catalog_rebuild_member_empty")

            source_failure_rows = fetch_in(
                cursor,
                "SELECT generation_id,gemrate_id,latest_psa10_population,cohort,identity_pending,"
                "detail_json,computed_at FROM catalog_rebuild_member WHERE generation_id=%s "
                "AND gemrate_id IN ({placeholders})",
                sorted(set(source_failure_ids)),
                (generation["generation_id"],),
            )
            source_failure_members = {
                str(row["gemrate_id"]).strip().lower(): row for row in source_failure_rows
            }

            member_rows = fetch_in(
                cursor,
                "SELECT generation_id,gemrate_id,variant_id,latest_psa10_population,cohort,identity_pending,detail_json,computed_at "
                "FROM catalog_rebuild_member WHERE generation_id=%s AND gemrate_id IN ({placeholders})",
                census_ids,
                (generation["generation_id"],),
            )
            members = {str(row["gemrate_id"]): row for row in member_rows}
            alias_targets = sorted(
                {
                    target
                    for gemrate_id in census_ids
                    if (target := alias_target_from_member(gemrate_id, members.get(gemrate_id)))
                }
            )
            missing_alias_members = [target for target in alias_targets if target not in members]
            if missing_alias_members:
                alias_member_rows = fetch_in(
                    cursor,
                    "SELECT generation_id,gemrate_id,variant_id,latest_psa10_population,cohort,identity_pending,detail_json,computed_at "
                    "FROM catalog_rebuild_member WHERE generation_id=%s AND gemrate_id IN ({placeholders})",
                    missing_alias_members,
                    (generation["generation_id"],),
                )
                members.update({str(row["gemrate_id"]): row for row in alias_member_rows})

            identity_ids = sorted(set(census_ids) | set(alias_targets))
            gemrate_identity_rows = fetch_in(
                cursor,
                "SELECT source_code,external_entity_id,variant_id,match_status,evidence_sha256,source_product_number,"
                "bind_evidence_json,created_at,updated_at FROM catalog_source_identity "
                "WHERE source_code='gemrate' AND external_entity_id IN ({placeholders})",
                identity_ids,
            )
            gemrate_identities = {str(row["external_entity_id"]): row for row in gemrate_identity_rows}

            resolution: dict[str, dict[str, Any]] = {}
            variant_ids: set[int] = set()
            for gemrate_id in census_ids:
                member = members.get(gemrate_id)
                alias_target = alias_target_from_member(gemrate_id, member)
                source_row = gemrate_identities.get(gemrate_id)
                target_row = gemrate_identities.get(alias_target or "")
                target_member = members.get(alias_target or "")
                variant_id: int | None = None
                resolved_by = "none"
                if alias_target:
                    if target_row and target_row.get("variant_id") is not None:
                        variant_id = int(target_row["variant_id"])
                        resolved_by = "alias_target_source_identity"
                    elif target_member and target_member.get("variant_id") is not None:
                        variant_id = int(target_member["variant_id"])
                        resolved_by = "alias_target_rebuild_member"
                elif source_row and source_row.get("variant_id") is not None:
                    variant_id = int(source_row["variant_id"])
                    resolved_by = "gemrate_source_identity"
                elif member and member.get("variant_id") is not None:
                    variant_id = int(member["variant_id"])
                    resolved_by = "rebuild_member"
                if variant_id is not None:
                    variant_ids.add(variant_id)
                resolution[gemrate_id] = {
                    "variant_id": variant_id,
                    "alias_target": alias_target,
                    "resolved_by": resolved_by,
                    "member": member,
                    "source_identity": source_row,
                    "target_source_identity": target_row,
                }

            ordered_variant_ids = sorted(variant_ids)
            catalog_rows = fetch_in(
                cursor,
                "SELECT v.id,v.opaque_id,v.tcg_code,v.card_language,v.canonical_name,v.set_name AS variant_set_name,"
                "v.set_code AS variant_set_code,v.printing_code AS variant_printing_code,v.rarity_code,v.collector_number,"
                "v.identity_status AS variant_identity_status,v.created_at AS variant_created_at,v.updated_at AS variant_updated_at,"
                "p.tcg_code AS printing_tcg_code,p.card_language AS printing_card_language,p.set_name AS printing_set_name,"
                "p.set_code AS printing_set_code,p.collector_number AS printing_collector_number,p.printing_code,"
                "p.edition_code,p.parallel_code,p.finish_code,p.rarity_code AS printing_rarity_code,"
                "p.canonical_printing_sha256,p.identity_status AS printing_identity_status,p.evidence_sha256 AS printing_evidence_sha256,"
                "p.observed_at AS printing_observed_at,p.updated_at AS printing_updated_at "
                "FROM catalog_variant v LEFT JOIN catalog_printing_identity p ON p.variant_id=v.id WHERE v.id IN ({placeholders})",
                ordered_variant_ids,
            ) if ordered_variant_ids else []
            catalogs = {int(row["id"]): row for row in catalog_rows}
            missing_variants = sorted(variant_ids - set(catalogs))
            if missing_variants:
                raise RuntimeError("resolved_catalog_variants_missing:" + ",".join(map(str, missing_variants[:20])))

            universe_rows = fetch_in(
                cursor,
                "SELECT universe_lock_id,variant_id,segment_code,member_role,market_rank,watch_position,watch_score,created_at "
                "FROM market_universe_member WHERE universe_lock_id=%s AND variant_id IN ({placeholders})",
                ordered_variant_ids,
                (current_lock["id"],),
            ) if ordered_variant_ids else []
            universe = {int(row["variant_id"]): row for row in universe_rows}

            source_rows = fetch_in(
                cursor,
                "SELECT source_code,external_entity_id,variant_id,match_status,evidence_sha256,source_product_number,"
                "bound_tcg_code,bound_card_language,bound_set_code,bound_collector_number,bound_printing_code,"
                "bound_edition_code,bound_parallel_code,bound_finish_code,bind_evidence_json,created_at,updated_at "
                "FROM catalog_source_identity WHERE source_code IN ('pricecharting','snkrdunk','snk','snk_psa10') "
                "AND variant_id IN ({placeholders}) ORDER BY variant_id,source_code,external_entity_id",
                ordered_variant_ids,
            ) if ordered_variant_ids else []
            sources_by_variant: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for row in source_rows:
                sources_by_variant[int(row["variant_id"])].append(row)

            strict_rows = fetch_in(
                cursor,
                "SELECT source_code,external_entity_id,variant_id FROM operator_strict_source_identity "
                "WHERE source_code IN ('pricecharting','snkrdunk','snk','snk_psa10') AND variant_id IN ({placeholders})",
                ordered_variant_ids,
            ) if ordered_variant_ids else []
            strict_by_variant: dict[int, set[tuple[str, str]]] = defaultdict(set)
            for row in strict_rows:
                strict_by_variant[int(row["variant_id"])].add(
                    (str(row["source_code"]), str(row["external_entity_id"]))
                )

            capture_rows = fetch_in(
                cursor,
                "SELECT si.variant_id,r.source_code,r.external_entity_id,r.capture_sha256,r.capture_path,"
                "r.captured_at,r.generation_id,r.parser_version FROM catalog_provider_capture_receipt r "
                "INNER JOIN catalog_source_identity si ON si.source_code=r.source_code "
                "AND si.external_entity_id=r.external_entity_id "
                "WHERE si.source_code IN ('pricecharting','snkrdunk','snk','snk_psa10') "
                "AND si.variant_id IN ({placeholders}) ORDER BY r.captured_at DESC,r.capture_sha256 DESC",
                ordered_variant_ids,
            ) if ordered_variant_ids else []
            captures_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            for row in capture_rows:
                captures_by_key[(str(row["source_code"]), str(row["external_entity_id"]))].append(row)

            price_rows = fetch_in(
                cursor,
                "SELECT p.variant_id,CASE WHEN p.source_code IN ('snkrdunk','snk','snk_psa10') THEN 'snkrdunk' "
                "ELSE p.source_code END AS source_family,COUNT(*) AS ready_rows,MAX(p.observed_date) AS latest_ready_date,"
                "MAX(p.effective_at) AS latest_ready_at,"
                "SUBSTRING_INDEX(GROUP_CONCAT(CAST(p.price_usd AS CHAR) ORDER BY p.effective_at DESC,p.id DESC SEPARATOR '|||'),'|||',1) AS latest_price_usd,"
                "SUBSTRING_INDEX(GROUP_CONCAT(p.source_code ORDER BY p.effective_at DESC,p.id DESC SEPARATOR '|||'),'|||',1) AS latest_storage_source "
                "FROM market_price_observation p WHERE p.metric_status='ready' AND p.price_usd>0 "
                "AND p.source_code IN ('pricecharting','snkrdunk','snk','snk_psa10') AND p.variant_id IN ({placeholders}) "
                "GROUP BY p.variant_id,source_family",
                ordered_variant_ids,
            ) if ordered_variant_ids else []
            prices = {(int(row["variant_id"]), str(row["source_family"])): row for row in price_rows}

            sale_rows = fetch_in(
                cursor,
                "SELECT s.variant_id,CASE WHEN s.source_code IN ('snkrdunk','snk','snk_psa10') THEN 'snkrdunk' "
                "ELSE s.source_code END AS source_family,COUNT(*) AS eligible_sale_rows,SUM(s.quantity) AS eligible_quantity,"
                "MAX(s.observed_date) AS latest_sale_date,MAX(s.evidence_at) AS latest_sale_evidence_at "
                "FROM operator_eligible_accepted_psa10_sales_rows s "
                "WHERE s.source_code IN ('pricecharting','snkrdunk','snk','snk_psa10') AND s.variant_id IN ({placeholders}) "
                "GROUP BY s.variant_id,source_family",
                ordered_variant_ids,
            ) if ordered_variant_ids else []
            sales = {(int(row["variant_id"]), str(row["source_family"])): row for row in sale_rows}

            page_rows = fetch_in(
                cursor,
                "SELECT p.variant_id,p.exact_item_id,p.product_url,p.final_url,p.http_status,p.product_page_payload_sha256,"
                "p.product_page_observed_at,p.default_image_url,p.master_payload_sha256,p.identity_evidence_sha256,"
                "p.authority_sha256 FROM market_snk_en_product_page_authority p "
                "LEFT JOIN market_snk_en_product_page_authority newer ON newer.variant_id=p.variant_id "
                "AND (newer.product_page_observed_at>p.product_page_observed_at OR "
                "(newer.product_page_observed_at=p.product_page_observed_at AND newer.id>p.id)) "
                "WHERE newer.id IS NULL AND p.variant_id IN ({placeholders})",
                ordered_variant_ids,
            ) if ordered_variant_ids else []
            pages = {int(row["variant_id"]): row for row in page_rows}

            image_rows = fetch_in(
                cursor,
                "SELECT variant_id,canonical_image_acceptance_id,canonical_image_lineage_sha256,canonical_image_snk_item_id,"
                "canonical_image_product_url,canonical_image_master_payload_sha256,canonical_image_source_path,"
                "canonical_image_content_sha256,canonical_image_captured_at,canonical_image_source_observed_at,"
                "canonical_image_evidence_sha256,canonical_image_accepted_at FROM operator_canonical_image_projection "
                "WHERE variant_id IN ({placeholders})",
                ordered_variant_ids,
            ) if ordered_variant_ids else []
            images = {int(row["variant_id"]): row for row in image_rows}

            locale_rows = fetch_in(
                cursor,
                "SELECT variant_id,localized_name,localized_set_name,market_story,provenance_source_code,content_sha256,"
                "observed_at,provenance_json,updated_at FROM catalog_variant_locale "
                "WHERE locale_code='en' AND variant_id IN ({placeholders})",
                ordered_variant_ids,
            ) if ordered_variant_ids else []
            locales = {int(row["variant_id"]): row for row in locale_rows}

            official_rows = fetch_in(
                cursor,
                "SELECT variant_id,official_full_name,official_name_source_code,official_name_external_entity_id,"
                "official_name_evidence_sha256,official_name_payload_sha256,official_name_observed_at,"
                "official_name_lineage_sha256 FROM operator_official_name_projection WHERE variant_id IN ({placeholders})",
                ordered_variant_ids,
            ) if ordered_variant_ids else []
            officials = {int(row["variant_id"]): row for row in official_rows}

        connection.rollback()
    finally:
        connection.close()

    return {
        "database_identity": database_identity,
        "current_lock": current_lock,
        "generation": generation,
        "source_failure_members": source_failure_members,
        "members": members,
        "resolution": resolution,
        "catalogs": catalogs,
        "universe": universe,
        "sources_by_variant": sources_by_variant,
        "strict_by_variant": strict_by_variant,
        "captures_by_key": captures_by_key,
        "prices": prices,
        "sales": sales,
        "pages": pages,
        "images": images,
        "locales": locales,
        "officials": officials,
    }


def compact_capture(row: Mapping[str, Any] | None, claimed_sha: str | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "sha256": row.get("capture_sha256"),
        "path": row.get("capture_path"),
        "capturedAt": json_value(row.get("captured_at")),
        "generationId": row.get("generation_id"),
        "parserVersion": row.get("parser_version"),
        "matchesBindingEvidence": bool(claimed_sha and row.get("capture_sha256") == claimed_sha),
    }


def identity_payload(
    row: Mapping[str, Any],
    strict_keys: set[tuple[str, str]],
    captures_by_key: Mapping[tuple[str, str], list[dict[str, Any]]],
) -> dict[str, Any]:
    source_code = str(row.get("source_code") or "")
    external_id = str(row.get("external_entity_id") or "")
    evidence = parse_json(row.get("bind_evidence_json"))
    claimed_sha = compact_text(get_nested(evidence, "evidence", "sha256"), 64)
    available_captures = captures_by_key.get((source_code, external_id), [])
    selected_capture = next(
        (capture for capture in available_captures if claimed_sha and capture.get("capture_sha256") == claimed_sha),
        available_captures[0] if available_captures else None,
    )
    provider_claims = evidence.get("providerClaims") if isinstance(evidence.get("providerClaims"), dict) else None
    return {
        "sourceCode": source_code,
        "externalId": external_id,
        "sourceProductNumber": row.get("source_product_number"),
        "matchStatus": row.get("match_status"),
        "strict": (source_code, external_id) in strict_keys,
        "evidence": {
            "sha256": row.get("evidence_sha256"),
            "type": get_nested(evidence, "evidence", "type"),
            "captureSha256": claimed_sha,
            "capturePath": get_nested(evidence, "evidence", "path"),
            "providerClaims": json_value(provider_claims),
        },
        "boundPrinting": {
            "tcgCode": row.get("bound_tcg_code"),
            "cardLanguage": row.get("bound_card_language"),
            "setCode": row.get("bound_set_code"),
            "collectorNumber": row.get("bound_collector_number"),
            "printingCode": row.get("bound_printing_code"),
            "editionCode": row.get("bound_edition_code"),
            "parallelCode": row.get("bound_parallel_code"),
            "finishCode": row.get("bound_finish_code"),
        },
        "url": evidence_url(evidence),
        "discoveryReason": evidence_reason(evidence, str(row.get("match_status") or "")),
        "capture": compact_capture(selected_capture, claimed_sha),
        "updatedAt": json_value(row.get("updated_at")),
    }


def content_price(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "readyRowCount": int(row.get("ready_rows") or 0),
        "latestDate": json_value(row.get("latest_ready_date")),
        "latestObservedAt": json_value(row.get("latest_ready_at")),
        "latestUsd": row.get("latest_price_usd"),
        "storageSourceCode": row.get("latest_storage_source"),
    }


def content_sale(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "eligibleRowCount": int(row.get("eligible_sale_rows") or 0),
        "eligibleQuantity": int(row.get("eligible_quantity") or 0),
        "latestDate": json_value(row.get("latest_sale_date")),
        "latestObservedAt": json_value(row.get("latest_sale_evidence_at")),
    }


def page_payload(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    item_id = str(row.get("exact_item_id") or "")
    product_url = str(row.get("product_url") or "")
    expected_url = f"https://snkrdunk.com/en/trading-cards/{item_id}"
    hashes_valid = all(
        HEX64.fullmatch(str(row.get(key) or ""))
        for key in (
            "product_page_payload_sha256",
            "master_payload_sha256",
            "identity_evidence_sha256",
            "authority_sha256",
        )
    )
    return {
        "exactItemId": item_id,
        "productUrl": product_url,
        "finalUrl": row.get("final_url"),
        "httpStatus": int(row.get("http_status") or 0),
        "observedAt": json_value(row.get("product_page_observed_at")),
        "payloadSha256": row.get("product_page_payload_sha256"),
        "authoritySha256": row.get("authority_sha256"),
        "ready": bool(
            item_id.isdigit()
            and product_url == expected_url
            and row.get("final_url") == row.get("product_url")
            and int(row.get("http_status") or 0) == 200
            and hashes_valid
            and row.get("product_page_observed_at") is not None
        ),
    }


def image_payload(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {"projectionPresent": False, "currentSnkImagePresent": False}
    snk_item = str(row.get("canonical_image_snk_item_id") or "")
    return {
        "projectionPresent": True,
        "currentSnkImagePresent": bool(snk_item),
        "snkItemId": snk_item or None,
        "productUrl": row.get("canonical_image_product_url"),
        "sourcePath": row.get("canonical_image_source_path"),
        "contentSha256": row.get("canonical_image_content_sha256"),
        "capturedAt": json_value(row.get("canonical_image_captured_at")),
        "sourceObservedAt": json_value(row.get("canonical_image_source_observed_at")),
        "acceptedAt": json_value(row.get("canonical_image_accepted_at")),
        "evidenceSha256": row.get("canonical_image_evidence_sha256"),
    }


def english_metadata_payload(locale: Mapping[str, Any] | None, official: Mapping[str, Any] | None) -> dict[str, Any]:
    locale = locale or {}
    provenance = parse_json(locale.get("provenance_json"))
    evidence_complete = bool(
        locale
        and str(locale.get("provenance_source_code") or "").strip()
        and HEX64.fullmatch(str(locale.get("content_sha256") or ""))
        and locale.get("observed_at") is not None
        and provenance
    )
    return {
        "rowPresent": bool(locale),
        "localizedName": locale.get("localized_name"),
        "localizedSetName": locale.get("localized_set_name"),
        "marketStoryPresent": bool(str(locale.get("market_story") or "").strip()),
        "provenanceSourceCode": locale.get("provenance_source_code"),
        "contentSha256": locale.get("content_sha256"),
        "observedAt": json_value(locale.get("observed_at")),
        "provenancePresent": bool(provenance),
        "evidenceComplete": evidence_complete,
        "officialName": official.get("official_full_name") if official else None,
        "officialNameSourceCode": official.get("official_name_source_code") if official else None,
        "officialNameObservedAt": json_value(official.get("official_name_observed_at")) if official else None,
        "officialNameEvidencePresent": bool(official),
    }


def work_payload(
    catalog_state: str,
    pc: Mapping[str, Any],
    snk: Mapping[str, Any],
) -> dict[str, Any]:
    sources: set[str] = set()
    p0: list[str] = []
    p1: list[str] = []
    p2: list[str] = []
    p3: list[str] = []

    if catalog_state == "gemrate_only_new":
        p0.append("gemrate_only_new_no_catalog_variant")
        sources.update(("pricecharting", "snkrdunk"))
    else:
        if pc["bucket"] == "no_row":
            p1.append("pricecharting_no_row")
            sources.add("pricecharting")
        if snk["bucket"] == "no_row":
            p1.append("snkrdunk_no_row")
            sources.add("snkrdunk")

        if pc["bucket"] in ("exact_non_numeric", "manual_review", "conflict", "rejected", "other_non_exact"):
            p2.append(f"pricecharting_{pc['bucket']}")
            sources.add("pricecharting")
        elif pc["bucket"] == "exact_numeric" and not pc["strictEvidence"]:
            p2.append("pricecharting_exact_evidence_incomplete")
            sources.add("pricecharting")

        if snk["bucket"] in (
            "exact_but_not_strict",
            "legacy_alias_only",
            "manual_review",
            "conflict",
            "rejected",
        ):
            p2.append(f"snkrdunk_{snk['bucket']}")
            sources.add("snkrdunk")

        if pc["bucket"] in ("exact_numeric", "exact_non_numeric"):
            if pc["latestReadyPrice"] is None:
                p3.append("pricecharting_no_ready_price")
                sources.add("pricecharting")
            if pc["eligibleSale"] is None:
                p3.append("pricecharting_no_eligible_sale")
                sources.add("pricecharting")

        if snk["bucket"] in ("strict_exact", "exact_but_not_strict", "legacy_alias_only"):
            if snk["latestReadyKline"] is None:
                p3.append("snkrdunk_no_ready_kline")
                sources.add("snkrdunk")
            if snk["eligibleSale"] is None:
                p3.append("snkrdunk_no_eligible_sale")
                sources.add("snkrdunk")
            if snk["productPageAuthority"] is None:
                p3.append("snkrdunk_no_product_page_authority")
                sources.add("snkrdunk")
            if not snk["providerCapturePresent"]:
                p3.append("snkrdunk_no_provider_capture")
                sources.add("snkrdunk")
            if not snk["canonicalImage"]["currentSnkImagePresent"]:
                p3.append("snkrdunk_no_current_snk_image")
                sources.add("snkrdunk")
        if not snk["englishMetadata"]["evidenceComplete"]:
            p3.append("english_metadata_provenance_incomplete")
            sources.add("snkrdunk")

    groups = (("P0", p0), ("P1", p1), ("P2", p2), ("P3", p3))
    priority, reasons = next(((priority, reasons) for priority, reasons in groups if reasons), (None, []))
    all_reasons = [reason for _, group in groups for reason in group]
    action = {
        "P0": "research_and_return_canonical_identity_evidence_only",
        "P1": "research_missing_provider_identity_and_return_proposal",
        "P2": "return_non_overriding_evidence_for_guarded_review",
        "P3": "research_content_gap_and_return_observation_evidence",
        None: "none",
    }[priority]
    return {
        "required": bool(priority),
        "priority": priority,
        "sourceCodes": sorted(sources),
        "primaryReason": reasons[0] if reasons else "complete",
        "reason": reasons[0] if reasons else "complete",
        "allReasons": all_reasons,
        "allowedNextAction": action,
    }


def build_payload(census_rows: list[dict[str, Any]], census_meta: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    captures_by_key = snapshot["captures_by_key"]

    for gemrate_row in census_rows:
        psa_id = str(gemrate_row["psa_id"]).lower()
        resolved = snapshot["resolution"][psa_id]
        variant_id = resolved["variant_id"]
        catalog = snapshot["catalogs"].get(variant_id) if variant_id is not None else None
        universe = snapshot["universe"].get(variant_id) if variant_id is not None else None
        alias_target = resolved["alias_target"]
        if alias_target:
            catalog_state = "alias"
        elif variant_id is None:
            catalog_state = "gemrate_only_new"
        elif universe:
            catalog_state = "active"
        else:
            catalog_state = "pending/inactive"

        raw_source_rows = snapshot["sources_by_variant"].get(variant_id, []) if variant_id is not None else []
        strict_keys = snapshot["strict_by_variant"].get(variant_id, set()) if variant_id is not None else set()
        pc_raw = [row for row in raw_source_rows if row.get("source_code") == "pricecharting"]
        snk_raw = [row for row in raw_source_rows if row.get("source_code") in SNK_SOURCES]
        pc_identities = [identity_payload(row, strict_keys, captures_by_key) for row in pc_raw]
        snk_identities = [identity_payload(row, strict_keys, captures_by_key) for row in snk_raw]
        pc_primary = pc_bucket(pc_raw)
        snk_primary = snk_bucket(snk_raw, strict_keys)

        pc_price = content_price(snapshot["prices"].get((variant_id, "pricecharting"))) if variant_id is not None else None
        pc_sale = content_sale(snapshot["sales"].get((variant_id, "pricecharting"))) if variant_id is not None else None
        snk_price = content_price(snapshot["prices"].get((variant_id, "snkrdunk"))) if variant_id is not None else None
        snk_sale = content_sale(snapshot["sales"].get((variant_id, "snkrdunk"))) if variant_id is not None else None
        page = page_payload(snapshot["pages"].get(variant_id)) if variant_id is not None else None
        image = image_payload(snapshot["images"].get(variant_id)) if variant_id is not None else image_payload(None)
        english = english_metadata_payload(
            snapshot["locales"].get(variant_id) if variant_id is not None else None,
            snapshot["officials"].get(variant_id) if variant_id is not None else None,
        )

        pc_payload = {
            "bucket": pc_primary,
            "externalIds": [identity["externalId"] for identity in pc_identities],
            "urls": sorted({identity["url"] for identity in pc_identities if identity.get("url")}),
            "strictEvidence": any(identity["strict"] for identity in pc_identities),
            "strictExternalIds": [identity["externalId"] for identity in pc_identities if identity["strict"]],
            "deterministicBlackHole": pc_primary == "exact_non_numeric",
            "identities": pc_identities,
            "latestReadyPrice": pc_price,
            "eligibleSale": pc_sale,
            "discoveryState": sorted({identity["discoveryReason"] for identity in pc_identities}) or ["unsearched"],
            "gapFlags": [
                flag
                for flag, present in (
                    (f"identity:{pc_primary}", pc_primary != "exact_numeric"),
                    ("no_ready_price", pc_price is None),
                    ("no_eligible_sale", pc_sale is None),
                )
                if present
            ],
        }
        snk_payload = {
            "bucket": snk_primary,
            "externalIds": [identity["externalId"] for identity in snk_identities],
            "rawSourceCodes": sorted({identity["sourceCode"] for identity in snk_identities}),
            "strictEvidence": snk_primary == "strict_exact",
            "strictExternalIds": [
                identity["externalId"]
                for identity in snk_identities
                if identity["sourceCode"] == "snkrdunk" and identity["strict"]
            ],
            "identities": snk_identities,
            "providerCapturePresent": any(identity.get("capture") for identity in snk_identities),
            "productPageAuthority": page,
            "latestReadyKline": snk_price,
            "eligibleSale": snk_sale,
            "canonicalImage": image,
            "englishMetadata": english,
            "gapFlags": [
                flag
                for flag, present in (
                    (f"identity:{snk_primary}", snk_primary != "strict_exact"),
                    ("no_product_page_authority", page is None),
                    ("no_provider_capture", not any(identity.get("capture") for identity in snk_identities)),
                    ("no_ready_kline", snk_price is None),
                    ("no_eligible_sale", snk_sale is None),
                    ("no_current_snk_image", not image["currentSnkImagePresent"]),
                    ("english_metadata_provenance_incomplete", not english["evidenceComplete"]),
                )
                if present
            ],
        }

        member = resolved.get("member") or {}
        gemrate_source = resolved.get("source_identity") or resolved.get("target_source_identity") or {}
        catalog_payload = {
            "state": catalog_state,
            "variantId": variant_id,
            "opaqueId": catalog.get("opaque_id") if catalog else None,
            "market": {
                "universeLockId": universe.get("universe_lock_id") if universe else None,
                "segmentCode": universe.get("segment_code") if universe else None,
                "memberRole": universe.get("member_role") if universe else None,
                "marketRank": universe.get("market_rank") if universe else None,
            },
            "canonicalPrintingTuple": {
                "tcgCode": catalog.get("printing_tcg_code") or catalog.get("tcg_code") if catalog else None,
                "cardLanguage": catalog.get("printing_card_language") or catalog.get("card_language") if catalog else None,
                "canonicalName": catalog.get("canonical_name") if catalog else None,
                "setName": catalog.get("printing_set_name") or catalog.get("variant_set_name") if catalog else None,
                "setCode": catalog.get("printing_set_code") or catalog.get("variant_set_code") if catalog else None,
                "collectorNumber": catalog.get("printing_collector_number") or catalog.get("collector_number") if catalog else None,
                "printingCode": catalog.get("printing_code") or catalog.get("variant_printing_code") if catalog else None,
                "editionCode": catalog.get("edition_code") if catalog else None,
                "parallelCode": catalog.get("parallel_code") if catalog else None,
                "finishCode": catalog.get("finish_code") if catalog else None,
                "rarityCode": catalog.get("printing_rarity_code") or catalog.get("rarity_code") if catalog else None,
                "canonicalPrintingSha256": catalog.get("canonical_printing_sha256") if catalog else None,
                "identityStatus": catalog.get("printing_identity_status") if catalog else None,
            },
            "variantIdentityStatus": catalog.get("variant_identity_status") if catalog else None,
            "gemrateResolution": {
                "resolvedBy": resolved.get("resolved_by"),
                "sourceExternalId": gemrate_source.get("external_entity_id"),
                "sourceMatchStatus": gemrate_source.get("match_status"),
                "sourceEvidenceSha256": gemrate_source.get("evidence_sha256"),
                "rebuildGenerationId": member.get("generation_id"),
                "rebuildCohort": member.get("cohort"),
                "rebuildPopulation": member.get("latest_psa10_population"),
                "identityPending": bool(member.get("identity_pending")) if member else None,
            },
        }

        work = work_payload(catalog_state, pc_payload, snk_payload)
        full_name = gemrate_row.get("full_name")
        raw_full_name = gemrate_row.get("raw_full_name")
        name_source = gemrate_row.get("name_source")
        name_provenance = gemrate_row.get("name_provenance")
        if not isinstance(full_name, str) or not full_name.strip():
            raise IncompleteCensus(f"missing_output_full_name:{psa_id}")
        if not isinstance(raw_full_name, str) or raw_full_name != full_name:
            raise IncompleteCensus(f"raw_full_name_not_verbatim:{psa_id}")
        if gemrate_row.get("name") != full_name:
            raise IncompleteCensus(f"name_not_full_authority_name:{psa_id}")
        if not name_source or not isinstance(name_provenance, Mapping):
            raise IncompleteCensus(f"missing_full_name_provenance:{psa_id}")
        row_payload = {
            "gemrate": {
                "psaId": psa_id,
                "psa10Population": int(gemrate_row.get("psa_10") or 0),
                "name": full_name,
                "fullName": full_name,
                "rawFullName": raw_full_name,
                "nameSource": name_source,
                "nameProvenance": name_provenance,
                "tcgCode": infer_tcg(gemrate_row, catalog),
                "category": gemrate_row.get("category"),
                "year": gemrate_row.get("year"),
                "setName": gemrate_row.get("set_name") or gemrate_row.get("_set_name"),
                "collectorNumber": gemrate_row.get("card_number"),
                "parallel": gemrate_row.get("parallel") or gemrate_row.get("card_set_parallel"),
                "psaDetails": gemrate_row.get("psa_details"),
                "psaUrl": gemrate_row.get("psa_url"),
                "psaObservedDate": gemrate_row.get("psa_date"),
                "sourceSetId": gemrate_row.get("_set_id"),
                "aliasOfPsaId": alias_target,
            },
            "catalog": catalog_payload,
            "pricecharting": pc_payload,
            "snkrdunk": snk_payload,
            "work": work,
        }
        rows.append(json_value(row_payload))

    rows.sort(
        key=lambda row: (
            PRIORITY_ORDER[row["work"]["priority"]],
            -int(row["gemrate"]["psa10Population"]),
            row["gemrate"]["psaId"],
        )
    )
    catalog_counts = Counter(row["catalog"]["state"] for row in rows)
    pc_counts = Counter(row["pricecharting"]["bucket"] for row in rows)
    snk_counts = Counter(row["snkrdunk"]["bucket"] for row in rows)
    work_counts = Counter(row["work"]["priority"] or "complete" for row in rows)
    gap_counts = Counter(
        reason
        for row in rows
        for reason in row["work"]["allReasons"]
    )
    total = len(rows)
    catalog_order = ("active", "pending/inactive", "alias", "gemrate_only_new")
    pc_order = (
        "exact_numeric",
        "exact_non_numeric",
        "manual_review",
        "conflict",
        "rejected",
        "other_non_exact",
        "no_row",
    )
    snk_order = (
        "strict_exact",
        "exact_but_not_strict",
        "legacy_alias_only",
        "manual_review",
        "conflict",
        "rejected",
        "no_row",
    )
    catalog_summary = {key: catalog_counts.get(key, 0) for key in catalog_order}
    pc_summary = {key: pc_counts.get(key, 0) for key in pc_order}
    snk_summary = {key: snk_counts.get(key, 0) for key in snk_order}
    work_summary = {key: work_counts.get(key, 0) for key in ("P0", "P1", "P2", "P3", "complete")}

    assert total == int(census_meta["uniquePsaIdCount"]), "row_count_must_equal_census"
    assert sum(catalog_summary.values()) == total, "catalog_buckets_must_sum_to_census"
    assert sum(pc_summary.values()) == total, "pc_buckets_must_sum_to_census"
    assert sum(snk_summary.values()) == total, "snk_buckets_must_sum_to_census"
    assert sum(work_summary.values()) == total, "work_buckets_must_sum_to_census"
    assert all(row["work"]["primaryReason"] and row["work"]["allowedNextAction"] for row in rows)

    db_identity = snapshot["database_identity"]
    current_lock = snapshot["current_lock"]
    generation = snapshot["generation"]
    generated_at = utc_iso()
    return {
        "contract": {
            "name": "cardz_gemrate_db_completeness_handoff_v1",
            "scope": census_meta["scope"],
            "providerGlobalCensus": bool(census_meta.get("providerGlobal")),
            "populationPolicy": "PSA 10 population >= 1000 within the declared census scope",
            "fullNamePolicy": (
                "gemrate.name, fullName, and rawFullName preserve the complete authority string "
                "verbatim: fresh psa_details or exact immutable raw population_data PSA-row description"
            ),
            "databaseMode": "single consistent read-only transaction",
            "rowKey": "gemrate.psaId",
            "sorting": "priority ascending P0-P3, then PSA10 population descending",
            "unknownDiscoveryState": "unsearched",
            "grokRole": "research and return evidence only",
            "grokReturnFormat": "one JSON object per line (JSONL)",
            "grokReturnSchema": {
                "psaId": "...",
                "variantId": 123,
                "sourceCode": "pricecharting|snkrdunk",
                "decision": "propose|not_found|ambiguous|blocked_by_existing_ruling",
                "proposedUrl": "https://...",
                "externalId": "...",
                "pageTitle": "...",
                "candidateUrls": [],
                "evidenceSummary": "...",
                "queriesTried": [],
                "observedAt": "...",
                "notes": "",
            },
            "prohibitions": [
                "no SQL or database writes",
                "no canonical identity changes",
                "no overwrite of rejected or conflict rulings",
                "no direct match_status=exact",
                "no publish, deploy, or daily-chain changes",
            ],
            "acceptance": {
                "rowCountEqualsUniqueCensusPsaIds": True,
                "catalogPrimaryBucketsSumToCensus": True,
                "pricechartingPrimaryBucketsSumToCensus": True,
                "snkrdunkPrimaryBucketsSumToCensus": True,
                "everyRowHasPrimaryReasonAndAllowedNextAction": True,
                "everyRowHasVerbatimAuthorityFullNameAndProvenance": True,
            },
        },
        "generatedAt": generated_at,
        "census": json_value(census_meta),
        "dbSnapshot": {
            "databaseName": db_identity.get("database_name"),
            "serverVersion": db_identity.get("server_version"),
            "selectedAt": json_value(db_identity.get("selected_at")),
            "transaction": "READ ONLY / WITH CONSISTENT SNAPSHOT",
            "currentUniverse": {
                "lockId": int(current_lock["id"]),
                "lockSha256": current_lock.get("lock_sha256"),
                "effectiveAt": json_value(current_lock.get("effective_at")),
                "declaredMemberCount": int(current_lock.get("member_count") or 0),
                "actualMemberCount": int(current_lock.get("actual_member_count") or 0),
            },
            "latestCatalogGeneration": {
                "generationId": generation.get("generation_id"),
                "computedAt": json_value(generation.get("computed_at")),
                "memberCount": int(generation.get("member_count") or 0),
            },
        },
        "summary": {
            "censusRows": total,
            "catalog": catalog_summary,
            "pricecharting": pc_summary,
            "snkrdunk": snk_summary,
            "work": work_summary,
            "content": {
                "pricechartingStrictEvidence": sum(1 for row in rows if row["pricecharting"]["strictEvidence"]),
                "pricechartingReadyPrice": sum(1 for row in rows if row["pricecharting"]["latestReadyPrice"] is not None),
                "pricechartingEligibleSale": sum(1 for row in rows if row["pricecharting"]["eligibleSale"] is not None),
                "snkrdunkStrictEvidence": sum(1 for row in rows if row["snkrdunk"]["strictEvidence"]),
                "snkrdunkProviderCapture": sum(1 for row in rows if row["snkrdunk"]["providerCapturePresent"]),
                "snkrdunkProductPageAuthority": sum(1 for row in rows if row["snkrdunk"]["productPageAuthority"] is not None),
                "snkrdunkReadyKline": sum(1 for row in rows if row["snkrdunk"]["latestReadyKline"] is not None),
                "snkrdunkEligibleSale": sum(1 for row in rows if row["snkrdunk"]["eligibleSale"] is not None),
                "snkrdunkCurrentCanonicalImage": sum(
                    1 for row in rows if row["snkrdunk"]["canonicalImage"]["currentSnkImagePresent"]
                ),
                "englishMetadataProvenanceComplete": sum(
                    1 for row in rows if row["snkrdunk"]["englishMetadata"]["evidenceComplete"]
                ),
            },
            "workReasons": dict(sorted(gap_counts.items(), key=lambda item: (-item[1], item[0]))),
            "grokRequiredRows": sum(1 for row in rows if row["work"]["required"]),
        },
        "rows": rows,
    }


def build_known_scope_inventory_payload(
    census_rows: list[dict[str, Any]],
    census_meta: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Inventory every known strict PSA ID while keeping census blockers explicit."""

    payload = build_payload(census_rows, census_meta, snapshot)
    blocker_payload = build_incomplete_payload(census_meta, snapshot)
    rows = payload["rows"]
    total = len(rows)
    content = payload["summary"]["content"]

    identity_pending_count = sum(
        1
        for row in rows
        if row["catalog"]["gemrateResolution"].get("identityPending") is True
    )
    variant_identity_status = Counter(
        str(row["catalog"].get("variantIdentityStatus") or "<missing>") for row in rows
    )
    printing_identity_status = Counter(
        str(row["catalog"]["canonicalPrintingTuple"].get("identityStatus") or "<missing>")
        for row in rows
    )
    no_ready_market_price = sum(
        1
        for row in rows
        if row["pricecharting"]["latestReadyPrice"] is None
        and row["snkrdunk"]["latestReadyKline"] is None
    )
    no_eligible_sale_any_source = sum(
        1
        for row in rows
        if row["pricecharting"]["eligibleSale"] is None
        and row["snkrdunk"]["eligibleSale"] is None
    )
    canonical_image_projection_present = sum(
        1 for row in rows if row["snkrdunk"]["canonicalImage"]["projectionPresent"]
    )
    snk_bound_missing_current_image = sum(
        1
        for row in rows
        if row["snkrdunk"]["bucket"]
        in ("strict_exact", "exact_but_not_strict", "legacy_alias_only")
        and not row["snkrdunk"]["canonicalImage"]["currentSnkImagePresent"]
    )
    high_level_gaps = {
        "knownStrictQualifiedPsaIds": total,
        "qualifiedPrintingsMissingStrictPsaId": len(blocker_payload["unresolvedQualifiedPrintings"]),
        "verifiedExactSourceFailures": len(blocker_payload["sourceFailures"]),
        "notInDbNoVariant": int(payload["summary"]["catalog"]["gemrate_only_new"]),
        "catalogPendingOrInactive": int(payload["summary"]["catalog"]["pending/inactive"]),
        "catalogAlias": int(payload["summary"]["catalog"]["alias"]),
        "gemrateIdentityPendingFlag": identity_pending_count,
        "pricechartingNoIdentityRow": int(payload["summary"]["pricecharting"]["no_row"]),
        "pricechartingWithoutStrictEvidence": total - int(content["pricechartingStrictEvidence"]),
        "pricechartingMissingReadyPrice": total - int(content["pricechartingReadyPrice"]),
        "pricechartingMissingEligibleSale": total - int(content["pricechartingEligibleSale"]),
        "snkrdunkNoIdentityRow": int(payload["summary"]["snkrdunk"]["no_row"]),
        "snkrdunkWithoutStrictEvidence": total - int(content["snkrdunkStrictEvidence"]),
        "snkrdunkMissingProviderCapture": total - int(content["snkrdunkProviderCapture"]),
        "snkrdunkMissingProductPageAuthority": total - int(content["snkrdunkProductPageAuthority"]),
        "snkrdunkMissingReadyKline": total - int(content["snkrdunkReadyKline"]),
        "snkrdunkMissingEligibleSale": total - int(content["snkrdunkEligibleSale"]),
        "canonicalImageProjectionPresent": canonical_image_projection_present,
        "canonicalImageProjectionMissing": total - canonical_image_projection_present,
        "snkrdunkBoundCardsMissingCurrentSnkImage": snk_bound_missing_current_image,
        "englishMetadataProvenanceIncomplete": total - int(content["englishMetadataProvenanceComplete"]),
        "missingReadyMarketPriceAcrossBothPrimarySources": no_ready_market_price,
        "missingEligibleSaleAcrossBothPrimarySources": no_eligible_sale_any_source,
    }

    queue: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        work = row["work"]
        if not work["required"]:
            continue
        queue.append(
            {
                "taskKey": f"known-gap:{row['gemrate']['psaId']}",
                "kind": "known_card_database_gap",
                "priority": work["priority"],
                "psaId": row["gemrate"]["psaId"],
                "requestId": None,
                "variantId": row["catalog"]["variantId"],
                "populationPsa10": row["gemrate"]["psa10Population"],
                "fullName": row["gemrate"]["fullName"],
                "sourceCodes": work["sourceCodes"],
                "primaryReason": work["primaryReason"],
                "allReasons": work["allReasons"],
                "allowedNextAction": work["allowedNextAction"],
                "recordRef": f"rows/{row_index}",
            }
        )
    for index, row in enumerate(blocker_payload["unresolvedQualifiedPrintings"]):
        queue.append(
            {
                "taskKey": f"unresolved-psa:{row['requestId']}",
                "kind": "qualified_printing_missing_strict_psa_id",
                "priority": "P0",
                "psaId": None,
                "requestId": row["requestId"],
                "variantId": None,
                "populationPsa10": row["populationPsa10"],
                "fullName": row["fullName"],
                "sourceCodes": ["gemrate"],
                "primaryReason": row["primaryReason"],
                "allReasons": [row["primaryReason"]],
                "allowedNextAction": row["allowedNextAction"],
                "recordRef": f"unresolvedQualifiedPrintings/{index}",
            }
        )
    for index, row in enumerate(blocker_payload["sourceFailures"]):
        queue.append(
            {
                "taskKey": f"gemrate-refresh:{row['requestId']}",
                "kind": "verified_gemrate_source_refresh_failure",
                "priority": "P3",
                "psaId": None,
                "requestId": row["requestId"],
                "variantId": None,
                "populationPsa10": row.get("latestStoredPopulation"),
                "fullName": None,
                "sourceCodes": ["gemrate"],
                "primaryReason": str(row.get("reason") or "source_refresh_failed"),
                "allReasons": [str(row.get("reason") or "source_refresh_failed")],
                "allowedNextAction": "retry_exact_gemrate_source_refresh_and_return_receipt",
                "recordRef": f"sourceFailures/{index}",
            }
        )
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    queue.sort(
        key=lambda item: (
            priority_order[item["priority"]],
            -int(item.get("populationPsa10") or 0),
            str(item["taskKey"]),
        )
    )
    queue_priority = Counter(item["priority"] for item in queue)
    queue_kind = Counter(item["kind"] for item in queue)

    census_blockers_remain = bool(
        blocker_payload["unresolvedQualifiedPrintings"] or blocker_payload["sourceFailures"]
    )
    payload["status"] = (
        "INCOMPLETE_CENSUS_WITH_KNOWN_SCOPE_INVENTORY"
        if census_blockers_remain
        else "KNOWN_SCOPE_INVENTORY_COMPLETE_PROVIDER_GLOBAL_UNPROVEN"
    )
    payload["contract"]["status"] = payload["status"]
    payload["contract"]["knownScopeInventoryGenerated"] = True
    payload["contract"]["knownScopeRowCount"] = total
    payload["contract"]["censusBlockersRemain"] = census_blockers_remain
    payload["contract"]["queuePaths"] = {
        "flatExecutionQueue": "grokQueue",
        "fullKnownCardEvidence": "rows",
        "qualifiedIdentityBlockers": "unresolvedQualifiedPrintings",
        "sourceRefreshBlockers": "sourceFailures",
    }
    payload["contract"]["grokReturnSchema"]["sourceCode"] = "gemrate|pricecharting|snkrdunk"
    payload["contract"]["grokReturnSchema"]["requestId"] = "... or null"
    payload["contract"]["grokReturnSchema"]["proposedPsaId"] = "40 lowercase hex or null"
    payload["census"]["knownScopeInventoryStatus"] = "COMPLETE"
    payload["census"]["knownScopeInventoryRowCount"] = total
    payload["unresolvedQualifiedPrintings"] = blocker_payload["unresolvedQualifiedPrintings"]
    payload["sourceFailures"] = blocker_payload["sourceFailures"]
    payload["summary"]["highLevelGaps"] = high_level_gaps
    payload["summary"]["variantIdentityStatus"] = dict(sorted(variant_identity_status.items()))
    payload["summary"]["canonicalPrintingIdentityStatus"] = dict(sorted(printing_identity_status.items()))
    payload["summary"]["knownStrictQualifiedPsaIds"] = total
    payload["summary"]["unresolvedQualifiedPrintings"] = len(payload["unresolvedQualifiedPrintings"])
    payload["summary"]["sourceFailures"] = len(payload["sourceFailures"])
    payload["summary"]["databaseCompletenessBucketsGenerated"] = True
    payload["summary"]["grokQueue"] = {
        "total": len(queue),
        "byPriority": {key: queue_priority.get(key, 0) for key in ("P0", "P1", "P2", "P3")},
        "byKind": dict(sorted(queue_kind.items())),
    }
    payload["grokQueue"] = queue
    return payload


def build_incomplete_payload(
    census_meta: dict[str, Any],
    snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    snapshot = snapshot or {}
    unresolved = census_meta.get("unresolvedQualifiedPrintings")
    if not isinstance(unresolved, list):
        unresolved = []
    raw_source_failures = census_meta.get("sourceFailures")
    if not isinstance(raw_source_failures, list):
        raw_source_failures = []
    stored_members = snapshot.get("source_failure_members")
    if not isinstance(stored_members, Mapping):
        stored_members = {}
    source_failures: list[dict[str, Any]] = []
    for raw in raw_source_failures:
        if not isinstance(raw, Mapping):
            continue
        request_id = str(raw.get("requestId") or "").strip().lower()
        member = stored_members.get(request_id)
        member = member if isinstance(member, Mapping) else {}
        stored_population = member.get("latest_psa10_population")
        if stored_population is None:
            stored_population = raw.get("latestStoredPopulation")
        source_failures.append(
            {
                "requestId": request_id,
                "reason": raw.get("reason"),
                "latestStoredPopulation": (
                    int(stored_population) if stored_population is not None else None
                ),
                "cohort": member.get("cohort") if member else raw.get("cohort"),
                "identityPending": (
                    bool(member.get("identity_pending"))
                    if member
                    else raw.get("identityPending")
                ),
                "latestCatalogGenerationId": (
                    member.get("generation_id")
                    if member
                    else raw.get("latestCatalogGenerationId")
                ),
                "latestCatalogComputedAt": json_value(
                    member.get("computed_at")
                    if member
                    else raw.get("latestCatalogComputedAt")
                ),
            }
        )

    enriched_census = dict(census_meta)
    enriched_census["sourceFailures"] = source_failures
    evidence_summary = dict(census_meta.get("evidenceSummary") or {})
    evidence_summary["sourceFailureCount"] = len(source_failures)
    evidence_summary["databaseCompletenessBucketsGenerated"] = False
    enriched_census["evidenceSummary"] = evidence_summary
    db_identity = snapshot.get("database_identity")
    db_identity = db_identity if isinstance(db_identity, Mapping) else {}
    generation = snapshot.get("generation")
    generation = generation if isinstance(generation, Mapping) else {}
    return {
        "contract": {
            "name": "cardz_gemrate_db_completeness_handoff_v1",
            "status": "INCOMPLETE_CENSUS",
            "scope": census_meta.get("scope"),
            "providerGlobalCensus": False,
            "populationPolicy": "PSA 10 population >= 1000 within the declared bounded scope",
            "psaIdentityPolicy": (
                "psaId is only population_data[grader=psa].gemrate_id; request, top-level, "
                "and providerEntity IDs are provenance only"
            ),
            "fullNamePolicy": (
                "exact fullName is population_data[grader=psa].description preserved verbatim; "
                "fresh fullName is psa_details preserved verbatim"
            ),
            "databaseMode": "single consistent read-only transaction",
            "rowsPolicy": "rows stays empty until every qualified printing has a strict PSA ID and source coverage",
            "grokRole": "research and return GemRate PSA identity evidence only",
            "grokReturnFormat": "one JSON object per line (JSONL)",
            "grokReturnSchema": {
                "requestId": "...",
                "sourceCode": "gemrate",
                "decision": "propose|not_found|ambiguous|blocked_by_existing_ruling",
                "proposedPsaId": "40 lowercase hex or null",
                "proposedUrl": "https://...",
                "pageTitle": "...",
                "candidateUrls": [],
                "evidenceSummary": "...",
                "queriesTried": [],
                "observedAt": "...",
                "notes": "",
            },
            "prohibitions": [
                "no SQL or database writes",
                "no requestId substitution into psaId",
                "no canonical identity changes",
                "no direct match_status=exact",
                "no publish, deploy, or daily-chain changes",
            ],
        },
        "status": "INCOMPLETE_CENSUS",
        "generatedAt": utc_iso(),
        "census": json_value(enriched_census),
        "dbSnapshot": {
            "available": bool(db_identity and generation),
            "databaseName": db_identity.get("database_name"),
            "serverVersion": db_identity.get("server_version"),
            "selectedAt": json_value(db_identity.get("selected_at")),
            "transaction": "READ ONLY / WITH CONSISTENT SNAPSHOT",
            "latestCatalogGeneration": {
                "generationId": generation.get("generation_id"),
                "computedAt": json_value(generation.get("computed_at")),
                "memberCount": int(generation.get("member_count") or 0),
            },
        },
        "evidenceSummary": json_value(evidence_summary),
        "strictKnownSummary": json_value(census_meta.get("strictKnownSummary") or {}),
        "unresolvedQualifiedPrintings": json_value(unresolved),
        "sourceFailures": json_value(source_failures),
        "summary": {
            "knownStrictQualifiedPsaIds": int(census_meta.get("knownStrictQualifiedPsaIdCount") or 0),
            "unresolvedQualifiedPrintings": len(unresolved),
            "sourceFailures": len(source_failures),
            "databaseCompletenessBucketsGenerated": False,
        },
        "rows": [],
    }


def incomplete_census_metadata_from_available_evidence(
    error: Exception,
    exact_meta: Mapping[str, Any] | None,
    census_meta: Mapping[str, Any] | None,
) -> dict[str, Any]:
    metadata = dict(census_meta) if isinstance(census_meta, Mapping) else {}
    exact = exact_meta if isinstance(exact_meta, Mapping) else {}
    metadata["status"] = "INCOMPLETE_CENSUS"
    metadata["declaredVerifiedScopeStatus"] = "INCOMPLETE"
    metadata.setdefault("providerGlobal", False)
    metadata.setdefault("providerGlobalStatus", "INCOMPLETE_PROVIDER_GLOBAL")
    metadata.setdefault(
        "scope",
        exact.get("scope")
        or "GemRate declared bounded census scope; evidence processing did not complete",
    )

    passthrough_keys = (
        "manifestRunId",
        "manifestAttempted",
        "manifestSucceeded",
        "manifestFailed",
        "manifestPartial",
        "manifestPromotable",
        "manifestPromoted",
        "sourceRunPartial",
        "sourceRunFailed",
        "sourceRunUnresolvedReasons",
        "worklistCount",
        "worklistIdsSha256",
        "unverifiedRegistryCandidatesExcludedCount",
        "unverifiedRegistryCandidatesExcludedSha256",
    )
    for key in passthrough_keys:
        if key not in metadata and key in exact:
            metadata[key] = json_value(exact.get(key))

    unresolved = metadata.get("unresolvedQualifiedPrintings")
    if not isinstance(unresolved, list):
        raw_unresolved = exact.get("unresolvedQualifiedPrintings")
        unresolved = list(raw_unresolved) if isinstance(raw_unresolved, list) else []
    metadata["unresolvedQualifiedPrintings"] = json_value(unresolved)

    failures = metadata.get("sourceFailures")
    if not isinstance(failures, list):
        raw_failures = exact.get("sourceFailures")
        failures = list(raw_failures) if isinstance(raw_failures, list) else []
    metadata["sourceFailures"] = json_value(failures)

    strict_summary = metadata.get("strictKnownSummary")
    if not isinstance(strict_summary, Mapping):
        exact_summary = exact.get("strictKnownSummary")
        exact_summary = exact_summary if isinstance(exact_summary, Mapping) else {}
        strict_summary = {
            "knownQualifiedStrictPsaIdsAfterMerge": int(exact.get("uniquePsaIdCount") or 0),
            "unresolvedQualifiedPrintings": len(unresolved),
            "sourceFailureRequests": len(failures),
            "exactLane": json_value(exact_summary),
        }
    metadata["strictKnownSummary"] = json_value(strict_summary)
    metadata.setdefault(
        "knownStrictQualifiedPsaIdCount",
        int(
            get_nested(strict_summary, "knownQualifiedStrictPsaIdsAfterMerge")
            or exact.get("uniquePsaIdCount")
            or 0
        ),
    )

    evidence = metadata.get("evidenceSummary")
    evidence = dict(evidence) if isinstance(evidence, Mapping) else {}
    if "requestedToStrictPsaIdReplacements" not in evidence and exact:
        evidence["requestedToStrictPsaIdReplacements"] = json_value(
            exact.get("requestedToStrictPsaIdReplacements")
        )
    if "missingStrictPsaIdRequestIds" not in evidence and exact:
        evidence["missingStrictPsaIdRequestIds"] = json_value(
            exact.get("missingStrictPsaIdRequestIds")
        )
    evidence["unresolvedQualifiedPrintingCount"] = len(unresolved)
    evidence["sourceFailureCount"] = len(failures)
    evidence["databaseCompletenessBucketsGenerated"] = False
    evidence["processingFailure"] = {
        "type": type(error).__name__,
        "message": str(error),
    }
    metadata["evidenceSummary"] = json_value(evidence)
    metadata["failureReason"] = f"{type(error).__name__}:{error}"
    return metadata


def _markdown_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")


def render_incomplete_markdown(payload: Mapping[str, Any]) -> str:
    census = payload["census"]
    strict = payload["strictKnownSummary"]
    exact = strict.get("exactLane") if isinstance(strict, Mapping) else {}
    exact = exact if isinstance(exact, Mapping) else {}
    replacements = payload["evidenceSummary"].get("requestedToStrictPsaIdReplacements") or {}
    unresolved = payload["unresolvedQualifiedPrintings"]
    failures = payload["sourceFailures"]
    failure = payload.get("failure")
    failure = failure if isinstance(failure, Mapping) else None
    lines = [
        "# CARDZ GemRate DB 完整度盤點及 GROK Handoff",
        "",
        f"> 產生時間：`{payload['generatedAt']}`；狀態：`INCOMPLETE_CENSUS`；DB：單一 consistent read-only snapshot。",
        "",
        "## 結論",
        "",
        *(
            [
                f"- [KNOWN] Processing failure：`{failure.get('type')}`；原因：`{_markdown_cell(failure.get('message'))}`。"
            ]
            if failure
            else []
        ),
        "- [KNOWN] exact-page request ID／頂層 ID／providerEntity ID 都唔係 PSA ID；本報告只認 raw `population_data[grader='psa'].gemrate_id`。",
        "- [KNOWN] exact 卡全名只取同一 raw PSA row 的 `description`，JSON 原字保留，無 compact、無 split。",
        f"- [COMPUTED] Fresh + exact merge 後已知合資格 strict PSA IDs：{payload['summary']['knownStrictQualifiedPsaIds']}；仍未解決合資格 printing：{len(unresolved)}。",
        f"- [COMPUTED] request ID → strict PSA ID replacement pairs：{replacements.get('count', 0)}；aggregate SHA-256：`{replacements.get('aggregateSha256')}`。",
        f"- [COMPUTED] Raw exact 成功 request：{exact.get('successfulExactPageRequests', 0)}；verified worklist source failures：{len(failures)}。",
        "- [KNOWN] 因母體 identity 未完整，`rows=[]`；Catalog／PriceCharting／SNK buckets 全部不生成，避免偽完整報告。",
        f"- [KNOWN] Provider-global 狀態同樣係 `{census.get('providerGlobalStatus')}`；本 scope 亦未完成。",
        "",
        "## Evidence summary",
        "",
        "| 項目 | 數值 |",
        "|---|---:|",
        f"| Manifest attempted / succeeded / failed | {census.get('manifestAttempted')} / {census.get('manifestSucceeded')} / {census.get('manifestFailed')} |",
        f"| Raw PSA rows missing valid strict ID | {exact.get('rawPsaRowsMissingValidStrictId', 0)} |",
        f"| Missing strict ID but POP < 1000 | {exact.get('rawPsaRowsMissingValidStrictIdBelowPopulationFloor', 0)} |",
        f"| Qualified strict unique PSA IDs in exact lane | {exact.get('qualifiedStrictUniquePsaIds', 0)} |",
        f"| Known strict qualified PSA IDs after fresh+exact merge | {payload['summary']['knownStrictQualifiedPsaIds']} |",
        "",
        "## Unresolved qualified printings（交 GROK）",
        "",
        "| Request ID | PSA10 POP | Provider PSA-format full name | Year | Set | Number | Parallel |",
        "|---|---:|---|---|---|---|---|",
    ]
    for row in unresolved:
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(row.get(key))
                for key in (
                    "requestId",
                    "populationPsa10",
                    "fullName",
                    "year",
                    "setName",
                    "collectorNumber",
                    "parallel",
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "每項只准回傳證據；`requestId` 禁止當 `psaId`。完整 raw receipt path／SHA／欄位 provenance 已保留喺 JSON。",
            "",
            "## Exact-page source failures",
            "",
            "| Request ID | Run reason | Latest stored POP | Cohort | Generation |",
            "|---|---|---:|---|---|",
        ]
    )
    for row in failures:
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(row.get(key))
                for key in (
                    "requestId",
                    "reason",
                    "latestStoredPopulation",
                    "cohort",
                    "latestCatalogGenerationId",
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## GROK 邊界",
            "",
            "- 只研究並回傳 GemRate PSA identity evidence；一行一項 JSONL。",
            "- 禁止 SQL／DB 寫入、禁止改 canonical identity、禁止直接設定 exact、禁止 publish／deploy／改日更鏈。",
            "",
            "## 產物一致性",
            "",
            "- [COMPUTED] Markdown 數字直接來自同一 JSON dataset。",
            "- [KNOWN] `rows=[]` 係 fail-closed 結果，唔代表零張卡或 DB 完整。",
            "",
        ]
    )
    return "\n".join(lines)


def markdown_table(bucket: Mapping[str, int], total: int) -> list[str]:
    lines = ["| Bucket | 張數 | 佔母體 |", "|---|---:|---:|"]
    for key, value in bucket.items():
        pct = (value / total * 100.0) if total else 0.0
        lines.append(f"| `{key}` | {value} | {pct:.1f}% |")
    lines.append(f"| **合計** | **{sum(bucket.values())}** | **100.0%** |")
    return lines


def render_markdown(payload: Mapping[str, Any]) -> str:
    census = payload["census"]
    db = payload["dbSnapshot"]
    summary = payload["summary"]
    total = int(summary["censusRows"])
    content = summary["content"]
    work = summary["work"]
    schema = json.dumps(payload["contract"]["grokReturnSchema"], ensure_ascii=False, indent=2)
    lineage_rows = [
        f"| Census scope kind | `{census['scopeKind']}` |",
        f"| Provider-global census | `{'yes' if census.get('providerGlobal') else 'no'}` |",
        f"| Census captured at | `{census['capturedAt']}` |",
        f"| Source SHA-256 | `{census['sha256']}` |",
        f"| 唯一 PSA ID（POP >= 1000） | {census['uniquePsaIdCount']} |",
    ]
    if census["scopeKind"] == "verified_cardz_hybrid_census":
        merge = census["merge"]
        worklist_counts = census["worklistCounts"]
        lineage_rows.extend(
            [
                f"| Declared coverage status | `{census['candidateScopeStatus']}` |",
                f"| Coverage universe | {census['coverageUniverseCount']} unique PSA IDs |",
                f"| Verified latest catalog generation | {worklist_counts['verifiedCatalogGeneration']} IDs |",
                f"| Fresh fixed-snapshot coverage | {census['freshSnapshotAllCount']} IDs / {census['freshSnapshotQualifiedCount']} qualified |",
                f"| Fresh below-threshold IDs excluded from exact refresh | {census['freshBelowThresholdExcludedFromExactRefresh']} |",
                f"| Exact-page worklist | {census['worklistCount']} IDs |",
                f"| Exact-page worklist SHA-256 | `{census['worklistIdsSha256']}` |",
                f"| Exact-page refresh run | `{census.get('manifestRunId')}` |",
                f"| Exact-page attempted / succeeded / failed | {census['manifestAttempted']} / {census['manifestSucceeded']} / {census['manifestFailed']} |",
                f"| Source run partial / promotable | `{str(census['sourceRunPartial']).lower()}` / `{str(census['manifestPromotable']).lower()}` |",
                f"| Registry-only excluded IDs | {census['unverifiedRegistryCandidatesExcludedCount']} |",
                f"| Excluded IDs SHA-256 | `{census['unverifiedRegistryCandidatesExcludedSha256']}` |",
                f"| Fresh/exact overlap overridden by exact | {merge['overlapExactOverrides']} |",
                f"| Overlap population consistent / changed / dropped | {merge['overlapPopulationConsistent']} / {merge['overlapPopulationChanged']} / {merge['overlapDroppedBelowPopulationFloor']} |",
            ]
        )
        source_fact = (
            f"Hybrid coverage universe={census['coverageUniverseCount']}；fresh snapshot="
            f"{census['freshSnapshotAllCount']}；exact-page補充 attempted={census['manifestAttempted']}、"
            f"succeeded={census['manifestSucceeded']}、failed/excluded={census['manifestFailed']}。"
        )
    else:
        lineage_rows.extend(
            [
                f"| Fixed public snapshot set rows | {census['sourceSetCount']} |",
                f"| Fixed public snapshot card rows | {census['sourceSetCardCount']} |",
                f"| Failed set fetches | {census['failedSetCount']} |",
            ]
        )
        source_fact = (
            f"Fixed public snapshot 只含 {census['sourceSetCount']} 個可見 TCG set；"
            "不可用作 provider-global 卡量。"
        )
    lineage_rows.extend(
        [
            f"| PSA observation date | `{census.get('psaObservationDateMin')}` → `{census.get('psaObservationDateMax')}` |",
            f"| DB selected at | `{db['selectedAt']}` |",
            f"| Current universe lock | `{db['currentUniverse']['lockId']}` / {db['currentUniverse']['actualMemberCount']} members |",
            f"| Latest catalog generation | `{db['latestCatalogGeneration']['generationId']}` / {db['latestCatalogGeneration']['memberCount']} members |",
        ]
    )
    lines = [
        "# CARDZ GemRate Verified Hybrid Coverage DB 完整度盤點及 GROK Handoff",
        "",
        f"> 產生時間：`{payload['generatedAt']}`；資料庫：單一 consistent read-only snapshot；全程零 DB 寫入。",
        "",
        "## 結論",
        "",
        f"- [KNOWN] 本報告只涵蓋 `{census['scope']}`。",
        f"- [KNOWN] 呢份報告**唔係 GemRate provider-global census**；verified hybrid coverage 不會被說成全站卡目錄。",
        f"- [KNOWN] Provider-global 狀態為 `{census['status']}`；declared verified scope 為 `{census.get('declaredVerifiedScopeStatus')}`／`{census.get('candidateScopeStatus', census['status'])}`；{source_fact}",
        f"- [COMPUTED] 此範圍內 GemRate `PSA 10 POP >= 1000` 有 {total} 個唯一 `psa_id`。",
        f"- [COMPUTED] Catalog、PriceCharting、SNKRDUNK 三組互斥 primary buckets 均精確加總至 {total}。",
        f"- [COMPUTED] GROK 需要處理 {summary['grokRequiredRows']} 行；優先序為 P0 → P1 → P2 → P3，同級按 PSA10 POP 由高至低。",
        "- [KNOWN] 每行 `gemrate.name`／`fullName`／`rawFullName` 都原樣保留 authority full name；fresh 來自 `psa_details`，exact 來自 immutable raw `population_data[grader=psa].description`，不 compact、不 split。",
        "- [KNOWN] `unsearched` 代表冇逐卡 discovery 證據；不會把今日曾發生的全局 preflight blocker猜成每張卡的失敗原因。",
        "",
        "## Snapshot lineage",
        "",
        "| 項目 | 值 |",
        "|---|---|",
        *lineage_rows,
        "",
        "## Catalog 對照",
        "",
        *markdown_table(summary["catalog"], total),
        "",
        "## PriceCharting 完整度",
        "",
        *markdown_table(summary["pricecharting"], total),
        "",
        f"- [COMPUTED] Strict evidence：{content['pricechartingStrictEvidence']}；有 ready price：{content['pricechartingReadyPrice']}；有 eligible PSA10 sale：{content['pricechartingEligibleSale']}。",
        "- [KNOWN] `exact_non_numeric` 是 deterministic black hole：registry 視為缺綁，但 discovery 可能因已有 exact row而跳過，必須獨立處理。",
        "- [KNOWN] `no_row` 才代表 DB 沒有 PriceCharting identity row；其他 bucket 均保留既有 candidate／裁決／證據狀態。",
        "",
        "## SNKRDUNK 完整度",
        "",
        *markdown_table(summary["snkrdunk"], total),
        "",
        f"- [COMPUTED] Strict evidence：{content['snkrdunkStrictEvidence']}；provider capture：{content['snkrdunkProviderCapture']}；product-page authority：{content['snkrdunkProductPageAuthority']}。",
        f"- [COMPUTED] Ready kline：{content['snkrdunkReadyKline']}；eligible PSA10 sale：{content['snkrdunkEligibleSale']}；current canonical SNK image：{content['snkrdunkCurrentCanonicalImage']}。",
        f"- [COMPUTED] 英文 metadata provenance 完整：{content['englishMetadataProvenanceComplete']}。",
        "- [KNOWN] `snk`／`snk_psa10` 只作 legacy alias 統計，canonical provider 統一為 `snkrdunk`。",
        "- [KNOWN] `no_ready_kline`、`no_eligible_sale`、`no_current_snk_image` 是獨立內容缺口，不等於 identity 錯；冇逐卡供應商回應證據時不會硬標成 `empty_kline`。",
        "",
        "## GROK 工作次序",
        "",
        "| Priority | 張數 | 定義 |",
        "|---|---:|---|",
        f"| P0 | {work['P0']} | GemRate 過線但 DB 完全冇 variant |",
        f"| P1 | {work['P1']} | 有 variant，但 PC 或 SNK `no_row` |",
        f"| P2 | {work['P2']} | candidate／manual review／conflict／rejected／exact evidence 不完整 |",
        f"| P3 | {work['P3']} | 已綁定但缺 price、sale、kline、page、capture、image 或英文 provenance |",
        f"| complete | {work['complete']} | 本盤點未見工作缺口 |",
        "",
        "JSON 主檔已按上述次序排序。每行都有 `work.primaryReason`、`work.allowedNextAction`；GROK 只處理 `work.required=true`。",
        "",
        "## GROK 回傳契約",
        "",
        "每項一行 JSONL：",
        "",
        "```json",
        schema,
        "```",
        "",
        "只准 `decision = propose | not_found | ambiguous | blocked_by_existing_ruling`。`candidateUrls`、`queriesTried` 同 `evidenceSummary` 要保留實際研究證據。",
        "",
        "## 禁止事項",
        "",
        "- 禁止直接 SQL 或任何 DB 寫入。",
        "- 禁止改 canonical identity，禁止覆蓋 rejected／conflict ruling。",
        "- 禁止直接設定 `match_status='exact'`；後續只可交現有 guarded binding 流程另批處理。",
        "- 禁止 publish、deploy、修改日更鏈。",
        "",
        "## 產物一致性",
        "",
        f"- [COMPUTED] JSON rows = {total} = census unique PSA IDs。",
        f"- [COMPUTED] Catalog buckets = {sum(summary['catalog'].values())}；PC buckets = {sum(summary['pricecharting'].values())}；SNK buckets = {sum(summary['snkrdunk'].values())}。",
        "- [KNOWN] 本 Markdown 所有數字直接由同一份 JSON dataset 產生。",
        "",
    ]
    return "\n".join(lines)


def render_known_scope_inventory_markdown(payload: Mapping[str, Any]) -> str:
    base = render_markdown(payload).rstrip()
    summary = payload["summary"]
    gaps = summary["highLevelGaps"]
    queue = summary["grokQueue"]
    unresolved = payload["unresolvedQualifiedPrintings"]
    failures = payload["sourceFailures"]
    labels = (
        ("已知 strict PSA IDs（POP >= 1000）", "knownStrictQualifiedPsaIds"),
        ("合資格但缺 strict PSA ID", "qualifiedPrintingsMissingStrictPsaId"),
        ("未入 DB／沒有 variant", "notInDbNoVariant"),
        ("Catalog pending／inactive", "catalogPendingOrInactive"),
        ("Catalog alias", "catalogAlias"),
        ("GemRate identity_pending=true", "gemrateIdentityPendingFlag"),
        ("PriceCharting 完全沒有 identity row", "pricechartingNoIdentityRow"),
        ("PriceCharting 未有 strict evidence", "pricechartingWithoutStrictEvidence"),
        ("PriceCharting 缺 ready price", "pricechartingMissingReadyPrice"),
        ("PriceCharting 缺 eligible sale", "pricechartingMissingEligibleSale"),
        ("SNKRDUNK 完全沒有 identity row", "snkrdunkNoIdentityRow"),
        ("SNKRDUNK 未有 strict evidence", "snkrdunkWithoutStrictEvidence"),
        ("SNKRDUNK 缺 provider capture", "snkrdunkMissingProviderCapture"),
        ("SNKRDUNK 缺 product-page authority", "snkrdunkMissingProductPageAuthority"),
        ("SNKRDUNK 缺 ready kline", "snkrdunkMissingReadyKline"),
        ("SNKRDUNK 缺 eligible sale", "snkrdunkMissingEligibleSale"),
        ("任何 canonical image projection 已存在", "canonicalImageProjectionPresent"),
        ("任何 canonical image projection 都沒有", "canonicalImageProjectionMissing"),
        ("已綁 SNKRDUNK 但缺 current SNK image", "snkrdunkBoundCardsMissingCurrentSnkImage"),
        ("英文 metadata provenance 未完整", "englishMetadataProvenanceIncomplete"),
        ("PC／SNK 兩邊都沒有 ready market price", "missingReadyMarketPriceAcrossBothPrimarySources"),
        ("PC／SNK 兩邊都沒有 eligible sale", "missingEligibleSaleAcrossBothPrimarySources"),
        ("Verified exact source refresh failures", "verifiedExactSourceFailures"),
    )
    lines = [
        base,
        "",
        "## 已知範圍缺口總表（GROK 工作母表）",
        "",
        "- [KNOWN] Census 仍有 blocker，但已知 1,660 個 strict PSA ID 已逐張完成 DB／PriceCharting／SNKRDUNK／內容缺口盤點；唔再因 blocker 輸出空表。",
        "- [KNOWN] `rows` 保存逐張完整證據；`grokQueue` 係按 P0 → P3、POP 由高至低排好嘅 flat 7×24 執行清單。",
        "",
        "| 缺口 | 張數 |",
        "|---|---:|",
    ]
    lines.extend(f"| {_markdown_cell(label)} | {int(gaps[key])} |" for label, key in labels)
    lines.extend(
        [
            "",
            "## Identity 狀態分佈",
            "",
            "### catalog_variant.identity_status",
            "",
            "| 狀態 | 張數 |",
            "|---|---:|",
        ]
    )
    lines.extend(
        f"| `{_markdown_cell(status)}` | {int(count)} |"
        for status, count in summary["variantIdentityStatus"].items()
    )
    lines.extend(
        [
            "",
            "### canonical printing identity_status",
            "",
            "| 狀態 | 張數 |",
            "|---|---:|",
        ]
    )
    lines.extend(
        f"| `{_markdown_cell(status)}` | {int(count)} |"
        for status, count in summary["canonicalPrintingIdentityStatus"].items()
    )
    lines.extend(
        [
            "",
            "## GROK 7×24 Queue",
            "",
            f"- [COMPUTED] 總工作項：{queue['total']}；P0={queue['byPriority']['P0']}、P1={queue['byPriority']['P1']}、P2={queue['byPriority']['P2']}、P3={queue['byPriority']['P3']}。",
            "- 每次由 `grokQueue` 順序領一項；用 `recordRef` 回看 `rows`／blocker 原始證據；只回傳 JSONL proposal／not_found／ambiguous，禁止直接寫 DB。",
            "",
            "| Kind | 工作項 |",
            "|---|---:|",
        ]
    )
    lines.extend(
        f"| `{_markdown_cell(kind)}` | {int(count)} |"
        for kind, count in queue["byKind"].items()
    )
    lines.extend(
        [
            "",
            "## 合資格但缺 strict PSA ID（P0）",
            "",
            "| Request ID | PSA10 POP | 完整 PSA-format full name |",
            "|---|---:|---|",
        ]
    )
    for row in unresolved:
        lines.append(
            f"| `{_markdown_cell(row.get('requestId'))}` | {int(row.get('populationPsa10') or 0)} | {_markdown_cell(row.get('fullName'))} |"
        )
    lines.extend(
        [
            "",
            "## Exact GemRate source failures",
            "",
            "| Request ID | 原因 | Latest stored POP | Cohort |",
            "|---|---|---:|---|",
        ]
    )
    for row in failures:
        lines.append(
            f"| `{_markdown_cell(row.get('requestId'))}` | {_markdown_cell(row.get('reason'))} | "
            f"{_markdown_cell(row.get('latestStoredPopulation'))} | {_markdown_cell(row.get('cohort'))} |"
        )
    lines.extend(
        [
            "",
            "## Cron／GROK 執行邊界",
            "",
            "- 輸入：`grokQueue`；`recordRef` 指向完整逐卡證據。",
            "- 輸出：一行一項 JSONL，只可 propose／not_found／ambiguous／blocked_by_existing_ruling。",
            "- 禁止 SQL、禁止直接改 canonical identity、禁止覆蓋 rejected／conflict、禁止直接設 exact、禁止 publish／deploy。",
            "",
        ]
    )
    return "\n".join(lines)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="One-shot CARDZ GemRate DB completeness handoff")
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Exact-page source-run manifest; with --census this enables fail-closed hybrid mode",
    )
    parser.add_argument(
        "--census",
        type=Path,
        required=True,
        help="Fresh fixed-snapshot qualified JSONL; required for hybrid coverage",
    )
    parser.add_argument(
        "--worklist-metadata",
        type=Path,
        help="Required in --manifest mode; binds the hybrid exact-page worklist",
    )
    parser.add_argument(
        "--refresh-not-before",
        "--harvest-not-before",
        dest="refresh_not_before",
        required=True,
    )
    parser.add_argument("--json-out", required=True, type=Path)
    parser.add_argument("--markdown-out", required=True, type=Path)
    args = parser.parse_args()

    exact_meta: dict[str, Any] | None = None
    census_meta: dict[str, Any] | None = None
    snapshot: dict[str, Any] | None = None
    try:
        repo = args.repo.resolve()
        not_before = parse_instant(args.refresh_not_before)
        fresh_rows, fresh_meta, fresh_all_psa_ids = validate_fresh_census(
            args.census.resolve(),
            not_before,
        )
        if args.manifest is not None:
            if args.worklist_metadata is None:
                raise IncompleteCensus("missing_required_argument:--worklist-metadata")
            exact_rows, exact_meta, exact_observed_populations = validate_exact_page_manifest(
                args.manifest.resolve(),
                args.worklist_metadata.resolve(),
                not_before,
            )
            census_rows, census_meta = build_hybrid_census(
                fresh_rows,
                fresh_meta,
                fresh_all_psa_ids,
                exact_rows,
                exact_meta,
                exact_observed_populations,
            )
        else:
            if args.worklist_metadata is not None:
                raise IncompleteCensus("--worklist-metadata_requires_--manifest")
            census_rows, census_meta = fresh_rows, fresh_meta
        if census_meta.get("status") == "INCOMPLETE_CENSUS":
            raw_failures = census_meta.get("sourceFailures")
            raw_failures = raw_failures if isinstance(raw_failures, list) else []
            source_failure_ids = [
                str(row.get("requestId") or "").strip().lower()
                for row in raw_failures
                if isinstance(row, Mapping)
                and HEX40.fullmatch(str(row.get("requestId") or "").strip().lower())
            ]
            census_ids = [str(row["psa_id"]).lower() for row in census_rows]
            snapshot = load_database_snapshot(repo, census_ids, source_failure_ids)
            payload = build_known_scope_inventory_payload(census_rows, census_meta, snapshot)
            atomic_write(
                args.json_out.resolve(),
                json.dumps(json_value(payload), ensure_ascii=False, indent=2) + "\n",
            )
            atomic_write(
                args.markdown_out.resolve(),
                render_known_scope_inventory_markdown(payload),
            )
            print(
                json.dumps(
                    {
                        "status": payload["status"],
                        "strictUniquePsaIdsObserved": get_nested(
                            payload, "strictKnownSummary", "exactLane", "strictUniquePsaIdsObserved"
                        ),
                        "unresolvedQualifiedPrintings": len(payload["unresolvedQualifiedPrintings"]),
                        "sourceFailures": len(payload["sourceFailures"]),
                        "knownScopeRows": len(payload["rows"]),
                        "grokQueue": len(payload["grokQueue"]),
                        "catalog": payload["summary"]["catalog"],
                        "pricecharting": payload["summary"]["pricecharting"],
                        "snkrdunk": payload["summary"]["snkrdunk"],
                        "highLevelGaps": payload["summary"]["highLevelGaps"],
                        "jsonOut": str(args.json_out.resolve()),
                        "markdownOut": str(args.markdown_out.resolve()),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        census_ids = [str(row["psa_id"]).lower() for row in census_rows]
        snapshot = load_database_snapshot(repo, census_ids)
        payload = build_payload(census_rows, census_meta, snapshot)
        json_text = json.dumps(json_value(payload), ensure_ascii=False, indent=2) + "\n"
        markdown_text = render_markdown(payload)
        atomic_write(args.json_out.resolve(), json_text)
        atomic_write(args.markdown_out.resolve(), markdown_text)
        print(
            json.dumps(
                {
                    "status": payload["census"]["status"],
                    "candidateScopeStatus": payload["census"].get("candidateScopeStatus"),
                    "censusRows": payload["summary"]["censusRows"],
                    "catalog": payload["summary"]["catalog"],
                    "pricecharting": payload["summary"]["pricecharting"],
                    "snkrdunk": payload["summary"]["snkrdunk"],
                    "work": payload["summary"]["work"],
                    "jsonOut": str(args.json_out.resolve()),
                    "markdownOut": str(args.markdown_out.resolve()),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as error:
        failure_meta = incomplete_census_metadata_from_available_evidence(
            error,
            exact_meta,
            census_meta,
        )
        failure = build_incomplete_payload(failure_meta, snapshot)
        failure["reason"] = str(error)
        failure["failure"] = {
            "type": type(error).__name__,
            "message": str(error),
        }
        atomic_write(
            args.json_out.resolve(),
            json.dumps(failure, ensure_ascii=False, indent=2) + "\n",
        )
        atomic_write(
            args.markdown_out.resolve(),
            render_incomplete_markdown(failure),
        )
        print(
            json.dumps(
                {
                    "status": "INCOMPLETE_CENSUS",
                    "failureType": type(error).__name__,
                    "reason": str(error),
                    "manifestAttempted": failure["census"].get("manifestAttempted"),
                    "manifestSucceeded": failure["census"].get("manifestSucceeded"),
                    "manifestFailed": failure["census"].get("manifestFailed"),
                    "unresolvedQualifiedPrintings": len(
                        failure["unresolvedQualifiedPrintings"]
                    ),
                    "sourceFailures": len(failure["sourceFailures"]),
                    "rows": 0,
                    "jsonOut": str(args.json_out.resolve()),
                    "markdownOut": str(args.markdown_out.resolve()),
                },
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
