#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Harvest sealed (原盒) box-front images.

Priority per SKU (DADDY: SNK 為王):
  1. SNKRDUNK product image (bind note / harvest)
  2. official page image hints
  3. PriceCharting div.cover img (1600/960/240) then og:image

Writes content-addressed webp (full + _200 + _600) into
data/public/market-assets/ and rows into market_sealed_image_asset.

Never auto-freezes: DADDY accepts once via
  operator_control.py sealed-accept-binding --sku ... --kind image

Usage:
  python -X utf8 pipelines/sealed_image_harvest.py --limit 20
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from urllib.parse import quote, urlparse

from sealed_discover_lib import (  # noqa: E402
    console_key,
    match_pc_inventory,
    pc_cover_urls,
    pick_pc_inventory_box,
    score_pc_console,
)
from sealed_runtime import HTML_DIR, OUT_DIR, db, load_env, utc_naive, utc_now  # noqa: E402

ASSETS_DIR = ROOT / "data" / "public" / "market-assets"
VENDOR_MANIFEST = ROOT / "data" / "vendor" / "sealed" / "media-manifest.json"
MIGRATION = ROOT / "pipelines" / "migrations" / "025_sealed_images.mysql.sql"
SNK_HARVEST = ROOT / "data" / "private" / "snkrdunk_brute" / "snkrdunk_all.jsonl"
PC_INVENTORY = ROOT / "data" / "runtime" / "sealed" / "pc_pattern" / "console-inventory.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
MIN_WIDTH = 150
OG_IMAGE_RE = re.compile(r'<meta property="og:image" content="([^"]+)"', re.I)


def split_sql(text: str) -> list[str]:
    parts, buf = [], []
    for line in text.splitlines():
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt:
                parts.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def manifest_hint_map() -> dict[str, list[str]]:
    if not VENDOR_MANIFEST.is_file():
        return {}
    doc = json.loads(VENDOR_MANIFEST.read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    for item in doc.get("items", []):
        key = str(item.get("id") or "")
        hints = [u for u in (item.get("image_hints") or []) if isinstance(u, str) and u.startswith("http")]
        if key and hints:
            out[key] = hints
    return out


def load_targets(cur, *, limit: int | None, refresh: bool) -> list[dict]:
    cur.execute(
        """
        SELECT p.id, p.sku_id, p.slug, p.game, p.lang, p.group_code, p.set_code, p.print_wave,
               p.product_kind, p.name_en, p.official_url,
          EXISTS(SELECT 1 FROM market_sealed_image_asset a WHERE a.sealed_id=p.id) AS has_asset,
          EXISTS(SELECT 1 FROM operator_sealed_binding_freeze f
                 WHERE f.sealed_id=p.id AND f.freeze_kind='image' AND f.acceptance_status='accepted') AS image_frozen
        FROM catalog_sealed_product p
        WHERE p.status <> 'no-box'
        ORDER BY p.id
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    rows = [r for r in rows if not int(r["image_frozen"] or 0) and (refresh or not int(r["has_asset"] or 0))]
    if limit:
        rows = rows[:limit]
    return rows


def hint_urls(cur, sealed_id: int) -> list[str]:
    cur.execute(
        "SELECT url FROM catalog_sealed_source_hint WHERE sealed_id=%s AND hint_kind IN ('image','official') ORDER BY id",
        (sealed_id,),
    )
    return [str(r["url"]) for r in cur.fetchall() if str(r["url"]).startswith("http")]


def load_snk_harvest_images() -> dict[int, str]:
    out: dict[int, str] = {}
    if not SNK_HARVEST.is_file():
        return out
    with SNK_HARVEST.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                doc = json.loads(line)
            except json.JSONDecodeError:
                continue
            item_id = doc.get("item_id")
            url = doc.get("image_url") or ""
            if item_id and url:
                out[int(item_id)] = str(url)
    return out


def load_pc_inventory() -> dict[str, list[dict]]:
    from collections import defaultdict
    from sealed_discover_lib import console_key

    by_console: dict[str, list[dict]] = defaultdict(list)
    if not PC_INVENTORY.is_file():
        return {}
    doc = json.loads(PC_INVENTORY.read_text(encoding="utf-8"))
    for box in doc.get("boxes") or []:
        slug = console_key(box.get("console") or "")
        if slug:
            by_console[slug].append(box)
    return by_console


def snk_image_urls(cur, sealed_id: int, harvest_images: dict[int, str]) -> list[str]:
    urls: list[str] = []
    cur.execute(
        """
        SELECT external_entity_id, note
        FROM catalog_sealed_source_identity
        WHERE sealed_id=%s AND source_code='snkrdunk' AND match_status<>'rejected'
        """,
        (sealed_id,),
    )
    for row in cur.fetchall():
        try:
            doc = json.loads(row["note"] or "{}")
        except json.JSONDecodeError:
            doc = {}
        url = str(doc.get("imageUrl") or "")
        if url:
            urls.append(url)
        ext = str(row["external_entity_id"] or "")
        if "apparels:" in ext:
            try:
                apparel = int(ext.rsplit(":", 1)[-1])
            except ValueError:
                apparel = None
            hv = harvest_images.get(apparel or -1, "")
            if hv:
                urls.append(hv)
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


# Exact SNK harvest item_id for leftover boxes (name-confirmed BOX rows, not singles).
SNK_HARVEST_BOX_ID = {
    ("ptcg", "jp", "S7D"): 12888,
    ("ptcg", "jp", "S5a"): 12878,
    ("ptcg", "jp", "S5I"): 12877,
    ("ptcg", "jp", "S5R"): 12876,
    ("ptcg", "jp", "SM4S"): 525911,
}

# Catalog set_code -> already-walked PC console slug. Image-only; not a bind.
WALKED_CONSOLE_ALIAS = {
    ("ptcg", "en", "BLW"): "pokemon-black-&-white",
    ("ptcg", "en", "HS"): "pokemon-heartgold-&-soulsilver",
    ("ptcg", "en", "DP"): "pokemon-diamond-&-pearl",
    ("ptcg", "en", "PL"): "pokemon-platinum",
    # SS: PC console exists but /booster-box redirects to search list — no box product.
    ("ptcg", "en", "FL"): "pokemon-fire-red-&-leaf-green",
    ("ptcg", "en", "RS"): "pokemon-ruby-&-sapphire",
    ("ptcg", "en", "MA"): "pokemon-team-magma-&-team-aqua",
    ("ptcg", "jp", "XY10"): "pokemon-japanese-awakening-psychic-king",
    ("ptcg", "jp", "SM5S"): "pokemon-japanese-ultra-sun",
    ("ptcg", "jp", "SM5M"): "pokemon-japanese-ultra-moon",
    ("ptcg", "jp", "SM3H"): "pokemon-japanese-battle-rainbow",
    ("ptcg", "jp", "SM2+"): "pokemon-japanese-facing-a-new-trial",
    ("ptcg", "jp", "SM2K"): "pokemon-japanese-islands-await-you",
    ("ptcg", "jp", "SM1M"): "pokemon-japanese-collection-moon",
    ("ptcg", "jp", "XY9"): "pokemon-japanese-rage-of-the-broken-heavens",
    ("ptcg", "jp", "L2"): "pokemon-japanese-reviving-legends",
    ("ptcg", "jp", "DPt1"): "pokemon-japanese-galactic's-conquest",
    ("ptcg", "jp", "DPs"): "pokemon-japanese-intense-fight-in-the-destroyed-sky",
    # ADV-WCP: PC /booster-box redirects to search list — no exact product page.
    ("ptcg", "jp", "ADV-HL"): "pokemon-japanese-undone-seal",
    ("ptcg", "jp", "ADV3"): "pokemon-japanese-rulers-of-the-heavens",
    ("ptcg", "jp", "jp1"): "pokemon-japanese-expansion-pack",
}

TITLE_NEEDLES = {
    "BLW": ("black & white", "black and white"),
    "HS": ("heartgold", "soul silver", "soulsilver"),
    "DP": ("diamond & pearl", "diamond and pearl"),
    "PL": ("platinum",),
    "SS": ("sandstorm",),
    "FL": ("firered", "fire red", "leafgreen", "leaf green"),
    "RS": ("ruby & sapphire", "ruby and sapphire"),
    "MA": ("magma", "aqua"),
    "XY10": ("awakening psychic king", "psychic king"),
    "SM5S": ("ultra sun",),
    "SM5M": ("ultra moon",),
    "SM3H": ("battle rainbow",),
    "SM2+": ("new trial",),
    "SM2K": ("islands await",),
    "SM1M": ("collection moon",),
    "XY9": ("broken heavens", "rage of the broken"),
    "L2": ("reviving legends",),
    "DPt1": ("galactic", "conquest"),
    "DPs": ("destroyed sky", "intense fight"),
    "ADV-WCP": ("world champions pack",),
    "ADV1": ("ruby", "sapphire", "expansion pack"),
    "ADV-HL": ("undone seal",),
    "ADV3": ("rulers of the heavens",),
    "jp1": ("expansion pack",),
    "S7D": ("towering", "skyscraping", "muten"),
    "S5a": ("matchless", "peerless", "twin fighter"),
    "S5I": ("single strike", "one-strike", "ichigeki"),
    "SM4S": ("awakened heroes", "awakens brave"),
}


def encode_pc_game_url(console_slug: str, product_slug: str = "booster-box") -> str:
    slug = console_key(console_slug)
    return f"https://www.pricecharting.com/game/{quote(slug, safe='-')}/{quote(product_slug, safe='-')}"


def normalize_pc_fetch_url(url: str) -> str:
    parsed = urlparse((url or "").replace("&amp;", "&"))
    path = "/".join(quote(part, safe="-") for part in parsed.path.split("/"))
    return parsed._replace(path=path).geturl()


def pc_url_is_identity(row: dict, url: str) -> bool:
    path = urlparse(url or "").path.strip("/").split("/")
    if len(path) < 2 or path[0] != "game":
        return False
    return score_pc_console(row, path[1]) >= 2.0


def pc_page_url(cur, row: dict, boxes_by_console: dict[str, list[dict]]) -> str | None:
    sealed_id = int(row["id"])
    cur.execute(
        """
        SELECT canonical_url FROM catalog_sealed_source_identity
        WHERE sealed_id=%s AND source_code='pricecharting' AND match_status<>'rejected'
        LIMIT 1
        """,
        (sealed_id,),
    )
    found = cur.fetchone()
    bind_url = str(found["canonical_url"]).replace("&amp;", "&") if found and found["canonical_url"] else ""
    if bind_url and pc_url_is_identity(row, bind_url):
        return normalize_pc_fetch_url(bind_url)
    cur.execute(
        """
        SELECT url FROM catalog_sealed_source_hint
        WHERE sealed_id=%s AND source_code='pricecharting'
          AND (url LIKE '%%/console/%%' OR url LIKE '%%/game/%%')
        LIMIT 1
        """,
        (sealed_id,),
    )
    hint = cur.fetchone()
    hint_url = str(hint["url"]) if hint else ""
    picked = match_pc_inventory(row, boxes_by_console, hint_url)
    if picked and picked.get("href"):
        href = str(picked["href"]).replace("&amp;", "&")
        if pc_url_is_identity(row, href):
            return normalize_pc_fetch_url(href)
    alias = WALKED_CONSOLE_ALIAS.get((str(row.get("game")), str(row.get("lang")), str(row.get("set_code"))))
    if alias:
        boxes = boxes_by_console.get(console_key(alias)) or []
        picked = pick_pc_inventory_box(row, boxes) if boxes else None
        if picked and picked.get("href"):
            return normalize_pc_fetch_url(str(picked["href"]))
        return encode_pc_game_url(alias)
    return None


def pc_html_path(sealed_id: int, url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    return HTML_DIR / f"{sealed_id}_{digest}.html"


def html_is_sku_product(html: str, row: dict) -> bool:
    head = (html or "")[:8000].lower()
    title_match = re.search(r"<title>([^<]+)", html or "", re.I)
    title = (title_match.group(1) if title_match else "").lower()
    if "booster box list" in title or "search-products" in head:
        return False
    if "ultra shiny" in title and str(row.get("set_code")) in {"SM5S", "SM5M"}:
        return False
    needles = TITLE_NEEDLES.get(str(row.get("set_code") or ""), ())
    if not needles:
        name = str(row.get("name_en") or "").lower()
        return bool(name) and name[:12] in title
    return any(n in title for n in needles)


def ensure_pc_html(sealed_id: int, url: str, row: dict, *, timeout_s: int) -> bool:
    out = pc_html_path(sealed_id, url)
    if out.exists():
        text = out.read_text(encoding="utf-8", errors="replace")[:200000]
        if html_is_sku_product(text, row) and ("VGPC" in text or 'class="cover"' in text):
            return True
        return False
    import pricecharting_cf_session as cf

    code = cf.cmd_fetch(url, out, headless=True, timeout_s=timeout_s)
    if code != 0 or not out.exists():
        return False
    text = out.read_text(encoding="utf-8", errors="replace")[:200000]
    if html_is_sku_product(text, row):
        return True
    out.unlink(missing_ok=True)
    return False


def pc_og_image(html_path: Path) -> str | None:
    try:
        match = OG_IMAGE_RE.search(html_path.read_text(encoding="utf-8", errors="replace")[:200000])
    except OSError:
        return None
    return match.group(1) if match else None


def pc_cover_image(html_path: Path) -> list[str]:
    try:
        html = html_path.read_text(encoding="utf-8", errors="replace")[:400000]
    except OSError:
        return []
    urls = pc_cover_urls(html)
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def manifest_key(row: dict) -> str:
    set_code = str(row["set_code"])
    wave = str(row["print_wave"])
    if wave in ("wave1", "wave2"):
        set_code = f"{set_code} W{wave[-1]}"
    return f"{row['group_code']}-{set_code}"


def candidates(
    cur,
    row: dict,
    manifest: dict[str, list[str]],
    *,
    harvest_images: dict[int, str],
    boxes_by_console: dict[str, list[dict]],
    fetch_pc: bool,
    timeout_s: int,
) -> list[tuple[str, str]]:
    """Ordered (source_code, url) candidates. SNK first."""
    out: list[tuple[str, str]] = []
    for url in snk_image_urls(cur, int(row["id"]), harvest_images):
        out.append(("snkrdunk", url))
    box_id = SNK_HARVEST_BOX_ID.get((str(row.get("game")), str(row.get("lang")), str(row.get("set_code"))))
    if box_id:
        hv = harvest_images.get(int(box_id), "")
        if hv and ("snkrdunk", hv) not in out:
            out.append(("snkrdunk", hv))
    for url in manifest.get(manifest_key(row), []):
        if "pricecharting.com" in url:
            continue
        out.append(("official", url))
    for url in hint_urls(cur, int(row["id"])):
        if "/game/" in url or "/console/" in url or url.lower().endswith((".html", ".php", "/")):
            continue
        if any(url == existing for _, existing in out):
            continue
        out.append(("official", url))
    official = str(row.get("official_url") or "")
    if "30th.pokemon-card.com/product/m6a" in official:
        out.append(("official", "https://www.30th.pokemon-card.com/m6a_ogp.jpg"))
    if official.startswith("https://www.pokemon-card.com/ex/"):
        base = official.rstrip("/")
        for suffix in (
            "/assets/images/hero-visual.jpg",
            "/assets/images/hero-visual.png",
            "/assets/images/ogp.png",
        ):
            url = base + suffix
            if ("official", url) not in out:
                out.append(("official", url))
    if fetch_pc:
        page = pc_page_url(cur, row, boxes_by_console)
        if page and ensure_pc_html(int(row["id"]), page, row, timeout_s=timeout_s):
            html_path = pc_html_path(int(row["id"]), page)
            for url in pc_cover_image(html_path):
                out.append(("pricecharting", url))
            pc = pc_og_image(html_path)
            if pc and ("pricecharting", pc) not in out:
                out.append(("pricecharting", pc))
    return out


def to_webp_variants(raw: bytes) -> tuple[bytes, bytes, bytes, int, int] | None:
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:  # noqa: BLE001
        return None
    if img.width < MIN_WIDTH or img.height < MIN_WIDTH:
        return None
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")

    def encode(image, width: int | None) -> bytes:
        target = image
        if width and image.width > width:
            height = round(image.height * width / image.width)
            target = image.resize((width, height), Image.LANCZOS)
        buf = io.BytesIO()
        target.save(buf, format="WEBP", quality=90, method=5)
        return buf.getvalue()

    return encode(img, None), encode(img, 200), encode(img, 600), img.width, img.height


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--refresh", action="store_true", help="re-harvest SKUs that already have an asset")
    ap.add_argument("--delay", type=float, default=0.4)
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--skip-pc-fetch", action="store_true", help="do not CDP-fetch PC product pages")
    args = ap.parse_args()

    import requests

    session = requests.Session()
    session.headers["User-Agent"] = UA

    def referer_for(url: str) -> str:
        if "pricecharting.com" in url or "images.pricecharting.com" in url:
            return "https://www.pricecharting.com/"
        if "pokemon-card.com" in url:
            return "https://www.pokemon-card.com/"
        if "onepiece-cardgame.com" in url:
            return "https://en.onepiece-cardgame.com/"
        if "bulbagarden.net" in url:
            return "https://archives.bulbagarden.net/"
        return "https://snkrdunk.com/"

    load_env()
    conn = db()
    results: list[dict[str, Any]] = []
    try:
        cur = conn.cursor()
        for stmt in split_sql(MIGRATION.read_text(encoding="utf-8")):
            cur.execute(stmt)
        conn.commit()
        manifest = manifest_hint_map()
        harvest_images = load_snk_harvest_images()
        boxes_by_console = load_pc_inventory()
        targets = load_targets(cur, limit=args.limit, refresh=args.refresh)
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        def try_urls(pairs: list[tuple[str, str]]):
            for source, url in pairs:
                try:
                    response = session.get(url, timeout=30, headers={"Referer": referer_for(url)})
                    if response.status_code != 200 or len(response.content) < 800:
                        continue
                    variants = to_webp_variants(response.content)
                    if not variants:
                        continue
                    return (source, url, variants)
                except Exception:  # noqa: BLE001
                    continue
                finally:
                    time.sleep(args.delay)
            return None

        for row in targets:
            item: dict[str, Any] = {"sku": row["sku_id"]}
            chosen = try_urls(
                candidates(
                    cur,
                    row,
                    manifest,
                    harvest_images=harvest_images,
                    boxes_by_console=boxes_by_console,
                    fetch_pc=False,
                    timeout_s=args.timeout,
                )
            )
            if not chosen and not args.skip_pc_fetch:
                chosen = try_urls(
                    candidates(
                        cur,
                        row,
                        manifest,
                        harvest_images=harvest_images,
                        boxes_by_console=boxes_by_console,
                        fetch_pc=True,
                        timeout_s=args.timeout,
                    )
                )
            if not chosen:
                item["status"] = "no_usable_image"
                results.append(item)
                continue
            source, url, (full, w200, w600, width, height) = chosen
            sha = hashlib.sha256(full).hexdigest()
            (ASSETS_DIR / f"{sha}.webp").write_bytes(full)
            (ASSETS_DIR / f"{sha}_200.webp").write_bytes(w200)
            (ASSETS_DIR / f"{sha}_600.webp").write_bytes(w600)
            cur.execute(
                """
                INSERT INTO market_sealed_image_asset
                  (sealed_id, image_kind, content_sha256, source_code, source_url, mime_type,
                   width_px, height_px, captured_at)
                VALUES (%s,'box_front',%s,%s,%s,'image/webp',%s,%s,%s)
                ON DUPLICATE KEY UPDATE source_url=VALUES(source_url), captured_at=VALUES(captured_at)
                """,
                (int(row["id"]), sha, source, url[:700], width, height,
                 utc_naive().strftime("%Y-%m-%d %H:%M:%S.%f")),
            )
            conn.commit()
            item.update({"status": "ok", "source": source, "sha256": sha, "width": width, "height": height})
            results.append(item)
    finally:
        conn.close()

    ok = len([r for r in results if r["status"] == "ok"])
    doc = {"asOf": utc_now(), "action": "sealed-image-harvest", "attempted": len(results), "ok": ok, "items": results}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "image-harvest-receipt.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**doc, "items": results[:30]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
