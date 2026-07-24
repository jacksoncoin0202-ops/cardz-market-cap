"""
Grade10.com daily scraper — pulls ALL public data from index.grade10.com + app.grade10.com tRPC APIs.

Usage:
    python grade10_scraper.py                  # full daily run
    python grade10_scraper.py --index-only     # only index stats/charts/constituents
    python grade10_scraper.py --cards-only     # only card details
    python grade10_scraper.py --skip-images    # skip image downloads
    python grade10_scraper.py --dry-run        # enumerate targets, no card requests

Storage layout (under DATA_DIR):
    index/{indexType}/stats.json
    index/{indexType}/summary.json
    index/{indexType}/chart_{range}.json
    index/{indexType}/constituents.json
    cards/{source}/{id}/asset_info.json
    cards/{source}/{id}/apparel_grade_{grade}.json
    cards/{source}/{id}/ebay_{grade_slug}.json
    cards/{source}/{id}/populations.json
    cards/{source}/{id}/summary_{locale}.json
    images/{source}_{id}.{ext}
    _state/last_run.json

Sources:
    snkrdunk  — numeric IDs (530 cards across 3 indexes)
    altxyz    — UUIDs (170 cards; URL path says /ebay/ but API source is "altxyz")
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

INDEX_BASE = "https://index.grade10.com/api/trpc/"
APP_BASE = "https://app.grade10.com/api/trpc/"

INDEX_TYPES = ["ptcg", "ptcg100", "opcg"]
CHART_RANGES = ["1W", "1M", "3M", "6M", "YTD", "ALL"]

GRADE_MAP = {
    -1: "All", 20: "C", 21: "D", 22: "PSA 10", 23: "PSA 9",
    24: "PSA 8 or Below", 25: "BGS 10 BL", 26: "BGS 10 GL",
    27: "BGS 9.5", 28: "BGS 9 or Below", 29: "ARS 10+",
    30: "ARS 10", 31: "ARS 9", 32: "ARS 8 or Below",
}
ACTIVE_GRADES = list(GRADE_MAP.keys())

EBAY_GRADES = [
    "PSA 10", "PSA 9", "BGS 10", "BGS BL",
    "CGC 10", "CGC BL", "ARS 10+", "ARS 10", "Ungraded",
]

LOCALES = ["en", "jp"]

REQUESTS_PER_SECOND = 3
MAX_WORKERS = 8
BATCH_SIZE = 20
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3

DATA_DIR = Path(__file__).parent / "data"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("grade10")

# ---------------------------------------------------------------------------
# tRPC client
# ---------------------------------------------------------------------------

class RateLimiter:
    def __init__(self, rps: float):
        self.min_interval = 1.0 / rps
        self.last = 0.0

    def wait(self):
        now = time.monotonic()
        delta = now - self.last
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta)
        self.last = time.monotonic()


_limiter = RateLimiter(REQUESTS_PER_SECOND)


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Accept": "application/json"})
    return s


def trpc_batch(base: str, calls: list[tuple[str, dict]], session: requests.Session | None = None) -> list:
    """Call multiple procedures in one HTTP request. Returns list of payloads."""
    if not calls:
        return []
    sess = session or _make_session()
    names = ",".join(c[0] for c in calls)
    batch_input = {str(i): {"json": c[1]} for i, c in enumerate(calls)}
    url = (
        f"{base}{names}"
        f"?batch=1&input={urllib.parse.quote(json.dumps(batch_input, separators=(',', ':')))}"
    )
    for attempt in range(1, MAX_RETRIES + 1):
        _limiter.wait()
        try:
            resp = sess.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429:
                wait = 2 ** attempt * 5
                log.warning("429 rate-limited, waiting %ds", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            body = resp.json()
            return [
                item.get("result", {}).get("data", {}).get("json") if "error" not in item else None
                for item in body
            ]
        except Exception as exc:
            log.warning("batch attempt %d failed: %.100s", attempt, exc)
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
    log.error("batch %s failed after %d attempts", names[:80], MAX_RETRIES)
    return [None] * len(calls)


def trpc_get(base: str, procedure: str, payload: dict) -> dict | list | None:
    """Single procedure call (convenience wrapper around batch of 1)."""
    results = trpc_batch(base, [(procedure, payload)])
    return results[0] if results else None


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def save_json(path: Path, data) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_text = json.dumps(data, ensure_ascii=False, indent=2)
    if path.exists():
        if path.read_text(encoding="utf-8") == new_text:
            return False
    path.write_text(new_text, encoding="utf-8")
    return True


def load_json(path: Path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


# ---------------------------------------------------------------------------
# Index scraping
# ---------------------------------------------------------------------------

def scrape_indexes():
    log.info("=== Index data ===")
    sess = _make_session()
    for it in INDEX_TYPES:
        calls = [
            ("cardIndex.getIndexStats", {"indexType": it}),
            ("cardIndex.getSummary", {"indexType": it}),
        ] + [
            ("cardIndex.getChartSeries", {"indexType": it, "range": r})
            for r in CHART_RANGES
        ] + [
            ("cardIndex.getConstituents", {"indexType": it}),
        ]
        results = trpc_batch(INDEX_BASE, calls, session=sess)

        base = DATA_DIR / "index" / it
        labels = ["stats", "summary"] + [f"chart_{r}" for r in CHART_RANGES] + ["constituents"]
        changed = 0
        for label, data in zip(labels, results):
            if data is not None:
                if save_json(base / f"{label}.json", data):
                    changed += 1
        log.info("  %s: %d/%d files updated", it, changed, len(labels))


# ---------------------------------------------------------------------------
# Card enumeration
# ---------------------------------------------------------------------------

def enumerate_cards() -> list[dict]:
    cards = {}
    for it in INDEX_TYPES:
        cons = load_json(DATA_DIR / "index" / it / "constituents.json")
        if not cons or "rows" not in cons:
            continue
        for row in cons["rows"]:
            url = row.get("url", "")
            parts = url.rstrip("/").split("/")
            if len(parts) < 2:
                continue
            path_source, raw_id = parts[-2], parts[-1]
            api_source = {"ebay": "altxyz"}.get(path_source, path_source)
            card_id = int(raw_id) if raw_id.isdigit() else raw_id
            key = f"{api_source}:{card_id}"
            if key not in cards:
                cards[key] = {
                    "source": api_source,
                    "id": card_id,
                    "name": row.get("name"),
                    "imageUrl": row.get("imageUrl"),
                }
    return list(cards.values())


# ---------------------------------------------------------------------------
# Card detail scraping (batched)
# ---------------------------------------------------------------------------

def scrape_card(card: dict, skip_images: bool = False):
    source, cid = card["source"], card["id"]
    base = DATA_DIR / "cards" / source / str(cid)
    updated = 0
    sess = _make_session()

    # -- Phase 1: batch asset_info + populations + summaries (up to 4 calls) --
    phase1_calls = [
        ("price.getAssetInfo", {"id": cid, "source": source}),
        ("price.getGradingPopulations", {"id": cid, "source": source}),
    ]
    if source == "snkrdunk":
        for locale in LOCALES:
            phase1_calls.append(("price.getCardSummary", {
                "id": cid, "source": source, "locale": locale,
            }))

    phase1_results = trpc_batch(APP_BASE, phase1_calls, session=sess)

    info = phase1_results[0]
    if info and save_json(base / "asset_info.json", info):
        updated += 1

    pops = phase1_results[1]
    if pops and save_json(base / "populations.json", pops):
        updated += 1

    if source == "snkrdunk":
        for i, locale in enumerate(LOCALES):
            summary = phase1_results[2 + i]
            if summary and save_json(base / f"summary_{locale}.json", summary):
                updated += 1

    # -- Phase 2: batch all grade queries (14 apparel + 9 ebay = 23, split into chunks of BATCH_SIZE) --
    all_grade_calls = []
    for grade_id in ACTIVE_GRADES:
        all_grade_calls.append(("price.getApparelDetails", {
            "id": cid, "source": source,
            "grade": grade_id,
            "preferences": {"currency": "usd"},
        }))
    for grade_str in EBAY_GRADES:
        all_grade_calls.append(("price.getEBayDetails", {
            "id": cid, "source": source,
            "grade": grade_str,
            "preferences": {"currency": "usd"},
        }))

    for chunk_start in range(0, len(all_grade_calls), BATCH_SIZE):
        chunk = all_grade_calls[chunk_start:chunk_start + BATCH_SIZE]
        results = trpc_batch(APP_BASE, chunk, session=sess)
        for i, data in enumerate(results):
            if data is None:
                continue
            call_idx = chunk_start + i
            if call_idx < len(ACTIVE_GRADES):
                # apparel result
                grade_id = ACTIVE_GRADES[call_idx]
                if data.get("averagePrice") is not None:
                    if save_json(base / f"apparel_grade_{grade_id}.json", data):
                        updated += 1
            else:
                # ebay result
                ebay_idx = call_idx - len(ACTIVE_GRADES)
                grade_str = EBAY_GRADES[ebay_idx]
                if data.get("averagePrice") is not None:
                    slug = grade_str.replace(" ", "_").replace("+", "plus")
                    if save_json(base / f"ebay_{slug}.json", data):
                        updated += 1

    # -- Phase 3: image (optional) --
    if not skip_images:
        img_url = (info or {}).get("image") or card.get("imageUrl")
        if img_url:
            try:
                _limiter.wait()
                img_resp = sess.get(img_url, timeout=REQUEST_TIMEOUT)
                if img_resp.status_code == 200:
                    ext = img_url.rsplit(".", 1)[-1].split("?")[0][:5]
                    img_path = DATA_DIR / "images" / f"{source}_{cid}.{ext}"
                    img_path.parent.mkdir(parents=True, exist_ok=True)
                    if not img_path.exists() or img_path.stat().st_size != len(img_resp.content):
                        img_path.write_bytes(img_resp.content)
                        updated += 1
            except Exception as exc:
                log.debug("image fetch failed for %s:%s: %s", source, cid, exc)

    return updated


def scrape_cards(skip_images: bool = False):
    cards = enumerate_cards()
    if not cards:
        log.warning("No cards found — run --index-only first")
        return
    log.info("=== Card details: %d cards ===", len(cards))

    total_updated = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(scrape_card, card, skip_images): card
            for card in cards
        }
        for i, future in enumerate(as_completed(futures), 1):
            card = futures[future]
            try:
                n = future.result()
                total_updated += n
                if i % 25 == 0 or i == len(cards):
                    log.info("  progress: %d/%d cards, %d files updated", i, len(cards), total_updated)
            except Exception as exc:
                log.error("  card %s:%s failed: %s", card["source"], card["id"], exc)

    log.info("  done: %d files updated across %d cards", total_updated, len(cards))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Grade10.com daily scraper")
    parser.add_argument("--index-only", action="store_true")
    parser.add_argument("--cards-only", action="store_true")
    parser.add_argument("--skip-images", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    start = time.time()
    log.info("Grade10 scraper starting (data dir: %s)", DATA_DIR)

    if args.dry_run:
        scrape_indexes()
        cards = enumerate_cards()
        log.info("DRY RUN: would scrape %d cards", len(cards))
        for c in cards[:5]:
            log.info("  %s:%s %s", c["source"], c["id"], c.get("name", ""))
        if len(cards) > 5:
            log.info("  ... and %d more", len(cards) - 5)
        return

    if not args.cards_only:
        scrape_indexes()

    if not args.index_only:
        scrape_cards(skip_images=args.skip_images)

    state = {
        "lastRun": datetime.now(timezone.utc).isoformat(),
        "elapsedSeconds": round(time.time() - start, 1),
        "cardCount": len(enumerate_cards()),
    }
    save_json(DATA_DIR / "_state" / "last_run.json", state)
    log.info("Done in %.0fs", time.time() - start)


if __name__ == "__main__":
    main()
