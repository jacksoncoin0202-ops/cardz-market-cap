# CARDZ Market Cap — Soak 監察 Runbook

每日排程 run 出事嗰陣，**人要做咩**。所有數字同格式都係 2026-07-26 喺真嘅 run／真嘅 alert 檔度攞返嚟，唔係照抄設計文檔。

---

## 0. 先講一件會令你誤判嘅事

> **而家冇任何嘢會主動通知你。**

`CARDZ_ALERT_WEBHOOK` 喺呢部機 Machine 同 User scope 都係 **NOT SET**（2026-07-26 05:3x 實測）。`notify_alert.py` 見到冇 webhook 就行呢條路：

```
summary = f"no {WEBHOOK_ENV} configured; alert recorded only"
```

即係話：**alert 只會靜靜寫入檔案**，冇 email、冇 Slack、冇任何 push。soak 期間你唔主動去睇，出咗事你係唔會知嘅。

要有真通知，設 `CARDZ_ALERT_WEBHOOK` 就得（`notify_alert.py` 會 POST JSON payload，內置 `redact()` 濾走敏感值）。未設之前，下面每日檢查係**唯一**發現問題嘅途徑。

### 更陰濕：通知鏈自己會報「成功」

`notify_alert.py` 收尾嘅 exit 邏輯係：

```python
if result["attempted"] and result["webhookConfigured"] and not result["sent"]:
    return 1
return 0
```

冇 webhook → `webhookConfigured` 係 False → **return 0**。

後果係喺 Linux 上，`OnFailure=cardz-market-cap-alert@%n.service` 觸發嘅 alert unit **自己 exit 0、自己 succeed**，`systemctl --failed` 連佢都唔會標紅。你去睇系統狀態，見到「通知鏈行過，冇報錯」，實情係零推送。

換句話講：**「通知鏈健康」呢個訊號本身就係假嘅**，唔好攞佢做判斷依據。Linux 側冇 webhook 時得三個出口，全部要人主動去睇：

| 出口 | 有冇 push |
|---|---|
| `data/runtime/alerts/*.json` | ❌ 淨係寫檔 |
| `journalctl -u 'cardz-market-cap-alert@*'` | ❌ 要自己去睇 |
| `systemctl --failed` | ❌ 見到嘅係死咗嗰個 daily unit，唔係 alert unit |

裝完 webhook 之後要跑 `python scripts/notify_alert.py --self-test`，見到 `[notify] delivered (...)` 先算數（`--self-test` 用 `persist=False`，唔會污染 dedupe state）。

### Throttle 唔會影響檔案，只影響 push

呢兩件嘢好易搞亂，講清楚：

| | 受唔受 throttle |
|---|---|
| `data/runtime/alerts/*.json` 檔 | **唔受**。每次失敗一定寫一個新檔。 |
| webhook push | **受**。同一個 status key 每 `CARDZ_ALERT_REPEAT_DAYS`（預設 **3** 日）先再送一次。 |

`data/runtime/notify/state.json` 而家嘅內容：

```json
"daily": {
  "status": "exit1|price_freshness,snapshot_freshness,source_coverage",
  "occurrences": 2,
  "lastNotifiedAt": null
}
```

所以：**「今日冇收到通知」永遠唔等於「今日冇事」**。睇檔案。修好之後 verify gate 會自動 `clear()` 呢個 state，下次同款失敗先會再出聲。

---

## 1. Soak 通過標準

連續 **2 日** 09:30 JST 排程 run 做到晒以下全部：

1. task／unit 真係入過 Running（唔係跳過）
2. 收工 exit code = **0**
3. `verify_daily_run.py` 四個 check 全 **PASS**
4. 嗰日冇新增 `data/runtime/alerts/daily_verify_*.json`

四條缺一唔算。特別係第 1 條 —— 2026-07-25 就係排程「成功」咁 exit 0 但完全冇新數據（詳見 §5）。

---

## 2. 每日 3 分鐘檢查

### Linux（正式目標）

```bash
bash deploy/linux/cardz-status.sh
```

一句過睇晒 timer 下次幾時行、上次 run 嘅 `ExecMainStatus`、unit 有冇裝。佢內置 exit code 翻譯。

補一句人手驗（唔寫 alert 檔、純唯讀）：

```bash
.venv-backend/bin/python scripts/verify_daily_run.py --no-alert
```

### Windows（過渡期）

```powershell
powershell -NoProfile -File deploy\windows\cardz-status.ps1
```

### 唔用腳本嘅話，睇呢三樣

```bash
# 1. 今日有冇新 alert（有 = 有事）
ls -lt data/runtime/alerts/ | head -5

# 2. 最後一個 run 嘅 log 尾
ls -t data/runtime/logs/daily_*.log | head -1 | xargs tail -30

# 3. snapshot 有冇當日新行 —— 呢個係最終驗收標準
.venv-backend/bin/python scripts/verify_daily_run.py --no-alert
```

---

## 3. Exit code 解碼

### `verify_daily_run.py`

| code | 意思 | 第一步做咩 |
|---|---|---|
| `0` | 四個 check 全過 | 冇嘢做 |
| `1` | 數據檢查唔過 | 開 §4 逐個 check 對 |
| `2` | **驗唔到**（DB 連唔到／`backend.env` 唔見咗） | 唔係數據問題，係基建。檢查 `data/runtime/config/backend.env` 同 DB 可達性 |
| `226` | systemd `226/NAMESPACE` | `ReadWritePaths` 有路徑唔存在，unit 喺 `ExecStart` 之前就死。`mkdir -p` 嗰個路徑 |

`2` 同 `1` 一定要分開睇：`2` 代表**閘本身冇行過**，唔可以當成「數據冇問題」。

### Windows Task Scheduler `LastTaskResult`

| code | 意思 |
|---|---|
| `0` | 完成 |
| `267009` | 仲行緊 |
| `267011` | 由頭到尾未行過 |
| `3221225786` | `0xC000013A` STATUS_CONTROL_C_EXIT —— **被 console CTRL 事件殺死** |

最後嗰個係 2026-07-26 01:47 嗰次真實死法，詳見 §5。

---

## 4. 四個 check 逐個點救

### `price_freshness`
`max(observed_date)` 細過 expected date。

價格觀測係**即時抓先有**，冇可能事後補返舊日期。所以呢個 check 一 FAIL，嗰日就係永久窿，唔好嘥時間試 backfill。要查嘅係「點解價格階段冇行到」——通常喺 log 度會見到鏈喺更早階段死咗。

### `snapshot_freshness`
`market_index_snapshot` 冇當日新行（會列出邊幾個 index stale）。

多數係前面階段失敗嘅**下游症狀**，唔係獨立問題。先去睇 `price_freshness` 同 `source_coverage`，嗰兩個修好呢個通常自己會好。

### `source_coverage`
當日 source 計數唔夠。規則係：一定要有 `gemrate`，加上 `snk_psa10` / `snkrdunk` 至少一個。

只有 `gemrate` 有數 = GemRate 階段行完但價格階段冇行到（**呢個就係 07-25 嘅形態**：`{'gemrate': 1136}`，價格 0）。
只有價格冇 `gemrate` = GemRate key 出事，睇 §6。

### `constituent_sanity`
成分數量突變。呢個係**單向棘輪**保護（task #19）：catalog 縮水會攔住，唔會靜靜發佈一個少咗卡嘅 snapshot。

`228 -> 262` 呢類升幅係正常（新卡入列）。見到**跌**就要查，唔好放行。

---

## 5. 已知窿 · 已知結構性弱點

### 2026-07-25 snapshot 永久缺失
01:47 JST 起嘅 backfill 喺 02:44 被 console CTRL 事件殺死（`0xC000013A`），死喺 GemRate 第 825/1468 版，價格階段完全冇行過。GemRate 當日 1136 行有落地，但冇價格觀測就砌唔到 index snapshot。

**補唔返**，理由見 §4 `price_freshness`。呢日就當永久窿，唔好再試。

### Windows 排程係綁死喺互動 console
該 task 係 `LogonType: Interactive` + `AllowHardTerminate: True` —— 一個兩三個鐘嘅生產 job 掛喺用戶 console 上面，session 一有動靜就死。

`RestartCount=3` 救唔到：Task Scheduler 嘅 restart-on-failure 只喺**起唔到**嗰陣觸發，唔理 action 嘅非零 exit code。呢個設定喺呢度係擺設。

而且 Task Scheduler operational log 係 disabled（開佢要 admin），所以 CTRL 事件嘅來源查唔到，冇 forensic trail。

> **呢個弱點喺遷去 Linux 之後自然消失** —— systemd unit 冇 console 依附，`Restart=on-failure` 係真嘅睇 exit code。呢條係遷移嘅實質理由之一，唔淨係「整齊啲」。

---

## 6. 幾時要停低叫人

| 情況 | 做咩 |
|---|---|
| 連續 2 日同一個 check FAIL | 停低。唔好第三日再等，去查根因 |
| exit `2`（驗唔到） | 即刻處理。閘冇行過 = 你其實乜都唔知 |
| `constituent_sanity` 見到**跌** | 停低。唔好人手覆蓋個棘輪 |
| GemRate key 到期（~2026-07-29） | 見 task #9 應對方案 |
| task／unit `267011` 或者 timer 冇 next | 排程本身冇咗，重裝 |

---

## 7. 相關檔案

| 用途 | 路徑 |
|---|---|
| 驗收閘 | [scripts/verify_daily_run.py](../scripts/verify_daily_run.py) |
| 通知／throttle | [scripts/notify_alert.py](../scripts/notify_alert.py) |
| Linux 狀態 | [deploy/linux/cardz-status.sh](../deploy/linux/cardz-status.sh) |
| Windows 狀態 | [deploy/windows/cardz-status.ps1](../deploy/windows/cardz-status.ps1) |
| 遷移指南 | [docs/SERVER_MIGRATION.md](SERVER_MIGRATION.md) |
| 新卡入列制度 | [docs/CARD_SOURCING_HANDBOOK.md](CARD_SOURCING_HANDBOOK.md) |
| alert 檔 | `data/runtime/alerts/{daily,watchdog}_verify_<date>_<ts>Z.json` |
| throttle state | `data/runtime/notify/state.json` |
| run log | `data/runtime/logs/daily_*.log` |
