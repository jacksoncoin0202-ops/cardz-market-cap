#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""failure_ledger: constant paths are resolved once per process, ids unchanged.

A10 2026-08-23 [KNOWN, measured in WSL on the live tree]: normalize_script
resolved both the script and ROOT on every record_* call (~9 ms each on
/mnt/c) and _events_root re-resolved the ledger root on every read and write
(~10 ms); 1,176 "ok" cards cost ~28 s of pure path syscalls inside the c11
child.  Contract:

  * stable_failure_id is byte-identical to what it returned before the memo
    (golden values captured on the pre-change tree)
  * a second call with the same script / ledger root performs no resolve()
    (proved by making Path.resolve raise after the warm call)
  * distinct ledger roots still resolve to distinct event roots
  * record_failure -> read_events round trip lands where it always did

Run: python -X utf8 scripts/test_failure_ledger_memo.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import failure_ledger as fl  # noqa: E402

TEST_TMP = ROOT / "data" / "runtime" / "test-tmp"
TEST_TMP.mkdir(parents=True, exist_ok=True)

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


def main() -> int:
    # 1. golden ids (captured 2026-08-23 on the pre-memo tree; tree-independent
    #    inputs: a relative script and a path outside ROOT)
    check(
        "golden relative script",
        fl.stable_failure_id(
            source="pricecharting", stage="c11-sold-ingest",
            script="pipelines/c11_pc_sold_ingest.py", item_key="7096087",
        ),
        "failure_7b6bfa0a059998b7c7a1c90b",
    )
    check(
        "golden outside-root script",
        fl.stable_failure_id(source="s", stage="t", script="/nonexistent/elsewhere.py", item_key="k"),
        "failure_1ef351e2cbb776b152a2d0e3",
    )
    check(
        "absolute script normalises to a ROOT-relative posix path",
        fl.normalize_script(ROOT / "pipelines" / "c11_pc_sold_ingest.py"),
        "pipelines/c11_pc_sold_ingest.py",
    )

    # 2. memo: the warm call resolved; the second must not touch Path.resolve
    tmp_a = Path(tempfile.mkdtemp(prefix="ledger_a_", dir=str(TEST_TMP)))
    tmp_b = Path(tempfile.mkdtemp(prefix="ledger_b_", dir=str(TEST_TMP)))
    warm_root = fl._events_root(tmp_a)
    warm_script = fl.normalize_script(ROOT / "pipelines" / "failure_ledger.py")
    original_resolve = Path.resolve

    def boom(self, *args, **kwargs):
        raise AssertionError(f"Path.resolve called on the memoised path: {self}")

    Path.resolve = boom  # type: ignore[assignment]
    try:
        check("events root memoised", fl._events_root(tmp_a), warm_root)
        check("normalize_script memoised", fl.normalize_script(ROOT / "pipelines" / "failure_ledger.py"), warm_script)
        check(
            "stable_failure_id on a memoised script needs no resolve",
            fl.stable_failure_id(source="x", stage="y", script=ROOT / "pipelines" / "failure_ledger.py", item_key="1"),
            fl.stable_failure_id(source="x", stage="y", script=ROOT / "pipelines" / "failure_ledger.py", item_key="1"),
        )
    except AssertionError as error:
        FAILED.append(f"FAIL memo: {error}")
    finally:
        Path.resolve = original_resolve  # type: ignore[assignment]

    # 3. distinct roots stay distinct
    check("distinct ledger roots", fl._events_root(tmp_b) == fl._events_root(tmp_a), False)
    check("events root keeps the dirname", fl._events_root(tmp_b).name, fl.EVENTS_DIRNAME)

    # 4. round trip still lands under <root>/<EVENTS_DIRNAME>/<failure_id>/
    written = fl.record_failure(
        source="pricecharting", stage="unit", script="pipelines/failure_ledger.py",
        item_key="memo-roundtrip", reason_code="unit_test", message="memo", ledger_root=tmp_b,
    )
    failure_id = fl.stable_failure_id(
        source="pricecharting", stage="unit", script="pipelines/failure_ledger.py",
        item_key="memo-roundtrip",
    )
    check("event file lives under its failure id", Path(written).parent.name, failure_id)
    events = fl.read_events(ledger_root=tmp_b, failure_id=failure_id)
    check("one event written", len(events), 1)
    check("event id round trip", events[0]["eventId"], Path(written).stem.split("_", 1)[1])
    check(
        "event file under the memoised root",
        sorted((fl._events_root(tmp_b) / failure_id).glob("*.json")) != [],
        True,
    )

    if FAILED:
        print("\n".join(FAILED))
        print(f"CHECKS {CHECKS} FAILED {len(FAILED)}")
        return 1
    print(f"CHECKS {CHECKS} OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
