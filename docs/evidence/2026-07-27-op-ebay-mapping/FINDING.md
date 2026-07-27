# FINDING — 132 張 OP 卡 eBay identity mapping 調查（op-ebay-mapping）

- **量度日期**：2026-07-27（DB 查詢即日執行；G10 檔案樹 snapshot 讀取即日）
- **結論一句**：132 張目標卡**冇一張可以合法新寫 eBay identity**——40 張嘅價格其實已存在於 catalog 孿生行（舊批 G10 variant，最新價 2026-07-24），1 張早已 mapped 但 alt.xyz 無成交數據，其餘 91 張 alt.xyz 根本無 asset 且 G10 生態無公開 search 可發現新 UUID；identity 寫入 0 行、價格新增 0 行，係裁決結果而非執行失敗。

## 前提

1. eBay 價格唯一活路徑係 G10 鏈：`grade10-scraper`（tRPC `price.getEBayDetails`，asset source=`altxyz`）→ `pipelines/g10_ebay_ingest.py` → 三張 fact table。CARDZ 側 `run_daily.py` 嘅 ebay 線（`ebay_sold_data.py`）因 `CARDZ_EBAY_SOLD_ENABLED`/`CARDZ_EBAY_SOLD_INPUT` 未配置係死線。
2. `catalog_source_identity` PK = (source_code, external_entity_id)：**一個 altxyz UUID 只能綁一個 variant**。alt.xyz 一張實體卡（一個 art 版本）只有一個 asset UUID——孿生行冇「第二個 UUID」可綁。
3. G10 scraper 卡 universe 只由三個 index（ptcg/ptcg100/opcg）嘅 constituents 枚舉，冇 per-card search 功能。
4. 目標名單 `docs/evidence/2026-07-27-op-price-mainline/target_list_132.jsonl` 凍結不變；名單以外唔寫 identity、唔採集（紅線）。

## 點量（可重跑）

```bash
cd "C:\Users\jackson0202\Documents\Playground\cardz-market-cap"
set -a && . data/runtime/config/backend.env && set +a

# 1. eBay identity 總數（量得 119 行）
python -X utf8 scripts/ro_sql.py "SELECT COUNT(*) FROM catalog_source_identity WHERE source_code='ebay'"

# 2. OP 卡號格式嘅 ebay identity variant + 各自 ebay 價格行數（量得 53 個 variant，52 個有價 4-30 行，最新 2026-07-24）
python -X utf8 scripts/ro_sql.py "SELECT csi.variant_id, cv.collector_number, cv.set_name, COUNT(mpo.variant_id) AS ebay_price_rows, MAX(mpo.observed_date) AS latest FROM catalog_source_identity csi JOIN catalog_variant cv ON cv.id = csi.variant_id LEFT JOIN market_price_observation mpo ON mpo.variant_id = csi.variant_id AND mpo.source_code='ebay' WHERE csi.source_code='ebay' AND (cv.collector_number REGEXP '^(OP|ST|EB|PRB)[0-9]' OR cv.collector_number IN ('P-001','112')) GROUP BY csi.variant_id, cv.collector_number, cv.set_name ORDER BY cv.collector_number"

# 3. before/after 基準：132 張目標卡嘅 ebay 價格行（before = 0 行 / 0 卡；本任務冇寫入，after 相同）
#    （IN list 由 target_list_132.jsonl 嘅 variant_id 生成）
python -X utf8 scripts/ro_sql.py "SELECT COUNT(*), COUNT(DISTINCT variant_id) FROM market_price_observation WHERE source_code='ebay' AND variant_id IN (<132 個 variant_id>)"

# 4. 分類重現：132 張 × 現有 identity × G10 altxyz 檔案樹
python -X utf8 docs/evidence/2026-07-27-op-ebay-mapping/op_ebay_match.py
```

## 統計總表

| 分類 | 張數 | PSA10 POP 合計 | 說明 |
|---|---|---|---|
| twin-mapped（孿生行已有價） | 40 | 89,219 | 同 collector_number 嘅舊批 G10 variant 已綁 UUID 且有活躍 ebay 價（孿生行合計約 589 行，最新 2026-07-24） |
| already-mapped-no-sales | 1 | 1,032 | v1254 OP01-003 Errata（UUID `36bc91e4`）：identity 早已存在，但 alt.xyz 無 eBay PSA10 成交統計（G10 目錄只有 populations.json） |
| unmapped：no-uuid-in-g10 | 91 | 149,154 | alt.xyz 無現成 asset；多數係普通 SR/rare/stage/DON 卡，唔係 alt.xyz 收錄嘅高價 alt-art 資產 |
| **新寫 identity** | **0 行** | — | dry-run 報數：計劃寫入 0 行（無合法 UUID 可綁） |
| **新增價格行** | **0 行（覆蓋 0 卡）** | — | before=0 / after=0（唯讀驗證） |

## 核心發現：catalog 平行世界（兩批 variant 行）

catalog_variant 對 One Piece 存在兩批行：

- **舊批（variant id 19–318 為主）**：set_name 係 G10 風格（例 `2023 Awakening of the New Era Manga Alternate Art`），53 個 variant 已綁 altxyz UUID，52 個有 ebay 價格（4–30 行/卡）。
- **新批（589–1568，GemRate 凍結掃開行）**：set_name 係 GemRate 風格（例 `One Piece Awakening of the New Era`），有 POP 觀測、零價格——132 張目標卡全部屬此批。

**40 張目標卡嘅「零價格」係 catalog 重複行造成嘅假象**：同一張實體卡，價喺舊行、POP 喺新行。因為 UUID PK 一對一 + alt.xyz 一卡一 asset，**技術上不可能**為新行再寫 identity（會撞 PK / 要搶舊行 UUID = UPDATE，紅線禁止）。正解係 catalog 層面 dedupe/merge——屬 PM 治理決策，超出本任務權限（唔准改 catalog_variant）。

### twin 對應細分（俾 merge 決策用）

**1:1 高信心孿生（7 個卡號，7 張目標卡）**——舊批同新批各得一行，幾乎肯定同卡：

| 卡號 | 目標行 | 孿生行（有價） | 孿生價格行數 |
|---|---|---|---|
| OP01-078 Boa Hancock | v953 | v132 | 17 |
| OP06-119 Sanji | v1232 | v192 | 17 |
| OP08-118 Silvers Rayleigh | v1428 | v308 | 16 |
| OP09-061 Monkey D. Luffy | v941 | v262 | 13 |
| OP10-119 Trafalgar Law | v1430 | v301 | 11 |
| OP11-080 Gear Two | v1440 | v247 | 10 |
| OP11-118 Monkey D. Luffy | v1227 | v128 | 11 |

**N:M 模糊（17 個卡號，33 張目標卡）**——同卡號多個 art 版本（Manga/Alt/SP/Wanted/serialized），要 PSA label variety 先分得開邊行對邊行。極端例：OP05-119 目標 3 行（v1204/v1457/v1248）vs 舊批 6 行已綁（v24/v304/v146/v239/v72/v43，合計 96 價格行）。全表見 `mapping_classification.json`。

## Discovery 試探記錄（點解 91 張收手）

按「同一假設最多 3 次」紀律，試探全部無果即收：

1. grade10 tRPC 盲猜 search procedure 5 個名（`price.searchAssets`/`price.search`/`asset.search`/`search.assets`/`price.getAssetSearch`）→ 全部 404 `NOT_FOUND`（endpoint 活，procedure 唔存在）。
2. 掃 app.grade10.com 首頁 12 個 JS bundle grep procedure 字串 → 零 match（turbopack 切碎，首頁無 search 功能）。
3. alt.xyz：首頁係 SPA、`app.alt.xyz/api/search` 301，無公開 search API。

即係：G10 生態內冇安全途徑為 index 以外嘅卡發現 altxyz UUID。91 張 no-uuid 卡誠實標 unmapped。

## 附帶觀察（唔阻塞，記錄俾 PM）

- 2 個 unbound OP UUID 存在於 G10 檔案樹但未綁任何 variant：`5570a773`（ST10-006 1st Anniversary Luffy——唔喺 132 名單）同 `a7d2dc8b`（cardId=`112`，2026 Boa Hancock Official Event Top Prize serialized promo）。後者卡號含糊，同名單內 v1462（OP14-112 set 卡，pop 1,782）**唔判定**為同卡（serialized top prize 流通量同 pop 對唔上），維持 unmapped，唔估。
- v1254（名單內唯一已 mapped）嘅 G10 目錄 `36bc91e4` 只有 populations.json——G10 scraper 只喺 `averagePrice` 非空先寫價格檔，即 alt.xyz 對呢張卡無 eBay 成交統計。屬「mapped-but-no-sales」，同 unmapped 分開計。
- 跑一次 `g10_ebay_ingest.py` 可以將 G10 最新檔案入庫，但受惠者全部係名單以外嘅已 mapped 卡（紅線：名單外唔採集），故本任務冇跑。

## Evidence artifacts

- `mapping_classification.json` — 132 張逐張分類（category / twin_variants / unbound_uuids）
- `op_ebay_match.py` — 分類重現 script（唯讀，唔寫 DB）
