#!/usr/bin/env python3
"""
SNKRDUNK 全圖鑑發現器（search-HTML 路線）
=========================================

突破點：`/search?keywords=...&page=N` 係 server-rendered，
plain requests 就可以抽 `/apparels/{id}`，唔使 browser、唔使過 CF。
分頁去到 100+ 都有新卡，係真正嘅全圖鑑 discovery route（比 BFS same-category 闊得多）。

用法：
    # 用一組 keyword 發現大量 item id
    python snkrdunk_discover.py --keywords ポケモンカードゲーム pokemon ピカチュウ リザードン \
        --max-pages 60 --out data/private/snkrdunk_brute/discovered_ids.txt

    # 發現完即時 harvest（call 返 snkrdunk_bulk.pull_all）
    python snkrdunk_discover.py --keywords ポケモンカードゲーム --max-pages 60 --harvest \
        --out data/private/snkrdunk_brute/discovered_ids.txt \
        --harvest-out data/private/snkrdunk_brute/snkrdunk_all.jsonl

產物：
    discovered_ids.txt   每行一個 apparel id（可直接俾 snkrdunk_bulk 嘅 --ids-file 精神用）
    harvest jsonl        master + 16 condition + K線 + 成交（由 pull_all 產生）
"""
from __future__ import annotations

import argparse
import re
import time
import urllib.parse
from pathlib import Path

import requests

import sys
sys.path.insert(0, str(Path(__file__).parent))
from snkrdunk_bulk import pull_all, UA  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "private" / "snkrdunk_brute"
DATA_DIR.mkdir(parents=True, exist_ok=True)

ITEM_RE = re.compile(r"/apparels/(\d+)")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def discover_ids(keywords: list[str], max_pages: int, delay: float) -> list[int]:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    seen: list[int] = []
    seen_set: set[int] = set()
    for kw in keywords:
        log(f"keyword: {kw}")
        for page in range(1, max_pages + 1):
            url = "https://snkrdunk.com/search?keywords=" + urllib.parse.quote(kw) + f"&page={page}"
            try:
                r = s.get(url, timeout=25)
            except requests.RequestException as e:
                log(f"  page {page} ERROR {e}")
                time.sleep(delay * 3)
                continue
            if r.status_code != 200:
                log(f"  page {page} status {r.status_code}, stop keyword")
                break
            ids = [int(x) for x in dict.fromkeys(ITEM_RE.findall(r.text))]
            new = [i for i in ids if i not in seen_set]
            for i in new:
                seen_set.add(i)
                seen.append(i)
            if page % 10 == 0 or page == 1:
                log(f"  page {page}: {len(ids)} ids, +{len(new)} new, total={len(seen)}")
            if not ids:  # 冇結果，停呢個 keyword
                log(f"  page {page}: empty, stop keyword")
                break
            time.sleep(delay)
    return seen


def main() -> None:
    ap = argparse.ArgumentParser(description="SNKRDUNK 全圖鑑發現器")
    ap.add_argument("--keywords", nargs="+", required=True)
    ap.add_argument("--max-pages", type=int, default=60)
    ap.add_argument("--delay", type=float, default=0.8)
    ap.add_argument("--out", default=str(DATA_DIR / "discovered_ids.txt"))
    ap.add_argument("--harvest", action="store_true", help="發現完即時 harvest")
    ap.add_argument("--harvest-out", default=str(DATA_DIR / "snkrdunk_all.jsonl"))
    ap.add_argument("--harvest-delay", type=float, default=1.0)
    args = ap.parse_args()

    ids = discover_ids(args.keywords, args.max_pages, args.delay)
    log(f"DISCOVERED total unique ids: {len(ids)}")

    out_path = Path(args.out)
    out_path.write_text("\n".join(str(i) for i in ids) + "\n", encoding="ascii")
    log(f"ids written -> {out_path}")

    if args.harvest and ids:
        log("開始 harvest ...")
        report = pull_all(ids, Path(args.harvest_out), delay=args.harvest_delay)
        log(f"harvest done: {report}")


if __name__ == "__main__":
    main()
