# Policy: 「獨家 / Exclusive」— 排名忽視、QC 唔踢走

**asOf:** 2026-07-30  
**Authority:** DADDY — 「見到獨家自動忽視；排除出排名算；但唔好 QC 壓到唔出嚟」  
**Status:** **ACTIVE**

---

## 1. 決策

| # | 規則 |
|---|------|
| 1 | **排名 / Top100 / index 市值座位**：**自動忽視 exclusive 產品**（唔好佔 #1 毒價 / 唔影響正常榜） |
| 2 | **QC**：**唔好**因為「獨家」本身把卡踢到完全唔出嚟；可以 `warning` / 降權，**唔硬 fail 整個 variant 消失** |
| 3 | 獨家價（尤其 G10/ebay **derived** 毒價）**唔入 exact 排名價** |
| 4 | 正常卡（如梵高比卡超）有 exact SNK → **必須可上榜**，唔受獨家毒價連坐 |

## 2. 點樣認定「獨家」

名稱 / set（case-insensitive）命中任一：

- `exclusive`（含 `PSA Exclusive`）
- `獨家` / `専売` / `限定店`
- set 含 `Exclusive Collaboration` / `Exclusive Promo`

（可擴充；改 `pipelines/exclusive_product.py`）

## 3. 實作落點

| 層 | 行為 |
|----|------|
| `market_alerts.tracked_indexes` | exclusive 排喺所有非 exclusive ready 之後（Top100 優先非 exclusive） |
| `market_alerts.pick_rank_price` | 已要求 ebay/g10 identity **exact**（reject derived） |
| `canonical_db_qc` | exclusive 唔另加 hard blocker；最多 `warnings += exclusive_product_rank_ignored` |
| FE | Top100 實際由 index 決定；獨家自然唔霸榜 |

## 4. 梵高案例（記錄）

- 梵高 vid=1 **有** SNK exact + 成交；**唔係 QC 剔除**  
- 曾被 Venusaur/Blastoise **derived ebay $42k** 毒價壓落 #3  
- 修：exact-only identity + exclusive 排名降權  
- 最新 index：**#1 Pikachu with Grey Felt Hat**

## 5. 唔做

- 唔刪 exclusive 卡 DB 行  
- 唔 invent 價  
- 唔用 exclusive 毒價當 exact  
