import json, re, sys
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
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
print("today", date.today(), "cutoff", cutoff)

PSA10_RE = re.compile(r"\bPSA\s*10\b", re.I)
OTHER = re.compile(r"\b(?:BGS|CGC|SGC|TAG)\s*10\b", re.I)
RAW = re.compile(r"\b(?:raw|ungraded|proxy|orica|reprint)\b", re.I)
BUNDLE = re.compile(r"\b(?:lot|bundle|set of|x\s*\d+)\b", re.I)
JP = re.compile(r"\b(?:japanese|japan|jp)\b", re.I)

def parse_date(t):
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(t.strip(), fmt).date()
        except Exception:
            pass
    return None

summary = {}
for vid, m in sales_meta.items():
    # prefer newest full900 html
    cands = list((ROOT/"data/private/pricecharting_session/html/full900").glob(f"{vid}_*.html"))
    cands += list((ROOT/"data/private/pricecharting_session/html/c11").glob(f"{vid}_*.html"))
    cands = sorted(cands, key=lambda p: p.stat().st_mtime, reverse=True)
    if not cands:
        print(vid, "NO HTML")
        continue
    html_path = cands[0]
    html = html_path.read_text(encoding="utf-8", errors="replace")
    parsed = parse_product_html(html)
    # structure
    sales = []
    if isinstance(parsed, dict):
        # try common keys
        for k in ("sales", "completed_sales", "psa10_sales", "sold"):
            if k in parsed:
                sales = parsed[k]
                break
        if not sales:
            # nested by grade
            for k,v in parsed.items():
                if "manual" in str(k).lower() or "psa" in str(k).lower() or "sale" in str(k).lower():
                    if isinstance(v, list) and v:
                        sales = v
                        print("  key", k, "n", len(v), "sample keys", list(v[0]) if isinstance(v[0], dict) else type(v[0]))
    # if parse returns object
    if hasattr(parsed, "sales"):
        sales = parsed.sales
    if hasattr(parsed, "completed_sales"):
        sales = parsed.completed_sales
    print(f"\nvid={vid} html={html_path.name} parsed_type={type(parsed)}")
    if isinstance(parsed, dict):
        print("  keys", list(parsed.keys())[:30])
    elif hasattr(parsed, "__dict__"):
        print("  attrs", list(vars(parsed).keys())[:30])
        # try asdict-like
        for attr in ("sales_by_label", "sales", "completed", "psa10", "manual_only"):
            if hasattr(parsed, attr):
                val = getattr(parsed, attr)
                print(f"  .{attr}", type(val), (len(val) if hasattr(val,'__len__') else ''))
    # deeper inspect
    if not sales and isinstance(parsed, dict):
        for k,v in parsed.items():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                print("  list", k, len(v), "keys", list(v[0].keys())[:12])
