#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Derive exact PSA 10 current prices from saved PriceCharting evidence.

This is deliberately a local, replayable derivation: it never fetches.  A
PriceCharting guide point is usable only when the exact PC binding, saved HTML
canonical URL, embedded product id, and explicit ``PSA 10`` chart field all
agree.  Independently, an eBay current-price family can be derived from at
least three already-verified PC/eBay PSA 10 sales in the last 30 days.  When
both exist the plan keeps both; CARDZ cross-source QC decides whether they
agree instead of silently treating one as a fallback.

Default mode writes an immutable plan only.  ``--write`` is intentionally not
provided here: canonical materialisation remains a separately reviewed DB
writer step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from c11_pc_sold_ingest import db
from pc_page_cache import load_page as load_pc_page
from collection_contract import LIVE_EBAY_SOLD_SOURCE_CODES
from pc_ungraded_reference_ingest import (
    MAP_DEFAULT,
    _url_key,
    canonical_url_from_html,
    load_candidate_rows,
    partition_exact_bindings,
    resolve_html_path,
    source_observed_at,
)
from pricecharting_page_parse import parse_product_html

SOURCE_PC = "pricecharting"
SOURCE_EBAY = "ebay"
WINDOW = timedelta(days=30)
MIN_EBAY_SALES = 3
CENT = Decimal("0.000001")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def money(value: Decimal) -> str:
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def _decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except Exception:
        return None
    return parsed if parsed > 0 else None


def validate_pc_psa10(row: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """Return one explicit PSA10 guide price, or a fail-closed reason."""

    html_path = resolve_html_path(dict(row))
    if html_path is None:
        return None, "no_html"
    # A05 2026-08-23: one read + one parse per page, shared with the ingest
    # child and the materializer through pc_page_cache (size/mtime/parser
    # keyed). The lane used to read and parse every page four times.
    page = load_pc_page(html_path, source_url=str(row["pc_url"]))
    if page is None:
        return None, "no_html"
    canonical_url = page.canonical_url
    if canonical_url is None or _url_key(canonical_url) != _url_key(str(row["pc_url"])):
        return None, "canonical_url_mismatch"
    parsed = page.parsed
    if not parsed.get("ok"):
        return None, "parse_fail"
    product = parsed.get("product") if isinstance(parsed.get("product"), Mapping) else {}
    if str(product.get("id") or "") != str(row["pc_product_id"]):
        return None, "product_id_mismatch"
    psa10 = parsed.get("psa10") if isinstance(parsed.get("psa10"), Mapping) else {}
    history = psa10.get("history") if isinstance(psa10, Mapping) else None
    if not isinstance(history, Mapping) or history.get("label") != "PSA 10":
        return None, "no_explicit_psa10_field"
    # ``last_usd`` is a last-nonzero convenience value.  Never use it when the
    # actual latest explicit field is zero/absent: that would silently revive a
    # stale guide quote.
    latest = history.get("last")
    observed_at = source_observed_at(latest)
    cents = latest[1] if isinstance(latest, list) and len(latest) >= 2 else None
    cents_value = _decimal(cents)
    price = _decimal(cents_value / Decimal(100)) if cents_value is not None else None
    if observed_at is None or price is None:
        return None, "no_positive_psa10_field"
    return {
        "variant_id": int(row["variant_id"]),
        "source_code": SOURCE_PC,
        "external_entity_id": str(row["pc_product_id"]),
        "source_url": str(row["pc_url"]),
        "observed_date": observed_at.date(),
        "price_usd": money(price),
        "method": "pricecharting_explicit_psa10_field_v1",
        "artifact_sha256": page.sha256,
        "field": "VGPC.chart_data.manualonly.last",
        "latest_sold_date": None,
        "sale_fingerprints": [],
    }, "accepted"


def select_ebay_median(
    *,
    variant_id: int,
    pc_product_id: str,
    sales: Iterable[Mapping[str, Any]],
    as_of: datetime,
    source_url: str | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Strictly choose PC-backed eBay PSA10 sales and calculate their median."""

    cutoff = (as_of - WINDOW).replace(tzinfo=None)
    selected: list[Mapping[str, Any]] = []
    seen_fingerprints: set[str] = set()
    for sale in sales:
        if int(sale.get("variant_id") or 0) != variant_id:
            continue
        sale_source = str(sale.get("source_code") or "").casefold()
        live_sold = {code.casefold() for code in LIVE_EBAY_SOLD_SOURCE_CODES}
        if sale_source not in live_sold:
            continue
        ext = str(sale.get("external_entity_id") or "").casefold()
        want = {str(pc_product_id).casefold(), f"pc:{pc_product_id}".casefold()}
        if ext not in want:
            continue
        if str(sale.get("grader_code") or "").casefold() != "psa":
            continue
        if str(sale.get("grade_label") or "").upper().replace(" ", "") not in {
            "10",
            "PSA10",
        }:
            continue
        if str(sale.get("coverage_status") or "").casefold() not in {"partial", "complete", "certified"}:
            continue
        if str(sale.get("timestamp_quality") or "").casefold() not in {"exact", "date", "timestamp", "exact_date", "relative_resolved", "relative_subday"}:
            continue
        sold_at = sale.get("sold_at")
        if not isinstance(sold_at, datetime) or sold_at < cutoff:
            continue
        price = _decimal(sale.get("unit_price_usd"))
        fingerprint = str(sale.get("transaction_fingerprint") or "")
        if price is None or not fingerprint or fingerprint in seen_fingerprints:
            continue
        seen_fingerprints.add(fingerprint)
        selected.append(sale)
    if len(selected) < MIN_EBAY_SALES:
        return None, "insufficient_exact_ebay_psa10_sales"
    values = sorted(_decimal(item["unit_price_usd"]) for item in selected)
    assert all(value is not None for value in values)
    latest = max(item["sold_at"] for item in selected)
    fingerprints = sorted(str(item["transaction_fingerprint"]) for item in selected)
    return {
        "variant_id": variant_id,
        "source_code": SOURCE_EBAY,
        "external_entity_id": f"pc:{pc_product_id}",
        # This is a current-price derivation observed at ``as_of``.  The latest
        # contributing sale date remains separately bound below.
        "observed_date": as_of.date(),
        "price_usd": money(Decimal(str(median(values)))),
        "method": "ebay_psa10_30d_median_from_exact_pricecharting_v1",
        "artifact_sha256": None,
        "field": None,
        "source_url": source_url,
        "latest_sold_date": latest.date().isoformat(),
        "sale_fingerprints": fingerprints,
    }, "accepted"


def derive(rows: Iterable[Mapping[str, Any]], sales: Iterable[Mapping[str, Any]], *, as_of: datetime) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Derive independent PC-guide and exact-eBay-sale price families."""

    result: list[dict[str, Any]] = []
    outcomes: Counter[str] = Counter()
    sales_list = list(sales)
    for row in rows:
        direct, reason = validate_pc_psa10(row)
        if direct is not None:
            result.append(direct)
            outcomes["pricecharting_explicit_psa10"] += 1
        else:
            outcomes[reason] += 1
        ebay, ebay_reason = select_ebay_median(
            variant_id=int(row["variant_id"]),
            pc_product_id=str(row["pc_product_id"]),
            sales=sales_list,
            as_of=as_of,
            source_url=str(row["pc_url"]),
        )
        if ebay is not None:
            result.append(ebay)
            outcomes["ebay_psa10_30d_median"] += 1
        else:
            outcomes[ebay_reason] += 1
    return result, outcomes


def load_pc_sales(connection: Any, variant_ids: list[int], *, as_of: datetime) -> list[dict[str, Any]]:
    # 2026-09-25: a sale in market_pc_sale_title_quarantine (058) is poison for
    # every reader, this eBay median included -- the same table the 058 view
    # applies, so there is one enforcement point, not a second copy of the rule.
    # No 1146 tolerance: the chain's migrate stage installs 058 before this runs,
    # and a missing table must stop the median, not silently re-admit the sales.
    if not variant_ids:
        return []
    marks = ",".join(["%s"] * len(variant_ids))
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT variant_id, source_code, external_entity_id, grader_code, grade_label,
                   sold_at, timestamp_quality, unit_price_usd, coverage_status,
                   transaction_fingerprint, source_payload_sha256
            FROM market_sale_observation s
            WHERE variant_id IN ({marks})
              AND source_code IN ({",".join(["%s"] * len(LIVE_EBAY_SOLD_SOURCE_CODES))})
              AND sold_at >= %s
              AND NOT EXISTS (
                SELECT 1 FROM market_pc_sale_title_quarantine tq
                WHERE tq.sale_observation_id = s.id
              )
            ORDER BY variant_id, sold_at, transaction_fingerprint
            """,
            (*variant_ids, *LIVE_EBAY_SOLD_SOURCE_CODES, (as_of - WINDOW).replace(tzinfo=None)),
        )
        return list(cursor.fetchall())


def plan_rows(rows: Iterable[Mapping[str, Any]], *, as_of: datetime) -> list[dict[str, Any]]:
    """Bind method + source evidence into the exact materialisation payload."""

    planned: list[dict[str, Any]] = []
    for row in rows:
        payload = {
            "contract": "pc_psa10_current_price_v1",
            "variantId": row["variant_id"],
            "source": row["source_code"],
            "externalEntityId": row["external_entity_id"],
            "method": row["method"],
            "field": row["field"],
            "artifactSha256": row["artifact_sha256"],
            "sourceUrl": row.get("source_url"),
            "selectedSaleFingerprints": row["sale_fingerprints"],
            "latestSoldDate": row["latest_sold_date"],
            "asOf": as_of.isoformat().replace("+00:00", "Z"),
        }
        planned.append({
            "variantId": row["variant_id"], "sourceCode": row["source_code"],
            "observedDate": row["observed_date"].isoformat(), "priceUsd": row["price_usd"],
            "effectiveAt": as_of.isoformat().replace("+00:00", "Z"),
            "sourcePriority": 95 if row["source_code"] == SOURCE_PC else 90,
            "metricStatus": "ready", "payloadSha256": sha256(payload), "payload": payload,
        })
    return sorted(planned, key=lambda item: (item["variantId"], item["sourceCode"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan exact PriceCharting PSA10 current price derivation")
    parser.add_argument("--map", type=Path, default=MAP_DEFAULT)
    parser.add_argument("--as-of", default=None, help="UTC ISO time; fixes replay input")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--sources",
        default=SOURCE_PC,
        help="comma-separated output families: pricecharting,ebay",
    )
    args = parser.parse_args()
    selected_sources = {
        value.strip().casefold()
        for value in str(args.sources).split(",")
        if value.strip()
    }
    if not selected_sources or not selected_sources <= {SOURCE_PC, SOURCE_EBAY}:
        parser.error("--sources accepts only pricecharting,ebay")
    as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00")) if args.as_of else datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        parser.error("--as-of must include a timezone")
    as_of = as_of.astimezone(timezone.utc)
    candidates = load_candidate_rows(args.map)
    connection = db()
    try:
        with connection.cursor() as cursor:
            exact, rejected = partition_exact_bindings(cursor, candidates)
        sales = load_pc_sales(connection, [int(row["variant_id"]) for row in exact], as_of=as_of)
    finally:
        connection.close()
    derived, outcomes = derive(exact, sales, as_of=as_of)
    derived = [
        row for row in derived if str(row["source_code"]).casefold() in selected_sources
    ]
    plan = plan_rows(derived, as_of=as_of)
    document = {
        "contract": "pc_psa10_current_price_v1", "readOnly": True,
        "asOf": as_of.isoformat().replace("+00:00", "Z"), "mapPath": str(args.map),
        "selectedSources": sorted(selected_sources),
        "candidateRows": len(candidates), "exactBindings": len(exact), "rejectedBindings": len(rejected),
        "pricechartingExplicit": sum(1 for row in plan if row["sourceCode"] == SOURCE_PC),
        "ebayMedian": sum(1 for row in plan if row["sourceCode"] == SOURCE_EBAY),
        "outcomes": dict(sorted(outcomes.items())), "rows": plan,
    }
    document["planSha256"] = sha256({key: value for key, value in document.items() if key != "planSha256"})
    text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
