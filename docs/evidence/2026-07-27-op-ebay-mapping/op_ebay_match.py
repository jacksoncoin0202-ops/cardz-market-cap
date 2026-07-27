# -*- coding: utf-8 -*-
"""132 張 OP 目標卡 vs 現有 eBay identity/altxyz 資產 — 分類 matching（唯讀，唔寫 DB）"""
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / "docs/evidence/2026-07-27-op-price-mainline/target_list_132.jsonl"
IDENTITY_DUMP = REPO / "temp/ebay_identity_full.txt"
ALTXYZ = REPO.parent / "grade10-scraper/data/cards/altxyz"
OUT = REPO / "temp/mapping_classification.json"


def norm_cn(cn: str) -> str:
    """collector_number 標準化：upper、去空格；OP05119 → OP05-119 統一帶 hyphen"""
    cn = (cn or "").strip().upper().replace(" ", "")
    m = re.fullmatch(r"(OP|ST|EB|PRB)(\d{2})-?(\d{3})", cn)
    if m:
        return f"{m.group(1)}{m.group(2)}-{m.group(3)}"
    return cn


# 1. 讀 target 132
targets = []
for line in TARGET.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line:
        targets.append(json.loads(line))
assert len(targets) == 132, f"target rows = {len(targets)}"

# 2. 讀 identity dump（ro_sql 輸出格式：=== 行 + 縮排 JSON 行）
mapped = []
for line in IDENTITY_DUMP.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line.startswith("{"):
        mapped.append(json.loads(line))
print(f"identity rows loaded: {len(mapped)}")

# OP 舊批（identity 已綁）by collector_number
op_mapped_by_cn = {}
uuid_to_variant = {}
for m in mapped:
    cn = norm_cn(m["collector_number"])
    uuid_to_variant[m["external_entity_id"]] = m["variant_id"]
    op_mapped_by_cn.setdefault(cn, []).append(m)

# 3. 讀 altxyz asset_info（全部 161 個）
altxyz_assets = {}
for d in sorted(ALTXYZ.iterdir()):
    if not d.is_dir():
        continue
    rec = {"uuid": d.name, "has_psa10_file": (d / "ebay_PSA_10.json").exists()}
    p = d / "asset_info.json"
    if p.exists():
        info = json.loads(p.read_text(encoding="utf-8"))
        rec["title"] = info.get("fullTitle", "")
        rec["cardId"] = norm_cn(info.get("cardId", ""))
        rec["lang"] = info.get("language", "")
    else:
        rec["title"] = None
        rec["cardId"] = None
    rec["bound_variant"] = uuid_to_variant.get(d.name)
    altxyz_assets[d.name] = rec

unbound = [a for a in altxyz_assets.values() if a["bound_variant"] is None]
print(f"altxyz dirs: {len(altxyz_assets)}, unbound UUIDs: {len(unbound)}")
for a in unbound:
    print(f"  UNBOUND: {a['uuid']} cardId={a['cardId']} title={(a['title'] or 'N/A')[:60]}")

# altxyz by collector_number（唯 One Piece 格式先入圍）
altxyz_by_cn = {}
for a in altxyz_assets.values():
    if a["cardId"]:
        altxyz_by_cn.setdefault(a["cardId"], []).append(a)

# 4. 分類
result = []
for t in targets:
    cn = norm_cn(t["collector_number"])
    twins = op_mapped_by_cn.get(cn, [])
    twins_en = [x for x in twins if x["card_language"] == "en" and x["variant_id"] != t["variant_id"]]
    self_mapped = [x for x in twins if x["variant_id"] == t["variant_id"]]
    alt_assets = altxyz_by_cn.get(cn, [])
    alt_unbound = [a for a in alt_assets if a["bound_variant"] is None]

    if self_mapped:
        cat = "already-mapped"
        if self_mapped[0]["price_rows"] == 0:
            cat = "already-mapped-no-sales"
    elif alt_unbound:
        cat = "uuid-available-unbound"
    elif twins_en:
        cat = "twin-mapped"  # 同 collector_number 已有其他 variant 綁 UUID + 有價
    else:
        cat = "no-uuid-in-g10"

    result.append({
        "variant_id": t["variant_id"],
        "collector_number": cn,
        "canonical_name": t["canonical_name"],
        "set_name": t["set_name"],
        "psa10_pop": t["psa10_pop"],
        "category": cat,
        "twin_variants": [
            {"variant_id": x["variant_id"], "set_name": x["set_name"],
             "price_rows": x["price_rows"], "uuid": x["external_entity_id"]}
            for x in twins_en
        ],
        "unbound_uuids": [a["uuid"] for a in alt_unbound],
    })

# 5. 統計
from collections import Counter
stats = Counter(r["category"] for r in result)
print("\n=== 分類統計 ===")
for k, v in stats.most_common():
    print(f"  {k}: {v}")

twin_with_price = [r for r in result if r["category"] == "twin-mapped"
                   and any(tw["price_rows"] > 0 for tw in r["twin_variants"])]
print(f"\ntwin-mapped 且孿生行有價: {len(twin_with_price)}")

no_uuid = [r for r in result if r["category"] == "no-uuid-in-g10"]
print(f"no-uuid-in-g10 明細（頭 40）:")
for r in no_uuid[:40]:
    print(f"  v{r['variant_id']} {r['collector_number']} {r['canonical_name'][:40]} pop={r['psa10_pop']}")

OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\nwritten: {OUT}")
