#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply 043 bootstrap: seed quote revisions + legacy reconstructed lineage."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from current_quote_revision import (
    bootstrap_from_eligible_observations,
    reconstruct_legacy_generation_quotes,
    self_test,
)
from rebuild_036 import connect, DEFAULT_CREDENTIALS_ENV, DAILY_CREDENTIALS_ENV


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--credentials-env",
        type=Path,
        default=DAILY_CREDENTIALS_ENV
        if DAILY_CREDENTIALS_ENV.exists()
        else DEFAULT_CREDENTIALS_ENV,
    )
    parser.add_argument("--self-test-only", action="store_true")
    args = parser.parse_args()
    if args.self_test_only:
        print(json.dumps(self_test(), sort_keys=True))
        return 0
    unit = self_test()
    conn = connect(args.credentials_env)
    try:
        with conn.cursor() as cur:
            boot = bootstrap_from_eligible_observations(cur)
            legacy = reconstruct_legacy_generation_quotes(cur)
            cur.execute("SELECT COUNT(*) AS n FROM market_current_quote_revision")
            total = int((cur.fetchone() or {}).get("n") or 0)
        conn.commit()
    finally:
        conn.close()
    print(
        json.dumps(
            {"ok": True, "unit": unit, "bootstrap": boot, "legacy": legacy, "totalRevisions": total},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
