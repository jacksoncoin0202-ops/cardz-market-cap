# Policy: DB 數據來源注腳（Provenance）— 增量同步前提

**asOf:** 2026-07-30  
**Authority:** DADDY — 成個 DB 要有注腳，標明邊個 script / 渠道寫入；已證明成功嘅路徑先好做增量同步  
**Status:** **ACTIVE**

---

## 1. 決策

| # | 規則 |
|---|------|
| 1 | **每類 production 事實**（價 / 成交 / pop / 圖 / identity / index）必須可追溯：**script → transport → 外部源 → DB 表 / source_code** |
| 2 | **增量同步只允許「已證明成功」路徑**（有 run receipt / 非空 payload / QC 曾綠或人工批核） |
| 3 | **禁止**「DB 有數但無人知邊個腳本寫」——發現即補注腳或 quarantine 新寫入 |
| 4 | **`source_code` 標籤 ≠ 渠道名**——必須寫清（例：`ebay` 價 = G10 altxyz 檔；`ebay` 成交可 = G10 **或** PC `pc:{id}`） |
| 5 | 新 pipeline 上線前：registry 一條 + 本 map 一條 + sample run_key |

## 2. 為何

- 前端 / TopN 維運 = 重複跑**已通**腳本，唔係日日發明新源  
- 出事時 30 秒定位：表 → source_code → script → 磁碟/API  
- 避免把 PC 當 eBay、G10 當 live eBay API 嘅命名陷阱再犯

## 3. 權威產物

| 產物 | 路徑 |
|------|------|
| 本政策 | `docs/POLICY_DB_PROVENANCE.md` |
| 機器可讀 map | `docs/DB_PROVENANCE_MAP.json` |
| 人類表 | `docs/DB_PROVENANCE_MAP.md` |
| 掃庫報告（可再跑） | `data/runtime/private-reports/fill/FRONTEND_GAP_FILL/db_provenance_scan.json` |
| 生成器 | `temp/_db_provenance_build.py`（可升格 `pipelines/`） |

## 4. 注腳最低欄位

每條 map entry：

```text
fact          # price | sale | pop | image | identity | index | fx
db_table
source_code   # as stored in DB (may be overloaded)
scripts[]     # pipelines/*.py that WRITE
channel       # physical: SNK HTTP | PC CDP | G10 files | GemRate | Limitless CDN | ...
identity_key  # how row binds to variant
incremental   # command skeleton that re-proved success
notes         # naming traps / fail-closed
```

## 5. 增量同步清單（只列已證路徑）

見 `DB_PROVENANCE_MAP.md` § Incremental runbook。  
**未列入 = 唔准當 production incremental 默認路徑。**

## 6. 相關政策

- **來源綁定教義（2026-07-30 終審，含 index 禁令 + 點解噉綁）：** `docs/POLICY_SOURCE_BINDING_DOCTRINE.md`  
- G10 exact 價（部分被教義覆寫）：`docs/POLICY_G10_EXACT_PRICE.md`  
- Top100 流動性剔除：`docs/POLICY_FE_TOP100_LIQUIDITY.md`  
- 最優組裝：`docs/OPTIMAL_SOURCE_ASSEMBLY.md`
