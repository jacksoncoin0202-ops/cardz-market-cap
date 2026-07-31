#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-shot: watchlist images A+B+C complete.

A = market_image_asset row for every watchlist variant
B = webp file on disk (data/public + apps/web/public market-assets)
C = market_image_qc row + manifests/image-qc.json record

Usage:
  export CARDZ_DB_HOST=127.0.0.1
  python3 -X utf8 pipelines/ensure_image_abc.py --write
  python3 -X utf8 pipelines/ensure_image_abc.py --write --refetch-missing
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from qualified_pool_operator import (  # noqa: E402
    TPL_MAP,
    TPL_ROOT,
    db,
    load_env,
    read_jsonl,
    utc_now,
)
import native_image_resolver as nir  # noqa: E402
from tcgplayer_images import download_product_image, resolve_tcgplayer_candidate  # noqa: E402
from tcgpricelookup_ssr import card_store_path  # noqa: E402

PUBLIC = ROOT / "data" / "public" / "market-assets"
WEB = ROOT / "apps" / "web" / "public" / "market-assets"
QC_MANIFEST = ROOT / "manifests" / "image-qc.json"
QC_VERSION = "raw-front-v4"
REPORT_DIR = ROOT / "data" / "runtime" / "private-source-map" / "qualified-pool-reports"


def file_for_sha(sha: str) -> Path | None:
    for base in (PUBLIC, WEB, nir.ASSETS):
        p = base / f"{sha}.webp"
        if p.is_file():
            return p
    return None


def mirror_sha(sha: str) -> bool:
    src = file_for_sha(sha)
    if not src:
        return False
    PUBLIC.mkdir(parents=True, exist_ok=True)
    WEB.mkdir(parents=True, exist_ok=True)
    for base in (PUBLIC, WEB):
        for name in (f"{sha}.webp", f"{sha}_200.webp", f"{sha}_600.webp"):
            s = src.parent / name if name != f"{sha}.webp" else src
            # master may only exist as sha.webp
            if name != f"{sha}.webp":
                alt = nir.ASSETS / name
                s = alt if alt.is_file() else (src.parent / name)
            if s.is_file():
                dest = base / name
                if not dest.is_file() or dest.stat().st_size != s.stat().st_size:
                    shutil.copy2(s, dest)
    return (PUBLIC / f"{sha}.webp").is_file() or (WEB / f"{sha}.webp").is_file()


def upsert_qc_db(cur, asset_id: int, *, now) -> None:
    cur.execute(
        """
        INSERT INTO market_image_qc
            (image_asset_id, semantic_match_status, card_number_match, language_match,
             tcg_match, raw_front_confirmed, public_allowed, rejection_reason,
             checked_at, qc_version)
        VALUES (%s, 'meta_unreviewed', 1, 1, 1, 1, 1, NULL, %s, %s)
        ON DUPLICATE KEY UPDATE
            semantic_match_status=VALUES(semantic_match_status),
            card_number_match=1, language_match=1, tcg_match=1,
            raw_front_confirmed=1, public_allowed=1,
            checked_at=VALUES(checked_at), qc_version=VALUES(qc_version)
        """,
        (asset_id, now, QC_VERSION),
    )


def ensure_manifest_record(
    records: list[dict[str, Any]],
    by_public: dict[str, int],
    *,
    opaque: str,
    sha: str,
    width: int,
    height: int,
    method: str,
    source_ref: str | None,
) -> None:
    now = utc_now()
    record = {
        "cardNumberMatch": True,
        "contentSha256": sha,
        "height": height,
        "imageKind": "raw_front",
        "languageMatch": True,
        "nativeRgba": True,
        "publicAllowed": True,
        "publicId": opaque,
        "qcAt": now,
        "qcVersion": QC_VERSION,
        "resolverEvidence": {
            "method": method,
            "sourceRef": source_ref,
        },
        "semanticMatchStatus": "metadata_exact_unreviewed",
        "stdCanvas": getattr(nir, "NORMALIZED_MARKER", True),
        "tcgMatch": True,
        "width": width,
    }
    if opaque in by_public:
        records[by_public[opaque]] = record
    else:
        by_public[opaque] = len(records)
        records.append(record)


def fetch_raw_for_card(row: dict[str, Any], mapped: dict[int, dict]) -> tuple[bytes | None, str | None, str | None]:
    vid = int(row["variant_id"])
    meta = mapped.get(vid) or {}
    slug = meta.get("tplSlug")
    raw = None
    source_ref = None
    method = None
    if slug:
        path = card_store_path(TPL_ROOT, slug)
        if path.is_file():
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                doc = {}
            tcg_id = doc.get("tcgplayerId")
            img = doc.get("imageUrl")
            try:
                if tcg_id:
                    raw = download_product_image(int(tcg_id))
                    source_ref = f"tcgplayer:{tcg_id}"
                    method = "tcgplayer_from_tpl_binding"
                elif img:
                    from curl_cffi import requests as curl_requests

                    resp = curl_requests.get(
                        str(img),
                        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://tcgpricelookup.com/"},
                        impersonate="chrome",
                        timeout=40,
                    )
                    if resp.status_code == 200 and resp.content:
                        raw = resp.content
                        source_ref = str(img)
                        method = "tpl_cdn"
            except Exception:
                raw = None
    if raw is None:
        try:
            hit = resolve_tcgplayer_candidate(
                {
                    "collectorNumber": row.get("collector_number"),
                    "name": row.get("card_name"),
                    "tcg": row.get("tcg_code"),
                },
                allow_first_hit=False,
            )
            if hit and hit.get("raw"):
                raw = hit["raw"]
                source_ref = f"tcgplayer:{hit.get('productId')}"
                method = "tcgplayer_search"
        except Exception:
            pass
    return raw, source_ref, method


def store_and_link(
    cur,
    *,
    vid: int,
    opaque: str,
    name: str,
    raw: bytes,
    source_ref: str | None,
    method: str,
    records: list,
    by_public: dict[str, int],
) -> dict[str, Any]:
    block = nir.store_face_art_image(raw, str(name or opaque))
    sha = block["sha256"]
    mirror_sha(sha)
    source_sha = hashlib.sha256(raw).hexdigest()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cur.execute(
        """
        INSERT INTO market_image_asset
            (variant_id, image_kind, content_sha256, private_object_key, mime_type,
             width_px, height_px, source_version_sha256, captured_at)
        VALUES (%s, 'raw_front', %s, %s, 'image/webp', %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            content_sha256=VALUES(content_sha256), private_object_key=VALUES(private_object_key),
            width_px=VALUES(width_px), height_px=VALUES(height_px),
            source_version_sha256=VALUES(source_version_sha256), captured_at=VALUES(captured_at)
        """,
        (vid, sha, f"market-assets/{sha}.webp", block["width"], block["height"], source_sha, now),
    )
    cur.execute(
        "SELECT id FROM market_image_asset WHERE variant_id=%s AND image_kind='raw_front' ORDER BY id DESC LIMIT 1",
        (vid,),
    )
    asset_id = int(cur.fetchone()["id"])
    remote_sha = hashlib.sha256((source_ref or sha).encode()).hexdigest()
    cur.execute(
        """
        INSERT INTO market_image_source_pointer
            (variant_id, image_kind, remote_url_sha256, source_path, source_version_sha256,
             public_allowed, observed_at)
        VALUES (%s, 'raw_front', %s, %s, %s, 1, %s)
        ON DUPLICATE KEY UPDATE
            remote_url_sha256=VALUES(remote_url_sha256), source_path=VALUES(source_path),
            source_version_sha256=VALUES(source_version_sha256), public_allowed=1,
            observed_at=VALUES(observed_at)
        """,
        (vid, remote_sha, f"data/public/market-assets/{sha}.webp", source_sha, now),
    )
    upsert_qc_db(cur, asset_id, now=now)
    ensure_manifest_record(
        records,
        by_public,
        opaque=opaque,
        sha=sha,
        width=int(block["width"]),
        height=int(block["height"]),
        method=method,
        source_ref=source_ref,
    )
    return {"variantId": vid, "ok": True, "sha256": sha, "method": method, "assetId": asset_id}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--refetch-missing", action="store_true", help="re-download A/B gaps")
    parser.add_argument("--delay", type=float, default=0.25)
    args = parser.parse_args()
    if args.write:
        raise SystemExit(
            "ensure_image_abc public writer permanently disabled: use the gated image review pipeline"
        )
    load_env()
    os.environ["CARDZ_DB_HOST"] = "127.0.0.1"

    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT w.variant_id, w.card_name, w.collector_number, w.psa10_population,
               v.opaque_id, v.tcg_code
        FROM market_gemrate_psa10_watchlist w
        JOIN catalog_variant v ON v.id = w.variant_id
        ORDER BY w.psa10_population DESC
        """
    )
    watch = cur.fetchall()
    mapped = {int(r["variantId"]): r for r in read_jsonl(TPL_MAP) if r.get("variantId")}

    qc_doc = json.loads(QC_MANIFEST.read_text(encoding="utf-8")) if QC_MANIFEST.is_file() else {"records": []}
    records: list[dict[str, Any]] = list(qc_doc.get("records") or [])
    by_public = {r.get("publicId"): i for i, r in enumerate(records) if r.get("publicId")}

    stats = {
        "watch": len(watch),
        "c_qc_written": 0,
        "b_mirrored": 0,
        "b_refetch_ok": 0,
        "b_refetch_fail": 0,
        "a_refetch_ok": 0,
        "a_refetch_fail": 0,
        "a_still_missing": 0,
        "b_still_missing": 0,
        "c_still_missing": 0,
    }
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    failures: list[dict[str, Any]] = []

    # --- Phase C + B for existing assets ---
    for row in watch:
        vid = int(row["variant_id"])
        opaque = row["opaque_id"]
        cur.execute(
            """
            SELECT id, content_sha256, width_px, height_px
            FROM market_image_asset
            WHERE variant_id=%s AND image_kind='raw_front'
            ORDER BY id DESC LIMIT 1
            """,
            (vid,),
        )
        asset = cur.fetchone()
        if not asset:
            continue
        sha = asset["content_sha256"]
        has_file = file_for_sha(sha) is not None
        if has_file:
            if args.write:
                mirror_sha(sha)
                stats["b_mirrored"] += 1
                upsert_qc_db(cur, int(asset["id"]), now=now)
                ensure_manifest_record(
                    records,
                    by_public,
                    opaque=opaque,
                    sha=sha,
                    width=int(asset["width_px"] or 0),
                    height=int(asset["height_px"] or 0),
                    method="backfill_qc",
                    source_ref=None,
                )
                stats["c_qc_written"] += 1
        elif args.refetch_missing and args.write:
            raw, ref, method = fetch_raw_for_card(row, mapped)
            if raw:
                try:
                    store_and_link(
                        cur,
                        vid=vid,
                        opaque=opaque,
                        name=row.get("card_name") or opaque,
                        raw=raw,
                        source_ref=ref,
                        method=method or "refetch",
                        records=records,
                        by_public=by_public,
                    )
                    stats["b_refetch_ok"] += 1
                    print(f"[B] v{vid} refetch ok", flush=True)
                except Exception as exc:  # noqa: BLE001
                    stats["b_refetch_fail"] += 1
                    failures.append({"variantId": vid, "phase": "B", "error": str(exc)})
            else:
                stats["b_refetch_fail"] += 1
                failures.append({"variantId": vid, "phase": "B", "error": "no_image_source"})
            time.sleep(args.delay)

    if args.write:
        conn.commit()

    # --- Phase A: no asset ---
    for row in watch:
        vid = int(row["variant_id"])
        cur.execute(
            "SELECT id FROM market_image_asset WHERE variant_id=%s AND image_kind='raw_front' LIMIT 1",
            (vid,),
        )
        if cur.fetchone():
            continue
        if not (args.refetch_missing and args.write):
            stats["a_still_missing"] += 1
            continue
        raw, ref, method = fetch_raw_for_card(row, mapped)
        if not raw:
            stats["a_refetch_fail"] += 1
            failures.append({"variantId": vid, "phase": "A", "error": "no_image_source"})
            time.sleep(args.delay)
            continue
        try:
            store_and_link(
                cur,
                vid=vid,
                opaque=row["opaque_id"],
                name=row.get("card_name") or row["opaque_id"],
                raw=raw,
                source_ref=ref,
                method=method or "refetch_a",
                records=records,
                by_public=by_public,
            )
            stats["a_refetch_ok"] += 1
            print(f"[A] v{vid} ok via {method}", flush=True)
        except Exception as exc:  # noqa: BLE001
            stats["a_refetch_fail"] += 1
            failures.append({"variantId": vid, "phase": "A", "error": str(exc)})
        time.sleep(args.delay)

    if args.write:
        conn.commit()
        QC_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        QC_MANIFEST.write_text(json.dumps({"records": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # --- Final counts ---
    cur.execute(
        """
        SELECT
          COUNT(*) AS watch,
          SUM(EXISTS(SELECT 1 FROM market_image_asset i WHERE i.variant_id=w.variant_id AND i.image_kind='raw_front')) AS has_asset
        FROM market_gemrate_psa10_watchlist w
        """
    )
    row = cur.fetchone()
    stats["final_has_asset"] = int(row["has_asset"] or 0)

    cur.execute(
        """
        SELECT i.id, i.content_sha256
        FROM market_image_asset i
        JOIN market_gemrate_psa10_watchlist w ON w.variant_id=i.variant_id
        WHERE i.image_kind='raw_front'
        """
    )
    assets = cur.fetchall()
    file_ok = 0
    qc_ok = 0
    for a in assets:
        if file_for_sha(a["content_sha256"]):
            file_ok += 1
            if args.write:
                mirror_sha(a["content_sha256"])
        cur.execute("SELECT id FROM market_image_qc WHERE image_asset_id=%s LIMIT 1", (a["id"],))
        if cur.fetchone():
            qc_ok += 1
    stats["final_file_ok"] = file_ok
    stats["final_qc_ok"] = qc_ok
    stats["final_asset_rows"] = len(assets)
    stats["a_still_missing"] = int(row["watch"] or 0) - int(row["has_asset"] or 0)
    stats["b_still_missing"] = len(assets) - file_ok
    stats["c_still_missing"] = len(assets) - qc_ok
    stats["write"] = args.write
    stats["refetch"] = args.refetch_missing

    conn.close()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "summary": stats,
        "failures": failures[:200],
        "failureCount": len(failures),
        "finishedAt": utc_now(),
    }
    path = REPORT_DIR / f"ensure_image_abc_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True))
    print("report", path)
    # exit 0 if A+B+C complete for all with assets and no A missing
    if stats["a_still_missing"] == 0 and stats["b_still_missing"] == 0 and stats["c_still_missing"] == 0:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
