#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""俾任何 opaque_id 跌咗出 cmc_ 命名空間嘅卡鑄一個乾淨嘅公開 id。

背景：opaque_id 就係公開 URL（`/card/<id>`）同 sitemap entry。2026-08-11 查 DB
實測有 4 行帶住供應商前綴，3 行已經出咗街。opaque_id 由 D7 凍結、而且被兩個
stored hash 當咗前像，所以唔 rename —— 改為喺 public_card_alias 鑄一個公開 id，
投影層出 alias，內部一切照舊用 opaque_id（migration 041 有全套理由）。

公開 id 用返 g10_public_snapshot.opaque_id() 同一條規則計，input 係呢一行自己嘅
canonical identity。即係話：呢個 id 係「當初照規矩鑄嘅話會係咩」，而唔係求其一個
新 sha —— 同一行資料重跑一百次都係同一個 id。

    python -X utf8 pipelines/public_card_alias.py --dry-run
    python -X utf8 pipelines/public_card_alias.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from g10_public_snapshot import normalize_collector, opaque_id  # noqa: E402

REASON = "opaque_id carried a provider prefix into the public URL (frozen by D7, aliased instead)"

# 前端出 308 嘅嗰張表。一鑄 alias，舊 URL 即刻冇卡對得上（硬 404），所以呢兩樣
# 嘢一定要同步。呢度 fail-closed：鑄咗但冇寫落去就當今次冇做完。
REDIRECT_MAP = ROOT / "apps" / "web" / "next.config.ts"


def missing_redirects(aliases: list[dict]) -> list[str]:
    source = REDIRECT_MAP.read_text(encoding="utf-8")
    gaps: list[str] = []
    for entry in aliases:
        old, new = entry["opaqueId"], entry["publicId"]
        # 個檔係 object literal，key 唔使引號，所以搵「舊 id ... 新 id」同一行。
        if not any(old in line and new in line for line in source.splitlines()):
            gaps.append(f"{old} -> {new}")
    return gaps


def load_backend_env() -> None:
    path = ROOT / "data" / "runtime" / "config" / "backend.env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip().startswith("CARDZ_DB_"):
            os.environ.setdefault(key.strip(), value.strip())


def mint(row: dict) -> str:
    language = str(row["card_language"] or "")
    collector = normalize_collector(row["collector_number"], language, row["set_name"])
    return opaque_id(
        str(row["tcg_code"]),
        language,
        str(row["set_name"]),
        collector,
        str(row["canonical_name"]),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真係寫入；唔加就淨係報")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    load_backend_env()
    connection = pymysql.connect(
        host=args.host or os.environ.get("CARDZ_DB_HOST", "127.0.0.1"),
        port=args.port or int(os.environ.get("CARDZ_DB_PORT", "3308")),
        user=os.environ["CARDZ_DB_USER"],
        password=os.environ["CARDZ_DB_PASSWORD"],
        database=os.environ["CARDZ_DB_NAME"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    minted: list[dict] = []
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                r"""
                SELECT v.id, v.opaque_id, v.tcg_code, v.card_language, v.set_name,
                       v.collector_number, v.canonical_name, a.public_id AS existing_alias
                FROM catalog_variant v
                LEFT JOIN public_card_alias a ON a.variant_id = v.id
                WHERE v.opaque_id NOT LIKE 'cmc\_%'
                ORDER BY v.id
                """
            )
            rows = cursor.fetchall()
            for row in rows:
                public_id = mint(row)
                # 撞名 = 兩張卡爭同一條公開 URL。寧願停，唔好靜靜覆蓋。
                cursor.execute(
                    "SELECT id FROM catalog_variant WHERE opaque_id = %s AND id <> %s",
                    (public_id, row["id"]),
                )
                clash = cursor.fetchone()
                if clash:
                    raise RuntimeError(
                        f"minted public id {public_id} already belongs to variant {clash['id']}"
                    )
                minted.append({
                    "variantId": int(row["id"]),
                    "opaqueId": str(row["opaque_id"]),
                    "publicId": public_id,
                    "existingAlias": row["existing_alias"],
                    "action": "keep" if row["existing_alias"] == public_id else (
                        "replace" if row["existing_alias"] else "insert"
                    ),
                })
            if args.apply:
                for entry in minted:
                    if entry["action"] == "keep":
                        continue
                    cursor.execute(
                        """
                        INSERT INTO public_card_alias (variant_id, public_id, reason)
                        VALUES (%s, %s, %s)
                        ON DUPLICATE KEY UPDATE public_id = VALUES(public_id), reason = VALUES(reason)
                        """,
                        (entry["variantId"], entry["publicId"], REASON),
                    )
                connection.commit()
    finally:
        connection.close()

    gaps = missing_redirects(minted)
    print(json.dumps(
        {"applied": args.apply, "aliases": minted, "missingRedirects": gaps},
        ensure_ascii=False,
        indent=2,
    ))
    if gaps:
        print(
            f"\nFAIL: {len(gaps)} 個 alias 冇喺 {REDIRECT_MAP.relative_to(ROOT)} 出現。"
            "\n舊 URL 已經出咗街，冇 308 就係硬 404。加返落去先當做完：\n  "
            + "\n  ".join(gaps),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
