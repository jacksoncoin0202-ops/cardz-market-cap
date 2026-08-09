# AGENTS.md — cardz-market-cap-fe-db-20260805

任何 agent（Claude / Codex / 其他）喺呢個 repo 開工前必讀。呢度只放「跟錯會出事」嘅硬規矩；操作細節全部喺 **[docs/COLLECTION_RUNBOOK.md](docs/COLLECTION_RUNBOOK.md)** —— 做任何採集（全量/增量）之前先讀佢，跟佢嘅 canonical 命令，唔好自己憑記憶砌 flag。

## 硬規矩（違反 = 事故）

1. **唔准 `git add -A` / `git add .`** — 逐個檔 add。`data/private/` 同 `data/runtime/` 已 gitignore（2026-08-08 起），但呢條規矩照守：working tree 隨時有唔應該入 repo 嘅嘢。
2. **`backend.env` 一個 byte 都唔准改**（讀可以）。任何 rebuild DDL/DML 用 `data/runtime/config/rebuild.env`（`--credentials-env`）。writer freeze 期間 `cardz@%` 只有 SELECT。
3. **PriceCharting 只用 CDP port 9333**（headed Chrome，`scripts/ensure_chrome_cdp.ps1 -Port 9333`）。9222 係 Codex 嘅 browser profile：唔准掂，唔准 fallback。headless 必被 Cloudflare 擋，唔好試。
4. **唔准同時起兩個 `rebuild-036` orchestrator**（冇 process mutex 保護你）。有 background run 行緊時，要跑 stage 就直接 call stage function，唔好再入 orchestrator。
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
| [docs/COLLECTION_RUNBOOK.md](docs/COLLECTION_RUNBOOK.md) | 五條採集線嘅全量/增量命令、resume 語義、failure receipts、exit codes、port doctrine、freeze 生命週期、FE 對數、**缺陷形狀清單** |
| [docs/POSTMORTEM_OP_GAP_20260809.md](docs/POSTMORTEM_OP_GAP_20260809.md) | 「pop≥1000 但上唔到 FE」十二個缺陷嘅逐個根因同修法 |
| [PLAN_036_FE02.md](PLAN_036_FE02.md) | 036 rebuild 總計劃（stage 定義、gate 條件） |
| `pipelines/rebuild_036.py` docstrings | 每個 stage 嘅實際行為（code 係權威） |

文件同 code 衝突時：**code 贏**，然後修文件。
