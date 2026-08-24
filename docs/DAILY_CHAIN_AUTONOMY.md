# 每日鏈自動成功閘

> **⚠ 歷史檔（2026-08-24 封存）：** 呢份係 **036 舊鏈**嘅 autonomy 定義（`daily_chain_autonomy.json` receipt）。V2 鏈嘅現行定義（event 107 + `manual_intervention_count=0` 連續兩日，journal `proven_autonomous`）喺 [DAILY_CHAIN_V2_CUTOVER.md](DAILY_CHAIN_V2_CUTOVER.md)；現狀睇 [../PROJECT_STATE.md](../PROJECT_STATE.md)。
> **2026-08-15：** 呢份係資料 bake 成功閘。FE 出街車 = `../cardz-market-cap-037-fe04-live`。Live 卡數而家 1449，唔係 1368／1322。

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
- BOX sidecar 價（計；P6 起由 `sealed_daily.py` 入日鏈，唔再人手搬）

## 2026-08-14 狀態

死結已拆（discover/e2e 唔再擋出街）。今日未 proven。自然證明要睇 08-15 同 08-16。
