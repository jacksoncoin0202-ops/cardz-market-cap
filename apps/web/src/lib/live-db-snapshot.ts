import "server-only";

import type {
  DailyHistoryPoint,
  LocalizedText,
  MarketMetric,
  PublicCard,
  PublicMarketSnapshot,
  WindowMetrics,
} from "@cardz/market-data";
import type { RowDataPacket } from "mysql2";
import mysql from "mysql2/promise";
import { normaliseSnapshot } from "./snapshot";
import { formatStoryForDisplay } from "./story-display";
import { currencies, type MarketViewSnapshot } from "./types";

const LOCALES = ["en", "zhTW", "zhCN", "ja", "ko"] as const;
const WINDOWS = { "1d": 1, "7d": 7, "30d": 30, "90d": 90, "180d": 180, "365d": 365 } as const;
const LONG_WINDOWS = new Set(["90d", "180d", "365d"]);
// 長窗寫入 bake 檔。1d/7d/30d 仍然 nearest-day；90/180/365 用 as-of，市值%唔作。

type DbRow = RowDataPacket & Record<string, unknown>;

/*
 * 下面 5 句 `require("node:fs"/"node:path")` 全部係**同步** lazy load，喺同步 function 入面。
 * ESM 對版係 `await import()`（server-snapshot.ts:137 就係咁），但佢會逼 `repoRoot()` /
 * `loadDbEnvironment()` / `loadSaleQuarantine(alreadyExcludedSaleIds)` 三個 function 變 async，跟住成條同步
 * call chain 一齊改 —— 係行為改動，唔係 lint 修。所以逐句 disable + 記住點解。
 */
function repoRoot(): string {
  // eslint-disable-next-line @typescript-eslint/no-require-imports -- 同步 lazy load；轉 `await import` 會令呢個 function 變 async（見上面 block 註）。
  const { basename, dirname, resolve } = require("node:path") as typeof import("node:path");
  const configuredRoot = process.env.CARDZ_REPO_ROOT?.trim();
  if (configuredRoot) return configuredRoot;
  const cwd = process.cwd();
  return basename(cwd) === "web" && basename(dirname(cwd)) === "apps"
    ? resolve(cwd, "../..")
    : cwd;
}

function loadDbEnvironment(): void {
  if (process.env.CARDZ_DB_PASSWORD && process.env.CARDZ_DB_USER && process.env.CARDZ_DB_NAME) return;
  // eslint-disable-next-line @typescript-eslint/no-require-imports -- 同步 lazy load（見 repoRoot 上面嘅 block 註）。
  const { readFileSync } = require("node:fs") as typeof import("node:fs");
  // eslint-disable-next-line @typescript-eslint/no-require-imports -- 同上。
  const { resolve } = require("node:path") as typeof import("node:path");
  const contents = readFileSync(resolve(repoRoot(), "data/runtime/config/backend.env"), "utf8");
  for (const raw of contents.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const separator = line.indexOf("=");
    if (separator < 1) continue;
    const key = line.slice(0, separator).trim();
    const value = line.slice(separator + 1).trim();
    if (key.startsWith("CARDZ_DB_") && process.env[key] === undefined) process.env[key] = value;
  }
}

// PC 成交 title↔卡號矛盾隔離 receipt（runbook 形狀 29，v1326 Latias +556% 事故）。
// sales landing 冇 status 欄、acceptance append-only、sales history 係 VIEW，
// 所以隔離用 ledger 形式：pipelines/pc_sale_title_quarantine.py 用判別器
// （c11_pc_sold_ingest.title_collector_contradiction，一個概念一份實現）重新
// 生成，呢度淨係讀 receipt 扣數，唔准喺 TS 再抄一次判別邏輯。
// 檔案唔存在就 throw：靜靜咁 fail-open 出街 = 毒數照出，寧願 bake 死。
// receipt 過期就由 scripts/test_price_lane_contracts.py 嘅 DB gate 兜住。
function loadSaleQuarantine(alreadyExcludedSaleIds: ReadonlySet<number>): Map<string, { valueUsd: number; count: number }> {
  // eslint-disable-next-line @typescript-eslint/no-require-imports -- 同步 lazy load（見 repoRoot 上面嘅 block 註）。
  const { readFileSync } = require("node:fs") as typeof import("node:fs");
  // eslint-disable-next-line @typescript-eslint/no-require-imports -- 同上。
  const { resolve } = require("node:path") as typeof import("node:path");
  const receiptPath = resolve(repoRoot(), "data/runtime/operator/audit/pc_sale_title_quarantine_current.json");
  const doc = JSON.parse(readFileSync(receiptPath, "utf8")) as {
    entries: Array<{ saleObservationId: number; variantId: number; observedDate: string; transactionValueUsd: number | null; quantity: number | null }>;
  };
  const excluded = new Map<string, { valueUsd: number; count: number }>();
  for (const entry of doc.entries) {
    // 058 之後 DB view 已經將 market_pc_sale_title_quarantine 入面嘅成交剔走；
    // 呢度再扣一次就係雙重扣減（有真成交嗰日會被扣到偏低／歸零）。只扣 DB 未識嘅。
    if (alreadyExcludedSaleIds.has(Number(entry.saleObservationId))) continue;
    const key = `${entry.variantId}|${entry.observedDate}`;
    const slot = excluded.get(key) ?? { valueUsd: 0, count: 0 };
    slot.valueUsd += entry.transactionValueUsd ?? 0;
    slot.count += entry.quantity ?? 0;
    excluded.set(key, slot);
  }
  return excluded;
}

async function loadDbExcludedSaleIds(connection: mysql.Connection): Promise<Set<number>> {
  // 058 之前嘅 DB 冇呢張表：ER_NO_SUCH_TABLE = 乜都未剔走，全靠 receipt 扣。
  try {
    const [rows] = await connection.query<DbRow[]>(`
      SELECT sale_observation_id FROM market_pc_sale_title_quarantine
    `);
    return new Set(rows.map((row) => Number(row.sale_observation_id)));
  } catch (error) {
    if ((error as { code?: string }).code === "ER_NO_SUCH_TABLE") return new Set<number>();
    throw error;
  }
}

function iso(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  const date = value instanceof Date ? value : new Date(String(value));
  return Number.isNaN(date.valueOf()) ? null : date.toISOString().replace(".000Z", "Z");
}

function day(value: unknown): string | null {
  const valueIso = iso(value);
  return valueIso?.slice(0, 10) ?? null;
}

function numberValue(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function readyMetric(value: number | null, asOf: string | null): MarketMetric {
  return { value, status: value === null ? "unavailable" : "ready", asOf: value === null ? null : asOf };
}

function localized(): LocalizedText {
  return { en: null, zhTW: null, zhCN: null, ja: null, ko: null };
}

type AnchorCandidate = { at: string; priceUsd: number; sourceCode: string | null };

// 錨同現價要同一個基準先至叫「變化」。實測 variant 1（梵高）：現價係
// pricecharting 指導價 $2,836，而 08-09/08-10 嘅 snkrdunk 日線係 ¥156,000
// （$969，PSA10 賣晒之後最低掛價變咗第二樣嘢），nearest 政策揀咗佢做 1d 錨，
// 熱力圖出咗個 +192.6% 嘅假暴升；7d/30d 嘅 PC-vs-SNK 數同樣係兩間市場嘅基差，
// 唔係市場變動。所以跨 marketplace 嘅參考價點唔准做錨。但唔可以就咁 skip 咗
// 嗰日：SNK 日線好密，佢霸住咗個 day point，當日真實 PSA10 成交（sales lane
// 一直喺 point 度）會被遮蔽——冇咗佢，梵高連有 3 單成交嘅 7d 都會冤枉變灰。
// 於是每日出一個候選：參考點同源（或本身係 *_sales 升格點）就用佢；唔同源
// 就退去當日成交均價（同 :444 升格邏輯同一份證據、同一個條件）；兩樣都冇
// 先至冇候選。真成交 vs 指導價係本來就接受嘅比較，sourceSwitched 照 flag
// 俾 UI 提示。
// 056→sale lane：`pricecharting_sales` / `snkrdunk_sales` 係同一個供應商嘅成交升格碼，
// 同 chart 觀測點（`pricecharting` / `snkrdunk`，market_price_observation 嗰邊永遠係母碼）
// 比較時要當同一條 lane。唔剝尾碼：全板 currentSource 永遠對唔中任何 history 點——
// EN 卡嘅日線由 PC 點靜靜變咗 SNK 點（ladder tier 1 < 2），1d/7d 變幅嘅錨點亦由
// chart 退晒去成交均價，頁面照出、零 error。test-fe-sale-date-label T5 釘住兩個 call site。
// R6（2026-08-24）之後上面兩段變成歷史紀錄：history 已經冇 chart 觀測點，剩返成交點，
// 所以 chartLaneOf 而家嘅工作淨係「錨（`<lane>_sales`）同現價 quote（`<lane>_sales`）
// 剝返同一個母碼再比」，跨 marketplace 嗰個假暴升由源頭消失。
// 2026-09-25 收緊：上面「唔同源就退去當日成交均價」同「`*_sales` 升格點照收」兩條後門一齊封。
// 錨一定要同現價同一條 lane；另一個市場嘅成交、同埋多源嗰日嘅 `exact_psa10_sales` 均價都唔准。
// 實測 Mimikyu（#26）現價係 SNKRDUNK 成交 $21,063，180d 錨咗 PC 一單 $95（03-28，錯卡成交），
// 出街 +22,072%；Celebrations 噴火龍（#54）現價 $450，90d 錨咗另一條 lane 嘅 $20,500，出 −97.8%。
// 同 lane 冇錨就照灰（accumulating）。currentSource 係 null 嗰陣（例如
// test-fe-default-window-coverage 對住已剝碼嘅出街 payload 行）照舊唔揀 lane。
function chartLaneOf(sourceCode: unknown): string | null {
  const code = String(sourceCode ?? "").trim();
  if (!code) return null;
  return code.endsWith("_sales") ? code.slice(0, -"_sales".length) : code;
}

function anchorCandidate(
  point: DailyHistoryPoint,
  currentSource: string | null,
): AnchorCandidate | null {
  const source = (point as DailyHistoryPoint & { priceSourceCode?: string | null }).priceSourceCode ?? null;
  // 現價有 lane 嗰陣，錨要講得出自己係同一條 lane：冇 source 嘅點（連下面成交均價後備）一樣唔准。
  if (currentSource && (!source || chartLaneOf(source) !== currentSource)) return null;
  if (point.priceUsd !== null) {
    return { at: point.at, priceUsd: point.priceUsd, sourceCode: source };
  }
  const count = point.trackedSalesCount;
  const value = point.trackedSalesValueUsd;
  if (count !== null && count > 0 && value !== null && value > 0) {
    // 行到呢度個點已經過咗 lane 閘，均價就係同一條 lane 嘅；標返 exact_psa10_sales 會令
    // sourceSwitched 無端端 true，validate_daily_release.py 當跨 lane 擋低成個 release。
    return { at: point.at, priceUsd: value / count, sourceCode: currentSource ? source : "exact_psa10_sales" };
  }
  return null;
}

function nearestPrice(
  history: DailyHistoryPoint[],
  targetMs: number,
  toleranceDays: number,
  beforeMs: number,
  currentSource: string | null,
): AnchorCandidate | null {
  let winner: AnchorCandidate | null = null;
  let winnerDelta = Number.POSITIVE_INFINITY;
  for (const point of history) {
    const candidate = anchorCandidate(point, currentSource);
    if (candidate === null) continue;
    const at = new Date(candidate.at).valueOf();
    // 錨必須嚴格舊過而家嗰個 as-of。1d 個 tolerance 係 2 日（下面 :126）而
    // daysBack 得 1 日，所以卡自己嗰個 current price point 落喺錨嘅容忍窗入面
    // （delta 啱啱好 1.0）—— 冇更近嘅舊點嗰陣佢就贏，變成攞自己同自己比，
    // 出一個 status:"ready" 嘅 0.00%。實測 393 張卡（372 pricecharting + 21
    // snkrdunk）就係咁，錨價同頭條價 byte 相同。我哋冇嗰 24 小時嘅證據，就唔應該
    // 出嗰個數。
    if (at >= beforeMs) continue;
    const delta = Math.abs(at - targetMs) / 86_400_000;
    if (delta <= toleranceDays && delta < winnerDelta) {
      winner = candidate;
      winnerDelta = delta;
    }
  }
  return winner;
}

// 30d 帶內（25–35 日前）搵唔到錨，唔代表「唔知 30 日前價錢係幾多」——價格
// 係 step function：30 日前嘅價 = 嗰一刻嘅最後已知價（股票圖星期一計 1d 變化
// 用星期五收市價，同一個道理）。PC 月線每月 1 號先郁一次，所以每個月嘅尾段
// 「啱啱好 30±5 日前」永遠冇點落喺帶內——帶內政策同月線源頭嘅週期天生相沖
//（同 40 日 freshness cutoff 要遷就月線係同一個理由）。所以 30d 帶內落空時，
// 准退去帶前最後一個同基準點：梵高喺 08-12 會攞 07-01 月線 $2,850 對現價
// $2,836 出 −0.5%，係真·月對月變化，唔係發明數。1d 唔跟（:117 嘅 24 小時
// 證據原則照企），7d 都唔跟（周對周退到月線會出假 0.0%，週期唔匹配）。
// 乜舊點都冇（上市未夠一個月）嘅卡照灰——嗰個先至係真「資料累積中」。
function latestBefore(
  history: DailyHistoryPoint[],
  beforeMs: number,
  currentSource: string | null,
): AnchorCandidate | null {
  let winner: AnchorCandidate | null = null;
  let winnerMs = Number.NEGATIVE_INFINITY;
  for (const point of history) {
    const candidate = anchorCandidate(point, currentSource);
    if (candidate === null) continue;
    const at = new Date(candidate.at).valueOf();
    if (at < beforeMs && at > winnerMs) {
      winner = candidate;
      winnerMs = at;
    }
  }
  return winner;
}

function percentage(current: number | null, previous: number | null): number | null {
  return current === null || previous === null || previous === 0
    ? null
    : (current / previous - 1) * 100;
}

function salesTotal(history: DailyHistoryPoint[], endMs: number, days: number): { value: number; count: number; asOf: string } | null {
  // historyDaily 一日一點，時間固定係 UTC 00:00；currentAsOf 就保留成交／檢查嘅
  // 時分秒。兩邊唔先對齊日桶，1d 會變成 [20:46, 20:46]，當日 00:00 個真成交
  // 點永遠入唔到窗，7d / 30d 亦會各自漏咗最早一日。
  const endDayMs = new Date(endMs).setUTCHours(0, 0, 0, 0);
  const startMs = endDayMs - (days - 1) * 86_400_000;
  const dated = history.map((point) => ({ point, at: new Date(point.at).valueOf() }));
  if (dated.some(({ point, at }) => !Number.isFinite(at)
    && point.salesCoverage !== "unavailable")) return null;
  const rows = dated
    .filter(({ point, at }) => Number.isFinite(at)
      && at >= startMs
      && at <= endDayMs
      && point.salesCoverage !== "unavailable")
    .sort((left, right) => left.at - right.at);
  if (rows.length === 0) return null;
  // A covered day with no numeric aggregate is unknown, not a verified zero.
  // Returning null keeps the whole window honest instead of biasing it low.
  if (rows.some(({ point }) => !Number.isFinite(point.trackedSalesValueUsd)
    || !Number.isFinite(point.trackedSalesCount))) return null;
  return {
    value: rows.reduce((sum, { point }) => sum + (point.trackedSalesValueUsd as number), 0),
    count: rows.reduce((sum, { point }) => sum + (point.trackedSalesCount as number), 0),
    asOf: rows[rows.length - 1].point.at,
  };
}

/*
 * 混合錨政策（R6b，owner 2026-08-24）—— 短窗同長窗**唔同**規矩：
 *  · 1d / 7d / 30d：只認 `history`（真成交）。`reference`（K 線／
 *    `market_price_observation`）一個都唔准做錨。呢個係 R6 原本要斬嗰個病：
 *    真成交 $2,000 → $3,100 應該 +55%，錨咗 K 線出 +8.8%，冇 error 冇 warning。
 *  · 90d / 180d / 365d：有真成交錨就用真成交，**淨係**喺冇嘅時候先退去參考點。
 *    成交系列嘅最早一點中位數得 74 日大，純成交嘅 180d 只錨得住 165/1604（10.3%）；
 *    混合之後 1,593/1604（99.3%）。
 * 「現價」嗰邊任何情況都係最新一單真成交推出嚟嘅 `currentPrice`，唔經呢兩條 series。
 * 市值變幅照舊只喺短窗作（`inventCap`），所以參考錨永遠生唔出市值變幅。
 * 條界由 `scripts/test-fe-hybrid-window-anchor.mjs` 用 mutation 探針釘住。
 */
/*
 * 離晒譜嘅變幅唔出街（2026-09-25；owner 2026-07-29 規矩：Top100 升跌異常 fail-closed，可信 > 覆蓋）。
 * 錨同現價差過呢個倍數（升或跌對稱），個錨幾乎一定係錯卡／錯級成交混咗入日線：實測
 * Latias & Latios GX 170/181（#12）90d 錨咗 06-27 一單 $1,485（前後兩日 $17,100／$17,700），
 * 出街 +916.8%。日線未有離群帶（見 R6-OPEN-TICKET），呢度唔係離群帶，係出街閘：唔改錨、
 * 唔揀第二個點，淨係唔出個數，標 unavailable。短窗 3 倍即係「|30d| > 200% 唔准 ready」；
 * 長窗真係會有大郁（新卡上市、OP 熱潮），所以逐級放寬。
 * scripts/validate_daily_release.py 用同一組數對住將要出街嘅 snapshot 再驗一次。
 */
const MAX_WINDOW_RATIO: Record<keyof typeof WINDOWS, number> = {
  "1d": 3, "7d": 3, "30d": 3, "90d": 4, "180d": 5, "365d": 7,
};

function windowMetrics(
  history: DailyHistoryPoint[],
  currentPrice: number | null,
  currentPopulation: number | null,
  currentAsOf: string | null,
  currentSource: string | null,
  reference: DailyHistoryPoint[] = [],
): Record<keyof typeof WINDOWS, WindowMetrics> {
  const currentMs = currentAsOf ? new Date(currentAsOf).valueOf() : Date.now();
  const currentCap = currentPrice === null || currentPopulation === null ? null : currentPrice * currentPopulation;
  return Object.fromEntries(Object.entries(WINDOWS).map(([code, daysBack]) => {
    const tolerance = code === "1d" ? 2 : code === "7d" ? 3 : 5;
    const targetMs = currentMs - daysBack * 86_400_000;
    // 帶內空咗先輪到 step-function 後備（見 latestBefore 註釋）。帶內冇點
    // ⇒ (target−5d, target+5d) 全空 ⇒「最後一個 < target−5d 嘅點」就係
    //「最後一個 ≤ target 嘅點」，即係標準 as-of 語義，冇偷步。
    const saleAnchor = LONG_WINDOWS.has(code)
      ? latestBefore(history, targetMs + 1, currentSource)
      : nearestPrice(history, targetMs, tolerance, currentMs, currentSource)
        ?? (code === "30d" ? latestBefore(history, targetMs - tolerance * 86_400_000, currentSource) : null);
    const found = saleAnchor
      ?? (LONG_WINDOWS.has(code) ? latestBefore(reference, targetMs + 1, currentSource) : null);
    // 現價或錨 ≤ 0 算唔出倍數，一樣唔出街（唔係 −100%）。
    const implausible = found !== null && currentPrice !== null && (currentPrice <= 0 || found.priceUsd <= 0
      || Math.max(currentPrice / found.priceUsd, found.priceUsd / currentPrice)
        > MAX_WINDOW_RATIO[code as keyof typeof WINDOWS]);
    const anchor = implausible ? null : found;
    const priceChange = percentage(currentPrice, anchor?.priceUsd ?? null);
    // 錨點而家一律係同 lane 嘅成交點（`<lane>_sales`），
    // 而 currentSource 已經行過 chartLaneOf（母碼）。唔剝尾碼比較 = 全板 1,604
    // 張卡永遠 anchorSource !== currentSource，UI 掛住一個假嘅「換咗來源」提示。
    const anchorSource = chartLaneOf(anchor?.sourceCode ?? null);
    const sourceSwitched = Boolean(anchorSource && currentSource && anchorSource !== currentSource);
    const inventCap = !LONG_WINDOWS.has(code);
    const anchorCap = !inventCap || anchor === null || currentPopulation === null
      ? null
      : anchor.priceUsd * currentPopulation;
    const capChange = inventCap ? percentage(currentCap, anchorCap) : null;
    const currentSales = salesTotal(history, currentMs, daysBack);
    const previousSales = salesTotal(history, currentMs - daysBack * 86_400_000, daysBack);
    const salesChange = percentage(currentSales?.value ?? null, previousSales?.value ?? null);
    const accumulating = currentPrice === null || implausible ? "unavailable" : "accumulating";
    return [code, {
      changePct: {
        value: priceChange,
        status: priceChange === null ? accumulating : "ready",
        asOf: priceChange === null ? null : currentAsOf,
        // 供應商代號唔出街，連 field 都唔要：呢個 snapshot 會原封不動 ship 落
        // client payload，睇 view-source 就見到。UI 只需要 sourceSwitched 呢個
        // boolean（下面），佢由 anchorSource 計出嚟但唔帶住個名。
        sourceSwitched: priceChange === null ? false : sourceSwitched,
      },
      marketCapChangePct: {
        value: capChange,
        status: capChange === null ? accumulating : "ready",
        asOf: capChange === null ? null : currentAsOf,
        sourceSwitched: capChange === null ? false : sourceSwitched,
      },
      trackedSalesChangePct: {
        value: salesChange,
        status: salesChange === null ? (currentSales ? "accumulating" : "unavailable") : "ready",
        asOf: salesChange === null ? null : currentSales?.asOf ?? null,
      },
      trackedSales: {
        valueUsd: readyMetric(currentSales?.value ?? null, currentSales?.asOf ?? null),
        count: readyMetric(currentSales?.count ?? null, currentSales?.asOf ?? null),
        coverage: currentSales ? "partial" : "unavailable",
        asOf: currentSales?.asOf ?? null,
      },
    } satisfies WindowMetrics];
  })) as Record<keyof typeof WINDOWS, WindowMetrics>;
}

async function openConnection(): Promise<mysql.Connection> {
  loadDbEnvironment();
  return mysql.createConnection({
    host: process.env.CARDZ_DB_HOST?.trim() || "127.0.0.1",
    port: Number(process.env.CARDZ_DB_PORT || 3308),
    user: process.env.CARDZ_DB_USER,
    password: process.env.CARDZ_DB_PASSWORD,
    database: process.env.CARDZ_DB_NAME,
    timezone: "Z",
    decimalNumbers: true,
    connectTimeout: 5_000,
  });
}

async function currentGenerationHash(): Promise<string> {
  const connection = await openConnection();
  try {
    const [generationRows] = await connection.query<DbRow[]>(`
      SELECT ranking_generation_sha256,MAX(accepted_at) AS accepted_at
      FROM market_canonical_metric_acceptance
      GROUP BY ranking_generation_sha256
      ORDER BY accepted_at DESC
      LIMIT 1
    `);
    const generationHash = String(generationRows[0]?.ranking_generation_sha256 ?? "");
    if (!/^[0-9a-f]{64}$/.test(generationHash)) throw new Error("3308 has no current canonical ranking generation");
    return generationHash;
  } finally {
    await connection.end();
  }
}

// Rebuilding the full snapshot costs seconds; every request pays only the
// one-row generation probe and the snapshot is rebuilt when the current
// ranking generation flips.
let cachedSnapshot: { generationHash: string; snapshot: MarketViewSnapshot } | null = null;
let inflightBuild: { generationHash: string; promise: Promise<MarketViewSnapshot> } | null = null;

export async function loadLiveDbSnapshot(): Promise<MarketViewSnapshot> {
  const generationHash = await currentGenerationHash();
  if (cachedSnapshot?.generationHash === generationHash) return cachedSnapshot.snapshot;
  if (inflightBuild?.generationHash === generationHash) return inflightBuild.promise;
  const promise = buildLiveDbSnapshot(generationHash)
    .then((snapshot) => {
      cachedSnapshot = { generationHash, snapshot };
      return snapshot;
    })
    .finally(() => {
      if (inflightBuild?.promise === promise) inflightBuild = null;
    });
  inflightBuild = { generationHash, promise };
  return promise;
}

async function buildLiveDbSnapshot(generationHash: string): Promise<MarketViewSnapshot> {
  const connection = await openConnection();
  try {
    const [universeRows] = await connection.query<DbRow[]>(`
      SELECT member_count
      FROM market_universe_lock
      WHERE is_current=1
    `);
    if (universeRows.length !== 1) {
      throw new Error(`3308 must have exactly one current universe lock, found ${universeRows.length}`);
    }
    const memberCount = Number(universeRows[0].member_count);
    if (!Number.isSafeInteger(memberCount) || memberCount < 1) {
      throw new Error(`3308 current universe member_count is invalid: ${universeRows[0].member_count}`);
    }
    const [coreRows] = await connection.query<DbRow[]>(`
      SELECT
        metric.variant_id,
        -- 公開 id ≠ 內部 opaque_id。有 4 行 opaque_id 帶住供應商前綴（D7 凍結，
        -- 改唔到），public_card_alias 幫佢哋鑄咗個乾淨嘅公開 id。冇 alias 嘅卡
        -- 行為完全不變。理由喺 migration 041。
        COALESCE(alias.public_id,variant.opaque_id) AS opaque_id,
        variant.canonical_name,metric.canonical_market_rank,
        variant.set_name AS variant_set_name,
        variant.collector_number AS variant_collector_number,
        printing.tcg_code,printing.card_language,printing.collector_number,
        printing.set_name,printing.set_code,printing.edition_code,printing.finish_code,
        printing.identity_status,printing.canonical_printing_sha256,
        printing.evidence_sha256 AS printing_evidence_sha256,
        quote.price_usd AS psa10_price_usd,
        quote.source_period_at AS price_observed_date,
        quote.source_period_at AS price_source_period_at,
        COALESCE(quote.checked_at, source_observation.observed_at) AS price_checked_at,
        quote.checked_at AS price_effective_at,
        COALESCE(quote.checked_at, source_observation.observed_at) AS price_observed_at,
        CASE
          WHEN quote.source_code IN ('snk','snk_psa10') THEN 'snkrdunk'
          ELSE quote.source_code
        END AS price_source_code,
        population.top_grade_population AS psa10_population,
        population.effective_at AS population_effective_at,
        metric.market_cap_usd,metric.accepted_at AS metric_accepted_at
      FROM market_canonical_metric_acceptance metric
      INNER JOIN catalog_variant variant ON variant.id=metric.variant_id
      LEFT JOIN public_card_alias alias ON alias.variant_id=metric.variant_id
      INNER JOIN catalog_printing_identity printing ON printing.variant_id=metric.variant_id
      INNER JOIN operator_resolved_canonical_metric_quote quote
        ON quote.metric_acceptance_id=metric.id
      LEFT JOIN market_source_observation source_observation
        ON source_observation.id=quote.source_observation_id
      INNER JOIN market_metric_history_acceptance population_history ON population_history.id=metric.population_history_acceptance_id
      INNER JOIN market_grader_population_observation population ON population.id=population_history.source_record_id
      WHERE metric.ranking_generation_sha256=?
        AND quote.price_usd IS NOT NULL
      ORDER BY metric.canonical_market_rank IS NULL,
               metric.canonical_market_rank,metric.variant_id
    `, [generationHash]);
    if (coreRows.length === 0) throw new Error("3308 current ranking generation is empty");
    if (coreRows.length !== memberCount) {
      throw new Error(`3308 ranking rows=${coreRows.length}, universe members=${memberCount}`);
    }
    const variantIds = coreRows.map((row) => Number(row.variant_id));
    const placeholders = variantIds.map(() => "?").join(",");

  const [localeRows, imageRows, fallbackImageRows, rawRows, referenceRows, salesRows, fxRows] = await Promise.all([
      connection.query<DbRow[]>(`
        SELECT variant_id,locale_code,localized_name,localized_set_name,market_story,observed_at
        FROM catalog_variant_locale WHERE variant_id IN (${placeholders})
        ORDER BY variant_id,locale_code
      `, variantIds),
      connection.query<DbRow[]>(`
        SELECT variant_id,canonical_image_content_sha256,canonical_image_width,canonical_image_height,
          canonical_image_qc_at,canonical_image_source_observed_at
        FROM operator_canonical_image_projection WHERE variant_id IN (${placeholders})
      `, variantIds),
      connection.query<DbRow[]>(`
        SELECT f.variant_id,
          a.content_sha256 AS canonical_image_content_sha256,
          a.width_px AS canonical_image_width,a.height_px AS canonical_image_height,
          q.checked_at AS canonical_image_qc_at,
          ca.accepted_at AS canonical_image_source_observed_at
        FROM operator_binding_freeze f
        INNER JOIN market_canonical_image_acceptance ca
          ON ca.id=f.canonical_image_acceptance_id AND ca.variant_id=f.variant_id
        INNER JOIN market_image_asset a
          ON a.id=ca.image_asset_id AND a.variant_id=ca.variant_id AND a.image_kind='raw_front'
         AND a.content_sha256=f.content_sha256
        INNER JOIN market_image_qc q
          ON q.image_asset_id=a.id
         AND q.id=(SELECT q2.id FROM market_image_qc q2
                   WHERE q2.image_asset_id=a.id
                   ORDER BY q2.checked_at DESC,q2.id DESC LIMIT 1)
         AND q.public_allowed=1 AND q.raw_front_confirmed=1 AND q.card_number_match=1
         AND q.language_match=1 AND q.tcg_match=1
         AND q.semantic_match_status IN ('human_or_vision_confirmed','accepted_freeze')
        WHERE f.variant_id IN (${placeholders})
          AND f.freeze_kind='image' AND f.acceptance_status='accepted'
          AND f.canonical_image_acceptance_id IS NOT NULL
          AND f.content_sha256 REGEXP '^[0-9a-f]{64}$'
          AND NOT EXISTS (
            SELECT 1 FROM market_canonical_image_acceptance newer
            WHERE newer.supersedes_acceptance_id=ca.id
          )
      `, variantIds),
      connection.query<DbRow[]>(`
        SELECT raw.variant_id,raw.price_usd,raw.observed_at
        FROM market_ungraded_reference_price raw
        INNER JOIN (
          SELECT variant_id,MAX(id) AS id FROM market_ungraded_reference_price
          WHERE variant_id IN (${placeholders}) GROUP BY variant_id
        ) latest ON latest.id=raw.id
      `, variantIds),
      /*
       * 參考點（K 線）讀路 —— R6b（owner 2026-08-24）行返嚟，但**只**餵
       * `historyReference` 呢條獨立 series，唔准掂 `historyDaily`。
       * 用途得兩個，兩個都寫死喺 code：
       *   ① 長窗（90/180/365）冇真成交錨嗰陣做後備錨（`windowMetrics` 個 `reference` 參數）。
       *   ② 卡頁長時段圖表「最早一單真成交」之前嗰段深歷史（`mergeReferenceHistory`）。
       * 短窗（1d/7d/30d）同 `historyDaily` 一個字都唔准用佢。
       */
      connection.query<DbRow[]>(`
        SELECT history.id,price.variant_id,price.observed_date,price.price_usd,price.effective_at,
          CASE WHEN price.source_code IN ('snk','snk_psa10') THEN 'snkrdunk' ELSE price.source_code END AS source_code
        FROM market_metric_history_acceptance history
        INNER JOIN market_price_observation price ON history.source_record_type='market_price_observation'
          AND history.source_record_id=price.id AND history.variant_id=price.variant_id
        WHERE history.metric_kind='psa10_price' AND history.variant_id IN (${placeholders})
          -- acceptance 行係 append-only，可以指住事後被隔離嘅觀測（quarantined /
          -- quarantined_lane / banned_g10_kline）。SQL 讀模全部（023/026/028/032）
          -- 都係 metric_status='ready' 先出街；呢條 TS 讀路一直冇跟，2026-08-12
          -- ad-hoc lane 事故先發現。等值 filter：新隔離字自動 fail-closed。
          -- （scripts/test_snk_price_lane_audit.py 第 4 段釘住呢句。）
          AND price.metric_status='ready'
        ORDER BY price.variant_id,price.observed_date,price.effective_at,history.id
      `, variantIds),
      connection.query<DbRow[]>(`
        SELECT variant_id,observed_date,sales_count,sales_value_usd,sales_coverage_status,
          sales_verified_zero,sales_source_codes,sales_evidence_at
        FROM operator_accepted_psa10_sales_history
        WHERE variant_id IN (${placeholders}) ORDER BY variant_id,observed_date
      `, variantIds),
      connection.query<DbRow[]>(`
        SELECT rate.quote_currency,rate.rate,rate.effective_at
        FROM market_fx_rate_observation rate
        INNER JOIN (
          SELECT quote_currency,MAX(effective_at) AS effective_at
          FROM market_fx_rate_observation WHERE base_currency='USD' GROUP BY quote_currency
        ) latest ON latest.quote_currency=rate.quote_currency AND latest.effective_at=rate.effective_at
        WHERE rate.base_currency='USD'
      `),
    ]);

    const locales = new Map<number, { names: LocalizedText; sets: LocalizedText; stories: LocalizedText; observedAt: string | null }>();
    for (const row of localeRows[0]) {
      const variantId = Number(row.variant_id);
      const target = locales.get(variantId) ?? { names: localized(), sets: localized(), stories: localized(), observedAt: null };
      const locale = String(row.locale_code) as (typeof LOCALES)[number];
      if (LOCALES.includes(locale)) {
        target.names[locale] = String(row.localized_name ?? "").trim() || null;
        target.sets[locale] = String(row.localized_set_name ?? "").trim() || null;
        target.stories[locale] = formatStoryForDisplay(String(row.market_story ?? "")) ;
      }
      const observedAt = iso(row.observed_at);
      if (observedAt && (!target.observedAt || observedAt > target.observedAt)) target.observedAt = observedAt;
      locales.set(variantId, target);
    }
    const byVariant = <T extends DbRow>(rows: T[]): Map<number, T> => new Map(rows.map((row) => [Number(row.variant_id), row]));
    const images = byVariant(imageRows[0]);
    // The canonical image view is the normal fast path.  This narrow fallback
    // covers an already accepted/frozen image whose source lineage was added
    // after that view's older acceptance chain, without broad product-view
    // expansion on every 3800 request.
    for (const row of fallbackImageRows[0]) {
      const variantId = Number(row.variant_id);
      if (!images.has(variantId)) images.set(variantId, row);
    }
    const rawPrices = byVariant(rawRows[0]);

    /*
     * 參考 series（K 線）—— 同 `historyDaily` **完全分家**嘅第二條 series（R6b）。
     * 佢有自己嘅 map、自己嘅 draft type，**唔會**經下面嗰個 `getPoint`，即係由構造上
     * 塞唔入 `histories`；下面成段 sale-only history builder 一個字都冇改（R6 嗰段照留，
     * `scripts/test-fe-history-sale-only.mjs` 抽緊嗰段源碼行）。
     * 一日一點：同一日多過一個觀測就跟 lane 優先（同現價同源 > snkrdunk >
     * pricecharting > 其他），同 R6 之前嗰個 ladder 一模一樣。
     */
    type ReferenceDraft = DailyHistoryPoint & {
      priceSourceCode: string | null;
      priceSourcePriority: number;
    };
    const currentSourceByVariant = new Map(coreRows.map((row) => [Number(row.variant_id), chartLaneOf(row.price_source_code) ?? ""]));
    const references = new Map<number, Map<string, ReferenceDraft>>();
    for (const row of referenceRows[0]) {
      const variantId = Number(row.variant_id);
      const observedDate = day(row.observed_date);
      if (!observedDate) continue;
      const priceUsd = numberValue(row.price_usd);
      if (priceUsd === null) continue;
      const sourceCode = String(row.source_code);
      const priceSourcePriority = sourceCode === currentSourceByVariant.get(variantId)
        ? 0
        : sourceCode === "snkrdunk"
          ? 1
          : sourceCode === "pricecharting"
            ? 2
            : 3;
      const byDate = references.get(variantId) ?? new Map<string, ReferenceDraft>();
      references.set(variantId, byDate);
      const existing = byDate.get(observedDate);
      if (existing && existing.priceSourcePriority <= priceSourcePriority) continue;
      byDate.set(observedDate, {
        at: `${observedDate}T00:00:00Z`,
        priceUsd,
        priceStatus: "ready",
        trackedSalesValueUsd: null,
        trackedSalesCount: null,
        salesCoverage: "unavailable",
        salesVerifiedZero: false,
        priceSourceCode: sourceCode,
        priceSourcePriority,
      });
    }

    type HistoryDraft = DailyHistoryPoint & {
      priceSourceCode: string | null;
      priceSourcePriority: number;
    };
    const histories = new Map<number, Map<string, HistoryDraft>>();
    const getPoint = (variantId: number, date: string): HistoryDraft => {
      const variant = histories.get(variantId) ?? new Map<string, HistoryDraft>();
      histories.set(variantId, variant);
      const point = variant.get(date) ?? {
        at: `${date}T00:00:00Z`, priceUsd: null, priceStatus: "unavailable",
        trackedSalesValueUsd: null, trackedSalesCount: null, salesCoverage: "unavailable",
        salesVerifiedZero: false, priceSourceCode: null, priceSourcePriority: Number.POSITIVE_INFINITY,
      };
      variant.set(date, point);
      return point;
    };
    const saleQuarantine = loadSaleQuarantine(await loadDbExcludedSaleIds(connection));
    for (const row of salesRows[0]) {
      const observedDate = day(row.observed_date);
      if (!observedDate) continue;
      const point = getPoint(Number(row.variant_id), observedDate);
      // 扣除 title↔卡號矛盾隔離 receipt 嘅貢獻（PC fuzzy match 塞錯卡嘅成交）。
      // 淨低仲有真成交就照出真嗰部分；扣到零就當嗰日冇 tracked sales。
      const quarantined = saleQuarantine.get(`${Number(row.variant_id)}|${observedDate}`);
      let dayValue = numberValue(row.sales_value_usd);
      let dayCount = numberValue(row.sales_count);
      if (quarantined && dayValue !== null && dayCount !== null) {
        dayValue = Math.max(0, dayValue - quarantined.valueUsd);
        dayCount = Math.max(0, dayCount - quarantined.count);
        if (dayValue <= 0 || dayCount <= 0) {
          dayValue = null;
          dayCount = null;
        }
      }
      point.trackedSalesValueUsd = dayValue;
      point.trackedSalesCount = dayCount;
      point.salesCoverage = String(row.sales_coverage_status || "unavailable") as DailyHistoryPoint["salesCoverage"];
      point.salesVerifiedZero = Boolean(row.sales_verified_zero);

      // 日線價**只可以**由呢度嚟。`operator_accepted_psa10_sales_history` 淨係
      // 收嚴格 provider-bound 嘅 PSA10 真成交（已扣走 title↔卡號隔離嗰啲）。
      // 2026-08-24 之前呢度仲有一條 `market_price_observation` 讀路餵住 K 線
      // （PriceCharting／SNKRDUNK 嘅「有價冇成交」圖表點，最近 30 日 3,530 個），
      // 而 `windowMetrics` 全部變幅都錨喺呢條 history 上面 —— 即係攞真成交同
      // K 線比，出街一個 +8.8% 其實應該係 +55%，冇 error、冇 warning。owner
      // 2026-08-23：「月 K 全部全線踢走」。所以 chart 讀路已經整條刪走，日線
      // 淨返成交日；冇成交嘅日就冇點（唔准填），窗計算靠 nearestPrice /
      // latestBefore 嘅 as-of 語義自己向前帶。
      // R6-OPEN-TICKET: history-daily-latest-sale-and-outlier-band —— 呢條日線
      // **未**做到 brief 要求嘅「當日最新一單非離群成交」。兩處差異，明文寫低，
      // 唔准當交咗貨：
      //  (a) 同日多過一單攞嘅係**當日成交均價**（value/count），唔係當日最後一單。
      //      要修就要 sale 級排序：`operator_eligible_accepted_psa10_sales_rows`
      //      （migration 026:527）只出 `DATE(sold_at)`，冇 sold_at 亦冇
      //      sale_observation_id，即係要改 view／migration —— 呢個 worktree 唔准掂。
      //  (b) 呢條日線**冇離群帶**。離群帶淨係喺 quote 側（頭條價）行，呢個 tree
      //      冇對應實現，喺 TS 再抄一份 = 一個概念兩份實現，唔准。後果：頭條價
      //      自己會 reject 嘅一單離群成交，仍然可以做到窗錨。
      // (a)(b) 係一套嚟，唔准拆半修：淨做 (a) 唔做 (b) 會**放大**離群曝光——
      // 出街 top100 嘅 2,623 個成交日點入面 1,592 個（60.7%）當日有 ≥2 單，均價
      // 本身有攤薄作用，換成單一最後成交就冇。頭條價嗰邊照舊係單筆最新真成交
      // （已過離群帶），唔經呢度。
      // 依然係 history-only：唔會取代已 accept 嘅現價，唔會郁市值排名。
      const salesCount = dayCount;
      const salesValue = dayValue;
      if (point.priceUsd === null && salesCount !== null && salesCount > 0 && salesValue !== null && salesValue > 0) {
        const sources = String(row.sales_source_codes || "")
          .split(",")
          .map((source) => source.trim())
          .filter(Boolean);
        point.priceUsd = salesValue / salesCount;
        point.priceStatus = "ready";
        point.priceSourceCode = sources.length === 1 ? `${sources[0]}_sales` : "exact_psa10_sales";
        point.priceSourcePriority = 4;
      }
    }

    const cards: PublicCard[] = coreRows.map((row) => {
      const variantId = Number(row.variant_id);
      const canonicalName = String(row.canonical_name ?? "").trim();
      const locale = locales.get(variantId) ?? { names: localized(), sets: localized(), stories: localized(), observedAt: null };
      if (canonicalName) for (const code of LOCALES) if (!locale.names[code]) locale.names[code] = canonicalName;
      const image = images.get(variantId);
      // 出 DB 講嘅 canonical sha，唔好去 data/public/market-assets 度摸有冇檔。
      // 新收嘅圖一定係 private-only（2026-08-11 collect_control:snk_en_image 一次過
      // accept 咗 677 張，bytes 喺 data/runtime/operator/snk-en-assets/），要靠
      // scripts/materialize_snapshot_assets.py 抄過去再 rebase 做 public hash。
      // 舊寫法喺呢度摸唔到檔就靜靜寫 "" —— 即係「啱啱收到嘅新圖」變成「冇圖」，
      // 榜面出 placeholder，materialize 連機會都冇。實測跌咗 28 張出街卡
      // （rank 14/55/58…）。摸唔到檔要 materialize 嗰度嗌，唔係喺呢度靜靜降級。
      const imageHash = String(image?.canonical_image_content_sha256 ?? "");
      const imageVariants = imageHash ? {
        "200": `/market-assets/${imageHash}_200.webp`,
        "600": `/market-assets/${imageHash}_600.webp`,
      } : undefined;
      const currentPrice = numberValue(row.psa10_price_usd);
      const currentPopulation = numberValue(row.psa10_population);
      const canonicalRank = numberValue(row.canonical_market_rank) ?? 0;
      const awaitingFreshPrice = canonicalRank === 0;
      const pricePeriodAt = iso(row.price_source_period_at) ?? iso(row.price_observed_date);
      const priceCheckedAt = iso(row.price_checked_at) ?? iso(row.price_observed_at) ?? iso(row.price_effective_at);
      // Public asOf / freshness clock is checkedAt (043), not the PC month head.
      const priceAsOf = priceCheckedAt ?? pricePeriodAt;
      // 056→sale lane：quote 來源以 `_sales` 結尾即係「呢個價本身就係一單真成交」，
      // 個期數日期就係成交日，唔再係 chart 嘅月線頭。舊 chart quote（legacy
      // generation 重建出嚟嗰啲）先至仲有「價格期數」呢個概念，所以兩個欄位互斥：
      // 有 saleAt 就冇 sourcePeriodAt，反之亦然。FE 靠「邊個有值」判斷點寫個 label，
      // 唔使多開一個 quoteBasis 欄（全部 quote 轉晒成交之後嗰個欄係恆定噪音）。
      // 供應商代號本身唔會跟住出街：`price_source_code` 淨係喺呢度做判斷，
      // 落 payload 嘅只有日期。
      const isSaleQuote = String(row.price_source_code ?? "").endsWith("_sales");
      const priceSaleAt = isSaleQuote ? pricePeriodAt : null;
      const priceSourcePeriodAt = isSaleQuote ? null : pricePeriodAt;
      const populationAsOf = iso(row.population_effective_at);
      // 市值 = 價 × POP（rebuild_036.py:7176），所以佢只可以同兩個輸入入面**舊**
      // 嗰個一樣新。舊版攞 .at(-1)（max），即係用 POP 嘅新鮮度去標一個食緊 08-01
      // 價嘅市值：1100/1322 張卡（914 PC + 186 SNK）POP 係 08-10/08-11，價係 08-01，
      // 個市值就掛住 08-11 出街。改做 .at(0)。
      const effectiveAt = [priceAsOf, populationAsOf].filter(Boolean).sort().at(0) ?? null;
      const historyDrafts = [...(histories.get(variantId)?.values() ?? [])]
        .sort((a, b) => a.at.localeCompare(b.at));
      const history = historyDrafts
        .map(({ priceSourceCode: _priceSourceCode, priceSourcePriority: _priceSourcePriority, ...point }) => point);
      const referenceDrafts = [...(references.get(variantId)?.values() ?? [])]
        .sort((a, b) => a.at.localeCompare(b.at));
      // 同 `history` 一樣剝走供應商代號先出街（`test-public-surface-gate.mjs` 會喺
      // payload 任何深度搵供應商代號）。lane 判斷淨係喺呢個 module 入面用 draft 做。
      // 顯式逐欄抄而唔係 `_x` rest-exclusion：eslint ratchet 每個棄用 destructure
      // 計一個 warning（基線 8 已滿）。回型釘住 DailyHistoryPoint，漏欄 tsc 會嗌；
      // 加欄唔逐個諗過就出唔到街——publish surface 本來就應該逐欄過數。
      const reference = referenceDrafts
        .map((draft): DailyHistoryPoint => ({
          at: draft.at,
          priceUsd: draft.priceUsd,
          priceStatus: draft.priceStatus,
          trackedSalesValueUsd: draft.trackedSalesValueUsd,
          trackedSalesCount: draft.trackedSalesCount,
          salesCoverage: draft.salesCoverage,
          salesVerifiedZero: draft.salesVerifiedZero,
        }));
      const raw = rawPrices.get(variantId);
      const printingSetName = String(row.set_name ?? "");
      /*
       * 1286 張出街卡入面 624 張根本冇 `en` 嘅 catalog_variant_locale 行（非英文
       * locale 有 680 張），所以 2026-08-11 喺 stage_bind 加嘅 locale 重述
       * （UPDATE ... INNER JOIN）根本 match 唔到佢哋。呢條 fallback 本來就喺度補
       * 空位，但佢補嘅係 `printing.set_name` —— 指紋欄，即係 postmortem 明文寫
       * 「永遠唔准跟 PSA 重述」嗰個 hash 前像。結果一半卡（680 張，3344 個 locale
       * 位）畫緊指紋自己嗰套用字，其中 456 張同 catalog_variant.set_name 已經有
       * 嘅 PSA 標籤直接矛盾。
       *
       * 補空位補返判斷欄（catalog_variant.set_name）—— 佢就係今朝由 PSA 行重述
       * 嗰個，locale 行有譯名時照樣優先。指紋欄留返做 printingIdentity 自己嗰
       * 條記錄，唔再洩去顯示。
       */
      const displaySetName = String(row.variant_set_name ?? "");
      for (const code of LOCALES) if (!locale.sets[code]) locale.sets[code] = displaySetName || null;
      const imageAlt = Object.fromEntries(LOCALES.map((code) => [code, canonicalName || locale.names[code]])) as unknown as LocalizedText;
      return {
        id: String(row.opaque_id),
        // Rank 0 is the explicit unranked state: the card remains addressable
        // while its last eligible market price is older than 30 days.
        rank: canonicalRank,
        marketRank: canonicalRank,
        viewRank: canonicalRank,
        tcg: String(row.tcg_code) === "one-piece" ? "one-piece" : String(row.tcg_code) === "pokemon" ? "pokemon" : "other",
        cardLanguage: row.card_language as PublicCard["cardLanguage"],
        /*
         * 編號嘅顯示值由 catalog_variant 出，唔再由 catalog_printing_identity 出。
         * printing 嗰欄係 printing_sha() 十個 casefold 前像之一，凍死咗（同 set_name
         * 撞嗰堵牆一樣，見 rebuild_036 S5 嘅註）—— 佢永遠只會係 PSA 印嘅裸號。
         * variant 嗰欄唔喺 hash 入面，S5 bind 會用同一份 GemRate payload 其他 grader
         * 行嘅分母補完佢（`110` → `110/080`），所以要出街嘅完整號喺呢邊。
         *
         * `complete` 以前係 `Boolean(row.collector_number)` —— 只要有字就當完整，
         * 實測出街 top 100 有 29 張根本冇分母都標住 true。完整 = 有分母（`110/80`）
         * 或者有 set 前綴（`EB02-010`）；補唔到嗰批（實測全 universe 61 張邊個
         * grader 都冇分母）照出短號，但唔准扮完整。
         */
        collectorNumber: (() => {
          const display = String(row.variant_collector_number ?? row.collector_number ?? "");
          return {
            display,
            normalized: display.toLowerCase(),
            complete: display.includes("/") || display.includes("-"),
          };
        })(),
        printingIdentity: {
          setName: printingSetName,
          setCode: String(row.set_code ?? "") || null,
          collectorNumber: String(row.collector_number ?? ""),
          editionCode: String(row.edition_code ?? ""),
          finishCode: String(row.finish_code ?? ""),
          cardLanguage: row.card_language as PublicCard["cardLanguage"],
          canonicalPrintingSha256: String(row.canonical_printing_sha256 ?? ""),
          evidenceSha256: String(row.printing_evidence_sha256 ?? ""),
        },
        identityStatus: String(row.identity_status) === "canonical" || String(row.identity_status) === "confirmed" ? "confirmed" : "provisional",
        officialName: canonicalName,
        names: locale.names,
        sets: locale.sets,
        stories: locale.stories,
        image: {
          src: imageHash ? `/market-assets/${imageHash}.webp` : "/card-placeholder.svg",
          sha256: imageHash || "0".repeat(64),
          kind: "raw_front",
          width: Number(image?.canonical_image_width ?? 0),
          height: Number(image?.canonical_image_height ?? 0),
          alt: imageAlt,
          qcAt: iso(image?.canonical_image_qc_at) ?? iso(image?.canonical_image_source_observed_at) ?? "",
          variants: imageVariants && Object.keys(imageVariants).length > 0
            ? imageVariants
            : undefined,
        },
        pricePsa10: awaitingFreshPrice
          ? {
              value: null,
              status: "accumulating",
              asOf: priceAsOf,
              sourcePeriodAt: priceSourcePeriodAt,
              saleAt: priceSaleAt,
              checkedAt: priceCheckedAt,
            }
          : {
              ...readyMetric(currentPrice, priceAsOf),
              sourcePeriodAt: priceSourcePeriodAt,
              saleAt: priceSaleAt,
              checkedAt: priceCheckedAt,
            },
        priceUngradedReference: readyMetric(numberValue(raw?.price_usd), iso(raw?.observed_at)),
        populationPsa10: { ...readyMetric(currentPopulation, populationAsOf), estimated: false },
        marketCap: awaitingFreshPrice
          ? { value: null, status: "accumulating", asOf: priceAsOf }
          : readyMetric(numberValue(row.market_cap_usd), effectiveAt),
        windows: windowMetrics(
          historyDrafts,
          awaitingFreshPrice ? null : currentPrice,
          currentPopulation,
          priceAsOf,
          chartLaneOf(row.price_source_code),
          referenceDrafts,
        ),
        historyDaily: history,
        historyReference: reference,
      };
    });

    // metric_accepted_at 係 daily-accept 落筆嗰刻嘅 wall clock，唔係證據時間 ——
    // 佢一路都係三個入面最大嗰個，所以 snapshot 個 effectiveAt 實質等於「我幾時
    // 跑咗 accept」，同數據幾新冇關。剩返兩個先係真證據時間。
    const evidenceTimes = coreRows.flatMap((row) => [iso(row.price_observed_date), iso(row.population_effective_at)]).filter((value): value is string => Boolean(value));
    const effectiveAt = evidenceTimes.sort().at(-1) ?? new Date().toISOString();
    const fx = new Map(fxRows[0].map((row) => [String(row.quote_currency).toUpperCase(), row]));
    const rate = (code: string): MarketMetric => {
      if (code === "USD") return readyMetric(1, effectiveAt);
      const row = fx.get(code);
      return readyMetric(numberValue(row?.rate), iso(row?.effective_at));
    };
    const snapshot: PublicMarketSnapshot = {
      schemaVersion: "2.0.0",
      generation: {
        id: `db3308_${generationHash.slice(0, 16)}`,
        generatedAt: new Date().toISOString(),
        effectiveAt,
      },
      universe: {
        memberCount,
        populationMin: 1000,
        grade: "PSA 10",
        rankingMetric: "psa10_market_cap_usd",
        windows: ["1d", "7d", "30d", "90d", "180d", "365d"] as unknown as PublicMarketSnapshot["universe"]["windows"],
        salesCoverage: "partial",
      },
      coverage: {
        claim: "verified-top-n",
        requestedCount: 100,
        verifiedCount: Math.min(100, cards.length),
        top100Count: Math.min(100, cards.length),
        watchlistCount: Math.max(0, cards.length - 100),
        changeReady: Object.fromEntries(Object.keys(WINDOWS).map((code) => [code, cards.filter((card) => (card.windows as Record<string, WindowMetrics>)[code].changePct.value !== null).length])) as Record<keyof typeof WINDOWS, number>,
        salesReady: Object.fromEntries(Object.keys(WINDOWS).map((code) => [code, cards.filter((card) => (card.windows as Record<string, WindowMetrics>)[code].trackedSales.valueUsd.value !== null).length])) as Record<keyof typeof WINDOWS, number>,
        completeIdentityCount: cards.filter((card) => card.identityStatus === "confirmed").length,
        localizedStoryCount: Object.fromEntries(LOCALES.map((code) => [code, cards.filter((card) => Boolean(card.stories[code])).length])) as Record<(typeof LOCALES)[number], number>,
      },
      currencies: {
        base: "USD",
        /* 逐隻手寫 = 加一隻貨幣就要記得改呢度；改行 `currencies`（lib/types.ts）自動跟。
           DB 冇嗰隻嘅 `rate()` 會出 value=null 嘅 metric，落到 normaliseSnapshot 變 NaN。 */
        supported: [...currencies],
        rates: Object.fromEntries(currencies.map((code) => [code, rate(code)])) as PublicMarketSnapshot["currencies"]["rates"],
        asOf: [...fx.values()].map((row) => iso(row.effective_at)).filter((value): value is string => Boolean(value)).sort().at(-1) ?? effectiveAt,
      },
      top100: cards.slice(0, 100),
      watchlist: cards.slice(100),
    };
    return normaliseSnapshot(snapshot);
  } finally {
    await connection.end();
  }
}
