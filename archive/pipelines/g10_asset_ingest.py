#!/usr/bin/env python3
"""把本機 G10 (`../grade10-scraper`) 嘅卡圖同 `asset_info.json` 入庫。

點解要有呢個檔：`market_image_asset` / `market_image_qc` /
`market_image_source_pointer` 三張表**全部 0 行**。`db_runtime.py` 有個
`image_metadata` 分支識寫 pointer，但由頭到尾冇人餵過佢。與此同時 G10 本機硬碟
坐住 594 張卡圖同 636 份 `asset_info.json`（連上游圖片 URL）。呢個 loader 就係
嗰條線：G10 檔案樹 → 三張 image table。冇新爬蟲，冇新契約。

寫入三張表：
  * `market_image_source_pointer`  上游圖片 URL 指針（由 asset_info.json 嚟）
  * `market_image_asset`           本機圖檔本體（content sha + 真實尺寸）
  * `market_image_qc`              逐張圖嘅 QC 判定

## 三個 sha256 係三樣嘢，唔准互相頂替

呢個係最易做錯、而且錯咗會偽造來源鏈嘅位（`image_store_consolidate.recover()`
個 docstring 都特登講過同一件事）：

  * `content_sha256`        = **圖檔本身 bytes** 嘅 sha256
  * `source_version_sha256` = **`asset_info.json` bytes** 嘅 sha256（即係我哋讀
                              嗰份來源記錄嘅版本）
  * `remote_url_sha256`     = **`asset_info["image"]` 嗰條 URL 字串**嘅 sha256

所以一張圖**冇配對到 asset_info.json 就冇合法嘅 `source_version_sha256`**，
唯一啱嘅做法係 quarantine，唔准攞 `content_sha256` 頂上去湊夠 NOT NULL。

## private_object_key 指去邊

`market_image_asset.private_object_key` 要指到一個真係搵得返嗰啲 bytes 嘅位。
G10 `data/images/` 本身唔喺呢個 repo 入面（107 MB，冇 git-tracked，亦唔應該
track），所以用 `grade10_full_freeze.py` 整落嘅 private landing freeze：

    g10/full/{runId}/payload/images/{filename}

入庫前逐張 re-hash landing 副本同 live 源檔對數，唔一致就 quarantine —— 個 key
唔可以指去一份內容唔同嘅嘢。

## 卡片對應

唯一合法路徑係 `catalog_source_identity`，唔准靠卡名（641 個目錄得 319 個
unique 卡名，卡名 match 係假嘅）：
  * `snkrdunk_{id}.*`   → `source_code='snkrdunk'`, external_entity_id = `{id}`
  * `altxyz_{uuid}.*`   → `source_code='ebay'`,     external_entity_id = `{uuid}`
對唔到就如實計入 rejected，唔會靜靜雞當冇事。

## QC 只記量得到嘅嘢

`card_number_match` / `language_match` 係真係攞 asset_info 對 `catalog_variant`
比出嚟。`tcg_match` 恆為 0 —— asset_info **根本冇寫 TCG**，冇得確認就唔准當 1。
`raw_front_confirmed` 用四角 alpha 判原生去背卡圖（同 `is_native_rounded()` 同
一把尺），唔係靠檔名估。`public_allowed` 全部 0：入私庫係一件事，出街係另一件
事，唔喺呢度決定。

## Idempotency

`run_key` 用**輸入內容**算（唔用時間戳），所以同一份輸入再跑一次，run 行會
reuse 返同一個 `run_id`，三張表全部 `INSERT IGNORE` 淨增 0。

Exit codes: 0 = 正常, 1 = 冇嘢做, 2 = G10 目錄 / landing freeze 唔見。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args

DEFAULT_G10_ROOT = ROOT.parent / "grade10-scraper" / "data"
DEFAULT_LANDING = ROOT / "data" / "runtime" / "private-landing"

SOURCE_CODE = "g10_asset"
IMAGE_KIND = "raw_front"
QC_VERSION = "g10-asset-v1"
SEMANTIC_MATCH_STATUS = "source_id_exact"

# G10 目錄前綴 → catalog_source_identity.source_code
PROVIDER_IDENTITY_SOURCE = {"altxyz": "ebay", "snkrdunk": "snkrdunk"}

PIL_FORMAT_MIME = {"JPEG": "image/jpeg", "WEBP": "image/webp", "PNG": "image/png"}

# asset_info["language"] → catalog_variant.card_language
LANGUAGE_ALIASES = {"jp": "ja", "ja": "ja", "en": "en"}

# 四角 alpha 低過呢個值先當原生去背圓角卡圖（同 native_image_resolver 同一把尺）
CORNER_ALPHA_MAX = 10


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def mtime_utc(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(tzinfo=None)


def normalise_card_number(value: str) -> str:
    """`001/024` / `SN 001` / `1` 統一到可比較形式；比唔到嘅返空字串。"""
    cleaned = re.sub(r"[^0-9a-z]", "", str(value or "").lower())
    return cleaned.lstrip("0")


@dataclass
class SourceRecord:
    """一份 `asset_info.json`。"""

    provider: str
    external_id: str
    path: Path
    version_sha256: str
    data: Mapping[str, Any]
    observed_at: datetime
    variant_id: int | None = None

    @property
    def remote_url(self) -> str:
        return str(self.data.get("image") or "")


@dataclass
class ImageRecord:
    """一個 `data/images/` 圖檔。"""

    provider: str
    external_id: str
    path: Path
    content_sha256: str
    width_px: int
    height_px: int
    mime_type: str
    has_alpha_corners: bool
    captured_at: datetime
    variant_id: int | None = None
    private_object_key: str = ""
    source: SourceRecord | None = None


@dataclass
class Tally:
    observed: int = 0
    accepted: int = 0
    quarantined: int = 0
    rejected: int = 0
    reasons: Counter = field(default_factory=Counter)

    def drop(self, bucket: str, reason: str) -> None:
        setattr(self, bucket, getattr(self, bucket) + 1)
        self.reasons[reason] += 1


def scan_source_records(cards_root: Path) -> tuple[list[SourceRecord], Counter]:
    """行 `data/cards/{provider}/{external_id}/asset_info.json`。"""
    records: list[SourceRecord] = []
    skipped: Counter = Counter()
    for provider_dir in sorted(p for p in cards_root.iterdir() if p.is_dir()):
        provider = provider_dir.name
        if provider not in PROVIDER_IDENTITY_SOURCE:
            skipped[f"unknown_provider:{provider}"] += 1
            continue
        for card_dir in sorted(p for p in provider_dir.iterdir() if p.is_dir()):
            path = card_dir / "asset_info.json"
            if not path.exists():
                skipped["missing_asset_info"] += 1
                continue
            raw = path.read_bytes()
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                skipped["asset_info_unparsable"] += 1
                continue
            records.append(
                SourceRecord(
                    provider=provider,
                    external_id=card_dir.name,
                    path=path,
                    version_sha256=sha256_bytes(raw),
                    data=data,
                    observed_at=mtime_utc(path),
                )
            )
    return records, skipped


def probe_image(path: Path) -> tuple[int, int, str, bool]:
    """真係開圖度尺寸／格式／四角 alpha，唔靠副檔名估。"""
    with Image.open(path) as im:
        width, height = im.size
        mime = PIL_FORMAT_MIME.get(str(im.format or ""), "")
        has_alpha_corners = False
        if im.mode in {"RGBA", "LA"} or "transparency" in im.info:
            rgba = im.convert("RGBA")
            w, h = rgba.size
            corners = [
                rgba.getpixel((0, 0))[3],
                rgba.getpixel((w - 1, 0))[3],
                rgba.getpixel((0, h - 1))[3],
                rgba.getpixel((w - 1, h - 1))[3],
            ]
            has_alpha_corners = all(a < CORNER_ALPHA_MAX for a in corners)
    return width, height, mime, has_alpha_corners


def scan_images(images_root: Path) -> tuple[list[ImageRecord], Counter]:
    records: list[ImageRecord] = []
    skipped: Counter = Counter()
    for path in sorted(p for p in images_root.iterdir() if p.is_file()):
        provider, _, external_id = path.stem.partition("_")
        if not external_id or provider not in PROVIDER_IDENTITY_SOURCE:
            skipped["unparsable_filename"] += 1
            continue
        raw = path.read_bytes()
        try:
            width, height, mime, alpha_corners = probe_image(path)
        except Exception as exc:  # noqa: BLE001 - Pillow 會掟好多種 decode error
            skipped[f"decode_failed:{type(exc).__name__}"] += 1
            continue
        if not mime or width <= 0 or height <= 0:
            skipped["unsupported_format"] += 1
            continue
        records.append(
            ImageRecord(
                provider=provider,
                external_id=external_id,
                path=path,
                content_sha256=sha256_bytes(raw),
                width_px=width,
                height_px=height,
                mime_type=mime,
                has_alpha_corners=alpha_corners,
                captured_at=mtime_utc(path),
            )
        )
    return records, skipped


def resolve_landing_run(landing_root: Path) -> Path:
    """揀最新嗰個 full freeze run 嘅 images 目錄。"""
    full_root = landing_root / "g10" / "full"
    if not full_root.exists():
        raise FileNotFoundError(f"landing freeze 唔見: {full_root}")
    candidates = [p for p in full_root.iterdir() if (p / "payload" / "images").is_dir()]
    if not candidates:
        raise FileNotFoundError(f"landing freeze 冇 payload/images: {full_root}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def attach_private_keys(
    images: list[ImageRecord], landing_run: Path, tally: Tally
) -> list[ImageRecord]:
    """逐張同 landing 副本對 sha，唔一致就 quarantine。"""
    images_dir = landing_run / "payload" / "images"
    run_id = landing_run.name
    kept: list[ImageRecord] = []
    for record in images:
        landed = images_dir / record.path.name
        if not landed.exists():
            tally.drop("quarantined", "no_private_landing_copy")
            continue
        if sha256_bytes(landed.read_bytes()) != record.content_sha256:
            tally.drop("quarantined", "private_landing_content_drift")
            continue
        record.private_object_key = f"g10/full/{run_id}/payload/images/{record.path.name}"
        kept.append(record)
    return kept


def load_identity_map(connection) -> dict[tuple[str, str], int]:
    """`catalog_source_identity` 係唯一合法對應，唔准用卡名 join。"""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT variant_id, source_code, external_entity_id FROM catalog_source_identity "
            "WHERE source_code IN ('ebay', 'snkrdunk')"
        )
        rows = cursor.fetchall()
    mapping: dict[tuple[str, str], int] = {}
    for row in rows:
        mapping[(str(row["source_code"]), str(row["external_entity_id"]))] = int(row["variant_id"])
    return mapping


def load_variants(connection, variant_ids: set[int]) -> dict[int, Mapping[str, Any]]:
    if not variant_ids:
        return {}
    ids = sorted(variant_ids)
    placeholders = ", ".join(["%s"] * len(ids))
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, canonical_name, collector_number, card_language, tcg_code "
            f"FROM catalog_variant WHERE id IN ({placeholders})",
            ids,
        )
        return {int(row["id"]): row for row in cursor.fetchall()}


def build_qc(image: ImageRecord, variant: Mapping[str, Any] | None) -> dict[str, Any]:
    """只記真係量得到嘅嘢，量唔到就 0 + 寫低點解。"""
    source_data = image.source.data if image.source else {}
    unconfirmed: list[str] = []

    source_number = normalise_card_number(source_data.get("cardId", ""))
    variant_number = normalise_card_number((variant or {}).get("collector_number", ""))
    card_number_match = bool(source_number and variant_number and source_number == variant_number)
    if not card_number_match:
        unconfirmed.append("card_number" if source_number else "card_number_absent_in_source")

    source_language = LANGUAGE_ALIASES.get(str(source_data.get("language") or "").lower(), "")
    variant_language = str((variant or {}).get("card_language") or "").lower()
    language_match = bool(source_language and source_language == variant_language)
    if not language_match:
        unconfirmed.append("language" if source_language else "language_absent_in_source")

    # asset_info 冇 TCG 欄位，冇得確認 → 恆 0，唔准當 1
    unconfirmed.append("tcg_unstated_in_source")

    if not image.has_alpha_corners:
        unconfirmed.append("raw_front_not_alpha_native")

    return {
        "semantic_match_status": SEMANTIC_MATCH_STATUS,
        "card_number_match": int(card_number_match),
        "language_match": int(language_match),
        "tcg_match": 0,
        "raw_front_confirmed": int(image.has_alpha_corners),
        "public_allowed": 0,
        "rejection_reason": ("unconfirmed:" + ",".join(unconfirmed))[:500] if unconfirmed else None,
    }


def build(connection, cards_root: Path, images_root: Path, landing_root: Path) -> dict[str, Any]:
    sources, source_skips = scan_source_records(cards_root)
    images, image_skips = scan_images(images_root)

    source_tally = Tally(observed=len(sources))
    image_tally = Tally(observed=len(images))
    for reason, count in source_skips.items():
        source_tally.observed += count
        source_tally.rejected += count
        source_tally.reasons[reason] += count
    for reason, count in image_skips.items():
        image_tally.observed += count
        image_tally.rejected += count
        image_tally.reasons[reason] += count

    landing_run = resolve_landing_run(landing_root)
    images = attach_private_keys(images, landing_run, image_tally)

    identity = load_identity_map(connection)

    source_index: dict[tuple[str, str], SourceRecord] = {}
    pointer_rows: list[SourceRecord] = []
    for record in sources:
        source_index[(record.provider, record.external_id)] = record
        variant_id = identity.get((PROVIDER_IDENTITY_SOURCE[record.provider], record.external_id))
        if variant_id is None:
            source_tally.drop("rejected", "no_identity_match")
            continue
        if not record.remote_url:
            source_tally.drop("quarantined", "asset_info_missing_image_url")
            continue
        record.variant_id = variant_id
        pointer_rows.append(record)

    asset_rows: list[ImageRecord] = []
    for record in images:
        variant_id = identity.get((PROVIDER_IDENTITY_SOURCE[record.provider], record.external_id))
        if variant_id is None:
            image_tally.drop("rejected", "no_identity_match")
            continue
        source = source_index.get((record.provider, record.external_id))
        if source is None:
            # 冇 asset_info.json 就冇合法 source_version_sha256，唔准用 content sha 頂替
            image_tally.drop("quarantined", "no_paired_asset_info")
            continue
        record.variant_id = variant_id
        record.source = source
        asset_rows.append(record)

    source_tally.accepted = len(pointer_rows)
    image_tally.accepted = len(asset_rows)

    variants = load_variants(connection, {r.variant_id for r in asset_rows if r.variant_id})
    qc_rows = {r.content_sha256: build_qc(r, variants.get(r.variant_id or 0)) for r in asset_rows}

    fingerprints = sorted(
        [f"pointer|{r.variant_id}|{r.version_sha256}" for r in pointer_rows]
        + [f"asset|{r.variant_id}|{r.content_sha256}" for r in asset_rows]
    )
    effective_at = max(
        [r.observed_at for r in pointer_rows] + [r.captured_at for r in asset_rows],
        default=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    return {
        "pointer_rows": pointer_rows,
        "asset_rows": asset_rows,
        "qc_rows": qc_rows,
        "variants": variants,
        "source_tally": source_tally,
        "image_tally": image_tally,
        "landing_run": landing_run.name,
        "effective_at": effective_at,
        "payload_sha256": sha256_text("\n".join(fingerprints)),
        "manifest_sha256": sha256_text(
            "\n".join(sorted(r.content_sha256 for r in asset_rows) + sorted(r.version_sha256 for r in pointer_rows))
        ),
    }


def table_count(cursor, table: str) -> int:
    cursor.execute(f"SELECT COUNT(*) AS c FROM {table}")
    return int(cursor.fetchone()["c"])


TABLES = ("market_image_source_pointer", "market_image_asset", "market_image_qc")


def write(connection, plan: Mapping[str, Any]) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    source_tally: Tally = plan["source_tally"]
    image_tally: Tally = plan["image_tally"]

    # run_key 用輸入內容算，唔用時間戳 —— 同一份輸入再跑會 reuse 同一個 run_id
    run_key = sha256_text(f"g10_asset|{plan['payload_sha256']}|{plan['manifest_sha256']}")

    with connection.cursor() as cursor:
        before = {t: table_count(cursor, t) for t in TABLES}

        cursor.execute(
            """
            INSERT INTO market_ingest_run
                (run_key, source_code, ingest_mode, effective_at, payload_sha256, manifest_sha256,
                 status, observed_count, started_at)
            VALUES (%s, %s, 'backfill', %s, %s, %s, 'running', %s, %s)
            ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id), status = 'running', started_at = VALUES(started_at)
            """,
            (
                run_key,
                SOURCE_CODE,
                plan["effective_at"],
                plan["payload_sha256"],
                plan["manifest_sha256"],
                source_tally.observed + image_tally.observed,
                started_at,
            ),
        )
        run_id = int(cursor.lastrowid)

        for record in plan["pointer_rows"]:
            cursor.execute(
                """
                INSERT IGNORE INTO market_image_source_pointer
                    (variant_id, image_kind, remote_url_sha256, source_path,
                     source_version_sha256, public_allowed, observed_at)
                VALUES (%s, %s, %s, %s, %s, 0, %s)
                """,
                (
                    record.variant_id,
                    IMAGE_KIND,
                    sha256_text(record.remote_url),
                    str(record.path.relative_to(record.path.parents[3]))[:500],
                    record.version_sha256,
                    record.observed_at,
                ),
            )

        for record in plan["asset_rows"]:
            assert record.source is not None
            cursor.execute(
                """
                INSERT IGNORE INTO market_image_asset
                    (variant_id, image_kind, content_sha256, private_object_key, mime_type,
                     width_px, height_px, source_version_sha256, captured_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record.variant_id,
                    IMAGE_KIND,
                    record.content_sha256,
                    record.private_object_key[:500],
                    record.mime_type,
                    record.width_px,
                    record.height_px,
                    record.source.version_sha256,
                    record.captured_at,
                ),
            )
            cursor.execute(
                "SELECT id FROM market_image_asset WHERE variant_id=%s AND image_kind=%s AND content_sha256=%s",
                (record.variant_id, IMAGE_KIND, record.content_sha256),
            )
            row = cursor.fetchone()
            if not row:
                continue
            qc = plan["qc_rows"][record.content_sha256]
            cursor.execute(
                """
                INSERT IGNORE INTO market_image_qc
                    (image_asset_id, semantic_match_status, card_number_match, language_match,
                     tcg_match, raw_front_confirmed, public_allowed, rejection_reason,
                     checked_at, qc_version)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    int(row["id"]),
                    qc["semantic_match_status"],
                    qc["card_number_match"],
                    qc["language_match"],
                    qc["tcg_match"],
                    qc["raw_front_confirmed"],
                    qc["public_allowed"],
                    qc["rejection_reason"],
                    started_at,
                    QC_VERSION,
                ),
            )

        cursor.execute(
            """
            UPDATE market_ingest_run
               SET status = 'completed', observed_count = %s, accepted_count = %s,
                   quarantined_count = %s, rejected_count = %s, completed_at = %s
             WHERE id = %s
            """,
            (
                source_tally.observed + image_tally.observed,
                source_tally.accepted + image_tally.accepted,
                source_tally.quarantined + image_tally.quarantined,
                source_tally.rejected + image_tally.rejected,
                datetime.now(timezone.utc).replace(tzinfo=None),
                run_id,
            ),
        )

        after = {t: table_count(cursor, t) for t in TABLES}
    connection.commit()
    return {"run_id": run_id, "run_key": run_key, "before": before, "after": after}


def print_report(plan: Mapping[str, Any], write_result: Mapping[str, Any] | None) -> None:
    source_tally: Tally = plan["source_tally"]
    image_tally: Tally = plan["image_tally"]
    assets = plan["asset_rows"]

    print("== G10 asset ingest ==")
    print(f"  landing freeze run : {plan['landing_run']}")
    print(f"  effective_at       : {plan['effective_at']}")

    print("\n-- asset_info.json (→ market_image_source_pointer) --")
    print(f"  observed    : {source_tally.observed}")
    print(f"  accepted    : {source_tally.accepted}")
    print(f"  quarantined : {source_tally.quarantined}")
    print(f"  rejected    : {source_tally.rejected}")
    for reason, count in source_tally.reasons.most_common():
        print(f"    - {reason}: {count}")

    print("\n-- images (→ market_image_asset + market_image_qc) --")
    print(f"  observed    : {image_tally.observed}")
    print(f"  accepted    : {image_tally.accepted}")
    print(f"  quarantined : {image_tally.quarantined}")
    print(f"  rejected    : {image_tally.rejected}")
    for reason, count in image_tally.reasons.most_common():
        print(f"    - {reason}: {count}")

    print("\n-- accepted 圖尺寸分佈 --")
    sizes = Counter(f"{r.width_px}x{r.height_px}" for r in assets)
    print(f"  429x600 (canon) : {sizes.get('429x600', 0)}")
    print(f"  其他尺寸        : {sum(sizes.values()) - sizes.get('429x600', 0)}")
    for size, count in sizes.most_common(8):
        print(f"    - {size}: {count}")

    print("\n-- accepted 格式 / 原生去背 --")
    for mime, count in Counter(r.mime_type for r in assets).most_common():
        print(f"    - {mime}: {count}")
    native = sum(1 for r in assets if r.has_alpha_corners)
    print(f"  原生 RGBA 四角透明 : {native}")
    print(f"  非原生（要後處理） : {len(assets) - native}")

    print("\n-- QC --")
    qc = plan["qc_rows"]
    print(f"  card_number_match : {sum(v['card_number_match'] for v in qc.values())}/{len(qc)}")
    print(f"  language_match    : {sum(v['language_match'] for v in qc.values())}/{len(qc)}")
    print(f"  raw_front_confirmed: {sum(v['raw_front_confirmed'] for v in qc.values())}/{len(qc)}")
    print(f"  tcg_match         : 0/{len(qc)} (asset_info 冇 TCG 欄位，冇得確認)")
    print(f"  public_allowed    : 0/{len(qc)} (入私庫唔等於出街)")

    if write_result is None:
        print("\n(dry-run — 加 --write 先會真係寫入)")
        return
    print("\n-- 寫入 --")
    print(f"  run_id  : {write_result['run_id']}")
    print(f"  run_key : {write_result['run_key']}")
    for table in TABLES:
        before = write_result["before"][table]
        after = write_result["after"][table]
        print(f"  {table}: {before} → {after} (+{after - before})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--g10-root", type=Path, default=DEFAULT_G10_ROOT)
    parser.add_argument("--landing-root", type=Path, default=DEFAULT_LANDING)
    parser.add_argument("--write", action="store_true", help="真係寫入（預設 dry-run）")
    add_connection_args(parser)
    args = parser.parse_args(argv)

    cards_root = args.g10_root / "cards"
    images_root = args.g10_root / "images"
    if not cards_root.is_dir() or not images_root.is_dir():
        print(f"G10 目錄唔見: {cards_root} / {images_root}", file=sys.stderr)
        return 2

    connection = connection_from_args(args)
    try:
        try:
            plan = build(connection, cards_root, images_root, args.landing_root)
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if not plan["asset_rows"] and not plan["pointer_rows"]:
            print_report(plan, None)
            print("冇任何可入庫記錄。", file=sys.stderr)
            return 1
        write_result = write(connection, plan) if args.write else None
        print_report(plan, write_result)
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
