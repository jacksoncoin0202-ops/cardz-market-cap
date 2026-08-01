#!/usr/bin/env python3
"""Export the active universe lock's per-variant source identities.

The active-universe file (`tracked-universe.json`) is GemRate-keyed by design
(universe_authority._candidate_card).  The coverage audit's crosswalk rows are
keyed by their owning source (snkrdunk/ebay/...).  This export bridges the two:
one row per (lock member, source identity) so the audit can join source-keyed
crosswalk rows to active-universe cards without touching the hashed universe
file.

Regenerate whenever the universe lock changes (re-seal) or new source
identities are bound:

    .venv-backend/bin/python scripts/export_active_source_identities.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args  # noqa: E402

DEFAULT_OUT = ROOT / "data" / "runtime" / "private-source-map" / "active-source-identities.json"

MEMBER_SQL = """
SELECT
    member.variant_id,
    source.source_code,
    source.external_entity_id,
    variant.tcg_code,
    variant.canonical_name,
    variant.collector_number,
    variant.card_language
FROM market_universe_member AS member
JOIN market_universe_lock AS lock_row
    ON lock_row.id = member.universe_lock_id AND lock_row.is_current = 1
JOIN catalog_variant AS variant ON variant.id = member.variant_id
LEFT JOIN catalog_source_identity AS source ON source.variant_id = member.variant_id
ORDER BY member.variant_id, source.source_code
"""


PRICE_SQL = """
SELECT
    member.variant_id,
    price.source_code,
    price.observed_date,
    price.price_usd
FROM market_universe_member AS member
JOIN market_universe_lock AS lock_row
    ON lock_row.id = member.universe_lock_id AND lock_row.is_current = 1
JOIN market_price_observation AS price ON price.variant_id = member.variant_id
WHERE price.source_code IN ('snk_psa10', 'ebay', 'pricecharting')
ORDER BY member.variant_id, price.observed_date DESC
"""

# Sold-price fallback chain per the owner's A-then-B policy.  g10_kline and
# pricecharting are excluded: their price_usd scale does not agree with
# sold-price sources (orders of magnitude apart on the same variant).
PRICE_PRIORITY = ("snk_psa10", "ebay", "pricecharting")


SALE_PRICE_SQL = """
SELECT
    member.variant_id,
    sale.source_code,
    sale.sold_at,
    sale.unit_price_usd
FROM market_universe_member AS member
JOIN market_universe_lock AS lock_row
    ON lock_row.id = member.universe_lock_id AND lock_row.is_current = 1
JOIN market_sale_observation AS sale ON sale.variant_id = member.variant_id
WHERE sale.source_code IN ('snkrdunk', 'snk_grade', 'ebay')
    AND sale.unit_price_usd > 0
ORDER BY member.variant_id, sale.sold_at DESC
"""


def latest_variant_prices(connection) -> dict[int, dict[str, Any]]:
    candidates: dict[int, list[tuple[str, int, str, float]]] = {}

    def offer(variant_id: int, source: str, observed: str, price: float) -> None:
        if not observed or price <= 0:
            return
        candidates.setdefault(variant_id, []).append((observed, -PRICE_PRIORITY.index(source) if source in PRICE_PRIORITY else -9, source, price))

    with connection.cursor() as cursor:
        cursor.execute(PRICE_SQL)
        for row in cursor.fetchall():
            observed = row.get("observed_date")
            offer(
                int(row["variant_id"]),
                str(row["source_code"]),
                observed.isoformat() if hasattr(observed, "isoformat") else str(observed),
                float(row["price_usd"]),
            )
    # Last resort for variants whose freshest observation is old: the most
    # recent sold observation competes on date alone (sold prices only).
    with connection.cursor() as cursor:
        cursor.execute(SALE_PRICE_SQL)
        for row in cursor.fetchall():
            sold_at = row.get("sold_at")
            offer(
                int(row["variant_id"]),
                f"{row['source_code']}_last_sale",
                sold_at.date().isoformat() if hasattr(sold_at, "date") else str(sold_at)[:10],
                float(row["unit_price_usd"]),
            )
    best: dict[int, dict[str, Any]] = {}
    for variant_id, options in candidates.items():
        observed, _priority, source, price = max(options)
        best[variant_id] = {"sourceCode": source, "observedDate": observed, "priceUsd": price}
    return best


def export(connection) -> dict:
    with connection.cursor() as cursor:
        cursor.execute(MEMBER_SQL)
        rows = [dict(row) for row in cursor.fetchall()]
    cards = []
    for row in rows:
        source_code = str(row.get("source_code") or "").strip()
        external_id = str(row.get("external_entity_id") or "").strip()
        if not source_code or not external_id:
            continue
        cards.append(
            {
                "variantId": int(row["variant_id"]),
                "canonicalSourceCode": source_code,
                "canonicalExternalId": external_id,
                "tcg": str(row.get("tcg_code") or ""),
                "name": str(row.get("canonical_name") or ""),
                "collectorNumber": str(row.get("collector_number") or ""),
                "language": str(row.get("card_language") or ""),
            }
        )
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "authority": "canonical_mysql",
        "cards": cards,
        "variantPrices": {str(k): v for k, v in latest_variant_prices(connection).items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    add_connection_args(parser)
    args = parser.parse_args()
    connection = connection_from_args(args)
    try:
        document = export(connection)
    finally:
        connection.close()
    out = args.out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "cards": len(document["cards"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
