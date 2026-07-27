# PROJECT_STATE — CARDZ Market Cap

> **單一真相來源。** 任何 agent（Claude Code / Codex / Hermes / Pi / 其他）開工前一定要讀呢份，
> 收工前一定要更新呢份。呢份文係 model-agnostic —— 唔靠任何一個 model 嘅 session 記憶。
>
> 最後更新：**2026-07-27 20:55（本機時區）** by Claude Code (pkg-release) ——
> **quota 切點交接（→ Opus 5 接手，用戶下令停低寫低先）**。今晚三件事：
> ① **eBay PSA10 成交接入 producer**：零成交卡 12→1（剩 rank 96 兩源皆死）、
> 主板 Top100「—」11→1、30d 成交環比 54→106。⚠ 唯一正確取法 = `market_sale_observation`
> 過濾 ebay/psa/10，**唔准用 `market_daily_sales_aggregate` 嘅 ebay 行**（全 grade 混合）。
> 三個陷阱（`--view` 默認錯配、`trackedSales` nested、SSR 頁面 memory cache）記
> [docs/evidence/2026-07-27-ebay-sales-wiring/FINDING.md](docs/evidence/2026-07-27-ebay-sales-wiring/FINDING.md)。
> ② **借數 cascade 方向用戶更正**：任何空窗以 **1d→7d→30d 最新鮮優先**借
> （唔再係只長借短），code/test/DATA_CONCERNS 已同步。
> ③ **OP 卡名 i18n 7 卡批案已批 + discovery 完成、code 一行未落**：病灶 = producer
> pass-through 蓋死 card-names.ts lookup；接手照
> [docs/evidence/2026-07-27-i18n-7cards/HANDOFF.md](docs/evidence/2026-07-27-i18n-7cards/HANDOFF.md) §4 直行。
> **docker build GO 已俾（07-27 晚）**：隊列 = i18n 完成 → build（seed 臨時蓋入→即還原）→ push GH。
> 上一輪：**OP 已出版 27 張「價唔得」三病類診斷歸位**（27/27 有價 90/90 點；A 單日源 spike
> rank 5 +544% 已定案上游 SNKRDUNK 單日異常唔係身份污染，修法 source-level sanity gate 未實裝
> · B snk 日更源死/冇 → 窗口借數 · C 稀疏源 forward-fill 24/27 Δ1=0。證據
> [docs/evidence/2026-07-27-op-published-27/FINDING.md](docs/evidence/2026-07-27-op-published-27/FINDING.md)）
> + **OPTCG canonical 圖源制度化**（用戶人眼驗收拍板：G10 樹 SNKRDUNK assets，寫入
> [docs/CARD_SOURCING_HANDBOOK.md](docs/CARD_SOURCING_HANDBOOK.md)「三之一」+ 來源審批制度表）。
> 上一輪：**上線打包前端修復兩單齊**（grader 板市值駁通 + 窗口借數 cascade——初版 30d→7d→1d，
> **07-27 晚已更正方向做 1d→7d→30d**——30d 價變 258/258、市值變 244/258、TAG POP 0→86，
> 代價見 launch-fallback DATA_CONCERNS）
> 上一輪：**六個背景 agent 全部收工 + doc-rot 4 條 BROKEN 已修**（HANDOFF ×3 + DB_INVENTORY ×1）。
> 最大反轉：**GemRate key 07-29 到期係真風險** —— 07-27 run log `direct=enabled`
> 即 key 係 primary transport，07-26「已降級為非事件」結論作廢（見 §4 新段 + §2）
> 上一輪：**用戶投訴兩單全修**：heatmap 空白（`allowedDevOrigins` 修 127.0.0.1 hydration 403）
> + One Piece 得 20 張（producer 新增 `top300_boards` 聯集 view，OP **20 → 27 張**）
> + 途中撞出故事 gate 全站 500，4 張新卡故事四語補齊（Sonnet 代筆），258 published —— 見 §4
> 上一輪：3800 serving 鏈修好 + 五條線收齊（K 線橋接 run_id=107 / image-fix / 故事 243）；
> 再上一輪：真 Top 300 推導（排到 **257**，樽頸係價格唔係 POP）——見 §1 / §4 / §5 / §6 D9
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

**真係 0 行嘅得 5 張**（07-27：`catalog_printing_identity` 已通電 68 行，見 §4 identity-batch）：
`market_tracked_sales_aggregate`（已廢棄）· `market_raw_payload_object` ·
`market_source_observation_payload_pointer` · `market_source_effective_observation` ·
`market_retention_archive_manifest`。

**價格覆蓋率兩個分母**：對 roster 395/1,468（**26.9%**）；對 catalog 395/1,705（**23.2%**）。
舊文寫嘅「硬上限 18.6%」**已作廢**，唔好再引用。

**POP 每日真相**：07-21 641 卡 · 07-22 **2 卡 + TAG 307 卡** · 07-23 **1 卡** ·
07-24 1,216 卡 · 07-25 1,211 卡。即係 gemrate 得 3 日真量，**TAG 一世得 07-22 一日**。

⚠ **07-26 更正**：當日 POP 寫入全部係 `source_code='tag'` **324 行** —— 「TAG 一世得
07-22 一日」已被推翻，TAG 似乎翻生咗 <!--@verified 2026-07-26 id=db.pop_obs.tag_0726 expect>=324 ttl=14 sql=SELECT COUNT(*) FROM market_grader_population_observation WHERE source_code='tag' AND observed_date='2026-07-26'-->。
記低未追（組裝模式）；影響：CLAUDE.md「TAG 每日死亡」章節要重驗先好引用。
GemRate 最新完整日仍然係 **07-25**（JST 午夜收盤制）。

**上線快照覆蓋**（id=19 / 2026-07-25 / 255 卡）：價格 255 · POP 255 · 市值 255 ·
卡圖 **251** · 四語 **236** · delta 1d 255 / 7d **237** / 30d **232** ·
POP delta 7d/30d **0** · sparkline 尾 14 日 **251**。

---

## 1. 而家喺邊

### 🧭 工作模式再變（2026-07-26 深夜用戶指令，最新）：**組裝模式 —— 用戶一步步帶住做**

用戶原話：「記住我依家搵你做嗰啲嘢，你一定要 mark 翻低。因為基本上我依家就係組裝翻個網站
嘅流程嚟嘅。依家組裝翻網站，我一步步帶住你做，如果唔係，你發散性思維，咩都做，可以做一世。」

規則：每收到一步指示**即場寫低先執行**；只做該步範圍；隔籬發現嘅問題記低唔准追。
優先級 = **真確性**（「我唔想個數據出到街俾人質疑」）。詳見 §6 D9。

**現行四步（用戶 2026-07-26 定）同進度**：
1. ① 證明數據冇消失 —— ✅ 已證：全部 fact table append-only，POP-ever 1,590 卡 ≥ roster 1,468；
   用戶記憶中嘅「~1,900」最貼近 POP-ever 1,590，冇嘢被刪過。
2. ② POP 全 roster 掃齊 → 推出真 Top 300 —— ✅ 已量：gemrate POP 新鮮度 **97.8%**
   （1,435/1,468 停喺 07-24/25），**唔使全宇宙重掃**；真市值榜今日深度 = **257 唔係 300**，
   樽頸係**價格覆蓋**（395/1,468 有過任何價）。證據包：
   [docs/evidence/2026-07-26-top300-derivation/](docs/evidence/2026-07-26-top300-derivation/)。
   淨低 33 張 POP 缺口卡（見 §5）。
3. ③ Top 300 價格長期日日追 —— 排緊隊（§5 P0-組③）。價係 flow data，冇得事後補。
4. ④ 圖片 / metadata / 小故事一律 **DEFER**，Top 300 穩定先追（包括 EB02-010 錯圖）。

**⑤ 現行步（2026-07-26 深夜用戶指令，收尾）** 用戶原話重點：「我想組裝到 top 300 出到嚟先，
同埋睇嚇仲有冇啲缺圖片。跟住如果出唔到嚟，你幫我去爬返啲數據返嚟就得。我有腳本，
腳本唔啱你就改一改通用版本。……我依家其實都係維護呢 1,590 張卡嘅啫，暫時嚟講……
真係入咗圍嘅數據先會開始爬佢嘅價錢。圖片方面，係上網嗰陣時候先需要去揾圖片，準則已齊。」
追問：「海賊王已經一定有問題，冇理由 live 嘅數據得二十張，我爬一百都唔齊。」

**海賊王診斷（2026-07-26 全實測 —— 數據冇消失，係兩個口徑錯位）**：
- 用戶爬嘅 OP 價**全部仲喺 DB**：priced-ever **95 張**，且 95 張全部 7 日內有新價（採集器一直行緊）。
- 但 95 張入面 **55 張唔喺 roster 1,468 內** → eval 唔計、live 唔顯示；roster 內只有 40 張有過價。
- OP roster 227 張（ready 33 + accumulating 169 + unavailable 25），其中 **181 張連
  ebay/snkrdunk 價源 identity 都冇** —— 結構上永遠冇可能有價，收集器點跑都追唔到。
- 結果：OP 上榜 = **33 張**（rank 9 / 15 / 18 / 25 / 38 …），再過 image/eligible gate
  → 用戶眼見 ~20-30 張。「live 得二十張」同「爬咗一百」**兩句都啱**，中間差咗入圍同 mapping。
  `@verified 2026-07-26 id=op.roster_no_identity expect=181 ttl=7
  sql=SELECT COUNT(*) FROM market_candidate_daily_snapshot s JOIN catalog_variant v ON v.id=s.variant_id
  WHERE s.evaluation_id=8 AND v.tcg_code='one-piece' AND NOT EXISTS (SELECT 1 FROM
  catalog_source_identity c WHERE c.variant_id=s.variant_id AND c.source_code IN ('ebay','snkrdunk'))`
- 順帶解決舊矛盾：`market_sale_observation` 93,063 行 = ebay 13,015 + snk_grade 14,546 +
  snkrdunk 65,502 —— **`g10_ebay_ingest.py` 已經入咗庫**（docstring 講「0 行」係寫嗰時嘅舊話）。
  G10 disk 有 559 卡 eBay PSA10 數據，DB 只 join 到 344 卡 → ~215 卡差額全部卡喺 identity join。
- 缺圖實測：257 ready 中冇 image asset 只有 **4 張** —— 圖片唔係樽頸，價源 identity 先係。

**⑥ 現行步（2026-07-26 深夜再落，蓋過⑤嘅收尾狀態）** 用戶原話：「擺哂 TOP300 即係
海賊王 100 加 PTCG 100，仲有係 TCG 300。你擺曬上去先，跟住之後我哋就就著呢一啲數據，
去搵返佢哋缺少嘅資料：缺文就補文，缺圖就搵圖。仲有，要用實時今日嘅數據加歷史數據，
咁樣先為之完成。如果有啲遇 block 嘅就唔好停，過咗佢先。記得嘅問題事後先翻去，
再做多次、再研究下點樣過 block，點樣去解決件事。」

操作定義（照原話解讀，唔擅自加減）：
1. 榜制改**三張榜**：海賊王 Top 100 ＋ PTCG Top 100 ＋ 總榜 TCG Top 300。先擺上站
   （本地 :3800 驗收；push production 照舊等用戶點頭先郁）。
2. 「海賊王 100」數學上必然要用埋嗰 **55 張 out-of-roster priced OP**（roster 內
   得 40 張有過價）——即係用戶對「55 張入唔入圍」嘅答案係**入**。正規 roster/lock
   擴張程序另計；未完成之前三張榜由 derivation 層出數，provenance 標明邊啲卡未入 lock。
3. 「實時今日 + 歷史」= 今日（07-26/27）採集行要落埋今日數 + G10 disk 歷史（559 卡
   只入咗 344）要入晒庫，先算「完成」。
4. 遇 block **唔停**：過咗先，全部記入 blocker log（evidence 目錄），事後逐個返轉頭解。
5. 榜出咗之後先做「缺文補文、缺圖搵圖」盤點——以三張榜嘅卡做範圍。

### 🚀 上一個目標（2026-07-26 朝用戶指令，已達成）：**明日交一個可以擺上 AWS 嘅出街版本**

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
| **:3800 dev server（用戶驗收接口）** | 🟢 **行緊**（`npx next dev -p 3800`，07-27 03:00 起）。⚠ 每次 producer 重寫 snapshot 後要重啟先食到新數據（`loadNodeSnapshot` 進程內 cache） | pm-closeout | 見 §4「3800 serving 鏈」+ §7 next start 地雷 |
| today-prices #12：今日 ebay+snk 價格採集 | ✅ 完成（07-27 凌晨）：snk +56 價格行（07-26 收盤 17 卡有成交＋07-25 補 39）；ebay 0 行＝上游 G10 未有 07-26 收成（fail-closed 正常，外部阻塞非採集壞） | Opus 背景 agent | docs/evidence/2026-07-27-today-prices/FINDING.md |
| board-gaps：三榜缺圖缺文盤點 + EB02-010 換圖候選 | ✅ 完成（07-27 凌晨）：265 卡全集，缺圖 22／缺英文故事 28／非標準畫布 169／opaque_id 漂移 191；EB02-010 換圖候選＝TCGplayer 641620（HTTP 實測 625×873 乾淨卡面，等用戶拍板先入庫） | Opus 背景 agent | docs/evidence/2026-07-27-board-gaps/FINDING.md |
| **上線打包 rel-20260727** | 🟡 **GO 已俾（07-27 晚）**，排喺 i18n 批案之後開波。前端修復兩單 + eBay PSA10 接線完成兼實測（見 §4）。build 步驟：production snapshot 臨時蓋入 build context 嘅 seed → build → 即刻 `git checkout` 還原 seed → 驗容器 `/api/health` → commit + push GH（唔准 force-push） | pkg-release | [docs/evidence/2026-07-27-launch-fallback/DATA_CONCERNS.md](docs/evidence/2026-07-27-launch-fallback/DATA_CONCERNS.md)、[.dockerignore](.dockerignore) |
| **OP 卡名 i18n 7 卡批案** | 🟡 **discovery 完成、code 一行未落**（quota 切點交接俾 Opus 5）。病灶 = producer pass-through 蓋死 card-names.ts lookup；實測 14 張 pass-through（批案只 7 張，其餘唔准順手譯）、「3th」實際喺 rank 36+91（批案講 20/36，過目時要講明出入）、rank 47 爛值要 exact override。接手照 HANDOFF §4 edit 計劃直行；`card-names.ts` claim 喺 FILE_CLAIMS（19:28 起 4h 租，過期 re-stamp） | pkg-release（→ Opus 5） | [docs/evidence/2026-07-27-i18n-7cards/HANDOFF.md](docs/evidence/2026-07-27-i18n-7cards/HANDOFF.md) |

---

## 4. 已完成 —— 唔好重做

> 呢個 section 存在嘅唯一理由：阻止下一個 agent 重新考古。
> 見到下面任何一項，**唔好再查一次**，直接信，除非 §0 嘅命令話你知佢壞咗。

**eBay PSA10 成交接入 producer + 借數方向更正（2026-07-27 20:55，pkg-release）**
- `latest_sales()` 由 snk_psa10 單源改成 snk+eBay 雙源合併（history sparkline 同步）。
  實測（258 published，前→後）：零成交卡 12→**1**（剩 rank 96 ST10-006，snk 死 06-15 兼
  eBay 無 PSA10 成交，兩源都冇嘢可接）、主板 Top100「—」11→**1**、30d 成交環比 54→**106**、
  7d 194→**232**。窗口 containment（1d⊆7d⊆30d）0 violations。
  ⚠ **eBay PSA10 唯一正確取法 = `market_sale_observation` 過濾
  `source_code='ebay' AND grader_code='psa' AND grade_label='10'` 逐單聚合；
  唔准用 `market_daily_sales_aggregate` 嘅 ebay 行**（全 grade 混合，唔係 PSA10）。
  三個真踩過嘅陷阱（`--view` argparse 默認 `top300` ≠ 生產 `top300_boards`、
  `trackedSales` 係 nested `{count:{value},…}`、SSR 頁面 route 揸 snapshot 喺 process memory
  → promotion 後要重啟 dev server）詳見
  [docs/evidence/2026-07-27-ebay-sales-wiring/FINDING.md](docs/evidence/2026-07-27-ebay-sales-wiring/FINDING.md)。
- **借數 cascade 方向更正（用戶下令）**：由「30d→7d→1d 只長借短」改成
  **任何空窗以 1d→7d→30d 順序借（最新鮮優先，跳過自己）**。
  [market-cap-delta.test.ts](apps/web/src/lib/market-cap-delta.test.ts) 鎖死新次序，
  DATA_CONCERNS 同步注記。fail-closed 底線冇郁（三窗全空照舊空白）。

**OP 已出版 27 張價格診斷收官 + OPTCG 圖源制度化（2026-07-27 19:05，pkg-release）**
- **27/27 全部有價格歷史**（90/90 點 priced，status=ready，冇一張缺價）。用戶見到嘅怪數字分三病：
  **A 單日源 spike** —— rank 5 ST21-014 Luffy +544%：snk_psa10 07-26 ¥294,500 vs 07-25 ¥45,700。
  **已定案：上游 SNKRDUNK 單日異常，唔係身份污染** —— 兩張 ST21-014（variant 54 = Jump 雜誌
  promo ↔ snk 706813；variant 240 = Flagship Battle 優勝紀念 ↔ 605546）`catalog_source_identity`
  綁定 1:1 乾淨；原始 payload（`referenceMethod: snk_daily_history`）07-26 SNK 自己就報
  ¥294,500，而同卡 07-25 G10 metrics 先 $295.53 —— pipeline 冇抄錯。修法 = source-level
  日環比 sanity gate（一日 ×N 隔離候審，N≈3 起步），**未實裝**。
  **B snk 日更源死/冇 → 窗口借數** —— rank 95 Boa OP07-051（零 snk，ebay-only 13 行）、
  rank 96 ST10-006（snk 死 06-15）、Law ST10-010 / Zoro OP01-025（07-11 死）：30d 砌唔出
  → 借 7d/1d，畫面嘅 +6.41% / 0.00% 就係借數結果（有 `fallbackWindow` 標記）。
  **C 稀疏源 forward-fill** —— 24/27 張 Δ1=0.00%。全部證據 + tail -80 截斷推「冇」嘅反面教訓：
  [docs/evidence/2026-07-27-op-published-27/FINDING.md](docs/evidence/2026-07-27-op-published-27/FINDING.md)。
- **OPTCG canonical 圖源制度化**（用戶 07-27 人眼驗收拍板「啲卡又是正，亦都冇 sample 字眼」）：
  G10 樹 SNKRDUNK assets（480 bundle，一包齊圖/價/POP）。已寫入
  [docs/CARD_SOURCING_HANDBOOK.md](docs/CARD_SOURCING_HANDBOOK.md)「三之一」章 +
  「來源審批制度」表（用戶逐源人眼驗收，OK 先入 canonical，被彈嘅記低唔准再用）。
- OP 卡名 i18n：7 張譯名批案**已批（07-27 晚）**，discovery 完成未落筆——進度同 edit 計劃
  睇 §3 i18n 行 + [docs/evidence/2026-07-27-i18n-7cards/HANDOFF.md](docs/evidence/2026-07-27-i18n-7cards/HANDOFF.md)。

**上線打包前端修復：grader 市值駁通 + 30d 窗口借數 cascade（2026-07-27 15:35，pkg-release）**
- **grader 板市值欄（用戶投訴一）**：BGS/CGC/SGC/TAG 版之前市值全部「—」，唔係冇數 ——
  係 [grader-page.tsx](apps/web/src/components/grader-page.tsx) 對非 PSA grader 硬編碼唔出
  `card.marketCap`（marketCap 一直喺 payload）。已拆：五版統一出 PSA10 市值（heading 副題注明），
  mobile 版順手修埋攞 marketCap 當價嘅重複顯示（改用 `pricePsa10`）。
  [i18n.ts](apps/web/src/lib/i18n.ts) 刪咗五語 `marketCapUnavailable` 死文案。
  實測：BGS 版 100/100 個市值格有 $ 值，0 個「—」。
- **30d→7d→1d 整窗口借數（用戶投訴二，產品取捨蓋過 fail-closed）**：本窗口指標唔可顯示就借
  較短窗口嘅**成個結果**，標 `stale` + `fallbackWindow`。落腳 [snapshot.ts](apps/web/src/lib/snapshot.ts)
  `borrowWindowMetric()` / `borrowTrackedSales()`（view 層，producer 同 `composeChangePct` 零改動，
  同窗口頂替照舊禁止）。實測收益（30d，258 published）：價變 235→258、市值變 224→244、
  TAG POP change 0→86/100、SGC 12→13；成交 12 張三窗全零冇得借，維持「—」。
  代價（TAG 30d 全部係借 7d 之類）+ 還原四步全部寫低喺
  [docs/evidence/2026-07-27-launch-fallback/DATA_CONCERNS.md](docs/evidence/2026-07-27-launch-fallback/DATA_CONCERNS.md)。
- 驗證：vitest 103/103（含新 borrow test 鎖借數次序唔准跳級）、tsc 0 error、eslint 清、
  :3800 五 grader 版 + 主板 + watchlist 逐版數格（數字見 DATA_CONCERNS）。
- 順手發現寫低：snapshot `coverage.graderPopulationChangeReady` 5 grader × 3 窗全報 0
  但 per-card PSA 30d 實際 98 ready —— producer 計數器 bug，冇 UI 讀，唔擋出街。
- **.dockerignore 補咗 `!data/editorial/set-names.json`**（snapshot.ts static import，
  之前 docker build 會 Module not found）。deny-all-then-allow 結構冇郁。

**六 agent 審計波次收工 + doc-rot 4 條 BROKEN 修復（2026-07-27 13:20，pm-live-3800）**
- 六個背景 agent 交付齊：story provenance · 指數停更診斷 · doc-rot 審計 · verify gate 時區 ·
  G10 歷史 POP 盤點 · OP POP 決策包。證據包全部喺 `docs/evidence/2026-07-27-*/`。
- **doc-rot 審計（34 份文 637 個引用）：BROKEN 4 · STALE 10 · OK 14** —— 零死連結零缺 script，
  腐爛 100% 集中喺數字/狀態描述。**4 條 BROKEN 已即場修好**：
  ① [docs/HANDOFF.md](docs/HANDOFF.md)「GemRate key 到期非事件」→ **推翻**（07-27 log
  `direct=enabled`，key 係 primary transport；07-29 前必須實測 keyless standby 食唔食到
  1468 全量）；② HANDOFF publish 命令 `--required-presentation-view` → `--presentation-view`
  （`backend.py` 只收後者，餵錯 argparse exit 2）；③ HANDOFF external_entity_id「格式
  `gemrate:<gid>`」→ 實測三個 namespace 且同 `source_code` 唔對齊（`source_code='gemrate'`
  行入面 1147 行 `snkrdunk:` prefix），撈 source 一律用 `source_code`；
  ④ [docs/DB_INVENTORY_20260726.md](docs/DB_INVENTORY_20260726.md)「07-25 永久缺口」→
  已被 07-26 補 run 填返（0 → 755 行）。10 條 STALE 記隊列（逐字改法喺
  [docs/evidence/2026-07-27-doc-rot/FINDING.md](docs/evidence/2026-07-27-doc-rot/FINDING.md)），
  最高槓桿係 STALE-9：推廣 `@verified` 戳（34 份文得 1 份用，4 條 BROKEN 全部本可自動捉到）。
- **verify gate 時區 bug 已修**（commit `b0da73a`）：`verify_daily_run.py`
  `default_expected_date()` 改 T-1 基準 + 回歸測試。兩個前提更正：bug 真身喺
  [scripts/verify_daily_run.py](scripts/verify_daily_run.py)（唔係 verify_handoff.py，後者零日期
  邏輯）；本機係 **JST UTC+9**（唔係 UTC+8）。殘留三項記隊列唔擋 live：volume_floor 攞 T-1
  未熟數對 T-2 熟數（報 87% 跌實係同齡 171% 升）· ingest_activity 結構上 fail 唔到（97% 回填
  都照 PASS）· `verify_claims.py` naive `date.today()` JST/UTC 判決可差一日。
- **G10 歷史 POP 盤點（零 DB 寫入）**：磁碟有 3 年真歷史（833 個 `history_full.json` /
  393 個日期，2023-07-25 起），DB 兩張 POP 表得 7 日點，缺口 317,421 個 grade 點。
  **兩個政策阻塞等用戶拍板先可以起 producer**：① 唯一寫者用 `ON DUPLICATE KEY UPDATE`，
  直餵會 mutate 2,761 行現有數據（違 append-only）；② 來源有 264 個負 delta 點（155 條序列，
  違「POP 只升唔跌」紅線）。前端 POP delta **唔受影響**（`population_series()` 直讀磁碟繞過
  DB）。就算做到 100% 都只係 730/1590（45.9%）—— 735 張冇歷史檔。
- **OP POP 決策包**：「71 張缺 POP」前提修正 —— roster 內 227 張 OP **零缺** POP，缺嘅係
  榜外 76 張（70 張連 gemrate identity 都冇，要先行 keyless `collect`）。**quota 唔係約束，
  identity 先係**（有 id 嗰 6 張只需 6-12 call）。「~07-29 到期」呢個**日期**冇實證（8 處
  出處全係內部循環引證，最實係 `gemrate-freeze-oneshot.ps1` 參數預設）。真樽頸係價：
  132 張 POP≥1000 只差一個價，補價上限 42 → 174 張上榜，**零 GemRate call**。
  四選項 A/B/C/D 等用戶揀（詳見 docs/evidence/2026-07-27-op-pop-decision/）。

**heatmap + OP 27 張 + 故事 gate 修復（2026-07-27 13:00，pm-live-3800）**
- **heatmap 空白根因**：Next.js 16 dev 對帶 Origin 嘅 cross-origin 資源預設 403（server 自認
  localhost，用戶開 127.0.0.1）→ hydration 死 → client-only heatmap 空白。修法：
  `apps/web/next.config.ts` 加 `allowedDevOrigins: ["127.0.0.1"]`。驗收：帶
  `Origin: http://127.0.0.1:3800` 打 app chunk 全 200，dev log **0 個 "Blocked cross-origin"**。
- **One Piece 20 → 27 張**：`pipelines/canonical_public_snapshot.py` 新增 `top300_boards`
  presentation view = combined top300 core ∪ 同日分榜（one-piece/pokemon）成員（實測 3 張 OP
  跌出 combined 300 外：rank 304/312/336）。producer 出貨 **336 ranked / 258 published /
  78 skipped（全部 image_unavailable）**，generation `canonical_20260726_e88c81289ac3`。
- **故事 gate 全站 500 修復**：新榜 4 張卡（vid 42/145/234/240 = ST13-003 BVB / P-110
  OP DAY'25 / OP06-119 Comic Parallel / ST21-014 Flagship 優勝紀念）四語故事全空，
  `validate.ts` assertPublicSnapshot throw → SSR + API 全 500。修法（唔放寬 gate，補內容）：
  Sonnet 子 agent 寫 4 篇 G10 標準英文長文（2813–2913 字）+ 4×3 語譯文；
  `g10_research_ingest --write` run_id=110（32/32 accepted，story_pointer 494→498）；
  `editorial_locale_sync --write` run_id=111（zhTW/zhCN/ja 各 246→250）。
  英文長文擺 `data/editorial/long-form-stories/snkrdunk/<snkrdunk_id>/`（4 張冇 ebay identity，
  用 snkrdunk provider 對表）。producer 重跑 **stories 258 attached / 0 missing**。
- **驗收（07-27 13:00 實測）**：`/one-piece` 200（**27 卡行**）、`/pokemon` 200（100）、
  `/watchlist` 200（158）、`/` 200（top100=100）、`/api/v1/market` 200 count=100
  effectiveAt=2026-07-26。dev log 唯一 error 係 server 重啟前舊 browser tab 嘅 HMR 殘影
  （時序在所有新 request 之前），新 snapshot 過晒 validate。
- 剩 15 張 OP 未出街：13 張 skip 係圖源問題（TCGplayer 100% SAMPLE 水印、本機 100% 評級殼相，
  等 One Piece 官方卡 DB 採集——下一單工程）+ 2 張榜外。

**3800 serving 鏈修好 + 五條線收齊（2026-07-27 03:00，pm-closeout）**
- **3800 而家出緊 production 快照，已驗收**：`/api/v1/market` `generatedAt=2026-07-26T17:39:32.725922Z`
  `count=100` `effectiveAt=2026-07-25T00:00:00Z`；one-piece SSR 269,818 bytes，
  新圖 sha `b7e4d2e1`(v8 EB02-010) ×9、`74169e81`(v111 ST10-006) ×9、舊爛圖 `398cebb1` ×0。
  serving 檔 = `data/runtime/local-serve/snapshot.json`（5,107,810 bytes，
  generation `canonical_20260725_aaca5dd2ee9f`），由 `apps/web/.env.local` 嘅
  `MARKET_DATA_SNAPSHOT_PATH` 指入去。**啟動方式一定係 `npx next dev -p 3800`** —— 點解唔係
  next start 見 §7 新地雷。
- **producer 最終版**：243 published / 12 skipped（全部 `image_unavailable`）；
  故事 **243/243 全齊**、價格 0 缺、POP 0 缺。v8 + v111 兩張換圖新 sha 已出街。
- **K 線橋接（run_id=107）**：`pipelines/g10_kline_price_bridge.py` 將 ledger 47,582 行
  `g10_kline_daily` 入面 4,357 條真成交（carried=0）扣 374 條 identity 對唔到後，
  **3,964 行 PSA10 收盤價寫入 `market_price_observation`**（source_code=`g10_kline`、
  priority=300 補洞位，直採源永遠贏）。ebay 體系三年價格歷史（最遠 2023-08-31）接通前端。
  證據 [docs/evidence/2026-07-27-kline-bridge/](docs/evidence/2026-07-27-kline-bridge/)。
- **image-fix 返貨**：**4 張搞掂**（v8 EB02-010 + v111 ST10-006 已出街；v168 入庫**未出街**
  因唔喺榜；v35 撤回因 language mismatch）；**21 張有據未處理**（20 張 OP：altxyz 全評級殼、
  TCGplayer 36/36 SAMPLE 水印，等乾淨圖源；v276 EB03-026 雙重卡死）。
  證據 [docs/evidence/2026-07-27-image-fix/](docs/evidence/2026-07-27-image-fix/)（gap-table.json 25 張逐張結局）。
  ⚠ 發現 `manifests/image-qc.json` **624 條記錄得 420 個唯一 publicId（204 條重複）**，
  `upsert_qc()` 只換第一條 match —— 已入 §5 必修。
- **EB02-010 錯圖重做（用戶欽點嗰單）就此完成** —— 唔好再照 §5 舊字句去做。

**Boards UI 全剷 + OP100 樽頸實測 + 出街範圍定案（2026-07-27 凌晨）**
- **用戶最終決定「BOARDS 直頭唔要，唔使有呢個掣」** —— watchlist 三榜 toggle 連 BoardsView 成個 UI
  剷除（`git checkout HEAD` 還原 market-page.tsx / i18n.ts / globals.css，刪 boards-view.tsx 同
  apps/web/src/data/）。驗證：tsc 0 error · vitest 83/83 · /watchlist SSR 冇 toggle。
  三榜**數據**照留：CSV + build_boards_json.py 喺 [docs/evidence/2026-07-26-three-boards/](docs/evidence/2026-07-26-three-boards/)，
  用戶原話「數據嘅邏輯都係：數據擺曬喺度」。
  ⚠ [apps/web/src/lib/server-snapshot.ts](apps/web/src/lib/server-snapshot.ts) 工作區改動（dev 讀
  `MARKET_DATA_SNAPSHOT_PATH`）係**有意保留**，3800 靠佢先出真數據，唔好 revert。
- **出街範圍定案（用戶 07-27 欽點）**：TCG300 ∪ OP100 ∪ PTCG100 ∪ watchlist(101–300)，
  合計 ~400–500 張唯一卡。次序：補卡 → 補齊**用戶可見**內容（價、故事、圖）→ QC → 出街。
  數據上見唔到嘅嘢慢慢執甚至唔執。**用戶驗收接口 = http://127.0.0.1:3800**（佢用 3800 判斷上線狀態）。
- **OP100 樽頸實測（07-27）**：OP catalog 311 → 282 有 PSA POP（最新 07-26）→ **196 張 POP≥1000
  過官方 gate** <!--@verified 2026-07-27 id=op100.pop_qualified expect>=196 ttl=7 sql=SELECT COUNT(*) FROM (SELECT p.variant_id FROM market_grader_population_observation p JOIN catalog_variant v ON v.id=p.variant_id WHERE p.grader_code='PSA' AND v.tcg_code='one-piece' GROUP BY p.variant_id HAVING MAX(p.top_grade_population)>=1000) t-->
  → 63 張有過價 → 得 11 張價喺 48h 內。**樽頸係價格覆蓋，唔係 POP**：133 張 POP 合格但一行價都冇。
  修法 = 擴 OP 價格採集（snk/ebay 已有 collector），**唔係**降 gate、**唔係**掃 POP。live 得 20 張
  係 evaluation 時 fresh-price 卡少嘅直接後果。
- **`catalog_printing_identity` 通電（identity-batch）**：0 → **68 行**
  <!--@verified 2026-07-27 id=db.printing_identity.rows expect>=68 sql=SELECT COUNT(*) FROM catalog_printing_identity-->
  —— 19 組真重複收斂（19 canonical + 20 duplicate）+ 10 組 false-dup 送 review（29 行 review）。
  opaque_id 零變動、腳本冪等。證據包 docs/evidence/2026-07-27-identity-batch/。**3 個裁決等用戶**。

**真 Top 300 推導 + POP 覆蓋真相（2026-07-26 深夜）**
- 證據包 [docs/evidence/2026-07-26-top300-derivation/](docs/evidence/2026-07-26-top300-derivation/)：
  `FINDING.md` + 可重跑 `produce_top300.py` + `top300.csv`（257 行）+ `gap33.csv`（33 行）。
- **結論**：eval 8（07-26 06:28 建，1,468 行 = 全 roster，lock 9）`metric_status='ready'`
  得 **257** 張 <!--@verified 2026-07-26 id=top300.eval8.ready expect=257 ttl=14 sql=SELECT COUNT(*) FROM market_candidate_daily_snapshot WHERE evaluation_id=8 AND metric_status='ready'--> ——
  真市值榜深度係 257 唔係 300。accumulating 1,186 張入面 **1,181 張有 POP 冇價**（樽頸鐵證）。
- **POP 唔係樽頸**：gemrate/PSA 新鮮度 97.8% fresh / 99.5% ever。缺口淨返 **33 張**
  （25 stale 停 07-21 + 8 never，全部 One Piece，全部有 gemrate id mapping，0 斷鏈）
  <!--@verified 2026-07-26 id=top300.pop_gap expect<=33 ttl=14 sql=SELECT COUNT(*) FROM market_candidate_daily_snapshot s LEFT JOIN (SELECT variant_id, MAX(observed_date) d FROM market_grader_population_observation WHERE grader_code='PSA' AND source_code='gemrate' GROUP BY variant_id) p ON p.variant_id=s.variant_id WHERE s.evaluation_id=8 AND (p.d IS NULL OR p.d < '2026-07-24')-->。
  ⚠ 8 張 never 入面 **3 張靠 snkrdunk/ebay 非權威 PSA POP 入咗 ready**，出街前要 gemrate 或人手覆核。
- **唔使做**：全宇宙 POP 重掃（用戶原本假設要）。**要做**：擴價格覆蓋（§5 P0-組③）+ 補 33 張（§5 P1-組②）。
- 用戶問嘅「數據有冇消失」：**冇**。表全部 append-only，POP-ever 1,590 ≥ roster 1,468，
  佢記得嘅「~1,900」≈ POP-ever 1,590。

**六項投訴排查 + Google Drive 交接歸檔包（2026-07-26 夜）**
- **「熱力圖消失 + theme/語言/貨幣/share 四個掣全部死」係同一個根因，app code 冇壞。**
  `heatmap.tsx` 啲 tile 係 client-only（`useEffect` 裝 `ResizeObserver` 量完 frame 先 render，
  SSR HTML 實測 `heatmap-tile` 出現 **0** 次係設計）；React 19 hydration retry 行
  `requestAnimationFrame`，**背景/hidden tab 唔 fire rAF → 成頁零 hydration** →
  空 heatmap + 全部掣冇 listener。用可見 tab 重開就正常。SSR 健康已驗：
  curl 主頁 1,146,126 bytes、`</html>` 完整、generation `aaca5dd2ee9f`。
- **07-26 daily 實況**：09:30 JST run 死於 GemRate 7200s timeout，14:12 JST 重跑成功，
  index snapshot 15:28 JST 落地，`verify_daily_run` **5 PASS / 1 FAIL（volume_floor）**：
  07-25 資料日 ebay 66→**0** 行、snkrdunk 142→**0** 行（gemrate/snk_psa10 正常）。
  eBay/SNKRDUNK 斷供係真 degradation，歸 Part② eBay 補量隊列，唔係管道斷鏈。
- **交接歸檔包（用戶要求單一 folder 上 Google Drive）**：
  `C:\Users\jackson0202\Documents\Playground\cardz-market-cap-handoff-20260726\`（repo 外），
  **778MB / 13,509 檔**。`db/`（canonical seed 19.5MB + manifest，verify 通過
  seedSha256 `b7a8ed26…`）· `images/`（G10 run `100a8860…` 146MB，456 個 DB 資產全中）·
  `snapshot/`（local-serve 出街快照）· `docs/` 全量 · `source/` 全源碼
  （排除 node_modules / .next / 任何 .env / publish-staging；secret sweep 零命中）。
  說明書 `README_HANDOFF.md` 喺包內（還原步驟、驗證命令、排除清單、已知問題）。

**contentSha256 跨語言序列化 bug 根修 + 本機 :3800 接 production 快照（2026-07-26 深夜）**
- **病因**：TS `publicSnapshotContentSha256`（[validate.ts](packages/market-data/src/validate.ts)）
  係 parse 完 restringify 先 hash；Python 就 hash 自己序列化文字。整數值 float
  Python 寫 `0.0`／JS 重寫做 `0` → 兩份文字差 430 bytes → production 快照 hash 永遠
  對唔上（demo seed 冇整數值 float 所以一直冇事）。實測全快照 24,009 個 float lexeme
  只有 6 個 distinct 整數值；16,015 個非整數 float 零 mismatch，指數寫法零出現。
- **修法（producer 側，`validate.ts` 一行冇郁）**：[canonical_public_snapshot.py](pipelines/canonical_public_snapshot.py)
  加 `js_safe_numbers()`——整數值 float → int；non-finite／指數寫法 fail-closed raise。
  `stable_json`（hash）同 `atomic_json`（寫檔）行同一份正規化，hash 同磁碟文字冇得分家。
  測試 +3（`JsSafeNumbersTests` 釘死序列化 bytes）。
- **:3800 接線**：[server-snapshot.ts](apps/web/src/lib/server-snapshot.ts) `loadMarketSnapshot`
  dev 分支加 guard——設咗 `MARKET_DATA_SNAPSHOT_PATH` 先行 `loadNodeSnapshot()`
  （同生產同一條 `assertPublicSnapshot({production:true})` 閘），冇設照舊派 demo seed。
- **新檔歸位**：`data/runtime/local-serve/snapshot.json`（gitignored；邊個寫 =
  `canonical_public_snapshot.py --production --output`；邊個讀 = dev server 經
  `MARKET_DATA_SNAPSHOT_PATH`；✅ 用緊）＋ `apps/web/.env.local`（gitignored；人手維護；
  Next dev 自動 load；✅ 用緊 —— delete 佢即退返 demo seed）。
- **驗證**（2026-07-26 深夜實測）：快照 `canonical_20260725_aaca5dd2ee9f` TS validator
  production:true PASS；:3800 主頁 HTML demo seed 零出現、`2026-07-25` 1,132 處；
  `/card/<top1>`／`/graders/PSA`／`/watchlist`／`/api/v1/market` 全 200；tsc 乾淨。
  **git 入面 `data/public/seed-snapshot.json` 一個 bit 冇掂。**
  ⚠ 換數據要重啟 dev server（`loadNodeSnapshot` 進程內 cache）。

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
| ~~P2-3~~ | ~~`topGrade` 出字面 `"top"`~~ | ✅ **2026-07-26 快照層修咗** | `top_grade_label()` 將 literal 'top'／空值回退 `TOP_GRADE[grader]`。regen 實測 literal "top" **1,108 → 0**。DB 9,862 行 label 根修（ingest 層）留 Part②，見 [BETA_PLAN_20260726.md](docs/BETA_PLAN_20260726.md) §3.5 |
| P2-1/2 | 市值 delta、成交 delta | ✅ 2026-07-26 已修 | schema 加咗 `marketCapChangePct` / `trackedSalesChangePct`，producer 用 `(1+Δ價)(1+ΔPOP)−1` 組合、成交行真環比。實測 seed 30d **14/100 張卡舊碼箭嘴指錯方向**（rank 2 −76,503 → +2,401,322）。詳見 §4 |
| P3 | POP 每日覆蓋 → ≥90% | ⏳ | 唔搞掂呢個，上面所有「等時間」嘅日期都冇意義。**要連 TAG 一齊修** |
| **P3-新** | **10 萬條 quarantine / 203 條裁決積壓** | 🔴 未知數據債 | 94 次 run 累計 quarantined **70,474** + rejected **32,738**，`market_identity_review_queue` **203 條 pending** —— 全部冇人睇過。閘一直有響，係冇人聽 |
| ~~—~~ | ~~eBay sold comps 採集~~ | ✅ **唔係工程項** | 實測 **3,824 行 / 343 卡 / 92 個日期（04-25 → 07-25）**，已經每日跑緊 3 個月，覆蓋卡數多過 snk_psa10。舊講法「92 行 / 59 variant / 2 個日期」係**讀錯咗**（92 係日期數）。剩返擴覆蓋率，唔係起採集器 |
| **P0-組③** | **Top300 價格覆蓋擴張（組裝模式步驟③）** | 🔴 用戶現行步驟 | 真市值榜深度 257/300，**1,181 張有 POP 冇價**（OP accumulating 169 / pokemon 1,017）。**第一子步唔係爬蟲，係價源 identity 補洞**：OP roster 181 張連 ebay/snkrdunk mapping 都冇，mapping 唔起好收集器擴極都冇用（pokemon 邊要同款審計，未量）。素材：private-source-map `one-piece-*`、`source-crosswalk.json`、G10 disk 559 卡（DB 只 join 到 344，差 ~215）。55 張 out-of-roster priced OP **入唔入圍等用戶決定**（roster 係 lock 9，唔准自行擴）。價係 flow data 冇得事後補——每遲一日就永久少一日。見 §4 + §1 步驟⑤ |
| **P1-組②** | **33 張 POP 缺口卡補掃** | ⏳ 有兩條路 | 全 One Piece（25 stale 停 07-21 + 8 never），gemrate id 全齊 0 斷鏈。路 A：`gemrate_source.py public-card-dump` 逐張（無 key 無 quota，33×~4.4s≈2.5 分鐘）；路 B：摺入 07-27 00:30 UTC quota 窗。名單：[gap33.csv](docs/evidence/2026-07-26-top300-derivation/gap33.csv) |

**其他排咗隊**：**Top-300 逐卡外部連結**（07-27 用戶交帶：每張卡出 Cardland / SNKRDUNK /
eBay / PSA 官網直達 link）、**POP 官方源優先 + click-through**（07-27 用戶交帶：POP 數
優先用 PSA 官方數（非 GemRate 匯總），POP 數字本身做 link 跳去 PSA 官網對應頁）、
~~EB02-010 錯圖重做~~（✅ **07-27 完成**，新圖 sha `b7e4d2e1` 已出街，見 §4）、
**`manifests/image-qc.json` 去重**（624 條記錄得 420 個唯一 publicId，**204 條重複**；
`upsert_qc()` 只換第一條 match，delta skip 判斷可能讀到舊條目 —— 修法係一次過 groupby publicId
留最新再收窄 upsert）、
seed 刷新 + 本地 site 驗收、排程加 `--publish`、soak Day 1 驗收、
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

**D9 — 組裝模式：用戶一步步帶住做，唔准發散。**（07-26 深夜新增，用戶欽點）
用戶原話見 §1。每步指示**即場寫低（呢份文 + memory）先執行**；只做該步範圍；
隔籬發現嘅問題記低唔准追。優先級 = **真確性 > 速度 > 覆蓋面**
（「我唔想個數據出到街俾人質疑」）。圖片／metadata／小故事 DEFER 到 Top 300 穩定。
呢條蓋過本文其他優先次序（包括 §1 AWS 段嗰句「蓋過所有」——嗰個目標已達成，
組裝模式係之後嘅新指令）。

**D10 — README scheduler 禁令收窄：每日價/POP 唔准另裝，freeze/sweep/TAG 豁免。**（07-27 用戶裁決「改文檔認可」）
背景：doc-rot 審計 STALE-7 發現 README 寫「唔准裝任何獨立 GemRate/SNK scheduler」，
但實機有 freeze oneshot 兩個 + TAG capture 一個，全部行緊。用戶裁決以現狀為準——
呢啲 task 係刻意嘅獨立節奏，唔係違規。README data-routing 段已改（註明 user-ratified 2026-07-27）。
`run_daily.py` 仍然獨佔每日 run ID 同 publish gate，呢層冇放寬。

**D11 — POP 數據有衝突，一律以 GemRate 嘅 POP 數為準。**（07-27 用戶裁決）
用戶原話：「按道理，佢哋同一個資訊來源應該係對嘅，唔會有出入。如果大家有衝突嘅話，
請根據 GEM rate 嗰個 POP 數為準。」
事實澄清：G10 磁碟歷史（833 個 history_full.json）**本身就係 GemRate 嘅數**——
264 個負 delta 步係 GemRate 自己歷史序列內部嘅跳動（例：variant 336 BGS 68→1），
唔係兩個來源打交。原則應用：入庫層照錄 GemRate 原數（as-is，唔准自己「修正」）；
展示層「population never shows negative delta」紅線係另一層，維持不變
（前端 population_series() 直讀磁碟，唔受入庫影響）。
G10 歷史 backfill 本身做唔做未拍板——唔擋 live，記隊列；真係做先起 append-only producer
（現有 UPSERT 會 mutate 2,761 行歷史，禁止用）。

---

## 7. 地雷 —— 踩過，唔好再踩

- **:3800 本地驗收一律用 `npx next dev -p 3800`，唔准用 `next start`。**（07-27 踩過）
  `next start` serve 嘅係 `.next` **舊 build** —— working-tree 嘅 `server-snapshot.ts`
  改動（`MARKET_DATA_SNAPSHOT_PATH` 支援）從未 build 過入 production bundle，
  於是 `.env.local` 正確都照出 demo seed（`2026-07-22T09:48:26` 嗰份）。
  dev mode 即時反映 working tree，先係本地驗收嘅正確形態。
  另外兩個連帶陷阱：①TaskStop/Ctrl-C 殺咗 shell 唔等於殺咗 child node process，
  port 3800 會俾殭屍霸住 EADDRINUSE —— 用 PowerShell
  `Get-NetTCPConnection -LocalPort 3800 -State Listen` 揾 PID 再 `Stop-Process -Force`；
  ②producer 重寫 snapshot 檔之後**必須重啟 dev server** —— `loadNodeSnapshot()`
  個 module-level cache 係進程級，唔會自己 reload。
- **「成版掣死晒 + heatmap 空白」唔好當 app bug 查。** heatmap tile 係 client-only
  （`ResizeObserver` 量完 frame 先出 tile，SSR HTML `heatmap-tile` 0 次係**正常**），
  而 React 19 hydration retry 用 `requestAnimationFrame` —— **hidden/背景 tab 唔 fire rAF，
  成頁永遠唔 hydrate**：theme/語言/貨幣/share 掣全部冇 listener。診斷次序：
  先 curl SSR（`</html>` 有冇 + generation id 啱唔啱），再叫用戶用**可見 tab** 重開，
  唔好一上嚟就改 code。
- **`canonical_public_snapshot.py` 唔加 `--output` 會覆寫 `data/public/seed-snapshot.json`。**
  永遠寫 `--output temp/xxx.json`。
- **`pytest` 唔喺 `.venv-backend`。** 用系統 `python -X utf8 -m pytest`。
- **`tracked-gemrate-ids.txt` 係 CRLF**，其他 roster 檔係 LF。用 `comm` / `grep -f` / `sort -u`
  比對會靜靜報零重疊。一律用 Python `line.strip()`。
- **`data/private/gemrate/cards/` 嘅檔案數 ≠ roster 進度。** 個目錄同時累積 candidate 探索卡
  （2978 個目錄 vs roster 1468）。一定要同 roster 取交集先算覆蓋率。
- **DB 時鐘慢本機大約一日**（`CURDATE()` 返 07-25 而本機 07-26）。
  ~~07-26 凌晨曾實測 snapshot 停喺 07-24、疑似斷鏈~~ → **07-26 10:38 UTC 重跑
  `verify_daily_run.py`：`PASS snapshot_freshness`，3 個指數都有 effective_date≥07-25
  新行，鏈已癒合**（當日 09:30 JST run 補返）。判斷斷鏈一律以 `verify_daily_run.py`
  即場輸出為準，唔好引本文舊量度。
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
  ~~**168h 新鮮度閘救唔到你**：閘只讀 `populationPsa10`（由 PSA 行嚟，gemrate 健康），
  `graderPopulations['TAG'].asOf` 冇任何地方查年齡，`latest_populations()` 又冇 date floor。~~
  **→ 2026-07-26 已封**：producer `card_from_row()` 加咗 168h date floor
  （`POPULATION_STALE_HOURS`），過期 grader 觀測成個 block 歸 unavailable，
  唔會再靜靜餵過期數扮 ready。TAG 採集本身（tag_pop_data.py 條 raise）仲係要修，
  但爛數而家會**熄**，唔會**呃人**。

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
| **`docs/BETA_PLAN_20260726.md`** | **現行階段計劃（07-26 起）**：Beta 上線三步（①前端數據補齊 ②DB 整理 ③自動化押後）、風險日曆、驗收標準。操作人決策記錄喺 §0 | 階段目標／次序改變 |
| `docs/STAGE_REVIEW_20260726.md` | 上一階段（AWS 交付衝刺）凍結盤點：五類反覆錯誤 + ghost source 發現。當歷史讀 | 唔改（凍結） |
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
