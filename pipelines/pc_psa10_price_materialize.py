#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reviewed materializer for ``pc_psa10_current_price_v1`` immutable plans.

No network, no RAW fields, no inferred grade.  Before the one transactional
write it replays the explicit PC artifact or the exact PC-bound eBay sales,
plus exact binding ownership, for every selected row.  A completed plan replay
is a no-op.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from c11_pc_sold_ingest import db
from pc_psa10_price_derivation import (
    MAP_DEFAULT,
    SOURCE_EBAY,
    SOURCE_PC,
    canonical_bytes,
    load_candidate_rows,
    load_pc_sales,
    select_ebay_median,
    sha256,
    validate_pc_psa10,
)
from pc_ungraded_reference_ingest import (
    _url_key,
    canonical_url_from_html,
    partition_exact_bindings,
    source_observed_at,
)
from pricecharting_page_parse import parse_product_html
from pc_page_cache import load_page as load_pc_page

CONTRACT = "pc_psa10_current_price_v1"
DEFAULT_PLAN = ROOT / "data/runtime/private-source-map/pc-psa10-current-price-plan-20260731T0630Z.json"
LOCAL_HISTORY_CONTRACT = "pc_psa10_local_history_v1"
DEFAULT_HISTORY_HTML_ROOT = ROOT / "data/private/pricecharting_session/html"
DEFAULT_HISTORY_REPORT = ROOT / "data/editorial/one-time-034-all-local-market-merge.json"


def _parse_stamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _price(value: Any) -> Decimal:
    parsed = Decimal(str(value))
    if parsed <= 0:
        raise ValueError("price must be positive")
    return parsed


def read_plan(path: Path, required_sha256: str) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("contract") != CONTRACT or doc.get("readOnly") is not True:
        raise ValueError("not a read-only pc PSA10 price plan")
    actual = sha256({key: value for key, value in doc.items() if key != "planSha256"})
    declared = str(doc.get("planSha256") or "")
    if actual != declared or actual != required_sha256:
        raise ValueError("plan SHA256 mismatch")
    if not isinstance(doc.get("rows"), list):
        raise ValueError("plan rows missing")
    return doc


def validate_plan_rows(
    doc: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        tzinfo=None
    )
    plan_as_of = _parse_stamp(doc.get("asOf"))
    if plan_as_of > current:
        raise ValueError("future-dated price plan")
    rows: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for raw in doc.get("rows", []):
        if not isinstance(raw, Mapping):
            raise ValueError("non-object plan row")
        variant_id = int(raw.get("variantId") or 0)
        source = str(raw.get("sourceCode") or "").casefold()
        payload = raw.get("payload")
        if (
            variant_id <= 0
            or source not in {SOURCE_PC, SOURCE_EBAY}
            or not isinstance(payload, Mapping)
        ):
            raise ValueError("only exact PriceCharting or PC-bound eBay PSA10 rows are materializable")
        if source == SOURCE_EBAY:
            raise ValueError(
                "PC-bound eBay medians have no exact eBay provider entity identity; "
                "refuse to materialize synthetic pc:<id> lineage"
            )
        if (variant_id, source) in seen:
            raise ValueError("ambiguous duplicate variant/source plan row")
        seen.add((variant_id, source))
        expected_priority = 95 if source == SOURCE_PC else 90
        if (
            str(raw.get("metricStatus") or "") != "ready"
            or int(raw.get("sourcePriority") or 0) != expected_priority
        ):
            raise ValueError("unapproved price metric state")
        if (
            str(payload.get("contract") or "") != CONTRACT
            or str(payload.get("source") or "").casefold() != source
        ):
            raise ValueError("payload contract/source mismatch")
        artifact = str(payload.get("artifactSha256") or "")
        external = str(payload.get("externalEntityId") or "")
        source_url = str(payload.get("sourceUrl") or "")
        url = urlsplit(source_url)
        if url.scheme != "https" or url.netloc != "www.pricecharting.com":
            raise ValueError("invalid PriceCharting source URL")
        if source == SOURCE_PC:
            if str(payload.get("method") or "") != "pricecharting_explicit_psa10_field_v1":
                raise ValueError("only explicit PriceCharting PSA10 field is accepted")
            if str(payload.get("field") or "") != "VGPC.chart_data.manualonly.last":
                raise ValueError("generic/raw PriceCharting field rejected")
            if (
                payload.get("selectedSaleFingerprints") not in ([], None)
                or payload.get("latestSoldDate") is not None
            ):
                raise ValueError("sale evidence cannot masquerade as a PriceCharting guide")
            if (
                len(artifact) != 64
                or any(ch not in "0123456789abcdef" for ch in artifact)
                or not external.isdigit()
            ):
                raise ValueError("invalid explicit PriceCharting evidence")
        else:
            fingerprints = payload.get("selectedSaleFingerprints")
            if (
                str(payload.get("method") or "")
                != "ebay_psa10_30d_median_from_exact_pricecharting_v1"
                or payload.get("field") is not None
                or payload.get("artifactSha256") is not None
                or not external.startswith("pc:")
                or not external.removeprefix("pc:").isdigit()
                or not isinstance(fingerprints, list)
                or len(fingerprints) < 3
                or len(set(map(str, fingerprints))) != len(fingerprints)
                or any(not str(value).strip() for value in fingerprints)
            ):
                raise ValueError("invalid exact eBay PSA10 median evidence")
            try:
                datetime.strptime(str(payload.get("latestSoldDate") or ""), "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError("invalid latest eBay sold date") from exc
        if sha256(payload) != str(raw.get("payloadSha256") or ""):
            raise ValueError("row payload SHA256 mismatch")
        observed = str(raw.get("observedDate") or "")
        effective = str(raw.get("effectiveAt") or "")
        effective_at = _parse_stamp(effective)
        payload_as_of = _parse_stamp(payload.get("asOf"))
        if effective_at != plan_as_of or payload_as_of != plan_as_of:
            raise ValueError("price plan timestamp mismatch")
        if not observed or _price(raw.get("priceUsd")) <= 0:
            raise ValueError("invalid planned price")
        rows.append(dict(raw))
    return rows


def pc_product_id(row: Mapping[str, Any]) -> str:
    external = str(row["payload"]["externalEntityId"])
    return external.removeprefix("pc:")


def recheck_artifacts(rows: list[dict[str, Any]], map_path: Path) -> None:
    indexed: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
    for candidate in load_candidate_rows(map_path):
        key = (int(candidate["variant_id"]), str(candidate["pc_product_id"]), str(candidate["pc_url"]))
        indexed.setdefault(key, []).append(candidate)
    for planned in rows:
        payload = planned["payload"]
        key = (
            int(planned["variantId"]),
            pc_product_id(planned),
            str(payload["sourceUrl"]),
        )
        candidates = indexed.get(key, [])
        if len(candidates) != 1:
            raise ValueError(f"artifact map ambiguity/missing for variant {key[0]}")
        if str(planned["sourceCode"]).casefold() == SOURCE_EBAY:
            continue
        replayed, reason = validate_pc_psa10(candidates[0])
        if replayed is None:
            raise ValueError(f"artifact replay rejected variant {key[0]}: {reason}")
        if (
            replayed["artifact_sha256"] != payload["artifactSha256"]
            or replayed["price_usd"] != planned["priceUsd"]
            or replayed["observed_date"].isoformat() != planned["observedDate"]
            or replayed["method"] != payload["method"]
        ):
            raise ValueError(f"artifact evidence drift for variant {key[0]}")


def assert_binding_ownership(rows: list[dict[str, Any]], identities: list[Mapping[str, Any]]) -> None:
    """Fail closed on alternate/duplicate PC owners, including aliased variants."""

    expected_by_variant = {
        int(row["variantId"]): pc_product_id(row)
        for row in rows
    }
    by_variant: dict[int, list[Mapping[str, Any]]] = {}
    by_external: dict[str, list[Mapping[str, Any]]] = {}
    for identity in identities:
        variant_id = int(identity["canonical_variant_id"])
        external_id = str(identity["external_entity_id"])
        by_variant.setdefault(variant_id, []).append(identity)
        by_external.setdefault(external_id, []).append(identity)
    for variant_id, expected_external in expected_by_variant.items():
        variants = by_variant.get(variant_id, [])
        if len(variants) != 1 or str(variants[0]["external_entity_id"]) != expected_external:
            raise ValueError(f"ambiguous exact PriceCharting binding for variant {variant_id}")
        owners = by_external.get(expected_external, [])
        if len(owners) != 1 or int(owners[0]["canonical_variant_id"]) != variant_id:
            raise ValueError(f"PriceCharting product has ambiguous owner: {expected_external}")


def recheck_exact_bindings(connection: Any, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    variants = [int(row["variantId"]) for row in rows]
    externals = [pc_product_id(row) for row in rows]
    variant_marks = ",".join(["%s"] * len(variants))
    external_marks = ",".join(["%s"] * len(externals))
    with connection.cursor() as cursor:
        cursor.execute(
            f"""SELECT COALESCE(alias.canonical_variant_id, identity.variant_id) AS canonical_variant_id,
                       identity.variant_id AS bound_variant_id, identity.external_entity_id
                FROM catalog_source_identity AS identity
                LEFT JOIN catalog_variant_alias AS alias ON alias.duplicate_variant_id=identity.variant_id
                WHERE identity.source_code=%s AND identity.match_status='exact'
                  AND (COALESCE(alias.canonical_variant_id, identity.variant_id) IN ({variant_marks})
                       OR identity.external_entity_id IN ({external_marks}))""",
            [SOURCE_PC, *variants, *externals],
        )
        identities = list(cursor.fetchall())
    assert_binding_ownership(rows, identities)


def recheck_ebay_rows(
    rows: list[dict[str, Any]],
    sales: list[Mapping[str, Any]],
    *,
    as_of: datetime,
) -> None:
    """Replay every selected sale fingerprint and median from canonical sales."""

    for planned in rows:
        if str(planned["sourceCode"]).casefold() != SOURCE_EBAY:
            continue
        payload = planned["payload"]
        replayed, reason = select_ebay_median(
            variant_id=int(planned["variantId"]),
            pc_product_id=pc_product_id(planned),
            sales=sales,
            as_of=as_of,
            source_url=str(payload["sourceUrl"]),
        )
        if replayed is None:
            raise ValueError(
                f"eBay sale replay rejected variant {planned['variantId']}: {reason}"
            )
        expected = (
            replayed["price_usd"],
            replayed["observed_date"].isoformat(),
            replayed["method"],
            replayed["sale_fingerprints"],
            replayed["latest_sold_date"],
        )
        actual = (
            str(planned["priceUsd"]),
            str(planned["observedDate"]),
            str(payload["method"]),
            list(payload["selectedSaleFingerprints"]),
            str(payload["latestSoldDate"]),
        )
        if actual != expected:
            raise ValueError(
                f"eBay sale evidence drift for variant {planned['variantId']}"
            )


def recheck_sale_evidence(
    connection: Any,
    rows: list[dict[str, Any]],
    *,
    as_of: datetime,
) -> None:
    ebay_rows = [
        row for row in rows if str(row["sourceCode"]).casefold() == SOURCE_EBAY
    ]
    if not ebay_rows:
        return
    sales = load_pc_sales(
        connection,
        [int(row["variantId"]) for row in ebay_rows],
        as_of=as_of,
    )
    recheck_ebay_rows(ebay_rows, sales, as_of=as_of)


def changed_rows(connection: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    ids = [int(row["variantId"]) for row in rows]
    sources = sorted({str(row["sourceCode"]).casefold() for row in rows})
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT price.variant_id, price.source_code, price.observed_date, "
            "price.effective_at, price.price_usd, price.source_priority, "
            "price.metric_status, price.payload_sha256, price.source_external_entity_id, "
            "price.source_observation_id, source.source_code AS observation_source_code, "
            "source.external_entity_id AS observation_external_entity_id, "
            "source.payload_sha256 AS observation_payload_sha256, "
            "source.payload_json AS observation_payload_json "
            "FROM market_price_observation AS price "
            "LEFT JOIN market_source_observation AS source "
            "ON source.id=price.source_observation_id WHERE price.source_code IN ("
            + ",".join(["%s"] * len(sources))
            + ") AND price.variant_id IN ("
            + ",".join(["%s"] * len(ids))
            + ")",
            [*sources, *ids],
        )
        existing = {
            (
                int(row["variant_id"]),
                str(row["source_code"]).casefold(),
                str(row["observed_date"]),
            ): row
            for row in cursor.fetchall()
        }
    changed: list[dict[str, Any]] = []
    window = _v2_business_window()
    for row in rows:
        source = str(row["sourceCode"]).casefold()
        current = existing.get(
            (int(row["variantId"]), source, str(row["observedDate"]))
        )
        if window is not None and current is not None and _same_day_same_evidence(
            row, current, source=source, window=window,
        ):
            continue
        expected = (
            str(row["priceUsd"]),
            _parse_stamp(row["effectiveAt"]),
            int(row["sourcePriority"]),
            "ready",
            str(row["payloadSha256"]),
            pc_product_id(row),
            True,
        )
        actual = None if current is None else (
            str(current["price_usd"]), current["effective_at"], int(current["source_priority"]),
            str(current["metric_status"]), str(current["payload_sha256"]),
            str(current.get("source_external_entity_id") or ""),
            (
                current.get("source_observation_id") is not None
                and str(current.get("observation_source_code") or "").casefold() == source
                and str(current.get("observation_external_entity_id") or "") == pc_product_id(row)
                and str(current.get("observation_payload_sha256") or "")
                == str(row["payloadSha256"])
            ),
        )
        if actual != expected:
            changed.append(row)
    return changed


def _v2_business_window() -> tuple[datetime, datetime] | None:
    """The V2 run's business window (naive UTC), or None outside the chain."""

    from daily_chain_v2_db import business_window_from_env

    return business_window_from_env()


def _evidence(payload: Any) -> dict[str, Any] | None:
    """The hashed payload without its run clock."""

    if isinstance(payload, (bytes, str)):
        try:
            payload = json.loads(payload)
        except ValueError:
            return None
    if not isinstance(payload, Mapping):
        return None
    return {key: value for key, value in payload.items() if key != "asOf"}


def _same_day_same_evidence(
    row: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    source: str,
    window: tuple[datetime, datetime],
) -> bool:
    """A05 2026-08-23: every run re-stamped ``asOf`` into the hashed payload,
    so all 1238 source observations and price rows were rewritten each run
    even when page, price, artifact and sales were identical -- and that
    rewrite re-minted 1618 bootstrap quotes plus 35,949 legacy quote rows per
    run.  The V2 contract needs one capture per business day
    (quote.checked_at inside business_window_utc), not one per run: a row
    captured earlier in this window with the same evidence stays as it is."""

    effective_at = current.get("effective_at")
    if not isinstance(effective_at, datetime):
        return False
    start, end = window
    if not (start <= effective_at.replace(tzinfo=None) < end):
        return False
    if (
        str(current.get("price_usd")) != str(row["priceUsd"])
        or int(current.get("source_priority") or 0) != int(row["sourcePriority"])
        or str(current.get("metric_status")) != "ready"
        or str(current.get("source_external_entity_id") or "") != pc_product_id(row)
        or current.get("source_observation_id") is None
        or str(current.get("observation_source_code") or "").casefold() != source
        or str(current.get("observation_external_entity_id") or "") != pc_product_id(row)
        or str(current.get("observation_payload_sha256") or "") != str(current.get("payload_sha256") or "")
    ):
        return False
    planned_evidence = _evidence(row.get("payload"))
    current_evidence = _evidence(current.get("observation_payload_json"))
    return planned_evidence is not None and planned_evidence == current_evidence


def run_key_for_plan(plan_sha256: str) -> str:
    return hashlib.sha256(
        f"pc_psa10_price|{plan_sha256}".encode("utf-8")
    ).hexdigest()


def materialize(connection: Any, rows: list[dict[str, Any]], *, plan_sha256: str) -> int:
    change = changed_rows(connection, rows)
    # market_ingest_run.run_key is CHAR(64). Keep the plan lineage while
    # satisfying the schema instead of prefixing the already-64-byte digest.
    run_key = run_key_for_plan(plan_sha256)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connection.cursor() as cursor:
        run_id: int | None = None
        if change:
            cursor.execute("SELECT status FROM market_ingest_run WHERE run_key=%s", (run_key,))
            prior = cursor.fetchone()
            if prior is not None:
                raise ValueError("plan run already exists but materialized rows differ")
            cursor.execute(
                """INSERT INTO market_ingest_run (run_key, source_code, ingest_mode, effective_at, payload_sha256, manifest_sha256, status, observed_count, started_at)
                   VALUES (%s,%s,'backfill',%s,%s,%s,'running',%s,%s)""",
                (run_key, SOURCE_PC, now, plan_sha256, plan_sha256, len(change), now),
            )
            run_id = int(cursor.lastrowid)
            price_values: list[tuple[Any, ...]] = []
            for row in change:
                variant_id = int(row["variantId"])
                source_code = str(row["sourceCode"]).casefold()
                external_entity_id = pc_product_id(row)
                cursor.execute(
                    """
                    SELECT COUNT(*) AS n
                    FROM catalog_source_identity AS identity
                    WHERE identity.variant_id=%s AND identity.source_code=%s
                      AND identity.external_entity_id=%s AND identity.match_status='exact'
                    """,
                    (variant_id, source_code, external_entity_id),
                )
                if int(cursor.fetchone()["n"]) != 1:
                    raise ValueError(
                        "PriceCharting exact identity is missing or ambiguous: "
                        f"variant={variant_id} external={external_entity_id}"
                    )
                effective_at = _parse_stamp(row["effectiveAt"])
                payload_json = canonical_bytes(row["payload"]).decode("utf-8")
                cursor.execute(
                    """
                    INSERT INTO market_source_observation
                        (run_id, source_code, external_entity_id, observation_kind, effective_at,
                         observed_date, payload_sha256, payload_json, observed_at)
                    VALUES (%s, %s, %s, 'psa10_price_guide', %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        id=LAST_INSERT_ID(id), run_id=VALUES(run_id),
                        effective_at=VALUES(effective_at), payload_json=VALUES(payload_json),
                        observed_at=VALUES(observed_at)
                    """,
                    (
                        run_id, source_code, external_entity_id, effective_at,
                        row["observedDate"], row["payloadSha256"], payload_json, effective_at,
                    ),
                )
                source_observation_id = int(cursor.lastrowid)
                if source_observation_id <= 0:
                    raise RuntimeError("PriceCharting source observation upsert returned no id")
                price_values.append(
                    (
                        run_id, variant_id, source_code, external_entity_id,
                        source_observation_id, row["observedDate"], effective_at,
                        row["priceUsd"], int(row["sourcePriority"]), "ready",
                        row["payloadSha256"],
                    )
                )
            cursor.executemany(
                """INSERT INTO market_price_observation
                     (run_id,variant_id,source_code,source_external_entity_id,
                      source_observation_id,observed_date,effective_at,price_usd,
                      native_price,native_currency,source_priority,metric_status,payload_sha256)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NULL,NULL,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE
                     last_run_id=VALUES(run_id),
                     restamp_count=restamp_count+1,
                     source_external_entity_id=VALUES(source_external_entity_id),
                     source_observation_id=VALUES(source_observation_id),
                     effective_at=VALUES(effective_at),price_usd=VALUES(price_usd),
                     source_priority=VALUES(source_priority),
                     metric_status=CASE WHEN market_price_observation.metric_status='quarantined'
                                        THEN 'quarantined' ELSE VALUES(metric_status) END,
                     payload_sha256=VALUES(payload_sha256)""",
                price_values,
            )
            cursor.execute(
                "UPDATE market_ingest_run SET status='completed',accepted_count=%s,completed_at=%s WHERE id=%s",
                (len(change), now, run_id),
            )

        # No quote is minted here any more (owner 2026-08-23).
        # `manualonly.last` is the head of PriceCharting's monthly chart, not a
        # sale: it was a month-old chart level for 1,148 of 1,184 EN cards and
        # was nevertheless the published price of every one of them.  The chart
        # rows above still land in market_price_observation because the charts
        # need the series; the PRICE now comes from
        # pipelines/psa10_latest_sale_quote.py, which reads the actual PSA 10
        # sales.  insert_quote_revision would refuse `pricecharting` under
        # purpose='live' anyway -- this deletion and that gate are one change.
    connection.commit()
    return len(change)


def _active_exact_pc_rows(connection: Any, map_path: Path) -> tuple[list[dict[str, Any]], int]:
    """Return only current-universe, exact, one-owner PriceCharting bindings."""

    candidates = load_candidate_rows(map_path)
    with connection.cursor() as cursor:
        exact, _rejected = partition_exact_bindings(cursor, candidates)
        cursor.execute(
            """
            SELECT member.variant_id
            FROM market_universe_member AS member
            INNER JOIN market_universe_lock AS lock_row
              ON lock_row.id=member.universe_lock_id AND lock_row.is_current=1
            """
        )
        active_ids = {int(row["variant_id"]) for row in cursor.fetchall()}
    active = [row for row in exact if int(row["variant_id"]) in active_ids]
    ownership_rows = [
        {
            "variantId": int(row["variant_id"]),
            "payload": {"externalEntityId": str(row["pc_product_id"])},
        }
        for row in active
    ]
    recheck_exact_bindings(connection, ownership_rows)
    return active, len(candidates)


def _history_point(
    *,
    row: Mapping[str, Any],
    point: Any,
    artifact_sha256: str,
    artifact_path: Path,
) -> dict[str, Any] | None:
    observed_at = source_observed_at(point)
    if observed_at is None:
        return None
    try:
        cents = Decimal(str(point[1]))
        price = cents / Decimal(100)
    except Exception:
        return None
    if price <= 0:
        return None
    source_url = str(row["pc_url"])
    payload = {
        "contract": LOCAL_HISTORY_CONTRACT,
        "source": SOURCE_PC,
        "variantId": int(row["variant_id"]),
        "externalEntityId": str(row["pc_product_id"]),
        "method": "pricecharting_explicit_psa10_history_v1",
        "field": "VGPC.chart_data.manualonly.series",
        "sourceUrl": source_url,
        "artifactSha256": artifact_sha256,
        "artifactPath": str(artifact_path),
        "chartPoint": [int(point[0]), str(point[1])],
    }
    return {
        "variantId": int(row["variant_id"]),
        "externalEntityId": str(row["pc_product_id"]),
        "observedDate": observed_at.date().isoformat(),
        "effectiveAt": observed_at,
        "priceUsd": str(price.quantize(Decimal("0.000001"))),
        "payloadSha256": sha256(payload),
        "payload": payload,
    }


def collect_local_history(
    connection: Any,
    *,
    map_path: Path,
    html_roots: list[Path],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Merge every local PC page that proves the active exact provider identity.

    The old repository is deliberately just another evidence root.  A page is
    accepted only when both its canonical URL and embedded PC product id agree
    with the live exact binding; path names never decide identity.
    """

    active_rows, map_rows = _active_exact_pc_rows(connection, map_path)
    expected = {
        (str(row["pc_product_id"]), _url_key(str(row["pc_url"]))): row
        for row in active_rows
    }
    selected: dict[tuple[int, str], tuple[tuple[int, int, str], dict[str, Any]]] = {}
    root_reports: list[dict[str, Any]] = []
    for root_rank, root in enumerate(html_roots):
        report = {
            "root": str(root),
            "exists": root.is_dir(),
            "htmlFiles": 0,
            "matchedExactArtifacts": 0,
            "acceptedPricePoints": 0,
        }
        if not root.is_dir():
            root_reports.append(report)
            continue
        for artifact_path in sorted(root.rglob("*.html")):
            report["htmlFiles"] += 1
            try:
                # A05 2026-08-23: 3638 pages under the history root were read
                # and parsed on every run (24.7 s); pc_page_cache serves the
                # parse and the sha256 of exactly those bytes after one stat.
                page = load_pc_page(artifact_path)
                if page is None or page.canonical_url is None:
                    continue
                canonical_url = page.canonical_url
                parsed = page.parsed
                product = parsed.get("product") if isinstance(parsed.get("product"), Mapping) else {}
                product_id = str(product.get("id") or "")
                row = expected.get((product_id, _url_key(canonical_url)))
                if row is None or not parsed.get("ok"):
                    continue
                history = (
                    parsed.get("psa10", {}).get("history")
                    if isinstance(parsed.get("psa10"), Mapping)
                    else None
                )
                if not isinstance(history, Mapping) or history.get("label") != "PSA 10":
                    continue
                series = history.get("series")
                if not isinstance(series, list):
                    continue
            except (OSError, ValueError, TypeError):
                continue
            report["matchedExactArtifacts"] += 1
            artifact_sha256 = page.sha256
            precedence = (root_rank, page.mtime_ns, str(artifact_path))
            for point in series:
                item = _history_point(
                    row=row,
                    point=point,
                    artifact_sha256=artifact_sha256,
                    artifact_path=artifact_path,
                )
                if item is None:
                    continue
                report["acceptedPricePoints"] += 1
                key = (int(item["variantId"]), str(item["observedDate"]))
                prior = selected.get(key)
                if prior is None or precedence > prior[0]:
                    selected[key] = (precedence, item)
        root_reports.append(report)
    rows = [item for _order, item in selected.values()]
    rows.sort(key=lambda item: (int(item["variantId"]), str(item["observedDate"])))
    report = {
        "contract": LOCAL_HISTORY_CONTRACT,
        "map": str(map_path),
        "mapRows": map_rows,
        "activeExactPriceChartingRows": len(active_rows),
        "roots": root_reports,
        "mergedHistoricalPriceRows": len(rows),
        "variantsWithHistoricalPrice": len({int(row["variantId"]) for row in rows}),
        "rejectedReason": "non-exact provider identity, no explicit PSA 10 series, malformed local artifact, or empty price point",
    }
    return rows, report


def materialize_local_history(connection: Any, rows: list[dict[str, Any]]) -> int:
    """Fast one-transaction overlay of local exact PriceCharting daily history."""

    if not rows:
        return 0
    manifest_sha256 = hashlib.sha256(
        "\n".join(str(row["payloadSha256"]) for row in rows).encode("utf-8")
    ).hexdigest()
    run_key = hashlib.sha256(f"pc_local_history|{manifest_sha256}".encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO market_ingest_run
              (run_key,source_code,ingest_mode,effective_at,payload_sha256,manifest_sha256,status,observed_count,started_at)
            VALUES (%s,%s,'backfill',%s,%s,%s,'running',%s,%s)
            ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id),status='running',observed_count=VALUES(observed_count),started_at=VALUES(started_at)
            """,
            (run_key, SOURCE_PC, now, manifest_sha256, manifest_sha256, len(rows), now),
        )
        run_id = int(cursor.lastrowid)
        cursor.execute("DROP TEMPORARY TABLE IF EXISTS pc_local_history_stage")
        cursor.execute(
            """
            CREATE TEMPORARY TABLE pc_local_history_stage (
              variant_id BIGINT UNSIGNED NOT NULL,
              external_entity_id VARCHAR(191) NOT NULL,
              observed_date DATE NOT NULL,
              effective_at DATETIME(6) NOT NULL,
              price_usd DECIMAL(18,6) NOT NULL,
              payload_sha256 CHAR(64) NOT NULL,
              payload_json JSON NOT NULL,
              PRIMARY KEY (variant_id, observed_date)
            ) ENGINE=InnoDB
            """
        )
        values = [
            (
                int(row["variantId"]),
                str(row["externalEntityId"]),
                str(row["observedDate"]),
                row["effectiveAt"],
                str(row["priceUsd"]),
                str(row["payloadSha256"]),
                canonical_bytes(row["payload"]).decode("utf-8"),
            )
            for row in rows
        ]
        cursor.executemany(
            """
            INSERT INTO pc_local_history_stage
              (variant_id,external_entity_id,observed_date,effective_at,price_usd,payload_sha256,payload_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            values,
        )
        cursor.execute(
            """
            INSERT INTO market_source_observation
              (run_id,source_code,external_entity_id,observation_kind,effective_at,observed_date,payload_sha256,payload_json,observed_at)
            SELECT %s,%s,stage.external_entity_id,'psa10_price_guide',stage.effective_at,
                   stage.observed_date,stage.payload_sha256,stage.payload_json,stage.effective_at
            FROM pc_local_history_stage AS stage
            ON DUPLICATE KEY UPDATE run_id=VALUES(run_id),effective_at=VALUES(effective_at),payload_json=VALUES(payload_json),observed_at=VALUES(observed_at)
            """,
            (run_id, SOURCE_PC),
        )
        cursor.execute(
            """
            INSERT INTO market_price_observation
              (run_id,variant_id,source_code,source_external_entity_id,source_observation_id,observed_date,effective_at,price_usd,native_price,native_currency,source_priority,metric_status,payload_sha256)
            SELECT %s,stage.variant_id,%s,stage.external_entity_id,source.id,stage.observed_date,
                   stage.effective_at,stage.price_usd,NULL,NULL,95,'ready',stage.payload_sha256
            FROM pc_local_history_stage AS stage
            INNER JOIN market_source_observation AS source
              ON source.source_code=%s AND source.external_entity_id=stage.external_entity_id
             AND source.observation_kind='psa10_price_guide'
             AND source.observed_date=stage.observed_date
             AND source.payload_sha256=stage.payload_sha256
            ON DUPLICATE KEY UPDATE last_run_id=VALUES(run_id),restamp_count=restamp_count+1,
              source_external_entity_id=VALUES(source_external_entity_id),
              source_observation_id=VALUES(source_observation_id),effective_at=VALUES(effective_at),
              price_usd=VALUES(price_usd),source_priority=VALUES(source_priority),
              metric_status=CASE WHEN market_price_observation.metric_status='quarantined'
                                 THEN 'quarantined' ELSE VALUES(metric_status) END,
              payload_sha256=VALUES(payload_sha256)
            """,
            (run_id, SOURCE_PC, SOURCE_PC),
        )
        cursor.execute(
            "UPDATE market_ingest_run SET status='completed',accepted_count=%s,completed_at=%s WHERE id=%s",
            (len(rows), now, run_id),
        )
        # Local history is a backfill of chart points, and a chart point may no
        # longer become a price (owner 2026-08-23).  It used to mint the head
        # bar here so leftover-5 would stop aborting S12; that hole is filled by
        # a real sale now.  The history rows above still land in
        # market_price_observation, which is all the charts ever needed.
    connection.commit()
    return len(rows)


def run_local_history(
    *,
    map_path: Path,
    html_roots: list[Path],
    report_path: Path,
    write: bool,
) -> int:
    connection = db()
    try:
        rows, report = collect_local_history(connection, map_path=map_path, html_roots=html_roots)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        written = materialize_local_history(connection, rows) if write else 0
        if not write:
            connection.rollback()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    print(json.dumps({"contract": LOCAL_HISTORY_CONTRACT, "write": write, "report": str(report_path), "mergedHistoricalPriceRows": len(rows), "changed": written, "roots": report["roots"]}, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize reviewed explicit PriceCharting PSA10 prices")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--map", type=Path, default=MAP_DEFAULT)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--local-history", action="store_true", help="merge every local exact PriceCharting PSA10 chart point")
    parser.add_argument("--html-root", type=Path, action="append", default=[], help="additional local PriceCharting HTML root; later roots overlay earlier evidence")
    parser.add_argument("--history-report", type=Path, default=DEFAULT_HISTORY_REPORT)
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test_checked_at_lineage(), sort_keys=True))
        return 0
    if args.local_history:
        roots = [*args.html_root, DEFAULT_HISTORY_HTML_ROOT]
        unique_roots: list[Path] = []
        seen_roots: set[Path] = set()
        for root in roots:
            resolved = root.resolve()
            if resolved not in seen_roots:
                seen_roots.add(resolved)
                unique_roots.append(resolved)
        return run_local_history(
            map_path=args.map.resolve(),
            html_roots=unique_roots,
            report_path=args.history_report.resolve(),
            write=bool(args.write),
        )
    if not args.plan_sha256:
        parser.error("--plan-sha256 is required unless --local-history is used")
    doc = read_plan(args.plan, args.plan_sha256)
    rows = validate_plan_rows(doc)
    recheck_artifacts(rows, args.map)
    plan_as_of = _parse_stamp(doc["asOf"]).replace(tzinfo=timezone.utc)
    connection = db()
    try:
        recheck_exact_bindings(connection, rows)
        recheck_sale_evidence(connection, rows, as_of=plan_as_of)
        changed = changed_rows(connection, rows)
        written = materialize(connection, rows, plan_sha256=args.plan_sha256) if args.write else 0
        if not args.write:
            connection.rollback()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    print(json.dumps({"contract": CONTRACT, "write": bool(args.write), "planSha256": args.plan_sha256, "planned": len(rows), "wouldChange": len(changed), "changed": written}, sort_keys=True))
    return 0



def self_test_checked_at_lineage() -> dict[str, Any]:
    """checked_at must follow capture effectiveAt, never materialize wall-clock."""
    from current_quote_revision import quote_lineage_sha256

    capture_at = "2026-08-12T06:15:00+00:00"
    period = "2026-08-01"
    payload = "c" * 64
    lineage_a = quote_lineage_sha256(
        variant_id=42,
        source_code=SOURCE_PC,
        source_external_entity_id="999",
        price_usd="2925.000000",
        source_period_at=period,
        checked_at=_parse_stamp(capture_at),
        payload_sha256=payload,
    )
    lineage_b = quote_lineage_sha256(
        variant_id=42,
        source_code=SOURCE_PC,
        source_external_entity_id="999",
        price_usd="2925.000000",
        source_period_at=period,
        checked_at=_parse_stamp(capture_at),
        payload_sha256=payload,
    )
    wall = quote_lineage_sha256(
        variant_id=42,
        source_code=SOURCE_PC,
        source_external_entity_id="999",
        price_usd="2925.000000",
        source_period_at=period,
        checked_at=datetime.now(timezone.utc).replace(tzinfo=None),
        payload_sha256=payload,
    )
    if lineage_a != lineage_b:
        raise AssertionError("identical capture must produce identical quote lineage")
    if lineage_a == wall:
        raise AssertionError("wall-clock checked_at must not collide with capture lineage")
    return {"ok": True, "idempotentCaptureLineage": True}



if __name__ == "__main__":
    raise SystemExit(main())
