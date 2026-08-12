#!/usr/bin/env python3
"""The 035 resolver must bind its manifest to today's complete universe."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import resolve_active_psa_identity as resolver  # noqa: E402


class Cursor:
    def __init__(self, variant_ids: list[int]) -> None:
        self.variant_ids = variant_ids

    def execute(self, _sql: str) -> None:
        self.sql = _sql
        return None

    def fetchall(self) -> list[dict[str, int]]:
        return [{"variant_id": variant_id} for variant_id in self.variant_ids]


def expect_drift(current: list[int], planned: list[int]) -> None:
    try:
        resolver.assert_current_universe_membership(
            Cursor(current), {"rows": [{"variantId": value} for value in planned]}
        )
    except RuntimeError as error:
        assert "universe membership drifted" in str(error)
    else:
        raise AssertionError("universe membership drift did not fire")


resolver.assert_current_universe_membership(
    Cursor([1, 2, 3]), {"rows": [{"variantId": 3}, {"variantId": 1}, {"variantId": 2}]}
)
expect_drift([1, 2, 3], [1, 2])
expect_drift([1, 2, 3], [1, 2, 3, 4])

partial = Cursor([7, 9])
resolver.assert_current_universe_membership(
    partial,
    {
        "scope": "incomplete-printing-identity",
        "rows": [{"variantId": 9}, {"variantId": 7}],
    },
)
assert "printing.identity_status" in partial.sql
print("active PSA resolution universe tests passed")
