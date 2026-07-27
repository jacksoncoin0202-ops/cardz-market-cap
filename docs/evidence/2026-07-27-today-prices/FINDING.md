# FINDING — today-prices (#12) · ebay + snkrdunk 今日採集

## 結論一句

SNKRDUNK 於 JST 2026-07-27 成功 append：`market_price_observation` **56 行**（54 個
variant，`observed_date` 推進到 **2026-07-26**）、`market_source_observation` **9,642 行**；
eBay append **0 行**，因為上游 grade10-scraper 磁碟嘅 `sold_at` 上限係 2026-07-25、
而且 repo 冇任何啟用中嘅 eBay 成交輸入源。

## 量度日期

| 項目 | 值 |
|---|---|
| 量度時刻（機器本地 JST，UTC+09:00） | 2026-07-27 01:12 |
| 同一時刻嘅 UTC | 2026-07-26 16:12 |
| DB session `NOW()` | `2026-07-26 16:12:51`（UTC） |
| 量度窗口起點 `jst_day_start_utc` | `2026-07-26 15:00:00`（= JST 07-27 00:00） |

## 點量（可直接重跑）

```bash
cd "C:\Users\jackson0202\Documents\Playground\cardz-market-cap"
set -a && . data/runtime/config/backend.env && set +a
.venv-backend/Scripts/python.exe -X utf8 docs/evidence/2026-07-27-today-prices/verify_today_prices.py
```

量度腳本：[verify_today_prices.py](verify_today_prices.py)。六條唯讀 query 全部經
`scripts/ro_sql.py` 出（read-only gate 生效，冇用 `information_schema.TABLE_ROWS`，
行數一律 exact `COUNT(*)`）。窗口起點係腳本內嘅常數 `JST_DAY_START_UTC`
= `(TIMESTAMP(DATE(NOW() + INTERVAL 9 HOUR)) - INTERVAL 9 HOUR)`，會跟住當日自動
推移，所以下一日照跑都係啱嘅窗口，唔使改檔。

核心兩條 query（腳本內叫 `price_appended_this_jst_day` / `source_appended_this_jst_day`）：

```sql
SELECT source_code, COUNT(*) AS rows_appended,
       COUNT(DISTINCT variant_id) AS variants,
       MIN(observed_date) AS min_observed_date, MAX(observed_date) AS max_observed_date,
       MIN(created_at) AS first_created_at, MAX(created_at) AS last_created_at
FROM market_price_observation
WHERE created_at >= (TIMESTAMP(DATE(NOW() + INTERVAL 9 HOUR)) - INTERVAL 9 HOUR)
GROUP BY source_code ORDER BY source_code;

SELECT source_code, observation_kind, COUNT(*) AS rows_appended,
       MIN(observed_date) AS min_observed_date, MAX(observed_date) AS max_observed_date,
       MAX(created_at) AS last_created_at
FROM market_source_observation
WHERE created_at >= (TIMESTAMP(DATE(NOW() + INTERVAL 9 HOUR)) - INTERVAL 9 HOUR)
GROUP BY source_code, observation_kind ORDER BY source_code, observation_kind;
```

輸出快照：採集前 [before-import.txt](before-import.txt)、採集後
[after-import.txt](after-import.txt)。eBay 三重封路證據：
[ebay-blocker-probe.txt](ebay-blocker-probe.txt)。

## 結果

### 今日（JST 2026-07-27）append 行數

| 表 | source_code | 行數 | variant | observed_date 範圍 |
|---|---|---|---|---|
| `market_price_observation` | `snk_psa10` | **56** | 54 | 2026-07-25 → **2026-07-26** |
| `market_price_observation` | `ebay` | **0** | — | — |
| `market_price_observation` | `snkrdunk`（legacy） | **0** | — | — |
| `market_source_observation` | `snk_psa10` / `index_constituent` | **7,799** | — | 2026-06-11 → **2026-07-26** |
| `market_source_observation` | `snk_psa10` / `tracked_sales_daily` | **1,843** | — | 2026-06-11 → **2026-07-26** |
| `market_source_observation` | `ebay` | **0** | — | — |

`created_at` 落在 `2026-07-26 16:12:34`–`16:12:35`（UTC）。

### 前後對帳（exact COUNT，兩表總數）

| source_code | 表 | 採集前 | 採集後 | 差 |
|---|---|---|---|---|
| `snk_psa10` | price | 115,036 | **115,092** | +56 |
| `snk_psa10` | source_obs | 277,755 | **287,397** | +9,642 |
| `ebay` | price | 5,476 | 5,476 | 0 |
| `snkrdunk`（legacy） | price | 406 | 406 | 0 |

+9,642 同 `db_runtime.py import` 回報嘅 `{"observations": 9642, "batches": 1,
"replayedBatches": 0}` 一致。

### snk_psa10 價格行按 observed_date（近日）

| observed_date | 採集前 | 採集後 |
|---|---|---|
| 2026-07-23 | 168 | 168 |
| 2026-07-24 | 162 | 162 |
| 2026-07-25 | 128 | **167** |
| 2026-07-26 | 0（冇呢個桶） | **17** |

即係今次同時補齊 07-25 遲公佈嘅成交（+39），亦第一次拎到 07-26 收盤（+17）。
251 個 tracked SNK id 之中得 17 個當日有 k 線點——SNKRDUNK 只在有成交嘅日子出點，
呢個係數據本身嘅性質，唔係採集失敗。

### 各源最新日期（採集後）

| source_code | 表 | 總行數 | max observed_date |
|---|---|---|---|
| `snk_psa10` | price | 115,092 | **2026-07-26** |
| `ebay` | price | 5,476 | 2026-07-25 |
| `snkrdunk` | price | 406 | 2026-07-24 |
| `snk_psa10` | source_obs | 287,397 | 2026-07-26 |
| `gemrate` | source_obs | 8,474 | 2026-07-26 |
| `tag` | source_obs | 530 | 2026-07-26 |
| `g10_analytics` | source_obs | 48,223 | 2026-07-25 |
| `ebay` | source_obs | 393 | 2026-07-24 |
| `snkrdunk` | source_obs | 2,247 | 2026-07-24 |
| `g10_index` | source_obs | 27 | 2026-07-23 |

## 執行咗嘅指令

全部前置 `cd "C:\Users\jackson0202\Documents\Playground\cardz-market-cap"` 同
`set -a && . data/runtime/config/backend.env && set +a`。冇跑 `pipelines/run_daily.py`。

```bash
# 1. SNKRDUNK 採集（251/251 ok, failed 0）
.venv-backend/Scripts/python.exe -X utf8 pipelines/snk_market_data.py \
  --ids-file data/runtime/private-source-map/tracked-snk-ids.txt \
  --condition trading_card_single_psa10 --run-id sources_20260727_optp01 \
  --out data/runtime/private-source-runs/sources_20260727_optp01/snk-psa10.jsonl

# 2. 正規化落 immutable landing（gemrate 用空目錄壓住）
.venv-backend/Scripts/python.exe -X utf8 pipelines/market_source_sync.py \
  --active-universe data/runtime/private-source-map/tracked-universe.json \
  --gemrate-root temp/empty-gemrate \
  --snk-run data/runtime/private-source-runs/sources_20260727_optp01/snk-psa10.jsonl \
  --fx-snapshot data/runtime/private-fx/latest.json \
  --landing-root data/runtime/private-landing --run-id sources_20260727_optp01

# 3. 只 import 呢一個 batch（--landing-root 指向單一 run 目錄）
.venv-backend/Scripts/python.exe -X utf8 pipelines/db_runtime.py import \
  --active-universe data/runtime/private-source-map/tracked-universe.json \
  --landing-root data/runtime/private-landing/sources/sources_20260727_optp01

# 4. eBay：只 dry-run，冇 --write（理由見下面前提第 5 條）
.venv-backend/Scripts/python.exe -X utf8 pipelines/g10_ebay_ingest.py --grades PSA_10
```

採集器回報：`{"ok": 251, "failed": 0, "remaining": 0, "total": 251, "replayed": false,
"run_id": "sources_20260727_optp01", "finished_at": "2026-07-26T16:08:52Z"}`。

## 量度時嘅前提（讀數之前要知）

1. **`observed_date = 2026-07-27` 今晚結構上拎唔到。** `market_source_sync.py` 將
   `observed_date` 封頂在 `effective_at.date()`，而 `effective_at` 預設係
   `datetime.now(timezone.utc)`——即係 2026-07-26。要出 07-27 就要人手餵
   `--effective-at` 造假日期，違反「唔准降 gate」，所以冇做。本報告講嘅「今日」
   一律指 **append 時間** 落在 JST 07-27 這一日，行本身嘅 `observed_date` 最高
   到 2026-07-26。
2. **DB session 時鐘係 UTC，機器係 JST（+09:00）。** 所以 MySQL `CURDATE()` 喺
   JST 每日頭 9 個鐘都落後一日；`CURDATE()` 做窗口會漏數。全部 query 統一用
   `jst_day_start_utc` = `2026-07-26 15:00:00`。
3. **兩張表嘅去重行為唔同，唔可以直接比行數。** `market_price_observation` 有
   UNIQUE `uq_market_price_daily` = (variant_id, source_code, observed_date)，
   所以只有真正新嘅 (variant, 日期) 對才成行，因此得 56；
   `market_source_observation` 冇同款日鍵，每次 run 都會為整段 k 線歷史寫新行，
   所以係 9,642。行數多少唔等於資訊量多少。
4. **今日較早嗰次 snk 已完成嘅 281 行係 JST 07-26 嘅事。** 佢 `created_at` 06:28 UTC
   = JST 15:28 07-26，早過 JST 07-26 收盤，所以當時 `market_price_observation`
   完全冇 07-26 桶、07-25 亦只有 128 行。今次係過咗 JST 午夜再跑，才拎到 07-26 收盤。
   兩次唔重複，亦解釋咗為何 07-25 仲有 +39 行補落嚟。
5. **eBay 刻意冇 `--write`。** 三重確認封路：`CARDZ_EBAY_SOLD_ENABLED` 同
   `CARDZ_EBAY_SOLD_INPUT` 都 unset 令 `ebay_sold_data.py` fail-closed（實測
   exit 1）、`data/private/ebay_brute/` 空、唯一有數據嘅
   `g10_ebay_ingest.py` 讀嘅 G10 磁碟最新 mtime 係 2026-07-25 21:46 UTC 而
   dry-run `sold_at` 上限就係 2026-07-25——同 DB 現有 `ebay` max `observed_date`
   一模一樣。佢個 INSERT 係 `ON DUPLICATE KEY UPDATE` 蓋 `run_id` / `effective_at`，
   跑落去會將 5,420 條舊行改成帶今日 provenance 而一行新數據都冇，直接污染本任務要
   量嘅新鮮度。詳細見 [ebay-blocker-probe.txt](ebay-blocker-probe.txt)。
6. **gemrate 用空目錄壓住。** `--gemrate-root temp/empty-gemrate` 令
   `build_source_observations` 嘅 `if gemrate:` 分支唔行，回報
   `gemrateCards 0 / gemrateObservations 0` 而 `status: ready`。目的：batch 保持
   SNK-only（符合「只准單源採集」），零 GemRate API call，唔燒 key 配額。
7. **import 範圍收窄靠 `--landing-root` 指向單一 run 目錄**，冇複製任何檔。
   `iter_batches` 係 `landing_root.rglob("canonical-batch.json")`；`private-landing`
   下另有 17 個 batch，而 `market_ingest_run` 99 條之中有 23 條 status 係
   `'completed'`（唔係 `'complete'`），短路判斷 `existing["status"] == "complete"`
   對佢哋唔成立，會被重入——所以一定要收窄，唔可以指全個 landing root。
8. **`tracked-snk-ids.txt` 睇落過期但實測冇 drift。** 檔案 mtime（07-25 00:06）
   舊過 `tracked-universe.json`（07-26 00:51），本來會踩到
   `market_source_sync.py` 嘅 fail-closed gate「SNK exact mapped item is missing
   from the completed run」。實測對過：universe 251 個 SNK id vs ids 檔 251 個，
   `missing_from_idsfile 0` / `extra_in_idsfile 0`，所以冇重生成 ids 檔。
9. **冇跑 `pipelines/run_daily.py`**，冇掂 snapshot / publish / 卡圖自愈，冇寫
   `data/public/seed-snapshot.json`，冇改 `apps/web/src/data/boards.json`，冇 commit。

## 未解決 / 下一步選項

- **eBay 今日 0 行係外部阻塞，唔係腳本問題。** 要有新 eBay 日期，順序係：
  (a) 等 grade10-scraper 下一次收成（約 21:17 UTC）跑完，令磁碟出現 07-26/07-27 嘅
  `sold_at`，然後才跑 `g10_ebay_ingest.py --write`；或者
  (b) 配置 `CARDZ_EBAY_SOLD_INPUT` 指向一個 repo 擁有嘅成交輸入，令
  `ebay_sold_data.py` 唔再 fail-closed。兩者都超出本任務範圍，冇做。
- **`market_source_observation` 嘅 legacy `source_code`（`snkrdunk` / `ebay`）冇活
  producer**，兩者最新 `observed_date` 都停在 2026-07-24。現行寫入者係
  `snk_psa10`（SNK 價格）同 `ebay`（由 `g10_ebay_ingest.py` 直寫價格表）。如果
  下游有嘢仍然讀 legacy code，會一直見到 07-24——值得另開一單追，本任務冇改。
