# -*- coding: utf-8 -*-
"""Harvest PriceCharting card images ONCE from full900 HTML cache.

Reads local HTML only (no PC product crawl). Downloads 1600.jpg when the
image hash is known from product pages (preferred) or search rows.

Writes:
  - data/private/pricecharting_images/{variant_id}_{product_id}_1600.jpg
  - market_image_asset (image_kind=raw_front, private_object_key=pricecharting/{product_id}/1600.jpg)
  - market_image_source_pointer (public_allowed=0)
  - market_image_qc (public_allowed=0 candidate; NOT auto-public)

Fail-closed (036 Gate 1): bind only when product_id maps to
  operator_strict_source_identity (source_code=pricecharting).  The strict
  view enforces exact status + bound-field/catalog agreement + provider
  claims, so a bare match_status='exact' row is no longer enough.  Output
  stays candidate-only (public_allowed=0, awaiting_human_picker); promotion
  goes through the existing acceptance / freeze path.

Usage:
  python -X utf8 tools/harvest_pc_images_full900.py
  python -X utf8 tools/harvest_pc_images_full900.py --limit 25 --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pipelines"))

from qualified_pool_operator import db, load_env  # noqa: E402

FULL900 = ROOT / "data" / "private" / "pricecharting_session" / "html" / "full900"
OUT_DIR = ROOT / "data" / "private" / "pricecharting_images"
REPORT_PATH = ROOT / "temp" / "pm_w3_pc_image_harvest.json"

IMAGE_KIND = "raw_front"
QC_VERSION = "pc-full900-v1"
USER_AGENT = "Mozilla/5.0 (compatible; CARDZ-PC-ImageHarvest/1.0)"
REFERER = "https://www.pricecharting.com/"

RE_1600 = re.compile(
    r"https://storage\.googleapis\.com/images\.pricecharting\.com/([a-f0-9]+)/1600\.jpg"
)
RE_IMG = re.compile(
    r"https://storage\.googleapis\.com/images\.pricecharting\.com/([a-f0-9]+)/(60|240|1600)\.jpg"
)
RE_PID_ATTR = re.compile(r'data-product-id=["\'](\d+)["\']')
# search listing: thumbnail then title="productId"
RE_SEARCH_PAIR = re.compile(
    r"images\.pricecharting\.com/([a-f0-9]+)/(?:60|240|1600)\.jpg.{0,1200}?title=\"(\d{5,})\"",
    re.S,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_strict_pc_identities(cur) -> dict[str, int]:
    """product_id (external_entity_id) -> variant_id for strict PC binds only."""
    cur.execute(
        """
        SELECT variant_id, external_entity_id
        FROM operator_strict_source_identity
        WHERE source_code = 'pricecharting'
        """
    )
    out: dict[str, int] = {}
    for row in cur.fetchall():
        pid = str(row["external_entity_id"] or "").strip()
        if not pid:
            continue
        out[pid] = int(row["variant_id"])
    return out


def extract_json_product_id(html_path: Path) -> str | None:
    jpath = html_path.with_suffix(".json")
    if not jpath.is_file():
        return None
    try:
        payload = json.loads(jpath.read_text(encoding="utf-8"))
    except Exception:
        return None
    product = payload.get("product") if isinstance(payload, dict) else None
    if not isinstance(product, dict):
        return None
    pid = product.get("id")
    if pid is None or pid == "":
        return None
    return str(pid)


def page_product_id(html_path: Path, text: str) -> str | None:
    """Prefer JSON product.id; else most-common data-product-id on page."""
    jpid = extract_json_product_id(html_path)
    if jpid:
        return jpid
    pids = RE_PID_ATTR.findall(text)
    if not pids:
        return None
    return max(set(pids), key=pids.count)


def consider(
    best: dict[str, dict[str, Any]],
    *,
    product_id: str,
    image_hash: str,
    score: int,
    source: str,
    size_hint: str,
) -> None:
    if not product_id or not image_hash:
        return
    prev = best.get(product_id)
    if prev is None or score > int(prev["score"]):
        best[product_id] = {
            "product_id": product_id,
            "image_hash": image_hash,
            "score": score,
            "source": source,
            "size_hint": size_hint,
        }


def scan_full900() -> dict[str, dict[str, Any]]:
    """product_id -> best image hash candidate from local HTML only."""
    if not FULL900.is_dir():
        raise FileNotFoundError(f"full900 cache missing: {FULL900}")

    best: dict[str, dict[str, Any]] = {}
    html_files = sorted(FULL900.glob("*.html"))

    for path in html_files:
        name = path.name
        is_search = "search-products" in name
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        if is_search:
            # Prefer tr-local pairing; regex fallback covers most listings.
            for block in re.split(r"<tr\b", text):
                imgs = RE_IMG.findall(block)
                titles = re.findall(r'title="(\d{5,})"', block)
                if imgs and titles:
                    # prefer larger size if present in block
                    by_size = {"1600": [], "240": [], "60": []}
                    for h, s in imgs:
                        by_size.setdefault(s, []).append(h)
                    h = (by_size["1600"] or by_size["240"] or by_size["60"] or [imgs[0][0]])[0]
                    size = "1600" if by_size["1600"] else ("240" if by_size["240"] else "60")
                    consider(
                        best,
                        product_id=titles[0],
                        image_hash=h,
                        score=12 if size == "1600" else 8,
                        source=f"search:{name}",
                        size_hint=size,
                    )
            for h, pid in RE_SEARCH_PAIR.findall(text):
                consider(
                    best,
                    product_id=pid,
                    image_hash=h,
                    score=10,
                    source=f"search_re:{name}",
                    size_hint="60",
                )
            continue

        # Product pages — prefer 1600.jpg
        pid = page_product_id(path, text)
        if not pid:
            continue
        imgs = RE_IMG.findall(text)
        h1600 = [h for h, s in imgs if s == "1600"]
        h240 = list(dict.fromkeys(h for h, s in imgs if s == "240"))
        h60 = list(dict.fromkeys(h for h, s in imgs if s == "60"))
        has_json = extract_json_product_id(path) is not None
        base = 20 if has_json else 0

        if h1600:
            # Prefer product page 1600 over search thumbs
            consider(
                best,
                product_id=pid,
                image_hash=h1600[0],
                score=100 + base + (5 if len(set(h1600)) == 1 else 0),
                source=f"product1600:{name}",
                size_hint="1600",
            )
        elif len(h240) == 1:
            consider(
                best,
                product_id=pid,
                image_hash=h240[0],
                score=70 + base,
                source=f"product240:{name}",
                size_hint="240",
            )
        elif len(h60) == 1:
            consider(
                best,
                product_id=pid,
                image_hash=h60[0],
                score=50 + base,
                source=f"product60:{name}",
                size_hint="60",
            )
        elif h240 and has_json:
            consider(
                best,
                product_id=pid,
                image_hash=h240[0],
                score=40 + base,
                source=f"product240m:{name}",
                size_hint="240",
            )
        elif h60 and has_json:
            # weak: only with JSON product.id anchor
            consider(
                best,
                product_id=pid,
                image_hash=h60[0],
                score=25 + base,
                source=f"product60m:{name}",
                size_hint="60",
            )

    return best


def remote_url(image_hash: str, size: str = "1600") -> str:
    return f"https://storage.googleapis.com/images.pricecharting.com/{image_hash}/{size}.jpg"


def download_jpeg(url: str, timeout: float = 45.0) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": REFERER,
            "Accept": "image/jpeg,image/*;q=0.8,*/*;q=0.5",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    if len(data) < 100 or data[:3] != b"\xff\xd8\xff":
        raise ValueError(f"not a jpeg ({len(data)} bytes) url={url}")
    return data


def image_dims(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as im:
        w, h = im.size
    return int(w), int(h)


def local_path(variant_id: int, product_id: str) -> Path:
    return OUT_DIR / f"{variant_id}_{product_id}_1600.jpg"


def private_key(product_id: str) -> str:
    return f"pricecharting/{product_id}/1600.jpg"


def existing_asset(cur, variant_id: int, content_sha: str) -> int | None:
    cur.execute(
        """
        SELECT id FROM market_image_asset
        WHERE variant_id=%s AND image_kind=%s AND content_sha256=%s
        LIMIT 1
        """,
        (variant_id, IMAGE_KIND, content_sha),
    )
    row = cur.fetchone()
    return int(row["id"]) if row else None


def existing_pc_asset_for_key(cur, variant_id: int, product_id: str) -> dict[str, Any] | None:
    key = private_key(product_id)
    cur.execute(
        """
        SELECT id, content_sha256, private_object_key
        FROM market_image_asset
        WHERE variant_id=%s AND image_kind=%s AND private_object_key=%s
        LIMIT 1
        """,
        (variant_id, IMAGE_KIND, key),
    )
    return cur.fetchone()


def upsert_asset_and_qc(
    cur,
    *,
    variant_id: int,
    product_id: str,
    content_sha: str,
    width: int,
    height: int,
    remote: str,
    now: datetime,
) -> tuple[int, bool]:
    """Insert asset + pointer + qc(public_allowed=0). Returns (asset_id, inserted)."""
    key = private_key(product_id)
    source_ver = content_sha  # bytes version of stored JPEG
    remote_sha = sha256_text(remote)

    existing = existing_asset(cur, variant_id, content_sha)
    inserted = False
    if existing is not None:
        asset_id = existing
        # Keep private_object_key aligned if row already present under other key.
        cur.execute(
            """
            UPDATE market_image_asset
               SET private_object_key=%s,
                   mime_type='image/jpeg',
                   width_px=%s,
                   height_px=%s,
                   source_version_sha256=%s
             WHERE id=%s
            """,
            (key[:500], width, height, source_ver, asset_id),
        )
    else:
        cur.execute(
            """
            INSERT INTO market_image_asset
                (variant_id, image_kind, content_sha256, private_object_key, mime_type,
                 width_px, height_px, source_version_sha256, captured_at)
            VALUES (%s, %s, %s, %s, 'image/jpeg', %s, %s, %s, %s)
            """,
            (
                variant_id,
                IMAGE_KIND,
                content_sha,
                key[:500],
                width,
                height,
                source_ver,
                now,
            ),
        )
        asset_id = int(cur.lastrowid)
        inserted = True

    cur.execute(
        """
        INSERT INTO market_image_source_pointer
            (variant_id, image_kind, remote_url_sha256, source_path,
             source_version_sha256, public_allowed, observed_at)
        VALUES (%s, %s, %s, %s, %s, 0, %s)
        ON DUPLICATE KEY UPDATE
            remote_url_sha256=VALUES(remote_url_sha256),
            source_path=VALUES(source_path),
            public_allowed=0,
            observed_at=VALUES(observed_at)
        """,
        (
            variant_id,
            IMAGE_KIND,
            remote_sha,
            key[:500],
            source_ver,
            now,
        ),
    )

    cur.execute(
        """
        INSERT INTO market_image_qc
            (image_asset_id, semantic_match_status, card_number_match, language_match,
             tcg_match, raw_front_confirmed, public_allowed, rejection_reason,
             checked_at, qc_version)
        VALUES (%s, 'pc_harvest_candidate', 0, 0, 0, 0, 0, 'awaiting_human_picker', %s, %s)
        ON DUPLICATE KEY UPDATE
            semantic_match_status='pc_harvest_candidate',
            public_allowed=0,
            rejection_reason='awaiting_human_picker',
            checked_at=VALUES(checked_at)
        """,
        (asset_id, now, QC_VERSION),
    )
    return asset_id, inserted


def upsert_with_retry(conn, cur, **kwargs) -> tuple[int, bool]:
    """Retry once on InnoDB deadlock (concurrent writers on same tables)."""
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            return upsert_asset_and_qc(cur, **kwargs)
        except Exception as exc:
            last_exc = exc
            msg = str(exc).lower()
            if "deadlock" not in msg and "1213" not in str(exc):
                raise
            try:
                conn.rollback()
            except Exception:
                pass
            time.sleep(0.15 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def harvest(args: argparse.Namespace) -> dict[str, Any]:
    load_env()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    started = utc_now()
    scanned = scan_full900()

    conn = db()
    cur = conn.cursor()
    try:
        strict = load_strict_pc_identities(cur)
        strict_count = len(strict)

        # Fail-closed: only strict PC identities (operator_strict_source_identity)
        jobs: list[dict[str, Any]] = []
        missing_strict_id = 0
        for pid, meta in scanned.items():
            if pid not in strict:
                missing_strict_id += 1
                continue
            jobs.append(
                {
                    "product_id": pid,
                    "variant_id": strict[pid],
                    "image_hash": meta["image_hash"],
                    "source": meta["source"],
                    "size_hint": meta["size_hint"],
                    "score": meta["score"],
                }
            )

        # One job per variant: if multiple product_ids map oddly, keep highest score
        by_variant: dict[int, dict[str, Any]] = {}
        for job in jobs:
            vid = int(job["variant_id"])
            prev = by_variant.get(vid)
            if prev is None or int(job["score"]) > int(prev["score"]):
                by_variant[vid] = job
        jobs = sorted(by_variant.values(), key=lambda j: (int(j["variant_id"]), j["product_id"]))

        if args.limit and args.limit > 0:
            jobs = jobs[: int(args.limit)]

        stats: dict[str, Any] = {
            "startedAt": started,
            "finishedAt": None,
            "full900": str(FULL900),
            "outDir": str(OUT_DIR),
            "scannedProductIds": len(scanned),
            "strictIdentities": strict_count,
            "missing_strict_id": missing_strict_id,
            "jobsPlanned": len(jobs),
            "downloaded": 0,
            "skipped": 0,
            "skippedExistingFile": 0,
            "skippedExistingDb": 0,
            "failed": 0,
            "bound_variants": 0,
            "dbInserted": 0,
            "dbResumed": 0,
            "publicAllowedForced": 0,  # always 0 by policy
            "sourceBreakdown": Counter(),
            "failures": [],
            "dryRun": bool(args.dry_run),
        }

        bound_vids: set[int] = set()
        now = utc_now_naive()

        for i, job in enumerate(jobs, start=1):
            vid = int(job["variant_id"])
            pid = str(job["product_id"])
            image_hash = str(job["image_hash"])
            stats["sourceBreakdown"][str(job["source"]).split(":")[0]] += 1
            dest = local_path(vid, pid)
            url = remote_url(image_hash, "1600")

            try:
                # Resume: existing file with valid jpeg
                data: bytes | None = None
                if dest.is_file() and dest.stat().st_size > 100:
                    data = dest.read_bytes()
                    if data[:3] != b"\xff\xd8\xff":
                        data = None
                    else:
                        stats["skippedExistingFile"] += 1

                if data is None:
                    if args.dry_run:
                        stats["downloaded"] += 1  # would download
                        bound_vids.add(vid)
                        if i == 1 or i % 25 == 0 or i == len(jobs):
                            print(
                                f"[harvest] {i}/{len(jobs)} dry-run vid={vid} pid={pid}",
                                flush=True,
                            )
                        continue
                    data = download_jpeg(url)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    tmp = dest.with_suffix(".jpg.part")
                    tmp.write_bytes(data)
                    tmp.replace(dest)
                    stats["downloaded"] += 1
                    if args.delay > 0:
                        time.sleep(float(args.delay))
                else:
                    stats["skipped"] += 1

                content_sha = sha256_bytes(data)
                width, height = image_dims(data)

                # Resume DB: same private key or same content already bound
                prior = existing_pc_asset_for_key(cur, vid, pid)
                if prior and str(prior.get("content_sha256")) == content_sha:
                    upsert_with_retry(
                        conn,
                        cur,
                        variant_id=vid,
                        product_id=pid,
                        content_sha=content_sha,
                        width=width,
                        height=height,
                        remote=url,
                        now=now,
                    )
                    stats["skippedExistingDb"] += 1
                    stats["dbResumed"] += 1
                    bound_vids.add(vid)
                else:
                    _asset_id, inserted = upsert_with_retry(
                        conn,
                        cur,
                        variant_id=vid,
                        product_id=pid,
                        content_sha=content_sha,
                        width=width,
                        height=height,
                        remote=url,
                        now=now,
                    )
                    if inserted:
                        stats["dbInserted"] += 1
                    else:
                        stats["dbResumed"] += 1
                    bound_vids.add(vid)

                if not args.dry_run and i % 25 == 0:
                    conn.commit()

            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:
                    pass
                stats["failed"] += 1
                if len(stats["failures"]) < 40:
                    stats["failures"].append(
                        {
                            "variant_id": vid,
                            "product_id": pid,
                            "url": url,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                if stats["failed"] <= 5:
                    print(f"[harvest] FAIL vid={vid} pid={pid}: {exc}", file=sys.stderr, flush=True)

            if i == 1 or i % 25 == 0 or i == len(jobs):
                print(
                    f"[harvest] progress {i}/{len(jobs)} "
                    f"dl={stats['downloaded']} skip={stats['skipped']} "
                    f"fail={stats['failed']} bound={len(bound_vids)}",
                    flush=True,
                )

        if not args.dry_run:
            conn.commit()

        stats["bound_variants"] = len(bound_vids)
        stats["sourceBreakdown"] = dict(stats["sourceBreakdown"])
        stats["finishedAt"] = utc_now()
        stats["outFiles"] = len(list(OUT_DIR.glob("*_1600.jpg"))) if OUT_DIR.is_dir() else 0

        # Post-verify PC assets
        cur.execute(
            """
            SELECT COUNT(*) AS n
            FROM market_image_asset
            WHERE private_object_key LIKE 'pricecharting/%%/1600.jpg'
            """
        )
        stats["dbPcAssets"] = int(cur.fetchone()["n"])
        cur.execute(
            """
            SELECT COUNT(*) AS n
            FROM market_image_asset a
            JOIN market_image_qc q ON q.image_asset_id = a.id AND q.qc_version = %s
            WHERE a.private_object_key LIKE 'pricecharting/%%/1600.jpg'
              AND q.public_allowed = 1
            """,
            (QC_VERSION,),
        )
        stats["dbPcPublicAllowed"] = int(cur.fetchone()["n"])

        REPORT_PATH.write_text(
            json.dumps(stats, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({k: stats[k] for k in (
            "downloaded", "skipped", "failed", "bound_variants",
            "missing_strict_id", "jobsPlanned", "dbInserted", "dbPcAssets",
            "dbPcPublicAllowed", "strictIdentities", "scannedProductIds",
        )}, ensure_ascii=False, indent=2), flush=True)
        print(f"report: {REPORT_PATH}", flush=True)
        return stats
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="max jobs (0=all)")
    parser.add_argument("--delay", type=float, default=0.05, help="sleep between downloads")
    parser.add_argument("--dry-run", action="store_true", help="scan+plan only, no download/DB write")
    args = parser.parse_args()
    harvest(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
