# Grade10 數據整合技術文檔

> 版本： 1.0 | 日期： 2026-07-22 | 狀態： 已上線運行

---

## 1. 項目概述

本項目建立一套自動化數據整合管線，每日定時從 Grade10 平台同步收藏卡市場數據至本地存儲，用於後續分析、研究及投資參考。

**Grade10 平台**提供兩項核心服務：
- **指數服務** (`index.grade10.com`) — 發佈 PTCG500、PTCG100、OPCG 三個收藏卡市場基準指數
- **研究服務** (`app.grade10.com`) — 提供每隻成份卡的詳細市場數據、成交記錄、鑑定統計及 AI 研究報告

---

## 2. 數據來源

### 2.1 指數數據

| 指數 | 代碼 | 成份卡數 | 說明 |
|------|------|---------|------|
| PTCG500 | `ptcg` | 500 | 寶可夢卡牌綜合基準 |
| PTCG100 | `ptcg100` | 100 | 寶可夢卡牌精選 |
| OPCG | `opcg` | 100 | One Piece 卡牌基準 |

每個指數提供以下數據面：

| 數據面 | 內容 | 更新頻率 |
|--------|------|---------|
| 統計記錄 | ATH（歷史最高）、ATL（歷史最低）、52 週高低、成立以來回報率 | 每日 |
| 當前摘要 | 指數現值、7 日變化率、下次調倉日期 | 每日 |
| 圖表序列 | 每日指數收盤值，支援 1W/1M/3M/6M/YTD/ALL 六種時間範圍 | 每日 |
| 成份卡清單 | 完整成份排名（含卡名、系列、語言、美元價格、30 日漲跌、權重、圖片連結） | 每日 |

其中 `ALL` 範圍包含自 2023 年 2 月以來的完整每日歷史（逾 1,200 個數據點）。

### 2.2 卡片數據

兩個指數合計覆蓋 **600 隻去重收藏卡**，分佈於兩個數據源：

| 數據源 | 卡片數 | ID 格式 | 說明 |
|--------|--------|---------|------|
| Snkrdunk | 530 | 數字 | 日本最大收藏卡交易平台 |
| Alt.xyz | 170 | UUID | 美國另類資產交易平台 |

每隻卡提供六類數據：

| 數據面 | 內容 |
|--------|------|
| 基本資料 | 完整卡名、顯示標題、卡片名稱、系列名、卡號、年份、語言、圖片 URL |
| Snkrdunk 成交記錄 | 最近成交明細（日期、鑑定等級、成交價、數量），附平均價 |
| eBay 成交記錄 | 最近 eBay 售出明細（日期、價格、鑑定等級），附平均價 |
| 鑑定人口統計 | PSA、BGS、CGC、SGC 四大鑑定機構的鑑定總量及頂級數量 |
| AI 研究報告 | 由 Grade10 AI 生成的卡片深度研究報告（支援英文及日文） |
| 卡片圖片 | 高清卡片掃描圖（WebP/JPG） |

### 2.3 鑑定等級體系

平台使用統一的鑑定等級編碼：

| 編碼 | 等級 | 說明 |
|------|------|------|
| -1 | All | 全部等級混合 |
| 20-21 | C / D | 未鑑定卡品相分級 |
| 22-24 | PSA 10 / PSA 9 / PSA 8↓ | PSA 鑑定 |
| 25-28 | BGS BL / BGS 10 / BGS 9.5 / BGS 9↓ | BGS 鑑定 |
| 29-32 | ARS 10+ / ARS 10 / ARS 9 / ARS 8↓ | ARS 鑑定 |

eBay 成交查詢使用對應的字串標籤（如 `"PSA 10"`, `"BGS BL"`, `"ARS 10+"` 等）。

---

## 3. 技術架構

### 3.1 系統組成

```
┌─────────────────────────────────────────────────┐
│              Windows Task Scheduler              │
│         Grade10-Daily-Scraper @ 06:30            │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│            grade10_scraper.py                   │
│                                                 │
│  ┌───────────┐  ┌────────────┐  ┌────────────┐  │
│  │  Index    │  │   Card     │  │   Image    │  │
│  │  Fetcher  │  │   Fetcher  │  │  Fetcher   │  │
│  └─────┬─────┘  └─────┬──────┘  └─────┬──────┘  │
│        │              │               │          │
│        ▼              ▼               ▼          │
│  ┌─────────────────────────────────────────┐    │
│  │         Rate Limiter (3 req/s)          │    │
│  │         Retry Handler (3 attempts)      │    │
│  │         Dedup Writer (content-compare)  │    │
│  └─────────────────────────────────────────┘    │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│              Local Data Store (JSON)             │
│                                                 │
│  data/                                          │
│  ├── index/{ptcg,ptcg100,opcg}/                 │
│  ├── cards/{snkrdunk,altxyz}/{id}/              │
│  ├── images/                                    │
│  └── _state/                                    │
└─────────────────────────────────────────────────┘
```

### 3.2 數據傳輸協議

Grade10 平台使用 **tRPC over HTTP** 作為 API 協議，具體規格：

| 項目 | 規格 |
|------|------|
| 傳輸方式 | HTTP GET |
| 端點格式 | `{base}/api/trpc/{router}.{procedure}?batch=1&input={JSON}` |
| 序列化 | devalue（tRPC 標準 transformer） |
| 批量請求 | 支援，以逗號分隔多個 procedure，input 按索引對應 |
| 認證 | 無需認證（公開數據） |
| CORS | 開放 (`Access-Control-Allow-Origin: *`) |
| CDN | Cloudflare |

批量請求示例：
```
GET /api/trpc/price.getAssetInfo,price.getGradingPopulations?batch=1&input=
  {"0":{"json":{"id":146897,"source":"snkrdunk"}},
   "1":{"json":{"id":146897,"source":"snkrdunk"}}}
```

### 3.3 性能優化

| 策略 | 效果 |
|------|------|
| tRPC 批量請求 | 每卡 27 個查詢合併為 3 個 HTTP 請求（減少 89% 請求數） |
| 多線程並發 | 8 個 worker 同時處理不同卡片 |
| 請求限速 | 全域 3 req/s，避免對服務器造成壓力 |
| 內容去重 | 僅在數據變化時寫入檔案，減少磁碟 I/O |
| 指數 batch | 每個指數的 9 個查詢合併為 1 個請求 |

**每日全量同步**: ~1,800 個 HTTP 請求，預計 10-15 分鐘完成。

### 3.4 錯誤處理

| 場景 | 處理方式 |
|------|---------|
| HTTP 429 (速率限制） | 指數退避重試（5s → 10s → 20s） |
| 網絡超時 | 最多 3 次重試，間隔指數遞增 |
| 空數據（卡片無某等級成交） | 跳過寫入，不視為錯誤 |
| 個別卡片失敗 | 記錄錯誤日誌，不影響其他卡片 |

---

## 4. 本地數據結構

### 4.1 目錄佈局

```
data/
├── index/
│   ├── ptcg/
│   │   ├── stats.json              # ATH/ATL/52週高低
│   │   ├── summary.json            # 現值 + 7日% + rebalance
│   │   ├── chart_1W.json           # 最近 1 週每日點位
│   │   ├── chart_1M.json
│   │   ├── chart_3M.json
│   │   ├── chart_6M.json
│   │   ├── chart_YTD.json
│   │   ├── chart_ALL.json          # 完整歷史 (2023-02 至今)
│   │   └── constituents.json       # 500 隻成份卡排名
│   ├── ptcg100/                    # 同上結構
│   └── opcg/                       # 同上結構
│
├── cards/
│   ├── snkrdunk/
│   │   └── {id}/
│   │       ├── asset_info.json
│   │       ├── apparel_grade_-1.json    # All grades
│   │       ├── apparel_grade_22.json    # PSA 10
│   │       ├── apparel_grade_23.json    # PSA 9
│   │       ├── ...                      # 其他等級
│   │       ├── ebay_PSA_10.json
│   │       ├── ebay_PSA_9.json
│   │       ├── ...
│   │       ├── populations.json
│   │       ├── summary_en.json
│   │       └── summary_jp.json
│   └── altxyz/
│       └── {uuid}/
│           └── (同上結構)
│
├── images/
│   ├── snkrdunk_{id}.webp
│   └── altxyz_{uuid}.webp
│
└── _state/
    ├── last_run.json               # 最近運行元數據
    └── scraper.log                 # 運行日誌
```

### 4.2 核心數據格式

**指數統計** (`stats.json`):
```json
{
  "records": {
    "ath": { "indexValue": 284.71, "asOfDate": "2026-06-25" },
    "atl": { "indexValue": 76.03, "asOfDate": "2024-07-06" },
    "high52": 284.71,
    "low52": 145.46,
    "sinceInceptionPct": 178.18
  },
  "lastCalculated": "2026-07-19"
}
```

**成份卡** (`constituents.json`, 每行一隻卡）:
```json
{
  "rows": [{
    "rank": 1,
    "name": "Pikachu With Grey Felt Hat #085",
    "setName": "2023 Scarlet and Violet Black Star Promo",
    "lang": "EN",
    "priceUsd": 2993,
    "change30dPct": -5.32,
    "imageUrl": "https://app.grade10.com/media/card-index/...",
    "weightPct": 2.73,
    "url": "https://app.grade10.com/research/card/snkrdunk/146897"
  }],
  "rebalanceDate": "2026-05-27"
}
```

**成交記錄** (`apparel_grade_22.json`):
```json
{
  "averagePrice": { "price": 3058.45, "currency": "usd" },
  "saleHistory": [
    {
      "date": "18 hours ago",
      "grade": "PSA10",
      "price": 3262.18,
      "txAmount": 3262.18,
      "currency": "usd",
      "bundleSize": 1
    }
  ],
  "sourceLink": "https://snkrdunk.com/en/trading-cards/146897/..."
}
```

**鑑定人口** (`populations.json`):
```json
{
  "population": [
    { "gradeName": "PSA", "total": 114981, "topGrade": 49496 },
    { "gradeName": "BGS", "total": 4104, "topGrade": 1595 },
    { "gradeName": "CGC", "total": 6857, "topGrade": 3674 },
    { "gradeName": "SGC", "total": 311, "topGrade": 123 }
  ],
  "source": "https://www.gemrate.com/universal-search?gemrate_id=..."
}
```

---

## 5. 歷史數據策略

### 5.1 即時可用的歷史深度

| 數據 | 歷史深度 | 說明 |
|------|---------|------|
| 指數每日點位 | **2023-02-27 至今** (1,238 點） | 完整歷史，一次拉取 |
| 成份卡名單 | 當前 + 調倉日標記 | 每次調倉後更新 |
| Snkrdunk 成交 | 最近 ~20 筆/等級 | 滾動窗口，無分頁 |
| eBay 成交 | 最近 ~20 筆/等級 | 滾動窗口，無分頁 |
| AI 研究報告 | 當前版本 | 內容隨平台更新 |
| 鑑定人口 | 當前快照 | 每日更新 |

### 5.2 歷史累積機制

成交記錄採用「滾動窗口」模式 — 平台僅返回最近約 20 筆。本系統通過**每日定時同步**實現歷史數據累積：

```
Day 1: 拉到最近 20 筆 → 存入本地
Day 2: 拉到最新 20 筆（含 Day 1 的部分重疊）→ 本地已有 → 只寫入新增部分
Day 3: 同上
...
Day N: 本地已累積 N 天的完整成交歷史
```

由於每次拉取都包含最近的完整窗口，不會遺漏任何成交。隨著時間推移，本地數據將超越平台的展示深度。

---

## 6. 運維

### 6.1 定時任務

| 項目 | 值 |
|------|---|
| 任務名稱 | `Grade10-Daily-Scraper` |
| 觸發時間 | 每日 06:30（本地時區） |
| 執行命令 | `run_daily.bat` |
| Python | `C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe` |
| 工作目錄 | `C:\Users\jackson0202\Documents\Playground\grade10-scraper` |

### 6.2 手動操作

```powershell
# 完整運行（指數 + 卡片 + 圖片）
python grade10_scraper.py

# 僅指數數據
python grade10_scraper.py --index-only

# 僅卡片數據（跳過圖片）
python grade10_scraper.py --cards-only --skip-images

# 預覽模式（不發卡片請求）
python grade10_scraper.py --dry-run
```

### 6.3 監控

運行狀態存儲於 `data/_state/last_run.json`：
```json
{
  "lastRun": "2026-07-22T07:15:00+00:00",
  "elapsedSeconds": 600,
  "cardCount": 600
}
```

日誌附加於 `data/_state/scraper.log`。

---

## 7. 檔案清單

| 路徑 | 說明 |
|------|------|
| `grade10_scraper.py` | 主爬蟲程式 |
| `grade10_analytics.py` | 升跌% + 成交量分析（見 §8） |
| `grade10_kline.py` | 每日 K 線（OHLCV）生成（見 §8.4） |
| `run_daily.bat` | Task Scheduler 入口腳本（爬蟲 → 指標 → K 線） |
| `data/` | 所有同步數據（JSON + 圖片） |
| `data/analytics/` | 分析輸出（card_metrics / summary / klines/） |
| `data/sales_cache/` | 累積成交歷史（每日合併，只加唔刪） |
| `DATA_GUIDE.md` | 數據運用指南（喺邊、點用、點講） |

### 8.4 每日 K 線（OHLCV）

`grade10_kline.py` 將 `sales_cache` 嘅累積成交聚合成**每卡每日一行**嘅 K 線，輸出至 `data/analytics/klines/`：

```
date, open, high, low, close, tx, volumeUsd, carried
```

- 價格序列：PSA 10（`--grade` 可換）；量：全等級全平台
- `carried=1` = 該日無成交，收盤帶前一日（保證每日都有線）
- 同日內成交先後次序平台唔提供，open/close 係聚合值（價格排序後取首末），唔好當逐筆順序用
- 平台「相對日期」（"2 days ago"）係滾動標籤：同一筆成交舊 fetch 記 ISO 日期、新 fetch 記相對日期。為免同一筆喺唔同爬取之間漂移，最近 42 日窗口內嘅成交以**發現日**（爬取日）入桶；窗口外全部係 ISO，用原日期。呢個係 K 線「今日特別粗」嘅原因 — 首次全量等於一次過吸咗窗口內全部成交，聽日起每日先係真正日線。

---

## 8. 升跌% 與成交量分析

`grade10_analytics.py` 從本地成交記錄計算每隻卡的 **1 日 / 7 日 / 30 日升跌%** 及 **成交量**（筆數 + 美元額），輸出至 `data/analytics/`。每次爬蟲完成後運行：

```powershell
python grade10_analytics.py
```

### 8.1 計算方法

| 指標 | 方法 |
|------|------|
| 升跌% | 同一鑑定等級價格序列：最近 24h 均價 vs 對比窗口均價（1d: 24-48h 前；7d: 6-8 日前；30d: 28-32 日前） |
| 價格序列 | 默認 PSA 10（平台 UI 默認等級）；該卡無 PSA 10 數據時用成交最多的等級 |
| 成交量 | 跨等級、跨平台全部成交筆數及美元總額（1d/7d/30d） |

### 8.2 數據質量規則

原始數據有三個已知陷阱，分析時已處理：

1. **`apparel_grade_-1.json`（All 等級）係各等級檔案嘅重複串流** — 跳過，否則成交量雙計。
2. **eBay 等級查詢係關鍵字過濾，唔係精確匹配** — `ebay_PSA_10.json` 內會混入提及 "PSA 10" 嘅未評級平價成交（實測：$2,500 PSA 10 序列內混入 $190 裸卡）。處理：價格低於該檔案 `averagePrice` 25% 嘅成交歸入 Ungraded（仍計成交量，但唔入價格序列）。
3. **同一「等級」跨平台價格層級可能差 10 倍**（例如 snkrdunk 嘅 PSA 10 檔案實為未評級成交）。處理：兩平台中位數差 >2 倍時，價格序列只用成交較多嘅平台，並喺 `seriesPlatform` 欄目標記。

每隻卡輸出 `seriesGrade` / `seriesPlatform` / `seriesSaleCount`，方便追溯升跌% 係基於邊條序列。

### 8.3 注意事項

- 成交記錄歷史深度受每日累積限制（見 §5.2）— 運行初期 30d 升跌% 覆蓋率較低，累積約 5 週後達完整覆蓋。
- 升跌% 以窗口均價對比（唔係單筆最新成交），減少單筆異常成交造成嘅假訊號。
