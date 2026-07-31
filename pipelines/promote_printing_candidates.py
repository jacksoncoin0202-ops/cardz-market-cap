#!/usr/bin/env python3
"""Promote lock-member printing identities from 'candidate' to 'canonical'.

The 56 affected rows were written before the 7-part printing identity contract
(edition/parallel/finish) existed, so they carry empty parallel metadata and a
legacy hash. For every lock member still at 'candidate' this script:

1. derives edition/parallel/finish from the card's exact GemRate receipt
   (``data/private/gemrate/cards/<gemrate_id>/card_details.json``), preferring
   the edition vocabulary already used by the canonical cohort for the same
   variant set_name;
2. aligns the printing row's base fields to the catalog_variant row (the audit
   requires printing tcg/language/set/number == variant's);
3. recomputes ``canonical_printing_sha256`` with the shared 7-part recipe and
   proves both hash and tuple are unique in the whole table;
4. writes one approval receipt per card and marks the row canonical.

Dry-run by default; ``--write`` applies. Anything that cannot be mapped stays
'candidate' and is reported — never silently promoted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args  # noqa: E402
from universe_authority import SHA256_HEX, _printing_key, _printing_key_sha256  # noqa: E402

RECEIPT_ROOT = ROOT / "data" / "private" / "gemrate" / "cards"
RECEIPT_OUT = ROOT / "data" / "runtime" / "private-reports" / "printing-candidate-promotion"

# 候選 variant set_name → cohort 通用 edition（已對 canonical cohort 核實）。
# 呢啲 variant set_name 喺 cohort 冇同款，fallback 由 receipt 推會出唔同風格，所以寫明。
SET_EDITION_OVERRIDES = {
    "SV8a: Terastal Fest ex": "high class pack terastal festival ex",
    "SV2a: Pokemon Card 151": "enhanced expansion pack pokemon card 151",
    "One Piece Romance Dawn": "booster pack romance dawn",
    "One Piece Awakening of the New Era": "booster pack awakening of the new era",
    "2023 Awakening of the New Era Special Art": "booster pack awakening of the new era",
}

PARALLEL_MAP = {
    "Special Art Rare": ("sar", "foil"),
    "Art Rare": ("ar", "foil"),
    "Ultra Rare": ("ur", "foil"),
    "Super Rare": ("sr", "foil"),
    "Master Ball Reverse Holo": ("master ball reverse holo", "reverse-holo"),
    "Alternate Art": ("aa", "foil"),
    "Manga Alternate Art": ("sec-sp", "foil"),
    "Special Alternate Art": ("sec-sp", "foil"),
    "1st Anniversary-Signature": ("sr-spc", "foil"),
    "1st Anniversary": ("base", "non-foil"),
    "Alternate Art-Errata": ("aa", "foil"),
    "Wanted Alternate Art": ("sec-sp", "foil"),
    "Treasure Rare": ("tr", "foil"),
}
# Receipt says 'Base' but the card's own name proves a radiant/parallel printing.
VARIANT_PARALLEL_OVERRIDES = {
    632: ("rr", "foil"),  # Radiant Charizard
    910: ("base", "non-foil"),  # Celebrations Classic Collection reprint
}


def canon_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def receipt_identity(gemrate_id: str) -> Mapping[str, Any] | None:
    path = RECEIPT_ROOT / gemrate_id / "card_details.json"
    if not path.is_file():
        return None
    document = json.loads(path.read_text(encoding="utf-8"))
    identity = (document.get("publicCardPage") or {}).get("identity") or {}
    return identity if identity.get("parallel") else None


def edition_fallback(receipt_set: str) -> str:
    """Receipt set_name → 簡約全名（無 cohort 參照時嘅後備）。"""

    text = receipt_set.strip()
    for prefix in ("Pokemon Japanese ", "One Piece ", "Pokemon "):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    # 去 set code 前綴（SV8a- / OP05- / OP14-EB04- 等）
    head, _, tail = text.partition("-")
    if tail and all(part.isupper() or part.isdigit() for part in head.replace("EB", "0").split()):
        text = tail
    return " ".join(text.replace("-", " ").split()).casefold()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_connection_args(parser)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    connection = connection_from_args(args)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    applied: list[int] = []
    skipped: list[dict[str, Any]] = []
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT m.variant_id, v.tcg_code, v.card_language, v.set_name, v.collector_number,
                   si.external_entity_id AS gemrate_id
            FROM market_universe_member m
            JOIN market_universe_lock l ON l.id = m.universe_lock_id AND l.is_current = 1
            JOIN catalog_variant v ON v.id = m.variant_id
            JOIN catalog_printing_identity p ON p.variant_id = m.variant_id
            JOIN catalog_source_identity si
              ON si.variant_id = m.variant_id AND si.source_code = 'gemrate' AND si.match_status = 'exact'
            WHERE p.identity_status = 'candidate'
            ORDER BY m.variant_id
            """
        )
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT v.set_name, p.edition_code, COUNT(*) AS n
            FROM catalog_printing_identity p
            JOIN catalog_variant v ON v.id = p.variant_id
            WHERE p.identity_status = 'canonical' AND p.edition_code <> ''
            GROUP BY v.set_name, p.edition_code
            """
        )
        edition_by_set: dict[str, str] = {}
        edition_votes: dict[str, tuple[int, str]] = {}
        for row in cursor.fetchall():
            set_name = str(row["set_name"])
            votes = edition_votes.get(set_name)
            if votes is None or int(row["n"]) > votes[0]:
                edition_votes[set_name] = (int(row["n"]), str(row["edition_code"]))
        edition_by_set = {set_name: best for set_name, (_n, best) in edition_votes.items()}
    plan: list[dict[str, Any]] = []
    for row in rows:
        variant_id = int(row["variant_id"])
        identity = receipt_identity(str(row["gemrate_id"]))
        if identity is None:
            skipped.append({"variantId": variant_id, "reason": "gemrate_receipt_missing"})
            continue
        receipt_parallel = str(identity.get("parallel") or "")
        mapped = VARIANT_PARALLEL_OVERRIDES.get(variant_id) or PARALLEL_MAP.get(receipt_parallel)
        if mapped is None:
            skipped.append({"variantId": variant_id, "reason": f"parallel_unmapped:{receipt_parallel}"})
            continue
        parallel_code, finish_code = mapped
        edition_code = (
            SET_EDITION_OVERRIDES.get(str(row["set_name"]))
            or edition_by_set.get(str(row["set_name"]))
            or " ".join(str(row["set_name"]).replace(":", " ").replace("-", " ").split()).casefold()
        )
        key = _printing_key(
            row["tcg_code"], row["card_language"], row["set_name"], row["collector_number"],
            edition_code, parallel_code, finish_code,
        )
        if any(not part for part in key):
            skipped.append({"variantId": variant_id, "reason": f"empty_key_part:{key}"})
            continue
        digest = _printing_key_sha256(key)
        plan.append(
            {
                "variantId": variant_id,
                "gemrateId": str(row["gemrate_id"]),
                "tcg": row["tcg_code"],
                "language": row["card_language"],
                "setName": row["set_name"],
                "collectorNumber": row["collector_number"],
                "editionCode": edition_code,
                "parallelCode": parallel_code,
                "finishCode": finish_code,
                "receiptParallel": receipt_parallel,
                "canonicalPrintingSha256": digest,
            }
        )
    with connection.cursor() as cursor:
        for item in plan:
            cursor.execute(
                "SELECT variant_id, identity_status FROM catalog_printing_identity WHERE canonical_printing_sha256 = %s AND variant_id <> %s",
                (item["canonicalPrintingSha256"], item["variantId"]),
            )
            if cursor.fetchone() is not None:
                item["blocked"] = "hash_collision"
                continue
            cursor.execute(
                """SELECT variant_id FROM catalog_printing_identity
                   WHERE tcg_code=%s AND card_language=%s AND set_name=%s AND collector_number=%s
                     AND edition_code=%s AND parallel_code=%s AND finish_code=%s AND variant_id<>%s""",
                (
                    item["tcg"], item["language"], item["setName"], item["collectorNumber"],
                    item["editionCode"], item["parallelCode"], item["finishCode"], item["variantId"],
                ),
            )
            if cursor.fetchone() is not None:
                item["blocked"] = "tuple_collision"
    ready = [item for item in plan if "blocked" not in item]
    blocked = [item for item in plan if "blocked" in item]
    for item in blocked:
        skipped.append({"variantId": item["variantId"], "reason": item["blocked"], "proposed": item})
    if args.write and ready:
        RECEIPT_OUT.mkdir(parents=True, exist_ok=True)
        with connection.cursor() as cursor:
            for item in ready:
                receipt = {
                    "schemaVersion": 1,
                    "type": "printing-candidate-promotion/v1",
                    "promotedAt": now,
                    "evidence": {
                        "gemrateReceipt": f"data/private/gemrate/cards/{item['gemrateId']}/card_details.json",
                        "receiptParallel": item["receiptParallel"],
                    },
                    **{key: item[key] for key in (
                        "variantId", "gemrateId", "tcg", "language", "setName", "collectorNumber",
                        "editionCode", "parallelCode", "finishCode", "canonicalPrintingSha256",
                    )},
                }
                receipt_sha = hashlib.sha256(canon_json(receipt)).hexdigest()
                receipt_path = RECEIPT_OUT / f"{item['variantId']}.json"
                receipt_path.write_bytes(canon_json(receipt))
                cursor.execute(
                    """
                    UPDATE catalog_printing_identity
                    SET tcg_code=%s, card_language=%s, set_name=%s, collector_number=%s,
                        edition_code=%s, parallel_code=%s, finish_code=%s,
                        canonical_printing_sha256=%s, identity_status='canonical', evidence_sha256=%s
                    WHERE variant_id=%s AND identity_status='candidate'
                    """,
                    (
                        item["tcg"], item["language"], item["setName"], item["collectorNumber"],
                        item["editionCode"], item["parallelCode"], item["finishCode"],
                        item["canonicalPrintingSha256"], receipt_sha, item["variantId"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(f"promotion raced or row missing: {item['variantId']}")
                applied.append(item["variantId"])
        connection.commit()
    print(
        json.dumps(
            {
                "status": "applied" if args.write else "dry-run",
                "candidates": len(rows),
                "ready": len(ready),
                "applied": applied,
                "skipped": skipped,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
