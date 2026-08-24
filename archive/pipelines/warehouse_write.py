#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write pull-first warehouse rows for exact-bound cards.

Usage examples:
  python -X utf8 pipelines/warehouse_write.py --variant-id 19 --source-code snkrdunk --kind listing --payload-json '{"a":1}'
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
from qualified_pool_operator import db, load_env  # noqa: E402

BANNED = {"g10_kline"}


def utc_now_sql() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def sha(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def write_row(
    *,
    variant_id: int,
    source_code: str,
    external_entity_id: str,
    observation_kind: str,
    field_map: dict[str, Any],
    raw_payload: dict[str, Any] | None,
    ingest_run_key: str,
) -> dict[str, Any]:
    if source_code in BANNED or observation_kind.startswith("g10_kline"):
        raise SystemExit(f"banned source/kind: {source_code}/{observation_kind}")
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        payload = raw_payload if raw_payload is not None else field_map
        content = sha({"source": source_code, "ext": external_entity_id, "kind": observation_kind, "payload": payload})
        cur.execute(
            """
            INSERT INTO market_source_warehouse
              (variant_id, source_code, external_entity_id, observation_kind, observed_at,
               field_map_json, raw_payload_json, content_sha256, ingest_run_key)
            VALUES (%s,%s,%s,%s,%s,CAST(%s AS JSON),CAST(%s AS JSON),%s,%s)
            ON DUPLICATE KEY UPDATE
              field_map_json=VALUES(field_map_json),
              raw_payload_json=VALUES(raw_payload_json),
              observed_at=VALUES(observed_at)
            """,
            (
                variant_id,
                source_code,
                external_entity_id or "",
                observation_kind,
                utc_now_sql(),
                json.dumps(field_map, ensure_ascii=False, default=str),
                json.dumps(payload, ensure_ascii=False, default=str),
                content,
                ingest_run_key,
            ),
        )
        conn.commit()
        return {
            "variantId": variant_id,
            "sourceCode": source_code,
            "observationKind": observation_kind,
            "contentSha256": content,
            "status": "upserted",
        }
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant-id", type=int, required=True)
    ap.add_argument("--source-code", required=True)
    ap.add_argument("--external-entity-id", default="")
    ap.add_argument("--kind", required=True)
    ap.add_argument("--payload-json", required=True, help="JSON object string or @path")
    ap.add_argument("--run-key", default="manual")
    args = ap.parse_args()
    raw = args.payload_json
    if raw.startswith("@"):
        payload = json.loads(Path(raw[1:]).read_text(encoding="utf-8"))
    else:
        payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise SystemExit("payload must be object")
    # field_map = shallow scalars + nested kept as-is under payload keys
    field_map = {k: v for k, v in payload.items()}
    result = write_row(
        variant_id=args.variant_id,
        source_code=args.source_code,
        external_entity_id=args.external_entity_id,
        observation_kind=args.kind,
        field_map=field_map,
        raw_payload=payload,
        ingest_run_key=args.run_key,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
