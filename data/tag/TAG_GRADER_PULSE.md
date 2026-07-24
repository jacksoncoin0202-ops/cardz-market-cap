# 每日送評流向（Grading Pulse）五大廠商操作手冊

> 目的：cardz-market-cap 首頁「每日送評流向」由四大廠商（PSA / BGS / CGC / SGC）擴充為五大廠商，加入 TAG 每日送評量同 TAG 市佔。
> 上游認證 / API 細節：`pipelines/TAG_POP_DATA.md`（逆向配方）、`reverse-skill/work/tag-grading/TAG_GRADING_REVERSE.md`（完整拆解報告）。
> 數據落點：本目錄 `data/tag/`。

## 1. 保留嘅 UI 欄位（四大廠商而家嘅展示契約）

首頁 Grading Pulse 區塊（`apps/web/src/components/grading-pulse.tsx`），每一行一間廠商，欄位如下：

| 欄位 | 來源 | 講法 |
|---|---|---|
| 廠商名 | `i18n gradingPulse.names.{PSA,BGS,CGC,SGC}` | 四語（en / zh-TW / zh-CN / ja）各自有譯名 |
| 頂級卡增量（+N） | top100 每卡 `graderPopulations[g].topGradePopulation` 對上一日 anchor 嘅差 | 例：`+12` |
| 變化 % | `topGradePopulationChangePct`（1d / 7d / 30d window） | 例：`+0.31%`；caption 係「較上一{period}的變化」 |
| 市佔 % 棒 | 該廠商 top100 頂級 pop 總和 ÷ 四廠商總和 | 截圖：PSA 88.9 / CGC 5.7 / BGS 5.2 / SGC 0.3 |

區塊標題文案（`i18n gradingPulse`）：eyebrow「評級動態」、title「每日送評流向」、subtitle「覆蓋已追蹤前 100 名」。
**五廠商之後呢啲欄位一個唔少，TAG 行用同一套契約渲染。**

## 2. TAG 做第五間廠商：數值定義

| 項目 | 定義 | 原因 |
|---|---|---|
| `grader` key | `"TAG"` | 加落現有 grader 維度，唔開新欄（見第 4 節） |
| 頂級 grade 標籤 | `"10P"`（Pristine） | TAG 官方最高 grade；snapshot labels 由 `{"PSA":"10","BGS":"Black Label","CGC":"10","SGC":"10"}` 加 `"TAG":"10P"` |
| `topGradePopulation` | `grades["10"] + grades["10P"]` | TAG 10P 太稀少（全 Pokémon 得幾千張），單計 10P 會令每日增量長期係 0；10+10P 先同 PSA 10 嘅「gem 級」概念可比 |
| `total` | 該卡 `grades` 全部 key 嘅和（唔計 `"VA"` Authentic） | VA 係「真品但唔評分」，唔係評級供應 |
| 每卡數據源 | `data/tag/tag_pop_observations.jsonl`（由每日 capture 經 active-universe exact identity join 產生） | TAG API 全量 dump，join 契機係 collector number + language + normalized card name 唯一命中，fail-closed |

## 3. TAG 市佔點計

TAG API 冇歷史，但**總量係一個 request 攞到**：

```
TAG 總送評量 = Σ /pops/year（categoryName=Pokémon）每年 gradedTotal
```

市佔有兩個層次，兩個都出：

1. **Top100 市佔棒**（現有 UI 邏輯，免費送）：TAG 行加落去之後，分母自動由四廠商變五廠商總和，`grading-pulse.tsx` 唔使改公式，只係 graders 多一個 key。
2. **全市場市佔**（新指標）：

```
TAG 全市場市佔 = TAG 總送評量 ÷ (GemRate 四廠商最新 total 之和 + TAG 總送評量)
```

四廠商每日 total 由 `pipelines/gemrate_source.py` 嘅 `history_full.json` → `by_grader.<g>.history[-1].total` 提供；TAG 總量每日 capture 時順手記低（1 個 request），唔使靠全量 dump 先計到。

## 4. 設計決定：擴充 grader 維度，唔係加獨立數據欄

用戶問「加多個數據欄，又或者點樣指示」——答案係**擴充現有 grader 維度**，原因：

- 現有機器（landing replay → snapshot → UI）係以 `Record<Grader, GraderPopulationView>` 做骨架，加一個 key 就全鏈路食到；另開 TAG 專用欄等於整套 snapshot/UI/ freshness 邏輯寫多一次。
- `grader_population_*` canonical-batch 契約本身就係 per-grader 設計，`payload.grader` 填 `"TAG"` 即合法（只要 `GRADERS` tuple 有佢）。
- 前端 `grading-pulse.tsx` 用 `grader.toLowerCase()` 出 CSS class，加 `grading-tag` 一隻色就完事。

## 5. 每日同步增量（硬性要求）

TAG API **只有即時快照，冇歷史冇「最近新增」端點**，所以每日送評量 = 自家每日 capture 做差分。管道如下：

```
每日 (Windows Task Scheduler)
  └─ pipelines/tag_daily_capture.py          ← 每日 capture（fail-closed exact matching）
       1. 全量 dump pops_pokemon.jsonl（約 10 分鐘，state file 斷點續跑）
       2. 對 active-universe（data/runtime/private-source-map/active-universe.json）
          做 exact identity join：collector number + language + normalized card name
          → 唯一命中先接受；ambiguous 落 review file，唔會估
       3. 輸出 data/tag/tag_pop_observations.jsonl
          （topGradePopulation = grades["10"]+grades["10P"]，total 唔計 VA）
  └─ pipelines/market_source_sync.py --tag-run data/tag/tag_pop_observations.jsonl
       將 TAG observations 以 grader_population_tag 同 GemRate/SNK 一齊
       emit 入 private landing root canonical-batch.json（sourcePriority 150）
  └─ g10_ingest.py（現有，唔使改邏輯）
       load_landing_replay 自動食 batch；同一 (source, ext_id, grader, date) first-wins → 同日重跑唔會 double count（idempotent）
  └─ g10_public_snapshot.py（現有機器）
       add_population_windows 由每日 anchor 出 1d/7d/30d 變化
```

關鍵性質：

- **冇 backfill**：第一日 capture 之後先有第一個 anchor，1d 變化要第二日先出，7d/30d 照現有 `accumulating` 狀態行。首日 baseline 係而家 `data/tag/pops_pokemon.jsonl`（2026-07-22 dump，784,106 張）。
- **斷點續跑**：dump 用 `<out>.state` 記低完成咗嘅 `year|brand|set`，中途斷咗重跑會接返。
- **排程**：Windows Task Scheduler，建議每日 06:45（Grade10 爬蟲 06:30 之後，snapshot build 之前），Windows Python `C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe`，工作目錄 repo root。**唔好用 WSL cron**（backslash 路徑會炸）。
- **速率**：管道預設 0.15s delay（實測上限 6.6 req/s），全量約 10 分鐘，每日全量重抓比分片差分簡單而且先保證唔會漏新卡／改 grade。

## 6. 實施 touchpoint 清單（手冊之後先做）

| 檔案 | 改動 |
|---|---|
| `pipelines/tag_daily_capture.py` | 新增：每日 capture + 出 canonical-batch（第 5 節） |
| `apps/web/src/lib/types.ts` | `graders` 加 `"TAG"` |
| `packages/market-data/src/schema.ts` | `GRADERS` 加 `"TAG"` |
| `pipelines/g10_ingest.py` | `GRADERS` tuple 加 `"TAG"` |
| `pipelines/g10_public_snapshot.py` | labels 加 `"TAG": "10P"` |
| `apps/web/src/lib/i18n.ts` | `gradingPulse.names` 四語各加 `TAG` 譯名（TAG 係品牌名，四語都係 "TAG"） |
| `apps/web/src/app/globals.css`（或 grading-pulse 對應 CSS） | 加 `.grading-tag` 品牌色（TAG 品牌藍） |
| `pipelines/population_daily.py` | `SOURCE_PRIORITY` 加 `tag: 2`，legacy → 3 |
| Task Scheduler | `CARDZ-TAG-Daily-Capture` 已註冊：每日 06:45，Windows Python，cwd = repo root，`pipelines\tag_daily_capture.py --catalog-out data\tag\daily\pops_latest.jsonl --out data\tag\tag_pop_observations.jsonl`（immutable：同日重跑印 `replayed` 唔會重寫） |

## 7. 驗收

1. `tag_daily_capture.py` 連跑兩日，第二日 landing batch 嘅 `observedDate` 正確、first-wins 唔重複。
2. snapshot 入面 600 卡 `graderPopulations.TAG.topGradePopulation.status == "ready"`，freshness ≤48h。
3. 首頁 Grading Pulse 出五行，市佔棒分母係五廠商總和，TAG 行有 +N 同變化 %（第二日起）。
4. `data/tag/totals.jsonl` 每日一行，全市場市佔可由佢 + GemRate history 重現。
