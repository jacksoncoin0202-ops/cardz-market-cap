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
 * `loadDbEnvironment()` / `loadSaleQuarantine()` 三個 function 變 async，跟住成條同步
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
function loadSaleQuarantine(): Map<string, { valueUsd: number; count: number }> {
  // eslint-disable-next-line @typescript-eslint/no-require-imports -- 同步 lazy load（見 repoRoot 上面嘅 block 註）。
  const { readFileSync } = require("node:fs") as typeof import("node:fs");
  // eslint-disable-next-line @typescript-eslint/no-require-imports -- 同上。
  const { resolve } = require("node:path") as typeof import("node:path");
  const receiptPath = resolve(repoRoot(), "data/runtime/operator/audit/pc_sale_title_quarantine_current.json");
  const doc = JSON.parse(readFileSync(receiptPath, "utf8")) as {
    entries: Array<{ variantId: number; observedDate: string; transactionValueUsd: number | null; quantity: number | null }>;
  };
  const excluded = new Map<string, { valueUsd: number; count: number }>();
  for (const entry of doc.entries) {
    const key = `${entry.variantId}|${entry.observedDate}`;
    const slot = excluded.get(key) ?? { valueUsd: 0, count: 0 };
    slot.valueUsd += entry.transactionValueUsd ?? 0;
    slot.count += entry.quantity ?? 0;
    excluded.set(key, slot);
  }
  return excluded;
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
function anchorCandidate(
  point: DailyHistoryPoint,
  currentSource: string | null,
): AnchorCandidate | null {
  const source = (point as DailyHistoryPoint & { priceSourceCode?: string | null }).priceSourceCode ?? null;
  if (
    point.priceUsd !== null
    && (!source || !currentSource || source === currentSource || source.endsWith("_sales"))
  ) {
    return { at: point.at, priceUsd: point.priceUsd, sourceCode: source };
  }
  const count = point.trackedSalesCount;
  const value = point.trackedSalesValueUsd;
  if (count !== null && count > 0 && value !== null && value > 0) {
    return { at: point.at, priceUsd: value / count, sourceCode: "exact_psa10_sales" };
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
  const startMs = endMs - (days - 1) * 86_400_000;
  const rows = history.filter((point) => {
    const at = new Date(point.at).valueOf();
    return at >= startMs && at <= endMs && point.salesCoverage !== "unavailable";
  });
  if (rows.length === 0) return null;
  return {
    value: rows.reduce((sum, point) => sum + (point.trackedSalesValueUsd ?? 0), 0),
    count: rows.reduce((sum, point) => sum + (point.trackedSalesCount ?? 0), 0),
    asOf: rows[rows.length - 1].at,
  };
}

function windowMetrics(
  history: DailyHistoryPoint[],
  currentPrice: number | null,
  currentPopulation: number | null,
  currentAsOf: string | null,
  currentSource: string | null,
): Record<keyof typeof WINDOWS, WindowMetrics> {
  const currentMs = currentAsOf ? new Date(currentAsOf).valueOf() : Date.now();
  const currentCap = currentPrice === null || currentPopulation === null ? null : currentPrice * currentPopulation;
  return Object.fromEntries(Object.entries(WINDOWS).map(([code, daysBack]) => {
    const tolerance = code === "1d" ? 2 : code === "7d" ? 3 : 5;
    const targetMs = currentMs - daysBack * 86_400_000;
    // 帶內空咗先輪到 step-function 後備（見 latestBefore 註釋）。帶內冇點
    // ⇒ (target−5d, target+5d) 全空 ⇒「最後一個 < target−5d 嘅點」就係
    //「最後一個 ≤ target 嘅點」，即係標準 as-of 語義，冇偷步。
    const anchor = LONG_WINDOWS.has(code)
      ? latestBefore(history, targetMs + 1, currentSource)
      : nearestPrice(history, targetMs, tolerance, currentMs, currentSource)
        ?? (code === "30d" ? latestBefore(history, targetMs - tolerance * 86_400_000, currentSource) : null);
    const priceChange = percentage(currentPrice, anchor?.priceUsd ?? null);
    const anchorSource = anchor?.sourceCode ?? null;
    const sourceSwitched = Boolean(anchorSource && currentSource && anchorSource !== currentSource);
    const inventCap = !LONG_WINDOWS.has(code);
    const anchorCap = !inventCap || anchor === null || currentPopulation === null
      ? null
      : anchor.priceUsd * currentPopulation;
    const capChange = inventCap ? percentage(currentCap, anchorCap) : null;
    const currentSales = salesTotal(history, currentMs, daysBack);
    const previousSales = salesTotal(history, currentMs - daysBack * 86_400_000, daysBack);
    const salesChange = percentage(currentSales?.value ?? null, previousSales?.value ?? null);
    const accumulating = currentPrice === null ? "unavailable" : "accumulating";
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

  const [localeRows, imageRows, fallbackImageRows, rawRows, priceRows, salesRows, fxRows] = await Promise.all([
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
    const currentSource = new Map(coreRows.map((row) => [Number(row.variant_id), String(row.price_source_code)]));
    for (const row of priceRows[0]) {
      const variantId = Number(row.variant_id);
      const observedDate = day(row.observed_date);
      if (!observedDate) continue;
      const point = getPoint(variantId, observedDate);
      const sourceCode = String(row.source_code);
      const sourcePriority = sourceCode === currentSource.get(variantId)
        ? 0
        : sourceCode === "snkrdunk"
          ? 1
          : sourceCode === "pricecharting"
            ? 2
            : 3;
      if (point.priceUsd !== null && point.priceSourcePriority <= sourcePriority) continue;
      point.priceUsd = numberValue(row.price_usd);
      point.priceStatus = point.priceUsd === null ? "unavailable" : "ready";
      point.priceSourceCode = sourceCode;
      point.priceSourcePriority = sourcePriority;
    }
    const saleQuarantine = loadSaleQuarantine();
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

      // `operator_accepted_psa10_sales_history` contains only strict
      // provider-bound PSA10 transactions.  A daily close from an exact
      // provider remains preferred; when it is absent, the actual same-day
      // PSA10 transaction average is a valid historical price anchor.  This
      // is deliberately history-only: it never replaces the accepted current
      // price or changes the market-cap ranking.
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
              sourcePeriodAt: pricePeriodAt,
              checkedAt: priceCheckedAt,
            }
          : {
              ...readyMetric(currentPrice, priceAsOf),
              sourcePeriodAt: pricePeriodAt,
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
          String(row.price_source_code),
        ),
        historyDaily: history,
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
