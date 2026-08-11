# HANDOFF — 後端／資料側（2026-08-11）

> **內部文件，唔准出街。**
> 接手人：**Codex**（後端／pipeline／infra）。前端嗰份見 [HANDOFF_GEO_FE_20260811.md](HANDOFF_GEO_FE_20260811.md)。
> 呢份文件**唔列供應商名**——要知邊幾個字串，自己喺 DB 查。呢份文件本身將來可能會俾唔應該睇到嘅人睇到，所以唔寫死。

---

## 執行紀錄 — 2026-08-11（Wave 0/1/2 已做，本機驗完，未 commit 未 deploy）

Owner 2026-08-11 改咗指派：呢份嘢由寫文件嗰個人自己做。以下係實際做咗嘅嘢，全部本機驗過。

| 波 | 做咗咩 | 證據 |
|---|---|---|
| 0 | 量度（零寫入） | 生產 sitemap／首頁／`/api/v1/market` 三個前綴 id 真係出咗街；供應商字串喺生產全部係 `null`（所以第 3 項係死 field，唔係泄漏）；DB 1605 行 variant，4 行帶前綴 |
| 1 | 刪 `priceAnchorSource`（3 個發射點 + 2 個 type）→ 重 bake | 重 bake 前後逐張卡 hash 一樣，唯一分別係少咗嗰條 key 同 `generatedAt`；出街 artifact 由 7,932 個 key 跌到 0 |
| 1 | `db_runtime.upsert_variant` INSERT 前擋非 `cmc_` id（只擋新 row，舊 4 行照 UPDATE） | `scripts/test_opaque_id_shape.py` 15 checks |
| 1 | 出街閘搬去 `scripts/public-surface-gate.mjs`，bake 一定行 | `scripts/test-public-surface-gate.mjs` 20 checks |
| 2 | migration 041 `public_card_alias` + `pipelines/public_card_alias.py` 鑄 4 個乾淨公開 id；投影層 `COALESCE(alias.public_id, variant.opaque_id)` | DB CHECK / FK 兩條都即場證過會炸 |
| 2 | 舊 URL 308（`/card/*` + `/api/v1/cards/*`）喺 `next.config.ts` | 本機實測 308 + Location 正確；`scripts/test-legacy-card-redirects.mjs` 22 checks |
| 3 | `compose.yaml` 加 `CARDZ_ENVIRONMENT`；robots 政策補 test；刪死配置 `public/_headers` | `scripts/test-robots-policy.mjs` 15 checks |

**本機實測（localhost:3800，live-db 模式）**：sitemap 1326 條 `<loc>`、首頁、`/api/v1/market`、卡頁四個面，供應商 token 同前綴 id 全部 **0**。

**閘嘅 ratchet 真係郁過**：alias 落咗之後重 bake，個閘自己出提示話「0/3 舊 id 仲喺度」，於是 allowlist 收到零 —— 呢個就係佢應該有嘅行為。

**未做（要 owner 決定或者唔屬工程題）**：第 5 節嘅文案門檻（5 筆／3 筆／0 筆點講）、robots blocklist 補唔補、canonical host 揀 apex 定 `app.`、ISR/`no-store`、OG 圖搬出 `/api/`。

**未 commit、未 deploy。** `data/public/seed-snapshot.json`（68MB）已經重 bake，asset prune 冇跑（`--no-prune`）。

---

## 0. TL;DR — 五件事，按嚴重度

| # | 事 | 嚴重度 | 狀態 |
|---|---|---|---|
| 1 | **3 個公開卡片 URL 嘅 id 帶住供應商代號前綴**，其中 1 個已經入咗 sitemap（連 6 個 hreflang alternate） | 🔴 已經出街 | ✅ 修咗（alias + 308，未 deploy） |
| 2 | **公開面泄漏 gate 冇咗** —— `scripts/canary-public.mjs` 2026-08-07 被刪，今日零自動檢查 | 🔴 冇防護 | ✅ 修咗（producer 側 ratchet + test） |
| 3 | **`data/public/seed-snapshot.json` 仲有 ~7,576 個供應商字串**；前端而家硬 null 走佢——即係「前端幫後端擋」 | 🟠 實測全部 `null`，係死 field | ✅ 刪咗 + 重 bake |
| 4 | **出街文案講嘅門檻同 code 唔一樣**：文案 5 筆／30 日，code 3 筆，而每日跑嗰條線係 **0 筆**（走指引價） | 🟠 事實準確度 | ⏸ 等 owner 決定 |
| 5 | robots / sitemap / cache 幾個 infra 缺口（staging 會被索引、ISR 失效、canonical host 未定） | 🟡 | 🟡 做咗 3 件，其餘要決定 |

**冇一件係前端可以自己修。** 前端今次做嘅嘢係喺投影層擋住 1 同 3 嘅可見部分，唔係修根因。

---

## 0.5 執行順序 —— 唔好一次過做晒

睇落十件事，但**真正必須做嘅得三件，其餘全部可以排隊**。分四波，每一波做完都係一個可以停低嘅穩定狀態。

### Wave 0 — 先量度，唔改任何嘢（半日內，零風險）

**呢一波唔寫 code。** 目的係決定 Wave 2 到底係 P0 定 P2 —— 而家所有「已經出街」嘅講法都係基於本機 build artifact，未驗過生產。

| # | 做咩 | 答到咩 |
|---|---|---|
| 1 | `SELECT opaque_id FROM catalog_variant WHERE opaque_id NOT LIKE 'cmc\_%'` | blast radius 到底 3 個定 300 個 |
| 2 | curl 生產 `/sitemap.xml`，grep 前綴 | 真係出咗街定只係本機 |
| 3 | curl 生產 `/api/v1/market` + 一個 `/api/v1/cards/{id}` | JSON API 有冇漏 source code |
| 4 | curl 生產 `/api/health` | 行緊 baked 定 live-db、邊個 generation |
| 5 | `SELECT DISTINCT source_code FROM market_price_observation` | 完整值域（我只見到 bake 出嚟 6 個） |
| 6 | 查嗰三個 variant_id 喺 `catalog_source_identity` / `market_ingest_run` 嘅 provenance | 邊個 writer 鑄嘅 |

**如果 (2) 同 (3) 都乾淨** → 第 2 節即刻由 🔴 降做 🟡，Wave 2 可以慢慢計劃。
**如果有嘢** → 照 Wave 2 做，而且要當已經俾人爬過。

### Wave 1 — 封窿（1 日，低風險，唔使掂已存在嘅資料）

呢一波唔碰任何舊資料，只係令**新錯誤唔會再發生**。做完之後前端就唔再係唯一防線。

| # | 改咩 | 規模 |
|---|---|---|
| 1 | `db_runtime.upsert_variant` INSERT 前 assert `^cmc_[0-9a-f]{20,24}$`，fail-closed | ~5 行 |
| 2 | `scripts/bake-public-snapshot.mjs` 加 id 格式 assert（或 wire 返 `isOpaquePublicId`），bake 唔過就唔出 | ~10 行 |
| 3 | 刪 `priceAnchorSource` 三個發射點 + 兩個 type，重 bake（第 4 節 5 步） | ~5 行 + 一次 bake |
| 4 | 落 assert 之後**即場證明佢會 fire**（餵個壞 id 落去睇佢 raise） | 5 分鐘 |

> (4) 唔准慳。`isOpaquePublicId` 同 `PRIVATE_TOKENS` 就係「寫咗但冇 call site」嘅活教材——加多個死 assert 只係令下一個人更加信錯。

### Wave 2 — 收拾已經出街嘅（要你決策，1–2 日）

#### 建議：**唔好 rename，起 alias 層**

文件第 2 節列咗 remap 要掂嘅嘢——兩個 stored hash 要重算、要撞 D7 凍結政策、要起 redirect。**呢啲全部可以避開。**

做法：**`catalog_variant.opaque_id` 一個字都唔變**（D7 完好無缺），另起一張 `public_card_alias(opaque_id, public_id)`，公開投影層（`live-db-snapshot.ts` + bake）出 `public_id`。冇 alias 就出 `opaque_id`。

| | rename 方案 | alias 方案 |
|---|---|---|
| 掂 `opaque_id` | ✅ 要（違反 D7，要 receipted 例外） | ❌ 唔使 |
| 重算 `bind_evidence_json` + `evidence_sha256` | ✅ 要 | ❌ 唔使 |
| 重算 `provenance_json.sourceRowSha256` | ✅ 要 | ❌ 唔使 |
| `converge_printing_identity` fingerprint 會 fire | ✅ 會 | ❌ 唔會 |
| editorial JSON key 要掃 | ✅ 要 | ❌ 唔使（內部照用 opaque_id） |
| 舊 URL 301 | 要 | 要（一樣） |
| 出街 URL 乾淨 | ✅ | ✅ |

**兩個方案都要做嘅**：`card/[id]/page.tsx` 加一層 lookup（收到舊 id 就 301 去新 id），然後 rebuild 出新 sitemap。冇呢步舊 URL 硬 404（`next.config.ts` 冇 `redirects()`）。

**唔建議嘅第三條路**：直接喺投影層剔走嗰幾張卡。rank 93 喺首頁 top-100，剔走即係首頁得 99 行，會撞到落 coverage assert。

#### 另一件決策題：文案門檻（第 5 節）

同 Wave 2 一齊決定就得，改字本身係 5 分鐘。要答嘅係一句：**「每日線接受一個零成交嘅指引價」呢件事，出街文案點講先算誠實？** 決定咗就寫死一個共用 constant，唔好再散三份。

### Wave 3 — infra，可以拖（按價值排）

| 優先 | 事 | 規模 | 備註 |
|---|---|---|---|
| 高 | `compose.yaml` 加 `CARDZ_ENVIRONMENT` | 1 行 | 唔加嘅話 staging 一開就被索引 |
| 高 | `createRobotsPolicy` 加兩個 test | ~20 行 | 而家零 test |
| 中 | 定 canonical host + Dockerfile build ARG | 半日 | 要先決定 apex 定 app. |
| 中 | 刪 `public/_headers` | 1 行 | 死配置 |
| 低 | ISR / `no-store` | 真工程 | 要先 measure 生產 header，edge 唔喺 repo |
| 低 | OG 圖搬出 `/api/` | 半日 | 要先 live 驗社交 preview 有冇壞 |
| — | robots blocklist 補唔補 | **唔係工程題** | Owner 政策，Codex 唔好自己決定 |

### 一句總結

**Wave 0 + Wave 1 = 一日半，做完就止咗血。** Wave 2 睇 Wave 0 量到咩先計劃。Wave 3 除咗 `CARDZ_ENVIRONMENT` 嗰一行之外全部可以下個 sprint。

---

## 1. 呢份文件點嚟

12 個 read-only agent：6 條線各自查一個題目，然後**每條線再派一個 adversarial verifier 逐條 claim 開返個 file:line 對**。verifier 推翻／修正咗 36 條，包括好多 off-by-one 嘅行號同兩條實質錯誤。下面寫嘅係**修正後**嘅版本。

冇跑過任何寫入操作，冇 query 過 MySQL。所有需要查 DB 先答到嘅嘢集中喺第 7 節。

---

## 2. 🔴 P0-1：公開 card id 帶供應商前綴

### 事實

公開 URL 嘅 card id **就係 `catalog_variant.opaque_id` 原封不動**，兩條資料路徑都係，**零格式驗證**：

- live-db 路徑：[apps/web/src/lib/live-db-snapshot.ts:470](../../apps/web/src/lib/live-db-snapshot.ts) — `id: String(row.opaque_id)`
- baked 路徑：[pipelines/operator_control.py:1141](../../pipelines/operator_control.py) + `:1257`
- 投影層照抄：[apps/web/src/lib/snapshot.ts:112](../../apps/web/src/lib/snapshot.ts) — `id: card.id,`（sibling field 有 whitelist／有 null 化，就係 `id` 冇）

現行 snapshot 嘅 id 普查：**1312 個 `cmc_`+24hex、7 個 `cmc_`+20hex、3 個帶供應商前綴+24hex**。三個係：

```
g10_036812c0a409b0fef6ba5dff   ← 已經喺 built sitemap，1 個 <loc> + 6 個 hreflang alternate
g10_356a7d75453fba4d71586411   ← rank 93，首頁 top-100（2002 Pokemon Japanese McDonald's Squirtle）
g10_c43dd6aa54b7671068938692   ← rank 266（One Piece 1st Anniversary Zoro）
```

證據：[data/public/seed-snapshot.json:314712](../../data/public/seed-snapshot.json)、`apps/web/.next/server/app/sitemap.xml.body:2564`、[apps/web/src/app/sitemap.ts:25](../../apps/web/src/app/sitemap.ts)。

> ⚠️ 個 sitemap body 係**本機 build 產物**，唔一定等於線上嗰份（佢淨係含 3 個入面嘅 1 個，而且 lastmod 比 snapshot 舊一個 bake）。**線上到底出咗幾多個，要 curl 生產環境 `/sitemap.xml` 先知。**

### 點解會咁

**唔係 migration 漏咗。** 全 repo 冇一句 `UPDATE catalog_variant SET opaque_id`，冇任何 rename 存在過。相反，`opaque_id` 係**明文凍結**嘅（`PLAN_036_FE02.md` D7、[pipelines/converge_printing_identity.py:127](../../pipelines/converge_printing_identity.py)「一個 opaque_id 都唔准變」、[pipelines/db_runtime.py:757](../../pipelines/db_runtime.py) 會 raise）。即係：**鑄錯咗，然後被凍結鎖死。**

個窿喺邊：`catalog_variant` 有三個 INSERT 點，兩個自己計 id（安全），第三個 [pipelines/db_runtime.py:725](../../pipelines/db_runtime.py) **照單全收 caller 俾嘅 `card["pokedexId"]`**，冇 prefix／長度／字符集檢查。

**而呢個窿已經有第二種前綴喺度爬緊**：[pipelines/tracked_universe.py:202](../../pipelines/tracked_universe.py) 鑄 `candidate_`+sha[:20] 做 pokedexId，直入同一道門。（注意：verifier 話 repo 內搵唔到 `tracked_universe` 嘅 consumer，所以呢條路徑係 [INFERRED]，未見到實際 `candidate_` id 落地。）

**冇一個 writer 喺呢個 tree 鑄過 `g10_` id** [INFERRED]——全 repo 搵唔到構造式。三個 id 都係 24 hex，同 `cmc_` 嘅 sha256[:24] 完全同一個形狀，即係當年用同一條公式、淨係換咗個 prefix。個 writer 唔喺呢個 repo。

### 兩個死表

1. **[packages/market-data/src/id.ts:11](../../packages/market-data/src/id.ts)** 有個 `isOpaquePublicId`，regex `^cmc_(?:[0-9a-f]{20}|[0-9a-f]{24})$`——**全 repo 零 call site**，export 咗冇人 import。
2. **[pipelines/g10_public_snapshot.py:56](../../pipelines/g10_public_snapshot.py)** 個 `PRIVATE_TOKENS` **字面上就有呢個前綴**，但個結果淨係加落 `:1426` 一個 summary field，**冇人 assert，fail 唔到 build**。

> 呢個正正係 daddy 條規矩：**有檢查但零 call site = 冇檢查**。

### 建議做法

1. **即刻查真** — `SELECT opaque_id FROM catalog_variant WHERE opaque_id NOT LIKE 'cmc\_%'`。snapshot 只覆蓋 ranked universe（1322 張），未上榜嘅 row 未睇過。
2. **封窿（做錯即刻嗌）** — 喺 `db_runtime.upsert_variant` INSERT 之前 assert `^cmc_[0-9a-f]{20,24}$`，fail-closed。呢個係唯一一個收 caller id 嘅位。
3. **封第二層** — 喺 [scripts/bake-public-snapshot.mjs](../../scripts/bake-public-snapshot.mjs) 加 id 格式 assert（或者直接 wire 返 `isOpaquePublicId`），bake 唔過就唔好出。一個 gate 蓋住兩種 serving mode。
4. **remap 要準備嘅嘢**（做之前睇清楚）：
   - `opaque_id` 係 UNIQUE key，但**冇 FK 指住佢**（FK 用 BIGINT `catalog_variant.id`），022–026 read model 全部係 VIEW，會自動跟。
   - **但兩個 stored hash 會被搞爛**：`catalog_source_identity.bind_evidence_json` 入面 embed 咗 `canonicalOpaqueId`（[db_runtime.py:480](../../pipelines/db_runtime.py)），佢個 `evidence_sha256` 就係呢個 payload 嘅 sha（`:483`／`:494`）；`catalog_printing_identity.provenance_json.sourceRowSha256` 亦 hash 咗 `v.opaque_id`。**改 id 一定要一齊重算，唔係之後所有 receipt check 都會報幻覺 mismatch。**
   - 編輯內容係 keyed by published id：`data/editorial/top100-stories.json`、`canonical-image-rebase.json`、`canonical-printing-repairs-026.json`，同 [pipelines/editorial_localization.py:229](../../pipelines/editorial_localization.py) 個 fallback。
   - **唔使掂**：卡圖（content-addressed sha256，同 id 無關）、watchlist（server-side rank≥101，唔係 client storage；localStorage 只有 `cardz-theme` / `cardz-heatmap-params`）。
   - **`opaque_id` 凍結係寫死嘅政策（D7）。** remap = 一次性 receipted 例外，`converge_printing_identity` 個 before/after fingerprint 一定會 fire ——**預咗佢會 fire，唔好熄佢**。
5. **舊 URL 會硬 404。** [next.config.ts:71](../../apps/web/next.config.ts) 只有 `headers()`，冇 `redirects()`／`rewrites()`；[middleware.ts:15](../../apps/web/src/middleware.ts) 只加個 lang header。冇 `_redirects` / `vercel.json` / wrangler。要保住舊 URL 就要**先**起 alias 表（`old_opaque_id → variant_id`）+ `card/[id]/page.tsx` 入面 `redirect()`。既然已經入咗 sitemap，應該當佢已經俾人爬過。

---

## 3. 🔴 P0-2：公開面泄漏 gate 已經冇咗

`scripts/canary-public.mjs` **喺呢個 tree 唔存在**，disk 冇、HEAD 冇。b3734e4f（2026-07-22）加入，**78848067（2026-08-07，"release: CARDZ Market Cap 033 FE02 snapshot [deploy]"）刪走**，連 `pipelines/run-generation-canary.mjs`、`tests/canary-generation.test.mjs`、`docs/RUNBOOK.md` 一齊。

**今日冇任何嘢頂上**：root `package.json` 得 `dev`/`build`；`apps/web/package.json` 得 `dev`/`build`/`start`；`.github/workflows/` 存在但係**空而且未 tracked**；`AWS_GITHUB_PULL_DEPLOY.md` 個 deploy 指令係 dirty check → SHA 對數 → fast-forward → `docker compose up --build --wait` → curl `/api/health`——**零 leak scan，而且啲 assert 全部喺新 container 已經對外服務之後先行**。

> 個舊 script 嘅完整內容仲拎得返：`git show 78848067^:scripts/canary-public.mjs`。
> 另外個舊 tree `Playground/cardz-market-cap/scripts/canary-public.mjs` 都仲有一份（**嗰個 tree 係舊 checkout，唔好攞嚟改 code，淨係攞嚟參考**）。

### 就算 revert 返都擋唔到今次件事

- 舊 regex 係 `\b(?:g10|gemrate|snkrdunk|sneakerdunk|ebay)\b`。`_` 喺 JS 係 word character，所以 `g10_356a…` **冇 word boundary，match 唔到**（node 實測：`g10_356a…` → false，`g10-356a` → true）。同一個窿令 `<供應商>_sales` 呢類 source code 全部隱形。
- `DEFAULT_PATHS` **從來冇 `/card/*`**，而且入面 4 條 `/graders/*` 路由**而家根本唔存在**（`grep -rn graders apps/web/src` 零命中），`fetchPage` 撞到非 200 就 throw ——**直接 revert 會即刻炸**。
- 佢個 robots assert 都過時（要求 `/latest.json`、`/generations/`，而家 policy 出嘅係 `/api/`、`/data/private/`、`/tune`）。
- 佢**由頭到尾冇掃過 JSON API**。`PRIVATE_PATHS` 探嗰五條路由喺現行 app 一條都唔存在，所以嗰個邊界檢查係**空跑通過**。而 [api/v1/cards/[id]/route.ts:11](../../apps/web/src/app/api/v1/cards/[id]/route.ts) 係**原封不動吐成個 card object**——泄漏保真度最高嘅就係佢。

### 建議做法

唔好照 revert。要嘅係**兩層**：

1. **producer 側 assert**（首選，"冇得做錯"）：bake 時 assert 每個公開 id 係 `^cmc_[0-9a-f]{20,24}$`，assert 投影後嘅 payload 唔含任何 source code 字串。呢個係唯一一個兩種 serving mode 都蓋到嘅位。
2. **deploy 前掃描**（"做錯即刻嗌"）：掃 `/`、`/pokemon`、`/one-piece`、`/watchlist`、**由 snapshot 抽 N 條 `/card/{id}`**、`/api/v1/market`、`/api/v1/cards/{id}`、`/sitemap.xml`。boundary 改成 `(?<![A-Za-z0-9])…(?![A-Za-z0-9])`，token list 補返而家真係會出現嗰幾個 source code。放喺 `docker compose up` **之前**，唔係之後。

**另外**：`pipelines/rebuild_036.py` stage 14 個 `stage_canary`（[:6126](../../pipelines/rebuild_036.py)）**唔係同一樣嘢**——佢係資料採集 gate（驗 gemrateId 40-hex、product_ready 之類），仲喺度，同公開面泄漏無關。唔好因為見到「canary」就以為有防護。

---

## 4. 🟠 P1-1：`priceAnchorSource` —— 前端幫後端擋緊

### 事實

呢個 field **唔係 DB column**，係純 TypeScript 計出嚟。值嘅來源單一：[live-db-snapshot.ts:129](../../apps/web/src/lib/live-db-snapshot.ts)（讀 per-day `priceSourceCode`，底層係 `market_price_observation.source_code`，加上 `:419` 合成嘅 `<source>_sales` / `exact_psa10_sales` 偽 code）。

**發射點有三個**（唔係一個）：`live-db-snapshot.ts:146`、`:153`、同 [snapshot.ts:44](../../apps/web/src/lib/snapshot.ts)。前端今次全部改成硬 `null`——**HEAD 嗰陣 `snapshot.ts:42` 係 `priceAnchorSource: value.priceAnchorSource ?? null`，即係真直通**。

**全 app 零 consumer。** UI 真正讀嘅係 sibling boolean `sourceSwitched`（`live-db-snapshot.ts:130` 計，4 個 render 點用），已經存在，唔使發明新 field。

**但 `data/public/seed-snapshot.json` 仲然帶住成堆原字串**：HEAD 版 7,716 個 occurrence（340 個 null）＝ **7,376 個非 null**；working tree 版 7,932 個（356 null）＝ **7,576 個**。共 6 個 distinct 供應商／來源 code。呢個檔係生產 container COPY 入去嗰個（[apps/web/Dockerfile:45](../../apps/web/Dockerfile)）。

**即係：而家唯一擋住呢啲字串出街嘅，就係前端投影層嗰三行 `null`。** 只要有人「順手」清返個 field 但冇重 bake，或者有第四條讀路徑繞過 `normaliseSnapshot`，就即刻出街。（目前所有讀路徑——`api/v1/market`、`api/health`、`api/og`、`singleCardSnapshot`——都經 `loadMarketSnapshot` → `normaliseSnapshot`，冇人吐 canonical 原文。）

### 建議做法（5 步，做完就唔使再靠前端）

1. 刪 `live-db-snapshot.ts:146` 同 `:153`。**保留 `:129` 同 `:130`**——`sourceSwitched` 靠 `:129` 個 local。
2. 刪 `snapshot.ts:44`。
3. 由 type 刪走：[packages/market-data/src/schema.ts:28](../../packages/market-data/src/schema.ts)、[apps/web/src/lib/types.ts:26](../../apps/web/src/lib/types.ts)。兩邊都係 optional (`?`)，冇人 dereference，安全。
4. 重 build `packages/market-data`。**注意**：`packages/market-data/dist/` 係 **gitignored（[.gitignore:9](../../.gitignore) `**/dist/`）**，唔係 checked-in artifact——即係 commit 唔到嘢，但**本機舊 dist 會遮住新 src type 直到你 rebuild**。
5. **重 bake**：`node scripts/bake-public-snapshot.mjs`，commit 新嘅 `seed-snapshot.json`。個 bake script 會對 `live-db-snapshot.ts` 做字面 patch（`:56-65` 對兩個 string literal），今次改動唔掂到嗰兩行，所以 bake 照行。

⚠️ **另一個 writer**：[pipelines/g10_public_snapshot.py:47](../../pipelines/g10_public_snapshot.py) 個 `DEFAULT_OUTPUT` **就係 `data/public/seed-snapshot.json`**（`:1532` 寫、`:1540` 接 `--output`），而且佢**唔喺 `_archived_non_daily/`**。佢唔發 `priceAnchorSource`，但佢個 `effectiveAt` 語意完全唔同（`effectiveAt = generatedAt`，仲會標 `mode: "demo"` / `productionEligible: false`，見 `:1357`）。**唔好用佢寫公開檔**——一寫就靜靜咁改咗全站「Updated」日期嘅定義。

---

## 5. 🟠 P1-2：出街文案講嘅門檻，同 code 唔一樣

| 講法 | 出處 | 真值 |
|---|---|---|
| 「五筆已驗證 PSA 10 成交／30 日滾動窗」 | [apps/web/src/lib/i18n.ts:186](../../apps/web/src/lib/i18n.ts)（出街 footer） | ❌ |
| 30 日窗 | `pc_psa10_price_derivation.py:47` `WINDOW = timedelta(days=30)` | ✅ 啱 |
| 最少成交筆數 | `pc_psa10_price_derivation.py:48` **`MIN_EBAY_SALES = 3`** | **3，唔係 5** |
| 同一個 3 又寫死咗一次 | `pc_psa10_price_materialize.py:156` 硬 literal `3` | 改 constant 唔會生效 |
| 第三份 copy | `ebay_sold_data.py:213/217` docstring 都寫住三筆 | 同一個數字散落三處 |
| **每日跑嗰條線** | `collect_control.py:3427/3433` 只傳 `--sources pricecharting` | **完全唔行成交筆數呢條路**，讀 PriceCharting 指引價欄位，**零成交要求** |
| 「Top 100 席位靠成交量維持」 | 同一段 footer | ❌ `ranking_derivation.py` 個 `rejection_reason()` **冇任何成交筆數 gate**——只有 identity 完整、population ≥ 1000（`:27`）、價格新鮮度 ≤ 48h |
| 「成交太少會標 accumulating」 | 我今次新加嘅 `provenance.steps[2]`（i18n.ts） | ❌ `accumulating` 由 window coverage 決定（`operator_fe_export.py:893`、`market_metrics.py:95`），同筆數無關 |

`pipelines/_archived_non_daily/` **喺呢個 tree 唔存在**，所以上面全部係活 code，唔係死 code。呢個 repo 亦**冇 `docs/MODEL.md`**，冇任何文件寫過門檻——即係「文件同 code 對唔對得上」呢條問題本身冇文件可對。

### 要你決定

**邊個係權威？** 兩條路：

- 改 code 去夾文案（`MIN_EBAY_SALES` 3→5，`materialize` 個 literal 改成 import 個 constant，`ebay_sold_data` 一齊）；**同時要面對「每日線根本唔經呢條路」呢個更大問題**；或者
- 改文案去夾 code——但要先答到「每日線接受一個零成交嘅指引價」呢件事點樣講先算誠實。

決定咗之後**寫死一個共用 constant，唔好再散三份**，同時把個數字寫入 i18n 嘅來源（或者最少加個 test 對住 constant）。前端嗰邊我已經標咗 P0b，等你答案。

---

## 6. 🟡 P2：robots / sitemap / cache

全部係 code-generated，冇 static file：`apps/web/public/` 只有 `_headers`、`brand/`、`card-placeholder.svg`。

1. **AI crawler blocklist 只有兩個**：[robots.ts:19](../../apps/web/src/app/robots.ts) `{ userAgent: ["GPTBot", "CCBot"], disallow: "/" }`。**ClaudeBot / anthropic-ai / PerplexityBot / Google-Extended / Bytespider / Amazonbot / Applebot-Extended / Meta-ExternalAgent 全部放行。** `OAI-SearchBot` 係故意放行（`:18`），search 同 training 分開——做法啱，但**得 OpenAI 一家有呢個分法**。要唔要補齊係 owner 決定。
2. **staging 會被索引 [INFERRED]**：`robots.ts:8` 有 canary/staging「全擋」分支，讀 `process.env.CARDZ_ENVIRONMENT`；`.env.example` 有宣告，但 **`compose.yaml` 個 `environment:` 冇傳入去，亦冇 `env_file:`** ——即係 runtime 永遠 undefined，永遠行 production（全開放）分支。修法：`compose.yaml` 加 `CARDZ_ENVIRONMENT: ${CARDZ_ENVIRONMENT:-production}`，然後**真係 curl staging `/robots.txt` 驗**，唔好信 code path。
3. **`createRobotsPolicy` 零 call site、零 test**（`robots.ts:7` export 咗「方便測試」但冇人測）。加返兩個 assert 就搞掂。
4. **canonical host 未定**：`layout.tsx:9` fallback `https://cardzmarketcap.com`，`.env.example` 寫 `https://app.cardzmarketcap.com`，而 `compose.yaml` 同 Dockerfile 都冇傳 `NEXT_PUBLIC_SITE_URL`。呢個值同時決定 canonical、sitemap `<loc>`、robots 個 `Sitemap:` 行、JSON-LD 絕對 URL。`NEXT_PUBLIC_*` 係 **build time inline**，所以要加做 Dockerfile build ARG，淨係加 runtime env 唔生效。
5. **ISR 係死嘅**：全部 page route 宣告 `revalidate = 300`（`layout.tsx:21`），但 `RootLayout` `await headers()` 令成棵樹變 dynamic + `Cache-Control: no-store`（code comment 有記低係實測結果）。即係 sitemap 宣告嗰 1,290+ 條 URL **每條都係無 cache SSR**。好消息：variant 係 keyed by `?lang=` query 唔係 header，所以 URL-keyed CDN cache 本來就啱，喺 ALB/Nginx 加 `s-maxage` 係可行嘅。
   （snapshot 讀檔本身唔係樽頸——[server-snapshot.ts:94](../../apps/web/src/lib/server-snapshot.ts) `snapshotPromise ??= readSnapshot()` 每個 process 只 parse 一次。）
6. **sitemap 係 build 時 prerender 嘅，唔係每次 request 計**（`.next/server/app/sitemap.xml.body` 1,111,188 bytes + prerender-manifest 有列）。**只有 `/robots.txt` 係 per-request**（`robots.ts:5` `force-dynamic`）。實際後果同直覺相反：**換 snapshot 唔會更新 sitemap，要 rebuild**。現行 body 有 1,290 條 URL（1,286 條 `/card/`），`lastmod` 2026-08-10T21:03:16Z，而 snapshot `effectiveAt` 係 2026-08-11T08:18:20Z ——**個 sitemap 落後一個 bake**。
7. **每條 sitemap entry 用同一個 `lastModified`**（`snapshot.effectiveAt`），即係每日 bake 一次全部 1,300+ 條 URL 一齊「改過」。Google 會學識唔信呢個信號。有 per-card 觀測時間就用，冇就寧願唔出 `lastModified`。
8. **OG 圖被自己封死**：`/api/og/card/<id>` 喺 `/api/` 底下，robots 對所有 UA disallow，`next.config.ts` 仲加 `X-Robots-Tag: noindex, nofollow, noarchive, nosnippet`。社交爬蟲唔理 robots 所以 preview 應該仲出到，但 Google／Bing 嘅圖片同 rich result fetcher 會遵守——即係卡圖對佢哋唔存在。要修就搬出 `/api/`（例如 `/og/card/[id]`）或者喺 disallow 之上開一條 `allow: "/api/og/"`。**改之前先 live fetch 驗返現況。**
9. **`apps/web/public/_headers` 係死配置**（Cloudflare Pages / Netlify 慣例檔，Node standalone runtime 唔理）。仲有：Dockerfile runner stage **只 COPY `.next/standalone`（`:44`）同 seed-snapshot（`:45`）**，冇 COPY `apps/web/public` 或 `.next/static`——所以 `/_headers` 到底公開攞唔攞到**唔確定，要 curl 先知**。無論如何佢公開咗一個 `Access-Control-Allow-Origin: *` 嘅意圖，建議刪或者搬入 `next.config.ts`。
10. **infra 唔喺 repo**：冇 wrangler / vercel.json / netlify.toml / Terraform / nginx / Caddy。deploy 係 Docker Compose 行 Next standalone。真正嘅 edge（ALB／Nginx／Tunnel）config 喺 repo 外，**crawler 實際收到咩 header 一定要 curl 生產環境先知**。

---

## 7. 附錄：snapshot 契約（改嘢之前睇，唔好整爛）

**兩層 type**：後端寫嘅 canonical `PublicMarketSnapshot`（[packages/market-data/src/schema.ts:163](../../packages/market-data/src/schema.ts)）→ `normaliseSnapshot()` 投影成 DOM 面嘅 `MarketViewSnapshot`（[apps/web/src/lib/types.ts](../../apps/web/src/lib/types.ts)）。**契約檔係 schema.ts**，view type 係下游。

**模式由一個 env 決定**：`CARDZ_DATA_MODE === "live-db"` → 直讀 MySQL 3308（按 ranking generation hash cache）；其他值 → 讀 baked JSON（`MARKET_DATA_SNAPSHOT_PATH`，預設 `data/public/seed-snapshot.json`，**每個 process 只讀一次**）。

幾條會咬死人嘅規則：

- **live-db mode 只喺 ranking generation hash 變咗先重建。** 每個 request 探一次 `market_canonical_metric_acceptance` 最新 `ranking_generation_sha256`；hash 冇變就回 cache。**唔產生新 accepted ranking generation 嘅寫入，前端永遠見唔到。**
- **重 bake 之後要 restart web process**，唔係 bake 完 curl 舊 process 就話冇效。
- **投影層有 whitelist**：canonical `printingIdentity` 只有 `setCode`／`finishCode` 過到嚟（`snapshot.ts:68-75`）。加 canonical field 唔會自動出到前端。
- **圖片契約靠一條 regex**：`snapshot.ts:83` 要求 `image.kind === "raw_front"` 而且 `src` 完全符合 `^/market-assets/[a-f0-9]{64}\.webp$`。**其他格式（CDN URL、.jpg、其他目錄）會靜靜變成 placeholder，冇 error。** 卡圖真身喺 `data/public/market-assets/`，build 會刪走 `apps/web/public/market-assets`（`prepare-standalone.mjs`，故意嘅）。
- **缺咗會直接 throw 嘅 field**（唔係 nullable）：`currencies.rates[<7 種>]`、`coverage.localizedStoryCount` 呢個 object、`windows[w]` 同 `windows[w].changePct` / `.trackedSales` / `.trackedSales.valueUsd` / `.count`、`historyDaily`（必須係 array）、`collectorNumber` object、`image` object。
  **缺咗係靜靜變 undefined、唔會 throw 嘅**：`collectorNumber.display`、`trackedSales.coverage`、`trackedSales.asOf`、個別 `localizedStoryCount` locale key。
- **冇任何 runtime schema 驗證**。`dist/validate-production-snapshot.js` 係孤兒 build artifact，零 importer。契約壞咗會以 TypeError 或者一版白嘢出現，唔會有乾淨嘅 gate failure。**想要就自己喺 bake 加。**
- **provenance panel 靠兩個時間值**：list 頁用 `snapshot.effectiveAt`（live-db mode = 所有 core row 嘅 `metric.accepted_at` / `price.observed_date` / `population.effective_at` 最大值），卡片頁用 `card.pricePsa10.asOf`（= `market_price_observation.observed_date`，fallback `effective_at`）。
  - **一個未來日期嘅 accepted_at 會扯高全站個「Updated」日子。**
  - `observed_date` 而家係**面向公眾嘅展示日期**，唔再係純內部欄位——重 stamp 佢即係改咗卡片頁話畀用戶聽嘅嘢。
  - **千祈唔好出一個非 null 嘅 `pricePsa10.value` 配 null `asOf`**（`live-db-snapshot.ts:71` 綁死咗兩者），會令頁面用 generation 時間做價格日期，可以差幾個月。
- `generation.id` 公開睇得到：頁面 shell 個 `data-cardz-generation` attribute 同 `/api/health`（仲會報 `dataMode`）。**debug 之前先用 `/api/health` 確認個 server 到底行緊邊個 mode 同邊個 generation。**

---

## 8. 未答嘅問題（要查 DB 或者 curl 生產先答到）

呢啲全部係 read-only 靜態審計答唔到嘅，**唔好當已知**：

1. `catalog_variant` 到底仲有幾多個非 `cmc_` 嘅 `opaque_id`？（snapshot 只覆蓋 1,322 張 ranked 卡）
2. 邊個 writer 鑄咗嗰三個 id？查 `catalog_source_identity` / `market_ingest_run` 嗰三個 variant_id 嘅 provenance。**用現有欄位重算 `cmc_` 公式對唔返**——PSA authority 事後 restate 咗 `canonical_name` / `set_name`（rank 93 重算得 `909295e0…`，唔係 `356a7d75…`）。
3. 生產而家行緊 baked snapshot 定 live-db？兩條路 id 一樣，但決定咗修完幾快生效。
4. 生產 `/sitemap.xml` 真係含幾多個帶前綴 id？搜尋引擎索咗未？
5. `CARDZ_ENVIRONMENT` / `NEXT_PUBLIC_SITE_URL` 有冇喺 repo 外注入（host 上面嘅 `.env`、systemd、CI）？
6. 生產實際回嘅 cache header 係咩（edge 唔喺 repo）？
7. `market_price_observation.source_code` 喺 live DB 嘅完整值域（我只見到 bake 出嚟嗰 6 個）。
8. 每日線實際有幾多 % 嘅參考價係經零成交嘅指引價欄位入嚟？（直接關係到第 5 節點樣改文案）
9. 有冇 repo 外嘅 consumer 一直喺度 scrape `/api/v1/market` 個 `priceAnchorSource`？（patch 之前佢係公開可見嘅）
10. `78848067` 刪 canary 係有意定係順手？——嗰個 commit 係整棵樹嘅 release commit，我冇 diff 全部。
