"""Corner zoom strip: prove or disprove the 124 white-corner flags by eye."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "docs" / "evidence" / "2026-07-27-image-qc" / "sheets"
BG, PANEL, TEXT, DIM, WARN = (26, 29, 36), (200, 30, 140), (232, 234, 238), (150, 156, 168), (255, 176, 32)
Z = 96          # px of the original taken from each corner
S = 4           # magnification


def font(sz, bold=False):
    for n in (("seguisb.ttf",) if bold else ("segoeui.ttf",)):
        try: return ImageFont.truetype(f"C:/Windows/Fonts/{n}", sz)
        except OSError: pass
    return ImageFont.load_default()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vids", required=True, help="comma separated variant ids")
    ap.add_argument("--name", required=True)
    a = ap.parse_args()
    want = [int(x) for x in a.vids.split(",")]

    R = json.loads((ROOT / "temp" / "image-qc-classified.json").read_text(encoding="utf-8"))["records"]
    by = {r["variantId"]: r for r in R}
    rows = [by[v] for v in want if v in by]

    cw = Z * S
    lab = 42
    W = 20 + 4 * cw + 3 * 10 + 340
    H = 20 + len(rows) * (cw + lab)
    sheet = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(sheet)
    f, fb = font(15), font(17, True)

    for i, r in enumerate(rows):
        m = r["measure"]
        y = 20 + i * (cw + lab)
        with Image.open(ROOT / m["path"]) as im:
            im = im.convert("RGBA")
            w, h = im.size
            z = m.get("cornerRadiusPx") or Z      # crop exactly what was measured
            boxes = {"tl": (0, 0, z, z), "tr": (w - z, 0, w, z),
                     "bl": (0, h - z, z, h), "br": (w - z, h - z, w, h)}
            for j, (nm, bx) in enumerate(boxes.items()):
                x = 20 + j * (cw + 10)
                # magenta backdrop: transparent pixels show as magenta, white shows as white
                cellbg = Image.new("RGBA", (cw, cw), PANEL + (255,))
                crop = im.crop(bx).resize((cw, cw), Image.NEAREST)
                cellbg.alpha_composite(crop)
                sheet.paste(cellbg.convert("RGB"), (x, y))
                d.rectangle([x, y, x + cw - 1, y + cw - 1], outline=(90, 96, 108))
                flag = nm in (m.get("whiteCorners") or [])
                d.text((x + 4, y + 4), nm.upper() + ("  WHITE" if flag else ""), font=fb,
                       fill=WARN if flag else DIM)
        tx = 20 + 4 * (cw + 10)
        d.text((tx, y + 6), f"v{r['variantId']}  {(r['name'] or '')[:24]}", font=fb, fill=TEXT)
        d.text((tx, y + 28), f"{r['collectorNumber']}  {m['width']}x{m['height']}  {m['mode']}", font=f, fill=DIM)
        d.text((tx, y + 48), f"whiteCorners={''.join(m.get('whiteCorners') or []) or '-'}", font=f, fill=WARN)
        d.text((tx, y + 68), f"opaqueCorners={''.join(m.get('opaqueCorners') or []) or '-'}", font=f, fill=DIM)
        d.text((tx, y + 88), f"edgeWhite={m.get('borderWhiteFrac')}  tilt={m.get('inkTiltMaxSlope')}", font=f, fill=DIM)
        d.text((20, y + cw + 6), "背景係洋紅色：透明 = 見洋紅｜白角 = 見白色方角", font=f, fill=DIM)

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"corners-{a.name}.png"
    sheet.save(p, optimize=True)
    print(p.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
