#!/usr/bin/env python3
"""Review-only exact PriceCharting identity binds from saved PC-FULL HTML.

Plans are content-addressed.  A plan row requires one current catalog variant,
one unclaimed PC product, exact title/name/collector/set/language evidence and
one immutable saved HTML artifact.  Default mode creates a plan only; --write
is an explicit transactional, idempotent writer.
"""
from __future__ import annotations

import argparse, hashlib, html, json, re, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
from pc_ungraded_reference_ingest import MAP_DEFAULT, db, load_candidate_rows, resolve_html_path
from pc_psa10_price_derivation import sha256, validate_pc_psa10

CONTRACT = "pc_saved_identity_bind_v1"
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", html.unescape(str(value or "")).casefold())

def collector_match(db_value: Any, title: str) -> bool:
    base = norm(str(db_value).split("/", 1)[0])
    return bool(base and re.search(r"(?:#|\b)" + re.escape(base) + r"\b", html.unescape(title), re.I))

def title_of(path: Path) -> str | None:
    match = TITLE_RE.search(path.read_text(encoding="utf-8", errors="replace"))
    return re.sub(r"\s+", " ", html.unescape(match.group(1))).strip() if match else None

def current_targets(report: Path) -> set[int]:
    doc = json.loads(report.read_text(encoding="utf-8"))
    wanted = {"exact_psa10_price_missing", "exact_psa10_price_stale"}
    return {int(card["facts"]["identity"]["variantId"]) for card in doc.get("cards", []) if wanted.intersection(card.get("blockers", []))}

def catalog_rows(conn: Any, ids: set[int]) -> dict[int, dict[str, Any]]:
    if not ids: return {}
    marks = ",".join(["%s"] * len(ids))
    with conn.cursor() as cur:
        cur.execute(f"SELECT id,canonical_name,set_name,collector_number,card_language FROM catalog_variant WHERE id IN ({marks})", sorted(ids))
        return {int(row["id"]): row for row in cur.fetchall()}

def existing_pc(conn: Any) -> dict[str, list[int]]:
    with conn.cursor() as cur:
        cur.execute("SELECT external_entity_id,variant_id FROM catalog_source_identity WHERE source_code='pricecharting'")
        result: dict[str, list[int]] = defaultdict(list)
        for row in cur.fetchall(): result[str(row["external_entity_id"])].append(int(row["variant_id"]))
        return result

def classify(conn: Any, candidates: list[dict[str, Any]], ids: set[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Counter[str]]:
    catalog, existing = catalog_rows(conn, ids), existing_pc(conn)
    selected = [row for row in candidates if int(row["variant_id"]) in ids]
    product_counts = Counter(str(row["pc_product_id"]) for row in selected)
    approved, rejected, outcomes = [], [], Counter()
    for row in selected:
        vid, product = int(row["variant_id"]), str(row["pc_product_id"])
        card = catalog.get(vid); reason = None
        path = resolve_html_path(row)
        replay, replay_reason = validate_pc_psa10(row)
        title = title_of(path) if path else None
        if card is None: reason = "catalog_missing"
        elif path is None or replay is None or title is None: reason = "artifact_" + replay_reason
        elif norm(card["canonical_name"]) not in norm(title): reason = "title_name_mismatch"
        elif not collector_match(card["collector_number"], title): reason = "title_collector_mismatch"
        elif ("japanese" in norm(card["set_name"]) or str(card["card_language"]).casefold() == "ja") and "japanese" not in norm(title): reason = "title_set_or_language_mismatch"
        elif "japanese" in norm(title) and str(card["card_language"]).casefold() != "ja": reason = "title_set_or_language_mismatch"
        else:
            # All title set tokens must be represented by the canonical set,
            # except the stable storefront boilerplate and named set-code tail.
            title_set = title.split("|")[1] if "|" in title else ""
            required = [token for token in re.findall(r"[a-z0-9]{3,}", title_set.casefold()) if token not in {"pokemon", "piece", "cards", "one", "prices"}]
            if any(token not in norm(card["set_name"]) for token in required): reason = "title_set_mismatch"
            elif product_counts[product] != 1: reason = "product_collision_in_candidate"
            elif existing.get(product): reason = "product_already_owned"
        evidence = {"variantId": vid, "pcProductId": product, "pcUrl": row["pc_url"], "htmlPath": str(path.relative_to(ROOT)) if path and path.is_relative_to(ROOT) else str(path or ""), "artifactSha256": replay["artifact_sha256"] if replay else None, "htmlTitle": title}
        if reason:
            outcomes[reason] += 1; rejected.append({**evidence, "reason": reason}); continue
        payload = {"contract": CONTRACT, **evidence, "dbCard": {"name": card["canonical_name"], "set": card["set_name"], "collector": card["collector_number"], "language": card["card_language"]}}
        approved.append({"variantId": vid, "sourceCode": "pricecharting", "externalEntityId": product, "evidenceSha256": sha256(payload), "payload": payload})
        outcomes["unique_and_exact"] += 1
    for vid in sorted(ids - {int(r["variant_id"]) for r in selected}): outcomes["no_saved_pc_candidate"] += 1; rejected.append({"variantId":vid,"reason":"no_saved_pc_candidate"})
    return approved, rejected, outcomes

def build_plan(conn: Any, report: Path, map_path: Path) -> dict[str, Any]:
    ids = current_targets(report); candidates = [row for row in load_candidate_rows(map_path) if int(row["variant_id"]) in ids]
    approved, rejected, outcomes = classify(conn, candidates, ids)
    collisions = {product: sorted(int(row["variant_id"]) for row in candidates if str(row["pc_product_id"]) == product) for product, count in Counter(str(row["pc_product_id"]) for row in candidates).items() if count > 1}
    doc = {"contract": CONTRACT, "readOnly": True, "targetReportSha256": hashlib.sha256(report.read_bytes()).hexdigest(), "mapSha256": hashlib.sha256(map_path.read_bytes()).hexdigest(), "approved": approved, "quarantined": rejected, "candidateProductCollisions": collisions, "outcomes": dict(sorted(outcomes.items()))}
    doc["planSha256"] = sha256({key:value for key,value in doc.items() if key != "planSha256"}); return doc

def read_plan(path: Path, expected: str) -> dict[str, Any]:
    doc=json.loads(path.read_text(encoding="utf-8")); actual=sha256({key:value for key,value in doc.items() if key != "planSha256"})
    if doc.get("contract") != CONTRACT or doc.get("readOnly") is not True or actual != expected or doc.get("planSha256") != actual: raise ValueError("PC identity plan SHA mismatch")
    return doc

def recheck_and_write(conn: Any, plan: Mapping[str, Any], *, write: bool) -> tuple[int,int]:
    rows=plan.get("approved", []); seen_products=set(); seen_variants=set()
    for row in rows:
        payload=row.get("payload",{}); vid=int(row.get("variantId") or 0); product=str(row.get("externalEntityId") or "")
        if not isinstance(payload,Mapping) or vid<=0 or not product.isdigit() or product in seen_products or vid in seen_variants or sha256(payload)!=row.get("evidenceSha256"): raise ValueError("invalid/ambiguous approved PC row")
        path = ROOT / str(payload.get("htmlPath") or "")
        replay, reason = validate_pc_psa10({"variant_id":vid,"pc_product_id":product,"pc_url":payload.get("pcUrl"),"htmlPath":str(path)})
        if replay is None or replay.get("artifact_sha256") != payload.get("artifactSha256") or title_of(path) != payload.get("htmlTitle"):
            raise ValueError(f"saved PC artifact drift for variant {vid}: {reason}")
        seen_products.add(product);seen_variants.add(vid)
    if not rows:return 0,0
    marks=",".join(["%s"]*len(rows)); ids=[int(r["variantId"]) for r in rows]; products=[str(r["externalEntityId"]) for r in rows]
    with conn.cursor() as cur:
        cur.execute(f"SELECT id,canonical_name,set_name,collector_number,card_language FROM catalog_variant WHERE id IN ({marks})", ids)
        cards = {int(card["id"]): card for card in cur.fetchall()}
        for row in rows:
            expected = row["payload"].get("dbCard")
            card = cards.get(int(row["variantId"]))
            if not isinstance(expected, Mapping) or card is None or any(str(card.get(key)) != str(expected.get(label)) for key,label in (("canonical_name","name"),("set_name","set"),("collector_number","collector"),("card_language","language"))):
                raise ValueError("catalog identity drift")
        cur.execute(f"SELECT source_code,external_entity_id,variant_id FROM catalog_source_identity WHERE source_code='pricecharting' AND (external_entity_id IN ({marks}) OR variant_id IN ({marks}))", [*products,*ids])
        if cur.fetchall(): raise ValueError("current PC ownership drift")
        cur.execute(f"SELECT duplicate_variant_id,canonical_variant_id FROM catalog_variant_alias WHERE duplicate_variant_id IN ({marks}) OR canonical_variant_id IN ({marks})", [*ids,*ids])
        if cur.fetchall(): raise ValueError("alias present; PC plan refuses alias binding")
        if not write:return len(rows),0
        now=datetime.now(timezone.utc).replace(tzinfo=None)
        cur.executemany("INSERT INTO catalog_source_identity (source_code,external_entity_id,variant_id,match_status,evidence_sha256) VALUES ('pricecharting',%s,%s,'exact',%s)", [(str(r["externalEntityId"]),int(r["variantId"]),str(r["evidenceSha256"])) for r in rows])
    conn.commit(); return len(rows),len(rows)

def main() -> int:
    ap=argparse.ArgumentParser(description="Build/replay strict saved-PC identity binding plan")
    ap.add_argument("--report",type=Path);ap.add_argument("--map",type=Path,default=MAP_DEFAULT);ap.add_argument("--out",type=Path);ap.add_argument("--plan",type=Path);ap.add_argument("--plan-sha256");ap.add_argument("--write",action="store_true");a=ap.parse_args()
    if a.plan:
        if not a.plan_sha256: ap.error("--plan requires --plan-sha256")
        doc=read_plan(a.plan,a.plan_sha256)
    else:
        if not a.report: ap.error("--report required to build")
        conn=db()
        try: doc=build_plan(conn,a.report,a.map)
        finally: conn.close()
        if a.out: a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    conn=db()
    try: would,written=recheck_and_write(conn,doc,write=a.write)
    except Exception: conn.rollback();raise
    finally: conn.close()
    print(json.dumps({"contract":CONTRACT,"planSha256":doc["planSha256"],"approved":len(doc["approved"]),"quarantined":len(doc["quarantined"]),"wouldChange":would,"changed":written,"outcomes":doc["outcomes"]},sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
