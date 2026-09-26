#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V2 structural audit 2026-08-23 -- gemrate collector work package.

Covers audit P0-2, P1-5, P1-6, P1-7, P2-9, P2-10, P2-11, P2-12, P2-13 and the
operator investigation into the 2026-08-24 core repair that never shrank its
due set.  Every check here is behavioural: the browser and the child process
are faked, no network, no MySQL, no writes outside a temporary directory.

Set ``CARDZ_V2S_PIPELINES_DIR`` to a pristine copy of ``pipelines/`` to run the
same checks against the unfixed code and watch them fail.

Run: python -X utf8 scripts/test_v2s_gemrate.py
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import sys
import tempfile
import threading
import types
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PIPELINES = Path(os.environ.get("CARDZ_V2S_PIPELINES_DIR") or (ROOT / "pipelines"))
sys.path.insert(0, str(PIPELINES))

import gemrate_source as gs  # noqa: E402
import collect_control as cc  # noqa: E402
import daily_chain_v2_adapters as adapters  # noqa: E402

FAILED: list[str] = []
CHECKS = 0

HEX_A = "a" * 40
HEX_B = "b" * 40


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


def case(label: str, fn) -> None:
    """Run one check group; a raised exception is a failure, never a crash."""
    try:
        fn()
    except Exception as error:  # noqa: BLE001
        global CHECKS
        CHECKS += 1
        FAILED.append(f"FAIL {label} raised {type(error).__name__}: {error}")


# --------------------------------------------------------------- fake browser


class FakePage:
    def __init__(self) -> None:
        self.waits: list[int] = []
        self.gotos: list[str] = []

    def goto(self, url: str, **_kwargs: Any):
        self.gotos.append(url)
        return None

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self._page = page

    def add_init_script(self, _script: str) -> None:
        return None

    def route(self, _pattern: str, _handler: Any) -> None:
        return None

    def new_page(self) -> FakePage:
        return self._page


class FakeBrowser:
    def __init__(self, page: FakePage, fail_context: int) -> None:
        self._page = page
        self._fail_context = fail_context
        self.closed = 0

    def new_context(self, **_kwargs: Any) -> FakeContext:
        if self._fail_context > 0:
            self._fail_context -= 1
            raise TimeoutError("cloudflare warm-up refused the context")
        return FakeContext(self._page)

    def close(self) -> None:
        self.closed += 1


def install_fake_playwright() -> None:
    """`_gemrate_public_page` imports playwright inside the function body."""
    if "playwright.sync_api" in sys.modules:
        return
    package = types.ModuleType("playwright")
    module = types.ModuleType("playwright.sync_api")

    class _PW:
        def __enter__(self):
            return object()

        def __exit__(self, *_exc):
            return False

    module.sync_playwright = lambda: _PW()  # type: ignore[attr-defined]
    package.sync_api = module  # type: ignore[attr-defined]
    sys.modules["playwright"] = package
    sys.modules["playwright.sync_api"] = module


# ------------------------------------------------------- P0-2 warm-up retry


def test_warmup_retry_survives_transient_session_failures() -> None:
    install_fake_playwright()
    page = FakePage()
    launched: list[FakeBrowser] = []
    # Two transient failures then success: the 2026-08-20 / 2026-08-23 shape,
    # where the session died ~1.5s in while sibling shards launched Chrome.
    plan = [2, 0, 0]

    def fake_launch(_pw):
        browser = FakeBrowser(page, plan[len(launched)] if len(launched) < len(plan) else 0)
        launched.append(browser)
        return browser

    original_launch = gs._launch_chromium
    original_backoff = getattr(gs, "WEBSITE_SESSION_RETRY_BACKOFF", None)
    gs._launch_chromium = fake_launch
    if original_backoff is not None:
        gs.WEBSITE_SESSION_RETRY_BACKOFF = (0.0, 0.0, 0.0)
    try:
        with gs._gemrate_public_page() as opened:
            check("warm-up retry yields a live page", opened is page, True)
        check("warm-up retried the whole session", len(launched), 2)
        check("half-dead browser was closed between attempts", launched[0].closed, 1)
        check("warm-up navigation ran", page.gotos[-1].endswith("/universal-pop-report"), True)
    finally:
        gs._launch_chromium = original_launch
        if original_backoff is not None:
            gs.WEBSITE_SESSION_RETRY_BACKOFF = original_backoff


def test_warmup_gives_up_after_the_ladder_and_keeps_the_class() -> None:
    install_fake_playwright()
    page = FakePage()
    launched: list[FakeBrowser] = []

    def fake_launch(_pw):
        browser = FakeBrowser(page, 99)
        launched.append(browser)
        return browser

    original_launch = gs._launch_chromium
    original_backoff = getattr(gs, "WEBSITE_SESSION_RETRY_BACKOFF", None)
    gs._launch_chromium = fake_launch
    if original_backoff is not None:
        gs.WEBSITE_SESSION_RETRY_BACKOFF = (0.0, 0.0, 0.0)
    raised: list[str] = []
    try:
        try:
            with gs._gemrate_public_page():
                pass
        except Exception as error:  # noqa: BLE001
            raised.append(type(error).__name__)
        check("exhausted ladder still raises", raised, ["TimeoutError"])
        check("ladder tried every rung", len(launched), 4)
        check(
            "failure keeps its class for the receipt",
            gs._safe_browser_error(TimeoutError("x")),
            "browser_collection_failed:TimeoutError",
        )
    finally:
        gs._launch_chromium = original_launch
        if original_backoff is not None:
            gs.WEBSITE_SESSION_RETRY_BACKOFF = original_backoff


# ------------------------------------------------------------------- P2-10


def test_safe_browser_error_keeps_the_exception_class() -> None:
    check(
        "launch failure stays browser_unavailable",
        gs._safe_browser_error(RuntimeError("no chrome")),
        "browser_unavailable",
    )
    check(
        "session failure names the class",
        gs._safe_browser_error(ValueError("boom")),
        "browser_collection_failed:ValueError",
    )


# ------------------------------------------- P1-6 + P2-11 + P2-9 + P2-10 file


def test_cmd_daily_pass_loop_and_transport_receipts() -> None:
    calls: list[dict[str, Any]] = []

    def fake_collect(ids, **kwargs):
        calls.append({"ids": list(ids), "delay": kwargs.get("delay"), "workers": kwargs.get("workers")})
        return {
            "transport": gs.WEBSITE_TRANSPORT,
            "requested": len(ids),
            "attempted": len(ids),
            "succeeded": 0,
            "failed": len(ids),
            "error": "browser_collection_failed:TimeoutError",
            "failureReceipts": [
                {"gemrateId": gid, "httpStatus": None,
                 "reason": "browser_collection_failed:TimeoutError"}
                for gid in ids
            ],
        }

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ids_file = root / "ids.txt"
        ids_file.write_text(f"{HEX_A}\n{HEX_B}\n", encoding="utf-8")
        args = types.SimpleNamespace(
            ids_file=str(ids_file), speed="fast", limit=None, run_suffix="",
            identity_file=str(root / "no-identity.json"),
            mirror_root=str(root / "no-mirror"),
            website_budget_seconds=60, workers=1,
            with_history=False, no_history=True, skip_grader_volume=True,
            volume_slug=None,
        )
        original = (gs.OUT_DIR, gs.CARDS_DIR, gs.collect_public_card_details,
                    os.environ.get("GEMRATE_API_KEY"))
        out_dir = root / "out"
        gs.OUT_DIR = out_dir
        gs.CARDS_DIR = out_dir / "cards"
        gs.collect_public_card_details = fake_collect
        os.environ["GEMRATE_API_KEY"] = ""
        try:
            rc = gs.cmd_daily(args)
        finally:
            gs.OUT_DIR, gs.CARDS_DIR, gs.collect_public_card_details = original[:3]
            if original[3] is None:
                os.environ.pop("GEMRATE_API_KEY", None)
            else:
                os.environ["GEMRATE_API_KEY"] = original[3]

        check("partial run returns 1", rc, 1)
        # P1-6: a session-level error must not skip the designed slow-retry pass.
        check("first pass error still runs the slow-retry pass", len(calls), 2)
        # P2-11: --speed fast is 0.15s; the old max(0.3, delay) floor doubled it.
        check("first pass uses the caller's delay", calls[0]["delay"], gs.SPEEDS["fast"])
        check("slow-retry pass uses the slow delay", calls[1]["delay"], gs.SPEEDS["slow"])

        run_dirs = sorted((out_dir / "runs").glob("daily_*"))
        check("one run dir written", len(run_dirs), 1)
        manifest = json.loads((run_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
        reasons = {row["gemrateId"]: row["reason"] for row in manifest["unresolved"]}
        # P2-9: a dead browser is not a verdict about GemRate's data.
        check(
            "unresolved reason is the transport receipt",
            reasons.get(HEX_A),
            "browser_collection_failed:TimeoutError",
        )
        check(
            "manifest carries the website failure receipts",
            manifest.get("websiteFailureReceipts", {}).get(HEX_B),
            "browser_collection_failed:TimeoutError",
        )
        # P2-10: the run dir keeps the receipts for the next outage.
        receipts_path = run_dirs[0] / "website_transport_receipts.json"
        check("transport receipts written to the run dir", receipts_path.is_file(), True)
        if receipts_path.is_file():
            receipts = json.loads(receipts_path.read_text(encoding="utf-8"))
            check("receipts name the class", receipts["reasons"][HEX_A],
                  "browser_collection_failed:TimeoutError")


def test_build_run_prefers_the_transport_reason() -> None:
    cards = [{"gemrateId": HEX_A}]
    manifest = gs.build_population_transport_run(
        cards,
        direct_payloads={},
        website_payloads={},
        mirror_payloads={},
        fetched_at="2026-08-23T00:00:00Z",
        website_attempted_ids={HEX_A},
        website_failure_reasons={HEX_A: "budget_exhausted"},
    )
    check(
        "explicit transport reason wins",
        manifest["unresolved"][0]["reason"],
        "budget_exhausted",
    )
    fallback = gs.build_population_transport_run(
        cards,
        direct_payloads={},
        website_payloads={},
        mirror_payloads={},
        fetched_at="2026-08-23T00:00:00Z",
        website_attempted_ids={HEX_A},
    )
    check(
        "no receipt keeps the data verdict",
        fallback["unresolved"][0]["reason"],
        "no_current_population",
    )


def test_json_poll_interval_is_50ms() -> None:
    source = (PIPELINES / "gemrate_source.py").read_text(encoding="utf-8")
    check("200ms /card-details poll is gone", "page.wait_for_timeout(200)" in source, False)
    check("50ms /card-details poll is in", "page.wait_for_timeout(50)" in source, True)


# ------------------------------------------------------------------- item 10


def test_shutdown_returns_captured_payloads_instead_of_raising() -> None:
    install_fake_playwright()
    page = FakePage()

    def fake_session():
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            yield page

        return _cm()

    original_session = gs._gemrate_public_page
    original_fetch = gs._fetch_card_once
    original_persist = gs._persist_public_card_capture
    gs._gemrate_public_page = fake_session
    gs._fetch_card_once = lambda _page, gid: (
        {"gemrate_id": gid, "population_data": []}, None, False,
    )
    gs._persist_public_card_capture = lambda *_a, **_k: {}
    sink: dict[str, Any] = {}
    try:
        gs.request_shutdown()
        with tempfile.TemporaryDirectory() as tmp:
            outcome = gs.collect_public_card_details(
                [HEX_A, HEX_B], cards_dir=Path(tmp), delay=0.0,
                chunk_size=25, workers=1, payload_sink=sink,
            )
        check("shutdown is reported as a transport error",
              outcome["error"], "interrupted_by_signal")
        check("shutdown does not raise out of the collector",
              isinstance(outcome, dict), True)
    finally:
        gs._SHUTDOWN_EVENT.clear()
        gs._gemrate_public_page = original_session
        gs._fetch_card_once = original_fetch
        gs._persist_public_card_capture = original_persist


def test_deferred_termination_holds_sigterm() -> None:
    if not hasattr(signal, "SIGTERM"):
        return
    with cc._deferred_termination() as state:
        # raise_signal(), not os.kill(getpid()): on Windows os.kill() is
        # TerminateProcess(), which kills this interpreter with exit code 15
        # before any handler can run, so the check could never execute there.
        # raise_signal() delivers through the C runtime on both platforms and
        # therefore actually exercises the deferring handler.
        signal.raise_signal(signal.SIGTERM)
        check("SIGTERM recorded, process alive", state["signalled"], True)
    check("handler restored after the block",
          signal.getsignal(signal.SIGTERM) is not None, True)


def test_worker_shutdown_grace_lets_the_ingest_land() -> None:
    check(
        "source worker grace is no longer the 5s default",
        adapters.WORKER_SHUTDOWN_GRACE_SECONDS >= 30.0,
        True,
    )
    # R3 (2026-08-24): the grace is per worker kind now.  60 s never covered a
    # collect/gemrate child finishing its step and ingesting its manifest.
    check(
        "collect worker grace covers the child wind-down plus ingest",
        adapters.worker_shutdown_grace_seconds("collect") >= 240.0,
        True,
    )
    source = (PIPELINES / "daily_chain_v2_adapters.py").read_text(encoding="utf-8")
    check(
        "source worker termination uses the per-kind grace table",
        "grace_seconds=worker_shutdown_grace_seconds(self.worker_kind)" in source,
        True,
    )


# --------------------------------------------------------------------- P1-5


def _gemrate_command(gemrate_workers: int, tmp: Path) -> list[str]:
    captured: list[list[str]] = []

    def fake_run(cmd, *, timeout, dry_run):
        captured.append(list(cmd))
        return {"cmd": cmd, "exit": 2}

    original = (cc.ROOT, cc.OUT_DIR, cc.QUARANTINE_PATH, cc._run)
    cc.ROOT = tmp
    cc.OUT_DIR = tmp / "out"
    cc.QUARANTINE_PATH = tmp / "quarantine.json"
    cc._run = fake_run
    try:
        cc.run_gemrate_pop(
            [{"variantId": 1, "externalId": HEX_A}],
            mode="incr", limit=None, dry_run=False, work_scope="0-of-4",
            gemrate_workers=gemrate_workers,
        )
    finally:
        cc.ROOT, cc.OUT_DIR, cc.QUARANTINE_PATH, cc._run = original
    return captured[0] if captured else []


def test_gemrate_workers_is_payload_driven() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        command = _gemrate_command(2, Path(tmp))
    index = command.index("--workers") if "--workers" in command else -1
    check("child receives the payload worker count",
          command[index + 1] if index >= 0 else None, "2")


def test_gemrate_workers_fails_closed_above_the_ceiling() -> None:
    check("ceiling is 2 Chromes per shard", cc.GEMRATE_MAX_WORKERS, 2)
    raised: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _gemrate_command(4, Path(tmp))
        except RuntimeError as error:
            raised.append(str(error))
    check("workers=4 (16 Chromes) is refused", bool(raised), True)


def test_adapter_payload_and_worker_thread_the_knob() -> None:
    registry = adapters.build_default_registry()
    gemrate = registry.get("gemrate")
    check("adapter payload carries gemrateWorkers",
          gemrate.worker_payload.get("gemrateWorkers"), 2)
    check("gemrate still runs at most 4 shards concurrently",
          gemrate.spec.max_concurrency, 4)
    worker_source = (PIPELINES / "daily_chain_v2_worker.py").read_text(encoding="utf-8")
    check(
        "worker forwards gemrateWorkers to collect_control",
        'gemrate_workers=int(worker.get("gemrateWorkers") or 1)' in worker_source,
        True,
    )


def test_parallel_shard_path_is_intact() -> None:
    source = (PIPELINES / "gemrate_source.py").read_text(encoding="utf-8")
    check("round-robin sharding kept", "pending[offset::worker_count]" in source, True)
    check("1.5s stagger kept", "offset * 1.5" in source, True)
    check("per-card 429 ladder kept", "RATE_LIMIT_LADDER[ladder_step]" in source, True)


# --------------------------------------------------------------------- P1-7


def test_transport_outage_discriminator() -> None:
    outage = {
        "transports": {
            gs.WEBSITE_TRANSPORT: {"attempted": 2, "succeeded": 0, "failed": 2},
            gs.DIRECT_TRANSPORT: {"attempted": 0, "succeeded": 0, "failed": 0},
        }
    }
    partial = {
        "transports": {
            gs.WEBSITE_TRANSPORT: {"attempted": 2, "succeeded": 1, "failed": 1},
        }
    }
    check("whole-cohort zero harvest is a lane failure",
          cc._gemrate_transport_outage(outage, 2), True)
    check("one success is not a lane failure",
          cc._gemrate_transport_outage(partial, 2), False)
    check("a smaller attempt set is not a lane failure",
          cc._gemrate_transport_outage(outage, 5), False)


def _write_outage_manifest(runs_dir: Path) -> Path:
    run_dir = runs_dir / "daily_20260824T000000000000Z_0-of-4"
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schemaVersion": "2.0.0",
        "fetchedAt": "2026-08-24T00:00:00Z",
        "attempted": 2, "succeeded": 0, "failed": 2,
        "partial": True, "promotable": False, "promoted": False,
        "transports": {
            gs.WEBSITE_TRANSPORT: {"attempted": 2, "succeeded": 0, "failed": 2},
            gs.DIRECT_TRANSPORT: {"attempted": 0, "succeeded": 0, "failed": 0},
            gs.MIRROR_TRANSPORT: {"attempted": 0, "succeeded": 0, "failed": 0},
        },
        "observations": [], "resolved": [],
        "unresolved": [
            {"gemrateId": HEX_A, "reason": "browser_collection_failed:TimeoutError"},
            {"gemrateId": HEX_B, "reason": "browser_collection_failed:TimeoutError"},
        ],
        "websiteFailureReceipts": {
            HEX_A: "browser_collection_failed:TimeoutError",
            HEX_B: "browser_collection_failed:TimeoutError",
        },
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run_dir / "manifest.json"


def test_transport_outage_advances_no_quarantine_streak() -> None:
    recorded: list[dict[str, Any]] = []

    def spy_record(adapter, *, succeeded, failed):
        recorded.append({"adapter": adapter, "failed": list(failed)})

    def fake_run(cmd, *, timeout, dry_run):
        _write_outage_manifest(cc.ROOT / "data/private/gemrate/runs")
        return {"cmd": cmd, "exit": 1}

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "data/private/gemrate/runs").mkdir(parents=True, exist_ok=True)
        original = (cc.ROOT, cc.OUT_DIR, cc.QUARANTINE_PATH, cc._run,
                    cc._record_item_outcomes)
        cc.ROOT = root
        cc.OUT_DIR = root / "out"
        cc.QUARANTINE_PATH = root / "quarantine.json"
        cc._run = fake_run
        cc._record_item_outcomes = spy_record
        try:
            report = cc.run_gemrate_pop(
                [{"variantId": 1, "externalId": HEX_A},
                 {"variantId": 2, "externalId": HEX_B}],
                mode="incr", limit=None, dry_run=False, work_scope="0-of-4",
            )
        finally:
            (cc.ROOT, cc.OUT_DIR, cc.QUARANTINE_PATH, cc._run,
             cc._record_item_outcomes) = original

    check("lane failure is named", report.get("error"), "gemrate_transport_unavailable")
    check("lane still fails", report.get("ok"), False)
    check("no per-item streak advanced", recorded, [])


def test_quarantine_streak_decays() -> None:
    stale = (datetime.now(timezone.utc) - timedelta(days=QUARANTINE_DECAY_PROBE)).isoformat()
    fresh = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    check("old streak decays", cc._streak_has_decayed({"lastAttempt": stale}), True)
    check("recent streak stands", cc._streak_has_decayed({"lastAttempt": fresh}), False)
    check("no timestamp keeps the streak", cc._streak_has_decayed({}), False)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "quarantine.json"
        path.write_text(json.dumps({
            "contract": cc.QUARANTINE_CONTRACT,
            "updatedAt": stale,
            "adapters": {"gemrate_pop": {
                f"1:{HEX_A}": {"variantId": 1, "externalId": HEX_A,
                               "consecutiveFailures": 3, "lastAttempt": stale},
                f"2:{HEX_B}": {"variantId": 2, "externalId": HEX_B,
                               "consecutiveFailures": 3, "lastAttempt": fresh},
            }},
        }), encoding="utf-8")
        original = cc.QUARANTINE_PATH
        cc.QUARANTINE_PATH = path
        try:
            quarantined = cc._quarantined_streams("gemrate_pop")
        finally:
            cc.QUARANTINE_PATH = original
    check("decayed item is retried again", f"1:{HEX_A}" in quarantined, False)
    check("fresh quarantine still holds", f"2:{HEX_B}" in quarantined, True)


# --------------------------------------------------------------------- P2-12


def test_manifest_reuse_is_run_anchored() -> None:
    # 2026-08-24 live shape: run created 2026-08-23T00:38:37Z (09:38 JST) for
    # business_date 2026-08-24, manifest fetched 2026-08-23T00:38:56Z.
    business_date = "2026-08-24"
    started = "2026-08-23T00:38:37.000000+00:00"
    fetched = "2026-08-23T00:38:56Z"
    saved_env = {
        key: os.environ.get(key)
        for key in ("CARDZ_DAILY_CHAIN_V2", "CARDZ_V2_BUSINESS_DATE",
                    "CARDZ_V2_RUN_STARTED_AT")
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        runs = root / "data/private/gemrate/runs"
        run_dir = runs / "daily_20260823T003856907796Z_0-of-4"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(json.dumps({
            "fetchedAt": fetched,
            "resolved": [{"gemrateId": HEX_A}],
            "promoted": True, "promotable": True,
        }), encoding="utf-8")
        os.environ["CARDZ_DAILY_CHAIN_V2"] = "1"
        os.environ["CARDZ_V2_BUSINESS_DATE"] = business_date
        os.environ["CARDZ_V2_RUN_STARTED_AT"] = started
        original_root = cc.ROOT
        cc.ROOT = root
        try:
            window = cc._gemrate_reuse_window_start(business_date)
            reusable = cc._reusable_gemrate_manifest(
                [{"variantId": 1, "externalId": HEX_A}], "0-of-4"
            )
            os.environ["CARDZ_V2_RUN_STARTED_AT"] = ""
            no_anchor = cc._gemrate_reuse_window_start(business_date)
        finally:
            cc.ROOT = original_root
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    check("window opens at the run, not JST midnight",
          window.isoformat(), "2026-08-23T00:38:37+00:00")
    check("manual business date still reuses its own receipt",
          reusable is not None, True)
    check("without a run anchor the window is JST midnight",
          no_anchor.astimezone(timezone.utc).isoformat(), "2026-08-23T15:00:00+00:00")


def test_manifest_reuse_rejects_a_future_receipt() -> None:
    business_date = "2026-08-24"
    saved = os.environ.get("CARDZ_V2_RUN_STARTED_AT")
    try:
        os.environ["CARDZ_V2_RUN_STARTED_AT"] = "2026-08-23T00:38:37+00:00"
        window = cc._gemrate_reuse_window_start(business_date)
    finally:
        if saved is None:
            os.environ.pop("CARDZ_V2_RUN_STARTED_AT", None)
        else:
            os.environ["CARDZ_V2_RUN_STARTED_AT"] = saved
    check("window start is timezone aware", window.tzinfo is not None, True)
    check("window start never runs ahead of now",
          window <= datetime.now(timezone.utc), True)


# --------------------------------------------------------------------- P2-13


def _run_pc_partition(map_rows: list[dict[str, Any]], items: list[dict[str, Any]]):
    fake_module = types.ModuleType("pc_psa10_price_derivation")
    fake_module.validate_pc_psa10 = lambda row: (  # type: ignore[attr-defined]
        {"field": "psa10", "observed_date": "2026-08-24"}, "local_exact_html_ok"
    )
    saved_module = sys.modules.get("pc_psa10_price_derivation")
    sys.modules["pc_psa10_price_derivation"] = fake_module
    original_map = cc._pc_subset_map
    cc._pc_subset_map = lambda *_a, **_k: ({}, map_rows)
    try:
        return cc.partition_local_pc_stock_pages(
            items, mode="incr", dry_run=False, force_network=False
        )
    finally:
        cc._pc_subset_map = original_map
        if saved_module is None:
            sys.modules.pop("pc_psa10_price_derivation", None)
        else:
            sys.modules["pc_psa10_price_derivation"] = saved_module


def test_pc_local_replay_guards() -> None:
    items = [{"variantId": 11}, {"variantId": 22}, {"variantId": 33}]
    rows = [
        # empty html_path: ROOT / "" resolves to ROOT, so stat() used to succeed
        # on a directory and read_bytes() raised IsADirectoryError.
        {"variant_id": 11, "html_path": ""},
        {"variant_id": 22, "html_path": "data/private/does-not-exist.html"},
        # variant 33 is absent from the map entirely -> used to be a KeyError.
    ]
    replayed, network, report = _run_pc_partition(rows, items)
    reasons = report["networkReasons"]
    check("empty map field never replays", replayed, [])
    check("all three fall back to the network lane", len(network), 3)
    check("empty map field is named", reasons.get("11"), "local_exact_map_html_path_empty")
    check("missing artifact is named", reasons.get("22"), "local_exact_html_missing")
    check("absent map row is named", reasons.get("33"), "local_exact_map_row_missing")
    check("map contract error is reported", report.get("mapContractErrors"), [11])


# ------------------------------- operator item 10: repair due set never shrinks


def test_repair_due_set_is_frozen_by_explicit_variant_ids() -> None:
    """The 2026-08-24 finding, pinned so the shape cannot come back unnoticed.

    A contract-repair task freezes its targets into the task payload and
    ``_collect_mode_impl`` selects on ``explicit_variants`` instead of the
    checkpoint-driven ``_due`` set, so the SAME task always re-fetches the full
    cohort. Progress only shrinks the due set through the next plan() pass, and
    that pass can only see progress if the interrupted attempt ingested.
    """
    source = (PIPELINES / "collect_control.py").read_text(encoding="utf-8")
    check(
        "explicit variant IDs still bypass the due/checkpoint filter",
        "if explicit_variants" in source and "else _due(reg, adapter, modes)" in source,
        True,
    )
    check(
        "interrupted child still ingests before failing the lane",
        '"error": "gemrate_child_interrupted"' in source,
        True,
    )
    check(
        "no forbidden CARDS_DIR resume shortcut",
        "cards_dir=CARDS_DIR, resume=True" in source,
        False,
    )


# ---------------------------------- 2026-09-26 Cloudflare block page, GemRate


def test_block_page_is_told_from_the_challenge() -> None:
    check("block page title is a block",
          gs.is_cloudflare_block(403, "Attention Required! | Cloudflare", ""), True)
    check("block page heading is a block",
          gs.is_cloudflare_block(403, "", "Sorry, you have been blocked\nYou are unable to access"), True)
    check("the self-clearing JS challenge is not a block",
          gs.is_cloudflare_block(403, "Just a moment...", "Checking your browser"), False)
    for status in (200, None, 503):
        check(f"status {status} is never a block",
              gs.is_cloudflare_block(status, "Attention Required! | Cloudflare",
                                     "Sorry, you have been blocked"), False)


class _Status:
    def __init__(self, status: int) -> None:
        self.status = status


class BlockPage:
    """A card page answered `status`; its settled document reads `seen`."""

    def __init__(self, status: int, seen: Any, clock: list[float]) -> None:
        self.status, self.seen, self.clock = status, seen, clock
        self.listener: Any = None
        self.waits: list[int] = []
        self.probes = 0
        self.dom_polls = 0

    def on(self, _event: str, callback: Any) -> None:
        self.listener = callback

    def remove_listener(self, _event: str, _callback: Any) -> None:
        self.listener = None

    def goto(self, _url: str, **_kwargs: Any) -> _Status:
        return _Status(self.status)

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)
        self.clock[0] += ms / 1000.0

    def evaluate(self, _script: str, args: Any = None) -> Any:
        if args is None:  # the block probe reads title + body text
            self.probes += 1
            if isinstance(self.seen, Exception):
                raise self.seen
            return self.seen
        self.dom_polls += 1
        return {"__failureReason": "population_table_missing"}


def _fetch_once_on(page: BlockPage) -> tuple[Any, Any, Any]:
    real_time = gs.time
    gs.time = type("FakeClock", (), {"monotonic": staticmethod(lambda: page.clock[0])})
    try:
        return gs._fetch_card_once(page, HEX_A)
    finally:
        gs.time = real_time


BLOCK_DOCUMENT = {"title": "Attention Required! | Cloudflare",
                  "text": "Sorry, you have been blocked"}


def test_block_page_is_a_verdict_without_the_json_wait() -> None:
    page = BlockPage(403, BLOCK_DOCUMENT, [0.0])
    payload, failure, limited = _fetch_once_on(page)
    check("block page: no payload, not rate limited", (payload, limited), (None, False))
    check("block page: receipt names the block",
          failure, {"gemrateId": HEX_A, "httpStatus": 403, "reason": gs.CLOUDFLARE_BLOCK_REASON})
    check("block page: no JSON wait, no DOM poll", (page.waits, page.dom_polls), ([], 0))
    check("block page: the response listener is removed", page.listener, None)

    for label, seen in (
        ("JS challenge", {"title": "Just a moment...", "text": "Checking your browser"}),
        ("unreadable document", RuntimeError("Execution context was destroyed")),
    ):
        page = BlockPage(403, seen, [0.0])
        _payload, failure, _limited = _fetch_once_on(page)
        check(f"{label}: stays on the slow path", len(page.waits) > 0 and page.dom_polls == 1, True)
        check(f"{label}: is no block verdict", (failure or {}).get("reason"), "population_table_missing")

    page = BlockPage(200, BLOCK_DOCUMENT, [0.0])
    _fetch_once_on(page)
    check("a 200 page is never probed for the block", page.probes, 0)


def _collect_with(outcomes: dict[str, str], ids: list[str], workers: int) -> tuple[dict[str, Any], list[str]]:
    """collect_public_card_details over fake pages: 'block' or 'ok' per id."""

    page = FakePage()
    calls: list[str] = []
    lock = threading.Lock()

    @contextmanager
    def fake_session():
        yield page

    def fake_fetch(_page: Any, gid: str):
        with lock:
            calls.append(gid)
        if outcomes.get(gid) == "ok":
            return {"gemrate_id": gid, "population_data": []}, None, False
        return None, gs._public_failure_receipt(
            gid, http_status=403, reason=gs.CLOUDFLARE_BLOCK_REASON,
        ), False

    original = (gs._gemrate_public_page, gs._fetch_card_once, gs._persist_public_card_capture)
    gs._gemrate_public_page = fake_session
    gs._fetch_card_once = fake_fetch
    gs._persist_public_card_capture = lambda *_a, **_k: {}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            outcome = gs.collect_public_card_details(
                ids, cards_dir=Path(tmp), delay=0.0, chunk_size=25, workers=workers,
            )
    finally:
        gs._gemrate_public_page, gs._fetch_card_once, gs._persist_public_card_capture = original
    return outcome, calls


def _reasons(outcome: dict[str, Any]) -> dict[str, str]:
    return {str(row["gemrateId"]): str(row["reason"]) for row in outcome.get("failureReceipts") or []}


def test_block_breaker_stops_the_pass() -> None:
    ids = [f"{index:040x}" for index in range(6)]
    blocked = {gid: gs.CLOUDFLARE_BLOCK_REASON for gid in ids[:3]}
    not_attempted = {gid: gs.CLOUDFLARE_BLOCK_NOT_ATTEMPTED for gid in ids[3:]}

    outcome, calls = _collect_with({}, ids, workers=1)
    check("breaker: three block pages in a row, then no more requests", calls, ids[:3])
    check("breaker: the outcome says blocked", (outcome.get("blocked"), outcome.get("error")),
          (True, gs.CLOUDFLARE_BLOCK_REASON))
    check("breaker: the rest are named not attempted", _reasons(outcome), {**blocked, **not_attempted})

    plan = dict(zip(ids, ("block", "block", "ok", "block", "block", "ok")))
    outcome, calls = _collect_with(plan, ids, workers=1)
    check("breaker: a card that loads resets the streak", calls, ids)
    check("breaker: two in a row is not a block",
          (outcome.get("blocked"), outcome.get("error"), outcome.get("succeeded")), (False, None, 2))

    # workers=2 shards round-robin: w1 gets ids 0/2/4, w2 waits out its 1.5s
    # stagger, and w1's third block page stops w2 before it opens a page.
    outcome, calls = _collect_with({}, ids, workers=2)
    check("breaker: one worker's block stops its sibling", calls, [ids[0], ids[2], ids[4]])
    check("breaker: the sibling's cards are not attempted",
          {gid: reason for gid, reason in _reasons(outcome).items() if gid in ids[1::2]},
          {gid: gs.CLOUDFLARE_BLOCK_NOT_ATTEMPTED for gid in ids[1::2]})
    check("breaker: blocked across workers", outcome.get("blocked"), True)


def _run_cmd_daily(fake_collect) -> tuple[int, dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ids_file = root / "ids.txt"
        ids_file.write_text(f"{HEX_A}\n{HEX_B}\n", encoding="utf-8")
        args = types.SimpleNamespace(
            ids_file=str(ids_file), speed="fast", limit=None, run_suffix="",
            identity_file=str(root / "no-identity.json"),
            mirror_root=str(root / "no-mirror"),
            website_budget_seconds=60, workers=1,
            with_history=False, no_history=True, skip_grader_volume=True,
            volume_slug=None,
        )
        original = (gs.OUT_DIR, gs.CARDS_DIR, gs.collect_public_card_details,
                    os.environ.get("GEMRATE_API_KEY"))
        out_dir = root / "out"
        gs.OUT_DIR = out_dir
        gs.CARDS_DIR = out_dir / "cards"
        gs.collect_public_card_details = fake_collect
        os.environ["GEMRATE_API_KEY"] = ""
        try:
            rc = gs.cmd_daily(args)
        finally:
            gs.OUT_DIR, gs.CARDS_DIR, gs.collect_public_card_details = original[:3]
            if original[3] is None:
                os.environ.pop("GEMRATE_API_KEY", None)
            else:
                os.environ["GEMRATE_API_KEY"] = original[3]
        run_dirs = sorted((out_dir / "runs").glob("daily_*"))
        manifest = (
            json.loads((run_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
            if len(run_dirs) == 1 else {}
        )
    return rc, manifest


def test_cmd_daily_skips_the_slow_retry_when_blocked() -> None:
    calls: list[list[str]] = []

    def fake_collect(ids, **_kwargs):
        calls.append(list(ids))
        return {
            "transport": gs.WEBSITE_TRANSPORT, "requested": len(ids), "attempted": len(ids),
            "succeeded": 0, "failed": len(ids), "error": gs.CLOUDFLARE_BLOCK_REASON,
            "blocked": True,
            "failureReceipts": [
                {"gemrateId": HEX_A, "httpStatus": 403, "reason": gs.CLOUDFLARE_BLOCK_REASON},
                {"gemrateId": HEX_B, "httpStatus": None,
                 "reason": gs.CLOUDFLARE_BLOCK_NOT_ATTEMPTED},
            ],
        }

    rc, manifest = _run_cmd_daily(fake_collect)
    check("blocked run is still a declared partial", rc, 1)
    check("no slow-retry pass knocks on the block again", len(calls), 1)
    blocked = manifest.get("blocked") or {}
    check("manifest says who blocked and on which pass",
          (blocked.get("reason"), blocked.get("breaker"), blocked.get("pass")),
          (gs.CLOUDFLARE_BLOCK_REASON, gs.CLOUDFLARE_BLOCK_BREAKER, "first"))
    check("unresolved reasons keep the transport receipts",
          {row["gemrateId"]: row["reason"] for row in manifest.get("unresolved") or []},
          {HEX_A: gs.CLOUDFLARE_BLOCK_REASON, HEX_B: gs.CLOUDFLARE_BLOCK_NOT_ATTEMPTED})


def _write_blocked_manifest(runs_dir: Path, resolved: list[str]) -> None:
    run_dir = runs_dir / "daily_20260926T000000000000Z_0-of-4"
    run_dir.mkdir(parents=True, exist_ok=True)
    reasons = {HEX_A: gs.CLOUDFLARE_BLOCK_REASON, HEX_B: gs.CLOUDFLARE_BLOCK_NOT_ATTEMPTED}
    unresolved = {gid: reason for gid, reason in reasons.items() if gid not in resolved}
    counts = {"attempted": 2, "succeeded": len(resolved), "failed": len(unresolved)}
    idle = {"attempted": 0, "succeeded": 0, "failed": 0}
    manifest = {
        "schemaVersion": "2.0.0", "fetchedAt": "2026-09-26T00:00:00Z", **counts,
        "partial": True, "promotable": False, "promoted": False,
        "transports": {gs.WEBSITE_TRANSPORT: counts, gs.DIRECT_TRANSPORT: idle,
                       gs.MIRROR_TRANSPORT: idle},
        "observations": [], "resolved": [{"gemrateId": gid} for gid in resolved],
        "unresolved": [{"gemrateId": gid, "reason": reason} for gid, reason in unresolved.items()],
        "websiteFailureReceipts": unresolved,
        "blocked": {"reason": gs.CLOUDFLARE_BLOCK_REASON, "breaker": 3, "pass": "first",
                    "at": "2026-09-26T00:00:00Z"},
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _run_blocked_pop(resolved: list[str]) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    seen: dict[str, list[Any]] = {"streaks": [], "succeeded": [], "ingested": [], "checkpointed": []}

    def spy_record(_adapter, *, succeeded, failed):
        seen["succeeded"].extend(int(item["variantId"]) for item in succeeded)
        seen["streaks"].extend(int(item["variantId"]) for item in failed)

    def fake_run(cmd, *, timeout, dry_run):
        _write_blocked_manifest(cc.ROOT / "data/private/gemrate/runs", resolved)
        return {"cmd": cmd, "exit": 1}

    def fake_ingest(_manifest, items):
        seen["ingested"].extend(int(item["variantId"]) for item in items)
        return {"runId": "fixture-run", "inserted": len(items), "checkpointed": len(items)}

    def fake_checkpoints(_adapter, *, mode, items, payload_sha_by_external, run_id):
        seen["checkpointed"].extend(int(item["variantId"]) for item in items)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "data/private/gemrate/runs").mkdir(parents=True, exist_ok=True)
        names = ("ROOT", "OUT_DIR", "QUARANTINE_PATH", "_run", "_record_item_outcomes",
                 "_db_writer_lease", "_ingest_gemrate_manifest", "_persist_item_checkpoints")
        original = {name: getattr(cc, name) for name in names}
        cc.ROOT = root
        cc.OUT_DIR = root / "out"
        cc.QUARANTINE_PATH = root / "quarantine.json"
        cc._run = fake_run
        cc._record_item_outcomes = spy_record
        cc._db_writer_lease = nullcontext
        cc._ingest_gemrate_manifest = fake_ingest
        cc._persist_item_checkpoints = fake_checkpoints
        try:
            report = cc.run_gemrate_pop(
                [{"variantId": 1, "externalId": HEX_A}, {"variantId": 2, "externalId": HEX_B}],
                mode="incr", limit=None, dry_run=False, work_scope="0-of-4",
            )
        finally:
            for name, value in original.items():
                setattr(cc, name, value)
    return report, seen


def test_blocked_manifest_is_one_lane_verdict() -> None:
    report, seen = _run_blocked_pop([])
    # The same manifest is also a whole-cohort zero harvest; the block wins.
    check("blocked lane: named as the block, not a transport outage",
          (report.get("ok"), report.get("error")), (False, cc.GEMRATE_BLOCKED_ERROR))
    check("blocked lane: the receipt carries the block",
          (report.get("blocked") or {}).get("reason"), gs.CLOUDFLARE_BLOCK_REASON)
    check("blocked lane: no quarantine streak advances", seen["streaks"], [])
    check("blocked lane: both cards counted as transport skips",
          report.get("quarantineStreaksSkipped"), 2)

    report, seen = _run_blocked_pop([HEX_A])
    check("one card landed before the block: it is ingested and checkpointed",
          (seen["ingested"], seen["checkpointed"], seen["succeeded"]), ([1], [1], [1]))
    check("one card landed before the block: the lane still says blocked",
          (report.get("ok"), report.get("error"), report.get("inserted")),
          (False, cc.GEMRATE_BLOCKED_ERROR, 1))
    check("one card landed before the block: the other card is no streak", seen["streaks"], [])


def test_worker_names_the_collector_block_string() -> None:
    import daily_chain_v2_worker as v2worker

    check("worker's pinned error is what run_gemrate_pop emits",
          v2worker.GEMRATE_BLOCKED_ADAPTER_ERROR, cc.GEMRATE_BLOCKED_ERROR)


QUARANTINE_DECAY_PROBE = 8


def main() -> int:
    case("P0-2 warm-up retry", test_warmup_retry_survives_transient_session_failures)
    case("P0-2 ladder exhaustion", test_warmup_gives_up_after_the_ladder_and_keeps_the_class)
    case("P2-10 error class", test_safe_browser_error_keeps_the_exception_class)
    case("P1-6/P2-11/P2-9/P2-10 cmd_daily", test_cmd_daily_pass_loop_and_transport_receipts)
    case("P2-9 reason map", test_build_run_prefers_the_transport_reason)
    case("P2-11 poll interval", test_json_poll_interval_is_50ms)
    case("item 10 shutdown keeps payloads", test_shutdown_returns_captured_payloads_instead_of_raising)
    case("item 10 deferred termination", test_deferred_termination_holds_sigterm)
    case("item 10 worker grace", test_worker_shutdown_grace_lets_the_ingest_land)
    case("P1-5 payload driven workers", test_gemrate_workers_is_payload_driven)
    case("P1-5 ceiling", test_gemrate_workers_fails_closed_above_the_ceiling)
    case("P1-5 adapter payload", test_adapter_payload_and_worker_thread_the_knob)
    case("P1-5 parallel path intact", test_parallel_shard_path_is_intact)
    case("P1-7 outage discriminator", test_transport_outage_discriminator)
    case("P1-7 no streak on outage", test_transport_outage_advances_no_quarantine_streak)
    case("P1-7 streak decay", test_quarantine_streak_decays)
    case("P2-12 run anchored reuse", test_manifest_reuse_is_run_anchored)
    case("P2-12 window bounds", test_manifest_reuse_rejects_a_future_receipt)
    case("P2-13 pc local guards", test_pc_local_replay_guards)
    case("item 10 repair due set", test_repair_due_set_is_frozen_by_explicit_variant_ids)
    case("09-26 block page vs challenge", test_block_page_is_told_from_the_challenge)
    case("09-26 block page skips the waits", test_block_page_is_a_verdict_without_the_json_wait)
    case("09-26 block breaker", test_block_breaker_stops_the_pass)
    case("09-26 cmd_daily blocked", test_cmd_daily_skips_the_slow_retry_when_blocked)
    case("09-26 blocked lane verdict", test_blocked_manifest_is_one_lane_verdict)
    case("09-26 worker error pin", test_worker_names_the_collector_block_string)

    for line in FAILED:
        print(line)
    print(f"{CHECKS - len(FAILED)}/{CHECKS} checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
