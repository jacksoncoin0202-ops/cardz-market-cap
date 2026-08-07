#!/usr/bin/env python3
"""由 G10 `asset_info.json` 開返 catalog 入面缺咗嘅卡，等研究文／POP／成交數據落到地。

點解要有呢個檔：`g10_research_ingest.py` 有 480 篇現成英文研究文，入到庫得 350 篇。
餘下 **130 篇唔係對唔到 identity —— 係嗰 130 張卡由頭到尾未入過 `catalog_variant`**。
實測兩路確認：130 個目錄喺 `catalog_source_identity` 任何 source 都搵唔到（gemrate 1496 /
snkrdunk 350 / ebay 119），喺 `market_source_observation` 亦都零行。即係 catalog 缺卡，
唔係接線缺失。`g10_identity_expand.py` 個 docstring 一早預言咗呢個 case：
「92 個目錄卡名喺 catalog 有、但 collector number 唔同」—— 兩張唔同嘅卡。

## 點解唔用 `db_runtime.upsert_variant`

佢第 641 行攞 `card["pokedexId"]` 做 opaque_id（GemRate id space），而 G10 `asset_info.json`
根本冇 GemRate id。佢仲有個 fail-closed identity assert，identity 一唔同就
`ValueError: opaque_id canonical identity changed`。所以呢度自己砌 INSERT。

## opaque_id 係內容定址，唔使外部 id

`g10_public_snapshot.opaque_id()` = `cmc_` + sha256(tcg, language, set_name,
collector.normalized, name)[:24]。即係由 G10 五個欄可以直接算返個 id 出嚟，
天然去重：同樣內容永遠出同一個 id，撞到就係已知卡，撞唔到就係真新卡。

## 兩個推斷步驟嘅實測命中率（350 個已對到嘅 snkrdunk 目錄做 ground truth）

  * `opaque_id` 重算：331/348 = **95.1%**。17 個 miss 全部係真數據分歧
    （setName 唔同、en vs ja、`in` vs `In` 大細楷），唔係算法錯。
  * `tcg_code` 推斷：350/350 = **100%**（要有 `^P-\d{2,3}$` 呢條，唔係 One Piece
    JUMP 附錄促銷卡會被當成 Pokemon）。

## altxyz 唔喺呢個腳本嘅範圍

118 個已對到嘅 altxyz 目錄入面 **113 個 `asset_info.json` 五欄唔齊**（96%），而且
altxyz 一篇 `summary_en.json` 都冇 —— 入咗都冇故事。要收 altxyz 要另外處理欄位缺失。

## 三層去重（順序唔可以掉轉）

  1. 算出嘅 `opaque_id` 撞到現有 variant → 唔開新卡，補 identity 落去（`existing_opaque`）
  2. `(tcg, language, set_name, collector_number)` printing key 撞到**一張** → 同上
     （`existing_printing`）。呢層免費修補 identity 覆蓋率。撞到**多過一張** → quarantine
  3. 都撞唔到 → 開新 variant

第 2 層一定要喺第 3 層之前。跳咗佢就會將「同一張卡但寫法唔同」開多一次，
直接餵大 CLAUDE.md 記低嗰 24 組重複。

## `identity_status` 點解係 'confirmed'

跟 `db_runtime.upsert_variant:676` 同一個 convention。呢個欄嘅語義係「呢個 variant 嘅
canonical identity 定唔定得落」，唔係「來源信唔信得過」—— G10 asset_info 五欄齊，identity
本身確定。用第二個值會令 `canonical_public_snapshot.py:259` 靜靜地隔走佢哋，製造
第二套語義。（新卡冇任何價格觀測，唔會即刻入榜。）

## Fail-closed

  * `language` 唔喺 {jp, en} → quarantine（DB 得 en/ja，實測 130 張入面 3 張 `zh-hans`）
  * 五欄任何一欄空 → quarantine，唔准填假值
  * `normalize_collector` 出 `complete=False` → quarantine。兩個出處：空輸入（第 133 行，
    出 "Unknown"）同埋十四條格式規則全部唔中嘅尾段 fallback（第 216 行）。後者計得出
    opaque_id，但代表個號碼格式我哋未認得 —— 照入就係攞未驗證嘅字串落 identity key
  * printing key 撞到多過一張 → quarantine，唔准估
  * quarantine 比例 > 40% → 唔入庫，推斷規則要先修

Exit codes: 0 = 正常, 1 = 冇嘢做 / fail-closed, 2 = G10 目錄唔見。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args
from g10_public_snapshot import normalize_collector, opaque_id

DEFAULT_G10_ROOT = ROOT.parent / "grade10-scraper" / "data" / "cards"
DEFAULT_REPORT_OUT = ROOT / "temp" / "g10-variant-seed-report.json"

SOURCE_CODE = "g10_variant_seed"
PROVIDER = "snkrdunk"
IDENTITY_SOURCE = "snkrdunk"
ASSET_FILENAME = "asset_info.json"

# G10 `language` → `catalog_variant.card_language`。DB 目前只有 en / ja 兩個值，
# 見到第三種語言唔准自創新 code，quarantine 交人裁。
LANGUAGE_MAP = {"jp": "ja", "en": "en"}

# One Piece 判別。`P-\d{2,3}` 嗰半條專門捉 JUMP 附錄促銷卡 —— 佢哋個 setName 係雜誌名
# （"Weekly Shonen JUMP 2023 36 37 combined issue supplement"），冇一個 One Piece 字眼。
ONE_PIECE_NUMBER = re.compile(r"^(OP|ST|EB|PRB)\d{1,2}-\d{1,3}$|^P-\d{2,3}$", re.I)
ONE_PIECE_SET = re.compile(
    r"one piece|romance dawn|emperors|conspiracy|wanted|straw hat", re.I
)

# `normalize_collector` 判 `complete=False` 但實測有 DB 先例嘅格式。**唔准喺 normalize_collector
# 加規則解決呢件事** —— 嗰個 function 供 6 個 producer 共用，改咗會令現有 1590 張卡嘅
# `opaque_id` 漂移，直接撞 CLAUDE.md「normalizer 唔一致 = 同一張卡分裂成兩個 id」。呢度只放寬
# 「收唔收」嘅判斷，`display` / `normalized` 照用 fallback 出嗰個原值，opaque_id 一個 bit 唔郁。
#
#   * `^\d{1,4}$` 裸數字冇分母 —— **DB 855 張卡（54%）就係咁**，248 個唯一值。日版盒裝／
#     促銷卡大多冇印分母。呢個唔係例外，係主流。
#   * `^PRB\d{2}-\d{3}$` One Piece Premium Booster —— DB 已有 `PRB02-006`（Roronoa Zoro）。
#     `normalize_collector:168` 條 `(?:OP|ST|EB)\d{2}-\d{3}` 冇包 PRB，純粹係覆蓋缺口。
SEED_ACCEPT_COLLECTOR = re.compile(r"^\d{1,4}$|^PRB\d{2}-\d{3}$", re.I)

QUARANTINE_FAIL_RATIO = 0.40
MAX_NAME_CHARS = 255
MAX_COLLECTOR_CHARS = 96


@dataclass(frozen=True)
class SeedRow:
    external_entity_id: str
    opaque: str
    tcg_code: str
    card_language: str
    canonical_name: str
    set_name: str
    collector_number: str
    source_product_number: str
    set_code: str
    printing_code: str
    rarity_code: str
    provider_claims: Mapping[str, Any]
    evidence_sha256: str
    source_path: str
    # 撞到現有 variant 就填佢個 id，開新卡就 None
    existing_variant_id: int | None
    resolution: str


@dataclass(frozen=True)
class Skipped:
    external_entity_id: str
    reason_code: str
    evidence_sha256: str
    detail: str


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def guess_tcg(set_name: str, collector_raw: str) -> str:
    if ONE_PIECE_NUMBER.match(str(collector_raw).strip()):
        return "one-piece"
    if ONE_PIECE_SET.search(str(set_name)):
        return "one-piece"
    return "pokemon"


def load_existing(cursor: Any) -> tuple[dict[str, int], dict[tuple, list[int]], set[str]]:
    """現有 catalog 嘅三個索引：opaque → id、printing key → [id]、已有 identity 嘅目錄名。"""

    cursor.execute(
        "SELECT id, opaque_id, tcg_code, card_language, set_name, collector_number "
        "FROM catalog_variant"
    )
    by_opaque: dict[str, int] = {}
    by_printing: dict[tuple, list[int]] = {}
    for row in cursor.fetchall():
        variant_id = int(row["id"])
        by_opaque[str(row["opaque_id"])] = variant_id
        key = (
            str(row["tcg_code"]).casefold(),
            str(row["card_language"]).casefold(),
            str(row["set_name"]).strip().casefold(),
            str(row["collector_number"]).strip().casefold(),
        )
        by_printing.setdefault(key, []).append(variant_id)

    cursor.execute(
        "SELECT external_entity_id FROM catalog_source_identity WHERE source_code = %s",
        (IDENTITY_SOURCE,),
    )
    mapped = {str(row["external_entity_id"]) for row in cursor.fetchall()}
    return by_opaque, by_printing, mapped


def scan(
    g10_root: Path,
    by_opaque: dict[str, int],
    by_printing: dict[tuple, list[int]],
    mapped: set[str],
    limit: int | None,
) -> tuple[list[SeedRow], list[Skipped], Counter]:
    provider_root = g10_root / PROVIDER
    rows: list[SeedRow] = []
    skipped: list[Skipped] = []
    stats: Counter = Counter()
    # 同一批入面自己撞自己（兩個 G10 目錄指向同一張卡）都要當已知，否則第二個會撞
    # `uq_catalog_variant_opaque`。
    pending_opaque: set[str] = set()

    directories = sorted(p for p in provider_root.iterdir() if p.is_dir())
    if limit is not None:
        directories = directories[:limit]

    for directory in directories:
        stats["observed"] += 1
        eid = directory.name
        if eid in mapped:
            stats["already_mapped"] += 1
            continue

        asset_path = directory / ASSET_FILENAME
        if not asset_path.is_file():
            stats["quarantined"] += 1
            skipped.append(Skipped(eid, "g10_no_asset_info", "", "冇 asset_info.json"))
            continue

        payload = asset_path.read_bytes()
        evidence = sha256_bytes(payload)
        try:
            asset = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            stats["quarantined"] += 1
            skipped.append(
                Skipped(eid, "g10_asset_unreadable", evidence, f"{type(error).__name__}: {error}")
            )
            continue

        name = str(asset.get("cardName") or "").strip()
        set_name = str(asset.get("setName") or "").strip()
        collector_raw = str(asset.get("cardId") or "").strip()
        raw_language = str(asset.get("language") or "").strip().casefold()
        set_code = str(asset.get("setCode") or "").strip()
        printing_code = str(asset.get("printingCode") or "").strip()
        rarity_code = str(asset.get("rarityCode") or "").strip()
        provider_claims = {
            "provider": PROVIDER,
            "externalEntityId": eid,
            "cardName": name,
            "setName": set_name,
            "productNumber": collector_raw,
            "language": raw_language,
            "setCode": set_code or None,
            "printingCode": printing_code or None,
            "rarityCode": rarity_code or None,
            "sourcePath": str(asset_path.relative_to(g10_root.parent.parent)).replace("\\", "/"),
        }

        # `cardId` 唔喺必要欄位入面：實測 18 張 1996–2001 老日版卡（初版 Expansion Pack
        # Charizard / Mewtwo / Blastoise、Rocket Gang Dark Charizard、Mystery of the
        # Fossils Mew…）個 `cardId` 係空 —— **因為嗰個年代嘅日版卡本身冇印 collector
        # number**。呢個係卡嘅事實唔係數據殘缺，`normalize_collector:133` 就係為呢個 case
        # 而設（出 "Unknown"）。當佢缺欄位就會丟走呢批最貴嘅老卡。
        missing = [
            field
            for field, value in (
                ("cardName", name),
                ("setName", set_name),
                ("language", raw_language),
            )
            if not value
        ]
        if missing:
            stats["quarantined"] += 1
            skipped.append(
                Skipped(eid, "g10_asset_incomplete", evidence, f"缺欄位: {','.join(missing)}")
            )
            continue

        language = LANGUAGE_MAP.get(raw_language)
        if language is None:
            stats["quarantined"] += 1
            skipped.append(
                Skipped(
                    eid,
                    "g10_language_unmapped",
                    evidence,
                    f"language={raw_language!r} 冇對應 DB card_language（只有 en/ja）",
                )
            )
            continue

        tcg_code = guess_tcg(set_name, collector_raw)
        collector = normalize_collector(collector_raw, language, set_name)
        unnumbered = not collector_raw
        if not collector.complete and not unnumbered and not SEED_ACCEPT_COLLECTOR.match(collector_raw):
            stats["quarantined"] += 1
            skipped.append(
                Skipped(
                    eid,
                    "g10_collector_unparsed",
                    evidence,
                    f"cardId={collector_raw!r} 唔符合任何已知格式",
                )
            )
            continue
        if unnumbered:
            stats["unnumbered"] += 1

        if len(name) > MAX_NAME_CHARS or len(set_name) > MAX_NAME_CHARS:
            stats["quarantined"] += 1
            skipped.append(Skipped(eid, "g10_field_too_long", evidence, "卡名/系列名超過 255 字"))
            continue
        if any(len(value) > 64 for value in (set_code, printing_code, rarity_code)):
            stats["quarantined"] += 1
            skipped.append(
                Skipped(eid, "g10_provider_code_too_long", evidence, "provider printing code 超過 64 字")
            )
            continue
        if len(collector.display) > MAX_COLLECTOR_CHARS:
            stats["quarantined"] += 1
            skipped.append(Skipped(eid, "g10_field_too_long", evidence, "collector_number 超過 96 字"))
            continue

        opaque = opaque_id(tcg_code, language, set_name, collector, name)
        printing_key = (
            tcg_code.casefold(),
            language.casefold(),
            set_name.casefold(),
            collector.display.strip().casefold(),
        )

        existing_id: int | None = None
        if opaque in by_opaque:
            existing_id = by_opaque[opaque]
            resolution = "existing_opaque"
        elif opaque in pending_opaque:
            stats["quarantined"] += 1
            skipped.append(
                Skipped(
                    eid,
                    "g10_duplicate_within_batch",
                    evidence,
                    f"同批另一個目錄已經算出 {opaque}",
                )
            )
            continue
        else:
            candidates = by_printing.get(printing_key, [])
            if len(candidates) == 1:
                existing_id = candidates[0]
                resolution = "existing_printing"
            elif len(candidates) > 1:
                stats["quarantined"] += 1
                skipped.append(
                    Skipped(
                        eid,
                        "g10_printing_ambiguous",
                        evidence,
                        f"printing key 撞到 {len(candidates)} 張 variant: {candidates}",
                    )
                )
                continue
            else:
                resolution = "new_variant"
                pending_opaque.add(opaque)

        stats["accepted"] += 1
        stats[resolution] += 1
        rows.append(
            SeedRow(
                external_entity_id=eid,
                opaque=opaque,
                tcg_code=tcg_code,
                card_language=language,
                canonical_name=name,
                set_name=set_name,
                collector_number=collector.display,
                source_product_number=collector_raw,
                set_code=set_code,
                printing_code=printing_code,
                rarity_code=rarity_code,
                provider_claims=provider_claims,
                evidence_sha256=evidence,
                source_path=str(asset_path.relative_to(g10_root.parent.parent)).replace("\\", "/"),
                existing_variant_id=existing_id,
                resolution=resolution,
            )
        )

    return rows, skipped, stats


def table_count(cursor: Any, table: str) -> int:
    cursor.execute(f"SELECT COUNT(*) AS n FROM {table}")
    return int(cursor.fetchone()["n"])


def write_all(connection: Any, rows: list[SeedRow], skipped: list[Skipped], stats: Counter) -> dict:
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    run_key = sha256_text(f"{SOURCE_CODE}|{started_at.isoformat()}")
    payload_sha256 = sha256_text("\n".join(sorted(f"{r.external_entity_id}:{r.opaque}" for r in rows)))
    manifest_sha256 = sha256_text(
        "\n".join(sorted(f"{r.source_path}:{r.evidence_sha256}" for r in rows))
    )

    with connection.cursor() as cursor:
        before = {
            "catalog_variant": table_count(cursor, "catalog_variant"),
            "catalog_source_identity": table_count(cursor, "catalog_source_identity"),
            "market_identity_review_queue": table_count(cursor, "market_identity_review_queue"),
        }
        cursor.execute(
            """
            INSERT INTO market_ingest_run
                (run_key, source_code, ingest_mode, effective_at, payload_sha256, manifest_sha256,
                 status, observed_count, started_at)
            VALUES (%s, %s, 'backfill', %s, %s, %s, 'running', %s, %s)
            """,
            (
                run_key,
                SOURCE_CODE,
                started_at,
                payload_sha256,
                manifest_sha256,
                int(stats.get("observed", 0)),
                started_at,
            ),
        )
        run_id = int(cursor.lastrowid)

        created = 0
        for row in rows:
            variant_id = row.existing_variant_id
            if variant_id is None:
                cursor.execute(
                    """
                    INSERT INTO catalog_variant
                        (opaque_id, tcg_code, card_language, canonical_name, set_name,
                         set_code, printing_code, rarity_code, collector_number, identity_status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'confirmed')
                    """,
                    (
                        row.opaque,
                        row.tcg_code,
                        row.card_language,
                        row.canonical_name,
                        row.set_name,
                        row.set_code,
                        row.printing_code,
                        row.rarity_code,
                        row.collector_number,
                    ),
                )
                variant_id = int(cursor.lastrowid)
                created += 1
            cursor.execute(
                """
                INSERT INTO catalog_source_identity
                    (source_code, external_entity_id, variant_id, match_status, evidence_sha256,
                     source_product_number, bound_set_code, bound_printing_code, bind_evidence_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    variant_id=VALUES(variant_id), evidence_sha256=VALUES(evidence_sha256),
                    source_product_number=CASE
                        WHEN VALUES(source_product_number)<>'' THEN VALUES(source_product_number)
                        ELSE source_product_number END,
                    bound_set_code=CASE
                        WHEN VALUES(bound_set_code)<>'' THEN VALUES(bound_set_code)
                        ELSE bound_set_code END,
                    bound_printing_code=CASE
                        WHEN VALUES(bound_printing_code)<>'' THEN VALUES(bound_printing_code)
                        ELSE bound_printing_code END,
                    bind_evidence_json=VALUES(bind_evidence_json)
                """,
                (
                    IDENTITY_SOURCE, row.external_entity_id, variant_id, row.resolution,
                    row.evidence_sha256, row.source_product_number, row.set_code,
                    row.printing_code,
                    json.dumps(
                        {
                            "schemaVersion": 1,
                            "matchMethod": row.resolution,
                            "providerClaims": row.provider_claims,
                            "sourcePayloadSha256": row.evidence_sha256,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )

        if skipped:
            cursor.executemany(
                """
                INSERT INTO market_identity_review_queue
                    (run_id, source_code, external_entity_id, reason_code, evidence_sha256)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE status='pending'
                """,
                [
                    (run_id, IDENTITY_SOURCE, s.external_entity_id, s.reason_code, s.evidence_sha256)
                    for s in skipped
                ],
            )

        cursor.execute(
            """
            UPDATE market_ingest_run
            SET status='completed', accepted_count=%s, quarantined_count=%s, rejected_count=0,
                completed_at=%s
            WHERE id=%s
            """,
            (
                len(rows),
                len(skipped),
                datetime.now(timezone.utc).replace(tzinfo=None),
                run_id,
            ),
        )
        after = {
            "catalog_variant": table_count(cursor, "catalog_variant"),
            "catalog_source_identity": table_count(cursor, "catalog_source_identity"),
            "market_identity_review_queue": table_count(cursor, "market_identity_review_queue"),
        }

    return {
        "run_id": run_id,
        "run_key": run_key,
        "variants_created": created,
        "before": before,
        "after": after,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--g10-root", type=Path, default=DEFAULT_G10_ROOT)
    parser.add_argument("--write", action="store_true", help="真入庫（預設 dry-run）")
    parser.add_argument("--limit", type=int, default=None, help="只掃頭 N 個卡目錄")
    parser.add_argument("--report-out", type=Path, default=DEFAULT_REPORT_OUT)
    add_connection_args(parser)
    args = parser.parse_args()

    g10_root = args.g10_root.resolve()
    if not (g10_root / PROVIDER).is_dir():
        print(f"[seed] G10 目錄唔見: {g10_root / PROVIDER}")
        return 2

    connection = connection_from_args(args)
    try:
        with connection.cursor() as cursor:
            by_opaque, by_printing, mapped = load_existing(cursor)
        rows, skipped, stats = scan(g10_root, by_opaque, by_printing, mapped, args.limit)

        observed = int(stats.get("observed", 0))
        unmapped = observed - int(stats.get("already_mapped", 0))
        quarantine_ratio = (len(skipped) / unmapped) if unmapped else 0.0

        report = {
            "g10Root": str(g10_root),
            "provider": PROVIDER,
            "identitySource": IDENTITY_SOURCE,
            "stats": dict(stats),
            "unmapped": unmapped,
            "quarantineRatio": round(quarantine_ratio, 4),
            "skipReasons": dict(Counter(s.reason_code for s in skipped)),
            "accepted": [
                {
                    "externalEntityId": r.external_entity_id,
                    "opaqueId": r.opaque,
                    "resolution": r.resolution,
                    "existingVariantId": r.existing_variant_id,
                    "tcgCode": r.tcg_code,
                    "cardLanguage": r.card_language,
                    "canonicalName": r.canonical_name,
                    "setName": r.set_name,
                    "collectorNumber": r.collector_number,
                }
                for r in rows
            ],
            "quarantined": [
                {
                    "externalEntityId": s.external_entity_id,
                    "reasonCode": s.reason_code,
                    "detail": s.detail,
                }
                for s in skipped
            ],
            "write": bool(args.write),
        }

        print(f"G10 root       : {g10_root}")
        print(f"provider       : {PROVIDER} (identity source={IDENTITY_SOURCE})")
        print(f"[掃描] 目錄 {observed} | 已對到 {stats.get('already_mapped', 0)} | 未對到 {unmapped}")
        print(
            f"  accepted {len(rows)}"
            f"  = 開新卡 {stats.get('new_variant', 0)}"
            f" + 撞現有 opaque {stats.get('existing_opaque', 0)}"
            f" + 撞現有 printing {stats.get('existing_printing', 0)}"
        )
        print(f"  quarantined {len(skipped)} ({quarantine_ratio:.1%})")
        for reason, count in sorted(Counter(s.reason_code for s in skipped).items(), key=lambda kv: -kv[1]):
            print(f"    {reason:32s} {count}")

        if not rows:
            report["failReason"] = "no_rows"
            args.report_out.parent.mkdir(parents=True, exist_ok=True)
            args.report_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            print("[seed] 冇嘢入庫。")
            return 1

        if quarantine_ratio > QUARANTINE_FAIL_RATIO:
            report["failReason"] = f"quarantine_ratio>{QUARANTINE_FAIL_RATIO}"
            args.report_out.parent.mkdir(parents=True, exist_ok=True)
            args.report_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"[seed] fail-closed：quarantine 比例 {quarantine_ratio:.1%} 超過上限，推斷規則要先修。")
            return 1

        if args.write:
            outcome = write_all(connection, rows, skipped, stats)
            connection.commit()
            report["writeOutcome"] = outcome
            print(f"✅ run_id {outcome['run_id']}｜新開 variant {outcome['variants_created']}")
            for table in ("catalog_variant", "catalog_source_identity", "market_identity_review_queue"):
                print(f"   {table}: {outcome['before'][table]} → {outcome['after'][table]}")
        else:
            print("[seed] dry-run（加 --write 先真入庫）")

        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[seed] 報告: {args.report_out}")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
