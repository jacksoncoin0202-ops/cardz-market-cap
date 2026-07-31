#!/usr/bin/env python3
"""Repair the split Rare Candy / Mega Charizard X ex #125 identity.

Dry-run is the default.  ``--write`` performs one guarded MySQL transaction,
then repairs only the current private operational maps that carried the stale
variant id.  Historical reports remain immutable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from card_identity import (  # noqa: E402
    opaque_id_from_row,
    printing_key7,
    printing_key7_sha256,
)
from c11_pc_sold_ingest import db  # noqa: E402
from failure_ledger import record_failure, record_resolution  # noqa: E402
from identity_evidence_ledger import upsert_evidence  # noqa: E402


OLD_VARIANT_ID = 794
GEMRATE_ID = "65d400c703d55f5f027a164235672aed79bf8f78"
PC_ID = "11069001"
EBAY_ID = "28556947-0d32-4b80-a503-ca10fd81ecc7"
SNK_ID = "727323"
TARGET = {
    "tcg_code": "pokemon",
    "card_language": "en",
    "canonical_name": "Mega Charizard X ex",
    "set_name": "Pokemon Mega Evolution Phantasmal Flames",
    "collector_number": "125/094",
    "edition_code": "mega evolution phantasmal flames",
    "parallel_code": "sir",
    "finish_code": "foil",
}
TARGET_OPAQUE_ID = opaque_id_from_row(TARGET)
TARGET_PRINTING_SHA256 = printing_key7_sha256(
    printing_key7(
        TARGET["tcg_code"],
        TARGET["card_language"],
        TARGET["set_name"],
        TARGET["collector_number"],
        TARGET["edition_code"],
        TARGET["parallel_code"],
        TARGET["finish_code"],
    )
)
CHARIZARD_ASSET_IDS = (18, 2003, 2452)
CHARIZARD_POINTER_SOURCE_SHA256 = (
    "667bd4b905c5143386134a45f665ecc17931cb7bbd1f14372f8ef2406095991a",
    "caa718331d647a8c6ae70eb68ae695e949f0107d8f3d881387bdc8848eabcad0",
    "d4f98c2ec529af5c27b204e9d2176efceb8ca8f54c7abd8131f8daa85dbd27b0",
    "e7bf7baed9c9adb7148292543ec611ff39e680a31e2ed2c356f31fc2ef5b6db5",
)
FACT_TABLES = (
    "market_alert",
    "market_candidate_daily_snapshot",
    "market_daily_sales_aggregate",
    "market_gemrate_psa10_history",
    "market_index_constituent",
    "market_population_transport_observation",
    "market_sale_observation",
    "market_universe_member",
)
CURRENT_JSONL = (
    ROOT / "data/runtime/private-source-map/c11_pc_ebay_map_full900_shard_1.jsonl",
    ROOT / "data/runtime/private-source-map/c11_pc_ebay_map_full900.jsonl",
    ROOT / "data/runtime/private-source-map/qualified-940-worklist.jsonl",
    ROOT / "data/runtime/private-source-map/qualified-940-identity.jsonl",
    ROOT / "data/runtime/private-source-map/tpl-slug-map.jsonl",
    ROOT / "data/runtime/private-source-map/liquidity-source-registry.jsonl",
)


def sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fetch_one(cursor: Any, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    cursor.execute(query, params)
    return cursor.fetchone()


def preflight(cursor: Any) -> dict[str, Any]:
    old = fetch_one(
        cursor,
        "SELECT * FROM catalog_variant WHERE id=%s",
        (OLD_VARIANT_ID,),
    )
    if not old:
        raise RuntimeError(f"old variant {OLD_VARIANT_ID} is missing")
    expected_old = (
        str(old["canonical_name"]),
        str(old["collector_number"]),
        str(old["card_language"]),
    )
    if expected_old != ("Rare Candy", "125/132", "en"):
        raise RuntimeError(f"old variant drifted: {expected_old!r}")

    watch = fetch_one(
        cursor,
        "SELECT * FROM market_gemrate_psa10_watchlist WHERE gemrate_id=%s",
        (GEMRATE_ID,),
    )
    if not watch:
        raise RuntimeError("target GemRate watch row is missing")

    pc = fetch_one(
        cursor,
        """
        SELECT * FROM catalog_source_identity
        WHERE source_code='pricecharting' AND external_entity_id=%s
        """,
        (PC_ID,),
    )
    existing = fetch_one(
        cursor,
        "SELECT * FROM catalog_variant WHERE opaque_id=%s",
        (TARGET_OPAQUE_ID,),
    )
    if existing:
        target_fields = (
            str(existing["canonical_name"]),
            str(existing["set_name"]),
            str(existing["collector_number"]),
            str(existing["card_language"]),
        )
        expected = (
            TARGET["canonical_name"],
            TARGET["set_name"],
            TARGET["collector_number"],
            TARGET["card_language"],
        )
        if target_fields != expected:
            raise RuntimeError(f"target opaque id collision: {target_fields!r}")

    printing_owner = fetch_one(
        cursor,
        """
        SELECT variant_id FROM catalog_printing_identity
        WHERE canonical_printing_sha256=%s
        """,
        (TARGET_PRINTING_SHA256,),
    )
    if printing_owner and (
        not existing or int(printing_owner["variant_id"]) != int(existing["id"])
    ):
        raise RuntimeError(
            f"target printing hash already belongs to {printing_owner['variant_id']}"
        )
    if pc and int(pc["variant_id"]) not in {
        OLD_VARIANT_ID,
        int(existing["id"]) if existing else -1,
    }:
        raise RuntimeError(f"PriceCharting {PC_ID} belongs to unexpected variant")

    return {
        "oldVariantId": OLD_VARIANT_ID,
        "targetVariantId": int(existing["id"]) if existing else None,
        "targetOpaqueId": TARGET_OPAQUE_ID,
        "targetPrintingSha256": TARGET_PRINTING_SHA256,
        "watchVariantId": (
            int(watch["variant_id"]) if watch.get("variant_id") is not None else None
        ),
        "pricechartingVariantId": int(pc["variant_id"]) if pc else None,
        "psa10Population": int(watch["psa10_population"]),
        "populationAsOf": str(watch["population_as_of"]),
        "sourcePayloadSha256": str(watch["source_payload_sha256"]),
    }


def ensure_target_variant(cursor: Any) -> int:
    existing = fetch_one(
        cursor,
        "SELECT id FROM catalog_variant WHERE opaque_id=%s",
        (TARGET_OPAQUE_ID,),
    )
    if existing:
        variant_id = int(existing["id"])
    else:
        cursor.execute(
            """
            INSERT INTO catalog_variant
                (opaque_id, tcg_code, card_language, canonical_name, set_name,
                 collector_number, identity_status)
            VALUES (%s,%s,%s,%s,%s,%s,'confirmed')
            """,
            (
                TARGET_OPAQUE_ID,
                TARGET["tcg_code"],
                TARGET["card_language"],
                TARGET["canonical_name"],
                TARGET["set_name"],
                TARGET["collector_number"],
            ),
        )
        variant_id = int(cursor.lastrowid)

    printing_evidence = sha256_json(
        {
            "repair": "rare_candy_125_132_split_from_mega_charizard_125_094",
            "variantId": variant_id,
            "gemrateId": GEMRATE_ID,
            "pricechartingId": PC_ID,
            "ebayId": EBAY_ID,
            "snkrdunkId": SNK_ID,
            "printingSha256": TARGET_PRINTING_SHA256,
        }
    )
    cursor.execute(
        """
        INSERT INTO catalog_printing_identity
            (variant_id, tcg_code, card_language, set_name, collector_number,
             edition_code, parallel_code, finish_code, canonical_printing_sha256,
             identity_status, evidence_sha256)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'canonical',%s)
        ON DUPLICATE KEY UPDATE
            tcg_code=VALUES(tcg_code),
            card_language=VALUES(card_language),
            set_name=VALUES(set_name),
            collector_number=VALUES(collector_number),
            edition_code=VALUES(edition_code),
            parallel_code=VALUES(parallel_code),
            finish_code=VALUES(finish_code),
            canonical_printing_sha256=VALUES(canonical_printing_sha256),
            identity_status='canonical',
            evidence_sha256=VALUES(evidence_sha256)
        """,
        (
            variant_id,
            TARGET["tcg_code"],
            TARGET["card_language"],
            TARGET["set_name"].casefold(),
            TARGET["collector_number"],
            TARGET["edition_code"],
            TARGET["parallel_code"],
            TARGET["finish_code"],
            TARGET_PRINTING_SHA256,
            printing_evidence,
        ),
    )
    return variant_id


def bind_source(
    cursor: Any,
    *,
    variant_id: int,
    source: str,
    external_id: str,
    url: str | None,
    claim: dict[str, Any],
) -> None:
    evidence_sha256 = upsert_evidence(
        cursor,
        variant_id=variant_id,
        evidence_kind="bind",
        source_code=source,
        external_entity_id=external_id,
        external_url=url,
        match_status="exact",
        claim=claim,
        actor="repair_mega_charizard_125",
    )
    cursor.execute(
        """
        INSERT INTO catalog_source_identity
            (source_code, external_entity_id, variant_id, match_status, evidence_sha256)
        VALUES (%s,%s,%s,'exact',%s)
        ON DUPLICATE KEY UPDATE
            variant_id=VALUES(variant_id),
            match_status='exact',
            evidence_sha256=VALUES(evidence_sha256),
            updated_at=CURRENT_TIMESTAMP
        """,
        (source, external_id, variant_id, evidence_sha256),
    )


def apply_database(cursor: Any, preflight_state: dict[str, Any]) -> dict[str, Any]:
    variant_id = ensure_target_variant(cursor)
    moved: dict[str, int] = {}

    cursor.execute(
        """
        UPDATE catalog_identity_evidence
        SET variant_id=%s
        WHERE variant_id=%s
        """,
        (variant_id, OLD_VARIANT_ID),
    )
    moved["catalog_identity_evidence"] = int(cursor.rowcount)
    cursor.execute(
        """
        UPDATE catalog_identity_evidence
        SET match_status='superseded_repaired',
            actor='repair_mega_charizard_125'
        WHERE variant_id=%s AND source_code='pricecharting'
          AND external_entity_id=%s
          AND claim_json LIKE '%%"collector":"125/132"%%'
        """,
        (variant_id, PC_ID),
    )

    for table in FACT_TABLES:
        cursor.execute(
            f"UPDATE `{table}` SET variant_id=%s WHERE variant_id=%s",
            (variant_id, OLD_VARIANT_ID),
        )
        moved[table] = int(cursor.rowcount)

    placeholders = ",".join(["%s"] * len(CHARIZARD_ASSET_IDS))
    cursor.execute(
        f"""
        UPDATE market_image_asset
        SET variant_id=%s
        WHERE variant_id=%s AND id IN ({placeholders})
        """,
        (variant_id, OLD_VARIANT_ID, *CHARIZARD_ASSET_IDS),
    )
    moved["market_image_asset"] = int(cursor.rowcount)

    pointer_placeholders = ",".join(
        ["%s"] * len(CHARIZARD_POINTER_SOURCE_SHA256)
    )
    cursor.execute(
        f"""
        UPDATE market_image_source_pointer
        SET variant_id=%s
        WHERE variant_id=%s
          AND source_version_sha256 IN ({pointer_placeholders})
        """,
        (
            variant_id,
            OLD_VARIANT_ID,
            *CHARIZARD_POINTER_SOURCE_SHA256,
        ),
    )
    moved["market_image_source_pointer"] = int(cursor.rowcount)

    cursor.execute(
        """
        UPDATE market_gemrate_psa10_watchlist
        SET variant_id=%s, collector_number=%s
        WHERE gemrate_id=%s
        """,
        (variant_id, TARGET["collector_number"], GEMRATE_ID),
    )
    moved["market_gemrate_psa10_watchlist"] = int(cursor.rowcount)

    base_claim = {
        "name": TARGET["canonical_name"],
        "set": TARGET["set_name"],
        "collectorNumber": TARGET["collector_number"],
        "language": TARGET["card_language"],
        "repair": "split_from_rare_candy_125_132",
    }
    bind_source(
        cursor,
        variant_id=variant_id,
        source="gemrate",
        external_id=GEMRATE_ID,
        url=f"https://www.gemrate.com/universal-search?gemrate_id={GEMRATE_ID}",
        claim={
            **base_claim,
            "psa10Population": preflight_state["psa10Population"],
            "populationAsOf": preflight_state["populationAsOf"],
            "sourcePayloadSha256": preflight_state["sourcePayloadSha256"],
        },
    )
    bind_source(
        cursor,
        variant_id=variant_id,
        source="pricecharting",
        external_id=PC_ID,
        url=(
            "https://www.pricecharting.com/game/"
            "pokemon-phantasmal-flames/mega-charizard-x-ex-125"
        ),
        claim={
            **base_claim,
            "pricechartingProductId": int(PC_ID),
            "printingEvidence": "completed PSA 10 titles identify 125/094",
        },
    )
    bind_source(
        cursor,
        variant_id=variant_id,
        source="ebay",
        external_id=EBAY_ID,
        url=None,
        claim={
            **base_claim,
            "transport": "g10_altxyz",
            "printingEvidence": "2025 English Phantasmal Flames SIR #125",
        },
    )
    bind_source(
        cursor,
        variant_id=variant_id,
        source="snkrdunk",
        external_id=SNK_ID,
        url=f"https://snkrdunk.com/apparels/{SNK_ID}",
        claim={
            **base_claim,
            "printingEvidence": "PFL EN 125/094 SIR",
        },
    )
    return {"targetVariantId": variant_id, "moved": moved}


def transform_row(
    path: Path,
    row: dict[str, Any],
    *,
    variant_id: int,
    stamp: str,
    population: int,
    population_as_of: str,
) -> tuple[dict[str, Any], bool]:
    changed = False
    output = dict(row)
    is_target = (
        str(row.get("gemrateId") or "") == GEMRATE_ID
        or (
            int(row.get("variant_id") or 0) == OLD_VARIANT_ID
            and str(row.get("pc_product_id") or "") == PC_ID
        )
        or (
            int(row.get("variantId") or 0) == OLD_VARIANT_ID
            and (
                str(row.get("snkItemId") or "") == SNK_ID
                or str(row.get("externalId") or "") == EBAY_ID
            )
        )
    )
    if not is_target:
        return output, changed

    if "variant_id" in output:
        output["variant_id"] = variant_id
    if "variantId" in output:
        output["variantId"] = variant_id
    if "opaqueId" in output:
        output["opaqueId"] = TARGET_OPAQUE_ID
    if "collector_number" in output:
        output["collector_number"] = TARGET["collector_number"]
    if "collectorNumber" in output:
        output["collectorNumber"] = TARGET["collector_number"]
    if "psa10Population" in output:
        output["psa10Population"] = population
    if "populationAsOf" in output:
        output["populationAsOf"] = population_as_of

    if path.name == "tpl-slug-map.jsonl":
        output.update(
            {
                "tplSlug": None,
                "tplSlugCandidate": None,
                "tplMatchScore": 0,
                "tplCandidates": [],
                "needsReview": True,
                "mapError": "stale Rare Candy 125/132 slug removed",
            }
        )
    elif path.name == "qualified-940-identity.jsonl":
        output.update(
            {
                "tplSlug": None,
                "tplSlugCandidate": None,
                "tplNeedsReview": True,
                "tplMatchScore": 0,
                "tcgplayerId": "662184",
                "tplHistoryDays": 0,
                "tplPsa10EbayUsd": None,
                "tplImageUrl": None,
                "updatedAt": stamp,
            }
        )
        links = dict(output.get("links") or {})
        links.update(
            {
                "tpl": None,
                "tcgplayer": "https://www.tcgplayer.com/product/662184",
            }
        )
        output["links"] = links
        lookup = dict(output.get("lookup") or {})
        tpl = dict(lookup.get("tcgpricelookup") or {})
        tpl.update({"key": None, "link": None})
        lookup["tcgpricelookup"] = tpl
        tcgplayer = dict(lookup.get("tcgplayer") or {})
        tcgplayer.update(
            {
                "key": "662184",
                "link": "https://www.tcgplayer.com/product/662184",
            }
        )
        lookup["tcgplayer"] = tcgplayer
        cardz = dict(lookup.get("cardz") or {})
        cardz["key"] = TARGET_OPAQUE_ID
        lookup["cardz"] = cardz
        output["lookup"] = lookup
    elif path.name == "liquidity-source-registry.jsonl":
        for key in ("tplSlug", "tplScript", "tplMarkedAt"):
            output.pop(key, None)
        output.update(
            {
                "preferredLiquiditySource": "ebay",
                "externalId": EBAY_ID,
                "script": "pipelines/g10_ebay_ingest.py",
                "snkItemId": int(SNK_ID),
                "pcProductId": int(PC_ID),
                "updatedAt": stamp,
            }
        )
    # ``updatedAt`` is audit metadata, not a reason for an otherwise-idempotent
    # replay to rewrite the JSONL artifact on every run.
    before_without_timestamp = {key: value for key, value in row.items() if key != "updatedAt"}
    after_without_timestamp = {key: value for key, value in output.items() if key != "updatedAt"}
    if before_without_timestamp == after_without_timestamp:
        if "updatedAt" in row:
            output["updatedAt"] = row["updatedAt"]
        else:
            output.pop("updatedAt", None)
    changed = output != row
    return output, changed


def repair_artifacts(
    *,
    variant_id: int,
    population: int,
    population_as_of: str,
    write: bool,
) -> dict[str, int]:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    counts: dict[str, int] = {}
    for path in CURRENT_JSONL:
        if not path.is_file():
            continue
        rows: list[dict[str, Any]] = []
        changed = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            row, row_changed = transform_row(
                path,
                row,
                variant_id=variant_id,
                stamp=stamp,
                population=population,
                population_as_of=population_as_of,
            )
            rows.append(row)
            changed += int(row_changed)
        counts[str(path.relative_to(ROOT))] = changed
        if write and changed:
            temporary = path.with_suffix(path.suffix + ".repair-next")
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                for row in rows:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            os.replace(temporary, path)

    ledger = ROOT / "data/runtime/private-source-map/semi-auto-identity-ledger.jsonl"
    if ledger.is_file():
        existing = [
            json.loads(line)
            for line in ledger.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        already = any(
            row.get("action") == "repair_mega_charizard_125"
            and int(row.get("variantId") or 0) == variant_id
            for row in existing
        )
        counts[str(ledger.relative_to(ROOT))] = 0 if already else 1
        if write and not already:
            with ledger.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(
                    json.dumps(
                        {
                            "action": "repair_mega_charizard_125",
                            "at": stamp,
                            "oldVariantId": OLD_VARIANT_ID,
                            "variantId": variant_id,
                            "opaqueId": TARGET_OPAQUE_ID,
                            "gemrateId": GEMRATE_ID,
                            "pricechartingId": int(PC_ID),
                            "ebayId": EBAY_ID,
                            "snkItemId": int(SNK_ID),
                            "collectorNumber": TARGET["collector_number"],
                            "script": "scripts/repair_mega_charizard_125.py",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    connection = db()
    try:
        cursor = connection.cursor()
        before = preflight(cursor)
        if not args.write:
            artifact_counts = repair_artifacts(
                variant_id=before["targetVariantId"] or -1,
                population=before["psa10Population"],
                population_as_of=before["populationAsOf"],
                write=False,
            )
            print(
                json.dumps(
                    {
                        "write": False,
                        "preflight": before,
                        "artifactRowsNeedingRepair": artifact_counts,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

        try:
            database = apply_database(cursor, before)
            after = preflight(cursor)
            if after["watchVariantId"] != database["targetVariantId"]:
                raise RuntimeError("watchlist did not bind to repaired target")
            if after["pricechartingVariantId"] != database["targetVariantId"]:
                raise RuntimeError("PriceCharting identity did not move to repaired target")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

        artifacts = repair_artifacts(
            variant_id=database["targetVariantId"],
            population=before["psa10Population"],
            population_as_of=before["populationAsOf"],
            write=True,
        )
        record_resolution(
            source="identity_repair",
            stage="bind_canonical_variant",
            script=__file__,
            item_key=GEMRATE_ID,
            resolution="mega_charizard_125_split_from_rare_candy",
            context={
                "oldVariantId": OLD_VARIANT_ID,
                "targetVariantId": database["targetVariantId"],
                "artifactRows": sum(artifacts.values()),
            },
        )
        print(
            json.dumps(
                {
                    "write": True,
                    "before": before,
                    "database": database,
                    "after": after,
                    "artifactRowsRepaired": artifacts,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except Exception as error:
        record_failure(
            source="identity_repair",
            stage="bind_canonical_variant",
            script=__file__,
            item_key=GEMRATE_ID,
            reason_code="repair_failed",
            message=str(error),
            retryable=True,
            next_action="agent_review",
            error_type=type(error).__name__,
        )
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
