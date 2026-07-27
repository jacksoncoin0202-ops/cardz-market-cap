# 真 Top 300 推導 — 今日排名深度係 257，樽頸係價格唔係 POP

## 結論一句

用「價 × PSA10 POP」計真市值，2026-07-26 排得出嘅深度係 **257 張，唔係 300**。
樽頸係**價格覆蓋**（roster 1,468 張只有 395 張有過任何價格觀測），
**唔係 POP**（權威 gemrate POP 已經 97.8% 新鮮，唔使全宇宙重掃）。

## 量度日期同對象身分

- 量度日期：**2026-07-26**
- 量度對象：`market_candidate_daily_snapshot` **evaluation_id = 8**
  （created `2026-07-26 06:28:10`，1,468 行 = 全 roster，universe lock 9）
- POP 數據日：GemRate 最新完整日 = **2026-07-25**（JST 午夜收盤制；07-26 嘅 324 行係 TAG，唔係 gemrate）

## 點量（可直接重跑）

```bash
set -a && . data/runtime/config/backend.env && set +a
python -X utf8 docs/evidence/2026-07-26-top300-derivation/produce_top300.py
```

輸出兩個 CSV（同目錄）：

| 檔 | 行數 | 內容 |
|---|---:|---|
| `top300.csv` | 257 | eval 8 全部 `metric_status='ready'` 卡，按 `market_cap_usd` DESC 排（rank 同 `shadow_rank` 一致） |
| `gap33.csv` | 33 | 權威 POP（`source_code='gemrate'`, `grader_code='PSA'`）停喺 2026-07-24 之前或者從未有嘅 roster 卡 |

## 實測數字（2026-07-26）

**eval 8 狀態分佈（1,468 行）**：

| metric_status | 行數 | 註 |
|---|---:|---|
| ready | **257** | 價 + POP + 市值齊 → 排得名 |
| accumulating | 1,186 | 其中 1,181 張**有 POP 冇價**，只有 5 張有價冇 POP |
| unavailable | 25 | 就係下面 25 張 stale One Piece |

**權威 POP（gemrate/PSA）新鮮度（1,468 roster）**：fresh（07-24/25）**1,435** ·
stale（停 07-21）**25** · never **8** → 覆蓋率 97.8% fresh / 99.5% ever。

**33 張缺口卡全部係 One Piece，全部有 gemrate id mapping（0 張斷鏈）**：

- **25 張 stale**：OP 主系列尾段（variant_id 1492–1586 一帶，OP05/OP07 等），
  每日 run 個 website fallback budget 追到尾追唔切，07-21 之後冇再刷到。
- **8 張 never**：全部係 promo／特別版（ST10-006 一週年、ST21-014 三款、ST13-003 BVB、
  P-110 ONE PIECE DAY'25、OP01-025 Flagship、OP06-119 Comic Parallel Sanji）。
  gemrate 從未回過數，但其中 6 張有 snkrdunk 頁面 PSA 數（07-21）、
  2 張有 snkrdunk/ebay 頁面 PSA 數（07-24）——所以有 3 張照樣入到 ready 榜。
  呢啲屬**非權威源 POP**，出街前要俾 gemrate 或人手覆核。

**Top 5 sanity（top300.csv 頭 5 行）**：
1. Pikachu with Grey Felt Hat 085/SVP — $3,054.55 × 49,517 = **$151.25M**
2. Pikachu 227/S-P — $81.57M
3. Pikachu Munch 288/SM-P — $58.82M
4. Poncho 208/XY-P — $55.15M
5. Poncho 207/XY-P — $54.47M

（EB02-010 Luffy × Dodgers 排第 9，$45.06M——就係已知錯圖嗰張，高風險位。）

## 前提（呢啲變咗，本 finding 就要重量）

1. eval 8 係 2026-07-26 06:28 UTC 嘅產物；之後每日 run 會出新 evaluation，數會郁。
2. GemRate 直連 quota 今日已燒清（09:30 JST run 用咗 ~1,000 direct）；
   剩返 07-27、07-28 兩個 quota 窗口，key 預計 ~07-29 死。
3. `top300.csv` 嘅 rank 係按 `market_cap_usd` DESC 重新編號，實測同 eval 8 嘅
   `shadow_rank` 完全一致；`eligible_rank` 有 255 張（2 張 ready 但唔 eligible，屬圖片閘）。
4. 價格覆蓋 395/1,468 係「有史以來有過價」；要由 257 擴到 300+，
   係**加價格採集覆蓋**嘅事（eBay/SNK 擴 roster），唔係 POP 嘅事。
