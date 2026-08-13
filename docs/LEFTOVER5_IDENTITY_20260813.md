# Leftover-5 身份裁決同方法論（2026-08-13）

> 操作命令仍然以 [COLLECTION_RUNBOOK.md](COLLECTION_RUNBOOK.md) 為準。
> Pin 只准寫喺 `pipelines/leftover5_go.py`；refresh lane 只准 call
> `hold_exact_against_refresh`（形狀 22）。
> Code 同呢份文件衝突時 **code 贏**，然後改文件。

DADDY 2026-08-13：identity backlog leftover 5 張。目標係綁正確價源、寫齊註解、
`leftover=0`、下游重跑、上 Live。中間試過用 heading 方括號、PC `VGPC.pop_data`、
同「缺 census 就降級」去否決正確頁 —— 全部係錯。下面係最後用得住嘅方法，同
每張卡嘅記錄。

三件分開報，唔好合成一句「做完」：

1. **identity exact**（`catalog_source_identity`）
2. **現役 PSA10 價**（price-route 只讀 `pricecharting` / `snkrdunk`）
3. **FE**（activate + universe/health + bake/push；未見到就未上）

---

## 方法論（下次遇到同樣問題跟呢度）

### 1. POP 先，名第二

卡入池係因為 GemRate PSA10 pop ≥ 1000。候選頁／listing 要先對 **POP**，再對名。

- 候選 POP 同 GemRate 差太遠 → **唔係同一張卡**。唔好為咗清 leftover 硬綁。
- PC 頁 `VGPC.pop_data` 可以滯後、可以壞、可以空白。空白 **≠** mismatch。
- 缺 PC census 嗰陣，去 **PSA CardFacts / eBay sold / GemRate 自己** 搵 POP，
  用嚟認**版本**，唔好當「證據唔夠就 hold」。
- `pop_close`（`pipelines/adjudicate_pc_pop_corroboration_20260813.py`）係
  自動 lane 用嘅尺：PC 可以滯後 `max(30, 15%)`，唔可以超前超過 10。
  leftover-5 GO pin **繞過**呢把尺，因為裁決已經用過外部 POP。

### 2. Catalog 獨一無二 → GO

Catalog 得一行、得一種語言，外部 PSA/eBay POP ≈ GemRate → 就係嗰張。
唔好因為 PC census 空白而降成 `manual_review`（v35、v1228）。

「點解會錯？」—— unique + pop 對得上，錯嘅機會唔喺身份，喺價源有冇現役成交。

### 3. EN / JP 孖生係兩張卡

唔同 pop = 唔同卡。綁 EN 去 EN 嘅 PC/TCG；JP twin 留低自己嗰條 SNK。
唔好偷 twin 嘅 SNK 去令 leftover 變 0。

| EN | pop | JP twin | pop | 唔好偷 |
|---|---|---|---|---|
| v1225 Stussy SP | 1576 | v1875 | 1247 | SNK 520534 |
| v1438 Shirahoshi SP | 1670 | v2167 | 1530 | SNK 520532 |

v1228 Lucky Roux TR 係 unique EN，**冇** JP twin。

### 4. PC 英文 OP11 reprint 標題經常唔寫 `[SP]` / `[TR]`

身份睇 **details 盒 TCGPlayer ID + 成交標題**，唔睇 SAMPLE 圖，亦唔單靠 heading
有冇方括號。

- SAMPLE 圖經常張冠李戴。
- EN slug 可以 redirect 去 JP console（v1225 → `8843990`）。嗰頁係 **另一個
  product id**，唔係「同一張卡嘅日文名」。唔好當 provider 事實就綁落 EN。
- Unbracketed EN 頁 `9362363` / `9362360` / `9967271` **就係** SP/TR 產品，因為
  TCGPlayer ID 分別係 `632506` / `632502` / `632773`，成交標題寫 SP / TR。
- 呢條 **唔推翻** `_pc_print_signature_ok` 對普通卡嘅規矩：unbracketed heading
  對 treated printing 仍然拒絕（2026-08-09 Yamato：base 頁綁咗 SAA，7 張錯綁）。
  leftover-5 係 pin，唔係放寬印記比較。

形狀 21 仍然成立：GemRate 寫包裝套（OP11），卡面印原套編號（OP07-085 等）。

### 5. TCGPlayer 只係身份旁證

`tcgplayer` exact 可以清 leftover 顯示，**唔作出價 lane**。price-route /
image-bind 忽略佢。Exact TCG ≠ 現役 PSA10 價 ≠ FE。

### 6. 中文可以用 PC 或者 SNK；日文仍然 SNK 優先

DADDY：中文卡兩邊都得，冇偏好。S8/S12 以前只接受 `card_language='en'` 嘅 PC
quote，所以 zhTW exact 仍然 `qualified_market_pending`。而家
`PC_PRICE_LANGUAGES`（`current_quote_revision.pc_price_language_ok`，casefold；
`zhtw` 同 `zhTW` 都得）。日文仍然 SNK-primary（v1874 Battle
Festa 教訓：PC EN/JP console 係唔同產品）。

### 7. Yellow Cheeks 家族：PSA 拆版本，catalog 得 unlimited

PSA 拆 1st / shadowless / unlimited / E3 / 4th / red-cheeks。036 catalog 只有
v1900（unlimited，pop 3104）。

- 1st Yellow Cheeks CardFacts pop **549** —— 另一張卡，唔好綁。
- PC `pikachu-58`（630471）`pop_data=5` 係 **欄位壞**。Live 成交標題係
  UNLIMITED YELLOW CHEEKS，外部 PSA/eBay ≈ 3079–3104。
- SNK 766125 名係 Yellow Cheeks；SKU 寫 1st 係店舖講大話。版本跟 POP，唔跟 SKU。
- 唔綁 shadowless / 1st / E3 / 1999-2000 頁。

### 8. 有 POP／版本證據就自己揀頁，唔好再問 DADDY 重裁決

搵到就答、就綁。只表面對唔上嘅真問題。

### 9. 後續 stage 唔准靜靜推翻 GO

`pc-replay` 會用 heading 印記把 unbracketed SP 打落 `manual_review`。
`snk-refresh` 會用 SKU/fingerprint 把 v1900 SNK 打落 `manual_review`。
保護只經 `leftover5_go.hold_exact_against_refresh`。其他 operator-knowledge
卡仍然要喺 snk-refresh **之後**再 `--write`（2026-08-13：38 張非 leftover-5）。

---

## 五張卡記錄

| vid | GemRate / catalog | pop | 語言 | GO | 拒絕 |
|---|---|---|---|---|---|
| **35** | zhTW 5th Anniversary Pikachu 153/SV-P | 5316 | zhTW | PC **7980813** exact（strict=1）。Unique。eBay PSA10 ~4851 ≈ 5316。 | SNK 459741 simplified [CN] |
| **1900** | 1999 Pokemon Game Pikachu Yellow Cheeks 58（`58/102`；catalog 冇 `psa_description` row） | 3104 | en | PC **630471** exact + SNK **766125**。Unlimited。 | 1st YC（pop 549）；shadowless/E3 |
| **1225** | EN Stussy SP OP07-085 packed in OP11 | 1576 | en | PC **9362363**（unbracketed Fist of Divine Speed；TCG **632506** = Stussy (SP)）。 | JP `[SP]` **8843990**；唔偷 v1875 SNK 520534。TCG 632506 exact 但 `strict_n=0`（唔係價 lane） |
| **1438** | EN Shirahoshi SP EB01-057 packed in OP11 | 1670 | en | PC **9362360**（TCG **632502** = Shirahoshi (SP)）。 | 唔偷 v2167 SNK 520532 |
| **1228** | EN Lucky Roux TR OP09-015 packed in OP11 | 1158 | en | PC **9967271**（TCG **632773** Lucky.Roux (TR)）。Unique EN，冇 JP twin。 | `[Foil]` **8091560**（原套 OP09 foil）。SNK 364046 係 base R → `manual_review` |

`canonical_name` 同 GemRate 係同一張卡；catalog 有時會加印面編號。v1900 缺
acceptance row 唔阻 identity GO。

註解同 heading token 清單：`pipelines/adjudicate_operator_knowledge_20260813.py`
（搜 `leftover-5`）。Pin：**只** `pipelines/leftover5_go.py`。

### 點樣對到呢五頁（重做時跟呢個次序）

1. 讀 GemRate pop + 語言 + 印面編號。
2. 睇 catalog 有冇 twin（同一印面、另一語言、另一 pop）。
3. 外部 POP（PSA/eBay）認版本。PC census 對唔上就當欄位，唔當另一張卡。
4. PC：打開產品頁，抄 **PriceCharting ID** 同 **TCGPlayer ID**，再掃成交標題。
   SAMPLE 圖可以錯；heading 可以冇方括號。
5. EN slug 若跳去另一個 PC id，當兩個 console，逐個對語言同 TCG ID。
6. SNK：標題／圖認卡；SKU 可以講大話（v1900）。Twin 已有 SNK 就唔偷。
7. 寫 pin + 註解 + `--write`。然後先保護 refresh，先至跑 S7/S6。

---

## 曾經犯錯（唔好再做）

1. **用本地 heading parser 否決 DADDY 畀嘅 URL。** 應該上網對 TCGPlayer ID 同成交。
2. **用 PC `pop_data=5` 否決 630471。** Census 壞；成交同外部 POP 先係版本。
3. **unique 中文卡因為缺 PC census 就降級。** Unique + 外部 POP ≈ GemRate → GO。
4. **當 leftover=0 / exact bind = 已上 FE。** 三件分開報。
5. **綁 JP `[SP]` 8843990 去 EN Stussy。** Redirect ≠ 同一 console。
6. **放寬 `_pc_print_signature_ok` 令 unbracketed 頁過 SP。** 會重開 Yamato 洞。
   用 pin，唔改印記尺。
7. **snk-refresh 之後冇再 `--write` operator-knowledge。** leftover-5 而家有 pin
   擋；其餘 38 張仍然要 refresh 後再寫。
8. **S8 讀 replay 目錄，唔讀 `full900`。** 爬咗 HTML 但冇入 `replay-<generation>`
   = 有綁定冇價（形狀 31）。
9. **activate 喺 `identity-resolve` / `bind` / `pc-replay` / `prune-plan` pending
   時照跑。** 會 ABORT。`--invalidate-from` 之後一定要行完 LINEAR_STAGES。
10. **未 Enable 返 freeze 前 Ready 嘅 Task Scheduler。** 夜鏈 03:30 靜靜死。
11. **S8 寫咗 local-history observation ≠ 有 live quote revision。** ranking view
    排除 `legacy_generation_reconstructed`；bootstrap 以前只收 `manualonly.last`。
    leftover-5 有 10–67 個 series 點仍然 S12 `acceptance_present_but_view_rejected`。
12. **`LOWER(card_language)` 之後用大小寫敏感 set 去 route。** S8 將 zhTW 變成
    `zhtw`，`PC_PRICE_LANGUAGES` 淨係寫 `zhTW` → v35 有 19 個 PC 點仍然
    `route=none`。`pc_price_language_ok` 一定要 casefold。
13. **Python writer 改咗語言閘、043 ranking view 冇改（形狀 22）。** S12 讀 view。
    而家 `eligible_current_quote_revision_ddl()` 係權威，activate 每次 re-apply。

---

## 上 Live 要行嘅鏈（摘要）

Freeze 生命週期同 gap intake 命令：runbook §5。Leftover-5 額外：

1. Code pin 已經喺 `pc-replay` / `snk-refresh`（先 push 呢份 code，先至跑 stage）。
2. Freeze → dump（`restore-proof.freezeOpenedAt` == 而家 window）。
3. 如 checkpoint 仍 pending `identity-resolve`… 就要行完先至 activate。
4. `snk-refresh` → operator-knowledge `--write` → 必要時 `pc_cache_replay` 將
   leftover HTML 抄入 replay 目錄 → `price-materialize` → `image-bind` →
   `validate` → `activate`。
5. `unfreeze --confirm` + Enable 返 Ready 嘅 task。
6. bake + `scripts/daily_public_release.ps1`。Live health 嘅 `generatedAt` 要
   對得返呢次 bake。WSL release repo **只睇 `origin/main`**，code 未 push =
   出街仲係舊腳本。
