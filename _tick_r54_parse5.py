import sys, json, re
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

def rows_from_parsed(parsed):
    psa10 = parsed.get("psa10") or {}
    cs = psa10.get("completed_sales") if isinstance(psa10, dict) else None
    if isinstance(cs, dict) and isinstance(cs.get("rows"), list):
        return cs["rows"]
    sales = parsed.get("sales") or {}
    mo = sales.get("completed-auctions-manual-only")
    if isinstance(mo, dict) and isinstance(mo.get("rows"), list):
        return mo["rows"]
    if isinstance(mo, list):
        return mo
    return []

def pick_html(vid):
    cands = list((ROOT/"data/private/pricecharting_session/html/full900").glob(f"{vid}_*.html"))
    cands += list((ROOT/"data/private/pricecharting_session/html/c11").glob(f"{vid}_*.html"))
    cands = sorted(cands, key=lambda p: (("search" in p.name.lower()), -p.stat().st_mtime))
    return cands

summary = {}
for vid, m in sales_meta.items():
    print(f"\n=== vid {vid} {m['name']} #{m['num']} jp={m['jp']} ===")
    for html_path in pick_html(vid)[:2]:
        parsed = parse_product_html(html_path.read_text(encoding="utf-8", errors="replace"))
        if not parsed.get("ok"):
            print(" bad", html_path.name)
            continue
        rows = rows_from_parsed(parsed)
        print(f" {html_path.name} rows={len(rows)} pid={(parsed.get('product') or {}).get('id')}")
        if rows:
            print("  sample keys", list(rows[0].keys()) if isinstance(rows[0], dict) else type(rows[0]))
            print("  sample", rows[0])
        accept=[]; rej=Counter(); dates=[]
        for s in rows:
            title = str(s.get("title") or "")
            itm = str(s.get("ebay_itm") or s.get("item_id") or s.get("id") or "")
            price = s.get("price_usd") if s.get("price_usd") is not None else s.get("price")
            d = parse_date(str(s.get("date") or ""))
            if d: dates.append(d)
            if not title:
                rej["no_title"]+=1; continue
            # PSA10 bucket already - title may or may not say PSA 10
            if OTHER.search(title) or RAW.search(title):
                rej["conflict"]+=1; continue
            if BUNDLE.search(title):
                rej["bundle"]+=1; continue
            if m["jp"] and not JP.search(title):
                rej["lang"]+=1
                # still allow if collector match later; count both
            if d is None:
                rej["no_date"]+=1; continue
            if d < cutoff:
                rej["old"]+=1; continue
            if price is None or float(price)<=0:
                rej["price"]+=1; continue
            if not re.fullmatch(r"\d{9,15}", itm):
                # try extract from url
                url = str(s.get("url") or s.get("href") or "")
                mm = re.search(r"/itm/(\d{9,15})", url) or re.search(r"(\d{9,15})", itm)
                itm = mm.group(1) if mm else itm
            accept.append({"date": str(d), "price": float(price), "title": title[:100], "itm": itm})
        dates_sorted = sorted(dates, reverse=True)[:5]
        print(f"  accept30={len(accept)} rej={dict(rej)} latest_dates={dates_sorted}")
        for a in accept[:6]:
            print("   ", a)
        if vid not in summary or len(accept) > summary[vid].get("n",0):
            summary[vid] = {"n": len(accept), "html": str(html_path), "pid": (parsed.get("product") or {}).get("id"), "accept": accept[:20], "rej": dict(rej), "latest": [str(x) for x in dates_sorted]}

print("\nSUMMARY")
for vid, s in summary.items():
    print(vid, s["n"], s["html"], s["latest"][:3], s["rej"])
