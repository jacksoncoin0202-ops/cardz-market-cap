"""One-off geometric probe for the 265-card board universe.

Read-only.  Resolves each board card to the image actually on disk, then
measures the three things the operator asked about: white corners, wrong card,
and misalignment.  "Wrong card" cannot be measured here - it is emitted as
not-checked and handled by vision review.

Run:
    set -a && . data/runtime/config/backend.env && set +a
    python -X utf8 temp/image_qc_probe.py --out temp/image-qc-probe.json
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
FROZEN_BOARDS = ROOT / "docs" / "evidence" / "2026-07-27-board-gaps" / "board-universe.json"
IMAGE_QC = ROOT / "manifests" / "image-qc.json"
WEB_ASSETS = ROOT / "apps" / "web" / "public" / "market-assets"
PRODUCER_ASSETS = ROOT / "data" / "public" / "market-assets"

STD_W, STD_H = 429, 600
STD_MARKER = "std-429x600"

# alpha at or below this is treated as background
ALPHA_BG = 64
# alpha at or above this is treated as solid image content
ALPHA_SOLID = 200
# per-channel floor for "near white"
WHITE_FLOOR = 235


def fetch(sql: str, params: tuple | None = None) -> list[dict]:
    import pymysql

    conn = pymysql.connect(
        host=os.environ.get("CARDZ_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("CARDZ_DB_PORT", "3306")),
        user=os.environ["CARDZ_DB_USER"],
        password=os.environ["CARDZ_DB_PASSWORD"],
        database=os.environ.get("CARDZ_DB_NAME", "cardz_market_cap"),
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        return list(cur.fetchall())
    finally:
        conn.close()


def measure(path: Path) -> dict:
    """Every geometric fact we can get out of one file."""
    out: dict = {"path": str(path.relative_to(ROOT)).replace("\\", "/")}
    try:
        with Image.open(path) as im:
            out["realFormat"] = im.format
            out["declaredExt"] = path.suffix.lower().lstrip(".")
            out["extLies"] = (im.format or "").lower() != {"webp": "webp", "jpg": "jpeg", "jpeg": "jpeg", "png": "png"}.get(out["declaredExt"], out["declaredExt"])
            out["mode"] = im.mode
            w, h = im.size
            out["width"], out["height"] = w, h
            out["stdCanvas"] = (w == STD_W and h == STD_H)
            out["aspect"] = round(w / h, 4) if h else None
            out["landscape"] = w > h
            arr = np.asarray(im.convert("RGBA"))
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out

    alpha = arr[..., 3]
    rgb = arr[..., :3].astype(np.int16)
    h, w = alpha.shape
    out["hasTransparency"] = bool(alpha.min() < ALPHA_SOLID)

    # ---- criterion 1: white corners -------------------------------------
    inset, patch = 2, 4
    spans = {
        "tl": (slice(inset, inset + patch), slice(inset, inset + patch)),
        "tr": (slice(inset, inset + patch), slice(w - inset - patch, w - inset)),
        "bl": (slice(h - inset - patch, h - inset), slice(inset, inset + patch)),
        "br": (slice(h - inset - patch, h - inset), slice(w - inset - patch, w - inset)),
    }
    corners = {}
    for name, (ys, xs) in spans.items():
        a = alpha[ys, xs]
        c = rgb[ys, xs]
        corners[name] = {
            "alphaMax": int(a.max()),
            "rgbMin": int(c.min()),
            "rgbMean": round(float(c.mean()), 1),
        }
    out["corners"] = corners
    out["opaqueCorners"] = sorted(n for n, c in corners.items() if c["alphaMax"] >= ALPHA_SOLID)
    out["whiteCorners"] = sorted(
        n for n, c in corners.items()
        if c["alphaMax"] >= ALPHA_SOLID and c["rgbMin"] >= WHITE_FLOOR
    )
    out["cornerAlphaMax"] = max(c["alphaMax"] for c in corners.values())

    # ---- content mask ---------------------------------------------------
    if alpha.min() < ALPHA_SOLID:
        mask = alpha > ALPHA_BG
        out["maskSource"] = "alpha"
    else:
        mask = rgb.min(axis=2) < 240
        out["maskSource"] = "nonWhite"

    if not mask.any():
        out["error"] = "empty content mask"
        return out

    cols, rows = mask.any(axis=0), mask.any(axis=1)
    x0 = int(np.argmax(cols)); x1 = int(w - 1 - np.argmax(cols[::-1]))
    y0 = int(np.argmax(rows)); y1 = int(h - 1 - np.argmax(rows[::-1]))
    out["bbox"] = [x0, y0, x1, y1]
    out["contentFrac"] = round(float(mask.mean()), 4)

    # ---- criterion 3a: canvas margin symmetry ---------------------------
    mL, mR, mT, mB = x0, w - 1 - x1, y0, h - 1 - y1
    out["margins"] = {"left": mL, "right": mR, "top": mT, "bottom": mB}
    out["marginDiffX"] = abs(mL - mR)
    out["marginDiffY"] = abs(mT - mB)

    # ---- criterion 3b: tilt (edge-profile slope) ------------------------
    bw, bh = x1 - x0 + 1, y1 - y0 + 1

    def profile_slope(sub: np.ndarray, axis: int) -> dict:
        """First-content index per line, over the middle 80% of the edge."""
        first = np.argmax(sub, axis=axis).astype(float)
        valid = sub.any(axis=axis)
        if valid.sum() < 8:
            return {"slope": None, "spread": None}
        first[~valid] = np.nan
        n = first.size
        seg = max(1, n // 5)
        left = float(np.nanmedian(first[:seg]))
        right = float(np.nanmedian(first[-seg:]))
        return {
            "slope": round(right - left, 2),
            "spread": round(float(np.nanmax(first) - np.nanmin(first)), 2),
        }

    lo_x, hi_x = x0 + int(bw * 0.10), x0 + int(bw * 0.90)
    lo_y, hi_y = y0 + int(bh * 0.10), y0 + int(bh * 0.90)
    top = profile_slope(mask[y0:y1 + 1, lo_x:hi_x + 1], 0)
    left = profile_slope(mask[lo_y:hi_y + 1, x0:x1 + 1], 1)
    out["tiltTop"] = top
    out["tiltLeft"] = left
    slopes = [abs(v) for v in (top["slope"], left["slope"]) if v is not None]
    out["tiltMaxAbsSlope"] = round(max(slopes), 2) if slopes else None
    # normalise by edge length so a big card and a small card compare
    out["tiltTopNorm"] = round(abs(top["slope"]) / bw, 4) if top["slope"] is not None and bw else None
    out["tiltLeftNorm"] = round(abs(left["slope"]) / bh, 4) if left["slope"] is not None and bh else None

    # ---- criterion 1 (robust): the rounded-corner triangle --------------
    # A real card has a ~5.5%-of-width corner radius.  If the image was cropped
    # to the card's bounding box without cutting the arc out, the region OUTSIDE
    # the arc is leftover photo background -- almost always white.  Sampling a
    # 4x4 patch lands on antialiasing; sampling the whole quarter-disc does not.
    r = max(6, int(round(w * 0.055)))
    yy, xx = np.mgrid[0:r, 0:r]
    outside = ((r - 1 - xx) ** 2 + (r - 1 - yy) ** 2) > (r - 1) ** 2   # beyond the arc
    tri = {
        "tl": (slice(0, r), slice(0, r), outside),
        "tr": (slice(0, r), slice(w - r, w), outside[:, ::-1]),
        "bl": (slice(h - r, h), slice(0, r), outside[::-1, :]),
        "br": (slice(h - r, h), slice(w - r, w), outside[::-1, ::-1]),
    }
    out["cornerRadiusPx"] = r
    cw_frac, cw_op = {}, {}
    for nm, (sy, sx, msk) in tri.items():
        a_t, rgb_t = alpha[sy, sx][msk], rgb[sy, sx][msk]
        if a_t.size == 0:
            continue
        op = a_t >= ALPHA_SOLID
        cw_op[nm] = round(float(op.mean()), 4)
        cw_frac[nm] = round(float((op & (rgb_t.min(axis=1) >= WHITE_FLOOR)).mean()), 4)
    out["cornerWhiteFrac"] = cw_frac
    out["cornerOpaqueFrac"] = cw_op
    # a corner counts as white only if most of the triangle is opaque AND white
    out["whiteCornersTri"] = sorted(n for n, v in cw_frac.items() if v >= 0.60)
    out["cornerWhiteMax"] = round(max(cw_frac.values()), 4) if cw_frac else None
    # widened: light-grey leftover background reads as a square corner too
    cl_frac = {}
    for nm, (sy, sx, msk) in tri.items():
        a_t, rgb_t = alpha[sy, sx][msk], rgb[sy, sx][msk]
        if a_t.size:
            cl_frac[nm] = round(float(((a_t >= ALPHA_SOLID) & (rgb_t.min(axis=1) >= 200)).mean()), 4)
    out["cornerLightFrac"] = cl_frac
    out["lightCornersTri"] = sorted(n for n, v in cl_frac.items() if v >= 0.60)
    # structural: does the file carry a native transparent rounded corner at all
    out["nativeRounded"] = bool(cw_op) and all(v <= 0.40 for v in cw_op.values())

    # ---- stage 2: the card artwork INSIDE the opaque region -------------
    # The canvas normaliser produces an axis-aligned rounded rectangle, so the
    # alpha mask is square even when the card inside it is crooked.  Tilt and
    # white banding therefore have to be measured against the ink, not alpha.
    opaque = alpha >= ALPHA_SOLID
    if opaque.any():
        ocols, orows = opaque.any(axis=0), opaque.any(axis=1)
        ox0 = int(np.argmax(ocols)); ox1 = int(w - 1 - np.argmax(ocols[::-1]))
        oy0 = int(np.argmax(orows)); oy1 = int(h - 1 - np.argmax(orows[::-1]))
        ink = opaque & (rgb.min(axis=2) < WHITE_FLOOR)
        out["inkFrac"] = round(float(ink.sum() / max(1, opaque.sum())), 4)
        if ink.any():
            icols, irows = ink.any(axis=0), ink.any(axis=1)
            ix0 = int(np.argmax(icols)); ix1 = int(w - 1 - np.argmax(icols[::-1]))
            iy0 = int(np.argmax(irows)); iy1 = int(h - 1 - np.argmax(irows[::-1]))
            out["opaqueBbox"] = [ox0, oy0, ox1, oy1]
            out["inkBbox"] = [ix0, iy0, ix1, iy1]
            iL, iR = ix0 - ox0, ox1 - ix1
            iT, iB = iy0 - oy0, oy1 - iy1
            out["innerMargins"] = {"left": iL, "right": iR, "top": iT, "bottom": iB}
            out["innerMarginDiffX"] = abs(iL - iR)
            out["innerMarginDiffY"] = abs(iT - iB)
            out["whiteFrameMaxPx"] = max(iL, iR, iT, iB)

            def ink_edge(sub: np.ndarray, axis: int) -> dict:
                first = np.argmax(sub, axis=axis).astype(float)
                valid = sub.any(axis=axis)
                if valid.sum() < 8:
                    return {"slope": None, "spread": None}
                first[~valid] = np.nan
                n = first.size
                seg = max(1, n // 5)
                lft = float(np.nanmedian(first[:seg]))
                rgt = float(np.nanmedian(first[-seg:]))
                return {
                    "slope": round(rgt - lft, 2),
                    "spread": round(float(np.nanmax(first) - np.nanmin(first)), 2),
                }

            iw, ih = ix1 - ix0 + 1, iy1 - iy0 + 1
            lx0, lx1 = ix0 + int(iw * 0.10), ix0 + int(iw * 0.90)
            ly0, ly1 = iy0 + int(ih * 0.10), iy0 + int(ih * 0.90)
            it = ink_edge(ink[iy0:iy1 + 1, lx0:lx1 + 1], 0)
            il = ink_edge(ink[ly0:ly1 + 1, ix0:ix1 + 1], 1)
            out["inkTiltTop"], out["inkTiltLeft"] = it, il
            cands_slope = [abs(v) for v in (it["slope"], il["slope"]) if v is not None]
            cands_spread = [v for v in (it["spread"], il["spread"]) if v is not None]
            out["inkTiltMaxSlope"] = round(max(cands_slope), 2) if cands_slope else None
            out["inkTiltMaxSpread"] = round(max(cands_spread), 2) if cands_spread else None

    # ---- criterion 3c: white fringe on the content border ---------------
    band = 3
    chunks = []
    for ys, xs in (
        (slice(y0, min(y0 + band, y1 + 1)), slice(x0, x1 + 1)),
        (slice(max(y1 - band + 1, y0), y1 + 1), slice(x0, x1 + 1)),
        (slice(y0, y1 + 1), slice(x0, min(x0 + band, x1 + 1))),
        (slice(y0, y1 + 1), slice(max(x1 - band + 1, x0), x1 + 1)),
    ):
        m = mask[ys, xs]
        if m.any():
            chunks.append(rgb[ys, xs][m])
    if chunks:
        ring = np.concatenate(chunks)
        out["borderWhiteFrac"] = round(float((ring.min(axis=1) >= WHITE_FLOOR).mean()), 4)
        out["borderPx"] = int(ring.shape[0])
    else:
        out["borderWhiteFrac"] = None
        out["borderPx"] = 0

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="temp/image-qc-probe.json")
    args = ap.parse_args()

    measured_at = datetime.now(timezone.utc).isoformat()

    boards = json.loads(FROZEN_BOARDS.read_text(encoding="utf-8"))
    membership: dict[int, list[str]] = defaultdict(list)
    board_rows: dict[int, dict] = {}
    ranks: dict[int, dict[str, int]] = defaultdict(dict)
    for key, board in boards["boards"].items():
        for row in board["rows"]:
            vid = int(row["variantId"])
            membership[vid].append(key)
            ranks[vid][key] = int(row["rank"])
            board_rows.setdefault(vid, row)

    universe = sorted(membership)
    ph = ",".join(["%s"] * len(universe))

    variants = {
        int(r["id"]): r
        for r in fetch(
            "SELECT id, opaque_id, tcg_code, card_language, canonical_name, set_name, "
            f"collector_number FROM catalog_variant WHERE id IN ({ph})",
            tuple(universe),
        )
    }

    assets: dict[int, list[dict]] = defaultdict(list)
    for r in fetch(
        "SELECT variant_id, image_kind, content_sha256, width_px, height_px, captured_at "
        f"FROM market_image_asset WHERE variant_id IN ({ph})",
        tuple(universe),
    ):
        assets[int(r["variant_id"])].append(r)

    qc = json.loads(IMAGE_QC.read_text(encoding="utf-8"))
    recs = qc["records"] if isinstance(qc, dict) and "records" in qc else qc
    by_public_id: dict[str, list[dict]] = defaultdict(list)
    by_source_sha: dict[str, list[dict]] = defaultdict(list)
    for rec in recs:
        if rec.get("imageKind") != "raw_front":
            continue
        if rec.get("publicId"):
            by_public_id[rec["publicId"]].append(rec)
        src = (rec.get("resolverEvidence") or {}).get("sourceContentSha256")
        if src:
            by_source_sha[src].append(rec)

    def locate(sha: str) -> dict:
        web = WEB_ASSETS / f"{sha}.webp"
        prod = PRODUCER_ASSETS / f"{sha}.webp"
        return {
            "sha256": sha,
            "web": web.is_file(),
            "producer": prod.is_file(),
            "servePath": web if web.is_file() else (prod if prod.is_file() else None),
        }

    out_records = []
    for vid in universe:
        variant = variants.get(vid)
        row = board_rows[vid]
        opaque = variant["opaque_id"] if variant else None
        asset_rows = [a for a in assets.get(vid, []) if a["image_kind"] == "raw_front"]
        asset_shas = [a["content_sha256"] for a in asset_rows]

        cands = []
        seen = set()
        for via, hits in (
            ("publicId", by_public_id.get(opaque or "", [])),
            ("sourceSha", [r for s in asset_shas for r in by_source_sha.get(s, [])]),
        ):
            for rec in hits:
                sha = rec.get("contentSha256")
                if not sha or (via, sha) in seen:
                    continue
                seen.add((via, sha))
                loc = locate(sha)
                cands.append({
                    "matchedVia": via,
                    "qcVersion": rec.get("qcVersion"),
                    "publicAllowed": bool(rec.get("publicAllowed")),
                    "stdMarker": rec.get("stdCanvas") == STD_MARKER,
                    "nativeRgba": bool(rec.get("nativeRgba")),
                    "qcPublicId": rec.get("publicId"),
                    "resolverMethod": (rec.get("resolverEvidence") or {}).get("method"),
                    **loc,
                })
        # raw DB shas as a last resort (no manifest verdict attached)
        for sha in asset_shas:
            if any(c["sha256"] == sha for c in cands):
                continue
            loc = locate(sha)
            cands.append({
                "matchedVia": "dbAssetRaw", "qcVersion": None, "publicAllowed": False,
                "stdMarker": False, "nativeRgba": False, "qcPublicId": None,
                "resolverMethod": None, **loc,
            })

        # pick the image a visitor would actually get:
        # on disk > std marker > reachable via current opaque_id > publicAllowed
        def rank_key(c: dict) -> tuple:
            return (
                c["servePath"] is not None,
                c["web"],
                c["stdMarker"],
                c["matchedVia"] == "publicId",
                c["publicAllowed"],
            )

        chosen = max(cands, key=rank_key) if cands else None
        rec_out = {
            "variantId": vid,
            "boards": sorted(membership[vid]),
            "ranks": ranks[vid],
            "tcg": row.get("tcg"),
            "name": (variant or {}).get("canonical_name") or row.get("name"),
            "set": (variant or {}).get("set_name") or row.get("set"),
            "collectorNumber": (variant or {}).get("collector_number") or row.get("collectorNumber"),
            "language": (variant or {}).get("card_language"),
            "opaqueId": opaque,
            "dbAssetRows": len(asset_rows),
            "candidateCount": len(cands),
        }
        if chosen is None or chosen["servePath"] is None:
            rec_out["resolution"] = "no_image"
            rec_out["chosen"] = {k: v for k, v in (chosen or {}).items() if k != "servePath"}
        else:
            rec_out["resolution"] = "resolved"
            rec_out["chosen"] = {k: v for k, v in chosen.items() if k != "servePath"}
            rec_out["measure"] = measure(chosen["servePath"])
        out_records.append(rec_out)

    payload = {
        "measuredAt": measured_at,
        "universeSize": len(universe),
        "boardSizes": {k: len(v["rows"]) for k, v in boards["boards"].items()},
        "thresholds": {
            "alphaBackground": ALPHA_BG,
            "alphaSolid": ALPHA_SOLID,
            "whiteFloor": WHITE_FLOOR,
            "stdCanvas": f"{STD_W}x{STD_H}",
        },
        "records": out_records,
    }
    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    resolved = sum(1 for r in out_records if r["resolution"] == "resolved")
    print(f"universe={len(universe)} resolved={resolved} no_image={len(universe)-resolved}")
    print(f"-> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
