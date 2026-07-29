# Production webhook 出街（本機驗 → main + [deploy]）

> **未授權 push 前唔好執行 §3。** 先用本機 3800 驗數據。

## 契約（IT 已確認）

| 項目 | 值 |
|---|---|
| 分支 | `main` only |
| 觸發 | commit message 含 `[deploy]` |
| 伺服器 | `git pull` → `docker compose up --build` |
| 站點 | https://app.cardzmarketcap.com |
| 本機 compose | 根目錄 [`compose.yaml`](../compose.yaml)（web；`compose.backend.yaml` 只係 MySQL） |

## 1. 本機預覽（你而家）

資料來源：`plan_a_qc3_20260729_154227`（#1 梵高帽比卡）。  
原始檔缺 `viewRank` / `marketRank` / coverage claim，前端 `assertPublicSnapshot` 會拒收；本機用修補檔：

- [`temp/local_preview_3800_snapshot.json`](../temp/local_preview_3800_snapshot.json)（只改 rank 欄 + contentSha256，**數字同原 generation**）

```powershell
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap
$env:CARDZ_RUNTIME = "node"
$env:MARKET_DATA_ALLOW_DEMO = "true"
$env:MARKET_DATA_SNAPSHOT_PATH = (Resolve-Path "temp\local_preview_3800_snapshot.json").Path
$env:MARKET_DATA_ASSETS_PATH = (Resolve-Path "data\public\market-assets").Path
cd apps\web
npx next dev -H 127.0.0.1 -p 3800
```

開：http://127.0.0.1:3800  
health：http://127.0.0.1:3800/api/health  

驗收點：#1 Grey Felt Hat Pikachu、市值 ~$126.9M、1d/7d/30d 有數、圖齊。  
**未 push。**

## 2. Webhook 煙霧（小 commit，唔帶真數據）

```text
chore: webhook smoke test [deploy]
```

只上 `main`，改動極小（例如本檔或 `compose.yaml`）。目的：確認 pull + rebuild 有反應。

## 3. 真數據上板（你驗完本機先做）

1. 將要出街嘅 snapshot 寫入 `data/public/seed-snapshot.json`（Docker build 讀呢份）
2. 對應 `market-assets` 齊（LFS）
3. merge 入 `main`
4. commit message 含 `[deploy]`
5. `git push origin main`
6. 開 https://app.cardzmarketcap.com 驗

**Secret 永不入 git。**
