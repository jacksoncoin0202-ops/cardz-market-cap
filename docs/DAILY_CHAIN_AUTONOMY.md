# 每日鏈自動成功閘

DADDY 2026-08-14 寫死。

## 一句話

連續兩個 JST 日，排程自己更新到 live，先算自動成功。人手補推唔計。未 `proven` 唔准講「每日識得自己更新」。

## 點樣先算一日成功

排程 process 帶 CARDZ_DAILY_CHAIN=1（朝鏈 09:30，或當日 11:30／16:30 重試），而且：

- daily_public_release.sh live health 對到當日 bake 嘅 generation + generatedAt，或
- no-change 閘確認 live 已經係同一份

scripts/stamp_daily_chain_autonomy.py 先會記嗰個 JST 日。

## 點樣先算真成功

`data/runtime/operator/daily_chain_autonomy.json` 入面：

- `consecutiveScheduledDays >= 2`
- `proven = true`

唔計：

- 人手跑 `daily-accept` / `daily_public_release.ps1`（冇 `CARDZ_DAILY_CHAIN=1`）
- 只係 Task Scheduler LastRun 郁咗但冇 live 對數
- BOX sidecar 價（仍然唔喺呢條 PSA10 日鏈）

## 2026-08-14 狀態

死結已拆（discover/e2e 唔再擋出街）。今日未 proven。自然證明要睇 08-15 同 08-16。
