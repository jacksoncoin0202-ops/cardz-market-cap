#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The exact release tree must run every guard before it starts a bake."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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


# ---------------------------------------------------------------------------
# 2026-09-25: the price DB gates run on the release path, after the quarantine
# receipt is regenerated and before the bake, as a bare top-level command.
DB_GATE_LINE = re.compile(
    r'^"\$TEST_PY" -X utf8 "\$SOURCE_REPO/scripts/run_all_tests\.py" --release-db-gates$', re.MULTILINE
)


def assert_release_db_gate(source: str) -> None:
    regen = source.index('"$TEST_PY" -X utf8 "$SOURCE_REPO/pipelines/pc_sale_title_quarantine.py"\n')
    gates = DB_GATE_LINE.findall(source)
    assert len(gates) == 1, f"exactly one top-level --release-db-gates line, found {len(gates)}"
    gate = DB_GATE_LINE.search(source).start()
    publish = source.index("\nwhile true; do\n  if publish_assets")
    assert regen < gate < publish, "the DB gate must run after the receipt regeneration and before the bake"


assert_release_db_gate(real_source)
gate_text = '"$TEST_PY" -X utf8 "$SOURCE_REPO/scripts/run_all_tests.py" --release-db-gates'
regen_text = '"$TEST_PY" -X utf8 "$SOURCE_REPO/pipelines/pc_sale_title_quarantine.py"\n'
for label, planted in (
    ("gate removed", real_source.replace(gate_text, "")),
    ("gate moved above the receipt regeneration",
     real_source.replace(gate_text, "").replace(regen_text, gate_text + "\n" + regen_text)),
    ("gate softened with || true", real_source.replace(gate_text, gate_text + " || true")),
    ("gate wrapped in an if", real_source.replace(gate_text, "if " + gate_text + "; then :; fi")),
):
    try:
        assert_release_db_gate(planted)
    except (AssertionError, ValueError):
        print(f"NEGATIVE_OK release DB gate: {label} was rejected")
    else:
        raise AssertionError(f"negative release DB gate fixture did not fire: {label}")

# Runner contract, DB-free.
assert runner.RELEASE_DB_GATES <= runner.NEEDS_DB, runner.RELEASE_DB_GATES - runner.NEEDS_DB
assert "test_price_lane_contracts.py" in runner.RELEASE_DB_GATES
for name in runner.RELEASE_DB_GATES:
    assert (ROOT / "scripts" / name).is_file(), name
combined = subprocess.run(
    [sys.executable, "-X", "utf8", str(RUNNER), "--release-db-gates", "--no-db"],
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
)
assert combined.returncode == 2 and "CARDZ_TEST_RESULT" not in combined.stdout, combined
saved_gates = runner.RELEASE_DB_GATES
runner.RELEASE_DB_GATES = {"_missing_gate.py"}
try:
    missing = runner.release_db_gate_results(timeout=5)
finally:
    runner.RELEASE_DB_GATES = saved_gates
assert [row[1] for row in missing] == ["FAIL"], missing
assert runner.test_result_document(missing)["failed"] == 1
print("POSITIVE_OK release DB gate: after the receipt, before the bake; a missing gate is FAIL")

# The verdicts the V2 classifier reads off this path.
sys.path.insert(0, str(ROOT / "pipelines"))
from daily_chain_v2_contract import classify_error  # noqa: E402

ok_doc = json.dumps(runner.test_result_document([("a", "PASS", 0.1, "")]))
red_doc = json.dumps(runner.test_result_document([("a", "PASS", 0.1, ""), ("b", "FAIL", 0.2, "x")]))
red_gate = f"CARDZ_TEST_RESULT {ok_doc}\n...\nCARDZ_TEST_RESULT {red_doc}\nrelease exit=1"
assert classify_error(red_gate, stage="publish").error_code == "PUBLISH_DETERMINISTIC"
db_down = (
    f"CARDZ_TEST_RESULT {ok_doc}\nrelease DB gate: MySQL 連唔到，唔出 verdict（可重試）："
    "OperationalError: (2003, \"Can't connect to MySQL server on '127.0.0.1:3308' (111)\")\nrelease exit=1"
)
assert classify_error(db_down, stage="publish").error_code == "PUBLISH_FAILED"
snapshot_ts = (ROOT / "apps" / "web" / "src" / "lib" / "live-db-snapshot.ts").read_text(encoding="utf-8")
thrown = re.search(r"`(image rejection registry contract mismatch: )", snapshot_ts)
assert thrown is not None, "the image rejection gate's message moved; re-pin the classifier"
image_red = f"release exit=1: Error: {thrown.group(1)}1 published (variant, sha) pair(s) are human-rejected: 260:dae7a026ec6c"
assert classify_error(image_red, stage="publish").error_code == "TERMINAL_CONTRACT"
print("POSITIVE_OK classifier: red DB gate and rejected image are terminal; an unreachable DB retries")

# publish_assets runs inside `if publish_assets; then`, where set -e is off: each
# step must return on failure itself, or a dead bake is followed by validators
# that judge yesterday's snapshot and the loop breaks as if published.
publish_fn = re.search(r"\npublish_assets\(\) \{\n(?:.*\n)*?\}\n", real_source)
assert publish_fn is not None, "publish_assets() not found in daily_public_release.sh"


def publish_after_failed_bake(function_text: str) -> tuple[str, list[str]]:
    # On Windows `bash` is WSL's bash.exe, which cannot open a C:/... path; a log
    # named relative to cwd works from both sides (WSL maps the cwd to /mnt/c/...).
    # Under the repo's ignored temp/, not %TEMP%: WSL does not resolve 8.3 names.
    with tempfile.TemporaryDirectory(dir=ROOT / "temp" if os.name == "nt" else None) as tmp:
        log = Path(tmp) / "calls.log"
        script = (
            "set -euo pipefail\n"
            "LOG=calls.log\n"
            'SOURCE_REPO=/s; RELEASE_REPO=/r; BOX_DST=/b; BOX_PREV=/p\n'
            'node() { case "$*" in *bake-public-snapshot*) echo bake >> "$LOG"; return 1;; '
            '*) echo "node $*" >> "$LOG";; esac; }\n'
            'python3() { echo "python3 $*" >> "$LOG"; }\n'
            + function_text
            + "if publish_assets; then echo PASS; else echo FAIL; fi\n"
        )
        done = subprocess.run(["bash", "-s"], input=script.encode("utf-8"), capture_output=True, timeout=60, cwd=tmp)
        assert done.returncode == 0, (done.returncode, done.stderr.decode("utf-8", "replace"))
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        return done.stdout.decode("utf-8", "replace").strip(), calls


if shutil.which("bash"):
    verdict, calls = publish_after_failed_bake(publish_fn.group(0))
    assert verdict == "FAIL" and calls == ["bake"], (verdict, calls)
    unguarded = publish_fn.group(0).replace(" || return", "")
    verdict, calls = publish_after_failed_bake(unguarded)
    assert verdict == "PASS" and len(calls) > 1, (verdict, calls)
    print("NEGATIVE_OK publish_assets without `|| return` swallows a dead bake (the harness sees it)")
    print("POSITIVE_OK publish_assets stops at a dead bake")
else:
    raise AssertionError("bash is required to execute publish_assets; this is not a skippable check")
