#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""US PSA10 price fallback chain (no paid API keys).

Order (CARDZ rule 2026-07-28):
  1. tcgpricelookup SSR  — primary eBay graded + TCGPlayer raw
  2. tcgfish SSR         — secondary current Ungraded/PSA9/PSA10
  3. collectr api-v2     — only if product_id known (long history)
  4. pricecharting HTML  — eBay sold rows (when session/html available)

Does not call partner APIs. SNK remains JP ranking authority (separate path).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from curl_cffi import requests as cr

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "pipelines") not in sys.path:
    sys.path.insert(0, str(ROOT / "pipelines"))

from tcgpricelookup_ssr import fetch_card as tpl_fetch  # noqa: E402

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def session() -> cr.Session:
    s = cr.Session(impersonate="chrome131")
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    return s


def fetch_tcgfish(path: str, *, sess: cr.Session | None = None) -> dict[str, Any]:
    path = path if path.startswith("/") else f"/{path}"
    url = f"https://www.tcgfish.net{path}"
    s = sess or session()
    r = s.get(url, timeout=60)
    if r.status_code != 200:
        return {"ok": False, "sourceCode": "tcgfish", "error": f"http_{r.status_code}", "url": url}
    html = r.text or ""
    text = re.sub(r"<script[\s\S]*?</script>", " ", html)
    text = re.sub(r"<[^>]+>", "\n", text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    prices: dict[str, str] = {}
    for i, ln in enumerate(lines):
        if ln in {"Ungraded", "PSA 9", "PSA 10"} and i + 1 < len(lines) and lines[i + 1].startswith("$"):
            prices[ln] = lines[i + 1]
    def _money(label: str) -> float | None:
        raw = prices.get(label)
        if not raw:
            return None
        try:
            return float(raw.replace("$", "").replace(",", ""))
        except ValueError:
            return None

    return {
        "ok": bool(prices),
        "sourceCode": "tcgfish",
        "transport": "ssr_html",
        "url": url,
        "fetchedAt": utc_now(),
        "pricesText": prices,
        "ungradedUsd": _money("Ungraded"),
        "psa9Usd": _money("PSA 9"),
        "psa10Usd": _money("PSA 10"),
    }


def fetch_collectr(product_id: str, *, sess: cr.Session | None = None) -> dict[str, Any]:
    s = sess or session()
    s.get("https://app.getcollectr.com/", timeout=40)
    url = f"https://api-v2.getcollectr.com/catalog/products/{product_id}"
    r = s.get(
        url,
        headers={
            "Origin": "https://app.getcollectr.com",
            "Referer": "https://app.getcollectr.com/",
            "Accept": "application/json",
        },
        timeout=40,
    )
    if r.status_code != 200:
        return {"ok": False, "sourceCode": "collectr", "error": f"http_{r.status_code}", "productId": product_id}
    try:
        payload = r.json()
    except json.JSONDecodeError:
        return {"ok": False, "sourceCode": "collectr", "error": "invalid_json", "productId": product_id}
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, Mapping):
        return {"ok": False, "sourceCode": "collectr", "error": "missing_data", "productId": product_id}
    return {
        "ok": True,
        "sourceCode": "collectr",
        "transport": "api_v2_no_auth",
        "productId": str(data.get("product_id") or product_id),
        "name": data.get("product_name"),
        "marketPrice": data.get("market_price"),
        "gradedSubTypes": data.get("graded_sub_types") or [],
        "priceHistoryLen": len(data.get("price_history") or []),
        "priceHistory": data.get("price_history") or [],
        "imageUrl": data.get("image_url"),
        "fetchedAt": utc_now(),
    }


def resolve_us_price(work: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve one card through the fallback chain.

    work keys (any subset):
      tplSlug, tcgfishPath, collectrProductId, canonicalSourceCode, canonicalExternalId
    """

    attempts: list[dict[str, Any]] = []
    chosen: dict[str, Any] | None = None
    s = session()

    if work.get("tplSlug"):
        row = tpl_fetch(str(work["tplSlug"]), sess=s)
        attempts.append({"source": "tcgpricelookup", "ok": bool(row.get("ok")), "error": row.get("error")})
        if row.get("ok"):
            chosen = {
                "authority": "tcgpricelookup",
                "psa10Usd": (row.get("psa10") or {}).get("ebayAvg1d"),
                "psa10HistoryDays": (row.get("psa10") or {}).get("historyDays"),
                "historyDaysEmbedded": row.get("historyDaysEmbedded"),
                "payload": row,
            }

    if chosen is None and work.get("tcgfishPath"):
        row = fetch_tcgfish(str(work["tcgfishPath"]), sess=s)
        attempts.append({"source": "tcgfish", "ok": bool(row.get("ok")), "error": row.get("error")})
        if row.get("ok") and row.get("psa10Usd") is not None:
            chosen = {
                "authority": "tcgfish",
                "psa10Usd": row.get("psa10Usd"),
                "psa10HistoryDays": 0,
                "historyDaysEmbedded": 0,
                "payload": row,
            }

    if chosen is None and work.get("collectrProductId"):
        row = fetch_collectr(str(work["collectrProductId"]), sess=s)
        attempts.append({"source": "collectr", "ok": bool(row.get("ok")), "error": row.get("error")})
        if row.get("ok"):
            chosen = {
                "authority": "collectr",
                "psa10Usd": None,  # grade_id map needed for true PSA10
                "marketPrice": row.get("marketPrice"),
                "psa10HistoryDays": 0,
                "historyDaysEmbedded": row.get("priceHistoryLen"),
                "payload": row,
            }

    return {
        "ok": chosen is not None,
        "canonicalSourceCode": work.get("canonicalSourceCode"),
        "canonicalExternalId": work.get("canonicalExternalId"),
        "fetchedAt": utc_now(),
        "attempts": attempts,
        "result": chosen,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="US price fallback chain (no API keys)")
    parser.add_argument("--tpl-slug")
    parser.add_argument("--tcgfish-path")
    parser.add_argument("--collectr-product-id")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    work = {
        "tplSlug": args.tpl_slug,
        "tcgfishPath": args.tcgfish_path,
        "collectrProductId": args.collectr_product_id,
    }
    if not any(work.values()):
        raise SystemExit("provide at least one of --tpl-slug / --tcgfish-path / --collectr-product-id")
    row = resolve_us_price(work)
    text = json.dumps(row, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(json.dumps({"ok": row.get("ok"), "authority": (row.get("result") or {}).get("authority"), "out": str(args.out)}, sort_keys=True))
    else:
        print(text)
    return 0 if row.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
