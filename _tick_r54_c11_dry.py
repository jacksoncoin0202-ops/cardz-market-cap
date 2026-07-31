import json, os, re, sys
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, "pipelines")
import c11_pc_sold_ingest as c11

ROOT = Path(".")
sales_meta = {
  653: {"pc": 5809563, "name": "Dragonair", "set": "Pokemon Scarlet and Violet 151 MEW", "num": "181"},
  681: {"pc": 4637112, "name": "Hisuian Zoroark VSTAR", "set": "Pokemon Sword and Shield Crown Zenith", "num": "GG56"},
  693: {"pc": 4637096, "name": "Glaceon VSTAR", "set": "Pokemon Sword and Shield Crown Zenith", "num": "GG40"},
  772: {"pc": 7800269, "name": "Durant ex", "set": "Pokemon Scarlet and Violet Surging Sparks", "num": "236"},
  784: {"pc": 11069056, "name": "Piplup", "set": "Pokemon Mega Evolution Phantasmal Flames", "num": "98"},
}

def pick_html(vid):
    cands = list((ROOT/"data/private/pricecharting_session/html/full900").glob(f"{vid}_*.html"))
    cands += list((ROOT/"data/private/pricecharting_session/html/c11").glob(f"{vid}_*.html"))
    cands = [p for p in cands if "search" not in p.name.lower()]
    cands = sorted(cands, key=lambda p: -p.stat().st_mtime)
    return cands[0] if cands else None

map_path = Path("data/runtime/private-reports/fill/A07-SALES-R54/c11_map_residual5.jsonl")
map_path.parent.mkdir(parents=True, exist_ok=True)
rows = []
for vid, m in sales_meta.items():
    html = pick_html(vid)
    if not html:
        print("NO HTML", vid); continue
    row = {
        "variant_id": vid,
        "pc_url": f"https://www.pricecharting.com/game/x/{m['name']}",
        "confidence": "high",
        "ready_for_c12": True,
        "card_name": m["name"],
        "set_name": m["set"],
        "collector_number": m["num"],
        "pc_product_id": m["pc"],
        "html_path": str(html).replace("\\", "/"),
        "notes": f"html={html.as_posix()}; source=A07-SALES-R54-full900; residual_sales_only",
        "status": "mapped",
        "mapped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    rows.append(row)
with map_path.open("w", encoding="utf-8") as fh:
    for r in rows:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
print("wrote map", map_path, "n", len(rows))

# dry-run collect
sales, stats, reports = c11.collect_sales(rows)
print("stats", dict(stats))
print("sales collected", len(sales))
by_vid = {}
for s in sales:
    by_vid.setdefault(s["variant_id"], 0)
    by_vid[s["variant_id"]] += 1
print("by_vid", by_vid)
# 30d filter locally
from datetime import date, timedelta
cutoff = date.today() - timedelta(days=30)
within = {}
for s in sales:
    d = s["sold_at"].date() if hasattr(s["sold_at"], "date") else s["sold_at"]
    if d >= cutoff:
        within[s["variant_id"]] = within.get(s["variant_id"], 0) + 1
print("within30", within)
# sample rejects from reports
for r in reports:
    print("card", r.get("variant_id"), "accepted", r.get("accepted"), "rejected", r.get("rejected") or r.get("stats"))
