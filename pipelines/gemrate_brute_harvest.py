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
INDEX_URL = "https://www.gemrate.com/universal-pop-report"
# 2026-09-21 live: the set-list page answered 403 on both census attempts and
# the harvest died on its first request.  A Cloudflare 403 here has cleared
# after a pause (09-23: a 403'd set page answered on its retry), so the list
# page gets a bounded backoff before the harvest gives up.
INDEX_RETRY_WAITS = (30.0, 90.0)
# 2026-09-22/23 live: 1 and then 4 set pages stayed 403 through their single
# retry, the harvest still exited 0, and a census 73 qualified cards short was
# promoted as refreshed.  Failed sets now get one more pass after a cool-down.
# If any set still fails, the combined census files are NOT rewritten and the
# run exits INCOMPLETE_EXIT_CODE -- the V2 census stage then falls back to the
# last complete census and says so (IDENTITY_CENSUS_STALE).
FAILED_SET_COOLDOWN = 60.0
# A block that has not cleared answers every page the same way; stop the retry
# pass instead of spending the subprocess timeout on it.
RETRY_PASS_CONSECUTIVE_FAILURE_LIMIT = 3
INCOMPLETE_EXIT_CODE = 3


class CensusHarvestIncomplete(RuntimeError):
    """Some TCG sets could not be fetched; the combined census was not rewritten."""


def http_failure(label: str, status: int) -> str:
    # "forbidden" is the word daily_chain_v2_contract.classify_error reads as
    # AUTH_OR_BLOCKED; "HTTP 5xx/429" reads as TRANSIENT_SOURCE.
    text = f"{label} HTTP {status}"
    if status == 403:
        text += " forbidden"
    return text


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


def fetch_index_html() -> str:
    """GET /universal-pop-report with a bounded backoff; raise a classifiable error."""
    waits = tuple(INDEX_RETRY_WAITS)
    tries = len(waits) + 1
    last = "universal-pop-report no response"
    for attempt in range(1, tries + 1):
        try:
            r = _get(INDEX_URL)
        except Exception as e:  # noqa: BLE001 - network faults retry like a 403
            last = f"universal-pop-report request failed: {type(e).__name__}: {str(e)[:200]}"
        else:
            if r.status_code == 200:
                return r.text
            last = http_failure("universal-pop-report", int(r.status_code))
        if attempt < tries:
            wait = waits[attempt - 1]
            log(f"    {last}; retry {attempt}/{tries - 1} in {wait:.0f}s")
            time.sleep(wait)
    raise RuntimeError(f"{last} after {tries} tries")


def extract_sets_data() -> list[dict]:
    """由 /universal-pop-report 抽 inline setsData。"""
    log("Fetching /universal-pop-report ...")
    html = fetch_index_html()
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
        raise RuntimeError(http_failure("set page", int(r.status_code)))
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

    def _set_path(s: dict) -> Path:
        safe = re.sub(r"[^\w\-]+", "_", s.get("set_name", "unknown"))[:60]
        return DATA_DIR / f"set_{s.get('set_id')}_{safe}.jsonl"

    def _fetch_and_save(s: dict, fetch) -> list[dict]:
        cards = fetch(s.get("set_link"))
        if not cards:
            raise RuntimeError("empty rowData")
        for card in cards:
            card["_set_id"] = s.get("set_id")
            card["_set_name"] = s.get("set_name", "unknown")
            card["_set_link"] = s.get("set_link")
        save_jsonl(cards, _set_path(s))
        return cards

    # Per-set results stay in set order so the combined file does not depend
    # on which sets needed the retry pass.
    results: list[Optional[list[dict]]] = [None] * len(tcg_sets)
    errors: dict[int, str] = {}
    for i, s in enumerate(tcg_sets, 1):
        set_name = s.get("set_name", "unknown")
        set_path = _set_path(s)
        if resume and set_path.exists() and set_path.stat().st_size > 0:
            cards = load_jsonl(set_path)
            results[i - 1] = cards
            log(f"[{i}/{len(tcg_sets)}] {set_name} — resume: {len(cards)} cards from {set_path.name}")
            continue
        log(f"[{i}/{len(tcg_sets)}] {set_name}")
        try:
            cards = _fetch_and_save(s, fetch_set_with_retry)
            results[i - 1] = cards
            log(f"    -> {len(cards)} cards")
            time.sleep(SET_DELAY)
        except Exception as e:
            log(f"    ERROR: {e}")
            errors[i - 1] = str(e)
            time.sleep(SET_DELAY * 2)

    if errors:
        log(f"{len(errors)} set(s) failed; retry pass after {FAILED_SET_COOLDOWN:.0f}s cool-down")
        time.sleep(FAILED_SET_COOLDOWN)
        consecutive = 0
        for index in sorted(errors):
            if consecutive >= RETRY_PASS_CONSECUTIVE_FAILURE_LIMIT:
                log(f"    retry pass stopped after {consecutive} consecutive failures")
                break
            s = tcg_sets[index]
            log(f"[retry {index + 1}/{len(tcg_sets)}] {s.get('set_name', 'unknown')}")
            try:
                cards = _fetch_and_save(s, extract_row_data)
            except Exception as e:
                consecutive += 1
                errors[index] = str(e)
                log(f"    ERROR: {e}")
                time.sleep(SET_DELAY * 2)
            else:
                consecutive = 0
                results[index] = cards
                del errors[index]
                log(f"    -> {len(cards)} cards")
                time.sleep(SET_DELAY)

    failed_sets = [
        {
            "set_id": tcg_sets[index].get("set_id"),
            "set_name": tcg_sets[index].get("set_name", "unknown"),
            "error": errors[index],
        }
        for index in sorted(errors)
    ]
    # Written on every run, empty when clean: gemrate_db_completeness reads a
    # fresh non-empty file as failed_sets:N, and an old file must not survive
    # beside a newer census as if it described it.
    save_jsonl(failed_sets, DATA_DIR / "failed_sets.jsonl")
    if failed_sets:
        log(f"Failed sets: {len(failed_sets)}")
        detail = "; ".join(f"{f['set_name']}: {f['error']}" for f in failed_sets[:3])
        raise CensusHarvestIncomplete(
            f"CENSUS_HARVEST_FAILED: census incomplete: {len(failed_sets)}/{len(tcg_sets)}"
            f" TCG sets failed after retry pass ({detail}); all_cards.jsonl and"
            " psa10_1000_plus.jsonl were not rewritten"
        )

    all_cards = [card for cards in results for card in (cards or [])]
    save_jsonl(all_cards, DATA_DIR / "all_cards.jsonl")
    log(f"Total cards harvested: {len(all_cards)}")
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
        try:
            harvest_all_sets(limit=args.limit, resume=args.resume)
        except CensusHarvestIncomplete as exc:
            # Last stderr line: identity_census_stage copies it into the error.
            print(str(exc), file=sys.stderr, flush=True)
            sys.exit(INCOMPLETE_EXIT_CODE)
    elif args.set_id:
        harvest_single_set(args.set_id)
    elif args.query:
        search_cards(args.query)
    log("Done.")


if __name__ == "__main__":
    main()
