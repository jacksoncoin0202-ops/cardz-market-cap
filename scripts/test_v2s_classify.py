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
import shutil
import subprocess
import sys
import tempfile
import time
import types
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # Windows has no flock; the release lock is a WSL fixture
    fcntl = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import daily_chain_v2 as chain_module  # noqa: E402
from daily_chain_v2 import DailyChainV2  # noqa: E402
from daily_chain_v2_adapters import build_default_registry  # noqa: E402
from daily_chain_v2_contract import (  # noqa: E402
    INFRA_RETRY_SECONDS,
    PUBLISH_LOCK_EXIT_CODE,
    PUBLISH_LOCK_MARKER,
    PUBLISH_RETRY_SECONDS,
    TRANSIENT_RETRY_SECONDS,
    SourceTask,
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
        "65/66 passed, 1 failed, 7 skipped\n"
    )
    code, terminal, delays = decide(verdict, stage="publish")
    assert code == "PUBLISH_DETERMINISTIC" and terminal and delays == (), (code, terminal, delays)
    # Audit 6 #4: the same tokens are ordinary inside a source worker traceback.
    source_code_, source_terminal, source_delays = decide(verdict)
    assert not source_terminal and not source_code_.startswith("PUBLISH"), (source_code_, source_terminal)
    assert source_delays == TRANSIENT_RETRY_SECONDS

    # Audit 6 #5: tests all green, git push failed -> still retryable.
    push_failure = (
        "release exit=1: 66/66 passed, 0 failed, 7 skipped\n"
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
    chain = DailyChainV2(
        journal=journal,
        business_date=DAY,
        allow_publish=False,
        notify=False,
        deadline_monotonic=time.monotonic() + 600,
    )

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

    try:
        with contextlib.redirect_stdout(io.StringIO()):
            transient_row = fail_publish_task(
                "publish-transient",
                "release exit=1: fatal: unable to access remote: HTTP 502",
            )
            alert_after_transient = chain_module.LAST_ALERT
            terminal_row = fail_publish_task(
                "publish-deterministic",
                "release exit=1: FAIL scripts/test-fe-share-file.mjs\n65/66 passed, 1 failed, 7 skipped",
            )
        assert transient_row["status"] == "RETRY", transient_row
        assert transient_row["last_error_code"] == "PUBLISH_FAILED", transient_row
        assert alert_after_transient is None, alert_after_transient
        assert terminal_row["status"] == "TERMINAL", terminal_row
        assert terminal_row["last_error_code"] == "PUBLISH_DETERMINISTIC", terminal_row
        assert chain_module.LAST_ALERT is not None, "terminal publish verdict alerted nobody"
        assert chain_module.LAST_ALERT["key"] == f"v2-publish-terminal:{DAY.isoformat()}", chain_module.LAST_ALERT
    finally:
        os.environ.pop("CARDZ_V2_NOTIFY_DRY_RUN", None)
    print("POSITIVE_OK P1-2 a terminal publish verdict alerts immediately while a retryable one stays quiet")

finally:
    cleanup()
