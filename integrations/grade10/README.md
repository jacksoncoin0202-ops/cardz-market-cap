# Grade10 private acquisition integration

呢個目錄係 CARDZ Market Cap 私有取得層嘅可重建 dependency。七個 upstream 檔案按 `UPSTREAM_MANIFEST.json` 原樣保存；`run_service.py` 係 CARDZ 新增嘅 Windows／Linux 共用入口。任何執行結果只可以寫入已被 Git ignore 嘅 `integrations/grade10/data/`，不可進入 public snapshot 或 browser build。

## 正式 CARDZ 口徑

- `grade10_scraper.py`：可用作廣域 index discovery 及 bootstrap/card payload 取得。
- `grade10_analytics.py`：只保留作舊格式相容／問題追查，**不可**直接成為 canonical 1d／7d／30d 或成交額。
- `grade10_kline.py`：只保留作舊格式相容；其 OHLC 並非按真實逐宗成交時間排序，**不可**公開稱為真 K 線。
- `run_daily.bat`：只係原 Windows 操作證據；內含舊機器絕對路徑，AWS／新 Windows 安裝均不可執行它。
- CARDZ 排名、升跌、成交 aggregate、population resolver 仍由 repo 根目錄 `pipelines/` 從 dated canonical observations 重新推導。

## 離線驗證

```bash
python integrations/grade10/run_service.py self-check
```

驗證會比對七個原始檔 SHA-256、語法及 Python dependency，不會連線或下載資料。

## 取得資料

```bash
# 每日低成本 discovery，只更新 index/constituents
python integrations/grade10/run_service.py collect --scope index --expected-cards 600

# 明確 bootstrap/backfill 才下載全部 600 張 detail/image
python integrations/grade10/run_service.py collect --scope full --expected-cards 600

# 如只需 payload、不要圖片
python integrations/grade10/run_service.py collect --scope full --skip-images --expected-cards 600
```

`run_service.py` 使用跨平台單例鎖、子程序非零退出即失敗，並在收集後驗證 `_state/last_run.json` 及 card count。AWS/Linux 不使用 `run_daily.bat` 或 Windows Task Scheduler。

## 舊格式衍生資料（只供相容）

```bash
python integrations/grade10/run_service.py legacy-derive
```

此命令明確標記為 legacy，CARDZ production pipeline 不會呼叫它。

完整 CARDZ service 由根目錄 `scripts/backend.py` 啟動；見 `docs/RUNBOOK.md` 及 `deploy/systemd/`。密鑰只經 AWS Secrets Manager／程序環境注入，不能加入此目錄或 Git。
