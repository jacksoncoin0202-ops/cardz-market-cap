#!/usr/bin/env python3
"""catalog_variant 重複組 → catalog_printing_identity 收斂。

## 重複判定口徑

跟 `docs/DB_INVENTORY_20260726.md` §4.2 記錄嘅主口徑：
`(tcg_code, card_language, set_name, collector_number, edition_code, parallel_code, finish_code)`
出現多過一次即一組。
呢個口徑係「舊 evidence 用咩，我就用咩」，唔係重新發明——所以數字可以同舊報告
直接對比（舊報告 1,590 行時 24 組／52 行）。

## 為何唔可以一組共用一個 hash

`catalog_printing_identity` 有 `PRIMARY KEY (variant_id)` **同時** 有
`UNIQUE KEY (canonical_printing_sha256)`。兩個約束一齊擺，張表結構上就係
「一個 variant 一個 printing key，而且 printing key 唔准撞」——即係佢冇辦法
表達「呢 3 個 variant_id 係同一個 printing」。UNIQUE 唔係阻礙，佢就係**探測器**：
同一組成員行落去會撞，撞就證明佢哋真係同一個 printing key。

所以收斂用呢個做法：

* **真重複組**（成員 `canonical_name` 一致）：選一個 canonical 成員，佢拿純
  7-part language-inclusive hash（同 `db_runtime.py` 一模一樣嘅配方）同
  `identity_status='canonical'`；
  其餘成員拿 `<純key>|variant:<id>` 嘅 salted hash 同 `identity_status='duplicate'`。
  `WHERE identity_status='canonical'` 就係每組一行。
* **名字唔一致組**（例如 `collector_number='Unknown'` 令 Pikachu 同 Charizard 撞埋）：
  **唔選 canonical**，全部成員 `identity_status='review'` + salted hash。呢啲根本
  唔係同一張卡，選 canonical 等於講 Charizard 係 Pikachu 嘅副本——寧願誠實標
  review，唔好寫一個錯嘅 merge 落 DB。

組員身份靠 7 條 identity 欄位 join 返（同組成員值完全一樣），唔靠 hash。

## 紅線

只寫 `catalog_printing_identity`。唔 UPDATE／DELETE `catalog_variant`，唔掂任何
`market_*` fact 表。commit 前會重新量一次 `catalog_variant` 嘅行數同 opaque_id
指紋，同開頭唔一致就 rollback 兼非零退出（fail-closed）。

預設 dry-run；要真寫要 `--write`。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args  # noqa: E402

# 口徑常數，寫落 evidence 度好過散落 SQL string 之間。
# Language is part of printing identity (post-018 / rehash_identity_language).
GROUP_KEY_COLUMNS = (
    "tcg_code",
    "card_language",
    "set_name",
    "collector_number",
    "edition_code",
    "parallel_code",
    "finish_code",
)
GROUP_CRITERION = "(" + ", ".join(GROUP_KEY_COLUMNS) + ")"

STATUS_CANONICAL = "canonical"
STATUS_DUPLICATE = "duplicate"
STATUS_REVIEW = "review"


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def printing_key(row: Mapping[str, Any]) -> str:
    """7-part canonical printing key (includes card_language).

    edition/parallel/finish 只讀已有 canonical printing evidence；空代表未有證據。
    唔准由 canonical_name 尾嘅 "(Error)" 之類
    反推 parallel_code：咁樣係猜，猜錯就係寫假數據落 canonical 層。
    """
    from card_identity import printing_key7_from_row

    return "|".join(printing_key7_from_row(row))


def load_variants(cursor: Any) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT variant.id, variant.opaque_id, variant.tcg_code,
               variant.card_language,
               variant.canonical_name, variant.set_name,
               variant.collector_number, variant.identity_status,
               COALESCE(printing.edition_code, '') AS edition_code,
               COALESCE(printing.parallel_code, '') AS parallel_code,
               COALESCE(printing.finish_code, '') AS finish_code
        FROM catalog_variant AS variant
        LEFT JOIN catalog_printing_identity AS printing
          ON printing.variant_id=variant.id
        ORDER BY variant.id
        """
    )
    return [dict(row) for row in cursor.fetchall()]


def load_evidence_weights(cursor: Any) -> dict[int, dict[str, int]]:
    """每個 variant 有幾多實質觀測證據——用嚟決定邊個成員做 canonical。

    三條獨立 aggregate query，唔用 correlated subquery：1,705 個 variant 逐個跑
    subquery 係無謂嘅。
    """
    weights: dict[int, dict[str, int]] = {}
    for field, sql in (
        ("priceObs", "SELECT variant_id, COUNT(*) AS n FROM market_price_observation GROUP BY variant_id"),
        ("popObs", "SELECT variant_id, COUNT(*) AS n FROM market_grader_population_observation GROUP BY variant_id"),
        ("sourceIds", "SELECT variant_id, COUNT(*) AS n FROM catalog_source_identity GROUP BY variant_id"),
    ):
        cursor.execute(sql)
        for row in cursor.fetchall():
            variant_id = int(row["variant_id"])
            weights.setdefault(variant_id, {"priceObs": 0, "popObs": 0, "sourceIds": 0})[field] = int(row["n"])
    return weights


def variant_fingerprint(cursor: Any) -> dict[str, Any]:
    """catalog_variant 嘅行數 + opaque_id/identity 指紋。

    紅線係「一個 opaque_id 都唔准變」。BIT_XOR/SUM 嘅 aggregate 對次序免疫，亦冇
    GROUP_CONCAT 嘅 1024-byte 截斷問題，所以做寫前寫後對比夠用。
    """
    cursor.execute(
        """
        SELECT COUNT(*) AS n,
               BIT_XOR(CRC32(CONCAT(id, ':', opaque_id))) AS xor_id_opaque,
               SUM(CRC32(opaque_id)) AS sum_crc_opaque,
               SUM(CRC32(CONCAT_WS('|', tcg_code, canonical_name,
                                        set_name, collector_number))) AS sum_crc_identity
        FROM catalog_variant
        """
    )
    row = dict(cursor.fetchone())
    return {key: (None if value is None else str(value)) for key, value in row.items()}


def build_groups(
    variants: Sequence[Mapping[str, Any]],
    weights: Mapping[int, Mapping[str, int]],
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in variants:
        key = tuple(str(row.get(column) or "") for column in GROUP_KEY_COLUMNS)
        buckets.setdefault(key, []).append(dict(row))

    groups: list[dict[str, Any]] = []
    for key, members in sorted(buckets.items()):
        if len(members) < 2:
            continue
        members.sort(key=lambda row: int(row["id"]))
        for member in members:
            member["weights"] = dict(weights.get(int(member["id"]), {"priceObs": 0, "popObs": 0, "sourceIds": 0}))

        names = {str(member["canonical_name"] or "") for member in members}
        base_key = printing_key(members[0])
        # 同組成員嘅 identity 欄位理論上一樣，key 都應該一樣。唔一樣就係分組
        # 邏輯同 key 配方脫節，fail-closed 好過寫落去。
        for member in members:
            if printing_key(member) != base_key:
                raise ValueError(f"group {key} 成員 printing key 唔一致: variant_id={member['id']}")

        same_card = len(names) == 1
        canonical_id: int | None = None
        if same_card:
            canonical = max(
                members,
                key=lambda row: (
                    row["weights"]["priceObs"],
                    row["weights"]["popObs"],
                    row["weights"]["sourceIds"],
                    -int(row["id"]),
                ),
            )
            canonical_id = int(canonical["id"])

        groups.append(
            {
                "groupKey": dict(zip(GROUP_KEY_COLUMNS, key)),
                "printingKey": base_key,
                "printingKeySha256": sha256(base_key.encode("utf-8")),
                "members": len(members),
                "distinctNames": len(names),
                "sameCard": same_card,
                "canonicalVariantId": canonical_id,
                "variantIds": [int(member["id"]) for member in members],
                "rows": members,
            }
        )
    return groups


def build_rows(groups: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in groups:
        for member in group["rows"]:
            variant_id = int(member["id"])
            if group["sameCard"]:
                status = STATUS_CANONICAL if variant_id == group["canonicalVariantId"] else STATUS_DUPLICATE
            else:
                status = STATUS_REVIEW
            if status == STATUS_CANONICAL:
                hash_input = group["printingKey"]
            else:
                hash_input = f"{group['printingKey']}|variant:{variant_id}"
            evidence = {
                "criterion": GROUP_CRITERION,
                "groupKey": group["groupKey"],
                "groupMembers": group["members"],
                "groupVariantIds": group["variantIds"],
                "distinctCanonicalNames": group["distinctNames"],
                "canonicalVariantId": group["canonicalVariantId"],
                "variantId": variant_id,
                "opaqueId": str(member["opaque_id"] or ""),
                "canonicalName": str(member["canonical_name"] or ""),
                "variantIdentityStatus": str(member["identity_status"] or ""),
                "weights": member["weights"],
                "printingKeySha256": group["printingKeySha256"],
                "identityStatus": status,
            }
            rows.append(
                {
                    "variantId": variant_id,
                    "tcgCode": str(member["tcg_code"] or "").strip().casefold(),
                    "cardLanguage": str(member.get("card_language") or "") or None,
                    "setName": str(member["set_name"] or ""),
                    "collectorNumber": str(member["collector_number"] or ""),
                    "editionCode": str(member["edition_code"] or ""),
                    "parallelCode": str(member["parallel_code"] or ""),
                    "finishCode": str(member["finish_code"] or ""),
                    "canonicalPrintingSha256": sha256(hash_input.encode("utf-8")),
                    "identityStatus": status,
                    "evidenceSha256": sha256(canonical_json(evidence)),
                    "canonicalName": str(member["canonical_name"] or ""),
                    "opaqueId": str(member["opaque_id"] or ""),
                }
            )

    # 寫之前自己驗一次：hash 一定 64-hex，一定唔准撞（撞就會踩 UNIQUE）。
    seen: dict[str, int] = {}
    for row in rows:
        digest = row["canonicalPrintingSha256"]
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError(f"canonical_printing_sha256 唔係 64-hex: variant_id={row['variantId']}")
        if digest in seen:
            raise ValueError(
                f"canonical_printing_sha256 撞: variant_id={row['variantId']} 同 {seen[digest]}"
            )
        seen[digest] = row["variantId"]
    return rows


def apply_rows(connection: Any, rows: Sequence[Mapping[str, Any]], *, commit: bool) -> dict[str, Any]:
    with connection.cursor() as cursor:
        before = variant_fingerprint(cursor)
        cursor.execute("SELECT COUNT(*) AS n FROM catalog_printing_identity")
        identity_before = int(cursor.fetchone()["n"])

        affected = 0
        for row in rows:
            cursor.execute(
                """
                INSERT INTO catalog_printing_identity
                    (variant_id, tcg_code, card_language, set_name, collector_number,
                     edition_code, parallel_code, finish_code, canonical_printing_sha256,
                     identity_status, evidence_sha256)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    tcg_code=VALUES(tcg_code), card_language=VALUES(card_language),
                    set_name=VALUES(set_name),
                    collector_number=VALUES(collector_number),
                    edition_code=VALUES(edition_code), parallel_code=VALUES(parallel_code),
                    finish_code=VALUES(finish_code),
                    canonical_printing_sha256=VALUES(canonical_printing_sha256),
                    identity_status=VALUES(identity_status),
                    evidence_sha256=VALUES(evidence_sha256)
                """,
                (
                    row["variantId"], row["tcgCode"], row.get("cardLanguage"),
                    row["setName"], row["collectorNumber"],
                    row["editionCode"], row["parallelCode"], row["finishCode"],
                    row["canonicalPrintingSha256"], row["identityStatus"], row["evidenceSha256"],
                ),
            )
            affected += 1

        cursor.execute("SELECT COUNT(*) AS n FROM catalog_printing_identity")
        identity_after = int(cursor.fetchone()["n"])
        after = variant_fingerprint(cursor)

    drifted = {key: (before[key], after[key]) for key in before if before[key] != after[key]}
    if drifted:
        connection.rollback()
        raise RuntimeError(f"catalog_variant 指紋變咗，已 rollback: {drifted}")

    if commit:
        connection.commit()
    else:
        connection.rollback()

    return {
        "rowsUpserted": affected,
        "identityBefore": identity_before,
        "identityAfterInTxn": identity_after,
        "variantFingerprintBefore": before,
        "variantFingerprintAfter": after,
        "committed": bool(commit),
    }


def summarize(groups: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    statuses = Counter(row["identityStatus"] for row in rows)
    return {
        "criterion": GROUP_CRITERION,
        "dupGroups": len(groups),
        "dupRows": sum(int(group["members"]) for group in groups),
        "trueDuplicateGroups": sum(1 for group in groups if group["sameCard"]),
        "divergentNameGroups": sum(1 for group in groups if not group["sameCard"]),
        "byStatus": dict(sorted(statuses.items())),
    }


def _print_report(
    summary: Mapping[str, Any],
    groups: Sequence[Mapping[str, Any]],
    report: Mapping[str, Any] | None,
    *,
    wrote: bool,
) -> None:
    print("=" * 78)
    print(f"catalog_variant → catalog_printing_identity 收斂 — {'WRITE' if wrote else 'DRY-RUN'}")
    print("=" * 78)
    print(f"  重複判定口徑                : {summary['criterion']}")
    print(f"  重複組                      : {summary['dupGroups']}")
    print(f"  涉及行數                    : {summary['dupRows']}")
    print(f"  真重複組（名一致）          : {summary['trueDuplicateGroups']}")
    print(f"  名唔一致組（唔准 merge）    : {summary['divergentNameGroups']}")
    print("\n[打算寫嘅 identity_status 分佈]")
    for status, count in summary["byStatus"].items():
        print(f"  {status:<12} {count}")

    print("\n[逐組明細]")
    for group in groups:
        key = group["groupKey"]
        tag = "SAME-CARD" if group["sameCard"] else "DIVERGENT"
        print(
            f"  [{tag}] {key['tcg_code']} | {key['set_name']} "
            f"| #{key['collector_number']} | edition={key['edition_code'] or '-'} "
            f"parallel={key['parallel_code'] or '-'} finish={key['finish_code'] or '-'} "
            f"| members={group['members']} names={group['distinctNames']}"
        )
        print(f"      variant_ids  : {group['variantIds']}")
        print(f"      canonical    : {group['canonicalVariantId'] if group['canonicalVariantId'] else '—（唔選）'}")
        for member in group["rows"]:
            weights = member["weights"]
            print(
                f"        - {member['id']:<6} {str(member['canonical_name'])[:44]:<44} "
                f"price={weights['priceObs']:<4} pop={weights['popObs']:<3} src={weights['sourceIds']}"
            )

    if report is None:
        return
    print("\n[寫入結果]")
    print(f"  upsert 執行行數                    : {report['rowsUpserted']}")
    print(f"  catalog_printing_identity 寫前     : {report['identityBefore']}")
    print(f"  catalog_printing_identity 交易內後 : {report['identityAfterInTxn']}")
    print(f"  committed                          : {report['committed']}")
    print("\n[catalog_variant 指紋（寫前 / 寫後，必須一模一樣）]")
    for key, value in report["variantFingerprintBefore"].items():
        after = report["variantFingerprintAfter"][key]
        flag = "OK" if value == after else "DRIFT"
        print(f"  {key:<18} {value} → {after}  [{flag}]")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="catalog_variant 重複組收斂入 catalog_printing_identity（預設 dry-run）。"
    )
    parser.add_argument("--write", action="store_true", help="commit（預設 dry-run）")
    parser.add_argument("--json-out", type=Path, default=None, help="把逐組判定寫落檔")
    add_connection_args(parser)
    args = parser.parse_args(argv)

    connection = connection_from_args(args)
    try:
        with connection.cursor() as cursor:
            variants = load_variants(cursor)
            weights = load_evidence_weights(cursor)
        groups = build_groups(variants, weights)
        rows = build_rows(groups)
        summary = summarize(groups, rows)
        summary["catalogVariantRows"] = len(variants)
        report = apply_rows(connection, rows, commit=bool(args.write))
    finally:
        connection.close()

    _print_report(summary, groups, report, wrote=bool(args.write))

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(
                {
                    "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat() + "Z",
                    "summary": summary,
                    "report": report,
                    "groups": [
                        {
                            "groupKey": group["groupKey"],
                            "printingKeySha256": group["printingKeySha256"],
                            "members": group["members"],
                            "distinctNames": group["distinctNames"],
                            "sameCard": group["sameCard"],
                            "canonicalVariantId": group["canonicalVariantId"],
                            "variantIds": group["variantIds"],
                            "memberDetail": [
                                {
                                    "variantId": int(member["id"]),
                                    "opaqueId": str(member["opaque_id"] or ""),
                                    "canonicalName": str(member["canonical_name"] or ""),
                                    "weights": member["weights"],
                                }
                                for member in group["rows"]
                            ],
                        }
                        for group in groups
                    ],
                    "rows": [
                        {
                            key: row[key]
                            for key in (
                                "variantId", "tcgCode", "setName", "collectorNumber", "cardLanguage",
                                "canonicalPrintingSha256", "identityStatus", "evidenceSha256",
                                "canonicalName", "opaqueId",
                            )
                        }
                        for row in rows
                    ],
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n  JSON → {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
