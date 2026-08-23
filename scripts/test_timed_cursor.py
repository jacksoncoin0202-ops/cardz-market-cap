"""_TimedCursor (daily-accept receipt clock) must delegate faithfully and rank statements."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipelines"))
import rebuild_036  # noqa: E402


class FakeCursor:
    rowcount = 7

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def execute(self, query, args=None):
        self.calls.append(("execute", query, args))
        if "SLOW" in query:
            time.sleep(0.05)
        return 1

    def executemany(self, query, args):
        self.calls.append(("executemany", query, args))
        return len(args)

    def fetchone(self):
        return {"n": 1}

    def fetchall(self):
        return [{"n": 1}]


def main() -> int:
    failed = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failed
        print(("OK" if ok else "FAIL"), label, detail)
        failed += 0 if ok else 1

    fake = FakeCursor()
    timed = rebuild_036._TimedCursor(fake)
    timed.execute("SELECT 1")
    timed.execute("SELECT   SLOW    FROM t", (1, 2))
    timed.executemany("INSERT INTO t VALUES (%s)", [(1,), (2,)])
    check("delegates execute/executemany", [c[0] for c in fake.calls] == ["execute", "execute", "executemany"])
    check("delegates args", fake.calls[1][2] == (1, 2))
    check(
        "delegates rowcount/fetchone/fetchall",
        timed.rowcount == 7 and timed.fetchone() == {"n": 1} and timed.fetchall() == [{"n": 1}],
    )
    slow = timed.slowest(2)
    check(
        "slowest first and sql whitespace collapsed",
        slow[0]["sql"] == "SELECT SLOW FROM t" and slow[0]["seconds"] >= 0.04,
        repr(slow),
    )
    check("limit respected", len(slow) == 2 and len(timed.slowest()) == 3)
    with timed as inner:
        check("context manager returns proxy", inner is timed)

    # A05 2026-08-23: totals() aggregates by statement shape, and the key is
    # anchored on the SELECT list so INSERT ... SELECT statements that share a
    # column list no longer collapse into one bucket.
    agg = rebuild_036._TimedCursor(FakeCursor())
    head = "INSERT INTO market_metric_history_acceptance (" + ", ".join(f"col{i}" for i in range(20)) + ")"
    agg.execute(head + " SELECT a.one FROM a")
    agg.execute(head + " SELECT b.two FROM b")
    for _ in range(3):
        agg.execute("SELECT 1")
    keys = {row["sql"] for row in agg.totals()}
    check("INSERT ... SELECT keys differ on the SELECT list", len(keys) == 3, repr(keys))
    check("key keeps the INSERT head", all(k.startswith("INSERT INTO market_metric_history_") for k in keys if "SELECT a" in k or "SELECT b" in k))
    totals = {row["sql"]: row for row in agg.totals()}
    check("totals count repeated statement", totals["SELECT 1"]["count"] == 3, repr(totals.get("SELECT 1")))
    check("totals limit", len(agg.totals(2)) == 2)
    check("slowest uses the same key", agg.slowest(1)[0]["sql"] in keys)
    print("EXIT", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
