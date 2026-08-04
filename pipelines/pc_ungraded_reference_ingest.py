#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ingest exact PriceCharting Ungraded references into their own table.

The full PriceCharting map is only a candidate receipt.  A row is accepted
only when its saved HTML repeats the mapped product id and the canonical DB
already has that exact ``pricecharting`` bind for the same variant.  The
``chart.used`` value is a RAW/Ungraded detail reference; it never writes to
``market_price_observation`` and is not a PSA10 price, rank, or market-cap
input.

Default mode is read-only.  ``--write`` performs idempotent ``INSERT IGNORE``
after migration 019 has been applied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from html import unescape
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from failure_ledger import record_failure, record_resolution  # noqa: E402
from pricecharting_page_parse import parse_product_html  # noqa: E402

MAP_DEFAULT = ROOT / "data/runtime/private-source-map/c11_pc_ebay_map_full900.jsonl"
SOURCE_CODE = "pricecharting"
CENT = Decimal("0.000001")


def load_env() -> None:
    env = ROOT / "data/runtime/config/backend.env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().replace("\r", ""))
    os.environ.setdefault("CARDZ_DB_HOST", "127.0.0.1")


def db():
    import pymysql

    load_env()
    return pymysql.connect(
        host=os.environ.get("CARDZ_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("CARDZ_DB_PORT", "3308")),
        user=os.environ["CARDZ_DB_USER"],
        password=os.environ["CARDZ_DB_PASSWORD"],
        database=os.environ["CARDZ_DB_NAME"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def money(value: Decimal) -> str:
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def resolve_html_path(row: dict[str, Any]) -> Path | None:
    for key in ("htmlPath", "html_path"):
        value = row.get(key)
        if not value:
            continue
        path = Path(str(value))
        if path.is_file():
            return path
        rooted = ROOT / path
        if rooted.is_file():
            return rooted
    return None


def _valid_url(value: Any) -> str | None:
    url = str(value or "").strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "www.pricecharting.com":
        return None
    if not parsed.path.startswith("/game/"):
        return None
    return url


def _url_key(value: str) -> str:
    return unescape(value).rstrip("/")


def canonical_url_from_html(html: str) -> str | None:
    match = re.search(
        r'rel=["\']canonical["\'][^>]*href=["\']([^"\']+)', html, re.IGNORECASE,
    ) or re.search(
        r'href=["\']([^"\']+)["\'][^>]*rel=["\']canonical["\']', html, re.IGNORECASE,
    )
    return _valid_url(unescape(match.group(1))) if match else None


def load_candidate_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_variants: set[int] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            continue
        try:
            variant_id = int(row.get("variant_id"))
        except (TypeError, ValueError):
            continue
        product_id = str(row.get("pc_product_id") or "").strip()
        url = _valid_url(row.get("pc_url"))
        if (
            variant_id <= 0
            or not product_id.isdigit()
            or url is None
            or row.get("ready_for_c12") is not True
            or str(row.get("confidence") or "").casefold() != "high"
            or variant_id in seen_variants
        ):
            continue
        seen_variants.add(variant_id)
        rows.append({**row, "variant_id": variant_id, "pc_product_id": product_id, "pc_url": url})
    return rows


def partition_exact_bindings(cursor: Any, rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Require current canonical PC external id → exact variant ownership."""

    candidates = list(rows)
    if not candidates:
        return [], []
    cursor.execute(
        """
        SELECT variant_id, external_entity_id
        FROM catalog_source_identity
        WHERE source_code=%s AND match_status='exact'
          AND external_entity_id IN (""" + ",".join(["%s"] * len(candidates)) + ")",
        (SOURCE_CODE, *[row["pc_product_id"] for row in candidates]),
    )
    bound = {
        (int(value["variant_id"]), str(value["external_entity_id"]))
        for value in cursor.fetchall()
    }
    accepted = [row for row in candidates if (row["variant_id"], row["pc_product_id"]) in bound]
    rejected = [row for row in candidates if (row["variant_id"], row["pc_product_id"]) not in bound]
    return accepted, rejected


def source_observed_at(point: Any) -> datetime | None:
    if not isinstance(point, list) or len(point) < 2:
        return None
    try:
        milliseconds = int(point[0])
    except (TypeError, ValueError):
        return None
    if milliseconds <= 0:
        return None
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).replace(tzinfo=None)


def collect_references(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    for row in rows:
        variant_id = int(row["variant_id"])
        html_path = resolve_html_path(row)
        if html_path is None:
            outcomes.append({"variant_id": variant_id, "status": "no_html", "row": row})
            continue
        html_bytes = html_path.read_bytes()
        html = html_bytes.decode("utf-8", errors="replace")
        canonical_url = canonical_url_from_html(html)
        if canonical_url is None or _url_key(canonical_url) != _url_key(row["pc_url"]):
            outcomes.append({"variant_id": variant_id, "status": "canonical_url_mismatch", "row": row})
            continue
        parsed = parse_product_html(html, source_url=row["pc_url"])
        if not parsed.get("ok"):
            outcomes.append({"variant_id": variant_id, "status": "parse_fail", "row": row})
            continue
        product = parsed.get("product") if isinstance(parsed.get("product"), dict) else {}
        if str(product.get("id") or "") != row["pc_product_id"]:
            outcomes.append({"variant_id": variant_id, "status": "product_id_mismatch", "row": row})
            continue
        chart = parsed.get("chart") if isinstance(parsed.get("chart"), dict) else {}
        used = chart.get("used") if isinstance(chart.get("used"), dict) else {}
        observed_at = source_observed_at(used.get("last"))
        try:
            price = Decimal(str(used.get("last_usd")))
        except Exception:
            price = Decimal(0)
        if used.get("label") != "Ungraded" or observed_at is None or price <= 0:
            outcomes.append({"variant_id": variant_id, "status": "no_positive_ungraded", "row": row})
            continue
        accepted.append(
            {
                "variant_id": variant_id,
                "external_entity_id": row["pc_product_id"],
                "source_url": row["pc_url"],
                "observed_at": observed_at,
                "price_usd": money(price),
                "payload_sha256": hashlib.sha256(html_bytes).hexdigest(),
            }
        )
        outcomes.append({"variant_id": variant_id, "status": "accepted", "row": row})
    return accepted, outcomes


def write_references(connection: Any, references: list[dict[str, Any]]) -> int:
    if not references:
        return 0
    payload = [
        (
            item["variant_id"], SOURCE_CODE, item["external_entity_id"], item["source_url"],
            item["observed_at"], item["price_usd"], item["payload_sha256"],
        )
        for item in references
    ]
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT IGNORE INTO market_ungraded_reference_price
                (variant_id, source_code, external_entity_id, source_url, observed_at,
                 price_usd, source_payload_sha256)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            payload,
        )
        inserted = int(cursor.rowcount)
    connection.commit()
    return inserted


def record_outcomes(outcomes: Iterable[dict[str, Any]]) -> None:
    for outcome in outcomes:
        row = outcome["row"]
        status = str(outcome["status"])
        item_key = int(outcome["variant_id"])
        if status == "accepted":
            record_resolution(
                source=SOURCE_CODE,
                stage="normalize_ungraded_reference",
                script=__file__,
                item_key=item_key,
                resolution="accepted_ungraded_reference",
                context={"pcProductId": row["pc_product_id"]},
            )
            continue
        record_failure(
            source=SOURCE_CODE,
            stage="normalize_ungraded_reference",
            script=__file__,
            item_key=item_key,
            reason_code=status,
            message=status,
            retryable=status in {"no_html", "parse_fail"},
            url=row.get("pc_url"),
            context={"pcProductId": row.get("pc_product_id")},
            next_action="retry" if status in {"no_html", "parse_fail"} else "agent_review",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest PriceCharting Ungraded detail references")
    parser.add_argument("--map", type=Path, default=MAP_DEFAULT)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")

    rows = load_candidate_rows(args.map)
    if args.limit:
        rows = rows[:args.limit]
    connection = db()
    try:
        with connection.cursor() as cursor:
            exact_rows, rejected_bindings = partition_exact_bindings(cursor, rows)
        references, outcomes = collect_references(exact_rows)
        for row in rejected_bindings:
            outcomes.append({"variant_id": row["variant_id"], "status": "exact_binding_missing", "row": row})
        record_outcomes(outcomes)
        inserted = write_references(connection, references) if args.write else 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    status_counts: dict[str, int] = {}
    for outcome in outcomes:
        status = str(outcome["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    print(json.dumps({
        "asOf": utc_now(), "write": bool(args.write), "mapPath": str(args.map),
        "candidateRows": len(rows), "exactBindings": len(exact_rows),
        "referencesAccepted": len(references), "inserted": inserted,
        "outcomes": status_counts,
        "contract": "PriceCharting Ungraded detail reference only; not PSA10 market price/rank/market cap",
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
