#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S12 leftover-5: Chinese PC language + local-history quotes must reach ranking.

2026-08-13 activate aborted acceptance_present_but_view_rejected. Two fields,
not the gate:

1. S8 lowercases language to `zhtw`; a set that only lists `zhTW` routes
   Chinese to SNK-primary and v35 becomes route=none despite 19 PC points.
2. S8 writes pc_psa10_local_history_v1 series observations. Ranking bootstrap
   only accepted pc_psa10_current_price_v1 `last`. leftover-5 had charts and
   zero live quote revisions. Legacy reconstructed quotes are excluded.

Do not relax _pc_print_signature_ok. Do not copy the language set (shape 22).
"""
from __future__ import annotations

from datetime import datetime, timezone
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from current_quote_revision import (  # noqa: E402
    bootstrap_from_eligible_observations,
    eligible_current_quote_revision_ddl,
    pc_guide_observation_predicate_sql,
    pc_price_language_ok,
    pc_price_language_sql,
    self_test,
)
from pc_psa10_price_materialize import (  # noqa: E402
    _quote_checked_at,
    materialize_local_history,
)
import rebuild_036  # noqa: E402

FAILURES: list[str] = []


def check(name: str, actual: object, expected: object) -> None:
    status = "ok" if actual == expected else "FAIL"
    if actual != expected:
        FAILURES.append(name)
    print(f"[{status}] {name}: got {actual!r}, want {expected!r}")


unit = self_test()
check("current_quote_revision.self_test", unit.get("ok"), True)
check("zhtw (S8 LOWER) is a PC price language", pc_price_language_ok("zhtw"), True)
check("zhTW catalog spelling is a PC price language", pc_price_language_ok("zhTW"), True)
check("ja is not a PC price language", pc_price_language_ok("ja"), False)

lang_sql = pc_price_language_sql("pi")
check("language SQL is case-insensitive", "LOWER(REPLACE(pi.card_language" in lang_sql, True)
check("language SQL lists zhtw", "zhtw" in lang_sql, True)

ddl = eligible_current_quote_revision_ddl()
check("ranking view DDL names the view", "operator_eligible_current_quote_revision" in ddl, True)
check("ranking view DDL allows zhtw", "zhtw" in ddl, True)
check(
    "ranking view DDL no longer hard-codes only en",
    "pi.card_language='en'" not in ddl.replace(" ", ""),
    True,
)

guide = pc_guide_observation_predicate_sql("p2", "so2")
check("guide predicate includes last-field contract", "pc_psa10_current_price_v1" in guide, True)
check("guide predicate includes local-history contract", "pc_psa10_local_history_v1" in guide, True)

boot_src = inspect.getsource(bootstrap_from_eligible_observations)
check(
    "bootstrap uses the shared guide predicate (not last-only)",
    "pc_guide_observation_predicate_sql" in boot_src,
    True,
)

accept_src = inspect.getsource(rebuild_036._activation_accept_history)
check(
    "S12 accept reapplies the ranking view from code",
    "apply_eligible_current_quote_revision_view" in accept_src,
    True,
)
check(
    "S12 accept bootstraps quotes after the new lock is current",
    "bootstrap_from_eligible_observations" in accept_src,
    True,
)

hist_src = inspect.getsource(materialize_local_history)
check(
    "local-history materialize mints the latest quote revision",
    "insert_quote_revision" in hist_src,
    True,
)
check(
    "local-history quote clock accepts S8 naive UTC datetimes",
    "_quote_checked_at" in hist_src,
    True,
)
naive = datetime(2026, 8, 1, 0, 0, 0)
check(
    "naive UTC series stamp is kept",
    _quote_checked_at(naive),
    naive,
)
aware = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)
check(
    "aware stamp becomes naive UTC",
    _quote_checked_at(aware),
    naive,
)

if FAILURES:
    print(f"\n{len(FAILURES)} failure(s): {FAILURES}")
    raise SystemExit(1)
print("\nall PC quote language / local-history checks passed")
