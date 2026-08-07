#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows-host CDP refresh for PriceCharting sold HTML (serial, stable).

Runs on Windows Python so Playwright talks to local Chrome :9222 without WSL networking weirdness.
Then optionally runs C11 sold ingest via WSL venv.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
from pricecharting_cf_session import _is_cf  # noqa: E402
from pc_psa10_price_derivation import validate_pc_psa10  # noqa: E402

MAP = ROOT / "data/runtime/private-source-map/c11_pc_ebay_map_full900.jsonl"
OUT = ROOT / "data/runtime/operator/collect/pc_cdp_refresh_report.json"
PY_WSL = "wsl.exe"


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


def ensure_cdp(port: int = 9222) -> None:
    ps1 = ROOT / "scripts" / "ensure_chrome_cdp.ps1"
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1), "-Port", str(port)],
        check=False,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 = all selected rows")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=4.0)
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
    with sync_playwright() as playwright:
        try:
            if pending_batch:
                browser = playwright.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{args.cdp_port}"
                )
                if not browser.contexts:
                    raise RuntimeError("dedicated CARDZ Chrome has no browser context")
                context = browser.contexts[0]
                price_pages = [page for page in context.pages if "pricecharting.com" in (page.url or "")]
                page = price_pages[0] if price_pages else context.new_page()
                for extra in price_pages[1:]:
                    extra.close()
            else:
                page = None
        except Exception as exc:  # noqa: BLE001
            session_error = f"single_cdp_connect:{type(exc).__name__}:{exc}"
            page = None

        if page is not None:
            for pending_index, row in enumerate(pending_batch, 1):
                i = len(reused_results) + pending_index
                # Map URLs originate in HTML and may therefore contain
                # ``&amp;`` inside a path segment. Playwright needs the decoded
                # URL; otherwise PriceCharting redirects the exact product to
                # a search result whose product id happens to be present but
                # whose canonical URL is not the bound product URL.
                url = unescape(str(row.get("pc_url") or ""))
                html_rel = row.get("html_path") or row.get("htmlPath")
                if not url or not html_rel:
                    fail += 1
                    results.append({"variant_id": row.get("variant_id"), "status": "missing"})
                    break
                out = ROOT / html_rel
                out.parent.mkdir(parents=True, exist_ok=True)
                try:
                    response = page.goto(url, wait_until="domcontentloaded", timeout=120000)
                    code = response.status if response is not None else None
                    retry_after = response.headers.get("retry-after") if response is not None else None
                    html = page.content()
                    title = page.title().strip()
                except Exception as exc:  # noqa: BLE001
                    fail += 1
                    results.append(
                        {
                            "variant_id": row.get("variant_id"),
                            "status": "navigation_error",
                            "error": f"{type(exc).__name__}:{exc}",
                        }
                    )
                    break
                expected_product_id = str(row.get("pc_product_id") or "").strip()
                product_match = re.search(r'\bproduct-id=["\'](\d+)["\']', html, re.I)
                actual_product_id = product_match.group(1) if product_match else ""
                canonical_url = canonical_url_from_html(html)
                blocked = _is_cf(title, html) if html else True
                challenge_resolved = False
                if code == 403 and blocked and args.challenge_wait > 0:
                    deadline = time.monotonic() + args.challenge_wait
                    while time.monotonic() < deadline:
                        page.wait_for_timeout(5000)
                        html = page.content()
                        title = page.title().strip()
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
                temporary = out.with_name(f".{out.name}.{os.getpid()}.next")
                temporary.write_text(html, encoding="utf-8", errors="replace")
                validation_row = dict(row)
                validation_row.pop("htmlPath", None)
                validation_row["html_path"] = str(temporary)
                validation_row["notes"] = ""
                exact_price, exact_reason = validate_pc_psa10(validation_row)
                explicit_psa10_ok = exact_price is not None
                if (
                    (code == 200 or challenge_resolved)
                    and len(html) > 5000
                    and not blocked
                    and identity_ok
                    and explicit_psa10_ok
                ):
                    os.replace(temporary, out)
                    ok += 1
                    status = "ok"
                else:
                    temporary.unlink(missing_ok=True)
                    if code == 429:
                        rate_limited += 1
                        fail += 1
                        status = "rate_limited"
                    elif blocked:
                        cf += 1
                        status = "cf_or_fail"
                    elif identity_ok and not explicit_psa10_ok:
                        fail += 1
                        status = "explicit_psa10_missing"
                    else:
                        fail += 1
                        status = "product_id_mismatch" if not identity_ok else "http_or_content_fail"
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
                print(f"[{i}/{len(batch)}] {status} vid={row.get('variant_id')} len={len(html)} code={code}", flush=True)
                if status != "ok":
                    break
                time.sleep(args.sleep)

    ingest = None
    if not args.no_ingest and ok == len(batch) and fail == 0 and cf == 0:
        # C11 via WSL backend venv (MySQL path lives there)
        cmd = [
            PY_WSL, "-d", "Ubuntu", "--", "bash", "-lc",
            "cd /mnt/c/Users/jackson0202/Documents/Playground/cardz-market-cap && "
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
