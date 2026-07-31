# 價錢全面 Review — 2026-07-29

> **asOf（UTC）：** `2026-07-29T11:43:57Z`  
> **工具：** [`scripts/price_full_review.py`](../scripts/price_full_review.py)（只讀）  
> **證據目錄：** [`docs/evidence/2026-07-29-price-review/`](evidence/2026-07-29-price-review/)  
> **Snapshot：** [`data/public/publish-staging/generations/plan_a_qc3_20260729_154227/snapshot.json`](../data/public/publish-staging/generations/plan_a_qc3_20260729_154227/snapshot.json)  
> **重跑：**  
> `python -X utf8 scripts/price_full_review.py --today 2026-07-29`

---

## Executive（答你三條）

| # | 問題 | 判定 | 一句 |
|---|---|---|---|
| **Q1** | 價錢全量跑晒未？有無錯？ | **PASS** | 940 池而家 **941/941 有正 PSA10 價**；無 Limitless/raw 污染；假 today 只有 **14** 張同價今日（非 bulk 爆炸） |
| **Q2** | 仲組成到 1d/7d/30d 升幅嗎？ | **PARTIAL** | **FE top100 三窗 ~97% ready**；假 0% 嫌疑 8 張；**全池深度仍薄**（近 30 日 ≥1 bar 只有 446/941） |
| **Q3** | 有無突然爆大／爆小？ | **PASS（Top100）/ 留意 watchlist** | Top100 **\|30d\|>200% = 0**；watchlist 有 Sabo **+1007% 30d** 等恐怖值要查 identity／錨點 |

**唔好講「全庫完美」。** 有價面已滿；升跌質素 FE 可用、池面 depth 同市值 delta 仍有缺口。

---

## 0. 九百幾 vs 千幾（分層，唔係同一個池）

| 層 | 今次活量 | 意思 |
|---|---:|---|
| catalog_variant | **1749** | 全庫 printing 行（散） |
| universe lock 成員 | **932** | 現役 universe 鎖成員 |
| watchlist（運維池） | **941** | GemRate PSA10 POP≥1000 池（你口中「入庫九百幾」主戰場） |
| 有正價（strict） | **941** | 池內 `price_usd > 0` |
| 最新 index constituent | combined **982** / pokemon **838** / OP **144** | 可排入榜 |
| FE_SET（出街） | **243**（top100=100 + watchlist=143） | 前端實際食嘅集合 |
| GemRate roster（文件） | ~1468 | 掃 POP 全集，**唔等於** 有價 KPI |

```text
1468 roster ─→ ~932 universe ─→ 941 watchlist（全有價）
                                    └─ rankable → index ~982 combined
                                                 └─ FE 243 出街
```

**結論：** 入庫／運維池係 **~941**；宇宙／roster 係 **~932–1468**——**分層**，唔係「入咗九百但同一池實際有千幾」。價錢 review 主戰場 = **941 池 + FE 243**。

---

## 1. Q1 — 覆蓋 / 全量 / 有無錯

### 1.1 池覆蓋（活量）

| KPI | 數 |
|---|---:|
| watch | **941** |
| any_price / strict_price | **941 / 941** |
| **no_price** | **0** |
| tcgpricelookup | 806 |
| snk_psa10 | 224 |
| snk_family（含 g10_kline） | 280 |
| ebay | 117 |
| g10_kline | 113 |
| tcgfish | 68（全部單日 2026-07-29） |
| snk_id bind | 560 |

### 1.2 全表 `market_price_observation` 源枚舉

| source | 行數 | variants | 日期窗 |
|---|---:|---:|---|
| snk_psa10 | 128,761 | 534 | 2023-06-19 → 2026-07-29 |
| tcgpricelookup | 41,474 | 805 | 2025-07-28 → 2026-07-29 |
| ebay | 6,177 | 527 | 2026-04-25 → 2026-07-27 |
| g10_kline | 3,945 | 588 | 2023-07-20 → 2026-07-25 |
| snkrdunk | 582 | 342 | … |
| tcgfish | 68 | 68 | **只有 2026-07-29**（綠 any_price、唔當 30d 史） |

- **禁源（Limitless / tcgplayer / raw / cardmarket）命中：0**
- 今日（2026-07-29）新鮮：SNK 219 + TPL 13 + tcgfish 68（TPL 全日 bulk 唔係今日一嘢灌完）

### 1.3 假 today（同價 stamp 掃描）

| | 數 |
|---|---:|
| 今日價 == 前一有效日同源價 | **14** |
| 其中 TPL | 11 |
| 其中 SNK | 3 |

→ **唔係** 舊日「大批 30d=0%」級別 bulk stamp。14 張可接受為真市價不動或零星，建議仍人工抽 3–5 張 TPL 核對。清單：[`B_fake_today.jsonl`](evidence/2026-07-29-price-review/B_fake_today.jsonl)

### 1.4 Index 新鮮度

最新 run **244** / effective **2026-07-29**：

| index | constituents | total market cap USD |
|---|---:|---:|
| tcg-combined | 982 | ~$2.31B |
| pokemon | 838 | ~$2.10B |
| one-piece | 144 | ~$0.21B |

---

## 2. Q2 — 1d / 7d / 30d 仲組唔組到

### 2.1 FE snapshot（出街真相）

| 集合 | n | 價 ready | 1d ready(+stale) | 7d | 30d | 假 0% 嫌疑 |
|---|---:|---:|---:|---:|---:|---:|
| top100 | 100 | **100** | 97 | 97 | 97 | **8**（多數 1d） |
| watchlist | 143 | **143** | 140 | 140 | 140 | 4 |

- 三窗 **accumulating** 各約 3 張（OP：Luffy 1st Anniv / Nami / O-Nami 等）— 史真短，唔 invent。
- **假 0%：** published `changePct≈0` 但 historyDaily 頭尾可算出非 0（例 rank10 Charizard 1d history ~757 vs ~766）。根因多為 **rank 用 `pick_rank_price` vs historyDaily `source_priority` 兩套路徑**，或 index 寫 0 而 history 唔同意。  
  → Q2 降為 **PARTIAL**，唔當「升跌完美」。

### 2.2 池原料深度（941 池，DB 觀測）

| 指標 | 數 | 解讀 |
|---|---:|---|
| 有任何正價 bar | 933 | ≈全池 |
| 理論可組 1d / 7d / 30d | **531 / 603 / 583** | 一半左右有窗 |
| 近 30 日 ≥1 bar | **446** | 其餘主要靠更舊錨點或單點 |
| 近 30 日 ≥10 bars | 174 | 深史少數 |
| sparse 無 30d 錨 | 350 | 池面升跌唔完整 |

**any_price 滿 ≠ 30d 升跌滿**——E6 結論仍然成立。

### 2.3 DB candidate daily（最新 evaluation）

| | non-null / rows |
|---|---|
| change_1d_pct | 362 / 997 |
| change_7d_pct | 662 / 997 |
| change_30d_pct | 834 / 997 |

Snapshot 對 FE 會用 history 回補，所以 FE ready 率 **高過** candidate 1d 欄。

### 2.4 市值升跌 vs 價升跌（`audit_snapshot_deltas`）

| 窗 | 市值 delta 有值 | 箭嘴指錯（價%  alone） | 成交 delta 有值 |
|---|---:|---:|---:|
| 1d | 67/100 | 0 | 0/100 |
| 7d | 90/100 | 2 | 86/100 |
| 30d | **45/100** | 3 | 26/100 |

- Producer 已出 `marketCapChangePct`；**30d 市值窗只有 ~45%**——多因 POP change 缺。
- 若 UI 仍用純價 % 扮市值，7d/30d 仍有少數箭嘴相反（Palkia / Victini / Mega Charizard X 等）。

---

## 3. Q3 — 恐怖數值

### 3.1 規則結果

| 檢查 | 結果 |
|---|---|
| Top100 \|30d changePct\| > 200% | **0** |
| Snapshot 旗標總數 | 11 |
| DB 同卡多源 spread >5× | **84**（池內） |
| risky 最新 / trusted median >5× | **1**（g10_kline $4920 vs trusted ~$904） |

### 3.2 最需要睇嘅卡（摘自 [`D_outliers.csv`](evidence/2026-07-29-price-review/D_outliers.csv)）

| 位置 | 卡 | 現象 | 判斷 |
|---|---|---|---|
| WL #195 | **Sabo** | 30d **+1007%**，1d/7d +177% | **必查** identity / 錨點價是否過低或跨源跳 |
| WL #158 | Monkey D. Luffy | 7d **+252%** | 必查 |
| Top #71 | Mega Venusaur ex | 1d **+175%** | 查 TPL/SNK 錨；對照歷史 Venusaur 離群事故 |
| WL #142 | Boa Hancock | 1d/7d/30d 全 **+105%**（三窗同數） | 似 anchor 同一舊點 → 深度/錨問題 |
| Top #82–#29 一批 | Blastoise / Sylveon / Umbreon / Charizard… | 1d **−50%～−60%** 成群 | 可能真實回吐或**同源切換**；要抽 2–3 張對 history |

**Top100 冇再見到萬級 % / >200% 30d**——CARD_INDEPENDENT_QC 離群 purge 後水位合理。  
**Watchlist 仍有爆炸 %**——唔應當 FE 主榜可靠度同等。

### 3.3 端到端抽樣（E）

- **marketCap = price × POP：** 抽樣 top 卡 **全部 true**
- history 頭尾 vs published：1d/30d 多數 match；**7d 偶有偏差**（例 #1 梵高 −24.0 published vs −17.7 history-only）  
  → 再確認：**index `pick_rank_price` 窗** 同 **historyDaily 日桶優先序** 可以唔同錨。

---

## 4. Fix backlog（只列，本次 0 write）

| Sev | 項 | 建議 |
|---|---|---|
| P0 | Sabo / Luffy / Boa 等 watchlist 爆炸 % | 查 identity + 錨點；必要時 quarantine 升跌或解綁錯源 |
| P0 | Top100 假 0% 嫌疑 8 張 | 對齊 history 頭尾 vs index change；考慮 snapshot 一律以 history 為準當 index≈0 |
| P1 | 池 30d depth（446/941 近窗有 bar） | 繼續 TPL incremental + g10 bridge + SNK bind；**唔 invent today** |
| P1 | 市值 30d change 只 45/100 | 修 `population_change_*` 寫入（而家多 NULL） |
| P2 | 84 張跨源 >5× | 擴 `pick_rank_price` 已 reject 之外，post-ingest 報表 / 可選 purge risky |
| P2 | tcgfish 68 單日 | 標「only stub」；唔當 depth |
| P3 | 升格 gate | `verify_daily_run` 加：FE change null 率、假 today bulk、Top100 \|30d\|>200% |

---

## 5. 建議閘（長期）

| Check | 建議 |
|---|---|
| FE_SET 1d/7d/30d ready 率 | warning <95%；fail <80% |
| 假 today 同價 ≥50（單源） | fail |
| Top100 \|30d\|>200% | fail if >0（可配白名單） |
| 池 bars30d_ge1 | warning 趨勢 |

---

## 6. 檔案地圖

| 檔 | 內容 |
|---|---|
| [`scripts/price_full_review.py`](../scripts/price_full_review.py) | 一鍵 A–E |
| [`docs/evidence/2026-07-29-price-review/report.json`](evidence/2026-07-29-price-review/report.json) | 機讀全量 |
| `A_baseline.json` · `B_*.json(l)` · `C_window_coverage.json` · `D_outliers.csv` · `E_sample_audit.json` | 分階段證據 |

---

## 7. 誠實水位（一句）

| 層 | 狀態 |
|---|---|
| 池有價 | **滿（941/941）** |
| FE 價 | **滿** |
| FE 價升跌三窗 | **大致可用（~97%），假 0 要清** |
| 池升跌深度 | **一半左右；未全量深史** |
| Top100 恐怖 % | **受控（0 張 >200% 30d）** |
| Watchlist 恐怖 % | **有（Sabo 等）** |
| 市值 30d delta | **弱（POP 因子缺）** |

**交付句：** 價錢全量覆蓋已打通；升跌 FE 主榜大致組到，但唔係零問題；最大風險喺 **watchlist 爆炸 %** 同 **池 depth／假 0／市值窗**，唔喺「九百 vs 千幾」嗰個數。

---

## 8. 清場（2026-07-29 · 先清後補）

> 用戶指令：**有問題先清走；搵返係後話。** 本次只 DELETE，唔 harvest / 唔 invent。

### 做咗咩

| 步 | 動作 | 量 |
|---|---|---:|
| 1 | 旗標卡（review 爆炸% + 假0 嫌疑）**全價 wipe** | 16 variants · ~3477 行 |
| 2 | risky vs trusted 5× · 假 today · 同日 spread · 序列離群 | 多 pass 累計 |
| 3 | watchlist 仍 multi-source >5× → **整卡 wipe** | 41 variants · 9518 行 |
| 4 | 仍 series outlier → 整卡 wipe | 1 variant · 126 行 |
| 5 | `market_alerts` 重算 | evaluation **21** · eligible **919** |

腳本：[`scripts/purge_bad_prices.py`](../scripts/purge_bad_prices.py)  
證據：[`docs/evidence/2026-07-29-price-review/PURGE_*.json`](evidence/2026-07-29-price-review/) · [`PURGE_WIPE_SUMMARY.json`](evidence/2026-07-29-price-review/PURGE_WIPE_SUMMARY.json)

### 清完 DB 水位（asOf ~11:51Z）

| KPI | 清前 | 清後 |
|---|---:|---:|
| watchlist 有正價 | 941 | **881** |
| no_price | 0 | **60**（刻意：有問題卡已收） |
| fake_today 同價 | 14 | **0** |
| DB latest multi-source >5× | 84 | **0** |
| DB risky vs trusted >5× | 1 | **0** |

### 未做（後話）

- **唔** 補 60 張無價  
- **唔** 重 bake FE snapshot（舊 `plan_a_qc3` 檔仍係清場前畫面；DB 已乾淨，出街要另 bake）  
- 搵返正確 PSA10 價 = 之後獨立 pass
