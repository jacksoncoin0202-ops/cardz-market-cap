# FE05 → FE04 Rollback

> Owner 硬要求：FE05 出咗街之後，**任何時候**一句命令退得返 FE04。呢份就係嗰句同埋點驗。
> 文件同 code 衝突：**code 贏**（`scripts/fe05_rollback.ps1`），然後修文件。

## FE05 係咩

037 / **FE04** 之上嘅純前端升級（`apps/web` only）：design token + 字階、卡圖 holo/tilt、
scroll reveal + chart draw-in、skeleton / empty state、OG 圖有卡圖。
**唔掂** data pipeline、bake、`seed-snapshot.json`、DB。所以 rollback = 淨係退 `apps/web` 嘅 code，
資料層一個 byte 都唔使郁。

`apps/web/src/lib/product-generation.ts` 嘅 `PRESENTATION` 由 `"FE04"` 升做 `"FE05"`；
`/api/health` 出返呢個字，**所以 rollback 驗得到**。

## 錨點

| | |
|---|---|
| Tag | `fe04-live`（annotated，已 push 上 origin） |
| Commit | `c622d7419e2d84e9c9ffd120bf5f3e60926d0a1b`（2026-08-17 02:20 由 `4bed89a7` 移上嚟：加埋四粒 `fix(fe)` 手機 hotfix——時段 popover 上表頭行／不透明、anchor-based back-nav scroll restore——全部已 live）|
| 內容 | 2026-08-17 02:16 現役 live 嘅 FE04 build（含 hotfix） |
| Live 憑據 | `https://app.cardzmarketcap.com/api/health` → `"presentation":"FE04"` |

**唔准刪呢個 tag。** 冇咗佢就唔知退去邊。

## 一句命令

```powershell
pwsh -NoProfile -File scripts\fe05_rollback.ps1
```

行之前想睇佢會做咩（零改動）：

```powershell
pwsh -NoProfile -File scripts\fe05_rollback.ps1 -DryRun
```

腳本做嘅嘢，順序：

1. **Preflight** — tracked 檔要乾淨（untracked 唔理）；`git fetch origin main`；
   `HEAD` 要等於 `origin/main`（落後就自動 `merge --ff-only`，行前咗／分叉就停）；`fe04-live` 要存在。
   > 判準係 `HEAD` vs `origin/main`，**唔係** branch 名（呢棵樹平時企喺 `release/037-fe04-box`）。
   > 所以 FE05 commit 一日未 push 上 `main`，呢一步就會叫停 —— 呢個係啱嘅：未出街嘅嘢冇嘢好退。
   > 另外呢棵係 worktree，common gitdir 喺舊 checkout；`fetch` / `commit` / `push` 都會寫嗰邊嘅
   > refs 同 objects（一路都係咁），但**唔會**掂舊 checkout 嘅 working tree。
2. **搵 target** — `fe04-live..origin/main` 之間 **subject 以 `fe05(` 開頭** 嘅 commit，由新到舊。
   已經退過嘅會 skip（睇 commit body 嘅 `This reverts commit …` / `fe05-neutralized: …`）。
3. **閘：有冇未覆蓋嘅 `apps/web` commit**（2026-08-17 加）— 枚舉 `fe04-live..origin/main` 之間
   **所有掂過 `apps/web`** 嘅 commit，凡係唔跟 `fe05(` 前綴、又唔係腳本自己造嘅 rollback
   （認 body 嘅 `fe05-rollback-marker:`），一律列出嚟 **abort**。
   冇呢道閘嘅話，手滑漏咗前綴嗰粒會被靜靜跳過，腳本照 exit 0 + push，FE05 就咁留喺 production。
   確認過真係應該咁樣 → `-AcceptCollateral`；想連佢哋一齊退 → `-Method Tree`。
4. **Revert**（預設）— 逐粒 `git revert --no-commit`。撞 conflict → `git revert --abort` +
   `git reset --hard`，工作樹還原，印住叫你轉 `-Method Tree`，exit 1。
5. **Commit** — `git add -u -- apps/web` 之後 `git commit -m 'rollback(fe): restore FE04 presentation from fe04-live [deploy]'`。
   **唔用 `git add -A`、唔用 `git commit -a`**（AGENTS.md 硬規矩 1）。
6. **Post-condition（push 之前）**（2026-08-17 加）— `git diff --quiet fe04-live HEAD -- apps/web`。
   **造咗 commit ≠ 退到**：唔對錨就列出仲有 diff 嘅檔、**唔 push**、exit 1（commit 仲喺本機，收得返）。
   淨係喺 `-AcceptCollateral` 之下先會降級做警告 + 殘留清單。
7. **Push** — `git push origin HEAD:main`。message 含 `[deploy]` → webhook → AWS `git pull` +
   `docker compose up --build`（契約見 [AWS_GITHUB_PULL_DEPLOY.md](../AWS_GITHUB_PULL_DEPLOY.md) 文首個表）。
8. **驗 live** — 每 40 秒抓一次 `/api/health`（browser UA），最多 12 分鐘，等到 `presentation == FE04`。
   等唔到 → exit 2，同時話你知 rollback commit 已經喺 `main` 上面，要去睇 webhook / docker log。

#### 等唔到嗰陣：先分「webhook 冇 fire」定「build 炸咗」（2026-08-17 加）

兩者外觀一模一樣（commit 喺 `main`、live 照舊），但處理完全唔同。**唔好靠估**，
GitHub 側嘅 delivery 記錄一 query 就分到（`repo` scope 已經夠，唔使 `admin:repo_hook`）：

```bash
gh api "repos/jacksoncoin0202-ops/cardz-market-cap/hooks/658470027/deliveries?per_page=8" --jq '.[] | "\(.delivered_at)  \(.status) \(.status_code)"'
```

同 `git reflog show --date=iso-strict-local origin/main` 逐粒對時間（正常係 push 之後 **2 秒內**
就有一次 delivery，1:1）。

| 見到 | 即係 | 做咩 |
|---|---|---|
| 有 delivery、`200` | webhook 收到咗，AWS 側 `git pull` / `next build` 有問題 | 去睇 docker build log（要 AWS 存取） |
| 有 delivery、非 `2xx` | 接收端死咗 | 睇 `spwebhook.funtoken.me`，IT 側 |
| **完全冇 delivery** | GitHub 根本冇派 —— 唔關 code 事，本機點驗都冇用 | 唔好改 code 去「修」佢。等下一粒 `[deploy]` push 帶起（會連埋之前積落嗰啲），仲係冇就升 IT |

`cf-cache-status` 順便睇埋：`DYNAMIC` = origin 真係出緊舊嘢（唔係 Cloudflare 快取），
`HIT` 先至係快取問題。

> 實例：`895f9f76`（`fe05(cjk)`）2026-08-17 `14:28:28Z` push 咗上 `main`，subject 有 `[deploy]`，
> 但 hook `658470027` 由 `13:16:23Z` 之後零 delivery（前 5 粒 push 全部對得返，2 秒內）。
> hook 本身 `active: true`、`last_response 200`。即係 GitHub 側冇派，唔係 build 炸。

### Flags

| Flag | 做咩 |
|---|---|
| `-DryRun` | 只印會行咩，零改動。髒樹都行得（真跑先會 abort）。 |
| `-Method Tree` | Revert 撞 conflict 先用。直接 `git checkout fe04-live -- apps/web` + `git rm` 走 anchor 之後新增嘅檔。 |
| `-AcceptCollateral` | 明示接受第 3 步列出嗰批 commit（Revert 法 = 佢哋會留低；Tree 法 = 佢哋會被抹走）。**唔係例行 flag**：加之前逐粒 `git show` 睇過先。加咗就同時放寬第 6 步嘅嚴格對錨。 |
| `-NoPush` | 本機造 rollback commit，唔推。 |
| `-SkipVerify` | 唔 poll live。 |
| `-Anchor <tag>` | 換錨點（平時唔使）。 |

### Exit codes

| Code | 意思 | 點處理 |
|---|---|---|
| `0` | 做完 / DryRun 完 / 冇嘢要退而且已經對得返錨點 | — |
| `1` | Abort：preflight 唔過、revert conflict、有未覆蓋嘅 `apps/web` commit、post-condition 唔對錨 | **未 push**，睇住訊息修完再跑 |
| `2` | **已經 push 上 `main`**，但 12 分鐘內 live 都冇轉返 FE04 | 生產中途狀態 —— 去睇 webhook / AWS docker build log，唔好當普通 abort |

`scripts/fe05_rollback.sh` 呢個 launcher 自己嘅錯用另一段號碼（`64` = 唔識嘅 flag，
`69` = 搵唔到 pwsh），**唔會**同上面個 `2` 撞。

### Revert vs Tree

- **Revert**（預設）：只回退 `fe05(` 嘅 commit，其他改動原封不動。乾淨，但 FE05 同其他 commit
  改過同一行就會 conflict。
- **Tree**：粗暴但零 conflict —— `apps/web` 整個攞返 `fe04-live` 嗰刻。
  **代價：anchor 之後所有掂過 `apps/web` 嘅非 FE05 改動一齊冇咗。** 腳本會逐粒列出嚟先做，
  唔想跌就 rollback 之後 cherry-pick 返。

### Git Bash / WSL

`scripts/fe05_rollback.sh` 同 flag（`--dry-run` `--no-push` `--skip-verify` `--method Tree`），
但佢**只係 launcher**：搵到 `pwsh` 就轉手畀上面嗰個 ps1（**ps1 先係 canonical**，唔會有兩份會走樣嘅邏輯）；
搵唔到 `pwsh` 就印晒等價嘅手動 git 步驟。

## 點驗真係退咗

1. **Health**（腳本自己會做，人手驗都係呢句）：

   ```powershell
   (Invoke-WebRequest 'https://app.cardzmarketcap.com/api/health' `
     -UserAgent 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
   ).Content | ConvertFrom-Json | Select-Object presentation, build, product
   ```

   要見 `presentation = FE04`。
   > `presentation` 喺 **top level**，唔係 `generation.presentation`；`generation` 係 bake id 字串
   > （例如 `db3308_60c4e06f038cb664`）。

2. **HTML marker** —— FE05 獨有嘅 class 要消失（WS2 落地之後 `card-art` 就係嗰個 marker）：

   ```powershell
   $ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
   $html = (Invoke-WebRequest 'https://app.cardzmarketcap.com/card/<某張卡 id>' -UserAgent $ua).Content
   if ($html -match 'card-art') { 'still FE05' } else { 'FE04 confirmed' }
   ```

   兩樣都要對得上先算退咗。`/api/health` 綠但 HTML 仲見到 `card-art` = container 未換／CDN 未 purge，
   **唔准講「已退返 FE04」**。

3. 順手睇埋 `/` 200、`/box` 200、`/card/does-not-exist` **404**、`/watchlist` **308**。

## 點 roll forward 返 FE05

腳本收尾自己會印埋，內容係：

```bash
git revert --no-commit <rollback commit sha>
git commit -m 'fe05(rollforward): re-apply FE05 presentation [deploy]'
git push origin HEAD:main
```

**subject 一定要以 `fe05(` 開頭。** 咁樣下一次 rollback 見到佢、revert 佢就等於再收起 FE05。
改個名唔跟前綴 = 之後退唔返（已測：rollback → rollforward → rollback 三輪，`apps/web` 每次都返到同
`fe04-live` 一模一樣）。

## 硬規矩：FE05 commit subject 一律 `fe05(` 開頭

```
fe05(tokens): 字階 + space scale + mono dedupe
fe05(card-art): 卡圖 tilt/holo + #1 chip
fe05(og): OG v2 有卡圖
```

腳本靠呢個前綴搵 target。**唔跟 = 嗰粒 revert 唔到**，只可以用 `-Method Tree` 一鑊過退，
連同期嘅非 FE05 改動一齊跌。呢條唔係風格偏好，係 rollback 能唔能夠用嘅前提。

**但由 2026-08-17 起唔再靠自律**：忘記前綴唔會再靜靜過骨 —— 第 3 步會列出嗰粒 commit
然後 abort（exit 1、冇 push），第 6 步再對錨兜多次底。即係「忘記前綴」由
「靜靜假成功、rollback 嗰日先發現」變成「即刻停、話你知邊粒」。

（`git add -A` 一樣係硬規矩：rollback commit 只准帶 revert 出嚟嗰啲檔。）

## 已證明過乜

2026-08-16 喺 throwaway clone、2026-08-17 喺 synthetic repo + 本機 bare origin
（`temp/fe05/fix-WS0/harness.ps1`、`harness2.ps1`）跑齊。全程冇掂真 origin。

| | 結果 |
|---|---|
| Revert：假 `fe05(test): probe` | revert commit 落地、probe 檔消失、`apps/web` 同 `fe04-live` 零 diff |
| 再跑一次（冪等） | skip（讀返 commit body 個 marker），唔造空 commit，exit 0，仍然對錨 |
| Revert 撞 conflict | `revert --abort` + `reset --hard`，工作樹乾淨、HEAD 冇郁、exit 1、印住叫轉 Tree |
| Tree | 列出非 FE05 附帶回退、`git rm` 走新檔、`PRESENTATION` 返 `FE04`、`apps/web` 零 diff |
| Push path（推去 throwaway bare） | `HEAD:main` 推到，bare `main` == `HEAD` |
| rollback → rollforward → rollback | 每輪 `apps/web` 都返到同 `fe04-live` 一模一樣 |
| **5 粒 commit 之中有 1 粒漏咗 `fe05(` 前綴** | **exit 1、冇 push**，列明 `feat(reveal): scroll reveal`。（未加閘之前呢個 case 係 exit 0 + push + FE05 留低喺 production） |
| **`feat(fe05): …` 咁樣命名**（前綴唔喺開頭） | `要處理：0` 之後 **abort exit 1**，唔會再報「冇嘢要 revert」就收工 |
| **roll-forward 用純 `git revert` 做**（subject 變 `Revert "rollback(fe): …"`） | **exit 1、冇 push** —— 唔會被 `This reverts commit` marker 呃到當佢已經退咗 |
| Tree 有非 FE05 附帶損傷、但冇 `-AcceptCollateral` | **exit 1、冇 push**，訊息叫你明寫個 flag |
| `-Method Tree -AcceptCollateral` 收拾上面第一個 case | exit 0、`apps/web` 同 `fe04-live` 零 diff、`PRESENTATION = FE04` |
| **種 bug 睇住 post-condition 紅**（AGENTS.md 規矩 9）：改一個副本令佢只 revert 3 粒之中嘅 1 粒 | `ABORT [post-commit] apps/web 未退返 fe04-live（2 個檔仲有 diff）`、**冇 push**、exit 1；還原真腳本後同一個 case exit 0 + `post-condition 過` |

**未證明**：真 push 去 GitHub `main`、webhook 觸發、live `/api/health` 真係轉返 FE04 ——
呢三樣要真係 rollback 嗰日先驗得到（第 8 步會逐次印時間戳）。
