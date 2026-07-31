#!/usr/bin/env python3
"""One-time, fail-closed repair for the Pokemon 151 JP/EN twin-set mix-up.

The manifest below is intentionally finite and auditable.  It is not a general
matcher: every source id was checked against a local GemRate receipt (JP) and
local SNK/PriceCharting material (EN).  Dry-run is the default; ``--write`` is
one transaction and rechecks every owner immediately before it changes it.

It never touches images.  It preserves the old source evidence as ``conflict``
and records the rebind on the English sibling as ``exact``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from card_identity import opaque_id_from_row, printing_identity_sha256_from_row  # noqa: E402
from db_runtime import add_connection_args, connection_from_args  # noqa: E402

try:
    from qualified_pool_operator import load_env  # noqa: E402
    load_env()
except Exception:
    pass


REPORT_DEFAULT = ROOT / "data" / "runtime" / "private-reports" / "pokemon151-twin-language-repair.json"
SNK_LOCAL = ROOT / "data" / "private" / "snkrdunk_brute" / "snkrdunk_all.jsonl"
PC_MAP_GLOB = "data/runtime/private-source-map/c11_pc_ebay_map_full900_shard_*.jsonl"
SNK_SALE_TRANSPORT_SOURCES = ("snkrdunk", "ebay")
SNK_PRICE_SOURCES = ("snkrdunk", "snk", "snk_psa10")

# ``jpSnk`` is a physical JP item currently attached to the EN sibling;
# ``enSnk`` is a physical EN item currently attached to the JP variant.
# This is a two-way repair, not an alternate-identity attachment.
TWIN_MANIFEST: tuple[dict[str, Any], ...] = (
    {"jp":260,"en":661,"gemrateId":"de087a4b10f586f34f65f2081a6110ca7a57d6b7","jpSnk":"128084","enSnk":"845606","pc":"5809549"},
    {"jp":991,"en":649,"gemrateId":"4c74d3fd5e1bd7a380d041e6c8b7ab7b46807a88","jpSnk":"128093","enSnk":"845615","pc":"5809558"},
    {"jp":1015,"en":666,"gemrateId":"4456d29a0f2e857b57b9881bd919437fad0f3257","jpSnk":"128112","enSnk":"486157","pc":"5809577","keepPc":"5326229"},
    {"jp":1024,"en":671,"gemrateId":"6507707512ecd84421f9e8589ad4626ebaca367e","jpSnk":"128086","enSnk":"845608","pc":"5809551","pcIdentityMoves":True},
    {"jp":1047,"en":659,"gemrateId":"4f05c9a410542dbc66b4e5257ab4c5d54694ef49","jpSnk":"128092","enSnk":"845614","pc":"5809557"},
    {"jp":1051,"en":654,"gemrateId":"fd50820d43a9320a50e4d374bd00591ab4bd66c8","jpSnk":"128082","enSnk":"845604","pc":"5809547"},
    {"jp":1469,"en":1187,"gemrateId":"7035bd649ab180df1b855705c6489192cd9bc351","jpSnk":"128095","enSnk":"845617","pc":"5809560"},
    {"jp":1470,"en":1432,"gemrateId":"444ad723f9fa88124e1dfb21ebf65bb734dec46d","jpSnk":"128087","enSnk":"845609","pc":"5809552"},
    {"jp":1471,"en":1434,"gemrateId":"de6409f775684d7f66fdd668298958bf927aa0e6","jpSnk":"128091","enSnk":"845613","pc":"5809556"},
    {"jp":1472,"en":1435,"gemrateId":"af6698b896e2a28669a99f7f426ca387996057c9","jpSnk":"128085","enSnk":"845607","pc":"5809550","pcIdentityMoves":True},
    {"jp":1473,"en":1186,"gemrateId":"30be4c7b94ac6814b6bdde3b9b735a9b91acafe5","jpSnk":"128096","enSnk":"845618","pc":"5809561"},
    {"jp":1474,"en":1188,"gemrateId":"e6a841d37aa7e166edf93e619289fbeba98109ec","jpSnk":"128094","enSnk":"845616","pc":"5809559"},
    {"jp":1475,"en":1433,"gemrateId":"62de271054b85188b5bc9dfbe1db5610ec2c49bc","jpSnk":"128083","enSnk":"845605","pc":"5809548"},
    {"jp":1477,"en":1431,"gemrateId":"a2df7d5ec972ffd2dd16fd0931760ac1493ff5df","jpSnk":"128090","enSnk":"845612","pc":"5809555"},
    {"jp":1478,"en":1189,"gemrateId":"2467ad9c241012e470811758fd42fa86245f7a58","jpSnk":"128088","enSnk":"845610","pc":"5809553"},
)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def manifest_errors(manifest: Sequence[Mapping[str, Any]] = TWIN_MANIFEST) -> list[str]:
    errors: list[str] = []
    jps = [int(r["jp"]) for r in manifest]
    ens = [int(r["en"]) for r in manifest]
    pcs = [str(r["pc"]) for r in manifest]
    jp_snks = [str(r["jpSnk"]) for r in manifest]
    en_snks = [str(r["enSnk"]) for r in manifest]
    if len(manifest) != 15 or len(set(jps)) != 15 or len(set(ens)) != 15:
        errors.append("manifest must contain 15 unique JP and EN variants")
    if len(set(pcs)) != 15:
        errors.append("manifest must contain 15 unique EN PriceCharting product ids")
    if len(set(jp_snks)) != 15 or len(set(en_snks)) != 15 or set(jp_snks) & set(en_snks):
        errors.append("manifest must contain 15 unique JP and 15 unique EN SNK ids")
    if sum(bool(r.get("pcIdentityMoves")) for r in manifest) != 2:
        errors.append("manifest must identify exactly two wrong PC identities")
    return errors


def load_local_snk_titles(path: Path = SNK_LOCAL) -> dict[str, str]:
    if not path.is_file():
        raise RuntimeError(f"missing local SNK receipt: {path}")
    found: dict[str, str] = {}
    needed = {str(r[key]) for r in TWIN_MANIFEST for key in ("jpSnk", "enSnk")}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        item_id = str(row.get("id") or row.get("item_id") or row.get("itemId") or "")
        if item_id not in needed:
            continue
        found[item_id] = json.dumps(row, ensure_ascii=False, sort_keys=True)
    return found


def is_explicit_en_snk(raw: str) -> bool:
    folded = raw.casefold()
    return "pkmn-tcg-en-mew-en-" in folded or "mew en" in folded


def is_explicit_ja_snk(raw: str) -> bool:
    return "pkmn-tcg-sv2a-" in raw.casefold()


def load_local_pc_products() -> dict[str, list[dict[str, Any]]]:
    products: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(ROOT.glob(PC_MAP_GLOB)):
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            product = str(row.get("pc_product_id") or "")
            if product:
                products.setdefault(product, []).append(row)
    return products


def is_explicit_en_pc(rows: Iterable[Mapping[str, Any]]) -> bool:
    for row in rows:
        url = str(row.get("pc_url") or "").casefold()
        set_name = str(row.get("set_name") or "").casefold()
        if "pokemon-japanese" not in url and ("scarlet" in url or "151 mew" in set_name):
            return True
    return False


def _rows_by_id(cursor: Any, table: str, ids: Sequence[int], *, key: str = "id") -> dict[int, dict[str, Any]]:
    marks = ",".join(["%s"] * len(ids))
    cursor.execute(f"SELECT * FROM {table} WHERE {key} IN ({marks})", tuple(ids))
    return {int(row[key]): dict(row) for row in cursor.fetchall()}


def build_plan(cursor: Any) -> dict[str, Any]:
    errors = manifest_errors()
    local_snk = load_local_snk_titles()
    local_pc = load_local_pc_products()
    jp_ids = [int(r["jp"]) for r in TWIN_MANIFEST]
    en_ids = [int(r["en"]) for r in TWIN_MANIFEST]
    variants = _rows_by_id(cursor, "catalog_variant", jp_ids + en_ids)
    printing = _rows_by_id(cursor, "catalog_printing_identity", jp_ids, key="variant_id")
    plan: dict[str, Any] = {"manifestSha256": sha256_json(TWIN_MANIFEST), "entries": [], "blockers": errors, "counts": {}}
    identity_moves: list[dict[str, Any]] = []
    sale_moves: list[dict[str, Any]] = []
    price_moves: list[dict[str, Any]] = []

    for spec in TWIN_MANIFEST:
        jp, en = int(spec["jp"]), int(spec["en"])
        jrow, erow = variants.get(jp), variants.get(en)
        entry: dict[str, Any] = {"jpVariantId": jp, "enVariantId": en, "jpSnk": spec["jpSnk"], "enSnk": spec["enSnk"], "pc": spec["pc"], "blockers": []}
        if not jrow or not erow:
            entry["blockers"].append("catalog_variant_missing")
        else:
            if str(jrow.get("tcg_code")) != "pokemon" or "151" not in str(jrow.get("set_name") or ""):
                entry["blockers"].append("jp_variant_not_pokemon_151")
            if str(jrow.get("card_language") or "") != "en":
                entry["blockers"].append("jp_variant_expected_current_en")
            if str(erow.get("tcg_code")) != "pokemon" or str(erow.get("card_language") or "") != "en" or "151" not in str(erow.get("set_name") or ""):
                entry["blockers"].append("en_sibling_fields_not_expected")
            if (jrow["canonical_name"], str(jrow["collector_number"]).replace("/165", "")) != (erow["canonical_name"], str(erow["collector_number"]).replace("/165", "")):
                entry["blockers"].append("twin_name_or_collector_mismatch")
            cursor.execute("SELECT COUNT(*) AS n FROM catalog_variant WHERE tcg_code='pokemon' AND card_language='en' AND set_name=%s AND canonical_name=%s AND collector_number=%s", (erow["set_name"], erow["canonical_name"], erow["collector_number"]))
            if int(cursor.fetchone()["n"]) != 1:
                entry["blockers"].append("en_sibling_not_unique")
        if jp not in printing:
            entry["blockers"].append("printing_identity_missing")
        # GemRate exact record is the durable local-receipt anchor; this job never
        # uses image URLs to decide language.
        cursor.execute("SELECT variant_id, match_status FROM catalog_source_identity WHERE source_code='gemrate' AND external_entity_id=%s", (spec["gemrateId"],))
        gem = cursor.fetchone()
        if not gem or int(gem["variant_id"]) != jp or str(gem["match_status"]) != "exact":
            entry["blockers"].append("gemrate_japanese_receipt_drift")

        pc_rows = local_pc.get(str(spec["pc"]), [])
        if str(spec["pc"]) != "5809560" and not is_explicit_en_pc(pc_rows):
            entry["blockers"].append("pc_local_english_receipt_missing")
        # 5809560's saved full900 HTML is the receipt; it was omitted from the
        # old JSONL map but is still a verified EN product, so require its file.
        if str(spec["pc"]) == "5809560" and not list((ROOT / "data/private/pricecharting_session/html/full900").glob("1187_mr-mime-179*.html")):
            entry["blockers"].append("pc_local_english_html_missing")

        cursor.execute("SELECT variant_id, match_status FROM catalog_source_identity WHERE source_code='pricecharting' AND external_entity_id=%s", (str(spec["pc"]),))
        pc_identity = cursor.fetchone()
        if pc_identity and int(pc_identity["variant_id"]) not in {jp, en}:
            entry["blockers"].append("pc_identity_owner_drift")
        if spec.get("pcIdentityMoves") and (not pc_identity or int(pc_identity["variant_id"]) != jp or str(pc_identity["match_status"]) != "exact"):
            entry["blockers"].append("expected_wrong_pc_identity_missing")
        if pc_identity and int(pc_identity["variant_id"]) == jp:
            identity_moves.append({"sourceCode": "pricecharting", "externalEntityId": str(spec["pc"]), "from": jp, "to": en})

        # A complete two-way SNK swap.  The 128xxx product is Japanese but
        # currently owned by EN; the 845xxx/486157 product is English but
        # currently owned by JP.  Neither is an alternate identity.
        for external, expected_from, target, predicate, tag in (
            (str(spec["jpSnk"]), en, jp, is_explicit_ja_snk, "jp"),
            (str(spec["enSnk"]), jp, en, is_explicit_en_snk, "en"),
        ):
            raw = local_snk.get(external)
            if not raw or not predicate(raw):
                entry["blockers"].append(f"snk_local_{tag}_receipt_missing")
            cursor.execute("SELECT variant_id, match_status FROM catalog_source_identity WHERE source_code='snkrdunk' AND external_entity_id=%s", (external,))
            snk_identity = cursor.fetchone()
            if not snk_identity or int(snk_identity["variant_id"]) != expected_from or str(snk_identity["match_status"]) != "exact":
                entry["blockers"].append(f"snk_{tag}_identity_owner_drift")
            else:
                identity_moves.append({"sourceCode": "snkrdunk", "externalEntityId": external, "from": expected_from, "to": target})
        # 1015's JP PriceCharting product must remain attached to JP.
        if spec.get("keepPc"):
            cursor.execute("SELECT variant_id, match_status FROM catalog_source_identity WHERE source_code='pricecharting' AND external_entity_id=%s", (spec["keepPc"],))
            keep = cursor.fetchone()
            if not keep or int(keep["variant_id"]) != jp or str(keep["match_status"]) != "exact":
                entry["blockers"].append("jp_keep_pc_drift")

        # Sale rows carry their source product id and are safe to move exactly.
        cursor.execute("SELECT id FROM market_sale_observation WHERE source_code='ebay' AND external_entity_id=%s AND variant_id=%s", (f"pc:{spec['pc']}", jp))
        sale_moves.extend({"id": int(row["id"]), "from": jp, "to": en, "source": f"pc:{spec['pc']}"} for row in cursor.fetchall())
        for external, expected_from, target in ((str(spec["jpSnk"]), en, jp), (str(spec["enSnk"]), jp, en)):
            cursor.execute("SELECT id FROM market_sale_observation WHERE source_code IN (%s,%s) AND external_entity_id=%s AND variant_id=%s", (*SNK_SALE_TRANSPORT_SOURCES, external, expected_from))
            sale_moves.extend({"id": int(row["id"]), "from": expected_from, "to": target, "source": external} for row in cursor.fetchall())

        # Price rows have no external id.  Only the SNK source family has a
        # one-to-one binding on both sides of this audited pair, so swap it.
        # PC/eBay prices stay unresolved and must be rematerialized from their
        # product lineage; this job never guesses from a daily price row.
        source_codes = list(SNK_PRICE_SOURCES)
        marks = ",".join(["%s"] * len(source_codes))
        for expected_from, target in ((jp, en), (en, jp)):
            cursor.execute(f"SELECT id, source_code, observed_date FROM market_price_observation WHERE variant_id=%s AND source_code IN ({marks})", tuple([expected_from] + source_codes))
            price_moves.extend({"id": int(price["id"]), "from": expected_from, "to": target, "sourceCode": str(price["source_code"]), "observedDate": str(price["observed_date"])} for price in cursor.fetchall())
        plan["entries"].append(entry)
        plan["blockers"].extend(f"{jp}:{item}" for item in entry["blockers"])

    # The final unique key is (variant, source, observed_date).  The moves are
    # intentionally staged through two temporary source codes on write, but a
    # duplicate final key would still mean two different source rows claim one
    # canonical daily price, so stop instead of silently choosing one.
    moving_price_ids = {int(move["id"]) for move in price_moves}
    final_price_keys: Counter[tuple[int, str, str]] = Counter(
        (int(move["to"]), str(move["sourceCode"]), str(move["observedDate"]))
        for move in price_moves
    )
    for key, count in final_price_keys.items():
        if count > 1:
            plan["blockers"].append(f"price_final_key_collision:{key}")
    for move in price_moves:
        cursor.execute("SELECT id FROM market_price_observation WHERE variant_id=%s AND source_code=%s AND observed_date=%s", (move["to"], move["sourceCode"], move["observedDate"]))
        row = cursor.fetchone()
        if row and int(row["id"]) not in moving_price_ids:
            plan["blockers"].append(f"price_target_nonmoving_collision:{move['id']}")
    plan["identityMoves"] = identity_moves
    plan["saleMoves"] = sale_moves
    plan["priceMoves"] = price_moves
    plan["pcPriceRematerializeNeeded"] = [
        {"jpVariantId": int(spec["jp"]), "enVariantId": int(spec["en"]), "pcProductId": str(spec["pc"])}
        for spec in TWIN_MANIFEST if spec.get("pcIdentityMoves")
    ]
    plan["languageUpdates"] = jp_ids
    plan["counts"] = {"entries": len(TWIN_MANIFEST), "identityMoves": len(identity_moves), "saleMoves": len(sale_moves), "priceMoves": len(price_moves), "blockers": len(plan["blockers"])}
    return plan


def write_plan(cursor: Any, plan: Mapping[str, Any]) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    by_jp = {int(row["jp"]): row for row in TWIN_MANIFEST}
    for move in plan["identityMoves"]:
        cursor.execute("UPDATE catalog_source_identity SET variant_id=%s WHERE source_code=%s AND external_entity_id=%s AND variant_id=%s", (move["to"], move["sourceCode"], move["externalEntityId"], move["from"]))
        if cursor.rowcount != 1:
            raise RuntimeError(f"identity write precondition failed: {move}")
        digest = sha256_json({"pipeline": "pokemon151_twin_language_repair", "move": move})
        claim = json.dumps({"reason": "wrong_language_twin_set", "movedToVariantId": move["to"]}, ensure_ascii=False, sort_keys=True)
        cursor.execute("INSERT IGNORE INTO catalog_identity_evidence (variant_id,evidence_kind,source_code,external_entity_id,match_status,claim_json,evidence_sha256,observed_at,actor) VALUES (%s,'bind',%s,%s,'conflict',%s,%s,%s,'pokemon151_twin_language_repair')", (move["from"], move["sourceCode"], move["externalEntityId"], claim, digest, now))
        cursor.execute("INSERT IGNORE INTO catalog_identity_evidence (variant_id,evidence_kind,source_code,external_entity_id,match_status,claim_json,evidence_sha256,observed_at,actor) VALUES (%s,'bind',%s,%s,'exact',%s,%s,%s,'pokemon151_twin_language_repair')", (move["to"], move["sourceCode"], move["externalEntityId"], claim, digest, now))
    for move in plan["saleMoves"]:
        cursor.execute("UPDATE market_sale_observation SET variant_id=%s WHERE id=%s AND variant_id=%s", (move["to"], move["id"], move["from"]))
        if cursor.rowcount != 1:
            raise RuntimeError(f"sale write precondition failed: {move}")
    # Two temporary source codes make a same-day JP<->EN SNK price swap safe
    # under uq_market_price_daily(variant_id, source_code, observed_date).
    temp_prefix = "repair_tmp_snk_to_"
    for move in plan["priceMoves"]:
        temp_source = f"{temp_prefix}{int(move['to'])}"
        cursor.execute("UPDATE market_price_observation SET source_code=%s WHERE id=%s AND variant_id=%s AND source_code=%s", (temp_source, move["id"], move["from"], move["sourceCode"]))
        if cursor.rowcount != 1:
            raise RuntimeError(f"price temporary-source precondition failed: {move}")
    for move in plan["priceMoves"]:
        temp_source = f"{temp_prefix}{int(move['to'])}"
        cursor.execute("UPDATE market_price_observation SET variant_id=%s WHERE id=%s AND variant_id=%s AND source_code=%s", (move["to"], move["id"], move["from"], temp_source))
        if cursor.rowcount != 1:
            raise RuntimeError(f"price write precondition failed: {move}")
    for move in plan["priceMoves"]:
        temp_source = f"{temp_prefix}{int(move['to'])}"
        cursor.execute("UPDATE market_price_observation SET source_code=%s WHERE id=%s AND variant_id=%s AND source_code=%s", (move["sourceCode"], move["id"], move["to"], temp_source))
        if cursor.rowcount != 1:
            raise RuntimeError(f"price restore-source precondition failed: {move}")
    for jp, spec in by_jp.items():
        cursor.execute("SELECT * FROM catalog_variant WHERE id=%s FOR UPDATE", (jp,))
        variant = dict(cursor.fetchone())
        variant["card_language"] = "ja"
        opaque = opaque_id_from_row(variant)
        cursor.execute("SELECT id FROM catalog_variant WHERE opaque_id=%s AND id<>%s FOR UPDATE", (opaque, jp))
        if cursor.fetchone():
            raise RuntimeError(f"opaque_id collision for JP variant {jp}")
        cursor.execute("UPDATE catalog_variant SET card_language='ja', opaque_id=%s WHERE id=%s AND card_language='en'", (opaque, jp))
        if cursor.rowcount != 1:
            raise RuntimeError(f"language write precondition failed: {jp}")
        cursor.execute("SELECT * FROM catalog_printing_identity WHERE variant_id=%s FOR UPDATE", (jp,))
        printing = dict(cursor.fetchone())
        printing["card_language"] = "ja"
        digest = printing_identity_sha256_from_row(printing)
        cursor.execute("SELECT variant_id FROM catalog_printing_identity WHERE canonical_printing_sha256=%s AND variant_id<>%s FOR UPDATE", (digest, jp))
        if cursor.fetchone():
            raise RuntimeError(f"printing hash collision for JP variant {jp}")
        cursor.execute("UPDATE catalog_printing_identity SET card_language='ja', canonical_printing_sha256=%s WHERE variant_id=%s", (digest, jp))
        if cursor.rowcount != 1:
            raise RuntimeError(f"printing hash write precondition failed: {jp}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_connection_args(parser)
    parser.add_argument("--write", action="store_true", help="apply the finite repair in one transaction")
    parser.add_argument("--report-out", type=Path, default=REPORT_DEFAULT)
    args = parser.parse_args(argv)
    connection = connection_from_args(args)
    try:
        with connection.cursor() as cursor:
            plan = build_plan(cursor)
            plan["mode"] = "write" if args.write else "dry-run"
            plan["generatedAt"] = datetime.now(timezone.utc).isoformat()
            plan["receiptSha256"] = sha256_json(plan)
            if plan["blockers"]:
                connection.rollback()
                status = 2
            elif not args.write:
                connection.rollback()
                status = 0
            else:
                write_plan(cursor, plan)
                connection.commit()
                status = 0
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"mode": plan["mode"], "counts": plan["counts"], "blockers": plan["blockers"], "receiptSha256": plan["receiptSha256"], "report": str(args.report_out)}, ensure_ascii=False, sort_keys=True))
        return status
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
