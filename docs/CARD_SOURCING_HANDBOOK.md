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

## 三、卡圖來源 priority chain（實測 2026-07-24）

逐個來源試，每張候選圖落完必過 **corner-alpha gate**（4 角 pixel alpha 全部 < 10）先收貨：

| 優先 | 來源 | 格式 | 覆蓋 | 備註 |
|------|------|------|------|------|
| 1 | SNK harvest cache（`data/private/snkrdunk_brute/snkrdunk_all.jsonl`） | RGBA WebP 1000×730 | 寶可夢 JA 主力 | 100% 原生圓角 |
| 2 | SNK get_master（`pipelines/snkrdunk_bulk.py`） | RGBA WebP | cache 冇嘅用 universe `snkItemId` | 同上 |
| 3 | Kado dump RGBA WebP（`kado-dump`） | RGBA | ~16% set 有原生圓角 | 冷門備用 |
| 4 | TCGdex EN PNG | RGBA PNG 600×825 | 英文卡專用 | ja 同 webp 版冇 alpha，日文卡唔好用 |

**OPTCG（One Piece）** 已有既定路線（2026-07-27 用戶驗收拍板），見下面「三之一、OPTCG 卡圖來源」。

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
3. 新 OP 卡揾圖次序：**先查 G10 樹 snkrdunk 目錄有冇現成 bundle**（480 個 dir 覆蓋主流卡），有就直接用；冇先行 SNKRDUNK API（[docs/SNKRDUNK_API_MANUAL.md](SNKRDUNK_API_MANUAL.md)）。
4. 呢條源同時係**價源**（`ebay_PSA_10.json`）同 **POP 源**（`populations.json`）—— 一個 bundle 三種數據，揾新卡優先行呢度係一石三鳥。

### 來源審批制度（2026-07-27 起，用戶欽點工作流）

用戶會逐個源做人眼驗收（圖質、水印、方向），**OK 嘅先記入呢本手冊做 canonical**；被彈嘅源要喺呢度記低唔准再用。而家嘅記錄：

| 源 | 判決 | 日期 | 原因 |
|---|---|---|---|
| G10 樹 SNKRDUNK assets（OPTCG） | ✅ 收貨 | 2026-07-27 | 「啲卡又是正，亦都冇 sample 字眼」 |

新源未經用戶過目前，只准落 staging/temp 比較，唔准直接入庫做 raw_front。

---

## 四、每日全自動流程（新卡入列點行）

```
run_daily.py
  └─ canonical_public_snapshot.py   # 由 DB 出新 ranking snapshot
  └─ ensure_std_card_images.py      # ← 卡圖自愈（新加嘅一步）
  └─ publish-snapshot.mjs           # 發佈
```

`ensure_std_card_images.py --write` 對 snapshot 每張卡做 delta 檢查：

1. 唔係 429×600 → `store_normalized_image()`（alpha-bbox crop + 等比縮放 + 置中）
2. 方角 → `store_rounded_image()`（補 6% 圓角）
3. 已達標 → skip（content-addressed，同一張圖永遠唔會重做）
4. 寫 `image-qc.json` QC 記錄（`stdCanvas: "std-429x600"`）

**即係：新卡今日入 Top 350，聽日 pipeline 行完佢已經係梵高標準，唔駛人追。**

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
