# Source × QC pattern（研究筆記 · 2026-07-29）

> **REFERENCE ONLY／已被指正：** 呢份係 2026-07-29 研究快照，唔係 active
> runtime policy。當中「Limitless `_EN` 無 SAMPLE」、TPL/G10 價源角色同舊覆蓋
> 數字已失效。執行一律以
> [`OPERATOR_HARD_GATES.md`](OPERATOR_HARD_GATES.md)、機器 policy 同最新
> canonical QC receipt 為準；不可由本頁恢復已否決來源或門檻。

> **流程鐵律：** 入庫前 QC → **先** 分辨邊個 source 可靠 → **再** 用已驗證路徑去揾更多卡。  
> 唔係「拉晒先 QC」當齊；亦唔係武斷「某源永遠 OK／永遠死」。  
> **SNK 有 feedback 可用，但一定要 QC 先**——你可能拉 20 張零過，又可能拉 1 張即過。

---

## 1. 循環（唯一得嘅做法）

```text
measure gap
    → harvest 候補（可亂撈、免 QC）
    → VERIFY / QC 閘（腳本）
    → 過：寫 DB + mark preferred source + script
    → 唔過：記 reject reason（pattern）
    → 用 pattern 調整下一輪 recall／源優先
    → 再 harvest（已知綠路徑優先）
```

| 做完 QC 之後可以 | 因為 |
|---|---|
| 再開大批 SNK／TPL harvest | 已知邊啲 set／number 格式易過 |
| 只跑 registry preferred 腳本 | 綠路徑已 mark |
| 避坑源（例如 OP 圖走 TCG SAMPLE） | cast／reject 已證 |

---

## 2. 池內覆蓋速查（即場 DB · 約 932 unique / 940 行）

### Identity

| source | variants | 註 |
|---|---:|---|
| gemrate | 932 | 池本體 |
| tcgpricelookup | **764** | EN／US 價主幹 |
| snkrdunk | **~169–171** | JP 流動／成交關鍵；verify 硬 |
| op_limitless | 115 | OP 乾淨圖 |
| ebay | 86 | 慢、match 難 |
| tcgfish | 67 | 多係單日 stub 價 |

### 價

| source | variants | 深度印象 |
|---|---:|---|
| tcgpricelookup | 799 | 主幹；30d score 主力 |
| snk_psa10 | 221 | 長 K 雙峰（有 id 先長） |
| ebay | 133 | |
| g10_kline | 133 | 補洞 |
| tcgfish | 68 | **僅 1 日**——有價 KPI 綠但唔當史 |

### 成交

| source | variants | 行數 |
|---|---:|---:|
| **snkrdunk** | **200** | 23k+ |
| ebay | 150 | 10k+ |
| snk_grade | 57 | |

→ **成交主戰場 = SNK trades**（有 verified id 之後 ingest 好使）。

### 圖

- 幾乎全部有 asset 行；QC semantic 多數仍 `meta_unreviewed`
- **SAMPLE 水印**：實務上**主要 OP**；**全部 TCG 仍要 QC**（身份／錯卡／缺圖）
- OP 綠圖路徑：Limitless `_EN` → G10 SNK；**禁** TCGplayer SAMPLE 出街

### Registry preferred（900 行）

| preferred | n |
|---|---:|
| tcgpricelookup | 614 |
| snkrdunk | 213 |
| ebay | 73 |

---

## 3. SNK pattern（一定要 QC 先）

### 實證（semi_auto 一輪）

| 指標 | 數 |
|---|---:|
| harvest 候補卡 | ~4145 |
| 有 recall 嘅 watch | ~155–156 |
| **verify 拒** | **~191** |
| **accept 寫入** | **~14–15** |
| clean 刪錯綁 | 上百級累計 |

**命中率極不穩定：** 大批 recall 可能只過十幾個——**所以唔可以「有 SNK feedback 就當 OK」**。

### Reject 主因（ledger + sampleRejected）

| reason | 含義 | 下一輪點做 |
|---|---|---|
| **verify_species** | 名／物種 token 唔啱（mew⊂mewtwo 等） | 收緊名；拒 first-hit |
| **verify_set** | 純數字 collector 缺 set hint | harvest 帶 set；comic／並行印要 exact id |
| **verify_collector** | 號唔中 | 多格式 collector（OP01-016 / OP01016…） |
| **verify_op_name** | OP 角色名唔 hit | OP 專用 name gate |
| **verify_col_too_short** | 號太短 | 唔好單靠 1–2 位數字 |

### 綠路徑（QC 過之後）

```text
semi_auto / full_volume verify 過
  → catalog_source_identity (snkrdunk)
  → snk_market_data (新 out 檔名)
  → ingest_snk_trades_sales
  → registry preferredLiquiditySource=snkrdunk
```

**G10 sales_cache / kline：** 要 identity 先；無 bind → quarantine。

---

## 4. TPL / 其他源 pattern

| 源 | 傾向綠 | 傾向紅／要注意 |
|---|---|---|
| **TPL SSR** | EN 卡、有 slug、有 PSA10 | ~100 unmapped（多 JP）；33+ 有 slug 無 PSA10；needsReview ~100；**唔降 threshold 亂 bind** |
| **TCGFish** | 救急單點 | 403／單日 stub；唔當 30d 史 |
| **eBay/PC** | 已有 id 後成交 | semi_auto ebay accept 常 **0**；慢、PX |
| **Limitless OP 圖** | `_EN.webp` 無 SAMPLE | comic SP 可能同 base 號——要對 collector；CDN miss 要 G10 |
| **TCGplayer 圖** | PTCG 常有 | **OP SAMPLE 高危**；user cast 以肉眼為準（OCR 有假陰） |
| **GemRate** | POP／入池 | 唔係價／成交源 |

---

## 5. 研究結論（俾 PM 用）

1. **QC 完再揾** = 唯一穩定循環；QC 產物係 **reject 原因 + 綠路徑 mark**。  
2. **SNK：** 能用、成交最強，但 **verify 命中率低且波動大**——每批都要過閘，唔好批量信任 feedback。  
3. **TPL：** 價覆蓋主幹；map 失敗／空 PSA10 要換 SNK 或記 dry，**唔 invent**。  
4. **圖：** 全 TCG QC；SAMPLE 重點 OP；cast 標紅 = 強制換源。  
5. **下輪 harvest 優先：** registry preferred + 上輪 verify 過嘅 set／格式；少做已證實 fail 嘅 first-hit 名搜。

---

## 6. 變更

| 日期 | 內容 |
|---|---|
| 2026-07-29 | 首版：用戶要求 QC→再揾 + 研究 SNK／TPL／圖 pattern |
