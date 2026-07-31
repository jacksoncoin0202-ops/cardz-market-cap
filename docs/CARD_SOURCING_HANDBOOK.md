# CARDZ Market Cap — 揾卡手冊（Card Sourcing Handbook）

呢本手冊係「一有新卡入列，點樣全自動攞齊佢要嘅嘢」嘅制度。目標：**零人手**，每日 pipeline 自己搞掂。

---

## 一、一張卡喺我哋網站需要咩（驗收清單）

每張卡入 Top 350，必須齊以下 4 樣先算合格：

| 項目 | 規格 | 邊度驗證 |
|------|------|----------|
| **裸卡圖** | 429×600 透明畫布、RGBA、原生圓角（角弧 ~6% 卡寬）、梵高比卡超做基準 | `manifests/image-qc.json` 有 `stdCanvas: "std-429x600"` 記錄 |
| **身份** | tcg + collectorNumber.display 唯一；名（en/ja/zhCN/zhTW）、set、稀有度 | snapshot card block |
| **市場數據** | PSA 10 價、population、market cap、1d/7d/30d 變化、grader populations | snapshot `pricePsa10` / `windows` / `graderPopulations` |
| **故事（editorial）** | 卡嘅背景/來歷/點解貴，4 語（en/zhTW/zhCN/ja） | `data/editorial/top100-stories.json`，join by card id |

---

## 二、卡圖標準（hard，2026-07-25 用戶規矩）

**全站每一張裸卡圖都係同一塊畫布，梵高比卡超（rank #1）做基準。**

- 畫布：**429×600**，透明底（RGBA）
- 卡身：等比縮放填滿 98.5%（`CANVAS_FILL`），置中
- 圓角：**原生圓角**，角弧 ~6% 卡寬（梵高實測 423×589 卡身角弧 ~25px）
- **唔准** CSS `border-radius` 削角、唔准 `transform: scale()`、唔准 `object-fit: cover` 裁切
- **唔准** `.convert("RGB")` 壓平 alpha（呢個係舊白邊 bug 嘅根源）

### 點解唔用 CSS 遮？
因為「張原圖唔得」——問題要喺來源圖本身解決，唔係前端夾硬遮。CSS 削角會食角、會忽大忽小、會喺深色底現白邊。

---

## 三、卡圖來源 priority chain（2026-07-28 用戶拍板）

`pipelines/native_image_resolver.py` + `pipelines/tcgplayer_images.py`：

| 優先 | 來源 | 格式 | 覆蓋 | 備註 |
|------|------|------|------|------|
| 1 | **TCGplayer** CDN（主線） | JPG/WebP product art | EN/多語卡面 | 方角 → `store_face_art_image`（normalize + 補圓角）。多印次唔好 first-hit 自動入庫 |
| 2 | **SNK** live `get_master` / harvest / G10 樹（**有 exact snk id 時可升為主圖**） | RGBA WebP `upload_bg_removed` | 寶可夢 JA / OP | 必先 identity bind + QC；見 [SNK_IMAGE_PIPELINE.md](SNK_IMAGE_PIPELINE.md) |
| 3 | Kado dump RGBA WebP | RGBA | 冷門 | 備用 |

TCGplayer SAMPLE 水印圖要 QC reject（尤其部分 OP 印次），唔准當乾淨卡面出街。

**SAMPLE vs 語言（2026-07-31 修正）**  
- 有 SAMPLE ≈ 多來自 **TCGplayer EN** 商城圖（美／英版觀感）——水印係商城防盜，唔係「日版標籤」。  
- Limitless `_EN.webp` **整個 One Piece family 拒收**：2026-07-31 全量 OCR
  實測有系統性 SAMPLE 污染；唔再交人工候選。
- `_EN.webp` 只可配 `en` variant。DB 係 `ja` 就算卡號相同都要
  `reject_language_mismatch`，改搵 G10／SNK／Drive 同語言 exact printing。
- OP **美／日共用 number**（如 OP01-016），但係 **唔同 SKU／語言印刷**；comic／SEC-SP 同 collector 可能共用 base 面——已知風險，要按 printing 分圖時另開。

**OPTCG（One Piece）** 既定路線見下面「三之一」；Limitless One Piece
已整源停用。

### 方角卡點算？（自助轉格式，已解決）
如果只有 RGB 方角圖（舊 ingest 壓平咗），**唔使重下載**：用
`native_image_resolver.apply_rounded_corners()` 補返 6% 卡寬嘅原生圓角。只改 alpha channel，卡面內容一個 pixel 都唔郁。呢個就係「自己手動轉格式」嘅答案 —— 已經做埋，74 張方角卡 2026-07-25 一次過補齊。

---

## 三之一、OPTCG（One Piece）卡圖來源 —— G10 樹入面嘅 SNKRDUNK assets（2026-07-27 用戶驗收拍板）

**用戶原話**：「你依家揾海賊王卡揾嗰個源頭幾好喎。即係啲卡又是正，亦都冇 sample 字眼。你可以記低，變成之後揾卡、揾卡腳本嘅來源。」—— 即係呢條源已通過人眼驗收（圖質正、無浮水印），係 OPTCG 嘅 **canonical 圖源**。

### 條源實際係咩（實測 2026-07-27）

| | |
|---|---|
| **檔喺邊** | `C:\Users\jackson0202\Documents\Playground\grade10-scraper\data\cards\snkrdunk\<numeric_id>\`（G10 harvest 樹，repo 外），實測 480 個 snkrdunk 目錄 |
| **每個目錄有咩** | `asset_info.json`（含卡圖 URL 同 metadata）· `ebay_PSA_10.json`（PSA10 成交）· `populations.json`（四廠 POP）· `summary_en.json` / `summary_jp.json`（研究文，注意 jp 係英文複製品） |
| **邊個寫** | `grade10-scraper`（SNKRDUNK 區採集）；跟手 [pipelines/grade10_full_freeze.py](../pipelines/grade10_full_freeze.py) 抄樹入 private landing |
| **邊個讀** | [pipelines/g10_variant_seed.py](../pipelines/g10_variant_seed.py) 寫 `source_path`（相對 g10 root 嘅 `cards\snkrdunk\...` 指針）→ image pipeline 循指針取圖 |
| **而家有冇人用** | ✅ 現行 27 張 published OP 卡入面 **26 張**嘅 `raw_front` 指針就係呢條源；唯一例外係 rank 95 Boa Hancock OP07-051 行咗 `cards\altxyz\<uuid>\`（alt.xyz 源） |

### 點解 SNK 係主路、alt.xyz 唔係

- alt.xyz 只收高價 alt-art 資產：132 張 roster 內零價 OP 卡入面 **91 張 alt.xyz 根本無 asset**（見 [2026-07-27-op-ebay-mapping FINDING](evidence/2026-07-27-op-ebay-mapping/FINDING.md)），普通 SR/rare 卡佢唔收。
- SNKRDUNK 係日本市場主流平台，OP 區覆蓋廣，圖係原生產品圖（無 sample/watermark）。
- 佐證：rank 95 Boa 係 27 張入面**唯一**冇 snk_psa10 價源嘅卡，亦係唯一行 altxyz 圖源嘅卡 —— 佢係經非 SNK 路入 catalog，圖源同價源缺失同根。

### 用呢條源要記住

1. 落圖照過 corner-alpha gate（4 角 alpha < 10）+ `normalize_card_canvas()` 429×600，同寶可夢一致，冇豁免。
2. `summary_jp.json` 唔係日文（G10_BASELINE 三坑之一），唔好攞嚟做 ja 文案。
3. **FE／出街 OP 圖次序（2026-07-29）**  
   1. G10 樹 snkrdunk exact bundle / SNK product CDN（語言、卡號、印次一致）
   2. 已購／Google Drive exact 圖庫
   3. 其他 exact clean source（Limitless One Piece 已拒收）
   **禁止** TCGplayer SAMPLE 出街。  
4. 呢條 G10 源同時係**價源**（`ebay_PSA_10.json`）同 **POP 源**（`populations.json`）。  
5. 換圖用 **新 content-hash**；唔覆寫舊 `{sha}.webp`（可能共用）；舊 sample 檔可留碟。

### 來源審批制度（2026-07-27 起，用戶欽點工作流）

用戶會逐個源做人眼驗收（圖質、水印、方向），**OK 嘅先記入呢本手冊做 canonical**；被彈嘅源要喺呢度記低唔准再用。而家嘅記錄：

| 源 | 判決 | 日期 | 原因 |
|---|---|---|---|
| G10 樹 SNKRDUNK assets（OPTCG） | ✅ 收貨 | 2026-07-27 | 「啲卡又是正，亦都冇 sample 字眼」 |
| Limitless OP `_EN.webp` CDN | ❌ 整源拒收 | 2026-07-31 | 系統性 SAMPLE 污染；不可再入人工候選 |
| SNK live `get_master` `upload_bg_removed` | ✅ 收貨（有 exact id） | 2026-07-29 | sample [93024 Glaceon VMAX HR](https://snkrdunk.com/en/trading-cards/93024)：RGBA 原生圓角、無 SAMPLE；**必先 identity bind + QC** |
| TCGplayer SAMPLE 水印 | ❌ 禁出街 | 持續 | QC reject |

新源未經用戶過目前，只准落 staging/temp 比較，唔准直接入庫做 raw_front。

### SNK live 原圖流水（2026-07-29）

- **Plan / 波次：** [docs/SNK_IMAGE_PIPELINE.md](SNK_IMAGE_PIPELINE.md)
- **腳本：** [pipelines/snk_image_ingest.py](../pipelines/snk_image_ingest.py)（`--snk-id` / `--missing-only` / `--upgrade-bad`；預設 dry-run，`--write` 先入庫）
- **後台綁定：** 先 `catalog_source_identity` snkrdunk → 再 A/B/C（asset + disk + `public_allowed`）
- **入庫前 QC：** SAMPLE 拒 · title/collector 對 master 名 · 角 alpha／補圓角 · 429×600

---

## 四、每日全自動流程（新卡入列點行）

```
run_daily.py
  └─ canonical_public_snapshot.py   # 由 DB 出新 ranking snapshot
  └─ ensure_std_card_images.py      # ← 卡圖自愈（新加嘅一步）
  └─ publish-snapshot.mjs           # 發佈
```

`ensure_std_card_images.py --write` 對 snapshot 每張卡做 delta 檢查：

1. 用 `cardz-front-geometry-v1` 驗 429×600 透明 RGBA 畫布、卡面雙軸填充
   ≥95%、卡面比例 0.68–0.75、中心偏移 ≤6 px；淨係尺寸相同唔算達標
2. geometry 唔合格 → `store_normalized_image()`（alpha-bbox crop + 等比縮放 + 置中）
3. 方角 → `store_rounded_image()`（補 6% 圓角）
4. 已達標 → skip（content-addressed，同一張圖永遠唔會重做）
5. 寫 `image-qc.json` QC 記錄（`stdCanvas: "std-429x600"`）

**即係：新卡今日入 Top 350，聽日 pipeline 行完佢已經係梵高標準，唔駛人追。**

語義 QC 另行用 human／vision receipt。`local_vlm_image_review.py` 會先過
geometry gate，再逐張寫私人 receipt；單張 timeout 只入 failure ledger，唔會
取消同批成功結果，亦唔會自動批准 DB 或 public asset。

SAMPLE 漏斗用 `cardz-source-sample-v1`：已證實必帶 SAMPLE 嘅 One Piece
官方 card-list path 直接整源拒收；TCGplayer 只按已證實嘅 OP 水印模板尺寸
`600×837`、`600×838`、`716×1000` 拒收，唔准將全部 TCGplayer 一刀切。
所有來源（包括 Limitless 同 SNK）都要跑 SAMPLE OCR；另外先用來源檔名／
metadata 做語言硬閘。錯卡、印次、語言、geometry、public approval 仍然
逐張驗。最後灰區交本機人工 review；主流程唔用本機 LLM。

本機人工 review：

1. `image_prefilter_qc.py` 先剔除 geometry／已知污染源／缺 asset，並寫 failure ledger。
2. `image_review_proxy.py build` 將 survivors 凍結成 hash-bound dataset。
3. `image_review_proxy.py serve` 只聽 `127.0.0.1`；DADDY 逐張撳 OK／唔得。
4. 頁面輸出 `CARDZ-IMG-QC1:` decision code；唔直接連 DB。
5. `image_review_decisions.py` 預設 dry-run，重驗 dataset、variant、latest asset、
   content hash、geometry 同 source policy；`--write` 先寫 `human-review-v1`
   QC，同時只將選中嘅 source pointer 設為 public-eligible。

---

## 五、故事 / metadata（editorial）

- 現況：`data/editorial/top100-stories.json` 100 條，join by card id（`snapshot.ts` 嘅 `editorialStories()`，id + tcg + cardLanguage + collectorNumber 四 key 對先中）
- **已知陷阱**：canonical snapshot 同 seed snapshot 用**唔同 card id**。join 兩邊必須用 **(tcg, collectorNumber.display)**，唔好用 `id`——用 id 會靜靜雞整唔見晒啲故事（vitest `snapshot.test.ts` 會炸）
- 新卡入列 → 故事係空 → fallback 去 editorial pack → 冇就顯示空。**故事生成係下一個要自動化嘅位**（暫時人手/半自動）

---

## 六、長期須知（唔係 block，係一定要記住處理嘅嘢）

| 項目 | 狀態 | 點處理 |
|------|------|--------|
| **方角卡** | ✅ 已解決 | `apply_rounded_corners()`，74 張已補，工具常駐 |
| **4 張 JA promo**（Pikachu 227/S-P、Poncho 208/XY-P、Cramorant 226/S-P、Clefairy 381/SM-P） | ✅ 已解決 | 同上方角修復 |
| **OPTCG raw 卡路線** | ✅ 已解決（2026-07-27） | G10 樹 SNKRDUNK assets 做 canonical 源，用戶人眼驗收拍板。見「三之一」章節 |
| **故事自動生成** | ⚠️ 半自動 | 新卡入列故事空白；要接 story pipeline 先至全自動 |
| **DB 每日變** | ✅ 已應對 | content-addressed asset + QC delta + 每日 self-heal |

---

## 七、相關檔案

| 檔案 | 作用 |
|------|------|
| `pipelines/native_image_resolver.py` | 來源 chain、corner-alpha gate、normalize、圓角修復 |
| `pipelines/ensure_std_card_images.py` | 每日卡圖自愈 CLI |
| `pipelines/canvas_normalize_backfill.py` | 一次性遷移（已用完，留做參考） |
| `pipelines/run_daily.py` | 每日 orchestration（已插入 self-heal） |
| `manifests/image-qc.json` | 每張卡嘅 QC 記錄 + stdCanvas 標記 |
| `tests/test_native_image_resolver.py` | 12 個 regression test |
| `data/editorial/top100-stories.json` | 故事 pack |
