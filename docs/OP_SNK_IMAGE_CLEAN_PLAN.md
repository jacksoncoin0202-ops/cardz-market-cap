# One Piece 圖清 SAMPLE / SNK 全量 Plan

> **目標：** OP 圖整齊、乾淨、可出街——**最緊要係圖**。  
> **日期：** 2026-07-29 · 基於 SNK W1–3 完結後即場 probe  
> **相關：** [SNK_IMAGE_PIPELINE.md](SNK_IMAGE_PIPELINE.md) · [CARD_SOURCING_HANDBOOK.md](CARD_SOURCING_HANDBOOK.md) · `pipelines/snk_image_ingest.py` · `pipelines/op_limitless_images.py`

---

## 0. 一句結論

| 問題 | 答案 |
|---|---|
| SNK 咁成功，點解 OP 仲有 SAMPLE 感覺？ | **OP watchlist 磁碟層 SAMPLE 已 0**；多數卡已有 SNK 圖。用戶見到嘅 SAMPLE 大機會係 **FE snapshot 未 rebuild**、或 **watchlist 外 catalog**、或 **錯印次／舊 pointer 殘影**——唔係 SNK 搞唔掂 OP。 |
| 可唔可以再用 SNK 再清一輪？ | **可以、應該**——但主戰場唔再係「整池 SAMPLE 重跑」，而係 **缺 snk id、錯綁、FE 出街、catalog 擴**。 |
| 係咪名寫太嚴命中唔到？ | **OP 唔係主因。** OP collector（`OP05-119`）對 SNK title 極準；W3 residual 幾乎 0。真正卡死係 **identity 錯 printing**（例 Newgate ST15 vs SNK OP12）同 **未 bind snk id**。 |

---

## 1. 即場實測（2026-07-29 post W1–3）

### 1.1 OP watchlist（`tcg_code=one-piece` ∩ GemRate watchlist）

| 指標 | 數 | 解讀 |
|---:|---:|---|
| OP watchlist 卡 | **145** | 上池 OP 宇宙 |
| 有 snk id | **137** | 94% 已 bind |
| 無 snk id | **8** | 要 identity 先 |
| 主圖源 = SNK CDN | **137** | W3 prefer 已覆蓋大部分 |
| 主圖源 = Limitless | **8** | 幾乎 = 無 snk 嗰批 |
| `public_allowed=1` | **145** | 後台全放行 |
| `qc_version=snk-image-v1` | **134** | 今次 SNK 流水寫入 |
| **磁碟 SAMPLE OCR hit** | **0 / 掃過嘅檔** | 重災區喺 **DB 現圖** 已清 |

**有 snk id 但仍非 SNK 主圖：** 實測只剩 **1** 張  
→ Edward Newgate `ST15-002` · snk `599072` · master 實際係 **OP12-002** → **title gate 正確 reject**（唔係太嚴，係 **identity 可能錯綁**）。

### 1.2 Catalog 全量 OP（不止 watchlist）

| 指標 | 數 |
|---:|---:|
| catalog `one-piece` variant | **340** |
| 其中有 snk identity | **193** |
| 未 bind snk | **~147** |

→ **池外 / 未來入池** 仍有大缺口；今日 watchlist 清完 ≠ 全 catalog 圖齊。

### 1.3 點解「感覺」同「DB」唔同

```text
DB raw_front 已 SNK / Limitless clean
        │
        ▼ 缺這步就會以為仲有 SAMPLE
canonical_public_snapshot / FE seed / R2 pointer 未用新 hash
        │
        ▼
用戶瀏覽器 / staging 仍見舊 TCGplayer SAMPLE
```

歷史背景：OP SAMPLE 真係 TCGplayer 重災；2026-07-29 FE 曾用 Limitless 清 0 hit。之後 SNK W3 再換一輪乾淨圖，**若未 snapshot 上 FE，畫面上仍似舊災區**。

---

## 2. 名太嚴？——拆解

### 2.1 而家 title gate 點做

`snk_image_ingest.identity_title_ok`：

1. **優先 exact collector**（`OP11-080` / `ST01-007`）出現喺 SNK master 名 → **過**  
2. 否則 number + species  
3. species 有、number 弱 → `needs_review`  
4. 全無 → `title_mismatch`

### 2.2 OP 實務

| 情況 | 嚴唔嚴 | 後果 |
|---|---|---|
| 正常 `OP##-###` / `ST##-###` | **唔嚴**（exact substring） | W3 OP 幾乎全 written |
| `Monkey.D.Luffy` vs `Monkey D. Luffy` | 中 | 多數靠 **collector** 過，唔靠名 |
| `Edward.Newgate` vs catalog `Edward Newgate` | 中 | 同樣靠 collector；**Newgate 係 set 號唔同** |
| catalog `ST15-002` · SNK 寫 `OP12-002` | **正確 fail-closed** | 防錯卡面入庫 |
| `Lacey` 231 vs PRE 175/131 | 正確 needs_review | 非 OP |

### 2.3 真正要鬆／要加嘅（半自動）

**唔建議** 為抬命中率而放寬到「只 match 角色名」——OP 同名 Nami/Luffy 極多，會入錯 alt/SEC。

**建議加（OP-specific normalize，仍 fail-closed）：**

| 規則 | 做法 |
|---|---|
| N1 | collector 正規化：`OP05 119` / `OP05-119` / `op05-119` 統一 |
| N2 | 名正規化：去 `.`、連續空白、全形半形；`Monkey.D.Luffy` ≡ `Monkey D Luffy` |
| N3 | SNK title 抽 `[OP12-002]` / `(OP12-002)` 做 **primary key** 對 catalog collector |
| N4 | 若 SNK collector ≠ catalog collector → **唔寫圖**；入 `identity_review`（疑錯綁） |
| N5 | Parallel / SEC-SP / 漫畫共通 base 號 → 必須 snk id exact，禁止 first-hit 名搜 |

**結論：** 而家卡 OP 嘅唔係「名太嚴」，係 **(a) 未有 snk id (b) 有 id 但 printing 唔對 (c) FE 未食新圖**。

---

## 3. 目標狀態（Definition of Done）

每張要出街嘅 OP 卡：

| 層 | 要求 |
|---|---|
| **A** | `market_image_asset` raw_front 429×600 |
| **B** | public + web `market-assets/{sha}.webp` 存在 |
| **C** | `public_allowed=1` · SAMPLE OCR 0 |
| **源** | preferred = **Limitless `_EN`** 或 **SNK `upload_bg_removed`**（兩者皆可；**禁** TCGplayer SAMPLE） |
| **地圖** | `source_pointer` 記清楚 CDN URL 或 `snkrdunk:{id}:url` |
| **FE** | snapshot rebuild 後 SAMPLE re-scan **0** |

優先序（產品）：

```text
1) 無 SAMPLE / 錯卡          ← 硬
2) 有 snk id 用 SNK 乾淨圖   ← 你而家想要
3) 無 snk → Limitless EN     ← 已有腳本
4) 仍缺 → G10 樹 / Drive     ← 殘渣
```

---

## 4. 執行波次（半自動 · 腳本 + AI residual）

### Wave OP-0 · 量度閘（先做 5 分）

```powershell
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap
$env:CARDZ_DB_HOST="127.0.0.1"
# 輸出：OP watchlist / catalog snk 覆蓋、SAMPLE scan、needs list
python -X utf8 temp\probe_op_sample_scan.py   # 或升級成 pipelines/op_image_status.py
```

驗收：數字落地；SAMPLE hits 表；缺 snk id 名單。

---

### Wave OP-1 · 有 snk id → 強制 SNK 圖（再清一輪）

**範圍：** `tcg_code=one-piece` 且 `catalog_source_identity snkrdunk`  
**含：** watchlist 全量 + 可選 catalog 全 193

```powershell
# 新 flag 建議：--tcg one-piece --prefer-snk --force --write
python -X utf8 pipelines\snk_image_ingest.py --prefer-snk --force --write --limit 2000
# 或只 OP：加 --tcg one-piece（要補 CLI）
```

**QC 硬閘（不變）：** SAMPLE 拒 · collector 對 SNK title（N3 優先）· 429×600 · A/B/C

**AI residual：** title_mismatch / needs_review → 人眼對 [snkrdunk trading-cards/{id}](https://snkrdunk.com/en/trading-cards/) · 錯綁就 clean identity，唔硬寫圖。

---

### Wave OP-2 · 無 snk id → 先 bind 再拉圖

**8 張 watchlist + catalog ~147**

| 步驟 | 工具 |
|---|---|
| 2a identity | `semi_auto_identity` / `bind_snk_watchlist` + [IDENTITY_HUMAN_SEARCH_PLAYBOOK](IDENTITY_HUMAN_SEARCH_PLAYBOOK.md) |
| 2b ladder | 日英名 × `OP##-###` 多寫法 × Parallel/SEC/SP |
| 2c 過 verify_pair | 先 identity 表，先圖 |
| 2d 圖 | `snk_image_ingest --snk-id … --write` |

**已知殘渣模式：** 週年/Errata/同 collector 多 printing——**必須 exact snk id**。

---

### Wave OP-3 · Limitless 補洞（無 snk 或 SNK 404）

```powershell
python -X utf8 pipelines\op_limitless_images.py --write --only-missing
# 可加：--force-replace-sample 若再發現 SAMPLE hash
```

Limitless = EN clean 秒級；**唔用** 其 raw 價入市值。

---

### Wave OP-4 · SAMPLE 全量 re-scan + 強制換源

```text
對 OP watchlist（後可擴 FE cut / catalog）：
  讀 content_sha256 → sample_image_qc
  hit → 記紅名單 → SNK（有 id）或 Limitless 覆寫 → 新 hash
  re-scan 必須 0
```

腳本可合：`pipelines/op_sample_rescan_replace.py`（未有就 semi-auto：status 腳本 + ingest）。

---

### Wave OP-5 · FE 出街（用戶先睇到「整齊」）

```powershell
# 依 repo 現行 publish 鏈（backend snapshot / canonical_public_snapshot）
# 重點：新 sha 入 snapshot；staging 開瀏覽器驗 OP 榜
```

驗收：

1. OP 詳情頁／榜 **無 SAMPLE 水印**（肉眼 + OCR）  
2. 抽 10 張高市值 OP（Luffy SEC / Nami OP01-016 等）對 SNK 或 Limitless  
3. `scan_sample_*` hits = **0**

**冇 Wave 5 = 後台齊、用戶仍覺得重災。**

---

## 5. 名／identity 改善（同圖一齊做）

| ID | 改動 | 優先 |
|---|---|---|
| G1 | title gate 加 OP collector 抽取（N3） | P0 |
| G2 | 名 normalize 去點（N2）— 只辅助，唔取代 collector | P1 |
| G3 | reject 時若 SNK collector ≠ catalog → 自動開 identity review | P0 |
| G4 | Newgate 類：ST15 vs OP12 → clean 或 re-bind 正確 snk | P0 人手 1 張 |
| G5 | `snk_image_ingest --tcg one-piece` 過濾 | P1 |
| G6 | pointer 多行混亂 → 寫入時 upsert 單一 preferred raw_front | P1 |

---

## 6. 風險

| 風險 | 緩解 |
|---|---|
| SNK JP 面 vs Limitless EN 面混榜 | 產品可接受則 OK；要統一語言就 mark preferred 源 |
| 同 collector 多 art（SEC/SP/comic） | 禁止 first-hit；exact snk id |
| 放寬名 match 入錯卡 | **唔做** 純名過閘 |
| 覆寫好圖 | content-hash 新檔；舊 hash 可留碟 |
| FE 未更新 | Wave 5 強制 checklist |

---

## 7. 預估工時（半自動）

| Wave | 工時 | 依賴 |
|---|---|---|
| OP-0 量度 | 10 分 | — |
| OP-1 SNK 再掃 OP | 30–60 分 | snk id 已有 |
| OP-2 identity 缺口 | 1–3 小時（8+147 深淺不一） | 人 ladder |
| OP-3 Limitless | 10–20 分 | — |
| OP-4 SAMPLE re-scan | 20–40 分 | — |
| OP-5 FE snapshot | 30 分 + 你 QA | 你確認上 staging |

Watchlist **再清一輪**可以好快；**catalog 全 340 齊圖** 受 snk bind 速度限制。

---

## 8. 建議執行序（你一聲就跑）

```text
A. 先 Wave OP-0 + OP-5 診斷：
   若 FE 仍 SAMPLE → 優先 rebuild snapshot（可能後台已齊）
B. 同步 Wave OP-1（OP snk 強制圖）+ OP-4 re-scan
C. Wave OP-2 半自動 bind 8 張 watchlist 缺口
D. catalog 擴充另開里程碑（340 全齊）
```

**PM 原則（同 SNK 圖）：**  
Harvest 可亂 · **入庫前 QC** · 報 **Harvest｜DB｜驗證綠** · residual AI/人 fact-check · 成功即寫 `source_pointer` 地圖。

---

## 9. 檔案地圖

| 檔 | 角色 |
|---|---|
| `pipelines/snk_image_ingest.py` | SNK 圖主線（要加 `--tcg`） |
| `pipelines/snk_image_semi_auto.py` | 多波 orchestrator |
| `pipelines/op_limitless_images.py` | Limitless EN |
| `pipelines/sample_image_qc.py` | SAMPLE 硬閘 |
| `temp/op_sample_scan.json` | 今次 probe 輸出 |
| `temp/op_snk_image_probe.json` | 覆蓋 probe |
| 本檔 | Plan 權威 |

---

## 10. 變更

| 日期 | 內容 |
|---|---|
| 2026-07-29 | 首版：post SNK W1–3 OP 實測；否定「名太嚴主因」；五波 OP 清圖 + FE |
