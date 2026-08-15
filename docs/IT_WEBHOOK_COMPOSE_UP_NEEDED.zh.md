# CARDZ 網站部署 Webhook 改動說明（給技術）

日期：2026-08-15  
站點：https://app.cardzmarketcap.com  

倉庫：`jacksoncoin0202-ops/cardz-market-cap`  
分支：只處理 `main`  
Webhook：`https://spwebhook.funtoken.me/hooks/deploy-cardzmarketcap`（唔好加 `.com`）

呢份係**唯一要改嘅伺服器命令**。唔改 URL、唔改觸發條件、唔改 secret、唔改 GitHub webhook 設定。

---

## 1. 請改咩

而家（已確認契約）：

```text
git pull
docker compose up --build
```

請改成（`git pull` 之後）：

```text
git pull
bash scripts/compose_up_needed.sh
```

腳本已喺 `origin/main`（commit `79c6cb68`，mode `100755`）：

```text
scripts/compose_up_needed.sh
```

工作目錄必須係 **git pull 完嗰個網站 repo 根目錄**（有 `compose.yaml` 同 `scripts/compose_up_needed.sh` 嗰度）。唔好喺第二個目錄跑。

`git pull` 請保持而家做法（要留到 `ORIG_HEAD`，腳本靠佢判斷今次 pull 改咗咩）。如果而家用 `git fetch` + `reset --hard`，請改用會寫 `ORIG_HEAD` 嘅 `git pull`，或者 pull 完先跑：

```text
ORIG_HEAD=<pull 前 SHA> bash scripts/compose_up_needed.sh
```

---

## 2. 影響大唔大？

**唔大。** 只換「pull 完之後起容器」嗰一句。網站產品、資料、域名、HTTPS、GitHub 觸發（commit 含 `[deploy]`）全部唔變。

| 項目 | 改之前 | 改之後 |
|---|---|---|
| 觸發 | push `main` 且 commit 有 `[deploy]` | 一樣 |
| Webhook URL | 同上 | 一樣 |
| 前端改動（`apps/web/`、lockfile、Dockerfile、`compose.yaml`） | 全量 `docker compose up --build`（會 `next build`） | **一樣全量 `--build`** |
| 每日只換數（`seed-snapshot.json` / `box-subset.json` / `market-assets/*.webp`） | 都係全量 `--build`（幾分鐘） | **唔 rebuild**，只 `compose up --no-build` + `restart web`（大約十幾秒） |
| 第一次冇 `ORIG_HEAD` | — | 腳本自己 fallback 做一次 `--build`（安全） |

用家睇到嘅頁面、卡數、BOX `/box`、健康檢查欄位，**唔會因為呢次改命令而變**。變嘅只係「純換數嗰日等幾耐先上到街」。

---

## 3. 腳本點判斷（唔使改腳本）

`git pull` 之後比較 `ORIG_HEAD` → `HEAD`：

**會 `--build`（同而家一樣）：**

- `apps/web/`
- `packages/`
- `package.json` / `package-lock.json`
- `apps/web/Dockerfile` / `Dockerfile`
- `compose.yaml`

**只 restart（新捷徑）：**

- 得 `data/public/seed-snapshot.json`
- 得 `data/public/box-subset.json`
- 得 `data/public/market-assets/*.webp`
- 或者其他唔喺上面名單嘅檔

前置（2026-08-15 已上 `main`）：`compose.yaml` 已用 volume 掛 seed／BOX sidecar。容器 restart 會讀主機上新檔，唔使再 bake 入 image。

---

## 4. 風險同回滾

風險細，而且全部有退路。

| 風險 | 實際影響 | 點處理 |
|---|---|---|
| 腳本路徑錯／權限唔夠 | 今次 deploy 失敗，舊容器通常仲喺度服務 | 改返 `docker compose up --build` 即刻復原 |
| `ORIG_HEAD` 唔存在 | 腳本自己 `--build`，行為同而家一樣 | 唔使做 |
| 誤判「純換數」但其實改咗前端 | 理論上會用舊 JS 配新數 | 判斷名單偏保守；改 `apps/web/` 一定 build。若懷疑，手動跑一次 `docker compose up -d --build` |
| `restart web` 期間 | 大約幾秒連唔上 | 比而家成個 `next build` 短好多 |
| volume 未掛上 | restart 讀唔到新 seed | `79c6cb68` 已加 volume。若 `docker inspect` 見唔到三條 volume，先 `--build` 一次再切捷徑 |

**回滾（一分鐘）：** webhook 命令改返

```text
git pull
docker compose up --build
```

唔使改 repo、唔使改 GitHub。

---

## 5. 技術改完點驗

1. 確認主機 repo 根目錄有 `scripts/compose_up_needed.sh`，而且 `bash scripts/compose_up_needed.sh` 唔報 `No such file`。
2. 下一次**只換數**嘅 `[deploy]`（日常 `release: daily CARDZ 037 FE04 … [deploy]`）之後，主機 log 應見：

   ```text
   compose_up_needed: data-only; restart without next build
   ```

   唔應該再見到成段 `next build`。
3. 開 https://app.cardzmarketcap.com/api/health  
   要 `status=ok`，`product=037`，`presentation=FE04`，`box.path=/box`。  
   `generation` / `generatedAt` 要跟到嗰次 bake。  
   **唔好**用 `build` 欄判斷成功（而家永遠係 `local`）。
4. 下一次有人改 `apps/web/` 再 `[deploy]`，log 應見：

   ```text
   compose_up_needed: FE/lockfile changed; --build
   ```

   呢轉仍然會全量 build，屬正常。

---

## 6. 唔准改

- Webhook URL（唔好加 `.com`）
- 觸發條件（仍然只要 commit message 含 `[deploy]`）
- 分支（仍然只 `main`）
- secret / deploy key
- `compose.yaml` volume（已經喺 `main`）
- 唔好 `docker compose down -v`（會清 volume／資料）

有問題先回滾第 4 節嗰句，再通知 owner。
