# GemRate 操作手冊：CARDZ Population Authority

GemRate 是 CARDZ 的 **grader population authority**。CARDZ 的 PSA 10 市值只可以使用：

```text
GemRate PSA 10 population × validated SNK PSA 10 reference price (USD)
```

## 0. TL;DR（2026-07-24 curl_cffi 版 — **唔使 browser**）

```powershell
# 一鍵暴力收割全部 TCG sets >= 2020（curl_cffi 模擬 Chrome TLS 指紋，直接 HTTP，唔使 Playwright）
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap
.\.venv-backend\Scripts\python.exe -X utf8 pipelines\gemrate_brute_harvest.py --all-sets

# 結果：52 sets, 16,309 張卡, 939 張 PSA10 >= 1000 → data/private/gemrate_brute/

# 單 set（40-hex set_id）
.\.venv-backend\Scripts\python.exe -X utf8 pipelines\gemrate_brute_harvest.py --set-id <40hex>

# 搜卡（POST /universal-search-query，body {"query":"..."}，無 /api 前綴）
.\.venv-backend\Scripts\python.exe -X utf8 pipelines\gemrate_brute_harvest.py --query "rayquaza vmax"
```

**點解唔使 browser**：GemRate 嘅 Cloudflare 只驗 TLS/HTTP 指紋。`curl_cffi`（`impersonate="chrome"`）模擬真 Chrome 嘅 TLS ClientHello，直接 `requests` 式 GET/POST 就拎到 inline `setsData` / `rowData`，唔使 Playwright 開真 browser。呢個係「內部直打」而唔係「外部爬蟲」。

**已驗證嘅無 key 路線**（全部 curl_cffi 直打）：

| 路線 | 方法 | 備註 |
|---|---|---|
| `POST https://www.gemrate.com/universal-search-query` | body `{"query":"..."}` | **無 `/api` 前綴**；回傳 cards 含 `gemrate_id` |
| `GET /universal-pop-report` | inline `setsData`（400 sets） | 要 `: NaN`→`:null` 清洗；keys: beckett_grades, category, psa_grades, set_id, set_link, set_name, total_grades, year；**category 係 `"TCG"` 大階，year 係 STRING，set_link 已係完整 URL** |
| `GET {set_link}` set 頁 | inline `const rowData = JSON.parse('...')` | 全卡 4 廠 grade 數據；卡名含 `"`（如 `Eustass"Captain"Kid`），先 `json.loads` 失敗先 `unicode_escape` fallback |
| `GET /card/{gemrate_id}` | search 返嘅 id 直接開 | 可用；**`gemrate_checklist_id` ≠ `gemrate_id`**（兩個 namespace） |
| `GET /card-details?gemrate_id=...` | page-context-bound | **replay 直接 fetch → 403**；token 綁頁面 session |
| `GET /api/*` 同 `POST *-query` 變種 | — | **全部 404**（唔存在）；`api.gemrate.com/v1/*` 全部 403（要 key） |

---

## 1. 先記住：authority 與 transport 不是同一件事

| 資料項 | Authority | 可用 transport（順序） | 不可做的事 |
|---|---|---|---|
| PSA 10 POP（現值） | GemRate | direct API → exact public card page session → exact Grade10 GemRate mirror | 不可用 search result 的 total population；不可用 SNK／eBay／名稱猜測 |
| PSA／BGS／CGC／SGC history | GemRate | direct API `population/history?interval=week` | mirror 或搜索結果不可補 history／0 |
| 5 廠評級量（含 TAG） | GemRate monthly recap | recap text；weekly social 圖需有可審核 input | 不可把廠商總量當單卡 POP |
| identity discovery | canonical exact mapping | GemRate search + G10 asset metadata + review queue | 不可把第一個名字相似的 search result 自動配對 |

`PSA 10 POP >= 1000` 才可入正式 ranking。`971–999` 僅可入 pre-entry radar；`<=970` 只保留 discovery evidence，不能進每日監察池或正式榜。

## 2. 目前可用路由與失敗語義

| 路由 | 用途 | 當前狀態 | 成功後可否升級 canonical observation |
|---|---|---|---|
| `api.gemrate.com/v1/cards/{id}/population?parsed_description=true` | exact current POP + private identity receipt input | 需要 `GEMRATE_API_KEY` | 可以；需 exact canonical identity 和完整卡號 |
| `api.gemrate.com/v1/cards/{id}/population/history?interval=week` | sampled population history | 需要 `GEMRATE_API_KEY` | 可以；官方 public response 是 week/two_week sampling，不可偽造成 raw daily history |
| Grade10 `price.getGradingPopulations` | GemRate current-POP mirror | bootstrap／last-good 旁路 | 可以只作 `grade10_gemrate_mirror` provenance 的 current observation；不可產生 history |
| GemRate universal search | 尋找 exact candidate／identity evidence | 無 key，可經受管 browser | 不可以；這只是一條 discovery route |
| `/card/{gemrateId}` server-rendered page | keyless exact current POP + provider identity evidence | 無 key，受管 browser page session | 可以作 current POP transport；須驗 canonical `/card/` route、GemRate ID、PSA `g10`、source date 與 DOM hash；`card_number` 可能完整、短碼或空白，不能自行補成完整卡號 |
| page-initiated `/card-details?gemrate_id=...` | exact current POP JSON | 只接受由已驗證 `/card/{id}` 頁面本身發出 | 首選 current POP transport；不得直接 fetch；response body 必須 content-addressed 保存於 private landing，且不得進 MySQL／public／log |
| direct `/card-details?gemrate_id=...` | 舊／不受支援的 JSON fetch | warm search/report 後可回 `403 unauthorized` | 不可作 transport；只記 private failure receipt，永不 promotion |
| monthly recap | 五廠總評級量 | 可爬文字 | 可以寫 grader-level volume；不可改寫 per-card POP |

`public-card-dump` 先開 exact `/card/{gemrateId}`，並只捕捉該頁面自己發出的同 ID `/card-details` JSON。它驗證 JSON 的 opaque ID、year／set／provider `card_number` evidence、PSA `grades.g10`、`date`／`last_population_change`、canonical `/card/` route 與 DOM hash，然後只寫入較小的 normalized receipt。它**不會**自己直接 fetch `/card-details`。公開頁的 `card_number` 可以完整、短碼或空白；只有已驗證為完整的值才可供 exact crosswalk 使用，短碼／空白一律留在 review，不准前端或 collector 補 suffix。若 page-initiated JSON 缺失或 schema drift，才以同一頁的 PSA row＋labelled `GEM MINT` DOM parser 作後備；兩者都不完整才失敗。2026-07-24 本機 live smoke 已確認 page-initiated JSON 路徑可取得 current PSA 10 POP；此證據只代表 route 可用，並不代表所有卡都可解析。任何最終失敗都令 run `partial=true`、`promotable=false`；不准用上一張卡、search total、或 0 補位。

### Direct API identity receipt（P0）

Direct current POP 必須使用 `GET /v1/cards/{requestedGemrateId}/population?parsed_description=true`。成功 payload 會連同 `population.json`（及要求 history 時的 `history_full.json`）原子寫入 private landing，另寫 `identity.receipt.json`；receipt 只保存 payload SHA-256 和 private relative pointer，不會把 key、header、完整 URL 或 raw body 放入 manifest／log／public。

GemRate 的三層 ID 有不同語義，**不可互相升級**：

1. `requestedGemrateId` 是本次 direct route 的 lookup ID。
2. `entityGemrateId` 是 response entity；`universalGemrateId` 加 `isUniversalMatch` 表示其 universal 關係（可為 member 或 universal entity）。
3. `population.graders[grader].gemrate_id`（及可選 `spec_id`）是該 grader member ID，不是 CARDZ canonical identity。

receipt fail-closed 驗 requested/entity 的 40-hex 完全一致、universal ID 與 boolean/null 關係一致、每個 grader member ID／spec ID 格式正確且 member ID 不跨 grader 重複。它保留 top 和 per-grader `parsedDescription` 作私有審核；requested、entity、universal、grader member 與 spec ID 都只會寫成 private alias，**沒有一個可直接 promotion 成 CARDZ canonical ID**，仍須走既有 exact crosswalk／identity gate。

Direct history 固定只用 `population/history?interval=week`。它是 weekly sampled history，不能由 current POP、fetched time、universal/member relationship 或 mirror 補成 daily history。

公開頁 JSON 的 `date`／`last_population_change` 是供應量最後變動的來源 metadata，**不是**「今天冇變動就過期」的證明。collector 會把本次受管 page session 成功取得 current POP 的 `fetchedAt` 記為 `effectiveDateSource=live_public_snapshot_fetch`，同時保留 `sourceDate` 與 `lastPopulationChange`。因此每次 live current 收集都有真實觀察時間，亦不會把其偽造成 GemRate 每日歷史。

### 官方 API 的額外能力（暫不假裝已接通）

- `GET /v1/cards/search/structured?query=...&grader=psa` 是 authenticated structured search，適合精確 query，不可當 first-result auto-match。
- `GET /v1/cards/population/changes?since=YYYY-MM-DD&cursor=&limit=` 是 authenticated change feed，最多 30 日且目前不是 basic plan 的保證能力；將來可作 radar 加速，不可取代 full current POP run。
- `GET /v1/catalogs/pokemon` 是 premium catalog 路徑，現階段只涵蓋 Pokémon；不可把它當全球 TCG／One Piece discovery completeness claim。

### GemRate ID alias crosswalk（canonical 規則）

GemRate ID 用來令 CARDZ 在之後的 incremental run 識得「今次 provider 回應的是哪一張已知 printing」，但它們永遠不是 CARDZ ID，亦不是前端／公開 snapshot 欄位。canonical printing identity 仍然只由已確認的 `TCG + language + set + complete collector number + edition + parallel + finish` 決定。

| Provider alias | 含義 | private canonical relation | 不可做的事 |
|---|---|---|---|
| `requestedGemrateId` | 本次 GemRate route 的 lookup ID | 寫入 `catalog_source_identity`，並作 receipt alias 的 requested anchor | 不可當 CARDZ ID 或從名稱猜 printing |
| `entityGemrateId` | response entity ID | 只作同次 receipt 的一致性證據 | requested/entity 不一致即留 private failure receipt，不 promotion |
| `universalGemrateId` | GemRate universal/member 關係 alias | 寫入 `catalog_provider_identity_alias` | 不可把 universal ID 自動當唯一 printing |
| `population.graders[grader].gemrate_id` | 指定 grader 的 member alias | 寫入 `catalog_provider_identity_alias`，semantic key 包含 grader | 不可跨 grader 合併或當 CARDZ ID |
| `population.graders[grader].spec_id` | 指定 grader 的 spec alias | 寫入 `catalog_provider_identity_alias`，semantic key 包含 grader | 不可省略 grader namespace 或猜 variant |

一個 `universalGemrateId` 可以對應多個 requested／member ID；只有所有 receipt 都已 exact crosswalk 到**同一個 canonical printing**時才可以保存。若同一 universal/member/spec alias 企圖綁到另一個 canonical printing，import 必須 fail closed 並進 `market_identity_review_queue`，不可覆蓋舊 mapping。member 與 spec alias 必須以 grader 作 namespace，例如 PSA member alias 與 CGC member alias 即使 value 相同亦不是同一條 alias。

每個可持久化 alias 都保存 provider、alias type/value、grader（如適用）、requested anchor、canonical variant、payload hash、private relative source pointer、observed time 與 exact-confirmed status。未確認、短卡號、語言／edition／parallel 衝突、或沒有 requested anchor 的資料只留 immutable private receipt 和 review queue，不能寫入 canonical alias relation。

當 exact crosswalk 亦取得 `snkItemId` 時，SNK identity 必須以同一 canonical variant 另行持久化；GemRate alias 不可推導、猜測或取代 SNK item identity。之後的 daily increment 先收 receipt，再驗證／寫入 alias，再以已確認的 canonical variant 取 exact SNK PSA 10 價格與歷史。這樣新卡可累積 mapping，而不會把 provider ID 暴露到公開內容。

## 3. 四層數據處理：保留原文，再做可重播清洗

不要在原始資料上「洗走」東西。正確做法是保留 immutable raw，並以確定規則生成較小、可重播的 normalized 與 canonical facts。

```text
GemRate raw response / G10 mirror payload
        ↓ immutable private landing (hash + source pointer)
normalized observation (exact identity + grader/grade + dates + provenance)
        ↓ validation gates
canonical MySQL facts (one printing, dated observations)
        ↓ deterministic derivation
daily market-cap / rank / windows / alerts / sanitized snapshot
```

成功的 page-initiated JSON 會以 content-addressed 方式留在 private landing：`cards/<gemrate-id>/raw/<sha256>.json`。`card_details.json` 只保存 `privateSourceReceipt` 的 hash／relative pointer；`card_details.raw.receipt.json` 連同 normalized receipt 是 `--resume` 的完整性 gate。DOM fallback 沒有 JSON body 時只保存 DOM evidence hash，清楚標為 `dom_evidence_only`，不會假裝有 raw JSON。collector 每 25 張即時 atomic persist 一個 chunk；process／browser 中斷後，`--resume` 只跳過 normalized、receipt 和 hash 全部驗證通過的卡，其餘會安全重抓。Raw payload 不可重複塞入 MySQL，也不可進 public snapshot、Git history 或 log。詳細欄位與拒絕規則見 [資料清洗與對照規則](../docs/DATA_CLEANING_RULES.md)。

## 4. 安全注入 key：Windows、Linux、AWS

`GEMRATE_API_KEY` 只能在 collector process 的環境變數存在。不要把值放入 Git、`config/data-routing.json`、Task Scheduler arguments、shell history、JSON manifest、stdout、或 error text。

### Windows 本機

以 1Password／受限制的環境檔／Windows secret store 將 key 注入目前的 process 後才執行 Python。`op run` 的 env-file 可以只存 1Password reference，不存 key 值：

```powershell
# data/runtime/config/gemrate.op.env 必須 ignored，並只由操作者讀取。
# 內容使用你自己的 1Password reference；不要把 key literal 寫入此檔或終端歷史。
op run --env-file data/runtime/config/gemrate.op.env -- `
  .\.venv-backend\Scripts\python.exe pipelines\gemrate_source.py api-dump `
  --ids-file data\runtime\private-source-map\gemrate-ids.txt --speed medium --resume
```

若不使用 1Password，請由受限制的 runner wrapper 將 `GEMRATE_API_KEY` 放入 child process environment；不要使用 `setx` 把可見 key 留在使用者環境中。

### Linux／AWS

讓 systemd、AWS Secrets Manager integration 或部署平台 secret store 在 service 啟動時 inject `GEMRATE_API_KEY`。secret file 必須在 repository 以外、權限 `0600`、service account only；systemd unit 不得把 key 寫在 `ExecStart=` 或 journal。

```bash
# Key 已由 service/secret manager 注入到本次 process；command 本身不帶 key。
python3.11 pipelines/gemrate_source.py api-dump \
  --ids-file data/runtime/private-source-map/gemrate-ids.txt --speed medium --resume
```

執行前後只確認 key 是否存在，不要 print value：

```powershell
if ($env:GEMRATE_API_KEY) { 'GEMRATE_API_KEY present' } else { 'GEMRATE_API_KEY missing' }
```

```bash
test -n "${GEMRATE_API_KEY:-}" && echo 'GEMRATE_API_KEY present' || echo 'GEMRATE_API_KEY missing'
```

## 5. 指令：何時用、輸入、輸出、失敗後怎樣做

所有命令都從 repository root 執行。日常營運只應由 `python scripts/backend.py daily` 作 parent job；不要另建獨立 GemRate scheduler。下列命令是 operator repair／backfill 工具。

| 命令 | 何時使用 | 輸入 | 私有輸出 | 成功條件 |
|---|---|---|---|---|
| `api-dump --resume` | 有 direct API key、首次或補 history | exact GemRate IDs | `data/private/gemrate/cards/<id>/population.json`、`history_full.json`、`identity.receipt.json` | history 存在仍須 receipt hash／pointer／ID 驗證；receipt 可 offline 重建，raw 不可驗才重抓 |
| `identity-receipts --resume` | 補回既有 direct cache 的 private identity receipt（完全 offline） | `cards/<id>/population.json` | `cards/<id>/identity.receipt.json` | parent folder／raw payload／hash／pointer／all ID fields 全部驗證；不 call API、不改 raw |
| `daily` | isolate current POP route for已追蹤 ID | exact IDs + tracked identity + mirror root | immutable `data/private/gemrate/runs/<run-id>/manifest.json` | all requested IDs resolve；mismatch/partial 不 promotion |
| `collect` | 只發現候選／identity evidence | query list | private search manifest | human/exact crosswalk still required |
| `public-card-dump` | keyless exact current POP backfill | exact GemRate IDs | private content-addressed raw JSON + raw receipt + normalised `card_details.json` | page-initiated JSON 或 labelled DOM fallback 完整；沒有 provider body 寫進 MySQL |
| `grader-volume --period monthly` | 更新 PSA/CGC/TAG/BGS/SGC 廠商量 | optional recap slug | `data/private/gemrate/grader_volume.json` | parsed month carries source slug and observed time |
| `export-csv` | 分析 direct history | harvested direct cache | private CSV | CSV is a convenience artefact, not canonical authority |

### First full GemRate backfill

```powershell
# 1. Validate source routing and exact identity inputs first.
.\.venv-backend\Scripts\python.exe scripts\backend.py routes
.\.venv-backend\Scripts\python.exe scripts\backend.py explain psa10_population

# 2. With a securely injected key, backfill exact population + history resumably.
.\.venv-backend\Scripts\python.exe pipelines\gemrate_source.py api-dump `
  --ids-file data\runtime\private-source-map\gemrate-ids.txt --speed medium --resume

# 3. Run the parent pipeline; it validates, normalizes, derives and audits.
.\.venv-backend\Scripts\python.exe scripts\backend.py daily
.\.venv-backend\Scripts\python.exe scripts\backend.py status --json
```

If `api-dump` loses access, do not restart from zero. Fix secure key injection, then repeat exactly the same command with `--resume`. If there is no key, run the parent job with its exact Grade10 mirror route and let unresolved cards remain unresolved; that state cannot certify a global ranking.

### Offline direct identity receipt rebuild

當 direct `population.json` 已存在但沒有 P0 receipt，以下命令只枚舉 `cards/<id>/population.json`；只有 public `card_details.json` 的 folder 不屬 direct receipt input，唔會計入 attempted／failed。命令不讀取 key、不發 HTTP request，只以每張 `population.json` 的 private file mtime 作 receipt `fetchedAt`，重新驗證 parent folder 的 40-hex ID、raw response identity、`payloadSha256` 及 `sourcePointer=population.json`，再 atomic 寫 `identity.receipt.json`：

```powershell
.\.venv-backend\Scripts\python.exe pipelines\gemrate_source.py identity-receipts --resume
```

需要指定私有 cache root 時可加 `--cards-dir <private-cards-dir>`。輸出固定有 `attempted`、`succeeded`、`failed`、`cached`；任何 invalid raw／folder 只保留 failure reason 並不覆蓋 `population.json`。`--resume` 只有在既有 receipt 的 raw hash、private pointer 及完整 ID receipt 都再次驗證成功時才跳過，否則安全重建。

`api-dump --resume` 用相同 gate：就算 `population.json`／`history_full.json` 已存在，只有 `identity.receipt.json` 對該 raw payload 的 hash、`sourcePointer=population.json`、requested/entity/universal/grader IDs 都再次驗證成功先 cache；receipt 缺失或失效而 raw 仍合法時，會在同一個 offline pass atomic 重建 receipt，不 call API；raw 不可驗證才會重新 direct fetch。

### Daily operation

```powershell
.\.venv-backend\Scripts\python.exe scripts\backend.py daily
.\.venv-backend\Scripts\python.exe scripts\backend.py audit
.\.venv-backend\Scripts\python.exe scripts\backend.py status --json
```

The parent sequence is route validation → candidate/radar refresh → POP + exact price collection → immutable landing → normalize → MySQL transaction → ranking/alert derivation → audit → checkpoint. A partial GemRate source must leave the last-good checkpoint and public generation unchanged.

## 6. Manifest, receipt and promotion contract

Every collector run writes a private manifest. It must contain counts, run state, transport, timestamps, content references and safe failure receipts; it must never contain `GEMRATE_API_KEY`, request headers, response bodies, or full upstream URLs.

Example failure receipt:

```json
{
  "gemrateId": "opaque-exact-id",
  "httpStatus": 403,
  "reason": "unauthorized"
}
```

The only valid reaction is `partial=true`, `promotable=false`, and no cache/checkpoint promotion. A later successful run creates a new immutable manifest; it does not mutate the old failure receipt.

## 7. Rules that prevent silent data corruption

1. Match a printing by TCG + set + complete collector number + language + edition + parallel + finish. Any ambiguity enters `identity_review_queue`.
2. Store every numeric population with `grader`, `grade`, `effectiveDate`, `fetchedAt`, `transport`, `payloadSha256` and source pointer. Do not store a broad “total population” as PSA 10.
3. Keep `fetchedAt` separate from GemRate `data_last_updated`／date graded. Never manufacture daily history from fetch time.
4. Direct-vs-mirror disagreement on the same effective date is a failed run, not a precedence choice.
5. Missing means `unavailable`／`accumulating`; it never means `0`.
6. Grade10 mirror may produce current POP evidence only. It cannot create GemRate population history.
7. Current POP and history are data facts; market cap, rank, 1d/7d/30d and alerts are derived facts and are recomputed deterministically.

### Public-card identity receipt gate

The keyless `/card/{gemrateId}` route can provide a normalized provider identity
receipt (`year`, `set_name`, `card_number`, and `parallel`) alongside current
PSA 10 POP. Its `card_number` is provider evidence only: it can be complete,
short, or blank. `gemrate_candidate_backfill.py` routes that receipt through
`source_crosswalk.py`; it does **not** use a first search result or append a
missing collector-number suffix.

An automatically confirmed new printing needs an exact opaque GemRate ID, the
verified settled card route, a complete receipt card number, no conflict with
any retained candidate set/number/parallel evidence, one explicit language,
and retained explicit edition/finish evidence. If any field is missing or
ambiguous, the same receipt is retained in `identityReviewQueue` with a stable
reason such as `collector_number_conflict`, `language_conflict`, or
`finish_unavailable`. Review records are useful discovery evidence, but cannot
enter SNK price collection or a market-cap rank until resolved.

Confirmed public-card receipts are written atomically to the private
`gemrate-receipt-mappings.json` overlay. A duplicate opaque GemRate ID may fill
missing evidence only; conflicting populated identity evidence, or a later
receipt whose canonical printing key differs, enters review and never replaces
the earlier binding. The next candidate run loads this overlay before it builds
the roster, so the durable ID-to-printing binding survives source replays.

Direct API receipt aliases and public-page aliases follow the same no-rebind
rule. `entityGemrateId`, `universalGemrateId`, grader member IDs and spec IDs
remain private lookup aliases with their raw hash, relative source pointer and
capture freshness. A receipt can enrich an already compatible binding, but an
alias never creates or silently changes a CARDZ canonical printing key.

## 8. Troubleshooting

| Symptom | Meaning | Operator action |
|---|---|---|
| `401` / `403` direct API | secret expired, wrong, or not injected | repair secret injection; re-run `api-dump --resume` |
| direct `/card-details` 回 `403 unauthorized` | direct fetch 不受支援／缺少已驗證 card-page session | 不要重試 direct fetch；public-card-dump 只捕捉 `/card/{id}` 頁面自己發出的 exact-ID JSON，失敗才用同頁 DOM fallback |
| `--resume` 仍重新抓取舊 successful ID | 舊 cache 只有 normalized 檔，未有 raw receipt／hash | 這是預期的安全補齊；成功後才會視為完整 cache |
| public card page receipt failed | route／PSA row／GEM MINT／DOM hash 未完整 | 保留 receipt，不 promotion；用 direct API 或 Grade10 mirror，並排入 review |
| `partial current-population run` | one or more exact IDs unresolved | inspect manifest receipts and identity worklist; last-good remains active |
| direct/mirror mismatch | same-day GemRate evidence conflicts | quarantine the printing; resolve identity/effective date before next run |
| browser unavailable | Playwright/browser dependency missing | install pinned `pipelines/requirements.txt`, then `python -m playwright install chromium` |
| history missing | no direct API history available | show unavailable; never synthesize from current POP/mirror |

## 9. Relationship to G10 operator guide

`integrations/grade10/OPERATOR_GUIDE.md` explains the preserved upstream acquisition dependency. Its legacy analytics and synthetic K-line outputs are evidence/replay aids only; they are not CARDZ canonical population, price, sale, or candle facts. CARDZ’s canonical rules are this guide, `config/data-routing.json`, `docs/DATA_CONTRACT.md`, and `docs/DATA_CLEANING_RULES.md`.
