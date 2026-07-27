# 洗數據 / 入庫統一接口（2026-07-26 全量實測）

> 用戶問（2026-07-26）：
> 「咁多唔同嘅數據源都要統一返個格式入 DB 呀嘛。DB 係咪應該要有一個入庫嘅洗數據接口，定係一啲欄位呢？現時係咪已經有㗎啦？」

**答案：有，三層架構已經喺 DB 度，落地層同事實層行緊；但判定層（洗數據嘅「判斷」）由第一日到而家從來冇響過，而且 normalizer 冇共用模組。**

---

## 1. 現行三層（實測行數）

```
外部源 5 個：snk_psa10 · gemrate · snkrdunk · ebay · tag
      │
      ├─① 落地層  market_source_observation ················ 277,127 行  ✅ 行緊
      │     統一格式，就係你問嗰個接口。所有源入呢張表：
      │     (run_id, source_code, external_entity_id, observation_kind,
      │      effective_at, observed_date, payload_sha256, payload_json)
      │     原始 payload 原封留 JSON，sha256 內容定址，源頭 id 保留 → 追溯得返
      │
      ├─② 身分層  catalog_source_identity ················· 1,891 行  ⚠ 一半
      │           catalog_provider_identity_alias ·········· 0 行  ❌ 從未寫過
      │           market_identity_review_queue ············· 0 行  ❌ 從未寫過
      │
      └─③ 事實層  market_price_observation ················ 115,253 行  ✅
                  market_grader_population_observation ····· 7,904 行  ✅
                  market_daily_sales_aggregate ············· 3,592 行  ✅
                  market_sale_observation ·················· 0 行  ❌（G10 eBay 目標）
```

### 落地層分源實測

| source_code | 行數 | 日期範圍 |
|---|---:|---|
| `snk_psa10` | 268,135 | 2023-06-19 → 2026-07-24 |
| `gemrate` | 6,146 | 2026-07-21 → 2026-07-25 |
| `snkrdunk` | 2,247 | 2026-07-19 → 2026-07-24 |
| `ebay` | 393 | 2026-07-19 → 2026-07-24 |
| `tag` | 206 | 2026-07-22 |

**呢張表就係「統一格式入庫接口」。** 加新源唔使改 schema，寫 `source_code` + 塞 `payload_json` 就得。

---

## 2. 判定層係死嘅 —— 呢個係真問題

洗數據嘅欄位全部**設計咗、建咗、但只得單一值**：

| 表.欄 | 設計用途 | 實測分佈 |
|---|---|---|
| `catalog_variant.identity_status` | 身分信唔信得過 | `confirmed` × 1,590 —— **單一值** |
| `market_price_observation.metric_status` | 呢個價乾唔乾淨 | `ready` × 115,253 —— **單一值** |
| `market_daily_sales_aggregate.coverage_status` | 呢日數據齊唔齊 | `partial` × 3,592 —— **單一值** |
| `market_ingest_run.quarantined_count` | 隔離幾多條 | **0**，69 次 run 冇一次 |
| `market_ingest_run.rejected_count` | 拒收幾多條 | **0**，69 次 run 冇一次 |

**閘裝咗，由第一日到而家從來冇響過。** 唔係數據乾淨，係冇人判過 —— 所有嘢一律當 pass。
呢個正正係路線更新第 6 點嗰句：**「靜靜地少咗嘢係最陰險嘅失敗」**。

收貨帳亦唔平：69 次 run 累計 `observed 2,011,920` → `accepted 277,127`，
中間 173 萬條嘅去向 `quarantined=0 rejected=0` **零記錄**。
（大機會係跨日重複觀測嘅正常去重，但**冇任何一行證明呢件事**。）

---

## 3. 11 張 0 行嘅表，其中 4 張就係洗數據層本身

| 表 | 設計用途 | 點解重要 |
|---|---|---|
| `catalog_provider_identity_alias` | 唔同源同一張卡叫唔同名，喺呢度對返 | **G10 identity 覆蓋率 395/1,590 卡喺呢度** |
| `market_identity_review_queue` | 對唔到嘅唔准靜靜丟，入隊等人裁 | 有 `reason_code` `evidence_sha256` `resolved_variant_id` |
| `market_raw_payload_object` | 原始 payload 內容定址歸檔 | 配 `market_retention_archive_manifest`（亦 0 行）|
| `market_source_observation_payload_pointer` | 落地行 → 歸檔物件 | 同上 |

另外 7 張 0 行：`catalog_printing_identity` `catalog_story_pointer` `catalog_variant_locale`
`market_image_asset` `market_image_qc` `market_image_source_pointer` `market_source_effective_observation`
`market_tracked_sales_aggregate` `market_sale_observation`。

---

## 4. Normalizer 冇共用模組 —— 已經出過事

洗數據函數散落 **11 個檔案，各寫各嘅**：

| 函數 | 幾份 | 喺邊 |
|---|---:|---|
| `normalize_collector` | **3** | [g10_public_snapshot.py](../pipelines/g10_public_snapshot.py) `normalize_collector()`（帶 set_name）· [tag_daily_capture.py](../pipelines/tag_daily_capture.py) `normalize_collector()` · 同一份 g10 檔嘅 `KadoRawResolver.resolve()` 自己嗰套 |
| `normalize_language` | 2 | [g10_ingest.py](../pipelines/g10_ingest.py) `normalize_language()` · [market_discovery.py](../pipelines/market_discovery.py) `normalize_language()` |
| `normalize_name` | 1 | [tag_daily_capture.py](../pipelines/tag_daily_capture.py) `normalize_name()` |
| 日期解析 | **5** | `parse_datetime` ×2 · `parse_instant` · `parse_time` ×2 · `parse_effective_at` · `parse_sale_at` |

### 實際代價（2026-07-26 實證）

`opaque_id = sha256(tcg, language, set_name, collector.normalized, name)[:24]`
（[g10_public_snapshot.py](../pipelines/g10_public_snapshot.py) `opaque_id()`）

→ **normalizer 唔一致 = 同一張卡分裂成兩個 id = 價格同 POP 史各自孤立。**

已證兩宗：

1. **collector number** —— `KadoRawResolver` 對 `GG69` 出 `gg69/gg70`，`normalize_collector` 出 `gg69`。
   同一張卡理論上可以出兩個 `opaque_id`。（修好咗 6 張分母缺失，呢個 resolver 不一致仍在，另開 task）
2. **卡名根本冇 normalizer** —— DB 36 條爛 `canonical_name`：

| id | canonical_name | 病 |
|---:|---|---|
| 111 | `Monkey.D.Luff` | 截斷（Luffy）·**rank 91 在榜** |
| 34 | `Mew/Mewtwo Gx` | `&` 變 `/`、`GX` 變 `Gx` ·**rank 30 在榜** |
| 22 / 36 / 56 / 62 … （24 條）| `Monkey.D.Luffy SEC-SP Booster Pack Awakening Of The New Era` | 卡名塞咗成個 booster pack 名，`set_name` 反而係 `Comic Parallel`（rarity 唔係 set）|
| 312 / 319 | `Shanks Gold Background SR-SPC 3th Anniversary Special Card` / set=`Booster Pack CARRYING ON HIS WILL` | name 同 set **調轉咗** |
| 32 / 910 | `_____'s Pikachu` vs `______'s Pikachu` | 5 個 vs 6 個底線，可能係同一張卡分裂 |

---

## 5. 要做咩（排優先）

| # | 缺口 | 值幾多 | 成本 | 何時 |
|---|---|---|---|---|
| 1 | **判定層通電**：`metric_status` / `coverage_status` / `identity_status` 真係分類；`quarantined/rejected` 記數 | 「靜靜地少咗嘢」即刻睇得到 | 中 | 出街後即刻 |
| 2 | **共用 normalizer 模組** `pipelines/normalize.py`：collector / language / name / datetime 各一份 | 杜絕同卡分裂成兩 id | 中 | 出街後 |
| 3 | `catalog_provider_identity_alias` + `market_identity_review_queue` 通電 | identity 395 → 641，解鎖 G10 全部數據 | 中 | 出街後 |
| 4 | **卡名 normalizer**：36 條爛名 | 產品文案直接見到 | 低（display 層）| **出街前修 rank 30 / 91** |
| 5 | `market_raw_payload_object` 歸檔 | 原始 payload 可追溯 | 低 | 之後 |

### 出街前後點分

- **出街唔使等呢個**：落地層 277K 行行緊、事實層有真數、snapshot 出得到。
- **爛名 rank 30 / 91 出街前要修**，行 **display 層**（`apps/web/src/lib/card-names.ts`），
  **唔准改 DB `canonical_name`** —— 會換 `opaque_id`、孤立價格 / POP 史、撞
  [db_runtime.py](../pipelines/db_runtime.py) `upsert_variant()` 嘅 fail-closed identity assert。
  （同 collector number 嗰次一樣：`display` 同 `normalized` 解耦，id 一個 bit 都唔郁。）

---

## 6. 呢份文邊個寫 · 邊個讀 · 而家有冇人用

| | |
|---|---|
| **檔喺邊** | `docs/DATA_NORMALIZATION.md`（呢份） |
| **邊個寫** | 人手；行數由 `SHOW TABLES` + `GROUP BY status` 逐張量；normalizer 清單由 `grep '^def \(normalize\|parse_\)' pipelines/*.py` 得出 |
| **邊個讀** | 加新數據源之前 · 改任何 `normalize_*` 之前 · 見到同一張卡兩個 id 之時 |
| **而家有冇人用** | ⚠ **冇 audit 腳本**，§1 §2 數字要自己跑先重驗得到 —— 同 [G10_BASELINE.md](G10_BASELINE.md) §6 係同一個已知缺口 |

**相關**：[G10_BASELINE.md](G10_BASELINE.md)（G10 = 我哋個底）· [HANDOFF.md](HANDOFF.md) · [PROJECT_STATE.md](../PROJECT_STATE.md)
