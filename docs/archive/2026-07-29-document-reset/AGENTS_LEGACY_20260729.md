# Archived CARDZ Market Cap Agent Entry (2026-07-29)

## Read PROJECT_STATE.md first, update it last

`PROJECT_STATE.md` at the repo root is the single source of truth for current
state: what is in flight, what is already done, hard deadlines, standing
decisions, and known traps. Read it before doing anything else. Update it before
you finish. It is model-agnostic on purpose — a task list that lives inside one
agent's session is invisible to every other agent, which is how work gets
repeated.

**Required:** [`PROJECT_STATE.md`](PROJECT_STATE.md)（營運 + 務實精華）。  
**Script / reverse map（點做）:** [`docs/PROJECT_MAP.md`](docs/PROJECT_MAP.md)。  
**填庫捷徑／踩坑:** [`docs/FILL_LOOP_LESSONS.md`](docs/FILL_LOOP_LESSONS.md)。

Optional: identity files under `data/runtime/private-source-map/`, long manuals only when debugging one provider.

Do not trust its prose alone. Section 0 lists three verification commands; run
them and let the real output override anything the document claims.

`PROJECT_STATE.md` records **operational state**. `config/data-routing.json`
remains the only handwritten source for **architecture ownership**. Never
restate node ownership, routing, or acceptance contracts in `PROJECT_STATE.md`.

---

## 而家階段（hard · 2026-07-29 用戶）

- **本機 session 揾料為主**（全量池而家 ~940，遲啲會加）。
- 料喺本機撈齊 → 入庫／mark 成功路徑 → **之後**先靠腳本每日自動更新走期。
- **而家唔做**日更調度、雲上自動 harvest 擴張——用戶話「齊咗先，後邊慢慢講」。
- 池脹正常；前端 cut 唔脹（見 `PROJECT_STATE` 真理 #1）。
- **價史：** 近 **30 日** 有 bar 已夠 score／升跌；更長史有就收（一掣），唔為全歷史卡 progress。

### 循環：QC 完先再揾（hard）

**只能咁做：** harvest 候補 → **QC／verify** → mark 綠路徑／記 reject pattern → **用 pattern 再開下一輪揾料**。  
QC 嘅價值唔止「擋錯」，仲係 **辨邊個 source 穩、邊個配邊類卡、邊個一定有問題**。

| 源 | 粗知 | 仍要 |
|---|---|---|
| **SNK** | feedback／trades **可以用**（成交主戰場） | **一定 QC 先**——可能 20 張零過，又可能 1 張即過 |
| TPL | EN 價主幹 | map 閘 + 有 PSA10 先寫 |
| OP 圖 | Limitless 綠 | SAMPLE 紅（cast／OCR） |
| PTCG 圖 | 通常無 SAMPLE 水印 | **仍要 QC**（錯卡／缺圖） |

Pattern 筆記：[`docs/SOURCE_QC_PATTERNS.md`](docs/SOURCE_QC_PATTERNS.md)

---

## QC 閘口（hard · 2026-07-29 用戶更正）

**一句定生死：搵料唔使 QC；入庫／出 public 之前一定 QC。**  
**入庫前驗證 = 正式工序**（唔係可選收尾）。搵數據易；**過閘先要有實要求——唔為難，但一定要過。**

| 階段 | 要唔要 QC | 例子 |
|---|---|---|
| **去搵料**（research / harvest / scrape / download raw） | **唔使** | SNK pull、TPL harvest、Limitless 下載、瀏覽、jsonl 暫存 |
| **入庫前**（寫 MySQL / identity / registry / public asset / snapshot） | **一定要** | `store_*`、ingest、verify bind、rebuild FE |

**完整清單：** [`docs/INGEST_VERIFY_GATE.md`](docs/INGEST_VERIFY_GATE.md) · 方法論：[`docs/RECALL_VERIFY_OPS.md`](docs/RECALL_VERIFY_OPS.md)

### 入庫前：邊類 · 邊個腳本 · 最低要求

| 寫咩 | 必過 | 最低要求（實而不難） |
|---|---|---|
| **identity** | `semi_auto_identity.verify_pair` / clean / `full_volume_recall_verify` | collector+species（OP 名）；拒 first-hit；錯綁刪 |
| **價** | TPL map 閘；ingest 只收有 PSA10；SNK 要已 verify id | 唔 invent；空 PSA10 唔寫 |
| **成交** | verified external id → `ingest_snk_trades_sales` / G10 sales | 指紋去重；PSA10 濾；有就入晒 |
| **圖 public** | `sample_image_qc` @ store；`ensure_image_abc` | SAMPLE 拒；`public_allowed` |
| **FE 圖** | `verify_images.py` + SAMPLE re-scan | hits=0；檔+hash 在 |

**AI 睇：** 只打腳本拒／needsReview／meta_unreviewed **殘渣**（高市值優先）；通過仍寫同一表。  
**禁止** AI 口頭「應該係」就 `INSERT`。

### 報「齊」必分三欄

Harvest 有料｜DB 有寫入｜**驗證綠** —— 缺第三欄 = 未齊。

### 派 subagent

- **Harvest：** 免 QC；只落 harvest／temp。  
- **Ingest：** prompt 必引用 `INGEST_VERIFY_GATE` + 對應腳本；唔 bypass。  
- 同一 agent：搵可以快；**每次寫 DB／public 前** 先過閘。

### 圖 QC（全部 TCG · 入庫前）

**一律要 QC**（PTCG、OP、其他）——身份啱、唔錯卡、可出街。  
**SAMPLE 水印呢一種缺陷**：實務上**主要／幾乎只**喺 **海賊王 OP**（TCGplayer 等）出現；**唔等於 PTCG 免 QC**（2026-07-29 用戶糾正：唔好武斷）。

| 檢查 | OP | PTCG |
|---|---|---|
| 身份／collector／錯卡 | ✅ | ✅ |
| 缺圖／壞檔／佔位 | ✅ | ✅ |
| SAMPLE 水印 OCR／cast | ✅ 重點 | 低優先（通常冇） |
| 換 clean 源 | Limitless EN / G10 SNK | TPL binding / 其他真源 |

**OP 圖源優先：** Limitless `_EN` → G10 SNK → **禁止** TCGplayer SAMPLE 出街。

---

## 防再犯記錄（真實犯過 · 寫低）

| # | 錯 | Why 蠢 | How to apply |
|---|---|---|---|
| 1 | 入庫／出街唔 QC（SAMPLE 當可選） | 髒圖髒 identity 上板 | **入庫前**硬閘；store 入口拒 SAMPLE |
| 2 | 以為腳本會自動 QC SAMPLE | 主線曾零 SAMPLE 字 | `sample_image_qc`；**寫 public 後** re-scan |
| 3 | 黃色像素 = SAMPLE | 梵高皮卡丘等假陽 | OCR SAMPLE 為準；pixel 只輔助 |
| 4 | 大批 30d=0% 當市場平 | TPL 假 today stamp | 查 today 假觀測；FILL_LOOP_LESSONS |
| 5 | 複雜「假 0 救援」錨點 | 用戶只要頭尾 | head-tail only |
| 6 | 以為 clean 圖=日版 | 主力 Limitless **_EN** | 美日 number≠SKU；comic SP 或共用 base |
| 7 | snk harvest 用舊 jsonl | universe lock crash | 新 out 檔名；partial promote 再 ingest |
| 8 | 只改 snapshot 唔 mark DB | 自動化冇路 | identity + registry |
| 9 | 文檔唔更新 | 數字腐爛 | 每輪改 PROJECT_STATE §D |
| 10 | 單線長任務唔派 subagent | 慢 | 2+ track 並行；**入庫** track 先寫 QC |
| 11 | 誤解「agent 都要 QC」= 搵料都要掃 | 用戶更正：搵料免 QC | 只閘 **入庫前**；harvest 全速 |
| 12 | 當「有數入 DB」= 驗證完 | 搵料易、入庫驗先係工序 | 跟 `INGEST_VERIFY_GATE`；報三欄 |
| 13 | 用戶 cast 標嘅卡當普通缺口 | 用戶話**全部 SAMPLE**，內容一定有問題 | cast-fails = **SAMPLE 紅燈**；強制換 Limitless/G10；store 前 OCR；唔好只報 any_image |
| 14 | 以為 PTCG 唔使 QC／或 SAMPLE 全池一刀切 | **全部 TCG 都要 QC**；SAMPLE **水印呢一種**主要出喺 OP | 入庫前一律過閘；SAMPLE 檢測／Limitless 換 clean 優先 OP；PTCG 仍要身份／錯卡／缺圖 QC |

---

## Task level comes before tooling

Classify the request before choosing infrastructure. Use the lightest existing
path that can produce the requested artifact. Do not promote a one-off research,
scraping, analysis, visualization, or mockup task into production
infrastructure unless the user explicitly asks for that outcome.

- **L0 — Research and disposable output:** one-off public-data scraping,
  analysis, charts, visualizations, and mockups. Use direct HTTP, an existing
  script, or the smallest disposable script that completes the request. Do not
  start Docker, MySQL, CodeGraph, or the full backend workflow. Do not modify
  canonical data.
- **L1 — Frontend-only change:** work from the existing public snapshot and
  run frontend-scoped validation. Do not start backend infrastructure unless
  the requested result cannot otherwise be verified.
- **L2 — Focused collector or pipeline change:** inspect only the relevant
  owner files and routing node, then run targeted tests. Use the full
  architecture workflow below only when the change alters architecture,
  ownership, dependencies, or the public data contract.
- **L3 — Production data architecture or runtime:** schema changes,
  migrations, canonical database work, daily runtime changes, and publication
  changes use the full backend workflow below, including the applicable
  database and release gates.

Docker and MySQL are opt-in tools. Start them only when the requested result
requires the canonical MySQL runtime; the presence of a Compose file is not a
reason to start them.

The only handwritten backend architecture source is
`config/data-routing.json`.

For L3 work, and for L2 work that changes architecture, ownership,
dependencies, or the public data contract:

1. Run `python scripts/backend.py explain <metric|field|node|task>`.
2. Run `python scripts/backend.py work-items --status in_progress`.
3. Run `npm run graph:sync`, then use `codegraph explore "<implementation>"` to
   inspect callers, imports, and impact.
4. Change only the owner files attached to the matching work item.
5. Update that node or work item in `config/data-routing.json` when the
   architecture, ownership, dependency, or acceptance contract changes.
6. Run `python scripts/backend.py generate-docs` and
   `python scripts/backend.py generate-docs --check`.

CodeGraph and its local SQLite database are read-only code evidence. They do
not define source authority, data transport, storage, ranking, presentation,
or task status.

Never hand-edit `docs/generated`, and never create a second routing or
architecture contract in a README, runbook, frontend, or database.

## Subagents（填庫／多源）

- 2+ 獨立 track（價／成交／圖 harvest／identity）→ **並行 spawn**，主 agent 做 PM + 驗收。
- **Harvest agent：** 免 QC，只撈料落 harvest／temp。
- **Ingest／上板 agent：** prompt 必含 **入庫前 QC**（SAMPLE 拒、verify、唔 invent）。
- 收工要：數字 before/after + mark 路徑 + 更新 PROJECT_STATE。
