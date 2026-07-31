# 價格缺口 Review（2026-07-29）

## 1. 而家仲爭幾多

| | 數 |
|---|---:|
| 合資格池 | 940 |
| 有 PSA10 價（any source） | **916** |
| **仍無價** | **24** |

### 24 張無價拆局

| TCG | 張 | 代表 |
|---|---:|---|
| Pokémon | **21** | Meowth 106、Piplup 98、Mew GG10、多個 Charizard/TG/GG |
| One Piece | **3** | DON!! Gold、Sanji ST14-003、Eustass Kid OP05-074 |

名單：`temp/no_price_remaining.json`

---

## 2. Limitless / onepiece.gg 價錢 — **可唔可以用？**

### Limitless OP（https://onepiece.limitlesstcg.com/cards）

| 實測 | 結論 |
|---|---|
| 卡頁有 `$` 價 | ✅ 有（例 OP01-016 多印刷 $3.66 / $412…） |
| 價來源 | **TCGPlayer partner link + Cardmarket EUR** |
| 關鍵字 | `raw` · `market` · `tcgplayer` — **冇 PSA 10 分級欄** |
| 圖 CDN | ✅ `limitlesstcg.nyc3.cdn.digitaloceanspaces.com/one-piece/{SET}/{SET}-{NUM}_EN.webp` |

### onepiece.gg

| 實測 | 結論 |
|---|---|
| 列表頁 | 200；價錢字樣稀少 |
| 公開 API host | `api.onepiece.gg` 解唔到 |
| 角色 | 圖／collection 較有用；價未證明係 PSA10 |

### 硬結論（市值口徑）

```text
CARDZ marketCap = PSA10 參考價 × GemRate PSA10 POP
```

| 源 | 當 PSA10 榜價？ | 原因 |
|---|---|---|
| Limitless OP `$` | ❌ **唔准** | raw TCG 市價；乘 POP 會炸市值 |
| onepiece.gg 列表價 | ❌ 未證 PSA10 | 同上風險 |
| TPL / SNK / TCGFish PSA10 | ✅ | 分級 eBay／JP PSA10 |

**可以用嘅部分**：Limitless **圖 CDN** + **collector 永久編號 mark**（`op_limitless`）  
**唔可以用嘅部分**：直接當 PSA10 價入 `market_price_observation` 權威。

若日後要「OP raw 市價」做**副指標／對照欄**（唔入市值），可另開 `metric`／source，**唔好**寫入而家 `pricePsa10` 路徑。

---

## 3. 24 張無價 — 點解仲係空

| 原因 | 說明 |
|---|---|
| TPL 無 PSA10 成交點 | 已 map 都可能 graded.psa 無 10 |
| TPL 對唔穩 | 舊印刷／promo／GG TG 易撞錯 |
| SNK 未 bind | 24 張多數無 snkItemId |
| TCGFish | 要 path；search DOM 變過，批量要加固 |
| Limitless/OP.gg | **有 raw 價但政策唔入 PSA10** |

呢 24 張多數係 **源頭真係缺 PSA10 觀測**，唔係「腳本未跑」。

---

## 4. 可做／已做方法

| 方法 | 狀態 |
|---|---|
| TPL map 放寬 + harvest | 已做；916 有價 |
| TCGFish PSA10 fill | 已做一輪；可再加固 search |
| Limitless 圖 CDN | **`pipelines/op_limitless_images.py`** 可跑 |
| Limitless/OP.gg 價入市值 | **拒絕**（除非產品改口徑） |
| SNK 擴 bind 只打 24 | 可試；庫存未必有 |
| 人手／Drive 只補圖 | 寶藏庫 §2.1 |

---

## 5. 逆向腳本 — 範圍

| 目標 | 做 | 唔做 |
|---|---|---|
| Limitless 圖 | CDN pattern 已掌握 → `op_limitless_images.py` | — |
| Limitless 價 | 可 parse HTML `card-price usd` **只標 raw** | 寫入 PSA10 市值 |
| OP.gg | 再 Network；價未優先 | 當 PSA10 |

永久 mark：

- `catalog_source_identity` `op_limitless` = collector（`OP01-016`）
- 圖 source_path = CDN URL

---

## 6. 「做好成個 DB」誠實水位

| 層 | 完成度 |
|---|---|
| 身份 940 | ~100% |
| 價／市值 | **~97.4%**（916/940）；24 可能長期無 PSA10 |
| 圖 A/B/C | 大部分有；OP 用 Limitless CDN 再清一輪 |
| Snapshot 出街 | 未當「完」 |

**爭少少係真**：再榨 PSA10 邊際效益低；**產品上可接受 24 張唔入排名前端**，或標 unavailable。

---

## 7. 建議下一刀（自主）

1. 跑 `op_limitless_images.py --write`（OP 圖 + id mark）  
2. 24 無價：再試 SNK bind + 加固 TCGFish；**唔**灌 Limitless raw  
3. 更新 identity registry  
4. 剩餘永遠無 PSA10 → 文件列死 + FE 唔上榜  

證據：`temp/op_price_recon.json` · `temp/recon_limitless_deep.py` 輸出  
