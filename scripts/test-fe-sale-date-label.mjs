#!/usr/bin/env node
/*
 * 「成交日」契約 —— 2026-08-23（PSA10 頭條價由 chart 月線／K 線改成最新真實成交）。
 * 純靜態 + 真評估：唔開瀏覽器、唔使 dev server、唔使 node_modules，
 * 由 run_all_tests.py 個 `scripts/test-*.mjs` glob 自動收（同其餘 test-fe-*.mjs 一樣）。
 *
 * 呢個檔守四樣嘢，每樣都係「錯咗唔會有 error、只會靜靜出錯嘢」嗰類：
 *
 *  ① **成交價唔准再叫「價格期數」。** producer 側靠 quote source code 以 `_sales` 結尾
 *     去分「呢個價本身就係一單成交」定「chart 嘅月線頭」。呢條判斷寫錯 = 全板 1,604 張卡
 *     繼續掛住「價格期數：2026-08-01」，而個價其實係 08-19 嗰單成交 —— 頁面照樣渲染，
 *     冇 error、冇 warning。所以 T1 唔係 grep 個字串，而係**真係抽 live-db-snapshot.ts
 *     嗰三行源碼出嚟行一次**，兩個方向（sale / chart）都要行到。
 *  ② **legacy chart quote 仲要有得出「價格期數」。** `operator_resolved_canonical_metric_quote`
 *     嘅 legacy branch 仲會重建舊 generation 嘅 chart quote，嗰批冇 saleAt。分支冚咗
 *     就變成成個「資料時間」行淨返「最近檢查」，日期證據靜靜消失。
 *  ③ **`<p className="data-time">` 元素同佢個位置唔准郁。** test-fe-detail-rail T6 已經釘住
 *     佢喺 `.detail-metrics` 同 `.detail-prose` 之間；呢度重複釘一次，因為今次改動就係
 *     改嗰個元素嘅內文，最容易順手手滑刪咗成個元素。
 *  ④ **`pricecharting_sales` / `snkrdunk_sales` 唔准出街。** 兩個新 source code 都會被
 *     scripts/public-surface-gate.mjs 個 TOKEN_PATTERN 命中（佢特登用非字母數字
 *     lookaround，就係為咗攔 `ebay_sales` 呢種 underscore 形狀）。今日 producer 喺
 *     `historyDrafts → history` 嗰度 strip 走 priceSourceCode／priceSourcePriority，
 *     所以泄漏唔到；但「今日啱」唔等於「聽日仲啱」，所以呢度兩個方向都行：
 *     真 snapshot 要過，種毒（把 alias code 塞入結構欄）要炸。
 *
 * Run: node scripts/test-fe-sale-date-label.mjs
 */
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { assertPublicSurface } from "./public-surface-gate.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const failed = [];
let checks = 0;
const check = (label, condition, detail) => {
  checks += 1;
  if (!condition) failed.push(detail ? `${label}: ${detail}` : label);
};
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");

const PRODUCER_REL = "apps/web/src/lib/live-db-snapshot.ts";
const TSX_REL = "apps/web/src/components/card-detail.tsx";
const I18N_REL = "apps/web/src/lib/i18n.ts";
const SNAPSHOT_REL = "data/public/seed-snapshot.json";

const producer = read(PRODUCER_REL);
const tsx = read(TSX_REL);
const i18n = read(I18N_REL);

/* 由 `start` 開始搵第一個平衡嘅 `{…}`，返 [inner, endIndexExclusive]。
   用嚟由 JSX 度抽真表達式出嚟行，唔係抄一份落 test 度（抄一份 = 改咗源碼呢度照綠）。 */
function braced(source, start) {
  const open = source.indexOf("{", start);
  if (open < 0) return null;
  let depth = 0;
  for (let i = open; i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    else if (source[i] === "}") {
      depth -= 1;
      if (depth === 0) return [source.slice(open + 1, i), i + 1];
    }
  }
  return null;
}

/* ─────────────────────────────────────────────────────────────
 * T1 — producer：`_sales` quote → saleAt；chart quote → sourcePeriodAt。抽真源碼行。
 * ───────────────────────────────────────────────────────────── */
{
  const lines = producer.split("\n");
  const wanted = ["const isSaleQuote", "const priceSaleAt", "const priceSourcePeriodAt"];
  const picked = wanted.map((needle) => {
    const hits = lines.filter((line) => line.trim().startsWith(needle));
    check(`T1: producer 有且只有一行 \`${needle}\``, hits.length === 1, `搵到 ${hits.length} 行`);
    return hits[0] ?? "";
  });

  if (picked.every(Boolean)) {
    // 三行真源碼，唔改一個字。row / pricePeriodAt 由外面餵。
    const split = new Function(
      "row",
      "pricePeriodAt",
      `${picked.join("\n")}\nreturn { isSaleQuote, priceSaleAt, priceSourcePeriodAt };`,
    );
    const PERIOD = "2026-08-19T00:00:00Z";
    const cases = [
      { label: "pricecharting_sales", row: { price_source_code: "pricecharting_sales" }, sale: true },
      { label: "snkrdunk_sales", row: { price_source_code: "snkrdunk_sales" }, sale: true },
      { label: "pricecharting（legacy chart）", row: { price_source_code: "pricecharting" }, sale: false },
      { label: "snkrdunk（legacy kline）", row: { price_source_code: "snkrdunk" }, sale: false },
      { label: "source code 缺失", row: {}, sale: false },
    ];
    let sawSale = 0;
    let sawChart = 0;
    for (const c of cases) {
      const got = split(c.row, PERIOD);
      if (c.sale) {
        sawSale += 1;
        check(`T1: ${c.label} → saleAt = 期數日期`, got.priceSaleAt === PERIOD, JSON.stringify(got));
        check(`T1: ${c.label} → sourcePeriodAt 一定係 null（唔准兩個一齊有）`,
          got.priceSourcePeriodAt === null, JSON.stringify(got));
      } else {
        sawChart += 1;
        check(`T1: ${c.label} → sourcePeriodAt 保留`, got.priceSourcePeriodAt === PERIOD, JSON.stringify(got));
        check(`T1: ${c.label} → saleAt 一定係 null`, got.priceSaleAt === null, JSON.stringify(got));
      }
    }
    // min-hit：兩個方向都要真係行過，否則呢個 T 係綠色牆紙。
    check("T1: sale 同 chart 兩個方向都行過", sawSale >= 2 && sawChart >= 3, `sale=${sawSale} chart=${sawChart}`);
  }

  // 兩個欄位都要真係落到 payload（producer 側白名單，唔係 spread）。
  const emitted = (producer.match(/^\s*saleAt: priceSaleAt,$/gm) || []).length;
  check("T1: pricePsa10 兩條 branch（awaiting / ready）都要帶 saleAt", emitted === 2, `搵到 ${emitted} 個`);
  const stale = (producer.match(/^\s*sourcePeriodAt: pricePeriodAt,$/gm) || []).length;
  check("T1: 唔准仲有 branch 直接寫 `sourcePeriodAt: pricePeriodAt`（成交價會掛返月線 label）",
    stale === 0, `仲有 ${stale} 個`);
}

/* ─────────────────────────────────────────────────────────────
 * T2 — 卡內頁：saleAt 有值 → 出「成交日」；saleAt 為 null 但 sourcePeriodAt 有值 → 出「價格期數」。
 *      抽 `<p className="data-time">` 入面第一個真表達式出嚟行，唔係 grep 個字串。
 * ───────────────────────────────────────────────────────────── */
{
  const anchor = tsx.indexOf('<p className="data-time">');
  check("T2: 搵到 `<p className=\"data-time\">`", anchor >= 0);
  if (anchor >= 0) {
    let cursor = anchor;
    let expr = null;
    for (let hop = 0; hop < 4 && expr === null; hop += 1) {
      const found = braced(tsx, cursor);
      if (!found) break;
      const [inner, end] = found;
      cursor = end;
      if (inner.trim().startsWith("/*")) continue;   // JSX 註釋，跳過
      expr = inner;
    }
    check("T2: 抽到 data-time 第一個表達式", expr !== null);
    if (expr !== null) {
      check("T2: 個表達式要同時提到 saleAt 同 sourcePeriodAt（legacy 分支唔准冚）",
        expr.includes("saleAt") && expr.includes("sourcePeriodAt"), expr.slice(0, 160));
      const t = { labels: { saleDate: "SALE_DATE", pricePeriod: "PRICE_PERIOD" } };
      const fmt = (value) => `<${value}>`;
      const run = new Function("card", "t", "formatObservationDate", "locale", `return (${expr});`);
      const sale = run(
        { pricePsa10: { saleAt: "2026-08-19T00:00:00Z", sourcePeriodAt: null } },
        t, fmt, "zh-TW",
      );
      check("T2: saleAt 有值 → 出成交日 label", String(sale).includes("SALE_DATE"), String(sale));
      check("T2: saleAt 有值 → 唔准出價格期數 label", !String(sale).includes("PRICE_PERIOD"), String(sale));
      check("T2: saleAt 有值 → 出嗰個日期就係 saleAt", String(sale).includes("<2026-08-19T00:00:00Z>"), String(sale));

      const legacy = run(
        { pricePsa10: { saleAt: null, sourcePeriodAt: "2026-08-01T00:00:00Z" } },
        t, fmt, "zh-TW",
      );
      check("T2: saleAt 為 null + sourcePeriodAt 有值 → 出返價格期數（legacy 相容）",
        String(legacy).includes("PRICE_PERIOD"), String(legacy));
      check("T2: legacy 分支唔准出成交日 label", !String(legacy).includes("SALE_DATE"), String(legacy));

      // 兩個一齊有（producer 今日保證唔會，但 legacy resolver / 將來第二個 producer 可以）：
      // 成交日一定要贏。呢個 case 就係令個三元次序變成有約束力嘅嘢 —— 冇佢，
      // 掉轉兩個分支照樣綠。
      const both = run(
        { pricePsa10: { saleAt: "2026-08-19T00:00:00Z", sourcePeriodAt: "2026-08-01T00:00:00Z" } },
        t, fmt, "zh-TW",
      );
      check("T2: 兩個都有值 → 成交日贏（分支次序係契約）",
        String(both).includes("SALE_DATE") && !String(both).includes("PRICE_PERIOD"), String(both));

      const empty = run({ pricePsa10: { saleAt: null, sourcePeriodAt: null } }, t, fmt, "zh-TW");
      check("T2: 兩個都冇 → 唔出前綴（唔准畫假日期）", empty === null || empty === undefined || empty === "",
        JSON.stringify(empty));
    }
  }

  // label key 五個 locale 都要有，否則 build 過到但 runtime 出 `undefined: 2026年8月19日`。
  check("T2: i18n interface 有 saleDate", /^\s*saleDate: string;$/m.test(i18n));
  const values = i18n.match(/saleDate: "[^"]+"/g) || [];
  check("T2: 五個 locale 都有 saleDate 值", values.length === 5, `搵到 ${values.length}：${values.join(" / ")}`);
  for (const literal of values) {
    check("T2: saleDate 文案唔准帶供應商代號", !/pricecharting|snkrdunk|ebay|g10|gemrate/i.test(literal), literal);
  }
}

/* ─────────────────────────────────────────────────────────────
 * T3 — `.data-time` 元素仲喺度，而且仲喺 `.detail-metrics` 同 `.detail-prose` 之間
 *      （同 test-fe-detail-rail T6 同一條契約，今次係改佢內文，所以再釘一次）。
 * ───────────────────────────────────────────────────────────── */
{
  const iMetrics = tsx.indexOf('className="detail-metrics"');
  const iTime = tsx.indexOf('className="data-time"');
  const iProse = tsx.indexOf('className="detail-prose"');
  check("T3: 三個定位點都搵得返", iMetrics >= 0 && iTime >= 0 && iProse >= 0,
    JSON.stringify([iMetrics, iTime, iProse]));
  check("T3: DOM 次序 = 市值/數量 → 資料時間 → 簡介",
    iMetrics >= 0 && iTime > iMetrics && iProse > iTime, JSON.stringify([iMetrics, iTime, iProse]));
  check("T3: `.data-time` 仲係 `<p>`（唔准換 tag，CSS 同讀屏都食呢個）",
    tsx.includes('<p className="data-time">'));
  check("T3: `.data-time` 仲要出「最近檢查」", tsx.slice(iTime, iProse).includes("t.labels.checkedAt"));
}

/* ─────────────────────────────────────────────────────────────
 * T5 — view model 個 `metric()` 係**明碼白名單複製**（唔係 spread）。漏抄一條就係
 *      「producer 有、type 有、頁面永遠 undefined」——tsc 過、test 綠、卡面靜靜冇咗
 *      成交日。所以呢度抽真嗰個 function body 出嚟行，唔係 grep 個欄名。
 * ───────────────────────────────────────────────────────────── */
{
  const snapshotTs = read("apps/web/src/lib/snapshot.ts");
  const at = snapshotTs.indexOf("function metric(");
  check("T5: snapshot.ts 有 metric()", at >= 0);
  if (at >= 0) {
    const body = braced(snapshotTs, at);
    check("T5: 抽到 metric() body", body !== null);
    if (body) {
      const project = new Function("value", body[0]);
      const got = project({
        value: 2750,
        status: "ready",
        asOf: "2026-08-23T00:00:00Z",
        sourcePeriodAt: null,
        saleAt: "2026-08-19T00:00:00Z",
        checkedAt: "2026-08-23T00:00:00Z",
      });
      check("T5: metric() 要抄住 saleAt", got.saleAt === "2026-08-19T00:00:00Z", JSON.stringify(got));
      check("T5: metric() 仍然抄住 sourcePeriodAt", "sourcePeriodAt" in got, JSON.stringify(got));
      const legacy = project({ value: 1, status: "ready", asOf: null, sourcePeriodAt: "2026-08-01T00:00:00Z" });
      check("T5: 冇 saleAt 嘅舊 payload 要正規化做 null（唔准 undefined）",
        legacy.saleAt === null, JSON.stringify(legacy));
      check("T5: legacy payload 個 sourcePeriodAt 唔准被食走",
        legacy.sourcePeriodAt === "2026-08-01T00:00:00Z", JSON.stringify(legacy));
    }
  }
}

/* ─────────────────────────────────────────────────────────────
 * T4 — 出街 snapshot 唔准帶 `pricecharting_sales` / `snkrdunk_sales`；
 *      而且個閘要真係炸得起（種毒證明，唔淨係睇綠燈）。
 * ───────────────────────────────────────────────────────────── */
{
  const snapshot = JSON.parse(read(SNAPSHOT_REL));
  const ALIAS = /(?<![A-Za-z0-9])(?:pricecharting|snkrdunk)_sales(?![A-Za-z0-9])/i;
  const hits = [];
  let strings = 0;
  const walk = (node, path) => {
    if (typeof node === "string") {
      strings += 1;
      if (ALIAS.test(node)) hits.push(`${path} = ${JSON.stringify(node.slice(0, 80))}`);
      return;
    }
    if (Array.isArray(node)) { node.forEach((item, i) => walk(item, `${path}[${i}]`)); return; }
    if (node && typeof node === "object") {
      for (const [key, value] of Object.entries(node)) walk(value, path ? `${path}.${key}` : key);
    }
  };
  walk(snapshot, "");
  check("T4: 真係掃過嘢（一個 string 都冇就唔准當佢過）", strings > 1000, `掃咗 ${strings} 個 string`);
  check("T4: snapshot 冇任何欄位帶 sale-lane alias code", hits.length === 0,
    hits.slice(0, 5).join(" | "));

  let gate = null;
  try {
    gate = assertPublicSurface(snapshot, { warn: () => {} });
  } catch (error) {
    failed.push(`T4: 出街 snapshot 過唔到 public-surface-gate: ${error.message.split("\n")[0]}`);
  }
  check("T4: public-surface-gate 有掃到公開 id", gate !== null && gate.publicIds > 0,
    JSON.stringify(gate));

  // 種毒：把 alias code 塞入一個結構欄，個閘一定要炸。
  const poisoned = JSON.parse(read(SNAPSHOT_REL));
  poisoned.top100[0].pricePsa10 = { ...poisoned.top100[0].pricePsa10, priceSourceCode: "pricecharting_sales" };
  let poisonMessage = null;
  try {
    assertPublicSurface(poisoned, { warn: () => {} });
  } catch (error) {
    poisonMessage = error.message;
  }
  check("T4: 種毒（結構欄塞 `pricecharting_sales`）個閘一定要炸", poisonMessage !== null);
  check("T4: 種毒炸嘅原因要係 provider token，唔係第二樣",
    poisonMessage !== null && poisonMessage.includes("provider token"), poisonMessage?.split("\n")[0]);

  const poisoned2 = JSON.parse(read(SNAPSHOT_REL));
  poisoned2.watchlist[0].pricePsa10 = { ...poisoned2.watchlist[0].pricePsa10, priceSourceCode: "snkrdunk_sales" };
  let poison2 = null;
  try {
    assertPublicSurface(poisoned2, { warn: () => {} });
  } catch (error) {
    poison2 = error.message;
  }
  check("T4: `snkrdunk_sales` 一樣要炸", poison2 !== null && poison2.includes("provider token"),
    poison2?.split("\n")[0]);
}

/* ─────────────────────────────────────────────────────────────
 * T5 — 變幅錨點要認得 `_sales` 係母 lane 嘅升格碼。
 * `windowMetrics(... currentSource)` 由 `row.price_source_code` 嚟，而 056 之後嗰個值
 * 係 `pricecharting_sales`。唔剝尾碼 = 全板對唔中、靜靜換 lane。
 *
 * 2026-08-24（R6）：舊嗰個 `currentSource` ladder map 淨係服務 `market_price_observation`
 * 嘅 K 線點排序，而成條 chart 讀路已經整條刪走（日線只准由真成交嚟，見
 * scripts/test-fe-history-sale-only.mjs）。所以呢度唔再釘個 ladder map，改為釘更強嘅
 * 一句：chart 讀路唔准返嚟。剩返兩個 chartLaneOf call site（currentSource + 錨點）照釘。
 * ───────────────────────────────────────────────────────────── */
{
  const helperMatch = producer.match(/function chartLaneOf\(sourceCode: unknown\): string \| null \{([\s\S]*?)\n\}/);
  check("T5: producer 有 chartLaneOf helper", Boolean(helperMatch));
  if (helperMatch) {
    const chartLaneOf = new Function("sourceCode", helperMatch[1]);
    check("T5: pricecharting_sales → pricecharting", chartLaneOf("pricecharting_sales") === "pricecharting",
      `→ ${chartLaneOf("pricecharting_sales")}`);
    check("T5: snkrdunk_sales → snkrdunk", chartLaneOf("snkrdunk_sales") === "snkrdunk",
      `→ ${chartLaneOf("snkrdunk_sales")}`);
    check("T5: chart 母碼原樣", chartLaneOf("pricecharting") === "pricecharting" && chartLaneOf("snkrdunk") === "snkrdunk");
    check("T5: 空值 → null", chartLaneOf(null) === null && chartLaneOf("") === null);
  }
  /* R6b（owner 2026-08-24）：observation 讀路返咗嚟，但只准得一處，餵
     `historyReference`（長窗後備錨 + ≥90d 深歷史圖）；historyDaily 側嘅深斷言喺
     scripts/test-fe-history-sale-only.mjs H1。舊 `priceRows` binding 照封
     （呢條 regex 原本俾 heredoc 食咗  變咗 backspace 字元，一直空轉，順手修埋）。 */
  check("T5: observation 讀路有且只有一處（reference），舊 priceRows 唔准返嚟",
    producer.split("INNER JOIN market_price_observation").length - 1 === 1
      && !/priceRows/.test(producer),
    "producer 嘅 K 線讀路唔係剛好一處，或者 priceRows 返咗嚟");
  check("T5: 變幅錨點嘅 source code 行過 chartLaneOf",
    /const anchorSource = chartLaneOf\(anchor\?\.sourceCode \?\? null\);/.test(producer),
    "anchorSource 冇剝尾碼，全板會假 sourceSwitched");
  check("T5: windowMetrics 個 currentSource 行過 chartLaneOf",
    /priceAsOf,\s*chartLaneOf\(row\.price_source_code\),\s*referenceDrafts,\s*\)/.test(producer),
    "windowMetrics 仲係直接 String(row.price_source_code)");
  check("T5: call site 冇剩返舊寫法",
    !/\[Number\(row\.variant_id\), String\(row\.price_source_code\)\]/.test(producer)
    && !/priceAsOf,\s*String\(row\.price_source_code\),?\s*\)/.test(producer)
    && !/const anchorSource = anchor\?\.sourceCode \?\? null;/.test(producer));
}

if (failed.length) {
  console.error(`FAIL test-fe-sale-date-label (${failed.length}/${checks})`);
  for (const f of failed) console.error(` - ${f}`);
  process.exit(1);
}
console.log(`PASS test-fe-sale-date-label (${checks} checks)`);
