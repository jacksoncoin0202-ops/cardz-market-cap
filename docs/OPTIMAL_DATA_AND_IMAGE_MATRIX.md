# CARDZ 最優數據 + 圖片矩陣（**只寫實測通過**）

**日期：** 2026-07-30  
**硬規則：** 未 live 成功拎到嘅 **唔寫入最優**。工具包重寫以呢份為準。

「Elaine」解讀為 **EN 線（英文 Pokémon）**（同 JP Pokémon、海賊王三條產品線）。

---

## 1. 每類數據「係咩」（寫清）

| 代碼 | 意思 | 唔係咩 |
|------|------|--------|
| **I** Identity | 名 / set / collector / 語言·printing | 價錢 |
| **P_exact** | 契約 exact PSA10 參考價（可入 market_cap） | listing ask、raw 均價 |
| **P_evidence** | 價證據（可入 ledger / 對照） | 唔自動升 exact |
| **S** | PSA10 **成交**（筆數／時間） | 在售 listing 數 |
| **O** | PSA10 **人口 pop** | 成交量 |
| **G** | **卡圖** raw_front 等 | 價 / pop |
| **K** | 日線 / kline 歷史 | 單點 snapshot |
| **Stock** | 零售在庫 / ask | 二手成交 |

---

## 2. 源 × 數據類型（**2026-07-30 實測**）

| 源 | I | P_exact | P_evidence | S | O | G | K | Stock | 實測 |
|----|---|---------|------------|---|---|---|---|-------|------|
| **SNK API** `GET /v1/apparels/{id}` | ✅ productNumber | ✅ 契約價源 | ✅ min/used | ✅ trades 另腳本 | ❌ | ✅ `primaryMedia.imageUrl` CDN | kline 另腳本 | listingCount | **圖 200 webp** Umbreon+OP Ace；API 200 |
| **GemRate** | ✅ | ❌ | ❌ | ❌ | ✅ 權威 | ❌ | hist API | ❌ | 既有生產（pop） |
| **pokeca-chart API** | JP 名/set | ❌（未升格契約） | ✅ raw+PSA10 円 | 樣本 nDataNum | ✅ nPSA10Num | ✅ `/img/*-large.webp` | ✅ chart-data | ✅ shop_stock / nRushStock | **691 items decrypt OK；圖 4 尺寸 200 image/webp** |
| **PriceCharting CDP** | EN 印刷 | ❌ 非契約 exact | sold 價 | ✅ completed sold | ❌ | 頁內圖（非最優） | hist | ❌ | 既有 CDP 路徑；CF 要真 Chrome |
| **Limitless CDN** | ❌ | ❌ **禁止作 PSA10 價** | raw 市價勿用 | ❌ | ❌ | ✅ OP `{SET}/{SET}-{NUM}_EN.webp` | ❌ | ❌ | **ST13-011 / OP01-001 / OP09-013 全 200 webp** |
| **TCGPlayer CDN** | 弱 | ❌ | ❌ | ❌ | ❌ | ✅ product-images | ❌ | ❌ | **508465.jpg 200** |
| **Huca.tw** | SNK 橋 | ❌ | SNK 代理 | SNK 代理 | 榜 | 弱 | chart 代理 | ❌ | recon 完成；**唔入最優**（SNK 直連更優） |
| **CardRush HTML** | 零售 SKU | ❌ | ask | ❌ | ❌ | og:image | ❌ | 通販庫存 | recon 完成；**庫存最優經 pokeca** |
| **Camofox** | transport | — | — | — | — | — | — | — | **唔係數據源** |
| **Firecrawl 本地** | — | — | — | — | — | — | — | — | 文章 MD；**唔做市價** |

---

## 3. 圖片最優解（兩條 + EN）

### 3.1 海賊王（One Piece）— **最優：Limitless CDN**

| | |
|--|--|
| **最優** | `pipelines/op_limitless_images.py` → Limitless DigitalOcean CDN |
| **格式** | `https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/one-piece/{SET}/{SET}-{NUM}_EN.webp` |
| **條件** | `catalog_variant.collector_number` 可 parse 成 `OP13-118` / `ST13-011` |
| **實測** | 200 image/webp ~90–110KB |
| **腳本修正** | 已改：universe = **全部 one-piece catalog**（唔再只 watchlist） |
| **次優** | SNK `primaryMedia`（有 exact bind 時）— 只當 Limitless miss |
| **禁止** | 用 Limitless **價錢** 當 PSA10 |

### 3.2 Pokémon JP — **最優：SNK 主圖（有 bind）**

| | |
|--|--|
| **最優** | `pipelines/snk_image_ingest.py` ← SNK API `primaryMedia.imageUrl` |
| **實測** | API 200；CDN `cdn.snkrdunk.com/upload_bg_removed/...webp` **200 / 95KB**；`--write` vid=1730 Umbreon **written_pending** |
| **閘** | 須 SNK exact identity；SAMPLE QC；store via `native_image_resolver` |
| **次優（JP 全景卡圖）** | pokeca `get-image-url.php` / `strImgUrl` **已實測 200 webp** — 適用 **未有 SNK bind 嘅 JP 卡**；待 wire 專用 ingest（未寫生產腳本前 **唔自稱已接入 CARDZ**） |

### 3.3 Pokémon EN — **最優：SNK 主圖（有 bind）**

| | |
|--|--|
| **最優** | 同 SNK image ingest（EN 卡多數有 snkrdunk apparel） |
| **實測** | 同上 Umbreon EN Prismatic #161 snk=502830 |
| **次優** | TCGPlayer product image（有 tcgplayer id 時）— 實測 CDN 200，**身份綁定要 exact** |
| **PC 頁圖** | 唔做最優（CF 重、解析差） |

---

## 4. 價 / 量 / 圖 — 腳本 dispatch（最優 only）

| need | TCG | **唯一腳本** | 拎到咩 |
|------|-----|--------------|--------|
| `snk_price` | 全 | `snk_market_data.py` | **P_exact** |
| `snk_sales` | 全 | SNK trades ingest | **S** |
| `gemrate_pop` | 全 | `gemrate_source.py` | **O** + I |
| `pc_en_sold` | EN poke | CDP + `c11_pc_sold_ingest` | **S** + I evidence |
| `img_op_limitless` | OP | `op_limitless_images.py --write --only-missing` | **G** |
| `img_snk` | poke（JP/EN）有 SNK | `snk_image_ingest.py --write` | **G** |
| `pokeca_jp_daily` | JP poke | `pokeca_scraper.py` → **待 wire** | K + P_evidence + O + Stock + G url |

---

## 5. 今日實測執行結果

| 動作 | 結果 |
|------|------|
| SNK CDN 圖 | ✅ 200 webp poke+OP |
| SNK ingest dry | ✅ dry_ok=1 |
| SNK ingest write 502830 | ✅ **written=1** vid=1730 pending_review |
| pokeca 圖 API | ✅ thumbnail/medium/large/full 全 200 |
| Limitless OP CDN | ✅ 3/3 200 |
| TCGPlayer CDN | ✅ 200 |
| op_limitless 修 universe | ✅ 改 catalog 全 OP |
| op_limitless --write | 跑緊 / 見 runtime report |

---

## 6. 工具包 review（要重寫 / 保持）

| 工具 | 狀態 | 行動 |
|------|------|------|
| `op_limitless_images.py` | 宇宙太窄 | **已改** catalog-wide |
| `snk_image_ingest.py` | 最優 poke 圖 | **保持**；繼續 `--missing-only --write` |
| `pokeca_scraper.py` | 最優 JP 數據 | **保持**；**缺** CARDZ ingest 接線（要新寫，實測 API 先） |
| `pricecharting_cf_session` | 最優 EN sold transport | 保持 + supervisor R1–R6 |
| Camofox / Firecrawl | 非最優價圖源 | 唔寫入價/圖最優 |
| Huca / CardRush 獨立爬 | 非最優 | 唔接；CardRush 庫存跟 pokeca |

---

## 7. 一句

- **圖：OP = Limitless；Pokémon（JP/EN）= SNK API 圖；JP 無 SNK 先考慮 pokeca 圖（另 wire）。**  
- **價 exact = SNK；成交 EN = PC sold；pop = GemRate；JP 全景 = pokeca。**  
- **未實測成功嘅唔寫最優。**
