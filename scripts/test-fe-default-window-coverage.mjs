#!/usr/bin/env node
/*
 * 預設時段（`types.ts` `defaultMarketWindow`）要喺**真數據**上面錨得住 —— 2026-08-24（R6 後續）。
 *
 * 背景：R6 將 `historyDaily` 由「K 線觀測點」收窄成「真成交日」之後，條 series 稀疏咗好多，
 * 而 `windows.*.changePct` 全部靠佢搵錨（`windowMetrics` → `nearestPrice` / `latestBefore`）。
 * 純成交嘅 180d 喺 08-23 嗰份 seed snapshot 上面只有 165/1604（10.3%）張卡搵到錨 —— 即係
 * 成板 ~90% 卡嘅預設視圖、卡頁 meta 變動徽章、同分享圖（`SHARE_WINDOW`）全部變「資料累積中」。
 * 冇 error、冇 500、頁面照出，所以呢種塌方唔會有人嗌，只可以用數據釘住。
 *
 * R6b（owner 2026-08-24）長窗（90/180/365）加咗混合錨：冇真成交錨先至退去參考點
 * （`historyReference`），短窗（1d/7d/30d）照樣淨認真成交。所以覆蓋率一律用**混合**計，
 * 同 producer 行緊嗰條規矩一模一樣（真 `windowMetrics` 收兩條 series）。
 *
 * 呢個檔守五樣嘢：
 *  ① 預設值係 runtime 值（transpile types.ts 攞返），唔係讀註釋。
 *  ② **真 `windowMetrics` 行一次**：閘要有牙 —— 合成 fixture 冇夠舊嘅成交 → 覆蓋率 0；
 *     有夠舊嘅成交 → 覆蓋率 1。證明條數真係會分辨得出，唔係永遠 pass。
 *  ③ 攞 `data/public/seed-snapshot.json` 全板 1,604 張卡跑真 `windowMetrics`，配置嘅
 *     `defaultMarketWindow` 要 ≥ 60% 有錨。日後成交數據長多咗，長窗自然過到。
 *  ④ 分享圖個窗要**由** `defaultMarketWindow` 推出嚟，唔准喺 OG route 另寫一個字面值 ——
 *     兩份 copy = 網頁一個窗、分享圖另一個窗（AGENTS.md 規矩 13）。
 *  ⑤ 後備真係喺度做緊嘢：同一份真數據，關咗參考 series（即係 R6 純成交嗰個形狀）之後
 *     預設窗一定要跌穿條線。唔跌穿 = ③ 係空斷言。
 *
 * 參考 series 邊度嚟（2026-08-24 R6b 收尾改）：R6b 之後嘅 bake 直接出 `historyReference`
 * 獨立欄，呢度以佢為準；R6 之前嘅舊 bake 冇呢個欄，就由混合 `historyDaily` 拆返出嚟
 * （冇成交而又有價嗰日 = 觀測點，舊 builder 觀測 loop 行喺成交 loop 之前 ——
 * 見 `git show 4d4191d7^:apps/web/src/lib/live-db-snapshot.ts`）。R6-final 過渡 bake
 * 兩樣都冇 —— D3/D5 對佢冇嘢好測，詳見 D3 塊頭 skip 註釋；release script
 * （daily_public_release.sh publish_assets）喺 bake 之後即刻用 `--board` 對住將要
 * 出街嘅 bytes 重跑 D3/D5，出街嗰刻永遠有真判 —— gate 冇鬆過，只係搬到見到真相嘅位。
 *
 * 純靜態 + 真評估：唔開 DB、唔開瀏覽器，由 run_all_tests.py 個 `scripts/test-*.mjs` glob 收。
 *
 * Run: node scripts/test-fe-default-window-coverage.mjs [--board]
 */
import { createRequire } from "node:module";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const TYPES_REL = "apps/web/src/lib/types.ts";
const PRODUCER_REL = "apps/web/src/lib/live-db-snapshot.ts";
const OG_ROUTE_REL = "apps/web/src/app/api/og/card/[id]/route.tsx";
const SNAPSHOT_REL = "data/public/seed-snapshot.json";

/* 板面預設視圖至少要有六成卡見到真數，先叫「預設」。低過就係預設出「資料累積中」。 */
const MIN_COVERAGE = 0.60;

/* release script 喺 bake 之後帶 --board 入嚟：對住將要出街嘅 snapshot 硬跑 D3/D5，唔准 skip。 */
const BOARD_FORCED = process.argv.includes("--board");

const failed = [];
let checks = 0;
const check = (label, condition, detail) => {
  checks += 1;
  if (!condition) failed.push(detail ? `${label}: ${detail}` : label);
};
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");

const require_ = createRequire(join(ROOT, "apps/web/package.json"));
const ts = require_("typescript");
const dir = join(tmpdir(), `cardz-default-window-${process.pid}`);
mkdirSync(dir, { recursive: true });
const transpile = (source, name) => {
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  });
  const file = join(dir, name);
  writeFileSync(file, outputText);
  return pathToFileURL(file).href;
};

/* ─────────────────────────────────────────────────────────────
 * D1 — 攞 runtime 值（唔係 regex 讀註釋）。
 * ───────────────────────────────────────────────────────────── */
const typesLib = await import(transpile(read(TYPES_REL), "types.mjs"));
const DEFAULT_WINDOW = typesLib.defaultMarketWindow;
check("D1: defaultMarketWindow 係 marketWindows 入面一個",
  typesLib.marketWindows.includes(DEFAULT_WINDOW), String(DEFAULT_WINDOW));
check("D1: marketWindowDays 有呢個窗",
  Number.isFinite(typesLib.marketWindowDays[DEFAULT_WINDOW]), String(DEFAULT_WINDOW));

// 真 `windowMetrics`（剝走 import 頭），唔喺 test 度抄一份錨點政策。
const producer = read(PRODUCER_REL);
const moduleBody = producer.slice(producer.indexOf("const LOCALES = ["));
const { windowMetrics } = await import(transpile(
  `${moduleBody}\nexport { windowMetrics };\n`,
  "producer.mjs",
));

/*
 * 「有錨」= 真 `windowMetrics` 出到一個非 null 嘅 `changePct`，即係頁面真係見到個數。
 */
const saleOnlyHistory = (card) => (card.historyDaily ?? [])
  .filter((point) => (point.trackedSalesCount ?? 0) > 0 && (point.trackedSalesValueUsd ?? 0) > 0)
  .map((point) => ({
    at: point.at,
    priceUsd: point.trackedSalesValueUsd / point.trackedSalesCount,
    priceStatus: "ready",
    trackedSalesValueUsd: point.trackedSalesValueUsd,
    trackedSalesCount: point.trackedSalesCount,
    salesCoverage: point.salesCoverage,
    salesVerifiedZero: point.salesVerifiedZero,
  }));

/*
 * 參考 series（兩代 snapshot 都要識讀，見檔頭）：
 *  · 有 `historyReference` 欄（R6b 之後 bake）→ 佢係唯一來源，空欄都唔會回落去猜。
 *  · 冇呢個欄（R6 之前 bake）→ 由混合 historyDaily 拆：冇成交而又有價嗰日 = 觀測點。
 */
const referenceHistory = (card) => (Array.isArray(card.historyReference)
  ? card.historyReference
    .filter((point) => typeof point.priceUsd === "number" && Number.isFinite(point.priceUsd))
    .map((point) => ({ ...point }))
  : (card.historyDaily ?? [])
    .filter((point) => (point.trackedSalesCount ?? 0) === 0
      && typeof point.priceUsd === "number" && Number.isFinite(point.priceUsd))
    .map((point) => ({
      at: point.at,
      priceUsd: point.priceUsd,
      priceStatus: "ready",
      trackedSalesValueUsd: null,
      trackedSalesCount: null,
      salesCoverage: "unavailable",
      salesVerifiedZero: false,
    })));

const anchored = (card, code, { hybrid = true } = {}) => windowMetrics(
  saleOnlyHistory(card),
  card.pricePsa10?.value ?? null,
  card.populationPsa10?.value ?? null,
  card.pricePsa10?.asOf ?? null,
  null,
  hybrid ? referenceHistory(card) : [],
)[code].changePct.value !== null;

const coverage = (cards, code, options) => (cards.length === 0
  ? 0
  : cards.filter((card) => anchored(card, code, options)).length / cards.length);

/* ─────────────────────────────────────────────────────────────
 * D2 — 閘有牙：合成 fixture 兩邊都要答啱，證明 D3 條數唔係永遠 pass。
 * ───────────────────────────────────────────────────────────── */
{
  const NOW = "2026-08-23T00:00:00Z";
  const sale = (date, price) => ({
    at: `${date}T00:00:00Z`,
    trackedSalesValueUsd: price,
    trackedSalesCount: 1,
    salesCoverage: "partial",
    salesVerifiedZero: false,
  });
  const card = (history) => ({
    pricePsa10: { value: 3100, asOf: NOW },
    populationPsa10: { value: 1000 },
    historyDaily: history,
  });
  // 淨係得三日前一單：30d / 365d 都搵唔到夠舊嘅錨。
  const recentOnly = [card([sale("2026-08-20", 3000)])];
  // 加返 40 日前嗰單：30d 有錨（帶外 step-function 後備），365d 仲係冇。
  const midOld = [card([sale("2026-07-14", 2400), sale("2026-08-20", 3000)])];
  // 加返一年多前嗰單：連 365d 都有錨。
  const veryOld = [card([sale("2025-06-01", 900), sale("2026-08-20", 3000)])];

  check("D2: 冇夠舊成交 → 30d 覆蓋率 0", coverage(recentOnly, "30d") === 0,
    String(coverage(recentOnly, "30d")));
  check("D2: 40 日前有成交 → 30d 覆蓋率 1", coverage(midOld, "30d") === 1,
    String(coverage(midOld, "30d")));
  check("D2: 40 日前嗰單唔夠 365d 用 → 365d 覆蓋率 0", coverage(midOld, "365d") === 0,
    String(coverage(midOld, "365d")));
  check("D2: 一年前有成交 → 365d 覆蓋率 1", coverage(veryOld, "365d") === 1,
    String(coverage(veryOld, "365d")));
  check("D2: 一半卡有錨 → 覆蓋率 0.5（分數計得啱，唔係 boolean）",
    coverage([...recentOnly, ...midOld], "30d") === 0.5,
    String(coverage([...recentOnly, ...midOld], "30d")));

  // R6b 之後 bake：參考點住喺 `historyReference` 欄。呢三條證明讀欄嗰條 path
  // 真係會 fire，同埋欄一存在就係唯一來源（空欄唔會回落 historyDaily 猜）。
  const refPoint = { at: "2025-06-01T00:00:00Z", priceUsd: 900, priceStatus: "ready",
    trackedSalesValueUsd: null, trackedSalesCount: null,
    salesCoverage: "unavailable", salesVerifiedZero: false };
  const fieldCard = { ...card([sale("2026-08-20", 3000)]), historyReference: [refPoint] };
  check("D2: historyReference 欄有夠舊參考點 → 365d 錨到（讀欄 path 會 fire）",
    coverage([fieldCard], "365d") === 1, String(coverage([fieldCard], "365d")));
  check("D2: 同一張卡冇 historyReference 欄 → 365d 錨唔到（對照組）",
    coverage([card([sale("2026-08-20", 3000)])], "365d") === 0,
    String(coverage([card([sale("2026-08-20", 3000)])], "365d")));
  const emptyFieldCard = { ...card([sale("2026-08-20", 3000)]), historyReference: [] };
  check("D2: historyReference 欄存在就係唯一來源（空欄唔回落 historyDaily）",
    coverage([emptyFieldCard], "365d") === 0, String(coverage([emptyFieldCard], "365d")));
}

/* ─────────────────────────────────────────────────────────────
 * D3 / D5 — 真板：配置嘅預設窗要 ≥ 60% 卡有錨（混合錨），
 *           而且關咗參考 series 之後要跌穿條線（證明後備真係喺度做緊嘢）。
 * ───────────────────────────────────────────────────────────── */
{
  const snapshot = JSON.parse(read(SNAPSHOT_REL));
  const cards = [...(snapshot.top100 ?? []), ...(snapshot.watchlist ?? [])];
  /*
   * 呢棵 tree 嘅 seed-snapshot 係「上一次 bake」——producer schema 啱啱改咗嗰晚佢必然
   * 係舊 schema。R6-final（4d4191d7）將 historyDaily 收窄成純成交之後、R6b 第一次 bake
   * 之前，恰好有一代 snapshot 兩樣都冇：historyDaily 冇觀測點、又未有 historyReference
   * 欄。對住呢代 snapshot 跑 D3 唔係測 producer，係測「舊檔案冇料」——所以只喺呢個唯一
   * 情況 skip 並大聲講；release 喺 bake 之後用 --board 對住將要出街嘅 bytes 硬跑呢兩段
   * （scripts/daily_public_release.sh publish_assets），所以出街路徑一日都冇少過判。
   */
  const boardJudgeable = BOARD_FORCED
    || cards.some((c) => Array.isArray(c.historyReference))
    || cards.some((c) => (c.historyDaily ?? []).some((p) =>
      (p.trackedSalesCount ?? 0) === 0 && typeof p.priceUsd === "number"));
  if (!boardJudgeable) {
    console.log("[board] D3/D5 deferred: snapshot 係 R6-final 過渡 bake（純成交、冇 historyReference）——release 會喺 bake 後用 --board 重跑");
  } else {
  check("D3: seed-snapshot 有卡", cards.length > 0, String(cards.length));

  const tableFor = (options) => typesLib.marketWindows.map((code) => {
    const hit = cards.filter((card) => anchored(card, code, options)).length;
    return { code, hit, pct: cards.length === 0 ? 0 : hit / cards.length };
  });
  const render = (table) => table
    .map(({ code, hit, pct }) => `${code}=${hit}/${cards.length} (${(pct * 100).toFixed(1)}%)`)
    .join("  ");
  const hybrid = tableFor(undefined);
  const saleOnly = tableFor({ hybrid: false });
  const line = render(hybrid);
  console.log(`[anchor coverage · hybrid   ] ${line}`);
  console.log(`[anchor coverage · sale-only] ${render(saleOnly)}`);

  const chosen = hybrid.find((row) => row.code === DEFAULT_WINDOW);
  check(`D3: 預設窗 ${DEFAULT_WINDOW} 錨覆蓋率 ≥ ${(MIN_COVERAGE * 100).toFixed(0)}%`,
    Boolean(chosen) && chosen.pct >= MIN_COVERAGE,
    `${DEFAULT_WINDOW} = ${chosen ? (chosen.pct * 100).toFixed(1) : "?"}% —— 預設視圖／卡頁 meta 徽章／`
    + `分享圖會有 ${chosen ? (100 - chosen.pct * 100).toFixed(1) : "?"}% 卡出「資料累積中」；`
    + `而家真數據排名：${line}`);

  /*
   * D5 — 條閘唔准係空斷言。而家嘅預設係長窗，佢過到係**因為**有混合錨；同一份真數據
   * 關咗參考 series（即係 R6 純成交嗰個形狀）就一定要跌穿條線。
   * 如果日後真成交長到連純成交都夠 60%，呢條就唔再係長窗嘅測試 —— 嗰陣就應該改成
   * 「拆咗後備都過到」，而唔係靜靜留住一個永遠 pass 嘅斷言。
   */
  const bare = saleOnly.find((row) => row.code === DEFAULT_WINDOW);
  const shortWindow = !["90d", "180d", "365d"].includes(DEFAULT_WINDOW);
  check("D5: 拆走參考後備之後，預設窗要跌穿條線（證明後備真係喺度做緊嘢）",
    shortWindow || (Boolean(bare) && bare.pct < MIN_COVERAGE),
    `純成交 ${DEFAULT_WINDOW} = ${bare ? (bare.pct * 100).toFixed(1) : "?"}%，冇跌穿 `
    + `${(MIN_COVERAGE * 100).toFixed(0)}% —— D3 變咗空斷言，要重寫，唔准就咁留住`);

  /* 短窗嘅覆蓋率唔准受參考 series 影響（owner hard rule：K 線唔准錨短窗）。 */
  for (const code of ["1d", "7d", "30d"]) {
    const a = hybrid.find((row) => row.code === code);
    const b = saleOnly.find((row) => row.code === code);
    check(`D5: ${code} 開唔開參考 series 都係同一個覆蓋率（K 線唔准錨短窗）`,
      a.hit === b.hit, `hybrid=${a.hit} sale-only=${b.hit}`);
  }
  }
}

/* ─────────────────────────────────────────────────────────────
 * D4 — 分享圖個窗由 `defaultMarketWindow` 推，唔准另寫字面值。
 * ───────────────────────────────────────────────────────────── */
{
  const og = read(OG_ROUTE_REL);
  check("D4: SHARE_WINDOW = defaultMarketWindow",
    /const SHARE_WINDOW: MarketWindow = defaultMarketWindow;/.test(og));
  const stripComments = (src) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  check("D4: OG route 冇另寫死一個窗字面值",
    !/SHARE_WINDOW[^\n]*=\s*"(?:1d|7d|30d|90d|180d|365d)"/.test(stripComments(og)));
}

rmSync(dir, { recursive: true, force: true });

if (failed.length) {
  console.error(`FAIL test-fe-default-window-coverage (${failed.length}/${checks})`);
  for (const f of failed) console.error(` - ${f}`);
  process.exit(1);
}
console.log(`PASS test-fe-default-window-coverage (${checks} checks)`);
