#!/usr/bin/env python3
"""為 OP POP>=1000 未有價嘅目標卡，喺 SNKRDUNK 搜索候選 apparel id（唯讀）。

只做 HTTP GET search HTML，唔寫 DB、唔寫 crosswalk。輸出係「候選 + 證據」，
交返人／下一個 agent 審批，因為我哋 catalog 冇 parallel 欄位，
同一個 collector number 喺 SNKRDUNK 可以對應多個 parallel listing。

判定：
  unique_single      bracket 命中且只有一個候選 → 可以提名為 exact mapping
  parallel_ambiguous bracket 命中多個 parallel → 要人揀，唔准估
  not_found          搜唔到 → SNKRDUNK 冇收錄／關鍵詞唔中
"""
import csv, json, re, sys, time, urllib.parse, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
SEALED = re.compile(r"パック|ボックス|BOX|カートン|未開封|テーマデッキ|スタートデッキ")


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=25) as fh:
        return fh.read().decode("utf-8", "replace")


def candidates(html: str, collector: str) -> list[dict]:
    want = f"[{collector}]"
    out, seen = [], set()
    for chunk in html.split("/apparels/")[1:]:
        m = re.match(r"(\d+)", chunk)
        if not m:
            continue
        item_id = int(m.group(1))
        window = chunk[:400]
        if want not in window:
            continue
        title = ""
        tm = re.search(r"([^\"<>]{4,90}?)\s*" + re.escape(want), window)
        if tm:
            title = " ".join(tm.group(1).split())
        # 一定要用 itemId 做 dedup key。同一個 apparel id 喺同一頁會用兩種寫法
        # 出現（例如「ロロノア・ゾロ L パラレル」同「ロロノア・ゾロ L-P」），
        # 用 (id, title) 做 key 會令同一件商品當兩個候選，虛報 ambiguity。
        if item_id in seen:
            continue
        seen.add(item_id)
        # sealed 只准由 bracket 之前嘅標題判斷。bracket 之後嘅括號係 set 名
        # （例如「ブースターパック『神速の拳』」），嗰個 パック 係 set 名嘅一部分，
        # 唔代表未開封商品——用整個 window 判會誤殺真單卡。
        out.append({"itemId": item_id, "titlePrefix": title, "sealed": bool(SEALED.search(title))})
    return out


def main() -> int:
    targets = list(csv.DictReader(open(HERE / "op_targets_133.csv", encoding="utf-8")))
    numbers: dict[str, list[dict]] = {}
    for row in targets:
        numbers.setdefault(row["collector_number"], []).append(row)

    results, tally = [], {"unique_single": 0, "parallel_ambiguous": 0, "not_found": 0, "error": 0}
    for idx, (collector, rows) in enumerate(sorted(numbers.items()), 1):
        try:
            html = fetch("https://snkrdunk.com/search?keywords=" + urllib.parse.quote(collector) + "&page=1")
            cand = [c for c in candidates(html, collector) if not c["sealed"]]
            verdict = "unique_single" if len(cand) == 1 else ("parallel_ambiguous" if cand else "not_found")
        except Exception as exc:  # noqa: BLE001
            cand, verdict = [], "error"
            print(f"  !! {collector} {type(exc).__name__}: {exc}", file=sys.stderr)
        tally[verdict] += 1
        results.append({
            "collectorNumber": collector,
            "verdict": verdict,
            "targetVariants": [
                {"variantId": r["variant_id"], "name": r["canonical_name"], "setName": r["set_name"], "pop": r["pop"]}
                for r in rows
            ],
            "snkCandidates": cand,
        })
        if idx % 20 == 0:
            print(f"  ... {idx}/{len(numbers)} {tally}", flush=True)
        time.sleep(1.0)

    (HERE / "snk_candidates.json").write_text(
        json.dumps({"measuredAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "distinctCollectorNumbers": len(numbers), "tally": tally, "rows": results},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\ndistinct collector numbers: {len(numbers)}")
    print(f"tally: {tally}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
