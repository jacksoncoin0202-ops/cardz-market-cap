# Identity 人類搜尋 Playbook（SNK／eBay／PC）

> **PMO 強制**：所有擴 SNK／eBay id 嘅 agent **必讀本檔**。  
> 唔係淨跑腳本名；係用 **人類會點 search** 嘅多鍵查詢，再過腳本 `verify_pair`。  
> 召回可闊；**入庫前 verify 硬閘**（見 [RECALL_VERIFY_OPS.md](RECALL_VERIFY_OPS.md)）。

---

## 0. 一句心智

```text
一張卡 ≠ 一個字串
一張卡 = 變種 × 語言名 × 編號多寫法 × set 提示 ×（可選）grade
```

搜尋時 **叉積展開** 多 query；命中後 **逐個 verify**，唔好 first-hit 寫 DB。

### 0.1 三層方法論（用戶 2026-07-29 確認 · 突破口）

| 層 | 名 | 做咩 |
|---|---|---|
| **1. 自動化** | Automation | 腳本／Agent 開 browser、過 **CF**、換 **cookie**，先打通搜尋管線（唔好死喺 403） |
| **2. 搵存量** | Stock | 已有 harvest／DB／registry 上做 **變種 exact**、人類 query ladder、rehome |
| **3. 搵增量** | Increment | 存量榨乾後 **discover 新 id → harvest → merge → match → trades** |

口訣：**自動化 → 存量 → 增量**；有增長先加大劑量，無增長唔空跑 match。

---

## 1. 變種（printing / parallel）— 必分開想

| 維度 | 例 | 搜尋／綁定注意 |
|---|---|---|
| Base vs AA vs SAR vs SR | 同號唔同面 | SNK **唔同 apparel id**；唔好用 base 圖／價頂 AA |
| Reverse Foil / Holo / Mirror | PTCG EN 常見 | eBay／PC 標題常寫 `Reverse Holo`；漏咗會綁錯 listing |
| Comic / Manga / Wanted / SEC-SP | OP | **必須 comic exact id**（見 FILL_LOOP_LESSONS）；base `OP11-118` ≠ comic |
| Promo / Stamped / Staff | 特別標 | 標題 token 要齊，否則 verify_species 過但 printing 錯 |
| JP vs EN printing | 同角色唔同 set | **唔共用**同一個 SNK id（PK 一 id 一 variant） |

**Agent 動作：**  
對每張 watchlist 卡先判斷 `parallel_hint`（名／set／gemrate description）。  
Query 要帶 parallel 關鍵字；命中後若 source 標題無該 parallel → **拒**。

---

## 2. 名：日文 · 英文 · 別名

### 2.1 一定試齊

| 層 | 來源 | 例 |
|---|---|---|
| EN display | GemRate / catalog `card_name` | `Charizard ex` |
| JP | locale / SNK 標題 / 知識 | `リザードンex` |
| 角色 core | 去 rarity 後 species token | `Charizard` / `リザードン` / `Luffy` / `ルフィ` |
| 全名 vs 短名 | | `Monkey D. Luffy` ↔ `Luffy` ↔ `モンキー・D・ルフィ` |
| 符號正規化 | | `é`→`e`、全形→半形、`・`↔空白、`'` 有無 |

### 2.2 物種 token 硬規則（已寫喺腳本）

- **全字 token**：`mew` **唔可** hit `mewtwo`
- Stage：`VMAX` / `VSTAR` / `GX` / `ex` / `V` 要一致（錯 stage = 拒）
- 非單卡：`sleeve` / `playmat` / `box` / `booster` → 拒

### 2.3 SNK 搜尋 query 模板（人類會打）

```text
{JP_species}
{JP_species} {collector_compact}
{EN_species} {collector}
{EN_species} PSA10
{JP_species} PSA10
{EN_species} {set_code}
ワンピース {collector}          # OP
ポケモン {collector}
{comic|コミパラ|manga} {species}  # OP special
```

Discover keywords **唔好淨係 game 名**；要 **角色 + 編號 + set 代號** 三層都有。

---

## 3. 編號（collector）— 最大盲點

人類同網站寫法極亂。**每張卡要展開多 form** 先 recall：

### 3.1 標準展開（OP 例 `OP01-016`）

| Form | 例 |
|---|---|
| 原樣 | `OP01-016` |
| 小寫 | `op01-016` |
| 去 hyphen | `OP01016` |
| 空白分隔 | `OP01 016` |
| 只數字段 | `016` / `16`（**純數字必須配 set hint**） |
| 去 leading zero | `OP01-16` |
| 斜線分母 | `123/187` → 試 `123`、`123/187`、`123-187` |
| 子集字母 | `GG70`、`SV49`、`TG20`、`SWSH012` |
| 全形數字 | `０１６` → 半形 `016` |
| 空白／NBSP | trim 所有 Unicode space |
| 大小寫混 | `Op01-016` |

腳本已有 `collector_forms()`——agent **手搜／API 搜時要同樣展開**，唔好只打 DB 原字串。

### 3.2 PTCG 例 `215/203` 或 `GG70`

| 試 | |
|---|---|
| 連分母 | `215/203` |
| 只分子 | `215` + set code `SV` / `151` |
| 無 slash | `215-203`、`215 203` |
| 字母號 | `GG70` 全字；亦試 `GG 70` |

### 3.3 驗證

- collector **必須** hit（verify 硬閘）
- **純短數字 alone** → 必須 set hint 中，否則 `verify_col_too_short` / 拒
- 歧義雙 cand 近分 → **拒**，記 needsReview，唔估

---

## 4. Set 提示（日英雙語）

| Watchlist set（例） | 應試 hint |
|---|---|
| Scarlet & Violet 151 | `151`、`sv`、`ポケモンカード151`、`強化拡張パック` |
| Evolving Skies | `evolving`、`s7R`、`蒼空ストリーム` |
| One Piece Romance Dawn | `OP01`、`ROMANCE`、`ロマンスドーン` |
| Carrying On His Will | `OP13`、`受け継がれる意志` |

**EN set 名 ↔ JP 包名** 對唔上 = 大量 `verify_set` reject。  
Agent 應維護／使用 alias（見 ceiling C07），搜尋時 **EN+JP set 都入 query**。

---

## 5. 推薦搜尋次序（每張無 id 卡）

```text
1) 變種分類（base / AA / comic / reverse…）
2) 展開 collector_forms（§3）
3) 展開名：EN full → EN species → JP species → JP full
4) 組 6–20 條 query（名×號×set×parallel 組合，由嚴到鬆）
5) RECALL 合併候補（可低分）
6) 逐候補 VERIFY：
     species token + collector + set hint + stage + parallel + 非單卡
7) 只 1 個高分通過 → 寫 identity
   0 個 → 記 no_source_reason
   ≥2 歧義 → needsReview，唔寫
8) mark ledger + preferred script
```

### 由嚴到鬆（query ladder）

1. `{JP} {OP01-016} {parallel}`  
2. `{EN} {OP01-016}`  
3. `{species} {OP01016}`  
4. `{species} {016}` + set code  
5. collector-only（**僅 OP 完整 code**）  
6. **停止**：唔好只 `016` 無 set 就 bind  

---

## 6. SNK vs eBay／PC 差異

| | SNK | eBay（G10 altxyz / PC） |
|---|---|---|
| ID 形態 | 數字 apparel id | UUID 或 listing |
| 搜尋語言 | **JP 優先** 再 EN | **EN 標題** 為主 |
| Grade | PSA10 condition 參數 | 標題 `PSA 10` / `PSA10` |
| 變種 | 商品名常日文 parallel | `Alt Art`、`Reverse Holo`、`Illustration Rare` |
| 禁 | 弱分 bind 搶 PK | invent UUID；PX 直爬 sold |

---

## 7. Agent 交付格式（強制）

每張嘗試過嘅卡，ledger／jsonl 至少：

```json
{
  "variant_id": 0,
  "queries_tried": ["リザードン 215/203", "Charizard 215/203", "..."],
  "collector_forms": ["215/203", "215", "215-203"],
  "parallel_hint": "base|aa|comic|reverse|unknown",
  "candidates": 3,
  "verify_pass": 1,
  "external_id": "...",
  "reject_reasons": [],
  "no_source_reason": null
}
```

**禁止：** 只報「跑完 match-snk written=0」而無 queries_tried。  
**禁止：** 為抬 KPI 用 first-hit。

---

## 8. 同現有腳本關係

| 能力 | 腳本 | 人類 playbook 補位 |
|---|---|---|
| collector 展開 | `collector_forms()` | discover keyword 都要同樣展開 |
| species | `species_token` / `species_hit` | JP 名要自己補 query |
| set | `set_hints` + alias | C07 擴 alias；搜尋帶 JP 包名 |
| 入庫閘 | `verify_pair` | **唯一** 可寫 DB 閘 |
| 清錯 | `clean` | 改規則後重跑 clean |

有 playbook 新 form 而腳本未覆蓋 → **先改腳本 + 測試**，再 bulk write。

---

## 9. PMO 一句

> 打開 ID 天花板 = **多變種 × 多語言名 × 多編號寫法** 去搜，  
> 唔係多開幾個 agent 空跑 `match-snk`。  
> 搜尋要似人類；裁決要似腳本。
