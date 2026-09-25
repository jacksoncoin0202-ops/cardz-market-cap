#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""What the price-quarantine release lane is allowed to touch.

The release exists because a flag set by a superseded guess outlived the guess.
These tests hold the two properties that keep it from becoming the next such
flag: it can only ever free rows whose identity is proven today, and it refuses
to report a number it did not actually write.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import rebuild_036 as R  # noqa: E402

FAILED: list[str] = []


def check(label: str, got: object, want: object) -> None:
    if got == want:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}: got {got!r}, want {want!r}")


# 1. The two statements are one predicate.
#
# A count that drifts from the update is how a receipt ends up describing a
# database that never existed, so they are composed from the same fragments
# rather than written twice.
for name, sql in (
    ("count", R.COUNT_RELEASABLE_PRICE_ROWS_SQL),
    ("update", R.RELEASE_COLLATERAL_PRICE_QUARANTINE_SQL),
):
    check(f"{name} keeps the quarantined+priced filter",
          R._RELEASABLE_PRICE_ROWS_WHERE in sql, True)
    check(f"{name} demands an exact binding",
          "LOWER(si.match_status) = 'exact'" in sql, True)
    check(f"{name} demands the operator strict identity",
          "operator_strict_source_identity osi" in sql, True)
    check(f"{name} matches the provider item the row came from",
          "si.external_entity_id = p.source_external_entity_id" in sql, True)

check("only quarantined rows are eligible",
      "p.metric_status = 'quarantined'" in R._RELEASABLE_PRICE_ROWS_WHERE, True)
check("the update writes nothing but ready",
      R.RELEASE_COLLATERAL_PRICE_QUARANTINE_SQL.count("SET"), 1)
check("no run-scoped clause survives",
      "last_run_id" in R.RELEASE_COLLATERAL_PRICE_QUARANTINE_SQL, False)


# 2. The count/update mismatch guard actually fires.
#
# A guard nobody has watched fire is a guard nobody knows is wired up, and this
# repo has shipped at least one of those. A fake connection lets the two numbers
# disagree on demand.
class _Cursor:
    def __init__(self, owner: "_Conn") -> None:
        self.owner = owner
        self.rowcount = 0

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, args: object = None) -> None:
        self.rowcount = (
            self.owner.counted if sql.lstrip().startswith("SELECT")
            else self.owner.updated
        )

    def fetchone(self) -> dict[str, int]:
        return {"rows_n": self.owner.counted, "variants_n": 1}


class _Conn:
    def __init__(self, counted: int, updated: int) -> None:
        self.counted, self.updated = counted, updated
        self.committed = self.rolled_back = False

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


agreeing = _Conn(counted=7, updated=7)
check("agreeing counts return the released rows",
      R.release_collateral_price_quarantine(agreeing), {"rows": 7, "variants": 1})
check("agreeing counts commit", agreeing.committed, True)

disagreeing = _Conn(counted=7, updated=3)
try:
    R.release_collateral_price_quarantine(disagreeing)
except SystemExit as exc:
    check("a mismatch stops the run", "counted 7 rows but updated 3" in str(exc), True)
else:
    FAILED.append("a mismatch stops the run")
    print("FAIL a mismatch stops the run: no SystemExit")

check("nothing was committed on mismatch", disagreeing.committed, False)


# 3. The demotion is the release's mirror, never its twin.
#
# A ready row whose card is not proven to be its item is quarantined; the
# release frees a quarantined row whose card is. Disjoint predicates are what
# stop a row flapping between the two in one pass.
for name, sql in (
    ("count", R.COUNT_UNPROVEN_READY_PRICE_ROWS_SQL),
    ("update", R.QUARANTINE_UNPROVEN_READY_PRICE_ROWS_SQL),
):
    check(f"demote {name} keeps the shared predicate",
          R._UNPROVEN_READY_PRICE_ROWS_WHERE in sql, True)
check("demotion only reads ready rows",
      "p.metric_status = 'ready'" in R._UNPROVEN_READY_PRICE_ROWS_WHERE, True)
check("demotion requires the strict identity to be ABSENT",
      "NOT EXISTS (SELECT 1 FROM operator_strict_source_identity osi"
      in R._UNPROVEN_READY_PRICE_ROWS_WHERE, True)
check("demotion matches the provider item the row came from",
      "osi.external_entity_id = p.source_external_entity_id"
      in R._UNPROVEN_READY_PRICE_ROWS_WHERE, True)
check("demotion writes nothing but quarantined",
      R.QUARANTINE_UNPROVEN_READY_PRICE_ROWS_SQL.count("SET"), 1)
check("demotion SET is quarantined",
      "SET p.metric_status = 'quarantined'" in R.QUARANTINE_UNPROVEN_READY_PRICE_ROWS_SQL,
      True)
# One source mapping for both halves. The release used to map only snk /
# snk_psa10, so a `*_sales` row demoted here could never have come back.
check("release and demotion map storage codes the same way",
      R._PRICE_ROW_IDENTITY_SOURCE_SQL in R._RELEASABLE_PRICE_ROWS_JOIN
      and R._PRICE_ROW_IDENTITY_SOURCE_SQL in R._UNPROVEN_READY_PRICE_ROWS_WHERE,
      True)
for code in ("snkrdunk_sales", "pricecharting_sales", "snk_psa10"):
    check(f"mapping covers {code}", f"'{code}'" in R._PRICE_ROW_IDENTITY_SOURCE_SQL, True)
    check(f"demotion scope covers {code}",
          f"'{code}'" in R._UNPROVEN_READY_PRICE_ROWS_WHERE, True)
check("demotion never touches other sources (ebay/tcgfish/g10)",
      any(f"'{code}'" in R._UNPROVEN_READY_PRICE_ROWS_WHERE
          for code in ("ebay", "tcgfish", "g10_kline")), False)

agreeing = _Conn(counted=5, updated=5)
check("agreeing demotion returns the quarantined rows",
      R.quarantine_unproven_price_rows(agreeing), {"rows": 5, "variants": 1})
check("agreeing demotion commits", agreeing.committed, True)
disagreeing = _Conn(counted=5, updated=2)
try:
    R.quarantine_unproven_price_rows(disagreeing)
except SystemExit as exc:
    check("a demotion mismatch stops the run", "counted 5 rows but updated 2" in str(exc), True)
else:
    FAILED.append("a demotion mismatch stops the run")
    print("FAIL a demotion mismatch stops the run: no SystemExit")
check("nothing was committed on demotion mismatch", disagreeing.committed, False)

# Both call sites run the demotion; a helper nobody calls is no helper.
_src = (ROOT / "pipelines" / "rebuild_036.py").read_text(encoding="utf-8")
check("both release call sites demote first",
      _src.count("quarantine_unproven_price_rows(conn)"), 2)


print()
if FAILED:
    print(f"{len(FAILED)} FAILED: {FAILED}")
    raise SystemExit(1)
print("price quarantine release rules hold")
