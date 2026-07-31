# 940 合格池維護手冊（簡）

**環境 standard**：WSL / Linux `python3`（`PROJECT_STATE` D7）。  
**唔申請**第三方 API key。

**Agent 總索引（現役 vs 舊腳本）**：一定要讀 [`AGENT_PIPELINE_INDEX.md`](AGENT_PIPELINE_INDEX.md)。  
**各源點搵卡／點打 call**：一定要讀 [`SOURCE_LOOKUP_METHODS.md`](SOURCE_LOOKUP_METHODS.md)。

### POP 係 GemRate，唔係 TPL

| 欄位 | 邊個負責 |
|---|---|
| `psa10Population` / 入池 ≥1000 | **GemRate**（`gemrateId`） |
| US eBay 衍生價 | TCGPriceLookup（`tplSlug`） |
| 日文榜價 | SNK（`snkItemId`） |

TPL **冇**取代 GemRate POP。對照表一定有 `gemrateId` + `linkGemrateSearch` + `populationAuthority=gemrate`。

---

## 你只需要記兩樣

### 1. 身份對照表（維護中心）

| 檔 | 用途 |
|---|---|
| [`data/runtime/private-source-map/qualified-940-identity.csv`](../data/runtime/private-source-map/qualified-940-identity.csv) | Excel 可開；有 website links |
| [`data/runtime/private-source-map/qualified-940-identity.jsonl`](../data/runtime/private-source-map/qualified-940-identity.jsonl) | 腳本讀 |

每行 = 一張印刷（printing），欄位包括：

- **CARDZ 穩定 ID**：`variantId` · `opaqueId`（前端 id）· `gemrateId`
- **卡面**：name / set / collectorNumber / tcg / psa10Population
- **對照**：`tplSlug` · `tcgplayerId` · `snkItemId`
- **Link（可撳）**：TPL 卡頁 · TCGplayer product · SNK item
- **狀態**：有冇 TPL 價、history 日數、needsReview

重建對照表：

```bash
cd /mnt/c/Users/jackson0202/Documents/Playground/cardz-market-cap   # 或 ~/cardz-market-cap
export CARDZ_DB_HOST=127.0.0.1
python3 -X utf8 pipelines/build_identity_registry.py
```

### 2. 一個入口腳本（唔使記一堆）

```bash
python3 -X utf8 pipelines/qualified_pool_operator.py maintain
# 或
powershell 唔用；Linux:
bash scripts/backend.sh   # backend daily 係另一條
# 專用 maintain（Windows 檔名保留，請喺 WSL 直接 call operator）:
python3 -X utf8 pipelines/qualified_pool_operator.py maintain --harvest-mode incremental
```

子命令（需要先先拆開用）：

| 命令 | 做咩 |
|---|---|
| `status` | DB 覆蓋率 |
| `export-worklist` | 由 watchlist 倒 940 |
| `map-tpl` | 綁 TPL slug |
| `harvest-tpl` | 全量/增量爬 TPL |
| `ingest-prices` | 寫入 `market_price_observation` |
| `fill-images` | 補圖 |
| `gap-report` | 仲爭咩 |

---

## 全量 vs 每日增量

| | 全量（一次） | 增量（每日） |
|---|---|---|
| 身份 | watchlist 940 + identity 表 | 只加新入池卡 |
| TPL 價 | `harvest --mode full` | `harvest --mode incremental`（同一窗 upsert） |
| 入 DB | `ingest-prices` | 同左 |
| 圖 | `fill-images --write` 缺邊補邊 | 只補缺圖 |

**全量通咗 → 每日就係 `maintain --harvest-mode incremental`。**

---

## 腳本數量原則（YAGNI）

| 保留 | 角色 |
|---|---|
| `qualified_pool_operator.py` | **唯一** 合格池運維入口（池可 >940） |
| `build_identity_registry.py` | 重生對照表（CSV/JSONL + lookup）— **map/ingest 後必跑** |
| `tcgpricelookup_ssr.py` | TPL 底層 collector（operator 會 call） |
| `us_price_fallback.py` | TCGFish/Collectr/PC fallback |
| `temp/fill_no_price_psa10.py` | 無價尾批量（暫 temp；應收編 operator） |

唔好再為每個源各開一條「每日維護腳本」——對照表有 id/link，operator 讀表做事。  
`pipelines/g10_*` 等 = **legacy 補史**，唔當合格池日更主線（見 AGENT_PIPELINE_INDEX）。

---

## 前端接駁

- 榜／詳情讀 **`opaqueId`**（public id）
- 私庫 `variantId` 唔出街
- 價：DB `market_price_observation`（`source_code=tcgpricelookup` 做 US 副線；榜權威仍係 SNK）
- 圖：`opaqueId` → image-qc / market-assets

出街仲要：`canonical_public_snapshot` → publish pointer（另步，唔喺 operator 內）。
