# FINDING — eBay PSA10 成交接入 producer（ebay-sales-wiring）

- **量度日期**：2026-07-27 晚間（promotion 後 :3800 實測）
- **結論一句**：producer `latest_sales()` 併入 eBay PSA10 成交後，snapshot 原生成交覆蓋
  30d 246→257、零成交卡 12→1（剩 rank 96 ST10-006），30d 成交環比 54→106、7d 194→232；
  主板 Top100「—」由 11 減到 1。順帶踩中兼修復兩個陷阱：`--view` 默認值錯配、SSR 頁面 memory cache。
- **前提**：dev :3800 經 `MARKET_DATA_SNAPSHOT_PATH` 讀 `data/runtime/local-serve/snapshot.json`；
  generation `canonical_20260726_e88c81289ac3`；presentation view `top300_boards`（258 published）；
  view 層借數 cascade（1d→7d→30d）已上線。

## 做咗咩

`pipelines/canonical_public_snapshot.py` 嘅 `latest_sales()` 由 snk_psa10 單源
改成 snk_psa10 + eBay PSA10 雙源合併，history sparkline 同步合併。

**eBay PSA10 唯一正確取法**：`market_sale_observation` 過濾
`source_code='ebay' AND grader_code='psa' AND grade_label='10'` 逐單聚合。
⚠ **唔准用 `market_daily_sales_aggregate` 嘅 ebay 行**——嗰啲係全 grade 混合，唔係 PSA10。

## 點量（可重跑）

```bash
cd "C:\Users\jackson0202\Documents\Playground\cardz-market-cap"
set -a && . data/runtime/config/backend.env && set +a

# 重出 snapshot（--view 必須明寫，見陷阱 1）
python -X utf8 pipelines/canonical_public_snapshot.py \
  --presentation data/runtime/local-serve/snapshot.json \
  --view top300_boards --production --output temp/prod-candidate.json

# 驗證：零成交卡計數（schema 係 nested，見陷阱 2）
python -X utf8 -c "
import json
d = json.load(open('temp/prod-candidate.json', encoding='utf-8'))
cards = d['top100'] + d['watchlist']
zero = [c for c in cards if all(
    (((c['windows'][w].get('trackedSales') or {}).get('count') or {}).get('value') in (None, 0))
    for w in ('1d','7d','30d'))]
print(len(zero), [c['rank'] for c in zero])"

# 驗證：live 頁面空格（SSR，重啟 dev server 後先準——見陷阱 3）
curl -s "http://localhost:3800/" | grep -c '>—<'
```

## 實測數字（258 published，接線前 → 後）

| 指標 | 前 | 後 |
|---|---:|---:|
| salesReady 1d | 165 | 165 |
| salesReady 7d | 228 | 249 |
| salesReady 30d | 246 | 257 |
| trackedSalesChangePct 7d | 194 | 232 |
| trackedSalesChangePct 30d | 54 | 106 |
| 零成交卡（三窗全空） | 12 | **1**（rank 96 ST10-006） |
| 主板 Top100 SSR「—」 | 11 | **1** |

窗口值 containment（1d ⊆ 7d ⊆ 30d）實測 0 violations。
剩低嗰張 rank 96 Monkey.D.Luffy ST10-006：snk 死於 06-15（見
[2026-07-27-op-published-27/FINDING.md](../2026-07-27-op-published-27/FINDING.md) 病類 B），
兼 `market_sale_observation` 無任何 eBay PSA10 成交——兩源都冇嘢可接，唔係接線問題。

## 三個陷阱（今次全部真踩過）

1. **`--view` argparse 默認係 `top300`，但 production 係 `top300_boards`。**
   漏咗 flag 嘅 regen 靜靜出咗 224 張（board-extras 34 張蒸發），完全冇報錯。
   兩個 view 嘅 ranked row set 唔同（336 vs 300）——`build_snapshot()` 用
   `include_board_extras=(presentation_view == "top300_boards")` 決定。
   **Regen 前必查現役 snapshot 嘅 `coverage.requestedView`，照抄佢。**
   當日為咗追呢 34 張卡，image gate／QC／catalog 方向全部查錯晒——gate 由頭到尾冇問題。
2. **`trackedSales` 係 nested object**：`{count: {value}, valueUsd: {value}, coverage, asOf}`，
   唔係扁平 `{value}`。用錯 key 讀出「258/258 全零」呢種完全錯嘅結論。
   驗證 script 落筆前先 dump 一張卡個真 JSON shape。
3. **SSR 頁面 route 揸住 snapshot 喺 process memory，API route 每 request 重讀。**
   換咗 snapshot 檔之後 `/api/v1/market` 即刻反映新數，但 `/` 頁面繼續 render 舊數
   （實測：API 99/100 有成交、頁面照出 11 個「—」）。**Promotion 後要重啟 dev server**
   先算完成——「file replacement 唔使 restart」只對 API route 成立。
