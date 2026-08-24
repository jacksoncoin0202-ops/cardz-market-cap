#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The exact release tree must run every guard before it starts a bake."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "scripts" / "daily_public_release.sh"
RUNNER = ROOT / "scripts" / "run_all_tests.py"


def assert_release_guard(source: str) -> None:
    merge = source.index('git -C "$RELEASE_REPO" merge --ff-only FETCH_HEAD')
    authority_guard = source.index('"$SOURCE_REPO/scripts/run_all_tests.py"')
    guard = source.index("$RELEASE_REPO/scripts/run_all_tests.py")
    bake = source.index('CARDZ_REPO_ROOT="$SOURCE_REPO" node "$RELEASE_REPO/scripts/bake-public-snapshot.mjs"')
    assert merge < authority_guard < guard < bake, "both authority and release guards must run before bake"
    assert "--no-db" in source
    assert "--skip-fe" in source
    assert "validate_daily_release" in source
    assert 'TEST_PY="/home/jackson0202/cardz-market-cap/.venv-backend/bin/python"' in source


real_source = RELEASE.read_text(encoding="utf-8")

# Negative fixture: this is the former production shape. Prove the contract
# actually rejects it in the same deterministic test invocation.
without_guard = real_source.replace(
    "$RELEASE_REPO/scripts/run_all_tests.py",
    "$RELEASE_REPO/scripts/missing_guard.py",
)
try:
    assert_release_guard(without_guard)
except (AssertionError, ValueError):
    print("NEGATIVE_OK release chain without the no-DB guard was rejected")
else:
    raise AssertionError("negative release-chain fixture did not fire")

without_authority = real_source.replace(
    '"$SOURCE_REPO/scripts/run_all_tests.py"',
    '"$SOURCE_REPO/scripts/missing_authority_guard.py"',
)
try:
    assert_release_guard(without_authority)
except (AssertionError, ValueError):
    print("NEGATIVE_OK release chain without the authority-tree guard was rejected")
else:
    raise AssertionError("negative authority-tree guard fixture did not fire")

assert_release_guard(real_source)
spec = importlib.util.spec_from_file_location("cardz_run_all_tests_contract", RUNNER)
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
assert runner.SCRIPT_SELF_TEST_ENTRIES.get("validate_daily_release.py") == ["--self-test"]
fixture = [("one", "PASS", 0.1, ""), ("two", "FAIL", 0.2, "boom"), ("three", "SKIP", 0.0, "fixture")]
assert runner.test_result_document(fixture) == {
    "contract": "cardz-test-result-v1", "entries": 3, "passed": 1,
    "failed": 1, "skipped": 1, "timeouts": 0,
}
print("POSITIVE_OK release checkout runs all no-DB guards before bake")
