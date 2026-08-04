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

CONTRACT = "pc_psa10_current_price_v1"
DEFAULT_PLAN = ROOT / "data/runtime/private-source-map/pc-psa10-current-price-plan-20260731T0630Z.json"


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
            "SELECT variant_id, source_code, observed_date, effective_at, price_usd, "
            "source_priority, metric_status, payload_sha256 "
            "FROM market_price_observation WHERE source_code IN ("
            + ",".join(["%s"] * len(sources))
            + ") AND variant_id IN ("
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
    for row in rows:
        source = str(row["sourceCode"]).casefold()
        current = existing.get(
            (int(row["variantId"]), source, str(row["observedDate"]))
        )
        expected = (
            str(row["priceUsd"]),
            _parse_stamp(row["effectiveAt"]),
            int(row["sourcePriority"]),
            "ready",
            str(row["payloadSha256"]),
        )
        actual = None if current is None else (
            str(current["price_usd"]), current["effective_at"], int(current["source_priority"]), str(current["metric_status"]), str(current["payload_sha256"]),
        )
        if actual != expected:
            changed.append(row)
    return changed


def run_key_for_plan(plan_sha256: str) -> str:
    return hashlib.sha256(
        f"pc_psa10_price|{plan_sha256}".encode("utf-8")
    ).hexdigest()


def materialize(connection: Any, rows: list[dict[str, Any]], *, plan_sha256: str) -> int:
    change = changed_rows(connection, rows)
    if not change:
        return 0
    # market_ingest_run.run_key is CHAR(64). Keep the plan lineage while
    # satisfying the schema instead of prefixing the already-64-byte digest.
    run_key = run_key_for_plan(plan_sha256)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connection.cursor() as cursor:
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
        cursor.executemany(
            """INSERT INTO market_price_observation (run_id,variant_id,source_code,observed_date,effective_at,price_usd,native_price,native_currency,source_priority,metric_status,payload_sha256)
               VALUES (%s,%s,%s,%s,%s,%s,NULL,NULL,%s,%s,%s)
               ON DUPLICATE KEY UPDATE run_id=VALUES(run_id),effective_at=VALUES(effective_at),price_usd=VALUES(price_usd),source_priority=VALUES(source_priority),metric_status=VALUES(metric_status),payload_sha256=VALUES(payload_sha256)""",
            [
                (
                    run_id,
                    int(row["variantId"]),
                    str(row["sourceCode"]).casefold(),
                    row["observedDate"],
                    _parse_stamp(row["effectiveAt"]),
                    row["priceUsd"],
                    int(row["sourcePriority"]),
                    "ready",
                    row["payloadSha256"],
                )
                for row in change
            ],
        )
        cursor.execute("UPDATE market_ingest_run SET status='completed',accepted_count=%s,completed_at=%s WHERE id=%s", (len(change), now, run_id))
    connection.commit()
    return len(change)


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize reviewed explicit PriceCharting PSA10 prices")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--map", type=Path, default=MAP_DEFAULT)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
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


if __name__ == "__main__":
    raise SystemExit(main())
