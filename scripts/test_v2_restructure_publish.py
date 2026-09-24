#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R2 2026-09-25: publishing the same generation twice is success, not an error.

A supersede rerun whose accept re-derives today's content gets today's
generation id back (db3308_ + sha256(date, content)).  live-confirm then asks
the outbox to record a generation live already serves.  That must answer with
the row that already carries it, idempotently, like the same-key replay.  The
rest of the outbox contract (a DIFFERENT generation supersedes atomically, the
same key refuses another generation) is pinned once, in
scripts/test_daily_chain_v2_supersede.py.

No MySQL, no network: the outbox is a recording fake behind chain_db.db().
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import daily_chain_v2_db as chain_db  # noqa: E402

DAY_TEXT = "2026-09-25"
LIVE_ROW = {
    "id": 7, "event_key": f"live.confirmed:{DAY_TEXT}", "event_type": "live.confirmed",
    "business_date": DAY_TEXT, "generation_id": "db3308_aaaaaaaaaaaaaaaa", "superseded": 0,
}
# The /2 rerun re-derived the generation live already serves.
UNCHANGED_EVENT = {
    "eventKey": f"live.confirmed:{DAY_TEXT}/2", "eventType": "live.confirmed",
    "businessDate": DAY_TEXT, "runId": f"cardz-v2:{DAY_TEXT}/2",
    "generationId": LIVE_ROW["generation_id"],
    "generatedAt": "2026-09-25T03:00:00+00:00", "activeCount": 1200,
    "degraded": False, "sourceHealth": {}, "liveUrl": "https://example.invalid",
    "contentSha256": "a" * 64, "occurredAt": "2026-09-25T03:00:01+00:00",
}


class FakeCursor:
    """The two outbox reads insert_live_event makes; any write is a failure."""

    def __init__(self, owner: "FakeConnection") -> None:
        self.owner = owner
        self._result: dict[str, Any] | None = None

    def execute(self, sql: str, params: Any = None) -> None:
        text = " ".join(str(sql).split())
        params = tuple(params or ())
        self.owner.statements.append(text.split(" ", 1)[0])
        row = self.owner.row
        if text.startswith("SELECT id,generation_id FROM publication_outbox"):
            self._result = row if params == (row["event_key"],) else None
        elif text.startswith("SELECT id,event_key,generation_id FROM publication_outbox"):
            self._result = row if params == (row["business_date"], row["event_type"]) else None
        else:
            # An UPDATE would un-live the row that carries these bytes; an
            # INSERT would collide with it on UNIQUE(date,type,generation).
            raise AssertionError(f"same-generation supersede must not write: {text[:80]}")

    def fetchone(self) -> dict[str, Any] | None:
        return self._result


class FakeConnection:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = dict(row)
        self.statements: list[str] = []
        self.committed = 0
        self.rolled_back = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1

    def close(self) -> None:
        pass


def insert(connection: FakeConnection, event: dict[str, Any]) -> tuple[int, bool]:
    saved = chain_db.load_env, chain_db.db
    chain_db.load_env = lambda: None
    chain_db.db = lambda: connection
    try:
        return chain_db.insert_live_event(event)
    finally:
        chain_db.load_env, chain_db.db = saved


outbox = FakeConnection(LIVE_ROW)
assert insert(outbox, UNCHANGED_EVENT) == (7, False)
assert outbox.statements == ["SELECT", "SELECT"], outbox.statements
# The commit only releases the FOR UPDATE locks; nothing was written.
assert outbox.committed == 1 and outbox.rolled_back == 0
# A retried live-confirm asks again and gets the same answer.
assert insert(outbox, UNCHANGED_EVENT) == (7, False)
assert outbox.committed == 2 and outbox.rolled_back == 0
print("POSITIVE_OK same-generation supersede returns the live row instead of raising")
