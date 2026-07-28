# 部署交接手冊（唔識都可以跟）

> 給接手同事。詳細長文見 [SERVER_MIGRATION.md](SERVER_MIGRATION.md) · [deploy/systemd/README.md](../deploy/systemd/README.md) · [RUNBOOK.md](RUNBOOK.md)。  
> **本檔只講最短可跑路徑。**

---

## 0. 架構一句

```text
MySQL（canonical DB）
  → pipelines 採集／QC
  → canonical_public_snapshot.py
  → data/public/publish-staging/（snapshot + pointer）
  → apps/web（Next.js）只讀 snapshot
```

前端**永遠唔直連 MySQL**。

---

## 1. 本機 Windows（開發／驗板）

### 1.1 依賴

- Windows 11  
- Node **24** + npm **11**  
- Python **3.10+**（本機：`C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe`）  
- Docker Desktop（MySQL `127.0.0.1:3308`）  
- Git LFS  

### 1.2 機密

```text
data/runtime/config/backend.env   # 或 /etc/cardz-market-cap/backend.env
```

必備變數（例）：

```env
CARDZ_DB_HOST=127.0.0.1
CARDZ_DB_PORT=3308
CARDZ_DB_USER=...
CARDZ_DB_PASSWORD=...
CARDZ_DB_NAME=cardz_market_cap
```

**永不 commit。**

### 1.3 起 DB

```powershell
# 按 repo docker-compose / 既有 container 起 MySQL:3308
# 確認：
Test-NetConnection 127.0.0.1 -Port 3308
```

### 1.4 起前端

```powershell
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap
npm install
# 確保 pointer / seed-snapshot 存在
# data/public/publish-staging/latest.json
# data/public/seed-snapshot.json
cd apps\web
npm run dev
# 瀏覽器開 http://127.0.0.1:3000（或 terminal 顯示嘅 port）
```

`predev` 會 build market-data 同 sync snapshot。

### 1.5 重建 FE snapshot（DB 更新後必做）

```powershell
$env:CARDZ_DB_HOST = "127.0.0.1"
# load backend.env into process env first

python -X utf8 pipelines\canonical_public_snapshot.py `
  --view top300_boards `
  --presentation data\public\seed-snapshot.json `
  --output data\public\publish-staging\generations\canonical_live_fe\snapshot.json `
  --host 127.0.0.1 --port 3308 `
  --database $env:CARDZ_DB_NAME --user $env:CARDZ_DB_USER --password $env:CARDZ_DB_PASSWORD

# 更新 latest.json pointer 指去 generations/canonical_live_fe/snapshot.json
# 然後：
cd apps\web
node scripts\sync-snapshot.mjs
```

要求：`market_index_snapshot` 有 `tcg-combined` 且 `evaluation_id` 對應 `publish_gate_status=passed`。

---

## 2. Linux / AWS（systemd）

### 2.1 目錄與用戶

```bash
# 預設
REPO=/opt/cardz-market-cap
ENV=/etc/cardz-market-cap/backend.env
USER=cardz
```

```bash
sudo useradd --system --create-home --home-dir /var/lib/cardz --shell /usr/sbin/nologin cardz || true
sudo install -d -m 0750 -o cardz -g cardz /etc/cardz-market-cap
cd /opt/cardz-market-cap
git lfs install --local
git lfs pull   # 需要完整 market-assets 時必須全拉
```

### 2.2 Secrets

```bash
sudo install -m 0600 /path/from/secrets/backend.env /etc/cardz-market-cap/backend.env
# LF only, root:root 0600
# CARDZ_DB_MODE=external + CARDZ_DB_* ; 非 loopback 要 CARDZ_DB_SSL_CA
```

### 2.3 安裝 unit

```bash
sudo deploy/linux/cardz-daily-systemd.sh dry-run
sudo deploy/linux/cardz-daily-systemd.sh install
# 確認 cutover 後先：
sudo deploy/linux/cardz-daily-systemd.sh install --enable --start --confirm-single-writer
```

常見 unit：

| Unit | 作用 |
|---|---|
| `cardz-market-cap-web.service` | Next / web |
| `cardz-market-cap-daily.service` + timer | 日更採集／publish |
| watchdog / gemrate freeze | 見 deploy/systemd |

### 2.4 健康檢查

```bash
sudo systemctl status cardz-market-cap-web.service
curl -sS http://127.0.0.1:<port>/api/health
# 或 repo：
node apps/web/scripts/verify-runtime-health.mjs
```

---

## 3. Cloudflare / Staging web

```powershell
cd apps\web
npm run deploy:staging
# 或 build:cloudflare + wrangler（見 package.json）
```

公開邊界：`node scripts/verify-public.mjs`（必要時 `--allow-demo`）。

---

## 4. Windows 日更（Task Scheduler）

```powershell
# 例：deploy/windows/install_daily_task.ps1
# 必須 Windows Python + WorkingDirectory = repo root
# 唔好用 WSL cron 做 production
```

---

## 5. 出事點（最常見）

| 症狀 | 查 |
|---|---|
| 前端舊數據 | pointer 未更新／未 sync-snapshot |
| snapshot 失敗「no ranking」 | evaluation `publish_gate_status` 未 `passed` 或 snapshot `evaluation_id` null |
| 圖 404 | LFS 未 pull；`market-assets` 缺 webp |
| WSL file not found | 改用 Windows Python + 反斜線 path |
| env 怪錯 | `backend.env` CRLF → strip `\r` |

---

## 6. 部署完成定義

- [ ] DB 可連  
- [ ] `status` 正常  
- [ ] FE_SET snapshot 七欄 100%（見 handoff）  
- [ ] pointer → 正確 generation  
- [ ] web health 200  
- [ ] secrets 唔在 git  
- [ ] 日更 timer／Task 已登記（若要自動）  
