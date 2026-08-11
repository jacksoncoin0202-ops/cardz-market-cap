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
import type { MarketViewSnapshot } from "./types";

const LOCALES = ["en", "zhTW", "zhCN", "ja", "ko"] as const;
const WINDOWS = { "1d": 1, "7d": 7, "30d": 30 } as const;

type DbRow = RowDataPacket & Record<string, unknown>;

function repoRoot(): string {
  const { basename, dirname, resolve } = require("node:path") as typeof import("node:path");
  const configuredRoot = process.env.CARDZ_REPO_ROOT?.trim();
  if (configuredRoot) return configuredRoot;
  const cwd = process.cwd();
  return basename(cwd) === "web" && basename(dirname(cwd)) === "apps"
    ? resolve(cwd, "../..")
    : cwd;
}

function marketAssetExists(filename: string): boolean {
  const { existsSync } = require("node:fs") as typeof import("node:fs");
  const { resolve } = require("node:path") as typeof import("node:path");
  return existsSync(resolve(repoRoot(), "data/public/market-assets", filename));
}

function loadDbEnvironment(): void {
  if (process.env.CARDZ_DB_PASSWORD && process.env.CARDZ_DB_USER && process.env.CARDZ_DB_NAME) return;
  const { readFileSync } = require("node:fs") as typeof import("node:fs");
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

function nearestPrice(
  history: DailyHistoryPoint[],
  targetMs: number,
  toleranceDays: number,
): DailyHistoryPoint | null {
  let winner: DailyHistoryPoint | null = null;
  let winnerDelta = Number.POSITIVE_INFINITY;
  for (const point of history) {
    if (point.priceUsd === null) continue;
    const delta = Math.abs(new Date(point.at).valueOf() - targetMs) / 86_400_000;
    if (delta <= toleranceDays && delta < winnerDelta) {
      winner = point;
      winnerDelta = delta;
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
    const anchor = nearestPrice(history, currentMs - daysBack * 86_400_000, tolerance);
    const priceChange = percentage(currentPrice, anchor?.priceUsd ?? null);
    const anchorSource = (anchor as (DailyHistoryPoint & { priceSourceCode?: string | null }) | null)?.priceSourceCode ?? null;
    const sourceSwitched = Boolean(anchorSource && currentSource && anchorSource !== currentSource);
    const anchorCap = anchor?.priceUsd === null || anchor?.priceUsd === undefined || currentPopulation === null
      ? null
      : anchor.priceUsd * currentPopulation;
    const capChange = percentage(currentCap, anchorCap);
    const currentSales = salesTotal(history, currentMs, daysBack);
    const previousSales = salesTotal(history, currentMs - daysBack * 86_400_000, daysBack);
    const salesChange = percentage(currentSales?.value ?? null, previousSales?.value ?? null);
    const accumulating = currentPrice === null ? "unavailable" : "accumulating";
    return [code, {
      changePct: {
        value: priceChange,
        status: priceChange === null ? accumulating : "ready",
        asOf: priceChange === null ? null : currentAsOf,
        // 供應商代號唔出街：呢個 snapshot 會原封不動 ship 落 client payload，
        // 睇 view-source 就見到。UI 只需要「換咗錨點」呢個 boolean。
        priceAnchorSource: null,
        sourceSwitched: priceChange === null ? false : sourceSwitched,
      },
      marketCapChangePct: {
        value: capChange,
        status: capChange === null ? accumulating : "ready",
        asOf: capChange === null ? null : currentAsOf,
        priceAnchorSource: null,
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
    const [coreRows] = await connection.query<DbRow[]>(`
      SELECT
        metric.variant_id,variant.opaque_id,variant.canonical_name,metric.canonical_market_rank,
        variant.set_name AS variant_set_name,
        variant.collector_number AS variant_collector_number,
        printing.tcg_code,printing.card_language,printing.collector_number,
        printing.set_name,printing.set_code,printing.edition_code,printing.finish_code,
        printing.identity_status,printing.canonical_printing_sha256,
        printing.evidence_sha256 AS printing_evidence_sha256,
        price.price_usd AS psa10_price_usd,price.observed_date AS price_observed_date,
        price.effective_at AS price_effective_at,
        source_observation.observed_at AS price_observed_at,
        CASE WHEN price.source_code IN ('snk','snk_psa10') THEN 'snkrdunk' ELSE price.source_code END AS price_source_code,
        population.top_grade_population AS psa10_population,
        population.effective_at AS population_effective_at,
        metric.market_cap_usd,metric.accepted_at AS metric_accepted_at
      FROM market_canonical_metric_acceptance metric
      INNER JOIN catalog_variant variant ON variant.id=metric.variant_id
      INNER JOIN catalog_printing_identity printing ON printing.variant_id=metric.variant_id
      INNER JOIN market_metric_history_acceptance price_history ON price_history.id=metric.price_history_acceptance_id
      INNER JOIN market_price_observation price ON price.id=price_history.source_record_id
      INNER JOIN market_source_observation source_observation ON source_observation.id=price.source_observation_id
      INNER JOIN market_metric_history_acceptance population_history ON population_history.id=metric.population_history_acceptance_id
      INNER JOIN market_grader_population_observation population ON population.id=population_history.source_record_id
      WHERE metric.ranking_generation_sha256=?
      ORDER BY metric.canonical_market_rank IS NULL,
               metric.canonical_market_rank,metric.variant_id
    `, [generationHash]);
    if (coreRows.length === 0) throw new Error("3308 current ranking generation is empty");
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
        target.stories[locale] = String(row.market_story ?? "").trim() || null;
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
    for (const row of salesRows[0]) {
      const observedDate = day(row.observed_date);
      if (!observedDate) continue;
      const point = getPoint(Number(row.variant_id), observedDate);
      point.trackedSalesValueUsd = numberValue(row.sales_value_usd);
      point.trackedSalesCount = numberValue(row.sales_count);
      point.salesCoverage = String(row.sales_coverage_status || "unavailable") as DailyHistoryPoint["salesCoverage"];
      point.salesVerifiedZero = Boolean(row.sales_verified_zero);

      // `operator_accepted_psa10_sales_history` contains only strict
      // provider-bound PSA10 transactions.  A daily close from an exact
      // provider remains preferred; when it is absent, the actual same-day
      // PSA10 transaction average is a valid historical price anchor.  This
      // is deliberately history-only: it never replaces the accepted current
      // price or changes the market-cap ranking.
      const salesCount = numberValue(row.sales_count);
      const salesValue = numberValue(row.sales_value_usd);
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
      if (canonicalName) for (const code of LOCALES) locale.names[code] = canonicalName;
      const image = images.get(variantId);
      const canonicalImageHash = String(image?.canonical_image_content_sha256 ?? "");
      const imageHash = canonicalImageHash && marketAssetExists(`${canonicalImageHash}.webp`)
        ? canonicalImageHash
        : "";
      const imageVariants = imageHash ? {
        ...(marketAssetExists(`${imageHash}_200.webp`)
          ? { "200": `/market-assets/${imageHash}_200.webp` }
          : {}),
        ...(marketAssetExists(`${imageHash}_600.webp`)
          ? { "600": `/market-assets/${imageHash}_600.webp` }
          : {}),
      } : undefined;
      const currentPrice = numberValue(row.psa10_price_usd);
      const currentPopulation = numberValue(row.psa10_population);
      const canonicalRank = numberValue(row.canonical_market_rank) ?? 0;
      const awaitingFreshPrice = canonicalRank === 0;
      const priceAsOf = iso(row.price_observed_date) ?? iso(row.price_effective_at);
      const populationAsOf = iso(row.population_effective_at);
      const effectiveAt = [priceAsOf, populationAsOf].filter(Boolean).sort().at(-1) ?? null;
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
          ? { value: null, status: "accumulating", asOf: priceAsOf }
          : readyMetric(currentPrice, priceAsOf),
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

    const evidenceTimes = coreRows.flatMap((row) => [iso(row.metric_accepted_at), iso(row.price_observed_date), iso(row.population_effective_at)]).filter((value): value is string => Boolean(value));
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
        populationMin: 1000,
        grade: "PSA 10",
        rankingMetric: "psa10_market_cap_usd",
        windows: ["1d", "7d", "30d"],
        salesCoverage: "partial",
      },
      coverage: {
        claim: "verified-top-n",
        requestedCount: 100,
        verifiedCount: Math.min(100, cards.length),
        top100Count: Math.min(100, cards.length),
        watchlistCount: Math.max(0, cards.length - 100),
        changeReady: Object.fromEntries(Object.keys(WINDOWS).map((code) => [code, cards.filter((card) => card.windows[code as keyof typeof WINDOWS].changePct.value !== null).length])) as Record<keyof typeof WINDOWS, number>,
        salesReady: Object.fromEntries(Object.keys(WINDOWS).map((code) => [code, cards.filter((card) => card.windows[code as keyof typeof WINDOWS].trackedSales.valueUsd.value !== null).length])) as Record<keyof typeof WINDOWS, number>,
        completeIdentityCount: cards.filter((card) => card.identityStatus === "confirmed").length,
        localizedStoryCount: Object.fromEntries(LOCALES.map((code) => [code, cards.filter((card) => Boolean(card.stories[code])).length])) as Record<(typeof LOCALES)[number], number>,
      },
      currencies: {
        base: "USD",
        supported: ["USD", "HKD", "CNY", "GBP", "TWD", "JPY", "KRW"],
        rates: {
          USD: rate("USD"), HKD: rate("HKD"), CNY: rate("CNY"), GBP: rate("GBP"),
          TWD: rate("TWD"), JPY: rate("JPY"), KRW: rate("KRW"),
        },
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
