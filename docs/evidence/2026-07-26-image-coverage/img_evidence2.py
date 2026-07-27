"""Read-only: correct side-by-side drift evidence using names/sets (plural) keys."""
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
from canonical_public_snapshot import load_public_images, load_presentation, normalize_collector

conn = pymysql.connect(
    host=os.environ.get("CARDZ_DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("CARDZ_DB_PORT", "3306")),
    user=os.environ.get("CARDZ_DB_USER", "root"),
    password=os.environ.get("CARDZ_DB_PASSWORD", ""),
    database=os.environ.get("CARDZ_DB_NAME", ""),
    charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
)
def fetch(sql, args=()):
    with conn.cursor() as cur:
        cur.execute(sql, args); return cur.fetchall()

def opaque(tcg, lang, set_name, coll_norm, name):
    v = "\x1f".join(p.strip().casefold() for p in (tcg, lang, set_name, coll_norm, name))
    return f"cmc_{hashlib.sha256(v.encode()).hexdigest()[:24]}"

images = load_public_images()
_, pack_by_id = load_presentation(ROOT / "data/public/seed-snapshot.json")
cat = fetch("SELECT id,opaque_id,tcg_code,card_language,canonical_name,set_name,collector_number FROM catalog_variant")
cat_ids = {r["opaque_id"] for r in cat}
ranked = {r["opaque_id"]: r for r in fetch(
    "SELECT c.rank_position, v.opaque_id, v.id vid FROM market_index_constituent c "
    "JOIN catalog_variant v ON v.id=c.variant_id WHERE c.index_snapshot_id=19")}

cat_by_key = {}
for r in cat:
    coll = normalize_collector(r["collector_number"], r["card_language"], r["set_name"])
    cat_by_key.setdefault((str(r["tcg_code"]).casefold(), str(r["card_language"]).casefold(), coll.normalized), []).append((r, coll))

orphans = set(images.by_public_id) - cat_ids
pairs = []
for oid in orphans:
    c = pack_by_id.get(oid)
    if c is None:
        continue
    pn = (c.get("names") or {}).get("en")
    ps = (c.get("sets") or {}).get("en")
    pc = (c.get("collectorNumber") or {})
    k = (str(c.get("tcg")).casefold(), str(c.get("language")).casefold(), str(pc.get("normalized") or "").casefold())
    hits = cat_by_key.get(k, [])
    if len(hits) != 1:
        continue
    r, coll = hits[0]
    # verify the frozen id really is reproducible from the pack identity
    repro = opaque(c.get("tcg"), c.get("language"), ps or "", str(pc.get("normalized") or ""), pn or "")
    diffs = []
    if str(r["canonical_name"]).strip().casefold() != str(pn or "").strip().casefold():
        diffs.append("name")
    if str(r["set_name"]).strip().casefold() != str(ps or "").strip().casefold():
        diffs.append("set_name")
    if coll.normalized != str(pc.get("normalized") or "").casefold():
        diffs.append("collector")
    if str(r["tcg_code"]).casefold() != str(c.get("tcg")).casefold():
        diffs.append("tcg")
    if str(r["card_language"]).casefold() != str(c.get("language")).casefold():
        diffs.append("language")
    pairs.append({"qc_publicId": oid, "repro_from_pack": repro, "repro_ok": repro == oid,
                  "db_opaque": r["opaque_id"], "db_variant_id": r["id"],
                  "pack_name": pn, "db_name": r["canonical_name"],
                  "pack_set": ps, "db_set": r["set_name"],
                  "pack_coll": pc.get("display"), "db_coll": r["collector_number"],
                  "pack_coll_norm": pc.get("normalized"), "db_coll_norm": coll.normalized,
                  "tcg": r["tcg_code"], "lang": r["card_language"],
                  "changed": diffs, "on_board": r["opaque_id"] in ranked,
                  "rank": ranked.get(r["opaque_id"], {}).get("rank_position")})

print(f"orphan QC ids = {len(orphans)}  uniquely rematched = {len(pairs)}")
print(f"frozen id reproducible from pack identity: {sum(1 for p in pairs if p['repro_ok'])}/{len(pairs)}")
print("\nWHAT CHANGED (QC-era -> current DB):")
for k, v in Counter(",".join(p["changed"]) or "(nothing)" for p in pairs).most_common():
    print(f"   {k:30s} {v}")
print(f"\ndrifted cards that are on the current 255 board = {sum(1 for p in pairs if p['on_board'])}")

board = sorted([p for p in pairs if p["on_board"]], key=lambda x: x["rank"])
print("\n" + "=" * 108)
print("SIDE-BY-SIDE  (QC/pack frozen identity  vs  current DB identity)")
print("=" * 108)
for p in board[:8]:
    print(f"\n### rank {p['rank']}  variant_id={p['db_variant_id']}   changed={p['changed']}")
    print(f"  id    QC/pack : {p['qc_publicId']}   (reproducible from pack identity: {p['repro_ok']})")
    print(f"        DB now  : {p['db_opaque']}")
    print(f"  name  QC/pack : {p['pack_name']!r}")
    print(f"        DB now  : {p['db_name']!r}")
    print(f"  set   QC/pack : {p['pack_set']!r}")
    print(f"        DB now  : {p['db_set']!r}")
    print(f"  coll  QC/pack : {p['pack_coll']!r} / norm {p['pack_coll_norm']!r}")
    print(f"        DB now  : {p['db_coll']!r} / norm {p['db_coll_norm']!r}")
    print(f"  tcg/lang      : {p['tcg']}/{p['lang']}  (unchanged)")

(ROOT / "temp" / "img_drift_pairs2.json").write_text(json.dumps(pairs, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\nwrote temp/img_drift_pairs2.json ({len(pairs)} pairs)")
conn.close()
