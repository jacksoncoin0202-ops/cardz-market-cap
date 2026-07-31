import sys, json
from pathlib import Path
from pprint import pprint
sys.path.insert(0, "pipelines")
from pricecharting_page_parse import parse_product_html
html = Path("data/private/pricecharting_session/html/full900/653_dragonair-181.html").read_text(encoding="utf-8", errors="replace")
p = parse_product_html(html)
psa10 = p.get("psa10")
print(type(psa10), list(psa10.keys()) if isinstance(psa10, dict) else psa10)
cs = psa10.get("completed_sales") if isinstance(psa10, dict) else None
print("completed type", type(cs))
if isinstance(cs, dict):
    print("keys", list(cs.keys())[:10])
    for k,v in list(cs.items())[:2]:
        print(k, type(v), v if not isinstance(v,(list,dict)) else (len(v) if hasattr(v,'__len__') else v))
elif isinstance(cs, list):
    print("len", len(cs), "0", cs[0] if cs else None)
else:
    print(repr(cs)[:500])
# also manual-only bucket
sales = p.get("sales") or {}
mo = sales.get("completed-auctions-manual-only")
print("manual-only type", type(mo), (len(mo) if hasattr(mo,'__len__') else None))
if isinstance(mo, list) and mo:
    pprint(mo[0])
    print("n", len(mo))
    # dates
    for s in mo[:8]:
        print(s.get("date"), s.get("price_usd") or s.get("price"), (s.get("title") or "")[:70])
