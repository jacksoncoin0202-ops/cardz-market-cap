# PLAN 036 / FE02 — Identity-first 全量重建（唯一執行文件）

> **版本命名（hard）：** backend = **036**、frontend = **FE02**。之後每次更新推上去：037 / FE03、038 / FE04……文件、migration、snapshot generation、FE build id 全部跟同一組數。
>
> **本版：** 2026-08-08 重寫。取代 [`PLAN_036_FE02.superseded-20260808.md`](PLAN_036_FE02.superseded-20260808.md)（916 行）。舊檔同時保留「舊方案 + 推翻 + 更正」三層，工程師有機會照錯版本做 —— 呢份**唔再有**「§9 推翻 §1」呢種疊加結構；有新發現直接改返原地方。
>
> **2026-08-08b：** 合併 Codex 收斂第二輪 — 吸收 8 項／拒收 4 項，處置紀錄喺 §3.13，改動位標 ⬅036b。owner 同日確認 G10 圖退出 current selection（D11）。
>
> **唯一 working tree：** `cardz-market-cap-fe-db-20260805`（branch `codex/cardz-fe-db-consolidation-20260805`）。
> ⚠️ 舊 folder `cardz-market-cap` **唔止係舊 code** —— 個 MySQL container 同 14 GB volume 都係佢管住。詳見 §10.1。
>
> **狀態：** 未開工。Gate 0.1 已批准；**0.1 之後每一步都要另攞 go-ahead**。

---

## 閱讀指引（給紅隊 / 外部審查者）

呢份文件用標籤制，全文強制：

| 標籤 | 意思 |
|---|---|
| `[KNOWN]` | 已確認事實 — 有 `file:line`、query 輸出或 command 輸出撐住 |
| `[COMPUTED]` | 由已知資料計算得出 |
| `[INFERRED]` | 合理推論 — 有證據支持但未直接驗過 |
| `[COMMON]` | 常識 |
| `[FRAME]` | 框架性假設 — 我哋揀嘅做法，唔係事實 |
| `[GUESS]` | 猜測 |

**冇實測撐嘅唔准寫 `[KNOWN]`。唔知就寫「我不知道」。**

想快速捉痛腳：**§3（已知錯誤模型）** 同 **§12（未解）** 係最脆嘅部分。§2 全部係實測數字，可以直接 re-query 對數。§附錄 A 係三句話總結。

---

## 目錄

| § | 內容 |
|---|---|
| 0 | 範圍 / 唔做乜 |
| 1 | 已鎖定決定 D1–D9 |
| 2 | 現況實測數字 |
| 3 | 已知錯誤模型（含 Codex 收斂版 7 條錯處） |
| 4 | Gate 0 — 地基 |
| 5 | Gate 1 — 工具回收 |
| 6 | Stage 1–6 執行 |
| 7 | Migration 036 規格 |
| 8 | Validator 參數化 |
| 9 | 驗收 / 攻擊案例 |
| 10 | 環境事實清單 |
| 11 | 備份 / 還原 runbook |
| 12 | 未解 / 留俾紅隊 |
| A | 一句話總結 |

---

## 0. 範圍

### 0.1 036 要達成乜

`[FRAME]` 目標狀態（⬅036b 兩層 cohort，併自 Codex）：

- **`qualified_identity`**：一個獨立 GemRate PSA printing entity，latest 真 GemRate PSA10 POP ≥ 1000
- **`product_ready`**：qualified_identity **再加** exact-bound current price（PC 或 SNK）＋ exact 來源圖（SNK 優先，PC 後備）→ market cap = current price × 該 identity 嘅 PSA10 POP，**公開上榜**
- **`qualified_market_pending`**：POP 合格但暫時冇 exact price／image → 留喺 catalog，**唔公開、唔准當非入圍刪**
- POP < 1000 或冇有效 PSA identity → 非入圍，activation 後**物理刪除**

`[COMPUTED]` 點解要兩層：PC exact 349 / SNK exact 154，~1,096 條 manual_review 未升（§3.3）。要求「全部 qualified 都 product-ready 先 activate」= activation 俾最慢嗰張卡挾持。activation 以 product_ready 子集上榜，market-pending 留喺後台繼續升級。

`[FRAME]` **「一 take pass」嘅定義**：一個 generation id、一條 orchestrator 命令、內建 checkpoint／resume、一次 activation。
**唔係**一個超大 SQL transaction。**唔係**斷網之後要由頭嚟過。

### 0.2 036 唔做嘅嘢（hard）

| 唔做 | 原因 |
|---|---|
| 刪／搬舊 checkout `cardz-market-cap` | `[KNOWN]` 佢管住 live MySQL 嘅 compose project（§10.1），仲行緊 3801 |
| 搬 Docker compose / 改 project name | `[KNOWN]` volume 名 `cardz-market-cap_cardz_mysql`（14 GB）由 folder name 推導，搬 = 有機會靜靜起新空 volume |
| 停 3801 | `[FRAME]` 同今次重建冇關係，只增加風險 |
| 發佈 AWS / public snapshot | `[FRAME]` owner 未批 |
| 改 Google Sheet | `[FRAME]` owner 未批 |
| 統一 `market_ingest_run.status` `'complete'` / `'completed'` | `[KNOWN]` 係故意分開（§3.6），改 = 行為改變，押後 037 |
| 加闊 `uq_market_price_daily` | `[COMPUTED]` 跨 293,893 行改 cardinality，押後 037 |
| 生成新故事 | owner 決定：故事唔擋 036（D8） |
| 改 PC parser | `[KNOWN]` parser 已經啱（§3.12 第 3 條）；改咗爆 7 個讀取點 |

---

## 1. 已鎖定決定（owner）

| # | 決定 | 定於 | 備註 |
|---|---|---|---|
| **D1** | GemRate 係 PSA identity + PSA10 POP **唯一** authority | 長期 | PC / SNK 只提供各自市場證據 |
| **D2** | `market cap = current PSA10 price × GemRate PSA10 POP` | 長期 | |
| **D3** | current 市值價**只認** exact PC 或 exact SNK PSA10 | 2026-08-08 | TPL / G10 / eBay 只入歷史 bar |
| **D4** | **兩邊都有價 → 語言路由決勝：EN → PC 贏，JP / OP → SNK 贏。只有一邊完全冇數，先跌落另一邊。** | **2026-08-08（改版）** | **原本「effective_at 較新贏」作廢**，理由 §3.7 |
| **D5** | 採集唔准 filter；fetch-all 落 landing，入 DB 先揀 | 長期 | |
| **D6** | 圖：exact SNK `primaryMedia.imageUrl` 第一，exact PC product image 第二；兩邊都冇 = 唔 product-ready。禁 Kado / TCGplayer / AI / 無來源圖 | 2026-08-01 | |
| **D7** | **現有 `opaque_id` 釘死唔准變；新／split 出嚟嘅 variant 由 printing sha 派生 opaque id** | **2026-08-08（新增）** | 理由 §3.5 |
| **D8** | 故事唔擋 036。保留真 en / ja / zhCN / zhTW；762 條模板式 ko 標非 current；唔生成新故事 | 2026-08-08 | |
| **D9** | 非入圍（POP<1000／無有效 identity）variant 最終物理刪除；raw provider evidence + 人手決定保留 | 2026-08-08（08b 修訂） | `qualified_market_pending` 唔屬非入圍，唔刪 |
| **D10** | 兩層 cohort：activation 以 `product_ready` 子集公開；`qualified_market_pending` 唔刪唔公開 | **2026-08-08b** | 併自 Codex；同時解決 §12.1 排序問題 |
| **D11** | **G10 圖正式退出 current selection，只留歷史 bar** | **2026-08-08 owner 確認** | D6 補充；TPL／G10／eBay 一致只入歷史 |

---

## 2. 現況實測數字

> 全部 `[KNOWN]`，2026-08-08 由 live DB（`127.0.0.1:3308`，schema `cardz_market_cap`）直接 query 得出。可 re-query 對數。

### 2.1 卡數

| 指標 | 數 |
|---|---:|
| `catalog_variant` 總數 | **1,782** |
| 有 ≥1 條真 GemRate PSA10 observation | **1,157** |
| 零真 PSA10 observation | **625** |
| latest 真 PSA10 POP ≥ 1000（= target pool） | **1,146** |
| 對應 distinct GemRate id | **1,154**（差 8 = collapsed 集） |
| 現時 live ranking generation `ab0b51aa…`（accepted 2026-08-07 09:48:07） | **762** |
| 兩者重疊 | **758** |
| 合格但未上榜（backlog） | **388** |
| 已上榜但**唔合格** | **4** |

「真 GemRate PSA10 observation」定義（`[KNOWN]`，三個條件缺一不可）：
```sql
grader_code = 'PSA'
AND top_grade_label = '10'
AND external_entity_id REGEXP '^[0-9a-f]{40}$'
```

4 張唔合格但 live 嘅卡 `[KNOWN]`：

| variant | 真 PSA10 POP | 入圍時用嘅 POP | 借自 |
|---|---:|---:|---|
| 110 | 222 | 7,759 | `snkrdunk:348126`，label `top` |
| 1530 | 582 | 1,387 | 已 rejected 嘅 sibling id `7c4fb3f3…` |
| 1552 | 931 | 2,160 | conflict id `fd33b921…` |
| 1553 | 892 | 1,902 | conflict id `dc37161b…` |

### 2.2 Schema

| 指標 | 數 |
|---|---:|
| Base table | **55** |
| View | **17** |
| FK 引用 `catalog_variant` 嘅表 / 約束 | **35 / 36** |
| 全 schema FK 總數 | **76** |
| 其中 `ON DELETE CASCADE` 或 `SET NULL` | **0**（全部 `NO ACTION`） |
| Trigger / Event | **0 / 0** |

`[KNOWN]` `catalog_variant_alias` 一張表貢獻兩條 FK（`canonical_variant_id` + `duplicate_variant_id`），所以 36 條 / 35 張表。

有 `variant_id` 但**冇 FK**（純靠 information_schema 派生刪除順序一定會漏）：
- `operator_binding_freeze` — **5,590 行**（identity 762、image accepted 2,420 覆蓋 629 個 variant、image rejected 1,283、source accepted 1,024）
- `catalog_variant_remap` — 0 行，用 `old_variant_id` / `new_variant_id`，連欄位名掃描都掃唔到

Raw payload 層（**冇** variant FK，D9 決定全部保留）：
- `market_raw_payload_object` — **317,169 行**，零 FK，無 `variant_id`
- `market_source_observation_payload_pointer` — **504,982 行**，FK 指去 raw object / archive manifest / source observation

### 2.3 Binding 現況

`catalog_source_identity` 按 source × match_status `[KNOWN]`：

| source | exact | manual_review | rejected | conflict | new_variant | derived |
|---|---:|---:|---:|---:|---:|---:|
| pricecharting | **349** | **483** | 109 | 2 | — | — |
| snkrdunk | **154** | **613** | 150 | 40 | 104 | 2 |
| gemrate | 762 | 757 | — | — | — | — |

### 2.4 View row count（最重要嗰組數）

| view | 行數 |
|---|---:|
| `operator_strict_source_identity` | **762**（全部 gemrate） |
| `operator_accepted_psa10_price_history` | **0** |
| `operator_eligible_accepted_psa10_price_history` | **0** |
| `operator_canonical_current_metric_projection` | **0** |
| `operator_eligible_pricecharting_variant` | **0** |
| `operator_canonical_image_projection` | **12** |

### 2.5 POP 表

`market_grader_population_observation`，`source_code='gemrate' AND grader_code='PSA'` `[KNOWN]`：

| 指標 | 數 |
|---|---:|
| 總行 | **56,116** |
| distinct `(variant_id, observed_date)` | **56,116** ← **完全飽和** |
| `label='10'` + bare-40-hex id | 52,936 |
| `label='top'` + 非 hex id | 3,064 |
| `label='top'` + bare-40-hex id | **116** |
| 有 >1 個 distinct `external_entity_id` 嘅 variant | **1,125** |
| 逐日 POP **下跌** 次數 / 涉及 variant | **507 / 493** |

### 2.6 故事

`catalog_variant_locale.market_story` `[KNOWN]`：

| locale | 有故事 | distinct skeleton（剝走卡名同拉丁數字後） | 同人共用 skeleton | 純 stub |
|---|---:|---:|---:|---:|
| en | 1,031 | 543 | 513 | — |
| ja | 815 | 308 | 533 | — |
| **ko** | **762** | **19** | **755** | **186** |
| zhCN | 815 | 294 | 547 | — |
| zhTW | 815 | 295 | 546 | — |

`[KNOWN]` ko 三個 skeleton 覆蓋 762 之中 508 條。
`[KNOWN]` 出處分兩批：`canonical_locale_merge_v1`（每 locale 762，模板）vs `migration023_db_state`（en 269 / ja、zhCN、zhTW 各 53 / **ko 只有 1**）。
`[COMPUTED]` → 韓文根本冇做過 editorial pass。

### 2.7 圖

| 指標 | 數 |
|---|---:|
| Pipeline 閘數（`canonical_image_acceptance_id IS NOT NULL`） | **762** → 過 |
| FE primary view 出到 | **12** |
| FE fallback（`operator_binding_freeze`）出到 | **629** |
| FE merged 實際 render 真圖 | **629** |
| **3800 而家見到 placeholder** | **133** |

`[KNOWN]` QC 行 semantic status 分佈：`human_or_vision_confirmed` 1,592 + `accepted_freeze` 2,248；`meta_unreviewed` 1,327、`pending_review` 686、`geometry_normalized_pending_review` 378。

---

## 3. 已知錯誤模型

> 每條都係開工前必須理解嘅，唔係「順手修」。

### 3.1 入圍閘攞錯行（原始事故）

`[KNOWN]` `pipelines/operator_control.py:2057` `latest_psa10_pop`：

```python
SELECT g.variant_id, g.top_grade_population, g.effective_at
FROM market_grader_population_observation g
INNER JOIN (
    SELECT variant_id, MAX(effective_at) AS mx
    FROM market_grader_population_observation
    WHERE grader_code = 'PSA'
    GROUP BY variant_id
) t ON t.variant_id = g.variant_id AND t.mx = g.effective_at
WHERE g.grader_code = 'PSA'
```

唯一 predicate 係 `grader_code='PSA'`。**冇** `source_code`、**冇** `top_grade_label`、**冇** id-shape 檢查。

`[KNOWN]` 而且更衰：表嘅 unique key 係 `(variant_id, grader_code, source_code, observed_date)`，**包含 `source_code`**，所以同一個 variant 同一日可以有多個 source 嘅行 → 上面個 self-join **每個 variant 返多過一行**，而 `:2073-2081` 個 Python loop 係 `out[variant_id] = ...` 逐行覆蓋 → **最後生還嗰個值取決於 `fetchall()` 次序，係非決定性嘅**。

呼叫鏈 `[KNOWN]`：`cmd_scan_candidates`（`:2085`，`:2095` 用佢）← `cmd_daily`（`:2362`，`min_pop=1000`）同 CLI `scan-candidates`（`:2605`）。

`[KNOWN]` **顯示層冇錯**：`market_metric_history_acceptance`（43,414 行 / 762 variant）零 colon-eid；ranking 層亦乾淨。漏洞**只喺 universe admission**（`market_universe_member` / lock 39，2026-08-02 凍結，之後未再跑）。

`[KNOWN]` **`source_code='gemrate'` 唔係足夠 filter**：該 source 下有 3,064 行非 hex id + **116 行係 bare-40-hex 但 label 係 `top`**。必須 `label='10'` **同時** id 係 40-hex。

### 3.2 🔴 A1 — POP 表物理上放唔落兩個 GemRate entity

`[KNOWN]` `uq_market_grader_population(variant_id, grader_code, source_code, observed_date)` —— **冇** `external_entity_id`、**冇** `top_grade_label`。

`[KNOWN]` 實測飽和：56,116 行 = 56,116 個 distinct `(variant_id, observed_date)`。即係**每個 variant 每日永遠只有一行**。

`[COMPUTED]` 後果：
- 一個 variant 綁咗兩個 GemRate id（實測 **1,125 個 variant** 有超過一個 distinct eid）→ 第二個 entity 當日個數值喺**寫入嗰刻已經被 upsert 冚死**，冇留低。
- PSA `'10'` 同 PSA `'top'` 亦唔可以共存 → label 會喺同一格互相蓋。
- 實測 **507 次逐日 POP 下跌**（493 個 variant）。PSA population 唔可能跌 → 每一次下跌都係 entity 或 field 換咗。

實例 `[KNOWN]`：
```
variant 24（live、有排名），eid 全程都係 4597e12f…
  2026-07-29  label='10'   → 1130
  2026-07-31  label='top'  → 7135      ← 冚咗
  2026-08-01  label='10'   → 1134

variant 343（live）
  2026-07-30  snkrdunk:141410 → 31442  ← 但 source_code 寫住 'gemrate'
  2026-07-31  snkrdunk:141411 →  7650
```

`[COMPUTED]` **collapsed identity 嘅歷史喺 DB 入面已經冇咗，唔係「揀錯咗」係「冇咗」。** 所以：
- 全量重爬 GemRate 係**必須**，唔係優化。
- 任何「由 DB 推斷 split 應該點分」嘅做法都係**憑空作**。split label 要由 discovery 學返嚟。

**做法** `[FRAME]`：Stage 1 **一行都唔准寫入舊 population 表**；改寫新表 `market_gemrate_psa10_observation_v2`，unique key 用 `(gemrate_id, observed_date)`，加 CHECK 令佢**物理上收唔到** `top` label 或非 40-hex id（規格 §7.2）。

### 3.3 🔴 A2 — 價錢驗收鏈今日已經係死嘅

`[KNOWN]` §2.4 嗰組數：三條 current-price view 全部 **0 行**。

`[KNOWN]` `pipelines/new_era_db_tidy.py:3106` 讀 `operator_eligible_accepted_psa10_price_history` 去揀每張卡嘅 current price → 0 行 → `latest = {}` → `:3193` raise：
```
canonical language-routed price/GemRate POP coverage is 0/762
```

`[COMPUTED]` **即係話：live 嗰 762 張，用而家個 DB 重跑唔返出嚟。** 佢淨係靠 `market_canonical_metric_acceptance` 上一次成功寫入撐住。

`[KNOWN]` 隔離出嚟嘅原因：**只**拎走 `operator_strict_source_identity` 個 join，同一條 query 就返 **213,157 行 / 629 variant**。其他所有 predicate 都過。

`[COMPUTED]` **連帶危險**：任何寫成「有 pending 就唔准 activate」嘅閘，如果讀呢啲 view，會 **0 pending → 空過**。`operator_canonical_current_metric_projection = 0` 就係呢個形狀 —— 同 global CLAUDE.md 條 hard rule「任何有檢查但零 call site 都當冇檢查」係同一類病。

`[COMPUTED]` **真實工作量**（Codex 份收斂 plan 完全冇 model 到）：即使修好 `evidence.type` 個 bug，`catalog_source_identity` 只有 PC **349** / SNK **154** 係 exact，但 PC **483** / SNK **613** 係 `manual_review`。Stage 4 唔係「改六個工具出 manifest」，**係逐張卡把 ~1,096 條 manual_review 升做 exact**。呢個先係 036 嘅主體。

### 3.4 🔴 A2b — strict view 對非 gemrate 來源結構性咁空

`[KNOWN]` `operator_strict_source_identity` = 762 行，全部 gemrate；pricecharting / snkrdunk / 其他全部 **0**。

三個定義位 `[KNOWN]`：
- `pipelines/migrations/031_active_exact_identity_market_repair.mysql.sql:68` ← **唯一 commit 咗嘅版本**
- `pipelines/migrations/034_psa_source_identity_repair.mysql.sql:58` ← untracked
- `pipelines/migrations/035_gemrate_provenance_psa_identity_resolution.mysql.sql:83` ← untracked（live DB 反映呢個）

**兩個獨立死因，缺一都仍然 0。**

**死因 A — `match_status` 同 `source_product_number` 對非 gemrate 近乎互斥** `[KNOWN]`

| source | exact + 有 prodnum | exact + prodnum 空 | manual_review + 有 prodnum |
|---|---:|---:|---:|
| gemrate | **762** | 0 | 0 |
| pricecharting | **5** | 344 | 483 |
| snkrdunk | **8** | 146 | 612 |

`[KNOWN]` 根源喺 `pipelines/apply_verified_source_bindings.py:347-351`：`source_product_number` 由 `provider_claims["productNumber"] or ["cardNumber"]` 讀，而 PC 個 fallback dict（`:340-346`）將 `productNumber` 設成 `raw["expectedCollectorNumber"]`，`:343-344` 條 comment 自己都承認只係「page heading 見到」。

**死因 B — view 要求一個 DB 入面根本唔存在嘅字串** `[KNOWN]`

view 對 `source_code <> 'gemrate'` 要求：
```sql
JSON_UNQUOTE(JSON_EXTRACT(si.bind_evidence_json,'$.evidence.type')) = 'provider_payload'
```
`[KNOWN]` `provider_payload` 喺**成個 DB 出現 0 次**。實際寫落去嘅值：

| source | `evidence.type` | 行數 |
|---|---|---:|
| gemrate | `provider_native_psa_identity_and_population` | 762 |
| gemrate | NULL / `database_lineage` | 769 / 2 |
| pricecharting | `database_lineage` / NULL / `pricecharting_product_html` | 590 / 351 / 2 |
| snkrdunk | `database_lineage` / NULL | 738 / 325 |

`[KNOWN]` 即使修好死因 A，8 條僅存嘅非 gemrate 行嘅 `evidence.type` 係 NULL(7) 或 `pricecharting_product_html`(1)，一樣過唔到 → **仍然 0**。

`[KNOWN]` 生產者係 `apply_verified_source_bindings.py:379`（legacy schemaVersion 1 路徑）同 `:894`（v2 路徑，contract `active-762-exact-identity-repair-031-v1`）。legacy 路徑個 `evidence_claim`（`:360-380`）**根本冇 `evidence` key**，所以佢寫嘅每一行 `$.evidence.type` 都係 JSON NULL。

**做法決定：修生產者，唔係放寬 view** `[FRAME]`。理由 `[COMPUTED]`：
- 放寬 `$.evidence.type` 會即刻放返 034 特登降級嗰批入嚟：PC 483 + SNK 612 = **1,095 條** `manual_review` + `database_lineage` 行（全部 `updated_at` = 2026-08-07 14:08:14）。生產者自己已經拒（`:692` raise `decision_database_lineage_is_not_provider_native`），validator 亦有 `strictDatabaseLineageZero` invariant。放寬 = 等於刪咗呢個 invariant。
- 另一批候選（PC 348 + SNK 154 `exact`）個 `bind_evidence_json` **完全係 NULL**，冇嘢可以「放寬去」。硬收 = 憑空作證據。

### 3.5 🔴 A3 — `opaque_id` 比 printing hash 弱

`[KNOWN]` `opaque_id()`（`pipelines/g10_public_snapshot.py:230`）只 hash 5 個 field：`(tcg, language, set_name, collector.normalized, canonical_name)`。
`[KNOWN]` `printing_sha()`（`pipelines/resolve_active_psa_identity.py:56`）hash 10 個，包括 `printing_code, rarity_code, edition_code, parallel_code, finish_code`。

`[COMPUTED]` **split 嘅定義就係靠 parallel／finish／edition 分**，所以一次 split 會：過到 `uq_catalog_printing_identity_sha`，但**撞死** `uq_catalog_variant_opaque`。

`[KNOWN]` 現時已經有 **21 組（49 行）** 呢種形狀，例：
```
One Piece / Carrying On His Will / OP13-119
  → 1565   ~     ~      ~      manga
  → 1464   ~    sec    foil     ~      sp
  → 1467   ~    aa     foil     ~      aa
```
`[KNOWN]` 而且有 **42 組** variant 嘅 `canonical_name` 完全一樣，所以「靠名分開」唔一定救到。

`[KNOWN]` 重算 `opaque_id()` 對 1,782 行，**只有 272 對得返，1,510 已經飄咗** —— 因為 034 用 PSA description 覆蓋咗 `canonical_name` 但凍結咗 `opaque_id`。

`[KNOWN]` `opaque_id` 係 FE 公開 URL：`apps/web/src/lib/live-db-snapshot.ts:435`、`/card/[id]`、`/api/og/card/[id]`、`sitemap.ts`。
`[KNOWN]` 而且有兩個 hard 閘釘住佢：`pipelines/converge_printing_identity.py:127`（註明「一個 opaque_id 都唔准變」，`:130-141` 前後對 fingerprint）同 `pipelines/db_runtime.py:747`。

**→ D7**：現有 opaque_id 釘死；新／split 出嚟嘅由 **printing sha** 派生。呢個要喺 Stage 2 **之前**寫好，唔係撞到 duplicate key error 先算。

### 3.6 B — `'complete'` / `'completed'` 係故意分開

`[KNOWN]` 冇任何 `{'complete','completed'}` 常數存在。

6 個 writer 寫 `'complete'`：`db_runtime.py:1302`、`fx_db_load.py:105`、`g10_identity_expand.py:588`、`ingest_snk_trades_sales.py:346`、`qualified_pool_operator.py:773`、`snk_market_data.py:1223`。
16 個寫 `'completed'`：`c11_pc_sold_ingest`、`collect_control`（×2）、`editorial_locale_sync`、`g10_*`（×7）、`pc_psa10_price_materialize`（×2）……

`[KNOWN]` 4 個讀取點**只認** `'complete'`：`market_alerts.py:604`（揀最新 run）、`db_retention.py:215`、`db_retention.py:259`、`db_runtime.py:1006`（idempotency short-circuit）。

`[KNOWN]` `pipelines/g10_research_ingest.py:30-36` 明文寫住 G10 家族寫 `'completed'` 就係為咗**唔遮蓋** `market_alerts.py:604` 個 latest-run 查詢。

`[COMPUTED]` → 統一呢 4 個讀取點會改變佢哋揀邊個 run，**係行為改變唔係 cleanup**。036 新 code 可以用自己嘅常數，**唔准套落現有 4 個位**。押後 037。

### 3.7 B — PC / SNK 時間戳唔喺同一個鐘

`[KNOWN]` 實測儲存習慣：

| source | `effective_at` 形態 |
|---|---|
| `snkrdunk` | **每一行**都係 `TIME() = 23:59:59`（人手砌 end-of-day），最新 `2026-08-07 23:59:59` |
| `g10_kline` | 同樣 23:59:59 |
| `pricecharting` | 真抓取時間，最新 `2026-08-07 09:47:30.725687`；最舊 `2021-01-01 07:00:00`（US/Pacific 午夜 render 成 UTC） |
| `snk_psa10` | 10,508 行 `observed_date <> DATE(effective_at)` |
| `ebay` | 6,495 行入面 4,504 行對唔上 |

`[KNOWN]` 同 variant 同日 PC vs SNK 重疊 **3,289 次，SNK 個 `effective_at` 較新有 3,128 次（95%）** —— 純粹因為個 23:59:59。

`[KNOWN]` 現有 code 做緊嘅係**相反**：`new_era_db_tidy.py:3117` 個 winner key 第一個元素係 `price_route_priority`（`card_language='en'` 時 PC=10、SNK=20），所以 EN 卡 PC 永遠贏，唔理新舊。實測 **85 張 live EN 卡**現時用緊嘅 PC 價比可用嘅 SNK 價舊；431 條 accepted PC 價全部落喺同一個 `observed_date = 2026-08-01`。

`[COMPUTED]` → 「effective_at 較新贏」會把 95% 同日重疊判俾 SNK，同現有 code、同 `cardz-psa10-price` skill 寫嘅「EN 主線 PC」全部相反。**owner 2026-08-08 改成 D4 語言路由。**

### 3.8 B — 圖嘅閘量緊另一樣嘢

`[KNOWN]` Pipeline 閘（`new_era_db_tidy.py:3395`、`:3587`、`:3591`）數 `img.canonical_image_acceptance_id IS NOT NULL`（定義喺 `migrations/026_…sql:915`）並 assert `== 762` → **過**。
`[KNOWN]` 但 3800 而家有 **133 張 placeholder**。

`[KNOWN]` FE 額外要求（`apps/web/src/lib/live-db-snapshot.ts:228` primary + `:233-261` fallback，`:322-330` merge）：
1. 該 asset 最新一行 `market_image_qc` 要 `public_allowed=1 AND raw_front_confirmed=1 AND card_number_match=1 AND language_match=1 AND tcg_match=1`
2. `semantic_match_status IN ('human_or_vision_confirmed','accepted_freeze')`
3. 冇被更新嘅 `market_canonical_image_acceptance` supersede
4. **隻 webp 真係喺 `data/public/market-assets/<sha>.webp`**

`[KNOWN]` asset 檔數：呢個 checkout **8,121** 個、舊 checkout **14,887** 個。

`[COMPUTED]` → Stage 5 個閘必須**行 FE 自己嗰兩條 query + 查檔案系統**，唔可以用 `canonical_image_acceptance_id IS NOT NULL`。而且新圖要 materialize **入呢個 checkout**（每卡 3 個 webp）先至可以 activate。

### 3.9 B — Upsert 會復活已 quarantine 嘅價 + 改寫歷史 run_id

`[KNOWN]` `pipelines/snk_market_data.py:1189` bound literal 係 `'ready'`，`:1202-1212` 個 `ON DUPLICATE KEY UPDATE` 有 `metric_status = VALUES(metric_status)`。
`[COMPUTED]` → **重跑一次 SNK kline，會靜靜地解封 034 quarantine 咗嘅每一行。** 呢個比 run_id 問題嚴重。

`[KNOWN]` 同一個 upsert 亦重蓋 `run_id`。run 5628 嘅 8,010 行入面：
- `market_source_observation` 8,010 行全部 `created_at` = 2026-08-07
- `market_price_observation` 8,010 行入面**只有 41 行**係 2026-08-07 新增；**7,969 行**係舊行（created_at 分佈 07-23 起，08-05 佔 7,956）被 upsert 改咗 `run_id`，id 低至 137，日期追到 **2023-06-20**
- 結果 `metric_status`：quarantined 4,189 / ready 3,821，**同 run header 寫嘅 `accepted_count=8010, quarantined=0` 矛盾**

`[KNOWN]` **run 5628 已破案，唔使再查**：run 喺 `2026-08-07 07:01:25` 開始；`cardz_migration_ledger` 顯示 034 喺 **`2026-08-07 14:07:57`**、035 喺 `14:39:13` 先落。即係 run 跑嗰陣仲係 031 版 view，當時 snkrdunk exact 行**係收嘅**。寫手就係 `snk_market_data.py`（`:1002` 砌 run_key `snk_kline_ingest_%Y%m%dT%H%M%SZ`、`:1065` hardcode `'snk_psa10'` 入 run 表、`:1161` 用 `'snkrdunk'` 寫 observation —— source_code 對唔上係設計如此）。
`[COMPUTED]` 4,189 quarantined 係 034 喺 14:08 追溯 quarantine 咗 07:01 寫入嘅行。run header 個 count 喺當時係啱嘅。

**→「刪除 old-exact 旁路」呢個 action item 冇嘢可刪。真問題係上面兩個 upsert 行為。**

### 3.10 B — `snk_psa10` / `snkrdunk` 兩個 code 一個源，FE 揀咗舊嗰個

`[KNOWN]` `uq_market_price_daily(variant_id, source_code, observed_date)` 容許兩個 code 同日共存。FE 兩個都 map 做 `'snkrdunk'`（`live-db-snapshot.ts:203`、`:272`）。

| 指標 | 數 |
|---|---:|
| `(variant, day)` 兩個 code 都有行 | **118,926**（527 個 variant） |
| 其中兩邊價錢**唔同** | **1,539** |
| 兩行**都入咗** `market_metric_history_acceptance` | **118,270** |

`[KNOWN]` `uq_metric_history_source` 建喺 `source_record_id` 上，所以 acceptance 層**冇能力**去重。
`[KNOWN]` FE 決勝：兩者 priority 都係 0 → `live-db-snapshot.ts:364` `if (point.priceUsd !== null && point.priceSourcePriority <= sourcePriority) continue` 保留**第一行**，而 `:277` `ORDER BY effective_at` **ASC** → **最舊嗰個贏**，直接餵 `nearestPrice` → `changePct` / `marketCapChangePct`。

### 3.11 其他已確認缺陷

| # | 內容 | 證據 |
|---|---|---|
| **3.11a** | image rejection 表**冇** `canonical_printing_sha256`，Stage 2 之後重 key 唔到 | `[KNOWN]` `market_image_rejection_registry` 欄位只有 `variant_id, content_sha256, first_image_asset_id, rejection_reason, decision_code_sha256, review_run_id, rejected_at`；43 行（36 live / 7 非 live）。`market_image_review_approval`（1,028 行）**有** `canonical_printing_sha256` + `expected_language` |
| **3.11b** | 重跑 activation 內容一樣就唔會郁 FE | `[KNOWN]` `new_era_db_tidy.py:3205` 由 member list 內容派生 sha，`:3257` `ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)` **唔更新 `accepted_at`**；FE 靠 `MAX(accepted_at)` 揀（`live-db-snapshot.ts:183-189`） |
| **3.11c** | migration runner 中途死 = 半生不熟 | `[KNOWN]` `db_runtime.py:408-451` 得一個 `connection.commit()` 喺 loop **外面**（`:450`）；MySQL DDL implicit-commit。035 已有地雷：`DROP INDEX uq_catalog_psa_raw_payload`（`035:5-7`）重播即爆 |
| **3.11d** | `split_sql` 逢 `;` 就切 | `[KNOWN]` `db_runtime.py:337-339` 只剝整行 `--` comment，唔識 string literal / `DELIMITER` |
| **3.11e** | 第二個 migration applier 唔查 ledger | `[KNOWN]` `scripts/canonical_seed.py:233` glob + apply，完全冇 consult `cardz_migration_ledger` |
| **3.11f** | `--resume` 會永久跳過退化嘅卡 | `[KNOWN]` `gemrate_source.py:388-391` `_has_complete_public_card_capture` 對 `rawStatus == 'dom_evidence_only'` 返 True，但嗰個狀態只有 `domSha256`、冇 provider JSON 可以 replay。該狀態由 `:344` 喺 `populationMode == 'dom_labelled_fallback'` 時產生 —— keyless Playwright 被限速時就係咁。現時 cache 乾淨（762 個 dir 全部 `captured`），所以係**潛伏**唔係已發生 |
| **3.11g** | `public-card-dump` 冇 429 backoff | `[KNOWN]` 該路徑係 keyless Playwright；`_api_get:130-152` 個 bounded retry 只服務 keyed API 命令。`_run_harvest:1109` 見到 `*_429` 就 abort 成個 run。chunk size `25` 寫死喺 `:1689`，`cmd_public_card_dump:1771-1776` 冇傳落去，CLI 改唔到。（`:82` 條 comment 講「429 backoff = 5x delay」係假嘅，`:137-139` 冇 5x） |
| **3.11h** | REVOKE 唔會斷已開連線 | `[COMMON]` MySQL db-level grant 下次連線／`USE` 先生效。`[KNOWN]` `cardz@%` = `GRANT ALL ON cardz_market_cap.*` + 兩個 `cardz_migration_verify_*` schema，**冇 global 權限**，讀唔到 `mysql.user`（error 1142） |
| **3.11i** | 仲有一個 run 掛住 `running` | `[KNOWN]` `market_ingest_run` id **3568**，`cardz_normalized`，`started_at 2026-08-01 05:48:01`，未 close |
| **3.11j** | FE 每個 request 重建成份 snapshot | `[KNOWN]` `GET http://127.0.0.1:3800/` = **5.151s**。`priceRows`（`live-db-snapshot.ts:270-278`）**無日期窗**，257,206 行 / 2.68s。所有 route `force-dynamic`。`populationRows`（`:279-289`，43,414 行）攞完喺 `:429` 掉咗，**從來冇用過** |
| **3.11k** | watchlist 卡死 rank 101..300 | `[KNOWN]` `apps/web/src/lib/server-snapshot.ts:112`。`[COMPUTED]` 1,146 張之後 **846 張唔會出現喺任何 list surface**，但 `sitemap.ts` 照出全部 URL |
| **3.11l** | 寫死 `762` 約 20 個位 | `[KNOWN]` `operator_control.py:30 TONIGHT_CARD_COUNT = 762`、`:1460`、`:1712`；`new_era_db_tidy.py:727/760/2029/2129/2280/2342/2743/2911/3193/3599`，加 `:3583-3593` **11 條 `!= 762` 塞喺同一個 `if`**；`resolve_active_psa_identity.py:296`；`apply_verified_source_bindings.py:732` 合約字串 `active-762-exact-identity-repair-031-v1`。FE 乾淨（`apps/web/src` 零命中） |

### 3.12 Codex 紅隊收斂版錯咗嘅 7 條（保留作記錄）

> 呢節唔係為咗鬧人，係防止下一輪紅隊重新提出同樣嘅錯 action item。

| Codex 講 | 實測 | 影響 |
|---|---|---|
| 由 commit `66c2d91b` **復原**被刪工具 | `[KNOWN]` `66c2d91b` **唔係 HEAD 祖先**，係 divergent branch `ui-experiments-20260725` 個 tip。merge-base `18b5da65`，HEAD 行前 8 個、佢行前 3 個。`git log --diff-filter=D` 對六個 path 全部**空** —— 零 deletion commit | 「復原 / revert」講法錯。正路係 `git show 66c2d91b:<path>` 或喺舊 checkout 抄 |
| `run 5628` 行**舊 exact 旁路**，要刪咗條旁路 | `[KNOWN]` 見 §3.9：係 migration 時序，唔係旁路。`snk_market_data.py:549-599` 本身**係**行 strict view，`:1091-1103` 仲有 fail-closed re-assert（只會 raise，唔會放行） | **冇嘢可刪**。真問題係 upsert 行為 |
| PC parser 要改去認 `VGPC.chart_data.manualonly` | `[KNOWN]` parser **已經啱**：`pricecharting_page_parse.py:219` 讀 `vgpc["chart_data"]` dict、`:265` 攞 `manualonly` key、`CHART_LABELS:28` map 去 `("PSA 10","manual-only-price",7,"completed-auctions-manual-only")`。而 `VGPC.chart_data.manualonly.last` 係存落 `payload_json.$.field` 嘅**出處標籤**（實測 `.series` 18,244 行 / `.last` 894 行 / NULL 1,765 行），仲係 `operator_eligible_accepted_psa10_price_history` 嘅**硬字串合約** | **唔准改**，改咗爆 7 個讀取點 |
| PC/SNK 雙 exact 時 `effective_at` 較新贏 | `[KNOWN]` 見 §3.7。snkrdunk **每行** 23:59:59，同日重疊 3,289 次 SNK 贏 3,128 次（95%） | 已由 owner 改成 **D4 語言路由** |
| prune 跨 **50 張表**，要改 `ON DELETE SET NULL` | `[KNOWN]` **35 張 / 36 條 FK**，全部 `NO ACTION`；全 schema 76 條 FK **零** CASCADE / SET NULL | 「改成 SET NULL」= drop+recreate FK，而 runner 冇 per-statement transaction。改用「先轉 stable key → 手動 UPDATE NULL → 再刪」 |
| `db_runtime.py` 出一個 `{'complete','completed'}` 常數 | `[KNOWN]` 見 §3.6，係故意分開 | 新 code 用新常數得，**唔准套落現有 4 個讀取點** |
| 排程 10 個全部 Disabled | `[KNOWN]` **14 個**。多咗 `PCFullShard2`、`PCFullShard2Test`、`PCFullShard2Watch`、`PCS2Keep` —— **淨係靠 action path 先認得出**，全部指去舊 checkout。14/14 Disabled | 停機證明要用 **action path** 對，唔可以淨係對 task name |

另加 3 條細修正 `[KNOWN]`：
- `gemrate_brute_harvest.py` 喺 `pipelines/` 唔係 `tools/`
- `scripts/validate_psa_identity_repair.py` **完全冇 argparse**（`main()` 零參數），加 flag 唔係加兩行
- `public-card-dump` 見 §3.11g

### 3.13 Codex 收斂第二輪處置（2026-08-08b，Fable 審）

**吸收 8 項**：兩層 cohort（§0.1／D10）、activate→prune 次序修正（§6.0 stage 表）、runner DDL-first ledger-last（§0.8）、unfreeze＋grants 失敗路徑保證（§4 Gate 0.4）、freshness window（§6.0）、EXECPLAN-037 改名歸檔（§4 Gate 0.1）、V2 incremental canary（S14）、FE02 拍板（§6.8）。

**拒收 4 項**（有實證反對，防止下輪紅隊重提）：

| Codex 提議 | 拒收理由 |
|---|---|
| 所有新物件放入單一 036 migration file | `[KNOWN]` §3.11c/d：runner 冇 per-statement transaction、`split_sql` 逢 `;` 切、8.4 冇 `ADD COLUMN IF NOT EXISTS` → ALTER 必須每檔一個；strict view 修訂必須遲過 S6（§7.4 排序約束），單一大檔做唔到 |
| PC／SNK price observation 全套改 append-only V2 | `[COMPUTED]` 病灶只係兩個 upsert 行為＋`snk_psa10` 正規化（§3.9／§3.10），6 個 writer patch 解決並重用成熟 harvester；全套 V2 = 重寫晒 writer DB 層，YAGNI。GemRate POP V2（§7.2）先係結構性必須 |
| 「同一 printing **歷史** POP 倒退 = 0」做 seal 條件 | `[KNOWN]` §3.2：507 次下跌嘅被冚值已經冇咗，legacy 永遠達唔到零倒退 → invariant 只限 V2 series；legacy 只入 quarantine lineage，唔入 authority |
| 「active **125** 個名稱／printing／language 衝突」 | `[GUESS]` 全份實測數字搵唔到出處（實測：17 個 multi-id variant、PC 2＋SNK 40 conflict）。未 re-query 對數唔准入執行文件 |

`[COMPUTED]` 未解主宰項照舊：~1,096 條 manual_review 升 exact 嘅工作量（§12.2）。prepare 開工第一件事係抽 100 條做唯讀 sample，量度可自動由 provider payload 派生 exact 嘅比例 — 個比例決定 036 係幾日定幾星期。

---

## 4. Gate 0 — 地基

> 順序係硬嘅，每步 gate 住下一步。**0.1 之後每步另攞 go-ahead。**

### 0.1 還原點（唯一一步已批准）

`[KNOWN]` 現況：`git status --porcelain` 16 條，**整套 034/035 地基全部 untracked**：
```
 M PROJECT_STATE.md
 M README.md
 M pipelines/apply_verified_source_bindings.py
 M pipelines/gemrate_source.py
 M pipelines/new_era_db_tidy.py
?? .agent/EXECPLAN-037-psa-identity-chain-rebuild.md
?? PLAN_036_FE02.md
?? data/editorial/psa-identity-sheet70-034.json
?? data/private/gemrate/                      ← 5,331 檔 / 12.2 MB
?? data/private/pricecharting_session/        ← 442 檔 / 281 MB
?? pipelines/__pycache__/
?? pipelines/migrations/034_psa_source_identity_repair.mysql.sql
?? pipelines/migrations/035_gemrate_provenance_psa_identity_resolution.mysql.sql
?? pipelines/psa_identity_repair.py
?? pipelines/resolve_active_psa_identity.py
?? scripts/validate_psa_identity_repair.py
```
`[KNOWN]` `data/private/**` **冇被 gitignore**（`git check-ignore` 返空）→ **絕對唔准 `git add -A`**，要逐個檔加。

步驟 `[FRAME]`：
1. private cache 打 tar 放 **repo 外**（`C:\Users\jackson0202\cardz-036-foundation-backup\`），記 sha256
2. `git checkout -b rebuild/036-foundation`
3. ⬅036b：`.agent/EXECPLAN-037-psa-identity-chain-rebuild.md` 先改名 `.agent/EXECPLAN-034-035-psa-identity-chain-rebuild.archived.md`（內容係 034/035 已完成紀錄，「037」個名同 release 命名相撞 — 解決 §12.10）。然後逐個 `git add`：2 個 migration、3 個 py、fixture、5 個 modified、`PLAN_036_FE02.superseded-20260808.md`、本檔、改名後嘅 archive
4. commit
5. 驗：`git show <sha>:pipelines/migrations/035_*.sql | head -5` 讀得返；`git show <sha>:PLAN_036_FE02.superseded-20260808.md | wc -l` = 916；`git status --porcelain` 只剩 `data/private/**` 同 `__pycache__`

`[COMPUTED]` 理由：migration 036 要**第四次**改寫 `operator_strict_source_identity`，035 唔 commit 就冇 base 可以 diff。而且舊 916 行 plan 一旦覆蓋就永久冇。

### 0.2 備份 + **真 restore 驗證**

`[KNOWN]` 主機**冇** `mysqldump`；container 內有 8.4.10。所有 table 都係 InnoDB，`--single-transaction` 有效。DB data dir 14 GB，C: 剩 291.7 GB。

```bash
docker exec cardz-market-cap-db-1 sh -c \
  "mysqldump --single-transaction --routines --triggers --hex-blob \
   --set-gtid-purged=OFF -u root -p\"\$MYSQL_ROOT_PASSWORD\" cardz_market_cap \
   --result-file=/tmp/cardz-036-pre.sql"
```
```bash
docker cp cardz-market-cap-db-1:/tmp/cardz-036-pre.sql C:/Users/jackson0202/cardz-036-foundation-backup/
```

`[FRAME]` 唔用 PowerShell 文字 redirect（encoding 會爛）。密碼由 container 環境變數攞，**唔准貼落任何 log 或 commit**。

驗證（缺一不可）`[FRAME]`：
1. dump sha256 記低
2. 檔尾有 `-- Dump completed`
3. **真 restore 落 throwaway schema**（root 開，`cardz@%` 開唔到新 schema），逐個 base table 對 exact row count，55 張全對
4. view 數對返 17
5. 驗完 drop throwaway schema

`[COMPUTED]` 未 restore 過嘅 dump 唔算備份。

### 0.3 停機證明（唔准靠聲稱）

`[KNOWN]` 14 個 task，其中 4 個**淨係 action path 認得出**：

| Task | 認法 | State |
|---|---|---|
| `cardz-beta-daily-refresh`、`cardz-beta-hourly-refresh`、`CARDZ-Freeze-Sweep-Guard`、`CARDZ-GemRate-PSA10-Watchlist`、`CARDZ-Market-Cap-Daily`、`CARDZ-Market-Cap-Watchdog`、`CARDZ-Nightly-Bake`、`CARDZ-TAG-Daily-Capture`、`CARDZ_PC_Shard1_Watchdog`、`JACKZCardzWhatsAppTunnel` | name | Disabled ×10 |
| `PCFullShard2`、`PCFullShard2Test`、`PCFullShard2Watch`、`PCS2Keep` | **action Execute path**（全部指去舊 checkout） | Disabled ×4 |

`[KNOWN]` WSL 冇 cardz cron / timer（crontab 只有 Hermes 嘢；13 個 systemd timer 全部係 Ubuntu stock + chatwoot-backup）。

證明做法 `[FRAME]`：`schtasks /query /fo LIST /v` 全表輸出，按上面 14 個 name **加** action path 過濾，每個要見到 `Scheduled Task State: Disabled` + `Next Run Time: N/A`，成份 capture hash 入 preflight receipt。**每次 resume 都要重驗**（task 可以喺兩次 run 之間被人 enable 返）。

### 0.4 Writer freeze — 用 MySQL 權限，唔用 Python lock

`[COMPUTED]` Python lock 喺呢個 repo 證明唔到：至少三個獨立 connection factory（`db_runtime.connection_from_args:379`、`qualified_pool_operator.db()`、`snk_market_data._db_connect()`）加 `scripts/canonical_seed.py`。喺其中一個加 guard，另外幾個照繞。

`[KNOWN]` 但**所有** writer 都用同一個帳戶（`CARDZ_DB_USER`）：`db_runtime.py:392`、`db_retention.py:194`、`c11_pc_sold_ingest.py:76`、`ingest_snk_trades_sales.py:58`、`snk_market_data.py:506`、`qualified_pool_operator.py:94`、`pc_ungraded_reference_ingest.py:58`、`scripts/canonical_seed.py:108`。→ 降權係啱嘅槓桿。

順序 `[FRAME]`：
1. root：`CREATE USER 'cardz_rebuild'@'%'`；`GRANT ALL ON cardz_market_cap.* TO 'cardz_rebuild'@'%'`
2. root：`REVOKE INSERT,UPDATE,DELETE,CREATE,DROP,ALTER,INDEX,REFERENCES ON cardz_market_cap.* FROM 'cardz'@'%'`；`GRANT SELECT`；`FLUSH PRIVILEGES`
3. root：`KILL` 晒 `information_schema.PROCESSLIST WHERE user='cardz'`，再驗返 0 行 ← **呢步唔做，已開連線照寫**（§3.11h）
4. **證明**：`scripts/prove_writer_freeze.py` 用**未改過嘅 `backend.env`** 以 `cardz` 身份連，喺一個會 rollback 嘅 transaction 入面試 `INSERT`，**expect error 1142**；INSERT 成功就 exit 1
5. Migration DDL 用 `cardz_rebuild` 跑（唔係 root、唔係 `cardz`）。`backend.env` **一個 byte 都唔准改** —— 留住佢指向已降權嘅帳戶，正正就係令個 freeze 證明得到嘅嘢。rebuild credential 放 `data/runtime/config/rebuild.env`，只經新 flag `--credentials-env` 傳
6. Teardown（還原 `cardz` grant、`DROP USER cardz_rebuild`）係一個有記錄、idempotent 嘅步驟，⬅036b：**成功定失敗路徑都必須行到** — run abort／crash 之後由 resume 收尾，或者人手行新 subcommand `rebuild-036 unfreeze --generation <id>`（可審計緊急解除 freeze＋還原 grants，**唔切 generation**）。唔做就成個系統一直 read-only

`[KNOWN]` 3800 降權後仍然行到：`live-db-snapshot.ts` 全部 SELECT，`/api/health` 返 `{"status":"ok","cards":762,…,"databasePort":3308}`。

`prove_writer_freeze.py` 嘅 call site（`[FRAME]`，貫徹 global CLAUDE.md「有檢查但零 call site 當冇檢查」）：
1. `rebuild-036` stage S0
2. S12 `activate` 開頭
3. S13 `prune-apply` 開頭（⬅036b 次序修正，見 §6.0）

另加 delta probe：S0 記低 `market_source_observation` / `market_price_observation` / `market_sale_observation` / `market_grader_population_observation` / `market_ingest_run` 嘅 `MAX(id)` 同 `catalog_source_identity.MAX(updated_at)`，每個 stage boundary 重驗；有唔屬於本 generation 嘅移動就 abort。

### 0.5 收拾殘留 run

`[KNOWN]` `market_ingest_run` id 3568（`cardz_normalized`，`running`，2026-08-01 05:48:01）仍然開住。喺 freeze **之前**標 `aborted` + 註明原因，唔好等 freeze 之後先發現然後賴落 freeze 度。

### 0.6 Snapshot 人手決定嘅橋

`[COMPUTED]` `market_image_rejection_registry`（43 行）冇 `canonical_printing_sha256`，唯一橋樑係 `catalog_printing_identity`，**而 Stage 2 會改寫佢**。

`[FRAME]` Gate 0 就要 dump `variant_id → canonical_printing_sha256` 落 `data/policy/printing-sha-snapshot-<gen>.json`，之後 rejection 靠佢重 key。**唔可以等到 Stage 2 之後。**

### 0.7 圍住第二個 migration applier

`[FRAME]` `scripts/canonical_seed.py:233` 加 hard raise，除非 `--allow-unledgered-migrations`。零風險，防止 seed run 冚咗 036 個 view。

### 0.8 修 migration runner（喺用佢之前）

`[FRAME]` 獨立一個 commit，先修好再用：
- 每個檔：**先行 DDL → 最後先寫 ledger row → per-file commit**（唔係全部行完先一次 commit）。⬅036b 修正：原「先 insert ledger」係錯方向 — MySQL DDL implicit commit 令 ledger row 喺檔案半路死嗰陣已經 commit 咗，下次 run 就 skip 一個未完成嘅檔，而「ledger 有但 object 冇」呢個方向 `--repair-ledger` 補唔到。DDL-first 嘅前提係 036 檔全部 idempotent（§7.1 硬約束），半路死 replay 係安全嘅
- `--repair-ledger`：只可以喺 information_schema 顯示所有 object definition 逐項等於 036 spec 時先准補 ledger
- 用一個 no-op migration 實測行得通；runner failure 測試用 throwaway schema＋throwaway migration directory，唔佔 production migration number

`[FRAME]` 036+ 嘅硬約束：
- 只准 `CREATE TABLE IF NOT EXISTS`、`CREATE OR REPLACE VIEW`、`INSERT IGNORE`
- MySQL 8.4 **冇** `ADD COLUMN IF NOT EXISTS` → **每個 `ALTER` 自己一個檔**
- 任何 string literal 入面唔准有 `;`（§3.11d）

### 0.9 五條 view 非零 assert

`[FRAME]` Gate 0 最後一步：assert §2.4 五條 view 喺**修完之後**返非零行。今日全部 0（§3.3），所以呢個 assert 而家一定 fail —— 咁先啱，佢係 gate 唔係裝飾。

---

## 5. Gate 1 — 工具回收

`[KNOWN]` 呢六個檔只存在於舊 checkout（branch `ui-experiments-20260725`，HEAD `66c2d91b`）：

| 檔 | 用途 |
|---|---|
| `tools/expand_gemrate_ids_full.py` | GemRate id universe 展開 |
| `pipelines/pc_full_serial_driver.py` | PC 連續抓取 driver |
| `pipelines/pc_full_shard_runner.py` | PC 分片 runner |
| `scripts/pricecharting_capture_and_parse.py` | PC capture + parse |
| `scripts/pc_full900_supervisor.ps1` | PC 900 監督 |
| `tools/harvest_pc_images_full900.py` | PC 圖收集 |

`[KNOWN]` 另有 `pc_saved_identity_bind.py`、`bind_snk_watchlist.py`、`gemrate_history_ingest.py` 亦只喺舊 checkout。**呢三個唔恢復做 writer。**

`[FRAME]` 回收方法：`git show 66c2d91b:<path> > <新 checkout 路徑>` 或直接抄檔。**唔係 revert、唔係 cherry-pick**（§3.12 第 1 條）。抄完喺 commit message 記低來源 sha。

`[FRAME]` 改動範圍：
- `expand_gemrate_ids_full.py`：由「只讀 exact binding」擴到合併 current + rejected + manual_review + legacy observation + 兩個 checkout 嘅 raw cache + full discovery id
- PC full scripts：保留 CDP、分片、resume、本地 HTML 重用；**移除** fuzzy auto-bind 同直接寫 DB exact；只輸出 provider-native evidence manifest
- `harvest_pc_images_full900.py`：由舊 `match_status='exact'` 改讀 `operator_strict_source_identity`；只產 candidate，promotion 照走現有 acceptance / freeze

### Gate 1 驗收（`[FRAME]`，唔准用「個工具行到」當驗收）

> 揀一個已知 PC product，用回收咗嘅工具 replay 一次，然後 assert 佢**出現喺 `operator_eligible_accepted_psa10_price_history`**。

`[COMPUTED]` 理由：呢條 view 對 pricecharting 有一串硬字串合約（全部要滿足）：
```
p.source_priority = 95
so.observation_kind = 'psa10_price_guide'
payload_json.$.source    = 'pricecharting'
payload_json.$.sourceUrl LIKE 'https://www.pricecharting.com/%'
source_external_entity_id REGEXP '^[0-9]+$'
且 (contract, method, field) 命中以下其一：
  ('pc_psa10_current_price_v1', 'pricecharting_explicit_psa10_field_v1',   'VGPC.chart_data.manualonly.last')
  ('pc_psa10_local_history_v1', 'pricecharting_explicit_psa10_history_v1', 'VGPC.chart_data.manualonly.series')
```
`[KNOWN]` DB 實有 894 + 18,244 行命中，另有 1,765 行 `legacy-price-observation-lineage-v1`（`field=NULL`）**永久唔合資格**。
`[COMPUTED]` 六個舊工具 pre-date 呢條 view。唔驗到底，價會落到 `market_price_observation` 但成條 acceptance 鏈**睇唔到**，冇 error、accepted = 0，一直去到 Stage 6 先發現 → 成個 cache replay 白做。

---

## 6. Stage 1–6

> 每個 stage：input / output / resume 粒度 / 完成定義 / 唔准做乜。

### 6.0 Orchestrator surface

`[KNOWN]` `operator_control.py` 現有 subcommand：`status`、`export-gaps`、`accept-binding`、`export-operator-snapshot`、`export-product-subset`、`snk-image-priority-status`、`promote-product-subset`、`freeze-active-sources-from-checkpoints`、`scan-candidates`、`daily`、`db-tidy`（`add_subparsers` 喺 `:2509`，dispatch `:2573-2618`）。**冇** `rebuild`、`--generation`、`--resume`。

`[FRAME]` 新增 `rebuild-036`（parser 加喺 `:2553` 附近，dispatch 加喺 `:2612` 附近）：
```
--generation <id>          必須，格式 ^036_\d{8}T\d{6}Z$（⬅036b：統一底線，同 §6.7「036_<UTC>」一致）
--resume
--stage <name>
--invalidate-from <stage>
--force-stage
--dry-run
--credentials-env <path>
```
**Activation 係另一條 subcommand**，`rebuild-036` 永遠唔會自己行 S13。

`[FRAME]` **唔新增** Codex 提議嘅 `catalog_rebuild.py` / `validate_catalog_rebuild.py` 兩個 wrapper —— 擴現有入口，唔另起爐灶。

Checkpoint 放 DB 唔放檔（`[FRAME]`，因為要改嘅就係 DB，放 DB 先有 per-stage 原子性）：
```sql
cardz_rebuild_generation(
  generation_id PK, created_at, policy_sha256, min_pop,
  admitted_count, activated_at, activation_receipt_sha256)

cardz_rebuild_checkpoint(
  generation_id, stage, status, attempt, input_sha256, output_sha256,
  counts_json, started_at, finished_at, error_code,
  PRIMARY KEY (generation_id, stage))
```
`[FRAME]` 用自己嘅常數 `REBUILD_STAGE_COMPLETE = "complete"`，**唔掂** `market_ingest_run` 嗰個分裂（§3.6）。

Resume 語義 `[FRAME]`：
- `--resume` 由第一個唔係 `complete` 嘅 stage 開始，喺該 stage 內部再用自己嘅 resume 粒度
- `failed` 嘅 stage 要 `--force-stage` 先重跑 —— **失敗唔准靜靜 retry loop**
- 某 stage 重算出嚟嘅 `input_sha256` 同記錄唔同 → 該 stage **同所有下游**失效，印出嚟，要 `--invalidate-from` 明示

Freshness（⬅036b，併自 Codex）`[FRAME]`：
- S12 `activate` 開始時，POP 同 current price captures 距離「該來源 capture **完成**時刻」唔准超過 **72 小時**（flag `--freshness-hours`，owner 可改。Codex 原提 36h，但 keyless Playwright 全量 crawl＋429 ladder 本身可能行超過 36h，所以計時起點定喺 capture 完成、預設放寬到 72）
- identity／image evidence：entity、URL、content hash 完全一致就可以較舊
- 超窗 → 只重抓超窗嗰部分同重跑受影響 stage，唔使重跑成個 generation

Stage 表 `[FRAME]`：

| Stage | 做乜 | Resume 粒度 |
|---|---|---|
| S0 `preflight` | freeze 證明、allowlist guard、task 證明、backup sha、殘留 run | 永遠重跑，要**而家**成立 |
| S1 `migrate` | `db_runtime.migrate(only={036…})` | ledger 驅動，本身 idempotent |
| S2 `discover` | GemRate id universe + `public-card-dump` | per-id（要修 §3.11f） |
| S3 `pop-land` | 解析 capture → `…_v2` | 決定性重解析，upsert on `(gemrate_id, observed_date)` |
| S4 `identity-resolve` | 由 entity 建身份、出 `split-plan.json` | plan sha；**見到新 split 就硬停** |
| S5 `bind` | 重新派生 PC/SNK binding + capture receipt | per `(source, external_id)` evidence sha |
| S6 `pc-replay` | 本機 HTML 重播 | per-file sha |
| S7 `snk-refresh` | `snkrdunk_bulk.py` + `snk_market_data.py` | per item_id |
| S8 `price-materialize` | D4 語言路由決勝 | per variant |
| S9 `image-bind` | SNK → PC，查決定 archive | per variant |
| S10 `prune-plan` | 算刪除集 + 順序，**唔刪嘢** | plan sha |
| S11 `validate` | 034–036 validator → receipt 表 | 永遠重跑 |
| S12 `activate` | 新 universe lock、切 current | **主流程到唔到，要另一條命令** |
| S13 `prune-apply` | **activation 成功之後**先分批刪 | per (table, batch) |
| S14 `canary` | 一次 V2 incremental canary（⬅036b，見 §6.7） | 一次性 |

> ⬅036b 次序修正：舊版 S11 `prune-apply` 排喺 validate／activate **之前**，同 §6.7「先啟用，後分批刪」自相矛盾 — linear `--resume` 會喺 validate 之前物理刪嘢，validate fail 就要 restore 成個 dump。而家 rollback 界線係：activation 前失敗 = candidate 標 failed、current pointer 不變、checkpoint 續跑；activation 後 prune 前失敗 = atomic pointer 切返上一個 generation；prune 開始後失敗 = restore dump（§11d）。

### 6.1 Stage 1 — GemRate provider-first 全量 landing

**Input** `[FRAME]`：worklist = 以下 union（唔再係「1,782 個 variant 逐張估」）
1. `gemrate_brute_harvest.py --all-sets` 攞 Pokemon + One Piece provider catalog
2. DB 全部 current / rejected / historical GemRate id
3. 兩個 checkout 嘅 raw cache id
4. 現有 candidate / backfill manifest
5. **625 個零真 PSA10 observation 嘅 variant** 嘅 discovery candidate

**做法**：`gemrate_source.py public-card-dump --ids-file <worklist> --resume --manifest-out <…>`

**唔准做（hard）**：
- ❌ **一行都唔准寫 `market_grader_population_observation`**（§3.2）
- ❌ 唔准按 POP / 語言 / printing / 現有 variant 過濾（D5）
- ❌ 唔准粗暴覆蓋舊 raw 檔 —— 只有 manifest 完整封口先更新 current raw pointer

**Output**：`data/private/gemrate/runs/<generation>/` immutable 目錄，payload 按 SHA 去重。

**開跑前必須修**（`[FRAME]`，唔修 S2 會不停 abort）：
- `public-card-dump` 加 429 ladder（§3.11g）
- 加 `--chunk-size` flag（而家寫死 25）
- `--resume` 只認 `rawStatus == 'captured'` + 已驗 raw sha（§3.11f）

**完成定義**：worklist 每個 id 都有 receipt，或者標 `pending`；有任何 `pending` → **整個 generation 唔准 activate**。

### 6.2 Stage 2 — 由 GemRate entity 重建身份

對每個 settled GemRate entity `[FRAME]`：
1. 只讀**唯一**一行 `grader='psa'`
2. Identity fingerprint 固定包含：PSA description 原文、year、set name、card number、parallel、derived language、canonical slug / settled canonical URL、spec id、set URL、raw PSA row SHA-256
3. 同 entity 嘅 alias id 合併
4. **不同語言 / set / parallel / printing / canonical slug 一律當作不同實體卡**
5. 對得返現有正確 variant 就沿用；舊 variant 混咗幾張就拆；冇 variant 但 POP 合格就建新
6. `canonical_name` **byte-for-byte** 等於 PSA description；collector number 等只放 structured field
7. raw current observation + 可證明身份嘅 completed legacy history 重播入 V2 表；aborted / stale-running 資料只寫 quarantine lineage
8. 同一個 PSA payload / settled entity 只可以有一個 current identity owner

**opaque_id 規矩（D7，hard）**：現有 opaque_id 釘死；新／split 出嚟嘅由 **printing sha** 派生。理由 §3.5。

**唔准做**：
- ❌ 唔准預先宣告 split label。`[KNOWN]` DB 對非 accepted id **零** language / parallel / printing 證據 —— 講「variant 98 係 JP+EN」係由 DB 以外攞嘅斷言。label 要由 S2 discovery 學返嚟。
- ❌ `[KNOWN]` 1552 / 1553 **係兩張唔同嘅卡**（OP09-004 Shanks、OP09-093 Marshall D. Teach），唔係一個 collapsed pair。

**已知要人手審嘅 input**（`[KNOWN]`，S4 停低問人，唔准自動化）：

| variant | 情況 |
|---|---|
| 98 | 兩個 id：`9f43a12e…` exact POP 1,512 / `0fdd826d…` rejected POP 4,178。DB 已記 `quarantine_collapsed_bind`，reason `collapsed_binding_price_overwrite` |
| 1530 | 三個 id：`756ef2cf…` exact 582（receipt parallel `"Base"`）/ `7c4fb3f3…` rejected 1,387 / `0e29f8b9…` rejected 134 |
| 1552 / 1553 | 各有 exact（931 / 892）+ conflict（2,160 / 1,902）；另有 `gemrate_product_line_language` evidence 把 `card_language` 由 `ja` 改 `en` |
| 1582 / 1583 | Sylveon / Espeon，`parallel='master ball reverse holo'`。**兩個 id 都標 exact，冇 conflict 記錄**，而 accepted 嗰個 POP **較大**（7,305 / 5,882 vs 967 / 884）。`[INFERRED]` Master Ball reverse holo 應該比普通 reverse holo 稀有得多，所以 accepted 嗰個好可能係普通 Reverse Holo —— 但 DB 冇證據，要 discovery 定奪 |
| 110 | 入圍 bug 個案，真 POP 222 |

`[KNOWN]` 全庫有 **17 個 variant** 有多過一個 GemRate id（16 個兩個、1 個三個），DB 已 quarantine 咗 13 個：98、1165、1194、1525、1554、1562、1563、1567、1720、1721、1728、1729、1730。

### 6.3 Stage 3 — 計唯一 qualified generation

`[FRAME]`：
- 每個獨立 GemRate printing 只讀 `operator_latest_gemrate_psa10`（§7.3）
- latest PSA10 POP ≥ 1000 → 入 `catalog_rebuild_member`（= `qualified_identity`）
- < 1000 → 標 non-qualified，等 final prune
- 以下任一 → 標 `identity_pending`，**任何 identity_pending 令 generation 唔准 activate**：冇 raw、PSA row 唔唯一、identity incident 未結案、current series 有下跌
- ⬅036b：POP 合格但未有 exact price／image → 標 `market_pending`，**唔擋 activation**（D10：activation 以 product_ready 子集上榜），留喺 catalog 繼續升級，S13 prune 唔准掂佢
- `min_pop` 由 caller 傳，**唔准 bake 落 view**
- `[COMPUTED]` **1,146 只係 full crawl 之前嘅暫算，唔准當硬驗收數**

### 6.4 Stage 4 — PC / SNK exact binding、價、成交

`[COMPUTED]` **呢個 stage 係 036 主體工作量**（§3.3）：~1,096 條 manual_review 要逐張升 exact。

**PC** `[FRAME]`：
1. 先重播本機 HTML（§6.5），再只抓 residual
2. 同一份 SSR 已經包含 `chart_data` 同 completed-auctions 資料 —— **唔准 navigate 去虛構 PSA10 URL**
3. product id、canonical URL、page title、collector、set、language、printing、parallel、raw HTML SHA **全部**吻合先 exact
4. product id collision 必須清零

**SNK** `[FRAME]`：
1. 沿用 `snkrdunk_bulk.py` + `snk_market_data.py`
2. landing 可以收所有 candidate payload，但 acceptance 固定 `trading_card_single_psa10` 同 `1枚` variant
3. exact product 要 tcg / language / collector / set / printing / parallel 一致
4. `primaryMedia.imageUrl` 一併保存

**Binding 生產者修正**（§3.4 決定：修生產者）`[FRAME]`：
- `apply_verified_source_bindings.py` 引入常數並統一兩條路徑：
  ```python
  EVIDENCE_TYPE_GEMRATE       = "provider_native_psa_identity_and_population"
  EVIDENCE_TYPE_PROVIDER_PAGE = "provider_native_product_page"
  EVIDENCE_TYPES_STRICT       = {EVIDENCE_TYPE_GEMRATE, EVIDENCE_TYPE_PROVIDER_PAGE}
  ```
  `[FRAME]` **特登唔重用 `provider_payload`** —— 嗰個 token 係 `psa_identity_repair.py:426` 為 gemrate 寫嘅，重用會令兩條分支分唔開。
- `_verify_decision_evidence`（`:689-704`）而家只**拒** `database_lineage`，`type` 缺失或空一樣過 → 改成必須 `in EVIDENCE_TYPES_STRICT`
- legacy 路徑個 `evidence_claim`（`:360-380`）**加返一個真 `evidence` object**（`type` / `path` / `sha256` / `capturedAt` / `productNumberSelector`）並 hash 驗本機 capture，同 v2 路徑（`:698-702`）睇齊
- `source_product_number`（`:347-351`）改由**重播返嚟嗰版頁面本身**派生（PC：product-id path segment + `<h1>` 卡號；SNK：item JSON 卡號欄位）。咁 `TRIM(source_product_number)<>''` 就 by construction 成立，同 `match_status='exact'` 嘅互斥自然消失（兩個 predicate 而家由同一個來源派生）

**價格規則（D4）** `[FRAME]`：
- current 市值價只認 exact PC 或 exact SNK PSA10
- PC 只認 `manualonly` 價同 `completed-auctions-manual-only` 成交
- SNK 只認 PSA10 condition、單卡 unitized 價同成交
- **EN → PC 贏；JP / OP → SNK 贏；只有一邊完全冇數先跌落另一邊**
- 唔平均兩個市場
- TPL / G10 / eBay 只留歷史 bar

**成交量分開儲**：PC = 指定窗口 distinct completed-auction fingerprint；SNK = unitized 後 quantity 總和。**唔准加埋當總量。**

**必須順手修嘅 upsert（§3.9）** `[FRAME]`：
- `ON DUPLICATE KEY UPDATE` **拎走 `run_id`**，改成 `last_run_id = VALUES(run_id)` + `restamp_count = restamp_count + 1`
- `metric_status` 改成 `CASE WHEN market_price_observation.metric_status='quarantined' THEN 'quarantined' ELSE VALUES(metric_status) END`
- 涉及 6 個 price writer：`snk_market_data.py:1196`、`pc_psa10_price_materialize.py`、`g10_ebay_ingest.py`、`g10_kline_price_bridge.py`、`g10_analytics_ingest.py`、`qualified_pool_operator.py`

**必須順手修（§3.10）** `[FRAME]`：`market_price_observation.source_code` 把 `snk_psa10` 正規化做 `snkrdunk`，否則重建會繼承同放大 118,270 條重複 accepted 行。

**唔准做**：
- ❌ 舊 `match_status='exact'` 唔會自動升 strict，必須重過 provider-native manifest
- ❌ harvest observation 可以存 landing，但 current price / sale / image **只可以經** `operator_strict_source_identity` 投影
- ❌ 錯 binding 要同一個 transaction 內 reject + quarantine 相關 price / sales / image pointer / metric acceptance / freeze

### 6.5 Stage 4b — PC 本機 cache 重播

`[KNOWN]` 檔案分佈：

| 位置 | 檔數 |
|---|---:|
| 新 checkout `data/private/pricecharting_session/html/full900` | **437** |
| 新 checkout 全部 `pricecharting_session` | 442 |
| **舊** checkout `…/html/full900` | **1,625** |
| **舊** checkout `…/html/full_identity` | **801** |
| 舊 checkout 全部 `pricecharting_session` | **2,699** |

`[KNOWN]` 舊 full900 1,625 檔嘅質素實測：
- **1,035** 有齊 `VGPC.chart_data` 同 `"manualonly"`
- **590** 冇
- **589** 冇 `completed-auctions-manual-only`
- **1,046 檔細過 600 KB** —— 所以**用大細做閘會誤殺約 456 個好檔**

`[FRAME]` `pipelines/pc_cache_replay.py`：
- `--source-root` 預設指舊 checkout，`--mode copy-first`
- **絕對唔准改舊 folder**：hash 每個候選檔 → 只 copy 過關嘅入 `<新>/data/private/pricecharting_session/html/replay-<generation>/` → 由 copy 解析
- `_assert_write_target(path)` 檢查 `os.path.commonpath([path, NEW_ROOT]) == NEW_ROOT`，喺 copy function 入面 call —— 一個可執行嘅收窄點，唔係一句約定
- `is_replayable(text) -> (bool, reason)` **內容判斷**：
  1. `'VGPC.chart_data' in text`
  2. `pricecharting_page_parse._extract_js_value`（`:48`）平衡括號解析 `chart_data` 成功 ← 真正嘅截斷測試
  3. `chart_data` 有 `manualonly` 而且係非空 `[[ts, cents], …]`
  4. `'completed-auctions-manual-only'` 存在（成交線必須；價線可選）
- 檔名 `{pcProductId}_{slug}.html` + `.json` sidecar，`pcProductId` 對 `catalog_source_identity(source_code='pricecharting', external_entity_id)`；**要求 sidecar 做 capture receipt**，HTML hash 入 `catalog_provider_capture_receipt.capture_sha256`（同 §7.4 view predicate 查嘅係同一個 hash → replay 同 binding 係一件 artifact 唔係兩件）
- **唔准靜靜咁掉咗嗰 590 個**：寫 `replay-rejects.json`（`{file, reason, sha256}`），啲 product id 餵去 live 重抓清單。36% 損失唔可以無聲無息
- `full_identity` 801 檔一併過同一個閘

### 6.6 Stage 5 — 圖 + 故事

**圖** `[FRAME]`：
1. exact SNK `primaryMedia.imageUrl` 永遠第一
2. SNK exact search 確認冇可用圖 → 用 exact PC product image
3. 128 / 958 / 1450 直接套同一規則
4. 兩者都冇 exact 圖 → **該卡唔 product-ready**。禁 Kado / TCGplayer / AI / 無來源圖
5. 所有圖仍要 asset hash、source pointer、QC、canonical acceptance、freeze

**Stage 5 個閘（改法，§3.8）** `[FRAME]`：必須逐 variant **行 FE 自己嗰兩條 query**（`live-db-snapshot.ts:228` + `:233-261`）**加**查 `data/public/market-assets/<sha>.webp` 存在。**唔准**用 `canonical_image_acceptance_id IS NOT NULL`。
`[COMPUTED]` 新圖要 materialize 入呢個 checkout（每卡 3 個 webp：base / `_200` / `_600`），而且要喺 activation **之前**。

**圖決定嘅存活**（§3.11a）`[FRAME]`：
- S13 prune-apply 之前 `snapshot_image_decisions()` 寫 `market_image_decision_archive`，key = `(content_sha256, canonical_printing_sha256, decision)`，**唔設 FK**，`original_variant_id` 只作參考
- `[KNOWN]` 可用嘅穩定 key：`market_image_review_approval` 有 `uq_image_review_approval_variant_content(variant_id, content_sha256)`（1,028 行）同 `canonical_printing_sha256`；`market_image_rejection_registry` PK 係 `(variant_id, content_sha256)`（43 行）但**冇** printing sha → 靠 Gate 0.6 個 snapshot 補
- S9 要 consult 呢個 archive，令曾經被人手 reject 嘅圖唔會靜靜咁復活

**故事** `[FRAME]`（D8）：
- 唔作 036 product-ready 條件
- 保留現有真 en / zhTW / zhCN / ja；identity fingerprint 變咗就停止沿用
- **762 條模板式 ko 標 rejected / 非 current**，唔用假內容扮完成
- 缺 story 就回空值或行現有 UI 英文顯示邏輯；**唔生成新故事**
- 完整五語 producer 屬後續 editorial，唔拖住身份市場庫

### 6.7 Stage 6 — 先啟用，後分批刪

**S11 pre-activation validator 全 PASS** 先可以入 S12 activation；**S13 prune-apply 只可以喺 activation 成功之後開始**（⬅036b 次序，見 §6.0）。

**S12 activation（一個短 transaction）** `[FRAME]`：
1. 寫入／更新 qualified variant 同 structured identity
2. 建 current universe lock
3. 切 current rebuild generation
4. rebuild canonical population / price / market cap / rank / image projection
5. **`accepted_at` 必須更新**（§3.11b），否則內容一樣就靜靜唔切

`[FRAME]` Activation 由**另一條 subcommand** 做：
```
operator_control.py rebuild-036-activate --generation <id> --receipt-sha256 <sha>
```
佢會 in-process 重跑 validator、重算 receipt sha，**除非等於命令列傳入嗰個 sha 而且 `cardz_rebuild_validation_receipt.passed = 1`，否則拒絕**。
`[COMPUTED]` 咁樣一次 activation、唔會撞到、而且 operator 一定要真係讀過個 receipt 先打得出個 hash。

**S13 物理清理（分批，activation 之後）** `[FRAME]`：

⬅036b（D10）：刪除集**只包含**非入圍（POP<1000／無有效 identity／wrong-identity shell／duplicate shell）；`qualified_market_pending` **唔准刪**。

未入圍嘅舊 row 喺 activation 之後即刻唔會出現喺 3800，但物理刪除分批做。

順序派生 + 雙向對數：
1. `information_schema.KEY_COLUMN_USAGE` 攞引用 `catalog_variant` 嘅 FK → `[KNOWN]` 36 條 / 35 張表
2. 遞迴行埋引用呢啲 child 嘅 FK（`market_price_observation.source_observation_id` → `market_source_observation`；`market_source_observation_payload_pointer` → 三個 parent），topological sort，葉先
3. 補返冇 FK 嘅：**`operator_binding_freeze`**（`variant_id`，5,590 行）、**`catalog_variant_remap`**（`old_variant_id` / `new_variant_id`，0 行）
4. 對 `data/policy/prune-order-allowlist.json` = `{table: {column, deleteMode, reviewedAt, reviewedBy}}`：
   - 喺 information_schema 但唔喺 allowlist → **ABORT**，印個表名。**唔准自動刪未知 FK 表**
   - 喺 allowlist 但唔喺 information_schema → **ABORT**，allowlist 過時
   `[COMPUTED]` 只對單邊就係呢類 guard 腐爛嘅方式
5. `deleteMode: "never"`（記錄成決定唔係疏忽）：`market_raw_payload_object`（317,169 行，零 FK，D9 話 raw evidence 要留）、`market_source_observation`。`[COMPUTED]` 因為 `market_price_observation.source_observation_id` 係 child→parent，刪價**唔需要**刪 observation

**❌ 永遠唔准 `SET FOREIGN_KEY_CHECKS=0`。** `[COMPUTED]` 76 條 FK 全部 `NO ACTION`，佢哋就係證明個順序啱嘅安全網。關咗 = 把一個排序 bug 變成幾個月後先發現嘅無聲孤兒 row。

批次：`DELETE FROM <child> WHERE variant_id IN (≤500 個 id) LIMIT 5000` 循環，每批 commit，進度寫 `cardz_rebuild_prune_progress(generation_id, table_name, batch_index, rows_deleted, finished_at)`，S13 可以喺表中間續。
`[COMPUTED]` **唔係一個 transaction** —— 「一 take」係一個 generation 一次 activation，唔係喺 293k + 255k + 159k 行刪除上面揸住一個鎖。

Review / rejection 資料先轉 stable identity key，再把 variant FK 設 NULL，之後先刪 catalog variant。
`market_source_observation_payload_pointer` 隨被刪 observation 清理；`market_raw_payload_object` 全部保留。
Shared ingest run 只喺完全冇剩餘 reference 時清理；raw landing 永遠保留。

`[KNOWN]` **17 條 view** 帶 `variant_id`，唔使 DDL（query time 解析），但每條都要喺 S13 receipt 記 before / after row count —— `[COMPUTED]` 一條 view 由 762 靜靜變 1,146，同靜靜變 0，喺 log 入面樣衰一模一樣。

**S14 canary（⬅036b，併自 Codex）** `[FRAME]`：對一張已知 qualified printing 加一個新 provider capture，行一次 V2 incremental lane：驗 append-only（無改寫舊 observation）、POP monotonicity、generation mapping、FE cache invalidation、無重複 current row。完成後 scheduler 仍然全部 Disabled — 036 交付 incremental lane 但唔啟用排程。

**收尾（S14 之後；⬅036b 失敗路徑一樣要行到，見 §4 Gate 0.4 第 6 步）** `[FRAME]`：
- drop 短命 rebuild DB user、還原 `cardz` 原有 DML grant
- 保持所有 scheduler Disabled
- backend generation 標 `036_<UTC>`，frontend 仍然 FE02
- AWS / public snapshot / Google Sheet **完全不變**

### 6.8 FE02（⬅036b 拍板，解決 §12.4／§12.5）

`[FRAME]` 同 backend 036 一齊交付；FE-only、可逆；**可以先行**（唔依賴 backend stage，行喺現有 762 generation 上）：
1. **Watchlist 分頁**：拆走 `server-snapshot.ts:112` 個 101..300 硬窗；API 加 `page`／`pageSize`（預設行為保持相容），UI 由 rank 101 分頁載到最後一張 product-ready 卡。list surface 從此同 sitemap 一致。
2. **Generation-keyed snapshot cache**：module-level cache keyed by current generation；每個 request 只做輕量 current-generation probe，generation 變先重建 snapshot。解決 §3.11j 每 request 5.15s。
3. **拆走 `populationRows`**（43,414 行攞完喺 `:429` 掉咗，從來冇用過）。
4. price 日期窗：只喺證實 FE 冇用超窗資料先加；唔准為咗慳 query 改變可見行為。
5. 卡名照 PSA raw description（現有行為，唔另做）。

驗收：`GET /` 快過今日 5.151s（要記數）；generation flip 後下一個 request 載到新 snapshot（配合 §3.11b `accepted_at` 修正）；watchlist 去到最後一張 product-ready 卡冇 300 截斷。

---

## 7. Migration 036 規格

### 7.1 檔案規矩

`[KNOWN]` 目錄 `pipelines/migrations/`，命名 `NNN_snake_case.mysql.sql`，`glob("*.mysql.sql")` + `sorted()`（純字典序，3 位零填充係 load-bearing）。現最高 035，**冇任何 036**。
`[KNOWN]` Ledger 表 `cardz_migration_ledger(migration_file PK, content_sha256, applied_at)`；已 apply 嘅檔內容改咗會 raise。

`[FRAME]` 036 硬約束（因為 §3.11c / §3.11d）：
- 只准 `CREATE TABLE IF NOT EXISTS`、`CREATE OR REPLACE VIEW`、`INSERT IGNORE`
- 每個 `ALTER` 自己一個檔（MySQL 8.4 冇 `ADD COLUMN IF NOT EXISTS`）
- string literal 入面唔准有 `;`

### 7.2 `market_gemrate_psa10_observation_v2`

`[FRAME]`
```sql
id                 BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
run_id             BIGINT UNSIGNED NOT NULL,     -- FK market_ingest_run(id)
gemrate_id         CHAR(40)  NOT NULL,           -- bare hex
variant_id         BIGINT UNSIGNED NULL,         -- 未綁定前係 NULL：身份先於綁定
psa10_population   INT UNSIGNED NOT NULL,
total_population   INT UNSIGNED NULL,
grader_code        VARCHAR(8) NOT NULL DEFAULT 'PSA',
top_grade_label    VARCHAR(8) NOT NULL DEFAULT '10',
estimated          TINYINT(1) NOT NULL DEFAULT 0,
effective_at       DATETIME(6) NOT NULL,
observed_date      DATE NOT NULL,
capture_path       VARCHAR(500) NOT NULL,
raw_payload_sha256 CHAR(64) NOT NULL,
psa_row_sha256     CHAR(64) NOT NULL,
generation_id      VARCHAR(64) NOT NULL,

UNIQUE KEY uq_gemrate_psa10_identity_day (gemrate_id, observed_date),
KEY ix_gemrate_psa10_variant (variant_id, effective_at),
KEY ix_gemrate_psa10_latest  (gemrate_id, effective_at, id),

CONSTRAINT ck_gemrate_psa10_id    CHECK (gemrate_id REGEXP '^[0-9a-f]{40}$'),
CONSTRAINT ck_gemrate_psa10_grade CHECK (grader_code = 'PSA' AND top_grade_label = '10'),
CONSTRAINT ck_gemrate_psa10_real  CHECK (estimated = 0)
```

`[COMPUTED]` 三個 CHECK 就係結構性修正：呢張表**物理上放唔落** snkrdunk `top` 行、ebay 行、或者 colon-prefixed id。呢個保證 `latest_psa10_pop` 從來冇有過。
`[COMPUTED]` `variant_id` nullable 係特登 —— GemRate discovery 係先搵到 identity，之後先知佢屬邊個 variant，而 collapsed split 正正需要呢個次序。
`[COMPUTED]` key 用 `(gemrate_id, observed_date)` 而唔係 `(variant_id, …)`，就係阻止 variant 98 兩個 id 互相冚死（§3.2）。

`[FRAME]` **舊 `market_grader_population_observation` 保留**（其他 grader / legacy 用途），但**由所有 current admission 同 market-cap projection 移走**。唔改佢個 unique key（避免 159k 行 ALTER）。

### 7.3 `operator_latest_gemrate_psa10`

`[FRAME]`
```sql
CREATE OR REPLACE VIEW operator_latest_gemrate_psa10 AS
SELECT gemrate_id, variant_id, psa10_population, effective_at,
       observed_date, raw_payload_sha256, generation_id
FROM (
  SELECT o.*,
         ROW_NUMBER() OVER (PARTITION BY o.gemrate_id
                            ORDER BY o.effective_at DESC, o.id DESC) rn
  FROM market_gemrate_psa10_observation_v2 o
) r
WHERE r.rn = 1;
```
`[COMPUTED]` 每個 `gemrate_id` **剛好一行**，`id DESC` 拆平手。§3.1 個 `MAX(effective_at)` self-join 多行問題同 Python loop 嘅 fetch-order 依賴一次過消失。

**呢個 view 係唯一 POP authority。** 額外 predicate（如果來源允許）`[FRAME]`：unique raw `grader='psa'` row、current PSA identity acceptance、current exact GemRate settled entity、相同 canonical slug 同 fingerprint、run status 只收 `complete` / `completed`、同一 series POP 不得下降。

### 7.4 `catalog_provider_capture_receipt` + strict view 修訂

`[FRAME]`
```sql
catalog_provider_capture_receipt(
  source_code, external_entity_id, capture_sha256 PK,
  capture_path, captured_at, generation_id, parser_version)
```

`[FRAME]` strict view **只改非 gemrate 分支**（035 檔嘅 line 127-131），gemrate 分支 byte-identical 保留，令 035 全部 invariant 生還：
```sql
si.source_code <> 'gemrate'
AND JSON_UNQUOTE(JSON_EXTRACT(si.bind_evidence_json,'$.evidence.type')) = 'provider_native_product_page'
AND JSON_UNQUOTE(JSON_EXTRACT(si.bind_evidence_json,'$.evidence.sha256')) REGEXP '^[0-9a-f]{64}$'
AND JSON_UNQUOTE(JSON_EXTRACT(si.bind_evidence_json,'$.evidence.path')) <> ''
AND EXISTS (
  SELECT 1 FROM catalog_provider_capture_receipt r
  WHERE r.source_code = si.source_code
    AND r.external_entity_id = si.external_entity_id
    AND r.capture_sha256 = JSON_UNQUOTE(JSON_EXTRACT(si.bind_evidence_json,'$.evidence.sha256')))
```
`[COMPUTED]` 呢個 shape 同 035 已經對 gemrate 用緊嘅 EXISTS pair（`operator_psa_identity_projection` / `operator_gemrate_provenance_projection`）一致 —— 係延續唔係新發明。

**排序約束** `[FRAME]`：capture receipt 表落 036；**view 修訂另開 `037_strict_source_identity_provider_native.mysql.sql`，喺 S6 完成之後先 apply**。唔係嘅話個 view 會有一段時間「啱但空」。

### 7.5 price observation 加欄（各自一個檔）

`[FRAME]`
```sql
ALTER TABLE market_price_observation ADD COLUMN first_run_id BIGINT UNSIGNED NULL;
```
```sql
ALTER TABLE market_price_observation ADD COLUMN last_run_id BIGINT UNSIGNED NULL;
```
```sql
ALTER TABLE market_price_observation ADD COLUMN restamp_count INT UNSIGNED NOT NULL DEFAULT 0;
```
```sql
UPDATE market_price_observation SET first_run_id = run_id WHERE first_run_id IS NULL;
```

### 7.6 Rebuild 記帳表

`[FRAME]` `cardz_rebuild_generation`、`cardz_rebuild_checkpoint`（§6.0）、`catalog_rebuild_member`、`catalog_population_identity_incident`、`cardz_rebuild_prune_progress`、`cardz_rebuild_validation_receipt`、`market_image_decision_archive`。

`[KNOWN]` 呢啲名喺現時 DB **全部唔存在**（唔係 table 亦唔係 view），所以全部係新 DDL。

`catalog_population_identity_incident` 只記真事故 `[FRAME]`：
- 同一 settled entity / fingerprint 後一筆 POP 下降
- 同一 requested id 喺不同時間 settle 成不同 canonical entity
- 已接受 GemRate binding 要改去另一個 slug / 語言 / set / parallel
- 舊 variant 實際混咗多張 printing，需要 split / reassign
- 跨 id 換卡造成 POP 上或落跳

**唔會**因為 full discovery 搵到兩張本身獨立嘅 printing 就開假 incident。同一 identity POP 上升亦唔係 incident。

---

## 8. Validator 參數化

`[KNOWN]` `scripts/validate_psa_identity_repair.py`：342 行，**冇 argparse**，`main()` 零參數，硬讀三個檔（`OUT_034/audit.json`、`OUT_035/resolution-plan.json`、`OUT_035/apply-receipt.json`），`:337` print JSON 去 stdout、`:338` `return 0 if report["pass"] else 1`，**乜都唔存**。

`[FRAME]` 加 `--generation`、`--phase {034,035,036}`、`--expect data/policy/rebuild-expectations.json`、`--out`。

`[COMPUTED]` 組織原則：**要生還嘅 034/035 invariant 係關於「證據形狀」同「人手決定」嗰啲；要參數化嘅係關於「隊列大細」嗰啲。** 把 `:265-292` 個 dict 拆兩組。

### 8.1 `structural` — 唔郁，永遠 enforce

`fixtureAllPass`、`literalPsaDescriptionByteExact`、`languageConflictsZero`、`collectorConflictsZero`、`setConflictsZero`、`parallelAmbiguityZero`、`completePrintingHashExact`、`topLevelDescriptionNeverAuthority`、`onePayloadOneCurrentName`、`oneCurrentAcceptancePerVariant`、`strictDatabaseLineageZero`、`gemrateCoverageUsesPositivePopulation`、`activeGemrateExactBindingUnique`、`migrationsLedgered`

### 8.2 `cohort` — 拆走 literal

| 舊 | 新 |
|---|---|
| `catalogExactly1782` | `catalogMatchesGenerationPlan`（prune 後 `catalog_count == generation.expectedCatalogCount`） |
| `auditExactly1782Unique` | `auditCoversFullCatalog`（`len(audit) == catalog_count` 且 unique） |
| `planExactly762Unique` | `planCoversAdmittedCohort` |
| `active762IdentityAndProvenanceResolved` | `activeCohortFullyResolved` —— **保留條等式鏈** `active_count == active_accepted == active_provenance == active_strict_gemrate`，只拆走 `== 762` 尾巴，改同 `generation.admittedCount` 比 |

`[COMPUTED]` 條等式鏈先係真 invariant，個常數係附帶。

### 8.3 `sheet70` 那組維持 literal

`[KNOWN]` `sheet70ExactMapping`（==70，每行 `matchCount==1`）、`green6AcceptedIdentity`（==6，全部喺 accepted）、`red13OldIdentityAndMarketQuarantined`（==13，且 `red_ready_prices` / `red_current_sales` / `red_public_images` / `red_non_identity_freezes` / `red_product_projection` / `red_old_printing_current` **全部 0**）。
`[KNOWN]` Fixture：`data/editorial/psa-identity-sheet70-034.json`（5,970 bytes，`contract: psa-identity-sheet70-034-v1`，range `A5:C74` = 70 行，green 6、red 13、`oldCanonicalNames` 70）。

`[COMPUTED]` 佢係**針對指定卡嘅人手驗收 fixture**，唔係隊列量度，所以唔參數化。

`[COMPUTED]` **一個要修嘅陷阱**：`red_ids` 由 `audit_by_old_name` ← `OUT_034/audit.json`（`:52-54`、`:89`）派生，係**檔案**，prune 之後仲喺度。但 `red_ready_prices` 等（`:243-253`）用 `WHERE variant_id IN (...)`，如果某張 red 卡被刪咗，就會**空洞地返 0**。→ 新增 `redVariantsPrunedOrQuarantined`，記錄每個 red id 究竟係「被刪」定「被 quarantine」，令 PASS 唔會有歧義。

### 8.4 036 新 invariant

`[FRAME]`
- `popAuthorityAllowlistClean`
- `qualifiedAllHaveRealGemratePsa10AtOrAbove(minPop)`
- `productReadyAllHaveExactBoundCurrentPrice`（⬅036b：範圍係 product_ready，唔係全 qualified）
- `productReadyAllHaveExactSourcedImage`
- `cohortPartitionExact` ← `product_ready + qualified_market_pending == qualified_identity`（D10）
- `noNonGemrateRowInPopAuthority`
- `strictSourceIdentityHasPcAndSnk` ← 證明 §3.4 個 blocker 真係修好（今日 762 gemrate / 0 其他）
- `prunedVariantsFullyDetached`
- `imageDecisionsPreserved`
- `runIdRestampZero`
- `feRenderedImageEqualsAdmitted` ← 用 FE 自己嗰兩條 query + 查檔（§3.8）
- `snkSourceCodeNormalized` ← `snk_psa10` 清零（§3.10）

### 8.5 存檔

`[FRAME]` 新表 `cardz_rebuild_validation_receipt(generation_id, phase, report_sha256, passed, report_json, created_at)`。Activation gate 讀佢。
`[KNOWN]` 今日 70/6/13 個 PASS **只存在於 `PROJECT_STATE.md:62` 嘅文字描述**，從來冇機器記錄過。

---

## 9. 驗收 / 攻擊案例

> 每條對應返上面某個 blocker。冇對應嘅 assert = 冇修過。

**Identity / POP（對應 §3.1、§3.2）**
- `label='top'`、`9.5`、prefixed external id 永遠唔可以成為 PSA10 POP
- **`label='top'` 但 bare-40-hex 嗰 116 行**都要被擋（單靠 id-shape 唔夠）
- current POP 必須同 current accepted settled GemRate entity + fingerprint 一致，唔止係 variant 一致
- aborted / stale-running run 對 V2 current authority 貢獻 = 0
- 同一 series POP 下降 = 0；跨 entity / slug 轉換全部有已結案 incident
- full discovery：每個 GemRate entity 只屬一個 printing；每個 qualified printing 只屬一個 variant
- 98 / 1530 / 1552 / 1553 / 1582 / 1583 各自按 discovery 結果分開，**唔准用預設 label**

**Strict binding / 價（對應 §3.3、§3.4、§3.7、§3.9、§3.10）**
- `strictSourceIdentityHasPcAndSnk`：PC 同 SNK strict coverage **不再為 0**
- strict projection 內 `database_lineage = 0`
- run 5628 類 observation 只有重新 strict bind 後先可以入 current
- PC 只用 `manualonly` + `completed-auctions-manual-only`
- SNK 只用 `trading_card_single_psa10` + `1枚`
- **D4：EN 卡 current price source = `pricecharting`；JP/OP 卡 = `snkrdunk`；只有單邊有數時先例外，且例外要有記錄**
- `runIdRestampZero`：`last_run_id IS NOT NULL AND last_run_id <> run_id AND observed_date < <generation 開始>` 計數 = 0
- 重跑 SNK ingest 之後，原本 quarantined 嘅行仍然 quarantined
- `market_price_observation` 冇 `source_code='snk_psa10'` 剩低
- 每張 qualified 卡都有 current exact 價 + market cap

**圖（對應 §3.8）**
- `feRenderedImageEqualsAdmitted`：行 FE 兩條 query + 查 webp，數目 == admitted count
- 人手 reject 嘅卡刪咗再模擬重新入圍，stable identity key 仍然阻止舊錯圖復活
- `market-assets` 每張卡三個尺寸（base / `_200` / `_600`）都喺呢個 checkout

**Prune（對應 §2.2、§6.7）**
- pre-prune manifest 冇遺漏 / 冇重複；final DB variant count 精確等於 qualified generation count
- `unknown FK tables = 0`；allowlist 雙向對數都 clean
- `operator_binding_freeze` 同 `catalog_variant_remap` 明確喺 allowlist 入面
- 17 條 view 有 before / after row count 記錄
- `market_raw_payload_object` 行數不變

**Sheet fixture（對應 §8.3）**
- 70/70、6 綠、13 紅舊錯身份同市場資料唔入 current
- `redVariantsPrunedOrQuarantined` 對每個 red id 有明確狀態

**Activation / FE（對應 §3.11b、§3.11j、§3.11k）**
- 重跑 activation 內容一樣時 `accepted_at` 仍然更新，FE 真係切到
- 3800 讀到同一個 `036_<UTC>` + FE02
- AWS snapshot SHA 保持不變
- `GET /` 響應時間記錄低（今日 5.151s，1,146 張後預期 7–8s）
- `scopeSnapshot` rank 窗口有明確決定（要唔要改 101..300）

**故事（對應 D8）**
- 假 ko 模板 current count = 0
- story 缺口**唔影響** product-ready

**最終**（⬅036b 兩層 cohort 版）
- `product_ready + qualified_market_pending == qualified_identity`
- activation 上榜集 == `product_ready`
- `identity_pending = 0`（`market_pending` 唔擋 activation）
- `incident unresolved = 0`
- `unknown FK tables = 0`
- freshness：activation 時 POP／price capture 喺 §6.0 窗口內

---

## 10. 環境事實清單

> 全部 `[KNOWN]`，2026-08-08 實測。外部審查者必讀。

### 10.1 MySQL 係 Docker

| 項 | 值 |
|---|---|
| Container | `cardz-market-cap-db-1`，image `mysql:8.4`（server 報 **8.4.10**），Up healthy |
| Port | `3306/tcp` → `127.0.0.1:3308`（loopback only） |
| compose project | `cardz-market-cap` |
| **compose 檔** | `cardz-market-cap/compose.backend.yaml` ← **舊 folder** |
| working_dir label | 同上（舊 folder） |
| Volume | `cardz-market-cap_cardz_mysql` → `/var/lib/mysql`，**14 GB** |
| 新 checkout 有冇 `compose.backend.yaml` | **冇**（新 folder 個 `compose.yaml` 只有 `web` service） |
| 另一個 container | `cardz-verify`（`cardz-web:verify`，Exited 255，9 日前） |

`mysqldump`：container 內有（8.4.10）；**主機冇**（PATH、Program Files、xampp、choco、`C:\tools` 全部搵唔到），`mysql` client 主機亦冇。
Container 檔案系統：`/dev/sdd` 1007 G，用 59 G，**剩 897 G**。主機 C: 用 1569.4 GB，**剩 291.7 GB**。

⚠️ `[COMPUTED]` **刪咗舊 folder = 冇咗管 DB 個 compose 檔**；之後喺新 folder 行 `docker compose` 會算出唔同 project name，resolve 唔到原本個 volume，最壞情況靜靜起一個**全新空 volume**。

### 10.2 帳戶權限

```
GRANT ALL PRIVILEGES ON `cardz_market_cap`.*                      TO `cardz`@`%`
GRANT ALL PRIVILEGES ON `cardz_migration_verify_20260728_1540`.*  TO `cardz`@`%`
GRANT ALL PRIVILEGES ON `cardz_migration_verify_20260728_1550`.*  TO `cardz`@`%`
```
**冇 global 權限**，讀唔到 `mysql.user`（error 1142）→ 佢改唔返自己嘅權限；帳戶操作一定要 root 經 `docker exec`。
Trigger 0 個、Event 0 個、`foreign_key_checks=1`。

### 10.3 排程 — 14 個全部 Disabled

見 §4 Gate 0.3 表。WSL 冇 cardz cron / timer。

### 10.4 而家行緊嘅 process

| Port | PID | Process | 詳情 |
|---|---|---|---|
| 3308 | 51516 | `com.docker.backend.exe` | Docker Desktop port proxy |
| 3800 | 57780 | `dllhost.exe` | **同 cardz 無關**（COM surrogate，parent `svchost.exe` 1932） |
| 3801 | 16228 | `node.exe` | `next start --hostname 127.0.0.1 --port 3801`，module root = **舊 checkout** `cardz-market-cap\node_modules`；parent 37856 = `npm --workspace @cardz/web run start`；兩者 2026-08-05 20:22:51 起 |

另有 3 個 `Photos.exe`（66104 / 21460 / 70348）揸住舊 checkout 底下嘅圖片 handle。

### 10.5 Git

| 項 | 值 |
|---|---|
| 新 checkout branch | `codex/cardz-fe-db-consolidation-20260805`（**冇 upstream**） |
| HEAD | `2056700e docs: add AWS GitHub pull deployment runbook` |
| 前 5 個 commit | `2056700e` / `78848067 release: CARDZ Market Cap 033 FE02 snapshot [deploy]` / `14eb3fe7 release: CARDZ 762 production generation…` / `7f7e0bc8` / `6c6782cc` |
| 舊 checkout branch | `ui-experiments-20260725`，HEAD `66c2d91b baseline: checkpoint working tree before eBay union port`（2026-08-01 03:53:09 +0900） |
| merge-base | `18b5da65`；HEAD 行前 8 個，`66c2d91b` 行前 3 個 |
| `66c2d91b` 喺邊啲 branch | `fe-lock-20260802`、`ui-experiments-20260725`、`wsl-cutover-20260731` + 對應 origin |

### 10.6 Migration ledger

| 檔 | applied_at |
|---|---|
| `035_gemrate_provenance_psa_identity_resolution.mysql.sql` | 2026-08-07 14:39:13.439350 |
| `034_psa_source_identity_repair.mysql.sql` | 2026-08-07 14:07:57.563029 |
| `033_canonical_projection_binding_lineage.mysql.sql` | 2026-08-06 06:39:01.990895 |
| `032_pricecharting_local_history_merge.mysql.sql` | 2026-08-06 05:33:48.697303 |
| `031_active_exact_identity_market_repair.mysql.sql` | 2026-08-05 15:28:28.141871 |

### 10.7 DB 連線 call site

`db_runtime.connection_from_args:379`（`:392` 讀 `CARDZ_DB_USER`）、`db_retention.py:194`、`c11_pc_sold_ingest.py:76`、`ingest_snk_trades_sales.py:58`、`snk_market_data.py:506`（`_db_connect`）、`qualified_pool_operator.py:94`（`db()`）、`pc_ungraded_reference_ingest.py:58`、`scripts/canonical_seed.py:108`。**全部用同一個 `CARDZ_DB_USER` 帳戶。**

---

## 11. 備份 / 還原 runbook

> 密碼由 container 環境變數 / `backend.env` 攞，**唔准貼落任何 log 或 commit**。

**(a) 全量 dump** — 見 §4 Gate 0.2。
**(b) 驗 dump 完整性** — sha256 + 檔尾 `-- Dump completed`。
**(c) restore 去 throwaway schema + 逐表對數** — root 開 schema（`cardz@%` 開唔到），載入，55 張 base table 逐個 exact row count diff，view 對返 17，驗完 drop。
**(d) 出事點 rollback**（⬅036b 三段界線）— 每個 stage 有 checkpoint。activation（S12）之前失敗：candidate generation 標 failed，current pointer 不變，**唔使 restore**，checkpoint 續跑；activation 後、prune（S13）前失敗：atomic pointer 切返上一個 generation；prune 開始後失敗：restore dump（因為 raw evidence 保留，重跑 Stage 1–5 唔使重爬）。
**(e) 已知限制** —
- `[COMPUTED]` dump 係 point-in-time，重建期間任何**非 rebuild 帳戶**寫入都會喺 restore 時消失。所以 §4 Gate 0.4 個 freeze 必須先成立。
- `[KNOWN]` `data/private/**` 唔喺 git 亦唔喺 DB dump，要另外 tar。
- `[KNOWN]` `data/public/market-assets/**`（8,121 檔）同樣。

---

## 12. 未解 / 留俾紅隊

> 呢節係俾下一輪紅隊（Fable 5 / Codex）優先攻嘅位。

1. `[COMPUTED]` **Stage 排序可能倒轉咗。** §3.3 話 Stage 4 嘅 exact-binding 升級（~1,096 條）係 Stage 3 qualification 嘅**前置**（冇 strict identity → 冇 eligible price → 冇 market cap → 冇 rank），但 §6 而家仍然把佢排喺 Stage 3 之後。呢個要拍板。**→ 已解決（036b，D10）**：qualification 只睇 POP；exact binding 只決定 product_ready，唔擋 activation。
2. `[COMPUTED]` **~1,096 條 manual_review 逐張升 exact，工作量未估過。** 有幾多可以自動由 provider payload 派生？有幾多要人手？呢個數決定 036 係幾日定幾星期。
3. `[INFERRED]` **1,582 / 1,583 accepted id POP 較大**，同「Master Ball reverse holo 應該較稀有」相反。DB 冇證據。discovery 之後如果證實 accepted 嗰個係普通 Reverse Holo，即係現時排行榜有兩張卡嘅 POP 係錯嘅（7,305 / 5,882 vs 967 / 884）—— 呢個係 **live 錯數**，唔止係 036 問題。
4. `[COMPUTED]` **`scopeSnapshot` rank 101..300 窗口**：1,146 張之後 846 張唔會出現喺任何 list surface，但 sitemap 出全部 URL。要唔要改？改嘅話係 FE 改動，超出 036 backend 範圍。**→ 已拍板（036b，§6.8）**：分頁到最後一張 product-ready 卡。
5. `[COMPUTED]` **FE 每 request 5.15s → 7–8s**。要唔要喺 036 順手拎走 `populationRows`（43,414 行攞完唔用）同加 price 日期窗？定係另開 FE03？**→ 已拍板（036b，§6.8）**：入 FE02 — generation-keyed cache＋拆 populationRows；日期窗只喺證實無行為改變先加。
6. `[KNOWN]` **`market_ingest_run` 3568 仍然 `running`** 自 2026-08-01。佢寫過啲乜？要唔要當 aborted 處理佢寫落嘅行？
7. `[COMPUTED]` **`uq_market_price_daily` 加闊** 可以一次過解決 collapsed-identity 價格覆蓋（26 條 `collapsed_binding_price_overwrite`），但跨 293,893 行改 cardinality。押後 037 定併入 036？
8. `[COMPUTED]` **舊 checkout 2,699 個 PC HTML**（含 801 個 `full_identity`，新 checkout 完全冇對應）。要唔要全部過 replay 閘？定只做 full900？
9. `[COMPUTED]` **590 個截斷 HTML** 要重抓，但 PC 有 Cloudflare（歷史上要 WSLg 有頭 Chrome + CDP 9222）。重抓成本未估。
10. `[KNOWN]` **`.agent/EXECPLAN-037-psa-identity-chain-rebuild.md`** 同呢份 plan 嘅關係未理清 —— 037 已經有一份 execplan，036 同佢邊個先？**→ 已解決（036b）**：改名歸檔做 034/035 完成紀錄（Gate 0.1 步驟 3），036 以本檔為唯一執行文件。
11. `[INFERRED]` **`market_image_decision_archive` 個 key 用 `(content_sha256, canonical_printing_sha256, decision)`**，但 rejection 表冇 printing sha，要靠 Gate 0.6 snapshot 補。如果 Stage 2 判斷某張卡嘅 printing sha 應該改變（正正係 split 嘅情況），舊 rejection 應該跟邊個 sha？未有答案。
12. `[COMPUTED]` **Stage 6 activation 個短 transaction 覆蓋幾多張表？** §6.7 列咗 5 個動作，但實際 SQL 未寫，唔知會唔會又變成跨十幾張表嘅長 transaction。

---

## 附錄 A — 一句話總結

`[COMPUTED]` 我哋以為 036 係「修一個入圍閘 bug + 由 762 張擴到 1,146 張」。實際上：

1. **入圍閘 bug 只係表面。** 底下 POP 表結構上放唔落多過一個 GemRate identity，1,125 張卡嘅第二身份歷史**已經冇咗**，所以全量重爬係必須。
2. **價錢驗收鏈今日已經係死嘅**（三條 view 返 0 行），live 嗰 762 張重跑唔返出嚟。真工作量係 ~1,096 條 binding 逐張升 exact，唔係「改幾個工具」。
3. **圖嘅閘量緊另一樣嘢**，3800 而家已經有 133 張 placeholder，而所有 assert 都話 762/762 過。

`[FRAME]` 呢三樣冇一樣係「重建之後就自動好」。要逐樣單獨修好、單獨證明修好，先至值得開始爬。
