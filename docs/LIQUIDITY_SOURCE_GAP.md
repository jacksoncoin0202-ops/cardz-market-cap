# 流動性「假低」診斷 — 源有成交，我哋未接到

> **用戶**：一定有成交，冇可能成個 Top 池得咁少流動性。  
> **結論（2026-07-29 實測）**：**同意。** 問題係 **腳本／identity 未把成交寫入或 join 到 watchlist variant**，唔係市場乾塘。

---

## 1. 數字對照（點解「98 張」係假象）

| 量度 | 數 | 含義 |
|---|---:|---|
| `market_sale_observation` 全庫 | **~121,000 行** | 庫入面其實有大量成交 |
| 其中 ebay / snkrdunk / snk_grade | 19.7k / 83.5k / 18.1k 行 | 多源已入過 |
| 全庫有 sale 嘅 variant（ebay） | **~507** | 唔止 98 |
| **940 watchlist ∩ 任何 sale** | **~91–95** | **樽頸：join 唔上 940** |
| 有價但 **永遠 0 sale 行** 嘅 watchlist | **~818** | 價有、成交表對呢張卡空白 |
| SNK identity 喺 940 | **~97** | 先有 id 先拉到 SNK 成交 |
| SNK harvest 檔 `snk-psa10-940.jsonl` | **97 行** 且多數有 `recent_trades` / `daily_activity` | **已爬到 trades，未入 sale 表**（只入咗價 kline） |

先前「30 日有成交 = 98」用咗 watchlist∩sale；**同「市場真實流動性」唔等價**。

---

## 2. 邊啲來源 **可以** 補流動性（有腳本）

| 優先 | 源 | 現有腳本 | 而家斷喺邊 | 可補幾多 |
|---:|---|---|---|---|
| **P0** | **SNK completed trades** | `snk_market_data.py`（已有 `trades` / `daily_activity`） | harvest JSONL 有，**冇 writer → `market_sale_observation`** | 先救 **~97** 已 bind；擴 bind 後可上百+ |
| **P0** | **擴 SNK exact bind** | `bind_snk_watchlist.py` | 940 只有 ~97 id | 中長期主線 JP 流動性 |
| **P1** | **eBay PSA10 sold（PC 衍生）** | `pricecharting_ebay_export.py` → `ebay_sold_data.py` · `g10_ebay_ingest` | 940 只有 ~91 有 ebay sale；**PC product map 未齊**；eBay sold search 被 PX 擋 | 擴 PC map 後 US 流動性 |
| **P1** | **G10 sales_cache** | `g10_sales_cache_ingest.py` | 歷史大量；**identity map 唔中就 quarantine** | 補舊窗 + 漏 join |
| **P2** | SNK `daily_activity` 當 **流動性 proxy** | 已在 jsonl | 可先做 gate 輸入（count>0 即非低流動）再完整逐筆 | 快速 unblock Top100 席位 |
| **唔用** | Limitless / OP.gg `$` | — | **raw 市價**，唔係成交筆數／PSA10 sold | 最多做參考，**唔當 tracked sales** |

**路由契約**（`data_routing`）：tracked sales primary = **SNK recent trades**，secondary = **eBay PSA10 sold** — 同上面 P0/P1 一致。

---

## 3. 斷層圖（一句）

```text
SNK API trades ──► snk-psa10-940.jsonl (有 recent_trades) ──✗──► market_sale_observation
                         │
                         └──► market_price_observation (只有價 ✓)

eBay/PC sold ──► 部分 g10/PC 入庫 ──✗──► 多數 940 無 catalog_source_identity(ebay)
                                        └──► 818 張有價無 sale 行

流動性閘讀 sale 表 ──► 以為「低流動」──► 其實「未接線」
```

---

## 4. 建議修法（務實次序）

| 步 | 動作 | 效果 |
|---:|---|---|
| 1 | **寫 `ingest_snk_trades_to_sales.py`**（或併入 operator）：讀 `recent_trades` → `market_sale_observation`（`source_code=snkrdunk`，PSA10 條件） | 即刻令 ~97 張有真 30d 成交 |
| 2 | 用 jsonl `daily_activity` **臨時**標 `liquidity_ok`（30d count>0）做 Top100 閘 | 未逐筆前唔誤殺 |
| 3 | 擴 SNK bind（card# exact） | 更多卡有成交源 |
| 4 | 對 FE/高市值卡跑 PC eBay export map | 補 US 成交 |
| 5 | 重跑 `g10_sales_cache_ingest` + identity 對表 | 回收歷史成交 |
| 6 | 流動性閘改用 **`sold_at`**（唔好用 `created_at`）+ PSA10 濾 | 量度正確 |

**未做 1–2 之前**：唔好對用戶講「市場只有 98 張有流動性」——應講 **「DB 只 join 到 ~95 張有 sale 行；SNK 檔已有更多 activity 未入表」**。

---

## 5. 同 Top100 規則嘅關係

用戶規則「30 日 0 成交禁止上 Top100」**仍然啱**。  
但執行前必須：

1. **先補成交接線**（上表），再  
2. 用補完嘅表跑閘，  

否則會把 **未爬到／未 join** 誤判成 **真低流動**，把高市值卡錯踢。

---

## 6. 變更

| 日期 | 內容 |
|---|---|
| 2026-07-29 | 首版：121k 行 vs watchlist 95；SNK trades 未入 sale 表；可補源清單 |
