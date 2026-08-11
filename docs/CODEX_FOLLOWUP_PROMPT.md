# 交俾 Codex 跟進 —— 貼呢段落去就得

> 用法：喺 `C:\Users\jackson0202\Documents\Playground\cardz-market-cap-fe-db-20260805` 開 Codex，
> 將下面成段貼落去做第一個 prompt。佢自己會讀齊 context，唔使你再解釋。
> 每次開新 session 都貼返同一段（改一改「今日狀態」嗰兩行就得）。

---

## 貼呢段 ↓

```
你接手 CARDZ Market Cap 036 / FE03。開工前必須按次序讀晒呢四份，唔准跳：

1. AGENTS.md                        — 硬規矩，違反即係整爛嘢
2. docs/HANDOFF_036_20260812.md     — 現狀、演化史、閘 tier 分級、未完成清單（★ 主文件）
3. docs/COLLECTION_RUNBOOK.md       — 採集操作唯一權威
4. CLAUDE.md                        — 入口索引

文件同 code 衝突時 code 贏。PROJECT_STATE.md 由「Locked baseline」到「2026-08-07
release state」之間全部係歷史記錄，唔係現狀 —— 唔好照住佢做計劃。

===== 你嘅任務 =====

按 handoff §8.1 個次序做九件事，目標係令 036 由「唔使人手㩒」變成「無人睇住都
唔會靜靜出錯數」。次序係計過嘅，唔好自己調轉：

  9 → 3 → 1 → 2 → 4 → 6 → 8 → 5 → 7

點解係呢個次序：
  第 9（test 上自動 call site）先做，因為佢係天花板 —— 唔做佢，你之後加嘅所有閘
  都係「有 code 但冇人跑」，等於冇做。
  第 3（receipt 加返 ranked / awaitingFreshPrice）第二，因為冇佢你之後嘅修補
  冇嘢可以比、冇嘢可以驗。
  第 1（PC 價年齡政策）第三，因為佢有死線。

🔴 死線：2026-08-31。嗰日 MAX_CURRENT_PRICE_AGE_DAYS = 30 會一次過掃走全部
PriceCharting 價（PC 出月線，每個檔最後一點 stamp 2026-08-01）。上限 925/1322 =
70% 卡失去排名，包括 rank 1。四層閘全部接唔住，receipt 照寫 accepted: 1322，
睇落完全正常，照 push 照 deploy。詳細機制同三條修法見 handoff §7。

第 1 件事開工之前，你要先量一個數（handoff §7.2 講嘅「實數」）：08-31 真係會失
幾多張 rank？上限 925 係假設冇 fallback，但 winner key 嘅 route priority 行先，
有第二來源嘅卡會跌落去繼續有 rank（價會靜靜跳，一樣係事故）。要 query DB 先知。

===== 硬規矩（違反 = 整爛嘢）=====

唔准：
- git add -A / git add . —— 逐檔 add
- 改 backend.env 一個 byte（讀得）
- 密碼入 log / commit —— 一律 docker exec cardz-market-cap-db-1 sh -c 'mysql -u root -p"$MYSQL_ROOT_PASSWORD" ...' 喺 container 入面展開，再 | grep -v "Using a password"
- commit data/private/pricecharting_session（cookie）、.env、token、data/runtime/private-source-map/
- 掂 CDP 9222（PC 爬蟲用 9333，headed only —— headless 一定俾 Cloudflare 擋）
- 開 generation 037
- 手改 c11_pc_ebay_map_full900.jsonl（佢同時係 PC sweep 入口清單）
- 放鬆任何 acceptance gate 嚟造靚數
- 兩條 collector 同時跑
- 掂 apps/web/src/app/globals.css、fe03-server.ps1、scripts/fe03-verify.cjs、.claude/
- 刪舊 cardz-market-cap/ folder（佢 owns MySQL 3308 個 14GB volume）
- --no-verify / 繞過簽名
- 加 QC 功能、加防禦性代碼

一定要：
- commit trailer：Co-Authored-By: <你自己>
- 出街靠 commit subject 入面個 [deploy] marker 推上 main
- 任何「有檢查但零 call site」當冇檢查 —— 加閘之後要即場證明佢會 fire
  （做法：故意改返舊 code → 睇住個 test 紅 → 還原 → 全綠，兩個結果都貼出嚟）
- 改咗 FE / bake script 一定要 push 上 main，否則出街嗰份仲係舊嘢而且冇嘢會嗌
  （release 由 WSL ~/cardz-market-cap-release-daily 行，只睇得到 origin/main）

===== 環境 =====

repo（真身）  C:\Users\jackson0202\Documents\Playground\cardz-market-cap-fe-db-20260805
branch        rebuild/036-foundation，推上 main
Python        C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe（CJK 加 -X utf8）
DB            docker cardz-market-cap-db-1，127.0.0.1:3308，cardz_market_cap，MySQL 8.4.10
release repo  WSL /home/jackson0202/cardz-market-cap-release-daily，branch daily-release-main
test          python -X utf8 scripts/run_all_tests.py
排程（JST）    03:30 夜鏈 / 09:30 朝鏈+發佈 / 11:30、16:30 重試

===== 交付要求 =====

每完成一件事：
1. 貼出「閘會 fire」嘅實證（紅一次 + 綠一次）
2. 逐檔 git add + commit，唔好一次過
3. 更新 docs/HANDOFF_036_20260812.md §8.1 嗰張表，剔走做完嗰行
4. 講返做咗咩、量到咩數；用 [KNOWN]/[COMPUTED]/[INFERRED]/[GUESS] 標籤，
   唔知就直接講唔知，唔好靠估

全部九件做完之後，再跑一次全量 e2e（scripts/morning_browser_lanes.ps1）證明
fail=0、inserted>0、有 [deploy] commit 上到 main。
```

---

## 你自己每日想 check 一句嘢，可以問佢

```
睇一睇 data/runtime/logs/ 最新嗰個 morning-*.log 同 nightly-*.log，
用 [KNOWN] 標籤答我三句：collect / accept / publish 各自 exit 幾多？
今日 accepted 同 ranked 各幾多張？同噚日比跌咗冇？
```
