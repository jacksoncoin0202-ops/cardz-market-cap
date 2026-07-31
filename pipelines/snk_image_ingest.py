#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SNKRDUNK master 原圖 → QC → CardZ A/B/C 入庫。

硬閘：
  - 必須已有 catalog_source_identity(source_code=snkrdunk)
  - sample_image_qc 拒 SAMPLE
  - store 經 native_image_resolver（429×600）
  - 預設 dry-run；--write 先寫 DB

Usage:
  cd C:\\Users\\jackson0202\\Documents\\Playground\\cardz-market-cap
  $env:CARDZ_DB_HOST = "127.0.0.1"
  # load data\\runtime\\config\\backend.env
  python -X utf8 pipelines\\snk_image_ingest.py --snk-id 93024
  python -X utf8 pipelines\\snk_image_ingest.py --snk-id 93024 --write
  python -X utf8 pipelines\\snk_image_ingest.py --missing-only --limit 20 --write
  python -X utf8 pipelines\\snk_image_ingest.py --upgrade-bad --limit 50 --write
  python -X utf8 pipelines\\snk_image_ingest.py --prefer-snk --limit 200 --write --force
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import native_image_resolver as nir  # noqa: E402
from failure_ledger import record_failure, record_resolution  # noqa: E402
from snkrdunk_bulk import SnkrdunkApi  # noqa: E402

PUBLIC = ROOT / "data" / "public" / "market-assets"
WEB = ROOT / "apps" / "web" / "public" / "market-assets"
REPORT_DIR = ROOT / "data" / "runtime" / "private-source-map" / "qualified-pool-reports"
QC_VERSION = "snk-image-v1"
SOURCE_CODE = "snkrdunk"
# Ingest is landing only — never claim human/vision or public street without promotion.
SEMANTIC_PENDING = "pending_review"  # varchar(24); strict gate needs human_or_vision_confirmed


def load_env() -> None:
    env_path = ROOT / "data" / "runtime" / "config" / "backend.env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("\r"))


def db():
    import pymysql

    load_env()
    os.environ["CARDZ_DB_HOST"] = "127.0.0.1"
    return pymysql.connect(
        host="127.0.0.1",
        port=int(os.environ.get("CARDZ_DB_PORT", "3308")),
        user=os.environ.get("CARDZ_DB_USER", "cardz"),
        password=os.environ.get("CARDZ_DB_PASSWORD") or "",
        database=os.environ.get("CARDZ_DB_NAME", "cardz_market_cap"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def mirror_sha(sha: str) -> bool:
    """Ensure sha.webp (+ derivatives if present) on PUBLIC and WEB."""
    src = None
    for base in (PUBLIC, WEB, nir.ASSETS, ROOT / "data" / "public" / "market-assets"):
        p = base / f"{sha}.webp"
        if p.is_file():
            src = p
            break
    if src is None:
        return False
    PUBLIC.mkdir(parents=True, exist_ok=True)
    WEB.mkdir(parents=True, exist_ok=True)
    import shutil

    for base in (PUBLIC, WEB):
        for name in (f"{sha}.webp", f"{sha}_200.webp", f"{sha}_600.webp"):
            s = src.parent / name
            if not s.is_file() and name != f"{sha}.webp":
                alt = nir.ASSETS / name
                s = alt if alt.is_file() else s
            if s.is_file():
                dest = base / name
                if not dest.is_file() or dest.stat().st_size != s.stat().st_size:
                    shutil.copy2(s, dest)
    return (PUBLIC / f"{sha}.webp").is_file()


def download_url(url: str) -> bytes:
    from curl_cffi import requests as curl_requests

    r = curl_requests.get(url, impersonate="chrome", timeout=45)
    r.raise_for_status()
    return r.content


def master_image_meta(api: SnkrdunkApi, snk_id: int) -> dict[str, Any]:
    master = api.get_master(int(snk_id))
    media = master.get("primaryMedia") or {}
    url = media.get("imageUrl")
    return {
        "snk_id": int(snk_id),
        "name": master.get("name"),
        "localizedName": master.get("localizedName"),
        "imageUrl": url,
        "productNumber": master.get("productNumber"),
        "raw_master_keys": list(master.keys())[:20],
    }


def _norm_name(s: str) -> str:
    """OP/PTCG name normalize: dots/apostrophes → space, collapse whitespace."""
    s = (s or "").lower()
    s = s.replace(".", " ").replace("·", " ").replace("'", " ").replace("'", " ")
    s = re.sub(r"[^a-z0-9\s\-/]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def identity_title_ok(
    master_name: str | None,
    variant: dict[str, Any],
    *,
    product_number: str | None = None,
) -> tuple[bool, str]:
    """Semantic gate: collector number (preferred) and/or species must appear in SNK title.

    Fail-closed on pure single-digit collector hits without species (too many false positives).
    AI/human residual when needs_review.
    OP: exact collector OP##-### is primary; name dots normalized only as assist.
    When title omits the code (e.g. DON!! Super Parallel), allow exact match of
    SNK productNumber to catalog collector_number (evidence-backed, not invent).
    """
    title_raw = master_name or ""
    title = title_raw.lower()
    title_n = _norm_name(title_raw)
    if not title:
        return False, "empty_master_name"
    collector = str(variant.get("collector_number") or "").strip()
    name = str(variant.get("canonical_name") or "").strip()
    species = re.split(
        r"\s+(vstar|vmax|v-union|ex|gx|v|sr|sec|sp|parallel|promo|errata)\b",
        name,
        flags=re.I,
    )[0].strip()
    species_l = _norm_name(species) if species else ""

    # Full collector string (OP-style ST01-013, 139/108)
    if collector and collector.lower() not in ("unknown", "none", ""):
        col_l = collector.lower().replace(" ", "")
        title_compact = title.replace(" ", "")
        if col_l in title_compact or col_l.replace("/", "") in title_compact.replace("/", ""):
            return True, f"title_hit:collector:{collector}"
        # SNK often wraps [OP12-002] — extract bracket codes
        for m in re.finditer(r"\[?\s*([a-z]{1,4}\d{1,3}\s*-\s*\d{1,4}[a-z]?)\s*\]?", title, re.I):
            code = re.sub(r"\s+", "", m.group(1).lower())
            if code == col_l:
                return True, f"title_hit:bracket_collector:{collector}"
        # productNumber exact == collector (DON!! / promo codes often absent from title)
        pn = str(product_number or "").strip()
        if pn:
            pn_l = pn.lower().replace(" ", "")
            if pn_l == col_l or pn_l.replace("/", "") == col_l.replace("/", ""):
                return True, f"title_hit:product_number:{pn}"
        # padded / unpadded number with set-like context
        nums = re.findall(r"\d+", collector)
        if nums:
            primary = nums[0]
            variants = {primary, primary.lstrip("0") or primary, primary.zfill(3)}
            # require species OR multi-digit (≥2) number to avoid "1" matching everything
            for n in variants:
                if len(n) >= 2 and n in title:
                    if species_l and species_l in title_n:
                        return True, f"title_hit:num+species:{n}"
                    if len(n) >= 3 or "/" in collector or "-" in collector:
                        return True, f"title_hit:num:{n}"
            # OP set prefix match without full collector (weak) → needs_review not auto
            set_m = re.match(r"([a-z]+\d+)-", col_l)
            if set_m and set_m.group(1) in title_compact and species_l and species_l in title_n:
                return False, f"needs_review:set_ok_collector_diff:{collector}"
    if species_l and len(species_l) >= 4 and species_l in title_n:
        # species-only OK when collector unknown
        if not collector or collector.lower() in ("unknown", "none", ""):
            return True, f"title_hit:species_only:{species_l}"
        # species + any multi-digit from collector in title
        if collector:
            for n in re.findall(r"\d+", collector):
                n2 = n.lstrip("0") or n
                if len(n2) >= 2 and n2 in title:
                    return True, f"title_hit:species+num:{species_l}+{n2}"
        # species present but number weak → needs_review (semi-auto AI)
        return False, f"needs_review:species_ok_num_weak:{species_l}"
    return False, "title_mismatch"


def qc_raw_image(raw: bytes) -> dict[str, Any]:
    from PIL import Image
    import io
    from sample_image_qc import assert_raw_bytes_not_sample

    assert_raw_bytes_not_sample(raw, context="snk_image_ingest")
    with Image.open(io.BytesIO(raw)) as im:
        mode = im.mode
        size = list(im.size)
        rgba = im.convert("RGBA")
        w, h = rgba.size
        corners = {
            "tl": rgba.getpixel((2, 2))[3],
            "tr": rgba.getpixel((w - 3, 2))[3],
            "bl": rgba.getpixel((2, h - 3))[3],
            "br": rgba.getpixel((w - 3, h - 3))[3],
        }
        native = all(a < 10 for a in corners.values())
    if min(size) < 80:
        raise ValueError(f"image_too_small:{size}")
    return {
        "bytes": len(raw),
        "mode": mode,
        "size": size,
        "corner_alpha": corners,
        "native_rounded": native,
        "sample_qc": "pass",
    }


def store_block(raw: bytes, alt: str, *, native_rounded: bool) -> dict[str, Any]:
    if native_rounded:
        return nir.store_native_image(raw, alt)
    return nir.store_face_art_image(raw, alt)


def count_snk_ids_for_variant(cur, variant_id: int) -> int:
    """Count active SNK binds that block multi-identity image land.

    `rejected` rows are demotions (e.g. metal UPC product) and must not
    keep multi_snk_identity closed after exact-only cleanup.
    """
    cur.execute(
        """
        SELECT COUNT(*) AS c FROM catalog_source_identity
        WHERE source_code=%s AND variant_id=%s
          AND IFNULL(match_status, '') <> 'rejected'
        """,
        (SOURCE_CODE, variant_id),
    )
    return int(cur.fetchone()["c"] or 0)


def upsert_abc(
    cur,
    *,
    variant_id: int,
    block: dict[str, Any],
    source_ref: str,
    raw: bytes,
    card_number_match: int = 0,
) -> dict[str, Any]:
    """Land asset + pointer + QC as **pending review only**.

    Hard rule (2026-07-29): title/script match must NOT set public_allowed=1,
    must NOT set language/tcg match=1, must NOT claim human_or_vision_confirmed.
    Promotion is pipelines/snk_image_promotion.py only.
    """
    sha = block["sha256"]
    mirror_sha(sha)
    source_sha = hashlib.sha256(raw).hexdigest()
    now = utc_naive()
    cur.execute(
        """
        INSERT INTO market_image_asset
            (variant_id, image_kind, content_sha256, private_object_key, mime_type,
             width_px, height_px, source_version_sha256, captured_at)
        VALUES (%s, 'raw_front', %s, %s, 'image/webp', %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            content_sha256=VALUES(content_sha256),
            private_object_key=VALUES(private_object_key),
            width_px=VALUES(width_px), height_px=VALUES(height_px),
            source_version_sha256=VALUES(source_version_sha256),
            captured_at=VALUES(captured_at)
        """,
        (
            variant_id,
            sha,
            f"market-assets/{sha}.webp",
            block["width"],
            block["height"],
            source_sha,
            now,
        ),
    )
    cur.execute(
        """
        SELECT id FROM market_image_asset
        WHERE variant_id=%s AND image_kind='raw_front'
        ORDER BY id DESC LIMIT 1
        """,
        (variant_id,),
    )
    asset_id = int(cur.fetchone()["id"])
    remote_sha = hashlib.sha256(source_ref.encode("utf-8")).hexdigest()
    # pointer.public_allowed = landing visibility flag only; keep 0 until promotion
    cur.execute(
        """
        INSERT INTO market_image_source_pointer
            (variant_id, image_kind, remote_url_sha256, source_path, source_version_sha256,
             public_allowed, observed_at)
        VALUES (%s, 'raw_front', %s, %s, %s, 0, %s)
        ON DUPLICATE KEY UPDATE
            remote_url_sha256=VALUES(remote_url_sha256),
            source_path=VALUES(source_path),
            source_version_sha256=VALUES(source_version_sha256),
            public_allowed=0,
            observed_at=VALUES(observed_at)
        """,
        (variant_id, remote_sha, source_ref, source_sha, now),
    )
    # Do not overwrite an already human/vision confirmed row with pending
    cur.execute(
        """
        SELECT semantic_match_status, public_allowed FROM market_image_qc
        WHERE image_asset_id=%s LIMIT 1
        """,
        (asset_id,),
    )
    existing_qc = cur.fetchone()
    if existing_qc and str(existing_qc.get("semantic_match_status") or "") == (
        "human_or_vision_confirmed"
    ):
        return {
            "assetId": asset_id,
            "sha256": sha,
            "sourceRef": source_ref,
            "qc": "kept_human_or_vision_confirmed",
            "publicAllowed": int(existing_qc.get("public_allowed") or 0),
        }

    cur.execute(
        """
        INSERT INTO market_image_qc
            (image_asset_id, semantic_match_status, card_number_match, language_match,
             tcg_match, raw_front_confirmed, public_allowed, rejection_reason,
             checked_at, qc_version)
        VALUES (%s, %s, %s, 0, 0, 1, 0, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            semantic_match_status=VALUES(semantic_match_status),
            card_number_match=VALUES(card_number_match),
            language_match=0,
            tcg_match=0,
            raw_front_confirmed=1,
            public_allowed=0,
            rejection_reason=VALUES(rejection_reason),
            checked_at=VALUES(checked_at),
            qc_version=VALUES(qc_version)
        """,
        (
            asset_id,
            SEMANTIC_PENDING,
            1 if card_number_match else 0,
            "pending_promotion:snk_ingest_not_street",
            now,
            QC_VERSION,
        ),
    )
    return {
        "assetId": asset_id,
        "sha256": sha,
        "sourceRef": source_ref,
        "sourceSha256": source_sha,
        "qc": SEMANTIC_PENDING,
        "publicAllowed": 0,
    }


def fetch_targets(
    cur,
    *,
    snk_ids: list[int] | None,
    missing_only: bool,
    upgrade_bad: bool,
    prefer_snk: bool,
    all_bound: bool,
    limit: int,
    watchlist_only: bool = False,
    tcg: str | None = None,
) -> list[dict[str, Any]]:
    tcg_clause = ""
    tcg_params: list[Any] = []
    if tcg:
        tcg_clause = " AND v.tcg_code=%s"
        tcg_params.append(tcg)

    if snk_ids:
        placeholders = ",".join(["%s"] * len(snk_ids))
        cur.execute(
            f"""
            SELECT i.variant_id, i.external_entity_id AS snk_id,
                   v.opaque_id, v.canonical_name, v.collector_number, v.set_name, v.tcg_code
            FROM catalog_source_identity i
            JOIN catalog_variant v ON v.id = i.variant_id
            WHERE i.source_code=%s AND i.external_entity_id IN ({placeholders})
            {tcg_clause}
            """,
            (SOURCE_CODE, *[str(x) for x in snk_ids], *tcg_params),
        )
        return list(cur.fetchall())

    where_extra = ""
    join_wl = ""
    if watchlist_only:
        join_wl = "JOIN market_gemrate_psa10_watchlist w ON w.variant_id=i.variant_id"
    if missing_only:
        where_extra = """
          AND NOT EXISTS (
            SELECT 1 FROM market_image_asset a
            WHERE a.variant_id=i.variant_id AND a.image_kind='raw_front'
          )
        """
    elif upgrade_bad:
        where_extra = """
          AND EXISTS (
            SELECT 1 FROM market_image_asset a
            LEFT JOIN market_image_qc q ON q.image_asset_id=a.id
            WHERE a.variant_id=i.variant_id AND a.image_kind='raw_front'
              AND (
                q.public_allowed=0
                OR q.rejection_reason IS NOT NULL
                OR a.width_px <> 429 OR a.height_px <> 600
                OR q.id IS NULL
              )
          )
        """
    elif prefer_snk and not all_bound:
        # Has some art, but source pointer is not SNK CDN / snkrdunk ref
        where_extra = """
          AND EXISTS (
            SELECT 1 FROM market_image_asset a
            WHERE a.variant_id=i.variant_id AND a.image_kind='raw_front'
          )
          AND NOT EXISTS (
            SELECT 1 FROM market_image_source_pointer p
            WHERE p.variant_id=i.variant_id AND p.image_kind='raw_front'
              AND (
                p.source_path LIKE '%%snkrdunk%%'
                OR p.source_path LIKE '%%upload_bg_removed%%'
              )
          )
        """
    elif all_bound:
        # every snk-bound row (optionally filtered by tcg)
        where_extra = ""

    cur.execute(
        f"""
        SELECT i.variant_id, i.external_entity_id AS snk_id,
               v.opaque_id, v.canonical_name, v.collector_number, v.set_name, v.tcg_code
        FROM catalog_source_identity i
        JOIN catalog_variant v ON v.id = i.variant_id
        {join_wl}
        WHERE i.source_code=%s
        {where_extra}
        {tcg_clause}
        ORDER BY i.variant_id
        LIMIT %s
        """,
        (SOURCE_CODE, *tcg_params, limit),
    )
    return list(cur.fetchall())


def process_one(
    api: SnkrdunkApi,
    cur,
    row: dict[str, Any],
    *,
    write: bool,
    force: bool,
    delay: float,
) -> dict[str, Any]:
    snk_id = int(row["snk_id"])
    vid = int(row["variant_id"])
    result: dict[str, Any] = {
        "snkId": snk_id,
        "variantId": vid,
        "name": row.get("canonical_name"),
        "collector": row.get("collector_number"),
    }

    try:
        multi = count_snk_ids_for_variant(cur, vid)
        if multi > 1:
            result["status"] = "reject"
            result["reason"] = f"multi_snk_identity:{multi}"
            return result

        meta = master_image_meta(api, snk_id)
        result["masterName"] = meta.get("name")
        url = meta.get("imageUrl")
        if not url:
            result["status"] = "reject"
            result["reason"] = "no_primary_media"
            return result
        result["imageUrl"] = url
        ok, reason = identity_title_ok(
            meta.get("name"),
            row,
            product_number=meta.get("productNumber"),
        )
        result["titleGate"] = reason
        if not ok:
            # needs_review stays reject for auto path; AI residual can --force later
            result["status"] = "reject" if not reason.startswith("needs_review") else "needs_review"
            result["reason"] = reason
            return result

        # Collector exact / productNumber exact → card_number_match candidate only (still not public)
        card_num_ok = (
            reason.startswith("title_hit:collector")
            or reason.startswith("title_hit:bracket_collector")
            or reason.startswith("title_hit:product_number")
        )

        raw = download_url(str(url))
        qc = qc_raw_image(raw)
        result["qc"] = qc
        alt = f"{row.get('canonical_name') or 'card'} {row.get('collector_number') or ''}".strip()
        block = store_block(raw, alt, native_rounded=bool(qc["native_rounded"]))
        result["block"] = {
            "sha256": block["sha256"],
            "width": block["width"],
            "height": block["height"],
        }
        mirror_sha(block["sha256"])
        source_ref = f"snkrdunk:{snk_id}:{url}"

        if not write:
            result["status"] = "dry_ok"
            result["wouldLand"] = "pending_review_not_public"
            return result

        # Skip only if already human/vision confirmed for this variant
        if not force:
            cur.execute(
                """
                SELECT a.id, q.public_allowed, q.semantic_match_status, p.source_path
                FROM market_image_asset a
                LEFT JOIN market_image_qc q ON q.image_asset_id=a.id
                LEFT JOIN market_image_source_pointer p
                  ON p.variant_id=a.variant_id AND p.image_kind='raw_front'
                WHERE a.variant_id=%s AND a.image_kind='raw_front'
                ORDER BY a.id DESC LIMIT 1
                """,
                (vid,),
            )
            ex = cur.fetchone()
            if ex and str(ex.get("semantic_match_status") or "") == (
                "human_or_vision_confirmed"
            ):
                result["status"] = "skip_already_confirmed"
                result["assetId"] = ex["id"]
                return result

        linked = upsert_abc(
            cur,
            variant_id=vid,
            block=block,
            source_ref=source_ref,
            raw=raw,
            card_number_match=1 if card_num_ok else 0,
        )
        result["status"] = "written_pending"
        result.update(linked)
    except Exception as e:
        result["status"] = "error"
        result["reason"] = str(e)[:300]
    finally:
        if delay > 0:
            time.sleep(delay)
    return result


def record_operational_result(
    result: dict[str, Any],
    *,
    report_path: Path,
    run_id: str,
) -> None:
    """Keep one stable retry item per SNK product instead of losing rejects in logs."""
    snk_id = result.get("snkId")
    if snk_id is None:
        return
    item_key = f"snk:{snk_id}"
    status = str(result.get("status") or "error")
    if status in {"written", "written_pending", "skip_already_confirmed"}:
        record_resolution(
            source=SOURCE_CODE,
            stage="image_ingest",
            script=Path(__file__),
            item_key=item_key,
            run_id=run_id,
            resolution="image_landed_or_already_confirmed",
            context={
                "snkId": snk_id,
                "variantId": result.get("variantId"),
                "status": status,
            },
            evidence_paths=[report_path],
        )
        return
    if status not in {"error", "reject", "needs_review"}:
        return
    raw_reason = str(result.get("reason") or status)
    reason_code = re.sub(r"[^a-z0-9_]+", "_", raw_reason.casefold()).strip("_")[:64]
    record_failure(
        source=SOURCE_CODE,
        stage="image_ingest",
        script=Path(__file__),
        item_key=item_key,
        reason_code=reason_code or status,
        message=raw_reason,
        retryable=True,
        run_id=run_id,
        url=result.get("imageUrl"),
        context={
            "snkId": snk_id,
            "variantId": result.get("variantId"),
            "name": result.get("name"),
            "collector": result.get("collector"),
            "status": status,
            "titleGate": result.get("titleGate"),
        },
        evidence_paths=[report_path],
        next_action="agent_review" if status != "error" else "retry",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="SNK master images → QC → CardZ A/B/C")
    parser.add_argument("--snk-id", type=int, action="append", dest="snk_ids")
    parser.add_argument("--ids-file", type=Path, help="text file: one snk id per line")
    parser.add_argument("--missing-only", action="store_true")
    parser.add_argument("--upgrade-bad", action="store_true")
    parser.add_argument(
        "--prefer-snk",
        action="store_true",
        help="Wave3: rewrite cards whose art is not SNK-sourced yet",
    )
    parser.add_argument(
        "--all-bound",
        action="store_true",
        help="All snk-bound identities (use with --tcg to scope OP)",
    )
    parser.add_argument("--tcg", type=str, default=None, help="e.g. one-piece / pokemon")
    parser.add_argument("--watchlist-only", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--force", action="store_true", help="overwrite even if public_allowed=1")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--delay", type=float, default=0.35)
    parser.add_argument(
        "--wave",
        choices=["1", "2", "3", "all"],
        default=None,
        help="shorthand: 1=missing 2=upgrade-bad 3=prefer-snk",
    )
    args = parser.parse_args()

    if args.wave == "1":
        args.missing_only = True
    elif args.wave == "2":
        args.upgrade_bad = True
        args.force = True
    elif args.wave == "3":
        args.prefer_snk = True
        args.force = True

    snk_ids = list(args.snk_ids or [])
    if args.ids_file and args.ids_file.is_file():
        for line in args.ids_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and line.lstrip("-").isdigit():
                snk_ids.append(int(line))

    load_env()
    os.environ["CARDZ_DB_HOST"] = "127.0.0.1"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    mode = (
        "missing"
        if args.missing_only
        else "upgrade"
        if args.upgrade_bad
        else "prefer"
        if args.prefer_snk and not args.all_bound
        else "all_bound"
        if args.all_bound
        else "ids"
        if snk_ids
        else "all_bound"
    )
    if args.tcg:
        mode = f"{mode}-{args.tcg}"
    report_path = REPORT_DIR / f"snk-image-ingest-{mode}-{ts}.jsonl"

    conn = db()
    cur = conn.cursor()
    targets = fetch_targets(
        cur,
        snk_ids=snk_ids or None,
        missing_only=args.missing_only,
        upgrade_bad=args.upgrade_bad,
        prefer_snk=args.prefer_snk,
        all_bound=args.all_bound or (not any([args.missing_only, args.upgrade_bad, args.prefer_snk, snk_ids])),
        limit=args.limit,
        watchlist_only=args.watchlist_only,
        tcg=args.tcg,
    )
    if not targets and args.snk_ids:
        for snk_id in args.snk_ids:
            record_failure(
                source=SOURCE_CODE,
                stage="image_ingest",
                script=Path(__file__),
                item_key=f"snk:{snk_id}",
                reason_code="no_identity_bind",
                message="catalog_source_identity binding is required before image ingest",
                retryable=True,
                run_id=ts,
                context={"snkId": snk_id},
                next_action="agent_review",
            )
        print(
            json.dumps(
                {
                    "error": "no_identity_bind",
                    "snkIds": args.snk_ids,
                    "hint": "bind catalog_source_identity first",
                },
                ensure_ascii=False,
            )
        )
        return 2

    api = SnkrdunkApi()
    # upgrade/prefer always force rewrite when QC-passed
    force = bool(
        args.force
        or snk_ids
        or args.upgrade_bad
        or args.prefer_snk
        or args.all_bound
    )
    summary: dict[str, Any] = {
        "mode": mode,
        "write": args.write,
        "force": force,
        "targets": len(targets),
        "written": 0,
        "dry_ok": 0,
        "reject": 0,
        "needs_review": 0,
        "skip": 0,
        "error": 0,
        "report": str(report_path),
    }

    with report_path.open("w", encoding="utf-8") as out:
        for i, row in enumerate(targets, 1):
            r = process_one(
                api,
                cur,
                row,
                write=args.write,
                force=force,
                delay=args.delay,
            )
            out.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
            out.flush()
            record_operational_result(r, report_path=report_path, run_id=ts)
            st = r.get("status")
            if st in ("written", "written_pending"):
                summary["written"] += 1
            elif st == "dry_ok":
                summary["dry_ok"] += 1
            elif st == "reject":
                summary["reject"] += 1
            elif st == "needs_review":
                summary["needs_review"] += 1
            elif st in ("skip_already_public", "skip_already_confirmed"):
                summary["skip"] += 1
            else:
                summary["error"] += 1
            if i % 10 == 0 or st in (
                "error",
                "reject",
                "needs_review",
                "written",
                "written_pending",
            ):
                print(
                    f"[{i}/{len(targets)}] {st} vid={r.get('variantId')} snk={r.get('snkId')} "
                    f"{r.get('name')} :: {r.get('reason') or r.get('titleGate') or ''}",
                    flush=True,
                )
            # commit every 25 writes so long runs survive crash
            if args.write and summary["written"] and summary["written"] % 25 == 0:
                conn.commit()
                print(f"[checkpoint] committed written={summary['written']}", flush=True)

    if args.write:
        conn.commit()
    else:
        conn.rollback()
    conn.close()

    summary_path = REPORT_DIR / f"snk-image-ingest-{mode}-{ts}_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    # non-zero only on hard errors; reject/needs_review are expected residual
    return 0 if summary["error"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
