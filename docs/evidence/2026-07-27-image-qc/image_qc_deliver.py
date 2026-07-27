"""Emit the three deliverables of the 2026-07-27 image QC pass.

Reads temp/image-qc-classified.json (which carries the geometric verdicts) plus
the hand-recorded vision verdicts below, and writes qc-results.csv,
redo-queue.csv and FINDING.md into the evidence pack.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "temp" / "image-qc-classified.json"
PACK = ROOT / "docs" / "evidence" / "2026-07-27-image-qc"

# ---------------------------------------------------------------------------
# Vision verdicts.  Recorded by eye off the contact sheets in sheets/.
# Only cards actually looked at appear here; everything else stays not-checked.
C2_FAIL = {
    8:   "唔係卡面：影住一包未開封嘅卡包（EB02-010 換圖任務進行中）",
    111: "佔位圖：卡面印住大字「NOW DESIGNING」，官方未出圖",
    276: "樣本圖：卡面印住大字「SAMPLE」水印",
}
SHEETS_REVIEWED = ["op100-01.png", "op100-02.png", "fails-01.png (第一版隊列)",
                   "fails-02.png", "fails-03.png"]

# fails-01 was reviewed against the FIRST cut of the queue, before the white-corner
# detector was rebuilt; regenerating the sheets overwrote that file with different
# cards.  The 24 actually looked at are pinned here rather than recomputed, so the
# coverage claim stays true to what was seen.
FAILS01_AS_REVIEWED = [1, 8, 2, 4, 9, 7, 36, 5, 13, 10, 11, 12,
                       15, 16, 71, 17, 80, 18, 98, 20, 26, 25, 23, 27]


def load() -> tuple[list[dict], dict]:
    d = json.loads(SRC.read_text(encoding="utf-8"))
    return d["records"], d["thresholds"]


def eyeballed(records: list[dict]) -> set[int]:
    """Exactly which cards were actually looked at, sheet by sheet."""
    seen: set[int] = set(FAILS01_AS_REVIEWED)
    op = sorted((r for r in records if "op100" in r["boards"] and r["resolution"] == "resolved"),
                key=lambda r: r["ranks"]["op100"])
    seen |= {r["variantId"] for r in op[:20]}                       # op100-01, op100-02
    # fails-02 and fails-03 were read after the rebuild, so they match the file
    # on disk: cards 25..72 of the current queue ordering.
    fails = sorted((r for r in records if r["resolution"] == "resolved"
                    and (r["_v"]["c1"] == "fail" or r["_v"]["c3"] == "fail")),
                   key=lambda r: min(r["ranks"].values()))
    seen |= {r["variantId"] for r in fails[24:72]}
    return seen


def main() -> int:
    records, th = load()
    seen = eyeballed(records)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    rows = []
    for r in sorted(records, key=lambda r: min(r["ranks"].values())):
        vid = r["variantId"]
        v, m = r["_v"], (r.get("measure") or {})
        resolved = r["resolution"] == "resolved"

        if not resolved:
            c1 = c2 = c3 = "無圖可QC"
        else:
            c1, c3 = v["c1"], v["c3"]
            if vid in C2_FAIL:
                c2 = "fail"
            elif vid in seen:
                c2 = "pass"
            else:
                c2 = "未檢查"

        reasons = list(v["reasons"])
        if vid in C2_FAIL:
            reasons.insert(0, C2_FAIL[vid])
        if not resolved:
            reasons = ["磁碟上冇對應圖檔，無法檢查"]
        if resolved and not reasons:
            reasons = ["三項都過"]
        if resolved and v.get("nonStd"):
            reasons.append(f"畫布 {m.get('width')}x{m.get('height')}（非 429x600 標準）")

        rows.append({
            "variant_id": vid,
            "卡名": r["name"] or "",
            "collector_number": r["collectorNumber"] or "",
            "tcg": r["tcg"] or "",
            "最佳板位": min(r["ranks"].values()),
            "板": "/".join(f"{k}#{v2}" for k, v2 in sorted(r["ranks"].items())),
            "C1_白角": c1,
            "C2_係咪嗰張卡": c2,
            "C3_走位白邊": c3,
            "原生透明圓角": ("是" if v.get("nativeRounded") else "否") if resolved else "",
            "畫布": f"{m.get('width')}x{m.get('height')}" if resolved else "",
            "白角三角白佔": m.get("cornerWhiteMax", "") if resolved else "",
            "邊緣斜率px": m.get("inkTiltMaxSlope", "") if resolved else "",
            "內環白佔": m.get("borderWhiteFrac", "") if resolved else "",
            "原因": "；".join(reasons),
            "圖檔路徑": m.get("path", "") if resolved else "",
        })

    PACK.mkdir(parents=True, exist_ok=True)
    p1 = PACK / "qc-results.csv"
    with p1.open("w", encoding="utf-8-sig", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        wr.writerows(rows)

    # ---- redo queue: only the calls I am willing to defend without asking ----
    redo = []
    for row in rows:
        vid = row["variant_id"]
        why, pri = [], None
        if row["C2_係咪嗰張卡"] == "fail":
            why.append(C2_FAIL[vid]); pri = "P0-換圖"
        if row["C3_走位白邊"] == "fail":
            why.append("卡面歪斜／白邊，肉眼一望就唔啱"); pri = pri or "P1-重切"
        if row["C1_白角"] == "fail" and str(row["白角三角白佔"] or 0) and float(row["白角三角白佔"] or 0) >= 0.60:
            why.append("圓角外係遺留背景，深色底會見到方角"); pri = pri or "P2-去背"
        if not why:
            continue
        redo.append({
            "優先級": pri,
            "最佳板位": row["最佳板位"],
            "variant_id": vid,
            "卡名": row["卡名"],
            "collector_number": row["collector_number"],
            "tcg": row["tcg"],
            "畫布": row["畫布"],
            "原因": "；".join(why),
            "圖檔路徑": row["圖檔路徑"],
        })
    redo.sort(key=lambda x: ({"P0-換圖": 0, "P1-重切": 1, "P2-去背": 2}[x["優先級"]], x["最佳板位"]))
    p2 = PACK / "redo-queue.csv"
    with p2.open("w", encoding="utf-8-sig", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(redo[0].keys()))
        wr.writeheader()
        wr.writerows(redo)

    # ---- counts for the report ------------------------------------------
    res = [r for r in rows if r["C1_白角"] != "無圖可QC"]
    cnt = {
        "universe": len(rows),
        "resolved": len(res),
        "noImage": len(rows) - len(res),
        "c1fail": sum(1 for r in res if r["C1_白角"] == "fail"),
        "c1white": sum(1 for r in res if float(r["白角三角白佔"] or 0) >= 0.60),
        "c1all4": sum(1 for r in records if r["resolution"] == "resolved"
                      and len(r["measure"].get("whiteCornersTri") or []) == 4),
        "native": sum(1 for r in res if r["原生透明圓角"] == "是"),
        "c2checked": sum(1 for r in res if r["C2_係咪嗰張卡"] != "未檢查"),
        "c2fail": sum(1 for r in res if r["C2_係咪嗰張卡"] == "fail"),
        "c3fail": sum(1 for r in res if r["C3_走位白邊"] == "fail"),
        "c3suspect": sum(1 for r in res if r["C3_走位白邊"] == "suspect"),
        "nonstd": sum(1 for r in res if r["畫布"] != "429x600"),
        "allpass": sum(1 for r in res if r["C1_白角"] == "pass" and r["C3_走位白邊"] == "pass"),
        "redo": len(redo),
        "redoP0": sum(1 for r in redo if r["優先級"] == "P0-換圖"),
        "redoP1": sum(1 for r in redo if r["優先級"] == "P1-重切"),
        "redoP2": sum(1 for r in redo if r["優先級"] == "P2-去背"),
    }
    (PACK / "counts.json").write_text(
        json.dumps({"measuredAt": now, "thresholds": th, "counts": cnt,
                    "sheetsReviewed": SHEETS_REVIEWED,
                    "redoTop10": redo[:10]}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    print(f"{p1.relative_to(ROOT)}   {len(rows)} 行")
    print(f"{p2.relative_to(ROOT)}   {len(redo)} 行")
    for k, v in cnt.items():
        print(f"  {k:<12} {v}")
    print("\n=== redo top 10 ===")
    for x in redo[:10]:
        print(f"  {x['優先級']}  rank{x['最佳板位']:<4} v{x['variant_id']:<5} "
              f"{x['collector_number']:<12} {x['卡名'][:24]:<24} {x['原因'][:38]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
