#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""人手裁決 exact binding：SNK item 91412 = v1874 普通版 Battle Festa 175/XY-P。

背景（同一晚兩單裁決嘅下半場）：
  - 413361（イベントオーガナイザー版，¥6.3M）錯綁 v1874 已裁決 reject
    （reject_snk_organizer_binding_20260813.py，跨家族矛盾 gate 8.57x 捉到）。
  - v1874 係 ja 卡，D4 語言路由之下 PC 價唔算數，冇 SNK binding 就冇價源，
    S12 product_ready gap 卡死成條夜鏈。
  - snk_identity_discover（selector 修正之後）搵到 91412，但 product_agrees
    要求 item 名寫出 parallel 字眼「battle festa」—— SNK 對普通版唔會寫活動名
    （特別版先會寫），所以自動 lane 冇得證。呢張卡要人手裁決，唔郁個 gate。

人手證據（2026-08-13，operator 核實 SNK API master）：
  - SNK 上面認「XY-P 175」嘅 item 淨係三個：
      413361  名寫「Battle Festa 2015 … Event Organizer Version」 → 已裁決拒
      210317  usedMinPrice=0 嘅死頁，rule_candidate 會出 no_psa10_1card_variant
      91412   「Pikachu: PROMO[XY-P 175/XY-P](XY-P Promotional cards)」
              usedMinPrice ¥154,000，有 PSA10 1-card variant
  - XY-P 175 呢個號喺 Pokemon 官方 numbering 入面就係 2015 Battle Festa 場販
    Pikachu（2014 孖生係 090/XY-P，SNK item 91407，號都唔同）。
  - 91412 價位（used ¥154k）同 PC/eBay 普通版 PSA10 $3-5k 同級；
    組織者版 ¥6.3M 係另一個市場。

用法：
  python -X utf8 pipelines/bind_snk_regular_battlefesta_20260813.py --dry-run
  python -X utf8 pipelines/bind_snk_regular_battlefesta_20260813.py --write
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from rebuild_036 import (  # noqa: E402
    connect, DAILY_CREDENTIALS_ENV, canonical_json, sha256_bytes, sha256_file,
)

ITEM = "91412"
VARIANT = 1874
GENERATION = "036_20260808T084217Z"
ITEMS_DIR = ROOT / "data" / "private" / "snk" / f"rebuild-{GENERATION}" / "items"
RECEIPT = ROOT / "data" / "runtime" / "operator" / "audit" / "snk_regular_battlefesta_bind_20260813.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args()

    from snkrdunk_bulk import SnkrdunkApi  # noqa: E402
    from snk_market_data import pull_market_data  # noqa: E402

    api = SnkrdunkApi(delay=1.5)
    payload = pull_market_data(api, int(ITEM))
    if payload.get("error"):
        raise SystemExit(f"SNK page fetch failed: {payload['error']}")
    master = (payload.get("source_payload") or {}).get("master") or {}
    name = str(master.get("name") or "")
    if "175/XY-P" not in name and "XY-P 175" not in name:
        raise SystemExit(f"item {ITEM} no longer claims XY-P 175: {name!r}")
    if "organizer" in name.lower() or "オーガナイザー" in name:
        raise SystemExit(f"item {ITEM} reads as the organizer version, refusing: {name!r}")
    if not payload.get("quantity_variant_id"):
        raise SystemExit(f"item {ITEM} has no PSA10 1-card variant; cannot be a price source")

    conn = connect(DAILY_CREDENTIALS_ENV)
    cur = conn.cursor()

    cur.execute(
        "SELECT variant_id, match_status FROM catalog_source_identity"
        " WHERE source_code='snkrdunk' AND external_entity_id=%s",
        (ITEM,),
    )
    existing = cur.fetchall()
    # idempotent re-run：行可以已存在，但只可以係我哋今次裁決落嘅嗰行
    if existing and not (
        len(existing) == 1
        and int(existing[0]["variant_id"]) == VARIANT
        and existing[0]["match_status"] == "exact"
    ):
        raise SystemExit(f"item {ITEM} bound elsewhere, refusing: {existing}")
    cur.execute(
        """SELECT external_entity_id FROM catalog_source_identity
           WHERE source_code='snkrdunk' AND variant_id=%s AND match_status='exact'
             AND external_entity_id<>%s""",
        (VARIANT, ITEM),
    )
    others = cur.fetchall()
    if others:
        raise SystemExit(f"v{VARIANT} already has another exact snkrdunk binding, refusing: {others}")

    cur.execute(
        """SELECT v.tcg_code, p.card_language, p.collector_number, p.parallel_code,
                  p.printing_code, p.set_code, p.edition_code, p.finish_code
           FROM catalog_variant v
           INNER JOIN catalog_printing_identity p ON p.variant_id = v.id
           WHERE v.id=%s""",
        (VARIANT,),
    )
    identity = cur.fetchone()

    # evidence 檔 + capture receipt：一個 byte 都跟 S7 stage_snk_refresh 嘅
    # 協議（evidence_doc 形狀、canonical_json bytes、items 目錄、receipt 欄），
    # 因為 strict view 嘅 non-gemrate 分支要 (a) $.evidence.path 非空
    # (b) capture receipt 行 capture_sha256 == $.evidence.sha256。
    capture_doc = {
        "itemId": int(ITEM),
        "master": master,
        "conditionFilter": payload.get("condition_filter"),
        "quantityVariantId": payload.get("quantity_variant_id"),
        "productNumber": payload.get("product_number"),
        "imageUrl": payload.get("image_url"),
        "fetchedAt": payload.get("fetched_at"),
    }
    blob = canonical_json(capture_doc)
    ITEMS_DIR.mkdir(parents=True, exist_ok=True)
    item_path = ITEMS_DIR / f"{ITEM}.json"
    item_path.write_bytes(blob)
    digest = sha256_bytes(blob)
    rel_path = item_path.relative_to(ROOT).as_posix()
    parser_version = "snkmd_" + sha256_file(ROOT / "pipelines" / "snk_market_data.py")[:12]

    evidence_doc = {
        "evidence": {
            "type": "provider_native_product_page",
            "path": rel_path,
            "sha256": digest,
            "canonicalUrl": f"https://snkrdunk.com/en/trading-cards/{ITEM}",
            "capturedAt": str(payload.get("fetched_at") or ""),
            "generation": GENERATION,
        },
        "providerClaims": {
            "tcgCode": "",
            "setCode": "",
            "cardLanguage": "ja",
            "collectorNumber": "XY-P 175",
            "parallelCode": "",
            "printingCode": "",
        },
        "action": "operator-adjudicated-bind",
        "adjudicatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "adjudicatedBy": "operator-adjudication-20260813",
        "reason": (
            "Only live SNK item claiming XY-P 175 with a PSA10 1-card variant."
            " 413361 (Event Organizer Version, JPY6.3M) rejected by adjudication;"
            " 210317 is a dead listing (usedMinPrice 0, no PSA10 variant);"
            " 91407 is the 2014 twin 090/XY-P. XY-P numbering pins 175 to the"
            " 2015 Battle Festa Pikachu; SNK never words the event on regular"
            " listings so product_agrees cannot see it (organizer-version shape)."
        ),
    }

    plan = {
        "item": ITEM,
        "variant": VARIANT,
        "masterName": name,
        "usedMinPrice": master.get("usedMinPrice"),
        "quantityVariantId": payload.get("quantity_variant_id"),
        "captureSha256": digest,
        "capturePath": rel_path,
        "boundFields": {
            "tcg": identity["tcg_code"],
            "language": identity["card_language"],
            "collector": identity["collector_number"],
            "parallel": identity["parallel_code"],
        },
    }
    print(json.dumps(plan, ensure_ascii=False, indent=1))
    if args.dry_run:
        conn.close()
        return 0

    fetched = str(payload.get("fetched_at") or "")
    captured_at = (
        datetime.strptime(fetched, "%Y-%m-%dT%H:%M:%S%z") if fetched
        else datetime.now(timezone.utc)
    )
    cur.execute(
        """INSERT INTO catalog_provider_capture_receipt
             (source_code, external_entity_id, capture_sha256, capture_path,
              captured_at, generation_id, parser_version)
           VALUES ('snkrdunk', %s, %s, %s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE capture_sha256=VALUES(capture_sha256),
             capture_path=VALUES(capture_path), captured_at=VALUES(captured_at),
             generation_id=VALUES(generation_id), parser_version=VALUES(parser_version)""",
        (ITEM, digest, rel_path[:500], captured_at, GENERATION, parser_version[:64]),
    )
    cur.execute(
        """INSERT INTO catalog_source_identity
             (source_code, external_entity_id, variant_id, match_status, evidence_sha256,
              source_product_number, bind_evidence_json, bound_tcg_code, bound_card_language,
              bound_collector_number, bound_set_code, bound_printing_code, bound_parallel_code,
              bound_edition_code, bound_finish_code)
           VALUES ('snkrdunk', %s, %s, 'exact', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE variant_id=VALUES(variant_id), match_status='exact',
             evidence_sha256=VALUES(evidence_sha256),
             source_product_number=VALUES(source_product_number),
             bind_evidence_json=VALUES(bind_evidence_json),
             bound_tcg_code=VALUES(bound_tcg_code),
             bound_card_language=VALUES(bound_card_language),
             bound_collector_number=VALUES(bound_collector_number),
             bound_set_code=VALUES(bound_set_code),
             bound_printing_code=VALUES(bound_printing_code),
             bound_parallel_code=VALUES(bound_parallel_code),
             bound_edition_code=VALUES(bound_edition_code),
             bound_finish_code=VALUES(bound_finish_code)""",
        (
            ITEM, VARIANT, digest, "XY-P 175",
            json.dumps(evidence_doc, ensure_ascii=False),
            identity["tcg_code"], identity["card_language"], identity["collector_number"],
            identity["set_code"] or "", identity["printing_code"] or "",
            identity["parallel_code"] or "", identity["edition_code"] or "",
            identity["finish_code"] or "",
        ),
    )
    conn.commit()

    cur.execute(
        "SELECT COUNT(*) n FROM operator_strict_source_identity"
        " WHERE source_code='snkrdunk' AND external_entity_id=%s AND variant_id=%s",
        (ITEM, VARIANT),
    )
    strict_after = int(cur.fetchone()["n"])
    if strict_after != 1:
        raise SystemExit(
            f"binding landed but strict view does not admit it (rows={strict_after});"
            " bound_* fields must mirror catalog_printing_identity -- investigate"
        )

    receipt = {**plan, "completedAt": evidence_doc["adjudicatedAt"], "strictAfter": strict_after,
               "reason": evidence_doc["reason"]}
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"bound": True, "strictAfter": strict_after}, ensure_ascii=False))
    print(f"receipt: {RECEIPT}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
