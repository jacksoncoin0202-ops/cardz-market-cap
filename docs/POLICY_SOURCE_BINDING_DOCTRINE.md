# POLICY: 來源綁定教義 + 點解噉配對

> **Authority：** daddy 與 agent 多日奮戰總結（鎖定 2026-07-30）  
> **地位：** 維護 CARDZ Market 嘅**政策真理**。綁定可能改、政策可能改——但**點解而家噉綁**必須有文可查。  
> **相關：** `POLICY_DB_PROVENANCE.md` · `DB_PROVENANCE_MAP.md` · `POLICY_G10_EXACT_PRICE.md`（部分被本文件覆寫）

---

## 0. 點解要有呢份文

1. **以後唔好再「帶一次」**——今次定義係最終維護起點；日常靠綁定 + 增量。  
2. **綁定會解、會重綁**——後人唔知點解舊 pair 存在，會亂改。  
3. **政策同綁定唔係同一樣嘢**：
   - **政策** = 邊啲源可信、優先序、禁乜  
   - **綁定** = 呢張卡喺 DB 實際連去邊個 external id / 邊個 asset / FE 讀邊欄  

冇政策文 → 只見綁定表會覺得「亂配」。  
有政策文 → 綁定係**可解釋嘅執行結果**。

---

## 1. 宇宙（邊啲卡值得綁）

| POP (GemRate PSA10) | 角色 |
|---------------------|------|
| **≥ 1000** | 正式宇宙：ranking / FE 主線 / 必須跟 |
| **971–999** | Pre-entry radar，唔 formal 上榜 |
| **≤ 970** | Discovery only |

約 **~900** 張 qualified = POP≥1000 主線（同 PC full900 作業同義）。

---

## 2. G10：工具數據 vs Index（最易混淆）

### 一句規則

| 判斷 | 用？ |
|------|------|
| 數據**有「index」元素**（G10 自創 index / `g10_kline` / index chart 等） | **全部唔用** |
| 數據**冇 index 元素**（G10 入面真 SNK、真 eBay、真成交量…） | **全部可以用** |

### 展開

| 概念 | 例子 | 政策 |
|------|------|------|
| G10 入面 **SNK 價 / 成交** | `snkrdunk_*` cards、sales_cache、→ `snk_psa10` / `snkrdunk` sales | ✅ 用 |
| G10 入面 **eBay 價 / 成交** | `altxyz` UUID、→ `source_code=ebay` + exact identity | ✅ 用 |
| G10 入面 **成交量** | sales_cache 滾出嘅量 | ✅ 用 |
| **G10 Index / kline** | `g10_kline`、`g10_index_*`、analytics index 成份 | ❌ **永遠唔用** 做 mcap / FE 口價 |

**Index 歸 index，G10 真市歸 G10。**  
Market cap **唔係** G10 指數；我哋用 **真 eBay + 真 SNK**（可經 G10 工具落地）× **GemRate POP**。

### 點解要分

G10 工具係**搬運真市**（SNK/eBay）嘅管道。  
G10 Index/kline 係佢哋**內部發明嘅序列**，同 CARDZ 公開 market cap **唔符合**——用咗會出現 Venusaur $42k、Charizard kline $3960 等災難。

---

## 3. 價源優先（PSA10 only 上榜）

**Trusted（identity exact 後）：**

1. **eBay**（G10-path exact UUID，或 PC 成交路徑 `pc:` + pricecharting exact——sales）  
2. **SNK**（`snk_psa10` / `snk` / `snkrdunk`，exact snkrdunk）  
3. **PC** 補 eBay 腿 / 流動 / 圖表  

**禁止：**

- `g10_kline` / 任何 index 價  
- SNK+G10 **blend 平均**（舊政策已廢）  
- 無 exact identity 嘅 **orphan ebay** 價行  
- trusted 空就 fallback 任意源  

**展示：** 主打 **PSA10 價**；BGS/CGC 等可 keep 增量，唔使做 mcap 主軸。

---

## 4. 圖源 + 人揀（點解噉配）

| 源 | 拉落嚟？ | Auto public？ | 點解 |
|----|----------|---------------|------|
| **G10 raw**（snkrdunk bg-removed 等，非 slab） | ✅ | ✅ **唯一 auto** | 物認可信；仍要拒 PSA slab（altxyz 相） |
| **TCGPlayer PTCG** | ✅ 照拉 | ❌ 人揀 | 有用但有問題 pattern |
| **TCGPlayer OP** | ✅ 照拉 | ❌ 人揀 | 更唔穩 |
| **PC** | ✅ 一次 ~900 | ❌ 人揀 | 成交強、圖要人判 |
| **SNK 直採圖** | ✅ | ❌ 人揀 | 價可信，圖唔 100% |

**成站只 raw 裸卡**；標準畫布全圖（裁滿，唔 letterbox）。

Picker 畀人睇：**多源價 + 來源 label + 外鏈 + 成交總數 + 一句可靠度**；要可**重揀**。

---

## 5. 綁定鐵律（DB ↔ 源 ↔ FE）

### 5.1 Identity 綁定

```
catalog_variant.id
    ↔ catalog_source_identity (source_code, external_entity_id, match_status='exact')
```

- **exact only** 擁有 ingest 權  
- `derived` / 無 identity → **唔准**寫入該源嘅價/成交做 ranking  
- SNK 腳本早已寫明 exact-only；違反 = **E19 orphan ebay**

### 5.2 觀測綁定

```
market_price_observation / market_sale_observation
    → variant_id + source_code + (理想) 可回溯 external / run
```

- 每條都要 **source label** 可解釋  
- FE 口價 asOf 跟**同一套優先序**嘅源，唔好 SNK 時間戳貼住 eBay 價

### 5.3 圖綁定

```
market_image_asset (content_sha256, private_object_key)
    ↔ market_image_qc (public_allowed, semantic, raw_front)
    ↔ FE image.sha256 / src
```

- G10 raw 先天 approve → QC public + 綁 opaque  
- 其他人揀結果 → 寫 QC + 綁  
- 冇綁 = 唔出 FE 圖

### 5.4 FE 客見欄 ↔ 路徑（綁定表）

| # | 客見 | Snapshot / 邏輯來源 |
|---|------|---------------------|
| 1 | 圖 | `image.sha256` ← QC public asset |
| 2 | 編號 | `collectorNumber` ← catalog |
| 3 | PSA10 價 | `pricePsa10` ← index ref **只准** ebay/SNK 優先序 |
| 4 | POP | `populationPsa10` ← **GemRate** |
| 5 | Market cap | `price × POP` |
| 6 | 30d sales | `windows.30d.trackedSales` ← SNK + eBay/PC sales |
| 7 | 30d 升跌 | 真市日線錨（SNK/eBay/PC；**唔用 kline**） |
| 8 | vol 1d/7d/30d | 同上；**1d 空 = 正常**（唔日日有成交） |

小故事 / 深 i18n = 後補，唔阻塞上線。

---

## 6. GemRate / 其他廠

- **全量一次 + 每日增量 keep**  
- **增量 = 全面**（**唔止 ~900 張 FE 宇宙**）— 全 GemRate id / 全 catalog 清單都要跟  
- 主打 **PSA10 POP** 入 mcap  
- 其他廠家數據**照全面增量**；有產品應用先展示——唔係「永遠唔拉」、亦唔係「只同步 Top900」

---

## 7. 點解會「噉樣配對」— 決策樹（畀後人）

```
卡有 POP≥1000？
  否 → 唔 formal 上榜
  是 → 有 exact SNK 或 exact eBay 價？
         否 → 唔入榜（fail-closed；唔用 kline 頂）
         是 → 優先 eBay(exact) 否則 SNK → × GemRate POP → rank
              圖：有 G10 raw 非 slab？→ auto 綁
                   否則 → 候選（PC/SNK/TCGPlayer…）等人揀後綁
```

**常見誤解：**

| 誤解 | 正解 |
|------|------|
| 「G10 全部都用」 | 只用 **非 index** 真市 |
| 「有 ebay 價就信」 | 要 **exact ebay identity** |
| 「kline 同 SNK 差好遠所以綁錯」 | kline 本身唔該用；先睇 SNK/exact ebay |
| 「1d vol 空 = 壞」 | 正常稀疏；PC/eBay 補有嘅日 |
| 「綁完就唔使政策文」 | 解綁/重綁時要靠政策解釋 |

---

## 8. 已廢 / 降級舊政策

| 舊文 | 狀態 |
|------|------|
| `POLICY_SNK_G10_BLEND_PRICE.md` | **廢** — 唔再平均 |
| `POLICY_G10_EXACT_PRICE.md` 若仍寫 kline 可信 | **以本文為準**：kline ❌；G10 eBay/SNK ✅ |
| temp 奮戰筆記 | 遷移摘要見 `temp/PM_DEFINITIONS_AND_ERRORS.md` |

---

## 9. 維護承諾

- **政策改：** 改本文 + 標日期 + 一句 why；再改碼/綁定  
- **綁定改：** receipt / identity match_status；唔靜默寫 derived  
- **增量：** GemRate + 價源日更；miss 日補跑  
- **今次 QC 過 = 最終維護版起點**——唔推倒重來  

---

## 10. 一頁索引（agent 必讀）

1. 宇宙 POP≥1000  
2. **有 index 元素 → 唔用；冇 → 用**  
3. 價 = exact eBay 然後 SNK；禁 kline / orphan ebay / blend  
4. 圖 = 只 G10 raw auto；其餘人揀  
5. **一切可解釋綁定**（identity + observation + asset + FE 欄）  
6. GemRate 全量+增量；PSA10 主打 mcap  
7. 梵高 #1 煙霧閘；未 visual approve 唔 deploy  

---

*Precious because hard-won. Do not flatten into “just use G10.”*
