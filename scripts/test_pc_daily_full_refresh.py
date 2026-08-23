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
            failed_refresh = {
                "adapter": "pc_cdp_fresh_pages",
                "mode": "incr",
                "processed": 2,
                "ok": False,
                "error": "pc_cloudflare_storm",
                "errorClass": "pc_cloudflare_storm",
                "retryable": True,
                "payloadShaByVariant": {},
            }
            outcome = fallback_fn(
                [items[1], items[2]],
                failed_refresh,
                mode="incr",
                run_started_at=run_started_at,
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
            refused = fallback_fn(
                [items[2], items[3]],
                failed_refresh,
                mode="incr",
                run_started_at=run_started_at,
            )
            check("有一張冇合格證據就唔准 fallback", refused, None)

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

        # --- 9. 日更鏈真係叫 daily_full，錨點係個 run id --------------------
        captured: dict[str, object] = {}

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
