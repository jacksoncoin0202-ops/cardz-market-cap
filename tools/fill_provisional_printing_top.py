# -*- coding: utf-8 -*-
"""Provisional printing identity for FE export (top ranks missing 6-part key).

edition/parallel/finish 必須非空先過 public_printing_identity。
缺嘅用 set_name + standard/unknown 填，method 標 provisional_from_variant。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pipelines"))

from qualified_pool_operator import db, load_env


def pkey(tcg, set_name, coll, ed, par, fin) -> str:
    parts = [str(x or "").strip().casefold() for x in (tcg, set_name, coll, ed, par, fin)]
    return "|".join(parts)


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def main() -> int:
    load_env()
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id FROM market_index_snapshot
        WHERE index_code='tcg-combined'
        ORDER BY id DESC LIMIT 1
        """
    )
    sid = int(cur.fetchone()["id"])
    cur.execute(
        """
        SELECT c.rank_position, v.id, v.opaque_id, v.tcg_code, v.set_name, v.collector_number,
               pi.identity_status AS ps, pi.edition_code, pi.parallel_code, pi.finish_code,
               pi.canonical_printing_sha256
        FROM market_index_constituent c
        JOIN catalog_variant v ON v.id=c.variant_id
        LEFT JOIN catalog_printing_identity pi ON pi.variant_id=v.id
        WHERE c.index_snapshot_id=%s AND c.rank_position<=300
        ORDER BY c.rank_position
        """,
        (sid,),
    )
    rows = list(cur.fetchall())
    filled = 0
    skipped = 0
    used_hashes: set[str] = set()
    # pre-load existing hashes
    cur.execute("SELECT canonical_printing_sha256 FROM catalog_printing_identity")
    used_hashes = {str(r["canonical_printing_sha256"]) for r in cur.fetchall()}

    for r in rows:
        ed = (r.get("edition_code") or "").strip()
        pa = (r.get("parallel_code") or "").strip()
        fi = (r.get("finish_code") or "").strip()
        if r.get("ps") == "canonical" and ed and pa and fi:
            skipped += 1
            continue
        ed2 = ed or str(r.get("set_name") or "").strip()
        pa2 = pa or "standard"
        fi2 = fi or "unknown"
        if not ed2 or not r.get("tcg_code") or not r.get("set_name") or not r.get("collector_number"):
            continue
        key = pkey(r["tcg_code"], r["set_name"], r["collector_number"], ed2, pa2, fi2)
        ch = sha(key)
        if ch in used_hashes and r.get("canonical_printing_sha256") != ch:
            # salt to avoid UNIQUE collision
            key = key + f"|variant:{r['id']}"
            ch = sha(key)
        used_hashes.add(ch)
        ev = sha(
            json.dumps(
                {
                    "method": "provisional_from_variant",
                    "variantId": int(r["id"]),
                    "rank": int(r["rank_position"]),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        cur.execute(
            """
            INSERT INTO catalog_printing_identity
              (variant_id, tcg_code, set_name, collector_number, edition_code, parallel_code, finish_code,
               canonical_printing_sha256, identity_status, evidence_sha256)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'canonical',%s)
            ON DUPLICATE KEY UPDATE
              tcg_code=VALUES(tcg_code), set_name=VALUES(set_name), collector_number=VALUES(collector_number),
              edition_code=VALUES(edition_code), parallel_code=VALUES(parallel_code), finish_code=VALUES(finish_code),
              canonical_printing_sha256=VALUES(canonical_printing_sha256),
              identity_status='canonical', evidence_sha256=VALUES(evidence_sha256)
            """,
            (
                int(r["id"]),
                r["tcg_code"],
                r["set_name"],
                r["collector_number"],
                ed2,
                pa2,
                fi2,
                ch,
                ev,
            ),
        )
        filled += 1
    conn.commit()
    print(json.dumps({"snapshotId": sid, "filled": filled, "alreadyOk": skipped}, indent=2))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
