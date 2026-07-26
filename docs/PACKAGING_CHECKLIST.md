# 打包交付清單

> 呢份係**交付次序**，唔係功能文檔。攞住個 folder 嘅新人由上至下跟一次就得。
> 深入內容各自連出去，唔喺呢度重複。
>
> 最後實測：2026-07-26

---

## 0. 你手上嗰份係邊種？

呢個 repo 有兩條交付路，驗證方法唔同，**唔可以撈亂**：

| 交付路 | 你會見到 | 用邊個閘 |
|---|---|---|
| **A. Git clone**（`git clone <url>` 或 `git clone file://…`） | 有 `.git/` | `verify_handoff.py` + `verify_clean_clone.py` 兩個都跑 |
| **B. Zip / Google Drive**（成個 folder 壓縮傳過去） | 冇 `.git/`（或者 `.git/` 冇用） | 淨係 `verify_handoff.py`；`tracking` 段會自動報 `enforced: false` |

> **點解要分**：`verify_handoff.py` 嘅 `tracking` 段驗嘅係「呢啲檔喺唔喺 `HEAD`」。
> Zip 交付根本冇 commit 呢個概念，所以佢會誠實報 `enforced: false` + 原因，
> **唔會**扮綠燈。B 路真正嘅閘係 `layout`（檔喺唔喺磁碟）同你有冇連 `data/` 一齊壓。

---

## 1. 交付前（喺**來源機**做，即係而家部機）

```powershell
# 1.1 靜態閘：確認 clean clone 攞得齊嘢
python -X utf8 scripts/verify_handoff.py

# 1.2 動態閘：真係 clone 一份出嚟驗（LFS + snapshot + installer 硬 require）
python -X utf8 scripts/verify_clean_clone.py
```

`verify_clean_clone.py` 嘅 exit code 有四種，**唔好見到非 0 就當一律壞咗**：

| exit | 意思 | 要做咩 |
|---|---|---|
| `0` | 齊件兼出得街 | 冇嘢做 |
| `1` | **打包 bug**：clone 缺檔／LFS 未 pull | 要有人 commit 返啲檔先可以交付 |
| `2` | 驗唔到（冇 git／冇 HEAD） | **唔等於冇事**，個 check 根本冇跑過 |
| `3` | 檔齊，但 snapshot 仲係 demo | **預期中**，喺目標機跑 producer 就得（見 §4） |

Zip 交付另加：壓縮之前確認 `data/private/cardz-active-bootstrap.tar.gz` 係**真檔**唔係
LFS pointer（131 bytes 嗰啲）。`verify_handoff.py --verify-archive` 會驗埋 checksum。

---

## 2. 落到目標機：先確認執行環境

目標環境係 **Linux**（[docs/SERVER_MIGRATION.md](SERVER_MIGRATION.md)），唔係 Windows。
全域 CLAUDE.md 嗰條「Windows 為主」規矩**唔適用於呢個 project**。

```bash
git clone <approved-private-repository-url> /opt/cardz-market-cap
cd /opt/cardz-market-cap
git lfs install --local
git lfs pull                       # 唔淨係 bootstrap archive，仲有 360 張 market-assets .webp
python3 scripts/verify_handoff.py
python3 scripts/verify_clean_clone.py
```

> **`git lfs pull` 唔好淨係 `--include` bootstrap archive。**
> `data/public/market-assets/*.webp`（360 張）一樣係 LFS。冇 pull 嘅話佢哋係
> 131 bytes 嘅文字 pointer，`sync-snapshot.mjs` 同 Pillow 開嗰陣先爆，
> 而且錯誤訊息完全唔會指向 LFS。`verify_clean_clone.py` 嘅 `gitLfs` 段就係捉呢樣。

---

## 3. 秘密檔（**永遠唔喺 repo 入面**，要人手放）

| 檔 | 位置 | 誰讀 | 權限 |
|---|---|---|---|
| `backend.env` | `/etc/cardz-market-cap/backend.env` | systemd unit → `scripts/backend.py` | `root:root` `0600` |
| `gemrate.env` | **兩個位，見 §6** | 日鏈同週掃各讀一個 | `root:root` `0600` |

呢兩個檔由目標機嘅 secret manager 派落嚟，**唔准由呢部工作站抄過去，唔准入 Git，
內容唔准出現喺任何 log 或者交付訊息**。

---

## 4. ⚠️ `data/public/seed-snapshot.json` 係 demo 佔位符 — 唔係壞咗

**呢個係落手嘅人第一個會誤判嘅位。**

git 入面嗰份 `data/public/seed-snapshot.json` **永遠**係 demo：`generation.mode = "demo"`、
`productionEligible = false`、帶住一堆 blocker。用 validator 掃佢會報**過千條 error**
（本地化未齊、身分未確認、價格超 48h SLA…）。

**呢個係正常，唔係 producer 壞咗。** 硬規矩係 git 唔准 carry production 數據，
所以佢一定舊、一定唔合格。真 snapshot 要**喺目標機生成**。

### 生成方法（唯一正路）

```bash
python3 -X utf8 pipelines/run_daily.py --mode production
```

佢會：出 candidate → 過圖片閘 → 過縮水閘 → 先至 promote 蓋過
`data/public/seed-snapshot.json`（**本機**）。

**promote 完之後嗰份唔准 commit 返上去。** 一 commit 就毀咗 demo 契約，
而且下一手 clone 落嚟會以為佢係 demo。

### ⚠️ 手動跑 producer 嘅陷阱

```bash
# ❌ 錯：--output 預設就係 data/public/seed-snapshot.json，直接覆寫咗 git 果份 demo
python3 -X utf8 pipelines/canonical_public_snapshot.py

# ✅ 啱：一定要明寫 --output 去第二個位
python3 -X utf8 pipelines/canonical_public_snapshot.py \
  --presentation data/public/seed-snapshot.json \
  --output temp/candidate-snapshot.json \
  --view top300 --production
```

（來源：[canonical_public_snapshot.py](../pipelines/canonical_public_snapshot.py) 個 `main()` 入面
`parser.add_argument("--output", …, default=ROOT / "data/public/seed-snapshot.json")`。
[run_daily.py](../pipelines/run_daily.py) 個 `main()` 砌 `export_command` 嗰陣自己一路都有傳
`--output <candidate>`，所以行 `run_daily.py` 係安全嘅；
**淨係手動直接叫 `canonical_public_snapshot.py` 先有呢個風險**。）

### 點知生成咗未

```bash
python3 -X utf8 scripts/verify_clean_clone.py     # exit 3 = 仲係 demo
```

`snapshotReadiness` 段會印 `mode` / `id` / `effectiveAt` / `blockers`，
同埋直接寫低下一步要跑咩。

---

## 5. 裝排程

### Linux（目標環境）

```bash
sudo bash deploy/linux/cardz-daily-systemd.sh
bash deploy/linux/cardz-status.sh
```

[cardz-daily-systemd.sh](../deploy/linux/cardz-daily-systemd.sh) 個 `require()` helper 同跟住嗰批
`require core …` 行組成 fail-closed 清單，缺任何一個即 `exit 1`，
**一條 timer 都唔會裝到**。`verify_clean_clone.py` 嘅 `installerHardRequires` 段
直接由呢個 shell 檔抽個清單出嚟核，所以 installer 加多一個 require，個閘自動跟到。

Watchdog / freeze 兩組 unit 就相反 —— 缺咗係**靜靜地唔裝**（`install_watchdog=0`），
installer 照樣 exit 0。所以佢哋喺 `verify_handoff.py` 嘅 `REQUIRED_FILES` 入面。

詳情：[deploy/systemd/README.md](../deploy/systemd/README.md)

### Windows（legacy 生產機，未遷之前仲行緊）

```powershell
powershell -NoProfile -File deploy/windows/install_daily_task.ps1 -Action dry-run
```

> **2026-07-26 搬過位**：由 `pipelines/` 搬入 `deploy/windows/`。詳情見 §7。

---

## 6. `gemrate.env` 而家有兩個位（要 PM 揀一個）

### 現況（唔係設計，係長出嚟嘅）

| 邊條鏈 | 讀邊個檔 | 出處 | 缺咗會點 |
|---|---|---|---|
| **日鏈** | `<repo>/data/runtime/config/gemrate.env` | [scripts/backend.py](../scripts/backend.py) 個 `SECRETS_PATH` 常數，用 `setdefault` 餵入 `daily_environment()` | key 冇 export，GemRate 步驟收唔到 population |
| **週掃（凍結）** | `/etc/cardz-market-cap/gemrate.env` | [cardz-gemrate-freeze.service](../deploy/systemd/cardz-gemrate-freeze.service) 個 `EnvironmentFile=-/etc/cardz-market-cap/gemrate.env` 行 | `-` 代表 optional，systemd **唔會報錯**；`run-cardz-gemrate-freeze.sh` 見到 `GEMRATE_API_KEY` 冇 set 先至 exit 2 |

**點解會分岔**：`run-cardz-gemrate-freeze.sh` 直接叫 `pipelines/gemrate_source.py api-dump`，
**冇經 `scripts/backend.py`**，所以攞唔到 `backend.py` 嗰個 `SECRETS_PATH` 注入。
兩條鏈各自搵自己嘅檔，就變成兩個位。

`cardz-market-cap-daily.service` **完全冇** gemrate 嘅 `EnvironmentFile` —— 佢靠 `backend.py` 自己讀。

### 兩個統一方案（**決定權喺 PM**，我唔郁）

| | 方案 A：統一去 `/etc/cardz-market-cap/gemrate.env` | 方案 B：統一去 `<repo>/data/runtime/config/gemrate.env` |
|---|---|---|
| **改咩** | `backend.py` 個 `SECRETS_PATH` 改成先睇 `/etc/…` 再 fallback repo 內 | `cardz-gemrate-freeze.service` 個 `EnvironmentFile=` 改指 repo 內路徑 |
| **好處** | 秘密全部集中喺 `/etc`，同 `backend.env` 一致；repo 目錄可以純唯讀 | 一行 unit 檔搞掂，`backend.py` 唔使郁 |
| **代價** | 要改 Python + 改文檔 + Windows 側冇 `/etc` 要另開 fallback 分支 | 秘密留喺 repo 目錄，`ProtectSystem=strict` 要開多個 `ReadWritePaths`；repo 唔可以純唯讀 |
| **風險** | fallback 邏輯寫錯 = 靜靜雞讀錯檔（唔會報錯，只會冇 population） | repo 目錄 chmod 一鬆，key 就俾同機其他 process 睇到 |
| **驗證方法** | 兩條鏈各跑一次，睇 GemRate observation 表有冇當日新行 | 同左 |

> **我做咗嘅**：只係**寫清楚現況**同兩個選項。**冇改任何一邊**，因為改咗就等於幫 PM
> 做咗決定，而兩個方案嘅代價唔同性質（一個係複雜度，一個係權限面）。

---

## 7. 2026-07-26 呢次搬咗邊啲檔（歸位記錄）

| 檔 | 舊位 | 新位 | 邊個寫 | 邊個讀 | 而家有冇人用 |
|---|---|---|---|---|---|
| `install_daily_task.ps1` | `pipelines/` | `deploy/windows/` | 人手 | 操作員手動跑；`tests/test_daily_scheduler_contract.py` 讀佢嘅字串做合約測試 | **有** — legacy Windows 生產機 `CARDZ-Market-Cap-Daily` 排程 |
| `install_gemrate_task.ps1` | `pipelines/` | `deploy/windows/` | 人手 | 冇人跑 —— 佢**第一行就 `throw`**（已退役，GemRate 併入母 job） | **冇**，保留做「唔好再拆返出嚟」嘅路標 |
| `freeze-sweep-guard.ps1` | `temp/` ⚠️ | `deploy/windows/` | 人手 | Windows Task Scheduler `CARDZ-Freeze-Sweep-Guard` | **有** — 09:20 JST 停週掃讓路俾日鏈 |

**點解要搬**：`temp/` 唔喺 `.gitignore` 入面，但佢係公認嘅垃圾桶。一個**生產排程**
指住 `temp/` 入面嘅腳本 = 隨時有人清 temp 就靜靜雞熄咗個護欄。

**順手修咗**：`freeze-sweep-guard.ps1` 加返 UTF-8 BOM。佢啲註解係中文，冇 BOM 喺
PowerShell 5.1 底下會變亂碼（[docs/SERVER_MIGRATION.md](SERVER_MIGRATION.md) 已經有呢條硬規矩，
只係呢個檔喺 `temp/` 冇被覆蓋到）。

**已接線**：`tests/test_daily_scheduler_contract.py`、`CLAUDE.md`、`PROJECT_STATE.md`、
`docs/RUNBOOK.md`、`docs/SERVER_MIGRATION.md`（6 處）、`pipelines/gemrate_source.py`、
`install_gemrate_task.ps1` 自己個 throw 訊息 —— 全部改晒指新路徑，repo 內已無舊路徑殘留。

---

## 8. 網站（AWS / Node container）

```bash
docker build -f apps/web/Dockerfile -t cardz-web:local .
docker run -d -p 3000:3000 cardz-web:local
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3000/api/health
```

- health check **必須**用 `/api/health`，**唔准**用 `/` —— `/` 就算數據壞晒都照返 200。
- image 入面唔應該有任何 wrangler / workerd binary（Node 路唔應該拖 Cloudflare runtime）。
- 2026-07-26 實測：build exit 0、image 626 MB、container `healthy`、7 個 endpoint 全 200。

詳情：[docs/AWS_DEPLOY.md](AWS_DEPLOY.md)、[docs/AWS_HANDOFF.md](AWS_HANDOFF.md)

---

## 9. 出街前最後一次過閘

```bash
python3 -X utf8 scripts/verify_handoff.py           # 要 PASS
python3 -X utf8 scripts/verify_clean_clone.py       # 要 exit 0（唔係 3）
python3 -X utf8 scripts/verify_daily_run.py         # 唯一 outcome gate
bash deploy/linux/cardz-status.sh
```

**`verify_daily_run.py` 係唯一分得清「行完」同「真係有新數據」嘅閘。**
收集鏈 exit 0 本身唔算驗收證據 —— 2026-07-25 就試過成條鏈 exit 0 但
`effective_date` 完全冇郁。
