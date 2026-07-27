"""Apply the calibrated thresholds and report cohort sizes + the eyeball worklist."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
R = json.loads((ROOT / "temp" / "image-qc-probe.json").read_text(encoding="utf-8"))["records"]

TILT_SLOPE = 2.0    # px; calibrated on 12 eyeballed op100 cards
WHITE_BAND = 0.12   # fraction of the 3px inner ring that is near-white
STD_W, STD_H = 429, 600


def verdict(r: dict) -> dict:
    x = r.get("measure") or {}
    v = {"c1": "no-image", "c3": "no-image", "reasons": []}
    if r["resolution"] != "resolved":
        return v
    wc = x.get("whiteCornersTri") or []
    lc = x.get("lightCornersTri") or []
    v["nativeRounded"] = bool(x.get("nativeRounded"))
    if wc:
        v["c1"] = "fail"
        v["reasons"].append(f"白角 {len(wc)}/4（圓弧外係實心白背景）")
    elif lc:
        v["c1"] = "fail"
        v["reasons"].append(f"淺色方角 {len(lc)}/4（圓弧外係淺灰背景）")
    else:
        v["c1"] = "pass"

    tilt = x.get("inkTiltMaxSlope")
    band = x.get("borderWhiteFrac")
    bad3, sus3 = [], []
    if tilt is not None and tilt >= TILT_SLOPE:
        bad3.append(f"歪斜（卡面邊緣斜 {tilt:.0f}px）")
    if band is not None and band >= WHITE_BAND:
        # a white printed border or pale artwork also lands here, so on its own
        # this is only a suspicion; with tilt it is the defect the user reported
        (bad3 if bad3 else sus3).append(f"白邊（內環白佔 {band:.0%}）")
    v["c3"] = "fail" if bad3 else ("suspect" if sus3 else "pass")
    v["reasons"] += bad3 + sus3
    v["nonStd"] = not (x.get("width") == STD_W and x.get("height") == STD_H)
    return v


res = [r for r in R if r["resolution"] == "resolved"]
for r in R:
    r["_v"] = verdict(r)

c1f = [r for r in res if r["_v"]["c1"] == "fail"]
c3f = [r for r in res if r["_v"]["c3"] == "fail"]
c3s = [r for r in res if r["_v"]["c3"] == "suspect"]
tilt = [r for r in res if (r["measure"].get("inkTiltMaxSlope") or 0) >= TILT_SLOPE]
band = [r for r in res if (r["measure"].get("borderWhiteFrac") or 0) >= WHITE_BAND]
nonstd = [r for r in res if r["_v"]["nonStd"]]
anyf = [r for r in res if r["_v"]["c1"] == "fail" or r["_v"]["c3"] in ("fail","suspect")]

print(f"resolved            {len(res)}")
print(f"no image            {len(R) - len(res)}")
print(f"C1 白角 fail        {len(c1f)}")
print(f"C3 fail (any)       {len(c3f)}   [歪斜 {len(tilt)} · 白邊 {len(band)} · 兩者 {len(set(id(x) for x in tilt) & set(id(x) for x in band))}]")
print(f"C3 suspect (白邊獨立)  {len(c3s)}")
print(f"C1 or C3 有問題     {len(anyf)}")
print(f"原生透明圓角        {sum(1 for r in res if r['_v']['nativeRounded'])}")
print(f"non-std canvas      {len(nonstd)}")
print(f"C1+C3 both pass     {len(res) - len(anyf)}")
print(f"  ...of which non-std {sum(1 for r in res if r not in anyf and r['_v']['nonStd'])}")

print("\n=== canvas sizes among resolved ===")
sizes: dict[str, int] = {}
for r in res:
    x = r["measure"]
    sizes[f"{x.get('width')}x{x.get('height')}"] = sizes.get(f"{x.get('width')}x{x.get('height')}", 0) + 1
for k, n in sorted(sizes.items(), key=lambda kv: -kv[1]):
    print(f"  {k:<12} {n}")

print("\n=== eyeball worklist: C1/C3 fails, by best board rank ===")
anyf.sort(key=lambda r: min(r["ranks"].values()))
for r in anyf:
    x = r["measure"]
    print(f"  v{r['variantId']:<5} rank{min(r['ranks'].values()):<4} {r['collectorNumber']:<12} "
          f"{(r['name'] or '')[:26]:<26} {'·'.join(r['_v']['reasons'])}")
print(f"\n{len(anyf)} cards to eyeball for C2 (plus 20 op100 already done)")

json.dump({"records": R, "thresholds": {"tiltSlope": TILT_SLOPE, "whiteBand": WHITE_BAND}},
          open(ROOT / "temp" / "image-qc-classified.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
