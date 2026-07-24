# Grade10 數據操作手冊

> 版本： 1.0 | 日期： 2026-07-23 | 對象： AI agent / 人類操作者
>
> **呢本係操作手冊：講「邊啲打邊啲、點樣做」。**
> 技術細節（API 規格、協議）→ `TECHNICAL_DOC.md`
> 業務運用（點分析、點對外講）→ `DATA_GUIDE.md`

---

## 0. 一句講晒

```
grade10_scraper.py  →  grade10_analytics.py  →  grade10_kline.py
（拉數據落嚟）         （計升跌% 成交量）        （出每日 K 線）
```

每日 06:30 由 Windows Task Scheduler（`Grade10-Daily-Scraper`）自動行 `run_daily.bat`，即係順序行晒上面三步。
**正常情況你唔使郁 — 淨係開 `data/` 入面嘅產物用。**

---

## 1. 環境（改任何嘢之前必讀）

| 項目 | 值 |
|------|---|
| 工作目錄 | `C:\Users\jackson0202\Documents\Playground\grade10-scraper` |
| Python | `C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe` |
| 平台 | **Windows，唔係 WSL**（WSL 會食錯 manifest 入面嘅 backslash 路徑） |
| 排程 | Windows Task Scheduler `Grade10-Daily-Scraper`，每日 06:30 |
| 排程入口 | `run_daily.bat` |
| 日誌 | `data\_state\scraper.log` |
| 狀態 | `data\_state\last_run.json`（最後跑完時間、耗時、卡數） |

依賴：標準庫為主，`requests` + `pillow`（圖片轉 WebP 用）。冇虛擬環境，直接用系統 Python。

---

## 2. 三隻腳本：邊個打邊個

### 2.1 `grade10_scraper.py` — 拉數據

由 `index.grade10.com` + `app.grade10.com` 嘅公開 tRPC API 拉三樣嘢：指數、每卡詳情、卡圖。

```powershell
cd C:\Users\jackson0202\Documents\Playground\grade10-scraper
set PY=C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe

%PY% grade10_scraper.py                    # 全量：指數 + 卡 + 圖
%PY% grade10_scraper.py --index-only       # 淨指數（快，1 分鐘內）
%PY% grade10_scraper.py --cards-only       # 淨卡詳情
%PY% grade10_scraper.py --skip-images      # 跳過圖（排程就係咁行）
%PY% grade10_scraper.py --dry-run          # 數目檢查，唔發卡請求
```

- 全量約 1,800 requests，3 req/s 限速 + 8 worker，**10–25 分鐘**
- 內容冇變就唔覆寫（去重），所以日跑磁碟 I/O 好細
- 失敗某幾隻卡唔會影響其他 — 錯誤入 log，下次跑補返

### 2.2 `grade10_analytics.py` — 計指標

食 `data/cards/` + `data/sales_cache/`，出 1d/7d/30d 升跌% 同成交量。**同時負責維護 `sales_cache`（累積成交歷史，只加唔刪）** — 所以呢步唔好 skip，skip 咗歷史就斷。

```powershell
%PY% grade10_analytics.py                  # 全量 → JSON + CSV + summary
%PY% grade10_analytics.py --top 20         # 控制台印成交量頭 20
```

### 2.3 `grade10_kline.py` — 出 K 線

食 `sales_cache`，每卡每日一行 OHLCV。

```powershell
%PY% grade10_kline.py                      # 全部卡，PSA 10 價格序列
%PY% grade10_kline.py --grade "PSA 9"      # 換等級
```

### 2.4 順序同依賴

| 腳本 | 依賴 | 產出 | 可獨立行？ |
|------|------|------|-----------|
| scraper | 網絡 | `data/cards/`、`data/index/`、`data/images/` | ✅ |
| analytics | scraper 嘅 `data/cards/` | `data/analytics/`、**`data/sales_cache/`** | ✅（用已有 raw data） |
| kline | analytics 嘅 `sales_cache` | `data/analytics/klines/` | ✅（用已有 cache） |

改咗 scraper → 三個順序重行。改咗 analytics → 行 analytics + kline。改咗 kline → 淨行 kline。

---

## 3. 數據喺邊（揾嘢用）

```
data/
├── index/{ptcg,ptcg100,opcg}/       指數（ptcg=PTCG500, ptcg100, opcg=One Piece）
│   ├── stats.json                   ATH/ATL/52週高低/成立以來回報
│   ├── summary.json                 現值、7日%、下次調倉日
│   ├── chart_ALL.json               ★ 完整歷史日線（2023-02 至今）
│   ├── chart_{1W,1M,3M,6M,YTD}.json 其他時間窗
│   └── constituents.json            ★ 成份卡排名（名、價、30日%、權重、圖、卡 URL）
│
├── cards/{source}/{id}/             每卡原始檔（每日覆寫最新窗口）
│   │                                source = snkrdunk（442 卡，數字 id）
│   │                                或 altxyz（158 卡，UUID id）
│   ├── asset_info.json              卡名、系列、卡號、年份、語言、圖 URL
│   ├── apparel_grade_{N}.json       snkrdunk 成交（N=-1..32，22=PSA10）
│   ├── ebay_{Grade}.json            eBay 成交（如 ebay_PSA_10.json）
│   ├── populations.json             PSA/BGS/CGC/SGC 鑑定人口
│   └── summary_{en,jp}.json         ★ AI 研究報告（小作文：背景/社群/趣事/TLDR）
│
├── sales_cache/{source}/{id}.json   ★ 累積成交歷史（analytics 維護，只加唔刪）
├── images/{source}_{id}.webp        卡圖
│
├── analytics/
│   ├── card_metrics.csv             ★★ 600 卡一覽（Excel 直接開，日常主用）
│   ├── card_metrics.json            同上，程式用
│   ├── summary.json                 排行榜（7d 升/跌/量王）
│   └── klines/
│       ├── {source}_{id}_PSA_10.csv ★ 每卡逐日 OHLCV（595 個檔）
│       └── index.json               卡 → K 線檔對照
│
└── _state/
    ├── last_run.json                最後跑完狀態
    └── scraper.log                  日誌（查錯先睇呢度）
```

★★ = 平時開最多；★ = 次常用。

### 等級編碼（apparel_grade_N）

`-1`=全部混合、`20-21`=裸卡 C/D、`22`=PSA10、`23`=PSA9、`24`=PSA8↓、`25`=BGS BL、`26`=BGS10、`27`=BGS9.5、`28`=BGS9↓、`29`=ARS10+、`30`=ARS10、`31`=ARS9、`32`=ARS8↓

### 揾某隻卡點做

卡 ID 唔使記 — 由 `constituents.json` 嘅 `url` 欄攞：
`https://app.grade10.com/research/card/snkrdunk/146897` → source=`snkrdunk`，id=`146897` → `data/cards/snkrdunk/146897/`。

```powershell
# 例：按卡名搵目錄
%PY% -c "import json; d=json.load(open('data/index/ptcg/constituents.json',encoding='utf-8')); [print(r['name'],r['url']) for r in d['rows'] if 'Charizard' in r['name']]"
```

---

## 4. 常見操作（逐個 task 話你知打咩）

### 4.1 手動全量重跑

```powershell
cd C:\Users\jackson0202\Documents\Playground\grade10-scraper
C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe grade10_scraper.py
C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe grade10_analytics.py
C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe grade10_kline.py
```

### 4.2 睇今日市況

開 `data/analytics/card_metrics.csv`（Excel/Sheets 直接開）。
關鍵欄：`latestPrice`、`change7dPct`、`volume7dUsd`、`seriesGrade`（升跌% 基於邊條序列）、`seriesSaleCount`（樣本數，<10 嘅升跌% 當參考）。

### 4.3 睇某隻卡嘅價錢明細同小作文

```powershell
# 以 snkrdunk/146897 為例 — PSA10 逐筆成交
type data\cards\snkrdunk\146897\apparel_grade_22.json
# AI 研究報告（小作文）
type data\cards\snkrdunk\146897\summary_en.json
# 日文版
type data\cards\snkrdunk\146897\summary_jp.json
```

### 4.4 出 PSA 10 以外嘅 K 線

```powershell
%PY% grade10_kline.py --grade "PSA 9"
```
產出喺 `data/analytics/klines/*_PSA_9.csv`，唔會覆寫 PSA 10 嘅。

### 4.5 查排程有冇行

```powershell
type data\_state\last_run.json
# 或睇 Task Scheduler
schtasks /query /tn "Grade10-Daily-Scraper" /v /fo list | findstr /i "Status Last"
```

### 4.6 排程冇行／數據冇更新

1. `type data\_state\scraper.log` — 睇最後嘅 ERROR
2. 手動行 `run_daily.bat` 重現
3. HTTP 429（限速）→ 佢自己會退避重試，log 見到就唔使理；連續全卡 429 先檢查網絡/Cloudflare 有冇擋
4. 個別卡失敗 → 唔使理，下次跑會補

---

## 5. 數據三個陷阱（改分析代碼前必讀）

呢三個係實測中伏位，analytics/kline 已經處理咗 — **你如果寫新嘢食 raw data，自己要處理返**：

1. **`apparel_grade_-1.json`（All 等級）係重複串流** — 係其他等級檔案嘅合體。計量時**一定要跳過**，否則成交量雙計。
2. **eBay 等級查詢係 keyword filter，唔係精確匹配** — `ebay_PSA_10.json` 會混入提及 "PSA 10" 嘅平價裸卡。過濾法：價低於該檔 `averagePrice` × 25% 嘅歸入 Ungraded。
3. **同「等級」跨平台可以差 10 倍**（snkrdunk 嘅 PSA10 檔實為未評級）。兩平台中位數差 >2 倍時，價格序列淨用成交多嗰個平台（metrics 入面 `seriesPlatform` 有標記）。

---

## 6. 已知限制

1. **成交歷史係累積緊**：平台只回最近 ~20 筆，`sales_cache` 由 2026-07-22 開始每日累積。越行越長，唔會蝕。
2. **K 線「今日」會粗**：平台相對日期（"2 days ago"）係滾動標籤，42 日窗口內嘅成交以發現日入桶 — 所以單日 K 嘅 open/close 係聚合值，**唔好當逐筆順序用**。
3. **1d 升跌% 覆蓋率**：需要兩日嘅平台日平均價，新卡會空。
4. **指數歷史完整**（2023-02 至今）— 呢部分冇限制。

---

## 7. 對外口徑（一句版）

「每日自動同步 Snkrdunk + eBay 公開成交數據（經 Grade10 公開指數服務），自建升跌指標同 K 線。」
**唔好用**「逆向」「破解」呢啲字 — 全部係公開免登入接口。

---

## 8. 檔案速查

| 檔案 | 用途 | 改完要重行咩 |
|------|------|-------------|
| `grade10_scraper.py` | 拉數據 | scraper → analytics → kline |
| `grade10_analytics.py` | 升跌% + 成交量 + 維護 sales_cache | analytics → kline |
| `grade10_kline.py` | 每日 OHLCV | kline |
| `run_daily.bat` | 排程入口（順序行三隻） | — |
| `TECHNICAL_DOC.md` | API/協議/儲存規格 | — |
| `DATA_GUIDE.md` | 業務運用 + 對外口徑 | — |
| `OPERATOR_GUIDE.md` | 呢本 — 操作手冊 | — |
