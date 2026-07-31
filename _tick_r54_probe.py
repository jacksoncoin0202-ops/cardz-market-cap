import json
from pathlib import Path
from collections import Counter

rep = json.loads(Path("data/runtime/private-reports/canonical-db-qc/qc_20260730_fill_r53/report.json").read_text(encoding="utf-8"))
cards = rep["cards"]
ready = [c for c in cards if c.get("decision") == "passed"]
print("ready", len(ready), Counter((c.get("tcg") or "") for c in ready))

filters = {
    "poke_mr<=100": lambda c: (c.get("tcg") or "").lower() == "pokemon" and isinstance(c.get("marketRank"), int) and c["marketRank"] <= 100,
    "op_mr<=100": lambda c: (c.get("tcg") or "").lower() == "one-piece" and isinstance(c.get("marketRank"), int) and c["marketRank"] <= 100,
    "all_mr<=100": lambda c: isinstance(c.get("marketRank"), int) and c["marketRank"] <= 100,
    "watch_101_300": lambda c: isinstance(c.get("marketRank"), int) and 101 <= c["marketRank"] <= 300,
}
for board_name, filt in filters.items():
    subset = [c for c in cards if filt(c)]
    passed = [c for c in subset if c.get("decision") == "passed"]
    blocked = [c for c in subset if c.get("decision") != "passed"]
    combos = Counter(tuple(sorted(c.get("blockers") or [])) for c in blocked)
    print(f"\n=== {board_name}: n={len(subset)} ready={len(passed)} blocked={len(blocked)}")
    for combo, n in combos.most_common(12):
        label = combo if combo else ("none",)
        print(f"  {n:3d} {label}")

# residual classes on mr<=300
print("\n=== residual class counts mr<=300 ===")
cls = Counter()
price_only = []
sales_only = []
sale_src = []
other = []
for c in cards:
    mr = c.get("marketRank")
    if not isinstance(mr, int) or mr > 300:
        continue
    if c.get("decision") == "passed":
        continue
    b = set(c.get("blockers") or [])
    price_b = "exact_psa10_price_missing" in b
    sales_b = "psa10_sales_30d_missing" in b
    if price_b and not sales_b and b <= {"exact_psa10_price_missing", "market_cap_not_materialized"}:
        cls["price_only(+cap)"] += 1
        price_only.append(c)
    elif sales_b and not price_b and b <= {"psa10_sales_30d_missing", "market_cap_not_materialized", "sale_source_identity_not_exact"}:
        cls["sales_only(+cap/sale_src)"] += 1
        sales_only.append(c)
    elif "sale_source_identity_not_exact" in b:
        cls["has_sale_source"] += 1
        sale_src.append(c)
    elif "price_source_identity_not_exact" in b:
        cls["price_identity"] += 1
    else:
        cls["other"] += 1
        other.append(c)
print(cls)
print("price_only n", len(price_only), "sales_only n", len(sales_only), "sale_src n", len(sale_src))
print("sales_only vids", sorted(c.get("variantId") for c in sales_only))
print("sale_src vids", [(c.get("variantId"), c.get("blockers"), c.get("tcg"), c.get("marketRank")) for c in sale_src])
# other sample
print("other sample combos", Counter(tuple(sorted(c.get("blockers") or [])) for c in other).most_common(10))
