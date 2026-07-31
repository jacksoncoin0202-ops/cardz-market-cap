#!/usr/bin/env python3
"""Quarantine the proven wrong PriceCharting bindings from the 2026 full pass.

The plan is immutable and content-addressed.  The writer preserves identity
evidence, changes the current binding from ``exact`` to ``conflict``, and marks
only price rows written by a PriceCharting materializer as ``quarantined``.
It never deletes identities, prices, sales, or source artifacts.
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

from c11_pc_sold_ingest import db
from pc_psa10_price_derivation import sha256
from pc_ungraded_reference_ingest import MAP_DEFAULT, load_candidate_rows

CONTRACT = "wrong_pricecharting_binding_quarantine_v1"
ACTOR = "wrong_pricecharting_binding_quarantine"

# Each pair was proven from canonical TCG/language/set/collector versus the
# saved PriceCharting product route.  Keep this allowlist closed: printing
# disagreements that are not proven source errors remain in human review.
PROVEN_BAD: dict[int, tuple[str, str]] = {
    83: ("4614290", "jp_151_bound_to_1996_carddass"),
    123: ("3473090", "jp_151_bound_to_1997_carddass"),
    176: ("5809554", "jp_151_bound_to_english_151"),
    353: ("5953436", "jp_151_bound_to_1997_carddass"),
    906: ("762776", "celebrations_bound_to_pop_series_5_gold_star"),
    987: ("5809420", "jp_151_bound_to_english_151"),
    990: ("5809461", "jp_151_bound_to_english_151"),
    992: ("5809475", "jp_151_bound_to_english_151"),
    996: ("5809518", "jp_151_bound_to_english_151"),
    999: ("4614401", "jp_151_bound_to_1996_carddass"),
    1008: ("5809466", "jp_151_bound_to_english_151"),
    1010: ("5809473", "jp_151_bound_to_english_151"),
    1017: ("5809525", "jp_151_bound_to_english_151"),
    1043: ("5809530", "jp_151_bound_to_english_151"),
    1253: ("8508378", "english_one_piece_bound_to_japanese_product"),
    1455: ("5809387", "jp_151_bound_to_english_151"),
}


def plan_sha256(document: Mapping[str, Any]) -> str:
    return sha256({key: value for key, value in document.items() if key != "planSha256"})


def validate_allowed(rows: list[Mapping[str, Any]]) -> None:
    observed = {
        int(row.get("variantId") or 0): str(row.get("pcProductId") or "")
        for row in rows
    }
    expected = {variant_id: product for variant_id, (product, _reason) in PROVEN_BAD.items()}
    if observed != expected:
        raise ValueError("plan does not exactly match the proven wrong-binding allowlist")


def build_plan(connection: Any, map_path: Path) -> dict[str, Any]:
    candidates: dict[int, list[dict[str, Any]]] = {}
    for row in load_candidate_rows(map_path):
        variant_id = int(row["variant_id"])
        if variant_id in PROVEN_BAD:
            candidates.setdefault(variant_id, []).append(row)
    if set(candidates) != set(PROVEN_BAD) or any(len(rows) != 1 for rows in candidates.values()):
        raise ValueError("consolidated map is missing or duplicates a proven wrong binding")

    ids = sorted(PROVEN_BAD)
    marks = ",".join(["%s"] * len(ids))
    with connection.cursor() as cursor:
        cursor.execute(
            f"""SELECT variant.id,variant.opaque_id,variant.canonical_name,
                       variant.set_name,variant.collector_number,variant.card_language,
                       identity.external_entity_id,identity.match_status,
                       identity.evidence_sha256
                FROM catalog_variant AS variant
                JOIN catalog_source_identity AS identity
                  ON identity.variant_id=variant.id
                 AND identity.source_code='pricecharting'
                WHERE variant.id IN ({marks})
                ORDER BY variant.id""",
            ids,
        )
        identity_rows = {int(row["id"]): row for row in cursor.fetchall()}
        cursor.execute(
            f"""SELECT price.id,price.variant_id,price.source_code,
                       price.payload_sha256,price.metric_status,
                       run.id AS run_id,run.run_key,run.source_code AS run_source
                FROM market_price_observation AS price
                JOIN market_ingest_run AS run ON run.id=price.run_id
                WHERE price.variant_id IN ({marks})
                  AND price.source_code IN ('pricecharting','ebay')
                  AND run.source_code='pricecharting'
                ORDER BY price.variant_id,price.id""",
            ids,
        )
        price_by_variant: dict[int, list[dict[str, Any]]] = {}
        for row in cursor.fetchall():
            price_by_variant.setdefault(int(row["variant_id"]), []).append(dict(row))

    rows: list[dict[str, Any]] = []
    for variant_id in ids:
        product, reason = PROVEN_BAD[variant_id]
        candidate = candidates[variant_id][0]
        identity = identity_rows.get(variant_id)
        if (
            identity is None
            or str(identity["external_entity_id"]) != product
            or str(identity["match_status"]) != "exact"
        ):
            raise ValueError(f"current exact PriceCharting ownership drift for variant {variant_id}")
        if str(candidate["pc_product_id"]) != product:
            raise ValueError(f"consolidated map product drift for variant {variant_id}")
        claim = {
            "contract": CONTRACT,
            "action": "quarantine_wrong_pricecharting_binding",
            "variantId": variant_id,
            "pcProductId": product,
            "pcUrl": str(candidate["pc_url"]),
            "reasonCode": reason,
            "priceObservationIds": [
                int(row["id"]) for row in price_by_variant.get(variant_id, [])
            ],
        }
        rows.append(
            {
                "variantId": variant_id,
                "pcProductId": product,
                "pcUrl": str(candidate["pc_url"]),
                "reasonCode": reason,
                "catalog": {
                    "opaqueId": identity["opaque_id"],
                    "name": identity["canonical_name"],
                    "set": identity["set_name"],
                    "collector": identity["collector_number"],
                    "language": identity["card_language"],
                },
                "identityEvidenceSha256": identity["evidence_sha256"],
                "priceRows": price_by_variant.get(variant_id, []),
                "conflictClaim": claim,
                "conflictEvidenceSha256": sha256(claim),
            }
        )
    validate_allowed(rows)
    document = {
        "contract": CONTRACT,
        "readOnly": True,
        "mapPath": str(map_path),
        "mapSha256": hashlib.sha256(map_path.read_bytes()).hexdigest(),
        "rows": rows,
    }
    document["planSha256"] = plan_sha256(document)
    return document


def read_plan(path: Path, required_sha256: str) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    actual = plan_sha256(document)
    if (
        document.get("contract") != CONTRACT
        or document.get("readOnly") is not True
        or document.get("planSha256") != actual
        or required_sha256 != actual
        or not isinstance(document.get("rows"), list)
    ):
        raise ValueError("wrong-binding quarantine plan SHA mismatch")
    validate_allowed(document["rows"])
    for row in document["rows"]:
        claim = row.get("conflictClaim")
        if not isinstance(claim, Mapping) or sha256(claim) != row.get("conflictEvidenceSha256"):
            raise ValueError("wrong-binding conflict evidence drift")
    return document


def apply_plan(connection: Any, document: Mapping[str, Any], *, write: bool) -> dict[str, int]:
    rows = list(document["rows"])
    ids = [int(row["variantId"]) for row in rows]
    products = [str(row["pcProductId"]) for row in rows]
    marks = ",".join(["%s"] * len(ids))
    price_ids = sorted(
        int(price["id"])
        for row in rows
        for price in row.get("priceRows", [])
    )
    price_marks = ",".join(["%s"] * len(price_ids)) if price_ids else "NULL"

    with connection.cursor() as cursor:
        cursor.execute(
            f"""SELECT variant_id,external_entity_id,match_status,evidence_sha256
                FROM catalog_source_identity
                WHERE source_code='pricecharting'
                  AND (variant_id IN ({marks}) OR external_entity_id IN ({marks}))""",
            [*ids, *products],
        )
        identities = list(cursor.fetchall())
        by_variant = {int(row["variant_id"]): row for row in identities}
        if len(identities) != len(rows):
            raise ValueError("PriceCharting ownership ambiguity during quarantine")
        identity_changes = 0
        for planned in rows:
            current = by_variant.get(int(planned["variantId"]))
            if (
                current is None
                or str(current["external_entity_id"]) != str(planned["pcProductId"])
                or str(current["evidence_sha256"]) != str(planned["identityEvidenceSha256"])
                or str(current["match_status"]) not in {"exact", "conflict"}
            ):
                raise ValueError("PriceCharting identity drift during quarantine")
            identity_changes += str(current["match_status"]) == "exact"

        cursor.execute(
            f"""SELECT price.id,price.variant_id,price.source_code,
                       price.payload_sha256,price.metric_status,
                       run.id AS run_id,run.run_key,run.source_code AS run_source
                FROM market_price_observation AS price
                JOIN market_ingest_run AS run ON run.id=price.run_id
                WHERE price.variant_id IN ({marks})
                  AND price.source_code IN ('pricecharting','ebay')
                  AND run.source_code='pricecharting'
                ORDER BY price.variant_id,price.id""",
            ids,
        )
        current_prices = {int(row["id"]): row for row in cursor.fetchall()}
        if set(current_prices) != set(price_ids):
            raise ValueError("PriceCharting materialized price set drift during quarantine")
        planned_prices = {
            int(price["id"]): price
            for row in rows
            for price in row.get("priceRows", [])
        }
        price_changes = 0
        for price_id, current in current_prices.items():
            planned = planned_prices[price_id]
            for key in ("variant_id", "source_code", "payload_sha256", "run_id", "run_key", "run_source"):
                if str(current.get(key)) != str(planned.get(key)):
                    raise ValueError("PriceCharting materialized price evidence drift")
            if str(current["metric_status"]) not in {"ready", "quarantined"}:
                raise ValueError("unexpected PriceCharting price state during quarantine")
            price_changes += str(current["metric_status"]) == "ready"

        evidence_changes = 0
        for planned in rows:
            cursor.execute(
                "SELECT id FROM catalog_identity_evidence WHERE evidence_sha256=%s",
                (planned["conflictEvidenceSha256"],),
            )
            evidence_changes += cursor.fetchone() is None

        if not write:
            connection.rollback()
            return {
                "identityChanges": identity_changes,
                "priceChanges": price_changes,
                "evidenceChanges": evidence_changes,
            }

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cursor.executemany(
            """UPDATE catalog_source_identity
               SET match_status='conflict'
               WHERE source_code='pricecharting' AND variant_id=%s
                 AND external_entity_id=%s AND match_status='exact'""",
            [(int(row["variantId"]), str(row["pcProductId"])) for row in rows],
        )
        if price_ids:
            cursor.execute(
                f"""UPDATE market_price_observation
                    SET metric_status='quarantined'
                    WHERE id IN ({price_marks}) AND metric_status='ready'""",
                price_ids,
            )
        for planned in rows:
            cursor.execute(
                "SELECT id FROM catalog_identity_evidence WHERE evidence_sha256=%s",
                (planned["conflictEvidenceSha256"],),
            )
            if cursor.fetchone() is not None:
                continue
            cursor.execute(
                """INSERT INTO catalog_identity_evidence
                   (variant_id,evidence_kind,source_code,external_entity_id,
                    external_url,match_status,claim_json,evidence_sha256,
                    observed_at,actor)
                   VALUES (%s,'bind','pricecharting',%s,%s,'conflict',%s,%s,%s,%s)""",
                (
                    int(planned["variantId"]),
                    str(planned["pcProductId"]),
                    str(planned["pcUrl"]),
                    json.dumps(planned["conflictClaim"], sort_keys=True),
                    str(planned["conflictEvidenceSha256"]),
                    now,
                    ACTOR,
                ),
            )
    connection.commit()
    return {
        "identityChanges": identity_changes,
        "priceChanges": price_changes,
        "evidenceChanges": evidence_changes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", type=Path, default=MAP_DEFAULT)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    connection = db()
    try:
        if args.plan:
            if not args.plan_sha256:
                parser.error("--plan requires --plan-sha256")
            document = read_plan(args.plan, args.plan_sha256)
        else:
            if args.write:
                parser.error("--write requires an immutable --plan")
            document = build_plan(connection, args.map)
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(
                    json.dumps(document, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8",
                )
        result = apply_plan(connection, document, write=args.write)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    print(
        json.dumps(
            {
                "contract": CONTRACT,
                "write": bool(args.write),
                "planSha256": document["planSha256"],
                "bindings": len(document["rows"]),
                **result,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
