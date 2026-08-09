#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Propose SNKRDUNK bindings for qualified variants that have no price source.

S7 (`stage_snk_refresh`) re-derives bindings that already exist and says so in
its own docstring: "New-variant SNK discovery is not this stage — unbound
variants stay market_pending, honestly." Nothing else in the rebuild proposes a
first candidate, so a card whose SNK item was never found sits at
qualified_market_pending forever no matter how many generations run. That is
the whole reason 134 One Piece cards with PSA10 population >= 1000 were absent
from the front end while every collector already knew them by name.

This module closes that hole and nothing else:

  find    SNKRDUNK's /search is server-rendered, so a plain GET on the
          provider's own product number ("OP01-016") returns candidate item
          ids without a browser.
  harvest each candidate's master record through the normal client.
  rule    the SAME acceptance comparison S7 and the reverify command use, via
          the shared helpers in rebuild_036 -- this file owns no private
          notion of what counts as a match.
  bind    only when exactly ONE candidate survives. A product number is not
          unique on SNKRDUNK: OP01-016 alone returns twelve different products
          (booster, premium collection, 25th anniversary, EN, CHN...), so
          "several survived" is an ambiguity to report, never a coin flip.

Everything else is held with a reason. Writes are off unless --write is passed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests

import rebuild_036 as R
import snk_market_data
# The One Piece vocabulary and the product-agreement rule are shared with the
# PriceCharting lane -- both providers reprint a card under its original
# number, so both need the same proof.
from op_identity_rules import (
    GEMRATE_TREATMENT,
    RARITY_TOKEN_RE,
    SET_CODE_RE,
    SNK_SUFFIX_TREATMENT,
    gemrate_treatment,
    product_agrees,
    snk_treatment,
)
from snkrdunk_bulk import UA


def progress(message: str) -> None:
    """Say what is happening while it happens.

    The report is a single JSON blob at the end, so without this a run that
    searches a hundred cards looks identical to a run that hung on the first
    one. stderr keeps stdout parseable.
    """

    print(message, file=sys.stderr, flush=True)


ITEM_RE = re.compile(r"/apparels/(\d+)")
SEARCH_URL = "https://snkrdunk.com/search?keywords={}&page=1"


def product_number(set_name: str, collector_number: str, set_code: str = "") -> str:
    """The provider's own key for a card, rebuilt from what the catalog holds.

    Most One Piece rows carry an empty set_code and spell the set only in
    set_name, so the code is recovered from there rather than trusted blindly.
    """

    code = str(set_code or "").strip().upper()
    if not code:
        match = SET_CODE_RE.search(str(set_name or "").upper())
        code = match.group(1) if match else ""
    number = str(collector_number or "").strip()
    if not code or not number:
        return ""
    return f"{code}-{number}"


def search_queries(row: dict[str, Any]) -> list[str]:
    """What to ask SNKRDUNK for this card, best key first.

    The character name plus the printed number is the reliable key: One Piece
    reprints keep their ORIGINAL set number while GemRate names the product
    they were sold in, so "<set from set_name>-<number>" misses every reprint.
    The set-prefixed form is still asked second because it is precise when the
    card really did come from that set.
    """

    queries: list[str] = []
    name = str(row.get("fp_name") or "").strip()
    number = str(row.get("fp_number") or row.get("collector_number") or "").strip()
    if name and number:
        queries.append(f"{name} {number}")
    prefixed = product_number(row.get("set_name") or "", number, row.get("v_set_code") or "")
    if prefixed:
        queries.append(prefixed)
    return queries


def latest_generation(conn: Any) -> str:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT generation_id FROM catalog_rebuild_member"
            " ORDER BY computed_at DESC LIMIT 1"
        )
        row = cursor.fetchone()
    if not row:
        raise SystemExit("no catalog_rebuild_member rows: nothing to discover against")
    return str(row["generation_id"])


def select_targets(
    conn: Any, generation: str, min_pop: int, tcg: str, limit: int,
) -> list[dict[str, Any]]:
    """Qualified, bound-to-a-variant, non-English, and with no price source.

    English cards route to PriceCharting (D4 language primacy), so they are not
    this lane's business even when SNKRDUNK happens to list them.
    """

    sql = """
        SELECT rm.variant_id, rm.latest_psa10_population AS pop,
               JSON_UNQUOTE(JSON_EXTRACT(rm.detail_json, '$.fingerprint.name')) AS fp_name,
               JSON_UNQUOTE(JSON_EXTRACT(rm.detail_json, '$.fingerprint.cardNumber')) AS fp_number,
               JSON_UNQUOTE(JSON_EXTRACT(rm.detail_json, '$.fingerprint.parallel')) AS fp_parallel,
               v.tcg_code, v.card_language, v.set_name, v.collector_number,
               v.canonical_name,
               v.set_code AS v_set_code, v.printing_code AS v_printing_code,
               p.parallel_code, p.printing_code, p.canonical_printing_sha256,
               p.tcg_code AS p_tcg_code, p.card_language AS p_card_language,
               p.set_code AS p_set_code, p.collector_number AS p_collector_number,
               p.edition_code AS p_edition_code, p.finish_code AS p_finish_code
          FROM catalog_rebuild_member rm
          JOIN catalog_variant v ON v.id = rm.variant_id
          LEFT JOIN catalog_printing_identity p ON p.variant_id = v.id
         WHERE rm.generation_id = %s
           AND rm.cohort <> 'non_qualified'
           AND rm.latest_psa10_population >= %s
           AND v.card_language <> 'en'
           AND NOT EXISTS (
                 SELECT 1 FROM catalog_source_identity si
                  WHERE si.variant_id = v.id
                    AND si.source_code IN ('snkrdunk', 'snk_psa10', 'pricecharting')
                    AND si.match_status = 'exact'
               )
    """
    params: list[Any] = [generation, min_pop]
    if tcg:
        sql += " AND v.tcg_code = %s"
        params.append(tcg)
    sql += " ORDER BY rm.latest_psa10_population DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with conn.cursor() as cursor:
        cursor.execute(sql, tuple(params))
        return [dict(row) for row in cursor.fetchall()]


def existing_owners(conn: Any, item_ids: list[int]) -> dict[str, dict[str, Any]]:
    """Who already holds each candidate item, and how firmly.

    catalog_source_identity is keyed (source_code, external_entity_id), so one
    SNKRDUNK item belongs to exactly one variant and a candidate cannot simply
    be copied onto a second card. But the status on that row decides what the
    row MEANS. An 'exact' row is a proven binding and is untouchable. A
    'manual_review' or 'rejected' row is an unproven proposal, and treating it
    as ownership is how the Japanese listing for OP01-016 Nami ended up parked
    on the English card while the Japanese card -- PSA10 population 8,414 --
    stayed off the front end with no candidate at all.
    """

    if not item_ids:
        return {}
    marks = ",".join(["%s"] * len(item_ids))
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT external_entity_id, variant_id, match_status"
            " FROM catalog_source_identity"
            f" WHERE source_code='snkrdunk' AND external_entity_id IN ({marks})",
            tuple(str(i) for i in item_ids),
        )
        return {
            str(row["external_entity_id"]): {
                "variant_id": int(row["variant_id"]),
                "match_status": str(row["match_status"]),
            }
            for row in cursor.fetchall()
        }


def search_item_ids(session: requests.Session, query: str, cap: int) -> list[int]:
    try:
        response = session.get(SEARCH_URL.format(urllib.parse.quote(query)), timeout=25)
    except requests.RequestException:
        return []
    if response.status_code != 200:
        return []
    return [int(x) for x in dict.fromkeys(ITEM_RE.findall(response.text))][:cap]


def rule_candidate(row: dict[str, Any], payload: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    """Does this provider master describe this variant? Shared contract only.

    Returns (accepted, reason, facts). Every rejection reason is the same one
    the reverify command would print for an existing binding.
    """

    if payload is None or payload.get("error"):
        return False, f"page_missing:{(payload or {}).get('error') or ''}", {}
    if not payload.get("quantity_variant_id"):
        return False, "no_psa10_1card_variant", {}
    master = (payload.get("source_payload") or {}).get("master") or {}
    master_name = str(master.get("name") or "")
    localized = str(master.get("localizedName") or "")
    number = str(payload.get("product_number") or "").strip()
    language = R._snk_language(master_name, localized)
    tcg = R._snk_tcg(master_name, localized)
    claim = R._snk_collector_claim(master_name, localized, number)
    pseudo_fp = {
        "cardNumber": R._snk_claim_number(claim),
        "derivedLanguage": language,
        "setName": f"{master_name} {localized}",
    }
    conflicts = R._fingerprint_variant_conflicts(pseudo_fp, row)
    claim_set = " ".join(claim.split()[:-1])
    if claim_set and claim_set.casefold() in {
        str(row["v_set_code"] or "").casefold(),
        str(row["p_set_code"] or "").casefold(),
    }:
        conflicts = [
            c for c in conflicts
            if not c.startswith("set:") and not c.startswith("set_code:")
        ]
    if tcg and str(row["tcg_code"] or "") and tcg != str(row["tcg_code"]):
        conflicts.append(f"tcg:{tcg}!={row['tcg_code']}")
    if not claim:
        conflicts.append("product_number_missing")
    snk_mirror = R._snk_treatment_mirror(master_name, localized)
    variant_blob = " ".join((
        str(row["parallel_code"] or ""),
        str(row["printing_code"] or ""),
        str(row["v_printing_code"] or ""),
    ))
    variant_mirror = "ミラー" in variant_blob or bool(re.search(r"(?i)mirror", variant_blob))
    if snk_mirror != variant_mirror:
        conflicts.append(f"parallel:mirror {snk_mirror}!={variant_mirror}")
    facts = {
        "master": master, "masterName": master_name, "localized": localized,
        "productNumber": number, "claim": claim, "language": language, "tcg": tcg,
        "quantityVariantId": payload.get("quantity_variant_id"),
        "imageUrl": payload.get("image_url"), "fetchedAt": payload.get("fetched_at"),
        "conditionFilter": payload.get("condition_filter"),
    }
    if conflicts:
        return False, "hard_conflict:" + ";".join(conflicts), facts

    same_product, why = product_agrees(
        row.get("set_name") or "", row.get("fp_parallel") or "", master_name, localized,
    )
    if not same_product:
        return False, why, facts

    # Treatment is compared on the shared vocabulary, not on the word
    # "parallel": SNKRDUNK spells it as a rarity suffix (R-P, SEC-SPC, SR-TR)
    # and GemRate spells it in words ("Alternate Art"). Either side saying
    # something this table cannot name is a hold, never a pass.
    ours = gemrate_treatment(row.get("fp_parallel") or "")
    theirs = snk_treatment(master_name, localized)
    if not ours:
        return False, f"treatment_unmapped_ours:{row.get('fp_parallel') or ''}", facts
    if not theirs:
        return False, f"treatment_unmapped_snk:{master_name[:60]}", facts
    if ours != theirs:
        return False, f"treatment_mismatch:snk={theirs} ours={ours}", facts
    facts["snkParallel"] = theirs
    return True, "", facts


def build_evidence(item_id: int, facts: dict[str, Any], items_dir: Path) -> tuple[dict[str, Any], str]:
    """Same evidence shape the reverify command writes, so the strict view and
    the 031/036 contract see one kind of SNK provider-page evidence, not two."""

    document = {
        "itemId": item_id,
        "master": facts["master"],
        "conditionFilter": facts["conditionFilter"],
        "quantityVariantId": facts["quantityVariantId"],
        "productNumber": facts["productNumber"],
        "imageUrl": facts["imageUrl"],
        "fetchedAt": facts["fetchedAt"],
    }
    blob = R.canonical_json(document)
    item_path = items_dir / f"{item_id}.json"
    item_path.write_bytes(blob)
    evidence = {
        "providerClaims": {
            "tcgCode": facts["tcg"],
            "cardLanguage": facts["language"],
            "collectorNumber": facts["claim"],
            "setCode": "",
            "printingCode": "",
            "parallelCode": facts.get("snkParallel", ""),
        },
        "evidence": {
            "type": R.EVIDENCE_TYPE_PROVIDER_PAGE,
            "sha256": R.sha256_bytes(blob),
            "path": item_path.relative_to(R.ROOT).as_posix(),
            "canonicalUrl": f"https://snkrdunk.com/en/trading-cards/{item_id}",
            "capturedAt": str(facts["fetchedAt"] or ""),
            "generation": "snk_discover",
        },
    }
    return evidence, R.sha256_bytes(R.canonical_json(evidence))


def mirror_tuple(row: dict[str, Any]) -> tuple[str, ...]:
    if row["canonical_printing_sha256"]:
        return (
            str(row["p_tcg_code"] or ""), str(row["p_card_language"] or ""),
            str(row["p_set_code"] or ""), str(row["p_collector_number"] or ""),
            str(row["printing_code"] or ""), str(row["parallel_code"] or ""),
            str(row["p_edition_code"] or ""), str(row["p_finish_code"] or ""),
        )
    return (
        str(row["tcg_code"] or ""), str(row["card_language"] or ""),
        str(row["v_set_code"] or ""), str(row["collector_number"] or ""),
        str(row["v_printing_code"] or ""), "", "", "",
    )


def cmd_snk_identity_discover(args: argparse.Namespace) -> int:
    credentials = getattr(args, "credentials_env", None) or R.DAILY_CREDENTIALS_ENV
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base_dir = R.ROOT / "data" / "runtime" / "rebuild-036" / "snk-identity-discover" / stamp
    items_dir = base_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    parser_version = "snkmd_" + R.sha256_file(R.ROOT / "pipelines" / "snk_market_data.py")[:12]

    conn = R.connect(credentials)
    counts = {
        "targets": 0, "noProductNumber": 0, "searchEmpty": 0, "candidates": 0,
        "candidatesTaken": 0, "candidatesContested": 0,
        "accepted": 0, "ambiguous": 0, "noSurvivor": 0,
        "bound": 0, "repointProposed": 0, "repointed": 0, "repointSkipped": 0,
    }
    proposals: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    try:
        generation = args.generation or latest_generation(conn)
        targets = select_targets(conn, generation, args.min_pop, args.tcg, args.limit)
        counts["targets"] = len(targets)
        if not targets:
            print(json.dumps({"snkIdentityDiscover": True, "targets": 0}, ensure_ascii=False))
            return 0

        progress(f"[discover] generation={generation} targets={len(targets)}"
                 f" write={bool(args.write)}")
        session = requests.Session()
        session.headers["User-Agent"] = UA
        per_target: dict[int, tuple[str, list[int]]] = {}
        every_id: set[int] = set()
        for index, row in enumerate(targets, 1):
            vid = int(row["variant_id"])
            queries = search_queries(row)
            if not queries:
                counts["noProductNumber"] += 1
                held.append({"variant_id": vid, "reason": "no_search_key",
                             "detail": str(row["set_name"] or "")})
                continue
            ids: list[int] = []
            for query in queries:
                for item_id in search_item_ids(session, query, args.per_card):
                    if item_id not in ids:
                        ids.append(item_id)
                time.sleep(args.delay)
            ids = ids[: args.per_card]
            progress(f"[search {index}/{len(targets)}] v{vid} pop={row['pop']}"
                     f" hits={len(ids)} :: {queries[0][:60]}")
            if not ids:
                counts["searchEmpty"] += 1
                held.append({"variant_id": vid, "reason": "search_empty",
                             "detail": " | ".join(queries)})
                continue
            per_target[vid] = (" | ".join(queries), ids)
            every_id.update(ids)
        counts["candidates"] = len(every_id)

        owners = existing_owners(conn, sorted(every_id))
        proven = {iid for iid, own in owners.items() if own["match_status"] == "exact"}
        contested = {iid: own for iid, own in owners.items() if iid not in proven}
        counts["candidatesTaken"] = len(proven)
        counts["candidatesContested"] = len(contested)
        progress(f"[harvest] candidates={len(every_id)} proven_elsewhere={len(proven)}"
                 f" contested={len(contested)} to_fetch={len(every_id) - len(proven)}")

        harvest_path = base_dir / "snk_discover_harvest.jsonl"
        worklist = sorted(i for i in every_id if str(i) not in proven)
        if worklist:
            snk_market_data.run(
                worklist, harvest_path, delay=0.0,
                condition_code=snk_market_data.PSA10_CONDITION,
                run_id=f"snk_discover_{stamp}", workers=args.workers,
            )
        if not harvest_path.is_file():
            partial = harvest_path.with_suffix(harvest_path.suffix + ".partial")
            if partial.is_file():
                harvest_path = partial
        rows_by_id: dict[int, dict[str, Any]] = {}
        if harvest_path.is_file():
            with harvest_path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    payload = json.loads(line)
                    if isinstance(payload.get("item_id"), int):
                        rows_by_id[payload["item_id"]] = payload

        by_variant = {int(r["variant_id"]): r for r in targets}
        writes: list[dict[str, Any]] = []
        for vid, (query, ids) in sorted(per_target.items()):
            row = by_variant[vid]
            survivors: list[tuple[int, dict[str, Any]]] = []
            rejections: list[str] = []
            for item_id in ids:
                held_by = owners.get(str(item_id))
                if held_by and held_by["match_status"] == "exact":
                    rejections.append(f"{item_id}:proven_binding_elsewhere")
                    continue
                ok, reason, facts = rule_candidate(row, rows_by_id.get(item_id))
                if ok:
                    survivors.append((item_id, facts))
                else:
                    rejections.append(f"{item_id}:{reason}")
            if len(survivors) == 1:
                item_id, facts = survivors[0]
                evidence, evidence_sha = build_evidence(item_id, facts, items_dir)
                counts["accepted"] += 1
                # An unproven row on another card is not ownership, but taking
                # the item away from it IS a change to that card, so it is
                # named in the proposal and gated behind its own flag.
                current = contested.get(str(item_id))
                repoint = current is not None and current["variant_id"] != vid
                if repoint:
                    counts["repointProposed"] += 1
                record = {
                    "variant_id": vid, "query": query, "item_id": item_id,
                    "claim": facts["claim"], "evidence_sha256": evidence_sha,
                    "masterName": facts["masterName"][:120],
                    "pop": int(row["pop"]), "card": str(row["canonical_name"])[:120],
                }
                if current is not None:
                    record["currentlyHeldBy"] = current
                proposals.append(record)
                writes.append({
                    "item_id": item_id, "variant_id": vid, "evidence": evidence,
                    "evidence_sha": evidence_sha, "claim": facts["claim"],
                    "fetched": str(facts["fetchedAt"] or ""), "mirror": mirror_tuple(row),
                    "current": current,
                })
            elif len(survivors) > 1:
                counts["ambiguous"] += 1
                held.append({
                    "variant_id": vid, "reason": "ambiguous_survivors", "detail": query,
                    "survivors": [
                        {"item_id": i, "masterName": f["masterName"][:100]} for i, f in survivors
                    ],
                })
            else:
                counts["noSurvivor"] += 1
                held.append({
                    "variant_id": vid, "reason": "no_survivor", "detail": query,
                    "card": str(row["canonical_name"])[:120],
                    "rejections": rejections[:12],
                })
            verdict = ("ACCEPT" if len(survivors) == 1 else
                       "ambiguous" if survivors else "hold")
            progress(f"[rule] v{vid} pop={row['pop']} {verdict}"
                     f" candidates={len(ids)} :: {str(row['canonical_name'])[:60]}")

        if args.write and writes:
            written = 0
            try:
                with conn.cursor() as cursor:
                    for w in writes:
                        if w["current"] is not None and not args.allow_repoint:
                            counts["repointSkipped"] += 1
                            continue
                        captured_at = (
                            datetime.strptime(w["fetched"], "%Y-%m-%dT%H:%M:%S%z")
                            if w["fetched"] else datetime.now(timezone.utc)
                        )
                        cursor.execute(
                            "INSERT INTO catalog_provider_capture_receipt (source_code,"
                            " external_entity_id, capture_sha256, capture_path,"
                            " captured_at, generation_id, parser_version)"
                            " VALUES ('snkrdunk', %s, %s, %s, %s, %s, %s)"
                            " ON DUPLICATE KEY UPDATE"
                            " capture_sha256=VALUES(capture_sha256),"
                            " capture_path=VALUES(capture_path),"
                            " captured_at=VALUES(captured_at),"
                            " generation_id=VALUES(generation_id),"
                            " parser_version=VALUES(parser_version)",
                            (
                                str(w["item_id"]), w["evidence"]["evidence"]["sha256"],
                                w["evidence"]["evidence"]["path"][:500],
                                captured_at, "snk_discover", parser_version[:64],
                            ),
                        )
                        mirror = w["mirror"]
                        payload = (
                            w["evidence_sha"], str(w["claim"] or "")[:64],
                            json.dumps(w["evidence"], ensure_ascii=False, sort_keys=True),
                            mirror[0][:32], mirror[1][:8], mirror[2][:24],
                            mirror[3][:96], mirror[4][:24], mirror[5][:64],
                            mirror[6][:191], mirror[7][:64],
                        )
                        if w["current"] is None:
                            # No ON DUPLICATE KEY: the id was proven unbound
                            # above, so a duplicate here means the world changed
                            # under us and the run must fail loudly instead of
                            # overwriting somebody else's binding.
                            cursor.execute(
                                "INSERT INTO catalog_source_identity (source_code,"
                                " external_entity_id, variant_id, match_status,"
                                " evidence_sha256, source_product_number,"
                                " bind_evidence_json, bound_tcg_code, bound_card_language,"
                                " bound_set_code, bound_collector_number,"
                                " bound_printing_code, bound_parallel_code,"
                                " bound_edition_code, bound_finish_code)"
                                " VALUES ('snkrdunk', %s, %s, 'exact', %s, %s, %s,"
                                " %s, %s, %s, %s, %s, %s, %s, %s)",
                                (str(w["item_id"]), w["variant_id"]) + payload,
                            )
                            written += 1
                            continue
                        # The row exists on another card as an unproven claim.
                        # match_status<>'exact' in the WHERE is the guard, not a
                        # formality: if anything promoted that row between the
                        # read above and here, zero rows change and the run
                        # aborts rather than quietly taking a proven binding.
                        cursor.execute(
                            "UPDATE catalog_source_identity SET variant_id=%s,"
                            " match_status='exact', evidence_sha256=%s,"
                            " source_product_number=%s, bind_evidence_json=%s,"
                            " bound_tcg_code=%s, bound_card_language=%s,"
                            " bound_set_code=%s, bound_collector_number=%s,"
                            " bound_printing_code=%s, bound_parallel_code=%s,"
                            " bound_edition_code=%s, bound_finish_code=%s"
                            " WHERE source_code='snkrdunk' AND external_entity_id=%s"
                            "   AND match_status <> 'exact'",
                            (w["variant_id"],) + payload + (str(w["item_id"]),),
                        )
                        if cursor.rowcount != 1:
                            raise SystemExit(
                                f"repoint of item {w['item_id']} changed"
                                f" {cursor.rowcount} rows, not 1: the row moved"
                                " or was proven while this run was thinking"
                            )
                        counts["repointed"] += 1
                        written += 1
                conn.commit()
                counts["bound"] = written
                progress(f"[bind] committed {written} snkrdunk identities"
                         f" ({counts['repointed']} taken from unproven claims,"
                         f" {counts['repointSkipped']} left alone without"
                         " --allow-repoint)")
            except Exception:
                conn.rollback()
                raise

        report = {
            "snkIdentityDiscover": True,
            "write": bool(args.write),
            "generation": generation,
            "counts": counts,
            "proposals": proposals,
            "held": held,
        }
        artifact = R.ROOT / "data" / "runtime" / "rebuild-036" / f"snk-identity-discover-{stamp}.json"
        artifact.write_bytes(R.canonical_json(report))
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
        return 0
    finally:
        conn.close()


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--write", action="store_true")
    parser.add_argument(
        "--allow-repoint", dest="allow_repoint", action="store_true",
        help="also take items whose only claim is another card's unproven"
             " (manual_review/rejected) row; without this they are reported"
             " and left alone",
    )
    parser.add_argument("--generation")
    parser.add_argument("--tcg", default="")
    parser.add_argument("--min-pop", dest="min_pop", type=int, default=int(R.POLICY["minPop"]))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--per-card", dest="per_card", type=int, default=20,
                        help="max search hits considered per card")
    parser.add_argument("--delay", type=float, default=0.8, help="seconds between searches")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--credentials-env", dest="credentials_env", type=Path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    return cmd_snk_identity_discover(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
