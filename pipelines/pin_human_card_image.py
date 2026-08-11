#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""人類指定卡圖（特例）—— 由本機一個檔案直接釘死一張卡嘅出街圖。

點解要有呢個檔：collector 揀圖只認來源（SNK EN storefront default），來源
畀咩就係咩。但有啲卡喺 storefront 得一張**連膠套**嘅相（例：v8 Monkey.D.Luffy
[Dodgers] EB02-010），來源本身冇去套版，collector 永遠都揀唔到啱。呢種情況
唯一權威係人，而人手改 DB 每次都要重新砌一次成條 lineage → 一定出錯。

寫入鏈同 collector 完全一樣，唔開後門：
  market_image_asset → market_image_qc → market_canonical_image_acceptance
  （fallback 分支，storefront_lineage_id=NULL）→ operator_binding_freeze
  （自己一個 source_code，唔會同 snkrdunk_en 撞 PK）

兩重鎖住舊圖，唔靠記性：
  1. 新 acceptance 個 supersedes_acceptance_id 指住舊嗰個 —— FE 兩條 query
     同 operator_canonical_image_projection 都有 `NOT EXISTS (newer ...)`，
     所以舊 acceptance 由呢刻起永遠出唔到街，就算 collector 再 accept 佢。
  2. 舊 content sha 入 market_image_rejection_registry —— rebuild_036 image-bind
     同 collect_control 個 SNK EN freeze 都會避開佢。

Run:
  python -X utf8 pipelines/pin_human_card_image.py --variant-id 8 \
      --image path/to.png --reason "storefront 得膠套版" --reject-sha <舊sha>
  加 --write 先真係落 DB；預設 dry-run。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import g10_public_snapshot as g10  # noqa: E402
import native_image_resolver as native  # noqa: E402
from rebuild_036 import DAILY_CREDENTIALS_ENV, connect  # noqa: E402

FREEZE_SOURCE_CODE = "human"
QC_VERSION = "human-pin-v1"
CONTRACT = "human-designated-card-image-v1"


def _sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _canonical_json(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _render(source: bytes) -> tuple[bytes, dict[str, bytes], int, int]:
    """同 collector 一模一樣嘅畫布規格，唔係另一套。"""
    image = Image.open(io.BytesIO(source))
    image.load()
    canvas = native.normalize_card_canvas(image)
    if not native.has_rounded_corners(canvas):
        canvas = native.apply_rounded_corners(canvas)
    master = g10._save_webp(canvas, 92)
    return master, g10.encode_derivatives(canvas), canvas.width, canvas.height


def _current_acceptance_id(cur, variant_id: int) -> int | None:
    cur.execute(
        """
        SELECT canonical_image_acceptance_id AS id
        FROM operator_binding_freeze
        WHERE variant_id=%s AND freeze_kind='image' AND acceptance_status='accepted'
          AND canonical_image_acceptance_id IS NOT NULL
        ORDER BY accepted_at DESC, source_code
        LIMIT 1
        """,
        (variant_id,),
    )
    row = cur.fetchone()
    return int(row["id"]) if row else None


def _published_shas(cur, variant_id: int) -> list[str]:
    """FE 真正會攞到嘅 sha —— view 同 fallback query 兩條都行一次。"""
    cur.execute(
        "SELECT canonical_image_content_sha256 AS sha"
        " FROM operator_canonical_image_projection WHERE variant_id=%s",
        (variant_id,),
    )
    shas = [str(row["sha"]) for row in cur.fetchall()]
    if shas:
        return shas
    cur.execute(
        """
        SELECT a.content_sha256 AS sha
        FROM operator_binding_freeze f
        INNER JOIN market_canonical_image_acceptance ca
          ON ca.id=f.canonical_image_acceptance_id AND ca.variant_id=f.variant_id
        INNER JOIN market_image_asset a
          ON a.id=ca.image_asset_id AND a.variant_id=ca.variant_id
         AND a.image_kind='raw_front' AND a.content_sha256=f.content_sha256
        INNER JOIN market_image_qc q
          ON q.image_asset_id=a.id
         AND q.id=(SELECT q2.id FROM market_image_qc q2 WHERE q2.image_asset_id=a.id
                   ORDER BY q2.checked_at DESC,q2.id DESC LIMIT 1)
         AND q.public_allowed=1 AND q.raw_front_confirmed=1 AND q.card_number_match=1
         AND q.language_match=1 AND q.tcg_match=1
         AND q.semantic_match_status IN ('human_or_vision_confirmed','accepted_freeze')
        WHERE f.variant_id=%s AND f.freeze_kind='image' AND f.acceptance_status='accepted'
          AND f.canonical_image_acceptance_id IS NOT NULL
          AND f.content_sha256 REGEXP '^[0-9a-f]{64}$'
          AND NOT EXISTS (SELECT 1 FROM market_canonical_image_acceptance newer
                          WHERE newer.supersedes_acceptance_id=ca.id)
        """,
        (variant_id,),
    )
    return [str(row["sha"]) for row in cur.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(description="人類指定卡圖（特例）")
    parser.add_argument("--variant-id", type=int, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--reason", required=True, help="點解要人手指定，會寫入 note")
    parser.add_argument("--reject-sha", action="append", default=[],
                        help="舊 content sha，入 rejection registry；可重複")
    parser.add_argument("--actor", default="human")
    parser.add_argument("--credentials-env", type=Path, default=DAILY_CREDENTIALS_ENV)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    source = args.image.read_bytes()
    source_sha = _sha256(source)
    master, derivatives, width, height = _render(source)
    content_sha = _sha256(master)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    run_id = f"human-pin-{now.strftime('%Y%m%dT%H%M%SZ')}"

    evidence = {
        "contract": CONTRACT,
        "variantId": args.variant_id,
        "sourceFile": args.image.name,
        "sourceSha256": source_sha,
        "contentSha256": content_sha,
        "canvas": [native.CANVAS_W, native.CANVAS_H],
        "fill": native.CANVAS_FILL,
        "actor": args.actor,
        "reason": args.reason,
        "reviewRunId": run_id,
    }
    evidence_sha = _sha256(_canonical_json(evidence))
    object_key = f"data/public/market-assets/{content_sha}.webp"
    note = f"人類指定．特例：{args.reason}"

    print(json.dumps({
        "sourceSha256": source_sha, "contentSha256": content_sha,
        "size": [width, height], "masterBytes": len(master),
        "evidenceSha256": evidence_sha, "reviewRunId": run_id,
        "rejectSha": args.reject_sha, "write": args.write,
    }, ensure_ascii=False, indent=2))

    conn = connect(args.credentials_env)
    try:
        with conn.cursor() as cur:
            supersedes = _current_acceptance_id(cur, args.variant_id)
            print(f"supersedes acceptance: {supersedes}")
            if not args.write:
                print("dry-run：冇寫任何嘢。加 --write 先落 DB。")
                return 0

            native.ASSETS.mkdir(parents=True, exist_ok=True)
            (native.ASSETS / f"{content_sha}.webp").write_bytes(master)
            for suffix, blob in derivatives.items():
                (native.ASSETS / f"{content_sha}_{suffix}.webp").write_bytes(blob)

            cur.execute(
                """
                INSERT INTO market_image_asset
                  (variant_id,image_kind,content_sha256,private_object_key,mime_type,
                   width_px,height_px,source_version_sha256,captured_at)
                VALUES (%s,'raw_front',%s,%s,'image/webp',%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)
                """,
                (args.variant_id, content_sha, object_key, width, height, source_sha, now),
            )
            cur.execute("SELECT LAST_INSERT_ID() AS id")
            asset_id = int(cur.fetchone()["id"])

            cur.execute(
                """
                INSERT INTO market_image_qc
                  (image_asset_id,semantic_match_status,card_number_match,language_match,
                   tcg_match,raw_front_confirmed,public_allowed,checked_at,qc_version)
                VALUES (%s,'human_or_vision_confirmed',1,1,1,1,1,%s,%s)
                ON DUPLICATE KEY UPDATE checked_at=VALUES(checked_at)
                """,
                (asset_id, now, QC_VERSION),
            )

            cur.execute(
                """
                INSERT INTO market_canonical_image_acceptance
                  (variant_id,storefront_lineage_id,image_asset_id,fallback_source_path,
                   fallback_source_version_sha256,fallback_source_observed_at,
                   lineage_sha256,evidence_sha256,accepted_by,accepted_at,
                   supersedes_acceptance_id)
                VALUES (%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    args.variant_id, asset_id,
                    f"human-designated:{run_id}:{args.image.name}",
                    source_sha, now, source_sha, evidence_sha,
                    args.actor, now, supersedes,
                ),
            )
            acceptance_id = int(cur.lastrowid)

            # 同一張卡唔可以有兩個 accepted image freeze —— FE 個 Map 係後蓋前，
            # 兩行就變咗睇邊行後讀到，等於冇釘死。
            cur.execute(
                """
                UPDATE operator_binding_freeze
                SET acceptance_status='rejected', actor=%s, note=%s
                WHERE variant_id=%s AND freeze_kind='image'
                  AND acceptance_status='accepted' AND source_code<>%s
                """,
                (f"pin_human_card_image:{run_id}", note, args.variant_id, FREEZE_SOURCE_CODE),
            )
            demoted = cur.rowcount

            cur.execute(
                """
                INSERT INTO operator_binding_freeze
                  (variant_id,freeze_kind,source_code,external_entity_id,content_sha256,
                   canonical_image_acceptance_id,accepted_lineage_sha256,acceptance_status,
                   actor,evidence_sha256,note,accepted_at)
                VALUES (%s,'image',%s,%s,%s,%s,%s,'accepted',%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  external_entity_id=VALUES(external_entity_id),
                  content_sha256=VALUES(content_sha256),
                  canonical_image_acceptance_id=VALUES(canonical_image_acceptance_id),
                  accepted_lineage_sha256=VALUES(accepted_lineage_sha256),
                  acceptance_status='accepted',actor=VALUES(actor),
                  evidence_sha256=VALUES(evidence_sha256),note=VALUES(note),
                  accepted_at=VALUES(accepted_at)
                """,
                (
                    args.variant_id, FREEZE_SOURCE_CODE, run_id, content_sha,
                    acceptance_id, source_sha, args.actor, evidence_sha, note, now,
                ),
            )

            for sha in args.reject_sha:
                cur.execute(
                    "SELECT id FROM market_image_asset"
                    " WHERE variant_id=%s AND content_sha256=%s LIMIT 1",
                    (args.variant_id, sha),
                )
                row = cur.fetchone()
                decision = _canonical_json({**evidence, "rejectedSha256": sha})
                cur.execute(
                    """
                    INSERT INTO market_image_rejection_registry
                      (variant_id,content_sha256,first_image_asset_id,rejection_reason,
                       decision_code_sha256,review_run_id,rejected_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE rejection_reason=VALUES(rejection_reason),
                      decision_code_sha256=VALUES(decision_code_sha256),
                      review_run_id=VALUES(review_run_id),rejected_at=VALUES(rejected_at)
                    """,
                    (
                        args.variant_id, sha, int(row["id"]) if row else None,
                        f"human_designated_override:{args.reason}"[:500],
                        _sha256(decision), run_id, now,
                    ),
                )

            published = _published_shas(cur, args.variant_id)
            if published != [content_sha]:
                conn.rollback()
                raise SystemExit(
                    f"ABORT: 釘完之後 FE 攞到 {published}，唔係 [{content_sha}]；已 rollback"
                )
        conn.commit()
        print(json.dumps({
            "assetId": asset_id, "acceptanceId": acceptance_id,
            "demotedFreezeRows": demoted, "publishedSha256": published[0],
        }, ensure_ascii=False, indent=2))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
