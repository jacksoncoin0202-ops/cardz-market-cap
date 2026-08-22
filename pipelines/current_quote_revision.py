#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Append-only current quote revisions for daily ranking lineage.

market_price_observation remains history (PC monthly series / SNK daily bars).
This module owns the ranking evidence layer: every capture is a new row with
immutable price_usd + source_period_at + checked_at. Writers never UPDATE an
existing revision's price.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence


CONTRACT = "market_current_quote_revision_v1"
LEGACY_RESOLUTION_CONTRACT = "market_legacy_quote_resolution_v2"
SOURCE_RECORD_TYPE = "market_current_quote_revision"
LIVE_KINDS = {None, "", "bootstrap_from_observation"}
LEGACY_KIND = "legacy_generation_reconstructed"
PC_CURRENT_PRICE_CONTRACT = "pc_psa10_current_price_v1"
PC_LOCAL_HISTORY_CONTRACT = "pc_psa10_local_history_v1"
# DADDY 2026-08-13: Chinese cards may use PriceCharting or SNK. Compare
# case-insensitively — S8 lowercases card_language to `zhtw` before the
# route decision, and a set that only lists `zhTW` silently routes Chinese
# to SNK-primary (v35: 19 PC history points, route=none).
PC_PRICE_LANGUAGES = frozenset({
    "en", "zh", "zhtw", "zh-tw", "zhcn", "zh-cn",
})


def _pc_price_language_key(language: str | None) -> str:
    return (language or "").strip().casefold().replace("_", "-")


def pc_price_language_ok(language: str | None) -> bool:
    return _pc_price_language_key(language) in PC_PRICE_LANGUAGES


def pc_price_language_sql(alias: str = "pi") -> str:
    langs = ",".join(f"'{item}'" for item in sorted(PC_PRICE_LANGUAGES))
    return f"LOWER(REPLACE({alias}.card_language, '_', '-')) IN ({langs})"


def pc_price_route_priority_sql(alias: str = "pi", source_expr: str = "q.source_code") -> str:
    """EN and Chinese prefer PriceCharting; Japanese stays SNK-primary."""

    lang = pc_price_language_sql(alias)
    return (
        f"CASE WHEN {lang} THEN "
        f"CASE WHEN {source_expr}='pricecharting' THEN 10 "
        f"WHEN {source_expr} IN ('snkrdunk','snk_psa10','snk') THEN 20 ELSE 90 END "
        f"ELSE CASE WHEN {source_expr} IN ('snkrdunk','snk_psa10','snk') THEN 10 "
        f"WHEN {source_expr}='pricecharting' THEN 20 ELSE 90 END END"
    )


def pc_guide_observation_predicate_sql(p_alias: str, so_alias: str) -> str:
    """PSA10 PC guide rows: current `last` field OR local history series.

    Ranking used to bootstrap only `pc_psa10_current_price_v1`. S8 writes
    `pc_psa10_local_history_v1` series points. Exact binds with a chart and
    no `last` contract then had observations but zero live quote revisions,
    so S12 aborted `acceptance_present_but_view_rejected` (leftover-5 2026-08-13).
    """

    return (
        f"{p_alias}.source_code='pricecharting'"
        f" AND {p_alias}.source_priority=95"
        f" AND {so_alias}.observation_kind='psa10_price_guide'"
        f" AND ("
        f"(JSON_UNQUOTE(JSON_EXTRACT({so_alias}.payload_json,'$.contract'))"
        f"='{PC_CURRENT_PRICE_CONTRACT}'"
        f" AND JSON_UNQUOTE(JSON_EXTRACT({so_alias}.payload_json,'$.method'))"
        f"='pricecharting_explicit_psa10_field_v1'"
        f" AND JSON_UNQUOTE(JSON_EXTRACT({so_alias}.payload_json,'$.field'))"
        f"='VGPC.chart_data.manualonly.last')"
        f" OR "
        f"(JSON_UNQUOTE(JSON_EXTRACT({so_alias}.payload_json,'$.contract'))"
        f"='{PC_LOCAL_HISTORY_CONTRACT}'"
        f" AND JSON_UNQUOTE(JSON_EXTRACT({so_alias}.payload_json,'$.method'))"
        f"='pricecharting_explicit_psa10_history_v1'"
        f" AND JSON_UNQUOTE(JSON_EXTRACT({so_alias}.payload_json,'$.field'))"
        f"='VGPC.chart_data.manualonly.series')"
        f")"
    )


def eligible_current_quote_revision_ddl() -> str:
    """Registry-driven ranking view installed by migration 053.

    Migration 051 created the generic-source tables and a strict-identity
    INNER JOIN view.  053 reconstructs eligibility: keep that path, and also
    accept a completed market_variant_source_state quote.  The V2 query
    deliberately contains no fixed eligible-source predicate.  Migration seed
    data preserves the old en/zh/zhtw/zh-tw/zhcn/zh-cn priorities while future
    sources only need registry plus route-policy rows.
    """

    return """
-- legacy seeded baseline: q.source_code IN ('snkrdunk','snk_psa10','snk','pricecharting')
-- exact-language seed set: en, zh, zhtw, zh-tw, zhcn, zh-cn
CREATE OR REPLACE VIEW operator_eligible_current_quote_revision AS
SELECT
  h.id AS price_history_acceptance_id,
  q.id AS quote_revision_id,
  q.variant_id,
  q.source_period_at AS observed_date,
  q.price_usd,
  q.checked_at,
  q.source_period_at,
  sr.canonical_source_code AS price_source_code,
  q.source_code AS price_storage_source_code,
  q.source_external_entity_id AS price_source_external_entity_id,
  q.source_observation_id AS price_source_observation_id,
  q.payload_sha256 AS price_payload_sha256,
  q.checked_at AS price_effective_at,
  q.checked_at AS price_source_observed_at,
  h.lineage_sha256 AS price_lineage_sha256,
  q.quote_lineage_sha256,
  q.reconstruction_kind,
  rp.priority AS price_route_priority,
  rp.policy_version AS price_policy_version
FROM market_metric_history_acceptance h
INNER JOIN market_current_quote_revision q
  ON h.source_record_type='market_current_quote_revision'
 AND h.source_record_id=q.id
 AND h.variant_id=q.variant_id
 AND h.observed_date=q.source_period_at
 AND h.source_effective_at=q.checked_at
 AND h.external_entity_id=q.source_external_entity_id
 AND h.source_payload_sha256=q.payload_sha256
INNER JOIN catalog_printing_identity pi ON pi.variant_id=q.variant_id
INNER JOIN market_source_registry sr
  ON sr.source_code=q.source_code AND sr.enabled=1
 AND JSON_CONTAINS(sr.capabilities_json,JSON_QUOTE('quote'),'$')=1
INNER JOIN market_quote_route_policy rp
  ON rp.id=(
    SELECT chosen.id
    FROM market_quote_route_policy chosen
    WHERE chosen.source_code=q.source_code
      AND chosen.is_active=1
      AND chosen.is_eligible=1
      AND chosen.language_code IN (
        LOWER(REPLACE(pi.card_language,'_','-')),'*'
      )
    ORDER BY
      CASE WHEN chosen.language_code=LOWER(REPLACE(pi.card_language,'_','-'))
           THEN 0 ELSE 1 END,
      chosen.activated_at DESC,
      chosen.policy_version DESC,
      chosen.priority ASC,
      chosen.id DESC
    LIMIT 1
  )
WHERE h.metric_kind='psa10_price'
  AND h.source_code=sr.canonical_source_code
  AND (
    EXISTS (
      SELECT 1 FROM operator_strict_source_identity si
      WHERE si.variant_id=q.variant_id
        AND si.source_code=sr.identity_source_code
        AND si.external_entity_id=q.source_external_entity_id
    )
    OR EXISTS (
      SELECT 1 FROM market_variant_source_state source_state
      WHERE source_state.variant_id=q.variant_id
        AND source_state.source_code=sr.canonical_source_code
        AND source_state.capability='quote'
        AND source_state.status='completed'
        AND source_state.selected_quote_revision_id=q.id
        AND source_state.payload_sha256=q.payload_sha256
        AND source_state.evidence_ref=CONCAT('market_current_quote_revision:',q.id)
    )
  )
  AND q.price_usd>0
  AND q.payload_sha256 REGEXP '^[0-9a-f]{64}$'
  AND q.quote_lineage_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.identity_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.acceptance_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.lineage_sha256 REGEXP '^[0-9a-f]{64}$'
  AND (q.reconstruction_kind IS NULL
       OR q.reconstruction_kind IN ('bootstrap_from_observation',''))
""".strip()


def apply_eligible_current_quote_revision_view(cursor: Any) -> None:
    cursor.execute(
        "SELECT COUNT(*) AS n FROM information_schema.tables"
        " WHERE table_schema=DATABASE() AND table_name='market_source_registry'"
    )
    row = cursor.fetchone() or {}
    count = row.get("n") if isinstance(row, Mapping) else row[0]
    if int(count or 0) != 1:
        if __import__("os").environ.get("CARDZ_DAILY_CHAIN_V2") == "1":
            raise RuntimeError("migration 051/053 is required before V2 quote selection")
        # Until DADDY authorises Task Scheduler cutover, the current local
        # scheduled path must keep its already-installed 050 view.  Do not
        # replace it with SQL that references tables not installed yet.
        return
    cursor.execute(eligible_current_quote_revision_ddl())


def _as_utc_naive(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    text = str(value).replace("Z", "+00:00").replace(" ", "T")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _price_text(value: Any) -> str:
    return format(Decimal(str(value)).quantize(Decimal("0.000001")), "f")


def quote_lineage_sha256(
    *,
    variant_id: int,
    source_code: str,
    source_external_entity_id: str,
    price_usd: Any,
    source_period_at: Any,
    checked_at: Any,
    payload_sha256: str,
    reconstruction_kind: str | None = None,
) -> str:
    payload = {
        "contract": CONTRACT,
        "variantId": int(variant_id),
        "sourceCode": str(source_code).casefold(),
        "externalEntityId": str(source_external_entity_id),
        "priceUsd": _price_text(price_usd),
        "sourcePeriodAt": _as_date(source_period_at).isoformat(),
        "checkedAt": _as_utc_naive(checked_at).isoformat(sep=" "),
        "payloadSha256": str(payload_sha256).casefold(),
        "reconstructionKind": reconstruction_kind or None,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def insert_quote_revision(
    cursor: Any,
    *,
    variant_id: int,
    source_code: str,
    source_external_entity_id: str,
    price_usd: Any,
    source_period_at: Any,
    checked_at: Any,
    payload_sha256: str,
    source_observation_id: int | None = None,
    market_price_observation_id: int | None = None,
    reconstruction_kind: str | None = None,
    reconstructed_from_acceptance_id: int | None = None,
    run_id: int | None = None,
) -> int:
    """Insert one immutable quote revision. Returns id (existing or new)."""

    source = str(source_code).casefold()
    external = str(source_external_entity_id)
    period = _as_date(source_period_at)
    checked = _as_utc_naive(checked_at)
    price = Decimal(_price_text(price_usd))
    if price <= 0:
        raise ValueError("quote revision price must be positive")
    payload = str(payload_sha256).casefold()
    if len(payload) != 64 or any(ch not in "0123456789abcdef" for ch in payload):
        raise ValueError("quote revision payload_sha256 invalid")
    lineage = quote_lineage_sha256(
        variant_id=variant_id,
        source_code=source,
        source_external_entity_id=external,
        price_usd=price,
        source_period_at=period,
        checked_at=checked,
        payload_sha256=payload,
        reconstruction_kind=reconstruction_kind,
    )
    cursor.execute(
        """
        INSERT INTO market_current_quote_revision
          (variant_id, source_code, source_external_entity_id, price_usd,
           source_period_at, checked_at, source_observation_id,
           market_price_observation_id, payload_sha256, quote_lineage_sha256,
           reconstruction_kind, reconstructed_from_acceptance_id, run_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)
        """,
        (
            int(variant_id),
            source,
            external,
            price,
            period,
            checked,
            int(source_observation_id) if source_observation_id else None,
            int(market_price_observation_id) if market_price_observation_id else None,
            payload,
            lineage,
            reconstruction_kind,
            int(reconstructed_from_acceptance_id)
            if reconstructed_from_acceptance_id
            else None,
            int(run_id) if run_id else None,
        ),
    )
    revision_id = int(cursor.lastrowid or 0)
    cursor.execute(
        """
        SELECT id, variant_id, source_code, source_external_entity_id,
               price_usd, source_period_at, checked_at, payload_sha256,
               reconstruction_kind, reconstructed_from_acceptance_id
        FROM market_current_quote_revision
        WHERE quote_lineage_sha256=%s
        """,
        (lineage,),
    )
    found = cursor.fetchone() or {}
    revision_id = int(found.get("id") or revision_id or 0)
    if revision_id <= 0:
        raise RuntimeError("quote revision insert returned no id")
    expected_owner = (
        int(reconstructed_from_acceptance_id)
        if reconstructed_from_acceptance_id
        else None
    )
    actual_owner = (
        int(found["reconstructed_from_acceptance_id"])
        if found.get("reconstructed_from_acceptance_id") is not None
        else None
    )
    if actual_owner != expected_owner:
        raise RuntimeError(
            "quote lineage collision across reconstruction owners: "
            f"lineage={lineage} expected={expected_owner} actual={actual_owner}"
        )
    return revision_id


def insert_quote_revisions_many(
    cursor: Any,
    rows: Sequence[Mapping[str, Any]],
) -> list[int]:
    ids: list[int] = []
    for row in rows:
        ids.append(
            insert_quote_revision(
                cursor,
                variant_id=int(row["variant_id"]),
                source_code=str(row["source_code"]),
                source_external_entity_id=str(row["source_external_entity_id"]),
                price_usd=row["price_usd"],
                source_period_at=row["source_period_at"],
                checked_at=row["checked_at"],
                payload_sha256=str(row["payload_sha256"]),
                source_observation_id=row.get("source_observation_id"),
                market_price_observation_id=row.get("market_price_observation_id"),
                reconstruction_kind=row.get("reconstruction_kind"),
                reconstructed_from_acceptance_id=row.get(
                    "reconstructed_from_acceptance_id"
                ),
                run_id=row.get("run_id"),
            )
        )
    return ids


def bootstrap_from_eligible_observations(cursor: Any, *, actor: str = "043-bootstrap") -> dict[str, int]:
    """Seed quote revisions from current eligible market_price_observation rows.

    checked_at prefers source observation observed_at, then price effective_at.
    source_period_at is the observation's observed_date (PC month or SNK day).
    """

    # Only the latest eligible observation per (variant, storage source).
    # Full SNK daily history stays in market_price_observation for charts.
    cursor.execute(
        """
        SELECT p.id AS market_price_observation_id,
               p.variant_id, p.source_code, p.source_external_entity_id,
               p.price_usd, p.observed_date, p.effective_at, p.payload_sha256,
               p.source_observation_id, p.run_id,
               so.observed_at AS source_observed_at
        FROM market_price_observation p
        INNER JOIN (
          SELECT p2.variant_id, p2.source_code, MAX(p2.observed_date) AS max_day
          FROM market_price_observation p2
          INNER JOIN market_universe_member am2 ON am2.variant_id=p2.variant_id
          INNER JOIN market_universe_lock ul2
            ON ul2.id=am2.universe_lock_id AND ul2.is_current=1
          INNER JOIN market_source_observation so2
            ON so2.id=p2.source_observation_id
           AND so2.source_code=p2.source_code
           AND so2.external_entity_id=p2.source_external_entity_id
           AND so2.payload_sha256=p2.payload_sha256
           AND so2.observed_date=p2.observed_date
          WHERE p2.metric_status='ready' AND p2.price_usd>0
            AND p2.source_code IN ('snkrdunk','snk_psa10','snk','pricecharting')
            AND (
              (p2.source_code IN ('snkrdunk','snk_psa10','snk')
               AND so2.observation_kind='psa10_reference_price')
              OR (""" + pc_guide_observation_predicate_sql("p2", "so2") + """)
            )
          GROUP BY p2.variant_id, p2.source_code
        ) latest
          ON latest.variant_id=p.variant_id
         AND latest.source_code=p.source_code
         AND latest.max_day=p.observed_date
        INNER JOIN market_universe_member am ON am.variant_id=p.variant_id
        INNER JOIN market_universe_lock ul
          ON ul.id=am.universe_lock_id AND ul.is_current=1
        INNER JOIN catalog_printing_identity pi ON pi.variant_id=p.variant_id
        INNER JOIN operator_strict_source_identity si
          ON si.variant_id=p.variant_id
         AND si.source_code=CASE WHEN p.source_code IN ('snk','snk_psa10')
                                 THEN 'snkrdunk' ELSE p.source_code END
         AND si.external_entity_id=p.source_external_entity_id
        INNER JOIN market_source_observation so
          ON so.id=p.source_observation_id
         AND so.source_code=p.source_code
         AND so.external_entity_id=p.source_external_entity_id
         AND so.payload_sha256=p.payload_sha256
         AND so.observed_date=p.observed_date
        WHERE p.metric_status='ready' AND p.price_usd>0
          AND (""" + pc_price_language_sql("pi") + """
               OR p.source_code IN ('snkrdunk','snk_psa10','snk'))
        """
    )
    rows = list(cursor.fetchall())
    inserted = 0
    for raw in rows:
        checked = (
            raw.get("source_observed_at")
            or raw.get("effective_at")
            or datetime.now(timezone.utc).replace(tzinfo=None)
        )
        insert_quote_revision(
            cursor,
            variant_id=int(raw["variant_id"]),
            source_code=str(raw["source_code"]),
            source_external_entity_id=str(raw["source_external_entity_id"]),
            price_usd=raw["price_usd"],
            source_period_at=raw["observed_date"],
            checked_at=checked,
            payload_sha256=str(raw["payload_sha256"]),
            source_observation_id=int(raw["source_observation_id"] or 0) or None,
            market_price_observation_id=int(raw["market_price_observation_id"]),
            reconstruction_kind="bootstrap_from_observation",
            run_id=int(raw["run_id"]) if raw.get("run_id") else None,
        )
        inserted += 1
    return {"eligibleObservations": len(rows), "revisionsWritten": inserted, "actor": actor}


def _legacy_quote_spec(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    """Derive one historical quote exclusively from immutable acceptances."""

    population = int(raw.get("psa10_population") or 0)
    market_cap = Decimal(str(raw.get("market_cap_usd") or 0))
    if population <= 0 or market_cap <= 0:
        return None
    accepted_price = (market_cap / Decimal(population)).quantize(Decimal("0.000001"))
    if accepted_price <= 0:
        return None
    return {
        "variant_id": int(raw["variant_id"]),
        "source_code": str(raw["source_code"]),
        "source_external_entity_id": str(raw["source_external_entity_id"]),
        "price_usd": accepted_price,
        "source_period_at": raw["source_period_at"],
        "checked_at": raw["accepted_at"],
        "payload_sha256": str(raw["source_payload_sha256"]),
        "source_observation_id": None,
        "market_price_observation_id": int(raw["source_record_id"]),
        "reconstruction_kind": LEGACY_KIND,
        "reconstructed_from_acceptance_id": int(raw["acceptance_id"]),
    }


def legacy_resolution_evidence_sha256(
    raw: Mapping[str, Any], quote_lineage_sha256_value: str,
) -> str:
    """Bind a resolver row to both immutable metric inputs and its quote."""

    spec = _legacy_quote_spec(raw)
    if spec is None:
        raise ValueError("invalid metric acceptance cannot be resolved")
    # Keep this exact order aligned with migration 049's SQL expression.  The
    # reader recomputes it, so a row merely labelled ``resolved`` is not trusted.
    evidence = (
        LEGACY_RESOLUTION_CONTRACT,
        int(raw["acceptance_id"]),
        int(raw["price_history_acceptance_id"]),
        int(raw["population_history_acceptance_id"]),
        str(raw["metric_lineage_sha256"]),
        str(raw["price_history_lineage_sha256"]),
        str(raw["population_history_lineage_sha256"]),
        _price_text(spec["price_usd"]),
        int(raw["psa10_population"]),
        str(quote_lineage_sha256_value),
    )
    encoded = "|".join(str(value) for value in evidence)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def select_verified_legacy_candidate(
    raw: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Select only an exact immutable-payload match; ambiguity fails closed."""

    spec = _legacy_quote_spec(raw)
    if spec is None:
        return None
    matches = [
        candidate for candidate in candidates
        if int(candidate.get("variant_id") or 0) == int(spec["variant_id"])
        and str(candidate.get("source_code") or "").casefold()
            == str(spec["source_code"]).casefold()
        and str(candidate.get("source_external_entity_id") or "")
            == str(spec["source_external_entity_id"])
        and _price_text(candidate.get("price_usd")) == _price_text(spec["price_usd"])
        and _as_date(candidate.get("source_period_at"))
            == _as_date(spec["source_period_at"])
        and _as_utc_naive(candidate.get("checked_at"))
            == _as_utc_naive(spec["checked_at"])
        and str(candidate.get("payload_sha256") or "").casefold()
            == str(spec["payload_sha256"]).casefold()
        and int(candidate.get("reconstructed_from_acceptance_id") or 0)
            == int(spec["reconstructed_from_acceptance_id"])
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def reconstruct_legacy_generation_quotes(cursor: Any) -> dict[str, int]:
    """Resolve every observation-pointed metric without mutating history.

    The immutable acceptance payload, identity, period, accepted market cap and
    accepted population mint the only permitted quote.  Mutable observation
    columns never decide the historical value.  Existing semantic candidates
    remain append-only; the explicit resolution map selects the canonical one.
    """

    cursor.execute(
        """
        SELECT metric.id AS acceptance_id,
               metric.variant_id,
               metric.market_cap_usd,
               metric.price_history_acceptance_id,
               metric.population_history_acceptance_id,
               metric.accepted_at,
               metric.metric_lineage_sha256,
               pop.top_grade_population AS psa10_population,
               price_history.source_code,
               price_history.external_entity_id AS source_external_entity_id,
               price_history.observed_date AS source_period_at,
               price_history.source_payload_sha256,
               price_history.source_record_id,
               price_history.lineage_sha256 AS price_history_lineage_sha256,
               pop_history.lineage_sha256 AS population_history_lineage_sha256,
               existing_resolution.quote_revision_id
                 AS existing_resolution_quote_revision_id,
               existing_resolution.price_usd AS existing_resolution_price_usd,
               existing_resolution.source_period_at
                 AS existing_resolution_source_period_at,
               existing_resolution.checked_at AS existing_resolution_checked_at,
               existing_resolution.quote_lineage_sha256
                 AS existing_resolution_quote_lineage_sha256,
               existing_resolution.resolution_status
                 AS existing_resolution_status,
               existing_resolution.resolver_evidence_sha256
                 AS existing_resolver_evidence_sha256,
               existing_quote.id AS existing_quote_id,
               existing_quote.variant_id AS existing_quote_variant_id,
               existing_quote.source_code AS existing_quote_source_code,
               existing_quote.source_external_entity_id
                 AS existing_quote_source_external_entity_id,
               existing_quote.price_usd AS existing_quote_price_usd,
               existing_quote.source_period_at AS existing_quote_source_period_at,
               existing_quote.checked_at AS existing_quote_checked_at,
               existing_quote.payload_sha256 AS existing_quote_payload_sha256,
               existing_quote.quote_lineage_sha256
                 AS existing_quote_lineage_sha256,
               existing_quote.reconstruction_kind
                 AS existing_quote_reconstruction_kind,
               existing_quote.reconstructed_from_acceptance_id
                 AS existing_quote_reconstructed_from_acceptance_id
        FROM market_canonical_metric_acceptance metric
        INNER JOIN market_metric_history_acceptance price_history
          ON price_history.id=metric.price_history_acceptance_id
         AND price_history.metric_kind='psa10_price'
         AND price_history.source_record_type='market_price_observation'
        INNER JOIN market_metric_history_acceptance pop_history
          ON pop_history.id=metric.population_history_acceptance_id
         AND pop_history.metric_kind='psa10_population'
        INNER JOIN market_grader_population_observation pop
          ON pop.id=pop_history.source_record_id
        LEFT JOIN market_legacy_quote_resolution existing_resolution
          ON existing_resolution.metric_acceptance_id=metric.id
        LEFT JOIN market_current_quote_revision existing_quote
          ON existing_quote.id=existing_resolution.quote_revision_id
        WHERE metric.market_cap_usd IS NOT NULL
          AND pop.top_grade_population > 0
        """
    )
    rows = list(cursor.fetchall())
    cursor.execute("SELECT COUNT(*) AS n FROM market_current_quote_revision")
    before = int((cursor.fetchone() or {}).get("n") or 0)
    resolved = 0
    invalid = 0
    scanned = len(rows)
    reused = 0
    resolution_work: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    pending_quotes: list[
        tuple[Mapping[str, Any], Mapping[str, Any], str]
    ] = []

    def flush_resolution_rows() -> None:
        if not resolution_work:
            return
        pending_resolution_rows = []
        for raw, candidate in resolution_work:
            evidence = legacy_resolution_evidence_sha256(
                raw, str(candidate.get("quote_lineage_sha256") or ""),
            )
            pending_resolution_rows.append((
                int(raw["acceptance_id"]), int(candidate["id"]),
                candidate["price_usd"], candidate["source_period_at"],
                candidate["checked_at"],
                str(candidate.get("quote_lineage_sha256") or ""), evidence,
            ))
        value_sql = (
            "(%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6),'resolved',%s,NULL,"
            "'legacy-quote-resolver-v2')"
        )
        for start in range(0, len(pending_resolution_rows), 500):
            chunk = pending_resolution_rows[start:start + 500]
            flat_params = tuple(
                value
                for resolution_row in chunk
                for value in resolution_row
            )
            cursor.execute(
                """
                INSERT INTO market_legacy_quote_resolution
                  (metric_acceptance_id,quote_revision_id,price_usd,
                   source_period_at,checked_at,quote_lineage_sha256,resolved_at,
                   resolution_status,resolver_evidence_sha256,blocker_code,
                   resolved_by)
                VALUES """ + ",".join(value_sql for _ in chunk) + """
                ON DUPLICATE KEY UPDATE
                  quote_revision_id=VALUES(quote_revision_id),
                  price_usd=VALUES(price_usd),
                  source_period_at=VALUES(source_period_at),
                  checked_at=VALUES(checked_at),
                  quote_lineage_sha256=VALUES(quote_lineage_sha256),
                  resolved_at=VALUES(resolved_at),
                  resolution_status=VALUES(resolution_status),
                  resolver_evidence_sha256=VALUES(resolver_evidence_sha256),
                  blocker_code=NULL,
                  resolved_by=VALUES(resolved_by)
                """,
                flat_params,
            )
        resolution_work.clear()

    for raw in rows:
        spec = _legacy_quote_spec(raw)
        if spec is None:
            mark_legacy_resolution_invalid(
                cursor, int(raw["acceptance_id"]), "non_positive_accepted_metric",
            )
            invalid += 1
            continue

        # First reuse the mapped resolution when the full immutable contract is
        # still current.  Stale/missing maps are handled set-wise below; there
        # must never be a per-acceptance SELECT/INSERT loop here again.
        joined_candidate = None
        if raw.get("existing_quote_id") is not None:
            joined_candidate = {
                "id": raw["existing_quote_id"],
                "variant_id": raw["existing_quote_variant_id"],
                "source_code": raw["existing_quote_source_code"],
                "source_external_entity_id": raw[
                    "existing_quote_source_external_entity_id"
                ],
                "price_usd": raw["existing_quote_price_usd"],
                "source_period_at": raw["existing_quote_source_period_at"],
                "checked_at": raw["existing_quote_checked_at"],
                "payload_sha256": raw["existing_quote_payload_sha256"],
                "quote_lineage_sha256": raw["existing_quote_lineage_sha256"],
                "reconstruction_kind": raw[
                    "existing_quote_reconstruction_kind"
                ],
                "reconstructed_from_acceptance_id": raw[
                    "existing_quote_reconstructed_from_acceptance_id"
                ],
            }
        candidate = select_verified_legacy_candidate(
            raw, [joined_candidate] if joined_candidate is not None else [],
        )
        if candidate is not None:
            expected_evidence = legacy_resolution_evidence_sha256(
                raw, str(candidate.get("quote_lineage_sha256") or ""),
            )
            resolution_is_current = all((
                str(raw.get("existing_resolution_status") or "") == "resolved",
                int(raw.get("existing_resolution_quote_revision_id") or 0)
                    == int(candidate["id"]),
                str(candidate.get("reconstruction_kind") or "") == LEGACY_KIND,
                _price_text(raw.get("existing_resolution_price_usd"))
                    == _price_text(candidate.get("price_usd")),
                _as_date(raw.get("existing_resolution_source_period_at"))
                    == _as_date(candidate.get("source_period_at")),
                _as_utc_naive(raw.get("existing_resolution_checked_at"))
                    == _as_utc_naive(candidate.get("checked_at")),
                str(raw.get("existing_resolution_quote_lineage_sha256") or "")
                    == str(candidate.get("quote_lineage_sha256") or ""),
                str(raw.get("existing_resolver_evidence_sha256") or "")
                    == expected_evidence,
            ))
            if resolution_is_current:
                resolved += 1
                reused += 1
                continue
            resolution_work.append((raw, candidate))
        else:
            lineage = quote_lineage_sha256(
                variant_id=int(spec["variant_id"]),
                source_code=str(spec["source_code"]),
                source_external_entity_id=str(spec["source_external_entity_id"]),
                price_usd=spec["price_usd"],
                source_period_at=spec["source_period_at"],
                checked_at=spec["checked_at"],
                payload_sha256=str(spec["payload_sha256"]),
                reconstruction_kind=spec.get("reconstruction_kind"),
            )
            pending_quotes.append((raw, spec, lineage))

    quote_by_lineage: dict[str, Mapping[str, Any]] = {}

    def load_quote_candidates(lineages: Sequence[str]) -> None:
        for start in range(0, len(lineages), 500):
            chunk = list(lineages[start:start + 500])
            if not chunk:
                continue
            marks = ",".join(["%s"] * len(chunk))
            cursor.execute(
                f"""
                SELECT id,variant_id,source_code,source_external_entity_id,
                       price_usd,source_period_at,checked_at,payload_sha256,
                       quote_lineage_sha256,reconstruction_kind,
                       reconstructed_from_acceptance_id
                FROM market_current_quote_revision
                WHERE quote_lineage_sha256 IN ({marks})
                """,
                tuple(chunk),
            )
            for quote in cursor.fetchall():
                quote_by_lineage[str(quote["quote_lineage_sha256"])] = quote

    desired_lineages = list(dict.fromkeys(
        lineage for _, _, lineage in pending_quotes
    ))
    load_quote_candidates(desired_lineages)
    missing_by_lineage: dict[
        str, tuple[Mapping[str, Any], Mapping[str, Any], str]
    ] = {}
    for item in pending_quotes:
        if item[2] not in quote_by_lineage:
            missing_by_lineage.setdefault(item[2], item)

    missing_items = list(missing_by_lineage.values())
    quote_value_sql = "(" + ",".join(["%s"] * 13) + ")"
    for start in range(0, len(missing_items), 500):
        chunk = missing_items[start:start + 500]
        if not chunk:
            continue
        params: list[Any] = []
        for raw, spec, lineage in chunk:
            price = Decimal(_price_text(spec["price_usd"]))
            payload = str(spec["payload_sha256"]).casefold()
            if price <= 0:
                raise ValueError("quote revision price must be positive")
            if len(payload) != 64 or any(
                char not in "0123456789abcdef" for char in payload
            ):
                raise ValueError("quote revision payload_sha256 invalid")
            params.extend((
                int(spec["variant_id"]), str(spec["source_code"]).casefold(),
                str(spec["source_external_entity_id"]), price,
                _as_date(spec["source_period_at"]),
                _as_utc_naive(spec["checked_at"]), None,
                int(spec["market_price_observation_id"]), payload, lineage,
                spec.get("reconstruction_kind"), int(raw["acceptance_id"]), None,
            ))
        cursor.execute(
            """
            INSERT INTO market_current_quote_revision
              (variant_id,source_code,source_external_entity_id,price_usd,
               source_period_at,checked_at,source_observation_id,
               market_price_observation_id,payload_sha256,quote_lineage_sha256,
               reconstruction_kind,reconstructed_from_acceptance_id,run_id)
            VALUES """ + ",".join(quote_value_sql for _ in chunk) + """
            ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)
            """,
            tuple(params),
        )
    if missing_items:
        load_quote_candidates([item[2] for item in missing_items])

    for raw, _spec, lineage in pending_quotes:
        candidate = quote_by_lineage.get(lineage)
        if candidate is None:
            mark_legacy_resolution_invalid(
                cursor, int(raw["acceptance_id"]),
                "ambiguous_or_missing_exact_quote_candidate",
            )
            invalid += 1
            continue
        owner = int(candidate.get("reconstructed_from_acceptance_id") or 0)
        expected_owner = int(raw["acceptance_id"])
        if owner != expected_owner:
            raise RuntimeError(
                "quote lineage collision across reconstruction owners: "
                f"lineage={lineage} expected={expected_owner} actual={owner}"
            )
        verified = select_verified_legacy_candidate(raw, [candidate])
        if verified is None:
            mark_legacy_resolution_invalid(
                cursor, expected_owner,
                "ambiguous_or_missing_exact_quote_candidate",
            )
            invalid += 1
            continue
        resolution_work.append((raw, verified))

    resolution_rows_written = len(resolution_work)
    resolved += resolution_rows_written
    flush_resolution_rows()
    cursor.execute("SELECT COUNT(*) AS n FROM market_current_quote_revision")
    after = int((cursor.fetchone() or {}).get("n") or 0)
    return {
        "scannedObservationPointers": scanned,
        "resolvedObservationPointers": resolved,
        "invalidObservationPointers": invalid,
        "reusedExistingResolutions": reused,
        "resolutionRowsWritten": resolution_rows_written,
        "revisionsWritten": after - before,
        "canonicalRepointed": 0,
    }


def resolve_legacy_quote_for_metric(cursor: Any, metric_acceptance_id: int) -> dict[str, Any] | None:
    """Resolve through the verified map view; missing evidence fails closed."""
    cursor.execute(
        """
        SELECT quote_revision_id AS id, variant_id, price_usd, source_period_at,
               checked_at, source_code, source_external_entity_id, payload_sha256,
               quote_lineage_sha256, reconstruction_kind,
               resolver_evidence_sha256
        FROM operator_resolved_canonical_metric_quote
        WHERE metric_acceptance_id=%s AND resolution_kind='legacy'
        LIMIT 1
        """,
        (int(metric_acceptance_id),),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def upsert_legacy_resolution(
    cursor: Any,
    metric_acceptance_id: int,
    quote_revision_id: int,
    resolver_evidence_sha256: str,
) -> None:
    """Write/refresh the 045 1:1 historical resolver evidence row."""
    evidence = str(resolver_evidence_sha256).casefold()
    if len(evidence) != 64 or any(ch not in "0123456789abcdef" for ch in evidence):
        raise ValueError("resolver evidence SHA256 invalid")
    cursor.execute(
        """
        INSERT INTO market_legacy_quote_resolution
          (metric_acceptance_id, quote_revision_id, price_usd, source_period_at, checked_at,
           quote_lineage_sha256, resolved_at, resolution_status,
           resolver_evidence_sha256, blocker_code, resolved_by)
        SELECT %s, q.id, q.price_usd, q.source_period_at, q.checked_at,
               q.quote_lineage_sha256, UTC_TIMESTAMP(6), 'resolved', %s, NULL,
               'legacy-quote-resolver-v2'
        FROM market_current_quote_revision q
        WHERE q.id=%s
        ON DUPLICATE KEY UPDATE
          quote_revision_id=VALUES(quote_revision_id),
          price_usd=VALUES(price_usd),
          source_period_at=VALUES(source_period_at),
          checked_at=VALUES(checked_at),
          quote_lineage_sha256=VALUES(quote_lineage_sha256),
          resolved_at=VALUES(resolved_at),
          resolution_status=VALUES(resolution_status),
          resolver_evidence_sha256=VALUES(resolver_evidence_sha256),
          blocker_code=NULL,
          resolved_by=VALUES(resolved_by)
        """,
        (int(metric_acceptance_id), evidence, int(quote_revision_id)),
    )


def mark_legacy_resolution_invalid(
    cursor: Any, metric_acceptance_id: int, blocker_code: str,
) -> None:
    """Invalidate an existing projection row without deleting any quote."""

    cursor.execute(
        """
        UPDATE market_legacy_quote_resolution
        SET resolution_status='invalid_acceptance',
            resolver_evidence_sha256=NULL,
            blocker_code=%s,
            resolved_by='legacy-quote-resolver-v2',
            resolved_at=UTC_TIMESTAMP(6)
        WHERE metric_acceptance_id=%s
        """,
        (str(blocker_code), int(metric_acceptance_id)),
    )



def self_test() -> dict[str, Any]:
    lineage_a = quote_lineage_sha256(
        variant_id=1,
        source_code="pricecharting",
        source_external_entity_id="123",
        price_usd="2925.000000",
        source_period_at="2026-08-01",
        checked_at="2026-08-10 12:00:00",
        payload_sha256="a" * 64,
    )
    lineage_b = quote_lineage_sha256(
        variant_id=1,
        source_code="pricecharting",
        source_external_entity_id="123",
        price_usd="2836.310000",
        source_period_at="2026-08-01",
        checked_at="2026-08-11 22:02:00",
        payload_sha256="b" * 64,
    )
    same = quote_lineage_sha256(
        variant_id=1,
        source_code="pricecharting",
        source_external_entity_id="123",
        price_usd="2925.000000",
        source_period_at="2026-08-01",
        checked_at="2026-08-10 12:00:00",
        payload_sha256="a" * 64,
    )
    assert lineage_a != lineage_b
    assert lineage_a == same
    assert len(lineage_a) == 64
    assert pc_price_language_ok("zhtw")
    assert pc_price_language_ok("zhTW")
    assert pc_price_language_ok("zh-TW")
    assert pc_price_language_ok("en")
    assert not pc_price_language_ok("ja")
    lang_sql = pc_price_language_sql("pi")
    assert "LOWER(REPLACE(pi.card_language" in lang_sql
    assert "zhtw" in lang_sql
    ddl = eligible_current_quote_revision_ddl()
    assert "CREATE OR REPLACE VIEW operator_eligible_current_quote_revision" in ddl
    assert "zhtw" in ddl
    assert "pc_psa10_local_history_v1" in pc_guide_observation_predicate_sql("p2", "so2")
    return {
        "ok": True,
        "distinctLineages": True,
        "pcPriceLanguageCasefold": True,
        "localHistoryGuidePredicate": True,
    }
