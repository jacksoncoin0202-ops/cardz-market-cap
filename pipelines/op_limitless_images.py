#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Disabled legacy One Piece Limitless image writer.

Operator correction 2026-07-31: the whole ``limitless-one-piece-en`` source
family is confirmed SAMPLE-polluted and may never be selected, reviewed, bound,
or published again.  This file remains only as a fail-closed tombstone for old
commands and documentation references.

PRICES on Limitless are TCGPlayer/Cardmarket **raw market**, NOT PSA10.
Do NOT ingest those as psa10 market-cap prices.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from curl_cffi import requests as cr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from qualified_pool_operator import db, load_env, utc_now  # noqa: E402
import native_image_resolver as nir  # noqa: E402

CDN = "https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/one-piece"
PUBLIC = ROOT / "data" / "public" / "market-assets"
WEB = ROOT / "apps" / "web" / "public" / "market-assets"
QC_MANIFEST = ROOT / "manifests" / "image-qc.json"
REPORT_DIR = ROOT / "data" / "runtime" / "private-source-map" / "qualified-pool-reports"
DISABLED_REASON = (
    "limitless-one-piece-en is permanently disabled: "
    "known SAMPLE-polluted source family"
)


def parse_collector(collector: str) -> tuple[str, str] | None:
    raw = (collector or "").strip().upper().replace(" ", "")
    # OP13-118, ST14-003, OP05-074
    m = re.fullmatch(r"([A-Z]+\d+)-(\d+[A-Z]?)", raw)
    if not m:
        # sometimes OP13-118a
        m = re.fullmatch(r"([A-Z]+\d+)-(\d+[A-Z]*)", raw)
    if not m:
        return None
    return m.group(1), m.group(2)


def candidate_urls(collector: str) -> list[str]:
    parsed = parse_collector(collector)
    if not parsed:
        return []
    set_code, num = parsed
    full = f"{set_code}-{num}"
    urls = [
        f"{CDN}/{set_code}/{full}_EN.webp",
        f"{CDN}/{set_code}/{full}_EN.png",
        f"{CDN}/{set_code}/{full}.webp",
        f"{CDN}/{set_code}/{full}.png",
        # JP
        f"{CDN}/{set_code}/{full}_JP.webp",
    ]
    return urls


def download_first(urls: list[str], sess: cr.Session) -> tuple[bytes | None, str | None]:
    for url in urls:
        try:
            r = sess.get(url, timeout=30)
            if r.status_code == 200 and r.content and len(r.content) > 2000:
                ctype = (r.headers.get("content-type") or "").lower()
                if "html" in ctype:
                    continue
                return r.content, url
        except Exception:
            continue
    return None, None


def upsert_qc(cur, asset_id: int, now) -> None:
    cur.execute(
        """
        INSERT INTO market_image_qc
            (image_asset_id, semantic_match_status, card_number_match, language_match,
             tcg_match, raw_front_confirmed, public_allowed, rejection_reason,
             checked_at, qc_version)
        VALUES (%s, 'meta_unreviewed', 1, 1, 1, 1, 1, NULL, %s, 'raw-front-v4')
        ON DUPLICATE KEY UPDATE
            public_allowed=1, raw_front_confirmed=1, checked_at=VALUES(checked_at),
            qc_version=VALUES(qc_version)
        """,
        (asset_id, now),
    )


def positive_variant_id(value: str) -> int:
    try:
        variant_id = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid variant id: {value!r}") from exc
    if variant_id <= 0:
        raise argparse.ArgumentTypeError("variant id must be positive")
    return variant_id


def parse_variant_id_csv(values: list[str]) -> list[int]:
    """Parse comma-separated allowlists, preserving first-seen order."""
    variant_ids: list[int] = []
    seen: set[int] = set()
    for value in values:
        for token in value.split(","):
            token = token.strip()
            if not token:
                continue
            variant_id = positive_variant_id(token)
            if variant_id not in seen:
                seen.add(variant_id)
                variant_ids.append(variant_id)
    return variant_ids


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--variant-id",
        action="append",
        default=[],
        type=positive_variant_id,
        help="Exact catalog_variant.id to process; repeat for multiple IDs.",
    )
    parser.add_argument(
        "--variant-id-csv",
        action="append",
        default=[],
        help="Comma-separated exact catalog_variant.id allowlist; repeat if needed.",
    )
    parser.add_argument("--write", action="store_true", help="Apply DB, asset, and QC-manifest writes.")
    parser.add_argument("--only-missing", action="store_true")
    parser.add_argument("--delay", type=float, default=0.15)
    return parser


def selected_variant_ids(args: argparse.Namespace) -> list[int]:
    variant_ids = list(args.variant_id)
    seen = set(variant_ids)
    for variant_id in parse_variant_id_csv(args.variant_id_csv):
        if variant_id not in seen:
            seen.add(variant_id)
            variant_ids.append(variant_id)
    return variant_ids


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    variant_ids = selected_variant_ids(args)
    if not variant_ids:
        parser.error("supply at least one --variant-id or --variant-id-csv; full-pool runs are disabled")
    parser.error(DISABLED_REASON)
    load_env()
    os.environ["CARDZ_DB_HOST"] = "127.0.0.1"

    conn = db()
    cur = conn.cursor()
    placeholders = ", ".join(["%s"] * len(variant_ids))
    cur.execute(
        f"""
        SELECT v.id AS variant_id,
               v.canonical_name AS card_name,
               v.collector_number,
               COALESCE(w.psa10_population, 0) AS psa10_population,
               v.opaque_id,
               v.tcg_code
        FROM catalog_variant v
        LEFT JOIN market_gemrate_psa10_watchlist w ON w.variant_id = v.id
        WHERE v.tcg_code IN ('one-piece', 'onepiece', 'optcg')
          AND v.collector_number IS NOT NULL
          AND TRIM(v.collector_number) <> ''
          AND v.id IN ({placeholders})
        ORDER BY COALESCE(w.psa10_population, 0) DESC, v.id ASC
        """,
        variant_ids,
    )
    rows = cur.fetchall()
    if args.only_missing:
        filtered = []
        for r in rows:
            cur.execute(
                "SELECT content_sha256 FROM market_image_asset WHERE variant_id=%s AND image_kind='raw_front' ORDER BY id DESC LIMIT 1",
                (int(r["variant_id"]),),
            )
            a = cur.fetchone()
            if not a:
                filtered.append(r)
                continue
            sha = a["content_sha256"]
            if not (PUBLIC / f"{sha}.webp").is_file() and not (WEB / f"{sha}.webp").is_file():
                filtered.append(r)
        rows = filtered

    sess = cr.Session(impersonate="chrome131")
    qc_doc = json.loads(QC_MANIFEST.read_text(encoding="utf-8")) if QC_MANIFEST.is_file() else {"records": []}
    records = list(qc_doc.get("records") or [])
    by_public = {r.get("publicId"): i for i, r in enumerate(records) if r.get("publicId")}

    ok = fail = skip = 0
    results = []
    for r in rows:
        vid = int(r["variant_id"])
        collector = str(r.get("collector_number") or "").strip()
        urls = candidate_urls(collector)
        if not urls:
            fail += 1
            results.append({"variantId": vid, "ok": False, "error": "bad_collector", "collector": collector})
            continue
        raw, url = download_first(urls, sess)
        if not raw:
            fail += 1
            results.append({"variantId": vid, "ok": False, "error": "cdn_miss", "collector": collector, "tried": urls[:3]})
            print(f"[miss] v{vid} {collector}", flush=True)
            time.sleep(args.delay)
            continue
        if not args.write:
            ok += 1
            results.append({"variantId": vid, "ok": True, "dryRun": True, "url": url})
            continue
        try:
            block = nir.store_face_art_image(raw, str(r.get("card_name") or collector))
            sha = block["sha256"]
            # nir.ASSETS == data/public/market-assets — already written; only mirror to web public
            WEB.mkdir(parents=True, exist_ok=True)
            for name in (f"{sha}.webp", f"{sha}_200.webp", f"{sha}_600.webp"):
                src = nir.ASSETS / name
                dest = WEB / name
                if src.is_file() and src.resolve() != dest.resolve():
                    shutil.copy2(src, dest)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            source_sha = hashlib.sha256(raw).hexdigest()
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
            remote_sha = hashlib.sha256(url.encode()).hexdigest()
            cur.execute(
                """
                INSERT INTO market_image_source_pointer
                    (variant_id, image_kind, remote_url_sha256, source_path, source_version_sha256,
                     public_allowed, observed_at)
                VALUES (%s, 'raw_front', %s, %s, %s, 1, %s)
                ON DUPLICATE KEY UPDATE source_path=VALUES(source_path), public_allowed=1, observed_at=VALUES(observed_at)
                """,
                (vid, remote_sha, url, source_sha, now),
            )
            # permanent id mark
            cur.execute(
                """
                INSERT INTO catalog_source_identity
                    (source_code, external_entity_id, variant_id, match_status, evidence_sha256)
                VALUES ('op_limitless', %s, %s, 'exact', %s)
                ON DUPLICATE KEY UPDATE variant_id=VALUES(variant_id), match_status='exact',
                    evidence_sha256=VALUES(evidence_sha256), updated_at=CURRENT_TIMESTAMP
                """,
                (
                    collector.upper().replace(" ", ""),
                    vid,
                    hashlib.sha256(f"op_limitless:{collector}:{vid}".encode()).hexdigest(),
                ),
            )
            upsert_qc(cur, asset_id, now)
            opaque = r["opaque_id"]
            rec = {
                "cardNumberMatch": True,
                "contentSha256": sha,
                "height": block["height"],
                "imageKind": "raw_front",
                "languageMatch": True,
                "publicAllowed": True,
                "publicId": opaque,
                "qcAt": utc_now(),
                "qcVersion": "raw-front-v4",
                "resolverEvidence": {"method": "op_limitless_cdn", "sourceRef": url},
                "semanticMatchStatus": "metadata_exact_unreviewed",
                "tcgMatch": True,
                "width": block["width"],
            }
            if opaque in by_public:
                records[by_public[opaque]] = rec
            else:
                by_public[opaque] = len(records)
                records.append(rec)
            conn.commit()
            ok += 1
            results.append({"variantId": vid, "ok": True, "url": url, "sha256": sha})
            print(f"[ok] v{vid} {collector} <- {url}", flush=True)
        except Exception as exc:  # noqa: BLE001
            fail += 1
            conn.rollback()
            results.append({"variantId": vid, "ok": False, "error": str(exc)})
            print(f"[err] v{vid} {exc}", flush=True)
        time.sleep(args.delay)

    if args.write:
        QC_MANIFEST.write_text(json.dumps({"records": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    conn.close()
    summary = {"ok": ok, "fail": fail, "skip": skip, "considered": len(rows), "write": args.write}
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"op_limitless_images_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    print("report", path)
    print("NOTE: Limitless card prices are raw TCGPlayer market — not used as PSA10.")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
