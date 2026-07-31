# DB 來源注腳表（Provenance Map）

**asOf:** 2026-07-30 · 政策：`POLICY_DB_PROVENANCE.md`  
**機器可讀：** `DB_PROVENANCE_MAP.json`

> `source_code` 係 DB 標籤；**Channel** 先係真渠道。

---

## 1. 核心事實 → Script → Channel

| Fact | DB 表 | source_code（常見） | **寫入 Script** | **真·渠道** | Identity 鍵 | 增量指令（已證骨架） |
|------|--------|---------------------|-----------------|-------------|--------------|----------------------|
| Exact 價 SNK | `market_price_observation` | `snk_psa10` | `snk_market_data.py` harvest+ingest | SNK HTTP API | `catalog_source_identity` snkrdunk | `snk_market_data.py --ids-file … --out …` → `--ingest-jsonl …` |
| Exact 價 G10 K | 同上 | `g10_kline` | `g10_analytics_ingest.py` + **`g10_kline_price_bridge.py`** | G10 analytics ledger（本機/已匯入 MSO） | ebay:/snkrdunk: entity | `g10_kline_price_bridge.py --write` |
| Exact 價 G10 eBay median | 同上 | **`ebay`** | **`g10_ebay_ingest.py`** | **G10 本機** `grade10-scraper/.../altxyz/{UUID}/ebay_PSA_*.json` | ebay = UUID | `g10_ebay_ingest.py`（backfill 已證） |
| 成交 G10 eBay | `market_sale_observation` | `ebay` + UUID entity | `g10_ebay_ingest.py` | 同上 G10 檔 | ebay UUID | 同上 |
| 成交 PC→eBay 標籤 | 同上 | **`ebay`** + entity **`pc:{id}`** | **`c11_pc_sold_ingest.py`** | **PriceCharting HTML** via CDP (`pricecharting_cf_session`) | variant via PC map；**唔寫** UUID identity | PC attach map → `c11_pc_sold_ingest.py --write` |
| 成交 SNK | 同上 | `snkrdunk` / snk_* | `ingest_snk_trades_sales.py` | SNK trades API | snkrdunk id | trades ingest after market_data |
| POP | `market_grader_population_*` / MSO | `gemrate` | `gemrate_source.py` / history | GemRate API/page | gemrate exact | gemrate harvest |
| Identity SNK/eBay/PC/GemRate/TPL | `catalog_source_identity` | snkrdunk, ebay, pricecharting, gemrate, tcgpricelookup | semi_auto / attach / bind_* / PC attach | 各源 | match_status | per-source bind |
| 圖 SNK | `market_image_*` | snkrdunk | `snk_image_ingest.py` + `snk_image_promotion.py` | SNK primaryMedia | snkrdunk | `--write --missing-only` |
| 圖 OP Limitless | 同上 | limitless 等 | `op_limitless_images.py` | Limitless CDN | collector | `--write --only-missing` |
| FX | `market_fx_rate_observation` | — | `fx_rates.py` / `fx_db_load.py` | FX API | — | daily FX |
| Index / rank | `market_index_*` | — | `market_alerts.py` | derived | — | after prices+pop |
| QC | report JSON | — | `canonical_db_qc.py` | read-only | — | `--run-id qc_…` |

### 命名陷阱（必讀）

| DB 見到 | 容易誤會 | 實際 |
|---------|----------|------|
| `source_code=ebay` **價** | live eBay API | **幾乎全係 G10 檔 median** |
| `source_code=ebay` **成交** + `pc:123` | eBay listing | **PriceCharting sold 表** |
| `source_code=ebay` **成交** + UUID | live eBay | **G10 altxyz 檔** |
| `tcgpricelookup` identity | **禁止**（錯 slug） | **2026-07-30 全量 purge identity**；價早已 0 行 |
| `g10_kline` | 次要 | **DADDY 2026-07-30 可當 exact**（identity 閘 + freshness） |

---

## 2. Incremental runbook（900 卡維運 · 已證優先序）

```text
1) Identity refresh (缺口)     semi_auto / PC attach / SNK bind
2) EN sold                     PC CDP → map → c11_pc_sold_ingest --write
3) Exact price                 SNK ingest 若有 kline；否則 G10 bridge / g10_ebay 價（政策已放行）
4) POP                         gemrate
5) Images                      snk_image / op_limitless
6) Derive + QC                 market_alerts → canonical_db_qc
7) FE Top100 filter            必須 has PSA10 sales 30d（POLICY_FE_TOP100_LIQUIDITY）
```

**PC 全宇宙 attach（已證）：** `pipelines/pc_full_serial_driver.py` → `pipelines/pc_full_shard_runner.py` serial + CDP :9222 → `c11_pc_ebay_map*.jsonl` → C11 write。`temp/**` 只係舊證據，禁止執行；逐項失敗記錄喺 `data/runtime/failures/events/`。

---

## 3. 維護

- 新成功 run：更新 `DB_PROVENANCE_MAP.json` + 本表一行  
- 每月：`temp/_db_provenance_build.py` 對 `market_ingest_run` 掃 source_code 分布，對照本表
