#!/usr/bin/env python3
"""審計任何一份公開 snapshot 嘅市值 delta / 成交 delta 對唔對，兼量度覆蓋率。

**點解要有呢個腳本**：市值 = 價 × POP，所以市值嘅變動率 **唔係** 價格嘅變動率。
2026-07-26 之前 `rankings.tsx:154` 同 `:158` 直接攞 `windows[w].changePct`
（純價格變動）同時餵市值 delta 同成交 delta，於是同一個數字扮三個指標。
POP 只升唔跌 → 市值幅度永遠低估；價跌而 POP 升得蓋得過 → 乘出嚟由負變正，
**箭嘴指錯方向**。修法係 `(1+Δ價)(1+ΔPOP)−1`（`compose_change_pct` /
`composeChangePct` 兩邊同一條式）。

呢個腳本鏡返 `apps/web/src/lib/format.ts` `formatDeltaMoney()` 嘅反推
（baseline = value / (1 + pct/100)），所以印出嚟嘅銀碼就係訪客真係會見到嗰個。

用法：
    python -X utf8 scripts/audit_snapshot_deltas.py
    python -X utf8 scripts/audit_snapshot_deltas.py --snapshot temp/x.json --window 7d
    python -X utf8 scripts/audit_snapshot_deltas.py --all-windows
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LIVE = {"ready", "stale"}
WINDOWS = ("1d", "7d", "30d")


def live(metric: object) -> float | None:
    if not isinstance(metric, dict):
        return None
    if metric.get("status") not in LIVE or metric.get("value") is None:
        return None
    return float(metric["value"])


def delta_money(value: float | None, change_pct: float | None) -> float | None:
    """鏡返 apps/web/src/lib/format.ts `formatDeltaMoney()` 嘅反推。"""
    if value is None or change_pct is None or change_pct <= -100:
        return None
    return value - value / (1 + change_pct / 100)


def compose(price_pct: float | None, pop_pct: float | None) -> float | None:
    """`(1+Δ價/100)(1+ΔPOP/100)−1`。任何一邊冇數就 None —— fail-closed，
    **唔准**退返去單用 Δ價頂替（頂替就係原本嗰個 bug 本身）。"""
    if price_pct is None or pop_pct is None:
        return None
    return ((1 + price_pct / 100) * (1 + pop_pct / 100) - 1) * 100


def read_rows(document: dict[str, Any], window: str) -> list[dict[str, Any]]:
    rows = []
    for card in document.get("top100", []):
        metrics = card["windows"][window]
        price_pct = live(metrics["changePct"])
        pop_pct = live(card["graderPopulations"]["PSA"]["topGradePopulationChangePct"][window])
        cap = live(card["marketCap"])
        # producer 出咗 `marketCapChangePct` 就信佢；舊 snapshot 冇就即場砌返
        # （同一條式，同 `snapshot.ts` 個 fallback 一模一樣）。
        published = live(metrics.get("marketCapChangePct")) if "marketCapChangePct" in metrics else None
        true_pct = published if published is not None else compose(price_pct, pop_pct)
        sales = live(metrics["trackedSales"]["valueUsd"])
        sales_pct = live(metrics.get("trackedSalesChangePct")) if "trackedSalesChangePct" in metrics else None
        rows.append({
            "rank": int(card["rank"]),
            "name": (card["names"].get("en") or card["id"])[:30],
            "cap": cap,
            "price_pct": price_pct,
            "pop_pct": pop_pct,
            "true_pct": true_pct,
            "from_producer": published is not None,
            "ui_money": delta_money(cap, price_pct),
            "true_money": delta_money(cap, true_pct),
            "sales": sales,
            "sales_pct": sales_pct,
            "sales_money_wrong": delta_money(sales, price_pct),
            "sales_money_right": delta_money(sales, sales_pct),
        })
    return rows


def report(document: dict[str, Any], window: str, watch: set[int], verbose: bool) -> None:
    rows = read_rows(document, window)
    total = len(rows)
    both = [r for r in rows if r["ui_money"] is not None and r["true_money"] is not None]
    flipped = [r for r in both if (r["ui_money"] >= 0) != (r["true_money"] >= 0)]
    under = [r for r in both if r["true_pct"] > r["price_pct"] + 1e-9]
    producer = sum(1 for r in rows if r["from_producer"])
    cap_ready = sum(1 for r in rows if r["true_pct"] is not None)
    sales_ready = sum(1 for r in rows if r["sales_pct"] is not None)

    print(f"[{window}] 卡 {total}｜市值 delta 有值 {cap_ready}/{total}（producer 出 {producer}）"
          f"｜成交 delta 有值 {sales_ready}/{total}")
    print(f"[{window}] 兩個 % 都有值 {len(both)}｜箭嘴指錯方向 {len(flipped)}｜UI 低估 {len(under)}")

    if watch:
        print(f"\n{'rank':>4}  {'卡':<30} {'舊 UI 顯示':>14} {'修完顯示':>14} {'差異':>10}"
              f"  價Δ%     POPΔ%    市值Δ%")
        for r in rows:
            if r["rank"] not in watch:
                continue
            ui, tr = r["ui_money"], r["true_money"]
            if ui is None or tr is None:
                print(f"{r['rank']:>4}  {r['name']:<30} "
                      f"{'—' if ui is None else format(ui, '+,.0f'):>14} {'—' if tr is None else format(tr, '+,.0f'):>14}")
                continue
            note = "正負相反" if (ui >= 0) != (tr >= 0) else f"放大 {abs(ui) / abs(tr):.2f}x" if tr else "—"
            print(f"{r['rank']:>4}  {r['name']:<30} {ui:>+14,.0f} {tr:>+14,.0f} {note:>10}"
                  f"  {r['price_pct']:+7.2f} {r['pop_pct']:+7.2f} {r['true_pct']:+7.2f}")

    if verbose and flipped:
        print(f"\n### [{window}] 箭嘴指錯方向嘅卡（{len(flipped)} 張）")
        for r in sorted(flipped, key=lambda x: x["rank"]):
            print(f"{r['rank']:>4}  {r['name']:<30} 舊 {r['ui_money']:>+13,.0f} → 修完 {r['true_money']:>+13,.0f}"
                  f"  (價 {r['price_pct']:+.2f}% × POP {r['pop_pct']:+.2f}%)")

    # 成交 delta：舊碼一樣攞價格 changePct 頂替，但成交額同價格變動 % 由頭到尾
    # 冇任何數學關係 —— 唔係精度問題，係兩個唔同嘅量。
    wrong_sales = [r for r in rows if r["sales_money_wrong"] is not None]
    if verbose and wrong_sales:
        differ = [r for r in wrong_sales if r["sales_money_right"] is not None
                  and (r["sales_money_wrong"] >= 0) != (r["sales_money_right"] >= 0)]
        print(f"\n### [{window}] 成交 delta：舊碼有顯示 {len(wrong_sales)} 張"
              f"｜真數算得到 {sales_ready} 張｜其中方向相反 {len(differ)} 張")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--snapshot", default=str(ROOT / "data/public/seed-snapshot.json"))
    parser.add_argument("--window", default="30d", choices=WINDOWS)
    parser.add_argument("--all-windows", action="store_true")
    parser.add_argument("--ranks", default="1,2,4,7", help="逗號分隔；空字串 = 唔印逐張表")
    parser.add_argument("--quiet", action="store_true", help="唔印方向相反嘅卡清單")
    args = parser.parse_args()

    document = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    watch = {int(r) for r in args.ranks.split(",") if r.strip()}
    print(f"snapshot = {args.snapshot}")
    print(f"generation = {document['generation']['id']} / effective {document['generation']['effectiveAt']}")
    for window in (WINDOWS if args.all_windows else (args.window,)):
        print()
        report(document, window, watch, not args.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
