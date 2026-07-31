import json, os, sqlite3
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone, timedelta

# DB path
db = None
for p in [
    Path("data/runtime/cardz_market_cap.db"),
    Path("data/cardz_market_cap.db"),
    Path("cardz_market_cap.db"),
]:
    if p.exists():
        db = p
        break
# search
if not db:
    for p in Path("data").rglob("*.db"):
        if "cardz" in p.name.lower() or "market" in p.name.lower():
            print("cand", p, p.stat().st_size)
print("db", db)

rep = json.loads(Path("data/runtime/private-reports/canonical-db-qc/qc_20260730_fill_r53/report.json").read_text(encoding="utf-8"))
by_vid = {c.get("variantId"): c for c in rep["cards"]}
sales_vids = [653, 681, 693, 772, 784, 1034, 1455]
for vid in sales_vids:
    c = by_vid.get(vid)
    if not c:
        print("missing", vid)
        continue
    f = c.get("facts") or {}
    print(f"\nvid={vid} tcg={c.get('tcg')} mr={c.get('marketRank')} blockers={c.get('blockers')}")
    print("  price", f.get("price"))
    print("  sales30d", f.get("sales30d"))
    print("  identity", (f.get("identity") or {}).get("set"), (f.get("identity") or {}).get("collectorNumber"))
