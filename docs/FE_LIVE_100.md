# FE Live 100% 驗收

**日期**：2026-07-28/29（素材）· **2026-07-29 晚**（升跌／SAMPLE／填庫複核）  
**Snapshot**：`data/public/publish-staging/generations/canonical_live_fe/snapshot.json`  
**Pointer**：`data/public/publish-staging/latest.json` · `feSetComplete: true`  
**本機 dev**：`MARKET_DATA_SNAPSHOT_PATH` → `data/runtime/local-serve/snapshot.json` · 例 port **3810**

## 結果（素材七欄 · 複核 ALL）

| 集合 | n | 價 | POP | 市值 | 圖 | 史 | 成交 | 故事 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| top100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| watchlist | 129 | 129 | 129 | 129 | 129 | 129 | 129 | 129 |
| **FE_SET** | **229** | **100%** | **100%** | **100%** | **100%** | **100%** | **100%** | **100%** |

## 升跌窗（heatmap = 表 · 同一 `windows.changePct`）

| 窗 | top100 `changePct.value` null | 備註 |
|---|---:|---|
| 1d | **0** | 頭尾：而家 ÷ ~1 日前（±容差） |
| 7d | **0** | 同上 |
| 30d | **0** | 同上；**唔 invent** 假 bar |

**計法（2026-07-29 拍板）**：簡單頭尾；有斷層都係搵 ~N 日前最貼點；±幾日 fallback OK。  
**已知 bug 已修**：TPL `ebayAvg1d` 唔好 stamp 成 today（會令大批 30d=0%）。詳 [FILL_LOOP_LESSONS.md](FILL_LOOP_LESSONS.md)。

**Sparse（±5d 內冇價點，但仍有 head-tail %）**：ST10-006、Greninja Star（源窗真乾）。

## SAMPLE 圖（OP）

| 項 | 狀態 |
|---|---|
| 偵測 | `temp/scan_sample_v2.py`（OCR + template；**唔好**淨用黃色像素） |
| FE 清場 | **20 張**換 clean · re-scan **0 hit**（2026-07-29） |
| 源 | Limitless **`_EN.webp` 優先**（19）· P-110 → G10 SNK CDN（1） |
| 寫法 | 新 content-hash webp；**唔覆寫**舊 sha；snapshot-first |
| 報告 | [temp/sample_image_fix_report.md](../temp/sample_image_fix_report.md) |

**語言**：SAMPLE 多來自 TCGplayer EN 水印圖；換走後多數係 **Limitless EN clean**，**唔係**整批日版。OP 美／日 **共用 number、唔共用 SKU**；comic/SEC-SP 同 collector 可能共用 base 面——已知風險。見 [CARD_SOURCING_HANDBOOK.md](CARD_SOURCING_HANDBOOK.md)。

## DB 同前端係咪「同一件事」？

**係同一條組裝鏈，唔係兩個無關產品。**

```text
MySQL DB（原材料）
  → pipelines 採集／QC／identity
  → market_index_snapshot（排名）
  → canonical_public_snapshot.py（組裝）
  → publish-staging snapshot + pointer（出街包）
  → apps/web 只讀 snapshot（前端）
```

| 層 | 角色 |
|---|---|
| DB | 全池原料（可 >940；半殘 OK） |
| Snapshot FE_SET | 上板 229 張 · **要 100%** |
| 前端 | 展示 snapshot · 唔直連 DB |

所以：報告講「DB 夠／成交夠」= 原料；「FE 100%」= **已 bake 入 snapshot 嘅上板集合**。

## 重建

見 [DEPLOY_FOR_HANDOVER.md](DEPLOY_FOR_HANDOVER.md) §1.5 · [FILL_LOOP_LESSONS.md](FILL_LOOP_LESSONS.md) · `scripts/db_fill_until_green.py`。
