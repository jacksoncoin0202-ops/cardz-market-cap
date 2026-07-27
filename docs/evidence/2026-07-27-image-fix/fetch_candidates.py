"""下載候選卡面圖、量度、砌人眼比對表。

每張缺圖卡嘅本機 bytes 係評級殼相（卡封膠殼），入唔到庫，但**殼相睇得到美術圖**，
所以佢係最可靠嘅「應該係咩樣」參考。呢個檔就係將本機殼相同 TCGplayer 候選並排，
逐張卡出一行，等人眼一眼睇得出邊個 productId 係同一幅畫。

同一個卡號通常有 base / (Alternate Art) / (Manga) / (Parallel) / (SP) / (Gold) 幾個
印次，價格差幾十倍，所以揀錯印次比冇圖更差——呢個檔唔會自動揀。
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "pipelines"))

from PIL import Image, ImageDraw  # noqa: E402
from curl_cffi import requests  # noqa: E402

CACHE = ROOT / "temp" / "tcg-candidates"
CDN = "https://tcgplayer-cdn.tcgplayer.com/product/{pid}_in_1000x1000.jpg"

# vid -> (primary, *alternates)。名稱對應理由寫喺 FINDING.md 嘅對照表。
SHORTLIST: dict[int, tuple[int, ...]] = {
    19: (527027, 529850, 657798),
    24: (527026,),
    43: (632504, 632503),
    60: (541660,),
    63: (629167, 629166),
    69: (657400, 657402, 657403),
    72: (632503, 632504),
    78: (587966,),
    102: (454666, 454665),
    103: (453506, 454515),
    128: (632499, 632498),
    132: (516555,),
    134: (657403, 657402, 657401),
    146: (587963, 586960),
    163: (661704, 615591),
    171: (597068, 597069),
    175: (544532, 544531, 587962),
    188: (485862, 587709),
    192: (654637, 656165),
    197: (619216, 596982),
}

THUMB = (170, 238)


def fetch(pid: int) -> tuple[Path | None, dict[str, object]]:
    target = CACHE / f"{pid}.jpg"
    if target.is_file():
        raw = target.read_bytes()
    else:
        try:
            response = requests.get(CDN.format(pid=pid), impersonate="chrome", timeout=30)
        except Exception as exc:  # noqa: BLE001
            return None, {"productId": pid, "error": f"{type(exc).__name__}"}
        if response.status_code != 200:
            return None, {"productId": pid, "error": f"HTTP {response.status_code}"}
        raw = response.content
        target.write_bytes(raw)
    with Image.open(io.BytesIO(raw)) as image:
        width, height = image.size
        mode = image.mode
    return target, {
        "productId": pid,
        "bytes": len(raw),
        "size": f"{width}x{height}",
        "ratio": round(width / height, 4),
        "mode": mode,
        # 標準畫布係 429x600；細過就係放大，放大會糊
        "upscaleRisk": width < 429 or height < 600,
        "ratioOk": 0.69 <= width / height <= 0.735,
    }


def label(draw: ImageDraw.ImageDraw, x: int, y: int, text: str) -> None:
    draw.text((x, y), text, fill=(20, 20, 20))


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    work = {card["variantId"]: card for card in
            json.loads((ROOT / "temp" / "missing22_work.json").read_text(encoding="utf-8"))}
    candidates = {row["variantId"]: row for row in
                  json.loads((ROOT / "temp" / "tcgplayer_candidates.json").read_text(encoding="utf-8"))}
    names = {hit["productId"]: hit["productName"]
             for row in candidates.values() for hit in row["hits"]}

    rows = sorted(SHORTLIST)
    columns = 1 + max(len(v) for v in SHORTLIST.values())
    cell_w, cell_h = THUMB[0] + 12, THUMB[1] + 30
    sheet = Image.new("RGB", (cell_w * columns + 8, cell_h * len(rows) + 8), (245, 245, 245))
    draw = ImageDraw.Draw(sheet)

    report: list[dict[str, object]] = []
    for index, vid in enumerate(rows):
        y = 4 + index * cell_h
        reference = Image.open(work[vid]["localPath"]).convert("RGB")
        reference.thumbnail(THUMB)
        sheet.paste(reference, (4, y + 22))
        label(draw, 4, y + 6, f"vid {vid} LOCAL(slab) {work[vid]['collector']}")

        measured = []
        for slot, pid in enumerate(SHORTLIST[vid], start=1):
            path, facts = fetch(pid)
            facts["productName"] = names.get(pid)
            facts["slot"] = slot
            measured.append(facts)
            x = 4 + slot * cell_w
            flag = "" if facts.get("error") else (
                "  !UPSCALE" if facts["upscaleRisk"] else ("" if facts["ratioOk"] else "  !RATIO"))
            label(draw, x, y + 6, f"{pid}{flag}")
            if path is None:
                label(draw, x, y + 22, str(facts.get("error")))
                continue
            thumb = Image.open(path).convert("RGB")
            thumb.thumbnail(THUMB)
            sheet.paste(thumb, (x, y + 22))
            time.sleep(0.25)
        report.append({"variantId": vid, "collector": work[vid]["collector"],
                       "wantSet": work[vid]["set"], "candidates": measured})
        print(f"{vid:>4} {work[vid]['collector']:<12} " +
              " | ".join(f"{c['productId']}:{c.get('size', c.get('error'))}" for c in measured),
              flush=True)

    sheet_path = ROOT / "temp" / "candidate_compare.png"
    sheet.save(sheet_path)
    (ROOT / "temp" / "candidate_measurements.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n比對表 {sheet_path}  ({sheet.width}x{sheet.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
