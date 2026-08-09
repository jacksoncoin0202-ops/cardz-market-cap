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
from snkrdunk_bulk import UA

ITEM_RE = re.compile(r"/apparels/(\d+)")
# "One Piece Japanese OP05-Awakening of the New Era" -> OP05; "...PRB01-..." -> PRB01
SET_CODE_RE = re.compile(r"\b([A-Z]{2,4}\d{2})\b")
SEARCH_URL = "https://snkrdunk.com/search?keywords={}&page=1"

# SNKRDUNK writes the treatment as a suffix on the rarity code, right before
# the bracketed product number: "Nami R-P [OP01-016]". Every pair below was
# read off a One Piece binding this database already accepted, not guessed:
#
#   SEC     [OP10-118]  <-> printing base   (item 563129)
#   R-P     [OP13-051]  <-> printing aa     "Alternate Art"        (718301)
#   SEC-P   [OP05-119]  <-> printing aa     "Alternate Art"        (198698)
#   R-SPC   [OP01-016]  <-> printing sp     "Special Alternate Art" (198702)
#   SEC-SPC [OP05-119]  <-> printing sp     "Wanted Alternate Art"  (471531)
#   SR-TR   [OP07-109]  <-> printing tr     "Treasure Rare"        (385091)
#   SEC-GSP [OP09-118]  <-> "Gold"                                  (349472)
#
# A bare "-SP" is deliberately absent: the only -SP listings seen so far spell
# a gloss next to it ("(Comic Parallel)" / "(コミパラ)"), and without that gloss
# there is nothing proving which treatment it is. Unmapped holds, never guesses.
SNK_SUFFIX_TREATMENT = {
    "": "base",
    "P": "aa",
    "SPC": "sp",
    "TR": "tr",
    "GSP": "gsp",
}
# GemRate's own wording for the same treatments (fingerprint.parallel).
GEMRATE_TREATMENT = {
    "": "base",
    "base": "base",
    "alternate art": "aa",
    "manga alternate art": "manga",
    "special alternate art": "sp",
    "wanted alternate art": "sp",
    "treasure rare": "tr",
    "gold": "gsp",
}
RARITY_TOKEN_RE = re.compile(r"^(?P<rarity>[A-Z]{1,4})(?:-(?P<suffix>[A-Z]{1,4}))?$")


def snk_treatment(master_name: str, localized: str) -> str:
    """Canonical treatment token SNKRDUNK claims, '' when it claims nothing
    this vocabulary can name."""

    text = f"{master_name} {localized}"
    head = master_name.split("[")[0].split("(")[0].strip()
    tokens = head.replace("　", " ").split()
    if not tokens:
        return ""
    match = RARITY_TOKEN_RE.match(tokens[-1])
    if not match:
        return ""
    suffix = (match.group("suffix") or "").upper()
    if suffix == "SP":
        # Only the spelled-out comic gloss makes an -SP readable.
        lowered = text.casefold()
        if "comic parallel" in lowered or "コミパラ" in text:
            return "manga"
        return ""
    return SNK_SUFFIX_TREATMENT.get(suffix, "")


def gemrate_treatment(parallel_words: str) -> str:
    return GEMRATE_TREATMENT.get(R._norm_text(parallel_words), "")


# Words that appear on nearly every One Piece listing and therefore prove
# nothing about WHICH product a card came from.
_PRODUCT_STOPWORDS = frozenset({
    "one", "piece", "japanese", "english", "version", "card", "cards", "the",
    "of", "in", "a", "an", "and", "for", "booster", "pack", "set", "edition",
    "collection", "deck", "vol", "no", "op", "st", "prb", "eb", "p",
    # "One Piece Japanese Promos" is GemRate's bucket for every promo ever
    # printed, so it names no product at all. Treating it as distinctive would
    # let any listing with the word "Promotional" in it look like a match.
    "promo", "promos", "promotional",
})


def _product_tokens(text: str) -> set[str]:
    words = re.split(r"[^0-9a-z]+", R._norm_text(text))
    return {w for w in words if w and w not in _PRODUCT_STOPWORDS and not SET_CODE_RE.match(w.upper())}


def product_agrees(
    our_set_name: str, our_parallel: str, master_name: str, localized: str,
) -> tuple[bool, str]:
    """Does SNKRDUNK's listing name the same product our catalog does?

    One Piece reprints a card under its ORIGINAL number in many later
    products: OP01-016 Nami exists as the Romance Dawn booster parallel, the
    25th Anniversary premium collection, the Girls Edition, a promo set and
    more. The shared conflict check stops as soon as the set CODES agree
    ("an agreeing set code is the vocabulary-free signal"), which is true for
    Pokemon but lets every one of those OP01-016 products look identical. So
    this lane additionally demands that our distinctive product words actually
    appear in the provider's listing.

    Which field holds those words depends on the row. A booster card names its
    product in set_name and its treatment in parallel ("Alternate Art"). A
    promo names nothing in set_name -- GemRate files every promo ever printed
    under one bucket -- and names the product in parallel instead ("Ichiban
    Kuji Purchase Bonus", "PSA Magazine Exclusive"). The GEMRATE_TREATMENT
    table already decides which of those two a parallel is: a wording it can
    name is a treatment and says nothing about the product, and a wording it
    cannot name is a product and has to be proved like one.
    """

    ours = _product_tokens(our_set_name)
    if not gemrate_treatment(our_parallel):
        ours |= _product_tokens(our_parallel)
    if not ours:
        return False, "our_set_name_has_no_distinctive_token"
    theirs = _product_tokens(f"{master_name} {localized}")
    missing = sorted(
        token for token in ours
        if not any(
            token == other
            or (min(len(token), len(other)) >= 4
                and (token.startswith(other) or other.startswith(token)))
            for other in theirs
        )
    )
    if missing:
        return False, f"product_mismatch:missing={missing}"
    return True, ""


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


def taken_item_ids(conn: Any, item_ids: list[int]) -> set[str]:
    """Item ids already bound to some variant.

    catalog_source_identity is keyed (source_code, external_entity_id): one
    SNKRDUNK item belongs to exactly one variant, so a candidate that is
    already spoken for must be dropped rather than stolen.
    """

    if not item_ids:
        return set()
    marks = ",".join(["%s"] * len(item_ids))
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT external_entity_id FROM catalog_source_identity"
            f" WHERE source_code='snkrdunk' AND external_entity_id IN ({marks})",
            tuple(str(i) for i in item_ids),
        )
        return {str(row["external_entity_id"]) for row in cursor.fetchall()}


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
        "candidatesTaken": 0, "accepted": 0, "ambiguous": 0, "noSurvivor": 0,
        "bound": 0,
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

        session = requests.Session()
        session.headers["User-Agent"] = UA
        per_target: dict[int, tuple[str, list[int]]] = {}
        every_id: set[int] = set()
        for row in targets:
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
            if not ids:
                counts["searchEmpty"] += 1
                held.append({"variant_id": vid, "reason": "search_empty",
                             "detail": " | ".join(queries)})
                continue
            per_target[vid] = (" | ".join(queries), ids)
            every_id.update(ids)
        counts["candidates"] = len(every_id)

        taken = taken_item_ids(conn, sorted(every_id))
        counts["candidatesTaken"] = len(taken)

        harvest_path = base_dir / "snk_discover_harvest.jsonl"
        worklist = sorted(i for i in every_id if str(i) not in taken)
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
                if str(item_id) in taken:
                    rejections.append(f"{item_id}:already_bound_elsewhere")
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
                record = {
                    "variant_id": vid, "query": query, "item_id": item_id,
                    "claim": facts["claim"], "evidence_sha256": evidence_sha,
                    "masterName": facts["masterName"][:120],
                    "pop": int(row["pop"]), "card": str(row["canonical_name"])[:120],
                }
                proposals.append(record)
                writes.append({
                    "item_id": item_id, "variant_id": vid, "evidence": evidence,
                    "evidence_sha": evidence_sha, "claim": facts["claim"],
                    "fetched": str(facts["fetchedAt"] or ""), "mirror": mirror_tuple(row),
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

        if args.write and writes:
            try:
                with conn.cursor() as cursor:
                    for w in writes:
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
                        # No ON DUPLICATE KEY: the id was proven unbound above,
                        # so a duplicate here means the world changed under us
                        # and the run must fail loudly instead of overwriting
                        # somebody else's binding.
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
                            (
                                str(w["item_id"]), w["variant_id"], w["evidence_sha"],
                                str(w["claim"] or "")[:64],
                                json.dumps(w["evidence"], ensure_ascii=False, sort_keys=True),
                                mirror[0][:32], mirror[1][:8], mirror[2][:24],
                                mirror[3][:96], mirror[4][:24], mirror[5][:64],
                                mirror[6][:191], mirror[7][:64],
                            ),
                        )
                conn.commit()
                counts["bound"] = len(writes)
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
