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
    print("EXIT", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
