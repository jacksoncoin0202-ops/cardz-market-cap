#!/usr/bin/env python3
"""Read-only, full-universe QC for the canonical CARDZ MySQL database.

The deterministic schema-5 universe candidate is the audit population.  This
tool never repairs, fills, promotes, or publishes anything: it opens one
consistent read-only transaction, evaluates canonical facts, and writes an
immutable private report plus a receipt bound to the exact report bytes.

Exit codes:

* 0: every qualified universe member passed the release-blocking checks;
* 1: the audit completed and found release-blocking gaps;
* 2: configuration, database, or immutable-output failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import pymysql


PIPELINES_DIR = Path(__file__).resolve().parent
if str(PIPELINES_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINES_DIR))

from image_geometry_qc import inspect_path as inspect_image_geometry
from image_source_qc import classify_source
from sample_image_qc import SampleImageRejected, assert_raw_bytes_not_sample
from data_routing import DEFAULT_RELEASE_PROFILE, load_registry, load_release_profile
from release_image_selector import select_release_image
from universe_authority import (
    CANONICAL_DATABASE,
    _printing_row_identity,
    active_universe_lock_hash,
    build_candidate as build_formal_universe_candidate,
    connect_from_values,
    latest_exact_population_rows,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "data" / "runtime" / "config" / "backend.env"
DEFAULT_ROUTING_CONFIG = ROOT / "config" / "data-routing.json"
DEFAULT_OUTPUT_ROOT = (
    ROOT / "data" / "runtime" / "private-reports" / "canonical-db-qc"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
EBAY_CARD_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
EXACT_GRADE_LABELS = frozenset({"10", "PSA 10", "PSA10"})
# Operator 2026-07-30: tcgpricelookup purged from DB — never treat as exact price.
# g10_kline stays banned (G10-internal invented).  DADDY 2026-07-31: an
# explicit PSA10 field on a saved, exact-bound PriceCharting artifact is an
# exact current price authority; generic graded / raw PriceCharting fields are not.
EXACT_PRICE_SOURCES = frozenset(
    {"snk_psa10", "snk", "snkrdunk", "ebay", "pricecharting"}
)
PRICE_IDENTITY_SOURCES: dict[str, frozenset[str]] = {
    "snk_psa10": frozenset({"snk_psa10", "snk", "snkrdunk"}),
    "snk": frozenset({"snk_psa10", "snk", "snkrdunk"}),
    "snkrdunk": frozenset({"snk_psa10", "snk", "snkrdunk"}),
    # G10 eBay PSA10 sold median (g10_ebay_ingest) — identity is altxyz UUID
    # PC path may also land as ebay with pc: external ids
    "ebay": frozenset({"ebay", "pricecharting"}),
    "pricecharting": frozenset({"pricecharting"}),
}
ACCEPTED_SALE_TIMESTAMP_QUALITIES = frozenset(
    {
        "exact",
        "date",
        "timestamp",
        "exact_date",
        "relative_resolved",
        "relative_subday",
    }
)
ACCEPTED_SALE_COVERAGE_STATUSES = frozenset(
    {"partial", "complete", "certified"}
)
SNK_SALE_SOURCES = frozenset({"snk_psa10", "snk", "snkrdunk", "snk_grade"})
SALE_IDENTITY_SOURCES: dict[str, frozenset[str]] = {
    **{
        source: frozenset({"snk_psa10", "snk", "snkrdunk"})
        for source in SNK_SALE_SOURCES
    },
    "ebay": frozenset({"ebay"}),
}
POPULATION_MAX_AGE = timedelta(days=7)
PRICE_MAX_AGE = timedelta(hours=48)
PRICE_CROSS_SOURCE_MAX_RATIO = Decimal("2")
INDEX_MAX_AGE = timedelta(hours=48)
SALES_WINDOW = timedelta(days=30)
MIN_PURE_PSA10_SALES_30D = 10
ANCHOR_TOLERANCE_DAYS = 5

BLOCKER_CATEGORY: dict[str, str] = {
    "catalog_identity_missing": "identity",
    "canonical_identity_incomplete": "identity",
    "canonical_identity_mismatch": "identity",
    "canonical_identity_not_confirmed": "identity",
    "canonical_printing_missing": "identity",
    "canonical_printing_not_confirmed": "identity",
    "canonical_printing_hash_invalid": "identity",
    "canonical_printing_duplicate": "identity",
    "gemrate_identity_not_exact": "sourceOwnership",
    "gemrate_identity_ambiguous": "sourceOwnership",
    "price_source_identity_not_exact": "sourceOwnership",
    "sale_source_identity_not_exact": "sourceOwnership",
    "population_not_exact_gemrate_psa10": "population",
    "population_below_qualified_threshold": "population",
    "population_outside_monitoring_band": "population",
    "population_stale": "population",
    "population_future_dated": "temporal",
    "population_timestamp_invalid": "temporal",
    "population_payload_hash_invalid": "population",
    "exact_psa10_price_missing": "price",
    "exact_psa10_price_stale": "price",
    "exact_psa10_price_not_ready": "price",
    "exact_psa10_price_insufficient_independent_sources": "price",
    "exact_psa10_price_source_spread_gt_2x": "price",
    "future_price_metric": "temporal",
    "price_anchor_source_method_mismatch": "price",
    "price_anchor_not_derivable": "anomalies",
    "psa10_sales_30d_missing": "sales",
    "psa10_sales_30d_insufficient": "sales",
    "psa10_sale_value_invalid": "sales",
    "sale_timestamp_quality_invalid": "sales",
    "sale_coverage_status_invalid": "sales",
    "sale_payload_hash_invalid": "sales",
    "future_sale_metric": "temporal",
    "bundle_sale_excluded": "sales",
    "non_psa10_sale_excluded": "sales",
    "market_cap_not_materialized": "marketCap",
    "market_cap_snapshot_stale": "marketCap",
    "market_cap_formula_mismatch": "marketCap",
    "market_cap_current_price_mismatch": "marketCap",
    "market_cap_current_population_mismatch": "marketCap",
    "future_market_cap_metric": "temporal",
    "image_raw_front_missing": "imageIdentity",
    "image_hash_invalid": "imageIdentity",
    "image_asset_missing": "imageIdentity",
    "image_asset_hash_mismatch": "imageIdentity",
    "image_source_pointer_missing": "imageIdentity",
    "image_source_pointer_disabled": "imageIdentity",
    "image_review_binding_missing": "imageIdentity",
    "image_review_binding_drift": "imageIdentity",
    "image_historically_human_rejected": "imageIdentity",
    "image_not_public_allowed": "imageSemantic",
    "image_qc_missing": "imageSemantic",
    "image_qc_version_not_current": "imageSemantic",
    "image_not_human_or_vision_confirmed": "imageSemantic",
    "image_card_number_mismatch": "imageIdentity",
    "image_tcg_mismatch": "imageIdentity",
    "image_not_raw_front": "imageIdentity",
    "image_sample_or_placeholder": "imageSample",
    "image_sample_scan_failed": "imageSample",
    "image_source_known_sample": "imageSample",
    "image_canvas_geometry_invalid": "imageGeometry",
    "image_geometry_scan_failed": "imageGeometry",
    "image_cross_tcg_duplicate": "imageDuplicate",
    "image_unapproved_duplicate": "imageDuplicate",
    "image_qc_future_dated": "temporal",
}


def resolved_release_profile(
    release_profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the immutable policy envelope used for one audit.

    Direct callers that predate release profiles retain strict behaviour.  The
    command-line entrypoint supplies the configured effective profile instead.
    """

    if release_profile is None:
        return load_release_profile(load_registry(DEFAULT_ROUTING_CONFIG), "strict-v1")
    profile_id = str(release_profile.get("releaseProfile") or "").strip()
    policy = release_profile.get("policy")
    policy_sha256 = str(release_profile.get("policySha256") or "").strip().casefold()
    if not profile_id or not isinstance(policy, Mapping) or not SHA256_RE.fullmatch(policy_sha256):
        raise CanonicalDbQcError("release profile envelope is invalid")
    return {
        "releaseProfile": profile_id,
        "policy": dict(policy),
        "policySha256": policy_sha256,
    }


def _policy_int(policy: Mapping[str, Any], key: str) -> int:
    value = policy.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CanonicalDbQcError(f"release profile has invalid {key}")
    return value


def _identity_authority_family(source_code: Any) -> str | None:
    source = str(source_code or "").strip().casefold()
    if source in {"snk", "snkrdunk", "snk_psa10"}:
        return "snk"
    if source in {"gemrate", "pricecharting", "psa"}:
        return source
    return None


def exact_identity_authorities(
    source_rows: Sequence[Mapping[str, Any]],
    recognized_authorities: Sequence[Any],
) -> tuple[dict[str, set[str]], list[str]]:
    """Return exact recognized bindings and only proven authority conflicts.

    Multiple different providers are expected.  A conflict is only declared
    when *one* authority family claims more than one exact external entity for
    the same CARDZ variant; cross-provider disagreement remains evidence, not
    an invented conflict.
    """

    recognized = {str(value).strip().casefold() for value in recognized_authorities}
    bindings: dict[str, set[str]] = defaultdict(set)
    for row in source_rows:
        if str(row.get("match_status") or "").strip().casefold() != "exact":
            continue
        family = _identity_authority_family(row.get("source_code"))
        if family is None or family not in recognized:
            continue
        external_id = str(row.get("external_entity_id") or "").strip()
        if external_id:
            bindings[family].add(external_id)
    conflicts = sorted(
        family for family, external_ids in bindings.items() if len(external_ids) > 1
    )
    return dict(bindings), conflicts


def explicit_identity_conflict_families(
    source_rows: Sequence[Mapping[str, Any]],
    recognized_authorities: Sequence[Any],
) -> list[str]:
    """Return only source rows explicitly recorded as an identity conflict.

    Relaxed launch permits several exact URLs/IDs from the same authority
    family.  That is a multi-route binding, not proof that two printings are
    being conflated.  A source row marked ``conflict`` is the durable evidence
    that still blocks public provisional identity.
    """

    recognized = {str(value).strip().casefold() for value in recognized_authorities}
    conflicts: set[str] = set()
    for row in source_rows:
        family = _identity_authority_family(row.get("source_code"))
        status = str(row.get("match_status") or "").strip().casefold()
        if family in recognized and status in {"conflict", "printing_conflict"}:
            conflicts.add(family)
    return sorted(conflicts)


def release_identity(
    catalog: Mapping[str, Any],
    source_rows: Sequence[Mapping[str, Any]],
    card: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> tuple[str | None, tuple[str, ...] | None, dict[str, Any], list[str]]:
    """Evaluate the public identity state without writing a printing row.

    ``confirmed`` remains the materialized canonical seven-part tuple.  The
    relaxed profile may expose a complete, evidence-hashed candidate tuple as
    ``provisional`` when one recognized authority is exact and there is no
    proven same-authority conflict.  It never promotes that tuple in MySQL.
    """

    blockers: list[str] = []
    required = (
        catalog.get("tcg_code"),
        catalog.get("card_language"),
        catalog.get("canonical_name"),
        catalog.get("set_name"),
        catalog.get("collector_number"),
    )
    if any(value is None or not str(value).strip() for value in required):
        blockers.append("canonical_identity_incomplete")
    if str(catalog.get("identity_status") or "") != "confirmed":
        blockers.append("canonical_identity_not_confirmed")
    if (
        str(catalog.get("tcg_code") or "") != str(card.get("tcg") or "")
        or str(catalog.get("set_name") or "") != str(card.get("setName") or "")
        or str(catalog.get("collector_number") or "")
        != str(card.get("collectorNumber") or "")
    ):
        blockers.append("canonical_identity_mismatch")

    printing_key = _printing_key(catalog)
    if printing_key is None:
        blockers.append("canonical_printing_missing")
    recognized = policy.get("recognizedIdentityAuthorities")
    if not isinstance(recognized, Sequence) or isinstance(recognized, (str, bytes)):
        raise CanonicalDbQcError("release profile has invalid recognizedIdentityAuthorities")
    bindings, duplicate_exact_families = exact_identity_authorities(source_rows, recognized)
    # Preserve the audit profile's historic duplicate-ID fail-closed behavior.
    # The relaxed profile is deliberately narrower: only a conflict already
    # evidenced by the source binding blocks a card, so 1/2/4 exact routes do
    # not turn into a made-up printing conflict.
    require_canonical = policy.get("requireCanonicalPrinting") is True
    conflicts = (
        duplicate_exact_families
        if require_canonical
        else explicit_identity_conflict_families(source_rows, recognized)
    )
    if conflicts:
        blockers.append("identity_authority_conflict")
    minimum_bindings = _policy_int(policy, "minimumExactAuthorityBindings")
    if len(bindings) < minimum_bindings:
        blockers.append("identity_authority_exact_binding_insufficient")

    status: str | None = None
    printing_status = str(catalog.get("printing_identity_status") or "").strip().casefold()
    declared_printing_hash = str(catalog.get("canonical_printing_sha256") or "").casefold()
    evidence_hash = str(catalog.get("printing_evidence_sha256") or "").casefold()
    expected_printing_hash = (
        hashlib.sha256("|".join(printing_key).encode("utf-8")).hexdigest()
        if printing_key is not None
        else None
    )
    if printing_key is not None and (
        not SHA256_RE.fullmatch(declared_printing_hash)
        or declared_printing_hash != expected_printing_hash
    ):
        blockers.append("canonical_printing_hash_invalid")
    if require_canonical:
        if printing_status != "canonical":
            blockers.append("canonical_printing_not_confirmed")
        if printing_key is not None and printing_status == "canonical":
            status = "confirmed"
    else:
        # Candidate printing remains private/auditable until the ordinary
        # materialization path approves it.  A public relaxed release only
        # labels it provisional; it never rewrites the DB row tonight.
        if printing_key is not None and SHA256_RE.fullmatch(evidence_hash):
            status = "confirmed" if printing_status == "canonical" else "provisional"
        elif printing_key is not None:
            blockers.append("canonical_printing_evidence_invalid")
    # A provisional label must never survive its own identity rejection.  This
    # keeps zero-source and explicit-conflict rows from looking publishable in
    # a downstream dossier even though the card-level gate would exclude them.
    if blockers:
        status = None
    return status, printing_key, {
        "variantId": int(catalog["variant_id"]),
        "identityStatus": status,
        "exactAuthorityBindings": {
            authority: sorted(external_ids)
            for authority, external_ids in sorted(bindings.items())
        },
        "authorityConflictFamilies": conflicts,
        "tcg": str(catalog.get("tcg_code") or ""),
        "cardLanguage": str(catalog.get("card_language") or ""),
        "set": str(catalog.get("set_name") or ""),
        "collectorNumber": str(catalog.get("collector_number") or ""),
        "edition": str(catalog.get("edition_code") or ""),
        "parallel": str(catalog.get("parallel_code") or ""),
        "finish": str(catalog.get("finish_code") or ""),
        "printingSha256": catalog.get("canonical_printing_sha256"),
        "printingEvidenceSha256": catalog.get("printing_evidence_sha256"),
    }, blockers


class CanonicalDbQcError(RuntimeError):
    """Raised when the read-only audit cannot produce trustworthy evidence."""


def canonical_json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    else:
        text = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    return (text + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def decimal_value(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key, separator, value = line.partition("=")
        if separator and key and key.replace("_", "").isalnum():
            values[key] = value.strip()
    return values


def connection_values(args: argparse.Namespace) -> dict[str, str]:
    values = {
        "CARDZ_DB_HOST": "127.0.0.1",
        "CARDZ_DB_PORT": "3308",
        "CARDZ_DB_NAME": CANONICAL_DATABASE,
        "CARDZ_DB_USER": "cardz",
        **read_env_file(args.config.resolve()),
    }
    overrides = {
        "CARDZ_DB_HOST": args.host,
        "CARDZ_DB_PORT": str(args.port) if args.port is not None else None,
        "CARDZ_DB_NAME": args.database,
        "CARDZ_DB_USER": args.user,
        "CARDZ_DB_PASSWORD": args.password,
        "CARDZ_DB_SSL_CA": os.environ.get("CARDZ_DB_SSL_CA"),
    }
    for key, value in overrides.items():
        if value is not None and str(value).strip():
            values[key] = str(value).strip()
        elif os.environ.get(key):
            values[key] = os.environ[key].strip()
    required = (
        "CARDZ_DB_HOST",
        "CARDZ_DB_PORT",
        "CARDZ_DB_NAME",
        "CARDZ_DB_USER",
        "CARDZ_DB_PASSWORD",
    )
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise CanonicalDbQcError(
            f"read-only database configuration is incomplete: {', '.join(missing)}"
        )
    if values["CARDZ_DB_NAME"] != CANONICAL_DATABASE:
        raise CanonicalDbQcError(
            f"QC refuses non-canonical database: {values['CARDZ_DB_NAME']}"
        )
    return values


def _fetch(
    connection: Any,
    sql: str,
    params: Sequence[Any] = (),
) -> list[dict[str, Any]]:
    """Execute one SELECT and reject accidental mutation in the audit module."""

    normalized = re.sub(r"\s+", " ", sql).strip()
    if not normalized.upper().startswith(("SELECT ", "WITH ")):
        raise CanonicalDbQcError("canonical DB QC attempted a non-read-only statement")
    with connection.cursor() as cursor:
        cursor.execute(sql, tuple(params))
        return [dict(row) for row in cursor.fetchall()]


def _placeholders(values: Sequence[Any]) -> str:
    if not values:
        raise CanonicalDbQcError("canonical DB QC cannot query an empty candidate set")
    return ",".join(["%s"] * len(values))


def _group(rows: Iterable[Mapping[str, Any]], key: str) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row[key])].append(dict(row))
    return dict(grouped)


def matching_qc_index_evaluation_id(
    connection: Any,
    formal_lock_sha256: str,
    variant_ids: Sequence[int],
) -> int | None:
    """Find the current evaluation for this exact formal universe lock.

    The DB QC discovery cohort intentionally contains every POP-qualified card,
    including cards without a canonical printing.  Ranking is only materialized
    for the stricter formal schema-5 universe, so binding an evaluation to the
    discovery hash/member set would falsely hide an existing formal ranking.
    """

    lock_hash = str(formal_lock_sha256 or "").casefold()
    if not SHA256_RE.fullmatch(lock_hash):
        return None

    rows = _fetch(
        connection,
        """
        SELECT
            evaluation.id,
            evaluation.publish_gate_status,
            universe.id AS universe_lock_id,
            universe.member_count
        FROM market_alert_evaluation AS evaluation
        JOIN market_universe_lock AS universe
          ON universe.id = evaluation.universe_lock_id
        WHERE universe.lock_sha256 = %s
          AND universe.is_current = 1
        ORDER BY
            (evaluation.publish_gate_status = 'passed') DESC,
            evaluation.id DESC
        """,
        (lock_hash,),
    )
    expected = set(int(value) for value in variant_ids)
    for row in rows:
        if int(row.get("member_count") or 0) != len(expected):
            continue
        member_rows = _fetch(
            connection,
            """
            SELECT variant_id
            FROM market_universe_member
            WHERE universe_lock_id = %s
            ORDER BY variant_id
            """,
            (int(row["universe_lock_id"]),),
        )
        if {int(member["variant_id"]) for member in member_rows} == expected:
            return int(row["id"])
    return None


def load_database_facts(
    connection: Any,
    candidate: Mapping[str, Any],
    as_of: datetime,
    *,
    formal_lock_sha256: str | None = None,
    formal_opaque_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Load the bounded fact set for one deterministic candidate."""

    raw_cards = candidate.get("cards")
    monitoring = candidate.get("monitoringCandidates")
    if not isinstance(raw_cards, list) or not isinstance(monitoring, list):
        raise CanonicalDbQcError("schema-5 universe candidate is invalid")
    opaque_ids = [
        str(row.get("pokedexId") or "")
        for row in [*raw_cards, *monitoring]
        if isinstance(row, Mapping)
    ]
    if not opaque_ids or any(not value for value in opaque_ids):
        raise CanonicalDbQcError("universe candidate contains an invalid CARDZ ID")

    opaque_marks = _placeholders(opaque_ids)
    catalog_rows = _fetch(
        connection,
        f"""
        SELECT
            variant.id AS variant_id,
            variant.opaque_id,
            variant.tcg_code,
            variant.card_language,
            variant.canonical_name,
            variant.set_name,
            variant.collector_number,
            variant.identity_status,
            printing.tcg_code AS printing_tcg_code,
            printing.card_language AS printing_card_language,
            printing.set_name AS printing_set_name,
            printing.collector_number AS printing_collector_number,
            printing.edition_code,
            printing.parallel_code,
            printing.finish_code,
            printing.canonical_printing_sha256,
            printing.identity_status AS printing_identity_status,
            printing.evidence_sha256 AS printing_evidence_sha256
        FROM catalog_variant AS variant
        LEFT JOIN catalog_printing_identity AS printing
          ON printing.variant_id = variant.id
        WHERE variant.opaque_id IN ({opaque_marks})
        """,
        opaque_ids,
    )
    catalog_by_opaque = {str(row["opaque_id"]): row for row in catalog_rows}
    variant_ids = sorted({int(row["variant_id"]) for row in catalog_rows})
    if not variant_ids:
        return {
            "catalogByOpaque": {},
            "sourceIdentitiesByVariant": {},
            "pricesByVariant": {},
            "salesByVariant": {},
            "indexByVariant": {},
            "imagesByVariant": {},
            "rowCounts": {
                "catalog": 0,
                "sourceIdentities": 0,
                "prices": 0,
                "sales": 0,
                "indexConstituents": 0,
                "imageRows": 0,
            },
        }

    variant_marks = _placeholders(variant_ids)
    source_rows = _fetch(
        connection,
        f"""
        SELECT
            COALESCE(alias.canonical_variant_id, identity.variant_id) AS variant_id,
            identity.variant_id AS bound_variant_id,
            identity.source_code,
            identity.external_entity_id,
            identity.match_status,
            identity.evidence_sha256
        FROM catalog_source_identity AS identity
        LEFT JOIN catalog_variant_alias AS alias
          ON alias.duplicate_variant_id = identity.variant_id
        WHERE COALESCE(alias.canonical_variant_id, identity.variant_id)
              IN ({variant_marks})
        """,
        variant_ids,
    )
    price_since = as_of - timedelta(days=40)
    price_rows = _fetch(
        connection,
        f"""
        SELECT
            COALESCE(alias.canonical_variant_id, price.variant_id) AS variant_id,
            price.id,
            price.source_code,
            price.observed_date,
            price.effective_at,
            price.price_usd,
            price.source_priority,
            price.metric_status,
            price.payload_sha256
        FROM market_price_observation AS price
        LEFT JOIN catalog_variant_alias AS alias
          ON alias.duplicate_variant_id = price.variant_id
        WHERE COALESCE(alias.canonical_variant_id, price.variant_id)
              IN ({variant_marks})
          AND price.effective_at >= %s
        ORDER BY price.variant_id, price.effective_at, price.id
        """,
        [*variant_ids, price_since.replace(tzinfo=None)],
    )
    sale_since = as_of - SALES_WINDOW
    sale_rows = _fetch(
        connection,
        f"""
        SELECT
            COALESCE(alias.canonical_variant_id, sale.variant_id) AS variant_id,
            sale.id,
            sale.source_code,
            sale.external_entity_id,
            sale.grader_code,
            sale.grade_label,
            sale.sold_at,
            sale.fetched_at,
            sale.timestamp_quality,
            sale.unit_price_usd,
            sale.quantity,
            sale.transaction_value_usd,
            sale.coverage_status,
            sale.source_payload_sha256
        FROM market_sale_observation AS sale
        LEFT JOIN catalog_variant_alias AS alias
          ON alias.duplicate_variant_id = sale.variant_id
        WHERE COALESCE(alias.canonical_variant_id, sale.variant_id)
              IN ({variant_marks})
          AND (
            sale.sold_at >= %s
            OR sale.fetched_at > %s
          )
        ORDER BY sale.variant_id, sale.sold_at, sale.id
        """,
        [
            *variant_ids,
            sale_since.replace(tzinfo=None),
            as_of.replace(tzinfo=None),
        ],
    )
    formal_opaque = {str(value) for value in formal_opaque_ids}
    formal_variant_ids = {
        int(catalog_by_opaque[opaque_id]["variant_id"])
        for opaque_id in formal_opaque
        if opaque_id in catalog_by_opaque
    }
    # Any missing catalog row is a cohort drift.  Do not borrow a ranking from
    # a nearby lock: the audit will report the affected formal rows as missing.
    if len(formal_variant_ids) != len(formal_opaque):
        formal_variant_ids = set()
    market_evaluation_id = (
        matching_qc_index_evaluation_id(
            connection, str(formal_lock_sha256 or ""), sorted(formal_variant_ids)
        )
        if formal_variant_ids
        else None
    )
    index_rows = []
    if market_evaluation_id is not None:
        index_rows = _fetch(
            connection,
            f"""
            SELECT
                COALESCE(alias.canonical_variant_id, constituent.variant_id)
                    AS variant_id,
                snapshot.index_code,
                snapshot.index_version,
                snapshot.effective_at,
                snapshot.effective_date,
                constituent.rank_position,
                constituent.reference_price_usd,
                constituent.psa10_population,
                constituent.market_cap_usd,
                constituent.metric_status
            FROM market_index_constituent AS constituent
            JOIN market_index_snapshot AS snapshot
              ON snapshot.id = constituent.index_snapshot_id
             AND snapshot.evaluation_id = %s
            LEFT JOIN catalog_variant_alias AS alias
              ON alias.duplicate_variant_id = constituent.variant_id
            WHERE COALESCE(alias.canonical_variant_id, constituent.variant_id)
                  IN ({variant_marks})
            ORDER BY snapshot.index_code, constituent.rank_position
            """,
            [market_evaluation_id, *variant_ids],
        )
    image_rows = _fetch(
        connection,
        f"""
        SELECT
            COALESCE(alias.canonical_variant_id, asset.variant_id) AS variant_id,
            asset.variant_id AS asset_variant_id,
            asset.id AS asset_id,
            asset.image_kind,
            asset.content_sha256,
            asset.private_object_key,
            asset.mime_type,
            asset.width_px,
            asset.height_px,
            asset.source_version_sha256,
            asset.captured_at,
            qc.id AS qc_id,
            qc.semantic_match_status,
            qc.card_number_match,
            qc.language_match,
            qc.tcg_match,
            qc.raw_front_confirmed,
            qc.public_allowed,
            qc.rejection_reason,
            qc.checked_at,
            qc.qc_version,
            pointer.source_path,
            pointer.public_allowed AS pointer_public_allowed,
            approval.image_asset_id AS approval_image_asset_id,
            approval.variant_id AS approval_variant_id,
            approval.content_sha256 AS approval_content_sha256,
            approval.source_version_sha256 AS approval_source_version_sha256,
            approval.canonical_printing_sha256 AS approval_printing_sha256,
            approval.expected_language AS approval_expected_language,
            approval.binding_sha256 AS approval_binding_sha256,
            approval.decision_code_sha256 AS approval_decision_code_sha256,
            image_printing.canonical_printing_sha256
                AS current_image_printing_sha256,
            image_printing.card_language AS current_image_printing_language,
            image_printing.identity_status AS current_image_printing_status,
            rejection.content_sha256 AS registered_rejection_content_sha256
        FROM market_image_asset AS asset
        LEFT JOIN catalog_variant_alias AS alias
          ON alias.duplicate_variant_id = asset.variant_id
        LEFT JOIN market_image_qc AS qc
          ON qc.image_asset_id = asset.id
        LEFT JOIN market_image_source_pointer AS pointer
          ON pointer.variant_id = asset.variant_id
         AND pointer.image_kind = asset.image_kind
         AND pointer.source_version_sha256 = asset.source_version_sha256
        LEFT JOIN market_image_review_approval AS approval
          ON approval.image_asset_id=asset.id
        LEFT JOIN catalog_printing_identity AS image_printing
          ON image_printing.variant_id=asset.variant_id
        LEFT JOIN market_image_rejection_registry AS rejection
          ON rejection.variant_id=asset.variant_id
         AND rejection.content_sha256=asset.content_sha256
        WHERE COALESCE(alias.canonical_variant_id, asset.variant_id)
              IN ({variant_marks})
        ORDER BY asset.variant_id, asset.captured_at, asset.id, qc.checked_at, qc.id
        """,
        variant_ids,
    )
    return {
        "catalogByOpaque": catalog_by_opaque,
        "sourceIdentitiesByVariant": _group(source_rows, "variant_id"),
        "pricesByVariant": _group(price_rows, "variant_id"),
        "salesByVariant": _group(sale_rows, "variant_id"),
        "indexByVariant": _group(index_rows, "variant_id"),
        "imagesByVariant": _group(image_rows, "variant_id"),
        "marketEvaluationId": market_evaluation_id,
        "formalVariantIds": sorted(formal_variant_ids),
        "formalUniverseLockSha256": formal_lock_sha256,
        "rowCounts": {
            "catalog": len(catalog_rows),
            "sourceIdentities": len(source_rows),
            "prices": len(price_rows),
            "sales": len(sale_rows),
            "indexConstituents": len(index_rows),
            "imageRows": len(image_rows),
        },
    }


def build_audit_input(
    connection: Any,
) -> tuple[dict[str, Any], dict[str, int], str, dict[str, Any], str]:
    """Build the QC population from the universe authority's discovery rows.

    A missing canonical printing must not make the audit population disappear:
    it is exactly the gap this report needs to expose.  Selection therefore
    reuses ``latest_exact_population_rows`` (the deterministic alias-resolved
    GemRate authority query), audits every POP >=971 row, and separately records
    how many could enter the stricter formal universe.
    """

    rows = latest_exact_population_rows(connection)
    discovery_rows = [
        row for row in rows if int(row["top_grade_population"]) >= 971
    ]
    printing_rows = [
        (row, resolved)
        for row in discovery_rows
        if (resolved := _printing_row_identity(row)) is not None
    ]
    printing_keys = Counter(resolved[0] for _row, resolved in printing_rows)
    printing_hashes = Counter(
        str(resolved[1]["canonicalPrintingSha256"])
        for _row, resolved in printing_rows
    )
    formal_ids = {
        int(row["resolved_variant_id"])
        for row, resolved in printing_rows
        if printing_keys[resolved[0]] == 1
        and printing_hashes[str(resolved[1]["canonicalPrintingSha256"])] == 1
    }

    def card(row: Mapping[str, Any]) -> dict[str, Any]:
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
            "populationAsOf": iso_utc(parse_time(row["effective_at"])),
            "populationObservedDate": str(row["observed_date"])[:10],
            "populationPayloadSha256": str(row["payload_sha256"]),
            "populationSourceState": "gemrate_exact",
            "populationEstimated": False,
            "formalUniverseEligible": int(row["resolved_variant_id"]) in formal_ids,
            "rankMemberships": {},
        }

    qualified = [
        card(row)
        for row in discovery_rows
        if int(row["top_grade_population"]) >= 1000
    ]
    monitoring = [
        {
            **card(row),
            "monitoringState": "pre_entry_population_971_999",
            "collectionCadence": "daily",
            "reasons": ["population_971_999"],
        }
        for row in discovery_rows
        if 971 <= int(row["top_grade_population"]) <= 999
    ]
    document: dict[str, Any] = {
        "schemaVersion": "qc-discovery-v1",
        "authority": {
            "database": CANONICAL_DATABASE,
            "selection": "universe_authority.latest_exact_population_rows",
            "population": "gemrate_psa10_exact_non_estimated",
            "aliasesResolved": True,
        },
        "cards": qualified,
        "monitoringCandidates": monitoring,
    }
    input_hash = sha256_bytes(canonical_json_bytes(document))
    counts = {
        "discoveryEvidenceCount": len(rows),
        "auditedCandidates": len(discovery_rows),
        "populationQualified": len(qualified),
        "populationMonitoring": len(monitoring),
        "formalEligible": len(formal_ids),
        "formalQualified": sum(
            1
            for row in discovery_rows
            if int(row["resolved_variant_id"]) in formal_ids
            and int(row["top_grade_population"]) >= 1000
        ),
    }
    formal_candidate = build_formal_universe_candidate(connection)
    formal_lock_sha256 = active_universe_lock_hash(formal_candidate)
    return document, counts, input_hash, formal_candidate, formal_lock_sha256


def source_bound(
    rows: Sequence[Mapping[str, Any]],
    accepted_codes: Iterable[str],
    *,
    external_entity_id: str | None = None,
) -> bool:
    accepted = {value.casefold() for value in accepted_codes}
    return any(
        str(row.get("source_code") or "").casefold() in accepted
        and str(row.get("match_status") or "").casefold() == "exact"
        and (
            external_entity_id is None
            or str(row.get("external_entity_id") or "") == external_entity_id
        )
        for row in rows
    )


def normalized_sale_external_id(
    source_code: Any,
    external_entity_id: Any,
) -> str | None:
    """Return only provider-level card IDs that can bind a sale exactly."""

    source = str(source_code or "").strip().casefold()
    external_id = str(external_entity_id or "").strip()
    if source in SNK_SALE_SOURCES:
        if not re.fullmatch(r"[0-9]+", external_id):
            return None
        normalized = str(int(external_id))
        return normalized if normalized != "0" else None
    if source == "ebay":
        normalized = external_id.casefold()
        # PriceCharting ``pc:*`` and numeric eBay listing IDs are transaction
        # evidence, not the stable G10 altxyz card UUID contract.
        return normalized if EBAY_CARD_ID_RE.fullmatch(normalized) else None
    return None


def sale_source_bound(
    source_rows: Sequence[Mapping[str, Any]],
    sale: Mapping[str, Any],
) -> bool:
    """Require the sale's own provider card ID to have an exact catalog owner."""

    sale_source = str(sale.get("source_code") or "").strip().casefold()
    raw_external = str(sale.get("external_entity_id") or "").strip()
    sale_external_id = normalized_sale_external_id(
        sale_source,
        sale.get("external_entity_id"),
    )
    accepted_sources = SALE_IDENTITY_SOURCES.get(sale_source)
    if accepted_sources is None:
        return False
    # Primary path: UUID ebay identity or numeric SNK sale identity.
    if sale_external_id is not None:
        for identity in source_rows:
            identity_source = str(identity.get("source_code") or "").strip().casefold()
            if identity_source not in accepted_sources:
                continue
            if str(identity.get("match_status") or "").strip().casefold() != "exact":
                continue
            identity_external_id = normalized_sale_external_id(
                sale_source,
                identity.get("external_entity_id"),
            )
            if identity_external_id == sale_external_id:
                return True
    # G10 dual-tree path (pipelines/g10_ebay_ingest.py): eBay PSA comps live under
    # data/cards/snkrdunk/{apparelId}/ebay_PSA_10.json and are written as source_code=ebay
    # with external_entity_id=apparelId. Bind only when an exact snkrdunk identity owns
    # that same numeric apparel id (no name matching).
    if sale_source == "ebay" and re.fullmatch(r"[0-9]+", raw_external):
        snk_id = str(int(raw_external))
        if snk_id != "0":
            for identity in source_rows:
                identity_source = str(identity.get("source_code") or "").strip().casefold()
                if identity_source not in {"snk", "snkrdunk"}:
                    continue
                if str(identity.get("match_status") or "").strip().casefold() != "exact":
                    continue
                identity_external = str(identity.get("external_entity_id") or "").strip()
                if re.fullmatch(r"[0-9]+", identity_external) and str(int(identity_external)) == snk_id:
                    return True
    # PriceCharting sold comps are written as source_code=ebay with external_entity_id
    # ``pc:{pcId}`` (see c11_pc_sold_ingest). Bind only when an exact pricecharting
    # identity owns that same numeric PC id (not a name match; missing≠0).
    if sale_source == "ebay":
        m = re.fullmatch(r"pc:([0-9]+)", raw_external, flags=re.IGNORECASE)
        if m is not None:
            pc_id = str(int(m.group(1)))
            if pc_id != "0":
                for identity in source_rows:
                    identity_source = str(identity.get("source_code") or "").strip().casefold()
                    if identity_source != "pricecharting":
                        continue
                    if str(identity.get("match_status") or "").strip().casefold() != "exact":
                        continue
                    identity_external = str(identity.get("external_entity_id") or "").strip()
                    if re.fullmatch(r"[0-9]+", identity_external) and str(int(identity_external)) == pc_id:
                        return True
    return False


def latest_qc_assets(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    assets: dict[int, dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        asset_id = int(row["asset_id"])
        current = assets.setdefault(
            asset_id,
            {
                key: row.get(key)
                for key in (
                    "asset_id",
                    "asset_variant_id",
                    "image_kind",
                    "content_sha256",
                    "private_object_key",
                    "mime_type",
                    "width_px",
                    "height_px",
                    "source_version_sha256",
                    "captured_at",
                    "source_path",
                    "pointer_public_allowed",
                    "approval_image_asset_id",
                    "approval_variant_id",
                    "approval_content_sha256",
                    "approval_source_version_sha256",
                    "approval_printing_sha256",
                    "approval_expected_language",
                    "approval_binding_sha256",
                    "approval_decision_code_sha256",
                    "current_image_printing_sha256",
                    "current_image_printing_language",
                    "current_image_printing_status",
                    "registered_rejection_content_sha256",
                )
            },
        )
        if row.get("qc_id") is None:
            continue
        candidate_time = parse_time(row.get("checked_at")) or datetime.min.replace(
            tzinfo=timezone.utc
        )
        current_qc = current.get("qc")
        current_time = (
            parse_time(current_qc.get("checked_at"))
            if isinstance(current_qc, Mapping)
            else None
        )
        if current_time is None or (candidate_time, int(row["qc_id"])) > (
            current_time,
            int(current_qc["qc_id"]),
        ):
            current["qc"] = {
                key: row.get(key)
                for key in (
                    "qc_id",
                    "semantic_match_status",
                    "card_number_match",
                    "language_match",
                    "tcg_match",
                    "raw_front_confirmed",
                    "public_allowed",
                    "rejection_reason",
                    "checked_at",
                    "qc_version",
                )
            }
    return sorted(
        assets.values(),
        key=lambda row: (
            parse_time(row.get("captured_at"))
            or datetime.min.replace(tzinfo=timezone.utc),
            int(row["asset_id"]),
        ),
        reverse=True,
    )


def resolve_asset_path(asset: Mapping[str, Any], assets_root: Path) -> Path | None:
    content_hash = str(asset.get("content_sha256") or "")
    key = str(asset.get("private_object_key") or "").strip()
    candidates: list[Path] = []
    if key:
        key_path = Path(key)
        if key_path.is_absolute():
            candidates.append(key_path)
        elif key.startswith("/market-assets/"):
            candidates.append(assets_root / Path(key).name)
        else:
            candidates.extend((ROOT / key_path, assets_root / key_path.name))
    if SHA256_RE.fullmatch(content_hash):
        candidates.append(assets_root / f"{content_hash}.webp")
    for path in candidates:
        if path.is_file():
            return path.resolve()
    return candidates[0].resolve() if candidates else None


def default_sample_scan(path: Path, card_id: str) -> str | None:
    try:
        assert_raw_bytes_not_sample(path.read_bytes(), context=card_id)
    except SampleImageRejected:
        return "image_sample_or_placeholder"
    except Exception:
        return "image_sample_scan_failed"
    return None


def image_evidence(
    rows: Sequence[Mapping[str, Any]],
    *,
    card_id: str,
    tcg_code: str,
    as_of: datetime,
    assets_root: Path,
    sample_scan: Callable[[Path, str], str | None],
    geometry_scan: Callable[[Path], Mapping[str, Any]],
    release_profile_id: str = "strict-v1",
    hash_owners: Mapping[str, Sequence[Any]] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    if release_profile_id == "relaxed-launch-v1":
        receipt = select_release_image(
            latest_qc_assets(rows),
            profile=release_profile_id,
            assets_root=assets_root,
            card_id=card_id,
            tcg_code=tcg_code,
            hash_owners=hash_owners,
            geometry_inspector=geometry_scan,
            sample_scanner=sample_scan,
        )
        chosen = receipt.get("chosen")
        if not isinstance(chosen, Mapping):
            return {
                "status": "failed",
                "contentSha256": None,
                "releaseImageReceipt": receipt,
            }, ["image_release_candidate_missing"]
        hard_blockers = chosen.get("hardBlockers")
        if not isinstance(hard_blockers, list):
            raise CanonicalDbQcError("relaxed image receipt has invalid hard blockers")
        return {
            "status": "passed" if not hard_blockers else "failed",
            "assetId": chosen.get("assetId"),
            "contentSha256": chosen.get("contentSha256"),
            "releaseImageReceipt": receipt,
        }, [str(value) for value in hard_blockers]

    assets = [
        row
        for row in latest_qc_assets(rows)
        if str(row.get("image_kind") or "") == "raw_front"
    ]
    if not assets:
        return {"status": "missing", "contentSha256": None}, [
            "image_raw_front_missing"
        ]

    evaluated: list[tuple[dict[str, Any], list[str]]] = []
    for asset in assets:
        blockers: list[str] = []
        content_hash = str(asset.get("content_sha256") or "")
        if not SHA256_RE.fullmatch(content_hash):
            blockers.append("image_hash_invalid")
        path = resolve_asset_path(asset, assets_root)
        if path is None or not path.is_file():
            blockers.append("image_asset_missing")
        elif SHA256_RE.fullmatch(content_hash) and sha256_file(path) != content_hash:
            blockers.append("image_asset_hash_mismatch")
        geometry: dict[str, Any] | None = None
        source_policy = classify_source(
            str(asset.get("source_path") or ""),
            tcg_code=tcg_code,
            width_px=asset.get("width_px"),
            height_px=asset.get("height_px"),
        )
        if source_policy["status"] == "reject":
            blockers.append("image_source_known_sample")
        if not str(asset.get("source_path") or "").strip():
            blockers.append("image_source_pointer_missing")
        elif not bool(asset.get("pointer_public_allowed")):
            blockers.append("image_source_pointer_disabled")

        if str(asset.get("registered_rejection_content_sha256") or "") == content_hash:
            blockers.append("image_historically_human_rejected")

        approval_asset_id = asset.get("approval_image_asset_id")
        if approval_asset_id is None:
            blockers.append("image_review_binding_missing")
        else:
            asset_id = int(asset["asset_id"])
            asset_variant_id = int(
                asset.get("asset_variant_id") or asset.get("variant_id") or 0
            )
            source_version_sha256 = str(
                asset.get("source_version_sha256") or ""
            ).strip().lower()
            approval_printing_sha256 = str(
                asset.get("approval_printing_sha256") or ""
            ).strip().lower()
            approval_language = str(
                asset.get("approval_expected_language") or ""
            ).strip().lower()
            expected_binding_sha256 = hashlib.sha256(
                "|".join(
                    (
                        str(asset_id),
                        str(asset_variant_id),
                        content_hash,
                        source_version_sha256,
                        approval_printing_sha256,
                        approval_language,
                    )
                ).encode("utf-8")
            ).hexdigest()
            if (
                int(approval_asset_id) != asset_id
                or int(asset.get("approval_variant_id") or 0)
                != asset_variant_id
                or str(asset.get("approval_content_sha256") or "").strip().lower()
                != content_hash
                or str(
                    asset.get("approval_source_version_sha256") or ""
                ).strip().lower()
                != source_version_sha256
                or not SHA256_RE.fullmatch(approval_printing_sha256)
                or not approval_language
                or str(
                    asset.get("current_image_printing_status") or ""
                ).strip().lower()
                != "canonical"
                or str(
                    asset.get("current_image_printing_sha256") or ""
                ).strip().lower()
                != approval_printing_sha256
                or str(
                    asset.get("current_image_printing_language") or ""
                ).strip().lower()
                != approval_language
                or str(asset.get("approval_binding_sha256") or "").strip().lower()
                != expected_binding_sha256
                or not SHA256_RE.fullmatch(
                    str(
                        asset.get("approval_decision_code_sha256") or ""
                    ).strip().lower()
                )
            ):
                blockers.append("image_review_binding_drift")
        if path is not None and path.is_file():
            try:
                geometry = dict(geometry_scan(path))
            except Exception:
                blockers.append("image_geometry_scan_failed")
            else:
                if str(geometry.get("status") or "") != "passed":
                    blockers.append("image_canvas_geometry_invalid")

        qc = asset.get("qc")
        if not isinstance(qc, Mapping):
            blockers.append("image_qc_missing")
        else:
            checked_at = parse_time(qc.get("checked_at"))
            if checked_at is not None and checked_at > as_of:
                blockers.append("image_qc_future_dated")
            if str(qc.get("qc_version") or "") != "human-review-v2":
                blockers.append("image_qc_version_not_current")
            if not bool(qc.get("public_allowed")):
                blockers.append("image_not_public_allowed")
            if (
                str(qc.get("semantic_match_status") or "")
                != "human_or_vision_confirmed"
            ):
                blockers.append("image_not_human_or_vision_confirmed")
            if not bool(qc.get("card_number_match")):
                blockers.append("image_card_number_mismatch")
            if not bool(qc.get("language_match")):
                blockers.append("image_language_match_failed")
            if not bool(qc.get("tcg_match")):
                blockers.append("image_tcg_mismatch")
            if not bool(qc.get("raw_front_confirmed")):
                blockers.append("image_not_raw_front")
            rejection = str(qc.get("rejection_reason") or "").casefold()
            if any(
                token in rejection
                for token in ("sample", "placeholder", "now designing")
            ):
                blockers.append("image_sample_or_placeholder")

        key_text = str(asset.get("private_object_key") or "").casefold()
        if any(token in key_text for token in ("sample", "placeholder", "now-designing")):
            blockers.append("image_sample_or_placeholder")
        blockers = sorted(set(blockers))
        if (
            not blockers
            and path is not None
            and source_policy["status"] == "scan_required"
        ):
            sample_blocker = sample_scan(path, card_id)
            if sample_blocker:
                blockers.append(sample_blocker)
        evidence = {
            "status": "passed" if not blockers else "failed",
            "assetId": int(asset["asset_id"]),
            "contentSha256": content_hash or None,
            "semanticMatchStatus": (
                str(qc.get("semantic_match_status") or "")
                if isinstance(qc, Mapping)
                else None
            ),
            "qcVersion": (
                str(qc.get("qc_version") or "")
                if isinstance(qc, Mapping)
                else None
            ),
            "checkedAt": (
                iso_utc(parse_time(qc.get("checked_at")))
                if isinstance(qc, Mapping)
                and parse_time(qc.get("checked_at")) is not None
                else None
            ),
            "geometry": geometry,
            "sourcePolicy": source_policy,
        }
        evaluated.append((evidence, sorted(set(blockers))))
        if not blockers:
            return evidence, []
    return evaluated[0]


def _rank_key(row: Mapping[str, Any]) -> tuple[datetime, int]:
    return (
        parse_time(row.get("effective_at"))
        or datetime.min.replace(tzinfo=timezone.utc),
        -int(row.get("rank_position") or 10**9),
    )


def _price_key(row: Mapping[str, Any]) -> tuple[datetime, int, int]:
    return (
        parse_time(row.get("effective_at"))
        or datetime.min.replace(tzinfo=timezone.utc),
        -int(row.get("source_priority") or 100),
        int(row.get("id") or 0),
    )


# DADDY 2026-07-31: explicit PC PSA10 > PC/eBay sold median > SNK; never g10_kline.
SNK_PRICE_FAMILY = frozenset({"snk_psa10", "snk", "snkrdunk"})
EBAY_PRICE_FAMILY = frozenset({"ebay"})
PC_PRICE_FAMILY = frozenset({"pricecharting"})
G10_PRICE_FAMILY = EBAY_PRICE_FAMILY  # compatibility alias: "G10 path" means eBay data, not kline


def _best_family_price(
    rows: Sequence[Mapping[str, Any]],
    family: frozenset[str],
) -> Mapping[str, Any] | None:
    cand = [
        row
        for row in rows
        if str(row.get("source_code") or "").casefold() in family
    ]
    if not cand:
        return None
    return max(cand, key=_price_key)


def price_anchor_at(row: Mapping[str, Any]) -> datetime | None:
    """Source date is the PC chart anchor; fetch time remains current freshness.

    PriceCharting's explicit PSA10 guide can be fetched today while its last
    chart bar is dated earlier.  ``observed_date`` is that immutable chart-bar
    date, so use it for the 30-day comparison.  Other sources retain their
    precise effective timestamp.
    """
    if str(row.get("source_code") or "").casefold() in PC_PRICE_FAMILY:
        value = row.get("observed_date")
        if isinstance(value, datetime):
            return parse_time(value)
        try:
            return datetime.combine(date.fromisoformat(str(value)), datetime.min.time()).replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None
    return parse_time(row.get("effective_at"))


def select_display_exact_price(
    usable_prices: Sequence[Mapping[str, Any]],
    source_rows: Sequence[Mapping[str, Any]],
    *,
    as_of: datetime | None = None,
    price_max_age: timedelta = PRICE_MAX_AGE,
    price_selection: str = "arithmetic_mean_of_fresh_authority_families",
) -> tuple[Mapping[str, Any] | None, dict[str, Any]]:
    """Pick the primary exact price and report independent cross-source QC."""

    bound: list[Mapping[str, Any]] = []
    for row in usable_prices:
        src = str(row.get("source_code") or "").casefold()
        status = str(row.get("metric_status") or "ready").casefold()
        if status and status != "ready":
            continue
        accepted = PRICE_IDENTITY_SOURCES.get(src, frozenset({src}))
        if source_bound(source_rows, accepted):
            bound.append(row)
    snk = _best_family_price(bound, SNK_PRICE_FAMILY)
    g10 = _best_family_price(bound, G10_PRICE_FAMILY)
    pc = _best_family_price(bound, PC_PRICE_FAMILY)
    meta: dict[str, Any] = {
        "mode": None,
        "snkUsd": None,
        "g10Usd": None,
        "pricechartingUsd": None,
        "snkSource": None,
        "g10Source": None,
        "pricechartingSource": None,
        "authorityMeanUsd": None,
        "authorityMeanSources": [],
        "priority": "pricecharting_then_ebay_then_snk",
        "releasePriceSelection": price_selection,
        "crossSourceQc": {
            "status": "exact_psa10_price_insufficient_independent_sources",
            "requiredSources": 1,
            "maxRatio": str(PRICE_CROSS_SOURCE_MAX_RATIO),
            "freshnessHours": int(price_max_age.total_seconds() // 3600),
            "freshSourceCount": 0,
            "freshSources": [],
            "observedRatio": None,
        },
    }
    if snk is not None:
        meta["snkUsd"] = str(decimal_value(snk.get("price_usd")))
        meta["snkSource"] = str(snk.get("source_code") or "").casefold()
    if g10 is not None:
        meta["g10Usd"] = str(decimal_value(g10.get("price_usd")))
        meta["g10Source"] = str(g10.get("source_code") or "").casefold()
    if pc is not None:
        meta["pricechartingUsd"] = str(decimal_value(pc.get("price_usd")))
        meta["pricechartingSource"] = str(pc.get("source_code") or "").casefold()

    latest_by_family = {
        "pricecharting": pc,
        "ebay": g10,
        "snk": snk,
    }
    fresh_values: dict[str, Decimal] = {}
    if as_of is not None:
        for family, row in latest_by_family.items():
            if row is None:
                continue
            effective_at = parse_time(row.get("effective_at"))
            value = decimal_value(row.get("price_usd"))
            if (
                effective_at is None
                or effective_at > as_of
                or as_of - effective_at > price_max_age
                or value is None
                or value <= 0
            ):
                continue
            fresh_values[family] = value
    cross_source = meta["crossSourceQc"]
    cross_source["freshSourceCount"] = len(fresh_values)
    cross_source["freshSources"] = sorted(fresh_values)
    if fresh_values:
        cross_source["status"] = "confirmed"
    if len(fresh_values) >= 2:
        observed_ratio = max(fresh_values.values()) / min(fresh_values.values())
        cross_source["observedRatio"] = str(
            observed_ratio.quantize(Decimal("0.000001"))
        )
        cross_source["status"] = (
            "exact_psa10_price_source_spread_gt_2x"
            if observed_ratio > PRICE_CROSS_SOURCE_MAX_RATIO
            else "confirmed"
        )
    if (
        cross_source["status"] == "confirmed"
        and price_selection == "arithmetic_mean_of_fresh_authority_families"
    ):
        mean_value = (
            sum(fresh_values.values(), Decimal("0"))
            / Decimal(len(fresh_values))
        ).quantize(Decimal("0.000001"))
        meta["authorityMeanUsd"] = str(mean_value)
        meta["authorityMeanSources"] = sorted(fresh_values)
        for family in ("pricecharting", "ebay", "snk"):
            if family not in fresh_values:
                continue
            selected = dict(latest_by_family[family])
            selected["price_usd"] = mean_value
            meta["mode"] = (
                "single_authority"
                if len(fresh_values) == 1
                else "authority_family_mean"
            )
            return selected, meta

    if pc is not None:
        pc_v = decimal_value(pc.get("price_usd"))
        if pc_v is not None and pc_v > 0:
            meta["mode"] = "priority_pricecharting_explicit_psa10"
            return pc, meta

    if g10 is not None:
        g10_v = decimal_value(g10.get("price_usd"))
        if g10_v is not None and g10_v > 0:
            meta["mode"] = "priority_ebay"
            return g10, meta
    if snk is not None:
        snk_v = decimal_value(snk.get("price_usd"))
        if snk_v is not None and snk_v > 0:
            meta["mode"] = "priority_snk_no_g10"
            return snk, meta
    meta["mode"] = "none"
    return None, meta


def _formula_matches(price: Any, population: Any, market_cap: Any) -> bool:
    price_value = decimal_value(price)
    population_value = decimal_value(population)
    cap_value = decimal_value(market_cap)
    if price_value is None or population_value is None or cap_value is None:
        return False
    return abs((price_value * population_value) - cap_value) < Decimal("0.01")


def _printing_key(row: Mapping[str, Any]) -> tuple[str, ...] | None:
    required = (
        row.get("printing_tcg_code"),
        row.get("printing_card_language"),
        row.get("printing_set_name"),
        row.get("printing_collector_number"),
        row.get("edition_code"),
        row.get("parallel_code"),
        row.get("finish_code"),
    )
    if any(value is None or not str(value).strip() for value in required):
        return None
    return tuple(str(value).strip().casefold() for value in required)


def audit_candidate(
    candidate: Mapping[str, Any],
    facts: Mapping[str, Any],
    *,
    universe_hash: str,
    run_id: str,
    as_of: datetime,
    assets_root: Path,
    sample_scan: Callable[[Path, str], str | None] = default_sample_scan,
    geometry_scan: Callable[[Path], Mapping[str, Any]] = inspect_image_geometry,
    authority_counts: Mapping[str, int] | None = None,
    release_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate every schema-5 qualified and monitoring member."""

    qualified = candidate.get("cards")
    monitoring = candidate.get("monitoringCandidates")
    if not isinstance(qualified, list) or not isinstance(monitoring, list):
        raise CanonicalDbQcError("schema-5 universe candidate is invalid")
    if not RUN_ID_RE.fullmatch(run_id):
        raise CanonicalDbQcError("run_id must be 1-96 safe filename characters")
    as_of = as_of.astimezone(timezone.utc)

    release = resolved_release_profile(release_profile)
    release_profile_id = str(release["releaseProfile"])
    policy = release["policy"]
    if not isinstance(policy, Mapping):
        raise CanonicalDbQcError("release profile policy is invalid")
    price_max_age = timedelta(hours=_policy_int(policy, "lastGoodMaximumHours"))
    sales_minimum = _policy_int(policy, "trackedPsa10Sales30dMinimumInclusive")
    catalog_by_opaque = facts.get("catalogByOpaque") or {}
    source_by_variant = facts.get("sourceIdentitiesByVariant") or {}
    prices_by_variant = facts.get("pricesByVariant") or {}
    sales_by_variant = facts.get("salesByVariant") or {}
    indexes_by_variant = facts.get("indexByVariant") or {}
    images_by_variant = facts.get("imagesByVariant") or {}
    raw_formal_variant_ids = facts.get("formalVariantIds")
    formal_variant_ids = {
        int(value)
        for value in raw_formal_variant_ids or ()
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
    }
    # Unit fixtures predating formal-cohort binding model one complete formal
    # card and omit the explicit field. Runtime facts always provide it.
    if raw_formal_variant_ids is None:
        formal_variant_ids = {
            int(row["variant_id"])
            for row in catalog_by_opaque.values()
            if isinstance(row, Mapping)
        }

    image_hash_owners: dict[str, list[dict[str, str]]] = defaultdict(list)
    for opaque_id, catalog_row in catalog_by_opaque.items():
        if not isinstance(catalog_row, Mapping):
            continue
        try:
            catalog_variant_id = int(catalog_row["variant_id"])
        except (KeyError, TypeError, ValueError):
            continue
        for image_row in images_by_variant.get(catalog_variant_id, []):
            if not isinstance(image_row, Mapping):
                continue
            image_hash = str(image_row.get("content_sha256") or "").casefold()
            if SHA256_RE.fullmatch(image_hash):
                image_hash_owners[image_hash].append(
                    {
                        "cardId": str(opaque_id),
                        "tcg": str(catalog_row.get("tcg_code") or ""),
                    }
                )

    card_rows: list[dict[str, Any]] = []
    for segment, raw_cards in (("qualified", qualified), ("monitoring", monitoring)):
        for raw_card in raw_cards:
            if not isinstance(raw_card, Mapping):
                raise CanonicalDbQcError("universe candidate contains a non-object card")
            card = dict(raw_card)
            card_id = str(card.get("pokedexId") or "")
            catalog = catalog_by_opaque.get(card_id)
            blockers: list[str] = []
            warnings: list[str] = []
            evidence: dict[str, Any] = {}
            variant_id: int | None = None
            formal_universe_eligible = False
            printing_key: tuple[str, ...] | None = None
            if not isinstance(catalog, Mapping):
                blockers.append("catalog_identity_missing")
                source_rows: list[Mapping[str, Any]] = []
                price_rows: list[Mapping[str, Any]] = []
                sale_rows: list[Mapping[str, Any]] = []
                index_rows: list[Mapping[str, Any]] = []
                image_rows: list[Mapping[str, Any]] = []
            else:
                variant_id = int(catalog["variant_id"])
                formal_universe_eligible = variant_id in formal_variant_ids
                source_rows = list(source_by_variant.get(variant_id, []))
                price_rows = list(prices_by_variant.get(variant_id, []))
                sale_rows = list(sales_by_variant.get(variant_id, []))
                index_rows = list(indexes_by_variant.get(variant_id, []))
                image_rows = list(images_by_variant.get(variant_id, []))
                # POLICY_EXCLUSIVE_RANK_IGNORE: flag exclusive products as warning only
                # (ranking demotes them; do NOT hard-fail QC solely for exclusive).
                try:
                    from exclusive_product import is_exclusive_product
                except ImportError:
                    is_exclusive_product = lambda *a, **k: False  # type: ignore
                if is_exclusive_product(
                    catalog.get("canonical_name"), catalog.get("set_name")
                ):
                    warnings.append("exclusive_product_rank_ignored")
                    evidence["exclusiveProduct"] = True
                identity_status, printing_key, identity_evidence, identity_blockers = release_identity(
                    catalog, source_rows, card, policy
                )
                blockers.extend(identity_blockers)
                evidence["identity"] = {
                    **identity_evidence,
                    "formalUniverseEligible": formal_universe_eligible,
                }

            if release_profile_id == "strict-v1":
                gemrate_external_id = str(card.get("gemrateId") or "")
                exact_gemrate = [
                    row
                    for row in source_rows
                    if str(row.get("source_code") or "").casefold() == "gemrate"
                    and str(row.get("match_status") or "").casefold() == "exact"
                ]
                if not source_bound(
                    source_rows,
                    {"gemrate"},
                    external_entity_id=gemrate_external_id,
                ):
                    blockers.append("gemrate_identity_not_exact")
                if len(
                    {
                        str(row.get("external_entity_id") or "")
                        for row in exact_gemrate
                    }
                ) > 1:
                    blockers.append("gemrate_identity_ambiguous")

            population = card.get("populationPsa10")
            population_at = parse_time(card.get("populationAsOf"))
            if (
                not isinstance(population, int)
                or isinstance(population, bool)
                or bool(card.get("populationEstimated"))
                or str(card.get("populationSourceState") or "") != "gemrate_exact"
            ):
                blockers.append("population_not_exact_gemrate_psa10")
            elif segment == "qualified" and population < 1000:
                blockers.append("population_below_qualified_threshold")
            elif segment == "monitoring" and not 971 <= population <= 999:
                blockers.append("population_outside_monitoring_band")
            if population_at is None:
                blockers.append("population_timestamp_invalid")
            elif population_at > as_of:
                blockers.append("population_future_dated")
            elif as_of - population_at > POPULATION_MAX_AGE:
                blockers.append("population_stale")
            if not SHA256_RE.fullmatch(str(card.get("populationPayloadSha256") or "")):
                blockers.append("population_payload_hash_invalid")
            evidence["population"] = {
                "value": population,
                "asOf": iso_utc(population_at) if population_at else None,
                "estimated": bool(card.get("populationEstimated")),
                "authority": "gemrate",
            }

            future_prices = [
                row
                for row in price_rows
                if (parse_time(row.get("effective_at")) or as_of) > as_of
            ]
            if future_prices:
                blockers.append("future_price_metric")
            usable_prices = [
                row
                for row in price_rows
                if str(row.get("source_code") or "").casefold()
                in EXACT_PRICE_SOURCES
                and (decimal_value(row.get("price_usd")) or Decimal("0")) > 0
                and (parse_time(row.get("effective_at")) or datetime.max.replace(
                    tzinfo=timezone.utc
                ))
                <= as_of
            ]
            # One exact authority passes; multiple inliers use their arithmetic mean.
            current_price, price_blend_meta = select_display_exact_price(
                usable_prices,
                source_rows,
                as_of=as_of,
                price_max_age=price_max_age,
                price_selection=str(policy.get("priceSelection") or ""),
            )
            cross_source_status = str(
                price_blend_meta.get("crossSourceQc", {}).get("status") or ""
            )
            if cross_source_status == "exact_psa10_price_source_spread_gt_2x":
                if policy.get("priceSpreadAction") == "warning":
                    warnings.append(cross_source_status)
                else:
                    blockers.append(cross_source_status)
            elif cross_source_status == "exact_psa10_price_insufficient_independent_sources":
                # Strict keeps its legacy independent-source block.  The
                # relaxed profile explicitly allows the configured priority
                # source to stand on its own.
                if release_profile_id == "strict-v1":
                    blockers.append(cross_source_status)
            if current_price is None:
                blockers.append("exact_psa10_price_missing")
                # if raw usable existed but none identity-bound, surface ownership
                if usable_prices:
                    blockers.append("price_source_identity_not_exact")
                current_price_value = None
                current_price_at = None
                current_price_source = None
            else:
                current_price_value = decimal_value(current_price.get("price_usd"))
                current_price_at = parse_time(current_price.get("effective_at"))
                current_price_source = str(
                    current_price.get("source_code") or ""
                ).casefold()
                if str(current_price.get("metric_status") or "") != "ready":
                    blockers.append("exact_psa10_price_not_ready")
                if current_price_at is None or as_of - current_price_at > price_max_age:
                    blockers.append("exact_psa10_price_stale")
                # Identity already enforced per-leg inside select_display_exact_price.

                anchor_target = (as_of - timedelta(days=30)).date()
                # Anchor prefer same family as primary leg
                anchor_src = current_price_source
                if current_price_source in G10_PRICE_FAMILY:
                    anchor_src = str(price_blend_meta.get("g10Source") or current_price_source)
                elif current_price_source in SNK_PRICE_FAMILY:
                    anchor_src = str(price_blend_meta.get("snkSource") or current_price_source)
                anchor_rows = [
                    row
                    for row in usable_prices
                    if price_anchor_at(row) is not None
                    and abs(
                        (
                            price_anchor_at(row).date()
                            - anchor_target
                        ).days
                    )
                    <= ANCHOR_TOLERANCE_DAYS
                ]
                same_method = [
                    row
                    for row in anchor_rows
                    if str(row.get("source_code") or "").casefold() == anchor_src
                    or (
                        anchor_src in SNK_PRICE_FAMILY
                        and str(row.get("source_code") or "").casefold()
                        in SNK_PRICE_FAMILY
                    )
                ]
                if same_method:
                    anchor = min(
                        same_method,
                        key=lambda row: (
                            abs(
                                (
                                    price_anchor_at(row).date()
                                    - anchor_target
                                ).days
                            ),
                            -int(row.get("id") or 0),
                        ),
                    )
                    evidence["priceAnchor"] = {
                        "source": str(anchor.get("source_code") or "").casefold(),
                        "asOf": iso_utc(price_anchor_at(anchor)),
                        "valueUsd": str(anchor.get("price_usd")),
                    }
                elif anchor_rows:
                    # A different-family historical point must never be used
                    # to calculate change for the current source.  It is a
                    # review warning, not a listing blocker: the current exact
                    # PSA 10 price remains valid and the frontend reports the
                    # affected change window as accumulating.
                    warnings.append("price_anchor_source_method_mismatch")
                else:
                    warnings.append("price_anchor_not_derivable")
            evidence["price"] = {
                "valueUsd": (
                    str(current_price_value)
                    if current_price_value is not None
                    else None
                ),
                "asOf": iso_utc(current_price_at) if current_price_at else None,
                "source": current_price_source,
                "priorityMeta": price_blend_meta,
            }

            future_sales = [
                row
                for row in sale_rows
                if (
                    parse_time(row.get("sold_at")) is not None
                    and parse_time(row.get("sold_at")) > as_of
                )
                or (
                    parse_time(row.get("fetched_at")) is not None
                    and parse_time(row.get("fetched_at")) > as_of
                )
            ]
            if future_sales:
                blockers.append("future_sale_metric")
            window_start = as_of - SALES_WINDOW
            recent_sales = [
                row
                for row in sale_rows
                if parse_time(row.get("sold_at")) is not None
                and window_start < parse_time(row.get("sold_at")) <= as_of
            ]
            exact_sales = [
                row
                for row in recent_sales
                if str(row.get("grader_code") or "").upper() == "PSA"
                and str(row.get("grade_label") or "").upper()
                in EXACT_GRADE_LABELS
                and int(row.get("quantity") or 0) == 1
            ]
            if any(
                str(row.get("grader_code") or "").upper() != "PSA"
                or str(row.get("grade_label") or "").upper()
                not in EXACT_GRADE_LABELS
                for row in recent_sales
            ):
                warnings.append("non_psa10_sale_excluded")
            if any(int(row.get("quantity") or 0) != 1 for row in recent_sales):
                warnings.append("bundle_sale_excluded")
            valid_sales = []
            unbound_sale_count = 0
            for row in exact_sales:
                row_valid = True
                timestamp_quality = str(
                    row.get("timestamp_quality") or ""
                ).strip().casefold()
                if timestamp_quality not in ACCEPTED_SALE_TIMESTAMP_QUALITIES:
                    blockers.append("sale_timestamp_quality_invalid")
                    row_valid = False
                coverage_status = str(
                    row.get("coverage_status") or ""
                ).strip().casefold()
                if coverage_status not in ACCEPTED_SALE_COVERAGE_STATUSES:
                    blockers.append("sale_coverage_status_invalid")
                    row_valid = False
                payload_sha256 = str(
                    row.get("source_payload_sha256") or ""
                ).strip().casefold()
                if not SHA256_RE.fullmatch(payload_sha256):
                    blockers.append("sale_payload_hash_invalid")
                    row_valid = False
                unit = decimal_value(row.get("unit_price_usd"))
                transaction = decimal_value(row.get("transaction_value_usd"))
                if (
                    unit is None
                    or transaction is None
                    or unit <= 0
                    or transaction <= 0
                    or unit != transaction
                ):
                    blockers.append("psa10_sale_value_invalid")
                    row_valid = False
                if not sale_source_bound(source_rows, row):
                    # Exclude the unbound row, but do not let one bad mapping
                    # erase a card that already has ten exact, usable sales.
                    # The excluded count remains private evidence for the
                    # identity retry lane; it becomes a release blocker only
                    # when it prevents this card from reaching the liquidity gate.
                    warnings.append("sale_source_identity_not_exact")
                    unbound_sale_count += 1
                    row_valid = False
                if row_valid:
                    valid_sales.append(row)
            if not valid_sales:
                blockers.append("psa10_sales_30d_missing")
            elif len(valid_sales) < sales_minimum:
                blockers.append("psa10_sales_30d_insufficient")
            if unbound_sale_count and len(valid_sales) < sales_minimum:
                blockers.append("sale_source_identity_not_exact")
            evidence["sales30d"] = {
                "purePsa10Count": len(valid_sales),
                "sourceIdentityRowsExcluded": unbound_sale_count,
                "bundleRowsExcluded": sum(
                    1 for row in recent_sales if int(row.get("quantity") or 0) != 1
                ),
                "otherGradeRowsExcluded": sum(
                    1
                    for row in recent_sales
                    if str(row.get("grader_code") or "").upper() != "PSA"
                    or str(row.get("grade_label") or "").upper()
                    not in EXACT_GRADE_LABELS
                ),
                "sources": sorted(
                    {
                        str(row.get("source_code") or "").casefold()
                        for row in valid_sales
                    }
                ),
            }

            if any(
                parse_time(row.get("effective_at")) is not None
                and parse_time(row.get("effective_at")) > as_of
                for row in index_rows
            ):
                blockers.append("future_market_cap_metric")
            current_indexes = [
                row
                for row in index_rows
                if parse_time(row.get("effective_at")) is not None
                and parse_time(row.get("effective_at")) <= as_of
            ]
            current_index = (
                max(current_indexes, key=_rank_key) if current_indexes else None
            )
            market_rank = (
                min(int(row.get("rank_position") or 10**9) for row in current_indexes)
                if current_indexes
                else None
            )
            if current_index is None:
                evidence["marketCap"] = {
                    "status": "missing" if formal_universe_eligible else "not_formal",
                    "marketRank": market_rank,
                }
                if formal_universe_eligible:
                    blockers.append("market_cap_not_materialized")
            else:
                index_at = parse_time(current_index.get("effective_at"))
                if index_at is None or as_of - index_at > INDEX_MAX_AGE:
                    blockers.append("market_cap_snapshot_stale")
                if not _formula_matches(
                    current_index.get("reference_price_usd"),
                    current_index.get("psa10_population"),
                    current_index.get("market_cap_usd"),
                ):
                    blockers.append("market_cap_formula_mismatch")
                if (
                    current_price_value is not None
                    and decimal_value(current_index.get("reference_price_usd"))
                    is not None
                    and abs(
                        decimal_value(current_index.get("reference_price_usd"))
                        - current_price_value
                    )
                    >= Decimal("0.01")
                ):
                    blockers.append("market_cap_current_price_mismatch")
                if (
                    isinstance(population, int)
                    and not isinstance(population, bool)
                    and int(current_index.get("psa10_population") or -1)
                    != population
                ):
                    blockers.append("market_cap_current_population_mismatch")
                evidence["marketCap"] = {
                    "valueUsd": str(current_index.get("market_cap_usd")),
                    "referencePriceUsd": str(
                        current_index.get("reference_price_usd")
                    ),
                    "populationPsa10": current_index.get("psa10_population"),
                    "asOf": iso_utc(index_at) if index_at else None,
                    "indexCode": str(current_index.get("index_code") or ""),
                    "marketRank": market_rank,
                }

            image, image_blockers = image_evidence(
                image_rows,
                card_id=card_id,
                tcg_code=str(card.get("tcg") or ""),
                as_of=as_of,
                assets_root=assets_root,
                sample_scan=sample_scan,
                geometry_scan=geometry_scan,
                release_profile_id=release_profile_id,
                hash_owners=image_hash_owners,
            )
            blockers.extend(image_blockers)
            evidence["image"] = image

            card_rows.append(
                {
                    "id": card_id,
                    "variantId": variant_id,
                    "tcg": str(card.get("tcg") or ""),
                    "segment": segment,
                    "marketRank": market_rank,
                    "identityStatus": identity_status if isinstance(catalog, Mapping) else None,
                    "printingKey": printing_key,
                    "imageSha256": image.get("contentSha256"),
                    "facts": evidence,
                    "blockers": sorted(set(blockers)),
                    "warnings": sorted(set(warnings)),
                }
            )

    printing_owners: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    image_owners: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in card_rows:
        if row["printingKey"] is not None:
            printing_owners[row["printingKey"]].append(row)
        image_hash = row.get("imageSha256")
        if isinstance(image_hash, str) and SHA256_RE.fullmatch(image_hash):
            image_owners[image_hash].append(row)
    for owners in printing_owners.values():
        if len({row["variantId"] for row in owners}) > 1:
            for row in owners:
                row["blockers"].append("canonical_printing_duplicate")
    duplicate_image_groups = 0
    for owners in image_owners.values():
        if len({row["variantId"] for row in owners}) <= 1:
            continue
        duplicate_image_groups += 1
        blocker = (
            "image_cross_tcg_duplicate"
            if len({row["tcg"] for row in owners}) > 1
            else "image_unapproved_duplicate"
        )
        for row in owners:
            row["blockers"].append(blocker)

    release_counts: Counter[str] = Counter()
    all_issue_counts: Counter[str] = Counter()
    queue_cards: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    passed_qualified = 0
    for row in card_rows:
        row["blockers"] = sorted(set(row["blockers"]))
        row["warnings"] = sorted(set(row["warnings"]))
        issues = [*row["blockers"], *row["warnings"]]
        all_issue_counts.update(issues)
        if row["segment"] == "qualified":
            release_counts.update(row["blockers"])
            if not row["blockers"]:
                passed_qualified += 1
        categories = sorted(
            {
                BLOCKER_CATEGORY.get(issue, "anomalies")
                for issue in issues
            }
        )
        row["reviewCategories"] = categories
        for category in categories:
            queue_cards[category][row["id"]] = {
                "id": row["id"],
                "segment": row["segment"],
                "marketRank": row["marketRank"],
                "sla": (
                    "24h"
                    if isinstance(row["marketRank"], int)
                    and row["marketRank"] <= 100
                    else "3_business_days"
                ),
                "issues": sorted(
                    issue
                    for issue in issues
                    if BLOCKER_CATEGORY.get(issue, "anomalies") == category
                ),
            }
        evidence_hash_input = {
            "runId": run_id,
            "releaseProfile": release_profile_id,
            "policySha256": release["policySha256"],
            "universeCandidateSha256": universe_hash,
            "asOf": iso_utc(as_of),
            "id": row["id"],
            "variantId": row["variantId"],
            "segment": row["segment"],
            "marketRank": row["marketRank"],
            "facts": row["facts"],
            "blockers": row["blockers"],
            "warnings": row["warnings"],
        }
        row["evidenceSha256"] = sha256_bytes(
            canonical_json_bytes(evidence_hash_input)
        )
        row["decision"] = (
            "passed"
            if row["segment"] == "qualified" and not row["blockers"]
            else "failed"
            if row["segment"] == "qualified"
            else "monitoring"
        )
        row.pop("printingKey", None)

    review_queues: dict[str, Any] = {}
    for category, entries in sorted(queue_cards.items()):
        cards = sorted(
            entries.values(),
            key=lambda row: (
                row["marketRank"] is None,
                int(row["marketRank"] or 10**9),
                row["id"],
            ),
        )
        top_ranked = sum(
            1
            for row in cards
            if isinstance(row["marketRank"], int) and row["marketRank"] <= 100
        )
        review_queues[category] = {
            "count": len(cards),
            "topRankedCount": top_ranked,
            "slaPolicy": {
                "topRanked": "24h",
                "other": "3_business_days",
            },
            "cards": cards,
        }

    qualified_count = len(qualified)
    monitoring_count = len(monitoring)
    blocked_qualified = qualified_count - passed_qualified
    global_blockers: list[str] = []
    per_card_exclude = policy.get("allPoolFailureAction") == "per_card_exclude"
    minimum_verified = _policy_int(policy, "minimumVerifiedCount")
    release_passed = (
        passed_qualified >= minimum_verified
        if per_card_exclude
        else blocked_qualified == 0
    )
    status = "passed" if release_passed and not global_blockers else "blocked"
    database_fingerprint = sha256_bytes(
        canonical_json_bytes(
            {
                "authority": "canonical_mysql",
                "database": CANONICAL_DATABASE,
                "marketEvaluationId": facts.get("marketEvaluationId"),
                "formalUniverseLockSha256": facts.get("formalUniverseLockSha256"),
                "universeCandidateSha256": universe_hash,
                "asOf": iso_utc(as_of),
            }
        )
    )
    authority_summary = {
        "discoveryEvidenceCount": qualified_count + monitoring_count,
        "auditedCandidates": qualified_count + monitoring_count,
        "populationQualified": qualified_count,
        "populationMonitoring": monitoring_count,
        "formalEligible": sum(
            1
            for row in [*qualified, *monitoring]
            if isinstance(row, Mapping) and row.get("formalUniverseEligible") is True
        ),
        "formalQualified": sum(
            1
            for row in qualified
            if isinstance(row, Mapping) and row.get("formalUniverseEligible") is True
        ),
        **dict(authority_counts or {}),
    }
    return {
        "schemaVersion": 1,
        "runId": run_id,
        "releaseProfile": release_profile_id,
        "policySha256": release["policySha256"],
        "asOf": iso_utc(as_of),
        "status": status,
        "readOnly": True,
        "database": {
            "authority": "canonical_mysql",
            "name": CANONICAL_DATABASE,
            "marketEvaluationId": facts.get("marketEvaluationId"),
            "fingerprint": database_fingerprint,
        },
        "universe": {
            "schemaVersion": candidate.get("schemaVersion"),
            "candidateSha256": universe_hash,
            "formalUniverseLockSha256": facts.get("formalUniverseLockSha256"),
            "authoritySelection": (
                candidate.get("authority", {}).get("selection")
                if isinstance(candidate.get("authority"), Mapping)
                else None
            ),
            "qualified": qualified_count,
            "monitoring": monitoring_count,
            "members": qualified_count + monitoring_count,
            **authority_summary,
        },
        "counts": {
            "qualified": qualified_count,
            "monitoring": monitoring_count,
            "releaseReadyQualified": passed_qualified,
            "releaseBlockedQualified": blocked_qualified,
            "minimumReleaseEligible": minimum_verified,
            "reviewQueueCards": len(
                {
                    card_id
                    for entries in queue_cards.values()
                    for card_id in entries
                }
            ),
            **dict(facts.get("rowCounts") or {}),
        },
        "releaseGate": {
            "eligible": status == "passed",
            "perCardExclude": per_card_exclude,
            "minimumEligible": minimum_verified,
            "eligibleCardCount": passed_qualified,
            "blockerCount": sum(release_counts.values()),
            "blockerCardCount": blocked_qualified,
            "blockers": dict(sorted(release_counts.items())),
            "globalBlockers": global_blockers,
        },
        "allIssueCounts": dict(sorted(all_issue_counts.items())),
        "reviewQueues": review_queues,
        "duplicateImageGroups": duplicate_image_groups,
        "limitations": {
            "indexSourceMethod": (
                "market_index_constituent does not persist source method; "
                "current/anchor consistency is checked from exact price history"
            ),
            "monitoringGate": (
                "monitoring issues are queued but do not enter the public release gate"
            ),
        },
        "cards": card_rows,
    }


def receipt_for(report: Mapping[str, Any], report_sha256: str) -> dict[str, Any]:
    release_gate = report["releaseGate"]
    counts = report["counts"]
    return {
        "schemaVersion": 1,
        "runId": report["runId"],
        "releaseProfile": report["releaseProfile"],
        "policySha256": report["policySha256"],
        "asOf": report["asOf"],
        "status": report["status"],
        "readOnly": True,
        "database": CANONICAL_DATABASE,
        "databaseFingerprint": report["database"]["fingerprint"],
        "evaluationId": report["database"]["marketEvaluationId"],
        "universeCandidateSha256": report["universe"]["candidateSha256"],
        "reportSha256": report_sha256,
        "counts": {
            "qualified": counts["qualified"],
            "monitoring": counts["monitoring"],
            "releaseReadyQualified": counts["releaseReadyQualified"],
            "releaseBlockedQualified": counts["releaseBlockedQualified"],
            "minimumReleaseEligible": counts["minimumReleaseEligible"],
        },
        "releaseGate": {
            "eligible": release_gate["eligible"],
            "perCardExclude": release_gate["perCardExclude"],
            "minimumEligible": release_gate["minimumEligible"],
            "eligibleCardCount": release_gate["eligibleCardCount"],
            "blockerCount": release_gate["blockerCount"],
            "blockerCardCount": release_gate["blockerCardCount"],
            "blockers": release_gate["blockers"],
            "globalBlockers": release_gate["globalBlockers"],
        },
        "reviewQueueCounts": {
            category: queue["count"]
            for category, queue in report["reviewQueues"].items()
        },
    }


def write_immutable(path: Path, payload: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if path.read_bytes() != payload:
            raise CanonicalDbQcError(
                f"immutable QC evidence already exists with different bytes: {path}"
            ) from None
        return True
    return False


def write_report_and_receipt(
    report: Mapping[str, Any],
    output_root: Path,
) -> dict[str, Any]:
    run_id = str(report["runId"])
    if not RUN_ID_RE.fullmatch(run_id):
        raise CanonicalDbQcError("run_id must be 1-96 safe filename characters")
    run_root = output_root.resolve() / run_id
    report_path = run_root / "report.json"
    receipt_path = run_root / "receipt.json"
    report_payload = canonical_json_bytes(report, pretty=True)
    report_hash = sha256_bytes(report_payload)
    receipt = receipt_for(report, report_hash)
    receipt_payload = canonical_json_bytes(receipt, pretty=True)
    report_replayed = write_immutable(report_path, report_payload)
    receipt_replayed = write_immutable(receipt_path, receipt_payload)
    return {
        "report": str(report_path),
        "reportSha256": report_hash,
        "receipt": str(receipt_path),
        "receiptSha256": sha256_bytes(receipt_payload),
        "replayed": report_replayed and receipt_replayed,
    }


def begin_read_only_snapshot(connection: Any) -> None:
    connection.autocommit(False)
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION TRANSACTION READ ONLY")
        cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
        cursor.execute("SELECT DATABASE() AS database_name")
        row = cursor.fetchone() or {}
        if str(row.get("database_name") or "") != CANONICAL_DATABASE:
            raise CanonicalDbQcError(
                "canonical DB QC transaction is not connected to cardz_market_cap"
            )


def parse_as_of(raw: str | None) -> datetime:
    if raw is None:
        return datetime.now(timezone.utc).replace(microsecond=0)
    parsed = parse_time(raw)
    if parsed is None:
        raise CanonicalDbQcError("--as-of must be an ISO-8601 timestamp")
    return parsed.replace(microsecond=0)


def default_run_id(as_of: datetime, release_profile: str = "strict-v1") -> str:
    safe_profile = re.sub(r"[^A-Za-z0-9._-]+", "-", release_profile).strip("-")
    return f"qc_{as_of.strftime('%Y%m%dT%H%M%SZ')}_{safe_profile}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--routing-config", type=Path, default=DEFAULT_ROUTING_CONFIG)
    parser.add_argument("--release-profile", default=DEFAULT_RELEASE_PROFILE)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--database")
    parser.add_argument("--user")
    parser.add_argument("--password")
    parser.add_argument("--as-of")
    parser.add_argument("--run-id")
    parser.add_argument("--assets-root", type=Path, default=ROOT / "data/public/market-assets")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    as_of = parse_as_of(args.as_of)
    release = load_release_profile(
        load_registry(args.routing_config.resolve()), args.release_profile
    )
    run_id = args.run_id or default_run_id(as_of, str(release["releaseProfile"]))
    values = connection_values(args)
    connection = connect_from_values(values, read_only=True)
    try:
        begin_read_only_snapshot(connection)
        (
            candidate,
            authority_counts,
            universe_hash,
            formal_candidate,
            formal_lock_sha256,
        ) = build_audit_input(connection)
        facts = load_database_facts(
            connection,
            candidate,
            as_of,
            formal_lock_sha256=formal_lock_sha256,
            formal_opaque_ids=[
                str(card["pokedexId"])
                for card in formal_candidate["cards"]
            ],
        )
        report = audit_candidate(
            candidate,
            facts,
            universe_hash=universe_hash,
            run_id=run_id,
            as_of=as_of,
            assets_root=args.assets_root.resolve(),
            authority_counts=authority_counts,
            release_profile=release,
        )
        output = write_report_and_receipt(report, args.output_root)
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()
    summary = {
        "action": "canonical-db-qc",
        "status": report["status"],
        "readOnly": True,
        "runId": run_id,
        "releaseProfile": report["releaseProfile"],
        "policySha256": report["policySha256"],
        "universe": report["universe"],
        "counts": report["counts"],
        "releaseGate": report["releaseGate"],
        **output,
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if report["releaseGate"]["eligible"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        CanonicalDbQcError,
        OSError,
        ValueError,
        pymysql.MySQLError,
    ) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from None
