# CARDZ Market Cap — Windows → Linux Server 遷移清單

> 寫於 2026-07-26。**目標平台：Linux server。** 排程用 systemd timer，cron 做 fallback。
> 範圍：後端每日數據鏈（採集 → canonical MySQL → 排名 → snapshot）+ watchdog。前端 Cloudflare Worker 發佈鏈唔喺本文範圍，見 [docs/RUNBOOK.md](RUNBOOK.md)。
> 本文只列環境變數 **key 名** 同取值來源；任何密碼／API key／token／bucket 實際值都唔會出現。

---

## 0. 前提／現況

### 0.1 現行 Windows 主機（source）——呢啲檔唔會搬過去

以下全部係 Windows-only，喺 Linux 上**完全冇用**，必須用 Linux 等價物取代：

| Windows 檔 | 作用 | Linux 等價物 |
|-----------|------|-------------|
| [deploy/windows/run-cardz-daily.ps1](../deploy/windows/run-cardz-daily.ps1) | daily 執行 wrapper（load env → 跑 daily → 跑 verify） | [deploy/systemd/run-cardz-daily.sh](../deploy/systemd/run-cardz-daily.sh)（**已存在**，符合 [§6.1 契約](#61-shell-wrapper-契約)） |
| [deploy/windows/run-cardz-watchdog.ps1](../deploy/windows/run-cardz-watchdog.ps1) | 獨立補跑 verify，捉「daily 根本冇 run」 | [deploy/systemd/run-cardz-watchdog.sh](../deploy/systemd/run-cardz-watchdog.sh)（**已存在**） |
| [deploy/windows/cardz-status.ps1](../deploy/windows/cardz-status.ps1) | 唯讀 soak 狀態面板 | `deploy/linux/cardz-status.sh`（**要新寫**，[§7.3](#73-cardz-statussh-等價物)） |
| [deploy/windows/install_daily_task.ps1](../deploy/windows/install_daily_task.ps1) | 註冊／查詢／移除 Task Scheduler 兩個 task | `systemctl enable --now`（[§6.4](#64-安裝)） |
| [pipelines/run_daily.ps1](../pipelines/run_daily.ps1) | 舊 PowerShell 入口 | 已由 `backend.py daily` 取代，唔使搬 |

Windows Task Scheduler 現行配置（出處 [deploy/windows/install_daily_task.ps1](../deploy/windows/install_daily_task.ps1)），做 Linux 側嘅對照基準：

| 項目 | Daily task | Watchdog task |
|------|-----------|---------------|
| 名 | `CARDZ-Market-Cap-Daily` | `CARDZ-Market-Cap-Daily-Watchdog` |
| 時間 | 09:30 JST（= **00:30 UTC**） | 14:07 JST（= **05:07 UTC**） |
| 逾時 | `ExecutionTimeLimit` 6 小時 | 20 分鐘 |
| 並行 | `-MultipleInstances IgnoreNew` | — |
| 補跑 | `-StartWhenAvailable` | `-StartWhenAvailable` |
| 權限 | `-RunLevel Limited`（非 admin） | 同左 |

### 0.2 Repo 已有嘅 Linux 部署檔——**呢套就係 production artifacts**

[deploy/systemd/](../deploy/systemd/) 加 [deploy/linux/](../deploy/linux/) 就是真正要部署的一套；[deploy/windows/](../deploy/windows/) 與各 `.ps1` 屬過渡期產物，不會搬過去。本文早期版本把其中三項列為「致命缺陷」，該三項已於 2026-07-26 修正，現況如下表。**請以下表為準，不要再照舊描述改回舊值。**

| 檔 | 現況 |
|----|------|
| [cardz-market-cap-daily.timer](../deploy/systemd/cardz-market-cap-daily.timer) | ✅ **已修正**：`OnCalendar=*-*-* 00:30:00 UTC`（= 09:30 JST）。舊值 `06:30 Asia/Tokyo` 等於 UTC 前一日 21:30，正是 2026-07-25 靜默事故的成因，**不得改回**，理由見 [§8.1](#81--排程時間--最高風險) |
| [run-cardz-daily.sh](../deploy/systemd/run-cardz-daily.sh) | ✅ **已修正**：結尾不再使用 `exec`，daily 之後無論成敗都會執行 `scripts/verify_daily_run.py`，理由見 [§8.2](#82--verify-gate-已修正不得改回) |
| [cardz-market-cap-daily.service](../deploy/systemd/cardz-market-cap-daily.service) | ✅ `TimeoutStartSec=21600`（6 小時，舊值 7200 秒會在 GemRate 爬取中途殺掉整棵 process tree）／✅ **已覆核**：`ProtectSystem=strict` 配合的 `ReadWritePaths` 白名單覆蓋 collect 側四條 + publish 側三條，共七條，provenance 註釋已寫入 unit 檔，見 [§8.3](#83-readwritepaths-已覆核)／✅ **已開發佈**：`Environment=CARDZ_DAILY_PUBLISH=local`，見 [§6.2](#62-cardz-market-cap-dailyservice) |
| [cardz-market-cap-watchdog.service](../deploy/systemd/cardz-market-cap-watchdog.service)／[.timer](../deploy/systemd/cardz-market-cap-watchdog.timer)／[run-cardz-watchdog.sh](../deploy/systemd/run-cardz-watchdog.sh) | ✅ **新增**：獨立 outcome watchdog，`05:07 UTC`（= 14:07 JST），跑 `verify_daily_run.py --tag watchdog` |
| [deploy/linux/cardz-daily-systemd.sh](../deploy/linux/cardz-daily-systemd.sh) | ✅ **已修正**：`dry-run` 現時印 `schedule=00:30 UTC (09:30 Asia/Tokyo)`、`watchdogSchedule=05:07 UTC (14:07 Asia/Tokyo)`、`timeoutSeconds=21600`，並會一併安裝 watchdog units |

本文 §6 保留完整 unit 契約做逐條對照用。

**Unit 命名（2026-07-26 統一）**：全部 unit 一律叫 `cardz-market-cap-daily.{service,timer}`、`cardz-market-cap-watchdog.{service,timer}`、`cardz-grade10-discovery.{service,timer}`——即 repo [deploy/systemd/](../deploy/systemd/) 入面的實際檔名，亦是 installer 認得的名。本文 §6 範本以前用較短的 `cardz-daily.*`／`cardz-watchdog.*`，已全數改齊；再見到短名一律當過時，唔好照抄。

`ReadWritePaths` 已於 2026-07-26 逐條 grep `pipelines/` + `scripts/` 覆核（結論見 [§8.3](#83-readwritepaths-已覆核)），provenance 註釋寫咗喺 [deploy/systemd/cardz-market-cap-daily.service](../deploy/systemd/cardz-market-cap-daily.service) 入面。`OnFailure=` 已加入 shipped daily unit（用 `cardz-market-cap-alert@%n.service`）；`LANG=C.UTF-8`／`LC_ALL=C.UTF-8`／`PYTHONUNBUFFERED=1` 亦已於 2026-07-26 加入 shipped daily unit。

### 0.3 每日鏈全貌

```
systemd timer (00:30 UTC)
  └─ cardz-market-cap-daily.service (Type=oneshot)
       │    Environment=CARDZ_DAILY_PUBLISH=local   ← local | remote | off
       └─ deploy/systemd/run-cardz-daily.sh
            ├─ scripts/backend.py daily --mode <mode> [--publish [--local-only]]
            │    └─ pipelines/run_daily.py
            │         ├─ singleton_lock(data/runtime/locks/daily.lock)   ← fcntl.flock
            │         ├─ pipelines/fx_rates.py
            │         ├─ market source refresh（GemRate / SNK / TAG / eBay）
            │         ├─ scripts/backend.py import
            │         ├─ pipelines/market_alerts.py（market cap + ranking + snapshot）
            │         └─ ── publish 鏈（off 模式喺呢度 return） ──────────────
            │              ├─ pipelines/canonical_public_snapshot.py   → candidate
            │              ├─ pipelines/ensure_std_card_images.py --write（卡圖自愈）
            │              ├─ verify_images --allow-unreferenced（lenient）
            │              ├─ assert_catalog_not_shrinking（預設 -5%，唔准放寬）
            │              ├─ promote candidate → data/public/seed-snapshot.json
            │              │                    + manifests/image-qc.json
            │              ├─ quarantine_unreferenced_assets（不可逆，所以排喺 gate 之後）
            │              ├─ verify_images（strict）
            │              ├─ npm run build --workspace @cardz/market-data
            │              └─ node pipelines/publish-snapshot.mjs
            │                   └─ (remote 模式先行) R2 上傳 → canary → pointer promote
            └─ scripts/verify_daily_run.py           ← outcome gate，無論上面成敗都要跑

systemd timer (05:07 UTC)
  └─ cardz-market-cap-watchdog.service
       └─ deploy/systemd/run-cardz-watchdog.sh
            └─ scripts/verify_daily_run.py --tag watchdog
```

---

## 1. Pre-migration checklist

### 1.1 ★ 版本控制 gap（第一優先，唔做就一定爆）

**`git status --porcelain` 顯示大量遷移必需檔案根本未入版本控制。clean checkout 會全部冇咗。**

以下係 2026-07-26 實測結果（已隔走 `data/public/market-assets/` 嗰三千幾個圖檔噪音）：

**未 track（`??`）——clone 出嚟完全唔存在：**

| 檔 | 嚴重度 | 影響 |
|----|--------|------|
| `scripts/verify_daily_run.py` | 🔴 **最高** | **成個 outcome gate 就係佢**。冇咗即係盲跑，[§7](#7-驗證) 全部做唔到 |
| `tests/test_verify_daily_run.py` | 🟠 | verify gate 嘅 regression case 冇咗 |
| `deploy/windows/run-cardz-watchdog.ps1` | 🟠 | watchdog 契約來源；本身唔搬，但要照佢寫 Linux 版 |
| `deploy/windows/cardz-status.ps1` | 🟡 | 同上 |
| `pipelines/ensure_std_card_images.py` | 🟠 | 每日鏈會叫，冇咗即 ImportError |
| `pipelines/native_image_refetch.py` | 🟠 | 同上 |
| `pipelines/native_image_resolver.py` | 🟠 | 同上 |
| `pipelines/canvas_normalize_backfill.py` | 🟡 | 圖片正規化 backfill |
| `tests/test_native_image_resolver.py` | 🟡 | — |
| `docs/HANDOFF.md`、`docs/SOAK_GUIDE.md`、`docs/CARD_SOURCING_HANDBOOK.md` | 🟡 | 運維知識，新機無人接手得到 |
| `data/public/presentation-pack.json` | 🟡 | — |
| `manifests/canvas-normalize-report.json`、`manifests/native-image-backfill-report.json` | 🟡 | — |
| `apps/web/src/components/copy-button.tsx`、`tooltip.tsx` | 🟡 | 前端 build 會斷 |
| `docs/SERVER_MIGRATION.md` | 🟡 | 本文自己 |

**已改未 commit（`M`）——clone 攞到嘅係舊版：**

```
deploy/windows/run-cardz-daily.ps1     ← verify gate 就係喺呢個未 commit 嘅改動入面
deploy/windows/install_daily_task.ps1       ← 09:30／UTC 對齊修正喺呢度
pipelines/run_daily.py                 ← 🔴 pipeline 本體
pipelines/active_universe.py
pipelines/canonical_public_snapshot.py
pipelines/db_runtime.py
pipelines/gemrate_source.py
pipelines/market_source_sync.py
pipelines/ranking_derivation.py
pipelines/publish-snapshot.mjs
tests/test_daily_scheduler_contract.py
tests/test_market_source_gemrate.py
tests/test_gemrate_candidate_backfill.py
tests/test_complete_ranking_consumers.py
tests/test_white_border_trim.py
tests/data/pipeline-behavior.test.mjs
apps/web/…（約 20 個檔）
```

**動作（遷移前必須完成）：**

```bash
cd /path/to/cardz-market-cap

# 1. 確認最新狀態（clone 之前一定要再跑一次，清單可能有變）
git status --porcelain | grep -vE 'market-assets|\.webp|\.png|\.ico'

# 2. 逐個加入（唔准 git add -A，會連三千個圖檔一齊入）
git add scripts/verify_daily_run.py tests/test_verify_daily_run.py
git add pipelines/ensure_std_card_images.py pipelines/native_image_refetch.py \
        pipelines/native_image_resolver.py pipelines/canvas_normalize_backfill.py \
        tests/test_native_image_resolver.py
git add deploy/windows/cardz-status.ps1 deploy/windows/run-cardz-watchdog.ps1
git add docs/HANDOFF.md docs/SOAK_GUIDE.md docs/CARD_SOURCING_HANDBOOK.md docs/SERVER_MIGRATION.md
git add apps/web/src/components/copy-button.tsx apps/web/src/components/tooltip.tsx
git add -u pipelines/ deploy/ tests/

# 3. 驗證 clean clone 真係攞齊（唔准靠肉眼睇 git status 交差）
git clone --depth 1 file:///path/to/cardz-market-cap /tmp/clone-check
test -f /tmp/clone-check/scripts/verify_daily_run.py && echo "verify gate OK" || echo "STILL MISSING"
test -f /tmp/clone-check/pipelines/ensure_std_card_images.py && echo "images OK" || echo "STILL MISSING"
rm -rf /tmp/clone-check
```

> 順手清理：`apps/web/NUL` 係 Windows 意外產物（`NUL` 係 Windows 保留裝置名，被重導向誤建）。Linux 上佢會變成一個普通檔。刪咗佢，唔好搬。

### 1.2 憑證清單（要準備嘅 key 名）

**遷移前確認每一個 key 喺 secret manager 攞唔攞到。以下只列名。**

#### 必需（後端每日鏈）

| Key | 用途／模組 | 值由邊度攞 |
|-----|-----------|-----------|
| `CARDZ_DB_PASSWORD` | [pipelines/db_runtime.py](../pipelines/db_runtime.py)、[scripts/verify_daily_run.py](../scripts/verify_daily_run.py) | 舊機 `data/runtime/config/backend.env`；或由 `backend.py` 自動生成（[§4.4](#44--密碼volume-配對陷阱)） |
| `CARDZ_DB_ROOT_PASSWORD` | compose MySQL 初始化 | 同上 |
| `CARDZ_ALERT_WEBHOOK` | [scripts/notify_alert.py](../scripts/notify_alert.py)。**唔設 = 所有失敗只寫檔，冇任何嘢 push 俾人**，而且 alert unit 一樣 exit 0，`systemctl --failed` 睇唔出。詳見 [§6.5](#65-onfailure-通知) | 用戶提供（Slack / Discord incoming webhook 或自架 endpoint）。**唔准憑空作一個域名頂替** |

#### 條件必需

| Key | 條件 | 值由邊度攞 |
|-----|------|-----------|
| `CARDZ_DB_HOST` / `CARDZ_DB_PORT` / `CARDZ_DB_NAME` / `CARDZ_DB_USER` | local docker 有預設值（`127.0.0.1` / `3308` / `cardz_market_cap` / `cardz`）；managed DB 必需明確設 | [compose.backend.yaml](../compose.backend.yaml) / 雲商 console |
| `CARDZ_DB_MODE` | 用 managed DB 時設 `external` | 部署決定 |
| `CARDZ_DB_SSL_CA` | `external` + `--mode production` **必需**，否則 [scripts/backend.py](../scripts/backend.py) 直接 raise `production managed database runs require CARDZ_DB_SSL_CA` | 雲商 CA bundle 檔路徑 |

#### 可選

| Key | 說明 | 值由邊度攞 |
|-----|------|-----------|
| `GEMRATE_API_KEY` | direct transport；**現時 disabled，跑 keyless（public card page + mirror）**。試用期約 07-29 屆滿 | 1Password。**新機唔好補返過期 key**，反而會令 direct transport 撞 403 |
| `CARDZ_FX_ENDPOINT` | 自架 FX endpoint；預設 Frankfurter v2 keyless | 自架時先設 |
| `CARDZ_EBAY_SOLD_ENABLED` / `CARDZ_EBAY_SOLD_INPUT` | eBay 採集，預設 disabled | 保持 unset |
| `CARDZ_PRIVATE_ACQUIRE_SCRIPT` | Grade10 整合入口 | [.env.example](../.env.example) |
| `CARDZ_PYTHON` | 明確 pin 直譯器路徑 | 部署決定 |
| `CARDZ_BROWSER_EXECUTABLE` | pin 系統 Chrome/Chromium 路徑，優先於內建候選 list。GemRate keyless transport 靠佢繞 Cloudflare，Linux 上建議明確設，詳見 [§8.4(b)](#84-路徑分隔符--實測結果) | 部署決定（如 `/usr/bin/chromium`） |
| `CARDZ_WSL_DISTRO` | Windows 專用 WSL 橋接 | **Linux 上唔需要，遷移後刪** |
| `CARDZ_ALERT_REPEAT_DAYS` | 同一 status key 重複 push 嘅間隔（日），預設 3。**只影響 webhook push，唔影響 alert 檔**（alert 檔每次失敗都寫） | 部署決定 |

#### 發佈鏈（只有 `CARDZ_DAILY_PUBLISH=remote` 先需要）

每日 unit 而家預設 `CARDZ_DAILY_PUBLISH=local`（[§6.2](#62-cardz-market-cap-dailyservice)），行足 publish 鏈但只寫本機 public tree，**呢批 key 一個都唔使設**。

改做 `remote`（R2 上傳 + pointer promotion）先需要以下全部——[run_daily.py](../pipelines/run_daily.py) 喺 argparse 之後即刻驗，欠任何一個都會 `RuntimeError` 拒絕開跑，唔會半路先死：

`CARDZ_STAGING_R2_BUCKET`、`CARDZ_PRODUCTION_R2_BUCKET`、`CARDZ_GENERATION_CANARY_COMMAND_JSON`、`CARDZ_POINTER_PROMOTE_COMMAND_JSON`、`CARDZ_CANARY_ORIGIN`、`CARDZ_CANARY_RECEIPT_PATH`、`CARDZ_POINTER_PROMOTION_RECEIPT`、`CARDZ_DEPLOYMENT_ENV` — 由 Cloudflare console / [docs/RUNBOOK.md](RUNBOOK.md)「Secret injection」取得。

> ⚠️ 反過嚟：`CARDZ_DAILY_PUBLISH=local` 之下呢啲 bucket key **唔可以**留喺 `backend.env`。[scripts/backend.py](../scripts/backend.py) 個 `daily_environment()` 會主動由子 process 環境剝走 `CARDZ_STAGING_R2_BUCKET` / `CARDZ_PRODUCTION_R2_BUCKET`，因為 `run_daily.py` 自己讀 env，見到 bucket 就會拒絕 `--local-only`。呢個剝離有 regression test 守住（[tests/test_backend_cli_contract.py](../tests/test_backend_cli_contract.py) `test_daily_local_publish_forwards_local_only_and_withholds_the_bucket`）。

### 1.3 遷移前狀態快照（舊機）

```powershell
# 記低現行成功基線，之後對數用
python -X utf8 scripts\verify_daily_run.py --no-alert
python scripts\backend.py status --json > migration-baseline-status.json
docker exec cardz-market-cap-db-1 sh -c 'exec mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" -e "SELECT index_code, MAX(effective_date) AS latest, COUNT(*) AS rows_total FROM market_index_snapshot GROUP BY index_code"'
```

---

## 2. Linux 主機準備

### 2.1 OS / 帳號 / 時區

```bash
sudo timedatectl set-timezone UTC          # ★ 令排程時間同 run_id 語義一致
timedatectl                                 # 確認 "Time zone: UTC (UTC, +0000)"

sudo useradd --system --create-home --home-dir /var/lib/cardz --shell /usr/sbin/nologin cardz
sudo mkdir -p /opt/cardz-market-cap /etc/cardz-market-cap
sudo chown -R cardz:cardz /opt/cardz-market-cap
```

### 2.2 Locale（必須明確設，唔好留空）

```bash
sudo apt-get install -y locales
sudo locale-gen en_US.UTF-8
sudo localectl set-locale LANG=C.UTF-8      # 或 en_US.UTF-8
locale                                       # LANG / LC_ALL 唔可以係空或者 "POSIX"
```

systemd service 內亦要明寫（[§6.2](#62-cardz-market-cap-dailyservice)），因為 systemd 唔會繼承登入 shell 嘅 locale。

### 2.3 Python

```bash
# Debian / Ubuntu
sudo apt-get install -y python3 python3-venv python3-pip
python3 --version    # 必須 ≥ 3.10；目標係 3.12（見下方版本決定）
```

> **版本決定（2026-07-26）：生產同 CI 一律對齊 Python 3.12，硬底線維持 ≥ 3.10。**
>
> 決定之前三邊唔一致——CI pin `3.10`、Windows 開發機 `.venv-backend` 係 3.10.11、WSL（Linux 驗證環境，Ubuntu 24.04）`python3` 係 3.12.3。兩個 wrapper（[run-cardz-daily.sh](../deploy/systemd/run-cardz-daily.sh)、[run-cardz-watchdog.sh](../deploy/systemd/run-cardz-watchdog.sh)）揀直譯器係由新到舊探測 `python3.14…python3.10` 再 fallback `python3`，所以 Ubuntu 24.04 server 實際命中 **3.12**。即係話舊 CI pin 3.10 跟嘅係 Windows 開發機，從來冇測過生產真正會用嗰個版本。
>
> 揀 3.12 嘅理由就係要 CI 測返生產嗰個直譯器。repo 冇 `pyproject.toml` 亦冇 `setup.cfg`，所以 `requires-python` 從來冇宣告過；唯一嘅版本 gate 係兩個 wrapper 入面嘅 `sys.version_info < (3, 10)`，**維持不變**，Windows 開發機（3.10.11）照跑得。
>
> 代碼側已逐項核過**冇**版本專屬語法／API，換 pin 唔使改代碼：冇 `match`；冇 3.11+ 嘅 `tomllib`／`TaskGroup`／`StrEnum`／`Self`／`datetime.UTC`；冇 3.12 嘅 `itertools.batched`／PEP 695 `type`；亦冇用 3.12 已移除嘅 `distutils`／`imp`／`asyncore` 同已移除嘅 `unittest` 舊 alias（`assertEquals` 等），冇 `datetime.utcnow()`。`msvcrt`／`fcntl` 喺 [run_daily.py](../pipelines/run_daily.py) 由 `os.name == "nt"` 分支包住，Linux 行 `fcntl` 嗰邊。

> **Amazon Linux 2023**：`/usr/bin/python3` 係 3.9，唔夠。`sudo dnf install -y python3.12`，然後設 `CARDZ_PYTHON=/usr/bin/python3.12`。**唔准改系統 `python3` symlink。**

### 2.4 Node / npm（★ 每日鏈必需，唔再係可選）

```bash
curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
sudo apt-get install -y nodejs
node --version    # 目標 v24.x（WSL 驗證機 v24.13.1）
sudo npm install -g npm@11.8.0
npm --version     # 11.8.0（package.json packageManager 指定）

command -v node npm    # ★ 必須係 /usr/bin/node 同 /usr/bin/npm
```

> **必須系統級安裝，唔准用 nvm。** 每日 unit 開咗 publish（[§6.2](#62-cardz-market-cap-dailyservice)）之後，鏈尾會行 `npm run build --workspace @cardz/market-data`（tsc → `packages/market-data/dist`）同 `node pipelines/publish-snapshot.mjs`。systemd 個預設 `PATH` 係 `/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`，而 nvm 裝嘅 node 喺 `$HOME/.nvm/` 底下，`ProtectHome=true` 之下連睇都睇唔到。
>
> [run-cardz-daily.sh](../deploy/systemd/run-cardz-daily.sh) 有前置檢查：publish 模式下 `command -v npm` 唔中就即刻 exit 1，唔會等燒完 2–2.5 鐘 GemRate quota 先死喺最後一步。
>
> `npm install`（裝 `node_modules`）要喺部署時做，見 [§3](#3-repo-部署)。每日 run 只跑 `npm run build`，唔使網絡。

### 2.5 Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker cardz
sudo systemctl enable --now docker
docker compose version
```

### 2.6 Git + Git LFS

```bash
sudo apt-get install -y git git-lfs
git lfs install --skip-repo
git lfs version    # ★ clone 之前一定要見到版本號
```

> **★ 一定要喺 `git clone` 之前裝好。** [.gitattributes](../.gitattributes) 將 `*.webp` 交俾 LFS filter（`filter=lfs diff=lfs merge=lfs -text`）。冇 git-lfs 就 clone，`data/public/market-assets/` 嗰 576 個 `.webp` 全部係 ~131 bytes 嘅 pointer 文字檔，唔係圖——Pillow 開嗰陣先爆，而且錯誤訊息完全唔會指向 LFS。
>
> 補救：`sudo apt-get install -y git-lfs && git lfs install --skip-repo && sudo -u cardz git lfs pull`。
>
> **唔好攞 WSL 驗證環境當證據。** WSL 個 repo 冇裝 git-lfs（`command -v git-lfs` 空），但 576 個 webp **全部係真 bytes**（實測 `find data/public/market-assets -name '*.webp' -size -1k | wc -l` = 0），因為佢係由 Windows working tree 複製過去，唔係 clean clone。即係話 WSL 行得通**唔代表** clean clone 行得通——呢一項只可以喺真 server 上面 clone 完先驗到。

---

## 3. Repo 部署

```bash
sudo -u cardz git clone <repo-url> /opt/cardz-market-cap
cd /opt/cardz-market-cap

# ★ 全量 LFS pull，唔可以再淨係 --include bootstrap archive：
#   publish 鏈要讀真圖（verify_images → quarantine → publish-snapshot 逐個 copy）
sudo -u cardz git lfs pull
# pointer 殘留檢查：一定要係 0，唔係就代表 git-lfs 冇裝好（見 §2.6）
find data/public/market-assets -name '*.webp' -size -1k | wc -l

# 建 venv
sudo -u cardz python3 -m venv .venv-backend
sudo -u cardz .venv-backend/bin/pip install --upgrade pip
sudo -u cardz .venv-backend/bin/pip install -r pipelines/requirements.txt

# ★ 兩步走，唔准合埋一句 `playwright install --with-deps chromium`
sudo .venv-backend/bin/python -m playwright install-deps chromium   # root 身分裝 OS 共享庫
sudo -u cardz .venv-backend/bin/python -m playwright install chromium

# publish 鏈要 node_modules（每日 run 只跑 build，唔會再上網）
sudo -u cardz npm ci
sudo -u cardz npm run build --workspace @cardz/market-data

# clean-clone gate
sudo -u cardz .venv-backend/bin/python scripts/verify_handoff.py \
  --require-archive --require-tracked --verify-archive
```

> **`--with-deps` 會 hang，唔准用。** `playwright install --with-deps` 內部自己 shell out 去 `sudo apt-get install`。喺 `sudo -u cardz` 底下跑（`cardz` 個 shell 係 `/usr/sbin/nologin`、冇 tty）嗰個內層 `sudo` 攞唔到密碼提示，成個命令就吊死喺度，唔會 timeout 亦冇錯誤訊息。拆成上面兩步：OS 依賴用 root 裝一次，瀏覽器本體用 `cardz` 身分裝（要落喺 `cardz` 個 `$HOME/.cache/ms-playwright`，systemd 之後要讀返）。
>
> Playwright 自帶 Chromium 就係 GemRate keyless 抓取繞 Cloudflare 嘅路，唔係可選件。要改用系統 Chromium 見 [§8.4](#84-路徑分隔符--實測結果)。

> **✅ `curl_cffi` 已補入 requirements.txt（2026-07-26），唔再需要手動補裝。** 三個 import 位全部係硬依賴，冇 try/except fallback：[ebay_brute_harvest.py](../pipelines/ebay_brute_harvest.py) 同 [gemrate_brute_harvest.py](../pipelines/gemrate_brute_harvest.py) 喺 module 頂層 `from curl_cffi import requests as cr`（一 import 個 module 就即刻 ImportError），[native_image_resolver.py](../pipelines/native_image_resolver.py) 喺 `_download()` 入面 import（每日鏈經 `ensure_std_card_images.py` 會行到）。現時 pin `curl_cffi==0.15.0`，對齊本機 venv 實裝版本，亦跟返 requirements 現有嘅全 `==` 釘版風格。
>
> 順帶做咗一次完整對照：`pipelines/**/*.py` 全部 top-level import 入面，第三方只得 `Crypto`(pycryptodome)／`PIL`(Pillow)／`curl_cffi`／`playwright`／`pymysql`(PyMySQL)／`requests`／`zstandard`，除 `curl_cffi` 外全部本身已釘；`scripts/*.py` 只多用 `pymysql`，一樣已覆蓋。冇 `importlib.import_module` 之類嘅動態第三方 import。`cryptography` 唔喺 import 清單但要保留——PyMySQL 做 `caching_sha2_password` 認證要佢。

**唔好複製舊機 `.venv-backend/`**：`pyvenv.cfg` 指向 Windows Python 安裝目錄，Linux 上完全無效，必須重建。

---

## 4. DB 遷移

### 4.1 現行配置

出處 [compose.backend.yaml](../compose.backend.yaml)：

| 項目 | 值 |
|------|-----|
| Image | `mysql:8.4` |
| Container | `cardz-market-cap-db-1` |
| Volume | `cardz-market-cap_cardz_mysql` → `/var/lib/mysql` |
| Port | `127.0.0.1:3308` → `3306`（**只綁 loopback，唔准改 `0.0.0.0`**） |
| Charset | `utf8mb4` / `utf8mb4_unicode_ci`，容器 `TZ: UTC` |
| DB / user | `cardz_market_cap` / `cardz` |

Schema 由 [pipelines/migrations/](../pipelines/migrations/) 十個 immutable 檔按序套用（`001_canonical_card_catalog` → `010_provider_identity_alias`），執行者 `backend.py bootstrap`。[scripts/seed_restore.py](../scripts/seed_restore.py) 對 migration 檔做 SHA-256 contract 檢查，**migration 檔遷移途中唔准改**。

### 4.2 路線 A：由 LFS bootstrap archive 重建（乾淨）

```bash
cd /opt/cardz-market-cap
sudo -u cardz .venv-backend/bin/python scripts/backend.py bootstrap \
  --bootstrap-archive data/private/cardz-active-bootstrap.tar.gz
sudo -u cardz .venv-backend/bin/python scripts/backend.py status
```

範圍：當前 universe lock + provider worklist + 解析得到嗰批 canonical observation。**唔包**歷史 snapshot 逐日行。

### 4.3 路線 B：mysqldump 全量搬（保留 point-in-time 歷史，建議）

**先決條件：舊機當刻冇 daily run 行緊**（`data/runtime/locks/daily.lock` 冇被佔）。

舊機（Windows PowerShell）匯出——密碼由 container 自身 env 取，唔會出現喺命令行或 log：

```powershell
docker exec cardz-market-cap-db-1 sh -c 'mysqldump -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" --single-transaction --quick --no-tablespaces --default-character-set=utf8mb4 "$MYSQL_DATABASE" > /tmp/cardz.sql'
docker cp cardz-market-cap-db-1:/tmp/cardz.sql .\cardz_20260726.sql
docker exec cardz-market-cap-db-1 rm -f /tmp/cardz.sql
```

> 用 `docker cp` 唔用 PowerShell pipe，避開 PS 重寫 encoding／newline 整爛 dump。`--no-tablespaces` 因為 `cardz` user 冇 `PROCESS` 權限。

新機還原：

```bash
cd /opt/cardz-market-cap
# ★ backend.env 必須喺 compose up 之前放好，見 §4.4
sudo -u cardz docker compose -f compose.backend.yaml \
  --env-file data/runtime/config/backend.env up -d db
sleep 20
docker exec -i cardz-market-cap-db-1 sh -c 'exec mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"' < cardz_20260726.sql
```

還原後對數（唯讀，同 [§1.3](#13-遷移前狀態快照舊機) baseline 逐項比）：

```bash
docker exec cardz-market-cap-db-1 sh -c 'exec mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" -e "
SELECT index_code, MAX(effective_date) AS latest, COUNT(*) AS rows_total FROM market_index_snapshot GROUP BY index_code;
SELECT COUNT(*) AS obs_total, MAX(observed_date) AS latest_obs FROM market_price_observation;"'
```

三個 `index_code`（`tcg-combined` / `pokemon` / `one-piece`）嘅 `latest` 同 `rows_total` 必須同舊機一致。

### 4.4 ★ 密碼／volume 配對陷阱

[scripts/backend.py](../scripts/backend.py) `runtime_config()`：local 模式下若 `data/runtime/config/backend.env` 冇 `CARDZ_DB_PASSWORD` / `CARDZ_DB_ROOT_PASSWORD`，佢會**自動 `secrets.token_urlsafe(36)` 生成並寫入該檔**；compose 之後用同一個檔初始化 MySQL volume。

硬順序：

1. **沿用舊密碼** → **先**放 `backend.env` 落 `data/runtime/config/`，**之後**先 `docker compose up`
2. **用新密碼** → 兩樣都唔好預先放，等 `backend.py` 自己生成；dump 還原照樣得（用 container env）

**唔准做**：volume 已用密碼 X 初始化，之後先覆蓋一個寫住密碼 Y 嘅 `backend.env` → `Access denied`，而且錯誤訊息唔會指返呢個成因。

### 4.5 必搬嘅 runtime 數據

`data/runtime/` 全個目錄喺 [.gitignore](../.gitignore) 第 25 行，clone 出嚟係空嘅。2.6 GB 入面**要搬嘅唔夠 50 MB**。

| 路徑 | 大小 | 判定 |
|------|------|------|
| `data/runtime/private-source-map/` | 6.5 MB | 🔴 **必搬**。`active-universe.json` + `active-universe-locks/*.json` 係 immutable frozen universe lock（350 卡邊界），[pipelines/active_universe.py](../pipelines/active_universe.py) `load_active_universe_lock()` 讀佢。**重新 freeze 會產生唔同 lock id**，令 tracked-universe 一對一完整性 gate 失效。同目錄仲有 `source-crosswalk.json`、`tracked-universe.json`、`tracked-gemrate-ids.txt`、`tracked-snk-ids.txt`、`discovery-radar.json` |
| `data/runtime/config/backend.env` | <1 KB | 🔴 睇 [§4.4](#44--密碼volume-配對陷阱) |
| `data/runtime/private-landing/` | 519 MB | 🟡 建議搬。已 import 入 DB，唔搬唔會斷鏈，但 replay 證據冇咗。頻寬緊就只搬最近 3 個 `sources_*` |
| `data/private/gemrate/`、`snk/`、`snkrdunk_brute/` | 160 MB | 🟡 可重爬，但要幾個鐘 + 撞 rate limit |
| `data/runtime/private-fx/latest.json` | 897 B | ⚪ 唔使，[pipelines/fx_rates.py](../pipelines/fx_rates.py) 自動重攞 |
| `data/runtime/locks/daily.lock` | — | ⚪ **唔好搬** |
| `data/runtime/logs/`、`alerts/`、`private-reports/`、`audits/` | — | ⚪ 每次 run 重出 |
| `data/runtime/node_modules.npmmirror/`、`node_modules.override/`、`npm-cache/` | 1.6 GB | ⚪ **絕對唔好搬** |
| `data/runtime/wrangler-*/`、`private-quarantine/` | 350 MB+ | ⚪ 唔參與每日鏈 |

打包（舊機）：

```powershell
tar -czf cardz-runtime-essential.tar.gz data/runtime/private-source-map data/runtime/config
```

新機：

```bash
sudo -u cardz tar -xzf cardz-runtime-essential.tar.gz -C /opt/cardz-market-cap
sudo -u cardz test -f /opt/cardz-market-cap/data/runtime/private-source-map/active-universe.json && echo "universe lock OK"
```

---

## 5. 憑證同 config

### 5.1 檔案權限（Windows ACL → Linux mode）

[deploy/windows/install_daily_task.ps1](../deploy/windows/install_daily_task.ps1) 嘅 `Assert-PrivateEnvironmentFile` 喺 Windows 用 `Get-Acl` 檢查有冇 `Everyone` / `Authenticated Users` / `\Users` 攞到 `Read|FullControl|Modify`，有就 throw。

Linux 等價物**已經實作咗**喺 [deploy/systemd/run-cardz-daily.sh](../deploy/systemd/run-cardz-daily.sh)：

```bash
permissions="$(stat -c '%a' "$env_file")"
owner="$(stat -c '%u' "$env_file")"
if (( (8#$permissions & 8#077) != 0 )) || [[ "$owner" != "0" ]]; then
  echo "CARDZ daily environment file must be root-owned and not group/world-readable" >&2
  exit 1
fi
```

即係 **env 檔必須 `root:root` 而且 mode 唔准有 group/other 任何位**：

```bash
sudo install -o root -g root -m 0600 /dev/null /etc/cardz-market-cap/backend.env
sudo -e /etc/cardz-market-cap/backend.env                    # 貼入 key=value
sudo stat -c '%U:%G %a' /etc/cardz-market-cap/backend.env    # 必須 root:root 600
```

`data/runtime/config/backend.env`（local docker 模式）由 `cardz` 擁有：

```bash
sudo chown cardz:cardz /opt/cardz-market-cap/data/runtime/config/backend.env
sudo chmod 600 /opt/cardz-market-cap/data/runtime/config/backend.env
```

### 5.2 env 檔格式

wrapper 逐行 parse，格式必須 `^[A-Za-z_][A-Za-z0-9_]*=.*$`，`#` 開頭同空行跳過。**唔准有 `export ` 前綴、唔准有引號包住成行、唔准有 CRLF**（見 [§8.6](#86-crlf--行尾)）。

---

## 6. systemd 排程安裝

### 6.1 Shell wrapper 契約

**兩個 wrapper 已經存在於 [deploy/systemd/](../deploy/systemd/)，以下 contract 逐條都已滿足，保留作為改動時的回歸清單——任何一條被改掉都會令 outcome gate 失效。** 契約每條都對應 Windows 側現有行為。

#### `deploy/systemd/run-cardz-daily.sh`

| # | 契約 | 對應 Windows |
|---|------|-------------|
| C1 | shebang `#!/usr/bin/env bash`，`set -uo pipefail`（**唔准 `set -e`**，會令 daily 失敗時跳過 verify） | — |
| C2 | 參數／env：`CARDZ_REPO_ROOT`（預設由 script 位置推）、`CARDZ_ENV_FILE`、`CARDZ_DAILY_MODE`（`staging`\|`production`，預設 `production`）、`CARDZ_PYTHON` | `-RepoRoot -PythonExe -EnvFile -Mode` |
| C3 | 驗 env 檔存在 + `root:root` + mode `& 077 == 0`，唔合即 `exit 1` | `Assert-PrivateEnvironmentFile` |
| C4 | 逐行 parse env 檔，非 `^[A-Za-z_][A-Za-z0-9_]*=` 即 `exit 1`，合法就 `export` | ps1 env parse loop |
| C5 | 驗 `scripts/backend.py` 存在，唔存在即 `exit 1` | ps1 path guard |
| C6 | 揀 python：`$CARDZ_PYTHON` → `.venv-backend/bin/python` → `python3.14…python3.10` → `python3`，並驗 ≥3.10 | ps1 `-PythonExe` |
| C7 | `mkdir -p data/runtime/logs`；log 檔名 **`daily_<mode>_<publish>_<YYYYmmdd_HHMMSS>.log`**（timestamp 用 `date -u`） | ps1 log 命名 |
| C8 | 跑 `"$python" -X utf8 scripts/backend.py daily --mode "$mode"`，stdout+stderr 全部 `>` log 檔，記低 `daily_exit=$?` | ps1 daily 段 |
| C8a | `CARDZ_DAILY_PUBLISH` 決定 C8 加咩 flag：`local` → `--publish --local-only`；`remote` → `--publish`；`off` → 唔加。**其他值即 `exit 1`**，唔准當 `off` 靜靜跳過 | `-Publish` 參數 |
| C8b | publish 模式先做兩件事：（一）`command -v npm` 唔中就即刻 `exit 1`（唔准等燒完 GemRate quota 先死喺鏈尾）；（二）`export npm_config_cache="$repo_root/data/runtime/npm-cache"` 並 `mkdir -p`。**唔准連 `HOME` 一齊改**，Playwright 靠 `$HOME/.cache/ms-playwright` 揾 Chromium | `-Publish` 段 |
| C9 | **無論 C8 成敗都要跑** `"$python" -X utf8 scripts/verify_daily_run.py`，`>>` 追加同一 log，記低 `verify_exit=$?` | ps1 verify 段 |
| C10 | exit code：`daily_exit != 0` → `exit $daily_exit`；否則 `exit $verify_exit` | ps1 結尾兩行 |
| C11 | **唔准用 `exec`**——`exec` 會取代 process，C9 永遠行唔到。[deploy/systemd/run-cardz-daily.sh](../deploy/systemd/run-cardz-daily.sh) 曾經犯過呢個，2026-07-26 已修正（[§8.2](#82--verify-gate-已修正不得改回)），唔准改返 | — |
| C12 | 用 managed DB 時 C8 加 `--external-db` | ps1 註解 |

> **⚠️ C8a 兩邊嘅預設值故意唔同，唔係手民之誤，唔准「順手統一」：**
>
> | | 預設 | 點解 |
> |---|---|---|
> | Linux（權威） | `local` | [cardz-market-cap-daily.service](../deploy/systemd/cardz-market-cap-daily.service) 有 `Environment=CARDZ_DAILY_PUBLISH=local` **明寫**喺 unit 檔。wrapper 嘅 `${CARDZ_DAILY_PUBLISH:-local}` 只係手動跑嗰陣嘅 fallback |
> | Windows（legacy） | `off` | 生產緊嘅 scheduled task `CARDZ-Market-Cap-Daily` 係**舊 action**，唔會傳 `-Publish`（[deploy/windows/install_daily_task.ps1](../deploy/windows/install_daily_task.ps1) `$arguments` 只有 `-RepoRoot -PythonExe -EnvFile -Mode`）。如果 ps1 預設 `local`，改完檔案就等於**靜靜地**令下一個 09:30 JST run 開始真發佈 —— 冇人改過排程、冇人 review 過，publish 就自己著咗。所以 Windows 側 publish 必須 opt-in |
>
> Windows 要開發佈：喺 task action 明寫 `-Publish local`（或 `remote`），唔好改 ps1 嘅 param 預設值。

#### `deploy/systemd/run-cardz-watchdog.sh`

| # | 契約 | 對應 Windows |
|---|------|-------------|
| W1 | 驗 `scripts/verify_daily_run.py` 存在，唔存在即 `exit 1`（**唔好靜靜跳過**） | watchdog ps1 guard |
| W2 | log 檔名 `watchdog_<YYYYmmdd_HHMMSS>.log` 落 `data/runtime/logs/` | ps1 log 命名 |
| W3 | 先寫 header：daily service 上次結果同下次觸發時間，用 `systemctl show cardz-market-cap-daily.service -p ExecMainStatus,ExecMainExitTimestamp` + `systemctl list-timers cardz-market-cap-daily.timer`。**目的係分辨「run 咗但數據唔啱」定「根本冇 run 過」** | ps1 用 `Get-ScheduledTaskInfo` |
| W4 | 跑 `"$python" -X utf8 scripts/verify_daily_run.py --tag watchdog`，`>>` log | ps1 verify 段 |
| W5 | `exit $verify_exit` | ps1 結尾 |

> Watchdog 存在理由（[run-cardz-watchdog.ps1](../deploy/windows/run-cardz-watchdog.ps1) 原註）：verify gate 只喺 daily 行完之後先跑，所以有天生盲點——「部機冇著 / timer 被 disable / run 掛住冇 exit」呢類情況 daily 根本冇 run，即係冇人叫 verify，結果**完全靜音**。Watchdog 喺 daily 之後幾個鐘獨立跑一次，令「乜都冇發生」都會留低 alert 檔 + 非零 exit。

> **BOM 硬規則**：Windows `.ps1` 全部有 UTF-8 BOM（實測 `cardz-status.ps1` / `run-cardz-daily.ps1` / `run-cardz-watchdog.ps1` / `install_daily_task.ps1` 都有）。**Linux shell script 絕對唔准有 BOM**——`#!/usr/bin/env bash` 前面有三個 BOM byte 會令 kernel 認唔到 shebang，報 `bad interpreter` 或者靜靜用錯 shell。現有三個 `.sh`（[deploy/linux/cardz-daily-systemd.sh](../deploy/linux/cardz-daily-systemd.sh)、[deploy/systemd/run-cardz-daily.sh](../deploy/systemd/run-cardz-daily.sh)、[scripts/backend.sh](../scripts/backend.sh)）實測乾淨，新寫嘅要保持。檢查：
> ```bash
> head -c 3 deploy/systemd/run-cardz-daily.sh | od -An -tx1    # 唔可以係 ef bb bf
> ```

### 6.2 `cardz-market-cap-daily.service`

**唔好照抄呢一節去手砌 unit。** 權威版本係 repo 入面嘅 [deploy/systemd/cardz-market-cap-daily.service](../deploy/systemd/cardz-market-cap-daily.service)，由 [deploy/linux/cardz-daily-systemd.sh](../deploy/linux/cardz-daily-systemd.sh) 安裝（[§6.4](#64-安裝)）。呢個文檔以前手抄過一份，之後兩邊各自漂移（`OnFailure` unit 名唔同、`ReadWritePaths` 清單唔同），抄咗嗰份等於裝咗一個通知永遠唔會響嘅 unit。以下只列**唔准改**嘅直令同理由：

| 直令 | 值 | 唔准改嘅理由 |
|------|-----|------------|
| `Type` | `oneshot` | 配 timer 用；`Restart=on-failure` + `RestartSec=10min` 只覆蓋 unit 層面死法 |
| `User` / `Group` | `cardz` | 同 `/opt/cardz-market-cap` 擁有者一致；[§5.1](#51-檔案權限windows-acl--linux-mode) |
| `WorkingDirectory` | `/opt/cardz-market-cap` | wrapper 同 `run_daily.py` 都用相對路徑落 `data/runtime` |
| `EnvironmentFile` | `/etc/cardz-market-cap/backend.env` | `runtime_config(external=True)` **淨係**由 `os.environ` 攞 DB 憑證，冇呢行就一定 `managed database configuration is incomplete` |
| `CARDZ_DAILY_PUBLISH` | `local` | 見下面「發佈模式」 |
| `TimeoutStartSec` | `21600`（6h） | 實測全鏈 2–2.5 鐘；調返 7200 會喺 GemRate 爬到一半殺成棵 process tree，尾段 verify gate 永遠唔會跑 → 靜默死亡、零 alert |
| `OnFailure` | `cardz-market-cap-alert@%n.service` | **名要同 [§6.5](#65-onfailure-通知) 個 template unit 一模一樣**，打錯字 = 靜默 |
| `ProtectSystem` / `ProtectHome` | `strict` / `true` | 收窄寫入面；一收窄就要靠 `ReadWritePaths` 開返門 |
| `ReadWritePaths` | 見下 | 少一個目錄 = `226/NAMESPACE`，喺 `ExecStart` **之前**死，零 log |
| `LANG` / `LC_ALL` / `PYTHONUNBUFFERED` | `C.UTF-8` / `C.UTF-8` / `1` | systemd 唔繼承登入 shell locale（[§2.2](#22-locale必須明確設唔好留空)）；`PYTHONUNBUFFERED` 令 log 即時落地，`TimeoutStartSec` 殺 process 嗰陣唔會連最後一段都冇 |

**發佈模式（2026-07-26 開通）**：`Environment=CARDZ_DAILY_PUBLISH=local`。三個值——

- `local`（預設）：行足 publish 鏈，只寫本機 public tree。唔使任何 R2／canary／pointer env。
- `remote`：再加 R2 上傳同 pointer promotion，`backend.env` 要有齊 [§1.2](#12-憑證清單要準備嘅-key-名) 嗰批 key。
- `off`：舊行為，`run_daily.py --backend-only`，只做 collect + DB sync。

**`ReadWritePaths` 清單**（權威版喺 unit 檔，安裝腳本會 `sed` 出嚟逐個 `install -d`）：

```
integrations/grade10/data   grade10 collector（_state/collector.lock + dump）＋ gemrate --mirror-root
data/runtime                logs / daily.lock / alerts / config / private-fx / private-landing /
                            private-source-map / private-source-runs（snk・tag・ebay）/ candidates /
                            npm-cache（publish 用，見下）
data/private/gemrate        gemrate_source.py daily 輸出
.venv-backend               backend.py ensure_python_environment（pip + marker）
data/public                 ★ publish：market-assets 寫入 + quarantine shutil.move + snapshot promote
manifests                   ★ publish：image-qc.json promote 目標
packages/market-data/dist   ★ publish：npm run build（tsc outDir）
```

打 ★ 嗰三個係開 publish 之後先需要。`data/private`（成個）**唔喺**清單——已逐條 grep 覆核過，daily 只寫 `data/private/gemrate`。

> **`npm` 同 `ProtectSystem=strict` 相撞**：npm 要寫自己個 cache／`_logs`，但 service user 個 home 喺 strict 之下唯讀。[run-cardz-daily.sh](../deploy/systemd/run-cardz-daily.sh) 喺 publish 模式會 `export npm_config_cache="$repo_root/data/runtime/npm-cache"` 並 `mkdir -p`，掉入已經可寫嘅 `data/runtime`。
>
> **唔准順手連 `HOME` 都改去嗰度。** Playwright 喺 `$HOME/.cache/ms-playwright` 揾 Chromium，改咗 `HOME` 會令 GemRate keyless 抓取由頭死起。`cardz` 個 home（`/var/lib/cardz`）唔喺 `/home` 底下，所以 `ProtectHome=true` 影響唔到佢，唯讀已經夠 Playwright 啟動瀏覽器。

**Singleton 語義**：systemd 對同一個 unit 天然唔會並行——service 仲喺 `activating`/`active` 嘅時候，timer 再觸發只會被記錄成 job 已存在然後丟棄。呢個**完全等價** Windows `-MultipleInstances IgnoreNew`，**唔使額外做嘢**。[pipelines/run_daily.py](../pipelines/run_daily.py) 內部嘅 `singleton_lock` 係第二層保險（手動執行時仍然有效）。

### 6.3 `cardz-market-cap-daily.timer` + watchdog units

`/etc/systemd/system/cardz-market-cap-daily.timer`：

> **源頭係 [deploy/systemd/cardz-market-cap-daily.timer](../deploy/systemd/cardz-market-cap-daily.timer)，唔係呢度。**
> 下面呢份係 2026-07-26 對住 unit 檔抄返嚟嘅。有出入一律以 unit 檔為準 ——
> 本文之前將 jitter 寫成零，同出貨嘅 unit 矛盾咗（點解唔准係零，見下面紅框）。
>
> 呢句**故意唔貼返嗰個舊值嘅完整 directive 寫法**：`RandomizedDelaySec` 加個零
> 係一句貼落 unit 檔即刻生效、而且會靜靜咁破壞 stealth 嘅嘢。一份講「唔好咁做」
> 嘅文檔如果同時提供咗一句可以直接 copy 嘅壞設定，就會變成佢自己警告緊嗰個陷阱。

```ini
[Unit]
Description=Run CARDZ Market Cap daily at 00:30-01:00 UTC (09:30-10:00 Asia/Tokyo)

[Timer]
# ★★ 09:30 JST = 00:30 UTC。run_id 用 UTC 日期生成（run_daily.py market_run_id）。
# 主機時區已設 UTC，直接寫 UTC 00:30 即可，唔使搞 timezone 轉換。
# 唔准改返 06:30 Asia/Tokyo —— 嗰個等於 UTC 前一日 21:30，run_id 會落返舊日期，
# collector 見到同名 run_id 就 replay 舊輸出 → 全鏈 exit 0 但零新數據（2026-07-25 事故）。
OnCalendar=*-*-* 00:30:00 UTC
RandomizedDelaySec=1800
Persistent=true
AccuracySec=1min
Unit=cardz-market-cap-daily.service

[Install]
WantedBy=timers.target
```

> ### 🔴 `RandomizedDelaySec=1800` 唔准改返 `0`
> 本文 2026-07-26 之前寫住 `0`。**嗰個唔止係過期數字，係一個會主動破壞 stealth 嘅值** ——
> 照抄就前功盡廢。
>
> 呢條鏈係唯一一條直接向外爬嘅每日排程。準時到秒嘅出擊時間本身就係一個可被對面辨認嘅指紋，
> [docs/HANDOFF.md](HANDOFF.md) §8 第 2 條（G10 stealth）要求爬取時序唔准照抄任何一方嘅固定日程，
> **包括我哋自己嘅**。
>
> **點解封頂喺 1800s（30 分鐘），唔推大啲：**
> 1. **唔准跨 UTC 日。** `00:30 + 30min` = 最遲 `01:00 UTC`，離 24:00 UTC 仲有 23 個鐘，
>    `market_run_id` 嘅 UTC 日對齊照舊成立（即係上面嗰宗 replay 事故嘅根因唔會翻兜）。
> 2. **唔准撞 05:07 UTC watchdog。** 全鏈實測 2–2.5 個鐘，最遲 01:00 開跑 → 最遲 03:30 完，
>    仲有 1.5 個鐘 buffer。jitter 再大就會撞正 watchdog，令個專捉靜默失敗嘅閘反過嚟報假警。
> 3. 對外表現係 09:30–10:00 JST 之間隨機一點，唔再係鐘擺。
>
> **同 `cardz-grade10-discovery.timer` 係一對，唔准分開睇。** 該 timer 2026-07-26 由 23:43 UTC
> 搬去 21:17 UTC、jitter 由 180s 加到 1500s。兩條夾埋令間距由**恆定 47 分鐘**變成每日浮動
> 2h48m–3h43m ——恆定 offset 本身就係指紋。**改任何一邊嘅時間之前，兩個 unit 一齊睇。**

`/etc/systemd/system/cardz-market-cap-watchdog.service`：

```ini
[Unit]
Description=CARDZ Market Cap daily outcome watchdog
After=network-online.target

[Service]
Type=oneshot
User=cardz
Group=cardz
WorkingDirectory=/opt/cardz-market-cap
EnvironmentFile=/etc/cardz-market-cap/backend.env
Environment=CARDZ_REPO_ROOT=/opt/cardz-market-cap
Environment=LANG=C.UTF-8
Environment=LC_ALL=C.UTF-8
ExecStart=/usr/bin/env bash /opt/cardz-market-cap/deploy/systemd/run-cardz-watchdog.sh
TimeoutStartSec=20min
Restart=no
OnFailure=cardz-alert@%n.service
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/cardz-market-cap/data/runtime

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/cardz-market-cap-watchdog.timer`：

```ini
[Unit]
Description=Re-check CARDZ daily outcome hours after the run window

[Timer]
# 14:07 JST = 05:07 UTC。喺 daily（00:30 UTC，最長 6 小時）之後，捉「根本冇 run 過」。
OnCalendar=*-*-* 05:07:00 UTC
Persistent=true
AccuracySec=1min
Unit=cardz-market-cap-watchdog.service

[Install]
WantedBy=timers.target
```

### 6.4 安裝

> 實際部署請優先用 repo 自帶的 installer [deploy/linux/cardz-daily-systemd.sh](../deploy/linux/cardz-daily-systemd.sh)：它會渲染並安裝 `cardz-market-cap-daily.{service,timer}`、`cardz-market-cap-watchdog.{service,timer}` 同 `cardz-market-cap-alert@.service`（共 5 個；alert 係 template unit，唔使亦唔可以 `enable`），同時檢查 `scripts/verify_daily_run.py` 存在（缺少即拒絕安裝）。`cardz-grade10-discovery.{service,timer}` 同 `cardz-market-cap-bootstrap.service` **唔喺呢個 installer 範圍**，要另外手動裝。先跑 `--dry-run` 核對輸出的 `schedule=00:30 UTC (09:30 Asia/Tokyo)`、`watchdogSchedule=05:07 UTC (14:07 Asia/Tokyo)`、`timeoutSeconds=21600` 三項再實裝。下面的手動指令保留作為 installer 不可用時的等價步驟，unit 檔名與 repo 內的檔案完全一致。

```bash
sudo install -m 0644 deploy/systemd/cardz-market-cap-daily.service     /etc/systemd/system/
sudo install -m 0644 deploy/systemd/cardz-market-cap-daily.timer       /etc/systemd/system/
sudo install -m 0644 deploy/systemd/cardz-market-cap-watchdog.service  /etc/systemd/system/
sudo install -m 0644 deploy/systemd/cardz-market-cap-watchdog.timer    /etc/systemd/system/
# ★ 唔好漏咗呢個：daily/watchdog 兩個 unit 嘅 OnFailure= 都指住佢，冇裝就係靜默
sudo install -m 0644 deploy/systemd/cardz-market-cap-alert@.service    /etc/systemd/system/
sudo chmod +x /opt/cardz-market-cap/deploy/systemd/run-cardz-daily.sh \
              /opt/cardz-market-cap/deploy/systemd/run-cardz-watchdog.sh \
              /opt/cardz-market-cap/deploy/systemd/run-cardz-alert.sh

sudo systemctl daemon-reload
sudo systemctl enable --now cardz-market-cap-daily.timer cardz-market-cap-watchdog.timer

# ★ 確認 NEXT 欄：daily 落喺 00:30–01:00 UTC 之間任何一點（jitter 1800s，唔會啱啱好 00:30）、
#   watchdog 準時 05:07 UTC（冇 jitter）。daily 出界或者 watchdog 唔係 05:07 就即刻停手查。
systemctl list-timers 'cardz-*' --all
```

> **`list-timers` 嘅 NEXT 已經計埋 `RandomizedDelaySec`。** daily 見到 `00:47` 呢類數字係
> **正常**，唔係壞咗 —— 每次 `daemon-reload` 都會重新抽一次。見到準時 `00:30:00` 反而要查
> jitter 係咪俾人改返 `0`（點解唔准改見 [§6.3](#63-cardz-market-cap-dailytimer--watchdog-units)）。
> 驗證日期：2026-07-26。

驗 unit 語法（唔會執行）：

```bash
systemd-analyze verify /etc/systemd/system/cardz-market-cap-daily.service
systemd-analyze calendar --iterations=3 '*-*-* 00:30:00 UTC'
```

### 6.5 `OnFailure` 通知

**唔好手砌。** 權威版本係 repo 入面嘅 [deploy/systemd/cardz-market-cap-alert@.service](../deploy/systemd/cardz-market-cap-alert@.service)，由 [deploy/linux/cardz-daily-systemd.sh](../deploy/linux/cardz-daily-systemd.sh) 一齊安裝（唔使 `enable`，`OnFailure=` 自己會拉起）。本文舊版曾經手抄過一份指去 `deploy/linux/notify-failure.sh`——**嗰個檔根本唔存在**，抄咗就等於裝咗一條斷咗嘅通知鏈。

實際鏈：

```
<任何 unit> 失敗
  └─ OnFailure=cardz-market-cap-alert@%n.service
       └─ deploy/systemd/run-cardz-alert.sh <失敗 unit 全名>
            └─ scripts/notify_alert.py --key <daily|watchdog> --write-alert …
                 ├─ 永遠：寫 data/runtime/alerts/<key>_notify_<date>_<stamp>.json
                 ├─ 永遠：print 去 stdout → journal
                 └─ 有 CARDZ_ALERT_WEBHOOK 先：POST 出去（同一 status key
                    每 CARDZ_ALERT_REPEAT_DAYS 日只 push 一次，預設 3）
```

#### ⚠️ 上線前必做：配置 `CARDZ_ALERT_WEBHOOK`

**`OnFailure=` 有設 ≠ 有人收到通知。** 2026-07-26 實測（Windows 側 Machine + User scope 都 NOT SET）：冇 webhook 嘅時候，[scripts/notify_alert.py](../scripts/notify_alert.py) 行 `no CARDZ_ALERT_WEBHOOK configured; alert recorded only`，然後 **return 0**——即係 alert unit 自己 succeed，`systemctl --failed` 都唔會見到佢。呢個窿 Linux 側**一模一樣**，因為兩邊行嘅係同一個 `notify_alert.py`。

冇 webhook 嗰陣，人類實際攞得到嘅只有以下三樣，全部**要人主動去睇**，冇任何嘢會 push：

| 出口 | 點睇 | 有冇 push |
|---|---|---|
| alert 檔 | `ls -t data/runtime/alerts/ \| head` | ❌ |
| journal | `journalctl -u 'cardz-market-cap-alert@*' --since today` | ❌ |
| failed unit | `systemctl --failed`（見到嘅係**死咗嗰個 daily unit**，唔係 alert unit） | ❌ |

冇 `wall`、冇 `mail`、冇 `systemd-cat` 以外嘅任何廣播。所以：

```bash
# /etc/cardz-market-cap/backend.env（root:root 0600），加一行：
CARDZ_ALERT_WEBHOOK=<Slack / Discord / 自架 endpoint 嘅 URL>
# 可選，同一故障重複 push 嘅間隔（日），預設 3
CARDZ_ALERT_REPEAT_DAYS=3
```

改完 `systemctl daemon-reload` 唔夠——`EnvironmentFile=` 係每次啟動先讀，所以下一次觸發自然攞到新值。落完之後**即場自測**（唔會污染 dedupe state，`--self-test` 用 `persist=False`）：

```bash
sudo -u cardz /opt/cardz-market-cap/.venv-backend/bin/python -X utf8 \
  /opt/cardz-market-cap/scripts/notify_alert.py --self-test
```

見到 `[notify] delivered (...)` 先算配置成功；見到 `no CARDZ_ALERT_WEBHOOK configured` = 個 env 冇入到 unit 環境，唔好當佢裝好咗。

> 語義唔好搞亂：**alert 檔每次失敗必寫、唔受 throttle**；被 throttle 擋住嘅淨係 webhook push。所以 `data/runtime/alerts/` 空 = 真係冇失敗過；有檔但冇收到 push = webhook 未設或者被 dedupe 擋住，睇 `data/runtime/notify/state.json` 嘅 `lastNotifiedAt` 分辨（`null` = 從來未成功送出過）。

> **`Restart=` 唔好用喺呢兩個 oneshot unit**。自動重試等於重入 pipeline：撞 `singleton_lock`（`RuntimeError: another CARDZ daily pipeline run is active`）或者製造重複 `run_id`。要重試就人手 `systemctl start cardz-market-cap-daily.service`。

### 6.6 cron fallback

只有喺**唔用 systemd**（容器內、精簡發行版）先用。cron 冇 timezone 欄位、冇 sandbox、冇 journal。

```cron
# /etc/cron.d/cardz-market-cap
# run_id 用 UTC 日期：呢兩個時間必須係 UTC。系統時區唔係 UTC 就唔准用 cron。
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
LANG=C.UTF-8
CRON_TZ=UTC
CARDZ_REPO_ROOT=/opt/cardz-market-cap
CARDZ_ENV_FILE=/etc/cardz-market-cap/backend.env
CARDZ_DAILY_MODE=production

30 0 * * * cardz /usr/bin/flock -n /opt/cardz-market-cap/data/runtime/locks/cron-daily.lock /opt/cardz-market-cap/deploy/systemd/run-cardz-daily.sh
 7 5 * * * cardz /usr/bin/flock -n /opt/cardz-market-cap/data/runtime/locks/cron-watchdog.lock /opt/cardz-market-cap/deploy/systemd/run-cardz-watchdog.sh
```

- `CRON_TZ=UTC` 只有 Vixie/cronie 支援；其他實作必須將**系統**時區設 UTC
- `flock -n` 代替 systemd 嘅單例語義（cron 冇呢個保證）
- cron 冇 `TimeoutStartSec` 等價物；要硬限時就 `timeout 6h <script>`
- cron 冇 `OnFailure`；wrapper 要自己喺非零 exit 時發通知

---

## 7. 驗證

### 7.1 部署即時驗證（未跑 daily 之前）

```bash
cd /opt/cardz-market-cap
sudo -u cardz .venv-backend/bin/python scripts/verify_handoff.py --require-archive --require-tracked --verify-archive
sudo -u cardz .venv-backend/bin/python scripts/backend.py registry --json
sudo -u cardz .venv-backend/bin/python scripts/backend.py status --json
sudo -u cardz .venv-backend/bin/python scripts/backend.py explain market_cap

# 唯讀跑一次 verify，對比舊機 baseline
sudo -u cardz .venv-backend/bin/python -X utf8 scripts/verify_daily_run.py --no-alert
```

單元測試（唔掂 DB）：

```bash
sudo -u cardz .venv-backend/bin/python -m pytest tests/test_verify_daily_run.py tests/test_daily_scheduler_contract.py -q
```

### 7.2 `scripts/verify_daily_run.py`——判成敗嘅唯一標準

**唔准用 exit 0 或者 log 冇 traceback 當成功。** daily 鏈有三個已知靜默失敗位（collector replay、`INSERT IGNORE` no-op、`status` 只驗 integrity 唔驗 freshness）全部都 exit 0。

CLI（實測自 [scripts/verify_daily_run.py](../scripts/verify_daily_run.py)）：

| Flag | 預設 | 說明 |
|------|------|------|
| `--expected-date` | **UTC 今日** | run 必須產出嘅日期，同 `run_id` 語義一致 |
| `--tag` | `daily` | 寫入 alert 檔名／payload 嘅 caller 標籤（`daily` \| `watchdog`） |
| `--no-alert` | off | 只報告唔寫 alert 檔。**唯讀狀態檢查專用** |

Exit code：

| Code | 意思 | 處理 |
|------|------|------|
| `0` | 全部 check pass | 冇嘢做 |
| `1` | 數據 check failed（行完但今日冇新數據落地） | 讀 `data/runtime/alerts/daily_verify_<date>_<ts>.json` |
| `2` | 無法驗證（DB 連唔到 / config 缺） | 查 MySQL container 同 `backend.env` |

四項 check：

| Check | 判定 |
|-------|------|
| `price_freshness` | `MAX(observed_date) FROM market_price_observation` ≥ expected date |
| `snapshot_freshness` | `tcg-combined` / `pokemon` / `one-piece`（version `psa10-v3-complete`）三個都有 ≥ expected date 嘅 snapshot |
| `source_coverage` | `gemrate` 必須有；`snk_psa10` 或 `snkrdunk` 至少一個有 |
| `constituent_sanity` | 成份卡數跌超過 20% → WARN（唔 fail） |

### 7.3 `cardz-status.sh` 等價物

對應 [deploy/windows/cardz-status.ps1](../deploy/windows/cardz-status.ps1)（純唯讀，用 `--no-alert`，可以隨時重複執行唔會污染 soak 記錄）。Linux 版契約：

```bash
#!/usr/bin/env bash
# 三段輸出，同 Windows 版逐段對應
set -uo pipefail
root="${CARDZ_REPO_ROOT:-/opt/cardz-market-cap}"
python="${CARDZ_PYTHON:-$root/.venv-backend/bin/python}"

echo "=== 排程 ==="
systemctl list-timers 'cardz-*' --all --no-pager
for u in cardz-market-cap-daily.service cardz-market-cap-watchdog.service; do
  systemctl show "$u" -p ActiveState -p ExecMainStatus -p ExecMainExitTimestamp --value \
    | paste -sd' ' - | sed "s|^|  $u  |"
done

echo; echo "=== 數據新鮮度（唯讀，唔寫 alert）==="
"$python" -X utf8 "$root/scripts/verify_daily_run.py" --no-alert ${1:+--expected-date "$1"}

echo; echo "=== Alert 檔 ==="
shopt -s nullglob
alerts=("$root"/data/runtime/alerts/*.json)
if (( ${#alerts[@]} == 0 )); then
  echo "  冇 alert（好）"
else
  printf '  ⚠ %d 個未清 alert：\n' "${#alerts[@]}"
  ls -1t "${alerts[@]}" | head -10 | sed 's|^|    |'
fi
```

`ExecMainStatus` 對應 Windows `LastTaskResult`：`0` = OK，`1` = FAIL（數據），`2` = FAIL（無法驗證）。`ActiveState=activating` 即係而家行緊。

### 7.4 首次自動 run 應該見到咩

```bash
systemctl status cardz-market-cap-daily.service
journalctl -u cardz-market-cap-daily.service --since today --no-pager
tail -60 /opt/cardz-market-cap/data/runtime/logs/daily_production_local_$(date -u +%Y%m%d)*.log
```

> log 檔名格式係 `daily_<mode>_<publish>_<UTC timestamp>.log`。預設 `CARDZ_DAILY_PUBLISH=local` 出 `daily_production_local_*.log`；見到 `daily_production_off_*.log` 即係 publish 被關咗。

**成功長相（收集側）：**

1. `ExecMainStatus=0`
2. log 見 `[daily] <N> cards, direct=disabled, public-card-page=enabled, mirror=enabled`（[pipelines/gemrate_source.py](../pipelines/gemrate_source.py)）
3. `data/runtime/private-landing/sources/` 出現**新**目錄 `sources_<UTC今日>_<hash>`，報告內 `"replayed"` 唔係 `true`
4. log 尾段四行 `[verify] PASS` + 一行 `"result": "pass"` JSON
5. `data/runtime/alerts/` 冇新檔

**成功長相（發佈側，`CARDZ_DAILY_PUBLISH=local|remote` 先有）：**

6. log 見 catalog shrink gate 冇 raise（gate fail 會直接 `RuntimeError`，promote 前就死，`data/public/seed-snapshot.json` 保持舊版——呢個係**預期行為**，唔好當 bug 去繞）
7. `packages/market-data/dist/` 有今日 mtime（`npm run build --workspace @cardz/market-data`）
8. log 尾有 `publish-snapshot.mjs` 嘅 JSON 單行，`"remotePublished": false`（local 模式應該係 `false`；`remote` 模式先係 `true`）
9. `data/public/seed-snapshot.json` mtime = 今日；`data/runtime/publish-staging/` 有新 generation 目錄
10. `manifests/image-qc.json` mtime = 今日（新卡先會變，冇新卡唔郁係正常）

```bash
stat -c '%y %n' /opt/cardz-market-cap/data/public/seed-snapshot.json \
                /opt/cardz-market-cap/packages/market-data/dist
find /opt/cardz-market-cap/data/public/market-assets -name '*.webp' -size -1k | wc -l   # 必須 0
```

> **第一次開 publish 專屬檢查：** 睇 `journalctl` 有冇 `status=226/NAMESPACE`。呢個 code 代表 systemd 喺 `ExecStart` **之前**就死，**零 log、verify gate 都唔會跑**，成因通常係 `ReadWritePaths=` 列咗一條唔存在嘅路徑（例如未 `install -d` 起 `packages/market-data/dist`）。詳見 [§8.3](#83-readwritepaths-已覆核)。

**唯讀 SQL 對數：**

```bash
docker exec cardz-market-cap-db-1 sh -c 'exec mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" -e "
SELECT id, run_id, index_code, effective_date, constituent_count FROM market_index_snapshot ORDER BY id DESC LIMIT 6;
SELECT observed_date, COUNT(*) FROM market_price_observation GROUP BY observed_date ORDER BY observed_date DESC LIMIT 3;"'
```

`effective_date` 必須有今日 UTC 日期嘅**新行**。

### 7.5 Cutover 驗收門檻

**連續兩個無人值守 daily run 全 PASS**（[docs/SOAK_GUIDE.md](SOAK_GUIDE.md) / [docs/AWS_HANDOFF.md](AWS_HANDOFF.md) 標準）。中途任何一日要人手介入，重新起計。期間舊機**保持行緊**做 fallback，兩邊 snapshot 對數一致先切。

---

## 8. 已知陷阱／Windows ↔ Linux 差異對照表

### 8.1 ★★ 排程時間 — 最高風險

> **現況：timer 已經係 `00:30 UTC`（= 09:30 JST），本節保留作為「不得改回」的完整理由。**
>
> **`run_id` 用 UTC 日期，唔係本地日期。排錯時間會令整條 pipeline 靜默 no-op 而且 exit 0。**

機制（2026-07-25 實證，[docs/HANDOFF.md](HANDOFF.md) §3）：

1. [pipelines/run_daily.py](../pipelines/run_daily.py) `market_run_id = datetime.now(timezone.utc).strftime("sources_%Y%m%d")`
2. 排程設 06:30 JST → 該刻 UTC 係**前一日 21:30** → `run_id` = 尋日
3. SNK collector 見同名 `run_id` 已有輸出 → `"replayed": true` → 唔重爬
4. `market_price_observation` 唔推進 → 最新價日期停滯
5. [pipelines/market_alerts.py](../pipelines/market_alerts.py) `INSERT IGNORE INTO market_index_snapshot`，unique key `(index_code, index_version, effective_date)` → no-op
6. 全鏈 exit 0，觀測數仲有增長（GemRate 用本地日期戳），表面完全正常

| 排程時間 | 對應 UTC | 判定 |
|---------|---------|------|
| **00:30 UTC** | 00:30 同日 | ✅ **本文採用** |
| 09:30 JST | 00:30 同日 | ✅ 等價（主機時區 = JST 時） |
| 06:30 JST | 21:30 **前一日** | ❌ 已證靜默 no-op |
| 00:05 UTC | 00:05 同日 | ⚠ 太貼午夜，`AccuracySec` 抖動可能落前一日 |

Run 全程實測 2–2.5 小時，00:30 UTC 起最遲約 03:00 UTC 完，仍喺同一 UTC 日；`verify` 預設 `--expected-date` 亦係 UTC 今日，三者對齊。Watchdog 排喺 05:07 UTC，即係留咗約兩個鐘水位，唔會撞正 daily 仲跑緊。**主機時區設 UTC 令呢類撕裂由結構上消失。**

`TimeoutStartSec` 同樣係呢條時間線嘅一部分：service 已設 21600 秒（6 小時）。舊值 7200 秒（2 小時）低過實測時長，會喺 GemRate 爬到一半殺成棵 process tree，令 wrapper 尾段個 verify gate 永遠冇機會跑——即係「排錯時間」同「逾時太短」兩個問題會導致同一個結果：靜默死亡、零 alert。**兩個都唔准改回舊值。**

### 8.2 ★ Verify gate 已修正——不得改回

**現況：已修好。** [deploy/systemd/run-cardz-daily.sh](../deploy/systemd/run-cardz-daily.sh) 結尾已改成先跑 daily、再無條件跑 outcome gate，並以兩者的 exit code 合成最終結果：

```bash
set +e
"$python_bin" -X utf8 "$repo_root/scripts/backend.py" daily --external-db --mode "$mode" 2>&1 | tee -a "$log_file"
daily_exit=${PIPESTATUS[0]}
"$python_bin" -X utf8 "$verify_script" 2>&1 | tee -a "$log_file"
verify_exit=${PIPESTATUS[0]}
set -e
if (( daily_exit != 0 )); then exit "$daily_exit"; fi
exit "$verify_exit"
```

**不得改回 `exec` 收尾。** 舊版最後一行是 `exec "$python_bin" ... backend.py daily ...`；`exec` 會用 python 取代整個 shell process，之後任何指令都不會執行，因此 `verify_daily_run.py` 永遠沒有機會跑。結果是整條鏈退回「盲跑 exit 0」——collector replay、`INSERT IGNORE` 落空、`effective_date` 停滯這三類靜默失敗全部照樣回報成功。契約逐條見 [§6.1 C9/C10/C11](#61-shell-wrapper-契約)。

同一理由下，`run-cardz-daily.sh` 也不可以改用 `set -e`：daily 失敗時 shell 會即時中止，gate 同樣跑不到，而 daily 中途死掉正正是最需要留下 alert 的情況。

**Watchdog 是第二層。** 內建 gate 只在 daily 真的執行完之後才跑，因此對「主機沒開機／timer 被 disable／run 掛住不 exit／unit 根本沒被觸發」完全無感。[cardz-market-cap-watchdog.timer](../deploy/systemd/cardz-market-cap-watchdog.timer)（05:07 UTC = 14:07 JST）獨立再跑一次 `verify_daily_run.py --tag watchdog`，先記下 daily unit 與 timer 的狀態以分辨「跑過但數據不對」與「根本沒跑過」，令「甚麼都沒發生」也會留下 alert 檔與 failed unit。watchdog service 刻意不設 `Restart=`：watchdog 失敗本身就是要保留的訊號。

### 8.3 `ReadWritePaths` 已覆核

**2026-07-26 覆核結論：collect 側四條路徑，publish 側三條，合共七條，已全部寫入 shipped unit。** 本節舊版列咗一堆「每日鏈仲會寫」嘅目錄，逐條 grep `pipelines/` + `scripts/` 之後證實大部分係誤判，記錄喺下面免得下次又照抄。

> **⚠️ 本節上半部原本寫住「`--publish` 未開，所以 publish 側目錄唔使加」。同日稍後已經開咗**（`CARDZ_DAILY_PUBLISH=local`，[§6.2](#62-cardz-market-cap-dailyservice)），三條 publish 路徑已經加入 unit。下面第一張表已包含。

collect 側寫入面（一直都喺 `ReadWritePaths=`）：

| 路徑 | 邊個寫 |
|---|---|
| `data/runtime` | logs / `locks/daily.lock` / alerts / config / private-fx / private-landing / private-source-map / private-source-runs（SNK・TAG・eBay 全部落呢度）/ private-reports / candidates |
| `data/private/gemrate` | `gemrate_source.py daily`：`cards/`、`runs/`、`pop_report.json`、`population_history.csv`、`grader_volume.json` |
| `integrations/grade10/data` | `run_service.py collect`（`_state/collector.lock` + dump）＋ `gemrate_source.py --mirror-root` |
| `.venv-backend` | `backend.py ensure_python_environment`（pip + requirements marker） |

publish 側寫入面（`CARDZ_DAILY_PUBLISH=local|remote` 先會行到，`off` 唔會掂）：

| 路徑 | 邊個寫 |
|---|---|
| `data/public` | `canonical_public_snapshot.py` 寫 snapshot JSON；`ensure_std_card_images.py --write` 寫 `market-assets/*.webp`；quarantine 步驟 `shutil.move` 走未被引用嘅 asset |
| `manifests` | `ensure_std_card_images.py` 寫 `image-qc.json`（`stdCanvas` delta 標記） |
| `packages/market-data/dist` | `npm run build --workspace @cardz/market-data`（tsc 輸出） |

> ⚠️ 呢三條係 2026-07-26 開 publish 時**同時**加入 unit 嘅。開 `CARDZ_DAILY_PUBLISH=local|remote` 但 unit 冇呢三條 = `226/NAMESPACE`，零 log。
>
> **installer 會自動跟。** [deploy/linux/cardz-daily-systemd.sh](../deploy/linux/cardz-daily-systemd.sh) 唔係抄一份路徑清單，而係 `sed -n 's/^ReadWritePaths=//p'` 由 unit 檔讀返出嚟，再逐條 `install -d -m 0750 -o cardz -g cardz`。所以將來再改 unit 嘅 `ReadWritePaths=`，installer 自動跟，唔會走音；`--dry-run` 嘅 `readWritePathsMissing=` 就係硬 gate，非空即停手。
>
> 兩個副作用要知：（一）`data/public` 同 `manifests` 係 **tracked** 目錄，clone 之後已經存在，installer 會將佢哋 chown 去 `cardz:cardz` 兼 `chmod 0750`——如果有另一個 uid（例如本機 nginx）要讀呢兩個目錄，要自己另外開權限；（二）`packages/market-data/dist` 係 build 產物，clean clone 冇，**手動裝 unit 就一定要自己 `install -d`**，唔係就 `226/NAMESPACE`。

舊版列錯、查證後確認唔使加：

| 舊版寫住 | 實際情況 |
|---|---|
| `data/private/snk` | 只出現喺 [pipelines/snk_market_data.py](../pipelines/snk_market_data.py) docstring 嘅 usage 例子；`run_daily.py` 傳 `--out` 去 `data/runtime/private-source-runs/<run>/snk-psa10.jsonl` |
| `data/private/snkrdunk_brute` | [pipelines/snkrdunk_discover.py](../pipelines/snkrdunk_discover.py) 有 module-level `mkdir`，但全 repo 冇嘢 import 佢；daily 行嘅係 `snkrdunk_bulk`（`mkdir` 喺 function 入面，target 係 `--out`） |
| `data/private/ebay_brute` | [pipelines/ebay_brute_harvest.py](../pipelines/ebay_brute_harvest.py) 同上，唯一 importer 係 `gemrate_resume_failed.py`（手動工具）。`ebay_sold_data.py` 唔 import 佢，而且成段由 `CARDZ_EBAY_SOLD_ENABLED` gate 住 |
| `data/private/cardz-active-bootstrap` | 由 `bootstrap_archive.py export` 寫；daily 唔會 export，bootstrap unit 只係 **讀** 個 archive |
| `manifests/g10-full-freeze.json` | `grade10_full_freeze.py` 寫，係獨立 full-backfill 路徑，唔喺 daily |

其餘三個 unit 亦已覆核：`cardz-market-cap-watchdog.service`（`data/runtime`）只寫 logs + alerts；`cardz-grade10-discovery.service`（`integrations/grade10/data`、`data/runtime`、`.venv-backend`）寫 collector dump + `private-source-map`；`cardz-market-cap-bootstrap.service`（`data/runtime`、`.venv-backend`）只 restore 去 `private-source-map` + `private-landing`。三個都無缺口。

> `PrivateTmp=true` 已經俾咗可寫 `/tmp`，所以 `tempfile` 預設目標唔使列入白名單。但 `canonical_public_snapshot.atomic_json` 用 `mkstemp(dir=path.parent)`，temp 檔跌喺目標旁邊，所以**目標自己個目錄**一定要喺白名單入面——呢個係最易睇漏嘅一條。
>
> 失敗形態要記住：`ProtectSystem=strict` 下寫唔到唔一定係 `PermissionError`。白名單列咗**唔存在**嘅路徑，systemd 會喺 `ExecStart` 之前就 namespace 砌唔起、`status=226/NAMESPACE` 死，零 log、verify gate 都唔會跑。所以 installer 嘅 `readWritePathsMissing=` 輸出要當硬 gate 睇。

### 8.4 路徑分隔符 — 實測結果

已掃全 repo（`grep -rnE 'C:\\'` + `os.sep` / `ntpath` / `WindowsPath` / `PureWindowsPath`），結論：

| 檢查 | 結果 |
|------|------|
| JSON manifest 存 backslash 相對路徑 | **零**（掃 `manifests/`、`data/public/`） |
| `os.sep` / `ntpath` / `WindowsPath` / `PureWindowsPath` | **零** |
| venv python 路徑 | [scripts/backend.py](../scripts/backend.py) 已經 cross-platform：`VENV_PATH / ("Scripts/python.exe" if os.name == "nt" else "bin/python")` ✅ |
| Python 代碼 hardcode `C:\` | **兩類，只有一類係可執行代碼**（見下） |

**(a) 註解／docstring（Linux 上無害，可順手清）**

[pipelines/gemrate_source.py](../pipelines/gemrate_source.py) 第 41、43 行（模組 docstring）同 2128、2129 行（檔尾註解），內容係 Windows Task Scheduler 註冊範例。
✅ 2026-07-26 已清理：該處原本寫「Daily at **06:45**」，暗示 GemRate 有獨立 Task Scheduler task。實測 Windows 排程**根本冇 `CARDZ-GemRate-Daily` 呢個 task**，GemRate 係由 `run_daily.py` 嘅 `gemrate_daily_command()` 喺 daily 鏈入面叫，所以跟 daily 時間（Linux 00:30 UTC = 09:30 JST；legacy Windows `CARDZ-Market-Cap-Daily` 09:30）。docstring 同檔尾註冊範例已改寫。

**(b) 可執行代碼 — `_CHROME_CANDIDATES`（[pipelines/gemrate_source.py](../pipelines/gemrate_source.py) 第 1313–1321 行）**

```python
_CHROME_CANDIDATES = [
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium", "/usr/bin/chromium-browser",
]
```

**呢個係 cross-platform-safe by design**：`_find_chrome()` 逐個做 `os.path.exists(p)`，Windows 路徑喺 Linux 上直接唔命中，跟住 fall through 去 `shutil.which("google-chrome" / "chromium" / …)`，全部揾唔到就 return `None`，最後 `_launch_chromium()` 退回 Playwright 自帶 Chromium。**唔使改代碼。**

但呢個係 **GemRate keyless transport 嘅 Cloudflare 繞過路徑**（現時 direct API 已 disabled，keyless 就係主線），所以 Linux 機**一定要有一個可用瀏覽器**，二揀一：

```bash
# 選項 1（[§3](#3-repo-部署) 已包）：Playwright 自帶 Chromium，最少依賴
# ★ 兩步走，唔准合埋一句 --with-deps（會喺 nologin 用戶底下 sudo 吊死，見 §3）
sudo .venv-backend/bin/python -m playwright install-deps chromium
sudo -u cardz .venv-backend/bin/python -m playwright install chromium

# 選項 2：裝系統 Chrome/Chromium，並明確 pin
sudo apt-get install -y chromium
# 然後喺 backend.env 加 key：CARDZ_BROWSER_EXECUTABLE=/usr/bin/chromium
```

`CARDZ_BROWSER_EXECUTABLE` 優先於整個候選 list（`_find_chrome()` 第一步就讀佢），係 Linux 上最穩陣嘅寫法。

> ⚠️ **最大未知數，要老實講清楚**：Playwright 安裝（上面兩步）**在 Linux 上一次都未執行過**，GemRate keyless 爬蟲整條路徑亦從未在 Linux 跑過。目前所有 1468 頁的成功紀錄全部來自 Windows。以下三件事全部未經驗證：Playwright 的系統依賴能否在目標發行版裝齊、headless Chromium 在無 GUI 的 systemd service context（`PrivateTmp=true` + `ProtectHome=true`）下能否啟動、以及 Cloudflare 對 Linux 機房 IP 的判斷是否與家用 Windows 出口相同。**遷移前必須先在目標機手動跑一次完整 GemRate 採集並記錄成功率**，不要把它排進第一晚的無人值守 run。這是整份遷移清單中風險最高、證據最少的一項。

**仲有 backslash 路徑嘅只有 Windows-only 檔**，本身唔搬：

- [deploy/windows/cardz-status.ps1](../deploy/windows/cardz-status.ps1) — `.venv-backend\Scripts\python.exe`
- [pipelines/run_daily.ps1](../pipelines/run_daily.ps1) — 預設 `python.exe`
- [deploy/windows/install_daily_task.ps1](../deploy/windows/install_daily_task.ps1) — `Get-Command python.exe`

→ 由 [§6.1](#61-shell-wrapper-契約) 嘅 `.sh` + [§7.3](#73-cardz-statussh-等價物) 取代，一律用 `.venv-backend/bin/python`。

### 8.5 檔名大小寫敏感 — **實測結果：無衝突**

```bash
git ls-files | tr 'A-Z' 'a-z' | sort | uniq -d      # 輸出空 = 冇 case collision
```

實測空輸出，即係 Linux 上唔會出現「兩個只差大小寫嘅檔互相覆蓋」。

**但仍要注意**：Windows 開發時 `import Foo from './bar'` 對住 `Bar.tsx` 會照過，Linux build 會 `Module not found`。搬完做一次完整 build 驗證：

```bash
npm ci --ignore-scripts && npm run build -w @cardz/web
```

### 8.6 CRLF / 行尾

**Repo 內嘅檔已經有保護**：[.gitattributes](../.gitattributes) 第 20–30 行對 `*.sh` `*.bash` `*.service` `*.timer` `*.py` `*.sql` `*.yml` `*.yaml` `*.toml` `Dockerfile` `*.env.example` 全部設 `text eol=lf`，而且第 16–19 行嘅註解已經明寫咗 `bad interpreter: /usr/bin/env bash^M` 同 systemd `ExecStart` 尾巴帶 `\r` 呢兩個死法。**新寫嘅 `deploy/linux/*.sh` 一 commit 就自動受保護，唔使加嘢。**

**剩返嘅風險係 repo 外嘅 env 檔**：`/etc/cardz-market-cap/backend.env` 唔喺 git 入面（`*.env.example` 覆蓋唔到真檔），若由 Windows 編輯過或者用 `scp` 由舊機搬過嚟，就可能帶 CRLF。wrapper 逐行 parse 時 `\r` 會併入值（變成 `CARDZ_DB_PASSWORD=<值>\r`），結果連唔到 DB 而且錯誤訊息完全唔會指返呢度。

```bash
file /etc/cardz-market-cap/backend.env      # 唔可以出現 "with CRLF line terminators"
sudo sed -i 's/\r$//' /etc/cardz-market-cap/backend.env
```

`data/runtime/config/backend.env`（local docker 模式）同樣要驗——佢喺 [.gitignore](../.gitignore) 入面，一樣冇 `.gitattributes` 保護。

### 8.7 Singleton lock — **實測結果：Linux 分支正確**

[pipelines/run_daily.py](../pipelines/run_daily.py) `singleton_lock()`：

```python
if os.name == "nt":
    import msvcrt
    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
else:
    import fcntl
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
```

釋放段（`finally`）同樣有 `fcntl.LOCK_UN` 分支。`LOCK_NB` 令衝突即刻 raise `RuntimeError("another CARDZ daily pipeline run is active")` 而唔會 block。

**Linux 反而更安全**：`flock` 由 kernel 綁 fd，process 死（OOM kill / SIGKILL）自動釋放，冇 Windows 嗰個「stale lock 擋住下一日」風險（[docs/HANDOFF.md](HANDOFF.md) §8 嗰條風險喺 Linux 上自動消解）。鎖檔本身留喺 disk 無害，**唔好搬過去**。

### 8.8 `python -X utf8` 同 locale

Linux 預設已經 UTF-8，`-X utf8` **保留無害**，建議照跟 Windows 寫法保持一致。但 systemd service **唔會繼承登入 shell 嘅 locale**，`LANG` 為空時 Python 會 fallback 到 POSIX/ASCII，處理日文卡名即 `UnicodeEncodeError`。→ [§6.2](#62-cardz-market-cap-dailyservice) 已明寫 `LANG=C.UTF-8` + `LC_ALL=C.UTF-8`。

### 8.9 完整對照表

| 項目 | Windows（現行） | Linux（目標） |
|------|----------------|--------------|
| 排程器 | Task Scheduler | systemd timer（cron fallback） |
| Daily 觸發 | 09:30 JST | `OnCalendar=*-*-* 00:30:00 UTC` + `RandomizedDelaySec=1800`（實際 09:30–10:00 JST） |
| Watchdog 觸發 | 14:07 JST | `OnCalendar=*-*-* 05:07:00 UTC`（冇 jitter） |
| 逾時 | `ExecutionTimeLimit 6h` / `20min` | `TimeoutStartSec=6h` / `20min` |
| 防並行 | `-MultipleInstances IgnoreNew` | systemd 同 unit 天然唔並行（**免配置**） |
| 停機補跑 | `-StartWhenAvailable` | `Persistent=true` |
| 執行權限 | `-RunLevel Limited` | `User=cardz` + `NoNewPrivileges=true` |
| Wrapper | `.ps1`（**要 BOM**） | `.sh`（**唔准 BOM**） |
| 重導向 | `cmd.exe /c ... > log 2>&1` | 直接 `> log 2>&1`（bash 冇 PS 嗰個 stderr-as-error 問題） |
| venv python | `.venv-backend\Scripts\python.exe` | `.venv-backend/bin/python` |
| Env 檔保護 | `Get-Acl` 查 Everyone/Users | `stat -c '%a'` + owner，`root:root 600` |
| 單例鎖 | `msvcrt.locking` | `fcntl.flock`（**已實作**） |
| 狀態查詢 | `Get-ScheduledTaskInfo` → `LastTaskResult` | `systemctl show -p ExecMainStatus` |
| 失敗通知 | 無（靠人睇 alert 檔） | `OnFailure=cardz-market-cap-alert@%n.service`。⚠️ **未設 `CARDZ_ALERT_WEBHOOK` 之前兩邊一樣係「靠人睇檔」**，systemd 只係幫你自動跑埋個 notifier，唔會變出一條 push 渠道，見 [§6.5](#65-onfailure-通知) |
| Log | `data\runtime\logs\daily_*.log` | 同路徑 + `journalctl -u cardz-market-cap-daily` |

### 8.10 尚未解決的 blocker（實測，非推測）

以下 B1–B5 全部來自 2026-07-26 在 Ubuntu 24.04（WSL2，ext4 原生 rootfs，**不是** `/mnt/c`）做 clean clone 加部署時真正撞到的問題，並非紙上推演。B1 與 B2 會令部署即時失敗，遷移步驟必須包含對應動作。

| # | Blocker | 實測證據 | 部署步驟必須補上 |
|---|---------|---------|-----------------|
| B1 | 🔴 **`git-lfs` 要另外安裝** | 未裝 LFS 就 clone，374 個圖片檔全部是 131-byte pointer 文字檔，`PIL` 一打開即 `UnidentifiedImageError` | `sudo apt install git-lfs` → `git lfs install` → `git lfs pull`，**必須排在任何 Python 步驟之前**。⚠️ 見下面「WSL 唔算證據」 |
| B2 | 🔴 **`packages/market-data/dist/` 一定要先 build**，而且 build tool chain 之後每日都要喺度 | 未 build 之前出現 5 條 module-not-found | `npm ci` → `npm run build --workspace @cardz/market-data`。開咗 publish 之後 `run_daily.py` 每日自己再 build 一次，所以 **`npm` 要係 system-wide**（`/usr/bin/npm`），nvm shim 喺 `ProtectHome=true` 下攞唔到 |
| B3 | 🔴 **GemRate keyless 爬蟲在 Linux 一次都未執行過** | `playwright install-deps` + `playwright install chromium` 從未在 Linux 執行；1468 頁的成功紀錄全部來自 Windows | 遷移前先在目標機手動跑一次完整採集並記錄成功率，詳見 [§8.4](#84-路徑分隔符--實測結果) 的警告框。**這是整份清單中風險最高、證據最少的一項** |
| B4 | ✅ **已修（2026-07-26）：`curl_cffi` 已補入 [pipelines/requirements.txt](../pipelines/requirements.txt)** | 原問題：[pipelines/ebay_brute_harvest.py](../pipelines/ebay_brute_harvest.py) 直接 `import curl_cffi`，但 requirements 沒有這個套件 | 已釘 `curl_cffi==0.15.0`，`pip install -r` 就攞齊，**唔再需要手動補裝**。詳見 [§3](#3-repo-部署) 的說明（三個 import 位、pin 理由、完整 import 對照結果） |
| B5 | ✅ **已修正（2026-07-26）**：所有 tracked `.sh` 的 git mode 由 `100644` 改為 `100755` | 修正前 `git ls-files -s -- '*.sh'` 三個檔全部 `100644`；已對 [deploy/linux/cardz-daily-systemd.sh](../deploy/linux/cardz-daily-systemd.sh)、[deploy/systemd/run-cardz-daily.sh](../deploy/systemd/run-cardz-daily.sh)、[scripts/backend.sh](../scripts/backend.sh) 跑 `git update-index --chmod=+x`，現時三個都係 `100755` | 拆彈完成：即使日後改成直接執行（`ExecStart=<script>`）都唔會 `Permission denied`。⚠️ 本機 `core.fileMode=false`（Windows），所以 exec bit **只存在於 git index**，working tree 睇唔到；新加 `.sh` 要記得手動 `git update-index --chmod=+x`，唔會自動繼承。watchdog 那組（`run-cardz-watchdog.sh`、`cardz-market-cap-watchdog.{service,timer}`）**現時仍未 track**，`git add` 之後要即刻補做 chmod |

> **⚠️ WSL 上面 `.webp` 有真 bytes ≠ B1 已解決。** 2026-07-26 覆核：WSL 個 `~/cardz-market-cap` 係由 Windows working tree **`cp` 過去**，唔係 `git clone`，所以 576 個 `data/public/market-assets/*.webp` 全部係真 bytes（`find … -size -1k | wc -l` = 0）—— 呢個結果同 LFS 有冇裝完全無關。同一時間 `command -v git-lfs` 喺 WSL 係**冇**嘅。即係話：**WSL 現況唔可以用嚟證明 clean clone 攞得到真圖**。B1 只可以喺目標機做完 `git clone` + `git lfs pull` 之後，用下面呢條命令當場驗：
>
> ```bash
> find data/public/market-assets -name '*.webp' -size -1k | wc -l   # 必須 0
> ```

**已實測通過、不需再擔心的項目：**

- **systemd 在 WSL 可用**：systemd 255、PID 1 是 systemd、`systemctl list-timers` 回傳真實 timer。`systemctl is-system-running` 回傳 `degraded` 是 WSL 常態（部分 unit 不適用於該環境），**不影響 timer 運作**，不要當成故障去追。
- **MySQL**：實測 8.4.10、DB `cardz_market_cap`、34 個 table、`lower_case_table_names=0`（table 名大小寫敏感，與 Linux filesystem 一致；由 Windows 搬過去不會出現大小寫映射問題）。

### 8.11 其他未解決 gap（不阻擋遷移）

| # | Gap | 影響 |
|---|-----|------|
| G2 | 冇 `.nvmrc`、`package.json` 冇 `engines` | Node 版本只喺 CI 同 [docs/RUNBOOK.md](RUNBOOK.md) 出現，新機易裝錯 major |
| G3 | ✅ **已關閉（2026-07-26）**：daily 排程已經真正發佈 | 原問題：`backend.py daily` 只行 `--backend-only`，後端 run 成功 **≠** 網站更新。而家 unit 帶 `CARDZ_DAILY_PUBLISH=local`，`backend.py daily --publish --local-only` 行足 snapshot → 卡圖自愈 → catalog shrink gate → promote → quarantine → strict verify → `npm run build` → `publish-snapshot.mjs`。要埋 R2 上傳就轉 `remote`（要先備妥 bucket + canary + pointer JSON），詳見 [§6.2](#62-cardz-market-cap-dailyservice) |
| G4 | Coverage audit 同 canonical DB 脫節 | `data-coverage-audit.json` 嘅 `canonicalDb.state="not_queried"`，唔擋遷移 |
| G5 | Alert evaluation 全部 `coverage_status=blocked` | 唔擋 snapshot 寫入（已實證） |
| G6 | ✅ **已關閉（2026-07-26）**：[pipelines/gemrate_source.py](../pipelines/gemrate_source.py) 模組 docstring 同檔尾註冊範例原本寫「Daily at 06:45」，已改寫成「由 `run_daily.py` 帶起，跟 daily 時間 00:30 UTC = 09:30 JST」 | 實測 Windows 排程冇 `CARDZ-GemRate-Daily` task，06:45 個 slot 屬於 `CARDZ-TAG-Daily-Capture`。檔尾 PowerShell 範例已標明 LEGACY／OPTIONAL 並改用不撞車的時間 |
| G7 | Log 冇 rotation | `data/runtime/logs/` 每 run 一檔，建議加 logrotate |
| G8 | `apps/web/NUL` 未 track 且係 Windows 意外產物 | 刪咗，唔好搬 |
| G9 | ❌ **撤回（2026-07-26 實測推翻）**：本行原本說 [data/tag/TAG_GRADER_PULSE.md](../data/tag/TAG_GRADER_PULSE.md) 的「TAG capture 排 06:45」已過時 | 查 Windows 排程實況：`CARDZ-TAG-Daily-Capture` 狀態 Ready、Next 06:45、上次 06:45:01 執行、`LastTaskResult=0`（成功）。**06:45 係真實而且行得通的排程，冇改**。TAG 同時亦喺 `run_daily.py` 內部跑一次（fail-soft + last_good fallback），兩者並存唔衝突。日後要動 TAG 時間，先查排程實況再改文檔 |

---

## 9. Rollback

遷移期間**舊 Windows 主機保持行緊**，做熱備。

### 9.1 立即回退（新機任何一日 verify FAIL 而且查唔到成因）

```bash
# 新機：停排程，唔停 DB（保留現場）
sudo systemctl disable --now cardz-market-cap-daily.timer cardz-market-cap-watchdog.timer
systemctl list-timers 'cardz-*' --all      # 確認冇 NEXT

# 保存現場
sudo -u cardz tar -czf /tmp/cardz-failure-$(date -u +%Y%m%d).tar.gz \
  /opt/cardz-market-cap/data/runtime/logs \
  /opt/cardz-market-cap/data/runtime/alerts
```

舊機（Windows）確認排程仍然生效：

```powershell
Get-ScheduledTask -TaskName 'CARDZ-Market-Cap-Daily','CARDZ-Market-Cap-Daily-Watchdog' |
  Select-Object TaskName, State
# State 必須係 Ready 或 Running
```

> 遷移期間**兩邊都唔准同時寫同一個 DB**。若新機用路線 B 還原咗舊 dump，兩邊係獨立 DB，各自跑冇問題；真正切換時再做一次 dump 對齊。

### 9.2 完全撤回（放棄本次遷移）

```bash
sudo systemctl disable --now cardz-market-cap-daily.timer cardz-market-cap-watchdog.timer
sudo rm -f /etc/systemd/system/cardz-market-cap-daily.service /etc/systemd/system/cardz-market-cap-daily.timer \
           /etc/systemd/system/cardz-market-cap-watchdog.service /etc/systemd/system/cardz-market-cap-watchdog.timer \
           /etc/systemd/system/cardz-alert@.service
sudo systemctl daemon-reload

# DB volume 保留（可能仲要對數）；真係要清先跑：
# sudo -u cardz docker compose -f /opt/cardz-market-cap/compose.backend.yaml down -v

sudo shred -u /etc/cardz-market-cap/backend.env
sudo rm -rf /etc/cardz-market-cap
```

### 9.3 部分回退（DB 還原錯咗，systemd 冇問題）

```bash
sudo systemctl stop cardz-market-cap-daily.timer
cd /opt/cardz-market-cap
sudo -u cardz docker compose -f compose.backend.yaml down -v     # 掉 volume
sudo -u cardz docker compose -f compose.backend.yaml \
  --env-file data/runtime/config/backend.env up -d db
sleep 20
docker exec -i cardz-market-cap-db-1 sh -c 'exec mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"' < cardz_<日期>.sql
sudo -u cardz .venv-backend/bin/python scripts/backend.py status --json
sudo systemctl start cardz-market-cap-daily.timer
```

### 9.4 回退決策點

| 現象 | 動作 |
|------|------|
| 第一次 run `verify` exit `2` | 唔使回退。查 `backend.env` / MySQL container，修完手動 `systemctl start cardz-market-cap-daily.service` |
| 第一次 run `verify` exit `1`，`sources_*` 目錄係新嘅 | 唔使回退。屬 collector 個別來源問題，照 [docs/SOAK_GUIDE.md](SOAK_GUIDE.md) triage |
| `verify` exit `1` 而且報告 `"replayed": true` | 🔴 **排程時間錯**。即刻停 timer，對返 [§8.1](#81--排程時間--最高風險) |
| 連續 2 日 FAIL 而且成因未明 | 執行 [§9.1](#91-立即回退新機任何一日-verify-fail-而且查唔到成因)，舊機繼續頂 |
| systemd `PermissionError` | 唔使回退。放寬 `ReadWritePaths`（[§8.3](#83-readwritepaths-已覆核)）後重跑 |

---

## 附錄：執行順序速查

```
□  1. git add 所有未 track 檔 + commit（★ 尤其 scripts/verify_daily_run.py）  → §1.1
□  2. clean clone 驗證真係攞齊                                                → §1.1
□  3. 記低舊機 baseline（verify --no-alert + status --json + SQL 對數）        → §1.3
□  4. 開 Linux server，timedatectl set-timezone UTC，建 cardz 帳號             → §2.1
□  5. locale 設 C.UTF-8                                                       → §2.2
□  6. 裝 Python 3.12（底線 3.10）/ Git+LFS / Docker / ★ system-wide Node 24 + npm  → §2.3–2.6
□  7. clone + git lfs pull（★ 驗 .webp 冇 pointer）+ venv + pip install -r      → §3
□  8. npm ci + npm run build --workspace @cardz/market-data                    → §3
□  9. playwright install-deps（root）→ playwright install chromium（cardz 身分） → §3
       ★ 唔准用 --with-deps，會 hang
□ 10. 【順序敏感】放 backend.env → 再 docker compose up -d db                  → §4.4
□ 11. 建 DB：路線 A（archive）或 B（mysqldump）                                → §4.2–4.3
□ 12. 搬 data/runtime/private-source-map/                                     → §4.5
□ 13. /etc/cardz-market-cap/backend.env 設 root:root 600，檢查冇 CRLF          → §5, §8.6
□ 14. ★ 落 CARDZ_ALERT_WEBHOOK，跑 notify_alert.py --self-test 見 delivered    → §6.5
       唔做 = 之後所有失敗零通知，冇人會知
□ 15. 對 deploy/systemd/run-cardz-{daily,watchdog}.sh 逐條核 C1–C12 / W1–W5    → §6.1
□ 16. 確認 .sh 冇 BOM、有 +x                                                   → §6.1, §6.4
□ 17. 跑 installer --dry-run，核 readWritePathsMissing= 係空                   → §6.4, §8.3
       （installer 由 unit 檔讀返 ReadWritePaths 再 install -d，七條全自動；
        手動裝 unit 就要自己 install -d，唔係就 226/NAMESPACE 零 log）
□ 18. 裝 5 個 unit（daily/watchdog 各 .service+.timer + alert@），
       daemon-reload，enable --now 兩個 timer（alert@ 唔好 enable）             → §6.4
□ 19. systemctl list-timers 確認 NEXT：daily 喺 00:30–01:00 UTC（jitter）、
       watchdog 準時 05:07 UTC                                                → §6.4
□ 20. 部署即時驗證（verify_handoff / registry / status / verify --no-alert）    → §7.1
□ 21. ★ 手動跑一次 GemRate 完整採集（Linux 上未驗證過，唔好留俾第一晚）        → §8.10 B3
□ 22. 等第一次自動 run，逐項對 §7.4 成功長相（收集側 1–5 + 發佈側 6–10）        → §7.4
□ 23. 連續兩日無人值守全 PASS → 判定完成，舊機先停                             → §7.5
```

---

**相關文檔**：[docs/HANDOFF.md](HANDOFF.md) · [docs/RUNBOOK.md](RUNBOOK.md) · [docs/AWS_HANDOFF.md](AWS_HANDOFF.md) · [docs/SOAK_GUIDE.md](SOAK_GUIDE.md) · [deploy/systemd/README.md](../deploy/systemd/README.md)（**現有內容過時，時間同 verify gate 須按本文修正**）
