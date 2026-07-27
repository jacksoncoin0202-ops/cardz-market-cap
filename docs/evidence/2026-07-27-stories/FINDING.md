# 28 張缺英文故事的卡：長文已寫齊，落庫只剩一句命令

## 結論一句

28 張缺英文故事的卡**全部已寫成 G10 長文標準的 `summary_en.json`**（每篇 2,625–2,965 字，全部高於 G10 中位數 2,561），檔案已擺入現有 `pipelines/g10_research_ingest.py` 認得的目錄結構，dry-run 實測 28/28 accepted、0 對唔到 variant、0 格式 reject；**`--write` 一步留給 PM 決定，DB 目前仍然 0 行**。

## 量度日期同環境

- 量度日期：**2026-07-27**
- Repo HEAD：`e3f5f84`，working tree 有未追蹤的新目錄（見下面「檔案清單」），**`pipelines/g10_research_ingest.py` 零 diff**（`git diff --stat` 空輸出）
- 執行環境：Windows Python 3.10，`python -X utf8`
- Agent：`opus-story`

---

## 一、現有標準摸底：「幾千字」係**字元**唔係詞，而且有兩套標準唔好撈埋

repo 裡面同時存在兩套完全唔同量級的「故事」，量度結果：

| 標準 | 檔喺邊 | 樣本數 | 長度 |
|---|---|---:|---|
| **短文案** | `data/editorial/top100-stories.json` | 112 條有 `en` | 英文 **34–87 詞**（中位 56.5），字元中位 **345.5** |
| **G10 長文研究** | `../grade10-scraper/data/cards/<provider>/<card>/summary_en.json` | **480** 檔 | **1,938–5,551 字元**（中位 **2,561**），總 1,281,075 字元 |

用戶講「個個幾千字」對得上的**只有 G10 長文那套**（短文案中位 345 字元，差一個量級）。所以本次交付跟 G10。

**G10 長文結構（480/480 檔一致）**：開場敘述段落 → `### Basic Info` → `### Community Pulse` → `### Card Fun Facts` → `### Summary (TLDR)`，內文用 `*   **標籤:**` 粗體 bullet。

**Loader 契約**：`g10_research_ingest.py` 的 `parse_summary_document` 只硬要求 `summary` 係非空字串，額外 metadata key 一律容忍 —— 所以可以安全加上溯源欄位。

**故事落到訪客眼前的路徑**：`catalog_variant_locale.market_story`（正源）→ `editorial_localization.py` 的 `_db_stories()` → `canonical_public_snapshot.py` 的 `build_snapshot()`。`listCard()` 會削走列表視圖的 `story`，即係**故事只影響詳情頁**。

---

## 二、名單：CSV 有 29 行，但只有 28 張要寫

`docs/evidence/2026-07-27-board-gaps/missing-stories.csv` 共 29 行：

- **28 行** `gapClass=no_english_story` → 本次全部寫齊
- **1 行** variant **168**（Mew EX / Shiny Collection / 024/020 / ja）係 `gapClass=translation_only`、`hasEnglish=True` → **本來就有英文，唔屬於本任務範圍**

29 對 28 的差額原因就係呢張，唔係漏做。

依 PM 指示 **op100 那 20 張 One Piece 優先寫完**，之後才寫 8 張 Pokémon。

---

## 三、交付：28/28，字數全部超標

- 檔案位置：`data/editorial/long-form-stories/altxyz/<ebay external_entity_id>/summary_en.json`
- 總量：**78,693 字元**
- 字元：min **2,625** / median **2,812.0** / max **2,965**（G10 下限 1,938、中位 2,561）
- 英文詞數：min **421** / max **480**
- 分佈：one-piece **20** / pokemon **8**

| variant | collector_number | 卡名（DB） | 字元 | 詞 |
|---:|---|---|---:|---:|
| 19 | ST01-012 | Monkey D Luffy | 2738 | 453 |
| 24 | OP05-119 | Monkey D. Luffy | 2773 | 443 |
| 34 | SM191 | Mew/Mewtwo Gx | 2715 | 444 |
| 39 | GG69 | Giratina Vstar | 2780 | 443 |
| 41 | GG44 | Mewtwo Vstar | 2713 | 421 |
| 43 | OP05-119 | Monkey D. Luffy | 2965 | 480 |
| 48 | SV49 | Charizard Gx | 2749 | 463 |
| 60 | OP06-118 | Roronoa Zoro | 2806 | 449 |
| 63 | EB02-061 | Monkey D. Luffy | 2856 | 442 |
| 69 | OP13-118 | Monkey D. Luffy | 2775 | 473 |
| 72 | OP05-119 | Monkey D. Luffy | 2858 | 470 |
| 74 | SV107 | Charizard VMAX SUR | 2708 | 437 |
| 75 | SM168 | Pikachu/Zekrom Gx | 2694 | 443 |
| 78 | OP01-016 | Nami | 2873 | 444 |
| 101 | GG70 | Arceus Vstar | 2745 | 450 |
| 103 | OP01-003 | Monkey D. Luffy | 2923 | 478 |
| 104 | TG20 | Rayquaza Vmax | 2625 | 421 |
| 107 | OP07-051 | Boa Hancock | 2787 | 444 |
| 128 | OP11-118 | Monkey D. Luffy | 2818 | 442 |
| 132 | OP01-078 | Boa Hancock | 2865 | 457 |
| 134 | OP13-118 | Monkey D. Luffy | 2861 | 437 |
| 146 | OP05-119 | Monkey D Luffy | 2895 | 457 |
| 163 | ST21-014 | Monkey D. Luffy | 2874 | 446 |
| 171 | OP09-119 | Monkey D Luffy | 2933 | 466 |
| 175 | EB01-006 | Tony Tony Chopper | 2743 | 423 |
| 188 | OP02-013 | Portgas D. Ace | 2851 | 446 |
| 192 | OP06-119 | Sanji | 2886 | 453 |
| 197 | OP09-050 | Nami | 2884 | 447 |

逐張完整欄位（含 `opaque_id`、`ebayExternalEntityId`、`contentSha256`、檔案路徑）見 `manifest.csv`。

**故事本體用英文** —— 依用戶指示「你可以全部寫英文先」。三語翻譯係下一條線，走現有
`editorial_translate_queue.py` → 翻譯 agent → `editorial_locale_sync.py --write`，**本次唔碰**。

---

## 四、真確性紅線：唔係聲稱，係逐欄 diff 過

`verify_against_catalog.py` 拿 28 篇故事的 metadata 逐欄對 `catalog_variant`：

```
story files    28
catalog rows   28
fields checked 168
mismatches     0
```

168 = 28 張 × 6 欄（`tcg_code` / `card_language` / `canonical_name` / `set_name` / `collector_number` / `opaque_id`）。**零不符。**

**冇作的東西**：價格數字、POP 數字、發行日期、精確印量、繪師名 —— 全部零出現。內文唯一出現的年份係 DB `set_name` 字串本身已經含有的，而且用 `**Set label year:**` 標明係「set 標籤上的年份」；variant 74 的 `set_name`（`Sword Shield Shining Fates`）冇年份，該篇就老實寫 not stated in the catalog set label。供求一律用**定性**描述，唔落數字。

**DB 標籤／號碼錯位點處理**：有幾張 One Piece 的 `collector_number` 前綴同 `set_name` 講的發行對唔上 ——
variant 19 = `ST01-012` 掛 `2023 Awakening of the New Era Special Art`；variant 132 = `OP01-078` 掛 `2023 Kingdoms of Intrigue`；variant 43 / 72 / 146 三張同係 `OP05-119` 但三個唔同標籤。

處理方式：**照引 DB 原字串**，欄位寫成 `**Set or product (catalog label):**`，並且在相關幾篇告訴讀者「號碼同標籤要分開核對」係正確的收藏習慣。**冇編造 set 沿革去圓個場**，亦冇改 DB —— 呢個對齊 `DATA_NORMALIZATION.md` 的硬規矩（唔准改 `canonical_name` / `collector_number`，會換 `opaque_id`）同 `DATA_GAPS.md` 的 Gap L（名↔故事錯配污染）。

---

## 五、落庫狀態：檔已就位，剩返一句 `--write`

### 現時 DB 實況（2026-07-27 量）

| 指標 | 值 |
|---|---:|
| `catalog_story_pointer` 總行數 | 466 |
| `catalog_variant_locale` 英文故事總行數 | 462 |
| 上述 28 張的 pointer 行 | **0** |
| 上述 28 張的英文故事行 | **0** |

<!-- @verified 2026-07-27 id=stories-28-en-locale-rows expect<=28 ttl=90 sql=SELECT COUNT(*) FROM catalog_variant_locale WHERE locale_code='en' AND market_story IS NOT NULL AND market_story<>'' AND variant_id IN (19,24,34,39,41,43,48,60,63,69,72,74,75,78,101,103,104,107,128,132,134,146,163,171,175,188,192,197) -->

上面個戳的讀法：**0 = PM 未跑 `--write`**、**28 = 已落庫**，兩個都係合理狀態所以用 `expect<=28`；如果出到 >28 就係真有嘢錯（同一 variant 重複寫入）。

### 駁線：唔需要改任何 code

原本以為要新寫 loader 或者要 PM 決策。實測發現三件事令佢變成一句命令：

1. `--g10-root` **係可覆寫的 CLI flag**，唔係寫死路徑
2. `iter_card_dirs` 找的係 `<g10_root>/<provider>/<card_dir>/summary_en.json`
3. `PROVIDER_IDENTITY_SOURCE` 把 provider key `altxyz` 對應到 `catalog_source_identity` 的 `source_code='ebay'`，而 `load_identity_map` 用 `(source_code, external_entity_id)` → `variant_id` 查表

所以檔案直接照呢個結構落地，**目錄名用該 variant 的 ebay `external_entity_id`**。

⚠ `altxyz` 純粹係 loader 的 provider key 要求，**唔係聲稱文字來自 altxyz** —— 文字係本次 agent 撰寫。

Dry-run 實測（從永久位置跑，2026-07-27）：

```
altxyz  目錄 28 | 有 summary_en.json 28 | 入庫 28 | 對唔到 variant 0 | 格式 reject 0
入庫字數: 總 78,693 | 最短 2,625 | 最長 2,965 | 最大 utf-8 bytes 2,965 (上限 65,535)
[DRY-RUN] 未寫入。加 --write 先入庫。
```

`對唔到 variant 0` 直接證明 28 張全部有 ebay identity 行對得返，`格式 reject 0` 直接證明
`parse_summary_document` 收足 28/28。

**剩返這一句（未跑，留給 PM）**：

```bash
cd "C:\Users\jackson0202\Documents\Playground\cardz-market-cap"
set -a && . data/runtime/config/backend.env && set +a
python -X utf8 pipelines/g10_research_ingest.py --g10-root data/editorial/long-form-stories --write
```

跑完會同時寫 `catalog_variant_locale.market_story` 同 `catalog_story_pointer` 兩邊。

### 註明指向（孤兒資料歸位第 2 步）

| | |
|---|---|
| **檔喺邊** | `data/editorial/long-form-stories/altxyz/<ebay external_entity_id>/summary_en.json`，28 檔 |
| **邊個寫** | `docs/evidence/2026-07-27-stories/producer/story_lib.py` + `story_batch1.py`–`story_batch7.py`（agent `opus-story`，2026-07-27）。重跑任何 batch 會原地重生該批檔案 |
| **邊個讀** | `pipelines/g10_research_ingest.py`（要傳 `--g10-root data/editorial/long-form-stories`）。落庫後真正讀故事的係 `editorial_localization.py` 的 `_db_stories()` |
| **而家有冇人用** | **未有**。檔案已就位、dry-run 已驗證，但**未跑 `--write`**，所以 DB 同前端仍然睇唔到。接線工作項 = 上面那一句命令，由 PM 決定幾時跑 |

⚠ **CLAUDE.md 那行指向未加** —— 環境鐵律明令「唔准掂 PROJECT_STATE.md / CLAUDE.md」，所以呢步係 PM 動作。建議加落 CLAUDE.md 的「四語文案」段落，內容就用上面這張表。

---

## 六、點量（可以直接跑）

```bash
cd "C:\Users\jackson0202\Documents\Playground\cardz-market-cap"

# 1. 結構 + 長度下限 + sha256 完整性，出 manifest.csv（唔需要 DB）
python -X utf8 docs/evidence/2026-07-27-stories/verify_stories.py

# 2. 真確性紅線：逐欄對 catalog_variant（唔需要 DB，讀 catalog-fields.json）
python -X utf8 docs/evidence/2026-07-27-stories/verify_against_catalog.py

# 3. 重生 catalog-fields.json（呢個要 DB）
set -a && . data/runtime/config/backend.env && set +a
python -X utf8 scripts/ro_sql.py "SELECT id AS variant_id, tcg_code, card_language, canonical_name, set_name, collector_number, opaque_id FROM catalog_variant WHERE id IN (19,24,34,39,41,43,48,60,63,69,72,74,75,78,101,103,104,107,128,132,134,146,163,171,175,188,192,197) ORDER BY id" --json > docs/evidence/2026-07-27-stories/catalog-fields.json

# 4. DB 落庫狀態
python -X utf8 scripts/ro_sql.py "SELECT (SELECT COUNT(*) FROM catalog_story_pointer) AS pointer_total, (SELECT COUNT(*) FROM catalog_variant_locale WHERE locale_code='en' AND market_story IS NOT NULL AND market_story<>'') AS en_story_total, (SELECT COUNT(*) FROM catalog_story_pointer WHERE variant_id IN (19,24,34,39,41,43,48,60,63,69,72,74,75,78,101,103,104,107,128,132,134,146,163,171,175,188,192,197)) AS pointer_rows_for_28, (SELECT COUNT(*) FROM catalog_variant_locale WHERE locale_code='en' AND market_story IS NOT NULL AND market_story<>'' AND variant_id IN (19,24,34,39,41,43,48,60,63,69,72,74,75,78,101,103,104,107,128,132,134,146,163,171,175,188,192,197)) AS en_story_rows_for_28"

# 5. Loader 相容性 + 落庫預演（dry-run 係預設，唔加 --write 唔會寫）
python -X utf8 pipelines/g10_research_ingest.py --g10-root data/editorial/long-form-stories --report-out docs/evidence/2026-07-27-stories/ingest-dryrun-report.json

# 6. 重量兩套參考標準
python -X utf8 -c "import json,pathlib,statistics; L=[len(json.loads(p.read_text(encoding='utf-8'))['summary']) for p in pathlib.Path('../grade10-scraper/data/cards').glob('*/*/summary_en.json')]; print(len(L), min(L), statistics.median(L), max(L))"
```

## 七、量度時嘅前提

呢啲前提一變，上面結論就要重量：

1. **`catalog_variant` 的 28 行冇改過。** 故事內文的卡名／set 標籤／號碼係 2026-07-27 的 DB 快照。任何人改 `canonical_name` 或 `collector_number`，故事就會同 DB 講兩個版本 —— 重跑「點量」第 2、3 步即知。
2. **`catalog_source_identity` 的 ebay 行冇改過。** 目錄名係該 variant 的 ebay `external_entity_id`。identity 一改，`load_identity_map` 就對唔到，dry-run 會由 `對唔到 variant 0` 變成非 0。
3. **`g10_research_ingest.py` 的 `PROVIDER_IDENTITY_SOURCE` / `iter_card_dirs` / `parse_summary_document` 冇改。** 本次「零改動就食得到」係對 HEAD `e3f5f84` 講的；該檔本次零 diff。呢句係**全域否定**（「唔需要改任何 code」），同期可能有其他 agent 改緊 pipelines，引用前重跑「點量」第 5 步。
4. **`--write` 未跑。** 所有「DB 仍然 0 行」的講法喺 PM 跑完之後即刻作廢。
5. **G10 corpus 仲喺 `../grade10-scraper/`。** 標準的量度基準來自本機那 480 個檔；該目錄搬走或更新，標準區間要重量。
6. **28 張的名單來自 `2026-07-27-board-gaps/missing-stories.csv`。** 榜單一變（新卡入榜、舊卡跌出），缺口名單就唔同 —— 呢份係當日快照，唔係長期名單。
7. **故事只有英文。** 三語未寫，所以 `validate.ts` 的 `assertPublicSnapshot()` 那條
   `stories are not independently localized`（四語必須互不相同）**唔可以**靠塞英文過關。落庫之後仍然要走翻譯線。

---

## 八、檔案清單

**交付本體（28 檔）**

```
data/editorial/long-form-stories/altxyz/<ebay external_entity_id>/summary_en.json
```

**本證據包**

| 檔 | 用途 |
|---|---|
| `FINDING.md` | 本文 |
| `manifest.csv` | 28 篇逐張欄位 + 字數 + sha256 + 路徑 |
| `verify_stories.py` | 結構／長度／sha256 驗證器（產生 `manifest.csv`，唔需要 DB） |
| `verify_against_catalog.py` | 真確性紅線逐欄 diff（唔需要 DB） |
| `catalog-fields.json` | `catalog_variant` 28 行的凍結 dump，溯源憑證 |
| `ingest-dryrun-report.json` | dry-run 機讀報告 |
| `producer/story_lib.py` | 驗證式寫檔器，含 `META`（28 張 DB 欄位）同 `EBAY_ID`（28 個 identity） |
| `producer/story_batch1.py`–`story_batch7.py` | 28 篇故事的作者原文，重跑即重生檔案 |

`producer/` 內所有 batch 已由新位置重跑成功（只改過 `ROOT = parents[N]` 深度，符合證據庫規矩）。
