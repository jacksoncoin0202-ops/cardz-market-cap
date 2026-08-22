#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MySQL contract barrier and publication outbox for Daily Chain V2."""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from daily_chain_v2_contract import (
    canonical_json,
    core_contract_keys,
    pop_contract_sources,
    quote_repair_routes,
    rates_contract_sources,
    route_policy_upserts,
    sha256,
)
from qualified_pool_operator import db, load_env


JST = ZoneInfo("Asia/Tokyo")
LIVE_HEALTH_URL = "https://app.cardzmarketcap.com/api/health"
LIVE_URL = "https://app.cardzmarketcap.com/"


def business_window_utc(business_date: str) -> tuple[datetime, datetime]:
    day = date.fromisoformat(business_date)
    start = datetime.combine(day, time.min, tzinfo=JST).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=JST).astimezone(timezone.utc)
    return start.replace(tzinfo=None), end.replace(tzinfo=None)


def _scalar(cursor: Any, query: str, params: tuple[Any, ...] = ()) -> int:
    cursor.execute(query, params)
    row = cursor.fetchone() or {}
    value = next(iter(row.values())) if isinstance(row, Mapping) else row[0]
    return int(value or 0)


def current_run_contract(business_date: str) -> dict[str, Any]:
    """Read the strict pre-publication coverage facts from canonical MySQL."""

    start, end = business_window_utc(business_date)
    load_env()
    connection = db()
    try:
        cursor = connection.cursor()
        tables = {
            # information_schema labels are TABLE_NAME on this Windows MySQL
            # build even when the query spells table_name.  This is a
            # single-column probe, so consume the value instead of depending
            # on server-specific DictCursor key casing.
            str(next(iter(row.values())))
            for row in _rows(
                cursor,
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema=DATABASE() AND table_name IN
                  ('market_source_registry','market_quote_route_policy',
                   'market_variant_source_state','publication_outbox',
                   'publication_delivery')
                """,
            )
        }
        expected_tables = {
            "market_source_registry", "market_quote_route_policy",
            "market_variant_source_state", "publication_outbox", "publication_delivery",
        }
        if tables != expected_tables:
            raise RuntimeError(
                "schema contract migration 051 incomplete:"
                f" missing={sorted(expected_tables - tables)}"
            )

        # The registry is the source list.  Every section below is built from
        # these rows, so a new provider needs a registry row and an adapter,
        # never another branch here.
        registry_rows = _rows(
            cursor,
            """
            SELECT source_code,canonical_source_code,identity_source_code,
                   required_class,enabled,capabilities_json,config_json
            FROM market_source_registry ORDER BY source_code
            """,
        )
        barrier_keys = core_contract_keys(registry_rows)
        pop_sources = pop_contract_sources(registry_rows) or (
            {
                "sourceCode": "gemrate",
                "identitySourceCode": "gemrate",
                "popCheckpointSourceCode": "gemrate_pop",
            },
        )
        rates_sources = rates_contract_sources(registry_rows) or ("fx",)

        active_count = _scalar(
            cursor,
            """
            SELECT COUNT(*) AS n FROM market_universe_member am
            INNER JOIN market_universe_lock ul
              ON ul.id=am.universe_lock_id AND ul.is_current=1
            """,
        )
        pop_sections: dict[str, Any] = {}
        for pop_source in pop_sources:
            pop_rows = _rows(
                cursor,
                """
                SELECT am.variant_id,MAX(CASE
                  WHEN si.variant_id IS NOT NULL
                   AND cp.last_effective_at>=%s AND cp.last_effective_at<%s
                   AND cp.last_payload_sha256 REGEXP '^[0-9a-f]{64}$'
                  THEN 1 ELSE 0 END
                ) AS current_pop
                FROM market_universe_member am
                INNER JOIN market_universe_lock ul
                  ON ul.id=am.universe_lock_id AND ul.is_current=1
                LEFT JOIN operator_strict_source_identity si
                  ON si.variant_id=am.variant_id AND si.source_code=%s
                LEFT JOIN market_ingest_checkpoint cp
                  ON cp.source_code=%s
                 AND cp.stream_key=CONCAT(am.variant_id,':',si.external_entity_id)
                GROUP BY am.variant_id ORDER BY am.variant_id
                """,
                (
                    start, end,
                    pop_source["identitySourceCode"],
                    pop_source["popCheckpointSourceCode"],
                ),
            )
            missing_pop = [
                int(row["variant_id"])
                for row in pop_rows
                if int(row.get("current_pop") or 0) != 1
            ]
            pop_sections[pop_source["sourceCode"]] = {
                "covered": len(pop_rows) - len(missing_pop),
                "missing": missing_pop,
                "complete": len(pop_rows) == active_count and not missing_pop,
            }

        quote_rows = _rows(
            cursor,
            """
            SELECT am.variant_id,MAX(CASE
              WHEN q.id IS NOT NULL
               AND sr.source_code IS NOT NULL
               AND si.variant_id IS NOT NULL
               AND rp.id IS NOT NULL
               AND q.price_usd>0
               AND q.payload_sha256 REGEXP '^[0-9a-f]{64}$'
               AND q.quote_lineage_sha256 REGEXP '^[0-9a-f]{64}$'
               AND (q.reconstruction_kind IS NULL
                    OR q.reconstruction_kind IN ('bootstrap_from_observation',''))
              THEN 1 ELSE 0 END
            ) AS current_quote
            FROM market_universe_member am
            INNER JOIN market_universe_lock ul
              ON ul.id=am.universe_lock_id AND ul.is_current=1
            INNER JOIN catalog_printing_identity pi ON pi.variant_id=am.variant_id
            LEFT JOIN market_current_quote_revision q
              ON q.variant_id=am.variant_id
             AND q.checked_at>=%s AND q.checked_at<%s
            LEFT JOIN market_source_registry sr
              ON sr.source_code=q.source_code AND sr.enabled=1
             AND JSON_CONTAINS(sr.capabilities_json,JSON_QUOTE('quote'),'$')=1
            LEFT JOIN operator_strict_source_identity si
              ON si.variant_id=q.variant_id
             AND si.source_code=sr.identity_source_code
             AND si.external_entity_id=q.source_external_entity_id
            LEFT JOIN market_quote_route_policy rp
              ON rp.id=(
                SELECT chosen.id FROM market_quote_route_policy chosen
                WHERE chosen.source_code=q.source_code
                  AND chosen.is_active=1 AND chosen.is_eligible=1
                  AND chosen.language_code IN (
                    LOWER(REPLACE(pi.card_language,'_','-')),'*'
                  )
                ORDER BY
                  CASE WHEN chosen.language_code=LOWER(REPLACE(pi.card_language,'_','-'))
                       THEN 0 ELSE 1 END,
                  chosen.activated_at DESC,chosen.policy_version DESC,
                  chosen.priority ASC,chosen.id DESC
                LIMIT 1
              )
            GROUP BY am.variant_id ORDER BY am.variant_id
            """,
            (start, end),
        )
        missing_quote = [
            int(row["variant_id"]) for row in quote_rows
            if int(row.get("current_quote") or 0) != 1
        ]

        from fx_rates import SUPPORTED_CURRENCIES

        fx_count = _scalar(
            cursor,
            """
            SELECT COUNT(DISTINCT quote_currency) AS n
            FROM market_fx_rate_observation
            WHERE base_currency='USD' AND fetched_at>=%s AND fetched_at<%s
            """,
            (start, end),
        )
        expected_fx = len(SUPPORTED_CURRENCIES) - 1
        contract: dict[str, Any] = {
            "businessDate": business_date,
            "windowUtc": {"start": start.isoformat(), "end": end.isoformat()},
            "activeCount": active_count,
            "sources": registry_rows,
            "barrierKeys": list(barrier_keys),
            "popSources": [item["sourceCode"] for item in pop_sources],
            "ratesSources": list(rates_sources),
            "quotes": {
                "covered": len(quote_rows) - len(missing_quote),
                "missing": missing_quote,
                "complete": len(quote_rows) == active_count and not missing_quote,
            },
        }
        contract.update(pop_sections)
        for rates_code in rates_sources:
            contract[rates_code] = {
                "covered": fx_count,
                "expected": expected_fx,
                "complete": fx_count == expected_fx,
            }
        for key in barrier_keys:
            # A core source the registry declares but nothing can measure must
            # block, never silently pass: registering it as quote/extra is the
            # deliberate way to keep it out of the barrier.
            if key not in contract:
                contract[key] = {
                    "covered": 0,
                    "missing": [],
                    "complete": False,
                    "reason": "core source declares no pop or rates capability",
                }
        return contract
    finally:
        connection.close()


def _rows(cursor: Any, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cursor.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def quote_repair_sources(variant_ids: Sequence[int]) -> dict[str, list[int]]:
    """Route each missing variant to its highest-priority strict quote source."""

    return quote_repair_plan(variant_ids)["routes"]


def quote_repair_plan(variant_ids: Sequence[int]) -> dict[str, Any]:
    """Repair routes plus every variant the versioned policy refused.

    The MySQL join proposes a winner; ``quote_repair_routes`` re-runs the
    declared ``select_quote`` policy over the same rows and drops any variant
    where the two disagree, so a drifted ORDER BY cannot quietly repair a card
    from an unproven source.
    """

    requested = sorted({int(value) for value in variant_ids if int(value) > 0})
    if not requested:
        return {"routes": {}, "rejected": []}
    load_env()
    connection = db()
    try:
        cursor = connection.cursor()
        placeholders = ",".join(["%s"] * len(requested))
        rows = _rows(
            cursor,
            f"""
            SELECT pi.variant_id,sr.canonical_source_code AS source_code,
                   rp.priority,rp.policy_version,rp.language_code,
                   LOWER(REPLACE(pi.card_language,'_','-')) AS card_language
            FROM catalog_printing_identity pi
            INNER JOIN market_source_registry sr
              ON sr.enabled=1
             AND JSON_CONTAINS(sr.capabilities_json,JSON_QUOTE('quote'),'$')=1
            INNER JOIN operator_strict_source_identity si
              ON si.variant_id=pi.variant_id
             AND si.source_code=sr.identity_source_code
            INNER JOIN market_quote_route_policy rp
              ON rp.id=(
                SELECT chosen.id FROM market_quote_route_policy chosen
                WHERE chosen.source_code=sr.source_code
                  AND chosen.is_active=1 AND chosen.is_eligible=1
                  AND chosen.language_code IN (
                    LOWER(REPLACE(pi.card_language,'_','-')),'*'
                  )
                ORDER BY
                  CASE WHEN chosen.language_code=LOWER(REPLACE(pi.card_language,'_','-'))
                       THEN 0 ELSE 1 END,
                  chosen.activated_at DESC,chosen.policy_version DESC,
                  chosen.priority ASC,chosen.id DESC
                LIMIT 1
              )
            WHERE pi.variant_id IN ({placeholders})
            ORDER BY pi.variant_id,rp.priority,sr.source_code
            """,
            tuple(requested),
        )
        return quote_repair_routes(rows)
    finally:
        connection.close()


def sync_source_registry(specs: Iterable[Any]) -> dict[str, int]:
    """Upsert registered adapters without requiring another schema migration.

    Alias storage codes remain migration-owned.  A new adapter only needs a
    SourceSpec registration; its canonical and identity codes default to its
    own source code, while route-policy membership remains an explicit policy
    decision.
    """

    spec_list = list(specs)
    load_env()
    connection = db()
    written = 0
    try:
        cursor = connection.cursor()
        for spec in spec_list:
            source_code = str(spec.source_code)
            capabilities = sorted({str(value) for value in spec.capabilities})
            lane = str(getattr(spec, "identity_lane", None) or "").strip() or None
            priority = getattr(spec, "route_priority", None)
            cursor.execute(
                """
                INSERT INTO market_source_registry
                  (source_code,canonical_source_code,identity_source_code,
                   display_name,capabilities_json,transport,concurrency_group,
                   max_concurrency,cadence,freshness_sla_minutes,required_class,
                   identity_lane,route_priority,enabled,config_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,JSON_OBJECT())
                ON DUPLICATE KEY UPDATE
                  canonical_source_code=VALUES(canonical_source_code),
                  identity_source_code=VALUES(identity_source_code),
                  capabilities_json=VALUES(capabilities_json),
                  transport=VALUES(transport),
                  concurrency_group=VALUES(concurrency_group),
                  max_concurrency=VALUES(max_concurrency),
                  cadence=VALUES(cadence),
                  freshness_sla_minutes=VALUES(freshness_sla_minutes),
                  identity_lane=VALUES(identity_lane),
                  route_priority=VALUES(route_priority),
                  required_class=VALUES(required_class),enabled=VALUES(enabled)
                """,
                (
                    source_code, source_code, source_code, source_code,
                    json.dumps(capabilities, ensure_ascii=False),
                    str(spec.transport), str(spec.concurrency_group),
                    int(spec.max_concurrency), str(spec.cadence),
                    int(spec.freshness_sla_minutes), str(spec.required_class),
                    lane, None if priority is None else int(priority),
                    1 if bool(spec.enabled) else 0,
                ),
            )
            written += 1
        # A newly registered quote source used to reach the route policy only
        # through a hand-written migration, so it could never be selected.
        existing_policy = {
            str(row["source_code"])
            for row in _rows(
                cursor,
                "SELECT DISTINCT source_code FROM market_quote_route_policy"
                " WHERE is_active=1",
            )
        }
        policy_rows = route_policy_upserts(
            spec_list,
            existing_source_codes=existing_policy,
            activated_at=datetime.now(timezone.utc)
            .replace(tzinfo=None)
            .strftime("%Y-%m-%d %H:%M:%S.%f"),
        )
        for policy_row in policy_rows:
            cursor.execute(
                """
                INSERT INTO market_quote_route_policy
                  (policy_version,language_code,source_code,priority,
                   is_eligible,is_active,activated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  priority=VALUES(priority),is_eligible=VALUES(is_eligible),
                  is_active=VALUES(is_active)
                """,
                policy_row,
            )
        connection.commit()
        return {"registered": written, "routePolicyRows": len(policy_rows)}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def sync_variant_source_states(run_id: str, business_date: str) -> dict[str, int]:
    """Project today's independent provider facts into the generic state table."""

    start, end = business_window_utc(business_date)
    load_env()
    connection = db()
    quotes_written = 0
    pop_written = 0
    try:
        cursor = connection.cursor()
        quote_rows = _rows(
            cursor,
            """
            SELECT ranked.* FROM (
              SELECT q.*,sr.canonical_source_code,
                     ROW_NUMBER() OVER (
                       PARTITION BY q.variant_id,sr.canonical_source_code
                       ORDER BY q.checked_at DESC,q.id DESC
                     ) AS rn
              FROM market_current_quote_revision q
              INNER JOIN market_source_registry sr
                ON sr.source_code=q.source_code AND sr.enabled=1
               AND JSON_CONTAINS(sr.capabilities_json,JSON_QUOTE('quote'),'$')=1
              INNER JOIN market_universe_member am ON am.variant_id=q.variant_id
              INNER JOIN market_universe_lock ul
                ON ul.id=am.universe_lock_id AND ul.is_current=1
              WHERE q.checked_at>=%s AND q.checked_at<%s
            ) ranked WHERE ranked.rn=1
            """,
            (start, end),
        )
        for row in quote_rows:
            cursor.execute(
                """
                INSERT INTO market_variant_source_state
                  (business_date,run_id,variant_id,source_code,capability,status,
                   observed_at,checked_at,usd_value,payload_sha256,evidence_ref,
                   selected_quote_revision_id,is_selected,source_switched,detail_json)
                VALUES (%s,%s,%s,%s,'quote','completed',%s,%s,%s,%s,%s,%s,0,0,%s)
                ON DUPLICATE KEY UPDATE run_id=VALUES(run_id),status=VALUES(status),
                  observed_at=VALUES(observed_at),checked_at=VALUES(checked_at),
                  usd_value=VALUES(usd_value),payload_sha256=VALUES(payload_sha256),
                  evidence_ref=VALUES(evidence_ref),
                  selected_quote_revision_id=VALUES(selected_quote_revision_id),
                  detail_json=VALUES(detail_json)
                """,
                (
                    business_date, run_id, int(row["variant_id"]),
                    str(row["canonical_source_code"]), row["source_period_at"],
                    row["checked_at"], row["price_usd"], row["payload_sha256"],
                    f"market_current_quote_revision:{int(row['id'])}", int(row["id"]),
                    json.dumps({"storageSourceCode": row["source_code"]}),
                ),
            )
            quotes_written += 1

        # Same registry loop as the barrier: the POP projection must cover every
        # core pop source, not the one that happened to exist first.
        pop_sources = pop_contract_sources(
            _rows(
                cursor,
                """
                SELECT source_code,canonical_source_code,identity_source_code,
                       required_class,enabled,capabilities_json,config_json
                FROM market_source_registry ORDER BY source_code
                """,
            )
        ) or (
            {
                "sourceCode": "gemrate",
                "identitySourceCode": "gemrate",
                "popCheckpointSourceCode": "gemrate_pop",
            },
        )
        for pop_source in pop_sources:
            checkpoint_code = pop_source["popCheckpointSourceCode"]
            pop_rows = _rows(
                cursor,
                """
                SELECT am.variant_id,si.external_entity_id,cp.last_effective_at,
                       cp.last_payload_sha256,cp.last_run_id
                FROM market_universe_member am
                INNER JOIN market_universe_lock ul
                  ON ul.id=am.universe_lock_id AND ul.is_current=1
                INNER JOIN operator_strict_source_identity si
                  ON si.variant_id=am.variant_id AND si.source_code=%s
                INNER JOIN market_ingest_checkpoint cp
                  ON cp.source_code=%s
                 AND cp.stream_key=CONCAT(am.variant_id,':',si.external_entity_id)
                WHERE cp.last_effective_at>=%s AND cp.last_effective_at<%s
                """,
                (
                    pop_source["identitySourceCode"], checkpoint_code, start, end,
                ),
            )
            for row in pop_rows:
                cursor.execute(
                    """
                    INSERT INTO market_variant_source_state
                      (business_date,run_id,variant_id,source_code,capability,status,
                       observed_at,checked_at,payload_sha256,evidence_ref,is_selected,
                       source_switched,detail_json)
                    VALUES (%s,%s,%s,%s,'pop','completed',%s,%s,%s,%s,0,0,%s)
                    ON DUPLICATE KEY UPDATE run_id=VALUES(run_id),status=VALUES(status),
                      observed_at=VALUES(observed_at),checked_at=VALUES(checked_at),
                      payload_sha256=VALUES(payload_sha256),evidence_ref=VALUES(evidence_ref),
                      detail_json=VALUES(detail_json)
                    """,
                    (
                        business_date, run_id, int(row["variant_id"]),
                        pop_source["sourceCode"],
                        row["last_effective_at"], row["last_effective_at"],
                        row["last_payload_sha256"],
                        f"market_ingest_checkpoint:{checkpoint_code}"
                        f":{row['variant_id']}:{row['external_entity_id']}",
                        json.dumps({"ingestRunId": int(row["last_run_id"])}),
                    ),
                )
                pop_written += 1
        connection.commit()
        return {"quotesWritten": quotes_written, "popWritten": pop_written}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def post_accept_contract(
    business_date: str, generation_id: str, run_id: str,
) -> dict[str, Any]:
    load_env()
    connection = db()
    try:
        cursor = connection.cursor()
        active_count = _scalar(
            cursor,
            """
            SELECT COUNT(*) AS n FROM market_universe_member am
            INNER JOIN market_universe_lock ul
              ON ul.id=am.universe_lock_id AND ul.is_current=1
            """,
        )
        selected = _scalar(
            cursor,
            """
            SELECT COUNT(DISTINCT variant_id) AS n FROM market_variant_source_state
            WHERE business_date=%s AND run_id=%s
              AND capability='canonical_quote' AND is_selected=1
              AND selected_quote_revision_id IS NOT NULL
              AND selected_policy_version IS NOT NULL
            """,
            (business_date, run_id),
        )
        cursor.execute(
            """
            SELECT ranking_generation_sha256,MAX(accepted_at) AS accepted_at,
                   COUNT(*) AS n
            FROM market_canonical_metric_acceptance
            WHERE ranking_generation_sha256=%s
            GROUP BY ranking_generation_sha256
            """,
            (generation_id,),
        )
        generation = cursor.fetchone()
        return {
            "activeCount": active_count,
            "selectedCount": selected,
            "selectionComplete": active_count > 0 and selected == active_count,
            "generation": None if generation is None else str(generation["ranking_generation_sha256"]),
            "acceptedCount": 0 if generation is None else int(generation["n"]),
            "acceptedAt": None if generation is None else str(generation["accepted_at"]),
            "generationComplete": (
                generation is not None and int(generation["n"]) == active_count
            ),
        }
    finally:
        connection.close()


def recover_daily_accept(run_id: str, business_date: str) -> dict[str, Any] | None:
    """Recover the exact committed V2 acceptance after a receipt crash.

    Canonical selections and metric acceptances commit in the same MySQL
    transaction.  Their run/date/generation lineage is therefore a stronger
    recovery point than a filesystem receipt that may have been interrupted
    immediately after COMMIT.
    """

    load_env()
    connection = db()
    try:
        cursor = connection.cursor()
        active_count = _scalar(
            cursor,
            """
            SELECT COUNT(*) AS n FROM market_universe_member am
            INNER JOIN market_universe_lock ul
              ON ul.id=am.universe_lock_id AND ul.is_current=1
            """,
        )
        cursor.execute(
            """
            SELECT COUNT(*) AS selected_count,
                   COUNT(DISTINCT JSON_UNQUOTE(JSON_EXTRACT(
                     detail_json,'$.rankingGenerationSha256'))) AS generation_count,
                   MIN(JSON_UNQUOTE(JSON_EXTRACT(
                     detail_json,'$.rankingGenerationSha256'))) AS generation_sha256,
                   COUNT(DISTINCT JSON_UNQUOTE(JSON_EXTRACT(
                     detail_json,'$.contentSha256'))) AS content_count,
                   MIN(JSON_UNQUOTE(JSON_EXTRACT(
                     detail_json,'$.contentSha256'))) AS content_sha256,
                   MAX(JSON_UNQUOTE(JSON_EXTRACT(
                     detail_json,'$.acceptedAt'))) AS accepted_at
            FROM market_variant_source_state
            WHERE business_date=%s AND run_id=%s
              AND capability='canonical_quote' AND is_selected=1
              AND selected_quote_revision_id IS NOT NULL
              AND selected_policy_version IS NOT NULL
            """,
            (business_date, run_id),
        )
        row = dict(cursor.fetchone() or {})
        generation_sha = str(row.get("generation_sha256") or "")
        content_sha = str(row.get("content_sha256") or "")
        if (
            active_count < 1
            or int(row.get("selected_count") or 0) != active_count
            or int(row.get("generation_count") or 0) != 1
            or int(row.get("content_count") or 0) != 1
            or not re.fullmatch(r"[0-9a-f]{64}", generation_sha)
            or not re.fullmatch(r"[0-9a-f]{64}", content_sha)
            or not str(row.get("accepted_at") or "")
        ):
            return None
        cursor.execute(
            """
            SELECT COUNT(*) AS n,MAX(accepted_at) AS accepted_at
            FROM market_canonical_metric_acceptance
            WHERE ranking_generation_sha256=%s
            """,
            (generation_sha,),
        )
        accepted = dict(cursor.fetchone() or {})
        if int(accepted.get("n") or 0) != active_count:
            return None
        return {
            "generationSha256": generation_sha,
            "publicGenerationId": f"db3308_{generation_sha[:16]}",
            "contentSha256": content_sha,
            "activeCount": active_count,
            "acceptedAt": str(row["accepted_at"]),
            "acceptedCount": int(accepted["n"]),
        }
    finally:
        connection.close()


def read_snapshot(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("published snapshot is not an object")
    return value


def fetch_live_health(url: str = LIVE_HEALTH_URL, *, timeout: float = 20.0) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Cache-Control": "no-cache",
            "User-Agent": "CARDZ-Daily-Chain-V2/1",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError("live health is not an object")
    return value


def validate_live_readback(snapshot: Mapping[str, Any], health: Mapping[str, Any]) -> dict[str, str]:
    generation = snapshot.get("generation")
    if not isinstance(generation, Mapping):
        raise RuntimeError("snapshot generation is missing")
    expected_id = str(generation.get("id") or "")
    expected_at = str(generation.get("generatedAt") or "")
    if not expected_id or not expected_at:
        raise RuntimeError("snapshot generation id/generatedAt is incomplete")
    if (
        str(health.get("status") or "") != "ok"
        or str(health.get("generation") or "") != expected_id
        or str(health.get("generatedAt") or "") != expected_at
    ):
        raise RuntimeError(
            "live generation mismatch:"
            f" expected={expected_id}@{expected_at}"
            f" actual={health.get('generation')}@{health.get('generatedAt')}"
        )
    return {"generationId": expected_id, "generatedAt": expected_at}


def build_live_event(
    *,
    business_date: str,
    run_id: str,
    snapshot: Mapping[str, Any],
    health: Mapping[str, Any],
    active_count: int,
    content_sha256: str,
    source_health: Mapping[str, Any],
    degraded_sources: Iterable[str],
    live_url: str = LIVE_URL,
) -> dict[str, Any]:
    readback = validate_live_readback(snapshot, health)
    if not re.fullmatch(r"[0-9a-f]{64}", str(content_sha256)):
        raise RuntimeError("accepted semantic content sha256 is invalid")
    degraded = sorted(set(str(value) for value in degraded_sources))
    event = {
        "eventKey": f"live.confirmed:{business_date}",
        "eventType": "live.confirmed",
        "businessDate": business_date,
        "runId": run_id,
        "generationId": readback["generationId"],
        "generatedAt": readback["generatedAt"],
        "activeCount": int(active_count),
        "degraded": bool(degraded),
        "degradedSources": degraded,
        "sourceHealth": dict(source_health),
        "liveUrl": live_url,
        # This is the daily-accept semantic market hash: timestamps do not make
        # unchanged market values look changed.  Keep the immutable artifact
        # hash separately so lineage and semantic equality are both explicit.
        "contentSha256": str(content_sha256),
        "artifactContentSha256": sha256(snapshot),
        "occurredAt": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
    }
    event["payloadSha256"] = sha256(event)
    return event


def recover_live_event(
    *,
    business_date: str,
    run_id: str,
    expected_generation: str,
    expected_content_sha256: str | None = None,
    expected_active_count: int | None = None,
) -> dict[str, Any] | None:
    """Recover one already-committed immutable outbox event.

    This is the publication equivalent of ``recover_daily_accept``: the MySQL
    row is authoritative when a worker dies after COMMIT but before its SQLite
    or filesystem receipt is durable.
    """

    event_key = f"live.confirmed:{business_date}"
    load_env()
    connection = db()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT id,event_type,business_date,run_id,generation_id,generated_at,
                   active_count,content_sha256,payload_json,occurred_at
            FROM publication_outbox WHERE event_key=%s
            """,
            (event_key,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        raw_payload = row.get("payload_json")
        if isinstance(raw_payload, Mapping):
            payload = dict(raw_payload)
        elif isinstance(raw_payload, (bytes, bytearray)):
            payload = json.loads(bytes(raw_payload).decode("utf-8"))
        else:
            payload = json.loads(str(raw_payload))
        if not isinstance(payload, dict):
            raise RuntimeError("live.confirmed recovery payload is not an object")
        unsigned = dict(payload)
        recorded_payload_sha = str(unsigned.pop("payloadSha256", ""))
        mismatches = []
        for name, actual, expected in (
            ("eventType", row.get("event_type"), "live.confirmed"),
            ("businessDate", str(row.get("business_date")), business_date),
            ("runId", row.get("run_id"), run_id),
            ("generation", row.get("generation_id"), expected_generation),
            ("payloadEventKey", payload.get("eventKey"), event_key),
            ("payloadEventType", payload.get("eventType"), "live.confirmed"),
            ("payloadBusinessDate", payload.get("businessDate"), business_date),
            ("payloadRunId", payload.get("runId"), run_id),
            ("payloadGeneration", payload.get("generationId"), expected_generation),
            ("payloadGeneratedAt", _mysql_datetime(payload.get("generatedAt")), row.get("generated_at")),
            ("payloadActiveCount", int(payload.get("activeCount") or 0), int(row.get("active_count") or 0)),
            ("payloadContentSha256", payload.get("contentSha256"), row.get("content_sha256")),
            ("payloadOccurredAt", _mysql_datetime(payload.get("occurredAt")), row.get("occurred_at")),
            ("payloadSha256", recorded_payload_sha, sha256(unsigned)),
        ):
            if str(actual) != str(expected):
                mismatches.append(f"{name}:{actual}!={expected}")
        if expected_content_sha256 is not None and str(row.get("content_sha256")) != str(
            expected_content_sha256
        ):
            mismatches.append("contentSha256")
        if expected_active_count is not None and int(row.get("active_count") or 0) != int(
            expected_active_count
        ):
            mismatches.append("activeCount")
        if mismatches:
            raise RuntimeError(
                "live.confirmed recovery contract mismatch: " + ",".join(mismatches)
            )
        return {"eventId": int(row["id"]), "inserted": False, "event": payload}
    finally:
        connection.close()


def insert_live_event(event: Mapping[str, Any]) -> tuple[int, bool]:
    event_key = str(event["eventKey"])
    if not re.fullmatch(r"live\.confirmed:\d{4}-\d{2}-\d{2}", event_key):
        raise ValueError("invalid live.confirmed event key")
    load_env()
    connection = db()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT id,generation_id FROM publication_outbox WHERE event_key=%s FOR UPDATE",
            (event_key,),
        )
        existing = cursor.fetchone()
        if existing is not None:
            if str(existing["generation_id"]) != str(event["generationId"]):
                raise RuntimeError("live.confirmed key already belongs to another generation")
            connection.commit()
            return int(existing["id"]), False
        cursor.execute(
            """
            INSERT INTO publication_outbox
              (event_key,event_type,business_date,run_id,generation_id,generated_at,
               active_count,degraded,source_health_json,live_url,payload_json,
               content_sha256,occurred_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                event_key, event["eventType"], event["businessDate"], event["runId"],
                event["generationId"], _mysql_datetime(event["generatedAt"]),
                int(event["activeCount"]),
                1 if event.get("degraded") else 0,
                json.dumps(event["sourceHealth"], ensure_ascii=False), event["liveUrl"],
                json.dumps(dict(event), ensure_ascii=False), event["contentSha256"],
                _mysql_datetime(event["occurredAt"]),
            ),
        )
        event_id = int(cursor.lastrowid)
        connection.commit()
        return event_id, True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _mysql_datetime(value: Any) -> datetime:
    """Convert ISO timestamps to an unambiguous UTC-naive MySQL DATETIME."""

    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def ensure_delivery(event_id: int, consumer_code: str) -> None:
    """Register a consumer only when that delivery channel is explicitly enabled."""

    load_env()
    connection = db()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO publication_delivery(event_id,consumer_code,status)
            VALUES (%s,%s,'pending')
            ON DUPLICATE KEY UPDATE event_id=VALUES(event_id)
            """,
            (int(event_id), str(consumer_code)),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def mark_delivery(
    event_id: int,
    consumer_code: str,
    *,
    delivered: bool,
    receipt: Mapping[str, Any] | None = None,
    error_code: str | None = None,
    error_text: str | None = None,
) -> None:
    load_env()
    connection = db()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE publication_delivery
            SET status=%s,attempt_count=attempt_count+1,
                delivered_at=CASE WHEN %s=1 THEN UTC_TIMESTAMP(6) ELSE delivered_at END,
                receipt_json=%s,error_code=%s,error_text=%s
            WHERE event_id=%s AND consumer_code=%s
            """,
            (
                "delivered" if delivered else "failed", 1 if delivered else 0,
                None if receipt is None else json.dumps(dict(receipt), ensure_ascii=False),
                error_code, error_text, int(event_id), consumer_code,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("publication delivery row is missing")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


class MemoryOutbox:
    """Fixture outbox with the same one-event-per-business-date invariant."""

    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}

    def insert(self, event: Mapping[str, Any]) -> bool:
        key = str(event["eventKey"])
        previous = self.events.get(key)
        if previous is not None:
            if previous["generationId"] != event["generationId"]:
                raise RuntimeError("event key collision")
            return False
        self.events[key] = dict(event)
        return True
