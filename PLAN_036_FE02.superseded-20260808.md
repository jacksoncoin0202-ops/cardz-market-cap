# PLAN 036 / FE02 — 身份先行全量重建

> 版本命名（hard）：**backend = 036**、**frontend = FE02**。之後每次更新就推上去：037 / FE03、038 / FE04…… 文件、migration、snapshot generation、FE build id 全部要跟同一組數。

**唯一 working tree**：`cardz-market-cap-fe-db-20260805`（branch `codex/cardz-fe-db-consolidation-20260805`）。
`cardz-market-cap` 係同一個 GitHub repo 嘅舊 checkout（branch `ui-experiments-20260725`，migrations 只到 022，有 3 個獨有 commit + 348 個未 commit 改動）。
> ⚠️ **舊 folder 唔止係「舊 code」—— 個 MySQL container 同 14GB volume 都係佢管住嘅。刪之前必讀 §9.1。**

---

## 閱讀指引（給紅隊 / 外部審查者）

呢份檔係**分層累積**寫成，唔係一次過寫好：

| 段 | 內容 | 權威性 |
|---|---|---|
| §0 | owner 鎖定嘅決定 | **最高** |
| §1–§5 | 第一版計劃（2026-08-08 早） | 部分已被 §9 推翻，見下面 ⚠️ 標記 |
| §6–§8 | A–G 必修清單 + 風險 + 次序 | §8 次序**已按 §9 重寫，係現行版本** |
| §9 | 5 隊平行審計 + 自驗發現 | **§1–§7 同 §9 衝突時，一律以 §9 為準** |
| §10–§12 | 工具盤點 / 環境事實 / 備份 runbook | 事實紀錄，2026-08-08 實測 |
| §13 | 未解問題 | **紅隊由呢度開始最有效率** |

已知被推翻嘅位（原文保留做記錄，唔好照做）：
- §2 第 2 點「收窄 status ENUM」→ **被 §9.2 推翻**
- §6-B「刪舊 checkout」→ **被 §9.1 推翻**
- §6-E 第 1 步「ENUM 收窄」→ **被 §9.2 推翻**
- §6-F「要熄日更」→ **被 §9.5 修正（已經全部熄咗，只需驗證）**
- §1.7 / Stage 4 關於「PC 要 navigate 去 PSA10 頁」→ **被 §9.7 推翻**
- §7.8「image projection 名不副實」嘅解釋 → **被 §9.6 更正（真因係空 view）**
- §7.1b「762 張齊 5 語」→ **被 §9.8 更正（ko 係樣板，內容假）**
- §3「單一入口 `catalog_rebuild.py`」→ 該檔**唔存在**，見 §7.1c / §10

---

## 0. 已鎖定決定（owner 2026-08-08）

| # | 決定 |
|---|---|
| D1 | `latest POP < 1000` 一律刪，**包括現役 762 入面嘅 4 張**（110 / 1530 / 1552 / 1553）。同時明文改 `PROJECT_STATE.md` 同 `CARDZMC_EXPERIENCE_LEDGER.md` 嗰兩條「唔准縮細 762」規矩。 |
| D2 | POP **向上跳**同下降對稱處理，兩個方向都開 incident。 |
| D3 | 舊 / rejected GemRate ID 一律走腳本重爬，**唔設「攞唔返」逃生門**（已實測 7/7 舊 ID 回 200、零 redirect）。 |
| D4 | current 價 = **PC / SNK 邊個有數用邊個**（owner 2026-08-08 明確講「唔係強制性」）。美／英版成交多數喺 eBay → 行 PC 腳本；JP / OP → 行 SNK。兩個都有就按 printing 對得上嗰個。TPL / G10 / eBay 只入歷史 bar，**唔入 market cap**。 |
| D5 | GemRate 全部來源當同一張表，**一次過全爬 + 粗暴覆蓋**，唔分 phase、唔分 625/1157。 |
| D6 | **採集層唔准 filter**。fetch-all → 落 landing → 入 DB 嗰刻先由單一 acceptance function 取捨。 |

---

## 1. 上一版 plan 要改嘅位（實測結果）

### 1.1 事故模型講錯咗一半

上一版寫「同一 variant_id 曾先後連接不同 GemRate ID」＝身份**轉換**。實測係：**兩至三張唔同嘅實體卡曾經同時掛喺同一個 variant 底下**。

GemRate `/card/{id}` 會 settle 到一條含 language / parallel / printing 嘅 canonical slug，一個 request 就定到案：

| variant | 舊 ID slug（POP） | 現行 ID slug（POP） | 真相 |
|---|---|---|---|
| 98 | `…one-piece-**japanese**-op01-…-manga-alternate-art-120`（4178） | `…one-piece-op01-…-manga-alternate-art-120`（1512） | 日文版 vs 英文版 |
| 1530 | `…-**poke-ball**-reverse-holo-059`（904）<br>`…-**master-ball**-reverse-holo-059`（1387） | `…-prismatic-evolutions-059`（582） | 三個唔同 parallel |
| 1552 | `…op09-…-**wanted**-alternate-art-004`（2160） | `…op09-…-**manga**-alternate-art-004`（931） | Wanted AA vs Manga AA |
| 1553 | `…-**wanted**-alternate-art-093`（1902） | `…-**manga**-alternate-art-093`（892） | 同上 |
| 1582 | `…sv8a-…-**reverse-holo**-068`（967） | `…-**master-ball**-reverse-holo-068`（7305） | 普通 RH vs Master Ball RH |
| 1583 | `…-**reverse-holo**-062`（884） | `…-**master-ball**-reverse-holo-062`（5882） | 同上 |

→ **canonical slug 必須入 PSA identity fingerprint**。上一版嘅欄位清單冇佢。
→ 同時要存 `requested_id` + `settled_canonical_url`：「兩個 ID 係咪同一張卡」只可以由 settled entity 判定。

### 1.2 真 root cause：population 表收咗 aborted run 嘅寫入

- run **428** `gemrate_hist_full_20260802T194639Z`，`status=aborted`、`observed_count=0`、`accepted_count=0` —— 實際寫咗 **26,332 行 / 55 張卡 / 2023-07-25 → 2026-07-25**。
- 全庫 gemrate PSA10 行：**6,610 行 / 52 張卡**來自 aborted run，其中 **21 張卡嘅最新 POP** 就係佢。
- 另有 **40,304 行 / 482 張卡**來自 `status='complete'`（非標準串）嘅 run，**12 張卡最新 POP** 靠佢。variant 98 嗰個污染 max 嘅 8/2 `4178`，就係 run 464 寫。

→ 上一版嘅 acceptance gate 只拒「同 fingerprint 下較細 POP」，擋唔到 aborted run 塞入嚟嘅**較大** POP，正正就係 98 嗰條路。

### 1.3 表結構物理上放唔落新 model

`market_grader_population_observation` unique key = `(variant_id, grader_code, source_code, observed_date)`，**冇 external_entity_id**。同一日兩個 GemRate ID 塞唔落。variant 98 實際係兩個 ID 逐日交替（7/25 舊 → 7/29–8/1 新 → 8/2 舊 → 8/4–8/7 新），重疊期每日只 survive 一邊。

### 1.4 【新】入圍閘攞錯行 —— 點解 222 POP 嘅卡上到 live

owner 2026-08-08 問「POP 唔夠 1000 點可能上到 live」。查到咗。

`market_grader_population_observation` 喺 `source_code='gemrate'` 底下有**兩種行**：

| 行格式 | external_entity_id | label | 係咩 |
|---|---|---|---|
| 真 GemRate PSA10 | 裸 40-hex（`efabbf9c…`） | `10` | ✅ 唯一可信 |
| grader-supply | 有前綴（`snkrdunk:348126`、`ebay:…`、`gemrate:…`） | `top` | ❌ 唔係 PSA10 |

錯格式行規模：`label='top'` **3,064 行 / 1,700 張 variant**；前綴分佈 `gemrate:` 7,046、`snkrdunk:` 2,571、`ebay:` 416。

**入圍閘冇 filter `top_grade_label='10'` + 裸 id**，結果 lock 39 嗰 762 張：

- **586 張**用真 GemRate PSA10 數字入圍 ✅
- **176 張用咗錯格式行嘅數字入圍** ❌
- 0 張無來源

variant **110** 就係中招嗰個：入圍記錄寫 `psa10Pop=7759`，嗰個 7,759 來自 `snkrdunk:348126` / label `top` / run 391；佢真正嘅 GemRate PSA10 係 **222**。佢係 Serialized 限量卡，天生極低 POP，靠呢個借回嚟嘅數字過咗閘。其餘 175 張僥倖真數字都 ≥1000。

**【2026-08-08 覆查修正】污染範圍只限「入圍閘」，顯示層係乾淨嘅。**

| 層 | 查法 | 結果 |
|---|---|---|
| `market_metric_history_acceptance`（psa10_population / gemrate） | 按 id 形狀 + label 分組 | **43,414 行 / 762 卡，100% 裸 id + label `10`** ✅ |
| `market_canonical_metric_acceptance`（前端排名／市值來源） | join 返 population 行睇形狀 | **9,906 行，100% ok**，零錯格式 ✅ |
| `market_universe_member.selection_signals_json`（入圍閘） | 對返實際 population 行 | **176 / 762 攞咗錯格式行** ❌ |

即係：**錯數字只用嚟決定「邊張卡入到嚟」，冇污染顯示同市值。**variant 110 喺 metric 層讀返嘅係正確嘅 **222**（`efabbf9c…` / label `10` / 2026-08-07）。所以個病係「**一張唔夠格嘅卡被放咗入嚟，然後老老實實顯示佢真實嘅 222**」。

現役 762 張入面，latest accepted POP < 1000 嘅**剛好 4 張**，同 D1 完全對得上：

| variant | POP |
|---|---|
| 110 | 222 |
| 1530 | 582 |
| 1553 | 892 |
| 1552 | 931 |

→ **A 嘅修復範圍收窄**：唔使郁 FE / metric 層（佢哋已經啱），要收嘅係**入圍閘**同 10 個直查原表嘅 Python 檔。

**036 對應動作：**

1. POP 只可以由**單一 function** 讀，admission / projection / validator / FE 全部 call 同一個，唔准各自寫 query。該 function 硬性 `source_code='gemrate' AND grader_code='PSA' AND top_grade_label='10' AND external_entity_id REGEXP '^[0-9a-f]{40}$'`。
2. 前綴行（`snkrdunk:` / `ebay:` / `gemrate:`）**歸還俾自己個 source_code**，唔准再掛喺 `gemrate` 底下。
3. DB 層加 CHECK：`source_code='gemrate' AND top_grade_label='10'` ⇒ id 必須係 40-hex 裸值。
4. validator 加一條：176 張要用**重算後嘅 latest POP** 重新過閘（唔可以用 max，否則會刪多咗）。

### 1.5 【新】宇宙係會「大」，唔係「細」

只信真行（裸 40-hex + label `10`）計最新 POP：

| | 數 |
|---|---|
| 全庫 variant | 1,782 |
| 有真 GemRate PSA10 行 | **1,157** |
| ├ latest ≥ 1000（會入圍） | **1,146** |
| └ latest < 1000（會出局） | **11** |
| 完全冇真行（要重爬先知） | **625** |

現役 live 得 **762**。即係修好個閘之後，宇宙大機會由 762 變 **1,146+**（625 張重爬完仲可能再加）。

→ **整份 plan 之前嘅重心（「刪」）係錯嘅。真正工作量係補 ~384 張新卡嘅圖、價、五語故事。**呢點直接改寫 Stage 4／5 嘅規模同時間估算，見 §7.1。

### 1.6 【更正】run_key 命名唔係 out-of-band 嘅信號

我之前講過 run 4045 / 5625 用裸 sha run_key，懷疑係 repo 外嘅 writer。**查完係我睇錯**：最近 20 個 run 全部都係裸 sha（`snk_en_image`、`pricecharting`、`en_price_ref`…），sha 先係常態，`gemrate_hist_full_<ts>Z` 嗰種先係例外。4045 / 5625 兩個都係 `source_code='gemrate'`、`status='completed'`、762/762，形狀正常。

**但個 concern 冇消失，只係轉咗形**：memory `cardz-unlogged-db-writer` 記低 2026-08-03 有三次零 receipt 嘅寫入。既然 run_key 認唔到，停機就**唔可以靠猜邊個 job**，一定要拉實際 scheduler 清單（Windows Task Scheduler + 任何 WSL 側常駐）逐個熄，再用 §7.2 個方法證明真係停晒。

### 1.7 採集層 filter 太早（D6 要修嘅位）

- **PC**：全 pipelines **冇任何腳本 navigate 去 `completed-auctions-manual-only`**（PC 個 PSA 10 chart）。落錯 default 頁 → `pc_psa10_price_derivation.py:91` 直接 `no_explicit_psa10_field` drop。PC exact 得 349 就係咁嚟。
- **SNK**：快線 `snk_market_data.py` 零 language / tcg 判斷，純靠 exact-ID worklist。同一版面有 EN / JA / KO / ZH 唔同卡唔同圖，worklist 錯咗就日日泵錯價。

---

## 2. Migration `036_identity_bound_qualified_rebuild.mysql.sql`

1. `market_grader_population_observation`：unique key 改為 `(variant_id, grader_code, source_code, external_entity_id, observed_date)`。
2. ⚠️ **【已被 §9.2 推翻，唔好做】** `market_ingest_run.status` 收窄成 ENUM(`running`,`completed`,`aborted`)；歷史 `'complete'` 一次過正規化成 `'completed'`。
3. **隔離 aborted-run 行**：所有 `run.status<>'completed'` 寫落嘅 population 行搬去 `market_population_quarantine`，唔入任何 projection。
4. `catalog_gemrate_population_acceptance`（immutable）：欄位含 `gemrate_id`、`requested_id`、`settled_canonical_url`、`canonical_slug`、`psa_identity_fingerprint`、`psa10_pop`、`raw_payload_sha256`、`prior_acceptance_id`、`run_id`。
   DB 約束：
   - FK `run_id` → 只准 `status='completed'` 嘅 run；
   - 同 `(gemrate_id, fingerprint)` 下 POP **唔准跌**；
   - 跨 `gemrate_id` 或跨 `canonical_slug` 嘅 POP 變動（**升或跌**）一律唔准直接成為 current，要開 incident。
5. `catalog_population_identity_incident`：記 ID 轉換、升跌、slug 變、identity mutation 同處理結果。
6. `catalog_rebuild_generation` / `catalog_rebuild_member`：generation code 格式 `036_<UTC timestamp>`。
7. `market_gemrate_psa10_watchlist` 降級成 compatibility projection（現有 946 行 vs latest-POP≥1000 嘅 1,146，唔再做 authority）。

---

## 3. 執行流程（單一入口 `pipelines/catalog_rebuild.py`）

```text
full         全爬 → 全覆蓋 → 重算 qualified → 補齊 → 刪未入圍
incremental  每日增量
resume       續跑指定 generation
incidents    列全部 POP／身份事故
status       各階段完整度
```

### Stage 1 — GemRate 一次過全爬（D5 + D6）

worklist = **1,782 張 variant × 所有曾用 GemRate ID**（現行 1,168 個 + 所有 rejected 舊 ID + 625 張零 POP 卡嘅 discovery ID）。

- 轉輸：`gemrate_brute_harvest.py`（curl_cffi，唔使 browser）做 discovery；`gemrate_source.py public-card-dump` 逐 ID 攞 page-initiated `/card-details` JSON。**唔使 API key。**
- **全部落 landing raw JSON，一個 byte 都唔篩。** 每個 receipt 存 `requested_id` / `settled_canonical_url` / `canonical_slug` / `raw_payload_sha256` / `dom_sha256`。
- 舊 landing 粗暴覆蓋（同源同表，冇加工）。

### Stage 2 — 入 DB（唯一取捨點）

單一 deterministic acceptance function：

1. raw payload 取唯一 `grader="psa"` row。
2. fingerprint = `description + year + set_name + card_number + parallel + language + canonical_slug + specid/set_url`。
3. 逐個 variant 分類佢名下所有 ID：同一 printing 嘅 alias／唔同語言／唔同 parallel／唔同 set／無法判斷。
4. slug 唔同 → 唔同 printing → 舊 ID 嘅 population／price／sales／qualification lineage 從該 variant 切走（raw 留 landing）。
5. 只有唯一 current ID 嘅 POP 序列進 acceptance。
6. AI 同人手 manifest 都行同一個 function，冇旁路。

### Stage 3 — 重算 qualified

`latest POP ≥ 1000` 入圍。未解決 incident 保持 incident，唔當 <1000。最終數由結果計，唔寫死。

### Stage 4 — 補 SNK / PC / 價（D4 + D6）

- **SNK**：同一 product 頁面所有 language variant（EN / JA / KO / ZH）**全部爬晒落 landing**，入 DB 先按 variant 嘅 `card_language` + `(tcg, setName, collectorNumber, parallel)` 揀。OP 同 Pokémon 分開規則表；OP 嘅異畫（Manga AA / Wanted AA / SP / Comic Parallel）要逐個 printing code 對到。
- **PC**：collector 一定要行到 `completed-auctions-manual-only`（＝PSA 10）嗰個 chart 先算攞齊；default 頁唔係 PSA 10 就**轉去 PSA 10 頁**，唔准 drop。canonical `/game/...` URL + numeric product id + 相同 printing tuple 三者齊先可以 bind。Cloudflare 走 WSLg 有頭 Chrome + CDP 9222。
- current 價只認 PC / SNK exact；TPL / G10 / eBay 只寫歷史 bar。
- market cap = current GemRate PSA10 POP × 有效 PSA10 價。

### Stage 5 — 圖 + 五語故事

- 圖：exact SNK `master.primaryMedia.imageUrl` → 冇先 exact PC product image。禁 Kado / TCGplayer / 無來源 / AI 圖。
- 故事：`en / zhTW / zhCN / ja / ko`，綁 `variant_id + current PSA identity fingerprint`。fingerprint 一變自動失效重排。現況：762 張齊 5 語、53 張 4 語、217 張 1 語。

### Stage 6 — 刪除 + 啟用（單一 transaction）

**逐表 keep / delete 清單**（全庫 50 張表帶 `variant_id`，唔准用「等」）：

- **DELETE**：`catalog_variant` 及 identity / population / price / sales / image / story / index / freeze / warehouse / alias / locale / printing_identity / universe_member 等業務行。
- **KEEP（人手權威，唔准跟住刪）**：`market_image_rejection_registry`（43 行人手 reject）、`market_image_review_approval`、`market_identity_review_resolution`、`market_universe_lock`（immutable 歷史）、`market_raw_payload_object` 指標。
  > 理由：incremental 會將卡加返，人手 reject 記憶一冇，同一張錯圖會翻生。
- **KEEP**：DB 外 landing / `data/private/gemrate/cards/**` raw cache 全部保留。

rollback 條件：未解決 incident、pending 身份、外鍵殘留、產品資料缺口。

### Stage 7 — 收尾

- 改 `PROJECT_STATE.md` 同 `CARDZMC_EXPERIENCE_LEDGER.md` 嗰兩條「唔准縮細 762」規矩（D1）。
- FE 版本標 **FE02**；backend generation 標 **036_<ts>**。
- 3800 係 live-db 直讀 3308，**刪完即刻變樣，唔使 promote** —— 呢個係預期。
- AWS / public snapshot **唔郁**。下次 republish 會由 762 變成新數（少咗 110 / 1530 / 1552 / 1553），要獨立 approve。

---

## 4. 全量重建期間

`data/runtime/cardz-writer.lock` 做 stop-the-world：daily lane（`snk_market_data.py` + `operator_control.py db-tidy`）同 gemrate incremental 全部停，直到 generation 啟用。

---

## 5. 驗收 `scripts/validate_catalog_rebuild.py`

- 每張 current 卡只有一個 GemRate ID、一個 PSA identity fingerprint、一個 canonical slug。
- population series 零跨 GemRate ID／跨語言／跨 printing。
- 同 fingerprint 內 POP 單調不減；跨 ID 嘅升跌 100% 有 incident 記錄同結案理由。
- **零** population 行來自 `run.status <> 'completed'`。
- `market_ingest_run.status` 只有 ENUM 內嘅值。
- 17 張多 ID variant 全部分類完成；4 宗下降 + 2 宗上跳（1582 / 1583）逐張有結論。
- qualified cohort 只由 current identity 嘅 latest POP ≥ 1000 組成。
- 未入圍 variant 及其 dependent 業務行喺 3308 = 0；`market_image_rejection_registry` 行數不變（43）。
- unresolved incident = 0。
- 每張 qualified 卡：PSA identity、SNK/PC 最終狀態、價、market cap、圖、五語故事齊。
- 圖 100% SNK-first / PC-only-fallback。
- current strict projection `database_lineage = 0`。
- `productReady == qualifiedCount` 先可啟用。
- 3800 讀到 `036_<ts>` + `fe02`；AWS snapshot SHA 不變。

---

## 6. A–G 開工前必修清單

長跑之前要清嘅嘢。每條有：**問題 / 證據 / 做法 / 卡唔卡住開工**。

> **A 同 E 都唔使 owner 決定任何嘢** —— 純粹工程修復，我照做。owner 只需要知道「做完會點驗證」。下面每條尾都有 ✅ 驗收句。

### A — POP 讀取有多個出口（🔴 卡住開工，但**零決定**）

**問題**：入圍、投影、驗證、前端各自寫 query 讀 POP，冇統一 filter，所以 grader-supply 行冒充咗 PSA10。
**證據**：762 張入圍卡入面 176 張用咗錯格式行；variant 110 用 `snkrdunk:348126`／label `top` 嘅 7,759 代替真值 222。髒行總量：label `top` 3,064 行／1,700 卡；前綴 `gemrate:` 7,046、`snkrdunk:` 2,571、`ebay:` 416。
**做法**：
1. 新增 `pipelines/population_authority.py::latest_psa10(variant_ids)` —— **全庫唯一** POP 出口。硬 filter：`source_code='gemrate' AND grader_code='PSA' AND top_grade_label='10' AND external_entity_id REGEXP '^[0-9a-f]{40}$' AND run.status='completed'`。
2. **實際要改嘅 call site（已 grep 出，共 10 個 Python + 2 個 script）**：
   `pipelines/collect_control.py`、`converge_printing_identity.py`、`db_runtime.py`、`market_alerts.py`、`new_era_db_tidy.py`、`operator_control.py`、`operator_fe_export.py`、`resolve_active_psa_identity.py`、`scripts/canonical_seed.py`、`scripts/validate_psa_identity_repair.py`。
   `apps/web/src/lib/live-db-snapshot.ts` **唔使改** —— 佢行 `market_metric_history_acceptance` 呢層，實測 100% 乾淨。
   加 CI／pre-commit 擋住新嘅裸 query。
3. 036 migration 加 CHECK：`source_code='gemrate' AND top_grade_label='10'` ⇒ id 必須裸 40-hex。
4. 前綴行歸還返自己個 `source_code`（`snkrdunk` / `ebay`），唔准再掛喺 `gemrate` 底下。
5. **即場證明個 CHECK 會 fire**：試插一行 `gemrate` + `snkrdunk:xxx` + label `10`，要被拒。冇 call site 嘅檢查＝冇檢查。

✅ **驗收**：(a) 全 repo grep 唔到第二個直查 `market_grader_population_observation` 嘅地方；(b) 試插髒行被 DB 拒絕（貼錯誤訊息）；(c) 重跑入圍，762 張裏面 176 張錯來源全部變成用真值。

### B — 舊 checkout 348 個未存檔改動（🔴 **做法已被 §9.1 推翻 —— 個 DB container 就係佢管住**）

**問題**：`cardz-market-cap` 有 348 個未 commit 改動 + 3 個獨有 commit，直接刪就冇咗。
**做法**：刪之前出一份 diff 摘要（只列檔名 + 改動行數 + 分類：腳本修正 / 實驗 / 垃圾），owner 掃一眼決定 salvage 邊啲，salvage 完先刪。
**WSL 側注意**：`~/cardz-market-cap`（branch `ui-experiments-20260725`）**冇任何 remote**，唔會指住 Windows 呢個 folder → 刪 Windows 嗰個唔會累到佢。但 `~/cardz-aws`（branch `wsl-cutover-20260731`）係 `~/cardz-market-cap` 嘅 **linked worktree**，兩個都唔好掂。fastpath runbook 就住喺 `~/cardz-aws/docs/`。

### C — variant 110 出局同「唔准縮細 762」規矩打架（🟡 文件要一齊改）

**問題**：110（One Piece Championship 2024 Luffy Serialized，OP07-109）真 POP 222 < 1000，一定出局；但 `PROJECT_STATE.md:16` 同 `CARDZMC_EXPERIENCE_LEDGER.md:128` 寫死唔准縮細 762 baseline。
**做法**：D1 已批。改嗰兩條規矩，改成「唔准為咗掩飾缺證據而縮細；因身份／POP 修正而出局要留 incident 記錄同理由」。連 110 一齊列成 4 宗（110 / 1530 / 1552 / 1553）+ 另外 11 張 latest<1000 嘅，全部逐張寫低點解。
> 註：修好之後宇宙係淨增（762 → 1,146+），所以「縮細」呢個講法本身要重寫，見 §1.5。

### D — 只查跌唔查升（🟡 併入 Stage 2）

**問題**：上一版只當 POP 下降係事故。
**證據**：1582 由 967 → 7,305；1583 由 884 → 5,882。兩單都係「普通 Reverse Holo」被換成「Master Ball Reverse Holo」，同下降嗰批係同一種病。
**做法**：D2 已批。acceptance 只要**跨 gemrate_id 或跨 canonical_slug**，唔理升定跌一律開 incident、唔准直接成為 current。驗收要 1582 / 1583 逐張有結案。

### E — aborted run 寫咗嘢入 DB（🔴 卡住開工，但**零決定**）

**問題**：`market_ingest_run` 記住失敗，但寫入照樣落咗表。
**證據**：run 428 `gemrate_hist_full_20260802T194639Z` status=`aborted`、observed 0、accepted 0，實際寫 **26,332 行 / 55 卡 / 2023-07-25→2026-07-25**。全庫 gemrate PSA10：**6,610 行 / 52 卡**來自 aborted run，其中 **21 卡嘅最新 POP** 就係佢；另 **40,304 行 / 482 卡**來自 `status='complete'`（非標準串）嘅 run，**12 卡**最新 POP 靠佢。
**status 現況**：`completed` 3,899、`complete` 798、`aborted` 3、`running` 1。

3 個 aborted 全部係 2026-08-02 夜晚同一輪：428 `gemrate_hist_full_…194639Z`、429 `…fast_…195005Z`、430 `…fast_…195118Z`，合共寫 **26,332 行 / 55 卡**。

嗰 1 個 `running`（id **3568**，`cardz_normalized` incremental）：起 2026-08-01 05:48:01、**completed_at 05:48:06**（5 秒後就有結束時間）、observed 9,091 / accepted **0**、寫咗 **37 行 / 20 卡**。一星期前死咗冇翻 status —— **形狀已經自證係 aborted，我直接當 aborted 處理，唔使問。**

**做法**：
1. `complete` → `completed` 正規化（798 行）；run 3568 標 `aborted`（理由入 migration comment）。
2. 所有 `run.status <> 'completed'` 寫落嘅 population 行搬去 `market_population_quarantine`。
3. `market_grader_population_observation.run_id` 已存在（bigint unsigned, NOT NULL）→ 可以直接 join 判定，唔使估。
4. ingest 收尾改成：**只有 run commit 成 `completed` 之後，寫入先 visible**（同一 transaction，或 staging 表 + swap）。

✅ **驗收**：(a) `SELECT status, COUNT(*) FROM market_ingest_run GROUP BY 1` 只剩 ENUM 三個值；(b) 任何 `run.status<>'completed'` 嘅 population 行喺主表 = 0；(c) 人為 kill 一個 ingest 中途，主表零新行、staging 有殘留、re-run 可續。

### F — 停機唔可以靠 run_key 認 job（🟢 **§9.5 已證實 10 個 task 全部 Disabled，只需驗證**）

**更正**：裸 sha run_key 係常態唔係異常，見 §1.6。
**問題**：既然認唔到，「熄晒每日更新」就冇客觀驗收。
**做法**：
1. 拉實際清單：`schtasks /query /fo LIST /v`（Windows）+ WSL 側常駐 / cron，逐個對出邊啲會寫 3308。
2. 全部停咗之後，**用結果證明**：記低 `MAX(market_ingest_run.id)` 同 `MAX(created_at)`，等 ≥ 1 個完整 daily 週期，再查一次，冇新 run 先算停到。
3. `data/runtime/cardz-writer.lock` 唔可以淨係「有個檔」，要有 call site：所有 ingest 入口開頭 check lock，check 唔到就 exit 非零。**寫完要即場試一次**（開住 lock 行 `snk_market_data.py`，要即刻死）。

### G — `cardz-psa10-price` skill 路徑會爆（🟡 唔使 owner 拍板，已結案）

**先講清楚歸屬**：`~/.agents/skills/cardz-psa10-price/SKILL.md` **係 CARDZMC 嘅，唔係 CardzOS 嘅**。佢通篇 `cd cardz-market-cap`、行 `pipelines/qualified_pool_operator.py`、寫 `market_price_observation`、叫你更新 `PROJECT_STATE.md`。CardzOS 係另一個 skill（`cardzos`，model 鎖 codex/gpt-5.6-sol，唔關呢度事）；`cardz-price-research` 先係 CardzOS 側嘅、而且已標 archived。

**兩個要修嘅位：**

1. **路徑指住將要刪嘅 folder**（🔴 一刪即爆）。SKILL.md 第 31 / 78 行寫死 `cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap`。B 執行完之後，呢個 skill 全部指令會死。→ 改成 `cardz-market-cap-fe-db-20260805`。
2. **「權威打架」係我睇錯，冇衝突。** owner 2026-08-08：「PC + SNK 邊個有數咪用邊個囉，冇問題冇衝突。我只不過陳述件事，就係如果美版、英版多數喺 eBay 嗰度有成交，噉你梗係要睇 PC 腳本啦。即係唔係強制性嘅。」
   → D4 已按呢個改寫成「邊個有數用邊個」，唔係硬性階梯。skill §2 個表本身冇錯，只需要補一句「PC 對英/美版係常用主線（eBay 成交集中喺嗰度），唔係最後補底」，同埋標明 TPL 出嘅 bar 唔入市值。
**做法**：改路徑（🔴 一定要，B 之前做）＋ 補上面嗰句（🟢 順手）。**唔使等 owner，Stage 4 唔受阻。**

---

## 7. 長跑會炸嘅位（plan 本身嘅問題）

### 7.1 工作量估算成個錯咗方向（🔴）—— 已逐項量度

plan 通篇當緊件事係「刪 4 張卡」，實情係**加 388 張**。以下全部係 2026-08-08 對 3308 實測，唔係估：

- 候選宇宙（latest 真 POP ≥ 1000）＝ **1,146**
- 其中已經喺 live 嘅 ＝ **758**
- → **淨新增 388 張**（現役 762 − 758 ＝ 4，就係要刪嗰 4 張）

| 項目 | 1,146 候選入面已齊 | 缺口 | 嚴重度 |
|---|---:|---:|---|
| 五語故事齊（≥80 字） | 758 | **388 張 × 5 語 ＝ 1,940 篇** | 🔴 最長路 |
| 有 frozen canonical image | 761 | **385 張** | 🔴 |
| 有 PC 或 SNK 價觀測 | 1,113 | **33 張** | 🟢 細 |

單源覆蓋（候選 1,146 入面）：ebay 957、pricecharting 929、snk_psa10 531、snkrdunk 474、g10_kline 314、tcgfish 52。
> 注意：呢啲係「有價觀測」，唔等於「exact identity 綁得實」。D4 要求 current 價要 exact bind 先算數，所以真實可用數字會低過 929 / 531，Stage 4 要另外量一次 exact-bind 覆蓋，唔好攞呢個表當已完成。

**五語故事係最長嗰條 critical path**，要喺 Stage 1 就開始排，唔可以等 Stage 5。另外 625 張零 POP 卡重爬完可能再加候選，388 呢個數要留浮動。

### 7.1b 🔴 五語故事根本冇 in-repo 生產者（最易炸嘅位）

拆開睇每種語言點嚟：

| 語言 | 生產者 | 現況 |
|---|---|---|
| `en` | `pipelines/g10_research_ingest.py`（食 G10 `summary_en.json`，中位數 2,544 字） | 要每張卡有 G10 research 資料；新 388 張多數冇 |
| `zhTW` / `zhCN` / `ja` | `pipelines/editorial_locale_sync.py`，讀 `temp/translate-out-*.json` | **`temp/` 入面一個 `translate-out-*.json` 都冇** —— 譯文係 repo 外手動／LLM 步驟產出，冇留低流程 |
| `ko` | **完全冇 producer** | `editorial_locale_sync.py` 自己個 docstring 明講只寫 zhTW/zhCN/ja |

仲有一個 dangling reference：`editorial_locale_sync.py` 話佢嘅驗收條款對齊 `packages/market-data/src/validate.ts:280-297`（四語齊、每語 ≥80 字、四語互不相同），但**呢個檔喺呢個 working tree 已經唔存在**。而 `packages/market-data/src/schema.ts:17` 嘅 `Locale` 係 **5 個**（含 `ko`）。即係「四語」同「五語」兩套規矩並存，驗收檔本身又冇咗。

**必修**：
1. 補一個真正嘅 story pipeline（EN 來源 + 翻譯步驟 + ko），或者明文寫低「翻譯係人手／外部 LLM 步驟」並定死輸入格式同批次大小。
2. 重建 `validate.ts` 或者將驗收搬入 Python，並統一成 **5 語**。
3. 1,940 篇要分批 + 可續 + 有 progress，唔可以一 shot。

### 7.1c 🔴 兩個「單一入口」腳本根本未存在

plan §3 寫住「單一入口 `pipelines/catalog_rebuild.py`」、§5 寫住「驗收 `scripts/validate_catalog_rebuild.py`」。

**兩個檔都唔存在。** `pipelines/` 有 68 個檔、`scripts/` 得 5 個，冇呢兩個。migration 最新係 `035_gemrate_provenance_psa_identity_resolution.mysql.sql`（036 個號啱）。

即係份 plan 讀落好似「行個指令就得」，實際係**要先寫兩個新工具**。呢個唔講清楚，開工第一日就會卡住。

### 7.1d 🔴 stop-the-world 機制唔存在（零 call site）

全 repo grep `cardz-writer.lock` / `writer_lock` / `writerLock`：**零結果**。

即係 §4 講嘅 stop-the-world **而家係一句空話**。跟你嘅硬規矩「有檢查但零 call site 就當冇檢查」，呢個要當**未做**。

**必修**：喺 `db_runtime.py` 拿連線嗰層插 lock check（唔係逐個腳本加，逐個加一定漏），check 唔到就 exit 非零；寫完即場開住 lock 行一次 `snk_market_data.py`，要即刻死先算數。

### 7.2 冇還原點就開始刪（🔴 最危險，已證實）

**實測：搵勻 `C:\Users\jackson0202` 深 6 層，冇任何 >5MB 嘅 `.sql` / `.sql.gz` / dump zip。零備份。** 3310 已經刪咗，3308 係唯一一份。
空間唔係問題：C: 仲有 **293 GB** free，gemrate raw cache 得 28 MB，全爬完估 100–150 MB。
**做法**：開工前 `mysqldump` 全庫 → 記 sha256 → **實際 restore 去一個臨時 schema 驗一次**（restore 唔試過就當冇備份）。dump 放 3308 主機以外嘅碟。

### 7.3 Stage 6 一個 transaction 跨 50 張表（🟡）

MySQL 大批 delete 一個 transaction 會谷爆 undo / lock。
**做法**：改成分批（每批 N 個 variant）+ 每批寫 progress + 可續跑；「單一 transaction」呢個保證改成「單一 generation，未 commit generation 之前 projection 唔切」。

### 7.4 Resume 粒度（🟢 已經有，唔使做）

覆查：`gemrate_source.py` 本身已經有 **per-ID resume** —— `_has_complete_public_card_capture(cards_dir, gid)`（`pipelines/gemrate_source.py:1703`）會跳過已完整攞到嘅 ID，`--resume` 直接用得。另有 `_has_verified_direct_identity_receipt` 保障 receipt 完整先算跳。
**做法**：Stage 1 一律加 `--resume`。唔使再造新 checkpoint 機制。

### 7.5 冇 rate limit / 封鎖策略（🟡）

幾千個 GemRate request 一次過轟，冇講 concurrency、backoff、被 Cloudflare 彈返點算。
**做法**：定死 concurrency 上限 + 指數 backoff + 429/403 自動熄火等；被封唔准靜靜跳過，要標記 `pending` 留返再跑。

### 7.6 PC 靠 WSLg 有頭 Chrome + CDP 9222，係單點（🟡）

1,146 張卡要行 `completed-auctions-manual-only` 頁，headless 過唔到 Cloudflare，要人手開住個有頭 Chrome。
**做法**：PC 做成可分批 queue，斷咗可續；唔可以當佢一口氣跑完。

### 7.7 重建期間 FE02 會見到半截狀態（🟢 講清楚就得）

3800 係 live-db 直讀 3308，Stage 2–6 期間本地前端會時好時壞。**呢個係預期**，唔係 bug；AWS / public snapshot 全程唔郁。

### 7.8 `operator_canonical_image_projection` 名不副實（⚠️ **解釋已被 §9.6 更正：真因係空 view，唔係表死咗**）

前端 `live-db-snapshot.ts:231` 攞圖第一路就係讀呢張表 —— **但全表得 12 行 / 12 張卡**。而 1,146 候選入面有 **761 張**係靠 `operator_binding_freeze` → `market_canonical_image_acceptance` → `market_image_asset` 嗰條 fallback 路先有圖。

即係「canonical image projection」實際唔係權威，真權威係 freeze 路徑。036 唔好當佢係圖嘅單一真源，否則會以為 1,134 張冇圖。要就補實佢，要就明文降級（同 `market_gemrate_psa10_watchlist` 一樣處理）。

### 7.9 其他未拍板 / 未對數嘅細節

- **3 張 One Piece 冇 exact SNK EN 圖**（128 / 958 / 1450）vs「SNK-first、PC-only fallback」規則 → 要 owner 決定用 PC 圖定留白。
- `market_gemrate_psa10_watchlist` 946 行 vs 真 latest≥1000 嘅 1,146 → 差 200，降級成 compatibility projection 之前要對數，唔好靜靜當佢啱。
- 176 張錯格式入圍嘅卡，重算之後**可能有啲跌出去**（現時只確認 110 一張穿底，其餘 175 張真值 ≥1000 但要逐張覆核）。
- ~~ENUM 收窄前嗰 1 個 `running` run 要有人判定~~ → 已自行判定（run 3568，見 §6-E）。
- `market_population_quarantine` 表**未存在**，036 要建（`SHOW TABLES LIKE '%quarantine%'` 空）。
- G10 唔可以完全冚 —— D4 話 G10 只入歷史價，但 **EN 故事嘅來源就係 G10 research**（`g10_research_ingest.py`）。兩個角色唔同，唔好一刀切停埋 G10。

---

## 8. 建議次序

```text
【第 0 關：地基 — 唔做就唔好開工】
  0.1  Docker/compose 遷移（9.1）
       停 3801 node → compose.backend.yaml 抄過嚟 + 寫死 name:
       → 驗到同一個 container + 同一個 14GB volume → 先至可以刪舊 folder
  0.2  備份（9.11）docker exec mysqldump → 驗檔尾 → root 開 temp schema
       → restore → 逐表 diff row count → DROP temp schema
  0.3  停機驗證（9.5）10 個 task 已 Disabled，記 baseline max(id) → 隔日再查
  0.4  寫入封鎖（9.4）6 個 connect 函數加 lock check
       ＋ 全爬期間 REVOKE INSERT/UPDATE/DELETE 做後盾 → 即場證明會死
  0.5  B 舊 folder：diff 348 個改動 → salvage → 確認 0.1 完成 → 刪

【第 1 關：先修根因，再起工具】
  1.1  9.6 operator_strict_source_identity — 補 PC/SNK 嘅 bind_evidence_json
       （唔修，388 張新卡攞唔到價、攞唔到圖）
  1.2  9.3 population_authority.latest_psa10() + 改 4 個有 bug 嘅 call site
       測試要包 label='top'、label='9.5'、前綴 id 三種髒行
  1.3  9.2 ingest_run_status 常數統一 8 個 call site（ENUM 收窄押後至 037）
  1.4  7.1c 寫 catalog_rebuild.py + validate_catalog_rebuild.py
  1.5  9.9 Stage 6 刪除邏輯（零現成 code）＋ KEEP 表改 identity-stable key
  ↓
  migration 036 —— 每句跟 031 嘅 idempotent DDL 寫法（9.10）
  含 market_population_quarantine；唔郁 status ENUM

【第 2 關：長跑】
  Stage 1 全爬（gemrate_source.py --resume）
  同步開故事線：EN 388 篇 + zhTW/zhCN/ja 各 388 + ko 全 1,146（9.8）
  ↓
  Stage 2 → 3 → 4（PC 補 fetch 484 個 HTML，唔使改 navigation，9.7）→ 5 → 6
```

> **一 take pass 嘅定義**：第 0、1 關全部有實證通過先入第 2 關。第 0 關任何一項冇實證，長跑一定要重做。

---

## 9. 深度審計發現（2026-08-08，5 隊平行審計 + 自驗）

以下每條都有實證。**編號 9.x 全部係新嘢，唔係重覆上面。**

### 9.1 🔴🔴 舊 folder 唔可以直接刪 —— 個 DB 就係佢管住

**推翻 §6-B 原本嘅做法。**

```
docker inspect cardz-market-cap-db-1
  → com.docker.compose.project.config_files
  = C:\Users\jackson0202\Documents\Playground\cardz-market-cap\compose.backend.yaml
```

- MySQL **唔係** Windows native，係 Docker container `cardz-market-cap-db-1`（mysql:8.4），port `127.0.0.1:3308→3306`
- 管佢嘅 compose 檔喺**舊 folder**，唔係新 folder（新 folder 個 `compose.yaml` 只有 `web`）
- data volume 叫 `cardz-market-cap_cardz_mysql`（**14.03 GB**），個名係由**舊 folder 個 folder name** 推導出嚟
- 仲有一個 **Next.js production server 行緊喺 port 3801**（PID 16228），module root 就係舊 folder 嘅 `node_modules`

**即係話：照 §6-B 刪咗舊 folder，會 (a) 冇咗管 DB 個 compose 檔、(b) 之後喺新 folder 行 `docker compose` 會算出唔同 project name、resolve 唔到原本個 volume，最壞情況係**靜靜起一個全新空 volume**、(c) 3801 個 node 揸住 file handle，Windows 可能直接刪唔郁或者刪一半。

**改正做法（B 要重寫）：**
1. 先 `Stop-Process` 掉 3801 個 node（或者指去新 repo）
2. `compose.backend.yaml` **複製**去新 folder，並且喺入面明寫 `name: cardz-market-cap` 鎖死 project name（或者永遠 `COMPOSE_PROJECT_NAME=cardz-market-cap`）
3. 確認新位置 `docker compose ... ps` 認得返同一個 container + 同一個 volume
4. **確認之後**先刪舊 folder

### 9.2 🔴🔴 ENUM 收窄會即刻整死跑緊嘅 pipeline

**推翻 §6-E 第 1 步。**`'complete'` **唔係歷史殘留，係今日仲寫緊、仲讀緊嘅活值。**

寫入方（會被 ENUM 擋死）：
- `pipelines/db_runtime.py:1301` — `UPDATE market_ingest_run SET status='complete', …`
- `pipelines/fx_db_load.py:105` — 同樣

讀取方（正規化咗就永遠搵唔返嘢，而且**靜靜失敗**）：
- `pipelines/market_alerts.py:604` — `SELECT id FROM market_ingest_run WHERE status='complete' ORDER BY …LIMIT 1`（`if run:` 守住，冇 error，只係 index/ranking lineage 停止更新）
- `pipelines/db_retention.py:215` 同 `:259` — `run.status='complete'` 係 payload compaction 嘅閘（管住 `market_raw_payload_object` 300k 行 / 215 MB）
- `pipelines/db_runtime.py:1006` — replay 去重判斷
- `pipelines/g10_research_ingest.py:30-36` 個 docstring **明文解釋**點解要分開 `complete` / `completed` —— 呢個係故意嘅設計，唔係手民之誤

`SELECT @@sql_mode` 確認有 **`STRICT_TRANS_TABLES`** → ENUM 收窄之後嗰兩句 UPDATE 直接 `1265 Data truncated`，run 會永遠卡喺 `running`。

**改正做法：**
- **036 唔好收窄 ENUM。** 先寫一個 `pipelines/ingest_run_status.py` 統一常數，改晒 8 個 call site，證明冇人再寫 `'complete'`，再喺 **037** 先收窄。
- 「aborted run 嘅行要隔離」呢個目標唔變，改用 **run_id join `status NOT IN ('completed','complete')`** 做，唔綁死喺 ENUM 上。

### 9.3 🔴 Bug 現場精確定位，而且範圍比我講嘅闊

`pipelines/operator_control.py:2057-2082`，function `latest_psa10_pop(cur)`：

```sql
SELECT g.variant_id, g.top_grade_population, g.effective_at
FROM market_grader_population_observation g
INNER JOIN (SELECT variant_id, MAX(effective_at) mx
            FROM market_grader_population_observation
            WHERE grader_code='PSA' GROUP BY variant_id) t
  ON t.variant_id=g.variant_id AND t.mx=g.effective_at
WHERE g.grader_code='PSA'
```

**冇 `source_code` filter、冇 `top_grade_label` filter、冇 id 形狀檢查。** 佢淨係攞「`grader_code='PSA'` 入面 `effective_at` 最新嗰行」，然後叫佢做 `psa10Pop`。呢個 dict 直接餵去 `cmd_scan_candidates`（:2085，`min_pop=1000` 喺 :2096-2099）。

所以除咗 `label='top'` 之外，**`label='9.5'` 嘅 33,232 行一樣入到嚟**。

→ §6-A 個 DB CHECK **唔係足夠嘅後盾**（佢只管 `label='10'` 嗰批）。真正修法 100% 喺 application code：所有人行 `population_authority.latest_psa10()`。§6-A 嗰個「插髒行測試」要加多一個 case：插一行 `label='9.5'`、`effective_at` 最新，證明入圍閘唔會攞佢。

其他已查嘅 call site 狀態：
- 有 bug：`operator_control.py:2057`、`market_alerts.py:222-231`、`converge_printing_identity.py:114`、`collect_control.py:611-619`（有 source 冇 label）
- **本身已經啱**：`resolve_active_psa_identity.py:106-119`、`new_era_db_tidy.py:1454-1520 / 3010-3018`

### 9.4 🔴 Writer lock 放喺 `db_runtime.py` 覆蓋唔到全部

14 個檔行 `connection_from_args`（`db_runtime.py:379`），但 **5 個檔自己開 `pymysql.connect`，完全繞過**：

| 檔 | 自開連線 | 有寫入 |
|---|---|---|
| `snk_market_data.py` | `_db_connect()` :506 | 4× INSERT/UPDATE |
| `qualified_pool_operator.py` | `db()` :94 | 7× INSERT/UPDATE |
| `c11_pc_sold_ingest.py` | `db()` :76 | 3× INSERT/UPDATE |
| `pc_ungraded_reference_ingest.py` | `db()` :58 | `--write` 時 INSERT |
| `ingest_snk_trades_sales.py` | :58 | INSERT run / sale |

→ lock 唔可以只放 `db_runtime`。**兩層做**：(a) 6 個 connect 函數全部加同一個 lock check；(b) 真正 fail-closed 嘅後盾係 DB 層 —— 全爬期間 `REVOKE INSERT,UPDATE,DELETE ON cardz_market_cap.* FROM 'cardz'@'%'`，做完再 GRANT 返。code drift 都殺唔穿。

### 9.5 🟢 日更其實已經全部熄咗

`schtasks /query /fo LIST /v`：**10 個 cardz 相關 task 全部 `Status: Disabled`、`Next Run Time: N/A`**（含 `CARDZ-Market-Cap-Daily`、`CARDZ-Nightly-Bake`、`CARDZ-GemRate-PSA10-Watchlist`、`CARDZ-TAG-Daily-Capture` 等）。WSL 側 `crontab -l` / `/etc/cron.d` / systemd timers 冇 cardz。`SHOW PROCESSLIST` 得 1 條（審計自己）。

→ **F 由「要熄」變成「只需驗證 + 唔好誤開」。** baseline 已記低：
```
market_ingest_run            max(id)=7865    max(started_at)=2026-08-07 09:47:43
market_source_observation    max(id)=3975227
market_price_observation     max(id)=4441343
market_grader_population_observation max(id)=321241
catalog_variant              max(id)=1869
```
過一日再查一次，全部無變 = 真係停咗。

### 9.6 🔴🔴 `operator_strict_source_identity` 對非 gemrate 來源係空嘅

呢個係**全新根因**，一次過解釋咗三個各自被當成獨立問題嘅症狀。

該 view 要求 `bind_evidence_json` 有 `providerClaims.*` + `evidence.type` 結構 —— **只有 `gemrate` 寫成呢個 schema**。`pricecharting`（349 條 exact）同 `snkrdunk`（154 條 exact）仲用舊嘅 `"contract":"exact-provider-identity-v1"` 格式，`bound_tcg_code` / `bound_card_language` 全部空。

```
SELECT source_code, COUNT(*) FROM operator_strict_source_identity GROUP BY source_code
  → gemrate 762。pricecharting 0。snkrdunk 0。
```

連鎖後果（全部而家就係 0 行）：
- `operator_eligible_accepted_psa10_price_history`
- `operator_eligible_pricecharting_variant`
- `operator_accepted_psa10_price_history`
- `operator_canonical_image_projection` **得 12 行** —— 唔係「表死咗」，係佢 LEFT JOIN 呢個空 view，把所有 SNK 血統嘅圖全部濾走（§7.8 我原本嘅解釋要更正）

前端而家睇落冇事，係因為佢直接讀 `market_canonical_metric_acceptance` 繞過咗呢條鏈。**但 036 一重算 ranking generation，388 張新卡會攞到零個合資格 PC/SNK 價、零張 SNK 圖。**

→ **呢條要排喺 Stage 4 之前修**，唔係 Stage 4 入面修。（一個未解點：run 5628 喺 2026-08-07 成功寫咗 48 條 snkrdunk 價，理論上要行同一條映射 —— 未查到係 view 之後先壞定係行咗另一條路。）

### 9.7 🟡 PC「要 navigate 去 PSA10 頁」呢個講法係錯嘅

`completed-auctions-manual-only` **唔係另一條 URL，係同一版 SSR 入面 `VGPC.chart_data` 個 chart key**（`pricecharting_page_parse.py:22-45,104-136,210-268`）。一次 `page.goto` 就攞晒全部 grade series。

實測：本地 cache `data/private/pricecharting_session/html/full900/` 嘅 **437 個 HTML 檔，100% 有正值 `manualonly` series**，冇一個踩到 `no_explicit_psa10_field`。

→ 真正缺口係另外兩樣：(a) 921 個已 map 嘅候選只 fetch 咗 **437** 個 HTML；(b) 9.6 個身份綁定 schema。**§1.7 嗰段關於 PC 嘅描述要改。**

### 9.8 🔴 韓文故事係假嘅 —— 761 張全部係模板

我親自查證：

| locale | distinct | rows | 平均字數 |
|---|---:|---:|---:|
| en | 1,007 | 1,030 | 1,549 |
| ja | 808 | 814 | 226 |
| zhTW | 811 | 814 | 197 |
| **ko** | 761 | 761 | 203 |

`ko` distinct 761 睇落好靚，但實物係：

```
KO  1  2023 Pokemon Svp EN-SV Black Star Promo Pikachu … 는 CARDZ 에서 PSA 10 가격…
KO  2  2021 Pokemon Japanese S Promo Full Art/Pikachu … 는 CARDZ 에서 …
```

**淨係卡名唔同，後面成句一模一樣嘅樣板。** 對比 zhTW 係真人寫嘅收藏史敘述。

而 `ko` 根本冇 producer：`editorial_localization.py:61` 寫死 `STORY_LOCALES=("en","zhTW","zhCN","ja")`，`editorial_translate_queue.py:89` 個 SQL 都係 `locale_code IN ('en','zhTW','zhCN','ja')`。

→ **「762 張齊 5 語」係行數真、內容假。**呢個比「冇 producer」更差，因為佢靜靜過咗 ≥80 字嗰道閘。036 要當 **ko 由零開始**，即係 **1,146 篇**，唔係 388 篇。

### 9.9 🔴 全 repo 冇一句 `DELETE FROM` —— Stage 6 係 100% 新 code

`grep -rn "DELETE FROM" pipelines/*.py scripts/*.py` → **零結果**。`operator_control.py db-tidy` 係重建投影，唔係刪卡。

而且 KEEP 清單嘅設計有結構性問題：`market_image_rejection_registry` / `market_image_review_approval` / `market_identity_review_resolution` **全部 FK 到 `catalog_variant(id)`，而 `catalog_variant.id` 係 AUTO_INCREMENT**。卡刪咗再由 incremental 加返 = 新 id = 保留咗嘅 reject 記錄變孤兒，**正正做唔到「唔好等錯圖翻生」呢個目的**。

（今次唔會即刻爆：43 條 reject 冇一條落喺會被刪嘅 variant 上。但機制本身係壞嘅。）

→ 修法：KEEP 表改用**身份穩定嘅 key**（`opaque_id` 或 printing fingerprint），或者 `catalog_variant` 改做 soft-delete。

### 9.10 🔴 Migration 036 一失敗就會半生不熟

`db_runtime.py:migrate()`（:408-451）成個 loop 行完先 `commit()` 一次 —— 但 **MySQL DDL 係 implicit commit**，Python 嗰個 commit 冇用。036 有 7 項大改動（改 unique key、動 159k 行的表、3 張新表…），中途爆咗：前面已經永久生效，但 ledger 冇寫，再行一次會撞「duplicate key name」。

house style 本身有解：`031_active_exact_identity_market_repair.mysql.sql` 每句 DDL 都包住
`SET @cardz_ddl = IF((SELECT COUNT(*) FROM information_schema.columns WHERE …)=0, '<ddl>', 'DO 0'); PREPARE … EXECUTE … DEALLOCATE`

→ **036 每一句都要跟呢個 idempotent 寫法**，否則失敗要人手法證恢復。

### 9.11 備份：實際可行，但要行 Docker + root

- 主機**冇** `mysqldump`；container 入面有（`docker exec cardz-market-cap-db-1 mysqldump`，8.4.10）
- schema 大小 **2,566 MB / 72 objects**（55 表 + 17 view）、**75 條 FK**、1 條 CHECK、全 InnoDB
- 2 張表 collation 係 `utf8mb4_0900_ai_ci` 唔同其餘（`market_gemrate_psa10_history`、`market_gemrate_psa10_watchlist`）→ restore **唔好**夾硬 `--default-character-set`
- **`cardz` user 只有 3 個 schema 權限，開唔到新 schema** → restore 驗證要用 root（`CARDZ_DB_ROOT_PASSWORD` 喺同一個 env 檔）
- Docker VM disk 仲有 897 GB、C: 292 GB → 空間冇問題
- `log_bin=ON`、ROW format、30 日保留，但 `cardz` 冇 `REPLICATION CLIENT` → PITR 要 root 先用得
- 舊 schema `cardz_migration_verify_20260728_1540`（316 MB）仲喺度冇清

指令（PowerShell）：
```powershell
docker exec cardz-market-cap-db-1 mysqldump -u cardz -p"$env:CARDZ_DB_PASSWORD" `
  --single-transaction --routines --triggers --events `
  --add-drop-table --set-gtid-purged=OFF --hex-blob `
  cardz_market_cap > "C:\backups\cardz_market_cap_$(Get-Date -Format yyyyMMdd_HHmmss).sql"
```
驗證：檔尾必須有 `-- Dump completed on …`，冇＝斷咗，唔准信。
Restore 驗證用 root 開一個 `cardz_restore_test_<ts>` schema，載入後逐表 diff row count，驗完 `DROP`。

### 9.12 🟡 「176 張錯來源入圍」呢個數而家覆核唔到

紅隊獨立重算：1,782 / 1,157 / 1,146 / 11 / 762 / 758 / 388 同 4 張（110/1530/1552/1553）全部對得返，variant 110 個 `psa10Pop=7759` 來自 `snkrdunk:348126`/`top` 亦都證實。

但 **176 呢個數重現唔到** —— lock 39 建立之後底層 population 表被日更不斷覆蓋，今日嘅狀態還原唔到當時個閘睇到咩。

→ **176 只可以做敘述，唔可以做驗收硬指標。** §6-A 個驗收 (c) 要改成「重跑入圍之後，每張入圍卡嘅 `psa10Pop` 都同 `population_authority.latest_psa10()` 完全一致」，唔好對 176 呢個數。

---

## 10. 現有工具盤點 —— 邊啲已經有、邊啲真係要新寫

`pipelines/` 有 68 個檔、`scripts/` 有 5 個。**唔好由零起。**

| Stage | 已經有嘅工具 + 指令 | 真正缺 |
|---|---|---|
| **1** GemRate 全爬 | 發現：`gemrate_brute_harvest.py --all-sets`（curl_cffi，唔使 browser）<br>逐 ID：`gemrate_source.py public-card-dump --ids-file X --resume`（per-ID resume，`:1703 _has_complete_public_card_capture`）<br>`gemrate_candidate_backfill.py --resume` | ❌ 冇腳本砌到「1,782 variant × (現行 ID + 全部 rejected 舊 ID + 625 discovery ID)」呢個 union worklist。`gemrate_candidate_discovery.py` / `gemrate_candidate_backfill.py` 只覆蓋 One Piece / candidate 子集<br>❌ rate-limit / backoff 未做（`gemrate_brute_harvest.py` 只有 `--limit` `--query` `--set-id`） |
| **2** 入 DB acceptance | `psa_identity_repair.py {audit,apply}`<br>`resolve_active_psa_identity.py {prepare,apply,amend-parallel}`（fingerprint / canonical-slug，immutable dual acceptance）<br>`new_era_db_tidy.py --prepare-source-identities`<br>`collect_control.py commit-snk-binding-delta` | ❌ 冇單一 deterministic acceptance function —— 邏輯散喺 4+ 個腳本，各有各 SQL、filter 唔一致<br>❌ 冇 D2 嘅「跨 ID / 跨 slug 一律開 incident，唔理升跌」 |
| **3** 重算 qualified | `active_universe.py --refresh-lock` → `db_runtime.import_lock()`（`db_runtime.py:855-874` 寫 `selection_signals_json`）<br>`operator_control.py scan-candidates`（`min_pop=1000` 喺 `:2096-2099`） | 🔴 **bug 現場：`operator_control.py:2057-2082 latest_psa10_pop`**（見 §9.3）<br>❌ `pipelines/population_authority.py` 唔存在 |
| **4** SNK + PC 價 | SNK：`snk_market_data.py`（BFS discover / ingest / `--variant-allowlist` / `--recover-local-history`）<br>PC：`pricecharting_page_parse.py`（識 `manualonly` → label `"PSA 10"`，`:28,:37,:266`）、`pc_cdp_sold_refresh_win.py`（CDP 9222）、`pc_psa10_price_derivation.py`、`c11_pc_sold_ingest.py`、`pc_psa10_price_materialize.py` | ❌ SNK 零語言判斷（§9.x-B）<br>❌ PC 只 fetch 咗 437 / 921（§9.7）<br>🔴 `operator_strict_source_identity` 空（§9.6）—— 呢個先係主閘 |
| **5** 圖 + 故事 | EN：`g10_research_ingest.py --write`（讀外部 sibling repo `../grade10-scraper/data/cards/{id}/summary_en.json`）<br>zhTW/zhCN/ja：`editorial_locale_sync.py --write`（讀 `temp/translate-out-*.json`）<br>排隊：`editorial_translate_queue.py`（寫 `temp/translate-in-N.json`）<br>合併：`editorial_story_merge.py`<br>圖：`collect_control.py:2133 run_snk_en_image`、freeze 靠 `operator_control.py freeze-active-sources-from-checkpoints` | ❌ `../grade10-scraper` **喺呢個 Windows checkout 唔存在** → EN 故事而家產唔到（未驗證 WSL 側有冇）<br>❌ `temp/` 成個目錄唔存在，零 translate-out 檔<br>❌ `ko` 完全冇 producer（`editorial_localization.py:61` 寫死四語）<br>❌ `qualified_pool_operator.py cmd_fill_images`（`:788-792`）已經**硬性 disable**：`raise RuntimeError(...permanently disabled...)`，而且 TCGplayer 本身係政策禁源<br>❌ SNK-EN 自動攞圖只覆蓋**已入圍且已 exact-bind** 嘅卡 → 新卡雞蛋問題<br>❌ 完全冇 PC 圖 fallback 嘅自動化（grep `pc_*image*` writer = 0） |
| **6** 刪除 + 啟用 | `operator_control.py db-tidy`（**重建投影，唔係刪卡**） | 🔴 **全 repo 零句 `DELETE FROM`**，100% 新 code（§9.9） |
| **7** Snapshot / FE | `operator_control.py export-operator-snapshot`（靠 library `operator_fe_export.py`，993 行，冇 CLI）<br>`g10_public_snapshot.py --output/--assets-out/--manifest-out/--clean-assets`<br>`scripts/materialize_snapshot_assets.py` | ❌ `036_<ts>` / `fe02` 版本戳未接入任何一個 |

**完整 subcommand 清單**（`--help` 實跑）：
- `operator_control.py`：`status, export-gaps, accept-binding, export-operator-snapshot, export-product-subset, snk-image-priority-status, promote-product-subset, freeze-active-sources-from-checkpoints, scan-candidates, daily, db-tidy`
- `qualified_pool_operator.py`：`status, export-worklist, gap-report, map-tpl, harvest-tpl, ingest-prices, fill-images, maintain`

**現有 validator**：
- `scripts/validate_psa_identity_repair.py` —— 真嘢，但**只覆蓋 migration 034/035** 嘅 acceptance contract，唔係通用 rebuild validator
- `scripts/canonical_seed.py` —— 可攜式 logical seed dump/restore（`CANONICAL_TABLES` + checksum）。**最接近 §7.2 嘅現成嘢，但佢只 dump normalized 行，唔包 raw provider payload**，唔可以當全量備份

**要新寫嘅（確定）**：
1. `pipelines/catalog_rebuild.py`
2. `scripts/validate_catalog_rebuild.py`
3. `pipelines/population_authority.py`
4. 全宇宙 GemRate worklist builder
5. Stage 6 刪除邏輯（跨 50 張表 / 75 條 FK）
6. `market_population_quarantine` 表 + aborted 隔離
7. writer lock 嘅真 call site（6 個 connect 函數）
8. `ko` 故事 producer + 真正嘅翻譯步驟
9. GemRate 爬蟲 rate-limit / backoff
10. `ingest_run_status` 常數模組（§9.2）

---

## 11. 環境事實清單（外部審查者必讀）

### 11.1 MySQL 係 Docker，唔係 native

| 項 | 值 |
|---|---|
| Container | `cardz-market-cap-db-1`，image `mysql:8.4`，`Up (healthy)` |
| Port | `127.0.0.1:3308 → 3306` |
| Compose 檔 | `C:\Users\jackson0202\Documents\Playground\**cardz-market-cap**\compose.backend.yaml`（**舊 folder**） |
| Volume | `cardz-market-cap_cardz_mysql`，**14.03 GB** |
| Windows service | 冇（`Get-Service *mysql*` 空） |
| 版本 | `8.4.10` |
| `@@sql_mode` | `ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION` |
| `log_bin` | ON，ROW format，`gtid_mode=OFF`，保留 30 日；`cardz` 冇 `REPLICATION CLIENT` |
| 主機 mysqldump | **冇**。只有 container 內 `/usr/bin/mysqldump`、`mysql`、`mysqlsh` |

### 11.2 Schema 盤點

- `cardz_market_cap`：**2,566.89 MB，72 objects（55 base table + 17 view）**
- **75 條 FOREIGN KEY**、**1 條 CHECK**（`chk_catalog_variant_locale_code_v1`）、0 trigger / 0 procedure / 0 event / 0 generated column
- 全部 InnoDB
- Schema collation `utf8mb4_unicode_ci`；**2 張表唔同**：`market_gemrate_psa10_history`、`market_gemrate_psa10_watchlist` 用 `utf8mb4_0900_ai_ci` → restore **唔准**夾硬 `--default-character-set`
- 最大嘅表：`market_source_observation` 944MB/1.36M、`market_metric_history_acceptance` 278MB/344k、`market_source_warehouse` 237MB/4.3k、`market_raw_payload_object` 215MB/300k、`market_source_observation_payload_pointer` 184MB/469k、`market_price_observation` 174MB/294k、`market_sale_observation` 160MB/255k、`market_source_effective_observation` 107MB/346k、`market_grader_population_observation` 63MB/159k
- 殘留 schema：`cardz_migration_verify_20260728_1540`（316 MB，44 表）、`cardz_migration_verify_20260728_1550` —— 未清
- `cardz`@`%` 只對 3 個 literal schema 有 ALL PRIVILEGES（上述兩個 + `cardz_market_cap`）→ **開唔到新 schema，restore 驗證要 root**
- 磁碟：Docker VM disk 897 GB free / 1007 GB；C: 292 GB free

### 11.3 排程 —— 10 個全部 Disabled

| Task | 指令 | 狀態 |
|---|---|---|
| `\CARDZ-Market-Cap-Daily` | `cardz-market-cap\deploy\windows\run-cardz-daily.ps1` → `scripts/backend.py` | Disabled |
| `\CARDZ-Nightly-Bake` | `wsl.exe -e bash -lc '~/cardz-aws/scripts/nightly_daily.sh'` → `pipelines/daily.py --mode production --publish` | Disabled |
| `\CARDZ-GemRate-PSA10-Watchlist` | `deploy\windows\run-cardz-gemrate-psa10-watchlist.ps1` | Disabled |
| `\CARDZ-Market-Cap-Watchdog` | `deploy\windows\run-cardz-watchdog.ps1` | Disabled |
| `\CARDZ-TAG-Daily-Capture` | `pipelines\tag_daily_capture.py` | Disabled |
| `\CARDZ-Freeze-Sweep-Guard` | `temp\freeze-sweep-guard.ps1` | Disabled |
| `\CARDZ_PC_Shard1_Watchdog` | `temp\pc_shard1_watchdog.ps1` | Disabled |
| `\cardz-beta-daily-refresh` / `\cardz-beta-hourly-refresh` | `cardz-platform` repo（另一個 project） | Disabled |
| `\JACKZCardzWhatsAppTunnel` | cloudflared（唔關 DB 事） | Disabled |

全部 `Next Run Time: N/A`。WSL `crontab -l`、`/etc/cron.d`、systemd timers 冇 cardz。Startup folder / `HKCU:\...\Run` 冇 cardz。
Repo 內冇 node-cron / APScheduler / PM2 config；`package.json` 冇 DB hook。

### 11.4 而家行緊嘅 process

- **`node` PID 16228 / 37856** —— `next start --hostname 127.0.0.1 --port 3801`，module root = **舊 folder** `cardz-market-cap\node_modules`，2026-08-05 起
- 冇任何 python pipeline process 行緊（Windows 或 WSL）
- `SHOW PROCESSLIST` = 1 條（審計自己），`Threads_connected=1`

### 11.5 停機 baseline（2026-08-08 記錄）

```
market_ingest_run                    max(id)=7865     max(started_at)=2026-08-07 09:47:43.918
market_source_observation            max(id)=3975227  max(observed_at)=2026-08-07 09:47:30.726
market_price_observation             max(id)=4441343  max(effective_at)=2026-08-07 23:59:59
market_grader_population_observation  max(id)=321241   max(effective_at)=2026-08-07 00:00:00
catalog_variant                      max(id)=1869
```
隔一個完整日更週期再查，全部無變 = 真係停咗。

### 11.6 DB 連線 call site

`connection_from_args`（`db_runtime.py:379`）—— **14 個檔跟規矩**：
`g10_identity_expand`、`g10_ebay_ingest`、`g10_variant_seed`、`editorial_locale_sync`、`g10_research_ingest`、`converge_printing_identity`、`market_alerts`、`g10_sales_cache_ingest`、`g10_kline_price_bridge`、`g10_analytics_ingest`、`g10_asset_ingest`、`fx_db_load`、`editorial_translate_queue`、`db_runtime` 自己

**5 個自己開連線、繞過 chokepoint**（見 §9.4）：
`snk_market_data.py:506`、`qualified_pool_operator.py:94`、`c11_pc_sold_ingest.py:76`、`pc_ungraded_reference_ingest.py:58`、`ingest_snk_trades_sales.py:58`

`apps/web` **確認只讀**：`live-db-snapshot.ts` 全部 SELECT，`apps/` 底下零 INSERT/UPDATE/DELETE。

### 11.7 Stage 4/5 現況數字（1,782 variant 為分母）

| 項 | 數 |
|---|---|
| 有 pricecharting 價觀測 | 932 variant（824 ready / 108 quarantined） |
| PC `match_status='exact'` identity | 349 |
| 有 snkrdunk 價觀測 | 727 variant / 120,180 行 |
| 有 snk_psa10 價觀測 | 665 variant / 158,176 行 |
| SNK `match_status='exact'` identity | 154 |
| `operator_strict_source_identity` 通過 | **gemrate 762、pricecharting 0、snkrdunk 0** |
| 有任何 image asset | 1,533 |
| 有 canonical image acceptance | 762 |
| 有 binding freeze | 762 |
| `operator_canonical_image_projection` | **12** |
| PC HTML 本地 cache | 437 / 921 已 map 候選（437 個全部 parse 到正值 PSA10） |

### 11.8 故事現況（`catalog_variant_locale`，≥80 字）

| locale | distinct | rows | 平均字數 | 真定假 |
|---|---:|---:|---:|---|
| en | 1,007 | 1,030 | 1,549 | 真（G10 research 原文） |
| ja | 808 | 814 | 226 | 真 |
| zhTW | 811 | 814 | 197 | 真 |
| zhCN | — | 814 | — | 真 |
| **ko** | 761 | 761 | 203 | **假 —— 樣板** |

`ko` 實物樣本：
```
2023 Pokemon Svp EN-SV Black Star Promo Pikachu … 는 CARDZ 에서 PSA 10 가격·매수·시가총액을 추적합니다.
2021 Pokemon Japanese S Promo Full Art/Pikachu Pokemon Stamp Box 227 는 CARDZ 에서 …
```
對比 zhTW 同一張卡：真人寫嘅多句收藏史敘述。
`ko` 行嘅 `provenance_source_code='canonical_locale_merge_v1'`，但**產生佢嗰段 code 喺 `pipelines/` 底下搵唔到**（未解，見 §13）。

---

## 12. 備份 / 還原完整 runbook

> 全部 command 喺 Windows（PowerShell 或 Git Bash）行。`$CARDZ_DB_PASSWORD` / `$CARDZ_DB_ROOT_PASSWORD` 喺 `data/runtime/config/backend.env`，**唔准貼落任何 log 或 commit**。

### (a) 全量 dump

```powershell
docker exec cardz-market-cap-db-1 mysqldump `
  -u cardz -p"$env:CARDZ_DB_PASSWORD" `
  --single-transaction --routines --triggers --events `
  --add-drop-table --set-gtid-purged=OFF --hex-blob `
  cardz_market_cap > "C:\backups\cardz_market_cap_$(Get-Date -Format yyyyMMdd_HHmmss).sql"
```
`--single-transaction` 安全，因為 55 張表全部 InnoDB。2.5 GB data 出 logical dump 估 3–8 GB。

### (b) 驗 dump 完整性

```bash
tail -5 cardz_market_cap_*.sql      # 必須見到 "-- Dump completed on ..."
grep -c "^INSERT INTO" cardz_market_cap_*.sql
wc -l cardz_market_cap_*.sql
```
**冇 `-- Dump completed on` 就係斷咗（斷線／爆碟），重做，唔准信。**

### (c) restore 去 throwaway schema + 逐表對數

```bash
RESTORE_DB=cardz_restore_test_$(date +%Y%m%d_%H%M%S)

# 1. root 開 schema + 授權（cardz user 開唔到新 schema）
docker exec cardz-market-cap-db-1 mysql -u root -p"$CARDZ_DB_ROOT_PASSWORD" \
  -e "CREATE DATABASE \`$RESTORE_DB\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
      GRANT ALL PRIVILEGES ON \`$RESTORE_DB\`.* TO 'cardz'@'%'; FLUSH PRIVILEGES;"

# 2. 載入
docker exec -i cardz-market-cap-db-1 mysql -u cardz -p"$CARDZ_DB_PASSWORD" "$RESTORE_DB" \
  < cardz_market_cap_YYYYMMDD_HHMMSS.sql

# 3. 逐表 row count diff
docker exec cardz-market-cap-db-1 mysql -u cardz -p"$CARDZ_DB_PASSWORD" -N \
  -e "SELECT table_name,table_rows FROM information_schema.tables WHERE table_schema='cardz_market_cap' ORDER BY table_name" > rows_source.txt
docker exec cardz-market-cap-db-1 mysql -u cardz -p"$CARDZ_DB_PASSWORD" -N \
  -e "SELECT table_name,table_rows FROM information_schema.tables WHERE table_schema='$RESTORE_DB' ORDER BY table_name" > rows_restored.txt
diff rows_source.txt rows_restored.txt && echo "ROW COUNTS MATCH"

# 4. 驗完清走
docker exec cardz-market-cap-db-1 mysql -u root -p"$CARDZ_DB_ROOT_PASSWORD" -e "DROP DATABASE \`$RESTORE_DB\`;"
```
⚠️ `information_schema.table_rows` 對 InnoDB 係**估算**。最大嗰兩張（`market_source_observation`、`market_metric_history_acceptance`）要另外行真 `COUNT(*)` 兩邊對。
⚠️ 開始之前先確認殘留嘅 `cardz_migration_verify_2026*` 係可棄嘅，唔好撈亂。

### (d) 出事點 rollback

**Tier 1 — logical restore**（MySQL 8.4 冇 `RENAME DATABASE`）：
1. 先照 (a) dump 一次「壞咗嘅現狀」做保險
2. `DROP DATABASE cardz_market_cap` → 重建空 schema
3. 載入 last-known-good dump
4. 再行 (c) 步驟 3 對數

**Tier 2 — volume 級（核彈）**：
```powershell
docker compose -f "C:\...\cardz-market-cap\compose.backend.yaml" stop db
docker run --rm -v cardz-market-cap_cardz_mysql:/data -v C:\backups:/backup alpine `
  tar czf /backup/cardz_mysql_volume_$(Get-Date -Format yyyyMMdd_HHmmss).tar.gz -C /data .
docker compose -f "C:\...\cardz-market-cap\compose.backend.yaml" start db
```
> ⚠️ 呢度個 compose 路徑就係 §9.1 講嘅問題 —— 遷移之後要同步改。

### (e) 已知限制

- 全部手動，冇自動排程備份
- restore 驗證同 Tier-1 rollback **每次都要 root 密碼**
- PITR 名義上得（binlog ON / 30 日），但 `cardz` 冇 `REPLICATION CLIENT`，實際要 root 先讀到 binlog

---

## 13. 未解 / 留俾紅隊挑戰嘅位

以下係我**查唔實**或者**有矛盾**嘅嘢，明文列出，唔當已解決：

1. **`run 5628` 之謎**：`snk_kline_ingest_20260807T070125Z` 喺 2026-08-07 成功寫咗 48 條 snkrdunk 價，理論上要行 `operator_strict_source_identity`（而佢對 snkrdunk 係 0 行）。→ 究竟係個 view 之後先壞，定係有第二條路繞過咗？**未查實。**
2. **`ko` 樣板由邊度嚟**：`provenance_source_code='canonical_locale_merge_v1'`，但 `pipelines/` 底下 grep 唔到嗰段樣板字串。→ 有冇 repo 外嘅 writer？（呼應 memory `cardz-unlogged-db-writer` 記低嘅 2026-08-03 三次零 receipt 寫入）
3. **`../grade10-scraper` 喺邊**：EN 故事來源，Windows checkout 冇。→ WSL 側有冇？冇嘅話 388 張新卡嘅 EN 故事點產？
4. **176 張錯來源入圍還原唔到**（§9.12）—— 只可以敘述，唔可以做驗收指標。
5. **WSL2 networking**：`~/cardz-aws` 用 `127.0.0.1:3308` 點解掂到 Windows 側 Docker —— 由 config 推斷，**未實測**（審計期間冇觀察到 WSL 側連線）。
6. **625 張零 POP 卡**重爬完會唔會令候選由 1,146 再向上升 → 388 呢個數要留浮動。
7. **exact-bind 覆蓋 vs 價觀測覆蓋**：1,113 張有 PC/SNK 價觀測，但 D4 要求 exact bind 先算數，而 exact 只有 PC 349 / SNK 154。→ Stage 4 開工前要另外量一次真實可用數。
8. **3 張 One Piece 冇 exact SNK EN 圖**（128 / 958 / 1450）vs SNK-first 規則 —— 要 owner 拍板。
9. **Stage 6 單一 transaction 跨 50 表 / 75 FK** —— 未做過壓力測試，未知會唔會谷爆 undo/lock。
10. **`market_raw_payload_object`（300k 行 / 215 MB）**喺 rebuild 之後嘅去留 —— plan 講咗「landing raw 全部保留」，但 DB 內呢張表點處理未寫明。
