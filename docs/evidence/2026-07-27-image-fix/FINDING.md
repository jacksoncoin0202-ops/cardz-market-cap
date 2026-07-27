# 卡圖換圖 + 缺圖補齊（2026-07-27）

## 結論一句

**25 張卡處理完 4 張（2 張原位換走爛圖已出街 · 1 張新填入庫但未出街 · 1 張入完撤回），
剩返 21 張唔係「揾唔到」而係「揾到但唔合格」——
20 張 One Piece 嘅正確印次喺 TCGplayer CDN 上面 100% 帶 Bandai 對角 SAMPLE 水印，
本機 bytes 100% 係評級殼拍賣相，兩條路都唔係覆蓋率問題而係來源本身冇乾淨卡面。**

## 量度日期 · 量度人

2026-07-27 · `opus-image-fix`

## 量度時嘅前提（過期之後靠呢段判斷仲啱唔啱）

1. **`manifests/image-qc.json` 係 producer 唯一認嘅卡圖來源。**
   [canonical_public_snapshot.py](../../../pipelines/canonical_public_snapshot.py) 個 `load_public_images()`
   用 `publicId`（= `catalog_variant.opaque_id`）做 key，同時要求 `publicAllowed` 真、
   `imageKind == "raw_front"`、64 字 sha、有 width/height、而且正本檔真係喺 `data/public/market-assets/`。
   任何一項唔齊，張圖就係「喺磁碟但 producer 睇唔到」。
2. **一次換圖要同時動四個地方**：producer 正本目錄、`apps/web/public/market-assets/`（Next.js 靜態）、
   `manifests/image-qc.json`、`data/runtime/local-serve/snapshot.json`。少動一個就出「檔案喺但 snapshot 唔認」
   或者反過來。
3. **舊圖一律唔刪。** sha 係內容定址，歷史 snapshot 仲指住舊 sha。
4. **標準畫布 429×600、圓角 5.9% 卡寬**，全部叫 [native_image_resolver.py](../../../pipelines/native_image_resolver.py)
   嘅 `normalize_card_canvas()` + `apply_rounded_corners()`，一個 byte 都冇自己發明。
5. **`semanticMatchStatus` 得兩個合法值**：`metadata_exact_unreviewed` 同 `human_or_vision_confirmed`。
   人手覆核過嘅寫後者。
6. **缺圖名單來源**：[docs/evidence/2026-07-27-board-gaps/missing-images.csv](../2026-07-27-board-gaps/missing-images.csv)
   嗰 22 張。PM 中途追加 3 張（v8 EB02-010、v111 ST10-006、v276 EB03-026），所以總數 25。

## 點量（直接跑得返）

```bash
cd "C:\Users\jackson0202\Documents\Playground\cardz-market-cap"

# 1. 缺口表（自己對返 manifests/image-qc.json，表同庫對唔上會即場出 None）
python -X utf8 docs/evidence/2026-07-27-image-fix/build_gap_table.py

# 2. QC 記錄數 / 唯一 publicId 數 / producer 真正認到幾張
python -X utf8 -c "
import json, sys, collections
from pathlib import Path
sys.path.insert(0, 'pipelines')
recs = json.loads(Path('manifests/image-qc.json').read_text(encoding='utf-8'))['records']
print('QC 記錄', len(recs), '/ 唯一 publicId', len(set(r['publicId'] for r in recs)))
print('重複 publicId', len([k for k,v in collections.Counter(r['publicId'] for r in recs).items() if v>1]))
from canonical_public_snapshot import load_public_images
print('producer 認到', len(load_public_images().by_public_id))"

# 3. 卡身分（語言對唔對得住張圖，就係靠呢個）
set -a && . data/runtime/config/backend.env && set +a
python -X utf8 scripts/ro_sql.py "SELECT id, opaque_id, canonical_name, collector_number, card_language FROM catalog_variant WHERE id IN (8,35,111,168,276)"

# 4. 閘仲過唔過
node --test tests/data/privacy-and-images.test.mjs

# 5. 單卡入庫／換圖（預設 dry-run，唔加 --write 咩都唔改）
python -X utf8 docs/evidence/2026-07-27-image-fix/ingest_card_image.py \
  --public-id <opaque_id> --source <URL 或本機路徑> --alt-en "<英文 alt>"
```

## 數目對帳

producer 認到嘅圖：**419 → 421 → 420**<!--@verified 2026-07-27 id=img.producer.recognised expect>=420 ttl=3 cmd=python -X utf8 -c "import sys;sys.path.insert(0,'pipelines');from canonical_public_snapshot import load_public_images;print(len(load_public_images().by_public_id))"-->

| 步 | 動作 | producer 認到 |
|---|---|---:|
| 起點 | — | 419 |
| +1 | v168 Mew EX 新填 | 420 |
| +1 | v35 Pikachu 新填 | **421** |
| −1 | v35 撤回（語言對唔上） | **420** |
| ±0 | v8 EB02-010 換圖（原位換 sha） | 420 |
| ±0 | v111 ST10-006 換圖（原位換 sha） | 420 |

**producer 淨增 1 張（v168）+ 2 張原位換走爛圖。** 換圖唔會加數，因為卡本來已經有圖 —— 有圖但係爛圖。
⚠ 「producer 認到」唔等於「訪客見到」，v168 就係差呢一步，見上面嗰段。

QC 記錄 **624** 條 / 唯一 publicId **420** 個<!--@verified 2026-07-27 id=img.qc.records expect>=624 ttl=3 cmd=python -X utf8 -c "import json;from pathlib import Path;print(len(json.loads(Path('manifests/image-qc.json').read_text(encoding='utf-8'))['records']))"-->

## 已入庫嘅三次寫入

| 卡 | 舊嘢 | 新來源 | 新 sha（頭 16） | 而家出街？ |
|---|---|---|---|---|
| **v8 EB02-010** Monkey.D.Luffy L | 卡封喺未開封卡包／膠套嘅照片 | TCGplayer CDN `641620`（625×873 · 無水印） | `b7e4d2e14b0580b6` | ✅ 已出街（live snapshot `top100`） |
| **v111 ST10-006** Monkey.D.Luff | 「NOW DESIGNING」官方佔位圖 | TCGplayer CDN `557283`（500×701 · 無水印） | `74169e81096ade82` | ✅ 已出街（live snapshot `top100`） |
| **v168 024/020** Mew EX | 冇圖 | 本機 `snkrdunk_91584.jpg`（711×1000） | `9922a15ca891a36b` | ⚠ **未出街**，見下 |

三張都係 `human_or_vision_confirmed`，`stdCanvas: std-429x600`，三個衍生檔（`.webp` / `_200` / `_600`）齊，
兩邊 assets 目錄各 3 個檔、byte 數一致（共 18 個檔）。

### ⚠ v168 入咗庫但未出街 —— 唔好當已上線

`data/runtime/local-serve/snapshot.json`（generation `canonical_20260725_aaca5dd2ee9f`）
**只有 `top100`(100) 同 `watchlist`(143) 兩個榜**，冇 `ptcg100` / `tcg300`。
而 v168 嘅榜係 `ptcg100|tcg300` → 呢張卡**唔喺現行 live snapshot 任何一個榜**，
所以 `patch_live_snapshot()` 冇位可以改，回報 `boards: []`。

即係：v168 張圖**已經入 `manifests/image-qc.json` 兼 producer 認到**，
**但要等下一次出 snapshot（而且嗰份 snapshot 要包含佢個榜）先會俾訪客見到**。

v8 同 v111 唔同 —— 兩張都真係喺 live `top100` 入面，pointer 已經改好，**即刻見得到**。

（缺圖名單 [missing-images.csv](../2026-07-27-board-gaps/missing-images.csv) 用嘅係
`ptcg100|tcg300|op100` 呢套榜名，同 live snapshot 嘅 `top100|watchlist` 唔同一個世代。
落手之前要留意自己睇緊邊套榜名。）

### PM 要求嘅三準則覆核（逐項對過）

覆核圖 [shipped-verify.png](shipped-verify.png)：卡圖疊喺洋紅／青綠棋盤格上面，透明位一睇就見。

| 準則 | 結果 |
|---|---|
| 1) 有冇白色角 | **冇。** 四角 alpha 實測全部 `[0, 0, 0, 0]`，棋盤格穿得過四個角 |
| 2) 係咪嗰張卡 | **係。** 單卡卡面，唔係卡包／唔係膠殼。卡號目視讀得出：EB02-010 / ST10-006 / 024/020 |
| 3) 有冇走位 | **冇。** 等比置中，`is_normalized()` 過，卡面內容冇被裁 |

額外自己加嘅一項：**有冇水印** —— 三張都冇。

## 更正一件事：EB02-010 唔使重換，一早已經換好

PM 嘅 QC 線報「v8 仍然係一包未開封卡包嘅照片」。**呢個報告係讀咗一份凍結嘅 pre-fix 檔得出嘅。**

- QC 線引用嘅 [../2026-07-27-board-gaps/board-gaps.json](../2026-07-27-board-gaps/board-gaps.json)
  係換圖**之前**凍結嘅證據，入面仲寫住舊 sha `398cebb1…`。
- 舊 sha 全 repo grep 得返：只喺 evidence / manifest 索引 / `temp/` 出現，**live snapshot 一個都冇**。
- v8 個 `opaque_id` = `cmc_93789e346bbc157fac002457`，QC 同 live snapshot 兩邊都指住新 sha `b7e4d2e14b0580b6…`。
- 落眼覆核：[eb02-010-shipped-check.jpg](eb02-010-shipped-check.jpg) —— 乾淨單卡卡面，卡號 EB02-010 讀得出。

所以**冇按 PM 指示重換**（重換等於用同一個 CDN product 覆蓋自己，白做兼多一條 QC 記錄）。
PM 指定嘅 `641620` 正正就係已經用咗嗰個 product。

**下次寫 QC 報告要記住嘅事**：`docs/evidence/` 入面嘅 JSON 係凍結快照，唔係現況。
查現況要讀 `manifests/image-qc.json` 或者 `data/runtime/local-serve/snapshot.json`。

## 缺口表：21 張未處理

機讀版 [gap-table.json](gap-table.json)。下面係人讀版，逐張寫「試過咩」同「點解唔要」。

### v35 153/SV-P Pikachu —— 入完撤回（唯一一張自己收返手嘅）

本機 `snkrdunk_459741.webp` 圖質乾淨、幾何過閘，已經入庫。
落眼覆核嗰刻喺卡文見到**繁體中文**（「電磁電光」「對手的1隻寶可夢受到10點傷害」「五週年」），
而 DB `card_language` 係 `ja`<!--@verified 2026-07-27 id=img.v35.lang expect=ja ttl=90 sql=SELECT card_language FROM catalog_variant WHERE id = 35-->。

即係我原本會寫落 QC 記錄嘅 `languageMatch: true` 係假嘅。**撤回 QC 記錄**（421 → 420），
資產檔留喺磁碟（內容定址，冇人指住就冇害）。

**要一張日文版 153/SV-P 先填得返。** 唔准為咗填數而報假 languageMatch。

### v276 EB03-026 Boa Hancock —— PM 追加項，未處理

兩重卡死：

1. 三個 TCGplayer 候選**全部帶 SAMPLE 水印**（見 [pm-extra-candidates.jpg](pm-extra-candidates.jpg)）。
2. DB `card_language` 係 `ja`<!--@verified 2026-07-27 id=img.v276.lang expect=ja ttl=90 sql=SELECT card_language FROM catalog_variant WHERE id = 276-->，
   即係英文版圖就算乾淨都會踩 language mismatch，同 v35 一樣嘅坑。

呢張而家庫入面**有圖**（sha `84767b5c…`），但係帶水印嗰張 —— 屬「有圖但係爛圖」，
唔係缺圖。要日文版乾淨卡面先換得。

### 20 張 One Piece —— 三條路全部行完

逐張嘅 product id、量度數字、拒收理由喺 [gap-table.json](gap-table.json)。理由分四類：

| 理由代碼 | 幾張 | 意思 |
|---|---:|---|
| `sample_watermark_all` | 11 | 全部正確印次候選都有 Bandai 對角 SAMPLE 水印 |
| `wrong_printing_only` | 7 | 唯一無水印候選係另一個印次／另一套，唔係要嗰張 |
| `upscale_too_small` | 2 | 候選細過 429×600，放大會糊（v103 · v188） |

**印次識別基本上全部做到。** 20 張全部搵到對應 product id，而且同本機殼相嘅美術圖對過。
即係下一個 agent 唔使由零查 —— 缺口表已經帶住 exact product id 交落去。

## 三個結構性發現（呢啲先係真正值錢嘅嘢）

### 1. `altxyz_*` 呢個來源類別結構上出唔到卡面

22 張缺圖卡嘅本機 bytes 按 prefix 分：

| prefix | 張數 | 係咩 | 用得？ |
|---|---:|---|---|
| `snkrdunk_*` | 2 | 商城卡面圖 | ✅ 兩張都入得（v168 出街，v35 因語言撤回） |
| `altxyz_*` | 20 | 評級殼拍賣相（卡封喺 PSA/BGS 膠殼，連標籤） | ❌ 全部唔得 |

**1:1 對應，零例外。** 呢個唔係「本機圖質差」，係 `altxyz` 呢個來源**根本唔係卡面圖來源**。
入佢等於重複用戶已經欽點否決嘅 EB02-010 舊圖嗰個毛病。

⚠ 呢句係全域否定，2026-07-27 量（n=22，`missing22_work.json` 個 `localFile` 欄）。
重量方法：`python -X utf8 -c "import json;from pathlib import Path;print(sorted(set(c['localFile'].split('_')[0] for c in json.loads(Path('docs/evidence/2026-07-27-image-fix/missing22_work.json').read_text(encoding='utf-8')))))"`。
同期有其他 agent 動 G10 檔案樹，引用之前重量過。

### 2. TCGplayer 對英文 One Piece 卡供兩種圖，尺寸就係指紋

| 尺寸 | 係咩 | 水印 |
|---|---|---|
| `600x838` / `600x837` / `716x1000` | Bandai 官方樣圖模版 | **有**，中間一大個對角 SAMPLE |
| 不規則（`625x873` · `500x701` · `711x1000`） | 真實掃描 | 無 |

**呢個係預篩條件唔係證明。** 反例已經捉到：v103 個 `453506` 尺寸 `300x419`（不規則），
落眼睇仍然帶水印。所以篩出嚟嘅一律要人眼覆核。

實測數字：人手挑出嚟嘅正確印次候選 **36 個唯一 product（40 個位）**，
其中 35 個一睇尺寸就知係樣圖模版，剩返嗰個 `453506` 尺寸不規則但落眼睇一樣有水印
→ **36/36 有水印，零乾淨**。

放寬去掃全部 **110 個唯一 product**，尺寸啟發式篩出 14 個命中位（11 個唯一 product）「疑真掃描」，
**冇一個係正確印次** —— v19 嗰 4 個係完全無關嘅 Battle Spirits Saga 卡「Scorched Battlefield」，
v24/43/72/146 撞同一個 2nd Anniversary 促銷版，v60/188 係日文版促銷。

證據 [candidate-compare.jpg](candidate-compare.jpg)：本機殼相（美術圖參考）同每個候選並排，一行一張卡。

⚠ 「零乾淨」係全域否定。2026-07-27 量，重量方法（唔使連 DB，讀凍結副本）：

```bash
python -X utf8 -c "
import json
from pathlib import Path
D = Path('docs/evidence/2026-07-27-image-fix')
slots = [c for r in json.loads((D/'candidate_measurements.json').read_text(encoding='utf-8')) for c in r['candidates']]
SAMPLE = {'600x838','716x1000','600x837'}
uniq = {c['productId'] for c in slots}
print('正確印次唯一 product', len(uniq), '/ 尺寸屬樣圖模版', len({c['productId'] for c in slots if c.get('size') in SAMPLE}))
scan = json.loads((D/'clean_source_scan.json').read_text(encoding='utf-8'))
print('全掃唯一 product', len({f['productId'] for r in scan for f in r['all']}),
      '/ 疑真掃描命中位', sum(r['likelyRealScanCount'] for r in scan))"
```

### 3. `manifests/image-qc.json` 有 204 個重複 publicId（潛在缺陷，唔係我改嘅範圍）

624 條記錄，唯一 publicId 得 420 個 → **204 個 publicId 有多過一條記錄**<!--@verified 2026-07-27 id=img.qc.dupes expect>=204 ttl=3 cmd=python -X utf8 -c "import json,collections;from pathlib import Path;r=json.loads(Path('manifests/image-qc.json').read_text(encoding='utf-8'))['records'];print(len([k for k,v in collections.Counter(x['publicId'] for x in r).items() if v>1]))"-->。
例：v276 同時有一條 436×610 `native_card_bound_raw_front` 同一條 429×600 `daily_self_heal`。

**點解危險**：`load_public_images()` 掃記錄嗰陣**後蓋前**（最後一條贏），
而 `ingest_card_image.py` 個 `upsert_qc()` 只換**第一條** match。
即係喺一個有重複記錄嘅 publicId 上面換圖，**改動會俾後面嗰條靜靜咁遮住**，
而且冇任何錯誤訊息。

我四次寫入全部逐次確認過係單一記錄兼真係生效（見上面「點量」第 2 步），所以呢次冇中招。
但下一個做批量換圖嘅 agent 會中。**修法唔喺呢個 task 範圍，但值得開一項。**

## 檔案

| 檔 | 係咩 |
|---|---|
| [ingest_card_image.py](ingest_card_image.py) | 單卡入庫／換圖工具。預設 dry-run，`--write` 先真改 |
| [tcgplayer_lookup.py](tcgplayer_lookup.py) | 卡號 → TCGplayer productId 候選 |
| [fetch_candidates.py](fetch_candidates.py) | 下載候選、量度、砌人眼比對表 |
| [scan_clean_sources.py](scan_clean_sources.py) | 用尺寸做代理篩「疑真掃描」 |
| [build_gap_table.py](build_gap_table.py) | 砌 `gap-table.json`，sha 即場對返 QC manifest |
| [gap-table.json](gap-table.json) | 25 張卡逐張結局 + 拒收理由（機讀） |
| [missing22_work.json](missing22_work.json) · [tcgplayer_candidates.json](tcgplayer_candidates.json) · [candidate_measurements.json](candidate_measurements.json) · [clean_source_scan.json](clean_source_scan.json) | 凍結輸入，等 `build_gap_table.py` 重跑得返 |
| [shipped-verify.png](shipped-verify.png) | 三準則覆核圖（棋盤格底，無損 PNG） |
| [candidate-compare.jpg](candidate-compare.jpg) | 20 張 One Piece 候選 vs 本機殼相，水印證據 |
| [pm-extra-candidates.jpg](pm-extra-candidates.jpg) | v111 / v276 候選 |
| [eb02-010-shipped-check.jpg](eb02-010-shipped-check.jpg) | v8 出街圖現況（駁 PM 個「仍然係卡包」報告） |

## 點解要有 `ingest_card_image.py`（唔係重複造輪）

- [ensure_std_card_images.py](../../../pipelines/ensure_std_card_images.py) 係 snapshot 驅動嘅**全量自癒**，
  冇單卡模式，而且會掃全庫。
- [g10_asset_ingest.py](../../../pipelines/g10_asset_ingest.py) 硬綁 G10 檔案樹同 ebay/snkrdunk 身分，
  **入唔到外部 CDN 圖**。

兩個都做唔到「一張卡 + 一個任意來源 + 人手確認過」。幾何規格全部叫返
`native_image_resolver`，冇自己寫過縮放或者圓角。
