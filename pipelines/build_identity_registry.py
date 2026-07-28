#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the single identity registry for the qualified POP>=1000 pool.

Every row = one printing, with:
  - stable CARDZ ids
  - provider ids
  - openable website links
  - lookupHints: which script key + how to call

See docs/SOURCE_LOOKUP_METHODS.md and docs/QUALIFIED_POOL_MAINTENANCE.md.
"""
from __future__ import annotations

import csv
import json
import os
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "data/runtime/private-source-map"
WORKLIST = MAP / "qualified-940-worklist.jsonl"
TPL_MAP = MAP / "tpl-slug-map.jsonl"
TPL_CARDS = MAP / "tcgpricelookup" / "cards"
OUT_JSONL = MAP / "qualified-940-identity.jsonl"
OUT_CSV = MAP / "qualified-940-identity.csv"

TPL_CARD = "https://tcgpricelookup.com/card/"
TCGPLAYER_PRODUCT = "https://www.tcgplayer.com/product/"
SNK_ITEM = "https://snkrdunk.com/app/products/"
GEMRATE_SEARCH = "https://www.gemrate.com/"  # site is search-first; id used in API
COLLECTR_APP = "https://app.getcollectr.com/"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_env() -> None:
    env = ROOT / "data/runtime/config/backend.env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
    os.environ.setdefault("CARDZ_DB_HOST", "127.0.0.1")


def identities_from_db() -> dict[int, dict[str, str]]:
    """variant_id -> {source_code: external_id}."""

    load_env()
    try:
        import pymysql
    except ImportError:
        return {}
    try:
        conn = pymysql.connect(
            host=os.environ.get("CARDZ_DB_HOST", "127.0.0.1"),
            port=int(os.environ.get("CARDZ_DB_PORT", "3308")),
            user=os.environ["CARDZ_DB_USER"],
            password=os.environ["CARDZ_DB_PASSWORD"],
            database=os.environ["CARDZ_DB_NAME"],
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
    except Exception:
        return {}
    cur = conn.cursor()
    cur.execute(
        """
        SELECT variant_id, source_code, external_entity_id
        FROM catalog_source_identity
        """
    )
    out: dict[int, dict[str, str]] = {}
    for row in cur.fetchall():
        vid = int(row["variant_id"])
        out.setdefault(vid, {})[str(row["source_code"])] = str(row["external_entity_id"])
    conn.close()
    return out


def watchlist_from_db() -> list[dict[str, Any]]:
    load_env()
    try:
        import pymysql
    except ImportError:
        return []
    try:
        conn = pymysql.connect(
            host=os.environ.get("CARDZ_DB_HOST", "127.0.0.1"),
            port=int(os.environ.get("CARDZ_DB_PORT", "3308")),
            user=os.environ["CARDZ_DB_USER"],
            password=os.environ["CARDZ_DB_PASSWORD"],
            database=os.environ["CARDZ_DB_NAME"],
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
    except Exception:
        return []
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
          w.gemrate_id, w.variant_id, w.card_name, w.set_name, w.collector_number,
          w.psa10_population, w.population_as_of,
          v.opaque_id, v.tcg_code
        FROM market_gemrate_psa10_watchlist w
        JOIN catalog_variant v ON v.id = w.variant_id
        ORDER BY w.psa10_population DESC, w.variant_id
        """
    )
    rows = []
    for r in cur.fetchall():
        rows.append(
            {
                "gemrateId": r["gemrate_id"],
                "variantId": int(r["variant_id"]),
                "opaqueId": r["opaque_id"],
                "tcg": r["tcg_code"],
                "name": r["card_name"],
                "setName": r["set_name"],
                "collectorNumber": r["collector_number"],
                "psa10Population": int(r["psa10_population"] or 0),
                "populationAsOf": str(r["population_as_of"] or ""),
                "populationAuthority": "gemrate",
            }
        )
    conn.close()
    return rows


def tpl_store_for_slug(slug: str) -> dict[str, Any] | None:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in slug)[:180]
    path = TPL_CARDS / f"{safe}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def gemrate_search_link(name: str, collector: str, set_name: str) -> str:
    q = " ".join(x for x in (name, collector, set_name) if x).strip()
    # Public site is search-first; deep links by id are not stable without app session.
    return GEMRATE_SEARCH + ("?q=" + urllib.parse.quote(q) if q else "")


def build_lookup_hints(row: dict[str, Any]) -> dict[str, Any]:
    """How each script should address this card."""

    return {
        "gemrate": {
            "role": "POP_authority",
            "script": "pipelines/gemrate_source.py",
            "keyField": "gemrateId",
            "key": row.get("gemrateId"),
            "call": "GET api.gemrate.com/v1/cards/{gemrateId}/population (needs-key); or POST www.gemrate.com/universal-search-query (no-key search)",
            "link": row.get("links", {}).get("gemrateSearch"),
        },
        "tcgpricelookup": {
            "role": "US_PSA10_price",
            "script": "pipelines/tcgpricelookup_ssr.py",
            "keyField": "tplSlug",
            "key": row.get("tplSlug"),
            "call": "GET tcgpricelookup.com/card/{tplSlug} with RSC:1 (no-key); catalog?game=&q= to discover slug",
            "link": row.get("links", {}).get("tpl"),
        },
        "tcgplayer": {
            "role": "card_art",
            "script": "pipelines/tcgplayer_images.py",
            "keyField": "tcgplayerId",
            "key": row.get("tcgplayerId"),
            "call": "CDN product/{id}_in_1000x1000.jpg; search mp-search-api (no partner key)",
            "link": row.get("links", {}).get("tcgplayer"),
        },
        "snk": {
            "role": "JP_PSA10_rank_price_and_trades",
            "script": "pipelines/snk_market_data.py → pipelines/ingest_snk_trades_sales.py",
            "keyField": "snkItemId",
            "key": row.get("snkItemId"),
            "call": "SNK JSON API by item id; trades→market_sale_observation",
            "link": row.get("links", {}).get("snk"),
        },
        "ebay": {
            "role": "US_PSA10_sales",
            "script": "pipelines/g10_ebay_ingest.py / pricecharting_ebay_export.py",
            "keyField": "ebayId",
            "key": row.get("ebayId") or (row.get("sources") or {}).get("ebay"),
            "call": "G10 altxyz dir or PC completed-sales; by catalog_source_identity",
            "link": None,
        },
        "liquidity": {
            "role": "preferred_sales_source_for_incremental",
            "script": "data/runtime/private-source-map/liquidity-source-registry.jsonl",
            "keyField": "preferredLiquiditySource",
            "key": (row.get("liquidity") or {}).get("preferredLiquiditySource"),
            "call": "Once a script lands sales for this card, registry records script+ids; daily only re-run those",
            "link": None,
        },
        "cardz": {
            "role": "canonical_identity",
            "script": "DB catalog_variant / frontend",
            "keyField": "opaqueId",
            "key": row.get("opaqueId"),
            "call": "variantId private; opaqueId public snapshot id",
            "link": None,
        },
    }


def build() -> list[dict[str, Any]]:
    db_rows = watchlist_from_db()
    work = {int(r["variantId"]): r for r in (db_rows or read_jsonl(WORKLIST))}
    tpl = {int(r["variantId"]): r for r in read_jsonl(TPL_MAP)}
    idmap = identities_from_db()

    ids = sorted(set(work) | set(tpl))
    rows: list[dict[str, Any]] = []
    for vid in ids:
        w = work.get(vid) or {}
        t = tpl.get(vid) or {}
        base = {**t, **w}  # DB/worklist wins on core identity
        # prefer worklist fields
        for k in (
            "gemrateId",
            "opaqueId",
            "tcg",
            "name",
            "setName",
            "collectorNumber",
            "psa10Population",
            "populationAsOf",
        ):
            if w.get(k) not in (None, ""):
                base[k] = w[k]

        slug = t.get("tplSlug") or None
        harvested = tpl_store_for_slug(slug) if slug else None
        tcgplayer_id = None
        tpl_image = None
        hist_days = None
        psa10 = None
        if isinstance(harvested, dict) and harvested.get("ok"):
            tcgplayer_id = harvested.get("tcgplayerId")
            tpl_image = harvested.get("imageUrl")
            hist_days = harvested.get("historyDaysEmbedded")
            psa10 = (harvested.get("psa10") or {}).get("ebayAvg1d")

        sources = idmap.get(vid) or {}
        snk_id = sources.get("snkrdunk") or sources.get("snk")
        tpl_id = sources.get("tcgpricelookup") or slug
        collectr_id = sources.get("collectr")

        name = base.get("name") or ""
        collector = base.get("collectorNumber") or ""
        set_name = base.get("setName") or ""
        gemrate_id = base.get("gemrateId")

        links = {
            "gemrateSearch": gemrate_search_link(str(name), str(collector), str(set_name)),
            "gemrateId": gemrate_id,  # API key path uses this, not a public card URL
            "tpl": f"{TPL_CARD}{slug}" if slug else None,
            "tcgplayer": f"{TCGPLAYER_PRODUCT}{tcgplayer_id}" if tcgplayer_id else None,
            "snk": f"{SNK_ITEM}{snk_id}" if snk_id and str(snk_id).isdigit() else None,
            "collectr": f"{COLLECTR_APP}" if collectr_id else None,
        }

        row: dict[str, Any] = {
            "variantId": vid,
            "opaqueId": base.get("opaqueId"),
            "gemrateId": gemrate_id,
            "tcg": base.get("tcg"),
            "name": name,
            "setName": set_name,
            "collectorNumber": collector,
            # POP always GemRate authority
            "populationAuthority": "gemrate",
            "psa10Population": base.get("psa10Population"),
            "populationAsOf": base.get("populationAsOf"),
            # provider ids
            "tplSlug": slug or tpl_id,
            "tplNeedsReview": bool(t.get("needsReview", not slug)),
            "tplSlugCandidate": t.get("tplSlugCandidate"),
            "tplMatchScore": t.get("tplMatchScore"),
            "tcgplayerId": tcgplayer_id,
            "snkItemId": snk_id,
            "collectrProductId": collectr_id,
            # harvest snapshot
            "tplHistoryDays": hist_days,
            "tplPsa10EbayUsd": psa10,
            "tplImageUrl": tpl_image,
            "links": links,
            "updatedAt": utc_now(),
        }
        row["lookup"] = build_lookup_hints(row)
        rows.append(row)
    return rows


def write_outputs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with OUT_JSONL.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    fieldnames = [
        "variantId",
        "opaqueId",
        "gemrateId",
        "tcg",
        "name",
        "setName",
        "collectorNumber",
        "populationAuthority",
        "psa10Population",
        "populationAsOf",
        "tplSlug",
        "tplNeedsReview",
        "tcgplayerId",
        "snkItemId",
        "tplHistoryDays",
        "tplPsa10EbayUsd",
        "linkGemrateSearch",
        "linkTpl",
        "linkTcgplayer",
        "linkSnk",
        "lookupGemrate",
        "lookupTpl",
        "lookupTcgplayer",
        "lookupSnk",
    ]
    with OUT_CSV.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            links = row.get("links") or {}
            lu = row.get("lookup") or {}
            w.writerow(
                {
                    "variantId": row.get("variantId"),
                    "opaqueId": row.get("opaqueId"),
                    "gemrateId": row.get("gemrateId"),
                    "tcg": row.get("tcg"),
                    "name": row.get("name"),
                    "setName": row.get("setName"),
                    "collectorNumber": row.get("collectorNumber"),
                    "populationAuthority": row.get("populationAuthority"),
                    "psa10Population": row.get("psa10Population"),
                    "populationAsOf": row.get("populationAsOf"),
                    "tplSlug": row.get("tplSlug"),
                    "tplNeedsReview": row.get("tplNeedsReview"),
                    "tcgplayerId": row.get("tcgplayerId"),
                    "snkItemId": row.get("snkItemId"),
                    "tplHistoryDays": row.get("tplHistoryDays"),
                    "tplPsa10EbayUsd": row.get("tplPsa10EbayUsd"),
                    "linkGemrateSearch": links.get("gemrateSearch"),
                    "linkTpl": links.get("tpl"),
                    "linkTcgplayer": links.get("tcgplayer"),
                    "linkSnk": links.get("snk"),
                    "lookupGemrate": (lu.get("gemrate") or {}).get("call"),
                    "lookupTpl": (lu.get("tcgpricelookup") or {}).get("call"),
                    "lookupTcgplayer": (lu.get("tcgplayer") or {}).get("call"),
                    "lookupSnk": (lu.get("snk") or {}).get("call"),
                }
            )

    return {
        "rows": len(rows),
        "withGemrateId": sum(1 for r in rows if r.get("gemrateId")),
        "withTplSlug": sum(1 for r in rows if r.get("tplSlug")),
        "withTcgplayerId": sum(1 for r in rows if r.get("tcgplayerId")),
        "withSnk": sum(1 for r in rows if r.get("snkItemId")),
        "withTplPrice": sum(1 for r in rows if r.get("tplPsa10EbayUsd") is not None),
        "jsonl": str(OUT_JSONL),
        "csv": str(OUT_CSV),
        "builtAt": utc_now(),
        "note": "POP authority is always gemrate; TPL is US price only",
    }


def main() -> int:
    rows = build()
    summary = write_outputs(rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
