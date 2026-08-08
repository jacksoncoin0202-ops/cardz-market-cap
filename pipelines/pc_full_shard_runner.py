#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PriceCharting full-universe shard runner using the proven CDP path.

The runner keeps CDP concurrency at one, resumes verified successes, and writes
every unresolved variant into the private failure ledger for Agent review and
bounded retry.  It is the tracked replacement for the historical executable
under ``temp/``.

036 Gate 1: this runner is a COLLECTOR only.  It never writes
catalog_source_identity or any identity evidence to the DB (the legacy
auto-bind was removed).  For every product page it can prove, it appends a
provider-native evidence manifest row (``evidence_manifest_shard_*.jsonl``);
identity resolution and binding happen downstream in the 036
identity-resolve stage under rebuild credentials.  DB access here is
read-only (resume skip + canonical language hydration).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from html import unescape
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "pipelines"))

from failure_ledger import record_failure, record_resolution  # noqa: E402
from pricecharting_cf_session import _is_cf, cmd_fetch  # noqa: E402
from qualified_pool_operator import db  # noqa: E402

OUT_ROOT = ROOT / "data/runtime/private-reports/fill/PC-FULL-900"
HTML_DIR = ROOT / "data/private/pricecharting_session/html/full900"
MAP_DIR = ROOT / "data/runtime/private-source-map"
LOCK = OUT_ROOT / "cdp.lock"
SCRIPT = Path(__file__).resolve()
SOURCE = "pricecharting"
STAGE = "full_universe_collect"
SUCCESS_STATUSES = {"attached", "attached_no_sold", "skip_have_pc"}
PC_BULK_FETCH_TIMEOUT_S = 20
PRICECHARTING_SUPPORTED_LANGUAGES = {
    "pokemon": frozenset({"en", "ja"}),
    "one-piece": frozenset({"en", "ja"}),
}
HTML_DIR.mkdir(parents=True, exist_ok=True)
OUT_ROOT.mkdir(parents=True, exist_ok=True)


class FileLock:
    """Windows-friendly exclusive lock via O_EXCL create."""

    def __init__(self, path: Path, timeout_s: float = 600.0, stale_s: float = 90.0):
        self.path = path
        self.timeout_s = timeout_s
        self.stale_s = stale_s
        self.fd = None

    def _lock_holder_dead(self) -> bool:
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace").strip()
            pid = int(text.split()[0] if text else "")
        except Exception:
            return False
        if pid <= 0:
            return False
        try:
            import ctypes

            process_query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                process_query_limited_information, False, pid
            )
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return False
            return True
        except Exception:
            return False

    def __enter__(self):
        deadline = time.time() + self.timeout_s
        while True:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, f"{os.getpid()} {time.time()}\n".encode())
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                    if age > self.stale_s or self._lock_holder_dead():
                        self.path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.time() >= deadline:
                    raise TimeoutError(f"CDP lock timeout: {self.path}")
                time.sleep(0.4)

    def __exit__(self, *exc):
        try:
            if self.fd is not None:
                os.close(self.fd)
        finally:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass


def norm(value: str) -> str:
    value = re.sub(r"\(.*?\)", " ", (value or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def name_ok(expected: str, title: str) -> bool:
    expected_norm, title_norm = norm(expected), norm(title)
    if not expected_norm or not title_norm:
        return False
    if expected_norm in title_norm:
        return True
    core = expected_norm.split()[0]
    if core and len(core) >= 3 and core in title_norm:
        return True
    return SequenceMatcher(None, expected_norm, title_norm).ratio() >= 0.42


def page_identity_ok(work: dict[str, Any], title: str, url: str) -> bool:
    """Fail closed when a PC page cannot prove the requested printing."""

    page = f"{title} {url}".casefold()
    page_flat = re.sub(r"[^a-z0-9]", "", page)
    collector = re.sub(
        r"[^a-z0-9]",
        "",
        str(work.get("num") or "").split("/")[0].casefold(),
    )
    tcg = str(work.get("tcg") or "").strip().casefold()
    if tcg == "one-piece" and (not collector or collector not in page_flat):
        return False

    language = str(work.get("language") or "").strip().casefold()
    is_japanese_page = "japanese" in page or "/japanese-" in page
    if language in {"en", "english"} and is_japanese_page:
        return False
    if language in {"ja", "jp", "japanese"} and not is_japanese_page:
        return False

    # Set-family aliases are not interchangeable.  These two high-collision
    # Pokémon families have reused collector numbers across Carddass, English
    # 151, Japanese 151, Celebrations, and POP Series.  Language alone cannot
    # distinguish the Japanese products, so require the exact PC collection
    # route before an identity can become durable.
    set_flat = re.sub(r"[^a-z0-9]", "", str(work.get("set") or "").casefold())
    route = unescape(url or "").casefold()
    if tcg == "pokemon" and (
        "sv2a" in set_flat
        or ("japanese" in set_flat and "151" in set_flat)
    ):
        if "/pokemon-japanese-scarlet-&-violet-151/" not in route:
            return False
    if tcg == "pokemon" and "celebrations" in set_flat:
        if "/pokemon-celebrations/" not in route:
            return False
    return True


def is_product_page(title: str, html: str, url: str) -> bool:
    if _is_cf(title, html):
        return False
    if "search-products" in (url or ""):
        return False
    if " List" in title and "Pokemon" not in title and "One Piece" not in title:
        return False
    if len(html) < 80000:
        return False
    has_game = ("Pokemon" in title) or ("Pokémon" in title) or ("One Piece" in title)
    return has_game or "Prices |" in title


def read_saved_snapshot(path: Path, requested_url: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    html = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"<title>([^<]+)</title>", html, re.I)
    title = match.group(1).strip() if match else ""
    if not html.strip() or _is_cf(title, html):
        return None
    final_url = requested_url
    canonical = re.search(
        r'canonical" href="(https://www\.pricecharting\.com/game/[^"]+)"',
        html,
        re.I,
    )
    if canonical:
        final_url = unescape(canonical.group(1))
    return {
        "html": html,
        "title": title,
        "final_url": final_url,
        "path": path,
    }


def load_or_fetch_snapshot(
    url: str,
    html_path: Path,
    *,
    refetch: bool = False,
) -> tuple[dict[str, Any] | None, bool]:
    """Reuse a saved raw page; touch the provider only when no usable raw exists."""

    if not refetch:
        saved = read_saved_snapshot(html_path, url)
        if saved is not None:
            return saved, True
    fetch_locked(url, html_path, timeout_s=PC_BULK_FETCH_TIMEOUT_S)
    return read_saved_snapshot(html_path, url), False


def parse_html(html_path: Path, url: str) -> dict[str, Any]:
    output = html_path.with_suffix(".json")
    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "pipelines/pricecharting_page_parse.py",
            str(html_path),
            "--out",
            str(output),
            "--psa10-only",
            "--url",
            url,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        return {"ok": False, "error": completed.stderr[-500:]}
    if output.exists():
        return json.loads(output.read_text(encoding="utf-8"))
    return {"ok": False, "error": "parser produced no output"}


def extract_product_links(html: str) -> list[str]:
    links = re.findall(
        r'href="(https://www\.pricecharting\.com/game/(?:pokemon|one-piece)[^"]+)"',
        html,
    )
    output: list[str] = []
    seen: set[str] = set()
    for raw_url in links:
        url = unescape(raw_url)
        if url not in seen:
            seen.add(url)
            output.append(url)
    return output


def pick_resolve_url(links: list[str], name: str, number: str, set_name: str) -> str | None:
    """Score product links from a search page; prefer name+number+set overlap."""

    tokens = [token for token in norm(name).split() if len(token) >= 3]
    core = tokens[0] if tokens else ""
    number_norm = re.sub(r"[^a-z0-9]", "", str(number or "").split("/")[0].lower())
    set_tokens = [token for token in norm(set_name).split() if len(token) > 3]
    scored: list[tuple[int, str]] = []
    for url in links:
        slug = url.lower()
        flat = re.sub(r"[^a-z0-9]", "", slug)
        score = 0
        if core and core in flat:
            score += 5
        for token in tokens[1:4]:
            if token in flat:
                score += 2
        if number_norm and number_norm in flat:
            score += 6
        for token in set_tokens[:6]:
            if token in slug:
                score += 1
        if "1st-edition" in slug or "shadowless" in slug:
            score -= 1
        if score >= 5:
            scored.append((score, url))
    if not scored:
        for url in links:
            if core and core in re.sub(r"[^a-z0-9]", "", url.lower()):
                scored.append((1, url))
    if not scored:
        return None
    scored.sort(key=lambda row: row[0], reverse=True)
    return scored[0][1]


def ensure_cdp_or_raise(port: int = 9222) -> None:
    """Never fetch without live CDP; revive through the registered helper."""

    import urllib.request

    def live() -> bool:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2)
            return True
        except Exception:
            return False

    if live():
        return
    try:
        if LOCK.exists():
            LOCK.unlink(missing_ok=True)
    except OSError:
        pass
    ensure_script = ROOT / "scripts" / "ensure_chrome_cdp.ps1"
    if ensure_script.is_file():
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-File",
                str(ensure_script),
                "-Port",
                str(port),
            ],
            cwd=str(ROOT),
            timeout=60,
            check=False,
        )
    if not live():
        raise RuntimeError(
            f"CDP_DOWN port={port} — ensure_chrome_cdp.ps1 failed; cannot PC-fetch"
        )


def fetch_locked(url: str, html_path: Path, timeout_s: int = 90) -> int:
    ensure_cdp_or_raise(9222)
    with FileLock(LOCK, timeout_s=600, stale_s=90):
        return cmd_fetch(
            url,
            html_path,
            headless=True,
            timeout_s=timeout_s,
            prefer_cdp=True,
        )


def process_one(work: dict[str, Any], force: bool = False) -> dict[str, Any]:
    variant_id = int(work["vid"])
    item: dict[str, Any] = {
        "vid": variant_id,
        "rank": work.get("rank"),
        "name": work.get("name"),
        "set": work.get("set"),
        "num": work.get("num"),
        "tcg": work.get("tcg"),
        "language": work.get("language"),
        "have_pc_before": work.get("have_pc"),
    }
    if work.get("_language_blocked_reason"):
        item["status"] = f"language_blocked:{work['_language_blocked_reason']}"
        return item
    if work.get("_exact_pc_bound") and not force:
        existing = list(HTML_DIR.glob(f"{variant_id}_*.html"))
        large = [
            path
            for path in existing
            if path.stat().st_size > 200000 and "search" not in path.name
        ]
        if large:
            item["status"] = "skip_have_pc"
            item["product_id"] = work.get("pc_id")
            return item

    saved_by_url: dict[str, Path] = {}
    saved_urls: list[str] = []
    if not force:
        for saved_path in sorted(
            HTML_DIR.glob(f"{variant_id}_*.html"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ):
            saved = read_saved_snapshot(saved_path, "")
            if saved is None:
                continue
            saved_url = str(saved["final_url"] or "")
            if (
                saved_url
                and is_product_page(saved["title"], saved["html"], saved_url)
                and name_ok(work.get("name") or "", saved["title"])
                and page_identity_ok(work, saved["title"], saved_url)
            ):
                saved_by_url.setdefault(saved_url, saved_path)
                saved_urls.append(saved_url)

    urls = list(dict.fromkeys([*saved_urls, *(work.get("urls") or [])]))
    for url in urls[:4]:
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", url.rstrip("/").split("/")[-1])[:80]
        html_path = saved_by_url.get(url, HTML_DIR / f"{variant_id}_{slug}.html")
        try:
            snapshot, reused = load_or_fetch_snapshot(
                url,
                html_path,
                refetch=force,
            )
        except Exception as error:
            item["status"] = f"fetch_err:{error}"
            item["url_final"] = url
            continue
        if snapshot is None:
            item["status"] = "fetch_no_output"
            item["url_final"] = url
            continue
        html = str(snapshot["html"])
        title = str(snapshot["title"])
        final_url = str(snapshot["final_url"])
        if reused:
            item["raw_snapshot_reused"] = True

        if (
            not is_product_page(title, html, final_url)
            or not name_ok(work.get("name") or "", title)
            or not page_identity_ok(work, title, final_url)
        ):
            best = pick_resolve_url(
                extract_product_links(html),
                work.get("name") or "",
                work.get("num") or "",
                work.get("set") or "",
            )
            if not best:
                item.update(
                    {
                        "status": "search_unresolved",
                        "title": title,
                        "url_final": final_url,
                    }
                )
                continue
            second_slug = re.sub(
                r"[^a-zA-Z0-9._-]+",
                "-",
                best.rstrip("/").split("/")[-1],
            )[:80]
            html_path = HTML_DIR / f"{variant_id}_{second_slug}_r.html"
            try:
                snapshot, reused = load_or_fetch_snapshot(
                    best,
                    html_path,
                    refetch=force,
                )
            except Exception as error:
                item["status"] = f"resolve_fetch_err:{error}"
                item["url_final"] = best
                continue
            if snapshot is None:
                item["status"] = "resolve_fetch_no_output"
                item["url_final"] = best
                continue
            html = str(snapshot["html"])
            title = str(snapshot["title"])
            final_url = str(snapshot["final_url"])
            if reused:
                item["raw_snapshot_reused"] = True
            if (
                not is_product_page(title, html, final_url)
                or not name_ok(work.get("name") or "", title)
                or not page_identity_ok(work, title, final_url)
            ):
                item.update(
                    {
                        "status": "resolve_mismatch",
                        "title": title,
                        "url_final": final_url,
                    }
                )
                continue

        data = parse_html(html_path, final_url)
        if not data.get("ok"):
            item.update(
                {
                    "status": "parse_fail",
                    "title": title,
                    "url_final": final_url,
                    "parse_error": data.get("error"),
                }
            )
            continue
        product_id = str((data.get("product") or {}).get("id") or "")
        sold = (data.get("psa10") or {}).get("completed_sales") or {}
        rows = sold.get("rows") or []
        prices = [
            row.get("price_usd")
            for row in rows
            if row.get("price_usd") is not None
        ]
        if not product_id:
            item.update(
                {
                    "status": "no_product_id",
                    "title": title,
                    "url_final": final_url,
                }
            )
            continue

        # 036 Gate 1: no DB identity write here.  Emit provider-native evidence
        # only; the identity-resolve stage decides binding downstream.
        html_relative = str(html_path).replace("\\", "/")
        if "data/" in html_relative:
            html_relative = html_relative[html_relative.index("data/") :]
        parse_json_relative = str(html_path.with_suffix(".json")).replace("\\", "/")
        if "data/" in parse_json_relative:
            parse_json_relative = parse_json_relative[parse_json_relative.index("data/") :]
        history = (data.get("psa10") or {}).get("history") or {}
        item.update(
            {
                "status": "attached" if rows else "attached_no_sold",
                "product_id": product_id,
                "title": title,
                "url_final": final_url,
                "sold_count": sold.get("count") or len(rows),
                "price_range": [min(prices), max(prices)] if prices else None,
                "html": html_relative,
                "html_sha256": hashlib.sha256(html_path.read_bytes()).hexdigest(),
                "parse_json": parse_json_relative,
                "psa10_history_points": history.get("points"),
                "psa10_history_last_usd": history.get("last_usd"),
                "ebay_items": [
                    str(row.get("ebay_itm")) for row in rows if row.get("ebay_itm")
                ],
            }
        )
        return item

    if "status" not in item:
        item["status"] = "all_urls_failed"
    return item


def _reason_code(status: str) -> str:
    return (status.split(":", 1)[0] or "unknown_failure").strip().lower()


def _occurred_at(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def record_item_outcome(
    item: dict[str, Any],
    work: dict[str, Any] | None = None,
    *,
    run_id: str | None = None,
    occurred_at: datetime | None = None,
) -> Path | None:
    work = work or {}
    variant_id = int(item["vid"])
    status = str(item.get("status") or "unknown_failure")
    evidence = [path for path in [item.get("html")] if path]
    context = {
        "rank": work.get("rank", item.get("rank")),
        "name": work.get("name", item.get("name")),
        "set": work.get("set", item.get("set")),
        "collectorNumber": work.get("num", item.get("num")),
        "tcg": work.get("tcg", item.get("tcg")),
        "language": work.get("language", item.get("language")),
        "title": item.get("title"),
        "productId": item.get("product_id"),
        "shard": item.get("shard"),
        "urls": list(work.get("urls") or [])[:4],
        "searchTerms": [
            value
            for value in [
                work.get("tcg", item.get("tcg")),
                work.get("name", item.get("name")),
                work.get("set", item.get("set")),
                work.get("num", item.get("num")),
                "PSA 10",
                "PriceCharting",
            ]
            if value
        ],
    }
    if status in SUCCESS_STATUSES:
        return record_resolution(
            source=SOURCE,
            stage=STAGE,
            script=SCRIPT,
            item_key=variant_id,
            run_id=run_id,
            resolution=status,
            context=context,
            evidence_paths=evidence,
            occurred_at=occurred_at,
        )
    transient = (
        status.startswith("fetch_err:")
        or status.startswith("resolve_fetch_err:")
        or status.startswith("exception:")
        or "CDP_DOWN" in status
        or "lock timeout" in status
    )
    return record_failure(
        source=SOURCE,
        stage=STAGE,
        script=SCRIPT,
        item_key=variant_id,
        reason_code=_reason_code(status),
        message=status,
        retryable=True,
        run_id=run_id,
        url=item.get("url_final") or next(iter(work.get("urls") or []), None),
        context=context,
        evidence_paths=evidence,
        next_action="retry" if transient else "agent_review",
        occurred_at=occurred_at,
    )


def _load_work_by_variant() -> dict[int, dict[str, Any]]:
    work: dict[int, dict[str, Any]] = {}
    for path in sorted(OUT_ROOT.glob("shard_*.json")):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("vid") is not None:
                work[int(row["vid"])] = row
    return work


def load_exact_pricecharting_variants() -> set[int]:
    with db() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT DISTINCT
                COALESCE(alias.canonical_variant_id, identity.variant_id)
                    AS variant_id
            FROM catalog_source_identity AS identity
            LEFT JOIN catalog_variant_alias AS alias
              ON alias.duplicate_variant_id = identity.variant_id
            WHERE identity.source_code='pricecharting'
              AND identity.match_status='exact'
            """
        )
        return {int(row["variant_id"]) for row in cursor.fetchall()}


def verified_done_variants(
    work: list[dict[str, Any]],
    exact_pricecharting_variants: set[int],
) -> set[int]:
    """Only a current exact DB binding makes a card complete."""

    return {
        int(row["vid"])
        for row in work
        if int(row["vid"]) in exact_pricecharting_variants
    }


def select_target_work(
    work: list[dict[str, Any]],
    *,
    variant_id: int | None,
    candidate_urls: list[str],
) -> list[dict[str, Any]]:
    selected = work
    if variant_id is not None:
        selected = [row for row in selected if int(row["vid"]) == variant_id]
        if not selected:
            raise ValueError(f"variant {variant_id} is not present in this shard")
    if candidate_urls:
        if variant_id is None or len(selected) != 1:
            raise ValueError("--candidate-url requires exactly one --variant-id")
        row = dict(selected[0])
        row["urls"] = list(dict.fromkeys([*candidate_urls, *(row.get("urls") or [])]))
        selected = [row]
    return selected


def apply_catalog_languages(
    work: list[dict[str, Any]],
    catalog_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Overwrite shard language from canonical catalog, or block before browse."""

    catalog_by_requested = {
        int(row["requested_variant_id"]): row
        for row in catalog_rows
        if row.get("requested_variant_id") is not None
    }
    hydrated: list[dict[str, Any]] = []
    for raw in work:
        row = dict(raw)
        variant_id = int(row["vid"])
        catalog = catalog_by_requested.get(variant_id)
        if catalog is None:
            row["_language_blocked_reason"] = "catalog_variant_missing"
            hydrated.append(row)
            continue
        tcg = str(catalog.get("tcg_code") or "").strip().casefold()
        language = str(catalog.get("card_language") or "").strip().casefold()
        supported = PRICECHARTING_SUPPORTED_LANGUAGES.get(tcg, frozenset())
        if not language:
            row["_language_blocked_reason"] = "catalog_language_missing"
        elif language not in supported:
            row["_language_blocked_reason"] = "catalog_language_unsupported"
        else:
            # Never infer this from a set name or preserve stale shard state.
            row["tcg"] = tcg
            row["language"] = language
        hydrated.append(row)
    return hydrated


def hydrate_selected_work_languages(work: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Read canonical aliases/language once before any PriceCharting fetch."""

    variant_ids = sorted({int(row["vid"]) for row in work})
    if not variant_ids:
        return []
    placeholders = ",".join(["%s"] * len(variant_ids))
    with db() as connection:
        cursor = connection.cursor()
        cursor.execute(
            f"""
            SELECT requested.id AS requested_variant_id,
                   COALESCE(alias.canonical_variant_id, requested.id) AS canonical_variant_id,
                   canonical.tcg_code, canonical.card_language
            FROM catalog_variant AS requested
            LEFT JOIN catalog_variant_alias AS alias
              ON alias.duplicate_variant_id=requested.id
            JOIN catalog_variant AS canonical
              ON canonical.id=COALESCE(alias.canonical_variant_id, requested.id)
            WHERE requested.id IN ({placeholders})
            """,
            variant_ids,
        )
        rows = list(cursor.fetchall())
    return apply_catalog_languages(work, rows)


def import_existing_results() -> dict[str, int]:
    work_by_variant = _load_work_by_variant()
    imported = invalid = 0
    for path in sorted(OUT_ROOT.glob("results_shard_*.jsonl")):
        fallback_stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                variant_id = int(item["vid"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                invalid += 1
                continue
            record_item_outcome(
                item,
                work_by_variant.get(variant_id),
                run_id="pc-full900-historical-import",
                occurred_at=(
                    _occurred_at(item.get("processed_at"))
                    or fallback_stamp + timedelta(microseconds=line_number)
                ),
            )
            imported += 1
    return {"imported": imported, "invalid": invalid}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", type=int, default=None)
    parser.add_argument("--shards", type=int, default=6)
    parser.add_argument("--shard-file", type=Path, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--variant-id", type=int)
    parser.add_argument(
        "--candidate-url",
        action="append",
        default=[],
        help="prepend a researched URL for one --variant-id retry",
    )
    parser.add_argument(
        "--import-existing-results",
        action="store_true",
        help="fold existing PC-FULL-900 JSONL statuses into the failure ledger",
    )
    args = parser.parse_args()

    if args.import_existing_results:
        print(json.dumps(import_existing_results(), sort_keys=True))
        return 0

    if args.shard_file:
        shard_path = args.shard_file
        shard_id = shard_path.stem
    else:
        if args.shard is None:
            raise SystemExit("--shard, --shard-file, or --import-existing-results required")
        shard_path = OUT_ROOT / f"shard_{args.shard}.json"
        shard_id = str(args.shard)

    work = json.loads(shard_path.read_text(encoding="utf-8"))
    work = select_target_work(
        work,
        variant_id=args.variant_id,
        candidate_urls=args.candidate_url,
    )
    work = hydrate_selected_work_languages(work)
    exact_pricecharting_variants = load_exact_pricecharting_variants()
    for row in work:
        row["_exact_pc_bound"] = int(row["vid"]) in exact_pricecharting_variants
    if args.offset:
        work = work[args.offset :]
    if args.limit:
        work = work[: args.limit]

    results_path = OUT_ROOT / f"results_shard_{shard_id}.jsonl"
    map_path = MAP_DIR / f"c11_pc_ebay_map_full900_shard_{shard_id}.jsonl"
    manifest_path = OUT_ROOT / f"evidence_manifest_shard_{shard_id}.jsonl"
    progress_path = OUT_ROOT / f"progress_shard_{shard_id}.json"

    # A prior JSONL status is diagnostic history, not completion authority.
    # Only the current canonical exact binding is durable enough to skip.
    done = verified_done_variants(work, exact_pricecharting_variants)

    print(
        f"SHARD {shard_id} load={len(work)} done={len(done)} "
        f"remain={sum(1 for row in work if int(row['vid']) not in done)}",
        flush=True,
    )

    ok = failed = skipped = 0
    cdp_fail_streak = 0
    run_id = datetime.now(timezone.utc).strftime("pc_full900_%Y%m%dT%H%M%S%fZ")
    with results_path.open("a", encoding="utf-8") as result_stream, map_path.open(
        "a", encoding="utf-8"
    ) as map_stream, manifest_path.open("a", encoding="utf-8") as manifest_stream:
        for index, row in enumerate(work):
            variant_id = int(row["vid"])
            if variant_id in done:
                skipped += 1
                continue
            print(
                f"[{shard_id} {index + 1}/{len(work)}] "
                f"vid={variant_id} r={row.get('rank')} {row.get('name')}",
                flush=True,
            )
            try:
                item = process_one(row, force=args.force)
            except Exception as error:
                item = {
                    "vid": variant_id,
                    "status": f"exception:{error}",
                    "name": row.get("name"),
                }
            item["shard"] = shard_id
            item["processed_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            result_stream.write(json.dumps(item, ensure_ascii=False) + "\n")
            result_stream.flush()
            record_item_outcome(item, row, run_id=run_id)

            status = str(item.get("status") or "")
            is_cdp_failure = (
                "CDP_DOWN" in status
                or "CDP lock timeout" in status
                or status.startswith("fetch_err:")
                or status.startswith("resolve_fetch_err:")
            )
            if is_cdp_failure:
                cdp_fail_streak += 1
                print(
                    f"  CDP_FAIL streak={cdp_fail_streak}/3 status={status}",
                    flush=True,
                )
                if cdp_fail_streak >= 3:
                    summary_document = {
                        "shard": shard_id,
                        "ok": ok,
                        "fail": failed + 1,
                        "skip": skipped,
                        "results": str(results_path),
                        "map": str(map_path),
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "fatal": "CDP fail ×3",
                        "last_status": status,
                        "last_vid": variant_id,
                    }
                    (OUT_ROOT / f"summary_shard_{shard_id}.json").write_text(
                        json.dumps(summary_document, indent=2),
                        encoding="utf-8",
                    )
                    print("SUMMARY", json.dumps(summary_document), flush=True)
                    return 2
            else:
                cdp_fail_streak = 0

            if status in {"attached", "attached_no_sold"}:
                manifest_row = {
                    "manifest_version": "pc_provider_native_evidence_v1",
                    "source_code": "pricecharting",
                    "variant_id": variant_id,
                    "pc_product_id": (
                        int(item["product_id"])
                        if str(item.get("product_id") or "").isdigit()
                        else item.get("product_id")
                    ),
                    "pc_url": item.get("url_final"),
                    "title": item.get("title"),
                    "html_path": item.get("html"),
                    "html_sha256": item.get("html_sha256"),
                    "parse_json_path": item.get("parse_json"),
                    "psa10_sales_count": item.get("sold_count"),
                    "psa10_price_range_usd": item.get("price_range"),
                    "psa10_history_points": item.get("psa10_history_points"),
                    "psa10_history_last_usd": item.get("psa10_history_last_usd"),
                    "card_name": item.get("name"),
                    "set_name": item.get("set"),
                    "collector_number": item.get("num"),
                    "tcg": item.get("tcg"),
                    "language": item.get("language"),
                    "captured_at": item["processed_at"],
                    "shard": shard_id,
                    "run_id": run_id,
                }
                manifest_stream.write(
                    json.dumps(manifest_row, ensure_ascii=False, sort_keys=True) + "\n"
                )
                manifest_stream.flush()

            if status == "attached":
                ok += 1
                map_row = {
                    "variant_id": variant_id,
                    "pc_url": item.get("url_final"),
                    "pc_product_id": (
                        int(item["product_id"])
                        if str(item.get("product_id") or "").isdigit()
                        else item.get("product_id")
                    ),
                    "htmlPath": item.get("html"),
                    "html_path": item.get("html"),
                    "ready_for_c12": True,
                    "card_name": item.get("name"),
                    "set_name": item.get("set"),
                    "collector_number": item.get("num"),
                    "ebay_items_psa10": item.get("ebay_items") or [],
                    "ebay_uuid_or_item": (item.get("ebay_items") or [None])[0],
                    "confidence": "high",
                    "status": "mapped",
                    "mapped_at": item["processed_at"],
                    "notes": f"full900 shard={shard_id}; sold={item.get('sold_count')}",
                    "source": "pc_full900",
                }
                map_stream.write(json.dumps(map_row, ensure_ascii=False) + "\n")
                map_stream.flush()
                print(
                    f"  OK pid={item.get('product_id')} sold={item.get('sold_count')} "
                    f"{item.get('price_range')}",
                    flush=True,
                )
            elif status in {"skip_have_pc", "attached_no_sold"}:
                skipped += 1
                print(f"  {status}", flush=True)
            else:
                failed += 1
                print(f"  FAIL {status} {(item.get('title') or '')[:60]}", flush=True)

            progress = {
                "shard": shard_id,
                "i": index + 1,
                "total": len(work),
                "ok": ok,
                "fail": failed,
                "skip": skipped,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            progress_path.write_text(
                json.dumps(progress, indent=2),
                encoding="utf-8",
            )
            time.sleep(0.35)

    summary_document = {
        "shard": shard_id,
        "ok": ok,
        "fail": failed,
        "skip": skipped,
        "results": str(results_path),
        "map": str(map_path),
        "manifest": str(manifest_path),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    (OUT_ROOT / f"summary_shard_{shard_id}.json").write_text(
        json.dumps(summary_document, indent=2),
        encoding="utf-8",
    )
    print("SUMMARY", json.dumps(summary_document), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
