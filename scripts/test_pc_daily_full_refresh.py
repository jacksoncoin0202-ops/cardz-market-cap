#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日更鏈嘅 PriceCharting source task 每轉都要用 9333 攞返新頁（daily_full）。

點解要有呢個 test：2026-08-24 receipt（live tree
`data/runtime/daily-chain-v2/2026-08-24/receipts/150cb2464a92b220-attempt-1.collect.json`）
`localStockReplay.processed=1171` / `networkRefresh.processed=0` / `inserted=0`——
本地 HTML 未夠 36h，`partition_local_pc_stock_pages` 全部 replay，出街價落後
PriceCharting 11–35 個鐘（v1720 Mew ex 232/091：PC 08-22 有 $3100，出街 $2850@08-20）。
36h 係 acceptance／checkpoint SLA，唔郁；日更 source task 改用 `refresh_policy="daily_full"`。

守住四件事：
1. 本地 HTML 只有 1 個鐘大，daily_full 一樣要落網路（reason `daily_full_refresh_due`）；
   operator 嘅 sla_replay 語義原封不動。
2. tick 中途被斬之後 resume：呢個 cycle 已經攞過嘅頁唔准再攞（mtime ≥ cycle 起步時間）。
3. 攞唔到（Cloudflare／CDP 死）先至逐張 fallback replay，reason 係
   `fresh_fetch_failed_replay_local`，分開計，唔准靜靜地當成功。
4. fallback 唔准過 SLA：有一張冇合格本地證據就唔准 fallback，成個 refresh 照舊 fail。
5. fallback 只准補「child 真係開過而且失敗」嗰啲 variant。child 未行到嗰啲頁根本冇
   fetch failed 過，用尋日 HTML 冚住佢就係 R5 要殺嗰個 bug 本身（review 2026-08-24
   blocking #2）：淨低嗰啲要留喺 network lane，成個 sweep 照舊 fail，下一個 tick 再行。
6. 第一次被 9333 拒絕唔准即刻出舊 HTML：要留返一次重試機會，第二次先至 fallback。
7. tick 斬到一半（childInterrupted）唔准 fallback：斬咗就要保住個 failure 去 resume。
8. 真係出咗舊 HTML 就要嘈：日更鏈嘅 source result 要標 degraded，唔准靜靜地當 completed。

Run: python -X utf8 scripts/test_pc_daily_full_refresh.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import collect_control as cc  # noqa: E402
import pc_psa10_price_derivation as deriv  # noqa: E402

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


def partition(items, **kwargs):
    """Call the partition; a missing knob is a failed check, not a traceback."""

    try:
        return cc.partition_local_pc_stock_pages(items, **kwargs)
    except TypeError as error:
        FAILED.append(f"FAIL partition_local_pc_stock_pages({kwargs!r}): {error}")
        return [], [], {}


def call_fallback(fn, items, refresh, **kwargs):
    """Same idea for the fallback: a missing knob is a failed check."""

    try:
        return fn(items, refresh, **kwargs)
    except TypeError as error:
        FAILED.append(f"FAIL pc_fresh_fetch_fallback({sorted(kwargs)}): {error}")
        # 舊 signature 一樣要行落去，否則下面每個 check 都會因為 None 而白白 pass。
        legacy = {k: v for k, v in kwargs.items() if k in ("mode", "run_started_at")}
        try:
            return fn(items, refresh, **legacy)
        except TypeError:
            return None


def main() -> int:
    test_tmp_root = ROOT / "data" / "runtime" / "test-tmp"
    test_tmp_root.mkdir(parents=True, exist_ok=True)
    tmpdir = Path(tempfile.mkdtemp(prefix="pc_daily_full_", dir=str(test_tmp_root)))
    now = time.time()
    run_started_at = datetime.now(timezone.utc) - timedelta(hours=1)
    ages = {1: now - (0.5 * 3600), 2: now - (5 * 3600), 3: now - (40 * 3600)}
    rows: dict[int, dict[str, object]] = {}
    for variant_id, stamp in ages.items():
        html_path = tmpdir / f"{variant_id}.html"
        html_path.write_text("x" * 6000, encoding="utf-8")
        os.utime(html_path, (stamp, stamp))
        rows[variant_id] = {
            "variant_id": variant_id,
            "pc_product_id": f"710881{variant_id}",
            "pc_url": f"https://www.pricecharting.com/game/pokemon-promo/test-{variant_id}",
            "html_path": str(html_path.relative_to(ROOT)).replace("\\", "/"),
        }
    items = {
        variant_id: {
            "variantId": variant_id,
            "externalId": f"710881{variant_id}",
            "modeNeeded": "incr",
        }
        for variant_id in ages
    }

    def fake_map(selected, *, mode, label):
        return tmpdir / "map.jsonl", [
            rows[int(item["variantId"])] for item in selected
        ]

    original_map = cc._pc_subset_map
    original_validate = deriv.validate_pc_psa10
    original_stamp = getattr(cc, "PC_DAILY_FULL_CYCLE_STAMP", None)
    cc._pc_subset_map = fake_map  # type: ignore[assignment]
    deriv.validate_pc_psa10 = lambda _row: (  # type: ignore[assignment]
        {"field": "VGPC.chart_data.manualonly.last", "observed_date": "2026-08-14"},
        "accepted",
    )
    try:
        # --- 1. operator 嘅 SLA replay 語義冇變 --------------------------------
        replayed, network, report = partition(
            list(items.values()), mode="incr", dry_run=False
        )
        check("sla_replay：36h 內照 replay", [i["variantId"] for i in replayed], [1, 2])
        check("sla_replay：淨係過 SLA 嗰張落網路", [i["variantId"] for i in network], [3])
        check(
            "sla_replay 唔會標 daily_full",
            (report.get("networkReasons") or {}).get("3"),
            "local_exact_html_exceeds_36h_sla",
        )

        # --- 2. daily_full：未夠 1 個鐘一樣要攞新頁 ---------------------------
        replayed, network, report = partition(
            list(items.values()),
            mode="incr",
            dry_run=False,
            refresh_policy="daily_full",
            run_started_at=run_started_at,
        )
        check(
            "daily_full：cycle 之前嗰啲全部落網路",
            [i["variantId"] for i in network],
            [2, 3],
        )
        check(
            "daily_full：5h 大都唔准 replay",
            (report.get("networkReasons") or {}).get("2"),
            "daily_full_refresh_due",
        )
        check(
            "daily_full：過 SLA 嘅 reason 唔變",
            (report.get("networkReasons") or {}).get("3"),
            "local_exact_html_exceeds_36h_sla",
        )
        check("receipt 標明 policy", report.get("refreshPolicy"), "daily_full")

        # --- 3. resume：呢個 cycle 攞過嘅頁唔再攞 ------------------------------
        check("resume：已攞嘅唔再落網路", [i["variantId"] for i in replayed], [1])
        check(
            "resume 有 reason",
            (report.get("replayReasons") or {}).get("1"),
            "fresh_page_captured_this_run",
        )
        check("localStockReplay 只計 resume 嗰張", report.get("processed"), 1)
        check("resume 一樣要有 payload hash", bool((report.get("payloadShaByVariant") or {}).get("1")), True)

        # --- 4. daily_full 冇 cycle 起步時間就要嘈，唔准靜靜地 replay ----------
        raised = ""
        try:
            cc.partition_local_pc_stock_pages(
                list(items.values()),
                mode="incr",
                dry_run=False,
                refresh_policy="daily_full",
            )
        except TypeError as error:
            raised = f"TypeError:{error}"
        except RuntimeError as error:
            raised = "RuntimeError"
            check("冇 run_started_at 要講明", "run_started_at" in str(error), True)
        check("daily_full 冇 cycle 錨點 = RuntimeError", raised, "RuntimeError")

        unknown = ""
        try:
            cc.partition_local_pc_stock_pages(
                list(items.values()),
                mode="incr",
                dry_run=False,
                refresh_policy="every_hour",
                run_started_at=run_started_at,
            )
        except TypeError as error:
            unknown = f"TypeError:{error}"
        except RuntimeError:
            unknown = "RuntimeError"
        check("唔識嘅 policy = RuntimeError", unknown, "RuntimeError")

        # --- 5. cycle 錨點跨 process 保持唔變 ----------------------------------
        stamp_fn = getattr(cc, "pc_daily_full_cycle_started_at", None)
        check("有 cycle 錨點 helper", callable(stamp_fn), True)
        if callable(stamp_fn):
            cc.PC_DAILY_FULL_CYCLE_STAMP = tmpdir / "cycle.json"  # type: ignore[attr-defined]
            first = stamp_fn("cardz-v2:2026-08-25")
            second = stamp_fn("cardz-v2:2026-08-25")
            check("同一個 run id：錨點唔郁（tick 斬完 resume）", second, first)
            third = stamp_fn("cardz-v2:2026-08-26")
            check("換咗業務日：錨點推前", third > first, True)

        # --- 6. 攞唔到頁先 fallback，而且要標返 reason ------------------------
        fallback_fn = getattr(cc, "pc_fresh_fetch_fallback", None)
        check("有 fallback helper", callable(fallback_fn), True)
        if callable(fallback_fn):
            cc.PC_DAILY_FULL_CYCLE_STAMP = tmpdir / "fallback-cycle.json"  # type: ignore[attr-defined]
            failed_refresh = {
                "adapter": "pc_cdp_fresh_pages",
                "mode": "incr",
                "processed": 2,
                "ok": False,
                "error": "pc_cloudflare_storm",
                "errorClass": "pc_cloudflare_storm",
                "retryable": True,
                "payloadShaByVariant": {},
                # child 真係開過 variant 2 而且失敗（cf）；variant 1 佢攞到手。
                "attemptedFailedVariantIds": [2],
            }
            # 第一次被拒：唔准即刻出舊 HTML，要留返一次重試（review major #1）。
            first_refusal = call_fallback(
                fallback_fn,
                [items[1], items[2]],
                failed_refresh,
                mode="incr",
                run_started_at=run_started_at,
                cycle_key="cardz-v2:fallback",
            )
            check("第一次被拒：唔准 fallback，要重試多一轉", first_refusal, None)
            outcome = call_fallback(
                fallback_fn,
                [items[1], items[2]],
                failed_refresh,
                mode="incr",
                run_started_at=run_started_at,
                cycle_key="cardz-v2:fallback",
            )
            check("有合格本地證據就 fallback", outcome is not None, True)
            if outcome is not None:
                fb_items, fb_report, degraded = outcome
                check("fallback 補晒兩張", [i["variantId"] for i in fb_items], [1, 2])
                check(
                    "fallback reason 分得開",
                    (fb_report.get("replayReasons") or {}).get("2"),
                    "fresh_fetch_failed_replay_local",
                )
                check(
                    "呢個 cycle 攞到嗰張唔算 fallback",
                    (fb_report.get("replayReasons") or {}).get("1"),
                    "fresh_page_captured_this_run",
                )
                check("networkRefresh 只計真係攞到嘅", degraded.get("processed"), 1)
                check("受影響嘅 variant 寫入 receipt", degraded.get("fallbackReplayVariantIds"), [2])
                check("失敗要留底，唔准靜靜地", degraded.get("freshFetchFailed"), True)
                check("原本個 error 要記住", degraded.get("freshFetchError"), "pc_cloudflare_storm")
                check("fallback 之後條 lane 先叫 ok", degraded.get("ok"), True)
                check("矛盾嘅 error key 要清走", "error" in degraded, False)

            # --- 7. 過 SLA 冇得 fallback：refresh 照舊 fail -------------------
            refused = call_fallback(
                fallback_fn,
                [items[2], items[3]],
                {**failed_refresh, "attemptedFailedVariantIds": [2, 3]},
                mode="incr",
                run_started_at=run_started_at,
                cycle_key="cardz-v2:fallback",
            )
            check("有一張冇合格證據就唔准 fallback", refused, None)

            # --- 7b. child 未開過嗰啲頁唔准用舊 HTML 冚（blocking #2）---------
            never_attempted = call_fallback(
                fallback_fn,
                [items[1], items[2]],
                {**failed_refresh, "attemptedFailedVariantIds": []},
                mode="incr",
                run_started_at=run_started_at,
                cycle_key="cardz-v2:fallback",
            )
            check("child 未行到嗰張：唔准 fallback，成個 sweep 照舊 fail", never_attempted, None)
            partial_sweep = call_fallback(
                fallback_fn,
                [items[1], items[2]],
                {**failed_refresh, "attemptedFailedVariantIds": [9999]},
                mode="incr",
                run_started_at=run_started_at,
                cycle_key="cardz-v2:fallback",
            )
            check("attempted 名單唔覆蓋就唔准 fallback", partial_sweep, None)

            # --- 7c. tick 斬到一半：保住 failure 去 resume（major #3）---------
            interrupted_outcome = call_fallback(
                fallback_fn,
                [items[1], items[2]],
                {**failed_refresh, "childInterrupted": True},
                mode="incr",
                run_started_at=run_started_at,
                cycle_key="cardz-v2:fallback",
            )
            check("tick 斬咗個 child：唔准 fallback", interrupted_outcome, None)

            # --- 8. receipt 兩個數要對得返 -----------------------------------
            merge_fn = getattr(cc, "_merge_pc_replay_reports", None)
            check("有 replay report 合併", callable(merge_fn), True)
            if callable(merge_fn) and outcome is not None:
                fb_items, fb_report, degraded = outcome
                _, _, cycle_report = partition(
                    [items[1]],
                    mode="incr",
                    dry_run=False,
                    refresh_policy="daily_full",
                    run_started_at=run_started_at,
                )
                merged = merge_fn(cycle_report, fb_report)
                check("localStockReplay 淨係計 replay 嗰啲", merged.get("processed"), 3)
                check("fallback 分開計", merged.get("fallbackReplays"), 1)
                check(
                    "每張都留得低點解 replay",
                    sorted((merged.get("replayReasons") or {}).items()),
                    [
                        ("1", "fresh_page_captured_this_run"),
                        ("2", "fresh_fetch_failed_replay_local"),
                    ],
                )
                check(
                    "兩個 adapter 食同一次 fetch 攞返嘅 hash",
                    sorted((merged.get("payloadShaByVariant") or {})),
                    ["1", "2"],
                )

        # --- 10. refresh_pc_pages 要交代 child 真係開過邊啲 variant ---------
        import contextlib  # noqa: PLC0415
        import json as _json  # noqa: PLC0415

        refresh_fn = getattr(cc, "refresh_pc_pages", None)
        check("有 refresh_pc_pages", callable(refresh_fn), True)
        if callable(refresh_fn):
            original_out_dir = cc.OUT_DIR
            original_report = cc.PC_REFRESH_REPORT
            original_windows_py = cc.WINDOWS_PY
            original_run_child = cc._run_pc_child
            original_defer = cc._deferred_termination
            child_report = tmpdir / "pc_cdp_refresh_report.json"
            # refresh_pc_pages 而家會睇 9333 child 嘅 progress stamp 決定使唔使
            # 讓路（review 2026-08-24 major #1）。stamp 住喺 repo 真實 runtime
            # 目錄，唔搬走嘅話，其他 test 留低嘅 stamp 就會決定呢個 test 嘅結果。
            import pc_cdp_sold_refresh_win as pc_child_mod  # noqa: PLC0415

            original_stamp_dir = pc_child_mod.PROGRESS_STAMP_DIR
            original_probe_fallback = getattr(cc, "PC_PROGRESS_STAMP_DIR_FALLBACK", None)
            pc_child_mod.PROGRESS_STAMP_DIR = tmpdir
            # 個 probe import 唔到 child module 就用自己嘅 fallback 目錄
            # （review 2026-08-24 minor #4），而嗰個 fallback 就係真實 runtime
            # 目錄，所以一齊搬走。
            cc.PC_PROGRESS_STAMP_DIR_FALLBACK = tmpdir  # type: ignore[attr-defined]
            cc.OUT_DIR = tmpdir  # type: ignore[attr-defined]
            cc.PC_REFRESH_REPORT = child_report  # type: ignore[attr-defined]
            cc.WINDOWS_PY = Path(__file__).resolve()  # type: ignore[attr-defined]

            def make_child(*, partial: bool, exit_code: int):
                def _fake_child(cmd, *, timeout, dry_run, use_vbs=None):
                    payload = {
                        "asOf": datetime.now(timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "batch": 2,
                        "ok": 1,
                        "fail": 1,
                        "cf": 1,
                        "results": [
                            {"variant_id": 1, "status": "ok"},
                            {"variant_id": 2, "status": "cf_or_fail"},
                        ],
                    }
                    if partial:
                        payload["partial"] = True
                        payload["stop_reason"] = "watchdog_stall"
                    child_report.write_text(
                        _json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                    )
                    return {"exit": exit_code, "childLog": "", "childLogTail": ""}

                return _fake_child

            def fake_defer(signalled: bool):
                @contextlib.contextmanager
                def _cm():
                    yield {"signalled": signalled}

                return _cm

            try:
                cc._run_pc_child = make_child(partial=False, exit_code=3)  # type: ignore[assignment]
                cc._deferred_termination = fake_defer(False)  # type: ignore[assignment]
                hard_exit = refresh_fn(
                    [items[1], items[2]],
                    mode="incr",
                    dry_run=False,
                    resume_report=tmpdir / "resume.json",
                    sleep_seconds=None,
                    tabs=None,
                    cdp_already_ensured=True,
                )
                check("child 死咗：sweep 照舊 fail", hard_exit.get("ok"), False)
                check(
                    "child 死咗都要講返佢開過而失敗嗰啲",
                    hard_exit.get("attemptedFailedVariantIds"),
                    [2],
                )
                check("冇被斬就唔標 childInterrupted", "childInterrupted" in hard_exit, False)

                cc._run_pc_child = make_child(partial=True, exit_code=0)  # type: ignore[assignment]
                partial_run = refresh_fn(
                    [items[1], items[2]],
                    mode="incr",
                    dry_run=False,
                    resume_report=tmpdir / "resume.json",
                    sleep_seconds=None,
                    tabs=None,
                    cdp_already_ensured=True,
                )
                check("partial report：sweep 照舊 fail", partial_run.get("ok"), False)
                check(
                    "partial report 一樣要交代 attempted",
                    partial_run.get("attemptedFailedVariantIds"),
                    [2],
                )

                # --- 11. tick 斬到一半：receipt 要認 ------------------------
                cc._run_pc_child = make_child(partial=False, exit_code=3)  # type: ignore[assignment]
                cc._deferred_termination = fake_defer(True)  # type: ignore[assignment]
                interrupted_run = refresh_fn(
                    [items[1], items[2]],
                    mode="incr",
                    dry_run=False,
                    resume_report=tmpdir / "resume.json",
                    sleep_seconds=None,
                    tabs=None,
                    cdp_already_ensured=True,
                )
                check("被斬要寫入 receipt", interrupted_run.get("childInterrupted"), True)
                check("被斬一樣係 fail", interrupted_run.get("ok"), False)

                # --- 12. 9333 得一個 child：orphan 未死唔准開第二個 -----------
                # review 2026-08-24 major #1：R5 之後條 sweep 50-65 分鐘，即係
                # 日日都會俾 tick 斬。斬嘅係 WSL 側個 collect process，Windows
                # 側個 hidden child 唔會死，繼續攞頁；下一個 tick 再開多一個
                # child = 同時兩條 sweep 打 PriceCharting，request rate 直接雙倍。
                launches: list[list[str]] = []
                base_child = make_child(partial=False, exit_code=0)

                def counting_child(cmd, *, timeout, dry_run, use_vbs=None):
                    launches.append(list(cmd))
                    return base_child(cmd, timeout=timeout, dry_run=dry_run)

                cc._run_pc_child = counting_child  # type: ignore[assignment]
                cc._deferred_termination = fake_defer(False)  # type: ignore[assignment]
                live_stamp = tmpdir / "pc_cdp_progress.424242.stamp"
                try:
                    live_stamp.write_text("beat", encoding="utf-8")
                    blocked = refresh_fn(
                        [items[1], items[2]],
                        mode="incr",
                        dry_run=False,
                        resume_report=tmpdir / "resume.json",
                        sleep_seconds=None,
                        tabs=None,
                        cdp_already_ensured=True,
                    )
                    check("orphan 未死：唔准開第二個 child", len(launches), 0)
                    check("orphan 未死：sweep 照舊 fail", blocked.get("ok"), False)
                    check(
                        "orphan 未死：有名有姓嘅 errorClass",
                        blocked.get("errorClass"),
                        "pc_child_already_running",
                    )
                    check("orphan 未死：下個 tick 可以再試", blocked.get("retryable"), True)
                    check(
                        "orphan 未死：一頁都未開過",
                        blocked.get("attemptedFailedVariantIds"),
                        [],
                    )
                    if callable(fallback_fn):
                        cc.PC_DAILY_FULL_CYCLE_STAMP = tmpdir / "orphan-cycle.json"  # type: ignore[attr-defined]
                        covered = call_fallback(
                            fallback_fn,
                            [items[1], items[2]],
                            blocked,
                            mode="incr",
                            run_started_at=run_started_at,
                            cycle_key="cardz-v2:orphan",
                        )
                        check("orphan 未死：唔准用舊 HTML 冚住佢", covered, None)
                    # stamp 舊過 hard stall killer 個窗 = 嗰個 child 一定已經俾
                    # 自己個 killer 殺咗，唔可以永遠封住條 lane。
                    dead = time.time() - (float(pc_child_mod.HARD_STALL_KILLER_SECONDS) + 120.0)
                    os.utime(live_stamp, (dead, dead))
                    resumed = refresh_fn(
                        [items[1], items[2]],
                        mode="incr",
                        dry_run=False,
                        resume_report=tmpdir / "resume.json",
                        sleep_seconds=None,
                        tabs=None,
                        cdp_already_ensured=True,
                    )
                    check("stamp 過咗期：照開新 child", len(launches), 1)
                    check(
                        "stamp 過咗期：唔會再賴 already-running",
                        resumed.get("errorClass"),
                        None,
                    )
                finally:
                    live_stamp.unlink(missing_ok=True)
            finally:
                pc_child_mod.PROGRESS_STAMP_DIR = original_stamp_dir
                cc.PC_PROGRESS_STAMP_DIR_FALLBACK = original_probe_fallback  # type: ignore[attr-defined]
                cc.OUT_DIR = original_out_dir  # type: ignore[attr-defined]
                cc.PC_REFRESH_REPORT = original_report  # type: ignore[attr-defined]
                cc.WINDOWS_PY = original_windows_py  # type: ignore[attr-defined]
                cc._run_pc_child = original_run_child  # type: ignore[assignment]
                cc._deferred_termination = original_defer  # type: ignore[assignment]

        # --- 9. 日更鏈真係叫 daily_full，錨點係個 run id --------------------
        captured: dict[str, object] = {}

        collect_report_extra: dict[str, object] = {}

        def fake_collect_command(**kwargs):
            captured.clear()
            captured.update(kwargs)
            return {
                "ok": True,
                "processed": 1,
                "inserted": 1,
                "checkpointed": 1,
                "failed": 0,
                "quarantined": 0,
                "asOf": "2026-08-25T00:00:00Z",
                "results": [],
                **collect_report_extra,
            }

        import types

        fake_collect = types.ModuleType("collect_control")
        fake_collect.cmd_incr = fake_collect_command
        fake_collect.cmd_stock = fake_collect_command
        fake_collect.REGISTRY_PATH = Path("fixture-registry.jsonl")
        fake_collect._jsonl_rows = lambda path: []
        previous_collect = sys.modules.get("collect_control")
        sys.modules["collect_control"] = fake_collect
        try:
            import daily_chain_v2_worker as v2worker  # noqa: PLC0415

            receipt_path = tmpdir / "receipt.json"
            v2worker.run_collect(
                {"source_code": "pricecharting", "run_id": "cardz-v2:2026-08-25"},
                {
                    "shard": "all",
                    "worker": {"adapters": ["pc_ebay_sales"], "variantIds": [7]},
                },
                receipt_path,
            )
            check("鏈嘅 source task 行 daily_full", captured.get("refresh_policy"), "daily_full")
            check(
                "resume 錨點 = business date 嗰個 run id",
                captured.get("refresh_cycle_key"),
                "cardz-v2:2026-08-25",
            )
            check("operator force_network 冇被順手開著", captured.get("force_network"), False)

            # --- 12. 出咗舊 HTML 就唔准當 completed（major #4）--------------
            collect_report_extra["pcRefresh"] = {
                "networkRefresh": {"ok": True, "freshFetchFailed": True},
                "localStockReplay": {"fallbackReplays": 2},
            }
            degraded_result = v2worker.run_collect(
                {"source_code": "pricecharting", "run_id": "cardz-v2:2026-08-25"},
                {
                    "shard": "all",
                    "worker": {"adapters": ["pc_ebay_sales"], "variantIds": [7]},
                },
                receipt_path,
            )
            check("fallback 出街 = degraded", degraded_result.get("status"), "degraded")
            check(
                "receipt 要寫低幾多張出咗舊 HTML",
                (degraded_result.get("detail") or {}).get("pcFallbackReplays"),
                2,
            )
            collect_report_extra.clear()
            clean_result = v2worker.run_collect(
                {"source_code": "pricecharting", "run_id": "cardz-v2:2026-08-25"},
                {
                    "shard": "all",
                    "worker": {"adapters": ["pc_ebay_sales"], "variantIds": [7]},
                },
                receipt_path,
            )
            check("全部攞到新頁就照舊 completed", clean_result.get("status"), "completed")
        finally:
            if previous_collect is None:
                sys.modules.pop("collect_control", None)
            else:
                sys.modules["collect_control"] = previous_collect

        # --- 13. cycle stamp 係 5-6 個 collect worker 共用嘅檔（review major #2）
        # run_collect 對每一個 source（gemrate 4 shard + snkrdunk + pricecharting）
        # 都送 refresh_policy="daily_full"，而佢哋喺同一個 ThreadPoolExecutor 入面
        # 一齊開工。所以（甲）冇 PC 卡嗰啲 worker 根本唔應該掂呢個檔，（乙）寫落去
        # 一定要行 _write_json_atomic：撕爛咗個 JSON = 讀返 None = 當成新 cycle，
        # 錨點推前，已經攞咗嘅頁全部要重新再 Cloudflare 一次。
        anchor_fn = getattr(cc, "pc_daily_full_run_anchor", None)
        check("有 run anchor helper", callable(anchor_fn), True)
        if callable(anchor_fn):
            anchor_stamp = tmpdir / "anchor-cycle.json"
            cc.PC_DAILY_FULL_CYCLE_STAMP = anchor_stamp  # type: ignore[attr-defined]
            check(
                "冇 PC 卡嘅 worker 唔准落錨",
                anchor_fn("daily_full", dry_run=False, pc_items=[], cycle_key="cardz-v2:anchor"),
                None,
            )
            check("冇 PC 卡：個檔根本唔應該出現", anchor_stamp.exists(), False)
            check(
                "dry-run 唔落錨",
                anchor_fn("daily_full", dry_run=True, pc_items=[items[1]], cycle_key="cardz-v2:anchor"),
                None,
            )
            check(
                "operator sla_replay 唔落錨",
                anchor_fn("sla_replay", dry_run=False, pc_items=[items[1]], cycle_key="cardz-v2:anchor"),
                None,
            )
            check("到呢刻個檔仍然唔應該出現", anchor_stamp.exists(), False)
            anchored = anchor_fn(
                "daily_full", dry_run=False, pc_items=[items[1]], cycle_key="cardz-v2:anchor"
            )
            check("真係有 PC 卡先落錨", anchor_stamp.is_file(), True)
            check("落到錨要有值", anchored is not None, True)
            check(
                "同一個 cycle 再問：錨點唔郁",
                anchor_fn("daily_full", dry_run=False, pc_items=[items[1]], cycle_key="cardz-v2:anchor"),
                anchored,
            )

        atomic_writes: list[str] = []
        original_atomic = cc._write_json_atomic
        atomic_stamp = tmpdir / "atomic-cycle.json"

        def spy_atomic(path, payload):
            atomic_writes.append(str(path))
            return original_atomic(path, payload)

        cc._write_json_atomic = spy_atomic  # type: ignore[assignment]
        try:
            cc.PC_DAILY_FULL_CYCLE_STAMP = atomic_stamp  # type: ignore[attr-defined]
            atomic_started = cc.pc_daily_full_cycle_started_at("cardz-v2:atomic")
            cc.pc_daily_full_record_refusal("cardz-v2:atomic", started_at=atomic_started)
        finally:
            cc._write_json_atomic = original_atomic  # type: ignore[assignment]
        check(
            "兩個 helper 都行 _write_json_atomic",
            atomic_writes.count(str(atomic_stamp)),
            2,
        )
        check("refusal 數得返",
              (lambda: __import__("json").loads(atomic_stamp.read_text(encoding="utf-8-sig")).get("refusedSweeps"))(),
              1)

        # --- 14. attempt 預算：pricecharting 同其他 source 一模一樣 7 ---------
        # 之前呢度加咗 headroom（7 + interruption budget = 13）落同一個 `attempts`
        # counter 上面，即係將「真失敗就 park」個閘由 7 放鬆到 13 —— 放鬆閘係唔准
        # 嘅。被 tick 斬嗰啲 attempt 點計係 R2 branch 嘅嘢（interruption 自己還返個
        # attempt）；呢邊只准得一個數：SOURCE_MAX_ATTEMPTS。
        import daily_chain_v2 as v2chain  # noqa: PLC0415
        import daily_chain_v2_adapters as v2adapters  # noqa: PLC0415
        import daily_chain_v2_contract as v2contract  # noqa: PLC0415
        import daily_chain_v2_journal as v2journal  # noqa: PLC0415
        import daily_chain_v2_worker as v2worker  # noqa: PLC0415

        specs = {
            adapter.spec.source_code: adapter.spec
            for adapter in v2adapters.build_default_registry().enabled()
        }
        check("真失敗嘅預算冇郁", getattr(v2chain, "SOURCE_MAX_ATTEMPTS", None), 7)
        check(
            "冇 per-source attempt headroom helper",
            getattr(v2chain, "source_task_max_attempts", None),
            None,
        )
        check(
            "SourceSpec 冇 resumable_sweep 呢個 headroom 掣",
            hasattr(specs["pricecharting"], "resumable_sweep"),
            False,
        )
        plan_src = (ROOT / "pipelines" / "daily_chain_v2.py").read_text(encoding="utf-8")
        check(
            "plan() 每個 source 都派同一個常數",
            "max_attempts=SOURCE_MAX_ATTEMPTS" in plan_src,
            True,
        )

        def planned_budget(spec):
            """生產真正派落 journal 嗰個數，行返 plan() 行嗰條路。"""

            helper = getattr(v2chain, "source_task_max_attempts", None)
            if callable(helper):
                return int(helper(spec))
            return int(v2chain.SOURCE_MAX_ATTEMPTS)

        check("pricecharting max_attempts", planned_budget(specs["pricecharting"]), 7)
        check("snkrdunk max_attempts", planned_budget(specs["snkrdunk"]), 7)
        check("gemrate max_attempts", planned_budget(specs["gemrate"]), 7)
        check(
            "interruption 上限冇拆走（照樣會 PARK）",
            v2journal.DEFAULT_MAX_INTERRUPTIONS >= 1,
            True,
        )

        # --- 15. 7 次真失敗照 TERMINAL；contention 唔准食走個額度 -------------
        # `pc_child_already_running` 係「呢條 lane 自己隻 9333 child 仲喺度跑」，
        # 唔係攞唔到頁。當成 SOURCE_FAILED 就會行 60..1800 條梯，第 7 次同類失敗
        # TERMINAL —— 一隻跑緊 40-90 分鐘嘅 orphan 足夠燒晒成條 lane，然後當日
        # 出街完全冇新 PC 數。Contention 唔准燒條梯，亦唔准燒 attempt 預算。
        journal = v2journal.Journal(tmpdir / "attempt-budget.sqlite3")
        journal.initialise()
        day_text = "2026-08-25"
        run_row = journal.ensure_run(
            business_date=day_text,
            source_cutoff_at="2026-08-25T01:15:00Z",
            sla_at="2026-08-25T05:00:00Z",
            final_at="2026-08-25T08:00:00Z",
        )
        run_id = str(run_row["run_id"])

        def drive(task_key, error_text, *, rounds, start_at):
            """Claim → fail 一個 task 幾轉，返回每轉個 status 同最後個時鐘。"""

            clock = start_at
            trail: list[str] = []
            for _ in range(rounds):
                claimed = journal.claim_ready(run_id, limit=8, now=clock)
                row = next(
                    (r for r in claimed if str(r["task_key"]) == task_key), None
                )
                if row is None:
                    trail.append("UNCLAIMABLE")
                    break
                status = journal.finish_failure(
                    task_key,
                    str(row["lease_token"]),
                    decision=v2contract.classify_error(error_text),
                    error_text=error_text,
                    now=clock,
                )
                trail.append(status)
                if status == "TERMINAL":
                    break
                next_retry = (journal.task(task_key) or {}).get("next_retry_at")
                if not next_retry:
                    break
                clock = datetime.fromisoformat(str(next_retry).replace("Z", "+00:00"))
            return trail, clock

        budget = planned_budget(specs["pricecharting"])
        clock0 = datetime(2026, 8, 25, 0, 0, tzinfo=timezone.utc)
        real_key = journal.add_raw_task(
            run_id=run_id,
            business_date=day_text,
            phase="source",
            source_code="pricecharting",
            capability="quote",
            required_class="quote",
            concurrency_group="cdp:9333",
            max_concurrency=1,
            max_attempts=budget,
        )
        real_text = "RuntimeError:PriceCharting sold parser blew up on page 4"
        check(
            "真失敗仲係 SOURCE_FAILED",
            v2contract.classify_error(real_text).error_code,
            "SOURCE_FAILED",
        )
        real_trail, _real_clock = drive(real_key, real_text, rounds=12, start_at=clock0)
        check("真失敗第 7 次就 TERMINAL", real_trail, ["RETRY"] * 6 + ["TERMINAL"])

        busy_text = (
            "RuntimeError:errorCode=PC_CHILD_ALREADY_RUNNING"
            " adapters=['pc_ebay_sales', 'en_price_ref']"
            " failed=['pc_ebay_sales', 'en_price_ref'] detail=[]"
        )
        busy_decision = v2contract.classify_error(busy_text)
        check("contention 有自己個 error code", busy_decision.error_code, "PC_CHILD_ALREADY_RUNNING")
        check("contention 唔係 terminal", busy_decision.terminal, False)
        check(
            "contention 條梯行唔完（第 50 次都仲有得等）",
            busy_decision.delay_for_attempt(50) is not None,
            True,
        )
        busy_key = journal.add_raw_task(
            run_id=run_id,
            business_date=day_text,
            phase="source",
            source_code="pricecharting",
            capability="price",
            required_class="quote",
            concurrency_group="cdp:9333",
            max_concurrency=1,
            max_attempts=budget,
        )
        busy_trail, busy_clock = drive(busy_key, busy_text, rounds=12, start_at=clock0)
        check("contention 一次都唔准 TERMINAL", sorted(set(busy_trail)), ["RETRY"])
        check(
            "contention 捱得住 90 分鐘",
            (busy_clock - clock0).total_seconds() >= 90 * 60,
            True,
        )
        check(
            "捱完 90 分鐘仲 claim 得返",
            [
                str(r["task_key"])
                for r in journal.claim_ready(run_id, limit=8, now=busy_clock)
            ],
            [busy_key],
        )

        # --- 16. 冇 playwright 一樣要 probe 到 9333 child（review minor #4）----
        # `pc_child_alive_stamp` 係 single-flight 個閘。佢入面 import 嘅
        # `pc_cdp_sold_refresh_win` 喺 module scope import playwright，import 一炸
        # 個閘就變成「完全冇 probe」，等於兩隻 child 同時劖 PriceCharting。
        probe_stamp = tmpdir / "pc_cdp_progress.777777.stamp"
        probe_stamp.write_text("beat", encoding="utf-8")
        original_fallback_dir = getattr(cc, "PC_PROGRESS_STAMP_DIR_FALLBACK", None)
        original_child_module = sys.modules.get("pc_cdp_sold_refresh_win")
        try:
            if original_fallback_dir is not None:
                cc.PC_PROGRESS_STAMP_DIR_FALLBACK = tmpdir  # type: ignore[attr-defined]
            sys.modules["pc_cdp_sold_refresh_win"] = None  # type: ignore[assignment]
            probed = cc.pc_child_alive_stamp()
        except Exception as error:  # noqa: BLE001
            probed = f"{type(error).__name__}:{error}"
        finally:
            if original_child_module is None:
                sys.modules.pop("pc_cdp_sold_refresh_win", None)
            else:
                sys.modules["pc_cdp_sold_refresh_win"] = original_child_module
            if original_fallback_dir is not None:
                cc.PC_PROGRESS_STAMP_DIR_FALLBACK = original_fallback_dir  # type: ignore[attr-defined]
            probe_stamp.unlink(missing_ok=True)
        check(
            "冇 playwright 都 probe 到 stamp",
            probed if not isinstance(probed, dict) else str(probed.get("stamp")),
            str(probe_stamp),
        )

        # --- 17. worker 認嘅 adapter error 就係 collector 真係出嗰個字 ---------
        unavailable = cc.run_en_price_ref(
            [items[1]], mode="incr", dry_run=True, refresh={"ok": False}
        )
        check(
            "worker 認嘅字串 = collector 真係出嗰個",
            getattr(v2worker, "PC_PAGES_UNAVAILABLE_ADAPTER_ERROR", None),
            unavailable.get("error"),
        )
        check(
            "contention class 兩邊拼法一致",
            v2contract.PC_CHILD_ALREADY_RUNNING_CLASS
            if hasattr(v2contract, "PC_CHILD_ALREADY_RUNNING_CLASS")
            else None,
            cc.PC_CHILD_ALREADY_RUNNING_CLASS,
        )
    finally:
        cc._pc_subset_map = original_map
        deriv.validate_pc_psa10 = original_validate
        if original_stamp is not None:
            cc.PC_DAILY_FULL_CYCLE_STAMP = original_stamp  # type: ignore[attr-defined]
        for child in sorted(tmpdir.glob("*")):
            child.unlink(missing_ok=True)
        tmpdir.rmdir()

    for line in FAILED:
        print(line)
    print(f"{CHECKS - len(FAILED)}/{CHECKS} checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
