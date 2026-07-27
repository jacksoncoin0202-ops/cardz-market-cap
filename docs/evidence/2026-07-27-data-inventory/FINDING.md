# 未入庫／未組裝數據盤點

**量度日期：** 2026-07-27（DB 與磁碟同日量度）
**Agent：** `opus-inventory`（唯讀，無任何寫入 DB 或改動 pipeline）

## 點量

| 面向 | 方法 |
|---|---|
| DB 行數 | `scripts/ro_sql.py` 逐表 `COUNT(*)`（34 張表全量，**未用** `information_schema.TABLE_ROWS`） |
| 磁碟數據 | [probe_disk.py](probe_disk.py) — walk `grade10-scraper/data`，逐檔 parse JSON 數記錄 |
| 卡圖 manifest | [probe_image_manifest.py](probe_image_manifest.py) — `manifests/image-qc.json` 對 `catalog_variant.opaque_id` |
| Live snapshot 落差 | [probe_snapshot_vs_db.py](probe_snapshot_vs_db.py) — `data/public/seed-snapshot.json` 對 live catalog |
| 機制存在性 | `grep` 表名／`observation_kind`／`SOURCE_CODE` 橫掃 `pipelines/`、`scripts/`、`apps/`、`packages/` |
| 是否有跑 | 雙條件：**(a)** 有無被 `pipelines/run_daily.py` 調用鏈引用 **且 (b)** 目標表行數 |

## 結論一句

**真正嘅瓶頸唔係「未入庫」，係「世代錯位」——`data/public/seed-snapshot.json` 同 `manifests/image-qc.json` 都係 2026-07-22T09:48:26 生成，之後 `catalog_variant` 重建過，令 snapshot 360 個 `opaque_id` 有 331 個（91%）喺 live DB 已經唔存在；DB 入面嘅 TAG POP、四語故事、價格序列全部齊，但接唔上一個 id 已死嘅 snapshot。**

## 前提

- `market_ingest_run` 只有 100 行、最早 `2026-07-23 17:42`，**唔係可靠嘅「從未跑過」判據**。所以本文所有「未跑」結論一律用上表嘅雙條件判定，唔單靠 run 記錄。
- 磁碟數字係 2026-07-27 一次 walk 嘅結果；`grade10-scraper/` 仍在變動。
- `manifests/image-qc.json` 由 `opus-image-fix` claim 住，本盤點只讀不寫；該 agent 完成後 manifest 數字會變，`331 orphan` 需重量。
- `data/public/seed-snapshot.json` 係 demo/seed 檔（自我餵飼），非生產 snapshot；`generation.blockers` 自報 6 項未完成，包括 `production_database_cutover_pending`。
- 「零 reader」類結論係 grep 全域負面斷言，量度時間 2026-07-27，pattern 為表名／`observation_kind` 字面值，範圍 `pipelines/ scripts/ apps/ packages/`（排除 `node_modules`、`temp/`、`data/`）。

---

## 總表（按對 live 版本嘅影響排序）

### P0 — 直接阻住 live 版本

| 機制名 | 數據瞓喺邊 | 規模（實測） | 欠咩先入到庫／組裝到 | 建議 |
|---|---|---|---|---|
| 公開 snapshot 世代錯位 | `data/public/seed-snapshot.json` | 360 個 id（top100 100 + watchlist 260），**只 29 個仲存在**，331 個已死（91%）；`generatedAt` 2026-07-22 | 用現行 `catalog_variant` 重跑 `pipelines/canonical_public_snapshot.py`，唔係補數據 | **最高優先。** 呢個唔修，下面任何數據都上唔到 live |
| 卡圖 manifest 同一批錯位 | `manifests/image-qc.json` + `data/public/market-assets/` | 623 條 `publicAllowed`+`raw_front`，**623 個檔案全部在磁碟**；419 個 distinct `publicId` 中只 **88** 對得上 live catalog，**331 orphan** | 同上：manifest 要對現行 catalog 重生成。檔案本身冇丟 | 直接關係 live 卡圖。331 orphan 同 snapshot 死 id 係**同一批**（數字完全一致），一次重生成兩邊都好返 |
| TAG POP 未上表面 | `market_grader_population_observation` (grader_code='TAG') | **837 行 / 451 張卡**，2026-07-22 → **2026-07-26**（仍在更新） | 數據齊，只欠 snapshot 組裝。snapshot 自報 `graderPopulationReady.TAG = 0` | 入。**順手更正 `CLAUDE.md`：TAG 已唔係凍結喺 07-22／307 卡** |
| 四語故事未上表面 | `catalog_variant_locale.market_story` | en **462** 張（平均 2,669 字）、ja/zhCN/zhTW 各 **246** 張（平均 221–301 字）；**四語齊 246 張**；`ko` 0 行（欄位不存在資料） | 數據齊，只欠組裝。snapshot 自報 `localizedStoryCount` 四語全 **0** | 入。另有 **216 張有英文長文但未翻譯**（462 − 246） |
| 價格／POP 變動率全空 | `market_price_observation`、`market_grader_population_observation` | 價格 `snk_psa10` **115,092 行 / 336 變體**（至 07-26）、`ebay` 5,476 / 505；POP 5 家共 **9,944 行** | snapshot 自報 `changeReady` 1d/7d/30d 全 **0**、`graderPopulationChangeReady` 五家全窗口 **0**，`historyDaily.priceStatus` 為 `unavailable` | 入。DB 有多日序列足以算 delta；係組裝層冇攞 |

### P1 — 已入庫但冇落點／冇 reader

| 機制名 | 數據瞓喺邊 | 規模（實測） | 欠咩先入到庫 | 建議 |
|---|---|---|---|---|
| G10 K 線／逐卡指標 | `market_source_observation`，`source_code='g10_analytics'` | `g10_kline_daily` **47,582 行 / 636 實體**（2023-07-20 → 2026-07-25）；`g10_card_metrics` **641 行 / 641 實體**（只 07-25 一日） | **冇 typed 目標表，亦冇任何 reader**：全 repo 只有 `pipelines/g10_analytics_ingest.py` 自己提及呢兩個 kind | 三年歷史 K 線係最大嘅未用資產。要落 typed 表 + 寫 reader 先用得。**同 live 版本有關**（走勢圖） |
| G10 指數 | `market_source_observation`，`source_code='g10_index'` | **27 行**：`g10_index_chart` 18 / `_stats` 3 / `_summary` 3 / `_constituents` 3，僅 2026-07-22 → 07-23 | 同上：零 reader。而且已停更 4 日 | 樣本太細又停更，**唔值得現階段入**。要做指數建議走 `market_index_snapshot`(15) / `market_index_constituent`(2,418) |
| 身分覆核隊列 | `market_identity_review_queue` | **216 行，全部 `pending`，全部 2026-07-26 建立**。主因：snkrdunk `collector_number_mismatch` 67、snkrdunk `name_not_in_catalog` 63、ebay `collector_number_mismatch` 38、ebay `variant_duplicate_conflict` 15 | **完全冇 resolver**：`db_runtime.py`、`g10_identity_expand.py`、`g10_variant_seed.py` 全部只有 `INSERT`，全 repo 冇一句 `UPDATE market_identity_review_queue` | 只入不出嘅收件箱。snapshot blocker `canonical_identity_review_pending` 就係佢。**要人手裁決或寫 resolver** |

### P2 — 機制寫好但從未跑（目標表 0 行）

| 機制名 | 數據瞓喺邊 | 規模（實測） | 欠咩先入到庫 | 建議 |
|---|---|---|---|---|
| Payload 保留／歸檔 | `pipelines/db_retention.py`（`INSERT` 三處齊備） | 目標 4 張表全部 **0 行**：`market_retention_archive_manifest`、`market_source_observation_payload_pointer`、`market_source_effective_observation`、`market_raw_payload_object` | 腳本同 migration（`008_payload_retention.mysql.sql`）都在，但 **`run_daily.py` 完全冇引用 `db_retention`**，即從未被排程 | 純運維（省儲存 + 歸檔），**同 live 表面無關**。低優先，但係唯一「寫好未跑」嘅乾淨例子 |
| 追蹤成交聚合（已棄用） | `market_tracked_sales_aggregate` | **0 行**。writer 在 `pipelines/db_runtime.py` 但只接 `tracked_sales_1d/7d/30d`；ledger 實際只有 **`tracked_sales_daily` 17,203 行 / 336 實體** | `observation_kind` 名對唔上 → writer 係 dead code。功能已由 `market_daily_sales_aggregate`（**15,776 行**）取代 | **唔好接返。** `scripts/audit_wiring_gaps.py` 同 `g10_analytics_ingest.py` 都明寫禁止回讀。建議連 dead writer 一齊刪 |

### P3 — GemRate key 死後嘅結構性斷點

| 機制名 | 數據瞓喺邊 | 規模（實測） | 欠咩先入到庫 | 建議 |
|---|---|---|---|---|
| G10 POP 鏡像路徑錯位 | 讀：`integrations/grade10/data/`；寫：`data/runtime/private-landing/g10/full/` | 鏡像 `cards/` **0 個 entry**（只剩 `_state/` 同 `index/`）；private-landing 有 **2 個完整 freeze，9,481 + 9,473 個檔** | `pipelines/gemrate_source.py` 嘅 `DEFAULT_G10_MIRROR_ROOT` 指向空目錄；`pipelines/grade10_full_freeze.py` 嘅 `DEFAULT_LANDING` 寫去另一處。兩條路唔通 | **GemRate key 一死，fallback 讀空目錄 → POP 直接 0。** 要接通兩條路徑（或改 fallback 指向 private-landing）。**同 live 版本有關** |

### P4 — 磁碟有數據但已核實冗餘（唔建議入）

| 機制名 | 數據瞓喺邊 | 規模（實測） | 為何唔值得入 |
|---|---|---|---|
| `populations.json` | `grade10-scraper/data/cards/*/*/populations.json` | **641 檔**；PSA 641 / CGC 582 / BGS 549 / SGC 388 個 grader 記錄 | 單點快照、**無歷史**；而 DB PSA 已覆蓋 **1,590 張卡** > 磁碟 641。入庫係倒退 |
| `apparel_grade_*` | 同上目錄 | 約 **36,631** 條記錄 | 鏡 SNKRDUNK；我方 `snk_psa10` 單一 source 已有 **287,397 行** observation。低價值 |
| eBay 逐級成交 | `ebay_PSA_10/PSA_9/BGS_10/BGS_BL/CGC_10.json` | 磁碟 → DB 入庫率 **86–90%** | 已入咗大部分，**唔係缺口**。`market_sale_observation` 120,086 行、`ebay` 至 2026-07-25 |
| 故事指針覆蓋缺口 | `catalog_story_pointer` | **466 行 / 462 變體**，對 `catalog_variant` **1,705** | 缺 1,239 張，但呢批唔喺 live top100/watchlist 表面。**P0 修好先講** |

### P5 — 唔喺每日調用鏈嘅 pipeline

`pipelines/run_daily.py` 實際只調用 13 個：`gemrate_source`、`source_crosswalk`、`tracked_universe`、`tag_daily_capture`、`snk_market_data`、`ebay_sold_data`、`market_source_sync`、`fx_db_load`、`market_alerts`、`data_coverage_audit`、`ensure_std_card_images`、`verify_images`、`canonical_public_snapshot`（另 import `fx_rates`、`g10_ingest`、`g10_public_snapshot`、`db_runtime`）。

`pipelines/` 共 58 個 `.py`，即 **41 個唔喺每日鏈**。當中按性質分：

| 類別 | 檔案 | 狀態 |
|---|---|---|
| 有 DB 痕跡、按需手動跑 | `g10_ebay_ingest`(ebay)、`g10_research_ingest`(g10_research)、`g10_asset_ingest`(g10_asset)、`g10_sales_cache_ingest`(snkrdunk)、`g10_snkrdunk_grades_ingest`(snk_grade)、`g10_variant_seed`、`g10_identity_expand`、`g10_analytics_ingest`、`editorial_locale_sync` | `market_ingest_run` 有記錄，最近 2026-07-26。**正常** |
| 部分跑過 | `converge_printing_identity` → `catalog_printing_identity` **68 行** | 跑過但未收斂完 |
| 寫好未接排程 | `db_retention` | 見 P2 |
| 採集／探索工具（按需） | `snkrdunk_bulk`、`snkrdunk_discover`、`gemrate_brute_harvest`、`ebay_brute_harvest`、`gemrate_candidate_backfill`、`gemrate_candidate_discovery`、`gemrate_resume_failed`、`tag_pop_data`、`grade10_full_freeze` | 設計上就係手動觸發，**唔係缺口** |
| 驗證／契約（唔寫 DB） | `verify_archives`、`registry_lineage`、`data_routing`、`data_cleaning_rules`、`active_universe` | 0 行係正常 |
| CARDZ 自算衍生 | `population_daily`、`daily_prices`、`market_metrics`、`ranking_derivation`、`market_discovery`、`discovery_refresh` | 唔喺每日鏈；**需另行確認邊個真係 live snapshot 嘅 delta 來源**（`changeReady` 全 0 疑似同呢批未跑有關） |
| 影像處理 | `image_store_consolidate`、`native_image_refetch`、`native_image_resolver`、`canvas_normalize_backfill`、`build_asset_derivatives` | 按需 |
| 編輯流程 | `editorial_localization`、`editorial_story_merge`、`editorial_translate_queue` | 按需（216 張待譯要用到 `editorial_translate_queue`） |

---

## 全庫行數（2026-07-27 精確 `COUNT(*)`，34 張表）

| 行數 | 表 | | 行數 | 表 |
|---:|---|---|---:|---|
| 347,291 | `market_source_observation` | | 456 | `market_image_qc` |
| 120,974 | `market_price_observation` | | 360 | `catalog_provider_identity_alias` |
| 120,086 | `market_sale_observation` | | 216 | `market_identity_review_queue` |
| 15,776 | `market_daily_sales_aggregate` | | 100 | `market_ingest_run` |
| 10,358 | `market_population_transport_observation` | | 76 | `market_alert` |
| 9,944 | `market_grader_population_observation` | | 71 | `market_alert_event` |
| 4,102 | `market_universe_member` | | 68 | `catalog_printing_identity` |
| 3,432 | `market_candidate_daily_snapshot` | | 15 | `market_index_snapshot` |
| 2,418 | `market_index_constituent` | | 10 | `cardz_migration_ledger` |
| 2,081 | `catalog_source_identity` | | 10 | `market_fx_rate_observation` |
| 1,705 | `catalog_variant` | | 9 | `cardz_schema_version` |
| 1,200 | `catalog_variant_locale` | | 8 | `market_alert_evaluation` |
| 468 | `market_image_source_pointer` | | 6 | `market_universe_lock` |
| 466 | `catalog_story_pointer` | | 1 | `market_ingest_checkpoint` |
| 456 | `market_image_asset` | | **0** | `market_raw_payload_object` |
| | | | **0** | `market_retention_archive_manifest` |
| | | | **0** | `market_source_effective_observation` |
| | | | **0** | `market_source_observation_payload_pointer` |
| | | | **0** | `market_tracked_sales_aggregate` |

## 已推翻嘅舊記錄

| 舊說法 | 出處 | 實測（2026-07-27） |
|---|---|---|
| 卡圖 263 張入，只 87 join 得返 variant | agent memory | manifest **623** 條、檔案 **623** 個齊、**88** 對得上、**331** orphan。舊數字偏低 |
| `market_image_asset` 有 opaque_id orphan | agent memory | **0 orphan**：456 行全部 join 得返，453 個 distinct variant。問題喺 manifest，唔喺 DB |
| TAG POP 凍結喺 2026-07-22、只 307 張卡 | `CLAUDE.md` | **837 行 / 451 張卡，至 2026-07-26 仍在更新**。已過時 |
| legacy `ebay` source 已停產 | 任務簡報 lead #4 | eBay **仍活躍**：`market_sale_observation` PSA 10 8,558 行 / 505 變體，至 2026-07-25。真正凍結嘅係 `snkrdunk`/`ebay` 嘅 `grader_population_*`（多數單日 07-21/07-22）同 `index_constituent`（最後 07-24） |
| `market_identity_review_queue` 203 行 | 任務簡報 lead #5 | **216 行** |
