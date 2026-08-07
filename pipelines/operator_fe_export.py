#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FE-facing projection helpers for operator_control export."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any

WINDOW_DEFS = {"1d": (1, 2), "7d": (7, 3), "30d": (30, 5)}
_PRODUCT_CACHE: dict[tuple[int, ...], dict[int, dict[str, Any]]] = {}
_025_SNAPSHOT_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "public"
    / "generations"
    / "product_subset_20260804T151703Z"
    / "seed-snapshot.json"
)
_025_HISTORY_BY_CARD_ID: dict[str, list[dict[str, Any]]] | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def metric(value, status, as_of, **extra):
    out = {"value": value, "status": status, "asOf": as_of}
    out.update(extra)
    return out


def iso_day(d) -> str | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    s = str(d)
    return s[:10] if s else None


def asof_iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(v, date):
        return f"{v.isoformat()}T00:00:00Z"
    s = str(v).replace(" ", "T")
    if s.endswith("+00:00"):
        s = s[:-6] + "Z"
    if len(s) == 10:
        s += "T00:00:00Z"
    return s


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str) and value.strip():
        parsed = json.loads(value)
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def nearest_day_price(by_day: dict[str, dict], target: date, tol_days: int):
    best = None
    best_delta = None
    for day_s, row in by_day.items():
        try:
            d = date.fromisoformat(day_s)
        except Exception:
            continue
        delta = abs((d - target).days)
        if delta > tol_days:
            continue
        if best_delta is None or delta < best_delta or (delta == best_delta and d > best[0]):
            best = (d, row)
            best_delta = delta
    if not best:
        return None, None, None
    d, row = best
    return float(row["price_usd"]), asof_iso(row.get("effective_at") or d), d


def pct_change(new, old):
    if new is None or old is None or old == 0:
        return None
    return (new / old - 1.0) * 100.0


def sales_window(sales_by_day: dict[str, dict], end: date, days: int):
    start = end - timedelta(days=days - 1)
    total_val = 0.0
    total_cnt = 0
    has = False
    last = None
    latest_evidence = None
    for day_s, row in sales_by_day.items():
        try:
            d = date.fromisoformat(day_s)
        except Exception:
            continue
        if start <= d <= end:
            if str(row.get("coverage") or "unavailable") == "unavailable":
                continue
            has = True
            total_val += float(row.get("value") or 0)
            total_cnt += int(row.get("count") or 0)
            last = max(last or d, d)
            evidence_at = asof_iso(row.get("asOf"))
            if evidence_at:
                latest_evidence = max(latest_evidence or evidence_at, evidence_at)
    if not has:
        return None, None, None
    return total_val, total_cnt, latest_evidence or asof_iso(last)


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def _fact_lineage_sha256(*parts: Any) -> str:
    return hashlib.sha256(
        "|".join("" if part is None else str(part) for part in parts).encode("utf-8")
    ).hexdigest()


def _latest_original_value(*values: Any) -> Any:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return max(present, key=lambda value: asof_iso(value) or "")


def _load_025_history_by_card_id() -> dict[str, list[dict[str, Any]]]:
    global _025_HISTORY_BY_CARD_ID
    if _025_HISTORY_BY_CARD_ID is None:
        with _025_SNAPSHOT_PATH.open("r", encoding="utf-8") as handle:
            snapshot = json.load(handle)
        cards = [
            *(snapshot.get("top100") or []),
            *(snapshot.get("watchlist") or []),
        ]
        _025_HISTORY_BY_CARD_ID = {
            str(card["id"]): [dict(point) for point in card.get("historyDaily") or []]
            for card in cards
        }
    return _025_HISTORY_BY_CARD_ID


def _merge_025_history(
    card_id: str, current_history: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_day = {
        day: point
        for point in current_history
        if (day := iso_day(point.get("at"))) is not None
    }
    for point in _load_025_history_by_card_id().get(card_id, []):
        day = iso_day(point.get("at"))
        if day is not None and day not in by_day:
            by_day[day] = point
    return [by_day[day] for day in sorted(by_day)]


def _load_daily_and_fx(
    cur,
    vids: list[int],
    current_source_by_variant: dict[int, str] | None = None,
) -> dict[str, Any]:
    ph = ",".join(["%s"] * len(vids))
    daily_by_key: dict[tuple[int, str], dict[str, Any]] = {}

    # The current canonical metric acceptance already selected the exact
    # provider identity.  History must use that same accepted lineage, not
    # re-compare its historical evidence hash with a later binding rewrite.
    # Routing and same-day winner selection happen in Python so MySQL never
    # expands a nested accepted-history view.
    cur.execute(
        f"""
        SELECT
          history.id AS price_history_acceptance_id,
          price.variant_id,price.observed_date,price.price_usd,
          CASE WHEN price.source_code IN ('snk','snk_psa10') THEN 'snkrdunk'
               ELSE price.source_code END AS price_source_code,
          price.effective_at AS price_effective_at,
          history.lineage_sha256 AS price_lineage_sha256
        FROM market_metric_history_acceptance history
        INNER JOIN market_price_observation price
          ON history.source_record_type='market_price_observation'
         AND history.source_record_id=price.id
         AND history.variant_id=price.variant_id
         AND history.observed_date=price.observed_date
         AND history.source_effective_at=price.effective_at
         AND history.external_entity_id=price.source_external_entity_id
         AND history.source_payload_sha256=price.payload_sha256
        WHERE price.variant_id IN ({ph})
          AND history.metric_kind='psa10_price'
          AND price.price_usd>0
          AND history.identity_evidence_sha256 REGEXP '^[0-9a-f]{{64}}$'
          AND history.acceptance_evidence_sha256 REGEXP '^[0-9a-f]{{64}}$'
          AND history.lineage_sha256 REGEXP '^[0-9a-f]{{64}}$'
        """,
        tuple(vids),
    )
    price_candidates = [dict(row) for row in cur.fetchall()]
    price_winners: dict[tuple[int, str], dict[str, Any]] = {}
    for row in price_candidates:
        variant_id = int(row["variant_id"])
        source = str(row.get("price_source_code") or "")
        day = iso_day(row.get("observed_date"))
        if not day:
            continue
        key = (variant_id, day)
        current_source = (current_source_by_variant or {}).get(variant_id, "")
        winner_key = (
            0 if source == current_source else 1 if source == "snkrdunk" else 2 if source == "pricecharting" else 3,
            asof_iso(row.get("price_effective_at")) or "",
            int(row.get("price_history_acceptance_id") or 0),
        )
        previous = price_winners.get(key)
        if previous is None or winner_key[0] < previous[0][0] or (
            winner_key[0] == previous[0][0] and winner_key[1:] > previous[0][1:]
        ):
            price_winners[key] = (winner_key, row)
    for key, (_, row) in price_winners.items():
        daily_by_key.setdefault(key, {}).update(row)

    # Inline 028 exact GemRate population rows and choose its same-day winner.
    cur.execute(
        f"""
        SELECT
          history.id AS population_history_acceptance_id,
          population.variant_id,population.observed_date,
          population.top_grade_population AS psa10_population,
          population.effective_at AS population_effective_at,
          history.lineage_sha256 AS population_lineage_sha256
        FROM market_metric_history_acceptance history
        INNER JOIN market_grader_population_observation population
          ON history.source_record_type='market_grader_population_observation'
         AND history.source_record_id=population.id
         AND history.variant_id=population.variant_id
         AND history.observed_date=population.observed_date
         AND history.source_effective_at=population.effective_at
         AND history.source_code=population.source_code
         AND history.external_entity_id=population.external_entity_id
         AND history.source_payload_sha256=population.payload_sha256
        WHERE population.variant_id IN ({ph})
          AND history.metric_kind='psa10_population'
          AND history.source_code='gemrate' AND population.source_code='gemrate'
          AND UPPER(population.grader_code)='PSA'
          AND UPPER(REPLACE(population.top_grade_label,' ','')) IN ('10','10.0','PSA10','GEMMINT10')
          AND population.estimated=0
          AND history.identity_evidence_sha256 REGEXP '^[0-9a-f]{{64}}$'
          AND history.acceptance_evidence_sha256 REGEXP '^[0-9a-f]{{64}}$'
          AND history.lineage_sha256 REGEXP '^[0-9a-f]{{64}}$'
        """,
        tuple(vids),
    )
    population_winners: dict[tuple[int, str], tuple[tuple[Any, ...], dict[str, Any]]] = {}
    for raw in cur.fetchall():
        row = dict(raw)
        day = iso_day(row.get("observed_date"))
        if not day:
            continue
        key = (int(row["variant_id"]), day)
        winner_key = (
            asof_iso(row.get("population_effective_at")) or "",
            int(row.get("population_history_acceptance_id") or 0),
        )
        previous = population_winners.get(key)
        if previous is None or winner_key > previous[0]:
            population_winners[key] = (winner_key, row)
    for key, (_, row) in population_winners.items():
        daily_by_key.setdefault(key, {}).update(row)

    # Sales is already materially cheaper than the price/pop views.  Keep its
    # accepted aggregate for this bounded repair; it remains a separate query.
    cur.execute(
        f"""
        SELECT variant_id,observed_date,sales_count,sales_value_usd,
          sales_coverage_status,sales_verified_zero,sales_evidence_at,
          sales_history_acceptance_id,sales_history_acceptance_ids,sales_lineage_sha256
        FROM operator_accepted_psa10_sales_history
        WHERE variant_id IN ({ph})
        ORDER BY variant_id,observed_date
        """,
        tuple(vids),
    )
    for raw in cur.fetchall():
        row = dict(raw)
        day = iso_day(row.get("observed_date"))
        if day:
            daily_by_key.setdefault((int(row["variant_id"]), day), {}).update(row)

    daily: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for (variant_id, observed_day), source in sorted(daily_by_key.items()):
        price = source.get("price_usd")
        population = source.get("psa10_population")
        market_cap = Decimal(str(price)) * Decimal(str(population)) if price is not None and population is not None else None
        sales_available = source.get("sales_history_acceptance_id") is not None
        fact_effective_at = _latest_original_value(source.get("price_effective_at"), source.get("population_effective_at"), source.get("sales_evidence_at"))
        row = {
            "variant_id": variant_id,"observed_date": date.fromisoformat(observed_day),
            "price_usd": price,"price_status": "ready" if price is not None else "unavailable",
            "price_effective_at": source.get("price_effective_at"),
            "price_source_observed_at": source.get("price_source_observed_at"),
            "market_cap_usd": market_cap,
            "sales_count": source.get("sales_count") if sales_available else None,
            "sales_value_usd": source.get("sales_value_usd") if sales_available else None,
            "sales_coverage_status": str(source.get("sales_coverage_status") or "unavailable") if sales_available else "unavailable",
            "sales_verified_zero": int(source.get("sales_verified_zero") or 0) if sales_available else 0,
            "sales_evidence_at": source.get("sales_evidence_at") if sales_available else None,
            "fact_effective_at": fact_effective_at,
            "price_history_acceptance_id": source.get("price_history_acceptance_id"),
            "population_history_acceptance_id": source.get("population_history_acceptance_id"),
            "sales_history_acceptance_id": source.get("sales_history_acceptance_id"),
            "sales_history_acceptance_ids": source.get("sales_history_acceptance_ids"),
        }
        row["fact_lineage_sha256"] = _fact_lineage_sha256(
            "accepted-daily-v1",variant_id,observed_day,row.get("price_history_acceptance_id"),
            row.get("population_history_acceptance_id"),row.get("sales_history_acceptance_ids"),
            source.get("price_lineage_sha256"),source.get("population_lineage_sha256"),source.get("sales_lineage_sha256"),
        )
        daily[variant_id].append(row)

    cur.execute(
        """
        SELECT f.quote_currency,f.rate,f.effective_at
        FROM market_fx_rate_observation f
        INNER JOIN (SELECT quote_currency,MAX(effective_at) mx FROM market_fx_rate_observation
          WHERE base_currency='USD' GROUP BY quote_currency) t
          ON t.quote_currency=f.quote_currency AND t.mx=f.effective_at
        WHERE f.base_currency='USD'
        """
    )
    return {"daily": daily, "fx": {str(row["quote_currency"]).upper(): row for row in cur.fetchall()}}


def load_active_members(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
          member.variant_id,
          member.universe_lock_id,
          metric.canonical_market_rank AS market_rank,
          member.member_role,
          member.segment_code
        FROM market_universe_member member
        INNER JOIN (
          SELECT id
          FROM market_universe_lock
          WHERE is_current=1
          ORDER BY effective_at DESC,id DESC
          LIMIT 1
        ) current_lock ON current_lock.id=member.universe_lock_id
        LEFT JOIN market_canonical_metric_acceptance metric
          ON metric.variant_id=member.variant_id
        LEFT JOIN market_canonical_metric_acceptance newer_metric
          ON newer_metric.supersedes_acceptance_id=metric.id
        WHERE newer_metric.id IS NULL
        ORDER BY COALESCE(metric.canonical_market_rank, 4294967295),member.variant_id
        """
    )
    return [dict(row) for row in cur.fetchall()]


def load_bulk(
    cur, vids: list[int], include_daily: bool = True
) -> dict[str, Any]:
    empty = {"product": {}, "daily": {}, "fx": {}}
    if not vids:
        return empty
    cache_key = tuple(int(vid) for vid in vids)
    ph = ",".join(["%s"] * len(vids))

    cached_product = _PRODUCT_CACHE.get(cache_key)
    if cached_product is not None:
        product = cached_product
        if include_daily:
            return {
                "product": product,
                **_load_daily_and_fx(
                    cur,
                    vids,
                    {
                        variant_id: str(row.get("psa10_price_source_code") or "")
                        for variant_id, row in product.items()
                    },
                ),
            }
        return {"product": product, "daily": {}, "fx": {}}
    else:
        product = {}

    # The public product view also carries latest-daily and RAW-reference legs.
    # MySQL 5.7 expands both legs even when the exporter does not select their
    # columns.  Read each canonical authority independently and merge by the
    # immutable variant id instead.
    if cached_product is None:
        cur.execute(
        f"""
        SELECT
          variant.id AS variant_id,
          variant.opaque_id,
          printing.variant_id AS printing_variant_id,
          printing.tcg_code,
          printing.card_language,
          printing.collector_number,
          printing.set_name AS canonical_set_name,
          printing.edition_code,
          printing.set_code,
          printing.finish_code,
          printing.printing_code,
          printing.identity_status,
          printing.canonical_printing_sha256,
          printing.evidence_sha256 AS printing_evidence_sha256,
          printing.provenance_json AS printing_provenance_json,
          printing.observed_at AS printing_observed_at,
          identity_freeze.identity_accepted_at
        FROM catalog_variant variant
        LEFT JOIN catalog_printing_identity printing
          ON printing.variant_id=variant.id
        LEFT JOIN operator_canonical_identity_freeze_projection identity_freeze
          ON identity_freeze.variant_id=variant.id
        WHERE variant.id IN ({ph})
        """,
        tuple(vids),
    )
        product = {}
        for raw in cur.fetchall():
            row = dict(raw)
            row.update(
            {
                "official_full_name": None,
                "official_name_acceptance_id": None,
                "official_name_evidence_sha256": None,
                "official_name_observed_at": None,
                "localized_names_json": {
                    locale: None for locale in ("en", "zhTW", "zhCN", "ja", "ko")
                },
                "localized_set_names_json": {
                    locale: None for locale in ("en", "zhTW", "zhCN", "ja", "ko")
                },
                "localized_stories_json": {
                    locale: None for locale in ("en", "zhTW", "zhCN", "ja", "ko")
                },
                "locale_latest_observed_at": None,
                "exact_source_codes": None,
                "psa10_price_usd": None,
                "psa10_price_effective_at": None,
                "psa10_price_source_observed_at": None,
                "psa10_population": None,
                "population_effective_at": None,
                "market_cap_usd": None,
                "canonical_market_rank": None,
                "metric_lineage_sha256": None,
                "psa10_price_source_code": None,
                "selected_price_route_priority": None,
                "eligible_pricecharting_exists": None,
                "psa10_price_external_entity_id": None,
                "population_source_code": None,
                "population_external_entity_id": None,
                "ungraded_reference_price_usd": None,
                "ungraded_reference_observed_at": None,
                "image_content_sha256": None,
                "image_width_px": None,
                "image_height_px": None,
                "image_qc_at": None,
                "image_source_path": None,
                "image_source_observed_at": None,
                "canonical_image_content_sha256": None,
                "canonical_image_width": None,
                "canonical_image_height": None,
                "canonical_image_qc_at": None,
                "canonical_image_source_path": None,
                "canonical_image_source_observed_at": None,
                "canonical_image_acceptance_id": None,
                "canonical_image_lineage_sha256": None,
                "image_alt_json": {
                    locale: None for locale in ("en", "zhTW", "zhCN", "ja", "ko")
                },
                "identity_complete": 0,
                "official_name_complete": 0,
                "canonical_metric_complete": 0,
                "canonical_image_complete": 0,
                "canonical_rank_complete": 0,
                "product_ready": 0,
            }
            )
            product[int(row["variant_id"])] = row

    cur.execute(
        f"""
        SELECT variant_id,locale_code,localized_name,localized_set_name,market_story,observed_at
        FROM catalog_variant_locale
        WHERE variant_id IN ({ph})
          AND locale_code IN ('en','zhTW','zhCN','ja','ko')
        ORDER BY variant_id,locale_code
        """,
        tuple(vids),
    )
    for raw in cur.fetchall():
        row = dict(raw)
        target = product.get(int(row["variant_id"]))
        if target is None:
            continue
        locale = str(row["locale_code"])
        target["localized_names_json"][locale] = row.get("localized_name")
        target["localized_set_names_json"][locale] = row.get("localized_set_name")
        target["localized_stories_json"][locale] = row.get("market_story")
        target["locale_latest_observed_at"] = _latest_original_value(
            target.get("locale_latest_observed_at"), row.get("observed_at")
        )

    cur.execute(
        f"""
        SELECT
          identity.variant_id,
          GROUP_CONCAT(DISTINCT identity.source_code
            ORDER BY identity.source_code SEPARATOR ',') AS exact_source_codes
        FROM operator_strict_source_identity identity
        WHERE identity.variant_id IN ({ph})
        GROUP BY identity.variant_id
        """,
        tuple(vids),
    )
    for raw in cur.fetchall():
        row = dict(raw)
        if int(row["variant_id"]) in product:
            product[int(row["variant_id"])]["exact_source_codes"] = row.get(
                "exact_source_codes"
            )

    # Promotion reads the already accepted canonical rows.  A 031 source
    # binding refresh can legitimately give the same physical identity a new
    # evidence hash; it must not make a previously accepted name disappear.
    cur.execute(
        f"""
        SELECT a.variant_id,a.official_full_name,
          a.id AS official_name_acceptance_id,
          a.evidence_sha256 AS official_name_evidence_sha256,
          a.source_observed_at AS official_name_observed_at
        FROM catalog_official_name_acceptance a
        INNER JOIN catalog_printing_identity printing
          ON printing.variant_id=a.variant_id
         AND printing.canonical_printing_sha256=a.canonical_printing_sha256
        LEFT JOIN catalog_official_name_acceptance newer
          ON newer.supersedes_acceptance_id=a.id
        WHERE a.variant_id IN ({ph})
          AND LOWER(a.source_code) IN ('gemrate','psa')
          AND TRIM(a.official_full_name)<>''
          AND newer.id IS NULL
        """,
        tuple(vids),
    )
    for raw in cur.fetchall():
        row = dict(raw)
        target = product.get(int(row["variant_id"]))
        if target is not None:
            target.update(row)

    cur.execute(
        """
        SELECT ranking_generation_sha256
        FROM market_canonical_metric_acceptance
        GROUP BY ranking_generation_sha256
        ORDER BY MAX(accepted_at) DESC
        LIMIT 1
        """
    )
    generation_row = cur.fetchone() or {}
    ranking_generation = str(generation_row.get("ranking_generation_sha256") or "")
    if not _is_sha256(ranking_generation):
        raise RuntimeError("3308 has no current canonical ranking generation")
    cur.execute(
        f"""
        SELECT
          metric.variant_id,
          metric.id AS canonical_metric_acceptance_id,
          metric.price_history_acceptance_id,
          metric.population_history_acceptance_id,
          metric.market_cap_usd,
          metric.canonical_market_rank,
          metric.ranking_generation_sha256,
          metric.metric_lineage_sha256,
          metric.evidence_sha256 AS metric_evidence_sha256,
          price.price_usd AS psa10_price_usd,
          price.observed_date AS psa10_price_observed_date,
          price.effective_at AS psa10_price_effective_at,
          source_observation.observed_at AS psa10_price_source_observed_at,
          CASE WHEN price.source_code IN ('snk','snk_psa10') THEN 'snkrdunk'
               ELSE price.source_code END AS psa10_price_source_code,
          price.source_external_entity_id AS psa10_price_external_entity_id,
          population.top_grade_population AS psa10_population,
          population.effective_at AS population_effective_at,
          population.source_code AS population_source_code,
          population.external_entity_id AS population_external_entity_id
        FROM market_canonical_metric_acceptance metric
        INNER JOIN market_metric_history_acceptance price_history
          ON price_history.id=metric.price_history_acceptance_id
         AND price_history.variant_id=metric.variant_id
         AND price_history.metric_kind='psa10_price'
        INNER JOIN market_price_observation price
          ON price_history.source_record_type='market_price_observation'
         AND price_history.source_record_id=price.id
         AND price_history.variant_id=price.variant_id
         AND price_history.observed_date=price.observed_date
         AND price_history.source_effective_at=price.effective_at
         AND price_history.external_entity_id=price.source_external_entity_id
         AND price_history.source_payload_sha256=price.payload_sha256
        INNER JOIN market_source_observation source_observation
          ON source_observation.id=price.source_observation_id
         AND source_observation.source_code=price.source_code
         AND source_observation.external_entity_id=price.source_external_entity_id
         AND source_observation.payload_sha256=price.payload_sha256
         AND source_observation.observed_date=price.observed_date
        INNER JOIN market_metric_history_acceptance population_history
          ON population_history.id=metric.population_history_acceptance_id
         AND population_history.variant_id=metric.variant_id
         AND population_history.metric_kind='psa10_population'
        INNER JOIN market_grader_population_observation population
          ON population_history.source_record_type='market_grader_population_observation'
         AND population_history.source_record_id=population.id
         AND population_history.variant_id=population.variant_id
         AND population_history.observed_date=population.observed_date
         AND population_history.source_effective_at=population.effective_at
         AND population_history.source_code=population.source_code
         AND population_history.external_entity_id=population.external_entity_id
         AND population_history.source_payload_sha256=population.payload_sha256
        WHERE metric.variant_id IN ({ph})
          AND metric.ranking_generation_sha256=%s
          AND metric.market_cap_usd=price.price_usd*population.top_grade_population
        """,
        (*vids, ranking_generation),
    )
    metric_rows = {}
    for raw in cur.fetchall():
        row = dict(raw)
        variant_id = int(row["variant_id"])
        source_code = str(row.get("psa10_price_source_code") or "")
        language = str((product.get(variant_id) or {}).get("card_language") or "")
        eligible_pc = int(language == "en" and source_code == "pricecharting")
        row["eligible_pricecharting_exists"] = eligible_pc
        row["selected_price_route_priority"] = (
            10 if source_code == "pricecharting" or language != "en" else 20
        )
        product[variant_id]["exact_source_codes"] = ",".join(
            sorted({"gemrate", source_code})
        )
        metric_rows[variant_id] = row
    ranked_metric_ids = sorted(
        metric_rows,
        key=lambda variant_id: (
            -Decimal(str(metric_rows[variant_id]["market_cap_usd"])),
            variant_id,
        ),
    )
    valid_metrics: dict[int, dict[str, Any]] = {}
    for expected_rank, variant_id in enumerate(ranked_metric_ids, 1):
        row = metric_rows[variant_id]
        if (
            int(row.get("canonical_market_rank") or 0) == expected_rank
            and _is_sha256(row.get("ranking_generation_sha256"))
            and _is_sha256(row.get("metric_lineage_sha256"))
            and _is_sha256(row.get("metric_evidence_sha256"))
        ):
            valid_metrics[variant_id] = row
            product[variant_id].update(row)

    cur.execute(
        f"""
        SELECT
          ca.variant_id,ca.id AS canonical_image_acceptance_id,
          ca.lineage_sha256 AS canonical_image_lineage_sha256,
          a.content_sha256 AS canonical_image_content_sha256,
          a.width_px AS canonical_image_width,a.height_px AS canonical_image_height,
          q.checked_at AS canonical_image_qc_at,
          CASE WHEN l.id IS NULL THEN ca.fallback_source_path
               ELSE CONCAT('snkrdunk-en:',l.exact_item_id,':',l.default_image_url) END
            AS canonical_image_source_path,
          COALESCE(page.product_page_observed_at,ca.fallback_source_observed_at)
            AS canonical_image_source_observed_at
        FROM market_canonical_image_acceptance ca
        LEFT JOIN market_snk_en_storefront_lineage l
          ON l.id=ca.storefront_lineage_id
         AND l.variant_id=ca.variant_id
         AND l.lineage_sha256=ca.lineage_sha256
        LEFT JOIN market_snk_en_product_page_authority page
          ON page.storefront_lineage_id=l.id
         AND page.variant_id=l.variant_id
         AND page.exact_item_id=l.exact_item_id
         AND page.product_url=l.product_url
        INNER JOIN market_image_asset a
          ON a.id=ca.image_asset_id AND a.variant_id=ca.variant_id
         AND a.image_kind='raw_front'
        INNER JOIN market_image_qc q
          ON q.image_asset_id=a.id
         AND q.id=(SELECT q2.id FROM market_image_qc q2
                   WHERE q2.image_asset_id=a.id
                   ORDER BY q2.checked_at DESC,q2.id DESC LIMIT 1)
         AND q.public_allowed=1 AND q.raw_front_confirmed=1
         AND q.card_number_match=1 AND q.language_match=1 AND q.tcg_match=1
         AND q.semantic_match_status IN ('human_or_vision_confirmed','accepted_freeze')
        INNER JOIN operator_binding_freeze freeze
          ON freeze.variant_id=ca.variant_id AND freeze.freeze_kind='image'
         AND freeze.acceptance_status='accepted'
         AND freeze.canonical_image_acceptance_id=ca.id
         AND freeze.accepted_lineage_sha256=ca.lineage_sha256
         AND freeze.content_sha256=a.content_sha256
        LEFT JOIN market_canonical_image_acceptance newer
          ON newer.supersedes_acceptance_id=ca.id
        WHERE ca.variant_id IN ({ph}) AND newer.id IS NULL
        """,
        tuple(vids),
    )
    for raw in cur.fetchall():
        row = dict(raw)
        target = product.get(int(row["variant_id"]))
        if target is None:
            continue
        target.update(row)
        target.update(
            {
                "image_content_sha256": row.get("canonical_image_content_sha256"),
                "image_width_px": row.get("canonical_image_width"),
                "image_height_px": row.get("canonical_image_height"),
                "image_qc_at": row.get("canonical_image_qc_at"),
                "image_source_path": row.get("canonical_image_source_path"),
                "image_source_observed_at": row.get(
                    "canonical_image_source_observed_at"
                ),
            }
        )

    cur.execute(
        f"""
        SELECT variant_id,id,price_usd,observed_at
        FROM market_ungraded_reference_price
        WHERE variant_id IN ({ph})
        ORDER BY variant_id,observed_at DESC,id DESC
        """,
        tuple(vids),
    )
    seen_raw_variants: set[int] = set()
    for raw in cur.fetchall():
        row = dict(raw)
        variant_id = int(row["variant_id"])
        if variant_id in seen_raw_variants or variant_id not in product:
            continue
        seen_raw_variants.add(variant_id)
        product[variant_id]["ungraded_reference_price_usd"] = row.get("price_usd")
        product[variant_id]["ungraded_reference_observed_at"] = row.get("observed_at")

    for variant_id, row in product.items():
        official_name = row.get("official_full_name")
        row["image_alt_json"] = {
            "en": official_name,
            "zhTW": row["localized_names_json"].get("zhTW"),
            "zhCN": row["localized_names_json"].get("zhCN"),
            "ja": row["localized_names_json"].get("ja"),
            "ko": row["localized_names_json"].get("ko"),
        }
        identity_complete = int(
            row.get("printing_variant_id") is not None
            and str(row.get("identity_status") or "") in {"confirmed", "canonical"}
            and str(row.get("tcg_code") or "") != ""
            and row.get("card_language") in {"en", "zhTW", "zhCN", "ja", "ko"}
            and str(row.get("collector_number") or "") != ""
            and str(row.get("set_code") or "") != ""
            and str(row.get("edition_code") or "") != ""
            and str(row.get("finish_code") or "") != ""
            and _is_sha256(row.get("canonical_printing_sha256"))
            and _is_sha256(row.get("printing_evidence_sha256"))
            and row.get("printing_provenance_json") is not None
            and row.get("printing_observed_at") is not None
            and row.get("identity_accepted_at") is not None
        )
        official_name_complete = int(row.get("official_name_acceptance_id") is not None)
        canonical_metric_complete = int(variant_id in valid_metrics)
        canonical_image_complete = int(row.get("canonical_image_acceptance_id") is not None)
        canonical_rank_complete = int(
            canonical_metric_complete == 1
            and int(row.get("canonical_market_rank") or 0) > 0
        )
        row.update(
            {
                "identity_complete": identity_complete,
                "official_name_complete": official_name_complete,
                "canonical_metric_complete": canonical_metric_complete,
                "canonical_image_complete": canonical_image_complete,
                "canonical_rank_complete": canonical_rank_complete,
                "product_ready": int(
                    identity_complete == 1
                    and official_name_complete == 1
                    and canonical_metric_complete == 1
                    and canonical_image_complete == 1
                ),
            }
        )

    _PRODUCT_CACHE[cache_key] = product
    if not include_daily:
        return {"product": product, "daily": {}, "fx": {}}
    daily_and_fx = _load_daily_and_fx(
        cur,
        vids,
        {
            variant_id: str(row.get("psa10_price_source_code") or "")
            for variant_id, row in product.items()
        },
    )
    return {"product": product, **daily_and_fx}


def build_history_windows(vid: int, bulk: dict):
    product = (bulk.get("product") or {}).get(vid) or {}
    rows = (bulk.get("daily") or {}).get(vid) or []
    by_day: dict[str, dict[str, Any]] = {}
    for raw in rows:
        day = iso_day(raw.get("observed_date"))
        if day:
            by_day[day] = raw
    days = sorted(by_day)
    sales_by_day: dict[str, dict[str, Any]] = {}
    history = []
    for day in days:
        row = by_day[day]
        price_ready = str(row.get("price_status") or "unavailable") == "ready"
        price = float(row["price_usd"]) if price_ready and row.get("price_usd") is not None else None
        sales_coverage = str(row.get("sales_coverage_status") or "unavailable")
        has_sales_evidence = sales_coverage != "unavailable"
        scount = int(row["sales_count"]) if has_sales_evidence and row.get("sales_count") is not None else None
        sval = float(row["sales_value_usd"]) if has_sales_evidence and row.get("sales_value_usd") is not None else None
        if has_sales_evidence:
            sales_by_day[day] = {
                "count": scount,
                "value": sval,
                "coverage": sales_coverage,
                "asOf": asof_iso(row.get("sales_evidence_at")),
                "verifiedZero": bool(row.get("sales_verified_zero")),
            }
        history.append(
            {
                "at": f"{day}T00:00:00Z",
                "priceUsd": price,
                "priceStatus": "ready" if price_ready else "unavailable",
                "trackedSalesValueUsd": sval,
                "trackedSalesCount": scount,
                "salesCoverage": sales_coverage if has_sales_evidence else "unavailable",
                "salesVerifiedZero": bool(row.get("sales_verified_zero")) if has_sales_evidence else False,
            }
        )

    if product.get("psa10_price_usd") is not None:
        cur_px = float(product["psa10_price_usd"])
        cur_asof = asof_iso(product.get("psa10_price_effective_at") or product.get("psa10_price_source_observed_at"))
        try:
            cur_day = date.fromisoformat(str(cur_asof or "")[:10])
        except Exception:
            cur_day = date.fromisoformat(days[-1]) if days else date.today()
    else:
        cur_day = date.fromisoformat(days[-1]) if days else date.today()
        cur_px = None
        cur_asof = None

    current_cap = float(product["market_cap_usd"]) if product.get("market_cap_usd") is not None else None

    windows = {}
    for code, (days_n, tol) in WINDOW_DEFS.items():
        price_series = {
            day: {
                "price_usd": float(row["price_usd"]),
                "effective_at": row.get("price_effective_at"),
            }
            for day, row in by_day.items()
            if row.get("price_usd") is not None and str(row.get("price_status")) == "ready"
        }
        old_px, old_asof, _ = nearest_day_price(price_series, cur_day - timedelta(days=days_n), tol)
        price_pct = pct_change(cur_px, old_px)
        price_status = "ready" if price_pct is not None else ("accumulating" if cur_px is not None else "unavailable")
        target_day = cur_day - timedelta(days=days_n)
        old_cap = None
        old_cap_asof = None
        best_delta = None
        for day_s, row in by_day.items():
            if row.get("market_cap_usd") is None:
                continue
            d = date.fromisoformat(day_s)
            delta = abs((d - target_day).days)
            if delta <= tol and (best_delta is None or delta < best_delta):
                best_delta = delta
                old_cap = float(row["market_cap_usd"])
                old_cap_asof = asof_iso(row.get("fact_effective_at") or d)
        cap_pct = pct_change(current_cap, old_cap)
        if cap_pct is not None:
            cap_status, cap_val = "ready", cap_pct
            cap_asof = cur_asof
        else:
            cap_status = "accumulating" if current_cap is not None else "unavailable"
            cap_val, cap_asof = None, None
        sval, scount, sasof = sales_window(sales_by_day, cur_day, days_n)
        pval, _, _ = sales_window(sales_by_day, cur_day - timedelta(days=days_n), days_n)
        sales_pct = pct_change(sval, pval)
        sales_status = "ready" if sval is not None else "unavailable"
        windows[code] = {
            "changePct": metric(price_pct, price_status, cur_asof if price_pct is not None else None, **({"anchorAt": old_asof} if old_asof else {})),
            "marketCapChangePct": metric(
                cap_val,
                cap_status,
                cap_asof if cap_val is not None else None,
                **({"anchorAt": old_cap_asof} if old_cap_asof and cap_val is not None else {}),
            ),
            "trackedSalesChangePct": metric(
                sales_pct,
                "ready" if sales_pct is not None else ("accumulating" if sval is not None else "unavailable"),
                sasof if sales_pct is not None else None,
            ),
            "trackedSales": {
                "valueUsd": metric(sval, sales_status, sasof),
                "count": metric(scount, sales_status, sasof),
                "coverage": "partial" if sval is not None else "unavailable",
                "asOf": sasof,
            },
        }
    card_id = str(product.get("opaque_id") or f"variant_{vid}")
    return _merge_025_history(card_id, history), windows


def coverage_from_cards(cards, top, watch):
    def ready_change(code):
        return sum(1 for c in cards if ((c.get("windows") or {}).get(code) or {}).get("changePct", {}).get("value") is not None)

    def ready_sales(code):
        return sum(
            1
            for c in cards
            if ((((c.get("windows") or {}).get(code) or {}).get("trackedSales") or {}).get("valueUsd") or {}).get("value") is not None
        )

    return {
        "claim": "verified-top-n",
        "requestedCount": 100,
        "verifiedCount": len(top),
        "top100Count": len(top),
        "watchlistCount": len(watch),
        "changeReady": {"1d": ready_change("1d"), "7d": ready_change("7d"), "30d": ready_change("30d")},
        "salesReady": {"1d": ready_sales("1d"), "7d": ready_sales("7d"), "30d": ready_sales("30d")},
        "completeIdentityCount": sum(1 for c in cards if c.get("identityStatus") == "confirmed"),
        "localizedStoryCount": {
            "en": sum(1 for c in cards if (c.get("stories") or {}).get("en")),
            "ja": sum(1 for c in cards if (c.get("stories") or {}).get("ja")),
            "zhCN": sum(1 for c in cards if (c.get("stories") or {}).get("zhCN")),
            "zhTW": sum(1 for c in cards if (c.get("stories") or {}).get("zhTW")),
            "ko": sum(1 for c in cards if (c.get("stories") or {}).get("ko")),
        },
    }


def currencies_block(effective_at: str, fx: dict | None = None):
    fx = fx or {}
    def rate(code):
        row = fx.get(code)
        if row and row.get("rate") is not None:
            return metric(float(row["rate"]), "ready", asof_iso(row.get("effective_at")))
        return metric(None, "unavailable", None)
    fx_times = [asof_iso(row.get("effective_at")) for row in fx.values() if row.get("effective_at")]
    return {
        "base": "USD",
        "supported": ["USD", "HKD", "CNY", "GBP", "TWD", "JPY", "KRW"],
        "rates": {
            "USD": metric(1, "ready", effective_at),
            "HKD": rate("HKD"),
            "CNY": rate("CNY"),
            "GBP": rate("GBP"),
            "TWD": rate("TWD"),
            "JPY": rate("JPY"),
            "KRW": rate("KRW"),
        },
        "asOf": max(fx_times) if fx_times else effective_at,
    }
