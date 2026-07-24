# CARDZ 前端數據契約（data contract）

前端所有數據一律經 `src/lib/data/market.ts` 進入，唔准喺 component 或 page 入面直接 fetch 數據源。後端接駁（R2 bucket → 日後 D1 / 外部 API）只需改 `src/lib/server-snapshot.ts` 嘅 `loadMarketSnapshot()`，上層契約不變。

## 入口函數（`src/lib/data/market.ts`）

| 函數 | 用途 | 回傳 |
|---|---|---|
| `getMarketData(scope)` | 首頁 / Pokémon / One Piece / Watchlist 列表 | `CardListPayload` |
| `getCardData(id)` | 單卡詳情頁 | `MarketCardView \| null` |
| `getGraderData(grader)` | 評級公司頁（PSA / BGS / CGC / SGC / TAG） | `CardListPayload` |

`CardListPayload = { generatedAt, effectiveAt, count, cards: MarketCardView[] }`

## REST API（`src/app/api/v1/`）

| Endpoint | 說明 |
|---|---|
| `GET /api/v1/market?scope=all\|pokemon\|one-piece\|watchlist` | 卡列表（預設 `all`） |
| `GET /api/v1/cards/{id}` | 單卡完整視圖 |
| `GET /api/v1/graders/{PSA\|BGS\|CGC\|SGC\|TAG}` | 該評級公司嘅卡列表（大小寫不拘） |

錯誤回應：`{ "error": string }`，未知 scope/grader 回 400，卡唔存在回 404。全部 endpoint `Cache-Control: public, max-age=60`。

## 核心型別（`src/lib/types.ts`）

- `MarketCardView` — 單卡視圖：`id`、`rank`、`tcg`、`language`、`collectorNumber`、`name`/`setName`/`story`（五語 `LocalizedText`）、`image`、`pricePsa10`、`populationPsa10`、`marketCap`、`graderPopulations`（每廠 `total` + `topGradePopulation` + `topGradePopulationChangePct`）、`windows["1d"|"7d"|"30d"]`（`changePct` + `trackedSales`）、`historyDaily: PricePoint[]`
- `MarketViewSnapshot` — 全量快照：`schemaVersion`、`generatedAt`、`effectiveAt`、`rates`（匯率表）、`top100`、`watchlist`
- `MarketMetric<T>` — 每個數值帶 `value`、`status`（`ready`/`stale`/`accumulating`/`unavailable`）、`asOf`；`status !== "ready"` 時 UI 用中性顯示，唔准當 0

## 數據語義規則（後端填數必守）

- **流量型**指標（價、成交額）先可以有 ±delta；**存量型**（population、收錄數）只升唔跌，`topGradePopulationChangePct` 唔准出現負值
- 冇數據用 `status: "unavailable"` + `value: null`，唔准填 0 或估算值頂替
- 卡名翻譯只准官方譯名或社群共識俗名，無共識保留英文
- 卡圖 URL 只接受 `/market-assets/[a-f0-9]{64}.webp`（`snapshot.ts` 會驗證，唔啱會落 placeholder）

## 後端接駁點

而家：`loadMarketSnapshot()` 從 Cloudflare R2 讀 `latest.json` pointer → snapshot JSON → `normaliseSnapshot()` → `MarketViewSnapshot`。接新後端時換呢個函數嘅實作就得，回傳型別維持 `MarketViewSnapshot`。
