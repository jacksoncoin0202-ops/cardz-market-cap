"""Read-only: identify the 12 image_unavailable cards and explain each drift."""
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # promoted from temp/ 2026-07-26;
# depth changed temp/<f> -> docs/evidence/<folder>/<f>. Only line altered.
sys.path.insert(0, str(ROOT / "pipelines"))

import pymysql
from canonical_public_snapshot import (
    load_public_images,
    load_presentation,
    printing_key,
    row_printing_key,
    normalize_collector,
)

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


images = load_public_images()
pack_doc, cards_by_id = load_presentation(ROOT / "data/public/seed-snapshot.json")
print(f"pack cards={len(cards_by_id)}  qc by_public_id={len(images.by_public_id)}  allowed_sha={len(images.allowed_sha)}")

rows = fetch(
    """
    SELECT c.variant_id, c.rank_position, v.opaque_id, v.tcg_code, v.card_language,
           v.canonical_name, v.set_name, v.collector_number, v.identity_status
    FROM market_index_constituent c JOIN catalog_variant v ON v.id=c.variant_id
    WHERE c.index_snapshot_id=19 ORDER BY c.rank_position
    """
)

cat_key_counts = Counter()
for r in fetch("SELECT tcg_code,card_language,collector_number FROM catalog_variant"):
    cat_key_counts[row_printing_key(r)] += 1

pack_key_counts = Counter()
pack_by_key = {}
for card in cards_by_id.values():
    k = printing_key(card.get("tcg"), card.get("language"), (card.get("collectorNumber") or {}).get("normalized"))
    pack_key_counts[k] += 1
    pack_by_key.setdefault(k, card)


def image_usable(entry):
    return str((entry.get("image") or {}).get("sha256") or "") in images.allowed_sha


# ---- replicate resolver ----
claimed, skipped, tiers = set(), [], Counter()
for row in rows:
    oid = str(row["opaque_id"])
    entry = cards_by_id.get(oid)
    if entry is not None and oid not in claimed and image_usable(entry):
        claimed.add(oid); tiers["pack_id"] += 1; continue
    key = row_printing_key(row)
    cand = pack_by_key.get(key)
    unique = pack_key_counts.get(key) == 1 and cat_key_counts.get(key, 0) == 1
    if cand is not None and unique and str(cand["id"]) not in claimed and image_usable(cand):
        claimed.add(str(cand["id"])); tiers["relinked_printing_key"] += 1; continue
    if images.by_public_id.get(oid) is None:
        collides = cand is not None and (pack_key_counts.get(key, 0) > 1 or cat_key_counts.get(key, 0) > 1)
        skipped.append((row, "ambiguous_printing_key" if collides else "image_unavailable", cand, key))
        continue
    tiers["new_from_catalog"] += 1

print(f"tiers={dict(tiers)} skipped={len(skipped)}")

# ---- private asset presence ----
assets = {}
for a in fetch("SELECT variant_id, content_sha256, image_kind, private_object_key, width_px, height_px FROM market_image_asset"):
    assets.setdefault(a["variant_id"], []).append(a)

# ---- pack lookup by name for evidence ----
pack_by_name = {}
for c in cards_by_id.values():
    nm = ((c.get("name") or {}).get("en") or "").strip().casefold()
    pack_by_name.setdefault(nm, []).append(c)


def opaque(tcg, lang, set_name, coll_norm, name):
    v = "\x1f".join(p.strip().casefold() for p in (tcg, lang, set_name, coll_norm, name))
    return f"cmc_{hashlib.sha256(v.encode()).hexdigest()[:24]}"


report = []
print("\n" + "=" * 100)
for row, reason, cand, key in skipped:
    vid = row["variant_id"]
    coll = normalize_collector(row["collector_number"], row["card_language"], row["set_name"])
    recomputed = opaque(row["tcg_code"], row["card_language"], row["set_name"], coll.normalized, row["canonical_name"])
    has_asset = vid in assets
    nm = (row["canonical_name"] or "").strip().casefold()
    packmatches = pack_by_name.get(nm, [])

    print(f"\n--- rank {row['rank_position']:>3} variant={vid} reason={reason}")
    print(f"    DB now : name={row['canonical_name']!r} set={row['set_name']!r} coll={row['collector_number']!r} "
          f"tcg={row['tcg_code']} lang={row['card_language']}")
    print(f"    opaque_id       = {row['opaque_id']}")
    print(f"    recomputed      = {recomputed}  match={recomputed == row['opaque_id']}")
    print(f"    collector.norm  = {coll.normalized!r} display={coll.display!r} complete={coll.complete}")
    print(f"    printing_key    = {key}  pack_n={pack_key_counts.get(key,0)} cat_n={cat_key_counts.get(key,0)}")
    print(f"    in QC by_public_id? {row['opaque_id'] in images.by_public_id}")
    print(f"    private asset rows = {len(assets.get(vid, []))}")
    for a in assets.get(vid, []):
        print(f"        kind={a['image_kind']} {a['width_px']}x{a['height_px']} sha={a['content_sha256'][:16]}… "
              f"in_allowed_sha={a['content_sha256'] in images.allowed_sha} key={a['private_object_key'][:70]}")
    if cand is not None:
        cc = cand.get("collectorNumber") or {}
        print(f"    PACK cand by key: id={cand['id']} name={(cand.get('name') or {}).get('en')!r} "
              f"set={(cand.get('set') or {}).get('en')!r} coll={cc.get('display')!r} "
              f"image_sha={str((cand.get('image') or {}).get('sha256') or '')[:16]}… usable={image_usable(cand)}")
    for p in packmatches:
        cc = p.get("collectorNumber") or {}
        print(f"    PACK by NAME    : id={p['id']} set={(p.get('set') or {}).get('en')!r} coll={cc.get('display')!r} "
              f"norm={cc.get('normalized')!r} tcg={p.get('tcg')} lang={p.get('language')} "
              f"image_usable={image_usable(p)} id_in_qc={p['id'] in images.by_public_id}")
    report.append({
        "rank": row["rank_position"], "variant_id": vid, "reason": reason,
        "name": row["canonical_name"], "set_name": row["set_name"],
        "collector": row["collector_number"], "tcg": row["tcg_code"], "lang": row["card_language"],
        "opaque_id": row["opaque_id"], "collector_normalized": coll.normalized,
        "collector_complete": coll.complete,
        "printing_key": list(key), "pack_key_n": pack_key_counts.get(key, 0),
        "cat_key_n": cat_key_counts.get(key, 0),
        "has_private_asset": has_asset,
        "assets": [{"kind": a["image_kind"], "sha": a["content_sha256"],
                    "w": a["width_px"], "h": a["height_px"],
                    "in_allowed_sha": a["content_sha256"] in images.allowed_sha,
                    "key": a["private_object_key"]} for a in assets.get(vid, [])],
        "pack_by_name": [{"id": p["id"], "set": (p.get("set") or {}).get("en"),
                          "coll": (p.get("collectorNumber") or {}).get("display"),
                          "coll_norm": (p.get("collectorNumber") or {}).get("normalized"),
                          "tcg": p.get("tcg"), "lang": p.get("language"),
                          "image_usable": image_usable(p),
                          "id_in_qc": p["id"] in images.by_public_id} for p in packmatches],
    })

(ROOT / "temp" / "img_skip_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
print("\nwrote temp/img_skip_report.json")
conn.close()
