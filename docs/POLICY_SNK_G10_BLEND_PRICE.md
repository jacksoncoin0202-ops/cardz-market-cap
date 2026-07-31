# Policy: SNK + G10 display price（單一 or 五五平均）

**asOf:** 2026-07-30  
**Authority:** DADDY（初版平均）  
**Status:** **SUPERSEDED / REVOKED 2026-07-30**

> **已廢。** 唔再做 SNK+G10 五五平均。  
> 現行：[`POLICY_SOURCE_BINDING_DOCTRINE.md`](./POLICY_SOURCE_BINDING_DOCTRINE.md)  
> — 真 eBay（非 index）優先，否則 SNK；**禁止 kline**；**禁止 blend**。

---

## 歷史 Rule（已廢，只作考古）

| 情況 | 顯示價（舊） |
|------|--------|
| 只有 **SNK 系** | 用 SNK（USD） |
| 只有 **G10 系**（含當時以為可信嘅 kline） | 用 G10（USD） |
| **兩邊都有** | **(SNK_USD + G10_USD) / 2** ← **已廢** |
| 兩邊都無 | 無價 |

### Units

- 兩邊入 `market_price_observation.price_usd` 後 **已係 USD**  
- SNK 原本 JPY → 寫庫時已 FX；**禁止** JPY 同 USD 未換算就平均  
- 平均在 **USD 小數** 上做；唔改 native 列

### Identity

- 每條 leg 先要 `catalog_source_identity` exact 對應  
- 無 ebay identity 嘅 `ebay` 價 **唔入 G10 leg**（唔好拖垮有 SNK 嘅卡）

### Code

- `canonical_db_qc.select_display_exact_price`  
- `market_alerts.pick_rank_price`（榜 / index 同口徑）  
- evidence.price.source = `snk_g10_blend` 當雙源平均

## 同其他政策

- G10 可信：`POLICY_G10_EXACT_PRICE.md`  
- 前端圖：**plan_a 等舊 FE 包圖可能錯**；正確圖要以 QC 已確認 / SNK promote 路徑為準，唔好用錯 generation 嘅 sample 圖
