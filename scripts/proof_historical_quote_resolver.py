#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only integration proof for the verified canonical quote view."""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from rebuild_036 import DAILY_CREDENTIALS_ENV, connect


def main() -> int:
    conn = connect(DAILY_CREDENTIALS_ENV)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT resolution_kind, COUNT(*) AS n
                FROM operator_resolved_canonical_metric_quote
                GROUP BY resolution_kind
                """
            )
            by_kind = {str(row["resolution_kind"]): int(row["n"]) for row in cur.fetchall()}
            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM market_canonical_metric_acceptance m
                INNER JOIN market_metric_history_acceptance ph
                  ON ph.id=m.price_history_acceptance_id
                 AND ph.source_record_type='market_price_observation'
                INNER JOIN market_metric_history_acceptance poh
                  ON poh.id=m.population_history_acceptance_id
                INNER JOIN market_grader_population_observation pop
                  ON pop.id=poh.source_record_id
                WHERE pop.top_grade_population>0 AND m.market_cap_usd>0
                """
            )
            expected_legacy = int((cur.fetchone() or {}).get("n") or 0)
            # A map row is healthy in exactly two terminal states:
            #   resolved            -> must carry a well-formed evidence sha
            #   invalid_acceptance  -> cap<=0/pop<=0 acceptances can never mint a
            #                          quote; the resolver records the blocker and
            #                          clears evidence, and the 049 view excludes
            #                          them. Anything else (unverified, malformed
            #                          evidence, missing blocker) fails closed.
            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM market_legacy_quote_resolution
                WHERE NOT (
                    (resolution_status='resolved'
                     AND resolver_evidence_sha256 IS NOT NULL
                     AND resolver_evidence_sha256 REGEXP '^[0-9a-f]{64}$')
                 OR (resolution_status='invalid_acceptance'
                     AND resolver_evidence_sha256 IS NULL
                     AND blocker_code IS NOT NULL)
                )
                """
            )
            invalid_maps = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM market_legacy_quote_resolution
                WHERE resolution_status='invalid_acceptance'
                """
            )
            invalid_acceptance_maps = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute(
                """
                SELECT m.market_cap_usd, q.price_usd, pop.top_grade_population,
                       q.resolution_kind, q.resolver_evidence_sha256
                FROM market_canonical_metric_acceptance m
                INNER JOIN operator_resolved_canonical_metric_quote q
                  ON q.metric_acceptance_id=m.id
                INNER JOIN market_metric_history_acceptance poh
                  ON poh.id=m.population_history_acceptance_id
                INNER JOIN market_grader_population_observation pop
                  ON pop.id=poh.source_record_id
                WHERE m.id=70191
                """
            )
            van_gogh = cur.fetchone()
            if not van_gogh:
                raise SystemExit("historical metric 70191 does not resolve through verified view")
            resolved_price = Decimal(str(van_gogh["price_usd"]))
            if resolved_price != Decimal("2925.000000"):
                raise SystemExit(f"historical metric 70191 expected 2925 got {resolved_price}")
            expected_cap = resolved_price * Decimal(int(van_gogh["top_grade_population"]))
            if expected_cap != Decimal(str(van_gogh["market_cap_usd"])):
                raise SystemExit("historical metric 70191 no longer matches accepted market cap")

            cur.execute(
                """
                SELECT ranking_generation_sha256, COUNT(*) AS accepted,
                       SUM(canonical_market_rank IS NOT NULL) AS ranked,
                       SUM(q.metric_acceptance_id IS NOT NULL) AS resolved
                FROM market_canonical_metric_acceptance m
                LEFT JOIN operator_resolved_canonical_metric_quote q
                  ON q.metric_acceptance_id=m.id
                WHERE ranking_generation_sha256=(
                  SELECT ranking_generation_sha256
                  FROM market_canonical_metric_acceptance
                  ORDER BY accepted_at DESC LIMIT 1)
                GROUP BY ranking_generation_sha256
                """
            )
            current = dict(cur.fetchone() or {})
            if int(current.get("accepted") or 0) != int(current.get("resolved") or 0):
                raise SystemExit(f"current generation quote coverage incomplete: {current}")
            cur.execute(
                """
                SELECT COUNT(*) AS n FROM market_canonical_metric_acceptance m
                INNER JOIN market_metric_history_acceptance ph
                  ON ph.id=m.price_history_acceptance_id
                 AND ph.accepted_by='043-legacy-quote-repoint'
                """
            )
            destructive_repoints = int((cur.fetchone() or {}).get("n") or 0)
            if destructive_repoints:
                raise SystemExit(f"destructive legacy repoints remain: {destructive_repoints}")
    finally:
        conn.close()

    if by_kind.get("legacy", 0) != expected_legacy or invalid_maps:
        raise SystemExit(
            f"legacy resolver coverage mismatch expected={expected_legacy} "
            f"view={by_kind.get('legacy', 0)} invalidMaps={invalid_maps}"
        )
    print(json.dumps({
        "ok": True,
        "resolvedByKind": by_kind,
        "expectedLegacy": expected_legacy,
        "invalidMaps": invalid_maps,
        "invalidAcceptanceMaps": invalid_acceptance_maps,
        "vanGogh70191": {"priceUsd": str(resolved_price), "resolution": van_gogh["resolution_kind"]},
        "currentGeneration": current,
        "destructiveRepoints": destructive_repoints,
    }, ensure_ascii=False, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
