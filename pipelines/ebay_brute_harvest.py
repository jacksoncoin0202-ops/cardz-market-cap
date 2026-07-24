#!/usr/bin/env python3
"""
eBay 暴力收割器（curl_cffi 版 — 唔使 browser）
================================================
用 curl_cffi 模擬 Chrome TLS 指紋，直接 HTTP GET 拎 item 頁 Product JSON-LD。
**唔使 Playwright**（2026-07-24 突破：item 頁靠 TLS 指紋就過到 PerimeterX）。

R1  item —— 逐個 item ID GET /itm/{id}，抽 Product JSON-LD：
      價錢、貨幣、PSA grade、PSA cert number、賣家評分。✅ 已驗證唔使 browser。

R2  search —— completed/sold 搜尋。⚠ PerimeterX 擋：
      即使 session warmup（首頁→item），sold search 都仲係彈 challenge 或空結果。
      呢條路而家未通，淨返 item route 可用。

用法：
    python ebay_brute_harvest.py item --ids 176641588834 176641588835 --out data/private/ebay_brute/items.jsonl
    python ebay_brute_harvest.py item --ids-file data/private/ebay_brute/ids.txt --out data/private/ebay_brute/items.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from curl_cffi import requests as cr

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "private" / "ebay_brute"
DATA_DIR.mkdir(parents=True, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def is_px(html: str) -> bool:
    h = html.lower()
    return ("px-captcha" in h or "pardon our interruption" in h
            or "security measure" in h or "human and not a bot" in h)


def extract_item_jsonld(html: str) -> dict:
    """由 item 頁 HTML 抽 Product JSON-LD。"""
    out = []
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
        try:
            out.append(json.loads(m.group(1)))
        except Exception:
            pass
    product = next((d for d in out if isinstance(d, dict) and d.get("@type") == "Product"), None)
    itempage = next((d for d in out if isinstance(d, dict) and d.get("@type") == "ItemPage"), None)
    return {"product": product, "itempage": itempage}


def _html_title(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    return (m.group(1).strip() if m else "")


def normalize_item(item_id: str, ld: dict, html: str) -> dict:
    product = (ld or {}).get("product") or {}
    offers = product.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    addl = product.get("additionalProperty") or []
    props = {}
    for p in addl:
        if isinstance(p, dict):
            props[p.get("name")] = p.get("value")
    cert = product.get("hasCertification") or {}
    if isinstance(cert, list):
        cert = cert[0] if cert else {}
    rating = product.get("aggregateRating") or {}

    title = product.get("name") or _html_title(html)

    return {
        "item_id": item_id,
        "url": f"https://www.ebay.com/itm/{item_id}",
        "title": title,
        "price": offers.get("price"),
        "currency": offers.get("priceCurrency"),
        "availability": offers.get("availability"),
        "item_condition": offers.get("itemCondition"),
        "grader": props.get("Grading Service"),
        "grade": props.get("Grade"),
        "cert_number": cert.get("certificationIdentification"),
        "certified_by": (cert.get("certifiedBy") or {}).get("name") if isinstance(cert.get("certifiedBy"), dict) else cert.get("certifiedBy"),
        "seller_rating": rating.get("ratingValue"),
        "seller_review_count": rating.get("reviewCount") or rating.get("ratingCount"),
        "brand": (product.get("brand") or {}).get("name") if isinstance(product.get("brand"), dict) else product.get("brand"),
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def cmd_item(args) -> None:
    ids = list(args.ids or [])
    if args.ids_file:
        for line in Path(args.ids_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                ids.append(line)
    ids = [re.sub(r"\D", "", str(i)) for i in ids if re.sub(r"\D", "", str(i))]
    if not ids:
        log("冇 item id 可跑。")
        return
    log(f"準備收割 {len(ids)} 件貨（curl_cffi，唔使 browser）")

    done = set()
    out_path = Path(args.out)
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            try:
                done.add(str(json.loads(line)["item_id"]))
            except Exception:
                pass

    s = cr.Session(impersonate="chrome")
    # warmup 首頁攞 cookie
    log("warmup：載入 ebay 首頁 ...")
    try:
        s.get("https://www.ebay.com/", headers=HEADERS, timeout=25)
    except Exception as e:
        log(f"warmup 失敗（繼續）: {e}")

    log("⚠ 注意：eBay 係 PerimeterX（JS fingerprinting），curl_cffi 過唔到挑戰頁，"
        "item 抽取多數會 ERROR 'PerimeterX challenge'。要用 Playwright 版先拎到真 JSON-LD。")

    ok, fail = 0, 0
    with out_path.open("a", encoding="utf-8") as f:
        for i, iid in enumerate(ids, 1):
            if iid in done:
                log(f"[{i}/{len(ids)}] {iid} 已有，skip")
                continue
            try:
                r = s.get(f"https://www.ebay.com/itm/{iid}", headers=HEADERS, timeout=25)
                html = r.text
                if is_px(html):
                    raise RuntimeError("PerimeterX challenge")
                ld = extract_item_jsonld(html)
                rec = normalize_item(iid, ld, html)
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                ok += 1
                log(f"[{i}/{len(ids)}] {iid} {rec.get('grader')} {rec.get('grade')} cert={rec.get('cert_number')} {rec.get('currency')} {rec.get('price')}")
            except Exception as e:
                fail += 1
                log(f"[{i}/{len(ids)}] {iid} ERROR {e}")
                f.write(json.dumps({"item_id": iid, "error": str(e), "fetched_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}, ensure_ascii=False) + "\n")
                f.flush()
            time.sleep(args.delay)
    log(f"完成：ok={ok} fail={fail} → {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="eBay 暴力收割器（curl_cffi，唔使 browser）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("item", help="逐件貨抽 Product JSON-LD")
    pi.add_argument("--ids", nargs="*", default=[])
    pi.add_argument("--ids-file", type=str)
    pi.add_argument("--out", default=str(DATA_DIR / "items.jsonl"))
    pi.add_argument("--delay", type=float, default=1.5)
    args = ap.parse_args()
    cmd_item(args)


if __name__ == "__main__":
    main()
