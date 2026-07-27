# G10 = 我哋個底（2026-07-26 全量實測）

> 用戶定位（2026-07-26）：
> 「g10 所有野做齊我地個底，我哋 base 於佢個底嘅內容，再擴展喺唔同嘅渠道，砌返啲新卡落去。
> 佢嗰個內容、分類同埋表示嘅東西，其實係最好嘅。我哋一定要做得好過佢，但係做到 G10 先係基本。」

即係話 **G10 唔係「參考網站」，係我哋嘅 baseline 數據集**。
G10 有嘅每一樣嘢，我哋都要有；跟住先喺其他渠道擴展、加新卡。
呢份文係嗰個底嘅完整清單同接線狀態。

**位置**：`../grade10-scraper/`（同層 sibling repo，唔喺呢個 repo 入面）
**本機鏡像**：`pipelines/grade10_full_freeze.py` 抄佢個檔案樹入私有 landing
（⚠ 呢個腳本同 GemRate 掃**完全無關**，名似而已）

---

## 1. 規模對照：G10 有幾多、我哋接咗幾多

| 項目 | G10 有 | 我哋 DB / snapshot | 接線狀態 |
|---|---:|---:|---|
| 卡目錄 | **641**（snkrdunk 480 + altxyz 161） | 251 張公開 | 🔌 唔係全部對得返 |
| **eBay PSA 10 成交** | **559 卡 / 9,489 條**（90 日滾動） | `market_price_observation` eBay **92 條 / 59 卡** | ❌ **未接，最大單一缺口** |
| eBay PSA 9 | 561 卡 | 冇 | ❌ 未接 |
| eBay BGS 10 / BGS BL | 561 卡 各一 | 冇 | ❌ 未接 |
| eBay CGC 10 | 359 卡 | 冇 | ❌ 未接 |
| SNKRDUNK 分級成交 | `apparel_grade_22`（PSA10）488 卡，另 12 個 grade 檔 | `snk_psa10` 114,755 行 | ✅ 已接（走自己條線） |
| 鑑定人口 | `populations.json` 641 卡 | `market_grader_population_observation` 7,904 行 / 5 日 | ⚠ 部分 |
| **AI 研究報告** | `summary_en.json` **480 卡**（平均 2,619 字） | `catalog_variant_locale` **0 行** | 🔌 **未接**，見 §3 |
| 卡片 metadata | `asset_info.json` 636 卡 | `catalog_variant` 1,590 行 | ⚠ 部分 |
| 指數 | `data/index/{ptcg,ptcg100,opcg}/` | `market_index_snapshot` | ✅ 概念對應 |
| 累積成交史 | `data/sales_cache/{source}/{id}.json`（只加唔刪） | — | ❌ 未接 |
| 卡圖 | `data/images/` | 251 張入庫 | ⚠ 部分 |
| 分析 | `data/analytics/` | — | ❌ 未睇過 |

---

## 2. 接線陷阱（實測，唔好靠估）

### 2.1 `summary_jp.json` **唔係日文** —— 480/480 個檔同英文一模一樣

實測全 480 對檔案，`summary_jp.json` 同 `summary_en.json` 逐字相同，零個例外。

**後果**：G10 幫到手嘅係**研究**（發行背景、稀有度、聯乘、市場脈絡），
**唔係翻譯**。zhTW / zhCN / ja 三語仍然要自己寫。
唔好見到個檔名叫 `_jp` 就當有日文素材。

### 2.2 eBay 日期撈埋兩種格式

9,489 條入面：

| 格式 | 條數 | 範圍 |
|---|---:|---|
| ISO `YYYY-MM-DD` | 7,731 | 2026-04-25 → 2026-07-21 |
| 相對 `N day(s) ago` | 1,758 | 最近 0–3 日 |
| 其他 / 空 | **0** | — |

即係一個 **90 日滾動窗口**：舊嘅落咗 ISO，最近幾日仲係相對詞。
**入桶前一定要先解析相對日期**（基準用檔案 mtime，唔好用 `now()` —— 檔可能係幾日前抄落嚟）。
直接 `min()`/`max()` 字串排序會得出 `0 day ago → 3 days ago` 呢種廢答案，我第一次就係咁中招。

### 2.3 join 唔可以淨靠卡名

| 方法 | 對到 | 可信度 |
|---|---:|---|
| `catalog_source_identity` 證據鏈 | 395 / 1,590 variant | ✅ 唯一有證據 |
| 卡名 exact match | 251 / 251 | ❌ **假嘅** —— G10 641 個目錄得 319 個 unique 卡名 |
| 卡名 + 系列名 | 50 / 251 | ⚠ 系列名格式完全唔同 |
| 卡名 + collector number | 補到少量 | ⚠ |

G10 系列名係緊湊代碼（`SV8a: Terastal Fest ex`、`XY-P: XY Promos`），
我哋係描述式長句（`2024 Scarlet and Violet Terastal Festival Ex Japanese Special Art Rare`）。
兩者**冇得直接字串比**。

`catalog_source_identity` 三個 source：`gemrate` 1,496 / `snkrdunk` 336 / `ebay` 59。
`snkrdunk` 對 G10 `data/cards/snkrdunk/{id}/`，`ebay` 對 `data/cards/altxyz/{uuid}/`
（altxyz 目錄名就係 UUID，同 `catalog_source_identity.external_entity_id` 一樣）。

**擴大 identity 覆蓋率係解鎖 G10 全部數據嘅前置條件。**

---

## 3. `catalog_variant_locale` —— 為呢件事起，一行都冇寫過

```
variant_id · locale_code · localized_name · localized_set_name · market_story · updated_at
```

欄位同我哋要嘅譯名／譯系列名／故事**完全對應**，**0 行**。

而家四語文字行緊嘅路係 flat JSON：
`data/editorial/card-names.json` · `set-names.json` · `top100-stories.json`
→ `pipelines/editorial_localization.py` → snapshot。

**點解 flat JSON 係暫時方案**：`top100-stories.json` 用公開 `cmc_*` id 做 key，
而 `cmc_*` 會隨 catalog 世代漂移 —— 實測 100 條故事得 **50 條** 仲對得返現行 top100，
另一半變咗孤兒。`variant_id` 唔漂。

**未接，因為**：出街死線優先，flat JSON 路線已經驗證通過（251/251 名 + 系列名落地）。
**接線工作項**：`editorial_localization.py` 改成 DB 優先、flat JSON fallback，
再寫一個 `pipelines/editorial_locale_sync.py` 把三個 JSON 灌入 `catalog_variant_locale`。

---

## 4. 排優先次序

| # | 缺口 | 值幾多 | 成本 |
|---|---|---|---|
| 1 | **eBay PSA 10 成交入庫** 92 → 9,489 條 | 路線更新第 4 點「eBay = PSA10 成交唯一真源」直接兌現；成交額／sparkline 有真數 | 中（要寫日期解析 + identity 擴充） |
| 2 | **identity 覆蓋率** 395 → 641 | 解鎖上面全部；一次做好，之後 G10 加卡自動有 | 中 |
| 3 | `catalog_variant_locale` 駁線 | 譯文唔會再隨世代漂走 | 低 |
| 4 | 多廠成交（PSA 9 / BGS / CGC） | 跨廠溢價分析，G10 有我哋冇 | 低（同 #1 同一條線） |
| 5 | `sales_cache` 累積史 | 比 90 日窗口更長嘅歷史 | 低 |
| 6 | `data/analytics/` | 未盤點 | — |

---

## 5. Stealth 規矩（唔准犯）

路線更新第 3 點：**爬取時序唔准貼住 G10 自己嘅日程**。

~~實測現況：`cardz-market-cap-daily.timer` 00:30 UTC，`cardz-grade10-discovery.timer` 23:43 UTC —
**只差 47 分鐘**，而 daily 係唯一冇 `RandomizedDelaySec` 嗰條。~~
**已於 2026-07-26 修正（task #29，用戶 call 咗）。**

修正後（實測 `systemd-analyze verify` 通過，`systemd-analyze calendar` 核對過 JST 換算）：

| unit | OnCalendar | jitter | 實際窗口 (JST) |
|---|---|---|---|
| `cardz-market-cap-daily.timer` | 00:30 UTC | 1800s | 09:30–10:00 |
| `cardz-grade10-discovery.timer` | 21:17 UTC | 1500s | 06:17–06:42（翌日 JST）|

兩條都有 jitter，中間間距每日喺 2h48m–3h43m 之間浮動 —— 冇固定 offset 可以指紋化。
**呢度嘅重點唔係「有冇 jitter」，係「兩條 timer 之間嘅間距係咪定值」**：兩條準時到秒
嘅 timer 相隔恆定 47 分鐘，同照抄一份日程係同一件事。

daily 嘅 jitter 唔可以無限加大，有兩條硬邊界：最遲開跑唔准跨 UTC 日（`market_run_id`
由 UTC 日期砌），最遲完成要早過 05:07 UTC 嘅 watchdog。兩條邊界由
`tests/test_daily_scheduler_contract.py::test_outbound_timers_are_jittered_and_not_a_fixed_offset_apart`
直接由 unit 檔讀返出嚟驗，改時間改到踩線就會見紅。

讀本機 G10 檔案樹**唔涉及爬取**，唔受呢條規矩限制。

---

## 6. 呢份文邊個寫 · 邊個讀 · 而家有冇人用

| | |
|---|---|
| **檔喺邊** | `docs/G10_BASELINE.md`（呢份） |
| **邊個寫** | 人手；數字由 §1 表格嗰啲一次性量度得出，重量方法見各節 |
| **邊個讀** | 做 G10 相關接線之前一定要讀；`CLAUDE.md` 有指向 |
| **而家有冇人用** | ⚠ 未有腳本自動重量。想重驗 §1 數字要自己跑，**冇 audit 腳本** —— 呢個係已知缺口 |
