# SNK（SNKRDUNK）原圖入庫流水 — 流程 + 規模化

> **樣板：** [Glaceon VMAX HR SA S6a 091/069](https://snkrdunk.com/en/trading-cards/93024) → `snkItemId=93024`  
> **硬規則：** Harvest 可亂撈；**寫 MySQL / public 前必 QC**（[INGEST_VERIFY_GATE.md](INGEST_VERIFY_GATE.md)）  
> **入口腳本：** [`pipelines/snk_image_ingest.py`](../pipelines/snk_image_ingest.py)

---

## 0. 一句結論（sample 驗收）

| 項 | 結果 |
|---|---|
| SNK master 名 | Glaceon VMAX HR: SA[S6a 091/069](Eevee Heroes) |
| 原圖 URL | `https://cdn.snkrdunk.com/upload_bg_removed/…webp`（**bg_removed** 優先） |
| 原圖 QC | RGBA · 4 角 alpha=0（原生圓角 gate 過）· SAMPLE gate 過 |
| CardZ bind | `catalog_source_identity` `snkrdunk:93024` → `variant_id=1698`（已有） |
| 正規化 | 429×600 透明畫布 · content-hash 新檔 |
| 現況缺口 | 1698 **有 identity、無 raw_front asset**（唔喺 watchlist 亦要可入庫） |

**點解好：** 搵啱 `trading-cards/{id}` / apparel id，SNK `primaryMedia.imageUrl` 多數係去背產品圖，無 SAMPLE 水印，適合做 JP／部分 OP 裸卡圖。

---

## 1. 端到端流程（每張卡）

```text
① Identity 綁定（後台）
   catalog_source_identity(source_code=snkrdunk, external_entity_id=<id>, variant_id=?)
   ← verify_pair / semi_auto_identity / 人手 ladder
   ❌ 未 bind 或 reject → 停，唔拉圖入庫

② Harvest 原圖（可未 QC，落 temp／cache）
   GET /v1/apparels/{itemId}  → primaryMedia.imageUrl
   優先：cdn…/upload_bg_removed/*
   次選：G10 樹 cards/snkrdunk/<id>/ 本地檔
   再次：harvest jsonl 已有 image_url

③ 入庫前 QC（硬閘，全過先寫）
   a. SAMPLE / NOW DESIGNING 字拒（sample_image_qc）
   b. 可解碼；RGBA 或可補圓角
   c. 角位 alpha gate（原生圓角）或 store_face_art 補圓角
   d. Identity 語意：master name 含 collector／物種／stage（可腳本 + 殘渣人眼）
   e. 正規化 429×600（normalize_card_canvas）

④ 後台 A/B/C 一次做足
   A  market_image_asset (variant_id, raw_front, content_sha256, …)
   B  data/public/market-assets/{sha}.webp + apps/web/public/… 鏡像
   C  market_image_qc (public_allowed=1, raw_front_confirmed=1, …)
      + market_image_source_pointer (source_path 記 SNK URL 或 ref)
      + manifests/image-qc.json 可選 delta

⑤ Mark
   liquidity-source-registry / 圖源 preferred=snkrdunk（有成功先 mark）
   之後增量只跟已 bind 嘅 snk id，唔 first-hit 名搜
```

**報進度永遠三欄：** Harvest 有｜DB 寫入｜驗證綠。

---

## 2. 規模化策略（分波，唔一次全庫無閘）

### 宇宙

| 池 | 約數（2026-07-29 probe） | 用途 |
|---|---:|---|
| `catalog_source_identity` snkrdunk | ~900–1100+ | 全部已 bind id |
| 其中 watchlist | ~500–800 | FE／市值池優先 |
| 有 snk id 但缺 raw_front | ~100 | **Wave 1 補洞** |
| 有圖但 pointer 非 SNK CDN | 多數 | **Wave 2 升級**（SAMPLE／方角／TCGplayer 水印） |
| 未 bind snk | 其餘池 | **Wave 0 先 identity**（[IDENTITY_HUMAN_SEARCH_PLAYBOOK.md](IDENTITY_HUMAN_SEARCH_PLAYBOOK.md)） |

### 波次

| Wave | 目標 | 條件 | 命令骨架 |
|---|---|---|---|
| **0** | Identity | verify 過先 | `semi_auto_identity` / `bind_snk_watchlist` |
| **1** | 缺圖補洞 | snk bind + 無 raw_front | `snk_image_ingest.py --missing-only --write` |
| **2** | 品質升級 | snk bind + 現圖 SAMPLE／public_allowed=0／非 std 畫布 | `snk_image_ingest.py --upgrade-bad --write` |
| **3** | 可選 SNK 優先覆蓋 | snk bind + 已有 TCG 圖但想 JP 原圖 | `snk_image_ingest.py --prefer-snk --write`（要加 `--force` 先覆） |
| **FE** | 出街 | snapshot rebuild 後 re-scan SAMPLE=0 | `canonical_public_snapshot` + scan |

### 節流與穩定

- `get_master` delay ≥ 0.25–0.4s；失敗記 jsonl，可 resume  
- **每次新 report 檔名**（唔鎖死舊 partial）  
- 並行 workers 先 2–4；403/429 即降速  
- 唔喺 daily 主鏈盲跑 Wave 3；本機 session 揾料為主（PROJECT_STATE 階段定調）

---

## 3. QC checklist（入庫 copy）

```text
[ ] identity: catalog_source_identity snkrdunk 存在且 variant 正確
[ ] URL: primaryMedia 係 upload_bg_removed 或已知乾淨 CDN（非 listing 自拍）
[ ] sample_image_qc pass
[ ] corner / rounded 可過（原生或補角）
[ ] 429×600 已寫 content-hash
[ ] A asset + B disk(public+web) + C qc.public_allowed=1
[ ] source_pointer 記 snk URL 或 snkrdunk:{id}
[ ] reject 有 reason（唔 silently skip）
```

**禁止：** 未 bind 就靠名 first-hit 入圖；SAMPLE 出街；覆寫共用 sha 舊檔（新 hash only）。

---

## 4. 同現有鏈對位

| 現有 | 角色 |
|---|---|
| `snkrdunk_bulk.SnkrdunkApi.get_master` | 拉 master + imageUrl |
| `native_image_resolver.snk_master_url` / `store_native_image` | 下載 + 規格 |
| `sample_image_qc.assert_raw_bytes_not_sample` | SAMPLE 閘 |
| `ensure_image_abc.store_and_link` | A/B/C 寫法參考 |
| `CARD_SOURCING_HANDBOOK` 三之一 | OP 已驗收 G10 SNK 樹；本流水補 **live get_master** 同 PTCG JP |
| 價／成交 SNK | **另一條**（`snk_market_data`）；圖唔混 PSA10 價閘，但共用 identity |

圖源 priority（更新心智，詳 handbook）：

```text
OP:   Limitless _EN → G10 SNK bundle → SNK get_master → …
PTCG: TCGplayer 乾淨 → SNK get_master (JP / alt) → Kado → …
```

SNK 唔再只係「OP 專用」——**有 exact id + QC 過就可以做主圖或升級源**。

---

## 5. 驗收（pilot 93024）

| 欄 | 期望 |
|---|---|
| Harvest | master imageUrl 200 + 本地 temp 檔 |
| DB | variant 1698 有 raw_front asset + pointer + public_allowed=1 |
| 驗證綠 | SAMPLE 0 · 429×600 · 角 alpha · 人眼=冰伊布 VMAX HR 091/069 |

---

## 6. One Piece 跟進

OP SAMPLE 重災／再用 SNK 清圖 → 專檔：**[OP_SNK_IMAGE_CLEAN_PLAN.md](OP_SNK_IMAGE_CLEAN_PLAN.md)**  
（2026-07-29 實測：OP watchlist 145 張磁碟 SAMPLE=0、SNK 主圖 137；餘下係 identity + FE snapshot。）

## 7. 公開閘／Promotion（2026-07-29 硬轉）

**停 bulk 加圖當完成。** 落地檔必須再經：

**[SNK_IMAGE_PROMOTION.md](SNK_IMAGE_PROMOTION.md)** · `pipelines/snk_image_promotion.py`

- ingest → `pending_review` / `public_allowed=0`（唔再 auto 出街）  
- multi-SNK identity → reject  
- approve → hashed receipt → `human_or_vision_confirmed` + manifest auto upsert  

## 8. 變更

| 日期 | 內容 |
|---|---|
| 2026-07-29 | 首版：sample 93024 驗證；規模波次 + snk_image_ingest 入口 |
| 2026-07-29 | 連 OP 清圖 plan；W1–3 完結後 OP probe |
| 2026-07-29 | pending-only ingest + promotion 流程；demote 誤 public |
