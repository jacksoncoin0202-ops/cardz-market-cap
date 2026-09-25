#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Error-classification and worker-receipt fixtures for CARDZ Daily Chain V2.

Package v2s-classify of the 2026-08-23 structural audit: P1-2, P1-4, P2-15,
P2-6, P2-5, P2-7 and the first step of P2-4.  Every check below fires on the
shape the audit measured and stays quiet on the healthy one.  No Telegram,
browser, MySQL, push, or deploy occurs: `collect_control` is a fake module, the
journal lives in a private temp directory, alerts run in dry-run mode, and the
only release script executed is a copy whose lock file is a temp path.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # Windows has no flock; the release lock is a WSL fixture
    fcntl = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import daily_chain_v2 as chain_module  # noqa: E402
import cardz_db_config as db_config  # noqa: E402
from daily_chain_v2 import DailyChainV2  # noqa: E402
from daily_chain_v2_adapters import build_default_registry  # noqa: E402
from daily_chain_v2_contract import (  # noqa: E402
    INFRA_RETRY_SECONDS,
    PUBLISH_ASSET_MAX_ATTEMPTS,
    PUBLISH_LEG_SECONDS,
    PUBLISH_LOCK_EXIT_CODE,
    PUBLISH_LOCK_MARKER,
    PUBLISH_RETRY_SECONDS,
    TRANSIENT_RETRY_SECONDS,
    RetryDecision,
    SourceTask,
    clamp_retry_at,
    classify_error,
)
from daily_chain_v2_journal import Journal  # noqa: E402
import daily_chain_v2_worker as v2worker  # noqa: E402

DAY = date(2026, 8, 20)
RUN_ID = f"cardz-v2:{DAY.isoformat()}"
WORKSPACE = Path(tempfile.mkdtemp(prefix="cardz-v2s-classify-"))


def cleanup() -> None:
    shutil.rmtree(WORKSPACE, ignore_errors=True)


def decide(text: str, *, stage: str = "source") -> tuple[str, bool, tuple[int, ...]]:
    decision = classify_error(text, stage=stage)
    return decision.error_code, decision.terminal, decision.delays_seconds


def new_journal(name: str) -> Journal:
    journal = Journal(WORKSPACE / f"{name}.sqlite3")
    journal.initialise()
    journal.ensure_run(
        business_date=DAY.isoformat(),
        source_cutoff_at="2026-08-20T01:15:00+00:00",
        sla_at="2026-08-20T02:00:00+00:00",
        final_at="2026-08-20T08:00:00+00:00",
    )
    return journal


def fake_collect(registry_rows: list[dict[str, Any]], report: dict[str, Any]) -> types.ModuleType:
    module = types.ModuleType("collect_control")
    module.REGISTRY_PATH = Path("fixture-registry.jsonl")
    module._jsonl_rows = lambda path: list(registry_rows)
    module.cmd_incr = lambda **kwargs: dict(report)
    module.cmd_stock = lambda **kwargs: dict(report)
    return module


@contextlib.contextmanager
def collect_module(module: types.ModuleType):
    previous = sys.modules.get("collect_control")
    sys.modules["collect_control"] = module
    try:
        yield
    finally:
        if previous is None:
            sys.modules.pop("collect_control", None)
        else:
            sys.modules["collect_control"] = previous


try:
    # ------------------------------------------------------------------ P1-4
    # Measured 2026-08-23: every one of these read as TERMINAL_CONTRACT because
    # "nan" is a substring of maintenance/governance and "forbidden" of a CF
    # 403.  A zero-retry terminal can only be reopened by hand.
    for token in (
        "SNKRDUNK is under maintenance, retry later",
        "HTTP 503 Service Unavailable: scheduled maintenance window",
        "governance check failed",
        "finance rate feed timeout",
        "nanjing card lookup failed",
        "HTTP 403 Forbidden (cloudflare)",
    ):
        code, terminal, _ = decide(token)
        assert code != "TERMINAL_CONTRACT" and not terminal, (token, code, terminal)

    assert decide("HTTP 503 Service Unavailable: scheduled maintenance window")[0] == "TRANSIENT_SOURCE"
    for token in (
        "HTTP 403 Forbidden (cloudflare)",
        "401 unauthorized: session expired",
        "authentication rejected",
    ):
        code, terminal, delays = decide(token)
        assert code == "AUTH_OR_BLOCKED" and not terminal, (token, code, terminal)
        assert delays == INFRA_RETRY_SECONDS, (token, delays)
    # Publish is not an escape hatch for the same class of fault.
    assert decide("authentication rejected", stage="publish")[0] == "AUTH_OR_BLOCKED"

    # Still terminal: a real numeric/contract fault and a real key rejection.
    for token in (
        "FX schema contract: stale last-good fetchedAt=2026-08-23T00:00:00+00:00",
        "migration content changed for 051",
        "MIGRATION HASH mismatch",
        "migration checksum does not match recorded bytes",
        "invalid api key",
        "contract mismatch: registry adapter drifted",
        "illegal numeric NaN in quote payload",
        "rate=Infinity rejected by the loader",
        "quote value nan is not a number",
    ):
        code, terminal, _ = decide(token)
        assert code == "TERMINAL_CONTRACT" and terminal, (token, code, terminal)
    print("POSITIVE_OK P1-4 maintenance/governance/403 stay retryable while numeric and migration faults stay terminal")

    # ------------------------------------------------------------------ P1-2
    # 2026-08-23 release attempts 2-6: six byte-identical 5940-byte logs, one
    # failing mjs test, 68.8 minutes of ladder sleep before the first terminal.
    verdict = (
        "release exit=1: PASS scripts/test-public-surface-gate.mjs\n"
        "FAIL scripts/test-fe-share-file.mjs\n"
        'CARDZ_TEST_RESULT {"contract":"cardz-test-result-v1","entries":73,"passed":65,"failed":1,"skipped":7,"timeouts":0}\n'
    )
    code, terminal, delays = decide(verdict, stage="publish")
    assert code == "PUBLISH_DETERMINISTIC" and terminal and delays == (), (code, terminal, delays)
    old_human_summary = "release exit=1: 65/66 passed, 1 failed, 7 skipped"
    old_code, old_terminal, old_delays = decide(old_human_summary, stage="publish")
    assert old_code == "PUBLISH_FAILED" and not old_terminal and old_delays == PUBLISH_RETRY_SECONDS
    malformed = 'release exit=1: CARDZ_TEST_RESULT {"contract":"cardz-test-result-v1","failed":1}'
    assert decide(malformed, stage="publish")[0] == "PUBLISH_FAILED"
    # Audit 6 #4: the same tokens are ordinary inside a source worker traceback.
    source_code_, source_terminal, source_delays = decide(verdict)
    assert not source_terminal and not source_code_.startswith("PUBLISH"), (source_code_, source_terminal)
    assert source_delays == TRANSIENT_RETRY_SECONDS

    # Audit 6 #5: tests all green, git push failed -> still retryable.
    push_failure = (
        'release exit=1: CARDZ_TEST_RESULT {"contract":"cardz-test-result-v1","entries":73,"passed":66,"failed":0,"skipped":7,"timeouts":0}\n'
        "error: failed to push some refs to 'github.com:cardz/market-cap.git'\n"
    )
    code, terminal, delays = decide(push_failure, stage="publish")
    assert code == "PUBLISH_FAILED" and not terminal and delays == PUBLISH_RETRY_SECONDS, (code, terminal, delays)
    assert decide("release exit=1: fatal: unable to access remote: HTTP 502", stage="publish")[0] == "PUBLISH_FAILED"

    for deterministic in (
        "release exit=1: TypeError: cards.map is not a function\n",
        "release exit=1: ModuleNotFoundError: No module named 'rebuild_036'\n",
        "release exit=1: Error: ReferenceError: snapshot is not defined\n",
        "release exit=1: SyntaxError: unexpected token\n",
        "release exit=127: bash: node: command not found\n",
    ):
        code, terminal, delays = decide(deterministic, stage="publish")
        assert code == "PUBLISH_DETERMINISTIC" and terminal and delays == (), (deterministic, code)
        assert not decide(deterministic)[1], deterministic
    # A word that merely contains an error class name is not a verdict.
    assert decide("release exit=1: prototypeerrors.mjs reported nothing", stage="publish")[0] == "PUBLISH_FAILED"
    print("POSITIVE_OK P1-2 deterministic publish verdicts are terminal on attempt 1 and only inside the publish branch")

    # ----------------------------------------------------------------- P2-15
    # The legacy publisher holds the same flock.  Under `set -euo pipefail`
    # `flock -n 9` exited 1 with no output, so V2 read "release exit=1: " and
    # burned the publish ladder waiting for a human to finish publishing.
    lock_tools = fcntl is not None and shutil.which("bash") and shutil.which("flock")
    lock_dir = WORKSPACE / "release-lock"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_copy_root = lock_dir / "repo"
    (lock_copy_root / "scripts").mkdir(parents=True, exist_ok=True)
    fixture_lock = lock_dir / "fixture-release.lock"
    release_source = (ROOT / "scripts" / "daily_public_release.sh").read_text(encoding="utf-8")
    assert 'LOCK_FILE="/tmp/cardz-market-cap-daily-release.lock"' in release_source
    release_copy = lock_copy_root / "scripts" / "daily_public_release.sh"
    release_copy.write_text(
        release_source.replace(
            'LOCK_FILE="/tmp/cardz-market-cap-daily-release.lock"',
            f'LOCK_FILE="{fixture_lock}"',
        ),
        encoding="utf-8",
        newline="\n",
    )
    lock_stderr = f"daily release: another publisher holds {fixture_lock}"
    lock_exit = PUBLISH_LOCK_EXIT_CODE
    if lock_tools:
        # This process is the other publisher: an flock(2) held here is the
        # same lock the script takes, and Python's O_CLOEXEC keeps the child
        # from inheriting it.
        holder = fixture_lock.open("w", encoding="utf-8")
        try:
            fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            blocked = subprocess.run(
                [
                    "bash", str(release_copy),
                    "--v2-run-id=cardz-v2:2026-08-20",
                    "--v2-business-date=2026-08-20",
                    "--v2-expected-generation=fixture-generation",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        finally:
            holder.close()
        lock_exit = blocked.returncode
        lock_stderr = blocked.stderr.strip()
        assert lock_exit == PUBLISH_LOCK_EXIT_CODE, (lock_exit, lock_stderr)
        assert "another publisher holds" in lock_stderr, lock_stderr
    else:
        print("SKIP_NO_FLOCK release-lock execution needs bash+flock; classifier half still runs")
    # The orchestrator turns that exit into one error string; the classifier
    # must read it as contention, not as a broken release.
    held_text = f"RuntimeError:release exit={lock_exit}: {lock_stderr}"
    code, terminal, delays = decide(held_text, stage="publish")
    assert code == "PUBLISH_LOCK_HELD" and not terminal and delays == INFRA_RETRY_SECONDS, (code, terminal, delays)
    marked = f"RuntimeError:release exit=75: {PUBLISH_LOCK_MARKER} {lock_stderr}"
    assert decide(marked, stage="publish")[0] == "PUBLISH_LOCK_HELD"
    # Publish-branch scoped: the same words in a source worker are not a lock.
    assert decide(held_text)[0] != "PUBLISH_LOCK_HELD"
    print("POSITIVE_OK P2-15 a held release lock exits 75 with a reason and classifies as contention")

    # ------------------------------------------------------------------ P2-4
    # A structured worker code decides before 6000 characters of provider prose.
    receipt_missing = (
        "RuntimeError:errorCode=WORKER_RECEIPT_MISSING"
        " JSONDecodeError:Expecting value: line 1 column 1 (char 0)"
    )
    code, terminal, delays = decide(receipt_missing)
    assert code == "WORKER_RECEIPT_MISSING" and not terminal and delays == INFRA_RETRY_SECONDS, (code, delays)
    # An unmapped code falls through, so the blob still finds the real fault.
    fallthrough = (
        "RuntimeError:errorCode=COLLECT_ADAPTER_FAILED adapters=['snk_price']"
        " detail=[{\"error\": \"mysql server has gone away\"}]"
    )
    assert decide(fallthrough)[0] == "MYSQL_UNAVAILABLE", decide(fallthrough)
    print("POSITIVE_OK P2-4 a mapped worker error code short-circuits the prose scan and an unmapped one does not")

    # 2026-09-25 live: the identity-completeness error listed 1858 card sha1s;
    # one held "...94933334..." and the whole failure read as a dead 9333.
    hash_blob = (
        "GemRate inventory build failed: {\"status\": \"INCOMPLETE_CENSUS\", \"reason\":"
        " \"missing_staged_raw_card_details:75b16a14a61dfe9dbf640b8ba21f1ffee94933334"
        ";missing_population_data:receipt_raw:0a3308ffbe01\"}"
    )
    assert decide(hash_blob)[0] == "SOURCE_FAILED", decide(hash_blob)
    assert decide("connect ECONNREFUSED 127.0.0.1:9333")[0] == "CDP_9333_UNAVAILABLE"
    assert decide("port 9333 refused")[0] == "CDP_9333_UNAVAILABLE"
    assert decide("tcp 127.0.0.1:3308 refused")[0] == "MYSQL_UNAVAILABLE"
    print("POSITIVE_OK a port inside a hex hash is not a dead browser or database, a real port still is")

    # ------------------------------------------------------------------ P2-6
    # Live receipt 0419b3c9e68c852f-attempt-2.json said {"currencies":0,
    # "written":30}: the receipt counted snapshot["quotes"], a key the FX
    # snapshot has never had.
    from fx_rates import SUPPORTED_CURRENCIES

    fx_cache = WORKSPACE / "fx-latest.json"
    fetched = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = fetched.isoformat().replace("+00:00", "Z")
    fx_snapshot = {
        "schemaVersion": "1.0.0",
        "base": "USD",
        "supported": list(SUPPORTED_CURRENCIES),
        "fetchedAt": stamp,
        "payloadSha256": "a" * 64,
        "rates": {
            code: {"value": 1.0 if code == "USD" else 2.5, "effectiveAt": stamp}
            for code in SUPPORTED_CURRENCIES
        },
    }
    fx_cache.write_text(json.dumps(fx_snapshot), encoding="utf-8")
    fx_quotes = [code for code in SUPPORTED_CURRENCIES if code != "USD"]
    import fx_rates as fx_module

    fx_calls: list[list[str]] = []
    fx_load_result: dict[str, Any] = {
        "status": "loaded", "runId": 7, "written": len(fx_quotes),
        "currencies": list(fx_quotes),
    }

    def fake_run_checked(command: list[str]) -> dict[str, Any]:
        fx_calls.append(list(command))
        if command[-1].endswith("fx_db_load.py"):
            return dict(fx_load_result)
        return {"status": "ok", "fetched": True}

    fx_task = {"source_code": "fx", "created_at": stamp}
    fx_payload = {"businessDate": DAY.isoformat(), "worker": {"timeoutSeconds": 5}}
    saved_cache = fx_module.DEFAULT_CACHE
    saved_run_checked = v2worker._run_checked
    fx_module.DEFAULT_CACHE = fx_cache
    v2worker._run_checked = fake_run_checked
    try:
        fx_receipt = v2worker.run_fx(fx_task, fx_payload)
        assert fx_receipt["status"] == "completed"
        assert fx_receipt["counts"]["currencies"] == len(SUPPORTED_CURRENCIES) == 31, fx_receipt["counts"]
        assert fx_receipt["counts"]["written"] == len(fx_quotes) == 30, fx_receipt["counts"]
        # Negative: a loader that wrote a different number of rows than the
        # snapshot carries must not return a completed receipt.
        fx_load_result["written"] = 29
        fx_load_result["currencies"] = list(fx_quotes[:29])
        try:
            v2worker.run_fx(fx_task, fx_payload)
        except RuntimeError as error:
            assert "FX rate count" in str(error), str(error)
        else:
            raise AssertionError("FX count mismatch fixture did not fire")
    finally:
        fx_module.DEFAULT_CACHE = saved_cache
        v2worker._run_checked = saved_run_checked
    print("POSITIVE_OK P2-6 the FX receipt counts the rates it really has and refuses a loader that disagrees")

    # ------------------------------------------------------------------ P2-5
    # A shard that matches no registry row used to return status completed with
    # sha256({"empty": true}) and no evidenceRef.
    shard_task = {"source_code": "gemrate", "run_id": RUN_ID}
    shard_payload = {"shard": "1-of-2", "worker": {"adapters": ["dummy_pop"]}}
    ok_report = {
        "ok": True, "processed": 2, "inserted": 2, "checkpointed": 2,
        "failed": 0, "quarantined": 0, "asOf": "2026-08-20T00:00:00Z",
        "failedAdapters": [], "truncatedAdapters": [], "results": [],
    }
    even_rows = [{"adapter": "dummy_pop", "variantId": value} for value in (2, 4, 6)]
    receipt_path = WORKSPACE / "shard-receipt.json"
    with collect_module(fake_collect(even_rows, ok_report)):
        empty_shard = v2worker.run_collect(shard_task, shard_payload, receipt_path)
    assert empty_shard["status"] == "degraded", empty_shard
    assert empty_shard["detail"]["reason"] == "shard_matched_no_registry_rows", empty_shard
    assert "payloadSha256" not in empty_shard, empty_shard
    assert "evidenceRef" not in empty_shard, empty_shard
    # Negative: an adapter with no registry row at all is a planning fault.
    with collect_module(fake_collect([], ok_report)):
        try:
            v2worker.run_collect(shard_task, shard_payload, receipt_path)
        except RuntimeError as error:
            assert "no registry rows for adapter" in str(error), str(error)
        else:
            raise AssertionError("empty adapter fixture did not fire")
    # Positive: a shard that does match rows still runs and still completes.
    with collect_module(fake_collect(even_rows, ok_report)):
        matched = v2worker.run_collect(
            shard_task, {"shard": "0-of-2", "worker": {"adapters": ["dummy_pop"]}}, receipt_path
        )
    assert matched["status"] == "completed" and matched["payloadSha256"], matched
    print("POSITIVE_OK P2-5 an empty shard is degraded with a reason while an empty adapter raises")

    # ------------------------------------------------------------------ P2-7
    # counts.failed was written to every receipt and read by nobody.
    failed_report = dict(ok_report)
    failed_report.update({
        "failed": 3,
        "results": [
            {"adapter": "snk_price", "ok": True, "failed": 3},
            {"adapter": "snk_trades", "ok": True, "failed": 0},
        ],
    })
    all_task = {"source_code": "snkrdunk", "run_id": RUN_ID}
    all_payload = {"shard": "all", "worker": {"adapters": ["snk_price", "snk_trades"]}}
    with collect_module(fake_collect(even_rows, failed_report)):
        failed_receipt = v2worker.run_collect(all_task, all_payload, receipt_path)
    assert failed_receipt["status"] == "degraded", failed_receipt
    assert failed_receipt["counts"]["failed"] == 3, failed_receipt["counts"]
    assert failed_receipt["detail"]["failedByAdapter"] == {"snk_price": 3}, failed_receipt["detail"]
    with collect_module(fake_collect(even_rows, ok_report)):
        clean_receipt = v2worker.run_collect(all_task, all_payload, receipt_path)
    assert clean_receipt["status"] == "completed" and clean_receipt["detail"]["failedByAdapter"] == {}
    print("POSITIVE_OK P2-7 failed items degrade the receipt and name the adapter that failed")

    # --------------------------------------------------- P2-4 worker + adapter
    fail_report = dict(ok_report)
    fail_report.update({
        "ok": False,
        "failedAdapters": ["snk_price"],
        "results": [{"adapter": "snk_price", "ok": False, "error": "boom"}],
    })
    with collect_module(fake_collect(even_rows, fail_report)):
        try:
            v2worker.run_collect(all_task, all_payload, receipt_path)
        except RuntimeError as error:
            text = str(error)
            assert text.startswith("errorCode="), text[:120]
            assert text.index("errorCode=") < text.index("detail="), text[:200]
        else:
            raise AssertionError("collect failure fixture did not fire")

    adapter = build_default_registry().get("gemrate")
    ingest_task = SourceTask(
        run_id=RUN_ID, business_date=DAY.isoformat(),
        source_code="gemrate", capability="pop", shard="1-of-2",
    )
    try:
        adapter.ingest(
            ingest_task,
            {
                "exitCode": 1,
                "receiptPath": str(receipt_path),
                "logPath": str(receipt_path.with_suffix(".log")),
                "receipt": {
                    "status": "terminal",
                    "errorCode": "WORKER_RECEIPT_MISSING",
                    "error": "JSONDecodeError:Expecting value: line 1 column 1 (char 0)",
                },
            },
            {},
        )
    except RuntimeError as error:
        assert str(error).startswith("errorCode=WORKER_RECEIPT_MISSING"), str(error)
        assert classify_error(f"RuntimeError:{error}").error_code == "WORKER_RECEIPT_MISSING"
    else:
        raise AssertionError("worker receipt error-code fixture did not fire")

    # P2-5 downstream: the adapter accepts the degraded empty-shard receipt.
    degraded_result = adapter.ingest(
        ingest_task,
        {
            "exitCode": 0,
            "receiptPath": str(receipt_path),
            "logPath": str(receipt_path.with_suffix(".log")),
            "receipt": dict(empty_shard, sourceCode="gemrate"),
        },
        {},
    )
    assert degraded_result.status == "degraded" and degraded_result.payload_sha256
    assert degraded_result.evidence_ref == str(receipt_path)
    print("POSITIVE_OK P2-4 the raised worker error leads with its code and the adapter keeps the degraded receipt")

    # ------------------------------------------------- R5 9333 single-flight
    # review 2026-08-24 (blocking): a refused PriceCharting sweep is this lane's
    # OWN 9333 child still fetching, not a failed fetch.  Read as SOURCE_FAILED
    # it climbed the 60..1800 ladder and turned the source TERMINAL on the 7th
    # refusal while the orphan was still working, so the day published with no
    # fresh PC data at all.  Same reading as PUBLISH_LOCK_HELD: contention must
    # not burn the ladder.
    busy_text = (
        "RuntimeError:errorCode=PC_CHILD_ALREADY_RUNNING adapters=['pc_ebay_sales']"
        " failed=['pc_ebay_sales'] detail=[]"
    )
    busy_code, busy_terminal, busy_delays = decide(busy_text)
    assert busy_code == "PC_CHILD_ALREADY_RUNNING" and not busy_terminal, (busy_code, busy_terminal)
    busy_decision = classify_error(busy_text)
    assert getattr(busy_decision, "contention", False) is True, busy_decision
    assert busy_decision.delay_for_attempt(1) == busy_delays[0], busy_decision
    # Repeating, not exhausting: the ladder never runs off its end into a
    # terminal verdict while the other sweep is still holding the host.
    assert busy_decision.delay_for_attempt(99) == busy_delays[-1], busy_decision
    assert getattr(classify_error("boom"), "contention", False) is False
    assert getattr(
        classify_error(f"release exit={PUBLISH_LOCK_EXIT_CODE}: {PUBLISH_LOCK_MARKER}", stage="publish"),
        "contention",
        False,
    ) is False

    pc_task = {"source_code": "pricecharting", "run_id": RUN_ID}
    pc_payload = {
        "shard": "all",
        "worker": {"adapters": ["pc_ebay_sales", "en_price_ref"]},
    }
    pc_busy_report = dict(ok_report)
    pc_busy_report.update({
        "ok": False,
        "failedAdapters": ["pc_ebay_sales", "en_price_ref"],
        "results": [
            {"adapter": "pc_ebay_sales", "ok": False, "error": "fresh_pc_pages_unavailable"},
            {"adapter": "en_price_ref", "ok": False, "error": "fresh_pc_pages_unavailable"},
        ],
        "pcRefresh": {
            "networkRefresh": {
                "ok": False,
                "error": "pc_child_already_running",
                "errorClass": "pc_child_already_running",
                "retryable": True,
            },
        },
    })
    with collect_module(fake_collect(even_rows, pc_busy_report)):
        try:
            v2worker.run_collect(pc_task, pc_payload, receipt_path)
        except RuntimeError as error:
            busy_raised = str(error)
            assert busy_raised.startswith("errorCode=PC_CHILD_ALREADY_RUNNING"), busy_raised[:160]
            assert classify_error(f"RuntimeError:{busy_raised}").error_code == "PC_CHILD_ALREADY_RUNNING"
        else:
            raise AssertionError("single-flight refusal fixture did not fire")

    # Negative: a real adapter fault next to the refusal must never be masked as
    # contention -- that would make an honest failure immortal.
    pc_mixed_report = dict(pc_busy_report)
    pc_mixed_report["results"] = [
        {"adapter": "pc_ebay_sales", "ok": False, "error": "fresh_pc_pages_unavailable"},
        {"adapter": "en_price_ref", "ok": False, "error": "pc_map:ValueError:bad row"},
    ]
    with collect_module(fake_collect(even_rows, pc_mixed_report)):
        try:
            v2worker.run_collect(pc_task, pc_payload, receipt_path)
        except RuntimeError as error:
            assert str(error).startswith("errorCode=COLLECT_ADAPTER_FAILED"), str(error)[:160]
        else:
            raise AssertionError("mixed PC failure fixture did not fire")
    print("POSITIVE_OK R5 a refused 9333 sweep carries its own contention code and never masks a real adapter fault")

    # ------------------------------------------- P1-2 alert on a terminal publish
    os.environ["CARDZ_V2_NOTIFY_DRY_RUN"] = "1"
    chain_module.LAST_ALERT = None
    journal = new_journal("publish-alert")
    # R4: the ladder is clamped to `final - PUBLISH_LEG_SECONDS`, so this
    # fixture has to own a real window instead of DAY's long-past 17:00 JST --
    # otherwise every publish verdict here is TERMINAL for lack of time and the
    # transient half of the check would prove nothing.
    _alert_now = chain_module.utc_now()
    chain = DailyChainV2(
        journal=journal,
        business_date=DAY,
        allow_publish=False,
        notify=False,
        deadline_monotonic=time.monotonic() + 600,
        schedule={
            "start": _alert_now - timedelta(hours=1),
            "source_cutoff": _alert_now + timedelta(hours=1),
            "sla": _alert_now + timedelta(hours=2),
            "final": _alert_now + timedelta(hours=4),
        },
    )

    # A MySQL recovery has to close the loop: compose, health, journal outcome,
    # and an immediate lifecycle alert when recovery itself fails.
    real_subprocess_run = chain_module.subprocess.run
    real_compose_env = db_config.compose_db_env
    db_config.compose_db_env = lambda path=None: {
        "CARDZ_DB_HOST": "fixture", "CARDZ_DB_PORT": "3308",
        "CARDZ_DB_USER": "fixture", "CARDZ_DB_PASSWORD": "fixture",
        "CARDZ_DB_NAME": "fixture",
    }
    recovery_commands: list[list[str]] = []

    def healthy_recovery(command, **kwargs):
        recovery_commands.append(list(command))
        return types.SimpleNamespace(returncode=0, stdout="healthy\n", stderr="")

    try:
        chain_module.subprocess.run = healthy_recovery
        assert chain.recover_mysql(trigger_task="fixture:mysql") is True
        assert len(recovery_commands) == 2 and "compose" in recovery_commands[0] and "inspect" in recovery_commands[1]
        assert any(row["event_type"] == "MYSQL_RECOVERY_COMPLETED" for row in journal.pending_events(RUN_ID))

        chain_module.LAST_ALERT = None
        chain_module.subprocess.run = lambda *args, **kwargs: types.SimpleNamespace(returncode=17, stdout="", stderr="")
        with contextlib.redirect_stdout(io.StringIO()):
            assert chain.recover_mysql(trigger_task="fixture:mysql-fail") is False
        assert any(row["event_type"] == "MYSQL_RECOVERY_FAILED" for row in journal.pending_events(RUN_ID))
        assert chain_module.LAST_ALERT and chain_module.LAST_ALERT["key"].startswith("v2-mysql-recovery-failed")
    finally:
        chain_module.subprocess.run = real_subprocess_run
        db_config.compose_db_env = real_compose_env
    mysql_key = journal.add_raw_task(
        run_id=RUN_ID, business_date=DAY.isoformat(), phase="source",
        source_code="fixture", capability="mysql-resume", required_class="core",
        concurrency_group="fixture:mysql", max_attempts=3,
    )
    mysql_claim = next(row for row in journal.claim_ready(RUN_ID, phases=("source",)) if row["task_key"] == mysql_key)
    mysql_decision = classify_error("pymysql.err.OperationalError: (2003, Can't connect to MySQL server)")
    assert mysql_decision.error_code == "MYSQL_UNAVAILABLE"
    journal.finish_failure(
        mysql_key, mysql_claim["lease_token"], decision=mysql_decision,
        error_text="fixture mysql unavailable",
    )
    resumed_recovery: list[str] = []
    real_recover_mysql = chain.recover_mysql
    chain.recover_mysql = lambda *, trigger_task="": (resumed_recovery.append(trigger_task) or True)  # type: ignore[method-assign]
    try:
        chain.recover_expired()
    finally:
        chain.recover_mysql = real_recover_mysql  # type: ignore[method-assign]
    assert resumed_recovery == [mysql_key], resumed_recovery
    print("POSITIVE_OK MySQL recovery composes, waits for health, journals outcome and alerts on failure")

    def fail_publish_task(capability: str, message: str) -> dict[str, Any]:
        def run(row):
            raise RuntimeError(message)

        key = journal.add_raw_task(
            run_id=RUN_ID, business_date=DAY.isoformat(), phase="publish",
            source_code="release", capability=capability,
            required_class="core", concurrency_group=f"publish:{capability}",
            max_attempts=3, payload={"kind": "stage", "stageName": "release"},
        )
        claimed = [
            row for row in journal.claim_ready(RUN_ID, phases=("publish",), limit=4)
            if str(row["task_key"]) == key
        ]
        assert len(claimed) == 1, (capability, claimed)
        chain._run_stage_process = run
        chain.execute_claim(claimed[0])
        return dict(journal.task(key) or {})

    alert_before_transient = chain_module.LAST_ALERT
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            transient_row = fail_publish_task(
                "publish-transient",
                "release exit=1: fatal: unable to access remote: HTTP 502",
            )
            alert_after_transient = chain_module.LAST_ALERT
            terminal_row = fail_publish_task(
                "publish-deterministic",
                'release exit=1: FAIL scripts/test-fe-share-file.mjs\nCARDZ_TEST_RESULT '
                '{"contract":"cardz-test-result-v1","entries":73,"passed":65,"failed":1,"skipped":7,"timeouts":0}',
            )
        assert transient_row["status"] == "RETRY", transient_row
        assert transient_row["last_error_code"] == "PUBLISH_FAILED", transient_row
        assert alert_after_transient == alert_before_transient, (
            alert_before_transient,
            alert_after_transient,
        )
        assert terminal_row["status"] == "TERMINAL", terminal_row
        assert terminal_row["last_error_code"] == "PUBLISH_DETERMINISTIC", terminal_row
        assert chain_module.LAST_ALERT is not None, "terminal publish verdict alerted nobody"
        assert chain_module.LAST_ALERT["key"] == f"v2-publish-terminal:{DAY.isoformat()}", chain_module.LAST_ALERT
    finally:
        os.environ.pop("CARDZ_V2_NOTIFY_DRY_RUN", None)
    print("POSITIVE_OK P1-2 a terminal publish verdict alerts immediately while a retryable one stays quiet")

    # -------------------------------------------------------------------- R4
    # 2026-08-24: the release ladder could burn itself out before the day's
    # 17:00 JST final.  V2 forced a single bake attempt
    # (daily_public_release.sh:263) and the orchestrator ladder
    # (120,300,600,1200,1800 = 67 min) could park the next publish retry AFTER
    # `final`, where lifecycle_events() stamps FAILED_FINAL unconditionally:
    # attempts left, and no time left in which to spend them.  The cutoff is
    # NOT loosened; the ladder is clamped to `final - PUBLISH_LEG_SECONDS` so
    # every remaining attempt still gets one whole publish leg.

    # (a) The two sides of the bake retry count agree, and the retry is only
    #     idempotent because it restores data/public first.
    assert 2 <= PUBLISH_ASSET_MAX_ATTEMPTS <= 3, PUBLISH_ASSET_MAX_ATTEMPTS
    v2_asset_lines = [
        line.strip() for line in release_source.splitlines()
        if "V2_MODE == 1" in line and "asset_max_attempts=" in line
    ]
    assert len(v2_asset_lines) == 1, v2_asset_lines
    assert f"asset_max_attempts={PUBLISH_ASSET_MAX_ATTEMPTS};" in v2_asset_lines[0], v2_asset_lines
    assert 'git -C "$RELEASE_REPO" checkout -- data/public' in release_source

    # (b) The ladder never schedules past cutoff - one leg, and once the last
    #     leg no longer fits there is no room at all (which reads as exhausted,
    #     never as "retry anyway after the cutoff").
    r4_cutoff = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)  # 17:00 JST
    r4_cap = r4_cutoff - timedelta(seconds=PUBLISH_LEG_SECONDS)
    for before_cutoff, delay in (
        (6 * 3600, 120),
        (3600, 300),
        (PUBLISH_LEG_SECONDS + 330, 600),
        (PUBLISH_LEG_SECONDS + 30, 1200),
        (PUBLISH_LEG_SECONDS, 1800),
    ):
        at = r4_cutoff - timedelta(seconds=before_cutoff)
        landed = clamp_retry_at(at, delay, not_after=r4_cap)
        assert landed is not None, (before_cutoff, delay)
        assert landed <= r4_cap, (before_cutoff, delay, landed, r4_cap)
        assert landed >= at, (before_cutoff, delay, landed)
    assert clamp_retry_at(r4_cap, 120, not_after=r4_cap) == r4_cap
    assert clamp_retry_at(r4_cap + timedelta(seconds=1), 120, not_after=r4_cap) is None
    # No deadline at all leaves the ladder exactly as classify_error wrote it.
    assert clamp_retry_at(r4_cutoff, 120, not_after=None) == r4_cutoff + timedelta(seconds=120)

    # (c) Through the real orchestrator: a publish failure that lands with less
    #     than one ladder step of window left retries INSIDE the window.
    os.environ["CARDZ_V2_NOTIFY_DRY_RUN"] = "1"
    try:
        def r4_publish_failure(
            name: str,
            seconds_of_window: float,
            *,
            capability: str = "release",
            leg_seconds: float = PUBLISH_LEG_SECONDS,
        ) -> dict[str, Any]:
            started = chain_module.utc_now()
            final_at = started + timedelta(seconds=leg_seconds + seconds_of_window)
            r4_journal = new_journal(f"publish-ladder-{name}")
            r4_chain = DailyChainV2(
                journal=r4_journal,
                business_date=DAY,
                allow_publish=False,
                notify=False,
                deadline_monotonic=time.monotonic() + 600,
                schedule={
                    "start": started - timedelta(hours=6),
                    "source_cutoff": started - timedelta(hours=2),
                    "sla": started - timedelta(hours=1),
                    "final": final_at,
                },
            )
            key = r4_journal.add_raw_task(
                run_id=RUN_ID, business_date=DAY.isoformat(), phase="publish",
                source_code="release", capability=capability,
                required_class="core", concurrency_group=f"publish:{name}",
                max_attempts=6, payload={"kind": "stage", "stageName": "release"},
            )
            claimed = [
                row for row in r4_journal.claim_ready(RUN_ID, phases=("publish",), limit=4)
                if str(row["task_key"]) == key
            ]
            assert len(claimed) == 1, (name, claimed)
            r4_chain._run_stage_process = lambda row: (_ for _ in ()).throw(
                RuntimeError("release exit=1: fatal: unable to access remote: HTTP 502")
            )
            with contextlib.redirect_stdout(io.StringIO()):
                r4_chain.execute_claim(claimed[0])
            row = dict(r4_journal.task(key) or {})
            row["_final"] = final_at
            row["_chain"] = r4_chain
            row["_journal"] = r4_journal
            return row

        # 60 s of window: the 120 s ladder step would land past the cutoff.
        inside = r4_publish_failure("inside", 60.0)
        assert inside["status"] == "RETRY", inside
        assert inside["last_error_code"] == "PUBLISH_FAILED", inside
        r4_due = datetime.fromisoformat(str(inside["next_retry_at"]).replace("Z", "+00:00"))
        r4_latest = inside["_final"] - timedelta(seconds=PUBLISH_LEG_SECONDS)
        assert r4_due <= r4_latest, (r4_due, r4_latest, inside["_final"])

        # No leg left in the day: TERMINAL, and the 17:00 cutoff still stamps
        # FAILED_FINAL.  The cap tightens the ladder; it buys no extra time.
        starved = r4_publish_failure("starved", -30.0)
        assert starved["status"] == "TERMINAL", starved
        assert not starved["next_retry_at"], starved
        starved["_chain"].lifecycle_events(starved["_final"] + timedelta(seconds=1))
        assert str((starved["_journal"].run(RUN_ID) or {}).get("status")) == "FAILED_FINAL", (
            starved["_journal"].run(RUN_ID)
        )
    finally:
        os.environ.pop("CARDZ_V2_NOTIFY_DRY_RUN", None)
    print("POSITIVE_OK R4 the publish ladder is capped at final - one leg and the 17:00 cutoff still ends the day")

    # ------------------------------------------------------------------- R4b
    # 2026-08-24 review of the cap above: it was charged to the whole `publish`
    # PHASE, but that phase carries two legs of very different length.
    # `release` (daily_chain_v2.py :1815) bakes/syncs/validates -- possibly
    # PUBLISH_ASSET_MAX_ATTEMPTS times -- then commits, pushes and polls the
    # live health endpoint for ten minutes.  `live-confirm` (:1871) is planned
    # only after `release` COMPLETED, i.e. always at the tail of the day, and
    # it reads the snapshot, makes one 20 s health request and inserts one
    # outbox row; it stays valid right up to `--confirm-before final`
    # (daily_chain_v2_stage.py :1248-1253), which is how the PUBLISH_FAILED
    # ladder waits out FE deploy lag.  Charging live-confirm a release leg made
    # every failure inside the last leg TERMINAL with attempts unspent and the
    # day's `live.confirmed` lost -- the exact shape R4 exists to remove.
    os.environ["CARDZ_V2_NOTIFY_DRY_RUN"] = "1"
    try:
        confirm = r4_publish_failure(
            "live-confirm", 300.0, capability="live-confirm", leg_seconds=0,
        )
        assert confirm["status"] == "RETRY", confirm
        assert confirm["last_error_code"] == "PUBLISH_FAILED", confirm
        assert int(confirm["attempts"]) < 6, confirm
        r4b_due = datetime.fromisoformat(str(confirm["next_retry_at"]).replace("Z", "+00:00"))
        assert r4b_due < confirm["_final"], (r4b_due, confirm["_final"])
    finally:
        os.environ.pop("CARDZ_V2_NOTIFY_DRY_RUN", None)

    from daily_chain_v2_contract import (  # noqa: E402
        LIVE_CONFIRM_LEG_SECONDS,
        PUBLISH_ASSET_PASS_SECONDS,
        PUBLISH_ASSET_RETRY_SLEEP_SECONDS,
        PUBLISH_HEALTH_POLL_SECONDS,
        PUBLISH_LEG_SECONDS_BY_CAPABILITY,
        publish_leg_seconds,
    )

    # Every publish capability the planner registers owns a MEASURED leg; a new
    # one cannot quietly inherit the release leg.
    chain_source = (ROOT / "pipelines" / "daily_chain_v2.py").read_text(encoding="utf-8")
    planned_publish = set(
        re.findall(r'phase="publish", capability="([a-z0-9-]+)"', chain_source)
    )
    assert planned_publish == {"release", "live-confirm"}, planned_publish
    assert planned_publish == set(PUBLISH_LEG_SECONDS_BY_CAPABILITY), (
        planned_publish, sorted(PUBLISH_LEG_SECONDS_BY_CAPABILITY)
    )
    assert publish_leg_seconds("release") == PUBLISH_LEG_SECONDS
    assert publish_leg_seconds("live-confirm") == LIVE_CONFIRM_LEG_SECONDS
    assert LIVE_CONFIRM_LEG_SECONDS < PUBLISH_LEG_SECONDS

    # The sleeps that make up a release leg are read off the script itself, so
    # neither side can drift alone.
    r4b_retry_sleep = re.search(
        # R4d put a BOX-sidecar re-stage between the checkout and the sleep;
        # anything else the retry tail grows must stay inside the loop body's
        # two-space indent, and the measured sleep is still read off the script.
        r'git -C "\$RELEASE_REPO" checkout -- data/public\n(?:  \w[^\n]*\n)*?  sleep (\d+)\n',
        release_source,
    )
    assert r4b_retry_sleep is not None, "asset retry sleep not found in daily_public_release.sh"
    assert PUBLISH_ASSET_RETRY_SLEEP_SECONDS == int(r4b_retry_sleep.group(1)), r4b_retry_sleep.group(1)
    r4b_polls = re.findall(
        r"for _ in \$\(seq 1 (\d+)\); do\n(?:.*\n)*?\s*sleep (\d+)\n\s*done\n", release_source
    )
    assert r4b_polls, "live health poll loop not found in daily_public_release.sh"
    for r4b_rounds, r4b_sleep in r4b_polls:
        assert PUBLISH_HEALTH_POLL_SECONDS == int(r4b_rounds) * int(r4b_sleep), (
            PUBLISH_HEALTH_POLL_SECONDS, r4b_rounds, r4b_sleep
        )
    print("POSITIVE_OK R4b the publish leg is per-capability and its parts are read off the script")

    # ------------------------------------------------------------------- R4c
    # 2026-08-24 review of R4b: R4b sized the release cap on the leg where
    # EVERYTHING fails -- 3 bake passes + 2 retry sleeps + the FULL 600 s
    # health poll = 1170 s -- but that leg is not the one a retry is trying to
    # buy.  The bake normally passes on attempt 1 and the poll exits on the
    # first healthy generation, so a SUCCESSFUL release leg is ~5 min.
    # Charging every retry 19.5 min refused retries that would very likely have
    # published: a PUBLISH_FAILED at 16:45 JST is TERMINAL under a 1170 s cap
    # (cap = 16:40:30) while a 780 s cap (16:47) still gives it one real
    # attempt -- and the brief's own spec for the cap is "cutoff minus one
    # publish leg (~13 min)".  The cap is therefore the leg that can still
    # SUCCEED: one bake pass + the health poll.
    #
    # The extra bake passes stay a SCRIPT-side budget: daily_public_release.sh
    # now refuses to start a bake pass it cannot finish before
    # CARDZ_V2_STAGE_DEADLINE_EPOCH (daily_chain_v2.py :2205, derived from
    # _work_deadline_monotonic, which is never later than `final` -- :2126), so
    # the worst case can no longer run past the cutoff and no longer has to be
    # paid for by every retry in the ladder.
    r4c_worst_leg = (
        PUBLISH_ASSET_MAX_ATTEMPTS * PUBLISH_ASSET_PASS_SECONDS
        + (PUBLISH_ASSET_MAX_ATTEMPTS - 1) * PUBLISH_ASSET_RETRY_SLEEP_SECONDS
        + PUBLISH_HEALTH_POLL_SECONDS
    )

    # (a) The divergence band the review named: a release PUBLISH_FAILED with
    #     16 min of window left must still get a real attempt.
    os.environ["CARDZ_V2_NOTIFY_DRY_RUN"] = "1"
    try:
        band = r4_publish_failure("release-band", 960.0, leg_seconds=0)
        assert band["status"] == "RETRY", band
        assert band["last_error_code"] == "PUBLISH_FAILED", band
        assert int(band["attempts"]) < 6, band
        r4c_due = datetime.fromisoformat(str(band["next_retry_at"]).replace("Z", "+00:00"))
        r4c_latest = band["_final"] - timedelta(seconds=PUBLISH_LEG_SECONDS)
        assert r4c_due <= r4c_latest, (r4c_due, r4c_latest, band["_final"])
    finally:
        os.environ.pop("CARDZ_V2_NOTIFY_DRY_RUN", None)

    # (b) ...because the leg is the succeeding one, not the all-fail one.
    assert PUBLISH_LEG_SECONDS == PUBLISH_ASSET_PASS_SECONDS + PUBLISH_HEALTH_POLL_SECONDS, (
        PUBLISH_LEG_SECONDS, PUBLISH_ASSET_PASS_SECONDS, PUBLISH_HEALTH_POLL_SECONDS
    )
    assert PUBLISH_LEG_SECONDS < r4c_worst_leg, (PUBLISH_LEG_SECONDS, r4c_worst_leg)

    # (c) ...and the worst case is bounded where it is spent: the script owns
    #     one definition of a pass, checks the orchestrator's stage deadline
    #     before each retry, and refuses instead of starting a pass that would
    #     be SIGTERMed mid-bake at the cutoff.
    r4c_pass_lines = [
        line.strip() for line in release_source.splitlines()
        if line.strip().startswith("asset_pass_seconds=")
    ]
    assert r4c_pass_lines == [f"asset_pass_seconds={PUBLISH_ASSET_PASS_SECONDS}"], r4c_pass_lines
    r4c_guard = re.search(r"\nasset_retry_fits\(\) \{\n(?:.*\n)*?\}\n", release_source)
    assert r4c_guard is not None, "asset_retry_fits() not found in daily_public_release.sh"
    assert re.search(
        r"if ! asset_retry_fits; then\n(?:.*\n)*?\s*exit 1\n\s*fi\n", release_source
    ), "the asset retry loop does not consult asset_retry_fits"
    if shutil.which("bash"):
        # The guard runs as the script's own text, not a paraphrase of it.
        def r4c_run(deadline: str | None) -> str:
            setup = (
                "unset CARDZ_V2_STAGE_DEADLINE_EPOCH\n" if deadline is None
                else f"export CARDZ_V2_STAGE_DEADLINE_EPOCH='{deadline}'\n"
            )
            script = (
                "set -euo pipefail\n"
                f"asset_pass_seconds={PUBLISH_ASSET_PASS_SECONDS}\n"
                + setup
                + r4c_guard.group(0)
                + "if asset_retry_fits; then echo FITS; else echo REFUSES; fi\n"
            )
            done = subprocess.run(
                # On stdin as bytes: text mode would rewrite every \n as \r\n
                # for Git-for-Windows bash, and a native path argument loses
                # its backslashes to the same re-parsing.
                ["bash", "-s"],
                input=script.encode("utf-8"),
                capture_output=True, timeout=60,
            )
            assert done.returncode == 0, (
                done.returncode, done.stdout.decode("utf-8", "replace"),
                done.stderr.decode("utf-8", "replace"),
            )
            return done.stdout.decode("utf-8", "replace").strip()

        # A whole pass still fits -> retry; less than a pass -> refuse.  No
        # deadline and unparsable deadline keep the pre-R4c behaviour.
        assert r4c_run(repr(time.time() + PUBLISH_ASSET_PASS_SECONDS + 120)) == "FITS"
        assert r4c_run(repr(time.time() + PUBLISH_ASSET_PASS_SECONDS - 60)) == "REFUSES"
        assert r4c_run(repr(time.time() - 60)) == "REFUSES"
        assert r4c_run(None) == "FITS"
        assert r4c_run("not-a-number") == "FITS"
    else:
        print("SKIP_NO_BASH asset_retry_fits execution needs bash; the parse half still runs")
    print("POSITIVE_OK R4c the release cap is the succeeding leg and the script owns the retry budget")

    # ------------------------------------------------------------------- R4d
    # 2026-08-24 adversarial review of R4c: raising V2's asset_max_attempts
    # from 1 to 3 made the retry loop REACHABLE for the first time, and that
    # loop starts by reverting data/public -- which includes the BOX sidecar,
    # copied in exactly once ABOVE the loop.  A retried bake therefore
    # published YESTERDAY's /box in silence: --box-previous is
    # `git show HEAD:data/public/box-subset.json`, i.e. the very bytes the
    # checkout restores, so validate_daily_release.py's no-regression check
    # compares the file against itself and passes.  Every pass must see
    # exactly what pass 1 saw.
    r4d_loop = re.search(r"\nwhile true; do\n((?:.*\n)*?)done\n", release_source)
    assert r4d_loop is not None, "asset retry loop not found in daily_public_release.sh"
    r4d_body = r4d_loop.group(1)
    assert 'git -C "$RELEASE_REPO" checkout -- data/public' in r4d_body, r4d_body
    # One definition of the copy, so the two call sites cannot drift apart...
    assert release_source.count('cp "$BOX_SRC" "$BOX_DST"') == 1, release_source
    # ...and it keeps the original site's condition: no SOURCE file still means
    # "keep whatever release git carries", never an empty /box.
    assert re.search(
        r'if \[\[ -s "\$BOX_SRC" \]\]; then\n(?:.*\n)*?\s*cp "\$BOX_SRC" "\$BOX_DST"\n',
        release_source,
    ), "the BOX sidecar copy lost its `[[ -s $BOX_SRC ]]` guard"
    r4d_functions = {
        match.group(1): match.group(2)
        for match in re.finditer(
            r"\n([A-Za-z_][A-Za-z0-9_]*)\(\) \{\n((?:.*\n)*?)\}\n", release_source
        )
    }

    def r4d_restages_sidecar(block: str) -> bool:
        """Does `block` put SOURCE's box-subset.json back into the release tree?"""
        if 'cp "$BOX_SRC" "$BOX_DST"' in block:
            return True
        return any(
            re.search(rf"^\s*{re.escape(name)}\s*$", block, re.MULTILINE)
            and 'cp "$BOX_SRC" "$BOX_DST"' in body
            for name, body in r4d_functions.items()
        )

    r4d_after_checkout = r4d_body[
        r4d_body.index('git -C "$RELEASE_REPO" checkout -- data/public'):
    ]
    assert r4d_restages_sidecar(r4d_after_checkout), (
        "the asset retry reverts data/public and starts the next bake pass "
        "without re-staging the BOX sidecar:\n" + r4d_body
    )
    # The same step runs before the first pass, out of the same definition.
    assert r4d_restages_sidecar(
        release_source[
            release_source.index('BOX_DST="$RELEASE_REPO/data/public/box-subset.json"'):
            release_source.index("publish_assets() {")
        ]
    ), "the first bake pass no longer stages the BOX sidecar"
    print("POSITIVE_OK R4d the asset retry re-stages the BOX sidecar it just reverted")

    # ------------------------------------------------------------------- R4e
    # Same review, observability half: reclassify_retry() corrects
    # chain_attempt.error_code and only THEN asks clamp_retry_at where the
    # corrected ladder lands.  Near the cutoff that answer is None and the
    # function returned before the chain_task UPDATE -- the attempt row carried
    # the NEW class (same transaction, committed on return) while the task row,
    # which is what `cardz-v2 status`, the observer and the alerts read, still
    # named the OLD one.  The label is bookkeeping, the clock is the gate:
    # correct the label either way, move the clock only when a slot exists.
    r4e_journal = new_journal("reclassify-clamped")
    r4e_key = r4e_journal.add_raw_task(
        run_id=RUN_ID,
        business_date=DAY.isoformat(),
        phase="publish",
        source_code="release",
        capability="release",
        required_class="publish",
        concurrency_group="publish-r4e",
        max_attempts=6,
    )
    r4e_claimed = r4e_journal.claim_ready(RUN_ID, phases=["publish"])
    assert len(r4e_claimed) == 1, r4e_claimed
    assert r4e_journal.finish_failure(
        r4e_key,
        str(r4e_claimed[0]["lease_token"]),
        decision=RetryDecision("PUBLISH_FAILED", False, PUBLISH_RETRY_SECONDS),
        error_text="release exit=1: bake failed",
        now=datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc),
        retry_not_after=datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc),
    ) == "RETRY"
    r4e_before = r4e_journal.task(r4e_key)
    assert r4e_before["status"] == "RETRY", r4e_before
    assert r4e_before["last_error_code"] == "PUBLISH_FAILED", r4e_before
    r4e_clock = r4e_before["next_retry_at"]

    # The corrected class is PUBLISH_LOCK_HELD, but `now` is already past the
    # deadline, so clamp_retry_at answers None.
    r4e_rescheduled = r4e_journal.reclassify_retry(
        r4e_key,
        decision=RetryDecision("PUBLISH_LOCK_HELD", False, INFRA_RETRY_SECONDS),
        now=datetime(2026, 8, 20, 7, 0, tzinfo=timezone.utc),
        retry_not_after=datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc),
    )
    with r4e_journal.connect() as r4e_conn:
        r4e_attempt_code = r4e_conn.execute(
            "SELECT error_code FROM chain_attempt WHERE task_key=?"
            " ORDER BY attempt_no DESC LIMIT 1",
            (r4e_key,),
        ).fetchone()["error_code"]
    r4e_after = r4e_journal.task(r4e_key)
    assert r4e_attempt_code == "PUBLISH_LOCK_HELD", r4e_attempt_code
    assert r4e_after["last_error_code"] == "PUBLISH_LOCK_HELD", r4e_after
    # ...while the clock and the caller's answer stay honest: nothing was
    # rescheduled, and the slot the task already owns is not moved.
    assert r4e_rescheduled is False, r4e_rescheduled
    assert r4e_after["next_retry_at"] == r4e_clock, (r4e_after, r4e_clock)
    assert r4e_after["status"] == "RETRY", r4e_after

    # An in-window correction still moves both, exactly as before.
    r4e_moved = r4e_journal.reclassify_retry(
        r4e_key,
        decision=RetryDecision("PUBLISH_FAILED", False, PUBLISH_RETRY_SECONDS),
        now=datetime(2026, 8, 20, 7, 0, tzinfo=timezone.utc),
        retry_not_after=datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc),
    )
    r4e_final = r4e_journal.task(r4e_key)
    assert r4e_moved is True, r4e_moved
    assert r4e_final["last_error_code"] == "PUBLISH_FAILED", r4e_final
    assert datetime.fromisoformat(str(r4e_final["next_retry_at"])) == datetime(
        2026, 8, 20, 7, 0, tzinfo=timezone.utc
    ) + timedelta(seconds=PUBLISH_RETRY_SECONDS[0]), r4e_final
    print("POSITIVE_OK R4e a retry the cutoff leaves no slot for still gets its class corrected")


finally:
    cleanup()
