# AGENTS.md — cardz-market-cap-fe-db-20260805

**呢棵係資料／日更真身**（collect → `daily-accept` → `daily_public_release`）。
FE 出街車 = `../cardz-market-cap-037-fe04-live`（`[deploy]`）。**唔喺呢度** push `main` 當網站 deploy。
實驗樹 `../cardz-market-cap` 只准讀 3308，**唔准** pass／bake／`[deploy]`。
Live：`https://app.cardzmarketcap.com` · `037`／`FE04` · 1449 張 · BOX `/box` sidecar。

任何 agent（Claude / Codex / 其他）喺呢個 repo 開工前必讀。呢度只放「跟錯會出事」嘅硬規矩；操作細節全部喺 **[docs/COLLECTION_RUNBOOK.md](docs/COLLECTION_RUNBOOK.md)** —— 做任何採集（全量/增量）之前先讀佢，跟佢嘅 canonical 命令，唔好自己憑記憶砌 flag。現狀契約：[docs/HANDOFF_037_FE04.md](docs/HANDOFF_037_FE04.md)。

## 硬規矩（違反 = 事故）

1. **唔准 `git add -A` / `git add .`** — 逐個檔 add。`data/private/` 同 `data/runtime/` 已 gitignore（2026-08-08 起），但呢條規矩照守：working tree 隨時有唔應該入 repo 嘅嘢。
2. **`backend.env` 一個 byte 都唔准改**（讀可以）。任何 rebuild DDL/DML 用 `data/runtime/config/rebuild.env`（`--credentials-env`）。writer freeze 期間 `cardz@%` 只有 SELECT。
3. **PriceCharting 只用 CDP port 9333**（Windows **headed** Chrome，`scripts/ensure_chrome_cdp.ps1 -Port 9333`。Headless 禁止：CF 擋、本機 CF tool 要視窗）。9222 係 Codex 嘅 browser profile：唔准掂，唔准 fallback。WSL/Linux Chrome 仍然禁止。**9333 PC 腳本永遠雙 tab**（`pc_cdp_sold_refresh_win.py` `PC_TABS=2`）。加卡（identity bind）同 cap 頁係**同一條腳本、一次執行**，唔准另開 `pc_identity_discover.py` 單 tab 去 9333。SNK 同 PC 係並行數據，有 SNK 唔等於唔使 PC。
4. **同時起兩個 orchestrator 而家係 code 擋，唔再靠自律。** `operator_control.py` `main()` 除 `READ_ONLY_COMMANDS` 之外每條 subcommand 都攞 `operator_e2e_lease`（MySQL `GET_LOCK`），第二個會即刻 `refused: another CARDZ 026 operator run owns …`。所以唔好再「直接 call stage function 繞過 orchestrator」——嗰個係舊時冇閘先要嘅做法，繞過即係繞過個閘。
5. **舊 checkout `C:\Users\jackson0202\Documents\Playground\cardz-market-cap` 只准讀** — 佢擁有 MySQL 3308 嘅 docker compose 同 14GB volume，刪/搬 = 斷 DB。
6. **秘密**：唔准將任何 env 密碼/token 印落 log 或 commit。
7. **採集唔准 filter** — fetch-all 落 landing，入 DB 先揀（政策，見 runbook）。
8. **`rebuild-036-unfreeze --confirm` 會 DROP `cardz_rebuild`。** 之後想再跑任何 stage，一定要
   先 `rebuild-036-freeze` 重建佢；同時兩條現役 Task Scheduler 要 Disable，跑完 unfreeze 再
   Enable 返。冇 Enable 返 = 夜鏈 03:30 靜靜死。（步驟見
   [runbook §5「Freeze / unfreeze 生命週期」](docs/COLLECTION_RUNBOOK.md)）
9. **加咗檢查要即場證明佢會 fire。** 新 assert / hook / test 寫完之後，臨時將個 bug 種返落去，
   睇住佢紅，再還原。冇做過呢步唔准講「已修」——「有檢查但零 call site」當冇檢查。
10. **唔准放鬆任何 acceptance gate 嚟令個數靚。** 數唔夠就修根因或者照報缺口。
11. **開新 lane 寫 binding 之前，先問「有冇人手裁決管住呢批卡」。** 034 audit sheet 紅名單
    13 張係人手拒絕嘅；紅名單要**推導**（`scripts/stamp_red_sheet_quarantine.py:
    red_variant_ids()`），唔准抄。睇唔到裁決嘅 lane 唔准提案（fail-closed）。做爆咗就行
    `python -X utf8 scripts/stamp_red_sheet_quarantine.py --write` 收返（idempotent）。

12. **一條 gate 100% 拒絕、而且理由永遠同一個 field → 查嗰個 field 嘅來源，唔好查 gate。**
    2026-08-10：123 張 OPTCG 卡全部死喺 `set_code:` 衝突，gate 冇錯，錯喺 GemRate 講「喺邊個
    產品賣」而卡面印「邊套出世」（runbook 形狀 21）。修 input，唔准放鬆 gate。
13. **改 identity 規矩之前，`grep` 個「概念」睇有幾多處獨立實現緊。** 同一條問題喺呢個 repo
    出現過四份 copy（runbook 形狀 22），修一份 = 同一張卡喺 A lane 過到、B lane 過唔到。
    收埋做一個 function 再三處 call，唔好逐處補。
14. **修完一個 gate，要分開數返每一邊，唔好淨係睇總數郁咗。** 2026-08-10：同一個 set_code
    缺陷有兩個方向，第一版修法喺 48 張「catalog 空白」度啱，喺 21 張「catalog 載住另一邊」
    度一格都冇郁；總數升咗 9 張，睇落似做完（runbook 形狀 21 補完）。剩低嗰批要逐個
    再跑一次修完嘅邏輯，見到佢由 refuse 變 pass 先算數。
15. **日更 `incr` 唔拉 residual stock。** 新 activate／未 freeze-complete 先 `collect_control.py stock`。FE 出街車係 `../cardz-market-cap-037-fe04-live`，唔係呢度 push `main`。
16. **宣傳鏈唔係自動更新鏈。** `live.confirmed` 之後等 **30 分鐘** 先跑 `promo_chain.py brief`（圖／文／閘）。**唔准**由 live.confirmed 直接 Hermes／X／Threads 發佈。操作法 [docs/PROMO_CHAIN.md](docs/PROMO_CHAIN.md)。9222 同一 host 一個 tab。Fork zh／WhatsApp／Threads 中文 = 繁體；簡體只准 x.com 中文。Threads compose 揀社羣 **CARDZGAME**。WhatsApp Hermes 用固定群名 **PTCG**（Pokémon 圖）／**Yaichi x Cardz.Game TCG 社區｜4號群**（TCG 圖）／**海賊王**（海賊王圖）（`CHANNEL_HERMES_NAME`），唔准 `send --list` 模糊對、唔准每次 AI 判定。

## 查 bug 之前

先對 [runbook「缺陷形狀清單」](docs/COLLECTION_RUNBOOK.md)。呢個 repo 出過嘅事故有固定形狀
（一欄兩意思、檢查窄過寫入、upsert 淨係 INSERT 講清楚、為 A 遊戲寫嘅規則套落 B 遊戲……），
逐條試快過由零查起。

## 狀態檔位置（唔好自己發明新位）

- 逐卡 checkpoint：`data/runtime/operator/collect/collect_item_checkpoints.json`
- Quarantine（3 連敗跳過）：`data/runtime/operator/collect/collect_quarantine.json`
- Collect 報告：`data/runtime/operator/collect/`
- Rebuild checkpoint 權威：MySQL `cardz_rebuild_checkpoint`（file 只係 receipt）

## 邊份文件講咩

| 文件 | 內容 |
|---|---|
| [docs/HANDOFF_037_FE04.md](docs/HANDOFF_037_FE04.md) | **而家開代**：037／FE04 = 036 PSA10 + BOX；036／FE03 隨時 fallback |
| [docs/COLLECTION_RUNBOOK.md](docs/COLLECTION_RUNBOOK.md) | 五條採集線嘅全量/增量命令、resume 語義、failure receipts、exit codes、port doctrine、freeze 生命週期、FE 對數、**缺陷形狀清單** |
| [docs/HANDOFF_036_20260812.md](docs/HANDOFF_036_20260812.md) | 每日鏈歷史＋未完成項。1322／08-12 數唔係 live |
| [docs/POSTMORTEM_OP_GAP_20260809.md](docs/POSTMORTEM_OP_GAP_20260809.md) | 「pop≥1000 但上唔到 FE」十二個缺陷嘅逐個根因同修法 |
| [PLAN_036_FE02.md](PLAN_036_FE02.md) | **歷史** 036 rebuild 計劃，唔係而家日更 |
| `pipelines/rebuild_036.py` docstrings | 每個 stage 嘅實際行為（code 係權威） |

文件同 code 衝突時：**code 贏**，然後修文件。
