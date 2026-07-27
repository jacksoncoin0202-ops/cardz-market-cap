# CARDZ Market Cap — Soak 監察指南

Soak 目標：**連續 2 日 09:30 JST 排程 run 零人手介入全 PASS，且同日 14:07 JST watchdog 亦 PASS** → 判定本地全自動化穩定，可遷移新 server。

## 兩層防護：outcome gate + watchdog

Soak 期間有兩個排程，**兩個都要檢查**：

| 排程 | 觸發 | 職責 |
|------|------|------|
| `CARDZ-Market-Cap-Daily` | 09:30 JST（= 00:30 UTC） | 執行完整採集鏈，收尾**必定**執行一次 [scripts/verify_daily_run.py](../scripts/verify_daily_run.py) 作為 outcome gate |
| `CARDZ-Market-Cap-Watchdog` | 14:07 JST（= 05:07 UTC） | 獨立重驗當日數據，唯讀，不重試 |

**為何需要兩層。** Outcome gate 掛在 daily 之後，只有 daily 真正執行過才會觸發。機器關機、排程被 disable、run 卡死——這三種情況下 gate 一次都不會執行，結果是完全靜音，表面上與「一切正常」無法區分。Watchdog 是獨立排程，不論 daily 有沒有執行都會在 14:07 出聲，因此只有它能分辨「執行了但沒有數據」與「根本沒有執行」。

Watchdog 每次會先記錄 daily 排程本身的狀態（`state` / `LastRunTime` / `LastTaskResult`；找不到該排程則寫 `daily task NOT FOUND`），然後才執行 verify。這一行 header 是判斷的關鍵。

## 每日 5 分鐘 checklist（14:10 之後做，一次看齊兩個排程）

```powershell
powershell -NoProfile -File "C:\Users\jackson0202\Documents\Playground\cardz-market-cap\deploy\windows\cardz-status.ps1"
```

[deploy/windows/cardz-status.ps1](../deploy/windows/cardz-status.ps1) 一次過輸出：兩個排程的 state / last / next 與退出碼解讀、當日數據新鮮度（以 `--no-alert` 唯讀模式執行，**不會**寫 alert 檔污染 soak 記錄）、未清理的 alert 檔清單，以及 `daily_staging_*.log` 與 `watchdog_*.log` 各自最新一份的 `[verify]` 行。該腳本自身的 exit code 等於 verify 的 exit code（0/1/2）。

只在 09:35 檢查 daily 亦可，但 soak 判定必須等 watchdog 執行完才成立。

### 判讀

| 見到 | 意思 | 動作 |
|------|------|------|
| `Result: 0` + 4 行 `[verify] PASS` + 冇 alert | 今日 run 完全成功 | 冇嘢做，soak +1 日 |
| `Result: 1` + alert 檔 | 過程行完但**今日冇新數據**（outcome fail） | 睇下表逐 check 追 |
| `Result: 2` | verify 本身連唔到 DB（Docker 冧咗？） | `docker ps` 檢查 `cardz-market-cap-db-1` |
| `Result: 非零` 而 log 冇 `[verify]` 行 | daily 鏈自己炒咗（fail-fast raise） | 睇 log 最後 traceback |
| Daily 行 `never run yet` 或 `NOT REGISTERED` | 排程根本沒有觸發（機器關機／排程被移除） | 這一日 soak 不計；重新註冊或確認開機時間後由 Day 1 重計 |
| Watchdog `Result: 0` | 14:07 獨立重驗亦通過，當日結果確認 | soak +1 日 |
| Watchdog `Result: 1` 而 daily `Result: 0` | daily 自報成功但四小時後數據仍不新鮮——**優先追這一項**，代表 gate 判斷與實況不符 | 對照兩份 log 的 `[verify]` 行差異 |
| Watchdog `Result: 非零` 而 daily 顯示 `never run yet` | 當日完全沒有執行過，屬靜默失敗 | 檢查機器開關機時間與排程 `Enabled` 狀態 |
| Watchdog 本身 `NOT REGISTERED` | 第二層防護不存在，靜默失敗無人發現 | 立即註冊；未註冊期間的 soak 日數不計 |

## Verify FAIL → 睇邊度

| FAIL check | 根因方向 | 追查位 |
|-----------|---------|--------|
| `price_freshness` | SNK 價冇入（collector 冇爬 / import 冇跑） | log 搵 `public card pages` 進度行；`market_price_observation` 最新 observed_date |
| `snapshot_freshness` | market_alerts 冇寫 snapshot（多數係 effective_date 舊 → INSERT IGNORE 落空） | log 搵 market_alerts 段；對照 `MAX(observed_date)` 係咪今日 |
| `source_coverage` gemrate 缺 | GemRate keyless 採集 fail（page session / mirror 都攞唔到） | log 搵 `[daily] 1468 cards, direct=...` 行之後嘅 fail 統計 |
| `source_coverage` SNK 缺 | SNK collector 冇 run / replay 咗舊輸出 | log 搵 snk 段；`data/runtime/private-landing` 當日 run 目錄 |
| `constituent_sanity` WARN | 成份卡數跌 >20%（唔 fail，但要人眼判斷） | 對比前日 snapshot constituent_count |

追完根因：修 → 手動補 run（見下）→ 當日 soak 唔計，重新起計。

## 手動補 run（同排程完全一致）

```powershell
Start-ScheduledTask -TaskName 'CARDZ-Market-Cap-Daily'
```

跑完照上面 checklist 驗。手動改完任何 `.py` 要先過 repo 測試先准補 run。

## 獨立驗收（唔靠排程 log）

```powershell
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap
.venv-backend\Scripts\python.exe -X utf8 scripts\verify_daily_run.py
```

`--expected-date YYYY-MM-DD` 可驗任何一日。exit 0=pass / 1=data fail / 2=infra fail。

## Soak 通過標準

- Day 1、Day 2 兩個 09:30 run：`LastTaskResult=0` + verify 4 checks PASS + 零 alert 檔 + 零人手介入
- 同兩日的 14:07 watchdog 亦要 `LastTaskResult=0`，且與當日 daily 的 gate 結論一致
- 中途任何一日 fail 而要人手修 → 修完重新由 Day 1 計
- 通過後 → 執行 server 遷移（HANDOFF task board 下一階段）

遷移到 Linux 之後，兩個排程換成 `cardz-market-cap-daily.timer`（00:30 UTC）與 `cardz-market-cap-watchdog.timer`（05:07 UTC），觸發時刻與此處完全對應，判讀方式不變；安裝與驗證步驟見 [deploy/systemd/README.md](../deploy/systemd/README.md)。

## 相關檔案

- Gate 本體：[scripts/verify_daily_run.py](../scripts/verify_daily_run.py)（測試：[tests/test_verify_daily_run.py](../tests/test_verify_daily_run.py)）
- 每日檢查單一入口：[deploy/windows/cardz-status.ps1](../deploy/windows/cardz-status.ps1)
- Daily 排程入口：[deploy/windows/run-cardz-daily.ps1](../deploy/windows/run-cardz-daily.ps1)
- Watchdog 排程入口：[deploy/windows/run-cardz-watchdog.ps1](../deploy/windows/run-cardz-watchdog.ps1)
- Alert 檔：`data\runtime\alerts\daily_verify_<date>_<ts>.json`（watchdog 寫的帶 `--tag watchdog`）
- Linux 對應單元：[deploy/systemd/README.md](../deploy/systemd/README.md)
- 交接總覽：[HANDOFF.md](HANDOFF.md)
