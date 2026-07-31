#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export PriceCharting PSA10 completed sales into ebay_sold_data input shape.

PriceCharting product pages embed eBay completed-sale rows
(``div.completed-auctions-manual-only``). Direct eBay sold search is still
PerimeterX-blocked (2026-07-28 probe); this transport is the production
eBay-derived path until a direct sold route is reopened.

Input map (JSON/JSONL), one row per CARDZ card:
  {
    "canonicalSourceCode": "snkrdunk",
    "canonicalExternalId": "91118",
    "language": "ja",
    "tcg": "pokemon",
    "collectorNumber": "227/S-P",
    "edition": "", "parallel": "", "finish": "",
    "htmlPath": "data/private/pricecharting_session/html/....html"
      OR "sourceUrl": "https://www.pricecharting.com/game/..."
      OR "productId": 630417
  }

Output: JSON array suitable for ``ebay_sold_data.py --input``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "pipelines") not in sys.path:
    sys.path.insert(0, str(ROOT / "pipelines"))

from failure_ledger import record_failure, record_resolution  # noqa: E402
from pricecharting_page_parse import parse_product_html  # noqa: E402

LANGUAGE_LABELS = {
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "zhCN": "Simplified Chinese",
    "zhTW": "Traditional Chinese",
}
TCG_LABELS = {
    "pokemon": "Pokemon",
    "one-piece": "One Piece",
}


def _read_json_or_jsonl(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8-sig")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        decoded = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if isinstance(decoded, Mapping):
        decoded = decoded.get("cards", decoded.get("rows", decoded.get("items", [])))
    if not isinstance(decoded, list):
        raise ValueError(f"map must be a JSON array or JSONL: {path}")
    rows: list[dict[str, Any]] = []
    for row in decoded:
        if not isinstance(row, Mapping):
            raise ValueError(f"map row is not an object: {path}")
        rows.append(dict(row))
    return rows


def _clean_ebay_url(url: str | None, itm: str | None) -> str | None:
    if url and "ebay.com/itm/" in url:
        return url.split("?", 1)[0].replace("http://", "https://")
    if itm:
        return f"https://www.ebay.com/itm/{itm}"
    return None


def sales_rows_to_listings(
    sales_block: Mapping[str, Any] | None,
    *,
    card: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Convert parse_product_html psa10.completed_sales into ebay_sold listings."""

    if not isinstance(sales_block, Mapping):
        return []
    rows = sales_block.get("rows") or []
    if not isinstance(rows, list):
        return []

    language = str(card.get("language") or "")
    language_label = LANGUAGE_LABELS.get(language, language)
    tcg = str(card.get("tcg") or "")
    tcg_label = TCG_LABELS.get(tcg, tcg)
    edition = card.get("edition") or ""
    parallel = card.get("parallel") or ""
    finish = card.get("finish") or ""

    listings: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        title = str(row.get("title") or "").strip()
        price = row.get("price_usd")
        sold_date = row.get("date")
        url = _clean_ebay_url(
            str(row.get("ebay_url") or "") or None,
            str(row.get("ebay_itm") or "") or None,
        )
        if not title or not isinstance(price, (int, float)) or float(price) <= 0 or not sold_date or not url:
            continue
        listings.append(
            {
                "sold": True,
                "listing_status": "completed_sold",
                "status": "completed_sold",
                "title": title,
                "url": url,
                "sold_date": str(sold_date),
                "price_amount": float(price),
                "currency": "USD",
                "quantity": 1,
                "grader": "PSA",
                "grade": "10",
                "language": language_label,
                "tcg": tcg_label,
                "edition": edition,
                "parallel": parallel,
                "finish": finish,
                "transport": "pricecharting_product_page",
                "item_id": str(row.get("ebay_itm") or ""),
            }
        )
    return listings


def load_html_for_row(row: Mapping[str, Any]) -> tuple[str, str | None]:
    html_path = row.get("htmlPath") or row.get("html_path")
    if html_path:
        path = Path(str(html_path))
        if not path.is_file():
            path = ROOT / path
        if not path.is_file():
            raise FileNotFoundError(f"htmlPath missing: {html_path}")
        return path.read_text(encoding="utf-8", errors="replace"), str(row.get("sourceUrl") or path)

    source_url = row.get("sourceUrl") or row.get("url")
    if source_url:
        # Prefer pre-saved session HTML; live fetch is optional.
        from pricecharting_http_fetch import fetch as pc_fetch  # local import

        out = ROOT / "data/private/pricecharting_session/html" / "_export_fetch.html"
        code = pc_fetch(str(source_url), out)
        if code != 0:
            raise RuntimeError(f"pricecharting fetch failed for {source_url} (exit {code})")
        return out.read_text(encoding="utf-8", errors="replace"), str(source_url)

    raise ValueError("map row needs htmlPath or sourceUrl")


def export_row(row: Mapping[str, Any], *, fetched_at: str) -> dict[str, Any]:
    source_code = str(row.get("canonicalSourceCode") or row.get("sourceCode") or "").strip()
    external_id = str(row.get("canonicalExternalId") or row.get("externalEntityId") or "").strip()
    if not source_code or not external_id:
        raise ValueError("map row requires canonicalSourceCode + canonicalExternalId")

    html, source_url = load_html_for_row(row)
    parsed = parse_product_html(html, source_url=source_url)
    if not parsed.get("ok"):
        raise RuntimeError(
            f"pricecharting parse failed for {source_code}:{external_id}: {parsed.get('error')}"
        )
    psa10 = parsed.get("psa10") if isinstance(parsed.get("psa10"), Mapping) else {}
    sales = psa10.get("completed_sales") if isinstance(psa10, Mapping) else None
    listings = sales_rows_to_listings(sales if isinstance(sales, Mapping) else None, card=row)
    product = parsed.get("product") if isinstance(parsed.get("product"), Mapping) else {}
    history = psa10.get("history") if isinstance(psa10, Mapping) else None
    last_usd = None
    if isinstance(history, Mapping):
        last_usd = history.get("last_usd")

    return {
        "sourceCode": source_code,
        "externalEntityId": external_id,
        "fetchedAt": fetched_at,
        "transport": "pricecharting",
        "pricechartingProductId": product.get("id"),
        "pricechartingSourceUrl": source_url,
        "psa10GuideUsd": last_usd,
        "listings": listings,
        "listingCount": len(listings),
    }


def export_map(rows: Iterable[Mapping[str, Any]], *, fetched_at: str | None = None) -> list[dict[str, Any]]:
    stamp = fetched_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    exported: list[dict[str, Any]] = []
    failures: list[Exception] = []
    for index, row in enumerate(rows):
        source_code = str(row.get("canonicalSourceCode") or row.get("sourceCode") or "").strip()
        external_id = str(row.get("canonicalExternalId") or row.get("externalEntityId") or "").strip()
        item_key = (
            f"{source_code}:{external_id}"
            if source_code and external_id
            else str(row.get("variant_id") or row.get("variantId") or f"row-{index}")
        )
        try:
            exported.append(export_row(row, fetched_at=stamp))
            record_resolution(
                source="pricecharting",
                stage="export_ebay_sales",
                script=__file__,
                item_key=item_key,
                resolution="row_exported",
            )
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError, FileNotFoundError) as error:
            failures.append(error)
            record_failure(
                source="pricecharting",
                stage="export_ebay_sales",
                script=__file__,
                item_key=item_key,
                reason_code="row_export_failed",
                message=str(error),
                retryable=True,
                url=str(row.get("sourceUrl") or row.get("url") or "") or None,
                context={"sourceCode": source_code, "externalEntityId": external_id},
                next_action="retry",
                error_type=type(error).__name__,
            )
    if failures:
        raise RuntimeError(
            f"PriceCharting export has {len(failures)} failed row(s); output was not promoted"
        )
    return exported


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="PriceCharting PSA10 sales → ebay_sold_data input")
    parser.add_argument("--map", type=Path, required=True, help="JSON/JSONL card→html map")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data/runtime/private-source-map/ebay-sold-from-pricecharting.json",
    )
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    rows = _read_json_or_jsonl(args.map.resolve())
    if args.limit:
        rows = rows[: args.limit]
    exported = export_map(rows)
    atomic_write_json(args.out.resolve(), exported)
    print(
        json.dumps(
            {
                "cards": len(exported),
                "withListings": sum(1 for row in exported if row.get("listingCount")),
                "listings": sum(int(row.get("listingCount") or 0) for row in exported),
                "output": str(args.out.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, FileNotFoundError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
