# E6 — 價軌窮盡（price depth）

> 跑次時間（UTC，來自腳本輸出）：**2026-07-28T22:29:45Z → 2026-07-28T22:32:54Z**  
> 環境：Windows Python · `data/runtime/config/backend.env` · MySQL `127.0.0.1:3308`  
> 原則：**唔 invent 假 today stamp**；只寫真源 harvest / ledger bridge 帶返嚟嘅 `observed_date`。

---

## 1. 一句結果

**any_price 已滿池 940/940（前後不變）**；呢次主要加深 **TPL 日史 + g10_kline 補洞**，唔係拓無價卡面。  
TPL `ingest-prices` 寫入 **41,093** 個價點（run_id **228**）；g10_kline bridge **+45** 新行（run_id **229**，表內 `g10_kline` **4206 → 4251**）。  
`fill_no_price` dry：**noPrice=0** → **唔跑 write**。`us_price_fallback` 只係單卡 resolve CLI，無池級 bulk `--write`。

---

## 2. status 前後（`qualified_pool_operator status`）

| 指標 | BEFORE `2026-07-28T22:29:45Z` | AFTER `2026-07-28T22:32:54Z` | Δ |
|---|---:|---:|---:|
| watch | 940 | 940 | 0 |
| **any_price** | **940** | **940** | **0** |
| snk_price | 223 | 223 | 0 |
| snk_family_price | 294 | 300 | +6 |
| ebay_price | 137 | 137 | 0 |
| **tpl_price** | **807** | **807** | **0** |
| snk_id | 288 | 303 | +15（他軌並行，非本次價鏈） |
| sale_any | 305 | 305 | 0 |
| **tplMapped** | **840** | **840** | **0** |
| tplMapRows | 940 | 940 | 0 |
| **tplHarvestFiles**（`cards/*.json`） | **810** | **810** | 0（in-place refresh） |

解讀：

- 卡面 any_price / tpl_price **未擴**（100 張仍無可靠 TPL slug；map 重跑後仍 **needsReview=100**）。
- 深度喺 **observation 點數** 同 **g10_kline 行**，唔喺「有冇一價」KPI。

---

## 3. 執行鏈（按指令）

### 3.1 `map-tpl --workers 10`

```text
[map] resume_keep=832 todo=100 workers=10
… progress 100/100 checkpoint_mapped=840
{"mapped": 840, "needsReview": 100, "rows": 940, "workers": 10}
```

- 出口 0。  
- **840/940** slug；**100** 張仍 map 唔穩（catalog search 無可靠 hit）——唔 invent slug。

### 3.2 `harvest-tpl --mode incremental --workers 8`

```text
[tpl-ssr] 795/795 ok=795 … last=… hist≈173
```

- 出口 0。  
- **795** slug 增量 SSR 全 ok（mapped 840 入面有檔可 harvest 嘅子集／fresh 集合）。  
- 檔數 status 仍 **810**（既有檔 in-place 更新；非 0→N 新增）。

### 3.3 `ingest-prices`

```json
{"cards": 799, "pricePointsWritten": 41093, "runId": 228, "runKey": "tpl_ssr_20260728T223146Z"}
```

- 出口 0。  
- `market_price_observation` source=`tcgpricelookup`（池內）：**n≈41104 / variants=799**  
- 日期窗（池內 TPL）：**min 2025-07-28 · max 2026-07-29**（**max 來自 harvest 內嵌 hist 日期**，非本腳本伪造 today）。

TPL 近端日期分佈（節錄，真 DB）：

| observed_date | points |
|---|---:|
| 2026-07-29 | 13 |
| 2026-07-20 | 9 |
| 2026-07-19 | 42 |
| …（更早日史 bulk） | … |

> **07-29 只有 13 點**——若 SSR 未帶「今日」bar 就唔會有假 stamp；本次 **無** 人工填 today。

### 3.4 `g10_kline_price_bridge --write`

（需 load `backend.env`：`CARDZ_DB_PASSWORD`；首次未 load 會 `RuntimeError`，reload 後成功。）

| 項 | 值 |
|---|---:|
| ledger 讀入 | 47582 |
| carried 跳過 | 43225 |
| identity 對唔到（計次） | 454（1 實體類） |
| 無效 close | 0 |
| 候選 accepted | 3903 |
| 寫前 `g10_kline` 行 | 4206 |
| **新 INSERT** | **45**（INSERT IGNORE；餘已存在） |
| 寫後 `g10_kline` 行 | **4251** |
| run_id | 229 |

規則重申：只 `carried=0`；`source_priority=300` 補洞唔搶 SNK/TPL。

### 3.5 `us_price_fallback` / `fill_no_price`

| 腳本 | 角色 | 本次 |
|---|---|---|
| [`pipelines/us_price_fallback.py`](../pipelines/us_price_fallback.py) | 單卡 TPL→TCGFish→Collectr resolve；**無** pool bulk / `--write` DB 入庫 | 跳過（唔係池運維入口） |
| [`temp/fill_no_price_psa10.py`](../temp/fill_no_price_psa10.py) | 無價尾 remap + fish + ingest；**無 dry flag**，有價就直接 write | **dry probe：`noPrice=0`** → **唔執行 write** |

```text
{'noPrice': 0, 'sample': []}
```

---

## 4. 深度補充量度（AFTER probe `temp/e6_price_depth_probe.json`）

> `probedAt`: **2026-07-28T22:32:54Z** · `maxObservedDate`（全表）= **2026-07-29**（資料源日期，非本機 wall-clock invent）

### 4.1 池面 source 覆蓋（watchlist EXISTS）

| source | 有價卡數 |
|---|---:|
| any | 940 |
| tcgpricelookup (tpl) | 807 |
| snk 系（status snk_price） | 223 |
| g10_kline | 144 |
| ebay | 137 |
| tcgfish | 68 |

### 4.2 observation 點數（watchlist 內）

| source_code | n | variants | min_d | max_d |
|---|---:|---:|---|---|
| tcgpricelookup | 41104 | 799 | 2025-07-28 | 2026-07-29 |
| snk_psa10 | 26696 | 221 | 2023-06-21 | 2026-07-29 |
| ebay | 1565 | 133 | 2026-04-25 | 2026-07-27 |
| g10_kline | 656* | 138 | 2025-02-06 | 2026-07-25 |
| snkrdunk | 107 | 29 | 2026-05-03 | 2026-07-27 |
| tcgfish | 68 | 68 | 2026-07-29 | 2026-07-29 |

\* 池 join 後 656；全表 `g10_kline` 行數 **4251**（含非 watchlist / 歷史累積）。

### 4.3 30d 窗（相對全表 `MAX(observed_date)`，唔用假 today）

| 指標 | 值 |
|---|---:|
| 近 30d ≥1 bar | **457** / 940 |
| 近 30d ≥5 bars | **258** / 940 |
| 任意歷史 ≥1 bar | 932* |
| avg bars（30d） | ~5.3 |
| avg bars（全史） | ~76.1 |
| 近 30d 完全無 bar | **475** |

\* status `any_price=940` 與「join 計 bar」932 可能差喺 filter / 重複鍵語意；以 **status any_price=940** 做運維 KPI。

用戶 KPI（PROJECT_STATE）：**近 30 日有 bar 就夠 score／升跌**；而家 30d 覆蓋 **457/940 ~48.6%** —— **any_price 滿池 ≠ 30d 深**。

---

## 5. 未解 / 下一步（真缺口，唔 invent）

| 缺口 | 規模 | 建議（真源） |
|---|---:|---|
| TPL slug 未 map | **100** | 人工／set-aware 搜；失敗就 document `no_source`，**唔造 slug** |
| TPL 有 map 但 status `tpl_price` 807 vs harvest 799 卡寫入 | ~8–40 | 查 slug 無 PSA10 hist／ingest skip；可 targeted re-harvest full |
| 30d 無 bar | **475** | SNK kline／trades bind；G10 identity 擴後重跑 bridge；TPL 等源有新 hist |
| g10 identity 未對 | 1 實體類 / 454 次 | 擴 `catalog_source_identity` 後重跑 bridge |
| fill 尾 | 0 無價 | 暫唔需要 `fill_no_price`；**應收編** operator 防 temp 腐爛 |

**唔做：** 用 last hist 複製成「今日」抬 30d KPI（FILL_LOOP_LESSONS 已禁）。

---

## 6. 產物路徑

| 檔 | 用途 |
|---|---|
| [`docs/E6_price_depth.md`](E6_price_depth.md) | 本報告 |
| [`temp/e6_price_depth_run.log`](../temp/e6_price_depth_run.log) | 命令 stdout 串 |
| [`temp/e6_price_depth_probe.json`](../temp/e6_price_depth_probe.json) | 深度 probe 原始 JSON |
| ingest run **228** | `tpl_ssr_20260728T223146Z` · 41093 points |
| ingest run **229** | `g10_kline` · +45 inserts |

---

## 7. 可重跑命令

```powershell
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap
# load backend.env → env:CARDZ_DB_*
$env:CARDZ_DB_HOST = "127.0.0.1"
python -X utf8 pipelines\qualified_pool_operator.py status
python -X utf8 pipelines\qualified_pool_operator.py map-tpl --workers 10
python -X utf8 pipelines\qualified_pool_operator.py harvest-tpl --mode incremental --workers 8
python -X utf8 pipelines\qualified_pool_operator.py ingest-prices
python -X utf8 pipelines\g10_kline_price_bridge.py --write
# only if noPrice>0:
# python -X utf8 temp\fill_no_price_psa10.py
python -X utf8 pipelines\qualified_pool_operator.py status
```
