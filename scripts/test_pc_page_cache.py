#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pc_page_cache: one read + one parse per local PriceCharting page.

A04/A05 2026-08-23: the PC lane read and parsed the same 1238 pages four times
per run (validate, ingest child, derive, materialize) plus every page under
the history root. Contract:

  * second load of an unchanged file: no parse, same sha256/canonical/parse
  * changed bytes or changed parser fingerprint: parse again
  * CARDZ_PC_PAGE_CACHE=off: parse every time, never touch a cache file
  * missing file / directory: None, no exception
  * callers get independent copies; source_url is per call
  * validate_pc_psa10, c11 collect_sales and materialize collect_local_history
    share one parse of the same page

Run: python -X utf8 scripts/test_pc_page_cache.py
"""
from __future__ import annotations

import hashlib
from decimal import Decimal
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

TEST_TMP = ROOT / "data" / "runtime" / "test-tmp"
TEST_TMP.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="pc_page_cache_", dir=str(TEST_TMP)))
CACHE_FILE = TMP / "cache.sqlite3"
os.environ["CARDZ_PC_PAGE_CACHE"] = str(CACHE_FILE)

import pc_page_cache as pcc  # noqa: E402
import c11_pc_sold_ingest as c11  # noqa: E402
import pc_psa10_price_derivation as deriv  # noqa: E402
import pc_psa10_price_materialize as mat  # noqa: E402

URL = "https://www.pricecharting.com/game/pokemon-promo/test-card-85"
PID = 7108819
FAILED: list[str] = []
CHECKS = 0
PARSES: list[int] = []


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


def page_html(cents: int = 13000) -> bytes:
    return (
        "<html><head>"
        f'<link rel="canonical" href="{URL}">'
        "</head><body>"
        f"<script>VGPC.product = {{ id: {PID}, name: 'x' }};</script>"
        '<script>VGPC.chart_data = {"manualonly": [[1723593600000, 12345], '
        f"[1723680000000, {cents}]]}};</script>"
        "</body></html>"
    ).encode("utf-8")


def main() -> int:
    real_parse = pcc.parse_product_html

    def counting_parse(html, source_url=None):
        PARSES.append(1)
        return real_parse(html, source_url=source_url)

    pcc.parse_product_html = counting_parse  # type: ignore[assignment]
    try:
        html_path = TMP / "1_7108819.html"
        html_path.write_bytes(page_html())
        sha = hashlib.sha256(page_html()).hexdigest()

        # 1. miss then hit
        PARSES.clear()
        first = pcc.load_page(html_path, source_url="u1")
        second = pcc.load_page(html_path, source_url="u2")
        check("first is a miss, second a hit", (first.hit, second.hit), (False, True))
        check("one parse for two loads", len(PARSES), 1)
        check("sha256 is the file hash", (first.sha256, second.sha256), (sha, sha))
        check("canonical url", second.canonical_url, URL)
        check("parse ok + product id", (second.parsed.get("ok"), second.parsed["product"]["id"]), (True, PID))
        check("source_url is per call", (first.parsed["source_url"], second.parsed["source_url"]), ("u1", "u2"))
        first.parsed["product"]["id"] = 0
        third = pcc.load_page(html_path)
        check("callers get independent copies", third.parsed["product"]["id"], PID)
        check("cache file exists", CACHE_FILE.is_file(), True)

        # 2. changed bytes -> parse again, new sha
        html_path.write_bytes(page_html(14000))
        os.utime(html_path, ns=(first.mtime_ns + 5_000_000_000, first.mtime_ns + 5_000_000_000))
        PARSES.clear()
        changed = pcc.load_page(html_path)
        check("changed file misses", (changed.hit, len(PARSES)), (False, 1))
        check("changed file new sha", changed.sha256, hashlib.sha256(page_html(14000)).hexdigest())
        check("changed file new value", changed.parsed["psa10"]["history"]["last"][1], 14000)

        # 3. parser fingerprint change -> miss
        saved_fp = pcc.PARSER_FINGERPRINT
        pcc.PARSER_FINGERPRINT = "deadbeefdeadbeef"
        PARSES.clear()
        refp = pcc.load_page(html_path)
        check("new parser fingerprint misses", (refp.hit, len(PARSES)), (False, 1))
        pcc.PARSER_FINGERPRINT = saved_fp
        PARSES.clear()
        check("old fingerprint row replaced, current fingerprint hits again",
              (pcc.load_page(html_path).hit, len(PARSES)), (False, 1))
        check("then hits", pcc.load_page(html_path).hit, True)

        # 4. off switch
        os.environ["CARDZ_PC_PAGE_CACHE"] = "off"
        pcc.reset()
        PARSES.clear()
        a = pcc.load_page(html_path)
        b = pcc.load_page(html_path)
        check("off: every load parses", (a.hit, b.hit, len(PARSES)), (False, False, 2))
        check("off: bypass counted", pcc.STATS["bypass"], 2)
        check("off: same evidence", (a.sha256, a.canonical_url), (hashlib.sha256(page_html(14000)).hexdigest(), URL))
        os.environ["CARDZ_PC_PAGE_CACHE"] = str(CACHE_FILE)
        pcc.reset()

        # 5. missing / directory
        check("missing -> None", pcc.load_page(TMP / "nope.html"), None)
        (TMP / "dir.html").mkdir()
        check("directory -> None", pcc.load_page(TMP / "dir.html"), None)

        # 6. the three production sites share one parse
        html_path.write_bytes(page_html())
        os.utime(html_path, ns=(first.mtime_ns + 9_000_000_000, first.mtime_ns + 9_000_000_000))
        row = {"variant_id": 1, "pc_product_id": str(PID), "pc_url": URL, "html_path": str(html_path)}
        PARSES.clear()
        v1, r1 = deriv.validate_pc_psa10(row)
        v2, r2 = deriv.validate_pc_psa10(row)
        check("validate twice parses once", len(PARSES), 1)
        check("validate accepted", (r1, r2), ("accepted", "accepted"))
        check("validate artifact sha is the file hash", (v1["artifact_sha256"], v2["artifact_sha256"]), (sha, sha))
        check("validate price", Decimal(v1["price_usd"]), Decimal("130"))
        sales, stats, reports = c11.collect_sales([row])
        check("ingest child reuses the parse", len(PARSES), 1)
        check("ingest child saw the page", stats["cards_seen"] - stats["cards_no_html"], 1)

        saved_active = mat._active_exact_pc_rows
        saved_point = mat._history_point
        mat._active_exact_pc_rows = lambda conn, mp: ([row], 1)  # type: ignore[assignment]
        mat._history_point = lambda **kw: None  # type: ignore[assignment]
        try:
            _rows, report = mat.collect_local_history(None, map_path=TMP / "map.jsonl", html_roots=[TMP])
        finally:
            mat._active_exact_pc_rows = saved_active  # type: ignore[assignment]
            mat._history_point = saved_point  # type: ignore[assignment]
        check("materialize reuses the parse", len(PARSES), 1)
        check("materialize matched the exact artifact", report["roots"][0]["matchedExactArtifacts"], 1)
        check("materialize skipped the directory and the missing file", report["roots"][0]["htmlFiles"], 2)

        # 7. with the cache off the same validate pair parses twice (the counter is live)
        os.environ["CARDZ_PC_PAGE_CACHE"] = "off"
        pcc.reset()
        PARSES.clear()
        deriv.validate_pc_psa10(row)
        deriv.validate_pc_psa10(row)
        check("NEGATIVE: cache off -> validate twice parses twice", len(PARSES), 2)

        # 8. A10 2026-08-23: the hit path must not walk Path.resolve (11 ms per
        #    call on /mnt/c, ~4,548 calls per PC lane); relative and absolute
        #    spellings of one file share one cache row.
        os.environ["CARDZ_PC_PAGE_CACHE"] = str(CACHE_FILE)
        pcc.reset()
        probe = TMP / "resolve-probe.html"
        probe.write_bytes(page_html(14100))
        first = pcc.load_page(probe)
        check("probe first load is a miss", first is not None and first.hit, False)
        same_drive = os.path.splitdrive(os.getcwd())[0].lower() == os.path.splitdrive(str(probe))[0].lower()
        relative = Path(os.path.relpath(probe)) if same_drive else probe
        original_resolve = Path.resolve

        def boom(self, *args, **kwargs):
            raise AssertionError(f"Path.resolve on the page-cache hit path: {self}")

        Path.resolve = boom  # type: ignore[assignment]
        try:
            second = pcc.load_page(probe)
            third = pcc.load_page(relative)
        except AssertionError as error:
            FAILED.append(f"FAIL hit path resolves: {error}")
            second = third = None
        finally:
            Path.resolve = original_resolve  # type: ignore[assignment]
        check("absolute spelling hits", second is not None and second.hit, True)
        check("relative spelling hits the same row", third is not None and third.hit, True)
        check("same sha via both spellings", second is not None and third is not None and second.sha256 == third.sha256 == first.sha256, True)
    finally:
        pcc.parse_product_html = real_parse  # type: ignore[assignment]
        os.environ["CARDZ_PC_PAGE_CACHE"] = str(CACHE_FILE)
        pcc.reset()

    for line in FAILED:
        print(line)
    print(f"{'FAIL' if FAILED else 'OK'} checks={CHECKS} failed={len(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
