#!/usr/bin/env python3
"""Merge alias/duplicate variants into one survivor, delete the rest.

Why duplicates exist (root causes):
  1. Multiple import paths (GemRate / SNK / eBay / Limitless / G10) each seeded
     a catalog_variant for the same physical printing before identity convergence.
  2. Printing convergence wrote catalog_variant_alias but left both rows live.
  3. Language rehash salted same 5-tuple rows (opaque_id unique) instead of merging.

Policy:
  - Score each side (prices + sales + pop + sources + images + locales).
  - Keep the richer row as survivor; reassign FK facts; delete victim.
  - review identity_mismatch (e.g. v794 Rare Candy) is NOT a mergeable duplicate.
  - incomplete without language is NOT deleted here.

Default dry-run; --write commits.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args  # noqa: E402

try:
    from qualified_pool_operator import load_env

    load_env()
except Exception:
    pass

REPORT = ROOT / "temp" / "purge-duplicate-variants-report.json"

# Tables that store variant_id and can reassign (handle uniques carefully)
WEIGHT_TABLES = (
    "market_price_observation",
    "market_sale_observation",
    "market_grader_population_observation",
    "market_image_asset",
    "catalog_source_identity",
    "catalog_variant_locale",
    "market_index_constituent",
    "catalog_story_pointer",
    "market_daily_sales_aggregate",
    "market_tracked_sales_aggregate",
    "market_gemrate_psa10_history",
    "market_gemrate_psa10_watchlist",
    "market_population_transport_observation",
    "market_candidate_daily_snapshot",
    "market_alert",
    "market_universe_member",
    "market_image_source_pointer",
    "catalog_identity_evidence",
    "catalog_provider_identity_alias",
)


def count_rows(cur: Any, table: str, vid: int) -> int:
    try:
        cur.execute(f"SELECT COUNT(*) AS n FROM `{table}` WHERE variant_id=%s", (vid,))
        return int(cur.fetchone()["n"])
    except Exception:
        return 0


def score_variant(cur: Any, vid: int) -> tuple[int, dict[str, int]]:
    w = {t: count_rows(cur, t, vid) for t in WEIGHT_TABLES}
    # Prefer market depth + multi-source identity
    s = (
        w["market_price_observation"]
        + w["market_sale_observation"]
        + w["market_grader_population_observation"] * 10
        + w["catalog_source_identity"] * 20
        + w["market_image_asset"] * 5
        + w["catalog_variant_locale"] * 8
        + w["market_index_constituent"] * 3
        + w["catalog_story_pointer"] * 4
        + w["market_gemrate_psa10_history"] * 2
    )
    return s, w


def reassign_simple(cur: Any, table: str, survivor: int, victim: int, stats: Counter) -> None:
    """Blind UPDATE variant_id; on unique fail, delete victim rows."""
    try:
        cur.execute(
            f"UPDATE `{table}` SET variant_id=%s WHERE variant_id=%s",
            (survivor, victim),
        )
        stats[f"moved_{table}"] += cur.rowcount
    except Exception:
        # delete victim leftovers that couldn't move
        cur.execute(f"DELETE FROM `{table}` WHERE variant_id=%s", (victim,))
        stats[f"deleted_conflict_{table}"] += cur.rowcount


def reassign_prices(cur: Any, survivor: int, victim: int, stats: Counter) -> None:
    # unique (variant_id, source_code, observed_date)
    cur.execute(
        """
        DELETE p FROM market_price_observation p
        INNER JOIN market_price_observation s
          ON s.variant_id=%s AND s.source_code=p.source_code AND s.observed_date=p.observed_date
        WHERE p.variant_id=%s
        """,
        (survivor, victim),
    )
    stats["price_conflict_deleted"] += cur.rowcount
    cur.execute(
        "UPDATE market_price_observation SET variant_id=%s WHERE variant_id=%s",
        (survivor, victim),
    )
    stats["price_moved"] += cur.rowcount


def reassign_sources(cur: Any, survivor: int, victim: int, stats: Counter) -> None:
    # PK (source_code, external_entity_id) — if same external already on survivor, drop victim row
    cur.execute(
        """
        DELETE v FROM catalog_source_identity v
        INNER JOIN catalog_source_identity s
          ON s.source_code=v.source_code AND s.external_entity_id=v.external_entity_id
         AND s.variant_id=%s
        WHERE v.variant_id=%s
        """,
        (survivor, victim),
    )
    stats["source_dup_deleted"] += cur.rowcount
    cur.execute(
        "UPDATE catalog_source_identity SET variant_id=%s WHERE variant_id=%s",
        (survivor, victim),
    )
    stats["source_moved"] += cur.rowcount


def reassign_locales(cur: Any, survivor: int, victim: int, stats: Counter) -> None:
    # PK (variant_id, locale_code) — only move missing locales
    cur.execute(
        """
        DELETE v FROM catalog_variant_locale v
        INNER JOIN catalog_variant_locale s
          ON s.variant_id=%s AND s.locale_code=v.locale_code
        WHERE v.variant_id=%s
        """,
        (survivor, victim),
    )
    stats["locale_dup_deleted"] += cur.rowcount
    cur.execute(
        "UPDATE catalog_variant_locale SET variant_id=%s WHERE variant_id=%s",
        (survivor, victim),
    )
    stats["locale_moved"] += cur.rowcount


def reassign_images(cur: Any, survivor: int, victim: int, stats: Counter) -> None:
    # unique (variant_id, image_kind, content_sha256)
    # Delete QC first for victim assets that collide with survivor (FK on image_asset_id)
    cur.execute(
        """
        DELETE q FROM market_image_qc q
        INNER JOIN market_image_asset v ON v.id=q.image_asset_id AND v.variant_id=%s
        INNER JOIN market_image_asset s
          ON s.variant_id=%s AND s.image_kind=v.image_kind AND s.content_sha256=v.content_sha256
        """,
        (victim, survivor),
    )
    stats["image_qc_dup_deleted"] += cur.rowcount
    cur.execute(
        """
        DELETE v FROM market_image_asset v
        INNER JOIN market_image_asset s
          ON s.variant_id=%s AND s.image_kind=v.image_kind AND s.content_sha256=v.content_sha256
        WHERE v.variant_id=%s
        """,
        (survivor, victim),
    )
    stats["image_dup_deleted"] += cur.rowcount
    # reassign remaining victim assets (QC stays on asset_id)
    cur.execute(
        "UPDATE market_image_asset SET variant_id=%s WHERE variant_id=%s",
        (survivor, victim),
    )
    stats["image_moved"] += cur.rowcount
    # PK (variant_id, image_kind, source_version_sha256)
    cur.execute(
        """
        DELETE v FROM market_image_source_pointer v
        INNER JOIN market_image_source_pointer s
          ON s.variant_id=%s AND s.image_kind=v.image_kind
         AND s.source_version_sha256=v.source_version_sha256
        WHERE v.variant_id=%s
        """,
        (survivor, victim),
    )
    stats["image_ptr_dup_deleted"] += cur.rowcount
    cur.execute(
        "UPDATE market_image_source_pointer SET variant_id=%s WHERE variant_id=%s",
        (survivor, victim),
    )


def reassign_index(cur: Any, survivor: int, victim: int, stats: Counter) -> None:
    # PK (index_snapshot_id, variant_id) — if both present, drop victim
    cur.execute(
        """
        DELETE v FROM market_index_constituent v
        INNER JOIN market_index_constituent s
          ON s.index_snapshot_id=v.index_snapshot_id AND s.variant_id=%s
        WHERE v.variant_id=%s
        """,
        (survivor, victim),
    )
    stats["index_dup_deleted"] += cur.rowcount
    cur.execute(
        "UPDATE market_index_constituent SET variant_id=%s WHERE variant_id=%s",
        (survivor, victim),
    )
    stats["index_moved"] += cur.rowcount


def reassign_sales(cur: Any, survivor: int, victim: int, stats: Counter) -> None:
    # unique (source_code, external_entity_id, transaction_fingerprint) — no variant in unique
    cur.execute(
        "UPDATE market_sale_observation SET variant_id=%s WHERE variant_id=%s",
        (survivor, victim),
    )
    stats["sales_moved"] += cur.rowcount


def delete_victim(cur: Any, survivor: int, victim: int, stats: Counter) -> None:
    # any remaining images: drop QC then assets
    cur.execute(
        """
        DELETE q FROM market_image_qc q
        INNER JOIN market_image_asset a ON a.id=q.image_asset_id
        WHERE a.variant_id=%s
        """,
        (victim,),
    )
    cur.execute("DELETE FROM market_image_asset WHERE variant_id=%s", (victim,))
    cur.execute("DELETE FROM market_image_source_pointer WHERE variant_id=%s", (victim,))
    # printing identity: keep survivor only
    cur.execute("DELETE FROM catalog_printing_identity WHERE variant_id=%s", (victim,))
    stats["printing_deleted"] += cur.rowcount
    # story pointer
    cur.execute("DELETE FROM catalog_story_pointer WHERE variant_id=%s", (victim,))
    # alias rows
    cur.execute(
        "DELETE FROM catalog_variant_alias WHERE duplicate_variant_id=%s OR canonical_variant_id=%s",
        (victim, victim),
    )
    stats["alias_rows_deleted"] += cur.rowcount
    # alerts reference candidate snapshots — clear/repoint before snapshot delete
    cur.execute(
        "UPDATE market_alert SET latest_snapshot_id=NULL WHERE variant_id=%s",
        (victim,),
    )
    cur.execute(
        "UPDATE market_alert SET variant_id=%s WHERE variant_id=%s",
        (survivor, victim),
    )
    stats["alert_moved"] += cur.rowcount

    # candidate snapshots: drop victim (alerts unlinked)
    cur.execute(
        "DELETE FROM market_candidate_daily_snapshot WHERE variant_id=%s",
        (victim,),
    )
    stats["candidate_snap_deleted"] += cur.rowcount

    for table in (
        "market_daily_sales_aggregate",
        "market_tracked_sales_aggregate",
        "market_gemrate_psa10_history",
        "market_gemrate_psa10_watchlist",
        "market_population_transport_observation",
        "market_universe_member",
        "market_grader_population_observation",
    ):
        reassign_simple(cur, table, survivor, victim, stats)

    for table, cols in (
        ("catalog_identity_evidence", ("variant_id",)),
        ("catalog_provider_identity_alias", ("variant_id",)),
        ("market_identity_review_queue", ("resolved_variant_id",)),
        ("market_identity_review_resolution", ("resolved_variant_id",)),
    ):
        try:
            for col in cols:
                cur.execute(
                    f"UPDATE `{table}` SET `{col}`=%s WHERE `{col}`=%s",
                    (survivor, victim),
                )
        except Exception:
            try:
                for col in cols:
                    cur.execute(f"DELETE FROM `{table}` WHERE `{col}`=%s", (victim,))
            except Exception:
                pass

    # any leftover rows with variant_id=victim
    for table in WEIGHT_TABLES + ("catalog_printing_identity", "catalog_story_pointer"):
        n = count_rows(cur, table, victim)
        if n:
            try:
                cur.execute(f"DELETE FROM `{table}` WHERE variant_id=%s", (victim,))
                stats[f"final_delete_{table}"] += cur.rowcount
            except Exception as exc:
                stats[f"final_delete_fail_{table}"] += 1
                raise RuntimeError(f"cannot clear {table} for victim={victim}: {exc}") from exc

    cur.execute("DELETE FROM catalog_variant WHERE id=%s", (victim,))
    stats["variants_deleted"] += cur.rowcount


def main() -> int:
    parser = argparse.ArgumentParser()
    add_connection_args(parser)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--report-out", type=Path, default=REPORT)
    args = parser.parse_args()

    conn = connection_from_args(args)
    stats: Counter = Counter()
    pairs_out: list[dict[str, Any]] = []

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM catalog_variant")
            before_n = int(cur.fetchone()["n"])

            cur.execute(
                """
                SELECT a.duplicate_variant_id AS dup_id, a.canonical_variant_id AS can_id,
                       a.reason_code,
                       d.canonical_name AS d_name, d.identity_status AS d_status,
                       c.canonical_name AS c_name, c.identity_status AS c_status
                FROM catalog_variant_alias a
                JOIN catalog_variant d ON d.id=a.duplicate_variant_id
                JOIN catalog_variant c ON c.id=a.canonical_variant_id
                ORDER BY a.duplicate_variant_id
                """
            )
            pairs = [dict(r) for r in cur.fetchall()]
            stats["alias_pairs"] = len(pairs)

            # process each pair independently; survivor may appear in multiple pairs
            deleted: set[int] = set()
            for p in pairs:
                dup = int(p["dup_id"])
                can = int(p["can_id"])
                if dup in deleted or can in deleted:
                    stats["skipped_already_deleted"] += 1
                    continue
                d_score, d_w = score_variant(cur, dup)
                c_score, c_w = score_variant(cur, can)
                if c_score > d_score or (c_score == d_score and can < dup):
                    survivor, victim = can, dup
                    flipped = False
                else:
                    survivor, victim = dup, can
                    flipped = True

                # Prefer confirmed status on survivor after merge
                rec = {
                    "survivor": survivor,
                    "victim": victim,
                    "flipped": flipped,
                    "d_score": d_score,
                    "c_score": c_score,
                    "d_name": p["d_name"],
                    "c_name": p["c_name"],
                    "reason": p["reason_code"],
                }
                pairs_out.append(rec)
                stats["merged"] += 1
                if flipped:
                    stats["flipped_to_richer"] += 1

                if not args.write:
                    continue

                reassign_prices(cur, survivor, victim, stats)
                reassign_sales(cur, survivor, victim, stats)
                reassign_sources(cur, survivor, victim, stats)
                reassign_locales(cur, survivor, victim, stats)
                reassign_images(cur, survivor, victim, stats)
                reassign_index(cur, survivor, victim, stats)
                delete_victim(cur, survivor, victim, stats)
                deleted.add(victim)

                # survivor must be confirmed if it has language
                cur.execute(
                    """
                    UPDATE catalog_variant
                    SET identity_status='confirmed'
                    WHERE id=%s AND card_language IS NOT NULL
                      AND card_language IN ('en','ja','ko','zhCN','zhTW')
                      AND identity_status IN ('alias','duplicate','review')
                    """,
                    (survivor,),
                )
                stats["survivor_promoted"] += cur.rowcount

            # purge empty alias table leftovers pointing at deleted
            if args.write:
                cur.execute(
                    """
                    DELETE a FROM catalog_variant_alias a
                    LEFT JOIN catalog_variant d ON d.id=a.duplicate_variant_id
                    LEFT JOIN catalog_variant c ON c.id=a.canonical_variant_id
                    WHERE d.id IS NULL OR c.id IS NULL
                    """
                )
                stats["orphan_alias_cleaned"] += cur.rowcount

            cur.execute("SELECT COUNT(*) AS n FROM catalog_variant")
            after_n = int(cur.fetchone()["n"])
            cur.execute(
                "SELECT identity_status, COUNT(*) n FROM catalog_variant GROUP BY identity_status ORDER BY n DESC"
            )
            status = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT COUNT(*) n FROM catalog_variant_alias")
            alias_left = int(cur.fetchone()["n"])

            # remaining same-tuple confirmed (should be 0)
            cur.execute(
                """
                SELECT COUNT(*) g FROM (
                  SELECT 1 FROM catalog_variant
                  WHERE identity_status='confirmed' AND card_language IS NOT NULL
                  GROUP BY tcg_code, card_language, set_name, collector_number, canonical_name
                  HAVING COUNT(*)>1
                ) t
                """
            )
            multi = int(cur.fetchone()["g"])

            if args.write:
                conn.commit()
            else:
                conn.rollback()

            report = {
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "write": bool(args.write),
                "whyDuplicates": [
                    "Multi-pipeline seed (GemRate/SNK/eBay/Limitless/G10) created separate variants for same printing",
                    "Printing convergence wrote alias links but left both catalog rows",
                    "Language rehash salted same 5-tuple instead of merging market facts",
                ],
                "beforeVariants": before_n,
                "afterVariants": after_n if args.write else before_n,
                "stats": dict(stats),
                "statusAfter": status,
                "aliasRowsLeft": alias_left if args.write else len(pairs),
                "confirmedSameTupleGroups": multi,
                "pairs": pairs_out,
                "notTouched": {
                    "review": "identity_mismatch rows (e.g. v794) kept for manual fix",
                    "incomplete": "missing language — not duplicates",
                },
            }
            args.report_out.parent.mkdir(parents=True, exist_ok=True)
            args.report_out.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        "write": args.write,
                        "stats": dict(stats),
                        "beforeVariants": before_n,
                        "afterVariants": report["afterVariants"],
                        "statusAfter": status,
                        "aliasRowsLeft": report["aliasRowsLeft"],
                        "confirmedSameTupleGroups": multi,
                    },
                    indent=2,
                    default=str,
                )
            )
            print(f"report {args.report_out}")
            return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
