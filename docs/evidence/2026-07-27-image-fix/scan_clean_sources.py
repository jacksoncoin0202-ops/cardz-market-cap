"""用尺寸做代理，喺全部候選 product 裡面篩出「真掃描」圖。

實測發現（2026-07-27，n=44）：TCGplayer CDN 為英文 One Piece 卡供兩種圖——

  * **Bandai 官方樣圖**：尺寸一律 600x838 或 716x1000，中間一大個對角
    `SAMPLE` 水印。已檢查嘅 41 張全部中招，零例外。
  * **真實掃描**：尺寸不規則（641620 = 625x873、557283 = 500x701），無水印。

所以「尺寸唔係 600x838 又唔係 716x1000」就係一個好用嘅預篩條件：規則尺寸嘅
唔用睇都知有水印，唔規則嘅先值得落眼。呢個係代理指標唔係證明，所以篩出嚟嘅
一律仍要人眼覆核水印。

呢個檔只做篩選同落盤，唔會自己揀圖入庫。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_candidates import fetch  # noqa: E402

# 已確認係 Bandai 樣圖模版嘅尺寸，見上面 docstring
SAMPLE_TEMPLATE_SIZES = {"600x838", "716x1000", "600x837"}


def main() -> int:
    candidates = json.loads((ROOT / "temp" / "tcgplayer_candidates.json").read_text(encoding="utf-8"))
    seen: dict[int, dict[str, object]] = {}
    rows: list[dict[str, object]] = []

    for card in candidates:
        measured = []
        for hit in card["hits"]:
            pid = hit["productId"]
            if pid not in seen:
                _, facts = fetch(pid)
                seen[pid] = facts
                time.sleep(0.25)
            facts = dict(seen[pid])
            facts["productName"] = hit["productName"]
            facts["setName"] = hit["setName"]
            size = str(facts.get("size") or "")
            facts["likelyRealScan"] = bool(size) and size not in SAMPLE_TEMPLATE_SIZES
            measured.append(facts)
        clean = [f for f in measured if f.get("likelyRealScan")]
        rows.append({
            "variantId": card["variantId"],
            "collector": card["collector"],
            "wantSet": card["wantSet"],
            "totalHits": len(measured),
            "likelyRealScanCount": len(clean),
            "likelyRealScan": clean,
            "all": measured,
        })
        print(f"{card['variantId']:>4} {card['collector']:<12} hits={len(measured):<3} "
              f"疑真掃描={len(clean)}"
              + ("  ->  " + ", ".join(f"{f['productId']}:{f['size']} {str(f['productName'])[:44]}"
                                     for f in clean) if clean else ""),
              flush=True)

    target = ROOT / "temp" / "clean_source_scan.json"
    target.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(row["likelyRealScanCount"] for row in rows)
    print(f"\n掃咗 {len(seen)} 個唯一 product，疑真掃描 {total} 個")
    print(f"寫入 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
