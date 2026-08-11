#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows-host CDP refresh for PriceCharting sold HTML.

Runs on Windows Python so Playwright talks to local Chrome :9333 without WSL networking weirdness.
Then optionally runs C11 sold ingest via WSL venv.

一個 Chrome、一個 profile、一個 cookie jar，但唔止一個 tab。舊版係單 tab 串行
再加 `--sleep 4.0`：實測 102 頁用咗 8 分 50 秒 = 5.2 s/頁，其中 **4.0 s 係
sleep**，真正 goto + content + parse 得 1.2 s。全量 993 張就係 86 分鐘，入面
66 分鐘係喺度瞓。嗰 4 秒冇任何量度撐住 —— runbook 只寫「politeness」。

而家：`--workers` 條 tab 共用同一個 CDP session（cookie / CF clearance 都係同
一份，所以唔會因為多開 tab 而變成「新訪客」），加一條**共用**嘅 backoff —— 任
何一條 tab 食到 429 / CF，全部 tab 一齊停低，唔係淨係嗰條慢返。HTML parse
（validate_pc_psa10）掉落 thread pool，唔准塞住 event loop 拖低所有 tab。

**分頁唔係樽頸，速率先係。** 同一批 40 張逐個設定量（2026-08-12）：

    分頁/sleep   每頁      429   推算 993 張
    4 / 1.0s     2.78s     3     46 分鐘
    3 / 1.0s     2.18s     2     36 分鐘
    2 / 1.5s     1.99s     1     33 分鐘
    2 / 3.0s     2.03s     0     34 分鐘   ← 用呢個
    6 / 8.0s     2.19s     1     36 分鐘
    4 / 4.8s     2.16s     1     36 分鐘

加分頁反而慢：食一次 429 就全部分頁一齊停 30/60/120 秒，賺嘅嘢蝕晒。乾淨上限
大約 0.5 goto/s，即係 993 張 ≈ 34 分鐘 —— 呢個係 Cloudflare 個閘，唔係腳本慢。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
from pricecharting_cf_session import _is_cf  # noqa: E402
from pc_psa10_price_derivation import validate_pc_psa10  # noqa: E402

MAP = ROOT / "data/runtime/private-source-map/c11_pc_ebay_map_full900.jsonl"
OUT = ROOT / "data/runtime/operator/collect/pc_cdp_refresh_report.json"
PY_WSL = "wsl.exe"
BACKOFF_LADDER = (30.0, 60.0, 120.0)
# 呢兩個數係量返嚟嘅，唔係估；calibration 全表見 docs/COLLECTION_RUNBOOK.md。
# 唔好淨係睇住「加分頁 = 快」就改大 —— 實測 4 分頁比 2 分頁**慢**，因為每食一次
# 429 就要全部分頁一齊停 30/60/120 秒，賺嘅嘢蝕晒。
PC_TABS = 2
PC_SLEEP_SECONDS = 3.0
# 邊個 status 由邊個 counter 記住。撤銷一個判死嗰陣要減返啱嗰幾個 —— 呢個表存在
# 嘅原因就係曾經「加嘅時候加兩個、減嘅時候減錯一個」，令 fail 少報咗一個。
FAILURE_COUNTERS = {
    "rate_limited": ("fail", "rateLimited"),
    "cf_or_fail": ("cf",),
    "server_error": ("fail",),
}
DEFAULT_FAILURE_COUNTERS = ("fail",)
RETRYABLE_STATUSES = ("rate_limited", "cf_or_fail", "server_error")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical_url_from_html(value: str) -> str:
    patterns = (
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',
        r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']canonical["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, value, re.I)
        if match:
            return unescape(match.group(1)).rstrip("/")
    return ""


def url_key(value: str) -> str:
    return unescape(str(value or "")).rstrip("/")


def ensure_cdp(port: int = 9333) -> None:
    ps1 = ROOT / "scripts" / "ensure_chrome_cdp.ps1"
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1), "-Port", str(port)],
        check=False,
    )


def arm_stall_watchdog(state: dict) -> None:
    """Turn a wedged CDP session into a bounded, visible failure.

    Playwright protocol calls such as page.content()/page.title() accept no
    timeout, so one dropped CDP response can park the run forever while the
    caller's subprocess timeout scales with batch size (hours). If the page
    loop stops beating for state["limit"] seconds, persist a partial report
    for forensics and hard-exit 3 so collect_control records a failed lane
    and the next incr run resumes from checkpoints.
    """

    def _watch() -> None:
        while not state.get("done"):
            time.sleep(15.0)
            limit = float(state.get("limit") or 0.0)
            if limit and (time.monotonic() - float(state["beat"])) > limit:
                try:
                    OUT.parent.mkdir(parents=True, exist_ok=True)
                    OUT.write_text(
                        json.dumps(
                            {
                                "asOf": utc_now(),
                                "watchdogStall": True,
                                "stallLimitSeconds": limit,
                                "batch": state.get("batch"),
                                "results": state.get("results"),
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                except Exception:  # noqa: BLE001
                    pass
                print(f"WATCHDOG_STALL no page-loop progress for {limit:.0f}s; exit 3", flush=True)
                os._exit(3)

    threading.Thread(target=_watch, daemon=True, name="stall-watchdog").start()


async def run_fetch_pool_with_pages(
    pending: list[dict],
    *,
    pages: list,
    sleep_seconds: float,
    challenge_wait: float,
    watchdog: dict,
    results: list[dict],
    start_index: int,
    batch_size: int,
) -> dict:
    """揸 `tabs` 條 tab 同時抽，共用一條 backoff。

    `throttle` 係共用嘅：一條 tab 食到 429 / CF，`until` 一推，全部 tab 落到
    下一頁之前都會等。舊版嗰個 `backoff_level` 係單線程獨有嘅，照搬落多 tab
    就變成「其餘 N-1 條照衝」—— 即係越撞越快，一定會俾人封。

    429 / CF 會**擺返落隊尾重試**，唔係當場判死。呢個唔係「容錯」，係做完件事：
    上游一 lane fail-closed，991 張成功嘅頁一齊唔入庫、checkpoint 一步都唔郁。
    實測 2026-08-11 全量 993 張：ok 991、429 兩張 → `inserted 0, checkpointed 0`。
    993 張入面撞到一兩次 429 幾乎係必然（實測率 0.2%），即係無人睇住嘅話呢條
    lane 日日都會咁死。而個共用 backoff 本來就已經等咗 30/60/120 秒 —— 等完之後
    唔重試返嗰張卡，等於白等。擺落隊尾係最疏嘅間隔（成隊行晒先輪到佢）。

    上游 5xx 一樣要重試。2026-08-12 第二轉全量：429 全部重試成功（rateLimited 0），
    但 **10 版一次過回 HTTP 500**（同一個 5189 bytes error page，vid 1009–1093），
    而嗰 10 條 URL 40 分鐘之前先啱啱成功過 —— 純粹上游一陣間唔得。舊版將佢跌落
    `product_id_mismatch`（500 個頁冇 product-id，identity 梗係唔過），而個 status
    係終局判決，於是 983 版好頁又一次全部作廢。5xx 唔用共用 backoff：佢係單版嘢壞，
    唔係成個 IP 俾人限速。
    """
    out: dict = {"ok": 0, "fail": 0, "cf": 0, "rateLimited": 0, "sessionError": None}
    queue: asyncio.Queue = asyncio.Queue()
    for index, row in enumerate(pending):
        queue.put_nowait((index, row, 0))
    throttle = {"level": 0, "until": 0.0}
    counter = {"done": 0}
    retried: list[dict] = []

    async def worker(page, tab_index: int) -> None:
        while True:
            try:
                index, row, attempt = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            watchdog["beat"] = time.monotonic()
            counter["done"] += 1
            position = start_index + counter["done"]
            url = unescape(str(row.get("pc_url") or ""))
            html_rel = row.get("html_path") or row.get("htmlPath")
            if not url or not html_rel:
                out["fail"] += 1
                results.append({"variant_id": row.get("variant_id"), "status": "missing"})
                continue
            target = ROOT / html_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            while True:
                remaining = throttle["until"] - time.monotonic()
                if remaining <= 0:
                    break
                watchdog["beat"] = time.monotonic()
                await asyncio.sleep(min(remaining, 5.0))
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=120000)
                code = response.status if response is not None else None
                retry_after = response.headers.get("retry-after") if response is not None else None
                html = await page.content()
                title = (await page.title()).strip()
            except Exception as exc:  # noqa: BLE001
                out["fail"] += 1
                results.append(
                    {
                        "variant_id": row.get("variant_id"),
                        "status": "navigation_error",
                        "error": f"{type(exc).__name__}:{exc}",
                    }
                )
                continue
            expected_product_id = str(row.get("pc_product_id") or "").strip()
            product_match = re.search(r'\bproduct-id=["\'](\d+)["\']', html, re.I)
            actual_product_id = product_match.group(1) if product_match else ""
            canonical_url = canonical_url_from_html(html)
            blocked = _is_cf(title, html) if html else True
            challenge_resolved = False
            if code == 403 and blocked and challenge_wait > 0:
                deadline = time.monotonic() + challenge_wait
                while time.monotonic() < deadline:
                    await page.wait_for_timeout(5000)
                    watchdog["beat"] = time.monotonic()
                    html = await page.content()
                    title = (await page.title()).strip()
                    product_match = re.search(r'\bproduct-id=["\'](\d+)["\']', html, re.I)
                    actual_product_id = product_match.group(1) if product_match else ""
                    canonical_url = canonical_url_from_html(html)
                    blocked = _is_cf(title, html) if html else True
                    if (
                        len(html) > 5000
                        and not blocked
                        and actual_product_id == expected_product_id
                        and canonical_url == url_key(url)
                        and page.url == url
                    ):
                        challenge_resolved = True
                        break
            identity_ok = (
                bool(expected_product_id)
                and actual_product_id == expected_product_id
                and canonical_url == url_key(url)
            )
            # 多 tab 之後 pid 唔再夠獨特 —— 兩條 tab 唔會撞同一張卡，但一齊寫
            # 同一個 `.pid.next` 名就會互相踩。加 tab index 收返。
            temporary = target.with_name(f".{target.name}.{os.getpid()}.{tab_index}.next")
            await asyncio.to_thread(
                temporary.write_text, html, encoding="utf-8", errors="replace"
            )
            validation_row = dict(row)
            validation_row.pop("htmlPath", None)
            validation_row["html_path"] = str(temporary)
            validation_row["notes"] = ""
            # 300 KB HTML 全 parse 一次，實測百幾 ms。留喺 event loop 度就等於
            # 全部 tab 排住隊等佢，tab 開幾多都冇用。
            exact_price, exact_reason = await asyncio.to_thread(
                validate_pc_psa10, validation_row
            )
            explicit_psa10_ok = exact_price is not None
            if (
                (code == 200 or challenge_resolved)
                and len(html) > 5000
                and not blocked
                and identity_ok
                and explicit_psa10_ok
            ):
                os.replace(temporary, target)
                out["ok"] += 1
                status = "ok"
            else:
                temporary.unlink(missing_ok=True)
                if code == 429:
                    status = "rate_limited"
                elif blocked:
                    status = "cf_or_fail"
                elif code is not None and code >= 500:
                    # 上游 5xx 唔係「我哋張 map 錯」，係佢哋伺服器嗰陣唔得。分開一個
                    # status 嚟講，係因為舊版將佢跌落 product_id_mismatch（500 個頁
                    # 冇 product-id，identity 一定唔過），而 product_id_mismatch 係
                    # 終局判決 —— 即係上游打個乞嚏就報「我哋認錯咗卡」。
                    status = "server_error"
                elif identity_ok and not explicit_psa10_ok:
                    status = "explicit_psa10_missing"
                else:
                    status = "product_id_mismatch" if not identity_ok else "http_or_content_fail"
                for key in FAILURE_COUNTERS.get(status, DEFAULT_FAILURE_COUNTERS):
                    out[key] += 1
            results.append({
                "variant_id": row.get("variant_id"),
                "status": status,
                "code": code,
                "len": len(html),
                "title": title[:80],
                "expectedProductId": expected_product_id or None,
                "actualProductId": actual_product_id or None,
                "actualCanonicalUrl": canonical_url or None,
                "explicitPsa10": explicit_psa10_ok,
                "explicitPsa10Reason": exact_reason,
                "retryAfter": retry_after,
                "challengeResolved": challenge_resolved,
            })
            print(
                f"[{position}/{batch_size}] {status} vid={row.get('variant_id')} "
                f"len={len(html)} code={code} tab={tab_index}",
                flush=True,
            )
            if status in RETRYABLE_STATUSES:
                if status == "server_error":
                    # 5xx 係單版嘢壞，唔係成個 IP 俾人限速 —— 停晒全部分頁冇意思，
                    # 而且 10 版 500 × 3 次重試 × 30/60/120s 會白白蝕半個鐘。擺落
                    # 隊尾等成隊行完先再試，本身已經係最疏嘅間隔。
                    delay = 0.0
                else:
                    delay = BACKOFF_LADDER[min(throttle["level"], len(BACKOFF_LADDER) - 1)]
                    throttle["level"] += 1
                    throttle["until"] = max(throttle["until"], time.monotonic() + delay)
                requeued = attempt + 1 <= len(BACKOFF_LADDER)
                if requeued:
                    # 呢一筆唔算數：撤返啱先加落 out 同 results 嗰個判死，擺返落隊尾。
                    results.pop()
                    for key in FAILURE_COUNTERS.get(status, DEFAULT_FAILURE_COUNTERS):
                        out[key] -= 1
                    counter["done"] -= 1
                    retried.append({"variant_id": row.get("variant_id"), "attempt": attempt + 1, "status": status})
                    queue.put_nowait((index, row, attempt + 1))
                print(
                    f"{f'backoff {delay:.0f}s (all tabs)' if delay else 'no backoff (upstream 5xx)'}"
                    f" after {status} vid={row.get('variant_id')}"
                    f"{f'; requeued attempt {attempt + 2}' if requeued else '; giving up'}",
                    flush=True,
                )
                continue
            if status != "ok":
                continue
            throttle["level"] = 0
            if sleep_seconds > 0:
                await asyncio.sleep(sleep_seconds)

    await asyncio.gather(*(worker(page, index) for index, page in enumerate(pages)))
    out["retries"] = retried
    return out


async def run_fetch_pool(
    pending: list[dict],
    *,
    cdp_port: int,
    tabs: int,
    **kwargs,
) -> dict:
    """開 CDP、備妥 `tabs` 條 tab，然後交俾上面條 pool 行。

    分開兩層淨係為咗一件事：上面條 pool 入面嘅 queue／backoff／requeue 算術，可以
    用假 page 直接測（見 scripts/test_pc_refresh_requeue.py）。呢層有 Playwright，
    測唔到；嗰層冇，測得到。
    """
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
            if not browser.contexts:
                raise RuntimeError("dedicated CARDZ Chrome has no browser context")
            context = browser.contexts[0]
            price_pages = [
                page for page in context.pages if "pricecharting.com" in (page.url or "")
            ]
            pool = price_pages[:tabs]
            for extra in price_pages[tabs:]:
                await extra.close()
            while len(pool) < tabs:
                pool.append(await context.new_page())
            # 唔好諗住 `page.route` 擋走圖／css 嚟慳額度：試過，會反效果。一版產品頁
            # 向 www.pricecharting.com 打 31 個 request（15 圖 / 6 script / 4 css /
            # 2 manifest / 2 xhr / 1 fetch / 1 document），睇落擋走 21 個就可以行快
            # 三倍。實際係同一個設定（2 分頁 / 3.0s）、隔 3 分鐘背對背行兩轉：
            # 唔擋 40/40 全清 2.05 s/頁，擋咗 38/40 兩次 429 3.69 s/頁。Cloudflare
            # 見到「瀏覽器」淨係攞 HTML 唔攞 css／圖，直接當你係 bot。要扮足全套。
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": 0,
                "fail": 0,
                "cf": 0,
                "rateLimited": 0,
                "retries": [],
                "sessionError": f"single_cdp_connect:{type(exc).__name__}:{exc}",
            }
        out = await run_fetch_pool_with_pages(pending, pages=pool, **kwargs)
        # 收工剩返一條 tab，同單 tab 年代嘅 session 狀態一模一樣。
        for extra in pool[1:]:
            try:
                await extra.close()
            except Exception:  # noqa: BLE001
                pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 = all selected rows")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=PC_SLEEP_SECONDS)
    ap.add_argument(
        "--workers",
        type=int,
        default=PC_TABS,
        help="concurrent tabs inside the one CARDZ CDP session",
    )
    ap.add_argument(
        "--challenge-wait",
        type=float,
        default=120.0,
        help="wait on the same 403 page for its JS/human challenge to resolve; never reload",
    )
    ap.add_argument("--cdp-port", type=int, default=9333)
    ap.add_argument(
        "--cdp-already-ensured",
        action="store_true",
        help="caller already started the singleton CARDZ CDP session while holding its adapter leases",
    )
    ap.add_argument(
        "--resume-report",
        type=Path,
        help="reuse only previously successful exact-ID pages from this durable report",
    )
    ap.add_argument(
        "--variant-ids-file",
        type=Path,
        help="exact active variant IDs to refresh; one integer per line",
    )
    ap.add_argument("--no-ingest", action="store_true")
    args = ap.parse_args()

    started_monotonic = time.monotonic()
    if not args.cdp_already_ensured:
        ensure_cdp(args.cdp_port)
    rows = [json.loads(l) for l in MAP.read_text(encoding="utf-8").splitlines() if l.strip()]
    requested_ids: list[int] = []
    if args.variant_ids_file:
        requested_ids = list(
            dict.fromkeys(
                int(line.strip())
                for line in args.variant_ids_file.read_text(encoding="utf-8-sig").splitlines()
                if line.strip()
            )
        )
        requested_set = set(requested_ids)
        rows = [row for row in rows if int(row.get("variant_id") or 0) in requested_set]
    else:
        requested_set = set()
    selected_ids = {int(row.get("variant_id") or 0) for row in rows}
    missing_requested = sorted(requested_set - selected_ids)
    rows = rows[args.offset :]
    batch = rows if args.limit <= 0 else rows[: args.limit]
    row_by_variant = {int(row["variant_id"]): row for row in batch}
    reused_results: list[dict] = []
    if args.resume_report:
        previous = json.loads(args.resume_report.read_text(encoding="utf-8-sig"))
        if not (
            previous.get("singleBrowserSession") is True
            and int(previous.get("fallbackBrowsers", -1)) == 0
            and int(previous.get("cdpPort") or 0) == args.cdp_port
        ):
            raise RuntimeError("resume report was not produced by the strict single-browser contract")
        for prior in previous.get("results") or []:
            if prior.get("status") != "ok":
                continue
            variant_id = int(prior.get("variant_id") or 0)
            row = row_by_variant.get(variant_id)
            if not row:
                continue
            expected_product_id = str(row.get("pc_product_id") or "").strip()
            html_path = ROOT / str(row.get("html_path") or row.get("htmlPath") or "")
            html = html_path.read_text(encoding="utf-8", errors="replace") if html_path.is_file() else ""
            product_match = re.search(r'\bproduct-id=["\'](\d+)["\']', html, re.I)
            actual_product_id = product_match.group(1) if product_match else ""
            canonical_url = canonical_url_from_html(html)
            title_match = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
            title = (title_match.group(1) if title_match else "").strip()
            if (
                len(html) <= 5000
                or _is_cf(title, html)
                or actual_product_id != expected_product_id
                or canonical_url != url_key(row.get("pc_url"))
                or int(prior.get("len") or -1) != len(html)
            ):
                raise RuntimeError(f"resume artifact no longer matches exact product for variant {variant_id}")
            reused_results.append({**prior, "resumed": True})

    reused_ids = {int(row["variant_id"]) for row in reused_results}
    pending_batch = [row for row in batch if int(row["variant_id"]) not in reused_ids]
    ok = len(reused_results)
    fail = cf = rate_limited = 0
    results = list(reused_results)
    session_error = None
    retries: list[dict] = []
    watchdog = {
        "beat": time.monotonic(),
        # Worst legitimate iteration: goto 120s + challenge wait (default
        # 120s, beaten every 5s inside) + backoff 120s + politeness sleep.
        # 600s of silence therefore means a lost CDP reply, not a slow page.
        "limit": 600.0,
        "done": False,
        "batch": len(batch),
        "results": results,
    }
    arm_stall_watchdog(watchdog)
    if pending_batch:
        pool = asyncio.run(
            run_fetch_pool(
                pending_batch,
                cdp_port=args.cdp_port,
                tabs=max(1, int(args.workers)),
                sleep_seconds=max(0.0, float(args.sleep)),
                challenge_wait=float(args.challenge_wait),
                watchdog=watchdog,
                results=results,
                start_index=len(reused_results),
                batch_size=len(batch),
            )
        )
        ok += pool["ok"]
        fail += pool["fail"]
        cf += pool["cf"]
        rate_limited += pool["rateLimited"]
        session_error = pool["sessionError"]
        retries = pool["retries"]


    ingest = None
    if not args.no_ingest and ok == len(batch) and fail == 0 and cf == 0:
        # C11 via WSL backend venv (MySQL path lives there); ingest must run in
        # THIS checkout (ROOT), never a hardcoded sibling tree.
        watchdog["beat"] = time.monotonic()
        watchdog["limit"] = 1500.0  # WSL ingest is bounded by its own timeout=1200
        root_wsl = "/mnt/" + ROOT.drive[0].lower() + ROOT.as_posix()[len(ROOT.drive):]
        cmd = [
            PY_WSL, "-d", "Ubuntu", "--", "bash", "-lc",
            f"cd '{root_wsl}' && "
            "/home/jackson0202/cardz-market-cap/.venv-backend/bin/python -X utf8 "
            "pipelines/c11_pc_sold_ingest.py --map data/runtime/private-source-map/c11_pc_ebay_map_full900.jsonl --write"
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1200)
        ingest = {"exit": r.returncode, "stdoutTail": (r.stdout or "")[-2500:], "stderrTail": (r.stderr or "")[-800:]}

    report = {
        "asOf": utc_now(),
        "limit": args.limit,
        "offset": args.offset,
        "cdpPort": args.cdp_port,
        "singleBrowserSession": True,
        "fallbackBrowsers": 0,
        "tabs": max(1, int(args.workers)),
        "sleepSeconds": max(0.0, float(args.sleep)),
        "elapsedSeconds": round(time.monotonic() - started_monotonic, 1),
        "retries": retries,
        "sessionError": session_error,
        "resumeReport": str(args.resume_report) if args.resume_report else None,
        "reused": len(reused_results),
        "fetched": ok - len(reused_results),
        "rateLimited": rate_limited,
        "batch": len(batch),
        "requested": len(requested_ids),
        "selected": len(rows),
        "missingRequestedVariantIds": missing_requested,
        "ok": ok,
        "cf": cf,
        "fail": fail,
        "results": results,
        "ingest": ingest,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    watchdog["done"] = True
    print(json.dumps({k: report[k] for k in ["asOf", "batch", "ok", "cf", "fail"]}, ensure_ascii=False, indent=2))
    print(f"REPORT {OUT}")
    complete = (
        len(batch) > 0
        and ok == len(batch)
        and fail == 0
        and cf == 0
        and rate_limited == 0
        and session_error is None
        and len(results) == len(batch)
        and not missing_requested
        and (not requested_ids or len(batch) == len(requested_ids))
        and (ingest is None or ingest.get("exit") == 0)
    )
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
