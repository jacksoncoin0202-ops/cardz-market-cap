#!/usr/bin/env python3
"""Architecture rehash: opaque_id + printing_key include card_language.

Backend/DB only. Does not touch FE UI.

## What changes

1. ``catalog_variant.opaque_id`` → ``opaque_id(tcg, language, set, collector, name)``
   for every row with non-null ``card_language``.
2. True identity collisions (same 5-tuple) → lowest ``variant_id`` owns clean
   opaque_id; others get salted opaque_id + ``identity_status='review'``.
3. ``catalog_printing_identity`` rehashed with **7-part** key
   (tcg|language|set|collector|edition|parallel|finish).
4. Rows with NULL language: opaque_id left unchanged; if
   ``identity_status='confirmed'`` → demoted to ``incomplete`` (fail-closed:
   cannot rank as confirmed without language).

## Safety

- Dry-run default; ``--write`` required.
- Single transaction; fingerprint variant count before/after.
- Writes mapping report for editorial/presentation remaps.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from card_identity import (  # noqa: E402
    opaque_id_duplicate_salt,
    opaque_id_from_row,
    printing_identity_sha256_from_row,
    printing_key7_from_row,
)
from db_runtime import add_connection_args, connection_from_args  # noqa: E402

try:
    from qualified_pool_operator import load_env

    load_env()
except Exception:
    pass

REPORT_DEFAULT = ROOT / "temp" / "rehash-identity-language-report.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    add_connection_args(parser)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--report-out", type=Path, default=REPORT_DEFAULT)
    args = parser.parse_args()

    conn = connection_from_args(args)
    stats: Counter = Counter()
    mapping: list[dict[str, Any]] = []
    printing_updates: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n, SUM(SHA2(opaque_id,256)) AS fp FROM catalog_variant")
            before = dict(cur.fetchone())

            cur.execute(
                """
                SELECT id, opaque_id, tcg_code, card_language, canonical_name,
                       set_name, collector_number, identity_status
                FROM catalog_variant
                ORDER BY id
                """
            )
            variants = [dict(r) for r in cur.fetchall()]

            # --- phase A: compute target opaque_ids ---
            by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
            targets: dict[int, str] = {}
            for v in variants:
                vid = int(v["id"])
                if not v.get("card_language"):
                    stats["null_language"] += 1
                    targets[vid] = str(v["opaque_id"])  # keep
                    if str(v.get("identity_status") or "") == "confirmed":
                        stats["demote_confirmed_no_lang"] += 1
                    continue
                try:
                    new_id = opaque_id_from_row(v)
                except ValueError as exc:
                    stats["opaque_error"] += 1
                    targets[vid] = str(v["opaque_id"])
                    mapping.append(
                        {
                            "variantId": vid,
                            "old": v["opaque_id"],
                            "new": None,
                            "error": str(exc),
                        }
                    )
                    continue
                by_target[new_id].append(v)
                targets[vid] = new_id

            # resolve collisions
            final_opaque: dict[int, str] = {}
            for target, members in by_target.items():
                members_sorted = sorted(members, key=lambda m: int(m["id"]))
                primary = members_sorted[0]
                final_opaque[int(primary["id"])] = target
                if len(members_sorted) == 1:
                    stats["unique_target"] += 1
                else:
                    stats["collision_groups"] += 1
                    stats["collision_members"] += len(members_sorted)
                    for dup in members_sorted[1:]:
                        salted = opaque_id_duplicate_salt(target, int(dup["id"]))
                        final_opaque[int(dup["id"])] = salted
                        stats["salted_duplicates"] += 1

            for v in variants:
                vid = int(v["id"])
                if vid not in final_opaque:
                    final_opaque[vid] = targets[vid]

            # Null-language rows keep historical opaque_id; if that collides with a
            # language-aware rehash target, salt the null-language row (fail-closed:
            # unknown language never steals a clean identity id).
            claimed: dict[str, int] = {}
            for vid, oid in list(final_opaque.items()):
                vrow = next(x for x in variants if int(x["id"]) == vid)
                if vrow.get("card_language"):
                    if oid in claimed and claimed[oid] != vid:
                        # should already be salted in collision pass
                        continue
                    claimed[oid] = vid
            for v in variants:
                vid = int(v["id"])
                if v.get("card_language"):
                    continue
                oid = final_opaque[vid]
                if oid in claimed and claimed[oid] != vid:
                    final_opaque[vid] = opaque_id_duplicate_salt(oid, vid)
                    stats["salted_null_lang_collision"] += 1
                else:
                    claimed[oid] = vid

            # uniqueness preflight
            seen: dict[str, int] = {}
            for vid, oid in final_opaque.items():
                if oid in seen and seen[oid] != vid:
                    # last-resort salt
                    final_opaque[vid] = opaque_id_duplicate_salt(oid, vid)
                    stats["salted_last_resort"] += 1
                    oid = final_opaque[vid]
                if oid in seen and seen[oid] != vid:
                    raise RuntimeError(
                        f"opaque_id collision unresolved: {oid} vids {seen[oid]} {vid}"
                    )
                seen[oid] = vid

            # --- phase B: apply opaque updates ---
            opaque_sql: list[tuple[str, str, int]] = []
            status_sql: list[tuple[str, int]] = []
            for v in variants:
                vid = int(v["id"])
                old = str(v["opaque_id"])
                new = final_opaque[vid]
                changed = old != new
                if changed:
                    stats["opaque_changed"] += 1
                    opaque_sql.append((new, old, vid))
                    mapping.append(
                        {
                            "variantId": vid,
                            "old": old,
                            "new": new,
                            "language": v.get("card_language"),
                            "name": v.get("canonical_name"),
                            "collector": v.get("collector_number"),
                            "salted": new != targets.get(vid),
                        }
                    )
                else:
                    stats["opaque_unchanged"] += 1

                if not v.get("card_language") and str(v.get("identity_status") or "") == "confirmed":
                    status_sql.append(("incomplete", vid))
                elif (
                    bool(v.get("card_language"))
                    and targets.get(vid) != final_opaque[vid]
                    and str(v.get("identity_status") or "") == "confirmed"
                ):
                    # salted duplicate of another confirmed identity
                    status_sql.append(("review", vid))
                    stats["demote_duplicate_review"] += 1

            # --- phase C: printing identity 7-part rehash ---
            cur.execute(
                """
                SELECT pi.*, v.card_language AS variant_language, v.tcg_code AS v_tcg,
                       v.set_name AS v_set, v.collector_number AS v_coll
                FROM catalog_printing_identity pi
                JOIN catalog_variant v ON v.id = pi.variant_id
                """
            )
            printing_rows = [dict(r) for r in cur.fetchall()]
            print_updates: list[tuple[str, str | None, int]] = []
            print_hashes: dict[str, int] = {}
            for r in printing_rows:
                row_for_key = {
                    "tcg_code": r.get("tcg_code") or r.get("v_tcg"),
                    "card_language": r.get("card_language") or r.get("variant_language"),
                    "set_name": r.get("set_name") or r.get("v_set"),
                    "collector_number": r.get("collector_number") or r.get("v_coll"),
                    "edition_code": r.get("edition_code") or "",
                    "parallel_code": r.get("parallel_code") or "",
                    "finish_code": r.get("finish_code") or "",
                }
                base = printing_identity_sha256_from_row(row_for_key)
                vid = int(r["variant_id"])
                # salt if hash collides across different variants
                digest = base
                if digest in print_hashes and print_hashes[digest] != vid:
                    digest = __import__("hashlib").sha256(
                        f"{base}|variant:{vid}".encode()
                    ).hexdigest()
                    stats["printing_salted"] += 1
                print_hashes[digest] = vid
                lang = row_for_key["card_language"]
                if digest != r["canonical_printing_sha256"] or (
                    lang and r.get("card_language") != lang
                ):
                    print_updates.append((digest, lang, vid))
                    printing_updates.append(
                        {
                            "variantId": vid,
                            "oldSha": r["canonical_printing_sha256"],
                            "newSha": digest,
                            "language": lang,
                            "key": list(printing_key7_from_row(row_for_key)),
                        }
                    )
                    stats["printing_changed"] += 1
                else:
                    stats["printing_unchanged"] += 1

            report = {
                "generatedAt": now,
                "write": bool(args.write),
                "before": before,
                "stats": dict(stats),
                "opaqueMappingCount": len(mapping),
                "opaqueMapping": mapping,
                "printingUpdateCount": len(printing_updates),
                "printingUpdates": printing_updates[:200],
            }

            if not args.write:
                args.report_out.parent.mkdir(parents=True, exist_ok=True)
                args.report_out.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(json.dumps({"dryRun": True, "stats": dict(stats)}, indent=2))
                print(f"report {args.report_out}")
                return 0

            # WRITE — two-phase opaque_id to avoid UNIQUE mid-collisions
            import hashlib as _hashlib

            for i, (new, old, vid) in enumerate(opaque_sql):
                tmp = f"cmc_tmp_{_hashlib.sha256(f'tmp|{vid}|{i}|{new}'.encode()).hexdigest()[:20]}"
                cur.execute(
                    "UPDATE catalog_variant SET opaque_id=%s WHERE id=%s AND opaque_id=%s",
                    (tmp, vid, old),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(f"opaque tmp update failed vid={vid}")
            for new, old, vid in opaque_sql:
                cur.execute(
                    "UPDATE catalog_variant SET opaque_id=%s WHERE id=%s",
                    (new, vid),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(f"opaque final update failed vid={vid}")
            for status, vid in status_sql:
                cur.execute(
                    "UPDATE catalog_variant SET identity_status=%s WHERE id=%s",
                    (status, vid),
                )
            # Two-phase printing hash update to avoid UNIQUE mid-collision
            for i, (digest, lang, vid) in enumerate(print_updates):
                tmp = __import__("hashlib").sha256(
                    f"tmp-lang7-rehash|{vid}|{i}|{digest}".encode()
                ).hexdigest()
                cur.execute(
                    """
                    UPDATE catalog_printing_identity
                    SET canonical_printing_sha256=%s,
                        card_language=COALESCE(%s, card_language)
                    WHERE variant_id=%s
                    """,
                    (tmp, lang, vid),
                )
            for digest, lang, vid in print_updates:
                cur.execute(
                    """
                    UPDATE catalog_printing_identity
                    SET canonical_printing_sha256=%s,
                        evidence_sha256=%s
                    WHERE variant_id=%s
                    """,
                    (
                        digest,
                        __import__("hashlib").sha256(
                            f"printing-lang7|{digest}|{vid}".encode()
                        ).hexdigest(),
                        vid,
                    ),
                )

            cur.execute("SELECT COUNT(*) AS n, SUM(SHA2(opaque_id,256)) AS fp FROM catalog_variant")
            after = dict(cur.fetchone())
            if int(after["n"]) != int(before["n"]):
                conn.rollback()
                raise RuntimeError("variant count changed during rehash — rollback")

            # unique opaque check
            cur.execute(
                """
                SELECT opaque_id, COUNT(*) c FROM catalog_variant
                GROUP BY opaque_id HAVING c > 1 LIMIT 5
                """
            )
            dups = cur.fetchall()
            if dups:
                conn.rollback()
                raise RuntimeError(f"opaque_id not unique after rehash: {dups}")

            conn.commit()
            report["after"] = after
            report["stats"] = dict(stats)
            args.report_out.parent.mkdir(parents=True, exist_ok=True)
            args.report_out.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps({"write": True, "stats": dict(stats), "after": after}, indent=2))
            print(f"report {args.report_out}")
            return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
