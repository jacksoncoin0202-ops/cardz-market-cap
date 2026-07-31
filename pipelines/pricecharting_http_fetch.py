# -*- coding: utf-8 -*-
"""Fetch PriceCharting product HTML using saved cookies + chrome impersonation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from curl_cffi import requests

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "data" / "private" / "pricecharting_session"
COOKIES = STATE_DIR / "cookies.json"


def load_cookie_jar() -> dict[str, str]:
    raw = json.loads(COOKIES.read_text(encoding="utf-8"))
    jar: dict[str, str] = {}
    for c in raw:
        if "pricecharting" not in (c.get("domain") or ""):
            continue
        jar[c["name"]] = c["value"]
    return jar


def fetch(url: str, out: Path) -> int:
    if not COOKIES.exists():
        print("missing cookies.json — run pricecharting_cf_session.py launch first")
        return 2
    jar = load_cookie_jar()
    print("cookies", sorted(jar.keys()))
    s = requests.Session(impersonate="chrome131")
    r = s.get(
        url,
        cookies=jar,
        headers={
            "accept": "text/html,application/xhtml+xml",
            "referer": "https://www.pricecharting.com/",
        },
        timeout=60,
        allow_redirects=True,
    )
    text = r.text
    cf = "just a moment" in text.lower() or "challenge-platform" in text[:8000].lower()
    print(f"status={r.status_code} bytes={len(r.content)} cf={cf} final={r.url}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", errors="replace")
    print("WROTE", out)
    return 2 if cf or r.status_code != 200 else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    return fetch(args.url, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
