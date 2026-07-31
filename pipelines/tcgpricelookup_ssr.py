#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TCGPriceLookup SSR/RSC collector — no API key.

Primary US eBay-derived graded + TCGPlayer raw price source for CARDZ.

Rules (see docs/US_PRICE_SOURCE_RULES.md):
- Do NOT call api.tcgpricelookup.com (requires key; user policy = no API signup).
- Full harvest = one card page load embeds ~1y daily history (observed ~211 days).
- Incremental = re-fetch same slug; merge by date (upsert), not append-duplicates.
- Exact printing: map by slug / tcgplayer_id / set+number; never first catalog hit alone.

CLI:
  python pipelines/tcgpricelookup_ssr.py fetch --slug pokemon-base-set-charizard-004-102-holofoil
  python pipelines/tcgpricelookup_ssr.py harvest --slugs-file path.txt --mode full|incremental
  python pipelines/tcgpricelookup_ssr.py search --q charizard --game pokemon
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from curl_cffi import requests as cr

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data/runtime/private-source-map/tcgpricelookup"
BASE = "https://tcgpricelookup.com"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def session() -> cr.Session:
    s = cr.Session(impersonate="chrome131")
    s.headers.update(
        {
            "User-Agent": UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    return s


def _extract_balanced_object(src: str, start: int) -> str | None:
    """Return JSON object text starting at src[start] == '{'."""

    if start < 0 or start >= len(src) or src[start] != "{":
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, min(len(src), start + 2_000_000)):
        c = src[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    return None


def _find_card_object(rsc: str) -> dict[str, Any] | None:
    # RSC embeds: "card":{...id...slug...prices...}
    for m in re.finditer(r'"card"\s*:\s*\{', rsc):
        obj = _extract_balanced_object(rsc, m.end() - 1)
        if not obj:
            continue
        try:
            data = json.loads(obj)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("slug") and ("prices" in data or data.get("tcgplayer_id")):
            return data
    return None


def _find_history_days(rsc: str) -> list[dict[str, Any]]:
    """Parse {\"date\":\"YYYY-MM-DD\",\"prices\":[...]} day blocks."""

    days: list[dict[str, Any]] = []
    for m in re.finditer(r'\{\s*"date"\s*:\s*"(20\d{2}-\d{2}-\d{2})"\s*,\s*"prices"\s*:\s*\[', rsc):
        # walk back to include opening brace already at m.start()
        start = m.start()
        # find end of this object by balancing from start
        obj = _extract_balanced_object(rsc, start)
        if not obj:
            continue
        try:
            row = json.loads(obj)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and isinstance(row.get("prices"), list):
            days.append(row)
    # dedupe by date keep last
    by_date: dict[str, dict[str, Any]] = {}
    for row in days:
        by_date[str(row["date"])] = row
    return [by_date[k] for k in sorted(by_date)]


def parse_rsc(rsc: str, *, source_url: str | None = None) -> dict[str, Any]:
    card = _find_card_object(rsc)
    history = _find_history_days(rsc)
    if not card:
        return {
            "ok": False,
            "error": "card_object_not_found",
            "sourceUrl": source_url,
            "historyDays": len(history),
        }

    prices = card.get("prices") if isinstance(card.get("prices"), Mapping) else {}
    graded = prices.get("graded") if isinstance(prices, Mapping) else {}
    raw = prices.get("raw") if isinstance(prices, Mapping) else {}

    psa10 = None
    if isinstance(graded, Mapping):
        psa = graded.get("psa") if isinstance(graded.get("psa"), Mapping) else {}
        if isinstance(psa, Mapping):
            node = psa.get("10") or psa.get(10)
            if isinstance(node, Mapping):
                psa10 = node

    nm = None
    if isinstance(raw, Mapping) and isinstance(raw.get("near_mint"), Mapping):
        nm = raw.get("near_mint")

    # history: extract PSA10 ebay avg_1d series
    psa10_history: list[dict[str, Any]] = []
    for day in history:
        date = day.get("date")
        for item in day.get("prices") or []:
            if not isinstance(item, Mapping):
                continue
            if (
                str(item.get("source") or "").casefold() == "ebay"
                and str(item.get("grader") or "").casefold() == "psa"
                and str(item.get("grade") or "") == "10"
            ):
                psa10_history.append(
                    {
                        "date": date,
                        "avg_1d": item.get("avg_1d"),
                        "avg_7d": item.get("avg_7d"),
                        "avg_30d": item.get("avg_30d"),
                        "price_low": item.get("price_low"),
                        "price_high": item.get("price_high"),
                    }
                )
                break

    set_obj = card.get("set") if isinstance(card.get("set"), Mapping) else {}
    game_obj = card.get("game") if isinstance(card.get("game"), Mapping) else {}

    return {
        "ok": True,
        "sourceCode": "tcgpricelookup",
        "transport": "ssr_rsc",
        "sourceUrl": source_url,
        "fetchedAt": utc_now(),
        "id": card.get("id"),
        "slug": card.get("slug"),
        "name": card.get("name"),
        "number": card.get("number"),
        "rarity": card.get("rarity"),
        "variant": card.get("variant"),
        "imageUrl": card.get("image_url"),
        "tcgplayerId": str(card.get("tcgplayer_id") or "") or None,
        "updatedAt": card.get("updated_at"),
        "set": {"id": set_obj.get("id"), "slug": set_obj.get("slug"), "name": set_obj.get("name")},
        "game": {"id": game_obj.get("id"), "slug": game_obj.get("slug"), "name": game_obj.get("name")},
        "prices": {
            "raw": raw,
            "graded": graded,
        },
        "psa10": {
            "current": psa10,
            "ebayAvg1d": (psa10 or {}).get("ebay", {}).get("avg_1d")
            if isinstance(psa10, Mapping) and isinstance(psa10.get("ebay"), Mapping)
            else (
                (psa10 or {}).get("avg_1d") if isinstance(psa10, Mapping) else None
            ),
            "history": psa10_history,
            "historyDays": len(psa10_history),
        },
        "nearMint": nm,
        "historyDaysEmbedded": len(history),
        "historyDateMin": history[0]["date"] if history else None,
        "historyDateMax": history[-1]["date"] if history else None,
        "history": history,
    }


def fetch_card(slug: str, *, sess: cr.Session | None = None) -> dict[str, Any]:
    slug = slug.strip().lstrip("/")
    if slug.startswith("card/"):
        slug = slug[5:]
    url = f"{BASE}/card/{slug}"
    s = sess or session()
    r = s.get(
        url,
        headers={"RSC": "1", "Accept": "text/x-component,*/*", "Referer": f"{BASE}/"},
        timeout=60,
    )
    if r.status_code != 200:
        return {
            "ok": False,
            "error": f"http_{r.status_code}",
            "slug": slug,
            "sourceUrl": url,
            "fetchedAt": utc_now(),
        }
    parsed = parse_rsc(r.text or "", source_url=url)
    parsed["slug"] = parsed.get("slug") or slug
    parsed["httpStatus"] = r.status_code
    return parsed


def search_catalog(query: str, *, game: str = "pokemon", sess: cr.Session | None = None) -> list[dict[str, Any]]:
    s = sess or session()
    url = f"{BASE}/catalog"
    r = s.get(url, params={"game": game, "q": query}, timeout=60)
    html = r.text or ""
    links = sorted(set(re.findall(r'href="(/card/[^"]+)"', html)))
    rows = []
    for path in links:
        slug = path.split("/card/", 1)[-1]
        rows.append({"slug": slug, "path": path})
    return rows


def merge_incremental(previous: Mapping[str, Any] | None, fresh: Mapping[str, Any]) -> dict[str, Any]:
    """Upsert history by date; prefer fresh current prices."""

    if not fresh.get("ok"):
        return dict(fresh)
    if not previous or not previous.get("ok"):
        out = dict(fresh)
        out["mergeMode"] = "full_replace"
        return out

    prev_hist = {
        str(row.get("date")): row
        for row in (previous.get("history") or [])
        if isinstance(row, Mapping) and row.get("date")
    }
    for row in fresh.get("history") or []:
        if isinstance(row, Mapping) and row.get("date"):
            prev_hist[str(row["date"])] = row
    merged_days = [prev_hist[k] for k in sorted(prev_hist)]

    # recompute psa10 history from merged days
    psa10_history: list[dict[str, Any]] = []
    for day in merged_days:
        date = day.get("date")
        for item in day.get("prices") or []:
            if not isinstance(item, Mapping):
                continue
            if (
                str(item.get("source") or "").casefold() == "ebay"
                and str(item.get("grader") or "").casefold() == "psa"
                and str(item.get("grade") or "") == "10"
            ):
                psa10_history.append(
                    {
                        "date": date,
                        "avg_1d": item.get("avg_1d"),
                        "avg_7d": item.get("avg_7d"),
                        "avg_30d": item.get("avg_30d"),
                        "price_low": item.get("price_low"),
                        "price_high": item.get("price_high"),
                    }
                )
                break

    out = dict(fresh)
    out["history"] = merged_days
    out["historyDaysEmbedded"] = len(merged_days)
    out["historyDateMin"] = merged_days[0]["date"] if merged_days else None
    out["historyDateMax"] = merged_days[-1]["date"] if merged_days else None
    psa10 = dict(out.get("psa10") or {})
    psa10["history"] = psa10_history
    psa10["historyDays"] = len(psa10_history)
    out["psa10"] = psa10
    out["mergeMode"] = "incremental_upsert"
    out["previousFetchedAt"] = previous.get("fetchedAt")
    return out


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def card_store_path(root: Path, slug: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", slug)[:180]
    return root / "cards" / f"{safe}.json"


def harvest(
    slugs: Iterable[str],
    *,
    out_dir: Path,
    mode: str = "full",
    delay: float = 0.35,
    workers: int = 6,
) -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    out_dir.mkdir(parents=True, exist_ok=True)
    clean = [s.strip() for s in slugs if s and s.strip() and not s.strip().startswith("#")]
    # unique preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for s in clean:
        if s not in seen:
            seen.add(s)
            ordered.append(s)

    def _one(slug: str) -> dict[str, Any]:
        path = card_store_path(out_dir, slug)
        previous = None
        if mode == "incremental" and path.is_file():
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous = None
        # skip re-full if already ok full file and mode full (resume full volume)
        if mode == "full" and path.is_file():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing.get("ok") and (existing.get("historyDaysEmbedded") or 0) > 0:
                    return {
                        "slug": slug,
                        "ok": True,
                        "path": str(path),
                        "historyDays": existing.get("historyDaysEmbedded"),
                        "psa10HistoryDays": (existing.get("psa10") or {}).get("historyDays"),
                        "psa10Ebay": (existing.get("psa10") or {}).get("ebayAvg1d"),
                        "historyMin": existing.get("historyDateMin"),
                        "historyMax": existing.get("historyDateMax"),
                        "mergeMode": "skipped_existing_full",
                        "error": None,
                    }
            except (OSError, json.JSONDecodeError):
                pass
        fresh = fetch_card(slug)
        if mode == "incremental":
            row = merge_incremental(previous, fresh)
        else:
            row = dict(fresh)
            row["mergeMode"] = "full"
        atomic_write_json(path, row)
        time.sleep(delay)
        return {
            "slug": slug,
            "ok": bool(row.get("ok")),
            "path": str(path),
            "historyDays": row.get("historyDaysEmbedded"),
            "psa10HistoryDays": (row.get("psa10") or {}).get("historyDays"),
            "psa10Ebay": (row.get("psa10") or {}).get("ebayAvg1d"),
            "historyMin": row.get("historyDateMin"),
            "historyMax": row.get("historyDateMax"),
            "mergeMode": row.get("mergeMode"),
            "error": row.get("error"),
        }

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(_one, slug): slug for slug in ordered}
        done = 0
        for fut in as_completed(futs):
            row = fut.result()
            results.append(row)
            done += 1
            if done % 20 == 0 or done == len(ordered):
                print(
                    f"[tpl-ssr] {done}/{len(ordered)} ok={sum(1 for r in results if r.get('ok'))} "
                    f"last={row.get('slug')} hist={row.get('historyDays')}",
                    flush=True,
                )

    manifest = {
        "sourceCode": "tcgpricelookup",
        "mode": mode,
        "fetchedAt": utc_now(),
        "cards": len(results),
        "ok": sum(1 for r in results if r.get("ok")),
        "workers": workers,
        "results": results,
    }
    atomic_write_json(out_dir / f"manifest_{mode}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json", manifest)
    atomic_write_json(out_dir / "latest_manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="TCGPriceLookup SSR collector (no API key)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser("fetch", help="Fetch one card slug")
    p_fetch.add_argument("--slug", required=True)
    p_fetch.add_argument("--out", type=Path)

    p_search = sub.add_parser("search", help="Catalog search → slugs")
    p_search.add_argument("--q", required=True)
    p_search.add_argument("--game", default="pokemon")
    p_search.add_argument("--out", type=Path)

    p_harvest = sub.add_parser("harvest", help="Batch full or incremental harvest")
    p_harvest.add_argument("--slugs-file", type=Path)
    p_harvest.add_argument("--slug", action="append", default=[])
    p_harvest.add_argument("--mode", choices=("full", "incremental"), default="full")
    p_harvest.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p_harvest.add_argument("--delay", type=float, default=0.8)

    p_canary = sub.add_parser("canary", help="Full then incremental canary on one slug")
    p_canary.add_argument("--slug", default="pokemon-base-set-charizard-004-102-holofoil")
    p_canary.add_argument("--out-dir", type=Path, default=DEFAULT_OUT / "canary")

    args = parser.parse_args()

    if args.cmd == "fetch":
        row = fetch_card(args.slug)
        text = json.dumps(row, ensure_ascii=False, indent=2)
        if args.out:
            atomic_write_json(args.out, row)
            print(json.dumps({"ok": row.get("ok"), "out": str(args.out), "historyDays": row.get("historyDaysEmbedded")}, sort_keys=True))
        else:
            # compact summary
            print(
                json.dumps(
                    {
                        "ok": row.get("ok"),
                        "slug": row.get("slug"),
                        "number": row.get("number"),
                        "tcgplayerId": row.get("tcgplayerId"),
                        "historyDaysEmbedded": row.get("historyDaysEmbedded"),
                        "historyDateMin": row.get("historyDateMin"),
                        "historyDateMax": row.get("historyDateMax"),
                        "psa10": row.get("psa10"),
                        "nearMintTcgplayer": ((row.get("nearMint") or {}).get("tcgplayer") if isinstance(row.get("nearMint"), Mapping) else None),
                        "error": row.get("error"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return 0 if row.get("ok") else 1

    if args.cmd == "search":
        rows = search_catalog(args.q, game=args.game)
        if args.out:
            atomic_write_json(args.out, rows)
        print(json.dumps({"query": args.q, "game": args.game, "count": len(rows), "slugs": [r["slug"] for r in rows[:30]]}, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "harvest":
        slugs = list(args.slug)
        if args.slugs_file:
            slugs.extend(args.slugs_file.read_text(encoding="utf-8").splitlines())
        if not slugs:
            raise SystemExit("provide --slug and/or --slugs-file")
        manifest = harvest(slugs, out_dir=args.out_dir.resolve(), mode=args.mode, delay=args.delay)
        print(json.dumps({k: manifest[k] for k in ("mode", "cards", "ok", "fetchedAt")}, sort_keys=True))
        return 0 if manifest.get("ok") else 1

    if args.cmd == "canary":
        out = args.out_dir.resolve()
        # wipe canary card store for clean full
        store = card_store_path(out, args.slug)
        if store.is_file():
            store.unlink()
        full = harvest([args.slug], out_dir=out, mode="full", delay=0.2)
        full_row = json.loads(store.read_text(encoding="utf-8"))
        # simulate second day incremental
        inc = harvest([args.slug], out_dir=out, mode="incremental", delay=0.2)
        inc_row = json.loads(store.read_text(encoding="utf-8"))
        report = {
            "slug": args.slug,
            "full": {
                "ok": full_row.get("ok"),
                "historyDays": full_row.get("historyDaysEmbedded"),
                "psa10HistoryDays": (full_row.get("psa10") or {}).get("historyDays"),
                "dateMin": full_row.get("historyDateMin"),
                "dateMax": full_row.get("historyDateMax"),
                "psa10Ebay": (full_row.get("psa10") or {}).get("ebayAvg1d"),
            },
            "incremental": {
                "ok": inc_row.get("ok"),
                "historyDays": inc_row.get("historyDaysEmbedded"),
                "psa10HistoryDays": (inc_row.get("psa10") or {}).get("historyDays"),
                "dateMin": inc_row.get("historyDateMin"),
                "dateMax": inc_row.get("historyDateMax"),
                "mergeMode": inc_row.get("mergeMode"),
                "psa10Ebay": (inc_row.get("psa10") or {}).get("ebayAvg1d"),
            },
            "sameShape": (
                full_row.get("ok")
                and inc_row.get("ok")
                and full_row.get("historyDaysEmbedded") == inc_row.get("historyDaysEmbedded")
                and set(str(d.get("date")) for d in (full_row.get("history") or []))
                == set(str(d.get("date")) for d in (inc_row.get("history") or []))
            ),
            "conclusion": None,
        }
        if report["sameShape"] and report["full"]["historyDays"]:
            report["conclusion"] = (
                "FULL page load already embeds the complete SSR history window "
                f"(~{report['full']['historyDays']} days). INCREMENTAL re-fetch yields the same "
                "history set (upsert by date); it does NOT unlock older-than-window history. "
                "No paid API used."
            )
        else:
            report["conclusion"] = "canary mismatch — inspect stored JSON"
        atomic_write_json(out / "canary_report.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("sameShape") else 1

    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
