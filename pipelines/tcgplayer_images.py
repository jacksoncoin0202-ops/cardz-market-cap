#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TCGplayer card-art candidates — primary image source for CARDZ.

Search + CDN only. Not a PSA10 price authority.
Exact printing selection is caller's job when multiple productIds match.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from curl_cffi import requests as curl_requests

SEARCH_URL = "https://mp-search-api.tcgplayer.com/v1/search/request"
CDN_1000 = "https://tcgplayer-cdn.tcgplayer.com/product/{pid}_in_1000x1000.jpg"
CDN_ORIG = "https://product-images.tcgplayer.com/{pid}.jpg"
HEADERS = {
    "Origin": "https://www.tcgplayer.com",
    "Referer": "https://www.tcgplayer.com/",
    "Accept": "application/json",
}


def search_products(query: str, *, size: int = 24, product_line: str | None = None) -> list[dict[str, Any]]:
    body: dict[str, Any] = {
        "algorithm": "sales_dismax",
        "from": 0,
        "size": size,
        "filters": {"term": {}, "range": {}, "match": {}},
        "listingSearch": {
            "context": {"cart": {}},
            "filters": {
                "term": {"sellerStatus": "Live", "channelId": 0},
                "range": {"quantity": {"gte": 1}},
                "exclude": {"channelExclusion": 0},
            },
        },
        "context": {"cart": {}, "shippingCountry": "US", "userProfile": {}},
        "settings": {"useFuzzySearch": True, "didYouMean": {}},
        "sort": {},
    }
    if product_line:
        body["filters"]["term"]["productLineName"] = [product_line]

    response = curl_requests.post(
        f"{SEARCH_URL}?q={query}&isList=false",
        json=body,
        impersonate="chrome",
        timeout=30,
        headers=HEADERS,
    )
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results") or []
    hits = results[0].get("results", []) if results else []
    out: list[dict[str, Any]] = []
    for hit in hits:
        if not hit.get("productId"):
            continue
        pid = int(hit["productId"])
        out.append(
            {
                "productId": pid,
                "productName": hit.get("productName"),
                "setName": hit.get("setName"),
                "productUrlName": hit.get("productUrlName"),
                "rarity": hit.get("rarityName"),
                "number": (hit.get("customAttributes") or {}).get("number")
                if isinstance(hit.get("customAttributes"), Mapping)
                else None,
                "cdn": CDN_1000.format(pid=pid),
                "cdnOrig": CDN_ORIG.format(pid=pid),
            }
        )
    return out


def product_line_for_tcg(tcg: str | None) -> str | None:
    code = (tcg or "").casefold()
    if code in {"pokemon", "ptcg"}:
        return "Pokemon"
    if code in {"one-piece", "optcg", "onepiece"}:
        return "One Piece Card Game"
    return None


def _norm_number(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def rank_hits(
    hits: list[Mapping[str, Any]],
    *,
    collector_number: str,
    set_name: str | None = None,
    name: str | None = None,
) -> list[dict[str, Any]]:
    """Prefer exact collector number, then set/name token overlap. Never auto-pick alone."""

    wanted = _norm_number(collector_number)
    set_tokens = {t for t in re.findall(r"[a-z0-9]+", (set_name or "").casefold()) if len(t) > 2}
    name_tokens = {t for t in re.findall(r"[a-z0-9]+", (name or "").casefold()) if len(t) > 2}
    ranked: list[tuple[int, dict[str, Any]]] = []
    for hit in hits:
        row = dict(hit)
        score = 0
        number = _norm_number(row.get("number") or "")
        product_name = str(row.get("productName") or "")
        set_hit = str(row.get("setName") or "")
        if wanted and (number == wanted or wanted in _norm_number(product_name)):
            score += 100
        if set_tokens:
            score += 10 * len(set_tokens & set(re.findall(r"[a-z0-9]+", set_hit.casefold())))
        if name_tokens:
            score += 5 * len(name_tokens & set(re.findall(r"[a-z0-9]+", product_name.casefold())))
        row["matchScore"] = score
        ranked.append((score, row))
    ranked.sort(key=lambda item: (-item[0], int(item[1].get("productId") or 0)))
    return [row for _, row in ranked]


def download_product_image(product_id: int) -> bytes:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.tcgplayer.com/",
        "Origin": "https://www.tcgplayer.com",
    }
    last_error: Exception | None = None
    for url in (CDN_1000.format(pid=product_id), CDN_ORIG.format(pid=product_id)):
        try:
            response = curl_requests.get(url, headers=headers, impersonate="chrome", timeout=30)
            if response.status_code == 200 and response.content and len(response.content) > 1000:
                ctype = (response.headers.get("content-type") or "").lower()
                if "image" in ctype or response.content[:3] in (b"\xff\xd8\xff", b"\x89PN"):
                    return response.content
                # webp/jpeg without content-type still ok if magic matches
                if response.content[:4] == b"RIFF" or response.content[:2] == b"\xff\xd8":
                    return response.content
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
    if last_error:
        raise RuntimeError(f"TCGplayer CDN failed for {product_id}: {last_error}") from last_error
    raise RuntimeError(f"TCGplayer CDN returned no image for productId={product_id}")


def resolve_tcgplayer_candidate(
    card: Mapping[str, Any],
    *,
    product_id: int | None = None,
    allow_first_hit: bool = False,
) -> dict[str, Any] | None:
    """Return best candidate metadata + raw image bytes, or None.

    Default refuses ambiguous multi-hit without product_id (exact printing safety).
    Set allow_first_hit=True only for offline tooling with human review after.
    """

    if product_id:
        raw = download_product_image(int(product_id))
        return {
            "productId": int(product_id),
            "cdn": CDN_1000.format(pid=int(product_id)),
            "raw": raw,
            "selection": "explicit_product_id",
        }

    collector = str(
        (card.get("collectorNumber") or {}).get("display")
        if isinstance(card.get("collectorNumber"), Mapping)
        else card.get("collectorNumber")
        or ""
    ).strip()
    if not collector:
        return None

    tcg = str(card.get("tcg") or "")
    names = card.get("names") if isinstance(card.get("names"), Mapping) else {}
    name = str((names or {}).get("en") or card.get("name") or "")
    sets = card.get("sets") if isinstance(card.get("sets"), Mapping) else {}
    set_name = str((sets or {}).get("en") or card.get("setName") or "")

    hits = search_products(collector, product_line=product_line_for_tcg(tcg))
    ranked = rank_hits(hits, collector_number=collector, set_name=set_name, name=name)
    if not ranked:
        return None
    top = ranked[0]
    # Ambiguity gate: require clear number match winner unless allow_first_hit
    if not allow_first_hit:
        if int(top.get("matchScore") or 0) < 100:
            return {
                "productId": None,
                "candidates": ranked[:8],
                "selection": "ambiguous_no_number_match",
                "raw": None,
            }
        # multiple exact number matches → still ambiguous
        exact = [row for row in ranked if int(row.get("matchScore") or 0) >= 100]
        if len(exact) > 1 and not (
            # same productId only once
            len({int(r["productId"]) for r in exact}) == 1
        ):
            # if top score uniquely higher, take it; else hand back candidates
            if int(exact[0].get("matchScore") or 0) == int(exact[1].get("matchScore") or 0):
                return {
                    "productId": None,
                    "candidates": exact[:8],
                    "selection": "ambiguous_multi_printing",
                    "raw": None,
                }

    pid = int(top["productId"])
    raw = download_product_image(pid)
    return {
        "productId": pid,
        "cdn": CDN_1000.format(pid=pid),
        "raw": raw,
        "selection": "ranked_search",
        "candidates": ranked[:8],
        "matchScore": top.get("matchScore"),
    }
