# PROJECT_STATE — CARDZ Market Cap（**唯一必讀**）

> **開工只睇呢一份。** 現況數字 + 務實營運精華（DB × 腳本 × 前端）都合喺度。  
> 最後更新：**2026-07-29**  
> 數字會漂 → 以下面 `status` 命令即場輸出為準；改完覆蓋要更新 §A。

```bash
export CARDZ_DB_HOST=127.0.0.1
cd /mnt/c/Users/jackson0202/Documents/Playground/cardz-market-cap
python3 -X utf8 pipelines/qualified_pool_operator.py status
```

---

# A. 而家做緊乜（活狀態）

| 優先 | 事項 | 狀態 |
|---:|---|---|
| 1 | **FE Live 100%** | ✅ **FE_SET 229 張**（top100+watchlist）價/POP/市值/圖/日史/成交/故事 **全 100%** · snapshot `canonical_live_fe` · 見 [FE_LIVE_100.md](docs/FE_LIVE_100.md) |
| 2 | 價／市值（池） | any_price ~921/940 · 繼續增量 |
| 3 | 圖（池） | asset ~850+ · 上板已齊 |
| 4 | 成交（池） | sale_any ~260+ · 全量未齊 · 增量繼續 |
| 5 | 全池 identity | SNK/eBay mark + registry · harvest 續 |
| 6 | productionEligible 旗 | 仍可能 False（ranked≠published skip）；**唔擋 FE soft live** |
| — | 交接 | [AGENT_HANDOFF_INCREMENTAL.md](docs/AGENT_HANDOFF_INCREMENTAL.md) · [DEPLOY_FOR_HANDOVER.md](docs/DEPLOY_FOR_HANDOVER.md) · [SESSION_RETRO_20260729.md](docs/SESSION_RETRO_20260729.md) |
| — | 召回/QC | [RECALL_VERIFY_OPS.md](docs/RECALL_VERIFY_OPS.md) |

**用戶優先**：價 → 圖 → 成交／流動性 → FE 成套（故事只補上板）→ snapshot。

---

# B. 十條硬真理

1. **池會大**（940→1000+ 正常）。**前端 cut 唔脹**（Top100／分 TCG／Grading 各 ≤100）。  
2. **市值** = PSA10 價 × GemRate PSA10 POP。**禁止** raw TCG 價（Limitless／OP.gg `$`、TCGplayer market）入市值。  
3. **前端只讀 snapshot**：DB → `canonical_public_snapshot` → pointer → web。**唔直連 MySQL**。  
4. **成套填滿只打會上 FE 嘅卡**，唔全池補圖／故事。  
5. **成交有就入晒**（1 日、7 日、成段歷史都入）；**1d/7d/21d/30d 係反推窗**，唔係採集過濾。Top100 閘用 30d 衍生（`market_alerts` liquid 排前）。  
6. **流動性假低**：全庫 sale 很多行但 watchlist join 少；根因 **identity + number 組合盲點**。  
7. **永久 mark 一次 ID**（`catalog_source_identity` + registry + ledger）；有 key 唔好名 first-hit。  
8. **唯一池運維入口**：`qualified_pool_operator.py`（status）；身份全源見 `semi_auto_identity` / `full_volume_recall_verify`。  
9. **圖 A/B/C 一次做足**：asset 行 + 磁碟 webp + `market_image_qc`（+ manifest）。  
10. **每卡記邊個腳本易拎** → `liquidity-source-registry.jsonl`；下次只跑嗰個。  
11. **召回可以極低門檻；入 DB 必須過腳本 Verify／QC**。AI 負責編排，唔好取代硬閘。詳見 [docs/RECALL_VERIFY_OPS.md](docs/RECALL_VERIFY_OPS.md)。

### 硬規則表（R1–R10）

| ID | 規則 |
|---|---|
| R1 | 唯一業務 DB：Docker `127.0.0.1:3308` / `cardz_market_cap` |
| R2 | POP 權威 = GemRate；前端唔顯示負 POP delta |
| R3 | 入池：GemRate PSA10 **POP ≥ 1000** → watchlist |
| R4 | 價：US **TPL SSR**；JP **SNK**；唔申請付費 API key |
| R5 | 圖：TCGplayer 主 → Limitless OP CDN → Drive／OP.gg；A/B/C |
| R6 | printing 獨立 `variant_id`；exact bind fail-closed |
| R7 | 成交：`market_sale_observation` + PSA10 濾；**唔用** daily_sales_aggregate 混 grade ebay |
| R8 | 全量先增量；缺數隱藏，唔假 0 |
| R9 | WSL/`python3` + Docker MySQL；環境 var 要 strip `\r` |
| R10 | 部署 AWS/Node 與 CF 並存；`CARDZ_RUNTIME=node` |

---

# C. 心智模型

```text
                    ┌─ GemRate POP≥1000 ──► watchlist（可 >1000）
供應商 ──pipelines──┤─ 價 PSA10 (SNK > TPL > Fish) ──► market_price_observation
                    ├─ 成交 (SNK trades > eBay/PC) ──► market_sale_observation
                    └─ 圖 (TCG / Limitless OP / Drive) ──► asset+file+QC
                                      │
                         市值=價×POP；流動性=30d sales>0
                                      │
                    FE cut（Top100 禁低流動）→ snapshot → 前端
```

| 層 | 入口 |
|---|---|
| 前端 | `apps/web` · 只讀 snapshot |
| 出街 | `pipelines/canonical_public_snapshot.py` |
| DB | Docker `:3308` |
| 池運維 | `pipelines/qualified_pool_operator.py` |
| 永久 id | `catalog_source_identity` + `data/runtime/private-source-map/` |

**價優先**：SNK（exact）> TPL > TCGFish/eBay 輔。  
**成交優先**：SNK trades 最易 → G10 sales_cache 本地快 → PC/eBay map 慢。

---

# D. 覆蓋水位（改完要更新）

| 維度 | 約數 | 解讀 |
|---|---:|---|
| watchlist | **940** | 會再升 |
| 有 PSA10 價 | **918** | TPL 795 / SNK 145 / eBay 109（live 2026-07-28T18:07Z） |
| TPL map / harvest 檔 | **829 / 785** | map 940 行 |
| 有圖 asset / ptr | **851 / 852** | 尾數 Drive／Limitless |
| SNK id | **132** | multi-form bind 後（+73 新 exact） |
| eBay id | **63** | G10 altxyz 仍要擴 identity |
| sale 1d / 7d / 21d / 30d / any | **50 / 154 / 180 / 182 / 185** | 全歷史入庫後反推 |
| 無價 | **~22** | 唔上排名 FE |

---

# E. 邊個源易拎（時間 × 用途）— 精華

| 用途 | 最易源 | 預估 | 腳本 |
|---|---|---|---|
| POP／入池 | GemRate | 日更 | `gemrate_*` / watchlist task |
| US PSA10 價+短史 | **TPL SSR** | 數十分～鐘（可並行） | operator map/harvest/ingest |
| JP 價+長 K+**成交** | **SNK** | ~100 id：5–15 分；ingest &lt;1 分 | `snk_market_data` → **`ingest_snk_trades_sales`** |
| 流動性歷史 | **G10 sales_cache 本地** | 2–10 分 | `g10_sales_cache_ingest --platforms snkrdunk` |
| US 成交 | PC→eBay | 較慢 | `pricecharting_ebay_export`；eBay 直爬 sold **PX 擋** |
| 價尾巴 | TCGFish PSA10 | 中 | `us_price_fallback` / fill_no_price |
| OP 圖 | **Limitless CDN** | 秒～分 | `op_limitless_images.py` · pattern `.../one-piece/{SET}/{SET}-{NUM}_EN.webp` |
| 圖一般 | TCGplayer | 中 | fill-images / ensure_image_abc |
| 圖死角 | Drive×3 + OP.gg | 半自動 | **§F 寶藏庫** |
| Limitless/OP.gg `$` | — | — | **raw → 禁止入 PSA10 市值** |

### 全量次序（認同）

```text
1) SNK bind → pull → trades 入 sale 表     ← 流動性主菜
2) G10 sales_cache 本地                     ← 快、補史
3) TPL 價全量
4) 圖：fill-images + Limitless OP + Drive
5) eBay/PC 只打高市值仍無成交

之後每日：registry preferred 源；TPL incremental；SNK 已 bind only
```

### 時間預算

| 工作 | 粗估 |
|---|---|
| SNK ~100 id live | 5–15 分 |
| trades→DB | &lt;1 分 |
| G10 ~480 檔 | 2–10 分（通常快過 live API） |
| TPL 200 full | 10–40 分 |
| 圖 200 | 10–30 分 |
| PC eBay 100 | 30 分–數小時 |

**易先難後**：SNK 成交 → G10 → TPL 價 → 圖 → PC/eBay。

---

# F. 搵圖寶藏庫（欽點 · 補圖必備）

| # | 連結 | 用途 |
|---|---|---|
| 1 | https://drive.google.com/drive/folders/12Y9o5_LzXtAry6tw042j7Fgk4eyyrmfj | Drive 圖庫 |
| 2 | https://drive.google.com/drive/u/0/folders/1w18aBFle3uMOSiD78B5O1sxTn6WI1hVq | Drive 圖庫 |
| 3 | https://drive.google.com/drive/folders/13HwWKRkiwZTarPbH4W0A2WhYqYkDWyxr | Drive 圖庫 |
| 4 | https://onepiece.limitlesstcg.com/cards | OP 圖+編號（價=raw 唔入市值） |
| 5 | https://onepiece.gg/cards/ | OP 圖 |

`no_image_source` 時 OP 先 #4/#5，再 Drive——唔好當冇圖放棄。

---

# G. 現役腳本（只記呢啲）

| 腳本 | 一句 |
|---|---|
| `pipelines/qualified_pool_operator.py` | status / TPL map·harvest·ingest / fill-images |
| `pipelines/build_identity_registry.py` | 重生 identity（**map/bind 後必跑**） |
| `pipelines/bind_snk_watchlist.py bind --write` | SNK exact bind |
| `pipelines/snk_market_data.py` | SNK 價+trades harvest |
| **`pipelines/ingest_snk_trades_sales.py`** | **trades → sale 表（流動性關鍵）** |
| `pipelines/g10_sales_cache_ingest.py` | 本地成交史（要 strip 密碼 `\r`、顯式 --host） |
| `pipelines/op_limitless_images.py` | OP 圖 CDN + mark `op_limitless` |
| `pipelines/ensure_image_abc.py` | 圖 A/B/C |
| `pipelines/canonical_public_snapshot.py` | 出街 |
| `temp/fe_liquidity_gap_report.py` | FE 缺口+低流動表 |
| `temp/run_liquidity_full.py` | SNK+ingest+G10 串燒 |

**Legacy 唔當主線**：`ebay_brute_harvest`（sold PX）、亂 `temp/*`。  
**70+ pipelines 唔使掃晒**——跟呢表。

---

# H. 永久 ID／Lookup 產物

| 檔 | 用途 |
|---|---|
| `data/runtime/private-source-map/qualified-940-identity.jsonl` (+csv) | 每卡 id+link+lookup |
| `…/tpl-slug-map.jsonl` | TPL slug |
| `…/snk-psa10-*.jsonl` | SNK harvest（含 recent_trades） |
| **`…/liquidity-source-registry.jsonl`** | **邊張卡用邊個流動性腳本** |
| `…/qualified-940-snk-ids.txt` | SNK id 列表 |

流動性 registry 例：

```json
{"variantId": 123, "preferredLiquiditySource": "snkrdunk", "snkItemId": 455596,
 "script": "pipelines/ingest_snk_trades_sales.py", "tradesIngested": 20}
```

下次只跑 preferred。

---

# I. DB 要唔要整合 column？

**而家唔做大 flatten。**

| 要 | 唔要 |
|---|---|
| `catalog_source_identity` 多源 id | 所有價塞入 variant 一行 |
| observation append-only + source_code | 前端直 join fact 海 |
| 中長期 read model | Limitless raw 蓋 PSA10 |

讀流動性用 **`sold_at`**，唔用 `created_at`；PSA10 濾 grade。

---

# J. 前端收尾

| 規則 | 做法 |
|---|---|
| Top100 市值 | 有市值 **且** 30d PSA10 成交 &gt; 0 |
| 低流動 | 禁 Top100（sale 接好先閘） |
| Grading | 每家 **≤100** |
| 成套 | 價·POP·市值·圖·史·故事(production)·成交 partial |
| 故事 | 只補 FE 名單 |

細表（可選深潛，**非必讀**）：`docs/FRONTEND_REQUIREMENT_CHECKLIST.md` · `docs/FE_READINESS_GAP_TABLE.md`

**流動性假低詳情**：早期 940∩sale ~95；SNK trades 入表後 ~149。全庫 12 萬行——未 join 先。

---

# K. 全量 vs 增量

| | |
|---|---|
| **而家** | 先全量：SNK 成交 ✓ · 繼續 G10/TPL/圖 |
| **之後** | TPL incremental；SNK 已 bind only；新卡 bind→全量一次→registry |
| **未全量開日更** | 禁止（缺口永久化） |

---

# L. 命令作弊紙（copy 即用）

```bash
export CARDZ_DB_HOST=127.0.0.1
cd /mnt/c/Users/jackson0202/Documents/Playground/cardz-market-cap

python3 -X utf8 pipelines/qualified_pool_operator.py status

# 價 TPL
python3 -X utf8 pipelines/qualified_pool_operator.py map-tpl --workers 10
python3 -X utf8 pipelines/qualified_pool_operator.py harvest-tpl --mode full --workers 8
python3 -X utf8 pipelines/qualified_pool_operator.py ingest-prices
python3 -X utf8 pipelines/build_identity_registry.py

# 流動性 SNK（最易）
python3 -X utf8 pipelines/bind_snk_watchlist.py bind --write
python3 -X utf8 pipelines/snk_market_data.py --ids-file data/runtime/private-source-map/qualified-940-snk-ids.txt \
  --condition trading_card_single_psa10 --out data/runtime/private-source-map/snk-psa10-liquidity-full.jsonl --delay 0.3
python3 -X utf8 pipelines/ingest_snk_trades_sales.py --harvest data/runtime/private-source-map/snk-psa10-liquidity-full.jsonl

# 流動性 G10 本地（快）— password 要無 \r
python3 -X utf8 pipelines/g10_sales_cache_ingest.py --write --platforms snkrdunk \
  --g10-root /mnt/c/Users/jackson0202/Documents/Playground/grade10-scraper/data/sales_cache \
  --host 127.0.0.1 --port 3308 --database "$CARDZ_DB_NAME" --user "$CARDZ_DB_USER" --password "$CARDZ_DB_PASSWORD"

# 圖
python3 -X utf8 pipelines/qualified_pool_operator.py fill-images --write
python3 -X utf8 pipelines/ensure_image_abc.py --write --refetch-missing
python3 -X utf8 pipelines/op_limitless_images.py --write --only-missing

# FE 缺口
python3 -X utf8 temp/fe_liquidity_gap_report.py

# 串燒流動性
python3 -X utf8 temp/run_liquidity_full.py
```

---

# M. 地雷／誤判（踩過）

1. 量表用 exact `COUNT(*)`，唔好 `TABLE_ROWS`。  
2. `market_index_snapshot` 每次三行（combined/pokemon/op）——唔好 `MAX(id)`。  
3. eBay PSA10：sale_observation + grade 濾；唔好 daily_sales 混 grade。  
4. 借窗 Δ：1d→7d→30d。  
5. TPL bind：名+卡號；fail-closed。  
6. TAG Δ 永遠可 unavailable。  
7. 「只有 98 張有流動性」= **未接線**，唔係市場。  
8. Limitless `$` = raw，唔入市值。  
9. fill-images ≠ QC 完（要 A/B/C）。  
10. identity CSV map 後唔 rebuild 會過期。  
11. `semantic_match_status` 用 `meta_unreviewed`（varchar 24；長字串會 1406）。  
12. Windows env `\r` → MySQL Access denied。  
13. WSL 系統 MySQL ≠ CARDZ；只用 Docker 3308。

---

# N. 項目地圖 + 逆向「點做」（分支多時睇）

腳本／逆向分支多 → **地圖一份**：

→ **[`docs/PROJECT_MAP.md`](docs/PROJECT_MAP.md)**

入面有：A–H 分支（GemRate／TPL／SNK／PC-eBay／圖／Fish／出街／前端）+ **每源直接點做**（命令＋URL），少方法論。  
舊長文（`SNKRDUNK_API_MANUAL` 等）當參考書，日常跟 MAP 嘅「點做」。

| 其他可選 | 何時開 |
|---|---|
| `docs/FRONTEND_REQUIREMENT_CHECKLIST.md` | 改 FE 欄位 |
| `docs/ROADMAP.md` | 短中長 |
| `config/data-routing.json` | 改架構所有權 |
| `docs/evidence/*` | 考古 |

**營運仍只維護本 STATE；地圖專門梳分支／逆向。**

---

# O. 收工 checklist

1. 跑 `status`，更新 **§A / §D** 數字。  
2. 新硬規則 → **§B**。  
3. 新「邊源易拎」→ **§E** + `liquidity-source-registry.jsonl`。  
4. map/bind 後 → `build_identity_registry.py`。  
5. **唔好** session 流水帳堆入本檔。  

---

# P. 變更

| 日期 | 內容 |
|---|---|
| 2026-07-29 | 合併 PRACTICAL_OPS 入本檔；**單一必讀** |
| 2026-07-29 | SNK trades 入 sale；流動性假低診斷；Limitless 圖 CDN；FE/低流動規則 |
