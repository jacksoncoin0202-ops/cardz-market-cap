import json, re, sys
from pathlib import Path
from datetime import date, datetime, timedelta
from collections import Counter
sys.path.insert(0, "pipelines")
from pricecharting_page_parse import parse_product_html

ROOT = Path(".")
sales_meta = {
  653: {"pc": 5809563, "name": "Dragonair", "set": "Pokemon Scarlet and Violet 151 MEW", "num": "181", "jp": False},
  681: {"pc": 4637112, "name": "Hisuian Zoroark VSTAR", "set": "Pokemon Sword and Shield Crown Zenith", "num": "GG56", "jp": False},
  693: {"pc": 4637096, "name": "Glaceon VSTAR", "set": "Pokemon Sword and Shield Crown Zenith", "num": "GG40", "jp": False},
  772: {"pc": 7800269, "name": "Durant ex", "set": "Pokemon Scarlet and Violet Surging Sparks", "num": "236", "jp": False},
  784: {"pc": 11069056, "name": "Piplup", "set": "Pokemon Mega Evolution Phantasmal Flames", "num": "98", "jp": False},
  1034: {"pc": 5809440, "name": "Arcanine", "set": "Pokemon Japanese Card 151", "num": "59", "jp": True},
  1455: {"pc": 5809387, "name": "Squirtle", "set": "Pokemon Japanese Card 151", "num": "7", "jp": True},
}
cutoff = date.today() - timedelta(days=30)
print("cutoff", cutoff)
PSA10_RE = re.compile(r"\bPSA\s*10\b", re.I)
OTHER = re.compile(r"\b(?:BGS|CGC|SGC|TAG)\s*10\b", re.I)
RAW = re.compile(r"\b(?:raw|ungraded|proxy|orica|reprint)\b", re.I)
BUNDLE = re.compile(r"\b(?:lot|bundle|set of|x\s*\d+)\b", re.I)
JP = re.compile(r"\b(?:japanese|japan|jp)\b", re.I)

def parse_date(t):
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime((t or "").strip(), fmt).date()
        except Exception:
            pass
    return None

def pick_html(vid):
    cands = list((ROOT/"data/private/pricecharting_session/html/full900").glob(f"{vid}_*.html"))
    cands += list((ROOT/"data/private/pricecharting_session/html/c11").glob(f"{vid}_*.html"))
    cands = sorted(cands, key=lambda p: (("search" in p.name.lower()), -p.stat().st_mtime))
    return cands

out = {}
for vid, m in sales_meta.items():
    cands = pick_html(vid)
    print(f"\n=== vid {vid} {m['name']} #{m['num']} ===")
    best = None
    for html_path in cands:
        parsed = parse_product_html(html_path.read_text(encoding="utf-8", errors="replace"))
        if not parsed.get("ok"):
            print("  skip bad", html_path.name)
            continue
        psa10 = parsed.get("psa10") or {}
        completed = []
        if isinstance(psa10, dict):
            completed = psa10.get("completed_sales") or []
        # also sales dict buckets
        sales_dict = parsed.get("sales") or {}
        print(f"  {html_path.name} product_id={ (parsed.get('product') or {}).get('id') } psa10_completed={len(completed)} sales_keys={list(sales_dict.keys())[:8] if isinstance(sales_dict, dict) else type(sales_dict)}")
        if completed:
            print("   sample", completed[0])
        # count 30d
        accept = []
        rej = Counter()
        for s in completed:
            if not isinstance(s, dict):
                continue
            title = str(s.get("title") or "")
            itm = str(s.get("ebay_itm") or s.get("item_id") or s.get("id") or "")
            price = s.get("price_usd") if s.get("price_usd") is not None else s.get("price")
            d = parse_date(str(s.get("date") or s.get("sold_at") or ""))
            if not title:
                rej["no_title"] += 1
                continue
            if not PSA10_RE.search(title):
                # completed_sales under psa10 may omit PSA 10 in title sometimes
                pass
            if OTHER.search(title) or RAW.search(title):
                rej["conflict"] += 1
                continue
            if BUNDLE.search(title):
                rej["bundle"] += 1
                continue
            if m["jp"] and not JP.search(title):
                rej["lang"] += 1
                # still count alternate
            if d is None:
                rej["no_date"] += 1
                continue
            if d < cutoff:
                rej["old"] += 1
                continue
            if price is None or float(price) <= 0:
                rej["price"] += 1
                continue
            accept.append({"date": str(d), "price": price, "title": title[:90], "itm": itm})
        print(f"   accept30={len(accept)} rej={dict(rej)}")
        for a in accept[:5]:
            print("    ", a)
        if accept and best is None:
            best = {"html": str(html_path), "n": len(accept), "accept": accept, "product_id": (parsed.get("product") or {}).get("id")}
    out[vid] = best

print("\n=== SUMMARY ===")
for vid, b in out.items():
    print(vid, "NONE" if not b else f"n={b['n']} html={b['html']} pid={b['product_id']}")
Path("data/runtime/private-reports/fill/MAIN-PROGRESS/_r54_sales_parse.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
