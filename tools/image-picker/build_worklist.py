# -*- coding: utf-8 -*-
"""Build FE image-picker worklist + candidate image pack for human review.

Policy (2026-07-30, operator):
  - SNK + G10 圖／價 = 物認 100% OK，預設 auto-pick，唔入人類 queue
  - G10 圖/價本身亦係 SNK 血統 + eBay 數據；當 trusted identity
  - market-assets 歷史 bake 視作 SNK 血統 → trusted
  - 只將「有 Double（多於 1 個 distinct SHA）且含非 SNK/G10 來源」
    或「明顯爭議（rejected / 非信任多圖）」嘅卡入 worklist

Usage:
  python -X utf8 tools/image-picker/build_worklist.py
"""
from __future__ import annotations

import json
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pipelines"))

from qualified_pool_operator import db, load_env

OUT = Path(__file__).resolve().parent / "public" / "data"
IMG_OUT = OUT / "images"
PUBLIC_ASSETS = ROOT / "data/public/market-assets"
WEB_ASSETS = ROOT / "apps/web/public/market-assets"

# 物認 100% OK — 唔使人類覆核
TRUSTED_CHANNELS = frozenset(
    {
        "g10_local_files",  # G10 工具鏈（底圖/價多來自 SNK + eBay）
        "snkrdunk_http",
        "market_assets_store",  # prior SNK / ensure bake
    }
)

PRICE_SOURCE_LABEL = {
    "snk_psa10": "SNK PSA10（口價/日線）",
    "snk": "SNK",
    "snkrdunk": "SNK",
    "ebay": "G10/eBay median（存量；非 live eBay API）",
    "g10_kline": "G10 K 線 bridge",
    "tcgfish": "TCGFish stub",
    "tcgpricelookup": "TPL（已 purge exact；僅參考）",
}


def infer_source(private_key: str | None, semantic: str | None) -> dict:
    key = (private_key or "").replace("\\", "/")
    script = "unknown"
    channel = "unknown"
    kl = key.lower()
    if "g10/" in kl or "grade10" in kl or "snkrdunk_" in kl:
        script = "pipelines/g10_asset_ingest.py"
        channel = "g10_local_files"
    elif "limitless" in kl:
        script = "pipelines/op_limitless_images.py"
        channel = "limitless_cdn"
    elif key.startswith("market-assets/") or "market-assets" in kl:
        script = "snk_image_ingest / ensure_image_abc / prior bake"
        channel = "market_assets_store"
    elif "snk" in kl:
        script = "pipelines/snk_image_ingest.py"
        channel = "snkrdunk_http"
    return {
        "script": script,
        "channel": channel,
        "privateObjectKey": key,
        "semanticMatchStatus": semantic,
        "trustedIdentity": channel in TRUSTED_CHANNELS,
    }


def find_webp(sha: str) -> Path | None:
    for base in (PUBLIC_ASSETS, WEB_ASSETS):
        p = base / f"{sha}.webp"
        if p.is_file():
            return p
    return None


def money(v) -> str | None:
    if v is None:
        return None
    try:
        return f"{Decimal(str(v)):.2f}"
    except Exception:
        return str(v)


def build_links(identities: list[dict], name: str, tcg: str) -> list[dict]:
    """External links for human cross-check (SNK / PC / TCG / search)."""
    links: list[dict] = []
    by = {str(i["source_code"]).lower(): i for i in identities}

    snk = by.get("snkrdunk") or by.get("snk") or by.get("snk_psa10")
    if snk and snk.get("external_entity_id"):
        eid = str(snk["external_entity_id"]).strip()
        links.append(
            {
                "label": "SNK 商品頁",
                "source": "snkrdunk",
                "url": f"https://snkrdunk.com/apparels/{eid}",
                "entityId": eid,
            }
        )

    pc = by.get("pricecharting")
    if pc and pc.get("external_entity_id"):
        eid = str(pc["external_entity_id"]).strip()
        links.append(
            {
                "label": "PriceCharting（eBay sold 頁）",
                "source": "pricecharting",
                "url": f"https://www.pricecharting.com/game/{eid}",
                "entityId": eid,
                "note": "C11 成交常標 source=ebay，真渠道係 PC",
            }
        )

    tpl = by.get("tcgpricelookup")
    if tpl and tpl.get("external_entity_id"):
        slug = str(tpl["external_entity_id"]).strip()
        links.append(
            {
                "label": "TCGPriceLookup slug",
                "source": "tcgpricelookup",
                "url": f"https://www.tcgpricelookup.com/card/{quote(slug)}",
                "entityId": slug,
                "note": "TPL 已 purge exact 價；link 只供認卡",
            }
        )

    op = by.get("op_limitless")
    if op and op.get("external_entity_id"):
        code = str(op["external_entity_id"]).strip()
        links.append(
            {
                "label": "Limitless OP",
                "source": "op_limitless",
                "url": f"https://limitlesstcg.com/cards/{quote(code)}",
                "entityId": code,
            }
        )

    ebay = by.get("ebay")
    if ebay and ebay.get("external_entity_id"):
        links.append(
            {
                "label": "G10 altxyz UUID（eBay 身份）",
                "source": "ebay",
                "url": None,
                "entityId": str(ebay["external_entity_id"]),
                "note": "本機 grade10-scraper/data/cards/altxyz/{uuid}/ — 非 live eBay 連結",
            }
        )

    q = " ".join(x for x in [name, tcg, "PSA 10"] if x)
    if q.strip():
        links.append(
            {
                "label": "Google 搜尋（認卡）",
                "source": "google",
                "url": f"https://www.google.com/search?q={quote(q)}",
                "entityId": None,
            }
        )
        links.append(
            {
                "label": "eBay 搜尋 sold（參考口價）",
                "source": "ebay_search",
                "url": f"https://www.ebay.com/sch/i.html?_nkw={quote(q + ' PSA 10')}&LH_Sold=1&LH_Complete=1",
                "entityId": None,
                "note": "搜尋頁，唔係 DB 綁定 listing",
            }
        )
    return links


def build_candidate(im: dict, key_letter: str, *, copy_files: bool) -> tuple[dict, int, int]:
    """Return (candidate, copied_delta, missing_delta)."""
    copied = missing = 0
    sha = str(im.get("content_sha256") or "")
    if len(sha) != 64:
        return {}, 0, 0
    src = find_webp(sha)
    if src and copy_files:
        dest = IMG_OUT / f"{sha}.webp"
        if not dest.is_file():
            shutil.copy2(src, dest)
            copied = 1
        url = f"./data/images/{sha}.webp"
    elif src:
        url = f"./data/images/{sha}.webp"
        dest = IMG_OUT / f"{sha}.webp"
        if not dest.is_file():
            # still copy if missing
            shutil.copy2(src, dest)
            copied = 1
    else:
        missing = 1
        url = None
    prov = infer_source(im.get("private_object_key"), im.get("semantic_match_status"))
    cand = {
        "key": key_letter,
        "assetId": im["asset_id"],
        "contentSha256": sha,
        "url": url,
        "width": im.get("width_px"),
        "height": im.get("height_px"),
        "publicAllowed": bool(im.get("public_allowed")),
        "semanticMatchStatus": im.get("semantic_match_status"),
        "provenance": prov,
    }
    return cand, copied, missing


def unique_by_sha(candidates: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for c in candidates:
        sha = c.get("contentSha256") or ""
        if not sha or sha in seen:
            continue
        seen.add(sha)
        out.append(c)
    return out


def auto_pick_score(c: dict) -> tuple:
    """Higher is better for trusted auto-pick.

    DADDY: image trust order = G10 first, then SNK, then market-assets (SNK bake).
    """
    ch = (c.get("provenance") or {}).get("channel") or ""
    channel_rank = {
        "g10_local_files": 50,  # G10 優先（高資訊 + eBay 血統）
        "snkrdunk_http": 40,
        "market_assets_store": 30,  # SNK 歷史 bake
    }.get(ch, 0)
    pub = 10 if c.get("publicAllowed") else 0
    sem = str(c.get("semanticMatchStatus") or "")
    sem_rank = 5 if sem in ("source_id_exact", "human_or_vision_confirmed") else 0
    if "rejected" in sem:
        sem_rank = -50
    area = int(c.get("width") or 0) * int(c.get("height") or 0)
    return (pub + channel_rank + sem_rank, area, -int(c.get("assetId") or 0))


def classify_review(unique_cands: list[dict]) -> dict:
    """Decide auto_ok vs needs_human.

    needs_human only when:
      - ≥2 distinct SHAs AND any non-trusted channel, OR
      - ≥2 distinct SHAs AND any rejected / pending conflict with untrusted
    Single SHA (even untrusted) → auto (no double).
    All-trusted multi SHA → auto (SNK/G10 family).
    """
    if not unique_cands:
        return {
            "decision": "no_image",
            "reason": "no_valid_sha",
            "needsHuman": False,
            "autoPick": None,
        }

    trusted = [c for c in unique_cands if (c.get("provenance") or {}).get("trustedIdentity")]
    untrusted = [c for c in unique_cands if not (c.get("provenance") or {}).get("trustedIdentity")]
    n = len(unique_cands)

    if n == 1:
        pick = unique_cands[0]
        return {
            "decision": "auto_single",
            "reason": "single_sha_no_double",
            "needsHuman": False,
            "autoPick": pick,
            "trustedCount": len(trusted),
            "untrustedCount": len(untrusted),
        }

    # multi SHA
    if not untrusted:
        # 全部 SNK / G10 / market-assets → 物認 OK，auto 揀最佳
        pick = max(unique_cands, key=auto_pick_score)
        return {
            "decision": "auto_trusted_family",
            "reason": "all_snk_g10_family_multi_sha",
            "needsHuman": False,
            "autoPick": pick,
            "trustedCount": len(trusted),
            "untrustedCount": 0,
        }

    # 有非 SNK/G10 圖 + 多 SHA → 人類揀 Double / 爭議
    return {
        "decision": "human_double_or_untrusted",
        "reason": "multi_sha_with_non_snk_g10",
        "needsHuman": True,
        "autoPick": None,
        "trustedCount": len(trusted),
        "untrustedCount": len(untrusted),
    }


def main() -> int:
    load_env()
    conn = db()
    cur = conn.cursor()

    # rank map from latest index
    rank_by: dict[int, dict] = {}
    cur.execute(
        """
        SELECT id FROM market_index_snapshot
        WHERE index_code IN ('tcg-combined','tcg')
        ORDER BY id DESC LIMIT 1
        """
    )
    snap = cur.fetchone()
    if snap:
        cur.execute(
            """
            SELECT c.rank_position, c.variant_id, v.opaque_id, v.canonical_name,
                   v.collector_number, v.set_name, v.tcg_code,
                   c.reference_price_usd, c.market_cap_usd, c.psa10_population
            FROM market_index_constituent c
            JOIN catalog_variant v ON v.id=c.variant_id
            WHERE c.index_snapshot_id=%s
            """,
            (snap["id"],),
        )
        for r in cur.fetchall():
            rank_by[int(r["variant_id"])] = dict(r)

    # all variants that have any image
    cur.execute(
        """
        SELECT DISTINCT a.variant_id, v.opaque_id, v.canonical_name, v.collector_number,
               v.set_name, v.tcg_code
        FROM market_image_asset a
        JOIN catalog_variant v ON v.id=a.variant_id
        ORDER BY a.variant_id
        """
    )
    cards: list[dict] = []
    for r in cur.fetchall():
        vid = int(r["variant_id"])
        base = rank_by.get(vid, {})
        cards.append(
            {
                "rank_position": base.get("rank_position") or 99999,
                "variant_id": vid,
                "opaque_id": r["opaque_id"],
                "canonical_name": r["canonical_name"],
                "collector_number": r["collector_number"],
                "set_name": r["set_name"],
                "tcg_code": r["tcg_code"],
                "reference_price_usd": base.get("reference_price_usd"),
                "market_cap_usd": base.get("market_cap_usd"),
                "psa10_population": base.get("psa10_population"),
            }
        )
    cards.sort(key=lambda x: (int(x["rank_position"] or 99999), int(x["variant_id"])))

    vids = [int(r["variant_id"]) for r in cards]
    ph = ",".join(str(v) for v in vids) if vids else "0"

    cur.execute(
        f"""
        SELECT variant_id, source_code, external_entity_id, match_status
        FROM catalog_source_identity
        WHERE variant_id IN ({ph})
        """
    )
    id_by: dict[int, list] = defaultdict(list)
    for r in cur.fetchall():
        id_by[int(r["variant_id"])].append(dict(r))

    cur.execute(
        f"""
        SELECT p.variant_id, p.source_code, p.price_usd, p.effective_at, p.observed_date,
               p.metric_status, p.native_price, p.native_currency
        FROM market_price_observation p
        INNER JOIN (
          SELECT variant_id, source_code, MAX(effective_at) mx
          FROM market_price_observation
          WHERE variant_id IN ({ph})
          GROUP BY variant_id, source_code
        ) t ON t.variant_id=p.variant_id AND t.source_code=p.source_code AND t.mx=p.effective_at
        WHERE p.variant_id IN ({ph})
        """
    )
    px_by: dict[int, list] = defaultdict(list)
    for r in cur.fetchall():
        px_by[int(r["variant_id"])].append(dict(r))

    cur.execute(
        f"""
        SELECT variant_id, source_code, COUNT(*) n,
               MIN(unit_price_usd) mn, MAX(unit_price_usd) mx, AVG(unit_price_usd) av
        FROM market_sale_observation
        WHERE variant_id IN ({ph})
          AND sold_at >= (UTC_DATE() - INTERVAL 30 DAY)
          AND unit_price_usd IS NOT NULL AND unit_price_usd > 0
        GROUP BY variant_id, source_code
        """
    )
    sales_by: dict[int, list] = defaultdict(list)
    for r in cur.fetchall():
        sales_by[int(r["variant_id"])].append(dict(r))

    cur.execute(
        f"""
        SELECT a.id AS asset_id, a.variant_id, a.content_sha256, a.private_object_key,
               a.width_px, a.height_px, a.mime_type,
               q.semantic_match_status, q.public_allowed, q.qc_version, q.checked_at
        FROM market_image_asset a
        LEFT JOIN market_image_qc q ON q.image_asset_id=a.id
        WHERE a.variant_id IN ({ph})
        ORDER BY a.variant_id, a.id
        """
    )
    imgs_by: dict[int, list] = defaultdict(list)
    for r in cur.fetchall():
        imgs_by[int(r["variant_id"])].append(dict(r))

    OUT.mkdir(parents=True, exist_ok=True)
    IMG_OUT.mkdir(parents=True, exist_ok=True)

    worklist: list[dict] = []
    auto_selections: dict[str, dict] = {}
    stats = {
        "scanned": 0,
        "autoSingle": 0,
        "autoTrustedFamily": 0,
        "humanQueue": 0,
        "noImage": 0,
    }
    copied = missing = 0

    for c in cards:
        vid = int(c["variant_id"])
        stats["scanned"] += 1
        identities = id_by.get(vid, [])

        # build all candidates (dedupe later)
        raw_cands: list[dict] = []
        for j, im in enumerate(imgs_by.get(vid, [])):
            letter = chr(ord("A") + j) if j < 26 else str(j)
            # first pass: don't copy yet; classify uses sha/provenance only
            cand, _, _ = build_candidate(im, letter, copy_files=False)
            if cand:
                # fix url without copy for classify; copy only human queue later
                sha = cand["contentSha256"]
                if find_webp(sha):
                    cand["url"] = f"./data/images/{sha}.webp"
                else:
                    cand["url"] = None
                raw_cands.append(cand)

        unique = unique_by_sha(raw_cands)
        # re-key A,B,C by unique order
        for i, u in enumerate(unique):
            u["key"] = chr(ord("A") + i) if i < 26 else str(i)

        decision = classify_review(unique)

        if decision["decision"] == "no_image":
            stats["noImage"] += 1
            continue

        if not decision["needsHuman"]:
            pick = decision["autoPick"]
            if decision["decision"] == "auto_single":
                stats["autoSingle"] += 1
            else:
                stats["autoTrustedFamily"] += 1
            if pick:
                auto_selections[str(vid)] = {
                    "variantId": vid,
                    "opaqueId": c["opaque_id"],
                    "name": c.get("canonical_name"),
                    "choiceKey": pick.get("key"),
                    "contentSha256": pick.get("contentSha256"),
                    "assetId": pick.get("assetId"),
                    "provenance": pick.get("provenance"),
                    "auto": True,
                    "autoReason": decision["reason"],
                    "actor": "auto:snk_g10_policy",
                    "at": datetime.now(timezone.utc).isoformat(),
                }
            continue

        # —— human queue: copy images, attach prices for ID ——
        stats["humanQueue"] += 1
        visible: list[dict] = []
        for u in unique:
            sha = u["contentSha256"]
            src = find_webp(sha)
            if src:
                dest = IMG_OUT / f"{sha}.webp"
                if not dest.is_file():
                    shutil.copy2(src, dest)
                    copied += 1
                u["url"] = f"./data/images/{sha}.webp"
                visible.append(u)
            else:
                missing += 1
                u["url"] = None
        # still show missing-url as placeholder? skip no-url for click
        visible = [x for x in unique if x.get("url")][:8]
        if len(visible) < 2:
            # double 喺 DB 但本機冇圖檔 → 仍放入，靠外鏈認
            visible = unique[:8]

        prices = []
        for p in px_by.get(vid, []):
            src = str(p.get("source_code") or "")
            prices.append(
                {
                    "sourceCode": src,
                    "label": PRICE_SOURCE_LABEL.get(src, src),
                    "priceUsd": money(p.get("price_usd")),
                    "nativePrice": money(p.get("native_price")) if p.get("native_price") is not None else None,
                    "nativeCurrency": p.get("native_currency"),
                    "effectiveAt": str(p.get("effective_at") or ""),
                    "observedDate": str(p.get("observed_date") or ""),
                    "metricStatus": p.get("metric_status"),
                }
            )
        order = {"snk_psa10": 0, "snk": 1, "snkrdunk": 2, "g10_kline": 3, "ebay": 4}
        prices.sort(key=lambda x: order.get(x["sourceCode"], 50))

        sales = []
        for s in sales_by.get(vid, []):
            sales.append(
                {
                    "sourceCode": s["source_code"],
                    "count30d": int(s["n"]),
                    "minUsd": money(s["mn"]),
                    "maxUsd": money(s["mx"]),
                    "avgUsd": money(s["av"]),
                    "note": "ebay 可能 = PC sold (pc:id) 或 G10 UUID",
                }
            )

        display_price = money(c.get("reference_price_usd"))
        display_src = "index_reference"
        if not display_price and prices:
            display_price = prices[0]["priceUsd"]
            display_src = prices[0]["sourceCode"]

        name = c.get("canonical_name") or ""
        tcg = c.get("tcg_code") or ""
        worklist.append(
            {
                "index": len(worklist),
                "variantId": vid,
                "opaqueId": c["opaque_id"],
                "name": name,
                "collectorNumber": c["collector_number"],
                "setName": c["set_name"],
                "tcg": tcg,
                "rankPosition": c.get("rank_position"),
                "marketCapUsd": money(c.get("market_cap_usd")),
                "psa10Population": c.get("psa10_population"),
                "displayPriceUsd": display_price,
                "displayPriceSource": display_src,
                "prices": prices,
                "sales30d": sales,
                "links": build_links(identities, name, tcg),
                "identities": [
                    {
                        "sourceCode": x["source_code"],
                        "externalEntityId": x["external_entity_id"],
                        "matchStatus": x["match_status"],
                    }
                    for x in identities
                ],
                "reviewReason": decision["reason"],
                "trustedCount": decision.get("trustedCount"),
                "untrustedCount": decision.get("untrustedCount"),
                "candidateCount": len(unique),
                "visibleCount": len([x for x in unique if x.get("url")]),
                "candidates": visible,
            }
        )

    meta = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "policy": "snk_g10_auto_ok_human_only_double_untrusted",
        "policyNote": (
            "SNK + G10（含 market-assets SNK 血統）圖/價預設物認 OK，auto-pick。"
            "只入 queue：≥2 distinct SHA 且含非 SNK/G10 來源（Double／爭議）。"
        ),
        "nCardsHuman": len(worklist),
        "nAuto": len(auto_selections),
        "stats": stats,
        "withAnyCandidate": sum(1 for w in worklist if w["visibleCount"] > 0),
        "multiCandidate": sum(1 for w in worklist if w["candidateCount"] > 1),
        "withPrices": sum(1 for w in worklist if w["prices"]),
        "copiedImages": copied,
        "missingOnDisk": missing,
        "priceNote": (
            "口價只供爭議卡認卡用。SNK/G10 已 auto OK 唔使你覆核。"
            "ebay 標籤 = G10 存量 median 或 PC sold。"
        ),
    }

    (OUT / "worklist.json").write_text(
        json.dumps({"meta": meta, "cards": worklist}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    auto_doc = {
        "schemaVersion": 1,
        "updatedAt": meta["generatedAt"],
        "updatedBy": "auto:snk_g10_policy",
        "policy": meta["policy"],
        "selections": auto_selections,
    }
    (OUT / "auto_selections.json").write_text(
        json.dumps(auto_doc, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if not (OUT / "selections.seed.json").exists():
        (OUT / "selections.seed.json").write_text(
            json.dumps(
                {"schemaVersion": 1, "updatedAt": None, "updatedBy": None, "selections": {}},
                indent=2,
            ),
            encoding="utf-8",
        )
    print(json.dumps(meta, indent=2, ensure_ascii=False))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
