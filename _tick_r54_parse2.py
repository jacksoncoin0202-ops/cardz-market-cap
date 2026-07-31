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
    # prefer non-search product pages
    cands = sorted(cands, key=lambda p: (("search" in p.name.lower()), -p.stat().st_mtime))
    return cands

for vid, m in sales_meta.items():
    cands = pick_html(vid)
    print(f"\n=== vid {vid} {m['name']} # {m['num']} jp={m['jp']} ===")
    for html_path in cands[:3]:
        parsed = parse_product_html(html_path.read_text(encoding="utf-8", errors="replace"))
        sales = parsed.get("sales") or []
        psa10 = parsed.get("psa10")
        print(f"  file={html_path.name} ok={parsed.get('ok')} product={parsed.get('product')} sales_n={len(sales)} psa10_type={type(psa10)}")
        if isinstance(psa10, dict):
            print("    psa10 keys", list(psa10.keys())[:20], "price", psa10.get("price") or psa10.get("manual-only-price"))
        if isinstance(psa10, list):
            print("    psa10 list", len(psa10))
        # sales sample structure
        if sales:
            print("    sale0", sales[0] if isinstance(sales[0], dict) else sales[0])
        # filter PSA10 sales from sales list - sales may be by grade buckets
        if sales and isinstance(sales, dict):
            print("    sales keys", list(sales.keys())[:20])
            for k,v in sales.items():
                if isinstance(v, list):
                    print(f"      {k}: {len(v)}")
        # if list of sales with grade field
        if sales and isinstance(sales, list) and sales and isinstance(sales[0], dict):
            grades = Counter(s.get("grade") or s.get("label") or s.get("condition") for s in sales)
            print("    grades", grades)
            within = 0
            accept = 0
            rej = Counter()
            for s in sales:
                title = str(s.get("title") or "")
                d = parse_date(str(s.get("date") or s.get("sold_at") or ""))
                grade = str(s.get("grade") or s.get("label") or "")
                is_psa10 = PSA10_RE.search(title) or "psa 10" in grade.lower() or grade.lower() in ("psa 10", "manual-only", "manual only")
                # PC often separates by bucket - check label
                if not is_psa10 and "10" in grade and "psa" not in grade.lower() and not PSA10_RE.search(title):
                    # might still be PSA10 bucket
                    pass
                if d and d >= cutoff:
                    within += 1
                # apply c11 filters loosely
                if not PSA10_RE.search(title) and "PSA 10" not in grade:
                    # check if from psa10 bucket via key - later
                    rej["not_psa10_title"] += 1
                    continue
                if OTHER.search(title) or RAW.search(title):
                    rej["conflict"] += 1
                    continue
                if BUNDLE.search(title):
                    rej["bundle"] += 1
                    continue
                if m["jp"] and not JP.search(title):
                    rej["lang"] += 1
                    continue
                if d and d >= cutoff:
                    accept += 1
            print(f"    within30d_any={within} accept_psa10_30d={accept} rej={dict(rej)}")
            # show recent 5
            dated = []
            for s in sales:
                d = parse_date(str(s.get("date") or ""))
                if d:
                    dated.append((d, s.get("title","")[:80], s.get("price_usd") or s.get("price")))
            dated.sort(reverse=True)
            for row in dated[:5]:
                print("     ", row)
