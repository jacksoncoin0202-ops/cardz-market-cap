#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""429 唔准當場判死 —— 要擺返落隊尾重試。

點解要有呢個 test：2026-08-11 全量 993 張跑咗 47 分鐘，`ok 991 / rateLimited 2`，
結果 `inserted 0, checkpointed 0` —— 一筆都冇入庫。`refresh_pc_pages` 要
`ok == expected and fail == 0 and cf == 0` 先算成功，所以 0.2% 失敗率丟咗 100%
成果。而實測 429 率就係 0.2%，即係 993 張入面撞到一兩次幾乎必然：無人睇住嘅話
呢條 lane 日日都會咁死。

而個共用 backoff 本來就已經停晒全部分頁 30/60/120 秒 —— 等完唔重試返嗰張卡就係
白等。呢個 test 守住：食完 429 之後，嗰張卡要行返一轉，而且最終報告要係乾淨嘅。

Run: python -X utf8 scripts/test_pc_refresh_requeue.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import pc_cdp_sold_refresh_win as mod  # noqa: E402

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


PRODUCT_ID = "7108819"
URL = "https://www.pricecharting.com/game/pokemon-promo/test-card-85"
def html_for(url: str) -> str:
    """真頁面嘅 canonical 一定同請求嘅 URL 對得返 —— identity check 就係驗呢樣。"""
    return (
        f'<html><head><link rel="canonical" href="{url}"></head>'
        f'<body product-id="{PRODUCT_ID}">' + ("x" * 6000) + "</body></html>"
    )


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status
        self.headers: dict[str, str] = {}


class FakePage:
    """一條 tab。`fail_first` 入面嘅 vid 第一次一定回 429。"""

    def __init__(self, script: dict[str, list[int]], seen: list[str]) -> None:
        self.script = script
        self.seen = seen
        self.url = URL
        self._current = ""

    async def goto(self, url, **_kwargs):
        self._current = url
        self.url = url
        vid = url.rsplit("/", 1)[-1]
        self.seen.append(vid)
        codes = self.script.get(vid) or [200]
        return FakeResponse(codes.pop(0) if len(codes) > 1 else codes[0])

    async def content(self):
        return html_for(self._current)

    async def title(self):
        return "Test Card PSA 10 Prices"

    async def wait_for_timeout(self, _ms):
        return None


async def drive(rows, script, *, tabs=2):
    seen: list[str] = []
    results: list[dict] = []
    pages = [FakePage(script, seen) for _ in range(tabs)]

    # 真嘅 run_fetch_pool 入面 `async with async_playwright()` 嗰段係開 browser，
    # 呢度直接叫返入面條 worker loop 唔行得，所以複製唔到 —— 改為 patch 走 CDP：
    # 我哋要測嘅係 queue / backoff / requeue 嘅算術，唔係 Playwright。
    return await mod.run_fetch_pool_with_pages(
        rows,
        pages=pages,
        sleep_seconds=0.0,
        challenge_wait=0.0,
        watchdog={"beat": 0.0},
        results=results,
        start_index=0,
        batch_size=len(rows),
    ), seen, results


def rows_for(vids, tmpdir: Path):
    return [
        {
            "variant_id": vid,
            "pc_url": f"{URL.rsplit('/', 1)[0]}/{vid}",
            "pc_product_id": PRODUCT_ID,
            "html_path": str((tmpdir / f"{vid}.html").relative_to(ROOT))
            if str(tmpdir).startswith(str(ROOT))
            else f"temp/pc_requeue_test/{vid}.html",
        }
        for vid in vids
    ]


def main() -> int:
    mod.BACKOFF_LADDER = (0.01, 0.02, 0.03)
    mod.validate_pc_psa10 = lambda _row: (1.0, "ok")
    mod._is_cf = lambda _title, _html: False

    tmpdir = Path(tempfile.mkdtemp(prefix="pc_requeue_", dir=str(ROOT / "temp")))
    try:
        vids = ["a", "b", "c", "d"]
        rows = rows_for(vids, tmpdir)
        for row in rows:
            row["pc_url"] = f"{URL.rsplit('/', 1)[0]}/{row['variant_id']}"

        # "b" 第一次 429、第二次 200：即係實測嗰個形狀。
        out, seen, results = asyncio.run(drive(rows, {"b": [429, 200]}))

        check("食完 429 重試之後全清", out["ok"], len(vids))
        check("重試成功就唔算 fail", out["fail"], 0)
        check("重試成功就唔算 rateLimited", out["rateLimited"], 0)
        check("被 429 嗰張真係再行過一轉", seen.count("b"), 2)
        check("報告有留低重試紀錄", len(out["retries"]), 1)
        check("results 冇留低撤咗嗰筆判死", len(results), len(vids))
        check("results 全部 ok", sorted(r["status"] for r in results), ["ok"] * len(vids))

        # 一路 429 就要停：唔准無限重試。ladder 3 級 = 最多 3 次重試。
        out2, seen2, _ = asyncio.run(drive(rows_for(["z"], tmpdir), {"z": [429]}))
        check("一路 429 最終仍然判死", out2["fail"], 1)
        check("重試次數受 ladder 封頂", seen2.count("z"), len(mod.BACKOFF_LADDER) + 1)
    finally:
        for path in tmpdir.glob("*"):
            path.unlink(missing_ok=True)
        tmpdir.rmdir()

    for line in FAILED:
        print(line)
    print(f"{CHECKS - len(FAILED)}/{CHECKS} checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
