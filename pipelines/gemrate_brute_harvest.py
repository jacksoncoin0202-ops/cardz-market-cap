#!/usr/bin/env python3
"""
GemRate Brute-Force Harvester（curl_cffi 版 — 唔使 browser）
=============================================================
用 curl_cffi 模擬 Chrome TLS 指紋，直接 HTTP GET/POST 拎 inline setsData / rowData。
**唔使 Playwright、唔使開 browser**（2026-07-24 突破：Cloudflare 靠 TLS 指紋就過到）。

Usage:
    python gemrate_brute_harvest.py --all-sets
    python gemrate_brute_harvest.py --set-id <40-hex>
    python gemrate_brute_harvest.py --query "rayquaza vmax"

Requires: pip install curl_cffi
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

from curl_cffi import requests as cr

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "private" / "gemrate_brute"
DATA_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT}
SET_DELAY = 1.0  # 每個 set 之間嘅 delay（秒）
RETRY_SLEEP = 10.0  # transient set failure 重試前等幾耐（秒）


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _get(url: str, **kw):
    kw.setdefault("impersonate", "chrome")
    kw.setdefault("timeout", 40)
    kw.setdefault("headers", HEADERS)
    return cr.get(url, **kw)


def _post(url: str, **kw):
    kw.setdefault("impersonate", "chrome")
    kw.setdefault("timeout", 40)
    h = dict(HEADERS)
    h.update(kw.pop("headers", {}))
    kw["headers"] = h
    return cr.post(url, **kw)


def extract_sets_data() -> list[dict]:
    """由 /universal-pop-report 抽 inline setsData。"""
    log("Fetching /universal-pop-report ...")
    r = _get("https://www.gemrate.com/universal-pop-report")
    if r.status_code != 200:
        raise RuntimeError(f"universal-pop-report status {r.status_code}")
    html = r.text
    big = None
    for m in re.finditer(r"<script[^>]*>(.*?)</script>", html, re.DOTALL):
        if len(m.group(1)) > 200000:
            big = m.group(1)
            break
    if not big:
        raise RuntimeError("setsData big script not found")
    m = re.search(r"setsData\s*=\s*(\[.*?\]);", big, re.DOTALL)
    if not m:
        raise RuntimeError("setsData regex failed")
    cleaned = m.group(1).replace(": NaN", ":null").replace(":NaN", ":null")
    cleaned = cleaned.replace(": Infinity", ":null").replace(":-Infinity", ":null")
    sets_data = json.loads(cleaned)
    log(f"Extracted {len(sets_data)} sets")
    return sets_data


def extract_row_data(set_link: str) -> list[dict]:
    """由 set 頁抽 inline rowData。"""
    url = set_link if set_link.startswith("http") else f"https://www.gemrate.com{set_link}"
    r = _get(url)
    if r.status_code != 200:
        raise RuntimeError(f"set page status {r.status_code}")
    html = r.text
    m = re.search(r"const rowData = JSON\.parse\('(.*?)'\);", html, re.DOTALL)
    if not m:
        raise RuntimeError("rowData not found on set page")
    js = m.group(1)
    try:
        row_data = json.loads(js)
    except json.JSONDecodeError:
        row_data = json.loads(js.encode("utf-8").decode("unicode_escape"))
    return row_data


def save_jsonl(data: list[dict], path: Path) -> None:
    # atomic：先寫 sibling .tmp 再 os.replace，crash 唔會爛咗舊檔
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for row in data:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def load_jsonl(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def fetch_set_with_retry(set_link: str) -> list[dict]:
    """拎一個 set 嘅 rowData；transient failure（exception 或空結果）重試一次。"""
    err = "unknown"
    for attempt in (1, 2):
        try:
            cards = extract_row_data(set_link)
            if cards:
                return cards
            err = "empty rowData"
        except Exception as e:
            err = str(e)
        if attempt == 1:
            log(f"    retry in {RETRY_SLEEP:.0f}s ({err})")
            time.sleep(RETRY_SLEEP)
    raise RuntimeError(err)


def harvest_all_sets(limit: Optional[int] = None, resume: bool = False) -> None:
    sets_data = extract_sets_data()

    def _is_tcg(s): return (s.get("category") or "").strip().lower() == "tcg"
    # This page exposes a fixed public set snapshot, not a paginated/global
    # GemRate catalogue.  Keep every TCG set present in that snapshot; callers
    # must not use this artifact alone as a provider-wide census.
    tcg_sets = [s for s in sets_data if _is_tcg(s)]
    log(
        f"Found {len(tcg_sets)} TCG sets in the public {len(sets_data)}-set "
        "snapshot (all years; not a global catalogue)"
    )
    if limit:
        tcg_sets = tcg_sets[:limit]
        log(f"Limited to first {limit} sets")

    all_cards, failed_sets = [], []
    for i, s in enumerate(tcg_sets, 1):
        set_id, set_link = s.get("set_id"), s.get("set_link")
        set_name = s.get("set_name", "unknown")
        safe = re.sub(r"[^\w\-]+", "_", set_name)[:60]
        set_path = DATA_DIR / f"set_{set_id}_{safe}.jsonl"
        if resume and set_path.exists() and set_path.stat().st_size > 0:
            cards = load_jsonl(set_path)
            all_cards.extend(cards)
            log(f"[{i}/{len(tcg_sets)}] {set_name} — resume: {len(cards)} cards from {set_path.name}")
            continue
        log(f"[{i}/{len(tcg_sets)}] {set_name}")
        try:
            cards = fetch_set_with_retry(set_link)
            for card in cards:
                card["_set_id"] = set_id
                card["_set_name"] = set_name
                card["_set_link"] = set_link
            all_cards.extend(cards)
            save_jsonl(cards, set_path)
            log(f"    -> {len(cards)} cards")
            time.sleep(SET_DELAY)
        except Exception as e:
            log(f"    ERROR: {e}")
            failed_sets.append({"set_id": set_id, "set_name": set_name, "error": str(e)})
            time.sleep(SET_DELAY * 2)

    save_jsonl(all_cards, DATA_DIR / "all_cards.jsonl")
    log(f"Total cards harvested: {len(all_cards)}")
    if failed_sets:
        save_jsonl(failed_sets, DATA_DIR / "failed_sets.jsonl")
        log(f"Failed sets: {len(failed_sets)}")
    psa = [c for c in all_cards if int(c.get("psa_10") or 0) >= 1000]
    log(f"Cards with PSA 10 >= 1000: {len(psa)}")
    save_jsonl(psa, DATA_DIR / "psa10_1000_plus.jsonl")


def harvest_single_set(set_id: str) -> None:
    sets_data = extract_sets_data()
    target = next((s for s in sets_data if s.get("set_id") == set_id), None)
    if not target:
        raise ValueError(f"Set ID {set_id} not found")
    log(f"Found: {target.get('set_name')}")
    cards = extract_row_data(target["set_link"])
    save_jsonl(cards, DATA_DIR / f"set_{set_id}.jsonl")
    log(f"Saved {len(cards)} cards")


def search_cards(query: str) -> None:
    log(f"Searching: {query}")
    r = _post("https://www.gemrate.com/universal-search-query",
              json={"query": query},
              headers={"Content-Type": "application/json",
                       "Origin": "https://www.gemrate.com",
                       "Referer": "https://www.gemrate.com/"})
    result = r.json()
    log(f"Found {len(result)} results")
    for x in result[:20]:
        log(f"  {x.get('description','N/A')[:60]} | gemrate_id: {x.get('gemrate_id','')[:16]}...")
    safe_q = re.sub(r"[^\w]+", "_", query)[:40]
    save_jsonl(result, DATA_DIR / f"search_{safe_q}.jsonl")


def main() -> None:
    ap = argparse.ArgumentParser(description="GemRate Brute-Force Harvester (curl_cffi, no browser)")
    ap.add_argument("--all-sets", action="store_true")
    ap.add_argument("--set-id", type=str)
    ap.add_argument("--query", type=str)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--resume", action="store_true",
                    help="skip sets whose per-set jsonl already exists (non-empty), reuse its rows")
    args = ap.parse_args()

    if not any([args.all_sets, args.set_id, args.query]):
        ap.print_help()
        sys.exit(1)

    if args.all_sets:
        harvest_all_sets(limit=args.limit, resume=args.resume)
    elif args.set_id:
        harvest_single_set(args.set_id)
    elif args.query:
        search_cards(args.query)
    log("Done.")


if __name__ == "__main__":
    main()
