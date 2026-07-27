"""Read-only audit: QC publicId vs current catalog opaque_id drift."""
import hashlib
import json
import os
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parents[3]  # promoted from temp/ 2026-07-26;
# depth changed temp/<f> -> docs/evidence/<folder>/<f>. Only line altered.
QC = ROOT / "manifests" / "image-qc.json"
ASSET_DIR = ROOT / "data" / "public" / "market-assets"

conn = pymysql.connect(
    host=os.environ.get("CARDZ_DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("CARDZ_DB_PORT", "3306")),
    user=os.environ.get("CARDZ_DB_USER", "root"),
    password=os.environ.get("CARDZ_DB_PASSWORD", ""),
    database=os.environ.get("CARDZ_DB_NAME", ""),
    charset="utf8mb4",
    cursorclass=pymysql.cursors.DictCursor,
)


def fetch(sql, args=()):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


# ---- 1. QC records that survive the exact public gate ----
doc = json.loads(QC.read_text(encoding="utf-8"))
qc_pass = {}
qc_raw_total = 0
for rec in doc.get("records", []):
    qc_raw_total += 1
    if not rec.get("publicAllowed"):
        continue
    if str(rec.get("imageKind") or "") != "raw_front":
        continue
    sha = str(rec.get("contentSha256") or "")
    w, h = rec.get("width"), rec.get("height")
    if len(sha) != 64 or not w or not h:
        continue
    if not (ASSET_DIR / f"{sha}.webp").is_file():
        continue
    pid = str(rec.get("publicId") or "")
    if not pid or pid in qc_pass:
        continue
    qc_pass[pid] = rec

print(f"QC records total={qc_raw_total} pass_public_gate={len(qc_pass)}")

# ---- 2. ranking cards (index_snapshot_id=19) ----
rank_rows = fetch(
    """
    SELECT c.variant_id, c.rank_position, v.opaque_id, v.tcg_code, v.card_language,
           v.canonical_name, v.set_name, v.collector_number, v.identity_status
    FROM market_index_constituent c
    JOIN catalog_variant v ON v.id = c.variant_id
    WHERE c.index_snapshot_id = 19
    ORDER BY c.rank_position
    """
)
rank_ids = {r["opaque_id"]: r for r in rank_rows}
print(f"ranking cards (snapshot 19) = {len(rank_rows)}, distinct opaque_id = {len(rank_ids)}")

# ---- 3. full catalog ----
cat_rows = fetch(
    "SELECT id, opaque_id, tcg_code, card_language, canonical_name, set_name, collector_number FROM catalog_variant"
)
cat_ids = {r["opaque_id"]: r for r in cat_rows}
print(f"catalog opaque_id = {len(cat_ids)}")

# ---- 4. intersections ----
qc_set = set(qc_pass)
rank_set = set(rank_ids)
cat_set = set(cat_ids)

hit_rank = qc_set & rank_set
miss_rank = rank_set - qc_set
orphan_qc = qc_set - cat_set
qc_in_cat_not_rank = (qc_set & cat_set) - rank_set

print()
print(f"[A] QC publicId               = {len(qc_set)}")
print(f"[B] ranking opaque_id         = {len(rank_set)}")
print(f"[C] QC matched ranking        = {len(hit_rank)}")
print(f"[D] ranking WITHOUT QC image  = {len(miss_rank)}")
print(f"[E] QC orphan (not in catalog)= {len(orphan_qc)}  ({len(orphan_qc)/len(qc_set)*100:.1f}% of QC)")
print(f"[F] QC in catalog but unranked= {len(qc_in_cat_not_rank)}")
print(f"[G] QC alive in catalog       = {len(qc_set & cat_set)}")

# ---- 5. how many missing-rank cards actually have a private image asset ----
sha_rows = fetch(
    "SELECT variant_id, content_sha256, image_kind FROM market_image_asset"
)
asset_by_variant = {}
for r in sha_rows:
    asset_by_variant.setdefault(r["variant_id"], []).append(r)
print(f"\nmarket_image_asset rows={len(sha_rows)} variants={len(asset_by_variant)}")

miss_rows = [rank_ids[o] for o in miss_rank]
miss_with_asset = [r for r in miss_rows if r["variant_id"] in asset_by_variant]
miss_no_asset = [r for r in miss_rows if r["variant_id"] not in asset_by_variant]
print(f"ranking-miss WITH private asset = {len(miss_with_asset)}")
print(f"ranking-miss WITHOUT any asset  = {len(miss_no_asset)}")
for r in sorted(miss_no_asset, key=lambda x: x["rank_position"]):
    print(f"   TRUE-MISSING variant={r['variant_id']} rank={r['rank_position']} "
          f"{r['canonical_name']} | {r['set_name']} | {r['collector_number']} | {r['tcg_code']}/{r['card_language']}")

# ---- 6. do the private asset shas appear in the QC manifest? ----
qc_sha_to_pid = {str(v.get("contentSha256")): k for k, v in qc_pass.items()}
recovered = []
for r in miss_with_asset:
    for a in asset_by_variant[r["variant_id"]]:
        pid = qc_sha_to_pid.get(a["content_sha256"])
        if pid:
            recovered.append((r, pid, a))
            break
print(f"\nranking-miss whose private sha IS a QC-passing image = {len(recovered)}")

# ---- 7. reconstruct: does recomputing the id from current DB fields reproduce opaque_id? ----
def opaque(tcg, lang, set_name, collector_norm, name):
    value = "\x1f".join(p.strip().casefold() for p in (tcg, lang, set_name, collector_norm, name))
    return f"cmc_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


out = {
    "qc_pass": len(qc_set),
    "ranking": len(rank_set),
    "matched": len(hit_rank),
    "ranking_miss": len(miss_rank),
    "qc_orphan": len(orphan_qc),
    "qc_alive": len(qc_set & cat_set),
    "true_missing": [
        {k: (str(v) if not isinstance(v, (int, str)) else v) for k, v in r.items()}
        for r in miss_no_asset
    ],
    "recovered_pairs": [
        {"variant_id": r["variant_id"], "rank": r["rank_position"], "current_opaque": r["opaque_id"],
         "qc_publicId": pid, "sha": a["content_sha256"], "name": r["canonical_name"],
         "set_name": r["set_name"], "collector": r["collector_number"],
         "tcg": r["tcg_code"], "lang": r["card_language"]}
        for r, pid, a in recovered
    ],
    "ranking_miss_all": [
        {"variant_id": r["variant_id"], "rank": r["rank_position"], "opaque_id": r["opaque_id"],
         "name": r["canonical_name"], "set_name": r["set_name"], "collector": r["collector_number"],
         "tcg": r["tcg_code"], "lang": r["card_language"]}
        for r in sorted(miss_rows, key=lambda x: x["rank_position"])
    ],
    "qc_orphan_ids": sorted(orphan_qc),
}
(ROOT / "temp" / "img_gap_result.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
print("\nwrote temp/img_gap_result.json")
conn.close()
