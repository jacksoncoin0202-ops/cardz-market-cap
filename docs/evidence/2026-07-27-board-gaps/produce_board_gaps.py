#!/usr/bin/env python3
"""Inventory missing card images and missing market stories across the three boards.

Read-only.  Touches the database with SELECT only, never writes to
`market-assets`, never rewrites `boards.json`, and emits everything it learns
into this directory beside itself.

Run from the repository root:

    python -X utf8 docs/evidence/2026-07-27-board-gaps/produce_board_gaps.py

Why the joins look the way they do
----------------------------------
`boards.json` rows carry `variantId`, which is `catalog_variant.id` - the
surrogate key, not `opaque_id`.  That matters because `opaque_id` is a hash of
(tcg, language, set_name, normalized collector number, name), so any editorial
change to a set name silently re-keys the card.  `manifests/image-qc.json` keys
its records on `publicId`, which *is* the drifting `opaque_id`, so a record can
be perfectly good and still fail to find its variant.  Every image lookup here
is therefore attempted twice: once on `publicId`, and once - drift-proof - on
`resolverEvidence.sourceContentSha256` matched against
`market_image_asset.content_sha256`, which is content-addressed and cannot
drift.

A row is only counted as having an image if a file is physically on disk.  The
database and the manifest both describe intent; `data/public/market-assets/`
and `apps/web/public/market-assets/` are what actually exist, and they disagree
with each other (the producer directory holds more files than the web one), so
both are probed separately.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

BOARDS = ROOT / "apps" / "web" / "src" / "data" / "boards.json"
# `boards.json` was deleted by another agent partway through this audit, along
# with its whole parent directory, while the three boards were being folded in
# behind the watchlist control.  The universe it defined is frozen beside this
# script so the audit stays re-runnable; the live file wins whenever it exists.
FROZEN_BOARDS = OUT / "board-universe.json"
IMAGE_QC = ROOT / "manifests" / "image-qc.json"
PRESENTATION_PACK = ROOT / "data" / "public" / "presentation-pack.json"
PRODUCER_ASSETS = ROOT / "data" / "public" / "market-assets"
WEB_ASSETS = ROOT / "apps" / "web" / "public" / "market-assets"
CARD_NAMES = ROOT / "data" / "editorial" / "card-names.json"
SET_NAMES = ROOT / "data" / "editorial" / "set-names.json"

# The canvas contract lives in pipelines/native_image_resolver.py; these are
# copies of CANVAS_W / CANVAS_H / NORMALIZED_MARKER, asserted against the
# manifest rather than imported so this script stays runnable on its own.
STD_W, STD_H = 429, 600
STD_MARKER = f"std-{STD_W}x{STD_H}"

# Locale codes as they actually appear in catalog_variant_locale.  The brief
# asked for en/zh-TW/zh-CN/ja/ko; the table uses en/ja/zhCN/zhTW and has no
# `ko` row anywhere, which is recorded as a structural gap, not a per-card one.
LOCALES = ["en", "ja", "zhCN", "zhTW"]
TRANSLATION_LOCALES = ["ja", "zhCN", "zhTW"]

TIERS = {
    "A_current_std": "reachable from the card's CURRENT opaque_id, std 429x600, file on disk",
    "B_drift_std": "std 429x600 file on disk, but only reachable by content sha - publicId has drifted",
    "C_drift_nonstd": "file on disk but not std canvas (raw-front-v3 era), publicId has drifted",
    "D_no_file": "market_image_asset row exists, but no public webp on disk - this is a missing image",
    "E_no_asset_row": "neither a market_image_asset row nor a file on disk - never harvested",
}
MISSING_TIERS = ("D_no_file", "E_no_asset_row")


def fetch(sql: str, params: tuple | None = None) -> list[dict]:
    import pymysql
    from pymysql.cursors import DictCursor

    from verify_claims import check_read_only, db_config, scrub

    problem = check_read_only(sql)
    if problem:
        raise SystemExit(f"refused, not read-only: {problem}")

    config = db_config()
    password = config.get("password", "")
    try:
        conn = pymysql.connect(charset="utf8mb4", cursorclass=DictCursor, **config)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"cannot connect: {scrub(str(exc), password)}") from exc
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params or ())
        return list(cursor.fetchall())
    finally:
        conn.close()


def probe(path: Path) -> dict:
    """Real dimensions, mode and corner alpha of a file on disk."""
    try:
        from PIL import Image
    except ImportError:
        return {"error": "Pillow not installed"}
    try:
        with Image.open(path) as im:
            width, height = im.size
            mode = im.mode
            corner_alpha = None
            if "A" in im.getbands():
                alpha = im.convert("RGBA").split()[-1]
                corner_alpha = max(
                    alpha.getpixel((2, 2)),
                    alpha.getpixel((width - 3, 2)),
                    alpha.getpixel((2, height - 3)),
                    alpha.getpixel((width - 3, height - 3)),
                )
            return {
                "width": width,
                "height": height,
                "mode": mode,
                "cornerAlphaMax": corner_alpha,
                "stdCanvas": width == STD_W and height == STD_H,
            }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def disk_state(sha: str | None) -> dict:
    if not sha:
        return {"sha256": None}
    state = {"sha256": sha}
    for label, directory in (("producer", PRODUCER_ASSETS), ("web", WEB_ASSETS)):
        base = directory / f"{sha}.webp"
        state[label] = {
            "base": base.is_file(),
            "d200": (directory / f"{sha}_200.webp").is_file(),
            "d600": (directory / f"{sha}_600.webp").is_file(),
        }
        if base.is_file() and "probe" not in state:
            state["probe"] = probe(base)
    return state


def main() -> int:
    measured_at = datetime.now(timezone.utc).isoformat()

    universe_source = BOARDS if BOARDS.is_file() else FROZEN_BOARDS
    if not universe_source.is_file():
        raise SystemExit(
            f"no board universe: neither {BOARDS} nor the frozen {FROZEN_BOARDS} exists"
        )
    boards = json.loads(universe_source.read_text(encoding="utf-8"))
    membership: dict[int, list[str]] = defaultdict(list)
    board_sizes = {}
    board_rows: dict[int, dict] = {}
    for board_key, board in boards["boards"].items():
        rows = board["rows"]
        board_sizes[board_key] = len(rows)
        for row in rows:
            vid = int(row["variantId"])
            membership[vid].append(board_key)
            board_rows.setdefault(vid, row)

    universe = sorted(membership)
    placeholders = ",".join(["%s"] * len(universe))

    variants = {
        int(r["id"]): r
        for r in fetch(
            "SELECT id, opaque_id, tcg_code, card_language, canonical_name, "
            f"set_name, collector_number, identity_status FROM catalog_variant WHERE id IN ({placeholders})",
            tuple(universe),
        )
    }

    assets: dict[int, list[dict]] = defaultdict(list)
    for row in fetch(
        "SELECT variant_id, image_kind, content_sha256, mime_type, width_px, height_px, "
        f"captured_at FROM market_image_asset WHERE variant_id IN ({placeholders})",
        tuple(universe),
    ):
        assets[int(row["variant_id"])].append(row)

    stories: dict[int, dict[str, dict]] = defaultdict(dict)
    for row in fetch(
        "SELECT variant_id, locale_code, localized_name, localized_set_name, "
        f"market_story FROM catalog_variant_locale WHERE variant_id IN ({placeholders})",
        tuple(universe),
    ):
        stories[int(row["variant_id"])][row["locale_code"]] = row

    all_locale_codes = sorted({r["locale_code"] for r in fetch("SELECT DISTINCT locale_code FROM catalog_variant_locale")})

    qc = json.loads(IMAGE_QC.read_text(encoding="utf-8"))
    qc_records = qc["records"] if isinstance(qc, dict) and "records" in qc else qc
    by_public_id: dict[str, list[dict]] = defaultdict(list)
    by_source_sha: dict[str, list[dict]] = defaultdict(list)
    for rec in qc_records:
        if rec.get("publicId"):
            by_public_id[rec["publicId"]].append(rec)
        src = (rec.get("resolverEvidence") or {}).get("sourceContentSha256")
        if src:
            by_source_sha[src].append(rec)

    pack = json.loads(PRESENTATION_PACK.read_text(encoding="utf-8"))
    pack_by_id = {}
    for section in ("top100", "watchlist"):
        for entry in pack.get(section, []):
            pack_by_id.setdefault(entry["id"], entry)

    card_names = json.loads(CARD_NAMES.read_text(encoding="utf-8")).get("entries", {})
    set_names = json.loads(SET_NAMES.read_text(encoding="utf-8")).get("entries", {})

    records = []
    for vid in universe:
        variant = variants.get(vid)
        row = board_rows[vid]
        opaque = variant["opaque_id"] if variant else None
        variant_assets = assets.get(vid, [])
        raw_shas = [a["content_sha256"] for a in variant_assets]

        publicid_hits = [
            rec for rec in by_public_id.get(opaque or "", [])
            if rec.get("imageKind") == "raw_front"
        ]
        sourcesha_hits = [
            rec
            for sha in raw_shas
            for rec in by_source_sha.get(sha, [])
            if rec.get("imageKind") == "raw_front"
        ]

        candidates = []
        seen = set()
        for origin, hits in (("publicId", publicid_hits), ("sourceSha", sourcesha_hits)):
            for rec in hits:
                sha = rec.get("contentSha256")
                key = (origin, sha)
                if not sha or key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "matchedVia": origin,
                        "qcPublicId": rec.get("publicId"),
                        "qcVersion": rec.get("qcVersion"),
                        "publicAllowed": bool(rec.get("publicAllowed")),
                        "stdCanvasMarker": rec.get("stdCanvas") == STD_MARKER,
                        "nativeRgba": bool(rec.get("nativeRgba")),
                        "manifestWidth": rec.get("width"),
                        "manifestHeight": rec.get("height"),
                        "resolverMethod": (rec.get("resolverEvidence") or {}).get("method"),
                        "sourceRef": (rec.get("resolverEvidence") or {}).get("sourceRef"),
                        "disk": disk_state(sha),
                    }
                )
        for sha in raw_shas:
            candidates.append({"matchedVia": "dbAssetRaw", "disk": disk_state(sha)})

        def servable(cand: dict, *, via: str, std: bool) -> bool:
            if cand.get("matchedVia") != via:
                return False
            if not cand.get("publicAllowed"):
                return False
            if std and not cand.get("stdCanvasMarker"):
                return False
            return bool(cand["disk"].get("producer", {}).get("base"))

        on_disk_producer = any(c["disk"].get("producer", {}).get("base") for c in candidates)
        on_disk_web = any(c["disk"].get("web", {}).get("base") for c in candidates)
        reachable_current = any(
            c.get("matchedVia") == "publicId" and c["disk"].get("producer", {}).get("base")
            for c in candidates
        )

        tier = "E_no_asset_row" if not variant_assets else "D_no_file"
        if any(servable(c, via="publicId", std=True) for c in candidates):
            tier = "A_current_std"
        elif any(servable(c, via="sourceSha", std=True) for c in candidates):
            tier = "B_drift_std"
        elif any(
            c["disk"].get("producer", {}).get("base")
            and c["disk"].get("probe", {}).get("stdCanvas") is False
            for c in candidates
        ):
            tier = "C_drift_nonstd"

        locale_rows = stories.get(vid, {})
        story_present = {
            loc: bool((locale_rows.get(loc) or {}).get("market_story") or "".strip())
            and bool(((locale_rows.get(loc) or {}).get("market_story") or "").strip())
            for loc in LOCALES
        }
        english_missing = not story_present["en"]
        translations_missing = [loc for loc in TRANSLATION_LOCALES if not story_present[loc]]

        english_name = (variant or {}).get("canonical_name") or row.get("name")
        english_set = (variant or {}).get("set_name") or row.get("set")
        name_entry = card_names.get(english_name) or {}
        set_entry = set_names.get(english_set) or {}

        pack_entry = pack_by_id.get(opaque)
        pack_image = (pack_entry or {}).get("image") or {}
        pack_stories = (pack_entry or {}).get("stories") or {}

        records.append(
            {
                "variantId": vid,
                "boards": membership[vid],
                "rank": row.get("rank"),
                "tcg": row.get("tcg"),
                "name": english_name,
                "set": english_set,
                "collectorNumber": row.get("collectorNumber"),
                "opaqueId": opaque,
                "identityStatus": (variant or {}).get("identity_status"),
                "inCatalogVariant": variant is not None,
                "image": {
                    "tier": tier,
                    "tierMeaning": TIERS[tier],
                    "missingImage": tier in MISSING_TIERS,
                    "reachableViaCurrentOpaqueId": reachable_current,
                    "dbAssetRows": len(variant_assets),
                    "dbRawDims": [f'{a["width_px"]}x{a["height_px"]}' for a in variant_assets],
                    "onDiskProducer": on_disk_producer,
                    "onDiskWeb": on_disk_web,
                    "candidates": candidates,
                },
                "story": {
                    "present": story_present,
                    "englishMissing": english_missing,
                    "translationsMissing": translations_missing,
                    "hasAnyLocaleRow": bool(locale_rows),
                },
                "editorialNames": {
                    "cardNameEntry": bool(name_entry),
                    "cardNameMissing": [loc for loc in ("zhTW", "zhCN", "ja") if not name_entry.get(loc)],
                    "setNameEntry": bool(set_entry),
                    "setNameMissing": [loc for loc in ("zhTW", "zhCN", "ja") if not set_entry.get(loc)],
                },
                "presentationPack": {
                    "present": pack_entry is not None,
                    "imageSha256": pack_image.get("sha256"),
                    "imageDims": (
                        f'{pack_image.get("width")}x{pack_image.get("height")}'
                        if pack_image.get("width")
                        else None
                    ),
                    "imageStdCanvas": pack_image.get("width") == STD_W and pack_image.get("height") == STD_H,
                    "imageFileOnWeb": bool(
                        pack_image.get("sha256")
                        and (WEB_ASSETS / f'{pack_image["sha256"]}.webp').is_file()
                    ),
                    "storiesPresent": {
                        loc: bool((pack_stories.get(loc) or "").strip()) for loc in LOCALES
                    },
                },
            }
        )

    missing_image = [r for r in records if r["image"]["missingImage"]]
    non_std = [r for r in records if r["image"]["tier"] == "C_drift_nonstd"]
    drift_only = [
        r for r in records if r["image"]["onDiskProducer"] and not r["image"]["reachableViaCurrentOpaqueId"]
    ]
    missing_english = [r for r in records if r["story"]["englishMissing"]]
    missing_translation = [
        r for r in records if not r["story"]["englishMissing"] and r["story"]["translationsMissing"]
    ]

    summary = {
        "measuredAt": measured_at,
        "universeSource": str(universe_source.relative_to(ROOT)).replace("\\", "/"),
        "universeSourceIsFrozenCopy": universe_source == FROZEN_BOARDS,
        "boardSizes": board_sizes,
        "universeSize": len(universe),
        "universeDefinition": "union of variantId across op100, ptcg100 and tcg300; see universeSource for which file supplied them",
        "variantsFoundInCatalog": sum(1 for r in records if r["inCatalogVariant"]),
        "localeCodesInDatabase": all_locale_codes,
        "koLocaleRowsInDatabase": "ko" in all_locale_codes,
        "assetDirCounts": {
            "producer": len(list(PRODUCER_ASSETS.iterdir())) if PRODUCER_ASSETS.is_dir() else None,
            "web": len(list(WEB_ASSETS.iterdir())) if WEB_ASSETS.is_dir() else None,
        },
        "imageTierCounts": {
            tier: sum(1 for r in records if r["image"]["tier"] == tier) for tier in TIERS
        },
        "tierMeanings": TIERS,
        "missingImageCount": len(missing_image),
        "missingImageDefinition": "no public webp on disk in data/public/market-assets (tiers D and E)",
        "nonStdCanvasCount": len(non_std),
        "imageOnDiskButNotReachableViaCurrentOpaqueIdCount": len(drift_only),
        "imageReachableViaCurrentOpaqueIdCount": sum(
            1 for r in records if r["image"]["reachableViaCurrentOpaqueId"]
        ),
        "onDiskProducerButNotWeb": sum(
            1 for r in records if r["image"]["onDiskProducer"] and not r["image"]["onDiskWeb"]
        ),
        # An image can be published and site-servable while the database has no
        # market_image_asset row for it.  That is a DB under-recording gap, not a
        # missing image, so it is counted separately from the tiers.
        "noDbAssetRowCount": sum(1 for r in records if r["image"]["dbAssetRows"] == 0),
        "noDbAssetRowVariantIds": [r["variantId"] for r in records if r["image"]["dbAssetRows"] == 0],
        "missingEnglishStoryCount": len(missing_english),
        "missingTranslationOnlyCount": len(missing_translation),
        "storyLocaleCoverage": {
            loc: sum(1 for r in records if r["story"]["present"][loc]) for loc in LOCALES
        },
        "presentationPackPresent": sum(1 for r in records if r["presentationPack"]["present"]),
        "presentationPackStdCanvas": sum(
            1 for r in records if r["presentationPack"]["imageStdCanvas"]
        ),
        "presentationPackEnglishStory": sum(
            1 for r in records if r["presentationPack"]["storiesPresent"]["en"]
        ),
        "editorialCardNameEntries": sum(1 for r in records if r["editorialNames"]["cardNameEntry"]),
        "editorialSetNameEntries": sum(1 for r in records if r["editorialNames"]["setNameEntry"]),
    }

    (OUT / "board-gaps.json").write_text(
        json.dumps({"summary": summary, "records": records}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    with (OUT / "missing-images.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "variantId", "boards", "tcg", "name", "set", "collectorNumber",
                "opaqueId", "tier", "tierMeaning", "dbAssetRows", "dbRawDims",
                "onDiskProducer", "onDiskWeb", "packImageDims", "resolverMethod",
            ]
        )
        for r in missing_image:
            methods = sorted({c.get("resolverMethod") for c in r["image"]["candidates"] if c.get("resolverMethod")})
            writer.writerow(
                [
                    r["variantId"], "|".join(r["boards"]), r["tcg"], r["name"], r["set"],
                    r["collectorNumber"], r["opaqueId"], r["image"]["tier"],
                    r["image"]["tierMeaning"], r["image"]["dbAssetRows"],
                    "|".join(r["image"]["dbRawDims"]), r["image"]["onDiskProducer"],
                    r["image"]["onDiskWeb"], r["presentationPack"]["imageDims"],
                    "|".join(methods),
                ]
            )

    with (OUT / "image-defects.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "variantId", "boards", "rank", "tcg", "name", "set", "collectorNumber",
                "tier", "missingImage", "nonStdCanvas", "reachableViaCurrentOpaqueId",
                "onDiskDims", "packImageDims", "qcVersion",
            ]
        )
        for r in records:
            defects = (
                r["image"]["missingImage"]
                or r["image"]["tier"] == "C_drift_nonstd"
                or not r["image"]["reachableViaCurrentOpaqueId"]
            )
            if not defects:
                continue
            probes = [
                f'{c["disk"]["probe"]["width"]}x{c["disk"]["probe"]["height"]}'
                for c in r["image"]["candidates"]
                if c["disk"].get("probe", {}).get("width")
            ]
            versions = sorted({c.get("qcVersion") for c in r["image"]["candidates"] if c.get("qcVersion")})
            writer.writerow(
                [
                    r["variantId"], "|".join(r["boards"]), r["rank"], r["tcg"], r["name"],
                    r["set"], r["collectorNumber"], r["image"]["tier"],
                    r["image"]["missingImage"], r["image"]["tier"] == "C_drift_nonstd",
                    r["image"]["reachableViaCurrentOpaqueId"], "|".join(probes),
                    r["presentationPack"]["imageDims"], "|".join(versions),
                ]
            )

    with (OUT / "missing-stories.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "variantId", "boards", "tcg", "name", "set", "collectorNumber",
                "gapClass", "hasEnglish", "missingStoryLocales", "hasAnyLocaleRow",
                "cardNameTranslationsMissing", "setNameTranslationsMissing",
            ]
        )
        for r in records:
            missing_locales = [loc for loc in LOCALES if not r["story"]["present"][loc]]
            if not missing_locales:
                continue
            gap_class = "no_english_story" if r["story"]["englishMissing"] else "translation_only"
            writer.writerow(
                [
                    r["variantId"], "|".join(r["boards"]), r["tcg"], r["name"], r["set"],
                    r["collectorNumber"], gap_class, not r["story"]["englishMissing"],
                    "|".join(missing_locales), r["story"]["hasAnyLocaleRow"],
                    "|".join(r["editorialNames"]["cardNameMissing"]),
                    "|".join(r["editorialNames"]["setNameMissing"]),
                ]
            )

    # `--counter <key>` prints one number and nothing else, so a @verified stamp
    # can re-measure a single headline figure without parsing JSON in a pipe.
    if len(sys.argv) == 3 and sys.argv[1] == "--counter":
        key = sys.argv[2]
        if key not in summary:
            print(f"no such counter: {key}", file=sys.stderr)
            return 1
        print(summary[key])
        return 0

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
