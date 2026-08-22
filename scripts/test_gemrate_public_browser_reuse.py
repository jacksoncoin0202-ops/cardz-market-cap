#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Public-page collection must open Chrome once per worker, not once per 25 cards.

2026-08-19/20: relaunch after WEBSITE_CHUNK hung the daily lane; first chunk
ok, next chromium.launch never returned, no manifest, POP stayed on 8/18.

Run: python -X utf8 scripts/test_gemrate_public_browser_reuse.py
"""
from __future__ import annotations

import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import gemrate_source as gs  # noqa: E402

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


class FakePage:
    def wait_for_timeout(self, _ms: int) -> None:
        return None


def fake_ids(n: int) -> list[str]:
    return [f"{i:040x}" for i in range(1, n + 1)]


def fake_payload(gid: str) -> dict[str, Any]:
    return {"gemrate_id": gid, "population_data": [{"grader": "psa", "grades": {"g10": 1}}]}


def run_collect(ids: list[str], *, workers: int, chunk_size: int) -> tuple[int, int]:
    launches = {"n": 0}

    @contextmanager
    def fake_session():
        launches["n"] += 1
        yield FakePage()

    def fake_fetch(_page, gid):
        return fake_payload(gid), None, False

    def fake_persist(_cards_dir, gid, _payload):
        return {}

    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(gs, "_gemrate_public_page", fake_session), patch.object(
            gs, "_fetch_card_once", fake_fetch
        ), patch.object(gs, "_persist_public_card_capture", fake_persist):
            result = gs.collect_public_card_details(
                ids,
                cards_dir=Path(tmp),
                delay=0.0,
                chunk_size=chunk_size,
                workers=workers,
            )
    return launches["n"], int(result["succeeded"])


def main() -> int:
    thirty = fake_ids(30)
    launches, succeeded = run_collect(thirty, workers=1, chunk_size=25)
    check("1 worker / 30 ids / chunk 25 launches once, not twice", launches, 1)
    check("1 worker collected all 30", succeeded, 30)

    launches2, succeeded2 = run_collect(thirty, workers=2, chunk_size=25)
    check("2 workers / 30 ids launches once per worker", launches2, 2)
    check("2 workers collected all 30", succeeded2, 30)

    source = (ROOT / "pipelines" / "gemrate_source.py").read_text(encoding="utf-8")
    check(
        "shard loop fetches on an open page, does not relaunch per chunk",
        "_fetch_card_pages_on_page(" in source
        and "with _gemrate_public_page() as page:" in source
        and "chunk_payloads, chunk_receipts = _chrome_card_pages_with_receipts(" not in source,
        True,
    )

    for line in FAILED:
        print(line)
    print(f"{CHECKS - len(FAILED)}/{CHECKS} checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
