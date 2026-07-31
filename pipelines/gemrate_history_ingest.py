#!/usr/bin/env python3
"""Import existing GemRate PSA-only history files into canonical MySQL."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args

DEFAULT_ROOT = ROOT / "data" / "private" / "gemrate" / "cards"
GEMRATE_ID = re.compile(r"[0-9a-f]{40}")


def extract_psa_history(document: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    data = document.get("data") or {}
    gemrate_id = str(data.get("universal_gemrate_id") or data.get("gemrate_id") or "")
    if not GEMRATE_ID.fullmatch(gemrate_id):
        raise ValueError("missing valid GemRate identity")
    population_data = (data.get("population") or {}).get("population_data") or {}
    psa = ((population_data.get("by_grader") or {}).get("psa") or {})
    rows = psa.get("history") or []
    if not isinstance(rows, list):
        raise ValueError("PSA history is not a list")
    return gemrate_id, rows


def ingest(connection, history_root: Path) -> dict[str, int]:
    files = sorted(history_root.glob("*/history_full.json"))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    observations = mapped_files = unmapped_files = 0
    with connection.cursor() as cursor:
        for path in files:
            document = json.loads(path.read_text(encoding="utf-8"))
            gemrate_id, rows = extract_psa_history(document)
            cursor.execute(
                """
                SELECT COALESCE(alias.canonical_variant_id, identity.variant_id) AS variant_id
                FROM catalog_source_identity AS identity
                LEFT JOIN catalog_variant_alias AS alias
                  ON alias.duplicate_variant_id=identity.variant_id
                WHERE identity.source_code='gemrate'
                  AND identity.external_entity_id=%s
                """,
                (gemrate_id,),
            )
            identity = cursor.fetchone()
            variant_id = int(identity["variant_id"]) if identity else None
            mapped_files += int(variant_id is not None)
            unmapped_files += int(variant_id is None)
            for row in rows:
                grades = row.get("grades") or {}
                payload_hash = hashlib.sha256(
                    json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                cursor.execute(
                    """
                    INSERT INTO market_gemrate_psa10_history
                        (gemrate_id, variant_id, observed_date, psa10_population,
                         psa_total_population, source_payload_sha256, ingested_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        variant_id=COALESCE(VALUES(variant_id), variant_id),
                        psa10_population=VALUES(psa10_population),
                        psa_total_population=VALUES(psa_total_population),
                        source_payload_sha256=VALUES(source_payload_sha256),
                        ingested_at=VALUES(ingested_at)
                    """,
                    (
                        gemrate_id,
                        variant_id,
                        str(row["date"]),
                        int(grades.get("psa_10") or 0),
                        int(row.get("total") or 0),
                        payload_hash,
                        now,
                    ),
                )
                observations += 1
    connection.commit()
    return {
        "files": len(files),
        "observations": observations,
        "mapped_files": mapped_files,
        "unmapped_files": unmapped_files,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history-root", type=Path, default=DEFAULT_ROOT)
    add_connection_args(parser)
    args = parser.parse_args()
    connection = connection_from_args(args)
    try:
        print(json.dumps(ingest(connection, args.history_root), sort_keys=True))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
