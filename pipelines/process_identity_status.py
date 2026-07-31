#!/usr/bin/env python3
"""Define + process non-confirmed identity_status buckets.

Statuses (see docs/IDENTITY_STATUS.md):
  confirmed | incomplete | alias | duplicate | review

Actions (--write):
  1. incomplete: re-resolve language; set language; rehash opaque; promote→confirmed when complete
  2. review: if same 5-tuple as a confirmed primary → alias + alias row
             if this row is alias-table canonical but status review → confirmed
             if v794-class identity mismatch (reason already on images) → leave review
  3. duplicate: if confirmed sibling same name/set/coll → copy language, alias→sibling
  4. alias: ensure catalog_variant_alias link exists; leave status

Dry-run default.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from backfill_card_language import find_g10_root, resolve_language  # noqa: E402
from card_identity import opaque_id_duplicate_salt, opaque_id_from_row  # noqa: E402
from db_runtime import add_connection_args, connection_from_args  # noqa: E402

try:
    from qualified_pool_operator import load_env

    load_env()
except Exception:
    pass

REPORT = ROOT / "temp" / "process-identity-status-report.json"


def sha_evidence(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def is_complete(row: dict[str, Any]) -> bool:
    lang = (row.get("card_language") or "").strip()
    coll = (row.get("collector_number") or "").strip()
    return bool(
        lang in {"en", "ja", "ko", "zhCN", "zhTW"}
        and (row.get("canonical_name") or "").strip()
        and (row.get("set_name") or "").strip()
        and (row.get("tcg_code") or "").strip()
        and coll
        and coll.casefold() not in {"unknown", "n/a", "none"}
    )


RANKING_POP_MINIMUM = 1000


def latest_exact_gemrate_psa10_pop(cur: Any, variant_id: int) -> int | None:
    """Trusted ranking POP only: GemRate PSA top-grade, not eBay/G10 mirrors."""

    cur.execute(
        """
        SELECT top_grade_population
        FROM market_grader_population_observation
        WHERE variant_id = %s
          AND grader_code = 'PSA'
          AND source_code = 'gemrate'
          AND estimated = 0
        ORDER BY observed_date DESC, id DESC
        LIMIT 1
        """,
        (variant_id,),
    )
    row = cur.fetchone()
    if not row or row.get("top_grade_population") is None:
        return None
    return int(row["top_grade_population"])


def main() -> int:
    parser = argparse.ArgumentParser()
    add_connection_args(parser)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--report-out", type=Path, default=REPORT)
    args = parser.parse_args()

    g10 = find_g10_root()
    conn = connection_from_args(args)
    stats: Counter = Counter()
    actions: list[dict[str, Any]] = []
    human_language_queue: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, opaque_id, tcg_code, card_language, canonical_name, set_name,
                       collector_number, identity_status
                FROM catalog_variant
                WHERE identity_status IN ('incomplete','review','duplicate','alias')
                ORDER BY identity_status, id
                """
            )
            rows = [dict(r) for r in cur.fetchall()]

            # Preload alias links
            cur.execute("SELECT duplicate_variant_id, canonical_variant_id, reason_code FROM catalog_variant_alias")
            alias_as_dup = {int(r["duplicate_variant_id"]): dict(r) for r in cur.fetchall()}
            cur.execute("SELECT duplicate_variant_id, canonical_variant_id FROM catalog_variant_alias")
            alias_targets = {int(r["canonical_variant_id"]) for r in cur.fetchall()}

            for v in rows:
                vid = int(v["id"])
                st = str(v["identity_status"])
                stats[f"seen_{st}"] += 1

                # ---------- incomplete ----------
                if st == "incomplete":
                    trusted_pop = latest_exact_gemrate_psa10_pop(cur, vid)
                    cur.execute(
                        "SELECT source_code, external_entity_id FROM catalog_source_identity WHERE variant_id=%s",
                        (vid,),
                    )
                    sources = [dict(x) for x in cur.fetchall()]
                    cur.execute(
                        "SELECT source_path FROM market_image_source_pointer WHERE variant_id=%s LIMIT 30",
                        (vid,),
                    )
                    ptrs = [str(x["source_path"] or "") for x in cur.fetchall()]
                    lang, evidence, status = resolve_language(
                        set_name=str(v.get("set_name") or ""),
                        sources=sources,
                        g10_root=g10,
                        pointer_paths=ptrs,
                    )
                    if not lang:
                        # Ranking hard gate: POP < 1000 (or no exact GemRate POP) is NOT human homework.
                        # Machine may still fill language later; do not escalate to daddy.
                        below_threshold = trusted_pop is None or trusted_pop < RANKING_POP_MINIMUM
                        reason = (
                            "below_pop1000_no_human"
                            if below_threshold
                            else "missing_card_language"
                        )
                        action_row = {
                            "variantId": vid,
                            "status": st,
                            "action": "leave_incomplete",
                            "reason": reason,
                            "detail": status,
                            "trustedGemratePsa10Pop": trusted_pop,
                            "humanLanguageRequired": not below_threshold,
                        }
                        actions.append(action_row)
                        stats["incomplete_still"] += 1
                        if below_threshold:
                            stats["incomplete_parked_below_pop1000"] += 1
                        else:
                            stats["incomplete_human_language"] += 1
                            human_language_queue.append(
                                {
                                    "variantId": vid,
                                    "name": v.get("canonical_name"),
                                    "set": v.get("set_name"),
                                    "collector": v.get("collector_number"),
                                    "tcg": v.get("tcg_code"),
                                    "trustedGemratePsa10Pop": trusted_pop,
                                    "why": status,
                                }
                            )
                        continue
                    # apply language + rehash + promote
                    v2 = dict(v)
                    v2["card_language"] = lang
                    try:
                        new_oid = opaque_id_from_row(v2)
                    except ValueError as exc:
                        actions.append(
                            {
                                "variantId": vid,
                                "action": "leave_incomplete",
                                "reason": f"opaque_error:{exc}",
                            }
                        )
                        stats["incomplete_opaque_err"] += 1
                        continue
                    # collision with existing?
                    cur.execute("SELECT id FROM catalog_variant WHERE opaque_id=%s AND id<>%s", (new_oid, vid))
                    owner = cur.fetchone()
                    if owner:
                        # become alias of owner
                        can_id = int(owner["id"])
                        actions.append(
                            {
                                "variantId": vid,
                                "action": "incomplete_to_alias",
                                "canonicalVariantId": can_id,
                                "language": lang,
                                "opaqueId": new_oid,
                            }
                        )
                        stats["incomplete_to_alias"] += 1
                        if args.write:
                            cur.execute(
                                "UPDATE catalog_variant SET card_language=%s, identity_status='alias' WHERE id=%s",
                                (lang, vid),
                            )
                            cur.execute(
                                """
                                INSERT INTO catalog_variant_alias
                                  (duplicate_variant_id, canonical_variant_id, reason_code,
                                   evidence_sha256, convergence_plan_sha256, merged_at)
                                VALUES (%s,%s,'opaque_salt_duplicate',%s,%s,%s)
                                ON DUPLICATE KEY UPDATE
                                  canonical_variant_id=VALUES(canonical_variant_id),
                                  reason_code=VALUES(reason_code),
                                  merged_at=VALUES(merged_at)
                                """,
                                (
                                    vid,
                                    can_id,
                                    sha_evidence({"lang": lang, "opaque": new_oid}),
                                    sha_evidence({"plan": "process_identity_status"}),
                                    now,
                                ),
                            )
                        continue
                    actions.append(
                        {
                            "variantId": vid,
                            "action": "incomplete_to_confirmed",
                            "language": lang,
                            "oldOpaque": v["opaque_id"],
                            "newOpaque": new_oid,
                            "evidence": evidence[:4],
                        }
                    )
                    stats["incomplete_to_confirmed"] += 1
                    if args.write:
                        # two-phase opaque if changing
                        old = str(v["opaque_id"])
                        if old != new_oid:
                            tmp = f"cmc_tmp_{hashlib.sha256(f'inc|{vid}|{new_oid}'.encode()).hexdigest()[:20]}"
                            cur.execute(
                                "UPDATE catalog_variant SET opaque_id=%s WHERE id=%s",
                                (tmp, vid),
                            )
                            cur.execute(
                                "UPDATE catalog_variant SET opaque_id=%s, card_language=%s, identity_status='confirmed' WHERE id=%s",
                                (new_oid, lang, vid),
                            )
                        else:
                            cur.execute(
                                "UPDATE catalog_variant SET card_language=%s, identity_status='confirmed' WHERE id=%s",
                                (lang, vid),
                            )
                        cur.execute(
                            """
                            UPDATE catalog_printing_identity
                            SET card_language=%s WHERE variant_id=%s
                            """,
                            (lang, vid),
                        )
                    continue

                # ---------- review ----------
                if st == "review":
                    # If alias table says THIS is the canonical target, promote to confirmed
                    if vid in alias_targets and is_complete(v):
                        actions.append(
                            {
                                "variantId": vid,
                                "action": "review_to_confirmed_is_alias_target",
                                "reason": "catalog_variant_alias.canonical",
                            }
                        )
                        stats["review_to_confirmed"] += 1
                        if args.write:
                            cur.execute(
                                "UPDATE catalog_variant SET identity_status='confirmed' WHERE id=%s",
                                (vid,),
                            )
                        continue

                    # same 5-tuple as a confirmed primary (lower id owns clean opaque)
                    if v.get("card_language"):
                        cur.execute(
                            """
                            SELECT id, identity_status, opaque_id FROM catalog_variant
                            WHERE tcg_code=%s AND card_language=%s AND set_name=%s
                              AND collector_number=%s AND canonical_name=%s AND id<>%s
                            ORDER BY id
                            """,
                            (
                                v["tcg_code"],
                                v["card_language"],
                                v["set_name"],
                                v["collector_number"],
                                v["canonical_name"],
                                vid,
                            ),
                        )
                        peers = [dict(x) for x in cur.fetchall()]
                        primary = next(
                            (p for p in peers if p["identity_status"] == "confirmed"),
                            None,
                        )
                        if primary is None:
                            # primary may be alias of someone else — pick lowest confirmed-capable
                            primary = next(
                                (p for p in peers if p["identity_status"] in ("confirmed", "alias")),
                                peers[0] if peers else None,
                            )
                        if primary:
                            can_id = int(primary["id"])
                            # if primary is alias of us, invert handled above
                            actions.append(
                                {
                                    "variantId": vid,
                                    "action": "review_to_alias",
                                    "canonicalVariantId": can_id,
                                    "reason": "opaque_salt_duplicate",
                                }
                            )
                            stats["review_to_alias"] += 1
                            if args.write:
                                cur.execute(
                                    "UPDATE catalog_variant SET identity_status='alias' WHERE id=%s",
                                    (vid,),
                                )
                                cur.execute(
                                    """
                                    INSERT INTO catalog_variant_alias
                                      (duplicate_variant_id, canonical_variant_id, reason_code,
                                       evidence_sha256, convergence_plan_sha256, merged_at)
                                    VALUES (%s,%s,'opaque_salt_duplicate',%s,%s,%s)
                                    ON DUPLICATE KEY UPDATE
                                      canonical_variant_id=VALUES(canonical_variant_id),
                                      reason_code=VALUES(reason_code),
                                      merged_at=VALUES(merged_at)
                                    """,
                                    (
                                        vid,
                                        can_id,
                                        sha_evidence({"peer": can_id}),
                                        sha_evidence({"plan": "process_identity_status"}),
                                        now,
                                    ),
                                )
                            continue

                    # known hard mismatch: any hard-reject image reason
                    cur.execute(
                        """
                        SELECT q.rejection_reason FROM market_image_qc q
                        JOIN market_image_asset a ON a.id=q.image_asset_id
                        WHERE a.variant_id=%s AND q.rejection_reason IS NOT NULL
                        LIMIT 10
                        """,
                        (vid,),
                    )
                    reasons = [str(r["rejection_reason"] or "") for r in cur.fetchall()]
                    hard = any(
                        "identity_mismatch" in r or "wrong_card_art" in r or "rare_candy" in r
                        for r in reasons
                    )
                    if hard or vid == 794:
                        actions.append(
                            {
                                "variantId": vid,
                                "action": "leave_review",
                                "reason": "identity_mismatch_name_vs_source",
                                "rejectionSamples": reasons[:3],
                            }
                        )
                        stats["review_leave_mismatch"] += 1
                        continue

                    actions.append(
                        {
                            "variantId": vid,
                            "action": "leave_review",
                            "reason": "unresolved",
                            "name": v.get("canonical_name"),
                        }
                    )
                    stats["review_leave_other"] += 1
                    continue

                # ---------- duplicate ----------
                if st == "duplicate":
                    cur.execute(
                        """
                        SELECT id, card_language, identity_status, opaque_id
                        FROM catalog_variant
                        WHERE tcg_code=%s AND set_name=%s AND collector_number=%s
                          AND canonical_name=%s AND id<>%s
                        ORDER BY id
                        """,
                        (
                            v["tcg_code"],
                            v["set_name"],
                            v["collector_number"],
                            v["canonical_name"],
                            vid,
                        ),
                    )
                    sibs = [dict(x) for x in cur.fetchall()]
                    primary = next((s for s in sibs if s["identity_status"] == "confirmed"), None)
                    if primary is None:
                        actions.append(
                            {
                                "variantId": vid,
                                "action": "leave_duplicate",
                                "reason": "no_confirmed_sibling",
                            }
                        )
                        stats["duplicate_leave"] += 1
                        continue
                    can_id = int(primary["id"])
                    lang = primary.get("card_language")
                    actions.append(
                        {
                            "variantId": vid,
                            "action": "duplicate_to_alias",
                            "canonicalVariantId": can_id,
                            "language": lang,
                        }
                    )
                    stats["duplicate_to_alias"] += 1
                    if args.write:
                        if lang:
                            cur.execute(
                                "UPDATE catalog_variant SET card_language=%s, identity_status='alias' WHERE id=%s",
                                (lang, vid),
                            )
                        else:
                            cur.execute(
                                "UPDATE catalog_variant SET identity_status='alias' WHERE id=%s",
                                (vid,),
                            )
                        cur.execute(
                            """
                            INSERT INTO catalog_variant_alias
                              (duplicate_variant_id, canonical_variant_id, reason_code,
                               evidence_sha256, convergence_plan_sha256, merged_at)
                            VALUES (%s,%s,'confirmed_printing_duplicate',%s,%s,%s)
                            ON DUPLICATE KEY UPDATE
                              canonical_variant_id=VALUES(canonical_variant_id),
                              reason_code=VALUES(reason_code),
                              merged_at=VALUES(merged_at)
                            """,
                            (
                                vid,
                                can_id,
                                sha_evidence({"sibling": can_id}),
                                sha_evidence({"plan": "process_identity_status"}),
                                now,
                            ),
                        )
                    continue

                # ---------- alias ----------
                if st == "alias":
                    link = alias_as_dup.get(vid)
                    if link:
                        actions.append(
                            {
                                "variantId": vid,
                                "action": "alias_ok",
                                "canonicalVariantId": link["canonical_variant_id"],
                                "reasonCode": link.get("reason_code"),
                            }
                        )
                        stats["alias_ok"] += 1
                    else:
                        # orphan alias status — try find same-tuple confirmed
                        cur.execute(
                            """
                            SELECT id FROM catalog_variant
                            WHERE tcg_code=%s AND set_name=%s AND collector_number=%s
                              AND canonical_name=%s AND identity_status='confirmed' AND id<>%s
                            ORDER BY id LIMIT 1
                            """,
                            (
                                v["tcg_code"],
                                v["set_name"],
                                v["collector_number"],
                                v["canonical_name"],
                                vid,
                            ),
                        )
                        p = cur.fetchone()
                        if p:
                            can_id = int(p["id"])
                            actions.append(
                                {
                                    "variantId": vid,
                                    "action": "alias_link_created",
                                    "canonicalVariantId": can_id,
                                }
                            )
                            stats["alias_link_created"] += 1
                            if args.write:
                                cur.execute(
                                    """
                                    INSERT INTO catalog_variant_alias
                                      (duplicate_variant_id, canonical_variant_id, reason_code,
                                       evidence_sha256, convergence_plan_sha256, merged_at)
                                    VALUES (%s,%s,'confirmed_printing_duplicate',%s,%s,%s)
                                    ON DUPLICATE KEY UPDATE merged_at=VALUES(merged_at)
                                    """,
                                    (
                                        vid,
                                        can_id,
                                        sha_evidence({"repair": True}),
                                        sha_evidence({"plan": "process_identity_status"}),
                                        now,
                                    ),
                                )
                        else:
                            actions.append(
                                {
                                    "variantId": vid,
                                    "action": "alias_orphan",
                                    "name": v.get("canonical_name"),
                                }
                            )
                            stats["alias_orphan"] += 1
                    continue

            if args.write:
                conn.commit()
            else:
                conn.rollback()

            cur.execute(
                "SELECT identity_status, COUNT(*) n FROM catalog_variant GROUP BY identity_status ORDER BY n DESC"
            )
            after_counts = [dict(r) for r in cur.fetchall()]

        report = {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "write": bool(args.write),
            "rankingPopMinimum": RANKING_POP_MINIMUM,
            "stats": dict(stats),
            "afterCounts": after_counts,
            "humanLanguageQueue": human_language_queue,
            "humanLanguageQueueRule": (
                "Only incomplete rows with exact GemRate PSA10 POP >= 1000 and no machine-resolved "
                "card_language. POP below 1000 / missing GemRate POP is parked — never human fill."
            ),
            "actions": actions,
        }
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"write": args.write, "stats": dict(stats), "afterCounts": after_counts}, indent=2))
        print(f"report {args.report_out}")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
