#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""補完 2026-08-12 incomplete-printing-identity run 留低嘅 parallel 半步。

嗰次 run（commit ee541e24，scope=incomplete-printing-identity）為咗唔踢走
已 exact 嘅 SNK/PC binding，特登唔將 blank parallel_code 補做 PSA label——
但照接受咗 10 條 psa_parallel='Base' 嘅 literal acceptance。結果呢 10 個
variant 而家先出現喺 operator_psa_identity_projection，validator034 嘅
parallelAmbiguityZero（PSA 有講 parallel 但 printing 冇記）即刻紅，
E2E validate stage fail，聽朝 daily chain 會 ABORT 喺呢級。

正確末態唔係放鬆 validator（AGENTS.md 規矩 10），係照 88 個先例補完：
  psa_parallel='Base' & parallel_code='Base' 嘅 88 個變體，佢哋嘅
  snkrdunk binding bound_parallel_code='Base'（77 個）、pricecharting
  'base'（11 個），全部因此留喺 operator_strict_source_identity。
  即係：printing 補 parallel 嗰陣，同一單 transaction 要將 exact binding
  嘅 bound_parallel_code 一齊蓋印，先唔會斷 strict 路。

呢個腳本對嗰 10 個 variant（精確名單，fail-closed 唔掃全表）做：
  1. catalog_printing_identity.parallel_code '' -> 'Base'，
     canonical_printing_sha256 用同一份 printing_sha() 重算
     （import 自 resolve_active_psa_identity，一個概念一份實現），
     行前檢查無 UNIQUE 碰撞。
  2. catalog_source_identity（該 variant 全部 match_status='exact' 而
     bound_parallel_code 空白嘅 row，含 gemrate）bound_parallel_code -> 'Base'。
  3. operator_binding_freeze freeze_kind='identity' 嘅 content_sha256
     跟 printing 換新 sha（new_era_db_tidy 有 f.content_sha256=
     p.canonical_printing_sha256 對數）。
  4. strict view before/after 全等護欄：任何 binding 消失即 rollback。
  5. receipt 落 data/runtime/rebuild-036/base-parallel-completion-20260813.json。

catalog_psa_identity_acceptance 唔掂：acceptance 係 append-only 收據，
記錄接受嗰刻嘅 sha；validator034 對數用嘅係 printing 行自己重算。

用法：
  python -X utf8 pipelines/complete_base_parallel_20260813.py --dry-run
  python -X utf8 pipelines/complete_base_parallel_20260813.py --write
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from rebuild_036 import connect, DAILY_CREDENTIALS_ENV  # noqa: E402
from resolve_active_psa_identity import printing_sha  # noqa: E402

# 2026-08-12 receipt（temp/psa-identity-incomplete10-20260812.json）嘅 10 個
# variant，同 validator034 active.parallelFailures 名單逐一核對過。
VARIANT_IDS = [221, 231, 238, 242, 272, 330, 347, 1637, 1751, 1814]
TARGET_PARALLEL = "Base"

RECEIPT = ROOT / "data" / "runtime" / "rebuild-036" / "base-parallel-completion-20260813.json"

SHA_FIELDS = (
    "tcg_code", "card_language", "set_name", "set_code", "collector_number",
    "printing_code", "rarity_code", "edition_code", "parallel_code", "finish_code",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args()

    conn = connect(DAILY_CREDENTIALS_ENV)
    cur = conn.cursor()
    ph = ",".join(["%s"] * len(VARIANT_IDS))

    # --- 讀現狀 + 前置檢查 --------------------------------------------------
    cur.execute(
        f"""SELECT p.variant_id, p.canonical_printing_sha256, a.psa_parallel,
                   {",".join("p." + f for f in SHA_FIELDS)}
            FROM catalog_printing_identity p
            INNER JOIN operator_psa_identity_projection a ON a.variant_id=p.variant_id
            WHERE p.variant_id IN ({ph}) ORDER BY p.variant_id""",
        VARIANT_IDS,
    )
    rows = cur.fetchall()
    if len(rows) != len(VARIANT_IDS):
        raise SystemExit(f"expected {len(VARIANT_IDS)} rows, got {len(rows)}")

    plan = []
    for r in rows:
        vid = int(r["variant_id"])
        if str(r["psa_parallel"]) != TARGET_PARALLEL:
            raise SystemExit(f"v{vid}: psa_parallel={r['psa_parallel']!r}, refusing (only 'Base' in scope)")
        if str(r["parallel_code"] or "") != "":
            raise SystemExit(f"v{vid}: parallel_code already {r['parallel_code']!r}, refusing")
        before_fields = {f: str(r[f] or "") for f in SHA_FIELDS}
        old_sha = str(r["canonical_printing_sha256"])
        if printing_sha(before_fields) != old_sha:
            raise SystemExit(f"v{vid}: stored sha does not recompute from live row, refusing")
        after_fields = dict(before_fields, parallel_code=TARGET_PARALLEL)
        new_sha = printing_sha(after_fields)
        cur.execute(
            "SELECT variant_id FROM catalog_printing_identity"
            " WHERE canonical_printing_sha256=%s AND variant_id<>%s LIMIT 1",
            (new_sha, vid),
        )
        clash = cur.fetchone()
        if clash:
            raise SystemExit(f"v{vid}: new sha collides with variant {clash['variant_id']}")
        plan.append({"variantId": vid, "oldSha": old_sha, "newSha": new_sha})

    # --- strict baseline -----------------------------------------------------
    cur.execute(
        f"""SELECT variant_id, source_code, external_entity_id
            FROM operator_strict_source_identity WHERE variant_id IN ({ph})""",
        VARIANT_IDS,
    )
    strict_before = {
        (int(r["variant_id"]), str(r["source_code"]), str(r["external_entity_id"]))
        for r in cur.fetchall()
    }

    print(f"plan: {len(plan)} printing rows, strict baseline {len(strict_before)} bindings")
    if args.dry_run:
        for p in plan:
            print(f"  v{p['variantId']}: {p['oldSha'][:12]} -> {p['newSha'][:12]}")
        conn.close()
        return 0

    # --- write（單 transaction + lock） ---------------------------------------
    counts = {"printing": 0, "bindings": 0, "freezes": 0}
    try:
        cur.execute("SELECT GET_LOCK('cardz-market-cap:active-psa-resolution-035',0) AS a")
        if int((cur.fetchone() or {}).get("a") or 0) != 1:
            raise RuntimeError("active PSA resolution lock unavailable")
        for p in plan:
            vid = p["variantId"]
            cur.execute(
                """UPDATE catalog_printing_identity
                   SET parallel_code=%s, canonical_printing_sha256=%s
                   WHERE variant_id=%s AND parallel_code='' AND canonical_printing_sha256=%s""",
                (TARGET_PARALLEL, p["newSha"], vid, p["oldSha"]),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"v{vid}: printing precondition failed")
            counts["printing"] += 1
            cur.execute(
                """UPDATE catalog_source_identity
                   SET bound_parallel_code=%s
                   WHERE variant_id=%s AND match_status='exact'
                     AND LOWER(TRIM(COALESCE(bound_parallel_code,'')))=''""",
                (TARGET_PARALLEL, vid),
            )
            counts["bindings"] += cur.rowcount
            cur.execute(
                """UPDATE operator_binding_freeze
                   SET content_sha256=%s
                   WHERE variant_id=%s AND freeze_kind='identity' AND source_code=''
                     AND content_sha256=%s""",
                (p["newSha"], vid, p["oldSha"]),
            )
            counts["freezes"] += cur.rowcount

        cur.execute(
            f"""SELECT variant_id, source_code, external_entity_id
                FROM operator_strict_source_identity WHERE variant_id IN ({ph})""",
            VARIANT_IDS,
        )
        strict_after = {
            (int(r["variant_id"]), str(r["source_code"]), str(r["external_entity_id"]))
            for r in cur.fetchall()
        }
        lost = sorted(strict_before - strict_after)
        if lost:
            raise RuntimeError(f"strict bindings would disconnect: {lost}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            cur.execute("SELECT RELEASE_LOCK('cardz-market-cap:active-psa-resolution-035')")
        except Exception:
            pass

    receipt = {
        "completedAt": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "why": "validator034 parallelAmbiguityZero: 10 Base literals accepted 2026-08-12"
               " with blank printing parallel_code (ee541e24 deliberate half-step)",
        "convention": "88 precedent variants: psa_parallel=Base -> parallel_code=Base,"
                      " exact bindings stamped bound_parallel_code=Base to stay strict",
        "counts": counts,
        "rows": plan,
        "strictBefore": len(strict_before),
        "strictAfter": len(strict_after),
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(counts))
    print(f"receipt: {RECEIPT}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
