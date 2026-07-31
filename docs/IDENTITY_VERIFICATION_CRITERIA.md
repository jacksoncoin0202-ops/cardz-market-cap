# 身份驗證準則（Identity Verification Criteria）

> **Authority level:** hard operator criteria（2026-07-30）  
> **Scope:** every `catalog_variant` (opaque CARDZ id) before it can be trusted as release-ready.  
> **Related:** `DATA_CONTRACT.md`, `DATA_ROUTING.md`, `catalog_source_identity`, `catalog_identity_evidence`.

---

## 1. 一句準則

**每張卡一個獨立 CARDZ `variant_id`。**  
所有來源嘅綁定、連結、POP、成交、人腦／agent 驗證，**全部掛喺呢個 ID 後面**（identity evidence ledger），唔散落、唔靠「記在 agent 腦」。

**市場身份：任意兩個獨立來源確認係同一張卡 → 身份已準確。**  
唔使等四個源全部齊先當 identity 過。  
交叉對照途中本身就係判斷「係咪同一張卡」。

多源仍建議掛齊（證據越厚越好），但 **gate 係「≥2 源一致」**，唔係「4 源齊」。

---

## 2. 身份來源池（Identity sources）

可用嚟互相確認「同一印刷」嘅來源（任揀 ≥2 條 **exact / 可驗證** 且 對齊）：

| # | 來源 | 角色 | 掛喺 variant 嘅證據 |
|---|------|------|---------------------|
| 1 | **GemRate** | 身份 + **PSA10 POP 權威** | `exact` bind + gemrate_id + POP 點 |
| 2 | **SNK (SNKRDUNK)** | 印刷 catalog + **PSA10 參考價** 權威 | `exact` apparel id + title/productNumber |
| 3 | **PriceCharting** | eBay sold／**英文卡產品頁幾乎一定有** | product URL + product id + sold 樣本 |
| 4 | **PSA 官方** | POP／印刷終審（可選加強） | 官方 POP 頁；與 GemRate POP 對齊 |

### 2.1 身份通過條件（硬 · 2026-07-30 修訂）

對同一 `variant_id`：

**最少兩個來源**（上表任一組合，例如 GemRate+SNK、GemRate+PC、SNK+PC、PC+PSA…）  
在以下維度 **一致** → **`identity_confirmed`**：

- 同一 collector number（完整，唔猜）
- 同一 set／edition 語境
- 同一 parallel（manga / SEC-SP / Classic vs Base 1st 等）
- 若兩邊都有 POP：數量級一致（允許日更差；唔允許「另一張卡」）

**第三、第四源** = 加厚證據同 QC 盡責，**唔係** identity 最低門檻。

**兩個源衝突**（明確唔係同一印刷）→ fail-closed；唔合併、唔 invent。

### 2.2 PriceCharting（EN 卡）

- **英文卡：PC 應視為可找（几乎一定有 product page）。**  
- 未掛 PC 唔等於「市場無」——多數係 **未 fetch / CF session 未清**。  
- 腳本：`pipelines/pricecharting_cf_session.py`（launch/connect/fetch）、`pricecharting_http_fetch.py`、`c11_pc_sold_ingest.py`。  
- Agent **必須**用 CF session 路徑；`web_fetch` 直打會撞 CF，**唔算 PC 失敗**。

### 2.3 唔當 identity 源

| 來源 | 地位 |
|------|------|
| **tcgpricelookup (TPL)** | **非身份權威**；錯 slug 吹爆 rank。唔用嚟「兩個源一致」。 |
| G10 | bootstrap／對照 |
| 裸 eBay keyword | 只可在 identity 之後作成交 |

---

## 3. 掛載規則（一切跟 variant_id）

### 3.1 主鍵

- **Canonical id：** `catalog_variant.id` + `opaque_id`
- **所有證據：** `catalog_identity_evidence.variant_id` → 指向上面
- **來源綁定：** `catalog_source_identity` 繼續做 exact/conflict bind；ledger 做 **URL + claim + 人驗證 + 收據** 總帳

### 3.2 必須累積嘅證據類型

| evidence_kind | 內容 |
|---------------|------|
| `bind` | GemRate / SNK / eBay / PC 等 external id |
| `pop` | PSA10 POP 快照（來源 + 數值 + 時間） |
| `price` | 可信價源快照 |
| `sales` | 30d PSA10 成交窗摘要 |
| `external_url` | 人／agent 驗證過嘅連結（PSA、PC、SNK、GemRate） |
| `human_review` | daddy／數據員判斷（對／錯印刷／要 keep 邊個） |
| `agent_receipt` | fill receipt path + sha256 |

### 3.3 數據員／agent 規則

做完 PSA 對、SNK bind、PC 頁、GemRate POP、人腦確認 → **即刻 `attach` 落同一 `variant_id`**。  
唔准只寫喺 chat、只寫 private report 而 DB 無掛。

---

## 4. QC 盡責定義（identity gate）

Release-ready / board fullyReady **除**現有 print·image·sales·price·cap 外，身份層應可回答：

1. 呢個 `variant_id` 掛咗邊幾個源？
2. **是否至少兩個源一致**（同一印刷）？
3. 邊個源缺？邊個 conflict？
4. 最新 PSA10 POP 係幾多、邊個源、邊個時間？
5. 外部驗證 URL（尤其 **PC EN 頁**）有無？

**QC 到最尾：**  
- **≥2 源一致** + 其餘 gate 過 → 身份可信  
- 只有 1 源 → 未完整（要補第二源，EN 優先 PC）  
- 源衝突 → 錯印刷或混 identity → 唔過  
- 第 3/4 源 = 加厚，唔卡最低 identity

實作入口：

- 表：`catalog_identity_evidence`（migration `017`）
- CLI：`pipelines/identity_evidence_ledger.py`（`migrate` / `backfill` / `attach` / `dossier` / `export-review`）
- 全庫 POP QC：`pipelines/identity_pop_qc.py`（應用 **≥2 源** 計 trusted，唔係 4 源齊）

---

## 5. 人腦 review 交付準則

給人判卡時 **必須帶**：

| 欄 | 原因 |
|----|------|
| `variant_id` + opaque_id | 唯一卡 |
| catalog 名 / set / collector | 我哋自稱係邊張 |
| **GemRate PSA10 POP** | 知「真唔真」數量級 |
| SNK id + link（如有） | 印刷 title |
| **PriceCharting URL**（如有） | 市場 sold 證據 |
| PSA 官方對齊狀態 | 終審 |
| 現價源 + 30d 成交摘要 | 發現價／印刷錯配 |

**唔准**只丟名同 collector 叫人估。

---

## 6. PriceCharting 地位（硬）

- PriceCharting 係 **有力市場證據源**（eBay sold / 產品頁）。
- 已有 CF session：`pipelines/pricecharting_cf_session.py` 等。
- **必須用**嚟交叉驗證身份同成交；唔准因為「SNK 搵唔到」就跳過 PC。
- PC product URL 經 `attach` 掛上 `variant_id` 後先算入 dossier。

---

## 7. Dual GemRate（澄清）

同一 `variant_id` 出現兩個 GemRate hash：

- **唔係**「兩個網站」或「兩堆數據要加總」
- 多數係不同 parallel／listing 被綁上同一 catalog 行
- 只允許 **一條 `exact`** 計 POP；另一條 `conflict` 保留作審計
- 若人確認其實同一印刷同一堆 → 業務可忽略 conflict 噪音，但仍只報一條 exact POP

---

## 8. 全庫 Identity+POP QC（必跑）

對 **PSA10 POP ≥ 1000** 嘅全部 seats 跑 `pipelines/identity_pop_qc.py`（唔只 top100 / 唔只 releaseReady）。

- 輸出：`data/runtime/private-reports/identity-pop-qc/<run-id>/`
- 分級：`trusted`（四源齊）/ `incomplete_four_source` / `identity_risk`（硬錯：無 GemRate exact、multi exact、POP 實體嚴重分歧）
- **releaseReady 數字 ≠ 身份可信數字。** POP 入選但 identity_risk 嘅卡可以令排行榜喺 rebind 後大變。
- QC 過程必須核實庫內 POP 所掛嘅 printing，而唔係橡皮圖章 ready 數。

首次全庫 run：`id_pop_qc_20260730_r1`（universe 1437；trusted 0 因 PC/PSA URL 未批掛；hard risk 66）。

## 9. 變更日誌

| 日期 | 決定 |
|------|------|
| 2026-07-30 | 初版四源一致；證據全掛 `variant_id`；PC 有力源；TPL 非 ranking 身份權威 |
| 2026-07-30 | `017_catalog_identity_evidence` + `identity_evidence_ledger.py` |
| 2026-07-30 | 全庫 `identity_pop_qc` 必跑；ready ≠ identity-trust |
| 2026-07-30 | **修訂：身份最低門檻 = 任意 2 源一致**；4 源齊係加厚；EN 卡 PC 應可找；CF 用 session 腳本 |

---

## 9. 違反即 stop

- 四源未對齊就宣稱「身份完美／ready 可信」
- 只靠單一源（尤其 TPL slug）定 printing
- 驗證結果只留 chat 唔寫 ledger
- 合併 conflict 兩個 POP 充大 market cap
- 人腦 review 唔附 POP / 外部 URL
