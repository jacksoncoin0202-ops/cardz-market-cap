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
SOURCE_RECORD_TYPE = "market_current_quote_revision"
LIVE_KINDS = {None, "", "bootstrap_from_observation"}
LEGACY_KIND = "legacy_generation_reconstructed"


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
    if revision_id <= 0:
        cursor.execute(
            "SELECT id FROM market_current_quote_revision WHERE quote_lineage_sha256=%s",
            (lineage,),
        )
        found = cursor.fetchone() or {}
        revision_id = int(found.get("id") or 0)
    if revision_id <= 0:
        raise RuntimeError("quote revision insert returned no id")
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
              OR
              (p2.source_code='pricecharting'
               AND p2.source_priority=95
               AND so2.observation_kind='psa10_price_guide'
               AND JSON_UNQUOTE(JSON_EXTRACT(so2.payload_json,'$.contract'))
                   ='pc_psa10_current_price_v1'
               AND JSON_UNQUOTE(JSON_EXTRACT(so2.payload_json,'$.method'))
                   ='pricecharting_explicit_psa10_field_v1'
               AND JSON_UNQUOTE(JSON_EXTRACT(so2.payload_json,'$.field'))
                   ='VGPC.chart_data.manualonly.last')
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
          AND (pi.card_language='en' OR p.source_code IN ('snkrdunk','snk_psa10','snk'))
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


def reconstruct_legacy_generation_quotes(cursor: Any) -> dict[str, int]:
    """Freeze accepted generation prices that no longer match the mutable observation.

    price_usd is reconstructed from accepted market_cap_usd / population so
    historical generation re-reads keep the originally accepted values.
    """

    cursor.execute(
        """
        SELECT metric.id AS acceptance_id,
               metric.variant_id,
               metric.market_cap_usd,
               metric.price_history_acceptance_id,
               metric.population_history_acceptance_id,
               metric.accepted_at,
               pop.top_grade_population AS psa10_population,
               price.source_code,
               price.source_external_entity_id,
               price.observed_date,
               price.payload_sha256,
               price.id AS market_price_observation_id,
               price.source_observation_id,
               price.price_usd AS live_price_usd
        FROM market_canonical_metric_acceptance metric
        INNER JOIN market_metric_history_acceptance price_history
          ON price_history.id=metric.price_history_acceptance_id
         AND price_history.metric_kind='psa10_price'
         AND price_history.source_record_type='market_price_observation'
        INNER JOIN market_price_observation price
          ON price.id=price_history.source_record_id
        INNER JOIN market_metric_history_acceptance pop_history
          ON pop_history.id=metric.population_history_acceptance_id
         AND pop_history.metric_kind='psa10_population'
        INNER JOIN market_grader_population_observation pop
          ON pop.id=pop_history.source_record_id
        WHERE metric.market_cap_usd IS NOT NULL
          AND pop.top_grade_population > 0
        """
    )
    written = 0
    mismatched = 0
    for raw in cursor.fetchall():
        pop = int(raw["psa10_population"])
        if pop <= 0:
            continue
        accepted_price = (
            Decimal(str(raw["market_cap_usd"])) / Decimal(pop)
        ).quantize(Decimal("0.000001"))
        live = Decimal(str(raw["live_price_usd"] or 0)).quantize(Decimal("0.000001"))
        if accepted_price <= 0:
            continue
        if abs(accepted_price - live) <= Decimal("0.000001"):
            continue
        mismatched += 1
        insert_quote_revision(
            cursor,
            variant_id=int(raw["variant_id"]),
            source_code=str(raw["source_code"]),
            source_external_entity_id=str(raw["source_external_entity_id"]),
            price_usd=accepted_price,
            source_period_at=raw["observed_date"],
            checked_at=raw["accepted_at"],
            payload_sha256=str(raw["payload_sha256"]),
            source_observation_id=int(raw["source_observation_id"] or 0) or None,
            market_price_observation_id=int(raw["market_price_observation_id"] or 0) or None,
            reconstruction_kind=LEGACY_KIND,
            reconstructed_from_acceptance_id=int(raw["acceptance_id"]),
        )
        written += 1
    return {"mismatchedGenerations": mismatched, "revisionsWritten": written}


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
    return {"ok": True, "distinctLineages": True}
