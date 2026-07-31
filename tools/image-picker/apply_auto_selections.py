# -*- coding: utf-8 -*-
"""Apply auto_selections (G10 > SNK) → DB QC + image-qc.json + public assets.

Usage:
  python -X utf8 tools/image-picker/apply_auto_selections.py
  python -X utf8 tools/image-picker/apply_auto_selections.py --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pipelines"))

from qualified_pool_operator import db, load_env  # noqa: E402

OUT = Path(__file__).resolve().parent / "public" / "data"
AUTO = OUT / "auto_selections.json"
PUBLIC = ROOT / "data" / "public" / "market-assets"
WEB = ROOT / "apps" / "web" / "public" / "market-assets"
LANDING = ROOT / "data" / "runtime" / "private-landing"
QC_MANIFEST = ROOT / "manifests" / "image-qc.json"
QC_VERSION = "auto-g10-snk-v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_source_file(private_key: str | None, sha: str) -> Path | None:
    """Find original bytes for a content sha."""
    for base in (PUBLIC, WEB):
        p = base / f"{sha}.webp"
        if p.is_file():
            return p
    if private_key:
        # g10/full/{run}/payload/images/{file}
        rel = private_key.replace("\\", "/").lstrip("/")
        cand = LANDING / rel
        if cand.is_file():
            return cand
        # also try under private-landing root without prefix issues
        for p in LANDING.glob(f"**/{Path(rel).name}"):
            if p.is_file():
                return p
    # search landing by re-hash webp/jpg matching sha (slow path skipped)
    return None


def ensure_public_webp(src: Path, sha: str) -> dict:
    """Write master webp + 200/600 derivatives into PUBLIC (and WEB mirror)."""
    PUBLIC.mkdir(parents=True, exist_ok=True)
    WEB.mkdir(parents=True, exist_ok=True)
    master = PUBLIC / f"{sha}.webp"
    if src.suffix.lower() == ".webp" and src.resolve() != master.resolve():
        if not master.is_file() or master.stat().st_size != src.stat().st_size:
            shutil.copy2(src, master)
    elif not master.is_file():
        im = Image.open(src).convert("RGBA")
        im.save(master, "WEBP", quality=90, method=4)
    # dimensions
    with Image.open(master) as im:
        w, h = im.size
        # derivatives
        for size in (200, 600):
            dest = PUBLIC / f"{sha}_{size}.webp"
            if not dest.is_file():
                thumb = im.copy()
                thumb.thumbnail((size, size * 2), Image.Resampling.LANCZOS)
                # keep aspect on card height bias
                thumb.save(dest, "WEBP", quality=85, method=4)
    # mirror to web
    for name in (f"{sha}.webp", f"{sha}_200.webp", f"{sha}_600.webp"):
        s = PUBLIC / name
        if s.is_file():
            d = WEB / name
            if not d.is_file() or d.stat().st_size != s.stat().st_size:
                shutil.copy2(s, d)
    # verify hash of master matches expected when source was webp of that sha
    raw = master.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    return {"width": w, "height": h, "path": str(master), "shaMatch": got == sha, "gotSha": got}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rebuild-auto", action="store_true", help="run build_worklist first")
    args = ap.parse_args()
    if not args.dry_run:
        raise SystemExit(
            "apply_auto_selections public writer permanently disabled: use the gated image review pipeline"
        )

    if args.rebuild_auto:
        import subprocess

        r = subprocess.run(
            [sys.executable, "-X", "utf8", str(ROOT / "tools/image-picker/build_worklist.py")],
            cwd=str(ROOT),
        )
        if r.returncode != 0:
            return r.returncode

    if not AUTO.is_file():
        print("missing auto_selections.json — run build_worklist.py first", file=sys.stderr)
        return 2

    doc = json.loads(AUTO.read_text(encoding="utf-8"))
    selections = doc.get("selections") or {}
    load_env()
    conn = db()
    cur = conn.cursor()

    # map asset_id → private key + dims
    asset_ids = [int(s["assetId"]) for s in selections.values() if s.get("assetId")]
    if not asset_ids:
        print("no asset ids")
        return 1
    ph = ",".join(str(i) for i in asset_ids)
    cur.execute(
        f"""
        SELECT a.id, a.variant_id, a.content_sha256, a.private_object_key,
               a.width_px, a.height_px, v.opaque_id, v.canonical_name
        FROM market_image_asset a
        JOIN catalog_variant v ON v.id=a.variant_id
        WHERE a.id IN ({ph})
        """
    )
    by_asset = {int(r["id"]): dict(r) for r in cur.fetchall()}

    # all assets per variant for demotion
    vids = sorted({int(r["variant_id"]) for r in by_asset.values()})
    vph = ",".join(str(v) for v in vids) if vids else "0"
    cur.execute(
        f"""
        SELECT id, variant_id FROM market_image_asset WHERE variant_id IN ({vph})
        """
    )
    assets_by_vid: dict[int, list[int]] = {}
    for r in cur.fetchall():
        assets_by_vid.setdefault(int(r["variant_id"]), []).append(int(r["id"]))

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stats = {
        "picked": 0,
        "promoted": 0,
        "demoted": 0,
        "copied": 0,
        "missingFile": 0,
        "shaMismatch": 0,
        "qcRows": 0,
    }
    records: list[dict] = []

    for vid_s, sel in selections.items():
        aid = int(sel.get("assetId") or 0)
        row = by_asset.get(aid)
        if not row:
            continue
        stats["picked"] += 1
        sha = str(row["content_sha256"] or sel.get("contentSha256") or "")
        key = row.get("private_object_key")
        src = resolve_source_file(key, sha)
        width = int(row.get("width_px") or 0)
        height = int(row.get("height_px") or 0)
        if src is None:
            stats["missingFile"] += 1
            # still mark QC if public already has file
            if not (PUBLIC / f"{sha}.webp").is_file():
                continue
            src = PUBLIC / f"{sha}.webp"
        if not args.dry_run:
            info = ensure_public_webp(src, sha)
            stats["copied"] += 1
            if not info.get("shaMatch") and src.suffix.lower() != ".webp":
                # jpg converted — update sha to actual content hash for FE
                # keep DB sha as content identity of original ingest; FE needs file named by DB sha
                pass
            if info.get("width"):
                width = info["width"]
                height = info["height"]
            if not info.get("shaMatch") and (PUBLIC / f"{sha}.webp").is_file():
                # if file was named by expected sha but bytes differ, still use for FE if we copied from g10 jpg
                # Re-hash and if mismatch, write under got sha AND keep symlink-like copy under expected?
                # Safer: if mismatch after convert, rename master to gotSha and update binding
                got = info["gotSha"]
                if got != sha:
                    stats["shaMismatch"] += 1
                    # For G10 jpg→webp convert, content sha changes. Update asset? fail-closed: keep expected name only if we hash-named correctly
                    # store_normalized would fix; for now copy as-is under DB sha name (already done) even if hash differs — FE loads by path
                    pass
        else:
            if not (PUBLIC / f"{sha}.webp").is_file() and src:
                stats["copied"] += 1  # would copy

        if width <= 0 or height <= 0:
            try:
                with Image.open(PUBLIC / f"{sha}.webp") as im:
                    width, height = im.size
            except Exception:
                width, height = 429, 600

        opaque = row["opaque_id"]
        records.append(
            {
                "cardNumberMatch": True,
                "contentSha256": sha,
                "height": height,
                "imageKind": "raw_front",
                "languageMatch": True,
                "publicAllowed": True,
                "publicId": opaque,
                "qcAt": utc_now(),
                "qcVersion": QC_VERSION,
                "resolverEvidence": {
                    "method": "auto_g10_then_snk",
                    "variantId": int(row["variant_id"]),
                    "assetId": aid,
                    "channel": (sel.get("provenance") or {}).get("channel"),
                    "autoReason": sel.get("autoReason"),
                },
                "semanticMatchStatus": "source_id_exact",
                "tcgMatch": True,
                "width": width,
            }
        )

        if args.dry_run:
            continue

        # promote chosen QC
        cur.execute(
            """
            INSERT INTO market_image_qc
                (image_asset_id, semantic_match_status, card_number_match, language_match,
                 tcg_match, raw_front_confirmed, public_allowed, rejection_reason,
                 checked_at, qc_version)
            VALUES (%s, 'source_id_exact', 1, 1, 1, 1, 1, NULL, %s, %s)
            ON DUPLICATE KEY UPDATE
                semantic_match_status='source_id_exact',
                card_number_match=1, language_match=1, tcg_match=1,
                raw_front_confirmed=1, public_allowed=1, rejection_reason=NULL,
                checked_at=VALUES(checked_at), qc_version=VALUES(qc_version)
            """,
            (aid, now, QC_VERSION),
        )
        stats["promoted"] += 1
        stats["qcRows"] += 1

        # demote other assets for variant
        for other in assets_by_vid.get(int(row["variant_id"]), []):
            if other == aid:
                continue
            cur.execute(
                """
                INSERT INTO market_image_qc
                    (image_asset_id, semantic_match_status, card_number_match, language_match,
                     tcg_match, raw_front_confirmed, public_allowed, rejection_reason,
                     checked_at, qc_version)
                VALUES (%s, 'auto_demoted_non_canonical', 0, 0, 0, 0, 0,
                        'not_selected_g10_snk_policy', %s, %s)
                ON DUPLICATE KEY UPDATE
                    public_allowed=0,
                    rejection_reason='not_selected_g10_snk_policy',
                    semantic_match_status='auto_demoted_non_canonical',
                    checked_at=VALUES(checked_at), qc_version=VALUES(qc_version)
                """,
                (other, now, QC_VERSION),
            )
            stats["demoted"] += 1

    if not args.dry_run:
        conn.commit()
        # one record per publicId (last wins — selections unique by variant)
        by_pub: dict[str, dict] = {}
        for r in records:
            by_pub[r["publicId"]] = r
        # merge: keep non-overlapping old public records for ids not in this apply?
        # Fail-closed for FE: rewrite full manifest from this canonical set only
        # so wrong plan_a images cannot win.
        manifest = {
            "schemaVersion": 1,
            "generatedAt": utc_now(),
            "source": "apply_auto_selections",
            "note": "G10 > SNK image policy; one publicAllowed raw_front per opaque_id",
            "records": list(by_pub.values()),
        }
        QC_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        QC_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"stats": stats, "records": len(records), "dryRun": args.dry_run}, indent=2))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
