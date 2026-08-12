#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Repository migration policy that must not depend on a live database.

Files in ``RETIRED_APPLIED_ONLY_MIGRATIONS`` are immutable incident evidence.
An existing ledger row is hash-checked, but a database that never applied the
file must never execute it.  Forward migrations supersede their effects.
"""
from __future__ import annotations

from typing import Literal, Mapping


RETIRED_APPLIED_ONLY_MIGRATIONS: Mapping[str, str] = {
    "046_legacy_quote_unique_owner.mysql.sql":
        "ff1d3a12514fdb0559d6687e3480a8b44abbb7b6d52a1525775bb46089fd8c00",
    "047_legacy_quote_nondestructive_recovery.mysql.sql":
        "ed532880bc13a9ba36a19f7c4690f0bba24342581896e97816e92708e8bce5d8",
    "047_restore_legacy_quote_append_only.mysql.sql":
        "64f83e10edcd4e508ccabca597cab7215e5830cbf065db76d19a6cac777313d6",
}


def assert_retired_hashes(repository_hashes: Mapping[str, str]) -> None:
    """Fail if immutable incident evidence is absent or byte-edited."""

    problems = [
        name
        for name, expected in RETIRED_APPLIED_ONLY_MIGRATIONS.items()
        if repository_hashes.get(name) != expected
    ]
    if problems:
        raise RuntimeError(
            "retired applied-only migration evidence changed: " + ", ".join(problems)
        )


def migration_action(
    migration_file: str,
    content_sha256: str,
    recorded_sha256: str | None,
) -> Literal["verified", "retired-skip", "execute"]:
    """Return the only permitted action for one repository migration."""

    if recorded_sha256 is not None:
        if recorded_sha256 != content_sha256:
            raise RuntimeError(f"applied migration content changed: {migration_file}")
        return "verified"
    if migration_file in RETIRED_APPLIED_ONLY_MIGRATIONS:
        expected = RETIRED_APPLIED_ONLY_MIGRATIONS[migration_file]
        if content_sha256 != expected:
            raise RuntimeError(f"retired migration content changed: {migration_file}")
        return "retired-skip"
    return "execute"
