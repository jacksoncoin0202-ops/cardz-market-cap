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
