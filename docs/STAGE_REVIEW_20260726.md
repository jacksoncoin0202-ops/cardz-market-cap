# 階段盤點 — 2026-07-26

盤點對象：由「交嘢俾人擺上 AWS」呢個死線倒推嘅衝刺階段。
量度基準：`HEAD b0da73a`，全部數字附量度時間，冇量過嘅一律寫明冇量過。

---

## 0. 一句話

**交付物件備妥，可以出。** 兩個 zip 已建好並驗過，`git clone` 亦已修返可裝。
**唯一真正嘅缺口係通知渠道——冇 webhook，所有失敗都無聲**，呢樣要你親手俾一個 URL，我唔可以憑空作一個域名頂替。

| 交付物 | 大小 | 內容 | 驗到咩程度 |
|---|---|---|---|
| [cardz-handoff-20260726.zip](../temp/cardz-handoff-20260726.zip) | 248.7 MB / 2,309 檔 | 主交付包 | 解壓後實跑：`verify_handoff.py` exit 0（`enforced: true`, `checked: 51`, `failures: []`）、`pytest` 736 passed / 1 skipped、`npm ci` exit 0、`prebuild` 同步 360 raw_front + 720 derivative、typecheck exit 0、vitest 83 tests |
| [cardz-pop-history-20260726.zip](../temp/cardz-pop-history-20260726.zip) | 8.7 MB / 701 檔 | POP 歷史（可選） | 288 MB 壓到 8.7 MB（33×） |
| `git clone` | — | 同一棵樹 | `verify_clean_clone.py` exit 3、`requiredFiles.missing = []`（51 檢查）、`installerHardRequires.missing = []`、LFS unresolved pointer 0 / 1,158 |

包同 clone 之間已核對五個關鍵檔 byte-identical（installer 348 行、`SERVER_MIGRATION.md`、`AWS_HANDOFF.md`、`.dockerignore`、`Dockerfile`）。包唔帶 `.git`，所以入面嘅嘢唔會俾一個手殘 `git checkout` 打回原形。

---

## 1. 首尾成效：今日真係行得通嘅係邊一截

| 環節 | 狀態 | 根據 |
|---|---|---|
| 前端 build → 出頁 | ✅ | tsc exit 0、vitest 83 tests / 14 files、本機 `localhost:3800` HTTP 200（19:2x 量） |
| 交付包 → 陌生機解壓 → 裝得起 | ✅ | 上表解壓後實跑 |
| `git clone` → installer → systemd timer | ✅ | `verify_clean_clone.py` exit 3 |
| 每日 run → 出 snapshot → 過閘 | ✅ | 07-25 snapshot 有新行；`verify_daily_run.py` 已入 HEAD |
| 失敗 → 有人收到通知 | ❌ | **冇 `CARDZ_ALERT_WEBHOOK`，寫檔就當完，alert unit 自己 return 0** |
| 每日 run → 出 production snapshot | ⚠️ | git 入面永遠係 demo placeholder（刻意設計）。真數要喺 host 跑 `run_daily.py --mode production` |
| `next build`（Docker image） | ⚠️ | **未驗過**。Windows Application Control 封咗原生 SWC binary——同一 byte 嘅 binary 喺原路徑載入到、第二個路徑載入唔到，係機器政策唔係包嘅問題。Linux host 上要重新驗一次 |

「失敗有冇人知」呢一格，就係 07-24 → 07-26 靜咗三日嘅同一個窿。

---

## 2. 呢階段做完嘅嘢（按影響排，唔按時間）

1. **`git clone` 由「裝唔到」變成「裝得到」**——51 個必要檔入面有 30 個原本淨係喺 working tree，包括結果閘 `verify_daily_run.py`、成層通知、watchdog/alert/freeze/image-backfill 全部 systemd unit、`Dockerfile`、`.dockerignore`，同五份呢份手冊叫 operator 去睇嘅 runbook。
2. **前端出街修**——og:image 全缺、TAG 死頁、i18n 唔接線、grader donut 分母錯（五個 grader 頁報五個唔同市值）、市值 delta 方向相反 14/100。
3. **G10 底庫入 DB**——identity 395 → 641、研究文 480 篇、analytics 641 行 + 638 條 K 線、成交史 641 檔、SNKRDUNK 多 grade、卡圖 594 張。
4. **閘同排程**——結果閘、watchdog（補「根本冇跑」盲點）、catalog 縮水單向棘輪、三條 timer 拆開 + jitter。
5. **`docs/DATA_GAPS.md`**——1,880 行 / 39 節，所有缺口一張表，按「可唔可以同一批做」歸堆，唔按嚴重程度。

---

## 3. 盤點發現（呢節先係呢份文最有價值嘅部分）

呢五條唔係 bug list，係反覆出現嘅**類型**。逐條都有唔止一個實例，所以下次一定會再撞到。

### 3.1 「檢查器 exit 0」比「檢查器 exit 1」貴得多

三個唔同人、唔同時間寫嘅嘢，同一個病：

| 實例 | 表現 |
|---|---|
| installer 86 行版 | 印 `schedule=06:30 Asia/Tokyo` 然後 **exit 0**——對住一個裝唔到嘅 clone 報成功 |
| `notify_alert.py` 冇 webhook | `no CARDZ_ALERT_WEBHOOK configured; alert recorded only` 然後 **return 0**——alert unit 自己成功，`systemctl --failed` 乾乾淨淨 |
| handoff secret scanner | 用副檔名白名單，`.tf/.html/.bat/.css/.svg/.lock/.state` **從來冇開過個檔**；7 MB 嘅 `.jsonl` 俾 size cap 靜靜跳過 |

**共同結構**：對「根本冇檢查過嘅輸入」報 pass。
**規則**：檢查器冇檢查到嘢，一定要 fail，唔准 pass。scanner 已改成用 NUL byte 探二進位——非二進位就逐行掃、冇 cap、**新檔類型預設會掃**。

### 3.2 staged ≠ working tree，而且完全無聲

`deploy/linux/cardz-daily-systemd.sh` 同時存在三代：HEAD 86 行 / index 230 行 / worktree 348 行。230 行嗰個係另一個 agent 嘅中途存檔，俾一次大 `git add` 掃咗入去。

但真正嚴重嘅唔係 installer，係**同一個 bulk add 令 72 個 tracked 檔嘅 index 版本落後 worktree**，入面有 `apps/web/src/lib/route-metadata.ts`。當時 commit 落去，當日所有前端修正會「有名無實」咁出貨。

**規則**：release commit 之前，`git diff --name-only` 必須係空。

### 3.3 文檔腐爛，貴過檔案散落

之前診斷過：agent 效能下降嘅主因唔係檔案周圍屙，係**佢讀啱咗路徑，但份文講大話**。通用嘅「禁止估路徑」措施一條都醫唔到呢個。

**呢份文自己啱啱就係實例**：`docs/AWS_HANDOFF.md` 頂頭寫住「🔴 STOP — clean clone 唔裝得，30 個檔唔喺 HEAD」，commit `b0da73a` 落咗之後呢句即刻變假。已即時改成量度結果，並把嗰段歷史壓縮保留（因為 installer 三代嘅教訓本身有價值）。

**規則**：任何寫住「量度於 X 時」嘅結論，改變咗狀態嘅 commit 要順手改埋份文，唔准留俾下一個人踩。

### 3.4 一次性 backfill 會污染第二日嘅 baseline

`_volume_floor_check()` 攞前一日做 baseline。一次過灌入嘅 backfill 令 baseline 虛高，**第二日必然出一日假紅**。

- eBay / SNKRDUNK 呢半邊會自己清——`baseline < 10 → continue`，佢哋今日係 0。
- 但 `g10_analytics 1277` **已排咗聽日做**，會準時複製同一個現象。

已寫入 `DATA_GAPS.md`（附三個修法俾閘嘅 owner 揀），**冇改**——當時三個 agent 喺飛，中途郁一個未被認領嘅閘唔抵。

### 3.5 Ghost source：源頭名係捏造出嚟嘅

`db_runtime.py` 嘅 fallback：`provider = row.get("providerCode") or row.get("sourceCode") or "private"`。有個腳本唔出 `providerCode`，於是跌返卡自己嘅 `canonicalSourceCode`，**憑空製造出 `ebay` / `snkrdunk` 兩個「源」**。

最硬嘅反證：`source_code='ebay'` 嗰批帶住 `grader_population_bgs/cgc/sgc/tag` 行——**eBay 唔可能產生評級 population**。真嘅 SNKRDUNK feed 叫 `snk_psa10`（07-26 有 117 行，生存中）。

**連帶發現**：`CLAUDE.md` 曾經寫住 eBay「日更咗三個月」，來源係數咗 92 個 distinct `observed_date`。加返 `DATE(created_at)` 一睇——3,732 行（97.6%）係 2026-07-25 一次過寫入，`ingest_mode='backfill'`，run_id 71。**eBay 從來冇接過做日更源。**

**規則**：判斷一個源係咪「日更」，一定要睇 `DATE(created_at)` 嘅分佈，唔可以睇 `observed_date` 嘅 distinct count。

### 3.6 其他值得記低嘅單點

- **`next/og` 解唔到 WebP**：同一份 markup，PNG 出 8450 px，WebP 出 **0 px**——無聲空白，唔報錯。market-assets 全部 1,080 個檔係 WebP，所以卡片 OG 圖係純文字版面。冇用 `sharp` 修（未宣告嘅 transitive dep + 原生 binary，Cloudflare Workers 上跑唔到）。
- **`data/private/` 頂層防護有窿**：`.gitignore` 逐個子目錄咁擋，擋唔到頂層新出嘅檔。一個 12.8 MB 嘅 `cardz-active-bootstrap.tar.gz`（入面係 `active-gemrate-ids` / `active-snk-ids` / `active-universe`，即係我哋追蹤邊啲卡、由邊個源攞）已經入咗 index。已 unstage，history 仍然乾淨（得 `.gitkeep`），規則已反轉成預設全擋 + 白名單。

---

## 4. 仲爭咩

### 4.1 要你親手做（我做唔到）

| 事項 | 點解要你 | 做完點驗 |
|---|---|---|
| **俾一個 `CARDZ_ALERT_WEBHOOK` URL** | Slack / Discord incoming webhook 或者自架 endpoint。**唔准憑空作一個域名頂替** | `notify_alert.py --self-test`，見到 `[notify] delivered` 為準 |
| **GemRate key ~07-29 到期** | 換新 key，或者接受凍結後嘅存量 | 凍結掃已排 07/27 05:47 同 07/28 05:47，兩次都**未跑過**（`LastResult=267011` = 未執行過） |

### 4.2 已排期會炸嘅嘢（日曆）

| 日期 | 事件 |
|---|---|
| 聽日 | `g10_analytics 1277` backfill → `volume_floor` 出一日假紅（見 3.4） |
| 07-27 05:47 / 07-28 05:47 | GemRate 凍結掃兩次 |
| ~07-29 | GemRate key 到期 |
| 2026-07-29T00:00:00Z | **TAG 新鮮度炸彈**：220 張一次過過期。修位喺 `canonical_public_snapshot.py` 嘅 `latest_populations()`，要加 >168h date floor → 標 `unavailable` / `value=null` |

### 4.3 開住嘅工單

Pending / in-progress：#6 #7 #8 #18 #21 #22 #24 #25 #27 #29 #32 #34 #38 #39 #49 #50 #54 #58 #59。
細節喺 `docs/DATA_GAPS.md`——**批 ③ 人手裁決批**一次 review session 可以一口氣清三個上線 blocker，係目前投資回報最高嗰舊。

---

## 5. 呢份文喺文檔樹邊度

- 想知**而家點**（狀態、工單）→ `PROJECT_STATE.md`
- 想知**點裝上 AWS** → `docs/AWS_HANDOFF.md`（operator 手冊，英文）
- 想知**缺咗咩數據** → `docs/DATA_GAPS.md`（1,880 行 / 39 節）
- 想知**呢輪做過咩、學到咩** → 呢份

呢份係階段快照，唔會逐日更新。下個階段開始時另開一份，唔好喺呢份上面改——改咗就冇咗「當時真係咁」呢個價值。
