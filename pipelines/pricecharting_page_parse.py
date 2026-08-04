# -*- coding: utf-8 -*-
"""Parse a PriceCharting product HTML page into chart history + completed sales.

Works on:
- live HTML after CF (saved file)
- Wayback `id_` raw HTML
- any SSR product page that embeds VGPC.chart_data

Does NOT fetch network. Feed it HTML bytes/text.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from html import unescape
from pathlib import Path
from typing import Any


CHART_LABELS = {
    "used": ("Ungraded", "loose-price", 9, "completed-auctions-used"),
    "cib": ("Grade 7", "cib-price", 2, "completed-auctions-cib"),
    "new": ("Grade 8", "new-price", 8, "completed-auctions-new"),
    "graded": ("Grade 9", "graded-price", 3, "completed-auctions-graded"),
    "boxonly": ("Grade 9.5", "box-only-price", 1, "completed-auctions-box-only"),
    "manualonly": ("PSA 10", "manual-only-price", 7, "completed-auctions-manual-only"),
}

SALES_LABELS = {
    "completed-auctions-used": "Ungraded",
    "completed-auctions-cib": "Grade 7",
    "completed-auctions-new": "Grade 8",
    "completed-auctions-graded": "Grade 9",
    "completed-auctions-box-only": "Grade 9.5",
    "completed-auctions-manual-only": "PSA 10",
    "completed-auctions-grade-seventeen": "CGC 10",
    "completed-auctions-grade-eighteen": "SGC 10",
    "completed-auctions-grade-nineteen": "BGS 10",
    "completed-auctions-grade-three": "Grade 3",
    "completed-auctions-grade-four": "Grade 4",
    "completed-auctions-grade-five": "Grade 5",
    "completed-auctions-grade-six": "Grade 6",
}


def _extract_js_value(src: str, start: int) -> str | None:
    i = start
    while i < len(src) and src[i] in " \t":
        i += 1
    if i >= len(src):
        return None
    if src[i] in "{[":
        stack: list[str] = []
        in_str = False
        esc = False
        quote = ""
        j = i
        while j < len(src):
            c = src[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == quote:
                    in_str = False
            else:
                if c in ('"', "'"):
                    in_str = True
                    quote = c
                elif c in "{[":
                    stack.append(c)
                elif c in "}]":
                    if stack:
                        stack.pop()
                        if not stack:
                            return src[i : j + 1]
            j += 1
            if j - i > 8_000_000:
                break
        return None
    if src[i] in ('"', "'"):
        quote = src[i]
        j = i + 1
        esc = False
        while j < len(src):
            c = src[j]
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                return src[i : j + 1]
            j += 1
        return None
    j = i
    while j < len(src) and src[j] not in ";\n":
        j += 1
    return src[i:j].strip()


def extract_vgpc(html: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in re.finditer(r"VGPC\.([a-zA-Z0-9_]+)\s*=\s*", html):
        key = m.group(1)
        raw = _extract_js_value(html, m.end())
        if raw is None:
            out[key] = None
            continue
        try:
            out[key] = json.loads(raw)
            continue
        except json.JSONDecodeError:
            pass
        # JS object with bare keys (VGPC.product)
        if key == "product" or (raw.startswith("{") and ":" in raw):
            prod: dict[str, Any] = {}
            for k, v in re.findall(
                r"([a-zA-Z0-9_]+)\s*:\s*(\"[^\"]*\"|'[^']*'|true|false|null|-?\d+(?:\.\d+)?)",
                raw,
            ):
                if v in ("true", "false", "null"):
                    prod[k] = {"true": True, "false": False, "null": None}[v]
                elif v[0] in "\"'":
                    prod[k] = v[1:-1]
                else:
                    try:
                        prod[k] = int(v)
                    except ValueError:
                        prod[k] = float(v)
            out[key] = prod
        else:
            out[key] = raw.strip().strip("\"'")
    return out


def extract_sales_for_class(html: str, cls: str) -> list[dict[str, Any]]:
    # Prefer content div (not tab button)
    pat = rf'<div[^>]+class="(?![^"]*\btab\b)[^"]*\b{re.escape(cls)}\b[^"]*"[^>]*>'
    dm = re.search(pat, html, flags=re.I)
    if dm:
        start = dm.start()
    else:
        start = None
        idx = 0
        while True:
            i = html.find(cls, idx)
            if i < 0:
                break
            window = html[max(0, i - 100) : i + 60]
            if "tab " in window or "data-show-tab" in window or 'class="tab' in window:
                idx = i + 1
                continue
            div_start = html.rfind("<div", max(0, i - 200), i)
            start = div_start if div_start >= 0 else i
            break
    if start is None:
        return []

    rest = html[start : start + 150000]
    tm = re.search(
        r'<table[^>]*class="[^"]*hoverable-rows[^"]*sortable[^"]*"[\s\S]*?</table>',
        rest,
        flags=re.I,
    )
    if not tm:
        tm = re.search(r"<table[\s\S]*?ebay\.com/itm/[\s\S]*?</table>", rest, flags=re.I)
    if not tm:
        return []

    rows: list[dict[str, Any]] = []
    for rm in re.finditer(r"<tr[\s\S]*?</tr>", tm.group(0), flags=re.I):
        row = rm.group(0)
        if "ebay.com/itm/" not in row:
            continue
        date_m = re.search(r"(20\d{2}-\d{2}-\d{2})", row)
        itm_m = re.search(r"ebay\.com/itm/(\d+)", row)
        price_m = re.search(r'class="numeric"[\s\S]*?\$([0-9,]+\.\d{2})', row) or re.search(
            r"\$([0-9,]+\.\d{2})", row
        )
        title_m = re.search(
            r'class="js-ebay-completed-sale"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>',
            row,
            flags=re.I,
        )
        if not title_m:
            title_m = re.search(
                r'href="([^"]*ebay\.com/itm/[^"]+)"[^>]*>([\s\S]*?)</a>',
                row,
                flags=re.I,
            )
        href = unescape(title_m.group(1)) if title_m else None
        title = (
            unescape(re.sub(r"<[^>]+>", "", title_m.group(2))).strip() if title_m else None
        )
        rows.append(
            {
                "date": date_m.group(1) if date_m else None,
                "ebay_itm": itm_m.group(1) if itm_m else None,
                "price_usd": float(price_m.group(1).replace(",", "")) if price_m else None,
                "title": (title or "")[:200] if title else None,
                "ebay_url": href,
            }
        )
    return rows


def parse_product_html(html: str, source_url: str | None = None) -> dict[str, Any]:
    if "Just a moment" in html[:2000] or "challenge-platform" in html[:8000]:
        return {
            "ok": False,
            "error": "cloudflare_challenge_html",
            "source_url": source_url,
        }

    vgpc = extract_vgpc(html)
    chart_raw = vgpc.get("chart_data") if isinstance(vgpc.get("chart_data"), dict) else {}
    chart: dict[str, Any] = {}
    for key, series in chart_raw.items():
        if not isinstance(series, list):
            continue
        meta = CHART_LABELS.get(key, (None, None, None, None))
        last = series[-1] if series else None
        last_nz = None
        for pt in reversed(series):
            if isinstance(pt, list) and len(pt) >= 2 and pt[1]:
                last_nz = pt
                break
        chart[key] = {
            "label": meta[0],
            "api_price_key": meta[1],
            "condition_id": meta[2],
            "tab_class": meta[3],
            "points": len(series),
            "unit": "US_cents",
            "series": series,
            "last": last,
            "last_nonzero": last_nz,
            "last_usd": (last_nz[1] / 100.0) if last_nz else None,
        }

    sales: dict[str, Any] = {}
    for cls, label in SALES_LABELS.items():
        rows = extract_sales_for_class(html, cls)
        sales[cls] = {"label": label, "count": len(rows), "rows": rows}

    product = vgpc.get("product") if isinstance(vgpc.get("product"), dict) else {}
    # fallback product id
    if not product.get("id"):
        m = re.search(r'data-product-id="(\d+)"', html)
        if m:
            product = {**product, "id": int(m.group(1))}

    return {
        "ok": True,
        "source_url": source_url,
        "product": product,
        "console_uid": vgpc.get("console_uid"),
        "category": vgpc.get("category"),
        "chart": chart,
        "sales": sales,
        "psa10": {
            "history": chart.get("manualonly"),
            "completed_sales": sales.get("completed-auctions-manual-only"),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Parse PriceCharting product HTML")
    ap.add_argument("html_path", type=Path)
    ap.add_argument("--url", default=None)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--psa10-only", action="store_true")
    args = ap.parse_args()

    html = args.html_path.read_text(encoding="utf-8", errors="replace")
    result = parse_product_html(html, source_url=args.url)
    if args.psa10_only and result.get("ok"):
        result = {
            "ok": True,
            "product": result.get("product"),
            "psa10": result.get("psa10"),
            "source_url": result.get("source_url"),
        }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"WROTE {args.out}")
    else:
        # compact summary to stdout
        if result.get("ok"):
            p = result.get("product") or {}
            h = (result.get("psa10") or {}).get("history") or {}
            s = (result.get("psa10") or {}).get("completed_sales") or {}
            print(
                json.dumps(
                    {
                        "ok": True,
                        "product_id": p.get("id"),
                        "psa10_history_points": h.get("points"),
                        "psa10_last_usd": h.get("last_usd"),
                        "psa10_sales_count": s.get("count"),
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(text)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
