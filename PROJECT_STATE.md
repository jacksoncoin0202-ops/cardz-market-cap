# PROJECT_STATE — CARDZ Market Cap

> **單一真相來源。** 任何 agent（Claude Code / Codex / Hermes / Pi / 其他）開工前一定要讀呢份，
> 收工前一定要更新呢份。呢份文係 model-agnostic —— 唔靠任何一個 model 嘅 session 記憶。
>
> 最後更新：**2026-07-26（本機時區）** by Claude Code (Opus 5) —— 文檔真相校正 + TAG/alert 查因
> ＋ 市值/成交 delta 修正（斷點 #5 · #6 收工，見 §4）
> 分支：`ui-experiments-20260725`｜工作區大量未 commit 檔案
>
> ⚠ 呢輪更新推翻咗幾個之前寫錯嘅「事實」，如果你手上有舊版印象，以下四條要覆寫：
> **FX 已接線**（唔係 0 行）· **成交已改讀對嘅表** · **eBay 3,824 行唔係 92 行** ·
> **判定閘一直有響**（quarantine 70,474 / reject 32,738，唔係全部 0）。

---

## 0. 唔好淨係信呢份文 —— 三條命令自己驗

狀態文檔一定會腐爛。`docs/HANDOFF.md` 就係活生生例子：佢寫住「publish 鏈未通、整體 65%」，
但 publish 鏈 07-26 已經打通。**所以呢個 section 排第一** —— 落手做嘢之前，先跑呢三條，
用真實輸出蓋過下面任何文字。

```bash
cd "C:/Users/jackson0202/Documents/Playground/cardz-market-cap"
set -a && . data/runtime/config/backend.env && set +a

python -X utf8 scripts/verify_daily_run.py      # 昨日 daily run 有冇真係出到數
python -X utf8 scripts/audit_wiring_gaps.py     # 有冇「數據喺 DB 但冇人接」嘅孤兒
python -X utf8 scripts/verify_claims.py         # 呢份文同 CLAUDE.md 啲數字仲啱唔啱
python -X utf8 scripts/verify_doc_refs.py       # 文檔指住嘅代碼位仲喺唔喺（唔使 DB）
python -X utf8 -m pytest -q --no-cov            # 全 repo 測試（唔喺 .venv-backend，用系統 python）
```

| 命令 | exit 0 代表 | exit 1 代表 |
|---|---|---|
| `verify_daily_run.py` | 昨日 snapshot 有新行 | 鏈斷咗，睇 `data/runtime/alerts/` |
| `audit_wiring_gaps.py` | 冇孤兒數據 | 有斷點，輸出會逐個列 |
| `verify_claims.py` | 所有帶戳數字仲啱兼未過期 | 有數字漂移（`DRIFT`）或者太耐冇驗（`STALE`）|
| `verify_doc_refs.py` | 文檔冇指住已經冇咗嘅檔、亦冇指住 `temp/` | 有 `BROKEN` 或者 `TEMPDEP`（`DRIFT`/`BARE` 只係嘈，唔會 fail）|

**`verify_claims.py` 係專門醫「讀啱檔但內容爛咗」呢個失效模式。** 帶 `@verified` 戳嘅數字
佢會即場重跑條 SQL 對數。`DRIFT` = 個數變咗要改文；`STALE` = 個數啱但太耐冇人驗。
戳點寫、3 日過期規則、點解揀 3 日 —— 睇 CLAUDE.md「多 agent 協作衛生」。
⚠ 呢個腳本**唔會**幫你改文，佢淨係報告。

**開工前仲要查 [FILE_CLAIMS.md](FILE_CLAIMS.md)** —— 7 個 agent 同時改一個 repo，
落第一個 edit 之前睇下你要改嗰個檔有冇人 claim 咗，收工前刪返自己嗰行。
租約 4 個鐘，過咗就當死 claim。**呢份文同 `CLAUDE.md` 係最容易撞車嗰兩個**。

**驗收 daily run 嘅唯一標準**：`market_index_snapshot` 有當日新行。唔係「腳本 exit 0」，
唔係「log 冇 error」。

⚠ **量表大細一律用 exact `COUNT(*)`，唔准用 `information_schema.TABLE_ROWS`。**
後者係估算值，喺呢個庫會俾你**完全相反**嘅結論（試過因此寫低「15 張表 0 行」，
實際得 6 張）。

⚠ **`market_index_snapshot` 每次 run 寫三行**（全盤 `tcg-combined` + `pokemon` + `one-piece`）。
**唔准用 `MAX(index_snapshot_id)` 當全盤快照** —— 最新 id 係 `one-piece`（31 行）。
2026-07-25 嗰次：id **19** = tcg-combined **255 行**、id 20 = pokemon 224、id 21 = one-piece 31。

---

## 0.5 DB 現況數字（2026-07-26 exact COUNT 實測 —— 唔好再逐個 agent 重數）

每行帶 `@verified` 戳（render 出嚟睇唔到）。**唔好肉眼信呢個表**，
跑 `python -X utf8 scripts/verify_claims.py PROJECT_STATE.md` 即場重量。
`expect>=` 因為呢啲係只升唔跌嘅計數器 —— 用 `expect=` 嘅話聽日入多一行就報假 DRIFT。

| 表 | 行數 | 註 |
|---|---|---|
| `market_source_observation` | 337,052 | 統一入庫接口，2023-06-19 → 07-26 <!--@verified 2026-07-26 id=db.source_obs.rows expect>=337052 sql=SELECT COUNT(*) FROM market_source_observation--> |
| `market_price_observation` | 119,266 | 395 卡 · snk_psa10 115,036 / **ebay 3,824** / snkrdunk 406 <!--@verified 2026-07-26 id=db.price_obs.rows expect>=119266 sql=SELECT COUNT(*) FROM market_price_observation--> |
| ├ 其中 `ebay` | 3,824 | **唔係 92 行** —— 舊文寫錯咗 40 倍，呢粒特別守住 <!--@verified 2026-07-26 id=db.price_obs.ebay expect>=3824 sql=SELECT COUNT(*) FROM market_price_observation WHERE source_code='ebay'--> |
| `market_sale_observation` | 93,063 | <!--@verified 2026-07-26 id=db.sale_obs.rows expect>=93063 sql=SELECT COUNT(*) FROM market_sale_observation--> |
| `market_daily_sales_aggregate` | 12,078 | 395 卡 / 217 日 <!--@verified 2026-07-26 id=db.daily_sales.rows expect>=12078 sql=SELECT COUNT(*) FROM market_daily_sales_aggregate--> |
| `market_grader_population_observation` | 9,538 | 1,590 卡，**得 3 日真量** <!--@verified 2026-07-26 id=db.pop_obs.rows expect>=9538 sql=SELECT COUNT(*) FROM market_grader_population_observation--> |
| `catalog_variant` | 1,705 | `identity_status` 全部 `confirmed` <!--@verified 2026-07-26 id=db.catalog_variant.rows expect>=1705 sql=SELECT COUNT(*) FROM catalog_variant--> |
| `catalog_variant_locale` | 1,200 | 四語故事正源 <!--@verified 2026-07-26 id=db.variant_locale.rows expect>=1200 sql=SELECT COUNT(*) FROM catalog_variant_locale--> |
| `market_image_source_pointer` | 468 | <!--@verified 2026-07-26 id=db.image_pointer.rows expect>=468 sql=SELECT COUNT(*) FROM market_image_source_pointer--> |
| `catalog_story_pointer` | 466 | <!--@verified 2026-07-26 id=db.story_pointer.rows expect>=466 sql=SELECT COUNT(*) FROM catalog_story_pointer--> |
| `market_image_asset` / `market_image_qc` | 456 / 456 | <!--@verified 2026-07-26 id=db.image_asset.rows expect>=456 sql=SELECT COUNT(*) FROM market_image_asset--> |
| `catalog_provider_identity_alias` | 360 | <!--@verified 2026-07-26 id=db.identity_alias.rows expect>=360 sql=SELECT COUNT(*) FROM catalog_provider_identity_alias--> |
| `market_identity_review_queue` | **203** | **全部 pending，冇人裁過**。呢粒用 `expect=` 唔用 `>=` —— 佢**跌**先係好消息（有人開始裁），升亦都要知，任何郁動都想見到 <!--@verified 2026-07-26 id=db.review_queue.pending expect=203 sql=SELECT COUNT(*) FROM market_identity_review_queue--> |
| `market_fx_rate_observation` | 10 | ✅ 已接線 <!--@verified 2026-07-26 id=db.fx.rows expect>=10 sql=SELECT COUNT(*) FROM market_fx_rate_observation--> |

**真係 0 行嘅得 6 張**：`market_tracked_sales_aggregate`（已廢棄）· `catalog_printing_identity` ·
`market_raw_payload_object` · `market_source_observation_payload_pointer` ·
`market_source_effective_observation` · `market_retention_archive_manifest`。

**價格覆蓋率兩個分母**：對 roster 395/1,468（**26.9%**）；對 catalog 395/1,705（**23.2%**）。
舊文寫嘅「硬上限 18.6%」**已作廢**，唔好再引用。

**POP 每日真相**：07-21 641 卡 · 07-22 **2 卡 + TAG 307 卡** · 07-23 **1 卡** ·
07-24 1,216 卡 · 07-25 1,211 卡。即係 gemrate 得 3 日真量，**TAG 一世得 07-22 一日**。

**上線快照覆蓋**（id=19 / 2026-07-25 / 255 卡）：價格 255 · POP 255 · 市值 255 ·
卡圖 **251** · 四語 **236** · delta 1d 255 / 7d **237** / 30d **232** ·
POP delta 7d/30d **0** · sparkline 尾 14 日 **251**。

---

## 1. 而家喺邊

### 🚀 目標變咗（2026-07-26 用戶指令）：**明日交一個可以擺上 AWS 嘅出街版本**

用戶原話：「其實依家你俾我哋個 website 先出到街先，我知道 DB 可能未必齊，
但係我哋出街嗰啲照齊先。即係我哋後面啲線點樣駁得齊啲、點樣自動化，後面再算。
我聽日 deadline，我要交嘢俾人去擺上 AWS server。」

**呢個目標蓋過本文件所有其他優先次序**，包括 §2 GemRate 死線。判斷標準由
「後端駁得幾齊」變成「**訪客開個網站見唔見到爛嘢**」。

**取捨原則（明日之前一律照呢個做）**：
- 冇數嘅欄 → **唔好顯示**，好過顯示 0 / 空白 / `null`。遮醜優先於駁通後端。
- 數據唔齊唔係 blocker；**睇落似未做完**先係 blocker。
- 任何本機已經有嘅資產（卡圖／數據／翻譯）**一律撿返嚟用，唔好重新爬**。

**✅ 呢個風險 07-26 08:05 解決咗。** AWS 路徑已經起好、真跑過、真 curl 過。
交付文件：[docs/AWS_DEPLOY.md](docs/AWS_DEPLOY.md)。一句總結俾第三方：

```bash
docker build -f apps/web/Dockerfile -t cardz-web:latest .   # 喺 repo root 跑
docker run -d -p 3000:3000 cardz-web:latest
curl http://localhost:3000/api/health
```

**唔使 DB、唔使 R2、唔使 Cloudflare。** 數據係 build 時打包入 image
（`data/public/seed-snapshot.json` + `data/public/market-assets/` +
`data/editorial/top100-stories.json` 三個路徑）。runtime image 入面
`wrangler` / `workerd` / `opennext` **一個都冇**（實測 grep 過）。
Cloudflare 路徑原封不動，兩條路並存 —— 見 §6 決策 D8。

### 後端（維持現狀，唔係今日重點）

後端條鏈**已經全自動通**（採集 → 指數 → snapshot → publish）。爭嘅係
**數據覆蓋率**同**前端斷線位**，唔係管道本身。

前端用戶已驗收。i18n 文案 0/192（584/586 error）本來係上線硬 blocker ——
**但依家要重新評估**：如果中文版唔係明日交付範圍，佢就唔再係 blocker。

---

## 2. 硬死線

### 🔴 GemRate key ~2026-07-29 到期（仲有 3 日）

key 死咗之後轉全 keyless scraping。死線前要**凍結曬 roster + POP 落 DB**，
否則每日價格 run 冇 roster 可以跟。

**現況（07:15 實測）**：

| 指標 | 數值 |
|---|---|
| roster 覆蓋 | **598 / 1468 = 40.7%**（population + history 兩個檔都齊先計） |
| 剩低 | 870 張 |
| 最近 15 分鐘完成 | **0 張 —— 完全卡死** |
| 429 次數 | log 入面 **1321 次** |

**✅ 已鎚實嘅斷症（07-26 07:15）**：**`--resume` 完全正常，唔使改。**
用真實 roster 卡複製 [gemrate_source.py:1064-1085](pipelines/gemrate_source.py:1064) 嘅判斷邏輯逐張跑：
598 張全部行 `skip:receipt_verified`，**0 張**會重 fetch；870 張係真係未爬過。

> **⚠️ 呢度曾經寫錯過，唔好再犯同一個錯。**
> 之前寫住「resume 冇生效喺度重 fetch，改成 file-exists 就慳一半時間」——**係錯嘅**。
> 錯喺淨係睇「mtime 有更新 + 13 秒/張」就推論，冇實際跑過個 gate。
> 真相：讀 :1078-1083 就見到，就算 receipt 驗唔過，佢係**重建 receipt 然後 continue**，
> 唔會 fetch。唯一會跌落 fetch 嘅路徑係嗰個 `except`。
> 教訓：**斷症要跑得出數字先算數，唔准靠 mtime 同時間推論。**

**真正根因：GemRate API rate limit。** 寫入時間直方圖講得好白：
1–2 鐘前完成 436 張 → 30–60 分鐘前 64 張 → **最近 30 分鐘 0 張**。
即係一路減速直至撞死喺 429 牆度。`_api_get`（[:122](pipelines/gemrate_source.py:122)）
`retries=4` 就放棄，`--speed medium` = 1.0s 間隔太密。

**已採取行動**：殺咗 PID 96120，用 **`--speed slow`（3.0s）** 重開背景跑，
log 去 `data/runtime/logs/gemrate_freeze_slow.log`。慢但唔會撞牆。

**下一個 agent 要做**：睇 `gemrate_freeze_slow.log` 嘅完成速率。
如果 slow 都係一路 429，就唔係間隔問題，係**日 quota 上限**——嗰陣要改策略
（分日跑 / 縮 roster 優先次序），唔好再調間隔。

**護欄已經裝好**：`CARDZ-Freeze-Sweep-Guard`（Task Scheduler，State=Ready）
09:20 停掃讓路俾 daily，daily 完之後用 `--speed slow` 接返。腳本喺
[deploy/windows/freeze-sweep-guard.ps1](deploy/windows/freeze-sweep-guard.ps1)。

---

## 3. 進行中（邊個做緊）

| 項 | 狀態 | 負責 | 落腳點 |
|---|---|---|---|
| P1 GemRate 凍結掃 | 40.7%，行緊 | 背景進程 PID 96120 + guard 排程 | 見 §2 |
| Linux/WSL 遷移 | systemd unit 寫好、驗過 | Claude | [docs/SERVER_MIGRATION.md](docs/SERVER_MIGRATION.md)、`deploy/systemd/` |
| 新卡 auto-add soak 監察 | runbook 寫好，等 soak Day 1 | Claude | [docs/SOAK_RUNBOOK.md](docs/SOAK_RUNBOOK.md) |
| **AWS 交付** | ✅ **做完，實測過** | Claude (Opus 5) | [docs/AWS_DEPLOY.md](docs/AWS_DEPLOY.md)、[apps/web/Dockerfile](apps/web/Dockerfile)、[.dockerignore](.dockerignore) |

---

## 4. 已完成 —— 唔好重做

> 呢個 section 存在嘅唯一理由：阻止下一個 agent 重新考古。
> 見到下面任何一項，**唔好再查一次**，直接信，除非 §0 嘅命令話你知佢壞咗。

**市值 delta / 成交 delta 修正（2026-07-26，斷點 #5 · #6）**
- **一條價格變動率一直扮緊三個指標。** `rankings.tsx` 嘅 :154（市值）、:158（成交）、
  :160（價格，呢個先啱）全部餵同一個 `windows[w].changePct`。
- **市值** 而家由 producer 出 `windows[w].marketCapChangePct = (1+Δ價)(1+ΔPOP)−1`
  （`compose_change_pct()`）。實測 seed 100 張入面 **14 張箭嘴指錯方向**、100 張全部低估；
  production snapshot 30d 46 張有數入面 **4 張指錯方向**。
- **成交** 由 producer 出 `windows[w].trackedSalesChangePct` = 本窗口成交額 ÷ 前一個
  同長度窗口 − 1（`latest_sales()` 一次過撈兩倍窗口長度，前半入 `prev_*` 欄）。
  成交額同價格變動 % **冇任何數學關係**，唔係精度問題。
- **fail-closed**：任何一個輸入唔係 ready/stale 就出 null，UI 空白。
  **唔准**退返去用 Δ價頂替 —— 頂替就係原本嗰個 bug。
- 新腳本 [scripts/audit_snapshot_deltas.py](scripts/audit_snapshot_deltas.py)（由 `temp/` 歸位）。
  詳情見 [CLAUDE.md](CLAUDE.md) 「市值 delta / 成交 delta」section 嘅四事實表。

**交付整備（2026-07-26）**
- **`scripts/verify_handoff.py` 由假綠燈修成真閘**。舊版用 `git ls-files --error-unmatch`
  驗 **index**，即係 `git add` 咗但未 commit 都報 tracked=true；`git clone` 只攞到 `HEAD`，
  所以改用 `git cat-file -e HEAD:<path>`。REQUIRED_FILES 由 14 擴到 51，
  而家一次過報晒所有 stage（唔喺第一個 failure 就停）。
  **實測 30/51 個部署必需檔喺磁碟有、`HEAD` 冇。**
- **新增 `scripts/verify_clean_clone.py`**（動態閘）：真 `git clone --depth 1 file://…` 一份出嚟驗。
  實測結果：LFS **360/360 全部 smudge 好，0 個未解 pointer**；
  但 `deploy/linux/cardz-daily-systemd.sh` 嘅硬 require 清單缺 `scripts/verify_daily_run.py`
  同 `scripts/notify_alert.py` —— **clean clone 上面 installer 會即刻 `exit 1`，一條 timer 都裝唔到**。
- **Windows 排程檔歸位**：`install_daily_task.ps1`／`install_gemrate_task.ps1`（`pipelines/`）
  同 `freeze-sweep-guard.ps1`（**`temp/`** ⚠️ 生產排程指住垃圾桶）全部搬入 `deploy/windows/`，
  7 處引用（含 `tests/test_daily_scheduler_contract.py`）已改晒，repo 內無舊路徑殘留。
- **`docs/PACKAGING_CHECKLIST.md`** 新增：交付次序、兩條交付路（clone / zip）嘅分別、
  seed 係 demo 唔係壞咗、`gemrate.env` 兩個位嘅統一選項同代價（**未改，等 PM 揀**）。
- `docs/AWS_HANDOFF.md` 補返 snapshot 生成步驟（原本完全冇提）同 `--output` 陷阱。

**管道 / 自動化**
- daily 鏈靜默失敗位審計完，`scripts/verify_daily_run.py` 係自動驗收 gate，已掛排程
- publish 鏈 `--local-only` 打通
- Watchdog 排程補咗「run 根本冇跑」盲點（`CARDZ-Market-Cap-Watchdog`）
- Catalog 縮水閘 + quarantine 單向棘輪防護

**數據正確性**
- `asOf` 唔再用 snapshot `effective_at` 冒充真實觀測時間（**公開 `asOf` 欄語義冇改，前端照舊**）
- `ensure_std_card_images` 補返 `resolverEvidence.sourceContentSha256`（**冇填假值、冇攞 `contentSha256` 頂替**）
- **成交額窗口駁線**（07-26）：`latest_sales()` 由 0 行嘅 `market_tracked_sales_aggregate`
  改讀 `market_daily_sales_aggregate`。效果：1d 163 張 / 7d 221 張 / 30d 239 張（共 251）
  有真成交額，30 日合計 US$6.46M。詳見 §6 決策 D3。
  （**源本身**實測 12,078 行 / 395 卡 / 217 日，最新 07-26 —— ebay 8,209 行由 2026-04-24 起、
  snk_psa10 3,869 行由 2023-07-20 起。舊嗰張 `market_tracked_sales_aggregate` 仍然 0 行。）
- **FX 匯率接入**（07-26，原 P0-1）：`market_fx_rate_observation` **10 行**，
  run 96 寫 07-26 六個幣種。`format.ts` 嗰個「一個非 finite rate blank 晒成版」嘅風險已解除。

**卡名 display 層修復（07-26，#40）**
- 三個上游截斷嘅英文卡名喺 display 層修好，**每個都有本機出處，冇一個係估**：
  `Monkey.D.Luff` → `Monkey.D.Luffy`（`grade10-scraper/data/cards/snkrdunk/287031/summary_en.json`）、
  `Okuge` → `Okuge-sama and Maiko-han Pikachu`（`.../91423/summary_en.json`）、
  `Ethan's Ho` → `Ethan's Ho-Oh ex`（DB `catalog_variant` 732/742/746，同一張卡嘅英版印刷）
- 落腳點：[card-names.ts](apps/web/src/lib/card-names.ts) 新增 `EN_DISPLAY` + `displayCardNameEn()`；
  [snapshot.ts:37-61](apps/web/src/lib/snapshot.ts:37) `localised()` 接線；
  [card-names.test.ts:7-29](apps/web/src/lib/card-names.test.ts:7) 三個回歸測試
- 順手修：`Red's Pikachu` 譯名 小智→**赤紅/赤红**（Red ≠ Ash）；`Ethan's Ho-Oh ex` 四語補返 `ex`；
  刪 `KO_LEXICON` 一條原文重複行
- `Okuge` 本機**冇任何可查證嘅 CJK 名**（`summary_jp.json` 同 `summary_en.json` byte 相同），
  所以四語 fallback 返修好嘅英文，**冇作譯名**

**環境**
- WSL 原生 ext4 環境（`~/cardz-market-cap`）建立完，Linux 測試套件全綠
- 跨平台代碼審計：Windows-only 假設清查完
- 前端 tsc + vitest + build 全綠

**AWS / Node 部署路徑（07-26，實測過，唔好重做）**
- `apps/web/Dockerfile`（3-stage）+ root `.dockerignore`（deny-all 再逐項放行，
  擋住 6.8 GB `data/` 同 `.env.private`）。image **638 MB**，run as `node`，port 3000
- `next.config.ts` 加 `CARDZ_BUILD_TARGET=node` → `output: "standalone"`。**唔設就照舊**，
  所以 Cloudflare build 出嘅嘢完全冇變
- `apps/web/src/lib/cloudflare-env.ts`（新）—— 全部 CF binding 收窄成一個 guard。
  `CARDZ_RUNTIME=node` 就 return `null`，**連 `@opennextjs/cloudflare` 都唔 import**
- 三個原本掂 CF 嘅檔改用呢個 guard：`server-snapshot.ts`、`robots.ts`、
  `market-assets/[asset]/route.ts`。**冇 `export const runtime = "edge"`，middleware 同
  三條 v1 API 本身已經 runtime-agnostic**
- `apps/web/src/app/api/health/route.ts`（新）—— ALB / container health check
- **實測結果**：容器 1 秒起身，`/api/health` 200、`/` `/pokemon` `/one-piece`
  `/watchlist` `/graders/psa` `/tune` `/card/<id>` 同三條 v1 API 全部 200，
  首頁 101 個 `<tr>` + 900 個圖引用 + `<h1>Top 100 market heatmap</h1>`（真內容唔係白畫面），
  圖 200 `image/webp`、亂 hash 404，`?lang=ja/zh-TW/ko` middleware 正常，
  CSP/HSTS/X-Frame 齊，閒置記憶體 182 MB

---

## 5. 未開始 / 排咗隊（按優先次序）

優先次序原則：**上線 blocker > 每蚊成本影響最大 > 顯示緊錯數 > 等時間 > 換策略**。

| # | 項 | 性質 | 點解排呢個位 |
|---|---|---|---|
| ~~P0-1~~ | ~~FX 匯率接入~~ | ✅ **2026-07-26 完成** | `market_fx_rate_observation` **10 行**，run 96 寫 07-26（JPY 163.69 · KRW 1467.41 · GBP 0.74941 · CNY 6.77 · HKD 7.8418 · TWD 32.343）。**唔好再當佢係 0 行 / 未接線** |
| ~~P0-2~~ | ~~i18n 文案~~ | ✅ **2026-07-26 完成** | production error 0。上線快照四語齊 236/255 |
| **P0-新** | **TAG 每日 fail** | 🔴 一行級 bug，但係影響訪客 | [tag_pop_data.py:208](pipelines/tag_pop_data.py:208) 1997 年 3 條爛行 → `raise` 炸咗成個 catalog dump。TAG 全庫**得 07-22 一日**，而家每日靜靜咁餵過期數俾訪客，`status` 照報 `ready`，**冇任何 validator 會叫**。詳情見 CLAUDE.md「TAG 每日死亡」 |
| P1-2 | POP delta 寫死 null | ✅ 已接線，⏳ 覆蓋率仍受 P3 封頂 | producer 2026-07-26 接咗 [canonical_public_snapshot.py](pipelines/canonical_public_snapshot.py) `population_change_windows()`。實測 243 published 入面 ready = 1d **66** / 7d **67** / 30d **119**，負 delta 全域 **0**（POP 係存量，只升不跌）。**呢個數同時封頂市值 delta 覆蓋率** —— ΔPOP 冇數嗰啲卡，市值 delta 一律 fail-closed 出 null，唔准退返去用價格 delta 頂替。要多啲數就要做 P3 |
| P2-3 | `topGrade` 出字面 `"top"` | 🐛 | [canonical_public_snapshot.py](pipelines/canonical_public_snapshot.py) `card_from_row()` 嘅 `"topGrade": str(observed["top_grade_label"])` |
| P2-1/2 | 市值 delta、成交 delta | ✅ 2026-07-26 已修 | schema 加咗 `marketCapChangePct` / `trackedSalesChangePct`，producer 用 `(1+Δ價)(1+ΔPOP)−1` 組合、成交行真環比。實測 seed 30d **14/100 張卡舊碼箭嘴指錯方向**（rank 2 −76,503 → +2,401,322）。詳見 §4 |
| P3 | POP 每日覆蓋 → ≥90% | ⏳ | 唔搞掂呢個，上面所有「等時間」嘅日期都冇意義。**要連 TAG 一齊修** |
| **P3-新** | **10 萬條 quarantine / 203 條裁決積壓** | 🔴 未知數據債 | 94 次 run 累計 quarantined **70,474** + rejected **32,738**，`market_identity_review_queue` **203 條 pending** —— 全部冇人睇過。閘一直有響，係冇人聽 |
| ~~—~~ | ~~eBay sold comps 採集~~ | ✅ **唔係工程項** | 實測 **3,824 行 / 343 卡 / 92 個日期（04-25 → 07-25）**，已經每日跑緊 3 個月，覆蓋卡數多過 snk_psa10。舊講法「92 行 / 59 variant / 2 個日期」係**讀錯咗**（92 係日期數）。剩返擴覆蓋率，唔係起採集器 |

**其他排咗隊**：seed 刷新 + 本地 site 驗收、排程加 `--publish`、soak Day 1 驗收、
排名語義（grader 頁重編 rank）、卡圖補 4 張（上線快照 251/255）、通知渠道未配置
（所有 alert 而家淨係寫檔，冇人收到）、TCGplayer adapter 攞唔攞到 graded listing、
fail-closed 數量閘、節奏拆三條 timer、48h 閘基準線、daily timer jitter/stealth、
**共用 `pipelines/normalize.py`**（`normalize_collector` 仍然 3 份散落 11 個檔）。

---

## 6. 重要決策同原因 —— 唔准推翻，除非用戶開口

**D1 — eBay 唔可以用 TCGplayer 取代。**
eBay sold comps 係 PSA10 **成交價**嘅唯一真源。TCGplayer 大部分係 raw（未評級）價，
攞佢乘 PSA10 population 會令市值**放大幾倍**。方向係修好 eBay，唔係換走佢。
（2026-07-26 更新：eBay **已經生勾勾** —— 3,824 行 / 343 卡 / 92 個日期。
「修好 eBay」而家嘅意思係**擴覆蓋率**，唔再係「由零起採集器」。）

**D2 — 本機 TCGplayer API 嘅角色係骨幹 + 絆線，唔係價格源。**
三個用途：(a) eBay 冇成交時做覆蓋兜底（**要標明係 anchor 唔係成交價**）
(b) PSA10/raw 比率帶檢查 —— 比率突然崩塌通常代表 parser 爆咗，唔係市場變咗
(c) 每日 liveness 證明。

**D3 — 成交額窗口 anchor 綁 snapshot generation 嘅 effective date。**
同價格 `change_{1,7,30}d_pct` 同一個基準。成交數據落後就照樣顯示縮水 ——
**唔准攞「該卡最後有成交嗰日」當今日嚟造靚個數**。
`market_tracked_sales_aggregate` 由頭到尾冇 writer，已棄用，**唔准接返去**。

**D4 — 職責分層：GemRate = 資格 + POP，唔係價。**
G10 adapter 有嘅欄先食。要加一個 coverage diff step。

**D5 — 節奏拆三條，唔好一大鑊。**
週掃 roster + POP 全量 / 每日 09:30 JST 只刷 roster（~600）價格 → 算指數 → publish /
按需只補新入 roster 卡嘅圖。**爬取時序唔准貼住 G10 自己嘅日程。**

**D6 — Fail-closed 要 gate 喺「數量」。**
最陰險嘅失敗係「靜靜地少咗嘢」—— error 捉唔到。所以要：roster 價格覆蓋下限
（例如 95% 且喺 48h 內），低過就唔准 publish；row-count 對比昨日（0 行同 10 倍都係壞）；
每個 source 一條 run record（source / started_at / finished_at / rows_written / status）。

**D8 — AWS 同 Cloudflare 兩條部署路並存，用 env var 分流，唔准二選一。**（07-26 新增）
分流點得兩個，好易記：

| | Cloudflare | AWS / Node |
|---|---|---|
| build | `npm run build:cloudflare` | `CARDZ_BUILD_TARGET=node npm run build` |
| runtime | `CARDZ_RUNTIME` 唔設 | `CARDZ_RUNTIME=node` |

**點解要一個 runtime guard 而唔係直接删走 CF code**：`getCloudflareContext()` 喺
Node 上面會**動態 import `wrangler` 再開一個 workerd/miniflare 子進程**去讀
`wrangler.jsonc`。`wrangler` 就喺 root `node_modules` 度，所以喺一個完整 checkout
上面 naive 咁跑 `next start` 會**靜靜雞起咗個 workerd**。
`cloudflare-env.ts` 就係為咗喺 import 之前截住佢。**唔准為咗「簡化」而拆走呢層 guard。**

**D7 — 目標環境係 Linux，唔係 Windows。**
WSL 原生 ext4（`~/cardz-market-cap`）係執行同驗證環境 —— 用戶原話：「呢一隻 project 嘅最大前提」。
（全域 `C:\Users\jackson0202\CLAUDE.md` 嗰條「Primary Environment: Windows」係寫俾
kami-content-ops 嘅，**呢個 project 唔適用**。）

---

## 7. 地雷 —— 踩過，唔好再踩

- **`canonical_public_snapshot.py` 唔加 `--output` 會覆寫 `data/public/seed-snapshot.json`。**
  永遠寫 `--output temp/xxx.json`。
- **`pytest` 唔喺 `.venv-backend`。** 用系統 `python -X utf8 -m pytest`。
- **`tracked-gemrate-ids.txt` 係 CRLF**，其他 roster 檔係 LF。用 `comm` / `grep -f` / `sort -u`
  比對會靜靜報零重疊。一律用 Python `line.strip()`。
- **`data/private/gemrate/cards/` 嘅檔案數 ≠ roster 進度。** 個目錄同時累積 candidate 探索卡
  （2978 個目錄 vs roster 1468）。一定要同 roster 取交集先算覆蓋率。
- **DB 時鐘慢本機大約一日**（`CURDATE()` 返 07-25 而本機 07-26）。
  ⚠ **唔好攞呢條解釋 snapshot 缺失。** 實測 `market_index_snapshot` 最新係 **07-24**，
  07-25 同 07-26 兩日都冇行 —— 差兩日唔係一日，係真嘅斷鏈唔係時鐘偏移。
- **~~Daily run 正常要跑成兩個鐘~~ —— 呢句係錯嘅，2026-07-26 推翻。**
  真相：`--pipeline-timeout-seconds` 預設 **7200s 硬牆**，跑唔切就 `TimeoutExpired` exit 1，
  永遠唔會完。09:30 嗰 run 就係咁死（log `daily_staging_off_20260726_093000.log:94-112`）。
  **根因係 key 冇接線**：`scripts/backend.py` 淨係 load `backend.env`，
  但 `GEMRATE_API_KEY` 住喺另一個 gitignore 檔 `data/runtime/config/gemrate.env`，冇人 load。
  於是 `gemrate_source.py daily` 印 `direct=disabled` → 1468 張全部跌落 Playwright
  公開卡頁爬蟲 → 撞牆。**已修**：`SECRETS_PATH` + `daily_environment()` `setdefault`
  （唔准放 `config`，`config` 會俾 `write_local_config` 寫返落 `backend.env`）。
  修後實測 `direct=enabled, public-card-page=standby`，**1.6s/張 × 1468 ≈ 39 分鐘**，
  對 7200s 有 3 倍鬆動。**見到 `direct=disabled` 即係 key 又甩線，唔係「慢」。**
- **`data/runtime/locks/daily.lock` 個 mtime 完全唔代表 lock 生死。** 佢係 OS 級 file lock，
  實測個檔 mtime 停喺 22/7 19:59、size 1 byte，但 lock 真係有人揸住。
  要判斷有冇 run 緊，睇 process list（`run_daily.py` / `gemrate_source.py`）同 log mtime，
  唔好睇 lock 檔。
- **[run_daily.py](pipelines/run_daily.py) 個 `singleton_lock()` 喺 `main()` 最頭攞，行先過 `validate_source`。**
  所以只要有 scheduled run 行緊，`tests/data/` 嗰個 `daily orchestrator is fail-closed`
  一定紅（`another CARDZ daily pipeline run is active`），**同參數無關、唔係 bug**。
  等 run 完再驗。
- **`npx vitest run` 喺 repo root 跑唔得。** 佢會掃到 `data/runtime/node_modules.npmmirror/`
  同 `node_modules.override/`（合共 1.5 GB）入面 63 個測試檔。兩個都俾 `.gitignore:25`
  嘅 `data/runtime/` 蓋住，永遠唔會 ship。正確命令係 **`npm run -w apps/web test`**。
- **`market_daily_sales_aggregate` 冇 grader/grade 欄**，PSA10 篩選靠 `source_code='snk_psa10'` 隱含。
- **`localizedCardName()` 永遠唔會用 `locale === "en"` 呼叫。**
  [card-names.ts:364](apps/web/src/lib/card-names.ts:364) 個 `if (locale === "en") return englishName;`
  喺 production 係死碼 —— [snapshot.ts:48-54](apps/web/src/lib/snapshot.ts:48) 直接原封傳 `en`，
  淨係 zh-TW/zh-CN/ja/ko 先入 `localizedCardName`。**改英文顯示一定要改 `snapshot.ts`，
  改 `card-names.ts` 入面嗰條 en 分支等於冇改過。**
- **Producer 會將英文卡名原封抄入吉嘅 locale 欄。** 實測 `temp/prod-candidate11.json` rank 91：
  `en` `zhTW` `ja` 三個都係 `Monkey.D.Luff`。而 `localizedCardName` 第一句係 `if (current) return current;`，
  所以**淨修 `en` 會留低 zh/ja 照樣顯示嗰個截斷名**。修英文名一定要同時作廢嗰啲抄本
  （見 `snapshot.ts` 個 `stale` flag）。
- **One Piece 卡名尾嗰段 `Booster Pack <SET>` 唔係冗餘，唔准 strip。**
  嗰 11 張卡嘅 `sets.en` 存嘅係**平行/加工規格**（`Comic Parallel`、`Gold Background`、
  `Silver Background`、`Red Comic Parallel`），唔係 set 名。卡名個尾巴先係真 set
  （`Awakening Of The New Era`、`Emperors In The New World`…）嘅唯一載體。
  剪咗＝靜靜地毀資料。呢 11 張歸 set-name 工作處理，唔歸卡名。
- **`normalizeKey()` 會剷走點號**（[card-names.ts](apps/web/src/lib/card-names.ts)），所以 `Monkey.D.Luffy` → `monkeydluffy`、
  `Monkey D Luffy` → `monkey d luffy` 係**兩條唔同 key**。ko 條路仲會將點號換空格再切 token
  （同檔 `translateTokens()` 入面查 `KO_LEXICON` 嗰段），所以 `KO_LEXICON` 加帶點號嘅 key 係**永遠查唔到**嘅死條目。
- **`reserve50` 永不可 export / publish。**
- **唔准 push GitHub / 部署 production**（要用戶確認；CF staging 可以先上）。
- **唔准自行降低 POP / 價格 / 身份 gate。**
- **population 係存量指標，永不顯示負 delta。**
- **UI 唔准自加「資料逾時／預覽／測試」類 disclaimer。**
- **產品 i18n 文案一律書面中文**（唔准口語 嘅／咗／喺／唔係）。
- **卡圖原生 RGBA 圓角 429×600**，唔准 CSS `border-radius` / `object-fit: cover`。
- **`cardz-deploy` 係 stale fork 唔准用**；`cardz-platform` 已廢棄只讀。
  唯一 live repo 就係呢個。
- **`/` 就算數據載入失敗都係回 HTTP 200。** root 有 `loading.tsx` 又冇 `error.tsx`，
  Next streaming SSR 喺 render 完之前已經 flush 咗 200。實測：壞 snapshot 之下
  `/api/health` 503 但 `/` 200。**ALB / target group health check 一定要用
  `/api/health`，唔准用 `/`。**
- **git 入面嗰份 `data/public/seed-snapshot.json` 一定要係 demo，唔准 commit
  production 數據落去。** 呢個係設計，唔係疏忽：
  - `canonical_public_snapshot.py` 攞現行 seed 做 **`--presentation` 輸入**，
    出完 candidate 之後 **promote 覆蓋返同一個檔**（[run_daily.py](pipelines/run_daily.py)
    `promote_file()`、[docs/SERVER_MIGRATION.md](docs/SERVER_MIGRATION.md)
    嘅 daily 流程圖「promote candidate → data/public/seed-snapshot.json」）。即係話個 seed 喺 server 上面每日都會
    被真數據蓋，**git 只需要存一份 hermetic 嘅 demo 底**。
  - `tests/data/` 有四個測試**結構上要求**佢係 demo：`publisher refuses demo
    data without explicit allow-demo`、`remote publisher requires generation
    canary…`、`publisher fails before pointer when raw-front missing/changed`、
    `production validation blocks a demo-mode seed from release`。commit 咗
    production 數據落去，呢四個即刻紅。
  - 對應嘅 **360 張圖 git 有 track**，同 demo seed 係一套。`git clone` 出嚟
    build 到、測試全綠，只不過係 demo 數據 —— **呢個就係想要嘅效果**。
  - 交當前 production 數據俾人上 server，**唔係靠 commit seed**，係靠對面跑
    `run_daily.py --publish --local-only`（unit 已帶 `CARDZ_DAILY_PUBLISH=local`，
    見 [docs/SERVER_MIGRATION.md](docs/SERVER_MIGRATION.md) 缺口表嘅 **G3** 行）。
- **07-26 10:39 修正咗一個之前寫錯嘅結論。** 呢度舊版寫住「seed 自稱
  `productionEligible: true` 但 `assertPublicSnapshot()` 實測 708 個 error，
  **要修就修 producer**」。**producer 冇事** —— 個 708 係 **presentation pack
  被污染**嘅後果：
  - 當時工作區嘅 seed 被 production 數據覆蓋咗（mtime 07-26 07:21），跟住
    佢又做返落一轉 export 嘅 `--presentation` 輸入，於是 6 張爛 collector
    number（`GG69` `GG44` `SV49` `SV107` `GG70` `TG20`）被原封照抄過下一代。
  - `git checkout data/public/seed-snapshot.json` 還原 demo pack 之後即刻重跑：
    `new_from_catalog` 由 **0 變 59**，嗰 6 張改由 catalog identity 重建，
    `collectorNumber.complete === false` 嘅卡 **變返 0 張**。
  - 實測 `temp/prod-candidate11.json`（同一個 generation
    `canonical_20260724_0a295bbce68a`）：**0 error**，top100 100/100 四語齊
    且互不相同。702 個 i18n error 亦已經俾 G10 翻譯入庫清晒。
  - **教訓：seed 係自我餵飼嘅（自己做自己下一代嘅輸入）。一次污染會世代遺傳，
    而且會扮成 producer bug。** 見到 validator 報結構性 error，第一件事係
    `git status data/public/seed-snapshot.json` 睇下個 pack 係咪俾人蓋咗。
- **Windows 跑唔到 `npm run build:cloudflare`。** `apps/web/.open-next/assets`
  自 07-24 20:57 就被鎖死，OpenNext 喺 `initOutputDir` 嗰步 `EPERM` 即死
  （**仲未行到任何 app code**）。OpenNext 自己都印 warning 話唔完全支援 Windows，
  叫用 WSL。唔關 Node/AWS 路徑事，唔好追呢條。
  **但唔好因為 Windows 行唔到就當 Cloudflare 路徑壞咗** —— 07-26 喺 Linux
  container 帶住全部 Node/AWS 改動實跑過 `build:cloudflare`，exit 0，出到
  `.open-next/worker.js`（2,278 bytes），`public/` 亦正常還原。兩條路真係並存。
- **喺 Linux 驗 `build:cloudflare` 要掛 named volume，唔可以淨係 `docker build`。**
  `cloudflare-build.mjs` 會 `renameSync(apps/web/public → os.tmpdir())`，
  overlayfs 跨 layer rename 目錄一定 `EXDEV`。改 `TMPDIR` 去 build context 內
  一樣死（都係 overlayfs）。要 `docker run -v <vol>:/work` 抄份源碼入真 ext4
  再 `export TMPDIR=/work/.tmpcf` 先跑得。
- **68 個 critical alert 唔好逐張卡查 —— 佢哋係同一件事。**（07-26 查實）
  68 個全部 `alert_type='entered_top100'`，**65/68 `previousRank` 係 null**。
  根因：`market_alert_evaluation` 嘅 `effective_date` 亂序寫入
  （照 id：07-23 → 07-22 → 07-23 → 07-24 → 07-21 → 07-24 → 07-24 → 07-25），
  `previous_snapshots()` 因此揾唔返前一日 rank，每張卡都好似新入榜。
  **係一次 backfill 嘅 cold-start artifact，唔係 68 個問題。**
  - **訪客睇唔到 alert**：`canonical_public_snapshot.py` 全份得 `:422` 一處掂 alert 表，
    只攞 `id` + `effective_date` 做 join key，`apps/` 同 `packages/` grep `alert` = 零。
  - **但有一條隱性路徑**：producer 揀「`effective_date <= 快照日` 最新嗰個 evaluation」。
    亂序之下如果某日最新嗰個係退化 eval，delta join 會**靜靜少行**。
    實測 **id=4 得 75 行 eligible（`top100_cutoff_usd` 係 NULL）**，正常係 194～270。
    今日夾啱冇事（255/255），改 alert 相關嘢之前要睇住呢條。
- **`coverageStatus: blocked` 唔係數據錯誤旗，唔好去查。**
  8 次 evaluation 全部 `blocked`，唯一原因係 `unresolved_high_potential_count` 唔係 0
  （最新 **124**，由 56 升上嚟）。呢個係內部發現雷達嘅治理旗，**唔會閘住公開快照**。
- **TAG 唔係「未排程」，係「排咗每日 fail」。** [run_daily.py:391](pipelines/run_daily.py:391)
  有叫 `tag_daily_capture.py`。死喺 [tag_pop_data.py:208](pipelines/tag_pop_data.py:208)
  `RuntimeError: TAG set identity is incomplete for 1997`（1997 年 144 行入面 3 行 blank identity），
  而個 `raise` 喺雙層迴圈**入面**，所以一條爛行炸咗成個多年份 dump。
  07-23 靠 `latest_tag_catalog(max_age_hours=72)` fallback 成功過一次（重用 07-22 數），
  之後 cache 過期就日日 `tag_status = "unavailable"` 靜靜跳過。
  **168h 新鮮度閘救唔到你**：閘只讀 `populationPsa10`（由 PSA 行嚟，gemrate 健康），
  `graderPopulations['TAG'].asOf` 冇任何地方查年齡，`latest_populations()` 又冇 date floor。

---

## 8. 檔案地圖 —— 邊份文管咩

> 呢個 section 直接對應用戶投訴「東一件西一件，搞到又要揾嚟揾去」。
> **加新文檔嘅時候，一定要喺呢度加返一行**，否則佢等於唔存在。

| 檔 | 管咩 | 幾時改 |
|---|---|---|
| **`PROJECT_STATE.md`**（呢份） | **當下營運狀態**：做緊乜、死線、決策、地雷 | 每次 session 收工 |
| `AGENTS.md` | agent 入口：任務分級 L0–L3、幾時先開 Docker/MySQL | 好少 |
| `CLAUDE.md` | repo 硬規則（Claude Code 讀） | 有新硬規則 |
| `config/data-routing.json` | **架構所有權契約**：邊個檔擁有邊個 node、work item acceptance。**唯一手寫架構源** | 架構／所有權／依賴／公開契約改變 |
| `docs/ARCHITECTURE_CHAIN.md` | 前端 → API → snapshot → DB 一條鏈同 9 個斷點 | 斷點修好／新增 |
| `docs/HANDOFF.md` | ⚠️ **2026-07-26 01:45 嘅凍結快照，已過時**（寫住 publish 未通，其實通咗）。當歷史讀，唔好當現況 | 唔再更新，由呢份取代 |
| **`docs/AWS_DEPLOY.md`** | **交俾第三方嘅 AWS/Node 部署手冊**：build 命令、env var、port、health check、已知限制 | Dockerfile／env var／health check 改變 |
| `apps/web/Dockerfile` | Node standalone runtime image（3-stage，build context 要 repo root） | 依賴／Node 版本／啟動方式改變 |
| `.dockerignore` | Docker build context 白名單（deny-all 再放行）。`data/` 有 6.8 GB，根目錄有 `.env.private`，**唔准改成黑名單** | 新增 build 期要讀嘅 repo 檔 |
| **`docs/PACKAGING_CHECKLIST.md`** | **交付次序**：git clone 定 zip、跑邊個閘、秘密檔擺邊、seed 係 demo 唔係壞咗、gemrate.env 兩個位嘅選項 | 交付流程／閘／檔案位置改變 |
| `docs/AWS_HANDOFF.md` | clean-clone + AWS backend 交接（已補 snapshot 生成步驟同 `--output` 陷阱） | 交接流程改變 |
| `docs/DATA_CONTRACT.md` | 公開數據契約 | 契約改變 |
| `docs/RUNBOOK.md` / `SOAK_RUNBOOK.md` | 日常操作 / soak 監察步驟 | 流程改變 |
| `docs/SERVER_MIGRATION.md` | Linux/systemd 遷移 | 遷移進度 |
| `docs/PAGE_DATA_REQUIREMENTS.md` | 前端每頁要咩欄位 | 前端需求改變 |
| `scripts/verify_handoff.py` | **靜態**交付閘：REQUIRED_FILES 51 個，驗磁碟（`layout`）+ 驗 `HEAD`（`tracking`）。⚠️ 驗 `HEAD` 唔係 index —— `git ls-files` 會將「`git add` 咗但未 commit」報成 tracked | 加咗部署必需檔 |
| `scripts/verify_clean_clone.py` | **動態**交付閘：真 `git clone` 一份出嚟，驗 LFS pointer + installer 硬 require + snapshot 出唔出得街。exit 3 = 檔齊但 snapshot 仲係 demo | 同上 |
| `scripts/verify_daily_run.py` | daily run 驗收 gate | — |
| `scripts/audit_wiring_gaps.py` | **孤兒數據偵測**（每個 probe 綁一個前端元素 + `consumer` 註記） | **改咗 producer 一定要同步改 `consumer` 欄** |

**界線（唔准違反 [AGENTS.md](AGENTS.md) 「Read PROJECT_STATE.md first, update it last」嗰段嘅
"`PROJECT_STATE.md` records **operational state**" 分工）**：
`config/data-routing.json` 管**架構所有權**，`PROJECT_STATE.md` 管**當下狀態**。
呢份文**唔准**重新宣告 node 所有權、routing、或者 acceptance 契約 —— 嗰啲淨係
`data-routing.json` 講嘅算。

---

## 9. Handoff protocol

### 開工（任何 agent、任何 model）
1. 讀呢份 `PROJECT_STATE.md`
2. 跑 §0 三條驗證命令，用真實輸出蓋過文字
3. 睇 §7 地雷，確認你要做嘅嘢冇踩
4. L2/L3 級改動：再跑 `python scripts/backend.py work-items --status in_progress`

### 收工（**唔准跳**）
1. 更新 §3 進行中、§4 已完成、§5 排隊
2. 有新決策 → 寫入 §6，**連原因一齊寫**（冇原因嘅決策下個 agent 一定會推翻）
3. 踩到新地雷 → 寫入 §7
4. 加咗新檔／新文檔 → 寫入 §8 檔案地圖
5. 改頭部「最後更新」時戳同 agent 名

### 發現「資料喺曬度但係冇接線」
跟 `CLAUDE.md`「孤兒資料歸位」硬規則，一次過做齊三步：
**歸位**（入 repo 正式位置）→ **註明指向**（檔喺邊／邊個寫／邊個讀／有冇人用）→
**駁線**（真係接落去用；接唔到要寫明「未接，因為 X，接線工作項係 Y」）。
淨係寫份報告收工 = 冇做。
