# OP 卡 PSA POP 缺口 — 決策包

| | |
|---|---|
| **量度日期** | 2026-07-27（JST） |
| **重跑指令** | `set -a && . data/runtime/config/backend.env && set +a && python -X utf8 docs/evidence/2026-07-27-op-pop-decision/produce_op_pop_gap.py` |
| **一句結論** | 缺 POP 嘅 76 張 OP 卡全部喺 roster 以外，抓晒佢哋最多得 **6 個 API call**（其餘 70 張連 gemrate_id 都未有，要行免費 keyless 解析）；而真正令 OP 榜停喺 42 深度嘅係 **132 張 roster 內 POP 已達標但從未有過價** 嘅卡 —— 樽頸唔喺 POP。 |
| **量度時前提** | evaluation_id=9（`market_alert_evaluation` 最新行，effective_date 2026-07-26）；OP 榜 = `market_index_snapshot` index_code='one-piece' 最新 id=24；入榜閘照 [pipelines/market_alerts.py](../../../pipelines/market_alerts.py) 生產碼，唔係 `ranking_derivation.py` 嗰套 |
| **本次操作** | 純唯讀。零 GemRate API 調用，冇改 roster / lock / 任何數據檔 |

---

## TLDR

1. **「71 張」呢個數要重新定義。** roster 內 227 張 OP 卡**冇一張**缺 gemrate PSA POP（缺口 = 0）。缺 POP 嘅係 **catalog 內、roster 以外嘅 76 張**（`catalog_variant` OP 共 311 張，roster 227，榜外 84，其中 76 缺 gemrate POP）。原本 71 出自 [2026-07-26 three-boards pack](../2026-07-26-three-boards/FINDING.md)，佢淨計 `snk_psa10`/`ebay` 兩個價源；今次計埋 `g10_kline`（同樣係 PSA10 日 K 線）所以係 76。

2. **抓呢批唔係 quota 問題，係 identity 問題。** 76 張入面得 **6 張有 gemrate_id**，可以即刻用 ID 抓 → **6 個 call**（current POP）或 **12 個**（連週線歷史）。剩低 **70 張連 gemrate_id 都冇**，唔可以用 ID 抓，要先行 `gemrate_source.py collect`（keyless、零 quota）解 identity。1000/窗口嘅配額喺呢單完全用唔完。

3. **而且 current POP 根本唔使用 key。** `cmd_public_card_dump` 個 docstring 直接寫 "Keyless current POP harvest from exact GemRate public card pages"（[gemrate_source.py:1744](../../../pipelines/gemrate_source.py)），而且生產線 07-25 已經係 keyless 做 primary（`direct=disabled`，見 [docs/HANDOFF.md:117](../../HANDOFF.md)）。key 唯一獨有嘅係**週線 POP 歷史**（`/population/history`）。

4. **抓晒都只係由 42 → 64。** 76 張入面 **54 張過唔到 POP >= 1000 呢道閘**（22 張過到）。最好嗰張新卡只排到第 **26** 位，top 10 一張都入唔到。

5. **對照組先係重點：132 張 roster 內 OP 卡 POP 已 >= 1000，但一條價都冇。** 佢哋每張只差一個價就入到榜。呢個數大過抓 POP 嘅全部預期收益（+22），而且零 GemRate quota。

6. **「~07-29 到期」冇硬證據。** 全部出處都係文檔散文加「~/約」，唯一具體 artifact 係 [deploy/windows/gemrate-freeze-oneshot.ps1:48](../../../deploy/windows/gemrate-freeze-oneshot.ps1) 一個參數預設值 `$KeyDeadlineUtc = '2026-07-29T00:00:00Z'`，係 agent 自己寫落去，唔係供應商聲明。`gemrate.env` 內零註解、零 expiry 欄位。

---

## 1. 「冇 POP」嘅準確定義

**表**：`market_grader_population_observation`

**join 條件**（照 [produce_three_boards.py](../2026-07-26-three-boards/produce_three_boards.py) 同生產線一致）：

```sql
-- 一張卡「有 gemrate PSA POP」= 下面呢條攞到行，而且 top_grade_population 非空非零
SELECT o.variant_id, o.observed_date, o.top_grade_population
FROM market_grader_population_observation o
JOIN (SELECT variant_id, MAX(observed_date) AS md
      FROM market_grader_population_observation
      WHERE grader_code='PSA' AND source_code='gemrate'
      GROUP BY variant_id) m
  ON m.variant_id = o.variant_id AND m.md = o.observed_date
WHERE o.grader_code='PSA' AND o.source_code='gemrate'
```

`variant_id` → `catalog_variant.id`。`source_code='gemrate'` 同 `grader_code='PSA'` 兩個都要，因為同一張表仲有 `snkrdunk` / `ebay` 來源同 `CGC/BGS/TAG/SGC` 等級。

### 母體拆解（全部 `COUNT(*)`，無用 `information_schema`）

| 口徑 | 張數 | 出處 |
|---|---:|---|
| `catalog_variant` tcg_code='one-piece' | 311 | `COUNT(*) GROUP BY tcg_code` |
| 其中喺現行 roster（`market_universe_lock is_current=1`，lock id=9） | 227 | join `market_universe_member` |
| 榜外（唔喺 roster） | 84 | 311 − 227 |
| **roster 內缺 gemrate PSA POP** | **0** | LEFT JOIN 上面條 SQL |
| **缺 gemrate PSA POP（全 catalog）** | **76** | 同上，去掉 roster 過濾 |
| 其中連 gemrate_id 都冇（`catalog_source_identity source_code='gemrate'`） | 70 | `NOT EXISTS` |
| 缺 gemrate POP 但有他源 PSA POP（ebay/snkrdunk） | 47 | `LATEST_ANY_PSA_POP` |
| 完全冇任何來源 PSA POP | 29 | 76 − 47 |

**76 張全部 `identity_status='confirmed'`、全部有價、價全部係 `g10_kline` 且 `price_as_of = 2026-07-25`。**
`g10_kline` 係 PSA10 日 K 線收盤（[g10_kline_price_bridge.py:4](../../../pipelines/g10_kline_price_bridge.py)「47,582 條 PSA10 日 K 線」），做市值分子口徑上成立，`source_priority=300` 即只喺該卡該日冇直採價先出頭。

---

## 2. POP 假設（呢步最易錯，寫清楚）

**唔可以用 roster 中位數。** roster 227 張 OP 卡 gemrate POP 中位數 = **1392**、74.0% >= 1000。但缺 POP 嗰 76 張全部係 promo / serial / championship 高價稀有卡，同 roster 主流卡唔同類 —— 用 1392 會系統性高估（例：`Monkey D Luffy L Serial Number Flagship [ST01-001]` 實測 snkrdunk POP 得 **161**，用 1392 算會得出 $229M 市值，比現榜第一名貴三倍，明顯荒謬）。

**正確參考類別** = 呢 76 張自己入面有他源 PSA POP 嗰 47 張嘅實測分佈：

| 統計 | 值 |
|---|---:|
| n | 47 |
| min | 143 |
| p25 | 445 |
| **median** | **924** |
| p75 | 2,095 |
| max | 4,727 |
| **>= 1000 佔比** | **46.8%**（22/47） |

**本報告採用嘅 POP 規則**（CSV `assumed_pop_basis` 欄逐張標明）：

- 該卡自己有他源 PSA POP（47 張）→ **直接用實測值**，`observed_ebay` / `observed_snkrdunk`。呢啲唔係假設，但要注意係他源，同 gemrate 口徑可能有細微差。
- 完全冇 POP（29 張）→ 用可比類別中位數 **924**，標 `comparable_median_assumption`。**呢 29 張嘅市值純屬假設。**

⚠️ 中位數 924 本身**低過** 1000 入榜門檻，即係話「典型」一張缺 POP 嘅 OP 卡係入唔到榜嘅。

---

## 3. 名單（top 30；完整 76 張見 CSV）

完整名單：[op_no_pop_cards.csv](op_no_pop_cards.csv)（76 行，含 `variant_id` / `opaque_id` / identity / 他源 POP / 價源 / 兩種市值口徑）

價全部 `g10_kline @ 2026-07-25`，所以下表略去價源欄。

| # | canonical_name | set_name | collector_no | 最新價 USD | POP（假設/實測） | 依據 | potential 市值 USD | 過 POP≥1000? | 有 gemrate_id? |
|---:|---|---|---|---:|---:|---|---:|:-:|:-:|
| 1 | Monkey D Luffy L Serial Number Flagshi… | Opened | ST01-001 | 164,812 | 161 | 實測 snkrdunk | 26,534,792 | N | 有 |
| 2 | Roronoa Zoro Opened L Promotional Card | Serial Zoro | OP12-020 | 24,417 | 924 | **假設** | 22,560,985 | N | 冇 |
| 3 | Boa Hancock Top Prize | Promotional Card Champions | OP14-112 | 23,196 | 924 | **假設** | 21,432,938 | N | 冇 |
| 4 | Monkey.D.Luffy L-SPC | Extra Booster Anime 25th | OP05-060 | 13,200 | 924 | **假設** | 12,196,800 | N | 冇 |
| 5 | Monkey D. Luffy | 2022 Super Pre-Release | P-001 | 17,100 | 614 | 實測 ebay | 10,499,400 | N | 有 |
| 6 | Roronoa Zoro Parallel | Championship Battle Best 1 | ST01-013 | 8,534 | 924 | **假設** | 7,885,065 | N | 冇 |
| 7 | Shanks | 2023 Promos Event Prize | OP01-120 | 24,400 | 314 | 實測 ebay | 7,661,600 | N | 有 |
| 8 | Gol D. Roger | 2024 Emperors In New World | OP09-118 | 22,000 | 326 | 實測 ebay | 7,172,000 | N | 有 |
| 9 | Nami | 2022 Romance Dawn Alt | OP01-016 | 4,000 | 1,752 | 實測 ebay | 7,007,982 | **Y** | 冇 |
| 10 | Tony Tony Chopper SR-P | EB Memorial Collection | EB01-006 | 2,750 | 2,406 | 實測 snkrdunk | 6,616,476 | **Y** | 冇 |
| 11 | Monkey.D.Luffy SR-P Promotional Card | Opened | OP07-109 | 18,923 | 300 | 實測 snkrdunk | 5,676,870 | N | 有 |
| 12 | Portgas D Ace SEC-P Serial Number | Opened | OP07-119 | 7,935 | 679 | 實測 snkrdunk | 5,388,143 | N | 冇 |
| 13 | Roronoa Zoro | 2025 Promos Championship | OP09-076 | 11,750 | 445 | 實測 ebay | 5,228,750 | N | 冇 |
| 14 | Trafalgar Law SR-SP Awakening | Comic Parallel | OP05-069 | 2,500 | 2,052 | 實測 snkrdunk | 5,129,979 | **Y** | 冇 |
| 15 | Monkey D. Luffy | 2024 Championship Event | OP07-109 | 22,800 | 218 | 實測 ebay | 4,970,400 | N | 有 |
| 16 | Monkey D Luffy | Champion Ship Prize | ST10-006 | 17,397 | 279 | 實測 snkrdunk | 4,853,724 | N | 冇 |
| 17 | Shanks SR-SP Emperors In | Comic Parallel | OP09-004 | 2,300 | 1,955 | 實測 snkrdunk | 4,496,480 | **Y** | 冇 |
| 18 | Nefeltari Vivi | Promotional Card Champions | OP05-086 | 4,761 | 924 | **假設** | 4,399,395 | N | 冇 |
| 19 | Monkey D Luffy | 2022 Super Pre Release | ST01-001 | 3,600 | 1,199 | 實測 ebay | 4,316,400 | **Y** | 冇 |
| 20 | Boa Hancock SR-P | Premium Booster | ST17-004 | 4,505 | 924 | **假設** | 4,162,500 | N | 冇 |
| 21 | Portgas.D.Ace SEC-RSP CARRY ON | Red Comic Parallel | OP13-119 | 7,935 | 523 | 實測 snkrdunk | 4,150,219 | N | 冇 |
| 22 | Marshall.D.Teach SR-SP Emperors | Comic Parallel | OP09-093 | 2,000 | 2,049 | 實測 snkrdunk | 4,097,980 | **Y** | 冇 |
| 23 | Monkey.D.Luffy Wanted SEC-SPC | Emperors in the New World | OP05-119 | 1,250 | 2,957 | 實測 ebay | 3,696,250 | **Y** | 冇 |
| 24 | Portgas.D.Ace SEC-SP CARRY ON | Comic Parallel | OP13-119 | 2,442 | 1,483 | 實測 snkrdunk | 3,620,982 | **Y** | 冇 |
| 25 | Nami | 2024 Emperors In New World | OP08-106 | 1,700 | 2,095 | 實測 ebay | 3,561,500 | **Y** | 冇 |
| 26 | Monkey D. Luffy | 2025 2nd Anniversary Set | OP09-061 | 1,236 | 2,805 | 實測 ebay | 3,466,980 | **Y** | 冇 |
| 27 | Gear Two | 2025 A Fist of Divine Speed | OP11-080 | 733 | 4,727 | 實測 ebay | 3,465,506 | **Y** | 冇 |
| 28 | Marshall D. Teach | 2024 Emperors In New World | OP09-093 | 3,899 | 874 | 實測 ebay | 3,407,726 | N | 冇 |
| 29 | Marshall.D.Teach SR-SPC 3th Anniv | Gold Background | OP09-093 | 3,650 | 924 | 實測 snkrdunk | 3,372,868 | N | 冇 |
| 30 | Trafalgar Law SEC-SP Royal Blood | Comic Parallel | OP10-119 | 1,150 | 2,868 | 實測 snkrdunk | 3,298,171 | **Y** | 冇 |

**76 張合計 assumed 市值 ≈ $289.6M**（top 10 佔 $129.6M）。**但 54/76 過唔到 POP≥1000 閘**，佢哋嘅市值入唔到榜，只係研究參考。

---

## 4. Quota 成本

### per-card request 數（讀碼確認，唔係估）

[pipelines/gemrate_source.py](../../../pipelines/gemrate_source.py)：

| 路徑 | 函數 | 每張卡 API call | 用途 |
|---|---|---:|---|
| `api-dump`（freeze） | `fetch_card(history=True)` → `/cards/{id}/population` ＋ `/cards/{id}/population/history?interval=week` | **2** | current POP ＋ 週線歷史 |
| `daily`（current only） | `fetch_current_population()` → `/cards/{id}/population` | **1** | 淨 current POP |
| `collect`（identity） | 內部 `/universal-search-query`，Chrome keyless | **0** | 解 gemrate_id |
| `public-card-dump` | GemRate 公開卡頁，keyless | **0** | current POP |

**冇 per-card 分頁** —— 一張卡一個 population endpoint call。

`_api_get` 4 次重試（429 / 網絡 0 / 5xx）；`_run_harvest` 撞到任何 `*_429` 就 return **4** 並印 "QUOTA SPENT … Re-run with --resume"；401/403 return **3**。配額 **1000 call / rolling window**（[docs/DATA_GAPS.md §U-2](../../DATA_GAPS.md)：「一個窗口 1000 call」）。

### 今次呢批嘅實際成本

| 目標 | 可即抓（有 gemrate_id） | 要先 keyless 解 identity | API call（current only） | API call（連歷史） |
|---|---:|---:|---:|---:|
| top 10 | 4 | 6 | **4** | **8** |
| top 30 | 6 | 24 | **6** | **12** |
| 全 76 張 | 6 | 70 | **6** | **12** |

**配額完全唔係約束。** 全抓都只食 6–12 個 call，佔一個窗口 0.6%–1.2%。真正嘅工作量喺 70 張要行 `collect` 解 identity —— 呢步零 quota，但要 Chrome / Playwright 跑內部 search API，係人手時間成本唔係額度成本。

解完 identity 之後，如果只要 current POP，**仲可以完全唔用 key**（`public-card-dump`）。

---

## 5. Key 時間窗 —— 出處審計

grep 全 repo（`docs/` `scripts/` `pipelines/` `deploy/` `data/runtime/config/`）：

| 出處 | 原文性質 | 硬度 |
|---|---|---|
| [docs/HANDOFF.md:29](../../HANDOFF.md) | 「GemRate 試用 key **約 07-29 到期**」 | 散文，帶「約」 |
| [docs/BETA_PLAN_20260726.md:11,48,138](../../BETA_PLAN_20260726.md) | 「GemRate key **~07-29** 到期 / 死」 | 散文，帶「~」 |
| [docs/DATA_GAPS.md:1231,1506](../../DATA_GAPS.md) | 「API key `2026-07-29` 死」「key 一死（07-29）」 | 散文，無引用來源 |
| [docs/SERVER_MIGRATION.md:188](../../SERVER_MIGRATION.md) | 「試用期**約** 07-29 屆滿」 | 散文，帶「約」 |
| [docs/SOAK_RUNBOOK.md:194](../../SOAK_RUNBOOK.md) | 「GemRate key 到期（**~2026-07-29**）」 | 散文，帶「~」 |
| [scripts/build_freeze_pass_c.py:5](../../../scripts/build_freeze_pass_c.py) | 「The GemRate service key dies 2026-07-29」 | 程式註解，肯定語氣但無引用 |
| [deploy/windows/gemrate-freeze-oneshot.ps1:48](../../../deploy/windows/gemrate-freeze-oneshot.ps1) | `[string]$KeyDeadlineUtc = '2026-07-29T00:00:00Z'` | **參數預設值**，最具體嘅 artifact |
| `data/runtime/config/gemrate.env` | **零註解行、零 expiry 欄位**（只 grep `^\s*#` 同 `expir`，冇讀賦值） | 無記錄 |

**結論：出處只有內部文檔同腳本常數，冇任何供應商聲明、email、dashboard 截圖或 API 回應做實證。** 07-29 呢個日期喺 repo 入面第一次出現之後就被其他文件互相引用，形成循環引證。

⚠️ **另一件同日但唔同嘢，唔好撈埋**：[docs/DATA_GAPS.md:8,15,122](../../DATA_GAPS.md) 嗰個 `2026-07-29T00:00:00Z` 係 **TAG freshness 炸彈**（07-22 + 168h，220 條 validation error），同 key 到期無關，只係啱撞同一日。

⚠️ **而且到期影響已經被降級**：[docs/HANDOFF.md:117](../../HANDOFF.md) 記錄 2026-07-26 02:20 JST 實證 —— 07-25/26 backfill log 出 `[daily] 1468 cards, direct=disabled, public-card-page=enabled, mirror=enabled`，即 pipeline 已經以 keyless 做 primary（成功率 ~96%，07-25 入咗 1136 行）；`.env.private` 冇 set `GEMRATE_API_KEY`。**key 到期唯一實質損失 = 週線 POP 歷史（`/population/history`）永久攞唔返**，current POP 唔受影響。

---

## 6. 範圍選項影響 — OP 榜深度

### 入榜規則（讀 [pipelines/market_alerts.py](../../../pipelines/market_alerts.py) 生產碼）

寫 `market_index_constituent` 嘅係 `market_alerts.py`，唔係 `ranking_derivation.py`。閘序：

1. **候選池 = roster only**。`load_candidates()` 由 `market_universe_lock WHERE is_current=1` JOIN `market_universe_member` 取 1468 張。**榜外卡就算有 POP 都入唔到榜，要先開新 universe lock 擴 roster。**
2. **價**：`pick_observation(..., max_gap_days=2)` —— `observed_date` 要喺 `[effective_date−2d, effective_date]` 內，優先序 `snk_psa10/snk`=10 > `g10`=20 > 其他=100。
3. **POP**：同窗口 2 日，`grader_code='PSA'`、`estimated` 假，優先序 `gemrate`=10 > `g10`=20 > 其他=100。
4. `metric_status='ready'` = 價同 POP 兩者齊。
5. **入榜** = `ready` **且** `psa10_population >= 1000` **且** cap 非空（`tracked_indexes()`）。
6. 排序：cap 降序，同值以 variant_id 升序。one-piece 榜**冇張數上限**，ready 就入。

→ **POP 係結構性必要條件，而且必須 >= 1000。** 有 POP 但 < 1000 一樣入唔到。

### 現況（`market_index_snapshot` id=24，effective 2026-07-26）

OP 榜深度 **42**。市值分佈：rank 1 = $71.7M、rank 10 = $14.7M、rank 20 = $8.1M、rank 30 = $5.7M、rank 42（榜尾）= $0.68M。

### roster 內 227 張 OP 卡真實 blocker（[op_roster_blockers.csv](op_roster_blockers.csv)）

| blocker | 張數 |
|---|---:|
| `on_board` | 42 |
| `no_current_price_in_2d_window` | **182** |
| `pop_below_1000` | 3 |
| 缺 POP | **0** |

而嗰 182 張唔係「價過期」，係**一條價都未有過**（`MAX(observed_date) IS NULL`，`market_price_observation` 全表冇佢哋任何 `price_usd > 0` 嘅行）。
**其中 132 張 POP 已經 >= 1000** —— 每張只差一個價就入榜。

### 抓 POP 之後嘅榜深度推算（[board_depth_projection.csv](board_depth_projection.csv)）

推算方法：已實測 POP 嗰批直接數過唔過 1000（唔係估）；完全冇 POP 嗰 29 張用可比類別 46.8% 加權。**所有選項都假設 roster 已經擴咗**（唔擴嘅話新增 = 0）。

| 選項 | 目標張數 | API call（current / 連歷史） | 要先 keyless 解 identity | 實測 POP≥1000 | 未知加權 | 期望新入榜 | OP 榜深度 |
|---|---:|---:|---:|---:|---:|---:|---:|
| top 10 | 10 | 4 / 8 | 6 | 2 | 1.9 | **+3.9** | 42 → **45.9** |
| top 30 | 30 | 6 / 12 | 24 | 12 | 2.8 | **+14.8** | 42 → **56.8** |
| 全 76 張 | 76 | 6 / 12 | 70 | 22 | 13.6 | **+35.6** | 42 → **77.6** |

### 併榜模擬（[merged_board_simulation.csv](merged_board_simulation.csv)）

將 22 張**實測** POP >= 1000 嘅卡插入現行 42 張榜：

- 併榜總數 **64**
- 新卡最好排到第 **26** 位
- 入 **top 10：0 張**
- 入 **top 30：2 張**

即係話：抓 POP 主要係**加長榜尾**，唔會改變榜頭。要 OP100 填滿（100 張），單靠呢 76 張榜外卡（最多 +36）唔夠。

---

## 7. 選項

**以下唔係建議，只係擺低成本 / 風險 / 收益俾你揀。**

### 選項 A — 唔抓，等新 key

| | |
|---|---|
| quota 成本 | 0 |
| 時間窗風險 | 如果 07-29 真係死，`/population/history` 週線資料**永久攞唔返**（keyless 冇呢個 endpoint）。current POP 唔受影響（`public-card-dump` keyless 一直得）。 |
| 榜深度變化 | 0（維持 42） |
| 備註 | 07-29 呢個日期本身冇實證（見 §5）。而且 76 張入面 54 張本來就過唔到 POP 閘，唔抓損失有限。 |

### 選項 B — 只抓有 gemrate_id 嗰 6 張

| | |
|---|---|
| quota 成本 | **6 call**（current only）／ **12 call**（連週線歷史） |
| 時間窗風險 | 極低，一次過幾分鐘搞掂，一個窗口食 1.2% |
| 榜深度變化 | 呢 6 張實測 POP 分別 161/614/314/326/300/218，**全部 < 1000，一張都入唔到榜**（要 gemrate 口徑推翻他源觀測先有機會） |
| 備註 | 純粹係「趁 key 未死攞返啲歷史」嘅保險動作，唔係榜深度動作 |

### 選項 C — 先做 keyless identity（70 張），再抓全部

| | |
|---|---|
| quota 成本 | identity 階段 **0**；之後 **6 call**（已有 id 嗰 6 張連歷史 12）＋ 新解出嘅 id 每張 1（current）/ 2（連歷史）。上限 = 76×2 = **152 call**，仍然係一個窗口嘅 15% |
| 時間窗風險 | 中。`collect` 要 Chrome 跑內部 search API，70 張要人手 / 半自動時間；如果 key 真係 07-29 死而 identity 未解完，就攞唔到嗰批嘅歷史（current POP 仍可 keyless 補） |
| 榜深度變化 | **需要同時擴 roster 先有效**。擴咗之後期望 42 → **~78**（實測部分 +22，未知部分加權 +13.6） |
| 備註 | 擴 roster 係另一個決定（要開新 `market_universe_lock`），唔喺呢單範圍內；roster 唔擴則榜深度變化 = 0 |

### 選項 D — 唔掂 POP，改為補嗰 132 張 roster 內卡嘅價

| | |
|---|---|
| quota 成本 | **0 GemRate call**（價唔經 GemRate，行 `snk_psa10` / `ebay` / `g10_kline`） |
| 時間窗風險 | 無 —— 同 GemRate key 完全無關 |
| 榜深度變化 | 上限 **+132**（42 → 174），因為呢 132 張 POP 已 >= 1000，只差價。實際取決於幾多張補到 2 日窗口內嘅價 |
| 備註 | 收益上限係選項 C 嘅 3.7 倍，成本結構完全唔同（價源覆蓋問題，唔係 POP 問題）。呢個唔喺你原本問題範圍，但量度結果指向佢，所以列出嚟俾你知有呢條路 |

---

## 附件

| 檔案 | 內容 |
|---|---|
| [produce_op_pop_gap.py](produce_op_pop_gap.py) | 生產本報告全部數字嘅唯讀腳本 |
| [measurements.json](measurements.json) | 所有 headline 數字 |
| [op_no_pop_cards.csv](op_no_pop_cards.csv) | 76 張缺 gemrate POP 嘅 OP 卡完整名單 |
| [op_roster_blockers.csv](op_roster_blockers.csv) | roster 內 227 張 OP 卡逐張 blocker |
| [board_depth_projection.csv](board_depth_projection.csv) | top-N 選項榜深度推算 |
| [merged_board_simulation.csv](merged_board_simulation.csv) | 22 張實測達標卡插入現榜之後嘅完整排名 |
