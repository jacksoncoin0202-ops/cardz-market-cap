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
            finally:
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
