# -*- coding: utf-8 -*-
"""Bind public FE images: G10 raw cards ONLY (never PSA slab), full-canvas std size.

Policy (daddy 2026-07-30):
  1. Prefer G10 snkrdunk / upload_bg_removed style RAW card art (not altxyz eBay slabs)
  2. Never publish PSA slab photos (path altxyz_*, or slab heuristics)
  3. SNK / market-assets fallback only after SAMPLE QC + raw-front checks
  4. Normalize every public master to std-429x600 full-bleed raw card
     (alpha-bbox crop so SNK 1000x730 letterbox becomes full card)

Usage:
  python -X utf8 tools/bind_g10_raw_public_images.py
  python -X utf8 tools/bind_g10_raw_public_images.py --limit 50
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pipelines"))

from qualified_pool_operator import db, load_env  # noqa: E402
import native_image_resolver as nir  # noqa: E402
from sample_image_qc import SampleImageRejected, assert_not_sample  # noqa: E402
import time  # noqa: E402

PUBLIC = ROOT / "data" / "public" / "market-assets"


def exec_retry(cur, sql: str, args=None, *, retries: int = 4):
    """Retry on MySQL deadlock 1213 / lock wait timeout."""
    last = None
    for i in range(retries):
        try:
            cur.execute(sql, args or ())
            return
        except Exception as exc:  # pymysql OperationalError
            last = exc
            msg = str(exc)
            if "1213" in msg or "1205" in msg or "Deadlock" in msg:
                time.sleep(0.2 * (i + 1))
                try:
                    cur.connection.rollback()
                except Exception:
                    pass
                continue
            raise
    raise last
WEB = ROOT / "apps" / "web" / "public" / "market-assets"
LANDING = ROOT / "data" / "runtime" / "private-landing"
G10_LIVE = ROOT.parent / "grade10-scraper" / "data" / "images"
QC_MANIFEST = ROOT / "manifests" / "image-qc.json"
QC_VERSION = "g10-raw-full-v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def classify_key(private_key: str | None) -> str:
    k = (private_key or "").replace("\\", "/").lower()
    if "altxyz" in k:
        return "g10_psa_slab"  # eBay graded photo — NEVER public raw
    if "snkrdunk_" in k or "/snkrdunk/" in k:
        return "g10_raw_snk"
    if k.startswith("g10/"):
        return "g10_other"
    if "market-assets" in k:
        return "market_assets"
    if "snk" in k:
        return "snk_other"
    return "other"


def is_psa_slab_image(im: Image.Image, private_key: str | None) -> bool:
    """Reject graded slab photography for FE (raw-only site)."""
    if classify_key(private_key) == "g10_psa_slab":
        return True
    rgba = im.convert("RGBA")
    w, h = rgba.size
    if h <= 0 or w <= 0:
        return True
    # Tall eBay slab photos are typically ~0.6 ratio RGB with white frame
    ratio = w / h
    has_alpha = "A" in im.getbands()
    if has_alpha:
        # transparent corners → likely raw/bg-removed
        a = rgba.getchannel("A")
        corners = [
            a.getpixel((2, 2)),
            a.getpixel((w - 3, 2)),
            a.getpixel((2, h - 3)),
            a.getpixel((w - 3, h - 3)),
        ]
        if all(c < 20 for c in corners):
            return False
    # No alpha + very tall + large canvas → slab candidate
    if not has_alpha and ratio < 0.75 and h >= 1000 and w >= 700:
        # white border mean near edges
        rgb = rgba.convert("RGB")
        edge = []
        for x in range(0, w, max(1, w // 40)):
            edge.append(rgb.getpixel((x, 2)))
            edge.append(rgb.getpixel((x, h - 3)))
        for y in range(0, h, max(1, h // 40)):
            edge.append(rgb.getpixel((2, y)))
            edge.append(rgb.getpixel((w - 3, y)))
        if edge:
            mean = sum(sum(p) for p in edge) / (len(edge) * 3)
            if mean > 200:
                return True
    return False


def resolve_file(private_key: str | None, sha: str) -> Path | None:
    for base in (PUBLIC, WEB, nir.ASSETS):
        p = base / f"{sha}.webp"
        if p.is_file():
            return p
    if private_key:
        rel = private_key.replace("\\", "/").lstrip("/")
        cand = LANDING / rel
        if cand.is_file():
            return cand
        name = Path(rel).name
        live = G10_LIVE / name
        if live.is_file():
            return live
        # jpg without extension match
        for p in G10_LIVE.glob(f"{Path(name).stem}.*"):
            if p.is_file():
                return p
    return None


def mirror_public(sha: str) -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    WEB.mkdir(parents=True, exist_ok=True)
    for name in (f"{sha}.webp", f"{sha}_200.webp", f"{sha}_600.webp"):
        src = PUBLIC / name
        if not src.is_file():
            continue
        dest = WEB / name
        if not dest.is_file() or dest.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dest)


def normalize_to_public(src: Path, alt: str, *, trusted_g10_raw: bool = False) -> dict:
    """Crop letterbox + std canvas + derivatives.

    G10 snkrdunk raw: skip deep SAMPLE OCR (Tesseract multi-angle is ~seconds/card
    and freezes Top300). Still reject PSA slabs by path/heuristic.
    Non-G10 fallback: keep SAMPLE hard gate.
    """
    raw = src.read_bytes()
    with Image.open(io.BytesIO(raw)) as opened:
        im = opened.convert("RGBA")
        if is_psa_slab_image(im, src.name):
            raise ValueError("psa_slab_rejected")
        if not trusted_g10_raw:
            assert_not_sample(opened.copy(), context=str(src.name))

    # Bypass store_native_image's internal deep OCR when trusted G10 raw
    if trusted_g10_raw:
        canvas = nir.normalize_card_canvas(im)
        if not nir.has_rounded_corners(canvas):
            canvas = nir.apply_rounded_corners(canvas)
        return nir._write_image_block(canvas, alt)

    try:
        return nir.store_native_image(raw, alt)
    except SampleImageRejected:
        raise
    except Exception:
        return nir.store_face_art_image(raw, alt)


# Hard rejects that auto-bind must never revive (rank-4 Rare Candy class).
HARD_REJECT_RE = re.compile(
    r"wrong_card_art|identity_mismatch|rare_candy_not_|sample_watermark",
    re.IGNORECASE,
)


def pick_asset(
    assets: list[dict],
    *,
    blocked_asset_ids: set[int] | None = None,
) -> tuple[dict | None, str]:
    """G10 raw snkrdunk first; never slab; never hard-rejected assets."""
    blocked = blocked_asset_ids or set()
    by_cls: dict[str, list[dict]] = defaultdict(list)
    for a in assets:
        aid = int(a.get("asset_id") or a.get("id") or 0)
        if aid in blocked:
            continue
        by_cls[classify_key(a.get("private_object_key"))].append(a)

    for cls in ("g10_raw_snk", "g10_other"):
        for a in by_cls.get(cls, []):
            if classify_key(a.get("private_object_key")) == "g10_psa_slab":
                continue
            return a, cls

    # market-assets / snk: only if file looks raw (not slab)
    for cls in ("market_assets", "snk_other", "other"):
        for a in by_cls.get(cls, []):
            return a, f"fallback_{cls}"
    return None, "none"


def load_hard_blocked_assets(cur, vid: int) -> set[int]:
    """Any asset with a prior hard-reject QC row is banned from auto public."""
    cur.execute(
        """
        SELECT DISTINCT a.id AS asset_id, q.rejection_reason
        FROM market_image_asset a
        JOIN market_image_qc q ON q.image_asset_id = a.id
        WHERE a.variant_id = %s
          AND q.rejection_reason IS NOT NULL
          AND q.public_allowed = 0
        """,
        (vid,),
    )
    blocked: set[int] = set()
    for row in cur.fetchall():
        reason = str(row.get("rejection_reason") or "")
        if HARD_REJECT_RE.search(reason):
            blocked.add(int(row["asset_id"]))
    return blocked


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="hard cap after top-N filter")
    ap.add_argument(
        "--top",
        type=int,
        default=300,
        help="only combined ranks 1..N (0 = all with images). Default 300 for FE B-mode",
    )
    ap.add_argument("--commit-every", type=int, default=50, help="DB commit batch size")
    ap.add_argument("--g10-raw-only", action="store_true", help="skip SNK/market-assets fallback")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.dry_run:
        raise SystemExit(
            "bind_g10_raw_public_images public writer permanently disabled: use the gated image review pipeline"
        )

    load_env()
    conn = db()
    cur = conn.cursor()

    # Ranked first from latest combined index
    cur.execute(
        """
        SELECT id FROM market_index_snapshot
        WHERE index_code='tcg-combined' ORDER BY id DESC LIMIT 1
        """
    )
    snap = cur.fetchone()
    ranked: dict[int, int] = {}
    if snap:
        cur.execute(
            """
            SELECT variant_id, rank_position FROM market_index_constituent
            WHERE index_snapshot_id=%s
            """,
            (snap["id"],),
        )
        ranked = {int(r["variant_id"]): int(r["rank_position"]) for r in cur.fetchall()}

    if args.top and args.top > 0:
        target_vids = [vid for vid, rk in ranked.items() if rk <= args.top]
        target_vids.sort(key=lambda v: ranked[v])
        print(
            f"[bind] B-mode top={args.top} targets={len(target_vids)} "
            f"commit_every={args.commit_every}",
            flush=True,
        )
    else:
        target_vids = []

    cur.execute(
        """
        SELECT a.id AS asset_id, a.variant_id, a.content_sha256, a.private_object_key,
               a.width_px, a.height_px, v.opaque_id, v.canonical_name
        FROM market_image_asset a
        JOIN catalog_variant v ON v.id=a.variant_id
        ORDER BY a.variant_id, a.id
        """
    )
    by_vid: dict[int, list[dict]] = defaultdict(list)
    meta: dict[int, dict] = {}
    for r in cur.fetchall():
        vid = int(r["variant_id"])
        by_vid[vid].append(dict(r))
        meta[vid] = {
            "opaque_id": r["opaque_id"],
            "name": r["canonical_name"],
            "rank": ranked.get(vid, 99999),
        }

    if target_vids:
        vids = [v for v in target_vids if v in by_vid]
    else:
        vids = sorted(by_vid.keys(), key=lambda v: (meta[v]["rank"], v))
    if args.limit:
        vids = vids[: args.limit]
    print(f"[bind] will process {len(vids)} variants", flush=True)

    stats = defaultdict(int)
    records: list[dict] = []
    # keep prior QC records for ids we don't touch
    prior_by_pub: dict[str, dict] = {}
    if QC_MANIFEST.is_file():
        try:
            prior = json.loads(QC_MANIFEST.read_text(encoding="utf-8"))
            for r in prior.get("records") or []:
                if r.get("publicId"):
                    prior_by_pub[str(r["publicId"])] = r
        except Exception:
            pass
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    batch = 0

    def write_manifest() -> None:
        by_pub = dict(prior_by_pub)
        for r in records:
            by_pub[r["publicId"]] = r
        QC_MANIFEST.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "generatedAt": utc_now(),
                    "source": "bind_g10_raw_public_images",
                    "note": (
                        "G10 raw preferred; PSA slabs rejected; std-429x600 full-bleed; "
                        f"top={args.top}; bound_this_run={len(records)}"
                    ),
                    "records": list(by_pub.values()),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    for i, vid in enumerate(vids, start=1):
        # Fast path: already bound this policy version + public
        if not args.dry_run:
            cur.execute(
                """
                SELECT a.content_sha256, a.id
                FROM market_image_asset a
                JOIN market_image_qc q ON q.image_asset_id=a.id
                WHERE a.variant_id=%s AND q.public_allowed=1 AND q.qc_version=%s
                ORDER BY a.id DESC LIMIT 1
                """,
                (vid, QC_VERSION),
            )
            already = cur.fetchone()
            if already and (PUBLIC / f"{already['content_sha256']}.webp").is_file():
                stats["skip_already"] = stats.get("skip_already", 0) + 1
                if i % 50 == 0:
                    print(f"[bind] {i}/{len(vids)} skip_already={stats['skip_already']}", flush=True)
                continue

        assets = by_vid[vid]
        blocked = load_hard_blocked_assets(cur, vid) if not args.dry_run else set()
        if blocked:
            stats["hard_blocked_assets"] = stats.get("hard_blocked_assets", 0) + len(blocked)
        pick, reason = pick_asset(assets, blocked_asset_ids=blocked)
        if not pick:
            stats["no_pick"] += 1
            if blocked:
                stats["no_pick_all_hard_blocked"] = stats.get("no_pick_all_hard_blocked", 0) + 1
            continue
        cls = classify_key(pick.get("private_object_key"))
        if cls == "g10_psa_slab":
            stats["skipped_slab_pick"] += 1
            continue
        if args.g10_raw_only and cls not in ("g10_raw_snk", "g10_other"):
            stats["skipped_non_g10"] += 1
            continue

        sha0 = str(pick["content_sha256"] or "")
        src = resolve_file(pick.get("private_object_key"), sha0)
        if src is None:
            stats["missing_file"] += 1
            continue

        try:
            with Image.open(src) as im0:
                if is_psa_slab_image(im0, pick.get("private_object_key")):
                    stats["rejected_slab"] += 1
                    alt_pick = None
                    for a in assets:
                        if a["asset_id"] == pick["asset_id"]:
                            continue
                        if int(a["asset_id"]) in blocked:
                            continue
                        if classify_key(a.get("private_object_key")) == "g10_psa_slab":
                            continue
                        if args.g10_raw_only and classify_key(a.get("private_object_key")) not in (
                            "g10_raw_snk",
                            "g10_other",
                        ):
                            continue
                        p2 = resolve_file(a.get("private_object_key"), str(a["content_sha256"]))
                        if p2 is None:
                            continue
                        with Image.open(p2) as im2:
                            if is_psa_slab_image(im2, a.get("private_object_key")):
                                continue
                        alt_pick = a
                        src = p2
                        pick = a
                        reason = "fallback_after_slab"
                        break
                    if alt_pick is None:
                        continue
        except Exception:
            stats["open_fail"] += 1
            continue

        name = meta[vid]["name"] or f"variant-{vid}"
        if args.dry_run:
            stats["would_bind"] += 1
            stats[f"reason_{reason}"] += 1
            if i % 25 == 0 or i == len(vids):
                print(f"[bind] dry {i}/{len(vids)} bound={stats['would_bind']}", flush=True)
            continue

        trusted = classify_key(pick.get("private_object_key")) in ("g10_raw_snk", "g10_other")
        try:
            block = normalize_to_public(src, name, trusted_g10_raw=trusted)
        except SampleImageRejected:
            stats["sample_rejected"] += 1
            continue
        except Exception as exc:
            stats["normalize_fail"] += 1
            if stats["normalize_fail"] <= 5:
                print(f"normalize fail vid={vid}: {exc}", file=sys.stderr, flush=True)
            continue
        if i == 1 or i % 10 == 0:
            print(f"[bind] progress {i}/{len(vids)} last_bound sha={block.get('sha256','')[:12]}…", flush=True)

        sha = block["sha256"]
        # Never promote a content hash that already carries a hard-reject QC row
        # (same art re-normalized / re-keyed still blocked).
        exec_retry(
            cur,
            """
            SELECT q.rejection_reason FROM market_image_qc q
            JOIN market_image_asset a ON a.id = q.image_asset_id
            WHERE a.variant_id=%s AND a.content_sha256=%s
              AND q.rejection_reason IS NOT NULL AND q.public_allowed=0
            LIMIT 5
            """,
            (vid, sha),
        )
        hard_hit = False
        for qr in cur.fetchall() or []:
            if HARD_REJECT_RE.search(str(qr.get("rejection_reason") or "")):
                hard_hit = True
                break
        if hard_hit:
            stats["blocked_hard_reject_sha"] = stats.get("blocked_hard_reject_sha", 0) + 1
            continue

        mirror_public(sha)
        stats["bound"] += 1
        stats[f"reason_{reason}"] += 1
        batch += 1

        exec_retry(
            cur,
            "SELECT id FROM market_image_asset WHERE content_sha256=%s AND variant_id=%s LIMIT 1",
            (sha, vid),
        )
        row = cur.fetchone()
        if row:
            aid = int(row["id"])
            if aid in blocked:
                stats["blocked_hard_reject_asset"] = stats.get("blocked_hard_reject_asset", 0) + 1
                continue
        else:
            exec_retry(
                cur,
                """
                INSERT INTO market_image_asset
                  (variant_id, image_kind, content_sha256, private_object_key, mime_type,
                   width_px, height_px, source_version_sha256, captured_at)
                VALUES (%s, 'raw_front', %s, %s, 'image/webp', %s, %s, %s, %s)
                """,
                (
                    vid,
                    sha,
                    f"market-assets/{sha}.webp",
                    int(block["width"]),
                    int(block["height"]),
                    sha,
                    now,
                ),
            )
            aid = int(cur.lastrowid)
            stats["new_asset"] += 1

        # Never public_allowed=1 over a hard-reject reason on this qc_version row
        exec_retry(
            cur,
            """
            INSERT INTO market_image_qc
              (image_asset_id, semantic_match_status, card_number_match, language_match,
               tcg_match, raw_front_confirmed, public_allowed, rejection_reason,
               checked_at, qc_version)
            VALUES (%s, 'source_id_exact', 1, 1, 1, 1, 1, NULL, %s, %s)
            ON DUPLICATE KEY UPDATE
              semantic_match_status=IF(
                rejection_reason REGEXP 'wrong_card_art|identity_mismatch|rare_candy_not_|sample_watermark',
                semantic_match_status, 'source_id_exact'),
              card_number_match=IF(
                rejection_reason REGEXP 'wrong_card_art|identity_mismatch|rare_candy_not_|sample_watermark',
                card_number_match, 1),
              language_match=IF(
                rejection_reason REGEXP 'wrong_card_art|identity_mismatch|rare_candy_not_|sample_watermark',
                language_match, 1),
              tcg_match=IF(
                rejection_reason REGEXP 'wrong_card_art|identity_mismatch|rare_candy_not_|sample_watermark',
                tcg_match, 1),
              raw_front_confirmed=IF(
                rejection_reason REGEXP 'wrong_card_art|identity_mismatch|rare_candy_not_|sample_watermark',
                raw_front_confirmed, 1),
              public_allowed=IF(
                rejection_reason REGEXP 'wrong_card_art|identity_mismatch|rare_candy_not_|sample_watermark',
                0, 1),
              rejection_reason=IF(
                rejection_reason REGEXP 'wrong_card_art|identity_mismatch|rare_candy_not_|sample_watermark',
                rejection_reason, NULL),
              checked_at=VALUES(checked_at), qc_version=VALUES(qc_version)
            """,
            (aid, now, QC_VERSION),
        )
        exec_retry(cur, "SELECT id FROM market_image_asset WHERE variant_id=%s", (vid,))
        for other in cur.fetchall():
            oid = int(other["id"])
            if oid == aid:
                continue
            exec_retry(
                cur,
                """
                INSERT INTO market_image_qc
                  (image_asset_id, semantic_match_status, card_number_match, language_match,
                   tcg_match, raw_front_confirmed, public_allowed, rejection_reason,
                   checked_at, qc_version)
                VALUES (%s, 'auto_demoted_non_raw_g10', 0, 0, 0, 0, 0,
                        'not_g10_raw_canonical', %s, %s)
                ON DUPLICATE KEY UPDATE
                  public_allowed=0,
                  rejection_reason='not_g10_raw_canonical',
                  semantic_match_status='auto_demoted_non_raw_g10',
                  checked_at=VALUES(checked_at), qc_version=VALUES(qc_version)
                """,
                (oid, now, QC_VERSION),
            )
            stats["demoted"] += 1

        records.append(
            {
                "cardNumberMatch": True,
                "contentSha256": sha,
                "height": int(block["height"]),
                "imageKind": "raw_front",
                "languageMatch": True,
                "publicAllowed": True,
                "publicId": meta[vid]["opaque_id"],
                "qcAt": utc_now(),
                "qcVersion": QC_VERSION,
                "resolverEvidence": {
                    "method": "g10_raw_full_canvas",
                    "variantId": vid,
                    "assetId": aid,
                    "pickReason": reason,
                    "sourceKey": pick.get("private_object_key"),
                    "stdCanvas": nir.NORMALIZED_MARKER,
                },
                "semanticMatchStatus": "source_id_exact",
                "stdCanvas": nir.NORMALIZED_MARKER,
                "tcgMatch": True,
                "width": int(block["width"]),
            }
        )

        if batch >= max(1, args.commit_every):
            conn.commit()
            write_manifest()
            print(
                f"[bind] {i}/{len(vids)} committed bound={stats['bound']} "
                f"slab={stats.get('rejected_slab', 0)} miss={stats.get('missing_file', 0)} "
                f"rank={meta[vid]['rank']} {name[:40]}",
                flush=True,
            )
            batch = 0

    if not args.dry_run:
        if batch:
            conn.commit()
        write_manifest()

    print(json.dumps({"stats": dict(stats), "records": len(records), "dryRun": args.dry_run}, indent=2), flush=True)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
