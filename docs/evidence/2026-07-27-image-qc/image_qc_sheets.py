"""Build labelled contact sheets from the probe output, for vision review.

Read-only against the assets; writes only into the evidence sheets directory.

Cards are composited on a dark panel that matches the site background, so an
opaque white corner or a white fringe shows up instead of blending into white
page chrome.  A magenta hairline marks the true canvas bounds, so a card that
does not fill its canvas is visible as a gap.

Run:
    python -X utf8 temp/image_qc_sheets.py --group op
    python -X utf8 temp/image_qc_sheets.py --group rest
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[3]
PROBE = ROOT / "temp" / "image-qc-probe.json"
OUT = ROOT / "docs" / "evidence" / "2026-07-27-image-qc" / "sheets"

BG = (26, 29, 36)
PANEL = (16, 18, 23)
GUIDE = (255, 0, 200)
TEXT = (232, 234, 238)
DIM = (150, 156, 168)
WARN = (255, 176, 32)


def font(size: int, bold: bool = False):
    for name in (("seguisb.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf")):
        try:
            return ImageFont.truetype(f"C:/Windows/Fonts/{name}", size)
        except OSError:
            continue
    return ImageFont.load_default()


def build(records: list[dict], cell_w: int, cols: int, label: str, seq: int) -> Path:
    cell_h = int(cell_w / 0.715)
    pad, gutter, cap = 14, 12, 62
    rows = (len(records) + cols - 1) // cols
    W = pad * 2 + cols * cell_w + (cols - 1) * gutter
    H = pad * 2 + 34 + rows * (cell_h + cap + gutter)
    sheet = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(sheet)
    f_title, f_name, f_meta = font(19, True), font(14, True), font(12)

    d.text((pad, pad), f"{label}  ·  sheet {seq}  ·  {len(records)} cards", font=f_title, fill=TEXT)

    for i, r in enumerate(records):
        cx = pad + (i % cols) * (cell_w + gutter)
        cy = pad + 34 + (i // cols) * (cell_h + cap + gutter)
        d.rectangle([cx, cy, cx + cell_w, cy + cell_h], fill=PANEL)

        m = r.get("measure") or {}
        p = m.get("path")
        if p and (ROOT / p).is_file():
            with Image.open(ROOT / p) as im:
                im = im.convert("RGBA")
                scale = min(cell_w / im.width, cell_h / im.height)
                tw, th = max(1, int(im.width * scale)), max(1, int(im.height * scale))
                thumb = im.resize((tw, th), Image.LANCZOS)
                ox, oy = cx + (cell_w - tw) // 2, cy + (cell_h - th) // 2
                sheet.paste(thumb, (ox, oy), thumb)
                d.rectangle([ox, oy, ox + tw - 1, oy + th - 1], outline=GUIDE, width=1)
        else:
            d.text((cx + 8, cy + cell_h // 2), "NO IMAGE", font=f_name, fill=WARN)

        ranks = "/".join(f"{k}#{v}" for k, v in sorted(r["ranks"].items()))
        ty = cy + cell_h + 4
        d.text((cx, ty), f"v{r['variantId']}  {ranks}", font=f_meta, fill=DIM)
        d.text((cx, ty + 14), (r["name"] or "?")[:34], font=f_name, fill=TEXT)
        d.text((cx, ty + 30), f"{r['collectorNumber']}  ·  {(r['set'] or '')[:26]}", font=f_meta, fill=DIM)
        flags = []
        if not m.get("stdCanvas"):
            flags.append(f"{m.get('width')}x{m.get('height')}")
        if m.get("whiteCorners"):
            flags.append(f"whiteCnr:{''.join(m['whiteCorners'])}")
        bw = m.get("borderWhiteFrac")
        if bw is not None and bw >= 0.05:
            flags.append(f"edgeWhite:{bw:.0%}")
        if flags:
            d.text((cx, ty + 44), "  ".join(flags)[:44], font=f_meta, fill=WARN)

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{label.lower().replace(' ', '-')}-{seq:02d}.png"
    sheet.save(path, optimize=True)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", choices=["op", "rest", "fails", "clean"], required=True)
    args = ap.parse_args()

    src = ROOT / "temp" / "image-qc-classified.json" if args.group in ("fails", "clean") else PROBE
    d = json.loads(src.read_text(encoding="utf-8"))
    R = d["records"]

    if args.group in ("fails", "clean"):
        want_fail = args.group == "fails"
        sel = [r for r in R if r["resolution"] == "resolved"
               and (r["_v"]["c1"] == "fail" or r["_v"]["c3"] == "fail") == want_fail]
        sel.sort(key=lambda r: min(r["ranks"].values()))
        cell, cols, per = 240, 6, 24
        label = "FAILS" if want_fail else "CLEAN"
    elif args.group == "op":
        sel = [r for r in R if "op100" in r["boards"] and r["resolution"] == "resolved"]
        sel.sort(key=lambda r: r["ranks"]["op100"])
        cell, cols, per, label = 300, 5, 10, "OP100"
    else:
        sel = [r for r in R if "op100" not in r["boards"] and r["resolution"] == "resolved"]
        sel.sort(key=lambda r: min(r["ranks"].values()))
        cell, cols, per, label = 210, 6, 24, "REST"

    for seq in range(0, len(sel), per):
        p = build(sel[seq:seq + per], cell, cols, label, seq // per + 1)
        print(f"{p.relative_to(ROOT)}  ({len(sel[seq:seq+per])} cards)")
    print(f"total {len(sel)} cards in group {args.group}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
