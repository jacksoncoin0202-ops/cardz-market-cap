#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Per-variant identity evidence ledger.

Every external bind / URL / POP snapshot / human verification hangs off
``catalog_variant.id`` (opaque CARDZ id). QC and human review read this table
to see the full identity dossier for one card.

Usage:
  python -X utf8 pipelines/identity_evidence_ledger.py migrate
  python -X utf8 pipelines/identity_evidence_ledger.py backfill
  python -X utf8 pipelines/identity_evidence_ledger.py dossier --variant-id 1268
  python -X utf8 pipelines/identity_evidence_ledger.py attach \\
      --variant-id 1268 --source pricecharting --kind external_url \\
      --url 'https://www.pricecharting.com/game/pokemon-paldea-evolved/magikarp-203' \\
      --status verified --actor daddy --claim '{"note":"human supplied"}'
  python -X utf8 pipelines/identity_evidence_ledger.py export-review \\
      --out data/runtime/private-reports/fill/MAIN-PROGRESS/IDENTITY_DOSSIERS.json
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

from snk_image_promotion import db  # noqa: E402

MIGRATION = ROOT / "pipelines/migrations/017_catalog_identity_evidence.mysql.sql"

# Known URL templates (no invent — only when we have external id)
URL_TEMPLATES = {
    "snkrdunk": "https://snkrdunk.com/apparels/{id}",
    "snk_psa10": "https://snkrdunk.com/apparels/{id}",
    "pricecharting": None,  # must supply full URL
    "psa": "https://www.psacard.com/pop/{id}",  # only if id is known pop slug
    "gemrate": "https://www.gemrate.com/universal-search?gemrate_id={id}",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ensure_table(conn) -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    # strip comments that are full-line; execute statements
    parts = []
    buf = []
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            parts.append("\n".join(buf))
            buf = []
    cur = conn.cursor()
    for stmt in parts:
        stmt = stmt.strip()
        if not stmt:
            continue
        cur.execute(stmt)
    conn.commit()


def upsert_evidence(
    cur,
    *,
    variant_id: int,
    evidence_kind: str,
    source_code: str,
    external_entity_id: str | None,
    external_url: str | None,
    match_status: str,
    claim: dict[str, Any] | None,
    actor: str,
    observed_at: datetime | None = None,
) -> str:
    claim_s = canonical_json(claim or {})
    observed = observed_at or utc_now()
    dedupe_src = "|".join(
        [
            str(variant_id),
            evidence_kind,
            source_code,
            external_entity_id or "",
            external_url or "",
            match_status,
            claim_s,
        ]
    )
    evi = sha256_text(dedupe_src)
    cur.execute(
        """
        INSERT INTO catalog_identity_evidence
          (variant_id, evidence_kind, source_code, external_entity_id, external_url,
           match_status, claim_json, evidence_sha256, observed_at, actor)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          external_entity_id=VALUES(external_entity_id),
          external_url=VALUES(external_url),
          match_status=VALUES(match_status),
          claim_json=VALUES(claim_json),
          observed_at=VALUES(observed_at),
          actor=VALUES(actor),
          updated_at=CURRENT_TIMESTAMP
        """,
        (
            variant_id,
            evidence_kind,
            source_code,
            external_entity_id,
            external_url,
            match_status,
            claim_s,
            evi,
            observed,
            actor,
        ),
    )
    return evi


def backfill(conn) -> dict[str, int]:
    cur = conn.cursor()
    stats = {
        "binds": 0,
        "pops": 0,
        "urls": 0,
        "prices": 0,
    }

    # 1) source binds
    cur.execute(
        """
        SELECT source_code, external_entity_id, variant_id, match_status, evidence_sha256, updated_at
        FROM catalog_source_identity
        """
    )
    for row in cur.fetchall():
        src = row["source_code"]
        eid = str(row["external_entity_id"])
        url = None
        tmpl = URL_TEMPLATES.get(src)
        if tmpl and "{id}" in tmpl:
            # only numeric SNK apparel ids
            if src in ("snkrdunk", "snk_psa10") and not eid.isdigit():
                url = None
            else:
                url = tmpl.format(id=eid)
        upsert_evidence(
            cur,
            variant_id=int(row["variant_id"]),
            evidence_kind="bind",
            source_code=src,
            external_entity_id=eid,
            external_url=url,
            match_status=str(row["match_status"]),
            claim={"from": "catalog_source_identity", "bind_evidence_sha256": row["evidence_sha256"]},
            actor="backfill",
            observed_at=row.get("updated_at") or utc_now(),
        )
        stats["binds"] += 1

    # 2) latest PSA POP per variant (gemrate)
    cur.execute(
        """
        SELECT p.variant_id, p.source_code, p.external_entity_id,
               p.top_grade_population, p.total_population, p.effective_at, p.grader_code
        FROM market_grader_population_observation p
        INNER JOIN (
          SELECT variant_id, MAX(effective_at) AS mx
          FROM market_grader_population_observation
          WHERE grader_code='psa'
          GROUP BY variant_id
        ) t ON t.variant_id=p.variant_id AND t.mx=p.effective_at
        WHERE p.grader_code='psa'
        """
    )
    for row in cur.fetchall():
        eid = str(row["external_entity_id"] or "")
        # strip gemrate: prefix for URL
        bare = eid.replace("gemrate:", "")
        url = None
        if len(bare) == 40 and all(c in "0123456789abcdef" for c in bare.lower()):
            url = URL_TEMPLATES["gemrate"].format(id=bare)
        upsert_evidence(
            cur,
            variant_id=int(row["variant_id"]),
            evidence_kind="pop",
            source_code=str(row["source_code"]),
            external_entity_id=eid,
            external_url=url,
            match_status="attached",
            claim={
                "grader": "psa",
                "psa10_pop": int(row["top_grade_population"]) if row["top_grade_population"] is not None else None,
                "total_pop": int(row["total_population"]) if row["total_population"] is not None else None,
                "effective_at": str(row["effective_at"]),
            },
            actor="backfill",
            observed_at=row["effective_at"],
        )
        stats["pops"] += 1

    # 3) latest ready prices by source (for dossier; not invent)
    cur.execute(
        """
        SELECT p.variant_id, p.source_code, p.price_usd, p.effective_at, p.metric_status
        FROM market_price_observation p
        INNER JOIN (
          SELECT variant_id, source_code, MAX(effective_at) AS mx
          FROM market_price_observation
          WHERE metric_status='ready'
          GROUP BY variant_id, source_code
        ) t ON t.variant_id=p.variant_id AND t.source_code=p.source_code AND t.mx=p.effective_at
        WHERE p.metric_status='ready'
        """
    )
    for row in cur.fetchall():
        upsert_evidence(
            cur,
            variant_id=int(row["variant_id"]),
            evidence_kind="price",
            source_code=str(row["source_code"]),
            external_entity_id=None,
            external_url=None,
            match_status="attached",
            claim={
                "price_usd": str(row["price_usd"]),
                "effective_at": str(row["effective_at"]),
            },
            actor="backfill",
            observed_at=row["effective_at"],
        )
        stats["prices"] += 1

    conn.commit()
    return stats


def attach(
    conn,
    *,
    variant_id: int,
    source: str,
    kind: str,
    url: str | None,
    external_id: str | None,
    status: str,
    actor: str,
    claim: dict[str, Any] | None,
) -> str:
    cur = conn.cursor()
    evi = upsert_evidence(
        cur,
        variant_id=variant_id,
        evidence_kind=kind,
        source_code=source,
        external_entity_id=external_id,
        external_url=url,
        match_status=status,
        claim=claim,
        actor=actor,
    )
    conn.commit()
    return evi


def dossier(conn, variant_id: int) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, opaque_id, tcg_code, canonical_name, set_name, collector_number, identity_status
        FROM catalog_variant WHERE id=%s
        """,
        (variant_id,),
    )
    v = cur.fetchone()
    cur.execute(
        """
        SELECT evidence_kind, source_code, external_entity_id, external_url,
               match_status, claim_json, evidence_sha256, observed_at, actor
        FROM catalog_identity_evidence
        WHERE variant_id=%s
        ORDER BY evidence_kind, source_code, observed_at DESC
        """,
        (variant_id,),
    )
    rows = []
    for r in cur.fetchall():
        claim = r["claim_json"]
        if isinstance(claim, str):
            try:
                claim = json.loads(claim)
            except Exception:
                pass
        rows.append({**r, "claim_json": claim})

    # derive summary POP for human review
    pop_psa10 = None
    for r in rows:
        if r["evidence_kind"] == "pop" and isinstance(r.get("claim_json"), dict):
            if r["claim_json"].get("psa10_pop") is not None:
                pop_psa10 = r["claim_json"]["psa10_pop"]
                break

    return {
        "variant": v,
        "psa10_pop_latest": pop_psa10,
        "evidence_count": len(rows),
        "evidence": rows,
    }


def export_review(conn, variant_ids: list[int] | None = None) -> list[dict[str, Any]]:
    cur = conn.cursor()
    if not variant_ids:
        cur.execute("SELECT id FROM catalog_variant ORDER BY id")
        variant_ids = [int(r["id"]) for r in cur.fetchall()]
    out = []
    for vid in variant_ids:
        d = dossier(conn, vid)
        if d["variant"]:
            out.append(d)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Identity evidence ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("migrate")
    sub.add_parser("backfill")

    p_d = sub.add_parser("dossier")
    p_d.add_argument("--variant-id", type=int, required=True)

    p_a = sub.add_parser("attach")
    p_a.add_argument("--variant-id", type=int, required=True)
    p_a.add_argument("--source", required=True)
    p_a.add_argument("--kind", default="external_url")
    p_a.add_argument("--url", default=None)
    p_a.add_argument("--external-id", default=None)
    p_a.add_argument("--status", default="verified")
    p_a.add_argument("--actor", default="human")
    p_a.add_argument("--claim", default="{}")

    p_e = sub.add_parser("export-review")
    p_e.add_argument("--out", type=Path, required=True)
    p_e.add_argument("--variant-ids", default=None, help="comma-separated")

    args = ap.parse_args()
    conn = db()

    if args.cmd == "migrate":
        ensure_table(conn)
        print(json.dumps({"action": "migrate", "ok": True}))
        return

    if args.cmd == "backfill":
        ensure_table(conn)
        stats = backfill(conn)
        print(json.dumps({"action": "backfill", **stats}, sort_keys=True))
        return

    if args.cmd == "dossier":
        ensure_table(conn)
        print(json.dumps(dossier(conn, args.variant_id), ensure_ascii=False, default=str, indent=2))
        return

    if args.cmd == "attach":
        ensure_table(conn)
        claim = json.loads(args.claim)
        evi = attach(
            conn,
            variant_id=args.variant_id,
            source=args.source,
            kind=args.kind,
            url=args.url,
            external_id=args.external_id,
            status=args.status,
            actor=args.actor,
            claim=claim,
        )
        print(json.dumps({"action": "attach", "evidence_sha256": evi, "variantId": args.variant_id}))
        return

    if args.cmd == "export-review":
        ensure_table(conn)
        vids = None
        if args.variant_ids:
            vids = [int(x) for x in args.variant_ids.split(",") if x.strip()]
        rows = export_review(conn, vids)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(rows, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
        print(json.dumps({"action": "export-review", "n": len(rows), "out": str(args.out)}))
        return


if __name__ == "__main__":
    main()
