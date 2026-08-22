# -*- coding: utf-8 -*-
"""Capture a PriceCharting CF-cleared browser session and fetch product HTML.

Modes:
1) connect — attach to Chrome already started with --remote-debugging-port=PORT
2) launch  — open headed Chrome; waits until CF clears (you can click if needed)
3) fetch   — reuse saved storage_state cookies to download product HTML

Examples:
  python pipelines/pricecharting_cf_session.py launch
  python pipelines/pricecharting_cf_session.py connect --port 9333
  python pipelines/pricecharting_cf_session.py fetch --url https://www.pricecharting.com/game/pokemon-base-set/charizard-4
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
try:
    from .failure_ledger import record_failure, record_resolution
except ImportError:
    from failure_ledger import record_failure, record_resolution

STATE_DIR = ROOT / "data" / "private" / "pricecharting_session"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STORAGE = STATE_DIR / "storage_state.json"
COOKIES = STATE_DIR / "cookies.json"
META = STATE_DIR / "session_meta.json"
HTML_DIR = STATE_DIR / "html"
HTML_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_URL = "https://www.pricecharting.com/game/pokemon-base-set/charizard-4"


def _is_cf(title: str, html: str) -> bool:
    t = (title or "").lower()
    h = html[:12000].lower()
    return (
        "just a moment" in t
        or "just a moment" in h
        or "しばらく" in (title or "")
        or "challenge-platform" in h
        or "cf-browser-verification" in h
        or "checking your browser" in h
    )


def _is_terminal_not_found(status_code: int | None, title: str, html: str) -> bool:
    """Recognize PriceCharting's tiny plain-text 404 response.

    A stale numeric product URL is one candidate among several for a card.  It
    must not spend the full Cloudflare timeout before the caller tries the next
    candidate URL.
    """
    if status_code != 404 or len(html) >= 4096:
        return False
    normalized = " ".join((html or "").lower().split())
    return "404 page not found" in normalized or "not found" in (title or "").lower()


def _save_state(context, page, note: str) -> dict:
    context.storage_state(path=str(STORAGE))
    cookies = context.cookies()
    COOKIES.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
    pc = [c for c in cookies if "pricecharting" in (c.get("domain") or "")]
    meta = {
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": note,
        "url": page.url,
        "title": page.title(),
        "cookie_count": len(cookies),
        "pricecharting_cookies": [
            {"name": c.get("name"), "domain": c.get("domain"), "expires": c.get("expires")}
            for c in pc
        ],
        "has_cf_clearance": any(c.get("name") == "cf_clearance" for c in pc),
        "storage_state": str(STORAGE),
        "cookies_path": str(COOKIES),
    }
    META.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2), flush=True)
    return meta


def _wait_clear(page, timeout_s: int = 180) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            title = page.title()
            html = page.content()
        except Exception:
            time.sleep(2)
            continue
        cf = _is_cf(title, html)
        print(
            f"  wait title={title!r} html_len={len(html)} cf={cf} url={page.url}",
            flush=True,
        )
        if not cf and len(html) > 20000 and "VGPC" in html:
            return True
        if not cf and len(html) > 50000:
            return True
        time.sleep(3)
    return False


def cmd_connect(port: int, url: str, timeout_s: int) -> int:
    from cdp_identity import require_session_ready

    require_session_ready(port)
    endpoint = f"http://127.0.0.1:{port}"
    print(f"Connecting CDP {endpoint}", flush=True)
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(endpoint, timeout=15000)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = None
        # Prefer existing pricecharting tab
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if "pricecharting.com" in (pg.url or ""):
                    page = pg
                    context = ctx
                    break
            if page:
                break
        if page is None:
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=120000)
        else:
            print(f"Reusing tab {page.url}", flush=True)
            if url and "pricecharting.com" in url and page.url.rstrip("/") != url.rstrip("/"):
                page.goto(url, wait_until="domcontentloaded", timeout=120000)

        ok = _wait_clear(page, timeout_s=timeout_s)
        meta = _save_state(context, page, note=f"connect port={port} ok={ok}")
        if ok:
            html = page.content()
            out = HTML_DIR / "capture_connect.html"
            out.write_text(html, encoding="utf-8", errors="replace")
            print(f"WROTE {out} bytes={len(html)}", flush=True)
        return 0 if ok and meta.get("has_cf_clearance") else 2


def cmd_launch(url: str, timeout_s: int) -> int:
    print("Launching headed Chrome — if CF shows, click through once in that window", flush=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(STATE_DIR / "browser_profile"),
            channel="chrome",
            headless=False,
            viewport={"width": 1400, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=120000)
        ok = _wait_clear(page, timeout_s=timeout_s)
        meta = _save_state(context, page, note=f"launch ok={ok}")
        if ok:
            html = page.content()
            out = HTML_DIR / "capture_launch.html"
            out.write_text(html, encoding="utf-8", errors="replace")
            print(f"WROTE {out} bytes={len(html)}", flush=True)
        context.close()
        return 0 if ok else 2


def _try_cdp_ports() -> list[int]:
    """Real Chrome with remote debugging is the proven CF path (2026-07-30)."""
    # 9333 is the dedicated CARDZ profile (scripts/ensure_chrome_cdp.ps1 -Port
    # 9333). 9222 belongs to the Codex browser profile: touching it interferes
    # with that tooling and its Cloudflare clearance differs, so failing fast
    # here beats silently fetching through the wrong profile.
    return [9333]


def _cmd_fetch_once(
    url: str,
    out: Path | None,
    headless: bool = True,
    timeout_s: int = 90,
    *,
    prefer_cdp: bool = True,
) -> int:
    """Fetch product HTML.

    Success experience (2026-07-30 Magikarp #203):
    - CDP connect to real Chrome (:9333) clears CF reliably.
    - Headless + storage_state / persistent profile alone often stays on CF challenge.
    """
    if out is None:
        slug = url.rstrip("/").split("/")[-1] or "product"
        out = HTML_DIR / f"{slug}.html"
    out.parent.mkdir(parents=True, exist_ok=True)

    # 1) Prefer CDP real Chrome
    if prefer_cdp:
        try:
            from .cdp_identity import fetch_targets, fetch_version, reject_reason
        except ImportError:
            from cdp_identity import fetch_targets, fetch_version, reject_reason

        with sync_playwright() as p:
            for port in _try_cdp_ports():
                endpoint = f"http://127.0.0.1:{port}"
                try:
                    why = reject_reason(fetch_version(port))
                    if why:
                        print(f"fetch: CDP {port} skip (identity {why})", flush=True)
                        continue
                    if fetch_targets(port) is None:
                        print(f"fetch: CDP {port} skip (jammed-targets)", flush=True)
                        continue
                    print(f"fetch: try CDP {endpoint}", flush=True)
                    browser = p.chromium.connect_over_cdp(endpoint, timeout=15000)
                except Exception as e:
                    print(f"fetch: CDP {port} skip ({e})", flush=True)
                    continue
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()
                response = page.goto(url, wait_until="domcontentloaded", timeout=120000)
                initial_html = page.content()
                status_code = response.status if response is not None else None
                if _is_terminal_not_found(status_code, page.title(), initial_html):
                    out.write_text(initial_html, encoding="utf-8", errors="replace")
                    print(f"WROTE terminal 404 {out}", flush=True)
                    try:
                        page.close()
                    except Exception:
                        pass
                    return 4
                ok = _wait_clear(page, timeout_s=timeout_s)
                html = page.content()
                title = page.title()
                cf = _is_cf(title, html)
                print(f"fetch CDP title={title!r} len={len(html)} cf={cf} ok={ok}", flush=True)
                if ok and not cf:
                    out.write_text(html, encoding="utf-8", errors="replace")
                    print(f"WROTE {out}", flush=True)
                    _save_state(context, page, note=f"fetch cdp port={port} ok url={url}")
                    try:
                        page.close()
                    except Exception:
                        pass
                    return 0
                # Unverified / CF-blocked HTML never lands on the final cache
                # path — keep it beside it for diagnosis only.
                reject = out.with_name(out.name + ".rejected")
                reject.write_text(html, encoding="utf-8", errors="replace")
                try:
                    page.close()
                except Exception:
                    pass
                print(f"fetch: CDP {port} still CF (snapshot {reject}) — try next", flush=True)

    # 2) No launch fallback: Chrome launched here (headless especially) is the
    # known Cloudflare-blocked mode and only produces junk CF snapshots.
    print(
        "fetch: CDP unavailable — start the headed Chrome CDP session first "
        "(scripts/ensure_chrome_cdp.ps1 -Port 9333), then retry",
        flush=True,
    )
    return 2


def cmd_fetch(
    url: str,
    out: Path | None,
    headless: bool = True,
    timeout_s: int = 90,
    *,
    prefer_cdp: bool = True,
) -> int:
    try:
        code = _cmd_fetch_once(
            url,
            out,
            headless=headless,
            timeout_s=timeout_s,
            prefer_cdp=prefer_cdp,
        )
    except Exception as error:
        record_failure(
            source="pricecharting",
            stage="fetch_product_html",
            script=__file__,
            item_key=url,
            reason_code="fetch_exception",
            message=str(error),
            retryable=True,
            url=url,
            evidence_paths=[out] if out else [],
            next_action="retry",
            error_type=type(error).__name__,
        )
        raise
    if code == 0:
        record_resolution(
            source="pricecharting",
            stage="fetch_product_html",
            script=__file__,
            item_key=url,
            resolution="html_verified_non_cf",
            evidence_paths=[out] if out else [],
        )
    elif code == 4:
        record_failure(
            source="pricecharting",
            stage="fetch_product_html",
            script=__file__,
            item_key=url,
            reason_code="http_not_found",
            message="candidate product URL returned a terminal 404",
            retryable=False,
            url=url,
            evidence_paths=[out] if out else [],
            next_action="try_alternate_candidate_url",
        )
    else:
        record_failure(
            source="pricecharting",
            stage="fetch_product_html",
            script=__file__,
            item_key=url,
            reason_code="cf_or_session_unavailable",
            message="fetch did not produce verified non-Cloudflare HTML",
            retryable=True,
            url=url,
            evidence_paths=[out] if out else [],
            next_action="refresh_session_then_retry",
        )
    return code


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_conn = sub.add_parser("connect", help="Attach to Chrome remote debugging port")
    p_conn.add_argument("--port", type=int, default=9333)
    p_conn.add_argument("--url", default=DEFAULT_URL)
    p_conn.add_argument("--timeout", type=int, default=120)

    p_launch = sub.add_parser("launch", help="Open headed Chrome and wait for CF clear")
    p_launch.add_argument("--url", default=DEFAULT_URL)
    p_launch.add_argument("--timeout", type=int, default=180)

    p_fetch = sub.add_parser("fetch", help="Fetch product HTML with saved session")
    p_fetch.add_argument("--url", default=DEFAULT_URL)
    p_fetch.add_argument("--out", type=Path, default=None)
    p_fetch.add_argument("--headed", action="store_true", help="Show browser (more CF-stable)")
    p_fetch.add_argument("--timeout", type=int, default=90)

    args = ap.parse_args()
    if args.cmd == "connect":
        return cmd_connect(args.port, args.url, args.timeout)
    if args.cmd == "launch":
        return cmd_launch(args.url, args.timeout)
    if args.cmd == "fetch":
        return cmd_fetch(args.url, args.out, headless=not args.headed, timeout_s=args.timeout)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
