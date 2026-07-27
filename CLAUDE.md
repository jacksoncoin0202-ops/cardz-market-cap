# CARDZ Market Cap — Project Rules

寶可夢卡市價網站（Next.js，`apps/web`）。呢度嘅規則全部係 2026-07-23 retro 從真實犯錯總結，hard rules，唔好再犯。

## 開工第一件事：讀 PROJECT_STATE.md（hard，2026-07-26 用戶欽點 —— 最高優先）

**[PROJECT_STATE.md](PROJECT_STATE.md) 係呢個 project 嘅單一真相來源。**
開工前一定要讀，收工前一定要更新。呢份文係 model-agnostic —— Claude / Codex / Hermes / Pi
睇同一份，唔靠任何一個 model 嘅 session 記憶。

**點解要有**：multi-agent + 中途換 model 之後，新 agent 完全唔知之前做過乜，
結果左揾右揾、重複做，最後先發現「原來一早做咗，淨係冇駁線」。
Claude Code 嘅 task list 淨係活喺 Claude session 入面，Codex / Hermes 睇唔到 ——
所以狀態一定要落地成檔。

**收工唔更新 = 冇做完。** 要更新嘅係：進行中／已完成／排隊／新決策（連原因）／
新地雷／新檔案（寫入檔案地圖 §8）／頭部時戳同 agent 名。

**一個反制腐爛嘅設計**：`PROJECT_STATE.md` §0 列住三條驗證命令。
**唔准淨係信份文** —— 落手前先跑，用真實輸出蓋過任何文字。
（`docs/HANDOFF.md` 就係腐爛咗嘅例子：佢寫住 publish 鏈未通，其實早就通咗。）

## 孤兒資料歸位（hard，2026-07-26 用戶欽點 —— 最高優先）

發現「**資料喺曬度但係冇接線**」、新材料包、新數據源、新腳本 —— **唔准淨係寫份報告收工**。
一次過做齊三步，缺一步就當冇做：

1. **歸位** —— 檔案／腳本擺入 repo 正式位置（`pipelines/` `scripts/` `docs/` `data/`）。
   **唔准留喺 `temp/`、桌面、下載資料夾、或者散落 chat 入面。**
2. **註明指向** —— 喺 CLAUDE.md 對應 section 或 `docs/` 加返一行：
   **檔喺邊 · 邊個寫 · 邊個讀 · 而家有冇人用**。四樣缺一唔可。
3. **駁線** —— 真係接落去用。接唔到就寫明「**未接，因為 X，接線工作項係 Y**」，
   唔准留一句「已記錄」就算。

**理由**：長期咁做落去 project 先會歸一成一個整體，唔會變成一堆互相唔知對方存在嘅碎片。
「發現咗 → 寫低 → 收工」等於製造下一次考古 —— 下次又要重新揾一次，重新驗一次。

**已知反面教材**（呢啲就係因為冇跟呢條規矩先會出現）：
`market_daily_sales_aggregate` 3592 行三年成交數據**冇任何 producer 讀**，
而前端同時顯示緊「成交數據不可用」，因為佢讀緊隔離嗰張 0 行嘅表。

## UI 文案與產品定位（high）
- 寫任何 user-facing 文案或狀態標籤：預設當 go-live 標準交付，**唔准自行加**「資料已逾時／累積中／預覽／測試／未驗證」類 disclaimer 或 banner（加過被用戶鬧「唔好再有呢啲嘢，你阻住我評估」）。數據不足用中性顯示（「—」或低調 dim），內部驗證狀態擺 console/report。想加 caveat 先問用戶。
- 產品 i18n 文案一律**書面中文**——對話可以廣東話，落代碼嘅 zh-TW/zh-CN copy 唔准有口語句式（嘅/咗/喺/唔係）。寫完 copy 自查一次先提交。
- 卡名/專有名詞中文化：只准用官方中文譯名或玩家社群共識俗名（例：梵高皮卡丘），**唔准逐詞直譯**（直譯出過「戴灰色毛氈帽的皮卡丘」，破壞專業定位）。無共識嘅卡保留英文名。批量譯名完成後將完整清單俾用戶過目先 ship，唔好淨報覆蓋率。

## 數據語義（high）
- 加任何升跌/delta 顯示前，先分類指標：**流量型**（價、成交額）先可以用 ±delta；**存量/累積型**（population、收錄數）只升唔跌，永遠唔准顯示負 delta 或跌箭嘴（被鬧過「PSA 10 嘅數量點會跌？啲卡唔會消失」）。冇對應窗口歷史數據就直話用戶，唔准攞另一個指標嘅 changePct 頂替。
- 數據源文檔講「有邊幾個廠商/實體」時，必須標明清單係邊個層面嘅事實（per-card API enum / 週報 / 網站界面）。發現官方界面同 API 有落差，開「已知落差」section 主動記低，唔好等用戶貼圖質問（GemRate 四廠 vs 五廠 TAG 事件）。

## 視覺標準（medium）
- 新頁面/改表格佈局：colgroup 各欄百分比加埋必須 = 100%；欄寬、行距、字級對照首頁現有標準。改完 screenshot 同首頁並排對照先話完成（出過加埋 110.5%、欄位肥過首頁）。
- 用戶可見控制元件（dropdown、menu、tooltip）：唔好用原生 `<select>`/OS 樣式交差，要自訂 component 配 theme tokens；「做完未」嘅標準包埋互動展開態。
- 詳情頁/新頁面遵守「數據密度優先」：hero 圖唔准佔超過首屏 1/3，騰出空間放數據。唔好照搬 showcase 網站嘅大圖排版。
- **裸卡圖一律原生 RGBA 圓角**（2026-07-25 用戶欽點規矩）：所有 `<img>` 卡圖（detail-art、preview-image、sheet-image、ranking-thumb、grader-card、heatmap tile-card）必須用 DB 入面嘅原生 RGBA 圓角圖，**唔准**用 CSS `border-radius` 削角、唔准 `transform: scale()` 放大遮醜、唔准 `object-fit: cover` 裁切。統一規格係 **429×600 透明畫布**（梵高比卡超做基準），入庫前由 `pipelines/native_image_resolver.py` 嘅 `normalize_card_canvas()` 做 alpha-bbox crop + 等比縮放 + 置中。新加卡圖位置必須照用呢個規格。
- **白邊 QC 入庫管道**（2026-07-25 用戶規矩）：裸卡圖入 DB 前必須係原生 RGBA 圓角來源（SNK harvest cache / SNK get_master / Kado dump RGBA / TCGdex EN PNG），過 `is_native_rounded()` 角位 alpha gate（4 角 alpha < 10）先收貨。落完貨經 `store_native_image()` 統一畫布規格後入庫。`trim_white_border` 只保留做非 alpha 來源嘅 fallback（`tests/test_white_border_trim.py`）。每日 delta 用 `manifests/image-qc.json` 嘅 `stdCanvas: "std-429x600"` 標記 skip 已處理卡。
- **方角卡自助轉格式**（2026-07-25）：得返 RGB 方角圖唔使重下載——`native_image_resolver.apply_rounded_corners()` 補 6% 卡寬原生圓角（只改 alpha，卡面內容唔郁）。`has_rounded_corners()` 做檢測，`store_rounded_image()` 已圓角回 None（delta skip）。
- **揾卡手冊**（2026-07-25）：新卡入列嘅完整制度（圖規格、來源 chain、故事 metadata、長期須知、每日全自動流程）睇 [docs/CARD_SOURCING_HANDBOOK.md](docs/CARD_SOURCING_HANDBOOK.md)。每日 `run_daily.py` 已插入 `ensure_std_card_images.py --write` 做卡圖自愈，新卡即日統一梵高標準。

## CSS 陷阱（high，同一 bug class 犯過兩次）
- 改 CSS media query 後 reload computed style 唔變：**第一個假設永遠係「同檔案後面有同 specificity 嘅 base rule 冚咗」**——即刻 Grep 該 selector 全部出現位置檢查順序，唔好連續 reload 超過 2 次。修完順手 Grep 晒成個 stylesheet 有冇同類位一次過清。每個新 layout block 都要有 base CSS，唔好齋寫 media query。

## Mockup → App 移植（high）
- `docs/mockups`（或任何 prototype HTML）永遠只係 prototype。用戶話「套用／用呢款／跟呢版」＝ 整合入 `apps/web` 正式 app，唔係繼續改 mockup（搞錯過被 interrupt）。有歧義動手前一句確認。
- 移植已驗收 mockup：先列出具體視覺元素清單（框、色標、光暈、rank chip、排序演算法、badge），移植後逐項 browser 驗證＋截圖同 mockup 並排對比先話搞掂。未經用戶同意唔准更換佈局/排序演算法（整唔見過排名 badge、擅自換排序被彈「個排序明顯有問題」）。

## 代碼改動驗證（medium）
- 改動任何現有 `.py`（provider、pipeline、workflow）後：必須跑 repo 現有測試先可以報「搞掂」。冇測試覆蓋改動嘅 interface 要明講「未覆蓋，風險係 X」。
- 前端改動：tsc + 相關 vitest 過，加一張成功 screenshot（desktop 同 mobile viewport），先叫完成。

---

# 腳本地圖（2026-07-26 全量抽取，唔好再 grep 揾嚟揾去）

50 個 `.py` 散喺 `pipelines/` 同 `scripts/`。落手做嘢前睇呢度，唔好靠 grep 猜路徑。

## 入口點（真正會被排程叫嘅得呢幾個）

| 檔 | 做咩 |
|---|---|
| [pipelines/run_daily.py](pipelines/run_daily.py) | **唯一每日主鏈**。fail-closed：採集 → 指標 → snapshot → 卡圖自愈 → publish。Linux `cardz-market-cap-daily.timer` 00:30 UTC = 09:30 JST；Windows legacy task `CARDZ-Market-Cap-Daily` 09:30 local |
| [scripts/verify_daily_run.py](scripts/verify_daily_run.py) | 事後**驗收閘**（4 個 check）。exit `0` 過 / `1` 數據唔過 / `2` 驗唔到（DB 死，閘根本冇行過）。`2` 唔等於冇事 |
| [scripts/notify_alert.py](scripts/notify_alert.py) | 通知層。⚠️ 冇 `CARDZ_ALERT_WEBHOOK` 就**只寫檔零推送**，而且**照 return 0**，連 `systemctl --failed` 都唔標紅（task #21） |
| [scripts/backend.py](scripts/backend.py) | 跨平台 backend bootstrap / replay |

## GemRate（POP 權威 — 唔係價）

| 檔 | 做咩 |
|---|---|
| [pipelines/gemrate_source.py](pipelines/gemrate_source.py) | **單檔全功能 collector**。⚠️ 唔喺 `integrations/`，我揾錯過兩次。8 個 subcommand：`daily`（run_daily 叫呢個）· `api-dump`（key window 批量，**呢個先係凍結掃**）· `collect`（無 key，Chrome 搜尋擴 id）· `public-card-dump`（無 key 逐張現值）· `pop-report`（無 key 每套四廠）· `identity-receipts` · `export-csv` · `grader-volume` |
| [pipelines/gemrate_client.py](pipelines/gemrate_client.py) | 私有 API client |
| [pipelines/gemrate_brute_harvest.py](pipelines/gemrate_brute_harvest.py) | curl_cffi 暴力收割（唔使 browser） |
| [pipelines/gemrate_candidate_discovery.py](pipelines/gemrate_candidate_discovery.py) | 搜尋證據 → API worklist |
| [pipelines/gemrate_candidate_backfill.py](pipelines/gemrate_candidate_backfill.py) | 候選卡 POP 補值 |
| [pipelines/gemrate_resume_failed.py](pipelines/gemrate_resume_failed.py) | 重收失敗 set（unicode_escape fix 之後） |
| [pipelines/population_daily.py](pipelines/population_daily.py) | append-only POP 落地 + 新鮮度 |
| [pipelines/grade10_full_freeze.py](pipelines/grade10_full_freeze.py) | ⚠️ **唔係** GemRate 掃！係抄本機 `../grade10-scraper/data` 檔案樹入私有 landing。名似而已 |

**ID 清單邊個係邊個**（全部喺 `data/runtime/private-source-map/`）：

| 檔 | 行數 | 用途 |
|---|---|---|
| `tracked-gemrate-ids.txt` | 1468 | **全 roster**，凍結掃用呢個 |
| `one-piece-gemrate-discovery-ids.txt` | 1597 | One Piece 發現候選（未入 roster） |
| `gemrate-ids.txt` | 600 | 每日價格 roster |
| `active-gemrate-ids.txt` | 400 | active universe 子集 |
| `pipelines/gemrate_ids.txt` | 1270 | 舊清單，唔好用 |

## 價格源（eBay = PSA10 成交唯一真源，TCGplayer = 骨幹＋絆線）

| 檔 | 做咩 |
|---|---|
| [pipelines/ebay_sold_data.py](pipelines/ebay_sold_data.py) | **PSA 10 成交價正源** |
| [pipelines/ebay_brute_harvest.py](pipelines/ebay_brute_harvest.py) | curl_cffi 收割 |
| [pipelines/snk_market_data.py](pipelines/snk_market_data.py) | SNKRDUNK 價格 |
| [pipelines/snkrdunk_bulk.py](pipelines/snkrdunk_bulk.py) · [snkrdunk_discover.py](pipelines/snkrdunk_discover.py) | 批量 API / 全圖鑑發現 |
| [pipelines/tag_pop_data.py](pipelines/tag_pop_data.py) · [tag_daily_capture.py](pipelines/tag_daily_capture.py) | TAG Grading（第五廠） |
| [pipelines/daily_prices.py](pipelines/daily_prices.py) | append-only 價格落地 |
| [pipelines/fx_rates.py](pipelines/fx_rates.py) | USD 匯率 |

## 卡圖

| 檔 | 做咩 |
|---|---|
| [pipelines/native_image_resolver.py](pipelines/native_image_resolver.py) | **規格權威**：`normalize_card_canvas()` 429×600、`is_native_rounded()` 角位 alpha gate、`apply_rounded_corners()` 補圓角 |
| [pipelines/ensure_std_card_images.py](pipelines/ensure_std_card_images.py) | 每日自愈。⚠️ **預設 dry-run**，要 `--write` 先真改 |
| [pipelines/verify_images.py](pipelines/verify_images.py) | 驗每張圖 local + 內容定址 + 完整 |
| [pipelines/native_image_refetch.py](pipelines/native_image_refetch.py) · [canvas_normalize_backfill.py](pipelines/canvas_normalize_backfill.py) | 一次性遷移 CLI |

## 指標 / 排名 / 發佈

| 檔 | 做咩 |
|---|---|
| [pipelines/market_metrics.py](pipelines/market_metrics.py) | 每日指標推導 |
| [pipelines/ranking_derivation.py](pipelines/ranking_derivation.py) | PSA 10 排名（確定性） |
| [pipelines/canonical_public_snapshot.py](pipelines/canonical_public_snapshot.py) | 出公開 snapshot |
| [pipelines/market_source_sync.py](pipelines/market_source_sync.py) | 正規化四源觀測 |
| [pipelines/active_universe.py](pipelines/active_universe.py) · [tracked_universe.py](pipelines/tracked_universe.py) | 界定追蹤宇宙 |
| [pipelines/market_alerts.py](pipelines/market_alerts.py) | 排名 / 覆蓋率 / 候選異動 |

## 數據治理

`data_cleaning_rules.py`（清洗控制面）· `data_routing.py`（路由契約）· `data_coverage_audit.py`（覆蓋審計）· `registry_lineage.py`（控制面註冊表）· `source_crosswalk.py`（源對照）· `db_runtime.py`（MySQL migration + replay）· `db_retention.py`（壓縮重複 payload）· `verify_archives.py`（zstd 驗證）

## 部署 / 交接

`scripts/canonical_seed.py`（可攜 seed）· `seed_restore.py`（還原入空 DB）· `bootstrap_archive.py` · `verify_handoff.py`（fail-closed 可攜性檢查）· `deploy/linux/cardz-status.sh` · `deploy/windows/cardz-status.ps1`

## 文檔

[docs/HANDOFF.md](docs/HANDOFF.md)（接手第一份）· [docs/SOAK_RUNBOOK.md](docs/SOAK_RUNBOOK.md)（出事點救）· [docs/SERVER_MIGRATION.md](docs/SERVER_MIGRATION.md)（Linux 上線）· [docs/CARD_SOURCING_HANDBOOK.md](docs/CARD_SOURCING_HANDBOOK.md)（新卡入列制度）· **[docs/G10_BASELINE.md](docs/G10_BASELINE.md)（G10 = 我哋個底，做 G10 接線前必讀）** · **[docs/DATA_NORMALIZATION.md](docs/DATA_NORMALIZATION.md)（洗數據三層架構，加新數據源／改 `normalize_*` 前必讀）**

## 揾嘢陷阱（真犯過）

- GemRate collector 喺 `pipelines/`，**唔喺** `integrations/grade10/`
- `grade10_full_freeze.py` 同 GemRate 掃**冇關係**
- `ensure_std_card_images.py` 唔加 `--write` 咩都唔會改
- run identity 係 **UTC**（`run_daily.py` `sources_%Y%m%d`、`verify_daily_run.py --expected-date` 預設 UTC today）。機係 JST，UTC 日界喺 09:00 JST 轉 —— 09:30 JST 嗰 run 出**當日** UTC 日期嘅 snapshot
- Grep/Glob 全 repo 裸搜會 20s timeout，先縮 path
- **`pytest` 唔喺 `.venv-backend`**。跑測試用系統 `python -X utf8 -m pytest`（3.10 / pytest 9.1.0）。`.venv-backend` 淨係跑生產腳本（`scripts/backend.py` 之類）
- 起 snapshot 做實驗一定要加 `--output temp/xxx.json`。`canonical_public_snapshot.py` 嘅 `--output` **預設覆寫 `data/public/seed-snapshot.json`**（同 `--presentation` 同一個檔）
- **git 入面嗰份 `data/public/seed-snapshot.json` 一定要保持 demo，唔准 commit production 數據落去。** 個 seed 係自我餵飼嘅（自己做自己下一代嘅 pack 輸入），一次污染會世代遺傳：07-26 就試過因為工作區個 seed 俾 production 蓋咗，6 張爛 collector number 被照抄過下一代，validator 報 708 error 而錯怪咗 producer。`git checkout data/public/seed-snapshot.json` 還原之後 0 error，producer 一行都冇改。**validator 報結構性 error，第一件事係 `git status data/public/seed-snapshot.json`，唔係去睇 producer。**

---

# 排程 · 閘 · Key 死後行為（2026-07-26 實測，唔好再查）

## 三條 timer 實況

| unit | OnCalendar | JST | jitter |
|---|---|---|---|
| `cardz-market-cap-daily.timer` | 00:30 UTC | 09:30–10:00 | 1800s |
| `cardz-market-cap-watchdog.timer` | 05:07 UTC | 14:07 | 冇 |
| `cardz-grade10-discovery.timer` | 21:17 UTC | 06:17–06:42 | 1500s |

**2026-07-26 更新（task #29 已做）**：discovery 由 23:43 UTC 搬去 21:17 UTC，daily 加咗 1800s jitter。
以前兩條準時到秒、恆定相隔 47 分鐘 —— 個**間距本身**就係指紋，撞正「唔准貼住 G10 日程」。
而家兩條都有 jitter，間距浮動 2h48m–3h43m。daily jitter 唔可以再加大：最遲開跑唔准跨 UTC 日
（`market_run_id` 由 UTC 日期砌），最遲完成要早過 05:07 UTC watchdog。兩條邊界由
`tests/test_daily_scheduler_contract.py` 直接讀 unit 檔驗，踩線就見紅。
`systemd-analyze verify` + `systemd-analyze calendar` 喺 WSL 實測通過。

## 節奏拆三條：唔使 refactor `run_daily.py`

**2026-07-26 更新：三條全部已經喺 `deploy/systemd/` 落咗地，冇一條係「要寫」。**

| 角色 | unit | 觸發 |
|---|---|---|
| 週掃 roster + POP | `cardz-gemrate-freeze.timer/.service` + `run-cardz-gemrate-freeze.sh` | `Sun *-*-* 14:23 UTC` + 3600s jitter，`Conflicts=daily` |
| 日更 ~600 價格→指數→publish | `cardz-market-cap-daily.timer/.service` | `00:30 UTC` + 1800s jitter |
| 按需補圖 | `cardz-image-backfill.service` + `run-cardz-image-backfill.sh` | **冇 timer 係設計，唔係漏咗** —— `systemctl start` 觸發，`Conflicts=daily` |

注意：`run_daily.py` **冇任何 flag 分得開 POP 掃同價格刷**，`--skip-market-source-refresh` 係全有全冇。「日更只刷價唔掃 POP」喺現有 code 唔存在 —— 但 daily 本身已綁 600，實際負載已經好接近。

## Publish 預設

`deploy/windows/run-cardz-daily.ps1` 嘅 `-Publish` 預設 = **`off`**（2026-07-26 改）。原因：生產 Windows 排程 `CARDZ-Market-Cap-Daily` 嘅 action **冇傳 `-Publish`**，預設留 `local` 會令排程靜靜自己開始真發佈入 `data/public/`。Linux 唔受影響 —— unit 檔明寫 `Environment=CARDZ_DAILY_PUBLISH=local`。[deploy/windows/install_daily_task.ps1](deploy/windows/install_daily_task.ps1) 個 `$arguments` 一樣冇 `-Publish`，重跑 installer 都維持唔發佈。

## GemRate key 點樣入到每日 run（2026-07-26 修好，之前完全冇接線）

| | |
|---|---|
| **檔喺邊** | `data/runtime/config/gemrate.env`（gitignore，**永不 print 內容**） |
| **邊個寫** | 人手擺（2026-07-26 用戶提供）。**唔准寫入 `backend.env`** —— `backend.py` 嘅 `write_local_config(CONFIG_PATH, generated)` 會把 `config` 全量寫返落 `backend.env`，key 會漏入 repo 可見路徑 |
| **邊個讀** | [scripts/backend.py](scripts/backend.py) `SECRETS_PATH` → `daily_environment()` 用 `setdefault` 灌入子進程 env（`setdefault` 唔係覆蓋，所以 operator 手動 export 嘅 key 仍然贏） |
| **而家有冇人用** | ✅ 每日排程行緊。驗證方法：睇 log 有冇 `[daily] N cards, direct=enabled` |

**點解要記低**：呢個檔一直存在而且內容正確，但由頭到尾**冇任何一段 code load 過佢**。
`backend.py` 淨係 load `backend.env`（冇 key），`.env.private` 只有 R2/canary 四個變數（冇 key），
排程 action 亦冇傳。結果 [gemrate_source.py](pipelines/gemrate_source.py) `cmd_daily()` 嗰句
`key = os.environ.get("GEMRATE_API_KEY", "")` 永遠攞到空字串 → `if key:` 直連段跳過 →
`website_ids` 變成全 1468 → 全部行 Playwright 公開卡頁 → 撞 7200s 硬 timeout → exit 1。
**典型「資料喺曬度但係冇接線」**。

## GemRate key 死咗（~07-29）之後

| 路徑 | 冇 key 行為 |
|---|---|
| `cmd_api_dump`（凍結掃） | **硬 require key，`return 2` 即死** |
| `cmd_daily`（run_daily 叫） | **唔死**，fallback 去 `integrations/grade10/data` 本機 mirror（[gemrate_source.py](pipelines/gemrate_source.py) `DEFAULT_G10_MIRROR_ROOT`） |

準確講法：**凍結掃保住歷史，mirror 頂住當前值**。但 mirror docstring 明寫佢 never allowed to generate a population history file —— 即係 key 死後每日 POP 靠本機 mirror 保鮮，mirror 越重要，撞 G10 日程嘅代價越高。

## 新鮮度閘點量度（P2 修完之後）

[validate.ts](packages/market-data/src/validate.ts) 嘅 `assertPublicSnapshot()` 用 `metricAgeHours()` 量 metric `asOf` 對 `generation.effectiveAt` 之差，門檻兩粒常數：`PRICE_FRESHNESS_HOURS` = 48h、`POPULATION_FRESHNESS_HOURS` = 168h。

- **修咗嘅**：`canonical_public_snapshot.card_from_row` 以前將 snapshot 自己個 `effective_at` stamp 落 `pricePsa10`/`populationPsa10`/`marketCap`，令 age 由構造上永遠 0。而家用真實觀測時間（`latest_price_at()` + PSA population observation），冇觀測就 fail-closed 拋 `SnapshotExportError`。
- **仲喺度嘅**：`effectiveAt` 由 DATE 欄轉成**午夜 00:00:00Z**，永遠早過當日觀測 → age 出負數 → 被 `metricAgeHours` 嘅 `Math.max(0,…)` 夾平。實測 251/251 張卡 vs effectiveAt = 0h；vs 真 now 係 median 29.2h / max 34.9h。後果：爬蟲死咗要 effectiveAt 推進兩日先夠 48h 爆閘，**偵測延遲約 2 日**。
- 分層防禦：`verify_daily_run.py` 嘅 `price_freshness` 1 日延遲捉「今日冇價格觀測」；validate.ts 48h SLA ~2 日延遲捉「用舊價砌新 snapshot」。兩層互補，唔係重複。

前端**冇**食 `pricePsa10.asOf` 做顯示：[card-detail.tsx](apps/web/src/components/card-detail.tsx) `CardDetail()` 用 `snapshot.effectiveAt || …`、[heatmap.tsx](apps/web/src/components/heatmap.tsx) `CardDialog()` 用 `changePct.asOf ?? …`，兩者都優先其他欄位，所以改 asOf 唔會郁到 UI。

---

# Roster 檔案圖（`data/runtime/private-source-map/`，2026-07-26 實測）

七個 `*-ids.txt` 樣衰都一樣，**唔好見到個名似就當係 roster**。

| 檔 | 數 | 邊個寫 | 邊個讀 | 狀態 |
|---|---:|---|---|---|
| **`tracked-gemrate-ids.txt`** | **1468** | [tracked_universe.py](pipelines/tracked_universe.py) `DEFAULT_GEMRATE_IDS` | **[run_daily.py](pipelines/run_daily.py) `run_market_source_refresh()` default** | ✅ **唯一現行 roster**，DB 100% 有對應 |
| `gemrate-ids.txt` | 600 | [source_crosswalk.py](pipelines/source_crosswalk.py) `main()` `--gemrate-ids-out` | 冇 pipeline 讀 | 07-23 02:11 舊產物 |
| `active-gemrate-ids.txt` | 400 | [active_universe.py](pipelines/active_universe.py) `main()` `--gemrate-ids-out` | `bootstrap_archive.py` | 舊世代 |
| `one-piece-gemrate-population-worklist.txt` | 1597 | [gemrate_candidate_discovery.py](pipelines/gemrate_candidate_discovery.py) `DEFAULT_IDS` | 候選 backfill | 候選池唔係 roster |
| `full-backfill-gemrate-ids.txt` | — | [scripts/backend.py](scripts/backend.py) `run_full_backfill()` | full-backfill profile 專用 | 只喺 overlay 用 |

三個檔互相**唔係子集**：600 ∩ 1468 = 347、400 ∩ 1468 = 290。即係話「600 入面 253 個唔喺 tracked」係**唔同世代嘅候選池未收編**，唔係 daily roster 有窿。凍結掃打 `tracked-gemrate-ids.txt` 就係啱。

## ⚠ CRLF 陷阱（會令你得出完全相反嘅結論）

`tracked-gemrate-ids.txt` 係 **CRLF**，`gemrate-ids.txt` / `active-gemrate-ids.txt` 係 **LF**。

```bash
comm -12 <(sort gemrate-ids.txt) <(sort tracked-gemrate-ids.txt) | wc -l   # → 0，假嘅
```

`\r` 令每個 ID 都唔 match，`comm`/`grep -f`/`sort -u` 全部中招，會報「兩個 roster 零重疊」。**比對 roster 一律用 Python `line.strip()`**（`temp/roster_overlap.py` 就係做呢件事）。ID 一律 40-char SHA1 hex。

---

# DB 家底（2026-07-26 盤點，全文 [docs/DB_INVENTORY_20260726.md](docs/DB_INVENTORY_20260726.md)）

34 張 base table，**得返 6 張真係 0 行**（2026-07-26 用 exact `COUNT(*)` 重驗）。
⚠ **唔准用 `information_schema.TABLE_ROWS`** —— 佢係估算值，喺呢個庫會俾你**完全相反**嘅結論。

| 層 | 實況 |
|---|---|
| raw 價格 `market_price_observation` | ✅ **119,266 行** / 395 卡，2023-06-19 → 2026-07-25，**真 3 年歷史** |
| raw ledger `market_source_observation` | ✅ **337,052 行**，2023-06-19 → 2026-07-26 |
| 成交 `market_sale_observation` | ✅ **93,063 行** |
| 日成交 `market_daily_sales_aggregate` | ✅ **12,078 行** / 395 卡 / 217 日，最新 07-26 |
| **POP** `market_grader_population_observation` | ⚠ 9,538 行 / 1,590 卡，**得 3 日有真量**（詳見下面） |
| **catalog identity** `catalog_variant` | **1,705 行**，全部 07-23 之後建，唔係舊數據 |
| `market_fx_rate_observation` | ✅ **10 行，已接線**（run 96 寫 07-26） |
| 四語 `catalog_variant_locale` | ✅ 1,200 行 |
| 卡圖 `market_image_asset` / `_qc` / `_source_pointer` | ✅ 456 / 456 / 468 行 |
| 跨源別名 `catalog_provider_identity_alias` | ✅ 360 行 |
| 裁決佇列 `market_identity_review_queue` | ✅ **203 行（全部 pending）** |
| 故事 `catalog_story_pointer` | ✅ 466 行 |

**真係仲 0 行嘅 5 張**（07-27 更新：`catalog_printing_identity` 已通電 **68 行**，見 PROJECT_STATE §4 identity-batch）：`market_tracked_sales_aggregate`（已正式廢棄）、
`market_raw_payload_object`、`market_source_observation_payload_pointer`、
`market_source_effective_observation`、`market_retention_archive_manifest`。

**去重結論同直覺相反**：price / population 兩張 fact table 實測 **0% 重複**（UNIQUE KEY 生效，而且真 key 用 `source_code` 唔係 brief 寫嘅 `source_priority`）。真正重複喺 **`catalog_variant` 自己**（24 組 / 52 行 / 3.3%，同一實體卡 2–3 個 variant_id）。收斂應該落 `catalog_printing_identity`（設計上就係做呢件事，07-27 已通電 68 行：19 組真重複收斂 + 10 組假陽性擋低，opaque_id 零變動，證據 `docs/evidence/2026-07-27-identity-batch/`）。

**價格歷史覆蓋率 —— 兩個分母講法唔同，唔好混**：
- 對 **catalog** 全量：395 / 1,705（**23.2%**）
- 對 **roster**（真正要追嘅 1,468 張）：395 / 1,468（**26.9%**）
07-23 之後新入 catalog 嗰批大部分未有任何價。

**per-source 生死**（`market_price_observation` 最後寫入日）：
- `snk_psa10` 07-25（**115,036 行 / 336 卡**，96.5%）
- `ebay` 07-25（**3,824 行 / 343 卡 / 92 個唔同日期**，2026-04-25 → 07-25）—— **唔係 92 行，係 92 日**
- `snkrdunk` 07-24（406 行 / 336 卡，07-19 起）
- `gemrate` = POP 唯一活源，07-25
- **`tag` 由頭到尾只有 07-22 一日**，而且係 POP 唔係價 —— 死因已查實，見下面「TAG 每日死亡」

**POP 每日真相**（唔好再講「5 日」，會誤導）：
| 日 | PSA/BGS/CGC/SGC（gemrate） | TAG | 註 |
|---|---|---|---|
| 07-21 | 641 卡 | — | 真量 |
| 07-22 | **2 卡** | **307 卡** | 呢日嘅量幾乎全部係 TAG 唯一一次寫入 |
| 07-23 | 1 卡（只有 PSA） | — | 空跑 |
| 07-24 | 1,216 卡 | — | 真量 |
| 07-25 | 1,211 卡 | — | 真量 |

即係 **gemrate 得 3 日真量（07-21 / 07-24 / 07-25），TAG 得 1 日（07-22）之後永久斷更**。

`market_ingest_checkpoint` 得 1 行（`cardz_normalized/canonical_batches`），**唔係逐 source 嘅 checkpoint 帳**，想知每個 source 幾時最後寫入要 `GROUP BY source_code` 直接量 fact table。

---

# 前端 → DB 斷點圖（2026-07-26，全文 [docs/ARCHITECTURE_CHAIN.md](docs/ARCHITECTURE_CHAIN.md)）

**先講結論**：唔係「數據唔夠」，係**有數冇接上**同**兩條線從未插電**。

## API 已經齊，唔使起

三個 v1 endpoint（`/api/v1/market`、`/cards/[id]`、`/graders/[grader]`）同網頁**讀同一份 R2 物件**，
唔係第二條數據路。發佈鏈：`canonical_public_snapshot.py` → R2 → pointer `latest.json` → Worker → `assertPublicSnapshot()` → SSR + API。

要修嘅係 **producer**，唔係 API。

三個要記住嘅設計：
- **pointer 做原子切換**，冇半新半舊狀態
- **`runtimeLastGood` in-memory fallback** —— 壞 snapshot 唔會爆版，會**靜靜地停留喺舊數據**（要靠外部監測先知）
- **`listCard()` 會削數據** —— 列表視圖清空 `story`、`historyDaily` 只留 `trackedSalesValueUsd !== null` 嘅點取最後 14 個
  → **stories 缺失只影響詳情頁**，但 **sparkline 完全靠 tracked sales**

## 前端硬契約（違反即 crash / 白版）

- `card.windows[period]` 同 `card.graderPopulations[grader]` **全部 component 冇 optional chaining**
  → 每張卡必須齊 **3 window key × 5 grader key**，缺一個 UI 直接 throw
- [format.ts](apps/web/src/lib/format.ts) `formatMoney()` 開頭嗰兩句 `Number.isFinite` guard：**任何一個 FX rate 唔係 finite → 嗰個幣種下所有錢銀欄位變空白**（唔係 fallback，係整版白）
- 1d/7d/30d 係**純 client toggle**，三個窗口一次過送落去
- 排序 market cap desc **server-side 定死**（`ranked()` top 100）；grader 分頁**重新 assign rank**

## 九個斷點

| # | 前端元素 | 實測現況（2026-07-26 重驗） | 性質 |
|---|---|---|---|
| 1 | 所有錢銀欄位 | ✅ **已接線**：`market_fx_rate_observation` **10 行**，run 96 寫 07-26（JPY 163.69 · KRW 1467.41 · GBP 0.74941 · CNY 6.77 · HKD 7.8418 · TWD 32.343） | ✅ **已解** |
| 2 | 成交額 / sparkline | ✅ **已改讀 `market_daily_sales_aggregate`**（12,078 行 / 395 卡 / 217 日，最新 07-26）。舊 `market_tracked_sales_aggregate` 仍然 0 行兼**正式廢棄** | ✅ **已解** |
| 3 | POP delta / Grading Pulse | `canonical_public_snapshot.py` 仍然**寫死 null**；就算拆咗，POP 得 3 日真量，7d/30d delta 實測 **0/255** | 🔌+⏳ 未解 |
| 4 | 中日韓繁簡文案 | ✅ production error 0；top100 故事 100/100 四語齊、watchlist 譯名 251/251。上線快照四語齊 **236/255** | ✅ **已解，唔再係 blocker** |
| 5 | 市值 delta | ✅ **已修**：producer 出 `windows[w].marketCapChangePct` = `(1+Δ價)(1+ΔPOP)−1` | ✅ **已解** |
| 6 | 成交 delta | ✅ **已修**：producer 出 `windows[w].trackedSalesChangePct` = 本窗口成交額 ÷ 前一個同長度窗口 − 1 | ✅ **已解** |
| 7 | topGrade 標籤 | 大部分出字面 `"top"` 唔係 `"10"`（[canonical_public_snapshot.py](pipelines/canonical_public_snapshot.py) `card_from_row()` 個 `"topGrade"` 欄） | 🐛 低成本 |
| 8 | 7d/30d 價格變動 | candidate snapshot 1,753 / 3,432 行有值；**producer 路徑上 255 張排名卡：1d 255 · 7d 237 · 30d 232** | ✅ |
| 9 | historyDaily | DB 有 597 日 | ✅ |

**上線快照實測覆蓋**（snapshot id=19 / effective 2026-07-25 / 255 constituents，行 producer 真 join path）：
價格 255/255 · POP 255/255 · 市值 255/255 · **卡圖 251/255** · **四語 236/255** ·
delta 1d 255 / 7d 237 / 30d 232 · **POP delta 7d/30d 0/255** ·
sparkline 全期 255/255 但**尾 14 日窗口得 251/255**。

POP 逐 grader（255 張排名卡）：PSA 255 · CGC 248 · BGS 240 · **TAG 224** · SGC 189。
⚠ TAG 嗰 224 張全部係 **07-22 嘅凍結數**，之後永久冇更新過（見下面「TAG 每日死亡」）。

## 市值 delta / 成交 delta（斷點 #5 · #6，2026-07-26 修好）

**一個價格變動率一直扮緊三個指標。** `rankings.tsx` 同一條 `windows[w].changePct`
餵咗市值 delta（`<MetricDelta>`）、成交 delta（`<SalesDelta>`）同最右邊個百分比欄（`metricTone(metrics.changePct)` 嗰欄，呢個先係啱嘅）。

**市值：** 市值 = 價 × POP ⇒ 市值變動 = `(1+Δ價)(1+ΔPOP)−1`，**唔係** Δ價。
POP 只升唔跌 ⇒ 幅度永遠低估；價跌而 POP 升得蓋得過 ⇒ 乘出嚟由負變正，**箭嘴指錯方向**
（seed 100 張入面 14 張中招，production 30d 46 張有數入面 4 張中招）。

**成交：** 成交額同價格變動 % **由頭到尾冇任何數學關係** —— 唔係精度問題，係兩個唔同嘅量。
真數 = 本窗口成交額 ÷ 緊貼前面同長度嗰個窗口 − 1。

**點解揀「複合」唔揀「由 T−N 原始價 × POP 重算」**：`market_metrics.derive_change_windows()`
揀錨點時綁死同一個 `(source, method)`，raw 重算會同已出街嘅 `change_Nd_pct` 對唔上。
複合式用返同一行上面已經顯示緊嘅兩個 %，用戶自己拎計數機都撳得返 —— 內部一致。

**fail-closed，唔准退返去用 Δ價頂替。** 任何一個輸入唔係 ready/stale 就出 null，
UI 顯示空白。頂替就係原本嗰個 bug 本身（見上面「數據語義」硬規矩）。

| | 市值 delta | 成交 delta |
|---|---|---|
| **檔喺邊** | `windows[w].marketCapChangePct` | `windows[w].trackedSalesChangePct` |
| **邊個寫** | [canonical_public_snapshot.py](pipelines/canonical_public_snapshot.py) `compose_change_pct()` | 同左，`ratio_change_pct()` + `latest_sales()` 嘅 `prev_*` 欄 |
| **邊個讀** | [rankings.tsx](apps/web/src/components/rankings.tsx) 個 `<MetricDelta changePct={metrics.marketCapChangePct}>` · [card-detail.tsx](apps/web/src/components/card-detail.tsx) `CardDetail()` 個 marketCap 格（經 [snapshot.ts](apps/web/src/lib/snapshot.ts) mapper） | [rankings.tsx](apps/web/src/components/rankings.tsx) 個 `<SalesDelta changePct={metrics.trackedSalesChangePct}>` · [card-detail.tsx](apps/web/src/components/card-detail.tsx) `CardDetail()` 個成交格 |
| **而家有冇人用** | ✅ 有。schema 欄係 optional，舊 snapshot 冇就由 [derive.ts](packages/market-data/src/derive.ts) `composeChangePct()` 即場砌返 | ✅ 有。**冇 fallback** —— 舊 snapshot 一律空白（成交額砌唔返） |

**兩份實作要一齊改**：Python `compose_change_pct()` 同 TS `composeChangePct()` 係同一條式，
`validate.ts` 有條 invariant 守住「PSA POP 郁過就唔准 marketCapChangePct == changePct」。

**審計腳本** [scripts/audit_snapshot_deltas.py](scripts/audit_snapshot_deltas.py)：
量任何一份 snapshot 嘅兩個 delta 覆蓋率同方向錯誤數，鏡返 `format.ts` `formatDeltaMoney()`
嘅反推所以印出嚟就係訪客見到嗰個銀碼。`--all-windows` 睇齊三個窗口。

## 執行次序

1. ~~**P0-1 FX**~~ —— ✅ 2026-07-26 完成，`market_fx_rate_observation` 10 行，run 96 寫 07-26
2. ~~**P1-1 sales 接線**~~ —— ✅ 2026-07-26 完成，已改讀 `market_daily_sales_aggregate`
3. **P1-2 拆 POP delta 硬編碼** —— 低成本，但**拆咗都未有數**：POP 得 3 日真量，實測 7d/30d delta 0/255。
   要有真 POP delta，前提係**先修好每日 POP run**（第 7 點），唔係拆硬編碼就得
4. **P2-3 topGrade 標籤** —— 順手
4b. ~~**P2-1 / P2-2 delta 正確性**~~ —— ✅ 2026-07-26 完成，見上面「市值 delta / 成交 delta」
5. ~~**P0-2 i18n**~~ —— ✅ 2026-07-26 完成，production error 0。
   **要記住嘅事實**：[validate.ts](packages/market-data/src/validate.ts) `assertPublicSnapshot()` 嘅
   `options.production` 段（`stories are not independently localized` / `story is too short` 嗰堆 assert）
   **只查 top100**，watchlist 只需要
   `names` / `sets`（實測 `watchlist[0].stories` 全 null → 0 error）。所以 watchlist 嗰
   151 條缺故事係**產品完成度問題唔係上線 blocker**，唔好再當死線嘢做。
   ⚠ 唔准機器直譯，跟返上面「卡名中文化」規矩，批量完要用戶過目
6. **P2-1 / P2-2 delta 正確性** —— 等 P1
7. **修每日 POP run 覆蓋率**（歷來最高 81.0% → 要 90%+）—— 唔修，下面個時間表冇意義。
   **包括修 TAG**：TAG 而家每日 fail，全庫得 07-22 一日
8. ~~**P4 eBay 採集**~~ —— **唔係工程項，源已經每日跑緊**（3,824 行 / 343 卡 / 92 日）。
   剩返擴覆蓋率，唔係起採集器

## 時間鎖同結構性上限

| 目標 | 最早 | 註 |
|---|---|---|
| ~~POP 7D「2026-07-28」~~ | **已作廢** | 見下面「POP 時間鎖作廢」 |
| ~~POP 30D「2026-08-20」~~ | **已作廢** | 同上 |
| POP 1D @ 全 roster | 要先修每日 run | 唔係時間鎖，係採集**節奏**問題 —— 見下 |
| POP 7D/30D @ ≥90% roster | 要先補 GemRate 落地檔 | 而家得 123/255 ranked 有歷史檔 |
| **TAG POP 任何 delta** | **無限期** | 全庫得 07-22 一日，源每日 fail，唔修就永遠冇第二點 |
| **價格覆蓋率 ≥90%** | 要新採集 | 1,073/1,468（73.1%）未有過一行價。**唔係「硬上限 18.6%」** —— 見下面 |

### POP 時間鎖作廢（2026-07-26 實測）

「POP 7D 最早 2026-07-28 / 30D 最早 2026-08-20」**已作廢，唔好再引用**。
嗰個推算假設咗 POP 史由 `market_grader_population_observation` 首行（07-21）起計。
實測**唔係**：本機有 3 年週線 POP 史，7D／30D 今日已經計得到。

**孤兒數據歸位 — `history_full.json`**

| 問題 | 答案 |
|---|---|
| 檔喺邊 | `data/private/gemrate/cards/<gemrate_sha1>/history_full.json`，701 個檔（ranked 255 入面 **123** 個有），每個 ~647KB |
| 邊個寫 | [gemrate_source.py](pipelines/gemrate_source.py) `fetch_card()` 嘅 `_save(cdir / "history_full.json", hist)` |
| 邊個讀 | ~~之前冇人讀~~ → 2026-07-26 起 [`pipelines/canonical_public_snapshot.py`](pipelines/canonical_public_snapshot.py) `population_series()` 讀緊 |
| 而家有冇人用 | **有**。POP 7D／30D delta 全部靠佢。之前淨係 [gemrate_candidate_backfill.py](pipelines/gemrate_candidate_backfill.py) `run_offline_backfill()` 檢查過個檔存唔存在，**由頭到尾冇人 parse 過** |

內容：`data.population.population_data.by_grader.{psa,beckett,sgc,cgc}.history`，
157 個週線點（2023-07-29 → 2026-07-25），逐點有 `grades.{psa_10,beckett_10_pristine,sgc_10_pristine,cgc_10_perfect}`。
點距實測 {7 日: 9088, 14 日: 3247}，即係週線為主、部分雙週。**TAG 唔喺 per-card population API 入面，冇歷史來源。**
呢個目錄 gitignore（[.gitignore](.gitignore) 個 `data/private/gemrate/` 條目），缺檔／缺目錄唔係錯 —— 冇歷史就退返 DB 每日觀測，窗口自然報 `accumulating`。

**真正卡住 POP delta 嘅係咩（實測 2026-07-26）**

1. `market_alerts.py` 係 `population_change_7d_pct`/`_30d_pct` **唯一寫者**，
   [`prior_population()`](pipelines/market_alerts.py) 用 `max_gap_days=3`：由 07-25 搵 07-18 錨點，
   窗口 07-15..07-18，而 DB 最早 POP 觀測係 07-21 → **永遠 None**。3,432 行一個值都冇。
   `population_change_1d_pct` 呢條欄**根本唔存在**，1D POP delta 由頭到尾冇 model 過。
2. 就算計到，[`market_rows()`](pipelines/canonical_public_snapshot.py) 個 SELECT **由來冇 select 過嗰兩條欄** → join 0/255。
3. Producer 直接硬編碼 `metric(None, "accumulating", None)`。
   → 三重斷線，拆任何一重都唔夠。**2026-07-26 改成由 `population_series()` 直接計，唔再經 `market_candidate_daily_snapshot`。**

**POP 1D 唔係時間鎖，係採集節奏。** ranked 255 嘅 PSA 觀測日：07-21(255)、07-24(254)、07-25(**得 71**)，
冇 07-22、冇 07-23。所以相鄰兩日成對嘅只有嗰 71 張 → 1D 實測 **70/255 ranked（66/243 published）**。
容差同 [`derive_price_windows()`](pipelines/g10_ingest.py) 對齊（1d±1 / 7d±2 / 30d±3），
**唔准**攞 3 日前嘅點當「1 日變動」——嗰啲係假數。每日 run 補齊，1D 即刻 255/255。

**「硬上限 18.6%」呢句已作廢，唔好再引用。** 舊算法將 catalog 全量做分母、又當 eBay 得 92 行。
實測：roster 1,468 張入面 395 張有價（**26.9%**），對 catalog 1,705 張係 **23.2%**。
上限唔係結構性嘅，係採集量問題 —— 加得幾多源就升幾多。

**eBay 唔係「要由 92 行做起」。** 實測 `market_price_observation` eBay =
**3,824 行 / 343 卡 / 92 個唔同日期（2026-04-25 → 07-25）**，即係**已經每日穩定跑緊 3 個月**，
覆蓋卡數（343）仲多過 snk_psa10（336）。之前「92 行 / 59 variant / 2 個日期」係讀錯咗
（92 係**日期數**唔係行數）。
→ 路線第 4 點「eBay = PSA10 成交唯一真源」**唔再係獨立長線工程，源已經生勾勾**。
   剩返嘅工作係**擴覆蓋率同對數**，唔係由零起採集器。

## 斷點偵測腳本

[scripts/audit_wiring_gaps.py](scripts/audit_wiring_gaps.py) —— 一鍵重驗上面全部斷點（唯讀 SQL）。
發現新嘅「有數冇接線」就加落去，唔好再寫一次性 `temp/` 腳本。

---

# G10 = 我哋個底（2026-07-26 用戶欽點，全文 [docs/G10_BASELINE.md](docs/G10_BASELINE.md)）

用戶定位：「**G10 所有嘢做齊我哋個底**，我哋 base 於佢個底嘅內容，再擴展喺唔同渠道，砌返啲新卡落去。
我哋一定要做得好過佢，但係做到 G10 先係基本。」

即係 G10（`../grade10-scraper/`）**唔係參考網站，係 baseline 數據集**。佢有嘅每一樣我哋都要有。

## 三個一定唔好再撞嘅坑

1. **`summary_jp.json` 唔係日文** —— 實測 480/480 個檔同 `summary_en.json` 逐字相同。
   G10 供應**研究**（2,619 字／張），**唔供應翻譯**。三語仍然要自己寫。
2. **eBay 日期撈埋兩種格式** —— 9,489 條入面 ISO 7,731（2026-04-25→07-21）+ 相對 `N day(s) ago` 1,758（最近 0–3 日），
   零異常。**入桶前一定要用檔案 mtime 解析相對日期**，直接字串 min/max 會得出 `0 day ago → 3 days ago` 呢種廢答案（我中過）。
3. **join 唔可以靠卡名** —— 卡名 exact match 報 251/251 係**假嘅**（641 個目錄得 319 個 unique 卡名）。
   唯一有證據嘅係 `catalog_source_identity`（395/1590 variant）。系列名格式兩邊完全唔同，加埋只對到 50/251。

## 最大單一缺口

**eBay PSA 10 成交：G10 本機有 559 卡 / 9,489 條，我哋 DB 得 92 條 / 59 卡。**
呢個直接兌現路線更新第 4 點「eBay = PSA10 成交唯一真源」，數據一早喺本機硬碟，冇人讀過。
**未接，因為** 要先寫日期解析同擴 identity 覆蓋率；**接線工作項** = `docs/G10_BASELINE.md` §4 第 1、2 項。

## 四語文案（2026-07-26 已駁線 —— 故事正源已由 JSON 搬入 DB）

**故事**同**譯名／套名**係兩條唔同嘅線，唔好撈埋。

### 故事：`catalog_variant_locale` 係正源，JSON 淨係 fallback

| | |
|---|---|
| **檔喺邊** | DB 表 `catalog_variant_locale`（`variant_id`, `locale_code`, `market_story`…），PK `(variant_id, locale_code)` |
| **邊個寫** | 英文原文 = [pipelines/g10_research_ingest.py](pipelines/g10_research_ingest.py)（G10 研究文，run 91 收 350 條）；三語譯本 = [pipelines/editorial_locale_sync.py](pipelines/editorial_locale_sync.py)（食 `temp/translate-out-*.json`，`--write` 先真寫） |
| **邊個讀** | [pipelines/editorial_localization.py](pipelines/editorial_localization.py) `_db_stories()` → [canonical_public_snapshot.py](pipelines/canonical_public_snapshot.py) `build_snapshot()` → `localize_cards(cards, connection=connection)` |
| **而家有冇人用** | ✅ 每次出 snapshot 都行。`coverage_summary()` 逐次印 `[N db / M json]` 分源計數 |

**點解要搬**：`data/editorial/top100-stories.json` 用公開 `cmc_*` 做 key，而
`cmc_* = sha256(tcg, language, set_name, collector, name)` —— catalog 一改名就漂移，
實測 100 條得 50 條仲對得返現行 top100。DB 表用唔漂嘅 `variant_id` 做 key，
`_db_stories()` **喺讀嗰刻先 join `catalog_variant.opaque_id`** 出當日公開 id，所以冇得漂。
JSON 保留做 fallback（DB 有嗰條贏），等舊 100 條唔使即刻重譯。

**英文行 lead，翻譯原封**：DB 嘅 `en` 行係 G10 研究全文（2,025–5,551 字，5 段：開場敘述 +
`Basic Info` / `Community Pulse` / `Card Fun Facts` / `Summary (TLDR)`）。結構化段係研究筆記唔係
出街文案，`story_lead()` 切喺第一個已知標題之前 —— 對英文係真切（→ 442–934 字），
對翻譯係 no-op。標題用**明確詞彙表**唔用「似標題」啟發式：實測 5 條研究文有
`Main subject / card theme:** Rayquaza V` 呢類粗體 bullet，啟發式會喺嗰度切錯。

**隊列由排名榜倒推，唔好由 snapshot 倒推**（2026-07-26 改）：
[pipelines/editorial_translate_queue.py](pipelines/editorial_translate_queue.py) 直接讀
`market_index_constituent`，**唔再收 `--snapshot`**。原因同下面「published id 漂移」一樣：
舊版攞 snapshot 嘅 `card["id"]` join `catalog_variant.opaque_id`，251 張出版卡得 75 張對得返，
其餘 176 張明明 DB 有 2,700 字研究文都被當成「冇英文原文」——**同一個閘遮住咗啲字，
又遮住咗解遮嘅工作**。排名榜本身就係攞 `variant_id` 做 key，冇得漂。
（出版名單 = 排名榜減走 `image_unavailable` 嗰 11 張，嗰批補完圖照樣上榜，唔算榜外卡。）

隊列只收「DB 已經有英文研究文」嘅卡；冇英文原文係 `g10_research_ingest.py` 覆蓋率問題，
**唔好混做翻譯問題**（實測 262 張排名卡入面 19 張真係連英文都冇）。

完整流程：
```
editorial_translate_queue.py --batch-size 20          # → temp/translate-in-N.json
（翻譯 agent 寫 temp/translate-out-*.json）
editorial_locale_sync.py --write                      # 灌返入 catalog_variant_locale
canonical_public_snapshot.py --output temp/x.json     # 再出，故事自動貼上去
```

**published id 漂移 → `story_keys`**（2026-07-26 修）：`canonical_public_snapshot.py` 出
snapshot 嗰陣，tier-1/2 卡個 `id` 係由 presentation pack **凍住抬過嚟**，而 `_db_stories()`
張表係用當日 `opaque_id` 做 key —— 兩者對唔上。所以 producer 即場砌一個
`story_keys={出版 id: 當日 opaque_id}` 傳落 `localize_cards()`，查表時 canonical 優先、
查唔到先回落出版 id（雙 key：JSON fallback 表仍然係用舊 `cmc_*` id，淨查一邊會蝕返 2 條）。
**公開 `id` 一個 bit 都冇郁。**

實測（同一個 producer 乾淨 A/B）：舊 keying `stories 143 attached [63 db / 80 json]`，
新 keying `143 attached [129 db / 14 json]`。總數一樣，但 66 條由「JSON 舊副本」變返
「DB 正源」—— 而 JSON 得 100 條、新譯嗰 114 張一條都冇，**冇呢個修正翻譯做完會上唔到街**。

測試：`tests/test_editorial_localization.py`（34 條，守 `story_lead()` 切位、`_accept()` 四閘、
DB 贏 JSON、分源計數）+ `tests/test_editorial_locale_sync.py`（34 條，守入庫閘）。

### 譯名／套名：仍然係 flat JSON

| | |
|---|---|
| **檔喺邊** | `data/editorial/{card-names,set-names}.json` |
| **邊個寫** | 人手 + 背景 agent；set-names 有 [docs/SET_NAME_REVIEW.md](docs/SET_NAME_REVIEW.md) 全 172 行對照 |
| **邊個讀** | 同上 —— `editorial_localization.py` `_entries()` |
| **而家有冇人用** | ✅ `names` 242 譯/9 英文 fallback、`sets` 251 譯/0 fallback |

呢兩個用**英文原文字串**做 key 唔係 `cmc_*`，所以冇漂移問題，唔急住搬 DB。
但記住卡名 display 層真源係 `apps/web/src/lib/card-names.ts`，JSON 只係佢嘅 dump。

### 兩種 fallback policy 唔同，唔好一刀切

`stories` 揾唔到留 **null** 唔准填英文（[validate.ts](packages/market-data/src/validate.ts) `assertPublicSnapshot()`
嗰句 `stories are not independently localized` 要四語互不相同，塞英文即刻違規）；
`names`/`sets` 揾唔到**保留英文**（對齊前端 `localizedCardName()` 一路以來嘅行為）。

# 洗數據 / 入庫接口（2026-07-26 實測，全文 [docs/DATA_NORMALIZATION.md](docs/DATA_NORMALIZATION.md)）

**統一入庫接口已經有，就係 `market_source_observation`（277,127 行）。**
5 個源全部走呢張表：`(run_id, source_code, external_entity_id, observation_kind, effective_at,
observed_date, payload_sha256, payload_json)`。加新源唔使改 schema，寫 `source_code` + 塞 JSON 就得。
原始 payload 原封留低 + sha256 內容定址 → 追溯得返。

## 三個一定要知嘅事實

1. **判定層唔係死嘅 —— 係響咗好多次冇人聽。**（2026-07-26 用 exact `COUNT(*)` 重驗，
   之前「全部 0 / 從來冇響過」嘅講法**完全相反**，唔好再引用）
   - 94 次 run 累計：observed **2,393,379** · accepted **554,828** ·
     **quarantined 70,474（13 次 run）** · **rejected 32,738（8 次 run）**
   - 最大隔離源：`g10_analytics` **69,393**；最大拒收：`snkrdunk` **19,190** + `snk_grade` **13,226**
   - `metric_status` 已經唔係單一值：`market_candidate_daily_snapshot`
     ready **1,834** / accumulating **1,570** / unavailable **28**
     （`market_index_constituent` 同 `market_price_observation` 就仍然係單一 `ready`）
   - `coverage_status` 逐張表係單一值，但**唔全部係 `partial`**：
     sales_aggregate `partial` · sale_observation `partial` ·
     **`market_alert_evaluation` 全部 `blocked`**
   - `market_identity_review_queue` **203 條全部 pending**，全部 G10 家族：
     `g10_collector_number_mismatch` 92 · `g10_name_not_in_catalog` 74 ·
     `g10_variant_duplicate_conflict` 17 · 其餘 20
   - `identity_status` 就真係仍然全部 `confirmed`（1,705/1,705）—— 呢粒仲啱

   **真正嘅問題唔係「冇人判」，係「判咗冇人跟進」**：10 萬條數據被隔離／拒收、203 條身分爭議
   排住隊等裁決，全部靜靜躺喺度。呢個先係路線第 6 點「靜靜地少咗嘢係最陰險嘅失敗」嘅實例。
   **下一個 agent 嘅動作唔係「幫判定層通電」（已經通咗），係「開 quarantine / review queue 嚟睇」。**
2. **冇共用 normalizer 模組。** `normalize_collector` **3 份**、`normalize_language` 2 份、
   日期解析 **5 份**，散落 11 個檔案。而
   `opaque_id = sha256(tcg, language, set_name, collector.normalized, name)`
   （[g10_public_snapshot.py](pipelines/g10_public_snapshot.py) `opaque_id()`），
   即係 **normalizer 唔一致 = 同一張卡分裂成兩個 id = 價格同 POP 史各自孤立**。已出過事兩次。
3. **洗數據層剩返 2 張 0 行**（唔係 11 張，2026-07-26 重驗）：
   `market_raw_payload_object` + `market_source_observation_payload_pointer`（原始 payload 歸檔）
   —— 呢兩張真係從未寫過，即係**原始 payload 冇獨立歸檔**，追溯淨係靠
   `market_source_observation.payload_json` 本身。
   已經通電嘅：`catalog_provider_identity_alias` **360 行**（跨源別名對照）、
   `market_identity_review_queue` **203 行**（對唔到嘅已經入咗隊，等人裁）。

## 硬規矩

**爛卡名／爛 collector number 一律行 display 層修，唔准改 DB `canonical_name` 或
`collector_number`** —— 會換 `opaque_id`、孤立價格 / POP 史、撞
[db_runtime.py](pipelines/db_runtime.py) `upsert_variant()` 嘅
fail-closed identity assert（`ValueError: opaque_id canonical identity changed`）。
做法：`display` 同 `normalized` 解耦，id 一個 bit 都唔郁
（2026-07-26 修 6 個 subset 分母就係行呢條路，已驗證 opaque_id 完全冇變）。

卡名 display 層真源係 **[apps/web/src/lib/card-names.ts](apps/web/src/lib/card-names.ts)**，
`data/editorial/card-names.json` 只係佢嘅 dump（`entries` key 底下，重跑 `temp/dump-card-names.mjs`）。
**改譯名改 .ts，唔好改 JSON。**

**接線工作項已經變咗**（原本 `docs/DATA_NORMALIZATION.md` §5 第 1–3 項「判定層通電 → 共用
`pipelines/normalize.py` → alias/review queue 通電」，其中第 1 同第 3 項**實測已經通咗**）：
1. ~~判定層通電~~ ✅ 已通（quarantine 70,474 / reject 32,738 實際發生過）
2. **共用 `pipelines/normalize.py`** —— 仍然未做，`normalize_collector` 3 份散落 11 個檔，係真風險
3. ~~alias / review queue 通電~~ ✅ 已通（alias 360 行 / queue 203 行）
4. **新增：處理積壓** —— 203 條 pending 裁決 + 10 萬條 quarantine/reject 冇人睇過，
   呢個而家係最大嘅未知數據債

---

# TAG 每日死亡（2026-07-26 查實，唔好再當「生死未確認」）

> ⚠ **07-27 注記（pm-closeout）**：PROJECT_STATE §0.5 實測 **07-26 有 324 行
> `source_code='tag'` POP 寫入**（@verified 戳 `id=db.pop_obs.tag_0726`），
> 「全庫得 07-22 一日」已被推翻 —— 本章節嘅死亡結論**引用前要重驗**。
> 死因分析（1997 爛行 raise）本身可能仍然成立，但「之後零」呢個量測已過時。

**一句講晒**：TAG 唔係「靜默」，係**每日 fail 得好準時**。全庫得 **07-22 一日 / 307 卡**，之後零。

## 唔係三個源死咗，係一個 grader 死咗

之前以為 `ebay`(22) / `snkrdunk`(285) / `tag`(206) 三個源同時凍結喺 07-22。
實測：嗰三個數字係**同一件事嘅三個 `source_code` 鏡頭** —— 全部係
`grader_population_tag` 嘅行數。三個加埋 = 307 張唯一卡，就係 07-22 嗰一日。
- `ebay` PSA 最後寫 07-24、`snkrdunk` PSA 最後寫 07-24 → **兩個源本身生勾勾**
- `gemrate`（POP 唯一活源）出 BGS/CGC/PSA/SGC 到 07-25，**由頭到尾冇出過 TAG**

→ TAG 得**一條授權路徑**：`tag_daily_capture.py` → `market_source_sync` `grader_population_tag`，
而佢**一世只成功過一次**。

## 死因（有 log 為證）

[tag_pop_data.py](pipelines/tag_pop_data.py) `dump_fresh()`：
```python
if not brand_name or not set_name:
    raise RuntimeError(f"TAG set identity is incomplete for {year}")
```
呢句喺 `dump_fresh` 嘅 `for year_row ... for set_row ...` 雙層迴圈**入面**，
所以 **1997 年一條爛行 = 成個多年份 catalog dump 全部 abort**。
實測 `data/tag/pops_pokemon.jsonl` 1997 年 144 行入面有 **3 行 blank identity**。

`data/runtime/logs/daily_staging_off_20260726_141239.log` 今日仍然係：
```
RuntimeError: TAG set identity is incomplete for 1997
"tagCards": 0, "tagMissing": 1241, "tagObservations": 0
"tagLive": false, "tagStatus": "unavailable"
```

## 點解 07-22 之後連 fallback 都冇

`run_daily.py` catch 到之後叫 `latest_tag_catalog(..., max_age_hours=72)`。
07-23 嗰次 fallback **成功過一次**（重用 07-22 嘅數，所以 DB 入面得 07-22 呢個日期），
之後 cache 過咗 72 個鐘就返 `None`，每日 `tag_status = "unavailable"` 靜靜跳過。

## TAG **有**接入排程，唔好再查

[run_daily.py](pipelines/run_daily.py) `run_market_source_refresh()` 有叫 `tag_daily_capture.py`。
**唔係「未排程」，係「排咗每日 fail」。** 修法係一行級：
爛行改成 skip + warn 而唔係 raise（或者喺 `dump_fresh` 入面逐 set try/except）。

## 168h 新鮮度閘**唔會**爆 —— 但呢個係壞消息唔係好消息

[validate.ts](packages/market-data/src/validate.ts) 個 `POPULATION_FRESHNESS_HOURS` 閘
淨係讀 `card.populationPsa10`，而 [canonical_public_snapshot.py](pipelines/canonical_public_snapshot.py) `card_from_row()` 寫嘅呢個欄
**只由 `grader_code = 'PSA'` 嘅行嚟**（gemrate 供，07-25 健康）。
`graderPopulations['TAG'].asOf` **由頭到尾冇任何地方查過年齡**，
`latest_populations()` 又**冇 date floor**。

→ 07-29 唔會爆版。**真正風險係相反：TAG 個 07-22 數會永遠餵落去，
   `status` 照報 `"ready"`，validator 一聲都唔出。**
   即係「靜靜地餵過期數據俾訪客」，同路線第 6 點嗰個最陰險嘅失敗模式一模一樣。
   要修就要主動加 per-grader 年齡閘，唔好等 168h 閘救你。

---

# 68 個 critical alert = **一個** cold-start artifact，唔係 68 個問題（2026-07-26 查實）

**唔好逐張卡去查。** 68 個全部 `alert_type='entered_top100'`，其中 **65/68 `previousRank` 係 null**。

## 機制

`market_alert_evaluation` 嘅 `effective_date` **係亂序寫入**嘅（照 id 排）：
```
id 1→07-23  2→07-22  3→07-23  4→07-24  5→07-21  6→07-24  7→07-24  8→07-25
```
`previous_snapshots()` 因此揾唔返「前一日」嘅 eligible_rank，
於是每張 top100 卡都好似係全新入榜 → [pipelines/market_alerts.py](pipelines/market_alerts.py)
嘅 `previous_rank is None or previous_rank > 100` 全部命中。

即係**一次 backfill 亂序造成嘅 cold-start 假象**，唔係 68 件事。

## 訪客睇唔到 alert，唔使驚

[canonical_public_snapshot.py](pipelines/canonical_public_snapshot.py) 全份**得 `latest_generation()` 一處**掂到 alert 表，
而且只攞 `id` + `effective_date` 做 join key，
**從來冇讀過** `market_alert` / `market_alert_event` / `coverage_status`。
`apps/` 同 `packages/` grep `alert` = 零。→ **alert 數據錯唔會令訪客見到錯數。**

## 但係有一條隱性路徑會靜靜咁食掉 delta

Producer 揀「`effective_date <= 快照日` 入面最新嗰個 evaluation」。
因為亂序寫入，**如果某日最新嗰個係退化 evaluation，delta join 會靜靜少行**。
實測 **id=4 只有 75 行 eligible（`top100_cutoff_usd` 仲係 NULL）**，
而正常係 194～270 行。今日夾啱冇事（eval 07-25 對 snap 07-25，255/255），
但**呢條係 alert 層唯一去到訪客嘅路**，改 alert 相關嘢之前要睇住。

## `coverageStatus: blocked` **唔係**數據錯誤旗

8 次 evaluation 全部 `blocked`，唯一原因係 `unresolved_high_potential_count` 唔係 0
（最新 **124**，由 56 升上嚟）。呢個係**內部發現雷達嘅治理旗**，
**唔會**閘住公開快照。見到 `blocked` 唔好當係「數據壞咗」去查。

---

# 多 agent 協作衛生（2026-07-26 新增制度）

呢個 project 嘅 agent 失效模式**唔係**「揾錯檔、猜錯路徑」——
係 **agent 成功讀到啱嗰份權威文檔，而份文檔啲數字爛咗，於是做錯方向**。

實例（同日一次審計揾到）：文檔話 FX 表 0 行從未接線（真係 10 行）、
quarantine/reject 全 0 閘從未響（真係隔離 70,474、拒收 32,738）、
eBay 92 行 59 卡（真係 3,824 行 343 卡，差 40 倍）、review queue 0 條（真係 203 條 pending）。
四單全部係「讀對咗檔，俾內容呃咗」。目錄結構、路徑規範、「唔好猜路徑」——**一單都醫唔到**。

以下三條就係醫呢件事。

## 1. 數字要帶驗證戳（hard）

**任何講具體數字嘅段落，必須帶一個 `@verified` 戳，寫明幾時量、點重量。**
冇戳嘅數字下一個 agent 有權當佢係未知，重量過先用。

戳係 HTML comment，貼喺句子後面：markdown render 出嚟睇唔到，但改動句子時會一齊搬走。
**唔准另開一份人手維護嘅清單** —— 清單同句子一定會分家，分家之後清單就係第二份要腐爛嘅嘢。

格式（欄位次序隨便，`sql=` 一定要擺最後，佢食到 `-->` 為止）：

| 欄 | 意思 |
|---|---|
| `<YYYY-MM-DD>` | 量度日期，緊貼 `@verified` 之後，**必填** |
| `id=<slug>` | 全 repo 唯一，跨檔都唔准撞（有測試守） |
| `expect<op><value>` | `op` = `=` `>=` `<=` `>` `<` `!=`。同 SQL 第一行第一欄比 |
| `ttl=<days>` | 幾多日後當過期，預設 **3** |
| `sql=` | **單條**唯讀語句。多行會自動收成一行 |

**例**（呢粒係真嘅，跑 `verify_claims.py` 會即場重量）：

`catalog_variant` 今日 1,705 行。
<!--@verified 2026-07-26 id=claude.example.catalog_variant expect>=1705
    sql=SELECT COUNT(*) FROM catalog_variant-->

**只升唔跌嘅計數器一律用 `expect>=`，唔好用 `expect=`。**
用 `=` 嘅話聽日入多一張卡就報 DRIFT，報幾次之後所有人就當份報告係噪音——
呢個係最常見嘅自殺方式。

### 過期規則：**3 日**（hard）

**超過 3 日冇重驗過嘅數字 = 未知數，唔准當事實引用，要重量過先講。**

點解係 3 日唔係 7 日或者 30 日：上面四單腐爛**全部**係每日 pipeline 喺文檔寫低之後
**72 個鐘內**整出嚟嘅。7 日嘅窗口對呢個 project 嘅腐爛速度嚟講，等於冇閘。
慢變嘅事實（例如「呢張表設計上就係 0 行」）自己 `ttl=14` / `ttl=90` 開長佢，
**逐粒開，唔好調全域預設**。

### 戳要寫低「量嗰陣個對象係邊個」（hard，2026-07-26 升級）

**日期唔夠。戳要帶量度對象嘅身分——而且要一個會變嘅識別，唔係一條路徑。**

路徑係穩定嘅，所以路徑證明唔到嘢：`data/public/seed-snapshot.json` 今日同上星期同名，
但已經係兩份唔同嘅檔。要記低嘅係**內容一變就跟住變**嗰種識別。

| 量緊咩 | 要記低嘅身分 |
|---|---|
| snapshot 入面嘅數字 | `generation.id`（例：`daily_20260722T094826496874Z`）|
| 代碼行為、「得 N 處」 | `git rev-parse HEAD`，同埋講明 working tree 乾唔乾淨 |
| 檔案內容 | `sha256` 或者 `git hash-object` |
| DB 數字 | 已經有 `sql=`，佢本身就係重量方法 |

點解升做硬條文：見下面實例 ③。一份**正式交接文檔**嘅核心結論，
係對住一份唔應該存在、而家已經冇咗嘅檔量出嚟。**只有記低咗對象身分先至偵測得到**——
當時如果淨係寫「2026-07-24 量」，今日睇落去會**永遠都似係啱**。

### 全域否定結論要特別標注（hard，2026-07-26 升級）

「**只有 N 處**」「**冇人 call 呢個**」「**全部都係 X**」——呢類**全域否定**係最脆弱嘅結論：
**加一行就推翻**，而且推翻嗰刻冇任何嘢會報錯。

寫呢類句子一定要同時寫低：**量度時間點** + **搵法（`grep` pattern）** +
**一句明講「可能有平行 agent 改緊」**。搵法要寫，係為咗下一個人重量得返，唔使自己重新諗點搵。

> 寫法示範：
> 「`metricAgeHours` 得 2 處 call site，兩處都係 PSA，所以 TAG 冇 age gate。」
> ——2026-07-26 量（HEAD `41a1514`），`grep -rn 'metricAgeHours(' packages/market-data/src/` 重量。
> ⚠ 呢句係全域否定，同期有其他 agent 改緊 `validate.ts`，引用之前重量過。

### 點跑

```bash
set -a && . data/runtime/config/backend.env && set +a
python -X utf8 scripts/verify_claims.py                    # 掃晒
python -X utf8 scripts/verify_claims.py CLAUDE.md          # 淨係一份
python -X utf8 scripts/verify_claims.py --json --out temp/claims.json
```

出五種狀態，**`DRIFT` 同 `STALE` 意思完全唔同，唔好撈埋**：

| | 意思 | 你要做咩 |
|---|---|---|
| `OK` | 冇過期，而且數字仲啱 | 冇嘢做 |
| `DRIFT` | **數字變咗** | 改份文，改完重新戳今日日期 |
| `STALE` | 數字啱，但太耐冇人驗 | 確認完更新戳日期就得，唔使改字 |
| `ERROR` | 個戳自己壞咗 / SQL 出錯 | 修個戳。**壞戳 = 冇人守嗰句嘢** |
| `SKIPPED` | `cmd=` 戳而冇俾 `--allow-cmd` | 有心嘅預設，要跑先自己加 flag |

exit `0` 全綠 / `1` 有 drift 或過期 / `2` 連唔到 DB（**唔等於全綠**）。

**呢個腳本唯讀**：唔會改文檔、唔會寫 DB。只准 `SELECT` / `SHOW` / `DESC` / `EXPLAIN` / `WITH`，
擋走接駁語句、comment 匿埋嘅寫入、`INTO OUTFILE`。
⚠ **`information_schema.TABLE_ROWS` 直接拒收** —— 佢係估算值，
2026-07-26 實測佢報 `catalog_variant` 1,590 行而 exact `COUNT(*)` 係 1,705。
**一律 `COUNT(*)`。**

## 2. 開工前查認領表（hard）

7 個 agent 同時改一個 repo。**落第一個 edit 之前查 [FILE_CLAIMS.md](FILE_CLAIMS.md)，
收工前刪返自己嗰行。**

- 租約 **4 個鐘**。過咗就係死 claim，直接攞，順手刪咗佢嗰行。
- 做緊嘢做過 4 個鐘：**更新自己個 `Since` 做而家**，冇另一個續期機制。
- 撞正人哋 live claim：**唔准「小心啲改」**。對方個檔喺佢 context 入面，
  佢寫返落去嗰刻你嘅改動連睇都冇人睇過就冇咗。行第二樣嘢，或者話俾 PM 聽。
- 熱門檔清單喺 `FILE_CLAIMS.md` 入面。**`CLAUDE.md` 同 `PROJECT_STATE.md` 係最惡嗰兩個**——
  因為個個 agent 都被要求收工前更新，所以撞車全部集中喺大家收工嗰一刻。
  claim → 改 → 即刻放，唔好攞住佢做成件長工。

**唔准開 per-agent worktree / sandbox 做隔離。** 大家夾份寫同一個 Next.js app 同一個 DB，
隔離嘅 merge 成本高過撞車成本，呢條路已經否決咗。

## 3. 診斷結論要歸位（hard）

做完診斷／審計，**有結論價值嘅嘢唔准留喺 `temp/`**。
`temp/` 係垃圾崗：同一日出咗 15 個診斷檔，幾個貴到要行全表掃，冇一個下一個 agent 揾得返，
於是下次由零查過——而且好可能查出**唔同嘅答案**，因為平嗰個問法會俾你錯嘅答案。

搬入 [docs/evidence/](docs/evidence/)`YYYY-MM-DD-<slug>/`，**三個條件要齊**：
**有結論**（一句你肯守嘅說話，唔係一堆數據）· **會再問**（覆蓋率、漂移、缺口呢類問題一定重複）·
**貴**（全表掃、重砌 snapshot、跨源 join）。三者缺一 → 留喺 `temp/` 等死，冇所謂。

每份 `FINDING.md` 四樣缺一唔可：**量度日期 · 點量（可以直接跑嘅指令／SQL）· 結論一句 ·
量度時嘅前提**。前提最容易漏又最重要——冇前提，過咗期嘅 finding 就只係「錯」，
有前提先至係「當時啱，因為 X 變咗所以而家唔同」。

**producer script 要同 output 擺埋一齊**：冇人重跑得返嘅 output 唔係證據，係一句斷言。
搬完記得改 `ROOT = Path(__file__).resolve().parents[N]` 個深度，
呢個係**唯一准許改**嘅嘢。詳細規矩 + 點讀舊 finding 睇 [docs/evidence/README.md](docs/evidence/README.md)。

**寧願少搬。** 咩都收嘅目錄就係換咗個靚名嘅 `temp/`。

## 4. 引用代碼唔准用行號（hard，2026-07-26 升級）

**行號係成個 repo 入面腐爛得最快嘅引用。** 而且佢壞法特別陰險：
路徑寫錯會揾唔到檔、即刻報錯；**行號寫錯係指住另一段正常運作、睇落好合理、但完全無關嘅代碼**。
讀嘅人唔會知自己俾人呃咗。

### 穩定度階梯

| | 寫法 | 點解 |
|---|---|---|
| ✅ **最好** | **symbol 名**：`evaluate()` 個 `INSERT IGNORE`、`PRESENTATION_VIEW_MIN_COVERAGE` | 改名先會壞，而改咗名 `grep` 即刻揾到——壞得**大聲** |
| ✅ 可以 | **markdown link 去個檔**：`[validate.ts](packages/market-data/src/validate.ts)` | 檔搬走 → link 斷 → 偵測得到 |
| ❌ **禁用** | **裸行號**：`validate.ts:289`、`第 51–52 行`、`（:641）` | 上面插一行就靜靜咁指錯位 |<!--docref:example-->

**行號唯一准出現嘅場合**：你貼出嚟嘅 `grep -n` 原始輸出。一寫入 `.md` 就要換成 symbol 名。

### 插咗行就要 grep 下游（hard）

**喺任何檔中間插入／刪除超過 10 行，收工之前要 `grep` 全 repo 睇下有冇人引用緊嗰個檔嘅行號。**

```bash
grep -rn '<檔名>:[0-9]' --include='*.md' --include='*.py' --include='*.ts' .
```

呢條唔係禮貌，係責任：實例 ② 就係有人喺 `canonical_public_snapshot.py` 中間插咗約 80 行，
一個錯咗嘅行號由文檔 → 抄入 task brief → 再抄入 `scripts/audit_wiring_gaps.py`。
**文檔腐爛會污染代碼，唔止污染 agent。**

### 掃描器

```bash
python -X utf8 scripts/verify_doc_refs.py                   # 掃 CLAUDE.md / PROJECT_STATE.md / docs/**
python -X utf8 scripts/verify_doc_refs.py --json --out temp/docrefs.json
python -X utf8 scripts/verify_doc_refs.py --strict          # 連 BARE 都當 fail
```

分類：`BROKEN`（檔冇咗）· `DRIFT`（行號似乎移咗位）· `RENAMED` · `AMBIGUOUS` ·
`BARE`（裸行號，即係上面禁嗰種）· `TEMPDEP`（指住 `temp/`）· `OK`。

⚠ **`DRIFT` 係啟發式，實測約 25% 假陽性**，所以佢**嘈住報但唔會 fail**（exit 0），
除非你自己開 `--strict`。**啟發式閘唔准喺擲毫嘅準確度下硬 fail** ——
報幾次假警之後大家就當佢係噪音，跟住連真嘅都唔會睇。
⚠ 佢**捉唔到**中文式「第 N 行」寫法，嗰啲要人手 `grep '第 [0-9]\+ 行'`。

**要引用一個裸行號做反面示範**（好似上面個階梯表），喺嗰行尾加 `<!--docref:example-->`。
**逐行標，冇檔案級豁免**——一個 file-level opt-out 會令一個示範遮住下面所有真嘅爛引用，
掃描器就會喺最需要出聲嘅位靜咗。呢個限制有反向測試守（`ExampleMarkerTests`）。

### 唔准指住 `temp/`

**排程／CI／systemd／文檔嘅穩定指引，一律唔准指住 `temp/` 入面嘅檔。**
`temp/` 冇人保證存在。`verify_doc_refs.py` 見到就報 `TEMPDEP` 並且**硬 fail**。

### 驗證腳本自己要有反向測試（hard）

**寫嚟守嘢嘅腳本，一定要有「餵佢一定要 fail 嘅輸入」嗰種測試。**
`verify_handoff.py` 曾經用 `git ls-files --error-unmatch` 查檔存唔存在——嗰個查嘅係 **index**
唔係 `HEAD`：一個 `git add` 過但未 commit 嘅檔照樣過閘，**個綠燈當時乜都證明唔到**。
（要查 HEAD 就用 `git cat-file -e HEAD:<path>`。）
`tests/test_verify_doc_refs.py` 嘅 `ReverseTests` 就係專門做呢件事。

## 點解上面幾條要升做硬條文：三單實例

CLAUDE.md 本身有條規矩——**同一類錯誤累積到 3 次就升做硬條文**。
「量度有保質期」已經夠三次，所以上面幾條由建議升做 hard。

**三單全部都唔係 agent 唔老實，三單都係制度冇要求標注時效。**
叫人「小心啲」對呢三單一單都冇用——因為三個 agent 當時全部都係啱。

| # | 發生咗咩 | 制度缺陷 | 已寫入邊條 |
|---|---|---|---|
| ① | Agent X 老實報告：`metricAgeHours` 得 2 處 call site、兩處都係 PSA，所以 TAG 冇 age gate。**量嗰陣完全正確**（`git show HEAD` 實證 `assertGraderPopulationFreshness` 出現 **0** 次）。Agent Y 之後加咗 `assertGraderPopulationFreshness()`，working tree 而家 **3** 次，TAG 有閘。**X 冇錯，個 repo 變咗。** | 全域否定 claim 冇要求標注「量度時點 + 可能有平行 agent 改緊」 | §1「全域否定結論要特別標注」 |
| ② | 有人喺 `canonical_public_snapshot.py` 中間插咗約 80 行。一個錯咗嘅行號（文檔寫 `656-658`，真身 `733-735`）由文檔 → task brief → 抄入 `scripts/audit_wiring_gaps.py`。 | 冇禁裸行號，亦冇「插完行要 grep 下游」呢個責任 | §4 全條 |
| ③ | `docs/AWS_DEPLOY.md` §8.3 話 `MARKET_DATA_SNAPSHOT_PATH` 壞咗，因為 6 張 subset 卡（`GG69`/`GG44`/`SV49`/`SV107`/`GG70`/`TG20`）缺分母 → 503，結論係「rebuild 係唯一出數據方法」。**呢個結論係對住 `canonical_20260724_0a295bbce68a` 量出嚟——嗰份係違規 commit 咗入 demo seed 嘅生產數據，後來已 revert。** 2026-07-26 重量：working tree 個 seed 係 `daily_20260722T094826496874Z`，`collectorNumber complete=False` = **0 張**，個 503 **重現唔到**。 | 戳只有日期、冇量度對象身分 | §1「戳要寫低量嗰陣個對象係邊個」 |

③ 最值得記：**得個對象身分令佢偵測得到**。如果當時淨係寫「2026-07-24 量度」，
今日再讀會**完全睇唔出有問題**——日期喺度、結論具體、有卡號有 error code；
而下一個 agent 會照信，然後照住「rebuild 係唯一出數據方法」去砌成個部署架構。

## 呢四樣嘅檔案地圖

| | 檔喺邊 | 邊個寫 | 邊個讀 | 而家有冇人用 |
|---|---|---|---|---|
| 驗證戳掃描器 | [scripts/verify_claims.py](scripts/verify_claims.py) | — | agent 手動跑；`tests/test_verify_claims.py` 守戳法同唯讀閘 | ✅ 上面個例真戳跑得過 |
| 唯讀 SQL runner | [scripts/ro_sql.py](scripts/ro_sql.py) | — | agent 手動跑 | ✅ 由 `temp/ro_sql.py` 歸位；唯讀閘直接 import `verify_claims.check_read_only`，全 repo 得一個定義 |
| 認領表 | [FILE_CLAIMS.md](FILE_CLAIMS.md) | 每個 agent 自己加／刪一行 | 每個 agent 開工前 | ✅ |
| 證據庫 | [docs/evidence/](docs/evidence/) | 做完診斷嘅 agent | 下一個問同樣問題嘅人 | ✅ 首份 `2026-07-26-image-coverage/` |
| 引用掃描器 | [scripts/verify_doc_refs.py](scripts/verify_doc_refs.py) | — | agent 手動跑；`tests/test_verify_doc_refs.py`（含 `ReverseTests`）守分類同唯讀 | ✅ `CLAUDE.md` 已清零裸行號 |

⚠ **`temp/ro_sql.py` 冇刪**（當時有 5 個背景 agent 行緊，刪咗會喺人哋中途踢柜）。
佢係舊副本，**新嘢一律用 `scripts/ro_sql.py`**。留意兩者 CLI 唔同：
temp 版 positional 食**檔案路徑**，scripts 版 positional 食 **SQL 本身**（要餵檔用 `--file`）。

## 呢套嘢醫唔到嘅嘢

戳只守**戳咗嘅數字**。冇人戳嘅句子照爛，而「邊句抵得起一個戳」係人判斷。
戳亦都唔守**文字結論**——「呢條鏈已經接通」呢種話冇 SQL 表達得到，一樣會腐爛。
認領表係君子協定，唔讀嘅 agent 完全唔受影響。
`docs/evidence/` 只係令搵得返，唔會令人真係去搵。

`verify_doc_refs.py` 只睇**引用格式**，唔睇引用講嘅嘢啱唔啱：
指住 `evaluate()` 而 `evaluate()` 根本冇做嗰件事，佢一樣報 `OK`。
2026-07-26 最後一輪換咗 11 個 ref，其中 **6 個嘅行號本身已經指錯位**
（`run_daily.py:718` 真身喺 `main()` 另一處、`canonical_public_snapshot.py:45` 真身係<!--docref:example-->
`PRESENTATION_VIEW_MIN_COVERAGE`、`AGENTS.md 第 51–52 行` 真身喺另一個 heading 底下，等等）——<!--docref:example-->
呢 6 個全部係**人手逐粒 `grep` 對返出嚟**，冇任何腳本捉得到。
**symbol 名醫嘅係「以後唔會再錯位」，唔係「而家寫嗰個 symbol 名係啱」。**
