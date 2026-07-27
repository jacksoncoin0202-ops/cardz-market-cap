"""Calibrate stage-2 thresholds against the cards I eyeballed on the OP sheets."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
R = json.loads((ROOT / "temp" / "image-qc-probe.json").read_text(encoding="utf-8"))["records"]
res = [r for r in R if r["resolution"] == "resolved"]

# What I saw with my own eyes on op100-01/02.png
SEEN_BAD = {36, 71, 112, 249, 80, 98}      # tilt and/or white band
SEEN_OK = {107, 87, 157, 311, 90, 130}     # look fine at sheet scale

def m(r): return r.get("measure") or {}

print("=== cards I judged BAD by eye ===")
for r in sorted(res, key=lambda r: r["variantId"]):
    if r["variantId"] not in SEEN_BAD: continue
    x = m(r)
    print(f"v{r['variantId']:<4} {x.get('width')}x{x.get('height')} "
          f"innerM={x.get('innerMargins')} dX={x.get('innerMarginDiffX')} dY={x.get('innerMarginDiffY')} "
          f"frameMax={x.get('whiteFrameMaxPx')} inkSlope={x.get('inkTiltMaxSlope')} "
          f"inkSpread={x.get('inkTiltMaxSpread')} edgeWhite={x.get('borderWhiteFrac')}")

print("\n=== cards I judged OK by eye ===")
for r in sorted(res, key=lambda r: r["variantId"]):
    if r["variantId"] not in SEEN_OK: continue
    x = m(r)
    print(f"v{r['variantId']:<4} {x.get('width')}x{x.get('height')} "
          f"innerM={x.get('innerMargins')} dX={x.get('innerMarginDiffX')} dY={x.get('innerMarginDiffY')} "
          f"frameMax={x.get('whiteFrameMaxPx')} inkSlope={x.get('inkTiltMaxSlope')} "
          f"inkSpread={x.get('inkTiltMaxSpread')} edgeWhite={x.get('borderWhiteFrac')}")

def dist(key, label):
    vals = sorted(v for v in (m(r).get(key) for r in res) if v is not None)
    if not vals: return
    n = len(vals)
    q = lambda p: vals[min(n - 1, int(n * p))]
    print(f"{label:<22} n={n:<4} med={q(.5):<8} p75={q(.75):<8} p90={q(.90):<8} "
          f"p95={q(.95):<8} max={vals[-1]}")

print("\n=== distributions over all 243 resolved ===")
for k, l in [("whiteFrameMaxPx", "whiteFrameMaxPx"), ("innerMarginDiffX", "innerMarginDiffX"),
             ("innerMarginDiffY", "innerMarginDiffY"), ("inkTiltMaxSlope", "inkTiltMaxSlope"),
             ("inkTiltMaxSpread", "inkTiltMaxSpread"), ("borderWhiteFrac", "borderWhiteFrac")]:
    dist(k, l)

print("\n=== distributions over the 58 v4 std cards (the good baseline) ===")
std = [r for r in res if m(r).get("qcVersion") == "raw-front-v4"]
print(f"n={len(std)}")
for k in ("whiteFrameMaxPx", "innerMarginDiffX", "innerMarginDiffY", "inkTiltMaxSlope",
          "inkTiltMaxSpread", "borderWhiteFrac"):
    vals = sorted(v for v in (m(r).get(k) for r in std) if v is not None)
    if vals:
        n = len(vals)
        print(f"  {k:<20} n={n:<4} med={vals[n//2]:<8} p95={vals[min(n-1,int(n*.95))]:<8} max={vals[-1]}")
