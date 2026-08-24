#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Full-catalog identity discovery ledger (043).

Every catalog variant gets an explicit discovery_status. Known gaps and inactive
records may not sit outside a cursor forever.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from rebuild_036 import connect, DEFAULT_CREDENTIALS_ENV, DAILY_CREDENTIALS_ENV

# The alias fold every other lane already applies: `snk` and `snk_psa10` are
# legacy spellings of `snkrdunk` (new_era_db_tidy.py, operator_fe_export.py
# both write `CASE WHEN source_code IN ('snk','snk_psa10') THEN 'snkrdunk'`).
# Sources this ledger does not read answer to no family and are skipped.
SOURCE_FAMILIES: dict[str, str] = {
    "pricecharting": "pricecharting",
    "snkrdunk": "snkrdunk",
    "snk": "snkrdunk",
    "snk_psa10": "snkrdunk",
}

_EMPTY_BINDING: dict[str, Any] = {"exact": {}, "nonexact": 0}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def canonical_source_code(source_code: Any) -> str:
    """The one name a provider answers to, '' when this ledger does not read it."""

    return SOURCE_FAMILIES.get(str(source_code or "").strip().lower(), "")


def aggregate_source_identities(
    rows: Iterable[Mapping[str, Any]],
) -> dict[int, dict[str, dict[str, Any]]]:
    """variant -> canonical source -> which PRODUCTS that provider binds it to.

    Exacts are counted over distinct (canonical source, external_entity_id)
    pairs, not over rows. catalog_source_identity is keyed
    (source_code, external_entity_id), so one SNKRDUNK product filed under two
    spellings of the same provider is two ROWS naming one product: counting
    rows read that as two bindings and raised multiple_exact_bindings against
    v1020 and v1040 on 2026-08-24, whose `snkrdunk` and `snk_psa10` rows both
    named external id 128132 / 128110. Ambiguity is two PRODUCTS.

    nonexact stays a row count: a non-exact row is a candidate awaiting review,
    and two candidates on one product really are two things to look at.
    """

    by_variant: dict[int, dict[str, dict[str, Any]]] = {}
    for raw in rows:
        family = canonical_source_code(raw.get("source_code"))
        if not family:
            continue
        status = raw.get("match_status")
        if status is None:  # mirrors SQL: NULL is neither exact nor non-exact
            continue
        agg = by_variant.setdefault(int(raw["variant_id"]), {}).setdefault(
            family, {"exact": {}, "nonexact": 0}
        )
        if str(status) != "exact":
            agg["nonexact"] += 1
            continue
        external = str(raw.get("external_entity_id") or "")
        # An exact row naming no product cannot be PROVED to be the same
        # binding as another one, so it folds into nothing: under-counting
        # exacts is how an ambiguity stops being reported.
        key = external or f"\x00{len(agg['exact'])}"
        agg["exact"].setdefault(key, {
            "externalId": external,
            "evidenceSha256": str(raw.get("evidence_sha256") or ""),
        })
    return by_variant


def _binding(
    bindings: dict[int, dict[str, dict[str, Any]]], variant_id: int, family: str
) -> dict[str, Any]:
    return bindings.get(variant_id, {}).get(family, _EMPTY_BINDING)


def rebuild_ledger(cursor: Any) -> dict[str, Any]:
    now = _now()
    # Read the bindings as ROWS and fold the provider aliases in Python. The
    # fold and the distinct-product count used to be two GROUP BY subqueries
    # that treated `snk`, `snk_psa10` and `snkrdunk` as three sources; see
    # aggregate_source_identities for what that cost.
    cursor.execute(
        """
        SELECT variant_id, source_code, match_status, external_entity_id, evidence_sha256
        FROM catalog_source_identity
        WHERE source_code IN ('pricecharting','snkrdunk','snk','snk_psa10')
        """
    )
    bindings = aggregate_source_identities(cursor.fetchall())
    # Current-universe membership is only is_current=1.
    cursor.execute(
        """
        SELECT v.id AS variant_id,
               CASE WHEN cur.variant_id IS NULL THEN 'inactive' ELSE 'active' END
                 AS catalog_status,
               pi.card_language
        FROM catalog_variant v
        LEFT JOIN catalog_printing_identity pi ON pi.variant_id=v.id
        LEFT JOIN (
          SELECT am.variant_id
          FROM market_universe_member am
          INNER JOIN market_universe_lock ul
            ON ul.id=am.universe_lock_id AND ul.is_current=1
        ) cur ON cur.variant_id=v.id
        """
    )
    rows = list(cursor.fetchall())
    counts: dict[str, int] = {}
    for raw in rows:
        variant_id = int(raw["variant_id"])
        catalog_status = str(raw["catalog_status"])
        language = str(raw.get("card_language") or "")
        pc = _binding(bindings, variant_id, "pricecharting")
        snk = _binding(bindings, variant_id, "snkrdunk")
        pc_exact = [pc["exact"][key] for key in sorted(pc["exact"])]
        snk_exact = [snk["exact"][key] for key in sorted(snk["exact"])]
        pc_exact_n = len(pc_exact)
        snk_exact_n = len(snk_exact)
        pc_nonexact_n = int(pc["nonexact"])
        snk_nonexact_n = int(snk["nonexact"])

        if pc_exact_n > 1 or snk_exact_n > 1:
            discovery = "identity_ambiguous"
            blocker = "multiple_exact_bindings"
            pc_status = (
                "ambiguous" if pc_exact_n > 1 else ("exact" if pc_exact_n == 1 else "missing")
            )
            snk_status = (
                "ambiguous" if snk_exact_n > 1 else ("exact" if snk_exact_n == 1 else "missing")
            )
        elif pc_exact_n == 1 or snk_exact_n == 1:
            if catalog_status == "active":
                discovery = "active_exact"
                blocker = None
            else:
                discovery = "inactive_exact"
                blocker = "inactive_with_exact_binding"
            pc_status = "exact" if pc_exact_n == 1 else ("nonexact" if pc_nonexact_n else "missing")
            snk_status = (
                "exact" if snk_exact_n == 1 else ("nonexact" if snk_nonexact_n else "missing")
            )
        elif pc_nonexact_n or snk_nonexact_n:
            discovery = "identity_ambiguous"
            blocker = "nonexact_only"
            pc_status = "nonexact" if pc_nonexact_n else "missing"
            snk_status = "nonexact" if snk_nonexact_n else "missing"
        elif catalog_status == "inactive":
            discovery = "inactive_unresolved"
            blocker = "inactive_no_exact_binding"
            pc_status = "missing"
            snk_status = "missing"
        else:
            discovery = "source_not_found"
            blocker = (
                "en_needs_pricecharting_or_snk"
                if language == "en"
                else "non_en_needs_snk"
            )
            pc_status = "missing"
            snk_status = "missing"

        detail = {
            "cardLanguage": language or None,
            "pcExactId": pc_exact[-1]["externalId"] if pc_exact else None,
            "pcExactEvidenceSha256": pc_exact[-1]["evidenceSha256"] if pc_exact else None,
            "pcExactIds": [item["externalId"] for item in pc_exact],
            "pcExactEvidenceSha256s": [item["evidenceSha256"] for item in pc_exact],
            "snkExactId": snk_exact[-1]["externalId"] if snk_exact else None,
            "snkExactEvidenceSha256": snk_exact[-1]["evidenceSha256"] if snk_exact else None,
            "snkExactIds": [item["externalId"] for item in snk_exact],
            "snkExactEvidenceSha256s": [item["evidenceSha256"] for item in snk_exact],
            "pcExactCount": pc_exact_n,
            "snkExactCount": snk_exact_n,
        }
        cursor.execute(
            """
            INSERT INTO market_identity_discovery_ledger
              (variant_id, catalog_status, discovery_status, pc_status, snk_status,
               blocker_code, detail_json, last_reviewed_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              catalog_status=VALUES(catalog_status),
              discovery_status=VALUES(discovery_status),
              pc_status=VALUES(pc_status),
              snk_status=VALUES(snk_status),
              blocker_code=VALUES(blocker_code),
              detail_json=VALUES(detail_json),
              -- only advance last_reviewed_at when status actually changes;
              -- otherwise daily rebuilds would always reset rotation order to id ASC
              last_reviewed_at=IF(
                VALUES(discovery_status)<>market_identity_discovery_ledger.discovery_status
                OR IFNULL(VALUES(blocker_code),'')<>IFNULL(market_identity_discovery_ledger.blocker_code,''),
                VALUES(last_reviewed_at),
                market_identity_discovery_ledger.last_reviewed_at
              ),
              updated_at=VALUES(updated_at)
              -- attempt_count/last_attempt_at/last_outcome/next_due_at/quarantine_until are
              -- owned by daily discovery attempts, never rebuilt here
            """,
            (
                variant_id,
                catalog_status,
                discovery,
                pc_status,
                snk_status,
                blocker,
                json.dumps(detail, ensure_ascii=False, sort_keys=True),
                now,
                now,
            ),
        )
        counts[discovery] = counts.get(discovery, 0) + 1
        counts[f"catalog:{catalog_status}"] = counts.get(f"catalog:{catalog_status}", 0) + 1

    cursor.execute("SELECT COUNT(*) AS n FROM market_identity_discovery_ledger")
    total = int((cursor.fetchone() or {}).get("n") or 0)
    cursor.execute("SELECT COUNT(*) AS n FROM catalog_variant")
    catalog = int((cursor.fetchone() or {}).get("n") or 0)
    if total != catalog:
        raise RuntimeError(
            f"discovery ledger incomplete: ledger={total} catalog={catalog}"
        )
    return {"catalog": catalog, "ledger": total, "byStatus": counts}


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild full identity discovery ledger")
    parser.add_argument(
        "--credentials-env",
        type=Path,
        default=DAILY_CREDENTIALS_ENV
        if DAILY_CREDENTIALS_ENV.exists()
        else DEFAULT_CREDENTIALS_ENV,
    )
    args = parser.parse_args()
    conn = connect(args.credentials_env)
    try:
        with conn.cursor() as cur:
            report = rebuild_ledger(cur)
        conn.commit()
    finally:
        conn.close()
    print(json.dumps({"ok": True, **report}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
