#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PC (PriceCharting / CDP 9333) lane durability guards.

2026-08-22 的一轉：child 用 capture_output 跑、report 淨係喺收工或者 watchdog
stall 兩個位寫、Cloudflare 一路擋一路重試燒到 TERMINAL、殺 stall 靠一個裸 PID
加一個全域 stamp、collect_control 自己又去開 Chrome。呢個檔逐樣守返：

1. partial report：任何死法（exception / Ctrl-C / SIGTERM / 每 10 版）都要留低
   ``partial: true`` + ``stop_reason``，而且下一轉 auto-resume 要接得返。
2. CF storm breaker：連續 N 版 challenge/403 就停，child exit 4，
   collect_control 譯做 ``pc_cf_storm``（可重試、backoff >= 20 分鐘、唔准當
   identity 唔見咗或者 coverage 蝕咗）。
3. child log + hidden launch：stdout/stderr 落 data/runtime/pc/、rc 由 rc-file
   讀（WSL interop 唔保證帶得返 rc）、最後 20 行貼返入自己個 log。
4. ensure_cdp：鏈入面淨係驗身份，唔通就 ``cdp_unreachable``（可重試）。
5. F-COLLECT-DISPATCH：SOURCE_ADAPTERS 一個 registry；未登記嘅 source 一定要
   出 ``source_not_registered``，唔准靜靜跳過。

Run: python -X utf8 scripts/test_pc_lane_durability.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import collect_control as cc  # noqa: E402
import collection_contract as contract  # noqa: E402
import pc_cdp_sold_refresh_win as mod  # noqa: E402

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# 1. partial report durability
# ---------------------------------------------------------------------------
def test_partial_report(tmp: Path) -> None:
    report_path = tmp / "pc_cdp_refresh_report.json"
    original_out, original_stamp_dir, original_main = (
        mod.OUT,
        mod.PROGRESS_STAMP_DIR,
        mod.main,
    )
    mod.OUT = report_path
    mod.PROGRESS_STAMP_DIR = tmp / "stamps"
    results: list[dict] = []
    state = mod.new_stall_watchdog_state(batch=25, results=results)
    seen: dict[str, object] = {}

    def fake_main() -> int:
        mod.register_report_state(
            batch=25,
            results=results,
            base={
                "cdpPort": 9333,
                "singleBrowserSession": True,
                "fallbackBrowsers": 0,
            },
        )
        for index in range(12):
            results.append(
                {"variant_id": index, "status": "ok", "len": 6000, "code": 200}
            )
            mod.mark_page_decision(state)
            if index == 8:  # 9 decisions so far: below the every-10 flush
                seen["after9"] = report_path.exists()
            if index == 9:  # the 10th decision must have flushed
                seen["after10"] = (
                    json.loads(report_path.read_text(encoding="utf-8"))
                    if report_path.is_file()
                    else {"partial": "no report after 10 pages"}
                )
        raise RuntimeError("injected CDP death after 12 pages")

    mod.main = fake_main  # type: ignore[assignment]
    raised = ""
    try:
        mod.run_cli()
    except RuntimeError as exc:  # noqa: BLE001
        raised = str(exc)
    finally:
        mod.main = original_main  # type: ignore[assignment]

    check("injected exception still propagates", raised.startswith("injected"), True)
    check("no partial report before the 10th page", seen.get("after9"), False)
    flushed = seen.get("after10") or {}
    check("every-10-pages flush wrote a partial report", flushed.get("partial"), True)
    check("mid-run flush counts the pages already proved", flushed.get("ok"), 10)

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    check("death path writes partial=true", payload.get("partial"), True)
    check(
        "death path records why it stopped",
        payload.get("stop_reason"),
        "exception:RuntimeError",
    )
    check("partial report keeps the proved pages", payload.get("ok"), 12)
    check("partial report keeps the real batch size", payload.get("batch"), 25)
    check(
        "partial report keeps the strict single-browser resume contract",
        (
            payload.get("singleBrowserSession"),
            payload.get("fallbackBrowsers"),
            payload.get("cdpPort"),
        ),
        (True, 0, 9333),
    )
    check(
        "auto-resume picks up a partial report",
        mod.should_auto_resume_report(payload, now=datetime.now(timezone.utc)),
        True,
    )
    check(
        "a finished sweep is still not auto-resumed",
        mod.should_auto_resume_report(
            {"asOf": payload["asOf"], "ok": 25, "batch": 25},
            now=datetime.now(timezone.utc),
        ),
        False,
    )

    # A run that never armed (bad flag, --help) must not clobber the receipt.
    mod._REPORT_STATE["armed"] = False
    before = report_path.read_bytes()
    check("unarmed run writes nothing", mod.write_partial_report("boom"), False)
    check("previous receipt survives an unarmed run", report_path.read_bytes(), before)

    # ARMED but zero progress (e.g. the dual-tab SystemExit guard fires right
    # after register_report_state): the fallback write must NOT clobber the
    # previous run's usable receipt with an empty partial.
    good_receipt = {"batch": 900, "ok": 900, "asOf": mod.utc_now(), "results": []}
    mod.write_report_atomic(good_receipt, report_path)

    def _systemexit_main() -> int:
        mod.register_report_state(
            batch=0,
            results=[],
            base={"cdpPort": 9333, "singleBrowserSession": True, "fallbackBrowsers": 0},
        )
        raise SystemExit("9333 PC script is dual-tab only")

    mod.main = _systemexit_main  # type: ignore[assignment]
    try:
        mod.run_cli()
    except SystemExit:
        pass
    finally:
        mod.main = original_main  # type: ignore[assignment]
    survived = json.loads(report_path.read_text(encoding="utf-8"))
    check(
        "a zero-progress SystemExit keeps the previous receipt",
        (survived.get("batch"), survived.get("ok")),
        (900, 900),
    )
    check(
        "a zero-progress SystemExit writes no partial marker",
        "partial" in survived,
        False,
    )

    # The zero-progress rule lives inside write_partial_report, so the
    # exception handler and the signal handler obey it too (2026-08-22 verify:
    # only the finally fallback was guarded; `except BaseException` and the
    # SIGTERM handler still wrote an empty partial over a good receipt).
    mod.write_report_atomic(good_receipt, report_path)

    def _exception_main() -> int:
        mod.register_report_state(
            batch=0,
            results=[],
            base={"cdpPort": 9333, "singleBrowserSession": True, "fallbackBrowsers": 0},
        )
        raise RuntimeError("cdp died before the first page")

    mod.main = _exception_main  # type: ignore[assignment]
    try:
        mod.run_cli()
    except RuntimeError:
        pass
    finally:
        mod.main = original_main  # type: ignore[assignment]
    survived = json.loads(report_path.read_text(encoding="utf-8"))
    check(
        "a zero-progress exception keeps the previous receipt",
        (survived.get("batch"), survived.get("ok"), "partial" in survived),
        (900, 900, False),
    )
    mod.register_report_state(
        batch=0,
        results=[],
        base={"cdpPort": 9333, "singleBrowserSession": True, "fallbackBrowsers": 0},
    )
    check(
        "a zero-progress signal write is refused",
        mod.write_partial_report("signal_15"),
        False,
    )
    check(
        "previous receipt survives the refused signal write",
        json.loads(report_path.read_text(encoding="utf-8")).get("ok"),
        900,
    )

    # Same death, but this run DID prove pages: the receipt must be replaced.
    proved = [{"variant_id": 1, "status": "ok", "len": 6000, "code": 200}]

    def _systemexit_main_with_progress() -> int:
        mod.register_report_state(
            batch=7,
            results=proved,
            base={"cdpPort": 9333, "singleBrowserSession": True, "fallbackBrowsers": 0},
        )
        raise SystemExit("dual-tab guard after real work")

    mod.main = _systemexit_main_with_progress  # type: ignore[assignment]
    try:
        mod.run_cli()
    except SystemExit:
        pass
    finally:
        mod.main = original_main  # type: ignore[assignment]
    replaced = json.loads(report_path.read_text(encoding="utf-8"))
    check("a SystemExit after real pages writes a partial", replaced.get("partial"), True)
    check(
        "that partial records why it stopped",
        replaced.get("stop_reason"),
        "interrupted",
    )
    check("that partial keeps the proved page", replaced.get("ok"), 1)
    check("that partial keeps the real batch size", replaced.get("batch"), 7)

    mod.OUT = original_out
    mod.PROGRESS_STAMP_DIR = original_stamp_dir


def test_sigterm_writes_partial(tmp: Path) -> None:
    """SIGTERM / SIGBREAK must still leave a receipt (real signal, real process)."""

    report_path = tmp / "signal_report.json"
    driver = tmp / "sigterm_driver.py"
    driver.write_text(
        "import os, signal, sys, time\n"
        f"sys.path.insert(0, {str(ROOT / 'pipelines')!r})\n"
        "from pathlib import Path\n"
        "import pc_cdp_sold_refresh_win as mod\n"
        f"mod.OUT = Path({str(report_path)!r})\n"
        f"mod.PROGRESS_STAMP_DIR = Path({str(tmp / 'stamps')!r})\n"
        "results = [{'variant_id': 1, 'status': 'ok'},"
        " {'variant_id': 2, 'status': 'cf_or_fail'}]\n"
        "mod.register_report_state(batch=9, results=results,"
        " base={'cdpPort': 9333, 'singleBrowserSession': True, 'fallbackBrowsers': 0})\n"
        "mod.install_partial_report_signals()\n"
        "os.kill(os.getpid(), signal.SIGTERM)\n"
        "time.sleep(10)\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(driver)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    check("SIGTERM exits through the partial-report handler", proc.returncode, 3)
    payload = (
        json.loads(report_path.read_text(encoding="utf-8"))
        if report_path.is_file()
        else {}
    )
    check("SIGTERM wrote a partial report", payload.get("partial"), True)
    check("SIGTERM records the signal", payload.get("stop_reason"), "signal_15")
    check("SIGTERM keeps the proved page", payload.get("ok"), 1)
    check("SIGTERM keeps the failed page", payload.get("cf"), 1)


# ---------------------------------------------------------------------------
# 2. Cloudflare storm breaker (child side)
# ---------------------------------------------------------------------------
PRODUCT_ID = "7108819"
BASE_URL = "https://www.pricecharting.com/game/pokemon-promo"


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status
        self.headers: dict[str, str] = {}


class FakePage:
    """One tab. `codes` maps variant id -> the HTTP status it keeps answering."""

    def __init__(self, codes: dict[str, int], seen: list[str]) -> None:
        self.codes = codes
        self.seen = seen
        self.url = BASE_URL
        self._current = ""
        self._cf = False

    async def goto(self, url, **_kwargs):
        self._current = url
        self.url = url
        vid = url.rsplit("/", 1)[-1]
        self.seen.append(vid)
        code = self.codes.get(vid, 200)
        self._cf = code in (403, "cf")
        return FakeResponse(403 if code == "cf" else code)

    def _html(self) -> str:
        body = (
            f'<html><head><link rel="canonical" href="{self._current}"></head>'
            f'<body product-id="{PRODUCT_ID}">' + ("x" * 6000) + "</body></html>"
        )
        return body + ("CFBLOCK" if self._cf else "")

    async def content(self):
        return self._html()

    async def title(self):
        return "Test Card PSA 10 Prices"

    async def wait_for_timeout(self, _ms):
        return None

    async def evaluate(self, _script, url):
        response = await self.goto(url)
        return {"status": response.status, "text": self._html(), "retryAfter": None}


def _rows(vids: list[str], tmp: Path) -> list[dict]:
    return [
        {
            "variant_id": vid,
            "pc_url": f"{BASE_URL}/{vid}",
            "pc_product_id": PRODUCT_ID,
            "html_path": str((tmp / f"{vid}.html").relative_to(ROOT)).replace("\\", "/"),
        }
        for vid in vids
    ]


def test_cf_storm_breaker(tmp: Path) -> None:
    import asyncio

    original_ladder, original_is_cf, original_validate = (
        mod.BACKOFF_LADDER,
        mod._is_cf,
        mod.validate_pc_psa10,
    )
    mod.BACKOFF_LADDER = (0.001, 0.001, 0.001)
    mod._is_cf = lambda _title, html: "CFBLOCK" in html  # type: ignore[assignment]
    mod.validate_pc_psa10 = lambda _row: (1.0, "ok")  # type: ignore[assignment]
    try:
        vids = [f"s{i}" for i in range(20)]
        rows = _rows(vids, tmp)
        seen: list[str] = []
        results: list[dict] = []
        out = asyncio.run(
            mod.run_fetch_pool_with_pages(
                rows,
                pages=[FakePage({vid: "cf" for vid in vids}, seen)],
                sleep_seconds=0.0,
                challenge_wait=0.0,
                watchdog={"beat": 0.0},
                results=results,
                start_index=0,
                batch_size=len(rows),
                transport="goto",
                cf_storm_threshold=3,
            )
        )
        check("a Cloudflare storm trips the breaker", out.get("cfStorm"), True)
        check("the breaker trips at the threshold", out.get("cfStormStreak"), 3)
        check("the breaker stops the sweep early", len(seen) < len(vids), True)

        seen_ok: list[str] = []
        out_ok = asyncio.run(
            mod.run_fetch_pool_with_pages(
                _rows(["a", "b", "c", "d"], tmp),
                pages=[FakePage({}, seen_ok)],
                sleep_seconds=0.0,
                challenge_wait=0.0,
                watchdog={"beat": 0.0},
                results=[],
                start_index=0,
                batch_size=4,
                transport="goto",
                cf_storm_threshold=3,
            )
        )
        check("a clean sweep never trips the breaker", out_ok.get("cfStorm"), False)
        check("a clean sweep still collects everything", out_ok.get("ok"), 4)

        # One CF page inside a healthy sweep is the 0.2% case, not a storm.
        seen_mix: list[str] = []
        out_mix = asyncio.run(
            mod.run_fetch_pool_with_pages(
                _rows(["m1", "m2", "m3", "m4"], tmp),
                pages=[FakePage({"m2": "cf"}, seen_mix)],
                sleep_seconds=0.0,
                challenge_wait=0.0,
                watchdog={"beat": 0.0},
                results=[],
                start_index=0,
                batch_size=4,
                transport="goto",
                cf_storm_threshold=8,
            )
        )
        check("one CF page below threshold is not a storm", out_mix.get("cfStorm"), False)
    finally:
        mod.BACKOFF_LADDER = original_ladder
        mod._is_cf = original_is_cf  # type: ignore[assignment]
        mod.validate_pc_psa10 = original_validate  # type: ignore[assignment]

    check("the storm threshold is env-tunable", mod.PC_CF_STORM_THRESHOLD >= 1, True)
    check("the storm exit code is 4", mod.EXIT_CF_STORM, 4)
    source = (ROOT / "pipelines" / "pc_cdp_sold_refresh_win.py").read_text(
        encoding="utf-8"
    )
    check(
        "main returns exit 4 with a pc_cf_storm receipt",
        'if pool.get("cfStorm")' in source
        and 'write_partial_report("pc_cf_storm")' in source
        and "return EXIT_CF_STORM" in source,
        True,
    )


# ---------------------------------------------------------------------------
# 2b / 1b. collect_control side: error class + partial is not a sweep
# ---------------------------------------------------------------------------
def test_pc_error_classes() -> None:
    check("exit 4 is a Cloudflare storm", cc.pc_child_error_class(4), "pc_cf_storm")
    check(
        "any other non-zero exit stays the generic failure",
        [cc.pc_child_error_class(code) for code in (1, 3, 124, None)],
        ["pc_cdp_refresh_failed"] * 4,
    )
    check("a Cloudflare storm is retryable", cc.pc_error_is_retryable("pc_cf_storm"), True)
    check(
        "a Cloudflare storm backs off at least 20 minutes",
        (cc.pc_error_retry_after_seconds("pc_cf_storm") or 0) >= 20 * 60,
        True,
    )
    check(
        "an unreachable CDP is retryable",
        cc.pc_error_is_retryable("cdp_unreachable"),
        True,
    )
    check(
        "the generic failure has no automatic backoff",
        cc.pc_error_is_retryable("pc_cdp_refresh_failed"),
        False,
    )


def _refresh_with_stub(
    tmp: Path,
    run_result: dict,
    source_report: dict | None,
    *,
    items: list[dict] | None = None,
    bind_missing_ids: list[int] | None = None,
):
    """Drive refresh_pc_pages with a stubbed child and a stubbed PC map."""

    html_path = tmp / "refresh.html"
    html_path.write_text("y" * 6000, encoding="utf-8")
    row = {
        "variant_id": 1,
        "pc_product_id": PRODUCT_ID,
        "pc_url": f"{BASE_URL}/1",
        "html_path": str(html_path.relative_to(ROOT)).replace("\\", "/"),
    }
    report_path = tmp / "pc_cdp_refresh_report.json"
    if report_path.exists():
        report_path.unlink()
    originals = (cc.OUT_DIR, cc.PC_REFRESH_REPORT, cc.WINDOWS_PY, cc._pc_subset_map, cc._run_pc_child)
    cc.OUT_DIR = tmp
    cc.PC_REFRESH_REPORT = report_path
    cc.WINDOWS_PY = ROOT / "pipelines" / "pc_cdp_hidden_launch.vbs"
    cc._pc_subset_map = lambda items, *, mode, label: (tmp / "map.jsonl", [row])  # type: ignore[assignment]

    def fake_child(cmd, *, timeout, dry_run, use_vbs=None):
        if source_report is not None:
            report_path.write_text(
                json.dumps({**source_report, "asOf": utc_stamp()}, ensure_ascii=False),
                encoding="utf-8",
            )
        return dict(run_result)

    cc._run_pc_child = fake_child  # type: ignore[assignment]
    try:
        return cc.refresh_pc_pages(
            [{"variantId": 1, "externalId": PRODUCT_ID, "modeNeeded": "incr"}]
            if items is None
            else items,
            mode="incr",
            dry_run=False,
            resume_report=None,
            sleep_seconds=None,
            tabs=None,
            cdp_already_ensured=True,
            bind_missing_ids=bind_missing_ids,
        )
    finally:
        (
            cc.OUT_DIR,
            cc.PC_REFRESH_REPORT,
            cc.WINDOWS_PY,
            cc._pc_subset_map,
            cc._run_pc_child,
        ) = originals


def test_refresh_maps_storm_and_rejects_partial(tmp: Path) -> None:
    complete = {
        "batch": 1,
        "ok": 1,
        "fail": 0,
        "cf": 0,
        "missingRequestedVariantIds": [],
        "results": [{"variant_id": 1, "status": "ok"}],
    }

    storm = _refresh_with_stub(
        tmp, {"exit": 4, "childLogTail": "PC_CF_STORM 8 consecutive"}, None
    )
    check("a storm fails the lane", storm.get("ok"), False)
    check("a storm is reported as pc_cf_storm", storm.get("errorClass"), "pc_cf_storm")
    check("a storm is retryable", storm.get("retryable"), True)
    check(
        "a storm backs off at least 20 minutes",
        (storm.get("retryAfterSeconds") or 0) >= 20 * 60,
        True,
    )
    check("a storm binds no identity as missing", storm.get("identityMissing"), [])
    check("a storm is not coverage loss", storm.get("coverageLoss"), False)

    generic = _refresh_with_stub(tmp, {"exit": 1, "childLogTail": ""}, None)
    check(
        "an ordinary child failure keeps the generic class",
        generic.get("errorClass"),
        "pc_cdp_refresh_failed",
    )
    check("an ordinary child failure is not auto-retried", generic.get("retryable"), False)

    partial = _refresh_with_stub(
        tmp,
        {"exit": 0, "childLogTail": ""},
        {**complete, "partial": True, "stop_reason": "keyboard_interrupt"},
    )
    check("a partial report is never a completed sweep", partial.get("ok"), False)
    check(
        "a partial report says so",
        "partial" in str(partial.get("error") or ""),
        True,
    )

    good = _refresh_with_stub(tmp, {"exit": 0, "childLogTail": ""}, complete)
    check("a complete report still passes", good.get("ok"), True)
    check(
        "a complete report hashes the refreshed HTML",
        bool((good.get("payloadShaByVariant") or {}).get("1")),
        True,
    )


# ---------------------------------------------------------------------------
# 3. child output streaming + hidden launch
# ---------------------------------------------------------------------------
def test_refresh_bind_only(tmp: Path) -> None:
    """No exact PC variant due, only the bind list: the sweep must still count.

    2026-08-22 attempt 15 (manual e2e): every exact page replayed from local
    stock, so refresh_pc_pages ran bind-only; the child fetched 152/152 and the
    contract step died with UnboundLocalError on ``map_rows`` -> both PC
    adapters failed with fresh_pc_pages_unavailable, task TERMINAL 15/15.
    """
    complete = {
        "batch": 1,
        "ok": 1,
        "fail": 0,
        "cf": 0,
        "missingRequestedVariantIds": [],
        "bindUnresolvedVariantIds": [8],
        "results": [{"variant_id": 7, "status": "ok"}],
    }
    bind_only = _refresh_with_stub(
        tmp, {"exit": 0, "childLogTail": ""}, complete, items=[], bind_missing_ids=[7, 8]
    )
    check("bind-only refresh (no exact variant due) succeeds", bind_only.get("ok"), True)
    check("bind-only refresh error is empty", bind_only.get("error"), None)
    check("bind-only refresh has no exact payload shas", bind_only.get("payloadShaByVariant"), {})
    check("bind-only refresh still hands the bind list to the child",
          bool(bind_only.get("bindMissingPath")), True)
    check("bind-only refresh surfaces unresolved bind ids",
          bind_only.get("bindUnresolvedVariantIds"), [8])


def test_child_log_and_rc(tmp: Path) -> None:
    check(
        "the child log is named per business date and pid",
        cc.pc_child_log_path(pid=1234, business_date="2026-08-22").name,
        "pc_cdp_2026-08-22_1234.log",
    )
    rc_file = tmp / "probe.log.rc"
    rc_file.write_text("5\n", encoding="utf-8")
    check("the rc-file is the authority on the child's exit", cc._read_rc_file(rc_file), 5)
    check("a missing rc-file is not an exit code", cc._read_rc_file(tmp / "nope.rc"), None)
    log_file = tmp / "tail.log"
    log_file.write_text("\n".join(f"line-{i}" for i in range(60)), encoding="utf-8")
    tail = cc._log_tail(log_file)
    check("only the last 20 lines are echoed", len(tail.splitlines()), 20)
    check("the tail is the end of the log", tail.splitlines()[-1], "line-59")

    launch = cc.pc_hidden_launch_command(
        ["python.exe", "script.py"], rc_path=tmp / "a.rc", log_path=tmp / "a.log"
    )
    check("the hidden launcher runs under wscript", launch[:3], ["wscript.exe", "//nologo", "//B"])
    check("C5 argument order is vbs, rc-file, log-file, command", launch[3].endswith("pc_cdp_hidden_launch.vbs"), True)
    check("the command comes last", launch[-2:], ["python.exe", "script.py"])
    if cc._running_under_wsl():
        # cmd.exe cannot open /mnt/c/...: the exe must cross as C:\...
        wsl_exe = cc.pc_hidden_launch_command(
            ["/mnt/c/Windows/System32/cmd.exe", "/c", "echo"],
            rc_path=tmp / "rc.txt",
            log_path=tmp / "log.txt",
        )
        check(
            "a WSL-path exe is handed to cmd.exe as a Windows path",
            (wsl_exe[-3].startswith("/mnt/"), wsl_exe[-3].lower().endswith("cmd.exe")),
            (False, True),
        )

    original_dir = cc.PC_CHILD_LOG_DIR
    cc.PC_CHILD_LOG_DIR = tmp / "pc-logs"
    try:
        stub = tmp / "stub_child.py"
        stub.write_text(
            "import sys\n"
            "print('child-line-1')\n"
            "print('child-line-2', file=sys.stderr)\n"
            "sys.exit(5)\n",
            encoding="utf-8",
        )
        direct = cc._run_pc_child(
            [sys.executable, "-X", "utf8", str(stub)],
            timeout=120,
            dry_run=False,
            use_vbs=False,
        )
        check("the native launch reports the child's exit code", direct.get("exit"), 5)
        log_text = Path(direct["childLog"]).read_text(encoding="utf-8")
        check("stdout landed in the child log", "child-line-1" in log_text, True)
        check("stderr landed in the same child log", "child-line-2" in log_text, True)
        check(
            "the tail is echoed even for a failing child",
            "child-line-1" in (direct.get("childLogTail") or ""),
            True,
        )

        if shutil.which("wscript.exe"):
            cmd_stub = tmp / "stub_child.cmd"
            cmd_stub.write_bytes(
                b"@echo off\r\n"
                b"echo vbs-line-1\r\n"
                b"echo vbs-line-2 1>&2\r\n"
                b"exit /b 5\r\n"
            )
            hidden = cc._run_pc_child(
                [cc._windows_path(cmd_stub)], timeout=180, dry_run=False, use_vbs=True
            )
            check("the hidden launch reads its rc from the rc-file", hidden.get("rcFileExit"), 5)
            check("collect_control reports the child's exit code", hidden.get("exit"), 5)
            hidden_log = Path(hidden["childLog"]).read_text(encoding="utf-8")
            check("the hidden child's stdout landed in the log", "vbs-line-1" in hidden_log, True)
            check("the hidden child's stderr landed in the log", "vbs-line-2" in hidden_log, True)
        else:
            print("SKIP hidden-launch: wscript.exe unavailable on this host")
    finally:
        cc.PC_CHILD_LOG_DIR = original_dir

    source = (ROOT / "pipelines" / "collect_control.py").read_text(encoding="utf-8")
    check(
        "refresh_pc_pages launches the child through the logging runner",
        "report[\"run\"] = _run_pc_child(" in source,
        True,
    )


# ---------------------------------------------------------------------------
# 4. ensure_cdp: identity only, cdp_unreachable
# ---------------------------------------------------------------------------
def _stub_args(args_file: Path, observed: dict) -> str:
    """What the stub ps1 was actually called with (or why it never ran)."""
    try:
        return args_file.read_text(encoding="utf-8-sig")
    except OSError:
        return f"stub never ran; ensure_cdp said {observed!r}"


def test_ensure_cdp_identity_only(tmp: Path) -> None:
    if not shutil.which("powershell.exe"):
        print("SKIP ensure_cdp: powershell.exe unavailable on this host")
        return
    stub_root = tmp / "cdp-stub"
    (stub_root / "scripts").mkdir(parents=True, exist_ok=True)
    args_file = stub_root / "args.txt"
    ps1 = stub_root / "scripts" / "ensure_chrome_cdp.ps1"
    exit_flag = stub_root / "exit.txt"
    exit_flag.write_text("1", encoding="utf-8")
    # The stub is run by Windows PowerShell, so it needs Windows paths even when
    # this test runs from WSL. ($args is an automatic variable - do not assign.)
    args_win = cc._windows_path(args_file)
    exit_win = cc._windows_path(exit_flag)
    ps1.write_bytes(
        (
            "param([int]$Port = 9333, [switch]$IdentityOnly, [switch]$SelfTest)\r\n"
            f"$line = \"Port=$Port IdentityOnly=$IdentityOnly\"\r\n"
            f"Set-Content -Path '{args_win}' -Value $line\r\n"
            f"$code = [int](Get-Content '{exit_win}')\r\n"
            "if ($code -ne 0) { Write-Host \"CDP_IDENTITY_REJECT reason=no-listener\" }\r\n"
            "else { Write-Host \"CDP_IDENTITY_OK\" }\r\n"
            "exit $code\r\n"
        ).encode("utf-8")
    )
    original_root = cc.ROOT
    original_full = os.environ.get("PC_ENSURE_CDP_FULL")
    cc.ROOT = stub_root
    os.environ.pop("PC_ENSURE_CDP_FULL", None)
    try:
        check("the identity budget is 30 seconds", cc.PC_ENSURE_CDP_TIMEOUT_SECONDS, 30)
        started = datetime.now(timezone.utc)
        unreachable = cc.ensure_cdp(9333)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        check("an unreachable session fails the lane", unreachable.get("ok"), False)
        check(
            "an unreachable session is cdp_unreachable",
            unreachable.get("errorClass"),
            "cdp_unreachable",
        )
        check("an unreachable session is retryable", unreachable.get("retryable"), True)
        check("the chain does not wait 90s for Chrome", elapsed < 60, True)
        check(
            "the chain asks for identity only",
            "IdentityOnly=True" in _stub_args(args_file, unreachable),
            True,
        )

        exit_flag.write_text("0", encoding="utf-8")
        reachable = cc.ensure_cdp(9333)
        check("a healthy session passes", reachable.get("ok"), True)
        check("a healthy session has no error class", reachable.get("errorClass"), None)
        check("a healthy session is still identity-only", reachable.get("identityOnly"), True)

        os.environ["PC_ENSURE_CDP_FULL"] = "1"
        full = cc.ensure_cdp(9333)
        check("the hand-run escape hatch restores the full path", full.get("identityOnly"), False)
        check(
            "the escape hatch stops passing -IdentityOnly",
            "IdentityOnly=False" in _stub_args(args_file, full),
            True,
        )
    finally:
        cc.ROOT = original_root
        if original_full is None:
            os.environ.pop("PC_ENSURE_CDP_FULL", None)
        else:
            os.environ["PC_ENSURE_CDP_FULL"] = original_full


# ---------------------------------------------------------------------------
# 5. F-COLLECT-DISPATCH
# ---------------------------------------------------------------------------
def test_source_registry_dispatch() -> None:
    for source_key in contract.SOURCE_ADAPTERS:
        check(
            f"registered source {source_key} resolves to a runner",
            cc._adapter_runner(source_key) is not None,
            True,
        )
    check(
        "the lane table is derived from the registry",
        contract.ADAPTER_LANE,
        {key: spec["lane"] for key, spec in contract.SOURCE_ADAPTERS.items()},
    )

    calls: list[tuple] = []

    def fake_runner(items, *, mode, dry_run, limit=None, **_ignored):
        calls.append((tuple(int(i["variantId"]) for i in items), mode, dry_run))
        return {"adapter": "fake_source", "ok": True, "processed": len(items)}

    cc.run_fake_source = fake_runner  # type: ignore[attr-defined]
    contract.SOURCE_ADAPTERS["fake_source"] = {
        "lane": "http",
        "runner": "run_fake_source",
        "kind": "test",
    }
    contract.SOURCE_ADAPTERS["ghost_source"] = {
        "lane": "http",
        "runner": "run_ghost_source_that_does_not_exist",
        "kind": "test",
    }
    try:
        dispatched = cc.dispatch_adapter(
            "fake_source",
            [{"variantId": 7}],
            mode="incr",
            dry_run=False,
            limit=None,
            delay=0.0,
            workers=1,
            work_scope=None,
            shared_harvest=None,
            refresh={},
        )
        check("one dict entry is enough to dispatch a source", dispatched.get("ok"), True)
        check("the registered runner actually ran", calls, [((7,), "incr", False)])

        unknown = cc.dispatch_adapter("never_registered", [], mode="incr", dry_run=False)
        check("an unregistered source fails closed", unknown.get("ok"), False)
        check(
            "an unregistered source is named source_not_registered",
            unknown.get("errorClass"),
            "source_not_registered",
        )
        ghost = cc.dispatch_adapter("ghost_source", [], mode="incr", dry_run=False)
        check(
            "a registry entry with no runner also fails closed",
            ghost.get("errorClass"),
            "source_not_registered",
        )
    finally:
        contract.SOURCE_ADAPTERS.pop("fake_source", None)
        contract.SOURCE_ADAPTERS.pop("ghost_source", None)
        delattr(cc, "run_fake_source")

    source = (ROOT / "pipelines" / "collect_control.py").read_text(encoding="utf-8")
    for gone in (
        'if "gemrate_pop" in requested:',
        'if "snk_trades" in requested:',
        'if "snk_price" in requested:',
        'if "snk_en_image" in requested:',
        'if "pc_ebay_sales" in requested:',
        'if "en_price_ref" in requested:',
    ):
        check(f"hard-coded dispatch site is gone: {gone}", gone in source, False)
    check("both lanes go through the registry", source.count("dispatch_adapter(") >= 3, True)


def main() -> int:
    test_tmp_root = ROOT / "data" / "runtime" / "test-tmp"
    test_tmp_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="pc_lane_durability_", dir=str(test_tmp_root)))
    try:
        test_partial_report(tmp)
        test_sigterm_writes_partial(tmp)
        test_cf_storm_breaker(tmp)
        test_pc_error_classes()
        test_refresh_maps_storm_and_rejects_partial(tmp)
        test_refresh_bind_only(tmp)
        test_child_log_and_rc(tmp)
        test_ensure_cdp_identity_only(tmp)
        test_source_registry_dispatch()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for line in FAILED:
        print(line)
    print(f"{CHECKS - len(FAILED)}/{CHECKS} checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
